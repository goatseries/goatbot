import requests
from . import config as c
BASE = "https://a.klaviyo.com/api"
def _h(): return {"Authorization": f"Klaviyo-API-Key {c.KLAVIYO()}", "revision": "2024-10-15", "accept": "application/json"}
def new_profiles(start_utc, end_utc, max_pages=100):
    """Count profiles created in [start, end) (datetimes, UTC-aware). Needs profiles:read.
    Returns int, or None on any error / no key (caller omits the line)."""
    if not c.KLAVIYO(): return None
    f = "%Y-%m-%dT%H:%M:%SZ"
    url = f"{BASE}/profiles/"
    params = {"filter": f"and(greater-or-equal(created,{start_utc.strftime(f)}),less-than(created,{end_utc.strftime(f)}))",
              "fields[profile]": "created", "page[size]": 100}
    n = 0
    try:
        for _ in range(max_pages):
            r = requests.get(url, headers=_h(), params=params, timeout=30)
            if r.status_code != 200:
                print(f"klaviyo profiles failed: HTTP {r.status_code}"); return None
            j = r.json(); n += len(j.get("data", []))
            nxt = (j.get("links") or {}).get("next")
            if not nxt: return n
            url, params = nxt, None
        print("klaviyo: page cap hit, count is a lower bound"); return n
    except Exception as e:
        print("klaviyo error: " + type(e).__name__); return None
