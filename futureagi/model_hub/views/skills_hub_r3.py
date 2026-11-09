"""Serializer-driven workspace spending and private checkpoint entry points."""

from model_hub.serializers import skills_hub_integration as s
from model_hub.serializers.skills_hub_r3 import SpendPolicyInput
from model_hub.serializers.skills_hub_v12 import PageQuery
from model_hub.services.skills_hub.r3 import draft_recovery, spend_policy
from model_hub.services.skills_hub.v12.types import ResourceKind
from model_hub.views.skills_context_foundation import IDEMPOTENCY, contract, header_uuid
from model_hub.views.skills_hub import SkillsView


class SpendPolicyView(SkillsView):
    private_resource = True

    @contract(s.SpendSummary)
    def get(self, request):
        return self.output(
            request,
            s.SpendSummary,
            spend_policy.spend_summary(principal=request.skill_principal),
        )

    @contract(s.SpendSummary, body=SpendPolicyInput, manual_parameters=[IDEMPOTENCY])
    def put(self, request):
        spend_policy.update_spend_policy(
            principal=request.skill_principal,
            idempotency_key=header_uuid(request, "Idempotency-Key"),
            **request.validated_data,
        )
        return self.output(
            request,
            s.SpendSummary,
            spend_policy.spend_summary(principal=request.skill_principal),
        )


class DraftCheckpointRestoreView(SkillsView):
    private_resource = True
    resource_kind: ResourceKind = "skill"

    @contract(
        s.CheckpointRestored,
        body=s.CheckpointRestoreInput,
        manual_parameters=[IDEMPOTENCY],
    )
    def post(self, request, resource_id, checkpoint_id):
        return self.output(
            request,
            s.CheckpointRestored,
            draft_recovery.restore_checkpoint(
                principal=request.skill_principal,
                resource_id=resource_id,
                kind=self.resource_kind,
                checkpoint_id=checkpoint_id,
                idempotency_key=header_uuid(request, "Idempotency-Key"),
                **request.validated_data,
            ),
        )


class DraftCheckpointsView(SkillsView):
    private_resource = True
    resource_kind: ResourceKind = "skill"

    @contract(s.CheckpointPage, query=PageQuery)
    def get(self, request, resource_id):
        return self.output(
            request,
            s.CheckpointPage,
            draft_recovery.list_checkpoints(
                principal=request.skill_principal,
                resource_id=resource_id,
                kind=self.resource_kind,
                **request.validated_query_data,
            ),
        )

    @contract(
        s.CheckpointCreated, body=s.CheckpointCreate, manual_parameters=[IDEMPOTENCY]
    )
    def post(self, request, resource_id):
        return self.output(
            request,
            s.CheckpointCreated,
            draft_recovery.checkpoint_draft(
                principal=request.skill_principal,
                resource_id=resource_id,
                kind=self.resource_kind,
                idempotency_key=header_uuid(request, "Idempotency-Key"),
                **request.validated_data,
            ),
        )
