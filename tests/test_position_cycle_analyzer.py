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

    # Patch at the actual import source used by _run_agent_analysis
    @patch("src.agent.factory.build_agent_executor")
    def test_analyze_position_agent_failure(self, mock_build):
        mock_executor = MagicMock()
        # _run_agent_analysis calls executor.chat() and accesses .content
        mock_executor.chat.side_effect = Exception("API error")
        mock_build.return_value = mock_executor

        result = self.analyzer.analyze_position(
            Position(code="600519", name="贵州茅台", quantity=100, cost_price=1800.0),
            current_price=2000.0,
        )
        self.assertEqual(result.decision, "持有")
        self.assertEqual(result.confidence_level, "低")
        self.assertIn("API error", result.reason)

    @patch("src.agent.factory.build_agent_executor")
    def test_analyze_position_agent_success(self, mock_build):
        mock_executor = MagicMock()
        mock_executor.chat.return_value.content = (
            "决策: 加仓\n评分: 78\n趋势: 看多\n"
            "目标价: 2300\n止损价: 1800\n置信度: 高\n"
            "理由: 业绩增长确定\n"
            "操作清单: [- 逢低加仓]\n"
            "风险提示: [- 市场波动]\n"
            "催化剂: [- 新产品放量]"
        )
        mock_build.return_value = mock_executor

        result = self.analyzer.analyze_position(
            Position(code="600519", name="贵州茅台", quantity=100, cost_price=1800.0),
            current_price=2000.0,
        )
        self.assertEqual(result.decision, "加仓")
        self.assertEqual(result.sentiment_score, 78)
        self.assertEqual(result.target_price, 2300.0)
        self.assertEqual(result.stop_loss, 1800.0)
        self.assertEqual(result.confidence_level, "高")
        self.assertIn("业绩增长确定", result.reason)
        self.assertGreaterEqual(result.historical_return, 0)
        self.assertGreaterEqual(result.holding_period_days, 0)

    def test_analyze_portfolio_with_positions(self):
        """analyze_portfolio delegates to analyze_position per position."""
        positions = [
            Position(code="600519", name="贵州茅台", quantity=100, cost_price=1800.0),
        ]
        prices = {"600519": 2000.0}
        report = self.analyzer.analyze_portfolio(positions, prices)
        # Each position falls back since no agent available
        self.assertEqual(report.total_positions, 1)
        self.assertEqual(len(report.reports), 1)
        # Fallback decision
        self.assertEqual(report.reports[0].decision, "持有")


if __name__ == "__main__":
    unittest.main()
