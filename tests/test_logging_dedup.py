# tests/test_logging_dedup.py
# -*- coding: utf-8 -*-
"""Tests for MessageDeduplicationFilter in src/logging_config.py."""
import importlib
import logging
import re
import sys
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