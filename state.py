"""
State management — deduplication of accession numbers and caching of recent
purchases for cluster detection across poll cycles.

Interface: StateStore
Implementations:
  - FileStateStore  (default, JSON file)
  - FirestoreStateStore
  - GCSStateStore

Selected via STATE_BACKEND env var: "file" | "firestore" | "gcs"
"""
from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Dict, List, Set

if TYPE_CHECKING:
    from config import Config
    from form4_parser import Transaction

logger = logging.getLogger(__name__)

# ── Interface ─────────────────────────────────────────────────────────────────


class StateStore(ABC):
    @abstractmethod
    def seen_accessions(self) -> Set[str]:
        """Return the set of already-processed accession numbers."""

    @abstractmethod
    def add_accessions(self, accessions: Set[str]) -> None:
        """Persist newly processed accession numbers."""

    @abstractmethod
    def get_recent_transactions(self) -> List[Dict]:
        """Return cached transaction dicts from previous poll cycles."""

    @abstractmethod
    def merge_transactions(self, transactions: List[Dict]) -> None:
        """Persist new transactions (merge, don't replace)."""

    @abstractmethod
    def save(self) -> None:
        """Flush any in-memory state to backing store."""

    def get_news_tickers(self) -> Dict[str, str]:
        """Return {ticker: expiry_date_iso} for news-triggered tickers."""
        return {}

    def add_news_tickers(self, tickers: Dict[str, str], ttl_days: int = 5) -> None:
        """Add tickers from news with expiry = today + ttl_days."""

    def expire_news_tickers(self) -> None:
        """Remove tickers whose TTL has passed."""


# ── File implementation ───────────────────────────────────────────────────────


class FileStateStore(StateStore):
    def __init__(self, path: str = "state.json"):
        self._path = path
        self._state: Dict = {
            "seen_accessions": [],
            "recent_transactions": [],
            "news_tickers": {},      # {ticker: expiry_date_iso}
        }
        self._load()

    def _load(self) -> None:
        if os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    self._state = json.load(f)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Could not load state from %s: %s", self._path, exc)

    def seen_accessions(self) -> Set[str]:
        return set(self._state.get("seen_accessions", []))

    def add_accessions(self, accessions: Set[str]) -> None:
        existing = self.seen_accessions()
        merged = existing | accessions
        self._state["seen_accessions"] = sorted(merged)

    def get_recent_transactions(self) -> List[Dict]:
        return self._state.get("recent_transactions", [])

    def merge_transactions(self, transactions: List[Dict]) -> None:
        existing = {t["accession_number"]: t for t in self.get_recent_transactions()}
        for txn in transactions:
            existing[txn["accession_number"]] = txn
        self._state["recent_transactions"] = list(existing.values())

    # ── News ticker cache ──────────────────────────────────────────────────

    def get_news_tickers(self) -> Dict[str, str]:
        return dict(self._state.get("news_tickers", {}))

    def add_news_tickers(self, tickers: Dict[str, str], ttl_days: int = 5) -> None:
        from datetime import date, timedelta
        expiry = (date.today() + timedelta(days=ttl_days)).isoformat()
        existing = self.get_news_tickers()
        for ticker in tickers:
            existing[ticker.upper()] = expiry
        self._state["news_tickers"] = existing

    def expire_news_tickers(self) -> None:
        from datetime import date
        today = date.today().isoformat()
        current = self.get_news_tickers()
        self._state["news_tickers"] = {
            t: exp for t, exp in current.items() if exp >= today
        }

    def save(self) -> None:
        tmp = self._path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._state, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self._path)
        except OSError as exc:
            logger.error("Failed to save state to %s: %s", self._path, exc)


# ── Firestore implementation ──────────────────────────────────────────────────


class FirestoreStateStore(StateStore):
    """
    Stores state in a single Firestore document.
    Requires: google-cloud-firestore installed and ADC configured.
    """

    def __init__(self, project: str, collection: str = "insider_agent_state"):
        try:
            from google.cloud import firestore as fs  # type: ignore
        except ImportError as exc:
            raise ImportError(
                "google-cloud-firestore is required for FirestoreStateStore. "
                "Install it with: pip install google-cloud-firestore"
            ) from exc

        self._db = fs.Client(project=project)
        self._doc_ref = self._db.collection(collection).document("state")
        self._state: Dict = {"seen_accessions": [], "recent_transactions": []}
        self._load()

    def _load(self) -> None:
        try:
            doc = self._doc_ref.get()
            if doc.exists:
                self._state = doc.to_dict() or self._state
        except Exception as exc:
            logger.warning("Could not load Firestore state: %s", exc)

    def seen_accessions(self) -> Set[str]:
        return set(self._state.get("seen_accessions", []))

    def add_accessions(self, accessions: Set[str]) -> None:
        existing = self.seen_accessions()
        self._state["seen_accessions"] = sorted(existing | accessions)

    def get_recent_transactions(self) -> List[Dict]:
        return self._state.get("recent_transactions", [])

    def merge_transactions(self, transactions: List[Dict]) -> None:
        existing = {t["accession_number"]: t for t in self.get_recent_transactions()}
        for txn in transactions:
            existing[txn["accession_number"]] = txn
        self._state["recent_transactions"] = list(existing.values())

    def save(self) -> None:
        try:
            self._doc_ref.set(self._state)
        except Exception as exc:
            logger.error("Failed to save Firestore state: %s", exc)


# ── GCS implementation ────────────────────────────────────────────────────────


class GCSStateStore(StateStore):
    """
    Stores state as a JSON object in Google Cloud Storage.
    Requires: google-cloud-storage installed and ADC configured.
    """

    def __init__(self, bucket: str, object_name: str = "insider_agent_state.json"):
        try:
            from google.cloud import storage as gcs  # type: ignore
        except ImportError as exc:
            raise ImportError(
                "google-cloud-storage is required for GCSStateStore. "
                "Install it with: pip install google-cloud-storage"
            ) from exc

        self._client = gcs.Client()
        self._bucket_name = bucket
        self._object_name = object_name
        self._state: Dict = {"seen_accessions": [], "recent_transactions": []}
        self._load()

    def _load(self) -> None:
        try:
            bucket = self._client.bucket(self._bucket_name)
            blob = bucket.blob(self._object_name)
            if blob.exists():
                self._state = json.loads(blob.download_as_text())
        except Exception as exc:
            logger.warning("Could not load GCS state: %s", exc)

    def seen_accessions(self) -> Set[str]:
        return set(self._state.get("seen_accessions", []))

    def add_accessions(self, accessions: Set[str]) -> None:
        existing = self.seen_accessions()
        self._state["seen_accessions"] = sorted(existing | accessions)

    def get_recent_transactions(self) -> List[Dict]:
        return self._state.get("recent_transactions", [])

    def merge_transactions(self, transactions: List[Dict]) -> None:
        existing = {t["accession_number"]: t for t in self.get_recent_transactions()}
        for txn in transactions:
            existing[txn["accession_number"]] = txn
        self._state["recent_transactions"] = list(existing.values())

    def save(self) -> None:
        try:
            bucket = self._client.bucket(self._bucket_name)
            blob = bucket.blob(self._object_name)
            blob.upload_from_string(
                json.dumps(self._state, indent=2, ensure_ascii=False),
                content_type="application/json",
            )
        except Exception as exc:
            logger.error("Failed to save GCS state: %s", exc)


# ── Factory ───────────────────────────────────────────────────────────────────


def build_state_store(cfg: "Config") -> StateStore:
    backend = cfg.state_backend.lower()
    if backend == "firestore":
        return FirestoreStateStore(
            project=cfg.gcp_project,
            collection=cfg.firestore_collection,
        )
    if backend == "gcs":
        return GCSStateStore(
            bucket=cfg.gcs_bucket,
            object_name=cfg.gcs_object,
        )
    return FileStateStore(path=cfg.state_file_path)


# ── Helpers to convert Transaction <-> dict ───────────────────────────────────


def transaction_to_dict(txn: "Transaction") -> Dict:
    from dataclasses import asdict
    return asdict(txn)


def dict_to_transaction(d: Dict) -> "Transaction":
    from form4_parser import Transaction
    return Transaction(**{k: v for k, v in d.items() if k in Transaction.__dataclass_fields__})
