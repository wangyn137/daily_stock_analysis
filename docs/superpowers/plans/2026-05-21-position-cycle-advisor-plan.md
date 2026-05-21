# 中长期持仓投资顾问系统 - 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现中长期持仓投资顾问系统，在现有 daily_stock_analysis 项目基础上新增持仓阶段分析、触发引擎和飞书报告推送。

**Architecture:** 独立模块设计，不侵入现有每日分析流程。PositionCycleAnalyzer 复用 Agent 框架 + 中长期策略，TriggerEngine 管理定期/阈值/事件三种触发模式，PortfolioReportFormatter 生成详细版飞书兼容报告。

**Tech Stack:** Python 3.10+, Pydantic, FastAPI, sqlalchemy, APScheduler (已有依赖)

---

## File Structure

### New Files
- `src/services/position_cycle_analyzer.py` - 中长期持仓分析器（核心）
- `src/core/trigger_engine.py` - 三种触发模式管理
- `src/formatters/portfolio_report_formatter.py` - 持仓报告格式化

### Modified Files
- `src/config.py` - 新增持仓顾问系统配置项
- `.env.example` - 新增环境变量模板
- `api/v1/endpoints/portfolio.py` - 新增分析触发和报告查询端点
- `api/v1/schemas/portfolio.py` - 新增 API schema
- `src/schemas/report_schema.py` - 新增持仓分析 schema
- `docs/CHANGELOG.md` - 记录变更

### Test Files
- `tests/test_position_cycle_analyzer.py`
- `tests/test_trigger_engine.py`
- `tests/test_portfolio_report_formatter.py`

---

### Task 1: 持仓分析 Schema 定义

**Files:**
- Create: `src/schemas/position_schemas.py`
- Test: verify dataclass creation

- [ ] **Step 1: Define position context and analysis result dataclasses**

```python
# src/schemas/position_schemas.py
"""Position cycle analysis schemas."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional


@dataclass
class Position:
    """Single position data."""
    code: str
    name: str
    quantity: int
    cost_price: float
    buy_date: Optional[date] = None


@dataclass
class PositionContext:
    """Context for position cycle analysis."""
    positions: List[Position]
    average_cost: float
    total_value: float
    holding_period: int  # days
    target_return: float  # target return %
    market_context: Optional[dict] = None
    current_price: Optional[float] = None


@dataclass
class CycleAnalysisResult:
    """Output of position cycle analysis per stock."""
    code: str
    name: str
    decision: str  # 建仓/加仓/持有/减仓/清仓
    sentiment_score: int  # 0-100
    trend_prediction: str  # 看多/震荡/看空
    target_price: Optional[float] = None
    stop_loss: Optional[float] = None
    confidence_level: str  # 高/中/低
    reason: str = ""
    action_checklist: List[str] = field(default_factory=list)
    risk_alerts: List[str] = field(default_factory=list)
    catalysts: List[str] = field(default_factory=list)
    historical_return: Optional[float] = None
    holding_period_days: Optional[int] = None


@dataclass
class PositionAnalysisReport:
    """Full portfolio analysis report."""
    generated_at: str
    reports: List[CycleAnalysisResult]
    total_positions: int
    summary: str = ""
```

- [ ] **Step 2: Verify file compiles**

Run: `python -m py_compile src/schemas/position_schemas.py`
Expected: exit code 0, no output

- [ ] **Step 3: Commit**

```bash
git add src/schemas/position_schemas.py
git commit -m "feat: add position cycle analysis schemas"
```

---

### Task 2: 持仓顾问配置项

**Files:**
- Modify: `src/config.py`
- Modify: `.env.example`

- [ ] **Step 1: Add portfolio advisor config dataclass and env parsing**

在 `src/config.py` 中添加持仓顾问系统配置。找到 `Config` dataclass 定义（约 1300 行左右，有多个 `@dataclass` 类），在现有 `Config` 类中新增字段。

先 grep 定位 Config 类：

```bash
grep -n "class Config" src/config.py
```

Expected output: `class Config` 所在行号

- [ ] **Step 2: Insert config fields into Config class**

```python
# 在 Config 类中（约 350 行处，`REPORT_SHOW_LLM_MODEL` 附近）添加：

# === Position Cycle Advisor Config ===
PORTFOLIO_TRIGGER_ENABLED: bool = True
PORTFOLIO_WEEKLY_DAY: str = "monday"
PORTFOLIO_WEEKLY_HOUR: int = 9
PORTFOLIO_WEEKLY_MINUTE: int = 0
PORTFOLIO_THRESHOLD_PCT: float = 10.0
PORTFOLIO_THRESHOLD_COOLDOWN_MINUTES: int = 60
PORTFOLIO_REPORT_CHANNEL: str = "feishu"
```

找到 `_load_from_env` 方法（约 `parse_env_bool` 和 `parse_env_int` 之后），在对应位置补充解析逻辑：

```python
# 在 _load_from_env 方法中，与上述字段对应位置添加：
self.PORTFOLIO_TRIGGER_ENABLED = parse_env_bool(os.getenv("PORTFOLIO_TRIGGER_ENABLED"), True)
self.PORTFOLIO_WEEKLY_DAY = os.getenv("PORTFOLIO_WEEKLY_DAY", "monday")
self.PORTFOLIO_WEEKLY_HOUR = parse_env_int(os.getenv("PORTFOLIO_WEEKLY_HOUR"), 9, field_name="PORTFOLIO_WEEKLY_HOUR", minimum=0, maximum=23)
self.PORTFOLIO_WEEKLY_MINUTE = parse_env_int(os.getenv("PORTFOLIO_WEEKLY_MINUTE"), 0, field_name="PORTFOLIO_WEEKLY_MINUTE", minimum=0, maximum=59)
self.PORTFOLIO_THRESHOLD_PCT = parse_env_float(os.getenv("PORTFOLIO_THRESHOLD_PCT"), 10.0, field_name="PORTFOLIO_THRESHOLD_PCT", minimum=1.0, maximum=50.0)
self.PORTFOLIO_THRESHOLD_COOLDOWN_MINUTES = parse_env_int(os.getenv("PORTFOLIO_THRESHOLD_COOLDOWN_MINUTES"), 60, field_name="PORTFOLIO_THRESHOLD_COOLDOWN_MINUTES", minimum=0, maximum=1440)
self.PORTFOLIO_REPORT_CHANNEL = os.getenv("PORTFOLIO_REPORT_CHANNEL", "feishu")
```

- [ ] **Step 3: Add env var templates to .env.example**

在 `.env.example` 末尾（`ENABLE_EASTMONEY_PATCH` 之前的部分）、Portfolio 配置区域添加：

```bash
# 在 # PORTFOLIO_P0: 导入 / 风险 / 汇率降级配置 段落末尾追加：

# PORTFOLIO_TRIGGER_ENABLED=true
# PORTFOLIO_WEEKLY_DAY=monday
# PORTFOLIO_WEEKLY_HOUR=9
# PORTFOLIO_WEEKLY_MINUTE=0
# PORTFOLIO_THRESHOLD_PCT=10.0
# PORTFOLIO_THRESHOLD_COOLDOWN_MINUTES=60
# PORTFOLIO_REPORT_CHANNEL=feishu
```

- [ ] **Step 4: Verify file compiles**

Run: `python -m py_compile src/config.py`
Expected: exit code 0

- [ ] **Step 5: Commit**

```bash
git add src/config.py .env.example
git commit -m "feat: add position cycle advisor config"
```

---

### Task 3: 触发引擎 (TriggerEngine)

**Files:**
- Create: `src/core/trigger_engine.py`
- Tests: pending Task 8

- [ ] **Step 1: Create TriggerEngine with weekly scheduler and threshold watcher**

```python
# src/core/trigger_engine.py
"""Trigger engine for portfolio position cycle analysis.

Supports three trigger modes:
1. Weekly scheduled — generate weekly portfolio report
2. Threshold — single stock price change exceeds threshold % triggers recommendation
3. Event — earnings release / technical breakout / policy change triggers event interpretation
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Set

from src.config import Config

logger = logging.getLogger(__name__)


@dataclass
class TriggerEvent:
    """A trigger event fired by TriggerEngine."""
    trigger_type: str  # "weekly" | "threshold" | "event"
    stock_codes: List[str]
    event_detail: str = ""
    triggered_at: str = ""


@dataclass
class ThresholdState:
    """Per-stock threshold cooldown state."""
    last_trigger_price: float
    last_trigger_at: datetime


TriggerCallback = Callable[[TriggerEvent], None]


class TriggerEngine:
    """Manage three trigger modes for portfolio analysis."""

    def __init__(
        self,
        *,
        config: Optional[Config] = None,
        callback: Optional[TriggerCallback] = None,
    ):
        self.config = config or Config.get_instance()
        self.callback = callback
        self._threshold_states: Dict[str, ThresholdState] = {}
        self._cooldown_minutes: int = self.config.portfolio_threshold_cooldown_minutes
        self._threshold_pct: float = self.config.portfolio_threshold_pct
        self._enabled: bool = self.config.portfolio_trigger_enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    def register_callback(self, callback: TriggerCallback) -> None:
        """Register callback for trigger events."""
        self.callback = callback

    def check_weekly_trigger(self, now: Optional[datetime] = None) -> Optional[TriggerEvent]:
        """Check if weekly trigger should fire."""
        if not self._enabled:
            return None
        now = now or datetime.now()
        day_map = {
            "monday": 0, "tuesday": 1, "wednesday": 2,
            "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
        }
        target_dow = day_map.get(self.config.portfolio_weekly_day.lower(), 0)
        if now.weekday() != target_dow:
            return None
        if now.hour != self.config.portfolio_weekly_hour:
            return None
        if now.minute != self.config.portfolio_weekly_minute:
            return None
        return TriggerEvent(
            trigger_type="weekly",
            stock_codes=[],
            event_detail=f"Weekly portfolio report at {now.strftime('%Y-%m-%d %H:%M')}",
            triggered_at=now.isoformat(),
        )

    def check_threshold_trigger(
        self,
        code: str,
        current_price: float,
        cost_price: float,
        now: Optional[datetime] = None,
    ) -> Optional[TriggerEvent]:
        """Check if a stock's price change exceeds threshold."""
        if not self._enabled:
            return None
        now = now or datetime.now()

        # Check cooldown
        state = self._threshold_states.get(code)
        if state:
            elapsed = (now - state.last_trigger_at).total_seconds() / 60
            if elapsed < self._cooldown_minutes:
                return None

        change_pct = ((current_price - cost_price) / cost_price) * 100
        if abs(change_pct) < self._threshold_pct:
            return None

        direction = "up" if change_pct > 0 else "down"
        self._threshold_states[code] = ThresholdState(
            last_trigger_price=current_price,
            last_trigger_at=now,
        )
        return TriggerEvent(
            trigger_type="threshold",
            stock_codes=[code],
            event_detail=(
                f"{code} price moved {direction} by {abs(change_pct):.1f}% "
                f"(threshold: {self._threshold_pct}%)"
            ),
            triggered_at=now.isoformat(),
        )

    def check_event_trigger(
        self,
        code: str,
        event_type: str,
        detail: str = "",
    ) -> Optional[TriggerEvent]:
        """Check and fire an event-based trigger."""
        if not self._enabled:
            return None
        supported_events = {"earnings_release", "technical_breakout", "policy_change"}
        if event_type not in supported_events:
            logger.warning("Unsupported event type: %s", event_type)
            return None
        return TriggerEvent(
            trigger_type="event",
            stock_codes=[code],
            event_detail=f"{event_type}: {detail}",
            triggered_at=datetime.now().isoformat(),
        )

    def fire(self, event: TriggerEvent) -> None:
        """Fire a trigger event via callback."""
        if self.callback:
            try:
                self.callback(event)
            except Exception:
                logger.exception("Trigger callback failed for event: %s", event)

    def reset_threshold_state(self, code: Optional[str] = None) -> None:
        """Reset threshold cooldown state for testing or manual override."""
        if code:
            self._threshold_states.pop(code, None)
        else:
            self._threshold_states.clear()
```

- [ ] **Step 2: Verify file compiles**

Run: `python -m py_compile src/core/trigger_engine.py`
Expected: exit code 0

- [ ] **Step 3: Commit**

```bash
git add src/core/trigger_engine.py
git commit -m "feat: add trigger engine for portfolio analysis"
```

---

### Task 4: 中长期持仓分析器 (PositionCycleAnalyzer)

**Files:**
- Create: `src/services/position_cycle_analyzer.py`
- Tests: pending Task 8

- [ ] **Step 1: Create PositionCycleAnalyzer with Agent integration**

```python
# src/services/position_cycle_analyzer.py
"""Position cycle analyzer for medium/long-term holding advice.

Reuses the existing Agent framework (build_agent_executor) with long-term
strategies: growth_quality, expectation_repricing, wave_theory, chan_theory.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from src.agent.factory import build_agent_executor
from src.schemas.position_schemas import (
    CycleAnalysisResult,
    Position,
    PositionAnalysisReport,
    PositionContext,
)

logger = logging.getLogger(__name__)

# Long-term strategies for position cycle analysis
LONG_TERM_STRATEGIES = [
    "growth_quality",
    "expectation_repricing",
    "wave_theory",
    "chan_theory",
]


class PositionCycleAnalyzer:
    """Analyze positions and generate medium/long-term cycle advice."""

    def __init__(
        self,
        *,
        agent_skills: Optional[List[str]] = None,
    ):
        self.agent_skills = agent_skills or LONG_TERM_STRATEGIES

    def build_context(self, position: Position, current_price: float) -> PositionContext:
        """Build PositionContext from a single position."""
        holding_period = 0
        if position.buy_date:
            holding_period = (date.today() - position.buy_date).days
        total_value = position.quantity * current_price
        cost_total = position.quantity * position.cost_price
        historical_return = ((current_price - position.cost_price) / position.cost_price) * 100

        return PositionContext(
            positions=[position],
            average_cost=position.cost_price,
            total_value=total_value,
            holding_period=holding_period,
            target_return=15.0,
            current_price=current_price,
            market_context={
                "holding_return": round(historical_return, 2),
                "holding_days": holding_period,
            },
        )

    def analyze_position(
        self,
        position: Position,
        current_price: float,
    ) -> CycleAnalysisResult:
        """Analyze a single position using Agent framework with long-term strategies."""
        ctx = self.build_context(position, current_price)

        try:
            agent_result = self._run_agent_analysis(position, ctx)
            return self._parse_agent_result(position, agent_result)
        except Exception as exc:
            logger.exception("Agent analysis failed for %s", position.code)
            return self._fallback_result(position, str(exc))

    def _run_agent_analysis(
        self,
        position: Position,
        ctx: PositionContext,
    ) -> str:
        """Run agent executor with long-term strategies."""
        system_prompt = self._build_system_prompt(position, ctx)
        executor = build_agent_executor(
            skills=self.agent_skills,
            system_prompt=system_prompt,
        )
        user_message = (
            f"请对 {position.name} ({position.code}) 进行中长期持仓分析。"
            f"当前价格: {ctx.current_price:.2f}, 成本价: {position.cost_price:.2f}, "
            f"持仓天数: {ctx.holding_period}天。请给出阶段建议（建仓/加仓/持有/减仓/清仓）。"
        )
        result = executor.invoke({"input": user_message})
        return str(result.get("output", ""))

    def _build_system_prompt(self, position: Position, ctx: PositionContext) -> str:
        """Build system prompt for position cycle analysis."""
        name = position.name or position.code
        return (
            f"你是一个中长期持仓投资顾问。请分析 {name} ({position.code}) 的持仓周期。\n"
            f"成本价: {position.cost_price:.2f}, 当前价: {ctx.current_price:.2f}, "
            f"持仓天数: {ctx.holding_period}天。\n"
            f"请从以下维度分析：\n"
            f"1. 技术面中长期趋势（周线/月线级别）\n"
            f"2. 基本面估值合理性\n"
            f"3. 行业前景与催化因素\n"
            f"4. 风险提示\n\n"
            f"最终给出操作建议：建仓/加仓/持有/减仓/清仓，并给出目标价和止损价。\n"
            f"输出格式：\n"
            f"决策: [建仓/加仓/持有/减仓/清仓]\n"
            f"评分: [0-100]\n"
            f"趋势: [看多/震荡/看空]\n"
            f"目标价: [价格]\n"
            f"止损价: [价格]\n"
            f"置信度: [高/中/低]\n"
            f"理由: [简要说明]\n"
            f"操作清单: [- 项1\\n- 项2]\n"
            f"风险提示: [- 风险1\\n- 风险2]\n"
            f"催化剂: [- 催化剂1\\n- 催化剂2]"
        )

    def _parse_agent_result(self, position: Position, raw: str) -> CycleAnalysisResult:
        """Parse agent output into structured CycleAnalysisResult."""
        result = self._fallback_result(position, "analysis incomplete")

        lines = raw.strip().split("\n")
        for line in lines:
            line_stripped = line.strip()
            if ":" not in line_stripped:
                continue
            key, _, value = line_stripped.partition(":")
            value = value.strip()
            if not value:
                continue

            if key == "决策":
                result.decision = value
            elif key == "评分":
                try:
                    result.sentiment_score = int(float(value))
                except ValueError:
                    pass
            elif key == "趋势":
                result.trend_prediction = value
            elif key == "目标价":
                try:
                    result.target_price = float(value.replace(",", "").replace(" ", ""))
                except ValueError:
                    pass
            elif key == "止损价":
                try:
                    result.stop_loss = float(value.replace(",", "").replace(" ", ""))
                except ValueError:
                    pass
            elif key == "置信度":
                result.confidence_level = value
            elif key == "理由":
                result.reason = value
            elif key == "操作清单":
                result.action_checklist = [
                    item.strip().lstrip("- ").strip()
                    for item in value.split("\\n") if item.strip()
                ]
            elif key == "风险提示":
                result.risk_alerts = [
                    item.strip().lstrip("- ").strip()
                    for item in value.split("\\n") if item.strip()
                ]
            elif key == "催化剂":
                result.catalysts = [
                    item.strip().lstrip("- ").strip()
                    for item in value.split("\\n") if item.strip()
                ]

        return result

    def _fallback_result(self, position: Position, reason: str) -> CycleAnalysisResult:
        """Generate safe fallback result when analysis fails."""
        return CycleAnalysisResult(
            code=position.code,
            name=position.name or "",
            decision="持有",
            sentiment_score=50,
            trend_prediction="震荡",
            confidence_level="低",
            reason=f"分析暂不可用: {reason}",
            action_checklist=["系统分析异常，建议参考其他信息源后再做决策"],
            risk_alerts=["分析服务暂不可用"],
            catalysts=[],
        )

    def analyze_portfolio(
        self,
        positions: List[Position],
        prices: Dict[str, float],
    ) -> PositionAnalysisReport:
        """Analyze all positions in a portfolio."""
        reports: List[CycleAnalysisResult] = []
        for position in positions:
            current_price = prices.get(position.code, position.cost_price)
            result = self.analyze_position(position, current_price)
            reports.append(result)

        hold_count = sum(1 for r in reports if r.decision in ("持有", "建仓", "加仓"))
        reduce_count = sum(1 for r in reports if r.decision in ("减仓", "清仓"))
        summary = (
            f"共分析 {len(reports)} 只持仓。建议持有/加仓 {hold_count} 只, "
            f"建议减仓/清仓 {reduce_count} 只。"
        )

        return PositionAnalysisReport(
            generated_at=datetime.now().isoformat(),
            reports=reports,
            total_positions=len(reports),
            summary=summary,
        )
```

- [ ] **Step 2: Verify file compiles**

Run: `python -m py_compile src/services/position_cycle_analyzer.py`
Expected: exit code 0

- [ ] **Step 3: Commit**

```bash
git add src/services/position_cycle_analyzer.py
git commit -m "feat: add position cycle analyzer with agent integration"
```

---

### Task 5: 持仓报告格式化 (PortfolioReportFormatter)

**Files:**
- Create: `src/formatters/portfolio_report_formatter.py`
- Tests: pending Task 8

- [ ] **Step 1: Create report formatter generating Feishu-compatible Markdown**

```python
# src/formatters/portfolio_report_formatter.py
"""Portfolio report formatter for detailed position analysis reports.

Generates Feishu-compatible Markdown reports from CycleAnalysisResult data.
"""

from __future__ import annotations

from datetime import datetime
from typing import List

from src.schemas.position_schemas import (
    CycleAnalysisResult,
    PositionAnalysisReport,
)


def _sentiment_emoji(score: int) -> str:
    if score >= 70:
        return "🟢"
    elif score >= 45:
        return "🟡"
    return "🔴"


def _confidence_label(level: str) -> str:
    mapping = {"高": "高", "中": "中", "低": "低"}
    return mapping.get(level, level)


def format_single_report(result: CycleAnalysisResult) -> str:
    """Format a single stock's analysis result as Feishu-compatible Markdown."""
    emoji = _sentiment_emoji(result.sentiment_score)
    lines = [
        f"## {result.name} ({result.code})",
        f"**操作建议**: {result.decision}  {emoji}",
        f"**综合评分**: {result.sentiment_score}/100",
        f"**趋势判断**: {result.trend_prediction}",
        f"**置信度**: {_confidence_label(result.confidence_level)}",
    ]

    if result.target_price is not None:
        lines.append(f"**目标价**: {result.target_price:.2f}")
    if result.stop_loss is not None:
        lines.append(f"**止损价**: {result.stop_loss:.2f}")
    if result.historical_return is not None:
        lines.append(f"**持仓收益率**: {result.historical_return:+.2f}%")
    if result.holding_period_days is not None:
        lines.append(f"**持仓天数**: {result.holding_period_days}天")

    lines.append(f"\n**分析理由**: {result.reason}")

    if result.action_checklist:
        lines.append("\n**操作清单**:")
        for item in result.action_checklist:
            lines.append(f"- {item}")

    if result.risk_alerts:
        lines.append("\n**风险提示**:")
        for alert in result.risk_alerts:
            lines.append(f"- ⚠️ {alert}")

    if result.catalysts:
        lines.append("\n**催化剂/关注点**:")
        for cat in result.catalysts:
            lines.append(f"- {cat}")

    lines.append("")
    return "\n".join(lines)


def format_portfolio_report(report: PositionAnalysisReport) -> str:
    """Format full portfolio analysis report as Feishu-compatible Markdown."""
    header = [
        "📊 **持仓分析报告**",
        f"**生成时间**: {report.generated_at}",
        f"**持仓总数**: {report.total_positions} 只",
        "",
        "---",
        "",
        report.summary,
        "",
        "---",
        "",
    ]

    sections = [format_single_report(r) for r in report.reports]

    footer = [
        "---",
        "",
        "⚠️ 本报告由 AI 生成，仅供参考，不构成投资建议。",
        "投资有风险，决策需谨慎。",
    ]

    return "\n".join(header + sections + footer)
```

- [ ] **Step 2: Verify file compiles**

Run: `python -m py_compile src/formatters/portfolio_report_formatter.py`
Expected: exit code 0

- [ ] **Step 3: Commit**

```bash
git add src/formatters/portfolio_report_formatter.py
git commit -m "feat: add portfolio report formatter"
```

---

### Task 6: API 端点扩展

**Files:**
- Modify: `api/v1/endpoints/portfolio.py`
- Create: `api/v1/schemas/portfolio.py` (verify existing, add schemas as needed)
- Tests: pending Task 8

- [ ] **Step 1: Add position analysis API schemas**

先查看现有 schema 文件末尾，确认追加位置：

```bash
wc -l api/v1/schemas/portfolio.py
```

在 `api/v1/schemas/portfolio.py` 末尾追加：

```python
# === Position Cycle Advisor Schemas ===


class PositionAnalysisRequest(BaseModel):
    """Request to trigger position analysis."""
    codes: Optional[List[str]] = None  # empty = all positions
    trigger_type: str = "manual"  # manual | threshold | event


class PositionAnalysisJobResponse(BaseModel):
    """Response for submitted analysis job."""
    success: bool
    job_id: str
    estimated_completion: Optional[str] = None


class CycleAnalysisItem(BaseModel):
    """Single stock cycle analysis result in API response."""
    code: str
    name: str = ""
    decision: str = "持有"
    sentiment_score: int = 50
    trend_prediction: str = "震荡"
    target_price: Optional[float] = None
    stop_loss: Optional[float] = None
    confidence_level: str = "中"
    reason: str = ""
    action_checklist: List[str] = []
    risk_alerts: List[str] = []
    catalysts: List[str] = []


class PositionAnalysisReportResponse(BaseModel):
    """Position analysis report response."""
    job_id: str
    status: str  # pending | running | completed | failed
    reports: List[CycleAnalysisItem] = []
    summary: str = ""
    generated_at: str = ""
```

- [ ] **Step 2: Add analysis endpoint to portfolio router**

在 `api/v1/endpoints/portfolio.py` 末尾添加：

```python
# === Position Cycle Advisor Endpoints ===
import uuid
from src.services.position_cycle_analyzer import PositionCycleAnalyzer
from src.services.portfolio_service import PortfolioService

# In-memory job store (simple; use DB when persistence needed)
_analysis_jobs: Dict[str, Dict[str, Any]] = {}
_analyzer = PositionCycleAnalyzer()
_portfolio_svc_for_analysis = PortfolioService()


@router.post("/analyze", response_model=PositionAnalysisJobResponse)
async def trigger_position_analysis(
    request: PositionAnalysisRequest,
):
    """Trigger position cycle analysis."""
    job_id = str(uuid.uuid4())
    _analysis_jobs[job_id] = {
        "job_id": job_id,
        "status": "pending",
        "reports": [],
        "summary": "",
        "generated_at": "",
    }

    try:
        # Get current positions from portfolio service
        positions = _portfolio_svc_for_analysis.get_all_positions()
        if request.codes:
            positions = [p for p in positions if p["code"] in request.codes]

        if not positions:
            _analysis_jobs[job_id]["status"] = "completed"
            _analysis_jobs[job_id]["summary"] = "当前无持仓数据"
            return PositionAnalysisJobResponse(
                success=True, job_id=job_id,
            )

        # Get current prices for each position
        from data_provider.base import get_data_provider
        provider = get_data_provider()
        prices = {}
        for pos in positions:
            try:
                quote = provider.get_realtime_quote(pos["code"])
                if quote:
                    prices[pos["code"]] = quote.get("price", 0.0) or quote.get("close", 0.0)
            except Exception:
                prices[pos["code"]] = pos.get("cost_price", 0.0)

        # Build Position objects and run analysis
        from src.schemas.position_schemas import Position
        pos_objects = [
            Position(
                code=p["code"],
                name=p.get("name", ""),
                quantity=int(p.get("quantity", 0)),
                cost_price=float(p.get("cost_price", 0.0)),
                buy_date=p.get("buy_date"),
            )
            for p in positions
        ]

        report = _analyzer.analyze_portfolio(pos_objects, prices)

        _analysis_jobs[job_id]["status"] = "completed"
        _analysis_jobs[job_id]["reports"] = [
            CycleAnalysisItem(
                code=r.code, name=r.name, decision=r.decision,
                sentiment_score=r.sentiment_score,
                trend_prediction=r.trend_prediction,
                target_price=r.target_price,
                stop_loss=r.stop_loss,
                confidence_level=r.confidence_level,
                reason=r.reason,
                action_checklist=r.action_checklist,
                risk_alerts=r.risk_alerts,
                catalysts=r.catalysts,
            )
            for r in report.reports
        ]
        _analysis_jobs[job_id]["summary"] = report.summary
        _analysis_jobs[job_id]["generated_at"] = report.generated_at

    except Exception as exc:
        _analysis_jobs[job_id]["status"] = "failed"
        logger.error("Position analysis failed: %s", exc)

    return PositionAnalysisJobResponse(
        success=True,
        job_id=job_id,
        estimated_completion=datetime.now().isoformat(),
    )


@router.get("/reports/{job_id}", response_model=PositionAnalysisReportResponse)
async def get_position_report(job_id: str):
    """Get position analysis report by job ID."""
    job = _analysis_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    return PositionAnalysisReportResponse(
        job_id=job["job_id"],
        status=job["status"],
        reports=job.get("reports", []),
        summary=job.get("summary", ""),
        generated_at=job.get("generated_at", ""),
    )
```

- [ ] **Step 3: Verify file compiles**

Run: `python -m py_compile api/v1/endpoints/portfolio.py && python -m py_compile api/v1/schemas/portfolio.py`
Expected: exit code 0

- [ ] **Step 4: Commit**

```bash
git add api/v1/endpoints/portfolio.py api/v1/schemas/portfolio.py
git commit -m "feat: add position analysis API endpoints"
```

---

### Task 7: 飞书报告推送集成

**Files:**
- Modify: `src/formatters/portfolio_report_formatter.py` 中补充 Feishu 专用的整体报告发送辅助函数
- 无需修改 FeishuSender — 其 `send_to_feishu(content)` 已直接支持 `lark_md` Markdown 内容

- [ ] **Step 1: 确认 FeishuSender 可直接复用**

FeishuSender 接口 (src/notification_sender/feishu_sender.py):
- `send_to_feishu(content: str, *, timeout_seconds: Optional[float] = None) -> bool`
- 发送 `lark_md` 格式 Markdown 到飞书交互卡片
- 超长内容自动分批
- 持仓报告文本直接调用 `feishu_sender.send_to_feishu(report_text)` 即可

- [ ] **Step 2: 在 portfolio_report_formatter 中添加 Feishu 发送辅助函数**

```python
# 在 src/formatters/portfolio_report_formatter.py 末尾追加：

def send_report_via_feishu(
    report: PositionAnalysisReport,
    feishu_sender,
    title: str = "📊 持仓分析报告",
) -> bool:
    """Send formatted portfolio report via Feishu.

    Args:
        report: The position analysis report to send.
        feishu_sender: An instance of FeishuSender.
        title: Optional title prefix (ignored, Feishu card uses its own header).

    Returns:
        True if sent successfully.
    """
    report_text = format_portfolio_report(report)
    return feishu_sender.send_to_feishu(report_text)
```

- [ ] **Step 3: Verify file compiles**

Run: `python -m py_compile src/formatters/portfolio_report_formatter.py`
Expected: exit code 0

- [ ] **Step 4: Commit**

```bash
git add src/formatters/portfolio_report_formatter.py
git commit -m "feat: add Feishu delivery helper for portfolio reports"
```

---

### Task 8: 单测

**Files:**
- Create: `tests/test_trigger_engine.py`
- Create: `tests/test_position_cycle_analyzer.py`
- Create: `tests/test_portfolio_report_formatter.py`

- [ ] **Step 1: Write TriggerEngine tests**

```python
# tests/test_trigger_engine.py
"""Tests for TriggerEngine."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from src.core.trigger_engine import TriggerEngine, TriggerEvent


class TestTriggerEngine(unittest.TestCase):

    def setUp(self):
        self.engine = TriggerEngine()
        self.engine._enabled = True
        self.engine._threshold_pct = 10.0
        self.engine._cooldown_minutes = 60
        self.caught_events = []

        def collect(event: TriggerEvent):
            self.caught_events.append(event)

        self.engine.register_callback(collect)

    def test_threshold_trigger_fires_on_big_up_move(self):
        event = self.engine.check_threshold_trigger(
            "600519", current_price=22.0, cost_price=18.0,
        )
        self.assertIsNotNone(event)
        self.assertEqual(event.trigger_type, "threshold")
        self.assertIn("600519", event.stock_codes)
        self.assertIn("up", event.event_detail)

    def test_threshold_trigger_fires_on_big_down_move(self):
        event = self.engine.check_threshold_trigger(
            "600519", current_price=15.0, cost_price=18.0,
        )
        self.assertIsNotNone(event)
        self.assertIn("down", event.event_detail)

    def test_threshold_trigger_ignores_small_move(self):
        event = self.engine.check_threshold_trigger(
            "600519", current_price=19.0, cost_price=18.0,
        )
        self.assertIsNone(event)

    def test_threshold_cooldown_suppresses_duplicate(self):
        now = datetime.now()
        event1 = self.engine.check_threshold_trigger(
            "600519", current_price=22.0, cost_price=18.0, now=now,
        )
        self.assertIsNotNone(event1)

        event2 = self.engine.check_threshold_trigger(
            "600519", current_price=25.0, cost_price=18.0,
            now=now + timedelta(minutes=5),
        )
        self.assertIsNone(event2)  # within cooldown

    def test_threshold_cooldown_expires(self):
        now = datetime.now()
        event1 = self.engine.check_threshold_trigger(
            "600519", current_price=22.0, cost_price=18.0, now=now,
        )
        self.assertIsNotNone(event1)

        event2 = self.engine.check_threshold_trigger(
            "600519", current_price=25.0, cost_price=18.0,
            now=now + timedelta(minutes=90),
        )
        self.assertIsNotNone(event2)  # cooldown expired

    def test_event_trigger_supported_types(self):
        event = self.engine.check_event_trigger("600519", "earnings_release", "Q2 earnings beat")
        self.assertIsNotNone(event)
        self.assertEqual(event.trigger_type, "event")

    def test_event_trigger_unsupported_type(self):
        event = self.engine.check_event_trigger("600519", "unknown_event")
        self.assertIsNone(event)

    def test_fire_invokes_callback(self):
        event = TriggerEvent(trigger_type="test", stock_codes=["600519"], event_detail="test", triggered_at="now")
        self.engine.fire(event)
        self.assertEqual(len(self.caught_events), 1)
        self.assertEqual(self.caught_events[0].trigger_type, "test")

    def test_disabled_engine_returns_none(self):
        self.engine._enabled = False
        self.assertIsNone(self.engine.check_weekly_trigger(datetime.now()))
        self.assertIsNone(self.engine.check_threshold_trigger("600519", 22.0, 18.0))

    def test_weekly_trigger_matches_schedule(self):
        # Monday at 09:00
        monday_9 = datetime(2026, 5, 25, 9, 0)  # a Monday
        event = self.engine.check_weekly_trigger(now=monday_9)
        self.assertIsNotNone(event)
        self.assertEqual(event.trigger_type, "weekly")

    def test_weekly_trigger_wrong_day(self):
        tuesday_9 = datetime(2026, 5, 26, 9, 0)  # a Tuesday
        event = self.engine.check_weekly_trigger(now=tuesday_9)
        self.assertIsNone(event)

    def test_weekly_trigger_wrong_hour(self):
        monday_10 = datetime(2026, 5, 25, 10, 0)
        event = self.engine.check_weekly_trigger(now=monday_10)
        self.assertIsNone(event)

    def test_reset_threshold_state(self):
        self.engine.check_threshold_trigger("600519", 22.0, 18.0)
        self.assertIn("600519", self.engine._threshold_states)
        self.engine.reset_threshold_state("600519")
        self.assertNotIn("600519", self.engine._threshold_states)
```

- [ ] **Step 2: Run TriggerEngine tests to verify they pass**

Run: `python -m pytest tests/test_trigger_engine.py -v`
Expected: all tests PASS

- [ ] **Step 3: Write PortfolioReportFormatter tests**

```python
# tests/test_portfolio_report_formatter.py
"""Tests for PortfolioReportFormatter."""

from __future__ import annotations

import unittest

from src.formatters.portfolio_report_formatter import (
    format_portfolio_report,
    format_single_report,
)
from src.schemas.position_schemas import (
    CycleAnalysisResult,
    PositionAnalysisReport,
)


class TestPortfolioReportFormatter(unittest.TestCase):

    def test_format_single_report_contains_decision(self):
        result = CycleAnalysisResult(
            code="600519",
            name="贵州茅台",
            decision="持有",
            sentiment_score=65,
            trend_prediction="看多",
            target_price=2100.0,
            stop_loss=1700.0,
            confidence_level="高",
            reason="基本面稳健，技术面多头排列",
            action_checklist=["当前估值合理，建议持有", "若跌破1700考虑减仓"],
            risk_alerts=["飞天批价下跌"],
            catalysts=["中秋国庆旺季动销"],
            historical_return=8.5,
            holding_period_days=120,
        )

        text = format_single_report(result)

        self.assertIn("600519", text)
        self.assertIn("贵州茅台", text)
        self.assertIn("持有", text)
        self.assertIn("65", text)
        self.assertIn("2100", text)
        self.assertIn("1700", text)
        self.assertIn("基本面稳健", text)

    def test_format_full_portfolio_report(self):
        results = [
            CycleAnalysisResult(
                code="600519", name="贵州茅台",
                decision="持有", sentiment_score=65,
                trend_prediction="看多", confidence_level="高",
                reason="ok",
            ),
            CycleAnalysisResult(
                code="300750", name="宁德时代",
                decision="减仓", sentiment_score=35,
                trend_prediction="看空", confidence_level="中",
                reason="风险较高",
            ),
        ]
        report = PositionAnalysisReport(
            generated_at="2026-05-21T10:00:00",
            reports=results,
            total_positions=2,
            summary="建议持有1只，减仓1只",
        )

        text = format_portfolio_report(report)

        self.assertIn("持仓分析报告", text)
        self.assertIn("600519", text)
        self.assertIn("300750", text)
        self.assertIn("建议持有1只", text)
        self.assertIn("不构成投资建议", text)

    def test_empty_report_generates_valid_markdown(self):
        report = PositionAnalysisReport(
            generated_at="2026-05-21T10:00:00",
            reports=[],
            total_positions=0,
            summary="无持仓数据",
        )
        text = format_portfolio_report(report)
        self.assertIn("持仓分析报告", text)
        self.assertIn("0 只", text)

    def test_single_report_without_optional_fields(self):
        result = CycleAnalysisResult(
            code="AAPL", name="Apple",
            decision="建仓", sentiment_score=50,
            trend_prediction="震荡", confidence_level="中",
            reason="fair value",
        )
        text = format_single_report(result)
        self.assertIn("AAPL", text)
        self.assertIn("建仓", text)
        # optional fields should not appear
        self.assertNotIn("目标价", text)
        self.assertNotIn("止损价", text)
```

- [ ] **Step 4: Run formatter tests**

Run: `python -m pytest tests/test_portfolio_report_formatter.py -v`
Expected: all tests PASS

- [ ] **Step 5: Write PositionCycleAnalyzer unit tests (mock agent)**

```python
# tests/test_position_cycle_analyzer.py
"""Tests for PositionCycleAnalyzer (with mocked agent)."""

from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch, MagicMock

from src.schemas.position_schemas import Position, CycleAnalysisResult
from src.services.position_cycle_analyzer import PositionCycleAnalyzer


class TestPositionCycleAnalyzer(unittest.TestCase):

    def setUp(self):
        self.analyzer = PositionCycleAnalyzer(agent_skills=["growth_quality"])

    def test_build_context_calculates_holding_period(self):
        position = Position(
            code="600519", name="贵州茅台",
            quantity=100, cost_price=1800.0,
            buy_date=date(2026, 1, 15),
        )
        ctx = self.analyzer.build_context(position, current_price=2000.0)
        self.assertEqual(ctx.average_cost, 1800.0)
        self.assertEqual(ctx.total_value, 200000.0)
        expected_days = (date.today() - date(2026, 1, 15)).days
        self.assertEqual(ctx.holding_period, expected_days)

    def test_build_context_no_buy_date(self):
        position = Position(
            code="600519", name="贵州茅台",
            quantity=100, cost_price=1800.0,
        )
        ctx = self.analyzer.build_context(position, current_price=2000.0)
        self.assertEqual(ctx.holding_period, 0)

    def test_fallback_result_returns_safe_values(self):
        result = self.analyzer._fallback_result(
            Position(code="600519", name="贵州茅台", quantity=100, cost_price=1800.0),
            "agent timeout",
        )
        self.assertEqual(result.decision, "持有")
        self.assertEqual(result.sentiment_score, 50)
        self.assertEqual(result.confidence_level, "低")
        self.assertIn("agent timeout", result.reason)

    def test_parse_agent_result_full(self):
        raw = (
            "决策: 持有\n"
            "评分: 72\n"
            "趋势: 看多\n"
            "目标价: 2100\n"
            "止损价: 1700\n"
            "置信度: 高\n"
            "理由: 基本面稳健\n"
            "操作清单: [- 持有\\n- 跌破1700减仓]\n"
            "风险提示: [- 批价下跌\\n- 需求放缓]\n"
            "催化剂: [- 旺季动销\\n- i茅台增长]"
        )
        result = self.analyzer._parse_agent_result(
            Position(code="600519", name="贵州茅台", quantity=100, cost_price=1800.0),
            raw,
        )
        self.assertEqual(result.decision, "持有")
        self.assertEqual(result.sentiment_score, 72)
        self.assertEqual(result.trend_prediction, "看多")
        self.assertEqual(result.target_price, 2100.0)
        self.assertEqual(result.stop_loss, 1700.0)
        self.assertEqual(result.confidence_level, "高")
        self.assertIn("基本面稳健", result.reason)
        self.assertEqual(len(result.action_checklist), 2)
        self.assertEqual(len(result.risk_alerts), 2)
        self.assertEqual(len(result.catalysts), 2)

    def test_parse_agent_result_minimal(self):
        raw = "决策: 持有\n评分: 50\n趋势: 震荡\n置信度: 中\n理由: 观望"
        result = self.analyzer._parse_agent_result(
            Position(code="300750", name="宁德时代", quantity=100, cost_price=200.0),
            raw,
        )
        self.assertEqual(result.decision, "持有")
        self.assertEqual(result.sentiment_score, 50)
        self.assertIsNone(result.target_price)
        self.assertIsNone(result.stop_loss)

    def test_analyze_portfolio_empty(self):
        report = self.analyzer.analyze_portfolio([], {})
        self.assertEqual(report.total_positions, 0)
        self.assertEqual(len(report.reports), 0)

    @patch("src.services.position_cycle_analyzer.build_agent_executor")
    def test_analyze_position_agent_failure(self, mock_build):
        mock_executor = MagicMock()
        mock_executor.invoke.side_effect = Exception("API error")
        mock_build.return_value = mock_executor

        result = self.analyzer.analyze_position(
            Position(code="600519", name="贵州茅台", quantity=100, cost_price=1800.0),
            current_price=2000.0,
        )
        self.assertEqual(result.decision, "持有")
        self.assertEqual(result.confidence_level, "低")
        self.assertIn("API error", result.reason)
```

- [ ] **Step 6: Run all tests**

Run: `python -m pytest tests/test_position_cycle_analyzer.py tests/test_trigger_engine.py tests/test_portfolio_report_formatter.py -v`
Expected: all tests PASS

- [ ] **Step 7: Commit**

```bash
git add tests/test_trigger_engine.py tests/test_portfolio_report_formatter.py tests/test_position_cycle_analyzer.py
git commit -m "test: add tests for position cycle advisor modules"
```

---

### Task 9: 更新 CHANGELOG

- [ ] **Step 1: Update docs/CHANGELOG.md [Unreleased] section**

```bash
# 在 [Unreleased] 段追加：
# - [新功能] 新增中长期持仓投资顾问系统
# - [新功能] PositionCycleAnalyzer - 中长期持仓分析器
# - [新功能] TriggerEngine - 三种触发模式（定期/阈值/事件）
# - [新功能] PortfolioReportFormatter - 持仓报告格式化
# - [新功能] API端点 POST /api/v1/portfolio/analyze + GET /api/v1/portfolio/reports/{job_id}
# - [新功能] 飞书持仓报告推送
```

Run: `python -m py_compile docs/CHANGELOG.md` (非 Python 文件，验证文件存在即可)

- [ ] **Step 2: Commit**

```bash
git add docs/CHANGELOG.md
git commit -m "docs: update changelog for position cycle advisor"
```
