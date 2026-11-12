"""Who may change the rollout, and how a rollout route must be signed in (READINESS 3.13 "Humans
operate production", RC-10, OD-23).

- Every rollout route needs a person's Future AGI browser session. An org API key pair is not a
  person: a `system` key authenticates as the org's first user by `created_at`, and any member can
  read it from `GET /accounts/keys/`, so a key would let a member act as a staff operator or as the
  org's owner. The Hub refuses org keys for the same reason (omega_hub.permissions).
- Every change names its operator: an active staff user holding `omega_rollout.operate_rollout`
  with an active organization membership, because the change is audited under the operator's own
  org (`accounts_audit_log` needs one) and a change that cannot be audited does not happen. The
  admin API, the CLI and waitlist invitations all pass through `require_operator`.
- The local-stack seeds are the one exception. They pass `SEED`, and the audit records a change with
  no operator (under the target org when there is one).
- A person's organizations are their active memberships, never the legacy `user.organization` key:
  removing a member deactivates the membership and leaves that key in place.
"""

from __future__ import annotations

from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

OPERATE = "omega_rollout.operate_rollout"
SESSION_REQUIRED = "session_required"
# Refusals about who acts, not about the change itself: the API answers them 403, not 409.
AUTHORITY_CODES = frozenset(
    {"operator_required", "not_an_operator", "operator_without_organization"}
)


class RolloutChangeRefused(ValueError):
    """A change that the rules refuse; `code` is stable and shown to the operator."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class _Seed:
    """The actor of a local-stack seed: a change no operator made. Never used by the API or the CLI."""

    def __repr__(self) -> str:
        return "SEED"


SEED = _Seed()


class SessionOnly(BasePermission):
    """Refuse an org API key pair on a rollout route, whatever the key's user may do."""

    def has_permission(self, request, view):
        if getattr(request, "org_api_key", None) is not None:
            raise PermissionDenied(
                detail="Sign in to Future AGI in a browser: rollout changes do not accept API keys.",
                code=SESSION_REQUIRED,
            )
        return True


def active_memberships(user):
    """The person's active org memberships, oldest first (a fixed order across workers)."""
    from accounts.models.organization_membership import OrganizationMembership

    if user is None or not getattr(user, "pk", None):
        return OrganizationMembership.no_workspace_objects.none()
    return (
        OrganizationMembership.no_workspace_objects.filter(user=user, is_active=True)
        .select_related("organization")
        .order_by("created_at", "pk")
    )


def home_organization(user):
    """The org a person's own audit rows go under: their oldest active membership, or None."""
    membership = active_memberships(user).first()
    return membership.organization if membership is not None else None


def is_operator(user) -> bool:
    return bool(
        user is not None
        and getattr(user, "is_active", False)
        and getattr(user, "is_staff", False)
        and user.has_perm(OPERATE)
    )


def require_operator(actor, *, allow_seed: bool = True):
    """The user a change is recorded against (None for a seed), or RolloutChangeRefused."""
    if actor is SEED and allow_seed:
        return None
    if actor is None or actor is SEED:
        raise RolloutChangeRefused(
            "operator_required",
            "Every rollout change names the staff operator who makes it.",
        )
    if not is_operator(actor):
        raise RolloutChangeRefused(
            "not_an_operator",
            "Only active Future AGI staff holding operate_rollout change the rollout.",
        )
    if home_organization(actor) is None:
        raise RolloutChangeRefused(
            "operator_without_organization",
            "The operator belongs to no organization, so the change could not be audited.",
        )
    return actor
