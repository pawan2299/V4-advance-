from __future__ import annotations

import os
import logging
import secrets
import re
from dataclasses import dataclass
from typing import Tuple

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


def _get(key: str, default: str = "") -> str:
    return (os.getenv(key, default) or default).strip()


def _require(key: str) -> str:
    value = _get(key)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {key}")
    return value


def _split_csv(value: str) -> Tuple[str, ...]:
    if not value:
        return ()
    parts = [item.strip() for item in value.split(",") if item.strip()]
    return tuple(dict.fromkeys(parts))


# Simple token format validation
_EAA_RE = re.compile(r"^EAA\w{20,}$")
_IGG_RE = re.compile(r"^IGQ\w{20,}$")
_BOT_RE = re.compile(r"^\d{8,}:[A-Za-z0-9_-]{30,}$")


def _validate_token(value: str, name: str, pattern: re.Pattern | None = None) -> str:
    """Log a warning if a token looks malformed but still return it."""
    if not value:
        return value
    if pattern and not pattern.match(value):
        logger.warning("%s does not match expected format — please verify", name)
    return value


@dataclass(frozen=True)
class Settings:
    # Meta/Instagram - Required
    verify_token: str
    app_secret: str
    page_id: str

    # Meta/Instagram - Tokens
    own_account_id: str  # For filtering self-comments
    graph_access_token: str  # EAA... token for comments/media operations
    instagram_login_access_token: str  # IGQ... token for DM operations

    # Database
    database_url: str

    # Telegram - Required
    telegram_bot_token: str
    telegram_chat_id: str

    # Telegram - Optional
    telegram_admin_chat_ids: Tuple[str, ...]
    telegram_webhook_secret: str

    # URLs
    public_base_url: str

    # AI Services
    gemini_api_keys: Tuple[str, ...]
    groq_api_key: str
    groq_model: str

    # Application Configuration
    environment: str
    log_level: str
    port: int

    # Performance/Cache Settings
    ai_cache_ttl_seconds: int
    ai_cache_maxsize: int
    db_pool_min: int
    db_pool_max: int

    # New: Maintenance & reliability
    maintenance_interval_seconds: int
    db_cleanup_days: int
    max_image_bytes: int
    webhook_rate_limit: int
    telegram_rate_limit: int


def _load() -> Settings:
    # Database URL with sslmode
    db_url = _require("DATABASE_URL")
    if "sslmode" not in db_url:
        db_url += ("&" if "?" in db_url else "?") + "sslmode=require"

    # Telegram admin IDs - fallback to main chat_id if not specified
    telegram_chat_id = _require("TELEGRAM_CHAT_ID")
    admin_ids = _split_csv(_get("TELEGRAM_ADMIN_CHAT_IDS"))
    if not admin_ids:
        admin_ids = (telegram_chat_id,)
    elif telegram_chat_id not in admin_ids:
        admin_ids = tuple([telegram_chat_id, *admin_ids])

    # Telegram webhook secret - generate if not provided
    webhook_secret = _get("TELEGRAM_WEBHOOK_SECRET")
    if not webhook_secret:
        webhook_secret = secrets.token_urlsafe(32)
        logger.warning(
            "TELEGRAM_WEBHOOK_SECRET not set — generated a random one for this process. "
            "Set it explicitly in your environment so it stays stable across restarts."
        )

    # Public base URL - fallback to RENDER_EXTERNAL_URL
    public_base_url = _get("PUBLIC_BASE_URL", _get("RENDER_EXTERNAL_URL"))

    # Load Gemini keys from CSV format only
    gemini_keys_raw = _get("GEMINI_API_KEYS")
    gemini_api_keys = _split_csv(gemini_keys_raw)

    # Validate token formats (warn but don't block)
    graph_token = _validate_token(_get("GRAPH_ACCESS_TOKEN"), "GRAPH_ACCESS_TOKEN", _EAA_RE)
    ig_token = _validate_token(_get("INSTAGRAM_LOGIN_ACCESS_TOKEN"), "INSTAGRAM_LOGIN_ACCESS_TOKEN", _IGG_RE)
    tg_token = _validate_token(_require("TELEGRAM_BOT_TOKEN"), "TELEGRAM_BOT_TOKEN", _BOT_RE)

    return Settings(
        # Meta/Instagram - Required
        verify_token=_require("VERIFY_TOKEN"),
        app_secret=_require("APP_SECRET"),
        page_id=_require("PAGE_ID"),

        # Meta/Instagram - Tokens
        own_account_id=_get("OWN_ACCOUNT_ID"),
        graph_access_token=graph_token,
        instagram_login_access_token=ig_token,

        # Database
        database_url=db_url,

        # Telegram - Required
        telegram_bot_token=tg_token,
        telegram_chat_id=telegram_chat_id,

        # Telegram - Optional
        telegram_admin_chat_ids=admin_ids,
        telegram_webhook_secret=webhook_secret,

        # URLs
        public_base_url=public_base_url,

        # AI Services
        gemini_api_keys=gemini_api_keys,
        groq_api_key=_get("GROQ_API_KEY"),
        groq_model=_get("GROQ_MODEL", "llama-3.3-70b-versatile"),

        # Application Configuration
        environment=_get("APP_ENV", "production"),
        log_level=_get("LOG_LEVEL", "INFO"),
        port=int(_get("PORT", "5000")),

        # Performance/Cache Settings
        ai_cache_ttl_seconds=int(_get("AI_CACHE_TTL_SECONDS", "1800")),
        ai_cache_maxsize=int(_get("AI_CACHE_MAXSIZE", "2000")),
        db_pool_min=int(_get("DB_POOL_MIN", "2")),
        db_pool_max=int(_get("DB_POOL_MAX", "10")),

        # New: Maintenance & reliability
        maintenance_interval_seconds=int(_get("MAINTENANCE_INTERVAL_SECONDS", "300")),
        db_cleanup_days=int(_get("DB_CLEANUP_DAYS", "7")),
        max_image_bytes=int(_get("MAX_IMAGE_BYTES", str(4 * 1024 * 1024))),  # 4 MB
        webhook_rate_limit=int(_get("WEBHOOK_RATE_LIMIT", "120")),
        telegram_rate_limit=int(_get("TELEGRAM_RATE_LIMIT", "60")),
    )


SETTINGS = _load()