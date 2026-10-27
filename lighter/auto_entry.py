#!/usr/bin/env python3
"""chkm8 auto entry: places live 4h-breakout longs right after the paper scanner flags them.

Moves the mechanical part of the old Mac "signal responder" onto the cluster, so an entry no longer waits for the
Mac, the tunnel and an LLM run. The LLM still reviews every entry minutes later (Mac) and twice a day (trade call).

Every run (CronJob, 2 minutes after each paper-scanner run):
  1. Read new signals from $CHKM8_DATA/paper_scanner.json (crypto only, not skipped for funding).
  2. Coded filters (same rules the responder followed):
     - fresh: the 4h bar closed within the last 8 hours (older = stale, logged once, never entered)
     - volume: >= $5M/day (watchlist names below the floor are left to the trade call)
     - no chasing: skip if price ran > 1 ATR above the signal close, or fell > 0.5 ATR below it
     - pause flag: if $CHKM8_DATA/auto_entry.pause exists, log and skip (the trade call can set/clear it)
  3. Levels: stop = price - 2 ATR, target = price + 4 ATR. Size for 1.4% of equity at the stop; size up to the
     market minimum if needed (stop risk still <= $25); shrink once to fit the $20 total-risk cap.
  4. Hand the order to lighter/trade.py long(): every hard cap is asserted there (stop required, leverage <= 3,
     5 positions, one per symbol, $25 per trade, $20 total, funding cost). A refusal is logged, never retried.

Mode: CHKM8_AUTO_ENTRY=dry (plan + all trade.py checks, no order) | live (sends). Default dry.
State/events: $CHKM8_DATA/auto_entry.json (read by the Mac post-entry review and the trade call).
"""
import asyncio, contextlib, io, json, math, os, sys, time, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import chkm8env

MODE = chkm8env.env("CHKM8_AUTO_ENTRY", "dry")
SCANNER = f"{chkm8env.DATA}/paper_scanner.json"
STATE = f"{chkm8env.DATA}/auto_entry.json"
PAUSE = f"{chkm8env.DATA}/auto_entry.pause"
FRESH_S = 8 * 3600            # bar must have closed within this window
RISK_FRAC = 0.014             # ~1.4% of equity at the stop
SL_ATR, TP_ATR = 2.0, 4.0
CHASE_UP_ATR, CHASE_DOWN_ATR = 1.0, 0.5
MIN_VOL = 5e6
LEV = 2
MIN_USD = 11.0
WATCHLIST = {x.strip().upper() for x in chkm8env.env("CHKM8_WATCHLIST", "").split(",") if x.strip()}
IGNORE = ("crowded funding", "funding too expensive", "underlying market closed", "breakouts are crypto-only")
log = chkm8env.logger("auto_entry", "auto_entry.log")


def load():
    try:
        return json.load(open(STATE))
    except FileNotFoundError:
        return {"handled": {}, "events": []}


def save(s):
    s["events"] = s["events"][-300:]
    cut = time.time() - 14 * 86400
    s["handled"] = {k: v for k, v in s["handled"].items() if v >= cut}
    json.dump(s, open(STATE + ".tmp", "w"), indent=1)
    os.replace(STATE + ".tmp", STATE)


def event(s, sig, action, reason, **kw):
    e = {"at": int(time.time()), "mode": MODE, "sym": sig["sym"], "bar": sig["bar"], "action": action,
         "reason": reason, **kw}
    s["events"].append(e)
    s["handled"][f"{sig['sym']}@{sig['bar']}"] = e["at"]
    log(f"{action} {sig['sym']} bar={time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime(sig['bar']))} {reason}"
        + (f" | {kw.get('plan')}" if kw.get("plan") else ""))


def rnd(x, dec):
    return round(x, dec)


def size_trade(T, m, px, atr, equity, open_risk_usd):
    """-> (usd, sl, tp, loss) or raises ValueError(reason)."""
    pd, sd = m["price_decimals"], m["size_decimals"]
    sl, tp = rnd(px - SL_ATR * atr, pd), rnd(px + TP_ATR * atr, pd)
    if sl <= 0:
        raise ValueError("stop below zero")
    frac = (px - sl) / px
    usd = math.floor(RISK_FRAC * equity / frac)
    min_usd = max(MIN_USD, float(m["min_quote_amount"]) * 1.02, float(m["min_base_amount"]) * px * 1.02)
    usd = max(usd, math.ceil(min_usd))
    usd = min(usd, math.floor(equity * T.MAX_NOTIONAL_FRAC))
    loss = (px - sl) * round(usd / px, sd)
    if loss > T.MAX_LOSS_USD:
        raise ValueError(f"market minimum needs ${loss:.2f} at the stop > ${T.MAX_LOSS_USD:.0f} cap")
    room = T.MAX_TOTAL_RISK_USD - open_risk_usd
    if loss > room:                       # shrink once to fit the portfolio cap, same stop
        usd = math.floor(room / frac)
        loss = (px - sl) * round(usd / px, sd)
        if usd < math.ceil(min_usd) or loss <= 0:
            raise ValueError(f"total risk cap: ${open_risk_usd:.2f} already at stops, ${room:.2f} room is below the market minimum")
    return usd, sl, tp, loss


async def run():
    import trade as T                    # lighter/trade.py: the enforced caps live there
    s = load()
    try:
        sc = json.load(open(SCANNER))
    except FileNotFoundError:
        log("no scanner state yet"); return
    now = time.time()
    sigs = [x for x in sc.get("signals", []) if f"{x['sym']}@{x['bar']}" not in s["handled"]
            and x.get("skipped") not in IGNORE and now - x["bar"] < 7 * 86400]
    if not sigs:
        log(f"mode={MODE}: no new signals"); save(s); return
    paused = os.path.exists(PAUSE)
    books = {o["symbol"]: o for o in T.get("orderBookDetails")["order_book_details"] if o["market_type"] == "perp"}
    for sig in sorted(sigs, key=lambda x: (x["bar"], x["sym"])):
        sym, atr, close = sig["sym"], float(sig["atr"]), float(sig["close"])
        age = now - (sig["bar"] + 14400)
        if age > FRESH_S:
            event(s, sig, "SKIP", f"stale signal (bar closed {age/3600:.1f}h ago)"); continue
        if paused:
            why = open(PAUSE).read().strip()[:120] or "paused"
            event(s, sig, "SKIP", f"auto entries paused: {why}"); continue
        m = books.get(sym)
        if not m or m.get("status") != "active":
            event(s, sig, "SKIP", "market not active"); continue
        if float(m.get("daily_quote_token_volume") or 0) < MIN_VOL:
            event(s, sig, "SKIP", "below $5M/day volume" + (" (watchlist name: left to the trade call)"
                                                             if sym in WATCHLIST else "")); continue
        px = float(m["last_trade_price"])
        if px > close + CHASE_UP_ATR * atr:
            event(s, sig, "SKIP", f"chasing: price {px:.6g} > close {close:.6g} + 1 ATR", px=px); continue
        if px < close - CHASE_DOWN_ATR * atr:
            event(s, sig, "SKIP", f"failed breakout: price {px:.6g} < close {close:.6g} - 0.5 ATR", px=px); continue
        try:
            a = T.account()
            open_syms = [p["symbol"] for p in a.get("positions", []) if float(p.get("position") or 0)]
            if sym in open_syms:
                event(s, sig, "SKIP", "already open", px=px); continue
            if len(open_syms) >= T.MAX_OPEN_POSITIONS:
                event(s, sig, "SKIP", f"{len(open_syms)} positions open (cap {T.MAX_OPEN_POSITIONS})", px=px); continue
            equity = float(a.get("total_asset_value") or a["collateral"])
            cur, _, rc = T.open_risk(a); await rc.close()
            usd, sl, tp, loss = size_trade(T, m, px, atr, equity, cur)
        except ValueError as e:
            event(s, sig, "SKIP", str(e), px=px); continue
        plan = f"${usd} notional @ ~{px:.6g}, {LEV}x, SL {sl} TP {tp}, risk ${loss:.2f} ({loss/equity:.1%} of ${equity:.2f})"
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                await T.long(sym, float(usd), LEV, sl, tp, MODE == "live")
        except BaseException as e:      # assert refusals, SystemExit, SDK errors: log, never retry
            sent = "entry:" in out.getvalue()
            if isinstance(e, AssertionError) and not sent:
                event(s, sig, "REFUSED", f"trade.py: {e}", px=px, plan=plan)
            else:                       # entry went out but a follow-up (stop/target) failed: needs eyes now
                event(s, sig, "ERROR", ("ENTRY SENT, FOLLOW-UP FAILED, CHECK STOP: " if sent else "")
                      + f"{type(e).__name__}: {e}"[:300], px=px, plan=plan, output=out.getvalue()[-1500:])
            save(s)
            continue
        finally:
            print(out.getvalue(), end="", flush=True)
        event(s, sig, "ENTER" if MODE == "live" else "DRY", "4h breakout", px=px, plan=plan,
              usd=usd, sl=sl, tp=tp, risk=round(loss, 2), output=out.getvalue()[-1500:])
        save(s)                           # persist after every order so a crash can't re-enter it
    save(s)


if __name__ == "__main__":
    log(f"auto entry start, mode={MODE}")
    try:
        asyncio.run(run())
    except Exception:
        traceback.print_exc(); sys.exit(1)
