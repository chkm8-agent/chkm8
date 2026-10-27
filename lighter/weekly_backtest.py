#!/usr/bin/env python3
"""chkm8 weekly backtest (operator 2026-10-27): a REPORT, never a gate. Backtests don't predict the future; the desk
trades on live signals and sentiment inside the code caps. This re-runs both paper strategies on fresh Lighter data
every Monday 05:30 PT and writes $CHKM8_DATA/weekly_backtest.json, which the 07:00 PnL email shows.

  breakout  the paper scanner's rules (lighter/paper_scanner.py): crypto perps >= $5M/day (+ watchlist), 4h bars,
            close > prior 20-bar high, close > EMA50 > EMA200, skip if ~72h of funding > 25% of the stop, entry next bar open
            + 0.05% slippage, stop 2 ATR / target 4 ATR (ATR floor 1% of price), 42-bar time stop, actual funding.
            Stop is checked before target inside a bar (pessimistic). One trade per market at a time; no max-open cap.
  index dip paper bot 2's rules, replayed bar by bar through lighter/paper_mr.step() itself (same code as the bot).
Windows: whole sample (~120 days breakout / ~60 days index), last 30 days, last 7 days, plus R by month.
Regime context: share of crypto perps above their 4h EMA200, BTC vs its EMA200.
Public market data only: no secrets, never trades.
"""
import collections
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chkm8env
import paper_mr
import paper_scanner as ps

API = "https://mainnet.zklighter.elliot.ai/api/v1"
OUT = f"{chkm8env.DATA}/weekly_backtest.json"
H4, H1 = 14400, 3600
DAYS_BO, DAYS_MR, WARM = 120, 60, 210
SLIP = 0.0005
log = chkm8env.logger("weekly_bt", "weekly_backtest.log")
paper_mr.log = lambda *a, **k: None      # replay must not write OPEN/CLOSE lines into the live bot's log


def get(p, tries=5):
    for i in range(tries):
        try:
            with urllib.request.urlopen(f"{API}/{p}", timeout=40) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(5 * (i + 1)); continue
            return json.loads(e.read() or b"{}")
        except Exception:
            if i == tries - 1: raise
            time.sleep(3 * (i + 1))
    return {}


def candles(mid, res, secs, start, now):
    out, end = {}, now
    while end > start:
        s = max(start, end - 490 * secs)
        cs = get(f"candles?market_id={mid}&resolution={res}&start_timestamp={s}&end_timestamp={end}&count_back=500").get("c", [])
        if not cs: break
        for c in cs: out[c["t"]] = [c["t"], c["o"], c["h"], c["l"], c["c"]]
        end = s; time.sleep(0.1)
    return [out[k] for k in sorted(out) if k // 1000 + secs <= now]      # closed bars only


def fundings(mid, start, now):
    """{hour index: rate in % per hour, + = longs pay}"""
    out, end = {}, now
    while end > start:
        s = max(start, end - 490 * H1)
        fs = get(f"fundings?market_id={mid}&resolution=1h&start_timestamp={s}&end_timestamp={end}&count_back=500").get("fundings", [])
        if not fs: break
        for f in fs:
            ts = int(f["timestamp"]); ts = ts // 1000 if ts > 1e12 else ts
            out[ts // H1] = float(f["rate"]) * (1 if f.get("direction", "long") == "long" else -1)
        end = s; time.sleep(0.1)
    return out


def stats(trades, since=None):
    R = [t["R"] for t in trades if since is None or t["t"] >= since]
    if not R: return {"n": 0, "win": 0, "avgR": 0.0, "sumR": 0.0}
    return {"n": len(R), "win": round(100 * sum(r > 0 for r in R) / len(R)), "avgR": round(sum(R) / len(R), 3),
            "sumR": round(sum(R), 2)}


def by_month(trades):
    m = collections.defaultdict(float)
    for t in trades: m[dt.datetime.fromtimestamp(t["t"], dt.timezone.utc).strftime("%b")] += t["R"]
    order = sorted({(dt.datetime.fromtimestamp(t["t"], dt.timezone.utc).strftime("%Y%m"),
                     dt.datetime.fromtimestamp(t["t"], dt.timezone.utc).strftime("%b")) for t in trades})
    return [[name, round(m[name], 2)] for _, name in order]


def breakout(now):
    mk = [o for o in get("orderBookDetails")["order_book_details"]
          if o["market_type"] == "perp" and o.get("status") == "active" and o["symbol"] not in ps.NONCRYPTO
          and (float(o.get("daily_quote_token_volume") or 0) >= ps.MIN_VOL or o["symbol"] in ps.WATCHLIST)]
    start = now - (DAYS_BO * 86400 + (WARM + 10) * H4)
    first_t = now - DAYS_BO * 86400
    trades, above, btc = [], [0, 0], None
    for o in mk:
        sym, mid = o["symbol"], o["market_id"]
        try:
            cs = candles(mid, "4h", H4, start, now)
        except Exception as e:
            log(f"{sym}: candles failed {e!r}"); continue
        if len(cs) < WARM + 20: continue
        t = [c[0] // 1000 for c in cs]; op = [c[1] for c in cs]; h = [c[2] for c in cs]; l = [c[3] for c in cs]
        c = [c[4] for c in cs]
        e50, e200, a = ps.ema(c, 50), ps.ema(c, 200), ps.atr(h, l, c)
        above[1] += 1; above[0] += c[-1] > e200[-1]
        if sym == "BTC": btc = {"close": c[-1], "ema200": round(e200[-1], 2), "above": c[-1] > e200[-1]}
        sigs, i, n = [], WARM, len(c)
        while i < n - 1:
            if t[i] >= first_t and c[i] > max(h[i - ps.N:i]) and c[i] > e50[i] > e200[i]:
                sigs.append(i)
            i += 1
        if not sigs: continue
        fu = fundings(mid, t[sigs[0]] - 9 * H1, now)
        busy_until = -1
        for i in sigs:
            if i <= busy_until: continue
            au = max(a[i], ps.MIN_ATR_FRAC * c[i])
            f8 = sum(fu.get(t[i] // H1 + 3 - k, 0.0) for k in range(8)) / 100      # funding paid over the last 8h
            if max(0.0, f8) * ps.FUNDING_HOLD_H / 8 > ps.FUNDING_MAX_STOP_FRAC * (ps.SL_ATR * au / c[i]):
                continue                                   # same rule as the scanner + trade.py (2026-10-26)
            j = i + 1; entry = op[j] * (1 + SLIP)
            sl, tp = entry - ps.SL_ATR * au, entry + ps.TP_ATR * au
            ex = reason = None; k = j
            for k in range(j, min(n, j + ps.MAX_BARS)):
                if l[k] <= sl: ex, reason = min(sl, op[k]) * (1 - SLIP), "stop"; break
                if h[k] >= tp: ex, reason = max(tp, op[k]), "target"; break
            if ex is None:
                k = min(n - 1, j + ps.MAX_BARS - 1)
                if k < j + ps.MAX_BARS - 1 and k == n - 1: break          # still open at the end of the data: skip
                ex, reason = c[k] * (1 - SLIP), "time"
            fr = sum(fu.get(hh, 0.0) for hh in range(t[j] // H1, (t[k] + H4) // H1)) / 100
            R = (ex / entry - 1 - fr) / ((entry - sl) / entry)
            trades.append({"t": t[j], "sym": sym, "reason": reason, "R": round(R, 3)})
            busy_until = k
    trades.sort(key=lambda x: x["t"])
    return trades, len(mk), above, btc


def index_dip(now):
    start = now - (DAYS_MR * 86400 + (WARM + 10) * 900)
    first_t = (now - DAYS_MR * 86400) * 1000
    trades = []
    for sym, mid in paper_mr.MARKETS.items():
        cs = candles(mid, "15m", 900, start, now)
        if len(cs) < WARM + 50: log(f"{sym}: only {len(cs)} 15m bars"); continue
        fu = fundings(mid, start, now)
        fund = lambda s, a, b, fu=fu: sum(fu.get(hh, 0.0) for hh in range(a // 3600000, b // 3600000 + 1))
        m = paper_mr.indicators(cs)
        st = paper_mr.new_state(); st["pending"][sym] = []
        for k in range(len(m["t"])):
            if m["t"][k] < first_t and not st["open"].get(sym): continue      # warm-up bars: indicators only
            paper_mr.step(st, sym, m, k, fund)
        trades += [{"t": x["fill_t"] // 1000, "sym": sym, "reason": x["reason"], "R": round(x["R"], 3)} for x in st["closed"]]
    trades.sort(key=lambda x: x["t"])
    return trades


def paper_live(path, since):
    try: cl = json.load(open(path)).get("closed", [])
    except (FileNotFoundError, ValueError): return None
    return stats([{"t": x["closed"], "R": x["R"]} for x in cl], since)


def main():
    now = int(time.time()); t0 = time.time()
    d30, d7 = now - 30 * 86400, now - 7 * 86400
    bo, n_mk, above, btc = breakout(now)
    mr = index_dip(now)
    res = {
        "generated": now, "runtime_s": round(time.time() - t0),
        "note": "report only: backtests inform the desk, they don't run it",
        "breakout": {"markets": n_mk, "days": DAYS_BO, "all": stats(bo), "d30": stats(bo, d30), "d7": stats(bo, d7),
                     "months": by_month(bo), "paper_d30": paper_live(ps.STATE, d30), "trades": bo[-60:]},
        "index_dip": {"markets": list(paper_mr.MARKETS), "days": DAYS_MR, "all": stats(mr), "d30": stats(mr, d30),
                      "d7": stats(mr, d7), "months": by_month(mr), "paper_d30": paper_live(paper_mr.STATE, d30),
                      "trades": mr[-60:]},
        "regime": {"breadth_ema200": round(100 * above[0] / above[1]) if above[1] else None, "btc": btc},
    }
    os.makedirs(chkm8env.DATA, exist_ok=True)
    json.dump(res, open(OUT + ".tmp", "w"), indent=1); os.replace(OUT + ".tmp", OUT)
    b, i = res["breakout"], res["index_dip"]
    log(f"done in {res['runtime_s']}s: breakout {b['all']} 30d {b['d30']} | index dip {i['all']} 30d {i['d30']} | "
        f"breadth {res['regime']['breadth_ema200']}%")
    print(json.dumps({k: v for k, v in res.items() if k not in ("breakout", "index_dip")}))
    for name in ("breakout", "index_dip"):
        print(name, json.dumps({k: v for k, v in res[name].items() if k != "trades"}))


if __name__ == "__main__":
    main()
