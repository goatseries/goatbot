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
query($q:String!,$after:String){orders(first:25,after:$after,query:$q,sortKey:CREATED_AT){
 pageInfo{hasNextPage endCursor}
 nodes{id name createdAt updatedAt test cancelledAt closed tags sourceName
  displayFinancialStatus totalPriceSet{shopMoney{amount}} totalTaxSet{shopMoney{amount}} currentTotalTaxSet{shopMoney{amount}} totalRefundedSet{shopMoney{amount}}
  transactions(first:10){kind status gateway fees{amount{amount} taxAmount{amount}}}
  totalShippingPriceSet{shopMoney{amount}}
  customer{numberOfOrders}
  customerJourneySummary{lastVisit{utmParameters{source medium campaign content}}}
  lineItems(first:25){nodes{sku title name variantTitle product{title productType tags} quantity originalUnitPriceSet{shopMoney{amount}} variant{id title sku price inventoryItem{unitCost{amount}}}}}
 }}}"""
_LEAN = None
def _lean(q):
    """Same query without the transactions/fees block (fallback)."""
    i = q.index("  transactions(first:10)"); j = q.index("\n", i)
    return q[:i] + q[j + 1:]
_fees_ok = {"v": True}
def orders(since_iso, until_iso=None, field="created_at"):
    """All non-test orders with `field` (created_at or updated_at) in [since, until]. Needs read_orders.
    If the fee block errors, retries once without it and logs a one-line safe message."""
    q = f"{field}:>='{since_iso}'" + (f" {field}:<='{until_iso}'" if until_iso else "")
    out, after = [], None
    while True:
        query = Q if _fees_ok["v"] else _lean(Q)
        j = _post({"query": query, "variables": {"q": q, "after": after}})
        if "errors" in j and _fees_ok["v"]:
            print("shopify: order query with transaction fees failed, using fee estimate: " + str(j["errors"])[:200])
            _fees_ok["v"] = False; out, after = [], None; continue
        if "errors" in j: raise RuntimeError(str(j["errors"])[:300])
        d = j["data"]["orders"]
        out += [o for o in d["nodes"] if not o["test"]]
        if not d["pageInfo"]["hasNextPage"]: return out
        after = d["pageInfo"]["endCursor"]

def find_order(name):
    """Order(s) matching a name like 1234 or #1234, with extra debug fields. Falls back to the standard query on error."""
    n = str(name).strip().lstrip("#")
    q = f"name:#{n}"
    body = Q.replace("orders(first:25,", "orders(first:5,").replace("nodes{id name", "nodes{id name confirmationNumber", 1)
    for query in (body, Q.replace("orders(first:25,", "orders(first:5,")):
        j = _post({"query": query, "variables": {"q": q, "after": None}})
        if "errors" not in j: return j["data"]["orders"]["nodes"]
        print("shopify: /order lookup query failed: " + str(j["errors"])[:200])
    return []
