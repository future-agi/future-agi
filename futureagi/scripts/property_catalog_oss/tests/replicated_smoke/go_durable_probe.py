"""One-shot actual Go sink/proof probe; independent of parent-owned runners.

Plan/compile is offline. Execute requires an exact confirmation, checks the
existing 6.5GiB harness headroom/ownership guards, and ALWAYS attempts exact
cleanup on terminal success or failure. No production, Kafka, or activation.
The only fault loses one complete replicated ledger ACK inside the Go fixture
transport; subsequent execution reopens the real journal and cannot resend it.
"""

import argparse
import http.client
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from harness import ROOT, Docker, digest, load, plan, read_file, save_new, validate_manifest
from run import NODES, Runner


INSERT_TABLES = (
    "property_definition_catalog",
    "span_attribute_value_catalog",
    "property_catalog_deliveries",
)
PROOF_TABLES = (
    *INSERT_TABLES,
    "property_catalog_source_streams",
    "property_catalog_checkpoints",
    "property_catalog_activations",
    "property_catalog_activation_control_events",
)
SYSTEM_TABLES = (
    "databases", "tables", "replicas", "zookeeper", "zookeeper_connection", "query_log"
)


def grant_statements(manifest, family):
    """Fixture-only principals: no roles, wildcards, admin writer, or SETTINGS knobs.

    Direct grants deliberately match the product's exact SHOW GRANTS attestor.
    Query logging is only a positive witness for this lost-ACK scenario; it is
    not installed as a global correctness prerequisite. An inherited unsafe
    async profile also proves the product's synchronous INSERT setting pins.
    """
    validate_manifest(manifest)
    if family not in ("standalone", "replicated"):
        raise ValueError("unowned catalog family")
    database = "property_catalog_dev_" + family + "_" + manifest["run_id"]
    writer, reader = "smoke_go_writer_" + family, "smoke_go_proof_" + family
    password_hash = digest(manifest["password"].encode())
    statements = [
        f"CREATE USER {writer} IDENTIFIED WITH sha256_hash BY '{password_hash}' "
        "SETTINGS readonly=0, async_insert=1, wait_for_async_insert=0, "
        "async_insert_busy_timeout_ms=60000, max_threads=1, "
        "max_memory_usage=268435456, max_execution_time=10, "
        "log_queries=1, log_query_settings=1, log_queries_probability=1, log_queries_min_query_duration_ms=0",
        f"CREATE USER {reader} IDENTIFIED WITH sha256_hash BY '{password_hash}' "
        "SETTINGS readonly=2, max_threads=1, max_memory_usage=134217728, max_execution_time=10",
    ]
    statements += [f"GRANT INSERT ON {database}.{table} TO {writer}" for table in INSERT_TABLES]
    statements += [f"GRANT SELECT ON {database}.{table} TO {reader}" for table in PROOF_TABLES]
    statements += [f"GRANT SELECT ON system.{table} TO {reader}" for table in SYSTEM_TABLES]
    return statements


def go_sources():
    root = ROOT / "fi-collector"
    paths = sorted((root / "pkg/propertycatalog").glob("*.go"))
    paths += [root / "go.mod", root / "go.sum"]
    # Include local package dependencies so evidence identifies the compiled
    # actual application implementation, not just the new test entrypoint.
    paths += sorted(p for p in (root / "pkg").rglob("*.go") if p not in paths)
    return {str(p.relative_to(ROOT)): digest(read_file(p)) for p in paths}


def process_env():
    return {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "LC_ALL")}


def prepare():
    directory, manifest = plan()
    sources = go_sources()
    binary = directory / "go-durable-probe.test"
    command = ["go", "test", "-c", "-o", str(binary), "./pkg/propertycatalog"]
    result = subprocess.run(command, cwd=ROOT / "fi-collector", env={**process_env(), "GOPROXY": "off"},
                            capture_output=True, text=True, timeout=120, check=False)
    save_new(directory / "go-build.json", {"command": command, "returncode": result.returncode,
             "stdout": result.stdout, "stderr": result.stderr, "sources": sources})
    if result.returncode != 0 or sources != go_sources():
        raise ValueError("offline Go build failed or sources changed; no infrastructure started")
    save_new(directory / "go-plan.json", {
        "run_id": manifest["run_id"], "binary_sha256": digest(read_file(binary, limit=128 << 20)),
        "source_sha256": sources, "fixture_memory_gib": 6.5,
        "topology": "standalone N1 and replicated N2 databases, two CH25.3 servers, one independent Keeper",
        "admission": "real Python producer, no descriptor edits; production N3 requirement unchanged",
        "faults": "exactly one lost full ACK on replicated ledger; read-only QueryFinish resolution after journal reopen",
        "claims_excluded": ["Kafka commit", "activation", "production admission", "Keeper HA", "missing-log repair"],
    })
    print(json.dumps({"status": "planned_no_infrastructure", "run_id": manifest["run_id"],
                      "directory": str(directory), "memory_gib": 6.5}), flush=True)


def validate_execution(directory, confirmation):
    manifest = load(directory)
    if confirmation != manifest["run_id"]:
        raise ValueError("requires exact confirmed run id")
    prepared = json.loads(read_file(directory / "go-plan.json"))
    binary = directory / "go-durable-probe.test"
    if prepared["run_id"] != confirmation or prepared["source_sha256"] != go_sources():
        raise ValueError("compiled Go sources changed since offline plan")
    if prepared["binary_sha256"] != digest(read_file(binary, limit=128 << 20)):
        raise ValueError("compiled Go binary changed")
    # Exclusive intent is written before Docker.connect/up. Retrying a failed
    # integration run requires a new owned plan, never replaying old writes.
    save_new(directory / "go-live-intent.json", {"run_id": confirmation, "one_shot": True})
    return manifest, binary, prepared


def wait_for_keeper(runner, timeout=45):
    """Only SELECTs may retry before the first CREATE/write intent.

    Running containers/ready HTTP do not imply Keeper is accepting sessions.
    The original e389 fixture failed CREATE with code 999 connection refused;
    that uncertain CREATE was not replayed. New fixtures wait here instead.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            resources = runner.verify_owned()
            for node in NODES:
                rows = runner.sql(node, "SELECT count() AS entries FROM system.zookeeper WHERE path='/'")
                if len(rows) != 1 or type(rows[0].get("entries")) is not int:
                    raise ValueError("Keeper readiness lacks complete SELECT evidence")
            return resources
        except (OSError, http.client.HTTPException, ValueError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.5)


def execute(directory, confirmation):
    manifest, binary, prepared = validate_execution(directory, confirmation)
    docker = Docker(directory, manifest)
    docker.connect()
    outcome = {"run_id": confirmation, "status": "failed", "production_admitted": False, "kafka_tested": False}
    try:
        docker.up(confirmation)  # exact caps, local image/socket and all-existing-container headroom guard
        runner = Runner(directory, manifest, docker)
        save_new(directory / "go-owned-start.json", [
            {"kind": kind, "name": name, "id": row.get("Id"), "state": row.get("State")}
            for kind, name, row in docker.owned()
        ])
        outcome["ready_resources"] = wait_for_keeper(runner)
        outcome["bootstrap"] = runner.bootstrap()
        resources = runner.verify_owned()
        save_new(directory / "go-owned-resources.json", resources)
        print(json.dumps({"status": "live", "run_id": confirmation, "directory": str(directory), "resources": resources}), flush=True)
        from admission_probe import probe

        probe(directory)  # fresh empty tables; actual native/HTTP/schema/Keeper producer; no fabricated admission
        for family in ("standalone", "replicated"):
            for node in NODES if family == "replicated" else NODES[:1]:
                runner.verify_owned()
                for statement in grant_statements(manifest, family):
                    runner.sql(node, statement, write=True)  # fixture admin only provisions isolated exact-grant users
        save_new(directory / "go-principals.json", {
            "writer_insert_tables": INSERT_TABLES, "proof_catalog_tables": PROOF_TABLES,
            "proof_system_tables": SYSTEM_TABLES, "no_wildcard_or_role_grants": True,
            "writer_profile": {"async_insert": 1, "wait_for_async_insert": 0, "log_queries": 1, "log_query_settings": 1},
        })
        runner.verify_owned()
        result = subprocess.run([str(binary), "-test.run=^TestDurableClickHouseOwnedFixture$", "-test.v",
                                 "-test.timeout=180s", "-catalog-durable-fixture=" + str(directory)],
                                cwd=ROOT / "fi-collector", env=process_env(), text=True,
                                capture_output=True, timeout=190, check=False)
        save_new(directory / "go-test-output.json", {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
        print(result.stdout, end="", flush=True)
        print(result.stderr, end="", file=sys.stderr, flush=True)
        outcome["resources_after"] = runner.verify_owned()
        if result.returncode:
            raise ValueError("actual Go durable sink/proof failed; see go-test-output.json and go-durable-result.json")
        if prepared["source_sha256"] != go_sources():
            raise ValueError("Go sources changed during live observation; compiled binary remains identified")
        outcome["status"] = "passed"
    except BaseException as exc:
        outcome["error"] = type(exc).__name__ + ": " + str(exc)
        try:
            logs = {}
            for kind, name, row in docker.owned():
                if kind == "container":
                    result = docker.command(["logs", "--tail", "100", "--timestamps", row["Id"]], check=False)
                    logs[name] = {"id": row["Id"], "stdout": result.stdout[-262144:], "stderr": result.stderr[-262144:]}
            save_new(directory / "go-terminal-logs.json", logs)
        except BaseException as log_error:
            outcome["log_capture_error"] = type(log_error).__name__ + ": " + str(log_error)
        raise
    finally:
        try:
            docker.cleanup()  # revalidates exact IDs/names/ownership; permitted even if sources drifted
            outcome["cleanup"] = "all owned containers, volumes and network absent; evidence retained"
        except BaseException as exc:
            outcome["cleanup_error"] = type(exc).__name__ + ": " + str(exc)
            raise
        finally:
            save_new(directory / "go-live-result.json", outcome)
            print(json.dumps(outcome), flush=True)


if __name__ == "__main__":
    fixture_env = process_env()
    os.environ.clear()
    os.environ.update(fixture_env)
    # Environment is sanitized here; process_env() is also used
    # for every subprocess. Never source application settings or a .env file.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("plan", "execute"))
    parser.add_argument("directory", nargs="?", type=Path)
    parser.add_argument("--confirm-run-id")
    args = parser.parse_args()
    if args.phase == "plan":
        if args.directory or args.confirm_run_id:
            parser.error("plan takes no existing resource target")
        prepare()
    else:
        if not args.directory or not args.confirm_run_id:
            parser.error("execute requires exact directory and --confirm-run-id")
        execute(args.directory.absolute(), args.confirm_run_id)
