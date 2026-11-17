"""The staff-only rollout API (READINESS 3.13, RC-10). Staff read it; only staff holding
`omega_rollout.operate_rollout` change it, and every change names a reason and is audited.

Every route needs a browser session: an org API key pair is refused before anything else, reads
included (omega_rollout/authority.py says why). No agent changes a production flag: production
changes are made by a named operator (OD-23).
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import Count, Q
from rest_framework import status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import Organization
from accounts.models.workspace import Workspace
from omega_rollout import ops
from omega_rollout import products as p
from omega_rollout import serializers as s
from omega_rollout.authority import AUTHORITY_CODES, OPERATE, SessionOnly
from omega_rollout.models import (
    ProductAccess,
    ProductRollout,
    RolloutAuditEvent,
    RolloutEpoch,
    WaitlistRequest,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope
from tfc.utils.api_serializers import EmptyRequestSerializer


class IsRolloutStaff(BasePermission):
    """Staff only; a change also needs the operate permission."""

    message = "Only Future AGI staff operators can use the rollout API."

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated and user.is_active and user.is_staff):
            return False
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return True
        return user.has_perm(OPERATE)


class StaffView(APIView):
    permission_classes = [SessionOnly, IsAuthenticated, IsRolloutStaff]


def _refused(exc: ops.RolloutChangeRefused) -> Response:
    code = (
        status.HTTP_403_FORBIDDEN
        if exc.code in AUTHORITY_CODES
        else status.HTTP_409_CONFLICT
    )
    return Response(
        build_error_envelope(str(exc), status_code=code, code=exc.code), status=code
    )


def _not_found(what: str) -> Response:
    return Response(
        build_error_envelope(f"{what} not found.", status_code=404), status=404
    )


def _product_rows() -> list[dict]:
    rows = {row.product: row for row in ProductRollout.objects.all()}
    entries = {
        item["product"]: item
        for item in ProductAccess.objects.values("product").annotate(
            allowed=Count(
                "organization", filter=Q(effect=p.EFFECT_ALLOW), distinct=True
            ),
            denied=Count("id", filter=Q(effect=p.EFFECT_DENY)),
        )
    }
    requests = {
        item["product"]: item["n"]
        for item in WaitlistRequest.objects.filter(status=WaitlistRequest.REQUESTED)
        .values("product")
        .annotate(n=Count("id"))
    }
    out = []
    for product in p.HOSTED_PRODUCTS:
        row = rows.get(product) or ProductRollout(product=product)
        counts = entries.get(product, {})
        out.append(
            {
                "product": product,
                "label": p.PRODUCT_LABELS[product],
                "stage": row.stage,
                "percentage": row.percentage,
                "paused": row.paused,
                "installs_paused": row.installs_paused,
                "epoch": row.epoch,
                "allowed_orgs": counts.get("allowed", 0),
                "denied_entries": counts.get("denied", 0),
                "open_requests": requests.get(product, 0),
            }
        )
    return out


def _epoch() -> int:
    return (
        RolloutEpoch.objects.filter(pk=1).values_list("epoch", flat=True).first() or 0
    )


class ProductListView(StaffView):
    @validated_request(
        query_serializer=EmptyRequestSerializer,
        responses={200: s.ProductListSerializer, **s.ERROR_RESPONSES},
    )
    def get(self, request):
        return Response({"epoch": _epoch(), "products": _product_rows()})


class ProductDetailView(StaffView):
    @validated_request(
        request_serializer=s.ProductChangeSerializer,
        reject_unknown_fields=True,
        responses={200: s.ProductListSerializer, **s.ERROR_RESPONSES},
    )
    def patch(self, request, product):
        if not p.is_product(product):
            return _not_found("Product")
        data = request.validated_data
        try:
            # A stage and a percentage in one request apply together or not at all.
            with transaction.atomic():
                if "stage" in data:
                    ops.set_stage(
                        product,
                        data["stage"],
                        actor=request.user,
                        reason=data["reason"],
                    )
                if "percentage" in data:
                    ops.set_percentage(
                        product,
                        data["percentage"],
                        actor=request.user,
                        reason=data["reason"],
                    )
        except ops.RolloutChangeRefused as exc:
            return _refused(exc)
        return Response({"epoch": _epoch(), "products": _product_rows()})


class KillView(StaffView):
    @validated_request(
        request_serializer=s.KillSerializer,
        reject_unknown_fields=True,
        responses={200: s.ProductListSerializer, **s.ERROR_RESPONSES},
    )
    def post(self, request, product):
        if not p.is_product(product):
            return _not_found("Product")
        data = request.validated_data
        try:
            ops.kill(
                product,
                actor=request.user,
                reason=data["reason"],
                pause_installs=data["pause_installs"],
            )
        except ops.RolloutChangeRefused as exc:
            return _refused(exc)
        return Response({"epoch": _epoch(), "products": _product_rows()})


class RestoreView(StaffView):
    @validated_request(
        request_serializer=s.ReasonSerializer,
        reject_unknown_fields=True,
        responses={200: s.ProductListSerializer, **s.ERROR_RESPONSES},
    )
    def post(self, request, product):
        if not p.is_product(product):
            return _not_found("Product")
        try:
            ops.restore(
                product, actor=request.user, reason=request.validated_data["reason"]
            )
        except ops.RolloutChangeRefused as exc:
            return _refused(exc)
        return Response({"epoch": _epoch(), "products": _product_rows()})


def _scope(data):
    organization = Organization.objects.filter(pk=data["organization_id"]).first()
    if organization is None:
        return None, None, _not_found("Organization")
    workspace = None
    if data.get("workspace_id"):
        workspace = Workspace.no_workspace_objects.filter(
            pk=data["workspace_id"], organization=organization
        ).first()
        if workspace is None:
            return None, None, _not_found("Workspace")
    return organization, workspace, None


def _access_rows(product: str) -> list[dict]:
    rows = ProductAccess.objects.filter(product=product).select_related("organization")
    return [
        {
            "id": row.pk,
            "organization_id": row.organization_id,
            "organization_name": getattr(row.organization, "display_name", None)
            or row.organization.name,
            "workspace_id": row.workspace_id,
            "effect": row.effect,
            "cohort": row.cohort,
            "reason": row.reason,
            "created_at": row.created_at,
        }
        for row in rows.order_by("-created_at")
    ]


class AccessView(StaffView):
    @validated_request(
        query_serializer=EmptyRequestSerializer,
        responses={200: s.AccessListSerializer, **s.ERROR_RESPONSES},
    )
    def get(self, request, product):
        if not p.is_product(product):
            return _not_found("Product")
        return Response({"product": product, "entries": _access_rows(product)})

    @validated_request(
        request_serializer=s.AccessChangeSerializer,
        reject_unknown_fields=True,
        responses={200: s.AccessListSerializer, **s.ERROR_RESPONSES},
    )
    def put(self, request, product):
        if not p.is_product(product):
            return _not_found("Product")
        data = request.validated_data
        organization, workspace, missing = _scope(data)
        if missing is not None:
            return missing
        try:
            ops.set_access(
                product,
                organization,
                effect=data["effect"],
                cohort=data["cohort"],
                workspace=workspace,
                actor=request.user,
                reason=data["reason"],
            )
        except ops.RolloutChangeRefused as exc:
            return _refused(exc)
        return Response({"product": product, "entries": _access_rows(product)})


class AccessRemoveView(StaffView):
    @validated_request(
        request_serializer=s.AccessRemoveSerializer,
        reject_unknown_fields=True,
        responses={200: s.AccessListSerializer, **s.ERROR_RESPONSES},
    )
    def post(self, request, product):
        if not p.is_product(product):
            return _not_found("Product")
        data = request.validated_data
        organization, workspace, missing = _scope(data)
        if missing is not None:
            return missing
        try:
            ops.remove_access(
                product,
                organization,
                workspace=workspace,
                actor=request.user,
                reason=data["reason"],
            )
        except ops.RolloutChangeRefused as exc:
            return _refused(exc)
        return Response({"product": product, "entries": _access_rows(product)})


def _waitlist_rows(query) -> list[dict]:
    rows = WaitlistRequest.objects.select_related("organization", "requested_by")
    if query.get("product"):
        rows = rows.filter(product=query["product"])
    if query.get("status"):
        rows = rows.filter(status=query["status"])
    return [
        {
            "id": row.pk,
            "product": row.product,
            "organization_id": row.organization_id,
            "organization_name": getattr(row.organization, "display_name", None)
            or row.organization.name,
            "requested_by": getattr(row.requested_by, "email", "") or "",
            "note": row.note,
            "status": row.status,
            "created_at": row.created_at,
            "decided_at": row.decided_at,
        }
        for row in rows.order_by("-created_at")[:500]
    ]


class WaitlistView(StaffView):
    @validated_request(
        query_serializer=s.AdminQuerySerializer,
        responses={200: s.WaitlistListSerializer, **s.ERROR_RESPONSES},
    )
    def get(self, request):
        return Response({"requests": _waitlist_rows(request.validated_query_data)})


class WaitlistDecisionView(StaffView):
    @validated_request(
        request_serializer=s.WaitlistDecisionSerializer,
        reject_unknown_fields=True,
        responses={200: s.WaitlistListSerializer, **s.ERROR_RESPONSES},
    )
    def post(self, request, request_id):
        found = WaitlistRequest.objects.filter(pk=request_id).first()
        if found is None:
            return _not_found("Request")
        data = request.validated_data
        try:
            ops.decide_request(
                found, invite=data["invite"], actor=request.user, reason=data["reason"]
            )
        except ops.RolloutChangeRefused as exc:
            return _refused(exc)
        return Response({"requests": _waitlist_rows({"product": found.product})})


class AuditView(StaffView):
    @validated_request(
        query_serializer=s.AdminQuerySerializer,
        responses={200: s.AuditListSerializer, **s.ERROR_RESPONSES},
    )
    def get(self, request):
        events = RolloutAuditEvent.objects.select_related("actor")
        product = request.validated_query_data.get("product")
        if product:
            events = events.filter(product=product)
        return Response(
            {
                "events": [
                    {
                        "id": e.pk,
                        "action": e.action,
                        "product": e.product,
                        "actor": getattr(e.actor, "email", "") or "",
                        "organization_id": e.organization_id,
                        "workspace_id": e.workspace_id,
                        "before": e.before,
                        "after": e.after,
                        "reason": e.reason,
                        "counts": e.counts,
                        "epoch": e.epoch,
                        "created_at": e.created_at,
                    }
                    for e in events[:500]
                ]
            }
        )
