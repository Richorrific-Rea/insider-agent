"""
Telegram bot listener — conversational interface for portfolio management.

Polls getUpdates every 3 seconds and handles natural language messages.
All parsing is done via LLM — no regex, handles ambiguous input gracefully.

Supported interactions (all in natural language):
  "compré 50 de Apple a 185"     → adds AAPL to portfolio
  "vendí Tesla"                  → removes TSLA from portfolio
  "vigila Nvidia"                → adds NVDA to watchlist
  "portafolio" / "mis acciones"  → shows portfolio
  "watchlist"                    → shows watchlist
  "ayuda"                        → shows available commands

The LLM:
  - Maps company names to tickers (Apple → AAPL, "la de los chips" → NVDA)
  - Detects missing info and asks for it
  - Responds in the same casual tone as the user's message
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"

# ── Parse prompt ──────────────────────────────────────────────────────────────

# Language codes → display names (for confirmation messages)
_LANGUAGE_NAMES = {
    "es": "español",
    "en": "English",
    "fr": "français",
    "pt": "português",
    "de": "Deutsch",
}

_LANGUAGE_INSTRUCTIONS = {
    "es": "Responde SIEMPRE en español, sin importar en qué idioma te escriba el usuario.",
    "en": "Always respond in English, regardless of what language the user writes in.",
    "fr": "Réponds TOUJOURS en français, quelle que soit la langue utilisée par l'utilisateur.",
    "pt": "Responda SEMPRE em português, independentemente do idioma que o usuário usar.",
    "de": "Antworte IMMER auf Deutsch, unabhängig davon, in welcher Sprache der Benutzer schreibt.",
}

_PARSE_SYSTEM_TEMPLATE = """\
You are the assistant for a stock alert system. The user sends messages \
to manage their portfolio. Your job is to interpret the message and return \
a JSON with the action to perform.

{language_instruction}

POSSIBLE ACTIONS:
- buy: user bought shares
- sell: user sold or wants to exit a position
- watch: user wants to monitor a stock price
- unwatch: user wants to stop monitoring
- portfolio: wants to see their portfolio
- watchlist: wants to see their watchlist
- language: user is asking to change the language of the bot
- help: asks for help
- unknown: you don't understand the message

RESPONSE FORMAT (always JSON, no extra text):
{{
  "action": "buy|sell|watch|unwatch|portfolio|watchlist|language|help|unknown",
  "ticker": "SYMBOL_IN_UPPERCASE_or_null",
  "shares": number_or_null,
  "price": number_or_null,
  "missing": ["shares", "price"],
  "language": "es|en|fr|pt|de|null",
  "message": "short conversational reply to the user"
}}

RULES:
- Map company names to tickers: Apple→AAPL, Nvidia→NVDA, Tesla→TSLA, Google/Alphabet→GOOGL, etc.
- Understand nicknames: "Elon's company"→TSLA, "la de los chips"→NVDA, "la de los iPhones"→AAPL.
- If info is missing (shares or price for buy), put it in "missing" and ask only for what's needed.
- "message" must be short and conversational — like a WhatsApp message, no formalities.
- For buy/sell without a clear ticker, return ticker: null and ask for clarification.
- NEVER give investment advice or opinions about whether to buy/sell anything.
- For "language" action: detect which language the user wants and put it in the "language" field.
  Examples: "habla en inglés"→en, "speak Spanish"→es, "parle français"→fr,
  "habla español"→es, "en inglés por favor"→en.
"""


def _build_parse_system(lang: str) -> str:
    """Build the parse system prompt with the given language instruction."""
    instruction = _LANGUAGE_INSTRUCTIONS.get(lang, _LANGUAGE_INSTRUCTIONS["es"])
    return _PARSE_SYSTEM_TEMPLATE.format(language_instruction=instruction)

# ── Telegram API helpers ──────────────────────────────────────────────────────

def _tg(token: str, method: str, **data) -> dict:
    url = TELEGRAM_API.format(token=token, method=method)
    try:
        resp = requests.post(url, json=data, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.error("Telegram API error (%s): %s", method, exc)
        return {}


def _send(token: str, chat_id: str, text: str) -> None:
    _tg(token, "sendMessage", chat_id=chat_id, text=text)


def _get_updates(token: str, offset: int) -> list:
    result = _tg(token, "getUpdates", offset=offset, timeout=20, limit=10)
    return result.get("result", [])


# ── LLM parsing ───────────────────────────────────────────────────────────────

def _parse_message(text: str, cfg, current_lang: str = "es") -> dict:
    """
    Sends user message to LLM and returns parsed intent as a dict.
    Falls back to {"action": "unknown"} on any error.
    """
    try:
        from enrich import _call_llm
        system = _build_parse_system(current_lang)
        raw = _call_llm(system, text, cfg)
        # Extract JSON from response (LLM sometimes adds markdown fences)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except Exception as exc:
        logger.warning("LLM parse failed: %s", exc)
        return {"action": "unknown", "message": "No entendí bien eso. Intenta: 'compré 50 de Apple a 185'"}


# ── Command handlers ──────────────────────────────────────────────────────────

def _handle_buy(parsed: dict, chat_id: str, token: str, cfg) -> None:
    from portfolio import PortfolioStore
    from notify import _fmt_money

    ticker = parsed.get("ticker")
    shares = parsed.get("shares")
    price  = parsed.get("price")
    missing = parsed.get("missing", [])

    if not ticker:
        _send(token, chat_id, parsed.get("message", "¿Qué acción compraste?"))
        return

    if missing:
        _send(token, chat_id, parsed.get("message", f"¿Cuántas acciones de {ticker} y a qué precio?"))
        return

    store = PortfolioStore(path=cfg.state_file_path)
    pos = store.add_position(ticker, float(shares), float(price))
    total = pos.shares * pos.buy_price

    _send(
        token, chat_id,
        f"✓ Listo. {ticker} en tu portafolio.\n"
        f"{pos.shares:,.0f} acc @ ${pos.buy_price:,.2f} = {_fmt_money(total)}\n"
        f"Te aviso si algo cambia."
    )


def _handle_sell(parsed: dict, chat_id: str, token: str, cfg) -> None:
    from portfolio import PortfolioStore

    ticker = parsed.get("ticker")
    if not ticker:
        _send(token, chat_id, parsed.get("message", "¿Qué acción vendiste?"))
        return

    store = PortfolioStore(path=cfg.state_file_path)
    if store.remove_position(ticker):
        _send(token, chat_id, f"✓ {ticker} removido del portafolio y watchlist.")
    else:
        _send(token, chat_id, f"No tenías {ticker} en el portafolio.")


def _handle_watch(parsed: dict, chat_id: str, token: str, cfg) -> None:
    from portfolio import PortfolioStore

    ticker = parsed.get("ticker")
    if not ticker:
        _send(token, chat_id, parsed.get("message", "¿Qué acción quieres monitorear?"))
        return

    store = PortfolioStore(path=cfg.state_file_path)
    if store.watch(ticker):
        _send(token, chat_id, f"✓ {ticker} en la watchlist. Te aviso si sube ≥7% en un día.")
    else:
        _send(token, chat_id, f"{ticker} ya estaba en la watchlist.")


def _handle_unwatch(parsed: dict, chat_id: str, token: str, cfg) -> None:
    from portfolio import PortfolioStore

    ticker = parsed.get("ticker")
    if not ticker:
        _send(token, chat_id, parsed.get("message", "¿Cuál quieres quitar de la watchlist?"))
        return

    store = PortfolioStore(path=cfg.state_file_path)
    if store.unwatch(ticker):
        _send(token, chat_id, f"✓ {ticker} quitado de la watchlist.")
    else:
        _send(token, chat_id, f"{ticker} no estaba en la watchlist.")


def _handle_portfolio(chat_id: str, token: str, cfg) -> None:
    from portfolio import PortfolioStore
    from notify import _fmt_money

    store = PortfolioStore(path=cfg.state_file_path)
    positions = store.get_positions()

    if not positions:
        _send(token, chat_id, "Portafolio vacío. Dime qué compraste y lo agrego.")
        return

    lines = [f"Portafolio ({len(positions)} posición{'es' if len(positions) != 1 else ''})\n"]
    for p in positions:
        lines.append(f"• {p.ticker}  {p.shares:,.0f} acc @ ${p.buy_price:,.2f}")
        if p.notes:
            lines.append(f"  {p.notes}")
    _send(token, chat_id, "\n".join(lines))


def _handle_watchlist(chat_id: str, token: str, cfg) -> None:
    from portfolio import PortfolioStore

    store = PortfolioStore(path=cfg.state_file_path)
    wl = store.get_watchlist()

    if not wl:
        _send(token, chat_id, "Watchlist vacía. Di 'vigila NVDA' para agregar.")
        return

    _send(token, chat_id, f"Watchlist: {', '.join(wl)}\nAlerta cuando suban ≥7% en un día.")


def _handle_help(chat_id: str, token: str, lang: str = "es") -> None:
    msgs = {
        "es": (
            "Qué puedo hacer:\n\n"
            "• \"compré 50 de Apple a 185\" → agrega al portafolio\n"
            "• \"vendí Tesla\" → quita del portafolio\n"
            "• \"vigila Nvidia\" → agrega a watchlist\n"
            "• \"portafolio\" → ver tus posiciones\n"
            "• \"watchlist\" → ver lo que monitoreas\n"
            "• \"habla en inglés\" → cambiar idioma\n\n"
            "Escribe como quieras, entiendo lenguaje natural."
        ),
        "en": (
            "What I can do:\n\n"
            "• \"I bought 50 Apple at 185\" → adds to portfolio\n"
            "• \"I sold Tesla\" → removes from portfolio\n"
            "• \"watch Nvidia\" → adds to watchlist\n"
            "• \"portfolio\" → see your positions\n"
            "• \"watchlist\" → see what you're monitoring\n"
            "• \"speak Spanish\" → change language\n\n"
            "Write naturally, I understand free text."
        ),
    }
    _send(token, chat_id, msgs.get(lang, msgs["es"]))


def _handle_language(parsed: dict, chat_id: str, token: str) -> Optional[str]:
    """
    Handles a language change request.
    Returns the new language code, or None if not recognized.
    """
    new_lang = parsed.get("language")
    if not new_lang or new_lang not in _LANGUAGE_NAMES:
        _send(token, chat_id,
              "No entendí qué idioma quieres. Puedes pedir: español, inglés, francés, portugués, alemán.")
        return None

    confirmations = {
        "es": "Perfecto, a partir de ahora te hablo en español.",
        "en": "Got it, I'll speak English from now on.",
        "fr": "Parfait, je vais parler français à partir de maintenant.",
        "pt": "Perfeito, vou falar português a partir de agora.",
        "de": "Verstanden, ich spreche ab jetzt auf Deutsch.",
    }
    _send(token, chat_id, confirmations[new_lang])
    logger.info("Language changed to: %s", new_lang)
    return new_lang


# ── Main message router ───────────────────────────────────────────────────────

def _route(update: dict, token: str, cfg, current_lang: str = "es") -> Optional[str]:
    """
    Routes an incoming Telegram update to the appropriate handler.
    Returns the new language code if it was changed, otherwise None.
    """
    message = update.get("message") or update.get("edited_message")
    if not message:
        return None

    chat_id = str(message.get("chat", {}).get("id", ""))
    text = (message.get("text") or "").strip()

    if not chat_id or not text:
        return None

    # Only respond to the configured chat
    if cfg.telegram_chat_id and chat_id != str(cfg.telegram_chat_id):
        logger.debug("Message from unknown chat %s — ignoring", chat_id)
        return None

    logger.info("Incoming [%s]: %r", current_lang, text[:80])

    parsed = _parse_message(text, cfg, current_lang)
    action = parsed.get("action", "unknown")

    handlers = {
        "buy":       lambda: _handle_buy(parsed, chat_id, token, cfg),
        "sell":      lambda: _handle_sell(parsed, chat_id, token, cfg),
        "watch":     lambda: _handle_watch(parsed, chat_id, token, cfg),
        "unwatch":   lambda: _handle_unwatch(parsed, chat_id, token, cfg),
        "portfolio": lambda: _handle_portfolio(chat_id, token, cfg),
        "watchlist": lambda: _handle_watchlist(chat_id, token, cfg),
        "help":      lambda: _handle_help(chat_id, token, current_lang),
    }

    if action == "language":
        return _handle_language(parsed, chat_id, token)

    handler = handlers.get(action)
    if handler:
        handler()
    else:
        fallback = parsed.get("message") or (
            "No entendí eso. Escribe 'ayuda' para ver qué puedo hacer."
            if current_lang == "es" else
            "I didn't understand that. Type 'help' to see what I can do."
        )
        _send(token, chat_id, fallback)

    return None


# ── Polling loop ──────────────────────────────────────────────────────────────

class TelegramListener:
    """
    Long-polling listener. Runs in a background thread.
    Call start() to begin, stop() to shutdown gracefully.
    """

    def __init__(self, cfg):
        self._cfg      = cfg
        self._token    = cfg.telegram_bot_token
        self._stop     = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # Language persists across messages; default from config (es)
        self._lang     = getattr(cfg, "bot_language", "es")
        if self._lang not in _LANGUAGE_NAMES:
            self._lang = "es"
        self._lang = self._load_saved_lang()

    def _lang_file(self) -> str:
        import os
        base = getattr(self._cfg, "state_file_path", "state.json")
        return base.replace(".json", "_lang.txt")

    def _load_saved_lang(self) -> str:
        """Load persisted language preference from disk."""
        import os
        path = self._lang_file()
        if os.path.exists(path):
            try:
                with open(path) as f:
                    saved = f.read().strip()
                if saved in _LANGUAGE_NAMES:
                    return saved
            except Exception:
                pass
        return self._lang

    def _save_lang(self, lang: str) -> None:
        """Persist language preference to disk."""
        try:
            with open(self._lang_file(), "w") as f:
                f.write(lang)
        except Exception as exc:
            logger.warning("Could not save language preference: %s", exc)

    def start(self) -> None:
        if not self._token:
            logger.warning("TELEGRAM_BOT_TOKEN not set — listener disabled.")
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="tg-listener")
        self._thread.start()
        logger.info("Telegram listener started (lang=%s).", self._lang)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Telegram listener stopped.")

    def _run(self) -> None:
        offset = 0
        while not self._stop.is_set():
            try:
                updates = _get_updates(self._token, offset)
                for update in updates:
                    try:
                        new_lang = _route(update, self._token, self._cfg, self._lang)
                        if new_lang and new_lang != self._lang:
                            self._lang = new_lang
                            self._save_lang(new_lang)
                            logger.info("Language switched to: %s", new_lang)
                    except Exception as exc:
                        logger.error("Error handling update: %s", exc)
                    offset = update["update_id"] + 1
            except Exception as exc:
                logger.warning("getUpdates error: %s", exc)
            time.sleep(3)
