#!/usr/bin/env python3
"""Unattended Keychain access test, run from launchd (same context as the 7am jobs).
Reads each secret the scheduled jobs need with a hard timeout; a GUI prompt would show up as a timeout."""
import subprocess, sys, time
sys.path.insert(0, "/Users/checkmate_agent/chkm8")
out = open("/Users/checkmate_agent/chkm8/logs/keychain_test.log", "w")
def log(s): out.write(s + "\n"); out.flush()
t = time.time()
try:
    import mailer
    pw = mailer._app_password()
    log(f"gmail app password: {'OK' if pw and len(pw) == 16 else 'MISSING'} ({time.time()-t:.1f}s)")
except Exception as e:
    log(f"gmail app password: ERROR {e!r}")
for svc, acct in [("chkm8-lighter-api", "<redacted:lighter-account>:4")]:
    t = time.time()
    try:
        r = subprocess.run(["/Users/checkmate_agent/chkm8/.venv/bin/python", "-c",
                            f"import keyring;print(bool(keyring.get_password('{svc}','{acct}')))"],
                           capture_output=True, text=True, timeout=30)
        log(f"{svc}: {r.stdout.strip() or r.stderr.strip()[:120]} ({time.time()-t:.1f}s)")
    except subprocess.TimeoutExpired:
        log(f"{svc}: TIMEOUT (likely a GUI prompt)")
log("done")
