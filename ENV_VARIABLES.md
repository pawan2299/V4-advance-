# Environment Variables Configuration

## Required Variables (8 total)

### Meta/Instagram - Required (3)
```
VERIFY_TOKEN=your_verify_token_here
APP_SECRET=your_app_secret_here
PAGE_ID=your_facebook_page_id
```

### Meta/Instagram - Tokens (2)
```
GRAPH_ACCESS_TOKEN=EAA...  # For comments, media, webhook subscriptions
INSTAGRAM_LOGIN_ACCESS_TOKEN=IGQ...  # For DM operations
```

### Database (1)
```
DATABASE_URL=postgresql://user:password@host:port/database?sslmode=require
```

### Telegram - Required (2)
```
TELEGRAM_BOT_TOKEN=bot_token_here
TELEGRAM_CHAT_ID=admin_chat_id
```

---

## Optional Variables (15 total)

### AI Services (3)
```
GEMINI_API_KEYS=key1,key2,key3  # Comma-separated, multi-project for quota multiplication
GROQ_API_KEY=groq_api_key_here  # Fallback AI provider
GROQ_MODEL=llama-3.3-70b-versatile  # Groq model name
```

### Telegram - Optional (2)
```
TELEGRAM_ADMIN_CHAT_IDS=id1,id2  # Additional admin IDs (main chat_id is always admin)
TELEGRAM_WEBHOOK_SECRET=your_secret  # Auto-generated if not provided
```

### Meta/Instagram - Optional (1)
```
OWN_ACCOUNT_ID=your_account_id  # To filter out your own comments
```

### URLs (1)
```
PUBLIC_BASE_URL=https://your-domain.com  # Falls back to RENDER_EXTERNAL_URL
```

### Performance Tuning (6)
```
DB_POOL_MIN=2              # Min DB connections (default: 2)
DB_POOL_MAX=10             # Max DB connections (default: 10)
AI_CACHE_TTL_SECONDS=1800  # Cache TTL in seconds (default: 1800 = 30 min)
AI_CACHE_MAXSIZE=2000      # Max cache entries (default: 2000)
MAX_IMAGE_BYTES=4194304     # Max image download size in bytes (default: 4 MB)
DB_CLEANUP_DAYS=7          # Days to keep processed records (default: 7)
```

### Reliability (2)
```
MAINTENANCE_INTERVAL_SECONDS=300  # Background task interval (default: 300 = 5 min)
WEBHOOK_RATE_LIMIT=120            # Max webhook requests per IP/min (default: 120)
TELEGRAM_RATE_LIMIT=60            # Max Telegram webhook requests per IP/min (default: 60)
```

### Application (2)
```
APP_ENV=production  # Environment name
LOG_LEVEL=INFO      # Logging level (DEBUG, INFO, WARNING, ERROR)
```

---

## Removed Variables (No longer needed)

- `META_APP_ID` — Never used
- `PAGE_ACCESS_TOKEN` — Redundant with GRAPH_ACCESS_TOKEN
- `ACCESS_TOKEN` — Unnecessary fallback
- `GEMINI_API_KEY_1`, `GEMINI_API_KEY_2`, `GEMINI_API_KEY_3` — Use GEMINI_API_KEYS CSV
- `GEMINI_API_KEY` — Single key fallback removed
- `PORT` — Auto-injected by Render
- `GEMINI_RPM_LIMIT` — Hardcoded in gemini_client.py
- `GEMINI_SOFT_DAILY_FRACTION` — Not used

---

## Token Usage Summary

### GRAPH_ACCESS_TOKEN (EAA...)
Used for:
- Replying to Instagram comments
- Fetching media URLs
- Token validity checks
- Setting up webhook subscriptions

**Required permissions:**
- `pages_manage_metadata`
- `pages_show_list`
- `instagram_basic`
- `instagram_manage_messages`

### INSTAGRAM_LOGIN_ACCESS_TOKEN (IGQ...)
Used for:
- Sending welcome DMs to new followers
- Token validity checks for DM functionality

**Required permissions:**
- `instagram_basic`
- `instagram_manage_messages`

---

## Security Notes

- `APP_SECRET` is now **mandatory** — the bot will reject all webhook calls without it
- `TELEGRAM_WEBHOOK_SECRET` is **mandatory** — the bot will reject all Telegram webhook calls without it
- If either secret is missing, the bot logs a warning and returns 403
- Token formats are validated on startup (EAA..., IGQ..., bot tokens) with warnings on mismatches