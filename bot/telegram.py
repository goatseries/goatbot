import re, requests
from . import config as c

def _chat_id():
    """Use the id as-is (group ids are negative, supergroups start with -100); only strip whitespace/quotes."""
    v = (c.TG_CHAT() or "").strip().strip('"').strip("'")
    return int(v) if re.fullmatch(r"-?\d+", v) else v  # @channelname stays a string

def _err(r):
    """Telegram's description only; never the URL or token."""
    try:
        j = r.json(); d = j.get("description", ""); p = j.get("parameters")
        return f"HTTP {r.status_code}: {d}" + (f" parameters={p}" if p else "")
    except Exception:
        return f"HTTP {r.status_code}: (non-JSON body)"

def send(text, parse_mode=None):
    if c.DRY_RUN:
        print("[DRY_RUN]\n" + text); return
    if not text or not text.strip():
        print("telegram: skipped empty message"); return
    url = f"https://api.telegram.org/bot{c.TG_TOKEN()}/sendMessage"
    payload = {"chat_id": _chat_id(), "text": text[:4000]}
    if parse_mode: payload["parse_mode"] = parse_mode
    r = requests.post(url, json=payload, timeout=30)
    if r.status_code != 200 and parse_mode and "parse entities" in _err(r).lower():
        print("telegram: parse error, retrying without parse_mode")
        payload.pop("parse_mode"); r = requests.post(url, json=payload, timeout=30)
    if r.status_code != 200:
        msg = "telegram sendMessage failed: " + _err(r)
        print(msg)
        raise RuntimeError(msg)

def send_document(filename, content, caption=""):
    if c.DRY_RUN:
        print(f"[DRY_RUN] document {filename}\n" + content[:500]); return
    r = requests.post(f"https://api.telegram.org/bot{c.TG_TOKEN()}/sendDocument",
        data={"chat_id": _chat_id(), "caption": caption},
        files={"document": (filename, content.encode("utf-8"), "text/csv")}, timeout=60)
    if r.status_code != 200:
        msg = "telegram sendDocument failed: " + _err(r); print(msg); raise RuntimeError(msg)

def startup_check():
    """Log bot username and whether the chat id is usable. Prints no token and no chat content."""
    try:
        r = requests.get(f"https://api.telegram.org/bot{c.TG_TOKEN()}/getMe", timeout=30)
        if r.status_code != 200:
            print("telegram getMe failed: " + _err(r) + " (bot token wrong?)"); return
        print("telegram bot: @" + r.json()["result"].get("username", "?"))
    except Exception as e:
        print("telegram getMe error: " + type(e).__name__); return
    cid = _chat_id()
    if isinstance(cid, int) and cid > 0:
        print("telegram: TELEGRAM_CHAT_ID is positive (a private user chat). For a group it is negative, e.g. -100...; that user must have pressed Start on the bot.")
    elif isinstance(cid, str):
        print("telegram: TELEGRAM_CHAT_ID is not numeric; use the numeric id (or @channelusername for public channels).")
    r = requests.post(f"https://api.telegram.org/bot{c.TG_TOKEN()}/getChat", json={"chat_id": cid}, timeout=30)
    print("telegram chat check: " + ("ok" if r.status_code == 200 else _err(r) +
          " (bot not in the chat, bot not started by user, or wrong id)"))

def commands(offset):
    """Poll for /t /y /w /m from the configured chat only. Returns (new_offset, [period,...])."""
    if c.DRY_RUN: return offset, []
    r = requests.get(f"https://api.telegram.org/bot{c.TG_TOKEN()}/getUpdates",
        params={"offset": offset, "timeout": 0}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError("telegram getUpdates failed: " + _err(r))
    out = []
    for u in r.json()["result"]:
        offset = u["update_id"] + 1
        m = u.get("message") or {}
        if str((m.get("chat") or {}).get("id")) != str(_chat_id()): continue
        w = (m.get("text") or "").split("@")[0].strip().lower()
        if w in ("/t", "/y", "/w", "/m", "/costs"): out.append(w[1:])
    return offset, out
