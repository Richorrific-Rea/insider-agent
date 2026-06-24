"""Tests for signals.passes_filters and signals.detect_clusters — no network access."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from config import Config
from form4_parser import Transaction
from signals import Signal, detect_clusters, passes_filters


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cfg(**overrides) -> Config:
    defaults = dict(
        edgar_user_agent="Test test@test.com",
        only_open_market_purchase=True,
        allowed_roles={"CEO", "CFO", "PRES", "DIR"},
        min_trade_value_usd=100_000,
        min_delta_own_pct=0.0,
        cluster_window_days=7,
        cluster_min_insiders=2,
    )
    defaults.update(overrides)
    return Config(**defaults)


def _txn(**overrides) -> Transaction:
    """Return a Transaction that passes all default filters by default."""
    defaults = dict(
        accession_number="0001234567-24-000001",
        ticker="ACME",
        issuer_name="Acme Corp",
        owner_name="Jane CEO",
        is_director=False,
        is_officer=True,
        is_ten_percent_owner=False,
        officer_title="Chief Executive Officer",
        transaction_code="P",
        acquired_disposed="A",
        shares=10_000,
        price=15.0,
        value=150_000.0,
        shares_owned_following=110_000,
        delta_own_pct=0.10,
        transaction_date="2024-03-01",
    )
    defaults.update(overrides)
    return Transaction(**defaults)


# ── passes_filters ────────────────────────────────────────────────────────────

class TestPassesFiltersHappy:
    def test_ceo_open_market_purchase_passes(self):
        assert passes_filters(_txn(), _cfg()) is True

    def test_cfo_passes(self):
        txn = _txn(officer_title="Chief Financial Officer")
        assert passes_filters(txn, _cfg()) is True

    def test_president_passes(self):
        txn = _txn(officer_title="President")
        assert passes_filters(txn, _cfg()) is True

    def test_director_passes(self):
        txn = _txn(is_officer=False, is_director=True, officer_title="")
        assert passes_filters(txn, _cfg()) is True

    def test_ten_pct_owner_allowed_when_in_roles(self):
        cfg = _cfg(allowed_roles={"TENPCT"})
        txn = _txn(is_officer=False, is_ten_percent_owner=True, officer_title="")
        assert passes_filters(txn, cfg) is True

    def test_exactly_at_min_value_passes(self):
        txn = _txn(value=100_000.0)
        assert passes_filters(txn, _cfg()) is True


class TestPassesFiltersFail:
    def test_sale_fails(self):
        txn = _txn(acquired_disposed="D")
        assert passes_filters(txn, _cfg()) is False

    def test_non_P_code_fails_when_only_open_market(self):
        txn = _txn(transaction_code="A")  # award
        assert passes_filters(txn, _cfg(only_open_market_purchase=True)) is False

    def test_non_P_code_passes_when_flag_off(self):
        txn = _txn(transaction_code="A")
        assert passes_filters(txn, _cfg(only_open_market_purchase=False)) is True

    def test_below_min_value_fails(self):
        txn = _txn(value=99_999.99)
        assert passes_filters(txn, _cfg()) is False

    def test_role_not_in_allowed_fails(self):
        txn = _txn(officer_title="General Counsel")  # maps to OFFICER
        assert passes_filters(txn, _cfg(allowed_roles={"CEO", "CFO"})) is False

    def test_unknown_role_fails_default_config(self):
        txn = _txn(is_officer=False, is_director=False, is_ten_percent_owner=False,
                   officer_title="")
        assert passes_filters(txn, _cfg()) is False

    def test_delta_own_pct_below_min_fails(self):
        txn = _txn(delta_own_pct=0.01)
        assert passes_filters(txn, _cfg(min_delta_own_pct=0.05)) is False

    def test_delta_own_pct_at_min_passes(self):
        txn = _txn(delta_own_pct=0.05)
        assert passes_filters(txn, _cfg(min_delta_own_pct=0.05)) is True


# ── detect_clusters ───────────────────────────────────────────────────────────

class TestDetectClusters:
    def test_empty_input_returns_empty(self):
        assert detect_clusters([], _cfg()) == []

    def test_single_txn_no_cluster(self):
        signals = detect_clusters([_txn()], _cfg())
        assert len(signals) == 1
        assert signals[0].is_cluster is False
        assert signals[0].cluster_size == 1

    def test_two_insiders_same_ticker_same_day_is_cluster(self):
        txns = [
            _txn(accession_number="ACC-001", owner_name="Alice CEO",
                 officer_title="Chief Executive Officer", transaction_date="2024-03-01"),
            _txn(accession_number="ACC-002", owner_name="Bob CFO",
                 officer_title="Chief Financial Officer", transaction_date="2024-03-01"),
        ]
        signals = detect_clusters(txns, _cfg())
        cluster_signals = [s for s in signals if s.is_cluster]
        assert len(cluster_signals) == 2
        assert all(s.cluster_size == 2 for s in cluster_signals)

    def test_two_insiders_within_window_is_cluster(self):
        txns = [
            _txn(accession_number="ACC-001", owner_name="Alice",
                 officer_title="Chief Executive Officer", transaction_date="2024-03-01"),
            _txn(accession_number="ACC-002", owner_name="Bob",
                 officer_title="Chief Financial Officer", transaction_date="2024-03-07"),
        ]
        signals = detect_clusters(txns, _cfg(cluster_window_days=7))
        assert any(s.is_cluster for s in signals)

    def test_two_insiders_outside_window_no_cluster(self):
        txns = [
            _txn(accession_number="ACC-001", owner_name="Alice",
                 officer_title="Chief Executive Officer", transaction_date="2024-03-01"),
            _txn(accession_number="ACC-002", owner_name="Bob",
                 officer_title="Chief Financial Officer", transaction_date="2024-03-10"),
        ]
        signals = detect_clusters(txns, _cfg(cluster_window_days=7))
        assert not any(s.is_cluster for s in signals)

    def test_same_insider_twice_not_a_cluster(self):
        txns = [
            _txn(accession_number="ACC-001", owner_name="Alice",
                 officer_title="Chief Executive Officer", transaction_date="2024-03-01"),
            _txn(accession_number="ACC-002", owner_name="Alice",
                 officer_title="Chief Executive Officer", transaction_date="2024-03-02"),
        ]
        signals = detect_clusters(txns, _cfg())
        assert not any(s.is_cluster for s in signals)

    def test_signals_sorted_by_value_descending(self):
        txns = [
            _txn(accession_number="ACC-001", owner_name="Alice",
                 officer_title="Chief Executive Officer", value=200_000, transaction_date="2024-03-01"),
            _txn(accession_number="ACC-002", owner_name="Bob",
                 officer_title="Chief Financial Officer", value=500_000, transaction_date="2024-03-01"),
            _txn(accession_number="ACC-003", owner_name="Carol",
                 officer_title="President", value=300_000, transaction_date="2024-03-01"),
        ]
        signals = detect_clusters(txns, _cfg())
        values = [s.transaction.value for s in signals]
        assert values == sorted(values, reverse=True)

    def test_non_qualifying_txns_excluded(self):
        # This one fails filters (sale, AD=D)
        bad = _txn(accession_number="ACC-BAD", acquired_disposed="D")
        good = _txn(accession_number="ACC-GOOD")
        signals = detect_clusters([bad, good], _cfg())
        accessions = {s.transaction.accession_number for s in signals}
        assert "ACC-GOOD" in accessions
        assert "ACC-BAD" not in accessions

    def test_different_tickers_not_clustered(self):
        txns = [
            _txn(accession_number="ACC-001", ticker="AAPL", owner_name="Alice",
                 officer_title="Chief Executive Officer", transaction_date="2024-03-01"),
            _txn(accession_number="ACC-002", ticker="MSFT", owner_name="Bob",
                 officer_title="Chief Financial Officer", transaction_date="2024-03-01"),
        ]
        signals = detect_clusters(txns, _cfg())
        assert not any(s.is_cluster for s in signals)

    def test_min_insiders_three_requires_three(self):
        txns = [
            _txn(accession_number="ACC-001", owner_name="Alice",
                 officer_title="Chief Executive Officer", transaction_date="2024-03-01"),
            _txn(accession_number="ACC-002", owner_name="Bob",
                 officer_title="Chief Financial Officer", transaction_date="2024-03-01"),
        ]
        # With min_insiders=3, two insiders should NOT form a cluster
        signals = detect_clusters(txns, _cfg(cluster_min_insiders=3))
        assert not any(s.is_cluster for s in signals)

    def test_deduplication_by_accession(self):
        # Same accession twice — should only appear once in results
        txn = _txn(accession_number="ACC-DUP")
        signals = detect_clusters([txn, txn], _cfg())
        accessions = [s.transaction.accession_number for s in signals]
        assert accessions.count("ACC-DUP") == 1
