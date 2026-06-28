"""
Signal filtering, cluster detection, and confluence scoring.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional, Set

from config import Config
from form4_parser import Transaction


@dataclass
class Signal:
    """A purchase that passed hard filters, enriched with cluster information."""
    transaction: Transaction
    is_cluster: bool = False
    cluster_size: int = 1
    cluster_transactions: List[Transaction] = field(default_factory=list)


# ── Confluence ─────────────────────────────────────────────────────────────────

CONFIDENCE_MEDIUM    = "MEDIA"      # insider(s) only, cluster
CONFIDENCE_HIGH      = "ALTA"       # insider(s) + politician(s) buying same ticker
CONFIDENCE_VERY_HIGH = "MUY ALTA"   # cluster of insiders + multiple politicians


@dataclass
class ConfluenceSignal:
    """
    Enriched signal that combines insider + politician data for the same ticker.
    """
    ticker: str
    insider_signals: List[Signal]           # qualified insider signals for this ticker
    politician_trades: List = field(default_factory=list)  # PoliticianTrade objects
    confidence: str = CONFIDENCE_MEDIUM
    window_days: int = 14

    @property
    def total_insider_value(self) -> float:
        return sum(s.transaction.value for s in self.insider_signals)

    @property
    def distinct_insiders(self) -> int:
        return len({s.transaction.owner_name for s in self.insider_signals})

    @property
    def distinct_politicians(self) -> int:
        return len({p.politician_name for p in self.politician_trades})

    @property
    def has_cluster(self) -> bool:
        return any(s.is_cluster for s in self.insider_signals)

    @property
    def primary_signal(self) -> Signal:
        """The highest-value insider signal for this ticker."""
        return max(self.insider_signals, key=lambda s: s.transaction.value)


def passes_filters(txn: Transaction, cfg: Config) -> bool:
    """Hard rules — all must pass."""
    if cfg.only_open_market_purchase and txn.transaction_code != "P":
        return False
    if txn.acquired_disposed != "A":
        return False
    if txn.value < cfg.min_trade_value_usd:
        return False
    if txn.delta_own_pct < cfg.min_delta_own_pct:
        return False
    roles = set(txn.role_labels)
    if not roles.intersection(cfg.allowed_roles):
        return False
    return True


def detect_clusters(
    transactions: List[Transaction],
    cfg: Config,
) -> List[Signal]:
    """
    Groups qualifying transactions by ticker within cluster_window_days.
    Marks a Signal as cluster if >= cluster_min_insiders distinct insiders
    bought the same stock.  Returns signals sorted by trade value (descending).
    """
    qualified = [t for t in transactions if passes_filters(t, cfg)]
    if not qualified:
        return []

    by_ticker: Dict[str, List[Transaction]] = defaultdict(list)
    for txn in qualified:
        if txn.ticker:
            by_ticker[txn.ticker].append(txn)

    signals: List[Signal] = []

    def _parse_date(t: Transaction) -> date:
        try:
            return date.fromisoformat(t.transaction_date)
        except (ValueError, AttributeError):
            return date.min

    for ticker, txns in by_ticker.items():
        txns_sorted = sorted(txns, key=_parse_date)
        for txn in txns_sorted:
            txn_date = _parse_date(txn)
            window_txns = [
                t for t in txns_sorted
                if abs((_parse_date(t) - txn_date).days) <= cfg.cluster_window_days
            ]
            distinct_insiders: Set[str] = {t.owner_name for t in window_txns}
            is_cluster = len(distinct_insiders) >= cfg.cluster_min_insiders
            signals.append(Signal(
                transaction=txn,
                is_cluster=is_cluster,
                cluster_size=len(distinct_insiders),
                cluster_transactions=window_txns if is_cluster else [],
            ))

    # Deduplicate by accession_number
    seen: Set[str] = set()
    unique: List[Signal] = []
    for sig in signals:
        key = sig.transaction.accession_number
        if key not in seen:
            seen.add(key)
            unique.append(sig)

    unique.sort(key=lambda s: s.transaction.value, reverse=True)
    return unique


def detect_confluence(
    insider_signals: List[Signal],
    politician_trades: List,        # List[PoliticianTrade]
    cfg: Config,
) -> List[ConfluenceSignal]:
    """
    For each ticker present in insider_signals, checks whether any politician
    also BOUGHT the same ticker within confluence_window_days.

    Confidence levels:
      MEDIA    — insider cluster but no politicians
      ALTA     — at least one politician buying the same ticker
      MUY ALTA — insider cluster + >= 2 politicians buying

    Returns one ConfluenceSignal per ticker, sorted by confidence then value.
    """
    def _parse_date_str(s: str) -> Optional[date]:
        try:
            return date.fromisoformat(s)
        except (ValueError, AttributeError):
            return None

    # Build map: ticker → list of politician purchases
    pol_buys: Dict[str, list] = defaultdict(list)
    for pt in politician_trades:
        if pt.is_purchase and pt.ticker:
            pol_buys[pt.ticker.upper()].append(pt)

    # Group insider signals by ticker
    by_ticker: Dict[str, List[Signal]] = defaultdict(list)
    for sig in insider_signals:
        if sig.transaction.ticker:
            by_ticker[sig.transaction.ticker].append(sig)

    confluence: List[ConfluenceSignal] = []
    window = timedelta(days=cfg.confluence_window_days)

    for ticker, sigs in by_ticker.items():
        # Find politician purchases of the same ticker within the window
        insider_dates = [
            _parse_date_str(s.transaction.transaction_date) for s in sigs
        ]
        insider_dates = [d for d in insider_dates if d]
        if not insider_dates:
            continue

        insider_min = min(insider_dates)
        insider_max = max(insider_dates)

        matching_pols = []
        for pt in pol_buys.get(ticker, []):
            pt_date = _parse_date_str(pt.transaction_date)
            if pt_date is None:
                matching_pols.append(pt)  # include if date unknown
                continue
            # Overlaps if politician trade is within window of any insider trade
            if (pt_date >= insider_min - window) and (pt_date <= insider_max + window):
                matching_pols.append(pt)

        n_pols = len({p.politician_name for p in matching_pols})
        has_cluster = any(s.is_cluster for s in sigs)

        if n_pols >= 2 or (n_pols >= 1 and has_cluster):
            confidence = CONFIDENCE_VERY_HIGH
        elif n_pols >= cfg.confluence_min_politicians:
            confidence = CONFIDENCE_HIGH
        elif has_cluster:
            confidence = CONFIDENCE_MEDIUM
        else:
            confidence = CONFIDENCE_MEDIUM

        confluence.append(ConfluenceSignal(
            ticker=ticker,
            insider_signals=sigs,
            politician_trades=matching_pols,
            confidence=confidence,
            window_days=cfg.confluence_window_days,
        ))

    # Sort: VERY_HIGH first, then HIGH, then MEDIUM; within each by total value
    _order = {CONFIDENCE_VERY_HIGH: 0, CONFIDENCE_HIGH: 1, CONFIDENCE_MEDIUM: 2}
    confluence.sort(key=lambda c: (_order.get(c.confidence, 9), -c.total_insider_value))
    return confluence
