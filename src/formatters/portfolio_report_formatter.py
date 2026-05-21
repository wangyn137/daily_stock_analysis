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
