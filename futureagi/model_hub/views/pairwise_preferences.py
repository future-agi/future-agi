from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from model_hub.models.dataset_versions import DatasetVersionItem
from model_hub.models.experiments import ExperimentsTable
from model_hub.models.pairwise_preferences import PairwisePreference
from model_hub.serializers.contracts import (
    MODEL_HUB_ERROR_RESPONSES,
    ModelHubErrorResponseSerializer,
)
from model_hub.serializers.pairwise_preferences import (
    PairwisePreferenceSerializer,
    PairwiseSummaryQuerySerializer,
    PairwiseSummarySerializer,
)
from model_hub.services.results_catalog import live_runs
from tfc.routers import primary_required
from tfc.utils.api_contracts import validated_request
from tfc.utils.tenant_scope import caller_workspace, scope_to_caller


def _pair(request, experiment_id, data):
    experiment = get_object_or_404(
        scope_to_caller(
            ExperimentsTable.no_workspace_objects.filter(dataset__deleted=False),
            request,
            organization_field="dataset__organization",
            workspace_field="dataset__workspace",
        ),
        pk=experiment_id,
    )
    runs = live_runs(experiment.dataset).filter(experiment=experiment)
    a, b = [get_object_or_404(runs, pk=data[key]) for key in ("run_a", "run_b")]
    if a.pk == b.pk:
        raise ValidationError("run_a and run_b must differ.")
    members = DatasetVersionItem.no_workspace_objects
    rows = members.filter(
        dataset_version=a.dataset_version,
        example_id__in=(
            members.filter(dataset_version=b.dataset_version).values("example_id")
        ),
    ).values_list("example_id", flat=True)
    return {"experiment": experiment, "run_a": a, "run_b": b}, rows


class PairwisePreferenceView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=PairwisePreferenceSerializer,
        responses={204: "Preference saved."},
    )
    @primary_required
    def post(self, request, experiment_id):
        """Upsert this reviewer's human vote for a stable example_id."""
        data = request.validated_data
        pair, rows = _pair(request, experiment_id, data)
        if not request.user.can_write_to_workspace(caller_workspace(request)):
            raise PermissionDenied("Workspace write access required")
        if not rows.filter(example_id=data["row_id"]).exists():
            raise ValidationError("row_id must belong to both runs' dataset versions.")
        PairwisePreference.objects.update_or_create(
            **pair,
            row_id=data["row_id"],
            created_by=request.user,
            defaults={
                "winner": data["winner"],
                "note": data["note"],
                "source": "human",
            },
        )
        return Response(status=204)

    @validated_request(
        query_serializer=PairwiseSummaryQuerySerializer,
        responses={
            200: PairwiseSummarySerializer,
            401: ModelHubErrorResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
    )
    @primary_required
    def get(self, request, experiment_id):
        """Summarize human votes for this ordered run pair."""
        pair, rows = _pair(request, experiment_id, request.validated_query_data)
        result = PairwisePreference.objects.filter(
            **pair,
            source="human",
            row_id__in=rows,
        ).aggregate(
            a_wins=Count("id", filter=Q(winner="a")),
            b_wins=Count("id", filter=Q(winner="b")),
            ties=Count("id", filter=Q(winner="tie")),
            rows_reviewed=Count("row_id", distinct=True),
        )
        if "judge_job" in request.validated_query_data:
            from model_hub.services.pairwise_judges import judge_summary
            from model_hub.views.pairwise_judges import scoped_job

            job = scoped_job(
                request, experiment_id, request.validated_query_data["judge_job"]
            )
            result.update(judge_summary(pair, rows, job))
        return Response({**result, "rows_total": rows.count()})
