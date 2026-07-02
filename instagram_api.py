from __future__ import annotations

import logging
import threading
from typing import Optional

import requests

from config import SETTINGS
from utils import METRICS

logger = logging.getLogger(__name__)

# Live token cache — updated by refresh_token(), read by other modules.
# Guarded by _token_lock so background refresh and request threads don't race.
_live_graph_token: str = ""
_live_dm_token: str = ""
_token_lock = threading.Lock()

# Refresh metadata stored in DB for cross-restart persistence.
_TOKEN_EXPIRY_KEY = "token:graph:expires_at"
_TOKEN_EXPIRY_DM_KEY = "token:dm:expires_at"
_REFRESH_BUFFER_SECONDS = 3600  # Refresh 1 hour before actual expiry.

# Session with connection pooling for better performance
_session = requests.Session()
_session.mount("https://graph.facebook.com", requests.adapters.HTTPAdapter(
    pool_connections=4,
    pool_maxsize=8,
    max_retries=2,
))
_session.mount("https://graph.instagram.com", requests.adapters.HTTPAdapter(
    pool_connections=2,
    pool_maxsize=4,
    max_retries=2,
))

BASE_GRAPH = "https://graph.facebook.com/v25.0"
BASE_IG = "https://graph.instagram.com/v25.0"


def _extract_error(resp: requests.Response) -> str:
    try:
        data = resp.json()
        return data.get("error", {}).get("message") or resp.text
    except Exception:
        return resp.text


def _facebook_post(endpoint: str, payload: dict, token: str) -> bool:
    try:
        resp = _session.post(
            f"{BASE_GRAPH}/{endpoint}",
            params={"access_token": token},
            json=payload,
            timeout=(10, 30),
        )
        if resp.ok:
            METRICS.inc("ig_api_success")
            return True
        METRICS.inc("ig_api_error")
        logger.error("Instagram API error %s: %s", resp.status_code, _extract_error(resp))
        return False
    except Exception:
        METRICS.inc("ig_api_exception")
        logger.exception("Instagram request failed")
        return False


def reply_to_comment(comment_id: str, message: str) -> bool:
    token = _get_graph_token()
    if not token:
        logger.error("No Graph API token configured for comment replies")
        return False
    return _facebook_post(f"{comment_id}/replies", {"message": message}, token)


def send_dm(user_id: str, message: str) -> bool:
    token = SETTINGS.instagram_login_access_token
    if not token:
        logger.error("INSTAGRAM_LOGIN_ACCESS_TOKEN missing; fixed DM disabled")
        return False

    try:
        resp = _session.post(
            f"{BASE_IG}/me/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "recipient": {"id": user_id},
                "message": {"text": message},
            },
            timeout=(10, 30),
        )
        if resp.ok:
            return True
        logger.error("Instagram DM error %s: %s", resp.status_code, _extract_error(resp))
        return False
    except Exception:
        logger.exception("Instagram DM request failed")
        return False


def get_media_url(media_id: str) -> str | None:
    token = _get_graph_token()
    if not token:
        return None
    try:
        resp = _session.get(
            f"{BASE_GRAPH}/{media_id}",
            params={"fields": "media_url,permalink", "access_token": token},
            timeout=(10, 20),
        )
        if not resp.ok:
            logger.warning("Failed to fetch media_url %s: %s", resp.status_code, _extract_error(resp))
            return None
        return resp.json().get("media_url")
    except Exception:
        logger.exception("Failed to fetch media_url")
        return None


def _get_graph_token() -> str:
    """Return the current live Graph token (may differ from env after refresh)."""
    with _token_lock:
        return _live_graph_token or SETTINGS.graph_access_token


def _get_dm_token() -> str:
    """Return the current live DM token (may differ from env after refresh)."""
    with _token_lock:
        return _live_dm_token or SETTINGS.instagram_login_access_token


def _set_live_token(token_type: str, new_token: str) -> None:
    """Update the in-memory live token cache."""
    global _live_graph_token, _live_dm_token
    with _token_lock:
        if token_type == "graph":
            _live_graph_token = new_token
        elif token_type == "dm":
            _live_dm_token = new_token


def debug_token_exchange(short_lived_token: str) -> Optional[dict]:
    """Exchange a short-lived Graph token for a long-lived one.

    Returns the API response dict on success, None on failure.
    Long-lived tokens last ~60 days.
    """
    if not short_lived_token:
        return None
    fb_exchange_url = (
        f"https://graph.facebook.com/v25.0/oauth/access_token"
        f"?grant_type=fb_exchange_token"
        f"&client_id={SETTINGS.page_id}"
        f"&client_secret={SETTINGS.app_secret}"
        f"&fb_exchange_token={short_lived_token}"
    )
    try:
        resp = _session.get(fb_exchange_url, timeout=(10, 20))
        if resp.ok:
            data = resp.json()
            logger.info("Long-lived Graph token obtained, expires in %ss", data.get("expires_in", "?"))
            return data
        logger.error("Token exchange failed (%s): %s", resp.status_code, _extract_error(resp))
        return None
    except Exception:
        logger.exception("Token exchange request failed")
        return None


def refresh_instagram_token() -> Optional[dict]:
    """Refresh the Instagram long-lived token using the Graph API.

    Returns dict with 'access_token' and 'expires_in' on success, None on failure.
    Must be called before the current token actually expires.
    """
    token = _get_graph_token()
    if not token:
        logger.error("Cannot refresh: no Graph token available")
        return None
    refresh_url = (
        f"https://graph.facebook.com/v25.0/oauth/access_token"
        f"?grant_type=fb_exchange_token"
        f"&client_id={SETTINGS.page_id}"
        f"&client_secret={SETTINGS.app_secret}"
        f"&fb_exchange_token={token}"
    )
    try:
        resp = _session.get(refresh_url, timeout=(10, 20))
        if resp.ok:
            data = resp.json()
            new_token = data.get("access_token")
            expires_in = data.get("expires_in", 0)
            if new_token and new_token != token:
                _set_live_token("graph", new_token)
                logger.info("Graph token refreshed successfully, expires in %ss", expires_in)
            else:
                logger.info("Graph token refresh returned same token, expires in %ss", expires_in)
            return data
        logger.error("Token refresh failed (%s): %s", resp.status_code, _extract_error(resp))
        return None
    except Exception:
        logger.exception("Token refresh request failed")
        return None


def check_token_validity(token_type: str = "graph") -> bool:
    """Check if the configured access token is still valid."""
    if token_type == "graph":
        token = _get_graph_token()
        base_url = BASE_GRAPH
    elif token_type == "dm":
        token = _get_dm_token()
        base_url = BASE_IG
    else:
        token = _get_graph_token()
        base_url = BASE_GRAPH

    if not token:
        logger.warning("No token configured for token check (%s)", token_type)
        return False

    try:
        resp = _session.get(
            f"{base_url}/me",
            params={"access_token": token, "fields": "id"},
            timeout=(10, 20),
        )
        if resp.ok:
            logger.info("%s token is valid (verified via /me)", token_type)
            return True
        err_msg = _extract_error(resp)
        logger.error("%s token validity check failed (Status: %s): %s", token_type, resp.status_code, err_msg)
        return False
    except Exception:
        logger.exception("Token validity check failed for %s", token_type)
        return False