import csv, os
from . import config as c
def load_cogs():
    if not os.path.exists(c.COGS_CSV): return {}
    with open(c.COGS_CSV) as f:
        return {r["sku"].strip(): float(r["cogs"]) for r in csv.DictReader(f) if r.get("sku")}
def order_costs(o, cogs):
    rev = float(o["totalPriceSet"]["shopMoney"]["amount"])
    fee = rev * c.FEE_PCT / 100 + c.FEE_FIXED
    cg, missing = 0.0, []
    for li in o["lineItems"]["nodes"]:
        sku = li["sku"] or li["title"]
        if sku in cogs: cg += cogs[sku] * li["quantity"]
        else: missing.append(sku)
    ret = float(o["totalRefundedSet"]["shopMoney"]["amount"])
    return {"revenue": rev, "fees": fee, "cogs": cg, "shipping": c.SHIP_DEFAULT,
            "returns": ret, "missing_cogs": missing}
