import csv, json, os
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
def classify(li, cfg=None):
    """Matching costs.json item or None."""
    cfg = load_config() if cfg is None else cfg
    sku = ((li.get("sku") or (li.get("variant") or {}).get("sku")) or "").strip()
    t = (li.get("title") or "").lower()
    for it in cfg.get("items", []):
        if sku and sku in it.get("skus", []): return it
        if any(s.lower() in t for s in it.get("title_contains", [])): return it
    return None
def unit_cost(li, cogs, cfg=None):
    """costs.json item, then CSV override by sku, then Shopify unit cost. None if none."""
    cfg = load_config() if cfg is None else cfg
    it = classify(li, cfg)
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
def order_costs(o, cogs):
    cfg = load_config()
    rev = float(o["totalPriceSet"]["shopMoney"]["amount"])
    fee = rev * c.FEE_PCT / 100 + c.FEE_FIXED
    cg, missing, lines, books, nonbook, gift_set, tags = 0.0, [], 0, 0, False, False, set()
    for li in o["lineItems"]["nodes"]:
        lines += 1
        it = classify(li, cfg)
        u = unit_cost(li, cogs, cfg)
        if u is None: missing.append(line_key(li))
        else: cg += u * li["quantity"]
        b = books_in(li, it)
        books += b * li["quantity"]
        if not b: nonbook = True
        if it and it.get("gift_set"): gift_set = True
        if it and it.get("assumed"): tags.add("some item costs are estimates")
        if not it: tags.add("unmapped items in costs.json")
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
        if boxes: cg += sum(float(pk.get(x, 0)) for x in boxes)
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
    ret = float(o["totalRefundedSet"]["shopMoney"]["amount"])
    return {"revenue": rev, "fees": fee, "cogs": cg, "shipping": ship, "returns": ret,
            "missing_cogs": missing, "lines": lines, "books": books, "tags": tags}
