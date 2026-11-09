import os, requests
from datetime import timedelta
from . import config as c
BASE = "https://a.klaviyo.com/api"
# Pinned revision. Older ones retire 2 years after release; override with KLAVIYO_REVISION if needed.
REVISION = os.environ.get("KLAVIYO_REVISION", "2024-10-15")
def _h(): return {"Authorization": f"Klaviyo-API-Key {c.KLAVIYO()}", "revision": REVISION, "accept": "application/json"}
def _detail(r):
    """errors[].detail only; never the key or URL."""
    try: return "; ".join(str(e.get("detail", "")) for e in r.json().get("errors", []))[:300]
    except Exception: return "(no detail)"
def new_profiles(start_utc, end_utc, max_pages=100):
    """Count profiles created in [start, end) (UTC-aware datetimes). Needs profiles:read.
    The profiles endpoint only supports greater-than / less-than on `created`
    (no greater-or-equal), so start is moved back 1 second to stay inclusive.
    Returns int, or None on any error / no key (caller omits the line)."""
    if not c.KLAVIYO(): return None
    f = "%Y-%m-%dT%H:%M:%SZ"
    url = f"{BASE}/profiles/"
    params = {"filter": f"greater-than(created,{(start_utc - timedelta(seconds=1)).strftime(f)}),less-than(created,{end_utc.strftime(f)})",
              "fields[profile]": "created", "page[size]": 100}
    n = 0
    try:
        for _ in range(max_pages):
            r = requests.get(url, headers=_h(), params=params, timeout=30)
            if r.status_code != 200:
                print(f"klaviyo profiles failed: HTTP {r.status_code}: {_detail(r)}"); return None
            j = r.json(); n += len(j.get("data", []))
            nxt = (j.get("links") or {}).get("next")
            if not nxt: return n
            url, params = nxt, None
        print("klaviyo: page cap hit, count is a lower bound"); return n
    except Exception as e:
        print("klaviyo error: " + type(e).__name__); return None
