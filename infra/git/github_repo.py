#!/Users/checkmate_agent/chkm8/.venv/bin/python
"""GitHub repo helper for chkm8-agent: every repo is private and shared with r3lik.

  github_repo.py create NAME "description"   create private repo + invite r3lik
  github_repo.py share NAME [NAME...]         invite r3lik to existing repos
  github_repo.py reinvite NAME [NAME...]      cancel r3lik's pending invites and send fresh ones
  github_repo.py status NAME [NAME...]        visibility, collaborators + pending invites
  github_repo.py public|private NAME [NAME...]  change visibility (run the leak checks first!)
  github_repo.py rename OLD NEW               rename a repo

Token: macOS Keychain chkm8-ghcr-push (read via keyring; never printed or put on argv).
r3lik has to accept each invitation (GitHub emails him) before he gets access.
"""
import json
import sys
import urllib.error
import urllib.request

import keyring

OWNER = "chkm8-agent"
COLLABORATORS = ["r3lik"]
API = "https://api.github.com"


def call(method, path, body=None):
    tok = keyring.get_password("chkm8-ghcr-push", OWNER)
    req = urllib.request.Request(API + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"token {tok}",
                                          "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req) as r:
        raw = r.read()
        return r.status, (json.loads(raw) if raw else None)


def share(name):
    for user in COLLABORATORS:
        st, _ = call("PUT", f"/repos/{OWNER}/{name}/collaborators/{user}", {"permission": "push"})
        print(f"{name}: {user} " + ("invited" if st == 201 else "already a collaborator" if st == 204 else f"HTTP {st}"))


def reinvite(name):
    _, inv = call("GET", f"/repos/{OWNER}/{name}/invitations")
    for i in inv:
        if i["invitee"]["login"] in COLLABORATORS:
            call("DELETE", f"/repos/{OWNER}/{name}/invitations/{i['id']}")
    share(name)


def status(name):
    _, r = call("GET", f"/repos/{OWNER}/{name}")
    _, c = call("GET", f"/repos/{OWNER}/{name}/collaborators")
    _, i = call("GET", f"/repos/{OWNER}/{name}/invitations")
    print(f"{name}: {r['visibility']} collaborators={[x['login'] for x in c]} "
          f"pending={[x['invitee']['login'] for x in i]}")


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    cmd, args = sys.argv[1], sys.argv[2:]
    try:
        if cmd == "create":
            _, r = call("POST", "/user/repos", {"name": args[0], "description": args[1] if len(args) > 1 else "",
                                                "private": True, "has_wiki": False, "has_projects": False})
            print(f"created {r['full_name']} ({'private' if r['private'] else 'PUBLIC'})")
            share(args[0])
        elif cmd == "share":
            for n in args:
                share(n)
        elif cmd == "reinvite":
            for n in args:
                reinvite(n)
        elif cmd == "status":
            for n in args:
                status(n)
        elif cmd in ("public", "private"):
            for n in args:
                _, r = call("PATCH", f"/repos/{OWNER}/{n}", {"private": cmd == "private"})
                print(f"{n}: now {r['visibility']}")
        elif cmd == "rename":
            _, r = call("PATCH", f"/repos/{OWNER}/{args[0]}", {"name": args[1]})
            print(f"renamed {args[0]} -> {r['full_name']}")
        else:
            sys.exit(__doc__)
    except urllib.error.HTTPError as e:
        sys.exit(f"GitHub API error {e.code}: {e.read()[:300].decode(errors='replace')}")


if __name__ == "__main__":
    main()
