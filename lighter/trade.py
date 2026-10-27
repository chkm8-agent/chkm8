#!/usr/bin/env python3
"""chkm8 Lighter trading helper.
  trade.py status                         # balances, positions, open orders
  trade.py long  LIT <usd_notional> --lev 2 --sl 3.60 --tp 4.60 [--send]
  trade.py close LIT [--send]             # reduce-only market close + cancel all
Without --send everything is a dry run. Hard caps below cannot be overridden from CLI."""
import asyncio, json, os, sys, time, argparse, urllib.request
sys.path.insert(0, __file__.rsplit("/lighter/", 1)[0] + "/wallet")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lighter
import chkm8env

BASE = "https://mainnet.zklighter.elliot.ai"
ACC = chkm8env.account()
IDX, KIDX = ACC["account_index"], ACC["api_key_index"]

# ---- HARD RISK CAPS (operator, 2026-10-08: no account kill switch; these are the only brakes) ----
MAX_LEVERAGE = 3
MAX_NOTIONAL_FRAC = 1.0      # notional <= 1.0x collateral (i.e. effective leverage ~1x on account)
MAX_LOSS_USD = float(os.environ.get("CHKM8_MAX_LOSS_USD", "25"))   # operator 2026-10-11: hard ceiling on
# the loss at the stop, in USD (was 1.5% of equity, ~$3, which blocked min-size entries like STRK). The bots
# still SIZE trades at ~1.4% of equity; this ceiling only stops a single trade from risking more than $25.
MAX_OPEN_POSITIONS = 5       # operator 2026-10-11 (was 3)
MAX_TOTAL_RISK_USD = float(os.environ.get("CHKM8_MAX_TOTAL_RISK_USD", "20"))  # operator 2026-10-11: sum of
# (entry - stop) x size over ALL open positions + the new trade must stay <= $20. A position with no resting
# stop-loss counts as MAX_LOSS_USD (so a missing stop blocks new entries until it's fixed).
REQUIRE_SL = True

# ---- FUNDING RULE (operator 2026-10-26: funding direction doesn't matter; only block when it's a real cost) ----
# Rates are Lighter's /funding-rates values (8h-normalised; 0.0001 = 0.01% per 8h, the usual baseline).
# Old rules (removed): shorts only when longs pay; longs refused above 0.03%/8h.
# New rule, same for longs and shorts: the side we're on may pay funding, but the expected funding cost over the
# hold must stay <= FUNDING_MAX_STOP_FRAC of the stop distance. At normal rates this never triggers; it blocks
# extreme cases (e.g. NATGAS at 0.5%/8h costs ~1.5%/day, more than a 2% stop over a few days).
FUNDING_HOLD_H = float(os.environ.get("CHKM8_FUNDING_HOLD_H", "72"))       # expected hold (breakouts ~3 days)
FUNDING_MAX_STOP_FRAC = float(os.environ.get("CHKM8_FUNDING_MAX_STOP_FRAC", "0.25"))

def funding(sym):
    """Current Lighter funding rate for sym (positive = longs pay shorts), plus Binance for context."""
    rates = get("funding-rates").get("funding_rates", [])
    pick = lambda ex: next((float(r["rate"]) for r in rates if r["symbol"] == sym and r["exchange"] == ex), None)
    return pick("lighter"), pick("binance")

def check_funding(sym, short, px, sl, hold_h=None):
    """Refuse only if the funding we'd PAY over the expected hold exceeds a quarter of the stop distance."""
    hold_h = hold_h or FUNDING_HOLD_H
    lt, bn = funding(sym)
    lt = lt or 0.0                                   # no rate published -> treat as zero cost
    ctx = f"Lighter funding {lt*100:+.4f}%/8h" + (f" (Binance {bn*100:+.4f}%)" if bn is not None else "")
    paid_per_8h = -lt if short else lt               # > 0: our side pays; < 0: our side is paid
    cost = max(0.0, paid_per_8h) * hold_h / 8        # fraction of notional over the hold
    stop_frac = abs(px - sl) / px
    ctx += f"; est. funding over {hold_h:.0f}h {('-' if paid_per_8h > 0 else '+')}{abs(paid_per_8h) * hold_h / 8 * 100:.3f}% vs stop {stop_frac*100:.2f}%"
    assert cost <= FUNDING_MAX_STOP_FRAC * stop_frac, \
        f"{'SHORT' if short else 'LONG'} refused: funding would cost {cost*100:.2f}% over {hold_h:.0f}h, " \
        f"> {FUNDING_MAX_STOP_FRAC:.0%} of the {stop_frac*100:.2f}% stop ({ctx})"
    return ctx

def get(path):
    try:
        return json.load(urllib.request.urlopen(f"{BASE}/api/v1/{path}", timeout=15))
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or b"{}")

def market(sym):
    for o in get("orderBookDetails")["order_book_details"]:
        if o["symbol"] == sym and o["market_type"] == "perp":
            return o
    raise SystemExit(f"no perp market {sym}")

def account():
    return get(f"account?by=index&value={IDX}")["accounts"][0]

def status():
    a = account()
    print(f"account {IDX}  collateral {a.get('collateral')}  available {a.get('available_balance')}  "
          f"total_asset_value {a.get('total_asset_value')}")
    for p in a.get("positions", []):
        if float(p.get("position", 0) or 0) != 0:
            print(f"  {p['symbol']:6} size {p['position']} sign {p['sign']} entry {p['avg_entry_price']} "
                  f"value {p['position_value']} uPnL {p['unrealized_pnl']} liq {p.get('liquidation_price')}")
    return a

def client():
    # Order-placing is only allowed on the designated trading host (the EU server), never on the Mac.
    # The marker file is baked into the server's pod spec only (CHKM8_TRADING_HOST_FILE), never created
    # on the Mac.
    marker = chkm8env.env("CHKM8_TRADING_HOST_FILE", os.path.expanduser("~/.chkm8/trading_host"))
    if not os.path.exists(marker):
        raise SystemExit("refusing to sign Lighter transactions: this machine is not the designated trading host. "
                         "Run trades from the EU server.")
    k = chkm8env.lighter_api_key(IDX, KIDX)
    return lighter.SignerClient(url=BASE, account_index=IDX, api_private_keys={KIDX: k})

def open_risk(a):
    """$ lost if every open position hit its resting stop-loss now. Needs an auth token -> trading host only."""
    c = client(); tok, _ = c.create_auth_token_with_expiry()
    total, parts = 0.0, []
    for p in a.get("positions", []):
        size = float(p.get("position") or 0)
        if not size: continue
        entry, sign = float(p["avg_entry_price"]), int(p["sign"])
        sl = None
        for o in get(f"accountActiveOrders?account_index={IDX}&market_id={p['market_id']}&auth={tok}").get("orders", []):
            if o.get("reduce_only") and o["type"].startswith("stop-loss"):
                sl = float(o.get("trigger_price") or 0) or None
        r = MAX_LOSS_USD if sl is None else max(0.0, (entry - sl) * sign * abs(size))
        parts.append(f"{p['symbol']} ${r:.2f}" + (" (NO STOP)" if sl is None else ""))
        total += r
    return total, parts, c

async def long(sym, usd, lev, sl, tp, send, short=False):
    m = market(sym); a = account()
    open_syms = [p["symbol"] for p in a.get("positions", []) if float(p.get("position") or 0)]
    assert sym not in open_syms, f"already have a {sym} position; no stacking"
    assert len(open_syms) < MAX_OPEN_POSITIONS, f"{len(open_syms)} positions open ({', '.join(open_syms)}); cap {MAX_OPEN_POSITIONS}"
    equity = float(a.get("total_asset_value") or a["collateral"])
    coll = float(a["collateral"]); px = float(m["last_trade_price"])
    sd, pd = m["size_decimals"], m["price_decimals"]
    size = round(usd / px, sd)
    max_lev = 10000 / m["min_initial_margin_fraction"]
    assert lev <= min(MAX_LEVERAGE, max_lev), f"lev {lev} > cap {min(MAX_LEVERAGE, max_lev)}"
    assert usd <= equity * MAX_NOTIONAL_FRAC, f"notional ${usd} > cap ${equity*MAX_NOTIONAL_FRAC:.2f}"
    assert usd >= float(m["min_quote_amount"]) and size >= float(m["min_base_amount"]), "below market minimum"
    d = -1 if short else 1          # direction
    if REQUIRE_SL: assert sl and (sl - px) * d < 0, "stop-loss required and must be on the losing side"
    if tp: assert (tp - px) * d > 0, "take-profit must be on the winning side"
    loss = abs(px - sl) * size
    assert loss <= MAX_LOSS_USD, f"SL risk ${loss:.2f} > ${MAX_LOSS_USD:.0f} cap"
    fund = check_funding(sym, short, px, sl)
    cur, parts, rc = open_risk(a); await rc.close()
    assert cur + loss <= MAX_TOTAL_RISK_USD, \
        f"total risk ${cur:.2f} open [{', '.join(parts)}] + ${loss:.2f} new > ${MAX_TOTAL_RISK_USD:.0f} cap"
    mmf = m["maintenance_margin_fraction"] / 10000
    liq_est = px * (1 - 1/lev) / (1 - mmf) if not short else px * (1 + 1/lev) / (1 + mmf)
    print(f"PLAN {sym} {'SHORT' if short else 'LONG'} {size} @ ~{px} = ${size*px:.2f} notional, {lev}x isolated (margin ~${size*px/lev:.2f})")
    print(f"  SL {sl} (risk ${loss:.2f} = {loss/equity:.1%} of equity ${equity:.2f})  TP {tp or '-'}"
          + (f"  (+${abs(tp-px)*size:.2f})" if tp else ""))
    print(f"  total risk at stops: ${cur:.2f} open + ${loss:.2f} new = ${cur+loss:.2f} / ${MAX_TOTAL_RISK_USD:.0f} cap "
          f"[{', '.join(parts) or 'none open'}]")
    print(f"  est. liquidation ~{liq_est:.5g} (SL fires first: {(sl - liq_est) * d > 0})")
    print(f"  funding OK: {fund}")
    if not send: print("dry run — add --send"); return
    c = client(); mid = m["market_id"]
    _, _, err = await c.update_leverage(mid, c.ISOLATED_MARGIN_MODE, lev); assert err is None, err
    base = int(round(size * 10**sd)); ideal = int(round(px * 10**pd))
    tx, resp, err = await c.create_market_order_limited_slippage(mid, int(time.time()) % 2**31, base, 0.01, short, ideal_price=ideal)
    assert err is None, err; print("entry:", resp.code, resp.message or "")
    await asyncio.sleep(3)
    exit_ask = not short
    slp = int(round(sl * 10**pd)); sl_lim = int(slp * (0.98 if not short else 1.02))
    _, r, err = await c.create_sl_order(mid, int(time.time()*10) % 2**31, base, slp, sl_lim, exit_ask, True)
    assert err is None, err; print("stop-loss:", r.code)
    if tp:
        tpp = int(round(tp * 10**pd)); tp_lim = int(tpp * (0.98 if not short else 1.02))
        _, r, err = await c.create_tp_order(mid, int(time.time()*10+1) % 2**31, base, tpp, tp_lim, exit_ask, True)
        assert err is None, err; print("take-profit:", r.code)
    await c.close(); await asyncio.sleep(3); status()

async def close(sym, send):
    m = market(sym); a = account()
    pos = next((p for p in a.get("positions", []) if p["symbol"] == sym and float(p["position"]) != 0), None)
    if not pos: print("no position"); return
    size = float(pos["position"]); is_long = int(pos["sign"]) == 1
    print(f"CLOSE {sym} {'long' if is_long else 'short'} {size}")
    if not send: print("dry run — add --send"); return
    c = client(); sd, pd = m["size_decimals"], m["price_decimals"]
    # Cancel only THIS market's resting orders. cancel_all_orders is account-wide and
    # used to strip the SL/TP off every other open position (STRK, 2026-10-09).
    tok, _ = c.create_auth_token_with_expiry()
    for o in get(f"accountActiveOrders?account_index={IDX}&market_id={m['market_id']}&auth={tok}").get("orders", []):
        _, _, err = await c.cancel_order(market_index=m["market_id"], order_index=int(o["order_index"]))
        print("cancel", o.get("type"), o.get("trigger_price"), "ok" if err is None else err)
    tx, r, err = await c.create_market_order_limited_slippage(
        m["market_id"], int(time.time()) % 2**31, int(round(size*10**sd)), 0.02, is_long,
        reduce_only=True, ideal_price=int(round(float(m["last_trade_price"])*10**pd)))
    assert err is None, err; print("close:", r.code); await c.close(); await asyncio.sleep(3); status()

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("cmd"); ap.add_argument("sym", nargs="?")
    ap.add_argument("usd", nargs="?", type=float); ap.add_argument("--lev", type=int, default=2)
    ap.add_argument("--sl", type=float); ap.add_argument("--tp", type=float); ap.add_argument("--send", action="store_true")
    a = ap.parse_args()
    if a.cmd == "status": status()
    elif a.cmd == "long": asyncio.run(long(a.sym, a.usd, a.lev, a.sl, a.tp, a.send))
    elif a.cmd == "short": asyncio.run(long(a.sym, a.usd, a.lev, a.sl, a.tp, a.send, short=True))
    elif a.cmd == "close": asyncio.run(close(a.sym, a.send))
