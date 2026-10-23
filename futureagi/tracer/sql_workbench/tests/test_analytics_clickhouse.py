"""Live analytics on seeded ClickHouse: window functions over scoped sources.

Two tenants get data of the same shape; the foreign one has far larger costs,
latencies and token counts and its own identifiers, so any foreign row would
change a result. Every query runs through the production path: the compiler,
then ``runner.run`` on an ephemeral locked service principal with the
server-side ``additional_table_filters``. Every expected value is computed in
Python from the owned seed rows, never read back from ClickHouse first.
"""

import importlib.util
import ipaddress
import os
import secrets
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from tracer.sql_workbench.catalog import COLUMNS, Scope, additional_table_filters
from tracer.sql_workbench.compiler import compile_query

pytestmark = pytest.mark.integration


def _sibling(name):
    path = Path(__file__).with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"_sql_analytics_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DERIVED_METRICS_TABLE = ", ".join(
    f"{column} {kind}".replace("DateTime64", "DateTime64(6, 'UTC')")
    for column, kind in COLUMNS["derived_metrics"].items()
)


@dataclass
class Span:
    id: str
    trace: UUID
    session: UUID
    user: UUID
    model: str
    provider: str
    start: datetime
    latency: int
    status: str
    cost: float
    tokens: int
    locale: str
    attrs_string: dict = field(default_factory=dict)
    attrs_number: dict = field(default_factory=dict)


@dataclass
class Tenant:
    org: UUID
    workspace: UUID
    project: UUID
    configs: dict
    label: UUID
    metric: UUID
    users: list = field(default_factory=list)
    sessions: list = field(default_factory=list)
    traces: list = field(default_factory=list)
    spans: list = field(default_factory=list)
    evals: list = field(default_factory=list)
    annotations: list = field(default_factory=list)
    metrics: list = field(default_factory=list)


# (trace index, model, provider, day, minute, latency ms, status, cost, tokens)
SPAN_ROWS = [
    (0, "m-a", "openai", 0, 0, 100, "OK", 0.010, 100),
    (0, "m-b", "anthropic", 0, 5, 250, "OK", 0.020, 200),
    (1, "m-a", "openai", 0, 30, 400, "ERROR", 0.000, 0),
    (1, "m-a", "openai", 0, 35, 150, "OK", 0.015, 150),
    (2, "m-b", "anthropic", 1, 0, 300, "OK", 0.030, 300),
    (2, "m-c", "google", 1, 10, 900, "ERROR", 0.005, 50),
    (2, "m-b", "anthropic", 1, 20, 350, "OK", 0.025, 250),
    (3, "m-a", "openai", 1, 40, 120, "OK", 0.012, 120),
    (3, "m-a", "openai", 1, 45, 120, "OK", 0.013, 130),
    (3, "m-c", "google", 1, 50, 1200, "OK", 0.040, 400),
    (4, "m-b", "anthropic", 2, 0, 200, "OK", 0.022, 220),
    (4, "m-b", "anthropic", 2, 5, 220, "ERROR", 0.000, 0),
    (4, "m-a", "openai", 2, 10, 110, "OK", 0.011, 110),
    (5, "m-c", "google", 2, 20, 1500, "OK", 0.050, 500),
    (5, "m-a", "openai", 2, 25, 140, "OK", 0.014, 140),
    (5, "m-b", "anthropic", 2, 30, 260, "OK", 0.026, 260),
]
# session index per trace, user index and locale per session
TRACE_SESSION = [0, 0, 1, 2, 3, 3]
SESSION_USER_LOCALE = [(0, "en"), (0, "fr"), (1, "en"), (2, "de")]
USER_FIRST_DAY = [0, 0, 1]


def build_tenant(base, *, scale, prefix):
    """One tenant's rows. ``scale`` > 1 makes every numeric value distinctive."""
    tenant = Tenant(
        org=uuid4(),
        workspace=uuid4(),
        project=uuid4(),
        configs={"toxicity": uuid4(), "helpfulness": uuid4()},
        label=uuid4(),
        metric=uuid4(),
    )
    days = [base - timedelta(days=2 - day) for day in range(3)]
    tenant.users = [
        (uuid4(), f"{prefix}-user-{index}", days[day] + timedelta(minutes=index))
        for index, day in enumerate(USER_FIRST_DAY)
    ]
    sessions = [uuid4() for _ in SESSION_USER_LOCALE]
    tenant.traces = [(uuid4(), sessions[session]) for session in TRACE_SESSION]
    for index, row in enumerate(SPAN_ROWS):
        trace, model, provider, day, minute, latency, status, cost, tokens = row
        session_index = TRACE_SESSION[trace]
        user_index, locale = SESSION_USER_LOCALE[session_index]
        attrs_string = {"locale": locale}
        if index in (0, 4, 10):
            tool = "search" if index != 4 else "calc"
            attrs_string["tool_args"] = f'{{"tool":"{tool}","k":{index + 1}}}'
        tenant.spans.append(
            Span(
                id=f"{prefix}-span-{index:02d}",
                trace=tenant.traces[trace][0],
                session=sessions[session_index],
                user=tenant.users[user_index][0],
                model=model,
                provider=provider,
                start=days[day] + timedelta(minutes=minute),
                latency=latency * scale,
                status=status,
                cost=round(cost * scale, 6),
                tokens=tokens * scale,
                locale=locale,
                attrs_string=attrs_string,
                attrs_number={"ttft_ms": float(latency * scale // 4)},
            )
        )
    first_seen = {}
    for span in tenant.spans:
        first_seen[span.session] = min(
            first_seen.get(span.session, span.start), span.start
        )
    tenant.sessions = [
        (session, f"{prefix}-session-{index}", first_seen[session], locale)
        for index, (session, (_, locale)) in enumerate(
            zip(sessions, SESSION_USER_LOCALE, strict=True)
        )
    ]
    # Span-targeted toxicity results (sampled, with inclusion probabilities)
    # and session-targeted helpfulness results (unsampled: no probability).
    for index, score, probability in [
        (0, 0.1, 0.5),
        (1, 0.3, 0.5),
        (4, 0.2, 0.25),
        (7, 0.05, 0.5),
        (10, 0.4, 1.0),
        (13, 0.6, 1.0),
    ]:
        span = tenant.spans[index]
        tenant.evals.append(
            {
                "id": uuid4(),
                "config": "toxicity",
                "target": "span",
                "span": span.id,
                "trace": span.trace,
                "session": span.session,
                "score": score,
                "passed": int(score < 0.5),
                "created": span.start + timedelta(minutes=1),
                "probability": probability,
                "stratum": span.locale,
            }
        )
    for index, score in enumerate([0.9, 0.7, 0.8, 0.4]):
        session, _, seen, locale = tenant.sessions[index]
        tenant.evals.append(
            {
                "id": uuid4(),
                "config": "helpfulness",
                "target": "session",
                "span": None,
                "trace": None,
                "session": session,
                "score": score,
                "passed": int(score >= 0.5),
                "created": seen + timedelta(minutes=2),
                "probability": None,
                "stratum": None,
            }
        )
    for index, value in [
        (0, "good"),
        (1, "good"),
        (4, "bad"),
        (7, "good"),
        (10, "bad"),
        (13, "bad"),
    ]:
        span = tenant.spans[index]
        tenant.annotations.append(
            {
                "id": uuid4(),
                "span": span.id,
                "trace": span.trace,
                "session": span.session,
                "value": value,
                "created": span.start + timedelta(minutes=3),
            }
        )
    for day, value in enumerate([0.8, 0.75, 0.9]):
        tenant.metrics.append(
            {
                "window_start": days[day] - timedelta(minutes=30),
                "window_end": days[day] + timedelta(minutes=30),
                "value": value * scale,
                "numerator": value * scale * 10,
                "denominator": 10.0,
            }
        )
    return tenant


def insert_tenant(client, database, tenant, eval_table):
    import json

    client.execute(
        f"INSERT INTO {database}.end_users (project_id,organization_id,end_user_id,user_id,first_seen) VALUES",
        [
            (tenant.project, tenant.org, user, external, seen)
            for user, external, seen in tenant.users
        ],
    )
    client.execute(
        f"INSERT INTO {database}.trace_sessions (project_id,trace_session_id,external_session_id,first_seen) VALUES",
        [
            (tenant.project, session, external, seen)
            for session, external, seen, _ in tenant.sessions
        ],
    )
    starts = {}
    for span in tenant.spans:
        starts[span.trace] = min(starts.get(span.trace, span.start), span.start)
    client.execute(
        f"INSERT INTO {database}.traces (project_id,id,session_id,name,created_at) VALUES",
        [
            (tenant.project, trace, session, f"trace-{index}", starts[trace])
            for index, (trace, session) in enumerate(tenant.traces)
        ],
    )
    client.execute(
        f"INSERT INTO {database}.spans (project_id,org_id,id,trace_id,trace_session_id,end_user_id,name,start_time,end_time,latency_ms,status,model,provider,total_tokens,cost,attrs_string,attrs_number) VALUES",
        [
            (
                tenant.project,
                tenant.org,
                span.id,
                str(span.trace),
                span.session,
                span.user,
                f"llm-{span.model}",
                span.start,
                span.start + timedelta(milliseconds=span.latency),
                span.latency,
                span.status,
                span.model,
                span.provider,
                span.tokens,
                span.cost,
                span.attrs_string,
                span.attrs_number,
            )
            for span in tenant.spans
        ],
    )
    rows = []
    for item in tenant.evals:
        metadata = {}
        if item["probability"] is not None:
            metadata = {
                "_task_selection": {
                    "sampling": {
                        "stratum": item["stratum"],
                        "inclusion_probability": item["probability"],
                        "population_size": 16,
                    }
                }
            }
        rows.append(
            (
                item["id"],
                item["trace"],
                item["span"],
                item["session"],
                tenant.configs[item["config"]],
                item["target"],
                item["score"],
                item["passed"],
                item["created"],
                json.dumps(metadata),
            )
        )
    client.execute(
        f"INSERT INTO {database}.{eval_table} (id,trace_id,observation_span_id,trace_session_id,custom_eval_config_id,target_type,output_float,output_bool,created_at,output_metadata) VALUES",
        rows,
    )
    client.execute(
        f"INSERT INTO {database}.model_hub_score (id,organization_id,workspace_id,tracer_project_id,trace_id,observation_span_id,trace_session_id,label_id,value,created_at,score_source) VALUES",
        [
            (
                item["id"],
                tenant.org,
                tenant.workspace,
                tenant.project,
                item["trace"],
                item["span"],
                item["session"],
                tenant.label,
                item["value"],
                item["created"],
                "human",
            )
            for item in tenant.annotations
        ],
    )
    client.execute(
        f"INSERT INTO {database}.derived_metrics ({', '.join(COLUMNS['derived_metrics'])}) VALUES",
        [
            (
                tenant.org,
                tenant.workspace,
                tenant.project,
                tenant.metric,
                1,
                uuid4(),
                uuid4(),
                "day",
                "",
                item["value"],
                item["numerator"],
                item["denominator"],
                "ok",
                10,
                10,
                0,
                0,
                item["window_start"],
                item["window_end"],
                item["window_end"],
                "",
                "hash",
                "all",
            )
            for item in tenant.metrics
        ],
    )


@dataclass
class Live:
    owned: Tenant
    foreign: Tenant
    scope: Scope

    def run(self, sql, scope=None):
        from tracer.sql_workbench.runner import run

        scope = scope or self.scope
        return run(
            compile_query(sql, scope),
            uuid4(),
            lambda: False,
            table_filters=additional_table_filters(scope),
        )

    def rows(self, sql):
        return self.run(sql)["rows"]

    def by_first(self, sql):
        return {row[0]: tuple(row[1:]) for row in self.rows(sql)}


# Sort keys of the small security fixture, and the leading key columns of the
# production CH25 schema (tracer/services/clickhouse/v2/schema/*.sql: spans
# lead with project_id, then columns this fixture does not have; annotations
# keep the legacy label-first key), which the estimate's tenant-key rule reads.
FIXTURE_KEYS = {
    "spans": "(project_id, id)",
    "trace_sessions": "trace_session_id",
    "end_users": "end_user_id",
}
PRODUCTION_KEYS = {
    "spans": "(project_id, id) PARTITION BY toDate(start_time)",
    "traces": "(project_id, id) PARTITION BY toYYYYMM(created_at)",
    "trace_sessions": "(project_id, trace_session_id)",
    "end_users": "(project_id, end_user_id)",
    "tracer_eval_logger_v2": (
        "(custom_eval_config_id, created_at, id) PARTITION BY toYYYYMM(created_at)"
    ),
    "model_hub_score": "(label_id, created_at, id)",
}


@contextmanager
def provisioned(keys=None, *, label="an"):
    """Two seeded tenants in a fresh database, read by an ephemeral locked
    service principal exactly as ``provision_sql_service`` creates it."""
    from django.test import override_settings

    from conftest import _ch_test_native_client
    from tracer.sql_workbench.provision import service_access_ddl

    keys = FIXTURE_KEYS if keys is None else keys
    tables = _sibling("test_clickhouse").TABLES
    assigned = os.environ.get("SQL_WORKBENCH_TEST_DATABASE") or "test_sql"
    database = f"{assigned}_{label}_{uuid4().hex[:10]}"
    eval_table = "tracer_eval_logger_v2"
    base = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    base -= timedelta(hours=3)
    with _ch_test_native_client(owned_database=database) as admin:
        admin.execute(f"CREATE DATABASE {database}")
        principal = None
        try:
            for table, columns in tables.items():
                admin.execute(
                    f"CREATE TABLE {database}.{table} ({columns}) ENGINE=ReplacingMergeTree(_version) ORDER BY {keys.get(table, 'id')}"
                )
            admin.execute(
                f"CREATE TABLE {database}.derived_metrics ({DERIVED_METRICS_TABLE}) ENGINE=MergeTree ORDER BY (project_id, metric_id, window_start)"
            )
            owned = build_tenant(base, scale=1, prefix="own")
            foreign = build_tenant(base, scale=1000, prefix="foreign")
            for tenant in (owned, foreign):
                insert_tenant(admin, database, tenant, eval_table)
            scope = Scope(
                owned.org,
                owned.workspace,
                owned.project,
                base - timedelta(days=3),
                base + timedelta(hours=1),
                tuple(owned.configs.values()),
                eval_table,
                database,
                True,
            )
            address = ipaddress.ip_address(
                str(
                    admin.execute(
                        "SELECT address FROM system.processes WHERE query_id=currentQueryID()"
                    )[0][0]
                )
            )
            address = getattr(address, "ipv4_mapped", None) or address
            name = f"sql_test_{uuid4().hex[:12]}"
            password = secrets.token_urlsafe(32)
            for index, statement in enumerate(
                service_access_ddl(
                    database=database,
                    principal=name,
                    password=password,
                    cidr=f"{address}/{32 if address.version == 4 else 128}",
                    eval_table=eval_table,
                    include_derived_metrics=True,
                )
            ):
                admin.execute(statement)
                if index == 0:
                    principal = name
            with (
                pytest.MonkeyPatch.context() as patch,
                override_settings(
                    CLICKHOUSE_V2={
                        "CH25_HOST": admin.connection.host,
                        "CH25_TCP_PORT": admin.connection.port,
                        "CH25_DATABASE": database,
                        "CH25_USER": admin.connection.user,
                    }
                ),
            ):
                patch.setenv("SQL_WORKBENCH_CH_USER", principal)
                patch.setenv("SQL_WORKBENCH_CH_PASSWORD", password)
                patch.setenv("SQL_WORKBENCH_CH_SECURE", "false")
                yield Live(owned, foreign, scope), admin
        finally:
            if principal:
                admin.execute(f"DROP USER {principal}")
            admin.execute(f"DROP DATABASE {database} SYNC")


@pytest.fixture(scope="module")
def _live_and_admin():
    with provisioned() as pair:
        yield pair


@pytest.fixture(scope="module")
def live(_live_and_admin):
    return _live_and_admin[0]


@pytest.fixture(scope="module")
def live_admin(_live_and_admin):
    return _live_and_admin[1]


# ---------------------------------------------------------------------------
# Python oracles (standard SQL semantics) over the owned seed rows.


def _partitions(items, key):
    groups = defaultdict(list)
    for item in items:
        groups[key(item)].append(item)
    return groups


def _ranks(items, value):
    """Standard rank, dense_rank, percent_rank and cume_dist by ``value``."""
    ordered = sorted(items, key=value)
    values = [value(item) for item in ordered]
    distinct = sorted(set(values))
    n = len(ordered)
    result = {}
    for item in ordered:
        v = value(item)
        rank = values.index(v) + 1
        peers_through = max(i for i, x in enumerate(values) if x == v) + 1
        result[item.id] = (
            rank,
            distinct.index(v) + 1,
            (rank - 1) / (n - 1) if n > 1 else 0.0,
            peers_through / n,
        )
    return result


def test_ranking_windows_match_standard_semantics(live):
    spans = live.owned.spans
    # Ties: two m-a spans share latency 120, so rank and dense_rank differ.
    rows = live.by_first(
        "SELECT id, rank() OVER (ORDER BY latency_ms) AS r, "
        "dense_rank() OVER (ORDER BY latency_ms) AS d, "
        "percent_rank() OVER (ORDER BY latency_ms) AS p FROM spans"
    )
    expected = _ranks(spans, lambda s: s.latency)
    assert set(rows) == {span.id for span in spans}
    for span_id, (rank, dense, percent) in rows.items():
        assert (rank, dense) == expected[span_id][:2]
        assert percent == pytest.approx(expected[span_id][2])
    assert len({rank for rank, _, _ in rows.values()}) < len(rows)
    rows = live.by_first(
        "SELECT id, cume_dist() OVER (PARTITION BY model ORDER BY latency_ms) AS c FROM spans"
    )
    for model, items in _partitions(spans, lambda s: s.model).items():
        oracle = _ranks(items, lambda s: s.latency)
        for item in items:
            assert rows[item.id][0] == pytest.approx(oracle[item.id][3]), model
    rows = live.by_first(
        "SELECT id, row_number() OVER (PARTITION BY session_id ORDER BY start_time) AS turn, "
        "ntile(3) OVER (ORDER BY cost, id) AS bucket FROM spans"
    )
    for items in _partitions(spans, lambda s: s.session).values():
        for turn, item in enumerate(sorted(items, key=lambda s: s.start), start=1):
            assert rows[item.id][0] == turn
    ordered = sorted(spans, key=lambda s: (s.cost, s.id))
    # Standard ntile: the first (n mod buckets) buckets hold one extra row.
    size, extra = divmod(len(ordered), 3)
    bucket_of = {}
    position = 0
    for bucket in range(1, 4):
        width = size + (1 if bucket <= extra else 0)
        for item in ordered[position : position + width]:
            bucket_of[item.id] = bucket
        position += width
    assert {key: value[1] for key, value in rows.items()} == bucket_of


def test_offset_and_value_windows_match_standard_semantics(live):
    spans = live.owned.spans
    rows = live.by_first(
        "SELECT id, lag(latency_ms) OVER (PARTITION BY trace_id ORDER BY start_time) AS prev, "
        "lead(latency_ms) OVER (PARTITION BY trace_id ORDER BY start_time) AS next, "
        "lead(latency_ms, 2, -1) OVER (PARTITION BY trace_id ORDER BY start_time) AS next2, "
        "first_value(model) OVER (PARTITION BY session_id ORDER BY start_time) AS first_model, "
        "last_value(model) OVER (PARTITION BY session_id ORDER BY start_time "
        "ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS last_model FROM spans"
    )
    for items in _partitions(spans, lambda s: str(s.trace)).values():
        ordered = sorted(items, key=lambda s: s.start)
        for index, item in enumerate(ordered):
            prev = ordered[index - 1].latency if index else None
            nxt = ordered[index + 1].latency if index + 1 < len(ordered) else None
            nxt2 = ordered[index + 2].latency if index + 2 < len(ordered) else -1
            assert rows[item.id][:3] == (prev, nxt, nxt2), item.id
    for items in _partitions(spans, lambda s: s.session).values():
        ordered = sorted(items, key=lambda s: s.start)
        for item in ordered:
            assert rows[item.id][3:] == (ordered[0].model, ordered[-1].model)
    # Before the rewrite ClickHouse's leadInFrame returned 0 for every row.
    assert any(value[1] not in (None, 0) for value in rows.values())


def test_aggregate_windows_and_frames_match_standard_semantics(live):
    spans = live.owned.spans
    rows = live.by_first(
        "SELECT id, sum(cost) OVER (ORDER BY start_time, id ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running, "
        "avg(latency_ms) OVER (PARTITION BY model ORDER BY start_time ROWS BETWEEN 1 PRECEDING AND 1 FOLLOWING) AS smooth, "
        "count() OVER (PARTITION BY model) AS n, min(cost) OVER (PARTITION BY model) AS lo, "
        "max(cost) OVER (PARTITION BY model) AS hi, "
        "count() OVER (ORDER BY total_tokens RANGE BETWEEN 100 PRECEDING AND CURRENT ROW) AS near FROM spans"
    )
    ordered = sorted(spans, key=lambda s: (s.start, s.id))
    running = 0.0
    for item in ordered:
        running += item.cost
        assert rows[item.id][0] == pytest.approx(running)
    for items in _partitions(spans, lambda s: s.model).values():
        series = sorted(items, key=lambda s: s.start)
        for index, item in enumerate(series):
            frame = series[max(0, index - 1) : index + 2]
            assert rows[item.id][1] == pytest.approx(
                sum(s.latency for s in frame) / len(frame)
            )
            assert rows[item.id][2] == len(items)
            assert rows[item.id][3] == pytest.approx(min(s.cost for s in items))
            assert rows[item.id][4] == pytest.approx(max(s.cost for s in items))
    for item in spans:
        near = sum(1 for s in spans if item.tokens - 100 <= s.tokens <= item.tokens)
        assert rows[item.id][5] == near
    grouped = live.rows(
        "SELECT model, sum(sum(cost)) OVER (ORDER BY model) AS cumulative FROM spans GROUP BY model ORDER BY model"
    )
    models = sorted({s.model for s in spans})
    assert [row[0] for row in grouped] == models
    total = 0.0
    for row, model in zip(grouped, models, strict=True):
        total += sum(s.cost for s in spans if s.model == model)
        assert row[1] == pytest.approx(total)


def test_windowed_subqueries_joins_and_ctes_stay_scoped(live):
    """Every table reference inside a windowed subquery, join or CTE is scoped:
    the foreign tenant's values are 1,000 times larger and its ids differ."""
    spans = live.owned.spans
    evaluated_traces = {str(e["trace"]) for e in live.owned.evals if e["trace"]}
    rows = live.rows(
        "SELECT trace_id, id, latency_ms FROM (SELECT trace_id, id, latency_ms, "
        "row_number() OVER (PARTITION BY trace_id ORDER BY latency_ms DESC, id) AS r "
        "FROM spans WHERE trace_id IN (SELECT toString(trace_id) FROM eval_results)) AS t "
        "WHERE r = 1 ORDER BY trace_id"
    )
    expected = sorted(
        (
            trace,
            min(
                (s for s in spans if str(s.trace) == trace),
                key=lambda s: (-s.latency, s.id),
            ),
        )
        for trace in evaluated_traces
    )
    assert [(r[0], r[1], r[2]) for r in rows] == [
        (trace, span.id, span.latency) for trace, span in expected
    ]
    ranked = live.rows(
        "WITH spend AS (SELECT user_id, sum(cost) AS c FROM spans GROUP BY user_id) "
        "SELECT u.external_id, s.c, dense_rank() OVER (ORDER BY s.c DESC) AS r "
        "FROM spend AS s JOIN users AS u ON s.user_id = u.id ORDER BY r"
    )
    spend = defaultdict(float)
    for span in spans:
        spend[span.user] += span.cost
    names = {user: external for user, external, _ in live.owned.users}
    expected = sorted(spend.items(), key=lambda item: -item[1])
    assert [row[0] for row in ranked] == [names[user] for user, _ in expected]
    assert [row[2] for row in ranked] == list(range(1, len(expected) + 1))
    assert all(row[0].startswith("own-") for row in ranked)
    joined = live.rows(
        "SELECT e.config_id, e.score, rank() OVER (PARTITION BY e.config_id ORDER BY e.score DESC) AS r "
        "FROM eval_results AS e JOIN sessions AS s ON e.session_id = s.id ORDER BY e.config_id, r"
    )
    owned_configs = {str(value) for value in live.owned.configs.values()}
    assert {row[0] for row in joined} <= owned_configs
    assert len(joined) == len(live.owned.evals)
    # Control: the same windowed query scoped to the foreign tenant sees only
    # its own, distinctively larger values (the fixture really has two tenants).
    foreign = live.foreign
    foreign_scope = replace(
        live.scope,
        organization_id=foreign.org,
        workspace_id=foreign.workspace,
        project_id=foreign.project,
        eval_config_ids=tuple(foreign.configs.values()),
    )
    other = live.run(
        "SELECT max(latency_ms) OVER () AS m FROM spans LIMIT 1", foreign_scope
    )["rows"]
    assert other == [[max(s.latency for s in foreign.spans)]]
    assert live.rows("SELECT max(latency_ms) OVER () AS m FROM spans LIMIT 1") == [
        [max(s.latency for s in spans)]
    ]


# ---------------------------------------------------------------------------
# Pre-run estimate: EXPLAIN ESTIMATE of the compiled scoped SQL on the locked
# principal, summarized so another project's volume cannot be read from it.

ESTIMATED_QUERIES = [
    "SELECT count() FROM spans",
    "SELECT * FROM traces",
    "SELECT * FROM sessions",
    "SELECT * FROM users",
    "SELECT * FROM eval_results",
    "SELECT * FROM annotations",
    "WITH spend AS (SELECT user_id, sum(cost) AS c FROM spans GROUP BY user_id) "
    "SELECT u.external_id, dense_rank() OVER (ORDER BY s.c DESC) AS r "
    "FROM spend AS s JOIN users AS u ON s.user_id = u.id",
]


def _estimates(value):
    from tracer.sql_workbench import runner
    from tracer.sql_workbench.service import summarize_estimate

    scope = value.scope
    raw, safe = {}, {}
    for sql in ESTIMATED_QUERIES:
        result = runner.estimate(
            compile_query(sql, scope),
            table_filters=additional_table_filters(scope),
            database=scope.database,
        )
        raw[sql] = {item["table"]: item["rows"] for item in result["tables"]}
        safe[sql] = summarize_estimate(result, scope.database)
    return raw, safe


def test_estimate_runs_on_the_locked_principal_and_hides_other_projects_volume():
    """Measured on production sort keys: the reported estimate is identical
    after another project's volume grows 10x and after merges mix both
    projects into the same parts and granules, while the raw granule row
    count that EXPLAIN ESTIMATE returns does change (so it is never shown)."""
    with provisioned(PRODUCTION_KEYS, label="est") as (value, admin):
        scope, foreign = value.scope, value.foreign
        database = scope.database
        raw_before, before = _estimates(value)
        by_dataset = {
            sql: {entry["dataset"]: entry for entry in summary["sources"]}
            for sql, summary in before.items()
        }
        for sql, summary in before.items():
            assert summary["status"] == "ok", sql
            assert summary["exceeds_scan_bound"] is False, sql
        spans = by_dataset["SELECT count() FROM spans"]["spans"]
        assert spans["estimated"] and spans["marks"] >= 1
        # An upper bound in whole granules, never below the owned rows.
        assert spans["max_rows"] % 8192 == 0
        assert spans["max_rows"] >= len(value.owned.spans)
        for sql, dataset in [
            ("SELECT * FROM traces", "traces"),
            ("SELECT * FROM sessions", "sessions"),
            ("SELECT * FROM users", "users"),
            ("SELECT * FROM eval_results", "eval_results"),
        ]:
            assert by_dataset[sql][dataset]["estimated"], (sql, by_dataset[sql])
        # The label-first annotations index is shared across projects.
        annotations = by_dataset["SELECT * FROM annotations"]["annotations"]
        assert annotations["estimated"] is False
        assert annotations["max_rows"] is None
        base = scope.start + timedelta(days=3)
        for copy in range(9):
            extra = replace(
                build_tenant(base, scale=1000, prefix=f"foreign-x{copy}"),
                org=foreign.org,
                workspace=foreign.workspace,
                project=foreign.project,
                configs=foreign.configs,
                label=foreign.label,
                metric=foreign.metric,
            )
            insert_tenant(admin, database, extra, scope.eval_table)
        assert admin.execute(
            f"SELECT count() FROM {database}.spans WHERE project_id = %(p)s",
            {"p": foreign.project},
        ) == [(10 * len(foreign.spans),)]
        _, grown = _estimates(value)
        assert grown == before
        for table in PRODUCTION_KEYS:
            admin.execute(f"OPTIMIZE TABLE {database}.{table} FINAL")
        raw_merged, merged = _estimates(value)
        assert merged == before
        spans_sql = "SELECT count() FROM spans"
        print(
            "estimate side channel: raw spans granule rows "
            f"{raw_before[spans_sql]['spans']} -> {raw_merged[spans_sql]['spans']} "
            f"after 10x foreign volume and merges; reported {spans}"
        )
        assert raw_merged[spans_sql]["spans"] > raw_before[spans_sql]["spans"]


def test_a_run_waits_out_brief_contention_for_the_principal(
    live, live_admin, monkeypatch
):
    """Both of the principal's concurrent-query slots are held (as an advisory
    estimate or another run can do for a moment); a run started meanwhile is
    turned away by ClickHouse with code 202, retried, and then succeeds."""
    import threading
    import time
    from types import SimpleNamespace

    from clickhouse_driver import Client

    from tracer.sql_workbench import runner

    principal = os.environ["SQL_WORKBENCH_CH_USER"]
    errors = []

    def hold():
        client = Client(**runner.connection_config())
        try:
            client.execute("SELECT sleep(3)")
        except Exception as exc:  # reported below, never swallowed
            errors.append(exc)
        finally:
            client.disconnect()

    holders = [threading.Thread(target=hold) for _ in range(2)]
    for holder in holders:
        holder.start()
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            held = live_admin.execute(
                "SELECT count() FROM system.processes WHERE user = %(user)s",
                {"user": principal},
            )[0][0]
            if held == 2:
                break
            time.sleep(0.05)
        assert held == 2, "both principal slots were not observed as held"
        retries = []
        # Only the runner's own clock is observed; other threads keep time.sleep.
        monkeypatch.setattr(
            runner,
            "time",
            SimpleNamespace(
                monotonic=time.monotonic,
                sleep=lambda s: (retries.append(s), time.sleep(s)),
            ),
        )
        # The test exercises the mechanism; it must outlast the 3 s holders.
        monkeypatch.setattr(runner, "START_RETRY_SECONDS", 10.0)
        started = time.monotonic()
        result = live.run("SELECT count() AS n FROM spans")
        waited = time.monotonic() - started
    finally:
        for holder in holders:
            holder.join(timeout=10)
    assert errors == []
    assert result["rows"] == [[len(live.owned.spans)]]
    assert retries, "the run was never turned away, so nothing was proven"
    print(
        f"principal contention: {len(retries)} retries, run finished in {waited:.2f} s"
    )


# ---------------------------------------------------------------------------
# Example gallery: every vetted example runs through the production path and
# returns exactly what Python computes from the owned seed rows.


def _quantile(values, level):
    """ClickHouse ``quantile`` on fewer than 8,192 values: linear interpolation."""
    ordered = sorted(values)
    index = max(0.0, min(len(ordered) - 1.0, level * (len(ordered) - 1)))
    left = int(index)
    if left + 1 == len(ordered):
        return float(ordered[left])
    return ordered[left] * (left + 1 - index) + ordered[left + 1] * (index - left)


def _span_end(span):
    return span.start + timedelta(milliseconds=span.latency)


def _gallery_oracles(tenant):
    """Expected (columns, rows, ordered) per example id; floats rounded like SQL."""
    spans, evals = tenant.spans, tenant.evals
    users = {user: (external, seen) for user, external, seen in tenant.users}
    sessions = {s: (external, locale) for s, external, _, locale in tenant.sessions}
    configs = {name: str(config) for name, config in tenant.configs.items()}
    by_model = _partitions(spans, lambda s: s.model)
    by_day = _partitions(spans, lambda s: s.start.date().isoformat())
    by_trace = _partitions(spans, lambda s: str(s.trace))
    by_user = _partitions(spans, lambda s: s.user)
    oracles = {}
    oracles["cost-tokens-by-model"] = (
        ["model", "span_count", "tokens", "total_cost"],
        sorted(
            (
                [m, len(g), sum(s.tokens for s in g), round(sum(s.cost for s in g), 6)]
                for m, g in by_model.items()
            ),
            key=lambda r: (-r[3], r[0]),
        ),
        True,
    )
    oracles["latency-percentiles-by-day"] = (
        ["day", "span_count", "p50_ms", "p95_ms", "p99_ms"],
        [
            [d, len(g)]
            + [_quantile([s.latency for s in g], q) for q in (0.5, 0.95, 0.99)]
            for d, g in sorted(by_day.items())
        ],
        True,
    )
    oracles["error-rate-by-day"] = (
        ["day", "span_count", "errors", "error_rate"],
        [
            [
                d,
                len(g),
                sum(s.status == "ERROR" for s in g),
                round(sum(s.status == "ERROR" for s in g) / len(g), 4),
            ]
            for d, g in sorted(by_day.items())
        ],
        True,
    )
    traces = [
        [
            t,
            len(g),
            round(
                (max(map(_span_end, g)) - min(s.start for s in g)).total_seconds()
                * 1000
            ),
            round(sum(s.cost for s in g), 6),
        ]
        for t, g in by_trace.items()
    ]
    oracles["slowest-traces"] = (
        ["trace_id", "span_count", "duration_ms", "total_cost"],
        sorted(traces, key=lambda r: (-r[2], r[0]))[:20],
        True,
    )
    oracles["sessions-per-user"] = (
        ["user_id", "external_user_id", "session_count", "span_count"],
        [
            [str(u), users[u][0], len({s.session for s in g}), len(g)]
            for u, g in by_user.items()
        ],
        False,
    )
    groups = _partitions(
        evals, lambda e: (configs[e["config"]], sessions[e["session"]][1])
    )
    oracles["eval-score-by-evaluator-and-locale"] = (
        ["config_id", "locale", "results", "avg_score", "pass_rate"],
        [
            [
                config,
                locale,
                len(g),
                round(sum(e["score"] for e in g) / len(g), 4),
                round(sum(e["passed"] for e in g) / len(g), 4),
            ]
            for (config, locale), g in groups.items()
        ],
        False,
    )
    passed_by_span = {e["span"]: e["passed"] for e in evals if e["target"] == "span"}
    pairs = _partitions(
        [(a["value"], passed_by_span[a["span"]]) for a in tenant.annotations],
        lambda pair: pair,
    )
    total = sum(len(g) for g in pairs.values())
    oracles["annotation-agreement"] = (
        ["human_label", "eval_passed", "spans_compared", "share"],
        [
            [label, ok, len(g), round(len(g) / total, 4)]
            for (label, ok), g in sorted(pairs.items())
        ],
        True,
    )
    oracles["derived-metric-over-time"] = (
        ["metric_id", "window_start", "latest_value", "attempts"],
        [
            [str(tenant.metric), m["window_start"], m["value"], 1]
            for m in sorted(tenant.metrics, key=lambda m: m["window_start"])
        ],
        True,
    )
    session_evals = [e for e in evals if e["target"] == "session"]
    oracles["lowest-scoring-sessions"] = (
        ["session_external_id", "locale", "results", "avg_score"],
        sorted(
            (
                [
                    sessions[e["session"]][0],
                    sessions[e["session"]][1],
                    1,
                    round(e["score"], 4),
                ]
                for e in session_evals
            ),
            key=lambda r: (r[3], r[0]),
        ),
        True,
    )
    spend = {u: sum(s.cost for s in g) for u, g in by_user.items()}
    ranks = {c: i + 1 for i, c in enumerate(sorted(set(spend.values()), reverse=True))}
    oracles["costly-users-ranked"] = (
        ["user_id", "total_cost", "span_count", "cost_rank", "cost_share"],
        sorted(
            (
                [
                    str(u),
                    round(c, 6),
                    len(by_user[u]),
                    ranks[c],
                    round(c / sum(spend.values()), 4),
                ]
                for u, c in spend.items()
            ),
            key=lambda r: r[3],
        ),
        True,
    )
    sampled = {
        e["span"]: e["probability"]
        for e in evals
        if e["target"] == "span" and e["probability"] is not None
    }
    oracles["sampled-vs-all-by-model"] = (
        ["model", "all_spans", "sampled_spans", "estimated_spans"],
        [
            [
                m,
                len(g),
                sum(s.id in sampled for s in g),
                round(sum(1 / sampled[s.id] for s in g if s.id in sampled), 4),
            ]
            for m, g in sorted(by_model.items())
        ],
        True,
    )
    cohorts = defaultdict(set)
    for span in spans:
        cohort = users[span.user][1].date()
        cohorts[(cohort.isoformat(), (span.start.date() - cohort).days)].add(span.user)
    oracles["retention-cohorts"] = (
        ["cohort_day", "days_later", "active_users"],
        [
            [day, later, len(members)]
            for (day, later), members in sorted(cohorts.items())
        ],
        True,
    )
    return oracles


def _normalized(value):
    """One comparable form for driver values: dates by day, times as UTC."""
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, datetime):
        value = value if value.tzinfo else value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat()
    if isinstance(value, str) and len(value) >= 19 and value[10] == "T":
        parsed = datetime.fromisoformat(value)
        parsed = parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).isoformat()
    if isinstance(value, str) and len(value) == 10 and value[4] == "-":
        return value
    return value


def test_every_gallery_example_returns_the_expected_rows(live):
    from tracer.sql_workbench.examples import EXAMPLES

    oracles = _gallery_oracles(live.owned)
    assert set(oracles) == {example.id for example in EXAMPLES}
    for example in EXAMPLES:
        columns, expected, ordered = oracles[example.id]
        result = live.run(example.sql)
        assert [c["name"] for c in result["columns"]] == columns, example.id
        assert result["truncated"] is False, example.id
        actual = [[_normalized(v) for v in row] for row in result["rows"]]
        wanted = [[_normalized(v) for v in row] for row in expected]
        if not ordered:
            actual, wanted = sorted(actual, key=repr), sorted(wanted, key=repr)
        assert actual == wanted, (example.id, actual, wanted)
        # No foreign identifier or foreign-scale value leaks into any example.
        assert "foreign" not in repr(result["rows"]), example.id


# ---------------------------------------------------------------------------
# Analytical function set: every approved function's probe (test_compiler's
# FUNCTION_PROBES) runs through the production path; values come from Python.


def _function_expectations(tenant):
    """Expected value, or a predicate, per approved function name."""
    import math
    import statistics

    spans = tenant.spans
    latency = [s.latency for s in spans]
    tokens = [s.tokens for s in spans]
    errors = [s for s in spans if s.status == "ERROR"]
    ok = [s for s in spans if s.status == "OK"]
    tool_args = [
        s.attrs_string["tool_args"] for s in spans if "tool_args" in s.attrs_string
    ]
    tool_k = [int(value.split('"k":')[1].rstrip("}")) for value in tool_args]
    starts = [s.start for s in spans]
    ordered = sorted(spans, key=lambda s: s.start)
    models = [s.model for s in spans]
    by_count = sorted(set(models), key=lambda m: -models.count(m))

    def week(moment):  # ClickHouse toStartOfWeek mode 0: weeks start on Sunday
        day = moment.date()
        return day - timedelta(days=(day.weekday() + 1) % 7)

    def buckets(minutes):
        return len(
            {
                s.start.replace(minute=s.start.minute // minutes * minutes, second=0)
                for s in spans
            }
        )

    mean_latency = sum(latency) / len(latency)
    expected = {
        "count": len(spans),
        "countif": len(errors),
        "sum": sum(tokens),
        "sumif": sum(s.tokens for s in ok),
        "avg": mean_latency,
        "avgif": sum(s.latency for s in ok) / len(ok),
        "min": min(latency),
        "minif": min(s.latency for s in errors),
        "max": max(latency),
        "maxif": max(s.latency for s in errors),
        "any": "openai",
        "anylast": "google",
        "argmin": min(spans, key=lambda s: s.latency).model,
        "argmax": max(spans, key=lambda s: s.latency).model,
        "uniq": len(set(models)),
        "uniqif": len({s.model for s in errors}),
        "uniqexact": len({s.trace for s in spans}),
        "uniqexactif": len({s.trace for s in errors}),
        "uniqcombined": len({s.session for s in spans}),
        "median": _quantile(latency, 0.5),
        "quantile": _quantile(latency, 0.9),
        "quantiles": [_quantile(latency, 0.5), _quantile(latency, 0.9)],
        "quantileexact": float(sorted(latency)[int(0.5 * len(latency))]),
        "quantiletdigest": lambda v: min(latency) <= v <= max(latency),
        "stddevpop": statistics.pstdev(latency),
        "stddevsamp": statistics.stdev(latency),
        "varpop": statistics.pvariance(latency),
        "varsamp": statistics.variance(latency),
        "corr": statistics.correlation(latency, tokens),
        "avgweighted": sum(lat * tok for lat, tok in zip(latency, tokens, strict=True))
        / sum(tokens),
        "grouparray": sorted(
            s.model for s in sorted(spans, key=lambda s: -s.latency)[:3]
        ),
        "groupuniqarray": sorted({s.provider for s in spans}),
        "topk": by_count[:1],
        "abs": max(latency) - min(latency),
        "round": round(sum(s.cost for s in spans), 2),
        "floor": float(math.floor(mean_latency)),
        "ceil": float(math.ceil(mean_latency)),
        "ceiling": float(math.ceil(mean_latency)),
        "sqrt": math.sqrt(max(latency)),
        "pow": float(max(latency) ** 2),
        "power": float(min(latency) ** 2),
        "log": math.log(max(latency)),
        "ln": math.log(min(latency)),
        "exp": math.exp(min(tokens) / 100),
        "greatest": max(max(latency), 2000),
        "least": min(max(latency), 2000),
        "intdiv": max(latency) // 7,
        "modulo": max(latency) % 7,
        "coalesce": len(tenant.evals),
        "ifnull": len(tenant.evals),
        "nullif": len(ok),
        "if": len(errors),
        "multiif": sum(value >= 500 for value in latency),
        "lower": len(errors),
        "upper": min(models).upper(),
        "length": max(len(f"llm-{m}") for m in models),
        "lengthutf8": max(len(s.provider) for s in spans),
        "substring": min(models)[2:3],
        "concat": f"{min(models)}/{min(s.provider for s in spans)}",
        "trim": max(models),
        "ltrim": max(models),
        "rtrim": max(models),
        "replace": max(m.replace("m-", "model-") for m in models),
        "replaceall": max(m.replace("-", "_") for m in models),
        "position": max(s.provider.find("o") + 1 for s in spans),
        "positioncaseinsensitive": max(s.provider.lower().find("o") + 1 for s in spans),
        "startswith": sum(s.provider.startswith("open") for s in spans),
        "endswith": sum(s.provider.endswith("ic") for s in spans),
        "match": sum(m in ("m-b", "m-c") for m in models),
        "splitbychar": max(m.split("-")[1] for m in models),
        "splitbystring": max(m.split("m-")[1] for m in models),
        "arrayelement": max(m.split("-")[0] for m in models),
        "has": len(tool_args),
        "mapkeys": max(len(s.attrs_string) for s in spans),
        "mapvalues": sum("de" in s.attrs_string.values() for s in spans),
        "mapcontains": sum("ttft_ms" in s.attrs_number for s in spans),
        "jsonextract": sum(tool_k),
        "jsonextractstring": sum('"tool":"search"' in value for value in tool_args),
        "jsonextractint": sum(tool_k),
        "jsonextractuint": sum(tool_k),
        "jsonextractfloat": float(sum(tool_k)),
        "jsonextractbool": 0,
        "jsonextractraw": '"search"',
        "jsonextractkeys": 2,
        "jsonhas": len(tool_args),
        "jsonlength": 2,
        "jsontype": len(tool_args),
        "tostring": max(str(value) for value in latency),
        "touuid": len(spans),
        "touuidornull": len(spans),
        "toint64": sum(latency),
        "tofloat64": float(sum(tokens)),
        "todate": len({s.start.date() for s in spans}),
        "todatetime": min(starts),
        "todatetime64": min(starts),
        "tostartofminute": buckets(1),
        "tostartoffiveminutes": buckets(5),
        "tostartoffifteenminutes": buckets(15),
        "tostartofinterval": buckets(30),
        "tostartofhour": buckets(60),
        "tostartofday": len({s.start.date() for s in spans}),
        "tostartofweek": len({week(s.start) for s in spans}),
        "tostartofmonth": len({(s.start.year, s.start.month) for s in spans}),
        "toyear": min(s.start.year for s in spans),
        "tomonth": min(s.start.month for s in spans),
        "todayofmonth": min(s.start.day for s in spans),
        "todayofweek": min(s.start.isoweekday() for s in spans),
        "tohour": min(s.start.hour for s in spans),
        "tominute": max(s.start.minute for s in spans),
        "formatdatetime": min(starts).strftime("%Y-%m-%d %H:%M"),
        "datediff": max(latency),
        "dateadd": len(spans),
        "now": len(spans),
        "rownumber": len(spans),
        "rank": len(spans),
        "denserank": len(set(latency)),
        "lag": 1,
        "lead": 1,
        "firstvalue": ordered[0].latency,
        "lastvalue": ordered[-1].latency,
    }
    return expected


def _matches(actual, wanted):
    import json
    import math

    if callable(wanted):
        return wanted(actual)
    if isinstance(wanted, list):
        values = json.loads(actual) if isinstance(actual, str) else actual
        if all(isinstance(item, str) for item in wanted):
            return sorted(values) == wanted
        return len(values) == len(wanted) and all(
            math.isclose(a, b, rel_tol=1e-9)
            for a, b in zip(values, wanted, strict=True)
        )
    if isinstance(wanted, datetime):
        return _normalized(actual) == _normalized(wanted)
    if isinstance(wanted, float):
        return isinstance(actual, (int, float)) and math.isclose(
            actual, wanted, rel_tol=1e-9, abs_tol=1e-9
        )
    return actual == wanted


def test_every_approved_function_runs_with_the_expected_value(live):
    from tracer.sql_workbench.compiler import FUNCTIONS

    probes = _sibling("test_compiler").FUNCTION_PROBES
    expected = _function_expectations(live.owned)
    assert set(probes) == set(expected) == set(FUNCTIONS)
    wrong = {}
    for name in sorted(probes):
        rows = live.rows(probes[name])
        assert len(rows) == 1 and len(rows[0]) == 1, (name, rows)
        if not _matches(rows[0][0], expected[name]):
            wrong[name] = (rows[0][0], expected[name])
    assert wrong == {}
