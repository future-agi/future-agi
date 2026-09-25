"""Authenticated control plane: the Node worker never receives DB credentials."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from simulate.authentication import InternalServiceAuthentication
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope
from tracer.serializers.trace_grouping import (
    ClaimGroupingRequestSerializer,
    CompleteGroupingFeatureSerializer,
    GroupingCheckpointSerializer,
    GroupingClaimsResponseSerializer,
    GroupingControlResponseSerializer,
    GroupingErrorSerializer,
    GroupingOutboxAckSerializer,
    GroupingOutboxRequestSerializer,
    GroupingOutboxResponseSerializer,
    PublishGroupingSerializer,
    RenewGroupingFeatureSerializer,
    ReserveGroupingCallSerializer,
    SettleGroupingCallSerializer,
    UpdateGroupingAttemptSerializer,
)
from tracer.services.grouping import (
    accounting,
    control,
    feature_completion,
    outbox,
    publish,
)

RESPONSES = {
    200: GroupingControlResponseSerializer,
    400: GroupingErrorSerializer,
    404: GroupingErrorSerializer,
    409: GroupingErrorSerializer,
}


def _respond(operation, **kwargs) -> Response:
    try:
        return Response(operation(**kwargs))
    except control.GroupingControlError as error:
        return Response(
            build_error_envelope(
                str(error), status_code=error.http_status, code=error.code
            ),
            status=error.http_status,
        )


class InternalGroupingView(APIView):
    authentication_classes = [InternalServiceAuthentication]
    permission_classes = [IsAuthenticated]


class ClaimGroupingFeaturesView(InternalGroupingView):
    @validated_request(
        ClaimGroupingRequestSerializer,
        responses={**RESPONSES, 200: GroupingClaimsResponseSerializer},
        reject_unknown_fields=True,
    )
    def post(self, request: Request) -> Response:
        return _respond(control.claim_feature_jobs, **request.validated_data)


class RenewGroupingFeatureView(InternalGroupingView):
    @validated_request(
        RenewGroupingFeatureSerializer, responses=RESPONSES, reject_unknown_fields=True
    )
    def patch(self, request: Request, feature_job_id) -> Response:
        return _respond(
            control.renew_feature_job,
            feature_job_id=feature_job_id,
            lease_token=request.validated_data["lease_token"],
        )


class CompleteGroupingFeatureView(InternalGroupingView):
    @validated_request(
        CompleteGroupingFeatureSerializer,
        responses=RESPONSES,
        reject_unknown_fields=True,
    )
    def post(self, request: Request, feature_job_id) -> Response:
        return _respond(
            feature_completion.complete_feature_job,
            feature_job_id=feature_job_id,
            **request.validated_data,
        )


class ClaimGroupingView(InternalGroupingView):
    @validated_request(
        ClaimGroupingRequestSerializer,
        responses={**RESPONSES, 200: GroupingClaimsResponseSerializer},
        reject_unknown_fields=True,
    )
    def post(self, request: Request) -> Response:
        return _respond(control.claim_grouping_work, **request.validated_data)


class UpdateGroupingAttemptView(InternalGroupingView):
    @validated_request(
        UpdateGroupingAttemptSerializer, responses=RESPONSES, reject_unknown_fields=True
    )
    def patch(self, request: Request, attempt_id) -> Response:
        return _respond(
            control.update_grouping_attempt,
            attempt_id=attempt_id,
            **request.validated_data,
        )


class GroupingCheckpointView(InternalGroupingView):
    @validated_request(
        GroupingCheckpointSerializer, responses=RESPONSES, reject_unknown_fields=True
    )
    def put(self, request: Request, attempt_id) -> Response:
        return _respond(
            control.checkpoint_attempt, attempt_id=attempt_id, **request.validated_data
        )


class ReserveGroupingCallView(InternalGroupingView):
    @validated_request(
        ReserveGroupingCallSerializer, responses=RESPONSES, reject_unknown_fields=True
    )
    def post(self, request: Request, attempt_id) -> Response:
        return _respond(
            accounting.reserve_call, attempt_id=attempt_id, **request.validated_data
        )


class SettleGroupingCallView(InternalGroupingView):
    @validated_request(
        SettleGroupingCallSerializer, responses=RESPONSES, reject_unknown_fields=True
    )
    def post(self, request: Request, attempt_id) -> Response:
        return _respond(
            accounting.settle_call, attempt_id=attempt_id, **request.validated_data
        )


class PublishGroupingView(InternalGroupingView):
    @validated_request(
        PublishGroupingSerializer, responses=RESPONSES, reject_unknown_fields=True
    )
    def post(self, request: Request, attempt_id) -> Response:
        return _respond(
            publish.publish_grouping, attempt_id=attempt_id, **request.validated_data
        )


class GroupingOutboxView(InternalGroupingView):
    @validated_request(
        GroupingOutboxRequestSerializer,
        responses={**RESPONSES, 200: GroupingOutboxResponseSerializer},
        reject_unknown_fields=True,
    )
    def post(self, request: Request) -> Response:
        return _respond(outbox.list_grouping_outbox, **request.validated_data)


class AcknowledgeGroupingOutboxView(InternalGroupingView):
    @validated_request(
        GroupingOutboxAckSerializer, responses=RESPONSES, reject_unknown_fields=True
    )
    def post(self, request: Request, event_id) -> Response:
        return _respond(outbox.acknowledge_grouping_outbox, event_id=event_id)
