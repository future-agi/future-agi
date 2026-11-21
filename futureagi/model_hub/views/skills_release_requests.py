"""Public adapters preserve service actor checks and immutable packet identities."""

from rest_framework.response import Response

from model_hub.serializers import skills_release_requests as s
from model_hub.serializers.skills_context_authoring import Revision
from model_hub.services.skills_hub.r3 import release_options, review_inbox
from model_hub.services.skills_hub.r3 import release_requests as service
from model_hub.views.skills_context_foundation import IDEMPOTENCY, contract, header_uuid
from model_hub.views.skills_hub import SkillsView


class ReviewView(SkillsView):
    private_resource = True

    def finalize_response(self, request, response, *args, **kwargs):
        # J4: review state is private and changes under races. A browser must
        # never reuse a heuristically cached 410/409 (or 200) after a renewal.
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        return response


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


class ReleaseInspectionView(ReviewView):
    @contract(s.ReleaseInspection, body=s.InspectionInput)
    def post(self, request):
        from model_hub.services.skills_hub.r3.release_inspection import inspect_release

        return self.output(
            request,
            s.ReleaseInspection,
            inspect_release(
                principal=request.skill_principal, **request.validated_data
            ),
        )


class NativeEvidenceShareView(ReviewView):
    @contract(s.r3.EvidenceRef, body=s.ShareEvidence)
    def post(self, request, evidence_id):
        from model_hub.services.skills_hub.r3.human_review import evidence_reference
        from model_hub.services.skills_hub.r3.native_evidence import (
            share_native_evidence,
        )

        principal = request.skill_principal
        ref = evidence_reference(
            principal=principal, source="simulation_native", source_id=evidence_id
        )
        share_native_evidence(
            principal=principal,
            resource_id=ref["subject_id"],
            kind=ref["subject_kind"],
            evidence_id=evidence_id,
            **request.validated_data,
        )
        return self.output(
            request,
            s.r3.EvidenceRef,
            evidence_reference(
                principal=principal, source="simulation_native", source_id=evidence_id
            ),
        )


class ReleaseOptionsView(ReviewView):
    @contract(s.ReviewOptions, query=s.OptionsQuery)
    def get(self, request):
        return self.output(
            request,
            s.ReviewOptions,
            release_options.release_options(
                principal=request.skill_principal, **request.validated_query_data
            ),
        )


class RequestListView(ReviewView):
    @contract(s.AuthoredPage, query=s.InboxQuery)
    def get(self, request):
        return self.output(
            request,
            s.AuthoredPage,
            release_options.authored_requests(
                principal=request.skill_principal, **request.validated_query_data
            ),
        )

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
        decision = (
            packet.hub_skill_decisions.select_related("actor")
            .order_by("-created_at", "-pk")
            .first()
        )
        return self.output(
            request,
            s.RequestDetail,
            {
                "id": row.pk,
                "revision": row.revision,
                "status": row.status,
                "author_id": row.author_id,
                "author_name": row.author.name or row.author.email,
                "decided_by_name": (decision.actor.name or decision.actor.email)
                if decision
                else None,
                "decision": decision.decision if decision else None,
                "name": row.skill.name,
                "updated_at": row.updated_at,
                "recipients": [
                    {
                        "id": str(member.recipient_id),
                        "name": member.recipient.name or member.recipient.email,
                    }
                    for member in row.hub_skill_recipients.select_related("recipient")
                ],
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


# --- J4: explicit reviewer renewal ------------------------------------------
class RequestRenewalView(ReviewView):
    @contract(s.RenewalPreview)
    def get(self, request, request_id):
        return self.output(
            request,
            s.RenewalPreview,
            service.release_renewal_preview(
                principal=request.skill_principal, request_id=request_id
            ),
        )

    @contract(s.RenewalReceipt, body=s.Renew, manual_parameters=[IDEMPOTENCY])
    def post(self, request, request_id):
        return self.output(
            request,
            s.RenewalReceipt,
            service.renew_release_request(
                principal=request.skill_principal,
                request_id=request_id,
                idempotency_key=header_uuid(request, "Idempotency-Key"),
                **request.validated_data,
            ),
        )
