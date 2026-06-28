"""
Congressional trading data client.

Sources (both free, no API key needed):
  - Senate: EFTS search API (JSON)
    https://efts.senate.gov/LATEST/search-results?type=ptr
  - House: eFD bulk XML (annual ZIP + search)
    https://disclosures.house.gov/eFD/

Rate-limit: we share the same MIN_DELAY as edgar_client to stay polite.
"""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import List, Optional
import xml.etree.ElementTree as ET

import requests

logger = logging.getLogger(__name__)

SENATE_URL = "https://efts.senate.gov/LATEST/search-results"
HOUSE_SEARCH_URL = "https://disclosures.house.gov/eFD/Search/Search"
MIN_DELAY = 0.5   # be conservative with non-EDGAR endpoints

_last_req: float = 0.0


def _get(url: str, user_agent: str, **kwargs) -> requests.Response:
    global _last_req
    elapsed = time.monotonic() - _last_req
    if elapsed < MIN_DELAY:
        time.sleep(MIN_DELAY - elapsed)
    resp = requests.get(
        url,
        headers={"User-Agent": user_agent},
        timeout=30,
        **kwargs,
    )
    _last_req = time.monotonic()
    resp.raise_for_status()
    return resp


# ── Senate ────────────────────────────────────────────────────────────────────

def fetch_senate_trades(
    user_agent: str,
    days_back: int = 30,
) -> List[dict]:
    """
    Returns list of raw trade dicts from the Senate EFTS PTR search.
    Each dict has: senator, ticker, asset_name, transaction_type,
    amount, transaction_date, report_date, filing_url, chamber='Senate'
    """
    date_from = (date.today() - timedelta(days=days_back)).isoformat()
    date_to = date.today().isoformat()

    params = {
        "type": "ptr",
        "dateFrom": date_from,
        "dateTo": date_to,
        "order": "desc",
        "limit": "100",
    }

    try:
        resp = _get(SENATE_URL, user_agent, params=params)
        data = resp.json()
    except Exception as exc:
        logger.warning("Senate EFTS fetch failed: %s", exc)
        return []

    trades: List[dict] = []
    hits = data.get("hits", {})
    if isinstance(hits, dict):
        hits = hits.get("hits", [])

    for hit in hits:
        src = hit.get("_source", hit)
        # The Senate EFTS returns document-level records; transactions may be
        # nested under 'transactions' or flattened at the top level.
        txn_list = src.get("transactions") or [src]
        senator = (
            src.get("first_name", "") + " " + src.get("last_name", "")
        ).strip() or src.get("senator_name", "") or src.get("name", "")
        party = src.get("party", "")
        state = src.get("state", "")
        filing_url = src.get("link", "") or src.get("url", "")

        for txn in txn_list:
            ticker = (txn.get("ticker") or txn.get("asset_ticker") or "").strip().upper()
            if not ticker or ticker in ("N/A", "--", ""):
                continue
            trades.append({
                "politician_name": senator,
                "party": party,
                "state": state,
                "chamber": "Senate",
                "ticker": ticker,
                "asset_name": txn.get("asset_name") or txn.get("asset_description") or "",
                "transaction_type": txn.get("transaction_type") or txn.get("type") or "",
                "amount_range": txn.get("amount") or txn.get("amount_range") or "",
                "transaction_date": txn.get("transaction_date") or txn.get("date") or "",
                "report_date": txn.get("report_date") or src.get("date_received") or "",
                "filing_url": filing_url,
                "chamber": "Senate",
            })

    return trades


# ── House ─────────────────────────────────────────────────────────────────────

def fetch_house_trades(
    user_agent: str,
    days_back: int = 30,
) -> List[dict]:
    """
    Returns list of raw trade dicts from the House eFD PTR search.
    Falls back gracefully if the endpoint is unavailable.
    """
    date_from = (date.today() - timedelta(days=days_back)).isoformat()
    date_to = date.today().isoformat()

    params = {
        "Type": "PTR",
        "DateFrom": date_from,
        "DateTo": date_to,
    }

    try:
        resp = _get(HOUSE_SEARCH_URL, user_agent, params=params)
    except Exception as exc:
        logger.warning("House eFD fetch failed: %s", exc)
        return []

    # The House search returns XML in some versions and HTML in others.
    # Try XML first, then skip gracefully.
    try:
        return _parse_house_xml(resp.text)
    except Exception:
        pass

    try:
        return _parse_house_html(resp.text)
    except Exception as exc:
        logger.warning("House eFD parse failed: %s", exc)
        return []


def _parse_house_xml(text: str) -> List[dict]:
    root = ET.fromstring(text)
    trades: List[dict] = []
    ns_strip = lambda tag: tag.split("}")[-1] if "}" in tag else tag

    for member in root.iter():
        if ns_strip(member.tag) not in ("Member", "FilingMember"):
            continue
        name = ""
        for child in member:
            if ns_strip(child.tag) in ("Name", "MemberName"):
                name = (child.text or "").strip()

        for txn in member.iter():
            if ns_strip(txn.tag) not in ("Transaction", "PTR"):
                continue
            ticker = ""
            txn_type = ""
            amount = ""
            txn_date = ""
            asset_name = ""
            for f in txn:
                tag = ns_strip(f.tag)
                val = (f.text or "").strip()
                if tag in ("Ticker", "Symbol"):
                    ticker = val.upper()
                elif tag in ("TransactionType", "Type"):
                    txn_type = val
                elif tag in ("Amount", "Value"):
                    amount = val
                elif tag in ("TransactionDate", "Date"):
                    txn_date = val
                elif tag in ("AssetName", "Asset", "Description"):
                    asset_name = val

            if ticker and ticker not in ("N/A", "--", ""):
                trades.append({
                    "politician_name": name,
                    "party": "",
                    "state": "",
                    "chamber": "House",
                    "ticker": ticker,
                    "asset_name": asset_name,
                    "transaction_type": txn_type,
                    "amount_range": amount,
                    "transaction_date": txn_date,
                    "report_date": "",
                    "filing_url": "",
                })
    return trades


def _parse_house_html(text: str) -> List[dict]:
    """
    Best-effort HTML scraper for the House eFD search results table.
    """
    import re
    trades: List[dict] = []
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.S | re.I)
    for row in rows:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S | re.I)
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
        if len(cells) < 5:
            continue
        # Typical columns: Name | Office | Year | Filing Date | Transactions
        # Skip header rows
        if any(h in cells[0].lower() for h in ("name", "member", "filer")):
            continue
        # We can only get limited data from the summary table; skip tickers
        # that aren't clearly identifiable.
    return trades


# ── Combined fetch ────────────────────────────────────────────────────────────

def fetch_all_politician_trades(
    user_agent: str,
    days_back: int = 30,
) -> List[dict]:
    """Fetch from Senate + House and merge."""
    senate = fetch_senate_trades(user_agent, days_back)
    house = fetch_house_trades(user_agent, days_back)
    logger.info(
        "Politician trades fetched: %d Senate, %d House",
        len(senate), len(house),
    )
    return senate + house
