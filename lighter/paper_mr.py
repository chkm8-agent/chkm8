#!/usr/bin/env python3
"""chkm8 paper bot #2: 15-minute index mean reversion (buy 3-sigma dips). PAPER ONLY: never sends orders,
needs no Lighter key.

Rules = the best stocks/indices variant from research/bt/mr/README.md, unchanged:
  markets   US500 US100 SPY QQQ AAPL, 15m bars
  signal    on a closed bar: close <= SMA20 - 3 x std20, EMA50 >= 0.99 x EMA200 (not in a downtrend),
            bar starts inside the US cash session (Mon-Fri 09:30-16:00 New York time)
  entry     limit at close - 0.5 x ATR14, valid for the next 2 bars; filled only if the low trades THROUGH
            it, at min(open, limit)
  exits     target = SMA20 at signal time (limit); stop = entry - 1.5 x ATR; time stop 48 bars (12h).
            The fill bar checks only the stop; later bars check stop before target. Stop/time exits slip 0.05%.
            Lighter hourly funding is charged/credited over the hold.
  one position per market; size = 1.5% of paper equity at risk, notional capped at 1x equity (the live cap)
Runs as a CronJob every 15 min. Every newly closed bar is replayed in order, so a missed run loses nothing.
State: $CHKM8_DATA/paper_mr.json  (same field names as paper_scanner.json, so the PnL report can show both)
"""
import json, math, os, sys, time, datetime as dt, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import chkm8env

API = "https://mainnet.zklighter.elliot.ai/api/v1"
STATE = f"{chkm8env.DATA}/paper_mr.json"
MARKETS = {"US500": 180, "US100": 181, "SPY": 128, "QQQ": 129, "AAPL": 113}
K, OFF, SLM, MAXB, SLIP = 3.0, 0.5, 1.5, 48, 0.0005
RISK, START_EQUITY, BAR = 0.015, 194.0, 900_000
log = chkm8env.logger("paper_mr", "paper_mr.log")

try:
    from zoneinfo import ZoneInfo
    NY = ZoneInfo("America/New_York")
except Exception:          # no tz database: fall back to EDT (UTC-4)
    NY = dt.timezone(dt.timedelta(hours=-4))


def get(p, tries=4):
    for i in range(tries):
        try:
            with urllib.request.urlopen(f"{API}/{p}", timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429: time.sleep(5 * (i + 1)); continue
            return json.loads(e.read() or b"{}")
        except Exception:
            if i == tries - 1: raise
            time.sleep(3 * (i + 1))
    return {}


def us_session(t_ms):
    d = dt.datetime.fromtimestamp(t_ms / 1000, NY)
    return d.weekday() < 5 and 9 * 60 + 30 <= d.hour * 60 + d.minute < 16 * 60


def ema(x, n):
    k = 2 / (n + 1); out = [x[0]]
    for v in x[1:]: out.append(v * k + out[-1] * (1 - k))
    return out


def indicators(cs):
    """cs: [[t_ms, o, h, l, c], ...] oldest first. Same maths as research/bt/mr_backtest.prep."""
    t = [c[0] for c in cs]; o = [c[1] for c in cs]; h = [c[2] for c in cs]; l = [c[3] for c in cs]; c = [c[4] for c in cs]
    n = len(c); sma, sd = [None] * n, [None] * n
    for i in range(19, n):
        w = c[i - 19:i + 1]; m = sum(w) / 20
        sma[i] = m; sd[i] = math.sqrt(sum((v - m) ** 2 for v in w) / 20)
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, n)]
    return dict(t=t, o=o, h=h, l=l, c=c, sma=sma, sd=sd, atr=ema(tr, 14), e50=ema(c, 50), e200=ema(c, 200))


def new_state():
    return {"equity": START_EQUITY, "start": START_EQUITY, "started": int(time.time()), "open": {}, "pending": {},
            "closed": [], "signals": [], "last_t": {}, "rules": "15m 3-sigma dip, US session, not-downtrend; "
            "limit -0.5 ATR, target SMA20, stop 1.5 ATR, 12h time stop"}


def _close(st, sym, pos, exit_px, reason, exit_t, fund_fn):
    fr = fund_fn(sym, pos["fill_t"], exit_t) / 100           # hourly % -> fraction, + = longs paid
    pnl_frac = exit_px / pos["entry"] - 1 - fr
    R = pnl_frac / pos["risk_frac"]
    pnl = pnl_frac * pos["notional"]
    st["equity"] += pnl
    st["closed"].append({**pos, "sym": sym, "exit": exit_px, "reason": reason, "closed": exit_t // 1000 + BAR // 1000,
                         "pnl": pnl, "R": R, "funding_frac": fr})
    st["open"].pop(sym, None)
    log(f"PAPER-MR CLOSE {sym} {reason} {pos['entry']:.6g} -> {exit_px:.6g}  {R:+.2f}R  ${pnl:+.2f}  equity ${st['equity']:.2f}")


def step(st, sym, m, k, fund_fn):
    """Process closed bar k of market sym. Mirrors research/bt/mr_backtest.simulate bar by bar."""
    t, o, h, l, c = m["t"][k], m["o"][k], m["h"][k], m["l"][k], m["c"][k]
    pos = st["open"].get(sym)
    if pos:                                                   # 1) manage the open position
        if t > pos["fill_t"]:
            pos["held"] += 1
            if l <= pos["sl"]: _close(st, sym, pos, min(pos["sl"], o) * (1 - SLIP), "stop", t, fund_fn)
            elif h > pos["tp"]: _close(st, sym, pos, max(pos["tp"], o), "target", t, fund_fn)
            elif pos["held"] >= MAXB: _close(st, sym, pos, c * (1 - SLIP), "time", t, fund_fn)
        return                                                # bar k is never a signal bar while/when in a trade
    keep, filled = [], False                                  # 2) resting limit orders, oldest first
    for p in sorted(st["pending"].get(sym, []), key=lambda p: p["sig_t"]):
        if filled or t > p["sig_t"] + 2 * BAR: continue      # expired, or superseded by a fill
        if l < p["lim"]:
            entry = min(o, p["lim"])
            if p["tp"] <= entry: continue                     # target already below the fill: cancel
            stop = entry - SLM * p["atr"]; risk_frac = (entry - stop) / entry
            risk_usd = st["equity"] * RISK
            notional = min(risk_usd / risk_frac, st["equity"])
            pos = {"market_id": MARKETS[sym], "entry": entry, "sl": stop, "tp": p["tp"], "fill_t": t, "held": 0,
                   "opened": t // 1000, "risk_frac": risk_frac, "risk_usd": notional * risk_frac,
                   "notional": notional, "sig_t": p["sig_t"]}
            st["open"][sym] = pos; filled = True
            log(f"PAPER-MR OPEN {sym} long @ {entry:.6g} stop {stop:.6g} target {p['tp']:.6g} notional ${notional:.2f}")
            if l <= stop: _close(st, sym, pos, min(stop, o) * (1 - SLIP), "stop", t, fund_fn)
        elif t < p["sig_t"] + 2 * BAR:
            keep.append(p)
    st["pending"][sym] = [] if filled else keep
    if filled: return
    if k < 210 or m["sma"][k] is None or not m["sd"][k]: return   # 3) signal on this bar?
    z = (c - m["sma"][k]) / m["sd"][k]
    if z > -K or m["e50"][k] / m["e200"][k] - 1 < -0.01 or not us_session(t): return
    p = {"sig_t": t, "lim": c - OFF * m["atr"][k], "tp": m["sma"][k], "atr": m["atr"][k], "z": z}
    st["pending"][sym].append(p)
    st["signals"].append({"sym": sym, "bar": t // 1000, "close": c, "z": round(z, 2), "limit": p["lim"],
                          "target": p["tp"], "at": int(time.time())})
    log(f"PAPER-MR SIGNAL {sym} z={z:.2f} close {c:.6g} -> limit {p['lim']:.6g} target {p['tp']:.6g}")


def fetch_15m(mid, now):
    out = {}
    end = now
    for _ in range(2):                                        # 2 x 500 bars: enough for EMA200 to settle
        s = end - 490 * 900
        d = get(f"candles?market_id={mid}&resolution=15m&start_timestamp={s}&end_timestamp={end}&count_back=500")
        for x in d.get("c", []): out[x["t"]] = [x["t"], x["o"], x["h"], x["l"], x["c"]]
        end = s
    return [out[k] for k in sorted(out) if k + BAR <= now * 1000]   # closed bars only


def live_funding(sym, t_from, t_to):
    a, b = t_from // 3600000, t_to // 3600000
    d = get(f"fundings?market_id={MARKETS[sym]}&resolution=1h&start_timestamp={a * 3600 - 3600}"
            f"&end_timestamp={b * 3600 + 3600}&count_back=100")
    tot = 0.0
    for f in d.get("fundings", []):
        hh = int(f["timestamp"]) // 3600
        if a <= hh <= b: tot += float(f["rate"]) * (1 if f.get("direction", "long") == "long" else -1)
    return tot


def main():
    now = int(time.time())
    try: st = json.load(open(STATE))
    except (FileNotFoundError, ValueError): st = new_state()
    for sym, mid in MARKETS.items():
        try:
            cs = fetch_15m(mid, now)
        except Exception as e:
            log(f"{sym}: candles failed {e!r}"); continue
        if len(cs) < 260: log(f"{sym}: only {len(cs)} bars"); continue
        m = indicators(cs)
        last = st["last_t"].get(sym)
        if last is None:                                     # first run: start from now, don't replay history
            st["last_t"][sym] = m["t"][-1]; st["pending"].setdefault(sym, []); continue
        for k in range(len(m["t"])):
            if m["t"][k] > last:
                step(st, sym, m, k, live_funding)
                st["last_t"][sym] = m["t"][k]
    st["signals"] = st["signals"][-300:]; st["updated"] = now
    os.makedirs(chkm8env.DATA, exist_ok=True)
    json.dump(st, open(STATE + ".tmp", "w"), indent=1); os.replace(STATE + ".tmp", STATE)
    n = len(st["closed"]); w = sum(1 for x in st["closed"] if x["R"] > 0)
    log(f"run done: open {list(st['open'])}, pending {sum(len(v) for v in st['pending'].values())}, "
        f"closed {n} ({w} wins), equity ${st['equity']:.2f}")


if __name__ == "__main__":
    main()
