"""Live read authority for the HTTP layer: application session + source state.

``session_is_live`` re-reads the ``AuthToken`` row behind the viewer's JWT on
every call: it must still be an active access token of the same user, used
within the normal inactivity window. Logout deactivates the row, so no logout
hook is needed and a copied media cookie dies with the application session.

``source`` reads the span from ClickHouse (``spans FINAL``: the latest version,
deleted rows collapse to ``is_deleted``) for the exact project, trace and span.
Retention comes from the organization's trace-retention policy; a failed
policy lookup denies. The span reader keeps no per-span deletion generation, so
the source generation is the constant ``SOURCE_GENERATION``. Whoever binds
occurrences to spans must take its ``SourceState`` from this same function so
binding and reading can never disagree.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from django.utils import timezone

from accounts.models import User
from accounts.models.auth_token import (
    AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES,
    AuthToken,
    AuthTokenOrigin,
    AuthTokenType,
)
from tracer.services.attachments.occurrences import SourceState

logger = structlog.get_logger(__name__)

SOURCE_GENERATION = 0
# Self-hosted deployments keep traces until a person deletes them. An explicit
# finite horizon still bounds every grant and occurrence derived from them.
UNLIMITED_RETENTION = timedelta(days=3650)


def _aware(value):
    if value is None:
        return None
    return value if timezone.is_aware(value) else value.replace(tzinfo=UTC)


def retention_for(organization_id, started_at, *, now=None):
    """The source's expiry, or None when the retention policy is unknown."""
    from tfc.ee_gates import RetentionPolicyUnavailable, trace_retention_cutoff

    now = now or timezone.now()
    try:
        cutoff = trace_retention_cutoff(organization_id, now=now)
    except RetentionPolicyUnavailable:
        return None
    if cutoff is None:
        return started_at + UNLIMITED_RETENTION
    return started_at + (now - cutoff)


def clickhouse_span(project_id, span_id):
    from tracer.services.clickhouse.v2 import get_reader

    return get_reader().get(span_id, project_id=str(project_id))


class LiveAuthority:
    """Production authority. Every failure is a denial, never a fallback."""

    def __init__(self, span_reader=clickhouse_span):
        self.span_reader = span_reader

    def session_is_live(self, viewer):
        """The same live checks ``decode_token`` applies, minus its side effects.

        SAML-origin sessions are re-validated against the IdP binding and
        generation, so an IdP-side revocation also ends media access.
        """
        from accounts.saml_credential import (
            SamlCredentialInvalid,
            validate_saml_credential,
        )

        now = timezone.now()
        threshold = now - timedelta(minutes=AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES)
        if not User.objects.filter(pk=viewer.user_id, is_active=True).exists():
            return False
        row = (
            AuthToken.objects.filter(
                pk=viewer.application_session_id, user_id=viewer.user_id
            )
            .values(
                "id",
                "user_id",
                "auth_type",
                "is_active",
                "last_used_at",
                "auth_origin",
                "scoped_organization_id",
                "origin_idp_id",
                "origin_idp_generation",
            )
            .first()
        )
        if (
            row is None
            or row["auth_type"] != AuthTokenType.ACCESS.value
            or not row["is_active"]
            or row["last_used_at"] is None
            or row["last_used_at"] < threshold
        ):
            return False
        if row["auth_origin"] == AuthTokenOrigin.SAML:
            try:
                validate_saml_credential(
                    dict(row), str(viewer.user_id), now=now, transport="http"
                )
            except SamlCredentialInvalid:
                return False
        return True

    def source(self, viewer, scope, parent):
        try:
            span = self.span_reader(scope.project_id, parent.span_id)
        except Exception:  # noqa: BLE001 - ClickHouse boundary; logged, fails closed
            logger.warning(
                "attachment_source_unavailable", project_id=str(scope.project_id)
            )
            return None
        if span is None or getattr(span, "is_deleted", True):
            return None
        try:
            trace = UUID(str(span.trace_id))
            started_at = _aware(span.start_time)
        except (ValueError, TypeError, AttributeError):
            return None
        if (
            str(span.project_id) != str(scope.project_id)
            or str(span.id) != parent.span_id
            or trace != parent.trace_id
            or not isinstance(started_at, datetime)
        ):
            return None
        expires_at = retention_for(scope.organization_id, started_at)
        if expires_at is None:
            return None
        user = User.objects.filter(pk=viewer.user_id, is_active=True).first()
        writable = bool(user and user.can_write_to_workspace(scope.workspace))
        return SourceState(
            scope_id=scope.id,
            parent=parent,
            generation=SOURCE_GENERATION,
            started_at=started_at,
            expires_at=expires_at,
            readable=True,
            writable=writable,
        )
