# src/core/trigger_engine.py
"""Trigger engine for portfolio position cycle analysis.

Supports three trigger modes:
1. Weekly scheduled — generate weekly portfolio report
2. Threshold — single stock price change exceeds threshold % triggers recommendation
3. Event — earnings release / technical breakout / policy change triggers event interpretation
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional

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
        day_name = self.config.portfolio_weekly_day.lower()
        target_dow = day_map.get(day_name)
        if target_dow is None:
            logger.warning("Unrecognized day '%s', defaulting to Monday", day_name)
            target_dow = 0
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

        if cost_price == 0:
            logger.warning("cost_price is zero for %s, skipping threshold check", code)
            return None

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
        now: Optional[datetime] = None,
    ) -> Optional[TriggerEvent]:
        """Check and fire an event-based trigger."""
        if not self._enabled:
            return None
        supported_events = {"earnings_release", "technical_breakout", "policy_change"}
        if event_type not in supported_events:
            logger.warning("Unsupported event type: %s", event_type)
            return None
        now = now or datetime.now()
        return TriggerEvent(
            trigger_type="event",
            stock_codes=[code],
            event_detail=f"{event_type}: {detail}",
            triggered_at=now.isoformat(),
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
