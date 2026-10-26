"""Post / reply on X as @Chkm8_agent.

Two backends:
  - OAuth 1.0a user tokens from env (X_CONSUMER_KEY, X_CONSUMER_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET):
    used on the server. Static tokens, so the server never touches the Mac's rotating OAuth2 refresh token.
  - xurl CLI (Mac default): uses ~/.xurl (OAuth2).
CHKM8_DRY_RUN=1: log the text, post nothing.

post(text, reply_to=None, log=print) -> tweet id (str) or None
"""
import base64, hashlib, hmac, json, os, secrets, subprocess, time, urllib.parse, urllib.request, urllib.error

import chkm8env

TWEETS_URL = "https://api.x.com/2/tweets"
XURL = chkm8env.env("CHKM8_XURL", "/opt/homebrew/bin/xurl")


def _pct(s):
    return urllib.parse.quote(str(s), safe="~-._")


def oauth1_header(method, url, ck, cs, tk, ts, nonce=None, timestamp=None):
    """OAuth 1.0a HMAC-SHA1 Authorization header. JSON bodies are not part of the signature."""
    p = {"oauth_consumer_key": ck, "oauth_nonce": nonce or secrets.token_hex(16),
         "oauth_signature_method": "HMAC-SHA1", "oauth_timestamp": str(timestamp or int(time.time())),
         "oauth_token": tk, "oauth_version": "1.0"}
    u = urllib.parse.urlsplit(url)
    q = urllib.parse.parse_qsl(u.query, keep_blank_values=True)
    base_url = f"{u.scheme}://{u.netloc}{u.path}"
    params = "&".join(f"{k}={v}" for k, v in sorted((_pct(k), _pct(v)) for k, v in list(p.items()) + q))
    base = "&".join([method.upper(), _pct(base_url), _pct(params)])
    key = f"{_pct(cs)}&{_pct(ts)}".encode()
    p["oauth_signature"] = base64.b64encode(hmac.new(key, base.encode(), hashlib.sha1).digest()).decode()
    return "OAuth " + ", ".join(f'{_pct(k)}="{_pct(v)}"' for k, v in sorted(p.items()))


def _creds():
    names = ("X_CONSUMER_KEY", "X_CONSUMER_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")
    vals = [chkm8env.env(n) for n in names]
    return vals if all(vals) else None


def _post_oauth1(text, reply_to, creds, log):
    body = {"text": text}
    if reply_to:
        body["reply"] = {"in_reply_to_tweet_id": str(reply_to)}
    req = urllib.request.Request(TWEETS_URL, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": oauth1_header("POST", TWEETS_URL, *creds)})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)["data"]["id"]
    except urllib.error.HTTPError as e:
        log(f"X post failed (oauth1): HTTP {e.code} {e.read()[:300]!r}")
    except Exception as e:
        log(f"X post failed (oauth1): {e!r}")
    return None


def _post_xurl(text, reply_to, log):
    cmd = [XURL, "reply", str(reply_to), text] if reply_to else [XURL, "post", text]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    try:
        return json.loads(r.stdout)["data"]["id"]
    except Exception:
        log(f"X post failed (xurl): {r.stdout[:300]} {r.stderr[:200]}")
        return None


def post(text, reply_to=None, log=print):
    if chkm8env.DRY_RUN:
        log(f"DRY RUN: would post to X (reply_to={reply_to}):\n{text}")
        return None
    creds = _creds()
    if creds:
        return _post_oauth1(text, reply_to, creds, log)
    if os.path.exists(XURL):
        return _post_xurl(text, reply_to, log)
    log("X post skipped: no OAuth1 env tokens and no xurl binary")
    return None
