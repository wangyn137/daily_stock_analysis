# -*- coding: utf-8 -*-
"""
===================================
日志配置模块 - 统一的日志系统初始化
===================================

职责：
1. 提供统一的日志格式和配置常量
2. 支持控制台 + 文件（常规/调试）三层日志输出
3. 自动降低第三方库日志级别
"""

import logging
import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, List, Optional, Tuple


SUMMARY_LOGGER_NAME = "src.logging_config.dedup"

_URL_RE = re.compile(r"https?://\S+?(?=[\s,)\]>}]|\b\d{6}\b|$)")
_STOCK_CODE_RE = re.compile(r"\b\d{6}\b")
_HEX_ADDR_RE = re.compile(r"0x[0-9a-fA-F]+")
_RETRY_TOTAL_RE = re.compile(r"Retry\(total=\d+\)")
_IP_PORT_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b")
_ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def normalize_message(text: str) -> str:
    """Replace variable fields (URL/code/IP/address/retry counter/date) with placeholders."""
    if not text:
        return text
    text = _URL_RE.sub("<URL>", text)
    text = _STOCK_CODE_RE.sub("<CODE>", text)
    text = _HEX_ADDR_RE.sub("<ADDR>", text)
    text = _RETRY_TOTAL_RE.sub("Retry(total=<N>)", text)
    text = _IP_PORT_RE.sub("<IP>:<PORT>", text)
    text = _ISO_DATE_RE.sub("<DATE>", text)
    return text


@dataclass
class _Bucket:
    first_seen: float
    last_seen: float
    count: int
    first_record: logging.LogRecord
    normalized_key: str


class MessageDeduplicationFilter(logging.Filter):
    """logging.Filter that silences duplicate WARNING/INFO records.

    - First record for a (level, logger, normalized_message) bucket passes through.
    - Subsequent duplicates are silenced; a summary line is emitted when the bucket
      expires (>= window_seconds since first_seen) or after `force_flush_after`
      silent duplicates.
    - ERROR/CRITICAL records always pass through.
    - Summary lines are emitted via the dedicated SUMMARY_LOGGER_NAME logger to
      avoid being re-captured by this same filter.
    """

    def __init__(
        self,
        window_seconds: float = 300.0,
        flush_interval_seconds: float = 60.0,
        max_buckets: int = 10000,
        force_flush_after: int = 100,
    ) -> None:
        super().__init__()
        self.window_seconds = float(window_seconds)
        self.flush_interval_seconds = float(flush_interval_seconds)
        self.max_buckets = int(max_buckets)
        self.force_flush_after = int(force_flush_after)
        self._buckets: Dict[Tuple[int, str, str], _Bucket] = {}
        self._silent_count = 0
        self._last_flush_monotonic = time.monotonic()
        self._lock = threading.Lock()

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            return self._filter_unsafe(record)
        except Exception:  # pragma: no cover - fail-open
            return True

    def _filter_unsafe(self, record: logging.LogRecord) -> bool:
        # Skip summary records to prevent self-recursion: a summary emitted by
        # this filter would otherwise re-enter and get bucketed against itself,
        # causing unbounded message growth.
        if record.name == SUMMARY_LOGGER_NAME:
            return True
        if record.levelno >= logging.ERROR:
            return True
        normalized = normalize_message(record.getMessage())
        key = (record.levelno, record.name, normalized)
        now = time.monotonic()
        pending_summaries: List[_Bucket] = []
        is_first_record = False
        with self._lock:
            pending_summaries.extend(self._maybe_flush_locked(now))
            bucket = self._buckets.get(key)
            if bucket is None:
                if len(self._buckets) >= self.max_buckets:
                    pending_summaries.extend(self._collect_all_buckets_locked())
                self._buckets[key] = _Bucket(
                    first_seen=now,
                    last_seen=now,
                    count=1,
                    first_record=record,
                    normalized_key=normalized,
                )
                is_first_record = True
            else:
                bucket.count += 1
                bucket.last_seen = now
                self._silent_count += 1
                if self._silent_count >= self.force_flush_after:
                    # force_flush_after: drain ALL pending buckets (not just expired ones)
                    pending_summaries.extend(self._collect_all_buckets_locked())
        for b in pending_summaries:
            self._emit_summary(b)
        return is_first_record

    def _maybe_flush_locked(self, now: float) -> List[_Bucket]:
        """If the periodic flush interval has elapsed, drain expired buckets.
        Returns the list of drained buckets; the caller emits summaries
        OUTSIDE the lock to avoid re-entrant deadlock (see _filter_unsafe).
        """
        if now - self._last_flush_monotonic < self.flush_interval_seconds:
            return []
        return self._collect_expired_buckets_locked(now)

    def _collect_expired_buckets_locked(self, now: float) -> List[_Bucket]:
        expired_keys = [
            k for k, b in self._buckets.items() if now - b.first_seen >= self.window_seconds
        ]
        result = [self._buckets.pop(k) for k in expired_keys]
        if expired_keys:
            self._silent_count = 0
            self._last_flush_monotonic = now
        return result

    def _collect_all_buckets_locked(self) -> List[_Bucket]:
        result = list(self._buckets.values())
        self._buckets.clear()
        self._silent_count = 0
        self._last_flush_monotonic = time.monotonic()
        return result

    def _emit_summary(self, bucket: _Bucket) -> None:
        duration = max(0, int(bucket.last_seen - bucket.first_seen))
        summary_text = (
            f"[去重汇总] {bucket.first_record.name}.{bucket.first_record.levelname} "
            f"{bucket.first_record.getMessage()} "
            f"在 {duration} 秒内出现 {bucket.count} 次"
        )
        logging.getLogger(SUMMARY_LOGGER_NAME).info(summary_text)

    def flush(self) -> None:
        """Force-emit summaries for all buckets (used at shutdown / tests)."""
        with self._lock:
            pending = self._collect_all_buckets_locked()
        for b in pending:
            self._emit_summary(b)


LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(pathname)s:%(lineno)d | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_ALLOWED_LOG_LEVELS = {
    'DEBUG': logging.DEBUG,
    'INFO': logging.INFO,
    'WARNING': logging.WARNING,
    'ERROR': logging.ERROR,
    'CRITICAL': logging.CRITICAL,
}
_DEFAULT_LITELLM_LOG_LEVEL = 'WARNING'


class RelativePathFormatter(logging.Formatter):
    """自定义 Formatter，输出相对路径而非绝对路径"""

    def __init__(self, fmt=None, datefmt=None, relative_to=None):
        super().__init__(fmt, datefmt)
        self.relative_to = Path(relative_to) if relative_to else Path.cwd()

    def format(self, record):
        # 将绝对路径转为相对路径
        try:
            record.pathname = str(Path(record.pathname).relative_to(self.relative_to))
        except ValueError:
            # 如果无法转换为相对路径，保持原样
            pass
        return super().format(record)



# 默认需要降低日志级别的第三方库
DEFAULT_QUIET_LOGGERS = [
    'urllib3',
    'sqlalchemy',
    'google',
    'httpx',
]

LITELLM_LOGGERS = [
    'LiteLLM',
    'LiteLLM Router',
    'LiteLLM Proxy',
    'litellm',
]


def _resolve_litellm_log_level(raw_level: Optional[str] = None) -> Tuple[int, Optional[str]]:
    """Resolve LiteLLM logger level from env, returning invalid raw value if any."""
    if raw_level is None:
        raw_level = os.getenv('LITELLM_LOG_LEVEL', '')

    normalized = (raw_level or '').strip().upper()
    if not normalized:
        normalized = _DEFAULT_LITELLM_LOG_LEVEL

    level = _ALLOWED_LOG_LEVELS.get(normalized)
    if level is None:
        return _ALLOWED_LOG_LEVELS[_DEFAULT_LITELLM_LOG_LEVEL], raw_level
    return level, None


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def setup_logging(
    log_prefix: str = "app",
    log_dir: str = "./logs",
    console_level: Optional[int] = None,
    debug: bool = False,
    extra_quiet_loggers: Optional[List[str]] = None,
    dedup_enabled: Optional[bool] = None,
    dedup_window_seconds: Optional[float] = None,
    dedup_flush_interval_seconds: Optional[float] = None,
    dedup_max_buckets: Optional[int] = None,
    dedup_force_flush_after: Optional[int] = None,
) -> Optional[MessageDeduplicationFilter]:
    """
    统一的日志系统初始化

    配置三层日志输出：
    1. 控制台：根据 debug 参数或 console_level 设置级别
    2. 常规日志文件：INFO 级别，10MB 轮转，保留 5 个备份
    3. 调试日志文件：DEBUG 级别，50MB 轮转，保留 3 个备份

    日志去重（dedup）：
    - 通过 MessageDeduplicationFilter 合并重复 WARNING/INFO；
    - 控制台 + 常规文件 handler 挂该 filter；调试 handler 不挂；
    - DEBUG 日志文件保留全部细节用于事后排查。

    Args:
        log_prefix: 日志文件名前缀（如 "api_server" -> api_server_20240101.log）
        log_dir: 日志文件目录，默认 ./logs
        console_level: 控制台日志级别（可选，优先于 debug 参数）
        debug: 是否启用调试模式（控制台输出 DEBUG 级别）
        extra_quiet_loggers: 额外需要降低日志级别的第三方库列表
        dedup_enabled: 是否启用去重过滤器；默认读取 LOG_DEDUP 环境变量（默认 true）
        dedup_window_seconds: 去重窗口；默认读取 LOG_DEDUP_WINDOW_SECONDS（默认 300）
        dedup_flush_interval_seconds: flush 间隔；默认读取 LOG_DEDUP_FLUSH_INTERVAL_SECONDS（默认 60）
        dedup_max_buckets: buckets 硬上限；默认读取 LOG_DEDUP_MAX_BUCKETS（默认 10000）
        dedup_force_flush_after: 静默计数阈值；默认读取 LOG_DEDUP_FORCE_FLUSH_AFTER（默认 100）

    Returns:
        安装的 MessageDeduplicationFilter 实例；如果 dedup_enabled=False 则返回 None。
    """
    if console_level is not None:
        level = console_level
    else:
        level = logging.DEBUG if debug else logging.INFO

    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    today_str = datetime.now().strftime('%Y%m%d')
    log_file = log_path / f"{log_prefix}_{today_str}.log"
    debug_log_file = log_path / f"{log_prefix}_debug_{today_str}.log"

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    if root_logger.handlers:
        root_logger.handlers.clear()

    project_root = Path.cwd()
    rel_formatter = RelativePathFormatter(
        LOG_FORMAT, LOG_DATE_FORMAT, relative_to=project_root
    )

    dedup_enabled_resolved = (
        dedup_enabled
        if dedup_enabled is not None
        else _env_flag("LOG_DEDUP", True)
    )

    dedup_filter: Optional[MessageDeduplicationFilter] = None
    if dedup_enabled_resolved:
        dedup_filter = MessageDeduplicationFilter(
            window_seconds=(
                dedup_window_seconds
                if dedup_window_seconds is not None
                else _env_float("LOG_DEDUP_WINDOW_SECONDS", 300.0)
            ),
            flush_interval_seconds=(
                dedup_flush_interval_seconds
                if dedup_flush_interval_seconds is not None
                else _env_float("LOG_DEDUP_FLUSH_INTERVAL_SECONDS", 60.0)
            ),
            max_buckets=(
                dedup_max_buckets
                if dedup_max_buckets is not None
                else _env_int("LOG_DEDUP_MAX_BUCKETS", 10000)
            ),
            force_flush_after=(
                dedup_force_flush_after
                if dedup_force_flush_after is not None
                else _env_int("LOG_DEDUP_FORCE_FLUSH_AFTER", 100)
            ),
        )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(rel_formatter)
    if dedup_filter is not None:
        console_handler.addFilter(dedup_filter)
    root_logger.addHandler(console_handler)

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding='utf-8',
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(rel_formatter)
    if dedup_filter is not None:
        file_handler.addFilter(dedup_filter)
    root_logger.addHandler(file_handler)

    debug_handler = RotatingFileHandler(
        debug_log_file,
        maxBytes=50 * 1024 * 1024,
        backupCount=3,
        encoding='utf-8',
    )
    debug_handler.setLevel(logging.DEBUG)
    debug_handler.setFormatter(rel_formatter)
    # DEBUG handler does NOT get the filter — keep full detail for debugging.
    root_logger.addHandler(debug_handler)

    quiet_loggers = DEFAULT_QUIET_LOGGERS.copy()
    if extra_quiet_loggers:
        quiet_loggers.extend(extra_quiet_loggers)

    for logger_name in quiet_loggers:
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    litellm_level, invalid_litellm_level = _resolve_litellm_log_level()
    for logger_name in LITELLM_LOGGERS:
        logging.getLogger(logger_name).setLevel(litellm_level)

    try:
        rel_log_path = log_path.resolve().relative_to(project_root)
    except ValueError:
        rel_log_path = log_path
    try:
        rel_log_file = log_file.resolve().relative_to(project_root)
    except ValueError:
        rel_log_file = log_file
    try:
        rel_debug_log_file = debug_log_file.resolve().relative_to(project_root)
    except ValueError:
        rel_debug_log_file = debug_log_file

    logging.info(f"日志系统初始化完成，日志目录: {rel_log_path}")
    logging.info(f"常规日志: {rel_log_file}")
    logging.info(f"调试日志: {rel_debug_log_file}")
    if invalid_litellm_level is not None:
        logging.warning(
            "LITELLM_LOG_LEVEL=%r 无效，已回退为 %s；可选值：%s",
            invalid_litellm_level,
            _DEFAULT_LITELLM_LOG_LEVEL,
            ", ".join(_ALLOWED_LOG_LEVELS),
        )
    return dedup_filter
