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
 nodes{id name createdAt processedAt updatedAt test cancelledAt closed tags sourceName
  displayFinancialStatus totalPriceSet{shopMoney{amount}} totalTaxSet{shopMoney{amount}} currentTotalTaxSet{shopMoney{amount}} totalRefundedSet{shopMoney{amount}}
  app{name}
  transactions(first:10){kind status gateway fees{amount{amount} taxAmount{amount}}}
  totalShippingPriceSet{shopMoney{amount}}
  customer{numberOfOrders}
  customerJourneySummary{lastVisit{utmParameters{source medium campaign content}}}
  lineItems(first:25){nodes{sku title name variantTitle product{title productType tags} quantity originalUnitPriceSet{shopMoney{amount}} variant{id title sku price inventoryItem{unitCost{amount}}}}}
 }}}"""
_LEAN = None
_dropped = set()
def _build(q, dropped):
    """Order query minus optional blocks that failed before: 'app' (app{name}) and 'transactions' (fees)."""
    for blk in dropped:
        marker = "  app{name}" if blk == "app" else "  transactions(first:10)"
        i = q.index(marker); j = q.index("\n", i); q = q[:i] + q[j + 1:]
    return q
def orders(since_iso, until_iso=None, field="created_at"):
    """All non-test orders with `field` (created_at or updated_at) in [since, until]. Needs read_orders.
    Optional blocks (app name, transaction fees) are dropped one at a time if the API rejects them, with a one-line log."""
    q = f"{field}:>='{since_iso}'" + (f" {field}:<='{until_iso}'" if until_iso else "")
    out, after = [], None
    while True:
        gql = _build(Q, _dropped)  # rebuilt each pass so app/transactions drops apply on retry
        if field == "processed_at": gql = gql.replace("sortKey:CREATED_AT", "sortKey:PROCESSED_AT")  # match sort to date field (2025-10)
        j = _post({"query": gql, "variables": {"q": q, "after": after}})
        if "errors" in j:
            msg = str(j["errors"])[:200]
            blk = "app" if ("app" in msg.lower() and "app" not in _dropped) else ("transactions" if "transactions" not in _dropped else None)
            if blk is None: raise RuntimeError(msg)
            print(f"shopify: order query without {blk} block after error: {msg}")
            _dropped.add(blk); out, after = [], None; continue
        d = j["data"]["orders"]
        out += [o for o in d["nodes"] if not o["test"]]
        if not d["pageInfo"]["hasNextPage"]: return out
        after = d["pageInfo"]["endCursor"]

def find_order(name):
    """Order(s) matching a name like 1234 or #1234, with extra debug fields. Falls back to the standard query on error."""
    n = str(name).strip().lstrip("#")
    q = f"name:#{n}"
    body = Q.replace("orders(first:25,", "orders(first:5,").replace("nodes{id name", "nodes{id name confirmationNumber", 1)
    for query in (_build(body, _dropped), _build(Q.replace("orders(first:25,", "orders(first:5,"), _dropped)):
        j = _post({"query": query, "variables": {"q": q, "after": None}})
        if "errors" not in j: return j["data"]["orders"]["nodes"]
        print("shopify: /order lookup query failed: " + str(j["errors"])[:200])
    return []
