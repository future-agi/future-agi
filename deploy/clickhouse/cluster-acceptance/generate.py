#!/usr/bin/env python3
"""Render a small isolated 3x2 + three-Keeper fixture; never start containers."""

import argparse
import json
import secrets
import shutil
from pathlib import Path


def generate(out, port):
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    password = secrets.token_hex(24)
    project = "fagi-cluster-" + secrets.token_hex(5)
    image = "clickhouse/clickhouse-server:25.3-alpine"
    services = {}
    raft = "".join(
        f"<server><id>{n}</id><hostname>keeper{n}</hostname><port>9234</port></server>"
        for n in range(1, 4)
    )
    for n in range(1, 4):
        config = f"""<clickhouse><logger><level>warning</level><console>true</console></logger><listen_host>0.0.0.0</listen_host><keeper_server><tcp_port>9181</tcp_port><server_id>{n}</server_id><log_storage_path>/var/lib/clickhouse/coordination/log</log_storage_path><snapshot_storage_path>/var/lib/clickhouse/coordination/snapshots</snapshot_storage_path><raft_configuration>{raft}</raft_configuration></keeper_server></clickhouse>"""
        (out / f"keeper{n}.xml").write_text(config)
        services[f"keeper{n}"] = {
            "image": image,
            "hostname": f"keeper{n}",
            "command": ["clickhouse", "keeper", "--config-file=/etc/keeper.xml"],
            "volumes": [
                f"./keeper{n}.xml:/etc/keeper.xml:ro",
                f"keeper{n}:/var/lib/clickhouse",
            ],
            "mem_limit": "256m",
        }
    shards = "".join(
        "<shard><internal_replication>true</internal_replication>"
        + "".join(
            f'<replica><host>ch{s}{r}</host><port>9000</port><user>default</user><password from_env="CLICKHOUSE_PASSWORD"/></replica>'
            for r in (1, 2)
        )
        + "</shard>"
        for s in (1, 2, 3)
    )
    keepers = "".join(
        f"<node><host>keeper{n}</host><port>9181</port></node>" for n in range(1, 4)
    )
    users = """<clickhouse><profiles><default><async_insert>0</async_insert><distributed_foreground_insert>1</distributed_foreground_insert><insert_quorum>2</insert_quorum><insert_quorum_timeout>1500</insert_quorum_timeout><distributed_product_mode>global</distributed_product_mode><prefer_global_in_and_join>1</prefer_global_in_and_join><distributed_group_by_no_merge>0</distributed_group_by_no_merge><optimize_skip_unused_shards>0</optimize_skip_unused_shards><optimize_distributed_group_by_sharding_key>0</optimize_distributed_group_by_sharding_key><skip_unavailable_shards>0</skip_unavailable_shards><max_parallel_replicas>1</max_parallel_replicas><max_threads>2</max_threads><max_memory_usage>400000000</max_memory_usage><deduplicate_blocks_in_dependent_materialized_views>1</deduplicate_blocks_in_dependent_materialized_views></default></profiles><users><default><password from_env="CLICKHOUSE_PASSWORD"/><networks><ip>::/0</ip></networks><access_management>1</access_management></default></users></clickhouse>"""
    (out / "users.xml").write_text(users)
    storage = (
        Path(__file__).resolve().parents[2]
        / "helm/futureagi/files/clickhouse/config.d/storage-policy.xml"
    )
    (out / "storage-policy.xml").write_text(storage.read_text())
    for s in (1, 2, 3):
        for r in (1, 2):
            name = f"ch{s}{r}"
            config = f"""<clickhouse><logger><level>warning</level><console>true</console></logger><listen_host>0.0.0.0</listen_host><max_server_memory_usage>600000000</max_server_memory_usage><mark_cache_size>16777216</mark_cache_size><uncompressed_cache_size>8388608</uncompressed_cache_size><background_pool_size>2</background_pool_size><background_merges_mutations_concurrency_ratio>4</background_merges_mutations_concurrency_ratio><merge_tree><number_of_free_entries_in_pool_to_execute_mutation>1</number_of_free_entries_in_pool_to_execute_mutation><number_of_free_entries_in_pool_to_execute_optimize_entire_partition>1</number_of_free_entries_in_pool_to_execute_optimize_entire_partition><number_of_free_entries_in_pool_to_lower_max_size_of_merge>1</number_of_free_entries_in_pool_to_lower_max_size_of_merge></merge_tree><macros><shard>{s}</shard><replica>{r}</replica></macros><zookeeper>{keepers}</zookeeper><distributed_ddl><path>/acceptance/ddl</path></distributed_ddl><remote_servers><acceptance>{shards}</acceptance></remote_servers><query_log remove="1"/><trace_log remove="1"/><metric_log remove="1"/><asynchronous_metric_log remove="1"/><part_log remove="1"/></clickhouse>"""
            (out / f"{name}.xml").write_text(config)
            services[name] = {
                "image": image,
                "hostname": name,
                "environment": {
                    "CLICKHOUSE_PASSWORD": password,
                    "CLICKHOUSE_SKIP_USER_SETUP": "1",
                },
                "volumes": [
                    f"./{name}.xml:/etc/clickhouse-server/config.d/cluster.xml:ro",
                    "./users.xml:/etc/clickhouse-server/users.d/cluster.xml:ro",
                    "./storage-policy.xml:/etc/clickhouse-server/config.d/storage-policy.xml:ro",
                    f"{name}:/var/lib/clickhouse",
                ],
                "ports": [f"127.0.0.1:{port + (s - 1) * 2 + r - 1}:8123"],
                "mem_limit": "768m",
                "ulimits": {"nofile": {"soft": 65536, "hard": 65536}},
            }
    (out / "compose.json").write_text(
        json.dumps(
            {"services": services, "volumes": {name: {} for name in services}}, indent=2
        )
    )
    (out / "fixture.json").write_text(
        json.dumps({"project": project, "password": password, "port": port})
    )
    for file in out.iterdir():
        file.chmod(0o644 if file.suffix == ".xml" else 0o600)
    print(
        f"Rendered {out}; nothing started. Uses approximately 5.3 GiB maximum container memory."
    )
    compose = shutil.which("docker-compose") or "docker compose"
    print(f"{compose} -p {project} -f {out / 'compose.json'} up -d")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", required=True, help="NEW directory on a sufficiently sized disk"
    )
    parser.add_argument(
        "--port", type=int, default=18123, help="First of six loopback-only ports"
    )
    args = parser.parse_args()
    if not 1024 <= args.port <= 65530:
        parser.error("port must leave six unprivileged ports")
    generate(args.output, args.port)
