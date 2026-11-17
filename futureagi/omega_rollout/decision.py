"""The rollout decision, as a pure function of the stored state (READINESS 3.13).

No Django imports: the same function runs in every Django worker and in the tests, and the only input
is data the store read. Two workers given the same rows give the same answer, because the percentage
uses SHA-256 of the product and org id (never Python's per-process `hash()`).

Order, for product P, organization O and workspace W:
1. a deny entry for (P, O, W) or for (P, O) refuses; deny beats every allow and the percentage;
2. an allow entry for (P, O) or (P, O, W) admits when the product's stage has reached the entry's
   cohort (staff from S0, partner from S1, waitlist from S2);
3. at S3, a stable hash of (P, O) under the percentage admits; raising the percentage never removes an
   org it admitted, because the threshold only grows;
4. an admitted product that is paused is `paused`, otherwise `active`; anything else is not admitted.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from omega_rollout import products as p

# Resolution of the percentage: 10,000 buckets, so a percentage is exact to 0.01.
BUCKETS = 10_000


@dataclass(frozen=True)
class ProductState:
    """One product's stored rollout state. A product with no row is S0, 0%, not paused (dark)."""

    product: str
    stage: str = p.S0
    percentage: int = 0
    paused: bool = False
    installs_paused: bool = False
    epoch: int = 0


@dataclass(frozen=True)
class AccessEntry:
    product: str
    organization_id: str
    workspace_id: str | None
    effect: str
    cohort: str


@dataclass(frozen=True)
class Admission:
    product: str
    admitted: bool
    state: str | None  # "active" | "paused" when admitted
    # Why, logged with every refusal (RC-03): allow:<cohort> or percentage when admitted;
    # denied:workspace, denied:organization, not_listed:<stage>, outside_percentage or anonymous
    # when refused.
    reason: str

    @property
    def paused(self) -> bool:
        return self.admitted and self.state == p.STATE_PAUSED


def bucket(product: str, organization_id: str) -> int:
    """The org's stable bucket for a product, 0..BUCKETS-1."""
    digest = hashlib.sha256(
        f"omega-rollout:{product}:{organization_id}".encode()
    ).digest()
    return int.from_bytes(digest[:8], "big") % BUCKETS


def under_percentage(product: str, organization_id: str, percentage: int) -> bool:
    if percentage <= 0:
        return False
    if percentage >= 100:
        return True
    return bucket(product, organization_id) < percentage * (BUCKETS // 100)


def _stage_admits(stage: str, cohort: str) -> bool:
    first = p.COHORT_FIRST_STAGE.get(cohort)
    if first is None or stage not in p.STAGE_RANK:
        return False
    return p.STAGE_RANK[stage] >= p.STAGE_RANK[first]


def decide(
    state: ProductState,
    organization_id: str | None,
    workspace_id: str | None,
    entries: list[AccessEntry] | tuple[AccessEntry, ...],
) -> Admission:
    """Admit or refuse `organization_id` (in `workspace_id`) to `state.product`.

    `entries` may hold entries for other products and orgs; only (product, org) entries count, and a
    workspace entry counts only for its own workspace. An anonymous caller (no org) is admitted only
    when the product is at S3 and 100%.
    """
    product = state.product
    if not organization_id:
        if state.stage == p.S3 and state.percentage >= 100:
            return _admitted(state, "percentage")
        return Admission(product, False, None, "anonymous")
    relevant = [
        entry
        for entry in entries
        if entry.product == product
        and entry.organization_id == organization_id
        and (entry.workspace_id is None or entry.workspace_id == workspace_id)
    ]
    for entry in relevant:
        if entry.effect == p.EFFECT_DENY:
            scope = "workspace" if entry.workspace_id is not None else "organization"
            return Admission(product, False, None, f"denied:{scope}")
    for entry in relevant:
        if entry.effect == p.EFFECT_ALLOW and _stage_admits(state.stage, entry.cohort):
            return _admitted(state, f"allow:{entry.cohort}")
    if state.stage == p.S3:
        if under_percentage(product, organization_id, state.percentage):
            return _admitted(state, "percentage")
        return Admission(product, False, None, "outside_percentage")
    return Admission(product, False, None, f"not_listed:{state.stage}")


def _admitted(state: ProductState, reason: str) -> Admission:
    return Admission(
        state.product, True, p.STATE_PAUSED if state.paused else p.STATE_ACTIVE, reason
    )


def products_claim(
    states: dict[str, ProductState],
    organization_id: str,
    workspace_id: str | None,
    entries: list[AccessEntry] | tuple[AccessEntry, ...],
) -> dict[str, str]:
    """`{product: "active" | "paused"}` for every hosted product this workspace is admitted to."""
    claim: dict[str, str] = {}
    for product in p.HOSTED_PRODUCTS:
        admission = decide(
            states.get(product, ProductState(product)),
            organization_id,
            workspace_id,
            entries,
        )
        if admission.admitted:
            claim[product] = admission.state or p.STATE_ACTIVE
    return claim
