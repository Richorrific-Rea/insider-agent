"""
Signal filtering and cluster detection.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Set

from config import Config
from form4_parser import Transaction


@dataclass
class Signal:
    """A purchase that passed hard filters, enriched with cluster information."""
    transaction: Transaction
    is_cluster: bool = False
    cluster_size: int = 1            # number of distinct insiders in the cluster
    cluster_transactions: List[Transaction] = field(default_factory=list)


def passes_filters(txn: Transaction, cfg: Config) -> bool:
    """Hard rules — all must pass."""
    # Must be an open-market purchase (code P) if configured
    if cfg.only_open_market_purchase and txn.transaction_code != "P":
        return False

    # Must be an acquisition
    if txn.acquired_disposed != "A":
        return False

    # Minimum dollar value
    if txn.value < cfg.min_trade_value_usd:
        return False

    # Minimum delta ownership percentage
    if txn.delta_own_pct < cfg.min_delta_own_pct:
        return False

    # Must have at least one allowed role
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
    bought the same stock.

    Returns all qualifying signals sorted by trade value (descending).
    """
    # Only work with transactions that pass the hard filters
    qualified = [t for t in transactions if passes_filters(t, cfg)]

    if not qualified:
        return []

    # Group by ticker
    by_ticker: Dict[str, List[Transaction]] = defaultdict(list)
    for txn in qualified:
        if txn.ticker:
            by_ticker[txn.ticker].append(txn)

    signals: List[Signal] = []

    for ticker, txns in by_ticker.items():
        # Sort by date to apply rolling window
        def _parse_date(t: Transaction) -> date:
            try:
                return date.fromisoformat(t.transaction_date)
            except (ValueError, AttributeError):
                return date.min

        txns_sorted = sorted(txns, key=_parse_date)
        window = timedelta(days=cfg.cluster_window_days)

        # For each transaction, check how many distinct insiders bought
        # within [txn_date - window, txn_date + window]
        for txn in txns_sorted:
            txn_date = _parse_date(txn)
            window_txns = [
                t for t in txns_sorted
                if abs((_parse_date(t) - txn_date).days) <= cfg.cluster_window_days
            ]
            distinct_insiders: Set[str] = {t.owner_name for t in window_txns}
            is_cluster = len(distinct_insiders) >= cfg.cluster_min_insiders

            signals.append(
                Signal(
                    transaction=txn,
                    is_cluster=is_cluster,
                    cluster_size=len(distinct_insiders),
                    cluster_transactions=window_txns if is_cluster else [],
                )
            )

    # Deduplicate by accession_number (a single filing may appear via multiple
    # paths if we merged recent state with new feed)
    seen: Set[str] = set()
    unique: List[Signal] = []
    for sig in signals:
        key = sig.transaction.accession_number
        if key not in seen:
            seen.add(key)
            unique.append(sig)

    unique.sort(key=lambda s: s.transaction.value, reverse=True)
    return unique
