# Changelog

## v3.1.0 — Engineering Hardening (July 2026)

### Critical Security Fixes
- **APP_SECRET bypass**: `security.verify_signature()` now REJECTS all requests when APP_SECRET is missing (previously allowed all — critical vulnerability)
- **Telegram webhook secret bypass**: `verify_telegram_secret()` now REJECTS when no secret is configured (previously allowed all)
- **Prompt injection**: Moved sanitization to shared `utils.sanitize_text()` with proper multilingual support (Hindi, Chinese, Japanese)
- **.env exposure**: Removed `.env` from repository, added proper `.env.example`, fixed `.gitignore` (was wrapped in code block markers)

### Bug Fixes
- **Duplicate classification**: Removed dead `_classify()` in `ai_router.py` — now uses `classify_comment()` as single source of truth in `bot_logic._classify()`
- **Spam filter bypass**: Spam detection (`_looks_suspicious`) now ALWAYS runs regardless of Gemini/safe-mode state
- **/ai command model display**: Fixed `_ai_text()` in `telegram_bot.py` — was printing raw dict objects instead of formatted model stats
- **show_logs placeholder**: `/logs` now fetches and displays recent activity from database instead of showing a dead-end message
- **_user_states memory leak**: Added TTL-based eviction (10 min) and `/cancel` command support to clean up conversation wizard state
- **Analytics calculation**: Fixed incorrect latency aggregation in `show_analytics()` (was multiplying latency by count twice)

### New Features
- **`middleware.py`**: Request ID tracking, response timing, per-endpoint IP rate limiting, CORS headers
- **`error_handlers.py`**: Centralized structured error responses with AppError hierarchy, request ID correlation
- **`health.py`**: Deep health check system probing database, Gemini, Groq, Telegram, Instagram, and memory
- **`/health/deep` endpoint**: Returns structured health report for monitoring/alerting
- **`/metrics` endpoint**: In-process counter/gauge metrics
- **`utils.py`**: Shared `@retry` decorator with exponential backoff + jitter, `sanitize_text()`, `truncate()`, `redact_secret()`, `SimpleMetrics`
- **`tasks.py`**: Background maintenance loop — DB record cleanup (7-day retention), stale connection pool eviction, daily RPD counter reset, expired cooldown compaction
- **`database.py`**: `cleanup_old_records()`, `get_pool_status()`, new indexes (`ai_usage_provider_model`, `bot_state_key_prefix`, `custom_keywords_keyword`)
- **Connection pooling**: All `requests.Session` objects now use `HTTPAdapter` with proper pool configuration
- **Image download safety**: `MAX_IMAGE_BYTES` cap (4MB default), Content-Length check before download, separate `_image_session` with its own pool
- **Token format validation**: `config.py` warns on malformed tokens (EAA..., IGQ..., bot tokens)
- **Unit tests**: 4 test files covering bot_logic, security, cache, and utils (40+ test cases)

### Reliability Improvements
- Database `get_cursor()` now guarantees `putconn()` even on exception
- Background maintenance tasks run every 5 minutes automatically
- Stale DB connection detection during maintenance sweep
- Expired bot_state cooldown entries auto-compacted

### Performance Improvements
- LRU eviction policy in `TTLCache` (was FIFO)
- `has()` method on cache for existence checks without updating access time
- Connection pooling on all `requests.Session` objects (Instagram API, Groq, Telegram, image downloads)
- Metrics counters avoid string formatting in hot paths

### Configuration
- New env vars: `MAX_IMAGE_BYTES`, `DB_CLEANUP_DAYS`, `MAINTENANCE_INTERVAL_SECONDS`, `WEBHOOK_RATE_LIMIT`, `TELEGRAM_RATE_LIMIT`
- New env vars in `render.yaml`: all performance tuning vars with defaults
- `.env.example` created with all 20+ variables documented

### Documentation
- Complete `README.md` rewrite with architecture diagram, feature list, command reference
- Updated `ENV_VARIABLES.md` with all new variables
- Updated `TROUBLESHOOTING.md` with new sections
- Updated `AI_IMPROVEMENTS.md` with latest changes

---

## v3.0.0 — Production Hardening Pass

### Critical Security Fix
- **Forged Telegram admin commands**: `/telegram-webhook` only checked `chat_id` inside the JSON body. Fixed by verifying Telegram's `X-Telegram-Bot-Api-Secret-Token` header.

### Real Bugs Fixed
- `/ai` and `/models` always reported 0 requests today — `get_key_requests_today()` now reads from `model_usage` table
- Spam filtering was wrongly gated behind `is_gemini_enabled() and not is_safe_mode()` — now always runs

### Hardening
- `hmac.compare_digest` for timing-attack safety
- `MAX_CONTENT_LENGTH` capped at 2 MB
- Configurable DB pool size
- Graceful shutdown (`SIGTERM`/`atexit`)
- Database ping in health check
- Keyword input length capping (64/500 chars)

### Files Restored
- `.env.example` and `render.yaml` added