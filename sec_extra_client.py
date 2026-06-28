"""
SEC EDGAR client for 13D, 13G, and 13F filings.

Uses the same Atom feed infrastructure as edgar_client.py.
"""
from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Optional
import requests

logger = logging.getLogger(__name__)

BASE = "https://www.sec.gov"
FEED_URL = (
    "https://www.sec.gov/cgi-bin/browse-edgar"
    "?action=getcurrent&type={filing_type}&owner=include&count={count}&output=atom"
)
MIN_DELAY = 0.2
_last_req: float = 0.0


def _get(url: str, ua: str, **kw) -> requests.Response:
    global _last_req
    elapsed = time.monotonic() - _last_req
    if elapsed < MIN_DELAY:
        time.sleep(MIN_DELAY - elapsed)
    r = requests.get(url, headers={"User-Agent": ua}, timeout=30, **kw)
    _last_req = time.monotonic()
    r.raise_for_status()
    return r


# ── Dataclasses ────────────────────────────────────────────────────────────────

@dataclass
class ActivistFiling:
    filer_name: str = ""
    ticker: str = ""
    company_name: str = ""
    filing_type: str = ""      # "13D" or "13G"
    stake_pct: float = 0.0
    shares: float = 0.0
    value_usd: float = 0.0
    filing_date: str = ""
    accession: str = ""
    filing_url: str = ""


@dataclass
class InstitutionalPosition:
    fund_name: str = ""
    ticker: str = ""
    company_name: str = ""
    shares: float = 0.0
    value_usd: float = 0.0
    change_type: str = ""      # "NEW", "INCREASED", "DECREASED"
    change_pct: float = 0.0
    period_of_report: str = ""
    filing_date: str = ""
    accession: str = ""
    filing_url: str = ""


# ── 13D / 13G ──────────────────────────────────────────────────────────────────

def fetch_activist_filings(
    user_agent: str,
    count: int = 40,
) -> List[ActivistFiling]:
    """Fetch recent 13D and 13G filings from EDGAR Atom feed."""
    results: List[ActivistFiling] = []
    for ftype in ("SC 13D", "SC 13G"):
        url = FEED_URL.format(filing_type=ftype.replace(" ", "+"), count=count)
        try:
            resp = _get(url, user_agent)
            results.extend(_parse_activist_feed(resp.text, ftype.replace("SC ", "")))
        except Exception as exc:
            logger.warning("Failed to fetch %s feed: %s", ftype, exc)
    return results


def _parse_activist_feed(atom_text: str, filing_type: str) -> List[ActivistFiling]:
    root = ET.fromstring(atom_text)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    filings: List[ActivistFiling] = []

    for entry in root.findall("atom:entry", ns):
        title_el = entry.find("atom:title", ns)
        title = (title_el.text or "") if title_el is not None else ""
        link_el = entry.find("atom:link", ns)
        filing_url = link_el.get("href", "") if link_el is not None else ""
        updated_el = entry.find("atom:updated", ns)
        filing_date = ""
        if updated_el is not None and updated_el.text:
            filing_date = updated_el.text[:10]

        # Title format: "SC 13D/A - COMPANY NAME (TICKER) (0001234567) (Filer: FILER NAME)"
        ticker = _extract_ticker_from_title(title)
        company = _extract_company_from_title(title)
        filer = _extract_filer_from_title(title)
        accession = _accession_from_url(filing_url)

        if not ticker and not company:
            continue

        filings.append(ActivistFiling(
            filer_name=filer,
            ticker=ticker,
            company_name=company,
            filing_type=filing_type,
            filing_date=filing_date,
            accession=accession,
            filing_url=filing_url,
            # stake_pct, shares, value_usd require downloading the actual filing
            # — left at 0 for feed-level data; enrich if needed
        ))

    return filings


def _extract_ticker_from_title(title: str) -> str:
    m = re.search(r"\(([A-Z]{1,5})\)", title)
    return m.group(1) if m else ""


def _extract_company_from_title(title: str) -> str:
    # "SC 13D - COMPANY NAME (TICKER) ..."
    m = re.match(r"SC 13[DG](?:/A)?\s*-\s*(.+?)(?:\s*\([A-Z]{1,5}\)|\s*\(\d)", title)
    return m.group(1).strip() if m else ""


def _extract_filer_from_title(title: str) -> str:
    m = re.search(r"\(Filer:\s*(.+?)\)", title)
    return m.group(1).strip() if m else ""


def _accession_from_url(url: str) -> str:
    m = re.search(r"(\d{10}-\d{2}-\d{6})", url)
    return m.group(1) if m else ""


# ── 13F ────────────────────────────────────────────────────────────────────────

def fetch_institutional_positions(
    user_agent: str,
    tickers_of_interest: List[str],
    count: int = 40,
) -> List[InstitutionalPosition]:
    """
    Fetch recent 13F-HR filings and extract positions for tickers of interest.
    Downloads each filing's index to find the XML, then parses holdings.
    """
    if not tickers_of_interest:
        return []

    tickers_upper = {t.upper() for t in tickers_of_interest}
    url = FEED_URL.format(filing_type="13F-HR", count=count)

    try:
        resp = _get(url, user_agent)
        filing_refs = _parse_13f_feed(resp.text)
    except Exception as exc:
        logger.warning("Failed to fetch 13F feed: %s", exc)
        return []

    positions: List[InstitutionalPosition] = []
    for ref in filing_refs[:10]:   # limit to avoid too many requests
        try:
            new = _fetch_13f_positions(ref, user_agent, tickers_upper)
            positions.extend(new)
        except Exception as exc:
            logger.debug("13F parse error for %s: %s", ref.get("accession"), exc)

    return positions


def _parse_13f_feed(atom_text: str) -> List[dict]:
    root = ET.fromstring(atom_text)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    refs = []
    for entry in root.findall("atom:entry", ns):
        link_el = entry.find("atom:link", ns)
        title_el = entry.find("atom:title", ns)
        updated_el = entry.find("atom:updated", ns)
        refs.append({
            "url": link_el.get("href", "") if link_el is not None else "",
            "filer": (title_el.text or "") if title_el is not None else "",
            "date": updated_el.text[:10] if updated_el is not None and updated_el.text else "",
            "accession": _accession_from_url(link_el.get("href", "") if link_el is not None else ""),
        })
    return refs


def _fetch_13f_positions(
    ref: dict,
    user_agent: str,
    tickers: set,
) -> List[InstitutionalPosition]:
    """Download a 13F filing's XML and extract positions for given tickers."""
    index_url = ref["url"]
    if not index_url:
        return []

    # Convert index HTML URL to directory URL
    dir_url = re.sub(r"/[^/]+-index\.htm[l]?$", "/", index_url)
    if dir_url == index_url:
        dir_url = index_url.rsplit("/", 1)[0] + "/"

    try:
        index_resp = _get(dir_url + "index.json", user_agent)
        items = index_resp.json().get("directory", {}).get("item", [])
    except Exception:
        return []

    # Find the information table XML (13F holdings)
    xml_name = None
    for item in items:
        name = item.get("name", "").lower()
        if "infotable" in name or "information_table" in name:
            xml_name = item["name"]
            break
    if not xml_name:
        for item in items:
            if item.get("name", "").lower().endswith(".xml") and item.get("name", "").lower() != "primary_doc.xml":
                xml_name = item["name"]
                break
    if not xml_name:
        return []

    try:
        xml_resp = _get(dir_url + xml_name, user_agent)
        return _parse_13f_xml(
            xml_resp.text,
            fund_name=ref.get("filer", ""),
            period=ref.get("date", ""),
            filing_url=index_url,
            accession=ref.get("accession", ""),
            tickers=tickers,
        )
    except Exception as exc:
        logger.debug("13F XML parse error: %s", exc)
        return []


def _parse_13f_xml(
    xml_text: str,
    fund_name: str,
    period: str,
    filing_url: str,
    accession: str,
    tickers: set,
) -> List[InstitutionalPosition]:
    xml_text = xml_text.lstrip("﻿").strip()
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    positions: List[InstitutionalPosition] = []
    ns_strip = lambda tag: re.sub(r"^\{[^}]+\}", "", tag)

    for info_entry in root.iter():
        if ns_strip(info_entry.tag) not in ("infoTable", "INFO"):
            continue

        ticker = ""
        cusip = ""
        company = ""
        value = 0.0
        shares = 0.0

        for child in info_entry:
            tag = ns_strip(child.tag).lower()
            val = (child.text or "").strip()
            if tag in ("ticker", "tickersymbol"):
                ticker = val.upper()
            elif tag == "cusip":
                cusip = val
            elif tag in ("nameofissuer", "name"):
                company = val
            elif tag in ("value", "val"):
                try: value = float(val.replace(",", "")) * 1000  # 13F values in $000s
                except: pass
            elif tag in ("shrsorprnamt", "shares", "sshprnamt"):
                for subchild in child:
                    if ns_strip(subchild.tag).lower() in ("sshprnamt", "shares"):
                        try: shares = float((subchild.text or "").replace(",", ""))
                        except: pass
                if shares == 0:
                    try: shares = float(val.replace(",", ""))
                    except: pass

        if ticker in tickers:
            positions.append(InstitutionalPosition(
                fund_name=fund_name,
                ticker=ticker,
                company_name=company,
                shares=shares,
                value_usd=value,
                change_type="NEW",   # simplified — full change tracking needs prior quarter data
                period_of_report=period,
                filing_date=period,
                accession=accession,
                filing_url=filing_url,
            ))

    return positions
