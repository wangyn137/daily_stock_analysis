# Log De-duplication — Design Spec

- Date: 2026-09-20
- Status: draft
- Owner: opencode
- Related issues / context: 2026-09-17 任务中 `scheduler.py:180` 在 100 分钟死循环内刷出 186 条同样的 WARNING；同日还有 urllib3 Retrying、`[情报搜索]` 限流、`[stock-index]` remote update failed 等重复失败信息刷屏。

## 1. 背景与动机

### 1.1 现状

`src/logging_config.py:53-58` 已经通过 `DEFAULT_QUIET_LOGGERS` 把 urllib3、sqlalchemy、google、httpx 默认降到 WARNING。但项目仍然在多种失败场景下出现**完全相同**或**结构相同**的日志被反复输出：

| 噪音源 | 单任务量级 | 形态 |
| --- | --- | --- |
| `scheduler.py:180 读取 SCHEDULE_TIME 失败，继续沿用 ... [Errno 24] Too many open files: '.env'` | ~186 条 / 100 分钟 | 字符串完全相同 |
| `urllib3 Retrying (Retry(total=N)) after connection broken by 'RemoteDisconnected(...)'` | ~30 条 | 同一次重试链重复 5 次 |
| `[情报搜索] 最新消息/机构分析/...: 搜索失败 - 请求频率过高` | ~12 条 | 同一只股票每个维度都报 |
| `[妙想] API Key mkt_gghz... 错误计数: N` | 14 条 | 计数器递增但语义重复 |
| `[stock-index] remote update failed (N/3)` | 1-3 条 | 重试链复制 |

### 1.2 目标

1. 在保留全部关键信号（ERROR 级、唯一失败）的前提下，**显著降低重复失败信息的噪声**
2. 不影响调试档日志（DEBUG handler 仍记录全部）
3. 改动最小、易回滚、可被环境变量关停

### 1.3 非目标（YAGNI）

- 跨进程 / 跨重启去重
- 引入第三方日志库
- 改 logger 格式 / handler 选型
- 修改任何业务模块（仅在 logging_config.py 内实现）

## 2. 设计

### 2.1 架构总览

在 `src/logging_config.py` 新增 `MessageDeduplicationFilter`（实现 `logging.Filter` 协议），挂在 console handler 和 INFO 级别常规文件 handler 上。**DEBUG handler 不挂**，详情全留。

```
record → handler.setLevel 过滤 → MessageDeduplicationFilter.filter
                                              ↓
                              ERROR+  → 始终放行
                              否则    → 算 key
                                              ↓
                                          key 已存在？
                                          yes → count+1, 静默
                                          no  → 注册 bucket, 放行
                                              ↓
                              定期 flush_locked
                                              ↓
                              输出 "x 条重复" 汇总行
                                              ↓
                              handler.format + write
```

### 2.2 组件

| 名字 | 文件 | 职责 |
| --- | --- | --- |
| `normalize_message(text)` | `src/logging_config.py` | 把可变字段（URL/股票代码/对象地址/时间戳）抹平为占位符 |
| `MessageDeduplicationFilter` | 同上 | 状态机 + 决策（首报 / 累加 / 静默 / 末尾汇总） |
| `_Bucket` | 同上 | 单条 key 的状态（first_seen / last_seen / count / 原始 first record） |
| `setup_logging(...)` | 同上 | 新增参数 `dedup_enabled`, `dedup_window_seconds`, `dedup_flush_interval_seconds`；给 console + info handler 挂 filter |
| `tests/test_logging_dedup.py` | `tests/` | 单元 + 集成测试 |

### 2.3 关键决策

#### 2.3.1 归一化（normalization）

`normalize_message(text)` 用正则替换以下模式为占位符：

| 模式 | 占位符 |
| --- | --- |
| 6 位股票代码（`\b\d{6}\b`） | `<CODE>` |
| URL（含 `http(s)://`） | `<URL>` |
| ISO 日期（`\d{4}-\d{2}-\d{2}`） | `<DATE>` |
| 十六进制对象地址（`0x[0-9a-f]+`） | `<ADDR>` |
| urllib3 `Retry(total=\d+)` | `Retry(total=<N>)` |
| `\b\d+\.\d+\.\d+\.\d+\b` | `<IP>` |

归一化后两条仅股票代码不同的记录会落到同一个 key；URL 中股票代码已经被替换为 `<CODE>`，不会再二次归一。

#### 2.3.2 key 构成

```python
key = (record.levelno, record.name, normalize_message(record.getMessage()))
```

- `levelno` 区分 INFO / WARNING（ERROR 不进 filter，所以不需要）
- `name` 区分 logger
- `message` 走归一化

#### 2.3.3 状态机

每个 key 一个 `_Bucket`：

```python
@dataclass
class _Bucket:
    first_seen: float          # monotonic
    last_seen: float           # monotonic
    count: int                 # 累计出现次数（包含首报）
    first_record: LogRecord    # 首报原始 record，用于末尾汇总时复用其格式
```

#### 2.3.4 flush 策略

`MessageDeduplicationFilter` 内部维护 `self._last_flush`。`filter()` 每次被调用都检查：

- 距上次 flush ≥ `flush_interval_seconds` → 触发汇总输出
- 触发时遍历所有 buckets：
  - `now - bucket.first_seen >= window_seconds` → 输出汇总行 "key xxx 在 N 秒内出现 M 次"，删除 bucket
  - 否则 → 保留到下一轮

filter 自身不直接输出，需通过 `handler.emit` 写回，因此引入 **辅助 handler**（见 2.3.5）。

#### 2.3.5 汇总行输出机制

汇总行不能走当前 filter 自身（否则会被自己静默掉）。所以：

- `MessageDeduplicationFilter` 内部通过专用 logger `logging.getLogger("src.logging_config.dedup")` 输出汇总
- 该 logger 走与业务 logger 相同的 handler 链，但因汇总行的 logger name 是 `src.logging_config.dedup`，**它本身的记录也不会被同一个 filter 的 buckets 捕获**（bucket key 含 logger name，跨 logger 不命中）
- 汇总文本格式：`"[去重汇总] <logger>.<levelname> <原始首条消息> 在 N 秒内出现 M 次"`

实现细节：

```python
SUMMARY_LOGGER_NAME = "src.logging_config.dedup"

def _emit_summary(self, bucket, normalized_key):
    summary = (
        f"[去重汇总] {bucket.first_record.name}.{bucket.first_record.levelname} "
        f"{bucket.first_record.getMessage()} "
        f"在 {int(bucket.last_seen - bucket.first_seen)} 秒内出现 {bucket.count} 次"
    )
    logging.getLogger(SUMMARY_LOGGER_NAME).info(summary)
```

注意：汇总 logger 也走根 logger 的 handler 链（继承）。如果业务调用 `setup_logging` 后又挂自己的 filter，dedup logger 不受影响（filter 挂在 handler 上而不是 logger 上，按 handler 顺序处理；汇总 logger 走的是相同的 handler 链，但因为 key 包含 logger name，不会和业务 bucket 冲突）。

#### 2.3.6 ERROR 始终放行

```python
if record.levelno >= logging.ERROR:
    return True
```

理由：ERROR 是问题报警，可能包含堆栈、异常链，压缩后丢失关键上下文。

#### 2.3.7 内存上限

buckets 硬上限 `MAX_BUCKETS = 10000`。达到上限时立即强制 flush 所有 buckets 并清空，防止极端场景下内存爆炸。

#### 2.3.8 线程安全

`MessageDeduplicationFilter` 在多线程环境下被调用（业务代码用 ThreadPoolExecutor），所有 buckets 访问走 `threading.Lock`。

### 2.4 配置

环境变量（带默认值）：

| 变量 | 默认 | 含义 |
| --- | --- | --- |
| `LOG_DEDUP` | `true` | 总开关 |
| `LOG_DEDUP_WINDOW_SECONDS` | `300` | bucket 存活窗口（5 分钟） |
| `LOG_DEDUP_FLUSH_INTERVAL_SECONDS` | `60` | 每分钟 flush 一次 |
| `LOG_DEDUP_MAX_BUCKETS` | `10000` | 硬上限 |
| `LOG_DEDUP_FORCE_FLUSH_AFTER` | `100` | 静默计数达此值后立即触发 flush |

### 2.5 失败模式与处理

| 情况 | 处理 |
| --- | --- |
| filter 抛异常 | 捕获后返回 True（fail-open，永不丢日志） |
| 内存超限 | 强制 flush 全部 + 清空 buckets |
| 多线程并发 | Lock 保护 |
| setup_logging 失败 | 静默回退到无 dedup 状态（不影响现有行为） |
| handler 链关闭 / 重新初始化 | filter state 保留（同一进程）；重启清零 |

### 2.6 数据流示例

时间线（假设 `flush_interval=60s`, `window=300s`）：

| t (s) | 事件 | filter 行为 | 控制台/INFO 文件实际收到 |
| --- | --- | --- | --- |
| 0 | `[情报搜索] 最新消息: 搜索失败 - 请求频率过高 (513180)` | 新 bucket，放行 | 原始消息 |
| 5 | 同样一条 | count=2，静默 | 无 |
| 30 | 同样一条 | count=3，静默 | 无 |
| 60 | flush 触发 | bucket 未到 window，无汇总 | 无 |
| 180 | 同样一条 | count=4，静默 | 无 |
| 240 | flush 触发 | bucket 未到 window（first_seen 才 240s），无汇总 | 无 |
| 300 | flush 触发 | bucket 到 window（first_seen=0），输出汇总 | `[2026-09-20 12:05:00] src.logging_config.dedup INFO [去重汇总] src.search_service.WARNING [情报搜索] 最新消息: 搜索失败 - 请求频率过高 (513180) 在 300 秒内出现 4 次` |

### 2.7 测试覆盖

`tests/test_logging_dedup.py`：

| 用例 | 验证 |
| --- | --- |
| `test_normalize_strips_url` | URL → `<URL>` |
| `test_normalize_strips_stock_code` | 6 位代码 → `<CODE>` |
| `test_normalize_strips_object_address` | `0x393aba810` → `<ADDR>` |
| `test_normalize_strips_ip` | IPv4 → `<IP>` |
| `test_normalize_strips_urllib3_retry` | `Retry(total=4)` → `Retry(total=<N>)` |
| `test_normalize_handles_empty_input` | 空字符串不崩 |
| `test_filter_first_record_passes` | 首条放行 |
| `test_filter_dedupes_subsequent` | 第 2-N 条静默 |
| `test_filter_always_passes_error` | ERROR 级始终放行 |
| `test_filter_different_messages_dont_collide` | 不同消息不同 key |
| `test_filter_flushes_after_window` | window 到期输出汇总 |
| `test_filter_force_flush_on_max_buckets` | 10000 buckets 上限触发强制 flush |
| `test_filter_force_flush_on_silent_count` | 静默计数达 `LOG_DEDUP_FORCE_FLUSH_AFTER` 触发 flush |
| `test_filter_thread_safe` | 多线程并发不崩 |
| `test_filter_fail_open_on_exception` | filter 内部异常 → 返回 True |
| `test_setup_logging_attaches_filter` | 默认开 → console + info handler 都有 filter |
| `test_setup_logging_disabled_skips_filter` | `LOG_DEDUP=false` → 不挂 filter |
| `test_setup_logging_debug_handler_unaffected` | DEBUG handler 不挂 filter |

## 3. 风险与权衡

### 3.1 多进程 / 跨重启不共享

每个进程独立 buckets。多进程部署时（当前项目只有 1 个主进程 + uvicorn worker）会重复首报。如果后续接入多 worker 需要额外设计（暂不处理）。

### 3.2 调试档不受影响

DEBUG handler 走 `debug_handler.setLevel(logging.DEBUG)`，filter 不挂；所有原始 record 仍写入 `logs/<prefix>_debug_<date>.log`。但**汇总行本身**（如"x 条重复"）不会写入调试档，因为它是 INFO 级 → 走 console + INFO handler → 也会被 dedup。

权衡：接受。汇总行是元信息，写入常规日志够用。

### 3.3 首次 flush 前的延迟

`flush_interval` 默认 60s。在 flush 触发前，重复消息完全静默。如果窗口内没有任何时间戳变化，运维可能误以为"日志停了"。

缓解：在 filter 内维护一个**静默计数器**，超过 `100` 条静默后立即强制 flush 一次（无须等下一个时间间隔）。新增 env: `LOG_DEDUP_FORCE_FLUSH_AFTER=100`。

### 3.4 ERROR 不压缩可能仍然刷屏

如果同一 ERROR 连续出现（如第三方库的 ConnectionError）也不会 dedup。设计上接受——保留 ERROR 信号比压缩它更重要。

如果后续发现 ERROR 也需要压缩，应升级方案（区分 user-defined ERROR vs library ERROR），本次不做。

## 4. 验证清单

完成实现后必须验证：

1. ✅ `tests/test_logging_dedup.py` 全部通过
2. ✅ 现有 `tests/test_logging*.py` 全部仍通过（如有）
3. ✅ `python -m py_compile src/logging_config.py` 通过
4. ✅ `python -m flake8 src/logging_config.py --max-line-length=120` 干净
5. ✅ Smoke test：跑一个循环输出 100 条相同 WARNING，确认 console 只收到首条 + 末尾汇总 ≤ 2 条
6. ✅ Smoke test：跑一个循环输出 50 条 ERROR，确认 console 收到全部 50 条
7. ⚠️ 未验证：明天 14:00 任务中的实际效果（需要真实运行）

## 5. 未来扩展（不在本次范围内）

- 跨进程去重（Redis 或共享内存）
- 按 logger 单独配置 dedup 策略
- 跟现有 `_check_fd_exhaustion` 类似，给 dedup 加 metric 输出
- 集成 Sentry / Lark 等外部告警通道（汇总时主动 ping）
