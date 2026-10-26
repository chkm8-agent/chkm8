"""Reliable email sender shared by report.py and digest_send.py.

Primary path: Gmail SMTP (smtp.gmail.com:465, SSL) using an app password stored in the macOS Keychain
(service "chkm8-gmail-smtp", account "<redacted:agent-email>"). No GUI app involved, so it works at 7am
when Mail.app is napping (Mail.app AppleEvents timed out on 2026-10-01 and 2026-10-02).
Fallback: Mail.app via AppleScript (old path), only if SMTP fails after retries.

Usage: send(to=[...], subject, body, attachment=path_or_None, html=None, log=callable) -> "sent" | "failed"
"""
import mimetypes, os, smtplib, ssl, subprocess, time
from email.message import EmailMessage
from email.utils import make_msgid, formatdate

FROM = "<redacted:agent-email>"
FROM_NAME = "Chkm8 Agent"
ACCOUNT = "chkm8.ag3nt"
KEYCHAIN_SERVICE = "chkm8-gmail-smtp"


# ---------------------------------------------------------------- SMTP (primary)
def _app_password():
    # Container/k8s: from the GMAIL_APP_PASSWORD env var (a Secret).
    pw = os.environ.get("GMAIL_APP_PASSWORD")
    if pw:
        return pw.replace(" ", "")
    # Mac: use the `security` CLI first: it created the Keychain item, so it's on the item's access list and
    # never triggers a GUI "allow access?" prompt (which would hang an unattended 7am job).
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", FROM, "-w"],
                           capture_output=True, text=True, timeout=30)
        pw = r.stdout.strip() if r.returncode == 0 else None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pw = None
    if not pw:
        try:
            import keyring
            pw = keyring.get_password(KEYCHAIN_SERVICE, FROM)
        except Exception:
            pw = None
    return pw.replace(" ", "") if pw else None   # Google shows it as 4 groups of 4


def _build(to, subject, body, attachment=None, html=None):
    """Plain text + optional HTML. An image attachment is EMBEDDED inline (multipart/related, cid:) and shown
    full-width at the top of the HTML body, so it reads in the mail client without opening the file; the
    same file is also kept as a regular attachment for full-resolution zoom/download."""
    msg = EmailMessage()
    msg["From"] = f"{FROM_NAME} <{FROM}>"
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="chkm8.local")
    msg.set_content(body)
    ctype = mimetypes.guess_type(attachment)[0] if attachment else None
    is_image = bool(ctype and ctype.startswith("image/"))
    if is_image:
        cid = make_msgid(domain="chkm8.local")          # "<...@chkm8.local>"
        # Our PNGs are rendered at 2x: show them at their natural CSS width (pixels / 2) so they stay sharp and
        # aren't stretched; width:100% lets them shrink on phones.
        css_w = 680
        try:
            import struct
            with open(attachment, "rb") as f:
                hdr = f.read(24)
            if hdr[:8] == b"\x89PNG\r\n\x1a\n":
                css_w = struct.unpack(">I", hdr[16:20])[0] // 2
        except Exception:
            pass
        img = f'<img src="cid:{cid[1:-1]}" alt="{os.path.basename(attachment)}" width="{css_w}" ' \
              f'style="display:block;width:100%;max-width:{css_w}px;height:auto;border:0;margin:0">'
        if html:
            html = html.replace("<body>", "<body>" + img, 1) if "<body>" in html else img + html
        else:
            esc = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            html = (f'<html><body style="margin:0;padding:12px 0">{img}'
                    f'<pre style="font-family:-apple-system,Helvetica,Arial,sans-serif;white-space:pre-wrap;'
                    f'color:#57606a;font-size:12px;padding:12px 0;margin:0;max-width:{css_w}px">{esc}</pre></body></html>')
        msg.add_alternative(html, subtype="html")
        maintype, subtype = ctype.split("/", 1)
        with open(attachment, "rb") as f:
            data = f.read()
        msg.get_payload()[1].add_related(data, maintype=maintype, subtype=subtype, cid=cid,
                                         filename=os.path.basename(attachment), disposition="inline")
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=os.path.basename(attachment))
        return msg
    if html:
        msg.add_alternative(html, subtype="html")
    if attachment:
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        with open(attachment, "rb") as f:
            msg.add_attachment(f.read(), maintype=maintype, subtype=subtype,
                               filename=os.path.basename(attachment))
    return msg


def send_smtp(to, subject, body, attachment=None, html=None, attempts=3, log=print):
    pw = _app_password()
    if not pw:
        log("smtp: no app password in Keychain")
        return "failed"
    msg = _build(to, subject, body, attachment, html)
    ctx = ssl.create_default_context()
    # 465 = implicit TLS (Mac default). 587 = STARTTLS: Hetzner blocks outbound 25/465 on new cloud
    # servers, so the cluster sets CHKM8_SMTP_PORT=587.
    port = int(os.environ.get("CHKM8_SMTP_PORT", "465"))
    for i in range(1, attempts + 1):
        try:
            if port == 587:
                s = smtplib.SMTP("smtp.gmail.com", 587, timeout=60)
                s.starttls(context=ctx)
            else:
                s = smtplib.SMTP_SSL("smtp.gmail.com", port, context=ctx, timeout=60)
            with s:
                s.login(FROM, pw)
                refused = s.send_message(msg)
            if refused:
                log(f"smtp attempt {i}: some recipients refused: {refused}")
            return "sent"
        except smtplib.SMTPAuthenticationError as e:
            log(f"smtp attempt {i}: auth failed ({e.smtp_code}); check app password")
            return "failed"                      # retrying won't fix bad credentials
        except Exception as e:
            log(f"smtp attempt {i}: {type(e).__name__}: {str(e)[:200]}")
            time.sleep(15 * i)
    return "failed"


# ---------------------------------------------------------------- Mail.app (fallback)
def _osa(script, timeout):
    r = subprocess.run(["osascript", "-"], input=script, capture_output=True, text=True, timeout=timeout)
    return (r.stdout.strip() or r.stderr.strip())

def _esc(s): return s.replace("\\", "\\\\").replace('"', '\\"')

def ensure_running():
    subprocess.run(["open", "-g", "-a", "Mail"], capture_output=True, timeout=30)   # -g: don't steal focus
    for _ in range(12):
        try:
            if _osa('tell application "Mail" to return (count of accounts) as string', 20).isdigit():
                return True
        except subprocess.TimeoutExpired:
            pass
        time.sleep(5)
    return False

def already_sent(subject, within_min=30):
    script = f'''tell application "Mail"
  repeat with mb in (every mailbox of account "{ACCOUNT}")
    if name of mb is "Sent Mail" then
      set cutoff to (current date) - ({within_min} * minutes)
      set ms to (messages of mb whose subject is "{_esc(subject)}" and date sent > cutoff)
      return (count of ms) as string
    end if
  end repeat
  return "0"
end tell'''
    try:
        res = _osa(script, 60).strip()
        return res.isdigit() and int(res) > 0      # errors (e.g. "AppleEvent timed out") must NOT count as sent
    except Exception:
        return False

def send_mailapp(to, subject, body, attachment=None, attempts=2, log=print):
    recips = "\n".join(f'    make new to recipient at end of to recipients with properties {{address:"{a}"}}' for a in to)
    att = (f'    make new attachment with properties {{file name:(POSIX file "{attachment}")}} at after the last paragraph'
           if attachment else "")
    script = f'''tell application "Mail"
  set m to make new outgoing message with properties {{subject:"{_esc(subject)}", content:"{_esc(body)}" & return & return, visible:false}}
  tell m
    set sender to "{FROM}"
{recips}
{att}
  end tell
  delay 4
  send m
  return "sent"
end tell'''
    for i in range(1, attempts + 1):
        ensure_running()
        try:
            res = _osa(script, 300)
            if res == "sent":
                return "sent"
            log(f"mailapp attempt {i}: {res[:200]}")
        except subprocess.TimeoutExpired:
            log(f"mailapp attempt {i}: osascript timed out")
        time.sleep(60)
        if already_sent(subject):
            log(f"mailapp attempt {i}: found in Sent Mail, treating as sent")
            return "sent"
        if i < attempts:
            subprocess.run(["osascript", "-e", 'tell application "Mail" to quit'], capture_output=True, timeout=60)
            time.sleep(10)
    return "failed"


# ---------------------------------------------------------------- entry point
def send(to, subject, body, attachment=None, attempts=3, log=print, html=None):
    res = send_smtp(to, subject, body, attachment, html=html, attempts=attempts, log=log)
    if res == "sent":
        log("sent via smtp")
        return "sent"
    if not os.path.exists("/usr/bin/osascript"):        # container: no Mail.app fallback
        log("smtp failed; no Mail.app fallback here")
        return "failed"
    log("smtp failed; falling back to Mail.app")
    res = send_mailapp(to, subject, body, attachment, log=log)
    if res == "sent":
        log("sent via Mail.app fallback")
    return res
