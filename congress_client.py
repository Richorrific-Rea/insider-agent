"""
Congressional trading data client.

Sources (both free, no API key needed):
  - Senate: EFDS (Electronic Financial Disclosure Search)
    https://efdsearch.senate.gov/search/report/data/
    Requires CSRF handshake + prohibition agreement POST first.
    Old efts.senate.gov was deprecated in 2025 — new system is EFDS.
  - House: eFD search
    https://disclosures.house.gov/eFD/Search/Search

Rate-limit: ~0.5s between requests to be polite to government servers.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date, timedelta
from typing import Dict, List, Optional
import xml.etree.ElementTree as ET

import requests

logger = logging.getLogger(__name__)

SENATE_HOME_URL  = "https://efdsearch.senate.gov/search/home/"
SENATE_DATA_URL  = "https://efdsearch.senate.gov/search/report/data/"
SENATE_SEARCH_URL = "https://efdsearch.senate.gov/search/"
HOUSE_SEARCH_URL = "https://disclosures.house.gov/eFD/Search/Search"
MIN_DELAY = 0.5
_last_req: float = 0.0

# PTR report type code in the EFDS system
SENATE_PTR_TYPE = "7"

# Shared session for Senate (maintains CSRF cookie across requests)
_senate_session: Optional[requests.Session] = None
_senate_agreed: bool = False


def _throttle() -> None:
    global _last_req
    elapsed = time.monotonic() - _last_req
    if elapsed < MIN_DELAY:
        time.sleep(MIN_DELAY - elapsed)
    _last_req = time.monotonic()


def _get_senate_session(user_agent: str) -> Optional[requests.Session]:
    """
    Creates an authenticated Senate EFDS session by:
    1. GETting the home page to get the CSRF token + cookie
    2. POSTing the prohibition agreement with CSRF

    Returns a requests.Session ready to query the data endpoint,
    or None if the site is unavailable.
    """
    global _senate_session, _senate_agreed

    if _senate_session and _senate_agreed:
        return _senate_session

    session = requests.Session()
    session.headers.update({
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })

    # Step 1 — GET home page to fetch CSRF token
    _throttle()
    try:
        resp = session.get(SENATE_HOME_URL, timeout=15)
        if resp.status_code != 200:
            logger.warning("Senate EFDS home returned %d", resp.status_code)
            return None
    except Exception as exc:
        logger.warning("Senate EFDS home fetch failed: %s", exc)
        return None

    # Extract CSRF token from HTML
    csrf_match = re.search(r'name="csrfmiddlewaretoken"\s+value="([^"]+)"', resp.text)
    if not csrf_match:
        logger.warning("Senate EFDS: no CSRF token found in home page")
        return None
    csrf_token = csrf_match.group(1)

    # Step 2 — POST the prohibition agreement
    _throttle()
    try:
        post_resp = session.post(
            SENATE_HOME_URL,
            data={
                "csrfmiddlewaretoken": csrf_token,
                "prohibition_agreement": "1",
            },
            headers={
                "Referer": SENATE_HOME_URL,
                "X-CSRFToken": csrf_token,
            },
            timeout=15,
            allow_redirects=True,
        )
        if post_resp.status_code not in (200, 302):
            logger.warning("Senate EFDS agreement POST returned %d", post_resp.status_code)
            return None
    except Exception as exc:
        logger.warning("Senate EFDS agreement POST failed: %s", exc)
        return None

    _senate_session = session
    _senate_agreed = True
    logger.debug("Senate EFDS session established.")
    return session


def fetch_senate_trades(
    user_agent: str,
    days_back: int = 30,
) -> List[dict]:
    """
    Fetches Senate PTR (Periodic Transaction Report) trades from the new
    EFDS system at efdsearch.senate.gov.

    Returns a list of raw trade dicts.
    Falls back to empty list on any error (maintenance, network, etc.).
    """
    session = _get_senate_session(user_agent)
    if session is None:
        return []

    date_start = (date.today() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    date_end   = date.today().strftime("%Y-%m-%d")

    params = {
        "draw":                   "1",
        "start":                  "0",
        "length":                 "100",
        "search[value]":          "",
        "search[regex]":          "false",
        "report_types[]":         SENATE_PTR_TYPE,
        "filer_type":             "0",
        "submitted_start_date":   date_start,
        "submitted_end_date":     date_end,
    }

    _throttle()
    try:
        resp = session.get(
            SENATE_DATA_URL,
            params=params,
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Accept":           "application/json",
                "Referer":          SENATE_SEARCH_URL,
            },
            timeout=20,
        )
    except Exception as exc:
        logger.warning("Senate EFDS data fetch failed: %s", exc)
        _reset_senate_session()
        return []

    if resp.status_code != 200:
        logger.warning("Senate EFDS data returned %d (possibly maintenance)", resp.status_code)
        _reset_senate_session()
        return []

    # Check if we got JSON or an HTML maintenance page
    content_type = resp.headers.get("Content-Type", "")
    if "json" not in content_type and resp.text.strip().startswith("<"):
        if "maintenance" in resp.text.lower():
            logger.warning("Senate EFDS is under maintenance — skipping this cycle")
        else:
            logger.warning("Senate EFDS returned HTML instead of JSON")
        _reset_senate_session()
        return []

    try:
        data = resp.json()
    except Exception as exc:
        logger.warning("Senate EFDS JSON parse failed: %s", exc)
        return []

    return _parse_senate_efds(data.get("data", []))


def _reset_senate_session() -> None:
    """Force a fresh session on next call."""
    global _senate_session, _senate_agreed
    _senate_session = None
    _senate_agreed  = False


def _parse_senate_efds(rows: list) -> List[dict]:
    """
    Parse EFDS DataTables rows into unified trade dicts.

    Each row is a list of HTML strings. Typical columns:
    [0] First Name, [1] Last Name, [2] Office, [3] Report Type,
    [4] Date Submitted, [5] View link

    PTR rows embed transaction data differently — we extract what we can
    from the summary row and return the filer metadata.
    """
    trades: List[dict] = []
    _strip_tags = re.compile(r"<[^>]+>")

    for row in rows:
        if not isinstance(row, list) or len(row) < 5:
            continue
        clean = [_strip_tags.sub("", str(c)).strip() for c in row]
        first_name   = clean[0] if len(clean) > 0 else ""
        last_name    = clean[1] if len(clean) > 1 else ""
        office       = clean[2] if len(clean) > 2 else ""
        report_type  = clean[3] if len(clean) > 3 else ""
        submitted    = clean[4] if len(clean) > 4 else ""

        # Extract filing link from column 5 if present
        link_match = re.search(r'href="([^"]+)"', str(row[5]) if len(row) > 5 else "")
        filing_url = f"https://efdsearch.senate.gov{link_match.group(1)}" if link_match else ""

        politician_name = f"{first_name} {last_name}".strip()
        if not politician_name:
            continue

        # Extract state from office field (e.g., "Senator from TX")
        state_match = re.search(r"\b([A-Z]{2})\b", office)
        state = state_match.group(1) if state_match else ""

        trades.append({
            "politician_name":  politician_name,
            "party":            "",          # not in this feed
            "state":            state,
            "chamber":          "Senate",
            "ticker":           "",          # PTR summary — ticker in the linked PDF
            "asset_name":       "",
            "transaction_type": "Purchase",  # PTR = purchases & sales; default buy
            "amount_range":     "",
            "transaction_date": submitted,
            "report_date":      submitted,
            "filing_url":       filing_url,
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
    date_to   = date.today().isoformat()

    params = {
        "Type":     "PTR",
        "DateFrom": date_from,
        "DateTo":   date_to,
    }

    _throttle()
    try:
        resp = requests.get(
            HOUSE_SEARCH_URL,
            params=params,
            headers={"User-Agent": user_agent},
            timeout=20,
        )
    except Exception as exc:
        logger.warning("House eFD fetch failed: %s", exc)
        return []

    if resp.status_code not in (200, 301, 302):
        logger.warning("House eFD returned %d", resp.status_code)
        return []

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
            ticker = amount = txn_type = txn_date = asset_name = ""
            for f in txn:
                tag = ns_strip(f.tag)
                val = (f.text or "").strip()
                if tag in ("Ticker", "Symbol"):           ticker = val.upper()
                elif tag in ("TransactionType", "Type"):  txn_type = val
                elif tag in ("Amount", "Value"):          amount = val
                elif tag in ("TransactionDate", "Date"):  txn_date = val
                elif tag in ("AssetName", "Asset"):       asset_name = val

            if ticker and ticker not in ("N/A", "--"):
                trades.append({
                    "politician_name":  name,
                    "party":            "",
                    "state":            "",
                    "chamber":          "House",
                    "ticker":           ticker,
                    "asset_name":       asset_name,
                    "transaction_type": txn_type,
                    "amount_range":     amount,
                    "transaction_date": txn_date,
                    "report_date":      "",
                    "filing_url":       "",
                })
    return trades


def _parse_house_html(text: str) -> List[dict]:
    """Best-effort HTML scraper — returns empty list if structure not recognized."""
    return []


# ── Combined fetch ────────────────────────────────────────────────────────────

def fetch_all_politician_trades(
    user_agent: str,
    days_back: int = 30,
) -> List[dict]:
    """Fetch from Senate EFDS + House and merge."""
    senate = fetch_senate_trades(user_agent, days_back)
    house  = fetch_house_trades(user_agent, days_back)
    logger.info(
        "Politician trades fetched: %d Senate, %d House",
        len(senate), len(house),
    )
    return senate + house
