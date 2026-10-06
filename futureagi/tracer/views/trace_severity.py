"""Internal severity control plane; shares grouping authentication/accounting."""

from rest_framework.request import Request
from rest_framework.response import Response

from tfc.utils.api_contracts import validated_request
from tracer.serializers.trace_grouping import (
    ClaimGroupingRequestSerializer,
    GroupingClaimsResponseSerializer,
    PublishSeveritySerializer,
    RenewGroupingFeatureSerializer,
    ReserveGroupingCallSerializer,
    SettleGroupingCallSerializer,
)
from tracer.services.grouping import accounting, severity
from tracer.views.trace_grouping import RESPONSES, InternalGroupingView, _respond


class ClaimSeverityView(InternalGroupingView):
    @validated_request(
        ClaimGroupingRequestSerializer,
        responses={**RESPONSES, 200: GroupingClaimsResponseSerializer},
        reject_unknown_fields=True,
    )
    def post(self, request: Request) -> Response:
        return _respond(severity.claim_severity, **request.validated_data)


class RenewSeverityView(InternalGroupingView):
    @validated_request(
        RenewGroupingFeatureSerializer, responses=RESPONSES, reject_unknown_fields=True
    )
    def patch(self, request: Request, job_id) -> Response:
        return _respond(
            severity.renew_severity, job_id=job_id, **request.validated_data
        )


class ReserveSeverityView(InternalGroupingView):
    @validated_request(
        ReserveGroupingCallSerializer, responses=RESPONSES, reject_unknown_fields=True
    )
    def post(self, request: Request, job_id) -> Response:
        return _respond(
            severity.account_call,
            job_id=job_id,
            accounting_operation=accounting.reserve_call,
            data=request.validated_data,
        )


class SettleSeverityView(InternalGroupingView):
    @validated_request(
        SettleGroupingCallSerializer, responses=RESPONSES, reject_unknown_fields=True
    )
    def post(self, request: Request, job_id) -> Response:
        return _respond(
            severity.account_call,
            job_id=job_id,
            accounting_operation=accounting.settle_call,
            data=request.validated_data,
        )


class PublishSeverityView(InternalGroupingView):
    @validated_request(
        PublishSeveritySerializer, responses=RESPONSES, reject_unknown_fields=True
    )
    def post(self, request: Request, job_id) -> Response:
        return _respond(
            severity.publish_severity, job_id=job_id, **request.validated_data
        )
