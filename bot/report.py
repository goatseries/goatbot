from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import os
import math
from .version import VERSION
from . import config as c, shopify, meta, costs, klaviyo, tiktok
def _utm(o):
    v = ((o.get("customerJourneySummary") or {}).get("lastVisit") or {}).get("utmParameters") or {}
    return v
def _when(iso):
    try: return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ZoneInfo(c.TZ)).strftime("%b %-d %-I:%M %p ET")
    except Exception: return iso or "?"
def sale_message(o, cogs):
    k = costs.order_costs(o, cogs); u = _utm(o)
    lines = [f"💰 NEW SALE — ${k['revenue']:.2f} (excl. tax)", "",
        f"🧾 {o.get('name', '?')} · {o.get('sourceName') or 'unknown source'} · {o.get('displayFinancialStatus', '?')}",
        f"🕒 created {_when(o.get('createdAt'))}", "",
        "🛒 " + ", ".join(f"{l['title']} x{l['quantity']}" for l in o["lineItems"]["nodes"])]
    if u.get("source"): lines.append(f"📣 {u['source']}" + (f"  🎨 {u['content']}" if u.get("content") else ""))
    if u.get("campaign"): lines.append(f"📱 {u['campaign']}")
    return "\n".join(lines)
def amazon_sale_message(o):
    """Alert for a fresh Amazon (marketplace) sale. Shows only the validated order amount
    (ex tax, after refunds, before Amazon fees) - Amazon fees/profit are unknown and never
    estimated. Timestamp is the original marketplace sale time (processedAt)."""
    r = costs.marketplace_revenue([o])                 # same validated math as the scoreboard line
    amt = r["revenue"] if r["valued"] == 1 else None   # None when any money field is missing/inconsistent
    price = f"${amt:.2f}" if amt is not None else "amount unavailable pending validation"
    lines = [f"\U0001f6d2 AMAZON SALE - {price} (excl. tax, before Amazon fees)", "",
        f"\U0001f9fe {o.get('name', '?')} · Amazon · {o.get('displayFinancialStatus', '?')}",
        f"\U0001f552 sold {_when(o.get('processedAt'))}", "",
        "\U0001f4e6 " + ", ".join(f"{l['title']} x{l['quantity']}" for l in o["lineItems"]["nodes"])]
    return "\n".join(lines)
def explain_order(name, seen=()):
    """Debug text for one order: would it be counted, and would the sale feed alert on it. No customer details."""
    found = shopify.find_order(name)
    if not found: return f"No order found for name #{str(name).lstrip('#')} (check the number; the lookup uses Shopify's order name)."
    out = []
    for o in found:
        why = []
        if o.get("test"): why.append("test order")
        if o.get("cancelledAt"): why.append("cancelled")
        allowed = {x.upper() for x in costs.load_config().get("order_statuses", ["PAID", "PARTIALLY_REFUNDED"])}
        st = o.get("displayFinancialStatus")
        if st and st not in allowed: why.append(f"status {st} not counted")
        if costs.load_config().get("exclude_zero_total", True) and costs.is_zero_total(o): why.append("$0 total (fully discounted)")
        if not costs.filter_status([o]): pass
        if costs.marketplace_reason(o): why.append("marketplace: " + costs.marketplace_reason(o))
        kept, n_ws = costs.split_wholesale([o])
        if n_ws: why.append("wholesale: " + (costs.wholesale_reason(o) or "rule"))
        age_days = (datetime.now(ZoneInfo("UTC")) - datetime.fromisoformat(o["createdAt"].replace("Z", "+00:00"))).days
        counted = not why
        feed = "already alerted" if o["id"] in seen else ("would alert on its next update" if counted and age_days <= 3 else
               ("not alerted: " + ("; ".join(why) if why else f"created {age_days} days ago (feed only alerts on orders under 3 days old)")))
        try:
            k = costs.order_costs(o, costs.load_cogs()); amt = f"total incl. tax ${k['revenue'] + k['tax']:.2f}, net of tax ${k['revenue']:.2f}"
        except Exception: amt = "amount unavailable"
        out.append("\n".join([
            f"🔎 Order {o.get('name')} (confirmation {o.get('confirmationNumber') or 'n/a'})",
            f"Source: {o.get('sourceName') or 'unknown'} · status {st} · created {_when(o.get('createdAt'))} · updated {_when(o.get('updatedAt'))}",
            f"Archived/closed: {'yes' if o.get('closed') else 'no'} · cancelled: {'yes' if o.get('cancelledAt') else 'no'} · test: {'yes' if o.get('test') else 'no'} · tags: {', '.join(o.get('tags') or []) or '-'}",
            f"Amounts: {amt}",
            f"Counted in scoreboards: {'YES' if counted else 'NO - ' + '; '.join(why)}",
            f"Sale feed: {feed}"]))
    return "\n\n".join(out)[:3900]
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
    for o in costs.split_wholesale(costs.split_marketplace(shopify.orders(since))[0])[0]:
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
                return f"\n📧 New contacts {cur} (prev {prev})"
        return f"\n📧 New contacts {cur}"
    except Exception as e:
        print("subs line error: " + type(e).__name__); return ""
def _valid_pa(o, tz):
    """Order's original marketplace sale time in ET, or None when processedAt is missing/malformed."""
    pa = o.get("processedAt")
    if not pa: return None
    try: return datetime.fromisoformat(str(pa).replace("Z", "+00:00")).astimezone(tz)
    except Exception: return None
def _mp_cohort(start, end):
    """Marketplace (Amazon) orders by ORIGINAL marketplace sale time. Shopify's processedAt is
    when the marketplace order was actually placed (verified on live import #1653: processedAt
    2026-11-11T14:43:03Z = the marketplace order's placement time; createdAt was the import time).
    Fetched with its own processed_at query, independent of the retail created_at window, so an
    order imported late still lands in its true period. `end` is a TRUE-EXCLUSIVE bound (unlike
    _range's inclusive last-second retail end): half-open [start, end), ET. An order without a
    valid processedAt cannot be dated and is excluded rather than re-dated to import time."""
    tz = ZoneInfo(c.TZ); utc = ZoneInfo("UTC"); f = "%Y-%m-%dT%H:%M:%SZ"
    q_end = end.astimezone(utc).strftime(f) if end else None
    got = shopify.orders(start.astimezone(utc).strftime(f), q_end, field="processed_at")
    out = []
    for o in costs.split_marketplace(got)[1]:
        d = _valid_pa(o, tz)
        if d is not None and d >= start and (end is None or d < end): out.append(o)
    return out
def _ad_totals(start, end, now):
    """Separate reported sources; unknown spend never becomes zero."""
    result = {"meta": None, "tiktok": None, "total": None, "note": ""}
    try:
        account = meta._get(c.META_ACCT(), {"fields": "currency,timezone_name"})
        if account.get("currency") != "USD" or account.get("timezone_name") != c.TZ:
            raise ValueError("Meta account currency or timezone mismatch")
        amount = meta.spend(start.strftime("%Y-%m-%d"), (end - timedelta(microseconds=1)).strftime("%Y-%m-%d"), strict=True)
        if amount < 0 or not math.isfinite(amount):
            raise ValueError("Meta spend invalid")
        result["meta"] = amount
    except Exception:
        result["note"] = "Meta spend unavailable"
    try:
        snap = tiktok.spend(start, end, now)
        result["tiktok"] = float(snap["amount"])
        fetched = snap["fetched_at"].astimezone(ZoneInfo(c.TZ))
        result["note"] += ("; " if result["note"] else "") + "TikTok fetched " + fetched.strftime("%b %-d %-I:%M %p") + " ET; spend may lag or be revised"
    except Exception as exc:
        # Only our fixed, non-provider error messages may be displayed.
        detail = str(exc) if isinstance(exc, tiktok.Unavailable) else "TikTok spend unavailable"
        result["note"] += ("; " if result["note"] else "") + detail
    if result["meta"] is not None and result["tiktok"] is not None:
        result["total"] = result["meta"] + result["tiktok"]
    return result

def _tagged_tiktok(orders, cogs):
    """A labeled Shopify last-visit UTM subset, never added to website total."""
    tagged = [o for o in orders if not o.get("cancelledAt") and str(_utm(o).get("source") or "").strip().lower() == "tiktok"]
    return sum(costs.order_costs(o, cogs)["revenue"] for o in tagged), len(tagged)

def _spend_line(ad):
    fmt = lambda v: f"${v:,.2f}" if v is not None else "unavailable"
    return f"Ads {fmt(ad['total'])} · Meta {fmt(ad['meta'])} / TikTok {fmt(ad['tiktok'])}"

def _compact_marketplace(orders):
    """Presentation only; preserve the existing validated marketplace values."""
    line = costs.marketplace_line(orders)
    if not line:
        return ""
    if "sales unavailable" in line:
        return line.split(" order(s)", 1)[0] + " orders · sales unavailable"
    return line

def scoreboard(period="t"):
    start, end, label, now = _range(period)
    utc = ZoneInfo("UTC"); f = "%Y-%m-%dT%H:%M:%SZ"
    os_ = shopify.orders(start.astimezone(utc).strftime(f), end.astimezone(utc).strftime(f) if end else None)
    os_, mp_seen = costs.split_marketplace(os_)
    # _range's end is the inclusive last second for retail; the cohort needs a true-exclusive bound
    mp = _mp_cohort(start, end + timedelta(seconds=1) if end else None)   # by original marketplace sale date
    undated = sum(1 for o in mp_seen if _valid_pa(o, ZoneInfo(c.TZ)) is None)
    os_, n_ws = costs.split_wholesale(os_)
    cogs = costs.load_cogs()
    t = dict(revenue=0, fees=0, cogs=0, shipping=0, returns=0, units_cost=0, packaging=0)
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
    ad = _ad_totals(start, end + timedelta(seconds=1) if end else now, now)
    ads = ad["total"]
    tt_revenue, tt_orders = _tagged_tiktok(os_, cogs)
    costs_total = t["fees"] + t["cogs"] + t["shipping"] + t["returns"]
    profit = t["revenue"] - ads - costs_total if ads is not None else None
    roi = profit / (ads + costs_total) * 100 if ads is not None and (ads + costs_total) else 0
    roas = t["revenue"] / ads if ads else None
    profit_line = f"Profit ${profit:,.0f} · ROI {roi:.1f}%" if profit is not None else "Profit / ROI / ROAS unavailable: ad spend missing"
    roas_text = f" · ROAS {roas:.2f}x" if roas is not None else (" · ROAS unavailable" if ads is not None else "")
    subs = _subs_line(period, start, end, now)
    mp_line = _compact_marketplace(mp)
    warning = "\n⚠️ Amazon dates incomplete" if undated else ""
    return (f"📊 {label} · {now.strftime('%-I:%M %p')} ET\n"
        f"💰 {profit_line}\n💵 Website sales ${t['revenue']:,.0f}{roas_text}\n"
        f"💸 {_spend_line(ad)}\n🧾 Costs ${costs_total:,.0f} · 💳 Orders {len(os_)}\n"
        f"🎵 TikTok sales ${tt_revenue:,.0f} (included)"
        f"{subs}" + ("\n" + mp_line if mp_line else "") + warning)
def creative_report(days=7):
    tz = ZoneInfo(c.TZ); end = datetime.now(tz); start = end - timedelta(days=days - 1)
    os_ = shopify.orders((start.replace(hour=0, minute=0, second=0).astimezone(ZoneInfo("UTC"))).strftime("%Y-%m-%dT%H:%M:%SZ"))
    sp = meta.spend_by_ad(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    sales = {}
    for o in costs.split_marketplace(os_)[0]:
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
    for o in costs.split_wholesale(costs.split_marketplace(shopify.orders(since))[0])[0]:
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

def breakdown(period="t"):
    """Cost components for a period (for tracing differences between readings). Costs line = fees + product cost + packaging + labels + returns (ad spend is separate)."""
    start, end, label, now = _range(period)
    utc = ZoneInfo("UTC"); f = "%Y-%m-%dT%H:%M:%SZ"
    os_ = shopify.orders(start.astimezone(utc).strftime(f), end.astimezone(utc).strftime(f) if end else None)
    n_all = len(os_); os_, mp = costs.split_marketplace(os_); n_mp = len(mp)
    n_st = len(os_) - len(costs.filter_status(os_)); os_, n_ws = costs.split_wholesale(os_)
    cogs = costs.load_cogs(); t = dict(revenue=0, fees=0, units_cost=0, packaging=0, shipping=0, returns=0); n = n_can = n_un = n_real = 0; tax_total = 0.0
    for o in os_:
        if o["cancelledAt"]: n_can += 1; continue
        k = costs.order_costs(o, cogs); n += 1; n_un += len(k["missing_cogs"]); n_real += k["fee_real"]; tax_total += k["tax"]
        for x in t: t[x] += k[x]
    tot = t["fees"] + t["units_cost"] + t["packaging"] + t["shipping"] + t["returns"]
    return (f"🔎 {label} breakdown ({VERSION}, costs.json {costs.config_tag()})\n"
        f"Window start {start.strftime('%b %-d %-I:%M %p')} ET, as of {now.strftime('%-I:%M %p')} ET\n"
        f"Orders counted {n} · cancelled skipped {n_can} · marketplace excluded {n_mp} · wholesale excluded {n_ws} · status/$0-total excluded {n_st} · fetched {n_all}\n"
        f"Revenue ${t['revenue']:,.2f} (excludes tax ${tax_total:,.2f})\nFees ${t['fees']:,.2f} (real on {n_real} of {n} orders, estimate on the rest) · Products ${t['units_cost']:,.2f} · Packaging ${t['packaging']:,.2f}\n"
        f"Labels ${t['shipping']:,.2f} · Returns ${t['returns']:,.2f}\nCosts total ${tot:,.2f} (ads not included)\n"
        f"Line items without a cost: {n_un} (see /unmapped)")

def _tally(o, prods, books, unexp):
    """prods: product -> [units, list revenue]. books: author -> [standalone units, units inside bundles].
    unexp: bundles/gift sets whose books are not known -> units."""
    cfg = costs.load_config()
    for li in o["lineItems"]["nodes"]:
        q = li["quantity"]; nm = (li.get("title") or costs.display(li) or "?").strip()
        try: rev = float(li["originalUnitPriceSet"]["shopMoney"]["amount"]) * q
        except Exception: rev = 0.0
        p_ = prods.setdefault(nm, [0, 0.0]); p_[0] += q; p_[1] += rev
        a = costs.author_of(li, cfg)
        if a: books.setdefault(a, [0, 0])[0] += q; continue
        comp = costs.components(li, cfg)
        if comp:
            for a in comp: books.setdefault(a, [0, 0])[1] += q
        else:
            it = costs.classify(li, cfg)
            if it and it.get("type") in ("bundle", "gift_set"): unexp[nm] = unexp.get(nm, 0) + q
def _rank(a, view, n):
    """Rows (label, units, revenue_or_None, extra) for the chosen view."""
    if view == "sold":
        rows = [(k, v[0], v[1], "") for k, v in a["prods"].items()]
    else:
        rows = [(k, sum(v), None, f"{v[0]} alone + {v[1]} in bundles") for k, v in a["books"].items()]
    return sorted(rows, key=lambda r: -r[1])[:n]
def _week_totals(orders, cogs):
    """Same math as scoreboard(): paid/partially refunded, tax excluded, real fees, label table, wholesale already removed."""
    t = dict(revenue=0.0, fees=0.0, cogs=0.0, shipping=0.0, returns=0.0); n = 0; prods = {}; books = {}; unexp = {}
    for o in orders:
        if o["cancelledAt"]: continue
        k = costs.order_costs(o, cogs); n += 1
        for x in t: t[x] += k[x]
        _tally(o, prods, books, unexp)
    t["books"] = books; t["unexpanded"] = unexp
    t["costs"] = t["fees"] + t["cogs"] + t["shipping"] + t["returns"]; t["orders"] = n; t["prods"] = prods
    return t
def _chg(cur, prev, money=True, plain=False):
    """'▲ +12.3%' style change; dollar change when prior is 0 or the measure can be negative."""
    d = cur - prev
    if abs(d) < 0.005: return "no change"
    arrow = "▲" if d > 0 else "▼"
    if plain or prev <= 0: return f"{arrow} {'+' if d > 0 else '-'}{'$' if money else ''}{abs(d):,.0f}"
    return f"{arrow} {d / prev * 100:+.0f}%"
def weekly_recap():
    """Previous full Mon-Sun week (ET) vs the week before."""
    tz = ZoneInfo(c.TZ); utc = ZoneInfo("UTC"); now = datetime.now(tz)
    mon = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now.weekday())
    w1, w0 = mon - timedelta(days=7), mon - timedelta(days=14)     # w1 = last week start, w0 = week before
    f = "%Y-%m-%dT%H:%M:%SZ"
    allo = shopify.orders(w0.astimezone(utc).strftime(f), (mon - timedelta(seconds=1)).astimezone(utc).strftime(f))
    allo = costs.split_marketplace(allo)[0]; allo, _ = costs.split_wholesale(allo); cogs = costs.load_cogs()
    def created(o): return datetime.fromisoformat(o["createdAt"].replace("Z", "+00:00")).astimezone(tz)
    mp_wk = _mp_cohort(w1, mon)          # by original marketplace sale date, not import date
    a = _week_totals([o for o in allo if w1 <= created(o) < mon], cogs)
    b = _week_totals([o for o in allo if w0 <= created(o) < w1], cogs)
    for t_, s_, e_ in ((a, w1, mon - timedelta(days=1)), (b, w0, w1 - timedelta(days=1))):
        t_["ad"] = _ad_totals(s_, e_ + timedelta(days=1), now)
        t_["ads"] = t_["ad"]["total"]
        t_["profit"] = t_["revenue"] - t_["ads"] - t_["costs"] if t_["ads"] is not None else None
        t_["roas"] = t_["revenue"] / t_["ads"] if t_["ads"] else None
        t_["roi"] = t_["profit"] / (t_["ads"] + t_["costs"]) * 100 if t_["ads"] is not None and (t_["ads"] + t_["costs"]) else 0
    subs = [klaviyo.new_profiles(w1.astimezone(utc), mon.astimezone(utc)), klaviyo.new_profiles(w0.astimezone(utc), w1.astimezone(utc))]
    rng = lambda s_, e_: f"{s_.strftime('%b %-d')}-{e_.strftime('%b %-d')}"
    complete = a['ads'] is not None and b['ads'] is not None
    roas_fmt = lambda value: f"{value:.2f}x" if value is not None else "unavailable"
    financial = [
        f"💰 Profit ${a['profit']:,.0f} · ROI {a['roi']:.1f}% · {_chg(a['profit'], b['profit'], plain=True)}",
        f"🎯 ROAS {roas_fmt(a['roas'])} · prev {roas_fmt(b['roas'])}"] if complete else ["💰 Profit / ROI / ROAS comparison unavailable: ad spend missing"]
    tt_revenue, tt_orders = _tagged_tiktok([o for o in allo if w1 <= created(o) < mon], cogs)
    L = [f"📅 {rng(w1, mon - timedelta(days=1))} · vs {rng(w0, w1 - timedelta(days=1))}"] + financial + [
         f"💵 Website sales ${a['revenue']:,.0f} · {_chg(a['revenue'], b['revenue'])}",
         "💸 " + _spend_line(a['ad']),
         f"🧾 Costs ${a['costs']:,.0f} · {_chg(a['costs'], b['costs'])}",
         f"💳 Orders {a['orders']} · {_chg(a['orders'], b['orders'], money=False)}",
         f"🎵 TikTok sales ${tt_revenue:,.0f} (included)"]
    _ml = _compact_marketplace(mp_wk)
    if _ml: L.append(_ml)
    if subs[0] is not None and subs[1] is not None: L.append(f"📧 New contacts {subs[0]} · {_chg(subs[0], subs[1], money=False)}")
    elif subs[0] is not None: L.append(f"📧 New contacts {subs[0]}")
    view = os.environ.get("TOP_VIEW", "books").lower()
    if view not in ("sold", "books"): view = "books"
    rows = _rank(a, view, 5)
    if rows:
        L += ["", "🏆 Top books" if view == "books" else "🏆 Best sellers"]
        for i, (nm, u, rev, extra) in enumerate(rows, 1):
            prev = (sum(b["books"].get(nm, [0, 0])) if view == "books" else b["prods"].get(nm, [0, 0.0])[0])
            L.append(f"{i}. {nm[:55]} · {u} units (prev {prev})")
        if view == "books" and a["unexpanded"]:
            L.append("⚠️ Some bundle contents unknown; top books incomplete")
    return "\n".join(L)[:3900]

def top_report():
    """Last full Mon-Sun week: both views, the filters applied, and the order names used (for reconciling with Shopify)."""
    tz = ZoneInfo(c.TZ); utc = ZoneInfo("UTC"); now = datetime.now(tz)
    mon = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now.weekday()); w1 = mon - timedelta(days=7)
    f = "%Y-%m-%dT%H:%M:%SZ"
    allo = shopify.orders(w1.astimezone(utc).strftime(f), (mon - timedelta(seconds=1)).astimezone(utc).strftime(f))
    n_all = len(allo); paid = costs.filter_status(allo); kept, mp = costs.split_marketplace(allo); n_mp = len(mp); kept, n_ws = costs.split_wholesale(kept)
    n_canc = sum(1 for o in kept if o["cancelledAt"]); used = [o for o in kept if not o["cancelledAt"]]
    a = _week_totals(used, costs.load_cogs())
    low_o = low_u = 0
    for o in paid:
        lu = sum(li["quantity"] for li in o["lineItems"]["nodes"] if costs.is_low_price_book(li))
        if lu: low_o += 1; low_u += lu
    st = {}
    for o in allo: st[o.get("displayFinancialStatus")] = st.get(o.get("displayFinancialStatus"), 0) + 1
    L = [f"🔎 Top sellers {w1.strftime('%b %-d')}-{(mon - timedelta(days=1)).strftime('%b %-d')} (ET)",
         f"Orders fetched {n_all} ({', '.join(f'{k} {v}' for k, v in sorted(st.items(), key=lambda kv: str(kv[0])))})",
         f"Counted {len(used)} (paid/partially refunded, not $0, not wholesale/marketplace) · marketplace excluded {n_mp} · wholesale excluded {n_ws} · cancelled {n_canc}",
         f"Low-price book lines (list price <= ${(costs.load_config().get('wholesale') or {}).get('low_price_book_rule', {}).get('max_unit_price', 10):g}): {low_u} units in {low_o} orders, "
         + ("EXCLUDED as wholesale" if ((costs.load_config().get('wholesale') or {}).get('low_price_book_rule') or {}).get('enabled') else "still counted (rule off)"), "",
         "1) As sold (bundles are their own products), units · list revenue"]
    for i, (nm, u, rev, _) in enumerate(_rank(a, "sold", 10), 1): L.append(f"{i}. {nm[:55]} - {u} · ${rev:,.0f}")
    L += ["", "2) Books incl. inside bundles and gift sets: alone + in bundles"]
    for i, (nm, u, _, ex) in enumerate(_rank(a, "books", 10), 1): L.append(f"{i}. {nm} - {u} ({ex})")
    if a["unexpanded"]: L.append("Not expanded (contents unknown): " + ", ".join(f"{k} x{v}" for k, v in sorted(a["unexpanded"].items(), key=lambda kv: -kv[1])[:6]))
    why = {}
    for o in allo:
        r = costs.exclusion_reason(o)
        if r: why.setdefault(r, []).append(o.get("name", "?"))
    if why:
        L += ["", "Not counted:"]
        for r, ns in sorted(why.items(), key=lambda kv: -len(kv[1])):
            L.append(f"- {r}: {len(ns)} orders ({', '.join(ns[:12])}{' ...' if len(ns) > 12 else ''})")
    names = ", ".join(o.get("name", "?") for o in sorted(used, key=lambda o: o["createdAt"])[:60])
    L += ["", f"Orders used: {names}" + (" ..." if len(used) > 60 else "")]
    out = ["\n".join(L)[:3900]]
    used_ids = {o["id"] for o in used}
    def lines_for(author, orders):
        res = []
        for o in orders:
            for li in o["lineItems"]["nodes"]:
                if costs.author_of(li) == author: kind = "standalone"
                elif author in (costs.components(li) or []): kind = "in " + (li.get("title") or "?")[:32]
                else: continue
                try: pr = f"${float(li['originalUnitPriceSet']['shopMoney']['amount']):.2f}"
                except Exception: pr = "?"
                res.append(f"{o.get('name', '?')} {o.get('displayFinancialStatus', '?')} x{li['quantity']} {kind} @{pr}")
        return res
    det = []
    for nm, u, _, ex in _rank(a, "books", 3):
        ln = lines_for(nm, sorted(used, key=lambda o: o["createdAt"]))
        excl = lines_for(nm, [o for o in allo if o["id"] not in used_ids])
        det.append(f"📖 {nm}: {u} units ({ex}) from {len(ln)} lines\n" + "\n".join(ln[:45]) + (f"\n... +{len(ln) - 45} more" if len(ln) > 45 else "")
                   + ("\nNot counted (status/wholesale/cancelled): " + "; ".join(excl[:15]) if excl else ""))
    txt = "\n\n".join(det)
    while txt: out.append(txt[:3800]); txt = txt[3800:]
    return out
