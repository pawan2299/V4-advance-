"""Flask middleware: request ID tracking, response timing, rate limiting."""

from __future__ import annotations

import logging
import time
import threading
import uuid
from typing import Callable

from flask import Flask, request, g, Response

from utils import METRICS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Request-ID middleware
# ---------------------------------------------------------------------------

def register_request_id(app: Flask) -> None:
    """Attach a unique request-id to every incoming request for log correlation."""

    @app.before_request
    def _attach_request_id() -> None:
        g.request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex[:12])

    @app.after_request
    def _set_request_id_header(response: Response) -> Response:
        rid = getattr(g, "request_id", "unknown")
        response.headers["X-Request-ID"] = rid
        return response


# ---------------------------------------------------------------------------
# Response-time middleware
# ---------------------------------------------------------------------------

def register_response_timing(app: Flask) -> None:
    """Record response time for every request."""

    @app.before_request
    def _start_timer() -> None:
        g._start_time = time.time()

    @app.after_request
    def _record_timing(response: Response) -> Response:
        elapsed = time.time() - getattr(g, "_start_time", time.time())
        METRICS.set_gauge("request_duration_s", elapsed)
        if elapsed > 5.0:
            logger.warning("Slow request %s %s took %.2fs", request.method, request.path, elapsed)
        return response


# ---------------------------------------------------------------------------
# IP-based rate limiter (per-endpoint, in-process)
# ---------------------------------------------------------------------------

class RateLimiter:
    """Simple sliding-window rate limiter keyed by (ip, endpoint)."""

    def __init__(self, max_requests: int = 60, window_seconds: int = 60) -> None:
        self.max_requests = max_requests
        self.window = window_seconds
        self._buckets: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, bucket: list[float], now: float) -> None:
        cutoff = now - self.window
        while bucket and bucket[0] < cutoff:
            bucket.pop(0)

    def is_allowed(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            bucket = self._buckets.setdefault(key, [])
            self._prune(bucket, now)
            if len(bucket) >= self.max_requests:
                return False
            bucket.append(now)
            return True


# Separate limiters for different concerns
_webhook_limiter = RateLimiter(max_requests=120, window_seconds=60)
_telegram_limiter = RateLimiter(max_requests=60, window_seconds=60)
_api_limiter = RateLimiter(max_requests=30, window_seconds=60)


def _get_client_ip() -> str:
    """Extract the real client IP from proxy headers.

    Security note: X-Forwarded-For can be spoofed by clients. The RIGHTMOST
    entry is set by the last trusted proxy (Render), so we use that.
    X-Real-IP (set by Render) is preferred when available.
    """
    # Prefer X-Real-IP — set directly by Render and cannot be client-spoofed
    real_ip = request.headers.get("X-Real-IP", "").strip()
    if real_ip:
        return real_ip
    # Fall back to the LAST entry in X-Forwarded-For (set by Render's proxy)
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        # Rightmost IP is the one closest to our server (set by trusted proxy)
        return xff.split(",")[-1].strip()
    return request.remote_addr or "unknown"


def register_rate_limiting(app: Flask) -> None:
    """Apply per-endpoint rate limiting."""

    @app.before_request
    def _check_rate_limit() -> None:
        path = request.path
        client_ip = _get_client_ip()

        if path == "/webhook":
            limiter = _webhook_limiter
        elif path == "/telegram-webhook":
            limiter = _telegram_limiter
        elif path.startswith("/stats") or path.startswith("/telegram-webhook-info") or path == "/metrics":
            limiter = _api_limiter
        else:
            return  # No rate limit for health check, static, etc.

        key = f"{client_ip}:{path}"
        if not limiter.is_allowed(key):
            logger.warning("Rate limit exceeded for %s on %s", client_ip, path)
            METRICS.inc("rate_limit_exceeded")
            return app.make_response(("Too Many Requests", 429))


# ---------------------------------------------------------------------------
# CORS headers (for future web dashboard)
# ---------------------------------------------------------------------------

def register_cors(app: Flask, origins: str = "*") -> None:
    """Add basic CORS headers to API responses."""

    @app.after_request
    def _add_cors(response: Response) -> Response:
        response.headers["Access-Control-Allow-Origin"] = origins
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Request-ID"
        return response