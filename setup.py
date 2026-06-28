"""
insider-agent setup wizard.

Guides the user through creating / updating .env with all required and
optional configuration values.  Validates inputs where possible and
auto-discovers the Telegram chat ID from the bot API.

Usage:
    python setup.py
"""
from __future__ import annotations

import os
import re
import sys
import textwrap

try:
    import requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

ENV_FILE = os.path.join(os.path.dirname(__file__), ".env")

# ── Terminal helpers ──────────────────────────────────────────────────────────

_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_DIM    = "\033[2m"
_GREEN  = "\033[32m"
_YELLOW = "\033[33m"
_CYAN   = "\033[36m"
_RED    = "\033[31m"

def _c(color: str, text: str) -> str:
    return f"{color}{text}{_RESET}" if sys.stdout.isatty() else text

def header(text: str) -> None:
    print(f"\n{_c(_BOLD + _CYAN, '▶ ' + text)}")

def info(text: str) -> None:
    print(f"  {_c(_DIM, text)}")

def ok(text: str) -> None:
    print(f"  {_c(_GREEN, '✓')} {text}")

def warn(text: str) -> None:
    print(f"  {_c(_YELLOW, '!')} {text}")

def error(text: str) -> None:
    print(f"  {_c(_RED, '✗')} {text}")

def ask(prompt: str, default: str = "", secret: bool = False) -> str:
    """Prompt the user for input, showing the default value."""
    display_default = ("***" if secret and default else default)
    suffix = f" [{display_default}]" if default else ""
    try:
        if secret:
            import getpass
            value = getpass.getpass(f"  {_c(_BOLD, prompt)}{suffix}: ")
        else:
            value = input(f"  {_c(_BOLD, prompt)}{suffix}: ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\nAborted.")
        sys.exit(0)
    return value or default

def ask_yn(prompt: str, default: bool = True) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    try:
        raw = input(f"  {_c(_BOLD, prompt)} {suffix}: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print("\nAborted.")
        sys.exit(0)
    if not raw:
        return default
    return raw in ("y", "yes")

# ── .env helpers ──────────────────────────────────────────────────────────────

def load_env(path: str) -> dict:
    """Parse existing .env into a dict."""
    result = {}
    if not os.path.exists(path):
        return result
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, val = line.partition("=")
                result[key.strip()] = val.strip().strip('"').strip("'")
    return result


def write_env(path: str, values: dict) -> None:
    """Write values to .env, preserving order and skipping empty ones."""
    lines = []
    for key, val in values.items():
        if val:
            # Quote values that contain spaces
            if " " in val and not (val.startswith('"') or val.startswith("'")):
                val = f'"{val}"'
            lines.append(f"{key}={val}\n")
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)

# ── Telegram helpers ──────────────────────────────────────────────────────────

def _tg_get_updates(token: str) -> list:
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    return resp.json().get("result", [])


def _tg_send_test(token: str, chat_id: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(url, json={
        "chat_id": chat_id,
        "text": "insider\\-agent conectado correctamente\\. Las señales de insiders llegarán aquí\\.",
        "parse_mode": "MarkdownV2",
    }, timeout=10)
    return resp.ok


def discover_chat_id(token: str) -> str | None:
    """
    Calls getUpdates to find chat IDs from recent messages.
    Returns the most recent chat ID, or None if none found.
    """
    try:
        updates = _tg_get_updates(token)
    except Exception as exc:
        warn(f"No se pudo consultar getUpdates: {exc}")
        return None

    chats: list[tuple[str, str]] = []  # (chat_id, description)
    seen: set = set()
    for update in reversed(updates):
        msg = update.get("message") or update.get("channel_post") or {}
        chat = msg.get("chat", {})
        cid = str(chat.get("id", ""))
        if cid and cid not in seen:
            seen.add(cid)
            ctype = chat.get("type", "")
            name = chat.get("title") or chat.get("username") or chat.get("first_name") or ""
            chats.append((cid, f"{ctype}: {name}" if name else ctype))

    if not chats:
        return None
    if len(chats) == 1:
        return chats[0][0]

    print()
    info("Se encontraron varios chats. Elige uno:")
    for i, (cid, desc) in enumerate(chats, 1):
        print(f"    {i}) {cid}  ({desc})")
    print(f"    {len(chats)+1}) Ingresar manualmente")
    try:
        choice = int(input(f"  {_c(_BOLD, 'Opción')}: ").strip())
    except (ValueError, KeyboardInterrupt):
        return None
    if 1 <= choice <= len(chats):
        return chats[choice - 1][0]
    return None

# ── Section wizards ───────────────────────────────────────────────────────────

def section_edgar(existing: dict) -> dict:
    header("SEC EDGAR — User-Agent (obligatorio)")
    info("La SEC requiere que te identifiques con nombre y email.")
    info("Ejemplo: Ricardo Rea ricarorea2584@gmail.com")
    info("Nadie más lo ve; solo lo usa SEC para contactarte si hay algún problema.")
    print()
    current = existing.get("EDGAR_USER_AGENT", "")
    ua = ask("Nombre y email", default=current)
    while not re.search(r".+\s+\S+@\S+\.\S+", ua):
        error("Formato inválido. Debe ser 'Nombre email@dominio.com'")
        ua = ask("Nombre y email", default=current)
    ok("EDGAR_USER_AGENT configurado.")
    return {"EDGAR_USER_AGENT": ua}


def section_llm(existing: dict) -> dict:
    header("LLM — Análisis de señales con IA (opcional)")
    info("Sin API key los mensajes serán texto plano. Con IA obtienes el análisis")
    info("del broker de los 80 con contexto de la empresa y teoría del movimiento.")
    print()

    if not ask_yn("¿Quieres configurar un proveedor de LLM?", default=True):
        info("Saltando. Se usará texto plano como fallback.")
        return {}

    # Provider menu
    providers = [
        ("groq",      "Groq",       "Gratis · llama-3.1-70b · MUY rápido",     "console.groq.com"),
        ("gemini",    "Gemini",     "Gratis · gemini-1.5-flash · Google",        "aistudio.google.com"),
        ("deepseek",  "DeepSeek",   "Muy barato · deepseek-chat · razonamiento fuerte", "platform.deepseek.com"),
        ("openai",    "OpenAI",     "GPT-4o-mini · $0.15/1M tokens",            "platform.openai.com"),
        ("grok",      "Grok (xAI)", "grok-beta · Elon Musk · tono agresivo",    "console.x.ai"),
        ("mistral",   "Mistral",    "Europeo · privacidad · mistral-large",      "console.mistral.ai"),
        ("anthropic", "Anthropic",  "Claude · el original del proyecto",         "console.anthropic.com"),
        ("ollama",    "Ollama",     "Local · sin internet · sin costo",          "ollama.com"),
    ]

    print()
    for i, (code, name, desc, url) in enumerate(providers, 1):
        badge = " ★" if code in ("groq", "gemini") else ""
        print(f"  {i}) {_c(_BOLD, name)}{badge}  —  {desc}")
        print(f"     {_c(_DIM, url)}")
    print(f"  {len(providers)+1}) Omitir (usar texto plano)")
    print()

    current_provider = existing.get("LLM_PROVIDER", "")
    default_idx = next((i+1 for i, (c,*_) in enumerate(providers) if c == current_provider), 1)

    try:
        raw = input(f"  {_c(_BOLD, 'Elige proveedor')} [{default_idx}]: ").strip()
        choice = int(raw) if raw else default_idx
    except (ValueError, KeyboardInterrupt):
        choice = default_idx

    if choice > len(providers):
        info("Saltando. Se usará texto plano.")
        return {}

    code, name, desc, url = providers[choice - 1]

    print()
    info(f"Obtén tu API key en: {url}")

    if code == "ollama":
        info("Ollama corre localmente — instálalo desde ollama.com")
        info("Luego: ollama pull llama3.2")
        model = ask("Modelo Ollama", default=existing.get("LLM_MODEL", "llama3.2"))
        base_url = ask("Base URL", default=existing.get("LLM_BASE_URL", "http://localhost:11434/v1"))
        ok(f"Ollama configurado — modelo: {model}")
        return {"LLM_PROVIDER": "ollama", "LLM_MODEL": model, "LLM_BASE_URL": base_url}

    api_key = ask(f"API key de {name}", default=existing.get("LLM_API_KEY", ""), secret=True)

    # Default models per provider
    default_models = {
        "groq":      "llama-3.1-70b-versatile",
        "gemini":    "gemini-1.5-flash",
        "deepseek":  "deepseek-chat",
        "openai":    "gpt-4o-mini",
        "grok":      "grok-beta",
        "mistral":   "mistral-large-latest",
        "anthropic": "claude-haiku-3-5",
    }
    default_model = default_models.get(code, "")
    model = ask(f"Modelo (Enter para default: {default_model})",
                default=existing.get("LLM_MODEL", default_model))

    ok(f"{name} configurado — modelo: {model or default_model}")

    result = {
        "LLM_PROVIDER": code,
        "LLM_API_KEY":  api_key,
        "LLM_MODEL":    model or default_model,
    }

    # Keep legacy Anthropic key for backward compat
    if code == "anthropic":
        result["ANTHROPIC_API_KEY"] = api_key
        result["ANTHROPIC_MODEL"]   = model or default_model

    return result


def section_telegram(existing: dict) -> dict:
    header("Telegram Bot (opcional — envía señales a tu chat)")
    info("Sin esto, usa --dry-run para ver las señales en la terminal.")
    print()
    if not ask_yn("¿Quieres configurar Telegram?", default=True):
        info("Saltando. Puedes correr 'python setup.py' de nuevo cuando quieras configurarlo.")
        return {}

    if not _HAS_REQUESTS:
        warn("requests no está instalado. Instálalo con: pip install requests")
        token = ask("TELEGRAM_BOT_TOKEN", default=existing.get("TELEGRAM_BOT_TOKEN", ""), secret=True)
        chat_id = ask("TELEGRAM_CHAT_ID", default=existing.get("TELEGRAM_CHAT_ID", ""))
        return {"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_CHAT_ID": chat_id}

    print()
    info("Paso 1 — Crea un bot:")
    info("  1. Abre Telegram y busca @BotFather")
    info("  2. Envíale el comando: /newbot")
    info("  3. Elige un nombre y username para tu bot")
    info("  4. BotFather te dará un token (formato: 123456789:ABCdef...)")
    print()

    current_token = existing.get("TELEGRAM_BOT_TOKEN", "")
    token = ask("Pega el token de tu bot", default=current_token, secret=True)

    # Validate token format
    if not re.match(r"^\d+:[A-Za-z0-9_-]{35,}$", token):
        warn("El token no parece tener el formato correcto (123456:ABC...), pero continúo.")

    # Try to get bot info
    try:
        resp = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
        if resp.ok:
            bot = resp.json().get("result", {})
            ok(f"Bot verificado: @{bot.get('username')} ({bot.get('first_name')})")
        else:
            warn(f"No se pudo verificar el bot: {resp.json().get('description', resp.text)}")
    except Exception as exc:
        warn(f"No se pudo conectar con Telegram: {exc}")

    print()
    info("Paso 2 — Obtén tu Chat ID:")
    info("  1. Busca tu bot en Telegram (por su @username)")
    info("  2. Envíale CUALQUIER mensaje (ej: 'hola')")
    info("  3. Vuelve aquí y presiona Enter para auto-detectar el chat ID")
    print()
    input(f"  {_c(_BOLD, 'Cuando hayas enviado el mensaje, presiona Enter...')}")

    chat_id = discover_chat_id(token)
    if chat_id:
        ok(f"Chat ID detectado: {chat_id}")
    else:
        warn("No se detectó ningún chat. Ingrésalo manualmente.")
        info("Puedes encontrarlo corriendo:")
        info(f'  curl "https://api.telegram.org/bot{token}/getUpdates"')
        info('  Busca "chat":{"id": ...}')
        print()
        chat_id = ask("TELEGRAM_CHAT_ID", default=existing.get("TELEGRAM_CHAT_ID", ""))

    # Send test message
    if chat_id and ask_yn("¿Enviar un mensaje de prueba a tu chat?", default=True):
        try:
            if _tg_send_test(token, chat_id):
                ok("Mensaje de prueba enviado. Revisa tu Telegram.")
            else:
                warn("No se pudo enviar el mensaje de prueba.")
        except Exception as exc:
            warn(f"Error al enviar prueba: {exc}")

    return {"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_CHAT_ID": chat_id}


def section_filters(existing: dict) -> dict:
    header("Filtros de señales (opcionales — puedes dejar los defaults)")
    info("Estos controlan qué compras se consideran señales.")
    print()
    if not ask_yn("¿Quieres ajustar los filtros? (recomendado: No para empezar)", default=False):
        info("Usando defaults: $100k mín, solo mercado abierto, roles CEO/CFO/PRES/DIR.")
        return {}

    result = {}
    result["MIN_TRADE_VALUE_USD"] = ask(
        "Valor mínimo de la compra en USD",
        default=existing.get("MIN_TRADE_VALUE_USD", "100000"),
    )
    result["ALLOWED_ROLES"] = ask(
        "Roles permitidos (separados por coma)",
        default=existing.get("ALLOWED_ROLES", "CEO,CFO,PRES,DIR"),
    )
    result["ONLY_OPEN_MARKET_PURCHASE"] = ask(
        "Solo compras en mercado abierto (true/false)",
        default=existing.get("ONLY_OPEN_MARKET_PURCHASE", "true"),
    )
    result["CLUSTER_WINDOW_DAYS"] = ask(
        "Ventana de días para detectar clusters",
        default=existing.get("CLUSTER_WINDOW_DAYS", "7"),
    )
    result["CLUSTER_MIN_INSIDERS"] = ask(
        "Mínimo de insiders distintos para declarar cluster",
        default=existing.get("CLUSTER_MIN_INSIDERS", "2"),
    )
    ok("Filtros configurados.")
    return result


def section_state(existing: dict) -> dict:
    header("Backend de estado (opcional — default: archivo local)")
    info("Guarda qué filings ya se procesaron para no duplicar señales.")
    current = existing.get("STATE_BACKEND", "file")
    if current == "file" and not ask_yn("¿Cambiar el backend de estado? (default: archivo local)", default=False):
        return {}
    backend = ask("Backend (file / firestore / gcs)", default=current)
    result = {"STATE_BACKEND": backend}
    if backend == "firestore":
        result["GCP_PROJECT"] = ask("GCP Project ID", default=existing.get("GCP_PROJECT", ""))
        result["FIRESTORE_COLLECTION"] = ask("Colección Firestore", default=existing.get("FIRESTORE_COLLECTION", "insider_agent_state"))
    elif backend == "gcs":
        result["GCP_PROJECT"] = ask("GCP Project ID", default=existing.get("GCP_PROJECT", ""))
        result["GCS_BUCKET"] = ask("Nombre del bucket GCS", default=existing.get("GCS_BUCKET", ""))
        result["GCS_OBJECT"] = ask("Nombre del objeto JSON", default=existing.get("GCS_OBJECT", "insider_agent_state.json"))
    return result

# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print()
    print(_c(_BOLD, "  insider-agent — Setup Wizard"))
    print(_c(_DIM,  "  Configura tu .env paso a paso\n"))

    existing = load_env(ENV_FILE)
    if existing:
        warn(f".env ya existe con {len(existing)} variable(s). Los valores actuales se muestran como default.")

    config: dict = {}

    config.update(section_edgar(existing))
    config.update(section_llm(existing))
    config.update(section_telegram(existing))
    config.update(section_filters(existing))
    config.update(section_state(existing))

    # Merge with existing (keep keys not touched by the wizard)
    merged = {**existing, **config}
    # Remove keys explicitly set to empty
    merged = {k: v for k, v in merged.items() if v}

    print()
    header("Guardar configuración")
    print()
    for k, v in merged.items():
        display = "***" if "KEY" in k or "TOKEN" in k or "WEBHOOK" in k else v
        print(f"  {_c(_DIM, k)}={_c(_BOLD, display)}")
    print()

    if ask_yn(f"¿Guardar en {ENV_FILE}?", default=True):
        write_env(ENV_FILE, merged)
        ok(f".env guardado en {ENV_FILE}")
        print()
        info("Próximos pasos:")
        info("  python main.py --once --dry-run   ← prueba sin enviar mensajes")
        info("  python main.py --once              ← ejecución real")
        info("  make install-cron                  ← programar en crontab")
    else:
        warn("No se guardó nada.")

    print()


if __name__ == "__main__":
    main()
