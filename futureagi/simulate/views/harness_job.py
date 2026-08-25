from __future__ import annotations

from django.conf import settings
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from simulate.models import HostedHarnessJob
from simulate.serializers.harness_job import (
    HarnessJobActionSerializer,
    HarnessJobCreateSerializer,
    HarnessPreflightSerializer,
)
from simulate.services.hosted_harness import (
    HostedHarnessError,
    create_hosted_job,
    request_cancellation,
)
from simulate.services.hosted_harness_gateway import (
    HOSTED_ENGINE_CATALOG,
    HOSTED_RUNTIME_CATALOG,
)
from simulate.temporal.client import (
    cancel_hosted_harness_gateway_workflow,
    start_hosted_harness_gateway_workflow,
)
from tfc.utils.api_contracts import validated_request


class HarnessJobViewSet(viewsets.ViewSet):
    """Tenant-bound control plane for hosted ALK harness jobs."""

    permission_classes = [IsAuthenticated]

    def _organization(self, request):
        return getattr(request, "organization", None) or getattr(
            request.user, "organization", None
        )

    def _job_or_404(self, request, job_id):
        organization = self._organization(request)
        return HostedHarnessJob.no_workspace_objects.filter(
            id=job_id, organization=organization
        ).first()

    @validated_request(
        request_serializer=HarnessJobCreateSerializer,
        reject_unknown_fields=True,
    )
    def create(self, request):
        organization = self._organization(request)
        if organization is None:
            return Response(
                {"detail": "Organization not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        idempotency_key = request.headers.get("Idempotency-Key", "").strip()
        if not idempotency_key:
            return Response(
                {"detail": "Idempotency-Key header is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            job, _ = create_hosted_job(
                organization,
                request.validated_data,
                idempotency_key=idempotency_key,
            )
            base_url = getattr(
                settings, "HARNESS_PUBLIC_BASE_URL", ""
            ) or request.build_absolute_uri("/").rstrip("/")
            start_hosted_harness_gateway_workflow(
                str(job.id),
                base_url,
                job.payload["retry"]["max_infrastructure_attempts"],
            )
        except HostedHarnessError as exc:
            return Response(exc.as_dict(), status=exc.status_code)
        except Exception:
            return Response(
                {"detail": "Hosted harness scheduler is unavailable"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(_serialize_job(job), status=status.HTTP_202_ACCEPTED)

    def list(self, request):
        organization = self._organization(request)
        jobs = HostedHarnessJob.no_workspace_objects.filter(
            organization=organization
        ).order_by("-created_at")[:100]
        return Response([_serialize_job(job) for job in jobs])

    @validated_request(
        request_serializer=HarnessPreflightSerializer,
        reject_unknown_fields=True,
    )
    @action(detail=False, methods=["post"])
    def preflight(self, request):
        payload = request.validated_data
        runtime = payload["runtime"]
        return Response(
            {
                "ready_to_submit": True,
                "effective_parallelism": runtime["parallelism"],
                "snapshot": {
                    "name": getattr(settings, "ALK_DAYTONA_SNAPSHOT", None),
                    "digest": getattr(settings, "ALK_DAYTONA_SNAPSHOT_DIGEST", None),
                    "engines": HOSTED_ENGINE_CATALOG,
                    "runtimes": HOSTED_RUNTIME_CATALOG,
                },
            }
        )

    def retrieve(self, request, pk=None):
        job = self._job_or_404(request, pk)
        if job is None:
            return Response(
                {"detail": "Hosted harness job not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(_serialize_job(job))

    @validated_request(
        request_serializer=HarnessJobActionSerializer,
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        job = self._job_or_404(request, pk)
        if job is None:
            return Response(
                {"detail": "Hosted harness job not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        request_cancellation(job, request.validated_data["reason"])
        try:
            cancel_hosted_harness_gateway_workflow(str(job.id))
        except Exception:
            return Response(
                {"detail": "Cancellation was recorded but could not be signaled"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        job.refresh_from_db()
        return Response(_serialize_job(job))

    @action(detail=False, methods=["get"])
    def health(self, request):
        return Response(
            {
                "configured": bool(
                    getattr(settings, "DAYTONA_API_KEY", "")
                    and getattr(settings, "ALK_DAYTONA_SNAPSHOT", "")
                ),
                "provider": "daytona",
            }
        )


def _serialize_job(job: HostedHarnessJob) -> dict:
    attempt = job.attempts.order_by("-attempt_number").first()
    events = []
    if attempt:
        recent = list(attempt.events.filter(accepted=True).order_by("-sequence")[:100])
        events = [
            {
                "event_id": event.event_id,
                "sequence": event.sequence,
                "stage": event.stage,
                "type": event.event_type,
                "payload": event.payload,
                "emitted_at": event.emitted_at.isoformat(),
            }
            for event in reversed(recent)
        ]
    return {
        "job": {
            "job_id": str(job.id),
            "run_id": str(job.run_id),
            "source": job.payload["source"],
            "metadata": job.payload.get("metadata", {}),
        },
        "status": {
            "state": job.state,
            "stage": job.current_stage,
            "updated_at": job.updated_at.isoformat(),
            "attempt": attempt.attempt_number if attempt else 0,
            "completed_scenarios": job.completed_count,
            "failed_scenarios": job.failed_count,
            "total_scenarios": job.scenario_count,
            "deadline_at": job.deadline_at.isoformat(),
            "failure": job.failure,
        },
        "events": events,
    }
