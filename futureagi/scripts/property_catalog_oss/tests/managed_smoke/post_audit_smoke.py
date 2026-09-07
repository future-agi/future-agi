"""Owned source commit after the final audit and before inventory acknowledgement.

The wrappers are installed explicitly in the disposable supervisor only. They
never change audits, catalog records, clocks, leases, or repair acknowledgements.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from completion_timing import COMPLETION_TIMEOUT_SECONDS, completion_timing
from old_version_smoke import settled_repairs
from run import save
from source_change_smoke import publish, query, rows, target

NAME = "post_audit_import"


def settled_part_baseline(run, runtime):
    """Let ordinary recovery settle pre-pause merges before injecting a fault.

    Stopping merges does not retroactively acknowledge an earlier merge. Never
    repair that inventory/ack ourselves or count broad repair as proof of this
    separate post-audit event.
    """
    from tracer.services.clickhouse.v2.property_catalog.coordinator import (
        FileSupersessionJournal,
    )

    scope = runtime._source_repair_scope()
    if (
        not run.manifest.get("application")
        or not run.owned()
        or scope["state_directory"] != str(run.directory / "application-runtime")
    ):
        raise RuntimeError("part baseline requires owned application control")
    previous = FileSupersessionJournal(scope["state_directory"]).load_record(
        f"source-parts:{scope['organization_id']}:{scope['workspace_id']}:"
        f"{scope['source_database']}"
    )
    started = runtime._source_parts_at_start
    inventory_settled = (
        previous is not None
        and started is not None
        and previous.get("parts") == [list(p) for p in started]
    )
    repairs_settled = settled_repairs(run)[0]
    publish(
        run.directory / "post-audit-baseline.json",
        {
            "inventory_settled": inventory_settled,
            "repairs_settled": repairs_settled,
            "prior_inventory": previous,
            "started_parts": started,
            "no_inventory_or_repair_ack_written": True,
        },
    )
    return inventory_settled and repairs_settled


@contextmanager
def observed_part_watch(run, runtime, report):
    """Record the real detector's inputs/results without adding source reads."""
    from tracer.services.clickhouse.v2.property_catalog.coordinator import (
        FileSupersessionJournal,
    )

    scope = runtime._source_repair_scope()
    if (
        not run.manifest.get("application")
        or not run.owned()
        or scope["state_directory"] != str(run.directory / "application-runtime")
    ):
        raise RuntimeError("part observations require owned application control")
    key = (
        f"source-parts:{scope['organization_id']}:{scope['workspace_id']}:"
        f"{scope['source_database']}"
    )
    observed = report["part_watch"] = {
        "started_parts": runtime._source_parts_at_start,
        "previous_inventory": FileSupersessionJournal(
            scope["state_directory"]
        ).load_record(key),
        "snapshots": [],
        "history_probes": [],
    }
    reader = runtime.span_reader
    original_snapshot = reader.parts_snapshot
    original_history = reader.history_in_parts

    def snapshot():
        result = original_snapshot()
        observed["snapshots"].append(result)
        return result

    def history(**kwargs):
        entry = {
            k: v.isoformat() if isinstance(v, datetime) else v
            for k, v in kwargs.items()
        }
        observed["history_probes"].append(entry)
        try:
            result = original_history(**kwargs)
            entry["result"] = result.isoformat() if result is not None else None
            return result
        except Exception as exc:
            entry["error"] = str(exc)
            raise

    with (
        patch.object(reader, "parts_snapshot", new=snapshot),
        patch.object(reader, "history_in_parts", new=history),
    ):
        yield


@contextmanager
def isolated_merge_pause(run):
    """Isolate this fault from unrelated merges, on the owned source table only.

    A conservative notice from an ordinary merge would also repair the injected
    row with the old broken detector. It cannot prove this particular boundary.
    Merge churn/performance remains a separate release gate. Restore scheduling
    even when STOP has an uncertain result or the primary assertion fails.
    """
    if not run.manifest.get("application") or not run.owned():
        raise RuntimeError("merge isolation requires owned disposable application")
    primary = None
    report = {"table": "default.spans", "disposable_only": True, "restored": False}
    try:
        query(run, "SYSTEM STOP MERGES default.spans")
        deadline = time.monotonic() + 10
        while True:
            active = rows(
                run,
                "SELECT count() AS count FROM system.merges "
                "WHERE database='default' AND table='spans' "
                "SETTINGS max_execution_time=2 FORMAT JSONEachRow",
            )
            if active == [{"count": "0"}]:
                report["inflight_merges_drained"] = True
                break
            if (
                len(active) != 1
                or set(active[0]) != {"count"}
                or not str(active[0]["count"]).isdigit()
                or time.monotonic() >= deadline
            ):
                raise RuntimeError("owned source merges did not drain within 10s")
            time.sleep(0.2)
        yield
    except BaseException as exc:
        primary = exc
        raise
    finally:
        try:
            query(run, "SYSTEM START MERGES default.spans")
            report["restored"] = True
        except BaseException as exc:
            report["restore_error"] = str(exc)
            if primary is None:
                raise
            primary.add_note(
                "Disposable source merge scheduling restore failed: " + str(exc)
            )
        finally:
            save(run.directory / "post-audit-merge-isolation.json", report)


def exact_scope(execution, fixture):
    from tracer.services.clickhouse.v2.property_catalog.durable_lifecycle import (
        LifecycleRunMode,
    )

    prepared = execution.prepared
    return (
        prepared.mode is LifecycleRunMode.INCREMENTAL
        and not prepared.resumed
        and prepared.scope.organization_id == fixture["organization_id"]
        and prepared.scope.workspace_id == fixture["workspace_id"]
        and prepared.scope.project_ids == (fixture["project_id"],)
    )


def inject(run, fixture, execution, audit):
    if (
        not run.manifest.get("application")
        or not run.owned()
        or not exact_scope(execution, fixture)
    ):
        raise RuntimeError(
            "post-audit insertion requires exact owned incremental scope"
        )
    if (
        execution.manifest is None
        or execution.fence is None
        or execution.activation is not None
    ):
        raise RuntimeError(
            "post-audit insertion requires qualified but not activated build"
        )
    token = str(uuid.UUID(execution.lease.build_token))
    if (
        audit["build_token"] != token
        or audit["result"].get("final_span_audit_count") is None
        or not re.fullmatch(
            r"[a-f0-9]{64}", audit["result"].get("final_span_audit_digest", "")
        )
    ):
        raise RuntimeError("post-audit insertion lacks completed real audit evidence")
    window = execution.prepared.cutoffs.span_window
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    event = window.since + (window.until - window.since) / 2
    event_us = (event - epoch) // timedelta(microseconds=1)
    if not window.since <= event < window.until or event_us <= 1:
        raise RuntimeError("post-audit event is not inside the increment")
    identity = uuid.uuid5(uuid.NAMESPACE_URL, run.manifest["run_id"] + NAME)
    project = str(uuid.UUID(fixture["project_id"]))
    predicate = f"project_id=toUUID('{project}') AND trace_id='{identity}' AND id='{identity.hex[:16]}' AND name='{NAME}'"
    if rows(
        run,
        f"SELECT count() AS count FROM default.spans WHERE {predicate} SETTINGS max_execution_time=2 FORMAT JSONEachRow",
    ) != [{"count": "0"}]:
        raise RuntimeError("post-audit synthetic source identity already exists")
    columns = query(
        run,
        "SELECT name FROM system.columns WHERE database='default' AND table='spans' "
        "AND default_kind NOT IN ('ALIAS','MATERIALIZED') ORDER BY position FORMAT TSV",
    ).splitlines()
    required = {
        "id",
        "trace_id",
        "name",
        "start_time",
        "end_time",
        "created_at",
        "updated_at",
        "attrs_string",
        "attrs_number",
        "attrs_bool",
        "attributes_extra",
        "_version",
    }
    if not required <= set(columns) or any(
        re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", c) is None for c in columns
    ):
        raise RuntimeError("post-audit writable source schema not proved")
    replacements = {
        "id": f"'{identity.hex[:16]}'",
        "trace_id": f"'{identity}'",
        "name": f"'{NAME}'",
        "attrs_string": f"map('{NAME}', 'arrived')",
        "attrs_number": "map()",
        "attrs_bool": "map()",
        "attributes_extra": "'{}'",
        "_version": "toUInt64(1)",
        **dict.fromkeys(
            ("start_time", "created_at", "updated_at"),
            f"fromUnixTimestamp64Micro({event_us}, 'UTC')",
        ),
        "end_time": f"fromUnixTimestamp64Micro({event_us + 1000}, 'UTC')",
    }
    projection = ", ".join(replacements.get(c, f"`{c}`") for c in columns)
    quoted = ", ".join(f"`{c}`" for c in columns)
    # Persist an exclusive attempt marker before the only possible INSERT.
    with (run.directory / "post-audit-attempt.json").open("x") as claim:
        json.dump(
            {"build_token": token, "project_id": project, "trace_id": str(identity)},
            claim,
        )
    query(
        run,
        f"INSERT INTO default.spans ({quoted}) SELECT {projection} FROM default.spans "
        f"WHERE {target(run, fixture)} ORDER BY _version DESC LIMIT 1 SETTINGS max_execution_time=3, max_threads=1",
    )
    after = rows(
        run,
        f"SELECT toString(_version) AS version, toUnixTimestamp64Micro(start_time) AS seen_us, "
        f"attrs_string['{NAME}'] AS value, _part AS part_name FROM default.spans WHERE {predicate} "
        "SETTINGS max_execution_time=2 FORMAT JSONEachRow",
    )
    if (
        len(after) != 1
        or after[0]["version"] != "1"
        or int(after[0]["seen_us"]) != event_us
        or after[0]["value"] != "arrived"
        or re.fullmatch(r"[A-Za-z0-9_-]{1,256}", after[0].get("part_name", "")) is None
    ):
        raise RuntimeError("post-audit source commit not independently proved")
    return {
        "build_token": token,
        "revision": execution.lease.catalog_revision,
        "event_us": event_us,
        "source_after": after[0],
        "actual_final_audit": audit,
        "increment_since": window.since.isoformat(),
        "increment_until": window.until.isoformat(),
        "no_notification_sent": True,
        "committed_after_final_audit": True,
    }


def install_fault(run):
    from tracer.services.clickhouse.v2.property_catalog.dev_runtime import (
        CheckedInPropertyCatalogDevRuntime as Runtime,
    )

    if not run.manifest.get("application") or not run.owned():
        raise RuntimeError("post-audit hook requires owned application")
    fixture = json.loads((run.directory / "source-fixture.json").read_text())
    arm = run.directory / "post-audit-arm.json"
    audit_path = run.directory / "post-audit-completed.json"
    original_audit = Runtime.reconcile_non_postgres
    original_notice = Runtime._notice_source_part_changes

    def armed(execution):
        if not arm.exists() or (run.directory / "post-audit-attempt.json").exists():
            return False
        if json.loads(arm.read_text()) != {
            "run_id": run.manifest["run_id"],
            "fixture": fixture,
        }:
            raise RuntimeError("post-audit arm escaped fixture scope")
        return exact_scope(execution, fixture)

    def reconcile(self, *args, **kwargs):
        result = original_audit(self, *args, **kwargs)
        execution = self._require_execution()
        if armed(execution):
            publish(
                audit_path,
                {"build_token": execution.lease.build_token, "result": result},
            )
        return result

    def notice(self, execution):
        if not armed(execution) or not settled_part_baseline(run, self):
            return original_notice(self, execution)
        report = inject(run, fixture, execution, json.loads(audit_path.read_text()))
        try:
            with observed_part_watch(run, self, report):
                result = original_notice(self, execution)
            path = (
                run.directory
                / "application-runtime/source-repairs"
                / (
                    f"repair-{fixture['organization_id']}-{fixture['workspace_id']}.json"
                )
            )
            request = json.loads(path.read_text()) if path.exists() else {}
            report.update(
                repair_notice=request,
                exact_event_notice=(
                    request.get("first_seen_us") == report["event_us"]
                    and request.get("last_seen_us") == report["event_us"]
                    and request.get("observed_at_us", 0) > report["event_us"]
                    and (
                        not path.with_suffix(".json.ack").exists()
                        or path.read_bytes()
                        != path.with_suffix(".json.ack").read_bytes()
                    )
                ),
            )
            return result
        finally:
            publish(run.directory / "post-audit-injected.json", report)

    Runtime.reconcile_non_postgres = reconcile
    Runtime._notice_source_part_changes = notice


def verify(run, fixture, _key, get):
    if not run.manifest.get("application") or not run.owned():
        raise RuntimeError("post-audit verification requires owned application")
    with isolated_merge_pause(run):
        return _verify(run, fixture, get)


def _verify(run, fixture, get):
    deadline = time.monotonic() + 30
    while not settled_repairs(run)[0]:
        if time.monotonic() >= deadline:
            raise RuntimeError("post-audit test cannot isolate earlier pending repairs")
        time.sleep(0.5)
    params = {"source": "traces", "project_ids": fixture["project_id"], "page_size": 50}
    before, _ = get("metrics", {**params, "cursor_mode": "true", "search": NAME})
    if before["metrics"]:
        raise RuntimeError("post-audit property existed before fault injection")
    publish(
        run.directory / "post-audit-arm.json",
        {"run_id": run.manifest["run_id"], "fixture": fixture},
    )
    started = time.monotonic()
    report = {"status": "failed", "observations": []}
    try:
        while time.monotonic() - started < COMPLETION_TIMEOUT_SECONDS:
            history, _ = get(
                "filter_values", {**params, "property_id": "custom_attribute:plan"}
            )
            if [v["value"] for v in history["values"]] != ["Pro"]:
                raise RuntimeError("post-audit repair lost valid catalog history")
            props, _ = get("metrics", {**params, "cursor_mode": "true", "search": NAME})
            report["observations"].append(
                {
                    "seconds": time.monotonic() - started,
                    "revision": props["catalog_revision"],
                    "properties": [m["name"] for m in props["metrics"]],
                }
            )
            path = run.directory / "post-audit-injected.json"
            if path.exists():
                injected = json.loads(path.read_text())
                report["injected"] = injected
                if injected.get("exact_event_notice") is not True:
                    raise RuntimeError(
                        "post-audit commit lacked its exact durable notice before activation"
                    )
                if [m["name"] for m in props["metrics"]] == [NAME]:
                    values, _ = get(
                        "filter_values",
                        {**params, "property_id": "custom_attribute:" + NAME},
                    )
                    if [v["value"] for v in values["values"]] != ["arrived"] or int(
                        values["catalog_revision"]
                    ) <= int(injected["revision"]):
                        raise RuntimeError(
                            "post-audit source value was not repaired into a later qualified catalog"
                        )
                    report.update(
                        status="passed",
                        timing=completion_timing(
                            time.monotonic() - started, latency_target_seconds=60
                        ),
                        seconds_after_arm=time.monotonic() - started,
                        later_revision=values["catalog_revision"],
                        history_preserved=True,
                    )
                    return report
            time.sleep(0.5)
        raise RuntimeError(
            "post-audit commit failed the 180s automatic detection/repair gate"
        )
    finally:
        save(run.directory / "catalog-post-audit.json", report)
