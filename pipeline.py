"""
Full pipeline orchestrator — any signal can trigger an alert.

Ticker universe (scanned every cycle):
  A. Form 4 new filings      → insider purchase signals
  B. Portfolio positions      → exit signals + price
  C. Watchlist               → price spikes/drops
  D. News-triggered          → tickers mentioned in major financial news (5-day TTL)
  E. Recent Form 4 (30d)     → follow-up on past signals

For tickers in B/C/D/E without a new Form 4:
  Short interest + unusual options + price → scored independently.
  Score ≥ STANDALONE_MIN_SCORE fires an alert even without insider filing.

run_once(cfg, dry_run):
  1.  Fetch + parse EDGAR Form 4 → insider signals
  2.  Fetch congressional trades
  3.  Fetch 13D/13G/13F
  4.  Fetch financial news → extract tickers → add to news cache (5d TTL)
  5.  Build full ticker universe (A+B+C+D+E)
  6.  Fetch short interest + options + prices for full universe
  7.  Score each ticker — Form 4 present OR standalone market signals
  8.  Emit alerts for score ≥ threshold
  9.  Check portfolio exit signals
  10. Check watchlist + news price moves
  11. Persist state
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Dict, List, Optional, Set

from config import Config
from congress_client import fetch_all_politician_trades
from congress_parser import PoliticianTrade, parse_politician_trades
from edgar_client import fetch_ownership_xml, fetch_recent_form4_filings
from enrich import enrich_exit, enrich_signal, enrich_tier_score
from exit_signals import ExitTierScore, detect_insider_sells, score_exit
from finra_client import fetch_short_interest_batch
from form4_parser import Transaction, parse_form4
from news_client import extract_tickers_from_headlines, fetch_headlines
from notify import (send_exit_alert, send_price_only_alert, send_signal,
                    send_tier_score, send_watchlist_alert,
                    send_portfolio_drop_alert, send_watchlist_drop_alert)
from options_client import fetch_unusual_options_batch
from portfolio import PortfolioStore
from price_client import PriceSnapshot, fetch_prices
from scorer import TierScore, score_ticker
from sec_extra_client import (
    ActivistFiling, InstitutionalPosition,
    fetch_activist_filings, fetch_institutional_positions,
)
from signals import Signal, detect_clusters, passes_filters
from state import build_state_store, dict_to_transaction, transaction_to_dict

logger = logging.getLogger(__name__)

# Minimum score for standalone alert (no Form 4 anchor)
STANDALONE_MIN_SCORE = 40.0   # MEDIA tier


def run_once(cfg: Config, dry_run: bool = False) -> int:
    store = build_state_store(cfg)
    seen  = store.seen_accessions()

    # ── 1. EDGAR Form 4 ───────────────────────────────────────────────────
    logger.info("Fetching EDGAR Form 4 feed…")
    try:
        feed_items = fetch_recent_form4_filings(cfg.edgar_user_agent, cfg.feed_count)
    except Exception as exc:
        logger.error("EDGAR feed failed: %s", exc)
        feed_items = []

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

    logger.info("Form 4: %d new filings → %d transactions.",
                len(new_accessions), len(new_transactions))

    cached_txns = [dict_to_transaction(d) for d in store.get_recent_transactions() if d]
    all_qualifying = [t for t in (new_transactions + cached_txns) if passes_filters(t, cfg)]
    new_qualifying_acc = {t.accession_number for t in new_transactions if passes_filters(t, cfg)}

    insider_signals: List[Signal] = detect_clusters(all_qualifying, cfg)
    new_insider_signals = [s for s in insider_signals
                           if s.transaction.accession_number in new_qualifying_acc]

    # Tickers from new Form 4 signals
    form4_tickers: Set[str] = {s.transaction.ticker for s in new_insider_signals if s.transaction.ticker}

    # ── 2. Congressional ──────────────────────────────────────────────────
    politician_trades: List[PoliticianTrade] = []
    if cfg.use_congress_data:
        logger.info("Fetching congressional trades…")
        try:
            raw = fetch_all_politician_trades(cfg.edgar_user_agent, days_back=cfg.congress_days_back)
            politician_trades = [p for p in parse_politician_trades(raw) if p.is_purchase]
        except Exception as exc:
            logger.warning("Congressional fetch failed: %s", exc)

    # ── 3. Activists + Institutional ──────────────────────────────────────
    activist_filings: List[ActivistFiling] = []
    institutional: List[InstitutionalPosition] = []
    try:
        all_activists = fetch_activist_filings(cfg.edgar_user_agent, count=40)
        activist_filings = all_activists   # score_ticker filters by ticker
    except Exception as exc:
        logger.warning("Activist fetch failed: %s", exc)

    # ── 4. News → tickers with TTL ────────────────────────────────────────
    news_tickers: Set[str] = set()
    if getattr(cfg, "use_news_triggers", True):
        logger.info("Fetching financial news for ticker triggers…")
        try:
            store.expire_news_tickers()
            headlines = fetch_headlines()
            extracted = extract_tickers_from_headlines(headlines, cfg)
            if extracted:
                store.add_news_tickers(extracted, ttl_days=cfg.news_ticker_ttl_days)
                logger.info("News: added %d tickers to scan window.", len(extracted))
        except Exception as exc:
            logger.warning("News fetch failed: %s", exc)

        news_tickers = set(store.get_news_tickers().keys())
        logger.info("Active news tickers: %d", len(news_tickers))

    # ── 5. Full ticker universe ───────────────────────────────────────────
    _pstore = PortfolioStore(path=cfg.state_file_path)
    portfolio_tickers  = {p.ticker for p in _pstore.get_positions()}
    watchlist_tickers  = set(_pstore.get_watchlist())

    # Recent Form 4 tickers (last 30 days from cached transactions)
    recent_form4_tickers: Set[str] = {
        t.ticker for t in cached_txns
        if t.ticker and passes_filters(t, cfg)
    }

    full_universe = (
        form4_tickers
        | portfolio_tickers
        | watchlist_tickers
        | news_tickers
        | recent_form4_tickers
    )
    logger.info(
        "Ticker universe: %d total (Form4=%d, portfolio=%d, watchlist=%d, news=%d, recent=%d)",
        len(full_universe), len(form4_tickers), len(portfolio_tickers),
        len(watchlist_tickers), len(news_tickers), len(recent_form4_tickers),
    )

    if not full_universe:
        logger.info("No tickers to scan this cycle.")
        _persist(store, new_accessions, all_qualifying)
        return 0

    # ── 6. Market data for full universe ──────────────────────────────────
    short_data:   Dict = {}
    options_data: Dict = {}
    price_data:   Dict = {}

    try:
        short_data = fetch_short_interest_batch(list(full_universe))
    except Exception as exc:
        logger.warning("Short interest fetch failed: %s", exc)
    try:
        options_data = fetch_unusual_options_batch(list(full_universe))
    except Exception as exc:
        logger.warning("Options fetch failed: %s", exc)
    try:
        price_data = fetch_prices(list(full_universe))
        spiking = [t for t, ps in price_data.items() if ps and ps.is_spiking]
        dropping = [t for t, ps in price_data.items() if ps and ps.is_dropping(cfg.price_drop_pct)]
        if spiking:  logger.info("Price spikes: %s", sorted(spiking))
        if dropping: logger.info("Price drops: %s", sorted(dropping))
    except Exception as exc:
        logger.warning("Price fetch failed: %s", exc)

    # Institutional positions for Form 4 tickers only (expensive to fetch broadly)
    try:
        institutional = fetch_institutional_positions(
            cfg.edgar_user_agent,
            tickers_of_interest=list(form4_tickers),
            count=20,
        ) if form4_tickers else []
    except Exception as exc:
        logger.warning("13F fetch failed: %s", exc)

    # ── 7. Score every ticker in universe ─────────────────────────────────
    sig_by_ticker:  Dict[str, List[Signal]] = defaultdict(list)
    pol_by_ticker:  Dict[str, List]         = defaultdict(list)
    act_by_ticker:  Dict[str, List]         = defaultdict(list)
    inst_by_ticker: Dict[str, List]         = defaultdict(list)

    for s in new_insider_signals:
        sig_by_ticker[s.transaction.ticker].append(s)
    for p in politician_trades:
        pol_by_ticker[p.ticker].append(p)
    for a in activist_filings:
        act_by_ticker[a.ticker].append(a)
    for ip in institutional:
        inst_by_ticker[ip.ticker].append(ip)

    scored: List[TierScore] = []
    for ticker in full_universe:
        sigs     = sig_by_ticker.get(ticker, [])
        pols     = pol_by_ticker.get(ticker, [])
        acts     = act_by_ticker.get(ticker, [])
        insts    = inst_by_ticker.get(ticker, [])
        si       = short_data.get(ticker)
        opts     = options_data.get(ticker, [])
        ps       = price_data.get(ticker)

        # Skip if literally nothing to score
        has_signal = any([
            sigs, pols, acts, insts,
            (si and si.decline_pct >= 10),
            opts,
            (ps and ps.is_spiking),
        ])
        if not has_signal:
            continue

        issuer = sigs[0].transaction.issuer_name if sigs else ticker
        ts = score_ticker(
            ticker=ticker,
            issuer_name=issuer,
            insider_signals=sigs,
            politician_trades=pols,
            activist_filings=acts,
            institutional_positions=insts,
            short_interest=si,
            unusual_options=opts,
            price_snapshot=ps,
        )

        # Only include if:
        #   a) there's a NEW Form 4 signal, OR
        #   b) score is high enough for standalone alert
        if ticker in form4_tickers or ts.total_score >= STANDALONE_MIN_SCORE:
            scored.append(ts)

    scored.sort(key=lambda s: s.total_score, reverse=True)
    logger.info("Scored tickers: %d (threshold ≥ %.0f for standalone).",
                len(scored), STANDALONE_MIN_SCORE)

    # ── 8. Emit entry alerts ──────────────────────────────────────────────
    notified        = 0
    alerted_tickers: Set[str] = set()

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
        alerted_tickers.add(ts.ticker)
        origin = "Form4" if ts.ticker in form4_tickers else "standalone"
        logger.info(
            "Signal [%s]: %s | tier=%s | score=%.0f | sources=%s",
            origin, ts.ticker, ts.tier, ts.total_score,
            "+".join(ts.active_source_types),
        )

    # ── 9. Portfolio exit surveillance ────────────────────────────────────
    positions = _pstore.get_positions()
    price_alerted: Set[str] = set(alerted_tickers)

    if positions:
        all_recent_txns = new_transactions + cached_txns
        pol_sells: Dict[str, list] = defaultdict(list)
        for p in politician_trades:
            if p.ticker in portfolio_tickers and not p.is_purchase:
                pol_sells[p.ticker].append(p)

        for position in positions:
            ticker = position.ticker
            insider_sells = detect_insider_sells(all_recent_txns, ticker)
            ps = price_data.get(ticker)

            exit_score = score_exit(
                ticker=ticker,
                issuer_name=position.notes or ticker,
                insider_sells=insider_sells,
                politician_sells=pol_sells.get(ticker, []),
                activist_reductions=[a for a in activist_filings if a.ticker == ticker],
                institutional_reductions=[],
                short_interest=short_data.get(ticker),
                unusual_puts=[o for o in options_data.get(ticker, []) if o.option_type == "PUT"],
            )

            if exit_score.should_alert and ticker not in alerted_tickers:
                brief = enrich_exit(exit_score, cfg)
                send_exit_alert(exit_score, position, brief,
                                cfg.telegram_bot_token, cfg.telegram_chat_id, dry_run)
                notified += 1
                price_alerted.add(ticker)
                logger.info("EXIT alert: %s tier=%s score=%.0f", ticker, exit_score.tier, exit_score.total_score)

            if ps and ticker not in price_alerted:
                if ps.is_spiking:
                    send_price_only_alert(ps, position, cfg.telegram_bot_token, cfg.telegram_chat_id, dry_run)
                    notified += 1
                    price_alerted.add(ticker)
                elif ps.is_dropping(cfg.price_drop_pct):
                    send_portfolio_drop_alert(ps, position, cfg.telegram_bot_token, cfg.telegram_chat_id, dry_run)
                    notified += 1
                    price_alerted.add(ticker)

    # ── 10. Watchlist + news price moves ──────────────────────────────────
    scan_for_price = (watchlist_tickers | news_tickers) - price_alerted

    for ticker in scan_for_price:
        ps = price_data.get(ticker)
        if not ps:
            continue
        if ticker in price_alerted:
            continue

        if ps.is_moving(cfg.watchlist_spike_pct):
            send_watchlist_alert(ps, cfg.telegram_bot_token, cfg.telegram_chat_id, dry_run)
            notified += 1
            price_alerted.add(ticker)
            logger.info("Price spike [watchlist/news]: %s +%.1f%%", ticker, ps.pct_change_vs_close)
        elif ps.is_dropping(cfg.watchlist_drop_pct):
            send_watchlist_drop_alert(ps, cfg.telegram_bot_token, cfg.telegram_chat_id, dry_run)
            notified += 1
            price_alerted.add(ticker)
            logger.info("Price drop [watchlist/news]: %s %.1f%%", ticker, ps.pct_change_vs_close)

    logger.info("Total alerts this cycle: %d", notified)

    # ── 11. Persist ───────────────────────────────────────────────────────
    _persist(store, new_accessions, all_qualifying)
    return notified


def _persist(store, new_accessions: Set[str], qualifying: List[Transaction]) -> None:
    store.add_accessions(new_accessions)
    store.merge_transactions([transaction_to_dict(t) for t in qualifying])
    store.save()
