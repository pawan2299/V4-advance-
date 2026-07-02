"""Background maintenance tasks — DB cleanup, pool eviction, daily resets, token refresh.

These run on a simple threading.Timer loop to avoid extra dependencies.
For Render free tier (single worker), this is sufficient.

IMPORTANT (Render free-tier limitation):
  The service sleeps after ~15 min of inactivity, which pauses ALL
  background threads including maintenance. UptimeRobot pings / every
  few minutes keep the service awake for health checks, but if the
  monitor fails or is misconfigured, token refresh and retry-queue
  processing will also stop. This is an inherent platform limitation
  — a paid plan or external cron (e.g., cron-job.org) would eliminate it.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# Interval between maintenance sweeps (seconds)
_MAINTENANCE_INTERVAL = 300  # 5 minutes


def _cleanup_old_processed_records() -> None:
    """Remove processed_comments and processed_events older than 7 days
    to prevent unbounded table growth on the free-tier Neon PostgreSQL."""
    try:
        from database import get_cursor
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        with get_cursor() as cur:
            cur.execute("DELETE FROM processed_comments WHERE created_at < %s", (cutoff,))
            deleted_comments = cur.rowcount
            cur.execute("DELETE FROM processed_events WHERE created_at < %s", (cutoff,))
            deleted_events = cur.rowcount
        if deleted_comments or deleted_events:
            logger.info("Cleanup: removed %d comments, %d events older than 7 days",
                        deleted_comments, deleted_events)
    except Exception:
        logger.exception("Failed to cleanup old processed records")


def _evict_stale_db_connections() -> None:
    """Force-close connections in the pool that have gone stale.
    psycopg2 ThreadedConnectionPool doesn't do this automatically."""
    try:
        from database import _pool, init_pool
        if _pool is None:
            return
        # Test one connection; if it fails, recreate the pool
        try:
            conn = _pool.getconn()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                _pool.putconn(conn)
            except Exception:
                try:
                    _pool.putconn(conn, close=True)
                except Exception:
                    pass
                logger.warning("Stale DB connection detected during maintenance, recreating pool")
                init_pool(force=True)
        except Exception:
            logger.warning("DB pool exhausted during maintenance, recreating pool")
            init_pool(force=True)
    except Exception:
        logger.exception("DB pool maintenance failed")


def _reset_daily_counters() -> None:
    """Reset in-memory rate-limit deques at midnight to free memory.
    The deques already self-clean, but a daily sweep prevents accumulation
    of date-stale RPD entries."""
    try:
        from gemini_client import _model_rpd_calls
        now_str = time.strftime("%Y-%m-%d")
        cleaned = 0
        for key, deque_entries in _model_rpd_calls.items():
            # Remove any entries that aren't from today
            while deque_entries and deque_entries[0] != now_str:
                deque_entries.popleft()
                cleaned += 1
            if not deque_entries:
                # Empty deque for a model that hasn't been used today — fine
                pass
        if cleaned:
            logger.debug("Cleaned %d stale RPD entries", cleaned)
    except Exception:
        logger.exception("Daily counter reset failed")


def _compact_bot_state() -> None:
    """Remove expired cooldown entries from bot_state table."""
    try:
        from database import get_cursor
        now_ts = str(time.time())
        with get_cursor() as cur:
            cur.execute(
                "DELETE FROM bot_state WHERE key LIKE 'cooldown:%%' AND value < %s",
                (now_ts,),
            )
            deleted = cur.rowcount
        if deleted:
            logger.info("Compacted %d expired cooldown entries from bot_state", deleted)
    except Exception:
        logger.exception("Failed to compact bot_state cooldowns")


def _refresh_tokens_if_needed() -> None:
    """Check token expiry timestamps and refresh the Graph token proactively.

    Meta long-lived tokens last ~60 days. We store the expiry epoch in bot_state
    after each successful refresh or token exchange. This task runs every sweep
    (5 min) but only actually calls the API when within the refresh buffer window.
    """
    try:
        from database import get_state, set_state
        from instagram_api import refresh_instagram_token, _TOKEN_EXPIRY_KEY

        expiry_str = get_state(_TOKEN_EXPIRY_KEY, "")
        if not expiry_str:
            # No expiry recorded yet — set a default 59 days from now so we
            # don't spam-refresh. The /tokenrefresh command will set the real one.
            logger.debug("No token expiry recorded; skipping auto-refresh")
            return

        try:
            expires_at = float(expiry_str)
        except ValueError:
            return

        now = time.time()
        if now < (expires_at - 3600):  # More than 1 hour until expiry
            return

        logger.info("Graph token nearing expiry — attempting proactive refresh")
        result = refresh_instagram_token()
        if result:
            new_token = result.get("access_token", "")
            expires_in = result.get("expires_in", 5184000)  # default ~60 days
            if new_token:
                set_state(_TOKEN_EXPIRY_KEY, str(now + expires_in))
                logger.info("Token refreshed; new expiry in %d seconds", expires_in)
        else:
            logger.warning("Proactive token refresh failed — will retry next sweep")
    except Exception:
        logger.exception("Token refresh maintenance task failed")


def _process_retry_queue() -> None:
    """Retry up to 5 failed replies that are at least 2 minutes old.
    Max 3 attempts per comment, then discard.
    """
    try:
        from database import dequeue_failed_replies, remove_failed_reply
        from instagram_api import reply_to_comment
        from database import claim_event, mark_replied

        pending = dequeue_failed_replies(limit=5)
        if not pending:
            return

        logger.info("Retry queue: processing %d pending replies", len(pending))
        for item in pending:
            cid = item["comment_id"]
            reply_text = item["reply_text"]
            attempt = item.get("attempt_count", 1)
            if reply_to_comment(cid, reply_text):
                claim_event(cid)
                mark_replied(cid)
                remove_failed_reply(cid)
                logger.info("Retry succeeded for %s (attempt %d)", cid[:10], attempt + 1)
            else:
                logger.warning("Retry failed for %s (attempt %d/3)", cid[:10], attempt + 1)
    except Exception:
        logger.exception("Retry queue processing failed")


def _maintenance_sweep() -> None:
    """Run all maintenance tasks."""
    logger.debug("Running maintenance sweep")
    _cleanup_old_processed_records()
    _evict_stale_db_connections()
    _reset_daily_counters()
    _compact_bot_state()
    _process_retry_queue()
    _refresh_tokens_if_needed()


_maintenance_thread: threading.Thread | None = None
_stop_event = threading.Event()


def start_background_tasks() -> None:
    """Start the background maintenance loop. Idempotent."""
    global _maintenance_thread
    if _maintenance_thread is not None and _maintenance_thread.is_alive():
        return

    def _loop() -> None:
        while not _stop_event.is_set():
            try:
                _maintenance_sweep()
            except Exception:
                logger.exception("Maintenance sweep failed")
            _stop_event.wait(_MAINTENANCE_INTERVAL)

    _maintenance_thread = threading.Thread(target=_loop, daemon=True, name="maintenance")
    _maintenance_thread.start()
    logger.info("Background maintenance started (interval=%ds)", _MAINTENANCE_INTERVAL)


def stop_background_tasks() -> None:
    """Signal the maintenance loop to stop and wait for it."""
    _stop_event.set()
    if _maintenance_thread is not None:
        _maintenance_thread.join(timeout=10)
        logger.info("Background maintenance stopped")