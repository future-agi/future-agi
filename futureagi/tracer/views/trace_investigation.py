from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from simulate.authentication import InternalServiceAuthentication
from tfc.permissions.rbac import IsOrganizationAdmin, IsOrganizationMember
from tfc.utils.api_contracts import validated_request
from tracer.serializers.trace_investigation import (
    ChangeActiveMemoryRequestSerializer,
    ChangeActiveMemoryResponseSerializer,
    ClaimInvestigationsRequestSerializer,
    ClaimInvestigationsResponseSerializer,
    CreateMemoryCandidateRequestSerializer,
    CreateMemoryCandidateResponseSerializer,
    InvestigationControlErrorSerializer,
    PublishInvestigationRequestSerializer,
    PublishInvestigationResponseSerializer,
    RecordMemoryEvaluationRequestSerializer,
    RecordMemoryEvaluationResponseSerializer,
    RecordTraceNotificationsRequestSerializer,
    RecordTraceNotificationsResponseSerializer,
    SubmitInvestigationFeedbackRequestSerializer,
    SubmitInvestigationFeedbackResponseSerializer,
    UpdateInvestigationAttemptRequestSerializer,
    UpdateInvestigationAttemptResponseSerializer,
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
from tracer.services.trace_investigation_memory import (
    change_active_memory,
    create_memory_candidate,
    record_memory_evaluation,
    submit_reviewed_feedback,
)
from tracer.views.feed._permissions import ErrorFeedLicenseRequired


def _error_response(error: InvestigationControlError) -> Response:
    if isinstance(error, InvestigationNotFound):
        response_status = status.HTTP_404_NOT_FOUND
    elif isinstance(error, InvestigationConflict):
        response_status = status.HTTP_409_CONFLICT
    else:
        response_status = status.HTTP_400_BAD_REQUEST
    return Response({"code": error.code, "detail": str(error)}, status=response_status)


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


class CreateMemoryCandidateView(InternalInvestigationView):
    @validated_request(
        CreateMemoryCandidateRequestSerializer,
        responses={
            200: CreateMemoryCandidateResponseSerializer,
            404: InvestigationControlErrorSerializer,
            409: InvestigationControlErrorSerializer,
        },
        reject_unknown_fields=True,
    )
    def post(self, request: Request) -> Response:
        try:
            return Response(create_memory_candidate(**request.validated_data))
        except InvestigationControlError as error:
            return _error_response(error)


class RecordMemoryEvaluationView(InternalInvestigationView):
    @validated_request(
        RecordMemoryEvaluationRequestSerializer,
        responses={
            200: RecordMemoryEvaluationResponseSerializer,
            404: InvestigationControlErrorSerializer,
            409: InvestigationControlErrorSerializer,
        },
        reject_unknown_fields=True,
    )
    def post(self, request: Request) -> Response:
        try:
            return Response(record_memory_evaluation(**request.validated_data))
        except InvestigationControlError as error:
            return _error_response(error)


class SubmitInvestigationFeedbackView(ErrorFeedLicenseRequired, APIView):
    permission_classes = [IsAuthenticated, IsOrganizationMember]

    @validated_request(
        SubmitInvestigationFeedbackRequestSerializer,
        responses={
            200: SubmitInvestigationFeedbackResponseSerializer,
            404: InvestigationControlErrorSerializer,
            409: InvestigationControlErrorSerializer,
        },
        reject_unknown_fields=True,
    )
    def post(self, request: Request) -> Response:
        try:
            return Response(
                submit_reviewed_feedback(
                    actor=request.user,
                    **request.validated_data,
                )
            )
        except InvestigationControlError as error:
            return _error_response(error)


class ChangeActiveMemoryView(ErrorFeedLicenseRequired, APIView):
    permission_classes = [IsAuthenticated, IsOrganizationAdmin]

    @validated_request(
        ChangeActiveMemoryRequestSerializer,
        responses={
            200: ChangeActiveMemoryResponseSerializer,
            404: InvestigationControlErrorSerializer,
            409: InvestigationControlErrorSerializer,
        },
        reject_unknown_fields=True,
    )
    def post(self, request: Request) -> Response:
        try:
            return Response(
                change_active_memory(
                    actor=request.user,
                    **request.validated_data,
                )
            )
        except InvestigationControlError as error:
            return _error_response(error)
