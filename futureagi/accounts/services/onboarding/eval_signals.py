from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Q

from accounts.models import OnboardingActivationEvent
from accounts.services.onboarding.activation_events import latest_event


@dataclass(frozen=True)
class EvalOnboardingSignals:
    source_count: int = 0
    sample_source_count: int = 0
    source_type: str | None = None
    source_id: str | None = None
    source_name: str | None = None
    scorer_count: int = 0
    scorer_id: str | None = None
    scorer_template_id: str | None = None
    scorer_name: str | None = None
    eval_group_count: int = 0
    eval_group_id: str | None = None
    run_count: int = 0
    run_id: str | None = None
    run_status: str | None = None
    run_completed_at: object | None = None
    failure_count: int = 0
    review_event_id: str | None = None
    reviewed_at: object | None = None
    failure_action_event_id: str | None = None
    failure_action_at: object | None = None
    is_sample_only: bool = False
    permission_limited: bool = False
    diagnostics: tuple[str, ...] = ()

    @property
    def has_source(self):
        return self.source_count > 0 and not self.is_sample_only

    @property
    def has_scorer(self):
        return self.scorer_count > 0

    @property
    def has_completed_run(self):
        return self.run_count > 0

    @property
    def has_failures(self):
        return self.failure_count > 0

    @property
    def has_review(self):
        return bool(self.review_event_id)

    @property
    def has_failure_action(self):
        return bool(self.failure_action_event_id)

    @property
    def first_loop_completed(self):
        return (
            self.has_source
            and self.has_scorer
            and self.has_completed_run
            and self.has_review
            and (self.has_failure_action or not self.has_failures)
        )

    def to_activation_eval_state(self, stage):
        return {
            "source_type": self.source_type,
            "source_id": self.source_id,
            "source_name": self.source_name,
            "scorer_id": self.scorer_id,
            "scorer_template_id": self.scorer_template_id,
            "scorer_name": self.scorer_name,
            "eval_group_id": self.eval_group_id,
            "run_id": self.run_id,
            "run_status": self.run_status,
            "run_completed_at": self.run_completed_at,
            "failure_count": self.failure_count,
            "reviewed_at": self.reviewed_at,
            "failure_action_at": self.failure_action_at,
            "stage": stage,
            "has_source": self.has_source,
            "has_scorer": self.has_scorer,
            "has_completed_run": self.has_completed_run,
            "has_failures": self.has_failures,
            "has_review": self.has_review,
            "has_failure_action": self.has_failure_action,
            "is_sample": self.is_sample_only,
            "sample_source_count": self.sample_source_count,
            "permission_limited": self.permission_limited,
            "diagnostics": list(self.diagnostics),
        }


def _latest_eval_event(
    *,
    organization,
    workspace,
    event_names,
    is_sample=False,
    source_id=None,
    scorer_id=None,
    scorer_template_id=None,
    run_id=None,
    eval_group_id=None,
):
    queryset = OnboardingActivationEvent.no_workspace_objects.filter(
        organization=organization,
        workspace=workspace,
        event_name__in=event_names,
        product_path="evals",
        is_sample=is_sample,
    )
    matchers = Q()
    if source_id:
        matchers |= Q(metadata__source_id=str(source_id))
        matchers |= Q(metadata__dataset_id=str(source_id))
        matchers |= Q(metadata__project_id=str(source_id))
    if scorer_id:
        matchers |= Q(metadata__scorer_id=str(scorer_id))
        matchers |= Q(metadata__eval_id=str(scorer_id))
    if scorer_template_id:
        matchers |= Q(metadata__scorer_template_id=str(scorer_template_id))
        matchers |= Q(metadata__eval_template_id=str(scorer_template_id))
        matchers |= Q(metadata__eval_id=str(scorer_template_id))
    if run_id:
        matchers |= Q(metadata__run_id=str(run_id))
        matchers |= Q(metadata__eval_task_id=str(run_id))
        matchers |= Q(metadata__evaluation_id=str(run_id))
    if eval_group_id:
        matchers |= Q(metadata__eval_group_id=str(eval_group_id))

    if matchers:
        event = (
            queryset.filter(matchers).order_by("-occurred_at", "-created_at").first()
        )
        if event:
            return event
    return queryset.order_by("-occurred_at", "-created_at").first()


def _safe_metadata_int(metadata, *keys):
    for key in keys:
        value = (metadata or {}).get(key)
        if value in {None, ""}:
            continue
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            continue
    return 0


def _dataset_evidence(*, organization, workspace):
    from model_hub.models.choices import DatasetSourceChoices
    from model_hub.models.develop_dataset import Dataset

    sample_sources = {DatasetSourceChoices.DEMO.value}
    real_sources = [
        choice
        for choice, _label in DatasetSourceChoices.get_choices()
        if choice not in sample_sources
    ]
    datasets = Dataset.no_workspace_objects.filter(
        organization=organization,
        workspace=workspace,
        source__in=real_sources,
    ).order_by("-updated_at", "-created_at")
    dataset = datasets.first()
    sample_count = Dataset.no_workspace_objects.filter(
        organization=organization,
        workspace=workspace,
        source__in=sample_sources,
    ).count()
    return {
        "count": datasets.count(),
        "sample_count": sample_count,
        "source": dataset,
    }


def _trace_source_evidence(*, organization, workspace):
    from tracer.models.project import Project

    projects = (
        Project.no_workspace_objects.filter(
            organization=organization,
            workspace=workspace,
            trace_type="observe",
        )
        .exclude(source="sample")
        .filter(Q(metadata__is_sample__isnull=True) | Q(metadata__is_sample=False))
        .order_by("-updated_at", "-created_at")
    )
    project = projects.first()
    return {
        "count": projects.count(),
        "source": project,
    }


def _scorer_evidence(*, organization, workspace, dataset_ids, project_ids):
    from model_hub.models.evals_metric import UserEvalMetric
    from tracer.models.custom_eval_config import CustomEvalConfig

    dataset_metrics = UserEvalMetric.no_workspace_objects.filter(
        organization=organization,
        workspace=workspace,
        show_in_sidebar=True,
    )
    if dataset_ids:
        dataset_metrics = dataset_metrics.filter(dataset_id__in=dataset_ids)
    else:
        dataset_metrics = dataset_metrics.none()
    dataset_metric = (
        dataset_metrics.select_related("template")
        .order_by("-updated_at", "-created_at")
        .first()
    )

    project_configs = CustomEvalConfig.no_workspace_objects.filter(
        project__organization=organization,
        project__workspace=workspace,
    )
    if project_ids:
        project_configs = project_configs.filter(project_id__in=project_ids)
    project_config = (
        project_configs.select_related("eval_template")
        .order_by("-updated_at", "-created_at")
        .first()
    )

    if dataset_metric:
        template = dataset_metric.template
        return {
            "count": dataset_metrics.count() + project_configs.count(),
            "scorer": dataset_metric,
            "template": template,
            "source": "dataset",
        }
    if project_config:
        return {
            "count": project_configs.count(),
            "scorer": project_config,
            "template": project_config.eval_template,
            "source": "project",
        }
    return {"count": 0, "scorer": None, "template": None, "source": None}


def _group_evidence(*, organization, workspace):
    from model_hub.models.eval_groups import EvalGroup

    groups = EvalGroup.no_workspace_objects.filter(
        organization=organization,
        workspace=workspace,
        is_sample=False,
    ).order_by("-updated_at", "-created_at")
    group = groups.first()
    return {
        "count": groups.count(),
        "group": group,
    }


def _run_evidence(*, organization, workspace, project_ids):
    from model_hub.models.evaluation import Evaluation, StatusChoices
    from tracer.models.eval_task import EvalTask, EvalTaskStatus

    tasks = EvalTask.no_workspace_objects.filter(
        project__organization=organization,
        project__workspace=workspace,
        status__in=[EvalTaskStatus.COMPLETED, EvalTaskStatus.FAILED],
    )
    if project_ids:
        tasks = tasks.filter(project_id__in=project_ids)
    task = tasks.order_by(
        "-end_time", "-last_run", "-updated_at", "-created_at"
    ).first()

    evaluations = Evaluation.no_workspace_objects.filter(
        organization=organization,
        workspace=workspace,
        status__in=[StatusChoices.COMPLETED, StatusChoices.FAILED],
    ).order_by("-updated_at", "-created_at")
    evaluation = evaluations.first()

    event = latest_event(
        organization=organization,
        workspace=workspace,
        event_names=["eval_run_completed"],
        product_path="evals",
        is_sample=False,
    )

    if task:
        failed_spans = task.failed_spans or []
        return {
            "count": tasks.count() + evaluations.count(),
            "run_id": str(task.id),
            "status": task.status,
            "completed_at": task.end_time or task.last_run or task.updated_at,
            "failure_count": len(failed_spans) if isinstance(failed_spans, list) else 0,
            "event": event,
        }
    if evaluation:
        return {
            "count": evaluations.count(),
            "run_id": str(evaluation.id),
            "status": evaluation.status,
            "completed_at": evaluation.updated_at or evaluation.created_at,
            "failure_count": 1 if evaluation.status == StatusChoices.FAILED else 0,
            "event": event,
        }
    if event:
        metadata = event.metadata or {}
        return {
            "count": 1,
            "run_id": (
                metadata.get("run_id")
                or metadata.get("eval_task_id")
                or metadata.get("evaluation_id")
            ),
            "status": metadata.get("status") or "completed",
            "completed_at": event.occurred_at,
            "failure_count": _safe_metadata_int(
                metadata,
                "failure_count",
                "failed_count",
                "failed_spans_count",
            ),
            "event": event,
        }
    return {
        "count": 0,
        "run_id": None,
        "status": None,
        "completed_at": None,
        "failure_count": 0,
        "event": None,
    }


def collect_eval_onboarding_signals(*, user, organization, workspace):
    if not organization or not workspace:
        return EvalOnboardingSignals()

    dataset = _dataset_evidence(organization=organization, workspace=workspace)
    trace_source = _trace_source_evidence(
        organization=organization,
        workspace=workspace,
    )

    dataset_source = dataset["source"]
    trace_project = trace_source["source"]
    source = dataset_source or trace_project
    source_type = None
    if dataset_source:
        source_type = "dataset"
    elif trace_project:
        source_type = "trace_project"

    dataset_ids = []
    if dataset_source:
        dataset_ids.append(dataset_source.id)
    project_ids = []
    if trace_project:
        project_ids.append(trace_project.id)

    scorer = _scorer_evidence(
        organization=organization,
        workspace=workspace,
        dataset_ids=dataset_ids,
        project_ids=project_ids,
    )
    group = _group_evidence(organization=organization, workspace=workspace)
    run = _run_evidence(
        organization=organization,
        workspace=workspace,
        project_ids=project_ids,
    )

    scorer_obj = scorer["scorer"]
    scorer_template = scorer["template"]
    group_obj = group["group"]
    review_event = _latest_eval_event(
        organization=organization,
        workspace=workspace,
        event_names=["eval_failures_reviewed"],
        is_sample=False,
        source_id=getattr(source, "id", None),
        scorer_id=getattr(scorer_obj, "id", None),
        scorer_template_id=getattr(scorer_template, "id", None),
        run_id=run["run_id"],
        eval_group_id=getattr(group_obj, "id", None),
    )
    failure_action_event = _latest_eval_event(
        organization=organization,
        workspace=workspace,
        event_names=["eval_failure_action_created"],
        is_sample=False,
        source_id=getattr(source, "id", None),
        scorer_id=getattr(scorer_obj, "id", None),
        scorer_template_id=getattr(scorer_template, "id", None),
        run_id=run["run_id"],
        eval_group_id=getattr(group_obj, "id", None),
    )
    group_event = _latest_eval_event(
        organization=organization,
        workspace=workspace,
        event_names=["eval_group_created"],
        is_sample=False,
        eval_group_id=getattr(group_obj, "id", None),
    )

    diagnostics = []
    sample_source_count = dataset["sample_count"]
    source_count = dataset["count"] + trace_source["count"]
    if sample_source_count and not source_count:
        diagnostics.append("sample_eval_source_ignored_for_real_activation")
    if run["failure_count"] and review_event and not failure_action_event:
        diagnostics.append("eval_failure_needs_source_action")

    scorer_count = scorer["count"]
    eval_group_count = group["count"] + (1 if group_event and not group_obj else 0)

    return EvalOnboardingSignals(
        source_count=source_count,
        sample_source_count=sample_source_count,
        source_type=source_type,
        source_id=str(source.id) if source else None,
        source_name=getattr(source, "name", None) if source else None,
        scorer_count=scorer_count,
        scorer_id=str(scorer_obj.id) if scorer_obj else None,
        scorer_template_id=str(scorer_template.id) if scorer_template else None,
        scorer_name=getattr(scorer_obj, "name", None) if scorer_obj else None,
        eval_group_count=eval_group_count,
        eval_group_id=str(group_obj.id) if group_obj else None,
        run_count=run["count"],
        run_id=str(run["run_id"]) if run["run_id"] else None,
        run_status=run["status"],
        run_completed_at=run["completed_at"],
        failure_count=run["failure_count"],
        review_event_id=str(review_event.id) if review_event else None,
        reviewed_at=review_event.occurred_at if review_event else None,
        failure_action_event_id=(
            str(failure_action_event.id) if failure_action_event else None
        ),
        failure_action_at=(
            failure_action_event.occurred_at if failure_action_event else None
        ),
        is_sample_only=bool(sample_source_count and not source_count),
        diagnostics=tuple(diagnostics),
    )
