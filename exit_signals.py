"""
Exit signal detection — mirror of the entry scoring system but for SELLS.

Sources:
  - Form 4 insider SELLING (AD=D)
  - Congressional PTR SELLS
  - Activists REDUCING stake (13D/G amendments)
  - Short interest INCREASING
  - Unusual PUT options
  - Institutional DECREASING positions (13F)

Exit score uses the same weight framework as scorer.py but inverted.
Only fires at ALTA (56+) or MUY ALTA (86+) to avoid false positives —
insider selling is noisier than buying.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import List, Optional, Set

from form4_parser import Transaction
from scorer import (
    CLUSTER_BONUS,
    CONVERGENCE_BONUS,
    TIER_THRESHOLDS,
    ScoreComponent,
    TierScore,
    W_ACTIVIST_13D,
    W_INSIDER_DIR,
    W_INSIDER_EXEC,
    W_INSIDER_OTHER,
    W_OPT_UNUSUAL,
    W_PASSIVE_13G,
    W_POLITICIAN,
    W_SHORT_DECLINE,
    _classify,
    _magnitude_activist,
    _magnitude_insider,
    _magnitude_options,
    _magnitude_short,
    _recency,
)

logger = logging.getLogger(__name__)

# Exit-specific: insider selling is noisier, reduce weight slightly
W_INSIDER_SELL_EXEC  = 22
W_INSIDER_SELL_DIR   = 16
W_INSIDER_SELL_OTHER = 10

# Minimum exit tier to actually send an alert (ALTA = 56+)
MIN_EXIT_TIER_TO_ALERT = 56


@dataclass
class ExitTierScore:
    """Exit signal score for a portfolio position."""
    ticker: str
    issuer_name: str
    total_score: float
    tier: str
    components: List[ScoreComponent] = field(default_factory=list)
    convergence_bonus: float = 0.0
    cluster_bonus: float = 0.0
    active_source_types: List[str] = field(default_factory=list)

    # Raw evidence
    insider_sells: List[Transaction] = field(default_factory=list)
    politician_sells: list = field(default_factory=list)
    activist_reductions: list = field(default_factory=list)
    institutional_reductions: list = field(default_factory=list)
    short_interest: object = None
    unusual_puts: list = field(default_factory=list)

    @property
    def should_alert(self) -> bool:
        return self.total_score >= MIN_EXIT_TIER_TO_ALERT


def is_insider_sell(txn: Transaction) -> bool:
    """True if this Form 4 transaction represents an open-market sale."""
    return (
        txn.acquired_disposed == "D"
        and txn.transaction_code in ("S", "G")   # S=open market, G=gift/transfer
        and txn.value > 0
    )


def detect_insider_sells(
    transactions: List[Transaction],
    ticker: str,
    window_days: int = 14,
) -> List[Transaction]:
    """Return significant insider sells for a given ticker within the window."""
    cutoff = date.today() - timedelta(days=window_days)
    sells = []
    for txn in transactions:
        if txn.ticker.upper() != ticker.upper():
            continue
        if not is_insider_sell(txn):
            continue
        try:
            txn_date = date.fromisoformat(txn.transaction_date)
        except (ValueError, AttributeError):
            continue
        if txn_date >= cutoff:
            sells.append(txn)
    return sells


def score_exit(
    ticker: str,
    issuer_name: str,
    insider_sells: List[Transaction],
    politician_sells: list,
    activist_reductions: list,
    institutional_reductions: list,
    short_interest,
    unusual_puts: list,
) -> ExitTierScore:
    """Compute an exit score for a portfolio position."""
    components: List[ScoreComponent] = []
    active_sources: List[str] = []
    total = 0.0
    cb = 0.0

    # ── Insider sells ──────────────────────────────────────────────────────
    if insider_sells:
        active_sources.append("insider_sells")
        for txn in insider_sells:
            roles = set(txn.role_labels)
            if roles & {"CEO", "CFO", "PRES"}:
                w = W_INSIDER_SELL_EXEC
                label = "EXEC"
            elif "DIR" in roles:
                w = W_INSIDER_SELL_DIR
                label = "DIR"
            else:
                w = W_INSIDER_SELL_OTHER
                label = "OTHER"
            mag = _magnitude_insider(txn.value)
            rec = _recency(txn.transaction_date)
            pts = w * mag * rec
            total += pts
            components.append(ScoreComponent(
                source=f"Insider vende ({txn.owner_name}/{label})",
                raw_weight=w, magnitude_mult=mag, recency_mult=rec, points=pts,
                detail=f"${txn.value:,.0f} el {txn.transaction_date}",
            ))
        distinct = len({t.owner_name for t in insider_sells})
        cb = CLUSTER_BONUS.get(min(distinct, 3), 0) if distinct >= 3 else (10 if distinct >= 2 else 0)
        total += cb

    # ── Politicians selling ────────────────────────────────────────────────
    if politician_sells:
        active_sources.append("politicos_venden")
        pol_pts = 0.0
        seen: Set[str] = set()
        for pt in politician_sells:
            if pt.politician_name in seen:
                continue
            seen.add(pt.politician_name)
            mag = _magnitude_insider(max(pt.amount_min, 1))
            rec = _recency(pt.transaction_date)
            pts = W_POLITICIAN * mag * rec
            pol_pts = min(pol_pts + pts, 50)
            components.append(ScoreComponent(
                source=f"Político vende ({pt.label})",
                raw_weight=W_POLITICIAN, magnitude_mult=mag, recency_mult=rec,
                points=pts, detail=f"{pt.amount_range or '?'} el {pt.transaction_date}",
            ))
        total += pol_pts

    # ── Activist reducing ──────────────────────────────────────────────────
    for af in activist_reductions:
        w = W_ACTIVIST_13D if af.filing_type == "13D" else W_PASSIVE_13G
        mag = _magnitude_activist(af.stake_pct) if af.stake_pct else 1.0
        rec = _recency(af.filing_date)
        pts = w * mag * rec
        total += pts
        src = f"activist_{af.filing_type}_reduce"
        if src not in active_sources:
            active_sources.append(src)
        components.append(ScoreComponent(
            source=f"{af.filing_type} reduciendo ({af.filer_name})",
            raw_weight=w, magnitude_mult=mag, recency_mult=rec, points=pts,
            detail=f"{af.filing_date}",
        ))

    # ── Institutional reducing ─────────────────────────────────────────────
    if institutional_reductions:
        active_sources.append("13F_reduce")
        inst_pts = 0.0
        for ip in institutional_reductions[:5]:
            rec = _recency(ip.period_of_report)
            pts = 10 * rec
            inst_pts = min(inst_pts + pts, 25)
            components.append(ScoreComponent(
                source=f"13F reduciendo ({ip.fund_name})",
                raw_weight=10, magnitude_mult=1.0, recency_mult=rec, points=pts,
                detail=f"${ip.value_usd:,.0f}",
            ))
        total += inst_pts

    # ── Short interest rising ──────────────────────────────────────────────
    if short_interest:
        rise_pct = -short_interest.decline_pct   # negative decline = rise
        if rise_pct >= 10:
            active_sources.append("short_rising")
            mag = _magnitude_short(rise_pct)
            rec = _recency(short_interest.report_date)
            pts = W_SHORT_DECLINE * mag * rec
            total += pts
            components.append(ScoreComponent(
                source="Short Interest subiendo",
                raw_weight=W_SHORT_DECLINE, magnitude_mult=mag, recency_mult=rec,
                points=pts,
                detail=f"Subió {rise_pct:.1f}% (ahora {short_interest.current_pct:.1f}%)",
            ))

    # ── Unusual PUTs ──────────────────────────────────────────────────────
    if unusual_puts:
        active_sources.append("puts_inusuales")
        best = max(unusual_puts, key=lambda o: o.volume_oi_ratio)
        mag = _magnitude_options(best.volume_oi_ratio)
        rec = _recency(best.timestamp[:10] if best.timestamp else "")
        pts = W_OPT_UNUSUAL * mag * rec
        total += pts
        components.append(ScoreComponent(
            source=f"PUTs inusuales",
            raw_weight=W_OPT_UNUSUAL, magnitude_mult=mag, recency_mult=rec,
            points=pts,
            detail=f"Vol/OI: {best.volume_oi_ratio:.1f}x | strike {best.strike}",
        ))

    # ── Convergence bonus ──────────────────────────────────────────────────
    n_types = len(active_sources)
    conv_bonus = 0.0
    for threshold in sorted(CONVERGENCE_BONUS.keys(), reverse=True):
        if n_types >= threshold:
            conv_bonus = CONVERGENCE_BONUS[threshold]
            break
    total += conv_bonus

    return ExitTierScore(
        ticker=ticker,
        issuer_name=issuer_name,
        total_score=round(total, 1),
        tier=_classify(total),
        components=components,
        convergence_bonus=conv_bonus,
        cluster_bonus=cb,
        active_source_types=active_sources,
        insider_sells=insider_sells,
        politician_sells=politician_sells,
        activist_reductions=activist_reductions,
        institutional_reductions=institutional_reductions,
        short_interest=short_interest,
        unusual_puts=unusual_puts,
    )
