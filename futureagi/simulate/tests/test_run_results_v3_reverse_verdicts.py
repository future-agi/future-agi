"""Reversal flips the evaluator verdict at the original pass threshold."""

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

REVERSE_CASES = [
    (0.4, 0.7, True, "passed"),
    (0.6, 0.3, True, "failed"),
    (0.5, 0.5, True, "failed"),
    (0.5, 0.5, False, "passed"),
    (0.7, 0.7, True, "failed"),
    (0.6999, 0.7, True, "passed"),
    (0.4, 0.7, False, "failed"),
    (0.6, 0.3, False, "passed"),
]


def _configured_result(output_type, raw_score, threshold, reverse):
    config = SimpleNamespace(
        eval_template=SimpleNamespace(
            output_type_normalized=output_type,
            config={},
            choice_scores={"selected": raw_score},
            pass_threshold=threshold,
        ),
        config={"reverse_output": reverse},
    )
    stored = {
        "status": "completed",
        "output": "selected" if output_type == "deterministic" else raw_score,
    }
    return config, stored


@pytest.mark.parametrize("output_type", ["percentage", "deterministic"])
@pytest.mark.parametrize("raw_score,threshold,reverse,outcome", REVERSE_CASES)
def test_reverse_verdict_uses_original_threshold(
    output_type, raw_score, threshold, reverse, outcome
):
    config, stored = _configured_result(output_type, raw_score, threshold, reverse)
    judgement = judge_stored_eval(stored, resolve_eval_scoring_spec(config))
    assert judgement.outcome == outcome
    assert judgement.score == pytest.approx(1 - raw_score if reverse else raw_score)


@pytest.mark.parametrize("status", ["pending", "skipped", "error", "failed"])
def test_reversal_does_not_measure_an_unfinished_or_errored_eval(status):
    config, stored = _configured_result("percentage", 0.4, 0.7, True)
    stored["status"] = status
    judgement = judge_stored_eval(stored, resolve_eval_scoring_spec(config))
    assert judgement.outcome == ("error" if status in {"error", "failed"} else None)
    assert judgement.score is None


@pytest.mark.parametrize("output", ["Pass", "Fail", True, False, 1, 0])
@pytest.mark.parametrize("reverse", [False, True])
def test_raw_pass_fail_does_not_use_zero_numeric_threshold(output, reverse):
    config, stored = _configured_result("pass_fail", output, 0.0, reverse)
    passed = output == "Pass" or output == 1
    if reverse:
        passed = not passed
    judgement = judge_stored_eval(stored, resolve_eval_scoring_spec(config))
    assert judgement.outcome == ("passed" if passed else "failed")
    assert judgement.score == float(passed)


@pytest.mark.parametrize("output,outcome", [("Passed", "passed"), ("Failed", "failed")])
@pytest.mark.parametrize("reverse", [False, True])
def test_final_pass_fail_remains_final_at_zero_threshold(output, outcome, reverse):
    config, stored = _configured_result("pass_fail", output, 0.0, reverse)
    judgement = judge_stored_eval(stored, resolve_eval_scoring_spec(config))
    assert judgement.outcome == outcome
    assert judgement.score == float(outcome == "passed")


@pytest.mark.django_db
@pytest.mark.parametrize("output", ["Pass", "Fail", True, False, 1, 0])
@pytest.mark.parametrize("reverse", [False, True])
def test_sql_raw_pass_fail_does_not_use_zero_numeric_threshold(output, reverse):
    config, stored = _configured_result("pass_fail", output, 0.0, reverse)
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
    expected = output == "Pass" or output == 1
    if reverse:
        expected = not expected
    assert actual_score == float(expected)
    assert bool(passed) == expected
    assert bool(failed) == (not expected)


@pytest.mark.django_db
@pytest.mark.parametrize("output_type", ["percentage", "deterministic"])
@pytest.mark.parametrize("raw_score,threshold,reverse,outcome", REVERSE_CASES)
def test_sql_reverse_verdict_uses_original_threshold(
    output_type, raw_score, threshold, reverse, outcome
):
    config, stored = _configured_result(output_type, raw_score, threshold, reverse)
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
    assert actual_score == pytest.approx(1 - raw_score if reverse else raw_score)
    assert bool(passed) == (outcome == "passed")
    assert bool(failed) == (outcome == "failed")
