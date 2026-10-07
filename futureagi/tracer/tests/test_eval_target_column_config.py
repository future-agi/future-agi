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


def _config(config_id, name, output_type, choices=None):
    return SimpleNamespace(
        id=config_id,
        name=name,
        eval_template=SimpleNamespace(
            id=f"template-{config_id}",
            choices=choices,
            config={"output": output_type, "choices_map": {}},
        ),
    )


def _names(columns):
    return {column["id"]: column["name"] for column in columns}


def test_count_tile_columns_are_not_labelled_as_averages():
    # TH-8106: an Observe count tile shows "2 pass / 1 fail", not an average,
    # so its header must not say "Avg.". A score column is still a mean.
    configs = [
        _config("pf", "Hallucination", EvalOutputType.PASS_FAIL.value),
        _config("ch", "Tone", EvalOutputType.CHOICES.value, ["clear", "vague"]),
        _config("sc", "Relevance", EvalOutputType.SCORE.value),
    ]

    names = _names(
        update_column_config_based_on_eval_config(
            [], configs, skip_choices=True, count_mode=True
        )
    )

    assert names == {
        "pf": "Hallucination",
        "ch": "Tone",
        "sc": "Avg. Relevance",
    }


def test_percentage_pivots_keep_their_average_headers():
    # Session, voice, user and prompt pivots still render averaged
    # percentages; only the count-mode Observe lists drop the prefix.
    configs = [
        _config("pf", "Hallucination", EvalOutputType.PASS_FAIL.value),
        _config("ch", "Tone", EvalOutputType.CHOICES.value, ["clear", "vague"]),
        _config("sc", "Relevance", EvalOutputType.SCORE.value),
    ]

    assert _names(update_column_config_based_on_eval_config([], configs)) == {
        "pf": "Avg. Hallucination",
        "ch**clear": "Avg. clear (Tone)",
        "ch**vague": "Avg. vague (Tone)",
        "sc": "Avg. Relevance",
    }
    assert _names(
        update_column_config_based_on_eval_config([], configs, skip_choices=True)
    ) == {
        "pf": "Avg. Hallucination",
        "ch": "Avg. Tone",
        "sc": "Avg. Relevance",
    }
    assert _names(
        update_column_config_based_on_eval_config([], configs, is_simulator=True)
    ) == {
        "pf": "Hallucination",
        "ch**clear": "clear (Tone)",
        "ch**vague": "vague (Tone)",
        "sc": "Relevance",
    }


def _count_mode_call_sites(path, function_name):
    import ast
    from pathlib import Path

    tree = ast.parse(Path(path).read_text())
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    calls = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None)
        == "update_column_config_based_on_eval_config"
    ]
    return [
        any(
            keyword.arg == "count_mode"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in call.keywords
        )
        for call in calls
    ]


def test_observe_count_lists_name_their_columns_as_counts():
    # The two readers that emit roll-up tiles are the only count-mode callers.
    from pathlib import Path

    views = Path(__file__).resolve().parents[1] / "views"
    assert _count_mode_call_sites(
        views / "trace.py", "_list_traces_of_session_clickhouse"
    ) == [True]
    assert _count_mode_call_sites(
        views / "observation_span.py", "_list_spans_clickhouse"
    ) == [True]
