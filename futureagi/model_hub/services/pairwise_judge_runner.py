"""Stored pair inputs using EvaluationRunner's admission and single-meter path."""

import json
from copy import copy, deepcopy
from types import SimpleNamespace

from model_hub.models.choices import CellStatus
from model_hub.models.evals_metric import UserEvalMetric
from model_hub.views.eval_runner import EvaluationRunner


class PairwiseJudgeRunner(EvaluationRunner):
    def __init__(self, job, item, version):
        super().__init__(
            version.pk,
            format_output=True,
            source="pairwise_judge",
            source_id=str(version.eval_template_id),
            organization_id=job.organization_id,
            workspace_id=job.workspace_id,
            version_number=version.version_number,
            source_configs={
                "run_id": str(job.pk),
                "version_id": str(version.pk),
                "item_id": str(item.pk),
                "presentation_order": item.presentation_order,
            },
        )
        self.dataset = job.experiment.dataset
        self._resolved_version = version
        self._usage_run = str(job.pk)
        self._item = item
        self._log = None
        # An unsaved binding, as in StoredEvaluationRunner; never create Cells.
        template = copy(version.eval_template)
        template.config = deepcopy(version.config_snapshot)
        template.criteria = version.criteria
        template.model = version.model
        template.choices = template.config["choices"]
        template.multi_choice = False
        template.choice_scores = deepcopy(version.choice_scores or {})
        template.output_type_normalized = version.output_type_normalized
        template.error_localizer_enabled = False
        self.eval_template = template
        self.user_eval_metric = UserEvalMetric(
            id=version.pk,
            name=template.name,
            dataset=self.dataset,
            organization=job.organization,
            workspace=job.workspace,
            template=template,
            model=version.model,
            pinned_version=version,
            config={"config": {}},
            error_localizer=False,
        )
        self._inputs = deepcopy(item.snapshot)
        if item.presentation_order == "ba":
            self._inputs["output_a"], self._inputs["output_b"] = (
                self._inputs["output_b"],
                self._inputs["output_a"],
            )

    def _saved_choice_version(self):
        return self._resolved_version

    def get_few_shot_examples(self, mapping, required_field=None):
        # Only the admitted literal examples, never mutable feedback retrieval.
        return []

    def _prepare_mapping_data(self, row, mappings):
        return list(self._inputs), list(deepcopy(self._inputs).values())

    def _get_required_fields_and_mappings(self, **kwargs):
        return list(self._inputs), list(kwargs.get("mapping") or [])

    def _get_input_token_count(self, row, mappings, config=None):
        from model_hub.views.eval_runner import count_tiktoken_tokens

        text = json.dumps(self._inputs, sort_keys=True, ensure_ascii=False)
        text += " " + str(self.eval_template.config.get("rule_prompt", ""))
        return count_tiktoken_tokens(text, []) if count_tiktoken_tokens else 0

    def _handle_api_call(self, row, mappings, config=None, **kwargs):
        self._log = super()._handle_api_call(row, mappings, config, **kwargs)
        return self._log

    def format_output(self, result_data, row=None, eval_template=None):
        return super().format_output(result_data, row=None, eval_template=eval_template)

    def _run_evaluation(self, row, mappings, config):
        from model_hub.utils.eval_input_validation import validate_eval_inputs

        fields, values = self._prepare_mapping_data(row, mappings)
        warning, _ = validate_eval_inputs(
            self.eval_template, self._inputs, mapped_keys=fields
        )
        instance = self._create_eval_instance(
            config=config,
            model=self.user_eval_metric.model,
            managed_judge_billing=self._managed_judge_billing(),
        )
        mapped = self.map_fields(
            required_field=list(fields), mapping=list(values), config=config
        )
        # No live Row/Cell, feedback or ground-truth retrieval widens the inputs.
        return instance.run(**mapped), fields, values, config, instance, warning

    def evaluate(self):
        # The item UUID binds job/example/trial and its saved presentation order.
        # Shared usage IDs use this row, the job UUID and the pinned version.
        row = SimpleNamespace(
            id=self._item.pk,
            pk=self._item.pk,
            dataset=self.dataset,
            dataset_id=self.dataset.pk,
        )
        response, status, value = self._process_eval_result(row, {})
        if status == CellStatus.ERROR.value:
            raise ValueError("Pairwise evaluation failed")
        return {
            "output": value,
            "reason": response.get("reason"),
            "log_id": str(self._log.log_id) if self._log is not None else None,
        }
