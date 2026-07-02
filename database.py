from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Any

import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor

from config import SETTINGS

logger = logging.getLogger(__name__)

_pool: pool.ThreadedConnectionPool | None = None
_pool_lock = threading.Lock()


def init_pool(force: bool = False) -> None:
    global _pool
    with _pool_lock:
        if _pool is not None and not force:
            return
        if _pool is not None and force:
            try:
                _pool.closeall()
            except Exception:
                logger.exception("Failed to close old connection pool")
        _pool = pool.ThreadedConnectionPool(
            minconn=SETTINGS.db_pool_min,
            maxconn=SETTINGS.db_pool_max,
            dsn=SETTINGS.database_url,
            cursor_factory=RealDictCursor,
            connect_timeout=5,
        )
        logger.info("Database pool initialized (min=%d, max=%d)", SETTINGS.db_pool_min, SETTINGS.db_pool_max)


def get_conn():
    if _pool is None:
        init_pool()
    if _pool is None:
        raise RuntimeError("Database pool not initialized")
    conn = _pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
    except Exception:
        # Close ONLY the bad connection — don't nuke the entire pool
        # which would break concurrent requests using other connections.
        logger.warning("Stale connection detected, closing only that connection")
        try:
            conn.close()
        except Exception:
            pass
        # Try to get a fresh connection from the existing pool.
        # The pool will create a new one to maintain minconn.
        try:
            conn = _pool.getconn()
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            return conn
        except Exception:
            # Pool itself is broken (e.g., all connections stale) — now safe
            # to recreate since we've already closed our bad connection.
            logger.warning("Pool connections exhausted, recreating pool")
            try:
                conn.rollback()
            except Exception:
                pass
            init_pool(force=True)
            conn = _pool.getconn()
    return conn


@contextmanager
def get_cursor():
    """Context manager that yields a cursor, auto-commits on success,
    auto-rollbacks on exception, and ALWAYS returns the connection to the pool."""
    conn = None
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            yield cur
        conn.commit()
    except Exception:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        raise
    finally:
        if conn:
            try:
                _pool.putconn(conn) if _pool else None
            except Exception:
                logger.warning("Failed to return connection to pool")


def db_ping() -> bool:
    try:
        with get_cursor() as cur:
            cur.execute("SELECT 1")
        return True
    except Exception:
        logger.exception("Database health check failed")
        return False


def init_keywords_table() -> None:
    with get_cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS custom_keywords (
                keyword TEXT PRIMARY KEY,
                reply TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
        # Add index for keyword scan optimization
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_custom_keywords_keyword
            ON custom_keywords (keyword)
            """
        )


def init_db() -> None:
    init_pool()
    with get_cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_comments (
                comment_id TEXT PRIMARY KEY,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_events (
                event_id TEXT PRIMARY KEY,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS dm_cooldowns (
                user_id TEXT PRIMARY KEY,
                sent_at TIMESTAMPTZ DEFAULT NOW(),
                dm_type TEXT DEFAULT 'welcome'
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_usage (
                id BIGSERIAL PRIMARY KEY,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                status TEXT NOT NULL,
                latency_ms INTEGER,
                prompt_tokens INTEGER,
                response_tokens INTEGER,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS model_usage (
                key_id TEXT NOT NULL,
                model TEXT NOT NULL,
                requests_today INTEGER NOT NULL DEFAULT 0,
                last_used DATE NOT NULL DEFAULT CURRENT_DATE,
                success_count INTEGER NOT NULL DEFAULT 0,
                failure_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (key_id, model)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS provider_usage (
                provider TEXT PRIMARY KEY,
                requests_today INTEGER NOT NULL DEFAULT 0,
                success_count INTEGER NOT NULL DEFAULT 0,
                failure_count INTEGER NOT NULL DEFAULT 0,
                last_used DATE NOT NULL DEFAULT CURRENT_DATE
            )
            """
        )
        # Indexes
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_processed_comments_created_at
            ON processed_comments(created_at DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_processed_events_created_at
            ON processed_events(created_at DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ai_usage_created_at
            ON ai_usage(created_at DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ai_usage_provider_model
            ON ai_usage(provider, model, created_at DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_bot_state_key_prefix
            ON bot_state (key text_pattern_ops)
            """
        )
        # Retry queue: failed replies that should be retried
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS failed_replies (
                comment_id TEXT PRIMARY KEY,
                comment_data JSONB NOT NULL,
                reply_text TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                last_attempt_at TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_failed_replies_created_at
            ON failed_replies(created_at)
            """
        )
        _ensure_default_state(cur)
    init_keywords_table()
    logger.info("Database initialized")


def _ensure_default_state(cur) -> None:
    defaults = {
        "bot_paused": "false",
        "gemini_enabled": "true",
        "safe_mode": "false",
        "gemini_consecutive_429": "0",
        "gemini_circuit_breaker_until": "0",
    }
    for key, value in defaults.items():
        cur.execute(
            """
            INSERT INTO bot_state (key, value)
            VALUES (%s, %s)
            ON CONFLICT (key) DO NOTHING
            """,
            (key, value),
        )


# -------------------- State --------------------

def get_state(key: str, default: str = "") -> str:
    with get_cursor() as cur:
        cur.execute("SELECT value FROM bot_state WHERE key = %s", (key,))
        row = cur.fetchone()
        return row["value"] if row else default


def set_state(key: str, value: str) -> None:
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO bot_state (key, value, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (key) DO UPDATE
            SET value = EXCLUDED.value,
                updated_at = NOW()
            """,
            (key, value),
        )


def is_bot_paused() -> bool:
    return get_state("bot_paused", "false") == "true"


def is_gemini_enabled() -> bool:
    return get_state("gemini_enabled", "true") == "true"


def is_safe_mode() -> bool:
    return get_state("safe_mode", "false") == "true"


def set_bot_paused(value: bool) -> None:
    set_state("bot_paused", "true" if value else "false")


def set_gemini_enabled(value: bool) -> None:
    set_state("gemini_enabled", "true" if value else "false")


def set_safe_mode(value: bool) -> None:
    set_state("safe_mode", "true" if value else "false")


# -------------------- Dedup --------------------

def claim_event(event_id: str) -> bool:
    if not event_id:
        return False
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO processed_events (event_id)
            VALUES (%s)
            ON CONFLICT (event_id) DO NOTHING
            """,
            (event_id,),
        )
        return cur.rowcount == 1


def is_already_replied(comment_id: str) -> bool:
    with get_cursor() as cur:
        cur.execute("SELECT 1 FROM processed_comments WHERE comment_id = %s", (comment_id,))
        return cur.fetchone() is not None


def mark_replied(comment_id: str) -> None:
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO processed_comments (comment_id)
            VALUES (%s)
            ON CONFLICT (comment_id) DO NOTHING
            """,
            (comment_id,),
        )


def claim_welcome_dm(user_id: str) -> bool:
    if not user_id:
        return False
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO dm_cooldowns (user_id, sent_at, dm_type)
            VALUES (%s, NOW(), 'welcome')
            ON CONFLICT (user_id) DO NOTHING
            """,
            (user_id,),
        )
        return cur.rowcount == 1


# -------------------- Keywords --------------------

def add_keyword(keyword: str, reply: str) -> None:
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO custom_keywords (keyword, reply)
            VALUES (%s, %s)
            ON CONFLICT (keyword) DO UPDATE SET reply = EXCLUDED.reply
            """,
            (keyword.lower().strip(), reply.strip()),
        )


def remove_keyword(keyword: str) -> bool:
    with get_cursor() as cur:
        cur.execute("DELETE FROM custom_keywords WHERE keyword = %s", (keyword.lower().strip(),))
        return cur.rowcount > 0


def list_keywords() -> list[dict[str, Any]]:
    with get_cursor() as cur:
        cur.execute("SELECT keyword, reply, created_at FROM custom_keywords ORDER BY created_at DESC")
        return list(cur.fetchall())


def get_keyword_reply(text: str) -> str | None:
    """Check if any configured keyword appears in the comment text.
    Uses a single query with ILIKE for DB-level matching."""
    text_l = text.lower()
    with get_cursor() as cur:
        # Fetch all keywords (they should be cached in production)
        cur.execute("SELECT keyword, reply FROM custom_keywords")
        for row in cur.fetchall():
            if row["keyword"] in text_l:
                return row["reply"]
    return None


# -------------------- AI Usage --------------------

def _today_key(prefix: str, name: str) -> str:
    return f"{prefix}:{name}:{date.today().isoformat()}"


def increment_daily_counter(prefix: str, name: str) -> int:
    key = _today_key(prefix, name)
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO bot_state (key, value, updated_at)
            VALUES (%s, '1', NOW())
            ON CONFLICT (key) DO UPDATE
            SET value = (COALESCE(bot_state.value, '0')::INT + 1)::TEXT,
                updated_at = NOW()
            RETURNING value
            """,
            (key,),
        )
        row = cur.fetchone()
        return int(row["value"]) if row else 0


def get_daily_counter(prefix: str, name: str) -> int:
    key = _today_key(prefix, name)
    with get_cursor() as cur:
        cur.execute("SELECT value FROM bot_state WHERE key = %s", (key,))
        row = cur.fetchone()
        return int(row["value"]) if row else 0


def set_model_cooldown(model_key: str, until_ts: float) -> None:
    set_state(f"cooldown:model:{model_key}", str(until_ts))


def is_model_on_cooldown(model_key: str) -> bool:
    value = get_state(f"cooldown:model:{model_key}", "0")
    if not value or value == "0":
        return False
    try:
        return datetime.now(timezone.utc).timestamp() < float(value)
    except ValueError:
        return False


def set_key_cooldown(key_id: str, until_ts: float) -> None:
    set_state(f"cooldown:key:{key_id}", str(until_ts))


def is_key_on_cooldown(key_id: str) -> bool:
    value = get_state(f"cooldown:key:{key_id}", "0")
    if not value or value == "0":
        return False
    try:
        return datetime.now(timezone.utc).timestamp() < float(value)
    except ValueError:
        return False


def get_model_usage_today(key_id: str) -> dict[str, dict[str, int]]:
    """Real today's usage per model for a key."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT model, requests_today, success_count, failure_count
            FROM model_usage
            WHERE key_id = %s AND last_used = CURRENT_DATE
            """,
            (key_id,),
        )
        return {
            row["model"]: {
                "requests_today": row["requests_today"],
                "success_count": row["success_count"],
                "failure_count": row["failure_count"],
            }
            for row in cur.fetchall()
        }


def get_key_requests_today(key_id: str) -> int:
    usage = get_model_usage_today(key_id)
    return sum(v["requests_today"] for v in usage.values())


def log_ai_usage(provider: str, model: str, status: str, latency_ms: int | None = None,
                 prompt_tokens: int | None = None, response_tokens: int | None = None) -> None:
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO ai_usage (provider, model, status, latency_ms, prompt_tokens, response_tokens)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (provider, model, status, latency_ms, prompt_tokens, response_tokens),
        )


def bump_model_usage(key_id: str, model: str, success: bool) -> int:
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO model_usage (key_id, model, requests_today, last_used, success_count, failure_count)
            VALUES (%s, %s, 1, CURRENT_DATE, %s, %s)
            ON CONFLICT (key_id, model) DO UPDATE
            SET requests_today = CASE
                    WHEN model_usage.last_used = CURRENT_DATE THEN model_usage.requests_today + 1
                    ELSE 1
                END,
                last_used = CURRENT_DATE,
                success_count = model_usage.success_count + %s,
                failure_count = model_usage.failure_count + %s
            RETURNING requests_today
            """,
            (key_id, model, 1 if success else 0, 0 if success else 1, 1 if success else 0, 0 if success else 1),
        )
        row = cur.fetchone()
        return int(row["requests_today"]) if row else 0


def bump_provider_usage(provider: str, success: bool) -> int:
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO provider_usage (provider, requests_today, success_count, failure_count, last_used)
            VALUES (%s, 1, %s, %s, CURRENT_DATE)
            ON CONFLICT (provider) DO UPDATE
            SET requests_today = CASE
                    WHEN provider_usage.last_used = CURRENT_DATE THEN provider_usage.requests_today + 1
                    ELSE 1
                END,
                last_used = CURRENT_DATE,
                success_count = provider_usage.success_count + %s,
                failure_count = provider_usage.failure_count + %s
            RETURNING requests_today
            """,
            (provider, 1 if success else 0, 0 if success else 1, 1 if success else 0, 0 if success else 1),
        )
        row = cur.fetchone()
        return int(row["requests_today"]) if row else 0


def get_ai_usage_summary(days: int = 1) -> list[dict[str, Any]]:
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT provider, model, status, COUNT(*) AS count, COALESCE(AVG(latency_ms), 0) AS avg_latency_ms
            FROM ai_usage
            WHERE created_at >= NOW() - (%s || ' days')::interval
            GROUP BY provider, model, status
            ORDER BY count DESC
            """,
            (days,),
        )
        return list(cur.fetchall())


def get_stats() -> dict[str, Any]:
    with get_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS c FROM processed_comments")
        comments = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) AS c FROM dm_cooldowns")
        dms = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) AS c FROM processed_events")
        events = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) AS c FROM ai_usage WHERE created_at >= NOW() - INTERVAL '24 hours'")
        ai_today = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) AS c FROM custom_keywords")
        kw = cur.fetchone()["c"]

    return {
        "comments_replied": comments,
        "welcome_dms_sent": dms,
        "processed_events": events,
        "ai_calls_24h": ai_today,
        "keywords": kw,
        "bot_paused": is_bot_paused(),
        "gemini_enabled": is_gemini_enabled(),
        "safe_mode": is_safe_mode(),
    }


def get_recent_activity(limit: int = 10) -> list[dict[str, Any]]:
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT action, created_at FROM (
                SELECT 'comment replied' AS action, created_at FROM processed_comments
                UNION ALL
                SELECT 'welcome dm sent' AS action, sent_at AS created_at FROM dm_cooldowns
                UNION ALL
                SELECT 'event processed' AS action, created_at FROM processed_events
            ) s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        return list(cur.fetchall())


# -------------------- Maintenance --------------------

def cleanup_old_records(days: int = 7) -> tuple[int, int]:
    """Remove old processed_comments and processed_events. Returns (comments_deleted, events_deleted)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    deleted_comments = 0
    deleted_events = 0
    try:
        with get_cursor() as cur:
            cur.execute("DELETE FROM processed_comments WHERE created_at < %s", (cutoff,))
            deleted_comments = cur.rowcount
            cur.execute("DELETE FROM processed_events WHERE created_at < %s", (cutoff,))
            deleted_events = cur.rowcount
        if deleted_comments or deleted_events:
            logger.info("Cleanup: removed %d comments, %d events older than %d days",
                        deleted_comments, deleted_events, days)
    except Exception:
        logger.exception("Failed to cleanup old records")
    return deleted_comments, deleted_events


def get_pool_status() -> dict[str, Any]:
    """Return pool connection stats for monitoring."""
    global _pool
    if _pool is None:
        return {"status": "not_initialized"}
    try:
        return {
            "status": "ok",
            "minconn": _pool.minconn,
            "maxconn": _pool.maxconn,
            # psycopg2 doesn't expose active count directly, but we can check
        }
    except Exception:
        return {"status": "error"}


# -------------------- Retry Queue --------------------

import json as _json


def enqueue_failed_reply(comment_id: str, comment_data: dict, reply_text: str) -> None:
    """Add a failed reply to the retry queue (upsert: first failure wins)."""
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                INSERT INTO failed_replies (comment_id, comment_data, reply_text, attempt_count, last_attempt_at)
                VALUES (%s, %s, %s, 1, NOW())
                ON CONFLICT (comment_id) DO UPDATE
                SET attempt_count = failed_replies.attempt_count + 1,
                    last_attempt_at = NOW()
                """,
                (comment_id, _json.dumps(comment_data), reply_text),
            )
    except Exception:
        logger.exception("Failed to enqueue retry for %s", comment_id[:10])


def dequeue_failed_replies(limit: int = 5) -> list[dict[str, Any]]:
    """Fetch pending failed replies, ordered by oldest first. Max 3 retries."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT comment_id, comment_data, reply_text, attempt_count, created_at
            FROM failed_replies
            WHERE attempt_count < 3
              AND last_attempt_at < NOW() - INTERVAL '2 minutes'
            ORDER BY created_at ASC
            LIMIT %s
            """,
            (limit,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            r["comment_data"] = _json.loads(r["comment_data"])
        return rows


def remove_failed_reply(comment_id: str) -> None:
    """Remove a successfully retried entry."""
    try:
        with get_cursor() as cur:
            cur.execute("DELETE FROM failed_replies WHERE comment_id = %s", (comment_id,))
    except Exception:
        logger.exception("Failed to remove retry entry for %s", comment_id[:10])