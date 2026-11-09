"""Explicit human administration and isolated-purpose consumer routes."""

from rest_framework.response import Response

from model_hub.serializers import skills_hub_integration as integration
from model_hub.serializers import skills_hub_v12 as s
from model_hub.services.skills_hub.v12 import consumers, releases, safety
from model_hub.services.skills_hub.v12.types import ResourceKind
from model_hub.views.skills_context_foundation import (
    IDEMPOTENCY,
    ConsumptionView,
    consumption_contract,
    contract,
    header_uuid,
)
from model_hub.views.skills_hub import SkillsView


class ContextBindingsView(SkillsView):
    @contract(integration.BindingPage, query=s.PageQuery)
    def get(self, request, context_id):
        return self.output(
            request,
            integration.BindingPage,
            consumers.list_bindings(
                principal=request.skill_principal,
                context_id=context_id,
                **request.validated_query_data,
            ),
        )

    @contract(
        s.BindingOut, body=s.BindingInput, status=201, manual_parameters=[IDEMPOTENCY]
    )
    def post(self, request, context_id):
        return self.output(
            request,
            s.BindingOut,
            consumers.create_binding(
                principal=request.skill_principal,
                context_id=context_id,
                request=request.validated_data,
                idempotency_key=header_uuid(request, "Idempotency-Key"),
            ),
            status=201,
        )


class ContextBindingView(SkillsView):
    private_resource = True

    @contract(s.BindingOut, body=integration.BindingUpdate)
    def patch(self, request, binding_id):
        data = request.validated_data
        return self.output(
            request,
            s.BindingOut,
            consumers.update_binding(
                principal=request.skill_principal,
                binding_id=binding_id,
                revision=data["revision"],
                request=data["binding"],
            ),
        )

    @contract(body=integration.RevisionInput, status=204)
    def delete(self, request, binding_id):
        consumers.update_binding(
            principal=request.skill_principal,
            binding_id=binding_id,
            revision=request.validated_data["revision"],
            request=None,
        )
        return Response(status=204)


class ContextSessionsView(ConsumptionView):
    @consumption_contract(
        s.SessionOut, body=s.SessionInput, manual_parameters=[IDEMPOTENCY]
    )
    def post(self, request, binding_id):
        return self.output(
            s.SessionOut,
            consumers.open_context_session(
                principal=request.auth,
                binding_id=binding_id,
                request=request.validated_data,
                idempotency_key=header_uuid(request, "Idempotency-Key"),
            ),
        )


class ContextSessionAccessView(ConsumptionView):
    @consumption_contract(s.AccessCheck, body=s.EmptyInput)
    def post(self, request, binding_id, session_id):
        return self.output(
            s.AccessCheck,
            consumers.check_session_access(
                principal=request.auth,
                binding_id=binding_id,
                session_id=session_id,
            ),
        )


class ContextReceiptsView(ConsumptionView):
    context_purpose = "report"

    @consumption_contract(s.ReceiptAccepted, body=s.ReceiptInput)
    def post(self, request, binding_id):
        return self.output(
            s.ReceiptAccepted,
            consumers.append_receipt(
                principal=request.auth,
                binding_id=binding_id,
                receipt=request.validated_data,
            ),
        )


class ContextReleaseStateView(SkillsView):
    @contract(s.ContextReleaseState)
    def get(self, request, context_id):
        return self.output(
            request,
            s.ContextReleaseState,
            releases.context_release_state(
                principal=request.skill_principal,
                context_id=context_id,
            ),
        )


class ContextConsumersView(SkillsView):
    @contract(integration.ConsumerPage, query=integration.ConsumerQuery)
    def get(self, request, context_id):
        data = request.validated_query_data
        return self.output(
            request,
            integration.ConsumerPage,
            consumers.adoption_summary(
                principal=request.skill_principal,
                context_id=context_id,
                window={"window_days": data["window_days"]},
                page=data["page"],
                page_size=data["page_size"],
            ),
        )


class VersionInspectionView(SkillsView):
    private_resource = True
    resource_kind: ResourceKind = "agent_context"

    @contract(integration.VersionInspection, query=s.ManifestQuery)
    def get(self, request, context_id, number):
        return self.output(
            request,
            integration.VersionInspection,
            safety.inspect_version(
                principal=request.skill_principal,
                context_id=context_id,
                number=number,
                kind=self.resource_kind,
                **request.validated_query_data,
            ),
        )


class VersionInspectionFileView(SkillsView):
    private_resource = True
    resource_kind: ResourceKind = "agent_context"

    @contract(s.ConsumptionChunk, query=s.FileQuery)
    def get(self, request, context_id, number):
        response = self.output(
            request,
            s.ConsumptionChunk,
            safety.inspect_version_file(
                principal=request.skill_principal,
                context_id=context_id,
                number=number,
                kind=self.resource_kind,
                **request.validated_query_data,
            ),
        )
        response["Cache-Control"] = "private, no-store"
        return response


class VersionSafetyView(SkillsView):
    @contract(s.ContextSafetyOut)
    def get(self, request, version_id):
        return self.output(
            request,
            s.ContextSafetyOut,
            safety.get_version_safety(
                principal=request.skill_principal, version_id=version_id
            ),
        )

    @contract(s.ContextSafetyOut, body=s.QuarantineInput)
    def put(self, request, version_id):
        data = request.validated_data
        return self.output(
            request,
            s.ContextSafetyOut,
            safety.set_quarantine(
                principal=request.skill_principal,
                version_id=version_id,
                revision=data["revision"],
                active=data["quarantined"],
                reason=data["reason"],
                replacement_version_id=data["replacement_version_id"],
            ),
        )
