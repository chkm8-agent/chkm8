#!/usr/bin/env python3
"""Multi-core Ethereum vanity address grinder.
Private keys are written ONLY to candidates.json (chmod 600); stdout shows addresses only."""
import json, os, sys, time, secrets
from multiprocessing import Process, Queue, cpu_count
from coincurve import PrivateKey
from Crypto.Hash import keccak

# Hex-only alphabet (0-9a-f). Patterns: (hex, label)
PREFIXES = [
    ("1337c0de", "1337 c0de"), ("1337def1", "1337 defi"), ("def11337", "defi 1337"), ("0def1", "defi"), ("def1", "defi"), ("c0ffee", "c0ffee"), ("defaced", "defaced"),
    ("5ca1ab1e", "scalable"), ("decade", "decade"), ("facade", "facade"),
    ("1337", "1337"), ("c4ec", "c(h)ec(k)"), ("deadbeef", "deadbeef"),
    ("ba5ed", "based"), ("0ddba11", "oddball"), ("b00b1e5", "boobies"),
    ("decaf", "decaf"), ("f00d", "food"), ("c0de", "c0de"), ("dead", "dead"),
    ("beef", "beef"), ("cafe", "cafe"), ("babe", "babe"), ("face", "face"),
    ("a11ce", "alice"), ("0b0e", "oboe"), ("b0ss", ""),
]
SUFFIXES = [("1337", "1337"), ("def1", "defi"), ("c0de", "c0de"), ("dead", "dead"), ("beef", "beef"),
            ("cafe", "cafe"), ("babe", "babe"), ("f00d", "food"), ("face", "face"),
            ("ba5ed", "based"), ("defaced", "defaced"), ("c0ffee", "c0ffee")]
PREFIXES = [p for p in PREFIXES if all(c in "0123456789abcdef" for c in p[0])]

def score(addr):
    best, why = 0, []
    for p, lbl in PREFIXES:
        if addr.startswith(p):
            best, why = len(p), [f"0x{p}… ({lbl})"]
            break
    # repeated-char prefix (e.g. 0x0000000, 0xaaaaaa)
    run = len(addr) - len(addr.lstrip(addr[0]))
    if run >= 6 and run > best:
        best, why = run, [f"{run}x '{addr[0]}' prefix"]
    sbest = 0
    for s, lbl in SUFFIXES:
        if addr.endswith(s) and len(s) > sbest:
            sbest = len(s); swhy = f"…{s} ({lbl})"
    if sbest:
        why.append(swhy)
    # Combo bonus: both ends match
    total = best + sbest + (2 if best and sbest else 0)
    return total, why

def worker(q, seed, min_score):
    import random
    while True:
        k = secrets.token_bytes(32)
        pub = PrivateKey(k).public_key.format(compressed=False)[1:]
        h = keccak.new(digest_bits=256); h.update(pub)
        addr = h.hexdigest()[-40:]
        s, why = score(addr)
        if s >= min_score:
            q.put((s, addr, k.hex(), why))
        q.put(None) if False else None

def counter_worker(q, min_score, n_report=20000):
    n = 0
    while True:
        k = secrets.token_bytes(32)
        pub = PrivateKey(k).public_key.format(compressed=False)[1:]
        h = keccak.new(digest_bits=256); h.update(pub)
        addr = h.hexdigest()[-40:]
        s, why = score(addr)
        if s >= min_score:
            q.put(("hit", s, addr, k.hex(), why))
        n += 1
        if n % n_report == 0:
            q.put(("count", n_report))

if __name__ == "__main__":
    budget = float(sys.argv[1]) if len(sys.argv) > 1 else 300
    min_score = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "candidates.json")
    q = Queue()
    procs = [Process(target=counter_worker, args=(q, min_score), daemon=True) for _ in range(cpu_count())]
    for p in procs: p.start()
    t0, total, hits = time.time(), 0, []
    while time.time() - t0 < budget:
        try:
            m = q.get(timeout=1)
        except Exception:
            continue
        if m[0] == "count":
            total += m[1]
        else:
            _, s, addr, key, why = m
            hits.append({"score": s, "address": addr, "key": key, "why": why})
            # append immediately so interruptions never lose hits (JSON lines, mode 600)
            fd = os.open(out + "l", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            os.write(fd, (json.dumps(hits[-1]) + "\n").encode()); os.close(fd)
            print(f"[{time.time()-t0:6.0f}s] score {s:2d}  0x{addr}  {' + '.join(why)}", flush=True)
    for p in procs: p.terminate()
    hits.sort(key=lambda h: -h["score"])
    old = umask = os.umask(0o077)
    with open(out, "w") as f: json.dump(hits[:50], f)
    os.chmod(out, 0o600)
    print(f"\nchecked {total:,} addresses in {budget:.0f}s ({total/budget:,.0f}/s); {len(hits)} hits saved (keys in {out}, mode 600)")
