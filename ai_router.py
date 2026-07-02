from __future__ import annotations

import logging
import random
import re
from typing import Optional

from cache import TTLCache, cache_key, normalize_text
from config import SETTINGS
from gemini_client import generate_comment_reply as gemini_generate_comment_reply, is_spam_or_negative
from groq_client import generate_comment_reply as groq_generate_comment_reply
from prompts import FIXED_WELCOME_DM_TEMPLATES
from utils import METRICS

logger = logging.getLogger(__name__)
_reply_cache = TTLCache(maxsize=SETTINGS.ai_cache_maxsize, ttl=SETTINGS.ai_cache_ttl_seconds)

GREETING_WORDS = {
    "radhe radhe", "jai shri krishna", "hare krishna", "hare ram", "namaste", "hello", "hi", "hey", "hari bol"
}
PRAISE_WORDS = {
    "beautiful", "cute", "amazing", "awesome", "lovely", "nice", "superb", "wow", "thanks", "thank you", "good"
}


def classify_comment(text: str) -> str:
    """Classify comment for AI routing. Single source of truth.

    This is the ONLY classification function used by the AI router.
    bot_logic._classify is a simpler version for the non-AI fallback path.
    """
    clean = normalize_text(text)
    if not clean:
        return "empty"
    emoji_only = bool(re.fullmatch(r"[\W_]+", clean.replace(" ", "")))
    if emoji_only:
        return "emoji"
    if any(phrase in clean for phrase in GREETING_WORDS) and len(clean) < 35:
        return "greeting"
    if any(word in clean for word in PRAISE_WORDS) and len(clean) < 60:
        return "praise"
    if "?" in clean or len(clean) > 35:
        return "question"
    return "general"


def _hardcoded_reply(kind: str) -> str:
    if kind == "emoji":
        return random.choice([
            "🙏✨ Thank you so much!",
            "💛 Radhe Radhe!",
            "🌸 Jai Shri Krishna!",
        ])
    if kind == "greeting":
        return random.choice([
            "Radhe Radhe! 🙏 Jai Shri Krishna!",
            "Jai Shri Krishna! 🌸 Haribol!",
            "Radhe Radhe! Thank you for being here 🙏",
        ])
    if kind == "praise":
        return random.choice([
            "Thank you so much! 🙏 Please follow our page for more Krishna-inspired videos.",
            "Your support means a lot 💛 Please like and follow for more devotional content.",
            "So grateful for your love 🌸 Please stay connected for more reels like this.",
        ])
    return random.choice([
        "Thank you so much for your comment 🙏 Please follow our page for more Krishna-inspired videos.",
        "We truly appreciate your support 💛 Please like and follow for more devotional content.",
        "Radhe Radhe! Thank you for checking out our video 🌸 Please stay connected for more.",
    ])


def generate_comment_reply(comment_text: str, post_caption: str = "", image_url: str | None = None) -> Optional[str]:
    """Generate a reply to a comment.

    Decision flow:
    1. Normalize & classify
    2. Spam/negative check
    3. Cache lookup
    4. Hardcoded for simple categories (emoji, greeting, praise)
    5. AI (Gemini) for question/general
    6. Groq fallback if Gemini fails
    7. Final fallback to hardcoded general
    """
    clean = normalize_text(comment_text)
    if not clean:
        return None

    kind = classify_comment(clean)
    if kind == "empty":
        return None
    if is_spam_or_negative(clean):
        METRICS.inc("spam_filtered_ai_router")
        return None

    key = cache_key("comment", clean, post_caption or "", image_url or "")
    cached = _reply_cache.get(key)
    if cached:
        METRICS.inc("cache_hits")
        return cached
    METRICS.inc("cache_misses")

    # Simple categories use hardcoded replies (no AI cost, NO caching to preserve variety)
    if kind in {"emoji", "greeting", "praise"}:
        return _hardcoded_reply(kind)

    # AI for meaningful / complex comments
    METRICS.inc("ai_requests")
    reply = gemini_generate_comment_reply(clean, post_caption=post_caption, image_url=image_url)
    if not reply:
        reply = groq_generate_comment_reply(clean)

    if not reply:
        reply = _hardcoded_reply("general")

    _reply_cache.set(key, reply)
    return reply


def get_fixed_welcome_dm() -> str:
    return random.choice(FIXED_WELCOME_DM_TEMPLATES)


def get_status() -> dict:
    return {
        "cache": _reply_cache.stats(),
        "gemini": __import__("gemini_client").get_status(),
        "groq_enabled": bool(SETTINGS.groq_api_key),
    }


def clear_cache() -> None:
    _reply_cache.clear()