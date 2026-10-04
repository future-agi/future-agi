"""HTTP-free SAML services and trust-boundary helpers."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.db import connection, models, transaction
from django.utils import timezone

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from tfc.settings.settings import get_assertion_url, get_entity_id, get_name_id_format

if TYPE_CHECKING:
    from accounts.models.organization import Organization
    from saml2_auth.models import SAMLMetadataModel


class SamlDenied(Exception):
    """A deliberately non-sensitive failure reason for the SAML boundary."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class AssertionFacts:
    """The assertion facts that must stay true through final issuance."""

    not_on_or_after: datetime | None


def resolve_login_user(raw_email: str) -> User | None:
    """Resolve one active user without making user enumeration observable."""

    email = raw_email.strip()
    users = list(User.objects.filter(email__iexact=email, is_active=True)[:2])
    return users[0] if len(users) == 1 else None


def resolve_login_idp(user: User) -> SAMLMetadataModel | None:
    """Choose only an enabled IdP in one of the user's live organizations."""

    from saml2_auth.models import SAMLMetadataModel

    membership_org_ids = list(
        OrganizationMembership.no_workspace_objects.filter(
            user=user, is_active=True
        ).values_list("organization_id", flat=True)
    )
    candidates = list(
        SAMLMetadataModel.no_workspace_objects.filter(
            deleted=False,
            is_enabled=True,
            organization_id__in=membership_org_ids,
        )[:2]
    )
    if user.organization_id in membership_org_ids:
        preferred = next(
            (
                candidate
                for candidate in candidates
                if candidate.organization_id == user.organization_id
            ),
            None,
        )
        if preferred is not None:
            return preferred
    return candidates[0] if len(candidates) == 1 else None


def build_sp_client(idp_row: SAMLMetadataModel, acs_url: str | None = None) -> Any:
    """Build a strict client from the IdP row's metadata, entirely in memory."""

    from saml2 import BINDING_HTTP_POST, BINDING_HTTP_REDIRECT
    from saml2.client import Saml2Client
    from saml2.config import Config as Saml2Config

    assertion_url = acs_url or get_assertion_url
    settings = {
        "accepted_time_diff": 60,
        "entityid": get_entity_id,
        "metadata": {"inline": [idp_row.meta]},
        "service": {
            "sp": {
                "allow_unsolicited": False,
                "authn_requests_signed": False,
                "endpoints": {
                    "assertion_consumer_service": [
                        (assertion_url, BINDING_HTTP_REDIRECT),
                        (assertion_url, BINDING_HTTP_POST),
                    ]
                },
                "logout_requests_signed": True,
                "name_id_format": get_name_id_format,
                "want_assertions_or_response_signed": False,
                "want_assertions_signed": True,
                "want_response_signed": False,
            }
        },
    }
    try:
        config = Saml2Config().load(settings)
        config.allow_unknown_attributes = True
        return Saml2Client(config=config)
    except Exception:
        raise SamlDenied("idp_unavailable") from None


def resolve_assertion_user(identity: dict[str, list[str]], name_id: str | None) -> User:
    """Resolve a single account from a parsed assertion without provisioning."""

    email_values = identity.get("email", []) if identity else []
    email = email_values[0] if email_values else name_id
    if not email:
        raise SamlDenied("identity_missing")
    users = list(User.objects.filter(email__iexact=email)[:2])
    if len(users) != 1:
        raise SamlDenied("identity_missing")
    return users[0]


def require_active_membership(
    user: User, organization: Organization
) -> OrganizationMembership:
    """Return the one live membership that permits an IdP-scoped login."""

    try:
        return OrganizationMembership.no_workspace_objects.get(
            user=user, organization=organization, is_active=True
        )
    except OrganizationMembership.DoesNotExist:
        raise SamlDenied("no_membership") from None


def verify_assertion_bindings(
    authn_response: Any, attempt: Any, idp: SAMLMetadataModel
) -> AssertionFacts:
    """Check bindings pysaml2 does not enforce when no request context is given.

    The function intentionally receives a duck-typed attempt so it can be used
    after the login-state migration without coupling this trust primitive to a
    persistence implementation.
    """

    response = getattr(authn_response, "response", None)
    assertion = getattr(response, "assertion", None)
    assertions = assertion if isinstance(assertion, list) else [assertion]
    if len(assertions) != 1 or assertions[0] is None:
        raise SamlDenied("response_invalid")
    if getattr(response, "in_response_to", None) != attempt.request_id:
        raise SamlDenied("response_invalid")
    if getattr(response, "destination", None) != get_assertion_url:
        raise SamlDenied("response_invalid")
    if getattr(authn_response, "issuer", lambda: None)() != _metadata_entity_id(idp):
        raise SamlDenied("response_invalid")

    assertion_value = assertions[0]
    conditions = getattr(assertion_value, "conditions", None)
    restrictions = (
        getattr(conditions, "audience_restriction", None) if conditions else None
    )
    audiences = [
        audience.text
        for restriction in restrictions or []
        for audience in (getattr(restriction, "audience", None) or [])
        if getattr(audience, "text", None)
    ]
    if not conditions or get_entity_id not in audiences:
        raise SamlDenied("response_invalid")

    confirmations = (
        getattr(getattr(assertion_value, "subject", None), "subject_confirmation", None)
        or []
    )
    bearer_data = [
        confirmation.subject_confirmation_data
        for confirmation in confirmations
        if getattr(confirmation, "method", None)
        == "urn:oasis:names:tc:SAML:2.0:cm:bearer"
        and getattr(confirmation, "subject_confirmation_data", None) is not None
    ]
    if not bearer_data:
        raise SamlDenied("response_invalid")
    if any(
        data.in_response_to != attempt.request_id
        or data.recipient != get_assertion_url
        or not data.not_on_or_after
        for data in bearer_data
    ):
        raise SamlDenied("response_invalid")
    return AssertionFacts(
        not_on_or_after=getattr(authn_response, "not_on_or_after", None)
    )


def _metadata_entity_id(idp: SAMLMetadataModel) -> str | None:
    """Read only the IdP entity identifier from the row's inline metadata."""

    try:
        from saml2.mdstore import MetaDataInline

        metadata = MetaDataInline([idp.meta])
        return next(iter(metadata.keys()))
    except Exception:
        raise SamlDenied("idp_unavailable") from None


def validate_next_path(value: str | None) -> str:
    """Accept only a bounded, same-origin relative post-login path."""

    default = "/dashboard/develop"
    if not value or len(value) > 512:
        return default
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return default
    return value


def admit_attempt(
    *, user: User, idp: SAMLMetadataModel, binder: str, next_path: str
) -> Any:
    """TX-I: bound, per-user admission for a new login attempt."""

    from saml2_auth.models import SamlLoginAttempt, SamlResponseCandidate

    now = timezone.now()
    retention_cutoff = now - timedelta(seconds=settings.SAML_ATTEMPT_RETENTION_SECONDS)
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                [f"saml_attempt:{user.id}"],
            )
        _delete_limited(SamlResponseCandidate, "expires_at__lt", now)
        _delete_limited(SamlLoginAttempt, "expires_at__lt", retention_cutoff)
        if SamlLoginAttempt.objects.count() >= settings.SAML_MAX_ATTEMPT_ROWS:
            raise SamlDenied("capacity")
        if (
            SamlLoginAttempt.objects.filter(
                user_hint=user,
                created_at__gt=now
                - timedelta(seconds=settings.SAML_ATTEMPT_TTL_SECONDS),
            ).count()
            >= settings.SAML_MAX_ATTEMPTS_PER_USER_PER_TTL
        ):
            raise SamlDenied("attempt_rate_denied")
        return SamlLoginAttempt.objects.create(
            relay_key=secrets.token_urlsafe(32),
            request_id=f"pending-{secrets.token_urlsafe(32)}",
            idp=idp,
            organization_id=idp.organization_id,
            user_hint=user,
            binder_hash=hashlib.sha256(binder.encode()).hexdigest(),
            next_path=validate_next_path(next_path),
            expires_at=now + timedelta(seconds=settings.SAML_ATTEMPT_TTL_SECONDS),
        )


def update_attempt_request_id(attempt: Any, request_id: str) -> None:
    """Persist the request id returned by pysaml2 after TX-I commits."""

    from saml2_auth.models import SamlLoginAttempt

    if not request_id or len(request_id) > 128:
        raise SamlDenied("response_invalid")
    SamlLoginAttempt.objects.filter(id=attempt.id, state="pending").update(
        request_id=request_id
    )
    attempt.request_id = request_id


def store_candidate(*, attempt: Any, payload: bytes) -> Any:
    """TX-C: save bounded response material without consuming the attempt."""

    from saml2_auth.models import SamlLoginAttempt, SamlResponseCandidate

    now = timezone.now()
    if len(payload) > settings.SAML_MAX_RESPONSE_BYTES:
        raise SamlDenied("acs_malformed")
    with transaction.atomic():
        locked_attempt = SamlLoginAttempt.objects.select_for_update().get(id=attempt.id)
        if (
            locked_attempt.state != SamlLoginAttempt.State.PENDING
            or locked_attempt.expires_at <= now
        ):
            raise SamlDenied("attempt_unavailable")
        _delete_limited(SamlResponseCandidate, "expires_at__lt", now)
        candidate_rows = SamlResponseCandidate.objects.count()
        candidate_bytes = (
            SamlResponseCandidate.objects.aggregate(total=models.Sum("payload_bytes"))[
                "total"
            ]
            or 0
        )
        if (
            candidate_rows >= settings.SAML_MAX_CANDIDATE_ROWS
            or candidate_bytes + len(payload) > settings.SAML_MAX_CANDIDATE_BYTES
        ):
            raise SamlDenied("capacity")
        if (
            locked_attempt.candidate_count
            >= settings.SAML_MAX_CANDIDATES_PER_ATTEMPT_LIFETIME
            or SamlResponseCandidate.objects.filter(
                attempt=locked_attempt, expires_at__gt=now
            ).count()
            >= settings.SAML_MAX_CANDIDATES_PER_ATTEMPT
        ):
            raise SamlDenied("candidate_capacity")
        locked_attempt.candidate_count += 1
        locked_attempt.save(update_fields=["candidate_count"])
        return SamlResponseCandidate.objects.create(
            candidate_key=secrets.token_urlsafe(32),
            attempt=locked_attempt,
            payload=payload,
            payload_bytes=len(payload),
            expires_at=now + timedelta(seconds=settings.SAML_CANDIDATE_TTL_SECONDS),
        )


def claim_attempt(*, candidate_key: str, binder: str) -> tuple[Any, bytes]:
    """TX-A: atomically prove browser possession and claim one response."""

    from saml2_auth.models import SamlLoginAttempt, SamlResponseCandidate

    now = timezone.now()
    with transaction.atomic():
        try:
            candidate = SamlResponseCandidate.objects.select_for_update().get(
                candidate_key=candidate_key
            )
        except SamlResponseCandidate.DoesNotExist:
            raise SamlDenied("candidate_unavailable") from None
        attempt = SamlLoginAttempt.objects.select_for_update().get(
            id=candidate.attempt_id
        )
        if candidate.expires_at <= now:
            candidate.delete()
            raise SamlDenied("candidate_unavailable")
        if attempt.state != SamlLoginAttempt.State.PENDING or attempt.expires_at <= now:
            candidate.delete()
            raise SamlDenied("attempt_unavailable")
        binder_hash = hashlib.sha256(binder.encode()).hexdigest()
        if not hmac.compare_digest(binder_hash, attempt.binder_hash):
            candidate.delete()
            raise SamlDenied("browser_mismatch")
        payload = bytes(candidate.payload)
        updated = SamlLoginAttempt.objects.filter(
            id=attempt.id,
            state=SamlLoginAttempt.State.PENDING,
            expires_at__gt=now,
        ).update(
            state=SamlLoginAttempt.State.CLAIMED,
            claimed_at=now,
            idp_meta_sha256=hashlib.sha256(attempt.idp.meta.encode()).hexdigest(),
            idp_generation=attempt.idp.security_generation,
        )
        candidate.delete()
        if updated != 1:
            raise SamlDenied("attempt_unavailable")
        attempt.state = SamlLoginAttempt.State.CLAIMED
        return attempt, payload


def record_failure(attempt_id: Any, reason: str) -> None:
    """TX-F: terminally record a failed claim without exposing raw data."""

    from saml2_auth.models import SamlLoginAttempt

    SamlLoginAttempt.objects.filter(
        id=attempt_id, state=SamlLoginAttempt.State.CLAIMED
    ).update(
        state=SamlLoginAttempt.State.FAILED,
        deny_reason=reason[:32],
        finished_at=timezone.now(),
    )


def _tx_b_barrier(stage: str) -> None:
    """Opt-in test-only TX-B interleaving hook; a no-op in production."""

    if os.getenv("SAML_TX_B_BARRIERS_ENABLED", "").lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_lock(hashtext(%s))", [f"saml_tx_b_barrier:{stage}"]
        )
        cursor.execute(
            "SELECT pg_advisory_unlock(hashtext(%s))", [f"saml_tx_b_barrier:{stage}"]
        )


def _delete_limited(model: Any, lookup: str, before: datetime) -> None:
    ids = list(
        model.objects.filter(**{lookup: before})
        .order_by("expires_at")
        .values_list("id", flat=True)[: settings.SAML_INLINE_CLEANUP_ROWS]
    )
    if ids:
        model.objects.filter(id__in=ids).delete()
