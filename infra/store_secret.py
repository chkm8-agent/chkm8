#!/usr/bin/env python3
"""Ask for a secret in a hidden-input macOS dialog and store it straight into the Keychain.

The value never appears on screen, in argv, in shell history or in stdout.

  store_secret.py <keychain-service> <account> "<prompt text>" [--check-github]

--check-github: call api.github.com/user with the token and print only the login and the
token's scopes (from the X-OAuth-Scopes header), so we can confirm it's the right token.
"""
import subprocess, sys, json, urllib.request
import keyring


def ask(prompt):
    script = (
        'display dialog ' + json.dumps(prompt) +
        ' default answer "" with hidden answer with title "chkm8: store secret in Keychain"'
        ' buttons {"Cancel", "Save"} default button "Save" giving up after 600'
    )
    r = subprocess.run(["osascript", "-e", script, "-e", "text returned of result"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("cancelled or timed out")
    val = r.stdout.rstrip("\n").strip()
    if not val:
        sys.exit("empty value, nothing stored")
    return val


def check_github(token):
    req = urllib.request.Request("https://api.github.com/user",
                                 headers={"Authorization": f"token {token}", "User-Agent": "chkm8"})
    with urllib.request.urlopen(req, timeout=20) as r:
        login = json.load(r).get("login")
        scopes = r.headers.get("X-OAuth-Scopes", "")
        exp = r.headers.get("GitHub-Authentication-Token-Expiration", "none")
    return login, scopes, exp


def main():
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    service, account, prompt = sys.argv[1:4]
    val = ask(prompt)
    if "--check-github" in sys.argv:
        try:
            login, scopes, exp = check_github(val)
        except Exception as e:
            sys.exit(f"token rejected by GitHub ({e.__class__.__name__}); nothing stored")
        print(f"github login={login} scopes=[{scopes}] expires={exp}")
    keyring.set_password(service, account, val)
    print(f"stored in Keychain: service={service} account={account} ({len(val)} chars)")


if __name__ == "__main__":
    main()
