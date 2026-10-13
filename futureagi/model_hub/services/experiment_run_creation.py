"""Create a tenant-authorized run header and pin its execution plan atomically."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Literal
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q

from accounts.models.user import User
from agent_playground.models.graph_version import GraphVersion
from model_hub.models.dataset_versions import DatasetVersion
from model_hub.models.evals_metric import EvalTemplateVersion, UserEvalMetric
from model_hub.models.experiments import ExperimentRun, ExperimentsTable
from model_hub.models.run_prompt import PromptVersion
from model_hub.services.dataset_version_service import JsonObject
from model_hub.services.experiment_run_writes import (
    _RUN_KEY,
    RunWriteError,
    _canonical,
    _digest,
    _identities,
    pin_run_plan,
)
from tfc.routers import force_primary


def _identifier(value: object, name: str) -> UUID:
    if not isinstance(value, UUID):
        raise RunWriteError(f"{name} must be a UUID")
    return value


def _object_copy(value: JsonObject | None, name: str) -> JsonObject:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise RunWriteError(f"{name} must be an object")
    return json.loads(_canonical(value))


def _task_snapshot(
    *,
    task_kind: str,
    prompt_version: PromptVersion | None,
    agent_version: GraphVersion | None,
    organization_id: UUID,
    workspace_id: UUID | None,
    default_workspace: bool,
) -> dict:
    if task_kind == "prompt":
        if prompt_version is None or agent_version is not None:
            raise RunWriteError("prompt tasks require only a prompt version")
        scope = Q(original_template__workspace_id=workspace_id)
        if default_workspace:
            scope |= Q(original_template__workspace__isnull=True)
        version = PromptVersion.no_workspace_objects.filter(
            scope,
            pk=prompt_version.pk,
            deleted=False,
            is_draft=False,
            original_template__deleted=False,
            original_template__organization_id=organization_id,
        ).first()
        if version is None:
            raise RunWriteError("prompt version was not found in this tenant")
        return {
            "prompt_version_id": str(version.id),
            "prompt_config_snapshot": version.prompt_config_snapshot,
            "variable_names": version.variable_names,
        }
    if task_kind == "graph":
        if agent_version is None or prompt_version is not None:
            raise RunWriteError("graph tasks require only a graph version")
        scope = Q(graph__workspace_id=workspace_id)
        if default_workspace:
            scope |= Q(graph__workspace__isnull=True)
        version = GraphVersion.no_workspace_objects.filter(
            scope,
            pk=agent_version.pk,
            deleted=False,
            status__in=("active", "inactive"),
            graph__deleted=False,
            graph__organization_id=organization_id,
        ).first()
        if version is None:
            raise RunWriteError("graph version was not found in this tenant")
        return {"agent_version_id": str(version.id), "graph_id": str(version.graph_id)}
    if (
        task_kind != "external"
        or prompt_version is not None
        or agent_version is not None
    ):
        raise RunWriteError("external tasks cannot reference prompt or graph versions")
    return {}


def _evaluator_snapshots(metric_ids: list[UUID], version: DatasetVersion) -> list[dict]:
    metrics = {
        metric.id: metric
        for metric in UserEvalMetric.no_workspace_objects.select_related(
            "template", "pinned_version"
        ).filter(id__in=metric_ids, dataset_id=version.dataset_id, deleted=False)
    }
    if len(metrics) != len(metric_ids):
        raise RunWriteError("evaluator binding was not found in the version dataset")
    snapshots = []
    for metric_id in metric_ids:
        metric = metrics[metric_id]
        template = metric.template
        if metric.organization_id not in (
            None,
            version.organization_id,
        ) or metric.workspace_id not in (None, version.workspace_id):
            raise RunWriteError("evaluator binding tenant is inconsistent")
        system_template = (
            template.owner == "system"
            and template.organization_id is None
            and template.workspace_id is None
        )
        if template.deleted or (
            not system_template
            and (
                template.organization_id != version.organization_id
                or template.workspace_id not in (None, version.workspace_id)
            )
        ):
            raise RunWriteError("evaluator template was not found in this tenant")
        pin = EvalTemplateVersion.objects.resolve_for_metric(metric)
        if pin is None or pin.deleted or pin.eval_template_id != template.id:
            raise RunWriteError("evaluator has no valid resolved version")
        if pin.organization_id not in (
            None,
            version.organization_id,
        ) or pin.workspace_id not in (None, version.workspace_id):
            raise RunWriteError("evaluator version tenant is inconsistent")
        snapshots.append(
            {
                "metric_id": str(metric.id),
                "evaluator_version_id": str(pin.id),
                "template_id": str(template.id),
                "version_number": pin.version_number,
                "binding_config": metric.config,
                "binding_model": metric.model,
                "prompt_messages": pin.prompt_messages,
                "config_snapshot": pin.config_snapshot,
                "criteria": pin.criteria,
                "model": pin.model,
                "output_type_normalized": pin.output_type_normalized,
                "pass_threshold": pin.pass_threshold,
                "choice_scores": pin.choice_scores,
            }
        )
    return json.loads(_canonical(snapshots))


@force_primary()
@transaction.atomic
def create_experiment_run(
    *,
    run_id: UUID,
    dataset_version: DatasetVersion,
    created_by: User,
    task_kind: Literal["prompt", "graph", "external"],
    expected_items: Sequence[tuple[UUID, int]],
    experiment: ExperimentsTable | None = None,
    prompt_version: PromptVersion | None = None,
    agent_version: GraphVersion | None = None,
    evaluator_metrics: Sequence[UserEvalMetric] = (),
    model_config: JsonObject | None = None,
    metadata: JsonObject | None = None,
) -> ExperimentRun:
    """Create a draft plus exact plan; a matching run UUID replays without repinning.

    The service snapshots current persisted configuration on first creation.
    Replays compare the supplied identities/configuration and preserve the
    recorded versions and existing lifecycle state, even if defaults moved.
    """
    run_id = _identifier(run_id, "run_id")
    if not isinstance(dataset_version, DatasetVersion) or dataset_version._state.adding:
        raise RunWriteError("dataset_version must be persisted")
    if not isinstance(created_by, User) or created_by._state.adding:
        raise RunWriteError("created_by must be persisted")
    if not isinstance(task_kind, str) or task_kind not in {
        "prompt",
        "graph",
        "external",
    }:
        raise RunWriteError("unknown task kind")
    if not isinstance(expected_items, (list, tuple)) or len(expected_items) > 10_000:
        raise RunWriteError("expected_items must contain at most 10000 identities")
    plan = _identities(expected_items)
    if not plan:
        raise RunWriteError("run plan must contain at least one item")
    config = _object_copy(model_config, "model_config")
    run_metadata = _object_copy(metadata, "metadata")
    if _RUN_KEY in run_metadata:
        raise RunWriteError("caller metadata cannot use the reserved run namespace")
    if not isinstance(evaluator_metrics, (list, tuple)):
        raise RunWriteError("evaluator_metrics must be a sequence")
    metric_ids = []
    for metric in evaluator_metrics:
        if not isinstance(metric, UserEvalMetric) or metric._state.adding:
            raise RunWriteError("evaluator bindings must be persisted")
        metric_ids.append(metric.id)
    if len(set(metric_ids)) != len(metric_ids):
        raise RunWriteError("evaluator bindings must be unique")
    metric_ids.sort(key=str)
    version = (
        DatasetVersion.no_workspace_objects.select_for_update(of=("self",))
        .select_related("dataset", "dataset__organization", "dataset__workspace")
        .filter(pk=dataset_version.pk, deleted=False, dataset__deleted=False)
        .first()
    )
    if version is None:
        raise RunWriteError("dataset version was not found")
    dataset = version.dataset
    if (
        version.organization_id != dataset.organization_id
        or version.workspace_id != dataset.workspace_id
    ):
        raise RunWriteError("dataset version tenant is inconsistent")
    actor = User.objects.filter(id=created_by.id, is_active=True).first()
    if actor is None or not actor.can_access_organization(dataset.organization):
        raise RunWriteError("actor cannot access the dataset organization")
    if dataset.workspace_id and not actor.can_write_to_workspace(dataset.workspace):
        raise RunWriteError("actor cannot write to the dataset workspace")
    for name, obj, cls in (
        ("experiment", experiment, ExperimentsTable),
        ("prompt_version", prompt_version, PromptVersion),
        ("agent_version", agent_version, GraphVersion),
    ):
        if obj is not None and (not isinstance(obj, cls) or obj._state.adding):
            raise RunWriteError(f"{name} must be persisted")
    request_hash = _digest(
        {
            "dataset_version_id": str(version.id),
            "created_by_id": str(actor.id),
            "task_kind": task_kind,
            "expected_items": plan,
            "experiment_id": str(experiment.id) if experiment else None,
            "prompt_version_id": str(prompt_version.id) if prompt_version else None,
            "agent_version_id": str(agent_version.id) if agent_version else None,
            "evaluator_metric_ids": [str(value) for value in metric_ids],
            "model_config": config,
            "metadata": run_metadata,
        }
    )
    existing = ExperimentRun.all_objects.select_for_update().filter(pk=run_id).first()
    if existing is not None:
        envelope = (
            existing.metadata.get(_RUN_KEY)
            if isinstance(existing.metadata, dict)
            else None
        )
        creation = envelope.get("creation") if isinstance(envelope, dict) else None
        if (
            existing.deleted
            or existing.dataset_version_id != version.id
            or not isinstance(creation, dict)
            or creation.get("request_hash") != request_hash
        ):
            raise RunWriteError("run UUID is already bound to another creation request")
        return existing
    if experiment is not None:
        experiment = ExperimentsTable.no_workspace_objects.filter(
            pk=experiment.pk, dataset_id=dataset.id, deleted=False
        ).first()
        if experiment is None:
            raise RunWriteError("experiment must belong to the version dataset")
    task_snapshot = json.loads(
        _canonical(
            _task_snapshot(
                task_kind=task_kind,
                prompt_version=prompt_version,
                agent_version=agent_version,
                organization_id=dataset.organization_id,
                workspace_id=dataset.workspace_id,
                default_workspace=bool(
                    dataset.workspace_id and dataset.workspace.is_default
                ),
            )
        )
    )
    evaluator_snapshots = _evaluator_snapshots(metric_ids, version)
    run_metadata[_RUN_KEY] = {
        "creation": {
            "request_hash": request_hash,
            "created_by_id": str(actor.id),
            "task_snapshot": task_snapshot,
        }
    }
    run = ExperimentRun(
        id=run_id,
        dataset_version=version,
        experiment=experiment,
        task_kind=task_kind,
        status=ExperimentRun.Status.DRAFT,
        model_config=config,
        prompt_version_id=prompt_version.id if prompt_version else None,
        agent_version_id=agent_version.id if agent_version else None,
        evaluator_versions=evaluator_snapshots,
        metadata=run_metadata,
    )
    try:
        run.full_clean(validate_unique=False, validate_constraints=False)
    except ValidationError as exc:
        raise RunWriteError(str(exc)) from exc
    try:
        with transaction.atomic():
            run.save(force_insert=True)
    except IntegrityError as exc:
        raise RunWriteError(
            "run UUID is already bound to another creation request"
        ) from exc
    return pin_run_plan(
        run_id=run.id,
        organization_id=dataset.organization_id,
        workspace_id=dataset.workspace_id,
        expected_items=plan,
    )
