"""Read-only Kafka -> exact delivery/value evidence after the private runner exits.

This prevents a successful canonical source audit from masquerading as exercised
Kafka delivery. Envelope bytes are parsed by the actual production wire parser.
"""

import base64
import json
import re
import subprocess

from harness import canonical, digest, read_file, save_new

VALUE_FIELDS = (
    "source_kind",
    "attribute_key",
    "attribute_type",
    "value_fingerprint",
    "value_json",
)
EXPECTED_NAMES = {"otlp_live", "otlp_late", "otlp_restart_live", "otlp_restart_late"}
ORDERED_NAMES = {"otlp_ordered", "otlp_restart_ordered"}
EXPECTED_NAMES |= ORDERED_NAMES


def match_records(candidates, ordered, fixture):
    # This host-side checker does not boot Django or connect to application
    # databases. Standalone parsing uses the checked-in default wire limits.
    from django.conf import settings

    if not settings.configured:
        settings.configure()
    from tracer.services.clickhouse.v2.property_catalog.wire import parse_envelope

    expected, matched, deliveries, values = set(), set(), set(), set()
    for raw in candidates:
        candidate = json.loads(raw)
        if (
            candidate["format"] != "futureagi.property-catalog-candidate"
            or candidate["version"] != 2
            or candidate["organization_id"] != fixture["organization_id"]
            or candidate["workspace_id"] != fixture["workspace_id"]
            or candidate["project_id"] != fixture["project_id"]
            or candidate["catalog_epoch"] != 0
            or candidate["projection_version"] != 0
            or candidate["gap_reasons"]
        ):
            raise ValueError(
                "collector candidate is not the exact managed tenant contract"
            )
        for row in candidate["values"]:
            if row["attribute_key"] in EXPECTED_NAMES:
                expected.add(tuple(row[k] for k in VALUE_FIELDS))
    if {r[1] for r in expected} != EXPECTED_NAMES or any(
        r[4] != '"initial"' for r in expected
    ):
        raise ValueError("Kafka did not retain every pre/post restart OTLP candidate")
    # Current/late observations may deliberately use reconciliation; the runner
    # proves their exact API values on every replica. Require real ordered
    # delivery for BOTH dedicated in-window probes, before and after restart.
    expected = {row for row in expected if row[1] in ORDERED_NAMES}
    for raw in ordered:
        envelope = parse_envelope(raw)
        document = envelope.document
        if (
            document["organization_id"] != fixture["organization_id"]
            or document["workspace_id"] != fixture["workspace_id"]
        ):
            raise ValueError("ordered record has a foreign tenant")
        for chunk in document["payload"]["chunks"]:
            if chunk["table"] != "span_attribute_value_catalog":
                continue
            for raw_row in base64.b64decode(
                chunk["json_each_row"], validate=True
            ).splitlines():
                row = json.loads(raw_row)
                selected = tuple(row[k] for k in VALUE_FIELDS)
                if selected not in expected:
                    continue
                if row["project_id"] != fixture["project_id"]:
                    raise ValueError("ordered value has a foreign project")
                matched.add(selected)
                deliveries.add((envelope.envelope_id, envelope.payload_sha256))
                values.add(
                    (
                        str(document["build_token"]),
                        int(document["catalog_revision"]),
                        *selected,
                    )
                )
    if matched != expected:
        raise ValueError(
            "canonical audit cannot substitute for missing ordered Kafka value rows"
        )
    return deliveries, values


def topic_records(docker, topic):
    args = [
        "/opt/kafka/bin/kafka-console-consumer.sh",
        "--bootstrap-server",
        "kafka:9092",
        "--topic",
        topic,
        "--from-beginning",
        "--timeout-ms",
        "5000",
        "--max-messages",
        "64",
        "--consumer-property",
        "isolation.level=read_committed",
    ]
    try:
        result = docker.exec_owned("kafka", args, timeout=15)
        output = result.stdout
    except subprocess.CalledProcessError as exc:
        # Kafka reports its finite idle end through this specific nonzero result.
        # Authentication/parser/startup errors never count as an empty topic.
        if (
            exc.returncode != 1
            or "org.apache.kafka.common.errors.TimeoutException"
            not in (exc.stderr or "")
        ):
            raise
        output = exc.stdout
    if not output or len(output.encode()) > 4 << 20:
        raise ValueError("Kafka evidence empty or over bounded byte budget")
    records = [line.encode() for line in output.splitlines() if line]
    if len(records) >= 64:
        raise ValueError(
            "Kafka evidence reached inventory bound; completeness unproven"
        )
    return records


def qualify(docker, directory, m):
    from application_lane import NODES

    fixture = json.loads(read_file(directory / "source-fixture.json"))
    candidates = topic_records(docker, m["candidate_topic"])
    ordered = topic_records(docker, m["ordered_topic"])
    save_new(directory / "kafka-candidates.jsonl", b"\n".join(candidates) + b"\n")
    save_new(directory / "kafka-ordered.jsonl", b"\n".join(ordered) + b"\n")
    deliveries, values = match_records(candidates, ordered, fixture)
    # All interpolations below are exact run-owned identifiers, parser-validated
    # SHA/UUID/integer fields, or strings escaped by the native driver's helper.
    from clickhouse_driver.util.escape import escape_param

    def escaped(value):
        # Only parser-validated strings reach this helper, so no timezone or
        # connection context is needed by the installed driver.
        if not isinstance(value, str):
            raise ValueError("evidence SQL binding requires a string")
        return escape_param(value, context=None)

    def query(node, sql):
        result = docker.exec_owned(
            node,
            [
                "clickhouse-client",
                "--database",
                m["database"],
                "--query",
                sql
                + " SETTINGS max_threads=1, max_execution_time=5, max_result_rows=257, max_result_bytes=1048576 FORMAT JSONEachRow",
            ],
            timeout=8,
        )
        if len(result.stdout.encode()) > 1 << 20:
            raise ValueError("member evidence exceeds byte bound")
        return [json.loads(line) for line in result.stdout.splitlines() if line]

    ids = sorted(row[0] for row in deliveries)
    if (
        not ids
        or len(ids) > 32
        or any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in ids)
    ):
        raise ValueError("unbounded exact ordered delivery inventory")
    ids_sql = ",".join(escaped(value) for value in ids)
    runtime = []
    for node in NODES:
        observed = query(
            node,
            "SELECT DISTINCT envelope_id, payload_sha256 FROM property_catalog_deliveries "
            f"WHERE envelope_id IN ({ids_sql}) AND transport='kafka' "
            "AND envelope_format='futureagi.property-catalog-envelope'",
        )
        if {
            tuple(row[k] for k in ("envelope_id", "payload_sha256")) for row in observed
        } != deliveries:
            raise ValueError(
                "exact Kafka delivery missing or conflicting on a serving member"
            )
        for token, revision, *value in sorted(values):
            clauses = [
                "build_token=" + escaped(token),
                "catalog_revision=" + str(revision),
                "project_id=" + escaped(fixture["project_id"]),
            ]
            clauses.extend(
                k + "=" + escaped(v) for k, v in zip(VALUE_FIELDS, value, strict=True)
            )
            found = query(
                node,
                "SELECT count() AS n FROM span_attribute_value_catalog WHERE "
                + " AND ".join(clauses),
            )
            if len(found) != 1 or int(found[0]["n"]) <= 0:
                raise ValueError(
                    "exact ordered value row missing from a serving member"
                )
        version = query(node, "SELECT version() AS version, hostName() AS hostname")
        if len(version) != 1 or version[0]["hostname"] != m["project"] + "-" + node:
            raise ValueError("runtime version evidence came from a foreign member")
        runtime.append(version[0])
    report = {
        "status": "passed",
        "runtime": runtime,
        "candidate_records": len(candidates),
        "ordered_records": len(ordered),
        "exact_delivery_rows": len(deliveries),
        "exact_value_rows": len(values),
        "all_member_count": 3,
        "envelope_parser": "production parse_envelope",
        "candidate_bytes_sha256": digest(b"\n".join(candidates)),
        "ordered_bytes_sha256": digest(b"\n".join(ordered)),
    }
    save_new(directory / "kafka-to-three-member-evidence.json", canonical(report))
    return report
