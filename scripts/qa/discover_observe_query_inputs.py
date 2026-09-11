#!/usr/bin/env python3
"""Collect bounded, typed catalog inputs for read-only query replay, not results.

Run using the backend virtualenv. Catalog suggestions do not prove density,
historical backfill completeness, or positive list membership at a given date.
"""

import argparse
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time

import replay_observe_filters as replay


def decode_seed(kind, raw):
    value = json.loads(raw)
    # The production catalog stores each selectable ARRAY MEMBER as a scalar,
    # not the source array. The array filter expects a list of members. Keep
    # scalar types intact and identify this representation explicitly.
    if kind == "array" and isinstance(value, (str, int, float, bool)):
        replay.canonical(value)
        return {
            "type": "array",
            "value": [value],
            "representation": "catalog_scalar_member",
        }
    valid = {
        "string": isinstance(value, str),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "array": isinstance(value, list),
        "map": isinstance(value, dict),
        "json": isinstance(value, (dict, list)),
    }
    if not valid.get(kind):
        raise replay.ReplayError("CATALOG_TYPE_VALUE_MISMATCH")
    replay.canonical(value)  # Reject non-finite numeric values rather than coerce.
    return {"type": kind, "value": value}


def control_head(client, database, scope):
    rows = client.execute(
        f"""SELECT control_sequence, action, catalog_epoch, target_catalog_revision,
                   projection_version, target_build_token, control_sha256
            FROM {database}.property_catalog_activation_control_events
            WHERE organization_id=%(organization_id)s AND workspace_id=%(workspace_id)s
            ORDER BY control_sequence DESC LIMIT 2""",
        scope,
    )
    if not rows or rows[0][1] not in ("activate", "rollback"):
        raise replay.ReplayError("CATALOG_NOT_ACTIVE")
    if len(rows) > 1 and rows[0][0] == rows[1][0] and rows[0] != rows[1]:
        raise replay.ReplayError("CONFLICTING_CATALOG_CONTROL")
    r = rows[0]
    return dict(
        zip(
            (
                "control_sequence",
                "action",
                "catalog_epoch",
                "catalog_revision",
                "projection_version",
                "build_token",
                "control_sha256",
            ),
            (r[0], r[1], r[2], r[3], r[4], str(r[5]), r[6]),
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production-read-only", action="store_true", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--expected-server", required=True)
    parser.add_argument("--catalog-database", required=True)
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--run-seconds", type=int, default=180)
    parser.add_argument("--max-pairs", type=int, default=300)
    parser.add_argument(
        "--attribute",
        action="append",
        help="Optional exact attribute names to discover",
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", args.catalog_database):
        raise replay.ReplayError("INVALID_DATABASE_IDENTIFIER")
    if not 0 < args.run_seconds <= 600 or not 0 < args.max_pairs <= 1000:
        raise replay.ReplayError("INVALID_DISCOVERY_BUDGET")
    if Path(args.output).exists():
        raise replay.ReplayError("OUTPUT_EXISTS")
    manifest = copy.deepcopy(replay.read_json(args.inventory))
    scope = {
        k: manifest["scope"][k]
        for k in ("organization_id", "workspace_id", "project_id")
    }
    from clickhouse_driver import Client

    client = Client(
        args.host,
        port=args.port,
        database="default",
        user=os.environ.get("OBSERVE_CH_USER", "default"),
        password=os.environ.get("OBSERVE_CH_PASSWORD", ""),
        connect_timeout=3,
        send_receive_timeout=6,
        settings={
            "readonly": 2,
            "max_execution_time": 3,
            "max_threads": 1,
            "max_memory_usage": 256 * 1024**2,
            "max_bytes_to_read": 64 * 1024**2,
            "max_result_rows": 12,
            "max_result_bytes": 1024**2,
            "read_overflow_mode": "throw",
            "result_overflow_mode": "throw",
            "timeout_overflow_mode": "throw",
            "use_query_cache": 0,
        },
    )
    lock = Path(args.ledger + ".lock")
    lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(lock_fd)
    try:
        if client.execute("SELECT hostName()")[0][0] != args.expected_server:
            raise replay.ReplayError("DATABASE_TARGET_MISMATCH")
        head = control_head(client, args.catalog_database, scope)
        for k in (
            "catalog_epoch",
            "catalog_revision",
            "projection_version",
            "build_token",
        ):
            if str(head[k]) != str(manifest["activation"][k]):
                raise replay.ReplayError("INVENTORY_CATALOG_ACTIVATION_CHANGED")
        run_id = replay.digest(
            {
                "scope": scope,
                "head": head,
                "database": args.catalog_database,
                "server": args.expected_server,
                "inventory": replay.digest(manifest),
                "version": 2,
            }
        )
        prior = replay.load_ledger(args.ledger, run_id)
        found = {r["case_id"]: r for r in prior}
        deadline, count, failures = time.monotonic() + args.run_seconds, 0, 0
        with os.fdopen(
            os.open(args.ledger, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600), "a"
        ) as out:
            for attr in manifest["attributes"]:
                if args.attribute and attr["name"] not in args.attribute:
                    continue
                for kind in attr["observed_types"]:
                    key = replay.digest([attr["name"], kind])
                    if key in found:
                        continue
                    if (
                        time.monotonic() + 6 > deadline
                        or count >= args.max_pairs
                        or failures >= 3
                    ):
                        break
                    start = time.monotonic()
                    row = {
                        "run_id": run_id,
                        "case_id": key,
                        "name": attr["name"],
                        "type": kind,
                        "seeds": [],
                        "values_complete": False,
                        "status": "COMPLETE",
                    }
                    params = {
                        **scope,
                        **head,
                        "attribute_key": attr["name"],
                        "attribute_type": kind,
                    }
                    try:
                        # Fixed sort-key prefix. No broad raw-span scan, data mutation,
                        # result sampling, ordering by recency, or string coercion.
                        raw = client.execute(
                            f"""
                            SELECT value_json FROM {args.catalog_database}.span_attribute_value_catalog
                            WHERE organization_id=%(organization_id)s AND workspace_id=%(workspace_id)s
                              AND project_id=%(project_id)s AND catalog_epoch=%(catalog_epoch)s
                              AND catalog_revision=%(catalog_revision)s AND build_token=%(build_token)s
                              AND source_kind='custom_attribute' AND attribute_key=%(attribute_key)s
                              AND attribute_type=%(attribute_type)s AND length(value_json)<=65536
                            LIMIT 12
                        """,
                            params,
                        )
                        unique = {}
                        for (value,) in raw:
                            seed = decode_seed(kind, value)
                            unique[replay.digest(seed)] = seed
                            if len(unique) >= 3:
                                break
                        row["seeds"] = list(unique.values())
                        row["read_rows"] = client.last_query.progress.rows
                        row["read_bytes"] = client.last_query.progress.bytes
                        failures = 0
                    except Exception as exc:
                        row.update(
                            status="ERROR",
                            exception_class=type(exc).__name__,
                            error_code=getattr(exc, "code", None),
                        )
                        failures += 1
                    row["elapsed_ms"] = round((time.monotonic() - start) * 1000, 2)
                    found[key] = row
                    out.write(replay.canonical(row) + "\n")
                    out.flush()
                    os.fsync(out.fileno())
                    count += 1
                    if count % 25 == 0:
                        print(
                            replay.canonical(
                                {
                                    "queried_pairs": count,
                                    "seeded_pairs": sum(
                                        bool(x["seeds"]) for x in found.values()
                                    ),
                                }
                            ),
                            flush=True,
                        )
                    time.sleep(0.05)
        if control_head(client, args.catalog_database, scope) != head:
            raise replay.ReplayError("CATALOG_ACTIVATION_CHANGED_REJECT_SEEDS")
        for attr in manifest["attributes"]:
            entries = [
                found.get(replay.digest([attr["name"], k]))
                for k in attr["observed_types"]
            ]
            if not any(entries):
                continue
            attr["seeds"] = [s for entry in entries if entry for s in entry["seeds"]]
            attr["values_complete"] = False
            attr["seed_discovery_complete"] = all(
                e and e["status"] == "COMPLETE" for e in entries
            )
        manifest["seed_provenance"] = {
            "layer": "read_only_catalog_suggestions",
            "server": args.expected_server,
            "control": head,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "density_verified": False,
            "backfill_completeness_verified": False,
            "all_values_enumerated": False,
            "max_input_value_json_bytes": 65536,
        }
        replay.private_write(args.output, manifest)
        print(
            replay.canonical(
                {
                    "queried_pairs_this_run": count,
                    "total_pairs_recorded": len(found),
                    "seeded_pairs": sum(bool(r["seeds"]) for r in found.values()),
                    "errors": sum(r["status"] == "ERROR" for r in found.values()),
                    "output": args.output,
                }
            ),
            flush=True,
        )
    finally:
        client.disconnect()
        lock.unlink()


if __name__ == "__main__":
    try:
        main()
    except replay.ReplayError as exc:
        raise SystemExit(str(exc)) from None
