"""Shared runtime config for chkm8 scripts, so the same code runs on the Mac and in a container.

Every setting has a Mac default (current behaviour) and an env-var override (container/k8s):

  CHKM8_DATA          mutable state dir: monitor_state.json, equity_snapshots.json   (Mac: ~/chkm8/lighter)
  CHKM8_LOGS          log dir, or "-" to log to stdout (k8s: kubectl logs)           (Mac: ~/chkm8/logs)
  CHKM8_REPORTS       PnL report HTML/PNG output dir                                 (Mac: ~/chkm8/reports)
  CHKM8_ACCOUNT_JSON  account.json path (non-secret: account index, key index, L1)   (Mac: <code>/lighter/account.json)
  LIGHTER_ACCOUNT_INDEX / LIGHTER_API_KEY_INDEX   override the values in account.json
  CHKM8_DRY_RUN=1     monitor: no X posts, no order cancels (log what it would do)

Secrets: env var first (k8s Secret), else macOS Keychain (Mac):
  LIGHTER_API_KEY     <- Keychain chkm8-lighter-api / "<account>:<key index>"
  GMAIL_APP_PASSWORD  <- Keychain chkm8-gmail-smtp / <redacted:agent-email>   (read in mailer.py)
  X_CONSUMER_KEY, X_CONSUMER_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET   (xpost.py; Mac uses xurl)
"""
import json
import logging
import os
import sys

CODE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~/chkm8")


def env(name, default=None):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


DATA = env("CHKM8_DATA", f"{HOME}/lighter")
LOGS = env("CHKM8_LOGS", f"{HOME}/logs")
REPORTS = env("CHKM8_REPORTS", f"{HOME}/reports")
DRY_RUN = env("CHKM8_DRY_RUN", "0") == "1"
IN_CONTAINER = os.path.exists("/.dockerenv") or bool(env("KUBERNETES_SERVICE_HOST"))


def account():
    path = env("CHKM8_ACCOUNT_JSON", f"{CODE}/lighter/account.json")
    a = json.load(open(path))
    if env("LIGHTER_ACCOUNT_INDEX"):
        a["account_index"] = int(env("LIGHTER_ACCOUNT_INDEX"))
    if env("LIGHTER_API_KEY_INDEX"):
        a["api_key_index"] = int(env("LIGHTER_API_KEY_INDEX"))
    return a


def secret(env_name, service, acct):
    """Env var (k8s Secret) first, then macOS Keychain. Returns None if neither has it."""
    v = env(env_name)
    if v:
        return v
    try:
        import keyring
        return keyring.get_password(service, acct)
    except Exception:
        return None


def lighter_api_key(account_index, key_index):
    return secret("LIGHTER_API_KEY", "chkm8-lighter-api", f"{account_index}:{key_index}")


def logger(name, filename):
    """File log on the Mac; stdout when CHKM8_LOGS='-' (so `kubectl logs` shows it)."""
    kw = dict(level=logging.INFO, format="%(asctime)s %(message)s")
    if LOGS == "-":
        logging.basicConfig(stream=sys.stdout, **kw)
    else:
        os.makedirs(LOGS, exist_ok=True)
        logging.basicConfig(filename=f"{LOGS}/{filename}", **kw)
    return logging.getLogger(name).info


def log_line(filename, text):
    """Append one line to a log file, or print it when logging to stdout."""
    from datetime import datetime
    line = f"{datetime.now().isoformat()} {text}"
    if LOGS == "-":
        print(line, flush=True)
    else:
        os.makedirs(LOGS, exist_ok=True)
        with open(f"{LOGS}/{filename}", "a") as f:
            f.write(line + "\n")
