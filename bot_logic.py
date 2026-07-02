from __future__ import annotations

import logging
import random
import threading
import time
from typing import Dict, Any

from ai_router import generate_comment_reply, get_fixed_welcome_dm
from database import (
    claim_event,
    claim_welcome_dm,
    enqueue_failed_reply,
    get_keyword_reply,
    is_already_replied,
    is_bot_paused,
    is_gemini_enabled,
    is_safe_mode,
    mark_replied,
)
from instagram_api import get_media_url, reply_to_comment, send_dm
from config import SETTINGS
from utils import METRICS

logger = logging.getLogger(__name__)

# Conservative request limiter for free-tier stability.
_rate_limit_window: list[float] = []
_rate_limit_lock = threading.Lock()
MAX_REQUESTS_PER_MINUTE = 15

SHORT_REPLIES = [
    "Radhe Radhe! 🙏",
    "Jai Shri Krishna! 🌸",
    "Hari Bol! ✨",
    "🙏💛",
    "Jai Radhe! 🌺",
    "Shri Krishna ki jai! ✨",
]

GREETING_REPLIES = [
    "Radhe Radhe! 🙏 Jai Shri Krishna!",
    "Jai Shri Krishna! 🌸 Hare Krishna!",
    "Hari Bol! 🙏 Welcome, devotee!",
]

PRAISE_REPLIES = [
    "Thank you so much! 🙏 Radhe Radhe!",
    "Your love means everything! Jai Shri Krishna! ✨",
    "Hare Krishna! 🌸 So grateful for you!",
    "Krishna's blessings to you! 💛🙏",
]


def _check_rate_limit() -> bool:
    now = time.time()
    with _rate_limit_lock:
        while _rate_limit_window and now - _rate_limit_window[0] > 60:
            _rate_limit_window.pop(0)
        if len(_rate_limit_window) >= MAX_REQUESTS_PER_MINUTE:
            return False
        _rate_limit_window.append(now)
        return True


def _looks_suspicious(text: str) -> bool:
    """Spam detection — returns True if the comment looks like spam.

    IMPORTANT: This is a moderation decision, NOT an AI-cost decision.
    It runs regardless of Gemini/safe-mode state.
    """
    lower = text.lower()
    signals = ("follow", "check", "link", "bio", "giveaway", "free", "click", "promo", "dm me", "collab")
    if any(signal in lower for signal in signals):
        return True
    if len(set(text.replace(" ", ""))) < 3:
        return True
    return False


def _classify(text: str) -> str:
    """Classify a comment into a reply category.

    Categories:
    - "short": Very short text (<=4 chars)
    - "greeting": Contains greeting words and is short
    - "praise": Contains praise words and is moderate length
    - "ai": Contains questions or is long enough for AI
    """
    clean = text.lower().strip()
    words = set(clean.split())
    if len(clean) <= 4:
        return "short"
    if words & {"hi", "hello", "hey", "namaste", "radhe", "jai", "hare", "hari", "bol"} and len(clean) < 25:
        return "greeting"
    if words & {"beautiful", "amazing", "lovely", "nice", "good", "great", "wow", "awesome", "love", "cute", "best", "divine", "blessed", "wonderful", "superb", "heart"} and len(clean) < 40:
        return "praise"
    if "?" in clean or len(clean) > 30:
        return "ai"
    return "short"


def _fixed_dm_template() -> str:
    return get_fixed_welcome_dm()


def _maybe_send_welcome_dm(user_id: str) -> None:
    if not SETTINGS.instagram_login_access_token:
        return
    if not claim_welcome_dm(user_id):
        return
    message = _fixed_dm_template()
    if send_dm(user_id, message):
        logger.info("Fixed welcome DM sent to %s", user_id[:10])
        METRICS.inc("welcome_dms_sent")
    else:
        logger.warning("Fixed welcome DM failed for %s", user_id[:10])
        METRICS.inc("welcome_dms_failed")


def handle_comment(comment_data: dict):
    """Process an incoming Instagram comment webhook event.

    Flow:
    1. Guard: paused / empty / self-comment / duplicate / rate limit
    2. Spam filtering (ALWAYS runs, independent of AI/safe-mode)
    3. Keyword match
    4. AI classification + generation (if applicable)
    5. Fallback to hardcoded replies
    6. Reply to comment + optionally send welcome DM
    """
    if is_bot_paused():
        return
    if not comment_data:
        return

    comment_id = comment_data.get("id", "")
    text = (comment_data.get("text") or "").strip()
    from_user = comment_data.get("from", {}) or {}
    from_id = from_user.get("id", "")
    username = from_user.get("username", "")

    if not comment_id or not text or not from_id:
        return
    if from_id == SETTINGS.own_account_id:
        return
    if is_already_replied(comment_id):
        return
    if not _check_rate_limit():
        logger.warning("Global local rate limit exceeded, skipping comment %s", comment_id[:10])
        METRICS.inc("rate_limit_dropped")
        return

    # Spam filtering — ALWAYS runs (moderation, not AI-cost decision)
    if _looks_suspicious(text):
        lowered = text.lower()
        if "http" in lowered or "bit.ly" in lowered:
            logger.info("Spam-like comment ignored (link): %s", text[:50])
            METRICS.inc("spam_filtered")
            return
        # Non-link spam gets a hardcoded reply (so real users aren't ignored)
        logger.info("Spam-like comment (no link) — using fallback reply: %s", text[:50])

    # Try keyword match first
    reply = get_keyword_reply(text)
    reply_type = "keyword"

    if reply is None:
        comment_type = _classify(text)
        reply_type = comment_type
        if comment_type == "ai" and is_gemini_enabled() and not is_safe_mode():
            media_id = comment_data.get("media_id") or comment_data.get("media", {}).get("id")
            image_url = get_media_url(media_id) if media_id else None
            reply = generate_comment_reply(text, image_url=image_url)

        if reply is None:
            if comment_type == "greeting":
                reply = random.choice(GREETING_REPLIES)
            elif comment_type == "praise":
                reply = random.choice(PRAISE_REPLIES)
            else:
                reply = random.choice(SHORT_REPLIES)

    if reply_to_comment(comment_id, reply):
        claim_event(comment_id)
        mark_replied(comment_id)
        logger.info("Replied [%s] to comment %s", reply_type, comment_id[:10])
        METRICS.inc("comments_replied")
        _maybe_send_welcome_dm(from_id)
    else:
        logger.warning("Failed to reply to comment %s — queuing for retry", comment_id[:10])
        METRICS.inc("reply_failures")
        enqueue_failed_reply(comment_id, comment_data, reply)


def handle_new_follower(user_id: str, username: str = ""):
    if is_bot_paused() or not user_id:
        return
    if user_id == SETTINGS.own_account_id:
        return
    _maybe_send_welcome_dm(user_id)