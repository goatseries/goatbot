import requests
from . import config as c
def _h(): return {"Authorization": f"Klaviyo-API-Key {c.KLAVIYO()}", "revision": "2024-10-15", "accept": "application/json"}
def flow_name_for(utm_campaign):
    """Placeholder: map UTM campaign/flow ids to readable names. Needs flows:read, campaigns:read.
    First version just uses the UTM campaign string from Shopify (e.g. 001a_cart_abandon)."""
    return utm_campaign
def list_flows():
    r = requests.get("https://a.klaviyo.com/api/flows/", headers=_h(), timeout=30); r.raise_for_status()
    return {f["id"]: f["attributes"]["name"] for f in r.json()["data"]}
