# Troubleshooting

## Auto-DM Not Working

### Root Cause
The auto-DM feature requires **webhook subscriptions** to be configured in your Meta app. Without these, Meta won't send your bot notifications when someone comments or follows.

### Solution

#### Step 1: Verify Token Permissions
Ensure `GRAPH_ACCESS_TOKEN` has these permissions:
- `pages_manage_metadata`
- `pages_show_list`
- `instagram_basic`
- `instagram_manage_messages`

#### Step 2: Run the Subscription Script
```bash
python setup_webhook_subscriptions.py --subscribe
```

#### Step 3: Verify Subscriptions
```bash
python setup_webhook_subscriptions.py --list
```

#### Step 4: Test
1. Have someone comment on your Instagram post
2. Check Render logs for webhook events
3. Verify the user receives both a comment reply and a welcome DM

---

## Security: Webhook Calls Rejected (403)

### "APP_SECRET missing — rejecting webhook"
**Fix:** Set the `APP_SECRET` environment variable in your Render dashboard. This is now mandatory for security.

### "TELEGRAM_WEBHOOK_SECRET not set — rejecting Telegram webhook"
**Fix:** Set `TELEGRAM_WEBHOOK_SECRET` in your Render dashboard, or let it auto-generate (but set it explicitly so it persists across restarts).

### "Invalid webhook signature"
**Fix:** Ensure `APP_SECRET` in your environment matches the App Secret in your Meta Developer Console exactly.

---

## Token Issues

### "Error validating access token: Session has expired"
Tokens last ~60 days. Generate a new long-lived token in Meta Developer Console.

### Token format validation warnings on startup
The bot now validates token formats:
- `GRAPH_ACCESS_TOKEN` should start with `EAA`
- `INSTAGRAM_LOGIN_ACCESS_TOKEN` should start with `IGQ`
- `TELEGRAM_BOT_TOKEN` should match `digits:alphanumeric`

These are warnings, not errors — but you should verify if you see them.

---

## Database Issues

### "Stale connection detected, recreating pool"
This is normal — the bot auto-recovers. If it happens frequently:
- Check Neon free tier limits (may pause during inactivity)
- Increase `DB_POOL_MIN` to keep connections warm

### Tables growing too large
The bot auto-cleans `processed_comments` and `processed_events` older than 7 days (configurable via `DB_CLEANUP_DAYS`). If you need to manually clean:
```sql
DELETE FROM processed_comments WHERE created_at < NOW() - INTERVAL '30 days';
DELETE FROM processed_events WHERE created_at < NOW() - INTERVAL '30 days';
```

---

## AI / Gemini Issues

### "All keys on cooldown"
- Check `/ai` in Telegram to see per-key status
- Cooldowns auto-expire (23h for quota hits, 15min for errors)
- Use `/clearcache` if responses are stale
- Check that your API keys are from different Google Cloud Projects for max quota

### Rate limit errors (429)
- The bot handles this with circuit breakers and auto-retry
- If persistent, reduce traffic or add more API keys
- Check `/analytics` to see if you're hitting daily limits

### Wrong model names
As of June 2026, the free tier supports:
- `gemini-3.5-flash` (primary)
- `gemini-3.1-flash-lite`
- `gemini-3-flash-preview`
- `gemini-2.5-flash`
- `gemini-1.5-flash` (emergency fallback)

---

## Telegram Bot Issues

### Bot not responding
1. Verify `TELEGRAM_BOT_TOKEN` is correct
2. Check `/telegram-webhook-info` endpoint
3. Ensure `PUBLIC_BASE_URL` is accessible
4. Verify `TELEGRAM_WEBHOOK_SECRET` matches between your env and the registered webhook

### Commands not working
1. Ensure your `TELEGRAM_CHAT_ID` is in `TELEGRAM_ADMIN_CHAT_IDS`
2. Check Render logs for "Unauthorized Telegram chat" warnings

---

## Performance Issues

### Slow responses
- Check `/health/deep` for dependency latency
- Verify DB is responsive (Neon free tier can have cold starts)
- Check cache hit rate via `/metrics`

### Memory usage
- Cache is capped at `AI_CACHE_MAXSIZE` (default 2000) with LRU eviction
- Background tasks auto-clean stale data every 5 minutes
- Check the memory health check in `/health/deep`