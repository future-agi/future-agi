"""RT-05/10 cloud-only raw CDC regression; disposable CH sidecar only.

Uses the repository's explicit test-native-client guard. Never run locally.
No deployed tables, materialized views or migrations are changed.
"""

from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest

from conftest import _ch_test_native_client
from tracer.services.clickhouse.query_builders.eval_metrics import (
    EvalMetricsQueryBuilder,
)
from tracer.services.clickhouse.v2.query_builders.eval_metrics import (
    EvalMetricsQueryBuilderV2,
)

pytestmark = pytest.mark.integration
START = datetime(2026, 10, 1)
PROJECT, OTHER, CONFIG = (str(UUID(int=i)) for i in (11, 12, 1))


@pytest.fixture
def raw_metrics(monkeypatch):
    token = uuid4().hex[:12]
    table, traces, sessions = (
        f"_nk20_{name}_{token}" for name in ("eval", "trace", "session")
    )
    with _ch_test_native_client() as client:
        database = client.execute("SELECT currentDatabase()")[0][0]
        client.execute(f"""CREATE TABLE {table} (
            id UUID, custom_eval_config_id UUID, created_at DateTime,
            trace_id Nullable(UUID), trace_session_id Nullable(UUID), target_type String,
            output_float Nullable(Float64), output_bool Nullable(UInt8), output_str Nullable(String),
            output_str_list String, error UInt8, status String, skipped_reason Nullable(String),
            deleted UInt8, _peerdb_is_deleted UInt8, _peerdb_version UInt64
        ) ENGINE=ReplacingMergeTree(_peerdb_version) ORDER BY (custom_eval_config_id, created_at, id)""")
        created = []
        try:
            for dimension in (traces, sessions):
                client.execute(
                    f"CREATE TABLE {dimension} (id UUID, project_id UUID) ENGINE=Memory"
                )
                created.append(("TABLE", dimension))
                client.execute(
                    f"INSERT INTO {dimension} VALUES",
                    [(UUID(int=21), UUID(PROJECT)), (UUID(int=22), UUID(OTHER))],
                )
                client.execute(f"""CREATE DICTIONARY {dimension}_dict (id UUID, project_id UUID)
                    PRIMARY KEY id SOURCE(CLICKHOUSE(DB '{database}' TABLE '{dimension}'))
                    LAYOUT(COMPLEX_KEY_HASHED()) LIFETIME(0)""")
                created.append(("DICTIONARY", dimension + "_dict"))
            original = EvalMetricsQueryBuilder._project_predicate
            monkeypatch.setattr(
                EvalMetricsQueryBuilder,
                "_project_predicate",
                lambda self: (
                    original(self)
                    .replace("trace_session_dict", sessions + "_dict")
                    .replace("trace_dict", traces + "_dict")
                ),
            )
            monkeypatch.setattr(
                EvalMetricsQueryBuilder,
                "_EVAL_LOGGER_SOURCE",
                staticmethod(
                    lambda alias, **kwargs: (
                        table,
                        f"{alias}._peerdb_is_deleted = 0 AND {alias}.deleted = 0",
                    )
                ),
            )

            def insert(
                *,
                row_id=None,
                version=1,
                score=None,
                boolean=None,
                choices="[]",
                text=None,
                error=0,
                status="completed",
                skipped=None,
                deleted=0,
                tombstone=0,
                foreign=False,
                session=False,
                hour=0,
            ):
                client.execute(
                    f"INSERT INTO {table} VALUES",
                    [
                        (
                            row_id or uuid4(),
                            UUID(CONFIG),
                            START + timedelta(hours=hour),
                            None if session else UUID(int=22 if foreign else 21),
                            UUID(int=22 if foreign else 21) if session else None,
                            "session" if session else "trace",
                            score,
                            boolean,
                            text,
                            choices,
                            error,
                            status,
                            skipped,
                            deleted,
                            tombstone,
                            version,
                        )
                    ],
                )

            yield client, insert
        finally:
            for kind, name in reversed(created):
                client.execute(f"DROP {kind} IF EXISTS {name}")
            client.execute(f"DROP TABLE IF EXISTS {table}")


@pytest.mark.parametrize("kind", ["SCORE", "PASS_FAIL", "CHOICES"])
def test_rt05_latest_error_tombstones_repeated_cdc_and_tenant_scope(raw_metrics, kind):
    client, insert = raw_metrics
    # A valid zero and valid one: both are in the quality population.
    insert(score=0, boolean=0, choices='["no"]')
    repeated = uuid4()
    insert(row_id=repeated, score=1, boolean=1, choices='["yes"]', session=True)
    insert(
        row_id=repeated, version=2, score=1, boolean=1, choices='["yes"]', session=True
    )
    # A newer error must retract an older success, including its kept output.
    changed = uuid4()
    insert(row_id=changed, score=1, boolean=1, choices='["yes"]')
    insert(
        row_id=changed,
        version=2,
        score=0,
        boolean=1,
        choices='["yes"]',
        error=1,
        text="ERROR",
    )
    for marker in ("deleted", "tombstone"):
        removed = uuid4()
        insert(row_id=removed, score=1, boolean=1, choices='["yes"]')
        insert(
            row_id=removed,
            version=2,
            score=1,
            boolean=1,
            choices='["yes"]',
            **{marker: 1},
        )
    for status in ("pending", "running", "skipped"):
        insert(score=1, boolean=1, choices='["yes"]', status=status)
    insert(score=1, boolean=1, choices='["yes"]', skipped="missing input")
    insert(error=1, text="ERROR")
    insert(status="rate_limited")
    insert(text="not numeric")
    insert(score=1, boolean=1, choices='["yes"]', foreign=True)
    insert(score=1, boolean=1, choices='["yes"]', foreign=True, session=True)
    insert(score=1, boolean=1, choices='["yes"]', hour=3)
    builder = EvalMetricsQueryBuilder(
        CONFIG,
        PROJECT,
        START,
        START + timedelta(hours=2),
        eval_output_type=kind,
        choices=["yes", "no"],
    )
    query, params = builder.build()
    rows, columns = client.execute(query, params, with_column_types=True)
    result = dict(zip([name for name, _ in columns], rows[0], strict=True))
    assert result["choice_0" if kind == "CHOICES" else "value"] == 50
    assert result["scored_count"] == 2
    assert result["error_count"] == 3
    assert result["pending_count"] == result["running_count"] == 1
    assert result["skipped_count"] == 2
    assert result["count"] == 10
    assert result["error_rate"] == 50


@pytest.mark.parametrize("kind", ["SCORE", "PASS_FAIL", "CHOICES"])
def test_rt05_all_errors_are_null_even_with_retained_results(raw_metrics, kind):
    client, insert = raw_metrics
    for boolean in (None, 0, 1):
        insert(score=0, boolean=boolean, choices='["yes"]', error=1, text="ERROR")
    builder = EvalMetricsQueryBuilder(
        CONFIG,
        PROJECT,
        START,
        START + timedelta(hours=1),
        eval_output_type=kind,
        choices=["yes"],
    )
    query, params = builder.build()
    rows, columns = client.execute(query, params, with_column_types=True)
    result = dict(zip([name for name, _ in columns], rows[0], strict=True))
    assert result["choice_0" if kind == "CHOICES" else "value"] is None
    assert result["scored_count"] == result["failed_count"] == 0
    assert result["error_count"] == 3


@pytest.mark.parametrize("cls", [EvalMetricsQueryBuilder, EvalMetricsQueryBuilderV2])
@pytest.mark.parametrize("representation", ["scalar", "list"])
@pytest.mark.parametrize(
    "values,expected",
    [pytest.param(["yes", "no"], 50, id="mixed"), pytest.param(["no"], 0, id="zero")],
)
def test_rt05_selected_choice_uses_full_valid_population(
    raw_metrics, monkeypatch, cls, representation, values, expected
):
    client, insert = raw_metrics
    # The V2 subclass declares its own source callback. Keep both builders on
    # this fixture's disposable raw table while retaining the real V2 rewrite.
    monkeypatch.setattr(
        EvalMetricsQueryBuilderV2,
        "_EVAL_LOGGER_SOURCE",
        staticmethod(EvalMetricsQueryBuilder._EVAL_LOGGER_SOURCE),
    )
    for value in values:
        if representation == "scalar":
            insert(text=value)
        else:
            insert(choices=f'["{value}"]')
    obj = cls(
        CONFIG,
        PROJECT,
        START,
        START + timedelta(hours=1),
        eval_output_type="CHOICES",
        choices=["yes"],
        valid_choices=["yes", "no"],
    )
    query, params = obj.build()
    rows, columns = client.execute(query, params, with_column_types=True)
    names = [name for name, _ in columns]
    result = dict(zip(names, rows[0], strict=True))
    assert result["choice_0"] == expected
    assert result["scored_count"] == len(values)
    assert result["unscored_count"] == 0
    assert "choice_1" not in names
    series = obj.format_result(rows, names)
    assert len(series) == 1
    assert series[0]["data"][0]["value"] == expected
