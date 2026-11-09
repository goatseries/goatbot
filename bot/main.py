import time, json, os, re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from . import config as c, shopify, telegram, report, costs
STATE = os.environ.get("STATE_PATH", "state.json")  # use a Railway volume for persistence
def redact(msg):
    for k in ("TELEGRAM_BOT_TOKEN", "SHOPIFY_ADMIN_TOKEN", "SHOPIFY_CLIENT_SECRET", "SHOPIFY_CLIENT_ID", "META_ACCESS_TOKEN", "KLAVIYO_API_KEY"):
        v = os.environ.get(k)
        if v: msg = msg.replace(v, "[REDACTED]")
    msg = re.sub(r"shp(?:at|ca|ss)_[A-Za-z0-9]+", "[REDACTED]", msg)
    return re.sub(r"bot\d+:[A-Za-z0-9_-]+", "bot[REDACTED]", re.sub(r"access_token=[^&\s]+", "access_token=[REDACTED]", msg))
def load():
    try: return json.load(open(STATE))
    except Exception: return {"seen": [], "last_poll": None, "sent_slots": []}
def save(s): json.dump(s, open(STATE, "w"))
def main():
    s = load()
    if not s["last_poll"]:
        s["last_poll"] = datetime.now(timezone.utc).isoformat()
    while True:
        try:
            since = (datetime.fromisoformat(s["last_poll"]) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
            cogs = costs.load_cogs()
            for o in shopify.orders(since):
                if o["id"] in s["seen"] or o["cancelledAt"]: continue
                telegram.send(report.sale_message(o, cogs)); s["seen"].append(o["id"])
            s["seen"] = s["seen"][-2000:]
            s["last_poll"] = datetime.now(timezone.utc).isoformat()
            now = datetime.now(ZoneInfo(c.TZ)); today = now.strftime("%Y-%m-%d")
            for t in c.TIMES:
                slot = f"{today} {t}"
                if now.strftime("%H:%M") >= t and slot not in s["sent_slots"]:
                    telegram.send(report.scoreboard())
                    # creative report disabled in v1: report.creative_report()
                    s["sent_slots"] = (s["sent_slots"] + [slot])[-20:]
            s["tg_offset"], reqs = telegram.commands(s.get("tg_offset", 0))
            for p_ in reqs: telegram.send(report.scoreboard(p_))
            save(s)
        except Exception as e:
            print("error:", redact(type(e).__name__ + ": " + str(e)))
        time.sleep(c.POLL)
if __name__ == "__main__": main()
