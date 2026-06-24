"""
Slack notifier.

Posts enriched signals to a Slack incoming webhook using Block Kit.
Supports dry_run mode (prints to stdout instead of posting).
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from signals import Signal

import requests

logger = logging.getLogger(__name__)

EDGAR_FILING_BASE = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=4&dateb=&owner=include&count=10&search_text=&CIK="
DISCLAIMER = "_Señal informativa de Form 4 (SEC EDGAR), no recomendación de inversión._"


def _filing_url(signal: "Signal") -> str:
    txn = signal.transaction
    if txn.filing_url:
        return txn.filing_url
    return f"{EDGAR_FILING_BASE}{txn.ticker}"


def _build_blocks(signal: "Signal", brief: str) -> list:
    txn = signal.transaction
    cluster_badge = " 🔗 CLÚSTER" if signal.is_cluster else ""
    header_text = f"*{txn.ticker}* — Compra Insider{cluster_badge}"

    meta_lines = [
        f"• *Insider:* {txn.owner_name} ({', '.join(txn.role_labels)})",
        f"• *Monto:* ${txn.value:,.0f}  ({txn.shares:,.0f} acc × ${txn.price:,.2f})",
        f"• *Fecha:* {txn.transaction_date}",
        f"• *Tenencia post:* {txn.shares_owned_following:,.0f} acciones",
    ]
    if signal.is_cluster:
        meta_lines.append(
            f"• *Clúster:* {signal.cluster_size} insiders distintos en ventana de 7 días"
        )

    filing_link = _filing_url(signal)

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"{txn.ticker} — Compra Insider{cluster_badge}", "emoji": False}},
        {"type": "section", "text": {"type": "mrkdwn", "text": header_text}},
        {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(meta_lines)}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Resumen:*\n{brief}"}},
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"<{filing_link}|Ver filing en SEC EDGAR>  |  {DISCLAIMER}"},
            ],
        },
        {"type": "divider"},
    ]
    return blocks


def send_signal(
    signal: "Signal",
    brief: str,
    webhook_url: str,
    dry_run: bool = False,
) -> None:
    """Post a single signal to Slack or print it in dry_run mode."""
    blocks = _build_blocks(signal, brief)
    payload = {"blocks": blocks}

    if dry_run:
        print("\n" + "=" * 60)
        print(f"[DRY RUN] Slack message for {signal.transaction.ticker}")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        print("=" * 60)
        return

    if not webhook_url:
        logger.warning("SLACK_WEBHOOK_URL not set; skipping notification for %s.", signal.transaction.ticker)
        return

    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        resp.raise_for_status()
    except Exception as exc:
        logger.error("Slack notification failed for %s: %s", signal.transaction.ticker, exc)
