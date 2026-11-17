"""SIEM export API (G16-SIEM): an organization admin's destinations and the
deliveries waiting for, sent to or dead-lettered by them.

Owners and Admins manage; Members and Viewers can read, for the organization the
request acts on; every queryset is built per request from that organization,
so another org's destination or delivery is a 404. A webhook's signing secret
appears in the create and rotate answers only; an OTLP header value never.
"""

from django.db.models import Count
from django.http import Http404
from django.views.defaults import page_not_found
from rest_framework.permissions import SAFE_METHODS, BasePermission, IsAuthenticated
from rest_framework.views import APIView

from agentcc.models.siem import AgentccSIEMDelivery, AgentccSIEMDestination
from agentcc.permissions import IsOrgGatewayAdmin, is_gateway_admin
from agentcc.serializers.contracts import (
    AgentccEmptyRequestSerializer,
    AgentccErrorResponseSerializer,
)
from agentcc.serializers.siem import (
    SIEMConnectionTestRequestSerializer,
    SIEMConnectionTestResponseSerializer,
    SIEMDeliveryListQuerySerializer,
    SIEMDeliveryListResponseSerializer,
    SIEMDeliveryResponseSerializer,
    SIEMDestinationCreateRequestSerializer,
    SIEMDestinationDeletedResponseSerializer,
    SIEMDestinationListResponseSerializer,
    SIEMDestinationResponseSerializer,
    SIEMDestinationSecretResponseSerializer,
    SIEMDestinationUpdateRequestSerializer,
    SIEMRotateSecretRequestSerializer,
)
from agentcc.services import siem_destinations as service
from agentcc.services.siem_hec import test_connection
from agentcc.services.siem_settings import export_enabled
from tfc.constants.levels import Level
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods

Status = AgentccSIEMDelivery.Status
AUDIT_MESSAGE = (
    "The change was not made because its audit record could not be written. Try again."
)
DELIVERY_PAGE = 100
_ERRORS = {
    400: AgentccErrorResponseSerializer,
    403: AgentccErrorResponseSerializer,
    404: AgentccErrorResponseSerializer,
    409: AgentccErrorResponseSerializer,
    503: AgentccErrorResponseSerializer,
}


def _time(value):
    return value.isoformat() if value else None


def destination_json(destination, counts=None):
    counts = counts or {}
    return {
        "id": str(destination.id),
        "name": destination.name,
        "kind": destination.kind,
        "url": destination.url,
        "streams": list(destination.streams or []),
        "enabled": destination.enabled,
        "auth_header": destination.auth_header,
        "has_secret": bool(destination.encrypted_secret),
        "secret_hint": destination.secret_hint,
        "created_at": _time(destination.created_at),
        "updated_at": _time(destination.updated_at),
        "last_delivered_at": _time(destination.last_delivered_at),
        "last_failed_at": _time(destination.last_failed_at),
        "pending_count": counts.get(Status.PENDING, 0),
        "dead_letter_count": counts.get(Status.DEAD_LETTER, 0),
    }


def delivery_json(delivery):
    return {
        "id": str(delivery.id),
        "destination_id": str(delivery.destination_id),
        "destination_name": delivery.destination.name,
        "stream": delivery.stream,
        "event_count": delivery.event_count,
        "status": delivery.status,
        "attempts": delivery.attempts,
        "max_attempts": delivery.max_attempts,
        "last_response_code": delivery.last_response_code,
        "last_error": delivery.last_error,
        "last_attempt_at": _time(delivery.last_attempt_at),
        "next_retry_at": _time(delivery.next_retry_at),
        "created_at": _time(delivery.created_at),
        "delivered_at": _time(delivery.delivered_at),
    }


class SIEMPermission(BasePermission):
    message = "Only an organization Owner or Admin can manage SIEM export."

    def has_permission(self, request, view):
        organization = getattr(request, "organization", None)
        if organization is None:
            return False
        if request.method not in SAFE_METHODS:
            return is_gateway_admin(request)
        level = request.user.get_membership_level(organization)
        return level is not None and level >= Level.VIEWER


class _SIEMView(APIView):
    permission_classes = [IsAuthenticated, SIEMPermission]
    _gm = GeneralMethods()

    def dispatch(self, request, *args, **kwargs):
        # Use Django's ordinary missing-route response before DRF initializes.
        if not export_enabled():
            response = page_not_found(request, Http404())
            response["Cache-Control"] = "no-store"
            return response
        return super().dispatch(request, *args, **kwargs)

    @staticmethod
    def _organization(request):
        return getattr(request, "organization", None)

    def _refused(self, exc):
        if isinstance(exc, service.AuditUnavailable):
            return self._gm.custom_error_response(
                503, AUDIT_MESSAGE, code="audit_unavailable"
            )
        return self._gm.custom_error_response(exc.status, exc.message, code=exc.code)

    def _destination(self, request, pk):
        return AgentccSIEMDestination.objects.filter(
            organization=self._organization(request), pk=pk
        ).first()

    def _delivery(self, request, pk):
        return (
            AgentccSIEMDelivery.objects.select_related("destination")
            .filter(organization=self._organization(request), pk=pk)
            .first()
        )


def _counts(organization):
    counts = {}
    rows = (
        AgentccSIEMDelivery.objects.filter(
            organization=organization, status__in=[Status.PENDING, Status.DEAD_LETTER]
        )
        .values("destination_id", "status")
        .annotate(n=Count("id"))
    )
    for row in rows:
        counts.setdefault(row["destination_id"], {})[row["status"]] = row["n"]
    return counts


class SIEMDestinationListView(_SIEMView):
    @validated_request(
        responses={200: SIEMDestinationListResponseSerializer, **_ERRORS},
        request_methods=["GET"],
    )
    def get(self, request):
        organization = self._organization(request)
        counts = _counts(organization)
        destinations = AgentccSIEMDestination.objects.filter(organization=organization)
        return self._gm.success_response(
            {
                "export_available": export_enabled(),
                "can_manage": is_gateway_admin(request),
                "destinations": [
                    destination_json(d, counts.get(d.id)) for d in destinations
                ],
            }
        )

    @validated_request(
        request_serializer=SIEMDestinationCreateRequestSerializer,
        responses={201: SIEMDestinationSecretResponseSerializer, **_ERRORS},
        request_methods=["POST"],
        reject_unknown_fields=True,
    )
    def post(self, request):
        try:
            destination, secret = service.create(
                self._organization(request), request.user, dict(request.validated_data)
            )
        except (service.DestinationError, service.AuditUnavailable) as exc:
            return self._refused(exc)
        result = destination_json(destination)
        if secret:
            result["signing_secret"] = secret
        return self._gm.success_response(result, status=201)


class SIEMDestinationDetailView(_SIEMView):
    @validated_request(
        responses={200: SIEMDestinationResponseSerializer, **_ERRORS},
        request_methods=["GET"],
    )
    def get(self, request, pk):
        destination = self._destination(request, pk)
        if destination is None:
            return self._gm.not_found("SIEM destination not found")
        counts = _counts(destination.organization).get(destination.id)
        return self._gm.success_response(destination_json(destination, counts))

    @validated_request(
        request_serializer=SIEMDestinationUpdateRequestSerializer,
        responses={200: SIEMDestinationResponseSerializer, **_ERRORS},
        request_methods=["PATCH"],
        reject_unknown_fields=True,
    )
    def patch(self, request, pk):
        destination = self._destination(request, pk)
        if destination is None:
            return self._gm.not_found("SIEM destination not found")
        try:
            service.update(destination, request.user, dict(request.validated_data))
        except (service.DestinationError, service.AuditUnavailable) as exc:
            return self._refused(exc)
        destination.refresh_from_db()
        counts = _counts(destination.organization).get(destination.id)
        return self._gm.success_response(destination_json(destination, counts))

    @validated_request(
        request_serializer=AgentccEmptyRequestSerializer,
        responses={200: SIEMDestinationDeletedResponseSerializer, **_ERRORS},
        request_methods=["DELETE"],
        reject_unknown_fields=True,
    )
    def delete(self, request, pk):
        destination = self._destination(request, pk)
        if destination is None:
            return self._gm.not_found("SIEM destination not found")
        try:
            service.delete(destination, request.user)
        except service.AuditUnavailable as exc:
            return self._refused(exc)
        return self._gm.success_response({"deleted": True})


class SIEMDestinationRotateView(_SIEMView):
    @validated_request(
        request_serializer=SIEMRotateSecretRequestSerializer,
        responses={200: SIEMDestinationSecretResponseSerializer, **_ERRORS},
        request_methods=["POST"],
        reject_unknown_fields=True,
    )
    def post(self, request, pk):
        destination = self._destination(request, pk)
        if destination is None:
            return self._gm.not_found("SIEM destination not found")
        try:
            secret = service.rotate_secret(
                destination, request.user, dict(request.validated_data)
            )
        except (service.DestinationError, service.AuditUnavailable) as exc:
            return self._refused(exc)
        result = destination_json(destination)
        if secret:
            result["signing_secret"] = secret
        return self._gm.success_response(result)


class SIEMDeliveryListView(_SIEMView):
    @validated_request(
        query_serializer=SIEMDeliveryListQuerySerializer,
        responses={200: SIEMDeliveryListResponseSerializer, **_ERRORS},
        request_methods=["GET"],
        reject_unknown_fields=True,
    )
    def get(self, request):
        query = request.validated_query_data
        queryset = AgentccSIEMDelivery.objects.select_related("destination").filter(
            organization=self._organization(request)
        )
        if query.get("status"):
            queryset = queryset.filter(status=query["status"])
        destination_id = query.get("destination_id")
        if destination_id:
            if self._destination(request, destination_id) is None:
                return self._gm.not_found("SIEM destination not found")
            queryset = queryset.filter(destination_id=destination_id)
        rows = queryset.order_by("-created_at")[:DELIVERY_PAGE]
        return self._gm.success_response(
            {"deliveries": [delivery_json(d) for d in rows]}
        )


class _SIEMDeliveryActionView(_SIEMView):
    operation = None

    def _act(self, request, pk):
        delivery = self._delivery(request, pk)
        if delivery is None:
            return self._gm.not_found("SIEM delivery not found")
        try:
            getattr(service, self.operation)(delivery, request.user)
        except (service.DestinationError, service.AuditUnavailable) as exc:
            return self._refused(exc)
        return self._gm.success_response(delivery_json(delivery))


class SIEMDeliveryRetryView(_SIEMDeliveryActionView):
    operation = "retry"

    @validated_request(
        request_serializer=AgentccEmptyRequestSerializer,
        responses={200: SIEMDeliveryResponseSerializer, **_ERRORS},
        request_methods=["POST"],
        reject_unknown_fields=True,
    )
    def post(self, request, pk):
        return self._act(request, pk)


class SIEMDeliveryDiscardView(_SIEMDeliveryActionView):
    operation = "discard"

    @validated_request(
        request_serializer=AgentccEmptyRequestSerializer,
        responses={200: SIEMDeliveryResponseSerializer, **_ERRORS},
        request_methods=["POST"],
        reject_unknown_fields=True,
    )
    def post(self, request, pk):
        return self._act(request, pk)


class SIEMConnectionTestView(_SIEMView):
    permission_classes = [IsAuthenticated, IsOrgGatewayAdmin]

    @validated_request(
        request_serializer=SIEMConnectionTestRequestSerializer,
        responses={200: SIEMConnectionTestResponseSerializer, **_ERRORS},
        request_methods=["POST"],
        reject_unknown_fields=True,
    )
    def post(self, request):
        try:
            result = test_connection(dict(request.validated_data))
        except service.DestinationError as exc:
            return self._refused(exc)
        return self._gm.success_response(result)
