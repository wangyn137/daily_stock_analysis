# tests/test_logging_dedup.py
# -*- coding: utf-8 -*-
"""Tests for MessageDeduplicationFilter in src/logging_config.py."""
import importlib
import logging
import re
import sys
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _load_logging_config():
    """Reload src.logging_config to pick up changes between tests."""
    if "src.logging_config" in sys.modules:
        return importlib.reload(sys.modules["src.logging_config"])
    return importlib.import_module("src.logging_config")


class TestNormalizeMessage:
    def test_strips_http_url(self):
        cfg = _load_logging_config()
        text = "GET https://api.example.com/v1/foo?x=1 failed"
        assert cfg.normalize_message(text) == "GET <URL> failed"

    def test_strips_https_url(self):
        cfg = _load_logging_config()
        text = "POST https://api.example.com/api failed"
        assert cfg.normalize_message(text) == "POST <URL> failed"

    def test_strips_six_digit_stock_code(self):
        cfg = _load_logging_config()
        text = "[情报搜索] 最新消息: 搜索失败 - 请求频率过高 (513180)"
        assert cfg.normalize_message(text) == "[情报搜索] 最新消息: 搜索失败 - 请求频率过高 (<CODE>)"

    def test_strips_ipv4(self):
        cfg = _load_logging_config()
        text = "connection to 198.18.0.1:58866 failed"
        assert cfg.normalize_message(text) == "connection to <IP>:<PORT> failed"

    def test_strips_hex_address(self):
        cfg = _load_logging_config()
        text = "object at 0x393aba810 leaked"
        assert cfg.normalize_message(text) == "object at <ADDR> leaked"

    def test_strips_urllib3_retry_total(self):
        cfg = _load_logging_config()
        text = "Retrying (Retry(total=4), connect=None, read=None)"
        assert cfg.normalize_message(text) == "Retrying (Retry(total=<N>), connect=None, read=None)"

    def test_strips_iso_date(self):
        cfg = _load_logging_config()
        text = "snapshot taken at 2026-09-17 finished"
        assert cfg.normalize_message(text) == "snapshot taken at <DATE> finished"

    def test_empty_string_returns_empty(self):
        cfg = _load_logging_config()
        assert cfg.normalize_message("") == ""

    def test_no_match_returns_input(self):
        cfg = _load_logging_config()
        text = "ordinary log without variables"
        assert cfg.normalize_message(text) == text

    def test_combined_replacements(self):
        cfg = _load_logging_config()
        text = "[warn] https://api.example.com/q?code=513180 at 0x10abcf00 on 2026-09-17"
        result = cfg.normalize_message(text)
        assert "<URL>" in result
        assert "<CODE>" in result
        assert "<ADDR>" in result
        assert "<DATE>" in result

    def test_url_stops_before_six_digit_token(self):
        cfg = _load_logging_config()
        text = "[warn] https://api.example.com/q?code=513180 at 0x10abcf00 on 2026-09-17"
        result = cfg.normalize_message(text)
        assert "<URL>" in result
        assert "<CODE>" in result
        assert "<ADDR>" in result
        assert "<DATE>" in result
        assert "513180" not in result


class TestDeduplicationFilterState:
    def _make_record(self, msg: str, level: int = logging.WARNING, name: str = "src.test"):
        rec = logging.LogRecord(
            name=name,
            level=level,
            pathname=__file__,
            lineno=1,
            msg=msg,
            args=(),
            exc_info=None,
        )
        return rec

    def test_filter_first_record_passes(self):
        cfg = _load_logging_config()
        f = cfg.MessageDeduplicationFilter(window_seconds=60, flush_interval_seconds=60)
        rec = self._make_record("first message")
        assert f.filter(rec) is True

    def test_filter_dedupes_subsequent(self):
        cfg = _load_logging_config()
        f = cfg.MessageDeduplicationFilter(window_seconds=60, flush_interval_seconds=60)
        rec = self._make_record("repeat me")
        assert f.filter(rec) is True
        rec2 = self._make_record("repeat me")
        assert f.filter(rec2) is False
        rec3 = self._make_record("repeat me")
        assert f.filter(rec3) is False

    def test_filter_always_passes_error(self):
        cfg = _load_logging_config()
        f = cfg.MessageDeduplicationFilter(window_seconds=60, flush_interval_seconds=60)
        rec_err = self._make_record("oops", level=logging.ERROR)
        assert f.filter(rec_err) is True
        # A second identical ERROR must also pass (no dedup for ERROR+)
        rec_err2 = self._make_record("oops", level=logging.ERROR)
        assert f.filter(rec_err2) is True

    def test_filter_always_passes_critical(self):
        cfg = _load_logging_config()
        f = cfg.MessageDeduplicationFilter(window_seconds=60, flush_interval_seconds=60)
        rec = self._make_record("fatal", level=logging.CRITICAL)
        assert f.filter(rec) is True
        rec2 = self._make_record("fatal", level=logging.CRITICAL)
        assert f.filter(rec2) is True

    def test_filter_different_messages_dont_collide(self):
        cfg = _load_logging_config()
        f = cfg.MessageDeduplicationFilter(window_seconds=60, flush_interval_seconds=60)
        assert f.filter(self._make_record("message A")) is True
        assert f.filter(self._make_record("message B")) is True
        assert f.filter(self._make_record("message A")) is False
        assert f.filter(self._make_record("message B")) is False

    def test_filter_different_loggers_dont_collide(self):
        cfg = _load_logging_config()
        f = cfg.MessageDeduplicationFilter(window_seconds=60, flush_interval_seconds=60)
        assert f.filter(self._make_record("same text", name="src.a")) is True
        assert f.filter(self._make_record("same text", name="src.b")) is True

    def test_filter_normalizes_before_keying(self):
        cfg = _load_logging_config()
        f = cfg.MessageDeduplicationFilter(window_seconds=60, flush_interval_seconds=60)
        # Two messages differing only by stock code should collide after normalization.
        assert f.filter(self._make_record("search failed (513180)")) is True
        assert f.filter(self._make_record("search failed (513180)")) is False
        assert f.filter(self._make_record("search failed (513181)")) is False

    def test_filter_dedupes_info_subsequent(self):
        cfg = _load_logging_config()
        f = cfg.MessageDeduplicationFilter(window_seconds=60, flush_interval_seconds=60)
        # INFO records must also be deduped (not just WARNING)
        assert f.filter(self._make_record("info message", level=logging.INFO)) is True
        assert f.filter(self._make_record("info message", level=logging.INFO)) is False
        assert f.filter(self._make_record("info message", level=logging.INFO)) is False

    def test_filter_emit_summary_does_not_deadlock_with_filter_attached(self):
        """Regression: _emit_summary calls logger.info while holding _lock.
        If the summary logger's handlers carry this same filter, this would
        deadlock (re-entrant acquire of non-reentrant Lock). Verify it does not.
        """
        import threading
        cfg = _load_logging_config()
        f = cfg.MessageDeduplicationFilter(
            window_seconds=0.05, flush_interval_seconds=0.05
        )
        handler = logging.StreamHandler()
        handler.addFilter(f)
        root = logging.getLogger()
        saved_level = root.level
        saved_handlers = list(root.handlers)
        root.addHandler(handler)
        root.setLevel(logging.INFO)
        try:
            def worker():
                for _ in range(50):
                    f.filter(self._make_record("biz message"))
                    time.sleep(0.001)

            t = threading.Thread(target=worker)
            t.start()
            t.join(timeout=2.0)
            assert not t.is_alive(), "filter deadlocked when summary re-enters filter"
        finally:
            root.removeHandler(handler)
            root.setLevel(saved_level)


class TestDeduplicationFilterFlush:
    def _make_record(self, msg: str, level: int = logging.WARNING, name: str = "src.test"):
        return logging.LogRecord(
            name=name,
            level=level,
            pathname=__file__,
            lineno=1,
            msg=msg,
            args=(),
            exc_info=None,
        )

    def test_window_expires_then_flush_emits_summary(self, caplog):
        cfg = _load_logging_config()
        # Short window, disable force-flush paths so only window-expiry drain fires.
        f = cfg.MessageDeduplicationFilter(
            window_seconds=0.05,
            flush_interval_seconds=999.0,
            force_flush_after=100000,
            max_buckets=100000,
        )
        with caplog.at_level(logging.INFO, logger=cfg.SUMMARY_LOGGER_NAME):
            assert f.filter(self._make_record("expiring warn")) is True
            assert f.filter(self._make_record("expiring warn")) is False
            # Wait past window so the bucket is "expired" by age.
            time.sleep(0.1)
            # Insert another record — this triggers _maybe_flush_locked (which only updates timer),
            # but doesn't drain. Then call flush() to drain the now-expired bucket.
            f.filter(self._make_record("kicker"))
            f.flush()
        summary_records = [r for r in caplog.records if r.name == cfg.SUMMARY_LOGGER_NAME]
        assert any("[去重汇总]" in r.getMessage() for r in summary_records)
        # Specifically, the expiring warn bucket should be the one summarized.
        assert any("expiring warn" in r.getMessage() for r in summary_records)

    def test_force_flush_on_max_buckets(self, caplog):
        cfg = _load_logging_config()
        f = cfg.MessageDeduplicationFilter(
            window_seconds=10.0,
            flush_interval_seconds=10.0,
            max_buckets=5,
        )
        with caplog.at_level(logging.INFO, logger=cfg.SUMMARY_LOGGER_NAME):
            for i in range(7):
                rec = self._make_record(f"unique-{i}")
                f.filter(rec)
        summary_records = [r for r in caplog.records if r.name == cfg.SUMMARY_LOGGER_NAME]
        # First 5 buckets are filled; the 6th forces flush of all existing buckets,
        # which emits summaries for all 5 drained buckets. Then the 6th and 7th buckets
        # are added without summary (still under window).
        assert len(summary_records) >= 5

    def test_force_flush_after_silent_count(self, caplog):
        cfg = _load_logging_config()
        f = cfg.MessageDeduplicationFilter(
            window_seconds=10.0,
            flush_interval_seconds=10.0,
            force_flush_after=3,
        )
        with caplog.at_level(logging.INFO, logger=cfg.SUMMARY_LOGGER_NAME):
            assert f.filter(self._make_record("dup msg")) is True
            for _ in range(5):
                f.filter(self._make_record("dup msg"))
        summary_records = [r for r in caplog.records if r.name == cfg.SUMMARY_LOGGER_NAME]
        assert any("[去重汇总]" in r.getMessage() for r in summary_records)

    def test_thread_safe_no_deadlock(self):
        cfg = _load_logging_config()
        f = cfg.MessageDeduplicationFilter(
            window_seconds=10.0, flush_interval_seconds=10.0
        )
        import threading

        errors = []

        def worker(prefix: str) -> None:
            try:
                for i in range(200):
                    f.filter(self._make_record(f"{prefix}-{i % 5}"))
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(f"t{i}",)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        assert errors == []
        assert all(not t.is_alive() for t in threads)

    def test_fail_open_on_exception(self, monkeypatch):
        cfg = _load_logging_config()
        f = cfg.MessageDeduplicationFilter(window_seconds=10.0, flush_interval_seconds=10.0)

        # Force normalize_message to raise inside filter.
        def boom(_text):
            raise RuntimeError("simulated internal failure")

        monkeypatch.setattr(cfg, "normalize_message", boom)
        rec = self._make_record("any text")
        # Should still pass (fail-open).
        assert f.filter(rec) is True

    def test_flush_method_emits_pending_summaries(self, caplog):
        cfg = _load_logging_config()
        f = cfg.MessageDeduplicationFilter(window_seconds=999.0, flush_interval_seconds=999.0)
        with caplog.at_level(logging.INFO, logger=cfg.SUMMARY_LOGGER_NAME):
            f.filter(self._make_record("never expires"))
            f.filter(self._make_record("never expires"))
            f.flush()
        summary_records = [r for r in caplog.records if r.name == cfg.SUMMARY_LOGGER_NAME]
        assert any("[去重汇总]" in r.getMessage() for r in summary_records)


class TestSetupLoggingIntegration:
    def test_setup_logging_attaches_filter_by_default(self, monkeypatch, tmp_path):
        cfg = _load_logging_config()
        monkeypatch.chdir(tmp_path)
        cfg.setup_logging(log_prefix="dedup_test", log_dir=str(tmp_path))
        root = logging.getLogger()
        dedup_filters = [
            f for h in root.handlers for f in h.filters if isinstance(f, cfg.MessageDeduplicationFilter)
        ]
        assert len(dedup_filters) >= 2  # console + file_handler

    def test_setup_logging_disabled_skips_filter(self, monkeypatch, tmp_path):
        cfg = _load_logging_config()
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("LOG_DEDUP", "false")
        # Env var is read at setup_logging() call time, not import time.
        cfg.setup_logging(log_prefix="dedup_test_off", log_dir=str(tmp_path))
        root = logging.getLogger()
        dedup_filters = [
            f for h in root.handlers for f in h.filters if isinstance(f, cfg.MessageDeduplicationFilter)
        ]
        assert dedup_filters == []

    def test_setup_logging_debug_handler_unaffected(self, monkeypatch, tmp_path):
        cfg = _load_logging_config()
        monkeypatch.chdir(tmp_path)
        cfg.setup_logging(log_prefix="dedup_test_debug", log_dir=str(tmp_path))
        root = logging.getLogger()
        # The RotatingFileHandler with maxBytes=50MB is the debug handler.
        debug_handlers = [
            h
            for h in root.handlers
            if hasattr(h, "maxBytes") and h.maxBytes == 50 * 1024 * 1024
        ]
        assert len(debug_handlers) == 1
        assert not any(
            isinstance(f, cfg.MessageDeduplicationFilter) for f in debug_handlers[0].filters
        )

    def test_setup_logging_end_to_end_dedupes_repeats(self, monkeypatch, tmp_path, caplog):
        cfg = _load_logging_config()
        monkeypatch.chdir(tmp_path)
        cfg.setup_logging(
            log_prefix="dedup_e2e",
            log_dir=str(tmp_path),
            dedup_window_seconds=0.05,
            dedup_flush_interval_seconds=0.05,
        )
        test_logger = logging.getLogger("src.dedup_e2e_subject")
        with caplog.at_level(logging.INFO, logger=cfg.SUMMARY_LOGGER_NAME):
            for _ in range(20):
                test_logger.warning("identical warning %s", 513180)
        caplog_records = [r for r in caplog.records if r.name == cfg.SUMMARY_LOGGER_NAME]
        assert any("[去重汇总]" in r.getMessage() for r in caplog_records)