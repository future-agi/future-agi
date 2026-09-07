"""Authenticated OTLP through the real collector, scoped to owned test services."""

from __future__ import annotations

import hashlib
import json
import shutil
import time
import uuid
from datetime import datetime

from run import HERE, LABEL, ROOT, Run, save

_CONTINUOUS_BATCH_COUNT = 24
_CONTINUOUS_INTERVAL_SECONDS = 2
_CONTINUOUS_TIMEOUT_SECONDS = 100
_CONTINUOUS_VISIBILITY_SECONDS = 45
# Test-process envelope: readiness, two POST/canonical/visibility phases,
# sustained traffic, and bounded Python/API orchestration. Individual gates
# below remain unchanged; this is not a product freshness or query timeout.
INGESTION_PROCESS_TIMEOUT_SECONDS = (
    20 + 2 * (10 + 15 + 45) + _CONTINUOUS_TIMEOUT_SECONDS + 40
)


def build_collector(run: Run) -> None:
    architecture = run.command(
        ["docker", "info", "--format", "{{.Architecture}}"]
    ).stdout.strip()
    target = {
        "aarch64": "arm64",
        "arm64": "arm64",
        "x86_64": "amd64",
        "amd64": "amd64",
    }.get(architecture)
    if target is None:
        raise RuntimeError("unsupported disposable collector architecture")
    binary = run.directory / "fi-collector-linux"
    if binary.exists():
        raise RuntimeError("refusing to replace an existing collector binary")
    previous = run.env
    try:
        run.env = {**previous, "GOOS": "linux", "GOARCH": target, "CGO_ENABLED": "0"}
        run.command(
            ["go", "build", "-trimpath", "-o", str(binary), "./cmd/fi-collector"],
            cwd=ROOT / "fi-collector",
            timeout=240,
        )
    finally:
        run.env = previous
    context = run.directory / "collector-image"
    context.mkdir(mode=0o700)
    shutil.copyfile(binary, context / "fi-collector")
    (context / "fi-collector").chmod(0o755)
    shutil.copyfile(
        ROOT / "fi-collector/config/collector.yaml", context / "collector.yaml"
    )
    run.command(
        [
            "docker",
            "build",
            "--label",
            LABEL + "=" + run.manifest["run_id"],
            "--tag",
            run.manifest["project"] + "-collector:local",
            "--file",
            str(HERE / "collector.Dockerfile"),
            str(context),
        ],
        timeout=180,
    )
    save(
        run.directory / "collector-build.json",
        {
            "status": "passed",
            "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            "platform": "linux/" + target,
            "actual_command": "./cmd/fi-collector",
            "release_image_build": False,
            "local_test_image": run.manifest["project"] + "-collector:local",
        },
    )


def start_collector(run: Run) -> None:
    if not run.manifest.get("application") or not run.owned():
        raise RuntimeError("collector requires the new application fixture")
    password = run.manifest["password"]
    if not password or any(c not in "0123456789abcdef" for c in password):
        raise RuntimeError("collector credential must be this fixture's generated hex")
    # No source SELECT, schema, or catalog write permission in this identity.
    run.execute(
        "clickhouse",
        ["clickhouse-client", "--multiquery"],
        stdin=f"CREATE USER managed_smoke_ingest IDENTIFIED BY '{password}';\n"
        "GRANT INSERT ON default.spans TO managed_smoke_ingest;\n",
    )
    # Do not share the supervisor's intentionally two-connection PG identity
    # with a separately pooled application service. Neither role gets broader
    # permissions or connection limits as a side effect of this probe.
    run.execute(
        "postgres",
        ["psql", "-U", "smoke_admin", "-d", "managed_smoke", "-v", "ON_ERROR_STOP=1"],
        stdin="CREATE ROLE managed_smoke_collector LOGIN NOSUPERUSER NOCREATEDB "
        "NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 5 "
        f"PASSWORD '{password}';\n"
        "GRANT pg_read_all_data TO managed_smoke_collector;\n"
        "ALTER ROLE managed_smoke_collector SET default_transaction_read_only='on';\n"
        "ALTER ROLE managed_smoke_collector SET statement_timeout='3000ms';\n",
    )
    run.command(
        run.compose + ["up", "-d", "--wait", "--wait-timeout", "45", "redis"],
        timeout=90,
    )
    run.command(run.compose + ["up", "-d", "collector"], timeout=90)
    state = collector_state(run)
    save(
        run.directory / "collector-runtime.json",
        {
            "container_id": state["Id"],
            "image_id": state["Image"],
            "user": state["Config"]["User"],
            "published_ports": state["NetworkSettings"]["Ports"],
            "readonly_root": state["HostConfig"]["ReadonlyRootfs"],
            "redis_enabled": True,
            "canonical_writer_insert_only": True,
            "existing_project_auth_uses_readonly_pg": True,
        },
    )


def collector_state(run: Run) -> dict:
    containers = json.loads(run.command(["docker", "inspect", *run.owned()]).stdout)
    matches = [
        c
        for c in containers
        if c["Config"]["Labels"].get("com.docker.compose.service") == "collector"
    ]
    if len(matches) != 1:
        raise RuntimeError("collector ownership is ambiguous")
    state = matches[0]
    expected = [{"HostIp": "127.0.0.1", "HostPort": str(run.manifest["ports"]["otlp"])}]
    ports = state["NetworkSettings"]["Ports"]
    if ports.get("4318/tcp") != expected or any(
        publication for port, publication in ports.items() if port != "4318/tcp"
    ):
        raise RuntimeError("collector publication escaped isolated OTLP endpoint")
    if not state["State"]["Running"] or state["RestartCount"]:
        raise RuntimeError("collector exited or restarted; see owned Compose logs")
    return state


def verify(run: Run, fixture: dict, key, get) -> dict:
    """Called after real Django auth setup; no fake writer/candidate API is used."""
    import requests

    collector_state(run)
    url = f"http://127.0.0.1:{run.manifest['ports']['otlp']}/v1/traces"
    session = requests.Session()
    session.trust_env = False
    headers = {"X-Api-Key": key.api_key, "X-Secret-Key": key.secret_key}
    current = time.time_ns()
    late = int(datetime.fromisoformat(fixture["since"]).timestamp() * 1_000_000_000)

    def span(label: str, timestamp: int, value: str) -> dict:
        identity = uuid.uuid5(uuid.NAMESPACE_URL, run.manifest["run_id"] + label).hex
        return {
            "traceId": identity,
            "spanId": identity[:16],
            "name": label,
            "kind": 1,
            "startTimeUnixNano": str(timestamp),
            "endTimeUnixNano": str(timestamp + 1_000_000),
            "attributes": [
                {"key": label, "value": {"stringValue": value}},
                {"key": "otlp_number", "value": {"intValue": "123"}},
                {"key": "otlp_boolean", "value": {"boolValue": False}},
                {
                    "key": "otlp_array",
                    "value": {
                        "arrayValue": {
                            "values": [
                                {"stringValue": "one"},
                                {"intValue": "2"},
                                {"boolValue": True},
                            ]
                        }
                    },
                },
            ],
        }

    def payload(spans: list[dict]) -> dict:
        return {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {
                                "key": "project_name",
                                "value": {"stringValue": "Managed source fixture"},
                            },
                            {
                                "key": "service.name",
                                "value": {"stringValue": "managed-smoke-otlp"},
                            },
                        ]
                    },
                    "scopeSpans": [
                        {"scope": {"name": "managed-smoke"}, "spans": spans}
                    ],
                }
            ]
        }

    spans = [span("otlp_live", current, "initial"), span("otlp_late", late, "initial")]
    try:
        # Readiness uses only an unauthenticated request, never a retry of a
        # potentially accepted ingestion write.
        deadline = time.monotonic() + 20
        while True:
            try:
                rejected = session.post(
                    url, json=payload(spans), timeout=3, allow_redirects=False
                )
                break
            except requests.ConnectionError:
                collector_state(run)
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.25)
        if rejected.status_code != 401:
            raise RuntimeError(
                f"OTLP missing-credential request returned {rejected.status_code}"
            )

        def canonical() -> list[dict]:
            response = run.execute(
                "clickhouse",
                [
                    "clickhouse-client",
                    "--query",
                    "SELECT name, toString(project_id) AS project, toString(org_id) AS organization, "
                    "argMax(attrs_string, _version) AS strings, argMax(attrs_number, _version) AS numbers, "
                    "argMax(attrs_bool, _version) AS booleans, argMax(attributes_extra, _version) AS extra "
                    "FROM default.spans WHERE name IN ('otlp_live', 'otlp_late') "
                    "GROUP BY name, project, organization ORDER BY name "
                    "SETTINGS max_execution_time=2, max_threads=1, max_result_rows=4 FORMAT JSONEachRow",
                ],
            )
            return [json.loads(line) for line in response.stdout.splitlines() if line]

        if canonical():
            raise RuntimeError("unauthenticated spans reached canonical storage")

        def send_and_check(
            items: list[dict], expected: dict[str, str], stage: str
        ) -> dict:
            submitted = time.monotonic()
            response = session.post(
                url,
                json=payload(items),
                headers=headers,
                timeout=10,
                allow_redirects=False,
            )
            if response.status_code != 200 or response.json().get(
                "partialSuccess", {}
            ).get("rejectedSpans", "0") not in (0, "0"):
                raise RuntimeError(
                    f"OTLP ingestion was not accepted: {response.status_code} {response.text[:1000]}"
                )
            deadline = time.monotonic() + 15
            while True:
                rows = canonical()
                if len(rows) == 2 and all(
                    row["strings"].get(row["name"]) == expected[row["name"]]
                    for row in rows
                ):
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "accepted OTLP spans did not reach canonical storage"
                    )
                collector_state(run)
                time.sleep(0.5)
            for row in rows:
                if (
                    row["project"] != fixture["project_id"]
                    or row["organization"] != fixture["organization_id"]
                    or row["numbers"].get("otlp_number") != 123
                    or row["booleans"].get("otlp_boolean") != 0
                    or json.loads(row["extra"]).get("otlp_array") != ["one", 2, True]
                ):
                    raise RuntimeError(f"OTLP tenant/type stamping mismatch: {row}")
            committed = time.monotonic()
            observed, last = {}, {}
            deadline = time.monotonic() + 45
            checks = {
                **{name: [value] for name, value in expected.items()},
                "otlp_number": [123],
                "otlp_boolean": [False],
                "otlp_array": ["one", 2, True],
            }
            while time.monotonic() < deadline and len(observed) < len(checks):
                for name in checks.keys() - observed.keys():
                    props, _ = get(
                        "metrics",
                        {
                            "cursor_mode": "true",
                            "page_size": 50,
                            "search": name,
                            "source": "traces",
                            "project_ids": fixture["project_id"],
                        },
                    )
                    if not any(item.get("name") == name for item in props["metrics"]):
                        continue
                    values, _ = get(
                        "filter_values",
                        {
                            "property_id": "custom_attribute:" + name,
                            "page_size": 50,
                            "source": "traces",
                            "project_ids": fixture["project_id"],
                        },
                    )
                    found = [item["value"] for item in values["values"]]
                    last[name] = found
                    if (
                        sorted(map(json.dumps, found))
                        == sorted(map(json.dumps, checks[name]))
                        and not values["has_more"]
                    ):
                        observed[name] = {
                            "seconds_after_submission": time.monotonic() - submitted,
                            "revision": values["catalog_revision"],
                            "values": found,
                        }
                if len(observed) < len(checks):
                    time.sleep(1)
            report = {
                "status": "passed" if len(observed) == len(checks) else "failed",
                "stage": stage,
                "observed": observed,
                "last_values": last,
                "canonical_commit_seconds": committed - submitted,
                "canonical_rows": rows,
            }
            save(run.directory / f"collector-{stage}.json", report)
            if report["status"] != "passed":
                raise RuntimeError(
                    f"OTLP catalog convergence failed; see collector-{stage}.json"
                )
            return report

        initial = send_and_check(
            spans, {"otlp_live": "initial", "otlp_late": "initial"}, "initial"
        )
        updated = send_and_check(
            [span("otlp_late", late, "updated")],
            {"otlp_live": "initial", "otlp_late": "updated"},
            "update",
        )
        continuous = verify_continuous(
            run, fixture, get, session, url, headers, span, payload, late
        )
        collector_state(run)
        report = {
            "status": "passed",
            "unauthenticated_request_rejected": True,
            "actual_collector_http": True,
            "no_manual_source_or_candidate_writes": True,
            "initial": initial,
            "updated": updated,
            "continuous": continuous,
        }
        save(run.directory / "collector-ingestion.json", report)
        return report
    finally:
        session.close()


def verify_continuous(run, fixture, get, session, url, headers, span, payload, late):
    """Qualify sustained progress and final catch-up, not per-batch latency."""
    batch_count = _CONTINUOUS_BATCH_COUNT
    started = time.monotonic()
    deadline = started + _CONTINUOUS_TIMEOUT_SECONDS
    submitted, observations = [], []
    seen = {"live": set(), "late": set()}
    first_visible, first_during_traffic = {}, {}
    converged_seconds = None
    expected = {str(i) for i in range(batch_count)}
    while time.monotonic() < deadline:
        if (
            len(submitted) < batch_count
            and time.monotonic()
            >= started + len(submitted) * _CONTINUOUS_INTERVAL_SECONDS
        ):
            index = len(submitted)
            items = []
            for family, timestamp in (
                ("live", time.time_ns()),
                ("late", late + index * 1_000_000),
            ):
                item = span(f"otlp_stream_{family}_{index}", timestamp, str(index))
                item["attributes"][0]["key"] = "otlp_stream_" + family
                items.append(item)
            response = session.post(
                url,
                json=payload(items),
                headers=headers,
                timeout=10,
                allow_redirects=False,
            )
            if response.status_code != 200 or response.json().get(
                "partialSuccess", {}
            ).get("rejectedSpans", "0") not in (0, "0"):
                raise RuntimeError(
                    f"continuous OTLP batch {index} was not accepted: {response.status_code}"
                )
            submitted.append({"batch": index, "seconds": time.monotonic() - started})
        for family in seen:
            name = "otlp_stream_" + family
            props, _ = get(
                "metrics",
                {
                    "cursor_mode": "true",
                    "page_size": 50,
                    "search": name,
                    "source": "traces",
                    "project_ids": fixture["project_id"],
                },
            )
            if not any(item.get("name") == name for item in props["metrics"]):
                if seen[family]:
                    raise RuntimeError(
                        f"continuous {family} property disappeared across activations"
                    )
                continue
            result, _ = get(
                "filter_values",
                {
                    "property_id": "custom_attribute:" + name,
                    "page_size": 50,
                    "source": "traces",
                    "project_ids": fixture["project_id"],
                },
            )
            found = {v["value"] for v in result["values"]}
            accepted = {str(i) for i in range(len(submitted))}
            if not found <= accepted or not seen[family] <= found or result["has_more"]:
                raise RuntimeError(
                    f"continuous {family} values leaked or disappeared across activations: {seen[family]} -> {found}"
                )
            observed_seconds = time.monotonic() - started
            if found:
                first_visible.setdefault(family, observed_seconds)
                if len(submitted) < batch_count:
                    first_during_traffic.setdefault(family, observed_seconds)
            if found != seen[family]:
                observations.append(
                    {
                        "family": family,
                        "seconds": observed_seconds,
                        "submitted_batches": len(submitted),
                        "visible_values": sorted(found),
                        "revision": result["catalog_revision"],
                    }
                )
            seen[family] = found
        if (
            all(found == expected for found in seen.values())
            and len(submitted) == batch_count
        ):
            converged_seconds = time.monotonic() - started
            break
        time.sleep(0.25)
    canonical = run.execute(
        "clickhouse",
        [
            "clickhouse-client",
            "--query",
            "SELECT count() AS rows, uniqExact(id) AS ids, uniqExact(project_id) AS projects, "
            "toString(any(project_id)) AS project, toString(any(org_id)) AS organization "
            "FROM default.spans WHERE startsWith(name, 'otlp_stream_') "
            "SETTINGS max_execution_time=2, max_threads=1, output_format_json_quote_64bit_integers=0 FORMAT JSONEachRow",
        ],
    )
    source = json.loads(canonical.stdout)
    duration_seconds = time.monotonic() - started
    catch_up_seconds = (
        converged_seconds - submitted[-1]["seconds"]
        if converged_seconds is not None and submitted
        else None
    )
    passed = (
        len(submitted) == batch_count
        and all(found == expected for found in seen.values())
        and set(first_during_traffic) == {"live", "late"}
        and all(
            seconds <= _CONTINUOUS_VISIBILITY_SECONDS
            for seconds in first_visible.values()
        )
        and catch_up_seconds is not None
        and catch_up_seconds <= _CONTINUOUS_VISIBILITY_SECONDS
        and duration_seconds <= _CONTINUOUS_TIMEOUT_SECONDS
        and source
        == {
            "rows": batch_count * 2,
            "ids": batch_count * 2,
            "projects": 1,
            "project": fixture["project_id"],
            "organization": fixture["organization_id"],
        }
    )
    report = {
        "status": "passed" if passed else "failed",
        "submitted": submitted,
        "observations": observations,
        "visible": {k: sorted(v) for k, v in seen.items()},
        "batch_count": batch_count,
        "submission_interval_seconds": _CONTINUOUS_INTERVAL_SECONDS,
        "total_budget_seconds": _CONTINUOUS_TIMEOUT_SECONDS,
        "visibility_budget_seconds": _CONTINUOUS_VISIBILITY_SECONDS,
        "first_visible_seconds": first_visible,
        "first_visible_while_sending_seconds": first_during_traffic,
        "canonical": source,
        "duration_seconds": duration_seconds,
        "converged_seconds": converged_seconds,
        "catch_up_after_last_submission_seconds": catch_up_seconds,
    }
    save(run.directory / "collector-continuous.json", report)
    if not passed:
        raise RuntimeError(
            "continuous ingestion missed sustained visibility, catch-up, or exact-source bounds; see collector-continuous.json"
        )
    return report


def verify_candidates(run: Run) -> None:
    # Read-only broker evidence ensures API visibility was not provided only by
    # scheduled source scans while collector candidate emission silently failed.
    result = run.execute(
        "kafka",
        [
            "/opt/kafka/bin/kafka-console-consumer.sh",
            "--bootstrap-server",
            "kafka:9092",
            "--topic",
            run.manifest["candidate_topic"] + ".application",
            "--from-beginning",
            "--timeout-ms",
            "5000",
            "--max-messages",
            # One record per continuous span plus the existing bounded setup
            # allowance; retain the broker and enclosing command timeouts.
            str(_CONTINUOUS_BATCH_COUNT * 2 + 20),
        ],
        timeout=20,
        check=False,
    )
    candidates = [
        json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")
    ]
    matched = [
        c
        for c in candidates
        if any(
            v.get("attribute_key", "").startswith("otlp_") for v in c.get("values", [])
        )
    ]
    fixture = json.loads((run.directory / "source-fixture.json").read_text())
    if not matched or any(
        c.get("version") != 2
        or c.get("catalog_epoch", 0)
        or c.get("projection_version", 0)
        or c.get("organization_id") != fixture["organization_id"]
        or c.get("workspace_id") != fixture["workspace_id"]
        for c in matched
    ):
        raise RuntimeError(
            "actual collector candidate identity/emission was not verified"
        )
    values = {
        (v["attribute_key"], v["value_json"]) for c in matched for v in c["values"]
    }
    if (
        not {
            ("otlp_live", '"initial"'),
            ("otlp_late", '"initial"'),
            ("otlp_late", '"updated"'),
        }
        <= values
    ):
        raise RuntimeError(
            "collector candidates did not include initial and updated observations"
        )
    expected_continuous = {
        (f"otlp_stream_{family}", json.dumps(str(i)))
        for family in ("live", "late")
        for i in range(_CONTINUOUS_BATCH_COUNT)
    }
    actual_continuous = {item for item in values if item[0].startswith("otlp_stream_")}
    if actual_continuous != expected_continuous:
        raise RuntimeError(
            "collector candidate stream mismatched continuous observations"
        )
    save(
        run.directory / "collector-candidates.json",
        {"status": "passed", "managed_candidates": matched},
    )
