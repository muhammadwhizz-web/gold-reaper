"""
GOLD REAPER APEX :: Notify — Telegram / Discord / Email
========================================================
Fan-out announcer. Every channel is optional; configured channels receive:
every kill order, every target hit, every breaker latch, every crash.

Env:
  TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID
  DISCORD_WEBHOOK_URL
  SMTP_HOST/PORT/USER/PASS + ALERT_EMAIL_FROM/TO
"""
from __future__ import annotations

import html
import json
import os
import smtplib
import ssl
import threading
import urllib.request
from email.mime.text import MIMEText


def _post_json(url: str, payload: dict, timeout: int = 10) -> bool:
    try:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 300
    except Exception:  # noqa: BLE001
        return False


def telegram(title: str, text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        return False
    # parse_mode HTML: raw veto/reasoning text with '<', '&' or stray tags
    # made Telegram answer 400 and the alert was silently lost - escape
    # everything (we only ever need plain text)
    msg = f"☠️ {html.escape(title)}\n{html.escape(text)}"[:4000]
    return _post_json(f"https://api.telegram.org/bot{token}/sendMessage",
                      {"chat_id": chat, "text": msg,
                       "parse_mode": "HTML"})


def discord(title: str, text: str) -> bool:
    url = os.getenv("DISCORD_WEBHOOK_URL", "")
    if not url:
        return False
    return _post_json(url, {
        "username": "GOLD REAPER",
        "embeds": [{"title": f"☠️ {title}", "description": text[:2000],
                    "color": 0xFF1744}]})


def email(title: str, text: str) -> bool:
    host = os.getenv("SMTP_HOST", "")
    if not host:
        return False
    try:
        port = int(os.getenv("SMTP_PORT", "587"))
        user = os.getenv("SMTP_USER", "")
        pwd = os.getenv("SMTP_PASS", "")
        frm = os.getenv("ALERT_EMAIL_FROM", user)
        to = os.getenv("ALERT_EMAIL_TO", "")
        if not to:
            return False
        msg = MIMEText(text)
        msg["Subject"] = f"[GOLD-REAPER] {title}"
        msg["From"], msg["To"] = frm, to
        with smtplib.SMTP(host, port, timeout=15) as s:
            # verified TLS: the default stdlib context does NOT validate
            # certificates, letting an on-path attacker MITM STARTTLS and
            # read SMTP_USER/SMTP_PASS plus every alert body
            s.starttls(context=ssl.create_default_context())
            if user:
                s.login(user, pwd)
            s.sendmail(frm, [to], msg.as_string())
        return True
    except Exception:  # noqa: BLE001
        return False


def _fan_out(title: str, text: str) -> dict:
    report = {
        "telegram": telegram(title, text),
        "discord": discord(title, text),
        "email": email(title, text),
    }
    return report


def notify(title: str, text: str = "") -> dict:
    """Fan out to every configured channel WITHOUT blocking the bot loop.

    The three sequential network sends (10s/10s/15s worst case) used to
    stall the tick loop - SL enforcement and heartbeats included. The
    fan-out now runs on a daemon thread; the returned report is a
    best-effort snapshot (channels deliver asynchronously)."""
    cfg_present = any([
        os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"),
        os.getenv("DISCORD_WEBHOOK_URL"),
        os.getenv("SMTP_HOST"),
    ])
    if not cfg_present:
        return {"telegram": False, "discord": False, "email": False}
    t = threading.Thread(target=_fan_out, args=(title, text),
                         daemon=True, name="reaper-notify")
    t.start()
    print(f"[NOTIFY] {title} | dispatched async")
    return {"telegram": None, "discord": None, "email": None,
            "async": True}
