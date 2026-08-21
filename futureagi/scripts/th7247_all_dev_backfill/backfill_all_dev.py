#!/usr/bin/env python3
"""Backfill every workspace-bound Observe project into one isolated DEV catalog.

This operator intentionally excludes legacy projects whose PostgreSQL project row
has no workspace.  It never guesses tenancy.  Each bounded lane owns a separate
Python/Go runtime directory and processes one workspace revision at a time.
Source PostgreSQL and ClickHouse access remains read-only; writes are limited to
the six tables in an already-provisioned ``th7247_catalog_dev_*`` database.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import threading
import uuid
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ACKNOWLEDGEMENT = "TH7247_BACKFILL_ALL_WORKSPACE_BOUND_DEV_DATA"
CATALOG_DATABASE_RE = re.compile(r"^th7247_catalog_dev_[a-z0-9_]+$")
EXPECTED_TABLES = {
    "property_catalog_activations",
    "property_catalog_checkpoints",
    "property_catalog_deliveries",
    "property_catalog_source_streams",
    "property_definition_catalog",
    "span_attribute_value_catalog",
}
COMPLETED_STAGES = ["schema", "backfill", "reconcile", "qualify", "activate"]
CONTAINER_RUNTIME = "/var/lib/property-catalog-runtime"
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class BackfillError(RuntimeError):
    """The all-DEV backfill cannot prove its bounded execution contract."""


@dataclass(frozen=True, slots=True)
class Scope:
    organization_id: str
    workspace_id: str
    project_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Lane:
    index: int
    runtime: Path
    scopes: tuple[Scope, ...]


def _canonical_uuid(value: str, *, field: str) -> str:
    try:
        canonical = str(uuid.UUID(value))
    except (ValueError, AttributeError) as exc:
        raise BackfillError(f"{field} is not a UUID") from exc
    if value != canonical:
        raise BackfillError(f"{field} is not canonical lowercase UUID text")
    return canonical


def _run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: int | None = None,
    stdout: Any = subprocess.PIPE,
    stderr: Any = subprocess.PIPE,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        list(command),
        cwd=cwd,
        check=False,
        text=True,
        stdout=stdout,
        stderr=stderr,
        timeout=timeout,
    )
    if completed.returncode != 0:
        detail = ""
        if isinstance(completed.stderr, str):
            detail = ANSI_RE.sub("", completed.stderr).strip()[-2_000:]
        raise BackfillError(
            f"command failed with exit {completed.returncode}: "
            f"{' '.join(command[:5])}; {detail}"
        )
    return completed


def _validate_host(expected_hostname: str) -> None:
    observed = _run(["hostname"]).stdout.strip()
    if observed != expected_hostname or any(
        marker in observed.casefold() for marker in ("prod", "production", "live")
    ):
        raise BackfillError(
            f"host identity mismatch: expected {expected_hostname!r}, got {observed!r}"
        )


def _load_scopes(args: argparse.Namespace) -> tuple[Scope, ...]:
    sql = """
WITH eligible AS (
  SELECT
    p.organization_id::text AS organization_id,
    p.workspace_id::text AS workspace_id,
    string_agg(p.id::text, ',' ORDER BY p.id::text) AS project_ids
  FROM tracer_project AS p
  JOIN accounts_workspace AS w ON w.id = p.workspace_id
  JOIN accounts_organization AS o ON o.id = p.organization_id
  WHERE NOT p.deleted
    AND p.trace_type = 'observe'
    AND p.workspace_id IS NOT NULL
    AND NOT w.deleted
    AND w.is_active
    AND w.organization_id = p.organization_id
  GROUP BY p.organization_id, p.workspace_id
)
SELECT organization_id, workspace_id, project_ids
FROM eligible
ORDER BY organization_id, workspace_id
""".strip()
    result = _run(
        [
            "docker",
            "exec",
            args.postgres_container,
            "psql",
            "-U",
            args.postgres_user,
            "-d",
            args.postgres_database,
            "-X",
            "-A",
            "-t",
            "-F",
            "\t",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            sql,
        ]
    )
    scopes: list[Scope] = []
    seen_workspaces: set[str] = set()
    seen_projects: set[str] = set()
    for line_number, line in enumerate(result.stdout.splitlines(), start=1):
        fields = line.split("\t")
        if len(fields) != 3:
            raise BackfillError(f"scope row {line_number} is not exact TSV")
        organization_id = _canonical_uuid(fields[0], field="organization_id")
        workspace_id = _canonical_uuid(fields[1], field="workspace_id")
        project_ids = tuple(
            _canonical_uuid(value, field="project_id")
            for value in fields[2].split(",")
            if value
        )
        if (
            not project_ids
            or len(project_ids) > 256
            or project_ids != tuple(sorted(set(project_ids)))
        ):
            raise BackfillError(
                f"workspace {workspace_id} does not have 1..256 sorted unique projects"
            )
        if workspace_id in seen_workspaces:
            raise BackfillError(f"workspace {workspace_id} appears more than once")
        overlap = seen_projects.intersection(project_ids)
        if overlap:
            raise BackfillError(
                f"projects cross workspace boundaries: {sorted(overlap)}"
            )
        seen_workspaces.add(workspace_id)
        seen_projects.update(project_ids)
        scopes.append(Scope(organization_id, workspace_id, project_ids))
    if len(scopes) != args.expected_workspaces:
        raise BackfillError(
            f"workspace inventory drifted: expected {args.expected_workspaces}, "
            f"observed {len(scopes)}"
        )
    if len(seen_projects) != args.expected_projects:
        raise BackfillError(
            f"project inventory drifted: expected {args.expected_projects}, "
            f"observed {len(seen_projects)}"
        )
    return tuple(scopes)


def _legacy_null_workspace_count(args: argparse.Namespace) -> int:
    sql = (
        "SELECT count(*) FROM tracer_project "
        "WHERE NOT deleted AND trace_type='observe' AND workspace_id IS NULL"
    )
    result = _run(
        [
            "docker",
            "exec",
            args.postgres_container,
            "psql",
            "-U",
            args.postgres_user,
            "-d",
            args.postgres_database,
            "-X",
            "-A",
            "-t",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            sql,
        ]
    )
    try:
        return int(result.stdout.strip())
    except ValueError as exc:
        raise BackfillError("legacy project count is not an integer") from exc


def _clickhouse_query(args: argparse.Namespace, sql: str) -> str:
    return _run(
        [
            "docker",
            "exec",
            args.clickhouse_container,
            "clickhouse-client",
            "--database",
            args.target_database,
            "--query",
            sql,
        ]
    ).stdout


def _validate_target_schema(args: argparse.Namespace) -> None:
    if CATALOG_DATABASE_RE.fullmatch(args.target_database) is None:
        raise BackfillError("target database is not an isolated DEV catalog identifier")
    sql = (
        "SELECT name FROM system.tables "
        f"WHERE database='{args.target_database}' ORDER BY name FORMAT TSV"
    )
    observed = set(_clickhouse_query(args, sql).splitlines())
    if observed != EXPECTED_TABLES:
        raise BackfillError(
            f"target table inventory differs: expected {sorted(EXPECTED_TABLES)}, "
            f"observed {sorted(observed)}"
        )


def _active_scopes(args: argparse.Namespace) -> set[tuple[str, str]]:
    sql = (
        "SELECT DISTINCT toString(organization_id),toString(workspace_id) "
        "FROM property_catalog_activations FINAL WHERE status='active' "
        "ORDER BY organization_id,workspace_id FORMAT TSV"
    )
    result: set[tuple[str, str]] = set()
    for line in _clickhouse_query(args, sql).splitlines():
        fields = line.split("\t")
        if len(fields) != 2:
            raise BackfillError("active scope inventory is not exact TSV")
        result.add(
            (
                _canonical_uuid(fields[0], field="active organization_id"),
                _canonical_uuid(fields[1], field="active workspace_id"),
            )
        )
    return result


def _validate_runtime(runtime: Path) -> None:
    expected = {
        runtime: 0o770,
        runtime / "cache": 0o770,
        runtime / "home": 0o770,
        runtime / "span-dead-letter": 0o770,
        runtime / "catalog-spool": 0o700,
    }
    for path, expected_mode in expected.items():
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise BackfillError(f"runtime path is not a physical directory: {path}")
        if stat.S_IMODE(info.st_mode) != expected_mode:
            raise BackfillError(
                f"runtime mode differs for {path}: "
                f"expected {oct(expected_mode)}, observed {oct(stat.S_IMODE(info.st_mode))}"
            )
    spool = runtime / "catalog-spool"
    if spool.stat().st_uid != 65532 or spool.stat().st_gid != 65532:
        raise BackfillError("catalog-spool must be owned by 65532:65532")


def _partition(
    scopes: tuple[Scope, ...], runtime_root: Path, count: int
) -> tuple[Lane, ...]:
    if not 1 <= count <= 8:
        raise BackfillError("lane count must be in [1, 8]")
    buckets: list[list[Scope]] = [[] for _ in range(count)]
    for index, scope in enumerate(scopes):
        buckets[index % count].append(scope)
    lanes = tuple(
        Lane(
            index=index,
            runtime=runtime_root / f"lane-{index}",
            scopes=tuple(sorted(bucket, key=lambda scope: scope.workspace_id)),
        )
        for index, bucket in enumerate(buckets)
    )
    for lane in lanes:
        _validate_runtime(lane.runtime)
    return lanes


def _producer_configuration(container: str) -> dict[str, Any] | None:
    inspect = subprocess.run(
        ["docker", "inspect", container],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if inspect.returncode != 0:
        return None
    decoded = json.loads(inspect.stdout)
    if not isinstance(decoded, list) or len(decoded) != 1:
        raise BackfillError(f"producer inspect is ambiguous: {container}")
    return decoded[0]


def _ensure_producer(args: argparse.Namespace, lane: Lane) -> str:
    name = f"{args.producer_name_prefix}-{lane.index}"
    allowlist = ",".join(scope.workspace_id for scope in lane.scopes)
    existing = _producer_configuration(name)
    if existing is not None:
        state = existing.get("State", {})
        mounts = existing.get("Mounts", [])
        environment = existing.get("Config", {}).get("Env", [])
        expected_mount = str(lane.runtime.resolve())
        mount_matches = any(
            value.get("Destination") == CONTAINER_RUNTIME
            and value.get("Source") == expected_mount
            for value in mounts
        )
        env_matches = (
            f"FI_PROPERTY_CATALOG_WORKSPACE_ALLOWLIST={allowlist}" in environment
        )
        if state.get("Running") is not True or not mount_matches or not env_matches:
            raise BackfillError(
                f"existing producer {name} differs; stop that exact test container "
                "before retrying"
            )
        return name
    _run(
        [
            "docker",
            "compose",
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "-v",
            f"{lane.runtime.resolve()}:{CONTAINER_RUNTIME}",
            "-e",
            f"FI_PROPERTY_CATALOG_WORKSPACE_ALLOWLIST={allowlist}",
            args.producer_service,
        ],
        cwd=args.compose_directory,
    )
    existing = _producer_configuration(name)
    if existing is None or existing.get("State", {}).get("Running") is not True:
        raise BackfillError(f"producer {name} did not become running")
    return name


def _operator_command(
    args: argparse.Namespace,
    lane: Lane,
    scope: Scope,
) -> list[str]:
    environment = {
        "PROPERTY_CATALOG_DEV_ORGANIZATION_ID": scope.organization_id,
        "PROPERTY_CATALOG_DEV_WORKSPACE_ID": scope.workspace_id,
        "PROPERTY_CATALOG_DEV_WORKSPACE_ALLOWLIST": scope.workspace_id,
        "PROPERTY_CATALOG_DEV_PROJECT_ALLOWLIST": ",".join(scope.project_ids),
        "PROPERTY_CATALOG_DEV_IDENTITY": args.dev_identity,
        "PROPERTY_CATALOG_DEV_SPAN_SINCE": args.span_since,
        "PROPERTY_CATALOG_DEV_SPAN_UNTIL": args.span_until,
        "PROPERTY_CATALOG_DEV_TARGET_DATABASE": args.target_database,
        "PROPERTY_CATALOG_DEV_WRITE_CH_DATABASE": args.target_database,
    }
    command = [
        "docker",
        "compose",
        "--profile",
        "operator",
        "run",
        "--rm",
        "-v",
        f"{lane.runtime.resolve()}:{CONTAINER_RUNTIME}",
    ]
    for name, value in environment.items():
        command.extend(("-e", f"{name}={value}"))
    command.extend(
        (
            args.operator_service,
            "--execute",
            "--initial-backfill-wall-ms",
            str(args.initial_backfill_wall_ms),
            "--organization-id",
            scope.organization_id,
            "--workspace-id",
            scope.workspace_id,
        )
    )
    return command


def _result_from_log(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        lines = handle.readlines()
    for raw in reversed(lines):
        line = ANSI_RE.sub("", raw).strip()
        if not line.startswith("{"):
            continue
        try:
            decoded = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(decoded, dict)
            and "completed" in decoded
            and "evidence" in decoded
        ):
            return decoded
    raise BackfillError(f"operator result JSON is absent from {path}")


def _validate_result(scope: Scope, result: dict[str, Any]) -> dict[str, Any]:
    if result.get("completed") != COMPLETED_STAGES:
        raise BackfillError(
            f"workspace {scope.workspace_id} did not complete all stages"
        )
    evidence = result.get("evidence")
    if not isinstance(evidence, list):
        raise BackfillError(f"workspace {scope.workspace_id} evidence is invalid")
    by_stage = {
        item.get("stage"): item.get("evidence")
        for item in evidence
        if isinstance(item, dict) and isinstance(item.get("evidence"), dict)
    }
    backfill = by_stage.get("backfill", {})
    reconcile = by_stage.get("reconcile", {})
    qualify = by_stage.get("qualify", {})
    activate = by_stage.get("activate", {})
    if (
        type(backfill.get("authoritative_source_count")) is not int
        or backfill["authoritative_source_count"] < 0
        or reconcile.get("postgres_adapter_count") != 5
        or reconcile.get("definition_streams") != 7
        or qualify.get("qualified") is not True
        or qualify.get("stream_count") != 10
        or activate.get("activated") is not True
    ):
        raise BackfillError(f"workspace {scope.workspace_id} evidence is incomplete")
    return {
        "activated": True,
        "catalog_revision": activate.get("catalog_revision"),
        "live_definition_rows": activate.get("live_definition_rows"),
        "organization_id": scope.organization_id,
        "project_count": len(scope.project_ids),
        "source_span_rows": backfill.get("authoritative_source_count"),
        "value_rows": activate.get("value_rows"),
        "workspace_id": scope.workspace_id,
    }


def _run_lane(
    args: argparse.Namespace,
    lane: Lane,
    active_before: set[tuple[str, str]],
    output_lock: threading.Lock,
    stop: threading.Event,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    lane_logs = args.evidence_directory / f"lane-{lane.index}"
    lane_logs.mkdir(mode=0o770, parents=True, exist_ok=True)
    for ordinal, scope in enumerate(lane.scopes, start=1):
        if stop.is_set():
            break
        key = (scope.organization_id, scope.workspace_id)
        if key in active_before:
            summary = {
                "organization_id": scope.organization_id,
                "project_count": len(scope.project_ids),
                "status": "already_active",
                "workspace_id": scope.workspace_id,
            }
        else:
            log_path = lane_logs / f"{ordinal:03d}-{scope.workspace_id}.log"
            try:
                with log_path.open("w", encoding="utf-8") as log:
                    _run(
                        _operator_command(args, lane, scope),
                        cwd=args.compose_directory,
                        timeout=args.operator_timeout_seconds,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                    )
                summary = _validate_result(scope, _result_from_log(log_path))
                summary["status"] = "activated"
                summary["log"] = str(log_path)
            except Exception:
                stop.set()
                raise
        output.append(summary)
        with output_lock:
            with args.summary_file.open("a", encoding="utf-8") as summary_file:
                summary_file.write(json.dumps(summary, sort_keys=True) + "\n")
                summary_file.flush()
                os.fsync(summary_file.fileno())
            print(json.dumps(summary, sort_keys=True), flush=True)
    return output


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--ack", required=True)
    parser.add_argument("--expected-hostname", required=True)
    parser.add_argument("--expected-workspaces", type=int, required=True)
    parser.add_argument("--expected-projects", type=int, required=True)
    parser.add_argument(
        "--expected-legacy-null-workspace-projects", type=int, required=True
    )
    parser.add_argument("--lanes", type=int, default=3)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--evidence-directory", type=Path, required=True)
    parser.add_argument("--compose-directory", type=Path, required=True)
    parser.add_argument("--target-database", required=True)
    parser.add_argument("--span-since", required=True)
    parser.add_argument("--span-until", required=True)
    parser.add_argument("--dev-identity", required=True)
    parser.add_argument("--postgres-container", default="futureagi-postgres-1")
    parser.add_argument("--postgres-user", default="user")
    parser.add_argument("--postgres-database", default="tfc")
    parser.add_argument("--clickhouse-container", default="futureagi-clickhouse-1")
    parser.add_argument("--producer-service", default="property-catalog-producer")
    parser.add_argument("--operator-service", default="property-catalog-operator")
    parser.add_argument("--producer-name-prefix", default="th7247-all-dev-producer")
    parser.add_argument("--initial-backfill-wall-ms", type=int, default=1_740_000)
    parser.add_argument("--operator-timeout-seconds", type=int, default=1_800)
    args = parser.parse_args()
    args.runtime_root = args.runtime_root.resolve(strict=True)
    args.compose_directory = args.compose_directory.resolve(strict=True)
    args.evidence_directory.mkdir(mode=0o770, parents=True, exist_ok=True)
    args.evidence_directory = args.evidence_directory.resolve(strict=True)
    args.summary_file = args.evidence_directory / "summary.jsonl"
    if args.summary_file.exists() and args.summary_file.stat().st_size:
        raise BackfillError(
            "summary file already contains evidence; use a fresh directory"
        )
    if not args.execute or args.ack != ACKNOWLEDGEMENT:
        raise BackfillError("the exact all-DEV execute acknowledgement is required")
    if not 100_001 <= args.initial_backfill_wall_ms <= 1_740_000:
        raise BackfillError("initial backfill wall is outside [100001, 1740000] ms")
    if not 1 <= args.operator_timeout_seconds <= 1_860:
        raise BackfillError("operator timeout must be in [1, 1860] seconds")
    return args


def main() -> int:
    args = _arguments()
    _validate_host(args.expected_hostname)
    _validate_target_schema(args)
    scopes = _load_scopes(args)
    legacy_count = _legacy_null_workspace_count(args)
    if legacy_count != args.expected_legacy_null_workspace_projects:
        raise BackfillError(
            "legacy null-workspace project inventory drifted: "
            f"expected {args.expected_legacy_null_workspace_projects}, "
            f"observed {legacy_count}"
        )
    lanes = _partition(scopes, args.runtime_root, args.lanes)
    active_before = _active_scopes(args)
    eligible = {(scope.organization_id, scope.workspace_id) for scope in scopes}
    unexpected = active_before.difference(eligible)
    if unexpected:
        raise BackfillError(
            f"catalog contains active scopes outside the eligible set: {unexpected}"
        )
    for lane in lanes:
        _ensure_producer(args, lane)
    output_lock = threading.Lock()
    stop = threading.Event()
    with ThreadPoolExecutor(max_workers=len(lanes)) as executor:
        futures = {
            executor.submit(
                _run_lane,
                args,
                lane,
                active_before,
                output_lock,
                stop,
            ): lane.index
            for lane in lanes
        }
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as exc:
                stop.set()
                raise BackfillError(f"lane {futures[future]} failed: {exc}") from exc
    active_after = _active_scopes(args)
    missing = eligible.difference(active_after)
    if missing:
        raise BackfillError(f"eligible workspaces remain inactive: {sorted(missing)}")
    final = {
        "active_workspace_count": len(eligible),
        "eligible_project_count": sum(len(scope.project_ids) for scope in scopes),
        "excluded_legacy_null_workspace_project_count": legacy_count,
        "target_database": args.target_database,
    }
    print(json.dumps(final, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
