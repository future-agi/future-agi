"""Historical source import with neither a fresh version nor notification.

This appends one new version of an exclusively owned synthetic span. Its version
is logically newer but deliberately predates the active event cutoff, as a
delayed commit/backfill can. No source deletion or catalog/control write occurs.
"""

from __future__ import annotations

import json
import re
import sys
import time
import uuid
from pathlib import Path

from run import save
from source_change_smoke import latest, query, rows, target


def settled_repairs(run):
    """Observe existing signals; never create or acknowledge one for the test."""
    pending = False
    observed = {}
    for directory in (
        run.directory / "application-spool/candidate-receipts",
        run.directory / "application-runtime/source-repairs",
    ):
        for path in directory.glob("repair-*.json"):
            raw = path.read_bytes()
            ack_path = Path(str(path) + ".ack")
            request = json.loads(raw)
            ack = json.loads(ack_path.read_bytes()) if ack_path.exists() else {}
            if "candidate_id" in request:
                pending |= (request["generation"], request["candidate_id"]) != (
                    ack.get("generation"),
                    ack.get("candidate_id"),
                )
            else:
                pending |= ack != request
            observed[str(path)] = raw
    if not observed:
        raise RuntimeError("earlier source/candidate repair evidence missing")
    return not pending, observed


def verify(run, fixture, _key, get):
    if not run.manifest.get("application") or not run.owned():
        raise RuntimeError("old-version import requires the owned application")
    params = {"source": "traces", "project_ids": fixture["project_id"], "page_size": 50}

    def values(name):
        result, _ = get(
            "filter_values", {**params, "property_id": "custom_attribute:" + name}
        )
        return result, [item["value"] for item in result["values"]]

    database = "property_catalog_dev_app_" + run.manifest["run_id"]
    org = str(uuid.UUID(fixture["organization_id"]))
    workspace = str(uuid.UUID(fixture["workspace_id"]))
    before = latest(run, fixture)
    if before["deleted"] != 0 or before["value"] != "midscan_updated":
        raise RuntimeError("old-version import source precondition is not proved")
    deadline = time.monotonic() + 30
    while True:
        result, found = values("otlp_live")
        if found != ["midscan_updated"]:
            raise RuntimeError("old-version import API precondition is not proved")
        settled, request_bytes = settled_repairs(run)
        revision = int(result["catalog_revision"])
        plans = rows(
            run,
            "SELECT argMax(build_plan_json, _version) AS plan "
            f"FROM `{database}`.property_catalog_source_streams WHERE catalog_revision={revision} "
            f"AND organization_id=toUUID('{org}') AND workspace_id=toUUID('{workspace}') "
            f"AND catalog_epoch={int(result['catalog_epoch'])} "
            "AND producer_stream_id=build_token SETTINGS max_execution_time=2 FORMAT JSONEachRow",
        )
        if len(plans) != 1:
            raise RuntimeError("old-version import selected plan is ambiguous")
        plan = json.loads(plans[0]["plan"])
        cutoff = plan["source_scope"]["span_until_us"]
        if settled and int(before["version"]) + 1 < cutoff * 1000:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError("old-version import isolation/cutoff not established")
        time.sleep(0.5)

    columns = query(
        run,
        "SELECT name FROM system.columns WHERE database='default' AND table='spans' "
        "AND default_kind NOT IN ('ALIAS','MATERIALIZED') ORDER BY position FORMAT TSV",
    ).splitlines()
    if not {"attrs_string", "_version"} <= set(columns) or any(
        not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", column) for column in columns
    ):
        raise RuntimeError("old-version import writable schema is not proved")
    replacements = {
        "attrs_string": "mapUpdate(attrs_string, map('otlp_live', 'historical_import'))",
        "_version": f"toUInt64({int(before['version']) + 1})",
    }
    quoted = ", ".join(f"`{name}`" for name in columns)
    projection = ", ".join(replacements.get(name, f"`{name}`") for name in columns)
    started = time.monotonic()
    # A process retry must not repeat a possibly accepted source INSERT.
    with (run.directory / "old-version-import-attempt.json").open("x") as claim:
        json.dump(
            {"run_id": run.manifest["run_id"], "target": target(run, fixture)}, claim
        )
    # One source INSERT, no retry. Preserve original event/created/updated times.
    query(
        run,
        f"INSERT INTO default.spans ({quoted}) SELECT {projection} FROM default.spans "
        f"WHERE {target(run, fixture)} ORDER BY _version DESC LIMIT 1 "
        "SETTINGS max_execution_time=3, max_threads=1",
    )
    after = latest(run, fixture)
    if (
        after["value"] != "historical_import"
        or int(after["version"]) != int(before["version"]) + 1
        or after["seen_us"] != before["seen_us"]
    ):
        raise RuntimeError("old-version source append not independently observed")
    observations = []
    report = {
        "status": "failed",
        "source_before": before,
        "source_after": after,
        "prior_event_cutoff_us": cutoff,
        "version_predates_cutoff": True,
        "no_notification_sent": True,
        "observations": observations,
    }
    try:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            result, found = values("otlp_live")
            _, history = values("plan")
            observations.append(
                {
                    "seconds": time.monotonic() - started,
                    "revision": result["catalog_revision"],
                    "values": found,
                }
            )
            if history != ["Pro"] or found not in (
                ["midscan_updated"],
                ["historical_import"],
            ):
                raise RuntimeError("old-version import lost valid catalog/history")
            if found == ["historical_import"]:
                report.update(
                    status="passed", seconds_after_import=time.monotonic() - started
                )
                return report
            time.sleep(1)
        raise RuntimeError(
            "unnotified old-version import missed the 45s freshness gate"
        )
    finally:
        primary_failure = sys.exc_info()[0] is not None
        try:
            _, current_requests = settled_repairs(run)
            report["repair_requests_changed"] = [
                path
                for path, raw in current_requests.items()
                if request_bytes.get(path) != raw
            ]
        except Exception as exc:
            report.update(status="failed", repair_observation_error=str(exc))
            if not primary_failure:
                raise
        finally:
            save(run.directory / "catalog-old-version-import.json", report)
