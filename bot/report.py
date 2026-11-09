from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from . import config as c, shopify, meta, costs, klaviyo
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
def _titles(d, cap=10):
    ks = sorted(d, key=lambda k: -d[k]); out = "; ".join(ks[:cap])
    return out + (f" (+{len(ks) - cap} more)" if len(ks) > cap else "")
def unmapped_report(days=None):
    """Titles/SKUs matching no costs.json item (even if the book default covers them). No figures."""
    days = days or c.COSTS_DAYS
    since = (datetime.now(ZoneInfo("UTC")) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cfg = costs.load_config(); seen = {}
    for o in costs.split_wholesale(shopify.orders(since))[0]:
        for li in o["lineItems"]["nodes"]:
            if costs.classify(li, cfg): continue
            sku, texts, ptype, tags = costs.fields(li); p = li.get("product") or {}
            _, is_def = costs.resolve(li, cfg)
            key = (costs.display(li), sku)
            seen[key] = f"- {costs.display(li)} | sku: {sku or '-'} | type: {p.get('productType') or '-'} | tags: {', '.join(p.get('tags') or []) or '-'} | {'default book' if is_def else 'NO MATCH'}"
    if not seen: return f"All products from the last {days} days match costs.json."
    lines = sorted(seen.values())
    txt = f"Not matched in costs.json ({len(lines)}), last {days} days:\n" + "\n".join(lines)
    return txt[:3900]
def _subs_line(period, start, end, now):
    """New Klaviyo profiles. Today/yesterday compare with the previous day. Empty string on any failure."""
    try:
        utc = ZoneInfo("UTC"); day = timedelta(days=1)
        cur = klaviyo.new_profiles(start.astimezone(utc), (end + timedelta(seconds=1) if end else now).astimezone(utc))
        if cur is None: return ""
        if period in ("t", "y"):
            prev = klaviyo.new_profiles((start - day).astimezone(utc), start.astimezone(utc))
            if prev is not None:
                return f"\n📧 New subscribers {cur} (prev day {prev})"
        return f"\n📧 New subscribers {cur}"
    except Exception as e:
        print("subs line error: " + type(e).__name__); return ""
def scoreboard(period="t"):
    start, end, label, now = _range(period)
    utc = ZoneInfo("UTC"); f = "%Y-%m-%dT%H:%M:%SZ"
    os_ = shopify.orders(start.astimezone(utc).strftime(f), end.astimezone(utc).strftime(f) if end else None)
    os_, n_ws = costs.split_wholesale(os_)
    cogs = costs.load_cogs()
    t = dict(revenue=0, fees=0, cogs=0, shipping=0, returns=0)
    n_ok = n_miss = lines = lines_miss = 0
    tagc = {}; unm = {}; asm = {}
    for o in os_:
        if o["cancelledAt"]: continue
        k = costs.order_costs(o, cogs)
        for x in t: t[x] += k[x]
        for t_ in k["unmapped"]: unm[t_] = unm.get(t_, 0) + 1
        for t_ in k["assumed_books"]: asm[t_] = asm.get(t_, 0) + 1
        for tg in k["tags"]: tagc[tg] = tagc.get(tg, 0) + 1
        lines += k["lines"]; lines_miss += len(k["missing_cogs"])
        if k["missing_cogs"]: n_miss += 1
        else: n_ok += 1
    ads = meta.spend(start.strftime("%Y-%m-%d"), (end or now).strftime("%Y-%m-%d"))
    costs_total = t["fees"] + t["cogs"] + t["shipping"] + t["returns"]
    profit = t["revenue"] - ads - costs_total
    roi = profit / (ads + costs_total) * 100 if (ads + costs_total) else 0
    roas = t["revenue"] / ads if ads else 0
    subs = _subs_line(period, start, end, now)
    prov = n_miss > 0 or bool(tagc)
    notes = []
    ws_note = f"\nℹ️ {n_ws} wholesale order(s) excluded" if n_ws else ""
    if n_miss: notes.append(f"cost missing on {lines_miss} of {lines} line items in {n_miss} of {n_ok + n_miss} orders (counted as $0)")
    notes += [f"{k} ({v} orders)" for k, v in sorted(tagc.items())]
    if unm: notes.append("not in costs.json: " + _titles(unm))
    if asm: notes.append("assumed to be books: " + _titles(asm))
    note = ("\n⚠️ Provisional: " + "; ".join(notes)) if prov else ""
    tag = " (provisional)" if prov else ""
    return (f"📊 {label} Scoreboard\nAs of {now.strftime('%b %-d, %-I:%M %p')} ET\n\n"
        f"💰 Profit{tag} ${profit:,.0f} ({roi:.1f}% ROI)\n💵 Revenue ${t['revenue']:,.0f} ({roas:.2f}x)\n"
        f"💸 Ads ${ads:,.0f} · Costs ${costs_total:,.0f}\n💳 Orders {len(os_)}{subs}{ws_note}{note}")
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

def costs_template(days=None):
    """CSV text: one row per variant seen in recent orders, with mapping status. Returns (csv, n, n_no_shopify_cost, n_unmapped)."""
    import csv, io
    days = days or c.COSTS_DAYS
    since = (datetime.now(ZoneInfo("UTC")) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cfg = costs.load_config(); cogs = costs.load_cogs(); rows = {}
    for o in costs.split_wholesale(shopify.orders(since))[0]:
        for li in o["lineItems"]["nodes"]:
            k = costs.line_key(li); v = li.get("variant") or {}
            it = costs.classify(li, cfg); u = costs.unit_cost(li, cogs, cfg)  # noqa
            sc = (((v.get("inventoryItem") or {}).get("unitCost") or {}).get("amount") or "")
            r = rows.setdefault(k, {"sku": k, "title": li["title"] + (f" - {v['title']}" if v.get("title") and v["title"] != "Default Title" else ""),
                "price": v.get("price") or li["originalUnitPriceSet"]["shopMoney"]["amount"],
                "shopify_unit_cost": sc, "shopify_cost_set": "yes" if sc != "" else "no",
                "mapped_in_costs_json": "yes" if it else "no", "item_type": (it or {}).get("type", ""),
                "books_per_unit": costs.books_in(li, it) if it else "", "cost_used": "" if u is None else f"{u:.2f}", "units_sold": 0})
            r["units_sold"] += li["quantity"]
    buf = io.StringIO(); w = csv.DictWriter(buf, fieldnames=["sku", "title", "price", "shopify_unit_cost", "shopify_cost_set", "mapped_in_costs_json", "item_type", "books_per_unit", "cost_used", "units_sold"])
    w.writeheader()
    for r in sorted(rows.values(), key=lambda r: -r["units_sold"]): w.writerow(r)
    vals = rows.values()
    return (buf.getvalue(), len(rows), sum(1 for r in vals if r["shopify_cost_set"] == "no"),
            sum(1 for r in vals if r["mapped_in_costs_json"] == "no"))
