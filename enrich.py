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
    from signals import ConfluenceSignal, Signal

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


# ── Confluence enrichment ──────────────────────────────────────────────────────

def _confluence_fallback(csig: "ConfluenceSignal") -> str:
    primary = csig.primary_signal.transaction
    pol_names = ", ".join(
        {p.politician_name for p in csig.politician_trades}
    ) if csig.politician_trades else ""
    pol_note = f" Políticos comprando: {pol_names}." if pol_names else ""
    return (
        f"{csig.distinct_insiders} insider(s) de {primary.issuer_name} ({csig.ticker}) "
        f"compraron un total de ${csig.total_insider_value:,.0f} "
        f"en los últimos {csig.window_days} días.{pol_note}"
    )


def enrich_confluence(csig: "ConfluenceSignal", cfg: "Config") -> str:
    """
    Generates a brief for a confluence signal. Never raises.
    """
    if not cfg.anthropic_api_key:
        return _confluence_fallback(csig)

    try:
        import anthropic

        primary = csig.primary_signal.transaction
        insider_lines = "\n".join(
            f"  - {s.transaction.owner_name} ({', '.join(s.transaction.role_labels)}): "
            f"${s.transaction.value:,.0f} el {s.transaction.transaction_date}"
            for s in csig.insider_signals[:5]
        )
        pol_lines = ""
        if csig.politician_trades:
            pol_lines = "\nPolíticos comprando el mismo ticker:\n" + "\n".join(
                f"  - {p.label}: {p.amount_range or 'monto no especificado'} el {p.transaction_date}"
                for p in csig.politician_trades[:5]
            )

        user_msg = (
            f"Empresa: {primary.issuer_name} (ticker: {csig.ticker}).\n"
            f"Nivel de confluencia: {csig.confidence}.\n\n"
            f"Insiders que compraron:\n{insider_lines}\n"
            f"{pol_lines}\n\n"
            "Resume en 4 frases factuales y neutrales en español. "
            "Describe quiénes compraron, cuánto y la coincidencia. "
            "PROHIBIDO: recomendaciones, predicciones de precio."
        )

        client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
        response = client.messages.create(
            model=cfg.anthropic_model,
            max_tokens=350,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        return response.content[0].text.strip()

    except Exception as exc:
        logger.warning("LLM confluence enrichment failed (%s), using fallback.", exc)
        return _confluence_fallback(csig)
