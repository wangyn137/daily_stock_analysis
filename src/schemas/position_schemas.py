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
