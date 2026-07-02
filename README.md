# KrishnaVerse AI

Production-ready Instagram automation bot with AI-powered comment replies, welcome DMs, multi-key Gemini routing, Groq fallback, and a full-featured Telegram admin panel.

## Features

- **Instagram comment replies** — AI-generated or keyword-matched public replies
- **Welcome DMs** — One-time automated DM to new followers/commenters
- **Multi-key Gemini routing** — Up to 6,000+ RPD with multi-project key rotation
- **Groq fallback** — Automatic fallback when Gemini is unavailable
- **Circuit breaker pattern** — Self-healing model isolation on failures
- **Telegram admin panel** — Full interactive dashboard with inline keyboards
- **Background maintenance** — Automatic DB cleanup, pool health, cooldown compaction
- **Rate limiting** — Per-endpoint IP rate limiting and global API rate limiting
- **Health checks** — Deep `/health/deep` endpoint probing all dependencies
- **Structured metrics** — `/metrics` endpoint with request counters and gauges
- **Security** — HMAC webhook verification, prompt injection protection, secret redaction

## Architecture

```
Instagram (Meta Webhook)          Telegram (Webhook)
         │                                │
         ▼                                ▼
    POST /webhook              POST /telegram-webhook
         │                                │
         ▼                                ▼
    bot_logic.py              telegram_bot.py
         │                                │
         ▼                                ▼
    ai_router.py ───► gemini_client.py ──► Google Gemini API
         │          └──► groq_client.py ──► Groq API
         │
         ▼
    instagram_api.py ──► Meta Graph API / Instagram API
         │
         ▼
    database.py ──► PostgreSQL (Neon)
```

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your actual tokens
```

### 3. Setup webhook subscriptions

```bash
python setup_webhook_subscriptions.py --subscribe
```

### 4. Run

```bash
python main.py
```

## Deploy to Render

The `render.yaml` is pre-configured. Push to GitHub and connect to Render:

1. Create a new **Web Service** from your repo
2. Render auto-detects `render.yaml`
3. Set all required environment variables in the Render dashboard
4. Deploy

After deploying, run the subscription script with production tokens:
```bash
python setup_webhook_subscriptions.py --subscribe
```

## Required Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `VERIFY_TOKEN` | Yes | Meta webhook verification token |
| `APP_SECRET` | Yes | Meta app secret for HMAC signatures |
| `PAGE_ID` | Yes | Facebook Page ID |
| `GRAPH_ACCESS_TOKEN` | Yes | EAA... token (comments, media, subscriptions) |
| `INSTAGRAM_LOGIN_ACCESS_TOKEN` | Yes | IGQ... token (DMs) |
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `TELEGRAM_BOT_TOKEN` | Yes | Telegram bot token |
| `TELEGRAM_CHAT_ID` | Yes | Admin Telegram chat ID |
| `GEMINI_API_KEYS` | No | Comma-separated Gemini API keys |
| `GROQ_API_KEY` | No | Groq API key (fallback) |

See [ENV_VARIABLES.md](./ENV_VARIABLES.md) for the complete list.

## Telegram Admin Commands

| Command | Description |
|---------|-------------|
| `/menu` | Open the interactive control panel |
| `/dashboard` | View detailed bot dashboard |
| `/analytics` | AI usage analytics (24h) |
| `/keywords` | Manage keyword auto-replies |
| `/settings` | Toggle bot, Gemini, safe mode |
| `/health` | System health check |
| `/status` | Quick status overview |
| `/stats` | Today's statistics |
| `/pause` / `/resume` | Pause/resume the bot |
| `/clearcache` | Clear AI reply cache |
| `/ai` / `/models` | AI model status and quotas |

## Project Structure

```
├── main.py              # Flask app, routes, middleware
├── bot_logic.py          # Comment/follower event handling
├── ai_router.py          # AI reply generation + caching
├── gemini_client.py      # Gemini API with multi-key rotation
├── groq_client.py        # Groq API fallback
├── instagram_api.py      # Meta/Instagram Graph API
├── telegram_bot.py       # Telegram bot + admin panel
├── database.py           # PostgreSQL pool, queries, maintenance
├── config.py             # Environment variable loading
├── security.py           # HMAC verification
├── cache.py              # In-memory TTL+LRU cache
├── prompts.py            # AI prompt templates
├── middleware.py          # Request ID, timing, rate limiting
├── error_handlers.py     # Centralized error handling
├── health.py             # Deep health checks
├── utils.py              # Shared utilities (retry, sanitize, metrics)
├── tasks.py              # Background maintenance tasks
├── templates/index.html  # Web status page
├── render.yaml           # Render deployment config
├── requirements.txt      # Python dependencies
├── .env.example          # Environment variable template
├── .gitignore            # Git ignore rules
├── setup_telegram_webhook.py       # Manual Telegram webhook setup
├── setup_webhook_subscriptions.py  # Meta webhook subscription setup
├── tests/                # Unit tests
│   ├── test_bot_logic.py
│   ├── test_cache.py
│   ├── test_security.py
│   └── test_utils.py
├── README.md
├── CHANGELOG.md
├── ENV_VARIABLES.md
├── TROUBLESHOOTING.md
└── AI_IMPROVEMENTS.md
```

## Running Tests

```bash
python -m pytest tests/ -v
```

## License

Private project. All rights reserved.