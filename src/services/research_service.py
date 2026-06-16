# -*- coding: utf-8 -*-
"""Research report & consensus EPS service (研报 + 一致预期 EPS).

Data sources:
  - 东财 reportapi (reportapi.eastmoney.com) → 研报列表 + 评级 + 三年EPS预测
  - 同花顺 basic.10jqka.com.cn → 机构一致预期EPS（均值/最小/最大）
  - 估值计算: 前向PE / PEG / PE消化时间
"""

from __future__ import annotations

import logging
import math
import re
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
from io import StringIO

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Eastmoney throttling (防封)
# ---------------------------------------------------------------------------

_EM_SESSION: Optional[requests.Session] = None
_EM_LAST_CALL = 0.0
_EM_MIN_INTERVAL = 1.1  # 秒；东财报告接口限流


def _em_session() -> requests.Session:
    global _EM_SESSION
    if _EM_SESSION is None:
        _EM_SESSION = requests.Session()
        _EM_SESSION.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/117.0.0.0 Safari/537.36"
            ),
        })
    return _EM_SESSION


def _em_get(
    url: str,
    params: Optional[Dict] = None,
    headers: Optional[Dict] = None,
    timeout: int = 30,
    **kwargs,
) -> requests.Response:
    """Rate-limited eastmoney HTTP GET."""
    global _EM_LAST_CALL
    wait = _EM_MIN_INTERVAL - (time.time() - _EM_LAST_CALL)
    if wait > 0:
        time.sleep(wait)
    sess = _em_session()
    merged_headers = dict(sess.headers)
    if headers:
        merged_headers.update(headers)
    resp = sess.get(url, params=params, headers=merged_headers, timeout=timeout, **kwargs)
    _EM_LAST_CALL = time.time()
    return resp


# ---------------------------------------------------------------------------
# Research Reports (东财 reportapi)
# ---------------------------------------------------------------------------

REPORT_API = "https://reportapi.eastmoney.com/report/list"
PDF_TPL = "https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf"


def get_eastmoney_reports(code: str, max_pages: int = 3) -> List[Dict[str, Any]]:
    """Fetch research reports for a stock from eastmoney.

    Args:
        code: 6-digit A-share code (e.g. '601020')
        max_pages: max pages to fetch (100 per page)

    Returns:
        List of report dicts, key fields:
        title, publishDate, orgSName, emRatingName,
        predictThisYearEps, predictNextYearEps, predictNextTwoYearEps,
        indvInduName, infoCode
    """
    all_records: List[Dict[str, Any]] = []
    seen = set()
    for page in range(1, max_pages + 1):
        params = {
            "industryCode": "*", "pageSize": "50",
            "industry": "*", "rating": "*", "ratingChange": "*",
            "beginTime": (date.today() - timedelta(days=365)).isoformat(),
            "endTime": (date.today() + timedelta(days=30)).isoformat(),
            "pageNo": str(page), "fields": "", "qType": "0",
            "orgCode": "", "code": code, "rcode": "",
            "p": str(page), "pageNum": str(page),
            "pageNumber": str(page),
        }
        try:
            r = _em_get(
                REPORT_API, params=params,
                headers={"Referer": "https://data.eastmoney.com/"},
                timeout=30,
            )
            data = r.json()
            rows = data.get("data") or []
            if not rows:
                break
            for row in rows:
                key = row.get("infoCode") or row.get("title")
                if key and key not in seen:
                    seen.add(key)
                    all_records.append(row)
            total_pages = data.get("TotalPage", 1) or 1
            if page >= total_pages:
                break
        except Exception as exc:
            logger.warning("东财研报拉取失败 (page=%d): %s", page, exc)
            break
    return all_records


def _eps_or_none(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        v = float(val)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def summarize_reports(reports: List[Dict], code: str, stock_name: str = "") -> Dict[str, Any]:
    """Extract structured insight from report list.

    Returns dict with:
      - total_count, recent_count (last 90 days)
      - consensus_eps_this_year, consensus_eps_next_year, consensus_eps_next_two_year
      - ratings: {买入: N, 增持: N, ...}
      - top_orgs: list[str] most active research houses
      - latest_reports: list[dict] latest 5 records
    """
    now = date.today()
    cutoff = now - timedelta(days=90)

    recent = [r for r in reports if (r.get("publishDate") or "")[:10] >= cutoff.isoformat()]

    ratings: Dict[str, int] = {}
    eps_this: List[float] = []
    eps_next: List[float] = []
    eps_next_two: List[float] = []
    orgs: Dict[str, int] = {}

    for r in reports:
        rating = (r.get("emRatingName") or "").strip()
        if rating:
            ratings[rating] = ratings.get(rating, 0) + 1
        org = (r.get("orgSName") or "").strip()
        if org:
            orgs[org] = orgs.get(org, 0) + 1

    for r in recent:
        e1 = _eps_or_none(r.get("predictThisYearEps"))
        e2 = _eps_or_none(r.get("predictNextYearEps"))
        e3 = _eps_or_none(r.get("predictNextTwoYearEps"))
        if e1:
            eps_this.append(e1)
        if e2:
            eps_next.append(e2)
        if e3:
            eps_next_two.append(e3)

    med = lambda xs: round(sorted(xs)[len(xs) // 2], 4) if xs else None

    top_orgs = sorted(orgs.items(), key=lambda x: -x[1])[:5]

    latest = sorted(reports, key=lambda r: r.get("publishDate", ""), reverse=True)[:5]
    latest_items = []
    for r in latest:
        latest_items.append({
            "date": (r.get("publishDate") or "")[:10],
            "org": r.get("orgSName", ""),
            "rating": r.get("emRatingName", ""),
            "title": (r.get("title") or "")[:80],
            "eps_this": _eps_or_none(r.get("predictThisYearEps")),
            "eps_next": _eps_or_none(r.get("predictNextYearEps")),
        })

    result = {
        "code": code,
        "name": stock_name,
        "total_count": len(reports),
        "recent_count": len(recent),
        "eps_median_this_year": med(eps_this),
        "eps_median_next_year": med(eps_next),
        "eps_median_next_two_year": med(eps_next_two),
        "eps_sample_count": max(len(eps_this), len(eps_next)),
        "ratings": ratings,
        "top_orgs": top_orgs,
        "latest_reports": latest_items,
    }
    return result


# ---------------------------------------------------------------------------
# Consensus EPS (同花顺 basic.10jqka.com.cn)
# ---------------------------------------------------------------------------

def get_ths_consensus_eps(code: str) -> pd.DataFrame:
    """Fetch institutional consensus EPS from 同花顺.

    Returns DataFrame with columns: 年度, 预测机构数, 最小值, 均值, 最大值.
    '均值' = consensus EPS from all analysts covering this stock.
    """
    url = f"https://basic.10jqka.com.cn/new/{code}/worth.html"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/117.0.0.0 Safari/537.36"
        ),
        "Referer": "https://basic.10jqka.com.cn/",
    }
    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.encoding = "gbk"
        dfs = pd.read_html(StringIO(r.text))
        for df in dfs:
            cols = [str(c) for c in df.columns]
            if any("每股收益" in c or "均值" in c for c in cols):
                return df
        return dfs[0] if dfs else pd.DataFrame()
    except Exception as exc:
        logger.warning("同花顺一致预期EPS拉取失败 (%s): %s", code, exc)
        return pd.DataFrame()


def _parse_ths_consensus(df: pd.DataFrame) -> Dict[int, Dict[str, Any]]:
    """Parse THS consensus table into structured dict by year."""
    if df.empty:
        return {}
    result: Dict[int, Dict[str, Any]] = {}
    # Only look at rows where first cell contains a 4-digit year
    for _, row in df.iterrows():
        cells = [str(row[c]).strip() for c in df.columns]
        fiscal_year = None
        for cell in cells:
            m = re.match(r"^(\d{4})\b", cell)
            if m:
                fiscal_year = int(m.group(1))
                break
        if not fiscal_year:
            continue
        # Extract all floats; EPS is typically between 0.10 and 200
        floats = []
        ints = []
        for cell in cells:
            try:
                v = float(cell.replace(",", ""))
                vabs = abs(v)
                if vabs <= 0.001:
                    continue
                if 0.10 <= vabs <= 200:
                    floats.append(v)
                if vabs == int(vabs) and 1 <= vabs <= 100:
                    ints.append(int(vabs))
            except (ValueError, TypeError):
                pass
        eps_mean = floats[0] if floats else None
        analyst_count = ints[0] if ints else 0
        result[fiscal_year] = {
            "year": fiscal_year,
            "eps_mean": eps_mean,
            "analysts": analyst_count,
        }
    return result


# ---------------------------------------------------------------------------
# Valuation calculations
# ---------------------------------------------------------------------------

def calc_forward_pe(price: float, eps_forecast: float) -> Optional[float]:
    """Forward PE = price / consensus EPS forecast."""
    if eps_forecast is None or eps_forecast <= 0:
        return None
    return round(price / eps_forecast, 2)


def calc_peg(pe: float, cagr_pct: float) -> Optional[float]:
    """PEG = Forward PE / (CAGR %).

    PEG < 1  → undervalued
    PEG 1-1.5 → fair
    PEG > 1.5 → expensive
    """
    if pe is None or pe <= 0 or cagr_pct <= 0:
        return None
    return round(pe / cagr_pct, 2)


def calc_cagr(eps_this: float, eps_next: float) -> Optional[float]:
    """Growth rate from this year's consensus EPS to next year's.

    Returns percentage (e.g. 25.5 means 25.5% CAGR).
    """
    if not eps_this or not eps_next or eps_this <= 0 or eps_next <= 0:
        return None
    return round((eps_next / eps_this - 1) * 100, 1)


def calc_pe_digestion(pe: float, cagr_pct: float, target_pe: float = 30.0) -> Optional[float]:
    """Years needed for current PE to digest to target PE via earnings growth.

    Formula: log(target_PE / current_PE) / log(1 + cagr)
    Target PE is 30x (A-share growth fair-value anchor).

    Returns 0 if already below target, None if never converges.
    """
    if pe <= target_pe:
        return 0.0
    if cagr_pct is None or cagr_pct <= 0:
        return None
    return round(math.log(pe / target_pe) / math.log(1 + cagr_pct / 100), 1)


# ---------------------------------------------------------------------------
# LLM context injection
# ---------------------------------------------------------------------------

def get_research_context(
    code: str,
    price: Optional[float] = None,
    stock_name: str = "",
) -> str:
    """Build a concise 中文 prompt section combining reports + consensus EPS + valuation.

    Args:
        code: 6-digit stock code
        price: current price (for forward PE / PEG)
        stock_name: optional display name

    Returns:
        Prompt string to inject into LLM analysis context.
    """
    lines = ["**机构研报与估值（东财研报 + 同花顺一致预期）**"]

    # 1. Reports summary (fetch first for EPS fallback)
    summary: Dict[str, Any] = {}
    try:
        reports = get_eastmoney_reports(code, max_pages=2)
        summary = summarize_reports(reports, code, stock_name)
    except Exception as exc:
        logger.warning("Research reports failed: %s", exc)

    # 2. Consensus EPS + valuation
    consensus: Dict[int, Dict[str, Any]] = {}
    try:
        df_ths = get_ths_consensus_eps(code)
        consensus = _parse_ths_consensus(df_ths)
    except Exception as exc:
        logger.warning("THS consensus EPS failed: %s", exc)

    years_sorted = sorted(consensus.keys()) if consensus else []
    next_year_eps = None
    this_year_eps = None
    current_year = date.today().year

    if years_sorted:
        this_year_eps = consensus.get(current_year, {}).get("eps_mean")
        next_year_eps = consensus.get(current_year + 1, {}).get("eps_mean")

    # Sanity check: if forward PE < 3 or EPS > 100, THS parsing is wrong,
    # fall back to eastmoney report median EPS
    if next_year_eps and price:
        implied_pe = price / next_year_eps
    else:
        implied_pe = None
    if not next_year_eps or implied_pe and implied_pe < 3 or next_year_eps > 100:
        report_next = summary.get("eps_median_next_year")
        report_this = summary.get("eps_median_this_year")
        if report_next:
            next_year_eps = report_next
        if report_this:
            this_year_eps = report_this

    source_label = "同花顺一致预期" if consensus else "东财研报中位数"

    if consensus:
        lines.append("| 年度 | 一致预期EPS | 预测机构数 |")
        lines.append("|------|-------------|-----------|")
        for yr in sorted(consensus.keys()):
            info = consensus[yr]
            eps = info["eps_mean"]
            lines.append(f"| {yr} | {eps:.2f} 元 | {info['analysts']} 家 |")
    elif next_year_eps:
        lines.append(f"一致预期EPS (明年): {next_year_eps} 元 ({source_label})")
    else:
        lines.append("(一致预期数据暂无)")

    # 3. Forward PE / PEG / PE digestion
    if price and next_year_eps:
        fwd_pe = calc_forward_pe(price, next_year_eps)
        cagr_val = calc_cagr(this_year_eps or 0, next_year_eps)
        peg_val = calc_peg(fwd_pe or 0, cagr_val or 0) if fwd_pe else None
        digest = calc_pe_digestion(fwd_pe or 0, cagr_val or 0) if fwd_pe and cagr_val else None

        lines.append("")
        lines.append(f"当前价: {price} 元")
        lines.append(f"估值基准EPS: {next_year_eps} 元 ({source_label})")
        if fwd_pe:
            lines.append(f"前向PE: {fwd_pe:.1f}x")
        if cagr_val is not None:
            lines.append(f"预期增速: {cagr_val:.1f}%")
        if peg_val is not None:
            peg_label = (
                "便宜 ✅" if peg_val < 1.0
                else "合理" if peg_val < 1.5
                else "偏贵 ⚠️"
            )
            lines.append(f"PEG: {peg_val:.2f} ({peg_label})")
        if digest is not None:
            digest_label = (
                "已低于30x" if digest == 0
                else "极快(<1年)" if digest < 1
                else f"约{digest:.1f}年"
            )
            lines.append(f"PE消化到30x: {digest_label}")

    # 4. Reports display
    if summary.get("total_count", 0) > 0:
        lines.append("")
        lines.append(
            f"近1年研报: {summary['total_count']} 篇 "
            f"(近90天: {summary['recent_count']} 篇)"
        )
        if summary["ratings"]:
            rating_str = " | ".join(
                f"{k}:{v}篇" for k, v in sorted(
                    summary["ratings"].items(), key=lambda x: -x[1]
                )[:4]
            )
            lines.append(f"机构评级分布: {rating_str}")
        if summary["top_orgs"]:
            org_str = ", ".join(
                f"{o}({n}篇)" for o, n in summary["top_orgs"][:4]
            )
            lines.append(f"覆盖最多的机构: {org_str}")
        if summary["latest_reports"]:
            lines.append("最新研报:")
            for r in summary["latest_reports"][:3]:
                eps_info = ""
                if r["eps_next"]:
                    eps_info = f" [明年EPS:{r['eps_next']:.2f}]"
                lines.append(
                    f"  • {r['date']} {r['org']} {r['rating']}"
                    f" — {r['title']}{eps_info}"
                )
    else:
        lines.append("\n(该股暂无近期研报覆盖)")

    return "\n".join(lines)
