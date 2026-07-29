"""SOS (support impersonation) session minting.

Both current entry points route through here so that every mint leaves a
record: the Django admin view, where the operator is the signed-in staff
user, and the Appsmith-facing API, which authenticates with a shared key and
therefore has no operator identity.

This is a convention, not an invariant. `issue_sos_tokens` remains callable
directly, so a future caller could mint a pair with no audit line; the ruff
banned-api rule on that import is what keeps the convention honest in CI.
Making it unforgeable needs the provenance on the row itself (an `is_sos` /
`issued_by` column on AuthToken), which is tracked as follow-up work.
"""

from typing import TypedDict
from urllib.parse import urlencode

import structlog
from django.conf import settings
from django.core.exceptions import ValidationError

from accounts.models.user import User
from accounts.services.token_service import issue_sos_tokens
from tfc.settings.settings import ssl

logger = structlog.get_logger(__name__)


class SosTokens(TypedDict):
    access: str
    refresh: str


def start_sos_session(
    target: User, *, source: str, operator: User | None = None
) -> SosTokens:
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


def build_sos_handoff_url(
    user_id: str, *, source: str, operator: User | None = None
) -> tuple[str, None] | tuple[None, str]:
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
