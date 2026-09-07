"""Deterministic local fault injection, not a product hook or mocked audit.

After the real captured VALUES checkpoint finishes, append one live-only update.
Both unmodified audits must validate the unchanged capture. Observe its initial
API view, then a separately noticed full repair and the updated API view. Only
fixture evidence is written: never catalog/control records or repair ACKs.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from completion_timing import COMPLETION_TIMEOUT_SECONDS, completion_timing
from run import save


def publish(path, value):
    temporary = path.with_suffix(".tmp")
    save(temporary, value)
    temporary.replace(path)


def query(run, sql):
    return run.execute("clickhouse", ["clickhouse-client", "--query", sql]).stdout


def rows(run, sql):
    return [json.loads(line) for line in query(run, sql).splitlines() if line]


def target(run, fixture):
    identity = uuid.uuid5(uuid.NAMESPACE_URL, run.manifest["run_id"] + "otlp_live")
    project = str(uuid.UUID(fixture["project_id"]))
    return (
        f"project_id=toUUID('{project}') AND trace_id='{identity}' "
        f"AND id='{identity.hex[:16]}' AND name='otlp_live'"
    )


def latest(run, fixture):
    return rows(
        run,
        "SELECT count() AS versions, argMax(is_deleted, _version) AS deleted, "
        "argMax(attrs_string['otlp_live'], _version) AS value, "
        "max(_version) AS version, toUnixTimestamp64Micro(max(start_time)) AS seen_us "
        f"FROM default.spans WHERE {target(run, fixture)} "
        "SETTINGS max_execution_time=2, max_threads=1 FORMAT JSONEachRow",
    )[0]


def repair_state(run, fixture):
    """Read the exact real request/ACK with production parsers, without locks/writes."""
    from tracer.services.clickhouse.v2.property_catalog.source_repair import (
        _parse,
        _state,
    )

    identity = {
        "organization_id": fixture["organization_id"],
        "workspace_id": fixture["workspace_id"],
        "source_database": "default",
        "source_table": "spans",
    }
    path = (
        run.directory
        / "application-runtime/source-repairs"
        / (f"repair-{fixture['organization_id']}-{fixture['workspace_id']}.json")
    )
    _, request, ack = _state(path, identity)
    return {"request": request, "ack": _parse(ack, identity) if ack else None}


def capture_description(run, fixture, execution):
    from tracer.services.clickhouse.v2.property_catalog.source_capture import (
        SourceCaptureSpec,
    )

    binding = execution.capture_binding
    if binding is None or type(binding.spec) is not SourceCaptureSpec:
        raise RuntimeError("source-change requires the real immutable capture binding")
    spec = binding.spec
    spec.__post_init__()
    if (
        (spec.organization_id, spec.workspace_id, spec.build_token)
        != (
            fixture["organization_id"],
            fixture["workspace_id"],
            execution.lease.build_token,
        )
        or spec.source_database != "default"
        or spec.catalog_database != "property_catalog_dev_app_" + run.manifest["run_id"]
        or binding.reader._source_table()
        != f"`{spec.capture_database}`.`{spec.capture_table}`"
    ):
        raise RuntimeError("source-change capture escaped the exact source/build")
    return {
        "spec": asdict(spec),
        "binding_sha256": spec.binding_sha256,
        "table_uuid": spec.capture_table_uuid,
        "table": binding.reader._source_table(),
    }


def capture_snapshot(run, fixture, reader, capture):
    # This uses the actual pinned SOURCE reader, not the fixture's default node.
    observed = reader._query(
        "SELECT toString(serverUUID()) AS server_uuid, count() AS versions, "
        "argMax(is_deleted, _version) AS deleted, "
        "argMax(attrs_string['otlp_live'], _version) AS value, "
        "max(_version) AS version, toUnixTimestamp64Micro(max(start_time)) AS seen_us "
        f"FROM {capture['table']} WHERE {target(run, fixture)}",
        {},
    )
    if (
        len(observed) != 1
        or observed[0].get("server_uuid") != capture["spec"]["source_server_uuid"]
        or int(observed[0].get("versions", 0)) < 1
        or observed[0].get("deleted") != 0
    ):
        raise RuntimeError("captured target/node was not independently observed")
    return {
        "target": dict(observed[0]),
        "parts": [list(p) for p in reader.parts_snapshot()],
    }


def capture_retirement(run, fixture, execution, capture):
    """Observe cleanup after real completion; never retire or manufacture proof."""
    from tracer.services.clickhouse.v2.property_catalog.source_capture import (
        DurableSourceCapture,
    )

    if capture != capture_description(run, fixture, execution):
        raise RuntimeError("retirement differs from the exact recorded capture")
    spec = execution.capture_binding.spec
    reader = execution.capture_binding.reader
    client = reader._client
    client._validate_identity()
    if (client.source_database, client.source_table) != (
        spec.capture_database,
        spec.capture_table,
    ):
        raise RuntimeError("retirement probe is not bound to the captured source")
    # Fixture-only metadata SELECT on the actual pinned, server-readonly source
    # transport. Do not broaden the product scanner's table-only query boundary.
    observed, columns, _ = client._driver.execute_read(
        "SELECT toString(serverUUID()) AS server_uuid, count() AS tables "
        "FROM system.tables WHERE database=%(database)s AND name=%(table)s",
        {"database": spec.capture_database, "table": spec.capture_table},
        timeout_ms=reader._deadline.remaining_ms(cap_ms=2000),
        settings={
            "readonly": 2,
            "max_threads": 1,
            "max_execution_time": 2,
            "max_result_rows": 1,
            "max_result_bytes": 4096,
            "result_overflow_mode": "throw",
            "timeout_overflow_mode": "throw",
            "use_query_cache": 0,
        },
    )
    names = tuple(c[0] if isinstance(c, tuple) else c for c in columns)
    if (
        names != ("server_uuid", "tables")
        or len(observed) != 1
        or len(observed[0]) != 2
        or observed[0][0] != spec.source_server_uuid
        or type(observed[0][1]) is not int
        or observed[0][1] != 0
    ):
        raise RuntimeError("retired capture is not absent on its exact source member")
    manager = DurableSourceCapture(
        str(run.directory / "application-runtime"),
        None,
        can_retire=lambda _: False,
    )
    with manager._source_lock(spec):
        state = manager._load(spec)
        if state is None or state["phase"] != "retired":
            raise RuntimeError("capture lacks its exact durable retired journal")
        # Unlike reservation_snapshot(), this includes retired-but-unreleased
        # entries. Other builds in the bounded namespace may remain reserved.
        if any(
            entry.spec.capture_table == spec.capture_table
            for entry in manager._reservations._entries(spec)
        ):
            raise RuntimeError("retired capture still has its index reservation")
    return {
        "source_server_uuid": spec.source_server_uuid,
        "table": capture["table"],
        "table_uuid": spec.capture_table_uuid,
        "table_absent": True,
        "journal_phase": "retired",
        "reservation_released": True,
    }


def checkpoint_proof(checkpoint, execution):
    lease = execution.lease
    if (
        not checkpoint.terminal
        or str(checkpoint.status) != "complete"
        or any(
            getattr(checkpoint, name) != getattr(lease, name)
            for name in (
                "organization_id",
                "workspace_id",
                "catalog_epoch",
                "catalog_revision",
                "build_token",
            )
        )
        or any(
            getattr(checkpoint, name) != 0
            for name in ("gap_count", "poison_count", "conflict_count")
        )
        or not re.fullmatch(r"[a-f0-9]{64}", checkpoint.source_digest)
    ):
        raise RuntimeError("source-change lacks exact complete audit/checkpoint proof")
    return {
        "producer_stream_id": checkpoint.producer_stream_id,
        "source_count": checkpoint.source_count,
        "source_digest": checkpoint.source_digest,
    }


def require_new_notice(state, injected):
    request, ack = state["request"], state["ack"]
    before = injected["repair_before"]["request"]
    if (
        request is None
        or request == ack
        or request["generation"] <= (before["generation"] if before else 0)
        or not request["first_seen_us"]
        <= int(injected["source_after"]["seen_us"])
        <= request["last_seen_us"]
        or request["observed_at_us"] < int(injected["source_after"]["version"]) // 1000
    ):
        raise RuntimeError(
            "live mutation lacks a new unacknowledged source-repair notice"
        )


def install_fault(run):
    """Called explicitly by the disposable supervisor launcher, nowhere else."""
    from tracer.services.clickhouse.v2.property_catalog.dev_runtime import (
        CheckedInPropertyCatalogDevRuntime as Runtime,
    )
    from tracer.services.clickhouse.v2.property_catalog.durable_lifecycle import (
        LifecycleRunMode,
    )
    from tracer.services.clickhouse.v2.property_catalog.span_source import (
        AuthoritativeSpanReconciler,
        AuthoritativeSpanRole,
    )

    if not run.manifest.get("application") or not run.owned():
        raise RuntimeError("source-change injection requires owned local application")
    original = AuthoritativeSpanReconciler._run_stream
    original_authoritative = Runtime._run_authoritative_span
    original_final = Runtime.reconcile_non_postgres
    original_notice = Runtime._notice_source_part_changes
    original_activate = Runtime.activate
    armed = run.directory / "source-change-arm.json"
    claimed = run.directory / "source-change-claimed.json"
    fixture = json.loads((run.directory / "source-fixture.json").read_text())
    contexts = {}

    def evidence(name):
        return run.directory / f"source-change-{name}.json"

    def watching(execution):
        scope = execution.prepared.scope
        return (
            armed.exists()
            and not evidence("repaired").exists()
            and scope.organization_id == fixture["organization_id"]
            and scope.workspace_id == fixture["workspace_id"]
            and scope.project_ids == (fixture["project_id"],)
            and execution.frozen.since
            <= datetime.fromisoformat(fixture["since"])
            < execution.frozen.until
        )

    def arm_request():
        request = json.loads(armed.read_text())
        now = time.monotonic()
        if (
            set(request) != {"run_id", "fixture", "deadline"}
            or request["run_id"] != run.manifest["run_id"]
            or request["fixture"] != fixture
            or type(request["deadline"]) not in (int, float)
            or not now < request["deadline"] <= now + COMPLETION_TIMEOUT_SECONDS
        ):
            raise RuntimeError("source-change arm scope/deadline is invalid")
        return request

    def authoritative(self, execution):
        if not watching(execution):
            return original_authoritative(self, execution)
        token = execution.lease.build_token
        capture = capture_description(run, fixture, execution)
        contexts[token] = (execution, capture)
        try:
            return original_authoritative(self, execution)
        finally:
            contexts.pop(token, None)

    def stream(self, *, frozen, build, role, dry_run, **kwargs):
        checkpoint = original(
            self, frozen=frozen, build=build, role=role, dry_run=dry_run, **kwargs
        )
        if (
            dry_run
            or role is not AuthoritativeSpanRole.VALUES
            or not armed.exists()
            or claimed.exists()
        ):
            return checkpoint
        arm_request()
        history = datetime.fromisoformat(fixture["since"])
        if not (
            frozen.since <= history < frozen.until
            and frozen.project_ids == (fixture["project_id"],)
            and build.workspace_id == fixture["workspace_id"]
            and build.organization_id == fixture["organization_id"]
        ):
            return checkpoint
        context = contexts.get(build.build_token)
        if context is None or self._reader is not context[0].capture_binding.reader:
            raise RuntimeError("VALUES reader is not the exact runtime capture")
        execution, capture = context
        # The file is an exclusive attempt claim, not a product repair signal.
        # An uncertain source INSERT is never retried, even after process loss.
        with claimed.open("x") as output:
            output.write("{}\n")
        before = latest(run, fixture)
        seen = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
            microseconds=int(before["seen_us"])
        )
        if not (
            checkpoint.terminal
            and str(checkpoint.status) == "complete"
            and int(before["versions"]) > 0
            and before["deleted"] == 0
            and before["value"] == "initial"
            and frozen.since <= seen < frozen.until
        ):
            raise RuntimeError("source-change target or completed VALUES not proven")
        checkpoint_proof(checkpoint, execution)
        frozen_before = capture_snapshot(run, fixture, self._reader, capture)
        if frozen_before["target"]["value"] != "initial" or int(
            frozen_before["target"]["version"]
        ) != int(before["version"]):
            raise RuntimeError("captured initial value differs before mutation")
        prior_repair = repair_state(run, fixture)
        database = "property_catalog_dev_app_" + run.manifest["run_id"]
        token = str(uuid.UUID(build.build_token))
        reservation = rows(
            run,
            "SELECT argMax(status, _version) AS status, "
            "argMax(build_lease_sha256, _version) AS lease_sha256 "
            f"FROM `{database}`.property_catalog_source_streams "
            f"WHERE build_token=toUUID('{token}') AND producer_stream_id=toUUID('{token}') "
            "SETTINGS max_execution_time=2, max_threads=1 FORMAT JSONEachRow",
        )[0]
        if reservation["status"] not in {"open", "draining"} or not re.fullmatch(
            r"[a-f0-9]{64}", reservation["lease_sha256"]
        ):
            raise RuntimeError("unfenced source-change reservation not proven")
        columns = query(
            run,
            "SELECT name FROM system.columns WHERE database='default' AND table='spans' "
            "AND default_kind NOT IN ('ALIAS','MATERIALIZED') ORDER BY position FORMAT TSV",
        ).splitlines()
        if not {"attrs_string", "_version", "updated_at"} <= set(columns) or any(
            not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) for name in columns
        ):
            raise RuntimeError("source-change writable schema not verified")
        replacements = {
            "attrs_string": "mapUpdate(attrs_string, map('otlp_live', 'midscan_updated'))",
            "_version": "toUnixTimestamp64Nano(now64(9, 'UTC'))",
            "updated_at": "now64(6, 'UTC')",
        }
        quoted = ", ".join(f"`{name}`" for name in columns)
        projection = ", ".join(replacements.get(name, f"`{name}`") for name in columns)
        query(
            run,
            f"INSERT INTO default.spans ({quoted}) SELECT {projection} FROM default.spans "
            f"WHERE {target(run, fixture)} ORDER BY _version DESC LIMIT 1 "
            "SETTINGS max_execution_time=3, max_threads=1",
        )
        after = latest(run, fixture)
        if after["value"] != "midscan_updated" or int(after["version"]) <= int(
            before["version"]
        ):
            raise RuntimeError(
                "single mid-scan source update not independently observed"
            )
        frozen_after = capture_snapshot(run, fixture, self._reader, capture)
        if frozen_after != frozen_before:
            raise RuntimeError("live mutation changed immutable captured source")
        publish(
            run.directory / "source-change-injected.json",
            {
                "build_token": token,
                "revision": build.catalog_revision,
                "epoch": build.catalog_epoch,
                "reservation": reservation,
                "capture": capture,
                "capture_before": frozen_before,
                "capture_after": frozen_after,
                "repair_before": prior_repair,
                "source_before": before,
                "source_after": after,
                "since": frozen.since.isoformat(),
                "until": frozen.until.isoformat(),
                "values_source_count": checkpoint.source_count,
                "values_source_digest": checkpoint.source_digest,
                "real_terminal_values_checkpoint": True,
                "original_independent_audit_next": True,
                "no_notification_for_mutation": True,
            },
        )
        return checkpoint

    def final(self, *args, **kwargs):
        result = original_final(self, *args, **kwargs)
        execution = self._require_execution()
        if not watching(execution) or not evidence("injected").exists():
            return result
        injected = json.loads(evidence("injected").read_text())
        initial = execution.lease.build_token == injected["build_token"]
        capture = capture_description(run, fixture, execution)
        values = checkpoint_proof(execution.authoritative.values, execution)
        audit = checkpoint_proof(execution.authoritative.source_audit, execution)
        proof = (values["source_count"], values["source_digest"])
        if (
            values["producer_stream_id"] == audit["producer_stream_id"]
            or proof != (audit["source_count"], audit["source_digest"])
            or proof
            != (result["final_span_audit_count"], result["final_span_audit_digest"])
        ):
            raise RuntimeError("real VALUES/SOURCE_AUDIT/final audit proofs disagree")
        snapshot = capture_snapshot(
            run, fixture, execution.capture_binding.reader, capture
        )
        claimed_repair = None
        if initial:
            if (
                capture != injected["capture"]
                or snapshot != injected["capture_before"]
                or proof
                != (injected["values_source_count"], injected["values_source_digest"])
            ):
                raise RuntimeError("initial captured build or audit changed")
        else:
            stable = json.loads(evidence("initial-activated").read_text())
            claimed = self._source_repair
            claimed_repair = json.loads(claimed.raw) if claimed is not None else None
            if (
                execution.prepared.mode is not LifecycleRunMode.FULL_REPAIR
                or execution.prepared.resumed
                or claimed_repair != stable["repair"]["request"]
                or execution.lease.catalog_revision <= injected["revision"]
                or capture["table_uuid"] == injected["capture"]["table_uuid"]
                or snapshot["target"]["value"] != "midscan_updated"
                or int(snapshot["target"]["version"])
                != int(injected["source_after"]["version"])
            ):
                raise RuntimeError(
                    "later full repair does not bind the exact new notice/capture"
                )
        publish(
            evidence("initial-audit" if initial else "repair-audit"),
            {
                "build_token": execution.lease.build_token,
                "revision": execution.lease.catalog_revision,
                "epoch": execution.lease.catalog_epoch,
                "capture": capture,
                "captured": snapshot,
                "values": values,
                "source_audit": audit,
                "final_audit": {
                    "source_count": result["final_span_audit_count"],
                    "source_digest": result["final_span_audit_digest"],
                },
                "claimed_repair": claimed_repair,
            },
        )
        return result

    def notice(self, execution):
        result = original_notice(self, execution)
        if watching(execution) and evidence("injected").exists():
            injected = json.loads(evidence("injected").read_text())
            if execution.lease.build_token == injected["build_token"]:
                state = repair_state(run, fixture)
                require_new_notice(state, injected)
                publish(
                    evidence("notice"),
                    {"build_token": execution.lease.build_token, "repair": state},
                )
        return result

    def activate(self, *args, **kwargs):
        execution = self._require_execution()
        observed = watching(execution) and evidence("injected").exists()
        result = original_activate(self, *args, **kwargs)
        if not observed:
            return result
        injected = json.loads(evidence("injected").read_text())
        initial = execution.lease.build_token == injected["build_token"]
        audited = json.loads(
            evidence("initial-audit" if initial else "repair-audit").read_text()
        )
        record = execution.activation.record
        if (
            result.get("activated") is not True
            or record.build_token != audited["build_token"]
            or record.catalog_revision != audited["revision"]
            or record.catalog_epoch != audited["epoch"]
            or not re.fullmatch(r"[a-f0-9]{64}", record.activation_sha256)
        ):
            raise RuntimeError("source-change lacks exact real activated build")
        retired = capture_retirement(run, fixture, execution, audited["capture"])
        state = repair_state(run, fixture)
        if initial:
            noticed = json.loads(evidence("notice").read_text())
            require_new_notice(state, injected)
            if (
                noticed["build_token"] != record.build_token
                or state["request"] != noticed["repair"]["request"]
            ):
                raise RuntimeError("initial activation lost the newly observed repair")
        elif state["ack"] != audited["claimed_repair"]:
            raise RuntimeError(
                "replacement did not acknowledge its exact captured repair"
            )
        completed = {
            **audited,
            "activation_sha256": record.activation_sha256,
            "capture_retirement": retired,
            "repair": state,
        }
        publish(evidence("initial-activated" if initial else "repaired"), completed)
        if initial:
            # Fixture observation rendezvous only, AFTER real activation and
            # native completion released their locks. Never a product repair ACK.
            deadline = arm_request()["deadline"]
            while time.monotonic() < deadline:
                if evidence("initial-api").exists():
                    observed_api = json.loads(evidence("initial-api").read_text())
                    if observed_api != {
                        "build_token": record.build_token,
                        "revision": record.catalog_revision,
                        "epoch": record.catalog_epoch,
                        "value": "initial",
                    }:
                        raise RuntimeError(
                            "initial API observation differs from captured activation"
                        )
                    break
                time.sleep(0.05)
            else:
                raise RuntimeError(
                    "stable captured API view was not observed within 180s"
                )
        return result

    Runtime._run_authoritative_span = authoritative
    AuthoritativeSpanReconciler._run_stream = stream
    Runtime.reconcile_non_postgres = final
    Runtime._notice_source_part_changes = notice
    Runtime.activate = activate


def trigger_repair(run, fixture, key):
    """One real OTLP late observation; do not author a repair/control record."""
    import requests
    from ingestion_smoke import collector_state

    collector_state(run)
    name = "midscan_repair_probe"
    identity = uuid.uuid5(uuid.NAMESPACE_URL, run.manifest["run_id"] + name).hex
    event_time = int(datetime.fromisoformat(fixture["since"]).timestamp() * 1e9)
    span = {
        "traceId": identity,
        "spanId": identity[:16],
        "name": name,
        "kind": 1,
        "startTimeUnixNano": str(event_time),
        "endTimeUnixNano": str(event_time + 1_000_000),
        "attributes": [{"key": name, "value": {"stringValue": "trigger"}}],
    }
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
                "scopeSpans": [{"scope": {"name": "managed-midscan"}, "spans": [span]}],
            }
        ]
    }
    with requests.Session() as session:
        session.trust_env = False
        response = session.post(
            f"http://127.0.0.1:{run.manifest['ports']['otlp']}/v1/traces",
            json=payload,
            headers={"X-Api-Key": key.api_key, "X-Secret-Key": key.secret_key},
            timeout=10,
            allow_redirects=False,
        )
        if response.status_code != 200 or response.json().get("partialSuccess"):
            raise RuntimeError("source-change repair trigger was not fully accepted")


def verify(run, fixture, key, get):
    if not run.manifest.get("application") or not run.owned():
        raise RuntimeError("source-change verification requires owned application")
    params = {"source": "traces", "project_ids": fixture["project_id"], "page_size": 50}

    def values(name):
        result, _ = get(
            "filter_values", {**params, "property_id": "custom_attribute:" + name}
        )
        return result, [item["value"] for item in result["values"]]

    before, observed = values("otlp_live")
    if observed != ["initial"]:
        raise RuntimeError("source-change API precondition not proved")
    started = time.monotonic()
    deadline = started + COMPLETION_TIMEOUT_SECONDS
    observations = []
    report = {"status": "failed", "fault_injection": True, "observations": observations}
    initial_api = None

    def evidence(name):
        path = run.directory / f"source-change-{name}.json"
        return json.loads(path.read_text()) if path.exists() else None

    def audited(record):
        proof = {
            name: record["values"][name] for name in ("source_count", "source_digest")
        }
        if (
            not re.fullmatch(r"[a-f0-9]{64}", record["activation_sha256"])
            or not re.fullmatch(r"[a-f0-9]{64}", proof["source_digest"])
            or record["values"]["producer_stream_id"]
            == record["source_audit"]["producer_stream_id"]
            or proof != {name: record["source_audit"][name] for name in proof}
            or proof != record["final_audit"]
            or record["capture"]["spec"]["build_token"] != record["build_token"]
        ):
            raise RuntimeError("activation lacks matching real capture/audit evidence")

    try:
        publish(
            run.directory / "source-change-arm.json",
            {
                "run_id": run.manifest["run_id"],
                "fixture": fixture,
                "deadline": deadline,
            },
        )
        # Setup a full build using the actual collector. The subsequent direct
        # source mutation emits NO notification or manufactured repair record.
        trigger_repair(run, fixture, key)
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
            if history != ["Pro"] or found not in (["initial"], ["midscan_updated"]):
                raise RuntimeError(
                    "valid catalog/history unavailable during source-change recovery"
                )
            injected, stable = evidence("injected"), evidence("initial-activated")
            if found == ["midscan_updated"] and initial_api is None:
                raise RuntimeError("updated view preceded stable captured API proof")
            if injected is not None and stable is not None and initial_api is None:
                audited(stable)
                require_new_notice(stable["repair"], injected)
                if (
                    stable["build_token"] != injected["build_token"]
                    or stable["revision"] != injected["revision"]
                    or stable["epoch"] != injected["epoch"]
                    or stable["capture"] != injected["capture"]
                    or stable["captured"] != injected["capture_before"]
                    or injected["capture_before"] != injected["capture_after"]
                    or stable["captured"]["target"]["value"] != "initial"
                    or injected["no_notification_for_mutation"] is not True
                    or stable["values"]["source_count"]
                    != injected["values_source_count"]
                    or stable["values"]["source_digest"]
                    != injected["values_source_digest"]
                ):
                    raise RuntimeError(
                        "initial activation lacks unchanged captured source proof"
                    )
                if (result["catalog_revision"], result["catalog_epoch"]) == (
                    stable["revision"],
                    stable["epoch"],
                ):
                    initial_api = {
                        name: stable[name]
                        for name in ("build_token", "revision", "epoch")
                    }
                    initial_api["value"] = "initial"
                    publish(
                        run.directory / "source-change-initial-api.json", initial_api
                    )
            repaired = evidence("repaired")
            if found != ["midscan_updated"] or repaired is None:
                time.sleep(0.5)
                continue
            audited(repaired)
            if (
                repaired["build_token"] == stable["build_token"]
                or repaired["revision"] <= stable["revision"]
                or repaired["epoch"] != stable["epoch"]
                or repaired["capture"]["table_uuid"] == stable["capture"]["table_uuid"]
                or repaired["claimed_repair"] != stable["repair"]["request"]
                or repaired["repair"]["ack"] != repaired["claimed_repair"]
                or repaired["captured"]["target"]["value"] != "midscan_updated"
                or int(repaired["captured"]["target"]["version"])
                != int(injected["source_after"]["version"])
                or result["catalog_revision"] < repaired["revision"]
                or result["catalog_epoch"] != repaired["epoch"]
            ):
                raise RuntimeError(
                    "fresh values lack exact later repair/capture/ACK proof"
                )
            report.update(
                status="passed",
                timing=completion_timing(
                    time.monotonic() - started, latency_target_seconds=75
                ),
                injected=injected,
                stable_capture_activation=stable,
                stable_capture_api=initial_api,
                repair_activation=repaired,
                replacement_revision=result["catalog_revision"],
                no_notification_for_mutation=True,
                real_independent_audits_preserved=True,
                previous_catalog_remained_available=True,
                unrelated_history_preserved=True,
                no_manual_lease_or_identity_changes=True,
                initial_revision=before["catalog_revision"],
                seconds_after_trigger=time.monotonic() - started,
            )
            return report
        raise RuntimeError(
            "captured view and later source-change repair did not finish within 180s"
        )
    finally:
        save(run.directory / "catalog-source-change.json", report)
