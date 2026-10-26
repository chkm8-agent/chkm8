#!/usr/bin/env python3
"""Read recent inbox mail (read-only). Prints sender/subject list, or full text of matches.
  imap_read.py list [n]            -> last n messages (default 15)
  imap_read.py get "subject|from substring" [outdir]  -> saves text/html bodies + attachments of newest match
"""
import email, imaplib, os, subprocess, sys
from email.header import decode_header, make_header

FROM = "<redacted:agent-email>"
pw = subprocess.run(["security", "find-generic-password", "-s", "chkm8-gmail-smtp", "-a", FROM, "-w"],
                    capture_output=True, text=True).stdout.strip().replace(" ", "")
M = imaplib.IMAP4_SSL("imap.gmail.com"); M.login(FROM, pw); M.select("INBOX", readonly=True)
_, ids = M.search(None, "ALL")
ids = ids[0].split()
h = lambda v: str(make_header(decode_header(v or "")))

if sys.argv[1] == "list":
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 15
    for i in reversed(ids[-n:]):
        _, d = M.fetch(i, "(BODY.PEEK[HEADER.FIELDS (SUBJECT DATE FROM)])")
        m = email.message_from_bytes(d[0][1])
        print(i.decode(), "|", m.get("Date"), "|", h(m.get("From")), "|", h(m.get("Subject")))
else:
    needle = sys.argv[2].lower() if sys.argv[1] == "get" else sys.argv[1].lower()
    out = sys.argv[3] if len(sys.argv) > 3 else os.path.expanduser("~/.hermes/cache/scratch/mail")
    os.makedirs(out, exist_ok=True)
    for i in reversed(ids[-60:]):
        _, d = M.fetch(i, "(BODY.PEEK[])")
        m = email.message_from_bytes(d[0][1])
        if needle in (h(m.get("Subject")) + " " + h(m.get("From"))).lower():
            print("MATCH", i.decode(), h(m.get("From")), h(m.get("Subject")), m.get("Date"))
            k = 0
            for part in m.walk():
                if part.is_multipart():
                    continue
                fn = part.get_filename(); ct = part.get_content_type()
                data = part.get_payload(decode=True) or b""
                name = f"{k:02d}_{h(fn)}" if fn else f"body_{k}.{'html' if ct == 'text/html' else 'txt' if ct == 'text/plain' else 'bin'}"
                k += 1  # numbered prefix: inline images often share the name "image.png"
                p = os.path.join(out, name.replace("/", "_"))
                open(p, "wb").write(data)
                print("  saved", p, ct, len(data))
            break
    else:
        print("no match")
M.logout()
