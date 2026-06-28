"""
Signal scoring engine.

Collects all signal types for a ticker, applies weights/multipliers/bonuses
and produces a TierScore with a numeric score and tier classification.

Tiers:
  BAJA     0  – 25   single weak signal
  MEDIA    26 – 55   solid signal, worth investigating
  ALTA     56 – 85   multiple independent sources
  MUY ALTA 86+       strong convergence — priority
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

# ── Weights ────────────────────────────────────────────────────────────────────

W_INSIDER_EXEC  = 30   # CEO / CFO / PRES
W_INSIDER_DIR   = 20   # Director
W_INSIDER_OTHER = 15   # Other officer / 10%
W_POLITICIAN    = 20   # per politician, capped at 50
W_ACTIVIST_13D  = 40   # activist crossing ≥5%
W_PASSIVE_13G   = 15   # passive large holder ≥5%
W_INST_13F      = 10   # per new institutional position, capped at 25
W_SHORT_DECLINE = 15   # short interest falling
W_OPT_UNUSUAL   = 25   # unusual call options
W_PRICE_CONFIRM = 20   # price spike confirming the signal (thesis playing out)

# Convergence bonuses (# of independent signal *types* firing)
CONVERGENCE_BONUS = {2: 10, 3: 25, 4: 40, 5: 55}

# Cluster bonus (multiple insiders, same ticker, same week)
CLUSTER_BONUS = {2: 10, 3: 20}   # 3 means 3+

TIER_THRESHOLDS: List[Tuple[float, str]] = [
    (86, "MUY ALTA"),
    (56, "ALTA"),
    (26, "MEDIA"),
    (0,  "BAJA"),
]

# ── Helpers ────────────────────────────────────────────────────────────────────

def _recency(date_str: str) -> float:
    try:
        d = date.fromisoformat(date_str)
    except (ValueError, TypeError):
        return 0.6   # unknown → moderate penalty
    days = (date.today() - d).days
    if days <= 3:  return 1.2
    if days <= 7:  return 1.0
    if days <= 14: return 0.8
    if days <= 30: return 0.6
    return 0.3


def _magnitude_insider(value_usd: float) -> float:
    if value_usd >= 5_000_000: return 2.0
    if value_usd >= 1_000_000: return 1.6
    if value_usd >= 500_000:   return 1.3
    if value_usd >= 100_000:   return 1.0
    return 0.5


def _magnitude_activist(stake_pct: float) -> float:
    if stake_pct >= 20: return 1.8
    if stake_pct >= 10: return 1.4
    return 1.0


def _magnitude_short(decline_pct: float) -> float:
    if decline_pct >= 30: return 1.6
    if decline_pct >= 20: return 1.3
    return 1.0


def _magnitude_options(volume_oi_ratio: float) -> float:
    if volume_oi_ratio >= 10: return 1.6
    if volume_oi_ratio >= 5:  return 1.3
    return 1.0


def _magnitude_price_spike(pct_change: float, vol_ratio: float) -> float:
    """Bigger move + more volume = stronger confirmation."""
    price_mult = 2.0 if pct_change >= 15 else (1.6 if pct_change >= 8 else 1.2)
    vol_mult   = 1.3 if vol_ratio >= 3.0  else (1.1 if vol_ratio >= 2.0  else 1.0)
    return price_mult * vol_mult


# ── Score components ───────────────────────────────────────────────────────────

@dataclass
class ScoreComponent:
    source: str
    raw_weight: float
    magnitude_mult: float
    recency_mult: float
    points: float
    detail: str


# ── Main output ────────────────────────────────────────────────────────────────

@dataclass
class TierScore:
    ticker: str
    issuer_name: str
    total_score: float
    tier: str
    components: List[ScoreComponent] = field(default_factory=list)
    convergence_bonus: float = 0.0
    cluster_bonus: float = 0.0
    active_source_types: List[str] = field(default_factory=list)

    # Raw evidence attached for enrich / notify
    insider_signals: list = field(default_factory=list)
    politician_trades: list = field(default_factory=list)
    activist_filings: list = field(default_factory=list)
    institutional_positions: list = field(default_factory=list)
    short_interest: object = None
    unusual_options: list = field(default_factory=list)
    price_snapshot: object = None   # PriceSnapshot if spike detected

    @property
    def has_price_confirmation(self) -> bool:
        return self.price_snapshot is not None and self.price_snapshot.is_spiking

    @property
    def score_breakdown(self) -> str:
        lines = [f"Score total: {self.total_score:.0f} → {self.tier}"]
        for c in self.components:
            lines.append(f"  {c.source}: {c.points:.1f}  ({c.detail})")
        if self.cluster_bonus:
            lines.append(f"  Bonus cluster: +{self.cluster_bonus:.0f}")
        if self.convergence_bonus:
            lines.append(f"  Bonus convergencia ({len(self.active_source_types)} fuentes): +{self.convergence_bonus:.0f}")
        return "\n".join(lines)


def _classify(score: float) -> str:
    for threshold, tier in TIER_THRESHOLDS:
        if score >= threshold:
            return tier
    return "BAJA"


# ── Main scoring function ──────────────────────────────────────────────────────

def score_ticker(
    ticker: str,
    issuer_name: str,
    insider_signals: list,
    politician_trades: list,
    activist_filings: list,
    institutional_positions: list,
    short_interest,
    unusual_options: list,
    price_snapshot=None,
) -> TierScore:
    """
    Compute a TierScore for a single ticker from all available signal types.
    """
    components: List[ScoreComponent] = []
    active_sources: List[str] = []
    total = 0.0

    # ── Insiders ───────────────────────────────────────────────────────────
    if insider_signals:
        active_sources.append("insiders")
        insider_total = 0.0
        for sig in insider_signals:
            txn = sig.transaction
            roles = set(txn.role_labels)
            if roles & {"CEO", "CFO", "PRES"}:
                w = W_INSIDER_EXEC
                label = "EXEC"
            elif "DIR" in roles:
                w = W_INSIDER_DIR
                label = "DIR"
            else:
                w = W_INSIDER_OTHER
                label = "OTHER"
            mag = _magnitude_insider(txn.value)
            rec = _recency(txn.transaction_date)
            pts = w * mag * rec
            insider_total += pts
            components.append(ScoreComponent(
                source=f"Insider ({txn.owner_name} / {label})",
                raw_weight=w, magnitude_mult=mag, recency_mult=rec, points=pts,
                detail=f"${txn.value:,.0f} el {txn.transaction_date}",
            ))

        # Cluster bonus
        distinct_insiders = len({s.transaction.owner_name for s in insider_signals})
        cb = CLUSTER_BONUS.get(min(distinct_insiders, 3), 0)
        if distinct_insiders >= 3:
            cb = CLUSTER_BONUS[3]

        total += insider_total + cb

    else:
        cb = 0.0

    # ── Politicians ────────────────────────────────────────────────────────
    if politician_trades:
        active_sources.append("politicos")
        pol_pts = 0.0
        seen_pols = set()
        for pt in politician_trades:
            if pt.politician_name in seen_pols:
                continue
            seen_pols.add(pt.politician_name)
            mag = _magnitude_insider(max(pt.amount_min, 1))
            rec = _recency(pt.transaction_date)
            pts = W_POLITICIAN * mag * rec
            pol_pts = min(pol_pts + pts, 50)   # cap
            components.append(ScoreComponent(
                source=f"Político ({pt.label})",
                raw_weight=W_POLITICIAN, magnitude_mult=mag, recency_mult=rec,
                points=pts, detail=f"{pt.amount_range or '?'} el {pt.transaction_date}",
            ))
        total += pol_pts

    # ── Activists 13D/13G ──────────────────────────────────────────────────
    for af in activist_filings:
        w = W_ACTIVIST_13D if af.filing_type == "13D" else W_PASSIVE_13G
        mag = _magnitude_activist(af.stake_pct)
        rec = _recency(af.filing_date)
        pts = w * mag * rec
        total += pts
        source_key = f"activist_{af.filing_type}"
        if source_key not in active_sources:
            active_sources.append(source_key)
        components.append(ScoreComponent(
            source=f"{af.filing_type} ({af.filer_name})",
            raw_weight=w, magnitude_mult=mag, recency_mult=rec, points=pts,
            detail=f"{af.stake_pct:.1f}% el {af.filing_date}",
        ))

    # ── Institutional 13F ──────────────────────────────────────────────────
    if institutional_positions:
        active_sources.append("13F")
        inst_pts = 0.0
        for ip in institutional_positions[:5]:
            rec = _recency(ip.period_of_report)
            pts = W_INST_13F * rec
            inst_pts = min(inst_pts + pts, 25)   # cap
            components.append(ScoreComponent(
                source=f"13F ({ip.fund_name})",
                raw_weight=W_INST_13F, magnitude_mult=1.0, recency_mult=rec,
                points=pts, detail=f"Nueva posición: ${ip.value_usd:,.0f}",
            ))
        total += inst_pts

    # ── Short interest ─────────────────────────────────────────────────────
    if short_interest and short_interest.decline_pct >= 10:
        active_sources.append("short_interest")
        mag = _magnitude_short(short_interest.decline_pct)
        rec = _recency(short_interest.report_date)
        pts = W_SHORT_DECLINE * mag * rec
        total += pts
        components.append(ScoreComponent(
            source="Short Interest",
            raw_weight=W_SHORT_DECLINE, magnitude_mult=mag, recency_mult=rec,
            points=pts,
            detail=f"Cayó {short_interest.decline_pct:.1f}% (ahora {short_interest.current_pct:.1f}%)",
        ))

    # ── Unusual options ────────────────────────────────────────────────────
    if unusual_options:
        active_sources.append("opciones")
        best = max(unusual_options, key=lambda o: o.volume_oi_ratio)
        mag = _magnitude_options(best.volume_oi_ratio)
        rec = _recency(best.timestamp[:10] if best.timestamp else "")
        pts = W_OPT_UNUSUAL * mag * rec
        total += pts
        components.append(ScoreComponent(
            source=f"Opciones inusuales ({best.option_type})",
            raw_weight=W_OPT_UNUSUAL, magnitude_mult=mag, recency_mult=rec,
            points=pts,
            detail=f"Vol/OI: {best.volume_oi_ratio:.1f}x | strike {best.strike} exp {best.expiration}",
        ))

    # ── Price spike confirmation ───────────────────────────────────────────
    # Only counts when there are OTHER signals already — price alone doesn't score
    if price_snapshot and price_snapshot.is_spiking and active_sources:
        active_sources.append("precio")
        mag = _magnitude_price_spike(
            price_snapshot.pct_change_vs_close,
            price_snapshot.volume_ratio,
        )
        pts = W_PRICE_CONFIRM * mag
        total += pts
        components.append(ScoreComponent(
            source="Precio confirmando",
            raw_weight=W_PRICE_CONFIRM, magnitude_mult=mag, recency_mult=1.0,
            points=pts,
            detail=(
                f"+{price_snapshot.pct_change_vs_close:.1f}% hoy | "
                f"Vol {price_snapshot.volume_ratio:.1f}x promedio"
            ),
        ))

    # ── Bonuses ────────────────────────────────────────────────────────────
    n_types = len(active_sources)
    conv_bonus = 0.0
    for threshold in sorted(CONVERGENCE_BONUS.keys(), reverse=True):
        if n_types >= threshold:
            conv_bonus = CONVERGENCE_BONUS[threshold]
            break

    total += cb + conv_bonus

    return TierScore(
        ticker=ticker,
        issuer_name=issuer_name,
        total_score=round(total, 1),
        tier=_classify(total),
        components=components,
        convergence_bonus=conv_bonus,
        cluster_bonus=cb,
        active_source_types=active_sources,
        insider_signals=insider_signals,
        politician_trades=politician_trades,
        activist_filings=activist_filings,
        institutional_positions=institutional_positions,
        short_interest=short_interest,
        unusual_options=unusual_options,
        price_snapshot=price_snapshot,
    )
