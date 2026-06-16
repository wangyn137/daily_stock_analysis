# -*- coding: utf-8 -*-
"""North-bound capital flow service (沪深股通/北向资金).

Data source: 同花顺 hsgtApi (data.hexin.cn), zero auth required.
Provides real-time minute-level net buy amounts for Shanghai and Shenzhen
Connect, plus daily snapshots cached locally for historical context.
"""

from __future__ import annotations

import csv
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

HSGT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "Chrome/117.0.0.0 Safari/537.36"
    ),
    "Host": "data.hexin.cn",
    "Referer": "https://data.hexin.cn/",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class NorthboundService:
    """Fetch and cache north-bound (沪深股通) capital flow data."""

    def __init__(self, cache_dir: Optional[str] = None):
        self._cache_dir = Path(cache_dir or "data/northbound")
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def get_realtime_flow(self) -> Dict[str, Any]:
        """Return today's realtime minute-level north-bound flow.

        Returns:
            Dict with keys:
                - times: list[str]  time labels for the trading day
                - hgt_yi: list[float] cumulative 沪股通 net buy (亿元)
                - sgt_yi: list[float] cumulative 深股通 net buy (亿元)
                - latest: latest cumulative values
        """
        try:
            r = requests.get(
                "https://data.hexin.cn/market/hsgtApi/method/dayChart/",
                headers=HSGT_HEADERS,
                timeout=10,
            )
            d = r.json()
            times = d.get("time", [])
            hgt = d.get("hgt", [])
            sgt = d.get("sgt", [])

            n = len(times)
            hgt_padded = list(hgt)[:n] + [None] * max(0, n - len(hgt))
            sgt_padded = list(sgt)[:n] + [None] * max(0, n - len(sgt))

            latest_hgt = _last_valid(hgt_padded)
            latest_sgt = _last_valid(sgt_padded)

            # Save today's snapshot for history
            today = date.today().isoformat()
            if latest_hgt is not None and latest_sgt is not None:
                self._save_snapshot(today, latest_hgt, latest_sgt)

            return {
                "date": today,
                "times": times,
                "hgt_yi": hgt_padded,
                "sgt_yi": sgt_padded,
                "latest_hgt_yi": latest_hgt,
                "latest_sgt_yi": latest_sgt,
                "total_net_yi": (
                    round(latest_hgt + latest_sgt, 2)
                    if latest_hgt is not None and latest_sgt is not None
                    else None
                ),
            }
        except Exception as exc:
            logger.warning(f"北向资金实时拉取失败: {exc}")
            return {"date": date.today().isoformat(), "error": str(exc)}

    def get_recent_history(self, days: int = 20) -> List[Dict[str, Any]]:
        """Return cached daily snapshots for the last N trading days."""
        csv_path = self._cache_path()
        if not csv_path.exists():
            return []
        rows: List[Dict[str, Any]] = []
        try:
            with open(csv_path, "r", newline="") as fp:
                reader = csv.DictReader(fp)
                for row in reader:
                    rows.append({
                        "date": row.get("date", ""),
                        "hgt_yi": float(row.get("hgt_yi", 0)),
                        "sgt_yi": float(row.get("sgt_yi", 0)),
                        "total_yi": row.get("total_yi", ""),
                    })
        except Exception:
            return []
        return rows[-days:]

    def get_context_summary(self, days: int = 5) -> str:
        """Return a concise 中文 prompt string for LLM context injection.

        Summarizes recent north-bound trend and today's flow.
        """
        realtime = self.get_realtime_flow()
        history = self.get_recent_history(days=days)

        lines = ["**北向资金（沪深股通）**"]

        # Today
        if realtime.get("total_net_yi") is not None:
            direction = "净流入" if realtime["total_net_yi"] > 0 else "净流出"
            lines.append(
                f"今日: {direction} {abs(realtime['total_net_yi']):.2f} 亿元 "
                f"(沪 {realtime.get('latest_hgt_yi',0) or 0:.2f} / "
                f"深 {realtime.get('latest_sgt_yi',0) or 0:.2f})"
            )
        elif "error" in realtime:
            lines.append(f"(今日数据暂不可用: {realtime['error']})")
        else:
            lines.append("(今日无数据)")

        # Recent trend
        if history:
            totals = [
                float(r.get("total_yi", 0) or 0)
                for r in history[-days:]
                if r.get("total_yi")
            ]
            if totals:
                cum = sum(totals)
                direction = "累计流入" if cum > 0 else "累计流出"
                pos = sum(1 for t in totals if t > 0)
                lines.append(
                    f"近{days}日: {direction} {abs(cum):.1f} 亿元, "
                    f"净流入 {pos}/{len(totals)} 日, "
                    f"趋势: {'持续流入 ↑' if cum > 0 and pos >= len(totals)*0.6 else '偏流出 ↓' if cum < 0 else '震荡'} "
                )

        # Signal interpretation
        if realtime.get("total_net_yi") is not None and history:
            total = realtime["total_net_yi"]
            recent_totals = [
                float(r.get("total_yi", 0) or 0) for r in history[-3:]
                if r.get("total_yi")
            ]
            avg_recent = sum(recent_totals) / len(recent_totals) if recent_totals else 0
            if total > 30 and avg_recent > 10:
                lines.append("→ 信号: 北向大幅流入，中期偏多")
            elif total < -30 and avg_recent < -10:
                lines.append("→ 信号: 北向大幅流出，中期谨慎")
            elif total > 0 and avg_recent > 0:
                lines.append("→ 信号: 北向温和流入，中性偏多")
            elif total < 0 and avg_recent < 0:
                lines.append("→ 信号: 北向温和流出，中性偏空")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    def _cache_path(self) -> Path:
        return self._cache_dir / "northbound_daily.csv"

    def _save_snapshot(self, date_str: str, hgt: float, sgt: float):
        csv_path = self._cache_path()
        existing_dates = set()
        if csv_path.exists():
            try:
                with open(csv_path, "r", newline="") as fp:
                    reader = csv.DictReader(fp)
                    existing_dates = {row.get("date", "") for row in reader}
            except Exception:
                pass

        if date_str in existing_dates:
            return  # Already saved today

        total = round(hgt + sgt, 2)
        need_header = not csv_path.exists()
        try:
            with open(csv_path, "a", newline="") as fp:
                writer = csv.writer(fp)
                if need_header:
                    writer.writerow(["date", "hgt_yi", "sgt_yi", "total_yi"])
                writer.writerow([date_str, round(hgt, 2), round(sgt, 2), total])
        except IOError as exc:
            logger.warning(f"北向资金缓存写入失败: {exc}")


def _last_valid(values: list) -> Optional[float]:
    for v in reversed(values):
        if v is not None:
            return float(v)
    return None
