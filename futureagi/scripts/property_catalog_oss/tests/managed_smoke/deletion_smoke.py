"""Canonical tombstone visibility with no fresh candidate to trigger repair.

This does NOT exercise the separate user-facing trace-delete API. The only
write appends a tombstone for one span created by this disposable OTLP test.
No DELETE, TRUNCATE, ALTER, existing-project access or version relabel is used.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime
from pathlib import Path

from run import save


def verify(run, fixture, key, get):
    if not run.manifest.get("application") or not run.owned():
        raise RuntimeError("canonical deletion test requires the owned application")
    identity = uuid.uuid5(uuid.NAMESPACE_URL, run.manifest["run_id"] + "otlp_late")
    project = str(uuid.UUID(fixture["project_id"]))
    predicate = (
        f"project_id=toUUID('{project}') AND trace_id='{identity}' "
        f"AND id='{identity.hex[:16]}' AND name='otlp_late'"
    )

    def query(sql):
        return run.execute("clickhouse", ["clickhouse-client", "--query", sql]).stdout

    def latest():
        return json.loads(
            query(
                "SELECT count() AS versions, argMax(is_deleted, _version) AS deleted, "
                "argMax(attrs_string['otlp_late'], _version) AS value "
                f"FROM default.spans WHERE {predicate} "
                "SETTINGS max_execution_time=2, max_threads=1, output_format_json_quote_64bit_integers=0 FORMAT JSONEachRow"
            )
        )

    before = latest()
    if before["versions"] < 1 or before["deleted"] or before["value"] != "updated":
        raise RuntimeError("exact disposable OTLP update target is not proved")
    # Wait for the last candidate-driven repair to finish before introducing
    # a deletion without an event. Otherwise that earlier event could mask the
    # missing notification. Never manufacture or modify request/ack files.
    receipt_dir = run.directory / "application-spool/candidate-receipts"
    source_notice_dir = run.directory / "application-runtime/source-repairs"
    deadline = time.monotonic() + 20
    while True:
        requests = list(receipt_dir.glob("repair-*.json"))
        if not requests:
            raise RuntimeError("continuous-ingestion repair evidence is missing")
        settled = True
        for path in requests:
            request = json.loads(path.read_text())
            ack_path = Path(str(path) + ".ack")
            ack = json.loads(ack_path.read_text()) if ack_path.exists() else {}
            settled &= (ack.get("generation"), ack.get("candidate_id")) == (
                request["generation"],
                request["candidate_id"],
            )
        # The source probe is a second, independent repair publisher. Pending
        # work from an earlier ingestion stage must not hide a lost deletion
        # notification in this test either. Never manufacture its ack.
        source_notices = list(source_notice_dir.glob("repair-*.json"))
        for path in source_notices:
            ack_path = Path(str(path) + ".ack")
            settled &= ack_path.exists() and ack_path.read_bytes() == path.read_bytes()
        if settled:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "earlier repairs are still pending; deletion isolation not established"
            )
        time.sleep(0.5)
    request_bytes = {str(path): path.read_bytes() for path in requests}
    source_before = {path.name: json.loads(path.read_text()) for path in source_notices}
    columns = query(
        "SELECT name FROM system.columns WHERE database='default' AND table='spans' "
        "AND default_kind NOT IN ('ALIAS','MATERIALIZED') ORDER BY position FORMAT TSV"
    ).splitlines()
    if not {
        "is_deleted",
        "_version",
        "updated_at",
        "id",
        "trace_id",
        "project_id",
    } <= set(columns) or any(
        re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", column) is None for column in columns
    ):
        raise RuntimeError("canonical writable column contract is not verified")
    replacements = {
        "is_deleted": "toUInt8(1)",
        "_version": "toUnixTimestamp64Nano(now64(9, 'UTC'))",
        "updated_at": "now64(6, 'UTC')",
    }
    quoted = ", ".join(f"`{column}`" for column in columns)
    projection = ", ".join(
        replacements.get(column, f"`{column}`") for column in columns
    )
    started = time.monotonic()
    # Exactly one attempt: an uncertain write must not be blindly retried.
    query(
        f"INSERT INTO default.spans ({quoted}) SELECT {projection} FROM default.spans "
        f"WHERE {predicate} ORDER BY _version DESC LIMIT 1 SETTINGS max_execution_time=3, max_threads=1"
    )
    after = latest()
    if after["deleted"] != 1 or after["value"] != "updated":
        raise RuntimeError("canonical tombstone was not durably observed")
    deadline = time.monotonic() + 45
    observations = []
    absent = False
    while time.monotonic() < deadline:
        props, _ = get(
            "metrics",
            {
                "cursor_mode": "true",
                "page_size": 50,
                "search": "otlp_late",
                "source": "traces",
                "project_ids": project,
            },
        )
        # A removed definition is no longer selectable. The value API rejects
        # arbitrary/unknown property IDs; never mistake that admission failure
        # for an empty value page or request a value the picker cannot offer.
        if not props["metrics"]:
            observations.append(
                {
                    "seconds": time.monotonic() - started,
                    "revision": props["catalog_revision"],
                    "properties": [],
                    "values": None,
                    "values_not_requested": "definition removed from picker",
                }
            )
            absent = True
            break
        values, _ = get(
            "filter_values",
            {
                "property_id": "custom_attribute:otlp_late",
                "page_size": 50,
                "source": "traces",
                "project_ids": project,
            },
        )
        observations.append(
            {
                "seconds": time.monotonic() - started,
                "revision": values["catalog_revision"],
                "properties": [item.get("name") for item in props["metrics"]],
                "values": [item["value"] for item in values["values"]],
            }
        )
        time.sleep(1)
    no_event = all(
        Path(path).read_bytes() == raw for path, raw in request_bytes.items()
    )
    report = {
        "status": "passed" if absent and no_event else "failed",
        "source_before": before,
        "source_after": after,
        "canonical_tombstone_only": True,
        "user_delete_api_tested": False,
        "no_new_candidate_repair": no_event,
        "source_repairs_settled_before_tombstone": True,
        "source_notices_before": source_before,
        "source_notices_after": {
            path.name: json.loads(path.read_text())
            for path in source_notice_dir.glob("repair-*.json")
        },
        "observations": observations,
        "seconds_after_tombstone": time.monotonic() - started,
    }
    save(run.directory / "catalog-deletion.json", report)
    if report["status"] != "passed":
        # This deliberately cannot turn the primary failed regression green.
        # Determine whether the normal candidate-driven repair can remove the
        # stale value, without editing leases, clocks, epochs, or repair files.
        diagnose_candidate_repair(run, fixture, key, get)
        raise RuntimeError(
            "canonical deletion not reflected without new candidate events; see catalog-deletion.json"
        )
    return report


def diagnose_candidate_repair(run, fixture, key, get):
    import requests
    from ingestion_smoke import collector_state

    collector_state(run)
    name = "deletion_repair_probe"
    identity = uuid.uuid5(uuid.NAMESPACE_URL, run.manifest["run_id"] + name).hex
    timestamp = int(datetime.fromisoformat(fixture["since"]).timestamp() * 1e9)
    payload = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {
                            "key": "project_name",
                            "value": {"stringValue": "Managed source fixture"},
                        }
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "managed-deletion-diagnostic"},
                        "spans": [
                            {
                                "traceId": identity,
                                "spanId": identity[:16],
                                "name": name,
                                "kind": 1,
                                "startTimeUnixNano": str(timestamp),
                                "endTimeUnixNano": str(timestamp + 1_000_000),
                                "attributes": [
                                    {"key": name, "value": {"stringValue": "trigger"}}
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
    }
    report = {
        "status": "failed",
        "diagnostic_only": True,
        "does_not_satisfy_notification_loss_gate": True,
        "trigger": "one genuine late OTLP span; no manual lifecycle controls",
        "observations": [],
    }
    started = time.monotonic()
    try:
        with requests.Session() as session:
            session.trust_env = False
            # Exactly one attempt, with redirects disabled. An accepted or
            # ambiguous canonical write must never be retried by this probe.
            response = session.post(
                f"http://127.0.0.1:{run.manifest['ports']['otlp']}/v1/traces",
                headers={"X-Api-Key": key.api_key, "X-Secret-Key": key.secret_key},
                json=payload,
                timeout=10,
                allow_redirects=False,
            )
            if response.status_code != 200 or response.json().get(
                "partialSuccess", {}
            ).get("rejectedSpans", "0") not in (0, "0"):
                raise RuntimeError("deletion diagnostic OTLP span was not accepted")
        while time.monotonic() - started < 45:
            scoped = {
                "source": "traces",
                "project_ids": fixture["project_id"],
                "page_size": 50,
            }
            props, _ = get(
                "metrics", {**scoped, "cursor_mode": "true", "search": "otlp_late"}
            )
            probe_props, _ = get(
                "metrics", {**scoped, "cursor_mode": "true", "search": name}
            )
            probe_values = None
            # The previous diagnostic accidentally asked for this new property
            # before discovery offered it, producing definition_missing. Follow
            # the same property-then-values order as the actual picker.
            if any(item.get("name") == name for item in probe_props["metrics"]):
                probe, _ = get(
                    "filter_values",
                    {**scoped, "property_id": "custom_attribute:" + name},
                )
                probe_values = [item["value"] for item in probe["values"]]
            observation = {
                "seconds": time.monotonic() - started,
                "revision": props["catalog_revision"],
                "properties": [item.get("name") for item in props["metrics"]],
                "probe_values": probe_values,
            }
            report["observations"].append(observation)
            if not props["metrics"] and observation["probe_values"] == ["trigger"]:
                preserved, _ = get(
                    "filter_values", {**scoped, "property_id": "custom_attribute:plan"}
                )
                if [item["value"] for item in preserved["values"]] != ["Pro"]:
                    raise RuntimeError("repair lost unrelated historical plan value")
                report.update(status="passed", unrelated_history_preserved=True)
                break
            time.sleep(1)
    except Exception as exc:
        report["error"] = str(exc)[:1000]
    finally:
        report["seconds_after_probe"] = time.monotonic() - started
        save(run.directory / "catalog-deletion-repair-diagnostic.json", report)
    return report
