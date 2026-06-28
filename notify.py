"""
Telegram notifier.

Two message types:
  1. send_signal()      — single insider purchase (plain signal)
  2. send_confluence()  — ticker bought by insiders + politicians (high-confidence)

Uses MarkdownV2 formatting. Supports dry_run mode.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from signals import ConfluenceSignal, Signal

import requests

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
DISCLAIMER = "_Idea para investigar basada en datos públicos de SEC EDGAR y divulgaciones del Congreso\\. No es recomendación de inversión\\._"

_MDV2_SPECIAL = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")


def _esc(text: str) -> str:
    return _MDV2_SPECIAL.sub(r"\\\1", str(text))


def _fmt_usd(value: float) -> str:
    return _esc(f"${value:,.0f}")


def _fmt_shares(shares: float) -> str:
    return _esc(f"{shares:,.0f}")


# ── Single insider signal ──────────────────────────────────────────────────────

def _build_signal_message(signal: "Signal", brief: str) -> str:
    txn = signal.transaction
    cluster_tag = "  \\[CLUSTER\\]" if signal.is_cluster else ""

    lines = [
        f"*{_esc(txn.ticker)}* — Compra Insider{cluster_tag}",
        "",
        f"*Insider:* {_esc(txn.owner_name)} \\({_esc(', '.join(txn.role_labels))}\\)",
        f"*Monto:* {_fmt_usd(txn.value)}  \\({_fmt_shares(txn.shares)} acc × {_esc(f'${txn.price:,.2f}')}\\)",
        f"*Fecha:* {_esc(txn.transaction_date)}",
        f"*Tenencia post:* {_fmt_shares(txn.shares_owned_following)} acc",
    ]
    if signal.is_cluster:
        lines.append(f"*Cluster:* {_esc(str(signal.cluster_size))} insiders distintos en 7 días")

    filing_url = txn.filing_url or f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=4&CIK={txn.ticker}"
    lines += [
        "",
        _esc(brief),
        "",
        f"[{_esc('Ver filing en EDGAR')}]({filing_url})",
        "",
        DISCLAIMER,
    ]
    return "\n".join(lines)


def send_signal(
    signal: "Signal",
    brief: str,
    bot_token: str,
    chat_id: str,
    dry_run: bool = False,
) -> None:
    message = _build_signal_message(signal, brief)
    _send(message, bot_token, chat_id, dry_run,
          label=f"Signal {signal.transaction.ticker}")


# ── Confluence signal ──────────────────────────────────────────────────────────

def _confidence_header(confidence: str) -> str:
    mapping = {
        "MUY ALTA": "CONFLUENCIA — PROBABILIDAD MUY ALTA",
        "ALTA":     "CONFLUENCIA — PROBABILIDAD ALTA",
        "MEDIA":    "CONFLUENCIA — CLUSTER DE INSIDERS",
    }
    return mapping.get(confidence, f"CONFLUENCIA — {confidence}")


def _build_confluence_message(csig: "ConfluenceSignal", brief: str) -> str:
    header = _confidence_header(csig.confidence)

    lines = [
        f"*{_esc(csig.ticker)}* — {_esc(header)}",
        "",
    ]

    # Insiders section
    lines.append(f"*Insiders \\({_esc(str(csig.distinct_insiders))}\\):*")
    for sig in csig.insider_signals[:5]:   # cap at 5 to avoid huge messages
        txn = sig.transaction
        roles = ", ".join(txn.role_labels)
        lines.append(
            f"  • {_esc(txn.owner_name)} \\({_esc(roles)}\\) "
            f"compró {_fmt_usd(txn.value)} el {_esc(txn.transaction_date)}"
        )
    if len(csig.insider_signals) > 5:
        lines.append(f"  \\.\\.\\. y {_esc(str(len(csig.insider_signals) - 5))} más")

    lines.append("")

    # Politicians section
    if csig.politician_trades:
        lines.append(f"*Políticos \\({_esc(str(csig.distinct_politicians))}\\):*")
        seen_pols: set = set()
        for pt in csig.politician_trades[:5]:
            if pt.politician_name in seen_pols:
                continue
            seen_pols.add(pt.politician_name)
            amount = f" ~ {_esc(pt.amount_range)}" if pt.amount_range else ""
            date_str = f" el {_esc(pt.transaction_date)}" if pt.transaction_date else ""
            lines.append(
                f"  • {_esc(pt.label)} compró{amount}{date_str}"
            )
        if csig.distinct_politicians > 5:
            lines.append(f"  \\.\\.\\. y {_esc(str(csig.distinct_politicians - 5))} más")
        lines.append("")

    # Summary line
    total_buyers = csig.distinct_insiders + csig.distinct_politicians
    lines.append(
        f"*{_esc(str(total_buyers))} compradores en ventana de "
        f"{_esc(str(csig.window_days))} días*"
    )
    lines.append("")

    # Brief from LLM
    lines.append(_esc(brief))
    lines.append("")

    # Links
    primary = csig.primary_signal.transaction
    edgar_url = primary.filing_url or (
        f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=4&CIK={csig.ticker}"
    )
    links = [f"[{_esc('EDGAR')}]({edgar_url})"]
    if csig.politician_trades and csig.politician_trades[0].filing_url:
        links.append(f"[{_esc('Divulgación Congreso')}]({csig.politician_trades[0].filing_url})")
    lines.append("  ".join(links))
    lines.append("")
    lines.append(DISCLAIMER)

    return "\n".join(lines)


def send_confluence(
    csig: "ConfluenceSignal",
    brief: str,
    bot_token: str,
    chat_id: str,
    dry_run: bool = False,
) -> None:
    message = _build_confluence_message(csig, brief)
    _send(message, bot_token, chat_id, dry_run,
          label=f"Confluence {csig.ticker} [{csig.confidence}]")


# ── Transport ──────────────────────────────────────────────────────────────────

def _send(
    message: str,
    bot_token: str,
    chat_id: str,
    dry_run: bool,
    label: str = "",
) -> None:
    if dry_run:
        print("\n" + "=" * 60)
        print(f"[DRY RUN] {label}")
        print(message)
        print("=" * 60)
        return

    if not bot_token or not chat_id:
        logger.warning("TELEGRAM credentials not set; skipping %s.", label)
        return

    url = TELEGRAM_API.format(token=bot_token)
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "MarkdownV2",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
    except Exception as exc:
        logger.error("Telegram send failed for %s: %s", label, exc)
