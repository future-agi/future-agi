"""Native worker write-through into the existing receipted run item store.

P02 calls start at actual dispatch and record after execution AND evaluation.
Classic Cell/EDT writes stay in their existing worker code. This module never
reads a Cell to manufacture results and never claims admission succeeded.
"""

from contextlib import contextmanager

from django.db import transaction

from model_hub.models.experiments import ExperimentRun, ExperimentRunItem
from model_hub.models.native_experiment_runs import (
    NativeExampleBinding,
    NativeRunBinding,
)
from model_hub.services.experiment_run_writes import (
    _RUN_KEY,
    RunWriteError,
    _digest,
    _planned,
    _uuid,
    claim_run_item,
    finalize_run,
    record_run_item_result,
    transition_run,
    verified_prompt_evidence,
)
from model_hub.services.native_run_binding import (
    BoundExample,
    live_dataset,
    run_examples,
    validate_run_references,
    validate_snapshot,
)
from tfc.routers import force_primary


def _bound_run(dataset, run_id):
    dataset = live_dataset(dataset, lock=True)
    run = (
        ExperimentRun.no_workspace_objects.select_for_update(of=("self",))
        .select_related(
            "dataset_version",
            "experiment",
        )
        .filter(
            pk=_uuid(run_id),
            dataset_version__dataset=dataset,
            dataset_version__deleted=False,
            deleted=False,
        )
        .first()
    )
    if run is None or run.experiment_id is None or run.experiment.deleted:
        raise RunWriteError("native run was not found in the authorized dataset")
    binding = (
        NativeRunBinding.objects.select_related(
            "snapshot__dataset_version",
            "snapshot__snapshot_dataset",
            "variant",
            "output_column",
        )
        .filter(run=run)
        .first()
    )
    if (
        binding is None
        or binding.snapshot.experiment_id != run.experiment_id
        or run.experiment.dataset_id != dataset.id
        or (
            run.task_kind != "external"
            and binding.snapshot.dataset_version_id != run.dataset_version_id
        )
        or binding.variant.deleted
        or binding.variant.experiment_id != run.experiment_id
        or binding.output_column.deleted
        or binding.output_column.dataset_id != binding.snapshot.snapshot_dataset_id
    ):
        raise RunWriteError("native run binding is unavailable or inconsistent")
    validate_snapshot(binding.snapshot, dataset, run.experiment)
    validate_run_references(binding, run, dataset)
    return dataset, run, binding


@contextmanager
def stored_evaluation_write(dataset, run, *, row_ids=(), pin=None):
    """Current HTTP eval-only authority; locks cover writes, never evaluation."""
    if (
        run.task_kind != "external"
        or run.metadata["native_identity"].get("execution_mode") != "eval_only"
    ):
        yield
        return
    from model_hub.models.choices import StatusType
    from model_hub.services.experiment_submit import _locked_native_admission
    from model_hub.views.experiment_runner import _native_dispatch

    with force_primary(), transaction.atomic():
        experiment, snapshot, state = _locked_native_admission(run.experiment_id)
        context = (
            state.get("workflow_args", {})
            .get("native_runs", {})
            .get(run.metadata["native_identity"]["native_config_id"], {})
        )
        if (
            snapshot is None
            or experiment.status
            not in {StatusType.RUNNING.value, StatusType.COMPLETED.value}
            or state.get("state") != "accepted"
            or not state.get("workflow_id")
            or context.get("run_id") != str(run.pk)
            or state.get("admission_id")
            != run.metadata["native_identity"]["execution_id"]
        ):
            raise RunWriteError("Stored evaluation was stopped or superseded")
        checked_dataset, current, binding = _native_dispatch(
            experiment.pk, context, full_plan=True
        )
        if (
            checked_dataset.pk != dataset.pk
            or current.status not in {"queued", "running"}
            or current.completed_at is not None
            or str(binding.execution_id) != state["admission_id"]
            or (pin is not None and pin not in current.evaluator_versions)
        ):
            raise RunWriteError("Stored evaluation no longer owns these writes")
        listed = set(
            map(
                str,
                experiment.user_eval_template_ids.filter(deleted=False).values_list(
                    "pk", flat=True
                ),
            )
        )
        if {entry["metric_id"] for entry in current.evaluator_versions} - listed:
            raise RunWriteError(
                "Stored evaluator is no longer listed on this experiment"
            )
        for row_id in row_ids:
            link = _example(binding, current, row_id, 1)
            if (
                ExperimentRunItem.no_workspace_objects.select_for_update()
                .filter(
                    run=current,
                    example_id=link.version_item.example_id,
                    trial_number=1,
                    status__in={"completed", "failed", "skipped"},
                )
                .exists()
            ):
                raise RunWriteError("Stored evaluation row is already terminal")
        yield


def _example(binding, run, snapshot_row_id, trial_number):
    if type(trial_number) is not int or trial_number < 1:
        raise RunWriteError("trial number must be a positive integer")
    if run.task_kind == "external":
        for link in run_examples(binding, run):
            if str(link.snapshot_row_id) == _uuid(snapshot_row_id) and (
                str(link.version_item.example_id),
                trial_number,
            ) in _planned(run):
                return link
        raise RunWriteError("native row/trial is not in the pinned run plan")
    link = (
        NativeExampleBinding.objects.select_related("version_item", "snapshot_row")
        .filter(
            snapshot=binding.snapshot,
            snapshot_row_id=_uuid(snapshot_row_id),
            snapshot_row__deleted=False,
            version_item__deleted=False,
            snapshot_row__dataset_id=binding.snapshot.snapshot_dataset_id,
            version_item__dataset_version_id=run.dataset_version_id,
        )
        .first()
    )
    if link is None or (
        str(link.version_item.example_id),
        trial_number,
    ) not in _planned(run):
        raise RunWriteError("native row/trial is not in the pinned run plan")
    return BoundExample(
        link.source_row_id, link.snapshot_row, link.version_item, link.row_order
    )


def _scope(dataset, run):
    return {
        "run_id": run.id,
        "organization_id": dataset.organization_id,
        "workspace_id": dataset.workspace_id,
    }


def _key(attempt_key):
    if (
        not isinstance(attempt_key, str)
        or not attempt_key
        or len(attempt_key) > 255
        or attempt_key.strip() != attempt_key
    ):
        raise RunWriteError(
            "attempt key must be a stable nonempty string of at most 255 characters"
        )
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
def start_attempt(
    *, dataset, run_id, snapshot_row_id, attempt_key, trial_number=1, exclusive=False
):
    """Replay terminal work; exclusive in-flight claims never expire or steal.

    A lost owner's unknown provider outcome requires reconciliation, not another
    paid invocation. The bound run lock serializes claim creation and replay.
    """
    dataset, run, binding = _bound_run(dataset, run_id)
    # Eval-only ownership is durable; callers cannot opt back into replay.
    exclusive = exclusive or (
        run.metadata["native_identity"].get("execution_mode") == "eval_only"
    )
    link = _example(binding, run, snapshot_row_id, trial_number)
    key = _key(attempt_key)
    # Existing terminal claims must replay after the run seals too; the base
    # helper deliberately only admits new claims on an executing run.
    existing = ExperimentRunItem.no_workspace_objects.filter(
        run=run,
        example_id=link.version_item.example_id,
        trial_number=trial_number,
    ).first()
    if existing:
        receipt = (existing.metadata or {}).get(_RUN_KEY, {}).get("claim_receipt", {})
        if receipt.get("receipt_key") == key:
            if exclusive and existing.status not in {"completed", "failed", "skipped"}:
                raise RunWriteError("native attempt is already in progress")
            return existing
        raise RunWriteError("native attempt is already claimed with another key")
    if run.status == "queued":
        run = transition_run(**_scope(dataset, run), status="running")
    item = claim_run_item(
        **_scope(dataset, run),
        example_id=link.version_item.example_id,
        trial_number=trial_number,
        receipt_key=key,
    )
    # Dataset example metadata is caller data, never a worker execution receipt.
    # Only the graph recorder may establish this reserved checkpoint after claim.
    if "native_graph" in (item.metadata or {}):
        item.metadata = {k: v for k, v in item.metadata.items() if k != "native_graph"}
        item.save(update_fields=["metadata", "updated_at"])
    return item


@force_primary()
@transaction.atomic
def record_attempt(
    *,
    dataset,
    run_id,
    snapshot_row_id,
    attempt_key,
    status,
    trial_number=1,
    output=None,
    error=None,
    trace_id=None,
    root_span_id=None,
    latency_ms=None,
    token_usage=None,
    cost=None,
    score_refs=(),
    prompt_evidence=None,
):
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
    item = (
        ExperimentRunItem.no_workspace_objects.select_for_update()
        .filter(
            run=run,
            example_id=link.version_item.example_id,
            trial_number=trial_number,
            deleted=False,
        )
        .first()
    )
    if (
        item is None
        or (item.metadata or {})
        .get(_RUN_KEY, {})
        .get("claim_receipt", {})
        .get("receipt_key")
        != key
    ):
        raise RunWriteError("native result requires its actual matching attempt claim")
    if status == "completed":
        required = {
            (str(pin["metric_id"]), str(pin["evaluator_version_id"]))
            for pin in run.evaluator_versions
        }
        if not isinstance(score_refs, (list, tuple)) or any(
            not isinstance(ref, dict) for ref in score_refs
        ):
            raise RunWriteError("score references must be explicit evaluator evidence")
        supplied = {
            (
                str(ref.get("metric_id")),
                str(ref.get("evaluator_version_id", ref.get("version_id"))),
            )
            for ref in score_refs
        }
        if required != supplied:
            raise RunWriteError(
                "completed attempt must include every pinned evaluator outcome"
            )
    return record_run_item_result(
        item_id=item.id,
        organization_id=dataset.organization_id,
        workspace_id=dataset.workspace_id,
        status=status,
        receipt_key=key,
        output=output,
        error=error,
        trace_id=trace_id,
        root_span_id=root_span_id,
        latency_ms=latency_ms,
        token_usage=token_usage,
        cost=cost,
        score_refs=score_refs,
        prompt_evidence=prompt_evidence,
    )


@force_primary()
@transaction.atomic
def finish_native_run(*, dataset, run_id):
    dataset, run, binding = _bound_run(dataset, run_id)
    # Also revalidate live pinned membership on sealed replay.
    run_examples(binding, run)
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
    identity = run.metadata["native_identity"]
    if identity.get("execution_mode") == "eval_only" and (
        run.task_kind == "external"
        or (
            identity.get("selected_parent_run_id")
            and identity.get("stored_evidence_only") is True
        )
    ):
        # Admission sealed prompt independence; no current producer is needed.
        return None
    if run.task_kind != "prompt":
        raise RunWriteError("native run is not a prompt execution")
    native = (
        ExperimentPromptConfig.no_workspace_objects.select_for_update()
        .filter(
            pk=binding.native_config_id,
            experiment_dataset_id=binding.variant_id,
            deleted=False,
            prompt_version_id=run.prompt_version_id,
        )
        .first()
    )
    if native is None:
        raise RunWriteError("native prompt configuration is unavailable")
    resolved = native_prompt_config(native)
    digest = _digest(
        {"prompt_version_id": str(native.prompt_version_id), "effective": resolved}
    )
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
    native_output_sources(
        binding.snapshot,
        binding.variant,
        binding.output_column,
        [link],
        pinned={str(link.snapshot_row_id): ref},
    )
    item = ExperimentRunItem.no_workspace_objects.get(
        pk=ref["item_id"], run_id=ref["run_id"], deleted=False
    )
    return {
        "prompt_evidence": verified_prompt_evidence(item),
        "output": item.output,
        "error": item.error,
        "status": item.status,
        **({"input": item.input} if identity.get("stored_evidence_only") else {}),
    }
