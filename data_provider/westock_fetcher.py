# -*- coding: utf-8 -*-
"""WeStockFetcher - Priority 0 data source via Tencent WeStock official API.

Data source: Tencent self-select stock data interface (腾讯自选股)
Package:    westock-data-clawhub@1.0.4 (npm, maintainer: Tencent WeStock team)
CLI:        npx -y westock-data-clawhub@1.0.4 <command> <args>
"""

from __future__ import annotations

import logging
import re
import subprocess
import time
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from .base import (
    BaseFetcher,
    DataFetchError,
    STANDARD_COLUMNS,
    is_bse_code,
    normalize_stock_code,
)
from .realtime_types import (
    UnifiedRealtimeQuote,
    RealtimeSource,
    safe_float,
    safe_int,
)

logger = logging.getLogger(__name__)

_WESTOCK_PACKAGE = "westock-data-clawhub@1.0.4"
_CMD_TIMEOUT = 20
_BULK_CACHE_TTL = 15.0  # seconds


# ---------------------------------------------------------------------------
# Stock code conversion
# ---------------------------------------------------------------------------

def _to_westock_code(stock_code: str) -> str:
    """Convert DSA stock code to WeStock format.

    600519 → sh600519 (沪市)
    000001 → sz000001 (深市)
    920748 → bj920748 (北交所)
    HK00700 → hk00700 (港股)
    AAPL   → usAAPL  (美股)
    """
    code = normalize_stock_code(str(stock_code or "").strip())
    if not code:
        raise DataFetchError(f"empty stock code: {stock_code!r}")

    if code.startswith("HK"):
        return f"hk{code[2:].zfill(5)}"
    if not code.isdigit():
        return f"us{code}"
    if is_bse_code(code):
        return f"bj{code}"
    if code.startswith("6"):
        return f"sh{code}"
    return f"sz{code}"


# ---------------------------------------------------------------------------
# subprocess helper
# ---------------------------------------------------------------------------

def _run_westock(*args: str) -> str:
    """Run a WeStock CLI command and return stdout text.

    Raises DataFetchError on timeout / non-zero exit / missing npx.
    """
    cmd = ["npx", "-y", _WESTOCK_PACKAGE] + list(args)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_CMD_TIMEOUT,
        )
    except FileNotFoundError:
        raise DataFetchError("npx not found — install Node.js >= v18")
    except subprocess.TimeoutExpired:
        raise DataFetchError(
            f"WeStock timeout ({_CMD_TIMEOUT}s): {' '.join(args[:3])}"
        )

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise DataFetchError(
            f"WeStock exit {result.returncode}: {stderr[:256]}"
        )
    return result.stdout


# ---------------------------------------------------------------------------
# Markdown table parser
# ---------------------------------------------------------------------------

def _parse_md_table(output: str) -> Tuple[List[str], List[List[str]]]:
    """Parse a markdown table from WeStock CLI output into (headers, rows).

    Returns ([], []) when no table is found.
    """
    lines = output.strip().split("\n")
    headers: List[str] = []
    rows: List[List[str]] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or not stripped.startswith("|"):
            continue
        # Skip separator lines
        if re.fullmatch(r"\|[\s\-:|]+\|", stripped):
            continue

        cells = [c.strip() for c in stripped.split("|")[1:-1]]
        if not cells:
            continue
        if not headers:
            headers = cells
        else:
            rows.append(cells)

    return headers, rows


# ---------------------------------------------------------------------------
# WeStockFetcher
# ---------------------------------------------------------------------------

class WeStockFetcher(BaseFetcher):
    """Tencent WeStock official data source.

    Provides daily K-line, technical indicators, capital flow, and
    financial statements for A-shares / HK / US stocks.
    """

    name = "WeStockFetcher"
    priority = 0
    allow_empty_daily_data = True

    # Per-process bulk caches to avoid repeated CLI calls for the
    # same stock within a short window.
    _bulk_price_store: Dict[str, Dict] = {}
    _bulk_price_ts: float = 0.0

    # ------------------------------------------------------------------
    # BaseFetcher contract: daily K-line
    # ------------------------------------------------------------------

    def _fetch_raw_data(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        wcode = _to_westock_code(stock_code)
        lookback = _estimate_lookback_days(start_date, end_date)
        output = _run_westock("kline", wcode, "--period", "day", "--limit", str(lookback))
        headers, rows = _parse_md_table(output)

        if not rows:
            logger.info("WeStock empty kline for %s (%s)", stock_code, wcode)
            return _empty_frame()

        cols = [h.lower() for h in headers]
        df = pd.DataFrame(rows, columns=cols)

        # Rename WeStock columns → standard names
        col_map = {}
        for c in df.columns:
            if c == "last" or c == "close":
                col_map[c] = "close"
            elif c == "exchange":
                col_map[c] = "pct_chg"
            else:
                col_map[c] = c
        df = df.rename(columns=col_map)

        # Ensure standard columns exist
        for std_col in STANDARD_COLUMNS:
            if std_col not in df.columns:
                df[std_col] = 0.0

        # Volume in WeStock kline is in 手 (lots=100 shares)
        if "volume" in df.columns:
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce") * 100

        # Convert numeric columns
        for col in ("open", "high", "low", "close", "amount", "pct_chg"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # Filter to requested date range
        if "date" in df.columns:
            df = df[(df["date"] >= start_date) & (df["date"] <= end_date)]

        if df.empty:
            logger.info("WeStock kline empty after date filter for %s", stock_code)
            return _empty_frame()

        return df[STANDARD_COLUMNS]

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        normalized = df.copy()
        for col in ("open", "high", "low", "close", "volume", "amount", "pct_chg"):
            if col in normalized.columns:
                normalized[col] = pd.to_numeric(normalized[col], errors="coerce")
        return normalized

    # ------------------------------------------------------------------
    # Realtime quote (derived from latest kline + technical)
    # ------------------------------------------------------------------

    def get_realtime_quote(
        self,
        stock_code: str,
        *,
        source: Optional[str] = None,
    ) -> Optional[UnifiedRealtimeQuote]:
        """Return a unified realtime quote using latest kline + technical data.

        Falls back gracefully to None on errors.
        """
        code = normalize_stock_code(str(stock_code or ""))
        try:
            wcode = _to_westock_code(code)
        except DataFetchError:
            return None

        # Fresh bulk price? (cache-bust via TTL)
        now = time.monotonic()
        cached = self._bulk_price_store.get(code)
        if cached and (now - self._bulk_price_ts) < _BULK_CACHE_TTL:
            return cached.get("_quote")

        # Fetch latest 2 kline bars + all technicals
        try:
            t_output = _run_westock("technical", wcode, "--group", "all")
            t_headers, t_rows = _parse_md_table(t_output)
        except DataFetchError:
            t_rows = []

        tech = {}
        if t_rows:
            t_cols = [h.lower() for h in t_headers]
            t_dict = dict(zip(t_cols, t_rows[0]))
            tech = t_dict

        kline_output = _run_westock("kline", wcode, "--period", "day", "--limit", "3")
        k_headers, k_rows = _parse_md_table(kline_output)

        if not k_rows:
            return None

        k_cols = [h.lower() for h in k_headers]
        latest = dict(zip(k_cols, k_rows[0]))
        prev = dict(zip(k_cols, k_rows[1])) if len(k_rows) > 1 else {}

        close = safe_float(latest.get("last") or latest.get("close"))
        prev_close = safe_float(prev.get("last") or prev.get("close"))
        open_p = safe_float(latest.get("open"))
        high = safe_float(latest.get("high"))
        low = safe_float(latest.get("low"))
        volume_lots = safe_float(latest.get("volume"))  # in 手
        amount = safe_float(latest.get("amount"))

        change_pct = 0.0
        if prev_close and prev_close > 0:
            change_pct = round((close - prev_close) / prev_close * 100, 2)

        # Volume in shares (WeStock kline volume is in 手=100 shares)
        volume = volume_lots * 100

        quote = UnifiedRealtimeQuote(
            code=code,
            name=tech.get("name") or "",
            source=RealtimeSource.WESTOCK,
            price=close,
            change_pct=change_pct,
            change_amount=round(close - prev_close, 2) if close and prev_close else None,
            volume=int(volume) if volume else None,
            amount=amount,
            turnover_rate=safe_float(_nested_get(tech, "other.turnover_rate")),
            volume_ratio=safe_float(_nested_get(tech, "other.volume_ratio")),
            open_price=open_p,
            high=high,
            low=low,
            pre_close=prev_close,
            pe_ratio=safe_float(_nested_get(tech, "other.pe_ttm")),
            pb_ratio=safe_float(_nested_get(tech, "other.pb")),
            total_mv=safe_float(_nested_get(tech, "other.total_mv")),
            circ_mv=safe_float(_nested_get(tech, "other.circ_mv")),
            fetched_at=datetime.now().isoformat(),
            provider_timestamp=latest.get("date") or str(date.today()),
        )

        # Brief cache
        self._bulk_price_store[code] = {"_quote": quote}
        self._bulk_price_ts = now

        return quote

    # ------------------------------------------------------------------
    # Extra: capital flow (A-shares)
    # ------------------------------------------------------------------

    def get_capital_flow(self, stock_code: str) -> Optional[Dict[str, Any]]:
        """Get A-share capital flow data for one stock."""
        try:
            wcode = _to_westock_code(stock_code)
            output = _run_westock("asfund", wcode)
            _, rows = _parse_md_table(output)
            if not rows:
                return None

            # Parse the wide flat table: first row only
            headers, _ = _parse_md_table(output)
            record = dict(zip([h.lower() for h in headers], rows[0]))
            return {
                "main_net_flow": safe_float(record.get("mainnetflow")),
                "main_inflow": safe_float(record.get("maininflow")),
                "main_outflow": safe_float(record.get("mainoutflow")),
                "jumbo_net_flow": safe_float(record.get("jumbonetflow")),
                "retail_inflow": safe_float(record.get("retailinflow")),
                "retail_outflow": safe_float(record.get("retailoutflow")),
                "main_net_flow_5d": safe_float(record.get("mainnetflow5d")),
                "main_net_flow_10d": safe_float(record.get("mainnetflow10d")),
                "main_net_flow_20d": safe_float(record.get("mainnetflow20d")),
                "block_net_flow": safe_float(record.get("blocknetflow")),
                "main_inflow_rank": safe_int(record.get("maininflowrank")),
                "date": record.get("enddate") or str(date.today()),
            }
        except DataFetchError:
            return None

    # ------------------------------------------------------------------
    # Extra: market stats (board data for market breadth)
    # ------------------------------------------------------------------

    def get_market_stats(self) -> Optional[Dict[str, Any]]:
        """Get A-share market breadth from WeStock board data."""
        try:
            output = _run_westock("board")
            headers, rows = _parse_md_table(output)
            if not rows:
                return None
            return {"_board_raw": dict(zip([h.lower() for h in headers], rows[0]))}
        except DataFetchError:
            return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=STANDARD_COLUMNS)


def _estimate_lookback_days(start_date: str, end_date: str) -> int:
    try:
        s = datetime.strptime(start_date, "%Y-%m-%d")
        e = datetime.strptime(end_date, "%Y-%m-%d")
        days = max(1, (e - s).days + 1)
    except ValueError:
        days = 90
    # trading days ~ calendar days * 5/7 + holiday buffer
    return max(60, min(2000, int(days * 1.8) + 20))


def _nested_get(d: dict, dotted_key: str) -> Any:
    """Get a nested dict value via dot-separated keys. E.g. ma.ma_5."""
    keys = dotted_key.split(".")
    val: Any = d
    for k in keys:
        if isinstance(val, dict):
            val = val.get(k)
            if val is None:
                return None
        else:
            return None
    return val



