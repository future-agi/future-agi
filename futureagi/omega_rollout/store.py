"""Read the rollout state for one decision. Nothing is cached across requests, so a change applies to
the next request (READINESS RC-04); each read is two indexed queries over a handful of rows.

Every read failure raises RolloutUnavailable: a caller must refuse (fail closed, RC-07), never admit.
"""

from __future__ import annotations

from django.db import DatabaseError

from omega_rollout import products as p
from omega_rollout.decision import AccessEntry, ProductState


class RolloutUnavailable(Exception):
    """The rollout store could not be read. Callers answer 503 `verification_unavailable`."""


def _state(row) -> ProductState:
    return ProductState(
        product=row.product,
        stage=row.stage,
        percentage=row.percentage,
        paused=row.paused,
        installs_paused=row.installs_paused,
        epoch=row.epoch,
    )


def load_states() -> dict[str, ProductState]:
    from omega_rollout.models import ProductRollout

    try:
        rows = list(ProductRollout.objects.all())
    except DatabaseError as exc:
        raise RolloutUnavailable("rollout state unreadable") from exc
    states = {product: ProductState(product) for product in p.HOSTED_PRODUCTS}
    for row in rows:
        if row.product in states:
            states[row.product] = _state(row)
    return states


def load_entries(organization_id: str | None) -> list[AccessEntry]:
    if not organization_id:
        return []
    from omega_rollout.models import ProductAccess

    try:
        rows = list(
            ProductAccess.objects.filter(organization_id=organization_id).values_list(
                "product", "organization_id", "workspace_id", "effect", "cohort"
            )
        )
    except (DatabaseError, ValueError) as exc:
        # ValueError: an org id that is not a UUID cannot name a row; refuse rather than guess.
        raise RolloutUnavailable("rollout entries unreadable") from exc
    return [
        AccessEntry(
            product=product,
            organization_id=str(org_id),
            workspace_id=str(workspace_id) if workspace_id else None,
            effect=effect,
            cohort=cohort,
        )
        for product, org_id, workspace_id, effect, cohort in rows
    ]


def current_epoch() -> int:
    from omega_rollout.models import RolloutEpoch

    try:
        row = RolloutEpoch.objects.filter(pk=1).values_list("epoch", flat=True).first()
    except DatabaseError as exc:
        raise RolloutUnavailable("rollout epoch unreadable") from exc
    return int(row or 0)
