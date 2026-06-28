"""
Congressional trade parser.

Converts raw dicts from congress_client into PoliticianTrade dataclasses.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

# Normalize transaction type to BUY / SELL / OTHER
_BUY_RE = re.compile(r"purchase|buy|bought|acquisition", re.I)
_SELL_RE = re.compile(r"sale|sell|sold|disposal", re.I)


@dataclass
class PoliticianTrade:
    politician_name: str = ""
    party: str = ""           # D, R, I
    state: str = ""
    chamber: str = ""         # Senate, House
    ticker: str = ""
    asset_name: str = ""
    transaction_type: str = ""  # BUY, SELL, OTHER
    amount_range: str = ""      # raw string e.g. "$15,001 - $50,000"
    amount_min: float = 0.0     # lower bound in USD
    amount_max: float = 0.0     # upper bound in USD
    transaction_date: str = ""
    report_date: str = ""
    filing_url: str = ""

    @property
    def is_purchase(self) -> bool:
        return self.transaction_type == "BUY"

    @property
    def party_label(self) -> str:
        mapping = {"D": "Dem", "R": "Rep", "I": "Ind"}
        return mapping.get(self.party.upper(), self.party or "?")

    @property
    def label(self) -> str:
        """Short display label, e.g. 'Nancy Pelosi (D-CA)'"""
        if self.state:
            return f"{self.politician_name} ({self.party}-{self.state})"
        return f"{self.politician_name} ({self.party})" if self.party else self.politician_name


def _parse_amount(raw: str) -> tuple[float, float]:
    """
    Parse strings like '$15,001 - $50,000' or '$250,000+' into (min, max).
    Returns (0, 0) if unparseable.
    """
    raw = raw.replace(",", "").replace("$", "").strip()
    # Range: "15001 - 50000"
    m = re.search(r"(\d+)\s*[-–]\s*(\d+)", raw)
    if m:
        return float(m.group(1)), float(m.group(2))
    # Single value or "250000+"
    m = re.search(r"(\d+)", raw)
    if m:
        val = float(m.group(1))
        return val, val
    return 0.0, 0.0


def _normalize_type(raw: str) -> str:
    if _BUY_RE.search(raw):
        return "BUY"
    if _SELL_RE.search(raw):
        return "SELL"
    return "OTHER"


def parse_politician_trades(raw_list: List[dict]) -> List[PoliticianTrade]:
    trades: List[PoliticianTrade] = []
    for d in raw_list:
        ticker = (d.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        txn_type = _normalize_type(d.get("transaction_type") or "")
        amount_raw = d.get("amount_range") or ""
        amount_min, amount_max = _parse_amount(amount_raw)
        trades.append(PoliticianTrade(
            politician_name=d.get("politician_name") or "",
            party=d.get("party") or "",
            state=d.get("state") or "",
            chamber=d.get("chamber") or "",
            ticker=ticker,
            asset_name=d.get("asset_name") or "",
            transaction_type=txn_type,
            amount_range=amount_raw,
            amount_min=amount_min,
            amount_max=amount_max,
            transaction_date=d.get("transaction_date") or "",
            report_date=d.get("report_date") or "",
            filing_url=d.get("filing_url") or "",
        ))
    return trades
