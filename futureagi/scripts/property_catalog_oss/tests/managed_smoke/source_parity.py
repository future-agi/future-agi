#!/usr/bin/env python3
"""Real CH25 source-reader parity against candidates round-tripped through Kafka."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from run import LABEL, ROOT, load_run, save


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> None:
    action, directory = sys.argv[1], Path(sys.argv[2])
    run = load_run(directory)
    m = run.manifest
    containers = json.loads(run.command(["docker", "inspect", *run.owned()]).stdout)
    ch = next(
        item
        for item in containers
        if item["Config"]["Labels"]["com.docker.compose.service"] == "clickhouse"
    )
    require(ch["Config"]["Labels"][LABEL] == m["run_id"], "wrong container owner")
    for name, port in (("http", "8123/tcp"), ("native", "9000/tcp")):
        require(
            ch["NetworkSettings"]["Ports"][port]
            == [{"HostIp": "127.0.0.1", "HostPort": str(m["ports"][name])}],
            "endpoint is not owned loopback publication",
        )

    # Minimal library settings: no project .env, app startup, pytest hooks or ORM.
    sys.path.insert(0, str(ROOT / "futureagi"))
    from django.conf import settings

    from tfc.settings.runtime_setting_specs import RUNTIME_NUMERIC_SETTING_SPECS

    settings.configure(
        **{key: spec.default for key, spec in RUNTIME_NUMERIC_SETTING_SPECS.items()}
    )
    from clickhouse_driver import Client

    from tracer.services.clickhouse.v2.property_catalog.publisher import (
        SharedCatalogDeadline,
    )
    from tracer.services.clickhouse.v2.property_catalog.span_source import (
        CanonicalSpanSourceReader,
        _build_span_catalog_rows,
    )

    def client(
        user: str = "default", password: str = "", database: str = "default"
    ) -> Client:
        return Client(
            "127.0.0.1",
            port=m["ports"]["native"],
            database=database,
            user=user,
            password=password,
            connect_timeout=3,
            send_receive_timeout=10,
            settings={
                "max_execution_time": 8,
                "max_threads": 1,
                "max_memory_usage": 536870912,
            },
        )

    if action == "seed":
        from identity_smoke import run_checks

        organization, workspace, project, foreign = (str(uuid4()) for _ in range(4))
        at = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(
            hours=2
        )
        seen = at.strftime("%Y-%m-%d %H:%M:%S.%f")
        row = {
            "project_id": project,
            "org_id": organization,
            "observation_type": "span",
            "service_name": "managed-smoke",
            "start_time": seen,
            "created_at": seen,
            "updated_at": seen,
            "trace_id": "smoke-trace",
            "id": "span-main",
            "name": "Smoke span",
            "model": "Smoke-Model",
            "attrs_string": {"plan": "Pro", "quote": 'a"b\\c', "unicode": "café"},
            "attrs_number": {"score": 1.5, "negative": -12.5, "zero": 0},
            "attrs_bool": {"enabled": 1, "disabled": 0},
            "attributes_extra": json.dumps(
                {
                    "tags": ["A", "B", "A", None, 3, True, {"nested": 1}],
                    "map_only": {"nested": 4},
                    "null_only": None,
                    "empty_array": [],
                },
                separators=(",", ":"),
            ),
            "is_deleted": 0,
            "_version": 1,
        }
        old = {
            **row,
            "id": "span-versioned",
            "attrs_string": {"versioned": "old"},
            "attrs_number": {},
            "attrs_bool": {},
            "attributes_extra": "{}",
            "model": "",
        }
        latest = {**old, "attrs_string": {"versioned": "new"}, "_version": 2}
        deleted = {
            **old,
            "id": "span-deleted",
            "attrs_string": {"deleted_value": "MUST_NOT_APPEAR"},
        }
        tombstone = {**deleted, "_version": 2, "is_deleted": 1}
        other = {
            **old,
            "project_id": foreign,
            "id": "span-foreign",
            "attrs_string": {"foreign_value": "MUST_NOT_APPEAR"},
        }
        rows = [row, old, latest, deleted, tombstone, other]
        admin = client()
        try:
            columns = tuple(row)
            admin.execute(
                f"INSERT INTO spans ({','.join(columns)}) VALUES",
                [
                    tuple(
                        item[key]
                        if key not in {"start_time", "created_at", "updated_at"}
                        else at
                        for key in columns
                    )
                    for item in rows
                ],
            )
        finally:
            admin.disconnect()
        save(
            directory / "candidate-input.json",
            {
                "format": "futureagi.managed-smoke-input.v1",
                "broker": f"127.0.0.1:{m['ports']['kafka']}",
                "topic": m["candidate_topic"],
                "organization_id": organization,
                "workspace_id": workspace,
                "rows": [row, latest],
                "output": str(directory / "kafka-evidence.json"),
            },
        )
        save(
            directory / "source-fixture.json",
            {
                "project_id": project,
                "organization_id": organization,
                "workspace_id": workspace,
                "since": at.isoformat(),
                "until": (at + timedelta(hours=1)).isoformat(),
                "physical_rows": len(rows),
                "logical_rows": 2,
            },
        )
        run_checks(directory, m)
        print(
            "Seeded six physical spans in the new isolated source; expected two scoped logical spans."
        )
        return
    require(action == "verify", "unknown action")
    fixture = json.loads((directory / "source-fixture.json").read_text())
    kafka = json.loads((directory / "kafka-evidence.json").read_text())
    require(
        kafka["roundtrip_records"] == 2 and kafka["duplicate_candidate_id_equal"],
        "Kafka duplicate/roundtrip evidence missing",
    )
    candidates = kafka["candidates"]
    require(len(candidates) == 1, "expected one managed candidate")
    candidate = candidates[0]
    require(
        candidate["version"] == 2
        and candidate["catalog_epoch"] == candidate["projection_version"] == 0,
        "collector allocated identity or did not use managed candidates",
    )
    require(candidate["gap_reasons"] == [], "candidate contains gaps")
    from tracer.services.clickhouse.client import ClickHouseClient
    from tracer.services.clickhouse.v2.property_catalog.dev_runtime import (
        NativeSourceClient,
    )

    # Use the actual readonly=1 transport adapter. A bare clickhouse-driver
    # sends setting changes that this deliberately locked source role rejects.
    source = ClickHouseClient(
        host="127.0.0.1",
        port=m["ports"]["native"],
        database="default",
        user="property_catalog_oss_source",
        password="oss-catalog-source-local-only",
        server_enforced_readonly=True,
        pool_size=1,
        connect_timeout=3,
        send_timeout=10,
        receive_timeout=10,
    )

    deadline = SharedCatalogDeadline(wall_ms=60_000)
    reader = CanonicalSpanSourceReader(
        NativeSourceClient(
            source, source_database="default", catalog_database=m["database"]
        ),
        source_database="default",
        catalog_database=m["database"],
        deadline=deadline,
        page_rows=1,
    )
    try:
        frozen = reader.freeze(
            project_ids=(fixture["project_id"],),
            since=datetime.fromisoformat(fixture["since"]),
            until=datetime.fromisoformat(fixture["until"]),
        )
        cursor, spans, values, key_types = None, [], set(), set()
        for _page in range(8):
            page = reader.read_page(frozen, cursor=cursor)
            spans.extend(page.spans)
            for span in page.spans:
                keys, built, gaps = _build_span_catalog_rows(
                    project_id=fixture["project_id"], catalog_epoch=1, span=span
                )
                require(not gaps, f"source projection gaps: {gaps}")
                key_types.update(
                    (key.attribute_key, key.attribute_type) for key in keys
                )
                values.update(
                    (
                        value.source_kind,
                        value.attribute_key,
                        value.attribute_type,
                        value.value_fingerprint,
                        value.value_json,
                    )
                    for value in built
                )
            if page.terminal:
                break
            require(page.next_cursor != cursor, "source cursor did not advance")
            cursor = page.next_cursor
        else:
            raise RuntimeError("fixture exceeded eight bounded source pages")
        require(
            len(spans) == fixture["logical_rows"] == candidate["source_rows"],
            "logical source rows disagree",
        )
        projected = {
            (
                v["source_kind"],
                v["attribute_key"],
                v["attribute_type"],
                v["value_fingerprint"],
                v["value_json"],
            )
            for v in candidate["values"]
        }
        require(
            values == projected,
            f"source/candidate parity mismatch: historical-only={values - projected}; candidate-only={projected - values}",
        )
        gold = {
            ("plan", "string", '"Pro"'),
            ("quote", "string", json.dumps('a"b\\c')),
            ("unicode", "string", json.dumps("café", ensure_ascii=False)),
            ("score", "number", "1.5"),
            ("negative", "number", "-12.5"),
            ("zero", "number", "0"),
            ("enabled", "boolean", "true"),
            ("disabled", "boolean", "false"),
            ("tags", "array", '"A"'),
            ("tags", "array", '"B"'),
            ("tags", "array", "3"),
            ("tags", "array", "true"),
            ("model", "string", '"Smoke-Model"'),
            ("versioned", "string", '"new"'),
        }
        actual = {
            (key, kind, value) for _source, key, kind, _fingerprint, value in values
        }
        require(
            actual == gold,
            f"golden fixture mismatch: missing={gold - actual}; unexpected={actual - gold}",
        )
        require(
            ("map_only", "map") in key_types
            and ("empty_array", "array") in key_types
            and ("null_only", "json") in key_types,
            "key-only map/null/array definitions missing",
        )
        require(
            not any(key in {"deleted_value", "foreign_value"} for key, _ in key_types),
            "deleted or foreign field leaked",
        )
        # Test the actual source principal cannot write, with a SELECT-only insert
        # that would touch no rows even if its grants were accidentally widened.
        probe = run.execute(
            "clickhouse",
            [
                "clickhouse-client",
                "--user",
                "property_catalog_oss_source",
                "--password",
                "oss-catalog-source-local-only",
                "--query",
                "INSERT INTO default.spans SELECT * EXCEPT (_peerdb_is_deleted, input_length, output_length) FROM default.spans WHERE 0",
            ],
            check=False,
        )
        denied = probe.returncode != 0 and any(
            f"Code: {code}." in probe.stderr for code in (164, 497)
        )
        require(denied, "source identity allowed INSERT")
        report = {
            "status": "passed",
            "physical_source_rows": fixture["physical_rows"],
            "scoped_logical_rows": len(spans),
            "value_tuples": len(values),
            "key_types": sorted(key_types),
            "source_write_denied": denied,
            "kafka_records": 2,
            "candidate_version": 2,
            "activation_written": False,
            "lifecycle_qualified": False,
        }
        save(directory / "parity-evidence.json", report)
        print(json.dumps(report, sort_keys=True))
    finally:
        source.close()


if __name__ == "__main__":
    main()
