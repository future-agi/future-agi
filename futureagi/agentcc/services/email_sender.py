import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

from integrations.services.credentials import CredentialManager

logger = logging.getLogger(__name__)


def send_alert_email(alert, event_type, context=None, recipient_override=None):
    """Send one alert email for ``event_type`` now.

    Kept for callers outside the dispatcher. It neither checks nor spends the
    cooldown: alert_dispatcher.dispatch_pending owns the cooldown and
    last_triggered_at for real alerts, and the console's Send test uses
    send_test_email.

    Returns a dict with success and, on failure, error.
    """
    recipients = [recipient_override] if recipient_override else alert.recipients
    subject = f"[Agentcc] Alert: {event_label(event_type)}"
    body = _render_email_body(alert.name, event_type, context or {})
    return deliver_email(alert, recipients, subject, body)


def send_test_email(alert, recipient_override=None):
    """The console's Send test: always sent, never in or against the cooldown."""
    recipients = [recipient_override] if recipient_override else alert.recipients
    body = _render_email_body(
        alert.name,
        "test.email",
        {"message": "This is a test email from Agentcc Gateway."},
    )
    return deliver_email(alert, recipients, "[Agentcc] Alert: Test Email", body)


def send_alert_digest(alert, pending):
    """Send an alert's pending events (alert_dispatcher state) as one email."""
    event_types = sorted(pending)
    labels = [event_label(event) for event in event_types]
    if len(labels) == 1:
        subject = f"[Agentcc] Alert: {labels[0]}"
    else:
        subject = f"[Agentcc] Alert: {alert.name}: {', '.join(labels)}"
    body = _render_digest_body(
        alert, [(event, pending[event]) for event in event_types]
    )
    return deliver_email(alert, alert.recipients, subject, body)


def event_label(event_type):
    from agentcc.models.email_alert import AgentccEmailAlert

    labels = dict(AgentccEmailAlert.EVENT_CHOICES)
    labels["test.email"] = "Test Email"
    return labels.get(event_type) or str(event_type).replace(".", " ").title()


def deliver_email(alert, recipients, subject, body):
    """Send one email through the alert's stored provider (SMTP, SendGrid, Resend).

    Never raises: a provider failure comes back as {"success": False, "error"}.
    """
    if not recipients:
        return {"success": False, "error": "No recipients configured."}

    config = {}
    if alert.encrypted_config:
        try:
            config = CredentialManager.decrypt(bytes(alert.encrypted_config))
        except Exception:
            return {"success": False, "error": "Invalid provider configuration."}

    try:
        if alert.provider == "sendgrid":
            return _send_via_sendgrid(config, recipients, subject, body)
        if alert.provider == "resend":
            return _send_via_resend(config, recipients, subject, body)
        if alert.provider == "smtp":
            return _send_via_smtp(config, recipients, subject, body)
        return {"success": False, "error": f"Unknown provider: {alert.provider}"}
    except Exception as e:
        logger.warning(
            "alert_email_provider_failed",
            extra={"alert_id": str(alert.id), "provider": alert.provider},
            exc_info=True,
        )
        return {"success": False, "error": f"{type(e).__name__}: {e}"}


def _render_digest_body(alert, items):
    """The HTML body for one or more pending events (ASCII-only template)."""
    import html

    from agentcc.services.alert_dispatcher import THRESHOLD_EVENTS

    sections = []
    for event_type, item in items:
        # A threshold event is recorded once per minute while it holds.
        count_label = (
            "Checks over threshold" if event_type in THRESHOLD_EVENTS else "Occurrences"
        )
        rows = {
            "Event": f"{event_label(event_type)} ({event_type})",
            count_label: item.get("count") or 1,
            "First seen (UTC)": item.get("first_at") or "",
            "Last seen (UTC)": item.get("last_at") or "",
            **(item.get("context") or {}),
        }
        cells = "".join(
            f"<tr><td style='padding:4px 8px;font-weight:bold'>{html.escape(str(k))}</td>"
            f"<td style='padding:4px 8px'>{html.escape(str(v))}</td></tr>"
            for k, v in rows.items()
            if v not in ("", None)
        )
        sections.append(
            f"<table style='margin-top:12px;border-collapse:collapse'>{cells}</table>"
        )
    cooldown = (
        f"At most one email every {alert.cooldown_minutes} minutes for this alert;"
        " events in between are sent together in the next email."
        if alert.cooldown_minutes > 0
        else "This alert has no cooldown."
    )
    return f"""
    <div style="font-family:sans-serif;max-width:600px;margin:0 auto">
        <div style="background:#1a1a2e;color:#fff;padding:16px 24px;border-radius:8px 8px 0 0">
            <h2 style="margin:0;font-size:18px">Agentcc Gateway Alert</h2>
        </div>
        <div style="padding:24px;border:1px solid #e0e0e0;border-top:none;border-radius:0 0 8px 8px">
            <p style="margin:0 0 8px"><strong>Alert:</strong> {html.escape(str(alert.name))}</p>
            {"".join(sections)}
            <hr style="margin:16px 0;border:none;border-top:1px solid #e0e0e0">
            <p style="color:#888;font-size:12px;margin:0">
                {html.escape(cooldown)} Manage alerts in Gateway Settings.
            </p>
        </div>
    </div>
    """


def _render_email_body(alert_name, event_type, context):
    """Render a simple HTML email body."""
    import html

    alert_name = html.escape(str(alert_name))
    event_type = html.escape(str(event_type))

    details_html = ""
    if context:
        rows = "".join(
            f"<tr><td style='padding:4px 8px;font-weight:bold'>{html.escape(str(k))}</td>"
            f"<td style='padding:4px 8px'>{html.escape(str(v))}</td></tr>"
            for k, v in context.items()
        )
        details_html = (
            f"<table style='margin-top:12px;border-collapse:collapse'>{rows}</table>"
        )

    return f"""
    <div style="font-family:sans-serif;max-width:600px;margin:0 auto">
        <div style="background:#1a1a2e;color:#fff;padding:16px 24px;border-radius:8px 8px 0 0">
            <h2 style="margin:0;font-size:18px">Agentcc Gateway Alert</h2>
        </div>
        <div style="padding:24px;border:1px solid #e0e0e0;border-top:none;border-radius:0 0 8px 8px">
            <p style="margin:0 0 8px"><strong>Alert:</strong> {alert_name}</p>
            <p style="margin:0 0 8px"><strong>Event:</strong> {event_type}</p>
            {details_html}
            <hr style="margin:16px 0;border:none;border-top:1px solid #e0e0e0">
            <p style="color:#888;font-size:12px;margin:0">
                This alert was sent by Agentcc Gateway. Manage alerts in your dashboard settings.
            </p>
        </div>
    </div>
    """


def _send_via_sendgrid(config, recipients, subject, body):
    """Send email via SendGrid API."""
    api_key = config.get("api_key", "")
    from_email = config.get("from_email", "alerts@agentcc-gateway.com")

    if not api_key:
        return {"success": False, "error": "SendGrid API key not configured."}

    personalizations = [{"to": [{"email": r} for r in recipients]}]

    resp = requests.post(
        "https://api.sendgrid.com/v3/mail/send",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "personalizations": personalizations,
            "from": {"email": from_email},
            "subject": subject,
            "content": [{"type": "text/html", "value": body}],
        },
        timeout=15,
    )

    if resp.status_code in (200, 202):
        return {"success": True}
    return {
        "success": False,
        "error": f"SendGrid error (HTTP {resp.status_code}): {resp.text[:200]}",
    }


def _send_via_resend(config, recipients, subject, body):
    """Send email via Resend API."""
    api_key = config.get("api_key", "")
    from_email = config.get("from_email", "alerts@agentcc-gateway.com")

    if not api_key:
        return {"success": False, "error": "Resend API key not configured."}

    resp = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "from": from_email,
            "to": recipients,
            "subject": subject,
            "html": body,
        },
        timeout=15,
    )

    if resp.status_code in (200, 201):
        return {"success": True}
    return {
        "success": False,
        "error": f"Resend error (HTTP {resp.status_code}): {resp.text[:200]}",
    }


def _send_via_smtp(config, recipients, subject, body):
    """Send email via SMTP."""
    host = config.get("host", "")
    port = config.get("port", 587)
    username = config.get("username", "")
    password = config.get("password", "")
    from_email = config.get("from_email", "")
    use_tls = config.get("use_tls", True)

    if not host or not from_email:
        return {"success": False, "error": "SMTP host and from_email are required."}

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(body, "html"))

    server = None
    try:
        server = smtplib.SMTP(host, port, timeout=15)
        if use_tls:
            server.starttls()
        if username and password:
            server.login(username, password)
        server.sendmail(from_email, recipients, msg.as_string())
        return {"success": True}
    finally:
        if server:
            try:
                server.quit()
            except Exception:
                pass
