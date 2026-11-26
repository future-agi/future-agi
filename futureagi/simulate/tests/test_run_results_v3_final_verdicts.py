"""Final receipt verdicts remain final across platform template types."""

import json
from types import SimpleNamespace

import pytest
from django.db import connection

from simulate.models import CallExecution
from simulate.services.run_results_v3_queries import _configured_eval_verdict
from simulate.services.run_results_v3_scoring import (
    judge_stored_eval,
    resolve_eval_scoring_spec,
)


def _config(output_type, reverse, threshold=0.8):
    return SimpleNamespace(
        eval_template=SimpleNamespace(
            output_type_normalized=output_type,
            config={},
            choice_scores={"Passed": 0, "Failed": 1},
            pass_threshold=0.8,
        ),
        config={"reverse_output": reverse, "pass_threshold": threshold},
    )


FINAL_OUTPUTS = [
    ("Passed", "passed", 1.0),
    ("Failed", "failed", 0.0),
    ({"failure": False}, "passed", 1.0),
    ({"failure": True}, "failed", 0.0),
]


@pytest.mark.parametrize("output_type", ["pass_fail", "percentage", "deterministic"])
@pytest.mark.parametrize("threshold", [0.8, 70, "bad"])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("stored_type", ["Pass/Fail", " pass_fail "])
@pytest.mark.parametrize("output,outcome,score", FINAL_OUTPUTS)
@pytest.mark.parametrize(
    "status", ["completed", "pending", "skipped", "error", "failed"]
)
def test_receipt_final_verdict_ignores_template_scoring(
    output_type, threshold, reverse, stored_type, output, outcome, score, status
):
    config = _config(output_type, reverse, threshold)
    stored = {
        "source": "harness",
        "output_type": stored_type,
        "status": status,
        "output": output,
    }
    expected_outcome = (
        "error"
        if status in {"error", "failed"}
        else None
        if status in {"pending", "skipped"}
        else outcome
    )
    result = judge_stored_eval(stored, resolve_eval_scoring_spec(config))
    assert result.outcome == expected_outcome
    assert result.score == (score if status == "completed" else None)


@pytest.mark.parametrize("source", [None, "harness"])
@pytest.mark.parametrize("stored_type", [None, "score"])
def test_choice_label_is_not_a_final_verdict_without_marker(source, stored_type):
    stored = {
        "status": "completed",
        "output": "Passed",
        "source": source,
        "output_type": stored_type,
    }
    result = judge_stored_eval(
        stored, resolve_eval_scoring_spec(_config("deterministic", False))
    )
    assert result.outcome == "failed"
    assert result.score == 0.0


@pytest.mark.django_db
@pytest.mark.parametrize("output_type", ["pass_fail", "percentage", "deterministic"])
@pytest.mark.parametrize("threshold", [0.8, 70, "bad"])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("output,outcome,score", FINAL_OUTPUTS)
@pytest.mark.parametrize(
    "status", ["completed", "pending", "skipped", "error", "failed"]
)
def test_final_receipt_verdict_sql_matches_python(
    output_type, threshold, reverse, output, outcome, score, status
):
    config = _config(output_type, reverse, threshold)
    stored = {
        "source": "harness",
        "output_type": "Pass/Fail",
        "status": status,
        "output": output,
    }
    expressions = _configured_eval_verdict("eval-1", config)
    query = CallExecution.objects.all().query
    compiler = query.get_compiler(connection=connection)
    compiled = [
        compiler.compile(expr.resolve_expression(query)) for expr in expressions
    ]
    columns = ", ".join(sql for sql, _ in compiled)
    params = [param for _, values in compiled for param in values]
    table = connection.ops.quote_name(CallExecution._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {columns} FROM (SELECT %s::jsonb AS eval_outputs, 1 AS id) {table}",
            [*params, json.dumps({"eval-1": stored})],
        )
        actual_score, passed, failed = cursor.fetchone()
    assert actual_score == (score if status == "completed" else None)
    assert bool(passed) == (status == "completed" and outcome == "passed")
    assert bool(failed) == (status == "completed" and outcome == "failed")
