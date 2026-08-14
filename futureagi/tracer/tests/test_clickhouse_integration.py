"""
ClickHouse Integration Tests

Tests that execute real queries against a ClickHouse instance.
These tests require a running ClickHouse server and are skipped
when ClickHouse is not available.

Run with:
    pytest tracer/tests/test_clickhouse_integration.py -v -m integration

Requires:
    - ClickHouse running on CH_TEST_HOST:CH_TEST_PORT (default: localhost:18123)
    - clickhouse-connect package installed

Covered:
- Connection and schema lifecycle
- SimulationQueryBuilder integration (system metrics, breakdowns, filters)
- DatasetQueryBuilder integration (system metrics, breakdowns)
- usage_apicalllog eval-score materialization per eval-output shape
- an opt-in eval-score backfill benchmark, see TestEvalScoreBackfillAtScale
"""

import json
import os
import time
import uuid
from datetime import datetime, timezone

import pytest

# ---------------------------------------------------------------------------
# Session-scoped fixtures
# ---------------------------------------------------------------------------

_TEST_DATABASE = "test_futureagi"
_EVAL_TABLE = f"{_TEST_DATABASE}.usage_apicalllog"

# (label, config payload, the eval_score it must materialize, holds a real number)
EVAL_OUTPUT_SHAPES: tuple[tuple[str, dict, float, bool], ...] = (
    (
        "structured_complete",
        {"output": {"output": {"score": 1, "choice": "Complete"}}},
        1.0,
        True,
    ),
    (
        "structured_partial",
        {"output": {"output": {"score": 0.5, "choice": "Partial"}}},
        0.5,
        True,
    ),
    (
        "structured_incomplete",
        {"output": {"output": {"score": 0.2, "choice": "Incomplete"}}},
        0.2,
        True,
    ),
    (
        "structured_null_score",
        {"output": {"output": {"score": None, "choice": "Unknown"}}},
        0.0,
        False,
    ),
    (
        "structured_text_score",
        {"output": {"output": {"score": "high"}}},
        0.0,
        False,
    ),
    ("scalar_score", {"output": {"output": 0.8}}, 0.8, True),
    ("text_output", {"output": {"output": "Passed"}}, 0.0, False),
    ("no_output", {"output": {}}, 0.0, False),
)

EVAL_SCORED_SHAPES = tuple(s for s in EVAL_OUTPUT_SHAPES if s[3])
EVAL_SCORED_AVERAGE = 0.625

# Over every shape: the four numbers above plus "Passed" read as 1.0. The empty,
# null-score and text-score rows are excluded rather than averaged in as 0.
EVAL_ALL_SHAPES_AVERAGE = 0.7

# Every path that answers "did this eval pass?" has to name this same set.
EVAL_PASSING_SHAPES = frozenset({"structured_complete", "text_output"})

EVAL_SHAPE_PAYLOADS = {label: payload for label, payload, _s, _h in EVAL_OUTPUT_SHAPES}
EVAL_SHAPE_SCORES = {label: score for label, _p, score, _h in EVAL_OUTPUT_SHAPES}

# ---------------------------------------------------------------------------
# Backfill benchmark seeding (opt-in, see TestEvalScoreBackfillAtScale)
# ---------------------------------------------------------------------------

# Unqualified: the benchmark client sets _TEST_DATABASE as its default, so the
# command's own currentDatabase()-scoped partition query works verbatim.
_BENCH_TABLE = "usage_apicalllog_bench"

# Upper bounds per 100k rows, so the production mix is expressible exactly:
# structured outputs are a 1.56% slice, split 76 / 18 / 6 inside that slice.
BENCH_BUCKET_SCALE = 100_000
BENCH_MIX: tuple[tuple[int, str], ...] = (
    (1_186, "structured_complete"),
    (1_467, "structured_incomplete"),
    (1_560, "structured_partial"),
    (41_560, "scalar_score"),
    (81_560, "text_output"),
    (BENCH_BUCKET_SCALE, "no_output"),
)

# Repeated prose plus a random tail, so a seeded row weighs about what a
# production row weighs on disk rather than compressing away to nothing.
BENCH_REASON_REPEATS = 20
BENCH_REASON_RANDOM_BYTES = 600
BENCH_REASON_SENTENCES: tuple[str, ...] = (
    "The response addresses every requirement stated in the user prompt.",
    "Key steps in the reasoning chain are present and ordered correctly.",
    "The assistant restated the constraint before applying it to the answer.",
    "No unsupported factual claims were introduced beyond the given context.",
    "The final paragraph summarises the outcome without adding new content.",
    "Tone stays consistent with the system instruction throughout the turn.",
    "One requested field is missing from the structured portion of the reply.",
    "The answer stops short of covering the third sub question that was asked.",
    "Formatting follows the requested schema and parses without modification.",
    "Citations map to passages that actually appear in the retrieved context.",
    "The model hedged where the context was genuinely ambiguous, which is fine.",
    "A minor arithmetic slip appears in the intermediate calculation step.",
    "Latency stayed inside the budget configured for this evaluation template.",
    "The refusal was appropriate given the policy category that was triggered.",
    "Coverage of the source document is partial but the omissions are minor.",
    "The reply repeats the question back before answering, which is redundant.",
    "Instructions about output length were respected within a small margin.",
    "The completion resolves the ambiguity by asking a clarifying question.",
    "Nothing in the transcript indicates the tool call result was ignored.",
    "The grader found the response complete against all listed criteria.",
)

# Small blocks: a seeded row carries kilobytes of config, so the default block
# size builds multi-GB buffers and a modest box runs out of memory mid-seed.
BENCH_SEED_SETTINGS = (
    "max_insert_threads = 1, max_threads = 1, max_memory_usage = 1400000000, "
    "max_block_size = 1024, max_insert_block_size = 32768, "
    "min_insert_block_size_rows = 32768, min_insert_block_size_bytes = 0"
)
BENCH_READ_SETTINGS = "max_memory_usage = 1400000000, max_threads = 3"
BENCH_SETTLED_PARTS = 16


def _bench_sql_literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _bench_multi_if(column: str, value_for_label) -> str:
    """Render BENCH_MIX as a multiIf over a per-row bucket column."""
    branches = [
        f"{column} < {upper}, {_bench_sql_literal(value_for_label(label))}"
        for upper, label in BENCH_MIX[:-1]
    ]
    branches.append(_bench_sql_literal(value_for_label(BENCH_MIX[-1][1])))
    return "multiIf(" + ", ".join(branches) + ")"


def _bench_output_fragment(label: str) -> str:
    """The opening of one row's config, carrying that shape's eval output."""
    output = EVAL_SHAPE_PAYLOADS[label]["output"]
    if "output" not in output:
        return '"output":{'
    return '"output":{"output":' + json.dumps(output["output"]) + ","


def _bench_reason_sql() -> str:
    pool = "multiIf(" + ", ".join(
        f"modulo(number, {len(BENCH_REASON_SENTENCES)}) = {i}, "
        f"{_bench_sql_literal(sentence)}"
        for i, sentence in enumerate(BENCH_REASON_SENTENCES[:-1])
    ) + f", {_bench_sql_literal(BENCH_REASON_SENTENCES[-1])})"
    return (
        f"concat(repeat(concat({pool}, ' '), {BENCH_REASON_REPEATS}), "
        f"hex(randomString({BENCH_REASON_RANDOM_BYTES})))"
    )


def _bench_ddl(table: str, shape: str) -> str:
    """Canonical DDL, optionally wound to the shape PeerDB built in production."""
    from tracer.services.clickhouse.schema import (
        CDC_USAGE_APICALLLOG,
        _to_single_node_engine,
    )

    ddl = _to_single_node_engine(CDC_USAGE_APICALLLOG).replace(
        "CREATE TABLE IF NOT EXISTS usage_apicalllog",
        f"CREATE TABLE IF NOT EXISTS {table}",
    )
    if shape != "prod":
        return ddl

    ddl = ddl.replace("PARTITION BY toYYYYMM(created_at)\n", "").replace(
        "ORDER BY (organization_id, source_id, created_at, id)", "ORDER BY id"
    )
    assert "PARTITION BY" not in ddl and "ORDER BY id" in ddl, (
        "the production table shape could not be derived from the canonical DDL"
    )
    return ddl


def _bench_seed(client, table: str, rows: int, shape: str) -> None:
    from tracer.services.clickhouse.schema import EVAL_OUTPUT_JSON_ARGS

    client.command(f"DROP TABLE IF EXISTS {table}")
    client.command(_bench_ddl(table, shape))
    # Wind the expression back to the one a deployed cluster still carries, so
    # every seeded row lands genuinely stale.
    client.command(
        f"ALTER TABLE {table} MODIFY COLUMN eval_score Float64 "
        f"MATERIALIZED JSONExtractFloat({EVAL_OUTPUT_JSON_ARGS})"
    )

    label_expr = _bench_multi_if("bucket", lambda label: label)
    output_expr = _bench_multi_if("bucket", _bench_output_fragment)
    if shape == "prod":
        created_expr = "now64(6) - toIntervalSecond(modulo(number, 15552000))"
    else:
        created_expr = (
            "toDateTime64('2026-01-01 00:00:00', 6) + "
            f"toIntervalSecond(intDiv(number * 15552000, {rows}))"
        )

    # One tenant, as a single customer's table looks, and merges held off until
    # the seed is in so they do not compete with it for memory.
    org = "toUUID('11111111-1111-1111-1111-111111111111')"
    client.command(f"SYSTEM STOP MERGES {table}")

    written = 0
    while written < rows:
        take = min(500_000, rows - written)
        client.command(
            f"""
            INSERT INTO {table}
                (id, log_id, organization_id, workspace_id, status, reference_id,
                 config, source, source_id, created_at, updated_at,
                 _peerdb_synced_at, _peerdb_is_deleted, _peerdb_version)
            SELECT toInt64(number), generateUUIDv4(), {org}, {org}, 'success', label,
                   toJSONString(concat('{{', output, '"reason":"', reason,
                       '"}},"trace_id":"', toString(generateUUIDv4()),
                       '","model":"gpt-4o-mini","latency_ms":',
                       toString(modulo(number, 5000) + 120), '}}')),
                   'tracer', 'bench-template', created, created, created, 0, 1
            FROM (
                SELECT number, modulo(number, {BENCH_BUCKET_SCALE}) AS bucket,
                       {label_expr} AS label,
                       {output_expr} AS output,
                       {_bench_reason_sql()} AS reason,
                       {created_expr} AS created
                FROM numbers({written}, {take})
            )
            SETTINGS {BENCH_SEED_SETTINGS}
            """
        )
        written += take

    client.command(f"SYSTEM START MERGES {table}")
    _bench_settle(client, table)


def _bench_settle(client, table: str, timeout: int = 600) -> None:
    """Let merges consolidate the seed so timings are not merge noise.

    Bounded: a fully merged table takes far longer than the timings are worth,
    and a part count in the low tens already matches a real table's layout.
    """
    deadline = time.time() + timeout
    last, stable = None, 0
    while time.time() < deadline:
        merging = client.query(
            "SELECT count() FROM system.merges "
            f"WHERE database = '{_TEST_DATABASE}' AND table = '{table}'"
        ).result_rows[0][0]
        parts = client.query(
            "SELECT count() FROM system.parts WHERE active "
            f"AND database = '{_TEST_DATABASE}' AND table = '{table}'"
        ).result_rows[0][0]
        if parts <= BENCH_SETTLED_PARTS:
            return
        if merging == 0 and parts == last:
            stable += 1
            if stable >= 3:
                return
        else:
            stable = 0
        last = parts
        time.sleep(5)


@pytest.fixture(scope="session")
def ch_client():
    """Connect to test ClickHouse instance. Skip if unavailable."""
    try:
        import clickhouse_connect
    except ImportError:
        pytest.skip("clickhouse-connect not installed")

    try:
        client = clickhouse_connect.get_client(
            host=os.environ.get("CH_TEST_HOST", "localhost"),
            port=int(os.environ.get("CH_TEST_PORT", "18123")),
        )
        client.command("SELECT 1")
        return client
    except Exception:
        pytest.skip("ClickHouse not available for integration tests")


@pytest.fixture(scope="session")
def ch_schema(ch_client):
    """Initialize ClickHouse schema for tests.

    Creates the test_futureagi database and applies all DDL statements.
    Runs once per test session.

    Uses ``_to_single_node_engine`` so the DDL works on the single-node
    test ClickHouse instance (no Keeper / Replicated engines).
    """
    import clickhouse_connect
    from tracer.services.clickhouse.schema import (
        _to_single_node_engine,
        SCHEMA_DDL_STATEMENTS,
    )

    ch_client.command(f"CREATE DATABASE IF NOT EXISTS {_TEST_DATABASE}")

    # Connect with the test database as default so unqualified table names
    # in DDL (``CREATE TABLE foo``) land in test_futureagi, not ``default``.
    db_client = clickhouse_connect.get_client(
        host=os.environ.get("CH_TEST_HOST", "localhost"),
        port=int(os.environ.get("CH_TEST_PORT", "18123")),
        database=_TEST_DATABASE,
    )

    for name, ddl in SCHEMA_DDL_STATEMENTS:
        # Convert to single-node engines for the test CH instance
        ddl_test = _to_single_node_engine(ddl)
        # Rewrite any explicit ``futureagi.`` references to the test database
        ddl_test = ddl_test.replace("futureagi.", f"{_TEST_DATABASE}.")
        try:
            db_client.command(ddl_test)
        except Exception as exc:
            # Ignore "already exists" errors; propagate others so schema
            # issues surface during test runs instead of hiding silently.
            err_msg = str(exc)
            if "already exists" not in err_msg.lower():
                import warnings
                warnings.warn(f"CH schema DDL failed for {name}: {err_msg[:200]}")

    db_client.close()
    return ch_client


# ---------------------------------------------------------------------------
# Simulation & Dataset fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ch_simulation_data(ch_schema):
    """Insert test simulation call data."""
    client = ch_schema
    test_execution_id = str(uuid.uuid4())
    scenario_id = str(uuid.uuid4())
    agent_version_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    for i in range(5):
        call_id = str(uuid.uuid4())
        call_type = "voice" if i % 2 == 0 else "text"
        call_status = "completed" if i < 4 else "failed"
        duration = 30.0 + i * 10
        score = 0.6 + i * 0.08

        client.command(
            f"""
            INSERT INTO {_TEST_DATABASE}.simulate_call_execution
                (id, test_execution_id, scenario_id, agent_version_id,
                 simulation_call_type, status,
                 duration_seconds, cost_cents, overall_score,
                 message_count, created_at,
                 _peerdb_synced_at, _peerdb_is_deleted, _peerdb_version)
            VALUES
                ('{call_id}', '{test_execution_id}', '{scenario_id}', '{agent_version_id}',
                 '{call_type}', '{call_status}',
                 {duration}, {i * 0.5}, {score},
                 {10 + i}, '{now.strftime("%Y-%m-%d %H:%M:%S")}',
                 now64(), 0, {i + 1})
            """
        )

    yield {
        "client": client,
        "test_execution_id": test_execution_id,
        "scenario_id": scenario_id,
        "agent_version_id": agent_version_id,
    }

    try:
        client.command(f"TRUNCATE TABLE {_TEST_DATABASE}.simulate_call_execution")
    except Exception:
        pass


@pytest.fixture
def ch_dataset_data(ch_schema):
    """Insert test dataset cell data."""
    client = ch_schema
    dataset_id = str(uuid.uuid4())
    row_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    column_id = str(uuid.uuid4())

    for i in range(5):
        cell_id = str(uuid.uuid4())
        prompt_tokens = 50 + i * 10
        completion_tokens = 20 + i * 5
        response_time = 100.0 + i * 50
        cell_status = "completed" if i < 4 else "error"

        client.command(
            f"""
            INSERT INTO {_TEST_DATABASE}.model_hub_cell
                (id, dataset_id, column_id, row_id,
                 prompt_tokens, completion_tokens, response_time, status,
                 created_at,
                 _peerdb_synced_at, _peerdb_is_deleted, _peerdb_version)
            VALUES
                ('{cell_id}', '{dataset_id}', '{column_id}', '{row_id}',
                 {prompt_tokens}, {completion_tokens}, {response_time}, '{cell_status}',
                 '{now.strftime("%Y-%m-%d %H:%M:%S")}',
                 now64(), 0, {i + 1})
            """
        )

    yield {
        "client": client,
        "dataset_id": dataset_id,
        "column_id": column_id,
    }

    try:
        client.command(f"TRUNCATE TABLE {_TEST_DATABASE}.model_hub_cell")
    except Exception:
        pass


@pytest.fixture
def ch_eval_output_rows(ch_schema):
    """Recreate usage_apicalllog from the canonical DDL and seed every shape.

    Dropped first because ``CREATE TABLE IF NOT EXISTS`` would keep a stale
    MATERIALIZED expression from an earlier run.
    """
    from tracer.services.clickhouse.schema import (
        CDC_USAGE_APICALLLOG,
        _to_single_node_engine,
    )

    client = ch_schema
    organization_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())
    all_shapes_source_id = str(uuid.uuid4())
    scored_shapes_source_id = str(uuid.uuid4())

    ddl = _to_single_node_engine(CDC_USAGE_APICALLLOG).replace(
        "CREATE TABLE IF NOT EXISTS usage_apicalllog",
        f"CREATE TABLE IF NOT EXISTS {_EVAL_TABLE}",
    )
    client.command(f"DROP TABLE IF EXISTS {_EVAL_TABLE}")
    client.command(ddl)

    # Backdated so clock skew against the CH container can't push rows past "now".
    seeded_at = "now64(6) - toIntervalHour(1)"

    def _seed(source_id, shapes, first_id):
        for offset, (label, payload, _score, _has_number) in enumerate(shapes):
            # trace_id: eval filters select on eval_trace_id, so it names the row.
            # config is double-encoded (a JSON string holding JSON) in CH.
            config = dict(payload, trace_id=label)
            literal = json.dumps(config).replace("\\", "\\\\").replace("'", "\\'")
            client.command(
                f"""
                INSERT INTO {_EVAL_TABLE}
                    (id, log_id, organization_id, workspace_id, status,
                     reference_id, config, source, source_id,
                     created_at, updated_at,
                     _peerdb_synced_at, _peerdb_is_deleted, _peerdb_version)
                SELECT {first_id + offset}, generateUUIDv4(),
                       toUUID('{organization_id}'), toUUID('{workspace_id}'),
                       'success', '{label}', toJSONString('{literal}'),
                       'tracer', '{source_id}',
                       {seeded_at}, {seeded_at}, {seeded_at}, 0, 1
                """
            )

    _seed(all_shapes_source_id, EVAL_OUTPUT_SHAPES, 1)
    _seed(scored_shapes_source_id, EVAL_SCORED_SHAPES, 101)

    yield {
        "client": client,
        "organization_id": organization_id,
        "workspace_id": workspace_id,
        "all_shapes_source_id": all_shapes_source_id,
        "scored_shapes_source_id": scored_shapes_source_id,
    }

    try:
        client.command(f"TRUNCATE TABLE {_EVAL_TABLE}")
    except Exception:
        pass


# ===========================================================================
# A. TestClickHouseConnection
# ===========================================================================


@pytest.mark.integration
class TestClickHouseConnection:
    """Test basic ClickHouse connectivity and schema management."""

    def test_can_connect_to_clickhouse(self, ch_client):
        """Should be able to execute a simple query."""
        result = ch_client.command("SELECT 1")
        assert result == 1

    def test_schema_initialization(self, ch_schema):
        """Applying DDL should create all expected tables."""
        client = ch_schema
        result = client.query(
            f"SELECT name FROM system.tables WHERE database = '{_TEST_DATABASE}'"
        )
        tables = [row[0] for row in result.result_rows]
        # Core CDC tables should exist
        assert "tracer_observation_span" in tables
        assert "tracer_trace" in tables

    def test_drop_and_recreate_schema(self, ch_client):
        """Should be able to drop and recreate the test database."""
        from tracer.services.clickhouse.schema import (
            _to_single_node_engine,
            get_drop_statements,
            SCHEMA_DDL_STATEMENTS,
        )

        temp_db = "test_futureagi_temp"
        ch_client.command(f"CREATE DATABASE IF NOT EXISTS {temp_db}")

        # Apply schema (single-node engines for test CH)
        for name, ddl in SCHEMA_DDL_STATEMENTS:
            ddl_test = _to_single_node_engine(ddl)
            ddl_test = ddl_test.replace("futureagi.", f"{temp_db}.")
            try:
                ch_client.command(ddl_test)
            except Exception:
                pass

        # Drop using drop statements (rewritten for temp DB)
        for drop_stmt in get_drop_statements():
            drop_stmt = drop_stmt.replace("futureagi.", f"{temp_db}.")
            try:
                ch_client.command(drop_stmt)
            except Exception:
                pass

        # Drop the database itself
        ch_client.command(f"DROP DATABASE IF EXISTS {temp_db}")

        # Verify it's gone
        result = ch_client.command(
            f"SELECT count() FROM system.databases WHERE name = '{temp_db}'"
        )
        assert result == 0


# ===========================================================================
# D. TestSimulationQueryBuilderIntegration
# ===========================================================================


@pytest.mark.integration
class TestSimulationQueryBuilderIntegration:
    """Test SimulationQueryBuilder against a real ClickHouse instance."""

    def _build_config(
        self,
        workspace_id,
        metric_name="duration",
        aggregation="avg",
        preset="30D",
        granularity="day",
        filters=None,
        breakdowns=None,
        **extra,
    ):
        return {
            "source": "simulation",
            "workspace_id": workspace_id,
            "granularity": granularity,
            "time_range": {"preset": preset},
            "metrics": [
                {
                    "id": metric_name,
                    "name": metric_name,
                    "type": "system_metric",
                    "aggregation": aggregation,
                    **extra,
                }
            ],
            "filters": filters or [],
            "breakdowns": breakdowns or [],
        }

    def test_simulation_metric_query_executes(self, ch_simulation_data):
        """Building and executing a simulation duration query should not raise."""
        from tracer.services.clickhouse.query_builders.simulation_dashboard import (
            SimulationQueryBuilder,
        )

        config = self._build_config(ch_simulation_data["test_execution_id"])
        builder = SimulationQueryBuilder(config)
        queries = builder.build_all_queries()
        assert len(queries) == 1

        sql, params, _ = queries[0]
        # Rewrite for test DB
        sql_test = sql.replace("futureagi.", f"{_TEST_DATABASE}.")
        try:
            result = ch_simulation_data["client"].query(sql_test, parameters=params)
            assert isinstance(result.result_rows, list)
        except Exception as e:
            if "UNKNOWN_TABLE" in str(e) or "doesn't exist" in str(e):
                pytest.skip(f"Simulation tables not in test schema: {e}")
            raise

    def test_simulation_breakdown_by_agent_version(self, ch_simulation_data):
        """Breakdown by agent_version should include breakdown_value column."""
        from tracer.services.clickhouse.query_builders.simulation_dashboard import (
            SimulationQueryBuilder,
        )

        config = self._build_config(
            ch_simulation_data["test_execution_id"],
            breakdowns=[{"type": "system_metric", "name": "agent_version"}],
        )
        builder = SimulationQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        assert "breakdown_value" in sql

    def test_simulation_filter_by_call_type(self, ch_simulation_data):
        """Filtering by call_type should produce valid SQL."""
        from tracer.services.clickhouse.query_builders.simulation_dashboard import (
            SimulationQueryBuilder,
        )

        config = self._build_config(
            ch_simulation_data["test_execution_id"],
            filters=[
                {
                    "metric_type": "system_metric",
                    "metric_name": "call_type",
                    "operator": "equal_to",
                    "value": "voice",
                }
            ],
        )
        builder = SimulationQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, params, _ = queries[0]
        assert "call_type" in sql

        sql_test = sql.replace("futureagi.", f"{_TEST_DATABASE}.")
        try:
            result = ch_simulation_data["client"].query(sql_test, parameters=params)
            assert isinstance(result.result_rows, list)
        except Exception as e:
            if "UNKNOWN_TABLE" in str(e) or "doesn't exist" in str(e):
                pytest.skip(f"Simulation tables not in test schema: {e}")
            raise


# ===========================================================================
# E. TestDatasetQueryBuilderIntegration
# ===========================================================================


@pytest.mark.integration
class TestDatasetQueryBuilderIntegration:
    """Test DatasetQueryBuilder against a real ClickHouse instance."""

    def _build_config(
        self,
        workspace_id,
        metric_name="row_count",
        aggregation="count",
        preset="30D",
        granularity="day",
        filters=None,
        breakdowns=None,
        **extra,
    ):
        return {
            "workflow": "dataset",
            "workspace_id": workspace_id,
            "granularity": granularity,
            "time_range": {"preset": preset},
            "metrics": [
                {
                    "id": metric_name,
                    "name": metric_name,
                    "type": "system_metric",
                    "aggregation": aggregation,
                    **extra,
                }
            ],
            "filters": filters or [],
            "breakdowns": breakdowns or [],
        }

    def test_dataset_metric_query_executes(self, ch_dataset_data):
        """Building and executing a dataset row_count query should not raise."""
        from tracer.services.clickhouse.query_builders.dataset_dashboard import (
            DatasetQueryBuilder,
        )

        config = self._build_config(ch_dataset_data["dataset_id"])
        builder = DatasetQueryBuilder(config)
        queries = builder.build_all_queries()
        assert len(queries) == 1

        sql, params, _ = queries[0]
        sql_test = sql.replace("futureagi.", f"{_TEST_DATABASE}.")
        try:
            result = ch_dataset_data["client"].query(sql_test, parameters=params)
            assert isinstance(result.result_rows, list)
        except Exception as e:
            if "UNKNOWN_TABLE" in str(e) or "doesn't exist" in str(e):
                pytest.skip(f"Dataset tables not in test schema: {e}")
            raise

    def test_dataset_breakdown_by_column(self, ch_dataset_data):
        """Breakdown by column_name should include breakdown_value column."""
        from tracer.services.clickhouse.query_builders.dataset_dashboard import (
            DatasetQueryBuilder,
        )

        config = self._build_config(
            ch_dataset_data["dataset_id"],
            breakdowns=[{"type": "system_metric", "name": "column_name"}],
        )
        builder = DatasetQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        assert "breakdown_value" in sql


# ===========================================================================
# F. TestEvalScoreMaterialization
# ===========================================================================


@pytest.mark.integration
class TestEvalScoreMaterialization:
    """Eval scores read back out of a real ClickHouse instance."""

    def test_every_eval_output_shape_materializes_its_score(self, ch_eval_output_rows):
        """Structured outputs resolve to their nested score; the rest hold."""
        result = ch_eval_output_rows["client"].query(
            f"SELECT reference_id, eval_score FROM {_EVAL_TABLE} "
            f"WHERE source_id = '{ch_eval_output_rows['all_shapes_source_id']}'"
        )

        assert dict(result.result_rows) == {
            label: score for label, _payload, score, _has_number in EVAL_OUTPUT_SHAPES
        }

    def test_structured_eval_output_str_holds_the_nested_object(
        self, ch_eval_output_rows
    ):
        """Readers branch on eval_output_str, so it must not be empty here."""
        label, payload, _score, _has_number = EVAL_OUTPUT_SHAPES[0]
        result = ch_eval_output_rows["client"].query(
            f"SELECT eval_output_str FROM {_EVAL_TABLE} "
            f"WHERE source_id = '{ch_eval_output_rows['all_shapes_source_id']}' "
            f"AND reference_id = '{label}'"
        )

        assert json.loads(result.result_rows[0][0]) == payload["output"]["output"]

    @pytest.mark.parametrize(
        ("source_key", "expected"),
        [
            ("scored_shapes_source_id", EVAL_SCORED_AVERAGE),
            ("all_shapes_source_id", EVAL_ALL_SHAPES_AVERAGE),
        ],
    )
    def test_dashboard_average_counts_only_the_shapes_it_can_score(
        self, ch_eval_output_rows, source_key, expected
    ):
        """Structured rows are averaged in; a null or text score stays excluded."""
        from tracer.services.clickhouse.query_builders.dashboard import (
            DashboardQueryBuilder,
        )

        config = {
            "organization_id": ch_eval_output_rows["organization_id"],
            "workspace_id": ch_eval_output_rows["workspace_id"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "structured_eval",
                    "name": "structured_eval",
                    "type": "eval_metric",
                    "config_id": ch_eval_output_rows[source_key],
                    "aggregation": "avg",
                }
            ],
        }
        sql, params, _ = DashboardQueryBuilder(config).build_all_queries()[0]
        sql_test = sql.replace("usage_apicalllog", _EVAL_TABLE)

        result = ch_eval_output_rows["client"].query(sql_test, parameters=params)

        assert [row[1] for row in result.result_rows] == [pytest.approx(expected)]

    def test_pass_fail_paths_classify_every_row_the_same_way(self, ch_eval_output_rows):
        """One eval, three widgets: the time series, the breakdown label and the
        filter have to reach the same verdict on the same row.
        """
        from tracer.services.clickhouse.query_builders.dashboard import (
            DashboardQueryBuilder,
        )

        client = ch_eval_output_rows["client"]
        source_id = ch_eval_output_rows["all_shapes_source_id"]
        metric = {
            "id": "pass_fail_eval",
            "name": "pass_fail_eval",
            "type": "eval_metric",
            "config_id": source_id,
            "output_type": "PASS_FAIL",
            "aggregation": "pass_count",
        }
        config = {
            "organization_id": ch_eval_output_rows["organization_id"],
            "workspace_id": ch_eval_output_rows["workspace_id"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [metric],
            "breakdowns": [metric],
        }

        def _run(sql, params=None):
            return client.query(
                sql.replace("usage_apicalllog", _EVAL_TABLE), parameters=params
            ).result_rows

        builder = DashboardQueryBuilder(config)

        sql, params, _ = builder.build_all_queries()[0]
        time_series_passes = sum(row[1] for row in _run(sql, params))

        label_expr = builder._resolve_all_breakdowns({})[0]["expr"]
        breakdown_passes = {
            label
            for label, verdict in _run(
                f"SELECT reference_id, {label_expr} FROM usage_apicalllog AS ev0 "
                f"WHERE source_id = '{source_id}'"
            )
            if verdict == "Pass"
        }

        clauses, filter_params = builder._build_subquery_filters(
            [
                {
                    "metric_type": "eval_metric",
                    "metric_name": source_id,
                    "output_type": "PASS_FAIL",
                    "operator": "equal_to",
                    "value": 1.0,
                }
            ],
            {},
            "f_",
        )
        subquery = clauses[0][clauses[0].index("(") + 1 : clauses[0].rindex(")")]
        filter_passes = {row[0] for row in _run(subquery, filter_params)}

        assert breakdown_passes == filter_passes == set(EVAL_PASSING_SHAPES), (
            "the breakdown label and the eval filter disagree on which rows "
            f"passed: breakdown {sorted(breakdown_passes)}, "
            f"filter {sorted(filter_passes)}"
        )
        assert time_series_passes == len(EVAL_PASSING_SHAPES), (
            "the time series counts a different number of passes than the "
            f"breakdown and the filter do: {time_series_passes}"
        )

    def test_backfill_leaves_the_score_index_agreeing_with_the_rows(
        self, ch_eval_output_rows
    ):
        """A backfilled row has to stay visible to a filter on eval_score."""
        from tracer.management.commands.backfill_eval_score import (
            materialize_statements,
            rebuild_statements,
        )
        from tracer.services.clickhouse.schema import (
            CDC_USAGE_APICALLLOG,
            EVAL_OUTPUT_JSON_ARGS,
            _to_single_node_engine,
        )

        client = ch_eval_output_rows["client"]
        table = f"{_EVAL_TABLE}_deployed"
        client.command(f"DROP TABLE IF EXISTS {table}")
        client.command(
            _to_single_node_engine(CDC_USAGE_APICALLLOG).replace(
                "CREATE TABLE IF NOT EXISTS usage_apicalllog",
                f"CREATE TABLE IF NOT EXISTS {table}",
            )
        )
        # Wind the table back to the expression a deployed cluster still has.
        client.command(
            f"ALTER TABLE {table} MODIFY COLUMN eval_score Float64 "
            f"MATERIALIZED JSONExtractFloat({EVAL_OUTPUT_JSON_ARGS})"
        )
        for offset, (_label, payload, _score, _has_number) in enumerate(
            EVAL_SCORED_SHAPES
        ):
            literal = json.dumps(payload).replace("\\", "\\\\").replace("'", "\\'")
            client.command(
                f"INSERT INTO {table} (id, log_id, organization_id, status, config, "
                "source, source_id, created_at, updated_at, _peerdb_synced_at, "
                "_peerdb_is_deleted, _peerdb_version) SELECT "
                f"{201 + offset}, generateUUIDv4(), generateUUIDv4(), 'success', "
                f"toJSONString('{literal}'), 'tracer', 'deployed', "
                "now64(6), now64(6), now64(6), 0, 1"
            )

        for statement in rebuild_statements(table) + materialize_statements(table):
            client.command(f"{statement} SETTINGS mutations_sync = 2")

        expected = sum(1 for _l, _p, score, _h in EVAL_SCORED_SHAPES if score >= 1.0)
        counts = {
            skip_indexes: client.query(
                f"SELECT count() FROM {table} WHERE eval_score >= 1.0 "
                f"SETTINGS use_skip_indexes = {skip_indexes}"
            ).result_rows[0][0]
            for skip_indexes in (0, 1)
        }

        client.command(f"DROP TABLE IF EXISTS {table}")
        assert counts[1] == counts[0] == expected, (
            "the eval_score minmax index disagrees with the stored rows after "
            f"the backfill, so filters prune real rows away: {counts}"
        )


# ===========================================================================
# F. TestEvalScoreBackfillAtScale
# ===========================================================================


@pytest.fixture
def ch_backfill_bench_table(ch_schema):
    """Seed a large usage_apicalllog copy, or skip when the run is not opted in."""
    rows = int(os.environ.get("EVAL_SCORE_BENCH_ROWS", "0"))
    if rows <= 0:
        pytest.skip(
            "set EVAL_SCORE_BENCH_ROWS to run the eval_score backfill benchmark"
        )

    import clickhouse_connect

    shape = os.environ.get("EVAL_SCORE_BENCH_SHAPE", "prod")
    client = clickhouse_connect.get_client(
        host=os.environ.get("CH_TEST_HOST", "localhost"),
        port=int(os.environ.get("CH_TEST_PORT", "18123")),
        database=_TEST_DATABASE,
        send_receive_timeout=14400,
        settings={"max_execution_time": 14400},
    )

    started = time.perf_counter()
    _bench_seed(client, _BENCH_TABLE, rows, shape)

    yield {
        "client": client,
        "table": _BENCH_TABLE,
        "rows": rows,
        "shape": shape,
        "seed_seconds": time.perf_counter() - started,
    }

    client.command(f"DROP TABLE IF EXISTS {_BENCH_TABLE}")
    client.close()


@pytest.mark.benchmark
@pytest.mark.skipif(
    not os.environ.get("EVAL_SCORE_BENCH_ROWS"),
    reason="set EVAL_SCORE_BENCH_ROWS to run the eval_score backfill benchmark",
)
class TestEvalScoreBackfillAtScale:
    """Time the eval_score backfill on a large table and prove it stays correct.

    Opt-in twice over: the ``benchmark`` marker is deselected by the default
    addopts, and the fixture skips unless ``EVAL_SCORE_BENCH_ROWS`` is set. A
    ten million row seed must never run in an ordinary suite invocation.

        EVAL_SCORE_BENCH_ROWS=10000000 pytest
            tracer/tests/test_clickhouse_integration.py -m benchmark -k AtScale -s

    ``EVAL_SCORE_BENCH_SHAPE`` picks the table shape: ``prod`` (default,
    unpartitioned and ordered by id, which is what PeerDB built) or
    ``canonical`` (the schema.py DDL, partitioned by month).
    """

    @staticmethod
    def _timed(call):
        started = time.perf_counter()
        result = call()
        return time.perf_counter() - started, result

    def _stale_count(self, client, table):
        from tracer.management.commands.backfill_eval_score import _AFFECTED_COUNT
        from tracer.services.clickhouse.eval_expressions import (
            eval_has_structured_score,
        )
        from tracer.services.clickhouse.schema import (
            CH_EVAL_SCORE_EXPR,
            EVAL_OUTPUT_JSON_ARGS,
        )

        sql = _AFFECTED_COUNT.format(
            table=table,
            column="eval_score",
            expr=CH_EVAL_SCORE_EXPR,
            predicate=eval_has_structured_score(EVAL_OUTPUT_JSON_ARGS),
        )
        seconds, result = self._timed(
            lambda: client.query(f"{sql}\nSETTINGS {BENCH_READ_SETTINGS}")
        )
        return seconds, int(result.result_rows[0][0])

    def test_backfill_scales_and_leaves_every_shape_correct(
        self, ch_backfill_bench_table
    ):
        """The real backfill statements, timed, against a table of real size."""
        from tracer.management.commands.backfill_eval_score import (
            _PARTITIONS,
            materialize_statements,
            rebuild_statements,
        )

        client = ch_backfill_bench_table["client"]
        table = ch_backfill_bench_table["table"]
        rows = ch_backfill_bench_table["rows"]

        size = client.query(
            "SELECT sum(rows), sum(bytes_on_disk), count() FROM system.parts "
            f"WHERE database = '{_TEST_DATABASE}' AND table = '{table}' AND active"
        ).result_rows[0]

        dry_run_cold, stale_before = self._stale_count(client, table)
        assert stale_before > 0, (
            "the seed is not stale, so this run would time a no-op backfill"
        )

        rebuild_seconds = 0.0
        for statement in rebuild_statements(table):
            seconds, _ = self._timed(lambda s=statement: client.command(s))
            rebuild_seconds += seconds

        partitions = [
            row[0] for row in client.query(_PARTITIONS.format(table=table)).result_rows
        ]
        assert partitions, "the backfill found no partitions to materialize"

        column_seconds = index_seconds = 0.0
        for partition in partitions:
            column_sql, index_sql = materialize_statements(table, partition)
            # mutations_sync = 2 so the wall time is completion, not submission.
            seconds, _ = self._timed(
                lambda s=column_sql: client.command(f"{s} SETTINGS mutations_sync = 2")
            )
            column_seconds += seconds
            seconds, _ = self._timed(
                lambda s=index_sql: client.command(f"{s} SETTINGS mutations_sync = 2")
            )
            index_seconds += seconds

        dry_run_after, stale_after = self._stale_count(client, table)

        parity = {
            use_skip_indexes: int(
                client.query(
                    f"SELECT count() FROM {table} WHERE eval_score >= 1.0 "
                    f"SETTINGS use_skip_indexes = {use_skip_indexes}, "
                    f"{BENCH_READ_SETTINGS}"
                ).result_rows[0][0]
            )
            for use_skip_indexes in (0, 1)
        }
        scores = dict(
            client.query(
                f"SELECT reference_id, eval_score FROM {table} "
                f"GROUP BY reference_id, eval_score SETTINGS {BENCH_READ_SETTINGS}"
            ).result_rows
        )

        print(
            "\n".join(
                [
                    "",
                    f"eval_score backfill, {rows:,} rows, "
                    f"{ch_backfill_bench_table['shape']} table shape",
                    f"  rows on disk           {size[0]:,} in {size[2]} active parts",
                    f"  bytes on disk          {size[1]:,} "
                    f"({size[1] / max(size[0], 1):.0f} per row)",
                    f"  seed                   {ch_backfill_bench_table['seed_seconds']:8.2f}s",
                    f"  dry-run scan           {dry_run_cold:8.2f}s  ({stale_before:,} stale)",
                    f"  rebuild DDL, 3 stmts   {rebuild_seconds:8.2f}s",
                    f"  MATERIALIZE COLUMN     {column_seconds:8.2f}s  "
                    f"over {len(partitions)} partition(s)",
                    f"  MATERIALIZE INDEX      {index_seconds:8.2f}s",
                    f"  dry-run scan after     {dry_run_after:8.2f}s  ({stale_after:,} stale)",
                    "",
                ]
            )
        )

        assert stale_after == 0, (
            f"{stale_after} rows are still stale after the backfill"
        )
        assert parity[1] == parity[0], (
            "the eval_score minmax index disagrees with the stored rows after "
            f"the backfill, so filters prune real rows away: {parity}"
        )
        assert scores == {
            label: EVAL_SHAPE_SCORES[label] for _bound, label in BENCH_MIX
        }, f"a seeded output shape resolved to the wrong score: {scores}"
