"""Run the real SELECT-only membership verifier on an owned disposable pair."""

import json
import secrets
import sys
from pathlib import Path

from harness import ROOT, Docker, digest, load, read_file, save_new
from run import NODES, Runner
from schema_probe import configure_libraries


def probe(directory):
    m = load(directory)
    docker = Docker(directory, m)
    docker.connect()
    docker.owned(running=True)
    runner = Runner(directory, m, docker)
    configure_libraries()
    from tracer.services.clickhouse.v2.property_catalog.keeper_membership import (
        KeeperMember,
        attest_keeper_membership,
    )

    database = "property_catalog_dev_replicated_" + m["run_id"]
    members, calls = [], []
    for node in NODES:
        identity = runner.sql(
            node, "SELECT hostName() AS hostname, toString(serverUUID()) AS server_uuid"
        )
        if len(identity) != 1:
            raise ValueError("owned server identity absent")
        rows = runner.sql(
            node,
            f"SELECT table, zookeeper_path FROM system.replicas WHERE database='{database}' ORDER BY table",
        )
        members.append(
            KeeperMember(
                node,
                identity[0]["hostname"],
                identity[0]["server_uuid"],
                tuple((row["table"], row["zookeeper_path"]) for row in rows),
            )
        )

    def read(node, sql, params, limit, timeout_ms):
        # Paths are already validated by KeeperMember; no arbitrary SQL or
        # host/credential is accepted. The fixture transport has a 10s server
        # deadline, so refuse a shorter remaining budget rather than overrun it.
        if node not in NODES or timeout_ms < 10_000:
            raise ValueError("owned read cannot honor its remaining deadline")
        if params:
            if set(params) != {"paths", "row_limit"} or limit != params["row_limit"]:
                raise ValueError("unexpected membership query parameters")
            sql = sql.replace(
                "%(paths)s",
                "(" + ",".join("'" + path + "'" for path in params["paths"]) + ")",
            )
            sql = sql.replace("%(row_limit)s", str(limit))
        rows = runner.sql(node, sql)
        calls.append({"node": node, "sql": sql, "rows": rows})
        return rows

    source = (
        ROOT
        / "futureagi/tracer/services/clickhouse/v2/property_catalog/keeper_membership.py"
    )
    before = digest(read_file(source))
    first = attest_keeper_membership(members, read)
    second = attest_keeper_membership(members, read)
    if first != second or digest(read_file(source)) != before:
        raise ValueError("membership or verifier changed during the probe")
    evidence = {
        "status": "passed",
        "run_id": m["run_id"],
        "keeper_membership_sha256": first,
        "queries": calls,
        "source_sha256": before,
        "production_admitted": False,
        "scope": "actual two-node live Keeper membership; no write/commit/activation durability or split-Keeper fault claim",
    }
    path = directory / ("keeper-membership-" + secrets.token_hex(6) + ".json")
    save_new(path, evidence)
    print(
        json.dumps(
            {"status": "passed", "evidence": str(path), "query_count": len(calls)}
        )
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("expected exact owned run directory")
    probe(Path(sys.argv[1]).absolute())
