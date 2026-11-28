"""Scoped HTTP entry points; transactional decisions remain in domain services."""

from model_hub.selectors.skills_hub_v12 import get_resource
from model_hub.serializers import skills_workflows as s
from model_hub.serializers.skills_hub_r3 import EvidenceRef
from model_hub.serializers.skills_hub_v12 import PageQuery
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.r3 import human_review, import_plans
from model_hub.services.skills_hub.v12 import capture, trace_source
from model_hub.views.skills_context_foundation import IDEMPOTENCY, contract, header_uuid
from model_hub.views.skills_hub import SkillsView
from model_hub.views.skills_uploads import SkillMultipartParser


class PrivateWorkflowView(SkillsView):
    private_resource = True

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        return response


class IntakePreviewView(PrivateWorkflowView):
    parser_classes = [SkillMultipartParser]

    @contract(s.IntakePlan, body=s.IntakePreviewInput)
    def post(self, request):
        uploads = []
        for upload in request.validated_data["files"]:
            content = upload.read(upload.size + 1)
            if len(content) > upload.size:
                raise SkillsHubError(
                    "payload_too_large",
                    "The upload exceeds its declared size.",
                    status=413,
                )
            uploads.append(
                (getattr(upload, "skill_relative_path", upload.name), content)
            )
        plan = import_plans.preview_import(
            principal=request.skill_principal,
            uploads=uploads,
            source_identities=request.validated_data.get("source_identities"),
        )
        return self.output(request, s.IntakePlan, plan)


class IntakePlanView(PrivateWorkflowView):
    @contract(s.IntakePlan)
    def get(self, request, plan_id):
        return self.output(
            request,
            s.IntakePlan,
            import_plans.get_import_plan(
                principal=request.skill_principal, plan_id=plan_id
            ),
        )


class IntakeAcceptView(PrivateWorkflowView):
    @contract(s.IntakeResult, body=s.IntakeDecision, manual_parameters=[IDEMPOTENCY])
    def post(self, request, plan_id):
        return self.output(
            request,
            s.IntakeResult,
            import_plans.accept_import(
                principal=request.skill_principal,
                plan_id=plan_id,
                idempotency_key=header_uuid(request, "Idempotency-Key"),
                **request.validated_data,
            ),
        )


class IntakeReconcileView(PrivateWorkflowView):
    @contract(s.IntakePlan, body=s.IntakeReconcileInput)
    def post(self, request, plan_id):
        return self.output(
            request,
            s.IntakePlan,
            import_plans.preview_manual_reconciliation(
                principal=request.skill_principal,
                plan_id=plan_id,
                **request.validated_data,
            ),
        )


class IntakeReceiptsView(PrivateWorkflowView):
    @contract(s.IntakeReceiptPage, query=PageQuery)
    def get(self, request, plan_id):
        return self.output(
            request,
            s.IntakeReceiptPage,
            import_plans.list_import_receipts(
                principal=request.skill_principal,
                plan_id=plan_id,
                **request.validated_query_data,
            ),
        )


class TraceCaptureSourceView(PrivateWorkflowView):
    @contract(s.CaptureSource, body=s.CaptureSourceInput)
    def post(self, request, context_id):
        # Return only selectable request/tool-result text, never the physical row,
        # assistant output, system instructions or external lineage metadata.
        get_resource(
            principal=request.skill_principal,
            resource_id=context_id,
            kind="agent_context",
        )
        return self.output(
            request,
            s.CaptureSource,
            trace_source.read_trace_source(
                principal=request.skill_principal, **request.validated_data
            ),
        )


class TraceCapturePreviewView(PrivateWorkflowView):
    @contract(s.CapturePreview, body=s.CapturePreviewInput)
    def post(self, request, context_id):
        return self.output(
            request,
            s.CapturePreview,
            capture.preview_trace_capture(
                principal=request.skill_principal,
                context_id=context_id,
                **request.validated_data,
            ),
        )


class TraceCaptureAcceptView(PrivateWorkflowView):
    @contract(s.CaptureSuite, body=s.CaptureAcceptance, manual_parameters=[IDEMPOTENCY])
    def post(self, request, context_id, capture_id):
        return self.output(
            request,
            s.CaptureSuite,
            capture.accept_trace_case(
                principal=request.skill_principal,
                context_id=context_id,
                capture_id=capture_id,
                request=request.validated_data,
                idempotency_key=header_uuid(request, "Idempotency-Key"),
            ),
        )


class EvidenceReferenceView(PrivateWorkflowView):
    @contract(EvidenceRef)
    def get(self, request, source, source_id):
        return self.output(
            request,
            EvidenceRef,
            human_review.evidence_reference(
                principal=request.skill_principal,
                source=source,
                source_id=source_id,
            ),
        )


class EvidenceAnnotationsView(PrivateWorkflowView):
    @contract(s.AnnotationPage, query=PageQuery)
    def get(self, request, source, source_id):
        return self.output(
            request,
            s.AnnotationPage,
            human_review.list_annotations(
                principal=request.skill_principal,
                source=source,
                source_id=source_id,
                **request.validated_query_data,
            ),
        )

    @contract(s.Annotation, body=s.AnnotationInput, status=201)
    def post(self, request, source, source_id):
        return self.output(
            request,
            s.Annotation,
            human_review.append_annotation(
                principal=request.skill_principal,
                source=source,
                source_id=source_id,
                **request.validated_data,
            ),
            status=201,
        )
