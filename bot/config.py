import os
def env(k, d=None, required=False):
    v = os.environ.get(k, d)
    if required and not v:
        raise SystemExit(f"Missing env var {k}")
    return v
TG_TOKEN = lambda: env("TELEGRAM_BOT_TOKEN", required=True)
TG_CHAT = lambda: env("TELEGRAM_CHAT_ID", required=True)
def TG_CHATS():
    """Chat ids allowed to send commands. TELEGRAM_CHAT_IDS (comma-separated) overrides;
    defaults to TELEGRAM_CHAT_ID alone. TELEGRAM_CHAT_ID stays the home chat that receives
    scheduled scoreboards, sale alerts and the weekly recap."""
    raw = env("TELEGRAM_CHAT_IDS") or env("TELEGRAM_CHAT_ID", required=True)
    return [x.strip().strip('"').strip("'") for x in raw.split(",") if x.strip()]
SHOP = lambda: env("SHOPIFY_SHOP_DOMAIN", required=True)
SHOP_TOKEN = lambda: env("SHOPIFY_ADMIN_TOKEN")  # optional fallback (static shpat_ token)
SHOP_CLIENT_ID = lambda: env("SHOPIFY_CLIENT_ID")
SHOP_CLIENT_SECRET = lambda: env("SHOPIFY_CLIENT_SECRET")
API_VER = env("SHOPIFY_API_VERSION", "2025-10")
META_TOKEN = lambda: env("META_ACCESS_TOKEN", required=True)
META_ACCT = lambda: env("META_AD_ACCOUNT_ID", required=True)
KLAVIYO = lambda: env("KLAVIYO_API_KEY")
TZ = env("REPORT_TZ", "America/New_York")
TIMES = [t.strip() for t in env("SCOREBOARD_TIMES", "09:00,18:00").split(",")]
POLL = int(env("POLL_SECONDS", "15"))
COGS_CSV = env("COGS_CSV_PATH", "cogs.csv")
FEE_PCT = float(env("SHOPIFY_FEE_PCT", "2.9"))
FEE_FIXED = float(env("SHOPIFY_FEE_FIXED", "0.30"))
SHIP_DEFAULT = float(env("DEFAULT_SHIPPING_COST", "0"))
DRY_RUN = env("DRY_RUN", "1") == "1"
COSTS_DAYS = int(env("COSTS_DAYS", "90"))
WEEKLY_DAY = env("WEEKLY_RECAP_DAY", "MON").upper()[:3]
WEEKLY_TIME = env("WEEKLY_RECAP_TIME", "08:00")
