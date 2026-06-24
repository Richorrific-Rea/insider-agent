"""
Form 4 XML parser.

Parses the ownershipDocument XML into Transaction dataclasses.
Only nonDerivativeTable entries are returned (no options/warrants).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional
import xml.etree.ElementTree as ET

# Maps normalized title tokens → canonical role label
_TITLE_ROLE_MAP = [
    (re.compile(r"\bCEO\b|CHIEF EXECUTIVE", re.I), "CEO"),
    (re.compile(r"\bCFO\b|CHIEF FINANCIAL", re.I), "CFO"),
    (re.compile(r"\bCOO\b|CHIEF OPERATING", re.I), "COO"),
    (re.compile(r"\bPRES(IDENT)?\b", re.I), "PRES"),
    (re.compile(r"\bDIR(ECTOR)?\b", re.I), "DIR"),
]


def _text(el: Optional[ET.Element], default: str = "") -> str:
    if el is None:
        return default
    return (el.text or "").strip()


def _val(el: Optional[ET.Element], default: str = "") -> str:
    """Read text from el, preferring a <value> child if present (EDGAR pattern)."""
    if el is None:
        return default
    value_child = None
    for child in el:
        if _strip_ns(child.tag) == "value":
            value_child = child
            break
    if value_child is not None:
        return (value_child.text or "").strip()
    return (el.text or "").strip()


def _float_val(el: Optional[ET.Element], default: float = 0.0) -> float:
    t = _val(el)
    try:
        return float(t.replace(",", ""))
    except (ValueError, AttributeError):
        return default


def _float_text(el: Optional[ET.Element], default: float = 0.0) -> float:
    t = _text(el)
    try:
        return float(t.replace(",", ""))
    except (ValueError, AttributeError):
        return default


def _bool_text(el: Optional[ET.Element]) -> bool:
    return _text(el) == "1"


@dataclass
class Transaction:
    # Identifiers
    accession_number: str = ""
    filing_url: str = ""

    # Issuer
    ticker: str = ""
    issuer_name: str = ""

    # Reporting owner
    owner_name: str = ""
    is_director: bool = False
    is_officer: bool = False
    is_ten_percent_owner: bool = False
    officer_title: str = ""

    # Transaction details
    transaction_date: str = ""        # ISO date string YYYY-MM-DD
    transaction_code: str = ""        # P, S, A, D, F, …
    acquired_disposed: str = ""       # A or D
    shares: float = 0.0
    price: float = 0.0
    value: float = 0.0                # shares * price

    shares_owned_following: float = 0.0

    # Derived
    delta_own_pct: float = 0.0        # shares / pre-transaction holding

    @property
    def role_labels(self) -> List[str]:
        labels: List[str] = []
        if self.is_ten_percent_owner:
            labels.append("TENPCT")
        if self.is_director:
            labels.append("DIR")
        if self.is_officer:
            title_upper = self.officer_title.upper()
            matched = False
            for pattern, label in _TITLE_ROLE_MAP:
                if pattern.search(title_upper):
                    labels.append(label)
                    matched = True
            if not matched:
                labels.append("OFFICER")
        return labels or ["UNKNOWN"]


def _strip_ns(tag: str) -> str:
    """Remove XML namespace prefix {uri}localname -> localname."""
    return re.sub(r"^\{[^}]+\}", "", tag)


def _find(root: ET.Element, *path_parts: str) -> Optional[ET.Element]:
    """Namespace-agnostic find via iterative tag matching."""
    current = root
    for part in path_parts:
        found = None
        for child in current:
            if _strip_ns(child.tag) == part:
                found = child
                break
        if found is None:
            return None
        current = found
    return current


def _findall(root: ET.Element, tag: str) -> List[ET.Element]:
    return [child for child in root.iter() if _strip_ns(child.tag) == tag]


def parse_form4(
    xml_text: str,
    accession_number: str = "",
    filing_url: str = "",
) -> List[Transaction]:
    """
    Parses an ownershipDocument XML string and returns one Transaction per
    nonDerivative transaction entry that is a buy (acquired_disposed == 'A').
    """
    try:
        # Strip BOM / leading whitespace
        xml_text = xml_text.lstrip("﻿").strip()
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    transactions: List[Transaction] = []

    # ── Issuer ───────────────────────────────────────────────────────────
    issuer_el = _find(root, "issuer")
    ticker = _text(_find(issuer_el, "issuerTradingSymbol")) if issuer_el is not None else ""
    issuer_name = _text(_find(issuer_el, "issuerName")) if issuer_el is not None else ""

    # ── Reporting owner ──────────────────────────────────────────────────
    ro_el = _find(root, "reportingOwner")
    owner_name = ""
    is_director = False
    is_officer = False
    is_ten_pct = False
    officer_title = ""

    if ro_el is not None:
        id_el = _find(ro_el, "reportingOwnerId")
        owner_name = _text(_find(id_el, "rptOwnerName")) if id_el is not None else ""

        rel_el = _find(ro_el, "reportingOwnerRelationship")
        if rel_el is not None:
            is_director = _bool_text(_find(rel_el, "isDirector"))
            is_officer = _bool_text(_find(rel_el, "isOfficer"))
            is_ten_pct = _bool_text(_find(rel_el, "isTenPercentOwner"))
            officer_title = _text(_find(rel_el, "officerTitle"))

    # ── nonDerivativeTable ───────────────────────────────────────────────
    nd_table = _find(root, "nonDerivativeTable")
    if nd_table is None:
        return []

    nd_transactions = _findall(nd_table, "nonDerivativeTransaction")
    for txn_el in nd_transactions:
        amounts_el = _find(txn_el, "transactionAmounts")
        post_el = _find(txn_el, "postTransactionAmounts")
        coding_el = _find(txn_el, "transactionCoding")

        # transactionCode is a direct-text field (no <value> wrapper)
        txn_code = _text(_find(coding_el, "transactionCode")) if coding_el is not None else ""
        # All other transaction fields use the <value> child pattern
        acq_disp = _val(_find(amounts_el, "transactionAcquiredDisposedCode")) if amounts_el is not None else ""
        shares = _float_val(_find(amounts_el, "transactionShares")) if amounts_el is not None else 0.0
        price = _float_val(_find(amounts_el, "transactionPricePerShare")) if amounts_el is not None else 0.0
        shares_following = _float_val(_find(post_el, "sharesOwnedFollowingTransaction")) if post_el is not None else 0.0
        txn_date = _val(_find(txn_el, "transactionDate"))

        value = shares * price

        # delta ownership: shares purchased / pre-transaction holding
        pre_holding = shares_following - shares if acq_disp == "A" else shares_following + shares
        delta_own_pct = (shares / pre_holding) if pre_holding > 0 else float("inf")

        t = Transaction(
            accession_number=accession_number,
            filing_url=filing_url,
            ticker=ticker.upper() if ticker else "",
            issuer_name=issuer_name,
            owner_name=owner_name,
            is_director=is_director,
            is_officer=is_officer,
            is_ten_percent_owner=is_ten_pct,
            officer_title=officer_title,
            transaction_date=txn_date,
            transaction_code=txn_code,
            acquired_disposed=acq_disp,
            shares=shares,
            price=price,
            value=value,
            shares_owned_following=shares_following,
            delta_own_pct=delta_own_pct,
        )
        transactions.append(t)

    return transactions
