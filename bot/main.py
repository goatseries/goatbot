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
    try: s = json.load(open(STATE))
    except Exception: s = {}
    s.setdefault("seen", []); s.setdefault("pending", []); s.setdefault("last_poll", None); s.setdefault("sent_slots", [])
    return s
def save(s):
    """Atomic write: temp file in the same directory, fsync, rename."""
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, STATE)
def check_state_path():
    d = os.path.dirname(os.path.abspath(STATE))
    try:
        os.makedirs(d, exist_ok=True)
        t = os.path.join(d, ".write_test"); open(t, "w").write("x"); os.remove(t)
    except Exception as e:
        print(f"WARNING: state path {STATE} is not writable ({type(e).__name__}); alerts may repeat after restarts"); return
    if not (os.path.ismount(d) or os.stat(d).st_dev != os.stat("/").st_dev):
        print(f"WARNING: {STATE} is not on a mounted volume. State is lost on every redeploy, which can re-send old alerts. Mount a Railway volume and set STATE_PATH=/data/state.json")
    else:
        print(f"state path {STATE} is on a mounted volume")
def new_sale_decision(o, s, keep_ids, max_age_h):
    """Returns 'alert', 'seed' (mark seen silently) or 'skip' (leave for later)."""
    if o["id"] in s["seen"]: return "skip"
    if o["cancelledAt"]: return "seed" if o["id"] not in s["pending"] else "skip"
    age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(o["createdAt"].replace("Z", "+00:00"))).total_seconds() / 3600
    st = o.get("displayFinancialStatus")
    if st in ("PENDING", "AUTHORIZED", "PARTIALLY_PAID", "UNPAID"):
        if age_h <= 24 * 7 and o["id"] not in s["pending"]: s["pending"].append(o["id"])
        return "skip"
    if o["id"] not in keep_ids: return "seed"         # wholesale, voided, refunded etc.
    if age_h <= max_age_h: return "alert"              # genuinely new order
    if o["id"] in s["pending"]: return "alert"         # we saw it unpaid and it has since been paid
    return "seed"                                      # older order that was just touched: never alert
def main():
    s = load()
    fresh = not s["last_poll"]
    check_state_path()
    print(f"state: {len(s['seen'])} seen orders, last_poll {s['last_poll']}" + (" (NO SAVED STATE: new deploy or no volume at STATE_PATH=" + STATE + ")" if fresh else ""))
    from .version import VERSION
    print("starting", VERSION, "costs.json", costs.config_tag())
    if not c.DRY_RUN: telegram.startup_check()
    if not s["last_poll"]:
        s["last_poll"] = datetime.now(timezone.utc).isoformat()
    while True:
        try:
            since = (datetime.fromisoformat(s["last_poll"]) - timedelta(minutes=0 if fresh else 5)).strftime("%Y-%m-%dT%H:%M:%SZ")
            cogs = costs.load_cogs()
            got = shopify.orders(since, field="updated_at")
            keep_ids = {o["id"] for o in costs.split_wholesale(got)[0]}
            max_age_h = float(os.environ.get("ALERT_MAX_AGE_HOURS", "6"))
            for o in got:
                d = new_sale_decision(o, s, keep_ids, max_age_h)
                if d == "alert":
                    telegram.send(report.sale_message(o, cogs))
                    print(f"alert: order {o.get('name')} id {o['id'].split('/')[-1]} status {o.get('displayFinancialStatus')} source {o.get('sourceName')} created {o.get('createdAt')} updated {o.get('updatedAt')}")
                if d in ("alert", "seed"):
                    s["seen"].append(o["id"])
                    if o["id"] in s["pending"]: s["pending"].remove(o["id"])
                    if d == "seed": print(f"seeded (no alert): order {o.get('name')} status {o.get('displayFinancialStatus')} created {o.get('createdAt')} updated {o.get('updatedAt')}")
                    save(s)   # persist right after each alert/seed
            s["seen"] = s["seen"][-5000:]; s["pending"] = s["pending"][-500:]
            s["last_poll"] = datetime.now(timezone.utc).isoformat()
            now = datetime.now(ZoneInfo(c.TZ)); today = now.strftime("%Y-%m-%d")
            for t in c.TIMES:
                slot = f"{today} {t}"
                if now.strftime("%H:%M") >= t and slot not in s["sent_slots"]:
                    telegram.send(report.scoreboard())
                    # creative report disabled in v1: report.creative_report()
                    s["sent_slots"] = (s["sent_slots"] + [slot])[-20:]
            now_w = datetime.now(ZoneInfo(c.TZ)); wk = (now_w - timedelta(days=now_w.weekday())).strftime("%Y-%m-%d")
            days = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
            if (days.index(c.WEEKLY_DAY) if c.WEEKLY_DAY in days else 0) == now_w.weekday() and now_w.strftime("%H:%M") >= c.WEEKLY_TIME \
               and wk not in s.setdefault("weekly_sent", []) and time.time() > s.get("weekly_retry_after", 0):
                try:
                    telegram.send(report.weekly_recap()); s["weekly_sent"] = (s["weekly_sent"] + [wk])[-10:]; save(s)
                except Exception as e:
                    print("weekly recap error:", redact(type(e).__name__ + ": " + str(e))); s["weekly_retry_after"] = time.time() + 600
            s["tg_offset"], reqs = telegram.commands(s.get("tg_offset", 0))
            for chat_id, p_ in reqs:
                if p_ == "costs":
                    csv_text, n, n_no, n_un = report.costs_template()
                    telegram.send_document("cost-mapping.csv", csv_text, f"{n} products/variants from the last {c.COSTS_DAYS} days; {n_un} are not mapped in costs.json, {n_no} have no Shopify cost per item set. Add the unmapped ones to costs.json.", chat_id=chat_id)
                elif p_ == "breakdown": telegram.send(report.breakdown(), chat_id=chat_id)
                elif p_.startswith("order "): telegram.send(report.explain_order(p_.split(" ", 1)[1], set(s["seen"])), chat_id=chat_id)
                elif p_ == "top":
                    for part in report.top_report(): telegram.send(part, chat_id=chat_id)
                elif p_ == "week": telegram.send(report.weekly_recap(), chat_id=chat_id)
                elif p_ == "unmapped": telegram.send(report.unmapped_report(), chat_id=chat_id)
                else: telegram.send(report.scoreboard(p_), chat_id=chat_id)
            save(s)
        except Exception as e:
            print("error:", redact(type(e).__name__ + ": " + str(e)))
        time.sleep(c.POLL)
if __name__ == "__main__": main()
