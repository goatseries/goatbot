import requests
from . import config as c
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
        r = requests.post(f"https://{c.SHOP()}/admin/api/{c.API_VER}/graphql.json",
            headers={"X-Shopify-Access-Token": c.SHOP_TOKEN()},
            json={"query": Q, "variables": {"q": q, "after": after}}, timeout=60)
        r.raise_for_status()
        j = r.json()
        if "errors" in j: raise RuntimeError(j["errors"])
        d = j["data"]["orders"]
        out += [o for o in d["nodes"] if not o["test"]]
        if not d["pageInfo"]["hasNextPage"]: return out
        after = d["pageInfo"]["endCursor"]
