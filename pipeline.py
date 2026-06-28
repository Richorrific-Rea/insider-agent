"""
Main pipeline orchestrator.

run_once(cfg, dry_run):
  1. Fetch EDGAR Form 4 feed + parse new filings
  2. Apply hard filters + detect insider clusters → Signals
  3. (optional) Fetch congressional trading data → PoliticianTrades
  4. Detect confluence: same ticker bought by insiders + politicians
  5. Emit ConfluenceSignals for tickers with both; plain Signals for the rest
  6. Enrich each with LLM brief, send to Telegram
  7. Persist state
"""
from __future__ import annotations

import logging
from typing import Dict, List, Set

from config import Config
from congress_client import fetch_all_politician_trades
from congress_parser import PoliticianTrade, parse_politician_trades
from edgar_client import fetch_ownership_xml, fetch_recent_form4_filings
from enrich import enrich_confluence, enrich_signal
from form4_parser import Transaction, parse_form4
from notify import send_confluence, send_signal
from signals import (
    ConfluenceSignal,
    Signal,
    detect_clusters,
    detect_confluence,
    passes_filters,
)
from state import build_state_store, dict_to_transaction, transaction_to_dict

logger = logging.getLogger(__name__)


def run_once(cfg: Config, dry_run: bool = False) -> int:
    store = build_state_store(cfg)
    seen = store.seen_accessions()

    # ── 1. EDGAR feed ─────────────────────────────────────────────────────
    logger.info("Fetching EDGAR Form 4 feed (count=%d)…", cfg.feed_count)
    try:
        feed_items = fetch_recent_form4_filings(cfg.edgar_user_agent, cfg.feed_count)
    except Exception as exc:
        logger.error("Failed to fetch EDGAR feed: %s", exc)
        return 0

    logger.info("Feed returned %d filings.", len(feed_items))

    new_items = [(acc, url) for acc, url in feed_items if acc not in seen]
    logger.info("%d new (unseen) filings to process.", len(new_items))

    new_transactions: List[Transaction] = []
    new_accessions: Set[str] = set()

    for accession, dir_url in new_items:
        new_accessions.add(accession)
        xml_text = fetch_ownership_xml(dir_url, cfg.edgar_user_agent)
        if not xml_text:
            continue
        filing_url = dir_url.rstrip("/") + "/"
        txns = parse_form4(xml_text, accession_number=accession, filing_url=filing_url)
        new_transactions.extend(txns)

    logger.info("Parsed %d transactions from new filings.", len(new_transactions))

    # ── 2. Insider signals ─────────────────────────────────────────────────
    cached_txns: List[Transaction] = [
        dict_to_transaction(d)
        for d in store.get_recent_transactions()
        if d
    ]
    all_qualifying = [
        t for t in (new_transactions + cached_txns)
        if passes_filters(t, cfg)
    ]
    new_qualifying_accessions = {
        t.accession_number for t in new_transactions if passes_filters(t, cfg)
    }

    insider_signals: List[Signal] = detect_clusters(all_qualifying, cfg)

    # Only proceed with signals that contain at least one NEW filing
    new_insider_signals = [
        s for s in insider_signals
        if s.transaction.accession_number in new_qualifying_accessions
    ]

    if not new_insider_signals:
        logger.info("No new qualifying insider signals this cycle.")
        _persist(store, new_accessions, all_qualifying)
        return 0

    # ── 3. Congressional data (optional) ──────────────────────────────────
    politician_trades: List[PoliticianTrade] = []
    if cfg.use_congress_data:
        logger.info("Fetching congressional trading data (last %d days)…", cfg.congress_days_back)
        raw = fetch_all_politician_trades(cfg.edgar_user_agent, days_back=cfg.congress_days_back)
        politician_trades = parse_politician_trades(raw)
        buys = [p for p in politician_trades if p.is_purchase]
        logger.info("Politician trades parsed: %d total, %d purchases.", len(politician_trades), len(buys))

    # ── 4. Confluence detection ────────────────────────────────────────────
    confluence_signals: List[ConfluenceSignal] = detect_confluence(
        new_insider_signals, politician_trades, cfg
    )

    # Tickers covered by a confluence signal
    confluence_tickers: Set[str] = {c.ticker for c in confluence_signals}

    # ── 5. Emit signals ────────────────────────────────────────────────────
    notified = 0

    # Confluence signals first (highest value)
    for csig in confluence_signals:
        brief = enrich_confluence(csig, cfg)
        send_confluence(
            csig=csig,
            brief=brief,
            bot_token=cfg.telegram_bot_token,
            chat_id=cfg.telegram_chat_id,
            dry_run=dry_run,
        )
        notified += 1
        logger.info(
            "Confluence signal: %s  confidence=%s  insiders=%d  politicians=%d  $%.0f",
            csig.ticker, csig.confidence,
            csig.distinct_insiders, csig.distinct_politicians,
            csig.total_insider_value,
        )

    # Plain insider signals for tickers NOT already covered by a confluence signal
    for sig in new_insider_signals:
        if sig.transaction.ticker in confluence_tickers:
            continue  # already sent as a richer confluence message
        brief = enrich_signal(sig, cfg)
        send_signal(
            signal=sig,
            brief=brief,
            bot_token=cfg.telegram_bot_token,
            chat_id=cfg.telegram_chat_id,
            dry_run=dry_run,
        )
        notified += 1
        logger.info(
            "Insider signal: %s  %s  $%.0f  cluster=%s",
            sig.transaction.ticker,
            sig.transaction.owner_name,
            sig.transaction.value,
            sig.is_cluster,
        )

    logger.info("Notified %d signal(s) this cycle.", notified)

    # ── 6. Persist state ───────────────────────────────────────────────────
    _persist(store, new_accessions, all_qualifying)
    return notified


def _persist(store, new_accessions: Set[str], qualifying: List[Transaction]) -> None:
    store.add_accessions(new_accessions)
    store.merge_transactions([transaction_to_dict(t) for t in qualifying])
    store.save()
