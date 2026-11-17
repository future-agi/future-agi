"""The one rollout answer every surface reads: the capability service, the session exchange's
`products` claim, the Hub and tab view checks, and Settings -> Early access.

Plan inclusion (`PlanEntitlement`, READINESS 3.13) is combined by AND: on cloud, an explicit
`has_omega_<product>` = false for the org refuses even an admitted org. No plan row means the plan
does not object, because these products are admitted by rollout, not sold by plan, today.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from omega_rollout import products as p
from omega_rollout import readiness
from omega_rollout.decision import Admission, ProductState, decide, products_claim
from omega_rollout.store import (
    RolloutUnavailable,
    current_epoch,
    load_entries,
    load_states,
)

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ClaimSet:
    products: dict[str, str]
    epoch: int


def _norm(value) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _plan_excludes(organization_id: str | None, product: str) -> bool:
    """True only when a cloud plan row explicitly turns the product off for this org."""
    if not organization_id:
        return False
    from tfc.capabilities import service as capability_service
    from tfc.licensing.types import DeploymentLocation

    if capability_service.get_deployment_location() != DeploymentLocation.CLOUD:
        return False
    try:
        from ee.usage.services.entitlements import Entitlements
    except ImportError:
        return False
    try:
        value = Entitlements.get_entitlement(
            organization_id, f"has_{p.CAPABILITY_IDS[product]}"
        )
    except Exception as exc:  # the entitlement store is down: refuse, never admit
        raise RolloutUnavailable("plan entitlement unreadable") from exc
    return value is False


def _hosted_withheld(product: str) -> bool:
    """True when hosted Future AGI withholds this product from every org (parity Q1, switch P22).

    Sandboxes and Deployments run on self-hosted installs today: hosted Future AGI has no sandbox
    plane or deployment host for them. On hosted (DeploymentLocation.CLOUD) each stays withheld until
    the release declares its part (`hosted_sandboxes`, `hosted_deployments`, readiness.py), whatever
    the rollout stage or allow list says, so the capability, the key's `products` claim, the
    gateway's wall and Settings -> Early access all leave it out. A self-hosted install is never
    affected.

    Keys already minted: the release list is read at every decision, so a key minted after a part
    is removed names no withheld product. A key minted while the part was declared keeps it in its
    `products` claim until it expires (accounts/omega_session.py LIFETIME_SECONDS, 45 s), the same
    bound RC-07 accepts for an unreadable store. The rollout epoch is NOT bumped: it counts changes
    to the rollout store only, and the gateway reads no epoch today, so folding the release list
    into it would change nothing a reader sees and split the counter admin_api reports.
    """
    part = readiness.HOSTED_PARTS.get(product)
    if part is None:
        return False
    from tfc.capabilities import service as capability_service
    from tfc.licensing.types import DeploymentLocation

    if capability_service.get_deployment_location() != DeploymentLocation.CLOUD:
        return False
    return not readiness.part_ready(part)


def _decide(state, organization_id, workspace_id, entries) -> Admission:
    if _hosted_withheld(state.product):
        return Admission(state.product, False, None, "hosted_withheld")
    decision = decide(state, organization_id, workspace_id, entries)
    if decision.admitted and _plan_excludes(organization_id, state.product):
        return Admission(state.product, False, None, "plan_excluded")
    return decision


def first_admission(product: str, scopes) -> Admission:
    """The first admitted scope of `scopes` ((org id, workspace id) pairs, in order), else the last
    refusal; no scope at all is refused. One read of the states, one read of entries per org.
    Raises RolloutUnavailable when the store cannot be read."""
    if not p.is_product(product):
        raise ValueError(f"unknown Omega product {product!r}")
    state = load_states()[product]
    entries: dict[str | None, list] = {}
    decision = Admission(product, False, None, "no_scope")
    for organization_id, workspace_id in scopes:
        org = _norm(organization_id)
        if org not in entries:
            entries[org] = load_entries(org)
        decision = _decide(state, org, _norm(workspace_id), entries[org])
        if decision.admitted:
            return decision
    return decision


def admission(product: str, organization_id=None, workspace_id=None) -> Admission:
    """Admit or refuse one product. Raises RolloutUnavailable when the store cannot be read."""
    return first_admission(product, [(organization_id, workspace_id)])


def admissions(organization_id, workspace_id) -> dict[str, Admission]:
    """Every hosted product's admission for this workspace, from one read of the store."""
    org = _norm(organization_id)
    workspace = _norm(workspace_id)
    states = load_states()
    entries = load_entries(org)
    return {
        product: _decide(states[product], org, workspace, entries)
        for product in p.HOSTED_PRODUCTS
    }


def claim_set(organization_id, workspace_id) -> ClaimSet:
    """The `products` claim and the epoch for a key minted for this workspace.

    The epoch is read FIRST. Every change bumps the epoch in the same transaction as its rows, so the
    rows read after it are at least as new as the epoch the key carries: a key whose epoch is still
    current names the current products, and a change that commits between the two reads leaves the
    key with an older epoch, which a re-check reads as "changed since minted" (READINESS RC-04).
    """
    epoch = current_epoch()
    org = _norm(organization_id)
    workspace = _norm(workspace_id)
    if org is None:
        return ClaimSet({}, epoch)
    states = load_states()
    claim = products_claim(states, org, workspace, load_entries(org))
    claim = {
        product: state
        for product, state in claim.items()
        if not _plan_excludes(org, product) and not _hosted_withheld(product)
    }
    return ClaimSet(claim, epoch)


def product_state(product: str) -> ProductState:
    return load_states()[product]


def org_overview(organization_id, workspace_id) -> dict[str, dict]:
    """What Settings -> Early access shows an org: each product's status for this workspace."""
    org = _norm(organization_id)
    workspace = _norm(workspace_id)
    states = load_states()
    entries = load_entries(org)
    overview = {}
    for product in p.HOSTED_PRODUCTS:
        decision = _decide(states[product], org, workspace, entries)
        overview[product] = {
            "admitted": decision.admitted,
            "state": decision.state,
            # A product hosted Future AGI withholds takes no requests: nothing could admit one.
            "accepting_requests": states[product].stage in (p.S1, p.S2, p.S3)
            and not _hosted_withheld(product),
        }
    return overview
