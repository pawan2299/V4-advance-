"""Deep health-check system with dependency probing."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)


@dataclass
class HealthCheck:
    name: str
    status: str  # "ok" | "degraded" | "down"
    latency_ms: float = 0.0
    details: str = ""
    timestamp: float = field(default_factory=time.time)


def _check_database() -> HealthCheck:
    """Probe database connectivity and query latency."""
    start = time.time()
    try:
        from database import db_ping, get_cursor
        ok = db_ping()
        latency = (time.time() - start) * 1000
        if ok:
            # Extra: try a lightweight query
            try:
                with get_cursor() as cur:
                    cur.execute("SELECT 1 AS ping")
            except Exception:
                pass
            return HealthCheck(name="database", status="ok", latency_ms=latency)
        return HealthCheck(name="database", status="down", latency_ms=latency, details="db_ping() returned False")
    except Exception as exc:
        latency = (time.time() - start) * 1000
        return HealthCheck(name="database", status="down", latency_ms=latency, details=str(exc)[:200])


def _check_gemini() -> HealthCheck:
    """Check if Gemini API keys are configured and not all on cooldown."""
    start = time.time()
    try:
        from config import SETTINGS
        from database import is_key_on_cooldown

        keys = SETTINGS.gemini_api_keys
        if not keys:
            return HealthCheck(name="gemini_api", status="degraded", latency_ms=0, details="No API keys configured")

        active = 0
        for idx in range(1, len(keys) + 1):
            if not is_key_on_cooldown(f"key_{idx}"):
                active += 1

        latency = (time.time() - start) * 1000
        if active > 0:
            return HealthCheck(
                name="gemini_api",
                status="ok",
                latency_ms=latency,
                details=f"{active}/{len(keys)} keys active",
            )
        return HealthCheck(
            name="gemini_api",
            status="degraded",
            latency_ms=latency,
            details="All keys on cooldown",
        )
    except Exception as exc:
        latency = (time.time() - start) * 1000
        return HealthCheck(name="gemini_api", status="down", latency_ms=latency, details=str(exc)[:200])


def _check_groq() -> HealthCheck:
    """Check if Groq fallback is configured."""
    start = time.time()
    try:
        from config import SETTINGS
        latency = (time.time() - start) * 1000
        if SETTINGS.groq_api_key:
            return HealthCheck(name="groq_api", status="ok", latency_ms=latency)
        return HealthCheck(name="groq_api", status="degraded", latency_ms=latency, details="No API key configured")
    except Exception as exc:
        latency = (time.time() - start) * 1000
        return HealthCheck(name="groq_api", status="down", latency_ms=latency, details=str(exc)[:200])


def _check_telegram() -> HealthCheck:
    """Check Telegram bot token validity."""
    start = time.time()
    try:
        from config import SETTINGS
        from telegram_bot import get_webhook_info
        if not SETTINGS.telegram_bot_token:
            return HealthCheck(name="telegram_bot", status="degraded", latency_ms=0, details="No token configured")
        info = get_webhook_info()
        latency = (time.time() - start) * 1000
        if info.get("ok"):
            return HealthCheck(name="telegram_bot", status="ok", latency_ms=latency)
        return HealthCheck(name="telegram_bot", status="degraded", latency_ms=latency, details=str(info.get("error", ""))[:200])
    except Exception as exc:
        latency = (time.time() - start) * 1000
        return HealthCheck(name="telegram_bot", status="down", latency_ms=latency, details=str(exc)[:200])


def _check_instagram() -> HealthCheck:
    """Check Instagram token validity."""
    start = time.time()
    try:
        from instagram_api import check_token_validity
        graph_ok = check_token_validity("graph")
        latency = (time.time() - start) * 1000
        if graph_ok:
            return HealthCheck(name="instagram_graph", status="ok", latency_ms=latency)
        return HealthCheck(name="instagram_graph", status="degraded", latency_ms=latency, details="Graph token invalid")
    except Exception as exc:
        latency = (time.time() - start) * 1000
        return HealthCheck(name="instagram_graph", status="down", latency_ms=latency, details=str(exc)[:200])


def _check_memory() -> HealthCheck:
    """Check process memory usage."""
    start = time.time()
    try:
        import resource
        rusage = resource.getrusage(resource.RUSAGE_SELF)
        maxrss_mb = rusage.ru_maxrss / 1024  # Linux: KB -> MB
        latency = (time.time() - start) * 1000
        status = "ok" if maxrss_mb < 256 else ("degraded" if maxrss_mb < 450 else "down")
        return HealthCheck(
            name="memory",
            status=status,
            latency_ms=latency,
            details=f"RSS {maxrss_mb:.1f} MB",
        )
    except Exception:
        return HealthCheck(name="memory", status="ok", latency_ms=0, details="resource module unavailable")


_CHECKS: List[Callable[[], HealthCheck]] = [
    _check_database,
    _check_gemini,
    _check_groq,
    _check_telegram,
    _check_instagram,
    _check_memory,
]


def run_all_checks() -> Dict[str, Any]:
    """Run every health check and return a structured report."""
    results = [check() for check in _CHECKS]
    statuses = {r.status for r in results}
    overall = "ok" if statuses == {"ok"} else ("degraded" if "down" not in statuses else "down")

    return {
        "status": overall,
        "checks": [
            {
                "name": r.name,
                "status": r.status,
                "latency_ms": round(r.latency_ms, 2),
                "details": r.details,
            }
            for r in results
        ],
    }