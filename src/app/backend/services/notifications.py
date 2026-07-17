"""Outbound notifications — approval emails with a deep-link back into the app.

Databricks Apps have NO built-in email, so this service SENDS via an SMTP relay only when
SMTP_HOST is configured; otherwise it SIMULATES (logs the rendered email + writes an audit
event) so the approval flow is complete end-to-end without a relay. This is the same
"simulate, then document the seam" pattern the providers use.

To enable real email, set SMTP_HOST/PORT/USER/PASSWORD/FROM (see env.example) and APP_URL
(so the deep-link is absolute). Everything here is best-effort: a notification failure must
NEVER break request creation — callers fire-and-forget.
"""
import logging
import smtplib
from email.mime.text import MIMEText

from .. import config
from ..database import db

logger = logging.getLogger("pave.notifications")


def approval_deep_link(request_id: str) -> str:
    """URL that opens the app straight on this request's approval.

    Absolute when APP_URL is set (what an email needs); otherwise a relative hash the SPA
    still understands. The SPA reads `#approvals/{id}` on boot (see app.js boot()).
    """
    frag = f"#approvals/{request_id}"
    return f"{config.APP_URL}/{frag}" if config.APP_URL else frag


def _render(request: dict, link: str) -> tuple[str, str]:
    pid = request.get("project_id") or request.get("id")
    tier = request.get("risk_tier") or "?"
    name = request.get("project_name") or "(unnamed)"
    requester = request.get("requester") or "unknown"
    subject = f"[PAVE] Approval needed: {name} ({tier})"
    body = (
        f"A new resource request needs your approval.\n\n"
        f"  Project:    {name}\n"
        f"  Project ID: {pid}\n"
        f"  Requester:  {requester}\n"
        f"  Risk tier:  {tier}\n\n"
        f"Review and approve/reject here:\n  {link}\n\n"
        f"— PAVE (Platform Asset Vending Engine)\n"
    )
    return subject, body


def _send_smtp(to_addrs: list[str], subject: str, body: str) -> None:
    """Synchronous SMTP send (wrap in asyncio.to_thread from async callers)."""
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = config.SMTP_FROM
    msg["To"] = ", ".join(to_addrs)
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as s:
        if config.SMTP_USE_TLS:
            s.starttls()
        if config.SMTP_USER:
            s.login(config.SMTP_USER, config.SMTP_PASSWORD)
        s.sendmail(config.SMTP_FROM, to_addrs, msg.as_string())


async def notify_approvers(request: dict, approvers: list[str]) -> dict:
    """Notify approvers a request is pending. Sends real email when SMTP is configured,
    otherwise logs + audits a simulated notification. Returns a summary; never raises."""
    import asyncio

    request_id = str(request.get("id") or request.get("request_id") or "")
    to_addrs = sorted({a.strip().lower() for a in approvers if a and a.strip()})
    link = approval_deep_link(request_id)
    subject, body = _render(request, link)

    if not to_addrs:
        logger.info("no approvers to notify for %s (set APPROVERS)", request_id)
        return {"sent": False, "reason": "no approvers configured", "link": link}

    sent, mode, error = False, "simulated", None
    if config.SMTP_HOST:
        try:
            await asyncio.to_thread(_send_smtp, to_addrs, subject, body)
            sent, mode = True, "smtp"
        except Exception as e:  # noqa: BLE001 — notification must never break the request
            error = str(e)
            logger.warning("SMTP send failed for %s (%s); simulating", request_id, e)
    else:
        # Simulated: log the full email so the flow is visible without a relay.
        logger.info("SIMULATED approval email for %s -> %s\nSubject: %s\n%s",
                    request_id, to_addrs, subject, body)

    try:
        await db.add_audit(actor="system", event_type="notification.approval_requested",
                           request_id=request_id,
                           payload={"to": to_addrs, "mode": mode, "sent": sent,
                                    "link": link, "error": error})
    except Exception as e:  # noqa: BLE001
        logger.warning("audit of notification failed for %s: %s", request_id, e)

    return {"sent": sent, "mode": mode, "to": to_addrs, "link": link, "error": error}
