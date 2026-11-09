import csv, json, os, re
from . import config as c
_cfg = {}
def load_config():
    """costs.json (COSTS_JSON env, default costs.json). Missing file = empty config."""
    p = os.environ.get("COSTS_JSON", "costs.json")
    if p not in _cfg:
        try: _cfg[p] = json.load(open(p))
        except Exception as e:
            print("costs.json not loaded: " + type(e).__name__); _cfg[p] = {}
    return _cfg[p]
def load_cogs():
    """Optional CSV override: columns sku,cogs (extra columns ignored, blank cogs skipped)."""
    if not os.path.exists(c.COGS_CSV): return {}
    out = {}
    with open(c.COGS_CSV, newline="") as f:
        for r in csv.DictReader(f):
            k, v = (r.get("sku") or "").strip(), (r.get("cogs") or "").strip()
            if k and v:
                try: out[k] = float(v.replace("$", ""))
                except ValueError: pass
    return out
def line_key(li):
    v = li.get("variant") or {}
    return (li.get("sku") or v.get("sku") or "").strip() or li["title"]
def norm(x):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (x or "").lower()).split())
def fields(li):
    """All text we match on: line title, name, variant title, product title/type, tags, sku."""
    p = li.get("product") or {}; v = li.get("variant") or {}
    sku = ((li.get("sku") or v.get("sku")) or "").strip()
    texts = [li.get("title"), li.get("name"), li.get("variantTitle"), v.get("title"), p.get("title")]
    return sku, [norm(t) for t in texts if t], norm(p.get("productType")), {norm(t) for t in (p.get("tags") or [])}
def classify(li, cfg=None):
    """Matching costs.json item or None. SKU, SKU prefix, product type, tag, then title substring."""
    cfg = load_config() if cfg is None else cfg
    sku, texts, ptype, tags = fields(li)
    for it in cfg.get("items", []):
        if sku and (sku in it.get("skus", []) or any(sku.lower().startswith(x.lower()) for x in it.get("sku_prefixes", []))): return it
        if ptype and ptype in {norm(x) for x in it.get("product_types", [])}: return it
        if tags & {norm(x) for x in it.get("tags", [])}: return it
        for sub in it.get("title_contains", []):
            n = norm(sub)
            if n and any(n in t for t in texts): return it
    return None
def resolve(li, cfg=None):
    """(item, is_default). Falls back to unmapped_default unless an exclude word appears."""
    cfg = load_config() if cfg is None else cfg
    it = classify(li, cfg)
    if it: return it, False
    d = cfg.get("unmapped_default")
    if d:
        _, texts, ptype, tags = fields(li)
        blob = " ".join(texts + [ptype] + sorted(tags))
        if not any(norm(w) in blob for w in d.get("exclude_words", [])): return d, True
    return None, False
def display(li):
    return (li.get("name") or li.get("title") or "").strip()
def unit_cost(li, cogs, cfg=None):
    """costs.json item, then CSV override by sku, then Shopify unit cost. None if none."""
    cfg = load_config() if cfg is None else cfg
    it, _ = resolve(li, cfg)
    if it:
        if "unit_cost" in it: return float(it["unit_cost"])
        t = cfg.get("unit_costs_by_type", {}).get(it.get("type"))
        if t is not None: return float(t)
    k = line_key(li)
    if k in cogs: return cogs[k]
    uc = (((li.get("variant") or {}).get("inventoryItem") or {}).get("unitCost") or {}).get("amount")
    return float(uc) if uc not in (None, "") else None
def books_in(li, it):
    if not it: return 0
    return it.get("books_per_unit", 1 if it.get("type") == "book" else 0)
def _tier(books, tiers, cfg):
    """(cost, estimated?). Exact tier, else linear interpolation between neighbours,
    else extrapolation above the top tier at shipping_extrapolate_per_book."""
    t = {int(k): float(v) for k, v in tiers.items()}
    if books in t: return t[books], False
    lo = [k for k in t if k < books]; hi = [k for k in t if k > books]
    if lo and hi:
        a, b = max(lo), min(hi)
        return t[a] + (t[b] - t[a]) * (books - a) / (b - a), True
    if hi: return t[min(hi)], True
    top = max(t)
    return t[top] + float(cfg.get("shipping_extrapolate_per_book", 0)) * (books - top), True
COUNT_STATUSES = None
def real_fee(o):
    """(fee, True) from Shopify Payments transaction fees on successful sale/capture transactions, else (None, False)."""
    tot, found = 0.0, False
    for t in o.get("transactions") or []:
        if t.get("kind") not in ("SALE", "CAPTURE") or t.get("status") != "SUCCESS": continue
        for f in t.get("fees") or []:
            try:
                tot += float(f["amount"]["amount"]) + float((f.get("taxAmount") or {}).get("amount") or 0); found = True
            except Exception: pass
    return (tot, True) if found else (None, False)
def filter_status(orders):
    """Keep paid and partially refunded orders (costs.json order_statuses overrides). Orders without status data are kept."""
    allowed = {x.upper() for x in load_config().get("order_statuses", ["PAID", "PARTIALLY_REFUNDED"])}
    zero = load_config().get("exclude_zero_total", True)
    return [o for o in orders if (not o.get("displayFinancialStatus") or o["displayFinancialStatus"] in allowed)
            and not (zero and is_zero_total(o))]
def is_zero_total(o):
    try: return float(o["totalPriceSet"]["shopMoney"]["amount"]) < 0.005
    except Exception: return False
def order_costs(o, cogs):
    cfg = load_config()
    gross = float(o["totalPriceSet"]["shopMoney"]["amount"])
    def _m(k):
        try: return float(o[k]["shopMoney"]["amount"])
        except Exception: return None
    tax = _m("totalTaxSet") or 0.0
    rev = gross - tax                      # sales tax is not income; shipping charged stays in revenue
    fee, fee_real = real_fee(o)
    if fee is None: fee, fee_real = gross * c.FEE_PCT / 100 + c.FEE_FIXED, False
    cur = _m("currentTotalTaxSet")
    tax_refunded = max(0.0, tax - cur) if cur is not None else 0.0
    cg, missing, lines, books, nonbook, gift_set, tags = 0.0, [], 0, 0, False, False, set()
    unmapped, assumed_books, ground = [], [], False
    units_cost = pack_cost = 0.0
    for li in o["lineItems"]["nodes"]:
        lines += 1
        it, is_def = resolve(li, cfg)
        u = unit_cost(li, cogs, cfg)
        if u is None: missing.append(line_key(li))
        else: cg += u * li["quantity"]; units_cost += u * li["quantity"]
        b = books_in(li, it)
        books += b * li["quantity"]
        if not b: nonbook = True
        if it and it.get("gift_set"): gift_set = True
        if it and it.get("ships_ground"): ground = True
        if it and it.get("assumed"): tags.add("estimated: " + it["assumed"])
        if it and it.get("wholesale"): tags.add("wholesale orders use retail packaging/label rules")
        if is_def: assumed_books.append(display(li)); tags.add("unmapped products assumed to be books")
        elif not it: unmapped.append(display(li))
    # packaging: first matching rule in costs.json
    pk = cfg.get("packaging_costs", {})
    if pk and cfg.get("packaging_rules"):
        boxes = None
        for r in cfg["packaging_rules"]:
            w = r.get("when", {})
            if w.get("gift_set") and not gift_set: continue
            if "min_books" in w and books < w["min_books"]: continue
            if "max_books" in w and books > w["max_books"]: continue
            boxes = r["boxes"]
            if r.get("assumed"): tags.add("packaging assumed: " + r["assumed"])
            break
        if boxes: pack_cost = sum(float(pk.get(x, 0)) for x in boxes); cg += pack_cost
    # shipping label
    tiers = cfg.get("shipping_label_by_books")
    if tiers:
        if books > 0:
            ship, est = _tier(books, tiers, cfg)
            if est: tags.add("label cost estimated (interpolated/extrapolated) for some book counts")
        elif cfg.get("no_book_shipping") is not None: ship = float(cfg["no_book_shipping"])
        else:
            ship, _ = _tier(1, tiers, cfg); tags.add("label cost for orders with no books assumed")
        if nonbook and books > 0 and cfg.get("non_book_adds_shipping") is None:
            tags.add("extra shipping for non-book items assumed $0")
        elif nonbook and books > 0: ship += float(cfg["non_book_adds_shipping"])
    else:
        ship = c.SHIP_DEFAULT
        if not ship: tags.add("shipping not included yet")
    if tiers and ground and cfg.get("ground_label") is not None: ship = max(ship, float(cfg["ground_label"]))
    ret = max(0.0, float(o["totalRefundedSet"]["shopMoney"]["amount"]) - tax_refunded)
    return {"fee_real": fee_real, "tax": tax, "revenue": rev, "fees": fee, "cogs": cg, "shipping": ship, "returns": ret,
            "missing_cogs": missing, "lines": lines, "books": books, "tags": tags,
            "unmapped": unmapped, "assumed_books": assumed_books,
            "units_cost": units_cost, "packaging": pack_cost}

def is_low_price_book(li, r=None):
    r = r or (load_config().get("wholesale") or {}).get("low_price_book_rule") or {}
    try: price = float(li["originalUnitPriceSet"]["shopMoney"]["amount"])
    except Exception: return False
    blob = " ".join(fields(li)[1])
    return price <= float(r.get("max_unit_price", 10)) and any(norm(x) in blob for x in r.get("title_contains", []))
def author_of(li, cfg=None):
    cfg = load_config() if cfg is None else cfg
    blob = " ".join(fields(li)[1]); it = classify(li, cfg)
    if it and it.get("type") == "book":
        for a in cfg.get("authors", []):
            if norm(a) in blob: return a
    return None
def components(li, cfg=None):
    """List of author names inside a known bundle, else None."""
    cfg = load_config() if cfg is None else cfg
    blob = " ".join(fields(li)[1])
    for k, v in cfg.get("bundle_components", {}).items():
        if norm(k) in blob: return v
    return None
def _is_ws_line(li, w):
    sku = ((li.get("sku") or (li.get("variant") or {}).get("sku")) or "").strip()
    blob = " ".join(fields(li)[1])
    r = w.get("low_price_book_rule") or {}
    low = bool(r.get("enabled")) and is_low_price_book(li, r)
    return low or (bool(sku) and any(sku.startswith(x) for x in w.get("sku_prefixes", []))) or \
           any(norm(x) in blob for x in w.get("title_words", []))
def _is_ws_order(o, w):
    tags = {norm(t) for t in (o.get("tags") or [])}
    if tags & {norm(t) for t in w.get("order_tags", [])}: return True
    src = (norm(o.get("sourceName")) + " " + norm((o.get("app") or {}).get("name"))).strip()
    return bool(src) and any(norm(x) and norm(x) in src for x in w.get("source_names", []))
def split_wholesale(orders):
    """(retail_orders, n_excluded). Rule in costs.json wholesale.mixed_order_rule."""
    orders = filter_status(orders)
    w = load_config().get("wholesale")
    if not w or w.get("mixed_order_rule") == "include_all": return orders, 0
    rule = w.get("mixed_order_rule", "exclude_order"); keep, n = [], 0
    for o in orders:
        lines = o["lineItems"]["nodes"]; ws = [li for li in lines if _is_ws_line(li, w)]
        if not ws and not _is_ws_order(o, w): keep.append(o); continue
        if rule == "exclude_lines" and ws and len(ws) < len(lines) and not _is_ws_order(o, w):
            rest = [li for li in lines if li not in ws]
            rev = sum(float(li["originalUnitPriceSet"]["shopMoney"]["amount"]) * li["quantity"] for li in rest)
            o2 = dict(o); o2["lineItems"] = {"nodes": rest}
            o2["totalPriceSet"] = {"shopMoney": {"amount": str(rev)}}
            o2["totalRefundedSet"] = {"shopMoney": {"amount": "0"}}
            keep.append(o2)
        else: n += 1
    return keep, n

def config_tag():
    """Short fingerprint of costs.json so readings can be traced to a config."""
    import hashlib
    try: return hashlib.sha1(open(os.environ.get("COSTS_JSON", "costs.json"), "rb").read()).hexdigest()[:7]
    except Exception: return "none"

def wholesale_reason(o):
    """Why an order counts as wholesale (for /top), or None."""
    w = load_config().get("wholesale")
    if not w or w.get("mixed_order_rule") == "include_all": return None
    tags = {norm(t) for t in (o.get("tags") or [])}
    hit = tags & {norm(t) for t in w.get("order_tags", [])}
    if hit: return "order tag " + "/".join(sorted(hit))
    src = (norm(o.get("sourceName")) + " " + norm((o.get("app") or {}).get("name"))).strip()
    for x in w.get("source_names", []):
        if norm(x) and norm(x) in src: return "source/app " + x
    r = w.get("low_price_book_rule") or {}
    for li in o["lineItems"]["nodes"]:
        sku = ((li.get("sku") or (li.get("variant") or {}).get("sku")) or "").strip()
        if sku and any(sku.startswith(x) for x in w.get("sku_prefixes", [])): return "wholesale SKU"
        if any(norm(x) in " ".join(fields(li)[1]) for x in w.get("title_words", [])): return "'wholesale' in title"
        if r.get("enabled") and is_low_price_book(li, r): return "low-price book line (list price <= $%g)" % float(r.get("max_unit_price", 10))
    return None
def exclusion_reason(o):
    """None if the order is counted, else a short reason string."""
    st = o.get("displayFinancialStatus"); allowed = {x.upper() for x in load_config().get("order_statuses", ["PAID", "PARTIALLY_REFUNDED"])}
    if st and st not in allowed: return f"status {st}"
    if load_config().get("exclude_zero_total", True) and is_zero_total(o): return "$0 total (fully discounted)"
    if split_wholesale([o])[1]: return "wholesale: " + (wholesale_reason(o) or "rule")
    if o.get("cancelledAt"): return "cancelled"
    return None
