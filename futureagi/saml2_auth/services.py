"""HTTP-free SAML services and trust-boundary helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

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
