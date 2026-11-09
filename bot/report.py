from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from . import config as c, shopify, meta, costs
def _utm(o):
    v = ((o.get("customerJourneySummary") or {}).get("lastVisit") or {}).get("utmParameters") or {}
    return v
def sale_message(o, cogs):
    k = costs.order_costs(o, cogs); u = _utm(o)
    first = (o.get("customer") or {}).get("numberOfOrders") in (None, "0", "1", 1, 0)
    lines = [f"💰 NEW SALE — ${k['revenue']:.2f}", "",
        "🛒 " + ", ".join(f"{l['title']} x{l['quantity']}" for l in o["lineItems"]["nodes"])]
    if u.get("source"): lines.append(f"📣 {u['source']}" + (f"  🎨 {u['content']}" if u.get("content") else ""))
    if u.get("campaign"): lines.append(f"📱 {u['campaign']}")
    if k["missing_cogs"]: lines.append("⚠️ missing COGS: " + ", ".join(k["missing_cogs"]))
    return "\n".join(lines)
def _range(period):
    tz = ZoneInfo(c.TZ); now = datetime.now(tz)
    d0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "y":
        return d0 - timedelta(days=1), d0 - timedelta(seconds=1), "Yesterday", now
    if period == "w":
        return d0 - timedelta(days=d0.weekday()), None, "This Week", now
    if period == "m":
        return d0.replace(day=1), None, "This Month", now
    return d0, None, "Today", now
def scoreboard(period="t"):
    start, end, label, now = _range(period)
    utc = ZoneInfo("UTC"); f = "%Y-%m-%dT%H:%M:%SZ"
    os_ = shopify.orders(start.astimezone(utc).strftime(f), end.astimezone(utc).strftime(f) if end else None)
    cogs = costs.load_cogs()
    t = dict(revenue=0, fees=0, cogs=0, shipping=0, returns=0)
    n_ok = n_miss = 0
    for o in os_:
        if o["cancelledAt"]: continue
        k = costs.order_costs(o, cogs)
        for x in t: t[x] += k[x]
        if k["missing_cogs"]: n_miss += 1
        else: n_ok += 1
    ads = meta.spend(start.strftime("%Y-%m-%d"), (end or now).strftime("%Y-%m-%d"))
    costs_total = t["fees"] + t["cogs"] + t["shipping"] + t["returns"]
    profit = t["revenue"] - ads - costs_total
    roi = profit / (ads + costs_total) * 100 if (ads + costs_total) else 0
    roas = t["revenue"] / ads if ads else 0
    prov = n_miss > 0 or c.SHIP_DEFAULT == 0
    notes = []
    if n_miss: notes.append(f"COGS missing on {n_miss} of {n_ok + n_miss} orders (counted as $0)")
    if c.SHIP_DEFAULT == 0: notes.append("shipping not included yet")
    note = ("\n⚠️ Provisional: " + "; ".join(notes)) if prov else ""
    tag = " (provisional)" if prov else ""
    return (f"📊 {label} Scoreboard\nAs of {now.strftime('%b %-d, %-I:%M %p')} ET\n\n"
        f"💰 Profit{tag} ${profit:,.0f} ({roi:.1f}% ROI)\n💵 Revenue ${t['revenue']:,.0f} ({roas:.2f}x)\n"
        f"💸 Ads ${ads:,.0f} · Costs ${costs_total:,.0f}\n💳 Orders {len(os_)}{note}")
def creative_report(days=7):
    tz = ZoneInfo(c.TZ); end = datetime.now(tz); start = end - timedelta(days=days - 1)
    os_ = shopify.orders((start.replace(hour=0, minute=0, second=0).astimezone(ZoneInfo("UTC"))).strftime("%Y-%m-%dT%H:%M:%SZ"))
    sp = meta.spend_by_ad(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    sales = {}
    for o in os_:
        u = _utm(o); key = u.get("content") or "unknown"
        s = sales.setdefault(key, [0, 0.0]); s[0] += 1; s[1] += float(o["totalPriceSet"]["shopMoney"]["amount"])
    rows = sorted(sales.items(), key=lambda kv: -kv[1][0])
    lines = [f"📣 Ad sales · Last {days} days"]
    for k, (n, rev) in rows:
        s = sp.get(k); lines.append(f"{k} — {n} · ROAS " + (f"{rev / s:.2f}x" if s else "—"))
    return "\n".join(lines)
