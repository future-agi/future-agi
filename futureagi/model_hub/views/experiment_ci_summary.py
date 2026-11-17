from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from model_hub.serializers.contracts import MODEL_HUB_ERROR_RESPONSES
from model_hub.serializers.experiment_ci_summary import (
    ExperimentCICommentQuerySerializer,
    ExperimentCICommentSerializer,
    ExperimentCISummaryQuerySerializer,
    ExperimentCISummarySerializer,
)
from model_hub.services.experiment_ci_comment import render_ci_comment
from model_hub.services.results_catalog import (
    ResultsReadError,
    live_runs,
    pinned_baseline,
)
from model_hub.services.results_grid import summary, vs_baseline_counts
from model_hub.services.results_selection import select_results
from model_hub.views.experiments import _scoped_experiment_queryset
from tfc.routers import primary_required
from tfc.utils.api_contracts import validated_request


def _ci_summary(request, experiment_id, query, *, regressions=None):
    experiment = (
        _scoped_experiment_queryset(request)
        .filter(pk=experiment_id, dataset__deleted=False)
        .select_related("dataset")
        .first()
    )
    if experiment is None:
        raise ResultsReadError("Experiment not found", 404)
    run_id = str(query["run_id"])
    if (
        not live_runs(experiment.dataset)
        .filter(pk=run_id, experiment=experiment)
        .exists()
    ):
        raise ResultsReadError("Run not found in this experiment.", 404)
    baseline, _ = pinned_baseline(experiment.dataset, query.get("baseline_run_id"))
    if baseline is None:
        raise ResultsReadError("No baseline run is pinned for this dataset.", 409)
    if run_id == baseline:
        raise ResultsReadError("Run must differ from the baseline run.", 400)
    selection = select_results(
        request,
        experiment.dataset_id,
        {
            "runs": [run_id, baseline],
            "baseline": baseline,
        },
    )
    if regressions is not None and any(
        run.status != "completed" for run in selection.runs
    ):
        raise ResultsReadError(
            "CI comments require completed candidate and baseline runs.", 409
        )
    aggregates = summary(selection)["aggregates"]
    counts = vs_baseline_counts(selection, regressions=regressions)
    evaluators = {
        key: {
            **per_run[run_id],
            "mean": aggregates[key][run_id]["value"],
            "baseline_mean": aggregates[key][baseline]["value"],
        }
        for key, per_run in counts.items()
    }
    total = sum(value["regressed"] for value in evaluators.values())
    return {
        "experiment_id": experiment.id,
        "run_id": run_id,
        "baseline_run_id": baseline,
        "evaluators": evaluators,
        "regressed_total": total,
        "passed": total <= query["max_regressed"],
    }


class ExperimentCISummaryView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        query_serializer=ExperimentCISummaryQuerySerializer,
        responses={
            200: ExperimentCISummarySerializer,
            503: MODEL_HUB_ERROR_RESPONSES[500],
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
        operation_id="experiment_ci_summary",
        operation_description="Read-only verdict against the pinned dataset baseline.",
    )
    @primary_required
    def get(self, request, experiment_id):
        query = request.validated_query_data
        return Response(
            ExperimentCISummarySerializer(
                _ci_summary(request, experiment_id, query)
            ).data
        )


class ExperimentCICommentView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        query_serializer=ExperimentCICommentQuerySerializer,
        responses={
            200: ExperimentCICommentSerializer,
            401: MODEL_HUB_ERROR_RESPONSES[403],
            503: MODEL_HUB_ERROR_RESPONSES[500],
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
        operation_id="experiment_ci_comment",
        operation_description=(
            "Read-only Markdown CI verdict for a completed run against a chosen "
            "baseline (the pinned dataset baseline when omitted)."
        ),
    )
    @primary_required
    def get(self, request, experiment_id):
        query = request.validated_query_data
        regressions = []
        result = _ci_summary(request, experiment_id, query, regressions=regressions)
        return Response(
            ExperimentCICommentSerializer(
                render_ci_comment(result, regressions, query)
            ).data
        )
