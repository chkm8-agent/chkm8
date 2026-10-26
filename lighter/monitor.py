#!/usr/bin/env python3
"""chkm8 position monitor. Polls Lighter every POLL seconds.
X posting: the first CHKM8_X_TOPLEVEL_PER_DAY (default 3) new positions per Pacific day get their own
top-level post; later ones go into that day's "trade log" thread. A position's close (TP / stop-loss /
manual / liquidation / ADL) is posted as a reply under its own entry post.
Scalper/HF trading must NOT run on this account: it gets its own Lighter sub-account (unwatched here)
and posts one daily summary instead of per-trade posts.
On close: cancels leftover reduce-only exit orders for that market, posts result as reply in the trade thread.
State: $CHKM8_DATA/monitor_state.json (Mac: ~/chkm8/lighter)   Log: $CHKM8_LOGS/monitor.log (Mac: ~/chkm8/logs; "-" = stdout)
CHKM8_DRY_RUN=1: read-only (no X posts, no order cancels). See chkm8env.py for all settings."""
import asyncio, json, os, sys, time, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lighter
import chkm8env, xpost

BASE = "https://mainnet.zklighter.elliot.ai"
API = BASE + "/api/v1"
STATE = f"{chkm8env.DATA}/monitor_state.json"
ACC = chkm8env.account()
IDX, KIDX = ACC["account_index"], ACC["api_key_index"]
POLL = int(chkm8env.env("CHKM8_POLL", "30"))
X_CAP = int(chkm8env.env("CHKM8_X_TOPLEVEL_PER_DAY", "3"))   # top-level trade posts per (Pacific) day
X_TRADES = chkm8env.env("CHKM8_X_TRADES", "1") == "1"         # 0 = don't post new trades to X (private PnL only)
log = chkm8env.logger("monitor", "monitor.log")

def get(path):
    try:
        return json.load(urllib.request.urlopen(f"{API}/{path}", timeout=20))
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or b"{}")

def load_state():
    try: return json.load(open(STATE))
    except FileNotFoundError: return {"positions": {}, "thread_id": None}

def save_state(s):
    json.dump(s, open(STATE + ".tmp", "w"), indent=2); os.replace(STATE + ".tmp", STATE)

def open_positions():
    a = get(f"account?by=index&value={IDX}")["accounts"][0]
    out = {}
    for p in a.get("positions", []):
        sz = float(p.get("position") or 0)
        if sz:
            imf = float(p.get("initial_margin_fraction") or 0)
            out[p["symbol"]] = {"market_id": p["market_id"], "size": sz, "side": "long" if int(p["sign"]) == 1 else "short",
                                "entry": float(p["avg_entry_price"]), "liq": p.get("liquidation_price"),
                                "lev": round(100 / imf, 1) if imf else None,
                                "mode": "isolated" if int(p.get("margin_mode", 0)) == 1 else "cross",
                                "margin": float(p.get("allocated_margin") or 0)}
    return out, a

def signer():
    return lighter.SignerClient(url=BASE, account_index=IDX,
                                api_private_keys={KIDX: chkm8env.lighter_api_key(IDX, KIDX)})

def fmt(x, d=4): return f"{x:.{d}f}".rstrip("0").rstrip(".")

async def analyse_close(sym, pos):
    c = signer(); tok, _ = c.create_auth_token_with_expiry()
    mid, opened = pos["market_id"], pos["opened_ms"]
    trades = get(f"trades?account_index={IDX}&market_id={mid}&sort_by=timestamp&limit=100&auth={tok}").get("trades", [])
    closing_is_ask = pos["side"] == "long"
    fills = []
    for t in trades:
        if t["timestamp"] <= opened: continue
        mine_ask = t.get("ask_account_id") == IDX
        mine_bid = t.get("bid_account_id") == IDX
        if (closing_is_ask and mine_ask) or (not closing_is_ask and mine_bid):
            fills.append(t)
    qty = sum(float(t["size"]) for t in fills)
    usd = sum(float(t["usd_amount"]) for t in fills)
    exit_px = usd / qty if qty else None
    kinds = {t.get("type") for t in fills}
    close_ids = {int(t["ask_id"] if closing_is_ask else t["bid_id"]) for t in fills}
    reason = "closed"
    if "liquidation" in kinds: reason = "liquidated"
    elif "deleverage" in kinds: reason = "adl"
    else:
        # Match the exact order(s) that produced the closing fills. Order history can lag the fill by a few
        # seconds (LIT 2026-10-02 was a stop-loss but got labelled "closed"), so retry before giving up.
        found = None
        for attempt in range(5):
            inact = get(f"accountInactiveOrders?account_index={IDX}&market_id={mid}&limit=30&auth={tok}").get("orders", [])
            types = {int(o["order_index"]): o["type"] for o in inact if o.get("status") == "filled"}
            hit = [types[i] for i in close_ids if i in types]
            if hit:
                found = hit[0]; break
            await asyncio.sleep(4)
        if found:
            if found.startswith("stop-loss"): reason = "stopped"
            elif found.startswith("take-profit"): reason = "tp"
        elif exit_px:   # last resort: compare exit price to the triggers recorded at open
            sl, tp = pos.get("sl"), pos.get("tp")
            d = 1 if pos["side"] == "long" else -1
            if sl and (exit_px - sl) * d <= abs(sl) * 0.01: reason = "stopped"
            elif tp and (exit_px - tp) * d >= -abs(tp) * 0.01: reason = "tp"
        log(f"close classification {sym}: order types {found} -> {reason}")
    # cancel leftover exit orders on this market
    act = get(f"accountActiveOrders?account_index={IDX}&market_id={mid}&auth={tok}").get("orders", [])
    for o in act:
        if o.get("reduce_only"):
            if chkm8env.DRY_RUN:
                log(f"DRY RUN: would cancel leftover {o['type']} {o['order_index']}"); continue
            _, _, err = await c.cancel_order(mid, int(o["order_index"]))
            log(f"cancel leftover {o['type']} {o['order_index']}: {err or 'ok'}")
    await c.close()
    size = pos["size"]; entry = pos["entry"]
    if exit_px is None: exit_px = float(get(f"orderBookDetails?market_id={mid}")["order_book_details"][0]["last_trade_price"])
    pnl = (exit_px - entry) * size * (1 if pos["side"] == "long" else -1)
    return reason, exit_px, pnl, qty

def compose(sym, pos, reason, exit_px, pnl, equity):
    pct = pnl / (pos["entry"] * pos["size"]) * 100
    sign = "+" if pnl >= 0 else "-"
    head = {"tp": f"TP hit on ${sym} ✅ printed",
            "stopped": f"stopped out on ${sym} 🩸 stop did its job",
            "liquidated": f"liquidated on ${sym} 💀 rekt. the stop didn't save me",
            "adl": f"auto-deleveraged on ${sym} ⚠️ exchange closed me out",
            "closed": f"closed ${sym} {pos['side']} ✂️"}[reason]
    tail = {"tp": "patience > leverage. NFA 🤖", "stopped": "small L, account lives. NFA 🤖",
            "liquidated": "lesson logged. NFA 🤖", "adl": "NFA 🤖", "closed": "NFA 🤖"}[reason]
    return (f"{head}\n\n{pos['side']} {fmt(pos['size'],2)} {sym}\nentry {fmt(pos['entry'])} → exit {fmt(exit_px)}\n"
            f"PnL {sign}${abs(pnl):.2f} ({sign}{abs(pct):.1f}% on notional)\n\naccount now ${equity:.2f}\n\n{tail}")

def exits(mid):
    """Return (stop_loss_trigger, take_profit_trigger) from resting reduce-only orders."""
    c = signer(); tok, _ = c.create_auth_token_with_expiry()
    sl = tp = None
    for o in get(f"accountActiveOrders?account_index={IDX}&market_id={mid}&auth={tok}").get("orders", []):
        if not o.get("reduce_only"): continue
        trig = float(o.get("trigger_price") or 0) or None
        if o["type"].startswith("stop-loss"): sl = trig
        elif o["type"].startswith("take-profit"): tp = trig
    return sl, tp, c

def compose_open(sym, p, sl, tp):
    notional = p["entry"] * p["size"]
    lines = [f"new position 🎯\n", f"{p['side']} ${sym} on Lighter",
             f"entry {fmt(p['entry'])} · {fmt(p['size'],2)} {sym} · ${notional:.0f} notional"]
    if p.get("lev"): lines.append(f"{fmt(p['lev'],1)}x {p['mode']}, ${p['margin']:.0f} margin on the line")
    risk = []
    sgn = 1 if p["side"] == "long" else -1
    if sl: risk.append(f"SL {fmt(sl)} (-${abs((sl - p['entry']) * p['size']):.1f})")
    if tp: risk.append(f"TP {fmt(tp)} (+${abs((tp - p['entry']) * p['size']):.1f})")
    lines.append("")
    lines.append(" · ".join(risk) if risk else "no stop set 👀 living dangerously")
    if p.get("liq"):
        try: lines.append(f"liq ~{float(p['liq']):.2f}")
        except ValueError: pass
    lines.append("\nNFA 🤖")
    return "\n".join(lines)

def post(text, reply_to):
    return xpost.post(text, reply_to, log=log)

def x_open_target(s):
    """Where a new position's entry post goes. The first X_CAP trades of the day (Pacific, TZ env) are
    top-level posts; after that they go into one 'trade log' thread for the day so followers' feeds
    aren't spammed. Returns ("top", None) or ("log", tweet_id_to_reply_to)."""
    today = time.strftime("%Y-%m-%d")
    d = s.get("x_day")
    if not d or d.get("date") != today:
        d = s["x_day"] = {"date": today, "toplevel": 0, "log_root": None, "log_last": None}
    if d["toplevel"] < X_CAP:
        return "top", None
    if not d["log_root"]:
        root = post(f"📒 trade log {time.strftime('%b %d')}: busy day, {X_CAP} trades already posted. "
                    f"the rest go in this thread so your feed survives 🧵\n\nNFA 🤖", None)
        d["log_root"] = d["log_last"] = root
    return "log", d["log_last"]

async def tick():
    s = load_state()
    live, acct = open_positions()
    # newly opened positions: post + track
    for sym, p in live.items():
        if sym not in s["positions"]:
            p["opened_ms"] = int(time.time() * 1000) - 60_000
            await asyncio.sleep(5)          # let SL/TP orders land after the entry fill
            sl, tp, c = exits(p["market_id"]); await c.close()
            text = compose_open(sym, p, sl, tp)
            if not X_TRADES:
                tid = None; kind = "private"     # autonomous trading: no X posts (operator, 2026-10-08)
            else:
                kind, reply_to = x_open_target(s)   # top-level (first 3/day) or the day's trade-log thread
                tid = post(text, reply_to)
            log(f"OPEN {sym} {p} sl={sl} tp={tp} x={kind} tweet={tid}\n{text}")
            if tid:
                p["thread_id"] = tid
                if kind == "top": s["x_day"]["toplevel"] += 1
                else: s["x_day"]["log_last"] = tid
            p["sl"], p["tp"] = sl, tp
            s["positions"][sym] = p
        else:
            s["positions"][sym].update(size=p["size"], entry=p["entry"], liq=p["liq"])
    # closed positions
    for sym in [k for k in s["positions"] if k not in live]:
        pos = s["positions"][sym]
        reason, exit_px, pnl, qty = await analyse_close(sym, pos)
        equity = float(acct.get("total_asset_value") or acct.get("collateral"))
        text = compose(sym, pos, reason, exit_px, pnl, equity)
        log(f"CLOSE {sym} reason={reason} exit={exit_px} pnl={pnl:.4f}\n{text}")
        # Close replies under that trade's own post. Trades opened privately (no entry post) stay private.
        tid = post(text, pos["thread_id"]) if pos.get("thread_id") else None
        s.setdefault("history", []).append({"sym": sym, "reason": reason, "entry": pos["entry"], "exit": exit_px,
                                             "size": pos["size"], "pnl": pnl, "closed_ms": int(time.time()*1000), "tweet": tid})
        del s["positions"][sym]
    save_state(s)

async def main():
    log(f"monitor start (account {IDX}, key {KIDX}, poll {POLL}s, state {STATE}"
        f"{', DRY RUN' if chkm8env.DRY_RUN else ''})")
    os.makedirs(chkm8env.DATA, exist_ok=True)
    while True:
        try:
            await tick()
        except Exception as e:
            log(f"tick error: {e!r}")
        # Liveness heartbeat: "the loop is alive", written even when a tick fails (e.g. Lighter down),
        # so k8s restarts a hung process but doesn't restart-loop us during an upstream outage.
        if chkm8env.env("CHKM8_HEARTBEAT"):
            try: open(chkm8env.env("CHKM8_HEARTBEAT"), "w").write(str(int(time.time())))
            except OSError as e: log(f"heartbeat write failed: {e!r}")
        await asyncio.sleep(POLL)

if __name__ == "__main__":
    if "--once" in sys.argv: asyncio.run(tick())
    else: asyncio.run(main())
