import requests
from . import config as c
def send(text):
    if c.DRY_RUN:
        print("[DRY_RUN]\n" + text); return
    r = requests.post(f"https://api.telegram.org/bot{c.TG_TOKEN()}/sendMessage",
        json={"chat_id": c.TG_CHAT(), "text": text[:4000]}, timeout=30)
    r.raise_for_status()

def commands(offset):
    """Poll for /t /y /w /m from the configured chat only. Returns (new_offset, [period,...])."""
    if c.DRY_RUN: return offset, []
    r = requests.get(f"https://api.telegram.org/bot{c.TG_TOKEN()}/getUpdates",
        params={"offset": offset, "timeout": 0}, timeout=30)
    r.raise_for_status(); out = []
    for u in r.json()["result"]:
        offset = u["update_id"] + 1
        m = u.get("message") or {}
        if str((m.get("chat") or {}).get("id")) != str(c.TG_CHAT()): continue
        w = (m.get("text") or "").split("@")[0].strip().lower()
        if w in ("/t", "/y", "/w", "/m"): out.append(w[1])
    return offset, out
