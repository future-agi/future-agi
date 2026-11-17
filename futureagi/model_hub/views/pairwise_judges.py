from django.db.models import F
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from model_hub.models.pairwise_judges import PairwiseJudgeJob
from model_hub.serializers.contracts import (
    MODEL_HUB_ERROR_RESPONSES,
    ModelHubErrorResponseSerializer,
)
from model_hub.serializers.pairwise_judges import (
    PairwiseJudgeCancelSerializer,
    PairwiseJudgeCreateSerializer,
    PairwiseJudgeItemSerializer,
    PairwiseJudgeJobSerializer,
    PairwiseJudgeListQuerySerializer,
    PairwiseJudgeListSerializer,
    PairwiseJudgePageSerializer,
)
from model_hub.services.pairwise_judges import (
    cancel_job,
    create_job,
    job_data,
    reconcile_job,
    require_version,
)
from tfc.routers import primary_required
from tfc.utils.api_contracts import validated_request
from tfc.utils.tenant_scope import caller_workspace, scope_to_caller


def scoped_jobs(request, experiment_id):
    return scope_to_caller(
        PairwiseJudgeJob.objects.filter(
            experiment_id=experiment_id,
            experiment__deleted=False,
            experiment__dataset__deleted=False,
            organization_id=F("experiment__dataset__organization_id"),
            workspace_id=F("experiment__dataset__workspace_id"),
        ),
        request,
    )


def scoped_job(request, experiment_id, job_id):
    return get_object_or_404(scoped_jobs(request, experiment_id), pk=job_id)


def require_write(request):
    if not request.user.can_write_to_workspace(caller_workspace(request)):
        raise PermissionDenied("Workspace write access required")


class PairwiseJudgeCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        query_serializer=PairwiseJudgeListQuerySerializer,
        responses={
            200: PairwiseJudgeListSerializer,
            401: ModelHubErrorResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
    )
    @primary_required
    def get(self, request, experiment_id):
        from model_hub.views.pairwise_preferences import _pair

        data = request.validated_query_data
        pair, _ = _pair(request, experiment_id, data)
        a, b = sorted((pair["run_a"], pair["run_b"]), key=lambda run: str(run.pk))
        offset, limit = data["offset"], data["limit"]
        jobs = list(
            scoped_jobs(request, experiment_id)
            .filter(run_a=a, run_b=b)
            .order_by("-created_at", "-pk")[offset : offset + limit + 1]
        )
        # Discovery is read-only: never create, enqueue or cancel a job here.
        return Response(
            {
                "jobs": [job_data(job) for job in jobs[:limit]],
                "next_offset": offset + limit if len(jobs) > limit else None,
            }
        )

    @validated_request(
        request_serializer=PairwiseJudgeCreateSerializer,
        responses={
            200: PairwiseJudgeJobSerializer,
            202: PairwiseJudgeJobSerializer,
            401: ModelHubErrorResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
    )
    @primary_required
    def post(self, request, experiment_id):
        from model_hub.views.pairwise_preferences import _pair

        pair, _ = _pair(request, experiment_id, request.validated_data)
        require_write(request)
        dataset = pair["experiment"].dataset
        if dataset.workspace_id is None:
            raise ValidationError("A workspace is required for a judge job.")
        version = require_version(
            request.validated_data["judge_version"],
            dataset.organization,
            dataset.workspace,
        )
        job, created = create_job(pair, version, request.user)
        if not created:
            job = reconcile_job(job.pk)
        return Response(job_data(job), status=202 if created else 200)


class PairwiseJudgeDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        query_serializer=PairwiseJudgePageSerializer,
        responses={
            200: PairwiseJudgeJobSerializer,
            401: ModelHubErrorResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
    )
    @primary_required
    def get(self, request, experiment_id, job_id):
        job = scoped_job(request, experiment_id, job_id)
        job = reconcile_job(job.pk)
        data = job_data(job)
        page = request.validated_query_data
        offset, limit = page["offset"], page["limit"]
        data["items"] = PairwiseJudgeItemSerializer(
            job.items.order_by("example_id", "trial_number")[offset : offset + limit],
            many=True,
        ).data
        data["next_offset"] = offset + limit if offset + limit < data["total"] else None
        return Response(data)


class PairwiseJudgeCancelView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=PairwiseJudgeCancelSerializer,
        responses={
            200: PairwiseJudgeJobSerializer,
            401: ModelHubErrorResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
    )
    @primary_required
    def post(self, request, experiment_id, job_id):
        job = scoped_job(request, experiment_id, job_id)
        require_write(request)
        return Response(job_data(cancel_job(job.pk)))
