"""Shared utility functions — retry decorator, sanitization, token redaction, metrics."""

from __future__ import annotations

import logging
import re
import time
import random
import functools
from typing import Any, Callable, TypeVar, Tuple

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# ---------------------------------------------------------------------------
# Retry decorator with exponential back-off + jitter
# ---------------------------------------------------------------------------

class RetryExhausted(Exception):
    """Raised when all retry attempts are exhausted."""


def retry(
    max_attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 30.0,
    retryable: Tuple[type, ...] | None = None,
    on_retry: Callable[[int, Exception, float], None] | None = None,
) -> Callable[[F], F]:
    """
    Decorator that retries a function on exception.

    Args:
        max_attempts: Total calls (1 initial + retries).
        base_delay: Base delay in seconds before first retry.
        max_delay: Cap on any single delay.
        retryable: Tuple of exception types that trigger retry.
                   If None, retries on all exceptions.
        on_retry: Optional callback(attempt, error, delay) for logging.
    """
    if retryable is None:
        retryable = (Exception,)

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_error: Exception | None = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except retryable as exc:
                    last_error = exc
                    if attempt >= max_attempts - 1:
                        break
                    jitter = random.uniform(0, min(base_delay, 1.0))
                    delay = min(base_delay * (2 ** attempt) + jitter, max_delay)
                    if on_retry:
                        on_retry(attempt + 1, exc, delay)
                    time.sleep(delay)
            raise RetryExhausted(
                f"{func.__name__} failed after {max_attempts} attempts"
            ) from last_error

        return wrapper  # type: ignore[return-value]
    return decorator


# ---------------------------------------------------------------------------
# Text cleaning (strips control/unicode exploit characters)
# ---------------------------------------------------------------------------

# Allow letters, digits, common punctuation, CJK ranges, Devanagari
_CLEAN_RE = re.compile(r"[^\w\s.,!?@#'\"'\-\u0900-\u097F\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]")


def clean_input_text(text: str) -> str:
    """Strip non-printable / control characters that could exploit unicode
    normalisation quirks in downstream systems.

    NOTE: This is NOT prompt-injection protection — plain English injection
    text (e.g. "ignore previous instructions") passes through unchanged.
    Actual injection mitigation is handled via system-prompt hardening
    and output length capping in the prompt template.
    """
    return _CLEAN_RE.sub("", text.strip())


# Backward-compatible alias (deprecated — use clean_input_text)
sanitize_text = clean_input_text


def truncate(text: str, max_len: int = 500, suffix: str = "...") -> str:
    """Truncate text to max_len characters."""
    if len(text) <= max_len:
        return text
    return text[: max_len - len(suffix)] + suffix


# ---------------------------------------------------------------------------
# Token / secret redaction for safe logging
# ---------------------------------------------------------------------------

def redact_secret(value: str, visible: int = 6) -> str:
    """Show first *visible* chars, mask the rest."""
    if not value or len(value) <= visible:
        return "***"
    return value[:visible] + "***"


# ---------------------------------------------------------------------------
# Metrics helper
# ---------------------------------------------------------------------------

class SimpleMetrics:
    """Thread-safe in-process counters for Prometheus-style metrics."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}
        self._gauges: dict[str, float] = {}
        import threading
        self._lock = threading.Lock()

    def inc(self, name: str, value: int = 1) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + value

    def set_gauge(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = value

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
            }


# Global metrics instance
METRICS = SimpleMetrics()