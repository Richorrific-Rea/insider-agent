"""
Telegram notifier — optimizado para lectura en móvil.

Principios de diseño:
  - Líneas cortas (≤40 chars de contenido)
  - Montos en formato $159k / $1.2M
  - Fechas en lenguaje natural (hoy, ayer, hace 3 días)
  - Sin jerga técnica (no Vol/OI ratios, no scores numéricos)
  - Info más importante arriba
  - Disclaimer corto al final
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scorer import TierScore
    from signals import Signal

import requests

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

_MDV2 = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")

def _e(t: str) -> str:
    return _MDV2.sub(r"\\\1", str(t))


# ── Format helpers ─────────────────────────────────────────────────────────────

def _fmt_money(value: float) -> str:
    if value >= 1_000_000:
        return f"${value/1_000_000:.1f}M"
    if value >= 10_000:
        return f"${value/1_000:.0f}k"
    return f"${value:,.0f}"


def _fmt_date(date_str: str) -> str:
    """Convert ISO date to 'hoy', 'ayer', 'hace N días', or 'el DD/MM'."""
    try:
        d = date.fromisoformat(date_str)
    except (ValueError, TypeError):
        return date_str or "fecha desconocida"
    today = date.today()
    diff = (today - d).days
    if diff == 0:   return "hoy"
    if diff == 1:   return "ayer"
    if diff <= 6:   return f"hace {diff} días"
    return f"el {d.day}/{d.month}"


def _fmt_role(roles: list) -> str:
    """Return the most important role in plain language."""
    priority = ["CEO", "CFO", "COO", "PRES", "DIR", "TENPCT", "OFFICER"]
    for r in priority:
        if r in roles:
            labels = {"CEO":"CEO","CFO":"CFO","COO":"COO","PRES":"Presidente",
                      "DIR":"Director","TENPCT":"Accionista 10%","OFFICER":"Ejecutivo"}
            return labels.get(r, r)
    return roles[0] if roles else "Insider"


def _tier_header(tier: str) -> str:
    return {
        "MUY ALTA": "Señal MUY ALTA",
        "ALTA":     "Señal ALTA",
        "MEDIA":    "Señal MEDIA",
        "BAJA":     "Señal baja",
    }.get(tier, tier)


_DISCLAIMER = "Idea para investigar, no consejo de inversión."


# ── Entry signal ───────────────────────────────────────────────────────────────

def _build_signal_message(ts: "TierScore", brief: str) -> str:
    lines = []

    # Header
    lines.append(f"*{_e(ts.ticker)}*  ·  {_e(_tier_header(ts.tier))}")

    # Price confirmation (when present)
    if ts.has_price_confirmation:
        ps = ts.price_snapshot
        lines.append(f"📈 Precio \\+{_e(f'{ps.pct_change_vs_close:.1f}%')} hoy · ${_e(f'{ps.current_price:.2f}')}")

    lines.append("")

    # Brief from LLM (first, most readable part)
    if brief:
        lines.append(_e(brief))
        lines.append("")

    # Insiders
    if ts.insider_signals:
        distinct = len({s.transaction.owner_name for s in ts.insider_signals})
        if distinct > 1:
            total_val = sum(s.transaction.value for s in ts.insider_signals)
            lines.append(f"*{distinct} directivos compraron · {_e(_fmt_money(total_val))} total*")
        for sig in ts.insider_signals[:4]:
            t = sig.transaction
            role = _fmt_role(t.role_labels)
            lines.append(f"  · {_e(role)}: {_e(_fmt_money(t.value))} {_e(_fmt_date(t.transaction_date))}")

    # Politicians
    if ts.politician_trades:
        seen: set = set()
        pols = [p for p in ts.politician_trades if not (seen.add(p.politician_name) or p.politician_name in seen - {p.politician_name})]
        # simpler dedup
        seen2: set = set()
        unique_pols = []
        for p in ts.politician_trades:
            if p.politician_name not in seen2:
                seen2.add(p.politician_name)
                unique_pols.append(p)
        if unique_pols:
            lines.append("")
            lines.append(f"*Políticos comprando*")
            for p in unique_pols[:3]:
                name = p.politician_name.split(",")[0].strip()  # "Pelosi, Nancy" → "Pelosi"
                amt = f" · {_e(p.amount_range)}" if p.amount_range else ""
                lines.append(f"  · {_e(name)}{amt}")

    # Activists
    if ts.activist_filings:
        lines.append("")
        for a in ts.activist_filings[:2]:
            name = a.filer_name.split("/")[0].strip()
            lines.append(f"*Activista {_e(a.filing_type)}:* {_e(name)}")

    # Short interest
    if ts.short_interest and ts.short_interest.decline_pct >= 10:
        si = ts.short_interest
        lines.append("")
        lines.append(f"Cortos cubriendo posiciones \\(\\-{_e(f'{si.decline_pct:.0f}%')}\\)")

    # Options
    if ts.unusual_options:
        opt = ts.unusual_options[0]
        lines.append("")
        lines.append(f"Opciones call inusuales detectadas")

    lines.append("")
    lines.append(_e(_DISCLAIMER))
    return "\n".join(lines)


def send_tier_score(
    ts: "TierScore",
    brief: str,
    bot_token: str,
    chat_id: str,
    dry_run: bool = False,
) -> None:
    message = _build_signal_message(ts, brief)
    _send(message, bot_token, chat_id, dry_run,
          label=f"{ts.tier} {ts.ticker}")


# ── Exit alert ─────────────────────────────────────────────────────────────────

def send_exit_alert(
    exit_score,
    position,
    brief: str,
    bot_token: str,
    chat_id: str,
    dry_run: bool = False,
) -> None:
    es = exit_score
    lines = []

    lines.append(f"*{_e(es.ticker)}*  ·  Posible señal de salida")
    lines.append("")

    # Position summary
    days_held = (date.today() - date.fromisoformat(position.buy_date)).days if position.buy_date else 0
    held_str = "hoy" if days_held == 0 else ("ayer" if days_held == 1 else f"hace {days_held} días")
    lines.append(f"Tu posición: {position.shares:,.0f} acc · {_e(_fmt_money(position.buy_price))} c/u")
    lines.append(f"Compraste {held_str}")
    lines.append("")

    # Brief
    if brief:
        lines.append(_e(brief))
        lines.append("")

    # What's happening
    lines.append("*Qué está pasando:*")

    if es.insider_sells:
        distinct = len({t.owner_name for t in es.insider_sells})
        total = sum(t.value for t in es.insider_sells)
        lines.append(f"  · {distinct} insider{'s' if distinct > 1 else ''} vendió{'n' if distinct > 1 else ''} {_e(_fmt_money(total))}")

    if es.politician_sells:
        seen: set = set()
        n = 0
        for p in es.politician_sells:
            if p.politician_name not in seen:
                seen.add(p.politician_name)
                n += 1
        lines.append(f"  · {n} político{'s' if n > 1 else ''} vendió{'n' if n > 1 else ''}")

    if es.activist_reductions:
        lines.append(f"  · Activista reduciendo posición")

    if es.short_interest:
        rise = -es.short_interest.decline_pct
        if rise >= 10:
            lines.append(f"  · Cortos aumentaron {_e(f'{rise:.0f}%')}")

    if es.unusual_puts:
        lines.append(f"  · Opciones PUT inusuales")

    lines.append("")
    lines.append(_e(_DISCLAIMER))

    message = "\n".join(lines)
    _send(message, bot_token, chat_id, dry_run,
          label=f"EXIT {es.ticker} [{es.tier}]")


# ── Portfolio price spike (position moving, no new signal) ────────────────────

def send_price_only_alert(
    price_snapshot,
    position,
    bot_token: str,
    chat_id: str,
    dry_run: bool = False,
) -> None:
    ps = price_snapshot
    days_held = 0
    if position and position.buy_date:
        try:
            days_held = (date.today() - date.fromisoformat(position.buy_date)).days
        except Exception:
            pass

    held_str = "hoy" if days_held == 0 else ("ayer" if days_held == 1 else f"hace {days_held} días")

    lines = [
        f"*{_e(ps.ticker)}*  ·  Tu posición está subiendo",
        f"📈 \\+{_e(f'{ps.pct_change_vs_close:.1f}%')} hoy · ${_e(f'{ps.current_price:.2f}')}",
        "",
    ]

    if position:
        unrealized_pct = ((ps.current_price - position.buy_price) / position.buy_price) * 100
        unrealized_usd = (ps.current_price - position.buy_price) * position.shares
        sign = "\\+" if unrealized_pct >= 0 else ""
        lines += [
            f"Compraste {position.shares:,.0f} acc · {_e(_fmt_money(position.buy_price))} c/u",
            f"Compraste {held_str}",
            f"Ganancia: {_e(_fmt_money(abs(unrealized_usd)))} \\({sign}{_e(f'{unrealized_pct:.1f}')}%\\)",
        ]

    lines.append("")
    lines.append(_e(_DISCLAIMER))

    message = "\n".join(lines)
    _send(message, bot_token, chat_id, dry_run,
          label=f"PriceSpike {ps.ticker} +{ps.pct_change_vs_close:.1f}%")


# ── Watchlist price alert ──────────────────────────────────────────────────────

def send_watchlist_alert(
    price_snapshot,
    bot_token: str,
    chat_id: str,
    dry_run: bool = False,
) -> None:
    ps = price_snapshot
    strength_labels = {
        "EXTREMO": "subida extrema",
        "FUERTE":  "subida fuerte",
        "NOTABLE": "movimiento notable",
    }
    label = strength_labels.get(ps.spike_strength, "movimiento de precio")

    vol_note = ""
    if ps.volume_ratio >= 3.0:
        vol_note = f" · volumen {_e(f'{ps.volume_ratio:.1f}')}x"
    elif ps.volume_ratio < 0.8:
        vol_note = " · volumen bajo"

    lines = [
        f"*{_e(ps.ticker)}*  ·  {_e(label.capitalize())}",
        f"📈 \\+{_e(f'{ps.pct_change_vs_close:.1f}%')} hoy · ${_e(f'{ps.current_price:.2f}')}{vol_note}",
        "",
        f"En tu watchlist\\. Sin señal de insiders activa\\.",
        "",
        _e(_DISCLAIMER),
    ]

    message = "\n".join(lines)
    _send(message, bot_token, chat_id, dry_run,
          label=f"Watchlist {ps.ticker} +{ps.pct_change_vs_close:.1f}%")


# ── Price drop alerts ─────────────────────────────────────────────────────────

_DROP_LABELS = {
    "EXTREMO": "Caída extrema",
    "FUERTE":  "Caída fuerte",
    "NOTABLE": "Caída notable",
}


def send_portfolio_drop_alert(
    price_snapshot,
    position,
    bot_token: str,
    chat_id: str,
    dry_run: bool = False,
) -> None:
    """Alert when a portfolio position is dropping significantly."""
    ps = price_snapshot
    label = _DROP_LABELS.get(ps.drop_strength, "Caída de precio")

    unrealized_pct = ((ps.current_price - position.buy_price) / position.buy_price) * 100
    unrealized_usd = (ps.current_price - position.buy_price) * position.shares

    days_held = 0
    if position.buy_date:
        try:
            days_held = (date.today() - date.fromisoformat(position.buy_date)).days
        except Exception:
            pass
    held_str = "hoy" if days_held == 0 else ("ayer" if days_held == 1 else f"hace {days_held} días")

    vol_note = f" · volumen {_e(f'{ps.volume_ratio:.1f}')}x" if ps.volume_ratio >= 2.0 else ""
    sign = "\\+" if unrealized_pct >= 0 else ""

    lines = [
        f"*{_e(ps.ticker)}*  ·  {_e(label)}",
        f"{_e(f'{ps.pct_change_vs_close:.1f}%')} hoy · ${_e(f'{ps.current_price:.2f}')}{vol_note}",
        "",
        f"Tu posición: {_e(f'{position.shares:,.0f}')} acc · {_e(_fmt_money(position.buy_price))} c/u",
        f"Compraste {held_str}",
        f"P&L: {_e(_fmt_money(abs(unrealized_usd)))} \\({sign}{_e(f'{unrealized_pct:.1f}')}%\\)",
        "",
        _e(_DISCLAIMER_RAW),
    ]

    _send("\n".join(lines), bot_token, chat_id, dry_run,
          label=f"PortfolioDrop {ps.ticker} {ps.pct_change_vs_close:.1f}%")


def send_watchlist_drop_alert(
    price_snapshot,
    bot_token: str,
    chat_id: str,
    dry_run: bool = False,
) -> None:
    """Alert when a watchlist ticker is dropping significantly."""
    ps = price_snapshot
    label = _DROP_LABELS.get(ps.drop_strength, "Caída de precio")
    vol_note = f" · volumen {_e(f'{ps.volume_ratio:.1f}')}x" if ps.volume_ratio >= 2.0 else ""

    lines = [
        f"*{_e(ps.ticker)}*  ·  {_e(label)}",
        f"{_e(f'{ps.pct_change_vs_close:.1f}%')} hoy · ${_e(f'{ps.current_price:.2f}')}{vol_note}",
        "",
        f"En tu watchlist\\. Sin señal de insiders activa\\.",
        "",
        _e(_DISCLAIMER_RAW),
    ]

    _send("\n".join(lines), bot_token, chat_id, dry_run,
          label=f"WatchlistDrop {ps.ticker} {ps.pct_change_vs_close:.1f}%")


# ── Legacy: single signal (backward compat) ───────────────────────────────────

def send_signal(
    signal: "Signal",
    brief: str,
    bot_token: str,
    chat_id: str,
    dry_run: bool = False,
) -> None:
    txn = signal.transaction
    role = _fmt_role(txn.role_labels)
    lines = [
        f"*{_e(txn.ticker)}*  ·  Compra insider",
        "",
        f"{_e(role)} compró {_e(_fmt_money(txn.value))} {_e(_fmt_date(txn.transaction_date))}",
    ]
    if signal.is_cluster:
        lines.append(f"Cluster: {signal.cluster_size} directivos compraron esta semana")
    if brief:
        lines += ["", _e(brief)]
    lines += ["", _e(_DISCLAIMER)]

    _send("\n".join(lines), bot_token, chat_id, dry_run,
          label=f"Signal {txn.ticker}")


# ── Legacy: confluence (backward compat) ──────────────────────────────────────

def send_confluence(csig, brief: str, bot_token: str, chat_id: str, dry_run: bool = False) -> None:
    if csig.insider_signals:
        send_signal(csig.primary_signal, brief, bot_token, chat_id, dry_run)


# ── Transport ──────────────────────────────────────────────────────────────────

def _send(message: str, bot_token: str, chat_id: str, dry_run: bool, label: str = "") -> None:
    if dry_run:
        sep = "─" * 50
        print(f"\n{sep}")
        print(f"[DRY RUN] {label}")
        print(message)
        print(sep)
        return

    if not bot_token or not chat_id:
        logger.warning("Telegram credentials not set; skipping %s.", label)
        return

    try:
        resp = requests.post(
            TELEGRAM_API.format(token=bot_token),
            json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "MarkdownV2",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.error("Telegram send failed (%s): %s", label, exc)
