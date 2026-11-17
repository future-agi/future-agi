import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

from agentcc.services.url_safety import smtp_connect_addresses
from integrations.services.credentials import CredentialManager

logger = logging.getLogger(__name__)

# The fixed failure messages of this module. A failed send's "error" is one of
# these and nothing else, and so is what the dispatcher stores in last_error
# (read by every org Member) and what the console's Send test answers
# (public_error): a provider's or a mail server's own text can quote what the
# alert's settings do not show (a password, an API key). That text comes back
# as "detail", for the server log only.
NO_RECIPIENTS = "No recipients configured."
INVALID_CONFIG = "Invalid provider configuration."
SMTP_FIELDS_REQUIRED = "SMTP host and from_email are required."
SMTP_HOST_REFUSED = (
    "The SMTP host was refused: it does not resolve, or it resolves to a "
    "loopback, link-local, cloud metadata or private address, or the port is "
    "a database port."
)
SENDGRID_KEY_MISSING = "SendGrid API key not configured."
RESEND_KEY_MISSING = "Resend API key not configured."
SMTP_CONNECT_FAILED = "Could not connect to the SMTP server."
SMTP_AUTH_FAILED = "The SMTP server did not accept the username and password."
SMTP_REFUSED = "The SMTP server refused the sender, a recipient or the message."
PROVIDER_REFUSED = (
    "The email provider refused the request. Check the API key and the from address."
)
PROVIDER_UNREACHABLE = "Could not reach the email provider."
SEND_FAILED = (
    "The email could not be sent. Check the alert's provider settings and try again."
)
_PUBLIC_ERRORS = frozenset(
    {
        NO_RECIPIENTS,
        INVALID_CONFIG,
        SMTP_FIELDS_REQUIRED,
        SMTP_HOST_REFUSED,
        SENDGRID_KEY_MISSING,
        RESEND_KEY_MISSING,
        SMTP_CONNECT_FAILED,
        SMTP_AUTH_FAILED,
        SMTP_REFUSED,
        PROVIDER_REFUSED,
        PROVIDER_UNREACHABLE,
        SEND_FAILED,
    }
)


def public_error(error):
    """What a user may be shown, and last_error may hold, for a failed send:
    this module's fixed message for it, else SEND_FAILED. Never the provider's
    or the server's own text (an older last_error may still hold one)."""
    return error if error in _PUBLIC_ERRORS else SEND_FAILED


_SMTP_REFUSALS = (
    smtplib.SMTPSenderRefused,
    smtplib.SMTPRecipientsRefused,
    smtplib.SMTPDataError,
)


def _failure_reason(provider, exc):
    """The fixed message for a send that raised ``exc``."""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return SMTP_AUTH_FAILED
    if isinstance(exc, _SMTP_REFUSALS):
        return SMTP_REFUSED
    if isinstance(exc, smtplib.SMTPConnectError):
        return SMTP_CONNECT_FAILED
    if isinstance(exc, requests.RequestException):
        return PROVIDER_UNREACHABLE
    if isinstance(exc, smtplib.SMTPException | ssl.SSLError):
        return SEND_FAILED
    if provider == "smtp" and isinstance(exc, OSError):
        # Refused, unreachable or timed out at every checked address.
        return SMTP_CONNECT_FAILED
    return SEND_FAILED


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

    Never raises: a failure comes back as {"success": False, "error", "detail"},
    where "error" is one of this module's fixed messages (public_error) and
    "detail", when there is one, the provider's or the server's own text for
    the server log only.
    """
    if not recipients:
        return {"success": False, "error": NO_RECIPIENTS}

    config = {}
    if alert.encrypted_config:
        try:
            config = CredentialManager.decrypt(bytes(alert.encrypted_config))
        except Exception:
            return {"success": False, "error": INVALID_CONFIG}

    try:
        if alert.provider == "sendgrid":
            result = _send_via_sendgrid(config, recipients, subject, body)
        elif alert.provider == "resend":
            result = _send_via_resend(config, recipients, subject, body)
        elif alert.provider == "smtp":
            result = _send_via_smtp(config, recipients, subject, body)
        else:
            result = {
                "success": False,
                "error": SEND_FAILED,
                "detail": f"Unknown provider: {alert.provider}",
            }
    except Exception as e:
        logger.warning(
            "alert_email_provider_failed",
            extra={"alert_id": str(alert.id), "provider": alert.provider},
            exc_info=True,
        )
        return {
            "success": False,
            "error": _failure_reason(alert.provider, e),
            "detail": f"{type(e).__name__}: {e}",
        }
    if result.get("success"):
        return result
    error = result.get("error")
    return {
        "success": False,
        "error": public_error(error),
        "detail": result.get("detail") or error,
    }


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
        return {"success": False, "error": SENDGRID_KEY_MISSING}

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
        "error": PROVIDER_REFUSED,
        "detail": f"SendGrid error (HTTP {resp.status_code}): {resp.text[:200]}",
    }


def _send_via_resend(config, recipients, subject, body):
    """Send email via Resend API."""
    api_key = config.get("api_key", "")
    from_email = config.get("from_email", "alerts@agentcc-gateway.com")

    if not api_key:
        return {"success": False, "error": RESEND_KEY_MISSING}

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
        "error": PROVIDER_REFUSED,
        "detail": f"Resend error (HTTP {resp.status_code}): {resp.text[:200]}",
    }


class _PinnedSMTP(smtplib.SMTP):
    """smtplib.SMTP that opens its socket to the addresses checked beforehand.

    ``addresses`` are tried in order, as socket.create_connection tries a
    name's addresses: one that refuses, is unreachable or times out falls
    through to the next, and the last one's error is raised. The name given to
    ``__init__`` stays the server's name for STARTTLS (SNI) and the messages;
    only the socket goes to an address, so the name is not resolved again after
    the check.
    """

    def __init__(self, addresses, **kwargs):
        self._pinned_addresses = list(addresses)
        super().__init__(**kwargs)

    def _get_socket(self, host, port, timeout):
        if not self._pinned_addresses:
            raise OSError("no checked address to connect to")
        for address in self._pinned_addresses[:-1]:
            try:
                return super()._get_socket(address, port, timeout)
            except OSError:
                continue
        return super()._get_socket(self._pinned_addresses[-1], port, timeout)


def _send_via_smtp(config, recipients, subject, body):
    """Send email via SMTP, to the addresses the host was checked at."""
    host = config.get("host", "")
    port = config.get("port", 587)
    username = config.get("username", "")
    password = config.get("password", "")
    from_email = config.get("from_email", "")
    use_tls = config.get("use_tls", True)

    if not host or not from_email:
        return {"success": False, "error": SMTP_FIELDS_REQUIRED}

    host = str(host).strip()
    try:
        addresses = smtp_connect_addresses(host, port)
    except ValueError:
        return {"success": False, "error": SMTP_HOST_REFUSED}

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(body, "html"))

    server = None
    try:
        server = _PinnedSMTP(addresses, host=host, port=port, timeout=15)
        if use_tls:
            server.starttls(context=ssl.create_default_context())
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
