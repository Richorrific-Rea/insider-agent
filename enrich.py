"""
LLM enrichment — multi-provider.

Proveedores soportados vía LLM_PROVIDER:
  anthropic  — Claude (default)
  openai     — GPT-4o, GPT-4o-mini, etc.
  groq       — llama-3.1-70b, mixtral (free tier)
  gemini     — gemini-1.5-flash (free tier)
  ollama     — modelos locales (sin internet, sin costo)
  custom     — cualquier endpoint OpenAI-compatible (LLM_BASE_URL)

Personalidad escala con el tier de la señal:
  BAJA     → analista sobrio y metódico
  MEDIA    → broker interesado, empieza a emocionarse
  ALTA     → broker de los 80 con energía alta
  MUY ALTA → broker de los 80 completamente desatado

Nunca da recomendaciones de inversión. Solo hechos — con actitud.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config import Config
    from scorer import TierScore
    from signals import ConfluenceSignal, Signal

logger = logging.getLogger(__name__)

# ── System prompts por tier ────────────────────────────────────────────────────

_PROMPT_BAJA = """\
Eres un analista financiero sobrio y metódico. Tu trabajo es resumir hechos \
de transacciones de insiders de SEC Form 4 en español.
REGLAS: describe solo hechos (quién compró, cuánto, cuándo, qué cargo tiene). \
Máximo 3 frases concisas. PROHIBIDO: recomendaciones, predicciones de precio."""

_PROMPT_MEDIA = """\
Eres un analista financiero experimentado con ojo para las oportunidades. \
Cuando ves actividad de insiders interesante, lo notas con calma profesional.
Describe los hechos en español (3-4 frases): quién compró, cuánto, qué patrón ves. \
Tono: informado, con cierto interés. PROHIBIDO recomendar comprar o predecir precios."""

_PROMPT_ALTA = """\
Eres un broker de Wall Street de los años 80, directo y con energía. \
Llevas 15 años en el piso y cuando el dinero listo se mueve, lo hueles.
Describe los HECHOS de estas transacciones en español (4 frases) con convicción \
y estilo de la época: directo, urgente, confiado. Usa frases como "el dinero listo \
se está moviendo", "los trajes están comprando", "esto tiene pinta seria".
PROHIBIDO decir que la acción va a subir o recomendar comprar. Solo hechos con actitud."""

_PROMPT_MUY_ALTA = """\
Eres un broker de Wall Street de los años 80 en tu MEJOR momento. \
Trabajaste con Milken, sobreviviste el crash del 87 y sabes reconocer un setup \
épico cuando lo ves. Cuando la convergencia de señales es tan fuerte como esta, \
pierdes la compostura de la mejor manera posible.

Describe los HECHOS en español (4-5 frases) con energía MÁXIMA: \
usa MAYÚSCULAS para énfasis, jerga de la época ('esto es un MONSTRUO', \
'GREED IS GOOD baby', 'el tablero está encendido', 'esto hace carreras', \
'los suits, los políticos y los activistas todos apuntando en la misma dirección'), \
exclamaciones, urgencia real.

REGLA DE ORO: solo describes HECHOS (quién compró, cuánto, cuántas fuentes distintas \
confirman el patrón). ABSOLUTAMENTE PROHIBIDO decir que la acción va a subir, \
recomendar comprar o dar asesoría financiera. \
Al final, un disclaimer breve y a regañadientes."""

_PROMPTS = {
    "BAJA":     _PROMPT_BAJA,
    "MEDIA":    _PROMPT_MEDIA,
    "ALTA":     _PROMPT_ALTA,
    "MUY ALTA": _PROMPT_MUY_ALTA,
}

# ── Plain-text fallbacks ───────────────────────────────────────────────────────

def _fallback_tier(ts: "TierScore") -> str:
    n_insiders = len(ts.insider_signals)
    n_pols = len({p.politician_name for p in ts.politician_trades})
    n_activists = len(ts.activist_filings)
    parts = []
    if n_insiders:
        parts.append(f"{n_insiders} insider(s) compraron")
    if n_pols:
        parts.append(f"{n_pols} político(s) compraron")
    if n_activists:
        parts.append(f"{n_activists} activista(s) registraron posición")
    if ts.short_interest and ts.short_interest.decline_pct >= 10:
        parts.append(f"short interest cayó {ts.short_interest.decline_pct:.0f}%")
    if ts.unusual_options:
        parts.append(f"opciones call inusuales detectadas")
    summary = ", ".join(parts) if parts else "actividad detectada"
    return (
        f"{ts.ticker} ({ts.issuer_name}): {summary}. "
        f"Score: {ts.total_score:.0f} — señal {ts.tier}."
    )


def _fallback_signal(signal: "Signal") -> str:
    txn = signal.transaction
    return (
        f"{txn.owner_name} ({', '.join(txn.role_labels)}) de {txn.issuer_name} "
        f"({txn.ticker}) compró {txn.shares:,.0f} acciones a ${txn.price:,.2f} "
        f"(total: ${txn.value:,.0f}) el {txn.transaction_date}."
    )


# ── Multi-provider LLM client ─────────────────────────────────────────────────

# Default models per provider
_DEFAULT_MODELS = {
    "anthropic": "claude-haiku-3-5",
    "openai":    "gpt-4o-mini",
    "groq":      "llama-3.1-70b-versatile",
    "gemini":    "gemini-1.5-flash",
    "ollama":    "llama3.2",
    "custom":    "gpt-4o-mini",
}

_GROQ_BASE    = "https://api.groq.com/openai/v1"
_GEMINI_BASE  = "https://generativelanguage.googleapis.com/v1beta/openai"
_OLLAMA_BASE  = "http://localhost:11434/v1"


def _has_llm(cfg: "Config") -> bool:
    """True if any LLM credentials are configured."""
    return bool(cfg.llm_api_key or cfg.anthropic_api_key)


def _call_llm(system: str, user: str, cfg: "Config") -> str:
    """
    Calls the configured LLM provider. Returns the text response.
    Raises on failure so callers can catch and fallback.
    """
    provider = cfg.llm_provider.lower()

    # Resolve effective API key and model
    api_key = cfg.llm_api_key or cfg.anthropic_api_key
    model   = cfg.llm_model or _DEFAULT_MODELS.get(provider, "gpt-4o-mini")

    # ── Anthropic (native SDK) ─────────────────────────────────────────────
    if provider == "anthropic":
        import anthropic as _ant
        client = _ant.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=model or cfg.anthropic_model,
            max_tokens=400,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return resp.content[0].text.strip()

    # ── OpenAI-compatible (openai, groq, gemini, ollama, custom) ──────────
    import openai as _oai

    base_urls = {
        "groq":   _GROQ_BASE,
        "gemini": _GEMINI_BASE,
        "ollama": cfg.llm_base_url or _OLLAMA_BASE,
        "custom": cfg.llm_base_url,
    }
    base_url = base_urls.get(provider) or cfg.llm_base_url or None

    # Ollama doesn't require a real API key
    if provider == "ollama" and not api_key:
        api_key = "ollama"

    client = _oai.OpenAI(api_key=api_key, base_url=base_url)
    resp = client.chat.completions.create(
        model=model,
        max_tokens=400,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    )
    return resp.choices[0].message.content.strip()


# ── TierScore enrichment (main path) ──────────────────────────────────────────

def enrich_tier_score(ts: "TierScore", cfg: "Config") -> str:
    """Generate a brief for a fully-scored TierScore. Never raises."""
    if not _has_llm(cfg):
        return _fallback_tier(ts)

    try:
        system   = _PROMPTS.get(ts.tier, _PROMPT_MEDIA)
        user_msg = _build_tier_user_msg(ts)
        return _call_llm(system, user_msg, cfg)

    except Exception as exc:
        logger.warning("LLM enrichment failed (%s), using fallback.", exc)
        return _fallback_tier(ts)


def _build_tier_user_msg(ts: "TierScore") -> str:
    lines = [
        f"Ticker: {ts.ticker} ({ts.issuer_name})",
        f"Score: {ts.total_score:.0f} pts — Señal {ts.tier}",
        f"Fuentes activas: {', '.join(ts.active_source_types)}",
        "",
    ]

    if ts.insider_signals:
        lines.append("INSIDERS:")
        for s in ts.insider_signals[:5]:
            t = s.transaction
            lines.append(
                f"  - {t.owner_name} ({', '.join(t.role_labels)}): "
                f"${t.value:,.0f} el {t.transaction_date}"
            )

    if ts.politician_trades:
        lines.append("POLITICOS:")
        seen = set()
        for p in ts.politician_trades[:5]:
            if p.politician_name not in seen:
                seen.add(p.politician_name)
                lines.append(f"  - {p.label}: {p.amount_range or '?'} el {p.transaction_date}")

    if ts.activist_filings:
        lines.append("ACTIVISTAS (13D/13G):")
        for a in ts.activist_filings[:3]:
            lines.append(
                f"  - {a.filer_name} ({a.filing_type}): {a.stake_pct:.1f}% stake el {a.filing_date}"
            )

    if ts.institutional_positions:
        lines.append("INSTITUCIONALES (13F):")
        for i in ts.institutional_positions[:3]:
            lines.append(f"  - {i.fund_name}: ${i.value_usd:,.0f} nueva posición")

    if ts.short_interest and ts.short_interest.decline_pct >= 10:
        si = ts.short_interest
        lines.append(
            f"SHORT INTEREST: cayó {si.decline_pct:.0f}% "
            f"(ahora {si.current_pct:.1f}% del float)"
        )

    if ts.unusual_options:
        opt = ts.unusual_options[0]
        lines.append(
            f"OPCIONES INUSUALES: {opt.option_type} strike {opt.strike} "
            f"exp {opt.expiration} | vol/OI: {opt.volume_oi_ratio:.1f}x"
        )

    lines.append("")
    lines.append("Escribe el análisis según tu personalidad y el nivel de señal.")
    return "\n".join(lines)


# ── Legacy: plain Signal enrichment (fallback path) ───────────────────────────

def enrich_signal(signal: "Signal", cfg: "Config") -> str:
    """For single signals not yet scored. Falls back to MEDIA personality."""
    if not _has_llm(cfg):
        return _fallback_signal(signal)

    try:
        txn = signal.transaction
        user_msg = (
            f"Insider: {txn.owner_name}, cargo: {txn.officer_title or ', '.join(txn.role_labels)}.\n"
            f"Empresa: {txn.issuer_name} ({txn.ticker}).\n"
            f"Compró {txn.shares:,.0f} acc a ${txn.price:,.2f} (total ${txn.value:,.0f}) "
            f"el {txn.transaction_date}.\n"
            f"Tenencia post: {txn.shares_owned_following:,.0f} acc.\n"
        )
        return _call_llm(_PROMPT_MEDIA, user_msg, cfg)
    except Exception as exc:
        logger.warning("LLM signal enrichment failed (%s).", exc)
        return _fallback_signal(signal)


# ── Legacy: confluence enrichment ─────────────────────────────────────────────

def enrich_confluence(csig: "ConfluenceSignal", cfg: "Config") -> str:
    """Kept for backward compat. Routes through enrich_signal."""
    if not csig.insider_signals:
        return ""
    return enrich_signal(csig.primary_signal, cfg)
