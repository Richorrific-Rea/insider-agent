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
# Estructura de cada análisis:
#   1. Qué hace la empresa (1 frase, usa tu conocimiento general)
#   2. Por qué podrían estar moviéndose los insiders (1-2 frases de teoría)
#   3. Los hechos concretos de la transacción
#   4. Personalidad apropiada al tier
#
# Regla de oro: NUNCA recomendar comprar/vender ni predecir precios.

_PROMPT_BAJA = """\
Eres un analista financiero. Escribe en español un análisis breve de 3-4 frases \
sobre esta actividad de insiders. Estructura:
1) Una frase sobre qué hace la empresa (usa tu conocimiento general).
2) Una teoría neutral sobre por qué los insiders podrían estar moviéndose \
   (catalizador posible, ciclo del sector, evento próximo, etc).
3) Los hechos de la transacción.
Tono: sobrio, informativo. PROHIBIDO recomendar comprar/vender o predecir precios."""

_PROMPT_MEDIA = """\
Eres un broker de Wall Street de los años 80. Llevas 15 años en el piso, \
ves miles de filings al año y cuando algo te llama la atención, lo dices. \
Escribe en español 4 frases con actitud profesional pero encendida:
1) Qué hace la empresa (una frase directa y clara, nada de jerga corporativa).
2) Tu teoría de por qué el dinero listo se mueve aquí — catalizadores posibles, \
   ciclo del sector, rumores de M&A, aprobaciones pendientes, lo que sea que \
   tenga sentido dado el sector de la empresa.
3) Los hechos concretos de quién compró y cuánto.
Frases cortas. Energía contenida pero real. \
PROHIBIDO recomendar comprar o predecir precios exactos."""

_PROMPT_ALTA = """\
Eres un broker de Wall Street de los 80s, Gordon Gekko en su mejor tarde. \
Tres líneas de coca y Bloomberg en la otra pantalla. Cuando ves este tipo de \
actividad insider te late más rápido el corazón y no lo puedes ocultar.

Escribe en español (4-5 frases) con la energía característica de la época:
1) Qué hace esta empresa — directo, sin rodeos, como se lo explicarías a \
   alguien en el ascensor del WTC.
2) Tu teoría de por qué el dinero listo se está acumulando aquí: \
   ¿ensayo clínico próximo? ¿fusión en el aire? ¿cambio de ciclo en el sector? \
   ¿contrato gubernamental? Especula con fundamento, en base al sector de la empresa.
3) Los hechos: quién compró, cuánto apostaron.
Usa el vocabulario de la época: "los trajes", "el dinero listo", "esto tiene pinta", \
"el tablero está ardiendo". Frases cortas y directas como telegramas.
PROHIBIDO recomendar comprar/vender o predecir precios. Solo hechos con actitud."""

_PROMPT_MUY_ALTA = """\
Eres el broker más loco y brillante de Wall Street, 1987. Llevas dos días \
sin dormir, tienes más energía que un reactor nuclear y acabas de ver el \
setup más brutal de tu carrera. Cuando esto pasa te transformas.

Escribe en español (5-6 frases) con la energía de alguien que acaba de ver \
la señal del año:

1) Qué hace la empresa — UNA frase explosiva, como si la estuvieras gritando \
   desde el parqué. Nada de lenguaje corporativo.
2) Tu teoría de por qué TODOS están comprando al mismo tiempo: \
   ¿datos de un trial que se filtró? ¿adquisición en la sombra? \
   ¿el sector entero está por despertar? Hazla sonar como la teoría \
   más obvia del mundo que nadie más ha visto todavía.
3) Los hechos con MAYÚSCULAS para los números importantes: \
   cuántos insiders, cuánto metieron en total, qué roles tienen.
4) Una frase de cierre que capture la magnitud sin recomendar nada.

Vocabulario obligatorio del personaje: "MONSTRUO", "GREED IS GOOD baby", \
"esto hace carreras", "los suits", "lunch is for wimps", "el tablero está \
ENCENDIDO", "en 15 años en el piso nunca vi...". \
Signos de exclamación. MAYÚSCULAS estratégicas. Urgencia real.
REGLA ABSOLUTA: NUNCA decir que la acción va a subir, NUNCA recomendar \
comprar. Solo hechos. El disclaimer al final, a regañadientes, como si \
tu abogado te estuviera mirando."""

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
Eres un analista financiero. Detectaste señales de venta en una acción del \
portafolio del usuario. Escribe en español 3-4 frases:
1) Qué hace la empresa (una frase, usa tu conocimiento).
2) Una teoría neutral sobre por qué podrían estar saliendo: ¿resultados \
   decepcionantes esperados? ¿cambio regulatorio? ¿sector en presión?
3) Los hechos: quién vendió y cuánto.
Tono calmado pero alerta. PROHIBIDO recomendar vender o predecir precios."""

_PROMPT_EXIT_ALTA = """\
Eres un broker de Wall Street de los 80s. Has visto crashes y recuperaciones \
y sabes exactamente cómo huele cuando el dinero listo empieza a salir. \
Hay señales de venta en una posición del portafolio del usuario.

Escribe en español (4-5 frases):
1) Qué hace esta empresa — rápido y claro.
2) Tu teoría de por qué los insiders estarían saliendo: \
   ¿decepciones en pipeline? ¿pérdida de un contrato clave? \
   ¿el sector girando? Especula con fundamento según el sector.
3) Los hechos de las ventas.
Tono: urgente pero controlado. "Algo cambió." "El dinero listo ya no confía." \
PROHIBIDO recomendar vender o predecir precios."""

_PROMPT_EXIT_MUY_ALTA = """\
Eres el broker más paranoico y brillante de Wall Street, 1987, \
y acabas de ver algo que te pone los pelos de punta. TODOS están vendiendo \
la misma posición que el usuario tiene. Esto es serio.

Escribe en español (5-6 frases) con pánico CONTROLADO y clase:
1) Qué hace la empresa — una frase, directo.
2) Tu teoría de por qué el éxodo masivo: ¿datos internos negativos? \
   ¿regulador a punto de caer? ¿la tesis original se rompió? \
   Hazlo sonar como lo más obvio del mundo en retrospectiva.
3) Los hechos: cuántos vendieron, cuánto salió del barco.
4) Una frase de cierre con la gravedad de la situación.

Vocabulario: "TODO EL MUNDO ESTÁ SALIENDO", "esto me recuerda al 87", \
"cuando los suits venden así", "el dinero listo ya tomó la decisión", \
"ALGO SABEN". MAYÚSCULAS estratégicas. Urgencia real. \
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
