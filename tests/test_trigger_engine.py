"""Tests for TriggerEngine."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from src.config import Config
from src.core.trigger_engine import TriggerEngine, TriggerEvent


class TestTriggerEngine(unittest.TestCase):

    def setUp(self):
        # Use default Config() to avoid loading from .env
        cfg = Config()
        self.engine = TriggerEngine(config=cfg)
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
        # Config default: Monday 9:00
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

    def test_zero_cost_price_returns_none(self):
        event = self.engine.check_threshold_trigger("600519", current_price=22.0, cost_price=0.0)
        self.assertIsNone(event)

    def test_weekly_trigger_wrong_minute(self):
        monday_9_1 = datetime(2026, 5, 25, 9, 1)
        event = self.engine.check_weekly_trigger(now=monday_9_1)
        self.assertIsNone(event)


if __name__ == "__main__":
    unittest.main()
