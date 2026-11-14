"""Legacy evaluator identity fixture, copied only into the E2E backend image.

Modern create-v2 returns a UUID and leaves the legacy integer eval_id at zero.
This fixture represents migrated legacy definitions without adding a production
identity allocator or mutating source rows through a read-only StateProbe.
"""

from django.db import connection, transaction
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import User, Workspace
from model_hub.models.evals_metric import EvalTemplate


class LegacyEvalRequest(serializers.Serializer):
    template_id = serializers.UUIDField()

    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) != {"template_id"}:
            raise serializers.ValidationError(
                {"non_field_errors": ["Only template_id is accepted."]}
            )
        return super().to_internal_value(data)


class E2ELegacyEvalView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = LegacyEvalRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        organization = getattr(request, "organization", None)
        requested_workspace = getattr(request, "workspace", None)
        user = User.objects.filter(pk=request.user.pk, is_active=True).first()
        workspace = Workspace.no_workspace_objects.filter(
            pk=getattr(requested_workspace, "pk", None),
            organization_id=getattr(organization, "pk", None),
            is_active=True,
        ).first()
        if (
            user is None
            or workspace is None
            or not user.can_write_to_workspace(workspace)
        ):
            return Response(
                {"detail": "Workspace write access is required."}, status=403
            )
        with transaction.atomic():
            # All fixture allocators take this lock. No production sequence or
            # row is introduced; the test image and its database are disposable.
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [52180001])
            template = (
                EvalTemplate.no_workspace_objects.select_for_update()
                .filter(
                    pk=serializer.validated_data["template_id"],
                    organization_id=workspace.organization_id,
                    workspace=workspace,
                    owner="user",
                    name__startswith="e2e-",
                )
                .first()
            )
            if template is None:
                return Response({"detail": "Template not found."}, status=404)
            # all_objects still applies ambient workspace scope. Allocation is
            # deliberately global, including soft-deleted legacy identities.
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT COALESCE(MAX(eval_id), 0) FROM model_hub_evaltemplate"
                )
                high = cursor.fetchone()[0]
            legacy_id = max(1000000000, high) + 1
            if legacy_id > 2147483647:
                return Response(
                    {"detail": "Legacy fixture identity space exhausted."}, status=400
                )
            template.eval_id = legacy_id
            template.save(update_fields=["eval_id"])
        return Response({"id": str(template.pk), "eval_id": legacy_id})
