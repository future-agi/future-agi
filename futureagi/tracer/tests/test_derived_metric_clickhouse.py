"""Executable SQL fixtures on a guarded sidecar or an explicitly opted-in chDB.

DERIVED_METRIC_TEST_CHDB=1 runs the same plans in embedded ClickHouse (no Docker).
The default native path uses the repository's test-sidecar mutation guard.
"""

import json
import os
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tracer.services.clickhouse.v2.apply_schema_rewriter import split_statements
from tracer.services.derived_metrics.formula import compile_formula
from tracer.services.derived_metrics.queries import (
    QUERY_SETTINGS,
    Scope,
    aggregate_select,
    snapshot_select,
)

SCHEMA = Path(__file__).parents[1] / "services" / "clickhouse" / "v2" / "schema"


class EmbeddedClient:
    server_enforced_readonly = False

    def __init__(self, session):
        self.session = session

    def execute(self, query, params=None, with_column_types=False, settings=None):
        from clickhouse_driver.util.escape import escape_params

        context = SimpleNamespace(
            server_info=SimpleNamespace(get_timezone=lambda: "UTC")
        )
        if params:
            query = query % escape_params(params, context)
        for name, value in (settings or {}).items():
            self.session.query(f"SET {name} = '{value}'")
        result = str(self.session.query(query, "JSON"))
        if not result.strip():
            return ([], []) if with_column_types else []
        parsed = json.loads(result)
        columns = [(column["name"], column["type"]) for column in parsed["meta"]]

        def native_value(value, kind):
            base = kind.removeprefix("Nullable(").removesuffix(")")
            return (
                int(value)
                if value is not None and base.startswith(("UInt", "Int"))
                else value
            )

        rows = [
            tuple(native_value(row[name], kind) for name, kind in columns)
            for row in parsed["data"]
        ]
        return (rows, columns) if with_column_types else rows


@pytest.fixture
def metric_ch(tmp_path, settings):
    settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger_v2"
    if os.getenv("DERIVED_METRIC_TEST_CHDB") == "1":
        # Explicit opt-in: a missing package is an error, not a skipped proof.
        from chdb.session import Session

        session = Session(str(tmp_path / "clickhouse"))
        client = EmbeddedClient(session)
        try:
            yield from _schema_client(client)
        finally:
            session.close()
    else:
        from conftest import _ch_test_native_client, _ch_test_owned_database

        with (
            _ch_test_owned_database("test_derived_metrics_") as database,
            _ch_test_native_client(database=database) as client,
        ):
            client.server_enforced_readonly = False
            yield from _schema_client(client)


def _schema_client(client):
    # Minimal physical source fixture: column types mirror spans and eval logger
    # v2. Deployment schema application is covered separately, not implied here.
    client.execute("""CREATE TABLE spans (
        id String, project_id UUID, org_id Nullable(UUID), trace_id String DEFAULT '',
        trace_session_id Nullable(UUID), end_user_id Nullable(UUID),
        start_time DateTime64(6,'UTC'), end_time Nullable(DateTime64(6,'UTC')),
        created_at DateTime64(6,'UTC'), updated_at DateTime64(6,'UTC'),
        _version UInt64 DEFAULT 1, is_deleted UInt8 DEFAULT 0,
        model String DEFAULT '', provider String DEFAULT '', status String DEFAULT '',
        name String DEFAULT '', service_name String DEFAULT '',
        latency_ms Int32 DEFAULT 0, cost Float64 DEFAULT 0,
        total_tokens Int32 DEFAULT 0, prompt_tokens Int32 DEFAULT 0, completion_tokens Int32 DEFAULT 0,
        attrs_number Map(String,Float64), attrs_bool Map(String,UInt8), attrs_string Map(String,String)
    ) ENGINE=ReplacingMergeTree(_version) ORDER BY (project_id,start_time,trace_id,id)""")
    client.execute("""CREATE TABLE tracer_eval_logger_v2 (
        id UUID, custom_eval_config_id UUID, config_hash Nullable(String),
        trace_id Nullable(UUID), observation_span_id Nullable(String), trace_session_id Nullable(UUID), target_type String,
        created_at DateTime64(6,'UTC'), updated_at DateTime64(6,'UTC'), _version UInt64,
        is_deleted UInt8 DEFAULT 0, error UInt8 DEFAULT 0, status String DEFAULT 'completed',
        output_float Nullable(Float64), output_bool Nullable(UInt8), output_str Nullable(String),
        output_metadata String DEFAULT '{}', eval_task_id Nullable(String)
    ) ENGINE=ReplacingMergeTree(_version,is_deleted) ORDER BY (custom_eval_config_id,created_at,id)""")
    for filename in ["019_id_remap.sql", "028_derived_metrics.sql"]:
        for statement in split_statements((SCHEMA / filename).read_text()):
            client.execute(statement)
    yield client


def seed_span(
    client,
    scope,
    span_id,
    session_id,
    user_id,
    when,
    resolved=1,
    escalated=0,
    cohort="west",
    trace_id=None,
):
    client.execute(
        """INSERT INTO spans (id, project_id, org_id, trace_id, trace_session_id, end_user_id,
        start_time, created_at, updated_at, attrs_number, attrs_string)
        VALUES (%(span)s, %(project)s, %(org)s, %(trace)s, %(session)s, %(user)s, %(at)s, %(at)s, %(at)s,
        map('resolved', %(resolved)s, 'escalated', %(escalated)s), if(%(cohort_present)s, map('region', %(cohort)s), map()))""",
        {
            "span": span_id,
            "project": str(scope.project),
            "org": str(scope.organization),
            "trace": str(trace_id or uuid4()),
            "session": str(session_id),
            "user": str(user_id),
            "at": when.strftime("%Y-%m-%d %H:%M:%S.%f"),
            "cohort_present": cohort is not None,
            "cohort": cohort if cohort is not None else "",
            "resolved": resolved,
            "escalated": escalated,
        },
    )


def seed_eval(
    client,
    config_id,
    target,
    when,
    score,
    probability,
    *,
    span_id=None,
    row_id=None,
    version=1,
    deleted=0,
    config_hash="a" * 64,
    sampling_unit_id=None,
    cohort_id="11111111-1111-1111-1111-111111111111",
    status="completed",
    error=0,
    selection_overrides=None,
    trace_id=None,
):
    day = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    sampling = {
        "inclusion_probability": probability,
        "sampling_unit": "session",
        "sampling_unit_id": "session:" + str(sampling_unit_id or target),
        "cohort_id": cohort_id,
        "policy_version": "c" * 64,
        "stratum": "region-west",
        "membership_mode": "live",
        "cohort_frozen_at": None,
        "population_size": None,
        "window": {
            "start": (day - timedelta(days=2)).isoformat(),
            "end": (day + timedelta(days=1)).isoformat(),
        },
        **(selection_overrides or {}),
    }
    client.execute(
        """INSERT INTO tracer_eval_logger_v2 (id, custom_eval_config_id, config_hash,
        trace_id, observation_span_id, trace_session_id, target_type, created_at, updated_at, _version,
        is_deleted, output_float, output_metadata, status, error)
        VALUES (%(id)s, %(config)s, %(hash)s, %(trace)s, %(span)s, %(session)s, %(target_type)s,
        %(at)s, %(at)s, %(version)s, %(deleted)s, %(score)s, %(metadata)s, %(status)s, %(error)s)""",
        {
            "id": str(row_id or uuid4()),
            "config": str(config_id),
            "hash": config_hash,
            "trace": str(trace_id) if trace_id else None,
            "span": span_id,
            "session": str(target) if span_id is None else None,
            "target_type": "session" if span_id is None else "span",
            "at": when.strftime("%Y-%m-%d %H:%M:%S.%f"),
            "version": version,
            "deleted": deleted,
            "score": score,
            "status": status,
            "error": error,
            "metadata": json.dumps({"_task_selection": {"sampling": sampling}}),
        },
    )


def recipe(configs=None, weighting="none"):
    inputs = [
        {
            "alias": alias,
            "source": "eval",
            "config_id": str(configs[index]),
            "config_hash": "a" * 64,
            "reducer": "latest",
        }
        if configs
        else {
            "alias": alias,
            "source": "span",
            "field": "attribute_number",
            "attribute_key": alias,
            "reducer": "latest",
        }
        for index, alias in enumerate(["resolved", "escalated"])
    ]
    return SimpleNamespace(
        formula="ratio(resolved == 1 and escalated == 0, resolved >= 0)",
        inputs=inputs,
        selection_unit="session",
        weighting=weighting,
        cohort_key="region",
        filters=[],
    )


def execute_formula(
    client,
    definition,
    scope,
    now,
    rollup="overall",
    *,
    window_start=None,
    window_end=None,
):
    query, params = snapshot_select(
        definition,
        scope,
        window_start or now - timedelta(days=1),
        window_end or now,
        now,
        [],
        "",
    )
    formula = compile_formula(
        definition.formula, [item["alias"] for item in definition.inputs]
    )
    rows, columns = client.execute(
        aggregate_select(query, formula, rollup=rollup),
        {**params, **formula.parameters},
        with_column_types=True,
        settings=QUERY_SETTINGS,
    )
    return [
        dict(zip([column[0] for column in columns], row, strict=True)) for row in rows
    ]


def test_session_evals_do_not_fan_out_and_weights_apply_once(metric_ch):
    scope, now = (
        Scope(uuid4(), uuid4(), uuid4()),
        datetime.now(UTC).replace(microsecond=0),
    )
    sessions, user, configs = [uuid4(), uuid4()], uuid4(), [uuid4(), uuid4()]
    for index in range(4):
        seed_span(
            metric_ch,
            scope,
            f"first-{index}",
            sessions[0],
            user,
            now - timedelta(hours=1),
        )
    seed_span(metric_ch, scope, "second", sessions[1], user, now - timedelta(hours=1))
    # Session A: resolved, weight 2. Session B: escalated, weight 4.
    for session, probability, escalated in [
        (sessions[0], 0.5, 0),
        (sessions[1], 0.25, 1),
    ]:
        seed_eval(
            metric_ch, configs[0], session, now - timedelta(minutes=10), 1, probability
        )
        seed_eval(
            metric_ch,
            configs[1],
            session,
            now - timedelta(minutes=10),
            escalated,
            probability,
        )
    rows = execute_formula(
        metric_ch, recipe(configs, "inverse_probability"), scope, now
    )
    assert rows[0]["status"] == "ok"
    assert float(rows[0]["value"]) == pytest.approx(1 / 3)
    assert float(rows[0]["numerator"]) == 2
    assert float(rows[0]["denominator"]) == 6
    assert int(rows[0]["unit_count"]) == int(rows[0]["valid_count"]) == 2
    user_rows = execute_formula(
        metric_ch, recipe(configs, "inverse_probability"), scope, now, "user"
    )
    assert len(user_rows) == 1 and float(user_rows[0]["value"]) == pytest.approx(1 / 3)


@pytest.mark.parametrize("probability", [0, -1, 2, None, "not-a-number"])
def test_invalid_probability_is_not_a_zero_or_unweighted_fallback(
    metric_ch, probability
):
    scope, now, session = (
        Scope(uuid4(), uuid4(), uuid4()),
        datetime.now(UTC).replace(microsecond=0),
        uuid4(),
    )
    configs = [uuid4(), uuid4()]
    seed_span(metric_ch, scope, "one", session, uuid4(), now - timedelta(hours=1))
    for index, config in enumerate(configs):
        seed_eval(
            metric_ch,
            config,
            session,
            now - timedelta(minutes=10),
            1 - index,
            probability,
        )
    row = execute_formula(
        metric_ch, recipe(configs, "inverse_probability"), scope, now
    )[0]
    assert row["status"] == "invalid_probability"
    assert row["value"] is None
    assert int(row["invalid_probability_count"]) == 1


def test_empty_missing_zero_and_zero_denominator_are_distinct(metric_ch):
    scope, now = (
        Scope(uuid4(), uuid4(), uuid4()),
        datetime.now(UTC).replace(microsecond=0),
    )
    definition = recipe()
    empty = execute_formula(metric_ch, definition, scope, now)[0]
    assert empty["status"] == "no_data" and empty["value"] is None
    seed_span(
        metric_ch, scope, "one", uuid4(), uuid4(), now - timedelta(hours=1), escalated=1
    )
    zero = execute_formula(metric_ch, definition, scope, now)[0]
    assert zero["status"] == "ok" and float(zero["value"]) == 0
    definition.formula = "ratio(resolved, escalated == 0)"
    denominator = execute_formula(metric_ch, definition, scope, now)[0]
    assert denominator["status"] == "zero_denominator" and denominator["value"] is None
    definition.inputs[0]["attribute_key"] = "absent"
    missing = execute_formula(metric_ch, definition, scope, now)[0]
    assert missing["status"] == "missing_inputs" and missing["value"] is None


def test_scope_and_canonical_session_remaps(metric_ch):
    scope, other, now = (
        Scope(uuid4(), uuid4(), uuid4()),
        Scope(uuid4(), uuid4(), uuid4()),
        datetime.now(UTC).replace(microsecond=0),
    )
    old, new, user = uuid4(), uuid4(), uuid4()
    metric_ch.execute(
        "INSERT INTO trace_session_id_remap (old_id,new_id) VALUES (%(old)s,%(new)s)",
        {"old": str(old), "new": str(new)},
    )
    seed_span(metric_ch, scope, "old", old, user, now - timedelta(hours=2))
    seed_span(metric_ch, scope, "new", new, user, now - timedelta(hours=1))
    seed_span(
        metric_ch,
        other,
        "foreign",
        uuid4(),
        user,
        now - timedelta(hours=1),
        escalated=1,
    )
    rows = execute_formula(metric_ch, recipe(), scope, now, "session")
    assert len(rows) == 1
    assert rows[0]["group_key"] == str(old)
    assert int(rows[0]["unit_count"]) == 1
    assert float(rows[0]["value"]) == 1


def test_latest_tombstone_and_wrong_evaluator_version_cannot_resurrect(metric_ch):
    scope, now, session = (
        Scope(uuid4(), uuid4(), uuid4()),
        datetime.now(UTC).replace(microsecond=0),
        uuid4(),
    )
    configs, row_id = [uuid4(), uuid4()], uuid4()
    seed_span(metric_ch, scope, "one", session, uuid4(), now - timedelta(hours=1))
    when = now - timedelta(minutes=10)
    seed_eval(metric_ch, configs[0], session, when, 1, 1, row_id=row_id)
    seed_eval(
        metric_ch, configs[0], session, when, 1, 1, row_id=row_id, version=2, deleted=1
    )
    seed_eval(metric_ch, configs[0], session, when, 1, 1, config_hash="b" * 64)
    seed_eval(metric_ch, configs[1], session, when, 0, 1)
    row = execute_formula(metric_ch, recipe(configs), scope, now)[0]
    assert row["status"] == "missing_inputs" and row["value"] is None


def test_session_eval_crosses_old_new_identity_without_fanout(metric_ch):
    scope, now = (
        Scope(uuid4(), uuid4(), uuid4()),
        datetime.now(UTC).replace(microsecond=0),
    )
    old, new, user = uuid4(), uuid4(), uuid4()
    configs = [uuid4(), uuid4()]
    metric_ch.execute(
        "INSERT INTO trace_session_id_remap (old_id,new_id) VALUES (%(old)s,%(new)s)",
        {"old": str(old), "new": str(new)},
    )
    for i in range(3):
        seed_span(metric_ch, scope, f"old-{i}", old, user, now - timedelta(hours=1))
    seed_eval(
        metric_ch,
        configs[0],
        new,
        now - timedelta(minutes=10),
        1,
        0.25,
        sampling_unit_id=old,
    )
    seed_eval(
        metric_ch,
        configs[1],
        new,
        now - timedelta(minutes=10),
        0,
        0.25,
        sampling_unit_id=old,
    )
    row = execute_formula(
        metric_ch, recipe(configs, "inverse_probability"), scope, now
    )[0]
    assert row["status"] == "ok" and float(row["value"]) == 1
    assert float(row["numerator"]) == 4 and float(row["denominator"]) == 4
    assert int(row["unit_count"]) == 1


def test_session_eval_under_any_consolidated_alias_reaches_its_one_unit(metric_ch):
    """Spans carry one old id and the new id; the evaluation carries ANOTHER old id."""
    scope, now = (
        Scope(uuid4(), uuid4(), uuid4()),
        datetime.now(UTC).replace(microsecond=0),
    )
    old_a, old_b, new, user = uuid4(), uuid4(), uuid4(), uuid4()
    configs = [uuid4(), uuid4()]
    for old in (old_a, old_b):
        metric_ch.execute(
            "INSERT INTO trace_session_id_remap (old_id,new_id) VALUES (%(old)s,%(new)s)",
            {"old": str(old), "new": str(new)},
        )
    survivor = min(old_a, old_b, key=str)
    seed_span(metric_ch, scope, "pre-flip", old_a, user, now - timedelta(hours=2))
    seed_span(metric_ch, scope, "post-flip", new, user, now - timedelta(hours=1))
    for config, score in zip(configs, [1, 0], strict=True):
        seed_eval(
            metric_ch,
            config,
            old_b,
            now - timedelta(minutes=10),
            score,
            0.25,
            sampling_unit_id=survivor,
        )
    rows = execute_formula(
        metric_ch, recipe(configs, "inverse_probability"), scope, now, "session"
    )
    assert [row["group_key"] for row in rows] == [str(survivor)]
    assert rows[0]["status"] == "ok" and float(rows[0]["value"]) == 1
    # One unit, weighted once (1 / 0.25), although three ids name the session.
    assert float(rows[0]["numerator"]) == 4 and float(rows[0]["denominator"]) == 4
    assert int(rows[0]["unit_count"]) == int(rows[0]["valid_count"]) == 1


def _plan_reads(client, definition, scope, now):
    """Physical table reads in the plan of one overall rollup over a snapshot."""
    query, params = snapshot_select(
        definition, scope, now - timedelta(days=1), now, now, [], ""
    )
    formula = compile_formula(
        definition.formula, [item["alias"] for item in definition.inputs]
    )
    plan = client.execute(
        "EXPLAIN PLAN " + aggregate_select(query, formula, rollup="overall"),
        {**params, **formula.parameters},
        # The plan text has one row per step; only the result row cap is lifted.
        settings={**QUERY_SETTINGS, "max_result_rows": 0},
    )
    reads = Counter()
    for (line,) in plan:
        match = re.search(r"ReadFromMergeTree \(([^)]*)\)", line)
        if match:
            reads[match.group(1).rsplit(".", 1)[-1]] += 1
    return reads


@pytest.mark.parametrize("selection_unit", ["session", "span"])
def test_snapshot_plan_reads_the_evaluation_log_once(metric_ch, selection_unit):
    """ClickHouse inlines a WITH clause at every reference, so each extra
    reference re-reads its sources. All references share one per-query read and
    time budget: re-reading the span window or the evaluation log shrinks the
    largest window a run can compute and multiplies planning work."""
    scope, now = (
        Scope(uuid4(), uuid4(), uuid4()),
        datetime.now(UTC).replace(microsecond=0),
    )
    session, user, configs = uuid4(), uuid4(), [uuid4(), uuid4()]
    # Every source holds a row so each read is planned as a MergeTree read.
    for table in ("trace_session_id_remap", "end_user_id_remap"):
        metric_ch.execute(
            f"INSERT INTO {table} (old_id,new_id) VALUES (%(old)s,%(new)s)",
            {"old": str(uuid4()), "new": str(uuid4())},
        )
    seed_span(metric_ch, scope, "one", session, user, now - timedelta(hours=1))
    for config in configs:
        seed_eval(metric_ch, config, session, now - timedelta(minutes=10), 1, 0.5)
    definition = recipe(configs, "inverse_probability")
    definition.selection_unit = selection_unit
    reads = _plan_reads(metric_ch, definition, scope, now)
    assert reads["tracer_eval_logger_v2"] == 1
    # Units, the evaluation-target map and the three evaluation prefilter keys.
    assert reads["spans"] <= 5
    assert reads["trace_session_id_remap"] <= 10
    assert reads["end_user_id_remap"] <= 4


@pytest.mark.parametrize(
    "formula,expected",
    [
        ("mean(resolved + escalated)", 5 / 3),
        ("sum(resolved + escalated)", 10),
        ("count_if(resolved == 1 and escalated == 0)", 2),
    ],
)
def test_weighted_means_sums_and_conditional_counts(metric_ch, formula, expected):
    scope, now = (
        Scope(uuid4(), uuid4(), uuid4()),
        datetime.now(UTC).replace(microsecond=0),
    )
    configs = [uuid4(), uuid4()]
    for index, probability in enumerate([0.5, 0.25]):
        session = uuid4()
        seed_span(
            metric_ch, scope, str(index), session, uuid4(), now - timedelta(hours=1)
        )
        seed_eval(
            metric_ch, configs[0], session, now - timedelta(minutes=10), 1, probability
        )
        seed_eval(
            metric_ch,
            configs[1],
            session,
            now - timedelta(minutes=10),
            index,
            probability,
        )
    definition = recipe(configs, "inverse_probability")
    definition.formula = formula
    row = execute_formula(metric_ch, definition, scope, now)[0]
    assert row["status"] == "ok" and float(row["value"]) == pytest.approx(expected)
    if not formula.startswith("mean"):
        assert row["denominator"] is None


@pytest.mark.parametrize("different_units", [False, True])
def test_equal_probabilities_from_independent_cohorts_are_rejected(
    metric_ch, different_units
):
    scope, now = (
        Scope(uuid4(), uuid4(), uuid4()),
        datetime.now(UTC).replace(microsecond=0),
    )
    configs = [uuid4(), uuid4()]
    for index in range(2 if different_units else 1):
        session = uuid4()
        seed_span(
            metric_ch,
            scope,
            f"unit-{index}",
            session,
            uuid4(),
            now - timedelta(hours=1),
        )
        for config_index, config in enumerate(configs):
            cohort = (
                "first"
                if (index if different_units else config_index) == 0
                else "independent"
            )
            seed_eval(
                metric_ch,
                config,
                session,
                now - timedelta(minutes=10),
                1 - config_index,
                0.5,
                cohort_id=cohort,
            )
    row = execute_formula(
        metric_ch, recipe(configs, "inverse_probability"), scope, now
    )[0]
    assert row["status"] == "invalid_probability"
    assert row["value"] is None and row["numerator"] is None
    assert int(row["invalid_probability_count"]) == (2 if different_units else 1)


def test_sampling_stratum_is_default_cohort_and_wrong_canonical_id_fails(metric_ch):
    scope, now, session = (
        Scope(uuid4(), uuid4(), uuid4()),
        datetime.now(UTC).replace(microsecond=0),
        uuid4(),
    )
    configs = [uuid4(), uuid4()]
    seed_span(metric_ch, scope, "one", session, uuid4(), now - timedelta(hours=1))
    for index, config in enumerate(configs):
        seed_eval(
            metric_ch, config, session, now - timedelta(minutes=10), 1 - index, 0.5
        )
    definition = recipe(configs, "inverse_probability")
    definition.cohort_key = ""
    row = execute_formula(metric_ch, definition, scope, now, "cohort")[0]
    assert row["group_key"] == "region-west" and row["status"] == "ok"
    seed_eval(
        metric_ch,
        configs[0],
        session,
        now - timedelta(minutes=1),
        1,
        0.5,
        sampling_unit_id=uuid4(),
    )
    row = execute_formula(metric_ch, definition, scope, now)[0]
    assert row["status"] == "invalid_probability" and row["value"] is None


def test_persisting_thousand_unit_rollups_is_not_limited_to_interactive_page_size(
    metric_ch,
):
    from tracer.services.derived_metrics.queries import rollup_insert

    scope, now = (
        Scope(uuid4(), uuid4(), uuid4()),
        datetime.now(UTC).replace(microsecond=0),
    )
    when = now - timedelta(hours=1)
    metric_ch.execute(
        """INSERT INTO spans (id,project_id,org_id,trace_session_id,end_user_id,start_time,created_at,updated_at,attrs_number)
        SELECT concat('unit-', toString(number)), %(project)s, %(org)s, generateUUIDv4(), %(user)s,
        %(at)s, %(at)s, %(at)s, map('resolved', toFloat64(1), 'escalated', toFloat64(number % 2))
        FROM numbers(1000)""".replace("number % 2", "number %% 2"),
        {
            "project": str(scope.project),
            "org": str(scope.organization),
            "user": str(uuid4()),
            "at": when,
        },
    )
    definition = recipe()
    definition.metric = SimpleNamespace(
        id=uuid4(),
        organization_id=scope.organization,
        workspace_id=scope.workspace,
        project_id=scope.project,
    )
    definition.version, definition.definition_hash = 1, "d" * 64
    run = SimpleNamespace(
        id=uuid4(),
        snapshot_id=uuid4(),
        window_start=now - timedelta(days=1),
        window_end=now,
        as_of=now,
        source_run_id="",
        source_manifest={"sampling_scope": ""},
    )
    query, params = snapshot_select(
        definition, scope, run.window_start, now, now, [], ""
    )
    params["snapshot_id"] = str(run.snapshot_id)
    metric_ch.execute(
        f"INSERT INTO derived_metric_inputs SELECT %(org_id)s, %(workspace_id)s, %(project_id)s, %(snapshot_id)s, frozen.* FROM ({query}) frozen",
        params,
        settings=QUERY_SETTINGS,
    )
    count = metric_ch.execute(
        "SELECT count() FROM derived_metric_inputs WHERE snapshot_id = %(snapshot_id)s",
        params,
    )[0][0]
    assert int(count) == 1000
    result_id = uuid4()
    for rollup in ["overall", "session"]:
        query, params = rollup_insert(definition, run, result_id, rollup)
        metric_ch.execute(query, params, settings=QUERY_SETTINGS)
    rows = metric_ch.execute(
        "SELECT value, numerator, denominator, unit_count FROM derived_metric_results WHERE result_id = %(result_id)s AND rollup = 'overall'",
        {"result_id": str(result_id)},
    )
    assert rows[0][:3] == (0.5, 500, 1000)
    assert int(rows[0][3]) == 1000
    assert (
        int(
            metric_ch.execute(
                "SELECT count() FROM derived_metric_results WHERE result_id = %(result_id)s",
                {"result_id": str(result_id)},
            )[0][0]
        )
        == 1001
    )


def test_newest_failed_evaluation_does_not_fall_back_to_an_older_score(metric_ch):
    scope, now, session = (
        Scope(uuid4(), uuid4(), uuid4()),
        datetime.now(UTC).replace(microsecond=0),
        uuid4(),
    )
    configs = [uuid4(), uuid4()]
    seed_span(metric_ch, scope, "one", session, uuid4(), now - timedelta(hours=1))
    for index, config in enumerate(configs):
        seed_eval(
            metric_ch, config, session, now - timedelta(minutes=10), 1 - index, 0.5
        )
    seed_eval(
        metric_ch,
        configs[0],
        session,
        now - timedelta(minutes=1),
        None,
        0.5,
        status="errored",
        error=1,
    )
    row = execute_formula(
        metric_ch, recipe(configs, "inverse_probability"), scope, now
    )[0]
    assert row["status"] == "missing_inputs" and row["value"] is None
    assert int(row["missing_count"]) == 1


def test_half_open_window_preserves_microseconds(metric_ch):
    scope = Scope(uuid4(), uuid4(), uuid4())
    end = datetime.now(UTC).replace(microsecond=500000)
    start = end - timedelta(days=1)
    seed_span(
        metric_ch,
        scope,
        "before",
        uuid4(),
        uuid4(),
        start - timedelta(microseconds=1),
        escalated=1,
    )
    seed_span(metric_ch, scope, "first", uuid4(), uuid4(), start, escalated=0)
    seed_span(
        metric_ch,
        scope,
        "last",
        uuid4(),
        uuid4(),
        end - timedelta(microseconds=1),
        escalated=0,
    )
    seed_span(metric_ch, scope, "after", uuid4(), uuid4(), end, escalated=1)
    row = execute_formula(metric_ch, recipe(), scope, end)[0]
    assert int(row["unit_count"]) == 2
    assert row["value"] == 1


def test_weight_overflow_has_finite_or_null_diagnostics(metric_ch):
    scope, now, session = (
        Scope(uuid4(), uuid4(), uuid4()),
        datetime.now(UTC).replace(microsecond=0),
        uuid4(),
    )
    configs = [uuid4(), uuid4()]
    seed_span(metric_ch, scope, "one", session, uuid4(), now - timedelta(hours=1))
    for index, config in enumerate(configs):
        seed_eval(
            metric_ch, config, session, now - timedelta(minutes=10), 1 - index, 1e-320
        )
    row = execute_formula(
        metric_ch, recipe(configs, "inverse_probability"), scope, now
    )[0]
    assert row["status"] == "non_finite"
    assert (
        row["value"] is None and row["numerator"] is None and row["denominator"] is None
    )


def test_cohort_values_do_not_collide_with_missing_or_mixed_groups(metric_ch):
    scope, now = (
        Scope(uuid4(), uuid4(), uuid4()),
        datetime.now(UTC).replace(microsecond=0),
    )
    mixed = uuid4()
    for index, (session, cohort) in enumerate(
        [
            (mixed, "west"),
            (mixed, "east"),
            (uuid4(), "[mixed]"),
            (uuid4(), ""),
            (uuid4(), None),
        ]
    ):
        seed_span(
            metric_ch,
            scope,
            f"cohort-{index}",
            session,
            uuid4(),
            now - timedelta(hours=1),
            cohort=cohort,
        )
    rows = execute_formula(metric_ch, recipe(), scope, now, "cohort")
    assert {row["group_key"] for row in rows} == {
        '["mixed"]',
        '["value","[mixed]"]',
        '["value",""]',
        '["missing"]',
    }
    assert all(int(row["unit_count"]) == 1 for row in rows)


@pytest.mark.parametrize(
    "fault",
    [
        "window_mismatch",
        "outside_frame",
        "missing_mode",
        "missing_freeze",
        "future_freeze",
    ],
)
def test_weighted_lineage_requires_compatible_window_and_membership(metric_ch, fault):
    scope, now, session = (
        Scope(uuid4(), uuid4(), uuid4()),
        datetime.now(UTC).replace(microsecond=0),
        uuid4(),
    )
    configs = [uuid4(), uuid4()]
    seed_span(metric_ch, scope, "one", session, uuid4(), now - timedelta(hours=1))
    for index, config in enumerate(configs):
        override = {}
        if fault == "window_mismatch" and index == 1:
            override["window"] = {
                "start": (now - timedelta(days=2)).isoformat(),
                "end": (now + timedelta(hours=1)).isoformat(),
            }
        elif fault == "outside_frame":
            override["window"] = {
                "start": (now - timedelta(hours=2)).isoformat(),
                "end": (now + timedelta(hours=1)).isoformat(),
            }
        elif fault == "missing_mode":
            override["membership_mode"] = None
        elif fault == "missing_freeze":
            override["membership_mode"] = "closed"
        elif fault == "future_freeze":
            override = {
                "membership_mode": "closed",
                "population_size": 2,
                "cohort_frozen_at": (now + timedelta(minutes=1)).isoformat(),
                "window": {
                    "start": (now - timedelta(days=1)).isoformat(),
                    "end": now.isoformat(),
                },
            }
        seed_eval(
            metric_ch,
            config,
            session,
            now - timedelta(minutes=10 - index),
            1 - index,
            0.5,
            selection_overrides=override,
        )
    row = execute_formula(
        metric_ch, recipe(configs, "inverse_probability"), scope, now
    )[0]
    assert row["status"] == "invalid_probability" and row["value"] is None


def test_closed_cohort_has_real_freeze_and_population_provenance(metric_ch):
    scope, now, session = (
        Scope(uuid4(), uuid4(), uuid4()),
        datetime.now(UTC).replace(microsecond=0),
        uuid4(),
    )
    end = now - timedelta(minutes=30)
    start = end - timedelta(days=1)
    configs = [uuid4(), uuid4()]
    seed_span(metric_ch, scope, "one", session, uuid4(), end - timedelta(hours=1))
    selection = {
        "membership_mode": "closed",
        "population_size": 2,
        "cohort_frozen_at": (end + timedelta(minutes=1)).isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
    }
    for index, config in enumerate(configs):
        seed_eval(
            metric_ch,
            config,
            session,
            end + timedelta(minutes=2),
            1 - index,
            0.5,
            selection_overrides=selection,
        )
    row = execute_formula(
        metric_ch,
        recipe(configs, "inverse_probability"),
        scope,
        now,
        window_start=start,
        window_end=end,
    )[0]
    assert row["status"] == "ok" and row["value"] == 1
    assert row["numerator"] == row["denominator"] == 2


def test_span_ids_are_scoped_by_trace_and_span_target_evals_follow_their_trace(
    metric_ch,
):
    scope, now = (
        Scope(uuid4(), uuid4(), uuid4()),
        datetime.now(UTC).replace(microsecond=0),
    )
    sessions, traces, configs = (
        [uuid4(), uuid4()],
        [uuid4(), uuid4()],
        [uuid4(), uuid4()],
    )
    for index in range(2):
        seed_span(
            metric_ch,
            scope,
            "same-span-id",
            sessions[index],
            uuid4(),
            now - timedelta(hours=1),
            trace_id=traces[index],
        )
        seed_eval(
            metric_ch,
            configs[0],
            sessions[index],
            now - timedelta(minutes=10),
            1 - index,
            1,
            span_id="same-span-id",
            trace_id=traces[index],
        )
        seed_eval(
            metric_ch,
            configs[1],
            sessions[index],
            now - timedelta(minutes=10),
            index,
            1,
            span_id="same-span-id",
            trace_id=traces[index],
        )
    definition = recipe(configs, "none")
    rows = execute_formula(metric_ch, definition, scope, now)
    assert rows[0]["status"] == "ok" and rows[0]["value"] == 0.5
    assert int(rows[0]["unit_count"]) == 2


@pytest.mark.parametrize("selection_unit", ["session", "user", "span"])
def test_distinct_trace_span_pairs_do_not_collapse_source_units(
    metric_ch, selection_unit
):
    scope, now = (
        Scope(uuid4(), uuid4(), uuid4()),
        datetime.now(UTC).replace(microsecond=0),
    )
    for escalated in [0, 1]:
        seed_span(
            metric_ch,
            scope,
            "duplicate-id",
            uuid4(),
            uuid4(),
            now - timedelta(hours=1),
            escalated=escalated,
            trace_id=uuid4(),
        )
    assert metric_ch.execute("SELECT count() FROM spans FINAL")[0][0] == 2
    definition = recipe()
    definition.selection_unit = selection_unit
    row = execute_formula(metric_ch, definition, scope, now)[0]
    assert (row["unit_count"], row["valid_count"], row["value"]) == (2, 2, 0.5)


def test_span_evaluation_never_attaches_to_same_id_in_another_trace(metric_ch):
    scope, now = (
        Scope(uuid4(), uuid4(), uuid4()),
        datetime.now(UTC).replace(microsecond=0),
    )
    traces, sessions, configs = (
        [uuid4(), uuid4()],
        [uuid4(), uuid4()],
        [uuid4(), uuid4()],
    )
    for index in range(2):
        seed_span(
            metric_ch,
            scope,
            "reused-id",
            sessions[index],
            uuid4(),
            now - timedelta(hours=1),
            trace_id=traces[index],
        )
    for index, config in enumerate(configs):
        seed_eval(
            metric_ch,
            config,
            sessions[0],
            now - timedelta(minutes=10),
            1 - index,
            1,
            span_id="reused-id",
            trace_id=traces[0],
        )
    rows = {
        row["group_key"]: row
        for row in execute_formula(metric_ch, recipe(configs), scope, now, "session")
    }
    assert rows[str(sessions[0])]["value"] == 1
    assert rows[str(sessions[1])]["status"] == "missing_inputs"
    assert rows[str(sessions[1])]["value"] is None


@pytest.mark.parametrize("collision", [False, True])
def test_span_weighting_requires_unambiguous_sampler_identity(metric_ch, collision):
    scope, now = (
        Scope(uuid4(), uuid4(), uuid4()),
        datetime.now(UTC).replace(microsecond=0),
    )
    configs = [uuid4(), uuid4()]
    for _ in range(2 if collision else 1):
        trace, session = uuid4(), uuid4()
        seed_span(
            metric_ch,
            scope,
            "sampled-id",
            session,
            uuid4(),
            now - timedelta(hours=1),
            trace_id=trace,
        )
        for number, config in enumerate(configs):
            seed_eval(
                metric_ch,
                config,
                session,
                now - timedelta(minutes=10),
                1 - number,
                0.5,
                span_id="sampled-id",
                trace_id=trace,
                selection_overrides={
                    "sampling_unit": "span",
                    "sampling_unit_id": "span:sampled-id",
                },
            )
    definition = recipe(configs, "inverse_probability")
    definition.selection_unit = "span"
    row = execute_formula(metric_ch, definition, scope, now)[0]
    assert row["status"] == ("invalid_probability" if collision else "ok")
    assert row["value"] == (None if collision else 1)


@pytest.mark.parametrize(
    "window_end,valid", [(None, True), ("", False), ("invalid", False)]
)
def test_live_sampling_window_can_be_explicitly_open_ended(
    metric_ch, window_end, valid
):
    scope, now, session = (
        Scope(uuid4(), uuid4(), uuid4()),
        datetime.now(UTC).replace(microsecond=0),
        uuid4(),
    )
    configs = [uuid4(), uuid4()]
    seed_span(metric_ch, scope, "one", session, uuid4(), now - timedelta(hours=1))
    for index, config in enumerate(configs):
        seed_eval(
            metric_ch,
            config,
            session,
            now - timedelta(minutes=10),
            1 - index,
            0.5,
            selection_overrides={
                "window": {
                    "start": (now - timedelta(days=2)).isoformat(),
                    "end": window_end,
                }
            },
        )
    row = execute_formula(
        metric_ch, recipe(configs, "inverse_probability"), scope, now
    )[0]
    assert row["status"] == ("ok" if valid else "invalid_probability")
    assert row["value"] == (1 if valid else None)


def seed_measured_span(client, scope, span_id, when, *, attrs_number=None, **columns):
    """One span per session; unsupplied measure columns keep the store's DEFAULT 0."""
    stamp = when.strftime("%Y-%m-%d %H:%M:%S.%f")
    values = {
        "id": span_id,
        "project_id": str(scope.project),
        "org_id": str(scope.organization),
        "trace_id": str(uuid4()),
        "trace_session_id": str(uuid4()),
        "end_user_id": str(uuid4()),
        "start_time": stamp,
        "created_at": stamp,
        "updated_at": stamp,
        **columns,
    }
    names = ", ".join([*values, "attrs_number"])
    placeholders = ", ".join(
        [*(f"%({name})s" for name in values), "mapFromArrays(%(keys)s, %(numbers)s)"]
    )
    client.execute(
        f"INSERT INTO spans ({names}) VALUES ({placeholders})",
        {
            **values,
            "keys": list(attrs_number or {}),
            "numbers": [float(value) for value in (attrs_number or {}).values()],
        },
    )


def measure_recipe(field, formula, **overrides):
    return SimpleNamespace(
        formula=formula,
        inputs=[
            {
                "alias": "value",
                "source": "span",
                "field": field,
                "attribute_key": overrides.pop("attribute_key", ""),
                "reducer": overrides.pop("reducer", "latest"),
            }
        ],
        selection_unit="session",
        weighting="none",
        cohort_key="",
        filters=[],
        **overrides,
    )


def test_latency_is_missing_when_the_span_never_ended(metric_ch):
    scope, now = (
        Scope(uuid4(), uuid4(), uuid4()),
        datetime.now(UTC).replace(microsecond=0),
    )
    at = now - timedelta(hours=1)
    ended = at + timedelta(milliseconds=100)
    seed_measured_span(
        metric_ch,
        scope,
        "ended",
        at,
        end_time=ended.strftime("%Y-%m-%d %H:%M:%S.%f"),
        latency_ms=100,
    )
    # A span that ended instantly measured a real zero.
    seed_measured_span(
        metric_ch,
        scope,
        "instant",
        at,
        end_time=at.strftime("%Y-%m-%d %H:%M:%S.%f"),
        latency_ms=0,
    )
    # A span that never ended has no end_time; its latency_ms is the column default 0.
    seed_measured_span(metric_ch, scope, "open", at)
    rows = execute_formula(
        metric_ch, measure_recipe("latency_ms", "mean(value)"), scope, now
    )
    assert rows[0]["status"] == "ok"
    assert float(rows[0]["value"]) == pytest.approx(50)  # (100 + 0) / 2, not / 3
    assert int(rows[0]["unit_count"]) == 3
    assert int(rows[0]["valid_count"]) == 2
    assert int(rows[0]["missing_count"]) == 1
    query, params = snapshot_select(
        measure_recipe("latency_ms", "mean(value)"),
        scope,
        now - timedelta(days=1),
        now,
        now,
        [],
        "",
    )
    frozen = metric_ch.execute(query, params, settings=QUERY_SETTINGS)
    # The frozen snapshot keeps an unmeasured latency as NULL, never as a stored 0.
    values = sorted((row[5]["value"] is None, row[5]["value"]) for row in frozen)
    assert values == [(False, 0.0), (False, 100.0), (True, None)]


def test_a_latency_reported_without_an_end_time_is_still_a_measurement(metric_ch):
    """Provider imports can store a latency for a call that has no end timestamp."""
    scope, now = (
        Scope(uuid4(), uuid4(), uuid4()),
        datetime.now(UTC).replace(microsecond=0),
    )
    at = now - timedelta(hours=1)
    seed_measured_span(metric_ch, scope, "reported", at, latency_ms=300)
    seed_measured_span(
        metric_ch,
        scope,
        "ended",
        at,
        end_time=(at + timedelta(milliseconds=100)).strftime("%Y-%m-%d %H:%M:%S.%f"),
        latency_ms=100,
    )
    # Neither ended nor reported: the stored 0 is the column DEFAULT.
    seed_measured_span(metric_ch, scope, "open", at)
    row = execute_formula(
        metric_ch, measure_recipe("latency_ms", "mean(value)"), scope, now
    )[0]
    assert row["status"] == "ok"
    assert float(row["value"]) == pytest.approx(200)  # (300 + 100) / 2
    assert int(row["valid_count"]) == 2 and int(row["missing_count"]) == 1


def test_only_an_open_span_population_has_no_latency_value(metric_ch):
    scope, now = (
        Scope(uuid4(), uuid4(), uuid4()),
        datetime.now(UTC).replace(microsecond=0),
    )
    for index in range(2):
        seed_measured_span(metric_ch, scope, f"open-{index}", now - timedelta(hours=1))
    row = execute_formula(
        metric_ch, measure_recipe("latency_ms", "mean(value)"), scope, now
    )[0]
    assert row["status"] == "missing_inputs"
    assert row["value"] is None and row["numerator"] is None
    assert int(row["missing_count"]) == 2 and int(row["valid_count"]) == 0


@pytest.mark.parametrize(
    "field", ["cost", "total_tokens", "prompt_tokens", "completion_tokens"]
)
def test_unreported_cost_and_tokens_are_missing_not_zero(metric_ch, field):
    scope, now = (
        Scope(uuid4(), uuid4(), uuid4()),
        datetime.now(UTC).replace(microsecond=0),
    )
    at = now - timedelta(hours=1)
    seed_measured_span(metric_ch, scope, "small", at, **{field: 2})
    seed_measured_span(metric_ch, scope, "large", at, **{field: 6})
    # Nothing was reported: the store holds its DEFAULT 0, which is not a measurement.
    seed_measured_span(metric_ch, scope, "unreported", at)
    mean = execute_formula(metric_ch, measure_recipe(field, "mean(value)"), scope, now)[
        0
    ]
    assert mean["status"] == "ok"
    assert float(mean["value"]) == pytest.approx(4)  # (2 + 6) / 2, not / 3
    assert int(mean["valid_count"]) == 2 and int(mean["missing_count"]) == 1
    total = execute_formula(metric_ch, measure_recipe(field, "sum(value)"), scope, now)[
        0
    ]
    assert float(total["value"]) == pytest.approx(8)
    only_unreported = Scope(uuid4(), uuid4(), uuid4())
    seed_measured_span(metric_ch, only_unreported, "none", at)
    empty = execute_formula(
        metric_ch, measure_recipe(field, "mean(value)"), only_unreported, now
    )[0]
    assert empty["status"] == "missing_inputs" and empty["value"] is None


def test_an_explicit_zero_attribute_is_a_measurement_unlike_a_default_column(
    metric_ch,
):
    scope, now = (
        Scope(uuid4(), uuid4(), uuid4()),
        datetime.now(UTC).replace(microsecond=0),
    )
    at = now - timedelta(hours=1)
    seed_measured_span(
        metric_ch, scope, "free", at, attrs_number={"gen_ai.cost.total": 0.0}
    )
    seed_measured_span(
        metric_ch, scope, "paid", at, attrs_number={"gen_ai.cost.total": 3.0}
    )
    seed_measured_span(metric_ch, scope, "silent", at)
    row = execute_formula(
        metric_ch,
        measure_recipe(
            "attribute_number", "mean(value)", attribute_key="gen_ai.cost.total"
        ),
        scope,
        now,
    )[0]
    assert float(row["value"]) == pytest.approx(
        1.5
    )  # a present 0 counts; absent does not
    assert int(row["valid_count"]) == 2 and int(row["missing_count"]) == 1


def test_session_measures_skip_spans_that_reported_nothing(metric_ch):
    """A session mixes LLM spans (cost, tokens) with tool/chain spans (none)."""
    scope, now = (
        Scope(uuid4(), uuid4(), uuid4()),
        datetime.now(UTC).replace(microsecond=0),
    )
    at = now - timedelta(hours=1)
    mixed, priced = str(uuid4()), str(uuid4())
    seed_measured_span(metric_ch, scope, "llm", at, trace_session_id=mixed, cost=4)
    # The tool span runs last and carries the store's DEFAULT 0 for cost.
    seed_measured_span(
        metric_ch,
        scope,
        "tool",
        at,
        trace_session_id=mixed,
        updated_at=(at + timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S.%f"),
    )
    seed_measured_span(metric_ch, scope, "other", at, trace_session_id=priced, cost=2)
    mean = execute_formula(
        metric_ch, measure_recipe("cost", "mean(value)", reducer="mean"), scope, now
    )[0]
    # Per-session mean over spans that reported a cost: (4 + 2) / 2. Reading the
    # tool span's stored 0 as a measurement would give ((4 + 0) / 2 + 2) / 2 = 2.
    assert mean["status"] == "ok"
    assert float(mean["value"]) == pytest.approx(3)
    assert int(mean["unit_count"]) == 2 and int(mean["missing_count"]) == 0
    # "latest" is the latest span's own value and does not fall back to an
    # earlier span: here the latest span reported nothing, so that unit is missing.
    latest = execute_formula(
        metric_ch, measure_recipe("cost", "mean(value)"), scope, now
    )[0]
    assert float(latest["value"]) == pytest.approx(2)
    assert int(latest["valid_count"]) == 1 and int(latest["missing_count"]) == 1
