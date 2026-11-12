"""Native worker write-through into the existing receipted run item store.

P02 calls start at actual dispatch and record after execution AND evaluation.
Classic Cell/EDT writes stay in their existing worker code. This module never
reads a Cell to manufacture results and never claims admission succeeded.
"""
from django.db import transaction

from model_hub.models.experiments import ExperimentRun, ExperimentRunItem
from model_hub.models.native_experiment_runs import NativeExampleBinding, NativeRunBinding
from model_hub.services.experiment_run_writes import (
    RunWriteError, _RUN_KEY, _digest, _planned, _uuid, claim_run_item, finalize_run,
    record_run_item_result, transition_run,
)
from model_hub.services.native_run_binding import live_dataset, validate_snapshot, validate_run_references
from tfc.routers import force_primary


def _bound_run(dataset, run_id):
    dataset = live_dataset(dataset, lock=True)
    run = ExperimentRun.no_workspace_objects.select_for_update(of=("self",)).select_related(
        "dataset_version", "experiment",
    ).filter(
        pk=_uuid(run_id), dataset_version__dataset=dataset, dataset_version__deleted=False,
        deleted=False,
    ).first()
    if run is None or run.experiment_id is None or run.experiment.deleted:
        raise RunWriteError("native run was not found in the authorized dataset")
    binding = NativeRunBinding.objects.select_related(
        "snapshot__dataset_version", "snapshot__snapshot_dataset", "variant", "output_column",
    ).filter(run=run).first()
    if (binding is None or binding.snapshot.experiment_id != run.experiment_id
            or run.experiment.dataset_id != dataset.id
            or binding.snapshot.dataset_version_id != run.dataset_version_id
            or binding.variant.deleted or binding.variant.experiment_id != run.experiment_id
            or binding.output_column.deleted
            or binding.output_column.dataset_id != binding.snapshot.snapshot_dataset_id):
        raise RunWriteError("native run binding is unavailable or inconsistent")
    validate_snapshot(binding.snapshot, dataset, run.experiment)
    validate_run_references(binding, run, dataset)
    return dataset, run, binding


def _example(binding, run, snapshot_row_id, trial_number):
    if type(trial_number) is not int or trial_number < 1:
        raise RunWriteError("trial number must be a positive integer")
    link = NativeExampleBinding.objects.select_related("version_item", "snapshot_row").filter(
        snapshot=binding.snapshot, snapshot_row_id=_uuid(snapshot_row_id),
        snapshot_row__deleted=False, version_item__deleted=False,
        snapshot_row__dataset_id=binding.snapshot.snapshot_dataset_id,
        version_item__dataset_version_id=run.dataset_version_id,
    ).first()
    if link is None or (str(link.version_item.example_id), trial_number) not in _planned(run):
        raise RunWriteError("native row/trial is not in the pinned run plan")
    return link


def _scope(dataset, run):
    return {"run_id": run.id, "organization_id": dataset.organization_id,
            "workspace_id": dataset.workspace_id}


def _key(attempt_key):
    if not isinstance(attempt_key, str) or not attempt_key or len(attempt_key) > 255 or attempt_key.strip() != attempt_key:
        raise RunWriteError("attempt key must be a stable nonempty string of at most 255 characters")
    return attempt_key


@force_primary()
@transaction.atomic
def mark_native_run(*, dataset, run_id, status):
    """P02 uses queued after confirmed admission; cancellation stays explicit."""
    dataset, run, _ = _bound_run(dataset, run_id)
    if status not in {"queued", "cancelled"}:
        raise RunWriteError("only admission or cancellation may be explicitly marked")
    return transition_run(**_scope(dataset, run), status=status)


@force_primary()
@transaction.atomic
def start_attempt(*, dataset, run_id, snapshot_row_id, attempt_key, trial_number=1):
    """One logical attempt key survives Temporal activity retry/re-delivery."""
    dataset, run, binding = _bound_run(dataset, run_id)
    link = _example(binding, run, snapshot_row_id, trial_number)
    key = _key(attempt_key)
    # Existing terminal claims must replay after the run seals too; the base
    # helper deliberately only admits new claims on an executing run.
    existing = ExperimentRunItem.no_workspace_objects.filter(
        run=run, example_id=link.version_item.example_id, trial_number=trial_number,
    ).first()
    if existing:
        receipt = (existing.metadata or {}).get(_RUN_KEY, {}).get("claim_receipt", {})
        if receipt.get("receipt_key") == key:
            return existing
        raise RunWriteError("native attempt is already claimed with another key")
    if run.status == "queued":
        run = transition_run(**_scope(dataset, run), status="running")
    item = claim_run_item(
        **_scope(dataset, run), example_id=link.version_item.example_id,
        trial_number=trial_number, receipt_key=key,
    )
    # Dataset example metadata is caller data, never a worker execution receipt.
    # Only the graph recorder may establish this reserved checkpoint after claim.
    if 'native_graph' in (item.metadata or {}):
        item.metadata = {k: v for k, v in item.metadata.items() if k != 'native_graph'}
        item.save(update_fields=['metadata', 'updated_at'])
    return item


@force_primary()
@transaction.atomic
def record_attempt(*, dataset, run_id, snapshot_row_id, attempt_key, status,
                   trial_number=1, output=None, error=None, trace_id=None,
                   root_span_id=None, latency_ms=None, token_usage=None, cost=None,
                   score_refs=()):
    """Seal real execution/evaluation evidence once; conflicting replays fail.

    For a completed output, every pinned evaluator must have an explicit
    assessed/not_assessed/error/etc reference before sealing. This prevents
    late evaluation from silently overwriting an immutable terminal receipt.
    Trace IDs are actual provider/worker evidence, never invented here; P07
    independently checks project authorization and the root's run-id stamp.
    """
    dataset, run, binding = _bound_run(dataset, run_id)
    link = _example(binding, run, snapshot_row_id, trial_number)
    key = _key(attempt_key)
    item = ExperimentRunItem.no_workspace_objects.select_for_update().filter(
        run=run, example_id=link.version_item.example_id, trial_number=trial_number,
        deleted=False,
    ).first()
    if item is None or (item.metadata or {}).get(_RUN_KEY, {}).get("claim_receipt", {}).get("receipt_key") != key:
        raise RunWriteError("native result requires its actual matching attempt claim")
    if status == "completed":
        required = {(str(pin["metric_id"]), str(pin["evaluator_version_id"])) for pin in run.evaluator_versions}
        if not isinstance(score_refs, (list, tuple)) or any(not isinstance(ref, dict) for ref in score_refs):
            raise RunWriteError("score references must be explicit evaluator evidence")
        supplied = {(str(ref.get("metric_id")), str(ref.get("evaluator_version_id", ref.get("version_id")))) for ref in score_refs}
        if required != supplied:
            raise RunWriteError("completed attempt must include every pinned evaluator outcome")
    return record_run_item_result(
        item_id=item.id, organization_id=dataset.organization_id,
        workspace_id=dataset.workspace_id, status=status, receipt_key=key,
        output=output, error=error, trace_id=trace_id, root_span_id=root_span_id,
        latency_ms=latency_ms, token_usage=token_usage, cost=cost, score_refs=score_refs,
    )


@force_primary()
@transaction.atomic
def finish_native_run(*, dataset, run_id):
    dataset, run, binding = _bound_run(dataset, run_id)
    # Also revalidate live pinned membership on sealed replay.
    from model_hub.services.native_run_binding import snapshot_examples
    snapshot_examples(binding.snapshot)
    return finalize_run(**_scope(dataset, run))


@force_primary()
@transaction.atomic
def trace_context(*, dataset, run_id, snapshot_row_id, trial_number=1):
    """Attributes P02 must put on the actual root span, not a synthetic trace."""
    _, run, binding = _bound_run(dataset, run_id)
    link = _example(binding, run, snapshot_row_id, trial_number)
    return {
        "fi.experiment.run_id": str(run.id),
        "fi.experiment.example_id": str(link.version_item.example_id),
        "fi.experiment.trial_number": trial_number,
        "fi.experiment.execution_id": str(binding.execution_id),
    }


@force_primary()
@transaction.atomic
def prompt_execution_config(*, dataset, run_id):
    """Private worker config; never serialize this through detail/results APIs.

    Execute only these returned values, not a second EPC resolution. Changes
    after admission fail closed instead of running under an incorrect identity.
    """
    from model_hub.models.experiments import ExperimentPromptConfig
    from model_hub.services.native_run_binding import native_prompt_config

    _, run, binding = _bound_run(dataset, run_id)
    if run.task_kind != "prompt":
        raise RunWriteError("native run is not a prompt execution")
    native = ExperimentPromptConfig.no_workspace_objects.select_for_update().filter(
        pk=binding.native_config_id, experiment_dataset_id=binding.variant_id, deleted=False,
        prompt_version_id=run.prompt_version_id,
    ).first()
    if native is None:
        raise RunWriteError("native prompt configuration is unavailable")
    resolved = native_prompt_config(native)
    digest = _digest({"prompt_version_id": str(native.prompt_version_id), "effective": resolved})
    if digest != run.metadata["native_identity"].get("native_config_hash"):
        raise RunWriteError("native prompt configuration changed after admission")
    return resolved


@force_primary()
@transaction.atomic
def evaluation_output(*, dataset, run_id, snapshot_row_id):
    """Read only the exact immutable parent receipt bound at eval-only admission."""
    from model_hub.services.native_run_binding import native_output_sources
    dataset, run, binding = _bound_run(dataset, run_id)
    identity = run.metadata.get("native_identity", {})
    if identity.get("execution_mode") != "eval_only":
        raise RunWriteError("run is not an eval-only execution")
    link = _example(binding, run, snapshot_row_id, 1)
    ref = identity.get("evaluation_sources", {}).get(str(link.snapshot_row_id))
    if not ref:
        raise RunWriteError("eval-only output identity is missing")
    native_output_sources(binding.snapshot, binding.variant, binding.output_column, [link],
                          pinned={str(link.snapshot_row_id): ref})
    item = ExperimentRunItem.no_workspace_objects.get(pk=ref["item_id"], run_id=ref["run_id"], deleted=False)
    return {"output": item.output, "error": item.error, "status": item.status}
