from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import structlog
from django.db.models import Sum

from tfc.deployment_telemetry.ch_counts import (
    count_spans,
    count_traces,
    ingesting_project_ids,
)

logger = structlog.get_logger(__name__)


def _safe_count(name: str, collector: Callable[[], int]) -> int | None:
    """Run a collector, returning None (not 0) on failure.

    A crashed collector reported as 0 is indistinguishable from genuine
    zero usage, so a broken collector would silently corrupt the metric.
    Returning None lets the receiver tell "collection failed" apart from
    "no activity".
    """
    try:
        return int(collector())
    except Exception:
        logger.warning(
            "deployment_telemetry_collector_failed",
            collector=name,
            exc_info=True,
        )
        return None


def collect_counts(window_start: datetime, window_end: datetime) -> dict[str, int | None]:
    # Traces and spans live in the CH v2 `spans` table post-CH-25; the
    # legacy PG Trace/ObservationSpan tables are being removed and report 0
    # on modern deployments.
    def traces() -> int:
        return count_traces(window_start, window_end)

    def spans() -> int:
        return count_spans(window_start, window_end)

    def projects() -> int:
        from tracer.models.project import Project

        return Project.no_workspace_objects.filter(
            created_at__gte=window_start,
            created_at__lt=window_end,
        ).count()

    def eval_loggers() -> int:
        from tracer.models.observation_span import EvalLogger

        return EvalLogger.no_workspace_objects.filter(
            created_at__gte=window_start,
            created_at__lt=window_end,
        ).count()

    def model_hub_evaluations() -> int:
        from model_hub.models.evaluation import Evaluation

        return Evaluation.no_workspace_objects.filter(
            created_at__gte=window_start,
            created_at__lt=window_end,
        ).count()

    def dataset_eval_runs() -> int:
        from model_hub.models.develop_dataset import Cell

        return Cell.no_workspace_objects.filter(
            column__source="evaluation",
            updated_at__gte=window_start,
            updated_at__lt=window_end,
        ).count()

    def experiments() -> int:
        from model_hub.models.experiments import ExperimentsTable

        return ExperimentsTable.no_workspace_objects.filter(
            created_at__gte=window_start,
            created_at__lt=window_end,
        ).count()

    def datasets() -> int:
        from model_hub.models.develop_dataset import Dataset

        return Dataset.no_workspace_objects.filter(
            created_at__gte=window_start,
            created_at__lt=window_end,
        ).count()

    def gateway_requests() -> int:
        from agentcc.models.request_log import AgentccRequestLog

        return AgentccRequestLog.no_workspace_objects.filter(
            started_at__gte=window_start,
            started_at__lt=window_end,
        ).count()

    def simulation_runs() -> int:
        from simulate.models.test_execution import TestExecution

        return TestExecution.no_workspace_objects.filter(
            created_at__gte=window_start,
            created_at__lt=window_end,
        ).count()

    def simulation_calls() -> int:
        from simulate.models.test_execution import TestExecution

        result = TestExecution.no_workspace_objects.filter(
            created_at__gte=window_start,
            created_at__lt=window_end,
        ).aggregate(total=Sum("completed_calls"))
        return result["total"] or 0

    def active_users() -> int:
        """Distinct users active in the window: app-entity creators ∪ owners of
        projects that ingested spans (so SDK-only users still count)."""
        from model_hub.models.develop_dataset import Dataset
        from model_hub.models.evaluation import Evaluation
        from model_hub.models.experiments import ExperimentsTable
        from tracer.models.project import Project

        user_ids: set[int | None] = set()
        for model in (Evaluation, ExperimentsTable, Dataset, Project):
            user_ids.update(
                model.no_workspace_objects.filter(
                    created_at__gte=window_start,
                    created_at__lt=window_end,
                ).values_list("user_id", flat=True)
            )

        ingesting_projects = ingesting_project_ids(window_start, window_end)
        if ingesting_projects:
            user_ids.update(
                Project.no_workspace_objects.filter(
                    id__in=ingesting_projects,
                ).values_list("user_id", flat=True)
            )

        user_ids.discard(None)
        return len(user_ids)

    counts: dict[str, int | None] = {
        "traces_count": _safe_count("traces", traces),
        "spans_count": _safe_count("spans", spans),
        "projects_count": _safe_count("projects", projects),
        "eval_logger_count": _safe_count("eval_loggers", eval_loggers),
        "model_hub_evaluations_count": _safe_count(
            "model_hub_evaluations",
            model_hub_evaluations,
        ),
        "dataset_eval_runs_count": _safe_count(
            "dataset_eval_runs",
            dataset_eval_runs,
        ),
        "simulation_runs_count": _safe_count("simulation_runs", simulation_runs),
        "simulation_calls_count": _safe_count("simulation_calls", simulation_calls),
        "experiments_count": _safe_count("experiments", experiments),
        "gateway_requests_count": _safe_count("gateway_requests", gateway_requests),
        "datasets_count": _safe_count("datasets", datasets),
        "active_users_count": _safe_count("active_users", active_users),
    }

    # Derived total is only meaningful when every component succeeded;
    # otherwise it would understate usage by silently treating a failed
    # collector as zero.
    eval_components = (
        counts["eval_logger_count"],
        counts["model_hub_evaluations_count"],
        counts["dataset_eval_runs_count"],
    )
    if any(component is None for component in eval_components):
        counts["total_evaluations_count"] = None
    else:
        counts["total_evaluations_count"] = sum(eval_components)
    return counts
