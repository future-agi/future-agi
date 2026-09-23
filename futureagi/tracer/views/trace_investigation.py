from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from simulate.authentication import InternalServiceAuthentication
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope
from tracer.serializers.trace_investigation import (
    ClaimInvestigationsRequestSerializer,
    ClaimInvestigationsResponseSerializer,
    InvestigationControlErrorSerializer,
    PublishInvestigationRequestSerializer,
    PublishInvestigationResponseSerializer,
    RecordTraceNotificationsRequestSerializer,
    RecordTraceNotificationsResponseSerializer,
    SimulationEvidenceRequestSerializer,
    UpdateInvestigationAttemptRequestSerializer,
    UpdateInvestigationAttemptResponseSerializer,
)
from tracer.services.simulation_investigation import (
    SimulationInvestigationConflict,
    simulation_evidence_page,
)
from tracer.services.trace_investigation import (
    InvestigationConflict,
    InvestigationControlError,
    InvestigationNotFound,
    claim_due_investigations,
    publish_investigation,
    record_trace_notifications,
    update_investigation_attempt,
)


def _error_response(error: InvestigationControlError) -> Response:
    if isinstance(error, InvestigationNotFound):
        response_status = status.HTTP_404_NOT_FOUND
    elif isinstance(error, InvestigationConflict):
        response_status = status.HTTP_409_CONFLICT
    else:
        response_status = status.HTTP_400_BAD_REQUEST
    return Response(
        build_error_envelope(str(error), status_code=response_status, code=error.code),
        status=response_status,
    )


class InternalInvestigationView(APIView):
    authentication_classes = [InternalServiceAuthentication]
    permission_classes = [IsAuthenticated]


class RecordTraceNotificationsView(InternalInvestigationView):
    @validated_request(
        RecordTraceNotificationsRequestSerializer,
        responses={
            200: RecordTraceNotificationsResponseSerializer,
            404: InvestigationControlErrorSerializer,
            409: InvestigationControlErrorSerializer,
        },
        reject_unknown_fields=True,
    )
    def post(self, request: Request) -> Response:
        try:
            return Response(
                record_trace_notifications(
                    deliveries=request.validated_data["deliveries"]
                )
            )
        except InvestigationControlError as error:
            return _error_response(error)


class ClaimInvestigationsView(InternalInvestigationView):
    @validated_request(
        ClaimInvestigationsRequestSerializer,
        responses={200: ClaimInvestigationsResponseSerializer},
        reject_unknown_fields=True,
    )
    def post(self, request: Request) -> Response:
        return Response(claim_due_investigations(**request.validated_data))


class UpdateInvestigationAttemptView(InternalInvestigationView):
    @validated_request(
        UpdateInvestigationAttemptRequestSerializer,
        responses={
            200: UpdateInvestigationAttemptResponseSerializer,
            404: InvestigationControlErrorSerializer,
            409: InvestigationControlErrorSerializer,
        },
        reject_unknown_fields=True,
    )
    def patch(self, request: Request, attempt_id) -> Response:
        try:
            return Response(
                update_investigation_attempt(
                    attempt_id=attempt_id, **request.validated_data
                )
            )
        except InvestigationControlError as error:
            return _error_response(error)


class PublishInvestigationView(InternalInvestigationView):
    @validated_request(
        PublishInvestigationRequestSerializer,
        responses={
            200: PublishInvestigationResponseSerializer,
            404: InvestigationControlErrorSerializer,
            409: InvestigationControlErrorSerializer,
        },
        reject_unknown_fields=True,
    )
    def post(self, request: Request) -> Response:
        try:
            return Response(publish_investigation(**request.validated_data))
        except InvestigationControlError as error:
            return _error_response(error)


class SimulationEvidenceView(InternalInvestigationView):
    def post(self, request: Request, attempt_id):
        serializer = SimulationEvidenceRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"code": "invalid_request", "detail": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            payload = simulation_evidence_page(
                attempt_id=attempt_id,
                lease_token=serializer.validated_data["lease_token"],
                cursor=serializer.validated_data["cursor"],
            )
        except SimulationInvestigationConflict as error:
            return Response(
                {"code": "conflict", "detail": str(error)},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(payload, status=status.HTTP_200_OK)
