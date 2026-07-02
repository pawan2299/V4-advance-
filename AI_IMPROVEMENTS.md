# AI Implementation Improvements

## v3.1.0 Updates (July 2026)

### New: Background Maintenance System (`tasks.py`)
- Automatic DB record cleanup (configurable retention, default 7 days)
- Stale DB connection pool detection and recreation
- Daily RPD counter memory cleanup
- Expired cooldown compaction in bot_state table
- Configurable interval via `MAINTENANCE_INTERVAL_SECONDS`

### New: Deep Health Checks (`health.py`)
- Database connectivity probe with latency measurement
- Gemini API key status (active/cooldown counts)
- Groq API availability check
- Telegram bot webhook validation
- Instagram Graph token validity
- Process memory usage monitoring (RSS)
- Overall status: ok / degraded / down
- Accessible at `/health/deep`

### New: Middleware Stack (`middleware.py`)
- Request ID tracking (X-Request-ID header + `g.request_id`)
- Response timing with slow-request warning (>5s)
- Per-endpoint IP-based rate limiting:
  - `/webhook`: 120 req/min per IP
  - `/telegram-webhook`: 60 req/min per IP
  - `/stats`, `/metrics`: 30 req/min per IP
- CORS headers for future web dashboard

### New: Centralized Error Handling (`error_handlers.py`)
- `AppError` hierarchy (ValidationError, RateLimitError, AuthenticationError)
- Structured JSON error responses with request_id and timestamp
- Automatic logging of all errors with context

### New: Shared Utilities (`utils.py`)
- `@retry` decorator with exponential backoff + jitter
- `sanitize_text()` — prompt injection prevention with multilingual support
- `truncate()` — safe text truncation
- `redact_secret()` — token masking for logs
- `SimpleMetrics` — in-process counter/gauge metrics

### Security Improvements
- **APP_SECRET bypass fixed**: `verify_signature()` now REJECTS when APP_SECRET is missing
- **Telegram secret bypass fixed**: `verify_telegram_secret()` now REJECTS when secret is empty
- **Token format validation**: `config.py` validates EAA..., IGQ..., and bot token formats on startup
- **Image download safety**: Content-Length check + body size cap via `MAX_IMAGE_BYTES`
- **.env removed from repo**: Created `.env.example` as template

### Bug Fixes
- Fixed duplicate classification logic between `ai_router.classify_comment()` and `bot_logic._classify()`
- Fixed spam filter bypass when Gemini/safe-mode was toggled off
- Fixed `/ai` command printing raw dict objects instead of formatted model stats
- Fixed `/logs` showing dead-end placeholder (now shows activity from database)
- Fixed `_user_states` memory leak with TTL eviction and `/cancel` command
- Fixed analytics latency aggregation (was multiplying by count twice)

### Performance Improvements
- LRU eviction in `TTLCache` (was FIFO, could evict hot entries)
- HTTP connection pooling on all `requests.Session` objects:
  - Instagram API: pool_maxsize=8
  - Telegram API: pool_maxsize=8
  - Groq API: pool_maxsize=4
  - Image downloads: pool_maxsize=4
- Metrics counters for cache hits/misses, spam filtered, rate limit drops

### Database Improvements
- `get_cursor()` now guarantees connection return to pool even on exception
- New indexes: `ai_usage_provider_model`, `bot_state_key_prefix`, `custom_keywords_keyword`
- `cleanup_old_records()` for unbounded table growth prevention
- `get_pool_status()` for monitoring

---

## v3.0.0 Features (June 2026)

### Multi-Layer Rate Limiting
- RPM (Requests Per Minute): 12 safe limit per model
- TPM (Tokens Per Minute): 1M tokens/minute tracking
- RPD (Requests Per Day): 1,500 daily limit per project

### Multi-Project Quota Pooling
- Support for multiple API keys from different projects
- 4 projects = 6,000 RPD total capacity

### Circuit Breaker Pattern
- 5 failures in 5 minutes opens the circuit
- Per-model isolation
- Automatic recovery after timeout

### Advanced Retry Logic
- 3 attempts with exponential backoff + jitter
- Smart retry: only transient errors (500-504)
- Fast fail on 429 rate limits

### Model Fallback Chain
```
gemini-3.5-flash (primary)
gemini-3.1-flash-lite (secondary)
gemini-3-flash-preview (tertiary)
gemini-2.5-flash (legacy)
gemini-1.5-flash (emergency)
```