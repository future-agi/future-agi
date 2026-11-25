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


def _config(config_id, name, output):
    return SimpleNamespace(
        id=config_id,
        name=name,
        eval_template=SimpleNamespace(
            id=f"template-{config_id}",
            choices=["Passed", "Failed"] if output != "score" else None,
            config={"output": output},
        ),
    )


def test_count_columns_drop_the_avg_prefix_but_averaged_scores_keep_it():
    configs = [
        _config("pf", "Helpful", EvalOutputType.PASS_FAIL.value),
        _config("ch", "Tone", EvalOutputType.CHOICES.value),
        _config("sc", "Relevance", EvalOutputType.SCORE.value),
    ]

    counted = update_column_config_based_on_eval_config(
        [], configs, skip_choices=True, count_mode=True
    )
    assert [c["name"] for c in counted] == ["Helpful", "Tone", "Avg. Relevance"]

    averaged = update_column_config_based_on_eval_config([], configs, skip_choices=True)
    assert [c["name"] for c in averaged] == [
        "Avg. Helpful",
        "Avg. Tone",
        "Avg. Relevance",
    ]
