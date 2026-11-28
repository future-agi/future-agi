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

from dataclasses import replace
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


def clickhouse_span(project_id, parent):
    from tracer.services.attachments.control_plane import read_spans

    spans = read_spans(project_id, {parent.span_id}, trace_ids={parent.trace_id})
    return spans.get((str(parent.trace_id), parent.span_id))


def source_state(scope, parent, span, *, retention=retention_for, session_check=None):
    """One source rule for live reads, metadata batches and late binding.

    Only the dependencies may be batched. Gateway calls use the live marker
    check on every invocation, including the checks around each storage read.
    """
    if span is None or getattr(span, "is_deleted", True):
        return None
    try:
        trace = UUID(str(span.trace_id))
        started_at = _aware(span.start_time)
        if (
            str(span.project_id) != str(scope.project_id)
            or str(span.id) != parent.span_id
            or trace != parent.trace_id
            or not isinstance(started_at, datetime)
        ):
            return None
    except (ValueError, TypeError, AttributeError):
        return None
    if session_check is None:
        from tracer.services.attachments.session_deletion import session_deleted

        session_check = session_deleted
    # The durable marker wins even when a collector has no PG Trace and its
    # CH span is still live because asynchronous cleanup has not succeeded.
    try:
        if session_check(scope.project_id, getattr(span, "trace_session_id", None)):
            return None
    except Exception:  # noqa: BLE001 - marker/remap boundary; fail closed
        logger.warning("attachment_session_deletion_unavailable")
        return None
    try:
        expires_at = retention(scope.organization_id, started_at)
    except Exception:  # noqa: BLE001 - unknown retention never grants authority
        return None
    if (
        not isinstance(expires_at, datetime)
        or timezone.is_naive(expires_at)
        or expires_at <= started_at
    ):
        return None
    return SourceState(
        scope_id=scope.id,
        parent=parent,
        generation=SOURCE_GENERATION,
        started_at=started_at,
        expires_at=expires_at,
        readable=True,
    )


class BatchSessionDeletion:
    """Project-batched marker/remap checks, valid for ONE metadata/bind batch.

    A failed lookup denies every session-bearing source in this batch. Never
    keep this snapshot on LiveAuthority: gateway chunk rechecks must be fresh.
    No PG-only row proves a parent exists; callers still need the exact CH span.
    """

    def __init__(self, project_id, spans):
        self.project_id = str(project_id)
        self.session_ids = set()
        self.loaded, self.deleted = False, None
        for span in spans:
            if str(getattr(span, "project_id", None)) != self.project_id:
                continue
            value = getattr(span, "trace_session_id", None)
            if value:
                try:
                    self.session_ids.add(str(UUID(str(value))))
                except (ValueError, TypeError):
                    pass  # Deny this malformed identity without poisoning others.

    def __call__(self, project_id, session_id):
        if str(project_id) != self.project_id:
            return True
        if not session_id:
            return False
        try:
            key = str(UUID(str(session_id)))
        except (ValueError, TypeError):
            return True
        if key not in self.session_ids:
            return True
        if not self.loaded:
            self.loaded = True
            try:
                from tracer.services.attachments.session_deletion import (
                    deleted_session_ids,
                    deleted_sessions,
                )
                from tracer.services.clickhouse.v2 import get_reader

                overlays, sessions = deleted_sessions(self.project_id)
                # Like the single-read rule, do not ask CH for remaps when
                # this project has no deletion markers at all.
                self.deleted = (
                    deleted_session_ids(self.project_id, self.session_ids, get_reader())
                    if overlays.exists() or sessions.exists()
                    else set()
                )
            except Exception:  # noqa: BLE001 - marker/remap boundary; fail closed
                logger.warning("attachment_session_deletion_unavailable")
        return self.deleted is None or key in self.deleted


def authorize_parent_batch(*, scope, parents, spans, retention):
    """Authorize exact parents from one project read and one marker/remap batch.

    The map is local to this operation. A denied or unavailable parent remains
    None even when another parent with the same span id is authorized.
    """
    session_check = BatchSessionDeletion(scope.project_id, spans.values())
    return {
        (parent.trace_id, parent.span_id): source_state(
            scope,
            parent,
            spans.get((str(parent.trace_id), parent.span_id)),
            retention=retention,
            session_check=session_check,
        )
        for parent in dict.fromkeys(parents)
    }


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
            span = self.span_reader(scope.project_id, parent)
        except Exception:  # noqa: BLE001 - ClickHouse boundary; logged, fails closed
            logger.warning(
                "attachment_source_unavailable", project_id=str(scope.project_id)
            )
            return None
        state = source_state(scope, parent, span, retention=retention_for)
        if state is None:
            return None
        user = User.objects.filter(pk=viewer.user_id, is_active=True).first()
        writable = bool(user and user.can_write_to_workspace(scope.workspace))
        return replace(state, writable=writable)
