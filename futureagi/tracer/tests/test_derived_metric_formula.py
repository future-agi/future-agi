from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tracer.services.derived_metrics.formula import FormulaError, compile_formula
from tracer.services.derived_metrics.queries import (
    Scope,
    aggregate_select,
    snapshot_select,
)


@pytest.mark.parametrize(
    "formula",
    [
        "__import__('os').system('id')",
        "mean(x.__class__)",
        "mean(x[0])",
        "mean([x for x in y])",
        "mean(open('/etc/passwd'))",
        "mean(x ** 100000)",
        "ratio(x)",
        "mean(x, x)",
        "mean(x, key=1)",
        "mean(1e999)",
        "mean(1)",
        "mean(unknown)",
        "sum(sum(x))",
        "mean(x); DROP TABLE spans",
        "mean('value')",
        "mean(lambda: x)",
    ],
)
def test_rejects_unsafe_or_unbounded_expressions(formula):
    with pytest.raises(FormulaError):
        compile_formula(formula, ["x"])


def test_formula_is_parameterized_and_null_aware():
    result = compile_formula(
        "ratio(resolved == 1 and escalated == 0, resolved >= 0)",
        ["resolved", "escalated"],
    )
    assert result.kind == "ratio"
    assert result.required == ("escalated", "resolved")
    assert "%(constant_0)s" in result.numerator
    assert result.parameters == {
        "constant_0": 1.0,
        "constant_1": 0.0,
        "constant_2": 0.0,
    }
    assert "isNotNull(v_resolved)" in result.complete_sql
    assert "isFinite(v_escalated)" in result.complete_sql


def test_row_division_uses_nullif_not_infinity():
    assert "nullIf(v_y, 0)" in compile_formula("mean(x / y)", ["x", "y"]).numerator


@pytest.mark.parametrize(
    "aliases", [["x", "x"], ["x); DROP TABLE spans"], ["mean"], ["A"], []]
)
def test_rejects_invalid_aliases(aliases):
    with pytest.raises(FormulaError):
        compile_formula("mean(x)", aliases)


def test_complexity_limit():
    with pytest.raises(FormulaError):
        compile_formula("mean(" + "+".join(["x"] * 129) + ")", ["x"])


def definition(**changes):
    values = {
        "formula": "ratio(resolved == 1 and escalated == 0, resolved >= 0)",
        "selection_unit": "session",
        "weighting": "inverse_probability",
        "cohort_key": "region",
        "inputs": [
            {
                "alias": alias,
                "source": "eval",
                "config_id": str(uuid4()),
                "config_hash": "a" * 64,
                "reducer": "latest",
            }
            for alias in ["resolved", "escalated"]
        ],
    }
    return SimpleNamespace(**{**values, **changes})


def test_snapshot_preaggregates_each_eval_and_session_before_join(settings):
    settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger_v2"
    scope = Scope(uuid4(), uuid4(), uuid4())
    now = datetime.now(UTC)
    sql, params = snapshot_select(
        definition(),
        scope,
        now - timedelta(days=1),
        now,
        now,
        [{"field": "model", "value": "'; DROP TABLE spans;--"}],
        "",
    )
    assert "session_members AS" in sql
    assert "HAVING uniqExact(m.unit_id) = 1" in sql
    assert "INNER JOIN session_members m ON e.target_id = m.session_id" in sql
    assert "evaluation_values AS" in sql
    assert "AS v_resolved" in sql and "AS v_escalated" in sql
    assert sql.count("GROUP BY unit_id") == 1
    assert (
        "LIMIT 1 BY custom_eval_config_id, ifNull(config_hash, ''), target_type, target_id"
        in sql
    )
    assert "DROP TABLE" not in sql
    assert params["filter_0"] == "'; DROP TABLE spans;--"
    assert params["project_id"] == str(scope.project)
    assert params["org_id"] == str(scope.organization)
    assert "ifNull(config_hash, '') = %(hash_resolved)s" in sql
    assert "updated_at <= %(as_of)s" in sql
    assert "_version DESC LIMIT 1 BY project_id, trace_id, id" in sql
    assert "is_deleted = 0" in sql


def test_span_units_do_not_duplicate_session_target_evaluations(settings):
    settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger_v2"
    now = datetime.now(UTC)
    sql, _ = snapshot_select(
        definition(selection_unit="span"),
        Scope(uuid4(), uuid4(), uuid4()),
        now,
        now,
        now,
        [],
        "",
    )
    assert "session_members AS" not in sql
    assert "WHERE e.target_type != 'session'" in sql


def test_rollups_are_weighted_sums_not_means_of_ratios():
    formula = compile_formula("ratio(resolved == 1, resolved >= 0)", ["resolved"])
    sql = aggregate_select("SELECT * FROM frozen", formula, rollup="user")
    assert "n / nullIf(probability, 0)" in sql
    assert "d / nullIf(probability, 0)" in sql
    assert "weighted_n / nullIf(weighted_d, 0)" in sql
    assert "GROUP BY group_key" in sql
    assert "invalid_probability_count > 0, 'invalid_probability'" in sql
    assert "unit_count = 0, 'no_data'" in sql
    assert "valid_count = 0, 'missing_inputs'" in sql


def test_count_has_no_denominator_and_empty_is_not_zero():
    sql = aggregate_select(
        "SELECT * FROM frozen",
        compile_formula("count_if(x > 0)", ["x"]),
        rollup="overall",
    )
    assert "if(valid_count > 0 AND isFinite(NULL), NULL, NULL) AS denominator" in sql
    assert "if(status = 'ok', computed_value, NULL)" in sql
    assert "GROUP BY group_key" not in sql
