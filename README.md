# Store metrics Telegram bot
Python worker. Polls Shopify for new orders, posts sale messages, posts scoreboard at SCOREBOARD_TIMES.
Runs with DRY_RUN=1 (prints instead of sending) until you set DRY_RUN=0.
Deploy: Railway service from this repo, set variables from .env.example, add a volume for state.json (STATE_PATH).
Known gaps (v1): upsell detection, Klaviyo flow naming, ShipStation shipping, real Shopify fees (uses 2.9%+$0.30 estimate), new vs returning split.
Commands in the configured chat: /t today, /y yesterday, /w week (Mon start), /m month.

## Known limits (v1)
- The sale feed only sees NEW orders by created_at and remembers seen IDs. Upsells added after checkout, order edits and refunds do not create feed events. Upsell definition is still owed by Ben.
- Scoreboard profit is labeled "provisional" while any order lacks COGS (counted as $0) or DEFAULT_SHIPPING_COST is 0 (shipping not included). Coverage is shown in the message.
- Shopify fees are an estimate (SHOPIFY_FEE_PCT + SHOPIFY_FEE_FIXED).
- Logs redact tokens; do not log raw request URLs.

## Shopify auth
Dev Dashboard apps: set SHOPIFY_CLIENT_ID and SHOPIFY_CLIENT_SECRET. The bot gets a short-lived token via the client credentials grant (POST /admin/oauth/access_token), caches it, refreshes before expiry and on 401. Scopes: read_orders, read_products, read_inventory. The app must be installed on the store. SHOPIFY_ADMIN_TOKEN is an optional static-token fallback.
