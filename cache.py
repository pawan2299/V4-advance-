from __future__ import annotations

import logging
import threading
import time
import hashlib
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class CacheEntry:
    value: Any
    expires_at: float
    last_accessed: float = 0.0  # For LRU eviction


class TTLCache:
    """Thread-safe TTL cache with LRU eviction when at capacity."""

    def __init__(self, maxsize: int = 2000, ttl: int = 1800):
        self.maxsize = maxsize
        self.ttl = ttl
        self._store: dict[str, CacheEntry] = {}
        self._lock = threading.Lock()

    def _now(self) -> float:
        return time.time()

    def _prune(self) -> None:
        """Remove expired entries and evict LRU entries if over capacity."""
        now = self._now()
        # Remove expired
        expired = [k for k, v in self._store.items() if v.expires_at <= now]
        for key in expired:
            self._store.pop(key, None)
        # LRU eviction if still over capacity
        if len(self._store) > self.maxsize:
            overflow = len(self._store) - self.maxsize
            # Sort by last_accessed, evict oldest
            sorted_keys = sorted(self._store.keys(),
                                 key=lambda k: self._store[k].last_accessed)
            for key in sorted_keys[:overflow]:
                self._store.pop(key, None)

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if not entry:
                return None
            if entry.expires_at <= self._now():
                self._store.pop(key, None)
                return None
            # Update last accessed for LRU
            entry.last_accessed = self._now()
            return entry.value

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        with self._lock:
            self._prune()
            self._store[key] = CacheEntry(
                value=value,
                expires_at=self._now() + (ttl or self.ttl),
                last_accessed=self._now(),
            )

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def stats(self) -> dict[str, int]:
        with self._lock:
            self._prune()
            return {"size": len(self._store), "maxsize": self.maxsize}

    def has(self, key: str) -> bool:
        with self._lock:
            entry = self._store.get(key)
            if not entry:
                return False
            if entry.expires_at <= self._now():
                self._store.pop(key, None)
                return False
            return True


def normalize_text(text: str) -> str:
    return " ".join(text.lower().strip().split())


def cache_key(*parts: str) -> str:
    raw = "||".join(normalize_text(part) for part in parts if part is not None)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()