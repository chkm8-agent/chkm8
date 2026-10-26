#!/usr/bin/env python3
"""chkm8 paper scanner: 4h Donchian-20 LONG breakout (the only strategy that held up in the
2026-10-08 backtest, see research/bt/README.md). Trades on paper only, never sends orders.

Every run (CronJob, every 15 min):
  1. For each crypto perp with >= $1M/day volume, load 4h candles built from Lighter's 1h candles.
  2. Signal on the last CLOSED 4h bar: close > highest high of the previous 20 bars, close > EMA50,
     EMA50 > EMA200, and Lighter funding not crowded (<= 0.03%/8h, same rule as trade.py longs).
  3. Open a paper position at the current price: stop 2 ATR below, target 4 ATR above, 42-bar (7-day)
     time stop, size = 1.5% of paper equity at risk, max 3 open, one per symbol.
  4. Mark open paper positions to market using 1h highs/lows since the last check (stop first if both).
Costs: 0.07%/side + actual hourly funding, as in the backtest.
State: $CHKM8_DATA/paper_scanner.json   (positions, closed trades, equity, signals log)
"""
import json, os, sys, time, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import chkm8env

API = "https://mainnet.zklighter.elliot.ai/api/v1"
STATE = f"{chkm8env.DATA}/paper_scanner.json"
START_EQUITY = float(chkm8env.env("CHKM8_PAPER_START", "194"))
RISK, MAX_OPEN, COST = 0.015, 5, 0.0007   # max 5 open (operator 2026-10-11)
N, SL_ATR, TP_ATR, MAX_BARS = 20, 2.0, 4.0, 42
MIN_VOL, LONG_MAX_FUNDING = 5e6, 0.0003   # $5M/day floor (2026-10-22; was $1M: MET/ARB/AERO/TIA faked out)
# Operator watchlist: always scanned regardless of the volume floor (e.g. TAO, ~$0.5M/day on Lighter).
WATCHLIST = {s.strip().upper() for s in chkm8env.env("CHKM8_WATCHLIST", "TAO").split(",") if s.strip()}
MIN_ATR_FRAC = 0.01          # ATR floor = 1% of price -> stop at least 2% away
# Stocks / indices / commodities (RWA perps). Scanned too (operator 2026-10-11), but their 4h breakouts only
# count while the US cash session is open (bar overlaps Mon-Fri 13:30-20:00 UTC); weekend/overnight bars are thin.
NONCRYPTO = {"XAU", "XAG", "US100", "US500", "SPY", "QQQ", "WTI", "BRENTOIL", "NATGAS", "NVDA", "AAPL", "MU", "GOOGL",
             "AMZN", "INTC", "META", "TSLA", "AMD", "MSFT", "NBIS", "HOOD", "COIN", "CRCL", "IBM", "SNDK",
             "SOXL", "ORCL", "PLTR", "MSTR", "NFLX", "AVGO", "TSM", "PONS", "IWM", "DIA", "GLD", "SLV", "TLT", "USO",
             "MRNA", "SKHY", "COPPER", "PLATINUM", "EURUSD", "USDJPY", "GBPUSD"}


def us_session_open(bar_start):
    """True if the 4h bar [bar_start, +4h) overlaps the US cash session (Mon-Fri 13:30-20:00 UTC)."""
    import datetime as _dt
    for k in range(0, 4 * 3600, 900):
        d = _dt.datetime.fromtimestamp(bar_start + k, _dt.timezone.utc)
        if d.weekday() < 5 and (13 * 60 + 30) <= d.hour * 60 + d.minute < 20 * 60:
            return True
    return False
log = chkm8env.logger("paper", "paper_scanner.log")


def get(p, tries=4):
    for i in range(tries):
        try:
            with urllib.request.urlopen(f"{API}/{p}", timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            return json.loads(e.read() or b"{}")
        except Exception:
            if i == tries - 1: raise
            time.sleep(3 * (i + 1))


def ema(x, n):
    k = 2 / (n + 1); out = [x[0]]
    for v in x[1:]: out.append(v * k + out[-1] * (1 - k))
    return out


def bars_4h(mid, now):
    """Closed 4h bars from Lighter's native 4h candles (UTC-aligned), plus the last 500 1h candles
    for stop/target marking. One request returns at most 500 candles."""
    d = get(f"candles?market_id={mid}&resolution=4h&start_timestamp={now - 300 * 14400}&end_timestamp={now}&count_back=300")
    bars = [b for b in sorted(d.get("c", []), key=lambda b: b["t"]) if b["t"] // 1000 + 14400 <= now]  # closed only
    t = [b["t"] // 1000 for b in bars]; o = [b["o"] for b in bars]
    h = [b["h"] for b in bars]; l = [b["l"] for b in bars]; c = [b["c"] for b in bars]
    d1 = get(f"candles?market_id={mid}&resolution=1h&start_timestamp={now - 500 * 3600}&end_timestamp={now}&count_back=500")
    cs = sorted(d1.get("c", []), key=lambda x: x["t"])
    return t, o, h, l, c, cs


def atr(h, l, c, n=14):
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(c))]
    return ema(tr, n)


def load():
    try: return json.load(open(STATE))
    except FileNotFoundError:
        return {"equity": START_EQUITY, "start": START_EQUITY, "started": int(time.time()), "open": {}, "closed": [],
                "signals": [], "last_bar": {}}


def save(s):
    os.makedirs(chkm8env.DATA, exist_ok=True)
    json.dump(s, open(STATE + ".tmp", "w"), indent=1); os.replace(STATE + ".tmp", STATE)


def mark(s, now, candles_by_mid, funding):
    """Walk 1h candles since each position's last check: stop/target/time exits; accrue funding."""
    for sym, p in list(s["open"].items()):
        cs = [c for c in candles_by_mid.get(p["market_id"], []) if p["checked"] <= c["t"] // 1000 and c["t"] // 1000 + 3600 <= now]
        exit_px = reason = None
        for c in cs:
            p["funding"] += funding.get(sym, 0.0) / 8          # 8h-normalised rate -> per hour (approx.)
            if c["l"] <= p["sl"]: exit_px, reason = p["sl"], "stop"; break
            if c["h"] >= p["tp"]: exit_px, reason = p["tp"], "target"; break
            p["checked"] = c["t"] // 1000 + 3600
        if exit_px is None and now - p["opened"] >= MAX_BARS * 14400:
            exit_px, reason = cs[-1]["c"] if cs else p["entry"], "time"
        if exit_px is not None:
            gross = (exit_px - p["entry"]) / p["entry"]
            net = gross - 2 * COST - p["funding"]
            pnl = net * p["notional"]
            R = pnl / p["risk_usd"]
            s["equity"] += pnl
            s["closed"].append({**p, "sym": sym, "exit": exit_px, "reason": reason, "closed": now, "pnl": pnl, "R": R})
            del s["open"][sym]
            log(f"PAPER CLOSE {sym} {reason} entry {p['entry']:.6g} exit {exit_px:.6g} pnl ${pnl:+.2f} ({R:+.2f}R) equity ${s['equity']:.2f}")


def main():
    now = int(time.time())
    s = load()
    mk = [o for o in get("orderBookDetails")["order_book_details"]
          if o["market_type"] == "perp" and o.get("status") == "active"   # all asset classes (operator 2026-10-11:
          # crypto + stocks/indices/commodities/RWAs); NONCRYPTO is kept only as a label for reports
          and (float(o.get("daily_quote_token_volume") or 0) >= MIN_VOL or o["symbol"] in WATCHLIST)]
    funding = {r["symbol"]: float(r["rate"]) for r in get("funding-rates").get("funding_rates", []) if r["exchange"] == "lighter"}
    candles_by_mid, new = {}, []
    for o in mk:
        sym, mid = o["symbol"], o["market_id"]
        try:
            t, op, h, l, c, cs = bars_4h(mid, now)
        except Exception as e:
            log(f"{sym}: candles failed {e!r}"); continue
        candles_by_mid[mid] = cs
        if len(c) < 210: continue
        i = len(c) - 1
        if s["last_bar"].get(sym) == t[i]: continue           # this bar was already evaluated
        s["last_bar"][sym] = t[i]
        e50, e200, a = ema(c, 50), ema(c, 200), atr(h, l, c)
        hh = max(h[i - N:i])
        if not (c[i] > hh and c[i] > e50[i] and e50[i] > e200[i]): continue
        f = funding.get(sym)
        tradfi = sym in NONCRYPTO
        atr_used = max(a[i], MIN_ATR_FRAC * c[i])   # floor: quiet/closed-session bars make ATR (and stops) tiny
        sig = {"sym": sym, "bar": t[i], "close": c[i], "atr": atr_used, "funding_8h": f, "at": now,
               "asset": "tradfi" if tradfi else "crypto"}
        if tradfi:
            sig["skipped"] = "breakouts are crypto-only"   # 2026-10-22: never backtested on tradfi; 6/16 live losses
        elif f is not None and f > LONG_MAX_FUNDING:
            sig["skipped"] = "crowded funding"
        elif sym in s["open"]:
            sig["skipped"] = "already open"
        elif len(s["open"]) >= MAX_OPEN:
            sig["skipped"] = f"{MAX_OPEN} open"
        else:
            px = float(o["last_trade_price"])
            sl, tp = px - SL_ATR * atr_used, px + TP_ATR * atr_used
            risk_usd = s["equity"] * RISK
            notional = min(risk_usd / ((px - sl) / px), s["equity"])
            s["open"][sym] = {"market_id": mid, "entry": px * (1 + COST * 0), "sl": sl, "tp": tp, "opened": now,
                              "checked": now, "risk_usd": risk_usd, "notional": notional, "funding": 0.0}
            sig["taken"] = {"entry": px, "sl": sl, "tp": tp, "notional": round(notional, 2)}
            log(f"PAPER OPEN {sym} long @ {px:.6g} sl {sl:.6g} tp {tp:.6g} notional ${notional:.2f} risk ${risk_usd:.2f}")
        s["signals"].append(sig); new.append(sig)
    mark(s, now, candles_by_mid, funding)
    s["signals"] = s["signals"][-500:]
    s["updated"] = now
    save(s)
    log(f"scan done: {len(mk)} markets, {len(new)} new signals, {len(s['open'])} open, equity ${s['equity']:.2f}")


if __name__ == "__main__":
    main()
