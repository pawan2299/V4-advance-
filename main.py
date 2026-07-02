from __future__ import annotations

import logging
import threading
import time
import atexit
import signal
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, jsonify, request, render_template, abort

from bot_logic import handle_comment, handle_new_follower
from config import SETTINGS
from database import init_db, get_stats, is_bot_paused, is_safe_mode, db_ping
from security import verify_signature, verify_meta_verify_token, verify_telegram_secret
from telegram_bot import get_webhook_info, handle_update, register_telegram_webhook
from instagram_api import check_token_validity
from middleware import register_request_id, register_response_timing, register_rate_limiting, register_cors
from error_handlers import register_error_handlers
from health import run_all_checks
from tasks import start_background_tasks, stop_background_tasks
from utils import METRICS

logging.basicConfig(
    level=getattr(logging, SETTINGS.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(request_id)s | %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024  # 2 MB
_executor = ThreadPoolExecutor(max_workers=4)
_init_lock = threading.Lock()
_init_done = False


def _startup_once() -> None:
    global _init_done
    with _init_lock:
        if _init_done:
            return
        logger.info("Starting KrishnaVerse AI initialization")
        init_db()
        if SETTINGS.telegram_bot_token and SETTINGS.public_base_url:
            register_telegram_webhook()
        # Token checks are non-fatal.
        try:
            check_token_validity("graph")
        except Exception:
            logger.exception("Graph token validation failed")
        try:
            check_token_validity("dm")
        except Exception:
            logger.exception("DM token validation failed")
        # Start background maintenance tasks
        start_background_tasks()
        _init_done = True
        logger.info("Initialization complete")


# Register middleware and error handlers
register_request_id(app)
register_response_timing(app)
register_rate_limiting(app)
register_cors(app, origins=SETTINGS.public_base_url or "")
register_error_handlers(app)


def _require_internal_auth() -> None:
    """Reject requests to internal endpoints that don't come from Render
    private network or a recognized monitoring source.
    On Render free tier, requests between services share the same private IP.
    We also allow requests with a shared secret header for external monitors.
    """
    # Option 1: X-Internal-Secret header (for external monitoring tools)
    secret = request.headers.get("X-Internal-Secret", "")
    if secret and SETTINGS.app_secret and secret == SETTINGS.app_secret:
        return
    # Option 2: Private network IPs (Render internal, localhost, 10.x, 172.16-31.x)
    # Use the same trusted-IP extraction as rate limiter to prevent spoofing
    from middleware import _get_client_ip
    client_ip = _get_client_ip()
    if client_ip in ("127.0.0.1", "::1", "localhost") or client_ip.startswith(("10.", "172.16.", "172.17.",
        "172.18.", "172.19.", "172.20.", "172.21.", "172.22.", "172.23.", "172.24.", "172.25.",
        "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31.", "192.168.")):
        return
    logger.warning("Unauthorized internal endpoint access from %s", client_ip)
    abort(403)


@app.before_request
def ensure_startup():
    _startup_once()


@app.get("/")
def health():
    stats = get_stats()
    status_data = {
        "status": "KrishnaVerse AI is live",
        "environment": SETTINGS.environment,
        "database": "ok" if db_ping() else "unreachable",
        "paused": is_bot_paused(),
        "safe_mode": is_safe_mode(),
        "stats": stats,
    }
    return render_template("index.html", status=status_data)


@app.get("/health/deep")
def deep_health():
    """Detailed health check for monitoring / alerting."""
    return jsonify(run_all_checks()), 200


@app.get("/webhook")
def verify_webhook():
    if (
        request.args.get("hub.mode") == "subscribe"
        and verify_meta_verify_token(request.args.get("hub.verify_token", ""))
    ):
        return request.args.get("hub.challenge", ""), 200
    return "Forbidden", 403


@app.post("/webhook")
def webhook():
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not verify_signature(request.data, signature):
        logger.warning("Invalid webhook signature")
        return "Forbidden", 403

    data = request.get_json(silent=True) or {}

    def process_payload():
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                field = change.get("field")
                value = change.get("value", {}) or {}
                if field == "comments":
                    handle_comment(value)
                elif field == "follows":
                    handle_new_follower(value.get("id", ""), value.get("username", ""))

    _executor.submit(process_payload)
    return "OK", 200


@app.post("/telegram-webhook")
def telegram_webhook():
    header = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not verify_telegram_secret(header):
        logger.warning("Rejected Telegram webhook call with invalid/missing secret token")
        return "Forbidden", 403
    data = request.get_json(silent=True) or {}
    _executor.submit(handle_update, data)
    return "OK", 200


@app.get("/stats")
def stats():
    # Protect internal stats — require either admin Telegram chat_id or same IP
    _require_internal_auth()
    return jsonify(get_stats()), 200


@app.get("/metrics")
def metrics():
    """Simple metrics endpoint for monitoring."""
    _require_internal_auth()
    return jsonify(METRICS.snapshot()), 200


@app.get("/telegram-webhook-info")
def telegram_webhook_info():
    _require_internal_auth()
    return jsonify(get_webhook_info()), 200


def _shutdown(*_args) -> None:
    logger.info("Shutting down — draining background task queue")
    stop_background_tasks()
    _executor.shutdown(wait=True, cancel_futures=False)


atexit.register(_shutdown)
signal.signal(signal.SIGTERM, _shutdown)


if __name__ == "__main__":
    _startup_once()
    app.run(host="0.0.0.0", port=SETTINGS.port, debug=False)