"""One workspace allowance for text, assistance and native work; never an executor.

Lock order: organization execution quota -> workspace -> policy -> reservations.
Call admission before acquiring resource/job locks. Calls consume their admission
ceiling, not a second balance. Calendar periods are UTC months; no amount defaults.
Before an administrator sets a workspace cap, legacy text/assist work retains its
explicit per-job ceilings. Its revision-zero ledger has no period cap, not a
fabricated dollar allowance. Native execution still requires an explicit policy.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from django.db import transaction
from django.db.models import Case, DecimalField, F, Sum, When
from django.db.models.functions import Coalesce
from django.utils import timezone

from accounts.models.workspace import Workspace
from model_hub.models.skills_hub_r3 import (
    HubSkillSpendPeriod,
    HubSkillSpendPolicy,
    HubSkillSpendReservation,
)
from model_hub.models.skills_hub_v11 import (
    HubSkillExecutionQuota,
    HubSkillJob,
    HubSkillModelCall,
)
from model_hub.models.skills_hub_v12 import HubSkillContextOperation
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v11.principals import (
    effective_scope_level,
    require_private_principal,
    require_scope_access,
)
from model_hub.services.skills_hub.v11.types import SkillPrincipal, SkillScope
from model_hub.services.skills_hub.v12.credentials import canonical_digest
from tfc.constants.levels import Level

ZERO = Decimal("0.00000000")
SOURCES = {"skills_text", "skills_assist", "simulation_native"}
CALL_KINDS = {"task", "judge", "assist", "tool"}


def _error(code: str, message: str, status: int = 409) -> SkillsHubError:
    return SkillsHubError(code, message, status=status)


def _money(value: object) -> Decimal:
    try:
        if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
            raise ValueError
        amount = Decimal(value)
        if (
            not amount.is_finite()
            or amount < 0
            or amount >= Decimal("10000000000")
            or amount != amount.quantize(ZERO)
        ):
            raise ValueError
        return amount
    except (ValueError, TypeError, InvalidOperation) as exc:
        raise _error(
            "invalid_spend_policy",
            "Use a nonnegative USD decimal with at most eight places.",
            400,
        ) from exc


def _scope(scope: SkillScope) -> dict:
    return {
        "organization_id": scope.organization_id,
        "workspace_id": scope.workspace_id,
    }


def _lock(scope: SkillScope) -> HubSkillSpendPolicy | None:
    from model_hub.services.skills_hub.v11.jobs import lock_quota

    lock_quota(scope.organization_id)
    Workspace.no_workspace_objects.select_for_update(no_key=True).get(
        pk=scope.workspace_id, organization_id=scope.organization_id
    )
    return (
        HubSkillSpendPolicy.objects.select_for_update().filter(**_scope(scope)).first()
    )


def _bounds(now: datetime) -> tuple[datetime, datetime]:
    now = now.astimezone(UTC)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    return start, end


def _charges(rows) -> Decimal:
    total = rows.aggregate(
        total=Coalesce(
            Sum(
                Case(
                    When(state="released", then=ZERO),
                    When(settled_usd__isnull=False, then=F("settled_usd")),
                    default=F("reserved_usd"),
                    output_field=DecimalField(max_digits=18, decimal_places=8),
                )
            ),
            ZERO,
        )
    )["total"]
    return Decimal(total)


def _committed(period: HubSkillSpendPeriod) -> Decimal:
    # Open ceilings include their children. Closed admissions release only unused
    # headroom, while all known/unknown child costs remain in the original period.
    roots = period.hub_skill_reservations.filter(parent__isnull=True)
    open_total = roots.exclude(state="closed").aggregate(
        total=Coalesce(Sum("reserved_usd"), ZERO)
    )["total"]
    closed = _charges(period.hub_skill_reservations.filter(parent__state="closed"))
    # An actual provider bill can exceed the estimate; never discard that spend.
    excess = ZERO
    for root in (
        roots.exclude(state="closed")
        .annotate(
            used=Coalesce(
                Sum(
                    Case(
                        When(hub_skill_calls__state="released", then=ZERO),
                        When(
                            hub_skill_calls__settled_usd__isnull=False,
                            then=F("hub_skill_calls__settled_usd"),
                        ),
                        default=F("hub_skill_calls__reserved_usd"),
                        output_field=DecimalField(max_digits=18, decimal_places=8),
                    )
                ),
                ZERO,
            )
        )
        .iterator(chunk_size=100)
    ):
        excess += max(ZERO, Decimal(root.used) - root.reserved_usd)
    return Decimal(open_total) + closed + excess


def _require_policy(
    policy: HubSkillSpendPolicy | None, *, source: str
) -> HubSkillSpendPolicy:
    if (
        policy is None
        or not policy.enabled
        or (source == "simulation_native" and policy.period_limit_usd is None)
    ):
        raise _error(
            "spend_policy_required",
            "An administrator must explicitly enable a workspace allowance.",
            403,
        )
    return policy


def _authorize(principal: SkillPrincipal) -> None:
    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=True)


@transaction.atomic
def update_spend_policy(
    *,
    principal: SkillPrincipal,
    revision: int,
    enabled: bool,
    period_limit_usd: Decimal | str,
    idempotency_key: UUID,
) -> HubSkillSpendPolicy:
    _authorize(principal)
    assert principal.user_id is not None
    if effective_scope_level(principal=principal) < Level.WORKSPACE_ADMIN:
        raise _error(
            "permission_denied", "A current administrator must authorize spending.", 403
        )
    amount = _money(period_limit_usd)
    if type(enabled) is not bool or type(revision) is not int or revision < 0:
        raise _error(
            "invalid_spend_policy",
            "Use an explicit enablement and policy revision.",
            400,
        )
    policy = _lock(principal.scope)
    receipt_key = dict(
        **_scope(principal.scope),
        principal_key=f"user:{principal.user_id}",
        operation="spend_policy",
        idempotency_key=idempotency_key,
    )
    digest = canonical_digest([revision, enabled, str(amount)])
    receipt = HubSkillContextOperation.objects.filter(**receipt_key).first()
    if receipt:
        if receipt.request_digest != digest:
            raise _error(
                "idempotency_conflict", "This operation key has another request."
            )
        assert policy is not None
        return policy
    if revision != (policy.revision if policy else 0):
        raise _error("spend_policy_conflict", "The workspace spending policy changed.")
    before = (
        None
        if policy is None
        else {
            "revision": policy.revision,
            "enabled": policy.enabled,
            "period_limit_usd": (
                str(policy.period_limit_usd)
                if policy.period_limit_usd is not None
                else None
            ),
        }
    )
    if policy is None:
        policy = HubSkillSpendPolicy.objects.create(
            **_scope(principal.scope),
            enabled=enabled,
            period_limit_usd=amount,
            updated_by_id=principal.user_id,
        )
    else:
        policy.revision += 1
        policy.enabled, policy.period_limit_usd, policy.updated_by_id = (
            enabled,
            amount,
            principal.user_id,
        )
        policy.save(
            update_fields=["revision", "enabled", "period_limit_usd", "updated_by"]
        )
    HubSkillContextOperation.objects.create(
        **receipt_key,
        request_digest=digest,
        result_resource_id=policy.pk,
        completed_at=timezone.now(),
        response={
            "before": before,
            "after": {
                "revision": policy.revision,
                "enabled": enabled,
                "period_limit_usd": str(amount),
            },
            "actor_api_key_id": (
                str(principal.api_key_id) if principal.api_key_id else None
            ),
        },
    )
    return policy


def spend_summary(*, principal: SkillPrincipal) -> dict:
    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=False)
    policy = HubSkillSpendPolicy.objects.filter(**_scope(principal.scope)).first()
    start, end = _bounds(timezone.now())
    period = (
        None
        if policy is None
        else policy.hub_skill_periods.filter(starts_at=start).first()
    )
    committed = _committed(period) if period else ZERO
    rows = HubSkillSpendReservation.objects.filter(
        **_scope(principal.scope), parent__isnull=False
    )
    limit = policy.period_limit_usd if policy else None
    return {
        "revision": policy.revision if policy else 0,
        "enabled": policy.enabled if policy else True,
        "period_limit_usd": str(limit) if limit is not None else None,
        "starts_at": start.isoformat(),
        "ends_at": end.isoformat(),
        "committed_usd": str(committed),
        "remaining_usd": (
            str(max(ZERO, limit - committed)) if limit is not None else None
        ),
        "known_estimate_usd": str(
            rows.filter(period=period).aggregate(
                total=Coalesce(Sum("settled_usd"), ZERO)
            )["total"]
        ),
        "unknown_reserved_usd": str(_charges(rows.filter(state="unknown"))),
        "prior_period_unknown_usd": str(
            _charges(rows.filter(state="unknown").exclude(period=period))
        ),
        "active_senders": rows.filter(sender_active=True).count(),
        "commercial_basis": "provider_estimates_not_invoices",
        "simulation_entitlements": "additional_checks_not_replaced",
    }


@transaction.atomic
def reserve_admission(
    *,
    principal: SkillPrincipal,
    source: str,
    source_id: UUID,
    operation_id: UUID,
    ceiling_usd: Decimal | str,
    generation: int = 0,
) -> HubSkillSpendReservation:
    _authorize(principal)
    amount = _money(ceiling_usd)
    if source not in SOURCES or type(generation) is not int or generation < 0:
        raise _error(
            "invalid_spend_policy",
            "Use a supported execution source and generation.",
            400,
        )
    policy = _lock(principal.scope)
    old = HubSkillSpendReservation.objects.filter(
        **_scope(principal.scope), operation_id=operation_id
    ).first()
    if old:
        if (
            old.source != source
            or old.source_id != source_id
            or old.parent_id
            or old.reserved_usd != amount
            or old.claim_generation != generation
        ):
            raise _error(
                "idempotency_conflict", "The admission operation has another identity."
            )
        return old
    if policy is None and source in {"skills_text", "skills_assist"}:
        # Workspace/quota locks serialize this with the first administrative
        # update. The same row/period is retained when a real cap is introduced,
        # so existing reservations and unknown costs cannot escape that cap.
        assert principal.user_id is not None  # Required by _authorize above.
        policy = HubSkillSpendPolicy.objects.create(
            **_scope(principal.scope),
            revision=0,
            enabled=True,
            period_limit_usd=None,
            updated_by_id=principal.user_id,
        )
    policy = _require_policy(policy, source=source)
    start, end = _bounds(timezone.now())
    period, _ = HubSkillSpendPeriod.objects.get_or_create(
        policy=policy,
        starts_at=start,
        defaults={**_scope(principal.scope), "ends_at": end},
    )
    if (
        policy.period_limit_usd is not None
        and _committed(period) + amount > policy.period_limit_usd
    ):
        raise _error(
            "budget_exhausted", "The workspace period allowance is exhausted.", 429
        )
    return HubSkillSpendReservation.objects.create(
        **_scope(principal.scope),
        period=period,
        operation_id=operation_id,
        source=source,
        source_id=source_id,
        kind="admission",
        claim_generation=generation,
        reserved_usd=amount,
    )


def _reservation(scope: SkillScope, reservation_id: UUID) -> HubSkillSpendReservation:
    row = (
        HubSkillSpendReservation.objects.select_for_update()
        .filter(**_scope(scope), pk=reservation_id)
        .first()
    )
    if row is None:
        raise _error("resource_not_found", "Reservation not found.", 404)
    return row


@transaction.atomic
def reserve_paid_call(
    *,
    principal: SkillPrincipal,
    admission_id: UUID,
    operation_id: UUID,
    kind: str,
    allowance_usd: Decimal | str,
    generation: int,
) -> HubSkillSpendReservation:
    _authorize(principal)
    amount = _money(allowance_usd)
    if kind not in CALL_KINDS or type(generation) is not int or generation < 0:
        raise _error(
            "invalid_spend_policy",
            "Use a supported paid-call kind and generation.",
            400,
        )
    policy = _lock(principal.scope)
    root = _reservation(principal.scope, admission_id)
    old = HubSkillSpendReservation.objects.filter(
        **_scope(principal.scope), operation_id=operation_id
    ).first()
    if old:
        if (
            old.parent_id != root.pk
            or old.kind != kind
            or old.reserved_usd != amount
            or old.claim_generation != generation
        ):
            raise _error(
                "idempotency_conflict", "The paid operation has another identity."
            )
        return old  # This is a receipt, never permission to resend.
    policy = _require_policy(policy, source=root.source)
    if root.parent_id or root.kind != "admission" or root.state == "closed":
        raise _error("reservation_closed", "This admission is not open.")
    if (
        policy.period_limit_usd is not None
        and _committed(root.period) > policy.period_limit_usd
    ) or _charges(root.hub_skill_calls.all()) + amount > root.reserved_usd:
        raise _error(
            "budget_exhausted",
            "The reviewed admission or workspace allowance is exhausted.",
            429,
        )
    return HubSkillSpendReservation.objects.create(
        **_scope(principal.scope),
        period=root.period,
        parent=root,
        source=root.source,
        source_id=root.source_id,
        operation_id=operation_id,
        kind=kind,
        claim_generation=generation,
        reserved_usd=amount,
    )


@transaction.atomic
def mark_paid_call_sent(
    *, principal: SkillPrincipal, reservation_id: UUID, generation: int
) -> None:
    _authorize(principal)
    policy = _lock(principal.scope)
    row = _reservation(principal.scope, reservation_id)
    policy = _require_policy(policy, source=row.source)
    if (
        row.claim_generation != generation
        or row.state != "prepared"
        or row.parent is None
        or row.parent.state == "closed"
    ):
        raise _error("execution_unknown", "This call is not eligible for transmission.")
    if (
        policy.period_limit_usd is not None
        and _committed(row.period) > policy.period_limit_usd
    ):
        raise _error("budget_exhausted", "The workspace allowance was reduced.", 429)
    quota = HubSkillExecutionQuota.objects.get(
        organization_id=principal.scope.organization_id
    )
    active = HubSkillSpendReservation.objects.filter(
        organization_id=principal.scope.organization_id,
        parent__isnull=False,
        sender_active=True,
    ).count()
    if active >= min(4, quota.max_active_provider_calls):
        raise _error(
            "skills_capacity_exceeded", "Shared provider sender capacity is full.", 429
        )
    row.state, row.sender_active = "in_flight", True
    row.save(update_fields=["state", "sender_active"])


@transaction.atomic
def settle_paid_call(
    *,
    scope: SkillScope,
    reservation_id: UUID,
    generation: int,
    actual_usd: Decimal | str | None,
    sender_stopped: bool,
) -> HubSkillSpendReservation:
    _lock(scope)
    row = _reservation(scope, reservation_id)
    actual = None if actual_usd is None else _money(actual_usd)
    if type(sender_stopped) is not bool:
        raise _error(
            "reservation_conflict", "Explicit sender settlement is required.", 400
        )
    if row.claim_generation != generation:
        raise _error("stale_generation", "This worker cannot settle the reservation.")
    if row.parent_id is None or row.state not in {"in_flight", "unknown", "settled"}:
        raise _error("reservation_conflict", "Only a transmitted paid call can settle.")
    if row.state == "settled" and actual != row.settled_usd:
        raise _error("reservation_conflict", "Known usage cannot be replaced.")
    row.state = "unknown" if actual is None else "settled"
    row.settled_usd = actual
    row.sender_active = row.sender_active and not sender_stopped
    row.settled_at = timezone.now()
    row.save(update_fields=["state", "settled_usd", "sender_active", "settled_at"])
    return row


@transaction.atomic
def close_admission(
    *, scope: SkillScope, admission_id: UUID
) -> HubSkillSpendReservation:
    _lock(scope)
    root = _reservation(scope, admission_id)
    if root.parent_id is not None:
        raise _error("reservation_conflict", "Close an admission, not a paid call.")
    root.hub_skill_calls.filter(state="prepared", sender_active=False).update(
        state="released", settled_usd=ZERO
    )
    root.state = "closed"
    root.save(update_fields=["state"])
    return root


@transaction.atomic
def release_unsent_call(
    *, scope: SkillScope, reservation_id: UUID, generation: int
) -> None:
    _lock(scope)
    row = _reservation(scope, reservation_id)
    if (
        row.parent_id is None
        or row.claim_generation != generation
        or row.sender_active
        or row.state not in {"prepared", "released"}
    ):
        raise _error("execution_unknown", "Only provably unsent work can be released.")
    row.state, row.settled_usd = "released", ZERO
    row.save(update_fields=["state", "settled_usd"])


def lock_spend_authority(*, scope: SkillScope) -> None:
    """Acceptance hook: acquire before skill/suite/draft/job locks, no I/O."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Spending authority must join the caller's transaction")
    _lock(scope)


def reserve_text_job(*, job: HubSkillJob) -> HubSkillSpendReservation:
    """Join existing job acceptance; job UUID is the immutable admission operation."""
    from model_hub.services.skills_hub.v11.jobs import principal_for_job

    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Job admission must be atomic with job creation")
    if job.kind not in {"test", "assist"} or job.max_estimated_cost_usd is None:
        raise _error(
            "invalid_spend_policy", "Only priced model jobs use this bridge.", 400
        )
    return reserve_admission(
        principal=principal_for_job(job),
        source="skills_assist" if job.kind == "assist" else "skills_text",
        source_id=job.pk,
        operation_id=job.pk,
        ceiling_usd=job.max_estimated_cost_usd,
    )


def reserve_text_call(*, call: HubSkillModelCall) -> HubSkillSpendReservation:
    """Join existing call preparation; no new provider request or retry path."""
    from model_hub.services.skills_hub.v11.jobs import principal_for_job

    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Call preparation must be atomic with call creation")
    principal = principal_for_job(call.job)
    root = HubSkillSpendReservation.objects.filter(
        **_scope(principal.scope), operation_id=call.job_id, parent__isnull=True
    ).first()
    if root is None:
        raise _error("spend_policy_required", "The job has no shared admission.", 403)
    return reserve_paid_call(
        principal=principal,
        admission_id=root.pk,
        operation_id=call.pk,
        kind=call.kind,
        allowance_usd=call.reserved_estimate_usd,
        generation=call.claim_generation,
    )


def text_call_reservation_id(*, call: HubSkillModelCall) -> UUID:
    row = HubSkillSpendReservation.objects.filter(
        organization_id=call.job.organization_id,
        workspace_id=call.job.workspace_id,
        operation_id=call.pk,
        source_id=call.job_id,
        parent__isnull=False,
    ).first()
    if row is None:
        raise _error(
            "spend_policy_required", "The call has no shared reservation.", 403
        )
    return row.pk
