import time, requests
from . import config as c
_tok = {"v": None, "exp": 0}
def _token(force=False):
    """Client credentials grant (Dev Dashboard apps); falls back to static SHOPIFY_ADMIN_TOKEN."""
    if not (c.SHOP_CLIENT_ID() and c.SHOP_CLIENT_SECRET()):
        t = c.SHOP_TOKEN()
        if not t: raise SystemExit("Set SHOPIFY_CLIENT_ID + SHOPIFY_CLIENT_SECRET (or SHOPIFY_ADMIN_TOKEN)")
        return t
    if force or not _tok["v"] or time.time() > _tok["exp"] - 300:
        r = requests.post(f"https://{c.SHOP()}/admin/oauth/access_token", json={
            "grant_type": "client_credentials", "client_id": c.SHOP_CLIENT_ID(),
            "client_secret": c.SHOP_CLIENT_SECRET()}, timeout=30)
        if r.status_code != 200:
            # never include request body/secret; status only
            raise RuntimeError(f"Shopify token request failed: HTTP {r.status_code}")
        j = r.json()
        _tok["v"] = j["access_token"]; _tok["exp"] = time.time() + int(j.get("expires_in", 86400))
    return _tok["v"]
def _post(body):
    for attempt in (0, 1):
        r = requests.post(f"https://{c.SHOP()}/admin/api/{c.API_VER}/graphql.json",
            headers={"X-Shopify-Access-Token": _token(force=attempt == 1)}, json=body, timeout=60)
        if r.status_code == 401 and attempt == 0 and c.SHOP_CLIENT_ID(): continue
        r.raise_for_status(); return r.json()
Q = """
query($q:String!,$after:String){orders(first:100,after:$after,query:$q,sortKey:CREATED_AT){
 pageInfo{hasNextPage endCursor}
 nodes{id name createdAt test cancelledAt
  totalPriceSet{shopMoney{amount}} totalRefundedSet{shopMoney{amount}}
  totalShippingPriceSet{shopMoney{amount}}
  customer{numberOfOrders}
  customerJourneySummary{lastVisit{utmParameters{source medium campaign content}}}
  lineItems(first:50){nodes{sku title quantity originalUnitPriceSet{shopMoney{amount}}}}
 }}}"""
def orders(since_iso, until_iso=None):
    """All non-test orders created in [since, until]. Needs read_orders."""
    q = f"created_at:>='{since_iso}'" + (f" created_at:<='{until_iso}'" if until_iso else "")
    out, after = [], None
    while True:
        j = _post({"query": Q, "variables": {"q": q, "after": after}})
        if "errors" in j: raise RuntimeError(j["errors"])
        d = j["data"]["orders"]
        out += [o for o in d["nodes"] if not o["test"]]
        if not d["pageInfo"]["hasNextPage"]: return out
        after = d["pageInfo"]["endCursor"]
