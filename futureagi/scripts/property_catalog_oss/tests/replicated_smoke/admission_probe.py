"""Exercise the real admission producer against only an owned disposable fixture.

This initializes private fixture identity/admission files after proving the owned
catalog empty. ClickHouse operations are SELECT-only. No activation, publication,
source ingestion, grant modification, or production admission is performed.
"""

import json
import secrets
import sys
from contextlib import ExitStack
from pathlib import Path

from harness import ROOT, Docker, digest, load, read_file, save_new
from run import NODES, Runner
from schema_probe import configure_libraries


def probe(directory):
    manifest = load(directory)
    docker = Docker(directory, manifest)
    docker.connect()
    docker.owned(running=True)
    runner = Runner(directory, manifest, docker)
    configure_libraries()
    from tracer.services.clickhouse.client import ClickHouseClient
    from tracer.services.clickhouse.v2.property_catalog.installation_bootstrap import (
        inspect_installation,
    )
    from tracer.services.clickhouse.v2.property_catalog.installation_identity import (
        IDENTITY_FILENAME,
        load_or_initialize_identity,
    )
    from tracer.services.clickhouse.v2.property_catalog.publisher import (
        PROPERTY_CATALOG_TABLES,
    )
    from tracer.services.clickhouse.v2.property_catalog.write_admission import (
        DirectCatalogConnection,
        admit_catalog_writes,
    )

    sources = (
        "installation_identity.py",
        "installation_bootstrap.py",
        "write_endpoint.py",
        "write_admission.py",
        "keeper_membership.py",
    )
    package = ROOT / "futureagi/tracer/services/clickhouse/v2/property_catalog"

    def fingerprints():
        return {name: digest(read_file(package / name)) for name in sources}

    before = fingerprints()
    tables = (*PROPERTY_CATALOG_TABLES, "property_catalog_activation_control_events")
    native_calls = []

    class ReadClient(ClickHouseClient):
        def execute_read(self, sql, params=None, *, timeout_ms=None, settings=None):
            if not sql.lstrip().upper().startswith(("SELECT ", "SELECT\n")):
                raise ValueError("admission fixture attempted a non-SELECT statement")
            # No reconnect-to-another-endpoint behavior is supplied. Both native
            # and HTTP routes are exact ownership-verified loopback mappings.
            result = super().execute_read(
                sql,
                params,
                timeout_ms=timeout_ms,
                settings={**(settings or {}), "readonly": 2},
            )
            native_calls.append(
                {"database": self.database, "query_sha256": digest(sql.encode())}
            )
            return result

    results = {}
    for family in ("standalone", "replicated"):
        database = f"property_catalog_dev_{family}_{manifest['run_id']}"
        nodes = NODES if family == "replicated" else NODES[:1]
        runtime = directory / ("admission-runtime-" + family)
        # A retry never overwrites a prior probe's identity/admission evidence.
        runtime.mkdir(mode=0o700)
        with ExitStack() as stack:
            connections = []
            observed_identities = []
            for node in nodes:
                client = ReadClient(
                    host="127.0.0.1",
                    port=manifest["ports"][node + "_native"],
                    database=database,
                    user="smoke_probe",
                    password=manifest["password"],
                    pool_size=1,
                    connect_timeout=3,
                    send_timeout=10,
                    receive_timeout=10,
                    server_enforced_readonly=True,
                    allow_query_settings_with_server_readonly=True,
                )
                stack.callback(client.close)
                # Seven empty physical tables on EVERY selected node, not a
                # count sampled through a service or only one caught-up replica.
                for table in tables:
                    if client.execute_read(
                        f"SELECT 1 FROM {database}.{table} LIMIT 1",
                        timeout_ms=5000,
                        settings={"readonly": 2, "max_result_rows": 1},
                    )[0]:
                        raise ValueError("admission fixture is not fresh and empty")
                observed_identities.append(
                    inspect_installation(
                        client,
                        environment="development",
                        target_database=database,
                        candidate_topic=manifest["project"] + ".candidates",
                        ordered_topic=manifest["project"] + ".ordered",
                    )
                )
                identity_rows = runner.sql(node, "SELECT hostName() AS hostname")
                if len(identity_rows) != 1:
                    raise ValueError("owned native node identity absent")
                connections.append(
                    DirectCatalogConnection(
                        name=node,
                        native_member_host="127.0.0.1",
                        expected_hostname=identity_rows[0]["hostname"],
                        driver=client,
                        http_scheme="http",
                        proof_username="smoke_probe",
                        proof_password=manifest["password"],
                    )
                )
            initial = observed_identities[0]
            if any(
                item.catalog_epoch != initial.catalog_epoch
                or item.projection_version != initial.projection_version
                or item.target_database != database
                for item in observed_identities
            ):
                raise ValueError("empty catalog identity inspection disagrees")
            identity = load_or_initialize_identity(
                runtime / IDENTITY_FILENAME, initialize=lambda initial=initial: initial
            )

            def mapped_route(connection, discovered, *, nodes=nodes, database=database):
                if connection.name not in nodes or discovered.database != database:
                    raise ValueError("unowned admission route")
                return (
                    "http://127.0.0.1:"
                    + str(manifest["ports"][connection.name + "_http"])
                )

            first = admit_catalog_writes(
                runtime,
                identity=identity,
                connections=connections,
                route_resolver=mapped_route,
            )
            # Reattest against both protocols and Keeper, then reuse exactly the
            # existing immutable descriptor, rather than regenerating identity.
            second = admit_catalog_writes(
                runtime,
                identity=identity,
                connections=connections,
                route_resolver=mapped_route,
            )
            if first != second or first.family != family:
                raise ValueError("admission reuse changed its identity or family")
            results[family] = {
                "status": "passed",
                "members": len(first.members),
                "topology_sha256": first.topology_sha256,
                "admission_file": str(runtime / "write-admission-v1.json"),
                "repeat_identical": True,
            }
    if before != fingerprints():
        raise ValueError("admission product sources changed during live observation")
    evidence = {
        "status": "passed",
        "run_id": manifest["run_id"],
        "families": results,
        "native_queries": native_calls,
        "source_sha256": before,
        "production_admitted": False,
        "scope": "real producer, exact schemas, native/HTTP mapping and live Keeper; not write/activation/Kafka durability or production grants",
    }
    path = directory / ("write-admission-" + secrets.token_hex(6) + ".json")
    save_new(path, evidence)
    print(json.dumps({"status": "passed", "evidence": str(path), "families": results}))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("expected exact owned run directory")
    probe(Path(sys.argv[1]).absolute())
