"""Consent boundary for the main-owned auto-top-up claim path.

Integration requirement: ee.usage.services.auto_topup.claim_auto_topup must call
has_current_auto_reload_consent(account) immediately after _locked(org), inside
its existing transaction, and return None on False, BEFORE reserving a claim.
Do not catch database failures and continue charging. Settings and claims share
the account row lock, so a settings disable or change serializes with claims.
A charge already claimed before disable may complete, as the terms disclose.

The legacy configure_auto_topup writer is NOT sufficient authorization: main
must record the authenticated actor's receipt using this boundary after checking
the current terms, or refuse enabling. Never synthesize consent for old rows.
"""

from django.db.models import F

from agentcc.auto_reload_terms import (
    AUTO_RELOAD_CONSENT_VERSION,
    AutoReloadConsentError,
    require_current_consent_version,
)
from agentcc.models.auto_reload_consent import AgentccAutoReloadConsent


def require_consent_actor(actor):
    if not getattr(actor, "is_authenticated", False) or not getattr(actor, "pk", None):
        raise AutoReloadConsentError(
            "auto_reload_consent_actor_required",
            "An authenticated user must acknowledge the auto-reload terms.",
        )


def record_auto_reload_consent(account, *, version, actor, accepted_at):
    """Append, never overwrite, under the caller's transaction and account lock.

    The caller must write this version/time to the ledger account in the SAME
    transaction. A failed receipt write must roll back the account settings too.
    """
    require_current_consent_version(version)
    require_consent_actor(actor)
    return AgentccAutoReloadConsent.objects.create(
        organization_id=account.organization_id,
        actor=actor,
        actor_identifier=actor.pk,
        consent_version=version,
        accepted_at=accepted_at,
        threshold_micros=account.autoreload_threshold_micros,
        amount_micros=account.autoreload_amount_micros,
    )


def has_current_auto_reload_consent(account):
    """Fail closed for absent, stale, actor-less or mismatched authorization.

    This is a read-only predicate, not a replacement for the ledger's claim lock,
    limits, balance check or idempotency. Pass the locked, freshly fetched row.
    The ledger version/time alone never count as proof of a user's agreement.
    """
    if (
        account is None
        or not account.autoreload_enabled
        or account.autoreload_consent_version != AUTO_RELOAD_CONSENT_VERSION
        or account.autoreload_consent_at is None
    ):
        return False
    return AgentccAutoReloadConsent.objects.filter(
        organization_id=account.organization_id,
        consent_version=AUTO_RELOAD_CONSENT_VERSION,
        accepted_at=account.autoreload_consent_at,
        threshold_micros=account.autoreload_threshold_micros,
        amount_micros=account.autoreload_amount_micros,
        actor_id=F("actor_identifier"),
    ).exists()
