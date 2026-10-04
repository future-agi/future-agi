"""Regression tests for call-row evaluation outcomes."""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db import connection

from simulate.models import CallExecution
from simulate.services.run_results_v3 import _eval_outcome, call_outcome, eval_rows
from simulate.services.run_results_v3_queries import (
    _configured_eval_verdict,
    run_calls_queryset,
)
from simulate.services.run_results_v3_scoring import (
    EvalScoringSpec,
    judge_stored_eval,
    resolve_eval_scoring_spec,
)


def _config(output_type: str):
    template = SimpleNamespace(
        output_type_normalized=output_type,
        choice_scores=None,
        config={"output": "Pass/Fail" if output_type == "pass_fail" else "score"},
        pass_threshold=0.5,
    )
    return SimpleNamespace(eval_template=template, config={"reverse_output": True})


def test_score_compilation_is_scoped_to_expression_and_resolved_alias():
    from simulate.services import run_results_v3_queries as queries

    query = CallExecution.objects.all().query
    compiler = query.get_compiler(connection=connection)
    score = queries._eval_score("eval-1").resolve_expression(query)
    table = CallExecution._meta.db_table
    with patch.object(
        queries, "_expanded_eval_score", wraps=queries._expanded_eval_score
    ) as expand:
        sql, params = compiler.compile(score)
        expected_params = list(params)
        params.append("must not leak into the next compilation")
        assert compiler.compile(score.copy()) == (sql, expected_params)
        assert expand.call_count == 1

        aliased_sql, aliased_params = compiler.compile(
            score.relabeled_clone({table: "other_call"})
        )
        assert '"other_call"."eval_outputs"' in aliased_sql
        assert aliased_params == expected_params
        assert expand.call_count == 2

        assert query.get_compiler(connection=connection).compile(score) == (
            sql,
            expected_params,
        )
        assert expand.call_count == 2

        fresh_score = queries._eval_score("eval-1").resolve_expression(query)
        assert query.get_compiler(connection=connection).compile(fresh_score) == (
            sql,
            expected_params,
        )
        assert expand.call_count == 3
        with patch.object(connection, "alias", "other_database"):
            assert query.get_compiler(connection=connection).compile(score) == (
                sql,
                expected_params,
            )
        assert expand.call_count == 4
        assert query.get_compiler(connection=connection).compile(score) == (
            sql,
            expected_params,
        )
        assert expand.call_count == 4


def test_score_compilation_distinguishes_evaluators_types_and_choice_weights():
    from simulate.services import run_results_v3_queries as queries

    query = CallExecution.objects.all().query
    compiler = query.get_compiler(connection=connection)
    specifications = [
        ("eval-1", None),
        ("eval-2", None),
        ("eval-1", EvalScoringSpec("pass_fail", 0.5, False, {})),
        ("eval-1", EvalScoringSpec("percentage", 0.5, False, {})),
        ("eval-1", EvalScoringSpec("deterministic", 0.5, False, {"good": 0.1})),
        ("eval-1", EvalScoringSpec("deterministic", 0.5, False, {"good": 0.9})),
    ]
    original = queries._expanded_eval_score
    with patch.object(
        queries, "_expanded_eval_score", wraps=queries._expanded_eval_score
    ) as expand:
        for eval_id, spec in specifications:
            score = queries._eval_score(eval_id, spec).resolve_expression(query)
            actual = compiler.compile(score)
            expected = compiler.compile(
                original(eval_id, spec).resolve_expression(query)
            )
            assert actual == expected
            # A fresh compiler must produce exactly the same SQL and parameters.
            assert actual == query.get_compiler(connection=connection).compile(score)
        assert expand.call_count == len(specifications)


@pytest.mark.parametrize(
    ("stored_output", "expected"),
    [("Passed", "passed"), ("Failed", "failed")],
)
def test_reversed_pass_fail_uses_stored_final_verdict(stored_output, expected):
    config = _config("pass_fail")
    result = {
        "status": "completed",
        "output_type": "Pass/Fail",
        "output": stored_output,
    }

    assert _eval_outcome(result, config) == expected

    call = SimpleNamespace(
        call_metadata={"harness_outcome_status": "passed"},
        status=CallExecution.CallStatus.COMPLETED,
        eval_outputs={"eval-1": result},
    )
    assert call_outcome(call, {"eval-1": config}) == expected


@pytest.mark.parametrize(
    ("raw_score", "expected"),
    [(0.9, "failed"), (0.1, "passed")],
)
def test_reversed_raw_numeric_score_is_still_reversed(raw_score, expected):
    assert (
        _eval_outcome(
            {"status": "completed", "output_type": "score", "output": raw_score},
            _config("percentage"),
        )
        == expected
    )


@pytest.mark.parametrize(
    ("output_type", "output", "runtime", "choices", "expected", "score"),
    [
        ("pass_fail", {"failure": False}, {"reverse_output": True}, {}, "passed", 1),
        ("pass_fail", 0.3, {}, {}, "passed", 1),
        ("percentage", "0.8", {"pass_threshold": 0.7}, {}, "passed", 0.8),
        (
            "deterministic",
            ["good", "bad"],
            {"pass_threshold": 0.6},
            {"good": 1, "bad": 0},
            "failed",
            0.5,
        ),
    ],
)
def test_stored_scoring_uses_binding_configuration(
    output_type, output, runtime, choices, expected, score
):
    config = _config(output_type)
    config.config = {"run_config": runtime}
    config.eval_template.choice_scores = choices

    judgement = judge_stored_eval(
        {"status": "completed", "output": output},
        resolve_eval_scoring_spec(config),
    )

    assert judgement.outcome == expected
    assert judgement.score == score


def test_configured_verdict_query_compiles_without_database():
    config = _config("deterministic")
    config.id = "eval-1"
    config.eval_template.choice_scores = {"good": 1.0, "bad": 0.0}
    with (
        patch(
            "simulate.services.run_results_v3_queries.SimulateEvalConfig.objects.filter"
        ) as filtered,
        patch(
            "simulate.services.run_results_v3_queries.HostedHarnessJob.all_objects.filter"
        ) as jobs,
    ):
        filtered.return_value.select_related.return_value = [config]
        jobs.return_value.values_list.return_value = []
        queryset = run_calls_queryset(SimpleNamespace(run_test=None), [uuid.uuid4()])

    sql = str(queryset.query)
    assert "result_scenario_key" in sql
    assert "customer_cost_cents" in sql
    assert "jsonb_path_exists" in sql


@pytest.mark.parametrize(
    "output",
    [{"score": 1.0, "choice": "Good"}, {"score": 1.0, "choices": ["Good"]}],
)
def test_formatted_choice_uses_attachment_score_override(output):
    config = _config("deterministic")
    config.eval_template.choice_scores = {"Good": 1.0}
    config.config = {
        "run_config": {"choice_scores": {"Good": 0.0}, "pass_threshold": 0.5}
    }

    judgement = judge_stored_eval(
        {"status": "completed", "output": output},
        resolve_eval_scoring_spec(config),
    )

    assert judgement.outcome == "failed"
    assert judgement.score == 0.0


@pytest.mark.parametrize(
    "output,status,expected",
    [
        ({"score": 1.0, "choice": "always"}, "completed", 1.0),
        ({"score": 80, "choice": "mostly"}, "completed", 0.8),
        ({"score": 0, "choice": "never"}, "completed", 0.0),
        ({"score": 0.5, "choices": ["a", "b"]}, "completed", 0.5),
        ({"score": "0.8"}, "completed", None),
        ({"score": True}, "completed", None),
        ({"score": None}, "completed", None),
        ({"score": {"value": 1}}, "completed", None),
        ({"choice": "always"}, "completed", None),
        ({"score": 1.0}, "error", None),
    ],
)
def test_native_choice_score_rows(output, status, expected):
    call = SimpleNamespace(
        eval_outputs={
            "native": {"source": "harness", "output": output, "status": status}
        }
    )
    assert eval_rows(call, {})[0]["score"] == expected


SCORING_PARITY_CASES = [
    ("percentage", {"score": 0.4, "choice": "sometimes"}, 0.5, {}),
    ("percentage", {"score": 80, "choice": "mostly"}, 0.5, {}),
    ("percentage", {"score": 0.5, "choices": ["a", "b"]}, 0.5, {}),
    ("pass_fail", "Failed", 0, {}),
    ("pass_fail", {"failure": True}, 0, {}),
    ("pass_fail", "Passed", 1, {}),
    ("pass_fail", "yes", 0.5, {}),
    ("pass_fail", "no", 0.5, {}),
    ("pass_fail", {"failure": "true"}, 0.5, {}),
    ("percentage", {"result": 0.8}, 0.5, {}),
    ("percentage", {"output": 0.8}, 0.5, {}),
    ("percentage", {"choice": 0.8}, 0.5, {}),
    ("percentage", {"value": 0.8}, 0.5, {}),
    ("percentage", {"score": None, "result": 0.8}, 0.5, {}),
    ("percentage", {"score": "", "result": 0.8}, 0.5, {}),
    ("percentage", {"score": "invalid", "result": 0.8}, 0.5, {}),
    ("percentage", -2, 0.5, {}),
    ("percentage", 200, 0.5, {}),
    ("percentage", " +0.8 ", 0.5, {}),
    ("percentage", "pass", 0.5, {}),
    ("deterministic", "pass", 0.5, {"pass": 0}),
    ("deterministic", {"score": 1, "choice": "pass"}, 0.5, {"pass": 0}),
    ("deterministic", ["good", "bad"], 0.5, {"good": 1, "bad": 0}),
    ("deterministic", "good", 0.5, {"good": 2}),
    ("deterministic", ["bad"], 0.5, {"bad": -2}),
    ("deterministic", [" GOOD ", "good", "bad"], 0.5, {"good": 2, "bad": -1}),
    ("deterministic", {"choices": ["good", "bad"]}, 0.5, {"good": 2, "bad": -1}),
    ("deterministic", ["unmatched"], 0.5, {"good": 1}),
    ("percentage", None, 0.5, {}),
    ("percentage", True, 0.5, {}),
    ("percentage", False, 0.5, {}),
    ("percentage", "NaN", 0.5, {}),
    ("percentage", "-Infinity", 0.5, {}),
]


@pytest.mark.django_db
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(
    "eval_status", ["completed", "pending", "skipped", "error", "failed"]
)
@pytest.mark.parametrize("output_type,output,threshold,choices", SCORING_PARITY_CASES)
def test_sql_scoring_matches_python(
    output_type, output, threshold, choices, reverse, eval_status
):
    config = _config(output_type)
    config.config = {"reverse_output": reverse, "pass_threshold": threshold}
    config.eval_template.choice_scores = choices
    stored = {"status": eval_status, "output": output}
    expected = judge_stored_eval(stored, resolve_eval_scoring_spec(config))
    score, passed, failed = _configured_eval_verdict("eval-1", config)
    query = CallExecution.objects.all().query
    compiler = query.get_compiler(connection=connection)
    expressions = [
        compiler.compile(expr.resolve_expression(query))
        for expr in (score, passed, failed)
    ]
    columns = ", ".join(sql for sql, _ in expressions)
    params = [param for _, values in expressions for param in values]
    table = connection.ops.quote_name(CallExecution._meta.db_table)
    # Evaluate real ORM SQL without persisting a call or requiring related fixtures.
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {columns} FROM (SELECT %s::jsonb AS eval_outputs, 1 AS id) {table}",
            [*params, json.dumps({"eval-1": stored})],
        )
        actual_score, actual_passed, actual_failed = cursor.fetchone()
    assert actual_score == (
        pytest.approx(expected.score) if expected.score is not None else None
    )
    assert bool(actual_passed) == (expected.outcome == "passed")
    assert bool(actual_failed) == (expected.outcome == "failed")
