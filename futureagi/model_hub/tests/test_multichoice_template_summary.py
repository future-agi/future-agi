"""Template usage summaries consume both generations of multi-choice results."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from evaluations.engine.formatting import format_eval_value
from model_hub.views.separate_evals import GetEvalTemplates

NOW = datetime(2026, 11, 1, 12, tzinfo=UTC)
MAPPING = {"Excellent": "pass", "Average": "neutral", "Poor": "fail"}


def template(choices_map):
    return SimpleNamespace(
        id="example",
        name="Selection quality",
        updated_at=NOW,
        multi_choice=True,
        choice_scores={"Excellent": 1, "Average": 0.5, "Poor": 0},
        config={"output": "choices", "choices_map": choices_map},
    )


def formatted(tpl, picks):
    return format_eval_value({"output": "choices", "data": picks}, tpl)


def assert_summary(tpl, values, expected):
    logs = [
        {
            "updated_at": NOW,
            "created_at": NOW,
            "status": "success",
            "config": json.dumps({"output": {"output": value}}),
        }
        for value in values
    ]
    view = GetEvalTemplates()
    log_map = {tpl.id: logs}
    summary = view.prepare_template_data(tpl, log_map)
    assert summary["last30_run"] == len(values)
    assert summary["average"]["average"] == expected
    daily = view.process_graph_data_and_send_ws(
        [NOW.date()], log_map, summary, {tpl.id: tpl}, NOW
    )
    assert daily["average"]["avg_graph_data"][0]["value"] == expected


@pytest.mark.parametrize("choices_map", [MAPPING, {}], ids=["mapped", "no-legacy-map"])
def test_formatter_to_template_summary(choices_map):
    tpl = template(choices_map)
    values = [formatted(tpl, picks) for picks in (["Excellent", "Average"], ["Poor"])]
    assert [value["score"] for value in values] == [0.75, 0]
    assert_summary(tpl, values, 37.5)


def test_mixed_history_uses_stored_scores_and_legacy_map():
    tpl = template(MAPPING)
    tpl.choice_scores["Average"] = 0.2
    values = [formatted(tpl, ["average", "unrecognized"]), ["Excellent", "Average"]]
    # The formatter skips its unknown pick. The legacy list still uses its map.
    assert_summary(tpl, values, 47.5)


@pytest.mark.parametrize("choices_map", [MAPPING, {}], ids=["mapped", "no-legacy-map"])
def test_malformed_results_do_not_dilute_a_scored_run(choices_map):
    tpl = template(choices_map)
    values = [
        formatted(tpl, ["Average"]),
        formatted(tpl, []),
        None,
        {"choices": ["Excellent"]},
        {"score": 1, "choices": []},
        {"score": 1, "choice": "Excellent"},
        {"score": True, "choices": ["Excellent"]},
        {"score": "0.5", "choices": ["Average"]},
        {"score": 2, "choices": ["Excellent"]},
    ]
    assert_summary(tpl, values, 50)
