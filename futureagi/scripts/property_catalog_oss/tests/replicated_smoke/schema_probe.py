"""Reuse pinned schema/renderers and read-only product adapters; no installers."""

from dataclasses import asdict

from harness import ROOT, canonical, digest, read_file


def source_fingerprints():
    paths = [
        "tracer/services/clickhouse/v2/schema/025_property_catalog_data.sql",
        "tracer/services/clickhouse/v2/schema/026_property_catalog_state.sql",
        "tracer/services/clickhouse/v2/schema/027_property_catalog_delivery.sql",
        "tracer/services/clickhouse/v2/catalog_prod_schema.py",
        "tracer/services/clickhouse/v2/apply_schema_rewriter.py",
        "tracer/services/clickhouse/v2/property_catalog/reader_activation.py",
        "tracer/services/clickhouse/v2/property_catalog/reader_activation_client.py",
        "tracer/services/clickhouse/v2/property_catalog/installation_bootstrap.py",
        "tracer/services/clickhouse/v2/property_catalog/installation_identity.py",
        "scripts/property_catalog_oss/bootstrap_clickhouse.sh",
        "scripts/property_catalog_oss/reader_activation_control.sql",
    ]
    return {path: digest(read_file(ROOT / "futureagi" / path)) for path in paths}


def schema_bundle(m):
    from tracer.services.clickhouse.v2 import catalog_prod_schema as schema

    canonical_statements = (
        *schema._load_canonical_statements(),
        schema._activation_control_statement(),
    )
    specs = (*schema._TABLE_SPECS, schema._ACTIVATION_CONTROL_SPEC)
    # Verify that OSS bootstrap and the production schema planner agree on the
    # seventh table too. Fail on drift rather than copying a stale DDL fixture.
    control = read_file(
        ROOT / "futureagi/scripts/property_catalog_oss/reader_activation_control.sql"
    ).decode()
    if control.strip() != canonical_statements[-1].sql.strip():
        raise ValueError("OSS and production activation-control schema differ")
    bundle = {
        "production_required_replicas": schema.REQUIRED_REPLICAS,
        "fixture_replicas": 2,
        "production_admitted": False,
        "production_mismatch": "two-replica diagnostic fixture; not a three-replica production install",
        "databases": {},
    }
    for family in ("standalone", "replicated"):
        database = f"property_catalog_dev_{family}_{m['run_id']}"
        tables = []
        for statement, spec in zip(canonical_statements, specs, strict=True):
            if family == "standalone":
                sql, count = schema._CANONICAL_CREATE_RE.subn(
                    f"CREATE TABLE IF NOT EXISTS {database}.{statement.table}",
                    statement.sql,
                    count=1,
                )
                if count != 1:
                    raise ValueError("unrecognized pinned CREATE")
                engine, keeper_path = statement.engine, None
            else:
                rendered = schema._render_table(
                    statement,
                    spec,
                    target_database=database,
                    cluster="smoke_cluster",
                    keeper_path_prefix="/clickhouse/tables",
                )
                # Fixture DDL is deliberately sent once to EACH owned node.
                # Do not invoke or relax the production three-replica installer.
                if rendered.sql.count(" ON CLUSTER 'smoke_cluster'") != 1:
                    raise ValueError("unexpected rendered cluster clause")
                sql = rendered.sql.replace(" ON CLUSTER 'smoke_cluster'", "", 1)
                engine, keeper_path = rendered.engine, rendered.keeper_path
            tables.append(
                {
                    "name": statement.table,
                    "engine": engine,
                    "sql": sql,
                    "source_sha256": statement.source_sha256,
                    "sql_sha256": digest(sql.encode()),
                    "keeper_path": keeper_path,
                }
            )
        bundle["databases"][family] = {
            "name": database,
            "engine": "Atomic",
            "tables": tables,
        }
    return bundle


def exact_schema(rows, expected):
    from tracer.services.clickhouse.v2 import catalog_prod_schema as schema

    if len(rows) != 7 or {row["name"] for row in rows} != {
        table["name"] for table in expected["tables"]
    }:
        raise ValueError("not exactly seven physical catalog tables")
    by_name = {row["name"]: row for row in rows}
    for table in expected["tables"]:
        row = by_name[table["name"]]
        options = {
            "target_database": expected["name"],
            "expected_table": table["name"],
            "cluster": "smoke_cluster",
        }
        if (
            row["database"] != expected["name"]
            or row["engine"] != table["engine"]
            or (
                schema._canonical_create_tokens(row["create_table_query"], **options)
                != schema._canonical_create_tokens(table["sql"], **options)
            )
        ):
            raise ValueError(f"exact schema/engine mismatch: {table['name']}")
    return digest(canonical(rows))


def topology_errors(replica_rows, expected, node):
    errors = []
    if len(replica_rows) != 7:
        errors.append("not exactly seven replicated tables")
    for table in expected["tables"]:
        path = table["keeper_path"].replace("{shard}", "1")
        rows = [row for row in replica_rows if row["table"] == table["name"]]
        if (
            len(rows) != 1
            or rows[0]["zookeeper_path"] != path
            or rows[0]["replica_name"] != node
            or rows[0]["total_replicas"] != 2
            or rows[0]["active_replicas"] != 2
        ):
            errors.append(
                "wrong shared Keeper path or 2/2 membership: " + table["name"]
            )
    return errors


def configure_libraries():
    # Same library-only settings pattern as managed_smoke/source_parity.py.
    # No project .env, Django application setup, ORM, identity-file allocation.
    from django.conf import settings

    from tfc.settings.runtime_setting_specs import RUNTIME_NUMERIC_SETTING_SPECS

    if not settings.configured:
        settings.configure(
            **{
                name: spec.default
                for name, spec in RUNTIME_NUMERIC_SETTING_SPECS.items()
            }
        )


def capture(call):
    try:
        value = call()
        if hasattr(value, "__dataclass_fields__"):
            value = asdict(value)
        return {"outcome": "returned", "value": value}
    except Exception as exc:
        return {"outcome": "rejected", "type": type(exc).__name__, "error": str(exc)}


def adapter_probes(m, node, database):
    """All product adapter calls below are SELECT/SHOW-only, with readonly=2."""
    configure_libraries()
    from tracer.services.clickhouse.client import ClickHouseClient
    from tracer.services.clickhouse.v2 import catalog_prod_schema as schema
    from tracer.services.clickhouse.v2.property_catalog.installation_bootstrap import (
        inspect_installation,
    )
    from tracer.services.clickhouse.v2.property_catalog.reader_activation import (
        verify_reader_activation_schema,
    )

    class ReadClient(ClickHouseClient):
        def execute_read(self, sql, params=None, *, timeout_ms=None, settings=None):
            if not sql.lstrip().upper().startswith(("SELECT ", "SELECT\n", "SHOW ")):
                raise ValueError("probe attempted a non-read statement")
            return super().execute_read(
                sql,
                params,
                timeout_ms=min(timeout_ms or 8000, 10000),
                settings={
                    **(settings or {}),
                    "readonly": 2,
                    "max_threads": 1,
                    "max_memory_usage": 268435456,
                },
            )

        def query_rows(self, sql, *, parameters=None):
            return self.execute_read(sql, parameters, timeout_ms=8000)[0]

    client = ReadClient(
        host="127.0.0.1",
        port=m["ports"][node + "_native"],
        database=database,
        user="smoke_probe",
        password=m["password"],
        pool_size=1,
        connect_timeout=3,
        send_timeout=10,
        receive_timeout=10,
        server_enforced_readonly=True,
        allow_query_settings_with_server_readonly=True,
    )
    try:
        return {
            "production_topology_admission": capture(
                lambda: schema._prove_cluster(client, cluster="smoke_cluster")
            ),
            "reader_activation_local_schema": capture(
                lambda: verify_reader_activation_schema(client, database=database)
            ),
            "installation_identity_local_inspection": capture(
                lambda: inspect_installation(
                    client,
                    environment="development",
                    target_database=database,
                    candidate_topic=m["project"] + ".candidates",
                    ordered_topic=m["project"] + ".ordered",
                )
            ),
            "identity_persisted": False,
            "production_grant_admission": "NOT_TESTED: isolated XML probe user, not production writer grants",
        }
    finally:
        client.close()


def reservation_row(m, revision):
    """Actual valid build plan, synthetic empty source-stream reservation only."""
    configure_libraries()
    from datetime import UTC, datetime, timedelta
    from uuid import NAMESPACE_URL, uuid5

    from tracer.services.clickhouse.v2.property_catalog.activation import (
        BuildPlanSourceScope,
        BuildPlanStream,
        ManifestStreamRole,
        RevisionBuildPlan,
    )
    from tracer.services.clickhouse.v2.property_catalog.models import SourceAdapter

    def identity(name):
        return str(uuid5(NAMESPACE_URL, m["project"] + "/" + name))

    now = datetime.now(UTC)
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S.%f")
    streams = tuple(
        BuildPlanStream(
            adapter, role, identity(str(adapter) + "/" + str(role)), "source_version", 1
        )
        for adapter in SourceAdapter
        for role in (
            tuple(ManifestStreamRole)
            if adapter is SourceAdapter.SPAN_ATTRIBUTE
            else (ManifestStreamRole.DEFINITIONS,)
        )
    )
    plan = RevisionBuildPlan(
        organization_id=identity("organization"),
        workspace_id=identity("workspace"),
        catalog_epoch=1,
        catalog_revision=revision,
        build_token=identity(f"build/{revision}"),
        projection_version=1,
        source_scope=BuildPlanSourceScope(
            (identity("project"),),
            int(now.timestamp() * 1000000),
            int((now + timedelta(hours=1)).timestamp() * 1000000),
        ),
        streams=streams,
    )
    return {
        "organization_id": plan.organization_id,
        "workspace_id": plan.workspace_id,
        "catalog_epoch": 1,
        "catalog_revision": revision,
        "build_token": plan.build_token,
        "projection_version": 1,
        "source_adapter": "system_manifest",
        "producer_stream_id": plan.build_token,
        "envelope_version": 0,
        "first_sequence": 0,
        "last_sequence": 0,
        "max_contiguous_sequence": 0,
        "last_issued_sequence": 0,
        "fenced_sequence": 0,
        "terminal_payload_sha256": "0" * 64,
        "build_plan_json": plan.canonical_json,
        "build_lease_sha256": plan.sha256,
        "status": "open",
        "gap_count": 0,
        "gap_reasons": [],
        "kafka_partition": -1,
        "kafka_high_water_offset": -1,
        "started_at": timestamp,
        "updated_at": timestamp,
        "drain_deadline": (now + timedelta(minutes=10)).strftime(
            "%Y-%m-%d %H:%M:%S.%f"
        ),
        "fenced_at": None,
        "_version": 1,
    }
