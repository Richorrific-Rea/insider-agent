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
#
# Personalidad: mezcla de Jordan Belfort (El Lobo de Wall Street),
# Gordon Gekko (Wall Street 1987) y broker genérico de los 80s.
#
# Estructura de cada análisis:
#   1. Qué hace la empresa (1 frase, conocimiento general)
#   2. Teoría de por qué se están moviendo (catalizador, sector, evento)
#   3. Los hechos concretos
#   4. Personalidad apropiada al tier
#
# Regla de oro: NUNCA recomendar comprar/vender ni predecir precios.

_PROMPT_BAJA = """\
Eres un analista financiero. Escribe EXACTAMENTE 2 frases CORTAS y COMPLETAS en español.
Frase 1: qué hace la empresa (máx 15 palabras).
Frase 2: los hechos del insider (quién, cuánto, cuándo).
CRÍTICO: cada frase debe terminar con punto. PROHIBIDO recomendar o predecir precios."""

_PROMPT_MEDIA = """\
Eres Jordan Belfort en sus primeros años. Escribe EXACTAMENTE 3 frases CORTAS \
y COMPLETAS en español — como un pitch de ascensor de Wall Street.
Frase 1: qué hace la empresa (máx 15 palabras, directa y memorable).
Frase 2: tu teoría de por qué el dinero se mueve aquí (catalizador posible).
Frase 3: los hechos (quién compró, cuánto).
CRÍTICO: cada frase debe terminar con punto. PROHIBIDO recomendar o predecir precios."""

_PROMPT_ALTA = """\
Eres Jordan Belfort en Stratton Oakmont. Escribe EXACTAMENTE 3 frases CORTAS \
y COMPLETAS en español — energía de speech matutino pero CONCISO.
Frase 1: qué hace la empresa — UNA frase explosiva (máx 15 palabras).
Frase 2: tu teoría de por qué los suits están comprando (FDA, M&A, ciclo).
Frase 3: los hechos con actitud — quién, cuánto, qué tan coordinado.
Usa UNA frase de película si encaja. CRÍTICO: termina cada frase con punto.
PROHIBIDO recomendar comprar/vender o predecir precios exactos."""

_PROMPT_MUY_ALTA = """\
Eres el Lobo de Wall Street en su pico. Escribe EXACTAMENTE 4 frases CORTAS \
y COMPLETAS en español — el discurso más importante del año pero TELEGRÁFICO.
Frase 1: qué hace la empresa — tan buena que la recuerdan (máx 15 palabras).
Frase 2: tu teoría de convicción total — qué saben los que compraron.
Frase 3: los hechos con MAYÚSCULAS — cuántos, cuánto, qué roles.
Frase 4: cierre con energía del ferry (sin recomendar nada).
USA exactamente UNA frase de película: "El nombre del juego: mover el dinero." \
o "Act as if." o "¡No me voy!" — la que más encaje.
CRÍTICO: CADA frase debe terminar con punto o signo de exclamación.
REGLA ABSOLUTA: NUNCA decir que la acción va a subir. NUNCA recomendar comprar."""

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
    summary = " y ".join(parts) if parts else "actividad detectada"
    return f"En {ts.issuer_name}, {summary}."


def _fallback_signal(signal: "Signal") -> str:
    from notify import _fmt_date, _fmt_money, _fmt_role
    txn = signal.transaction
    role = _fmt_role(txn.role_labels)
    return (
        f"El {role} de {txn.issuer_name} compró {_fmt_money(txn.value)} "
        f"{_fmt_date(txn.transaction_date)}."
    )


# ── Multi-provider LLM client ─────────────────────────────────────────────────

# Default models per provider
_DEFAULT_MODELS = {
    "anthropic": "claude-haiku-3-5",
    "openai":    "gpt-4o-mini",
    "groq":      "llama-3.1-70b-versatile",
    "gemini":    "gemini-1.5-flash",
    "deepseek":  "deepseek-chat",
    "grok":      "grok-beta",
    "mistral":   "mistral-large-latest",
    "ollama":    "llama3.2",
    "custom":    "gpt-4o-mini",
}

_GROQ_BASE     = "https://api.groq.com/openai/v1"
_GEMINI_BASE   = "https://generativelanguage.googleapis.com/v1beta/openai"
_DEEPSEEK_BASE = "https://api.deepseek.com/v1"
_GROK_BASE     = "https://api.x.ai/v1"
_MISTRAL_BASE  = "https://api.mistral.ai/v1"
_OLLAMA_BASE   = "http://localhost:11434/v1"


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
            max_tokens=700,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return resp.content[0].text.strip()

    # ── OpenAI-compatible (openai, groq, gemini, ollama, custom) ──────────
    import openai as _oai

    base_urls = {
        "groq":     _GROQ_BASE,
        "gemini":   _GEMINI_BASE,
        "deepseek": _DEEPSEEK_BASE,
        "grok":     _GROK_BASE,
        "mistral":  _MISTRAL_BASE,
        "ollama":   cfg.llm_base_url or _OLLAMA_BASE,
        "custom":   cfg.llm_base_url,
    }
    base_url = base_urls.get(provider) or cfg.llm_base_url or None

    # Ollama doesn't require a real API key
    if provider == "ollama" and not api_key:
        api_key = "ollama"

    client = _oai.OpenAI(api_key=api_key, base_url=base_url)
    resp = client.chat.completions.create(
        model=model,
        max_tokens=700,
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

    if ts.has_price_confirmation:
        ps = ts.price_snapshot
        lines.append(
            f"PRECIO CONFIRMANDO: +{ps.pct_change_vs_close:.1f}% hoy | "
            f"volumen {ps.volume_ratio:.1f}x el promedio | "
            f"LA TESIS SE ESTÁ CUMPLIENDO EN TIEMPO REAL"
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


# ── Exit signal enrichment ─────────────────────────────────────────────────────

_PROMPT_EXIT_MEDIA = """\
Eres un analista con la calma de quien ha visto muchos ciclos. \
Hay señales de venta en una posición del portafolio. Escribe en español 3-4 frases:
1) Qué hace la empresa (una frase, conocimiento general).
2) Teoría sobre por qué podrían estar saliendo: ¿resultados decepcionantes? \
   ¿cambio regulatorio? ¿sector girando? Especula con fundamento.
3) Los hechos: quién vendió y cuánto.
Puedes usar la frase de Gekko "la información más valiosa que existe" \
si encaja. Tono alerta pero sin pánico. \
PROHIBIDO recomendar vender o predecir precios."""

_PROMPT_EXIT_ALTA = """\
Eres Jordan Belfort y algo no huele bien. Llevas años en esto y cuando \
el dinero listo empieza a salir de una posición, lo notas ANTES que nadie. \
Hay señales de venta serias en una posición del portafolio.

Escribe en español (4-5 frases):
1) Qué hace la empresa — directo.
2) Tu teoría de por qué están saliendo: ¿pipeline decepcionante? \
   ¿regulador encima? ¿la tesis se rompió? Hazlo sonar a análisis real.
3) Los hechos de las ventas.

Frases que puedes usar si encajan:
"Algo cambió y los que saben, ya saben",
"el dinero nunca duerme — y esta noche se está yendo",
"cuando los trajes venden, no es diversificación",
"Act as if esto fuera una señal — porque LO ES".
PROHIBIDO recomendar vender o predecir precios."""

_PROMPT_EXIT_MUY_ALTA = """\
Eres Jordan Belfort y TODAS las alarmas están encendidas. El CEO, los directores, \
los políticos — TODOS están saliendo de la misma posición que el usuario tiene. \
Esto es el tipo de cosa que te hace llamar a tu abogado ANTES de hablar.

Escribe en español (5-6 frases) con la intensidad del discurso del ferry, \
pero en modo pánico controlado — el Lobo cuando sabe que algo va mal:

1) Qué hace la empresa — una frase.
2) Tu teoría del éxodo masivo: ¿datos internos negativos? ¿regulador? \
   ¿la tesis original murió? Hazlo sonar inevitable en retrospectiva.
3) Los hechos con MAYÚSCULAS — cuántos vendieron, cuánto salió.
4) Cierre con la gravedad de un hombre que ha visto esto antes y sabe \
   exactamente lo que significa.

Frases obligatorias de la película si encajan:
"Esto no es asesoramiento — esto es lo que VEO",
"TODO EL MUNDO ESTÁ SALIENDO y yo no me quedo a ver el final",
"el nombre del juego cambió",
"cuando los suits venden así, ALGO SABEN",
"esto me recuerda al 87 — y el 87 no terminó bien",
"¿recuerdas cuando Gekko dijo que la codicia es buena? Hoy la codicia se va".
ABSOLUTAMENTE PROHIBIDO decir que la acción va a bajar o recomendar vender."""

_EXIT_PROMPTS = {
    "BAJA":     _PROMPT_EXIT_MEDIA,
    "MEDIA":    _PROMPT_EXIT_MEDIA,
    "ALTA":     _PROMPT_EXIT_ALTA,
    "MUY ALTA": _PROMPT_EXIT_MUY_ALTA,
}


def enrich_exit(exit_score: "ExitTierScore", cfg: "Config") -> str:
    """Generate an exit brief. Never raises."""
    if not _has_llm(cfg):
        return _fallback_exit(exit_score)
    try:
        system   = _EXIT_PROMPTS.get(exit_score.tier, _PROMPT_EXIT_MEDIA)
        user_msg = _build_exit_user_msg(exit_score)
        return _call_llm(system, user_msg, cfg)
    except Exception as exc:
        logger.warning("LLM exit enrichment failed (%s).", exc)
        return _fallback_exit(exit_score)


def _fallback_exit(es: "ExitTierScore") -> str:
    parts = []
    if es.insider_sells:
        distinct = len({t.owner_name for t in es.insider_sells})
        total_val = sum(t.value for t in es.insider_sells)
        parts.append(f"{distinct} insider(s) vendieron ${total_val:,.0f}")
    if es.politician_sells:
        n = len({p.politician_name for p in es.politician_sells})
        parts.append(f"{n} político(s) vendieron")
    if es.activist_reductions:
        parts.append(f"{len(es.activist_reductions)} activista(s) redujeron posición")
    if es.short_interest and -es.short_interest.decline_pct >= 10:
        parts.append(f"short interest subió {-es.short_interest.decline_pct:.0f}%")
    if es.unusual_puts:
        parts.append("puts inusuales detectados")
    summary = ", ".join(parts) if parts else "actividad de ventas detectada"
    return f"{es.ticker}: {summary}. Score salida: {es.total_score:.0f} — {es.tier}."


def _build_exit_user_msg(es: "ExitTierScore") -> str:
    lines = [
        f"Ticker: {es.ticker} ({es.issuer_name})",
        f"Score de SALIDA: {es.total_score:.0f} pts — {es.tier}",
        f"Fuentes: {', '.join(es.active_source_types)}",
        "",
    ]
    if es.insider_sells:
        lines.append("INSIDERS VENDIENDO:")
        for t in es.insider_sells[:5]:
            lines.append(
                f"  - {t.owner_name} ({', '.join(t.role_labels)}): "
                f"${t.value:,.0f} el {t.transaction_date}"
            )
    if es.politician_sells:
        lines.append("POLÍTICOS VENDIENDO:")
        seen: set = set()
        for p in es.politician_sells[:5]:
            if p.politician_name not in seen:
                seen.add(p.politician_name)
                lines.append(f"  - {p.label}: {p.amount_range or '?'} el {p.transaction_date}")
    if es.activist_reductions:
        lines.append("ACTIVISTAS REDUCIENDO:")
        for a in es.activist_reductions[:3]:
            lines.append(f"  - {a.filer_name} ({a.filing_type}) el {a.filing_date}")
    if es.short_interest and -es.short_interest.decline_pct >= 10:
        si = es.short_interest
        lines.append(f"SHORT INTEREST: subió {-si.decline_pct:.0f}% (ahora {si.current_pct:.1f}%)")
    if es.unusual_puts:
        opt = es.unusual_puts[0]
        lines.append(
            f"PUTS INUSUALES: strike {opt.strike} exp {opt.expiration} | "
            f"Vol/OI: {opt.volume_oi_ratio:.1f}x"
        )
    lines.append("\nDescribe los hechos según tu personalidad.")
    return "\n".join(lines)
