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

import json
import os
import smtplib
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
    msg = f"☠️ {title}\n{text}"[:4000]
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
            s.starttls()
            if user:
                s.login(user, pwd)
            s.sendmail(frm, [to], msg.as_string())
        return True
    except Exception:  # noqa: BLE001
        return False


def notify(title: str, text: str = "") -> dict:
    """Fan out to every configured channel. Returns delivery report."""
    report = {
        "telegram": telegram(title, text),
        "discord": discord(title, text),
        "email": email(title, text),
    }
    if not any(report.values()):
        return report  # silent: no channels configured
    print(f"[NOTIFY] {title} | "
          + " ".join(f"{k}={'✓' if v else '✗'}" for k, v in report.items()))
    return report
