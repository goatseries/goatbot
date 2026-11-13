import requests
import math
from . import config as c
G = "https://graph.facebook.com/v21.0"
def _get(path, params):
    p = dict(params, access_token=c.META_TOKEN())
    r = requests.get(f"{G}/{path}", params=p, timeout=60); r.raise_for_status(); return r.json()
def spend(since, until, strict=False):
    """Total ad spend (account currency) for dates YYYY-MM-DD, inclusive. Needs ads_read."""
    d = _get(f"{c.META_ACCT()}/insights", {"fields": "spend",
        "time_range": f'{{"since":"{since}","until":"{until}"}}'})
    rows = d.get("data")
    if strict and (not isinstance(rows, list) or len(rows) != 1 or (d.get("paging") or {}).get("next")):
        raise RuntimeError("Meta spend coverage unavailable")
    amount = sum(float(x["spend"]) for x in rows or [])
    if strict and (not math.isfinite(amount) or amount < 0):
        raise RuntimeError("Meta spend invalid")
    return amount
def spend_by_ad(since, until):
    """{ad_name: spend}. Ad names should match the creative id used in UTM content (e.g. v827, i1411)."""
    out, url = {}, f"{c.META_ACCT()}/insights"
    d = _get(url, {"level": "ad", "fields": "ad_name,spend", "limit": 500,
        "time_range": f'{{"since":"{since}","until":"{until}"}}'})
    for x in d.get("data", []):
        out[x["ad_name"]] = out.get(x["ad_name"], 0) + float(x["spend"])
    return out
