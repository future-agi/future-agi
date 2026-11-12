"""The rollout check on every Omega Hub view (READINESS 3.13; the Hub lives in `omega_hub` until lane
0.3 merges it). `omega_hub/api/base.py` calls `check_hub_request` from each base view.

Whose access a Hub request uses:
- a Future AGI session, or an org API key pair (public reads only; the Hub refuses it elsewhere): the
  org and workspace DRF resolved for the request, so a workspace deny applies to both;
- a Hub token: wherever its holder could open the Hub with a session. That is each workspace they
  reach through an ACTIVE org membership (never the legacy `user.organization` key, which a removed
  member keeps), only in the publisher's own org for an org publisher. The token is admitted when
  any of them is, checked in a fixed order;
- nobody (anonymous browsing and installs): admitted only at S3 and 100% (READINESS 3.13, Hub S3).

What the kill switch stops (READINESS 3.13 per-product table): submissions, reviews, likes and every
other write that starts work; the checker's claim and heartbeat (running checks end within one
lease, and a check that finishes in it still reports); installs (bundle, file and cast downloads)
too when the operator paused installs. Browsing keeps working, and so do the writes that stop or
take down work (STOP_WORK below), like the gateway's `whilePaused` list: an incident needs them most.
"""

from __future__ import annotations

from omega_rollout import products as p
from omega_rollout.enforce import (
    log_refusal,
    not_admitted,
    paused,
    require_product,
    unavailable,
)

READ = "read"
INSTALL = "install"
WRITE = "write"
STOP = "stop"

# (view class, method, hub_action) of each write a paused Hub still serves, because it revokes,
# withdraws or takes down rather than starts anything. Every other write is new work.
STOP_WORK = frozenset(
    {
        ("TokenView", "DELETE", ""),  # revoke a Hub token
        ("SubmissionWithdrawView", "POST", ""),
        ("YankView", "POST", ""),  # a publisher yanks its own version
        ("UnlistView", "POST", ""),  # a publisher unlists its own entry
        ("MaintainerYankView", "POST", ""),
        ("EntryActionView", "POST", "remove"),
        ("EntryActionView", "POST", "unrank"),
        ("PublisherActionView", "POST", "suspend"),
        ("PublisherActionView", "POST", "unverify"),
        ("ReviewBlocklistView", "POST", ""),  # block a bad bundle digest
        ("ReviewPickView", "DELETE", ""),  # take an entry off the featured picks
    }
)
# Not body-dependent on purpose: reading the body here would answer a malformed request before the
# admission check. Resolving a report (whose action may or may not take something down) stays new
# work; maintainers use the direct takedown routes above while the Hub is paused.


def _reachable_workspaces(user, membership) -> list[str | None]:
    """The workspaces this membership's holder can open, in a fixed order; [None] if none."""
    from accounts.models.workspace import Workspace, WorkspaceMembership
    from tfc.constants.levels import Level

    organization = membership.organization
    # Owners and admins open every workspace of the org (has_global_workspace_access).
    if membership.level_or_legacy >= Level.ADMIN:
        rows = Workspace.no_workspace_objects.filter(
            organization=organization, is_active=True
        ).values_list("pk", flat=True)
    else:
        rows = WorkspaceMembership.no_workspace_objects.filter(
            user=user,
            workspace__organization=organization,
            workspace__is_active=True,
            is_active=True,
        ).values_list("workspace_id", flat=True)
    ids = sorted({str(pk) for pk in rows})
    return ids or [None]


def _token_scopes(token) -> list[tuple[str, str | None]]:
    from omega_rollout.authority import active_memberships

    memberships = active_memberships(token.user)
    if token.publisher.owner_org_id:
        memberships = memberships.filter(organization_id=token.publisher.owner_org_id)
    return [
        (str(membership.organization_id), workspace_id)
        for membership in memberships
        for workspace_id in _reachable_workspaces(token.user, membership)
    ]


def hub_scopes(request) -> list[tuple[str | None, str | None]]:
    """The (organization id, workspace id) scopes the request is decided for, in order."""
    from omega_hub.permissions import is_hub_token

    if is_hub_token(request):
        return _token_scopes(request.auth)
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        organization = getattr(request, "organization", None)
        workspace = getattr(request, "workspace", None)
        return [(getattr(organization, "pk", None), getattr(workspace, "pk", None))]
    return [(None, None)]


def work_kind(request, view) -> str:
    method = str(request.method).upper()
    if method in ("GET", "HEAD", "OPTIONS"):
        return INSTALL if getattr(view, "hub_install", False) else READ
    if (type(view).__name__, method, getattr(view, "hub_action", "")) in STOP_WORK:
        return STOP
    return WRITE


def _installs_paused(request) -> bool:
    from omega_rollout import service
    from omega_rollout.store import RolloutUnavailable

    try:
        return service.product_state(p.HUB).installs_paused
    except RolloutUnavailable as exc:
        log_refusal(
            p.HUB, p.REASON_UNAVAILABLE, reason="installs_state", request=request
        )
        raise unavailable(p.HUB) from exc


def check_hub_request(request, view) -> None:
    """Raise 404 `product_not_admitted`, 503 `product_paused` or 503 `verification_unavailable`."""
    kind = work_kind(request, view)
    admission = require_product(
        p.HUB, request, new_work=kind == WRITE, scopes=hub_scopes(request)
    )
    if kind == INSTALL and admission.paused and _installs_paused(request):
        log_refusal(p.HUB, p.REASON_PAUSED, reason="installs_paused", request=request)
        raise paused(p.HUB)


def require_hub_part(part: str, request=None) -> None:
    """Refuse a Hub write whose part does not work today (readiness.py) with the same 404 a dark
    product gets: a submission while no checker is configured would wait forever on "checks start in
    a moment", and a Hub token's only use is a CLI command no customer can install before the CLI
    ships it."""
    from omega_rollout import readiness

    if readiness.part_ready(part):
        return
    log_refusal(p.HUB, p.REASON_NOT_ADMITTED, reason=f"not_ready:{part}", request=request)
    raise not_admitted(p.HUB)


# The checker calls that start or extend work. While the Hub is paused they are refused, so no new
# check starts and a running check ends within the lease it holds; its context, bundle and result
# calls are still served, so a check that finishes inside that lease reports once and nothing runs
# twice.
CHECKER_NEW_WORK = frozenset({"ClaimView", "JobHeartbeatView"})


def check_checker_request(view, request=None) -> None:
    """The checker is not an org: it is never 'not admitted'. While the Hub is paused it drains."""
    if type(view).__name__ not in CHECKER_NEW_WORK:
        return
    from omega_rollout import service
    from omega_rollout.store import RolloutUnavailable

    try:
        state = service.product_state(p.HUB)
    except RolloutUnavailable as exc:
        log_refusal(p.HUB, p.REASON_UNAVAILABLE, reason="checker", request=request)
        raise unavailable(p.HUB) from exc
    if state.paused:
        log_refusal(p.HUB, p.REASON_PAUSED, reason="checker", request=request)
        raise paused(p.HUB)
