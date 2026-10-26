#!/usr/bin/env python3
"""Check delivery by reading the Gmail inbox over IMAP (same app password). Read-only.
  imap_check.py "subject substring" [minutes]"""
import imaplib, email, sys, subprocess
from email.header import decode_header, make_header
from datetime import datetime, timedelta

FROM = "<redacted:agent-email>"
pw = subprocess.run(["security", "find-generic-password", "-s", "chkm8-gmail-smtp", "-a", FROM, "-w"],
                    capture_output=True, text=True).stdout.strip().replace(" ", "")
needle = sys.argv[1].lower(); mins = int(sys.argv[2]) if len(sys.argv) > 2 else 60
M = imaplib.IMAP4_SSL("imap.gmail.com"); M.login(FROM, pw); M.select("INBOX", readonly=True)
since = (datetime.now() - timedelta(days=1)).strftime("%d-%b-%Y")
_, ids = M.search(None, f'(SINCE "{since}")')
found = 0
for i in reversed(ids[0].split()[-40:]):
    _, d = M.fetch(i, "(BODY.PEEK[HEADER.FIELDS (SUBJECT DATE TO)] BODYSTRUCTURE)")
    hdr = email.message_from_bytes(d[0][1])
    subj = str(make_header(decode_header(hdr.get("Subject", ""))))
    if needle in subj.lower():
        att = "attachment" in str(d).lower() or 'filename' in str(d).lower()
        print(f"FOUND | {hdr.get('Date')} | {subj} | to: {hdr.get('To')} | attachment: {att}")
        found += 1
print("matches:", found)
M.logout()
