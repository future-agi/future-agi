#!/usr/bin/env python3
"""Small correctness/fault fixture, not a throughput test or full product E2E.

Run only against the isolated fixture from generate.py. Deliberate container
stops require --allow-faults; every stopped service is restarted in finally.
No delete/down/volume removal is performed by this script.
"""

import argparse
import json
import shutil
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path


def run(directory, allow_faults):
    import clickhouse_connect

    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root / "futureagi"))
    from tracer.services.clickhouse.v2.cluster_apply import apply_plan
    from tracer.services.clickhouse.v2.cluster_cdc import verify_columns
    from tracer.services.clickhouse.v2.cluster_schema import (
        ClusterTopology,
        native_plan,
    )

    directory = Path(directory).resolve()
    fixture = json.loads((directory / "fixture.json").read_text())
    if not fixture["project"].startswith("fagi-cluster-"):
        raise ValueError("not an isolated acceptance fixture")
    compose = (
        [shutil.which("docker-compose")]
        if shutil.which("docker-compose")
        else ["docker", "compose"]
    ) + [
        "-p",
        fixture["project"],
        "-f",
        str(directory / "compose.json"),
    ]
    clients = []
    for index in range(6):
        deadline = time.monotonic() + 90
        while True:
            try:
                client = clickhouse_connect.get_client(
                    host="127.0.0.1",
                    port=fixture["port"] + index,
                    username="default",
                    password=fixture["password"],
                    send_receive_timeout=180,
                )
                client.query("SELECT 1")
                clients.append(client)
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(1)
    topology = ClusterTopology(
        "acceptance_v1", "acceptance", "/acceptance", insert_quorum=2
    )
    plan = native_plan(topology)
    admin = clients[0]
    assert apply_plan(admin, topology, plan, password=fixture["password"]) == len(plan)
    verify_columns(admin, topology, plan)
    assert apply_plan(clients[3], topology, plan, password=fixture["password"]) == 0
    project, session, user = [str(uuid.uuid4()) for _ in range(3)]
    ids = [str(uuid.uuid4()) for _ in range(90)]
    stamp = datetime(2026, 10, 1, 1, tzinfo=UTC)
    rows = [
        [str(uuid.uuid4()), project, trace, session, user, stamp, 1, stamp, stamp]
        for trace in ids
    ]
    columns = [
        "id",
        "project_id",
        "trace_id",
        "trace_session_id",
        "end_user_id",
        "start_time",
        "_version",
        "created_at",
        "updated_at",
    ]
    admin.insert("acceptance_v1.spans", rows, column_names=columns)
    # Block retries must not re-fire rollups on replicas or on the initiator.
    admin.insert("acceptance_v1.spans", rows, column_names=columns)
    for client in clients:
        client.command("SYSTEM SYNC REPLICA acceptance_v1.spans_local")
        client.command("SYSTEM SYNC REPLICA acceptance_v1.spans_per_session_local")
        client.command("SYSTEM SYNC REPLICA acceptance_v1.span_user_rollup_local")
    assert (
        admin.query("SELECT count() FROM acceptance_v1.spans FINAL").first_row[0] == 90
    )
    assert (
        admin.query(
            "SELECT countMerge(span_count) FROM acceptance_v1.spans_per_session"
        ).first_row[0]
        == 90
    )
    assert (
        admin.query(
            "SELECT countMerge(span_count) FROM acceptance_v1.span_user_rollup"
        ).first_row[0]
        == 90
    )
    shards = admin.query(
        "SELECT _shard_num, count() FROM acceptance_v1.spans GROUP BY _shard_num"
    ).result_rows
    assert len(shards) == 3 and sum(n for _, n in shards) == 90
    assert admin.query(
        "SELECT trace_session_id, count() FROM acceptance_v1.spans FINAL GROUP BY trace_session_id"
    ).result_rows == [(uuid.UUID(session), 90)]
    # Outbox/update-engine-style versioned replacement preserves physical shard.
    update = rows[0].copy()
    update[6] = 2
    admin.insert("acceptance_v1.spans", [update], column_names=columns)
    assert (
        admin.query("SELECT count() FROM acceptance_v1.spans FINAL").first_row[0] == 90
    )
    old, new = str(uuid.uuid4()), str(uuid.uuid4())
    admin.insert(
        "acceptance_v1.end_user_id_remap",
        [[old, new]],
        column_names=["old_id", "new_id"],
    )
    # Cross-shard membership must be a global set, not local remap joins.
    query = "SELECT count() FROM acceptance_v1.spans WHERE project_id IN (SELECT project_id FROM acceptance_v1.traces)"
    admin.insert(
        "acceptance_v1.traces",
        [[ids[0], project, stamp]],
        column_names=["id", "project_id", "created_at"],
    )
    assert admin.query(query).first_row[0] >= 90
    evaluation = str(uuid.uuid4())
    admin.insert(
        "acceptance_v1.tracer_eval_logger_v2",
        [[str(uuid.uuid4()), evaluation, stamp, stamp, 1, 0.5]],
        column_names=[
            "id",
            "custom_eval_config_id",
            "created_at",
            "updated_at",
            "output_bool",
            "output_float",
        ],
    )
    assert (
        admin.query(
            "SELECT countMerge(n) FROM acceptance_v1.eval_per_config"
        ).first_row[0]
        == 1
    )
    print(
        "PASS schema reapply, 3-shard reads, same-block dedup, MV once-per-shard, cross-shard grouping, RMT replacement, GLOBAL membership"
    )
    if not allow_faults:
        print(
            "NOT RUN replica/queue fault gates: rerun a fresh fixture with --allow-faults"
        )
        return
    stopped = []
    try:
        subprocess.run(compose + ["stop", "ch11"], check=True)
        stopped.append("ch11")
        survivor = clients[1]
        assert (
            survivor.query("SELECT count() FROM acceptance_v1.spans FINAL").first_row[0]
            == 90
        )
        # Route to the affected shard by taking an existing identity known there.
        trace = survivor.query(
            "SELECT trace_id FROM acceptance_v1.spans_local LIMIT 1"
        ).first_row[0]
        fault_row = [
            str(uuid.uuid4()),
            project,
            trace,
            session,
            user,
            stamp,
            3,
            stamp,
            stamp,
        ]
        try:
            survivor.insert(
                "acceptance_v1.spans",
                [fault_row],
                column_names=columns,
                settings={"insert_quorum": 2, "insert_quorum_timeout": 1000},
            )
        except Exception as exc:
            if "quorum" not in str(exc).lower() and "replica" not in str(exc).lower():
                raise
        else:
            raise AssertionError("quorum=2 incorrectly acknowledged one available copy")
        # Availability mode is an explicit weaker policy, not a durability claim.
        fault_row[0] = str(uuid.uuid4())
        survivor.insert(
            "acceptance_v1.spans",
            [fault_row],
            column_names=columns,
            settings={"insert_quorum": 1},
        )
        assert (
            survivor.query("SELECT count() FROM acceptance_v1.spans FINAL").first_row[0]
            >= 91
        )
        assert (
            survivor.query(
                "SELECT sum(data_files) FROM system.distribution_queue"
            ).first_row[0]
            == 0
        )
        subprocess.run(compose + ["start", "ch11"], check=True)
        stopped.remove("ch11")
        deadline = time.monotonic() + 90
        while True:
            try:
                clients[0].command("SYSTEM SYNC REPLICA acceptance_v1.spans_local")
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(1)
        # Acknowledged pre-fault rows all survive; ambiguous failed quorum rows
        # may exist and are deliberately NOT assumed absent or exactly-once.
        found = (
            clients[0]
            .query(
                "SELECT uniqExact(id) FROM acceptance_v1.spans FINAL WHERE id IN %(ids)s",
                parameters={"ids": tuple(row[0] for row in rows)},
            )
            .first_row[0]
        )
        assert found == 90
        print(
            "PASS acknowledged pre-fault data survived; strict quorum refused unsafe ack; explicit quorum=1 writes continued; queue empty; replica caught up"
        )
    finally:
        for name in stopped:
            subprocess.run(compose + ["start", name], check=True)
        for client in clients:
            client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--allow-faults", action="store_true")
    args = parser.parse_args()
    run(args.directory, args.allow_faults)
