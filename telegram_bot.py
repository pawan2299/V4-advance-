from __future__ import annotations

import html
import logging
import traceback
import time
from datetime import datetime, timezone

import requests

from ai_router import clear_cache, get_status as ai_get_status
from config import SETTINGS
from database import (
    add_keyword,
    get_recent_activity,
    get_stats,
    is_bot_paused,
    is_gemini_enabled,
    is_safe_mode,
    list_keywords,
    remove_keyword,
    set_bot_paused,
    set_gemini_enabled,
    set_safe_mode,
    get_ai_usage_summary,
    db_ping,
)

logger = logging.getLogger(__name__)

# Session with connection pooling
_session = requests.Session()
_session.mount("https://api.telegram.org", requests.adapters.HTTPAdapter(
    pool_connections=4,
    pool_maxsize=8,
    max_retries=2,
))

AUTHORIZED_ADMINS = set(SETTINGS.telegram_admin_chat_ids)
API_BASE = f"https://api.telegram.org/bot{SETTINGS.telegram_bot_token}"

# Store menu state for conversation wizards with TTL eviction
_user_states: dict[str, dict] = {}
_USER_STATE_TTL = 600  # 10 minutes


def _clean_stale_user_states() -> None:
    """Remove user states older than TTL to prevent memory leaks."""
    now = time.time()
    stale = [k for k, v in _user_states.items() if now - v.get("created_at", 0) > _USER_STATE_TTL]
    for k in stale:
        del _user_states[k]
    if stale:
        logger.debug("Cleaned %d stale user states", len(stale))


def _escape(text: str) -> str:
    return html.escape(text or "")


def _inline_keyboard(buttons: list[list[tuple[str, str]]]) -> dict:
    keyboard = []
    for row in buttons:
        keyboard.append([{"text": text, "callback_data": data} for text, data in row])
    return {"inline_keyboard": keyboard}


def _send(chat_id: str, text: str, parse_mode: str = "HTML", reply_markup: dict | None = None, disable_notification: bool = False):
    if not SETTINGS.telegram_bot_token:
        return None
    try:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
            "disable_notification": disable_notification,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        resp = _session.post(f"{API_BASE}/sendMessage", json=payload, timeout=(5, 30))
        if not resp.ok:
            logger.error("Telegram send error %s: %s", resp.status_code, resp.text[:400])
            return None
        return resp.json()
    except Exception:
        logger.exception("Telegram request failed")
        return None


def _edit(chat_id: str, message_id: int, text: str, parse_mode: str = "HTML", reply_markup: dict | None = None):
    try:
        payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": parse_mode}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        resp = _session.post(f"{API_BASE}/editMessageText", json=payload, timeout=(5, 30))
        return resp.json() if resp.ok else None
    except Exception:
        logger.exception("Telegram edit failed")
        return None


def _answer_callback(callback_query_id: str, text: str | None = None):
    try:
        payload = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        _session.post(f"{API_BASE}/answerCallbackQuery", json=payload, timeout=(5, 10))
    except Exception:
        logger.exception("Telegram callback answer failed")


def _delete_message(chat_id: str, message_id: int):
    try:
        resp = _session.post(f"{API_BASE}/deleteMessage", json={"chat_id": chat_id, "message_id": message_id}, timeout=(5, 10))
        return resp.ok
    except Exception:
        logger.exception("Telegram delete message failed")
        return False


def get_webhook_info() -> dict:
    if not SETTINGS.telegram_bot_token:
        return {}
    try:
        resp = _session.get(f"{API_BASE}/getWebhookInfo", timeout=(5, 20))
        return resp.json() if resp.ok else {"ok": False, "error": resp.text[:200]}
    except Exception:
        logger.exception("Telegram getWebhookInfo failed")
        return {"ok": False}


def register_telegram_webhook() -> bool:
    if not SETTINGS.telegram_bot_token or not SETTINGS.public_base_url:
        logger.info("Skipping Telegram webhook registration (missing token or public base url)")
        return False
    url = SETTINGS.public_base_url.rstrip("/") + "/telegram-webhook"
    try:
        resp = _session.post(
            f"{API_BASE}/setWebhook",
            json={
                "url": url,
                "secret_token": SETTINGS.telegram_webhook_secret,
                "allowed_updates": ["message", "callback_query"],
            },
            timeout=(10, 30),
        )
        if not resp.ok:
            logger.error("Telegram setWebhook error %s: %s", resp.status_code, resp.text[:400])
            return False
        data = resp.json()
        logger.info("Telegram webhook set: %s", data)
        return bool(data.get("ok"))
    except Exception:
        logger.exception("Telegram webhook registration failed")
        return False


def _admin_only(chat_id: str) -> bool:
    return chat_id in AUTHORIZED_ADMINS


def handle_update(update: dict):
    try:
        callback_query = update.get("callback_query")
        if callback_query:
            handle_callback_query(callback_query)
            return

        message = update.get("message") or {}
        chat_id = str(message.get("chat", {}).get("id", ""))
        text = (message.get("text") or "").strip()
        if not chat_id or not _admin_only(chat_id):
            logger.warning("Unauthorized Telegram chat: %s", chat_id)
            return
        if not text:
            return

        # Handle /cancel in any state
        if text.lower() == "/cancel":
            if chat_id in _user_states:
                del _user_states[chat_id]
                _send(chat_id, "✅ Cancelled.", reply_markup=_inline_keyboard([[("🔙 Menu", "back:main")]]))
            else:
                _send(chat_id, "Nothing to cancel.", reply_markup=_inline_keyboard([[("🔙 Menu", "back:main")]]))
            return

        # Check for conversation wizard state
        _clean_stale_user_states()
        if chat_id in _user_states:
            handle_conversation_input(chat_id, text)
            return

        if text.startswith("/"):
            handle_command(chat_id, text)
        else:
            show_main_menu(chat_id)
    except Exception:
        logger.error("Telegram update handler failed:\n%s", traceback.format_exc())


def handle_command(chat_id: str, text: str):
    logger.info("Received Telegram command: %s | chat_id=%s", text.split()[0], chat_id)
    parts = text.split()
    cmd = parts[0].lower().split("@")[0]
    args = parts[1:]

    handlers = {
        "/start": lambda: show_main_menu(chat_id),
        "/help": lambda: show_main_menu(chat_id),
        "/menu": lambda: show_main_menu(chat_id),
        "/status": lambda: _send(chat_id, _status_text(), reply_markup=_inline_keyboard([[("🔄 Refresh", "refresh:status")]])),
        "/stats": lambda: _send(chat_id, _stats_text()),
        "/dashboard": lambda: show_dashboard(chat_id),
        "/pause": lambda: _set_pause(chat_id, True),
        "/resume": lambda: _set_pause(chat_id, False),
        "/ping": lambda: _send(chat_id, "🏓 Pong\n\n✅ Bot is running."),
        "/ai": lambda: _send(chat_id, _ai_text()),
        "/models": lambda: _send(chat_id, _ai_text()),
        "/quota": lambda: _send(chat_id, _ai_text()),
        "/analytics": lambda: show_analytics(chat_id),
        "/keywords": lambda: show_keywords_menu(chat_id),
        "/addkeyword": lambda: start_add_keyword_wizard(chat_id),
        "/removekeyword": lambda: start_remove_keyword_wizard(chat_id),
        "/clearcache": lambda: _clear_cache(chat_id),
        "/safe_on": lambda: _set_safe_mode(chat_id, True),
        "/safe_off": lambda: _set_safe_mode(chat_id, False),
        "/gemini_on": lambda: _set_gemini(chat_id, True),
        "/gemini_off": lambda: _set_gemini(chat_id, False),
        "/activity": lambda: _send(chat_id, _activity_text()),
        "/logs": lambda: show_logs(chat_id),
        "/health": lambda: show_health_check(chat_id),
        "/tokenrefresh": lambda: _token_refresh(chat_id),
    }

    handler = handlers.get(cmd)
    if handler:
        try:
            handler()
        except Exception as exc:
            logger.error("Command handler failed for %s: %s", cmd, traceback.format_exc())
            _send(chat_id, f"❌ Error executing command <b>{cmd}</b>: {_escape(str(exc))}")
    else:
        _send(chat_id, "❓ Unknown command. Use /menu to open the control panel.")


def handle_callback_query(callback_query: dict):
    try:
        chat_id = str(callback_query.get("message", {}).get("chat", {}).get("id", ""))
        if not _admin_only(chat_id):
            return
        data = callback_query.get("data", "")
        message_id = callback_query["message"]["message_id"]

        handled = False

        if data == "refresh:status":
            _edit(chat_id, message_id, _status_text(), reply_markup=_inline_keyboard([[("🔄 Refresh", "refresh:status")]]))
            handled = True
        elif data == "menu:dashboard":
            show_dashboard(chat_id)
            handled = True
        elif data == "menu:analytics":
            show_analytics(chat_id)
            handled = True
        elif data == "menu:keywords":
            show_keywords_menu(chat_id)
            handled = True
        elif data == "menu:settings":
            show_settings_menu(chat_id)
            handled = True
        elif data == "menu:activity":
            show_activity_log(chat_id)
            handled = True
        elif data == "menu:health":
            show_health_check(chat_id)
            handled = True
        elif data == "back:main":
            show_main_menu(chat_id, edit=True, message_id=message_id)
            handled = True
        elif data == "back:keywords":
            show_keywords_menu(chat_id, edit=True, message_id=message_id)
            handled = True
        elif data == "back:settings":
            show_settings_menu(chat_id, edit=True, message_id=message_id)
        elif data == "toggle:pause":
            set_bot_paused(not is_bot_paused())
            show_settings_menu(chat_id, edit=True, message_id=message_id)
            handled = True
        elif data == "toggle:gemini":
            set_gemini_enabled(not is_gemini_enabled())
            show_settings_menu(chat_id, edit=True, message_id=message_id)
            handled = True
        elif data == "toggle:safe":
            set_safe_mode(not is_safe_mode())
            show_settings_menu(chat_id, edit=True, message_id=message_id)
            handled = True
        elif data == "action:clearcache":
            clear_cache()
            _answer_callback(callback_query.get("id", ""), "✅ Cache cleared")
            show_settings_menu(chat_id, edit=True, message_id=message_id)
            handled = True
        elif data.startswith("kw:del:"):
            keyword = data[7:]
            remove_keyword(keyword)
            show_keywords_menu(chat_id, edit=True, message_id=message_id)
            _answer_callback(callback_query.get("id", ""), f"✅ Removed {keyword}")
            handled = True
        elif data == "kw:add":
            start_add_keyword_wizard(chat_id)
            _delete_message(chat_id, message_id)
            handled = True
        elif data == "kw:remove":
            start_remove_keyword_wizard(chat_id)
            _delete_message(chat_id, message_id)
            handled = True
        elif data == "refresh:analytics":
            show_analytics(chat_id, edit=True, message_id=message_id)
            handled = True
        elif data == "refresh:dashboard":
            show_dashboard(chat_id, edit=True, message_id=message_id)
            handled = True
        elif data == "refresh:activity":
            show_activity_log(chat_id, edit=True, message_id=message_id)
            handled = True

        if handled:
            _answer_callback(callback_query.get("id", ""))
        else:
            _answer_callback(callback_query.get("id", ""), "⚠️ Action not implemented yet")

    except Exception:
        logger.exception("Callback query handler failed")


def handle_conversation_input(chat_id: str, text: str):
    state = _user_states.get(chat_id, {})
    action = state.get("action")

    if action == "add_keyword_step1":
        _user_states[chat_id]["keyword"] = text[:64]
        _user_states[chat_id]["action"] = "add_keyword_step2"
        _send(chat_id, f"📝 Now send the <b>reply text</b> for keyword <code>{_escape(text[:64])}</code>:\n\n(Will be truncated to 500 chars)")
    elif action == "add_keyword_step2":
        keyword = state.get("keyword", "")
        reply = text[:500]
        add_keyword(keyword, reply)
        del _user_states[chat_id]
        _send(chat_id, f"✅ Keyword <code>{_escape(keyword)}</code> added successfully!", reply_markup=_inline_keyboard([[("🔙 Back to Keywords", "back:keywords")]]))
    elif action == "remove_keyword_step1":
        if remove_keyword(text):
            _send(chat_id, f"✅ Keyword <code>{_escape(text)}</code> removed!", reply_markup=_inline_keyboard([[("🔙 Back to Keywords", "back:keywords")]]))
        else:
            _send(chat_id, f"❌ Keyword <code>{_escape(text)}</code> not found.")
        del _user_states[chat_id]
    else:
        del _user_states[chat_id]


def _set_pause(chat_id: str, value: bool):
    set_bot_paused(value)
    _send(chat_id, f"✅ Bot {'paused' if value else 'resumed'}.")


def _set_safe_mode(chat_id: str, value: bool):
    set_safe_mode(value)
    _send(chat_id, f"✅ Safe mode {'enabled' if value else 'disabled'}.")


def _set_gemini(chat_id: str, value: bool):
    set_gemini_enabled(value)
    _send(chat_id, f"✅ Gemini {'enabled' if value else 'disabled'}.")


def _clear_cache(chat_id: str):
    clear_cache()
    _send(chat_id, "✅ AI cache cleared.")


# ==================== UI FUNCTIONS ====================

def show_main_menu(chat_id: str, edit: bool = False, message_id: int | None = None):
    stats = get_stats()
    ai_status = ai_get_status()

    bot_status = "⏸️ <b>PAUSED</b>" if stats["bot_paused"] else "✅ <b>RUNNING</b>"
    gemini_status = "🟢 ON" if stats["gemini_enabled"] else "🔴 OFF"
    safe_status = "🛡️ ON" if stats["safe_mode"] else "⚪ OFF"

    text = (
        f"🦚 <b>KrishnaVerse AI Control Panel</b>\n\n"
        f"├─ Status: {bot_status}\n"
        f"├─ Gemini: {gemini_status}\n"
        f"└─ Safe Mode: {safe_status}\n\n"
        f"📊 <b>Quick Stats:</b>\n"
        f"├─ Comments Replied: {stats['comments_replied']}\n"
        f"├─ Welcome DMs: {stats['welcome_dms_sent']}\n"
        f"└─ AI Calls (24h): {stats['ai_calls_24h']}\n\n"
        f"<i>Select an option below:</i>"
    )

    keyboard = _inline_keyboard([
        [("📈 Dashboard", "menu:dashboard"), ("📊 Analytics", "menu:analytics")],
        [("🔑 Keywords", "menu:keywords"), ("⚙️ Settings", "menu:settings")],
        [("📝 Activity Log", "menu:activity"), ("💚 Health Check", "menu:health")],
    ])

    if edit and message_id:
        _edit(chat_id, message_id, text, reply_markup=keyboard)
    else:
        _send(chat_id, text, reply_markup=keyboard)


def show_dashboard(chat_id: str, edit: bool = False, message_id: int | None = None):
    stats = get_stats()
    ai_status = ai_get_status()

    gemini_data = ai_status.get("gemini", {})
    keys = gemini_data.get("keys", [])
    total_quota_hits = sum(k.get("quota_hits_today", 0) for k in keys)
    total_requests = sum(k.get("requests_today", 0) for k in keys)

    text = (
        f"📈 <b>Bot Dashboard</b>\n\n"
        f"<b>🤖 Bot Status:</b>\n"
        f"├─ State: {'⏸️ Paused' if stats['bot_paused'] else '✅ Running'}\n"
        f"├─ Gemini: {'🟢 Enabled' if stats['gemini_enabled'] else '🔴 Disabled'}\n"
        f"└─ Safe Mode: {'🛡️ Active' if stats['safe_mode'] else '⚪ Inactive'}\n\n"
        f"<b>📊 Today's Performance:</b>\n"
        f"├─ Comments Replied: <b>{stats['comments_replied']}</b>\n"
        f"├─ Welcome DMs Sent: <b>{stats['welcome_dms_sent']}</b>\n"
        f"├─ Events Processed: <b>{stats['processed_events']}</b>\n"
        f"└─ AI API Calls: <b>{stats['ai_calls_24h']}</b>\n\n"
        f"<b>🧠 AI Usage:</b>\n"
        f"├─ Total Requests: <b>{total_requests}</b>\n"
        f"├─ Quota Hits: <b>{total_quota_hits}</b>\n"
        f"├─ Cache Size: <b>{ai_status.get('cache', {}).get('size', 0)}</b>\n"
        f"└─ Groq Fallback: {'✅' if ai_status.get('groq_enabled') else '❌'}\n\n"
        f"<b>🔑 Configured Keywords:</b> {stats['keywords']}"
    )

    keyboard = _inline_keyboard([
        [("🔄 Refresh", "refresh:dashboard"), ("🔙 Back", "back:main")],
    ])

    if edit and message_id:
        _edit(chat_id, message_id, text, reply_markup=keyboard)
    else:
        _send(chat_id, text, reply_markup=keyboard)


def show_analytics(chat_id: str, edit: bool = False, message_id: int | None = None):
    usage_summary = get_ai_usage_summary(days=1)

    if not usage_summary:
        text = "📊 <b>Analytics</b>\n\nNo AI usage data available yet."
    else:
        lines = ["📊 <b>AI Analytics (Last 24h)</b>\n"]

        provider_stats = {}
        for row in usage_summary:
            provider = row["provider"]
            model = row["model"]
            status = row["status"]
            count = row["count"]
            latency = row["avg_latency_ms"]

            key = f"{provider}/{model}"
            if key not in provider_stats:
                provider_stats[key] = {"success": 0, "error": 0, "total_latency": 0, "total": 0}

            if status == "success":
                provider_stats[key]["success"] += count
            else:
                provider_stats[key]["error"] += count
            provider_stats[key]["total_latency"] += latency * count
            provider_stats[key]["total"] += count

        for key, data in provider_stats.items():
            total = data["total"]
            success_rate = (data["success"] / total * 100) if total > 0 else 0
            avg_latency = data["total_latency"] / total if total > 0 else 0

            lines.append(f"<b>{key}</b>:")
            lines.append(f"  ├─ Requests: {total}")
            lines.append(f"  ├─ Success Rate: {success_rate:.1f}%")
            lines.append(f"  └─ Avg Latency: {avg_latency:.0f}ms")
            lines.append("")

        text = "\n".join(lines)

    keyboard = _inline_keyboard([
        [("🔄 Refresh", "refresh:analytics"), ("🔙 Back", "back:main")],
    ])

    if edit and message_id:
        _edit(chat_id, message_id, text, reply_markup=keyboard)
    else:
        _send(chat_id, text, reply_markup=keyboard)


def show_keywords_menu(chat_id: str, edit: bool = False, message_id: int | None = None):
    keywords = list_keywords()

    if not keywords:
        text = "🔑 <b>Keywords Manager</b>\n\nNo keywords configured yet.\n\nUse the buttons below to add your first keyword!"
    else:
        lines = [f"🔑 <b>Keywords ({len(keywords)})</b>\n"]
        for kw in keywords[:15]:
            lines.append(f"├─ <code>{_escape(kw['keyword'])}</code>")
            lines.append(f"│  └─ {_escape(kw['reply'][:50])}")
        if len(keywords) > 15:
            lines.append(f"\n<i>...and {len(keywords) - 15} more</i>")
        text = "\n".join(lines)

    keyboard = _inline_keyboard([
        [("➕ Add Keyword", "kw:add"), ("➖ Remove Keyword", "kw:remove")],
        [("🔙 Back", "back:main")],
    ])

    if keywords:
        delete_buttons = [(f"🗑️ {_escape(kw['keyword'][:20])}", f"kw:del:{kw['keyword']}") for kw in keywords[:10]]
        rows = [delete_buttons[i:i+2] for i in range(0, len(delete_buttons), 2)]
        keyboard = _inline_keyboard(rows + [[("🔙 Back", "back:main")]])

    if edit and message_id:
        _edit(chat_id, message_id, text, reply_markup=keyboard)
    else:
        _send(chat_id, text, reply_markup=keyboard)


def show_settings_menu(chat_id: str, edit: bool = False, message_id: int | None = None):
    paused = is_bot_paused()
    gemini = is_gemini_enabled()
    safe = is_safe_mode()

    text = (
        f"⚙️ <b>Bot Settings</b>\n\n"
        f"<b>Toggles:</b>\n"
        f"├─ Bot Status: {'⏸️ PAUSED' if paused else '✅ RUNNING'}\n"
        f"├─ Gemini AI: {'🟢 ENABLED' if gemini else '🔴 DISABLED'}\n"
        f"└─ Safe Mode: {'🛡️ ACTIVE' if safe else '⚪ INACTIVE'}\n\n"
        f"<i>Click a toggle to change its state:</i>"
    )

    pause_btn = "▶️ Resume Bot" if paused else "⏸️ Pause Bot"
    gemini_btn = "🔴 Disable Gemini" if gemini else "🟢 Enable Gemini"
    safe_btn = "🛡️ Disable Safe Mode" if safe else "🛡️ Enable Safe Mode"

    keyboard = _inline_keyboard([
        [(pause_btn, "toggle:pause")],
        [(gemini_btn, "toggle:gemini")],
        [(safe_btn, "toggle:safe")],
        [("🗑️ Clear AI Cache", "action:clearcache")],
        [("🔙 Back", "back:main")],
    ])

    if edit and message_id:
        _edit(chat_id, message_id, text, reply_markup=keyboard)
    else:
        _send(chat_id, text, reply_markup=keyboard)


def show_activity_log(chat_id: str, edit: bool = False, message_id: int | None = None):
    activities = get_recent_activity(20)

    if not activities:
        text = "📝 <b>Activity Log</b>\n\nNo recent activity recorded."
    else:
        lines = ["📝 <b>Recent Activity (Last 20)</b>\n"]
        for i, act in enumerate(activities, 1):
            action = act["action"]
            timestamp = act["created_at"]
            if isinstance(timestamp, datetime):
                time_str = timestamp.strftime("%H:%M %d/%m")
            else:
                time_str = str(timestamp)[:16]
            lines.append(f"{i}. <b>{_escape(action)}</b> @ {time_str}")
        text = "\n".join(lines)

    keyboard = _inline_keyboard([
        [("🔄 Refresh", "refresh:activity"), ("🔙 Back", "back:main")],
    ])

    if edit and message_id:
        _edit(chat_id, message_id, text, reply_markup=keyboard)
    else:
        _send(chat_id, text, reply_markup=keyboard)


def show_health_check(chat_id: str, edit: bool = False, message_id: int | None = None):
    db_ok = db_ping()
    ai_status = ai_get_status()
    gemini_keys = ai_status.get("gemini", {}).get("keys", [])
    has_gemini = len(gemini_keys) > 0
    has_groq = ai_status.get("groq_enabled", False)

    checks = []
    checks.append(("🗄️ Database", db_ok))
    checks.append(("🧠 Gemini API", has_gemini))
    checks.append(("⚡ Groq Fallback", has_groq))
    checks.append(("🤖 Bot Running", not is_bot_paused()))

    all_ok = all(ok for _, ok in checks)
    status_icon = "✅" if all_ok else "⚠️"

    lines = [f"{status_icon} <b>System Health Check</b>\n"]
    for name, ok in checks:
        icon = "✅" if ok else "❌"
        lines.append(f"{icon} {name}")

    lines.append("\n<b>Configuration:</b>")
    lines.append(f"├─ Cache Size: {ai_status.get('cache', {}).get('size', 0)}")
    lines.append(f"├─ Gemini Keys: {len(gemini_keys)}")
    lines.append(f"└─ Keywords: {len(list_keywords())}")

    text = "\n".join(lines)

    keyboard = _inline_keyboard([
        [("🔄 Check Again", "menu:health"), ("🔙 Back", "back:main")],
    ])

    if edit and message_id:
        _edit(chat_id, message_id, text, reply_markup=keyboard)
    else:
        _send(chat_id, text, reply_markup=keyboard)


def show_logs(chat_id: str):
    """Show recent activity log as a substitute for real log access."""
    try:
        activities = get_recent_activity(5)
        if not activities:
            text = "📋 <b>Recent Log Entries</b>\n\nNo activity recorded yet."
        else:
            lines = ["📋 <b>Recent Activity</b>\n"]
            for act in activities:
                action = act["action"]
                ts = act["created_at"]
                if isinstance(ts, datetime):
                    time_str = ts.strftime("%Y-%m-%d %H:%M:%S")
                else:
                    time_str = str(ts)[:19]
                lines.append(f"• {_escape(action)} — <code>{time_str}</code>")
            text = "\n".join(lines)
            text += "\n\n<i>For full logs, check your Render dashboard.</i>"
    except Exception:
        text = "📋 <b>System Logs</b>\n\nCould not fetch activity log.\n\n<i>Check your Render dashboard for full logs.</i>"

    keyboard = _inline_keyboard([[("🔙 Back", "back:main")]])
    _send(chat_id, text, reply_markup=keyboard)


def start_add_keyword_wizard(chat_id: str):
    _user_states[chat_id] = {"action": "add_keyword_step1", "created_at": time.time()}
    text = (
        "➕ <b>Add New Keyword</b>\n\n"
        "Please send the <b>keyword/phrase</b> you want to add.\n\n"
        "<i>Example: krishna, radhe radhe, jai shri krishna</i>\n\n"
        "Send /cancel to abort."
    )
    keyboard = _inline_keyboard([[("❌ Cancel", "back:keywords")]])
    _send(chat_id, text, reply_markup=keyboard)


def start_remove_keyword_wizard(chat_id: str):
    keywords = list_keywords()
    if not keywords:
        _send(chat_id, "❌ No keywords to remove.", reply_markup=_inline_keyboard([[("🔙 Back", "back:keywords")]]))
        return

    _user_states[chat_id] = {"action": "remove_keyword_step1", "created_at": time.time()}
    text = (
        "➖ <b>Remove Keyword</b>\n\n"
        "Send the exact <b>keyword</b> you want to remove:\n\n"
    )
    kw_list = "\n".join([f"• <code>{_escape(k['keyword'])}</code>" for k in keywords[:10]])
    text += kw_list
    if len(keywords) > 10:
        text += f"\n<i>...and {len(keywords) - 10} more</i>"
    text += "\n\nSend /cancel to abort."

    keyboard = _inline_keyboard([[("❌ Cancel", "back:keywords")]])
    _send(chat_id, text, reply_markup=keyboard)


# ==================== LEGACY FUNCTIONS (kept for backward compatibility) ====================


def _welcome() -> str:
    return "🦚 <b>KrishnaVerse AI Admin</b>\n\nUse /menu to open the control panel."


def _help_text() -> str:
    return (
        "🦚 <b>Commands</b>\n\n"
        "/status - bot and database status\n"
        "/stats - today's stats\n"
        "/pause - pause bot\n"
        "/resume - resume bot\n"
        "/ping - health check\n"
        "/ai - AI status\n"
        "/models - model status\n"
        "/quota - quota summary\n"
        "/keywords - list keywords\n"
        "/addkeyword word reply - add keyword reply\n"
        "/removekeyword word - remove keyword\n"
        "/clearcache - clear AI cache\n"
        "/gemini_on /gemini_off - toggle Gemini\n"
        "/safe_on /safe_off - toggle safe mode\n"
        "/tokenrefresh - refresh Instagram tokens"
    )


def _token_refresh(chat_id: str) -> None:
    """Manually trigger a token refresh and report the result."""
    from instagram_api import refresh_instagram_token, check_token_validity, _TOKEN_EXPIRY_KEY
    from database import get_state, set_state
    import time as _time

    _send(chat_id, "🔄 Refreshing Graph token...")
    result = refresh_instagram_token()

    if result:
        new_token = result.get("access_token", "")
        expires_in = result.get("expires_in", 0)
        # Persist expiry for auto-refresh
        if new_token:
            set_state(_TOKEN_EXPIRY_KEY, str(_time.time() + expires_in))
        days_left = round(expires_in / 86400, 1) if expires_in else 0
        _send(chat_id, (
            "✅ <b>Token Refreshed Successfully</b>\n\n"
            f"Expires in: <b>{days_left} days</b> ({expires_in}s)\n"
            f"Token changed: {'<b>Yes</b>' if new_token else 'No (same token returned)'}\n\n"
            "Auto-refresh is now active."
        ))
    else:
        # Still report current token status
        graph_ok = check_token_validity("graph")
        dm_ok = check_token_validity("dm")
        _send(chat_id, (
            "❌ <b>Token Refresh Failed</b>\n\n"
            f"Graph token: {'✅ Valid' if graph_ok else '❌ Invalid'}\n"
            f"DM token: {'✅ Valid' if dm_ok else '❌ Invalid'}\n\n"
            "Check logs for details. The token may need manual re-authentication."
        ))


def _status_text() -> str:
    stats = get_stats()
    ai = ai_get_status()
    return (
        "🦚 <b>Bot Status</b>\n\n"
        f"Paused: <b>{stats['bot_paused']}</b>\n"
        f"Gemini Enabled: <b>{stats['gemini_enabled']}</b>\n"
        f"Safe Mode: <b>{stats['safe_mode']}</b>\n"
        f"Comments Replied: <b>{stats['comments_replied']}</b>\n"
        f"Welcome DMs Sent: <b>{stats['welcome_dms_sent']}</b>\n"
        f"AI Calls 24h: <b>{stats['ai_calls_24h']}</b>\n"
        f"Cache Size: <b>{ai.get('cache', {}).get('size', 0)}</b>\n"
        f"Gemini Keys: <b>{len(ai.get('gemini', {}).get('keys', []))}</b>\n"
        f"Groq Enabled: <b>{ai['groq_enabled']}</b>"
    )


def _stats_text() -> str:
    stats = get_stats()
    return (
        "📊 <b>Today's Stats</b>\n\n"
        f"Comments Replied: <b>{stats['comments_replied']}</b>\n"
        f"Welcome DMs Sent: <b>{stats['welcome_dms_sent']}</b>\n"
        f"Processed Events: <b>{stats['processed_events']}</b>\n"
        f"AI Calls 24h: <b>{stats['ai_calls_24h']}</b>\n"
        f"Keywords: <b>{stats['keywords']}</b>"
    )


def _ai_text() -> str:
    ai = ai_get_status()
    lines = ["🤖 <b>AI Status</b>"]

    gemini_data = ai.get("gemini", {})
    keys = gemini_data.get("keys", [])

    for key in keys:
        lines.append(f"\n<b>Key: {_escape(key.get('key_id', 'unknown'))}</b>")
        lines.append(f"Project: <code>{_escape(key.get('project_id', 'N/A'))}</code>")
        lines.append(f"Requests Today: <b>{key.get('requests_today', 0)}</b>")
        lines.append(f"Quota Hits: <b>{key.get('quota_hits_today', 0)}</b>")
        lines.append(f"Enabled: {'✅' if key.get('enabled') else '❌'}")

        # FIX: Properly iterate model dicts instead of printing raw dict
        models = key.get("models", [])
        if models:
            model_lines = []
            for m in models:
                name = m.get("name", "unknown")
                usage = m.get("usage_today", 0)
                sr = m.get("success_rate", 0.0)
                circuit = "🔴 OPEN" if m.get("circuit_open") else "🟢 OK"
                model_lines.append(f"  • {name}: {usage} req | {sr:.0f}% | {circuit}")
            if model_lines:
                lines.append("Models:")
                lines.extend(model_lines)

    lines.append(f"\n<b>Groq Fallback:</b> {'✅ Enabled' if ai.get('groq_enabled') else '❌ Disabled'}")
    lines.append(f"<b>Cache:</b> {ai.get('cache', {}).get('size', 0)} entries")
    lines.append(f"<b>Total Daily Capacity:</b> {gemini_data.get('total_daily_capacity', 0)}")
    return "\n".join(lines)


def _keywords_text() -> str:
    rows = list_keywords()
    if not rows:
        return "No keywords configured."
    lines = ["🔑 <b>Keywords</b>"]
    for row in rows:
        lines.append(f"• <code>{_escape(row['keyword'])}</code> → {_escape(row['reply'][:80])}")
    return "\n".join(lines)


def _activity_text() -> str:
    rows = get_recent_activity(10)
    if not rows:
        return "No recent activity."
    lines = ["📝 <b>Recent Activity</b>"]
    for row in rows:
        lines.append(f"• {_escape(row['action'])} @ {_escape(str(row['created_at']))}")
    return "\n".join(lines)