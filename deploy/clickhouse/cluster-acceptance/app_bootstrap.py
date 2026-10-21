"""Real migrated PostgreSQL -> actual cluster bootstrap/outbox qualification.

No mocked source inventory, toy mirrors, Temporal server or external credentials.
This proves the schema and capture/drain path, not the entire UI/worker stack.
"""

import os
import time
import uuid
from datetime import UTC, datetime


def prepare(fixture):
    from runtime_paths import enter_backend_root

    enter_backend_root()
    from fixture_connections import (
        configure,
        probe_django_postgres,
        probe_postgres,
        verify,
    )

    configure(fixture)
    verify(fixture)

    import django
    import psycopg

    django.setup()
    verify(fixture)
    from django.core.management import call_command

    # Only this generated project's fresh PG is touched. Wait for its server,
    # then run the real application's migrations rather than recreating a model
    # subset from an approximate fixture schema.
    deadline = time.monotonic() + 90
    while True:
        try:
            probe_postgres(
                port=fixture["pg_port"],
                database="cluster_acceptance",
                user="test",
                password=fixture["password"],
            )
            break
        except psycopg.OperationalError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(1)
    checked = probe_django_postgres(fixture)
    os.environ["NO_STARTUP_DB_MUTATIONS"] = "false"
    call_command("migrate", interactive=False, verbosity=0)
    return {"postgres_connections": checked}


def qualify(fixture):
    from model_hub.models.prompt_label import PromptLabel
    from tfc.management.commands import bootstrap_install
    from tracer.services.clickhouse import oss_cdc_bootstrap as core
    from tracer.services.clickhouse import oss_outbox_cdc as cdc
    from tracer.services.clickhouse.v2.cluster_cdc import check_cdc

    bootstrap_install.clickhouse_native_schema(print, 180)
    bootstrap_install.property_catalog(print)

    import clickhouse_connect

    writer = clickhouse_connect.get_client(
        host="127.0.0.1",
        port=fixture["port"],
        database="acceptance_catalog",
        username="observed_catalog_writer",
        password=fixture["password"] + "writer",
    )
    reader = clickhouse_connect.get_client(
        host="127.0.0.1",
        port=fixture["port"],
        database="acceptance_catalog",
        username="observed_catalog_reader",
        password=fixture["password"] + "reader",
    )
    try:
        stamp = datetime(2026, 10, 1, tzinfo=UTC)
        writer.insert(
            "observed_attribute_keys",
            [
                [
                    "org",
                    "workspace",
                    "project",
                    "custom_attribute",
                    "cluster.proof",
                    "string",
                    "cluster.proof",
                    stamp,
                    stamp,
                ]
            ],
            column_names=[
                "organization_id",
                "workspace_id",
                "project_id",
                "source_kind",
                "attribute_key",
                "attribute_type",
                "key_folded",
                "first_seen",
                "last_seen",
            ],
            settings={
                "async_insert": 0,
                "distributed_foreground_insert": 1,
                "insert_quorum": 2,
            },
        )
        assert (
            reader.query(
                "SELECT count() FROM observed_attribute_keys WHERE attribute_key = 'cluster.proof'"
            ).first_row[0]
            == 1
        )
        assert (
            reader.query(
                "SELECT value FROM system.settings WHERE name = 'readonly'"
            ).first_row[0]
            == "2"
        )
        try:
            reader.command(
                "INSERT INTO observed_attribute_keys SELECT * FROM observed_attribute_keys LIMIT 1"
            )
        except Exception as error:
            if (
                "readonly" not in str(error).lower()
                and "read-only" not in str(error).lower()
            ):
                raise
        else:
            raise AssertionError("catalog reader accepted a write")
    finally:
        reader.close()
        writer.close()
    config = cdc.load_config()
    pg, ch = cdc.connect(config)
    try:
        installed = cdc.install(pg, ch, config=config, apply=True, snapshot_budget_s=30)
        assert installed["ready"], installed
        again = cdc.install(pg, ch, config=config, apply=True, snapshot_budget_s=30)
        assert again["ready"] and not again["created_landing"], again
        tables = core.landing_tables(include_usage_schema=config.include_usage_schema)
        actual = dict(
            ch.query(
                "SELECT name, engine FROM system.tables WHERE database = currentDatabase()"
            ).result_rows
        )
        assert all(
            actual.get(name) == "Distributed"
            and actual.get(name + "_local") == "ReplicatedReplacingMergeTree"
            for name in tables
        )
        assert set(core.DEPENDENT) <= actual.keys()
        source = cdc.inspect_source(
            cdc._pg_query(pg), source=config.source, tables=tables
        )
        check_cdc(ch, source, config=config)

        def drain_until(predicate):
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                result = cdc.drain(
                    pg, ch, tables=tables, source=config.source, budget_s=3, batch=100
                )
                assert not result.get("errors") and not result.get("parked"), result
                value = predicate()
                if value:
                    return value
            raise AssertionError(
                "captured row did not reach the cluster within bounded drain budget"
            )

        identity = uuid.uuid4()
        PromptLabel.no_workspace_objects.create(
            id=identity, name="cluster-before", type="custom", metadata={}
        )

        def current():
            return ch.query(
                "SELECT name, _peerdb_version FROM model_hub_promptlabel FINAL WHERE id = %(id)s AND _peerdb_is_deleted = 0",
                parameters={"id": identity},
            ).result_rows

        def named(expected):
            rows = current()
            return rows if len(rows) == 1 and rows[0][0] == expected else None

        first = drain_until(lambda: named("cluster-before"))
        PromptLabel.no_workspace_objects.filter(id=identity).update(
            name="cluster-after"
        )
        updated = drain_until(lambda: named("cluster-after"))
        assert len(updated) == 1 and updated[0][1] > first[0][1]
        pg.execute("DELETE FROM model_hub_promptlabel WHERE id = %s", (identity,))
        drain_until(lambda: not current())
        print(
            f"PASS real application migrations, bootstrap replay, {len(tables)} CDC mirrors, all dependent dictionaries/views, captured insert/update/delete"
        )
        return {
            "cdc_tables": list(tables),
            "cdc_dependents": sorted(core.DEPENDENT),
            "capture_update_delete": True,
            "observed_catalog_bootstrap": True,
        }
    finally:
        pg.close()
        ch.close()
