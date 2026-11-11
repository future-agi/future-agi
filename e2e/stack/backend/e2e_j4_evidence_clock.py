"""J4 E2E-image-only retention clock seam; absent from the product image.

Skills payload retention is 7-90 days, so a measured run cannot wait for real
expiry. This seam advances the retention clock of ONE scoped text-evidence run
to its expiry instant and then invokes the production purge task unchanged. It
does not edit annotations, sharing, packets, decisions or labels, and the
release service still discovers the expiry through its ordinary checks.
"""

from django.utils import timezone
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from model_hub.models.skills_hub_v11 import HubSkillTestRun
from model_hub.services.skills_hub.v11.jobs import purge_expired_payloads
from tfc.utils.api_errors import build_error_envelope


class E2EEvidenceClockRequest(serializers.Serializer):
    source = serializers.ChoiceField(choices=["skills_text"])
    source_id = serializers.UUIDField()


class E2EJ4EvidenceClockView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = E2EEvidenceClockRequest(data=request.data)
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
        rows = HubSkillTestRun.objects.filter(
            job_id=serializer.validated_data["source_id"],
            job__organization_id=organization.id,
            job__workspace_id=workspace.id,
            payload_purged_at__isnull=True,
        )
        now = timezone.now()
        if rows.update(payload_expires_at=now) != 1:
            return Response(
                build_error_envelope("Evidence not found.", status_code=404),
                status=404,
            )
        purged = purge_expired_payloads(batch_size=100)
        run = HubSkillTestRun.objects.get(job_id=serializer.validated_data["source_id"])
        return Response(
            {
                "status": True,
                "result": {
                    "source_id": str(run.job_id),
                    "payload_expires_at": run.payload_expires_at.isoformat(),
                    "payload_purged": run.payload_purged_at is not None,
                    "purge_counts": purged,
                    "boundary": "test-image retention clock seam; production purge task",
                },
            }
        )
