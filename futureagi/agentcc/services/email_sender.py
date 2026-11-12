import logging
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

from integrations.services.credentials import CredentialManager
from tfc.temporal.drop_in import temporal_activity

logger = logging.getLogger(__name__)


def send_alert_email(
    alert, event_type, context=None, recipient_override=None, check_cooldown=True
):
    """Send an alert email using the configured provider.

    Args:
        alert: AgentccEmailAlert instance.
        event_type: The event type string that triggered this alert.
        context: Optional dict with event-specific details.
        recipient_override: Optional single email address for test sends.
        check_cooldown: False when the caller has already started the cooldown.

    Returns:
        dict with success status and any error message.
    """
    recipients = [recipient_override] if recipient_override else alert.recipients
    if not recipients:
        return {"success": False, "error": "No recipients configured."}

    # Decrypt provider config
    config = {}
    if alert.encrypted_config:
        try:
            config = CredentialManager.decrypt(bytes(alert.encrypted_config))
        except Exception:
            return {"success": False, "error": "Invalid provider configuration."}

    # Check cooldown
    if check_cooldown and not recipient_override and alert.last_triggered_at:
        elapsed = (datetime.now(timezone.utc) - alert.last_triggered_at).total_seconds()
        if elapsed < alert.cooldown_minutes * 60:
            return {"success": False, "error": "Alert is in cooldown period."}

    subject = f"[Agentcc] Alert: {event_type.replace('.', ' ').title()}"
    body = _render_email_body(alert.name, event_type, context or {})

    try:
        if alert.provider == "sendgrid":
            result = _send_via_sendgrid(config, recipients, subject, body)
        elif alert.provider == "resend":
            result = _send_via_resend(config, recipients, subject, body)
        elif alert.provider == "smtp":
            result = _send_via_smtp(config, recipients, subject, body)
        else:
            return {"success": False, "error": f"Unknown provider: {alert.provider}"}

        # Update last_triggered_at on success (skip for test sends)
        if result.get("success") and not recipient_override:
            alert.last_triggered_at = datetime.now(timezone.utc)
            alert.save(update_fields=["last_triggered_at"])

        return result

    except Exception as e:
        logger.exception("Failed to send alert email")
        return {"success": False, "error": str(e)}


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


@temporal_activity(max_retries=0, time_limit=120, queue="default")
def send_alert_email_activity(alert_id, event_type, context):
    """Send one alert email off the request path.

    Log ingestion started the alert's cooldown when it fired, so this sends
    without checking it again, and a provider that fails is not retried until
    the cooldown ends.
    """
    from agentcc.models import AgentccEmailAlert

    alert = AgentccEmailAlert.no_workspace_objects.filter(
        id=alert_id, is_active=True, deleted=False
    ).first()
    if alert is None:
        return
    result = send_alert_email(alert, event_type, context, check_cooldown=False)
    if not result.get("success"):
        logger.warning(
            "Alert email %s for %s not sent: %s",
            alert_id,
            event_type,
            result.get("error"),
        )
