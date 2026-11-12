"""The view-layer rollout check (READINESS 3.13, RC-02): each tab's Future AGI endpoints and every Hub
view call `require_product` before they do anything.

It answers like `tfc.ee_gating` is shaped (one call, an exception the view's handler renders), but
with different statuses: 404 `product_not_admitted` for an org not admitted, so a dark product does not
reveal itself; 503 `product_paused` for an admitted org whose product is paused, so the product does
not seem to vanish for its users; 503 `verification_unavailable` when the store cannot be read.

The check sits in the views, not in middleware, because `EEFeatureMiddleware` defers to the views on
cloud and an org is only known after DRF authentication.

Every refusal writes one `omega_rollout_refused` log line with its code and reason (READINESS RC-03:
each bypass attempt "answers 404 and logs its reason"), whichever wall refused it: a view, the Hub,
the key batch or a platform route.
"""

from __future__ import annotations

import structlog
from rest_framework import status
from rest_framework.exceptions import APIException

from omega_rollout import products as p
from omega_rollout import service
from omega_rollout.decision import Admission
from omega_rollout.store import RolloutUnavailable

logger = structlog.get_logger(__name__)

# Work a paused product still serves: reads. Everything else is new work and is refused.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class ProductUnavailable(APIException):
    """A refusal with a stable `code` (READINESS L07) and the product it concerns."""

    def __init__(self, product: str, reason: str, http_status: int, message: str):
        self.status_code = http_status
        self.product = product
        self.reason = reason
        super().__init__(detail=message, code=reason)


def not_admitted(product: str) -> ProductUnavailable:
    return ProductUnavailable(
        product, p.REASON_NOT_ADMITTED, status.HTTP_404_NOT_FOUND, "Not found."
    )


def paused(product: str) -> ProductUnavailable:
    return ProductUnavailable(
        product,
        p.REASON_PAUSED,
        status.HTTP_503_SERVICE_UNAVAILABLE,
        f"{p.PRODUCT_LABELS[product]} is paused for maintenance. Try again later.",
    )


def unavailable(product: str) -> ProductUnavailable:
    return ProductUnavailable(
        product,
        p.REASON_UNAVAILABLE,
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "Access could not be verified just now. Try again in a few seconds.",
    )


def log_refusal(
    product: str,
    code: str,
    *,
    reason: str,
    request=None,
    organization_id=None,
    workspace_id=None,
) -> None:
    """One structured line per refusal. Never a credential: ids, the method and the path only."""
    log = logger.warning if code == p.REASON_UNAVAILABLE else logger.info
    log(
        "omega_rollout_refused",
        product=product,
        code=code,
        reason=reason,
        method=getattr(request, "method", None),
        path=getattr(request, "path", None),
        organization_id=str(organization_id) if organization_id else None,
        workspace_id=str(workspace_id) if workspace_id else None,
    )


def request_scope(request) -> tuple:
    """(organization id, workspace id) DRF resolved for this request."""
    organization = getattr(request, "organization", None)
    workspace = getattr(request, "workspace", None)
    return getattr(organization, "pk", None), getattr(workspace, "pk", None)


def require_product(
    product: str,
    request,
    *,
    new_work: bool | None = None,
    scopes=None,
) -> Admission:
    """Refuse unless the request's org is admitted to `product`; refuse new work while it is paused.

    `new_work` defaults to "the method is not a read". `scopes`, a list of (org id, workspace id),
    decides for scopes other than the request's: admitted when any of them is (a Hub token's holder).
    """
    if scopes is None:
        scopes = [request_scope(request)]
    organization_id, workspace_id = scopes[0] if len(scopes) == 1 else (None, None)
    where = {
        "request": request,
        "organization_id": organization_id,
        "workspace_id": workspace_id,
    }
    try:
        decision = service.first_admission(product, scopes)
    except RolloutUnavailable as exc:
        log_refusal(product, p.REASON_UNAVAILABLE, reason="store", **where)
        raise unavailable(product) from exc
    if not decision.admitted:
        log_refusal(product, p.REASON_NOT_ADMITTED, reason=decision.reason, **where)
        raise not_admitted(product)
    if new_work is None:
        new_work = str(getattr(request, "method", "GET")).upper() not in SAFE_METHODS
    if decision.paused and new_work:
        log_refusal(product, p.REASON_PAUSED, reason="new_work", **where)
        raise paused(product)
    return decision


def require_platform(organization_id, workspace_id, *, route: str = "platform"):
    """For a route every tab needs (product `platform`): any workspace admitted to at least one hosted
    product passes, active or paused; one admitted to none gets 404 `product_not_admitted`. Returns
    the workspace's claim set (its products and the rollout epoch). `route` names the caller in the
    refusal log line.
    """
    where = {"organization_id": organization_id, "workspace_id": workspace_id}
    try:
        claims = service.claim_set(organization_id, workspace_id)
    except RolloutUnavailable as exc:
        log_refusal(p.PLATFORM, p.REASON_UNAVAILABLE, reason=route, **where)
        raise unavailable(p.PLATFORM_LABEL_PRODUCT) from exc
    if not claims.products:
        log_refusal(p.PLATFORM, p.REASON_NOT_ADMITTED, reason=route, **where)
        raise not_admitted(p.PLATFORM_LABEL_PRODUCT)
    return claims
