"""Public adapters preserve service actor checks and immutable packet identities."""

from rest_framework.response import Response

from model_hub.serializers import skills_release_requests as s
from model_hub.serializers.skills_context_authoring import Revision
from model_hub.services.skills_hub.r3 import release_requests as service
from model_hub.services.skills_hub.r3 import review_inbox
from model_hub.views.skills_context_foundation import IDEMPOTENCY, contract, header_uuid
from model_hub.views.skills_hub import SkillsView


class ReviewView(SkillsView):
    private_resource = True


class PacketPreviewView(ReviewView):
    @contract(s.Packet, body=s.Preview)
    def post(self, request):
        return self.output(
            request,
            s.Packet,
            service.preview_release_packet(
                principal=request.skill_principal, **request.validated_data
            ),
        )


class RequestListView(ReviewView):
    @contract(
        s.RequestReceipt, body=s.Submit, status=201, manual_parameters=[IDEMPOTENCY]
    )
    def post(self, request):
        return self.output(
            request,
            s.RequestReceipt,
            service.submit_release_request(
                principal=request.skill_principal,
                idempotency_key=header_uuid(request, "Idempotency-Key"),
                **request.validated_data,
            ),
            status=201,
        )


class RequestDetailView(ReviewView):
    @contract(s.RequestDetail)
    def get(self, request, request_id):
        row, packet = service.get_release_request(
            principal=request.skill_principal, request_id=request_id
        )
        return self.output(
            request,
            s.RequestDetail,
            {
                "id": row.pk,
                "revision": row.revision,
                "status": row.status,
                "author_id": row.author_id,
                "subject_id": row.skill_id,
                "packet": packet.packet,
                "fingerprint": packet.fingerprint,
            },
        )


class RequestDecisionView(ReviewView):
    @contract(s.DecisionReceipt, body=s.Decision, manual_parameters=[IDEMPOTENCY])
    def post(self, request, request_id):
        return self.output(
            request,
            s.DecisionReceipt,
            service.decide_release_request(
                principal=request.skill_principal,
                request_id=request_id,
                idempotency_key=header_uuid(request, "Idempotency-Key"),
                **request.validated_data,
            ),
        )


class RequestExecuteView(ReviewView):
    @contract(s.ExecutionReceipt, body=s.Execute, manual_parameters=[IDEMPOTENCY])
    def post(self, request, request_id):
        return self.output(
            request,
            s.ExecutionReceipt,
            service.execute_release_request(
                principal=request.skill_principal,
                request_id=request_id,
                idempotency_key=header_uuid(request, "Idempotency-Key"),
                **request.validated_data,
            ),
        )


class RequestReceiptView(ReviewView):
    @contract(s.ExecutionReceipt)
    def get(self, request, request_id):
        return self.output(
            request,
            s.ExecutionReceipt,
            service.get_release_receipt(
                principal=request.skill_principal, request_id=request_id
            ),
        )


class ReviewInboxView(ReviewView):
    @contract(s.Inbox, query=s.InboxQuery)
    def get(self, request):
        return self.output(
            request,
            s.Inbox,
            review_inbox.list_review_inbox(
                principal=request.skill_principal, **request.validated_query_data
            ),
        )


class ReviewSeenView(ReviewView):
    @contract(body=Revision, status=204)
    def post(self, request, request_id):
        review_inbox.mark_review_seen(
            principal=request.skill_principal,
            request_id=request_id,
            **request.validated_data,
        )
        return Response(status=204)
