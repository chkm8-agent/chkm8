#!/usr/bin/env python3
"""Daily PnL report for chkm8's Lighter account -> email via Mail.app.
Mail.app scripting can't reliably send real HTML, so the report is rendered as an HTML page,
screenshotted to PNG with headless Brave, and attached (Gmail shows it inline).
A short plain-text summary goes in the body as a fallback.
  report.py            # build + send
  report.py --dry      # build HTML/PNG only, don't send
Snapshots kept in ~/chkm8/lighter/equity_snapshots.json for day-over-day change."""
import json, os, shutil, subprocess, sys, time, html, urllib.request, urllib.error
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import chkm8env

HOME = chkm8env.HOME
API = "https://mainnet.zklighter.elliot.ai/api/v1"
ACC = chkm8env.account()
IDX = ACC["account_index"]
NET_DEPOSITS = float(chkm8env.env("CHKM8_NET_DEPOSITS", "211.897312"))   # update if funds are added/withdrawn
TO = [a.strip() for a in chkm8env.env("CHKM8_REPORT_TO", "<redacted:agent-email>,<redacted:owner-email>").split(",")]
FROM = "<redacted:agent-email>"
SNAP = f"{chkm8env.DATA}/equity_snapshots.json"
STATE = f"{chkm8env.DATA}/monitor_state.json"
PAPER = f"{chkm8env.DATA}/paper_scanner.json"
PAPER_MR = f"{chkm8env.DATA}/paper_mr.json"   # 15m index dip-buying paper bot (2026-10-16)
OUT = chkm8env.REPORTS
TZ = chkm8env.env("TZ", "America/Los_Angeles")


def browser():
    """Headless browser for the PNG: $CHKM8_BROWSER, else Chromium in a container, else Brave on the Mac."""
    for b in (chkm8env.env("CHKM8_BROWSER"), shutil.which("chromium"), shutil.which("chromium-browser"),
              "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"):
        if b and os.path.exists(b):
            return b
    raise SystemExit("no headless browser found (set CHKM8_BROWSER)")

def get(p, tries=5):
    for i in range(tries):
        try:
            return json.load(urllib.request.urlopen(f"{API}/{p}", timeout=30))
        except urllib.error.HTTPError as e:
            return json.loads(e.read() or b"{}")
        except Exception:
            if i == tries - 1: raise
            time.sleep(5 * (i + 1))

def money(x):
    if abs(x) < 0.005: return "$0.00"
    return f"{'+' if x >= 0 else '−'}${abs(x):,.2f}"
def pct(x): return f"{'+' if x >= 0 else '−'}{abs(x):.2f}%"
def cls(x): return "pos" if x >= 0.005 else "neg" if x <= -0.005 else "flat"

def collect():
    a = get(f"account?by=index&value={IDX}")["accounts"][0]
    mk = {o["symbol"]: o for o in get("orderBookDetails")["order_book_details"] if o["market_type"] == "perp"}
    snaps = json.load(open(SNAP)) if os.path.exists(SNAP) else []
    try: hist = json.load(open(STATE)).get("history", [])
    except FileNotFoundError: hist = []
    now = time.time()
    equity = float(a["total_asset_value"]); free = float(a["available_balance"])
    prev = next((s for s in reversed(snaps) if now - s["t"] >= 20 * 3600), None)
    pos = []
    for p in a["positions"]:
        if not float(p["position"] or 0): continue
        sym = p["symbol"]; m = mk.get(sym, {})
        size = float(p["position"]); entry = float(p["avg_entry_price"]); px = float(m.get("last_trade_price", 0))
        imf = float(p["initial_margin_fraction"] or 0)
        upnl = float(p["unrealized_pnl"])
        pos.append(dict(sym=sym, side="LONG" if int(p["sign"]) == 1 else "SHORT", size=size, entry=entry, px=px,
                        ch24=float(m.get("daily_price_change", 0)), notional=size * px,
                        lev=100 / imf if imf else 0, margin=float(p["allocated_margin"]),
                        upnl=upnl, upct=upnl / (size * entry) * 100, funding=-float(p.get("total_funding_paid_out") or 0),
                        liq=float(p["liquidation_price"] or 0)))
    closed = [h for h in hist if now * 1000 - h["closed_ms"] < 24 * 3600 * 1000]
    wins = sum(1 for h in hist if h["pnl"] > 0)
    try: paper = json.load(open(PAPER))
    except (FileNotFoundError, ValueError): paper = None
    try: paper_mr = json.load(open(PAPER_MR))
    except (FileNotFoundError, ValueError): paper_mr = None
    return dict(now=now, equity=equity, free=free, prev=prev, pos=pos, closed=closed, hist=hist, wins=wins,
                total=equity - NET_DEPOSITS, snaps=snaps, paper=paper, paper_mr=paper_mr)

CSS = """
*{box-sizing:border-box} body{margin:0;background:#0d1117;font-family:-apple-system,'SF Pro Text',Helvetica,Arial,sans-serif;color:#e6edf3}
.wrap{width:860px;padding:28px}
h1{font-size:22px;margin:0 0 2px} .sub{color:#8b949e;font-size:13px;margin-bottom:20px}
.cards{display:flex;gap:12px;margin-bottom:22px}
.card{flex:1;background:#161b22;border:1px solid #30363d;border-radius:10px;padding:14px 16px}
.card .l{color:#8b949e;font-size:11px;text-transform:uppercase;letter-spacing:.06em}
.card .v{font-size:22px;font-weight:600;margin-top:4px} .card .s{font-size:12px;margin-top:2px;color:#8b949e}
h2{font-size:13px;color:#8b949e;text-transform:uppercase;letter-spacing:.06em;margin:20px 0 8px}
table{width:100%;border-collapse:separate;border-spacing:0;background:#161b22;border:1px solid #30363d;border-radius:10px;overflow:hidden;font-size:13px}
th{text-align:right;color:#8b949e;font-weight:500;padding:9px 10px;border-bottom:1px solid #30363d;background:#11161d;white-space:nowrap}
td{text-align:right;padding:9px 10px;border-bottom:1px solid #21262d;font-variant-numeric:tabular-nums;white-space:nowrap}
th:first-child,td:first-child{text-align:left} tr:last-child td{border-bottom:none}
.pos{color:#3fb950} .neg{color:#f85149} .flat{color:#8b949e}
.tag{display:inline-block;padding:1px 7px;border-radius:5px;font-size:11px;font-weight:600;margin-left:4px}
.tag.LONG{background:#12361f;color:#3fb950} .tag.SHORT{background:#3d1416;color:#f85149}
.small{font-size:11px} .empty{color:#8b949e;text-align:center!important;padding:14px}
.foot{color:#6e7681;font-size:11px;margin-top:18px}
"""

def render(d):
    e = html.escape
    d24 = (d["equity"] - d["prev"]["equity"]) if d["prev"] else None
    if d24 is not None:
        c24 = f'<div class="v {cls(d24)}">{money(d24)}</div><div class="s {cls(d24)}">{pct(d24 / d["prev"]["equity"] * 100)}</div>'
    else:
        c24 = '<div class="v flat">n/a</div><div class="s">first report</div>'
    cards = f"""<div class="cards">
 <div class="card"><div class="l">Equity</div><div class="v">${d['equity']:,.2f}</div><div class="s">deposits ${NET_DEPOSITS:,.2f}</div></div>
 <div class="card"><div class="l">All-time PnL</div><div class="v {cls(d['total'])}">{money(d['total'])}</div><div class="s {cls(d['total'])}">{pct(d['total'] / NET_DEPOSITS * 100)}</div></div>
 <div class="card"><div class="l">Last 24h</div>{c24}</div>
 <div class="card"><div class="l">Free collateral</div><div class="v">${d['free']:,.2f}</div><div class="s">{d['free'] / d['equity'] * 100:.0f}% of equity</div></div>
</div>"""
    rows = "".join(f"""<tr><td><b>{e(p['sym'])}</b><span class="tag {p['side']}">{p['side']}</span></td>
<td>{p['size']:g}</td><td>{p['entry']:.4g}</td><td>{p['px']:.4g} <span class="small {cls(p['ch24'] * (1 if p['side'] == 'LONG' else -1))}">{p['ch24']:+.1f}%</span></td>
<td>${p['notional']:,.2f}</td><td>{p['lev']:.0f}x · ${p['margin']:,.0f}</td>
<td class="{cls(p['upnl'])}"><b>{money(p['upnl'])}</b> <span class="small">{pct(p['upct'])}</span></td>
<td class="{cls(p['funding'])}">{money(p['funding'])}</td><td>{p['liq']:.4g}</td></tr>""" for p in d["pos"]) \
        or '<tr><td colspan="9" class="empty">no open positions</td></tr>'
    upnl_total = sum(p["upnl"] for p in d["pos"])
    pos_tbl = f"""<h2>Open positions ({len(d['pos'])}) · uPnL <span class="{cls(upnl_total)}">{money(upnl_total)}</span></h2>
<table><tr><th>Market</th><th>Size</th><th>Entry</th><th>Mark · 24h</th><th>Notional</th><th>Lev · Margin</th><th>uPnL</th><th>Funding</th><th>Liq</th></tr>{rows}</table>"""
    BADGE = {"stopped": ("🛑 STOPPED OUT", "#3d1416", "#f85149"), "tp": ("🎯 TAKE PROFIT", "#12361f", "#3fb950"),
             "liquidated": ("💀 LIQUIDATED", "#3d1416", "#ff7b72"), "adl": ("⚠️ AUTO-DELEVERAGED", "#3b2e0a", "#d29922"),
             "closed": ("✂️ CLOSED MANUALLY", "#21262d", "#8b949e")}
    def badge(r):
        t, bg, fg = BADGE.get(r, (r.upper(), "#21262d", "#8b949e"))
        return f'<span style="background:{bg};color:{fg};padding:2px 8px;border-radius:5px;font-size:11px;font-weight:700">{t}</span>'
    def side(h): return h.get("side") or ""
    crow = "".join(f"""<tr><td><b>{e(h['sym'])}</b></td><td>{badge(h['reason'])}</td><td>{h['entry']:.4g}</td><td>{h['exit']:.4g}</td>
<td class="{cls(h['pnl'])}"><b>{money(h['pnl'])}</b></td><td>{datetime.fromtimestamp(h['closed_ms'] / 1000):%b %d %H:%M}</td></tr>""" for h in d["closed"]) \
        or '<tr><td colspan="6" class="empty">no trades closed in the last 24h</td></tr>'
    realized = sum(h["pnl"] for h in d["hist"]); n = len(d["hist"])
    by = lambda r: sum(1 for h in d["hist"] if h["reason"] == r)
    recent = "".join(f"""<tr><td><b>{e(h['sym'])}</b></td><td>{badge(h['reason'])}</td><td>{h['entry']:.4g} → {h['exit']:.4g}</td>
<td class="{cls(h['pnl'])}"><b>{money(h['pnl'])}</b></td><td>{datetime.fromtimestamp(h['closed_ms'] / 1000):%b %d}</td></tr>"""
                     for h in sorted(d["hist"], key=lambda h: -h["closed_ms"])[:8]) \
        or '<tr><td colspan="5" class="empty">no closed trades yet</td></tr>'
    closed_tbl = f"""<h2>Closed last 24h</h2>
<table><tr><th>Market</th><th>How it closed</th><th>Entry</th><th>Exit</th><th>PnL</th><th>Time</th></tr>{crow}</table>
<h2>Track record</h2>
<table><tr><th>Trades</th><th>Wins</th><th>Losses</th><th>Win rate</th><th>🎯 TP</th><th>🛑 Stopped</th><th>✂️ Manual</th><th>💀 Liq</th><th>Realized PnL</th></tr>
<tr><td>{n}</td><td class="pos">{d['wins']}</td><td class="neg">{n - d['wins']}</td><td>{(d['wins'] / n * 100 if n else 0):.0f}%</td>
<td>{by('tp')}</td><td>{by('stopped')}</td><td>{by('closed')}</td><td>{by('liquidated') + by('adl')}</td>
<td class="{cls(realized)}"><b>{money(realized)}</b></td></tr></table>
<h2>Recent closed trades</h2>
<table><tr><th>Market</th><th>How it closed</th><th>Entry → Exit</th><th>PnL</th><th>Date</th></tr>{recent}</table>"""
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body><div class="wrap">
<h1>chkm8 · daily PnL 🤖</h1><div class="sub">{datetime.fromtimestamp(d['now']):%A, %B %d %Y · %H:%M} PT · Lighter account #{IDX}</div>
{cards}{pos_tbl}{closed_tbl}{render_paper(d)}{render_paper(d, 'paper_mr', 'Paper bot 2 · 15m index dip-buying')}
<div class="foot"><redacted:wallet-short> · private report · NFA, automated</div></div></body></html>"""


def render_paper(d, key="paper", title="Paper scanner · 4h breakout longs"):
    p = d.get(key)
    if not p:
        return f'<h2>{title}</h2><table><tr><td class="empty">not started yet</td></tr></table>'
    e = html.escape
    cl = p.get("closed", []); n = len(cl); w = sum(1 for x in cl if x["pnl"] > 0)
    avgR = sum(x["R"] for x in cl) / n if n else 0
    ret = p["equity"] - p["start"]
    days = max(1, (d["now"] - p["started"]) / 86400)
    sig24 = [x for x in p.get("signals", []) if d["now"] - x["at"] < 86400]
    head = (f'<h2>{title} · day {days:.0f} · paper equity '
            f'<span class="{cls(ret)}">${p["equity"]:,.2f} ({money(ret)})</span></h2>')
    stats = (f'<table><tr><th>Trades</th><th>Wins</th><th>Win rate</th><th>Avg R</th><th>Open</th><th>Signals 24h</th></tr>'
             f'<tr><td>{n}</td><td class="pos">{w}</td><td>{(w / n * 100 if n else 0):.0f}%</td>'
             f'<td class="{cls(avgR)}">{avgR:+.2f}R</td><td>{len(p.get("open", {}))}</td><td>{len(sig24)}</td></tr></table>')
    rows = "".join(f'<tr><td><b>{e(s)}</b></td><td>{x["entry"]:.4g}</td><td>{x["sl"]:.4g}</td><td>{x["tp"]:.4g}</td>'
                   f'<td>${x["notional"]:,.0f}</td><td>{datetime.fromtimestamp(x["opened"]):%b %d %H:%M}</td></tr>'
                   for s, x in p.get("open", {}).items()) or '<tr><td colspan="6" class="empty">no open paper trades</td></tr>'
    opent = f'<h2>{title.split(" · ")[0]}: open</h2><table><tr><th>Market</th><th>Entry</th><th>Stop</th><th>Target</th><th>Notional</th><th>Opened</th></tr>{rows}</table>'
    recent = "".join(f'<tr><td><b>{e(x["sym"])}</b></td><td>{e(x["reason"])}</td><td>{x["entry"]:.4g} → {x["exit"]:.4g}</td>'
                     f'<td class="{cls(x["pnl"])}"><b>{money(x["pnl"])}</b> <span class="small">{x["R"]:+.2f}R</span></td>'
                     f'<td>{datetime.fromtimestamp(x["closed"]):%b %d}</td></tr>'
                     for x in sorted(cl, key=lambda x: -x["closed"])[:8]) \
        or '<tr><td colspan="5" class="empty">no closed paper trades yet</td></tr>'
    closedt = f'<h2>{title.split(" · ")[0]}: closed (latest)</h2><table><tr><th>Market</th><th>Exit</th><th>Entry → Exit</th><th>PnL</th><th>Date</th></tr>{recent}</table>'
    return head + stats + opent + closedt

def screenshot(html_path, png_path):
    args = [browser(), "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=2",
            "--window-size=860,2000", f"--screenshot={png_path}", f"file://{html_path}"]
    if chkm8env.IN_CONTAINER:
        args[1:1] = ["--no-sandbox", "--disable-dev-shm-usage"]   # no user namespaces / small /dev/shm in pods
    r = subprocess.run(args, capture_output=True, text=True, timeout=90)
    if not os.path.exists(png_path):
        err = "\n".join(l for l in r.stderr.splitlines() if "dbus" not in l and "Fontconfig" not in l)[-800:]
        raise SystemExit(f"browser screenshot failed (rc={r.returncode}): {err}")
    from PIL import Image
    im = Image.open(png_path).convert("RGB"); w, h = im.size
    bg = im.getpixel((w - 2, h - 2)); y = h - 1
    while y > 0 and all(im.getpixel((x, y)) == bg for x in range(0, w, 8)): y -= 1
    im.crop((0, 0, w, min(h, y + 50))).save(png_path, optimize=True)

def text_summary(d):
    lines = [f"Equity ${d['equity']:,.2f} | all-time {money(d['total'])} ({pct(d['total'] / NET_DEPOSITS * 100)})"]
    if d["prev"]:
        lines.append(f"Last 24h {money(d['equity'] - d['prev']['equity'])}")
    for p in d["pos"]:
        lines.append(f"{p['sym']} {p['side']} {p['size']:g} @ {p['entry']:.4g} → {p['px']:.4g}: uPnL {money(p['upnl'])}")
    lines.append(f"Closed 24h: {len(d['closed'])} | trades {len(d['hist'])}, {d['wins']} W")
    names = {"stopped": "STOPPED OUT", "tp": "TAKE PROFIT", "liquidated": "LIQUIDATED", "adl": "AUTO-DELEVERAGED", "closed": "closed manually"}
    for h in d["closed"]:
        lines.append(f"  {h['sym']} {names.get(h['reason'], h['reason'])}: {h['entry']:.4g} -> {h['exit']:.4g}  {money(h['pnl'])}")
    p = d.get("paper")
    if p:
        cl = p.get("closed", [])
        lines.append(f"Paper scanner: equity ${p['equity']:,.2f} ({money(p['equity'] - p['start'])}), "
                     f"{len(cl)} closed, {len(p.get('open', {}))} open")
    q = d.get("paper_mr")
    if q:
        cq = q.get("closed", [])
        lines.append(f"Paper bot 2 (15m index dips): equity ${q['equity']:,.2f} ({money(q['equity'] - q['start'])}), "
                     f"{len(cq)} closed ({sum(1 for x in cq if x['pnl'] > 0)} W), {len(q.get('open', {}))} open")
    lines.append("\nFull report attached. Explorer: https://app.lighter.xyz/explorer/accounts/" + ACC["l1"])
    return "\n".join(lines)

def send(subject, body, png):
    sys.path.insert(0, chkm8env.CODE)
    import mailer
    return mailer.send(TO, subject, body, png, log=lambda s: chkm8env.log_line("report.log", s))

def _unused_send(subject, body, png):
    esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
    recips = "\n".join(f'    make new to recipient at end of to recipients with properties {{address:"{a}"}}' for a in TO)
    script = f'''tell application "Mail"
  set m to make new outgoing message with properties {{subject:"{esc(subject)}", content:"{esc(body)}" & return & return, visible:false}}
  tell m
    set sender to "{FROM}"
{recips}
    make new attachment with properties {{file name:(POSIX file "{png}")}} at after the last paragraph
  end tell
  delay 4
  send m
  return "sent"
end tell'''
    r = subprocess.run(["osascript", "-"], input=script, capture_output=True, text=True, timeout=120)
    return r.stdout.strip() or r.stderr.strip()

if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True); os.makedirs(chkm8env.DATA, exist_ok=True)
    d = collect()
    stamp = datetime.fromtimestamp(d["now"]).strftime("%Y-%m-%d")
    hp, pp = f"{OUT}/pnl-{stamp}.html", f"{OUT}/pnl-{stamp}.png"
    open(hp, "w").write(render(d))
    screenshot(hp, pp)
    subject = f"chkm8 PnL {datetime.now():%b %d}: ${d['equity']:,.2f} ({money(d['total'])} all-time)"
    if "--dry" in sys.argv:
        print(subject); print(text_summary(d)); print(pp); sys.exit()
    res = send(subject, text_summary(d), pp)
    chkm8env.log_line("report.log", res)
    if res == "sent":
        d["snaps"].append({"t": d["now"], "equity": d["equity"]})
        json.dump(d["snaps"][-400:], open(SNAP, "w"))
    print(res)
