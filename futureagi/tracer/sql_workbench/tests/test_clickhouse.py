"""Small live security fixtures. Never point these at a shared/non-test database."""

import os
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from tracer.sql_workbench.catalog import Scope
from tracer.sql_workbench.compiler import compile_query

pytestmark = pytest.mark.integration

# Exact columns consumed by the catalog; isolated RMT tables preserve FINAL behavior.
TABLES = {
    "spans": "project_id UUID, org_id Nullable(UUID), id String, trace_id String, trace_session_id Nullable(UUID), end_user_id Nullable(UUID), name String, start_time DateTime64(6,'UTC'), end_time Nullable(DateTime64(6,'UTC')), latency_ms Int32, status String, model String, provider String, total_tokens Int32, cost Float64, attrs_string Map(String,String), attrs_number Map(String,Float64), is_deleted UInt8, _version UInt64",
    "traces": "project_id UUID, id UUID, session_id Nullable(UUID), name Nullable(String), external_id Nullable(String), created_at DateTime64(6,'UTC'), metadata String, is_deleted UInt8, _version UInt64",
    "trace_sessions": "project_id UUID, trace_session_id UUID, external_session_id String, first_seen DateTime64(6,'UTC'), is_deleted UInt8, _version UInt64",
    "end_users": "project_id UUID, organization_id UUID, end_user_id UUID, user_id String, user_id_type Nullable(String), first_seen DateTime64(6,'UTC'), metadata String, is_deleted UInt8, _version UInt64",
    "tracer_eval_logger_v2": "id UUID, trace_id Nullable(UUID), observation_span_id Nullable(String), trace_session_id Nullable(UUID), custom_eval_config_id UUID, target_type String, output_float Nullable(Float64), output_bool Nullable(UInt8), output_str Nullable(String), error UInt8, created_at DateTime64(6,'UTC'), eval_task_id Nullable(String), output_metadata String DEFAULT '{}', is_deleted UInt8, _version UInt64",
    "model_hub_score": "id UUID, organization_id UUID, workspace_id Nullable(UUID), tracer_project_id Nullable(UUID), trace_id Nullable(UUID), observation_span_id Nullable(String), trace_session_id Nullable(UUID), label_id UUID, value String, notes Nullable(String), created_at DateTime64(6,'UTC'), score_source String, deleted UInt8, _peerdb_is_deleted UInt8, _version UInt64",
}


@pytest.fixture
def scoped_clickhouse():
    from conftest import _ch_test_native_client

    assigned = os.environ.get("SQL_WORKBENCH_TEST_DATABASE")
    database = assigned or f"test_sql_{uuid4().hex}"
    assert re.fullmatch(r"test_[A-Za-z0-9_]+", database)
    created = []
    with _ch_test_native_client(owned_database=database) as client:
        if assigned:
            existing = client.execute(
                "SELECT name FROM system.tables WHERE database=%(db)s AND name IN %(tables)s",
                {"db": database, "tables": tuple(TABLES)},
            )
            assert not existing, "Refusing to replace pre-existing SQL fixture tables"
        else:
            client.execute(f"CREATE DATABASE {database}")
        try:
            for table, columns in TABLES.items():
                key = (
                    "(project_id, id)"
                    if table == "spans"
                    else (
                        "trace_session_id"
                        if table == "trace_sessions"
                        else "end_user_id" if table == "end_users" else "id"
                    )
                )
                client.execute(
                    f"CREATE TABLE {database}.{table} ({columns}) ENGINE=ReplacingMergeTree(_version) ORDER BY {key}"
                )
                created.append(table)
            now = datetime.now(timezone.utc)
            org, workspace, project, config = uuid4(), uuid4(), uuid4(), uuid4()
            scope = Scope(
                org,
                workspace,
                project,
                now - timedelta(hours=1),
                now + timedelta(minutes=1),
                (config,),
                "tracer_eval_logger_v2",
                database,
            )
            tenants = [
                (org, workspace, project, config),
                (uuid4(), uuid4(), uuid4(), uuid4()),
            ]
            ids = []
            for index, (o, w, p, c) in enumerate(tenants):
                trace, session, user, span, evaluation, annotation = (
                    uuid4(),
                    uuid4(),
                    uuid4(),
                    f"span-{index}",
                    uuid4(),
                    uuid4(),
                )
                ids.append(
                    dict(
                        trace=trace,
                        session=session,
                        user=user,
                        span=span,
                        eval=evaluation,
                        annotation=annotation,
                    )
                )
                client.execute(
                    f"INSERT INTO {database}.spans (project_id,org_id,id,trace_id,trace_session_id,end_user_id,name,start_time,attrs_string) VALUES",
                    [
                        (
                            p,
                            o,
                            span,
                            str(trace),
                            session,
                            user,
                            f"tenant-{index}",
                            now,
                            {"locale": f"locale-{index}"},
                        )
                    ],
                )
                client.execute(
                    f"INSERT INTO {database}.traces (project_id,id,session_id,name,created_at) VALUES",
                    [(p, trace, session, f"tenant-{index}", now)],
                )
                client.execute(
                    f"INSERT INTO {database}.trace_sessions (project_id,trace_session_id,external_session_id,first_seen) VALUES",
                    [(p, session, f"session-{index}", now)],
                )
                client.execute(
                    f"INSERT INTO {database}.end_users (project_id,organization_id,end_user_id,user_id,first_seen) VALUES",
                    [(p, o, user, f"user-{index}", now)],
                )
                client.execute(
                    f"INSERT INTO {database}.tracer_eval_logger_v2 (id,trace_session_id,custom_eval_config_id,target_type,output_float,created_at) VALUES",
                    [(evaluation, session, c, "session", 0.5, now)],
                )
                client.execute(
                    f"INSERT INTO {database}.model_hub_score (id,organization_id,workspace_id,tracer_project_id,trace_session_id,label_id,value,created_at) VALUES",
                    [(annotation, o, w, p, session, uuid4(), f"value-{index}", now)],
                )
            # Span ids are only unique within a project/trace. A foreign span
            # may reuse the same 16-hex id: config ownership + bare span id is
            # insufficient. The evaluator target must match the owned pair.
            client.execute(
                f"INSERT INTO {database}.spans (project_id,org_id,id,trace_id,trace_session_id,name,start_time) VALUES",
                [
                    (
                        tenants[1][2],
                        tenants[1][0],
                        ids[0]["span"],
                        str(ids[1]["trace"]),
                        ids[1]["session"],
                        "foreign-colliding-span",
                        now,
                    )
                ],
            )
            client.execute(
                f"INSERT INTO {database}.tracer_eval_logger_v2 (id,trace_id,observation_span_id,custom_eval_config_id,target_type,output_float,created_at) VALUES",
                [
                    (uuid4(), ids[1]["trace"], ids[0]["span"], config, "span", 99, now),
                    (uuid4(), None, ids[0]["span"], config, "span", 99, now),
                ],
            )
            # Config ownership alone must not authorize a foreign or NULL target.
            client.execute(
                f"INSERT INTO {database}.tracer_eval_logger_v2 (id,trace_session_id,custom_eval_config_id,target_type,output_float,created_at) VALUES",
                [
                    (uuid4(), ids[1]["session"], config, "session", 99, now),
                    (uuid4(), None, config, "session", 99, now),
                ],
            )
            # Explicit project ownership is insufficient when workspace is NULL/foreign.
            client.execute(
                f"INSERT INTO {database}.model_hub_score (id,organization_id,workspace_id,tracer_project_id,label_id,value,created_at) VALUES",
                [
                    (uuid4(), org, None, project, uuid4(), "null-scope", now),
                    (
                        uuid4(),
                        org,
                        tenants[1][1],
                        project,
                        uuid4(),
                        "foreign-scope",
                        now,
                    ),
                ],
            )
            client.execute(
                f"INSERT INTO {database}.spans (project_id,org_id,id,trace_id,name,start_time) VALUES",
                [
                    (project, None, "null-org", "bad", "null-org", now),
                    (project, tenants[1][0], "foreign-org", "bad", "foreign-org", now),
                ],
            )
            yield client, scope, ids[0]
        finally:
            if assigned:
                # Only tables this fixture created in our assigned empty namespace.
                for table in reversed(created):
                    client.execute(f"DROP TABLE {database}.{table} SYNC")
            else:
                client.execute(f"DROP DATABASE {database} SYNC")


@pytest.mark.parametrize(
    "dataset,key",
    [
        ("spans", "span"),
        ("traces", "trace"),
        ("sessions", "session"),
        ("users", "user"),
        ("eval_results", "eval"),
        ("annotations", "annotation"),
    ],
)
def test_each_dataset_returns_only_owned_rows(scoped_clickhouse, dataset, key):
    client, scope, ids = scoped_clickhouse
    assert client.execute(compile_query(f"SELECT id FROM {dataset}", scope)) == [
        (ids[key],)
    ]


def test_real_join_cte_union_and_subquery_cannot_expand_tenant_scope(scoped_clickhouse):
    client, scope, ids = scoped_clickhouse
    query = "WITH own AS (SELECT id, locale FROM sessions) SELECT s.locale, avg(e.score) AS score FROM own AS s JOIN eval_results AS e ON s.id = e.session_id GROUP BY s.locale"
    assert client.execute(compile_query(query, scope)) == [("locale-0", 0.5)]
    query = "SELECT name FROM spans WHERE session_id IN (SELECT id FROM sessions) UNION ALL SELECT external_id FROM sessions"
    assert sorted(client.execute(compile_query(query, scope))) == [
        ("session-0",),
        ("tenant-0",),
    ]


def test_real_locked_service_profile_and_settings_free_execution(
    scoped_clickhouse, monkeypatch
):
    import ipaddress
    import secrets
    from django.test import override_settings
    from tracer.sql_workbench.provision import service_access_ddl
    from tracer.sql_workbench.runner import run

    client, scope, ids = scoped_clickhouse
    principal = f"sql_test_{uuid4().hex[:12]}"
    password = secrets.token_urlsafe(32)
    # Restrict this ephemeral account to the actual caller address (Docker NAT
    # may differ from localhost). No network-wide or HOST ANY test shortcut.
    address = ipaddress.ip_address(
        str(
            client.execute(
                "SELECT address FROM system.processes WHERE query_id=currentQueryID()"
            )[0][0]
        )
    )
    address = getattr(address, "ipv4_mapped", None) or address
    cidr = f"{address}/{32 if address.version == 4 else 128}"
    statements = service_access_ddl(
        database=scope.database,
        principal=principal,
        password=password,
        cidr=cidr,
        eval_table=scope.eval_table,
    )
    created = False
    try:
        for i, statement in enumerate(statements):
            client.execute(statement)
            if i == 0:
                created = True
        monkeypatch.setenv("SQL_WORKBENCH_CH_USER", principal)
        monkeypatch.setenv("SQL_WORKBENCH_CH_PASSWORD", password)
        monkeypatch.setenv("SQL_WORKBENCH_CH_SECURE", "false")
        with override_settings(
            CLICKHOUSE_V2={
                "CH25_HOST": client.connection.host,
                "CH25_TCP_PORT": client.connection.port,
                "CH25_DATABASE": scope.database,
                "CH25_USER": client.connection.user,
            }
        ):
            result = run(
                compile_query("SELECT count() AS n FROM spans", scope),
                uuid4(),
                lambda: False,
            )
        assert result["rows"] == [[1]]
        assert result["truncated"] is False
    finally:
        if created:
            client.execute(f"DROP USER {principal}")


def test_real_sampling_metadata_preserves_unknowns_and_task_run_distinction(
    scoped_clickhouse,
):
    import json

    client, scope, ids = scoped_clickhouse
    query = compile_query(
        "SELECT run_id, eval_task_id, policy_version, stratum, inclusion_probability, sampling_unit, sampling_unit_id, population_size FROM eval_results",
        scope,
    )
    assert client.execute(query) == [(None,) * 8]
    task_id = str(uuid4())
    sampling = {
        "policy_version": "policy-v1",
        "stratum": "locale-0",
        "inclusion_probability": 0.25,
        "sampling_unit": "session",
        "sampling_unit_id": str(ids["session"]),
        "population_size": 4,
        "window": {"start": scope.start.isoformat(), "end": scope.end.isoformat()},
    }

    def write(version):
        client.execute(
            f"INSERT INTO {scope.database}.tracer_eval_logger_v2 (id,trace_session_id,custom_eval_config_id,target_type,created_at,eval_task_id,output_metadata,_version) VALUES",
            [
                (
                    ids["eval"],
                    ids["session"],
                    scope.eval_config_ids[0],
                    "session",
                    scope.end - timedelta(minutes=1),
                    task_id,
                    json.dumps({"_task_selection": {"sampling": sampling}}),
                    version,
                )
            ],
        )

    write(1)
    assert client.execute(query) == [
        (
            None,
            task_id,
            "policy-v1",
            "locale-0",
            0.25,
            "session",
            str(ids["session"]),
            4,
        )
    ]
    sampling["inclusion_probability"] = 2
    write(2)
    assert client.execute(query)[0][4] is None
    windows = client.execute(
        compile_query(
            "SELECT sampling_window_start, sampling_window_end FROM eval_results", scope
        )
    )
    assert windows == [(scope.start, scope.end)]


def test_e2e_only_bootstrap_is_idempotent_on_real_clickhouse(scoped_clickhouse):
    import importlib.util
    from pathlib import Path
    from clickhouse_driver import Client
    from clickhouse_driver.errors import ServerException

    client, scope, ids = scoped_clickhouse
    path = Path(__file__).resolve().parents[4] / "e2e/stack/backend/bootstrap_sql.py"
    spec = importlib.util.spec_from_file_location("live_sql_fixture_bootstrap", path)
    bootstrap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bootstrap)
    assert (
        client.execute(
            "SELECT count() FROM system.users WHERE name=%(name)s",
            {"name": bootstrap.ACCOUNT},
        )[0][0]
        == 0
    ), "Refusing to replace a pre-existing fixture account"
    env = {
        "FUTURE_AGI_E2E_FIXTURE": "sql-workbench",
        "SQL_WORKBENCH_CH_USER": bootstrap.ACCOUNT,
        "SQL_WORKBENCH_CH_PASSWORD": bootstrap.FIXTURE_PASSWORD,
        "CH_HOST": client.connection.host,
        "CH_PORT": str(client.connection.port),
        "CH_DATABASE": scope.database,
        "CH_USERNAME": client.connection.user,
        "CH_PASSWORD": client.connection.password,
        "CH25_EVAL_LOGGER_TABLE": scope.eval_table,
    }
    reader = None
    try:
        bootstrap.provision_fixture(env)
        bootstrap.provision_fixture(env)
        reader = Client(
            host=client.connection.host,
            port=client.connection.port,
            database=scope.database,
            user=bootstrap.ACCOUNT,
            password=bootstrap.FIXTURE_PASSWORD,
        )
        assert reader.execute(
            compile_query("SELECT count() AS n FROM spans", scope)
        ) == [(1,)]
        with pytest.raises(ServerException):
            reader.execute("SET max_result_rows=0")
        with pytest.raises(ServerException):
            reader.execute(
                f"INSERT INTO {scope.database}.spans (id) VALUES ('forbidden')"
            )
    finally:
        if reader is not None:
            reader.disconnect()
        client.execute(f"DROP USER IF EXISTS {bootstrap.ACCOUNT}")


def test_direct_bi_views_and_row_policies_are_tenant_safe(scoped_clickhouse):
    import ipaddress
    import secrets
    from clickhouse_driver import Client
    from clickhouse_driver.errors import ServerException
    from tracer.sql_workbench.provision import direct_access_ddl

    if (
        os.environ.get("SQL_WORKBENCH_TEST_DATABASE")
        and os.environ.get("SQL_WORKBENCH_ALLOW_DIRECT_TEST_NAMESPACE") != "true"
    ):
        pytest.skip(
            "Direct BI test needs explicit permission for its separate ephemeral view database."
        )
    client, scope, ids = scoped_clickhouse
    principal = f"sql_bi_{uuid4().hex[:12]}"
    view_database = f"sql_{principal}"
    password = secrets.token_urlsafe(32)
    address = ipaddress.ip_address(
        str(
            client.execute(
                "SELECT address FROM system.processes WHERE query_id=currentQueryID()"
            )[0][0]
        )
    )
    address = getattr(address, "ipv4_mapped", None) or address
    statements = direct_access_ddl(
        scope,
        database=scope.database,
        principal=principal,
        password=password,
        cidr=f"{address}/{32 if address.version == 4 else 128}",
    )
    created_users, created_policies = [], []
    created_database = False
    reader = None
    before = client.execute(f"SELECT count() FROM {scope.database}.spans")
    try:
        for statement in statements:
            client.execute(statement)
            if statement.startswith("CREATE USER "):
                created_users.append(statement.split()[2])
            elif statement.startswith("CREATE DATABASE "):
                created_database = True
            elif statement.startswith("CREATE ROW POLICY "):
                created_policies.append((statement.split()[3], statement.split()[5]))
        reader = Client(
            host=client.connection.host,
            port=client.connection.port,
            database=view_database,
            user=principal,
            password=password,
        )
        assert reader.execute("SELECT name FROM spans") == [("tenant-0",)]
        assert reader.execute(
            "SELECT s.locale, avg(e.score) FROM sessions s JOIN eval_results e ON s.id=e.session_id GROUP BY s.locale"
        ) == [("locale-0", 0.5)]
        assert reader.execute("SELECT id FROM annotations") == [(ids["annotation"],)]
        for forbidden in [
            f"SELECT count() FROM {scope.database}.spans",
            "SET max_execution_time=0",
            "SELECT count() FROM file('/etc/passwd', 'LineAsString')",
        ]:
            with pytest.raises(ServerException):
                reader.execute(forbidden)
        # Default server policy behavior must not silently deny existing readers.
        assert client.execute(f"SELECT count() FROM {scope.database}.spans") == before
    finally:
        if reader is not None:
            reader.disconnect()
        if created_database:
            client.execute(f"DROP DATABASE {view_database} SYNC")
        for policy, table in reversed(created_policies):
            client.execute(f"DROP ROW POLICY {policy} ON {table}")
        for user in reversed(created_users):
            client.execute(f"DROP USER {user}")


def test_session_locale_uses_latest_present_attribute_not_missing_child(
    scoped_clickhouse,
):
    client, scope, ids = scoped_clickhouse
    client.execute(
        f"INSERT INTO {scope.database}.spans (project_id,org_id,id,trace_id,trace_session_id,name,start_time) VALUES",
        [
            (
                scope.project_id,
                scope.organization_id,
                "newer-child",
                str(ids["trace"]),
                ids["session"],
                "without-locale",
                scope.end - timedelta(seconds=30),
            )
        ],
    )
    assert client.execute(compile_query("SELECT locale FROM sessions", scope)) == [
        ("locale-0",)
    ]
