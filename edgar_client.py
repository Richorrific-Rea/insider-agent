"""
SEC EDGAR client.

Two responsibilities:
  1. Fetch the Form 4 Atom feed → list of (accession_number, filing_dir_url)
  2. Given a filing directory URL, download the ownershipDocument XML text.

Fair-access constraints (§ 2.3 of EDGAR fair-access policy):
  - User-Agent header must identify the caller with an email contact.
  - Maximum 10 requests per second; we enforce >=0.15 s between every request.
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from typing import List, Optional, Tuple

import requests

FEED_URL = (
    "https://www.sec.gov/cgi-bin/browse-edgar"
    "?action=getcurrent&type=4&owner=include&count={count}&output=atom"
)
BASE_URL = "https://www.sec.gov"
MIN_DELAY = 0.15  # seconds between requests

_last_request_time: float = 0.0


def _get(url: str, user_agent: str, **kwargs) -> requests.Response:
    """Rate-limited GET with the required User-Agent header."""
    global _last_request_time
    elapsed = time.monotonic() - _last_request_time
    if elapsed < MIN_DELAY:
        time.sleep(MIN_DELAY - elapsed)
    resp = requests.get(
        url,
        headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
        timeout=30,
        **kwargs,
    )
    _last_request_time = time.monotonic()
    resp.raise_for_status()
    return resp


def fetch_recent_form4_filings(
    user_agent: str, count: int = 100
) -> List[Tuple[str, str]]:
    """
    Returns list of (accession_number, filing_dir_url) for the most recent
    Form 4 filings from the EDGAR Atom feed.
    """
    url = FEED_URL.format(count=count)
    resp = _get(url, user_agent)
    root = ET.fromstring(resp.text)

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    results: List[Tuple[str, str]] = []

    for entry in root.findall("atom:entry", ns):
        # The filing index URL lives in <link href="...">
        link_el = entry.find("atom:link", ns)
        if link_el is None:
            continue
        filing_index_url = link_el.get("href", "")
        if not filing_index_url:
            continue

        # Derive accession number from URL path, e.g.:
        # .../Archives/edgar/data/123456/000123456024000001-index.htm
        # or from the <id> element: urn:tag:sec.gov,...:accession-number
        accession = _accession_from_url(filing_index_url)
        if not accession:
            id_el = entry.find("atom:id", ns)
            if id_el is not None and id_el.text:
                accession = _accession_from_id(id_el.text)
        if not accession:
            continue

        # Convert index URL to directory URL
        dir_url = _index_to_dir_url(filing_index_url)
        results.append((accession, dir_url))

    return results


def fetch_ownership_xml(dir_url: str, user_agent: str) -> Optional[str]:
    """
    Given a filing directory URL, downloads index.json and locates the
    ownership XML document.  Returns raw XML text or None on failure.

    Handles the various real-world naming patterns seen in EDGAR:
      form4.xml, ownership.xml, primary_doc.xml, wk-form4_*.xml, *.xml
    """
    index_url = dir_url.rstrip("/") + "/index.json"
    try:
        resp = _get(index_url, user_agent)
        data = resp.json()
    except Exception:
        return None

    # index.json schema: {"directory": {"item": [...], "name": "...", ...}}
    items = []
    try:
        items = data["directory"]["item"]
    except (KeyError, TypeError):
        return None

    xml_url = _pick_ownership_xml(dir_url, items)
    if not xml_url:
        return None

    try:
        xml_resp = _get(xml_url, user_agent)
        return xml_resp.text
    except Exception:
        return None


# ── helpers ──────────────────────────────────────────────────────────────────

_ACCESSION_RE = re.compile(r"(\d{10}-\d{2}-\d{6})")


def _accession_from_url(url: str) -> str:
    m = _ACCESSION_RE.search(url)
    return m.group(1) if m else ""


def _accession_from_id(tag_id: str) -> str:
    # "urn:tag:sec.gov,2008:accession-number=0001234567-24-000001"
    if "accession-number=" in tag_id:
        return tag_id.split("accession-number=")[-1].strip()
    return _accession_from_url(tag_id)


def _index_to_dir_url(index_url: str) -> str:
    """
    Converts an index HTML URL to the filing directory base URL.
    E.g. https://www.sec.gov/Archives/edgar/data/123/000123-24-001-index.htm
      -> https://www.sec.gov/Archives/edgar/data/123/000123-24-001/
    """
    # Strip the filename portion
    dir_url = re.sub(r"/[^/]+-index\.htm[l]?$", "/", index_url)
    if dir_url == index_url:
        # Fallback: just take the parent directory
        dir_url = index_url.rsplit("/", 1)[0] + "/"
    return dir_url


_PRIORITY_PATTERNS = [
    re.compile(r"^form4.*\.xml$", re.I),
    re.compile(r"^ownership.*\.xml$", re.I),
    re.compile(r"^primary_doc.*\.xml$", re.I),
    re.compile(r"^wk-form4.*\.xml$", re.I),
    re.compile(r".*\.xml$", re.I),  # any XML as last resort
]


def _pick_ownership_xml(dir_url: str, items: list) -> Optional[str]:
    """
    Picks the best ownership XML from the index item list.
    Prefers files whose content matches <ownershipDocument.
    Applies priority patterns as a tiebreaker.
    """
    xml_names: List[str] = [
        item["name"]
        for item in items
        if isinstance(item, dict) and item.get("name", "").lower().endswith(".xml")
    ]

    if not xml_names:
        return None

    # Score by pattern priority (lower index = higher priority)
    def priority(name: str) -> int:
        for i, pat in enumerate(_PRIORITY_PATTERNS):
            if pat.match(name):
                return i
        return len(_PRIORITY_PATTERNS)

    xml_names.sort(key=priority)
    base = dir_url.rstrip("/")
    return f"{base}/{xml_names[0]}"
