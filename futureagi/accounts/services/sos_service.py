"""SOS (support impersonation) session minting.

Both entry points route through here so that no path can mint an
impersonation token without leaving a record: the Django admin view, where
the operator is the signed-in staff user, and the Appsmith-facing API, which
authenticates with a shared key and therefore has no operator identity.
"""

from urllib.parse import urlencode

import structlog
from django.conf import settings
from django.core.exceptions import ValidationError

from accounts.models.user import User
from accounts.services.token_service import issue_sos_tokens
from tfc.settings.settings import ssl

logger = structlog.get_logger(__name__)


def start_sos_session(target, *, source, operator=None):
    """Mint an SOS token pair for ``target`` and emit the audit line.

    ``operator`` is the staff user starting the session, or None when the
    caller authenticated with the shared API key — recorded as such rather
    than omitted, so an unattributable mint is visible in the log.

    Returns: {'access': encrypted_token, 'refresh': encrypted_token}
    """
    tokens = issue_sos_tokens(target)

    logger.warning(
        "sos_login_started",
        operator_id=str(operator.id) if operator is not None else None,
        operator_email=getattr(operator, "email", None),
        target_user_id=str(target.id),
        target_email=target.email,
        target_organization=getattr(target.organization, "name", None),
        source=source,
    )

    return tokens


def build_sos_handoff_url(user_id, *, source, operator=None):
    """Resolve ``user_id``, mint a session, and return (url, error_message).

    Exactly one of the two is non-None. Callers decide how to surface the
    error — a redirect with a message for the browser flow, JSON for the
    copy-link flow.
    """
    try:
        target = User.objects.select_related("organization").get(
            id=user_id, is_active=True
        )
    except (User.DoesNotExist, ValidationError, ValueError):
        return None, "Active user not found."

    if not settings.APP_URL:
        return None, "APP_URL is not configured — cannot build the SOS handoff URL."

    tokens = start_sos_session(target, source=source, operator=operator)

    params = urlencode({"access": tokens["access"], "refresh": tokens["refresh"]})
    return f"{ssl}{settings.APP_URL}/sos?{params}", None
