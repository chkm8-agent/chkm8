#!/usr/bin/python3
"""Block private data from entering git. Used by the pre-commit / pre-push hooks and for audits.

  pii_check.py staged          scan what is about to be committed (index content, i.e. AFTER the
                               redact clean filter), plus the commit identity
  pii_check.py history [REV..] scan every blob, commit message and author/committer email reachable
                               from REV (default: --all)
  pii_check.py paths P...      scan files on disk (pre-flight before the first `git add`)

Flags: emails, 0x wallet/contract addresses, public IPv4s, SSH public keys, PEM private keys,
64-hex strings (possible private keys), any value from the private redaction map (means the filter
did not run), files > 5 MB, and secret-looking filenames (.env, *.pem, *.key, kubeconfig, ...).

Also blocks, even after `git add -f`:
  - paths matching a regex in <repo>/.pii-deny (e.g. a private repo's real config.json and data/)
  - any term from the private list ~/.config/chkm8-git/deny-terms.txt (case-insensitive; private
    names that are not emails/keys but must never leave the Mac)

Allow known-safe matches with regexes in infra/git/pii-allow.txt (global) or <repo>/.pii-allow.
Exit 0 = clean, 1 = findings (printed with values masked).
"""
import ipaddress
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MAP = os.environ.get("CHKM8_REDACT_MAP", os.path.expanduser("~/.config/chkm8-git/redactions.json"))
DENY_TERMS = os.environ.get("CHKM8_DENY_TERMS", os.path.expanduser("~/.config/chkm8-git/deny-terms.txt"))
MAX_BYTES = 5 * 1024 * 1024
SKIP_NAMES = {".pii-allow", "pii-allow.txt"}
IMG_TLD = r"(?!png\b|jpe?g\b|webp\b|gif\b|svg\b|avif\b)"

PATTERNS = [
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\." + IMG_TLD + r"[A-Za-z]{2,}\b")),
    ("evm-address", re.compile(r"\b0x[a-fA-F0-9]{40}\b")),
    ("hex64", re.compile(r"(?<!sha256:)\b(?:0x)?[a-fA-F0-9]{64}\b")),
    ("ipv4", re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")),
    ("ssh-pubkey", re.compile(r"\b(?:ssh-(?:ed25519|rsa|dss)|ecdsa-sha2-nistp\d+) AAAA[A-Za-z0-9+/=]{20,}")),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]
BAD_NAME = re.compile(
    r"(^|/)(\.env(\..*)?|.*\.pem|.*\.key|.*\.p12|.*\.pfx|.*\.asc|.*\.kdbx|id_(rsa|ed25519|ecdsa)(\.pub)?"
    r"|kubeconfig.*|.*\.kubeconfig|\.?yahoo_access\.json|.*credentials.*\.json|.*token.*\.json|redactions\.json)$",
    re.I)


def git(*args, check=True):
    return subprocess.run(["git", *args], capture_output=True, check=check).stdout


def read_lines(*paths):
    out = []
    for p in paths:
        if os.path.exists(p):
            for line in open(p, encoding="utf-8"):
                line = line.strip()
                if line and not line.startswith("#"):
                    out.append(line)
    return out


def load_allow(root):
    return [re.compile(x) for x in read_lines(os.path.join(HERE, "pii-allow.txt"), os.path.join(root, ".pii-allow"))]


def load_private():
    try:
        return [v for v, _ in json.load(open(MAP, encoding="utf-8"))["map"]]
    except Exception:
        return []


def mask(v):
    return v if len(v) <= 8 else f"{v[:5]}…{v[-3:]} ({len(v)} chars)"


class Scanner:
    def __init__(self, root):
        self.allow = load_allow(root)
        self.deny_paths = [re.compile(x) for x in read_lines(os.path.join(root, ".pii-deny"))]
        self.deny_terms = [t.lower() for t in read_lines(DENY_TERMS)]
        self.private = load_private()
        self.findings = []

    def allowed(self, value):
        return any(a.search(value) for a in self.allow)

    def term_hit(self, line, term):
        """Deny term in the line once allowlisted phrases (known-public wording in a market note) are blanked out."""
        for a in self.allow:
            line = a.sub(lambda m: " " * len(m.group(0)), line)
        return term in line.lower()

    def add(self, where, kind, value):
        self.findings.append(f"{where}: {kind}: {mask(value)}")

    def check_name(self, path):
        if BAD_NAME.search(path) and not self.allowed(path):
            self.add(path, "secret-looking filename", os.path.basename(path))
        if any(d.search(path) for d in self.deny_paths):
            self.add(path, "path is in .pii-deny (must never be committed)", path)

    def check_blob(self, path, data):
        if len(data) > MAX_BYTES:
            self.add(path, "file > 5 MB", f"{len(data)} bytes")
        if b"\0" in data[:8192] or os.path.basename(path.split("@")[0]) in SKIP_NAMES:   # history paths are name@sha
            return
        text = data.decode("utf-8", "replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            where = f"{path}:{lineno}"
            for real in self.private:
                if real in line:
                    self.add(where, "private value (redact filter not applied?)", real)
            low = line.lower()
            for term in self.deny_terms:
                if term in low and self.term_hit(line, term):
                    self.add(where, "private term from deny-terms.txt", term)
            for kind, rx in PATTERNS:
                for m in rx.finditer(line):
                    v = m.group(0)
                    if kind == "ipv4":
                        try:
                            ip = ipaddress.ip_address(v)
                        except ValueError:
                            continue
                        if not ip.is_global:
                            continue
                    if self.allowed(v):
                        continue
                    self.add(where, kind, v)


def staged(sc):
    email = git("config", "user.email", check=False).decode().strip()
    if not email.endswith("@users.noreply.github.com"):
        sc.add("git config user.email", "commit identity is not a GitHub noreply address", email or "(unset)")
    names = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").decode().split("\0")
    for path in filter(None, names):
        sc.check_name(path)
        sc.check_blob(path, git("show", f":{path}"))


def history(sc, revs):
    revs = revs or ["--all"]
    seen = set()
    for line in git("rev-list", "--objects", *revs).decode().splitlines():
        sha, _, path = line.partition(" ")
        if sha in seen:
            continue
        seen.add(sha)
        kind = git("cat-file", "-t", sha).decode().strip()
        if kind == "blob":
            if path:
                sc.check_name(path)
            sc.check_blob(f"{path or sha}@{sha[:7]}", git("cat-file", "-p", sha))
        elif kind == "commit":
            meta = git("log", "-1", "--format=%ae%n%ce%n%B", sha).decode()
            ae, ce, body = meta.split("\n", 2)
            for e in (ae, ce):
                if not e.endswith("@users.noreply.github.com") and not sc.allowed(e):
                    sc.add(f"commit {sha[:7]}", "author/committer email", e)
            sc.check_blob(f"commit {sha[:7]} message", body.encode())


def paths(sc, roots):
    for r in roots:
        for d, dirs, files in os.walk(r) if os.path.isdir(r) else [(os.path.dirname(r), [], [os.path.basename(r)])]:
            dirs[:] = [x for x in dirs if x not in {".git", ".venv", "node_modules", "__pycache__"}]
            for f in files:
                p = os.path.join(d, f)
                sc.check_name(p)
                sc.check_blob(p, open(p, "rb").read())


def main():
    mode, args = (sys.argv[1], sys.argv[2:]) if len(sys.argv) > 1 else ("", [])
    root = git("rev-parse", "--show-toplevel", check=False).decode().strip() or os.getcwd()
    sc = Scanner(root)
    if mode == "staged":
        staged(sc)
    elif mode == "history":
        history(sc, args)
    elif mode == "paths":
        paths(sc, args)
    else:
        sys.exit(__doc__)
    if sc.findings:
        print(f"pii_check: {len(sc.findings)} finding(s) in {root}:", file=sys.stderr)
        for f in sc.findings:
            print("  " + f, file=sys.stderr)
        print("Fix: add the value to ~/.config/chkm8-git/redactions.json (redacted automatically), "
              "gitignore the file, or allowlist a known-safe value in .pii-allow.", file=sys.stderr)
        sys.exit(1)
    print(f"pii_check: clean ({mode})", file=sys.stderr)


if __name__ == "__main__":
    main()
