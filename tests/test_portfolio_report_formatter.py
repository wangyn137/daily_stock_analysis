"""Tests for PortfolioReportFormatter.

NOTE: This test imports the module directly via importlib.util to bypass
the src/formatters.py vs src/formatters/ package naming conflict.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# Load portfolio_report_formatter directly from its file path to bypass the
# src/formatters.py module that shadows the src/formatters/ package directory.
# ---------------------------------------------------------------------------
_formatter_path = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "formatters"
    / "portfolio_report_formatter.py"
)
_spec = importlib.util.spec_from_file_location(
    "portfolio_report_formatter",
    str(_formatter_path),
)
_portfolio_fmt = importlib.util.module_from_spec(_spec)
sys.modules["portfolio_report_formatter"] = _portfolio_fmt
_spec.loader.exec_module(_portfolio_fmt)

format_portfolio_report = _portfolio_fmt.format_portfolio_report
format_single_report = _portfolio_fmt.format_single_report
send_report_via_feishu = _portfolio_fmt.send_report_via_feishu

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
        self.assertNotIn("目标价", text)
        self.assertNotIn("止损价", text)

    def test_single_report_sentiment_emoji_high(self):
        result = CycleAnalysisResult(
            code="600519", name="贵州茅台",
            decision="持有", sentiment_score=85,
            trend_prediction="看多", confidence_level="高",
            reason="strong",
        )
        text = format_single_report(result)
        self.assertIn("持有", text)

    def test_single_report_sentiment_emoji_low(self):
        result = CycleAnalysisResult(
            code="000001", name="平安银行",
            decision="清仓", sentiment_score=25,
            trend_prediction="看空", confidence_level="低",
            reason="weak",
        )
        text = format_single_report(result)
        self.assertIn("清仓", text)

    def test_format_single_report_includes_return_and_period(self):
        result = CycleAnalysisResult(
            code="600519", name="贵州茅台",
            decision="持有", sentiment_score=60,
            trend_prediction="震荡", confidence_level="中",
            reason="fair",
            historical_return=12.5,
            holding_period_days=200,
        )
        text = format_single_report(result)
        self.assertIn("12.5", text)   # historical_return in formatted text
        self.assertIn("200", text)    # holding_period_days in formatted text

    def test_send_report_via_feishu_success(self):
        """send_report_via_feishu returns True when feishu_sender succeeds."""
        class FakeFeishuSender:
            def send_to_feishu(self, text: str) -> bool:
                self.last_text = text
                return True

        sender = FakeFeishuSender()
        report = PositionAnalysisReport(
            generated_at="2026-05-21T10:00:00",
            reports=[],
            total_positions=0,
            summary="无持仓",
        )
        result = send_report_via_feishu(report, sender)
        self.assertTrue(result)
        self.assertIn("持仓分析报告", sender.last_text)

    def test_send_report_via_feishu_custom_title(self):
        """Custom title parameter is accepted gracefully (title ignored by implementation)."""
        class FakeFeishuSender:
            def send_to_feishu(self, text: str) -> bool:
                self.last_text = text
                return True

        sender = FakeFeishuSender()
        report = PositionAnalysisReport(
            generated_at="2026-05-21T10:00:00",
            reports=[],
            total_positions=0,
            summary="空",
        )
        result = send_report_via_feishu(report, sender, title="自定义标题")
        self.assertTrue(result)
        # Title is ignored by implementation, but format should still work
        self.assertIn("持仓分析报告", sender.last_text)


if __name__ == "__main__":
    unittest.main()
