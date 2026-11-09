# Store metrics Telegram bot
Python worker. Polls Shopify for new orders, posts sale messages, posts scoreboard at SCOREBOARD_TIMES.
Runs with DRY_RUN=1 (prints instead of sending) until you set DRY_RUN=0.
Deploy: Railway service from this repo, set variables from .env.example, add a volume for state.json (STATE_PATH).
Known gaps (v1): upsell detection, Klaviyo flow naming, ShipStation shipping, real Shopify fees (uses 2.9%+$0.30 estimate), new vs returning split.
Commands in the configured chat: /t today, /y yesterday, /w week (Mon start), /m month.
