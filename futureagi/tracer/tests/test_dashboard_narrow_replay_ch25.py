"""Live CH 25.3 proof that the narrow dashboard replay equals FINAL.

The replay source resolves latest state from a packed ``argMax`` winner plus a
version-only replay leg. That is only admissible if it agrees with the engine's
own merge on every churn shape: a later non-matching version, a tombstone, a
re-parented span, a cleared key, a corrected timestamp and an equal-version
tie. Rows are synthetic and live in a disposable table on the local instance.

This module issues DDL and DML as an admin user, so it opts in explicitly
before it opens a socket: see ``_live_native_port``. Nothing here resolves a
default port, because a developer host's well-known ClickHouse ports are held
by port-forwards to shared clusters.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from clickhouse_driver import Client
from clickhouse_driver.util.escape import escape_params

from tracer.services.clickhouse.v2.adapter import CH_INSERT_COLUMNS
from tracer.services.clickhouse.v2.query_builders.dashboard import (
    DashboardQueryBuilderV2,
)

pytestmark = pytest.mark.integration

CH_HOST = os.environ.get("CH25_HOST", "127.0.0.1")
CH_USER = os.environ.get("CH25_USER", "default")
CH_PASSWORD = os.environ.get("CH25_PASSWORD", "")
CH_DATABASE = os.environ.get("CH25_DATABASE", "default")

LIVE_CH_TESTS_ENV_VAR = "FI_LIVE_CH_TESTS"

# Native ports a developer host keeps pointed at shared ClickHouse clusters.
# This suite creates and drops objects, so it refuses them outright rather
# than trusting whoever exported the variable to have meant the test stack.
REFUSED_NATIVE_PORTS = frozenset({19000, 19001, 19002, 19010, *range(18230, 18233)})

DRIVER_CONTEXT = SimpleNamespace(
    server_info=SimpleNamespace(get_timezone=lambda: "UTC")
)
PROJECT = uuid.UUID("11111111-1111-4111-8111-111111111111")
HOUR = datetime(2026, 9, 10, 10, tzinfo=UTC).replace(tzinfo=None)
WINDOW_START = datetime(2026, 9, 10, tzinfo=UTC)
WINDOW_END = datetime(2026, 9, 11, tzinfo=UTC)

COLUMNS = (
    "project_id",
    "observation_type",
    "service_name",
    "start_time",
    "trace_id",
    "id",
    "parent_span_id",
    "name",
    "end_time",
    "latency_ms",
    "org_id",
    "project_version_id",
    "end_user_id",
    "trace_session_id",
    "prompt_version_id",
    "prompt_label_id",
    "custom_eval_config_id",
    "status",
    "status_message",
    "model",
    "provider",
    "gen_ai_system",
    "gen_ai_operation",
    "operation_name",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cost",
    "attrs_string",
    "attrs_number",
    "attrs_bool",
    "attributes_extra",
    "input",
    "output",
    "input_gcs_url",
    "output_gcs_url",
    "tags",
    "span_events",
    "eval_status",
    "semconv_source",
    "created_at",
    "updated_at",
    "is_deleted",
    "_version",
)

_SPANS_DDL = """
    project_id UUID, observation_type LowCardinality(String),
    service_name LowCardinality(String), start_time DateTime64(6, 'UTC'),
    trace_id String, id String, parent_span_id String, name String,
    end_time Nullable(DateTime64(6, 'UTC')), latency_ms Int32,
    org_id Nullable(UUID), project_version_id Nullable(UUID),
    end_user_id Nullable(UUID), trace_session_id Nullable(UUID),
    prompt_version_id Nullable(UUID), prompt_label_id Nullable(UUID),
    custom_eval_config_id Nullable(UUID), status LowCardinality(String),
    status_message String, model LowCardinality(String),
    provider LowCardinality(String), gen_ai_system LowCardinality(String),
    gen_ai_operation LowCardinality(String),
    operation_name LowCardinality(String), prompt_tokens Int32,
    completion_tokens Int32, total_tokens Int32, cost Float64,
    attrs_string Map(LowCardinality(String), String),
    attrs_number Map(LowCardinality(String), Float64),
    attrs_bool Map(LowCardinality(String), UInt8),
    attributes_extra String, input String, output String,
    input_gcs_url Nullable(String), output_gcs_url Nullable(String),
    tags String, span_events String, eval_status LowCardinality(String),
    semconv_source LowCardinality(String), created_at DateTime64(6, 'UTC'),
    updated_at DateTime64(6, 'UTC'), is_deleted UInt8, _version UInt64
"""


def _live_native_port() -> int:
    """Return the opted-into native port, or skip before any socket is opened.

    There is deliberately no default and no fallback chain onto a sibling
    variable. An unpinned run must not resolve to whatever happens to be
    listening on a well-known port: on a developer host those are held by
    port-forwards to shared clusters, and this suite runs ``CREATE``/``INSERT``
    as an admin user. The caller names the disposable stack, or gets a skip.
    """

    if os.environ.get(LIVE_CH_TESTS_ENV_VAR) != "1":
        pytest.skip(f"live ClickHouse tests are opt-in: set {LIVE_CH_TESTS_ENV_VAR}=1")

    raw_port = os.environ.get("CH25_NATIVE_PORT", "").strip()
    if not raw_port:
        pytest.skip(
            "CH25_NATIVE_PORT is not set; this suite will not guess a "
            "ClickHouse port for a test that writes"
        )
    try:
        port = int(raw_port)
    except ValueError:
        pytest.skip(f"CH25_NATIVE_PORT={raw_port!r} is not a port number")

    if port in REFUSED_NATIVE_PORTS:
        pytest.skip(
            f"refusing to write to ClickHouse on port {port}: that port is "
            "reserved for port-forwards to shared clusters on this host"
        )
    return port


@pytest.fixture(scope="module")
def ch_port() -> int:
    return _live_native_port()


@pytest.fixture(scope="module")
def ch_client(ch_port: int):
    client = Client(
        host=CH_HOST,
        port=ch_port,
        user=CH_USER,
        password=CH_PASSWORD,
        database=CH_DATABASE,
        connect_timeout=3,
        settings={"optimize_on_insert": 0},
    )
    try:
        client.execute("SELECT 1")
    except Exception as exc:  # pragma: no cover - environment probe
        pytest.skip(f"CH 25.3 not reachable on {CH_HOST}:{ch_port} ({exc!r})")
    return client


def _row(
    span_id,
    version,
    *,
    stage="intake",
    measurement="alpha",
    latency=100,
    deleted=0,
    parent="",
    minute=30,
    attrs=None,
    name="span",
):
    attributes = (
        {"workflow_stage": stage, "measurement": measurement}
        if attrs is None
        else attrs
    )
    return (
        PROJECT,
        "span",
        "svc",
        HOUR.replace(minute=minute),
        "trace-1",
        span_id,
        parent,
        name,
        None,
        latency,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        "OK",
        "",
        "m",
        "p",
        "",
        "",
        "",
        0,
        0,
        0,
        0.0,
        attributes,
        {"score": 1.0},
        {"flag": 1},
        "",
        "",
        "",
        None,
        None,
        "",
        "",
        "",
        "",
        HOUR,
        HOUR,
        deleted,
        version,
    )


# id -> the latency the FINAL winner must contribute, or None when the latest
# version is not a public match at all.
POPULATION = (
    # One version, matches.
    ([_row("A", 1, latency=10)], 10),
    # Later version moved the value out of the filter.
    ([_row("B", 1, latency=20), _row("B", 2, stage="archived", latency=999)], None),
    # Later matching version corrected the value and the breakdown.
    ([_row("C", 1, latency=30), _row("C", 2, latency=31, measurement="beta")], 31),
    # Tombstone after a matching version.
    ([_row("D", 1, latency=40), _row("D", 2, latency=40, deleted=1)], None),
    # Root span later re-parented: the root-only metric must drop it.
    ([_row("E", 1, latency=50), _row("E", 2, latency=50, parent="A")], None),
    # Equal-version tie differing only in a column nothing reads.
    (
        [_row("F", 7, latency=60, name="left"), _row("F", 7, latency=60, name="right")],
        60,
    ),
    # Newest version cleared the filtered key entirely.
    ([_row("G", 1, latency=70), _row("G", 2, latency=70, attrs={"other": "x"})], None),
    # Newest version is the one that starts matching.
    ([_row("H", 1, latency=80, attrs={"other": "y"}), _row("H", 2, latency=81)], 81),
    # Corrected start_time inside the identity's own hour.
    ([_row("I", 1, latency=90), _row("I", 2, latency=91, minute=45)], 91),
)


@pytest.fixture()
def churn_table(ch_client):
    table = f"_test_dashboard_narrow_replay_{uuid.uuid4().hex[:8]}"
    ch_client.execute(
        f"""
        CREATE TABLE {table} ({_SPANS_DDL})
        ENGINE = ReplacingMergeTree(_version, is_deleted)
        PARTITION BY toDate(start_time)
        PRIMARY KEY (
            project_id, observation_type, service_name, toStartOfHour(start_time)
        )
        ORDER BY (
            project_id, observation_type, service_name,
            toStartOfHour(start_time), trace_id, id
        )
        SETTINGS allow_nullable_key = 1
        """
    )
    ch_client.execute(f"SYSTEM STOP MERGES {table}")
    rows = [row for versions, _ in POPULATION for row in versions]
    ch_client.execute(f"INSERT INTO {table} ({', '.join(COLUMNS)}) VALUES", rows)
    try:
        yield table
    finally:
        ch_client.execute(f"DROP TABLE {table}")


def _config(granularity, breakdown):
    config = {
        "project_ids": [str(PROJECT)],
        "granularity": granularity,
        "time_range": {
            "custom_start": WINDOW_START.isoformat(),
            "custom_end": WINDOW_END.isoformat(),
        },
        "metrics": [
            {
                "id": "latency",
                "name": "latency",
                "type": "system_metric",
                "source": "traces",
                "aggregation": "avg",
            }
        ],
        "filters": [
            {
                "metric_type": "custom_attribute",
                "metric_name": "workflow_stage",
                "operator": "in",
                "value": ["intake", "review"],
                "attribute_type": "string",
                "source": "traces",
                "canonical_filter": {
                    "column_id": "workflow_stage",
                    "filter_config": {
                        "col_type": "SPAN_ATTRIBUTE",
                        "filter_type": "text",
                        "filter_op": "in",
                        "filter_value": ["intake", "review"],
                        "attribute_value_types": ["string", "string"],
                    },
                },
            }
        ],
        "breakdowns": [],
    }
    if breakdown is not None:
        config["breakdowns"] = [
            {
                "name": breakdown,
                "type": "custom_attribute",
                "source": "traces",
                "attribute_type": "string",
            }
        ]
    return config


def _render(config, table, *, final_twin):
    builder = DashboardQueryBuilderV2(config)
    if final_twin:
        # The untouched fallback source: ``FROM spans AS finalized FINAL``.
        builder._exact_filter_replay_source = lambda *args, **kwargs: None
    sql, params = builder._build_metric_query_for_snapshot_mode(
        builder.metrics[0], latest_state=True
    )
    sql = re.sub(r"\bFROM spans AS\b", f"FROM {table} AS", sql)
    return sql % escape_params(params, context=DRIVER_CONTEXT)


RECIPES = (
    ("scalar", "month", None),
    ("series", "day", None),
    ("breakdown", "day", "measurement"),
)


@pytest.mark.parametrize("recipe, granularity, breakdown", RECIPES)
def test_narrow_replay_matches_final_over_every_churn_shape(
    ch_client, churn_table, recipe, granularity, breakdown
):
    config = _config(granularity, breakdown)
    replayed = ch_client.execute(_render(config, churn_table, final_twin=False))
    finalized = ch_client.execute(_render(config, churn_table, final_twin=True))

    assert sorted(map(repr, replayed)) == sorted(map(repr, finalized))
    survivors = [latency for _, latency in POPULATION if latency is not None]
    assert survivors, "the fixture must leave some public rows"
    if breakdown is None:
        assert len(replayed) == 1
        assert replayed[0][-1] == pytest.approx(sum(survivors) / len(survivors))
    else:
        # C's newest version moved it to its own breakdown bucket.
        assert {bucket: value for _, bucket, value in replayed} == {
            "alpha": pytest.approx(60.5),
            "beta": pytest.approx(31.0),
        }


def test_version_agreement_is_what_excludes_stale_identities(ch_client, churn_table):
    """Drop the HAVING and the answer changes — the check is load-bearing."""

    config = _config("day", None)
    exact = ch_client.execute(_render(config, churn_table, final_twin=False))
    mutant_sql = _render(config, churn_table, final_twin=False).replace(
        "HAVING max(dashboard_replay_source._version)\n"
        "                    = any(dashboard_candidate_state."
        "dashboard_witness_version)",
        "HAVING 1 = 1",
    )
    assert "HAVING 1 = 1" in mutant_sql
    mutant = ch_client.execute(mutant_sql)
    assert exact != mutant


def test_replay_leg_reads_no_attribute_map(ch_client, churn_table):
    plan = "\n".join(
        row[0]
        for row in ch_client.execute(
            "EXPLAIN header = 1 "
            + _render(_config("day", "measurement"), churn_table, final_twin=False)
        )
    )
    scans = re.split(rf"ReadFromMergeTree \([\w.]*{churn_table}\)", plan)[1:]
    assert len(scans) == 2
    headers = [
        [line.strip() for line in scan.split("Expression", 1)[0].strip().splitlines()]
        for scan in scans
    ]
    # Identify the legs by what they read, not by the order the planner emits.
    narrow = [
        header
        for header in headers
        if not any(line.startswith("attrs_") for line in header)
    ]
    assert len(narrow) == 1, headers
    columns = narrow[0]
    assert any(line.startswith("_version UInt64") for line in columns)
    # The six identity coordinates plus the version, and nothing else. One
    # header line per column, the first carrying the "Header:" label.
    assert len(columns) == 7, columns
    wide = [header for header in headers if header is not narrow[0]][0]
    assert any(line.startswith("attrs_string ") for line in wide)


def test_stored_projection_matches_the_column_set_the_winner_packs(ch_client):
    """A new ordinary column must reach the winner tuple, not vanish from it.

    ``SELECT spans.*`` used to publish every ordinary column. The packed winner
    is built from ``CH_INSERT_COLUMNS``; if the deployed table grows an ordinary
    column that constant does not list, the replay source would silently stop
    publishing it. Fail here rather than at a customer's widget.
    """

    stored = {
        name
        for (name,) in ch_client.execute(
            "SELECT name FROM system.columns "
            "WHERE database = %(database)s AND table = 'spans' "
            "AND default_kind NOT IN ('MATERIALIZED', 'ALIAS')",
            {"database": CH_DATABASE},
        )
    }
    if not stored:  # pragma: no cover - environment probe
        pytest.skip("no spans table on this instance")
    assert stored == set(CH_INSERT_COLUMNS) | {"_version"}
