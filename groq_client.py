from __future__ import annotations

import logging
import time
from typing import Optional

import requests

from config import SETTINGS
from prompts import COMMENT_PROMPT
from database import log_ai_usage
from utils import sanitize_text, truncate, METRICS

logger = logging.getLogger(__name__)

# Session with connection pooling for better performance
_session = requests.Session()
_session.mount("https://api.groq.com", requests.adapters.HTTPAdapter(
    pool_connections=2,
    pool_maxsize=4,
    max_retries=2,
))


def _extract_text(payload: dict) -> Optional[str]:
    try:
        choices = payload.get("choices") or []
        if not choices:
            return None
        message = choices[0].get("message") or {}
        text = message.get("content")
        if isinstance(text, str):
            return text.strip() or None
        return None
    except Exception:
        return None


def generate_comment_reply(prompt: str, max_tokens: int = 220) -> Optional[str]:
    if not SETTINGS.groq_api_key:
        return None

    sanitized = sanitize_text(prompt)
    body = {
        "model": SETTINGS.groq_model,
        "messages": [
            {"role": "system", "content": COMMENT_PROMPT},
            {"role": "user", "content": sanitized},
        ],
        "temperature": 0.5,
        "top_p": 0.9,
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {SETTINGS.groq_api_key}",
        "Content-Type": "application/json",
    }
    start = time.time()
    try:
        resp = _session.post(
            "https://api.groq.com/openai/v1/chat/completions",
            json=body,
            headers=headers,
            timeout=(10, 30),
        )
        latency = int((time.time() - start) * 1000)
        if not resp.ok:
            log_ai_usage("groq", SETTINGS.groq_model, f"http_{resp.status_code}", latency_ms=latency)
            logger.warning("Groq API error %s: %s", resp.status_code, resp.text[:300])
            return None

        data = resp.json()
        text = _extract_text(data)
        if text:
            log_ai_usage("groq", SETTINGS.groq_model, "success", latency_ms=latency)
            return truncate(text, 500)

        log_ai_usage("groq", SETTINGS.groq_model, "empty", latency_ms=latency)
        return None
    except Exception:
        latency = int((time.time() - start) * 1000)
        log_ai_usage("groq", SETTINGS.groq_model, "exception", latency_ms=latency)
        logger.exception("Groq request failed")
        return None


def health_check() -> bool:
    return bool(SETTINGS.groq_api_key)