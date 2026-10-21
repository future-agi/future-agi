from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
import sqlglot
from sqlglot import exp

from tracer.sql_workbench.catalog import COLUMNS, Scope, sources
from tracer.sql_workbench.compiler import (
    MAX_RESULT_ROWS,
    UnsafeQuery,
    compile_query,
)


@pytest.fixture
def scope():
    end = datetime(2026, 10, 21, tzinfo=timezone.utc)
    return Scope(
        uuid4(),
        uuid4(),
        uuid4(),
        end - timedelta(days=1),
        end,
        (uuid4(),),
        database="analytics",
    )


@pytest.mark.parametrize(
    "query",
    [
        "SELECT model, count() AS n FROM spans GROUP BY model ORDER BY n DESC",
        "SELECT s.id, e.score FROM sessions AS s JOIN eval_results AS e ON s.id = e.session_id",
        "WITH x AS (SELECT id FROM spans) SELECT * FROM x",
        "WITH x AS (SELECT id FROM spans), y AS (SELECT id FROM users) SELECT * FROM x UNION ALL SELECT toString(id) FROM y",
        "SELECT name FROM spans WHERE id IN (SELECT span_id FROM eval_results WHERE score < 0.5)",
        "SELECT * FROM (SELECT name FROM spans) AS s LEFT JOIN (SELECT toString(id) AS id FROM traces) AS t ON s.name = t.id",
        "SELECT name, row_number() OVER (PARTITION BY model ORDER BY start_time) AS rank FROM spans",
        "SELECT * FROM annotations UNION ALL SELECT * FROM annotations",
        "SELECT attrs_string['locale'] AS locale, sum(cost) AS spend FROM spans GROUP BY locale",
        "SELECT coalesce(label, 'unknown') AS label FROM eval_results",
    ],
)
def test_valid_queries_rewrite_every_physical_source(query, scope):
    compiled = compile_query(query, scope)
    tree = sqlglot.parse_one(compiled, read="clickhouse")
    assert tree.args["limit"].expression.this == str(MAX_RESULT_ROWS + 1)
    physical = [t for t in tree.find_all(exp.Table) if t.db]
    assert physical
    assert all(t.db == "analytics" for t in physical)
    assert str(scope.project_id) in compiled
    assert not any(t.name == "eval_results" and t.db for t in tree.find_all(exp.Table))


@pytest.mark.parametrize(
    "query",
    [
        "DROP TABLE spans",
        "SELECT * FROM spans; SELECT * FROM users",
        "SELECT * FROM spans; -- hide\n DELETE FROM spans",
        "INSERT INTO spans SELECT * FROM users",
        "ALTER TABLE spans DELETE WHERE 1",
        "SELECT * FROM spans INTO OUTFILE '/tmp/leak'",
        "SELECT * FROM spans SETTINGS max_execution_time=0",
        "SELECT * FROM spans FORMAT Parquet",
        "SELECT * FROM system.tables",
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM analytics.spans",
        "SELECT * FROM remote('attacker', 'db', 'table')",
        "SELECT * FROM url('https://attacker', 'CSV')",
        "SELECT * FROM file('/etc/passwd', 'CSV')",
        "SELECT * FROM s3('https://attacker', 'CSV')",
        "SELECT * FROM mysql('attacker','db','t','u','p')",
        "SELECT * FROM numbers(1000000000)",
        "SELECT * FROM merge('.*', '.*')",
        "SELECT * FROM view(SELECT * FROM system.tables)",
        "SELECT * FROM clusterAllReplicas('cluster', system.tables)",
        "SELECT * FROM input('x String')",
        "SELECT dictGet('secret', 'password', id) FROM spans",
        "SELECT getSetting('password') FROM spans",
        "SELECT currentDatabase() FROM spans",
        "SELECT version() FROM spans",
        "SELECT sleep(3) FROM spans",
        "SELECT filesystemAvailable('/') FROM spans",
        "SELECT file('/etc/passwd') FROM spans",
        "SELECT arrayJoin(range(1000000000)) FROM spans",
        "SELECT generateRandom() FROM spans",
        "SELECT * FROM spans CROSS JOIN users",
        "SELECT * FROM spans, users",
        "SELECT * FROM spans ARRAY JOIN attrs_string",
        "SELECT * FROM spans GLOBAL JOIN users ON 1=1",
        "SELECT * FROM spans FINAL",
        "SELECT * FROM spans SAMPLE 0.1",
        "WITH x AS (SELECT * FROM system.tables) SELECT * FROM x",
        "SELECT * FROM spans UNION ALL SELECT * FROM system.tables",
        "SELECT * FROM spans WHERE name IN (SELECT name FROM system.tables)",
        "SELECT (SELECT version()) FROM spans",
        "WITH RECURSIVE x AS (SELECT * FROM spans) SELECT * FROM x",
        "SELECT * FROM spans JOIN unknown ON 1=1",
        "SELECT * FROM spans JOIN remote('x', 't') ON 1=1",
        "SELECT * FROM `spans;DROP TABLE users`",
        "SELECT * FROM `ѕpans`",
        "SELECT * FROM `spans\n`",
        "SELECT * FROM {table:Identifier}",
        "SELECT * FROM spans WHERE name = {name:String}",
        "SELECT secret FROM tracer_eval_logger",
        "SHOW TABLES",
        "EXPLAIN SELECT * FROM spans",
        "SELECT * FROM spans UNION ALL SELECT * FROM file('/etc/passwd', 'CSV')",
    ],
)
def test_adversarial_sql_is_rejected(query, scope):
    with pytest.raises(UnsafeQuery):
        compile_query(query, scope)


def test_literal_attack_text_is_not_interpreted(scope):
    sql = "SELECT name FROM spans WHERE name = 'x; DROP TABLE system.users --'"
    assert "DROP TABLE" in compile_query(sql, scope)


def test_cte_cannot_shadow_physical_catalog(scope):
    sql = "WITH spans AS (SELECT * FROM users) SELECT * FROM spans"
    compiled = compile_query(sql, scope)
    assert "analytics.end_users" in compiled
    assert str(scope.organization_id) in compiled


@pytest.mark.parametrize(
    "dataset", [name for name in COLUMNS if name != "derived_metrics"]
)
def test_every_dataset_has_scope_and_deletion_predicate(dataset, scope):
    sql = sources(scope)[dataset]
    assert str(scope.project_id) in sql
    assert "is_deleted = 0" in sql
    if dataset in {"spans", "users", "annotations", "eval_results", "sessions"}:
        assert str(scope.organization_id) in sql
    if dataset == "annotations":
        assert f"workspace_id = toUUID('{scope.workspace_id}')" in sql
        assert "tracer_project_id =" in sql
        assert "IS NULL" not in sql


def test_eval_requires_config_and_target_and_no_null_shortcut(scope):
    sql = sources(scope)["eval_results"]
    assert f"custom_eval_config_id IN (toUUID('{scope.eval_config_ids[0]}'))" in sql
    assert (
        "target_type = 'span'" in sql
        and "target_type = 'trace'" in sql
        and "target_type = 'session'" in sql
    )
    assert "WHERE ((target_type" in sql
    assert "tuple(observation_span_id, toString(trace_id)) IN" in sql
    assert "trace_id IS NOT NULL" in sql
    assert "OR project_id IS NULL" not in sql


def test_no_configs_is_empty_eval_dataset(scope):
    from dataclasses import replace

    assert "AND 0 AND" in sources(replace(scope, eval_config_ids=()))["eval_results"]


@pytest.mark.parametrize("field", ["organization_id", "workspace_id", "project_id"])
@pytest.mark.parametrize(
    "value", [None, "'; SELECT * FROM system.tables --", "x", UUID(int=0)]
)
def test_missing_or_malicious_scope_never_compiles(scope, field, value):
    from dataclasses import replace

    with pytest.raises(ValueError):
        replace(scope, **{field: value})


def test_window_is_bounded(scope):
    from dataclasses import replace

    for start in [
        scope.end,
        scope.end - timedelta(days=32),
        scope.start.replace(tzinfo=None),
    ]:
        with pytest.raises(ValueError):
            replace(scope, start=start)


def test_limits_and_unknown_function(scope):
    with pytest.raises(UnsafeQuery):
        compile_query("SELECT '" + "a" * 33000 + "' FROM spans", scope)
    with pytest.raises(UnsafeQuery):
        compile_query("SELECT futureDangerousFunction(name) FROM spans", scope)


def test_other_project_is_not_injected(scope):
    second = uuid4()
    sql = compile_query(f"SELECT * FROM spans WHERE trace_id = '{second}'", scope)
    assert f"project_id = toUUID('{scope.project_id}')" in sql
    assert f"project_id = toUUID('{second}')" not in sql


def test_task_is_not_fabricated_run_lineage_and_sampling_is_nullable(scope):
    sql = sources(scope)["eval_results"]
    assert "CAST(NULL AS Nullable(String)) AS run_id, eval_task_id" in sql
    assert "eval_task_id AS run_id" not in sql
    assert "'_task_selection', 'sampling', 'stratum', 'Nullable(String)'" in sql
    assert "'inclusion_probability', 'Nullable(Float64)'" in sql
    assert "AS sampling_window_start" in sql
    assert "AS population_size" in sql


def test_optional_metrics_is_explicitly_unavailable_until_enabled(scope):
    from tracer.sql_workbench.catalog import CatalogUnavailable, catalog

    with pytest.raises(CatalogUnavailable):
        compile_query("SELECT value FROM derived_metrics", scope)
    item = next(value for value in catalog() if value["name"] == "derived_metrics")
    assert item["available"] is False and item["unavailable_reason"]


def test_derived_metrics_uses_all_scope_keys_and_committed_view(scope):
    from dataclasses import replace

    sql = compile_query(
        "SELECT d.rollup, sum(d.value) AS total FROM derived_metrics AS d GROUP BY d.rollup",
        replace(scope, derived_metrics_enabled=True),
    )
    for value in [scope.organization_id, scope.workspace_id, scope.project_id]:
        assert str(value) in sql
    assert "analytics.derived_metrics" in sql
    assert "derived_metrics FINAL" not in sql
    assert "window_start >=" in sql and "window_end <=" in sql


def test_recent_eval_ownership_is_candidate_bounded_not_target_time_bounded(scope):
    sql = sources(scope)["eval_results"]
    assert "created_at >=" in sql
    assert "start_time >=" not in sql
    assert "first_seen >=" not in sql
    assert "AND id IN (SELECT observation_span_id FROM __sql_eval_candidates" in sql
    compiled = compile_query("SELECT score FROM eval_results", scope)
    assert "analytics.__sql_eval_candidates" not in compiled
    assert "analytics.spans FINAL" in compiled


def test_intrinsic_row_policies_do_not_recurse_or_apply_fact_windows(scope):
    from tracer.sql_workbench.catalog import ownership_predicates

    for predicate in ownership_predicates(scope).values():
        assert "SELECT " not in predicate
        assert (
            "created_at" not in predicate
            and "first_seen" not in predicate
            and "start_time" not in predicate
        )


@pytest.mark.parametrize(
    "expression",
    [
        "toUUID('11111111-1111-4111-8111-111111111111')",
        "(SELECT id FROM traces LIMIT 1)",
    ],
)
def test_scalar_with_alias_cannot_capture_scope_columns(scope, expression):
    with pytest.raises(UnsafeQuery, match="scalar WITH"):
        compile_query(
            f"WITH {expression} AS project_id SELECT count() FROM traces", scope
        )


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id FROM spans WHERE status = 'ERROR' AND (cost > 0 OR total_tokens > 0)",
        "SELECT s.id FROM spans s JOIN spans t ON s.id=t.id AND s.trace_id=t.trace_id",
        "SELECT sumIf(cost, status='ERROR'), avgIf(cost, status='OK'), countIf(status='ERROR') FROM spans",
        "SELECT toStartOfHour(start_time), toStartOfDay(start_time), dateDiff('day', start_time, now()), dateAdd('day', 1, start_time) FROM spans",
        "SELECT id FROM spans WHERE start_time >= now() - INTERVAL 1 DAY",
        "SELECT quantile(0.95)(latency_ms), quantileExact(0.5)(cost), now() FROM spans",
    ],
)
def test_reviewed_boolean_date_and_aggregate_grammar(sql, scope):
    assert "analytics.spans" in compile_query(sql, scope)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT sumState(cost) FROM spans",
        "SELECT groupArrayIf(name, status='ERROR') FROM spans",
        "SELECT dateAdd('unreviewed_unit', 1, start_time) FROM spans",
        "SELECT id FROM spans WHERE status='OK' OR sleep(10)=0",
        "SELECT s.id FROM spans s JOIN spans t ON s.id=t.id OR s.trace_id=t.trace_id",
        "SELECT id FROM spans WHERE name IN (SELECT name FROM system.tables) AND 1=1",
    ],
)
def test_grammar_extensions_do_not_expand_privileges(sql, scope):
    with pytest.raises(UnsafeQuery):
        compile_query(sql, scope)
