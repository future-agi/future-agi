from django.utils import timezone
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from simulate.models import HostedHarnessJob
from simulate.serializers.harness_environment import (
    HarnessEnvironmentListQuerySerializer,
    HarnessEnvironmentListResponseSerializer,
    HarnessEnvironmentRunResponseSerializer,
)
from simulate.services.harness_environment import annotate_for_list, environment_row
from simulate.services.harness_provider import (
    _organization,
    _scope_jobs,
    _workspace,
    get_harness_provider,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.pagination import ExtendedPageNumberPagination


class HarnessEnvironmentViewSet(viewsets.ViewSet):
    """The environments surface: list, delete, and start a simulation.

    An environment is the job that built it (the world itself lives in object
    storage, addressed from the job's metadata), so these endpoints project the
    same rows the harness-jobs API serves. They exist separately because the
    list needs a row, not a run: the jobs list returns every event, receipt and
    stage-output payload for up to a hundred jobs, which is a detail document
    repeated a hundred times.

    Running and grading a simulation are deliberately not here. ``run`` starts
    one and returns 202; progress is read from the job.
    """

    permission_classes = [IsAuthenticated]
    pagination_class = ExtendedPageNumberPagination

    def _queryset(self, request):
        return annotate_for_list(
            _scope_jobs(
                HostedHarnessJob.no_workspace_objects.filter(
                    organization=_organization(request), deleted=False
                ),
                request,
            )
        )

    def _job(self, request, pk):
        return self._queryset(request).filter(id=pk).first()

    @validated_request(
        query_serializer=HarnessEnvironmentListQuerySerializer,
        responses={200: HarnessEnvironmentListResponseSerializer},
    )
    def list(self, request):
        organization = _organization(request)
        if organization is None:
            return Response(
                {"detail": "an organization is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(self._queryset(request), request, view=self)
        return paginator.get_paginated_response([environment_row(job) for job in page])

    @swagger_auto_schema(responses={204: "Deleted"})
    def destroy(self, request, pk=None):
        """Soft-delete an environment, cancelling its run first if one is live.

        Deleting while a sandbox is running would leave that sandbox billing
        against a row nobody can see, so cancellation is requested before the
        row disappears. The authoring archive and the organization's secrets are
        left in place: neither is owned by this row, and other environments may
        reference the same credentials.
        """
        from simulate.services.hosted_harness import request_cancellation
        from simulate.temporal.client import cancel_hosted_harness_gateway_workflow

        job = self._job(request, pk)
        if job is None:
            return Response(
                {"detail": "Environment not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        terminal = {
            HostedHarnessJob.State.COMPLETED,
            HostedHarnessJob.State.FAILED,
            HostedHarnessJob.State.CANCELED,
        }
        if job.state not in terminal:
            request_cancellation(job, "user_canceled")
            try:
                cancel_hosted_harness_gateway_workflow(str(job.id))
            except Exception:  # noqa: BLE001 - the row is still deleted below
                # A scheduler that cannot be reached must not strand the user
                # with an environment they cannot remove. The workflow is
                # bounded by the job deadline and its sandbox by its own TTL,
                # so the worst case is a sandbox that expires on its own.
                pass
        job.deleted = True
        job.deleted_at = timezone.now()
        job.save(update_fields=["deleted", "deleted_at", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    @validated_request(responses={202: HarnessEnvironmentRunResponseSerializer})
    @action(detail=True, methods=["post"])
    def run(self, request, pk=None):
        """Start a simulation on an existing environment.

        This reuses the saved contract and scenario suite rather than authoring
        a new one, which is what makes a second run comparable to the first.
        """
        from simulate.services.hosted_harness import HostedHarnessError

        job = self._job(request, pk)
        if job is None:
            return Response(
                {"detail": "Environment not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            result = get_harness_provider().rerun_saved(
                str(job.id),
                organization=_organization(request),
                workspace=_workspace(request),
                environment_values={},
            )
        except HostedHarnessError as exc:
            return Response(exc.as_dict(), status=exc.status_code)

        started = result.get("job") or {}
        started_status = result.get("status") or {}
        return Response(
            {
                "environment_id": str(job.id),
                "job_id": started.get("job_id") or str(job.id),
                "run_id": started.get("run_id"),
                "state": started_status.get("state"),
                "stage": started_status.get("stage"),
            },
            status=status.HTTP_202_ACCEPTED,
        )
