"""
Telegram notifier — broker de los 80 edition.

El tono del mensaje escala con el tier de la señal:
  BAJA     → mensaje limpio y profesional
  MEDIA    → algo interesante, vale la pena
  ALTA     → el broker se emociona
  MUY ALTA → el broker pierde la cabeza (de la mejor manera)
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from scorer import TierScore
    from signals import Signal

import requests

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
_DISCLAIMER_RAW = (
    "Idea para investigar basada en datos públicos "
    "(SEC EDGAR, divulgaciones del Congreso). "
    "No es recomendación de inversión."
)

_MDV2 = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")

def _e(t: str) -> str:
    return _MDV2.sub(r"\\\1", str(t))

def _usd(v: float) -> str:
    return _e(f"${v:,.0f}")

def _shares(v: float) -> str:
    return _e(f"{v:,.0f}")


# ── Tier headers ───────────────────────────────────────────────────────────────

_HEADERS = {
    "BAJA": (
        "Actividad de insider registrada",
        "SEÑAL: BAJA",
    ),
    "MEDIA": (
        "Actividad interesante detectada",
        "SEÑAL: MEDIA",
    ),
    "ALTA": (
        "El dinero listo se esta moviendo",
        "SEÑAL FUERTE",
    ),
    "MUY ALTA": (
        "CONFLUENCIA TOTAL — MAXIMO NIVEL",
        "ALERTA MAXIMA",
    ),
}

_SCORE_BAR = {
    "BAJA":     "▓░░░░",
    "MEDIA":    "▓▓▓░░",
    "ALTA":     "▓▓▓▓░",
    "MUY ALTA": "▓▓▓▓▓",
}


# ── Main: send_tier_score ──────────────────────────────────────────────────────

def send_tier_score(
    ts: "TierScore",
    brief: str,
    bot_token: str,
    chat_id: str,
    dry_run: bool = False,
) -> None:
    """Send a fully-scored TierScore message to Telegram."""
    message = _build_tier_message(ts, brief)
    _send(message, bot_token, chat_id, dry_run,
          label=f"{ts.tier} {ts.ticker} score={ts.total_score:.0f}")


def _build_tier_message(ts: "TierScore", brief: str) -> str:
    subtitle, badge = _HEADERS.get(ts.tier, ("Señal detectada", "SEÑAL"))
    bar = _SCORE_BAR.get(ts.tier, "▓░░░░")
    score_line = f"{bar} *{_e(ts.tier)}* \\| Score: *{_e(str(int(ts.total_score)))}* pts"

    # Price confirmation banner (shown right in the header when present)
    price_banner = ""
    if ts.has_price_confirmation:
        ps = ts.price_snapshot
        price_banner = (
            f"\n*precio ahora mismo:* \\+{_e(f'{ps.pct_change_vs_close:.1f}%')} hoy "
            f"\\| Vol {_e(f'{ps.volume_ratio:.1f}')}x \\| "
            f"${_e(f'{ps.current_price:.2f}')}"
        )

    lines = [
        f"*{_e(ts.ticker)}* — {_e(subtitle)}",
        score_line,
        price_banner,
        "",
    ]

    # ── Insiders ───────────────────────────────────────────────────────────
    if ts.insider_signals:
        n = len(ts.insider_signals)
        distinct = len({s.transaction.owner_name for s in ts.insider_signals})
        lines.append(f"*Insiders \\({_e(str(distinct))}\\):*")
        for sig in ts.insider_signals[:4]:
            t = sig.transaction
            roles = ", ".join(t.role_labels)
            lines.append(
                f"  • {_e(t.owner_name)} \\({_e(roles)}\\) "
                f"→ {_usd(t.value)} el {_e(t.transaction_date)}"
            )
        if n > 4:
            lines.append(f"  \\+{_e(str(n-4))} más")

    # ── Politicians ────────────────────────────────────────────────────────
    if ts.politician_trades:
        seen: set = set()
        unique_pols = []
        for p in ts.politician_trades:
            if p.politician_name not in seen:
                seen.add(p.politician_name)
                unique_pols.append(p)
        lines.append(f"\n*Políticos \\({_e(str(len(unique_pols)))}\\):*")
        for p in unique_pols[:4]:
            amt = f" ~ {_e(p.amount_range)}" if p.amount_range else ""
            dt = f" el {_e(p.transaction_date)}" if p.transaction_date else ""
            lines.append(f"  • {_e(p.label)}{amt}{dt}")

    # ── Activists ──────────────────────────────────────────────────────────
    if ts.activist_filings:
        lines.append(f"\n*Activistas \\(13D/13G\\):*")
        for a in ts.activist_filings[:3]:
            stake = f"{a.stake_pct:.1f}%" if a.stake_pct else "≥5%"
            lines.append(
                f"  • {_e(a.filer_name)} \\({_e(a.filing_type)}\\) "
                f"→ {_e(stake)} stake el {_e(a.filing_date)}"
            )

    # ── Institutional ──────────────────────────────────────────────────────
    if ts.institutional_positions:
        lines.append(f"\n*Institucionales \\(13F\\):*")
        for ip in ts.institutional_positions[:3]:
            lines.append(
                f"  • {_e(ip.fund_name)} → nueva posición {_usd(ip.value_usd)}"
            )

    # ── Short interest ─────────────────────────────────────────────────────
    if ts.short_interest and ts.short_interest.decline_pct >= 10:
        si = ts.short_interest
        lines.append(
            f"\n*Short Interest:* cayó {_e(f'{si.decline_pct:.0f}%')} "
            f"\\(ahora {_e(f'{si.current_pct:.1f}%')} del float\\)"
        )

    # ── Unusual options ────────────────────────────────────────────────────
    if ts.unusual_options:
        opt = ts.unusual_options[0]
        lines.append(
            f"\n*Opciones inusuales:* {_e(opt.option_type)} "
            f"strike {_e(str(opt.strike))} exp {_e(opt.expiration)} "
            f"\\| Vol/OI: *{_e(f'{opt.volume_oi_ratio:.1f}')}x*"
        )

    # ── Score breakdown ────────────────────────────────────────────────────
    n_sources = len(ts.active_source_types)
    lines.append(
        f"\n*{_e(str(n_sources))} fuente{'s' if n_sources != 1 else ''} independiente{'s' if n_sources != 1 else ''}* confirmando"
    )

    # ── Brief ──────────────────────────────────────────────────────────────
    lines.append(f"\n{_e(brief)}")

    lines.append(f"\n{_e(_DISCLAIMER_RAW)}")
    return "\n".join(lines)


# ── Exit alert ────────────────────────────────────────────────────────────────

_EXIT_HEADERS = {
    "MEDIA":    "Actividad de ventas detectada en tu posición",
    "ALTA":     "El dinero se esta moviendo — señal de salida",
    "MUY ALTA": "ALERTA MAXIMA DE SALIDA — TODOS ESTAN VENDIENDO",
}
_EXIT_BARS = {
    "BAJA":     "░░░░░",
    "MEDIA":    "▓▓░░░",
    "ALTA":     "▓▓▓▓░",
    "MUY ALTA": "▓▓▓▓▓",
}


def send_exit_alert(
    exit_score,          # ExitTierScore
    position,            # portfolio.Position
    brief: str,
    bot_token: str,
    chat_id: str,
    dry_run: bool = False,
) -> None:
    message = _build_exit_message(exit_score, position, brief)
    _send(message, bot_token, chat_id, dry_run,
          label=f"EXIT {exit_score.ticker} [{exit_score.tier}] score={exit_score.total_score:.0f}")


def _build_exit_message(exit_score, position, brief: str) -> str:
    subtitle = _EXIT_HEADERS.get(exit_score.tier, "Señal de salida")
    bar = _EXIT_BARS.get(exit_score.tier, "▓░░░░")

    lines = [
        f"*{_e(exit_score.ticker)}* — {_e(subtitle)}",
        f"{bar} *SALIDA {_e(exit_score.tier)}* \\| Score: *{_e(str(int(exit_score.total_score)))}* pts",
        "",
        f"*Tu posición:* {_shares(position.shares)} acc @ {_usd(position.buy_price)} "
        f"el {_e(position.buy_date)}",
    ]
    if position.notes:
        lines.append(f"*Nota:* {_e(position.notes)}")

    lines.append("")

    if exit_score.insider_sells:
        distinct = len({t.owner_name for t in exit_score.insider_sells})
        total_val = sum(t.value for t in exit_score.insider_sells)
        lines.append(f"*Insiders vendiendo \\({_e(str(distinct))}\\):*")
        for t in exit_score.insider_sells[:4]:
            roles = ", ".join(t.role_labels)
            lines.append(
                f"  • {_e(t.owner_name)} \\({_e(roles)}\\) "
                f"vendió {_usd(t.value)} el {_e(t.transaction_date)}"
            )

    if exit_score.politician_sells:
        seen: set = set()
        unique = [p for p in exit_score.politician_sells
                  if p.politician_name not in seen and not seen.add(p.politician_name)]
        lines.append(f"\n*Políticos vendiendo \\({_e(str(len(unique)))}\\):*")
        for p in unique[:3]:
            amt = f" ~ {_e(p.amount_range)}" if p.amount_range else ""
            lines.append(f"  • {_e(p.label)}{amt}")

    if exit_score.activist_reductions:
        lines.append(f"\n*Activistas reduciendo:*")
        for a in exit_score.activist_reductions[:2]:
            lines.append(f"  • {_e(a.filer_name)} \\({_e(a.filing_type)}\\)")

    if exit_score.short_interest:
        si = exit_score.short_interest
        rise = -si.decline_pct
        if rise >= 10:
            lines.append(
                f"\n*Short Interest:* subió {_e(f'{rise:.0f}%')} "
                f"\\(ahora {_e(f'{si.current_pct:.1f}%')} del float\\)"
            )

    if exit_score.unusual_puts:
        opt = exit_score.unusual_puts[0]
        lines.append(
            f"\n*PUTs inusuales:* strike {_e(str(opt.strike))} "
            f"exp {_e(opt.expiration)} \\| Vol/OI: *{_e(f'{opt.volume_oi_ratio:.1f}')}x*"
        )

    n_sources = len(exit_score.active_source_types)
    lines.append(
        f"\n*{_e(str(n_sources))} fuente{'s' if n_sources != 1 else ''} "
        f"independiente{'s' if n_sources != 1 else ''}* señalando salida"
    )

    lines.append(f"\n{_e(brief)}")
    lines.append(f"\n{_e(_DISCLAIMER_RAW)}")
    return "\n".join(lines)


# ── Price-only alert (portfolio position, no new signal) ─────────────────────

def send_price_only_alert(
    price_snapshot,      # PriceSnapshot
    position,            # portfolio.Position or None
    bot_token: str,
    chat_id: str,
    dry_run: bool = False,
) -> None:
    """
    Standalone price spike alert for a portfolio position that has no
    new scoring signal this cycle.
    """
    ps = price_snapshot
    strength = ps.spike_strength

    strength_labels = {
        "EXTREMO":    "MOVIMIENTO EXTREMO",
        "MUY_FUERTE": "Subida muy fuerte",
        "FUERTE":     "Subida fuerte",
    }
    label = strength_labels.get(strength, "Movimiento de precio")

    lines = [
        f"*{_e(ps.ticker)}* — {_e(label)}",
        f"*\\+{_e(f'{ps.pct_change_vs_close:.1f}%')} hoy* \\| "
        f"Vol: *{_e(f'{ps.volume_ratio:.1f}')}x* promedio \\| "
        f"${_e(f'{ps.current_price:.2f}')}",
        "",
    ]

    if position:
        unrealized_pct = ((ps.current_price - position.buy_price) / position.buy_price) * 100
        unrealized_usd = (ps.current_price - position.buy_price) * position.shares
        gain_str = f"\\+{unrealized_pct:.1f}%" if unrealized_pct >= 0 else f"{unrealized_pct:.1f}%"
        lines += [
            f"*Tu posición:* {_shares(position.shares)} acc @ {_usd(position.buy_price)}",
            f"*Ganancia no realizada:* {_usd(abs(unrealized_usd))} \\({gain_str}\\)",
        ]
        if position.notes:
            lines.append(f"*Nota original:* {_e(position.notes)}")
        lines.append("")

    lines += [
        f"*Apertura:* ${_e(f'{ps.open_price:.2f}')} \\| "
        f"*Máx hoy:* ${_e(f'{ps.day_high:.2f}')} \\| "
        f"*Mín hoy:* ${_e(f'{ps.day_low:.2f}')}",
        "",
        _e(_DISCLAIMER_RAW),
    ]

    message = "\n".join(lines)
    _send(message, bot_token, chat_id, dry_run,
          label=f"PriceSpike {ps.ticker} +{ps.pct_change_vs_close:.1f}%")


# ── Legacy: single signal (for backward compat) ───────────────────────────────

def send_signal(
    signal: "Signal",
    brief: str,
    bot_token: str,
    chat_id: str,
    dry_run: bool = False,
) -> None:
    txn = signal.transaction
    cluster_tag = "  \\[CLUSTER\\]" if signal.is_cluster else ""
    cluster_tag2 = "  \\+cluster bonus" if signal.is_cluster else ""

    lines = [
        f"*{_e(txn.ticker)}* — Compra Insider{cluster_tag}",
        f"▓░░░░ *BAJA/MEDIA* \\| sin score completo",
        "",
        f"*Insider:* {_e(txn.owner_name)} \\({_e(', '.join(txn.role_labels))}\\)",
        f"*Monto:* {_usd(txn.value)}  \\({_shares(txn.shares)} acc × {_e(f'${txn.price:,.2f}')}\\)",
        f"*Fecha:* {_e(txn.transaction_date)}",
        f"*Tenencia post:* {_shares(txn.shares_owned_following)} acc",
        "",
        _e(brief),
        "",
        _e(_DISCLAIMER_RAW),
    ]

    _send("\n".join(lines), bot_token, chat_id, dry_run,
          label=f"Signal {txn.ticker}")


# ── Legacy: confluence (for backward compat) ──────────────────────────────────

def send_confluence(csig, brief: str, bot_token: str, chat_id: str, dry_run: bool = False) -> None:
    """Route to send_signal using primary signal."""
    if csig.insider_signals:
        send_signal(csig.primary_signal, brief, bot_token, chat_id, dry_run)


# ── Transport ──────────────────────────────────────────────────────────────────

def _send(message: str, bot_token: str, chat_id: str, dry_run: bool, label: str = "") -> None:
    if dry_run:
        sep = "=" * 60
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
