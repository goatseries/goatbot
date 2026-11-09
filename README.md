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

## COGS
Per line item: cogs.csv (sku,cogs) overrides first, else Shopify inventory item unit cost (needs read_inventory), else counted as $0 and reported as missing ("cost missing on X of Y line items"). /costs sends a cogs.csv template of every variant sold in the last COSTS_DAYS days (default 90; Shopify only returns orders older than 60 days with the read_all_orders scope). Leave the cogs column blank to use Shopify's cost.

## Shipping cost (limit)
Shopify's Admin API does not expose the Shopify Shipping label cost per order (checked Nov 2026). Balance-transaction label types exist only for Managed Markets, and label charges are not linked to orders. So shipping uses DEFAULT_SHIPPING_COST (flat per order) until labels move to an app with an API (ShipStation, Shippo, EasyPost). Profit stays "provisional" while it is 0.

## costs.json (v4)
COGS per unit, packaging, and label cost by number of books live in costs.json (repo root, edit and redeploy). Order cost = unit costs + packaging box + label; label uses the book-count table, with linear interpolation for counts not listed and a per-book extrapolation above the top tier (both flagged as estimates). /costs sends cost-mapping.csv listing every variant sold recently with mapped yes/no, cost used and Shopify cost status. Every assumption (estimated tiers, assumed packaging rule, non-book shipping, bundle costs, unmapped items) adds a note and keeps profit "provisional". Legacy cogs.csv and Shopify cost per item are still fallbacks for unmapped items.

Packaging (v6): rules in costs.json packaging_rules (first match wins). Envelope for 1-2 book orders, 4-book bundles and gift sets get a gift box inside an outer bundle box (both costs added), bundle box alone for 3 and 5+ books (assumed, flagged).

/unmapped sends titles/SKUs/types/tags that match no costs.json item (no figures). Matching is case/punctuation-insensitive across product title, variant title, line name, product type and tags; unmapped_default treats leftover non-excluded products as 1 book at 1.35 (flagged).

## Wholesale
The bot is for the online store. Wholesale (SKU prefix 8600143252, 'wholesale' in a line title, or order tag/source 'wholesale') is excluded from revenue, costs, profit, order counts, the sale feed, /costs and /unmapped. Mixed orders: costs.json wholesale.mixed_order_rule (exclude_order default). Scoreboard shows how many wholesale orders were excluded.
