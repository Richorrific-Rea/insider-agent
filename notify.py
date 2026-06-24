"""
Telegram notifier.

Sends enriched signals to a Telegram chat via the Bot API.
Uses MarkdownV2 formatting.
Supports dry_run mode (prints to stdout instead of sending).

Setup:
  1. Create a bot via @BotFather → get TELEGRAM_BOT_TOKEN
  2. Send any message to the bot (or add it to a group/channel)
  3. Get your chat ID:
     curl "https://api.telegram.org/bot<TOKEN>/getUpdates"
     Look for "chat":{"id": ...} in the response
  4. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in your .env
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from signals import Signal

import requests

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
EDGAR_BASE = "https://www.sec.gov/Archives/edgar/data/"
DISCLAIMER = "_Señal informativa de Form 4 \\(SEC EDGAR\\), no recomendación de inversión\\._"

# Characters that must be escaped in MarkdownV2
_MDV2_SPECIAL = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")


def _esc(text: str) -> str:
    """Escape a plain string for Telegram MarkdownV2."""
    return _MDV2_SPECIAL.sub(r"\\\1", str(text))


def _fmt_usd(value: float) -> str:
    return _esc(f"${value:,.0f}")


def _fmt_shares(shares: float) -> str:
    return _esc(f"{shares:,.0f}")


def _filing_link(signal: "Signal") -> str:
    txn = signal.transaction
    url = txn.filing_url or f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=4&CIK={txn.ticker}"
    label = _esc("Ver filing en SEC EDGAR")
    return f"[{label}]({url})"


def _build_message(signal: "Signal", brief: str) -> str:
    txn = signal.transaction
    cluster_line = (
        f"\n*Cluster:* {_esc(str(signal.cluster_size))} insiders distintos en {_esc('7')} días"
        if signal.is_cluster
        else ""
    )

    cluster_tag = "  \\[CLUSTER\\]" if signal.is_cluster else ""
    lines = [
        f"*{_esc(txn.ticker)}* — Compra Insider{cluster_tag}",
        "",
        f"*Insider:* {_esc(txn.owner_name)} \\({_esc(', '.join(txn.role_labels))}\\)",
        f"*Monto:* {_fmt_usd(txn.value)}  \\({_fmt_shares(txn.shares)} acc × {_esc(f'${txn.price:,.2f}')}\\)",
        f"*Fecha:* {_esc(txn.transaction_date)}",
        f"*Tenencia post:* {_fmt_shares(txn.shares_owned_following)} acc",
    ]
    if cluster_line:
        lines.append(cluster_line)

    lines += [
        "",
        _esc(brief),
        "",
        _filing_link(signal),
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
    """Send a single signal to Telegram, or print it in dry_run mode."""
    message = _build_message(signal, brief)

    if dry_run:
        print("\n" + "=" * 60)
        print(f"[DRY RUN] Telegram message for {signal.transaction.ticker}")
        print(message)
        print("=" * 60)
        return

    if not bot_token or not chat_id:
        logger.warning(
            "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set; "
            "skipping notification for %s.",
            signal.transaction.ticker,
        )
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
        logger.error(
            "Telegram notification failed for %s: %s",
            signal.transaction.ticker,
            exc,
        )
