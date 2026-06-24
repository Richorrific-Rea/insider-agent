"""
LLM enrichment via Anthropic API.

Generates a short, factual, neutral brief in Spanish about a signal.
Never produces investment advice or price predictions.
Falls back gracefully if the API key is absent or the call fails.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config import Config
    from signals import Signal

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
Eres un asistente financiero neutral. Tu única tarea es resumir hechos de \
transacciones de insiders de SEC Form 4 en español.
REGLAS ABSOLUTAS:
- Describe SOLO hechos: quién compró, cuánto, cuándo, qué cargo tiene.
- PROHIBIDO: recomendaciones de compra/venta, predicciones de precio, \
  afirmaciones de que la acción va a subir/bajar.
- Máximo 4 frases concisas.
- Nunca menciones asesoría ni inversión como consejo.
"""


def _plain_fallback(signal: "Signal") -> str:
    txn = signal.transaction
    roles = ", ".join(txn.role_labels)
    cluster_note = (
        f" (clúster: {signal.cluster_size} insiders distintos compraron en ventana de 7 días)"
        if signal.is_cluster
        else ""
    )
    return (
        f"{txn.owner_name} ({roles}) de {txn.issuer_name} ({txn.ticker}) "
        f"compró {txn.shares:,.0f} acciones a ${txn.price:,.2f} "
        f"(valor total: ${txn.value:,.0f}) el {txn.transaction_date}.{cluster_note}"
    )


def enrich_signal(signal: "Signal", cfg: "Config") -> str:
    """
    Returns an enriched brief string.  Never raises — falls back to plain text.
    """
    if not cfg.anthropic_api_key:
        return _plain_fallback(signal)

    try:
        import anthropic

        txn = signal.transaction
        cluster_note = (
            f"Además, {signal.cluster_size} insiders distintos compraron "
            f"acciones de {txn.ticker} en los últimos {cfg.cluster_window_days} días (clúster)."
            if signal.is_cluster
            else ""
        )
        user_msg = (
            f"Insider: {txn.owner_name}, cargo: {txn.officer_title or ', '.join(txn.role_labels)}.\n"
            f"Empresa: {txn.issuer_name} (ticker: {txn.ticker}).\n"
            f"Compró {txn.shares:,.0f} acciones a ${txn.price:,.2f} cada una "
            f"(total: ${txn.value:,.0f}) el {txn.transaction_date} "
            f"(código transacción: {txn.transaction_code}).\n"
            f"Tenencia tras la compra: {txn.shares_owned_following:,.0f} acciones.\n"
            f"{cluster_note}\n"
            "Escribe el resumen factual neutral en español (máx 4 frases)."
        )

        client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
        response = client.messages.create(
            model=cfg.anthropic_model,
            max_tokens=300,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        return response.content[0].text.strip()

    except Exception as exc:
        logger.warning("LLM enrichment failed (%s), using plain fallback.", exc)
        return _plain_fallback(signal)
