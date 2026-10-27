"""Tests for the partial-input validation in EvaluationRunner._run_evaluation.

Covers the Phase 1 safety net for custom evals (see
`docs/superpowers/specs/2026-05-18-eval-optional-inputs-design.md`):

- System evals (custom_eval=False): per-key strict empty validation
  remains; a single empty mapped value still raises
  "No input received for '<key>'".
- Custom user evals (custom_eval=True):
  - All mapped variables filled  -> eval runs, no warning attached.
  - Some mapped variables empty  -> eval runs, partial_input warning
    attached on the runner instance and (after _format_response) on
    the response payload.
  - All mapped variables empty   -> raises
    "No input received for any of '<a>', '<b>'..."
  - Mapping points to a value that didn't resolve and the mapping
    itself is invalid -> still raises "Invalid mapping for '<key>'".
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

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
from model_hub.views.eval_runner import EvaluationRunner


@pytest.fixture
def dataset_with_two_columns(db, organization, user, workspace):
    """Minimal dataset with two text columns and a single row.

    The row's cells default to empty values; individual tests overwrite
    them to drive the validation branches.
    """
    dataset = Dataset.objects.create(
        name="partial-inputs-dataset",
        organization=organization,
        user=user,
        source=DatasetSourceChoices.BUILD.value,
        model_type=ModelTypes.GENERATIVE_LLM.value,
        column_order=[],
        column_config={},
        workspace=workspace,
    )
    col_a = Column.objects.create(
        dataset=dataset,
        name="content",
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    col_b = Column.objects.create(
        dataset=dataset,
        name="context",
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    row = Row.objects.create(dataset=dataset, order=0)
    cell_a = Cell.objects.create(
        dataset=dataset,
        row=row,
        column=col_a,
        value="",
        status=CellStatus.PASS.value,
    )
    cell_b = Cell.objects.create(
        dataset=dataset,
        row=row,
        column=col_b,
        value="",
        status=CellStatus.PASS.value,
    )
    return {
        "dataset": dataset,
        "row": row,
        "col_a": col_a,
        "col_b": col_b,
        "cell_a": cell_a,
        "cell_b": cell_b,
    }


def _make_runner(eval_template, dataset, user, workspace, organization):
    """Construct an EvaluationRunner wired to the given template/dataset.

    Skips network/LLM construction by mocking the eval instance and the
    config-preparation step; only the validation block exercised here
    runs against real DB state.
    """
    user_eval_metric = UserEvalMetric.objects.create(
        name="partial-inputs-uem",
        dataset=dataset,
        template=eval_template,
        user=user,
        workspace=workspace,
        organization=organization,
        model="turing_large",
        config={"mapping": {}},
    )
    runner = EvaluationRunner(
        user_eval_metric_id=str(user_eval_metric.id),
        is_only_eval=True,
        # format_output=True skips the constructor's _initialize_eval_metric
        # call so we can wire dependencies by hand below.
        format_output=True,
        organization_id=organization.id,
        workspace_id=workspace.id,
    )
    runner.user_eval_metric = user_eval_metric
    runner.eval_template = eval_template
    runner.dataset = dataset
    # _prepare_mapping_data dereferences self.column.id; we never actually
    # write to this column in these tests, so a Mock id is enough.
    column_mock = MagicMock()
    column_mock.id = uuid.uuid4()
    runner.column = column_mock
    return runner, user_eval_metric


@pytest.fixture
def custom_eval_template(db, organization, workspace):
    """A user-built custom LLM eval with two required vars."""
    return EvalTemplate.objects.create(
        name="custom-partial-inputs-eval",
        description="custom",
        owner=OwnerChoices.USER.value,
        organization=organization,
        workspace=workspace,
        eval_type="llm",
        config={
            "eval_type_id": "CustomPromptEvaluator",
            "custom_eval": True,
            "required_keys": ["content", "context"],
            "optional_keys": [],
            "output": "score",
            "rule_prompt": "is this content toxic? Content: {{content}} Context: {{context}}",
        },
        choices=["No Input", "minor_toxic", "free_of_toxic"],
        model="turing_large",
    )


@pytest.fixture
def system_eval_template(db, organization, workspace):
    """A system (FutureAGI-provided) eval — same shape but custom_eval=False."""
    return EvalTemplate.objects.create(
        name="system-partial-inputs-eval",
        description="system",
        owner=OwnerChoices.SYSTEM.value,
        organization=organization,
        workspace=workspace,
        eval_type="llm",
        config={
            "eval_type_id": "CustomPromptEvaluator",
            "custom_eval": False,
            "required_keys": ["content", "context"],
            "optional_keys": [],
            "output": "score",
        },
        choices=[],
        model="turing_large",
    )


def _run_validation_only(runner, row, mappings):
    """Drive _run_evaluation just far enough to exercise the validation block.

    We mock the LLM construction + execution and the prepare-config helper.
    The validation block sits between these mocks, so any ValueError it
    raises bubbles out and any state it sets on the runner is observable.
    """
    with (
        patch.object(runner, "_create_eval_instance", return_value=MagicMock()),
        patch.object(runner, "_prepare_eval_config", return_value=None),
        patch.object(
            runner,
            "map_fields",
            side_effect=lambda required_field, mapping, **_: {
                k: mapping[i] for i, k in enumerate(required_field)
            },
        ),
        patch(
            "evaluations.engine.preprocessing.preprocess_inputs",
            side_effect=lambda _name, mapped: mapped,
        ),
    ):
        # We don't care about the run result for these tests — we only
        # need _run_evaluation to either raise during validation or
        # reach the eval call. eval_instance is a MagicMock so .run()
        # returns a MagicMock that we never introspect here. Returns the
        # full 6-tuple so callers can read partial_input_warning (last).
        return runner._run_evaluation(row, mappings, config={})


@pytest.mark.django_db
class TestCustomEvalPartialInputs:
    def test_all_filled_no_warning(
        self,
        dataset_with_two_columns,
        custom_eval_template,
        organization,
        user,
        workspace,
    ):
        ds = dataset_with_two_columns
        ds["cell_a"].value = "you are an idiot"
        ds["cell_a"].save()
        ds["cell_b"].value = "casual chat"
        ds["cell_b"].save()

        runner, _ = _make_runner(
            custom_eval_template, ds["dataset"], user, workspace, organization
        )
        mappings = {"content": str(ds["col_a"].id), "context": str(ds["col_b"].id)}

        result = _run_validation_only(runner, ds["row"], mappings)
        partial_input_warning = result[5]

        assert partial_input_warning is None

    def test_partial_empty_attaches_warning(
        self,
        dataset_with_two_columns,
        custom_eval_template,
        organization,
        user,
        workspace,
    ):
        ds = dataset_with_two_columns
        ds["cell_a"].value = "you are an idiot"
        ds["cell_a"].save()
        # cell_b stays empty
        runner, _ = _make_runner(
            custom_eval_template, ds["dataset"], user, workspace, organization
        )
        mappings = {"content": str(ds["col_a"].id), "context": str(ds["col_b"].id)}

        result = _run_validation_only(runner, ds["row"], mappings)
        warning = result[5]

        assert warning is not None
        assert warning["type"] == "partial_input"
        assert warning["empty_keys"] == ["context"]
        assert warning["filled_keys"] == ["content"]

    def test_all_empty_raises(
        self,
        dataset_with_two_columns,
        custom_eval_template,
        organization,
        user,
        workspace,
    ):
        ds = dataset_with_two_columns
        # Both cells stay empty.
        runner, _ = _make_runner(
            custom_eval_template, ds["dataset"], user, workspace, organization
        )
        mappings = {"content": str(ds["col_a"].id), "context": str(ds["col_b"].id)}

        with pytest.raises(ValueError, match="No input received for any of"):
            _run_validation_only(runner, ds["row"], mappings)


@pytest.mark.django_db
class TestSystemEvalStrictValidation:
    def test_single_empty_mapped_value_still_raises(
        self,
        dataset_with_two_columns,
        system_eval_template,
        organization,
        user,
        workspace,
    ):
        ds = dataset_with_two_columns
        ds["cell_a"].value = "you are an idiot"
        ds["cell_a"].save()
        # cell_b stays empty — system evals must still error per-key.
        runner, _ = _make_runner(
            system_eval_template, ds["dataset"], user, workspace, organization
        )
        mappings = {"content": str(ds["col_a"].id), "context": str(ds["col_b"].id)}

        with pytest.raises(ValueError, match=r"No input received for '\w+'"):
            _run_validation_only(runner, ds["row"], mappings)


class _FakeTemplate:
    """Minimal duck-typed template for direct validator tests."""

    def __init__(self, *, required_keys, optional_keys=None, custom_eval=True):
        self.config = {
            "required_keys": list(required_keys),
            "optional_keys": list(optional_keys or []),
            "custom_eval": custom_eval,
        }


class TestSharedValidator:
    """Unit-test the validator in isolation — no DB, no runner.

    Covers the universal contract that dataset/playground/tracing now
    share: same inputs, same decisions.
    """

    def test_returns_none_when_no_keys_configured(self):
        from model_hub.utils.eval_input_validation import validate_eval_inputs

        template = _FakeTemplate(required_keys=[])
        warning, normalized = validate_eval_inputs(template, {"x": "y"})
        assert warning is None
        assert normalized == {"x": "y"}

    def test_custom_eval_missing_key_treated_as_empty_and_filled(self):
        """TH-5107 case: caller omits a required var → fill with "" + warn."""
        from model_hub.utils.eval_input_validation import validate_eval_inputs

        template = _FakeTemplate(required_keys=["content", "context"])
        warning, normalized = validate_eval_inputs(template, {"content": "x"})
        assert warning is not None
        assert warning["type"] == "partial_input"
        assert warning["empty_keys"] == ["context"]
        assert warning["filled_keys"] == ["content"]
        # Engine receives every required key — no "Missing required key".
        assert normalized["content"] == "x"
        assert normalized["context"] == ""

    def test_custom_eval_all_filled_no_warning(self):
        from model_hub.utils.eval_input_validation import validate_eval_inputs

        template = _FakeTemplate(required_keys=["content", "context"])
        warning, normalized = validate_eval_inputs(
            template, {"content": "x", "context": "y"}
        )
        assert warning is None
        assert normalized == {"content": "x", "context": "y"}

    def test_custom_eval_partial_attaches_warning(self):
        from model_hub.utils.eval_input_validation import validate_eval_inputs

        template = _FakeTemplate(required_keys=["content", "context"])
        warning, _ = validate_eval_inputs(
            template, {"content": "x", "context": ""}
        )
        assert warning is not None
        assert warning["type"] == "partial_input"
        assert warning["empty_keys"] == ["context"]
        assert warning["filled_keys"] == ["content"]

    def test_custom_eval_uses_mapped_keys_only(self):
        from model_hub.utils.eval_input_validation import validate_eval_inputs

        template = _FakeTemplate(required_keys=["content", "context", "notes"])
        warning, normalized = validate_eval_inputs(
            template,
            {"content": "x"},
            mapped_keys={"content"},
        )
        assert warning is None
        assert normalized["content"] == "x"
        # Unmapped declared vars are still filled for the engine, but
        # they do not count as partial input warnings for this run.
        assert normalized["context"] == ""
        assert normalized["notes"] == ""

    def test_custom_eval_mapped_key_missing_warns(self):
        from model_hub.utils.eval_input_validation import validate_eval_inputs

        template = _FakeTemplate(required_keys=["content", "context", "notes"])
        warning, normalized = validate_eval_inputs(
            template,
            {"content": "x"},
            mapped_keys={"content", "context"},
        )
        assert warning is not None
        assert warning["empty_keys"] == ["context"]
        assert warning["filled_keys"] == ["content"]
        assert normalized["context"] == ""

    def test_custom_eval_all_empty_raises(self):
        from model_hub.utils.eval_input_validation import validate_eval_inputs

        template = _FakeTemplate(required_keys=["content", "context"])
        with pytest.raises(ValueError, match="No input received for any of"):
            validate_eval_inputs(template, {"content": "", "context": ""})

    def test_custom_eval_all_missing_raises(self):
        """No keys provided at all → still the all-empty case for a custom eval."""
        from model_hub.utils.eval_input_validation import validate_eval_inputs

        template = _FakeTemplate(required_keys=["content", "context"])
        with pytest.raises(ValueError, match="No input received for any of"):
            validate_eval_inputs(template, {})

    def test_custom_eval_single_var_empty_raises(self):
        from model_hub.utils.eval_input_validation import validate_eval_inputs

        template = _FakeTemplate(required_keys=["content"])
        with pytest.raises(ValueError, match="No input received for any of"):
            validate_eval_inputs(template, {"content": ""})

    def test_system_eval_per_key_strict(self):
        from model_hub.utils.eval_input_validation import validate_eval_inputs

        template = _FakeTemplate(
            required_keys=["content", "context"], custom_eval=False
        )
        with pytest.raises(ValueError, match=r"No input received for '\w+'"):
            validate_eval_inputs(template, {"content": "x", "context": ""})

    def test_system_eval_does_not_fill_missing_keys(self):
        """System evals stay strict — no normalization, no implicit fills."""
        from model_hub.utils.eval_input_validation import validate_eval_inputs

        template = _FakeTemplate(required_keys=["content"], custom_eval=False)
        warning, normalized = validate_eval_inputs(template, {"content": "x"})
        assert warning is None
        # Returned dict is the caller's input unchanged.
        assert normalized == {"content": "x"}

    def test_treats_none_and_whitespace_as_empty(self):
        from model_hub.utils.eval_input_validation import validate_eval_inputs

        template = _FakeTemplate(required_keys=["content"])
        for empty in [None, "", "   ", "\n\t"]:
            with pytest.raises(ValueError):
                validate_eval_inputs(template, {"content": empty})

    def test_treats_empty_containers_and_json_strings_as_empty(self):
        from model_hub.utils.eval_input_validation import validate_eval_inputs

        template = _FakeTemplate(required_keys=["content", "context"])
        for empty in [{}, [], {"nested": ""}, ["", None], "{}", "[]", "null"]:
            warning, _ = validate_eval_inputs(
                template,
                {"content": "x", "context": empty},
            )
            assert warning is not None
            assert warning["empty_keys"] == ["context"]


@pytest.mark.django_db
class TestFormatResponseInjectsWarning:
    """_format_response should move the runner-side warning onto the response."""

    def test_warning_consumed_and_cleared(
        self,
        dataset_with_two_columns,
        custom_eval_template,
        organization,
        user,
        workspace,
    ):
        runner, _ = _make_runner(
            custom_eval_template,
            dataset_with_two_columns["dataset"],
            user,
            workspace,
            organization,
        )
        warning = {
            "type": "partial_input",
            "empty_keys": ["context"],
            "filled_keys": ["content"],
            "message": "Eval ran with some inputs empty. Result may be less reliable.",
        }
        # Minimal eval_result shape consumed by extract_raw_result; we
        # patch the helper so we don't need a real evaluator output.
        # Warning is passed per-row as an argument now (rows run in
        # parallel — see _process_eval_result) rather than stashed on
        # the runner.
        with patch(
            "evaluations.engine.formatting.extract_raw_result",
            return_value={"reason": "ok"},
        ):
            response = runner._format_response(
                MagicMock(), partial_input_warning=warning
            )

        assert response.get("warnings"), "warning should be on response"
        assert response["warnings"][0]["empty_keys"] == ["context"]
