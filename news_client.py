"""
Financial news RSS fetcher + ticker extractor.

Monitors free RSS feeds from major financial news sources.
Uses LLM to extract stock tickers from headlines (cheap — one batch call).
Extracted tickers are added to a 5-day scan window so the pipeline
monitors them for market signals even without a Form 4 anchor.

Free sources used (no API key needed):
  - Yahoo Finance top financial stories
  - Reuters business news
  - MarketWatch top stories
  - Seeking Alpha market news
"""
from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional, Set

import requests

logger = logging.getLogger(__name__)

RSS_FEEDS = [
    ("Yahoo Finance",   "https://finance.yahoo.com/rss/topfinstories"),
    ("Reuters",         "https://feeds.reuters.com/reuters/businessNews"),
    ("MarketWatch",     "https://feeds.marketwatch.com/marketwatch/topstories/"),
    ("Seeking Alpha",   "https://seekingalpha.com/market_currents.xml"),
]

MIN_DELAY = 1.0
_last_req: float = 0.0


@dataclass
class NewsItem:
    source: str
    title: str
    url: str
    pub_date: str = ""


def _get(url: str) -> Optional[requests.Response]:
    global _last_req
    elapsed = time.monotonic() - _last_req
    if elapsed < MIN_DELAY:
        time.sleep(MIN_DELAY - elapsed)
    try:
        r = requests.get(
            url,
            headers={"User-Agent": "insider-agent/1.0 (financial news reader)"},
            timeout=15,
        )
        _last_req = time.monotonic()
        r.raise_for_status()
        return r
    except Exception as exc:
        logger.debug("RSS fetch failed for %s: %s", url, exc)
        _last_req = time.monotonic()
        return None


def _parse_rss(xml_text: str, source: str) -> List[NewsItem]:
    items: List[NewsItem] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items

    ns_strip = lambda t: t.split("}")[-1] if "}" in t else t

    for item in root.iter():
        if ns_strip(item.tag) != "item":
            continue
        title = pub_date = link = ""
        for child in item:
            tag = ns_strip(child.tag).lower()
            if tag == "title":
                title = (child.text or "").strip()
            elif tag == "pubdate":
                pub_date = (child.text or "").strip()
            elif tag == "link":
                link = (child.text or "").strip()
        if title:
            items.append(NewsItem(source=source, title=title, url=link, pub_date=pub_date))

    return items[:30]   # max 30 items per feed


def fetch_headlines() -> List[NewsItem]:
    """Fetch headlines from all configured RSS feeds."""
    all_items: List[NewsItem] = []
    for source, url in RSS_FEEDS:
        resp = _get(url)
        if resp:
            items = _parse_rss(resp.text, source)
            all_items.extend(items)
            logger.debug("RSS %s: %d items", source, len(items))
    logger.info("News: fetched %d headlines from %d sources.", len(all_items), len(RSS_FEEDS))
    return all_items


def extract_tickers_from_headlines(headlines: List[NewsItem], cfg) -> Dict[str, str]:
    """
    Uses LLM to extract stock tickers from news headlines.
    Returns dict of {ticker: headline_that_triggered_it}.
    Falls back to empty dict if LLM unavailable.
    """
    if not headlines:
        return {}

    # First pass: regex for explicit $TICKER patterns (fast, free)
    import re
    tickers: Dict[str, str] = {}
    dollar_re = re.compile(r'\$([A-Z]{1,5})\b')
    for item in headlines:
        for match in dollar_re.finditer(item.title):
            t = match.group(1)
            if t not in ("A", "I", "AT", "IT", "BE", "GO", "SO", "AM", "PM", "US"):
                tickers[t] = item.title

    # Second pass: LLM extraction for company names in headlines
    if not _has_llm(cfg):
        return tickers

    try:
        from enrich import _call_llm

        # Batch all headlines into one call
        headlines_text = "\n".join(
            f"{i+1}. {item.title}" for i, item in enumerate(headlines[:40])
        )

        system = """\
You are a financial analyst. Extract US stock tickers from news headlines.
Return ONLY a JSON array of strings with the ticker symbols you find.
Only include tickers that are clearly identifiable from company names or $TICKER mentions.
Example: ["AAPL", "NVDA", "TSLA"]
If no tickers found, return: []
Do not include ETFs (SPY, QQQ, etc.) or indices."""

        user = f"Extract stock tickers from these headlines:\n{headlines_text}"

        raw = _call_llm(system, user, cfg).strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip()

        import json
        extracted = json.loads(raw)
        if isinstance(extracted, list):
            for ticker in extracted:
                if isinstance(ticker, str) and 1 <= len(ticker) <= 5:
                    t = ticker.upper()
                    if t not in tickers:
                        # Find the headline that mentions this ticker/company
                        tickers[t] = next(
                            (item.title for item in headlines if t in item.title.upper()),
                            "News mention"
                        )

    except Exception as exc:
        logger.debug("LLM ticker extraction failed: %s", exc)

    logger.info("News tickers extracted: %s", sorted(tickers.keys()))
    return tickers


def _has_llm(cfg) -> bool:
    return bool(getattr(cfg, "llm_api_key", "") or getattr(cfg, "anthropic_api_key", ""))
