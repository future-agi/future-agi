import html as _html
import logging
import re

from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)

DEFAULT_FROM_EMAIL = "Future AGI <noreply@mail.futureagi.com>"
DEFAULT_REPLY_TO = "support@futureagi.com"
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



def email_helper(
    mail_subject,
    template_name,
    template_data,
    to_email_list,
    *,
    reply_to=None,
    from_email=None,
    fail_silently=False,
):
    """Render a Django template and send as a multipart HTML + text email.

    Args:
        mail_subject: Subject line.
        template_name: Django template path (HTML).
        template_data: Context dict.
        to_email_list: List of recipients.
        reply_to: Optional list/str of reply-to addresses. Defaults to
            DEFAULT_REPLY_TO (support@futureagi.com) so replies go to a
            monitored inbox. Pass an explicit address to override, or
            reply_to=False/[] to disable.
        from_email: Optional sender override. Defaults to Future AGI noreply.
        fail_silently: When True, exceptions during email rendering or delivery
            are caught and logged as warnings rather than raised.
    """
    try:
        html_body = render_to_string(template_name, template_data)
        text_body = _html_to_text(html_body)

        if reply_to is None:
            reply_to_list = [DEFAULT_REPLY_TO]
        elif reply_to is False or (
            isinstance(reply_to, (list, tuple)) and not reply_to
        ):
            reply_to_list = None
        else:
            reply_to_list = [reply_to] if isinstance(reply_to, str) else list(reply_to)

        msg = EmailMultiAlternatives(
            subject=mail_subject,
            body=text_body,
            from_email=from_email or DEFAULT_FROM_EMAIL,
            to=to_email_list,
            reply_to=reply_to_list,
        )
        msg.attach_alternative(html_body, "text/html")
        msg.send(fail_silently=fail_silently)
        return True
    except Exception as e:
        if not fail_silently:
            raise
        logger.warning(
            "Failed to send email to %s (subject='%s'): %s",
            to_email_list,
            mail_subject,
            str(e),
            exc_info=True,
        )
        return False
