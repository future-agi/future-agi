"""Every rollout change, made by a named staff operator (READINESS 3.13 "Humans operate production").

Each function first checks its operator (omega_rollout/authority.py), then runs in one transaction: it
bumps the rollout epoch, changes the rows, writes the audit (omega_rollout/audit.py) and, after commit,
sends `product_state_changed` so the 1.2a channel can carry a signed `product.state` event to EE
(the 5 s kill path). Without that event the next key Django mints already carries the new state, so
the gateway and EE refuse within the 45 s key lifetime.
"""

from __future__ import annotations

from django.db import transaction
from django.dispatch import Signal
from django.utils import timezone

from omega_rollout import audit
from omega_rollout import products as p
from omega_rollout.authority import SEED, RolloutChangeRefused, require_operator
from omega_rollout.models import (
    ProductAccess,
    ProductRollout,
    RolloutEpoch,
    WaitlistRequest,
)

__all__ = ["SEED", "RolloutChangeRefused", "product_state_changed"]

# Sent after commit with product, state ("active" | "paused"), stage, percentage and epoch.
product_state_changed = Signal()


def _require_product(product: str) -> None:
    if not p.is_product(product):
        raise RolloutChangeRefused("unknown_product", f"Unknown product {product!r}.")


def _require_reason(reason: str) -> str:
    text = (reason or "").strip()
    if not text:
        raise RolloutChangeRefused(
            "reason_required", "Every rollout change needs a reason."
        )
    return text[:2000]


def _bump_epoch() -> int:
    row, _ = RolloutEpoch.objects.select_for_update().get_or_create(pk=1)
    row.epoch += 1
    row.save(update_fields=["epoch"])
    return row.epoch


def _rollout_row(product: str) -> ProductRollout:
    row, _ = ProductRollout.objects.select_for_update().get_or_create(product=product)
    return row


def _snapshot(row: ProductRollout) -> dict:
    return {
        "stage": row.stage,
        "percentage": row.percentage,
        "paused": row.paused,
        "installs_paused": row.installs_paused,
    }


def _announce(row: ProductRollout) -> None:
    payload = {
        "product": row.product,
        "state": p.STATE_PAUSED if row.paused else p.STATE_ACTIVE,
        "stage": row.stage,
        "percentage": row.percentage,
        "epoch": row.epoch,
    }
    transaction.on_commit(
        lambda: product_state_changed.send(sender=ProductRollout, **payload)
    )


def _change_rollout(
    product, actor, reason, action, apply, counts=None
) -> ProductRollout:
    actor = require_operator(actor)
    _require_product(product)
    reason = _require_reason(reason)
    with transaction.atomic():
        epoch = _bump_epoch()
        row = _rollout_row(product)
        before = _snapshot(row)
        apply(row)
        row.epoch = epoch
        row.updated_by = actor
        row.save()
        audit.record(
            action=action,
            product=product,
            actor=actor,
            before=before,
            after=_snapshot(row),
            reason=reason,
            epoch=epoch,
            counts=counts(row) if counts else None,
        )
        _announce(row)
    return row


def set_stage(product: str, stage: str, *, actor, reason: str) -> ProductRollout:
    if stage not in p.STAGES:
        raise RolloutChangeRefused("unknown_stage", f"Unknown stage {stage!r}.")

    def apply(row):
        row.stage = stage

    return _change_rollout(product, actor, reason, "stage_changed", apply)


def set_percentage(
    product: str, percentage: int, *, actor, reason: str
) -> ProductRollout:
    if type(percentage) is not int or not 0 <= percentage <= 100:
        raise RolloutChangeRefused(
            "invalid_percentage", "The percentage is a whole number 0-100."
        )

    def apply(row):
        if row.stage != p.S3 and percentage:
            raise RolloutChangeRefused(
                "stage_not_s3", "A percentage applies only at S3."
            )
        row.percentage = percentage

    return _change_rollout(product, actor, reason, "percentage_changed", apply)


def _hub_counts(row: ProductRollout) -> dict:
    if row.product != p.HUB:
        return {}
    from omega_hub.models.submission import CheckJob

    running = CheckJob.objects.filter(status=CheckJob.Status.RUNNING).count()
    queued = CheckJob.objects.filter(status=CheckJob.Status.QUEUED).count()
    return {
        "refused": "new submissions, reviews and checks",
        "draining_checks": running,
        "held_checks": queued,
        "cancelled": 0,
    }


def kill(
    product: str, *, actor, reason: str, pause_installs: bool = False
) -> ProductRollout:
    """Pause a product. A paused Hub can be killed again with `pause_installs` to stop installs as
    well, mid-incident, without restoring it first (which would reopen submissions)."""
    stop_installs = bool(pause_installs) and product == p.HUB

    def apply(row):
        if row.paused and not (stop_installs and not row.installs_paused):
            raise RolloutChangeRefused(
                "already_paused", "The product is already paused."
            )
        row.paused = True
        row.installs_paused = row.installs_paused or stop_installs

    return _change_rollout(product, actor, reason, "killed", apply, counts=_hub_counts)


def restore(product: str, *, actor, reason: str) -> ProductRollout:
    def apply(row):
        if not row.paused:
            raise RolloutChangeRefused("not_paused", "The product is not paused.")
        row.paused = False
        row.installs_paused = False

    return _change_rollout(product, actor, reason, "restored", apply)


def _entry_snapshot(entry: ProductAccess | None) -> dict:
    if entry is None:
        return {}
    return {"effect": entry.effect, "cohort": entry.cohort}


def set_access(
    product,
    organization,
    *,
    effect,
    cohort=p.COHORT_PARTNER,
    workspace=None,
    actor,
    reason: str,
    action: str | None = None,
) -> ProductAccess:
    """Add or change an allow or deny entry for an org, or one workspace of it."""
    actor = require_operator(actor)
    _require_product(product)
    reason = _require_reason(reason)
    if effect not in (p.EFFECT_ALLOW, p.EFFECT_DENY):
        raise RolloutChangeRefused("invalid_effect", "The effect is allow or deny.")
    if cohort not in p.COHORTS:
        raise RolloutChangeRefused("invalid_cohort", f"Unknown cohort {cohort!r}.")
    if workspace is not None and workspace.organization_id != organization.pk:
        raise RolloutChangeRefused(
            "workspace_not_in_org", "The workspace belongs to another org."
        )
    with transaction.atomic():
        epoch = _bump_epoch()
        entry = (
            ProductAccess.objects.select_for_update()
            .filter(product=product, organization=organization, workspace=workspace)
            .first()
        )
        before = _entry_snapshot(entry)
        if entry is None:
            entry = ProductAccess(
                product=product,
                organization=organization,
                workspace=workspace,
                created_by=actor,
            )
        entry.effect = effect
        entry.cohort = cohort
        entry.reason = reason
        entry.save()
        audit.record(
            action=action or f"{effect}_set",
            product=product,
            actor=actor,
            before=before,
            after=_entry_snapshot(entry),
            reason=reason,
            epoch=epoch,
            organization=organization,
            workspace_id=getattr(workspace, "pk", None),
        )
        _announce(_rollout_row(product))
    return entry


def remove_access(product, organization, *, workspace=None, actor, reason: str) -> None:
    actor = require_operator(actor)
    _require_product(product)
    reason = _require_reason(reason)
    with transaction.atomic():
        entry = (
            ProductAccess.objects.select_for_update()
            .filter(product=product, organization=organization, workspace=workspace)
            .first()
        )
        if entry is None:
            raise RolloutChangeRefused("no_entry", "There is no entry to remove.")
        epoch = _bump_epoch()
        before = _entry_snapshot(entry)
        entry.delete()
        audit.record(
            action="access_removed",
            product=product,
            actor=actor,
            before=before,
            after={},
            reason=reason,
            epoch=epoch,
            organization=organization,
            workspace_id=getattr(workspace, "pk", None),
        )
        _announce(_rollout_row(product))


def request_access(
    product: str, organization, *, user, note: str = ""
) -> WaitlistRequest:
    """An org asks for early access. Idempotent: asking again returns the open request."""
    _require_product(product)
    with transaction.atomic():
        existing = (
            WaitlistRequest.objects.select_for_update()
            .filter(product=product, organization=organization)
            .first()
        )
        if existing is not None:
            return existing
        request = WaitlistRequest.objects.create(
            product=product,
            organization=organization,
            requested_by=user,
            note=(note or "")[:1000],
        )
        audit.record(
            action="access_requested",
            product=product,
            actor=user,
            before={},
            after={"status": WaitlistRequest.REQUESTED},
            reason=(note or "early access request")[:2000],
            epoch=0,
            organization=organization,
            actor_organization=organization,
        )
    return request


def decide_request(
    waitlist_request: WaitlistRequest, *, invite: bool, actor, reason: str
):
    """Invite (admit, at S2 or later) or decline a waitlist request.

    An invitation never overrides an explicit org-level deny: the operator removes the deny first,
    which is its own audited change, so a deny is never lifted as a side effect of a batch invite.
    A seed never decides a request: only a named operator does.
    """
    actor = require_operator(actor, allow_seed=False)
    reason = _require_reason(reason)
    with transaction.atomic():
        current = WaitlistRequest.objects.select_for_update().get(
            pk=waitlist_request.pk
        )
        if current.status != WaitlistRequest.REQUESTED:
            raise RolloutChangeRefused(
                "already_decided", "This request was already decided."
            )
        if invite:
            stage = (
                ProductRollout.objects.filter(product=current.product)
                .values_list("stage", flat=True)
                .first()
                or p.S0
            )
            if p.STAGE_RANK[stage] < p.STAGE_RANK[p.S2]:
                raise RolloutChangeRefused(
                    "stage_below_waitlist",
                    "Move the product to S2 before inviting from the waitlist.",
                )
            if ProductAccess.objects.filter(
                product=current.product,
                organization=current.organization,
                workspace=None,
                effect=p.EFFECT_DENY,
            ).exists():
                raise RolloutChangeRefused(
                    "organization_denied",
                    "This organization has a deny entry for the product. Remove the deny"
                    " first if it should be invited.",
                )
            set_access(
                current.product,
                current.organization,
                effect=p.EFFECT_ALLOW,
                cohort=p.COHORT_WAITLIST,
                actor=actor,
                reason=reason,
                action="invited",
            )
        current.status = WaitlistRequest.INVITED if invite else WaitlistRequest.DECLINED
        current.decided_at = timezone.now()
        current.decided_by = actor
        current.save(update_fields=["status", "decided_at", "decided_by"])
        if not invite:
            audit.record(
                action="request_declined",
                product=current.product,
                actor=actor,
                before={"status": WaitlistRequest.REQUESTED},
                after={"status": current.status},
                reason=reason,
                epoch=0,
                organization=current.organization,
            )
    return current
