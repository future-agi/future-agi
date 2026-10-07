from types import SimpleNamespace

from tracer.models.custom_eval_config import EvalOutputType
from tracer.utils.helper import update_column_config_based_on_eval_config


def test_observe_eval_column_unions_observed_choices_and_exposes_target_type():
    config = SimpleNamespace(
        id="config-1",
        name="Quality",
        eval_template=SimpleNamespace(
            id="template-1",
            choices=["declared"],
            config={
                "output": EvalOutputType.CHOICES.value,
                "choices_map": {"declared": "success"},
            },
        ),
    )

    columns = update_column_config_based_on_eval_config(
        [],
        [config],
        skip_choices=True,
        target_types={"config-1": "span"},
        observed_choice_labels={"config-1": ["declared", "legacy"]},
    )

    assert len(columns) == 1
    assert columns[0]["id"] == "config-1"
    assert columns[0]["target_type"] == "span"
    assert columns[0]["choices"] == ["declared", "legacy"]
    assert columns[0]["choices_map"]["legacy"] == "neutral"
