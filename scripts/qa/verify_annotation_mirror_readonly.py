#!/usr/bin/env python3
"""Compare an exhaustive scoped PG Score snapshot with the live PeerDB mirror.

No data repair, mutation, replica wait or table-wide query is performed. A
comparison is a timestamped observation, not a claim of a shared transaction.
"""

import argparse
from datetime import datetime, timezone
import json
import os
import time
from uuid import UUID, uuid4

import replay_observe_filters as replay


NIL = "00000000-0000-0000-0000-000000000000"


def normalized_score(row):
    result = dict(row)
    for key in (
        "score_id",
        "project_id",
        "label_id",
        "annotator_id",
        "trace_id",
        "span_id",
    ):
        value = str(result[key]) if result.get(key) is not None else ""
        result[key] = None if value in ("", NIL) else value
    if isinstance(result["value"], str):
        result["value"] = json.loads(result["value"])
    for key in ("created_at", "updated_at"):
        value = result[key]
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        result[key] = value.astimezone(timezone.utc).isoformat(timespec="microseconds")
    result["deleted"] = bool(result["deleted"])
    return result


def compare_scores(source, mirrored):
    expected = {row["score_id"]: normalized_score(row) for row in source}
    actual = {str(row["score_id"]): normalized_score(row) for row in mirrored}
    if len(expected) != len(source) or len(actual) != len(mirrored):
        raise replay.ReplayError("DUPLICATE_SCORE_ID_IN_SNAPSHOT")
    missing, extra = set(expected) - set(actual), set(actual) - set(expected)
    changed = {
        key for key in set(expected) & set(actual) if expected[key] != actual[key]
    }
    return {
        "status": "MATCH" if not (missing or extra or changed) else "MISMATCH",
        "source_rows": len(expected),
        "mirror_rows": len(actual),
        "source_live_rows": sum(not row["deleted"] for row in expected.values()),
        "source_soft_deleted_rows": sum(row["deleted"] for row in expected.values()),
        "missing_ids": len(missing),
        "extra_ids": len(extra),
        "changed_records": len(changed),
        "source_sha256": replay.digest(expected),
        "mirror_sha256": replay.digest(actual),
        "mismatch_id_hashes": [
            replay.digest(key) for key in sorted(missing | extra | changed)[:10]
        ],
        "same_transaction_snapshot": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production-read-only", action="store_true", required=True)
    parser.add_argument("--metadata", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--expected-server", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    document = replay.read_json(args.metadata)
    if (
        document.get("transaction_read_only") is not True
        or document.get("annotation_scores_complete") is not True
    ):
        raise replay.ReplayError("EXHAUSTIVE_READ_ONLY_SOURCE_SNAPSHOT_REQUIRED")
    projects = sorted(
        {str(UUID(project)) for project in document["authorized_projects"]}
    )
    if not 1 <= len(projects) <= 100 or any(
        row["project_id"] not in projects
        for row in document["annotation_scores_snapshot"]
    ):
        raise replay.ReplayError("SCORE_PROJECT_SCOPE_MISMATCH")
    from clickhouse_driver import Client

    client = Client(
        "127.0.0.1",
        port=args.port,
        database="default",
        user=os.environ.get("OBSERVE_CH_USER", "default"),
        password=os.environ.get("OBSERVE_CH_PASSWORD", ""),
        connect_timeout=3,
        send_receive_timeout=15,
        settings={
            "readonly": 2,
            "max_execution_time": 10,
            "max_threads": 1,
            "max_memory_usage": 512 * 1024**2,
            "max_bytes_to_read": 512 * 1024**2,
            "max_result_rows": 10001,
            "max_result_bytes": 32 * 1024**2,
            "read_overflow_mode": "throw",
            "result_overflow_mode": "throw",
            "timeout_overflow_mode": "throw",
            "optimize_move_to_prewhere_if_final": 0,
        },
    )
    started = time.monotonic()
    try:
        identity = client.execute("SELECT hostName(), currentDatabase(), version()")[0]
        if identity[:2] != (args.expected_server, "default"):
            raise replay.ReplayError("DATABASE_TARGET_MISMATCH")
        rows, columns = client.execute(
            """SELECT id AS score_id,
            tracer_project_id AS project_id, label_id, value, annotator_id,
            trace_id, observation_span_id AS span_id, created_at, updated_at, deleted
            FROM model_hub_score FINAL
            WHERE tracer_project_id IN %(project_ids)s AND _peerdb_is_deleted=0
            ORDER BY id LIMIT 10001""",
            {"project_ids": tuple(UUID(project) for project in projects)},
            with_column_types=True,
            query_id="annotation-cdc-parity-" + uuid4().hex[:12],
        )
        if len(rows) > 10000:
            raise replay.ReplayError("ANNOTATION_MIRROR_SNAPSHOT_OVERFLOW")
        mirrored = [dict(zip([col[0] for col in columns], row)) for row in rows]
        result = compare_scores(document["annotation_scores_snapshot"], mirrored)
        result.update(
            source_captured_at=document["captured_at"],
            mirror_checked_at=datetime.now(timezone.utc).isoformat(),
            server=identity[0],
            version=identity[2],
            projects=projects,
            elapsed_ms=round((time.monotonic() - started) * 1000, 2),
            source_document_sha256=replay.digest(document),
        )
        replay.private_write(args.output, result)
        print(
            replay.canonical(
                {
                    key: result[key]
                    for key in (
                        "status",
                        "source_rows",
                        "mirror_rows",
                        "missing_ids",
                        "extra_ids",
                        "changed_records",
                        "elapsed_ms",
                    )
                }
            )
        )
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
