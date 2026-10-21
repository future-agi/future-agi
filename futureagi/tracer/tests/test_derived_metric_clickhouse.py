"""Executable SQL fixtures on a guarded sidecar or an explicitly opted-in chDB.

DERIVED_METRIC_TEST_CHDB=1 runs the same plans in embedded ClickHouse (no Docker).
The default native path uses the repository's test-sidecar mutation guard.
"""

import json
import os
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
        start_time DateTime64(6,'UTC'), created_at DateTime64(6,'UTC'), updated_at DateTime64(6,'UTC'),
        _version UInt64 DEFAULT 1, is_deleted UInt8 DEFAULT 0,
        model String DEFAULT '', provider String DEFAULT '', status String DEFAULT '',
        name String DEFAULT '', service_name String DEFAULT '',
        latency_ms Int32 DEFAULT 0, cost Float64 DEFAULT 0,
        total_tokens Int32 DEFAULT 0, prompt_tokens Int32 DEFAULT 0, completion_tokens Int32 DEFAULT 0,
        attrs_number Map(String,Float64), attrs_bool Map(String,UInt8), attrs_string Map(String,String)
    ) ENGINE=ReplacingMergeTree(_version) ORDER BY (project_id,start_time,id)""")
    client.execute("""CREATE TABLE tracer_eval_logger_v2 (
        id UUID, custom_eval_config_id UUID, config_hash Nullable(String),
        observation_span_id Nullable(String), trace_session_id Nullable(UUID), target_type String,
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
):
    client.execute(
        """INSERT INTO spans (id, project_id, org_id, trace_session_id, end_user_id,
        start_time, created_at, updated_at, attrs_number, attrs_string)
        VALUES (%(span)s, %(project)s, %(org)s, %(session)s, %(user)s, %(at)s, %(at)s, %(at)s,
        map('resolved', %(resolved)s, 'escalated', %(escalated)s), if(%(cohort_present)s, map('region', %(cohort)s), map()))""",
        {
            "span": span_id,
            "project": str(scope.project),
            "org": str(scope.organization),
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
        observation_span_id, trace_session_id, target_type, created_at, updated_at, _version,
        is_deleted, output_float, output_metadata, status, error)
        VALUES (%(id)s, %(config)s, %(hash)s, %(span)s, %(session)s, %(target_type)s,
        %(at)s, %(at)s, %(version)s, %(deleted)s, %(score)s, %(metadata)s, %(status)s, %(error)s)""",
        {
            "id": str(row_id or uuid4()),
            "config": str(config_id),
            "hash": config_hash,
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
