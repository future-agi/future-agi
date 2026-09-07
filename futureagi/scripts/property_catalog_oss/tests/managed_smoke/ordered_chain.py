"""Optional real ordered delivery/restart smoke; not workspace qualification.

Builds current checkout commands, never donor images. Only child PIDs created
here are signalled. All service endpoints belong to the caller's new project.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from run import ROOT, Run, load_run, save


def build(run: Run) -> None:
    for command in ("fi-property-catalog-sequencer", "fi-property-catalog-consumer"):
        run.command(
            ["go", "build", "-o", str(run.directory / command), "./cmd/" + command],
            cwd=ROOT / "fi-collector",
            timeout=180,
        )


def environments(run: Run, identity: dict) -> tuple[dict, dict]:
    m = run.manifest
    common = {
        **run.env,
        "FI_PROPERTY_CATALOG_ENVIRONMENT": "development",
        "FI_PROPERTY_CATALOG_DEV_ACK": "PROPERTY_CATALOG_V1_DEV_ONLY",
        "FI_PROPERTY_CATALOG_KAFKA_BROKERS": f"127.0.0.1:{m['ports']['kafka']}",
        "FI_PROPERTY_CATALOG_KAFKA_TOPIC": m["ordered_topic"],
        "FI_PROPERTY_CATALOG_DELIVERY_TIMEOUT": "5s",
        "FI_PROPERTY_CATALOG_REVISION_FENCE_FILE": identity["revision_fence_file"],
    }
    seq = {
        **common,
        "FI_PROPERTY_CATALOG_MODE": "sequencer",
        "FI_PROPERTY_CATALOG_WORKSPACE_SCOPE_MODE": "revision_fence",
        "FI_PROPERTY_CATALOG_SPOOL_DIR": str(run.directory / "sequencer-spool"),
        "FI_PROPERTY_CATALOG_SEQUENCER_TRANSACTIONAL_ID": m["project"] + "-owner",
        "FI_PROPERTY_CATALOG_CANDIDATE_KAFKA_BROKERS": common[
            "FI_PROPERTY_CATALOG_KAFKA_BROKERS"
        ],
        "FI_PROPERTY_CATALOG_CANDIDATE_KAFKA_TOPIC": m["candidate_topic"],
        "FI_PROPERTY_CATALOG_CANDIDATE_KAFKA_CONSUMER_GROUP": m["project"]
        + "-candidates",
        "FI_PROPERTY_CATALOG_CANDIDATE_KAFKA_INSTANCE_ID": m["project"] + "-instance",
        "FI_PROPERTY_CATALOG_SEQUENCER_STARTUP_TIMEOUT": "10s",
        "FI_PROPERTY_CATALOG_REPLAY_INTERVAL": "100ms",
        "FI_PROPERTY_CATALOG_SHUTDOWN_TIMEOUT": "5s",
        "FI_PROPERTY_CATALOG_MAX_SPOOL_FILES": "64",
        "FI_PROPERTY_CATALOG_MAX_SPOOL_BYTES": "4194304",
        "FI_PROPERTY_CATALOG_CANDIDATE_RECEIPT_MAX_FILES": "64",
        "FI_PROPERTY_CATALOG_CANDIDATE_RECEIPT_MAX_BYTES": "4194304",
    }
    consumer = {
        **common,
        "FI_PROPERTY_CATALOG_CONSUMER_MODE": "kafka",
        "FI_PROPERTY_CATALOG_KAFKA_CONSUMER_GROUP": m["project"] + "-delivery",
        "FI_PROPERTY_CATALOG_CH_URL": f"http://127.0.0.1:{m['ports']['http']}",
        "FI_PROPERTY_CATALOG_CH_DATABASE": m["database"],
        "FI_PROPERTY_CATALOG_CH_USERNAME": "property_catalog_oss_consumer",
        "FI_PROPERTY_CATALOG_CH_PASSWORD": "oss-catalog-consumer-local-only",
        "FI_PROPERTY_CATALOG_LEDGER_CH_URL": f"http://127.0.0.1:{m['ports']['http']}",
        "FI_PROPERTY_CATALOG_LEDGER_CH_DATABASE": m["database"],
        "FI_PROPERTY_CATALOG_LEDGER_CH_USERNAME": "property_catalog_oss_ledger",
        "FI_PROPERTY_CATALOG_LEDGER_CH_PASSWORD": "oss-catalog-ledger-local-only",
        # The full application fixture discovers four workspaces with ten
        # streams each; restart must seed all forty without a fixture-only cap.
        "FI_PROPERTY_CATALOG_CHECKPOINT_MAX_STREAMS": "64",
        "FI_PROPERTY_CATALOG_CHECKPOINT_MAX_INVENTORY_BYTES": "1048576",
        "FI_PROPERTY_CATALOG_CHECKPOINT_INVENTORY_TIMEOUT": "5s",
    }
    return seq, consumer


class Children:
    def __init__(self, run: Run, deadline: float, prefix: str = ""):
        self.run, self.deadline = run, deadline
        self.prefix = prefix
        self.children: list[tuple[subprocess.Popen, object, Path]] = []
        self.pids: list[int] = []
        self.generation = 0

    def start(self, seq: dict, consumer: dict) -> None:
        self.generation += 1
        for name, env, args in (
            ("sequencer", seq, []),
            ("consumer", consumer, ["--seed-from-delivery-ledger"]),
        ):
            path = self.run.directory / f"{self.prefix}{name}-{self.generation}.log"
            log = path.open("xb")
            try:
                child = subprocess.Popen(
                    [str(self.run.directory / ("fi-property-catalog-" + name)), *args],
                    cwd=self.run.directory,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            except BaseException:
                log.close()
                raise
            self.children.append((child, log, path))
            self.pids.append(child.pid)

    def check(self) -> None:
        if time.monotonic() >= self.deadline:
            raise TimeoutError("ordered chain wall/lease deadline reached")
        for child, _, path in self.children:
            if child.poll() is not None:
                raise RuntimeError(
                    f"new child {child.pid} exited {child.returncode}; see {path}"
                )
            if path.stat().st_size > 2_000_000:
                raise RuntimeError("child exceeded bounded log budget")

    def stop(self) -> None:
        for child, _, _ in self.children:
            if child.poll() is None:
                child.terminate()
        for child, log, _ in self.children:
            try:
                child.wait(timeout=7)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=3)
            log.close()
        self.children.clear()


def main(directory: Path) -> None:
    from clickhouse_driver import Client

    run = load_run(directory)
    m = run.manifest
    if not m.get("ordered_chain") or not run.owned():
        raise RuntimeError("ordered chain requires a newly owned, opted-in run")
    containers = json.loads(run.command(["docker", "inspect", *run.owned()]).stdout)
    ch = next(
        c
        for c in containers
        if c["Config"]["Labels"]["com.docker.compose.service"] == "clickhouse"
    )
    for name, key in (("http", "8123/tcp"), ("native", "9000/tcp")):
        if ch["NetworkSettings"]["Ports"][key] != [
            {"HostIp": "127.0.0.1", "HostPort": str(m["ports"][name])}
        ]:
            raise RuntimeError("ordered chain endpoint is not this run's publication")
    identity = json.loads((directory / "identity-evidence.json").read_text())
    if identity.get("opened_stream_count") != 10 or not identity.get(
        "building_fence_published"
    ):
        raise RuntimeError("coordinator has not admitted all ten streams")
    expires = datetime.fromisoformat(identity["lease_expires_at"])
    remaining = (expires - datetime.now(UTC)).total_seconds() - 10
    if remaining < 45:
        raise RuntimeError(
            "insufficient finite lease remains; start a fresh disposable run"
        )
    deadline = time.monotonic() + min(85, remaining)
    run.deadline = deadline
    children = Children(run, deadline)
    seq, consumer = environments(run, identity)
    path = Path(identity["identity_file"])
    initial_bytes = path.read_bytes()
    candidate = json.loads((directory / "kafka-evidence.json").read_text())[
        "candidates"
    ][0]
    expected = sorted(
        tuple(
            v[k]
            for k in (
                "source_kind",
                "attribute_key",
                "attribute_type",
                "value_fingerprint",
                "value_json",
            )
        )
        for v in candidate["values"]
    )
    reader = Client(
        "127.0.0.1",
        port=m["ports"]["native"],
        database=m["database"],
        user="property_catalog_oss_api",
        password="oss-catalog-api-local-only",
        connect_timeout=3,
        send_receive_timeout=5,
        settings={
            "readonly": 2,
            "max_execution_time": 2,
            "max_threads": 1,
            "max_memory_usage": 134217728,
            "max_bytes_to_read": 67108864,
            "max_result_rows": 64,
            "max_result_bytes": 1048576,
            "result_overflow_mode": "throw",
            "read_overflow_mode": "throw",
        },
    )
    scope = identity["source_scope"]
    params = {
        "org": scope["organization_id"],
        "ws": scope["workspace_id"],
        "build": identity["build_token"],
    }
    where = " WHERE organization_id=%(org)s AND workspace_id=%(ws)s AND build_token=%(build)s"

    def snapshot():
        children.check()
        values = sorted(
            reader.execute(
                "SELECT DISTINCT toString(source_kind), attribute_key, toString(attribute_type), toString(value_fingerprint), value_json FROM span_attribute_value_catalog"
                + where
                + " LIMIT 64",
                params,
            )
        )
        ledger = reader.execute(
            "SELECT toString(producer_stream_id), sequence, envelope_id, outcome, gap_reasons, transport, kafka_offset FROM property_catalog_deliveries"
            + where
            + " ORDER BY producer_stream_id, sequence LIMIT 64",
            params,
        )
        if any(row[3] != "committed" or row[4] or row[5] != "kafka" for row in ledger):
            raise RuntimeError(
                "delivery ledger contains noncommitted/gap/nonKafka evidence"
            )
        return values, ledger

    try:
        children.start(seq, consumer)
        while True:
            values, ledger = snapshot()
            if values == expected and ledger:
                break
            time.sleep(0.25)
        first = ledger
        save(
            directory / "ordered-chain-first-delivery.json",
            {
                "status": "delivery_passed_restart_pending",
                "value_tuples": len(values),
                "real_http_delivery_lease_guard": True,
                "delivery_rows": ledger,
                "lifecycle_qualified": False,
            },
        )
        children.stop()
        children.start(seq, consumer)
        # Same bytes produce the same candidate identity; receipt state is retained.
        run.command(
            [
                str(directory / "candidate-smoke"),
                str(directory / "candidate-input.json"),
            ],
            timeout=40,
        )
        group = m["project"] + "-candidates"
        while True:
            children.check()
            output = run.execute(
                "kafka",
                [
                    "/opt/kafka/bin/kafka-consumer-groups.sh",
                    "--bootstrap-server",
                    "kafka:9092",
                    "--describe",
                    "--group",
                    group,
                ],
                timeout=10,
            ).stdout
            caught_up = any(
                len(parts := line.split()) >= 6
                and parts[:3] == [group, m["candidate_topic"], "0"]
                and parts[3:6] == ["4", "4", "0"]
                for line in output.splitlines()
            )
            if caught_up:
                break
            time.sleep(0.25)
        values, ledger = snapshot()
        if values != expected or ledger != first or path.read_bytes() != initial_bytes:
            raise RuntimeError(
                "restart/replayed candidates changed projection, ledger or identity"
            )
        if (
            reader.execute("SELECT count() FROM property_catalog_activations")[0][0]
            != 0
        ):
            raise RuntimeError("ordered smoke unexpectedly activated a revision")
        save(
            directory / "ordered-chain-evidence.json",
            {
                "status": "passed",
                "actual_current_checkout_commands": True,
                "version_environment_variables_supplied": False,
                "real_http_delivery_lease_guard": True,
                "value_tuples": len(values),
                "delivery_rows": len(ledger),
                "candidate_group_committed_offset": 4,
                "restart_identity_and_ledger_stable": True,
                "child_pids": children.pids,
                "activation_rows": 0,
                "lifecycle_qualified": False,
            },
        )
    finally:
        children.stop()
        reader.disconnect()


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve(strict=True))
