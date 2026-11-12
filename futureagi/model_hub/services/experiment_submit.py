"""Commit experiment submission intent before admitting retryable execution."""

import hashlib
from copy import deepcopy
import json
import uuid
from dataclasses import dataclass
from functools import wraps

import structlog
from django.db import transaction
from rest_framework.response import Response

from model_hub.models.choices import StatusType
from model_hub.models.develop_dataset import Dataset
from model_hub.models.experiments import ExperimentsTable
from model_hub.services import experiment_target_admission as target_admission

STATE_KEY = "_experiment_v2_submission"
ADMISSIONS_KEY = "_experiment_v2_admissions"
logger = structlog.get_logger(__name__)


@dataclass
class Submission:
    experiment_id: str
    response: Response
    admission_id: str | None = None


def request_hash(data, operation):
    canonical = json.dumps(
        [operation, data], sort_keys=True, default=str, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def state_of(experiment):
    if not experiment.snapshot_dataset_id:
        return {}
    config = (
        Dataset.no_workspace_objects.filter(id=experiment.snapshot_dataset_id)
        .values_list("dataset_config", flat=True)
        .first()
    )
    return (config or {}).get(STATE_KEY, {})


def _admission_records(config):
    """Durable history is independent of the mutable latest-command cache."""
    records = deepcopy((config or {}).get(ADMISSIONS_KEY, {}))
    latest = (config or {}).get(STATE_KEY, {})
    if latest.get("admission_id"):
        records.setdefault(latest["admission_id"], deepcopy(latest))
    return records


def _remember_submission(config, state):
    """Insert immutable intent once; only its acknowledgement can advance.

    A latest-cache invalidation deliberately removes request_hash. It must not
    erase the original intent when a later PUT/append archives that cache.
    """
    if state.get("admission_id"):
        records = _admission_records(config)
        stored = records.setdefault(state["admission_id"], deepcopy(state))
        if stored.get("state") == "pending" and state.get("state") == "accepted":
            stored.update(state="accepted", workflow_id=state["workflow_id"])
        if stored.get("state") == "pending" and state.get("state") == "cancelled":
            stored.update(state="cancelled", cancelled_pending=True,
                          cancellation_reason="stopped_before_acknowledgement")
        config[ADMISSIONS_KEY] = records


def _peek_record(config, admission_id):
    """The journal entry, else the latest cache when it is that admission. No copy."""
    config = config or {}
    record = (config.get(ADMISSIONS_KEY) or {}).get(str(admission_id))
    if record is None:
        latest = config.get(STATE_KEY) or {}
        record = latest if latest.get("admission_id") == str(admission_id) else {}
    return record


def _submission_record(config, admission_id=None):
    if admission_id is None:
        return deepcopy((config or {}).get(STATE_KEY, {}))
    return deepcopy(_peek_record(config, admission_id))


def matching_submission(experiment, data, operation):
    """Recognize retries and refuse edits over unresolved or running work."""
    state = state_of(experiment)
    if state.get("request_hash") == request_hash(data, operation):
        return True
    if state.get("state") == "pending":
        raise ValueError(
            "An earlier submission is unconfirmed. Retry that submission before editing."
        )
    if experiment.status in (StatusType.QUEUED.value, StatusType.RUNNING.value):
        raise ValueError(
            "Wait for the current execution to finish before changing its configuration."
        )
    return False


def ensure_native_idle(experiment):
    """Never mutate shared configs/cells underneath an admitted immutable run."""
    from model_hub.models.native_experiment_runs import NativeRunBinding

    if NativeRunBinding.objects.filter(
        snapshot__experiment=experiment, run__deleted=False,
        run__completed_at__isnull=True,
    ).exists():
        raise ValueError("Wait for or cancel the bound execution before changing its configuration.")


def configuration_membership(experiment):
    """Non-dispatch edits are removals or clearing the base target, not execution."""
    from model_hub.models.experiments import ExperimentPromptConfig, ExperimentAgentConfig

    configs = [tuple(cls.no_workspace_objects.filter(
        experiment_dataset__experiment=experiment, experiment_dataset__deleted=False,
        deleted=False,
    ).order_by("id").values_list("id", flat=True))
        for cls in (ExperimentPromptConfig, ExperimentAgentConfig)]
    return (experiment.column_id, *configs, tuple(experiment.user_eval_template_ids.through.objects.filter(
        experimentstable_id=experiment.pk,
    ).order_by("userevalmetric_id").values_list("userevalmetric_id", flat=True)))


def invalidate_accepted_replay(experiment):
    """A successful non-dispatch edit invalidates replay, never pending recovery."""
    snapshot = Dataset.no_workspace_objects.select_for_update().get(pk=experiment.snapshot_dataset_id)
    config = dict(snapshot.dataset_config or {})
    state = dict(config.get(STATE_KEY, {}))
    if state.get("state") == "pending":
        raise ValueError("Retry the unconfirmed submission before editing its configuration.")
    if "request_hash" in state:
        _remember_submission(config, state)
        state.pop("request_hash")
        config[STATE_KEY] = state
        snapshot.dataset_config = config
        snapshot.save(update_fields=["dataset_config"])


def stage_rerun_batch(experiments, data, workspace, execution_key=None):
    """Resolve a cohort from durable admissions, never members' latest slots.

    The caller locks and authorizes the complete requested set. A completed
    member can be edited or run again without replacing this cohort's receipt.
    Return exact admission IDs so admission cannot accidentally dispatch a
    sibling's newer PUT/append intent after the transaction commits.
    """
    execution_key = validate_execution_key(execution_key)
    digest = request_hash(data, "rerun")
    members = sorted(str(experiment.id) for experiment in experiments)
    histories, states, recoverable = {}, {}, set()
    for experiment in experiments:
        config = Dataset.no_workspace_objects.select_for_update().get(
            pk=experiment.snapshot_dataset_id,
        ).dataset_config or {}
        history = _admission_records(config)
        latest = config.get(STATE_KEY, {})
        histories[str(experiment.id)], states[str(experiment.id)] = history, latest
        for record in history.values():
            active = (latest.get("admission_id") == record.get("admission_id") and
                      experiment.status in (StatusType.QUEUED.value, StatusType.RUNNING.value))
            if (record.get("request_hash") == digest and record.get("batch_id") and
                    (record.get("state") == "pending" or active)):
                recoverable.add(record["batch_id"])
    if execution_key is not None:
        # An explicit key names a durable cohort even after terminal completion.
        # Only the complete set authorized/locked by the caller is searched;
        # journal member IDs never grant authorization to another experiment.
        keyed = {member: [record for record in histories[member].values()
                          if record.get("execution_key") == execution_key]
                 for member in members}
        if any(keyed.values()):
            if any(len(records) != 1 for records in keyed.values()):
                raise ExecutionKeyConflict("Idempotency-Key already names a different execution cohort.")
            records = [keyed[member][0] for member in members]
            batches = {record.get("batch_id") for record in records}
            if (len(batches) != 1 or None in batches or any(
                    record.get("request_hash") != digest or record.get("batch_members") != members
                    or record.get("state") not in {"pending", "accepted", "cancelled"}
                    for record in records)):
                raise ExecutionKeyConflict("Idempotency-Key already names a different execution request.")
            return {member: keyed[member][0]["admission_id"] for member in members}
        # A new explicit key is not aliased to an older same-body request.
        # Existing pending/running checks still prevent overlapping work.
        recoverable.clear()
    if recoverable:
        if len(recoverable) != 1:
            raise ValueError("The requested batch has ambiguous admission history.")
        batch_id = recoverable.pop()
        admissions = {}
        for member in members:
            records = [record for record in histories[member].values()
                       if record.get("batch_id") == batch_id and record.get("request_hash") == digest
                       and record.get("state") in {"pending", "accepted", "cancelled"}
                       and record.get("batch_members", members) == members]
            if len(records) != 1:
                raise ValueError("Retry the original active batch before starting another execution.")
            admissions[member] = records[0]["admission_id"]
        return admissions
    if any(states[str(experiment.id)].get("state") == "pending" or experiment.status in (
        StatusType.QUEUED.value, StatusType.RUNNING.value,
    ) for experiment in experiments):
        raise ValueError("Retry the original active batch before starting another execution.")
    batch_id = str(uuid.uuid4())
    admissions = {}
    for experiment in experiments:
        stage_submission(
            experiment, data, "rerun", authorized_workspace=workspace, batch_id=batch_id,
            batch_members=members, execution_key=execution_key,
            max_concurrent_rows=data["max_concurrent_rows"],
        )
        admissions[str(experiment.id)] = state_of(experiment)["admission_id"]
    return admissions


def native_graph_plan(native, snapshot, *, workspace=None):
    """Pin the authorized graph closure and every node/output relationship.

    Topology serialization omits node definitions. Hash config, prompt and port
    schemas as well; private values never enter public run metadata. Runtime
    integration must validate this pin before each effect, not just allocation.
    """
    from django.core.exceptions import ValidationError
    from django.db.models import Q
    from agent_playground.models import GraphVersion, Node
    from agent_playground.models.prompt_template_node import PromptTemplateNode
    from agent_playground.services.engine import GraphAnalyzer
    from model_hub.models.choices import SourceChoices
    from model_hub.services.experiment_run_creation import _task_snapshot
    from model_hub.services.experiment_run_writes import RunWriteError, _digest
    from model_hub.services.native_run_binding import native_workspace

    dataset = snapshot.experiment.dataset
    scope = native_workspace(dataset, getattr(workspace, "pk", None))
    definitions, topologies, visiting = {}, {}, set()

    def fields(instance):
        return {field.attname: field.value_from_object(instance)
                for field in instance._meta.concrete_fields
                if field.name not in {"created_at", "updated_at", "position"}}

    def visit(version_id):
        key = str(version_id)
        if key in visiting:
            raise RunWriteError("native graph definition contains a recursive subgraph")
        if key in definitions:
            return
        if len(definitions) + len(visiting) >= 100:
            raise RunWriteError("native graph definition closure exceeds its bound")
        visiting.add(key)
        version = GraphVersion.no_workspace_objects.select_related("graph").get(pk=version_id, deleted=False)
        _task_snapshot(task_kind="graph", prompt_version=None, agent_version=version,
                       organization_id=dataset.organization_id, workspace_id=scope.pk if scope else None,
                       default_workspace=bool(scope and scope.is_default))
        topology = GraphAnalyzer.analyze(version.id)
        if not topology.nodes or len(topology.nodes) > 1000:
            raise RunWriteError("native graph requires 1 to 1000 nodes per version")
        nodes = sorted(topology.nodes.values(), key=lambda node: str(node.id))
        prompts = list(PromptTemplateNode.no_workspace_objects.select_related("prompt_version").filter(
            node_id__in=[node.id for node in nodes], deleted=False).order_by("id"))
        for link in prompts:
            if link.prompt_version.deleted or link.prompt_template_id != link.prompt_version.original_template_id:
                raise RunWriteError("native graph prompt definition is unavailable")
            _task_snapshot(task_kind="prompt", prompt_version=link.prompt_version, agent_version=None,
                           organization_id=dataset.organization_id, workspace_id=scope.pk if scope else None,
                           default_workspace=bool(scope and scope.is_default))
        definition = {"version_id": key,
                      "nodes": [fields(node) for node in nodes],
                      "ports": [fields(port) for port in sorted(topology.ports.values(), key=lambda port: str(port.id))],
                      "edges": [fields(edge) for edge in sorted(topology.edges, key=lambda edge: str(edge.id))],
                      "templates": [fields(node.node_template) for node in nodes if node.node_template_id],
                      "prompts": [{"binding": fields(link), "version": fields(link.prompt_version)} for link in prompts]}
        for node in nodes:
            if node.ref_graph_version_id:
                visit(node.ref_graph_version_id)
        definitions[key] = _digest(json.loads(json.dumps(definition, sort_keys=True, default=str)))
        topologies[key] = topology
        visiting.remove(key)

    visit(native.graph_version_id)
    topology = topologies[str(native.graph_version_id)]
    emitting = {str(node.id) for node in topology.nodes.values()
                if node.ref_graph_version_id or (node.node_template_id and
                    node.node_template.name in {"llm_prompt", "deterministic_text"})}
    columns = list(native.experiment_dataset.columns.filter(
        dataset_id=snapshot.snapshot_dataset_id, source=SourceChoices.EXPERIMENT.value, deleted=False))
    # Classic version updates retain prior output columns and their EDT links.
    # Exclude only proven older-version nodes in this tenant; unknown/foreign
    # identities still reach the strict checks below, as do current non-emitters.
    column_nodes = [str((column.metadata or {}).get("node_id")) for column in columns]
    node_scope = Q(graph_version__graph__workspace_id=scope.pk if scope else None)
    if scope and scope.is_default:
        node_scope |= Q(graph_version__graph__workspace__isnull=True)
    try:
        node_versions = {str(pk): str(version) for pk, version in Node.no_workspace_objects.filter(
            node_scope, pk__in=column_nodes, graph_version__graph__organization_id=dataset.organization_id,
        ).values_list("pk", "graph_version_id")}
    except (ValueError, TypeError, ValidationError) as error:
        raise RunWriteError("native graph output columns have invalid node identities") from error
    columns = [column for column, node_id in zip(columns, column_nodes)
               if node_versions.get(node_id, str(native.graph_version_id)) == str(native.graph_version_id)]
    mapping = {}
    for column in columns:
        node_id = str((column.metadata or {}).get("node_id"))
        if node_id not in emitting or node_id in mapping:
            raise RunWriteError("native graph output columns do not have unique node identities")
        mapping[node_id] = str(column.id)
    if not emitting or set(mapping) != emitting:
        raise RunWriteError("native graph output membership is incomplete")
    # Last emitting nodes on EVERY branch, never an arbitrary last column.
    adjacency = {str(key): {str(value) for value in values} for key, values in topology.adjacency.items()}
    terminals = []
    for node_id in sorted(emitting):
        pending, seen = list(adjacency.get(node_id, ())), set()
        while pending:
            child = pending.pop()
            if child not in seen:
                seen.add(child)
                pending.extend(adjacency.get(child, ()))
        if not (seen & emitting):
            terminals.append(mapping[node_id])
    if not terminals:
        raise RunWriteError("native graph has no terminal output columns")
    return {"graph_version_id": str(native.graph_version_id),
            "definition_hashes": dict(sorted(definitions.items())),
            "node_ids": sorted(str(value) for value in topology.nodes),
            "node_column_mapping": dict(sorted(mapping.items())),
            "output_columns": [{"id": str(column.id), "node_id": str(column.metadata["node_id"]),
                                "name": column.name, "data_type": column.data_type}
                               for column in sorted(columns, key=lambda value: str(value.id))],
            "terminal_column_ids": sorted(terminals)}


def stage_native_graph_runs(experiment, execution_id, workflow_args, workspace, *, config_ids=None,
                            subset_rows=None):
    """Bind authorized graph rows to the installed canonical execution recorder."""
    from model_hub.models.develop_dataset import Column
    from model_hub.models.experiments import ExperimentAgentConfig
    from model_hub.models.native_experiment_runs import NativeExperimentSnapshot
    from model_hub.services.native_run_binding import bind_native_run, validate_snapshot
    from model_hub.services.experiment_run_writes import RunWriteError

    snapshot = NativeExperimentSnapshot.objects.select_related(
        "dataset_version", "snapshot_dataset", "experiment__dataset").get(experiment=experiment)
    validate_snapshot(snapshot, experiment.dataset, experiment)
    agents = list(ExperimentAgentConfig.no_workspace_objects.select_related("experiment_dataset", "graph_version").filter(
        experiment_dataset__experiment=experiment, experiment_dataset__deleted=False, deleted=False))
    if config_ids is not None:
        chosen = {str(value) for value in config_ids}
        agents = [agent for agent in agents if str(agent.id) in chosen]
        if not chosen or {str(agent.id) for agent in agents} != chosen:
            raise RunWriteError("native graph selectors do not match live graph variants")
    copied = {str(column.id): column for column in Column.no_workspace_objects.filter(
        dataset_id=snapshot.snapshot_dataset_id, id__in=snapshot.column_map.values(), deleted=False)}
    if set(copied) != set(snapshot.column_map.values()):
        raise RunWriteError("native graph input columns are unavailable")
    columns = [{"source_id": old, "snapshot_id": new, "name": copied[new].name,
                "data_type": copied[new].data_type} for old, new in sorted(snapshot.column_map.items())]
    if len({column["name"] for column in columns}) != len(columns):
        raise RunWriteError("native graph input column names are ambiguous")
    contexts = {}
    for agent in agents:
        target_admission.ensure_agent_worker_admitted(
            experiment_id=str(experiment.id), dataset_id=str(snapshot.snapshot_dataset_id),
            experiment_agent_config_id=str(agent.id), graph_version_id=str(agent.graph_version_id),
            row_ids=(subset_rows or {}).get(str(agent.id)))
        graph_plan = native_graph_plan(agent, snapshot, workspace=workspace)
        # The required singular FK is only an identity anchor. Actual graph
        # output and evaluator receipts must retain ALL terminal outputs.
        output = Column.no_workspace_objects.get(pk=graph_plan["terminal_column_ids"][0],
            dataset_id=snapshot.snapshot_dataset_id, deleted=False)
        run = bind_native_run(dataset=experiment.dataset, experiment=experiment, variant=agent.experiment_dataset,
            execution_id=execution_id, output_column=output, created_by=experiment.user,
            authorized_workspace=workspace, graph_plan=graph_plan,
            effective_settings={"trials": 1, "concurrency": workflow_args.get("max_concurrent_rows", 10)},
            **({"snapshot_row_ids": subset_rows[str(agent.id)]} if subset_rows is not None else {}))
        contexts[str(agent.id)] = {"run_id": str(run.id), "execution_id": str(execution_id),
            "config_id": str(agent.id), "columns": columns, "execution_mode": "graph",
            "output_column_id": str(output.id)}
    return contexts


def _stage_native_prompt_runs(experiment, execution_id, workflow_args, workspace, *, config_ids=None, subset_rows=None,
                              eval_only=False, evaluator_metric_ids=None, evaluator_rows=None):
    """Bind versioned prompt and evaluator-only work before any provider runs.

    All other cases keep their existing executor and are explicitly recorded as
    pending bridge slices. No historical run or read request is backfilled.
    """
    from model_hub.models.develop_dataset import Column
    from model_hub.models.experiments import ExperimentPromptConfig, ExperimentAgentConfig
    from model_hub.models.native_experiment_runs import NativeExperimentSnapshot, NativeRunBinding
    from model_hub.models.choices import SourceChoices
    from model_hub.services.native_run_binding import bind_native_run
    from model_hub.services.experiment_run_writes import RunWriteError

    pending = []
    prompts = list(ExperimentPromptConfig.no_workspace_objects.select_related(
        "experiment_dataset",
    ).filter(experiment_dataset__experiment=experiment, experiment_dataset__deleted=False, deleted=False))
    if config_ids is not None:
        selected = {str(value) for value in config_ids}
        prompts = [prompt for prompt in prompts if str(prompt.id) in selected]
        if not selected or {str(prompt.id) for prompt in prompts} != selected:
            raise RunWriteError("native append selectors do not match live prompt variants")
    versioned = [p for p in prompts if p.prompt_version_id]
    if len(versioned) != len(prompts):
        pending.append("unversioned prompt modes have no canonical execution binding")
    if not versioned:
        return {}, pending
    snapshot = NativeExperimentSnapshot.objects.filter(experiment=experiment).first()
    if snapshot is None:
        if config_ids is not None:
            return {}, ["historical append has no immutable snapshot identity; classic execution only"]
        return {}, ["historical execution has no immutable snapshot identity; classic execution only"]
    copied = {str(c.id): c for c in Column.no_workspace_objects.filter(
        dataset_id=snapshot.snapshot_dataset_id,
        id__in=snapshot.column_map.values(), deleted=False,
    )}
    if set(copied) != set(snapshot.column_map.values()):
        raise RunWriteError("native rendering columns are unavailable")
    # Names/types are part of provider input interpretation, not live Cell reads.
    # Persist their exact admission-time representation beside the run identity.
    columns = [{"source_id": old, "snapshot_id": new,
                "name": copied[new].name, "data_type": copied[new].data_type}
               for old, new in sorted(snapshot.column_map.items())]
    result = {}
    for prompt in versioned:
        if eval_only and not NativeRunBinding.objects.filter(
                variant=prompt.experiment_dataset, run__deleted=False, run__completed_at__isnull=False,
                run__items__status__in=["completed", "failed", "skipped"]).exists():
            pending.append("historical eval-only output has no canonical receipt; classic execution only")
            continue
        outputs = list(prompt.experiment_dataset.columns.filter(
            dataset_id=experiment.snapshot_dataset_id, deleted=False, source=SourceChoices.EXPERIMENT.value,
        ))
        if len(outputs) != 1:
            raise RunWriteError("native prompt requires exactly one output column")
        run = bind_native_run(
            dataset=experiment.dataset, experiment=experiment,
            variant=prompt.experiment_dataset, execution_id=execution_id,
            output_column=outputs[0], created_by=experiment.user,
            authorized_workspace=workspace,
            effective_settings={"trials": 1, "concurrency": workflow_args.get("max_concurrent_rows", 10)},
            **({"snapshot_row_ids": subset_rows[str(prompt.id)]} if subset_rows is not None else {}),
            eval_only=eval_only, evaluator_metric_ids=evaluator_metric_ids,
            evaluator_rows=(evaluator_rows or {}).get(str(prompt.id)),
        )
        context = {"run_id": str(run.id), "execution_id": execution_id,
                   "config_id": str(prompt.id), "columns": columns,
                   "execution_mode": "eval_only" if eval_only else "prompt",
                   "output_column_id": str(outputs[0].id)}
        result[str(prompt.id)] = context
    return result, pending


def stage_submission(experiment, data, operation, *, authorized_workspace=None, batch_id=None,
                     batch_members=None, execution_key=None, subset_rows=None, native_eval_only=False,
                     evaluator_metric_ids=None, evaluator_rows=None, http_plan=None, **workflow_args):
    """Persist intent in the mutation transaction with the experiment locked."""
    execution_key = validate_execution_key(execution_key)
    if execution_key is not None:
        previous = keyed_submission(experiment, data, operation, execution_key)
        if previous is not None:
            return previous
    ensure_native_idle(experiment)
    snapshot = Dataset.no_workspace_objects.select_for_update().get(
        id=experiment.snapshot_dataset_id
    )
    config = dict(snapshot.dataset_config or {})
    _remember_submission(config, config.get(STATE_KEY, {}))
    admission_id = str(uuid.uuid4())
    native_runs, native_pending = ({}, [])
    from model_hub.models.experiments import ExperimentPromptConfig, ExperimentAgentConfig
    from model_hub.models.native_experiment_runs import NativeExperimentSnapshot
    prompt_ids = {str(value) for value in ExperimentPromptConfig.no_workspace_objects.filter(
        experiment_dataset__experiment=experiment, experiment_dataset__deleted=False, deleted=False).values_list('id', flat=True)}
    agent_ids = {str(value) for value in ExperimentAgentConfig.no_workspace_objects.filter(
        experiment_dataset__experiment=experiment, experiment_dataset__deleted=False, deleted=False).values_list('id', flat=True)}
    if subset_rows is not None:
        if set(subset_rows) - prompt_ids - agent_ids:
            raise ValueError('Native subset contains an unavailable configuration')
        selected_prompts, selected_agents = sorted(set(subset_rows) & prompt_ids), sorted(set(subset_rows) & agent_ids)
    elif operation in {'append_variant', 'update'}:
        selected_prompts = workflow_args.get('rerun_prompt_config_ids', [])
        selected_agents = workflow_args.get('rerun_agent_config_ids', [])
    else:
        selected_prompts, selected_agents = None, None
    if operation in {'create', 'append_variant', 'update', 'rerun', 'subset'}:
        if selected_prompts != []:
            native_runs, native_pending = _stage_native_prompt_runs(
                experiment, admission_id, workflow_args, authorized_workspace, config_ids=selected_prompts,
                subset_rows=subset_rows, eval_only=native_eval_only,
                evaluator_metric_ids=evaluator_metric_ids, evaluator_rows=evaluator_rows)
        if agent_ids and selected_agents != [] and not native_eval_only:
            if NativeExperimentSnapshot.objects.filter(experiment=experiment).exists():
                native_runs.update(stage_native_graph_runs(experiment, admission_id, workflow_args,
                    authorized_workspace, config_ids=selected_agents, subset_rows=subset_rows))
            else:
                native_pending.append('historical graph has no immutable snapshot; classic graph execution retained')
        if operation == 'create' and any('no immutable snapshot' in reason for reason in native_pending):
            raise ValueError('Native identity was not recorded at snapshot creation')
        if native_runs:
            workflow_args = {**workflow_args, 'native_runs': native_runs}
    if operation == 'update':
        eval_ids = workflow_args.get("rerun_eval_template_ids")
        if eval_ids:
            # Changed evaluators also run against unchanged native prompt outputs.
            # They get a fresh identity, but never schedule prompt generation.
            from model_hub.models.experiments import ExperimentPromptConfig
            remaining = [str(value) for value in ExperimentPromptConfig.no_workspace_objects.filter(
                experiment_dataset__experiment=experiment, experiment_dataset__deleted=False,
                deleted=False, prompt_version__isnull=False,
            ).values_list("id", flat=True) if str(value) not in native_runs]
            if remaining:
                evaluations, pending = _stage_native_prompt_runs(
                    experiment, admission_id, workflow_args, authorized_workspace,
                    config_ids=remaining, eval_only=True, evaluator_metric_ids=eval_ids,
                )
                native_runs.update(evaluations)
                native_pending.extend(pending)
        if native_runs:
            workflow_args = {**workflow_args, "native_runs": native_runs}
    plan = _execution_plan(experiment, admission_id, operation, execution_key, native_runs, authorized_workspace)
    config[STATE_KEY] = {
        "admission_id": admission_id,
        **({"execution_key": execution_key} if execution_key is not None else {}),
        **plan,
        "request_hash": request_hash(data, operation),
        **({"batch_id": batch_id, "batch_members": batch_members} if batch_id else {}),
        "state": "pending",
        "workflow_args": workflow_args,
        **({"http_plan": deepcopy(http_plan)} if http_plan is not None else {}),
        **({"native_bridge_pending": native_pending} if native_pending else {}),
    }
    _remember_submission(config, config[STATE_KEY])
    snapshot.dataset_config = config
    snapshot.save(update_fields=["dataset_config"])
    experiment.status = StatusType.QUEUED.value
    experiment.save(update_fields=["status"])
    return deepcopy(config[STATE_KEY])


class ExecutionKeyConflict(ValueError):
    """A caller key already names a different immutable execution request."""


class SubmissionCancelled(ExecutionKeyConflict):
    """Terminal stopped intent: never redispatch it or report ambiguous admission."""


def _locked_native_admission(experiment_id, contexts=None):
    """Caller holds atomic(): source -> experiment -> snapshot -> canonical runs.

    Contexts select one historical admission, never the latest run by guesswork.
    The request-side Stop may omit contexts to select its locked latest intent.
    """
    from model_hub.services.experiment_run_writes import RunWriteError

    candidate = ExperimentsTable.no_workspace_objects.get(id=experiment_id, deleted=False)
    Dataset.no_workspace_objects.select_for_update().get(id=candidate.dataset_id, deleted=False)
    experiment = ExperimentsTable.no_workspace_objects.select_for_update().get(
        id=experiment_id, dataset_id=candidate.dataset_id, deleted=False)
    if not experiment.snapshot_dataset_id:
        if contexts is not None:
            raise RunWriteError("native cancellation snapshot is unavailable")
        return experiment, None, {}
    snapshot = Dataset.no_workspace_objects.select_for_update().get(
        id=experiment.snapshot_dataset_id, deleted=False)
    admission_id = None
    if contexts is not None:
        ids = {context.get("execution_id") for context in contexts.values()}
        if not contexts or len(ids) != 1 or None in ids:
            raise RunWriteError("native cancellation requires one complete admission")
        admission_id = ids.pop()
    state = _submission_record(snapshot.dataset_config, admission_id)
    expected = state.get("workflow_args", {}).get("native_runs", {})
    if contexts is not None and (not expected or contexts != expected):
        raise RunWriteError("native cancellation does not match the committed admission")
    return experiment, snapshot, state


def _cancel_pending_locked(experiment, snapshot, state):
    """Revoke a never-acknowledged plan before releasing its reservation.

    The parent cannot dispatch before accepted commits under these same locks.
    A durable cancelled tombstone therefore fences even an unknown/lost start;
    no Temporal availability or eventually consistent visibility query is needed.
    """
    from model_hub.services.experiment_run_writes import RunWriteError
    from model_hub.services.native_run_recording import mark_native_run
    from model_hub.views.experiment_runner import _native_dispatch

    config = dict(snapshot.dataset_config or {})
    if config.get(STATE_KEY, {}).get("admission_id") != state.get("admission_id"):
        raise RunWriteError("cannot release a newer native admission")
    if state.get("state") != "pending":
        return None
    contexts = state.get("workflow_args", {}).get("native_runs", {})
    from model_hub.services.http_experiment_source import http_source_ids, cancel_pending_http_cells
    http_ids = http_source_ids(experiment)
    if not contexts and not http_ids:
        return None  # Classic-only pending admissions retain their existing path.
    checked = [_native_dispatch(str(experiment.id), ctx, full_plan=True) for ctx in contexts.values()]
    if any(run.status not in {"draft", "cancelled"} or run.items.exists()
           for _, run, _ in checked):
        raise RunWriteError("pending admission already has execution evidence")
    for dataset, run, _ in checked:
        if run.status == "draft":
            mark_native_run(dataset=dataset, run_id=run.id, status="cancelled")
    state.update(state="cancelled", cancelled_pending=True,
                 cancellation_reason="stopped_before_acknowledgement")
    _remember_submission(config, state)
    config[STATE_KEY] = deepcopy(state)
    snapshot.dataset_config = config
    snapshot.save(update_fields=["dataset_config"])
    experiment.status = StatusType.CANCELLED.value
    experiment.save(update_fields=["status"])
    if http_ids:
        cancel_pending_http_cells(experiment, state["admission_id"])
    return state


def cancel_pending_submission(experiment_id):
    """After request authorization, terminally Stop a pending native/HTTP admission.

    Waiting parents consume the tombstone and exit without cleanup. Preserve
    terminal receipts; settle claims left by older pending-enabled HTTP workers.
    """
    from tfc.routers import force_primary

    with force_primary(), transaction.atomic():
        experiment, snapshot, state = _locked_native_admission(experiment_id)
        if snapshot is None:
            return None
        if state.get("state") == "cancelled" and state.get("cancelled_pending"):
            return state
        return _cancel_pending_locked(experiment, snapshot, state)


def validate_execution_key(value):
    if value is not None and (not isinstance(value, str) or not 1 <= len(value) <= 128
                              or any(ord(char) < 33 or ord(char) > 126 for char in value)):
        raise ValueError("Idempotency-Key must contain 1 to 128 visible ASCII characters.")
    return value


def keyed_submission(experiment, data, operation, execution_key):
    """Resolve an exact journal receipt after caller authorization/source lock.

    A historical accepted create must not redispatch the latest edit/subset.
    Omitted keys retain their original request matching behavior.
    """
    execution_key = validate_execution_key(execution_key)
    if execution_key is None:
        return None
    config = Dataset.no_workspace_objects.select_for_update().get(
        pk=experiment.snapshot_dataset_id, deleted=False).dataset_config or {}
    matches = [record for record in _admission_records(config).values()
               if record.get("execution_key") == execution_key]
    if not matches:
        return None
    if (len(matches) != 1 or matches[0].get("request_hash") != request_hash(data, operation)
            or matches[0].get("state") not in {"pending", "accepted", "cancelled"}):
        raise ExecutionKeyConflict("Idempotency-Key already names a different execution request.")
    if matches[0]["state"] == "cancelled":
        raise SubmissionCancelled("Execution was stopped. Use a new Idempotency-Key for new work.")
    return matches[0]


def _target_for_binding(binding, run):
    """Exact selected cells are output_column_id x snapshot_row_ids.

    Row IDs and example IDs are index-aligned and sorted by snapshot row ID.
    They come from the canonical pinned run plan, never from live Cell state.
    """
    from model_hub.services.experiment_run_writes import RunWriteError, _planned
    from model_hub.services.native_run_binding import snapshot_examples

    planned = _planned(run)
    links = sorted((link for link in snapshot_examples(binding.snapshot)
                    if (str(link.version_item.example_id), 1) in planned),
                   key=lambda link: str(link.snapshot_row_id))
    if len(links) != len(planned):
        raise RunWriteError("native plan does not match its snapshot examples")
    return {"config_id": str(binding.native_config_id), "variant_id": str(binding.variant_id),
            "output_column_id": str(binding.output_column_id), "run_id": str(run.id),
            "dataset_version_id": str(run.dataset_version_id), "task_kind": run.task_kind,
            "evaluator_versions": deepcopy(run.evaluator_versions),
            **({"graph_plan": deepcopy(run.metadata["native_identity"]["graph_plan"])}
               if "graph_plan" in run.metadata.get("native_identity", {}) else {}),
            "execution_mode": run.metadata.get("native_identity", {}).get("execution_mode", "prompt"),
            "evaluation_sources": deepcopy(run.metadata.get("native_identity", {}).get("evaluation_sources", {})),
            "evaluator_rows": deepcopy(run.metadata.get("native_identity", {}).get("evaluator_rows", {})),
            "snapshot_row_ids": [str(link.snapshot_row_id) for link in links],
            "example_ids": [str(link.version_item.example_id) for link in links]}


def _execution_plan(experiment, admission_id, operation, execution_key, contexts, workspace):
    """One immutable plan format for whole and subset native dispatch.

    The plan (with every selected row) is persisted once in the admission
    journal. Dispatch contexts, which Temporal copies into every activity, carry
    only its identity (plan_id, plan_hash, row_count); workers re-read the
    persisted plan and validate it against the pinned run at entry.
    """
    from model_hub.models.native_experiment_runs import NativeRunBinding
    from model_hub.services.experiment_run_writes import RunWriteError, _digest

    if not contexts:
        return {}
    bindings = {str(binding.native_config_id): binding for binding in
                NativeRunBinding.objects.select_related("run", "snapshot").filter(
                    snapshot__experiment=experiment, execution_id=admission_id)}
    if set(bindings) != set(contexts):
        raise ValueError("Execution binding membership does not match admission.")
    scopes = {(binding.run.metadata or {}).get("native_identity", {}).get("authorization_workspace_id")
              for binding in bindings.values()}
    if len(scopes) != 1:
        raise RunWriteError("native authorization evidence is inconsistent")
    plan = {"plan_id": admission_id, "execution_key": execution_key or admission_id,
            "operation": operation, "experiment_id": str(experiment.id),
            "source_dataset_id": str(experiment.dataset_id),
            "snapshot_dataset_id": str(experiment.snapshot_dataset_id),
            "organization_id": str(experiment.dataset.organization_id),
            "authorization_workspace_id": scopes.pop(),
            "targets": {key: _target_for_binding(binding, binding.run)
                        for key, binding in sorted(bindings.items())}}
    digest = _digest(plan)
    for key, context in contexts.items():
        context.update(plan_id=admission_id, plan_hash=digest,
                       row_count=len(plan["targets"][key]["snapshot_row_ids"]))
        run = bindings[key].run
        run.metadata = {**run.metadata, "native_dispatch_hash": _digest(context),
                        "execution_plan_id": admission_id, "execution_plan_hash": digest}
        run.save(update_fields=["metadata", "updated_at"])
    return {"execution_plan": plan, "execution_plan_hash": digest}


def validate_execution_plan(experiment, context, run, binding, *, row_id=None, full=False):
    """Resolve exact history (never the latest cache); reject plan tampering.

    Every worker entry re-reads the persisted plan and checks its digest, scope,
    run binding and, for row entries, membership of that exact row. Setup and
    acknowledgement (full) also re-derive the target from the pinned canonical
    run so a forged plan cannot widen or move the expected items.
    """
    from model_hub.services.experiment_run_writes import RunWriteError, _digest

    metadata = run.metadata or {}
    if not context.get("plan_id") and not metadata.get("execution_plan_id"):
        return  # Already-admitted pre-plan executions keep their original contract.
    config = Dataset.no_workspace_objects.filter(pk=experiment.snapshot_dataset_id).values_list(
        "dataset_config", flat=True).first()
    state = _peek_record(config, context.get("plan_id"))
    plan = state.get("execution_plan") or {}
    target = (plan.get("targets") or {}).get(context.get("config_id")) or {}
    rows = target.get("snapshot_row_ids")
    scope = {"experiment_id": str(experiment.id), "source_dataset_id": str(experiment.dataset_id),
             "snapshot_dataset_id": str(experiment.snapshot_dataset_id),
             "organization_id": str(experiment.dataset.organization_id),
             "authorization_workspace_id": metadata.get("native_identity", {}).get(
                 "authorization_workspace_id")}
    if (not plan or not target or not isinstance(rows, list)
            or plan.get("plan_id") != str(binding.execution_id)
            or context.get("plan_id") != state.get("admission_id")
            or metadata.get("execution_plan_id") != context.get("plan_id")
            or any(plan.get(key) != value for key, value in scope.items())
            or state.get("execution_plan_hash") != _digest(plan)
            or context.get("plan_hash") != state.get("execution_plan_hash")
            or metadata.get("execution_plan_hash") != context.get("plan_hash")
            or context.get("row_count") != len(rows)
            or (state.get("workflow_args") or {}).get("native_runs", {}).get(
                context.get("config_id")) != context
            or target.get("run_id") != str(run.id)
            or target.get("variant_id") != str(binding.variant_id)
            or target.get("output_column_id") != str(binding.output_column_id)):
        raise RunWriteError("native execution plan does not match committed identity and expected items")
    if row_id is not None and str(row_id) not in set(rows):
        raise RunWriteError("native row is not in the admitted execution plan")
    if full and target != _target_for_binding(binding, run):
        raise RunWriteError("native execution plan does not match committed identity and expected items")


def _resolve_result_cells(native, cells):
    """Translate explicit Results selectors inside the authorized snapshot only.

    Classic row_id selectors remain snapshot IDs; never guess their namespace.
    The caller holds the source/experiment/snapshot locks and has validated scope.
    """
    from model_hub.models.native_experiment_runs import NativeRunBinding
    from model_hub.services.experiment_run_writes import _planned
    from model_hub.services.native_run_binding import snapshot_examples

    run_ids = {str(cell["run_id"]) for cell in cells if "example_id" in cell}
    if not run_ids:
        return cells
    bindings = {str(binding.run_id): binding for binding in NativeRunBinding.objects.select_related("run").filter(
        snapshot=native, run_id__in=run_ids, run__deleted=False,
        run__experiment_id=native.experiment_id, run__dataset_version_id=native.dataset_version_id,
        variant__experiment_id=native.experiment_id, variant__deleted=False,
        output_column__dataset_id=native.snapshot_dataset_id, output_column__deleted=False,
    )}
    rows = {str(link.version_item.example_id): str(link.snapshot_row_id)
            for link in snapshot_examples(native)}
    planned = {run_id: {example_id for example_id, _ in _planned(binding.run)}
               for run_id, binding in bindings.items()}
    resolved = []
    for cell in cells:
        if "example_id" not in cell:
            resolved.append(cell)
            continue
        run_id, example_id = str(cell["run_id"]), str(cell["example_id"])
        binding = bindings.get(run_id)
        if (binding is None or str(binding.output_column_id) != str(cell["column_id"])
                or example_id not in rows or example_id not in planned[run_id]):
            raise ValueError("Results cell not found in the bound experiment run.")
        resolved.append({"column_id": cell["column_id"], "row_id": rows[example_id]})
    return resolved


def stage_subset_submission(experiment, data, workspace, execution_key=None):
    """Resolve exact targets before reset under source/experiment/snapshot locks.

    Historical and base-only targets retain their existing executor.
    Native evaluator-only selections pin output receipts, rows and evaluator IDs.
    """
    from model_hub.models.choices import CellStatus, SourceChoices
    from model_hub.models.develop_dataset import Cell, Column
    from model_hub.models.experiments import ExperimentPromptConfig, ExperimentAgentConfig
    from model_hub.models.native_experiment_runs import NativeExperimentSnapshot
    from model_hub.services.native_run_binding import snapshot_examples, validate_snapshot

    native = NativeExperimentSnapshot.objects.select_related("dataset_version", "snapshot_dataset").filter(
        experiment=experiment).first()
    if native is None:
        return None
    validate_snapshot(native, experiment.dataset, experiment)
    snapshot = Dataset.no_workspace_objects.select_for_update().get(pk=experiment.snapshot_dataset_id, deleted=False)
    cells = _resolve_result_cells(native, data.get("cells", []))
    normalized = {"source_ids": sorted({str(value) for value in data.get("source_ids", [])}),
                  "cells": [dict(column_id=column, row_id=row) for column, row in sorted({
                      (str(cell["column_id"]), str(cell["row_id"])) for cell in cells})],
                  "user_eval_metric_ids": sorted({str(value) for value in data.get("user_eval_metric_ids", [])}),
                  "failed_only": bool(data.get("failed_only", False)),
                  "max_concurrent_rows": data.get("max_concurrent_rows", 10)}
    digest = request_hash(normalized, "subset")
    execution_key = validate_execution_key(execution_key)
    history = _admission_records(snapshot.dataset_config)
    latest = (snapshot.dataset_config or {}).get(STATE_KEY, {})
    if execution_key is not None:
        matches = [record for record in history.values() if record.get("execution_key") == execution_key]
        if matches:
            if len(matches) != 1 or matches[0].get("request_hash") != digest:
                raise ExecutionKeyConflict("Idempotency-Key already names a different execution request.")
            if matches[0].get("state") == "cancelled":
                raise SubmissionCancelled("Execution was stopped. Use a new Idempotency-Key for new work.")
            return matches[0]
    elif (latest.get("request_hash") == digest and latest.get("execution_plan", {}).get("operation") == "subset"
          and (latest.get("state") == "pending" or experiment.status in (StatusType.QUEUED.value, StatusType.RUNNING.value))):
        return latest
    columns = {str(column.id): column for column in Column.no_workspace_objects.filter(
        pk__in=[cell["column_id"] for cell in normalized["cells"]], dataset_id=snapshot.pk, deleted=False)}
    if set(columns) != {cell["column_id"] for cell in normalized["cells"]}:
        raise ValueError("Columns not found in experiment snapshot.")
    all_metric_ids = {str(value) for value in experiment.user_eval_template_ids.through.objects.filter(
        experimentstable_id=experiment.pk).values_list("userevalmetric_id", flat=True)}
    metrics = set(normalized["user_eval_metric_ids"])
    if metrics - all_metric_ids:
        raise ValueError("Evaluator selectors are outside this experiment.")
    eval_sources = {SourceChoices.EXPERIMENT_EVALUATION.value, SourceChoices.EXPERIMENT_EVALUATION_TAGS.value}
    source_kinds = {column.source for column in columns.values()}
    cell_evals = bool(columns) and source_kinds <= eval_sources
    if source_kinds and source_kinds != {SourceChoices.EXPERIMENT.value} and not cell_evals:
        return None  # Existing base/graph/mixed classic selector semantics stay intact.
    eval_only = bool(metrics) or cell_evals
    eval_columns = {}
    if cell_evals:
        for column in columns.values():
            left, marker, metric_id = (column.source_id or "").partition("-sourceid-")
            if not marker or metric_id not in all_metric_ids or len(left) != 73 or left[36] != "-":
                raise ValueError("Native evaluator column identity is invalid.")
            eval_columns[str(column.id)] = (left[:36], left[37:], metric_id)
            metrics.add(metric_id)
    variants = set(normalized["source_ids"]) | {
        eval_columns[str(column.id)][0] if cell_evals else str(column.source_id) for column in columns.values()}
    all_prompts = ExperimentPromptConfig.no_workspace_objects.select_related("experiment_dataset").filter(
        experiment_dataset__experiment=experiment, experiment_dataset__deleted=False, deleted=False)
    prompts = list(all_prompts.filter(experiment_dataset_id__in=variants) if variants else all_prompts)
    all_agents = ExperimentAgentConfig.no_workspace_objects.select_related('experiment_dataset').filter(
        experiment_dataset__experiment=experiment, experiment_dataset__deleted=False, deleted=False)
    agents = [] if eval_only else list(all_agents.filter(experiment_dataset_id__in=variants) if variants else all_agents)
    if variants and ({str(config.experiment_dataset_id) for config in prompts + agents} != variants
                     or any(not prompt.prompt_version_id for prompt in prompts)):
        return None
    if eval_only:
        from model_hub.models.native_experiment_runs import NativeRunBinding
        # Unproven historical outputs stay classic, never backfilled from Cells.
        prompts = [prompt for prompt in prompts if prompt.prompt_version_id and NativeRunBinding.objects.filter(
            variant=prompt.experiment_dataset, run__deleted=False, run__completed_at__isnull=False,
            run__items__status__in=["completed", "failed", "skipped"]).exists()]
    if not prompts and not agents:
        return None
    if latest.get("state") == "pending" or experiment.status in (StatusType.QUEUED.value, StatusType.RUNNING.value):
        raise ExecutionKeyConflict("Retry the active execution before starting another subset.")
    ensure_native_idle(experiment)
    links = snapshot_examples(native)
    all_rows = {str(link.snapshot_row_id) for link in links}
    if any(cell["row_id"] not in all_rows for cell in normalized["cells"]):
        raise ValueError("Rows not found in immutable experiment snapshot.")
    # Bulk evaluator retries also own classic base-column failures. Resolve
    # them before any reset, independently of the canonical per-variant rows.
    # The parent retains the classic union-of-failed-rows contract; native
    # children instead read their exact immutable target rows from the plan.
    base_rows = set()
    if normalized["user_eval_metric_ids"] and not columns and normalized["failed_only"]:
        base_columns = Column.no_workspace_objects.filter(
            dataset_id=snapshot.pk, deleted=False, source_id__in=metrics,
            source__in=[SourceChoices.EVALUATION.value, SourceChoices.EVALUATION_TAGS.value],
        ).values_list("id", flat=True)
        base_rows = {str(row) for row in Cell.no_workspace_objects.filter(
            dataset_id=snapshot.pk, column_id__in=base_columns, row_id__in=all_rows,
            deleted=False, status=CellStatus.ERROR.value,
        ).values_list("row_id", flat=True)}
    selected, evaluator_rows, resolved_columns = {}, {}, set()
    for prompt in prompts:
        outputs = list(prompt.experiment_dataset.columns.filter(
            dataset_id=snapshot.pk, deleted=False, source=SourceChoices.EXPERIMENT.value))
        if len(outputs) != 1:
            raise ValueError("Native prompt requires exactly one output column.")
        column = outputs[0]; variant_id = str(prompt.experiment_dataset_id)
        if cell_evals:
            selected_cells = [cell for cell in normalized["cells"]
                              if eval_columns[cell["column_id"]][0] == variant_id]
            if any(eval_columns[cell["column_id"]][1] != str(column.id) for cell in selected_cells):
                raise ValueError("Evaluator column does not belong to this native output.")
            if normalized["failed_only"]:
                failed = {(str(c), str(r)) for c, r in Cell.no_workspace_objects.filter(
                    dataset_id=snapshot.pk, column_id__in=[cell["column_id"] for cell in selected_cells],
                    row_id__in=[cell["row_id"] for cell in selected_cells], deleted=False,
                    status=CellStatus.ERROR.value).values_list("column_id", "row_id")}
                selected_cells = [cell for cell in selected_cells if (cell["column_id"], cell["row_id"]) in failed]
            per_row = {}
            for cell in selected_cells:
                per_row.setdefault(cell["row_id"], set()).add(eval_columns[cell["column_id"]][2])
            rows = set(per_row)
            resolved_columns.update(key for key, value in eval_columns.items() if value[0] == variant_id)
        else:
            resolved_columns.add(str(column.id))
            rows = set(all_rows) if variant_id in normalized["source_ids"] or (eval_only and not columns) else {
                cell["row_id"] for cell in normalized["cells"] if cell["column_id"] == str(column.id)}
            if normalized["failed_only"]:
                if eval_only:
                    eval_ids = list(Column.no_workspace_objects.filter(
                        dataset_id=snapshot.pk, source__in=eval_sources, deleted=False,
                        source_id__startswith=f"{variant_id}-{column.id}-sourceid-",
                    ).values_list("id", "source_id"))
                    eval_ids = [cid for cid, source_id in eval_ids if source_id.rsplit("-sourceid-", 1)[1] in metrics]
                    rows &= {str(r) for r in Cell.no_workspace_objects.filter(
                        dataset_id=snapshot.pk, column_id__in=eval_ids, row_id__in=rows,
                        deleted=False, status=CellStatus.ERROR.value).values_list("row_id", flat=True)}
                else:
                    rows &= {str(r) for r in Cell.no_workspace_objects.filter(
                        dataset_id=snapshot.pk, column_id=column.id, row_id__in=rows,
                        deleted=False, status=CellStatus.ERROR.value).values_list("row_id", flat=True)}
            per_row = {row: metrics for row in rows}
        if rows:
            selected[str(prompt.id)] = sorted(rows)
            if eval_only:
                evaluator_rows[str(prompt.id)] = {row: sorted(ids) for row, ids in per_row.items()}
    for agent in agents:
        output_ids = {str(value) for value in agent.experiment_dataset.columns.filter(
            dataset_id=snapshot.pk, deleted=False, source=SourceChoices.EXPERIMENT.value).values_list('id', flat=True)}
        if not output_ids:
            raise ValueError('Native graph has no output columns')
        resolved_columns.update(output_ids)
        chosen_columns = output_ids if str(agent.experiment_dataset_id) in normalized['source_ids'] else output_ids & set(columns)
        rows = set(all_rows) if str(agent.experiment_dataset_id) in normalized['source_ids'] else {
            cell['row_id'] for cell in normalized['cells'] if cell['column_id'] in output_ids}
        if normalized['failed_only']:
            rows &= {str(row) for row in Cell.no_workspace_objects.filter(
                dataset_id=snapshot.pk, column_id__in=chosen_columns, row_id__in=rows,
                deleted=False, status=CellStatus.ERROR.value).values_list('row_id', flat=True)}
        if rows:
            selected[str(agent.id)] = sorted(rows)
    if set(columns) - resolved_columns:
        if eval_only:
            return None
        raise ValueError("Selected output columns do not belong to native variants.")
    if not selected:
        if base_rows:
            # No native work is covered. Let the unchanged classic bulk path
            # reset and schedule the base failures rather than returning 400.
            return None
        raise ValueError("No rows match the requested subset.")
    state = stage_submission(experiment, normalized, "subset", authorized_workspace=workspace,
        execution_key=execution_key, subset_rows=selected, native_eval_only=eval_only,
        evaluator_metric_ids=sorted(metrics) if eval_only else None,
        evaluator_rows=evaluator_rows if eval_only else None, dataset_id=str(snapshot.pk),
        prompt_config_ids=[] if eval_only else sorted(set(selected) & {str(config.id) for config in prompts}),
        agent_config_ids=sorted(set(selected) & {str(config.id) for config in agents}),
        row_ids=sorted(base_rows | {row for rows in selected.values() for row in rows}) if eval_only else [], failed_only=False,
        eval_only=eval_only, edt_ids=sorted(variants) if eval_only else [],
        eval_template_ids=sorted(metrics if eval_only else all_metric_ids),
        max_concurrent_rows=normalized["max_concurrent_rows"])
    if set(state.get("workflow_args", {}).get("native_runs", {})) != set(selected):
        raise ValueError("Native subset binding did not cover the selected configurations.")
    return state


def native_admission_ready(experiment_id, contexts):
    """Read the exact committed acknowledgement; never admit from the worker.

    A live workflow may precede its HTTP start acknowledgement indefinitely.
    Pending is a durable wait state, not a failed execution/setup attempt.
    """
    from model_hub.services.experiment_run_writes import RunWriteError
    from model_hub.views.experiment_runner import _native_dispatch
    from tfc.routers import force_primary

    with force_primary(), transaction.atomic():
        candidate = ExperimentsTable.no_workspace_objects.get(id=experiment_id, deleted=False)
        Dataset.no_workspace_objects.select_for_update().get(id=candidate.dataset_id, deleted=False)
        experiment = ExperimentsTable.no_workspace_objects.select_for_update().get(
            id=experiment_id, dataset_id=candidate.dataset_id, deleted=False,
        )
        snapshot = Dataset.no_workspace_objects.select_for_update().get(
            id=experiment.snapshot_dataset_id, deleted=False,
        )
        execution_ids = {context.get("execution_id") for context in contexts.values()} if contexts else set()
        if len(execution_ids) != 1 or None in execution_ids:
            raise RunWriteError("native execution identity is missing or inconsistent")
        state = _submission_record(snapshot.dataset_config, execution_ids.pop())
        expected = state.get("workflow_args", {}).get("native_runs", {})
        if contexts and contexts == expected and state.get("state") == "cancelled":
            raise SubmissionCancelled("Native admission was stopped before acknowledgement.")
        if not contexts or contexts != expected or state.get("state") not in {"pending", "accepted"}:
            raise RunWriteError("native execution does not match the committed admission")
        ready = state["state"] == "accepted"
        if ready and not state.get("workflow_id"):
            raise RunWriteError("native admission has no confirmed workflow")
        for config_id, context in contexts.items():
            _, run, binding = _native_dispatch(experiment_id, context, full_plan=True)
            if (str(binding.execution_id) != state.get("admission_id")
                    or str(binding.native_config_id) != config_id):
                raise RunWriteError("native admission identity is inconsistent")
            if (not ready and run.status != "draft") or (ready and run.status in {"draft", "cancelled"}):
                raise RunWriteError("native execution is not available for this admission")
        return ready


def admit_submission(experiment_id, admission_id=None):
    """Serialize retries while retaining committed intent after ambiguous RPCs."""
    from tfc.temporal.experiments import start_experiment_v2_workflow, start_rerun_cells_v2_workflow

    with transaction.atomic():
        # Native writes lock source dataset before experiment/run. Keep that
        # ordering across request admission and worker acknowledgement.
        candidate = ExperimentsTable.no_workspace_objects.get(id=experiment_id)
        Dataset.no_workspace_objects.select_for_update().get(id=candidate.dataset_id)
        experiment = ExperimentsTable.no_workspace_objects.select_for_update().get(
            id=experiment_id
        )
        snapshot = Dataset.no_workspace_objects.select_for_update().get(
            id=experiment.snapshot_dataset_id
        )
        config = dict(snapshot.dataset_config or {})
        state = _submission_record(config, admission_id)
        if state.get("state") == "cancelled":
            raise SubmissionCancelled("Execution was stopped; submit a new execution request.")
        if state.get("state") == "accepted":
            return state
        if not state.get("admission_id"):
            raise ValueError("Submission intent is missing.")
        if config.get(STATE_KEY, {}).get("admission_id") != state["admission_id"]:
            raise ValueError("Pending admission is no longer the active intent.")
        # TX1-B: the staged selectors decide which agent targets would run.
        args = state["workflow_args"]
        subset = state.get("execution_plan", {}).get("operation") == "subset"
        # A prompt-only subset executes no agent: name its prompts so another
        # variant's stored agent target is not admitted (or refused) for it.
        if state.get("http_plan"):
            from model_hub.services.http_experiment_source import ensure_http_mutation_admitted
            ensure_http_mutation_admitted(str(experiment.id), state["admission_id"])
        else:
            target_admission.ensure_v2_run_admitted(
                str(experiment.id),
                rerun_prompt_config_ids=args.get("rerun_prompt_config_ids") or (
                    args.get("prompt_config_ids") if subset else None),
                rerun_agent_config_ids=args.get("rerun_agent_config_ids"),
                rerun_eval_template_ids=args.get("rerun_eval_template_ids") or (args.get("eval_template_ids") if subset and args.get("eval_only") else None),
                column_changed=args.get("column_changed", False),
            )
        starter = start_rerun_cells_v2_workflow if subset else start_experiment_v2_workflow
        workflow_id = starter(
            experiment_id=str(experiment.id),
            admission_id=state["admission_id"],
            **state["workflow_args"],
        )
        if not workflow_id:
            raise ValueError("Execution admission was not confirmed.")
        # The native parent waits durably on this committed acknowledgement.
        # Only the request-side admission path may queue a draft execution.
        native_runs = state["workflow_args"].get("native_runs", {})
        if native_runs:
            from model_hub.services.native_run_recording import mark_native_run
            from model_hub.models.evals_metric import UserEvalMetric
            from model_hub.models.native_experiment_runs import NativeRunBinding
            metric_ids = set()
            for context in native_runs.values():
                mark_native_run(dataset=experiment.dataset, run_id=context["run_id"], status="queued")
                run = NativeRunBinding.objects.get(run_id=context["run_id"]).run
                metric_ids.update(pin["metric_id"] for pin in run.evaluator_versions)
            # Start the selected evaluators exactly once at acknowledgement, not
            # in every row constructor (which could undo a later explicit Stop).
            UserEvalMetric.no_workspace_objects.filter(pk__in=metric_ids, deleted=False).update(
                status=StatusType.RUNNING.value)

        state.update(state="accepted", workflow_id=workflow_id)
        _remember_submission(config, state)
        config[STATE_KEY] = state
        snapshot.dataset_config = config
        snapshot.save(update_fields=["dataset_config"])
        return state


def submit_transaction(method):
    """Commit mutation and intent together, then report confirmed admission."""

    @wraps(method)
    def wrapped(*args, **kwargs):
        if transaction.get_connection().settings_dict.get("ATOMIC_REQUESTS"):
            return Response(
                {
                    "status": False,
                    "detail": "Experiment submission requires autocommit.",
                },
                status=503,
            )
        with transaction.atomic():
            result = method(*args, **kwargs)
            if not isinstance(result, Submission):
                if result.status_code >= 400:
                    transaction.set_rollback(True)
                return result
        try:
            if result.admission_id is None:
                admit_submission(result.experiment_id)
            else:
                admit_submission(result.experiment_id, result.admission_id)
        except SubmissionCancelled as error:
            return Response({"status": False, "code": "experiment_admission_cancelled",
                             "detail": str(error)}, status=409)
        except target_admission.AgentTargetNotQualified as error:
            # A policy refusal is a 403, never an unconfirmed admission.
            return target_admission.refusal_response(error)
        except Exception as error:
            logger.warning(
                "Experiment admission unconfirmed",
                experiment_id=result.experiment_id,
                error_type=type(error).__name__,
            )
            return Response(
                {
                    "status": False,
                    "code": "experiment_admission_unconfirmed",
                    "detail": (
                        "Configuration saved, but execution admission is unconfirmed. "
                        "Retry the same submission; do not create another experiment. "
                        f"experiment_id={result.experiment_id}"
                    ),
                },
                status=503,
            )
        return result.response

    return wrapped
