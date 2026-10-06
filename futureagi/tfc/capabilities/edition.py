"""Self-hosted edition policy: Community and Enterprise.

Answers two questions from server state only:

* ``commercial_caps_apply()``: do Future AGI Cloud's plan caps (dataset rows,
  API rate limits, knowledge bases, MCP tiers, ingestion rate limits, usage
  pre-checks) run here? Only on Cloud.
* ``check_creation()``: may this install create one more organization,
  workspace or organization member? Community allows 1 / 1 / 3; any usable
  Enterprise licence lifts all three. Cloud is never subject to the rule.

The rule never reads request input, Cloud Free-tier ResourceLimits/RateLimit
rows or subscription records. It only blocks new creation: an install that is
already over a limit (legacy data, or a licence that expired or was removed)
keeps everything it has.

This module MUST NOT import anything from ``ee.*`` at module top (see
tfc/ee_gating.py); the OSS image enforces the Community rule too.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from types import MappingProxyType

import structlog
from django.db import connection, transaction
from django.utils import timezone

from tfc.capabilities import service
from tfc.capabilities.registry import FEATURE_REGISTRY
from tfc.ee_loader import has_ee
from tfc.licensing.types import LicenseState

logger = structlog.get_logger(__name__)


class EditionResource(StrEnum):
    ORGANIZATION = "organizations"
    WORKSPACE = "workspaces"
    MEMBER = "members"


COMMUNITY_LIMITS: Mapping[EditionResource, int] = MappingProxyType(
    {
        EditionResource.ORGANIZATION: 1,
        EditionResource.WORKSPACE: 1,
        EditionResource.MEMBER: 3,
    }
)

CONTACT_EMAIL = "sales@futureagi.com"
ACTIVATION_ROUTE = "/dashboard/settings/ee-licenses"

# Products a self-hosted install only gets with a licence (registry oss_locked).
ENTERPRISE_PRODUCTS: tuple[str, ...] = tuple(
    feature_id for feature_id, feature in FEATURE_REGISTRY.items() if feature.oss_locked
)

# pg_advisory_xact_lock key serialising edition count-then-insert.
EDITION_LOCK_KEY = 0x0066_6564_6974_0001

_REFUSAL_MESSAGES: Mapping[EditionResource, str] = MappingProxyType(
    {
        EditionResource.ORGANIZATION: (
            "Community includes one organization. More organizations are an "
            "Enterprise feature."
        ),
        EditionResource.WORKSPACE: (
            "Community includes one workspace. More workspaces are an "
            "Enterprise feature."
        ),
        EditionResource.MEMBER: (
            "Community includes up to 3 organization members. More members are "
            "an Enterprise feature."
        ),
    }
)
_NEXT_STEP = (
    f" Contact {CONTACT_EMAIL} or activate a license in Settings > Plan & License."
)


@dataclass(frozen=True)
class EditionDecision:
    allowed: bool
    resource: EditionResource
    limit: int | None  # None when the rule does not apply (Cloud or licensed)
    current: int | None  # None when nothing was counted
    requested: int
    license_state: str


# ---------------------------------------------------------------------------
# Deployment and licence
# ---------------------------------------------------------------------------


def is_cloud() -> bool:
    """Secret-validated Future AGI Cloud test.

    The same answer capabilities (``_bootstrap._detect_location``) and
    Entitlements use: ``CLOUD_DEPLOYMENT`` alone never counts, the matching
    ``CLOUD_DEPLOYMENT_SECRET`` is required. The OSS image is never Cloud.
    """
    if not has_ee("ee.usage"):
        return False
    try:
        from ee.usage.deployment import DeploymentMode
    except ImportError:
        return False
    return DeploymentMode.is_cloud()


def commercial_caps_apply() -> bool:
    """The only switch for Cloud plan caps (A1, A2, A4, A5, B3, B3b)."""
    return is_cloud()


def enterprise_license_usable() -> bool:
    """Any usable licence (active, grace or trial, re-checked against the
    clock) lifts the edition rule. Per-contract seat counts are not read."""
    snapshot = service.get_license_snapshot()
    return bool(snapshot is not None and snapshot.is_usable)


def license_state() -> str:
    if is_cloud():
        return LicenseState.NOT_APPLICABLE.value
    snapshot = service.get_license_snapshot()
    if snapshot is None:
        return LicenseState.MISSING.value
    return snapshot.live_state().value


def edition_rule_applies() -> bool:
    return not is_cloud() and not enterprise_license_usable()


def current_edition() -> str:
    if is_cloud():
        return "cloud"
    return "enterprise" if enterprise_license_usable() else "community"


# ---------------------------------------------------------------------------
# Counting
# ---------------------------------------------------------------------------


def _seats(organization) -> tuple[int, set[str], set[str]]:
    """(active member count, their emails, pending-invite emails without a seat).

    Members are active OrganizationMemberships. A pending, unexpired invite
    holds a seat until it is accepted, cancelled or expires, so accepting an
    invite never needs a check.
    """
    from accounts.models.organization_invite import InviteStatus, OrganizationInvite
    from accounts.models.organization_membership import OrganizationMembership
    from tfc.constants.levels import INVITE_VALIDITY_DAYS

    active = list(
        OrganizationMembership.no_workspace_objects.filter(
            organization=organization, is_active=True
        )
        .values_list("user_id", "user__email")
        .distinct()
    )
    active_emails = {email.strip().lower() for _, email in active if email}
    cutoff = timezone.now() - timedelta(days=INVITE_VALIDITY_DAYS)
    pending_emails = {
        email.strip().lower()
        for email in OrganizationInvite.no_workspace_objects.filter(
            organization=organization,
            status=InviteStatus.PENDING,
            created_at__gte=cutoff,
        ).values_list("target_email", flat=True)
        if email
    } - active_emails
    return len({user_id for user_id, _ in active}), active_emails, pending_emails


def count(resource: EditionResource, *, organization=None) -> int:
    """Organizations and active workspaces instance-wide; members per organization."""
    resource = EditionResource(resource)
    if resource is EditionResource.ORGANIZATION:
        from accounts.models.organization import Organization

        return Organization.objects.count()
    if resource is EditionResource.WORKSPACE:
        from accounts.models.workspace import Workspace

        return Workspace.no_workspace_objects.filter(is_active=True).count()
    if organization is None:
        raise ValueError("Counting members needs an organization")
    active, _, pending = _seats(organization)
    return active + len(pending)


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


def check_creation(
    resource: EditionResource,
    *,
    organization=None,
    new_member_emails: Iterable[str] | None = None,
    adding: int = 1,
) -> EditionDecision:
    """May this install create ``adding`` more of ``resource``?

    For members pass the emails being added: emails that already hold a seat
    (active member or pending, unexpired invite) need no new seat, and a
    request is all-or-nothing.
    """
    resource = EditionResource(resource)
    emails = (
        None
        if new_member_emails is None
        else {e.strip().lower() for e in new_member_emails if e and e.strip()}
    )
    requested = adding if emails is None else len(emails)

    if not edition_rule_applies():
        return EditionDecision(
            allowed=True,
            resource=resource,
            limit=None,
            current=None,
            requested=requested,
            license_state=license_state(),
        )

    limit = COMMUNITY_LIMITS[resource]
    if resource is EditionResource.MEMBER:
        if organization is None:
            raise ValueError("Checking members needs an organization")
        active, active_emails, pending_emails = _seats(organization)
        current = active + len(pending_emails)
        if emails is not None:
            requested = len(emails - active_emails - pending_emails)
    else:
        current = count(resource)

    return EditionDecision(
        allowed=requested == 0 or current + requested <= limit,
        resource=resource,
        limit=limit,
        current=current,
        requested=requested,
        license_state=license_state(),
    )


def assert_can_create(resource: EditionResource, **kwargs) -> EditionDecision:
    """check_creation() that raises EnterpriseFeatureRequired on refusal."""
    decision = check_creation(resource, **kwargs)
    if not decision.allowed:
        from tfc.capabilities.errors import EnterpriseFeatureRequired

        logger.info(
            "edition_creation_refused",
            resource=decision.resource.value,
            limit=decision.limit,
            current=decision.current,
            requested=decision.requested,
            license_state=decision.license_state,
        )
        raise EnterpriseFeatureRequired.from_decision(decision)
    return decision


@contextmanager
def creation_lock() -> Iterator[None]:
    """Serialise count-then-insert while the edition rule applies.

    Runs the body in a transaction holding a transaction-scoped advisory
    lock, so two concurrent first-workspace creates cannot both pass. Cloud
    and licensed installs take no lock.
    """
    if not edition_rule_applies():
        yield
        return
    with transaction.atomic():
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [EDITION_LOCK_KEY])
        yield


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------


def refusal_message(resource: EditionResource) -> str:
    return _REFUSAL_MESSAGES[EditionResource(resource)] + _NEXT_STEP


def gate_payload(decision: EditionDecision) -> dict:
    """The ``enterprise_gate`` block for an edition-rule refusal."""
    return {
        "feature": decision.resource.value,
        "edition": "community",
        "limit": decision.limit,
        "current": decision.current,
        "requested": decision.requested,
        "license_state": decision.license_state,
        "contact": CONTACT_EMAIL,
        "activation_route": ACTIVATION_ROUTE,
    }


def product_gate_payload(feature_id: str, state: str | None = None) -> dict:
    """The ``enterprise_gate`` block for a self-hosted oss_locked product gate."""
    return {
        "feature": feature_id,
        "edition": current_edition(),
        "limit": None,
        "current": None,
        "requested": None,
        "license_state": state or license_state(),
        "contact": CONTACT_EMAIL,
        "activation_route": ACTIVATION_ROUTE,
    }


def usage_summary(organization) -> dict:
    """Edition, limits and current usage for Settings > Plan & License."""
    if is_cloud():
        return {"edition": "cloud", "deployment": "cloud"}

    rule_applies = edition_rule_applies()
    counts = {
        EditionResource.ORGANIZATION: count(EditionResource.ORGANIZATION),
        EditionResource.WORKSPACE: count(EditionResource.WORKSPACE),
        EditionResource.MEMBER: (
            count(EditionResource.MEMBER, organization=organization)
            if organization is not None
            else 0
        ),
    }
    limits = {
        resource.value: {
            "limit": COMMUNITY_LIMITS[resource] if rule_applies else None,
            "current": current,
        }
        for resource, current in counts.items()
    }
    return {
        "edition": "community" if rule_applies else "enterprise",
        "deployment": "self_hosted",
        "limits": limits,
        "over_limit": rule_applies
        and any(counts[r] > COMMUNITY_LIMITS[r] for r in EditionResource),
        "enterprise_features": [
            *ENTERPRISE_PRODUCTS,
            *(resource.value for resource in EditionResource),
        ],
        "contact": CONTACT_EMAIL,
    }
