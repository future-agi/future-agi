#!/usr/bin/env python3
"""Opt-in current-stack Omega Error Feed end-to-end check.

The default invocation is read-only and prints prerequisites. ``--run`` creates
one clearly marked Observe project, enables Omega only for that project, and
leaves the fixture in place for inspection. It never creates credentials,
deletes data, or enables billing emission.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


RUN_ACK = "CREATE_ONE_SYNTHETIC_OMEGA_PROJECT"
SAFE_GATEWAY_ACK = "ONE_BOUNDED_SYNTHETIC_GEMINI_TRACE"
FIXTURE_MARKER = "omega-current-stack-e2e/v1"
STATE_MARKER = "OMEGA_E2E_STATE="
REPO_ROOT = Path(__file__).resolve().parents[2]

PREFLIGHT = "preflight"

ACTIVATE_PROJECT = "activate"

READ_STATE = "state"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="create and run the fixture")
    parser.add_argument(
        "--backend-url",
        default=os.getenv("OMEGA_E2E_BACKEND_URL", "http://127.0.0.1:8000"),
    )
    parser.add_argument(
        "--collector-url",
        default=os.getenv("OMEGA_E2E_COLLECTOR_URL", "http://127.0.0.1:4318"),
    )
    parser.add_argument(
        "--organization-id", default=os.getenv("OMEGA_E2E_ORGANIZATION_ID")
    )
    parser.add_argument("--workspace-id", default=os.getenv("OMEGA_E2E_WORKSPACE_ID"))
    parser.add_argument("--engine-version", default="omega-v1")
    parser.add_argument(
        "--expected-model", default=os.getenv("OMEGA_E2E_EXPECTED_MODEL")
    )
    parser.add_argument("--collector-api-key-file", type=Path)
    parser.add_argument("--collector-secret-key-file", type=Path)
    parser.add_argument("--compose-file", action="append", type=Path)
    parser.add_argument("--compose-project-name")
    parser.add_argument("--backend-service", default="backend")
    parser.add_argument("--timeout-seconds", type=int, default=600)
    return parser


def _describe() -> None:
    print(
        json.dumps(
            {
                "mode": "read_only",
                "run_ack": f"OMEGA_E2E_RUN_ACK={RUN_ACK}",
                "safe_gateway_ack": (f"OMEGA_E2E_SAFE_GATEWAY_ACK={SAFE_GATEWAY_ACK}"),
                "required": [
                    "current backend, Postgres, ClickHouse, Kafka, collector, Node worker, grouping, and usage services",
                    "current migrations and ERROR_FEED_OMEGA_ENABLED=true",
                    "collector notifications enabled on error-feed.trace-available.v1",
                    "an existing API-key pair whose resolved user can write and read Error Feed in the supplied workspace",
                    "collector, Omega gateway, and Django grouping routes scoped to the same local stack",
                    "the expected Gemini model approved for one synthetic trace (Omega capped at eight calls)",
                    "ERROR_FEED_OMEGA_BILLING_EMIT_ENABLED=false",
                ],
                "endpoints": {
                    "project_create": "/tracer/project/",
                    "collector_otlp_http": "/v1/traces",
                    "feed_readback": "/tracer/feed/issues/",
                },
                "mutation": "none; pass --run with both acknowledgements to create one marked project",
            },
            indent=2,
        )
    )


def _uuid(value: str | None, name: str) -> str:
    try:
        parsed = uuid.UUID(value or "")
    except (AttributeError, ValueError) as error:
        raise ValueError(f"{name} must be a UUID") from error
    if parsed.int == 0:
        raise ValueError(f"{name} must not be the nil UUID")
    return str(parsed)


def _local_url(value: str, name: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{name} must be an uncredentialed loopback HTTP URL")
    return value.rstrip("/") + "/"


def _secret(path: Path | None, name: str) -> str:
    if path is None:
        raise ValueError(f"{name} is required")
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{name} must be a regular, non-symlink file")
    resolved = path.resolve(strict=True)
    if resolved.is_relative_to(REPO_ROOT):
        raise ValueError(f"{name} must be stored outside the repository")
    if resolved.stat().st_mode & 0o077:
        raise ValueError(f"{name} must not be group/world accessible")
    value = resolved.read_text(encoding="utf-8").strip()
    if not value or len(value) > 4096:
        raise ValueError(f"{name} is empty or unexpectedly large")
    return value


def _http_json(
    method: str,
    base_url: str,
    path: str,
    *,
    headers: dict[str, str],
    body: dict | None = None,
) -> dict:
    data = None if body is None else json.dumps(body, separators=(",", ":")).encode()
    request = Request(
        urljoin(base_url, path.lstrip("/")),
        data=data,
        headers={"Accept": "application/json", **headers},
        method=method,
    )
    try:
        with build_opener(NoRedirect()).open(request, timeout=30) as response:
            if urlparse(response.geturl()).hostname not in {
                "127.0.0.1",
                "localhost",
                "::1",
            }:
                raise RuntimeError("request escaped the loopback endpoint")
            raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise RuntimeError("HTTP response exceeded 2 MiB")
            return json.loads(raw or b"{}")
    except HTTPError as error:
        detail = error.read(4096).decode("utf-8", "replace")
        raise RuntimeError(
            f"{method} {path} returned HTTP {error.code}: {detail}"
        ) from error
    except (URLError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{method} {path} failed: {error}") from error


def _compose_prefix(args: argparse.Namespace) -> list[str]:
    docker = shutil.which("docker")
    legacy = shutil.which("docker-compose")
    command = [legacy] if legacy else ([docker, "compose"] if docker else None)
    if command is None:
        raise RuntimeError("docker compose or docker-compose is required")
    files = args.compose_file or [REPO_ROOT / "docker-compose.yml"]
    for path in files:
        resolved = path.resolve(strict=True)
        if not resolved.is_file():
            raise ValueError(f"compose file is not a regular file: {resolved}")
        command.extend(["-f", str(resolved)])
    if args.compose_project_name:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.compose_project_name):
            raise ValueError("invalid compose project name")
        command.extend(["-p", args.compose_project_name])
    return command


def _django_shell(
    args: argparse.Namespace, source: str, environment: dict[str, str]
) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.backend_service):
        raise ValueError("invalid backend service name")
    command = _compose_prefix(args) + ["exec", "-T"]
    command.extend(
        [
            "-e",
            "SERVICE_TYPE=bootstrap",
            "-e",
            "STARTUP_DB_MUTATION_MODE=operator",
            "-e",
            "NO_STARTUP_DB_MUTATIONS=false",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            "-e",
            "LOG_LEVEL=WARNING",
            "-e",
            "OTEL_SDK_DISABLED=true",
            "-e",
            "FUTURE_AGI_TELEMETRY_DISABLED=true",
            "-e",
            "LITELLM_LOCAL_MODEL_COST_MAP=True",
        ]
    )
    for key in environment:
        command.extend(["-e", key])
    command.extend(
        [
            "-e",
            f"OMEGA_E2E_RUN_ACK={RUN_ACK}",
            args.backend_service,
            "python",
            "manage.py",
            "verify_omega_current_stack",
            source,
        ]
    )
    try:
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            env={**os.environ, **environment},
            text=True,
            capture_output=True,
            timeout=180,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("Django container command exceeded 180 seconds") from None
    if completed.returncode:
        with tempfile.NamedTemporaryFile(
            mode="w", prefix="omega-e2e-command-", suffix=".log", delete=False
        ) as log:
            log.write(completed.stdout)
            log.write(completed.stderr)
        raise RuntimeError(
            f"Django container command failed with exit {completed.returncode}; "
            f"private diagnostic log: {log.name}"
        )
    for line in reversed(completed.stdout.splitlines()):
        if line.startswith(STATE_MARKER):
            return json.loads(line.removeprefix(STATE_MARKER))
    raise RuntimeError("Django container command returned no state marker")


def _otlp_payload(
    project_name: str, trace_id: uuid.UUID, root_id: str, child_id: str
) -> dict:
    end = datetime.now(UTC) - timedelta(seconds=5)
    start = end - timedelta(seconds=2)
    attrs = [
        {
            "key": "input.value",
            "value": {"stringValue": "Refund exactly USD 10 for order E2E-ONLY."},
        },
        {
            "key": "output.value",
            "value": {"stringValue": "Refunded only USD 5; request left incomplete."},
        },
        {"key": "error.type", "value": {"stringValue": "synthetic_partial_refund"}},
    ]
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "project_name", "value": {"stringValue": project_name}},
                        {"key": "project_type", "value": {"stringValue": "observe"}},
                        {
                            "key": "service.name",
                            "value": {"stringValue": "omega-current-stack-e2e"},
                        },
                        {"key": "fi.semconv", "value": {"stringValue": "fi_native"}},
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "omega-current-stack-e2e"},
                        "spans": [
                            {
                                "traceId": trace_id.hex,
                                "spanId": root_id,
                                "name": "synthetic refund workflow",
                                "startTimeUnixNano": str(
                                    int(start.timestamp() * 1_000_000_000)
                                ),
                                "endTimeUnixNano": str(
                                    int(end.timestamp() * 1_000_000_000)
                                ),
                                "attributes": attrs,
                                "status": {
                                    "code": 2,
                                    "message": "synthetic partial refund",
                                },
                            },
                            {
                                "traceId": trace_id.hex,
                                "spanId": child_id,
                                "parentSpanId": root_id,
                                "name": "apply refund",
                                "startTimeUnixNano": str(
                                    int(
                                        (
                                            start + timedelta(milliseconds=500)
                                        ).timestamp()
                                        * 1_000_000_000
                                    )
                                ),
                                "endTimeUnixNano": str(
                                    int(
                                        (end - timedelta(milliseconds=500)).timestamp()
                                        * 1_000_000_000
                                    )
                                ),
                                "attributes": attrs,
                                "status": {
                                    "code": 2,
                                    "message": "refunded 5 instead of 10",
                                },
                            },
                        ],
                    }
                ],
            }
        ]
    }


def _assert_report(
    state: dict, *, organization_id: str, project_id: str, expected_model: str | None
) -> None:
    if state["billing_emission_enabled"]:
        raise AssertionError("billing emission became enabled during the run")
    if (
        state["delivery_count"] < 1
        or "error-feed.trace-available.v1" not in state["delivery_topics"]
    ):
        raise AssertionError(
            "no durable collector/Kafka notification receipt was recorded"
        )
    if state["job_state"] != "completed":
        raise AssertionError(f"investigation job is {state['job_state']!r}")
    if (
        not state["active_projection_updated"]
        or state["grouping_status"] != "completed"
    ):
        raise AssertionError(
            "the current report was not completely projected and grouped"
        )
    if state["execution_status"] != "completed" or state["outcome"] != "failure":
        raise AssertionError(
            "synthetic failed trace was not reported as a completed failure"
        )
    if (
        len(state["findings"]) != 1
        or len(state["issues"]) != 1
        or state["membership_count"] != 1
    ):
        raise AssertionError(
            "the one-defect fixture did not publish exactly one clustered finding"
        )
    finding_pairs = {(row["kind"], row["statement"]) for row in state["findings"]}
    issue_pairs = {(row["category"], row["brief"]) for row in state["issues"]}
    if not finding_pairs.issubset(issue_pairs) or any(
        not row["cluster_id"] for row in state["issues"]
    ):
        raise AssertionError(
            "Feed issues do not faithfully represent the report findings"
        )

    accounting = state["gateway_accounting"]
    if not isinstance(state["model_calls"], int) or not 2 <= state["model_calls"] <= 8:
        raise AssertionError("model-call accounting is outside the pinned 2-8 range")
    if not isinstance(accounting, list) or len(accounting) != state["model_calls"]:
        raise AssertionError("gateway accounting does not match model-call count")
    if expected_model and any(
        row["model_used"] != expected_model for row in accounting
    ):
        raise AssertionError("gateway used a model other than --expected-model")

    receipt = state["receipt"]
    if receipt is None or receipt["report_id"] != state["report_id"]:
        raise AssertionError("the immutable report has no pinned usage receipt")
    if (
        receipt["organization_id"] != organization_id
        or receipt["project_id"] != project_id
    ):
        raise AssertionError(
            "usage receipt tenant scope does not match the synthetic project"
        )
    expected_event_id = str(
        uuid.uuid5(uuid.UUID(state["report_id"]), "trace-error-analysis-usage/v1")
    )
    if receipt["event_id"] != expected_event_id:
        raise AssertionError("usage event identity is not deterministic")
    if (
        receipt["status"] == "emitted"
        or receipt["emitted_at"]
        or receipt["delivery_attempts"]
    ):
        raise AssertionError("the E2E fixture attempted or completed billing delivery")

    costs = [row["cost"] for row in accounting]
    if any(cost is None for cost in costs):
        if receipt["raw_cost_usd"] is not None or receipt["status"] != "unpriced":
            raise AssertionError("missing gateway cost was not preserved as unknown")
        return
    total = sum((Decimal(str(cost)) for cost in costs), Decimal("0"))
    if receipt["raw_cost_usd"] is None or Decimal(receipt["raw_cost_usd"]) != total:
        raise AssertionError("usage receipt did not pin the gateway cost sum")
    if state["reported_cost_usd"] is None or abs(
        Decimal(str(state["reported_cost_usd"])) - total
    ) > Decimal("0.000000001"):
        raise AssertionError("reported total cost does not match per-call accounting")
    if receipt["event_payload"]:
        payload = receipt["event_payload"]
        if payload != {
            "event_id": receipt["event_id"],
            "org_id": organization_id,
            "event_type": "trace_error_analysis",
            "report_id": state["report_id"],
            "project_id": project_id,
            "source": "omega_trace_investigation",
        }:
            raise AssertionError("pinned usage event payload has the wrong scope")


def _usage_is_pinned(state: dict) -> bool:
    receipt = state.get("receipt")
    if not receipt:
        return False
    if receipt["status"] != "pending":
        return True
    return bool(receipt["event_payload"])


def _run(args: argparse.Namespace) -> None:
    if os.getenv("OMEGA_E2E_RUN_ACK") != RUN_ACK:
        raise ValueError(f"set OMEGA_E2E_RUN_ACK={RUN_ACK}")
    if os.getenv("OMEGA_E2E_SAFE_GATEWAY_ACK") != SAFE_GATEWAY_ACK:
        raise ValueError(f"set OMEGA_E2E_SAFE_GATEWAY_ACK={SAFE_GATEWAY_ACK}")
    organization_id = _uuid(args.organization_id, "organization_id")
    workspace_id = _uuid(args.workspace_id, "workspace_id")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,20}", args.engine_version):
        raise ValueError("engine_version must be 1-20 safe characters")
    if not args.expected_model:
        raise ValueError(
            "--expected-model is required to pin the approved gateway route"
        )
    if not 60 <= args.timeout_seconds <= 1800:
        raise ValueError("timeout_seconds must be between 60 and 1800")
    backend_url = _local_url(args.backend_url, "backend_url")
    collector_url = _local_url(args.collector_url, "collector_url")
    collector_api_key = _secret(args.collector_api_key_file, "collector_api_key_file")
    collector_secret_key = _secret(
        args.collector_secret_key_file, "collector_secret_key_file"
    )
    scoped_headers = {
        "X-Api-Key": collector_api_key,
        "X-Secret-Key": collector_secret_key,
        "X-Organization-Id": organization_id,
        "X-Workspace-Id": workspace_id,
    }

    preflight = _django_shell(
        args,
        PREFLIGHT,
        {
            "OMEGA_E2E_API_KEY": collector_api_key,
            "OMEGA_E2E_ORGANIZATION_ID": organization_id,
            "OMEGA_E2E_WORKSPACE_ID": workspace_id,
        },
    )
    if preflight != {
        "billing_emission_enabled": False,
        "omega_enabled": True,
        "omega_tables_ready": True,
        "api_key_scoped": True,
        "api_user_can_write": True,
        "organization_exists": True,
        "workspace_exists": True,
    }:
        raise RuntimeError(
            f"current-stack preflight refused the requested scope: {preflight}"
        )
    user_info = _http_json(
        "GET", backend_url, "/accounts/user-info/", headers=scoped_headers
    )
    if user_info.get("status") in {False, "failed", "error"}:
        raise RuntimeError("user token did not authenticate in the requested scope")
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4)
    project_name = f"__omega_e2e__{run_id}"
    created = _http_json(
        "POST",
        backend_url,
        "/tracer/project/",
        headers={"Content-Type": "application/json", **scoped_headers},
        body={
            "name": project_name,
            "model_type": "GenerativeLLM",
            "trace_type": "observe",
            "metadata": {"fixture": FIXTURE_MARKER},
            "source": "prototype",
            "tags": ["omega-e2e", "synthetic"],
        },
    )
    if created.get("status") is not True or not isinstance(created.get("result"), dict):
        raise RuntimeError("project create response did not use the expected envelope")
    project_id = _uuid(created["result"].get("project_id"), "created project_id")
    if created["result"].get("name") != project_name:
        raise RuntimeError("project create response returned a different project")
    print(
        json.dumps(
            {
                "stage": "fixture_created",
                "project_id": project_id,
                "project_name": project_name,
                "retained_on_failure": True,
            },
            sort_keys=True,
        )
    )

    shell_env = {
        "OMEGA_E2E_PROJECT_ID": project_id,
        "OMEGA_E2E_ORGANIZATION_ID": organization_id,
        "OMEGA_E2E_WORKSPACE_ID": workspace_id,
        "OMEGA_E2E_PROJECT_NAME": project_name,
        "OMEGA_E2E_ENGINE_VERSION": args.engine_version,
    }
    activated = _django_shell(args, ACTIVATE_PROJECT, shell_env)
    if (
        activated.get("billing_emission_enabled") is not False
        or activated.get("engine") != "omega"
        or activated.get("engine_version") != args.engine_version
        or activated.get("project_id") != project_id
        or activated.get("limits", {}).get("max_model_calls") != 8
    ):
        raise RuntimeError("Omega activation preflight returned unexpected state")

    trace_id = uuid.uuid4()
    root_span_id = secrets.token_hex(8)
    payload = _otlp_payload(project_name, trace_id, root_span_id, secrets.token_hex(8))
    _http_json(
        "POST",
        collector_url,
        "/v1/traces",
        headers={
            "Content-Type": "application/json",
            "X-Api-Key": collector_api_key,
            "X-Secret-Key": collector_secret_key,
        },
        body=payload,
    )

    state_env = {
        "OMEGA_E2E_PROJECT_ID": project_id,
        "OMEGA_E2E_TRACE_ID": str(trace_id),
    }
    deadline = time.monotonic() + args.timeout_seconds
    previous = None
    state = {}
    while time.monotonic() < deadline:
        state = _django_shell(args, READ_STATE, state_env)
        progress = (
            state.get("job_state"),
            state.get("report_id"),
            state.get("grouping_status"),
            state.get("receipt", {}).get("status") if state.get("receipt") else None,
        )
        if progress != previous:
            print(json.dumps({"stage": "pipeline", "state": progress}))
            previous = progress
        if state.get("execution_status") == "failed":
            raise AssertionError(
                f"investigation execution failed; report={state['report_id']}, "
                f"model_calls={state['model_calls']}, cost_usd={state['reported_cost_usd']}"
            )
        if state.get("grouping_status") in {"failed", "stale"}:
            raise AssertionError(f"grouping ended as {state['grouping_status']}")
        if state.get("grouping_status") == "completed" and _usage_is_pinned(state):
            break
        time.sleep(5)
    else:
        raise TimeoutError(f"pipeline did not finish within {args.timeout_seconds}s")

    _assert_report(
        state,
        organization_id=organization_id,
        project_id=project_id,
        expected_model=args.expected_model,
    )
    query = urlencode({"project_id": project_id, "source": "scanner", "limit": 200})
    feed = _http_json(
        "GET", backend_url, f"/tracer/feed/issues/?{query}", headers=scoped_headers
    )
    rows = feed.get("result", {}).get("data", []) if feed.get("status") is True else []
    issue_by_cluster = {row["cluster_id"]: row for row in state["issues"]}
    matching = [row for row in rows if row.get("cluster_id") in issue_by_cluster]
    if len(matching) != len(issue_by_cluster):
        raise AssertionError("existing Feed API did not return every grouped finding")
    for row in matching:
        issue = issue_by_cluster[row["cluster_id"]]
        if (
            row.get("project_id") != project_id
            or row.get("trace_id") != str(trace_id)
            or row.get("source") != "scanner"
            or row.get("error") != {"name": issue["brief"], "type": issue["category"]}
            or row.get("occurrences", 0) < 1
            or row.get("trace_count", 0) < 1
        ):
            raise AssertionError(
                "Feed row does not match the grouped synthetic finding"
            )

    print(
        json.dumps(
            {
                "status": "passed",
                "fixture": FIXTURE_MARKER,
                "project_id": project_id,
                "project_name": project_name,
                "trace_id": str(trace_id),
                "root_span_id": root_span_id,
                "job_id": state["job_id"],
                "report_id": state["report_id"],
                "cluster_ids": sorted(issue_by_cluster),
                "usage_event_id": state["receipt"]["event_id"],
                "billing_emitted": False,
                "retained_for_inspection": True,
            },
            sort_keys=True,
        )
    )


def main() -> int:
    args = _parser().parse_args()
    if not args.run:
        _describe()
        return 0
    try:
        _run(args)
    except Exception as error:
        print(f"current-stack E2E failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
