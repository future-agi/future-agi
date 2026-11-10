"""J1: scoped, non-cutover migration review and explicit export."""

import base64

from model_hub.models.skills_hub import HubSkillDraft
from model_hub.serializers import skills_migration as s
from model_hub.serializers.skills_workflows import IntakePlan
from model_hub.services.skills_hub.r3 import import_plans, migration_receipts
from model_hub.views.skills_context_foundation import contract
from model_hub.views.skills_workflows import PrivateWorkflowView


class LangfusePreviewView(PrivateWorkflowView):
    @contract(IntakePlan, body=s.LangfuseExport)
    def post(self, request):
        return self.output(
            request,
            IntakePlan,
            migration_receipts.preview_langfuse_export(
                principal=request.skill_principal, export=request.validated_data
            ),
        )


class MigrationReviewView(PrivateWorkflowView):
    @contract(s.MigrationReview, body=s.MigrationReviewInput)
    def post(self, request, plan_id):
        principal = request.skill_principal
        result = migration_receipts.read_import_comparison(
            principal=principal, plan_id=plan_id, **request.validated_data
        )
        _, item = import_plans.checked_plan_item(
            principal=principal,
            plan_id=plan_id,
            kind="skill_import",
            **request.validated_data,
        )
        result["has_draft"] = HubSkillDraft.all_objects.filter(
            skill_id=item["destination_id"],
            user_id=principal.user_id,
            discarded_at__isnull=True,
        ).exists()
        return self.output(request, s.MigrationReview, result)


class MigrationExportView(PrivateWorkflowView):
    @contract(s.MigrationExport)
    def get(self, request, resource_id, number):
        result = migration_receipts.export_migrated_version(
            principal=request.skill_principal, resource_id=resource_id, number=number
        )
        result["archive_base64"] = base64.b64encode(result.pop("archive")).decode("ascii")
        return self.output(request, s.MigrationExport, result)
