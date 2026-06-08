import uuid
from collections import Counter
from dataclasses import dataclass

from django.utils import timezone

from accounts.models import OnboardingLifecycleEvaluationLog
from accounts.services.onboarding.activation_state import resolve_activation_state
from accounts.services.onboarding.context import resolve_onboarding_context
from accounts.services.onboarding.feature_flags import get_onboarding_flags
from accounts.services.onboarding.lifecycle_candidates import lifecycle_candidates
from accounts.services.onboarding.lifecycle_eligibility import (
    evaluate_lifecycle_decision,
    write_lifecycle_evaluation,
)
from accounts.services.onboarding.signal_resolver import (
    OnboardingSignals,
    collect_onboarding_signals,
)


@dataclass(frozen=True)
class LifecycleBatchResult:
    run_id: uuid.UUID
    evaluated: int
    written: int
    status_counts: dict
    campaign_counts: dict
    suppression_counts: dict
    errors: list[dict]

    def to_payload(self):
        return {
            "run_id": str(self.run_id),
            "evaluated": self.evaluated,
            "written": self.written,
            "status_counts": self.status_counts,
            "campaign_counts": self.campaign_counts,
            "suppression_counts": self.suppression_counts,
            "errors": self.errors,
        }


class _Request:
    def __init__(self, *, user, organization, workspace, source):
        self.user = user
        self.organization = organization
        self.workspace = workspace
        self.query_params = {"source": source}


def _empty_signals():
    return OnboardingSignals(first_checks={})


def _activation_state_for_candidate(*, candidate, source):
    request = _Request(
        user=candidate.user,
        organization=candidate.organization,
        workspace=candidate.workspace,
        source=source,
    )
    context = resolve_onboarding_context(request)
    flags = get_onboarding_flags(
        user=context.user,
        organization=context.organization,
        workspace=context.workspace,
    )
    if not flags.get("onboarding_activation_state_api"):
        signals = _empty_signals()
    else:
        signals = collect_onboarding_signals(
            user=context.user,
            organization=context.organization,
            workspace=context.workspace,
        )
    activation_state = resolve_activation_state(
        context=context,
        flags=flags,
        signals=signals,
    )
    return context, flags, activation_state


def _write_error_log(*, candidate, run_id, error, now, source):
    return OnboardingLifecycleEvaluationLog.no_workspace_objects.create(
        run_id=run_id,
        user=candidate.user,
        organization=candidate.organization,
        workspace=candidate.workspace,
        activation_stage="error",
        status=OnboardingLifecycleEvaluationLog.STATUS_ERROR,
        suppression_reason="activation_state_error",
        suppression_details={"error": str(error)[:500]},
        evaluated_at=now,
        metadata={"source": source, "send_enabled": False},
    )


def run_onboarding_lifecycle_dry_run(
    *,
    limit=100,
    user_id=None,
    workspace_id=None,
    campaign_key=None,
    source="lifecycle_dry_run",
    write=True,
    run_id=None,
    now=None,
):
    now = now or timezone.now()
    run_id = run_id or uuid.uuid4()
    candidates = lifecycle_candidates(
        limit=limit,
        user_id=user_id,
        workspace_id=workspace_id,
    )

    status_counts = Counter()
    campaign_counts = Counter()
    suppression_counts = Counter()
    errors = []
    written = 0

    for candidate in candidates:
        try:
            context, flags, activation_state = _activation_state_for_candidate(
                candidate=candidate,
                source=source,
            )
            decision = evaluate_lifecycle_decision(
                user=context.user,
                organization=context.organization,
                workspace=context.workspace,
                activation_state=activation_state,
                flags=flags,
                now=now,
                run_id=run_id,
                source=source,
                campaign_key=campaign_key,
            )
            status_counts[decision.status] += 1
            campaign_counts[
                (decision.campaign or {}).get("campaign_key") or "none"
            ] += 1
            if decision.suppression_reason:
                suppression_counts[decision.suppression_reason] += 1
            if write:
                write_lifecycle_evaluation(decision)
                written += 1
        except Exception as exc:
            status_counts[OnboardingLifecycleEvaluationLog.STATUS_ERROR] += 1
            suppression_counts["activation_state_error"] += 1
            errors.append(
                {
                    "user_id": str(candidate.user.id),
                    "workspace_id": str(candidate.workspace.id),
                    "error": str(exc)[:500],
                }
            )
            if write:
                _write_error_log(
                    candidate=candidate,
                    run_id=run_id,
                    error=exc,
                    now=now,
                    source=source,
                )
                written += 1

    return LifecycleBatchResult(
        run_id=run_id,
        evaluated=len(candidates),
        written=written,
        status_counts=dict(status_counts),
        campaign_counts=dict(campaign_counts),
        suppression_counts=dict(suppression_counts),
        errors=errors,
    )
