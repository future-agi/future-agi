import contextlib
import html as _html
import re
import smtplib

import structlog
from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from tfc.ee_loader import is_cloud_env

# Future AGI Cloud's sender and support inbox. A self-hosted install uses
# neither: its mail leaves through the operator's own domain, where a
# futureagi.com sender fails DMARC, and a reply would carry the recipient's
# invite link or generated password to Future AGI.
DEFAULT_FROM_EMAIL = "Future AGI <noreply@mail.futureagi.com>"
DEFAULT_REPLY_TO = "support@futureagi.com"
# Backends that accept a message without delivering it: console is the
# self-hosted default when MAILGUN_API_KEY is empty.
_UNDELIVERED_BACKENDS = frozenset(
    {
        "django.core.mail.backends.console.EmailBackend",
        "django.core.mail.backends.dummy.EmailBackend",
    }
)
logger = structlog.get_logger(__name__)
_WHITESPACE_RE = re.compile(r"\n[ \t]*\n[ \t]*\n+")


def _html_to_text(html_body):
    """Derive a readable plain-text body from a rendered HTML email.

    Preserves line breaks, collapses 3+ blank lines, strips hidden preheader
    content and <style>/<head> noise.
    """
    # Drop <head> and <style> blocks — they contain CSS that would leak into text
    body = re.sub(r"<head[\s\S]*?</head>", "", html_body, flags=re.IGNORECASE)
    body = re.sub(r"<style[\s\S]*?</style>", "", body, flags=re.IGNORECASE)
    # Drop hidden preheader divs so they don't duplicate subject text
    body = re.sub(
        r"<div[^>]*display:\s*none[^>]*>[\s\S]*?</div>", "", body, flags=re.IGNORECASE
    )
    # Replace <br> and block-closing tags with newlines before stripping
    body = re.sub(r"<br\s*/?>", "\n", body, flags=re.IGNORECASE)
    body = re.sub(r"</(p|tr|li|h[1-6]|div|table)>", "\n", body, flags=re.IGNORECASE)
    text = strip_tags(body)
    text = _html.unescape(text)
    # Collapse runs of blank lines; trim trailing whitespace on each line
    text = "\n".join(line.rstrip() for line in text.splitlines())
    text = _WHITESPACE_RE.sub("\n\n", text)
    return text.strip() + "\n"


def email_delivery_configured() -> bool:
    """Whether a sent email reaches its recipient."""
    backend = (getattr(settings, "EMAIL_BACKEND", "") or "").strip()
    return bool(backend) and backend not in _UNDELIVERED_BACKENDS


def _default_from_email():
    """Future AGI's sender on Cloud, whatever DEFAULT_FROM_EMAIL says (Cloud
    sets it to the bare address; mail keeps the "Future AGI" display name).
    Self-hosted: DEFAULT_FROM_EMAIL; else noreply on the Mailgun sending
    domain, the one sender Mailgun accepts there."""
    if is_cloud_env(settings.CLOUD_DEPLOYMENT):
        return DEFAULT_FROM_EMAIL
    configured = (getattr(settings, "DEFAULT_FROM_EMAIL", "") or "").strip()
    if configured:
        return configured
    anymail = getattr(settings, "ANYMAIL", None) or {}
    domain = (anymail.get("MAILGUN_SENDER_DOMAIN") or "").strip()
    return f"Future AGI <noreply@{domain or 'localhost'}>"


def _default_reply_to():
    """DEFAULT_REPLY_TO_EMAIL; else Future AGI support on Cloud; else none."""
    configured = (getattr(settings, "DEFAULT_REPLY_TO_EMAIL", "") or "").strip()
    if configured:
        return [configured]
    if is_cloud_env(settings.CLOUD_DEPLOYMENT):
        return [DEFAULT_REPLY_TO]
    return None


def email_helper(
    mail_subject,
    template_name,
    template_data,
    to_email_list,
    *,
    reply_to=None,
    from_email=None,
    notification_category=None,
    unsubscribe_scope=None,
):
    """Render a Django template and send as a multipart HTML + text email.

    Args:
        mail_subject: Subject line.
        template_name: Django template path (HTML).
        template_data: Context dict.
        to_email_list: List of recipients.
        reply_to: Optional list/str of reply-to addresses. Defaults to
            DEFAULT_REPLY_TO_EMAIL (on Cloud, support@futureagi.com), and to
            no Reply-To when that is empty. Pass an explicit address to
            override, or reply_to=False/[] to disable.
        from_email: Optional sender override. Defaults to DEFAULT_FROM_EMAIL
            (see _default_from_email).
        notification_category: Set for notification (non-transactional) mail,
            one of the categories in ``tfc.utils.email_unsubscribe``. Opted-out
            recipients are dropped, and each remaining recipient gets their own
            message with a signed unsubscribe link (``unsubscribe_url`` in the
            template context) and RFC 8058 one-click ``List-Unsubscribe``
            headers. Leave unset for transactional mail (invites, password
            reset, sign-in), which must always arrive.
        unsubscribe_scope: The monitor id, for monitor alerts (required
            there, and rejected for every other category).

    Returns the number of recipients reached for notification mail (each
    address once, opted-out ones skipped); raises if none could be reached.
    """
    if reply_to is None:
        reply_to_list = _default_reply_to()
    elif reply_to is False or (isinstance(reply_to, (list, tuple)) and not reply_to):
        reply_to_list = None
    else:
        reply_to_list = [reply_to] if isinstance(reply_to, str) else list(reply_to)

    # Templates build their CTAs as base_url|add:"/dashboard/...", and nothing
    # supplies base_url on this path, so the links render host-less.
    context = {"base_url": settings.APP_BASE_URL, **(template_data or {})}
    sender = from_email or _default_from_email()

    if not notification_category:
        _send(
            mail_subject, template_name, context, to_email_list, sender, reply_to_list
        )
        return

    from tfc.utils import email_unsubscribe

    if notification_category not in email_unsubscribe.CATEGORY_LABELS:
        raise ValueError(f"Unknown notification category: {notification_category}")
    if (notification_category == email_unsubscribe.MONITOR_ALERTS) != bool(
        unsubscribe_scope
    ):
        raise ValueError("unsubscribe_scope is the monitor id, for monitor alerts only")

    # Resolve every recipient's preference before sending to anyone, so a
    # lookup failure sends nothing and the caller can retry the whole send.
    links = email_unsubscribe.recipient_links(
        to_email_list, notification_category, unsubscribe_scope
    )
    if not links:
        return 0  # everyone opted out: nothing to send, no connection to open

    # The footer says why this address gets the email, beside the link.
    reason = (
        "You're getting this because this address is on this monitor's "
        "alert recipients."
        if notification_category == email_unsubscribe.MONITOR_ALERTS
        else None
    )

    # One message per recipient: each needs its own unsubscribe link, and
    # recipients of a shared alert should not see each other's addresses.
    # A failure for one recipient must not stop the rest, nor make a caller
    # that retries on error (integration alerts) re-send to those already
    # reached; so raise only when nobody received it.
    delivered, last_error = 0, None
    with get_connection() as connection:
        for link in links:
            try:
                url = email_unsubscribe.link_url(link)
                headers = {"List-Unsubscribe": f"<{url}>"}
                # RFC 8058: mail clients POST to the link only over HTTPS.
                if url.startswith("https://"):
                    headers["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
                _send(
                    mail_subject,
                    template_name,
                    {
                        **context,
                        "unsubscribe_url": url,
                        "unsubscribe_reason": reason,
                    },
                    [link.email],
                    sender,
                    reply_to_list,
                    headers=headers,
                    connection=connection,
                    text_footer=f"Unsubscribe from these emails: {url}",
                )
                delivered += 1
            except Exception as exc:  # mail backend: best-effort per recipient
                last_error = exc
                # An SMTP server that dropped the session fails every later
                # send on it; closing makes the next send open a fresh one.
                # Closing a dead session can itself fail; the reopen is the point.
                with contextlib.suppress(OSError, smtplib.SMTPException):
                    connection.close()
                logger.exception(
                    "notification_email_send_failed",
                    category=notification_category,
                    template=template_name,
                    error=str(exc),
                )
    if last_error is not None and delivered == 0:
        raise last_error
    return delivered


def _send(
    subject,
    template_name,
    context,
    to,
    sender,
    reply_to,
    headers=None,
    connection=None,
    text_footer=None,
):
    html_body = render_to_string(template_name, context)
    text_body = _html_to_text(html_body)
    if text_footer:
        text_body = f"{text_body}\n{text_footer}\n"
    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        connection=connection,
        from_email=sender,
        to=to,
        reply_to=reply_to,
        headers=headers,
    )
    msg.attach_alternative(html_body, "text/html")
    msg.send()
