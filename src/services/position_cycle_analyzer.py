"""Position cycle analyzer for medium/long-term holding advice.

Reuses the existing Agent framework (build_agent_executor) with long-term
strategies: growth_quality, expectation_repricing, wave_theory, chan_theory.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from src.agent.factory import build_agent_executor
from src.agent.executor import AgentResult
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
        executor = build_agent_executor(skills=self.agent_skills)
        name = position.name or position.code
        user_message = (
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
        result: AgentResult = executor.run(task=user_message)
        return result.content

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
