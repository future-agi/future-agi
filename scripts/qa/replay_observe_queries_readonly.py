#!/usr/bin/env python3
"""Replay the local query layer against explicitly authorized read-only CH data.

This is NOT an HTTP/auth/UI test. Use the backend virtualenv. No schema startup,
ORM connections, Redis writes, task queues or migrations are permitted here.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from importlib import import_module
from contextlib import contextmanager, nullcontext
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import sys
import time
from unittest.mock import patch
from uuid import UUID, uuid4

import replay_observe_filters as replay

ROOT = Path(__file__).resolve().parents[2]


@contextmanager
def _timed_local_phase(phases, origin, name, **metadata):
    """Observe work in place; never exclude it from the candidate wall clock."""
    start, completed = time.monotonic(), False
    try:
        yield
        completed = True
    finally:
        end = time.monotonic()
        phases.append({
            "name": name, "start_ms": round((start - origin) * 1000, 2),
            "end_ms": round((end - origin) * 1000, 2),
            "elapsed_ms": round((end - start) * 1000, 2),
            "completed": completed, **metadata,
        })


def safe_json(value):
    if isinstance(value, (datetime, UUID, Decimal)):
        return value.isoformat() if isinstance(value, datetime) else str(value)
    if isinstance(value, dict):
        return {str(k): safe_json(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [safe_json(v) for v in value]
    return value


def source_fingerprint():
    result = hashlib.sha256()
    paths = list((ROOT / "futureagi" / "tracer").rglob("*.py"))
    # Legacy transport/resource policy also lives in tfc/utils, not only settings.
    # Bind evidence to every replay/oracle helper so a changed validator cannot
    # silently reuse a previous run's source identity.
    paths += list((ROOT / "futureagi" / "tfc").rglob("*.py"))
    # Exact preview/eval context is shared with model_hub. A passing query
    # ledger must not retain its identity after those application paths change.
    paths += list((ROOT / "futureagi" / "model_hub").rglob("*.py"))
    paths += list((ROOT / "scripts" / "qa").glob("*.py"))
    # Runtime filter operators also come from JSON, not Python. Bind both
    # loader locations (including absence), so a missing/changed asset cannot
    # reuse evidence produced with a different normalization contract.
    contracts = (
        ROOT / "api_contracts" / "filter_contract.json",
        ROOT / "futureagi" / "tracer" / "contracts" / "filter_contract.json",
    )
    for path in contracts:
        result.update(str(path.relative_to(ROOT)).encode())
        result.update(b"present\0" + path.read_bytes() if path.exists() else b"missing\0")
    for path in sorted(paths):
        result.update(str(path.relative_to(ROOT)).encode())
        result.update(path.read_bytes())
    return result.hexdigest()


def initialize_candidate():
    sys.path.insert(0, str(ROOT / "futureagi"))
    os.environ.update(
        DJANGO_SETTINGS_MODULE="tfc.settings.test",
        FI_SKIP_CH25_SCHEMA_APPLY="1",
        CH_ENABLED="false",
        CH_PORT="19999",
        CH25_TCP_PORT="19999",
        CH25_HTTP_PORT="19999",
        REDIS_URL="redis://127.0.0.1:19998/0",
        REDIS_LOCK_URL="redis://127.0.0.1:19998/2",
    )
    import django
    from django.conf import settings

    settings.DATABASES = {"default": {"ENGINE": "django.db.backends.dummy"}}
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    }
    settings.CELERY_TASK_ALWAYS_EAGER = False
    logging.disable(logging.CRITICAL)
    django.setup()


def candidate_runtime_profile():
    """Bind numeric policy and the allowed eval source, never credentials."""
    from django.conf import settings

    table = settings.CH25_EVAL_LOGGER_TABLE
    if table not in ("tracer_eval_logger", "tracer_eval_logger_v2"):
        raise replay.ReplayError("UNSUPPORTED_EVAL_SOURCE")
    profile = {
        key: getattr(settings, key)
        for key in dir(settings)
        if key.startswith(
            (
                "FILTER_SELECTOR_",
                "EXACT_GRAPH_",
                "USER_LIST_",
                "DASHBOARD_",
                "CLICKHOUSE_APPLICATION_READ_",
            )
        )
        and type(getattr(settings, key)) in (bool, int, float)
    }
    profile["CH25_EVAL_LOGGER_TABLE"] = table
    return profile


def qualification_summary(plan, rows, fingerprint):
    """Account for the entire plan, not just successful or selected requests.

    First-page identity checks do not prove full rows, pagination or UI.
    Old-source results and duplicate attempts are never additional coverage.
    """
    current, historical = {}, 0
    known = {case["id"] for case in plan["cases"]}
    for row in rows:
        if row.get("plan_id") != plan["plan_id"] or row.get("case_id") not in known:
            raise replay.ReplayError("COVERAGE_LEDGER_PLAN_MISMATCH")
        if row.get("source_sha256") != fingerprint:
            historical += 1
            continue
        current[row["case_id"]] = row
    groups = defaultdict(Counter)
    families = defaultdict(Counter)
    attrs = defaultdict(Counter)
    operators = defaultdict(Counter)
    totals = Counter()
    for case in plan["cases"]:
        row = current.get(case["id"])
        status = (
            "BLOCKED_INPUT"
            if case["blocked"]
            else row.get("status", "UNTESTED")
            if row
            else "UNTESTED"
        )
        counters = [totals, groups[f"{case['surface']}/{case['period']}"]]
        counters += [
            families[family]
            for family in case.get("property_families", ["SPAN_ATTRIBUTE"])
        ]
        counters += [attrs[name] for name in case["attributes"]]
        # Count a case once per family/type/operator, including combinations.
        # These counts are coverage, not a proof that each leaf independently
        # influenced the result. Untested cases retain their full denominator.
        request = case.get("request") or {}
        leaves = (request.get("body") or {}).get("filters")
        if leaves is None:
            leaves = (request.get("params") or {}).get("filters", "[]")
        if isinstance(leaves, str):
            leaves = json.loads(leaves)
        op_keys = set()
        for leaf in leaves:
            cfg = leaf.get("filter_config", {})
            if (
                leaf.get("column_id") == "created_at"
                and cfg.get("col_type") == "SYSTEM_METRIC"
                and cfg.get("filter_op") == "between"
                and cfg.get("filter_value")
                == [case["window"]["start"], case["window"]["end"]]
            ):
                continue
            op_keys.add(
                "/".join(
                    (
                        cfg.get("col_type", "NORMAL"),
                        cfg.get("filter_type", "UNKNOWN"),
                        cfg.get("filter_op", "UNKNOWN"),
                    )
                )
            )
        if not op_keys:
            # No real typed seed/request must not disappear as zero coverage.
            op_keys.add("BLOCKED_OR_UNRESOLVED/" + case.get("variant", "UNKNOWN"))
        counters += [operators[key] for key in op_keys]
        for counts in counters:
            counts["planned"] += 1
            counts[status] += 1
            if row and status != "BLOCKED_INPUT":
                counts["executed"] += 1
                if row.get("latency_met"):
                    counts["latency_met"] += 1
                reference = row.get("independent_reference", {})
                if reference.get("status") == "ID_ORDER_MATCH":
                    counts["identity_order_verified"] += 1
                elif reference.get("status") == "ID_ORDER_MISMATCH":
                    counts["identity_order_mismatch"] += 1
    return {
        "plan_id": plan["plan_id"],
        "source_sha256": fingerprint,
        "totals": dict(totals),
        "by_surface_period": {
            key: dict(value) for key, value in sorted(groups.items())
        },
        "by_attribute": {key: dict(value) for key, value in sorted(attrs.items())},
        "by_property_type_operator": {
            key: dict(value) for key, value in sorted(operators.items())
        },
        "by_property_family": {
            family: dict(families[family])
            for family in sorted(set(families) | set(replay.REQUIRED_PROPERTY_FAMILIES))
        },
        "property_family_inventory": plan.get("property_family_inventory", {}),
        "property_family_coverage_complete": False,
        "attributes_planned": len(attrs),
        "attributes_executed": sum(bool(value["executed"]) for value in attrs.values()),
        "historical_source_attempts_excluded": historical,
        "http_e2e": False,
        "ui_e2e": False,
        "full_row_independent_oracle": False,
        "qualification": "NOT_QUALIFIED",
    }


def validate_select(query, params, allowed_project_ids):
    # Reject writes, multiple statements, table functions and inline safety overrides.
    sql = query.strip().rstrip(";")
    if not re.match(r"^(SELECT|WITH)\b", sql, re.I) or ";" in sql:
        raise replay.ReplayError("SELECT_ONLY")
    if re.search(
        r"\b(INSERT|ALTER|DELETE|DROP|TRUNCATE|CREATE|OPTIMIZE|ATTACH|DETACH|GRANT|REVOKE|INTO\s+OUTFILE)\b",
        sql,
        re.I,
    ):
        raise replay.ReplayError("SELECT_ONLY")
    if re.search(
        r"\b(url|s3|file|remote|remoteSecure|mysql|postgresql|jdbc|executable)\s*\(",
        sql,
        re.I,
    ):
        raise replay.ReplayError("EXTERNAL_TABLE_FUNCTION_FORBIDDEN")
    if re.search(
        r"\b(readonly|max_execution_time|max_bytes_to_read|max_memory_usage|(?:read|result|timeout)_overflow_mode)\s*=",
        sql,
        re.I,
    ):
        raise replay.ReplayError("INLINE_SAFETY_SETTING_OVERRIDE")
    # The application also uses pid/attr_pid/etc. Discover project bindings
    # from their actual equality/IN predicate, not from a naming convention.
    bindings = set(
        re.findall(
            r"\bproject_id\s*(?:=|IN\b)\s*(?:toUUID\s*\(\s*)?%\((\w+)\)s", sql, re.I
        )
    )
    referenced = set(re.findall(r"%\((\w+)\)s", sql))
    projects = []
    for key, value in params.items():
        if key in bindings or (
            key in referenced and key.endswith(("project_id", "project_ids"))
        ):
            projects.extend(value if isinstance(value, (tuple, list)) else [value])
    if (
        not bindings
        or not projects
        or not set(map(str, projects)) <= set(allowed_project_ids)
    ):
        raise replay.ReplayError("QUERY_PROJECT_SCOPE_NOT_AUTHORIZED")
    if not re.search(r"\bproject_id\b", sql, re.I):
        raise replay.ReplayError("QUERY_HAS_NO_PROJECT_SCOPE")
    return sql


def diagnostic_read_settings(
    requested_settings,
    *,
    remaining_ms,
    args,
    preserve_caller_caps=False,
    timeout_ms=None,
):
    """Install run guards; independent diagnostics may request stricter limits."""
    limits = dict(requested_settings or {})

    def positive_ceiling(name, ceiling):
        requested = int(limits.get(name, 0) or 0)
        return ceiling if requested <= 0 else min(requested, ceiling)

    limits.update(
        readonly=2,
        max_execution_time=remaining_ms / 1000,
        max_execution_time_leaf=remaining_ms / 1000,
        max_threads=positive_ceiling("max_threads", args.threads),
        max_memory_usage=positive_ceiling("max_memory_usage", 4 * 1024**3),
        max_bytes_to_read=args.read_gib * 1024**3,
        max_bytes_to_read_leaf=args.read_gib * 1024**3,
        max_result_rows=100001,
        max_result_bytes=32 * 1024**2,
        read_overflow_mode="throw",
        result_overflow_mode="throw",
        timeout_overflow_mode="throw",
        use_query_cache=0,
    )
    if preserve_caller_caps:
        # Reference probes are diagnostics, not application reads. Preserve
        # their narrower caps, including fractional seconds, without granting
        # more than the remaining run wall or its outer row/byte envelope.
        requested_settings = requested_settings or {}
        for name in (
            "max_execution_time",
            "max_execution_time_leaf",
            "max_bytes_to_read",
            "max_bytes_to_read_leaf",
            "max_result_rows",
            "max_result_bytes",
        ):
            coerce = float if name.startswith("max_execution_time") else int
            requested = coerce(requested_settings.get(name, 0) or 0)
            if not math.isfinite(requested) or requested < 0:
                raise ValueError(f"invalid diagnostic limit: {name}")
            if requested > 0:
                limits[name] = min(limits[name], requested)
        if timeout_ms is not None:
            requested_seconds = float(timeout_ms) / 1000
            if not math.isfinite(requested_seconds) or requested_seconds <= 0:
                raise ValueError("diagnostic timeout_ms must be finite and positive")
            limits["max_execution_time"] = min(
                limits["max_execution_time"], requested_seconds
            )
        limits["max_execution_time_leaf"] = min(
            limits["max_execution_time_leaf"], limits["max_execution_time"]
        )
        # Distinct row/byte caps survive the dict copy above. Never accept a
        # partial DISTINCT population as an independent absence proof.
        limits["distinct_overflow_mode"] = "throw"
    return limits


_USERS_ORIGIN_SHA = "20d339691652b7d0120ae3b6b8d1dfaf2a859c778688f19cce0e74e66e944f1f"
_USERS_REMAP_SHA = "090df268267944b22e713077c59d4836e4046fadb60bfbb78116f3a43af46676"
_USERS_SOURCE_PINS = {
    "tracer.services.users_list_manager": "b5da3657a94ab71710a8db384990e018269929e80c2f651cf8a25b02df3eb831",
    "tracer.services.clickhouse.query_builders.user_list": "97c47542622bb599cb003d2e7c5dfcc6bcba0989aaef41e8c3461ed6d6435ba8",
    "tracer.services.clickhouse.v2.query_builders.user_list": "d5024fe5a46b7cbdf2621d04dfd02027c17816f7250f84120c920a0dd3c9908e",
    "tracer.services.clickhouse.v2.id_remap_sql": "56903f382c0f8dc40099e5ebfda45a8ab853c0b8f7ec16b5712f9c11092fe24a",
}


def _users_sources_current():
    return all(hashlib.sha256(Path(import_module(name).__file__).read_bytes()).hexdigest() == digest
               for name, digest in _USERS_SOURCE_PINS.items())


def _user_uuid(value):
    if not isinstance(value, (str, UUID)):
        raise replay.ReplayError("USERS_REMAP_INVALID_UUID")
    try:
        result = UUID(str(value))
    except ValueError:
        raise replay.ReplayError("USERS_REMAP_INVALID_UUID") from None
    if not result.int or str(result) != str(value):
        raise replay.ReplayError("USERS_REMAP_INVALID_UUID")
    return str(result)


@dataclass(frozen=True)
class _UsersRemapContext:
    projects: tuple[str, ...]
    authorized_projects: tuple[str, ...]
    origin_bindings: str
    binding: str


@dataclass(frozen=True)
class _UsersRemapCertificate:
    owner: object
    client: object
    context: _UsersRemapContext
    origin_query_id: str
    ids: tuple[str, ...]


class ReadOnlyExecutor:
    @property
    def supports_bounded_speculative_reads(self):
        # Candidate mode strips caller time/scan caps, just like the app.
        # The outer diagnostic envelope is not a probe-specific guarantee.
        return self.mode == "reference_diagnostic"

    def __init__(self, args, projects, deadline, *, mode="candidate"):
        if mode not in {"candidate", "reference_diagnostic"}:
            raise ValueError("unknown read policy mode")
        from clickhouse_driver import Client

        self.args = args
        self.projects = projects
        self.deadline = deadline
        self.mode = mode
        self.calls = []
        self._users_context = self._users_certificate = None
        self._users_origin_expected = False
        self.prefix = "observe-local-replay-" + uuid4().hex[:12]
        self.client = Client(
            args.host,
            port=args.port,
            database=args.database,
            user=os.environ.get("OBSERVE_CH_USER", "default"),
            password=os.environ.get("OBSERVE_CH_PASSWORD", ""),
            connect_timeout=3,
            send_receive_timeout=args.safety_seconds + 3,
            compression="lz4",
        )

    def remaining_read_ms(self):
        return max(0, int((self.deadline - time.monotonic()) * 1000))

    def _configure_users_remap(self, manager, case, scope, plan_id):
        from tracer.services.clickhouse.v2.query_builders.user_list import UserListQueryBuilderV2

        self._users_context = self._users_certificate = None
        self._users_origin_expected = False
        request, params = case["request"], case["request"]["params"]
        project = params.get("project_id")
        projects = (str(project),) if project else tuple(map(str, self.projects))
        valid_surface = ((case["surface"] == "users_project" and project == scope["project_id"])
                         or (case["surface"] == "users_workspace" and project is None))
        if (self.mode != "candidate" or not valid_surface or self.calls
                or request["method"] != "GET" or request["path"] != "/tracer/users/"
                or request["target_rows"] != 25 or params.get("page_size") != 25
                or params.get("cursor_mode") is not True or params.get("cursor")
                or manager.organization_id != scope["organization_id"]
                or tuple(manager.scoped_project_ids) != projects
                or not projects or len(set(projects)) != len(projects)
                or not set(projects) <= set(self.projects)
                or replay.digest(safe_json(manager.filters)) != replay.digest(safe_json(normalize_filters(request)))
                or not _users_sources_current()):
            raise replay.ReplayError("USERS_REMAP_CONTEXT_NOT_QUALIFIED")
        builder = UserListQueryBuilderV2(organization_id=manager.organization_id,
                                       project_ids=list(projects), filters=manager.filters,
                                       search=manager.search, empty_scope=manager.empty_scope)
        sql, bindings = builder.build_dimension_candidate_query(
            limit=26, window_start=replay.utc(case["window"]["start"]),
            window_end=replay.utc(case["window"]["end"]),
        )
        if hashlib.sha256(sql.strip().rstrip(";").encode()).hexdigest() != _USERS_ORIGIN_SHA:
            raise replay.ReplayError("USERS_REMAP_ORIGIN_NOT_QUALIFIED")
        self._users_context = _UsersRemapContext(
            tuple(_user_uuid(p) for p in projects), tuple(map(str, self.projects)),
            replay.digest(safe_json(bindings)),
            replay.digest({"case": case, "scope": scope, "plan_id": plan_id, "projects": projects}),
        )
        self._users_origin_expected = True

    def _validate_users_remap(self, query, params, certificate):
        if (self.mode != "candidate" or certificate is None
                or certificate.owner is not self or certificate.client is not self.client
                or certificate.context is not self._users_context
                or not self.calls or self.calls[-1]["query_id"] != certificate.origin_query_id
                or hashlib.sha256(query.encode()).hexdigest() != _USERS_REMAP_SHA
                or set(params) != {"dimension_candidate_ids"}
                or type(params["dimension_candidate_ids"]) is not tuple
                or any(type(value) is not str for value in params["dimension_candidate_ids"])
                or params["dimension_candidate_ids"] != certificate.ids):
            raise replay.ReplayError("QUERY_PROJECT_SCOPE_NOT_AUTHORIZED")

    def _users_result(self, result, *, origin, certificate, query_id):
        if origin:
            if (result.row_count != len(result.data) or result.row_count > 26
                    or len(result.columns) != len(set(result.columns))):
                raise replay.ReplayError("USERS_REMAP_ORIGIN_RESULT_INVALID")
            try:
                ids = tuple(_user_uuid(row["end_user_id"]) for row in result.data)
                valid = all(_user_uuid(row["project_id"]) in self._users_context.projects for row in result.data)
            except (KeyError, TypeError):
                raise replay.ReplayError("USERS_REMAP_ORIGIN_RESULT_INVALID") from None
            if not valid or len(ids) != len(set(ids)):
                raise replay.ReplayError("USERS_REMAP_ORIGIN_RESULT_INVALID")
            if ids:
                self._users_certificate = _UsersRemapCertificate(self, self.client, self._users_context, query_id, ids)
        elif certificate is not None:
            if result.columns != ["any_id", "survivor_id"]:
                raise replay.ReplayError("USERS_REMAP_RESULT_INVALID")
            seen = set()
            for row in result.data:
                if any(type(row.get(key)) is not str for key in result.columns):
                    raise replay.ReplayError("USERS_REMAP_RESULT_INVALID")
                alias, survivor = _user_uuid(row["any_id"]), _user_uuid(row["survivor_id"])
                if alias in seen or survivor not in certificate.ids:
                    raise replay.ReplayError("USERS_REMAP_RESULT_INVALID")
                seen.add(alias)

    def execute_ch_query(self, query, params=None, timeout_ms=None, settings=None):
        from clickhouse_driver.errors import Error
        from tracer.services.clickhouse.query_service import QueryResult
        from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded

        params = params or {}
        pending, self._users_certificate = self._users_certificate, None
        origin, self._users_origin_expected = self._users_origin_expected, False
        if self._users_context is not None:
            if not _users_sources_current():
                raise replay.ReplayError("USERS_REMAP_SOURCE_CHANGED")
            if self.mode != "candidate" or tuple(map(str, self.projects)) != self._users_context.authorized_projects:
                raise replay.ReplayError("USERS_REMAP_CONTEXT_CHANGED")
        certified = None
        try:
            sql = validate_select(query, params, self.projects)
        except replay.ReplayError as exc:
            if str(exc) != "QUERY_PROJECT_SCOPE_NOT_AUTHORIZED":
                raise
            self._validate_users_remap(query, params, pending)
            sql, certified = query, pending
        if origin and (hashlib.sha256(sql.encode()).hexdigest() != _USERS_ORIGIN_SHA
                       or replay.digest(safe_json(params)) != self._users_context.origin_bindings):
            raise replay.ReplayError("USERS_REMAP_ORIGIN_BINDINGS_CHANGED")
        remaining = self.remaining_read_ms()
        if remaining <= 0:
            raise ReadDeadlineExceeded("diagnostic_safety_wall")
        candidate_settings = None
        if self.mode == "candidate":
            from tracer.services.clickhouse.application_read_policy import (
                application_read_settings,
            )

            # Candidate qualification still exercises application policy first,
            # followed by separate, explicit run-only diagnostic safeguards.
            candidate_settings = application_read_settings(settings)
        limits = diagnostic_read_settings(
            candidate_settings if self.mode == "candidate" else settings,
            remaining_ms=remaining,
            args=self.args,
            preserve_caller_caps=self.mode == "reference_diagnostic",
            timeout_ms=timeout_ms,
        )
        query_id = f"{self.prefix}-{len(self.calls) + 1}"
        record = {
            "query_id": query_id,
            "sql_sha256": hashlib.sha256(sql.encode()).hexdigest(),
            "application_read_settings": candidate_settings,
            "read_policy_mode": self.mode,
            "diagnostic_guards_applied": True,
            "limits": limits,
        }
        if certified is not None:
            record["scope_certificate"] = {
                "kind": "finite_users_remap_certificate.v1",
                "origin_query_id": certified.origin_query_id,
                "origin_sql_sha256": _USERS_ORIGIN_SHA,
                "source_sha256": replay.digest(_USERS_SOURCE_PINS),
                "scope_binding_sha256": certified.context.binding,
                "candidate_count": len(certified.ids), "candidate_ids_sha256": replay.digest(certified.ids),
                "result_validated": False,
            }
        self.calls.append(record)
        start = time.monotonic()
        try:
            rows, cols = self.client.execute(
                sql, params, settings=limits, query_id=query_id, with_column_types=True
            )
            progress = self.client.last_query.progress
            record.update(
                read_rows=progress.rows,
                read_bytes=progress.bytes,
                server_elapsed_ms=round(
                    getattr(progress, "elapsed_ns", 0) / 1_000_000, 2
                ),
                result_rows=len(rows),
                elapsed_ms=round((time.monotonic() - start) * 1000, 2),
            )
            result = QueryResult.from_clickhouse_rows(rows, cols, record["elapsed_ms"])
            if origin or certified is not None:
                if any(len(row) != len(cols) for row in rows):
                    raise replay.ReplayError("USERS_REMAP_RESULT_INVALID")
                self._users_result(result, origin=origin, certificate=certified, query_id=query_id)
                if certified is not None:
                    record["scope_certificate"]["result_validated"] = True
            return result
        except Error as exc:
            record.update(
                error_code=exc.code,
                exception_class=type(exc).__name__,
                elapsed_ms=round((time.monotonic() - start) * 1000, 2),
            )
            raise
        finally:
            timing_origin = getattr(self, "timing_origin", None)
            if timing_origin is not None:
                offset = (start - timing_origin) * 1000
                record["candidate_client_start_ms"] = round(offset, 2)
                if "elapsed_ms" in record:
                    # Same existing execute timer; excludes later QueryResult conversion.
                    record["candidate_client_end_ms"] = round(offset + record["elapsed_ms"], 2)

    def close(self):
        self._users_context = self._users_certificate = None
        self._users_origin_expected = False
        self.client.disconnect()


def validate_preview_workload(case):
    """Refuse historical under-sized plans rather than relabel their timings.

    These are public Observe list proxies used by preview components, NOT
    workflow selection, detail/variable mapping or evaluation execution.
    Old one-row Eval ledgers remain valid historical smaller-workload evidence.
    """
    surface = case["surface"]
    expected = (
        1
        if surface.startswith("task_")
        else 50
        if surface.startswith("eval_")
        else None
    )
    if expected is None or case.get("blocked"):
        return
    request = case.get("request") or {}
    params = request.get("params") or {}
    if (
        request.get("method") != "GET"
        or request.get("path") != replay.LISTS.get(surface)
        or request.get("target_rows") != expected
        or params.get("page_size") != expected
        or params.get("cursor_mode") is not True
    ):
        raise replay.ReplayError("PREVIEW_WORKLOAD_MISMATCH_REGENERATE_PLAN")


def normalize_filters(req):
    from tracer.serializers.filters import FilterItemField

    source = (
        req.get("body", {}).get("filters", [])
        if req.get("body")
        else json.loads(req["params"]["filters"])
    )
    field = FilterItemField()
    return [field.run_validation(item) for item in source]


def collect_selector_page(read_page, builder, key_field, target_rows, remaining_ms):
    """Follow the real selector's exact checkpoints, never restart page zero.

    This models query-layer visible-page buffering, not signed HTTP cursor
    validation. It never shortens the fixed window or changes transport size.
    """
    rows, seen, checkpoints, phases = [], set(), set(), []
    state, page = {}, None
    for attempt in range(100):
        if remaining_ms() <= 0:
            break
        page = read_page(state)
        phases.extend(
            {
                "transport_page": attempt + 1,
                "kind": a.kind,
                "elapsed_ms": round(a.elapsed_ms, 2),
                "rows": a.rows_returned,
                "error_code": a.error_code,
            }
            for a in page.attempts
        )
        if len(page.rows) > target_rows:
            raise replay.ReplayError("SELECTOR_TRANSPORT_PAGE_OVERSIZED")
        identity = getattr(
            builder, "bounded_filter_row_identity", lambda r: str(r[key_field])
        )
        order = getattr(
            builder, "bounded_filter_row_order_token", lambda r: str(r[key_field])
        )
        for row in page.rows:
            rid = identity(row)
            if rid in seen:
                raise replay.ReplayError(
                    "DUPLICATE_IDENTITY_ACROSS_SELECTOR_CONTINUATIONS"
                )
            seen.add(rid)
            rows.append(row)
        if len(rows) >= target_rows or (page.complete and not page.has_more):
            break
        if page.rows:
            state.update(
                cursor_start_time=page.rows[-1]["start_time"],
                cursor_order_token=order(page.rows[-1]),
            )
        if not page.complete and page.continuation_slice_end is None:
            break  # No proven checkpoint: no restart and no false empty result.
        state.update(
            continuation_slice_start=page.continuation_slice_start,
            continuation_slice_end=page.continuation_slice_end,
            continuation_before_start_time=page.continuation_before_start_time,
            continuation_before_id=page.continuation_before_id,
        )
        fingerprint = replay.digest(safe_json(state))
        if fingerprint in checkpoints:
            raise replay.ReplayError("SELECTOR_CHECKPOINT_DID_NOT_ADVANCE")
        checkpoints.add(fingerprint)
    complete = bool(
        page and (len(rows) >= target_rows or (page.complete and not page.has_more))
    )
    return {
        "table": rows[:target_rows],
        "query_complete": complete,
        "query_status": "complete" if complete else "degraded",
        "query_error_code": None
        if complete
        else (page.error_code if page else "diagnostic_safety_wall"),
        "query_exact": True,
        "selector_query_layer_only": True,
        "selector_phases": phases,
        "scan_checkpoint": safe_json(state),
        "has_more": bool(
            len(rows) > target_rows or (page and page.has_more) or not complete
        ),
        "buffered_overflow_rows": len(rows[target_rows:]),
        "buffered_overflow_sha256": replay.digest(safe_json(rows[target_rows:])),
        "transport_pages": attempt + 1 if page else 0,
    }


class CandidateQueries:
    def __init__(self, args, plan, projects):
        self.args, self.plan, self.projects = args, plan, projects
        self.metadata = None
        if getattr(args, "relational_metadata", None):
            from observe_relational_metadata import SnapshotMetadata

            self.metadata = SnapshotMetadata(
                replay.read_json(args.relational_metadata), plan["scope"], projects
            )

    def run(self, case):
        start = time.monotonic()
        local_phases = []
        with _timed_local_phase(local_phases, start, "reader_initialization"):
            reader = ReadOnlyExecutor(
                self.args, self.projects, start + self.args.safety_seconds, mode="candidate"
            )
        reader.timing_origin = start
        reader.local_phase = lambda name, **metadata: _timed_local_phase(
            local_phases, start, name, **metadata
        )
        row = {
            "started_at_utc": datetime.now(timezone.utc).isoformat(),
            "case_id": case["id"],
            "case_variant": case.get("variant"),
            "attributes": case.get("attributes"),
            "property_families": case.get("property_families", ["SPAN_ATTRIBUTE"]),
            "window": case.get("window"),
            "surface": case["surface"],
            "period": case["period"],
            "target_ms": case["target_ms"],
            "layer": "local_candidate_query_path",
            "read_policy_mode": "candidate",
            "http_e2e": False,
            "ui_e2e": False,
            "correctness": "UNVERIFIED",
            "complete": False,
            "sampled": False,
        }
        if case["blocked"]:
            reader.close()
            return {
                **row,
                "status": "BLOCKED_INPUT",
                "reason": case["blocked"],
                "elapsed_ms": 0,
                "queries": [],
            }
        try:
            req = case["request"]
            filters = normalize_filters(req)
            surface = case["surface"]
            with self.metadata.metadata_io() if self.metadata else nullcontext():
                if surface in replay.GRAPHS:
                    payload = self.graph(reader, case, filters)
                elif surface.startswith("dashboard_"):
                    payload = self.dashboard(reader, case)
                elif surface.startswith("users_"):
                    payload = self.users(reader, case, filters)
                else:
                    payload = self.entity_list(reader, case, filters)
            complete = payload.get("query_complete") is True
            row.update(
                complete=complete,
                result_sha256=replay.digest(
                    safe_json(payload.get("table", payload.get("data", [])))
                ),
                result_hash_schema="query-data-only.v1",
                result_rows=len(payload.get("table", payload.get("data", []))),
                server_status=payload.get("query_status"),
                query_exact=payload.get("query_exact"),
                ordering_exact=payload.get("ordering_exact"),
                approximate_fields=payload.get("approximate_fields"),
                status="COMPLETE_UNVERIFIED" if complete else "INCOMPLETE",
                reason=payload.get("query_error_code"),
                selector_phases=payload.get("selector_phases"),
                scan_checkpoint=payload.get("scan_checkpoint"),
                transport_pages=payload.get("transport_pages"),
                buffered_overflow_rows=payload.get("buffered_overflow_rows"),
                query_layer_coverage=payload.get("query_layer_coverage"),
                hydration_phases=payload.get("hydration_phases"),
            )
            if surface.startswith("dashboard_"):
                row.update(
                    result_rows=sum(len(rows) for rows in payload["data"]),
                    statement_count=len(payload["data"]),
                )
            if (
                payload.get("query_sampled")
                or payload.get("query_exact") is False
                or payload.get("ordering_exact") is False
                or payload.get("approximate_fields")
            ):
                # Completion, correctness and performance are independent.
                # Do not turn a finished inexact response into a fake timeout.
                row.update(status="INEXACT")
        except Exception as exc:
            # Never emit SQL, raw values or exception text from production queries.
            row.update(
                status="ERROR",
                exception_class=type(exc).__name__,
                error_code=getattr(exc, "code", None),
            )
            if isinstance(exc, replay.ReplayError):
                row["reason"] = str(exc)
        finally:
            reader.close()
        row["elapsed_ms"] = round((time.monotonic() - start) * 1000, 2)
        row["local_timing_phases"] = local_phases
        row["latency_met"] = row["complete"] and row["elapsed_ms"] <= row["target_ms"]
        row["queries"] = reader.calls
        if time.monotonic() >= reader.deadline:
            row["diagnostic_safety_stop"] = True
            if row["status"] in ("ERROR", "INCOMPLETE"):
                row["status"] = "SAFETY_STOP"
        if (
            row["complete"]
            and (
                case["surface"] in {"traces", "task_traces", "eval_traces"}
                or (case["surface"] in {"spans", "task_spans", "eval_spans"})
                or case["surface"]
                in {"sessions", "task_sessions", "eval_sessions", "user_sessions"}
            )
            and self.args.verify_trace_ids
        ):
            from observe_trace_id_reference import reference_ids

            reference_start = time.monotonic()
            reference_reader = ReadOnlyExecutor(
                self.args,
                self.projects,
                reference_start + self.args.safety_seconds,
                mode="reference_diagnostic",
            )
            evidence = {
                "kind": "independent_scalar_trace_ID_order",
                "full_row_metrics_verified": False,
                "same_transaction_snapshot": False,
                "read_policy_mode": "reference_diagnostic",
            }
            try:
                if case["surface"] in {
                    "sessions",
                    "task_sessions",
                    "eval_sessions",
                    "user_sessions",
                }:
                    from observe_session_reference import reference_session_pages

                    evidence["kind"] = "independent_scalar_session_ID_order"
                    reference = reference_session_pages(
                        reference_reader,
                        project_ids=[self.plan["scope"]["project_id"]],
                        authorized_project_ids=self.projects,
                        start=case["window"]["start"],
                        end=case["window"]["end"],
                        filters=filters,
                        page_size=case["request"]["target_rows"],
                        order_mode=payload.get("session_order_mode"),
                    )
                    expected = [str(r["session_id"]) for r in reference["pages"][0]]
                    actual = [str(r["session_id"]) for r in payload["table"]]
                    info = {
                        "contract": reference["contract"],
                        "order_mode": reference["order_mode"],
                        "candidate_pages_verified": 1,
                        "pagination_verified": False,
                        "full_row_metrics_verified": False,
                    }
                elif (
                    "ANNOTATION" in case.get("property_families", []) and self.metadata
                ):
                    from observe_annotation_reference import (
                        reference_ids as annotation_reference_ids,
                        reference_span_ids,
                        span_identity,
                    )

                    is_span = case["surface"] in {"spans", "task_spans", "eval_spans"}
                    evidence["kind"] = (
                        "independent_PG_Score_span_identity_order"
                        if is_span
                        else "independent_PG_Score_trace_ID_order"
                    )
                    expected, info = (
                        reference_span_ids if is_span else annotation_reference_ids
                    )(
                        reference_reader,
                        case,
                        self.plan["scope"],
                        filters,
                        self.metadata.document,
                    )
                    actual = (
                        [span_identity(r) for r in payload["table"]]
                        if is_span
                        else [str(r["trace_id"]) for r in payload["table"]]
                    )
                else:
                    if case["surface"] in {"spans", "task_spans", "eval_spans"}:
                        from observe_span_reference import (
                            reference_span_ids,
                            span_identity,
                        )

                        evidence["kind"] = "independent_scalar_span_identity_order"
                        expected, info = reference_span_ids(
                            reference_reader, case, self.plan["scope"], filters
                        )
                        actual = [span_identity(r) for r in payload["table"]]
                    else:
                        expected, info = reference_ids(
                            reference_reader, case, self.plan["scope"], filters
                        )
                        actual = [str(r["trace_id"]) for r in payload["table"]]
                evidence.update(
                    info,
                    expected_id_order_sha256=replay.digest(expected),
                    actual_id_order_sha256=replay.digest(actual),
                    status="ID_ORDER_MATCH"
                    if actual == expected
                    else "ID_ORDER_MISMATCH",
                )
                if (
                    getattr(self.args, "verify_trace_full_rows", False)
                    and case["surface"] in {"traces", "task_traces", "eval_traces"}
                ):
                    from observe_trace_fullrow_reference import verify_trace_page

                    # Reuse independently selected IDs; never candidate root keys.
                    # Candidate timing/reader are already finalized above. A field
                    # failure stays nested and cannot erase the ID-order evidence.
                    evidence["query_layer_fields"] = verify_trace_page(
                        reference_reader, case, self.plan["scope"], payload, expected,
                        id_order_status=evidence["status"],
                    )
            except Exception as exc:
                evidence.update(
                    status="UNVERIFIED",
                    exception_class=type(exc).__name__,
                    error_code=getattr(exc, "code", None),
                )
                if isinstance(exc, replay.ReplayError):
                    evidence["reason"] = str(exc)
            finally:
                reference_reader.close()
            evidence["elapsed_ms"] = round(
                (time.monotonic() - reference_start) * 1000, 2
            )
            evidence["queries"] = reference_reader.calls
            row["independent_reference"] = evidence
        if (
            case["surface"].startswith("dashboard_")
            and row["complete"] and row["status"] == "COMPLETE_UNVERIFIED"
            and row.get("query_exact") is True
            and getattr(self.args, "verify_dashboard_aggregates", False)
        ):
            row["independent_reference"] = self.dashboard_reference(case, payload)
        return row

    def graph(self, reader, case, filters):
        from tracer.services.clickhouse import exact_graph_reads as graph

        kwargs = {
            "analytics": reader,
            "project_id": self.plan["scope"]["project_id"],
            "filters": filters,
            "interval": "day",
            "metric_id": "latency",
        }
        if case["surface"] == "users_graph":
            return graph.read_exact_user_system_graph(**kwargs)
        if case["surface"] == "session_graph":
            return graph.read_exact_session_system_graph(**kwargs)
        return graph.read_exact_system_graph(
            **kwargs,
            observe_type="span" if case["surface"] == "span_graph" else "trace",
        )

    def dashboard(self, reader, case):
        from tracer.serializers.dashboard import DashboardQuerySerializer
        from tracer.views.dashboard import (
            DashboardViewSet, _normalize_dashboard_query_filters,
        )
        from tracer.services.clickhouse.v2.query_builders.dashboard import (
            DashboardQueryBuilderV2,
        )

        serializer = DashboardQuerySerializer(data=case["request"]["body"])
        serializer.is_valid(raise_exception=True)
        config = _normalize_dashboard_query_filters(serializer.validated_data)
        config.update(
            organization_id=self.plan["scope"]["organization_id"],
            workspace_id=self.plan["scope"]["workspace_id"],
            require_versioned_snapshot=True,
            allow_sampled=False,
        )
        if self.metadata:
            config["annotation_label_ids_by_project"] = self.metadata.label_map(
                config["project_ids"]
            )
        builder = DashboardQueryBuilderV2(config)
        builder._latest_state_spans_required = True
        prepared = DashboardViewSet._prepare_metric_queries(builder)
        groups = builder.group_prepared_metric_queries(prepared)
        results, metric_rows = [], [None] * len(prepared)
        for indices, group in groups:
            sql, params = (group.sql, group.params) if group else prepared[indices[0]][1:]
            rows = reader.execute_ch_query(sql, params).data
            results.append(rows)
            if group:
                _, split_results = builder.metric_group_results(group, rows)
                for index, (_, values) in zip(indices, split_results, strict=True):
                    metric_rows[index] = values
            else:
                metric_rows[indices[0]] = rows
        group = groups[0][1] if len(groups) == 1 else None
        return {
            "query_complete": True,
            "query_status": "complete",
            "query_exact": True,
            "data": results,
            "dashboard_metric_rows": metric_rows,
            "dashboard_value_columns": group.value_columns if group else ("value",),
            "query_layer_only": True,
        }

    def dashboard_reference(self, case, payload):
        """Separate QA diagnostic; full 25.3 native barrier/row parity gate passed."""
        start, reader = time.monotonic(), None
        evidence = {
            "kind": "independent_scalar_dashboard_aggregate_rows",
            "status": "UNVERIFIED", "full_row_metrics_verified": False,
            "same_transaction_snapshot": False, "qualification": False,
            "read_policy_mode": "reference_diagnostic",
        }
        try:
            from observe_dashboard_reference import (
                build_dashboard_reference_query, compare_dashboard_rows,
            )

            plan = build_dashboard_reference_query(
                case["request"]["body"], authorized_project_ids=self.projects,
                enabled=getattr(self.args, "verify_dashboard_aggregates", False),
            )
            columns = payload.get("dashboard_value_columns", ())
            if len(payload["data"]) != 1 or len(columns) != len(plan.aggregations):
                raise replay.ReplayError("DASHBOARD_REFERENCE_UNSUPPORTED_RESULT_LAYOUT")
            reader = ReadOnlyExecutor(
                self.args, self.projects, start + self.args.safety_seconds,
                mode="reference_diagnostic",
            )
            try:
                reference = reader.execute_ch_query(plan.sql, plan.params, settings={}).data
                evidence.update(compare_dashboard_rows(
                    plan, payload["data"][0], reference, value_columns=columns,
                ))
            finally:
                reader.close()
        except Exception as exc:
            evidence.update(status="UNVERIFIED", full_row_metrics_verified=False,
                            exception_class=type(exc).__name__, error_code=getattr(exc, "code", None))
            if isinstance(exc, replay.ReplayError):
                evidence["reason"] = str(exc)
        evidence.update(
            elapsed_ms=round((time.monotonic() - start) * 1000, 2),
            queries=reader.calls if reader is not None else [],
        )
        return evidence

    def users(self, reader, case, filters):
        from tracer.services import users_list_manager as users

        scope = self.plan["scope"]
        manager = users.UsersListManager(
            organization_id=scope["organization_id"],
            allowed_project_ids=self.projects,
            project_id=scope["project_id"]
            if case["surface"] == "users_project"
            else None,
            filters=filters,
            requested_columns=json.loads(
                case["request"]["params"]["requested_columns"]
            ),
            attribute_keys=json.loads(
                case["request"]["params"].get("attribute_keys", "[]")
            ),
        )
        # Replace only the network adapter, never data, predicates, results or deadlines.
        if getattr(self.args, "verify_finite_users_remap", False):
            reader._configure_users_remap(manager, case, scope, self.plan["plan_id"])
        with patch.object(users, "V2AnalyticsQueryService", return_value=reader):
            read = manager.list_cursor_payload(page_size=case["request"]["target_rows"])
        return read.payload

    def entity_list(self, reader, case, filters):
        from django.conf import settings
        from tracer.selectors.trace_filter_reads import read_bounded_filter_page
        from tracer.services.clickhouse.v2.query_builders.trace_list import (
            TraceListQueryBuilderV2,
        )
        from tracer.services.clickhouse.v2.query_builders.span_list import (
            SpanListQueryBuilderV2,
        )
        from tracer.services.clickhouse.v2.query_builders.session_list import (
            SessionListQueryBuilderV2,
        )
        from observe_candidate_hydration import hydrate_session_queries

        surface = case["surface"]
        entity = (
            "sessions"
            if "sessions" in surface
            else "spans"
            if "spans" in surface
            else "traces"
        )
        candidate_deadline = time.monotonic() + (
            settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS / 1000
        )

        def session_timeout_ms():
            from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded

            remaining = min(
                reader.remaining_read_ms(),
                int((candidate_deadline - time.monotonic()) * 1000),
            )
            if remaining <= 0:
                raise ReadDeadlineExceeded("candidate_session_request_wall")
            return remaining

        if entity == "sessions":
            filters = self.resolve_session_user_filters(
                reader, filters, session_timeout_ms
            )
        cls = {
            "traces": TraceListQueryBuilderV2,
            "spans": SpanListQueryBuilderV2,
            "sessions": SessionListQueryBuilderV2,
        }[entity]
        page_size = case["request"]["target_rows"]
        builder_kwargs = {
            "project_id": self.plan["scope"]["project_id"],
            "filters": filters,
            "page_number": 0,
            "page_size": page_size,
        }
        if entity != "traces":
            builder_kwargs["bounded_internal_scan"] = True
        if self.metadata:
            builder_kwargs.update(
                self.metadata.builder_kwargs(self.plan["scope"]["project_id"])
            )
        builder = cls(**builder_kwargs)
        if entity == "sessions" and not builder.prefers_bounded_filter_page() and builder.supports_candidate_cursor_page():
            sql, params = builder.build_candidate_cursor_page_query()
            candidates = reader.execute_ch_query(
                sql, params, timeout_ms=session_timeout_ms()
            ).data
            chosen = candidates[:page_size]
            return hydrate_session_queries(reader, builder, {
                "query_complete": True,
                "query_status": "complete",
                "query_exact": True,
                "session_order_mode": "uuid",
                "table": chosen,
                "has_more": len(candidates) > page_size,
            }, remaining_ms=session_timeout_ms)
        key = {"traces": "trace_id", "spans": "id", "sessions": "session_id"}[entity]

        def read_page(state):
            options = {}
            if entity == "sessions":
                options.update(
                    max_candidates=settings.SESSION_LIST_FILTER_MAX_CANDIDATES,
                    max_seed_attempts=settings.SESSION_LIST_FILTER_MAX_SEED_ATTEMPTS,
                    max_query_count=settings.SESSION_LIST_FILTER_MAX_QUERIES,
                    classify_batch_size=builder.recommended_filter_classify_batch_size(),
                )
            return read_bounded_filter_page(
                builder=builder,
                analytics=reader,
                filters=filters,
                key_field=key,
                page_number=0,
                page_size=page_size,
                # Candidate configuration, not the benchmark's performance target.
                deadline_ms=min(
                    reader.remaining_read_ms(),
                    settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS,
                ),
                include_incomplete_rows=True,
                bounded_continuation=True,
                carry_continuation_slice_width=entity == "traces",
                root_time_discovery=bool(
                    entity == "traces"
                    and not state.get("continuation_before_start_time")
                    and not state.get("continuation_before_id")
                ),
                **state,
                **options,
            )

        payload = collect_selector_page(
            read_page, builder, key, page_size, reader.remaining_read_ms
        )
        if entity == "sessions":
            payload["session_order_mode"] = "uuid_string"
            payload = hydrate_session_queries(
                reader, builder, payload, remaining_ms=session_timeout_ms
            )
        if entity in {"traces", "spans"}:
            from observe_candidate_hydration import hydrate_page

            payload = hydrate_page(
                reader, builder, payload, entity=entity, request=case["request"],
                timing=getattr(reader, "local_phase", None),
            )
        return payload

    def resolve_session_user_filters(self, reader, filters, remaining_ms):
        from types import SimpleNamespace
        from tracer.services.clickhouse.v2 import end_user_dict_reader as users
        from tracer.services.user_filter_capabilities import is_native_user_id_filter

        class NativeAdapter:
            def query(self, sql, parameters=None, settings=None):
                result = reader.execute_ch_query(sql, parameters, settings=settings)
                return SimpleNamespace(
                    result_rows=[tuple(r.values()) for r in result.data]
                )

        resolved = []
        for item in filters:
            if not is_native_user_id_filter(item):
                resolved.append(item)
                continue
            cfg = item["filter_config"]
            op = cfg["filter_op"]
            if op not in ("equals", "in"):
                raise replay.ReplayError("SESSION_USER_OPERATOR_REPLAY_NOT_IMPLEMENTED")
            values = cfg["filter_value"]
            values = values if isinstance(values, list) else [values]
            ids = []
            with (
                patch.object(users, "_get_client", return_value=NativeAdapter()),
                patch.object(users, "_reset_client"),
            ):
                for value in values:
                    ids.extend(
                        users.resolve_end_user_ids_by_user_id(
                            value,
                            project_id=self.plan["scope"]["project_id"],
                            organization_id=self.plan["scope"]["organization_id"],
                            timeout_ms=remaining_ms(),
                        )
                    )
            resolved.append(
                {
                    "column_id": "end_user_id",
                    "filter_config": {
                        "col_type": "SYSTEM_METRIC",
                        "filter_type": "text",
                        "filter_op": "in",
                        "filter_value": list(dict.fromkeys(ids))
                        or ["00000000-0000-0000-0000-000000000000"],
                    },
                }
            )
        return resolved


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production-read-only", action="store_true", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--expected-server", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--authorized-projects", required=True)
    parser.add_argument(
        "--relational-metadata",
        help="Scoped real PG metadata capture, not a query-result fixture",
    )
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--surface", choices=replay.SURFACES)
    parser.add_argument("--period", choices=("7D", "30D", "12M"))
    parser.add_argument("--attribute")
    parser.add_argument("--variant")
    parser.add_argument(
        "--case-ids", help="Private JSON list of predeclared plan case IDs"
    )
    parser.add_argument(
        "--summary", help="New private full-plan coverage report; never overwritten"
    )
    parser.add_argument("--max-cases", type=int, default=3)
    parser.add_argument("--safety-seconds", type=float, default=60)
    parser.add_argument("--run-seconds", type=float, default=300)
    parser.add_argument("--max-consecutive-failures", type=int, default=3)
    parser.add_argument(
        "--verify-trace-ids",
        "--verify-entity-ids",
        action="store_true",
        help="Independent FINAL ID/order reference for scalar traces/spans/sessions (including Tasks/Eval adapters) and annotation traces/spans; session relational/user predicates fail explicitly as unsupported; not full-row, pagination or detail verification",
    )
    parser.add_argument(
        "--verify-trace-full-rows", action="store_true", default=False,
        help="Opt-in independent FINAL fields for up to 100 selected trace roots (including 50-row Eval previews) plus all-history requested attributes; requires --verify-trace-ids; not HTTP/UI/PG/eval/user verification",
    )
    parser.add_argument(
        "--verify-dashboard-aggregates", action="store_true", default=False,
        help="Opt-in independent whole-window scalar dashboard aggregates; bounded QA only, unsupported shapes stay unverified, not HTTP/UI qualification",
    )
    parser.add_argument("--read-gib", type=int, choices=(8, 32), default=8)
    parser.add_argument(
        "--verify-finite-users-remap", action="store_true", default=False,
        help="Opt-in source-bound finite Users remap authorization; unsupported origins fail closed, not independent result qualification",
    )
    parser.add_argument("--threads", type=int, choices=(1, 2, 4, 8), default=2)
    args = parser.parse_args()
    if args.verify_trace_full_rows and not args.verify_trace_ids:
        parser.error("--verify-trace-full-rows requires --verify-trace-ids")
    if not 0 < args.safety_seconds <= 300 or not 0 < args.max_cases <= 100000:
        raise replay.ReplayError("INVALID_DIAGNOSTIC_SAFETY_BUDGET")
    if not 0 < args.run_seconds <= 3600 or not 1 <= args.max_consecutive_failures <= 10:
        raise replay.ReplayError("INVALID_RUN_SAFETY_BUDGET")
    plan = replay.read_json(args.plan)
    if plan["plan_id"] != replay.digest(
        {k: v for k, v in plan.items() if k != "plan_id"}
    ):
        raise replay.ReplayError("PLAN_CHANGED")
    projects = replay.read_json(args.authorized_projects)
    if not isinstance(projects, list) or plan["scope"]["project_id"] not in projects:
        raise replay.ReplayError("PROJECT_NOT_AUTHORIZED")
    selected_ids = replay.read_json(args.case_ids) if args.case_ids else None
    if selected_ids is not None and (
        not isinstance(selected_ids, list)
        or not selected_ids
        or any(not isinstance(value, str) for value in selected_ids)
        or len(set(selected_ids)) != len(selected_ids)
        or not set(selected_ids) <= {case["id"] for case in plan["cases"]}
    ):
        raise replay.ReplayError("INVALID_CASE_SELECTION")
    by_id = {case["id"]: case for case in plan["cases"]}
    selected_cases = (
        [by_id[case_id] for case_id in selected_ids]
        if selected_ids is not None
        else plan["cases"]
    )
    # Validate before opening a production connection or creating a ledger.
    # In particular the old Eval one-row proxy cannot certify a 50-row UI.
    for case in selected_cases:
        validate_preview_workload(case)
    if args.summary and Path(args.summary).exists():
        raise replay.ReplayError("SUMMARY_ALREADY_EXISTS")
    fingerprint = source_fingerprint()
    run_profile = {
        "plan_id": plan["plan_id"],
        "source_sha256": fingerprint,
        "projects": projects,
        "host": args.host,
        "port": args.port,
        "server": args.expected_server,
        "database": args.database,
        "safety_seconds": args.safety_seconds,
        "threads": args.threads,
        "read_gib": args.read_gib,
        "verify_trace_ids": args.verify_trace_ids,
        "verify_trace_full_rows": args.verify_trace_full_rows,
        "relational_metadata_sha256": replay.digest(
            replay.read_json(args.relational_metadata)
        )
        if args.relational_metadata
        else None,
        "relational_metadata_io_timed": False,
    }
    if args.verify_finite_users_remap:
        run_profile["verify_finite_users_remap"] = True
    lock = Path(args.ledger + ".lock")
    lock_fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(lock_fd)
    try:
        # Identity check is metadata-only and uses server-enforced readonly before startup.
        from clickhouse_driver import Client

        check = Client(
            args.host,
            port=args.port,
            database=args.database,
            user=os.environ.get("OBSERVE_CH_USER", "default"),
            password=os.environ.get("OBSERVE_CH_PASSWORD", ""),
            connect_timeout=3,
            send_receive_timeout=5,
            settings={"readonly": 2, "max_execution_time": 3},
        )
        try:
            server = check.execute("SELECT hostName(), version(), currentDatabase()")
        finally:
            check.disconnect()
        if server[0][0] != args.expected_server or server[0][2] != args.database:
            raise replay.ReplayError("DATABASE_TARGET_MISMATCH")
        run_profile["server_version"] = server[0][1]
        initialize_candidate()
        run_profile["candidate_runtime"] = candidate_runtime_profile()
        run_id = replay.digest(run_profile)
        prior = replay.load_ledger(args.ledger, run_id)
        done = {r["case_id"] for r in prior}
        recorded = list(prior)
        adapter = CandidateQueries(args, plan, projects)
        count, failures = 0, 0
        run_end = time.monotonic() + args.run_seconds
        with os.fdopen(
            os.open(args.ledger, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "a"
        ) as out:
            for case in selected_cases:
                # Do not start an action that cannot fit in this diagnostic batch.
                if (
                    time.monotonic()
                    + args.safety_seconds * (2 if args.verify_trace_ids else 1)
                    > run_end
                    or failures >= args.max_consecutive_failures
                ):
                    break
                if case["id"] in done:
                    continue
                if args.surface and case["surface"] != args.surface:
                    continue
                if args.period and case["period"] != args.period:
                    continue
                if args.attribute and args.attribute not in case["attributes"]:
                    continue
                if args.variant and args.variant not in case["variant"]:
                    continue
                row = adapter.run(case)
                row.update(
                    run_id=run_id,
                    source_sha256=fingerprint,
                    plan_id=plan["plan_id"],
                    runtime_profile=run_profile,
                )
                out.write(replay.canonical(row) + "\n")
                out.flush()
                os.fsync(out.fileno())
                print(replay.canonical(row), flush=True)
                recorded.append(row)
                count += 1
                failures = (
                    failures + 1
                    if row["status"]
                    in ("ERROR", "INCOMPLETE", "INEXACT", "SAFETY_STOP")
                    else 0
                )
                if count >= args.max_cases:
                    break
                time.sleep(0.25)
        if source_fingerprint() != fingerprint:
            raise replay.ReplayError("SOURCE_CHANGED_DURING_RUN_REJECT_RESULTS")
        if args.summary:
            replay.private_write(
                args.summary, qualification_summary(plan, recorded, fingerprint)
            )
        print(
            replay.canonical(
                {
                    "executed": count,
                    "source_sha256": fingerprint,
                    "http_e2e": False,
                    "ui_e2e": False,
                    "full_row_independent_oracle": False,
                    "trace_identity_reference_requested": args.verify_trace_ids,
                    "trace_query_layer_fields_requested": args.verify_trace_full_rows,
                    "qualification": "NOT_QUALIFIED",
                }
            )
        )
    finally:
        lock.unlink()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted; completed cases remain checkpointed.", file=sys.stderr)
        raise SystemExit(130) from None
    except replay.ReplayError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from None
