"""Tests for state.py — FileStateStore only (no GCP credentials needed)."""
import sys, os, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from state import FileStateStore, build_state_store
from config import Config


def _cfg(**overrides) -> Config:
    defaults = dict(edgar_user_agent="Test test@test.com", state_backend="file")
    defaults.update(overrides)
    return Config(**defaults)


class TestFileStateStore:
    def test_initial_seen_accessions_empty(self, tmp_path):
        store = FileStateStore(str(tmp_path / "state.json"))
        assert store.seen_accessions() == set()

    def test_add_and_retrieve_accessions(self, tmp_path):
        store = FileStateStore(str(tmp_path / "state.json"))
        store.add_accessions({"ACC-001", "ACC-002"})
        assert store.seen_accessions() == {"ACC-001", "ACC-002"}

    def test_accessions_persist_after_save_and_reload(self, tmp_path):
        path = str(tmp_path / "state.json")
        store = FileStateStore(path)
        store.add_accessions({"ACC-001"})
        store.save()

        store2 = FileStateStore(path)
        assert "ACC-001" in store2.seen_accessions()

    def test_add_accessions_merges(self, tmp_path):
        store = FileStateStore(str(tmp_path / "state.json"))
        store.add_accessions({"ACC-001"})
        store.add_accessions({"ACC-002"})
        assert store.seen_accessions() == {"ACC-001", "ACC-002"}

    def test_initial_recent_transactions_empty(self, tmp_path):
        store = FileStateStore(str(tmp_path / "state.json"))
        assert store.get_recent_transactions() == []

    def test_merge_transactions_stored(self, tmp_path):
        store = FileStateStore(str(tmp_path / "state.json"))
        txn = {"accession_number": "ACC-001", "ticker": "AAPL", "value": 100_000}
        store.merge_transactions([txn])
        result = store.get_recent_transactions()
        assert len(result) == 1
        assert result[0]["ticker"] == "AAPL"

    def test_merge_transactions_deduplicates_by_accession(self, tmp_path):
        store = FileStateStore(str(tmp_path / "state.json"))
        txn_v1 = {"accession_number": "ACC-001", "ticker": "AAPL", "value": 100_000}
        txn_v2 = {"accession_number": "ACC-001", "ticker": "AAPL", "value": 200_000}
        store.merge_transactions([txn_v1])
        store.merge_transactions([txn_v2])
        result = store.get_recent_transactions()
        assert len(result) == 1
        assert result[0]["value"] == 200_000

    def test_transactions_persist_after_save_and_reload(self, tmp_path):
        path = str(tmp_path / "state.json")
        store = FileStateStore(path)
        store.merge_transactions([{"accession_number": "ACC-001", "ticker": "GOOG"}])
        store.save()

        store2 = FileStateStore(path)
        txns = store2.get_recent_transactions()
        assert len(txns) == 1
        assert txns[0]["ticker"] == "GOOG"

    def test_save_is_atomic_tmp_file(self, tmp_path):
        path = str(tmp_path / "state.json")
        store = FileStateStore(path)
        store.add_accessions({"ACC-001"})
        store.save()
        assert os.path.exists(path)
        assert not os.path.exists(path + ".tmp")

    def test_corrupted_file_recovers_gracefully(self, tmp_path):
        path = str(tmp_path / "state.json")
        with open(path, "w") as f:
            f.write("NOT VALID JSON{{{")
        store = FileStateStore(path)
        assert store.seen_accessions() == set()

    def test_build_state_store_returns_file_store(self, tmp_path):
        cfg = _cfg(state_backend="file", state_file_path=str(tmp_path / "s.json"))
        store = build_state_store(cfg)
        assert isinstance(store, FileStateStore)
