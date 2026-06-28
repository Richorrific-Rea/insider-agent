"""
Configuration loader — reads from environment / .env file into a dataclass.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Set

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


@dataclass
class Config:
    # ── EDGAR ──────────────────────────────────────────────────────────────
    edgar_user_agent: str = ""          # required: "Name email@example.com"
    feed_count: int = 100

    # ── Filters ────────────────────────────────────────────────────────────
    only_open_market_purchase: bool = True
    allowed_roles: Set[str] = field(default_factory=lambda: {"CEO", "CFO", "PRES", "DIR"})
    min_trade_value_usd: float = 100_000
    min_delta_own_pct: float = 0.0
    cluster_window_days: int = 7
    cluster_min_insiders: int = 2

    # ── LLM (cualquier proveedor) ──────────────────────────────────────────
    # Proveedor: anthropic | openai | groq | gemini | ollama | custom
    llm_provider: str = "anthropic"
    llm_api_key: str = ""        # API key del proveedor elegido
    llm_model: str = ""          # se elige default por proveedor si está vacío
    llm_base_url: str = ""       # para proveedores OpenAI-compatibles / Ollama

    # Retrocompatibilidad — si sólo está ANTHROPIC_API_KEY configurado, funciona igual
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"

    # ── Telegram ───────────────────────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # ── Congressional confluence ───────────────────────────────────────────
    use_congress_data: bool = True
    congress_days_back: int = 30        # how far back to look for politician trades
    confluence_window_days: int = 14    # insider + politician must trade within this window
    confluence_min_politicians: int = 1 # min politicians buying same ticker

    # ── State / storage ────────────────────────────────────────────────────
    state_backend: str = "file"         # "file" | "firestore" | "gcs"
    state_file_path: str = "state.json"
    gcp_project: str = ""
    firestore_collection: str = "insider_agent_state"
    gcs_bucket: str = ""
    gcs_object: str = "insider_agent_state.json"


def load_config() -> Config:
    def _bool(key: str, default: bool) -> bool:
        v = os.getenv(key, "").lower()
        if v in ("1", "true", "yes"):
            return True
        if v in ("0", "false", "no"):
            return False
        return default

    def _float(key: str, default: float) -> float:
        v = os.getenv(key, "")
        try:
            return float(v) if v else default
        except ValueError:
            return default

    def _int(key: str, default: int) -> int:
        v = os.getenv(key, "")
        try:
            return int(v) if v else default
        except ValueError:
            return default

    def _set(key: str, default: Set[str]) -> Set[str]:
        v = os.getenv(key, "")
        return {r.strip().upper() for r in v.split(",") if r.strip()} if v else default

    ua = os.getenv("EDGAR_USER_AGENT", "").strip()
    if not ua:
        raise ValueError(
            "EDGAR_USER_AGENT is required. Set it to 'YourName your@email.com' "
            "so SEC can identify your client per their fair-access policy."
        )

    return Config(
        edgar_user_agent=ua,
        feed_count=_int("FEED_COUNT", 100),
        only_open_market_purchase=_bool("ONLY_OPEN_MARKET_PURCHASE", True),
        allowed_roles=_set("ALLOWED_ROLES", {"CEO", "CFO", "PRES", "DIR"}),
        min_trade_value_usd=_float("MIN_TRADE_VALUE_USD", 100_000),
        min_delta_own_pct=_float("MIN_DELTA_OWN_PCT", 0.0),
        cluster_window_days=_int("CLUSTER_WINDOW_DAYS", 7),
        cluster_min_insiders=_int("CLUSTER_MIN_INSIDERS", 2),
        llm_provider=os.getenv("LLM_PROVIDER", "anthropic"),
        llm_api_key=os.getenv("LLM_API_KEY", ""),
        llm_model=os.getenv("LLM_MODEL", ""),
        llm_base_url=os.getenv("LLM_BASE_URL", ""),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        use_congress_data=_bool("USE_CONGRESS_DATA", True),
        congress_days_back=_int("CONGRESS_DAYS_BACK", 30),
        confluence_window_days=_int("CONFLUENCE_WINDOW_DAYS", 14),
        confluence_min_politicians=_int("CONFLUENCE_MIN_POLITICIANS", 1),
        state_backend=os.getenv("STATE_BACKEND", "file"),
        state_file_path=os.getenv("STATE_FILE_PATH", "state.json"),
        gcp_project=os.getenv("GCP_PROJECT", ""),
        firestore_collection=os.getenv("FIRESTORE_COLLECTION", "insider_agent_state"),
        gcs_bucket=os.getenv("GCS_BUCKET", ""),
        gcs_object=os.getenv("GCS_OBJECT", "insider_agent_state.json"),
    )
