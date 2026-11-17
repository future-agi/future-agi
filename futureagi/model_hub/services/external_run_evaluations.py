"""Evaluate pinned external receipts; never execute their producer or edit Cells."""

import math
import re
from copy import copy, deepcopy
from functools import partial
from types import SimpleNamespace
from uuid import UUID

from django.db import transaction

from evaluations.constants import FUTUREAGI_EVAL_TYPES
from evaluations.run_settings import run_settings
from model_hub.models.choices import CellStatus
from model_hub.models.develop_dataset import Dataset
from model_hub.models.evals_metric import (
    EvalTemplate,
    EvalTemplateVersion,
    UserEvalMetric,
)
from model_hub.models.experiments import ExperimentRun, ExperimentRunItem
from model_hub.models.native_experiment_runs import NativeRunBinding
from model_hub.services.evaluator_comparison_identity import (
    finalize_comparison_identities,
    with_comparison_identity,
)
from model_hub.services.experiment_run_creation import create_experiment_run
from model_hub.services.experiment_run_ingestion import _load_run
from model_hub.services.experiment_run_writes import (
    _RUN_KEY,
    RunWriteError,
    _digest,
    _envelope,
    _planned,
    _scope,
    claim_run_item,
    finalize_run,
    record_run_item_result,
    transition_run,
)
from model_hub.views.eval_runner import EvaluationRunner
from tfc.routers import force_primary

STORED_EVALUATION = "stored_evaluation"
_VERSION_FIELDS = (
    "version_number",
    "prompt_messages",
    "config_snapshot",
    "criteria",
    "model",
    "output_type_normalized",
    "pass_threshold",
    "choice_scores",
)
_TEMPLATE_FIELDS = (
    "name",
    "owner",
    "eval_type",
    "template_type",
    "config",
    "criteria",
    "model",
    "output_type_normalized",
    "pass_threshold",
    "choice_scores",
    "choices",
    "multi_choice",
)


def _stored(run):
    return _envelope(run.metadata)[_RUN_KEY].get(STORED_EVALUATION)


def _value(item, reference):
    """Resolve only explicit JSON paths in this exact receipt's stored values."""
    if not isinstance(reference, str):
        raise RunWriteError("Stored mappings must be input, expected or output paths")
    root, *path = reference.split(".")
    if root not in {"input", "expected", "output"}:
        raise RunWriteError("Stored mappings must be input, expected or output paths")
    value = getattr(item, root)
    for part in path:
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdecimal() and int(part) < len(value):
            value = value[int(part)]
        else:
            raise RunWriteError("Stored mapping path was not found")
    return deepcopy(value)


def _item_pin(item):
    receipt = _envelope(item.metadata)[_RUN_KEY].get("result_receipt")
    payload = {
        name: getattr(item, name)
        for name in (
            "status",
            "output",
            "error",
            "trace_id",
            "root_span_id",
            "latency_ms",
            "token_usage",
            "cost",
            "score_refs",
        )
    }
    if (
        item.deleted
        or item.status != "completed"
        or not isinstance(receipt, dict)
        or not receipt.get("receipt_key")
        or receipt.get("payload_hash") != _digest(payload)
    ):
        raise RunWriteError("Evaluation requires a completed, intact stored receipt")
    return {
        "item_id": str(item.id),
        "example_id": str(item.example_id),
        "trial_number": item.trial_number,
        "receipt": deepcopy(receipt),
        "evidence_hash": _digest(
            {
                "input": item.input,
                "expected": item.expected,
                **payload,
            }
        ),
    }


@force_primary()
@transaction.atomic
def _dispatch(run_id):
    from temporalio.exceptions import WorkflowAlreadyStartedError

    from model_hub.tasks.user_evaluation import process_evaluation_single_task

    run = (
        ExperimentRun.no_workspace_objects.select_for_update()
        .filter(
            id=run_id,
            deleted=False,
        )
        .first()
    )
    if run is None or run.status != "queued" or run.completed_at is not None:
        return
    admission = _stored(run)
    if not isinstance(admission, dict) or not admission.get("dispatch_pending"):
        return
    # Serialize publishers, not evaluator calls. A failure or process exit keeps
    # the committed pending marker, so the same request can recover publication.
    try:
        process_evaluation_single_task.apply_async(
            args=({"type": "stored_run", "run_id": str(run_id)},),
            task_id=f"stored-run-evaluation-{run_id}",
            dispatch_timeout_seconds=10,
        )
    except WorkflowAlreadyStartedError:
        # The earlier start succeeded but its acknowledgement was lost.
        pass
    metadata = _envelope(run.metadata)
    metadata[_RUN_KEY][STORED_EVALUATION] = {**admission, "dispatch_pending": False}
    run.metadata = metadata
    run.save(update_fields=["metadata", "updated_at"])


@force_primary()
@transaction.atomic
def admit_external_run_evaluation(
    *,
    parent,
    actor,
    run_id,
    evaluator_metric_id,
    item_ids,
    mapping,
):
    """Pin one child; replay only retries publication without an acknowledgement."""
    if not 1 <= len(item_ids) <= 100 or len(set(item_ids)) != len(item_ids):
        raise RunWriteError("Select 1 to 100 unique stored items")
    if not isinstance(mapping, dict) or not 1 <= len(mapping) <= 100:
        raise RunWriteError("Provide 1 to 100 stored input mappings")
    dataset = parent.dataset_version.dataset
    # Match creation's lock order: dataset, version, run. No parent write occurs.
    Dataset.no_workspace_objects.select_for_update().get(pk=dataset.pk)
    parent = _load_run(
        run_id=parent.id,
        organization_id=dataset.organization_id,
        workspace_id=dataset.workspace_id,
    )
    if (
        parent.task_kind != "external"
        or NativeRunBinding.objects.filter(run=parent).exists()
    ):
        raise RunWriteError("Select an API-ingested external run")
    request_hash = _digest(
        {
            "parent_run_id": str(parent.id),
            "actor_id": str(actor.id),
            "evaluator_metric_id": str(evaluator_metric_id),
            "item_ids": sorted(str(value) for value in item_ids),
            "mapping": mapping,
        }
    )
    existing = ExperimentRun.all_objects.select_for_update().filter(pk=run_id).first()
    if existing is not None:
        admission = _stored(existing)
        if (
            existing.deleted
            or existing.dataset_version_id != parent.dataset_version_id
            or not isinstance(admission, dict)
            or admission.get("request_hash") != request_hash
        ):
            raise RunWriteError("Run UUID is already bound to another evaluation")
        if existing.status == "queued" and admission.get("dispatch_pending"):
            transaction.on_commit(partial(_dispatch, existing.id))
        return existing
    items = list(
        ExperimentRunItem.no_workspace_objects.filter(
            id__in=item_ids,
            run=parent,
            deleted=False,
        ).order_by("example_id", "trial_number")
    )
    if len(items) != len(item_ids):
        raise RunWriteError("Stored item was not found in the parent run")
    plan = _planned(parent)
    sources = []
    for item in items:
        if (str(item.example_id), item.trial_number) not in plan:
            raise RunWriteError("Stored item is outside the parent plan")
        sources.append(_item_pin(item))
        for reference in mapping.values():
            _value(item, reference)
    metric = (
        UserEvalMetric.no_workspace_objects.select_related("template")
        .filter(
            id=evaluator_metric_id,
            dataset_id=dataset.id,
            deleted=False,
        )
        .first()
    )
    if metric is None:
        raise RunWriteError("Evaluator binding was not found in the parent dataset")
    template = metric.template
    if template.template_type != "single" or template.eval_type not in {"llm", "code"}:
        raise RunWriteError("Stored evaluation requires one non-agent evaluator")
    child = create_experiment_run(
        run_id=run_id,
        dataset_version=parent.dataset_version,
        created_by=actor,
        task_kind="external",
        experiment=parent.experiment,
        expected_items=[(item.example_id, item.trial_number) for item in items],
        evaluator_metrics=[metric],
        model_config=parent.model_config,
    )
    pin = child.evaluator_versions[0]
    pin["binding_config"] = {
        **(pin["binding_config"] or {}),
        "mapping": deepcopy(mapping),
    }
    # Creation sealed the comparison identity before the stored mapping joined
    # the binding; describe and seal the binding this child will actually run.
    version = EvalTemplateVersion.objects.resolve_for_metric(metric)
    if version is None or str(version.id) != pin["evaluator_version_id"]:
        raise RunWriteError("evaluator has no valid resolved version")
    admitted = copy(metric)
    admitted.config = {**(metric.config or {}), "mapping": deepcopy(mapping)}
    local = {key: value for key, value in pin.items() if key != "comparison"}
    child.evaluator_versions[0] = pin = finalize_comparison_identities(
        [with_comparison_identity(local, admitted, version, dataset)]
    )[0]
    execution = {name: deepcopy(getattr(template, name)) for name in _TEMPLATE_FIELDS}
    execution["config"] = run_settings(execution["config"] or {})
    execution["config"].update(deepcopy(pin["config_snapshot"] or {}))
    for name in (
        "criteria",
        "model",
        "output_type_normalized",
        "pass_threshold",
        "choice_scores",
    ):
        execution[name] = deepcopy(pin[name])
    if execution["config"].get("eval_type_id") == "AgentEvaluator":
        raise RunWriteError("Agent evaluators are not part of stored evaluation")
    if execution["config"].get("run_prompt_column"):
        raise RunWriteError("Stored evaluation has no producer prompt")
    # The engine expands dataset references from both the template and saved
    # prompt. This merged snapshot covers both; only literal examples are pinned.
    if any(
        isinstance(example, dict)
        and "id" in example
        and "input" not in example
        and "output" not in example
        for example in execution["config"].get("few_shot_examples") or []
    ):
        raise RunWriteError("Stored evaluation requires literal few-shot examples")
    metadata = _envelope(child.metadata)
    metadata[_RUN_KEY][STORED_EVALUATION] = {
        "request_hash": request_hash,
        "parent_run_id": str(parent.id),
        "organization_id": str(dataset.organization_id),
        "workspace_id": str(dataset.workspace_id) if dataset.workspace_id else None,
        "sources": sources,
        "mapping": deepcopy(mapping),
        "template": execution,
        "kb_id": str(metric.kb_id) if metric.kb_id else None,
        "dispatch_pending": True,
    }
    child.metadata = metadata
    child.save(update_fields=["metadata", "evaluator_versions", "updated_at"])
    scope = {
        "organization_id": dataset.organization_id,
        "workspace_id": dataset.workspace_id,
    }
    child = transition_run(run_id=child.id, status="queued", **scope)
    transaction.on_commit(partial(_dispatch, child.id))
    return child


class StoredEvaluationRunner(EvaluationRunner):
    """Use the shared engine and accounting with immutable values, never Cells."""

    def __init__(self, run):
        admission = _stored(run)
        (pin,) = run.evaluator_versions
        dataset = run.dataset_version.dataset
        version = EvalTemplateVersion.objects.filter(
            id=pin["evaluator_version_id"],
            eval_template_id=pin["template_id"],
            deleted=False,
        ).first()
        if version is None or any(
            getattr(version, name) != pin[name] for name in _VERSION_FIELDS
        ):
            raise RunWriteError("Pinned evaluator version is no longer intact")
        super().__init__(
            UUID(pin["metric_id"]),
            format_output=True,
            source="experiment",
            source_id=pin["template_id"],
            organization_id=dataset.organization_id,
            workspace_id=dataset.workspace_id,
            version_number=pin["version_number"],
            source_configs={"run_id": str(run.id), "version_id": str(version.id)},
        )
        self.dataset = dataset
        self._resolved_version = version
        self.eval_template = EvalTemplate(
            id=UUID(pin["template_id"]),
            organization=dataset.organization,
            workspace=dataset.workspace,
            **deepcopy(admission["template"]),
        )
        # This is a snapshot view of a persisted identity, never a new template.
        self.eval_template._state.adding = False
        self.eval_template._state.db = dataset._state.db
        self.eval_template.error_localizer_enabled = False
        self.user_eval_metric = UserEvalMetric(
            id=pin["metric_id"],
            dataset=dataset,
            organization=dataset.organization,
            workspace=dataset.workspace,
            template=self.eval_template,
            model=pin["binding_model"],
            config=deepcopy(pin["binding_config"] or {}),
            pinned_version=version,
            kb_id=admission["kb_id"],
            error_localizer=False,
        )
        self.user_eval_metric.config["mapping"] = deepcopy(admission["mapping"])
        self.futureagi_eval = (
            self.eval_template.config.get("eval_type_id") in FUTUREAGI_EVAL_TYPES
        )
        # Different child UUIDs intentionally execute and account independently.
        self._usage_run = str(run.id)
        self._pair = {key: pin[key] for key in ("metric_id", "evaluator_version_id")}

    def _saved_choice_version(self):
        return self._resolved_version

    def get_few_shot_examples(self, mapping, required_field=None):
        # Mutable feedback is not part of this admitted stored-input definition.
        return []

    def _prepare_mapping_data(self, row, mappings):
        return list(mappings), [
            _value(self._item, value) for value in mappings.values()
        ]

    def _get_required_fields_and_mappings(self, **kwargs):
        return list(self.user_eval_metric.config["mapping"]), list(
            kwargs.get("mapping") or []
        )

    def _replace_dynamic_ids(self, value, row):
        return re.sub(
            r"\{\{(.*?)\}\}",
            lambda match: str(_value(self._item, match.group(1))),
            value,
        )

    def _get_input_token_count(self, row, mappings, config=None):
        from model_hub.services.experiment_run_writes import _canonical
        from model_hub.views.eval_runner import count_tiktoken_tokens

        _, values = self._prepare_mapping_data(row, mappings)
        text = " ".join(_canonical(value) for value in values)
        text += " " + str((config or {}).get("rule_prompt", ""))
        return count_tiktoken_tokens(text, []) if count_tiktoken_tokens else 0

    def _create_reason_column(self, *args, **kwargs):
        return None

    def format_output(self, result_data, row=None, eval_template=None):
        # The non-dataset formatter cannot create auxiliary Cell/Column results.
        return super().format_output(result_data, row=None, eval_template=eval_template)

    def evaluate(self, item):
        self._item = item
        self._native_row_context = {
            "input": deepcopy(item.input),
            "expected": deepcopy(item.expected),
            "output": deepcopy(item.output),
        }
        row = SimpleNamespace(
            id=item.id,
            pk=item.id,
            dataset=self.dataset,
            dataset_id=self.dataset.id,
        )
        # _process_eval_result's Column type probe must see no arbitrary paths.
        # The actual mapping always comes from the stored admission below.
        self._mapping = self.user_eval_metric.config["mapping"]
        response, status, value = self._process_eval_result(row, {})
        explanation = response.get("reason", response.get("explanation"))
        ref = dict(self._pair)
        if isinstance(explanation, str):
            ref["explanation"] = explanation[:16384]
        if status == CellStatus.ERROR.value:
            return {**ref, "status": "error"}
        numeric = value.get("score") if isinstance(value, dict) else value
        if isinstance(numeric, bool):
            numeric = int(numeric)
        elif numeric in ("Passed", "Failed"):
            numeric = (
                int(numeric == "Passed")
                if isinstance(response.get("failure"), bool)
                else None
            )
        if isinstance(value, dict) and "choice" in value:
            from model_hub.utils.scoring import apply_choice_scores

            numeric = apply_choice_scores(
                value["choice"], self._effective_choice_scores() or {}
            )
        elif isinstance(value, dict) and "choices" in value:
            from model_hub.utils.scoring import apply_choice_scores

            scores = [
                apply_choice_scores(str(choice), self._effective_choice_scores() or {})
                for choice in value["choices"]
            ]
            numeric = (
                sum(scores) / len(scores)
                if scores and all(score is not None for score in scores)
                else None
            )
        if type(numeric) in (int, float) and math.isfinite(numeric):
            return {**ref, "status": "assessed", "value": numeric}
        return {**ref, "status": "not_assessed"}

    def _run_evaluation(self, row, mappings, config):
        from common.utils.data_injection import normalize
        from evaluations.engine.preprocessing import preprocess_inputs
        from model_hub.utils.eval_input_validation import validate_eval_inputs
        from model_hub.utils.function_eval_params import merge_code_eval_kwargs

        fields, values = self._prepare_mapping_data(row, self._mapping)
        warning, _ = validate_eval_inputs(
            self.eval_template,
            dict(zip(fields, values, strict=True)),
            mapped_keys=fields,
        )
        instance = self._create_eval_instance(
            config=config,
            model=self.user_eval_metric.model,
            kb_id=self.user_eval_metric.kb_id,
            managed_judge_billing=self._managed_judge_billing(),
        )
        error_config = self._prepare_eval_config(deepcopy(config))
        mapped = self.map_fields(
            required_field=list(fields),
            mapping=list(values),
            config=config,
        )
        mapped = merge_code_eval_kwargs(
            mapped,
            self.eval_template,
            self.user_eval_metric.config,
        )
        if self.eval_template.eval_type == "code":
            mapped = preprocess_inputs(self.eval_template.name, mapped)
        binding = self.user_eval_metric.config
        injection = (binding.get("run_config") or {}).get("data_injection")
        injection = injection or binding.get("data_injection")
        injection = injection or self.eval_template.config.get("data_injection", {})
        if normalize(injection)["full_row"]:
            mapped.setdefault("row_context", deepcopy(self._native_row_context))
        # No live Row/Cell, feedback or ground-truth retrieval may widen inputs.
        return instance.run(**mapped), fields, values, error_config, instance, warning

    def _handle_api_call(self, row, mappings, config=None, **kwargs):
        return super()._handle_api_call(row, self._mapping, config, **kwargs)


def _sources(run, admission):
    """Revalidate every admitted receipt before any evaluator invocation."""
    parent = ExperimentRun.no_workspace_objects.filter(
        id=admission["parent_run_id"],
        **_scope(
            run.dataset_version.dataset.organization_id,
            run.dataset_version.dataset.workspace_id,
        ),
    ).first()
    if parent is None or parent.dataset_version_id != run.dataset_version_id:
        raise RunWriteError("Stored parent version does not match the child")
    items = {
        str(item.id): item
        for item in ExperimentRunItem.no_workspace_objects.filter(
            run=parent,
            id__in=[source["item_id"] for source in admission["sources"]],
            deleted=False,
        )
    }
    result = []
    for source in admission["sources"]:
        item = items.get(source["item_id"])
        if item is None or _item_pin(item) != source:
            raise RunWriteError("Stored parent receipt no longer matches its pin")
        result.append(item)
    return result


@force_primary()
def process_stored_run_evaluation(run_id):
    """At most one invocation per child item, including overlapping deliveries.

    Claims commit before external calls. An interrupted running claim is never
    stolen automatically: an ambiguous call requires explicit reconciliation,
    not a retry that might invoke the evaluator twice.
    """
    with transaction.atomic():
        run = (
            ExperimentRun.no_workspace_objects.select_for_update(of=("self",))
            .select_related("dataset_version__dataset")
            .filter(
                id=run_id,
                deleted=False,
                dataset_version__deleted=False,
                dataset_version__dataset__deleted=False,
            )
            .first()
        )
        if run is None or not isinstance(_stored(run), dict):
            raise RunWriteError("Stored evaluation child was not found")
        if run.completed_at is not None or run.status == "cancelled":
            return {"run_id": str(run.id), "status": run.status}
        admission = _stored(run)
        dataset = run.dataset_version.dataset
        workspace_id = str(dataset.workspace_id) if dataset.workspace_id else None
        if (
            admission["organization_id"] != str(dataset.organization_id)
            or admission["workspace_id"] != workspace_id
        ):
            raise RunWriteError("Stored evaluation tenant changed after admission")
        sources = _sources(run, admission)
        runner = StoredEvaluationRunner(run)
        scope = {
            "organization_id": run.dataset_version.dataset.organization_id,
            "workspace_id": run.dataset_version.dataset.workspace_id,
        }
        if run.status == "queued":
            run = transition_run(run_id=run.id, status="running", **scope)
    for source in sources:
        receipt = f"stored-evaluation-{source.id}"
        with transaction.atomic():
            run = _load_run(run_id=run.id, **scope)
            if run.completed_at is not None or run.status == "cancelled":
                return {"run_id": str(run.id), "status": run.status}
            previous = ExperimentRunItem.all_objects.filter(
                run=run,
                example_id=source.example_id,
                trial_number=source.trial_number,
            ).first()
            if previous is not None:
                if previous.deleted:
                    raise RunWriteError("Deleted evaluation item requires recovery")
                if previous.status == "running":
                    return {"run_id": str(run.id), "status": "running"}
                continue
            item = claim_run_item(
                run_id=run.id,
                example_id=source.example_id,
                trial_number=source.trial_number,
                receipt_key=receipt,
                **scope,
            )
            # These are the parent's immutable inputs, not a later membership read.
            item.input = deepcopy(source.input)
            item.expected = deepcopy(source.expected)
            item.save(update_fields=["input", "expected", "updated_at"])
        ref = runner.evaluate(source)
        with transaction.atomic():
            run = _load_run(run_id=run.id, **scope)
            # Cancellation prevents new claims, not results of calls already sent.
            record_run_item_result(
                item_id=item.id,
                status="completed",
                receipt_key=receipt,
                output=source.output,
                score_refs=[ref],
                **scope,
            )
            if run.status == "cancelled":
                return {"run_id": str(run.id), "status": run.status}
    with transaction.atomic():
        run = _load_run(run_id=run.id, **scope)
        if run.status != "cancelled":
            run = finalize_run(run_id=run.id, **scope)
    return {"run_id": str(run.id), "status": run.status}
