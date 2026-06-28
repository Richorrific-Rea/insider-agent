"""
Full pipeline orchestrator — all signal sources → scorer → enrich → notify.

run_once(cfg, dry_run):
  1. EDGAR Form 4      → insider signals
  2. Congressional PTR → politician trades
  3. EDGAR 13D/13G     → activist filings
  4. EDGAR 13F         → institutional positions  (for tickers already hot)
  5. Yahoo Finance     → short interest           (for tickers already hot)
  6. Yahoo Options     → unusual options          (for tickers already hot)
  7. Score each ticker  → TierScore
  8. Enrich + notify   → Telegram (broker personality scales with tier)
  9. Persist state
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Dict, List, Set

from config import Config
from congress_client import fetch_all_politician_trades
from congress_parser import PoliticianTrade, parse_politician_trades
from edgar_client import fetch_ownership_xml, fetch_recent_form4_filings
from enrich import enrich_signal, enrich_tier_score
from finra_client import fetch_short_interest_batch
from form4_parser import Transaction, parse_form4
from notify import send_signal, send_tier_score
from options_client import fetch_unusual_options_batch
from scorer import TierScore, score_ticker
from sec_extra_client import (
    ActivistFiling,
    InstitutionalPosition,
    fetch_activist_filings,
    fetch_institutional_positions,
)
from signals import Signal, detect_clusters, passes_filters
from state import build_state_store, dict_to_transaction, transaction_to_dict

logger = logging.getLogger(__name__)


def run_once(cfg: Config, dry_run: bool = False) -> int:
    store = build_state_store(cfg)
    seen = store.seen_accessions()

    # ── 1. EDGAR Form 4 ───────────────────────────────────────────────────
    logger.info("Fetching EDGAR Form 4 feed (count=%d)…", cfg.feed_count)
    try:
        feed_items = fetch_recent_form4_filings(cfg.edgar_user_agent, cfg.feed_count)
    except Exception as exc:
        logger.error("EDGAR feed failed: %s", exc)
        return 0

    logger.info("Feed: %d filings, %d new.", len(feed_items),
                sum(1 for a, _ in feed_items if a not in seen))

    new_transactions: List[Transaction] = []
    new_accessions: Set[str] = set()
    for accession, dir_url in feed_items:
        if accession in seen:
            continue
        new_accessions.add(accession)
        xml = fetch_ownership_xml(dir_url, cfg.edgar_user_agent)
        if xml:
            txns = parse_form4(xml, accession_number=accession,
                               filing_url=dir_url.rstrip("/") + "/")
            new_transactions.extend(txns)

    logger.info("Parsed %d transactions from %d new filings.",
                len(new_transactions), len(new_accessions))

    # ── 2. Insider signals ─────────────────────────────────────────────────
    cached_txns = [dict_to_transaction(d) for d in store.get_recent_transactions() if d]
    all_qualifying = [t for t in (new_transactions + cached_txns) if passes_filters(t, cfg)]
    new_qualifying_acc = {t.accession_number for t in new_transactions if passes_filters(t, cfg)}

    insider_signals: List[Signal] = detect_clusters(all_qualifying, cfg)
    new_insider_signals = [s for s in insider_signals
                           if s.transaction.accession_number in new_qualifying_acc]

    if not new_insider_signals:
        logger.info("No new qualifying insider signals this cycle.")
        _persist(store, new_accessions, all_qualifying)
        return 0

    hot_tickers: Set[str] = {s.transaction.ticker for s in new_insider_signals if s.transaction.ticker}
    logger.info("Hot tickers: %s", sorted(hot_tickers))

    # ── 3. Congressional trades ────────────────────────────────────────────
    politician_trades: List[PoliticianTrade] = []
    if cfg.use_congress_data:
        logger.info("Fetching congressional trades…")
        raw = fetch_all_politician_trades(cfg.edgar_user_agent, days_back=cfg.congress_days_back)
        politician_trades = [p for p in parse_politician_trades(raw) if p.is_purchase]
        logger.info("Politician purchases: %d", len(politician_trades))

    # ── 4. Activists 13D/13G ──────────────────────────────────────────────
    activist_filings: List[ActivistFiling] = []
    try:
        logger.info("Fetching activist filings (13D/13G)…")
        all_activists = fetch_activist_filings(cfg.edgar_user_agent, count=40)
        activist_filings = [a for a in all_activists if a.ticker in hot_tickers]
        logger.info("Activists for hot tickers: %d", len(activist_filings))
    except Exception as exc:
        logger.warning("Activist fetch failed: %s", exc)

    # ── 5. Institutional 13F ──────────────────────────────────────────────
    institutional: List[InstitutionalPosition] = []
    try:
        logger.info("Fetching institutional positions (13F)…")
        institutional = fetch_institutional_positions(
            cfg.edgar_user_agent, tickers_of_interest=list(hot_tickers), count=20
        )
        logger.info("Institutional positions found: %d", len(institutional))
    except Exception as exc:
        logger.warning("13F fetch failed: %s", exc)

    # ── 6. Short interest + Unusual options (targeted) ────────────────────
    short_data = {}
    options_data = {}
    if hot_tickers:
        logger.info("Fetching short interest and options for %d tickers…", len(hot_tickers))
        try:
            short_data = fetch_short_interest_batch(list(hot_tickers))
        except Exception as exc:
            logger.warning("Short interest fetch failed: %s", exc)
        try:
            options_data = fetch_unusual_options_batch(list(hot_tickers))
        except Exception as exc:
            logger.warning("Options fetch failed: %s", exc)

    # ── 7. Score each ticker ───────────────────────────────────────────────
    # Group all signal types by ticker
    sig_by_ticker: Dict[str, List[Signal]] = defaultdict(list)
    for s in new_insider_signals:
        sig_by_ticker[s.transaction.ticker].append(s)

    pol_by_ticker: Dict[str, List[PoliticianTrade]] = defaultdict(list)
    for p in politician_trades:
        pol_by_ticker[p.ticker].append(p)

    act_by_ticker: Dict[str, List[ActivistFiling]] = defaultdict(list)
    for a in activist_filings:
        act_by_ticker[a.ticker].append(a)

    inst_by_ticker: Dict[str, List[InstitutionalPosition]] = defaultdict(list)
    for ip in institutional:
        inst_by_ticker[ip.ticker].append(ip)

    scored: List[TierScore] = []
    for ticker in hot_tickers:
        sigs = sig_by_ticker.get(ticker, [])
        if not sigs:
            continue
        issuer = sigs[0].transaction.issuer_name
        ts = score_ticker(
            ticker=ticker,
            issuer_name=issuer,
            insider_signals=sigs,
            politician_trades=pol_by_ticker.get(ticker, []),
            activist_filings=act_by_ticker.get(ticker, []),
            institutional_positions=inst_by_ticker.get(ticker, []),
            short_interest=short_data.get(ticker),
            unusual_options=options_data.get(ticker, []),
        )
        scored.append(ts)

    # Sort: highest score first
    scored.sort(key=lambda s: s.total_score, reverse=True)

    # ── 8. Enrich + notify ─────────────────────────────────────────────────
    notified = 0
    for ts in scored:
        brief = enrich_tier_score(ts, cfg)
        send_tier_score(
            ts=ts,
            brief=brief,
            bot_token=cfg.telegram_bot_token,
            chat_id=cfg.telegram_chat_id,
            dry_run=dry_run,
        )
        notified += 1
        logger.info(
            "Sent: %s | tier=%s | score=%.0f | sources=%s",
            ts.ticker, ts.tier, ts.total_score,
            "+".join(ts.active_source_types),
        )

    logger.info("Total signals sent: %d", notified)

    # ── 9. Persist ─────────────────────────────────────────────────────────
    _persist(store, new_accessions, all_qualifying)
    return notified


def _persist(store, new_accessions: Set[str], qualifying: List[Transaction]) -> None:
    store.add_accessions(new_accessions)
    store.merge_transactions([transaction_to_dict(t) for t in qualifying])
    store.save()
