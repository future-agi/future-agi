"""Template usage summaries consume both generations of multi-choice results."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from evaluations.engine.formatting import format_eval_value
from model_hub.models.evals_metric import EvalTemplate
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
    log_map = {str(tpl.id): logs}
    summary = view.prepare_template_data(tpl, log_map)
    assert summary["last30_run"] == len(values)
    assert summary["average"]["average"] == expected
    daily = view.process_graph_data_and_send_ws(
        [NOW.date()], log_map, summary, {str(tpl.id): tpl}, NOW
    )
    assert daily["average"]["avg_graph_data"][0]["value"] == expected


@pytest.mark.parametrize("choices_map", [MAPPING, {}], ids=["mapped", "no-legacy-map"])
def test_formatter_to_template_summary(choices_map):
    tpl = template(choices_map)
    values = [formatted(tpl, picks) for picks in (["Excellent", "Average"], ["Poor"])]
    assert [value["score"] for value in values] == [0.75, 0]
    assert_summary(tpl, values, 37.5)


@pytest.mark.django_db
def test_saved_percentage_template_counts_formatted_selections(auth_client):
    response = auth_client.post(
        "/model-hub/eval-templates/create-v2/",
        {
            "name": "percentage-picks",
            "eval_type": "llm",
            "model": "gpt-4o",
            "instructions": "Assess {{answer}}.",
            "output_type": "percentage",
            "multi_choice": True,
            "choice_scores": {"Excellent": 1, "Average": 0.5, "Poor": 0},
        },
        format="json",
    )
    assert response.status_code == 200
    tpl = EvalTemplate.objects.get(pk=response.data["result"]["id"])
    assert tpl.config["output"] == "score"
    values = [
        format_eval_value({"output": tpl.config["output"], "data": picks}, tpl)
        for picks in (["Excellent", "Average"], ["Poor"])
    ]
    assert [v["score"] for v in values] == [0.75, 0]
    assert_summary(tpl, values, 37.5)


def test_percentage_history_keeps_scalar_rounding_and_stored_mean_precision():
    tpl = template(MAPPING)
    tpl.config["output"] = "score"
    tpl.choice_scores["Average"] = 0.125
    stored = format_eval_value({"output": "score", "data": ["Average"]}, tpl)
    # The scalar branch rounds .246 to .25. The formatter's .125 is already scored.
    assert_summary(tpl, [0.246, stored, {"choices": [], "score": 1}], 18.75)


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
        {"score": True, "choices": ["Excellent"]},
        {"score": "0.5", "choices": ["Average"]},
        {"score": 2, "choices": ["Excellent"]},
    ]
    assert_summary(tpl, values, 50)


@pytest.mark.parametrize("choices_map", [MAPPING, {}], ids=["mapped", "no-legacy-map"])
def test_every_formatted_multi_choice_shape_counts_once(choices_map):
    tpl = template(choices_map)
    values = [
        formatted(tpl, ["Excellent", "Average"]),  # {score, choices}
        formatted(tpl, "Poor"),  # one label: {score, choice}
        formatted(tpl, {"result": "Average"}),  # dict reduced to one label
    ]
    assert values[1] == {"score": 0, "choice": "Poor"}
    assert values[2] == {"score": 0.5, "choice": "Average"}
    assert_summary(tpl, values, 41.67)


def test_unscored_single_label_uses_the_legacy_map():
    tpl = template(MAPPING)
    tpl.choice_scores = None
    values = [formatted(tpl, "Average"), formatted(tpl, ["Excellent", "Poor"])]
    assert values == ["Average", ["Excellent", "Poor"]]
    assert_summary(tpl, values, 50)


@pytest.mark.django_db
def test_saved_percentage_template_counts_single_label_runs(auth_client):
    response = auth_client.post(
        "/model-hub/eval-templates/create-v2/",
        {
            "name": "percentage-single-label",
            "eval_type": "llm",
            "model": "gpt-4o",
            "instructions": "Assess {{answer}}.",
            "output_type": "percentage",
            "multi_choice": True,
            "choice_scores": {"Excellent": 1, "Average": 0.5, "Poor": 0},
        },
        format="json",
    )
    assert response.status_code == 200
    tpl = EvalTemplate.objects.get(pk=response.data["result"]["id"])
    values = [
        format_eval_value({"output": tpl.config["output"], "data": data}, tpl)
        for data in (["Excellent", "Average"], ["Poor"], "Average")
    ]
    assert [v["score"] for v in values] == [0.75, 0, 0.5]
    assert_summary(tpl, values, 41.67)
