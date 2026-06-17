# -*- coding: utf-8 -*-
"""Eastmoney news service (东财新闻 — 免费无 Key，替代 Tavily).

Data sources (all free, no auth):
  - 东财个股新闻 (search-api-web.eastmoney.com JSONP)
  - 东财全球资讯 (np-list.eastmoney.com 7×24 快讯)

Used as the primary news provider for A-share stock analysis,
with zero quota limits unlike Tavily.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Eastmoney throttling (shared with research_service)
# ---------------------------------------------------------------------------

_EM_SESSION: Optional[requests.Session] = None
_EM_LAST_CALL = 0.0
_EM_MIN_INTERVAL = 1.05  # 秒
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/117.0.0.0 Safari/537.36"
)


def _em_session() -> requests.Session:
    global _EM_SESSION
    if _EM_SESSION is None:
        _EM_SESSION = requests.Session()
        _EM_SESSION.headers.update({"User-Agent": UA})
    return _EM_SESSION


def _em_get(
    url: str,
    params: Optional[Dict] = None,
    headers: Optional[Dict] = None,
    timeout: int = 15,
    **kwargs,
) -> requests.Response:
    global _EM_LAST_CALL
    wait = _EM_MIN_INTERVAL - (time.time() - _EM_LAST_CALL)
    if wait > 0:
        time.sleep(wait)
    sess = _em_session()
    merged = dict(sess.headers)
    if headers:
        merged.update(headers)
    resp = sess.get(url, params=params, headers=merged, timeout=timeout, **kwargs)
    _EM_LAST_CALL = time.time()
    return resp


# ---------------------------------------------------------------------------
# Stock news (个股新闻)
# ---------------------------------------------------------------------------

def get_stock_news(
    code: str,
    page_size: int = 10,
) -> List[Dict[str, Any]]:
    """Fetch stock-specific news from eastmoney.

    Args:
        code: 6-digit stock code
        page_size: max results (default 10)

    Returns:
        List of {title, content, time, source, url}
    """
    cb = "jQuery_news"
    inner = json.dumps(
        {
            "uid": "",
            "keyword": code,
            "type": ["cmsArticleWebOld"],
            "client": "web",
            "clientType": "web",
            "clientVersion": "curr",
            "param": {
                "cmsArticleWebOld": {
                    "searchScope": "default",
                    "sort": "default",
                    "pageIndex": 1,
                    "pageSize": page_size,
                    "preTag": "",
                    "postTag": "",
                }
            },
        },
        separators=(",", ":"),
    )
    params = {"cb": cb, "param": inner}
    headers = {"Referer": "https://so.eastmoney.com/"}

    try:
        r = _em_get(
            "https://search-api-web.eastmoney.com/search/jsonp",
            params=params,
            headers=headers,
            timeout=15,
        )
        text = r.text
        # Parse JSONP: jQuery_news({...});
        json_str = text[text.index("(") + 1 : text.rindex(")")]
        data = json.loads(json_str)

        articles = data.get("result", {}).get("cmsArticleWebOld", []) or []
        # Handle both list and {list:[...]} formats
        if isinstance(articles, dict):
            articles = articles.get("list", []) or []

        rows: List[Dict[str, Any]] = []
        for a in articles:
            if not isinstance(a, dict):
                continue
            rows.append(
                {
                    "title": re.sub(r"<[^>]+>", "", str(a.get("title", ""))),
                    "content": re.sub(r"<[^>]+>", "", str(a.get("content", "")))[:200],
                    "time": str(a.get("date", "")),
                    "source": str(a.get("mediaName", "")),
                    "url": str(a.get("url", "")),
                }
            )
        return rows
    except Exception as exc:
        logger.warning("东财个股新闻拉取失败 (%s): %s", code, exc)
        return []


# ---------------------------------------------------------------------------
# Global financial news (全球资讯)
# ---------------------------------------------------------------------------

def get_global_news(page_size: int = 10) -> List[Dict[str, Any]]:
    """Fetch 7×24 global financial news from eastmoney.

    Currently disabled — np-list endpoint migrated to new auth.
    Stock-specific news from get_stock_news() is sufficient.
    """
    return []


# ---------------------------------------------------------------------------
# LLM context injection
# ---------------------------------------------------------------------------

def get_news_context(code: str, stock_name: str = "") -> str:
    """Build a prompt section with stock-specific and global news.

    Args:
        code: 6-digit stock code
        stock_name: optional display name

    Returns:
        Markdown prompt string for LLM analysis context.
    """
    lines = [f"**📰 新闻情报（东财财经）**"]

    # Stock-specific news
    stock_news = get_stock_news(code, page_size=5)
    if stock_news:
        lines.append(f"\n**{stock_name or code}** 相关新闻：")
        for n in stock_news[:4]:
            t = n.get("time", "")[:10]
            s = n.get("source", "")
            title = n.get("title", "")[:60]
            lines.append(f"  • {t} [{s}] {title}")
    else:
        lines.append(f"\n（无{code}个股新闻）")

    # Global macro news
    global_news = get_global_news(page_size=8)
    if global_news:
        lines.append(f"\n**市场宏观快讯**：")
        for n in global_news[:5]:
            t = n.get("time", "")[:16]
            title = n.get("title", "")[:80]
            lines.append(f"  • {t} {title}")

    count = len(stock_news) + len(global_news)
    lines.append(f"\n（共 {count} 条，数据源：东方财富，免费无配额限制）")

    return "\n".join(lines)
