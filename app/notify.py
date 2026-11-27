"""Email (SMTP) and WhatsApp (Meta WhatsApp Cloud API) notifications.

Both channels are optional and switch on only when their settings are present.
Messages are sent from a background thread so a slow mail server never blocks a page.
"""
import json
import logging
import re
import smtplib
import ssl
import threading
import urllib.error
import urllib.request
from email.message import EmailMessage

log = logging.getLogger(__name__)


def email_enabled(cfg):
    return bool(cfg.get("SMTP_HOST") and cfg.get("SMTP_FROM"))


def whatsapp_enabled(cfg):
    return bool(cfg.get("WHATSAPP_TOKEN") and cfg.get("WHATSAPP_PHONE_NUMBER_ID"))


def send_email(cfg, to, subject, body):
    msg = EmailMessage()
    msg["From"] = cfg["SMTP_FROM"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    port = int(cfg.get("SMTP_PORT") or 587)
    context = ssl.create_default_context()
    if cfg.get("SMTP_SSL"):
        server = smtplib.SMTP_SSL(cfg["SMTP_HOST"], port, timeout=30, context=context)
    else:
        server = smtplib.SMTP(cfg["SMTP_HOST"], port, timeout=30)
        if cfg.get("SMTP_STARTTLS", True):
            server.starttls(context=context)
    with server:
        if cfg.get("SMTP_USER"):
            server.login(cfg["SMTP_USER"], cfg.get("SMTP_PASSWORD", ""))
        server.send_message(msg)


def send_whatsapp(cfg, phone, text):
    """Send via WhatsApp Cloud API.

    Business-initiated messages must use a pre-approved template. Configure
    WHATSAPP_TEMPLATE with a template whose body is a single variable ({{1}});
    the notification text is placed in it. Without a template a plain text
    message is sent, which WhatsApp only delivers within 24 h of the user's last message.
    """
    to = re.sub(r"\D", "", phone or "")
    if not to:
        return
    one_line = " · ".join(line.strip() for line in text.splitlines() if line.strip())[:1000]
    if cfg.get("WHATSAPP_TEMPLATE"):
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {
                "name": cfg["WHATSAPP_TEMPLATE"],
                "language": {"code": cfg.get("WHATSAPP_TEMPLATE_LANG") or "sk"},
                "components": [{"type": "body", "parameters": [{"type": "text", "text": one_line}]}],
            },
        }
    else:
        payload = {"messaging_product": "whatsapp", "to": to, "type": "text",
                   "text": {"body": text[:4000]}}
    version = cfg.get("WHATSAPP_API_VERSION") or "v21.0"
    url = f"https://graph.facebook.com/{version}/{cfg['WHATSAPP_PHONE_NUMBER_ID']}/messages"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": f"Bearer {cfg['WHATSAPP_TOKEN']}",
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()


def deliver(cfg, recipients, build):
    """recipients: user rows/dicts. build(lang) -> (subject, body). Runs synchronously."""
    results = []
    for user in recipients:
        lang = user["language"] or cfg.get("DEFAULT_LANGUAGE", "sk")
        subject, body = build(lang)
        if user["notify_email"] and user["email"] and email_enabled(cfg):
            try:
                send_email(cfg, user["email"], subject, body)
                results.append((user["username"], "email", "ok"))
            except (OSError, smtplib.SMTPException) as exc:
                log.warning("email to %s failed: %s", user["username"], exc)
                results.append((user["username"], "email", str(exc)))
        if user["notify_whatsapp"] and user["phone"] and whatsapp_enabled(cfg):
            try:
                send_whatsapp(cfg, user["phone"], f"{subject}\n{body}")
                results.append((user["username"], "whatsapp", "ok"))
            except (OSError, urllib.error.URLError) as exc:
                log.warning("whatsapp to %s failed: %s", user["username"], exc)
                results.append((user["username"], "whatsapp", str(exc)))
    return results


def notify(app, recipients, build):
    """Fire-and-forget delivery in a background thread."""
    recipients = [dict(r) for r in recipients]
    if not recipients:
        return
    cfg = dict(app.config)
    if cfg.get("TESTING"):
        app.extensions.setdefault("sent_notifications", []).extend(
            (r["username"],) + build(r["language"] or cfg.get("DEFAULT_LANGUAGE", "sk"))
            for r in recipients)
        return
    threading.Thread(target=deliver, args=(cfg, recipients, build), daemon=True).start()
