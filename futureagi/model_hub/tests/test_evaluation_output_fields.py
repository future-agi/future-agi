"""Persist native evaluation values into the matching typed output column."""

import pytest

from evaluations.engine.formatting import format_eval_value
from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.evaluation import Evaluation


@pytest.fixture
def evaluation(db, user, organization, workspace):
    template = EvalTemplate.no_workspace_objects.create(
        name="typed-output-regression",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "Pass/Fail", "eval_type_id": "DeterministicEvaluator"},
        multi_choice=True,
    )
    return Evaluation.objects.create(
        user=user,
        organization=organization,
        workspace=workspace,
        eval_template=template,
    )


@pytest.mark.django_db
@pytest.mark.parametrize("choice, expected", [("Passed", True), ("Failed", False)])
def test_formatted_pass_fail_list_persists_boolean(evaluation, choice, expected):
    evaluation.value = format_eval_value(
        {"output": "Pass/Fail", "data": [choice]}, evaluation.eval_template
    )
    assert evaluation.value == [choice]

    evaluation.save()
    evaluation.refresh_from_db()

    assert evaluation.output_bool is expected
    assert evaluation.output_float is None
    assert evaluation.output_str is None
    assert evaluation.output_str_list is None


@pytest.mark.django_db
@pytest.mark.parametrize("value", [True, False])
def test_native_boolean_persists_as_boolean_not_number(evaluation, value):
    evaluation.value = value

    evaluation.save()
    evaluation.refresh_from_db()

    assert evaluation.output_bool is value
    assert evaluation.output_float is None
    assert evaluation.output_str is None
    assert evaluation.output_str_list is None


@pytest.mark.django_db
@pytest.mark.parametrize(
    "value, expected",
    [
        ("Passed", (True, None, None, None)),
        ("Failed", (False, None, None, None)),
        (1, (None, 1.0, None, None)),
        (0, (None, 0.0, None, None)),
        (0.5, (None, 0.5, None, None)),
        (["other"], (None, None, None, ["other"])),
        (["Passed", "Failed"], (None, None, None, ["Passed", "Failed"])),
        ([], (None, None, None, [])),
        ("other", (None, None, "other", None)),
        (None, (None, None, None, None)),
    ],
)
def test_other_values_keep_their_output_columns(evaluation, value, expected):
    evaluation.value = value

    evaluation.save()
    evaluation.refresh_from_db()

    assert (
        evaluation.output_bool,
        evaluation.output_float,
        evaluation.output_str,
        evaluation.output_str_list,
    ) == expected
