"""
Main pipeline orchestrator.

run_once(cfg, dry_run):
  1. Fetch Form 4 feed from EDGAR
  2. Filter out already-seen accessions
  3. Download & parse new filings
  4. Apply hard filters
  5. Merge with cached recent transactions for cluster detection
  6. Detect clusters
  7. Enrich and notify only signals containing at least one NEW purchase
  8. Persist state
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from typing import List, Set

from config import Config
from edgar_client import fetch_ownership_xml, fetch_recent_form4_filings
from enrich import enrich_signal
from form4_parser import Transaction, parse_form4
from notify import send_signal
from signals import Signal, detect_clusters, passes_filters
from state import build_state_store, dict_to_transaction, transaction_to_dict

logger = logging.getLogger(__name__)


def run_once(cfg: Config, dry_run: bool = False) -> int:
    """
    Execute one full poll cycle.
    Returns the number of signals notified.
    """
    store = build_state_store(cfg)
    seen = store.seen_accessions()

    # ── 1. Fetch feed ─────────────────────────────────────────────────────
    logger.info("Fetching EDGAR Form 4 feed (count=%d)…", cfg.feed_count)
    try:
        feed_items = fetch_recent_form4_filings(cfg.edgar_user_agent, cfg.feed_count)
    except Exception as exc:
        logger.error("Failed to fetch EDGAR feed: %s", exc)
        return 0

    logger.info("Feed returned %d filings.", len(feed_items))

    # ── 2. Filter already-seen accessions ─────────────────────────────────
    new_items = [(acc, url) for acc, url in feed_items if acc not in seen]
    logger.info("%d new (unseen) filings to process.", len(new_items))

    # ── 3. Download & parse new filings ───────────────────────────────────
    new_transactions: List[Transaction] = []
    new_accessions: Set[str] = set()

    for accession, dir_url in new_items:
        new_accessions.add(accession)
        xml_text = fetch_ownership_xml(dir_url, cfg.edgar_user_agent)
        if not xml_text:
            logger.debug("No XML for accession %s", accession)
            continue

        # Derive a filing URL from the directory URL (link to index page)
        filing_url = dir_url.rstrip("/") + "/"
        txns = parse_form4(xml_text, accession_number=accession, filing_url=filing_url)
        new_transactions.extend(txns)

    logger.info("Parsed %d transactions from new filings.", len(new_transactions))

    # ── 4. Load cached recent transactions for cluster context ────────────
    cached_dicts = store.get_recent_transactions()
    cached_txns: List[Transaction] = []
    for d in cached_dicts:
        try:
            cached_txns.append(dict_to_transaction(d))
        except Exception:
            pass

    # ── 5. Merge and identify which qualify ───────────────────────────────
    all_qualifying = [t for t in (new_transactions + cached_txns) if passes_filters(t, cfg)]
    new_qualifying_accessions = {
        t.accession_number for t in new_transactions if passes_filters(t, cfg)
    }

    # ── 6. Detect clusters ────────────────────────────────────────────────
    signals: List[Signal] = detect_clusters(all_qualifying, cfg)

    # ── 7. Enrich and notify signals with at least one NEW purchase ────────
    notified = 0
    for signal in signals:
        # Only emit if the signal itself is from a new filing
        if signal.transaction.accession_number not in new_qualifying_accessions:
            continue

        brief = enrich_signal(signal, cfg)
        send_signal(
            signal=signal,
            brief=brief,
            webhook_url=cfg.slack_webhook_url,
            dry_run=dry_run,
        )
        notified += 1
        logger.info(
            "Signal notified: %s %s $%.0f cluster=%s",
            signal.transaction.ticker,
            signal.transaction.owner_name,
            signal.transaction.value,
            signal.is_cluster,
        )

    logger.info("Notified %d signal(s) this cycle.", notified)

    # ── 8. Persist state ──────────────────────────────────────────────────
    store.add_accessions(new_accessions)
    qualifying_dicts = [transaction_to_dict(t) for t in all_qualifying]
    store.merge_transactions(qualifying_dicts)
    store.save()

    return notified
