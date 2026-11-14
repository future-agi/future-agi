"""E2E-image-only attachment opt-in for one project; absent from the product image.

The product has no API, UI or command that turns on a project's attachment
scope: ``AttachmentScope`` is created disabled, with media redaction required,
by the F01 bootstrap. This fixture is the operator step a deployment performs
by hand. It changes only the scope's policy switches and bumps
``policy_version``, as the 0133 scope fence requires; every upload, bind,
validation and read after it runs the unmodified product path.
"""

from django.db import transaction
from django.db.models import F
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from tfc.utils.api_errors import build_error_envelope
from tracer.models.attachment import AttachmentScope
from tracer.models.project import Project


class E2EAttachmentScopeRequest(serializers.Serializer):
    project_id = serializers.UUIDField()


class E2EAttachmentScopeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = E2EAttachmentScopeRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        organization = (
            getattr(request, "organization", None) or request.user.organization
        )
        workspace = getattr(request, "workspace", None)
        if not workspace or not request.user.can_write_to_workspace(workspace):
            return Response(
                build_error_envelope(
                    "Workspace write access is required.", status_code=403
                ),
                status=403,
            )
        project = Project.no_workspace_objects.filter(
            pk=serializer.validated_data["project_id"],
            organization=organization,
            workspace=workspace,
            deleted=False,
        ).first()
        if project is None:
            return Response(
                build_error_envelope("Project not found.", status_code=404),
                status=404,
            )
        with transaction.atomic():
            scope, _ = AttachmentScope.objects.get_or_create(
                project=project,
                defaults={
                    "organization_id": project.organization_id,
                    "workspace_id": project.workspace_id,
                },
            )
            AttachmentScope.objects.filter(pk=scope.pk).update(
                enabled=True,
                require_media_redaction=False,
                record_audio=True,
                policy_version=F("policy_version") + 1,
            )
            scope.refresh_from_db()
        return Response(
            {
                "scope_id": str(scope.pk),
                "project_id": str(scope.project_id),
                "policy_version": scope.policy_version,
                "deletion_generation": scope.deletion_generation,
            }
        )
