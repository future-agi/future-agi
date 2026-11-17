"""Canonical HTTP receipts alongside the existing admission-fenced Cell writes.

Only immutable snapshot members have canonical identities. HTTP-only appended
rows retain their classic execution path, including when retried. No reader
calls this module to construct historical evidence.
"""

import logging
import math
import re
from contextlib import contextmanager
from copy import deepcopy
from uuid import NAMESPACE_URL, UUID, uuid5

from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.utils import timezone

from model_hub.models.choices import CellStatus, StatusType
from model_hub.models.evals_metric import EvalTemplateVersion, UserEvalMetric
from model_hub.models.experiments import ExperimentRun, ExperimentRunItem
from model_hub.models.native_experiment_runs import NativeRunBinding
from model_hub.services.experiment_run_creation import create_experiment_run
from model_hub.services.experiment_run_writes import (
    _RUN_KEY,
    RunWriteError,
    _digest,
    _planned,
    claim_run_item,
    finalize_run,
    record_run_item_result,
    transition_run,
)
from model_hub.services.experiment_summary import schedule_run_summaries
from model_hub.services.native_run_binding import (
    native_evaluator_pins,
    native_output_sources,
    native_workspace,
    snapshot_examples,
    validate_snapshot,
)
from tfc.routers import force_primary

logger = logging.getLogger(__name__)


def _recording_runs(experiment_id, admission_id, source_id=None):
    bindings = NativeRunBinding.objects.filter(
        snapshot__experiment_id=experiment_id,
        execution_id=admission_id,
        run__task_kind="external",
        run__deleted=False,
    )
    if source_id is not None:
        bindings = bindings.filter(variant_id=source_id)
    return (
        ExperimentRun.no_workspace_objects.select_for_update()
        .filter(pk__in=bindings.values("run_id"), completed_at__isnull=True)
        .order_by("pk")
    )


def _mark_incomplete(experiment_id, admission_id, source_id, failure, terminal):
    # This runs only after the failed savepoint has rolled back. Never inspect
    # or repair Cells here, and never manufacture a successful missing receipt.
    with transaction.atomic():
        for run in _recording_runs(experiment_id, admission_id, source_id):
            metadata = deepcopy(run.metadata)
            metadata.setdefault(_RUN_KEY, {})["http_recording_error"] = failure
            run.metadata = metadata
            fields = ["metadata", "updated_at"]
            if terminal:
                run.status = "failed"
                run.completed_at = timezone.now()
                fields.extend(["status", "completed_at"])
                ExperimentRunItem.no_workspace_objects.filter(
                    run=run, status__in=["pending", "running"], deleted=False
                ).update(
                    status="failed",
                    error={
                        "code": "canonical_recording_incomplete",
                        "message": "HTTP evidence could not be fully recorded.",
                    },
                    updated_at=run.completed_at,
                )
            run.save(update_fields=fields)
            if terminal:
                schedule_run_summaries(run)


@contextmanager
def canonical_http_write(
    experiment_id, admission_id, *, operation, source_id=None, terminal=False
):
    """Optional evidence never rolls back an admission-fenced classic write."""
    try:
        with transaction.atomic():
            yield
    except Exception as exc:
        # Do not log raw exception messages, endpoint payloads or evaluator data.
        if (
            isinstance(exc, RunWriteError)
            and str(exc) == "native evaluator version is unavailable in this tenant"
        ):
            reason = "evaluator_version_unavailable"
        elif isinstance(exc, (RunWriteError, ValidationError)):
            reason = "canonical_validation_refused"
        elif isinstance(exc, DatabaseError):
            reason = "canonical_storage_error"
        else:
            reason = "canonical_operation_failed"
        failure = {
            "operation": operation,
            "reason": reason,
            "error_type": type(exc).__name__,
        }
        logger.warning(
            "HTTP classic execution retained; canonical %s failed (%s, %s)",
            operation,
            reason,
            type(exc).__name__,
        )
        try:
            _mark_incomplete(experiment_id, admission_id, source_id, failure, terminal)
        except Exception as marker_error:
            logger.warning(
                "HTTP canonical incomplete marker unavailable (%s)",
                type(marker_error).__name__,
            )


def trace_metadata(payload):
    """Accept actual trace identifiers only, never arbitrary response metadata."""
    if not isinstance(payload, dict):
        return {}
    result = {}
    for key, length in (("trace_id", 32), ("root_span_id", 16)):
        value = payload.get(key)
        if (
            isinstance(value, str)
            and re.fullmatch(rf"[0-9a-fA-F]{{{length}}}", value)
            and int(value, 16)
        ):
            result[key] = value
    if "trace_id" not in result:
        result.pop("root_span_id", None)
    return result


def _scope(experiment, run):
    return {
        "run_id": run.pk,
        "organization_id": experiment.dataset.organization_id,
        "workspace_id": experiment.dataset.workspace_id,
    }


def validate_http_binding(binding, run, dataset, *, retired_ok=False):
    """An actual server binding, not external metadata, authorizes these pins."""
    from model_hub.services.native_run_binding import run_examples

    identity = (run.metadata or {}).get("native_identity", {})
    if (
        run.task_kind != "external"
        or run.prompt_version_id is not None
        or run.agent_version_id is not None
        or not isinstance(identity, dict)
        or identity.get("execution_mode") not in {"http", "eval_only"}
        or _digest(identity) != binding.request_hash
        or binding.run_id != run.pk
        or binding.native_config_id != binding.variant_id
        or binding.variant.deleted
        or binding.variant.experiment_id != run.experiment_id
        or run.experiment_id != binding.snapshot.experiment_id
        or run.experiment.dataset_id != dataset.pk
        or run.experiment.deleted
        or binding.output_column.deleted
        or binding.output_column.dataset_id != binding.snapshot.snapshot_dataset_id
        or (binding.variant.legacy_prompt_config or {}).get("target_type") != "http"
        or any(
            identity.get(key) != str(value)
            for key, value in (
                ("variant_id", binding.variant_id),
                ("native_config_id", binding.native_config_id),
                ("output_column_id", binding.output_column_id),
                ("snapshot_id", binding.snapshot_id),
                ("execution_id", binding.execution_id),
            )
        )
    ):
        raise RunWriteError("HTTP binding does not match its canonical run")
    validate_snapshot(binding.snapshot, dataset, run.experiment)
    links = run_examples(binding, run)
    if identity["execution_mode"] == "eval_only":
        if (
            not links
            or not run.evaluator_versions
            or set(identity.get("evaluation_sources", {}))
            != set(identity.get("snapshot_row_ids", []))
            or (
                not retired_ok
                and identity.get("policy_hash")
                != _digest(run.experiment.run_policy or {})
            )
        ):
            raise RunWriteError(
                "HTTP stored evaluation identity is incomplete or stale"
            )
        native_output_sources(
            binding.snapshot,
            binding.variant,
            binding.output_column,
            links,
            pinned=identity["evaluation_sources"],
        )
    scope = native_workspace(dataset, identity.get("authorization_workspace_id"))
    metrics = list(
        UserEvalMetric._base_manager.select_related("template").filter(
            pk__in=[pin["metric_id"] for pin in run.evaluator_versions]
        )
    )
    if retired_ok:
        # Historical provenance belongs to the saved version, not a mutable
        # metric's current template. Keep the real metric for scope checks.
        pins = {pin["metric_id"]: pin for pin in run.evaluator_versions}
        versions = {
            str(version.pk): version
            for version in EvalTemplateVersion._base_manager.select_related(
                "eval_template"
            ).filter(pk__in=[pin["evaluator_version_id"] for pin in pins.values()])
        }
        for metric in metrics:
            pin = pins[str(metric.pk)]
            version = versions.get(pin["evaluator_version_id"])
            if version is None or str(version.eval_template_id) != pin["template_id"]:
                raise RunWriteError("HTTP evaluator pin does not match its template")
            # This instance is only used for validation; no metric is saved.
            metric.template = version.eval_template
    native_evaluator_pins(
        metrics,
        dataset,
        binding.snapshot,
        pinned=run.evaluator_versions,
        workspace=scope,
        retired_ok=retired_ok,
        check_definition=identity["execution_mode"] == "eval_only"
        and not retired_ok
        and run.completed_at is None,
    )


def bind_http_run(
    experiment,
    source,
    snapshot,
    column,
    admission_id,
    state,
    *,
    evaluator_metric_ids=None,
    generation=None,
):
    """Called in the submission transaction, before admission or dispatch."""
    eval_only = evaluator_metric_ids is not None
    existing = NativeRunBinding.objects.filter(
        variant=source, execution_id=admission_id
    ).first()
    if existing is not None:
        identity = existing.run.metadata.get("native_identity", {})
        if identity.get("execution_mode") != ("eval_only" if eval_only else "http"):
            raise RunWriteError(
                "HTTP admission already names a different execution mode"
            )
        if eval_only and {
            pin["metric_id"] for pin in existing.run.evaluator_versions
        } != set(evaluator_metric_ids):
            raise RunWriteError(
                "Stored evaluation admission already names different targets"
            )
        return existing.run
    from model_hub.services.http_experiment_source import _http_rows

    validate_snapshot(snapshot, experiment.dataset, experiment)
    generation = generation or snapshot.dataset_version
    links = (
        snapshot_examples(snapshot)
        if generation.pk == snapshot.dataset_version_id
        else _http_rows(snapshot, generation=generation)
    )
    selected = (
        None
        if eval_only
        else source.legacy_prompt_config["_http_execution"].get("row_ids")
    )
    if selected is not None:
        links = [link for link in links if str(link.snapshot_row_id) in selected]
        if {str(link.snapshot_row_id) for link in links} != set(selected):
            raise RunWriteError("HTTP rows are outside the chosen generation")
    if eval_only and not links:
        raise RunWriteError("Stored evaluation requires immutable snapshot rows")
    if not links:
        # An append-only admission must not acquire an invented version member
        # or an empty canonical plan. Its HTTP execution still proceeds.
        return None
    scope = native_workspace(
        experiment.dataset,
        state.get("http_authorization_workspace_id"),
        actor=experiment.user,
    )
    metric_ids = experiment.user_eval_template_ids.through.objects.filter(
        experimentstable_id=experiment.pk,
        userevalmetric__deleted=False,
    ).values_list("userevalmetric_id", flat=True)
    metrics = list(
        UserEvalMetric.no_workspace_objects.filter(pk__in=metric_ids, deleted=False)
        .select_related("template")
        .order_by("id")
    )
    if eval_only:
        metrics = [
            metric for metric in metrics if str(metric.pk) in evaluator_metric_ids
        ]
        if not metrics or {str(metric.pk) for metric in metrics} != set(
            evaluator_metric_ids
        ):
            raise RunWriteError("Stored evaluation metrics are outside this experiment")
    pins = native_evaluator_pins(metrics, experiment.dataset, snapshot, workspace=scope)
    source_config = {
        key: deepcopy(source.legacy_prompt_config[key])
        for key in ("target_type", "http", "variable_mapping")
        if key in source.legacy_prompt_config
    }
    settings = {"trials": 1}
    identity = {
        "execution_mode": "http",
        "variant_id": str(source.pk),
        "native_config_id": str(source.pk),
        "output_column_id": str(column.pk),
        "output_column_name": column.name,
        "output_column_type": column.data_type,
        "snapshot_id": str(snapshot.pk),
        "execution_id": str(admission_id),
        "snapshot_row_ids": [str(link.snapshot_row_id) for link in links],
        "http_examples": [
            {
                "snapshot_row_id": str(link.snapshot_row_id),
                "source_row_id": str(link.source_row_id),
                "version_item_id": str(link.version_item.pk),
                "example_id": str(link.version_item.example_id),
                "content_hash": link.version_item.content_hash,
                "row_order": link.row_order,
            }
            for link in links
        ],
        "native_config_hash": _digest(source_config),
        "authorization_workspace_id": str(scope.pk) if scope else None,
    }
    if eval_only:
        identity.update(
            execution_mode="eval_only",
            evaluation_sources=native_output_sources(snapshot, source, column, links),
            policy_hash=_digest(experiment.run_policy or {}),
        )
    run = create_experiment_run(
        run_id=uuid5(NAMESPACE_URL, f"futureagi:http-run:{source.pk}:{admission_id}"),
        dataset_version=generation,
        created_by=experiment.user,
        task_kind="external",
        experiment=experiment,
        expected_items=[(link.version_item.example_id, 1) for link in links],
        metadata={"name": source.name, "native_identity": identity},
        authorized_workspace=scope,
    )
    run.evaluator_versions = pins
    run.full_clean()
    run.save(update_fields=["evaluator_versions", "updated_at"])
    binding = NativeRunBinding.objects.create(
        run=run,
        snapshot=snapshot,
        variant=source,
        execution_id=UUID(str(admission_id)),
        native_config_id=source.pk,
        output_column=column,
        requested_settings={},
        effective_settings=settings,
        config={},
        request_hash=_digest(identity),
    )
    validate_http_binding(binding, run, experiment.dataset)
    return run


def queue_http_runs(experiment, admission_id):
    """Queue only after the request-side workflow acknowledgement succeeds."""
    for binding in NativeRunBinding.objects.select_related("run").filter(
        snapshot__experiment=experiment,
        execution_id=admission_id,
        run__task_kind="external",
        run__deleted=False,
    ):
        if binding.run.status == "draft":
            transition_run(**_scope(experiment, binding.run), status="queued")


def _bound(experiment, source, row, column, attempt_id):
    from model_hub.services.http_experiment_source import http_attempt_id

    admission_id = attempt_id.split(":", 1)[0]
    try:
        UUID(admission_id)
    except ValueError:
        if NativeRunBinding.objects.filter(variant=source).exists():
            raise RunWriteError("HTTP receipt has no admission identity") from None
        return None  # Older unbound callers retain their classic attempt keys.
    binding = (
        NativeRunBinding.objects.select_related(
            "run__experiment",
            "snapshot__dataset_version",
            "snapshot__snapshot_dataset",
            "variant",
            "output_column",
        )
        .filter(variant=source, execution_id=admission_id)
        .first()
    )
    if binding is None:
        return None
    if attempt_id != http_attempt_id(admission_id, source.pk, row.pk):
        raise RunWriteError("HTTP receipt does not identify this source and row")
    run = ExperimentRun.no_workspace_objects.select_for_update().get(pk=binding.run_id)
    if run.completed_at is not None or run.deleted:
        raise RunWriteError("HTTP canonical execution is already terminal")
    if binding.output_column_id != column.pk:
        raise RunWriteError("HTTP canonical output column does not match")
    validate_http_binding(binding, run, experiment.dataset)
    from model_hub.services.native_run_binding import run_examples

    link = next(
        (link for link in run_examples(binding, run) if link.snapshot_row_id == row.pk),
        None,
    )
    if link is None:
        raise RunWriteError("HTTP row is outside the pinned canonical plan")
    if (
        link.version_item.deleted
        or link.version_item.dataset_version_id != run.dataset_version_id
        or (str(link.version_item.example_id), 1) not in _planned(run)
    ):
        raise RunWriteError("HTTP row is outside the pinned canonical plan")
    return binding, run, link


def claim_http_item(experiment, source, row, column, attempt_id):
    """The caller holds the same active-admission fence as the classic claim."""
    bound = _bound(experiment, source, row, column, attempt_id)
    if bound is None:
        return
    _, run, link = bound
    if run.status == "queued":
        run = transition_run(**_scope(experiment, run), status="running")
    item = claim_run_item(
        **_scope(experiment, run),
        example_id=link.version_item.example_id,
        trial_number=1,
        receipt_key=attempt_id,
    )
    # Keep staging inside the reserved writer envelope. Example metadata must
    # never be treated as an HTTP output or evaluator receipt.
    metadata = deepcopy(item.metadata)
    metadata[_RUN_KEY]["http"] = {"evaluations": {}}
    item.metadata = metadata
    item.save(update_fields=["metadata", "updated_at"])


def _claimed_item(run, link, attempt_id):
    item = ExperimentRunItem.no_workspace_objects.select_for_update().get(
        run=run, example_id=link.version_item.example_id, trial_number=1, deleted=False
    )
    if (
        item.metadata.get(_RUN_KEY, {}).get("claim_receipt", {}).get("receipt_key")
        != attempt_id
    ):
        raise RunWriteError("HTTP result requires its matching canonical claim")
    return item


def _seal(experiment, run, item, attempt_id):
    evidence = item.metadata[_RUN_KEY]["http"]
    output = evidence.get("output")
    if output is None:
        return
    evaluations = evidence["evaluations"]
    pins = [pin["metric_id"] for pin in run.evaluator_versions]
    if output["error"] is None and set(evaluations) != set(pins):
        return
    refs = [evaluations[key] for key in pins] if output["error"] is None else []
    record_run_item_result(
        item_id=item.pk,
        organization_id=experiment.dataset.organization_id,
        workspace_id=experiment.dataset.workspace_id,
        receipt_key=attempt_id,
        status="failed" if output["error"] else "completed",
        output=output["output"],
        error=output["error"],
        trace_id=output.get("trace_id"),
        root_span_id=output.get("root_span_id"),
        latency_ms=output["latency_ms"],
        score_refs=refs,
    )


def record_http_output(experiment, source, row, column, attempt_id, result):
    """Record actual transport evidence in the classic result transaction."""
    bound = _bound(experiment, source, row, column, attempt_id)
    if bound is None:
        return
    _, run, link = bound
    item = _claimed_item(run, link, attempt_id)
    output = {
        "output": None if result["error"] else result["output"],
        "error": deepcopy(result["error"]),
        "latency_ms": result["latency_ms"],
        **trace_metadata(result),
    }
    metadata = deepcopy(item.metadata)
    evidence = metadata[_RUN_KEY]["http"]
    if "output" in evidence and evidence["output"] != output:
        raise RunWriteError("HTTP output receipt cannot be replaced")
    if "output" not in evidence:
        evidence["output"] = output
        item.metadata = metadata
        item.save(update_fields=["metadata", "updated_at"])
    _seal(experiment, run, item, attempt_id)


def _score(runner, value, response):
    """Normalize the worker result, not a later read of its formatted Cell."""
    if isinstance(value, str):
        try:
            import json

            value = json.loads(value)
        except (TypeError, ValueError):
            pass
    numeric = value.get("score") if isinstance(value, dict) else value
    if isinstance(numeric, bool):
        numeric = int(numeric)
    elif isinstance(numeric, str) and numeric in {"Passed", "Failed"}:
        numeric = (
            int(numeric == "Passed")
            if isinstance(response, dict) and isinstance(response.get("failure"), bool)
            else None
        )
    if isinstance(value, dict) and ("choice" in value or "choices" in value):
        from model_hub.utils.scoring import apply_choice_scores

        choices = value.get("choices", [value.get("choice")])
        if not isinstance(choices, list):
            return None
        scores = [
            apply_choice_scores(str(choice), runner._effective_choice_scores() or {})
            for choice in choices
        ]
        numeric = (
            sum(scores) / len(scores)
            if scores and all(type(score) in (int, float) for score in scores)
            else None
        )
    try:
        return (
            numeric
            if type(numeric) in (int, float) and math.isfinite(numeric)
            else None
        )
    except OverflowError:
        return None


def recorded_http_evaluation_rows(context, metric_id, row_ids):
    """Find committed row/metric receipts under the caller's HTTP write fence.

    Classic Cells are not checkpoints: only this admission's claimed items
    can suppress evaluation. Unbound and appended-only rows remain classic.
    """
    if context is None:
        return set()
    with canonical_http_write(
        context["experiment_id"],
        context["admission_id"],
        source_id=context["source_id"],
        operation="evaluation_checkpoint",
    ):
        return _recorded_http_evaluation_rows(context, metric_id, row_ids)
    return set()


def _recorded_http_evaluation_rows(context, metric_id, row_ids):
    from model_hub.services.http_experiment_source import http_attempt_id

    binding = (
        NativeRunBinding.objects.select_related("run")
        .filter(
            variant_id=context["source_id"],
            execution_id=context["admission_id"],
            snapshot__experiment_id=context["experiment_id"],
            snapshot__snapshot_dataset_id=context["dataset_id"],
            output_column_id=context["column_id"],
            run__experiment_id=context["experiment_id"],
            run__task_kind="external",
            run__deleted=False,
        )
        .first()
    )
    if binding is None:
        return set()
    metric_id = str(metric_id)
    pin = next(
        (
            pin
            for pin in binding.run.evaluator_versions
            if pin["metric_id"] == metric_id
        ),
        None,
    )
    if pin is None:
        return set()  # A later classic evaluator has no canonical checkpoint.
    from model_hub.services.native_run_binding import run_examples

    rows_by_example = {
        link.version_item.example_id: link.snapshot_row_id
        for link in run_examples(binding, binding.run)
        if str(link.snapshot_row_id) in set(map(str, row_ids))
    }
    recorded = set()
    for item in ExperimentRunItem.no_workspace_objects.filter(
        run_id=binding.run_id,
        example_id__in=rows_by_example,
        trial_number=1,
        deleted=False,
    ).only("example_id", "metadata"):
        row_id = rows_by_example[item.example_id]
        envelope = item.metadata.get(_RUN_KEY, {})
        outcome = envelope.get("http", {}).get("evaluations", {}).get(metric_id)
        if (
            isinstance(outcome, dict)
            and outcome.get("metric_id") == metric_id
            and outcome.get("evaluator_version_id") == pin["evaluator_version_id"]
            and outcome.get("status") in {"assessed", "not_assessed", "error"}
            and envelope.get("claim_receipt", {}).get("receipt_key")
            == http_attempt_id(context["admission_id"], binding.variant_id, row_id)
        ):
            recorded.add(str(row_id))
    return recorded


def record_http_evaluation(runner, row, response, value, status):
    """Called only after a successful score Cell write, within its HTTP fence."""
    context = runner.source_configs.get("http_context")
    if context is None:
        return
    from model_hub.services.http_experiment_source import (
        _http_execution,
        http_attempt_id,
    )

    experiment, _, source, _, column = _http_execution(
        context["experiment_id"], context["source_id"], context["admission_id"]
    )
    attempt_id = http_attempt_id(context["admission_id"], source.pk, row.pk)
    bound = _bound(experiment, source, row, column, attempt_id)
    if bound is None:
        return
    binding, run, link = bound
    pin = next(
        (
            pin
            for pin in run.evaluator_versions
            if pin["metric_id"] == str(runner.user_eval_metric_id)
        ),
        None,
    )
    if pin is None:
        raise RunWriteError("HTTP evaluator is outside the admitted pins")
    scope = native_workspace(
        experiment.dataset,
        run.metadata["native_identity"].get("authorization_workspace_id"),
    )
    native_evaluator_pins(
        [runner.user_eval_metric],
        experiment.dataset,
        binding.snapshot,
        pinned=[pin],
        workspace=scope,
        check_definition=True,
    )
    resolved = (
        runner._resolved_version
        or EvalTemplateVersion.objects.resolve_for_metric(runner.user_eval_metric)
    )
    if (
        resolved is None
        or str(resolved.pk) != pin["evaluator_version_id"]
        or runner._runs_version(resolved) != pin["explicit_pin"]
    ):
        raise RunWriteError("HTTP evaluator ran a different version than its pin")
    item = _claimed_item(run, link, attempt_id)
    numeric = _score(runner, value, response)
    outcome = {
        "metric_id": pin["metric_id"],
        "evaluator_version_id": pin["evaluator_version_id"],
        "status": (
            "error"
            if status == CellStatus.ERROR.value
            else "assessed"
            if numeric is not None
            else "not_assessed"
        ),
    }
    if outcome["status"] == "assessed":
        outcome["value"] = numeric
    else:
        outcome["reason"] = (
            "evaluator_execution_failed"
            if outcome["status"] == "error"
            else "non_numeric_evaluator_output"
        )
    if isinstance(response, dict):
        explanation = response.get("reason", response.get("explanation"))
        if isinstance(explanation, str):
            outcome["explanation"] = explanation
    metadata = deepcopy(item.metadata)
    evidence = metadata[_RUN_KEY]["http"]
    previous = evidence["evaluations"].get(pin["metric_id"])
    if previous is not None:
        if previous != outcome:
            raise RunWriteError("HTTP evaluator receipt cannot be replaced")
        return
    if item.status != "running":
        raise RunWriteError("HTTP evaluator cannot amend a terminal item")
    evidence["evaluations"][pin["metric_id"]] = outcome
    item.metadata = metadata
    item.save(update_fields=["metadata", "updated_at"])
    _seal(experiment, run, item, attempt_id)


def finish_http_run(experiment, source, admission_id):
    binding = (
        NativeRunBinding.objects.select_related("run")
        .filter(variant=source, execution_id=admission_id, run__deleted=False)
        .first()
    )
    if binding is None or binding.run.completed_at is not None:
        return
    if binding.run.metadata.get(_RUN_KEY, {}).get("http_recording_error"):
        _end_run(experiment, binding.run, stopped=False)
        return
    # Missing callbacks are incomplete evidence, not a classic execution error.
    finalize_run(**_scope(experiment, binding.run))


def _end_run(experiment, run, *, stopped):
    if run.completed_at is not None:
        return
    for item in ExperimentRunItem.no_workspace_objects.select_for_update().filter(
        run=run, status="running", deleted=False
    ):
        envelope = item.metadata[_RUN_KEY]
        evidence = envelope.get("http", {})
        output = evidence.get("output", {})
        record_run_item_result(
            item_id=item.pk,
            organization_id=experiment.dataset.organization_id,
            workspace_id=experiment.dataset.workspace_id,
            receipt_key=envelope["claim_receipt"]["receipt_key"],
            status="failed",
            output=output.get("output"),
            error={
                "code": "cancelled" if stopped else "execution_interrupted",
                "message": (
                    "HTTP execution was stopped."
                    if stopped
                    else "HTTP execution ended before its evidence completed."
                ),
            },
            latency_ms=output.get("latency_ms"),
            trace_id=output.get("trace_id"),
            root_span_id=output.get("root_span_id"),
            score_refs=list(evidence.get("evaluations", {}).values()),
        )
    if stopped:
        transition_run(**_scope(experiment, run), status="cancelled")
    else:
        # Incomplete plans cannot pass finalize_run. Seal the failure explicitly
        # without inventing outcomes for requests/evaluations that never ran.
        run.status = "failed"
        run.completed_at = timezone.now()
        run.save(update_fields=["status", "completed_at", "updated_at"])
        schedule_run_summaries(run)


def end_http_runs(experiment, admission_id, *, stopped):
    """Terminal evidence only; classic source phases remain owned by HTTP."""
    from model_hub.services.experiment_submit import state_of

    if state_of(experiment).get("admission_id") != admission_id:
        return
    for run in _recording_runs(experiment.pk, admission_id):
        _end_run(experiment, run, stopped=stopped)


def end_superseded_http_runs(experiment, source, admission_id):
    """A new source admission retires only that source's older open evidence."""
    older = NativeRunBinding.objects.filter(
        snapshot__experiment=experiment,
        variant=source,
        run__task_kind="external",
        run__deleted=False,
        run__completed_at__isnull=True,
    ).exclude(execution_id=admission_id)
    for previous in older.order_by("run_id"):
        with canonical_http_write(
            experiment.pk,
            previous.execution_id,
            source_id=source.pk,
            operation="supersede",
            terminal=True,
        ):
            run = ExperimentRun.no_workspace_objects.select_for_update().get(
                pk=previous.run_id
            )
            _end_run(experiment, run, stopped=True)


@force_primary()
@transaction.atomic
def stop_http_runs(experiment_id):
    from model_hub.services.experiment_submit import _locked_native_admission

    with canonical_http_write(experiment_id, None, operation="stop", terminal=True):
        experiment, _, state = _locked_native_admission(experiment_id)
        if experiment.status == StatusType.CANCELLED.value and state.get(
            "admission_id"
        ):
            with canonical_http_write(
                experiment.pk, state["admission_id"], operation="stop", terminal=True
            ):
                end_http_runs(experiment, state["admission_id"], stopped=True)
