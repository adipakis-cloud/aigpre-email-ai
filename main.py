import imaplib, smtplib, email, os, time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import decode_header
from datetime import datetime, timezone
import httpx

IMAP_HOST     = os.environ["IMAP_HOST"]
IMAP_PORT     = int(os.environ.get("IMAP_PORT","993"))
SMTP_HOST     = os.environ.get("SMTP_HOST","smtp.gmail.com")
SMTP_PORT     = int(os.environ.get("SMTP_PORT","587"))
MONITOR_EMAIL = os.environ["AIGPRE_EMAIL"]
MONITOR_PASS  = os.environ["AIGPRE_PASS"]
SMTP_USER     = os.environ.get("SMTP_USER","")
SMTP_PASS     = os.environ.get("SMTP_PASS","")
OPS_EMAIL     = os.environ["OPS_EMAIL"]
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]
CHECK_INTERVAL= int(os.environ.get("CHECK_INTERVAL","120"))

SYSTEM_PROMPT = """You are the trade correspondent for AIGPRE Global Trade Platform.
AIGPRE facilitates structured cross-border industrial commodity transactions.
Commodities: Nickel, Coal, Copper, Lithium, Bauxite, Industrial Materials.
When responding to inquiries:
1. Acknowledge the specific commodity mentioned
2. Confirm entry into AIGPRE qualification review
3. Explain the 4-step process briefly
4. State 48-hour review timeline
5. Institutional tone. Maximum 220 words.
6. End with: AIGPRE Global Trade Platform | aigpre.com
If spam or not genuine: respond IGNORE"""

def decode_str(s):
    if not s: return ""
    parts = decode_header(s)
    result = ""
    for part, enc in parts:
        if isinstance(part, bytes): result += part.decode(enc or "utf-8", errors="ignore")
        else: result += str(part)
    return result.strip()

def get_body(msg):
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try: body = part.get_payload(decode=True).decode("utf-8", errors="ignore"); break
                except: pass
    else:
        try: body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")
        except: body = str(msg.get_payload())
    return body[:3000].strip()

def get_ai_reply(sender_email, sender_name, subject, body):
    prompt = f"FROM: {sender_name} <{sender_email}>\nSUBJECT: {subject}\nMESSAGE:\n{body}\n\nDraft professional reply or respond IGNORE if not genuine."
    try:
        r = httpx.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_KEY,"anthropic-version":"2023-06-01","content-type":"application/json"},
            json={"model":"claude-haiku-4-5-20251001","max_tokens":600,"system":SYSTEM_PROMPT,
                  "messages":[{"role":"user","content":prompt}]},timeout=30.0)
        data = r.json()
        if r.status_code == 200 and "content" in data: return data["content"][0]["text"]
        print(f"[AI ERROR] {data.get('error',{}).get('message',str(data))}")
        return None
    except Exception as e:
        print(f"[AI ERROR] {e}"); return None

def smtp_send(to_addr, subject, body):
    msg = MIMEMultipart("alternative")
    msg["From"] = f"AIGPRE Global Trade <{SMTP_USER}>"
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Reply-To"] = MONITOR_EMAIL
    msg.attach(MIMEText(body,"plain"))
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
            s.ehlo()
            s.starttls()
            s.ehlo()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_USER, to_addr, msg.as_string())
        return True
    except Exception as e:
        print(f"[SMTP ERROR] {e}")
        return False

def process_inbox():
    now = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{now}] Checking {MONITOR_EMAIL}...")
    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(MONITOR_EMAIL, MONITOR_PASS)
        mail.select("INBOX")
        _, ids = mail.search(None, "UNSEEN")
        uid_list = ids[0].split()
        if not uid_list:
            print("  No new messages.")
            mail.logout()
            return
        print(f"  {len(uid_list)} new message(s).")
        for uid in uid_list:
            _, data = mail.fetch(uid, "(RFC822)")
            msg = email.message_from_bytes(data[0][1])
            subject = decode_str(msg.get("Subject","(No Subject)"))
            sender_raw = decode_str(msg.get("From",""))
            body = get_body(msg)
            if "<" in sender_raw and ">" in sender_raw:
                sender_email = sender_raw.split("<")[1].split(">")[0].strip()
                sender_name = sender_raw.split("<")[0].strip().strip('"') or sender_email
            else:
                sender_email = sender_raw; sender_name = sender_raw
            print(f"  → {sender_name} <{sender_email}> | {subject[:50]}")
            skip = [MONITOR_EMAIL.lower(), OPS_EMAIL.lower(), SMTP_USER.lower(), "mailer-daemon", "postmaster"]
            if any(x in sender_email.lower() for x in skip):
                print("  Skipped — internal.")
                mail.store(uid,"+FLAGS","\\Seen")
                continue
            reply = get_ai_reply(sender_email, sender_name, subject, body)
            if not reply or reply.strip().upper().startswith("IGNORE"):
                print("  Not genuine — skipped.")
                mail.store(uid,"+FLAGS","\\Seen")
                continue
            sent = smtp_send(sender_email, f"Re: {subject}", reply)
            if sent: print(f"  ✅ AI reply sent → {sender_email}")
            else: print(f"  ❌ SMTP failed")
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            notif = f"AIGPRE NEW INQUIRY\nFROM: {sender_name}\nEMAIL: {sender_email}\nSUBJECT: {subject}\nTIME: {now_str}\n\nORIGINAL:\n{body[:800]}\n\nAI REPLY:\n{reply}\n\nACTION: Review and decide next steps.\nAIGPRE Trade Intelligence | aigpre.com"
            smtp_send(OPS_EMAIL, f"[NEW INQUIRY] {sender_name} — {subject[:50]}", notif)
            print(f"  ✅ Ops notified → {OPS_EMAIL}")
            mail.store(uid,"+FLAGS","\\Seen")
        mail.logout()
    except Exception as e:
        print(f"  [ERROR] {e}")

if __name__ == "__main__":
    print("AIGPRE Email AI — Railway Production")
    print(f"Monitor : {MONITOR_EMAIL}")
    print(f"SMTP    : {SMTP_HOST}:{SMTP_PORT} via {SMTP_USER}")
    print(f"Ops     : {OPS_EMAIL}")
    cycle = 0
    while True:
        cycle += 1
        print(f"\n[CYCLE #{cycle}]")
        process_inbox()
        print(f"Next check in {CHECK_INTERVAL}s...")
        time.sleep(CHECK_INTERVAL)
