import csv, os
from . import config as c
def load_cogs():
    """CSV override: columns sku,cogs (extra columns ignored, blank cogs skipped)."""
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
def unit_cost(li, cogs):
    """CSV override first, then Shopify inventoryItem unitCost. None if neither."""
    k = line_key(li)
    if k in cogs: return cogs[k]
    uc = (((li.get("variant") or {}).get("inventoryItem") or {}).get("unitCost") or {}).get("amount")
    return float(uc) if uc not in (None, "") else None
def order_costs(o, cogs):
    rev = float(o["totalPriceSet"]["shopMoney"]["amount"])
    fee = rev * c.FEE_PCT / 100 + c.FEE_FIXED
    cg, missing, lines = 0.0, [], 0
    for li in o["lineItems"]["nodes"]:
        lines += li["quantity"] and 1
        u = unit_cost(li, cogs)
        if u is None: missing.append(line_key(li))
        else: cg += u * li["quantity"]
    ret = float(o["totalRefundedSet"]["shopMoney"]["amount"])
    return {"revenue": rev, "fees": fee, "cogs": cg, "shipping": c.SHIP_DEFAULT,
            "returns": ret, "missing_cogs": missing, "lines": lines}
