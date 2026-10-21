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
import time
import uuid
from datetime import UTC, datetime, timedelta


def run(directory, allow_faults, application=False):
    from runtime_paths import initialize_runtime

    directory = initialize_runtime(directory)
    fixture = json.loads((directory / "fixture.json").read_text())
    if not fixture["project"].startswith("fagi-cluster-"):
        raise ValueError("not an isolated acceptance fixture")
    if application:
        from fixture_connections import configure

        # Match the backend image: environment precedes application imports.
        configure(fixture)
    import clickhouse_connect

    from tracer.services.clickhouse.v2.cluster_apply import apply_plan
    from tracer.services.clickhouse.v2.cluster_cdc import verify_columns
    from tracer.services.clickhouse.v2.cluster_schema import (
        ClusterTopology,
        compile_addition,
        compile_definitions,
        native_plan,
    )

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
    application_evidence = {}
    if application:
        from app_bootstrap import prepare, qualify

        # Production order: relational migrations, then native schema, catalog
        # and the existing outbox installer. Do not bootstrap CH ahead of PG.
        application_evidence = prepare(fixture)
        application_evidence.update(qualify(fixture))
        assert apply_plan(admin, topology, plan, password=fixture["password"]) == 0
    else:
        assert apply_plan(admin, topology, plan, password=fixture["password"]) == len(
            plan
        )
    verify_columns(admin, topology, plan)
    assert apply_plan(clients[3], topology, plan, password=fixture["password"]) == 0
    versions = [client.query("SELECT version()").first_row[0] for client in clients]
    assert len(set(versions)) == 1 and versions[0] == "25.3.14.14", versions
    evidence = {
        "native_schema_steps": len(plan),
        "schema_replay": True,
        "server_versions": versions,
        "clickhouse_image": fixture["clickhouse_image"],
        **application_evidence,
    }
    # A later migration adds storage plus an MV over a PREEXISTING native table.
    # The old baseline receipts stay unchanged and its raw data is never replaced.
    upgrades = compile_definitions(
        [
            "CREATE TABLE IF NOT EXISTS acceptance_upgrade_counts (project_id UUID, n AggregateFunction(count)) ENGINE=AggregatingMergeTree ORDER BY project_id",
            "CREATE MATERIALIZED VIEW IF NOT EXISTS acceptance_upgrade_counts_mv TO acceptance_upgrade_counts AS SELECT project_id, countState() AS n FROM spans WHERE acceptance_generation = 0 GROUP BY project_id",
        ],
        topology,
        version="acceptance-additive-v1",
        shard_keys={"acceptance_upgrade_counts": "cityHash64(project_id)"},
        known_tables={"spans"},
    )
    upgrades += compile_addition(
        "ALTER TABLE spans ADD COLUMN IF NOT EXISTS acceptance_generation UInt8 DEFAULT 0",
        topology,
        version="acceptance-additive-v1",
        known_tables={"spans"},
    )
    upgrades = [step for step in upgrades if step.engine != "MaterializedView"] + [
        step for step in upgrades if step.engine == "MaterializedView"
    ]
    assert apply_plan(
        admin, topology, plan + upgrades, password=fixture["password"]
    ) == len(upgrades)
    verify_columns(admin, topology, plan + upgrades)
    assert (
        apply_plan(clients[4], topology, plan + upgrades, password=fixture["password"])
        == 0
    )
    evidence["post_bootstrap_additive_upgrade"] = True
    project, session, user = [str(uuid.uuid4()) for _ in range(3)]
    ids = [str(uuid.uuid4()) for _ in range(90)]
    stamp = datetime(2026, 10, 1, 1, tzinfo=UTC)
    # Curated dimensions use named identities, not an `id` column. Updates
    # must route with the same project/entity key and collapse under FINAL.
    organization = str(uuid.uuid4())
    for offset, label in enumerate(("before", "after")):
        version = stamp + timedelta(seconds=offset)
        admin.insert(
            "acceptance_v1.end_users",
            [[project, user, organization, label, stamp, version]],
            column_names=[
                "project_id",
                "end_user_id",
                "organization_id",
                "user_id",
                "first_seen",
                "version",
            ],
        )
        admin.insert(
            "acceptance_v1.trace_sessions",
            [[project, session, label, stamp, version]],
            column_names=[
                "project_id",
                "trace_session_id",
                "external_session_id",
                "first_seen",
                "version",
            ],
        )
    assert admin.query(
        "SELECT user_id FROM acceptance_v1.end_users FINAL WHERE project_id = %(project)s AND end_user_id = %(entity)s",
        parameters={"project": project, "entity": user},
    ).result_rows == [("after",)]
    assert admin.query(
        "SELECT external_session_id FROM acceptance_v1.trace_sessions FINAL WHERE project_id = %(project)s AND trace_session_id = %(entity)s",
        parameters={"project": project, "entity": session},
    ).result_rows == [("after",)]
    evidence["curated_dimension_updates"] = True
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
        client.command(
            "SYSTEM SYNC REPLICA acceptance_v1.acceptance_upgrade_counts_local"
        )
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
    assert (
        admin.query(
            "SELECT countMerge(n) FROM acceptance_v1.acceptance_upgrade_counts"
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
    evidence.update(
        logical_span_count=90,
        shard_count=3,
        local_mv_fanout=True,
        eval_insert=True,
        rmt_update=True,
        global_membership=True,
    )
    (directory / "evidence.json").write_text(json.dumps(evidence, indent=2))
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
        evidence.update(
            replica_loss=True,
            strict_quorum_refused_unsafe_ack=True,
            explicit_availability_mode=True,
            acknowledged_rows_survived=found,
        )
        (directory / "evidence.json").write_text(json.dumps(evidence, indent=2))
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
    parser.add_argument(
        "--application",
        action="store_true",
        help="Require real app migrations, full CDC bootstrap and insert/update/delete",
    )
    args = parser.parse_args()
    run(args.directory, args.allow_faults, args.application)
