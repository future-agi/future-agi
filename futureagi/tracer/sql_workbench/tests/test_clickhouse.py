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
    reused = set()
    tenants = []
    with _ch_test_native_client(owned_database=database) as client:
        if assigned:
            # Full migrations may already have installed the real native schema.
            # Reuse only empty, compatible tables; never replace/drop them.
            import sqlglot
            from sqlglot import exp

            existing = client.execute(
                "SELECT name, engine, total_rows FROM system.tables WHERE database=%(db)s AND name IN %(tables)s",
                {"db": database, "tables": tuple(TABLES)},
            )
            for table, engine, rows in existing:
                assert engine == "ReplacingMergeTree" and rows == 0, (
                    "SQL tests require an empty isolated source namespace"
                )
                expected = {
                    column.name
                    for column in sqlglot.parse_one(
                        f"CREATE TABLE x ({TABLES[table]})", read="clickhouse"
                    ).find_all(exp.ColumnDef)
                }
                if table in {"trace_sessions", "end_users"}:
                    expected.discard("_version")
                actual = {
                    row[0]
                    for row in client.execute(
                        "SELECT name FROM system.columns WHERE database=%(db)s AND table=%(table)s",
                        {"db": database, "table": table},
                    )
                }
                assert expected <= actual, (
                    "Installed SQL source lacks required catalog columns"
                )
                reused.add(table)
        else:
            client.execute(f"CREATE DATABASE {database}")
        try:
            for table, columns in TABLES.items():
                if table in reused:
                    continue
                key = (
                    "(project_id, id)"
                    if table == "spans"
                    else (
                        "trace_session_id"
                        if table == "trace_sessions"
                        else "end_user_id"
                        if table == "end_users"
                        else "id"
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
                    f"INSERT INTO {database}.tracer_eval_logger_v2 (id,trace_session_id,custom_eval_config_id,target_type,output_float,created_at,_version) VALUES",
                    [(evaluation, session, c, "session", 0.5, now, 0)],
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
                    (project, None, "null-org", str(ids[0]["trace"]), "null-org", now),
                    (
                        project,
                        tenants[1][0],
                        "foreign-org",
                        str(ids[0]["trace"]),
                        "foreign-org",
                        now,
                    ),
                ],
            )
            yield client, scope, ids[0]
        finally:
            if assigned:
                # Remove only this test's minted project/config rows from the
                # installed native tables. Preserve their schema and peer state.
                for table in sorted(reused):
                    if tenants:
                        key = (
                            "custom_eval_config_id"
                            if table == "tracer_eval_logger_v2"
                            else (
                                "tracer_project_id"
                                if table == "model_hub_score"
                                else "project_id"
                            )
                        )
                        ids = tuple(
                            t[3] if table == "tracer_eval_logger_v2" else t[2]
                            for t in tenants
                        )
                        client.execute(
                            f"ALTER TABLE {database}.{table} DELETE WHERE {key} IN %(ids)s",
                            {"ids": ids},
                            settings={"mutations_sync": 2},
                        )
                # Only tables created by this fixture may be dropped.
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


def test_real_locked_service_profile_executes_with_only_the_scope_filter(
    scoped_clickhouse, monkeypatch
):
    import ipaddress
    import secrets
    from django.test import override_settings
    from tracer.sql_workbench.catalog import additional_table_filters
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
                table_filters=additional_table_filters(scope),
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
        "cohort_id": "cohort-1",
        "cohort_frozen_at": scope.start.isoformat(),
        "membership_mode": "frozen",
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
    cohort = client.execute(
        compile_query(
            "SELECT cohort_id, cohort_frozen_at, membership_mode FROM eval_results",
            scope,
        )
    )
    assert cohort == [("cohort-1", scope.start, "frozen")]
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
        # The bootstrapped account accepts exactly the runner's scope filter.
        from tracer.sql_workbench.catalog import additional_table_filters

        assert reader.execute(
            f"SELECT count() FROM {scope.database}.spans",
            settings={"additional_table_filters": additional_table_filters(scope)},
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


def test_direct_bi_views_and_row_policies_are_tenant_safe(
    scoped_clickhouse, record_property
):
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
    principal = (
        os.environ.get("SQL_WORKBENCH_DIRECT_TEST_PRINCIPAL")
        or f"sql_bi_{uuid4().hex[:12]}"
    )
    assert re.fullmatch(r"sql_bi_[0-9a-f]{12}", principal)
    view_database = f"sql_{principal}"
    definer = f"sqld_{principal}"
    assert (
        client.execute(
            "SELECT name FROM system.users WHERE name IN %(names)s",
            {"names": (principal, definer)},
        )
        == []
    )
    assert (
        client.execute(
            "SELECT name FROM system.databases WHERE name=%(name)s",
            {"name": view_database},
        )
        == []
    )
    record_property(
        "sql_direct_objects",
        {
            "source_database": scope.database,
            "view_database": view_database,
            "principals": [principal, definer],
        },
    )
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
    injected_permissive_policy = False
    reader = None
    before = client.execute(f"SELECT count() FROM {scope.database}.spans")
    try:
        for statement in statements:
            client.execute(statement)
            if (
                statement.startswith("CREATE USER sqld_")
                and not injected_permissive_policy
            ):
                # Existing broad permissive source policy must not OR-bypass the
                # tenant filter; generated restrictive source policies are AND.
                client.execute(
                    f"CREATE ROW POLICY sql_test_broad_{principal} ON {scope.database}.spans USING 1 TO {definer}"
                )
                created_policies.append(
                    (f"sql_test_broad_{principal}", f"{scope.database}.spans")
                )
                injected_permissive_policy = True
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
            f"WITH toUUID('{scope.project_id}') AS project_id SELECT count() FROM traces"
        ) == [(1,)]
        assert reader.execute(
            f"WITH toUUID('{scope.organization_id}') AS organization_id, toUUID('{scope.workspace_id}') AS workspace_id, toUUID('{scope.project_id}') AS tracer_project_id SELECT count() FROM annotations"
        ) == [(1,)]

        assert reader.execute(
            "SELECT s.locale, avg(e.score) FROM sessions s JOIN eval_results e ON s.id=e.session_id GROUP BY s.locale"
        ) == [("locale-0", 0.5)]
        assert reader.execute("SELECT id FROM annotations") == [(ids["annotation"],)]
        other_project = client.execute(
            f"SELECT DISTINCT project_id FROM {scope.database}.spans WHERE project_id != %(project)s",
            {"project": scope.project_id},
        )[0][0]
        client.execute(
            f"CREATE VIEW {view_database}.foreign_spans AS SELECT id FROM {scope.database}.spans WHERE project_id = %(project)s",
            {"project": other_project},
        )
        assert client.execute("EXISTS TABLE system.query_log") == [(1,)]
        for forbidden in [
            f"SELECT count() FROM {scope.database}.spans",
            f"SELECT count() FROM {view_database}.foreign_spans",
            "SELECT count() FROM system.query_log",
            "SELECT count() FROM file('/does-not-exist-sql-probe', 'LineAsString')",
            "SELECT count() FROM url('http://127.0.0.1:1/sql-probe', 'LineAsString')",
            "SELECT count() FROM remote('127.0.0.1:1', 'default', 'sql_probe')",
        ]:
            with pytest.raises(ServerException) as denied:
                reader.execute(forbidden)
            assert denied.value.code == 497, (
                "Expected privilege denial, not syntax/network/source failure"
            )
        for forbidden in [
            "SET max_execution_time=0",
            "SET readonly=0",
            "SET prefer_column_name_to_alias=0",
            f"INSERT INTO {view_database}.spans (name) VALUES ('forbidden')",
            f"GRANT SELECT ON {scope.database}.spans TO {principal}",
        ]:
            with pytest.raises(ServerException) as denied:
                reader.execute(forbidden)
            assert denied.value.code in {164, 497}
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


def test_recent_backfill_evaluations_keep_old_owned_targets(scoped_clickhouse):
    client, scope, ids = scoped_clickhouse
    old = scope.start - timedelta(days=60)
    now = scope.end - timedelta(minutes=1)
    trace, session, span = uuid4(), uuid4(), "old-owned-span"
    client.execute(
        f"INSERT INTO {scope.database}.traces (project_id,id,session_id,name,created_at) VALUES",
        [(scope.project_id, trace, session, "old-trace", old)],
    )
    client.execute(
        f"INSERT INTO {scope.database}.trace_sessions (project_id,trace_session_id,external_session_id,first_seen) VALUES",
        [(scope.project_id, session, "old-session", old)],
    )
    client.execute(
        f"INSERT INTO {scope.database}.spans (project_id,org_id,id,trace_id,trace_session_id,name,start_time) VALUES",
        [
            (
                scope.project_id,
                scope.organization_id,
                span,
                str(trace),
                session,
                "old-span",
                old,
            )
        ],
    )
    recent_ids = [uuid4(), uuid4(), uuid4()]
    client.execute(
        f"INSERT INTO {scope.database}.tracer_eval_logger_v2 (id,trace_id,observation_span_id,trace_session_id,custom_eval_config_id,target_type,created_at) VALUES",
        [
            (recent_ids[0], trace, span, None, scope.eval_config_ids[0], "span", now),
            (recent_ids[1], trace, span, None, scope.eval_config_ids[0], "trace", now),
            (
                recent_ids[2],
                None,
                None,
                session,
                scope.eval_config_ids[0],
                "session",
                now,
            ),
        ],
    )
    returned = {
        row[0]
        for row in client.execute(compile_query("SELECT id FROM eval_results", scope))
    }
    assert returned == {ids["eval"], *recent_ids}
    # User CTE names cannot shadow the server's internal ownership candidates.
    shadow = "WITH __sql_eval_candidates AS (SELECT * FROM annotations) SELECT id FROM eval_results"
    assert {row[0] for row in client.execute(compile_query(shadow, scope))} == returned


def test_reviewed_boolean_combinator_and_date_queries_execute(scoped_clickhouse):
    client, scope, _ = scoped_clickhouse
    assert client.execute(
        compile_query(
            "SELECT count() FROM spans WHERE name='tenant-0' AND (cost=0 OR total_tokens>0)",
            scope,
        )
    ) == [(1,)]
    assert client.execute(
        compile_query(
            "SELECT count() FROM spans s JOIN spans t ON s.id=t.id AND s.trace_id=t.trace_id",
            scope,
        )
    ) == [(1,)]
    assert client.execute(
        compile_query(
            "SELECT sumIf(cost, name='tenant-0'), avgIf(cost, name='tenant-0'), countIf(name='tenant-0') FROM spans",
            scope,
        )
    ) == [(0, 0.0, 1)]
    rows = client.execute(
        compile_query(
            "SELECT toStartOfHour(start_time), toStartOfDay(start_time), dateDiff('day',start_time,now()), dateAdd('day',1,start_time) FROM spans WHERE start_time >= now()-INTERVAL 1 DAY",
            scope,
        )
    )
    assert len(rows) == 1 and rows[0][2] == 0
    assert client.execute(
        compile_query(
            "SELECT quantile(0.95)(latency_ms),quantileExact(0.5)(cost) FROM spans",
            scope,
        )
    ) == [(0.0, 0.0)]


def _sibling(name):
    """Load a sibling test module's corpus without relying on import paths."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"_sql_workbench_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _service_principal(client, scope, *, extra_grants=(), estimate=False):
    """An ephemeral locked service principal, as provision_sql_service emits it."""
    import ipaddress
    import secrets

    from tracer.sql_workbench.provision import service_access_ddl

    principal = f"sql_test_{uuid4().hex[:12]}"
    password = secrets.token_urlsafe(32)
    address = ipaddress.ip_address(
        str(
            client.execute(
                "SELECT address FROM system.processes WHERE query_id=currentQueryID()"
            )[0][0]
        )
    )
    address = getattr(address, "ipv4_mapped", None) or address
    statements = service_access_ddl(
        database=scope.database,
        principal=principal,
        password=password,
        cidr=f"{address}/{32 if address.version == 4 else 128}",
        eval_table=scope.eval_table,
        estimate=estimate,
    )
    statements += [f"GRANT SELECT ON {grant} TO {principal}" for grant in extra_grants]
    return principal, password, statements


def _service_settings(client, database):
    return {
        "CH25_HOST": client.connection.host,
        "CH25_TCP_PORT": client.connection.port,
        "CH25_DATABASE": database,
        "CH25_USER": client.connection.user,
    }


def _string_values(rows):
    for row in rows:
        for value in row:
            if isinstance(value, (list, tuple)):
                yield from _string_values([value])
            elif isinstance(value, dict):
                yield from (str(item) for pair in value.items() for item in pair)
            else:
                yield str(value)


def test_adversarial_corpus_never_returns_foreign_rows(scoped_clickhouse):
    """Compile, then execute, every corpus query against two real tenants.

    This exercises the AST barrier alone: the executing connection is the
    unrestricted test account whose current database is the source database,
    with no server-side table filter, so an unscoped ``IN spans`` or
    ``FROM spans`` would resolve to the physical table and return the other
    tenant's rows. A one-column probe table holds a foreign sentinel, which
    turns ``x IN <table>`` into a membership oracle; the raw oracles are run
    once unscoped to prove they would leak.
    """
    from clickhouse_driver.errors import ServerException

    from conftest import _ch_test_native_client
    from tracer.sql_workbench.catalog import CatalogUnavailable
    from tracer.sql_workbench.compiler import UnsafeQuery

    client, scope, ids = scoped_clickhouse
    database = scope.database
    compiler_cases = _sibling("test_compiler")
    fuzz = _sibling("test_compiler_fuzz")
    sentinel = f"foreign-sentinel-{uuid4().hex}"
    client.execute(
        f"CREATE TABLE {database}.sql_probe_foreign (v String) ENGINE=MergeTree ORDER BY v"
    )
    try:
        client.execute(
            f"INSERT INTO {database}.sql_probe_foreign VALUES", [(sentinel,)]
        )
        stored = set()
        for table in TABLES:
            stored |= set(
                _string_values(client.execute(f"SELECT * FROM {database}.{table}"))
            )
        visible = set()
        for dataset in [
            "spans",
            "traces",
            "sessions",
            "users",
            "eval_results",
            "annotations",
        ]:
            visible |= set(
                _string_values(
                    client.execute(compile_query(f"SELECT * FROM {dataset}", scope))
                )
            )
        # Distinctive values the tenant must never see (UUIDs, names, sentinel).
        foreign = {
            value
            for value in stored - visible
            if len(value) >= 6 and not value.replace(".", "").replace("-", "").isdigit()
        } | {sentinel, "leak"}
        assert {"tenant-1", "foreign-org", "null-org"} <= foreign
        oracles = [
            f"SELECT name FROM spans WHERE '{sentinel}' IN sql_probe_foreign",
            f"SELECT name FROM spans WHERE '{sentinel}' IN (sql_probe_foreign)",
            f"SELECT name FROM spans WHERE '{sentinel}' GLOBAL IN sql_probe_foreign",
            f"SELECT name FROM spans WHERE '{sentinel}' IN {database}.sql_probe_foreign",
            f"SELECT if('{sentinel}' IN sql_probe_foreign, 'leak', name) FROM spans",
            f"SELECT name FROM spans WHERE NOT '{sentinel}' NOT IN sql_probe_foreign",
            "SELECT name FROM spans WHERE (project_id, org_id, id, trace_id, trace_session_id, end_user_id, name, start_time, end_time, latency_ms, status, model, provider, total_tokens, cost, attrs_string, attrs_number, is_deleted, _version) IN spans",
        ]
        with _ch_test_native_client(
            database=database, owned_database=database
        ) as executor:
            # Control: run unscoped, each oracle does read the foreign rows. The
            # whole-row oracle compares nullable columns that are NULL in the
            # fixture, and NULL never matches IN by default; transform_null_in
            # makes the control show the read. The corpus never gets settings.
            for oracle in oracles:
                rows = executor.execute(oracle, settings={"transform_null_in": 1})
                assert foreign & set(_string_values(rows)), oracle
            corpus = (
                compiler_cases.VALID_QUERIES
                + compiler_cases.ADVERSARIAL_QUERIES
                + compiler_cases.IN_TABLE_BYPASSES
                + compiler_cases.TABLE_NAME_FUNCTIONS
                + compiler_cases.CLAUSE_SWEEP
                + compiler_cases.REVIEWED_GRAMMAR
                + compiler_cases.GRAMMAR_ESCAPES
                + compiler_cases.WINDOW_QUERIES
                + compiler_cases.WINDOW_REJECTIONS
                + [sql for sql, _ in fuzz.variants()]
                + oracles
            )
            outcomes = {"rejected": 0, "executed": 0, "server_error": 0}
            for query in corpus:
                try:
                    compiled = compile_query(query, scope)
                except (UnsafeQuery, CatalogUnavailable):
                    outcomes["rejected"] += 1
                    continue
                assert query not in oracles, f"membership oracle compiled: {query}"
                try:
                    rows = executor.execute(compiled)
                except ServerException:
                    outcomes["server_error"] += 1
                    continue
                outcomes["executed"] += 1
                leaked = foreign & set(_string_values(rows))
                assert not leaked, f"{query!r} returned foreign values {leaked}"
        print(f"live adversarial corpus: {len(corpus)} queries, {outcomes}")
        assert outcomes["executed"] >= len(compiler_cases.VALID_QUERIES)
    finally:
        client.execute(f"DROP TABLE {database}.sql_probe_foreign SYNC")


def test_server_table_filters_block_unscoped_sql_under_the_locked_profile(
    scoped_clickhouse, monkeypatch
):
    """Second barrier: raw, unscoped SQL through the runner still sees one scope.

    The SQL below bypasses the compiler on purpose, standing in for an AST
    bypass: raw physical reads (bare and database-qualified), IN-table and
    function-form IN oracles over an exact foreign row, alias and CTE names on
    the right of IN, and a read through a view. The test account's control
    reads show the foreign rows exist. A 1,000-configuration scope checks that
    the largest admitted filter map is accepted by the server.
    """
    from dataclasses import replace

    from clickhouse_driver import Client
    from clickhouse_driver.errors import ServerException
    from django.test import override_settings

    from conftest import _ch_test_native_client
    from tracer.sql_workbench.catalog import additional_table_filters
    from tracer.sql_workbench.runner import run

    client, scope, ids = scoped_clickhouse
    database = scope.database
    project = f"toUUID('{scope.project_id}')"
    view = f"{database}.sql_probe_spans_view"
    client.execute(
        f"CREATE VIEW {view} AS SELECT project_id, id, name FROM {database}.spans"
    )
    principal, password, statements = _service_principal(
        client, scope, extra_grants=[view]
    )
    created = False
    try:
        for statement in statements:
            client.execute(statement)
            created = True
        foreign_session = client.execute(
            f"SELECT project_id, trace_session_id, external_session_id, first_seen, is_deleted, _version FROM {database}.trace_sessions WHERE project_id != %(p)s",
            {"p": scope.project_id},
        )[0]
        row = (
            f"(toUUID('{foreign_session[0]}'), toUUID('{foreign_session[1]}'), "
            f"'{foreign_session[2]}', toDateTime64('"
            f"{foreign_session[3].strftime('%Y-%m-%d %H:%M:%S.%f')}', 6, 'UTC'), "
            f"{foreign_session[4]}, {foreign_session[5]})"
        )
        session = f"toUUID('{foreign_session[1]}')"
        config = scope.eval_config_ids[0]
        raw = [
            f"SELECT count() FROM spans WHERE project_id != {project}",
            f"SELECT count() FROM {database}.spans WHERE project_id != {project}",
            f"SELECT count() FROM `{database}`.`traces` WHERE project_id != {project}",
            f"SELECT count() FROM end_users WHERE project_id != {project}",
            "SELECT count() FROM model_hub_score WHERE value IN ('value-1', 'null-scope', 'foreign-scope')",
            f"SELECT count() FROM tracer_eval_logger_v2 WHERE custom_eval_config_id != toUUID('{config}')",
            f"SELECT count() FROM {view} WHERE project_id != {project}",
            f"SELECT count() FROM spans FINAL WHERE project_id != {project}",
            f"SELECT {session} IN (SELECT trace_session_id FROM trace_sessions)",
            f"SELECT {row} IN trace_sessions",
            f"SELECT {row} IN (trace_sessions)",
            f"SELECT {row} IN {database}.trace_sessions",
            f"SELECT {row} GLOBAL IN trace_sessions",
            f"SELECT {row} IN `trace_sessions`",
            f"SELECT in({row}, trace_sessions)",
            f"SELECT globalIn({row}, trace_sessions)",
            f"SELECT count() FROM spans AS a JOIN spans AS b ON a.id = b.id WHERE b.project_id != {project}",
            # ClickHouse reads ``NOT (a, b)`` as the function not(a, b).
            f"SELECT NOT ({row} NOT IN trace_sessions)",
            f"SELECT {row} IN t FROM trace_sessions AS t WHERE project_id = {project}",
            f"WITH c AS (SELECT trace_session_id FROM trace_sessions) SELECT {session} IN c",
            f"SELECT count() FROM spans WHERE {session} IN (SELECT trace_session_id FROM trace_sessions) AND project_id != {project}",
            f"SELECT EXISTS (SELECT 1 FROM trace_sessions WHERE trace_session_id = {session})",
            f"SELECT (SELECT count() FROM traces WHERE project_id != {project})",
        ]
        with _ch_test_native_client(
            database=database, owned_database=database
        ) as unrestricted:
            for sql in raw:
                assert unrestricted.execute(sql)[0][0], (
                    f"control has no foreign row: {sql}"
                )
        monkeypatch.setenv("SQL_WORKBENCH_CH_USER", principal)
        monkeypatch.setenv("SQL_WORKBENCH_CH_PASSWORD", password)
        monkeypatch.setenv("SQL_WORKBENCH_CH_SECURE", "false")
        many = replace(scope, eval_config_ids=(config, *(uuid4() for _ in range(999))))
        with override_settings(CLICKHOUSE_V2=_service_settings(client, database)):
            for filters in (
                additional_table_filters(scope),
                additional_table_filters(many),
            ):
                for sql in raw:
                    result = run(sql, uuid4(), lambda: False, table_filters=filters)
                    assert result["rows"] in ([[0]], [[False]]), (sql, result["rows"])
                own = run(
                    f"SELECT name FROM {database}.spans ORDER BY name",
                    uuid4(),
                    lambda: False,
                    table_filters=filters,
                )
                assert own["rows"] == [["tenant-0"]]
                evals = run(
                    "SELECT count() FROM tracer_eval_logger_v2",
                    uuid4(),
                    lambda: False,
                    table_filters=filters,
                )
                # The owned config's rows only (one tenant-0 session score plus
                # the four adversarial target rows the fixture adds to it).
                assert evals["rows"] == [[5]]
        # Only the filter is changeable: every other per-query setting is refused.
        reader = Client(
            host=client.connection.host,
            port=client.connection.port,
            database=database,
            user=principal,
            password=password,
        )
        try:
            for setting in [
                {"max_threads": 64},
                {"readonly": 0},
                {"max_execution_time": 0},
                {"max_rows_to_read": 0},
            ]:
                with pytest.raises(ServerException) as denied:
                    reader.execute("SELECT 1", settings=setting)
                assert denied.value.code == 164
        finally:
            reader.disconnect()
    finally:
        if created:
            client.execute(f"DROP USER {principal}")
        client.execute(f"DROP VIEW {view} SYNC")


def test_server_table_filters_cover_distributed_and_shard_reads(
    scoped_clickhouse, monkeypatch
):
    """The ``<table>_local`` keys: a cluster's Distributed source and its shard.

    A second database mirrors a cluster installation: ``spans`` is a
    Distributed table over ``spans_local`` on a one-shard cluster whose only
    replica is this server. Raw, unscoped reads of either relation, including
    IN-table and GLOBAL IN forms, must see only the scope's rows.
    """
    from dataclasses import replace

    from django.test import override_settings

    from conftest import _ch_test_native_client
    from tracer.sql_workbench.catalog import additional_table_filters
    from tracer.sql_workbench.runner import run

    client, scope, _ = scoped_clickhouse
    clusters = client.execute(
        "SELECT cluster FROM system.clusters GROUP BY cluster "
        "HAVING max(shard_num) = 1 AND count() = 1 AND max(is_local) = 1 "
        "ORDER BY cluster LIMIT 1"
    )
    if not clusters:
        pytest.skip("no one-node cluster that resolves to this server")
    cluster = clusters[0][0]
    database = f"{scope.database}_d"
    sharded = replace(scope, database=database)
    project = f"toUUID('{scope.project_id}')"
    with _ch_test_native_client(owned_database=database) as admin:
        admin.execute(f"CREATE DATABASE {database}")
        principal = None
        try:
            admin.execute(
                f"CREATE TABLE {database}.spans_local ({TABLES['spans']}) "
                "ENGINE=ReplacingMergeTree(_version) ORDER BY (project_id, id)"
            )
            admin.execute(
                f"CREATE TABLE {database}.spans AS {database}.spans_local "
                f"ENGINE=Distributed('{cluster}', '{database}', 'spans_local')"
            )
            columns = ", ".join(
                column.split()[0] for column in TABLES["spans"].split(", ")
            )
            admin.execute(
                f"INSERT INTO {database}.spans_local ({columns}) "
                f"SELECT {columns} FROM {scope.database}.spans"
            )
            assert admin.execute(
                f"SELECT count() FROM {database}.spans WHERE project_id != {project}"
            )[0][0], "control: the Distributed table has no foreign row"
            principal, password, statements = _service_principal(
                client, sharded, extra_grants=[f"{database}.spans_local"]
            )
            for statement in statements:
                client.execute(statement)
            foreign = admin.execute(
                f"SELECT project_id, org_id, id FROM {database}.spans_local "
                f"WHERE project_id != {project} LIMIT 1"
            )[0]
            row = f"(toUUID('{foreign[0]}'), toUUID('{foreign[1]}'), '{foreign[2]}')"
            raw = [
                f"SELECT count() FROM spans WHERE project_id != {project}",
                f"SELECT count() FROM {database}.spans WHERE project_id != {project}",
                f"SELECT count() FROM spans_local WHERE project_id != {project}",
                f"SELECT count() FROM spans FINAL WHERE project_id != {project}",
                f"SELECT {row} IN (SELECT project_id, org_id, id FROM spans)",
                f"SELECT {row} GLOBAL IN (SELECT project_id, org_id, id FROM spans)",
                f"SELECT {row} IN (SELECT project_id, org_id, id FROM spans_local)",
                f"SELECT count() FROM spans AS a JOIN spans_local AS b ON a.id = b.id "
                f"WHERE b.project_id != {project}",
            ]
            for sql in raw:
                assert admin.execute(sql.replace(" spans", f" {database}.spans"))[0][
                    0
                ], f"control has no foreign row: {sql}"
            monkeypatch.setenv("SQL_WORKBENCH_CH_USER", principal)
            monkeypatch.setenv("SQL_WORKBENCH_CH_PASSWORD", password)
            monkeypatch.setenv("SQL_WORKBENCH_CH_SECURE", "false")
            filters = additional_table_filters(sharded)
            with override_settings(CLICKHOUSE_V2=_service_settings(client, database)):
                for sql in raw:
                    result = run(sql, uuid4(), lambda: False, table_filters=filters)
                    assert result["rows"] in ([[0]], [[False]]), (sql, result["rows"])
                own = run(
                    "SELECT name FROM spans ORDER BY name",
                    uuid4(),
                    lambda: False,
                    table_filters=filters,
                )
            assert own["rows"] == [["tenant-0"]]
        finally:
            if principal:
                client.execute(f"DROP USER IF EXISTS {principal}")
            admin.execute(f"DROP DATABASE IF EXISTS {database} SYNC")


def _fill_owned_spans(client, scope, rows=60_000):
    # 60 blocks of 1,000 owned rows; at 0.5 ms per row a full scan takes far
    # longer than any cancellation bound asserted below.
    client.execute(
        f"INSERT INTO {scope.database}.spans (project_id, org_id, id, trace_id, name, start_time) "
        f"SELECT toUUID('{scope.project_id}'), toUUID('{scope.organization_id}'), "
        "concat('filler-', toString(number)), 'filler-trace', 'filler', now64(6, 'UTC') "
        f"FROM numbers({rows})"
    )


def _running(client, query_id):
    return client.execute(
        "SELECT count() FROM system.processes WHERE query_id=%(q)s",
        {"q": str(query_id)},
    )[0][0]


def test_native_cancel_stops_a_running_scan_on_the_real_server(
    scoped_clickhouse, monkeypatch
):
    """A long scan on the locked profile ends promptly after a cancel signal."""
    import time

    from django.test import override_settings

    from tracer.sql_workbench.catalog import additional_table_filters
    from tracer.sql_workbench.runner import QueryCancelled, run

    client, scope, _ = scoped_clickhouse
    _fill_owned_spans(client, scope)
    principal, password, statements = _service_principal(client, scope)
    created = False
    try:
        for statement in statements:
            client.execute(statement)
            created = True
        monkeypatch.setenv("SQL_WORKBENCH_CH_USER", principal)
        monkeypatch.setenv("SQL_WORKBENCH_CH_PASSWORD", password)
        monkeypatch.setenv("SQL_WORKBENCH_CH_SECURE", "false")
        query_id = uuid4()
        started = time.monotonic()
        with override_settings(CLICKHOUSE_V2=_service_settings(client, scope.database)):
            with pytest.raises(QueryCancelled):
                run(
                    f"SELECT sum(sleepEachRow(0.0005)) FROM {scope.database}.spans",
                    query_id,
                    lambda: time.monotonic() - started > 1.0,
                    table_filters=additional_table_filters(scope),
                )
        elapsed = time.monotonic() - started
        # Cancel observed at the next 0.5 s watchdog poll, then the server stops
        # between 0.5 s blocks: far below the time the full scan would take.
        assert elapsed < 6, elapsed
        for _ in range(30):
            if not _running(client, query_id):
                break
            time.sleep(0.1)
        assert _running(client, query_id) == 0
        print(f"native cancel returned after {elapsed:.2f}s")
    finally:
        if created:
            client.execute(f"DROP USER {principal}")


@pytest.mark.django_db(transaction=True)
def test_service_cancel_holds_the_slot_until_the_native_scan_has_stopped(
    scoped_clickhouse, monkeypatch
):
    """End to end on real PostgreSQL and ClickHouse: submit, run, cancel.

    The worker runs in a thread exactly as the Temporal activity body does,
    against the locked service principal. Only the compile step is replaced,
    with a deliberately slow scan of the tenant's own rows (the catalog has
    no sleep function), so a cancel arrives while ClickHouse is executing.
    """
    import threading
    import time

    from django.test import override_settings
    from django.utils import timezone as django_timezone

    from accounts.models.organization import Organization
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.user import User
    from accounts.models.workspace import Workspace, WorkspaceMembership
    from model_hub.models.ai_model import AIModel
    from tracer.models.project import Project
    from tracer.models.sql_query import SQLExecution
    from tracer.sql_workbench import service, tasks
    from tracer.sql_workbench.access import Actor

    client, scope, _ = scoped_clickhouse
    _fill_owned_spans(client, scope)
    org = Organization.objects.create(id=scope.organization_id, name=f"sql-{uuid4()}")
    user = User(
        id=uuid4(), email=f"sql-{uuid4()}@example.test", name="SQL", organization=org
    )
    User.objects.bulk_create([user])
    workspace = Workspace(
        id=scope.workspace_id, name="sql-live", organization=org, created_by=user
    )
    Workspace.no_workspace_objects.bulk_create([workspace])
    membership = OrganizationMembership(
        id=uuid4(), user=user, organization=org, level=3
    )
    OrganizationMembership.no_workspace_objects.bulk_create([membership])
    WorkspaceMembership.no_workspace_objects.bulk_create(
        [
            WorkspaceMembership(
                id=uuid4(),
                user=user,
                workspace=workspace,
                level=3,
                organization_membership=membership,
            )
        ]
    )
    project = Project(
        id=scope.project_id,
        name="sql-live",
        organization=org,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    Project.no_workspace_objects.bulk_create([project])
    actor = Actor(user.id, org.id, workspace.id)
    principal, password, statements = _service_principal(client, scope)
    created = False
    workers = []

    def dispatch(args, **_):
        worker = threading.Thread(target=tasks.execute_sql, args=args, daemon=True)
        workers.append(worker)
        worker.start()

    try:
        for statement in statements:
            client.execute(statement)
            created = True
        monkeypatch.setenv("SQL_WORKBENCH_CH_USER", principal)
        monkeypatch.setenv("SQL_WORKBENCH_CH_PASSWORD", password)
        monkeypatch.setenv("SQL_WORKBENCH_CH_SECURE", "false")
        monkeypatch.setattr(
            tasks,
            "compile_query",
            lambda sql, scope: (
                f"SELECT sum(sleepEachRow(0.0005)) FROM {scope.database}.spans"
            ),
        )
        monkeypatch.setattr(tasks.execute_sql, "apply_async", dispatch)
        with override_settings(
            CLICKHOUSE_V2=_service_settings(client, scope.database),
            CH25_EVAL_LOGGER_TABLE=scope.eval_table,
        ):
            job = service.submit(
                actor,
                project_id=project.id,
                sql="SELECT count() FROM spans",
                start=scope.start,
                end=scope.end,
            )
            deadline = time.monotonic() + 20
            while not _running(client, job.id) and time.monotonic() < deadline:
                time.sleep(0.1)
            assert _running(client, job.id) == 1, SQLExecution.objects.get(id=job.id)
            requested = time.monotonic()
            snapshot = service.cancel(actor, project.id, job.id)
            waited = time.monotonic() - requested
            assert snapshot["state"] == "cancelled"
            assert waited < service.CANCEL_CONFIRM_SECONDS + 1

            def holds_slot():
                return SQLExecution.objects.filter(
                    service.active_filter(django_timezone.now()), id=job.id
                ).exists()

            job.refresh_from_db()
            confirmed_in_cancel = job.finished_at is not None
            if not confirmed_in_cancel:
                # A slow host: cancel() stopped waiting, but the slot is still
                # held while the worker has not confirmed the stop.
                assert holds_slot()
            for worker in workers:
                worker.join(timeout=15)
                assert not worker.is_alive()
            job.refresh_from_db()
            assert job.state == "cancelled" and job.finished_at is not None
            assert not holds_slot()
            # Confirmation means run() returned after the server ended the scan.
            for _ in range(20):
                if not _running(client, job.id):
                    break
                time.sleep(0.1)
            assert _running(client, job.id) == 0
            print(
                f"cancel returned after {waited:.2f}s; "
                f"confirmed before returning: {confirmed_in_cancel}"
            )
    finally:
        if created:
            client.execute(f"DROP USER {principal}")


def _live_people(scope, count):
    """PostgreSQL organization, workspace, project and ``count`` members whose
    ids match the seeded ClickHouse scope, for end-to-end service tests."""
    from accounts.models.organization import Organization
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.user import User
    from accounts.models.workspace import Workspace, WorkspaceMembership
    from model_hub.models.ai_model import AIModel
    from tracer.models.project import Project
    from tracer.sql_workbench.access import Actor

    org = Organization.objects.create(id=scope.organization_id, name=f"sql-{uuid4()}")
    workspace = None
    actors = []
    for _ in range(count):
        user = User(
            id=uuid4(),
            email=f"sql-{uuid4()}@example.test",
            name="SQL",
            organization=org,
        )
        User.objects.bulk_create([user])
        if workspace is None:
            workspace = Workspace(
                id=scope.workspace_id,
                name="sql-live",
                organization=org,
                created_by=user,
            )
            Workspace.no_workspace_objects.bulk_create([workspace])
        membership = OrganizationMembership(
            id=uuid4(), user=user, organization=org, level=3
        )
        OrganizationMembership.no_workspace_objects.bulk_create([membership])
        WorkspaceMembership.no_workspace_objects.bulk_create(
            [
                WorkspaceMembership(
                    id=uuid4(),
                    user=user,
                    workspace=workspace,
                    level=3,
                    organization_membership=membership,
                )
            ]
        )
        actors.append(Actor(user.id, org.id, workspace.id))
    project = Project(
        id=scope.project_id,
        name="sql-live",
        organization=org,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    Project.no_workspace_objects.bulk_create([project])
    # Service scope comes from PostgreSQL, not the ClickHouse fixture's Scope.
    # Without these owners, every eval join would pass over an empty source.
    from tracer.models.custom_eval_config import CustomEvalConfig
    from tracer.tests.eval_task_factories import make_template

    template = make_template(organization=org, workspace=workspace)
    CustomEvalConfig.no_workspace_objects.bulk_create(
        [
            CustomEvalConfig(
                id=config_id,
                project=project,
                eval_template=template,
                name=f"sql-{config_id}",
            )
            for config_id in scope.eval_config_ids
        ]
    )
    return actors, project


def _until_done(job_id, seconds=30):
    import time

    from tracer.models.sql_query import SQLExecution

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        job = SQLExecution.objects.get(id=job_id)
        if job.state not in ("queued", "running"):
            return job
        time.sleep(0.01)
    raise AssertionError("query did not finish")


@pytest.mark.django_db(transaction=True)
def test_result_reuse_answers_without_a_scan_and_only_for_the_same_person(
    scoped_clickhouse, monkeypatch
):
    """End to end on real PostgreSQL and ClickHouse: the first submit runs the
    real worker and runner on the locked principal; an identical submit by
    the same person returns that completed result without dispatching a scan,
    while a colleague in the same project runs their own. Prints the measured
    submit-to-rows latency of both paths."""
    import threading
    import time

    from django.test import override_settings

    from tracer.sql_workbench import service, tasks

    client, scope, _ = scoped_clickhouse
    actors, project = _live_people(scope, 2)
    principal, password, statements = _service_principal(client, scope)
    created = False
    workers = []

    def dispatch(args, **_):
        worker = threading.Thread(target=tasks.execute_sql, args=args, daemon=True)
        workers.append(worker)
        worker.start()

    sql = "SELECT model, count() AS n FROM spans GROUP BY model ORDER BY model"
    try:
        for statement in statements:
            client.execute(statement)
            created = True
        monkeypatch.setenv("SQL_WORKBENCH_CH_USER", principal)
        monkeypatch.setenv("SQL_WORKBENCH_CH_PASSWORD", password)
        monkeypatch.setenv("SQL_WORKBENCH_CH_SECURE", "false")
        monkeypatch.setattr(tasks.execute_sql, "apply_async", dispatch)
        with override_settings(
            CLICKHOUSE_V2=_service_settings(client, scope.database),
            CH25_EVAL_LOGGER_TABLE=scope.eval_table,
            SQL_WORKBENCH_RESULT_CACHE_SECONDS=60,
        ):
            owner, colleague = actors
            started = time.monotonic()
            first = service.submit(
                owner, project_id=project.id, sql=sql, start=scope.start, end=scope.end
            )
            first = _until_done(first.id)
            uncached = time.monotonic() - started
            assert first.state == "succeeded", first.error
            assert len(workers) == 1
            started = time.monotonic()
            again = service.submit(
                owner,
                project_id=project.id,
                sql=sql.lower(),
                start=scope.start,
                end=scope.end,
            )
            cached = time.monotonic() - started
            assert again.id == first.id and again.state == "succeeded"
            assert service.snapshot(again)["result"]["rows"] == first.result["rows"]
            assert len(workers) == 1  # no new scan was dispatched
            peer = _until_done(
                service.submit(
                    colleague,
                    project_id=project.id,
                    sql=sql,
                    start=scope.start,
                    end=scope.end,
                ).id
            )
            assert peer.id != first.id and len(workers) == 2
            assert peer.result["rows"] == first.result["rows"]
            minute = (scope.end - timedelta(minutes=5)).replace(second=0, microsecond=0)
            client.execute(
                f"INSERT INTO {scope.database}.spans (project_id,org_id,id,trace_id,name,start_time,model) VALUES",
                [
                    (
                        scope.project_id,
                        scope.organization_id,
                        f"exact-{second}",
                        str(uuid4()),
                        f"window-{second}",
                        minute + timedelta(seconds=second, microseconds=500000),
                        "exact-window",
                    )
                    for second in (1, 40)
                ],
            )
            exact_jobs = []
            for second in (1, 40):
                job = _until_done(
                    service.submit(
                        owner,
                        project_id=project.id,
                        sql="SELECT name FROM spans WHERE model = 'exact-window'",
                        start=minute + timedelta(seconds=second),
                        end=minute + timedelta(seconds=second + 1),
                    ).id
                )
                assert job.state == "succeeded", job.error
                assert job.result["rows"] == [[f"window-{second}"]]
                exact_jobs.append(job.id)
            assert exact_jobs[0] != exact_jobs[1]
            assert len(workers) == 4
            for worker in workers:
                worker.join(timeout=15)
            assert cached < uncached
            print(
                f"result reuse: uncached submit-to-rows {uncached * 1000:.0f} ms "
                f"(runner {first.result['elapsed_ms']} ms), reused {cached * 1000:.1f} ms"
            )
    finally:
        if created:
            client.execute(f"DROP USER {principal}")


@pytest.mark.django_db(transaction=True)
def test_capability_matrix_is_regenerated_live(scoped_clickhouse, monkeypatch):
    """Every case of scripts/sql_workbench/capability_check.py through the
    production path on real PostgreSQL and ClickHouse, each status asserted
    against its expectation. Writes the matrix to SQL_CAPABILITY_MATRIX_OUT
    when set (the script's output)."""
    import csv
    import importlib.util
    import io
    import json
    import os
    import threading
    from pathlib import Path

    import pyarrow.parquet as pq
    import sqlglot
    from django.test import override_settings
    from django.utils import timezone as django_timezone

    from tracer.models.sql_query import SQLExecution
    from tracer.sql_workbench import service, tasks
    from tracer.sql_workbench.catalog import CATALOG_VERSION, CatalogUnavailable
    from tracer.sql_workbench.compiler import UnsafeQuery

    script = Path(__file__).resolve().parents[3] / (
        "scripts/sql_workbench/capability_check.py"
    )
    spec = importlib.util.spec_from_file_location("sql_capability_check", script)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)

    client, scope, ids = scoped_clickhouse
    (actor,), project = _live_people(scope, 1)
    # Match the span-targeted joins as well as the session-targeted base row.
    # Both target kinds are owned; the fixture also has foreign/null sentinels.
    created_at = scope.end - timedelta(minutes=1)
    client.execute(
        f"INSERT INTO {scope.database}.{scope.eval_table} "
        "(id,trace_id,observation_span_id,custom_eval_config_id,target_type,"
        "output_float,created_at) VALUES",
        [
            (
                uuid4(),
                ids["trace"],
                ids["span"],
                scope.eval_config_ids[0],
                "span",
                0.75,
                created_at,
            )
        ],
    )
    client.execute(
        f"INSERT INTO {scope.database}.model_hub_score "
        "(id,organization_id,workspace_id,tracer_project_id,trace_id,"
        "observation_span_id,label_id,value,created_at) VALUES",
        [
            (
                uuid4(),
                scope.organization_id,
                scope.workspace_id,
                scope.project_id,
                ids["trace"],
                ids["span"],
                uuid4(),
                "approved",
                created_at,
            )
        ],
    )
    principal, password, statements = _service_principal(client, scope)
    estimator, estimate_password, estimate_statements = _service_principal(
        client, scope, estimate=True
    )
    created = estimate_created = False
    workers, held = [], []

    def dispatch(args, **_):
        if held:
            return  # Leave this one queued: the cancel case needs it.
        worker = threading.Thread(target=tasks.execute_sql, args=args, daemon=True)
        workers.append(worker)
        worker.start()

    def submit(sql):
        return service.submit(
            actor, project_id=project.id, sql=sql, start=scope.start, end=scope.end
        )

    def execute(sql):
        try:
            job = submit(sql)
        except (UnsafeQuery, CatalogUnavailable) as exc:
            return {"status": checker.REJECTED, "reason": str(exc)}
        job = _until_done(job.id)
        if job.state == "succeeded":
            if not job.result["row_count"]:
                return {
                    "status": checker.FAILED,
                    "reason": "Seeded query returned no rows",
                }
            return {"status": checker.SUPPORTED, "rows": job.result["row_count"]}
        return {"status": checker.FAILED, "reason": job.error}

    results = {}
    try:
        for statement in estimate_statements:
            client.execute(statement)
            estimate_created = True
        monkeypatch.setenv("SQL_WORKBENCH_ESTIMATE_CH_USER", estimator)
        monkeypatch.setenv("SQL_WORKBENCH_ESTIMATE_CH_PASSWORD", estimate_password)
        for statement in statements:
            client.execute(statement)
            created = True
        monkeypatch.setenv("SQL_WORKBENCH_CH_USER", principal)
        monkeypatch.setenv("SQL_WORKBENCH_CH_PASSWORD", password)
        monkeypatch.setenv("SQL_WORKBENCH_CH_SECURE", "false")
        monkeypatch.setattr(tasks.execute_sql, "apply_async", dispatch)
        with override_settings(
            CLICKHOUSE_V2=_service_settings(client, scope.database),
            CH25_EVAL_LOGGER_TABLE=scope.eval_table,
            SQL_WORKBENCH_RESULT_CACHE_SECONDS=60,
        ):
            for case_id, _, sql, _ in checker.QUERIES:
                results[case_id] = execute(sql)

            def lifecycle(case_id, check):
                try:
                    results[case_id] = check()
                except Exception as exc:  # recorded, then asserted below
                    results[case_id] = {
                        "status": checker.FAILED,
                        "reason": f"{type(exc).__name__}: {exc}",
                    }

            lifecycle_sql = "SELECT name, count() AS n FROM spans GROUP BY name"

            def async_lifecycle():
                # The scan runs on a worker thread, not inside submit; the
                # owner then reads the result by polling the execution id.
                before = len(workers)
                job = submit(lifecycle_sql)
                _until_done(job.id)
                polled = service.snapshot(service.execution(actor, project.id, job.id))
                ok = len(workers) == before + 1 and polled["state"] == "succeeded"
                return {
                    "status": checker.SUPPORTED if ok else checker.FAILED,
                    "rows": polled["result"].get("row_count"),
                    "reason": "" if ok else polled["state"],
                }

            def cancel_queued():
                held.append(True)
                try:
                    job = submit("SELECT count() AS n FROM traces")
                finally:
                    held.clear()
                snapshot = service.cancel(actor, project.id, job.id)
                still_active = SQLExecution.objects.filter(
                    service.active_filter(django_timezone.now()), id=job.id
                ).exists()
                ok = snapshot["state"] == "cancelled" and not still_active
                return {"status": checker.SUPPORTED if ok else checker.FAILED}

            def saved_query():
                saved = service.save_query(
                    actor,
                    project_id=project.id,
                    name="capability check",
                    sql="SELECT model, count() AS n FROM spans GROUP BY model",
                    visibility="project",
                    chart={"type": "table"},
                    window_hours=1,
                )
                again = service.saved_query(actor, project.id, saved.id)
                job = _until_done(submit(again.sql).id)
                ok = job.state == "succeeded"
                return {
                    "status": checker.SUPPORTED if ok else checker.FAILED,
                    "rows": job.result.get("row_count"),
                }

            exported = _until_done(
                submit("SELECT id, name, latency_ms FROM spans ORDER BY id").id
            )

            def export_csv():
                payload, kind = service.export_result(
                    actor, project.id, exported.id, "csv"
                )
                rows = list(csv.reader(io.StringIO(payload.decode("utf-8-sig"))))
                ok = (
                    kind == "text/csv" and len(rows) - 1 == exported.result["row_count"]
                )
                return {
                    "status": checker.SUPPORTED if ok else checker.FAILED,
                    "rows": len(rows) - 1,
                }

            def export_parquet():
                payload, _ = service.export_result(
                    actor, project.id, exported.id, "parquet"
                )
                table = pq.read_table(io.BytesIO(payload))
                ok = table.num_rows == exported.result["row_count"]
                return {
                    "status": checker.SUPPORTED if ok else checker.FAILED,
                    "rows": table.num_rows,
                }

            def estimate():
                value = service.estimate(
                    actor,
                    project_id=project.id,
                    sql="SELECT count() FROM spans",
                    start=scope.start,
                    end=scope.end,
                )
                ok = value["status"] == "ok" and value["exceeds_scan_bound"] is False
                return {
                    "status": checker.SUPPORTED if ok else checker.FAILED,
                    "reason": "" if ok else str(value),
                }

            def result_reuse():
                before = len(workers)
                first = _until_done(submit(lifecycle_sql + " ORDER BY name").id)
                again = submit(lifecycle_sql.lower() + " ORDER BY name")
                ok = again.id == first.id and len(workers) == before + 1
                return {"status": checker.SUPPORTED if ok else checker.FAILED}

            for case_id, check in [
                ("async-lifecycle", async_lifecycle),
                ("cancel-queued", cancel_queued),
                ("saved-query", saved_query),
                ("export-csv", export_csv),
                ("export-parquet", export_parquet),
                ("pre-run-estimate", estimate),
                ("result-reuse", result_reuse),
            ]:
                lifecycle(case_id, check)
            for worker in workers:
                worker.join(timeout=15)
        version = client.execute("SELECT version()")[0][0]
        report = checker.matrix(
            results,
            {
                "clickhouse": version,
                "sqlglot": sqlglot.__version__,
                "catalog_version": CATALOG_VERSION,
            },
        )
        target = os.environ.get("SQL_CAPABILITY_MATRIX_OUT")
        if target:
            Path(target).write_text(json.dumps(report, indent=1))
        mismatched = {
            case["id"]: (case["status"], case["reason"])
            for case in report["cases"]
            if case["status"] != case["expected"]
        }
        assert mismatched == {}
        assert report["matches_expectations"]
        print("capability matrix:", json.dumps(report["summary"], sort_keys=True))
    finally:
        if created:
            client.execute(f"DROP USER {principal}")
        if estimate_created:
            client.execute(f"DROP USER {estimator}")
