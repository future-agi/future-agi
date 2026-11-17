"""Whole-run evaluation through the existing isolated, metered eval runner."""

import json
import math
import uuid
from copy import deepcopy
from functools import partial
from types import SimpleNamespace

from django.db import transaction
from django.utils import timezone

from evaluations.run_settings import run_settings
from model_hub.models.experiment_summary import (
    ExperimentSummaryBinding,
    ExperimentSummaryResult,
)
from model_hub.models.experiments import ExperimentRun, ExperimentRunItem
from model_hub.services.experiment_run_writes import (
    _RUN_KEY,
    RunWriteError,
    _canonical,
    _planned,
)
from tfc.routers import force_primary

TERMINAL = {"completed", "partial", "failed", "cancelled"}
MAX_ROWS = 100000
MAX_INPUT_BYTES = 16 * 1024 * 1024


class SummaryError(ValueError):
    pass


def create_binding(*, experiment, template, version, name, mapping):
    """Admission receives an authorized template/version, never a live default."""
    from model_hub.services.external_run_evaluations import (
        _TEMPLATE_FIELDS,
        _VERSION_FIELDS,
    )

    if version.eval_template_id != template.id or version.deleted:
        raise SummaryError("Version does not belong to the evaluator.")
    if template.template_type != "single" or template.eval_type not in {"code", "llm"}:
        raise SummaryError("Select a single code or prompt evaluator.")
    config = run_settings(deepcopy(version.config_snapshot or {}))
    if not config.get("eval_type_id") or config["eval_type_id"] == "AgentEvaluator":
        raise SummaryError("Select a saved non-agent evaluator definition.")
    if config.get("run_prompt_column") or any(
        isinstance(example, dict)
        and "id" in example
        and "input" not in example
        and "output" not in example
        for example in config.get("few_shot_examples") or []
    ):
        raise SummaryError("Summary evaluators require literal, versioned inputs.")
    if (
        not mapping
        or len(mapping) > 20
        or any(
            not isinstance(key, str)
            or not key
            or len(key) > 100
            or value
            not in {
                "input.rows",
                "input.outputs",
                "input.expected",
                "input.scores",
                "input.run",
            }
            for key, value in mapping.items()
        )
    ):
        raise SummaryError("Map parameters to rows, outputs, expected, scores or run.")
    binding_id = uuid.uuid4()
    pin = {name: deepcopy(getattr(version, name)) for name in _VERSION_FIELDS}
    pin.update(
        {
            "metric_id": str(binding_id),
            "template_id": str(template.id),
            "evaluator_version_id": str(version.id),
            "binding_model": version.model,
            "binding_config": {"mapping": deepcopy(mapping), "config": {}},
        }
    )
    execution = {name: deepcopy(getattr(template, name)) for name in _TEMPLATE_FIELDS}
    # Do not overlay the live template's code/prompt onto an old version.
    execution["config"] = config
    for field in (
        "criteria",
        "model",
        "output_type_normalized",
        "pass_threshold",
        "choice_scores",
    ):
        execution[field] = deepcopy(getattr(version, field))
    if execution["output_type_normalized"] is None:
        raise SummaryError("Save a version with an explicit output type first.")
    dataset = experiment.dataset
    return ExperimentSummaryBinding.objects.create(
        id=binding_id,
        experiment=experiment,
        version=version,
        name=name,
        definition={
            "pin": pin,
            "template": execution,
            "mapping": deepcopy(mapping),
            "organization_id": str(dataset.organization_id),
            "workspace_id": str(dataset.workspace_id) if dataset.workspace_id else None,
        },
    )


def _live_run(run_id):
    return ExperimentRun.no_workspace_objects.select_related(
        "dataset_version__dataset",
        "experiment",
    ).filter(
        pk=run_id,
        deleted=False,
        experiment__deleted=False,
        dataset_version__deleted=False,
        dataset_version__dataset__deleted=False,
    )


def _check_scope(run, binding):
    dataset = run.dataset_version.dataset
    definition = binding.definition
    workspace = str(dataset.workspace_id) if dataset.workspace_id else None
    if (
        run.experiment_id != binding.experiment_id
        or run.experiment.dataset_id != dataset.id
        or str(dataset.organization_id) != definition["organization_id"]
        or workspace != definition["workspace_id"]
        or binding.version_id != uuid.UUID(definition["pin"]["evaluator_version_id"])
    ):
        raise SummaryError("Summary binding is outside the run scope.")
    if run.completed_at is None or run.status not in TERMINAL:
        raise SummaryError("Summary evaluation requires a sealed run.")


@force_primary()
@transaction.atomic
def request_summary(*, run, binding, retry_failed=False):
    run = _live_run(run.id).select_for_update(of=("self",)).first()
    if run is None:
        raise SummaryError("Experiment run is unavailable.")
    _check_scope(run, binding)
    result, _ = ExperimentSummaryResult.objects.get_or_create(
        run=run,
        binding=binding,
        version_id=binding.version_id,
    )
    result = ExperimentSummaryResult.objects.select_for_update().get(pk=result.pk)
    if result.status == "failed" and retry_failed:
        result.status = "pending"
        result.dispatched = False
        result.error_code = ""
        result.started_at = result.completed_at = None
        result.save(
            update_fields=[
                "status",
                "dispatched",
                "error_code",
                "started_at",
                "completed_at",
            ]
        )
    if result.status == "pending" and not result.dispatched:
        transaction.on_commit(partial(_dispatch, result.id))
    return result


def schedule_run_summaries(run):
    """Create durable work in the same transaction that seals the parent."""
    if not run.experiment_id or run.completed_at is None:
        return
    for binding in ExperimentSummaryBinding.objects.filter(
        experiment_id=run.experiment_id,
    ).order_by("id"):
        # Scope/version failures belong to the summary result, not the parent
        # run's terminal transaction. The worker validates before invocation.
        result, _ = ExperimentSummaryResult.objects.get_or_create(
            run=run,
            binding=binding,
            version_id=binding.version_id,
        )
        if result.status == "pending" and not result.dispatched:
            transaction.on_commit(partial(_dispatch, result.id))


@force_primary()
@transaction.atomic
def _dispatch(result_id):
    from temporalio.exceptions import WorkflowAlreadyStartedError

    from model_hub.tasks.user_evaluation import process_evaluation_single_task

    result = ExperimentSummaryResult.objects.select_for_update().get(pk=result_id)
    if result.status != "pending" or result.dispatched:
        return
    try:
        process_evaluation_single_task.apply_async(
            args=({"type": "summary_run", "result_id": str(result.id)},),
            task_id=f"summary-evaluation-{result.id}-{result.attempt}",
            dispatch_timeout_seconds=10,
        )
    except WorkflowAlreadyStartedError:
        pass
    except Exception:
        # The result is the durable outbox; replaying POST retries publication.
        result.error_code = "dispatch_failed"
        result.save(update_fields=["error_code"])
        return
    result.dispatched = True
    result.error_code = ""
    result.save(update_fields=["dispatched", "error_code"])


def _population(run):
    items = list(
        ExperimentRunItem.no_workspace_objects.filter(
            run=run,
            deleted=False,
        ).order_by("example_id", "trial_number")[: MAX_ROWS + 1]
    )
    if len(items) > MAX_ROWS:
        raise SummaryError("population_too_large")
    try:
        expected = _planned(run)
    except RunWriteError as exc:
        raise SummaryError("incomplete_population") from exc
    actual = {(str(item.example_id), item.trial_number) for item in items}
    if (
        not items
        or expected != actual
        or any(item.status not in {"completed", "failed", "skipped"} for item in items)
    ):
        raise SummaryError("incomplete_population")
    rows = [
        {
            "item_id": str(item.id),
            "example_id": str(item.example_id),
            "trial_number": item.trial_number,
            "status": item.status,
            "input": item.input,
            "expected": item.expected,
            "output": item.output,
            "scores": item.score_refs,
        }
        for item in items
    ]
    if len(_canonical(rows).encode("utf-8")) > MAX_INPUT_BYTES:
        raise SummaryError("population_too_large")
    return rows


def _metadata_object(metadata):
    """LLM evaluators serialize their metadata to JSON text; decode only that."""
    if isinstance(metadata, str) and metadata:
        try:
            return json.loads(metadata)
        except ValueError:
            return metadata
    return metadata or {}


def _evaluate(result, rows):
    from model_hub.services.external_run_evaluations import (
        STORED_EVALUATION,
        StoredEvaluationRunner,
    )

    class SummaryRunner(StoredEvaluationRunner):
        def _format_response(self, *args, **kwargs):
            response = super()._format_response(*args, **kwargs)
            self.summary_metadata = _metadata_object(response.get("metadata"))
            return response

    run = result.run
    definition = result.binding.definition
    # One transient aggregate item, not a dataset row or a second experiment.
    # Retry attempts have distinct usage identities; task replays never call here.
    invocation = uuid.uuid5(result.id, str(result.attempt))
    adapter = SimpleNamespace(
        id=invocation,
        dataset_version=run.dataset_version,
        evaluator_versions=[deepcopy(definition["pin"])],
        metadata={
            _RUN_KEY: {
                STORED_EVALUATION: {
                    "template": deepcopy(definition["template"]),
                    "mapping": deepcopy(definition["mapping"]),
                    "kb_id": None,
                }
            }
        },
    )
    runner = SummaryRunner(adapter)
    runner.source_configs.update(
        {
            "run_id": str(run.id),
            "summary_binding_id": str(result.binding_id),
            "summary_result_id": str(result.id),
            "attempt": result.attempt,
        }
    )
    ref = runner.evaluate(
        SimpleNamespace(
            id=result.id,
            input={
                "rows": rows,
                "outputs": [row["output"] for row in rows],
                "expected": [row["expected"] for row in rows],
                "scores": [row["scores"] for row in rows],
                "run": {
                    "id": str(run.id),
                    "status": run.status,
                    "row_count": len(rows),
                },
            },
            expected=None,
            output=None,
        )
    )
    if ref["status"] != "assessed":
        raise SummaryError("evaluation_failed")
    return {
        "score": ref["value"],
        "reason": ref.get("explanation", ""),
        "metadata": getattr(runner, "summary_metadata", {}),
    }


def _validated_output(output):
    if not isinstance(output, dict):
        raise SummaryError("invalid_output")
    score = output.get("score")
    reason = output.get("reason", "")
    metadata = output.get("metadata", {})
    if (
        type(score) not in (int, float)
        or not math.isfinite(score)
        or not isinstance(reason, str)
        or len(reason) > 16384
        or not isinstance(metadata, dict)
    ):
        raise SummaryError("invalid_output")
    try:
        if len(_canonical(metadata).encode("utf-8")) > 65536:
            raise SummaryError("invalid_output")
    except RunWriteError as exc:
        raise SummaryError("invalid_output") from exc
    return score, reason, metadata


@force_primary()
def process_summary_result(result_id):
    """Commit the claim before invoking; ambiguous running calls are not stolen."""
    with transaction.atomic():
        result = ExperimentSummaryResult.objects.select_for_update().get(pk=result_id)
        if result.status != "pending":
            return {"id": str(result.id), "status": result.status}
        result.status = "running"
        result.attempt += 1
        result.started_at = timezone.now()
        result.error_code = ""
        result.save(update_fields=["status", "attempt", "started_at", "error_code"])
    error_code = ""
    try:
        run = _live_run(result.run_id).first()
        if run is None:
            raise SummaryError("run_unavailable")
        result.run = run
        _check_scope(run, result.binding)
        score, reason, metadata = _validated_output(_evaluate(result, _population(run)))
    except SummaryError as exc:
        known = {
            "incomplete_population",
            "population_too_large",
            "invalid_output",
            "evaluation_failed",
            "run_unavailable",
        }
        error_code = str(exc) if str(exc) in known else "scope_changed"
    except Exception:
        error_code = "evaluation_failed"
    if error_code:
        score, reason, metadata = None, "", {}
    ExperimentSummaryResult.objects.filter(pk=result.id, status="running").update(
        status="failed" if error_code else "completed",
        score=score,
        reason=reason,
        metadata=metadata,
        error_code=error_code,
        completed_at=timezone.now(),
    )
    return {"id": str(result.id), "status": "failed" if error_code else "completed"}
