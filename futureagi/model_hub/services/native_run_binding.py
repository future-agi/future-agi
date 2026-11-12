"""Creation-time native identity; callers pass an authorized live Dataset.

HTTP callers must resolve it with scoped_dataset_queryset and require workspace
write permission. Worker callers use the admitted dataset, never tenant JSON.
No helper here is called by GET or infers historical example correspondence.
"""

from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

from django.db import transaction

from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import UserEvalMetric
from model_hub.models.experiments import (
    ExperimentAgentConfig,
    ExperimentDatasetTable,
    ExperimentPromptConfig,
    ExperimentsTable,
)
from model_hub.models.native_experiment_runs import (
    NativeExampleBinding,
    NativeExperimentSnapshot,
    NativeRunBinding,
)
from model_hub.services.dataset_version_service import (
    DatasetVersionItemInput,
    create_dataset_version,
)
from model_hub.services.experiment_run_creation import (
    _task_snapshot,
    create_experiment_run,
)
from model_hub.services.experiment_run_writes import RunWriteError, _digest, _uuid
from tfc.routers import force_primary


@dataclass(frozen=True)
class SnapshotIdentity:
    source_row_id: UUID
    snapshot_row_id: UUID
    example_id: UUID
    version_item_id: UUID

    def to_json(self):
        return {key: str(value) for key, value in vars(self).items()}


def live_dataset(dataset, *, lock=False):
    """Revalidate, but never widen, the dataset already authorized by the caller."""
    qs = Dataset.no_workspace_objects.select_related("organization", "workspace")
    if lock:
        qs = qs.select_for_update(of=("self",))
    live = qs.filter(
        id=dataset.pk,
        organization_id=dataset.organization_id,
        workspace_id=dataset.workspace_id,
        deleted=False,
    ).first()
    if live is None:
        raise RunWriteError("dataset was not found in the authorized scope")
    return live


def native_workspace(dataset, workspace_id=None, *, actor=None):
    """Validate scope separately; never relabel a legacy dataset/version tenant."""
    from accounts.models.user import User
    from accounts.models.workspace import Workspace

    workspace_id = workspace_id if workspace_id is not None else dataset.workspace_id
    if workspace_id is None:
        return None
    scope = Workspace.no_workspace_objects.filter(
        pk=_uuid(workspace_id),
        organization_id=dataset.organization_id,
        deleted=False,
        is_active=True,
    ).first()
    if (
        scope is None
        or (dataset.workspace_id and scope.pk != dataset.workspace_id)
        or (dataset.workspace_id is None and not scope.is_default)
    ):
        raise RunWriteError("workspace is not an authorized native dataset scope")
    if actor is not None:
        actor = User.objects.filter(pk=actor.pk, is_active=True).first()
        if (
            actor is None
            or not actor.can_access_organization(dataset.organization)
            or not actor.can_write_to_workspace(scope)
        ):
            raise RunWriteError(
                "actor cannot write to the native authorization workspace"
            )
    return scope


def native_prompt_config(native):
    """Private provider inputs resolved identically at admission and dispatch."""
    from model_hub.services.experiment_config_resolver import resolve_prompt_config

    try:
        resolved = resolve_prompt_config(native)
    except ValueError as exc:
        raise RunWriteError("native prompt configuration is unavailable") from exc
    return {
        key: value
        for key, value in resolved.items()
        if key not in {"name", "model_display_name"}
    }


def snapshot_examples(snapshot):
    """Validate the complete map, including deleted/mixed version references."""
    links = list(
        snapshot.examples.select_related("version_item", "snapshot_row").order_by(
            "row_order", "id"
        )
    )
    if not links or len(links) > 10000:
        raise RunWriteError("native snapshot must contain 1 to 10000 examples")
    for link in links:
        if (
            link.version_item.deleted
            or link.snapshot_row.deleted
            or link.version_item.dataset_version_id != snapshot.dataset_version_id
            or link.snapshot_row.dataset_id != snapshot.snapshot_dataset_id
        ):
            raise RunWriteError("native example binding is unavailable or inconsistent")
    if len({link.version_item.example_id for link in links}) != len(links):
        raise RunWriteError("native snapshot has ambiguous example identities")
    return links


@force_primary()
@transaction.atomic
def persist_snapshot_identity(
    *,
    dataset,
    experiment,
    snapshot_dataset,
    column_mapping,
    row_mapping,
    dataset_version=None,
):
    """Capture copied cells and exact source IDs; never infer identity by order."""
    dataset = live_dataset(dataset, lock=True)
    if experiment.dataset_id != dataset.id or experiment.deleted:
        raise RunWriteError("experiment is not in the snapshot dataset")
    if (
        snapshot_dataset.organization_id != dataset.organization_id
        or snapshot_dataset.workspace_id != dataset.workspace_id
        or snapshot_dataset.deleted
    ):
        raise RunWriteError("snapshot tenant does not match source")
    if NativeExperimentSnapshot.objects.filter(experiment=experiment).exists():
        raise RunWriteError("experiment snapshot identity is already captured")
    if not row_mapping or len(row_mapping) > 10000:
        raise RunWriteError("native snapshot must contain 1 to 10000 rows")
    source_ids = set(row_mapping)
    if Row.no_workspace_objects.filter(
        dataset=dataset, id__in=source_ids, deleted=False
    ).count() != len(source_ids):
        raise RunWriteError("source rows are unavailable")
    rows = {
        row.id: row
        for row in Row.no_workspace_objects.filter(
            dataset=snapshot_dataset,
            id__in=[r.id for r in row_mapping.values()],
            deleted=False,
        )
    }
    if len(rows) != len(row_mapping):
        raise RunWriteError("snapshot rows do not match the source mapping")
    column_map = {str(old): str(new.id) for old, new in column_mapping.items()}
    if Column.no_workspace_objects.filter(
        dataset=snapshot_dataset, id__in=column_map.values(), deleted=False
    ).count() != len(column_map):
        raise RunWriteError("snapshot columns do not match source mapping")
    reverse = {new: old for old, new in column_map.items()}
    values = {row_id: {} for row_id in rows}
    for cell in Cell.no_workspace_objects.filter(
        dataset=snapshot_dataset, row_id__in=rows, column_id__in=reverse, deleted=False
    ):
        values[cell.row_id][reverse[str(cell.column_id)]] = cell.value
    inputs = [
        DatasetVersionItemInput(
            legacy_row_id=source,
            input={"values": values[target.id]},
            metadata=rows[target.id].metadata or {},
        )
        for source, target in row_mapping.items()
    ]
    if dataset_version is None:
        version = create_dataset_version(
            dataset=dataset,
            items=inputs,
            created_by=experiment.user,
            metadata={"capture": "native-snapshot-v1", "columns": column_map},
        )
    else:
        version = DatasetVersion.no_workspace_objects.filter(
            pk=dataset_version.pk,
            dataset=dataset,
            organization_id=dataset.organization_id,
            workspace_id=dataset.workspace_id,
            deleted=False,
        ).first()
        if version is None:
            raise RunWriteError("requested dataset version is unavailable")
    members = list(
        DatasetVersionItem.no_workspace_objects.filter(
            dataset_version=version,
            legacy_row_id__in=source_ids,
            deleted=False,
        )
    )
    by_source = {item.legacy_row_id: item for item in members}
    if len(members) != len(source_ids) or len(by_source) != len(source_ids):
        raise RunWriteError("version does not contain an exact source-example map")
    for item in inputs:
        member = by_source[item.legacy_row_id]
        if (
            member.input != item.input
            or member.expected != item.expected
            or member.metadata != item.metadata
        ):
            raise RunWriteError(
                "requested version does not match captured execution inputs"
            )
    snapshot = NativeExperimentSnapshot.objects.create(
        experiment=experiment,
        snapshot_dataset=snapshot_dataset,
        dataset_version=version,
        column_map=column_map,
    )
    NativeExampleBinding.objects.bulk_create(
        [
            NativeExampleBinding(
                snapshot=snapshot,
                source_row_id=source,
                snapshot_row_id=target.id,
                version_item=by_source[source],
                row_order=rows[target.id].order,
            )
            for source, target in row_mapping.items()
        ]
    )
    # Composition: JSONField gets JSON, never a version identity dataclass.
    identities = [
        SnapshotIdentity(
            source, target.id, by_source[source].example_id, by_source[source].id
        ).to_json()
        for source, target in row_mapping.items()
    ]
    metadata = dict(experiment.run_metadata or {})
    metadata["native_snapshot"] = {
        "dataset_version_id": str(version.id),
        "identities": identities,
    }
    experiment.run_metadata = metadata
    experiment.save(update_fields=["run_metadata"])
    return snapshot


def _settings(requested, effective):
    """Records only demonstrable one-trial native scheduling, not a wider policy."""
    allowed = {"trials", "concurrency"}
    if not isinstance(effective, dict) or set(effective) - allowed:
        raise RunWriteError("effective settings must contain only enforced S1 settings")
    if type(effective.get("trials")) is not int or effective["trials"] != 1:
        raise RunWriteError("S1 native execution supports exactly one enforced trial")
    if "concurrency" in effective and (
        type(effective["concurrency"]) is not int or effective["concurrency"] < 1
    ):
        raise RunWriteError("effective concurrency must be a positive integer")
    requested = {} if requested is None else requested
    if not isinstance(requested, dict) or set(requested) - allowed:
        raise RunWriteError("requested settings contain unsupported S1 fields")
    if any(
        type(value) is not int or effective.get(key) != value
        for key, value in requested.items()
    ):
        raise RunWriteError("requested settings cannot be silently downgraded")
    return dict(requested), dict(effective)


def validate_snapshot(snapshot, dataset, experiment):
    version, copied = snapshot.dataset_version, snapshot.snapshot_dataset
    if (
        version.deleted
        or version.dataset_id != dataset.id
        or version.organization_id != dataset.organization_id
        or version.workspace_id != dataset.workspace_id
        or copied.deleted
        or copied.organization_id != dataset.organization_id
        or copied.workspace_id != dataset.workspace_id
        or experiment.snapshot_dataset_id != copied.id
    ):
        raise RunWriteError("native snapshot/version is unavailable or inconsistent")


def native_evaluator_definition(metric, version):
    """Hash execution-affecting fields, not mutable default/version selectors.

    Private binding/config values are never copied into the public run metadata.
    Older versions may use live fallback fields; changing one then fails closed.
    """
    template = metric.template
    return _digest(
        {
            "version": {
                key: getattr(version, key, None)
                for key in (
                    "config_snapshot",
                    "prompt_messages",
                    "criteria",
                    "model",
                    "output_type_normalized",
                    "pass_threshold",
                    "choice_scores",
                    "error_localizer_enabled",
                    "eval_tags",
                )
            },
            "fallback": {
                key: getattr(template, key, None)
                for key in (
                    "name",
                    "owner",
                    "eval_type",
                    "template_type",
                    "config",
                    "criteria",
                    "choices",
                    "multi_choice",
                    "choice_scores",
                    "output_type_normalized",
                    "pass_threshold",
                    "error_localizer_enabled",
                    "eval_tags",
                )
            },
            "binding": {
                "config": metric.config,
                "model": metric.model,
                "kb_id": str(metric.kb_id) if metric.kb_id else None,
                "error_localizer": metric.error_localizer,
            },
        }
    )


def native_output_sources(snapshot, variant, output, links, *, pinned=None):
    """Resolve actual terminal output receipts at admission, or exact pins later.

    No Cell read, row-order join or worker-side latest-run lookup is permitted.
    Several selected rows may legitimately reference different previous runs.
    """
    from model_hub.models.experiments import ExperimentRunItem
    from model_hub.services.experiment_run_writes import _RUN_KEY

    examples = {
        str(link.version_item.example_id): str(link.snapshot_row_id) for link in links
    }
    candidates = ExperimentRunItem.no_workspace_objects.select_related(
        "run__native_binding"
    ).filter(
        run__native_binding__snapshot=snapshot,
        run__native_binding__variant=variant,
        run__native_binding__output_column=output,
        run__dataset_version_id=snapshot.dataset_version_id,
        run__experiment_id=snapshot.experiment_id,
        run__deleted=False,
        run__completed_at__isnull=False,
        example_id__in=examples,
        trial_number=1,
        status__in=["completed", "failed", "skipped"],
        deleted=False,
    )
    if pinned is not None:
        if set(pinned) != set(examples.values()):
            raise RunWriteError("eval-only source rows do not match the pinned plan")
        candidates = candidates.filter(
            id__in=[ref["item_id"] for ref in pinned.values()]
        )
    result = {}
    for item in candidates.order_by("example_id", "-run__ordinal").iterator(
        chunk_size=256
    ):
        row_id = examples[str(item.example_id)]
        if row_id in result:
            continue
        identity = (item.run.metadata or {}).get("native_identity", {})
        receipt = (item.metadata or {}).get(_RUN_KEY, {}).get("result_receipt", {})
        if (
            not receipt.get("payload_hash")
            or _digest(identity) != item.run.native_binding.request_hash
            or item.input
            != next(
                link.version_item.input
                for link in links
                if str(link.snapshot_row_id) == row_id
            )
            or item.expected
            != next(
                link.version_item.expected
                for link in links
                if str(link.snapshot_row_id) == row_id
            )
        ):
            raise RunWriteError("eval-only output has no immutable receipt identity")
        ref = {
            "run_id": str(item.run_id),
            "item_id": str(item.id),
            "receipt_hash": receipt["payload_hash"],
            "status": item.status,
            "output_hash": _digest({"output": item.output, "error": item.error}),
            "native_config_hash": identity.get("native_config_hash"),
        }
        if pinned is not None and ref != pinned[row_id]:
            raise RunWriteError("eval-only output receipt changed after admission")
        result[row_id] = ref
        if len(result) == len(examples):
            break
    if set(result) != set(examples.values()):
        raise RunWriteError(
            "eval-only requires a terminal canonical output for every selected row"
        )
    return result


def native_evaluator_pins(
    metrics,
    dataset,
    snapshot,
    *,
    pinned=None,
    workspace=None,
    check_definition=False,
    retired_ok=False,
):
    """Validate native snapshot membership and tenant pins, including on replay.

    retired_ok is for reads of an existing pin only: a soft-deleted metric,
    template or pinned version stays readable (retired); every scope and
    membership check still applies.
    """
    from model_hub.models.evals_metric import EvalTemplateVersion

    scope = native_workspace(dataset, workspace.pk if workspace else None)
    workspace_id = scope.pk if scope else None
    previous = (
        {entry["metric_id"]: entry for entry in pinned} if pinned is not None else {}
    )
    if pinned is not None and set(previous) != {str(metric.id) for metric in metrics}:
        raise RunWriteError("native evaluator membership changed")
    result = []
    for metric in metrics:
        if (
            (metric.deleted and not retired_ok)
            or metric.dataset_id not in (dataset.id, snapshot.snapshot_dataset_id)
            or metric.organization_id not in (None, dataset.organization_id)
            or metric.workspace_id not in (None, workspace_id)
            or (
                metric.dataset_id == snapshot.snapshot_dataset_id
                and metric.source_id != str(snapshot.experiment_id)
            )
        ):
            raise RunWriteError("native evaluator is not in this experiment snapshot")
        template = metric.template
        system = (
            template.owner == "system"
            and template.organization_id is None
            and template.workspace_id is None
        )
        if (template.deleted and not retired_ok) or (
            not system
            and (
                template.organization_id != dataset.organization_id
                or template.workspace_id not in (None, workspace_id)
            )
        ):
            raise RunWriteError(
                "native evaluator template is unavailable in this tenant"
            )
        versions = (
            EvalTemplateVersion._base_manager.all()
            if retired_ok
            else EvalTemplateVersion.no_workspace_objects.filter(deleted=False)
        )
        pin = (
            versions.filter(
                pk=previous[str(metric.id)]["evaluator_version_id"],
            ).first()
            if pinned is not None
            else EvalTemplateVersion.objects.resolve_for_metric(metric)
        )
        if (
            pin is None
            or (pin.deleted and not retired_ok)
            or pin.eval_template_id != template.id
            or pin.organization_id not in (None, dataset.organization_id)
            or pin.workspace_id not in (None, workspace_id)
        ):
            raise RunWriteError(
                "native evaluator version is unavailable in this tenant"
            )
        definition_hash = native_evaluator_definition(metric, pin)
        if pinned is not None and check_definition:
            saved = previous[str(metric.id)]
            if (
                saved.get("binding_config") != {"sha256": _digest(metric.config)}
                or saved.get("binding_model") != {"sha256": _digest(metric.model)}
                or (
                    saved.get("definition_hash") is not None
                    and saved["definition_hash"] != definition_hash
                )
            ):
                raise RunWriteError(
                    "native evaluator definition changed after admission"
                )
        result.append(
            previous[str(metric.id)]
            if pinned is not None
            else {
                "definition_hash": definition_hash,
                "metric_id": str(metric.id),
                "evaluator_version_id": str(pin.id),
                "template_id": str(template.id),
                "version_number": pin.version_number,
                "binding_config": {"sha256": _digest(metric.config)},
                "binding_model": {"sha256": _digest(metric.model)},
                "output_type_normalized": pin.output_type_normalized,
                "pass_threshold": pin.pass_threshold,
                "choice_scores": pin.choice_scores,
                # Decided once, at admission: the recorded version is the metric's
                # own live pin (it runs as a version), not a default resolved for
                # an unpinned metric or a deleted pin (the live template runs).
                "explicit_pin": bool(
                    metric.pinned_version_id is not None
                    and str(metric.pinned_version_id) == str(pin.id)
                    and not pin.deleted
                ),
            }
        )
    return result


def validate_run_references(binding, run, dataset, *, retired_ok=False):
    """Recheck live target/evaluator references without re-resolving version pins.

    Readers pass retired_ok=True so a soft-deleted evaluator's pin stays
    readable; writers (recording) keep the default and still need a live one.
    """
    from agent_playground.models.graph_version import GraphVersion
    from model_hub.models.evals_metric import UserEvalMetric
    from model_hub.models.run_prompt import PromptVersion

    payload = (run.metadata or {}).get("native_identity", {})
    if not isinstance(payload, dict) or _digest(payload) != binding.request_hash:
        raise RunWriteError("native authorization evidence is inconsistent")
    scope = native_workspace(dataset, payload.get("authorization_workspace_id"))
    cls = ExperimentPromptConfig if run.task_kind == "prompt" else ExperimentAgentConfig
    if not cls.no_workspace_objects.filter(
        pk=binding.native_config_id,
        experiment_dataset_id=binding.variant_id,
        deleted=False,
    ).exists():
        raise RunWriteError("native configuration is unavailable")
    prompt = (
        PromptVersion.no_workspace_objects.filter(
            pk=run.prompt_version_id, deleted=False
        ).first()
        if run.prompt_version_id
        else None
    )
    agent = (
        GraphVersion.no_workspace_objects.filter(
            pk=run.agent_version_id, deleted=False
        ).first()
        if run.agent_version_id
        else None
    )
    _task_snapshot(
        task_kind=run.task_kind,
        prompt_version=prompt,
        agent_version=agent,
        organization_id=dataset.organization_id,
        workspace_id=scope.pk if scope else None,
        default_workspace=bool(scope and scope.is_default),
    )
    metric_ids = [pin["metric_id"] for pin in run.evaluator_versions]
    source = (
        UserEvalMetric._base_manager.all()
        if retired_ok
        else UserEvalMetric.no_workspace_objects.filter(deleted=False)
    )
    metrics = list(source.select_related("template").filter(id__in=metric_ids))
    native_evaluator_pins(
        metrics,
        dataset,
        binding.snapshot,
        pinned=run.evaluator_versions,
        workspace=scope,
        retired_ok=retired_ok,
    )


@force_primary()
@transaction.atomic
def bind_native_run(
    *,
    dataset,
    experiment,
    variant,
    execution_id,
    output_column,
    created_by,
    effective_settings,
    requested_settings=None,
    authorized_workspace=None,
    snapshot_row_ids=None,
    evaluator_metric_ids=None,
    eval_only=False,
    evaluator_rows=None,
    graph_plan=None,
):
    """Return a draft canonical run. It queues ONLY after confirmed admission.

    execution_id must be persisted in admission intent and reused on retries;
    a genuinely new execution receives a new UUID. Variant/config/output IDs
    are never accepted as the canonical run ID.
    """
    from model_hub.services.native_run_projection import sanitize_config

    dataset = live_dataset(dataset, lock=True)
    if authorized_workspace is not None and not getattr(
        authorized_workspace, "pk", None
    ):
        raise RunWriteError("authorized workspace must be persisted")
    scope = native_workspace(
        dataset, getattr(authorized_workspace, "pk", None), actor=created_by
    )
    experiment = (
        ExperimentsTable.no_workspace_objects.select_for_update()
        .filter(
            id=experiment.pk,
            dataset=dataset,
            deleted=False,
        )
        .first()
    )
    if experiment is None:
        raise RunWriteError("experiment was not found in the authorized dataset")
    snapshot = (
        NativeExperimentSnapshot.objects.select_related(
            "dataset_version",
            "snapshot_dataset",
        )
        .filter(experiment=experiment)
        .first()
    )
    if snapshot is None:
        raise RunWriteError("native identity was not recorded at snapshot creation")
    validate_snapshot(snapshot, dataset, experiment)
    links = snapshot_examples(snapshot)
    # Validate the complete immutable map first; an explicit subset only narrows
    # which of its examples this run expects, never what is validated.
    selected_rows = None
    if snapshot_row_ids is not None:
        if not isinstance(snapshot_row_ids, (list, tuple)) or not snapshot_row_ids:
            raise RunWriteError("native explicit row subset must be nonempty")
        selected_rows = sorted(str(UUID(_uuid(value))) for value in snapshot_row_ids)
        chosen = set(selected_rows)
        if len(chosen) != len(selected_rows):
            raise RunWriteError("native explicit row subset contains duplicate rows")
        if chosen - {str(link.snapshot_row_id) for link in links}:
            raise RunWriteError(
                "native explicit row subset is outside the immutable snapshot"
            )
        links = [link for link in links if str(link.snapshot_row_id) in chosen]
    variant = ExperimentDatasetTable.no_workspace_objects.filter(
        id=variant.pk,
        experiment=experiment,
        deleted=False,
    ).first()
    if variant is None:
        raise RunWriteError("variant was not found in the experiment")
    output = Column.no_workspace_objects.filter(
        id=output_column.pk,
        dataset_id=snapshot.snapshot_dataset_id,
        experiments_dataset_column=variant,
        deleted=False,
    ).first()
    if output is None or str(output.id) in snapshot.column_map.values():
        raise RunWriteError("output column must belong to this variant and snapshot")
    prompts = list(
        ExperimentPromptConfig.no_workspace_objects.select_related(
            "prompt_version",
            "prompt_template",
        ).filter(experiment_dataset=variant, deleted=False)
    )
    agents = list(
        ExperimentAgentConfig.no_workspace_objects.select_related(
            "graph_version",
            "graph",
        ).filter(experiment_dataset=variant, deleted=False)
    )
    if len(prompts) + len(agents) != 1:
        raise RunWriteError("variant must have exactly one live prompt or graph config")
    native = (prompts or agents)[0]
    task_kind = "prompt" if prompts else "graph"
    if prompts and (
        native.prompt_version_id is None
        or native.prompt_template_id != native.prompt_version.original_template_id
    ):
        raise RunWriteError("prompt variant requires its immutable prompt version")
    if agents and native.graph_version.graph_id != native.graph_id:
        raise RunWriteError("graph variant version does not belong to its graph")
    _task_snapshot(
        task_kind=task_kind,
        prompt_version=native.prompt_version if prompts else None,
        agent_version=native.graph_version if agents else None,
        organization_id=dataset.organization_id,
        workspace_id=scope.pk if scope else None,
        default_workspace=bool(scope and scope.is_default),
    )
    if graph_plan is not None:
        from model_hub.services.experiment_submit import native_graph_plan

        if (
            not agents
            or eval_only
            or graph_plan != native_graph_plan(native, snapshot, workspace=scope)
            or str(output.id) not in graph_plan["terminal_column_ids"]
        ):
            raise RunWriteError(
                "native graph definition/output plan does not match the admitted target"
            )
    requested, effective = _settings(requested_settings, effective_settings)
    resolved = native_prompt_config(native) if prompts else None
    raw_config = (
        {
            "model": {
                "name": resolved["model"],
                "params": {**resolved["model_config"], **resolved["model_params"]},
            },
            "tools": resolved["configuration"].get("tools", []),
        }
        if prompts
        else {
            "agent": {
                "id": str(native.graph_id),
                "version_id": str(native.graph_version_id),
            }
        }
    )
    native_config_hash = _digest(
        {"prompt_version_id": str(native.prompt_version_id), "effective": resolved}
        if prompts
        else {
            "graph_id": str(native.graph_id),
            "version_id": str(native.graph_version_id),
        }
    )
    config = sanitize_config(raw_config)
    execution_id = UUID(_uuid(execution_id))
    run_id = uuid5(NAMESPACE_URL, f"futureagi:native-run:{variant.id}:{execution_id}")
    # A deleted evaluation stays in the experiment's membership table; a new
    # run evaluates only the live ones, and refuses in plain words when none is left.
    members = experiment.user_eval_template_ids.through.objects.filter(
        experimentstable_id=experiment.pk,
    )
    metric_ids = list(
        members.filter(userevalmetric__deleted=False).values_list(
            "userevalmetric_id", flat=True
        )
    )
    if evaluator_metric_ids is None and not metric_ids and members.exists():
        raise RunWriteError(
            "Every evaluation on this experiment was deleted. "
            "Add an evaluation, then run the experiment again."
        )
    if evaluator_metric_ids is not None:
        selected_metrics = sorted({_uuid(value) for value in evaluator_metric_ids})
        if not selected_metrics or set(selected_metrics) - {
            str(value) for value in metric_ids
        }:
            raise RunWriteError(
                "native evaluator selectors are outside this experiment"
            )
        metric_ids = selected_metrics
    metrics = list(
        UserEvalMetric.no_workspace_objects.filter(id__in=metric_ids).order_by("id")
    )
    if len(metrics) != len(metric_ids):
        raise RunWriteError("native evaluator membership is unavailable")
    evaluation_sources = {}
    if evaluator_rows is not None:
        if (
            not eval_only
            or not isinstance(evaluator_rows, dict)
            or set(evaluator_rows) != {str(link.snapshot_row_id) for link in links}
            or any(
                not isinstance(ids, list)
                or not ids
                or len(ids) != len(set(ids))
                or set(ids) - {str(metric.id) for metric in metrics}
                for ids in evaluator_rows.values()
            )
        ):
            raise RunWriteError(
                "native per-row evaluator selection is outside the pinned plan"
            )
        evaluator_rows = {
            row: sorted(ids) for row, ids in sorted(evaluator_rows.items())
        }
    if eval_only:
        if not metrics:
            raise RunWriteError("eval-only requires at least one selected evaluator")
        previous_binding = (
            NativeRunBinding.objects.select_related("run")
            .filter(variant=variant, execution_id=execution_id)
            .first()
        )
        previous_sources = (
            previous_binding.run.metadata.get("native_identity", {}).get(
                "evaluation_sources"
            )
            if previous_binding
            else None
        )
        evaluation_sources = native_output_sources(
            snapshot, variant, output, links, pinned=previous_sources
        )
        if any(
            ref["native_config_hash"] != native_config_hash
            for ref in evaluation_sources.values()
        ):
            raise RunWriteError(
                "eval-only output was produced by a different prompt configuration"
            )
    payload = {
        **(
            {"execution_mode": "graph", "graph_plan": graph_plan}
            if graph_plan is not None
            else {}
        ),
        **(
            {"execution_mode": "eval_only", "evaluation_sources": evaluation_sources}
            if eval_only
            else {}
        ),
        **({"evaluator_rows": evaluator_rows} if evaluator_rows is not None else {}),
        "variant_id": str(variant.id),
        "native_config_id": str(native.id),
        "output_column_id": str(output.id),
        "execution_id": str(execution_id),
        "output_column_name": output.name,
        "output_column_type": output.data_type,
        "snapshot_id": str(snapshot.id),
        "requested_settings": requested,
        "effective_settings": effective,
        "config": config,
        "native_config_hash": native_config_hash,
        "authorization_workspace_id": str(scope.pk) if scope else None,
        "evaluator_metric_ids": [str(metric.id) for metric in metrics],
        "evaluator_config_hash": _digest(
            [
                {"id": str(metric.id), "config": metric.config, "model": metric.model}
                for metric in metrics
            ]
        ),
        "policy_hash": _digest(experiment.run_policy or {}),
        **({"snapshot_row_ids": selected_rows} if selected_rows is not None else {}),
    }
    digest = _digest(payload)
    existing = (
        NativeRunBinding.objects.select_for_update()
        .filter(
            variant=variant,
            execution_id=execution_id,
        )
        .first()
    )
    if existing and existing.request_hash != digest:
        raise RunWriteError("native execution is already bound to a different request")
    pins = native_evaluator_pins(
        metrics,
        dataset,
        snapshot,
        pinned=existing.run.evaluator_versions if existing else None,
        workspace=scope,
    )
    run = create_experiment_run(
        run_id=run_id,
        dataset_version=snapshot.dataset_version,
        created_by=created_by,
        task_kind=task_kind,
        experiment=experiment,
        expected_items=[(link.version_item.example_id, 1) for link in links],
        prompt_version=native.prompt_version if prompts else None,
        agent_version=native.graph_version if agents else None,
        evaluator_metrics=(),
        model_config=config,
        metadata={"name": native.name, "native_identity": payload},
        **(
            {"authorized_workspace": scope}
            if dataset.workspace_id is None and scope
            else {}
        ),
    )
    if existing:
        if existing.run_id != run.id:
            raise RunWriteError("canonical run identity is inconsistent")
        return run
    # This is still the same creation transaction, before any dispatch or item.
    # The existing external helper cannot accept snapshot-scoped native metrics.
    run.evaluator_versions = pins
    run.full_clean()
    run.save(update_fields=["evaluator_versions", "updated_at"])
    NativeRunBinding.objects.create(
        run=run,
        snapshot=snapshot,
        variant=variant,
        execution_id=execution_id,
        native_config_id=native.id,
        output_column=output,
        requested_settings=requested,
        effective_settings=effective,
        config=config,
        request_hash=digest,
    )
    return run
