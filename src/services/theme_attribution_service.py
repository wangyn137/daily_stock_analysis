# -*- coding: utf-8 -*-
"""Theme attribution & hot-stock signal service (题材归因 / 强势股).

Data source: 同花顺 event api (zx.10jqka.com.cn), zero auth, ~73ms.
Returns daily strong-performing stocks with editor-curated reason tags
explaining *why* each stock is moving.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Set

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_HOT_API = "http://zx.10jqka.com.cn/event/api/getharden/"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "Chrome/117.0.0.0 Safari/537.36"
    ),
}


class ThemeAttributionService:
    """Fetch and analyze daily strong-stock theme data from 同花顺."""

    def __init__(self):
        self._cache: Dict[str, pd.DataFrame] = {}

    # ------------------------------------------------------------------
    # Hot stocks
    # ------------------------------------------------------------------

    def get_hot_stocks(
        self,
        target_date: Optional[str] = None,
    ) -> pd.DataFrame:
        """Return today's strong-performing stocks with reason tags.

        Args:
            target_date: YYYY-MM-DD, defaults to today.

        Returns:
            DataFrame with columns: 代码, 名称, 收盘价, 涨幅%, 换手率%,
            成交额, 成交额亿, 题材归因, 市场, 大单净量
        """
        if target_date is None:
            target_date = date.today().strftime("%Y-%m-%d")

        cache_key = target_date
        if cache_key in self._cache:
            return self._cache[cache_key]

        url = (
            f"{_HOT_API}date/{target_date}/orderby/date/orderway/desc/charset/GBK/"
        )
        try:
            r = requests.get(url, headers=_HEADERS, timeout=10)
            data = r.json()
            if data.get("errocode", 0) != 0:
                logger.warning(
                    "同花顺热点 API 错误: %s", data.get("errormsg", "")
                )
                return pd.DataFrame()

            rows = data.get("data") or []
            if not rows:
                return pd.DataFrame()

            df = pd.DataFrame(rows)
            rename = {
                "name": "名称",
                "code": "代码",
                "reason": "题材归因",
                "close": "收盘价",
                "zhangdie": "涨跌额",
                "zhangfu": "涨幅%",
                "huanshou": "换手率%",
                "chengjiaoe": "成交额",
                "chengjiaoliang": "成交量",
                "ddejingliang": "大单净量",
                "market": "市场",
            }
            df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})

            # 成交额 → 亿
            if "成交额" in df.columns:
                df["成交额亿"] = pd.to_numeric(df["成交额"], errors="coerce") / 1e8
            if "涨幅%" in df.columns:
                df["涨幅%"] = pd.to_numeric(df["涨幅%"], errors="coerce")

            self._cache[cache_key] = df
            return df

        except Exception as exc:
            logger.warning("同花顺热点数据拉取失败: %s", exc)
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # Match portfolio stocks against hot themes
    # ------------------------------------------------------------------

    def match_portfolio(
        self,
        stock_codes: List[str],
        target_date: Optional[str] = None,
    ) -> Dict[str, Optional[Dict[str, Any]]]:
        """Check which portfolio stocks appear in today's hot list.

        Returns:
            Dict[code, Optional[Dict]] — None if not in hot list, else
            {名称, 涨幅%, 题材归因, 大单净量, 换手率%}
        """
        df = self.get_hot_stocks(target_date=target_date)
        result: Dict[str, Optional[Dict[str, Any]]] = {}
        code_set = set(str(c).strip() for c in stock_codes)

        if df.empty:
            return {c: None for c in code_set}

        for _, row in df.iterrows():
            code = str(row.get("代码", "")).strip()
            if code in code_set:
                result[code] = {
                    "名称": str(row.get("名称", "")),
                    "涨幅%": _safe_float(row.get("涨幅%")),
                    "题材归因": str(row.get("题材归因", "")),
                    "大单净量": _safe_float(row.get("大单净量")),
                    "换手率%": _safe_float(row.get("换手率%")),
                    "收盘价": _safe_float(row.get("收盘价")),
                }

        for c in code_set:
            if c not in result:
                result[c] = None

        return result

    # ------------------------------------------------------------------
    # Theme ranking
    # ------------------------------------------------------------------

    def get_top_themes(self, target_date: Optional[str] = None, top_n: int = 8) -> List[Dict[str, Any]]:
        """Aggregate and rank themes by occurrence frequency and avg gain.

        Each "reason" is a "+"-separated tag string like "算力租赁+AI政务+Token工厂".
        """
        df = self.get_hot_stocks(target_date=target_date)
        if df.empty:
            return []

        theme_stats: Dict[str, Dict[str, Any]] = {}
        for _, row in df.iterrows():
            reason = str(row.get("题材归因", ""))
            if not reason:
                continue
            tags = [t.strip() for t in reason.split("+") if t.strip()]
            gain = _safe_float(row.get("涨幅%")) or 0
            for tag in tags:
                if tag not in theme_stats:
                    theme_stats[tag] = {"tag": tag, "count": 0, "total_gain": 0.0}
                theme_stats[tag]["count"] += 1
                theme_stats[tag]["total_gain"] += gain

        themes = []
        for info in theme_stats.values():
            info["avg_gain"] = round(info["total_gain"] / info["count"], 2)
            themes.append(info)

        themes.sort(key=lambda t: -t["count"])
        return themes[:top_n]

    # ------------------------------------------------------------------
    # LLM context injection
    # ------------------------------------------------------------------

    def get_context_summary(
        self,
        portfolio_codes: Optional[List[str]] = None,
    ) -> str:
        """Return a concise prompt string for LLM analysis context.

        Includes:
        - Whether any portfolio stock is in today's hot list
        - Top themes driving the market today
        """
        lines = ["**题材与强势股（同花顺热点）**"]

        # Top themes
        top_themes = self.get_top_themes(top_n=8)
        if top_themes:
            theme_parts = []
            for t in top_themes[:5]:
                theme_parts.append(
                    f"{t['tag']}({t['count']}只,均+{t['avg_gain']}%)"
                )
            lines.append(f"今日热门题材: {' | '.join(theme_parts)}")

            # Also list remaining
            if len(top_themes) > 5:
                remaining = [t["tag"] for t in top_themes[5:]]
                lines.append(f"其他活跃题材: {', '.join(remaining)}")
        else:
            lines.append("(今日热点数据暂不可用)")

        # Portfolio matching
        if portfolio_codes:
            matches = self.match_portfolio(portfolio_codes)
            matched = {c: m for c, m in matches.items() if m}
            if matched:
                lines.append("你的持仓中，以下标的在今日强势股名单中：")
                for code, info in sorted(matched.items()):
                    lines.append(
                        f"  • {code} {info.get('名称','')} "
                        f"涨幅 {info.get('涨幅%','?')}% "
                        f"题材: {info.get('题材归因','??')}"
                    )
            else:
                lines.append("你的持仓今日无标的出现在强势股名单中。")

        return "\n".join(lines)


def _safe_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None
