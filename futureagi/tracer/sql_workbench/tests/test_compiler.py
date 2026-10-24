from datetime import UTC, datetime, timedelta, timezone
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
from tracer.sql_workbench.examples import EXAMPLES


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


VALID_QUERIES = [
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
]


@pytest.mark.parametrize("query", VALID_QUERIES)
def test_valid_queries_rewrite_every_physical_source(query, scope):
    compiled = compile_query(query, scope)
    tree = sqlglot.parse_one(compiled, read="clickhouse")
    assert tree.args["limit"].expression.this == str(MAX_RESULT_ROWS + 1)
    physical = [t for t in tree.find_all(exp.Table) if t.db]
    assert physical
    assert all(t.db == "analytics" for t in physical)
    assert str(scope.project_id) in compiled
    assert not any(t.name == "eval_results" and t.db for t in tree.find_all(exp.Table))


ADVERSARIAL_QUERIES = [
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
]


@pytest.mark.parametrize("query", ADVERSARIAL_QUERIES)
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


REVIEWED_GRAMMAR = [
    "SELECT id FROM spans WHERE status = 'ERROR' AND (cost > 0 OR total_tokens > 0)",
    "SELECT s.id FROM spans s JOIN spans t ON s.id=t.id AND s.trace_id=t.trace_id",
    "SELECT sumIf(cost, status='ERROR'), avgIf(cost, status='OK'), countIf(status='ERROR') FROM spans",
    "SELECT toStartOfHour(start_time), toStartOfDay(start_time), dateDiff('day', start_time, now()), dateAdd('day', 1, start_time) FROM spans",
    "SELECT id FROM spans WHERE start_time >= now() - INTERVAL 1 DAY",
    "SELECT quantile(0.95)(latency_ms), quantileExact(0.5)(cost), now() FROM spans",
]


@pytest.mark.parametrize("sql", REVIEWED_GRAMMAR)
def test_reviewed_boolean_date_and_aggregate_grammar(sql, scope):
    assert "analytics.spans" in compile_query(sql, scope)


GRAMMAR_ESCAPES = [
    "SELECT sumState(cost) FROM spans",
    "SELECT groupArrayIf(name, status='ERROR') FROM spans",
    "SELECT dateAdd('unreviewed_unit', 1, start_time) FROM spans",
    "SELECT id FROM spans WHERE status='OK' OR sleep(10)=0",
    "SELECT s.id FROM spans s JOIN spans t ON s.id=t.id OR s.trace_id=t.trace_id",
    "SELECT id FROM spans WHERE name IN (SELECT name FROM system.tables) AND 1=1",
]


@pytest.mark.parametrize("sql", GRAMMAR_ESCAPES)
def test_grammar_extensions_do_not_expand_privileges(sql, scope):
    with pytest.raises(UnsafeQuery):
        compile_query(sql, scope)


# ClickHouse reads the right side of IN as a whole table when it is a bare,
# parenthesized, quoted or database-qualified name, or a table alias / CTE name
# (verified on the owned 25.3 server: ``'x' IN (t)`` and ``'x' IN a`` read t).
# SQLGlot parses these as an In whose right side is a Column, not a Table, so
# they must be rejected outright: they can never be rewritten to a scope.
IN_TABLE_BYPASSES = [
    "SELECT id FROM spans WHERE id IN spans",
    "SELECT id FROM spans WHERE id IN system.tables",
    "SELECT id FROM spans WHERE (id, trace_id) IN spans",
    "SELECT id FROM spans WHERE id GLOBAL IN spans",
    "SELECT id FROM spans WHERE id NOT IN spans",
    "SELECT id FROM spans WHERE id GLOBAL NOT IN traces",
    "SELECT id FROM spans WHERE NOT id IN spans",
    "SELECT id FROM spans WHERE id IN (spans)",
    "SELECT id FROM spans WHERE id IN ((spans))",
    "SELECT id FROM spans WHERE id IN (spans, traces)",
    "SELECT id FROM spans WHERE id IN ('x', spans)",
    "SELECT id FROM spans WHERE id IN `spans`",
    'SELECT id FROM spans WHERE id IN "system"."tables"',
    "SELECT id FROM spans WHERE id IN `system`.`tables`",
    "SELECT id FROM spans WHERE id IN analytics.spans",
    "SELECT id FROM spans WHERE id IN default.spans",
    "SELECT id FROM spans WHERE id IN tracer_eval_logger",
    "SELECT id FROM spans WHERE id IN tracer_eval_logger_v2",
    "SELECT id FROM spans WHERE id IN model_hub_score",
    "SELECT id FROM spans WHERE id IN end_users",
    "SELECT id FROM spans WHERE id IN trace_sessions",
    "SELECT id FROM spans WHERE id IN derived_metric_results",
    "SELECT id FROM spans WHERE id IN information_schema.tables",
    "SELECT id FROM spans WHERE id iN SPANS",
    "SELECT id FROM spans WHERE id IN /* c */ spans",
    "SELECT id FROM spans WHERE id IN\n-- c\nspans",
    "SELECT id FROM spans WHERE id IN\tspans",
    "SELECT id FROM spans AS s WHERE s.id IN s",
    "WITH own AS (SELECT id FROM spans) SELECT id FROM spans WHERE id IN own",
    "SELECT id IN spans FROM spans",
    "SELECT id FROM spans GROUP BY id HAVING id IN spans",
    "SELECT CASE WHEN id IN spans THEN 1 ELSE 0 END FROM spans",
    "SELECT if(id IN spans, 1, 0) FROM spans",
    "SELECT id FROM spans ORDER BY id IN spans",
    "SELECT count() FROM spans GROUP BY id IN spans",
    "SELECT id FROM (SELECT id FROM spans WHERE id IN spans)",
    "SELECT id FROM spans WHERE id IN (SELECT id FROM spans WHERE trace_id IN traces)",
    "SELECT id FROM spans UNION ALL SELECT id FROM spans WHERE id IN spans",
    "WITH x AS (SELECT id FROM spans WHERE id IN spans) SELECT id FROM x",
    "SELECT s.id FROM spans AS s JOIN traces AS t ON s.trace_id = t.id WHERE s.id IN spans",
    "SELECT (SELECT count() FROM spans WHERE id IN spans) FROM spans",
    "SELECT id FROM spans WHERE id IN (spans.id)",
    "SELECT id FROM spans WHERE id IN (id, trace_id)",
    "SELECT id FROM spans WHERE id IN tuple('a')",
    "SELECT id FROM spans WHERE id IN numbers(10)",
    "SELECT id FROM spans WHERE id IN cluster('c', system.tables)",
    "SELECT id FROM spans WHERE id IN {t:Identifier}",
    "SELECT id FROM spans WHERE id IN 'spans'",
    "SELECT id FROM spans WHERE id GLOBAL IN (SELECT id FROM spans)",
    "SELECT id FROM spans WHERE id IN (SELECT id FROM spans) AND trace_id IN spans",
    "SELECT arrayMap(x -> x IN spans, [1]) FROM spans",
]

# Function forms of IN and functions that take a table, dictionary or setting
# name as an argument.
TABLE_NAME_FUNCTIONS = [
    "SELECT in(id, spans) FROM spans",
    "SELECT globalIn(id, spans) FROM spans",
    "SELECT notIn(id, spans) FROM spans",
    "SELECT globalNotIn(id, spans) FROM spans",
    "SELECT nullIn(id, spans) FROM spans",
    "SELECT dictGet('d', 'a', id) FROM spans",
    "SELECT dictGetOrDefault('d', 'a', id, '') FROM spans",
    "SELECT dictGetString('d', 'a', toUInt64(1)) FROM spans",
    "SELECT dictHas('d', id) FROM spans",
    "SELECT joinGet('j', 'v', id) FROM spans",
    "SELECT joinGetOrNull('j', 'v', id) FROM spans",
    "SELECT hasColumnInTable('default', 'spans', 'id') FROM spans",
    "SELECT getSetting('additional_table_filters') FROM spans",
    "SELECT * FROM spans WHERE EXISTS (SELECT 1 FROM system.tables)",
    "SELECT * FROM spans WHERE id = ANY (SELECT name FROM system.tables)",
    "SELECT * FROM (spans)",
    "SELECT * FROM spans LEFT ARRAY JOIN attrs_string",
    "SELECT * FROM spans AS s JOIN traces AS t USING (id) SETTINGS a = 1",
]


# Every other clause or node that can carry a table name or take one, with an
# IN-table read or a physical name placed in it.
CLAUSE_SWEEP = [
    "SELECT id FROM spans JOIN traces USING (id) WHERE id IN spans",
    "SELECT s.id FROM spans AS s JOIN traces AS t ON s.id = t.id AND s.id IN spans",
    "SELECT * FROM spans WHERE id = ALL (SELECT name FROM system.tables)",
    "SELECT * FROM spans WHERE id != ANY (SELECT id FROM spans)",
    "SELECT * FROM spans JOIN system.tables USING (name)",
    "SELECT * FROM spans AS s JOIN (traces) AS t ON s.trace_id = t.id",
    "SELECT * FROM spans SAMPLE 1/10 OFFSET 1/2",
    "SELECT * FROM spans AS s FINAL",
    "SELECT dictHas('d', toUInt64(1)) FROM spans",
    "SELECT * FROM (SELECT id FROM spans SETTINGS additional_table_filters = {}) AS x",
    "SELECT id FROM spans SETTINGS additional_table_filters = {'spans': '1'}",
    "SELECT id FROM spans INTERSECT SELECT name FROM system.tables",
    "SELECT id FROM spans EXCEPT SELECT name FROM system.tables",
    "SELECT id FROM spans WHERE id IN (SELECT id FROM spans INTERSECT SELECT name FROM system.tables)",
    "SELECT id FROM spans WHERE id IN (SELECT number FROM numbers(1))",
    "SELECT arrayExists(x -> x IN spans, [id]) FROM spans",
    "SELECT id FROM spans WHERE EXISTS spans",
    "SELECT id FROM spans ARRAY JOIN [1, 2] AS x",
    "SELECT id FROM spans WHERE id IN (SELECT id FROM spans) AND id IN `traces`",
    "SELECT id FROM spans WHERE id IN [spans]",
    "SELECT id FROM spans WHERE id IN tuple(spans)",
    "SELECT id FROM spans WHERE id IN (SELECT id FROM spans) OR id IN system.one",
    "SELECT joinGet(spans, 'name', id) FROM spans",
    "SELECT * FROM spans WHERE id IN (WITH x AS (SELECT 1) SELECT * FROM system.tables)",
    "SELECT id, (id IN traces) AS hit FROM spans",
    "SELECT count() OVER (PARTITION BY id IN spans) FROM spans",
    "SELECT id FROM spans ORDER BY id IN spans WITH FILL",
    "SELECT id FROM spans LIMIT 1 BY id IN spans",
    "SELECT id FROM spans QUALIFY id IN spans",
    "SELECT id FROM spans PREWHERE id IN spans",
    "SELECT * FROM spans AS s INNER ANY JOIN traces AS t ON s.trace_id = t.id WHERE s.id IN traces",
    "SELECT * FROM spans AS s PASTE JOIN traces AS t",
]


@pytest.mark.parametrize(
    "query", IN_TABLE_BYPASSES + TABLE_NAME_FUNCTIONS + CLAUSE_SWEEP
)
def test_names_where_clickhouse_expects_a_table_are_rejected(query, scope):
    with pytest.raises(UnsafeQuery):
        compile_query(query, scope)


@pytest.mark.parametrize(
    "query",
    [
        "SELECT id FROM spans WHERE id IN ('a', 'b')",
        "SELECT id FROM spans WHERE id NOT IN ('a')",
        "SELECT id FROM spans WHERE (id, trace_id) IN (('a', 'b'), ('c', 'd'))",
        "SELECT id FROM spans WHERE id IN [1, 2]",
        "SELECT id FROM spans WHERE total_tokens IN (-1, 2)",
        "SELECT id FROM spans WHERE session_id IN (toUUID('11111111-1111-4111-8111-111111111111'))",
        "SELECT id FROM spans WHERE id IN (SELECT span_id FROM eval_results)",
        "SELECT id FROM spans WHERE id NOT IN (SELECT span_id FROM eval_results WHERE score < 0.5)",
        "WITH own AS (SELECT id FROM spans) SELECT id FROM spans WHERE id IN (SELECT id FROM own)",
        "SELECT id FROM spans WHERE id IN (SELECT id FROM spans UNION ALL SELECT span_id FROM eval_results)",
    ],
)
def test_reviewed_in_forms_compile_fully_scoped(query, scope):
    compiled = compile_query(query, scope)
    tree = sqlglot.parse_one(compiled, read="clickhouse")
    assert all(node.args.get("field") is None for node in tree.find_all(exp.In)), (
        compiled
    )
    physical = [t for t in tree.find_all(exp.Table) if t.db]
    assert physical and all(t.db == "analytics" for t in physical)


def test_table_argument_functions_are_never_approved():
    from tracer.sql_workbench import compiler

    approved = compiler.FUNCTIONS | compiler.FUNCTION_ALIASES
    assert not approved & compiler.TABLE_ARGUMENT_FUNCTIONS
    assert not [name for name in approved if name.startswith(("dict", "joinget"))]


def test_user_cte_names_never_reach_clickhouse(scope):
    sql = (
        "WITH spans AS (SELECT id FROM users), tracer_eval_logger AS "
        "(SELECT id FROM traces) SELECT id FROM spans UNION ALL "
        "SELECT toString(id) FROM tracer_eval_logger"
    )
    compiled = compile_query(sql, scope)
    tree = sqlglot.parse_one(compiled, read="clickhouse")
    names = {cte.alias for cte in tree.find_all(exp.CTE)}
    assert {"__sql_cte_0", "__sql_cte_1"} <= names
    assert not names & {"spans", "tracer_eval_logger"}
    unqualified = {t.name for t in tree.find_all(exp.Table) if not t.db}
    assert unqualified <= names | {"__sql_eval_candidates"}
    # The user's names survive only as aliases, so x.col references still work.
    assert "__sql_cte_0 AS spans" in compiled


def test_verification_is_independent_of_the_in_rule(scope, monkeypatch):
    """A regression in the validation pass must still fail closed."""
    from tracer.sql_workbench import compiler

    monkeypatch.setattr(compiler, "_check_in", lambda node: None)
    for query in [
        "SELECT id FROM spans WHERE id IN spans",
        "SELECT id FROM spans WHERE id IN (spans)",
        "SELECT id FROM spans WHERE id IN system.tables",
    ]:
        with pytest.raises(UnsafeQuery, match="verified as fully scoped"):
            compile_query(query, scope)


def test_verification_catches_a_source_the_rewrite_missed(scope, monkeypatch):
    """Simulate a scope-analysis gap: one catalog reference is not rewritten."""
    from tracer.sql_workbench import compiler

    real = compiler._analyze

    def drops_last_reference(sql):
        tree, references, links = real(sql)
        return tree, references[:-1], links

    monkeypatch.setattr(compiler, "_analyze", drops_last_reference)
    with pytest.raises(UnsafeQuery, match="verified as fully scoped"):
        compile_query(
            "SELECT s.id FROM spans AS s JOIN traces AS t ON s.trace_id = t.id", scope
        )


@pytest.mark.parametrize(
    "remainder",
    [
        "SELECT * FROM (SELECT id FROM analytics.spans) AS query_result LIMIT 10001",
        "SELECT * FROM (SELECT id FROM spans) AS query_result LIMIT 10001",
        "SELECT * FROM (SELECT 1 WHERE 'x' IN spans) AS query_result LIMIT 10001",
        "SELECT * FROM (SELECT 1 WHERE 'x' IN (spans)) AS query_result LIMIT 10001",
        "SELECT * FROM (WITH spans AS (SELECT 1) SELECT * FROM spans) AS query_result",
        "SELECT * FROM (SELECT * FROM __sql_cte_0) AS query_result",
        "SELECT * FROM (SELECT dictGet('d', 'a', 1)) AS query_result",
        "SELECT * FROM (SELECT analytics.spans.id) AS query_result",
        "SELECT * FROM (SELECT 1 SETTINGS readonly = 0) AS query_result",
        "SELECT * FROM numbers(10)",
    ],
)
def test_verification_rejects_any_physical_or_unscoped_remainder(remainder):
    from tracer.sql_workbench.compiler import _verify_compiled

    with pytest.raises(UnsafeQuery):
        _verify_compiled(remainder, {})


def test_verification_requires_each_generated_source_exactly(scope):
    from collections import Counter

    from tracer.sql_workbench.compiler import _verify_compiled

    compiled = compile_query("SELECT count() FROM spans", scope)
    spans = sqlglot.parse_one(sources(scope)["spans"], read="clickhouse")
    for table in spans.find_all(exp.Table):
        table.set("db", exp.to_identifier(scope.database))
    text = spans.sql(dialect="clickhouse")
    _verify_compiled(compiled, Counter({text: 1}))
    for wrong in [Counter(), Counter({text: 2}), Counter({text + " ": 1})]:
        with pytest.raises(UnsafeQuery):
            _verify_compiled(compiled, wrong)


def test_compiled_text_is_bounded_by_the_profile_query_size(scope):
    from dataclasses import replace

    from tracer.sql_workbench import runner
    from tracer.sql_workbench.compiler import MAX_COMPILED_BYTES

    assert runner.QUERY_SETTINGS["max_query_size"] == MAX_COMPILED_BYTES
    many = replace(scope, eval_config_ids=tuple(uuid4() for _ in range(1000)))
    one = compile_query("SELECT count() FROM eval_results", many)
    assert len(one.encode()) < MAX_COMPILED_BYTES
    with pytest.raises(UnsafeQuery, match="too large"):
        compile_query(
            " UNION ALL ".join(["SELECT count() FROM eval_results"] * 6), many
        )


def test_deleted_configurations_are_kept_and_flagged(scope):
    from dataclasses import replace

    deleted = uuid4()
    flagged = replace(
        scope,
        eval_config_ids=(*scope.eval_config_ids, deleted),
        deleted_eval_config_ids=(deleted,),
    )
    sql = sources(flagged)["eval_results"]
    assert f"toUUID('{deleted}')" in sql.split("FROM tracer_eval_logger")[1]
    assert (
        f"toUInt8(custom_eval_config_id IN (toUUID('{deleted}'))) AS config_deleted"
        in sql
    )
    assert "toUInt8(0) AS config_deleted" in sources(scope)["eval_results"]
    with pytest.raises(ValueError):
        replace(scope, deleted_eval_config_ids=(uuid4(),))


def test_more_than_the_config_limit_is_an_explicit_error_not_partial(scope):
    from dataclasses import replace

    from tracer.sql_workbench.catalog import EvalConfigLimitExceeded, catalog

    with pytest.raises(EvalConfigLimitExceeded, match="1,000"):
        replace(scope, eval_config_ids=tuple(uuid4() for _ in range(1001)))
    item = next(
        value
        for value in catalog(False, "", "too many configurations")
        if value["name"] == "eval_results"
    )
    assert item["available"] is False
    assert item["unavailable_reason"] == "too many configurations"
    assert all(
        value["available"]
        for value in catalog(True, "", "")
        if value["name"] != "derived_metrics"
    )


def test_additional_table_filters_cover_every_grantable_relation(scope):
    from dataclasses import replace

    from tracer.sql_workbench.catalog import (
        DERIVED_METRIC_RELATIONS,
        additional_table_filters,
        intrinsic_ownership,
        ownership_predicates,
    )
    from tracer.sql_workbench.provision import service_access_ddl

    enabled = replace(scope, derived_metrics_enabled=True)
    filters = intrinsic_ownership(enabled)
    physical = {
        table.name
        for text in sources(enabled).values()
        for table in sqlglot.parse_one(text, read="clickhouse").find_all(exp.Table)
        if table.name != "__sql_eval_candidates"
    }
    granted = set()
    for eval_table in ("tracer_eval_logger", "tracer_eval_logger_v2"):
        for statement in service_access_ddl(
            database="analytics",
            principal="sql_service",
            password="secret" * 5,
            cidr="10.3.0.0/24",
            eval_table=eval_table,
            include_derived_metrics=True,
        )[1:]:
            granted.add(statement.split(" ON analytics.")[1].split(" ")[0])
    # Every table a source reads and every relation the service principal can
    # ever be granted carries a filter, whether or not the feature is enabled.
    assert physical | granted | set(ownership_predicates(enabled)) <= set(filters)
    assert intrinsic_ownership(scope) == filters
    assert set(DERIVED_METRIC_RELATIONS) <= set(filters)
    for predicate in filters.values():
        # Never a deletion flag or event time: FINAL-safe, intrinsic ownership.
        assert "deleted" not in predicate and "created_at" not in predicate
        assert "project_id = toUUID(" in predicate or "custom_eval_config_id IN (" in (
            predicate
        )
    assert filters["tracer_eval_logger_v2"] == filters["tracer_eval_logger"]
    assert (
        intrinsic_ownership(replace(scope, eval_config_ids=()))["tracer_eval_logger"]
        == "0"
    )
    rendered = additional_table_filters(enabled)
    assert rendered.startswith("{'analytics.spans': 'project_id = toUUID(\\'")
    for table in filters:
        assert f"'analytics.{table}': " in rendered
        assert f"'analytics.{table}_local': " in rendered
    # The ClickHouse map literal parses back to exactly these predicates.
    parsed = sqlglot.parse_one(f"SELECT {rendered}", read="clickhouse").expressions[0]
    keys = [key.this for key in parsed.args["keys"].expressions]
    values = [value.this for value in parsed.args["values"].expressions]
    assert dict(zip(keys, values, strict=True)) == {
        f"analytics.{name}": predicate
        for table, predicate in filters.items()
        for name in (table, f"{table}_local")
    }


# Window functions: the reviewed set, inline OVER (...) only, bounded ROWS/RANGE
# frames. The live suite executes these on ClickHouse with expected values.
WINDOW_QUERIES = [
    "SELECT session_id, row_number() OVER (PARTITION BY session_id ORDER BY start_time) AS turn FROM spans",
    "SELECT rank() OVER (ORDER BY cost DESC) AS r, dense_rank() OVER (ORDER BY cost DESC) AS d FROM spans",
    "SELECT ntile(4) OVER (PARTITION BY model ORDER BY latency_ms) AS quartile FROM spans",
    "SELECT percent_rank() OVER (ORDER BY latency_ms) AS pr FROM spans",
    "SELECT cume_dist() OVER (PARTITION BY model ORDER BY latency_ms) AS cd FROM spans",
    "SELECT lag(latency_ms) OVER (PARTITION BY trace_id ORDER BY start_time) AS prev FROM spans",
    "SELECT lead(latency_ms, 2, -1) OVER (PARTITION BY trace_id ORDER BY start_time) AS next2 FROM spans",
    "SELECT first_value(model) OVER (PARTITION BY session_id ORDER BY start_time) AS a, last_value(model) OVER (PARTITION BY session_id ORDER BY start_time ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS b FROM spans",
    "SELECT sum(cost) OVER (ORDER BY start_time ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running FROM spans",
    "SELECT avg(latency_ms) OVER (PARTITION BY model ORDER BY start_time ROWS BETWEEN 2 PRECEDING AND 2 FOLLOWING) AS smooth FROM spans",
    "SELECT count() OVER (PARTITION BY model) AS n, min(cost) OVER (PARTITION BY model) AS lo, max(cost) OVER (PARTITION BY model) AS hi FROM spans",
    "SELECT count() OVER (ORDER BY total_tokens RANGE BETWEEN 100 PRECEDING AND CURRENT ROW) AS near FROM spans",
    "SELECT e.config_id, rank() OVER (PARTITION BY e.config_id ORDER BY e.score DESC) AS r FROM eval_results AS e JOIN sessions AS s ON e.session_id = s.id",
    "WITH spend AS (SELECT user_id, sum(cost) AS c FROM spans GROUP BY user_id) SELECT user_id, dense_rank() OVER (ORDER BY c DESC) AS r FROM spend",
    "SELECT * FROM (SELECT trace_id, row_number() OVER (PARTITION BY trace_id ORDER BY latency_ms DESC) AS r FROM spans WHERE trace_id IN (SELECT toString(trace_id) FROM eval_results)) AS t WHERE r = 1",
    "SELECT model, sum(sum(cost)) OVER (ORDER BY model) AS cumulative FROM spans GROUP BY model",
]


@pytest.mark.parametrize("query", WINDOW_QUERIES)
def test_window_functions_compile_with_every_source_scoped(query, scope):
    compiled = compile_query(query, scope)
    tree = sqlglot.parse_one(compiled, read="clickhouse")
    assert any(tree.find_all(exp.Window))
    physical = [t for t in tree.find_all(exp.Table) if t.db]
    assert physical and all(t.db == "analytics" for t in physical)
    assert compiled.count(f"project_id = toUUID('{scope.project_id}')") >= 1


def test_lag_and_lead_read_the_whole_partition(scope):
    """ClickHouse runs lag/lead as lagInFrame/leadInFrame, which honour the
    frame; with the default frame (ending at the current row) lead would return
    the default value for every row, so the compiler sets the whole partition."""
    compiled = compile_query(
        "SELECT lag(latency_ms) OVER (PARTITION BY trace_id ORDER BY start_time) AS p, "
        "lead(latency_ms, 2, -1) OVER (ORDER BY start_time) AS n FROM spans",
        scope,
    )
    whole = "ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING"
    assert (
        "lagInFrame(toNullable(latency_ms)) OVER "
        f"(PARTITION BY trace_id ORDER BY start_time {whole})"
    ) in compiled
    assert f"leadInFrame(latency_ms, 2, -1) OVER (ORDER BY start_time {whole})" in (
        compiled
    )


def test_cume_dist_is_a_peer_inclusive_count_ratio(scope):
    compiled = compile_query(
        "SELECT cume_dist() OVER (PARTITION BY model ORDER BY latency_ms) AS cd FROM spans",
        scope,
    )
    assert "cume_dist" not in compiled.lower()
    assert (
        "(COUNT(*) OVER (PARTITION BY model ORDER BY latency_ms RANGE BETWEEN "
        "UNBOUNDED PRECEDING AND CURRENT ROW) / COUNT(*) OVER (PARTITION BY model)) AS cd"
    ) in compiled


WINDOW_REJECTIONS = [
    # Outside the reviewed function set.
    "SELECT nth_value(cost, 2) OVER (ORDER BY cost) FROM spans",
    "SELECT uniq(model) OVER () FROM spans",
    "SELECT quantile(0.5)(cost) OVER () FROM spans",
    "SELECT lagInFrame(cost) OVER (ORDER BY cost) FROM spans",
    "SELECT groupArray(cost) OVER () FROM spans",
    "SELECT countIf(cost > 0) OVER () FROM spans",
    "SELECT sum(cost) FILTER (WHERE cost > 0) OVER () FROM spans",
    "SELECT lag(cost) IGNORE NULLS OVER (ORDER BY cost) FROM spans",
    "SELECT count(DISTINCT model) OVER () FROM spans",
    # Named windows and nesting.
    "SELECT row_number() OVER w FROM spans WINDOW w AS (ORDER BY cost)",
    "SELECT sum(cost) OVER (w ORDER BY cost) FROM spans WINDOW w AS (PARTITION BY model)",
    "SELECT sum(row_number() OVER ()) OVER () FROM spans",
    "SELECT row_number() OVER (ORDER BY rank() OVER ()) FROM spans",
    # Ranking/offset functions need OVER and take no frame or column offsets.
    "SELECT ntile(4) FROM spans",
    "SELECT lag(cost) FROM spans",
    "SELECT row_number() FROM spans",
    "SELECT rank() OVER (ORDER BY cost ROWS BETWEEN 1 PRECEDING AND CURRENT ROW) FROM spans",
    "SELECT lead(cost) OVER (ORDER BY cost ROWS BETWEEN CURRENT ROW AND 1 FOLLOWING) FROM spans",
    "SELECT ntile(0) OVER (ORDER BY cost) FROM spans",
    "SELECT ntile(total_tokens) OVER (ORDER BY cost) FROM spans",
    "SELECT lag(cost, latency_ms) OVER (ORDER BY cost) FROM spans",
    "SELECT lag(cost, 2000000) OVER (ORDER BY cost) FROM spans",
    "SELECT percent_rank(cost) OVER (ORDER BY cost) FROM spans",
    # Frames: bounded whole-number offsets only, no EXCLUDE / GROUPS / intervals.
    "SELECT sum(cost) OVER (ORDER BY cost ROWS BETWEEN latency_ms PRECEDING AND CURRENT ROW) FROM spans",
    "SELECT sum(cost) OVER (ORDER BY cost ROWS BETWEEN 2000000 PRECEDING AND CURRENT ROW) FROM spans",
    "SELECT sum(cost) OVER (ORDER BY cost ROWS BETWEEN 1.5 PRECEDING AND CURRENT ROW) FROM spans",
    "SELECT sum(cost) OVER (ORDER BY start_time RANGE BETWEEN INTERVAL 1 DAY PRECEDING AND CURRENT ROW) FROM spans",
    "SELECT sum(cost) OVER (ORDER BY cost ROWS BETWEEN (SELECT 1) PRECEDING AND CURRENT ROW) FROM spans",
    "SELECT sum(cost) OVER (ORDER BY cost ROWS BETWEEN CURRENT ROW AND 3 FOLLOWING EXCLUDE CURRENT ROW) FROM spans",
    "SELECT sum(cost) OVER (ORDER BY cost GROUPS BETWEEN 1 PRECEDING AND CURRENT ROW) FROM spans",
    # Tables inside a window still follow every other rule.
    "SELECT sum(cost) OVER (PARTITION BY id IN spans) FROM spans",
    "SELECT row_number() OVER (ORDER BY (SELECT name FROM system.tables LIMIT 1)) FROM spans",
    "SELECT sum(cost) OVER (PARTITION BY dictGet('d', 'a', id)) FROM spans",
    "SELECT * FROM (SELECT row_number() OVER (ORDER BY id) AS r FROM system.tables) AS t",
]


@pytest.mark.parametrize("query", WINDOW_REJECTIONS)
def test_windows_outside_the_reviewed_set_are_rejected(query, scope):
    with pytest.raises(UnsafeQuery):
        compile_query(query, scope)


# ---------------------------------------------------------------------------
# Example gallery: every vetted example is ordinary user SQL that compiles
# with each physical source scoped (live values: test_analytics_clickhouse).

GALLERY_TOPICS = {
    "cost-tokens-by-model",
    "latency-percentiles-by-day",
    "error-rate-by-day",
    "slowest-traces",
    "sessions-per-user",
    "eval-score-by-evaluator-and-locale",
    "annotation-agreement",
    "derived-metric-over-time",
    "lowest-scoring-sessions",
    "costly-users-ranked",
    "sampled-vs-all-by-model",
    "retention-cohorts",
}


def _gallery_scope():
    end = datetime(2026, 10, 21, tzinfo=UTC)
    return Scope(
        uuid4(),
        uuid4(),
        uuid4(),
        end - timedelta(days=7),
        end,
        (uuid4(), uuid4()),
        "tracer_eval_logger_v2",
        "analytics",
        True,
    )


def test_the_gallery_covers_the_documented_analyses():
    from tracer.sql_workbench.compiler import validate
    from tracer.sql_workbench.examples import examples

    ids = [example.id for example in EXAMPLES]
    assert len(ids) == len(set(ids)) >= 12
    assert GALLERY_TOPICS <= set(ids)
    for example, wire in zip(EXAMPLES, examples(), strict=True):
        assert wire["sql"] == example.sql and wire["id"] == example.id
        assert wire["datasets"] == sorted(
            {table.name for table in validate(example.sql)[1]}
        )


@pytest.mark.parametrize(
    "example",
    EXAMPLES,
    ids=lambda example: example.id,
)
def test_every_gallery_example_compiles_fully_scoped(example):
    scope = _gallery_scope()
    compiled = compile_query(example.sql, scope)
    tree = sqlglot.parse_one(compiled, read="clickhouse")
    assert tree.args["limit"].expression.this == str(MAX_RESULT_ROWS + 1)
    physical = [t for t in tree.find_all(exp.Table) if t.db]
    assert physical and all(t.db == "analytics" for t in physical)
    # No catalog name survives as a table: each became a scoped subquery.
    assert not any(t.name in COLUMNS for t in tree.find_all(exp.Table) if not t.db)
    assert f"toUUID('{scope.project_id}')" in compiled


# ---------------------------------------------------------------------------
# Analytical function set. One probe per approved user-facing name: each must
# compile fully scoped here and is executed on live ClickHouse, with its value
# checked, by test_analytics_clickhouse. A name added to FUNCTIONS without a
# probe fails this file, so no approved function can be dead in practice.

FUNCTION_PROBES = {
    # Aggregates and combinators
    "count": "SELECT count() FROM spans",
    "countif": "SELECT countIf(status = 'ERROR') FROM spans",
    "sum": "SELECT sum(total_tokens) FROM spans",
    "sumif": "SELECT sumIf(total_tokens, status = 'OK') FROM spans",
    "avg": "SELECT avg(latency_ms) FROM spans",
    "avgif": "SELECT avgIf(latency_ms, status = 'OK') FROM spans",
    "min": "SELECT min(latency_ms) FROM spans",
    "minif": "SELECT minIf(latency_ms, status = 'ERROR') FROM spans",
    "max": "SELECT max(latency_ms) FROM spans",
    "maxif": "SELECT maxIf(latency_ms, status = 'ERROR') FROM spans",
    "any": "SELECT any(provider) FROM spans WHERE model = 'm-a'",
    "anylast": "SELECT anyLast(provider) FROM spans WHERE model = 'm-c'",
    "argmin": "SELECT argMin(model, latency_ms) FROM spans",
    "argmax": "SELECT argMax(model, latency_ms) FROM spans",
    "uniq": "SELECT uniq(model) FROM spans",
    "uniqif": "SELECT uniqIf(model, status = 'ERROR') FROM spans",
    "uniqexact": "SELECT uniqExact(trace_id) FROM spans",
    "uniqexactif": "SELECT uniqExactIf(trace_id, status = 'ERROR') FROM spans",
    "uniqcombined": "SELECT uniqCombined(session_id) FROM spans",
    "median": "SELECT median(latency_ms) FROM spans",
    "quantile": "SELECT quantile(0.9)(latency_ms) FROM spans",
    "quantiles": "SELECT quantiles(0.5, 0.9)(latency_ms) FROM spans",
    "quantileexact": "SELECT quantileExact(0.5)(latency_ms) FROM spans",
    "quantiletdigest": "SELECT quantileTDigest(0.5)(latency_ms) FROM spans",
    "stddevpop": "SELECT stddevPop(latency_ms) FROM spans",
    "stddevsamp": "SELECT stddevSamp(latency_ms) FROM spans",
    "varpop": "SELECT varPop(latency_ms) FROM spans",
    "varsamp": "SELECT varSamp(latency_ms) FROM spans",
    "corr": "SELECT corr(latency_ms, total_tokens) FROM spans",
    "avgweighted": "SELECT avgWeighted(latency_ms, total_tokens) FROM spans",
    "grouparray": (
        "SELECT groupArray(3)(model) FROM "
        "(SELECT model FROM spans ORDER BY latency_ms DESC LIMIT 3)"
    ),
    "groupuniqarray": "SELECT groupUniqArray(10)(provider) FROM spans",
    "topk": "SELECT topK(1)(model) FROM spans",
    # Math
    "abs": "SELECT abs(min(latency_ms) - max(latency_ms)) FROM spans",
    "round": "SELECT round(sum(cost), 2) FROM spans",
    "floor": "SELECT floor(avg(latency_ms)) FROM spans",
    "ceil": "SELECT ceil(avg(latency_ms)) FROM spans",
    "ceiling": "SELECT ceiling(avg(latency_ms)) FROM spans",
    "sqrt": "SELECT sqrt(max(latency_ms)) FROM spans",
    "pow": "SELECT pow(max(latency_ms), 2) FROM spans",
    "power": "SELECT power(min(latency_ms), 2) FROM spans",
    "log": "SELECT log(max(latency_ms)) FROM spans",
    "ln": "SELECT ln(min(latency_ms)) FROM spans",
    "exp": "SELECT exp(min(total_tokens) / 100) FROM spans",
    "greatest": "SELECT greatest(max(latency_ms), 2000) FROM spans",
    "least": "SELECT least(max(latency_ms), 2000) FROM spans",
    "intdiv": "SELECT intDiv(max(latency_ms), 7) FROM spans",
    "modulo": "SELECT modulo(max(latency_ms), 7) FROM spans",
    # Conditionals
    "coalesce": "SELECT countIf(coalesce(label, 'none') = 'none') FROM eval_results",
    "ifnull": "SELECT countIf(ifNull(label, 'none') = 'none') FROM eval_results",
    "nullif": "SELECT countIf(nullIf(status, 'OK') IS NULL) FROM spans",
    "if": "SELECT sum(if(status = 'ERROR', 1, 0)) FROM spans",
    "multiif": (
        "SELECT countIf(multiIf(latency_ms < 150, 'fast', latency_ms < 500, 'ok', "
        "'slow') = 'slow') FROM spans"
    ),
    # Strings
    "lower": "SELECT countIf(lower(status) = 'error') FROM spans",
    "upper": "SELECT upper(min(model)) FROM spans",
    "length": "SELECT max(length(name)) FROM spans",
    "lengthutf8": "SELECT max(lengthUTF8(provider)) FROM spans",
    "substring": "SELECT substring(min(model), 3, 1) FROM spans",
    "concat": "SELECT concat(min(model), '/', min(provider)) FROM spans",
    "trim": "SELECT max(trim(concat(' ', model, ' '))) FROM spans",
    "ltrim": "SELECT max(ltrim(concat(' ', model))) FROM spans",
    "rtrim": "SELECT max(rtrim(concat(model, ' '))) FROM spans",
    "replace": "SELECT max(replace(model, 'm-', 'model-')) FROM spans",
    "replaceall": "SELECT max(replaceAll(model, '-', '_')) FROM spans",
    "position": "SELECT max(position(provider, 'o')) FROM spans",
    "positioncaseinsensitive": (
        "SELECT max(positionCaseInsensitive(provider, 'O')) FROM spans"
    ),
    "startswith": "SELECT countIf(startsWith(provider, 'open')) FROM spans",
    "endswith": "SELECT countIf(endsWith(provider, 'ic')) FROM spans",
    "match": "SELECT countIf(match(model, '^m-[bc]$')) FROM spans",
    "splitbychar": "SELECT max(splitByChar('-', model)[2]) FROM spans",
    "splitbystring": "SELECT max(splitByString('m-', model)[2]) FROM spans",
    "arrayelement": "SELECT max(arrayElement(splitByChar('-', model), 1)) FROM spans",
    # Maps
    "has": "SELECT countIf(has(mapKeys(attrs_string), 'tool_args')) FROM spans",
    "mapkeys": "SELECT max(length(mapKeys(attrs_string))) FROM spans",
    "mapvalues": "SELECT countIf(has(mapValues(attrs_string), 'de')) FROM spans",
    "mapcontains": "SELECT countIf(mapContains(attrs_number, 'ttft_ms')) FROM spans",
    # JSON in span attributes
    "jsonextract": (
        "SELECT sum(JSONExtract(attrs_string['tool_args'], 'k', 'Nullable(Int64)')) "
        "FROM spans"
    ),
    "jsonextractstring": (
        "SELECT countIf(JSONExtractString(attrs_string['tool_args'], 'tool') = "
        "'search') FROM spans"
    ),
    "jsonextractint": (
        "SELECT sum(JSONExtractInt(attrs_string['tool_args'], 'k')) FROM spans"
    ),
    "jsonextractuint": (
        "SELECT sum(JSONExtractUInt(attrs_string['tool_args'], 'k')) FROM spans"
    ),
    "jsonextractfloat": (
        "SELECT sum(JSONExtractFloat(attrs_string['tool_args'], 'k')) FROM spans"
    ),
    "jsonextractbool": (
        "SELECT sum(JSONExtractBool(attrs_string['tool_args'], 'tool')) FROM spans"
    ),
    "jsonextractraw": (
        "SELECT max(JSONExtractRaw(attrs_string['tool_args'], 'tool')) FROM spans"
    ),
    "jsonextractkeys": (
        "SELECT max(length(JSONExtractKeys(attrs_string['tool_args']))) FROM spans"
    ),
    "jsonhas": "SELECT countIf(JSONHas(attrs_string['tool_args'], 'k')) FROM spans",
    "jsonlength": "SELECT max(JSONLength(attrs_string['tool_args'])) FROM spans",
    "jsontype": (
        "SELECT countIf(JSONType(attrs_string['tool_args'], 'k') = 'Int64') FROM spans"
    ),
    # Conversions
    "tostring": "SELECT max(toString(latency_ms)) FROM spans",
    "touuid": "SELECT countIf(toString(toUUID(trace_id)) = trace_id) FROM spans",
    "touuidornull": "SELECT countIf(toUUIDOrNull(id) IS NULL) FROM spans",
    "toint64": "SELECT sum(toInt64(latency_ms)) FROM spans",
    "tofloat64": "SELECT sum(toFloat64(total_tokens)) FROM spans",
    "todate": "SELECT uniqExact(toDate(start_time)) FROM spans",
    "todatetime": "SELECT min(toDateTime(start_time)) FROM spans",
    "todatetime64": "SELECT min(toDateTime64(start_time, 3)) FROM spans",
    # Time buckets and parts
    "tostartofminute": "SELECT uniqExact(toStartOfMinute(start_time)) FROM spans",
    "tostartoffiveminutes": (
        "SELECT uniqExact(toStartOfFiveMinutes(start_time)) FROM spans"
    ),
    "tostartoffifteenminutes": (
        "SELECT uniqExact(toStartOfFifteenMinutes(start_time)) FROM spans"
    ),
    "tostartofinterval": (
        "SELECT uniqExact(toStartOfInterval(start_time, INTERVAL 30 MINUTE)) FROM spans"
    ),
    "tostartofhour": "SELECT uniqExact(toStartOfHour(start_time)) FROM spans",
    "tostartofday": "SELECT uniqExact(toStartOfDay(start_time)) FROM spans",
    "tostartofweek": "SELECT uniqExact(toStartOfWeek(start_time)) FROM spans",
    "tostartofmonth": "SELECT uniqExact(toStartOfMonth(start_time)) FROM spans",
    "toyear": "SELECT min(toYear(start_time)) FROM spans",
    "tomonth": "SELECT min(toMonth(start_time)) FROM spans",
    "todayofmonth": "SELECT min(toDayOfMonth(start_time)) FROM spans",
    "todayofweek": "SELECT min(toDayOfWeek(start_time)) FROM spans",
    "tohour": "SELECT min(toHour(start_time)) FROM spans",
    "tominute": "SELECT max(toMinute(start_time)) FROM spans",
    "formatdatetime": (
        "SELECT min(formatDateTime(start_time, '%Y-%m-%d %H:%i')) FROM spans"
    ),
    "datediff": (
        "SELECT max(dateDiff('millisecond', start_time, end_time)) FROM spans"
    ),
    "dateadd": (
        "SELECT countIf(dateAdd(MILLISECOND, latency_ms, start_time) = end_time) "
        "FROM spans"
    ),
    "now": "SELECT countIf(start_time < now()) FROM spans",
    # Window functions
    "rownumber": (
        "SELECT max(r) FROM (SELECT row_number() OVER (ORDER BY latency_ms) AS r "
        "FROM spans)"
    ),
    "rank": (
        "SELECT max(r) FROM (SELECT rank() OVER (ORDER BY latency_ms) AS r FROM spans)"
    ),
    "denserank": (
        "SELECT max(r) FROM (SELECT dense_rank() OVER (ORDER BY latency_ms) AS r "
        "FROM spans)"
    ),
    "lag": (
        "SELECT countIf(p IS NULL) FROM (SELECT lag(latency_ms) OVER "
        "(ORDER BY start_time) AS p FROM spans)"
    ),
    "lead": (
        "SELECT countIf(n IS NULL) FROM (SELECT lead(latency_ms) OVER "
        "(ORDER BY start_time) AS n FROM spans)"
    ),
    "firstvalue": (
        "SELECT max(f) FROM (SELECT first_value(latency_ms) OVER "
        "(ORDER BY start_time) AS f FROM spans)"
    ),
    "lastvalue": (
        "SELECT max(v) FROM (SELECT last_value(latency_ms) OVER (ORDER BY "
        "start_time ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS v "
        "FROM spans)"
    ),
}


def test_every_approved_function_has_a_probe():
    from tracer.sql_workbench.compiler import FUNCTIONS

    assert set(FUNCTION_PROBES) == set(FUNCTIONS)


@pytest.mark.parametrize("name", sorted(FUNCTION_PROBES))
def test_every_approved_function_compiles_fully_scoped(name):
    scope = _gallery_scope()
    compiled = compile_query(FUNCTION_PROBES[name], scope)
    tree = sqlglot.parse_one(compiled, read="clickhouse")
    physical = [t for t in tree.find_all(exp.Table) if t.db]
    assert physical and all(t.db == "analytics" for t in physical)
    assert f"toUUID('{scope.project_id}')" in compiled


@pytest.mark.parametrize(
    "query",
    [
        # Unbounded or oversized collections, non-literal parameters.
        "SELECT groupArray(model) FROM spans",
        "SELECT groupUniqArray(model) FROM spans",
        "SELECT groupArray(1001)(model) FROM spans",
        "SELECT topK(5000)(model) FROM spans",
        "SELECT topK(latency_ms)(model) FROM spans",
        "SELECT groupArray(0)(model) FROM spans",
        "SELECT quantiles(2)(latency_ms) FROM spans",
        "SELECT quantile(latency_ms)(cost) FROM spans",
        "SELECT quantile(-0.1)(latency_ms) FROM spans",
        "SELECT quantileExact('0.5')(latency_ms) FROM spans",
        "SELECT quantiles(" + ", ".join(["0.5"] * 33) + ")(latency_ms) FROM spans",
        # Unreviewed combinators and parametric forms.
        "SELECT groupArrayIf(model, status = 'OK') FROM spans",
        "SELECT quantileTiming(0.9)(latency_ms) FROM spans",
        "SELECT quantileIf(0.9)(latency_ms, status = 'OK') FROM spans",
        # Side effects, server introspection, dictionaries, tables and files.
        "SELECT sleep(3) FROM spans",
        "SELECT sleepEachRow(1) FROM spans",
        "SELECT throwIf(1) FROM spans",
        "SELECT hostName() FROM spans",
        "SELECT version() FROM spans",
        "SELECT uptime() FROM spans",
        "SELECT currentUser() FROM spans",
        "SELECT currentDatabase() FROM spans",
        "SELECT getSetting('readonly') FROM spans",
        "SELECT getMacro('replica') FROM spans",
        "SELECT getServerPort('tcp_port') FROM spans",
        "SELECT serverUUID() FROM spans",
        "SELECT queryID() FROM spans",
        "SELECT transactionID() FROM spans",
        "SELECT filesystemAvailable() FROM spans",
        "SELECT globalVariable('x') FROM spans",
        "SELECT initializeAggregation('sum', 1) FROM spans",
        "SELECT runningAccumulate(sum(cost)) FROM spans",
        "SELECT dictGet('d', 'a', toUInt64(1)) FROM spans",
        "SELECT joinGet('t', 'c', 1) FROM spans",
        "SELECT hasColumnInTable('system', 'users', 'name') FROM spans",
        "SELECT file('/etc/passwd') FROM spans",
        "SELECT url('http://example.invalid') FROM spans",
        "SELECT arrayJoin([1, 2]) FROM spans",
        "SELECT repeat(model, 1000000) FROM spans",
        "SELECT range(1000000) FROM spans",
        "SELECT replaceRegexpAll(model, 'a', 'b') FROM spans",
        "SELECT generateUUIDv4() FROM spans",
        "SELECT JSONExtractString(attrs_string['tool_args'], '*') FROM system.users",
    ],
)
def test_functions_outside_the_analytical_set_are_rejected(query):
    with pytest.raises(UnsafeQuery):
        compile_query(query, _gallery_scope())


@pytest.mark.parametrize(
    "query",
    [
        "SELECT groupArray(1000)(model) FROM spans",
        "SELECT groupUniqArray(1)(model) FROM spans",
        "SELECT topK(1000)(model) FROM spans",
        "SELECT topK(model) FROM spans",
        "SELECT quantiles(" + ", ".join(["0.5"] * 32) + ")(latency_ms) FROM spans",
        "SELECT quantile(0)(latency_ms), quantile(1)(latency_ms) FROM spans",
    ],
)
def test_parametric_aggregates_accept_their_bounds(query):
    compile_query(query, _gallery_scope())
