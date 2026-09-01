"""
Regression tests for TH-7504: running system evals (prompt_instruction_adherence,
tone) on user-uploaded datasets failed with "No input received for 'prompt'".

The eval runner assumed every dataset column traces back to a RunPrompter
(platform-generated output). Uploaded CSV datasets have no RunPrompter, which
broke three code paths in model_hub/views/eval_runner.py:

1. process_mapping: the prompt_instruction_adherence special handling skipped
   normal cell resolution even when the RunPrompter lookup failed (misplaced
   `continue`).
2. process_mapping: the output->input auto-inference under run_prompt_column
   had unguarded ORM lookups that raised DoesNotExist.
3. EvaluationRunner._get_required_fields_and_mappings: broke after the first
   mapping entry whenever run_prompt_column was set, dropping the prompt
   column from the required fields.

Each test class below pins the uploaded-dataset fallback and the unchanged
platform-generated (RunPrompter-backed) behavior for one of those paths.
"""

import pytest

from model_hub.models.choices import (
    CellStatus,
    DatasetSourceChoices,
    DataTypeChoices,
    ModelTypes,
    OwnerChoices,
    SourceChoices,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.models.run_prompt import RunPrompter
from model_hub.views.eval_runner import EvaluationRunner, process_mapping

SYSTEM_TEXT = "Follow the instructions exactly."
USER_TEXT = "Summarize the document."


class _StubRunner:
    """Minimal runner stub for the RunPrompter-backed inference path."""

    def _replace_dynamic_ids(self, text, row):
        return text


@pytest.fixture
def dataset(db, user, organization, workspace):
    return Dataset.objects.create(
        name="TH-7504 Dataset",
        organization=organization,
        user=user,
        source=DatasetSourceChoices.BUILD.value,
        model_type=ModelTypes.GENERATIVE_LLM.value,
        workspace=workspace,
    )


@pytest.fixture
def row(db, dataset):
    return Row.objects.create(dataset=dataset, order=0)


@pytest.fixture
def run_prompter(db, dataset, organization, workspace):
    return RunPrompter.objects.create(
        dataset=dataset,
        model="gpt-4o",
        name="th-7504-run-prompt",
        organization=organization,
        workspace=workspace,
        messages=[
            {"role": "system", "content": [{"type": "text", "text": SYSTEM_TEXT}]},
            {"role": "user", "content": [{"type": "text", "text": USER_TEXT}]},
        ],
    )


def _make_column(dataset, name, source_id=None, source=SourceChoices.OTHERS.value):
    return Column.objects.create(
        name=name,
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=source,
        source_id=source_id,
    )


def _make_cell(dataset, column, row, value):
    return Cell.objects.create(
        dataset=dataset,
        column=column,
        row=row,
        value=value,
        status=CellStatus.PASS.value,
    )


@pytest.mark.django_db
class TestProcessMappingPromptInstructionAdherence:
    """prompt_instruction_adherence 'prompt' key resolution in process_mapping."""

    def test_uploaded_dataset_prompt_falls_back_to_cell_value(self, dataset, row):
        """Without a RunPrompter, the prompt must resolve from the dataset cell
        instead of being silently dropped (the misplaced-continue bug)."""
        prompt_col = _make_column(dataset, "prompt")
        output_col = _make_column(dataset, "output")
        _make_cell(dataset, prompt_col, row, "Answer in JSON only.")
        _make_cell(dataset, output_col, row, '{"answer": 42}')

        required_field, mapping = process_mapping(
            {"prompt": str(prompt_col.id), "output": str(output_col.id)},
            row,
            eval_template_name="prompt_instruction_adherence",
        )

        assert required_field == ["prompt", "output"]
        assert mapping == ["Answer in JSON only.", '{"answer": 42}']

    def test_platform_dataset_prompt_resolves_run_prompter_messages(
        self, dataset, row, run_prompter
    ):
        """With a RunPrompter, the prompt must still resolve to the structured
        messages dict and skip normal cell resolution (no duplicate entry)."""
        prompt_col = _make_column(
            dataset,
            "prompt",
            source_id=str(run_prompter.id),
            source=SourceChoices.RUN_PROMPT.value,
        )
        _make_cell(dataset, prompt_col, row, "raw cell value that must be ignored")

        required_field, mapping = process_mapping(
            {"prompt": str(prompt_col.id)},
            row,
            eval_template_name="prompt_instruction_adherence",
        )

        assert required_field == ["prompt"]
        assert mapping == [
            {
                "system_prompt": [
                    {
                        "role": "system",
                        "content": [{"type": "text", "text": SYSTEM_TEXT}],
                    }
                ],
                "user_prompt": [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": USER_TEXT}],
                    }
                ],
            }
        ]


@pytest.mark.django_db
class TestProcessMappingRunPromptColumnInference:
    """output->input auto-inference under run_prompt_column in process_mapping."""

    def test_uploaded_dataset_skips_inference_without_run_prompter(
        self, dataset, row
    ):
        """Without a RunPrompter, inference must be skipped gracefully instead
        of raising DoesNotExist (previously unguarded ORM lookups)."""
        output_col = _make_column(dataset, "output")
        _make_cell(dataset, output_col, row, "model output text")

        required_field, mapping = process_mapping(
            {"output": str(output_col.id)},
            row,
            run_prompt_column=True,
        )

        assert required_field == ["output"]
        assert mapping == ["model output text"]

    def test_platform_dataset_infers_input_from_run_prompter(
        self, dataset, row, run_prompter
    ):
        """With a RunPrompter, the user prompt must still be inferred and
        inserted as the 'input' field."""
        output_col = _make_column(
            dataset,
            "output",
            source_id=str(run_prompter.id),
            source=SourceChoices.RUN_PROMPT.value,
        )
        _make_cell(dataset, output_col, row, "model output text")

        required_field, mapping = process_mapping(
            {"output": str(output_col.id)},
            row,
            run_prompt_column=True,
            runner=_StubRunner(),
        )

        assert required_field == ["input", "output"]
        assert mapping == [USER_TEXT, "model output text"]


@pytest.mark.django_db
class TestGetRequiredFieldsAndMappings:
    """run_prompt_column handling in EvaluationRunner._get_required_fields_and_mappings."""

    def _make_runner(self, dataset, mapping, user, organization, workspace, name):
        template = EvalTemplate.objects.create(
            name=name,
            owner=OwnerChoices.SYSTEM.value,
            config={
                "required_keys": ["input"],
                "eval_type_id": "OutputEvaluator",
                "output": "Pass/Fail",
                "run_prompt_column": True,
            },
        )
        metric = UserEvalMetric.objects.create(
            name=f"{name}-metric",
            organization=organization,
            workspace=workspace,
            dataset=dataset,
            template=template,
            config={"mapping": mapping},
            user=user,
        )
        return EvaluationRunner(user_eval_metric_id=metric.id), metric

    def test_uploaded_dataset_keeps_all_mapped_columns(
        self, dataset, user, organization, workspace
    ):
        """Without a RunPrompter, every mapped column must survive — the old
        unconditional break dropped the prompt column entirely."""
        output_col = _make_column(dataset, "output")
        prompt_col = _make_column(dataset, "prompt")
        runner, metric = self._make_runner(
            dataset,
            {"output": str(output_col.id), "prompt": str(prompt_col.id)},
            user,
            organization,
            workspace,
            name="th-7504-uploaded",
        )

        required_field, mapping = runner._get_required_fields_and_mappings(
            user_eval_metric=metric
        )

        assert required_field == [str(output_col.id), str(prompt_col.id)]
        assert mapping == [str(output_col.id), str(prompt_col.id)]

    def test_platform_dataset_still_breaks_after_output_column(
        self, dataset, user, organization, workspace, run_prompter
    ):
        """With a RunPrompter behind the first mapped column, the prompt is
        auto-inferred later so only the output column must be kept."""
        output_col = _make_column(
            dataset,
            "output",
            source_id=str(run_prompter.id),
            source=SourceChoices.RUN_PROMPT.value,
        )
        prompt_col = _make_column(dataset, "prompt")
        runner, metric = self._make_runner(
            dataset,
            {"output": str(output_col.id), "prompt": str(prompt_col.id)},
            user,
            organization,
            workspace,
            name="th-7504-platform",
        )

        required_field, mapping = runner._get_required_fields_and_mappings(
            user_eval_metric=metric
        )

        assert required_field == [str(output_col.id)]
        assert mapping == [str(output_col.id)]
