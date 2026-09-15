from django.urls import include, path

from sdk.views.analytics import (
    SimulationAnalyticsView,
    SimulationMetricsView,
    SimulationRunsView,
)
from sdk.views.eval_ci_cd import CICDEvaluationsView
from sdk.views.evaluations import (
    GetEvalStructureEvalIdView,
    GetEvalsView,
    StandaloneEvalView,
    StandaloneEvalView_v2,
)

urlpatterns = [
    path(
        "api/v1/",
        include(
            [
                path(
                    "eval/<str:eval_id>/",
                    GetEvalStructureEvalIdView.as_view(),
                    name="get-eval-structure-eval-id",
                ),
                path("eval/", StandaloneEvalView.as_view(), name="standalone-eval"),
                path(
                    "new-eval/",
                    StandaloneEvalView_v2.as_view(),
                    name="standalone-eval-v2",
                ),
                path("get-evals/", GetEvalsView.as_view(), name="get-evals"),
                path(
                    "evaluate-pipeline/",
                    CICDEvaluationsView.as_view(),
                    name="ci-cd-evals",
                ),
                path(
                    "simulation/metrics/",
                    SimulationMetricsView.as_view(),
                    name="simulation-metrics",
                ),
                path(
                    "simulation/runs/",
                    SimulationRunsView.as_view(),
                    name="simulation-runs",
                ),
                path(
                    "simulation/analytics/",
                    SimulationAnalyticsView.as_view(),
                    name="simulation-analytics",
                ),
            ]
        ),
    ),
]
