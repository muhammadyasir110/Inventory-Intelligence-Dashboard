"""
core/email.py
--------------
Transactional e-mail (invitations, password resets, e-mail verification).

Backends (IIP_EMAIL_BACKEND):
  smtp      Real delivery through any SMTP provider (Amazon SES, SendGrid,
            Mailgun, Postmark, Microsoft 365, ...). A message is recorded as
            "sent" only when the SMTP server ACCEPTED it; that is not proof
            that it reached the inbox.
  console   Development/test only. Nothing is sent: the message is written to
            the application log and kept in memory (``console_messages``) so a
            developer can open the link. Recorded as "not_sent_dev_console".
            Refused in staging/production (core/settings.py).
  disabled  Nothing is sent; recorded as "not_sent_no_provider". Default in
            staging/production until a provider is configured. Account flows
            still answer generically, and administrators can still reset
            passwords and share invitation links by hand.

SMTP settings: IIP_SMTP_HOST, IIP_SMTP_PORT (587), IIP_SMTP_USERNAME,
IIP_SMTP_PASSWORD, IIP_SMTP_STARTTLS (true), IIP_SMTP_SSL (false),
IIP_SMTP_TIMEOUT_S (10), IIP_EMAIL_FROM. Links use IIP_PUBLIC_BASE_URL.

Every attempt is written to ``email_outbox`` with its honest status. Message
bodies are never stored (they contain single-use links), and the SMTP
password is never logged.
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from collections import deque
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Optional

log = logging.getLogger("iip.email")

STATUSES = ("sent", "failed", "not_sent_dev_console", "not_sent_no_provider")
console_messages: deque = deque(maxlen=200)   # development/test inspection only


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def backend_name() -> str:
    from core.settings import settings
    default = "disabled" if settings.is_production else "console"
    name = _env("IIP_EMAIL_BACKEND", default).lower()
    if name not in ("smtp", "console", "disabled"):
        raise RuntimeError(f"IIP_EMAIL_BACKEND must be smtp, console or disabled, got {name!r}")
    if name == "console" and settings.is_production:
        raise RuntimeError("IIP_EMAIL_BACKEND=console is for development only.")
    return name


def public_base_url() -> str:
    return _env("IIP_PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def status() -> dict:
    """What the UI and administrators are told about e-mail delivery."""
    b = backend_name()
    return {
        "backend": b,
        "delivers_email": b == "smtp",
        "message": {
            "smtp": "E-mail is sent through the configured SMTP provider.",
            "console": "Development mode: e-mails are NOT sent; links are written to the server log.",
            "disabled": "E-mail is not configured: no e-mails are sent. Administrators share "
                        "invitation links and reset passwords by hand.",
        }[b],
    }


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _smtp_send(msg: EmailMessage) -> None:
    host = _env("IIP_SMTP_HOST")
    if not host:
        raise RuntimeError("IIP_SMTP_HOST is not set")
    port = int(_env("IIP_SMTP_PORT", "587"))
    timeout = float(_env("IIP_SMTP_TIMEOUT_S", "10"))
    use_ssl = _env("IIP_SMTP_SSL", "false").lower() in ("1", "true", "yes")
    starttls = _env("IIP_SMTP_STARTTLS", "true").lower() in ("1", "true", "yes")
    user, pw = _env("IIP_SMTP_USERNAME"), os.getenv("IIP_SMTP_PASSWORD") or ""
    ctx = ssl.create_default_context()
    if use_ssl:
        server = smtplib.SMTP_SSL(host, port, timeout=timeout, context=ctx)
    else:
        server = smtplib.SMTP(host, port, timeout=timeout)
    try:
        server.ehlo()
        if starttls and not use_ssl:
            server.starttls(context=ctx)
            server.ehlo()
        if user:
            server.login(user, pw)
        refused = server.send_message(msg)
        if refused:
            raise RuntimeError(f"recipient refused by SMTP server ({len(refused)})")
    finally:
        try:
            server.quit()
        except Exception:
            pass


def send(db, *, to: str, template: str, subject: str, body: str,
         org_id: Optional[int] = None) -> str:
    """Send (or honestly not send) one e-mail; returns the recorded status.
    The caller commits. Never raises for delivery problems."""
    from models.models import EmailOutbox
    b = backend_name()
    row = EmailOutbox(org_id=org_id, to_address=to[:255], template=template, subject=subject[:255],
                      backend=b, status="failed", created_at=_utcnow())
    if b == "console":
        console_messages.append({"to": to, "template": template, "subject": subject, "body": body})
        log.warning("DEVELOPMENT E-MAIL - NOT SENT. to=%s template=%s\n%s", to, template, body)
        row.status = "not_sent_dev_console"
    elif b == "disabled":
        row.status = "not_sent_no_provider"
    else:
        msg = EmailMessage()
        msg["From"] = _env("IIP_EMAIL_FROM", "no-reply@localhost")
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        try:
            _smtp_send(msg)
            row.status, row.sent_at = "sent", _utcnow()
        except Exception as e:                                   # delivery problems are recorded, not raised
            row.status = "failed"
            row.error = f"{type(e).__name__}: {str(e)[:400]}"
            log.error("e-mail delivery failed template=%s error=%s", template, row.error)
    db.add(row)
    try:
        from core.metrics import metrics
        metrics.inc("iip_emails_total", status=row.status)
    except Exception:
        pass
    return row.status


# ---------------------------------------------------------------- templates

def _footer() -> str:
    return ("\n\nIf you did not expect this e-mail you can ignore it.\n"
            "- Inventory Intelligence Platform")


def invitation(org_name: str, inviter: str, role_label: str, link: str, days: int) -> tuple[str, str]:
    return (f"You are invited to {org_name} on Inventory Intelligence Platform",
            f"{inviter} invited you to join {org_name} as {role_label}.\n\n"
            f"Accept the invitation (valid for {days} days, single use):\n{link}" + _footer())


def password_reset(link: str, minutes: int) -> tuple[str, str]:
    return ("Reset your Inventory Intelligence Platform password",
            f"A password reset was requested for your account.\n\n"
            f"Choose a new password (link valid for {minutes} minutes, single use):\n{link}\n\n"
            "All your sessions will be signed out after the reset." + _footer())


def verify_email(link: str, hours: int) -> tuple[str, str]:
    return ("Confirm your e-mail address",
            f"Please confirm your e-mail address (link valid for {hours} hours):\n{link}" + _footer())
