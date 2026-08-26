#!/usr/bin/env python3
"""Current-source, SELECT-only live qualifier for the CATALOG read surfaces.

This file is intentionally inert when imported.  The launch bundle invokes it
directly as ``/harness/qualify.py`` after binding an exact image digest and a
deterministic source manifest.  It never logs in, creates credentials, starts a
worker, or calls a public network endpoint; real DRF callbacks are resolved and
invoked in-process with an existing authorized principal selected by read-only
queries.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import multiprocessing
import os
import re
import signal
import sys
import threading
import time
import traceback
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from safety import (
    BASE_COMMIT,
    CH_TIMEOUT_SECONDS,
    PG_TIMEOUT_MS,
    SCHEMA,
    SafetyViolation,
    assert_ch_read,
    assert_pg_read,
    bounded_ch_settings,
    safe_relative_path,
    sha256_bytes,
    static_guard_self_test,
    verify_regular_file,
)

BACKEND_ROOT = Path("/app/backend")
HARNESS_ROOT = Path("/harness")
SOURCE_MANIFEST_PATH = HARNESS_ROOT / "source-manifest.json"
REQUEST_WALL_SECONDS = 9.8
SUPERVISOR_WALL_SECONDS = 9.0
SUPERVISOR_KILL_GRACE_SECONDS = 0.25
MAX_RENDERED_RESPONSE_BYTES = 16 * 1024 * 1024
FILTER_ATTESTATION_VERSION = "canonical-json-sha256-v1"
EMPTY_CURSOR_CHECKPOINT_KINDS = frozenset({"trace", "span", "voice"})
QUALIFIER_WALL_SECONDS = 5_280
MAX_REQUESTS = 600
MAX_CH_READS = 4_096
MAX_TARGET_PROJECTS = 4
EXPECTED_STARTUP_NLTK_DOWNLOADS = (
    "punkt",
    "averaged_perceptron_tagger",
    "wordnet",
    "omw-1.4",
    "stopwords",
)
EXPECTED_STARTUP_REDIS_PINGS = 3
METRIC_CATALOG_DISCOVERY_MAX_PAGES = 2
METRIC_CATALOG_DISCOVERY_PAGE_SIZE = 50
METRIC_CATALOG_QUALIFICATION_MAX_PAGES = 8
METRIC_CATALOG_QUALIFICATION_PAGE_SIZE = 50
PROFILE_VALUE_DISCOVERY_MAX_CANDIDATES = 2
MODEL_VALUE_EXPECTED_ACTIVATED_QUERY_COUNT = 4
QUALIFIER_END_MAX_AGE = timedelta(hours=10)
QUALIFIER_SHARDS = (
    "whatfix",
    "colektia",
    "mudflap",
    "trace_system",
    "whatfix_graphs",
    "colektia_graphs",
)
SHARD_TARGET_NAMES = {
    "whatfix": ("whatfix",),
    "colektia": ("colektia",),
    "mudflap": ("mudflap",),
    "trace_system": ("whatfix", "colektia"),
    "whatfix_graphs": ("whatfix",),
    "colektia_graphs": ("colektia",),
}
SHARD_PROFILE_PARTITIONS = {
    "whatfix": "core",
    "colektia": "core",
    "mudflap": "all",
    "trace_system": "system",
    "whatfix_graphs": "selection",
    "colektia_graphs": "selection",
}
GRAPH_SHARD_TARGETS = {
    "whatfix_graphs": "whatfix",
    "colektia_graphs": "colektia",
}
SYSTEM_MATRIX_PROFILE_MODES = frozenset(
    {
        "f1.system",
        "f4.system_custom",
    }
)
EXACT_VALUE_PROFILE_MODES = frozenset(
    {
        *SYSTEM_MATRIX_PROFILE_MODES,
        "f5.eval_exact",
        "f6.annotation_exact",
        "f7.custom_eval_annotation",
    }
)
WINDOWS = (
    ("30m", timedelta(minutes=30)),
    ("1h", timedelta(hours=1)),
    ("6h", timedelta(hours=6)),
    ("24h", timedelta(hours=24)),
    ("7d", timedelta(days=7)),
    ("30d", timedelta(days=30)),
    ("90d", timedelta(days=90)),
    ("180d", timedelta(days=180)),
    ("365d", timedelta(days=365)),
)
TARGETS: dict[str, dict[str, Any]] = {
    "whatfix": {
        "anchor_project_id": "4b3d0477-ff0f-4681-9535-9b152152bf25",
        "tokens": ("whatfix",),
        "surface": "trace",
        "density": "dense",
        "preferred_keys": (
            "whatfix.ent_id",
            "ended_reason",
            "final_status",
            "call_id",
        ),
    },
    "colektia": {
        "anchor_project_id": "ca3025a9-b5eb-4872-9973-2330956d40d2",
        "tokens": ("colektia", "colly"),
        "surface": "trace",
        "density": "sparse",
        "preferred_keys": (
            "prompt_slug",
            "final_status",
            "ended_reason",
            "call_id",
        ),
    },
    "mudflap": {
        "anchor_project_id": "e5862a0e-118b-4fb4-968f-7d94a51aa4be",
        "tokens": ("mudflap",),
        "surface": "voice",
        "density": "dense",
        "preferred_keys": ("ended_reason", "final_status", "call_id"),
    },
}


class QualificationFailure(RuntimeError):
    pass


class PopulationGap(QualificationFailure):
    pass


class RequestDeadlineExceeded(TimeoutError):
    pass


_lock = threading.Lock()
_counts = {
    "requests": 0,
    "pg_select": 0,
    "pg_blocked": 0,
    "ch_read": 0,
    "ch_blocked": 0,
    "redis_blocked": 0,
    "celery_blocked": 0,
    "temporal_blocked": 0,
    "scheduler_blocked": 0,
    "external_cache_blocked": 0,
    "local_cache_write": 0,
    "startup_nltk_download_suppressed": 0,
    "startup_redis_ping_suppressed": 0,
}
_COUNT_NAMES = tuple(_counts)
_child_count_bridge: Any | None = None
_request_records: list[dict[str, Any]] = []
_lane_records: list[dict[str, Any]] = []
_cache_footprint: list[dict[str, Any]] = []
_active_context: dict[str, Any] = {}
_dispatch_tripwire_installation_complete = False
_startup_preload_evidence: dict[str, Any] = {
    "attempted": False,
    "completed": False,
    "callback_tripwires_active": False,
    "preloaded_route_count": 0,
    "preloaded_route_binding_sha256": None,
    "nltk_downloads_suppressed": [],
    "redis_pings_suppressed": 0,
}
_qualifier_deadline_monotonic: float | None = None


def _increment_child_bridge(name: str) -> None:
    bridge = _child_count_bridge
    if bridge is not None:
        index = _COUNT_NAMES.index(name)
        # The callback child is the bridge's only writer and the parent does
        # not read it until that child has exited or been killed.  A process-
        # shared lock is therefore unnecessary and, more importantly, unsafe:
        # SIGKILL while the child owns that lock could strand the supervisor in
        # cleanup beyond its hard wall.
        bridge[index] += 1


def _inc(name: str) -> None:
    with _lock:
        _counts[name] += 1
    _increment_child_bridge(name)


def _reserve_ch_read() -> None:
    """Atomically reserve one physical ClickHouse read below the hard fuse."""

    with _lock:
        if _counts["ch_read"] >= MAX_CH_READS:
            _counts["ch_blocked"] += 1
            reserved = False
        else:
            _counts["ch_read"] += 1
            reserved = True
    _increment_child_bridge("ch_read" if reserved else "ch_blocked")
    if not reserved:
        raise SafetyViolation("qualifier ClickHouse-read fuse reached")


def _snapshot_counts() -> dict[str, int]:
    with _lock:
        return dict(_counts)


def _digest(value: Any, length: int = 16) -> str:
    raw = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:length]


def _redact(value: Any) -> str:
    text = " ".join(str(value).split())
    text = re.sub(
        r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b",
        "<redacted-id>",
        text,
    )
    text = re.sub(r"(?i)(password|token|secret)=\S+", r"\1=<redacted>", text)
    return text[:400]


def _deadline_handler(_signum, _frame):
    raise RequestDeadlineExceeded("request exceeded the qualifier wall")


@contextmanager
def _request_deadline(seconds: float = REQUEST_WALL_SECONDS):
    previous = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _deadline_handler)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _assert_budget() -> None:
    counts = _snapshot_counts()
    if counts["requests"] >= MAX_REQUESTS:
        raise SafetyViolation("qualifier request fuse reached")
    if counts["ch_read"] >= MAX_CH_READS:
        raise SafetyViolation("qualifier ClickHouse-read fuse reached")
    if (
        _qualifier_deadline_monotonic is not None
        and time.monotonic() + REQUEST_WALL_SECONDS >= _qualifier_deadline_monotonic
    ):
        raise SafetyViolation("qualifier cumulative wall reached")


def _verify_source_identity() -> dict[str, Any]:
    expected_manifest_hash = os.environ.get("EXPECTED_SOURCE_MANIFEST_SHA256", "")
    expected_qualifier_hash = os.environ.get("EXPECTED_QUALIFIER_SHA256", "")
    expected_image = os.environ.get("EXPECTED_IMAGE_DIGEST", "")
    if os.environ.get("EXPECTED_BASE_COMMIT") != BASE_COMMIT:
        raise SafetyViolation("base commit environment pin mismatch")
    if not re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", expected_image):
        raise SafetyViolation("derived image digest pin is absent or invalid")
    verify_regular_file(SOURCE_MANIFEST_PATH, expected_manifest_hash)
    verify_regular_file(Path(__file__), expected_qualifier_hash)
    manifest = json.loads(SOURCE_MANIFEST_PATH.read_text(encoding="utf-8"))
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") != SCHEMA
        or manifest.get("base_commit") != BASE_COMMIT
        or not re.fullmatch(
            r"[^\s@]+@sha256:[0-9a-f]{64}", str(manifest.get("base_image") or "")
        )
    ):
        raise SafetyViolation("source manifest identity is invalid")
    runtime_hashes: dict[str, str] = {}
    for key in ("runtime_files",):
        values = manifest.get(key)
        if not isinstance(values, dict):
            raise SafetyViolation(f"source manifest {key} map is invalid")
        for relative, expected_hash in values.items():
            path = safe_relative_path(str(relative)).as_posix()
            if path in runtime_hashes and runtime_hashes[path] != expected_hash:
                raise SafetyViolation("source manifest runtime hashes conflict")
            runtime_hashes[path] = str(expected_hash)
    required = manifest.get("runtime_required")
    dirty = manifest.get("runtime_dirty")
    if not isinstance(required, dict) or not isinstance(dirty, dict):
        raise SafetyViolation("source manifest runtime subset maps are invalid")
    for subset_name, values in (
        ("runtime_required", required),
        ("runtime_dirty", dirty),
    ):
        for relative, expected_hash in values.items():
            path = safe_relative_path(str(relative)).as_posix()
            if runtime_hashes.get(path) != expected_hash:
                raise SafetyViolation(
                    f"source manifest {subset_name} is not bound to runtime_files"
                )
    raw_runtime_deletions = manifest.get("runtime_deletions")
    if not isinstance(raw_runtime_deletions, list) or not all(
        isinstance(value, str) for value in raw_runtime_deletions
    ):
        raise SafetyViolation("source manifest runtime_deletions list is invalid")
    runtime_deletions = [
        safe_relative_path(value).as_posix() for value in raw_runtime_deletions
    ]
    if runtime_deletions != sorted(set(runtime_deletions)):
        raise SafetyViolation(
            "source manifest runtime_deletions must be sorted and unique"
        )
    overlap = sorted(set(runtime_deletions) & set(runtime_hashes))
    if overlap:
        raise SafetyViolation(
            "source manifest runtime file/deletion overlap: " + ",".join(overlap[:20])
        )
    mismatches = []
    for relative, expected_hash in sorted(runtime_hashes.items()):
        path = BACKEND_ROOT / relative
        actual = sha256_bytes(path.read_bytes()) if path.is_file() else None
        if actual != expected_hash:
            mismatches.append(relative)
    for relative in runtime_deletions:
        # ``Path.exists`` follows symlinks and would treat a dangling symlink
        # as absent.  A deletion proof requires no filesystem object at all.
        if os.path.lexists(BACKEND_ROOT / relative):
            mismatches.append(relative)
    if mismatches:
        raise SafetyViolation(
            "runtime source hash mismatch: " + ",".join(mismatches[:20])
        )
    return {
        "base_commit": BASE_COMMIT,
        "derived_image_digest": expected_image,
        "base_image_digest": manifest["base_image"],
        "source_manifest_sha256": expected_manifest_hash,
        "qualifier_sha256": expected_qualifier_hash,
        "verified_runtime_files": len(runtime_hashes),
        "verified_runtime_deletions": len(runtime_deletions),
        "dirty_file_count": len(manifest.get("dirty") or {}),
        "dirty_runtime_file_count": len(manifest.get("runtime_dirty") or {}),
    }


def _install_pg_guard() -> None:
    from django.db.backends.utils import CursorWrapper

    original_execute = CursorWrapper._execute

    def guarded_execute(self, sql, params, *args, **kwargs):
        try:
            assert_pg_read(sql, params)
        except SafetyViolation:
            _inc("pg_blocked")
            raise
        first = re.sub(r"^\s*(?:/\*.*?\*/\s*)*", "", str(sql), flags=re.S)
        if re.match(r"(?i)^(SELECT|WITH)\b", first):
            _inc("pg_select")
        return original_execute(self, sql, params, *args, **kwargs)

    def guarded_executemany(self, _sql, _param_list, *args, **kwargs):
        _inc("pg_blocked")
        raise SafetyViolation("PostgreSQL executemany blocked")

    CursorWrapper._execute = guarded_execute
    CursorWrapper._executemany = guarded_executemany


def _ch_server_enforced_readonly() -> bool:
    return any(
        os.environ.get(name, "").strip().lower() == "true"
        for name in (
            "CH_SERVER_ENFORCED_READONLY",
            "CH25_SERVER_ENFORCED_READONLY",
        )
    )


def _ch_call_settings(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    settings_index: int,
    server_enforced: bool,
) -> tuple[list[Any], dict[str, Any]]:
    positional = list(args)
    call_kwargs = dict(kwargs)
    if server_enforced:
        return positional, call_kwargs
    if len(positional) > settings_index:
        positional[settings_index] = bounded_ch_settings(positional[settings_index])
        call_kwargs.pop("settings", None)
    else:
        call_kwargs["settings"] = bounded_ch_settings(call_kwargs.get("settings"))
    return positional, call_kwargs


def _install_ch_guard() -> None:
    from clickhouse_driver import Client as NativeClient

    server_enforced = _ch_server_enforced_readonly()
    for method_name in ("execute", "execute_iter"):
        original = getattr(NativeClient, method_name)

        def guarded(self, query, *args, __original=original, **kwargs):
            try:
                assert_ch_read(query)
            except SafetyViolation:
                _inc("ch_blocked")
                raise
            _reserve_ch_read()
            # clickhouse-driver execute(..., settings=...) has settings as the
            # fifth positional argument after query.
            positional, call_kwargs = _ch_call_settings(
                args,
                kwargs,
                settings_index=4,
                server_enforced=server_enforced,
            )
            return __original(self, query, *positional, **call_kwargs)

        setattr(NativeClient, method_name, guarded)

    try:
        from clickhouse_connect.driver.client import Client as HTTPClient
    except ImportError as exc:
        raise SafetyViolation("clickhouse-connect is unavailable") from exc

    for method_name, settings_index in (("query", 1), ("command", 2), ("raw_query", 1)):
        original = getattr(HTTPClient, method_name)

        def guarded(
            self,
            query,
            *args,
            __original=original,
            __settings_index=settings_index,
            **kwargs,
        ):
            try:
                assert_ch_read(query)
            except SafetyViolation:
                _inc("ch_blocked")
                raise
            _reserve_ch_read()
            positional, call_kwargs = _ch_call_settings(
                args,
                kwargs,
                settings_index=__settings_index,
                server_enforced=server_enforced,
            )
            return __original(self, query, *positional, **call_kwargs)

        setattr(HTTPClient, method_name, guarded)

    def blocked(*_args, **_kwargs):
        _inc("ch_blocked")
        raise SafetyViolation("ClickHouse mutation method blocked")

    for method_name in (
        "insert",
        "insert_df",
        "insert_df_arrow",
        "insert_arrow",
        "raw_insert",
    ):
        if hasattr(HTTPClient, method_name):
            setattr(HTTPClient, method_name, blocked)


def _select_only_exact_snapshot(
    namespace: str,
    identity: Any,
    *,
    refresh: bool,
    pending_payload: Any,
) -> Any:
    """Compute a reviewed exact graph/dashboard read without mutation.

    Product requests intentionally schedule cold exact reads and publish them
    atomically through Redis. The qualifier forbids both writes, so merely
    tripwiring dispatch would make every cold filtered graph or dashboard query
    fail before its SELECT plan ran. This qualifier-only seam invokes the same
    worker payload reader synchronously under the parent/child 9.8-second hard
    wall. It still performs the worker's tenant reauthorization and every real
    PostgreSQL and ClickHouse SELECT, but never claims, schedules, caches, or
    publishes work.
    """

    del refresh, pending_payload
    if not isinstance(namespace, str) or not (
        namespace.startswith("observe-") or namespace == "dashboard-query"
    ):
        raise SafetyViolation("qualifier synchronous exact read escaped review scope")
    if not isinstance(identity, dict):
        raise SafetyViolation("qualifier exact read identity is invalid")
    from tracer.tasks.exact_aggregation import _load_exact_payload

    return _load_exact_payload(namespace, dict(identity))


def _install_dispatch_tripwires() -> None:
    global _dispatch_tripwire_installation_complete

    _dispatch_tripwire_installation_complete = False

    def blocker(counter: str, detail: str):
        def blocked(*_args, **_kwargs):
            _inc(counter)
            raise SafetyViolation(detail)

        blocked._qualifier_tripwire_counter = counter
        return blocked

    from django.core.cache import caches
    from django.core.cache.backends.locmem import LocMemCache

    local_cache = caches["default"]
    if not isinstance(local_cache, LocMemCache):
        _inc("external_cache_blocked")
        raise SafetyViolation("default cache is not pod-local LocMemCache")
    for method_name in (
        "add",
        "set",
        "touch",
        "delete",
        "set_many",
        "delete_many",
        "clear",
        "incr",
        "decr",
        "get_or_set",
    ):
        if not hasattr(local_cache, method_name):
            raise SafetyViolation(f"local cache tripwire drifted: {method_name}")
        original = getattr(local_cache, method_name)

        def local_write(*args, __original=original, __name=method_name, **kwargs):
            _inc("local_cache_write")
            key = args[0] if args else kwargs.get("key", "")
            with _lock:
                _cache_footprint.append({"op": __name, "key_digest": _digest(key)})
            return __original(*args, **kwargs)

        setattr(local_cache, method_name, local_write)

    import redis
    import redis.asyncio as async_redis

    redis.Redis.execute_command = blocker(
        "redis_blocked", "external Redis command blocked"
    )
    async_redis.Redis.execute_command = blocker(
        "redis_blocked", "external async Redis command blocked"
    )

    from celery import Celery
    from celery.app.task import Task
    from celery.canvas import Signature

    for owner, name in (
        (Task, "apply_async"),
        (Task, "delay"),
        (Celery, "send_task"),
        (Signature, "apply_async"),
        (Signature, "delay"),
    ):
        setattr(owner, name, blocker("celery_blocked", "Celery dispatch blocked"))

    import tfc.temporal.common.client as temporal_client
    import tfc.temporal.drop_in.runner as temporal_runner
    import tfc.temporal.schedules.manager as schedule_manager

    for name in ("start_workflow_sync", "start_workflow_async"):
        if hasattr(temporal_client, name):
            setattr(
                temporal_client,
                name,
                blocker("temporal_blocked", "Temporal dispatch blocked"),
            )
    for name in ("start_activity", "start_activity_sync", "start_activity_async"):
        if hasattr(temporal_runner, name):
            setattr(
                temporal_runner,
                name,
                blocker("temporal_blocked", "Temporal dispatch blocked"),
            )
    for name in (
        "a_create_schedule",
        "create_schedule",
        "a_update_schedule",
        "update_schedule",
        "a_delete_schedule",
        "delete_schedule",
        "a_pause_schedule",
        "pause_schedule",
        "a_unpause_schedule",
        "unpause_schedule",
        "a_trigger_schedule",
        "trigger_schedule",
        "a_create_or_update_schedule",
        "a_cleanup_orphaned_schedules",
        "cleanup_orphaned_schedules",
        "a_register_schedules",
        "register_schedules",
    ):
        if hasattr(schedule_manager, name):
            setattr(
                schedule_manager,
                name,
                blocker("scheduler_blocked", "schedule mutation blocked"),
            )

    from tracer.tasks.exact_aggregation import refresh_exact_aggregation_snapshot

    for name in ("apply_async", "delay"):
        setattr(
            refresh_exact_aggregation_snapshot,
            name,
            blocker("celery_blocked", "exact refresh dispatch blocked"),
        )

    # These modules import this cache function by value, so patch every reviewed
    # live-qualifier call site after Django URL loading. The direct reader above
    # remains SELECT-only and the outer supervised child is still the
    # non-bypassable wall.
    import tracer.services.clickhouse.graph_dispatch as graph_dispatch
    import tracer.services.clickhouse.session_graph as session_graph
    import tracer.views.dashboard as dashboard

    graph_dispatch.read_or_schedule_exact_snapshot = _select_only_exact_snapshot
    session_graph.read_or_schedule_exact_snapshot = _select_only_exact_snapshot
    dashboard.read_or_schedule_exact_snapshot = _select_only_exact_snapshot
    _dispatch_tripwire_installation_complete = True


def _dispatch_tripwires_active() -> bool:
    import redis
    import redis.asyncio as async_redis

    return _dispatch_tripwire_installation_complete and all(
        getattr(method, "_qualifier_tripwire_counter", None) == "redis_blocked"
        for method in (
            redis.Redis.execute_command,
            async_redis.Redis.execute_command,
        )
    )


def _verify_database_defaults() -> dict[str, Any]:
    from django.db import connections

    if os.environ.get("PGOPTIONS") != (
        "-c default_transaction_read_only=on -c statement_timeout=9500"
    ):
        raise SafetyViolation("PGOPTIONS read-only contract drifted")
    aliases = [
        alias
        for alias in connections
        if "postgresql" in connections[alias].settings_dict.get("ENGINE", "")
    ]
    if not aliases:
        raise SafetyViolation("no PostgreSQL database alias is configured")
    verified = []
    for alias in aliases:
        with connections[alias].cursor() as cursor:
            cursor.execute(
                "SELECT current_setting('default_transaction_read_only'), "
                "current_setting('statement_timeout'), "
                "EXTRACT(EPOCH FROM "
                "current_setting('statement_timeout')::interval)"
            )
            read_only, timeout, timeout_seconds = cursor.fetchone()
        if read_only != "on" or not 0 < float(timeout_seconds) <= 9.5:
            raise SafetyViolation(
                f"PostgreSQL alias {alias} is not read-only and deadline-bounded"
            )
        verified.append({"alias": alias, "statement_timeout": timeout})
    return {
        "postgresql_aliases": verified,
        "clickhouse_readonly": 2,
        "clickhouse_server_enforced": _ch_server_enforced_readonly(),
    }


@dataclass(frozen=True)
class Principal:
    workspace: Any
    user: Any


@dataclass(frozen=True)
class RelationalFilterProfile:
    property_id: str
    column_id: str
    col_type: str
    filter_type: str
    filter_op: str
    filter_value: Any
    output_type: str


@dataclass(frozen=True)
class Target:
    name: str
    project: Any
    principal: Principal
    key: str | None
    value: Any = None
    value_type: str | None = None
    system_value: str | None = None
    system_value_type: str | None = None
    eval_profile: RelationalFilterProfile | None = None
    annotation_profile: RelationalFilterProfile | None = None


@dataclass(frozen=True)
class DatasetRepresentative:
    dataset_id: str
    active_rows: int
    column_id: str

    @property
    def column_property_id(self) -> str:
        return f"dataset_column:{self.column_id}"

    @property
    def binding_sha256(self) -> str:
        return _digest(
            {
                "dataset_id": self.dataset_id,
                "column_property_id": self.column_property_id,
            },
            64,
        )


@contextmanager
def _tenant_context(project, principal: Principal):
    from tfc.middleware.workspace_context import (
        clear_workspace_context,
        set_workspace_context,
    )

    if _active_context:
        raise SafetyViolation("tenant contexts may not nest")
    _active_context.update(
        {
            "workspace": principal.workspace,
            "organization": project.organization,
            "user": principal.user,
        }
    )
    set_workspace_context(
        workspace=principal.workspace,
        organization=project.organization,
        user=principal.user,
    )
    try:
        yield
    finally:
        _active_context.clear()
        clear_workspace_context()


def _install_request_context_hook() -> None:
    from rest_framework.views import APIView

    from tfc.middleware.workspace_context import set_workspace_context

    original_initial = APIView.initial

    def initial(view_self, request, *args, **kwargs):
        if not _active_context:
            raise SafetyViolation("DRF request escaped an explicit tenant context")
        request.workspace = _active_context["workspace"]
        request.organization = _active_context["organization"]
        set_workspace_context(
            workspace=request.workspace,
            organization=request.organization,
            user=_active_context["user"],
        )
        return original_initial(view_self, request, *args, **kwargs)

    APIView.initial = initial


ROUTES: dict[str, tuple[str, str, str]] = {
    "property_keys": (
        "GET",
        "/api/traces/span-attribute-keys/",
        "get",
    ),
    "filter_values": ("GET", "/tracer/dashboard/filter_values/", "filter_values"),
    "metrics": ("GET", "/tracer/dashboard/metrics/", "metrics"),
    "dashboard_query": ("POST", "/tracer/dashboard/query/", "query"),
    "trace_list": (
        "GET",
        "/tracer/trace/list_traces_of_session/",
        "list_traces_of_session",
    ),
    "span_list": (
        "GET",
        "/tracer/observation-span/list_spans_observe/",
        "list_spans_observe",
    ),
    "session_list": (
        "GET",
        "/tracer/trace-session/list_sessions/",
        "list_sessions",
    ),
    "voice_list": ("GET", "/tracer/trace/list_voice_calls/", "list_voice_calls"),
    "users": ("GET", "/tracer/users/", "get"),
    "trace_graph": (
        "POST",
        "/tracer/trace/get_graph_methods/",
        "get_graph_methods",
    ),
    "span_graph": (
        "POST",
        "/tracer/observation-span/get_graph_methods/",
        "get_graph_methods",
    ),
    "session_graph": (
        "POST",
        "/tracer/trace-session/get_session_graph_data/",
        "get_session_graph_data",
    ),
    "dataset_exact": (
        "GET",
        "/model-hub/develops/{dataset_id}/get-dataset-table/",
        "get",
    ),
    "simulation_executions": (
        "GET",
        "/simulate/run-tests/{run_test_id}/preview-executions/",
        "get",
    ),
    "simulation_calls": (
        "GET",
        "/simulate/test-executions/{test_execution_id}/preview-calls/",
        "get",
    ),
}

_ROUTE_PRELOAD_UUID = "00000000-0000-4000-8000-000000000000"


def _route_preload_path(path_template: str) -> str:
    try:
        path = path_template.format(
            dataset_id=_ROUTE_PRELOAD_UUID,
            run_test_id=_ROUTE_PRELOAD_UUID,
            test_execution_id=_ROUTE_PRELOAD_UUID,
        )
    except (KeyError, ValueError) as exc:
        raise SafetyViolation("reviewed route template cannot be preloaded") from exc
    if "{" in path or "}" in path:
        raise SafetyViolation("reviewed route template has an unknown parameter")
    return path


@contextmanager
def _suppress_reviewed_url_import_side_effects():
    """Prevent the two reviewed URL-import side effects from touching I/O.

    Importing the project's root URL configuration constructs the legacy NLTK
    corpus helper and three Redis-backed singleton managers. The qualifier
    preloads that configuration exactly once, before installing its permanent
    dispatch tripwires. Only the five source-frozen NLTK requests and a bare
    Redis PING are permitted here, and both are satisfied in-process. Any
    drift fails closed instead of falling through to the original I/O method.
    """

    import nltk
    import redis
    import redis.asyncio as async_redis

    original_nltk_download = nltk.download
    original_redis_execute = redis.Redis.execute_command
    original_async_redis_execute = async_redis.Redis.execute_command
    suppressed_nltk: list[str] = []
    suppressed_redis_pings = 0

    def suppress_nltk_download(package: Any, *args: Any, **kwargs: Any) -> bool:
        if (
            not isinstance(package, str)
            or package not in EXPECTED_STARTUP_NLTK_DOWNLOADS
            or args
            or kwargs != {"quiet": True}
        ):
            raise SafetyViolation("unexpected NLTK download during URL preload")
        suppressed_nltk.append(package)
        _inc("startup_nltk_download_suppressed")
        return True

    def validate_redis_ping(args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        nonlocal suppressed_redis_pings

        if len(args) != 1 or kwargs:
            raise SafetyViolation("unexpected Redis command during URL preload")
        command = args[0]
        if isinstance(command, bytes):
            try:
                command = command.decode("ascii")
            except UnicodeDecodeError as exc:
                raise SafetyViolation(
                    "unexpected Redis command during URL preload"
                ) from exc
        if not isinstance(command, str) or command.upper() != "PING":
            raise SafetyViolation("unexpected Redis command during URL preload")
        suppressed_redis_pings += 1
        _inc("startup_redis_ping_suppressed")

    def suppress_redis_ping(_client: Any, *args: Any, **kwargs: Any) -> bool:
        validate_redis_ping(args, kwargs)
        return True

    async def suppress_async_redis_ping(
        _client: Any, *args: Any, **kwargs: Any
    ) -> bool:
        validate_redis_ping(args, kwargs)
        return True

    nltk.download = suppress_nltk_download
    redis.Redis.execute_command = suppress_redis_ping
    async_redis.Redis.execute_command = suppress_async_redis_ping
    try:
        yield suppressed_nltk, lambda: suppressed_redis_pings
    finally:
        nltk.download = original_nltk_download
        redis.Redis.execute_command = original_redis_execute
        async_redis.Redis.execute_command = original_async_redis_execute


def _preload_reviewed_url_callbacks() -> dict[str, Any]:
    """Load and validate all reviewed callbacks without startup network I/O."""

    from django.urls import Resolver404, resolve

    _startup_preload_evidence.update(
        {
            "attempted": True,
            "completed": False,
            "callback_tripwires_active": False,
            "preloaded_route_count": 0,
            "preloaded_route_binding_sha256": None,
            "nltk_downloads_suppressed": [],
            "redis_pings_suppressed": 0,
        }
    )
    bindings: list[tuple[str, str, str, str]] = []
    with _suppress_reviewed_url_import_side_effects() as (
        suppressed_nltk,
        suppressed_redis_ping_count,
    ):
        for endpoint, (method, path_template, expected_action) in ROUTES.items():
            path = _route_preload_path(path_template)
            try:
                match = resolve(path)
            except Resolver404 as exc:
                raise SafetyViolation(
                    f"public route does not resolve during preload: {endpoint}"
                ) from exc
            callback = match.func
            actions = getattr(callback, "actions", None)
            resolved_action = (
                actions.get(method.lower())
                if isinstance(actions, dict)
                else method.lower()
            )
            if resolved_action != expected_action:
                raise SafetyViolation(
                    f"public route action drifted during preload: "
                    f"{endpoint}:{resolved_action}"
                )
            bindings.append((endpoint, method, path_template, expected_action))

        redis_ping_count = suppressed_redis_ping_count()
        _startup_preload_evidence.update(
            {
                "preloaded_route_count": len(bindings),
                "nltk_downloads_suppressed": list(suppressed_nltk),
                "redis_pings_suppressed": redis_ping_count,
            }
        )
        if tuple(suppressed_nltk) != EXPECTED_STARTUP_NLTK_DOWNLOADS:
            raise SafetyViolation("URL preload NLTK suppression count drifted")
        if redis_ping_count != EXPECTED_STARTUP_REDIS_PINGS:
            raise SafetyViolation("URL preload Redis suppression count drifted")

    binding_wire = json.dumps(
        bindings,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    evidence = {
        **_startup_preload_evidence,
        "completed": True,
        "preloaded_route_binding_sha256": sha256_bytes(binding_wire),
    }
    _startup_preload_evidence.update(evidence)
    return dict(evidence)


@contextmanager
def _block_raw_network_connections():
    """Block raw outbound connects during the pre-tripwire startup interval."""

    import socket

    original_create_connection = socket.create_connection
    original_socket_connect = socket.socket.connect

    def blocked(*_args, **_kwargs):
        raise SafetyViolation(
            "raw network connection blocked before dispatch tripwires were active"
        )

    blocked._qualifier_startup_network_block = True
    socket.create_connection = blocked
    socket.socket.connect = blocked
    try:
        yield
    finally:
        socket.create_connection = original_create_connection
        socket.socket.connect = original_socket_connect


def _validate_runtime_read_settings():
    from django.conf import settings

    if (
        settings.SPAN_ATTRIBUTE_CATALOG_READ_MODE != "off"
        or settings.SPAN_ATTRIBUTE_CATALOG_DEV_SNAPSHOT_ENABLED is not False
    ):
        raise SafetyViolation("attribute catalog public reads are not hard-disabled")
    if (
        settings.PROPERTY_CATALOG_READ_MODE != "read"
        or settings.PROPERTY_CATALOG_DEV_READ_ACK
        != "I_ACKNOWLEDGE_DEV_ONLY_UNIFIED_PROPERTY_CATALOG"
        or settings.PROPERTY_CATALOG_DATABASE != settings.PROPERTY_CATALOG_CH_DATABASE
        or re.fullmatch(
            r"fi_catalog_dev_[a-z0-9][a-z0-9_]*",
            str(settings.PROPERTY_CATALOG_DATABASE or ""),
        )
        is None
        or not settings.PROPERTY_CATALOG_DEV_WORKSPACE_ALLOWLIST
    ):
        raise SafetyViolation("unified property catalog DEV read contract drifted")
    return settings


def _bootstrap_reviewed_django_runtime(
    django_setup: Callable[[], Any],
) -> tuple[dict[str, Any], Any]:
    """Set up Django and permanent dispatch guards under one raw-network block.

    The explicit callable keeps this usable by the launcher's manual wrapper
    without importing or initializing Django at qualifier module import time.
    """

    with _block_raw_network_connections():
        django_setup()
        settings = _validate_runtime_read_settings()
        startup_preload = _preload_reviewed_url_callbacks()
        _install_dispatch_tripwires()
        if not _dispatch_tripwires_active():
            raise SafetyViolation(
                "callback Redis tripwires are not active after preload"
            )
        startup_preload["callback_tripwires_active"] = True
        _startup_preload_evidence["callback_tripwires_active"] = True
    return startup_preload, settings


@dataclass(frozen=True)
class EncodedDRFResponse:
    status_code: int
    data: dict[str, Any]
    rendered_sha256: str
    rendered_bytes: int


def _strict_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _render_and_encode_response(response: Any) -> dict[str, Any]:
    render = getattr(response, "render", None)
    if not callable(render):
        raise QualificationFailure("public callback returned an unrenderable response")
    render()
    content = getattr(response, "rendered_content", None)
    if isinstance(content, str):
        wire = content.encode("utf-8")
    elif isinstance(content, (bytes, bytearray, memoryview)):
        wire = bytes(content)
    else:
        raise QualificationFailure("public callback omitted encoded response bytes")
    if not wire or len(wire) > MAX_RENDERED_RESPONSE_BYTES:
        raise QualificationFailure("public callback returned an invalid response size")
    try:
        decoded = json.loads(
            wire.decode("utf-8"),
            parse_constant=_strict_json_constant,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise QualificationFailure(
            "public callback returned invalid strict JSON"
        ) from exc
    status_code = getattr(response, "status_code", None)
    if (
        not isinstance(status_code, int)
        or isinstance(status_code, bool)
        or not 100 <= status_code <= 599
        or not isinstance(decoded, dict)
    ):
        raise QualificationFailure("public callback returned an invalid JSON response")
    return {
        "status_code": status_code,
        "wire": wire,
        "rendered_sha256": sha256_bytes(wire),
        "rendered_bytes": len(wire),
    }


def _supervised_child_entry(
    send_connection: Any,
    operation: Callable[[], Any],
    count_bridge: Any,
    wall_seconds: float,
) -> None:
    global _child_count_bridge

    _child_count_bridge = count_bridge
    try:
        with _request_deadline(wall_seconds):
            payload = _render_and_encode_response(operation())
        send_connection.send({"ok": True, **payload})
    except BaseException as exc:
        try:
            send_connection.send(
                {
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "error": _redact(exc),
                    "safety_violation": isinstance(exc, SafetyViolation),
                    "deadline": isinstance(exc, RequestDeadlineExceeded),
                    "qualification_failure": isinstance(exc, QualificationFailure),
                }
            )
        except BaseException:
            pass
    finally:
        _child_count_bridge = None
        send_connection.close()


def _merge_child_count_bridge(count_bridge: Any) -> None:
    deltas = tuple(int(count_bridge[index]) for index in range(len(_COUNT_NAMES)))
    with _lock:
        for name, delta in zip(_COUNT_NAMES, deltas, strict=True):
            _counts[name] += delta


def _kill_supervised_process(process: Any, grace_seconds: float) -> None:
    if not process.is_alive():
        return
    process.kill()
    process.join(grace_seconds)
    if process.is_alive():
        raise SafetyViolation("supervised callback child could not be terminated")


def _supervise_drf_response(
    operation: Callable[[], Any],
    *,
    wall_seconds: float = SUPERVISOR_WALL_SECONDS,
) -> EncodedDRFResponse:
    if (
        not isinstance(wall_seconds, (int, float))
        or isinstance(wall_seconds, bool)
        or wall_seconds <= 0
        or wall_seconds >= REQUEST_WALL_SECONDS
    ):
        raise SafetyViolation("supervised callback wall is invalid")
    if "fork" not in multiprocessing.get_all_start_methods():
        raise SafetyViolation("qualification requires a fork-capable runtime")
    context = multiprocessing.get_context("fork")
    receive_connection, send_connection = context.Pipe(duplex=False)
    # Exactly one child writes these counters.  Keep this lock-free so killing
    # a wedged callback can never leave the parent waiting on an orphaned lock.
    count_bridge = context.Array("q", len(_COUNT_NAMES), lock=False)
    process = context.Process(
        target=_supervised_child_entry,
        args=(send_connection, operation, count_bridge, float(wall_seconds)),
    )
    started = time.monotonic()
    grace_seconds = min(
        SUPERVISOR_KILL_GRACE_SECONDS,
        max(0.01, float(wall_seconds) / 4),
    )
    message: dict[str, Any] | None = None
    try:
        # The child has its own alarm, but the parent must independently bound
        # both Connection.poll() and Connection.recv().  poll() only proves a
        # pipe is readable; it does not guarantee a complete framed message is
        # already available, so recv() must remain inside this parent alarm.
        try:
            with _request_deadline(float(wall_seconds)):
                process.start()
                send_connection.close()
                remaining = max(
                    0.0,
                    started + float(wall_seconds) - time.monotonic(),
                )
                if not receive_connection.poll(remaining):
                    raise RequestDeadlineExceeded(
                        "supervised callback exceeded its hard wall"
                    )
                try:
                    received = receive_connection.recv()
                except (EOFError, OSError) as exc:
                    raise QualificationFailure(
                        "supervised callback child exited without a response"
                    ) from exc
                if isinstance(received, dict):
                    message = received
                else:
                    raise QualificationFailure(
                        "supervised callback child returned an invalid message"
                    )
                if time.monotonic() >= started + float(wall_seconds):
                    raise RequestDeadlineExceeded(
                        "supervised callback exceeded its hard wall"
                    )
        except RequestDeadlineExceeded as exc:
            raise RequestDeadlineExceeded(
                "supervised callback exceeded its hard wall"
            ) from exc

        process.join(grace_seconds)
        if process.is_alive():
            _kill_supervised_process(process, grace_seconds)
        if process.exitcode not in (0, None):
            raise QualificationFailure("supervised callback child exited abnormally")
        if message is None:
            raise QualificationFailure("supervised callback child omitted its response")
        if message.get("ok") is not True:
            error = str(message.get("error") or "supervised callback failed")
            if message.get("safety_violation"):
                raise SafetyViolation(error)
            if message.get("deadline"):
                raise RequestDeadlineExceeded(error)
            raise QualificationFailure(
                f"child {message.get('error_type') or 'error'}: {error}"
            )
        wire = message.get("wire")
        if not isinstance(wire, bytes):
            raise QualificationFailure("supervised callback omitted encoded bytes")
        try:
            decoded = json.loads(
                wire.decode("utf-8"),
                parse_constant=_strict_json_constant,
            )
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            raise QualificationFailure(
                "supervised callback returned invalid strict JSON"
            ) from exc
        if not isinstance(decoded, dict):
            raise QualificationFailure("supervised callback returned non-object JSON")
        if time.monotonic() >= started + REQUEST_WALL_SECONDS:
            raise RequestDeadlineExceeded(
                "supervised response arrived after 9.8 seconds"
            )
        return EncodedDRFResponse(
            status_code=int(message["status_code"]),
            data=decoded,
            rendered_sha256=str(message["rendered_sha256"]),
            rendered_bytes=int(message["rendered_bytes"]),
        )
    finally:
        if process.pid is not None and process.is_alive():
            _kill_supervised_process(process, grace_seconds)
        receive_connection.close()
        send_connection.close()
        _merge_child_count_bridge(count_bridge)


class DirectDRFClient:
    def __init__(self, project, principal: Principal):
        self.project = project
        self.principal = principal

    def call(
        self,
        endpoint: str,
        *,
        lane: str,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        path_kwargs: dict[str, Any] | None = None,
    ):
        from django.db import connections

        _assert_budget()
        route = ROUTES.get(endpoint)
        if route is None:
            raise SafetyViolation(f"unreviewed endpoint requested: {endpoint}")
        method, path_template, expected_action = route
        path = path_template.format(**(path_kwargs or {}))
        query = query or {}
        before = _snapshot_counts()
        _inc("requests")
        started = time.monotonic()
        response = None
        error_type = None
        try:

            def invoke_callback():
                # Everything that can touch tenant state belongs inside the
                # supervised child.  Timing only the final view callback would
                # let two membership/project preflight SELECTs consume fresh
                # statement budgets before the advertised 9.8-second public
                # request wall even starts.
                from django.db.models import Q
                from django.urls import Resolver404, resolve
                from rest_framework.test import APIRequestFactory, force_authenticate

                from accounts.authentication import APIKeyAuthentication
                from accounts.models.workspace import WorkspaceMembership
                from tracer.models.project import Project

                try:
                    match = resolve(path)
                except Resolver404 as exc:
                    raise SafetyViolation(
                        f"public route does not resolve: {path}"
                    ) from exc
                callback = match.func
                actions = getattr(callback, "actions", None)
                resolved_action = (
                    actions.get(method.lower())
                    if isinstance(actions, dict)
                    else method.lower()
                )
                if resolved_action != expected_action:
                    raise SafetyViolation(
                        f"public route action drifted: {endpoint}:{resolved_action}"
                    )
                if (
                    not self.principal.user.is_active
                    or not self.principal.user.can_access_organization(
                        self.project.organization
                    )
                    or not self.principal.user.can_access_workspace(
                        self.principal.workspace
                    )
                    or not WorkspaceMembership.no_workspace_objects.filter(
                        workspace_id=self.principal.workspace.id,
                        user_id=self.principal.user.id,
                        is_active=True,
                    ).exists()
                ):
                    raise SafetyViolation(
                        "selected principal failed the workspace gate"
                    )
                if not (
                    Project.no_workspace_objects.filter(
                        id=self.project.id,
                        organization_id=self.project.organization_id,
                        deleted=False,
                    )
                    .filter(
                        Q(workspace_id=self.principal.workspace.id)
                        | Q(
                            workspace__is_default=True,
                            workspace__organization_id=self.project.organization_id,
                        )
                        | Q(workspace__isnull=True)
                    )
                    .exists()
                ):
                    raise SafetyViolation("selected project failed the tenant gate")

                factory = APIRequestFactory()
                if method == "GET":
                    request = factory.get(
                        path,
                        query,
                        HTTP_X_WORKSPACE_ID=str(self.principal.workspace.id),
                        HTTP_X_ORGANIZATION_ID=str(self.project.organization_id),
                    )
                else:
                    wire_path = path
                    if query:
                        wire_path += "?" + urlencode(query, doseq=True)
                    request = factory.post(
                        wire_path,
                        body or {},
                        format="json",
                        HTTP_X_WORKSPACE_ID=str(self.principal.workspace.id),
                        HTTP_X_ORGANIZATION_ID=str(self.project.organization_id),
                    )
                APIKeyAuthentication()._set_workspace_context(
                    request, self.principal.user
                )
                force_authenticate(request, user=self.principal.user)
                with _tenant_context(self.project, self.principal):
                    return callback(request, *match.args, **match.kwargs)

            # Never let a child operate a duplicated PostgreSQL socket. The
            # next read reconnects under locked read-only PGOPTIONS, and the
            # reconnect plus every authorization SELECT is now inside the same
            # supervised request wall.
            connections.close_all()
            remaining = REQUEST_WALL_SECONDS - (time.monotonic() - started)
            if remaining <= 0:
                raise RequestDeadlineExceeded(
                    "public request setup exceeded the 9.8-second wall"
                )
            response = _supervise_drf_response(
                invoke_callback,
                wall_seconds=min(SUPERVISOR_WALL_SECONDS, remaining),
            )
            if time.monotonic() - started >= REQUEST_WALL_SECONDS:
                raise RequestDeadlineExceeded(
                    "public request completed after the 9.8-second wall"
                )
            return response
        except Exception as exc:
            error_type = type(exc).__name__
            raise
        finally:
            elapsed = time.monotonic() - started
            after = _snapshot_counts()
            _request_records.append(
                {
                    "lane": lane,
                    "endpoint": endpoint,
                    "method": method,
                    "path": path_template,
                    "status": getattr(response, "status_code", None),
                    "elapsed_s": round(elapsed, 3),
                    "under_10s": elapsed < 10.0,
                    "within_9_8s_harness_wall": elapsed < REQUEST_WALL_SECONDS,
                    "pg_selects": after["pg_select"] - before["pg_select"],
                    "ch_reads": after["ch_read"] - before["ch_read"],
                    "request_digest": _digest({"query": query, "body": body}),
                    "response_digest": _digest(_body(response)) if response else None,
                    "rendered_sha256": (
                        response.rendered_sha256 if response is not None else None
                    ),
                    "rendered_bytes": (
                        response.rendered_bytes if response is not None else None
                    ),
                    "error_type": error_type,
                }
            )


def _body(response: Any) -> dict[str, Any]:
    data = getattr(response, "data", None)
    return data if isinstance(data, dict) else {}


def _result(response: Any) -> dict[str, Any]:
    data = _body(response)
    nested = data.get("result")
    return nested if isinstance(nested, dict) else data


def _require_status(response: Any, lane: str, expected: int = 200) -> dict[str, Any]:
    status_code = getattr(response, "status_code", None)
    if status_code != expected:
        raise QualificationFailure(f"{lane} returned HTTP {status_code}")
    envelope = _body(response)
    if envelope.get("status") is False:
        raise QualificationFailure(f"{lane} returned an unsuccessful envelope")
    payload = _result(response)
    if payload.get("query_complete") is False:
        raise QualificationFailure(f"{lane} returned an incomplete query")
    if payload.get("query_status") in {"sampled", "degraded", "pending", "failed"}:
        raise QualificationFailure(
            f"{lane} returned query_status={payload.get('query_status')}"
        )
    return payload


def _run_lane(
    lane: str,
    operation: Callable[[], Any],
    *,
    required: bool = True,
) -> dict[str, Any]:
    started = time.monotonic()
    before = _snapshot_counts()
    try:
        value = operation()
        if not isinstance(value, dict):
            value = {"result_digest": _digest(value)}
        qualified = value.get("qualified", True) is True
        reason = None if qualified else str(value.get("reason") or "not_qualified")
    except SafetyViolation:
        raise
    except Exception as exc:
        value = {"error_type": type(exc).__name__}
        qualified = False
        reason = _redact(exc)
    after = _snapshot_counts()
    record = {
        "lane": lane,
        "required": required,
        "qualified": qualified,
        "reason": reason,
        "elapsed_s": round(time.monotonic() - started, 3),
        "requests": after["requests"] - before["requests"],
        "pg_selects": after["pg_select"] - before["pg_select"],
        "ch_reads": after["ch_read"] - before["ch_read"],
        "evidence": value,
    }
    _lane_records.append(record)
    return record


def _target_text(project) -> str:
    workspace = project.workspace
    return " ".join(
        (
            project.name or "",
            getattr(project.organization, "name", "") or "",
            getattr(workspace, "name", "") or "",
            getattr(workspace, "display_name", "") or "",
        )
    ).lower()


def _resolve_tenant_projects(
    *, anchor_project_id: str, tokens: Iterable[str]
) -> tuple[Any, list[Any]]:
    from django.db.models import Q

    from tracer.models.project import Project

    tokens = tuple(token.lower() for token in tokens)
    manager = Project.no_workspace_objects
    anchor = (
        manager.filter(id=anchor_project_id, trace_type="observe", deleted=False)
        .select_related("organization", "workspace")
        .first()
    )
    if anchor is None or not any(token in _target_text(anchor) for token in tokens):
        token_q = Q()
        for token in tokens:
            token_q |= (
                Q(name__icontains=token)
                | Q(organization__name__icontains=token)
                | Q(workspace__name__icontains=token)
                | Q(workspace__display_name__icontains=token)
            )
        matches = manager.filter(
            token_q,
            trace_type="observe",
            deleted=False,
        )
        # Two organization IDs are enough to prove ambiguity. Never
        # materialize an unbounded tenant-name result while discovering a
        # bounded target population.
        organization_ids = list(
            matches.order_by().values_list("organization_id", flat=True).distinct()[:2]
        )
        if len(organization_ids) != 1:
            raise PopulationGap("tenant target did not resolve uniquely")
        anchor = (
            matches.filter(organization_id=organization_ids[0])
            .select_related("organization", "workspace")
            .order_by("id")
            .first()
        )
        if anchor is None:
            raise PopulationGap("tenant target did not resolve uniquely")
    organization_text = (getattr(anchor.organization, "name", "") or "").lower()
    workspace_text = " ".join(
        (
            getattr(anchor.workspace, "name", "") or "",
            getattr(anchor.workspace, "display_name", "") or "",
        )
    ).lower()
    queryset = manager.filter(
        organization_id=anchor.organization_id,
        trace_type="observe",
        deleted=False,
    )
    if any(token in organization_text for token in tokens):
        pass
    elif anchor.workspace_id and any(token in workspace_text for token in tokens):
        queryset = queryset.filter(workspace_id=anchor.workspace_id)
    else:
        project_q = Q(id=anchor.id)
        for token in tokens:
            project_q |= Q(name__icontains=token)
        queryset = queryset.filter(project_q)
    projects = [
        anchor,
        *list(
            queryset.exclude(id=anchor.id)
            .select_related("organization", "workspace")
            .order_by("id")[: MAX_TARGET_PROJECTS - 1]
        ),
    ]
    return anchor, projects


def _project_principal(project) -> Principal | None:
    from accounts.models.workspace import Workspace, WorkspaceMembership

    workspace = project.workspace
    if workspace is None:
        workspace = (
            Workspace.no_workspace_objects.filter(
                organization_id=project.organization_id,
                is_active=True,
                is_default=True,
            )
            .order_by("id")
            .first()
        )
    if workspace is None:
        return None
    membership = (
        WorkspaceMembership.no_workspace_objects.filter(
            workspace_id=workspace.id,
            is_active=True,
            user__is_active=True,
            user__organization_id=project.organization_id,
        )
        .select_related("user")
        .order_by("-level", "id")
        .first()
    )
    if membership is not None:
        return Principal(workspace=workspace, user=membership.user)
    return None


def _surface(project) -> str:
    from tracer.models.project import ProjectSourceChoices

    return (
        "voice" if project.source == ProjectSourceChoices.SIMULATOR.value else "trace"
    )


def _time_filter(start: datetime, end: datetime) -> dict[str, Any]:
    return {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [start.isoformat(), end.isoformat()],
            "col_type": "SYSTEM_METRIC",
        },
    }


def _custom_filter(key: str, value: Any, value_type: str) -> dict[str, Any]:
    filter_type = {
        "string": "text",
        "number": "number",
        "boolean": "boolean",
    }.get(value_type)
    if filter_type is None:
        raise PopulationGap("no scalar custom-attribute value was available")
    if value_type == "string" and not isinstance(value, str):
        raise PopulationGap("custom-attribute value did not match its scalar type")
    if value_type == "boolean" and type(value) is not bool:
        raise PopulationGap("custom-attribute value did not match its scalar type")
    if value_type == "number":
        if type(value) not in {int, float}:
            raise PopulationGap("custom-attribute value did not match its scalar type")
        try:
            finite = math.isfinite(float(value))
        except (OverflowError, TypeError, ValueError):
            finite = False
        if not finite:
            raise PopulationGap("custom-attribute numeric value was not finite")
    if value_type == "string":
        filter_config = {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": filter_type,
            "filter_op": "in",
            "filter_value": [value],
            "attribute_value_types": [value_type],
        }
    else:
        # The public SPAN_ATTRIBUTE contract permits list membership only for
        # text. Numeric and boolean leaves use scalar equality, and storage
        # provenance is legal only alongside in/not_in list operators.
        filter_config = {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": filter_type,
            "filter_op": "equals",
            "filter_value": value,
        }
    return {
        "column_id": key,
        "filter_config": filter_config,
    }


def _system_model_filter(value: str, value_type: str) -> dict[str, Any]:
    if value_type != "string" or not value:
        raise PopulationGap("no scalar Model system value was available")
    return {
        "column_id": "model",
        "property_id": "system_attribute:traces:model",
        "source": "traces",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "text",
            "filter_op": "in",
            "filter_value": [value],
        },
    }


def _presence_filter(column_id: str, present: bool) -> dict[str, Any]:
    if column_id not in {"has_eval", "has_annotation"}:
        raise SafetyViolation("unsupported relational presence filter")
    return {
        "column_id": column_id,
        "filter_config": {
            "filter_type": "boolean",
            "filter_op": "equals",
            "filter_value": present,
        },
    }


def _relational_filter(profile: RelationalFilterProfile) -> dict[str, Any]:
    value = profile.filter_value
    if isinstance(value, tuple):
        value = list(value)
    return {
        "column_id": profile.column_id,
        "property_id": profile.property_id,
        "output_type": profile.output_type,
        "filter_config": {
            "col_type": profile.col_type,
            "filter_type": profile.filter_type,
            "filter_op": profile.filter_op,
            "filter_value": value,
        },
    }


def _metric_catalog_page(
    client: DirectDRFClient,
    *,
    category: str,
    cursor: str | None,
    lane: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    query = {
        "cursor_mode": "true",
        "project_ids": str(client.project.id),
        "per_eval_config": "true",
        "category": category,
        "page_size": METRIC_CATALOG_DISCOVERY_PAGE_SIZE,
    }
    if cursor:
        query["cursor"] = cursor
    response = client.call(
        "metrics",
        lane=lane,
        query=query,
    )
    payload = _require_status(response, lane)
    metrics = payload.get("metrics")
    if not isinstance(metrics, list) or any(
        not isinstance(item, dict) for item in metrics
    ):
        raise QualificationFailure(f"{lane} omitted metric definitions")
    has_more = payload.get("has_more")
    next_cursor = payload.get("next_cursor")
    if (
        payload.get("page_size") != METRIC_CATALOG_DISCOVERY_PAGE_SIZE
        or payload.get("total") is not None
        or payload.get("total_is_exact") is not False
        or not isinstance(has_more, bool)
        or len(metrics) > METRIC_CATALOG_DISCOVERY_PAGE_SIZE
        or (has_more and not metrics)
        or (
            has_more
            and (
                not isinstance(next_cursor, str)
                or not next_cursor
                or len(next_cursor) > 16_384
            )
        )
        or (not has_more and next_cursor is not None)
    ):
        raise QualificationFailure(f"{lane} returned an invalid catalog page")
    if (
        payload.get("query_complete") is not True
        or payload.get("query_exact") is not True
        or payload.get("query_status") != "complete"
        or payload.get("query_provenance") != "activated_property_catalog"
    ):
        raise QualificationFailure(f"{lane} omitted an exact activation proof")
    epoch = payload.get("catalog_epoch")
    revision = payload.get("catalog_revision")
    fingerprint = payload.get("activation_fingerprint")
    if (
        not isinstance(epoch, int)
        or isinstance(epoch, bool)
        or not 1 <= epoch <= 65_535
        or not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision < 1
        or not re.fullmatch(r"[0-9a-f]{64}", str(fingerprint or ""))
    ):
        raise QualificationFailure(f"{lane} returned an invalid activation identity")
    if any(item.get("category") != category for item in metrics):
        raise QualificationFailure(f"{lane} returned a definition outside its category")
    return metrics, payload


def _configured_choice_value(option: Any) -> str | int | float | bool | None:
    value = option
    if isinstance(option, dict):
        value = option.get("value")
        if value in (None, ""):
            value = option.get("label") or option.get("name")
    if isinstance(value, bool):
        return value
    if isinstance(value, (str, int, float)) and value != "":
        return value
    return None


def _eval_profile_from_metric(
    metric: dict[str, Any],
) -> RelationalFilterProfile | None:
    column_id = str(metric.get("name") or "")
    property_id = str(metric.get("property_id") or "")
    if (
        not column_id
        or metric.get("property_kind") != "eval_config"
        or property_id != f"eval_config:{column_id}"
    ):
        return None
    output_type = str(metric.get("output_type") or "").upper()
    if output_type in {"PASS_FAIL", "CHOICE", "CHOICES"}:
        filter_type = "text"
        filter_op = "in"
    elif output_type == "SCORE":
        filter_type = "number"
        filter_op = "equals"
    else:
        return None
    # The catalog defines the wire type but is not value evidence. A later
    # exact /filter_values read must supply the value; configured choice order
    # is never used directly here.
    return RelationalFilterProfile(
        property_id=property_id,
        column_id=column_id,
        col_type="EVAL_METRIC",
        filter_type=filter_type,
        filter_op=filter_op,
        filter_value=None,
        output_type=output_type,
    )


def _annotation_profile_from_metric(
    metric: dict[str, Any],
) -> RelationalFilterProfile | None:
    column_id = str(metric.get("name") or "")
    property_id = str(metric.get("property_id") or "")
    if (
        not column_id
        or metric.get("property_kind") != "annotation"
        or property_id != f"annotation:{column_id}"
    ):
        return None
    output_type = str(metric.get("output_type") or "").lower()
    shape = {
        "numeric": ("number", "equals"),
        "number": ("number", "equals"),
        "star": ("number", "equals"),
        "text": ("text", "equals"),
        "thumbs_up_down": ("thumbs", "in"),
        "categorical": ("categorical", "in"),
    }.get(output_type)
    if shape is None:
        return None
    filter_type, filter_op = shape
    return RelationalFilterProfile(
        property_id=property_id,
        column_id=column_id,
        col_type="ANNOTATION",
        filter_type=filter_type,
        filter_op=filter_op,
        filter_value=None,
        output_type=output_type,
    )


def _finite_number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _profile_with_public_value(
    profile: RelationalFilterProfile,
    value: str | int | float | bool,
) -> RelationalFilterProfile | None:
    if profile.filter_type == "number":
        filter_value: Any = _finite_number(value)
        if filter_value is None:
            return None
    elif profile.filter_type in {"thumbs", "categorical"} or (
        profile.col_type == "EVAL_METRIC" and profile.filter_type == "text"
    ):
        if not isinstance(value, (str, int, float, bool)) or value == "":
            return None
        filter_value = (value,)
    elif profile.filter_type == "text":
        if not isinstance(value, str) or not value:
            return None
        filter_value = value
    else:
        return None
    return RelationalFilterProfile(
        property_id=profile.property_id,
        column_id=profile.column_id,
        col_type=profile.col_type,
        filter_type=profile.filter_type,
        filter_op=profile.filter_op,
        filter_value=filter_value,
        output_type=profile.output_type,
    )


def _discover_public_value_candidate(
    client: DirectDRFClient,
    *,
    profile: RelationalFilterProfile,
    lane: str,
) -> RelationalFilterProfile:
    response = client.call(
        "filter_values",
        lane=lane,
        query={
            "property_id": profile.property_id,
            "source": "traces",
            "project_ids": str(client.project.id),
            "page_size": 10,
        },
    )
    payload = _require_status(response, lane)
    if (
        payload.get("query_complete") is not True
        or payload.get("query_status") != "complete"
    ):
        raise QualificationFailure(f"{lane} omitted an exact value page")
    options = payload.get("values")
    if not isinstance(options, list):
        raise QualificationFailure(f"{lane} omitted public filter values")
    if not options and (payload.get("has_more") or payload.get("next_cursor")):
        raise QualificationFailure(f"{lane} exposed continuation from an empty page")
    for option in options:
        value = _configured_choice_value(option)
        if value is None:
            continue
        materialized = _profile_with_public_value(profile, value)
        if materialized is not None:
            return materialized
    raise PopulationGap("public filter-values page had no usable exact value")


def _discover_catalog_profile(
    client: DirectDRFClient,
    *,
    category: str,
    lane: str,
    factory: Callable[[dict[str, Any]], RelationalFilterProfile | None],
) -> RelationalFilterProfile:
    attempted_values = 0
    cursor: str | None = None
    cursors: set[str] = set()
    seen_property_ids: set[str] = set()
    activation_binding: tuple[int, int, str] | None = None
    for page in range(1, METRIC_CATALOG_DISCOVERY_MAX_PAGES + 1):
        metrics, payload = _metric_catalog_page(
            client,
            category=category,
            cursor=cursor,
            lane=f"{lane}.p{page}",
        )
        binding = (
            payload["catalog_epoch"],
            payload["catalog_revision"],
            payload["activation_fingerprint"],
        )
        if activation_binding is None:
            activation_binding = binding
        elif binding != activation_binding:
            raise QualificationFailure(
                f"{lane} cursor changed the activated catalog revision"
            )
        property_ids = [str(metric.get("property_id") or "") for metric in metrics]
        if (
            any(not property_id for property_id in property_ids)
            or len(set(property_ids)) != len(property_ids)
            or seen_property_ids.intersection(property_ids)
        ):
            raise QualificationFailure(f"{lane} catalog pages overlapped")
        seen_property_ids.update(property_ids)
        for metric in sorted(
            metrics,
            key=lambda item: str(item.get("property_id") or ""),
        ):
            profile = factory(metric)
            if profile is None:
                continue
            if attempted_values >= PROFILE_VALUE_DISCOVERY_MAX_CANDIDATES:
                break
            attempted_values += 1
            try:
                return _discover_public_value_candidate(
                    client,
                    profile=profile,
                    lane=f"{lane}.value.{attempted_values}",
                )
            except PopulationGap:
                continue
        if attempted_values >= PROFILE_VALUE_DISCOVERY_MAX_CANDIDATES:
            break
        if payload["has_more"] is not True:
            break
        next_cursor = payload["next_cursor"]
        if next_cursor in cursors or next_cursor == cursor:
            raise QualificationFailure(f"{lane} catalog cursor did not advance")
        cursors.add(next_cursor)
        cursor = next_cursor
    raise PopulationGap(f"no bounded {category} filter profile was available")


def _discover_relational_profiles(
    client: DirectDRFClient, *, lane: str
) -> tuple[RelationalFilterProfile, RelationalFilterProfile]:
    eval_profile = _discover_catalog_profile(
        client,
        category="eval_metric",
        lane=f"{lane}.eval",
        factory=_eval_profile_from_metric,
    )
    annotation_profile = _discover_catalog_profile(
        client,
        category="annotation_metric",
        lane=f"{lane}.annotation",
        factory=_annotation_profile_from_metric,
    )
    return eval_profile, annotation_profile


def _discover_system_model(client: DirectDRFClient, *, lane: str) -> tuple[str, str]:
    response = client.call(
        "filter_values",
        lane=lane,
        query={
            "property_id": "system_attribute:traces:model",
            "source": "traces",
            "project_ids": str(client.project.id),
            "page_size": 10,
        },
    )
    payload = _require_status(response, lane)
    if (
        payload.get("query_complete") is not True
        or payload.get("query_status") != "complete"
    ):
        raise QualificationFailure(f"{lane} omitted an exact Model value page")
    options = payload.get("values")
    if not isinstance(options, list):
        raise QualificationFailure(f"{lane} omitted Model values")
    if not options and (payload.get("has_more") or payload.get("next_cursor")):
        raise QualificationFailure(f"{lane} exposed continuation from an empty page")
    for option in options:
        if not isinstance(option, dict):
            continue
        value = option.get("value")
        value_type = str(option.get("type") or "string")
        if isinstance(value, str) and value and value_type == "string":
            return value, value_type
    raise PopulationGap("public Model value page had no usable string value")


def _matrix_filter_profiles(
    target: Target, *, partition: str = "all"
) -> tuple[tuple[str, list[dict]], ...]:
    if partition not in {"all", "core", "system"}:
        raise SafetyViolation("unknown target matrix partition")
    if (
        target.eval_profile is None
        or target.annotation_profile is None
        or target.system_value is None
        or target.system_value_type is None
    ):
        raise PopulationGap("target omitted a required filter profile")
    custom = _custom_filter(str(target.key), target.value, str(target.value_type))
    system = _system_model_filter(target.system_value, target.system_value_type)
    eval_exact = _relational_filter(target.eval_profile)
    annotation_exact = _relational_filter(target.annotation_profile)
    eval_present = _presence_filter("has_eval", True)
    eval_absent = _presence_filter("has_eval", False)
    annotation_present = _presence_filter("has_annotation", True)
    annotation_absent = _presence_filter("has_annotation", False)
    profiles = (
        ("default", []),
        (str(TARGETS[target.name]["density"]), [custom]),
        ("f1.system", [system]),
        ("f4.system_custom", [system, custom]),
        ("f5.eval_present", [eval_present]),
        ("f5.eval_absent", [eval_absent]),
        ("f5.eval_exact", [eval_exact]),
        ("f6.annotation_present", [annotation_present]),
        ("f6.annotation_absent", [annotation_absent]),
        ("f6.annotation_exact", [annotation_exact]),
        (
            "f7.custom_eval_annotation",
            [custom, eval_exact, annotation_present, annotation_exact],
        ),
    )
    if partition == "system":
        return tuple(
            item for item in profiles if item[0] in SYSTEM_MATRIX_PROFILE_MODES
        )
    if partition == "core":
        return tuple(
            item for item in profiles if item[0] not in SYSTEM_MATRIX_PROFILE_MODES
        )
    return profiles


def _rows_and_metadata(
    endpoint: str, response: Any, lane: str
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    payload = _require_status(response, lane)
    if endpoint == "voice_list":
        rows = payload.get("results")
        metadata = payload
    else:
        rows = payload.get("table")
        metadata = payload.get("metadata") or payload
    config = payload.get("config") or payload.get("column_config") or []
    if (
        not isinstance(rows, list)
        or not isinstance(metadata, dict)
        or not isinstance(config, list)
    ):
        raise QualificationFailure(f"{lane} omitted list rows or metadata")
    if not all(isinstance(row, dict) for row in rows):
        raise QualificationFailure(f"{lane} returned a malformed list row")
    if not all(isinstance(item, dict) for item in config):
        raise QualificationFailure(f"{lane} returned malformed column metadata")
    if (
        metadata.get("query_complete") is not True
        or metadata.get("query_status") != "complete"
    ):
        raise QualificationFailure(f"{lane} omitted a complete list-page proof")
    return rows, metadata, config


def _parse_public_datetime(
    value: Any,
    *,
    lane: str,
    field: str,
    assume_utc: bool = False,
) -> datetime:
    parsed: datetime
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and "T" in value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise QualificationFailure(
                f"{lane} returned an invalid {field} timestamp"
            ) from exc
    else:
        raise QualificationFailure(f"{lane} returned an invalid {field} timestamp")
    if parsed.tzinfo is None:
        if not assume_utc:
            raise QualificationFailure(
                f"{lane} returned a timezone-free {field} timestamp"
            )
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _scalar_values(value: Any) -> tuple[Any, ...]:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(scalar for item in value for scalar in _scalar_values(item))
    if isinstance(value, dict):
        # Only documented value wrappers count as public semantic evidence.
        # Lifecycle/error dictionaries deliberately do not prove an exact value.
        return tuple(
            scalar
            for key in ("value", "values", "label", "score", "output")
            if key in value
            for scalar in _scalar_values(value[key])
        )
    return ()


def _public_value_equals(actual: Any, expected: Any) -> bool:
    if isinstance(expected, bool):
        return isinstance(actual, bool) and actual is expected
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        actual_number = _finite_number(actual)
        expected_number = _finite_number(expected)
        return (
            actual_number is not None
            and expected_number is not None
            and math.isclose(
                float(actual_number),
                float(expected_number),
                rel_tol=1e-9,
                abs_tol=1e-9,
            )
        )
    if isinstance(expected, str):
        return isinstance(actual, str) and actual.casefold() == expected.casefold()
    return actual == expected


def _configured_property_columns(
    config: list[dict[str, Any]], *, property_id: str, property_kind: str
) -> tuple[str, ...]:
    return tuple(
        str(item.get("id"))
        for item in config
        if item.get("property_id") == property_id
        and item.get("property_kind") == property_kind
        and item.get("id")
    )


def _row_timestamp(kind: str, row: dict[str, Any]) -> Any:
    candidates = {
        "trace": ("created_at", "start_time"),
        "span": ("created_at", "start_time"),
        "session": ("created_at", "start_time"),
        "voice": ("created_at", "started_at", "start_time"),
        "users": ("last_active", "activated_at"),
    }[kind]
    for key in candidates:
        if row.get(key) is not None:
            return row[key]
    return None


def _row_has_relational_value(row: dict[str, Any], columns: Iterable[str]) -> bool:
    for column in columns:
        if column not in row:
            continue
        value = row[column]
        if value not in (None, "", [], {}):
            return True
    return False


def _row_matches_filter_leaf(
    *,
    kind: str,
    row: dict[str, Any],
    config: list[dict[str, Any]],
    item: dict[str, Any],
    lane: str,
    aggregate_filter_attested: bool = False,
) -> None:
    column_id = str(item.get("column_id") or "")
    filter_config = item.get("filter_config")
    if not column_id or not isinstance(filter_config, dict):
        raise QualificationFailure(f"{lane} included an unverifiable filter leaf")
    filter_op = str(filter_config.get("filter_op") or "")
    filter_value = filter_config.get("filter_value")
    col_type = str(filter_config.get("col_type") or "").upper()

    if column_id in {"created_at", "start_time"}:
        if (
            filter_op != "between"
            or not isinstance(filter_value, list)
            or len(filter_value) != 2
        ):
            raise QualificationFailure(f"{lane} included an unverifiable time leaf")
        start = _parse_public_datetime(filter_value[0], lane=lane, field="filter-start")
        end = _parse_public_datetime(filter_value[1], lane=lane, field="filter-end")
        observed = _parse_public_datetime(
            _row_timestamp(kind, row),
            lane=lane,
            field=f"{kind}-row",
            # Direct in-process DRF callbacks can still carry a naive CH datetime;
            # the public JSON serializer treats these query-builder values as UTC.
            assume_utc=True,
        )
        if end <= start or not start <= observed < end:
            raise QualificationFailure(
                f"{lane} returned a row outside the requested time window"
            )
        return

    if column_id in {"has_eval", "has_annotation"}:
        if filter_op != "equals" or not isinstance(filter_value, bool):
            raise QualificationFailure(
                f"{lane} included an unverifiable relational-presence leaf"
            )
        property_kind = "eval_config" if column_id == "has_eval" else "annotation"
        columns = tuple(
            str(item.get("id"))
            for item in config
            if item.get("property_kind") == property_kind and item.get("id")
        )
        if not columns:
            raise QualificationFailure(
                f"{lane} omitted exhaustive {property_kind} column evidence"
            )
        if _row_has_relational_value(row, columns) is not filter_value:
            raise QualificationFailure(
                f"{lane} returned a row that violates {column_id}={filter_value}"
            )
        return

    expected_values = (
        tuple(filter_value) if isinstance(filter_value, list) else (filter_value,)
    )
    if not expected_values or any(value is None for value in expected_values):
        raise QualificationFailure(f"{lane} included an unverifiable value leaf")
    if filter_op not in {"in", "equals"}:
        raise QualificationFailure(
            f"{lane} included an unsupported semantic proof operator"
        )

    property_id = str(item.get("property_id") or "")
    candidate_columns = (column_id,)
    if col_type == "EVAL_METRIC":
        candidate_columns = _configured_property_columns(
            config, property_id=property_id, property_kind="eval_config"
        )
        if not candidate_columns:
            raise QualificationFailure(
                f"{lane} omitted namespaced eval-column evidence"
            )
        output_type = str(item.get("output_type") or "").upper()
        if output_type == "SCORE":
            # A list cell is an aggregate across the entity's eval rows, while the
            # filter matches an individual raw score. Equality of those two is not
            # a sound membership proof without a backend-applied-filter attestation.
            if not aggregate_filter_attested:
                raise QualificationFailure(
                    f"{lane} cannot prove a raw SCORE leaf from an aggregate list cell"
                )
            return
        if output_type == "PASS_FAIL":
            value = _finite_number(row.get(column_id))
            if value is None:
                raise QualificationFailure(
                    f"{lane} omitted the filtered PASS_FAIL public value"
                )
            accepted = False
            for expected in expected_values:
                token = str(expected).strip().casefold()
                if token in {"passed", "pass", "true", "1"} and value > 0:
                    accepted = True
                if token in {"failed", "fail", "false", "0"} and value < 100:
                    accepted = True
            if not accepted:
                raise QualificationFailure(
                    f"{lane} returned a row that violates its PASS_FAIL leaf"
                )
            return
        if output_type in {"CHOICE", "CHOICES"}:
            for expected in expected_values:
                choice_column = f"{column_id}**{expected}"
                if choice_column not in candidate_columns:
                    continue
                percentage = _finite_number(row.get(choice_column))
                if percentage is not None and percentage > 0:
                    return
            raise QualificationFailure(
                f"{lane} returned a row that violates its CHOICES leaf"
            )
    elif col_type == "ANNOTATION":
        candidate_columns = _configured_property_columns(
            config, property_id=property_id, property_kind="annotation"
        )
        if not candidate_columns:
            raise QualificationFailure(
                f"{lane} omitted namespaced annotation-column evidence"
            )
        output_type = str(item.get("output_type") or "").lower()
        if kind in {"trace", "voice"} and output_type in {
            "numeric",
            "number",
            "star",
        }:
            # Trace/voice cells publish an average across raw annotation scores.
            # An average equal to the requested raw value does not prove any
            # contributing score matched that leaf.
            if not aggregate_filter_attested:
                raise QualificationFailure(
                    f"{lane} cannot prove a raw numeric annotation leaf from an aggregate list cell"
                )
            return
        if kind in {"trace", "voice"} and output_type in {
            "categorical",
            "thumbs_up_down",
        }:
            if any(
                _public_value_equals(actual, expected)
                for column in candidate_columns
                if column in row and not isinstance(row[column], dict)
                for actual in _scalar_values(row[column])
                for expected in expected_values
            ):
                return
            aggregate_cells = [
                row[column]
                for column in candidate_columns
                if isinstance(row.get(column), dict)
            ]
            for expected in expected_values:
                if output_type == "categorical" and any(
                    (_finite_number(cell.get(str(expected))) or 0) > 0
                    for cell in aggregate_cells
                ):
                    return
                token = str(expected).strip().casefold()
                if isinstance(expected, bool):
                    token = "up" if expected else "down"
                if output_type == "thumbs_up_down":
                    count_key = (
                        "thumbs_up"
                        if token in {"up", "true", "1"}
                        else "thumbs_down"
                        if token in {"down", "false", "0"}
                        else None
                    )
                    if count_key and any(
                        (_finite_number(cell.get(count_key)) or 0) > 0
                        for cell in aggregate_cells
                    ):
                        return
            raise QualificationFailure(
                f"{lane} returned a row that violates its aggregate annotation leaf"
            )

    observed_values = tuple(
        scalar
        for column in candidate_columns
        if column in row
        for scalar in _scalar_values(row[column])
    )
    if observed_values and any(
        _public_value_equals(actual, expected)
        for actual in observed_values
        for expected in expected_values
    ):
        return
    voice_attestation_substitute = (
        kind == "voice"
        and aggregate_filter_attested
        and (
            col_type == "SPAN_ATTRIBUTE"
            or (
                column_id == "model"
                and col_type == "SYSTEM_METRIC"
                and item.get("property_id") == "system_attribute:traces:model"
                and item.get("source") == "traces"
            )
        )
    )
    if voice_attestation_substitute:
        return
    raise QualificationFailure(
        f"{lane} returned a row that violates its {column_id} filter leaf"
    )


def _verify_list_row_semantics(
    *,
    kind: str,
    rows: list[dict[str, Any]],
    config: list[dict[str, Any]],
    project_id: str,
    filters: list[dict[str, Any]],
    lane: str,
    aggregate_filter_attested: bool = False,
) -> tuple[str, ...]:
    digests: list[str] = []
    for row in rows:
        row_project_id = row.get("project_id")
        if row_project_id is None:
            raise QualificationFailure(
                f"{lane} {kind} row omitted public project_id evidence"
            )
        if str(row_project_id) != project_id:
            raise QualificationFailure(f"{lane} returned a cross-project row")
        for item in filters:
            _row_matches_filter_leaf(
                kind=kind,
                row=row,
                config=config,
                item=item,
                lane=lane,
                aggregate_filter_attested=aggregate_filter_attested,
            )
        digests.append(
            _digest(
                {
                    "project_id": project_id,
                    "filters": filters,
                    "row": row,
                },
                64,
            )
        )
    return tuple(digests)


def _row_identity(kind: str, row: dict[str, Any], project_id: str) -> str:
    if kind in {"trace", "voice"}:
        identity = row.get("trace_id") or row.get("id")
    elif kind == "session":
        identity = row.get("trace_session_id") or row.get("session_id") or row.get("id")
    elif kind == "users":
        identity = f"{row.get('project_id') or project_id}:{row.get('end_user_id')}"
    elif kind == "span":
        identity = "|".join(
            str(row.get(key) or "")
            for key in ("project_id", "trace_id", "span_id", "start_time")
        )
    else:
        raise SafetyViolation(f"unknown row identity kind: {kind}")
    if not identity:
        raise QualificationFailure(f"{kind} row omitted its public identity")
    return str(identity)


def _list_continuation(
    metadata: dict[str, Any], *, lane: str
) -> tuple[bool, str | None]:
    has_more = metadata.get("has_more")
    next_cursor = metadata.get("next_cursor")
    if not isinstance(has_more, bool):
        raise QualificationFailure(f"{lane} omitted truthful continuation metadata")
    if has_more:
        if (
            not isinstance(next_cursor, str)
            or not next_cursor
            or len(next_cursor) > 16_384
        ):
            raise QualificationFailure(f"{lane} omitted a bounded continuation cursor")
    elif next_cursor is not None:
        raise QualificationFailure(f"{lane} terminal page exposed a cursor")
    return has_more, next_cursor


def _canonical_filter_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise QualificationFailure("filter evidence contained a non-finite number")
        return int(value) if value.is_integer() else value
    if isinstance(value, dict):
        return {str(key): _canonical_filter_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_filter_value(item) for item in value]
    return str(value)


def _canonical_filter_json(value: Any) -> str:
    return json.dumps(
        _canonical_filter_value(value),
        sort_keys=True,
        separators=(",", ":"),
    )


def _applied_filter_leaves(
    filters: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    leaves: list[dict[str, Any]] = []
    for item in filters:
        if not isinstance(item, dict):
            raise QualificationFailure("filter evidence contained a malformed leaf")
        column_id = item.get("column_id") or item.get("columnId")
        config = item.get("filter_config") or item.get("filterConfig") or {}
        operator = config.get("filter_op") or config.get("filterOp")
        datetime_complement = column_id in {"created_at", "start_time"} and (
            operator in {"not_equals", "not_between", "is_null"}
        )
        if column_id in {"created_at", "start_time"} and not datetime_complement:
            continue
        leaves.append(_canonical_filter_value(item))
    return sorted(leaves, key=_canonical_filter_json)


def _filter_binding_sha256(
    *, project_id: str, kind: str, filters: list[dict[str, Any]]
) -> tuple[str, int]:
    leaves = _applied_filter_leaves(filters)
    return (
        _digest(
            {
                "project_id": project_id,
                "observe_type": kind,
                "filters": leaves,
            },
            64,
        ),
        len(leaves),
    )


def _require_list_filter_attestation(
    metadata: dict[str, Any],
    *,
    project_id: str,
    kind: str,
    filters: list[dict[str, Any]],
    lane: str,
) -> bool:
    if kind not in {"trace", "span", "session", "voice"}:
        return False
    expected_digest, expected_count = _filter_binding_sha256(
        project_id=project_id,
        kind=kind,
        filters=filters,
    )
    if expected_count == 0:
        return False
    if (
        metadata.get("query_applied_filter_version") != FILTER_ATTESTATION_VERSION
        or metadata.get("query_applied_filter_sha256") != expected_digest
        or not isinstance(metadata.get("query_applied_filter_count"), int)
        or isinstance(metadata.get("query_applied_filter_count"), bool)
        or metadata.get("query_applied_filter_count") != expected_count
    ):
        raise QualificationFailure(
            f"{lane} omitted a response-bound applied-filter proof"
        )
    return True


def _list_query(
    *,
    kind: str,
    project_id: str,
    filters: list[dict[str, Any]],
    cursor: str | None,
    page_size: int = 5,
) -> dict[str, Any]:
    query: dict[str, Any] = {
        "project_id": project_id,
        "page_size": page_size,
        "cursor_mode": "true",
        "filters": json.dumps(filters, separators=(",", ":")),
    }
    if kind == "voice":
        query.update(page=1, remove_simulation_calls="false")
    elif kind == "users":
        query.update(current_page_index=0)
    else:
        query.update(page_number=0)
        if kind in {"trace", "session"}:
            query["interval"] = "month"
    if kind in {"trace", "voice"}:
        attribute_keys = tuple(
            dict.fromkeys(
                str(item.get("column_id"))
                for item in filters
                if isinstance(item.get("filter_config"), dict)
                and str(item["filter_config"].get("col_type") or "").upper()
                == "SPAN_ATTRIBUTE"
                and item.get("column_id")
            )
        )
        if attribute_keys:
            query["attribute_keys"] = json.dumps(attribute_keys, separators=(",", ":"))
    if cursor:
        query["cursor"] = cursor
        query.pop("page", None)
        query.pop("page_number", None)
        query.pop("current_page_index", None)
    return query


def _qualify_list_protocol(
    client: DirectDRFClient,
    *,
    kind: str,
    filters: list[dict[str, Any]],
    lane: str,
) -> dict[str, Any]:
    endpoint = {
        "trace": "trace_list",
        "span": "span_list",
        "session": "session_list",
        "voice": "voice_list",
        "users": "users",
    }[kind]

    def fetch(cursor: str | None, suffix: str):
        return client.call(
            endpoint,
            lane=f"{lane}.{suffix}",
            query=_list_query(
                kind=kind,
                project_id=str(client.project.id),
                filters=filters,
                cursor=cursor,
            ),
        )

    first_response = fetch(None, "p1")
    first_rows, first_metadata, first_config = _rows_and_metadata(
        endpoint, first_response, lane
    )
    if len(first_rows) > 5:
        raise QualificationFailure(f"{lane} exceeded its requested page size")
    has_more, next_cursor = _list_continuation(first_metadata, lane=lane)
    first_filter_attested = _require_list_filter_attestation(
        first_metadata,
        project_id=str(client.project.id),
        kind=kind,
        filters=filters,
        lane=f"{lane}.p1",
    )
    first_semantic_digests = _verify_list_row_semantics(
        kind=kind,
        rows=first_rows,
        config=first_config,
        project_id=str(client.project.id),
        filters=filters,
        lane=lane,
        aggregate_filter_attested=first_filter_attested,
    )
    if not first_rows and first_metadata.get("query_exact") is False:
        raise QualificationFailure(
            f"{lane} returned an empty page without an exact complete proof"
        )
    first_ids = tuple(
        _row_identity(kind, row, str(client.project.id)) for row in first_rows
    )
    repeat_response = fetch(None, "p1_repeat")
    repeat_rows, repeat_metadata, repeat_config = _rows_and_metadata(
        endpoint, repeat_response, lane
    )
    if len(repeat_rows) > 5:
        raise QualificationFailure(f"{lane} repeat exceeded its requested page size")
    repeat_has_more, repeat_cursor = _list_continuation(
        repeat_metadata,
        lane=f"{lane}.p1_repeat",
    )
    repeat_filter_attested = _require_list_filter_attestation(
        repeat_metadata,
        project_id=str(client.project.id),
        kind=kind,
        filters=filters,
        lane=f"{lane}.p1_repeat",
    )
    repeat_semantic_digests = _verify_list_row_semantics(
        kind=kind,
        rows=repeat_rows,
        config=repeat_config,
        project_id=str(client.project.id),
        filters=filters,
        lane=lane,
        aggregate_filter_attested=repeat_filter_attested,
    )
    repeat_ids = tuple(
        _row_identity(kind, row, str(client.project.id)) for row in repeat_rows
    )
    if repeat_ids != first_ids:
        raise QualificationFailure(f"{lane} page-one repeat changed physical order")
    if len(set(first_ids)) != len(first_ids):
        raise QualificationFailure(f"{lane} repeated an identity within page one")
    if repeat_semantic_digests != first_semantic_digests:
        raise QualificationFailure(f"{lane} page-one repeat changed semantic evidence")
    if repeat_has_more != has_more:
        raise QualificationFailure(f"{lane} repeat changed continuation truth")
    if not repeat_rows and repeat_metadata.get("query_exact") is False:
        raise QualificationFailure(
            f"{lane} repeated an empty page without an exact proof"
        )

    if (
        not first_ids
        and (has_more or next_cursor)
        and kind not in EMPTY_CURSOR_CHECKPOINT_KINDS
    ):
        raise QualificationFailure(f"{lane} exposed continuation from an empty page")
    second_ids: tuple[str, ...] = ()
    second_semantic_digests: tuple[str, ...] = ()
    second_cursor = None
    if has_more:
        if not isinstance(next_cursor, str) or not isinstance(repeat_cursor, str):
            raise QualificationFailure(f"{lane} omitted an advancing signed cursor")

        def read_continuation(cursor: str, suffix: str):
            response = fetch(cursor, suffix)
            rows, metadata, config = _rows_and_metadata(endpoint, response, lane)
            if len(rows) > 5:
                raise QualificationFailure(
                    f"{lane} continuation exceeded its page size"
                )
            page_has_more, page_cursor = _list_continuation(
                metadata,
                lane=f"{lane}.{suffix}",
            )
            filter_attested = _require_list_filter_attestation(
                metadata,
                project_id=str(client.project.id),
                kind=kind,
                filters=filters,
                lane=f"{lane}.{suffix}",
            )
            semantic_digests = _verify_list_row_semantics(
                kind=kind,
                rows=rows,
                config=config,
                project_id=str(client.project.id),
                filters=filters,
                lane=lane,
                aggregate_filter_attested=filter_attested,
            )
            if not rows and metadata.get("query_exact") is False:
                raise QualificationFailure(
                    f"{lane} continuation returned an empty page without an exact proof"
                )
            identities = tuple(
                _row_identity(kind, row, str(client.project.id)) for row in rows
            )
            if not identities and kind not in EMPTY_CURSOR_CHECKPOINT_KINDS:
                raise QualificationFailure(f"{lane} continuation returned no rows")
            if len(set(identities)) != len(identities):
                raise QualificationFailure(
                    f"{lane} repeated an identity within its continuation"
                )
            if set(first_ids) & set(identities):
                raise QualificationFailure(f"{lane} continuation overlapped page one")
            if page_has_more and page_cursor == cursor:
                raise QualificationFailure(f"{lane} second cursor did not advance")
            return identities, semantic_digests, page_has_more, page_cursor

        (
            second_ids,
            second_semantic_digests,
            second_has_more,
            second_cursor,
        ) = read_continuation(next_cursor, "p2")
        (
            repeat_second_ids,
            repeat_second_semantic_digests,
            repeat_second_has_more,
            _repeat_second_cursor,
        ) = read_continuation(repeat_cursor, "p1_repeat_p2")
        if (
            repeat_second_ids != second_ids
            or repeat_second_semantic_digests != second_semantic_digests
            or repeat_second_has_more != second_has_more
        ):
            raise QualificationFailure(
                f"{lane} timestamped repeat cursors changed continuation semantics"
            )
    return {
        "qualified": True,
        "kind": kind,
        "positive": bool(first_ids or second_ids),
        "p1_rows": len(first_ids),
        "p2_rows": len(second_ids),
        "p1_repeat_equal": True,
        "continuation_exercised": has_more,
        "no_page_overlap": not bool(set(first_ids) & set(second_ids)),
        "row_identity_digests": sorted(
            {_digest(identity) for identity in (*first_ids, *second_ids)}
        ),
        "semantic_row_digests": sorted(
            {*first_semantic_digests, *second_semantic_digests}
        ),
        "semantic_filter_sha256": _digest(filters, 64),
        "first_cursor_digest": _digest(next_cursor) if next_cursor else None,
        "second_cursor_digest": _digest(second_cursor) if second_cursor else None,
    }


def _qualify_list_first_page(
    client: DirectDRFClient,
    *,
    kind: str,
    filters: list[dict[str, Any]],
    lane: str,
) -> dict[str, Any]:
    endpoint = {
        "trace": "trace_list",
        "span": "span_list",
        "session": "session_list",
        "voice": "voice_list",
        "users": "users",
    }[kind]
    response = client.call(
        endpoint,
        lane=f"{lane}.p1",
        query=_list_query(
            kind=kind,
            project_id=str(client.project.id),
            filters=filters,
            cursor=None,
        ),
    )
    rows, metadata, config = _rows_and_metadata(endpoint, response, lane)
    if len(rows) > 5:
        raise QualificationFailure(f"{lane} exceeded its requested page size")
    has_more, next_cursor = _list_continuation(metadata, lane=lane)
    filter_attested = _require_list_filter_attestation(
        metadata,
        project_id=str(client.project.id),
        kind=kind,
        filters=filters,
        lane=f"{lane}.p1",
    )
    semantic_digests = _verify_list_row_semantics(
        kind=kind,
        rows=rows,
        config=config,
        project_id=str(client.project.id),
        filters=filters,
        lane=lane,
        aggregate_filter_attested=filter_attested,
    )
    if not rows and metadata.get("query_exact") is False:
        raise QualificationFailure(
            f"{lane} returned an empty page without an exact complete proof"
        )
    identities = tuple(_row_identity(kind, row, str(client.project.id)) for row in rows)
    if len(set(identities)) != len(identities):
        raise QualificationFailure(f"{lane} repeated an identity within page one")
    if not identities and (has_more or next_cursor):
        raise QualificationFailure(f"{lane} exposed continuation from an empty page")
    return {
        "qualified": True,
        "kind": kind,
        "positive": bool(identities),
        "p1_rows": len(identities),
        "p2_rows": 0,
        "p1_repeat_equal": None,
        "continuation_exercised": False,
        "continuation_available": has_more,
        "no_page_overlap": None,
        "row_identity_digests": sorted({_digest(identity) for identity in identities}),
        "semantic_row_digests": sorted(set(semantic_digests)),
        "semantic_filter_sha256": _digest(filters, 64),
        "first_cursor_digest": _digest(next_cursor) if next_cursor else None,
        "second_cursor_digest": None,
    }


def _property_key_page(
    client: DirectDRFClient,
    *,
    lane: str,
    q: str | None = None,
    cursor: str | None = None,
    page_size: int | None = 25,
) -> tuple[list[dict], dict]:
    query: dict[str, Any] = {
        "project_id": str(client.project.id),
        "discovery_mode": "filter",
    }
    if page_size is not None:
        query["page_size"] = page_size
    if q:
        query["q"] = q
    if cursor:
        query["cursor"] = cursor
    response = client.call("property_keys", lane=lane, query=query)
    payload = _require_status(response, lane)
    if (
        payload.get("query_complete") is not True
        or payload.get("query_status") != "complete"
    ):
        raise QualificationFailure(f"{lane} omitted a complete property-key proof")
    rows = payload.get("result")
    if not isinstance(rows, list):
        raise QualificationFailure(f"{lane} omitted property key rows")
    return [row for row in rows if isinstance(row, dict)], payload


def _qualify_key_read_more(client: DirectDRFClient, *, lane: str) -> dict[str, Any]:
    first_rows, first = _property_key_page(client, lane=f"{lane}.p1")
    repeat_rows, repeat = _property_key_page(client, lane=f"{lane}.p1_repeat")
    first_keys = tuple((row.get("key"), row.get("type")) for row in first_rows)
    repeat_keys = tuple((row.get("key"), row.get("type")) for row in repeat_rows)
    if not first_keys or first_keys != repeat_keys:
        raise QualificationFailure("property-key page-one repeat was not stable")
    if len(set(first_keys)) != len(first_keys):
        raise QualificationFailure("property-key page one repeated an identity")
    first_has_more, first_cursor = _list_continuation(first, lane=f"{lane}.p1")
    repeat_has_more, repeat_cursor = _list_continuation(
        repeat,
        lane=f"{lane}.p1_repeat",
    )
    if first_has_more != repeat_has_more:
        raise QualificationFailure("property-key continuation truth changed on repeat")
    second_count = 0
    if first_has_more:
        if not isinstance(first_cursor, str) or not isinstance(repeat_cursor, str):
            raise QualificationFailure("property-key page omitted its cursor")

        def read_continuation(cursor: str, suffix: str):
            rows, payload = _property_key_page(
                client,
                lane=f"{lane}.{suffix}",
                cursor=cursor,
            )
            keys = tuple((row.get("key"), row.get("type")) for row in rows)
            page_has_more, page_cursor = _list_continuation(
                payload,
                lane=f"{lane}.{suffix}",
            )
            if not keys or len(set(keys)) != len(keys) or set(first_keys) & set(keys):
                raise QualificationFailure(
                    "property-key read-more overlapped or was empty"
                )
            if page_has_more and page_cursor == cursor:
                raise QualificationFailure("property-key cursor failed to advance")
            return keys, page_has_more

        second_keys, second_has_more = read_continuation(first_cursor, "p2")
        repeat_second_keys, repeat_second_has_more = read_continuation(
            repeat_cursor,
            "p1_repeat_p2",
        )
        if (
            repeat_second_keys != second_keys
            or repeat_second_has_more != second_has_more
        ):
            raise QualificationFailure(
                "property-key timestamped repeat cursors changed continuation semantics"
            )
        second_count = len(second_keys)
    return {
        "qualified": True,
        "p1_count": len(first_keys),
        "p2_count": second_count,
        "continuation_exercised": first_has_more,
    }


def _discover_property_profile(
    client: DirectDRFClient,
    preferred_keys: Iterable[str],
    *,
    lane: str,
) -> tuple[str, Any, str]:
    selected_key = None
    selected_type = None
    for key in preferred_keys:
        rows, payload = _property_key_page(
            client,
            lane=f"{lane}.key.{_digest(key)}",
            q=key,
            page_size=None,
        )
        matches = [row for row in rows if row.get("key") == key]
        if matches:
            selected_key = key
            selected_type = str(matches[0].get("type") or "string")
            break
        if payload.get("exact_match") is True:
            raise QualificationFailure("exact property response omitted its match")
    if not selected_key:
        raise PopulationGap("none of the preferred custom keys was present")
    query: dict[str, Any] = {
        "metric_name": selected_key,
        "metric_type": "custom_attribute",
        "source": "traces",
        "project_ids": str(client.project.id),
        "page_size": 10,
    }
    if selected_type in {"string", "number", "boolean"}:
        query["attribute_type"] = selected_type
    response = client.call("filter_values", lane=f"{lane}.values.p1", query=query)
    payload = _require_status(response, f"{lane}.values.p1")
    values = payload.get("values")
    if not isinstance(values, list):
        raise QualificationFailure("property value response omitted values")
    for option in values:
        if not isinstance(option, dict):
            continue
        value = option.get("value")
        value_type = str(option.get("type") or selected_type or "string")
        if value_type in {"string", "number", "boolean"} and value is not None:
            return selected_key, value, value_type
    raise PopulationGap("preferred custom key had no scalar filter value")


def _qualify_model_values(
    client: DirectDRFClient,
    *,
    lane: str,
    page_size: int = 10,
) -> dict[str, Any]:
    if type(page_size) is not int or page_size not in {1, 10}:
        raise SafetyViolation("Model value page size is invalid")
    base_query = {
        "property_id": "system_attribute:traces:model",
        "source": "traces",
        "project_ids": str(client.project.id),
        "page_size": page_size,
    }
    first_response = client.call("filter_values", lane=f"{lane}.p1", query=base_query)
    first = _require_status(first_response, f"{lane}.p1")
    repeat_response = client.call(
        "filter_values", lane=f"{lane}.p1_repeat", query=base_query
    )
    repeat = _require_status(repeat_response, f"{lane}.p1_repeat")

    def validate_catalog_page(payload: dict[str, Any], suffix: str) -> tuple[Any, ...]:
        epoch = payload.get("catalog_epoch")
        revision = payload.get("catalog_revision")
        fingerprint = payload.get("activation_fingerprint")
        attribute_types = payload.get("attribute_types")
        values = payload.get("values")
        has_more = payload.get("has_more")
        next_cursor = payload.get("next_cursor")
        query_count = payload.get("query_count")
        if (
            payload.get("query_complete") is not True
            or payload.get("query_status") != "complete"
            or payload.get("query_provenance") != "activated_property_catalog"
            or not isinstance(epoch, int)
            or isinstance(epoch, bool)
            or not 1 <= epoch <= 65_535
            or not isinstance(revision, int)
            or isinstance(revision, bool)
            or revision < 1
            or not re.fullmatch(r"[0-9a-f]{64}", str(fingerprint or ""))
            or attribute_types != ["string"]
            or payload.get("attribute_types_exact") is not True
            or not isinstance(values, list)
            or any(not isinstance(item, dict) for item in values)
            or len(values) > page_size
            or not isinstance(has_more, bool)
            or (has_more and (not isinstance(next_cursor, str) or not next_cursor))
            or (not has_more and next_cursor is not None)
            or payload.get("browse_status")
            != ("continuation" if has_more else "exhausted")
            or type(query_count) is not int
            or query_count != MODEL_VALUE_EXPECTED_ACTIVATED_QUERY_COUNT
        ):
            raise QualificationFailure(
                f"{lane}.{suffix} omitted an activated Model value proof"
            )
        return epoch, revision, fingerprint

    first_binding = validate_catalog_page(first, "p1")
    repeat_binding = validate_catalog_page(repeat, "p1_repeat")
    if first_binding != repeat_binding:
        raise QualificationFailure("Model repeat changed activation lineage")
    first_values = tuple(
        (item.get("value"), item.get("type"))
        for item in first.get("values") or []
        if isinstance(item, dict)
    )
    repeat_values = tuple(
        (item.get("value"), item.get("type"))
        for item in repeat.get("values") or []
        if isinstance(item, dict)
    )
    if not first_values or first_values != repeat_values:
        raise QualificationFailure("Model values were absent or unstable")
    first_has_more = first["has_more"]
    repeat_has_more = repeat["has_more"]
    if first_has_more != repeat_has_more:
        raise QualificationFailure("Model continuation truth changed on repeat")
    second_count = 0
    if first_has_more:
        first_cursor = first.get("next_cursor")
        repeat_cursor = repeat.get("next_cursor")
        if not isinstance(first_cursor, str) or not isinstance(repeat_cursor, str):
            raise QualificationFailure("Model values omitted continuation cursor")

        def read_continuation(cursor: str, suffix: str):
            response = client.call(
                "filter_values",
                lane=f"{lane}.{suffix}",
                query={**base_query, "cursor": cursor},
            )
            payload = _require_status(response, f"{lane}.{suffix}")
            if validate_catalog_page(payload, suffix) != first_binding:
                raise QualificationFailure("Model cursor changed activation lineage")
            values = tuple(
                (item.get("value"), item.get("type"))
                for item in payload.get("values") or []
                if isinstance(item, dict)
            )
            if not values or set(first_values) & set(values):
                raise QualificationFailure("Model continuation overlapped or was empty")
            page_cursor = payload.get("next_cursor")
            if payload["has_more"] and page_cursor == cursor:
                raise QualificationFailure(
                    "Model continuation cursor failed to advance"
                )
            return values, payload["has_more"]

        second_values, second_has_more = read_continuation(first_cursor, "p2")
        repeat_second_values, repeat_second_has_more = read_continuation(
            repeat_cursor,
            "p1_repeat_p2",
        )
        if (
            repeat_second_values != second_values
            or repeat_second_has_more != second_has_more
        ):
            raise QualificationFailure(
                "Model timestamped repeat cursors changed continuation semantics"
            )
        second_count = len(second_values)

    search_response = client.call(
        "filter_values",
        lane=f"{lane}.search",
        query={**base_query, "search": str(first_values[0][0])},
    )
    search_payload = _require_status(search_response, f"{lane}.search")
    validate_catalog_page(search_payload, "search")
    search_values = {
        (item.get("value"), item.get("type"))
        for item in search_payload.get("values") or []
        if isinstance(item, dict)
    }
    if first_values[0] not in search_values:
        raise QualificationFailure("Model value search omitted its exact value")
    return {
        "qualified": True,
        "activation_fingerprint_digest": _digest(first_binding[2]),
        "catalog_epoch": first_binding[0],
        "catalog_read_mode": "read",
        "catalog_revision": first_binding[1],
        "p1_values": len(first_values),
        "p2_values": second_count,
        "page_size": page_size,
        "continuation_exercised": first_has_more,
        "search_proven": True,
    }


def _interval_for_window(name: str) -> str:
    if name in {"30m", "1h", "6h", "24h"}:
        return "hour"
    if name in {"7d", "30d", "90d"}:
        return "day"
    return "month"


def _graph_bucket_floor(value: datetime, interval: str) -> datetime:
    value = value.astimezone(UTC)
    if interval == "hour":
        return value.replace(minute=0, second=0, microsecond=0)
    if interval == "day":
        return value.replace(hour=0, minute=0, second=0, microsecond=0)
    if interval == "week":
        value -= timedelta(days=value.weekday())
        return value.replace(hour=0, minute=0, second=0, microsecond=0)
    if interval == "month":
        return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    raise SafetyViolation("unsupported graph interval")


def _next_graph_bucket(value: datetime, interval: str) -> datetime:
    if interval == "hour":
        return value + timedelta(hours=1)
    if interval == "day":
        return value + timedelta(days=1)
    if interval == "week":
        return value + timedelta(weeks=1)
    if interval == "month":
        if value.month == 12:
            return value.replace(year=value.year + 1, month=1)
        return value.replace(month=value.month + 1)
    raise SafetyViolation("unsupported graph interval")


def _expected_graph_timestamps(
    start: datetime, end: datetime, interval: str
) -> tuple[datetime, ...]:
    start = start.astimezone(UTC)
    end = end.astimezone(UTC)
    if end < start:
        raise SafetyViolation("graph qualification window is inverted")
    current = _graph_bucket_floor(start, interval)
    timestamps: list[datetime] = []
    while current <= end:
        timestamps.append(current)
        current = _next_graph_bucket(current, interval)
    return tuple(timestamps)


def _graph_filter_binding_sha256(
    *, project_id: str, kind: str, filters: list[dict[str, Any]]
) -> str:
    digest, _count = _filter_binding_sha256(
        project_id=project_id,
        kind=kind,
        filters=filters,
    )
    return digest


def _validate_graph_time_contract(
    payload: dict[str, Any],
    *,
    points: list[dict[str, Any]],
    start: datetime,
    end: datetime,
    interval: str,
    lane: str,
) -> tuple[str, ...]:
    published_start = _parse_public_datetime(
        payload.get("query_window_start"), lane=lane, field="query-window-start"
    )
    published_end = _parse_public_datetime(
        payload.get("query_window_end"), lane=lane, field="query-window-end"
    )
    expected_start = start.astimezone(UTC)
    expected_end = end.astimezone(UTC)
    if published_start != expected_start or published_end != expected_end:
        raise QualificationFailure(f"{lane} returned a different graph window")

    expected = _expected_graph_timestamps(start, end, interval)
    observed = tuple(
        _parse_public_datetime(
            point.get("timestamp"),
            lane=lane,
            field="graph-point",
            # Existing graph builders serialize UTC bucket datetimes without a
            # suffix. Their query-window proof supplies the UTC frame.
            assume_utc=True,
        )
        for point in points
    )
    if observed != expected:
        raise QualificationFailure(
            f"{lane} returned non-contiguous, unordered, duplicate, or out-of-window graph buckets"
        )
    return tuple(timestamp.isoformat() for timestamp in observed)


def _qualify_graph(
    client: DirectDRFClient,
    *,
    kind: str,
    start: datetime,
    end: datetime,
    window_name: str,
    lane: str,
    filters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    profile_filters = list(filters or [])
    request_filters = [_time_filter(start, end), *profile_filters]
    metric = {
        "trace": "latency",
        "span": "latency",
        "session": "session_count",
    }[kind]
    endpoint = {
        "trace": "trace_graph",
        "span": "span_graph",
        "session": "session_graph",
    }[kind]
    body = {
        "project_id": str(client.project.id),
        "filters": request_filters,
        "interval": _interval_for_window(window_name),
        "property": "average",
        "req_data_config": {"id": metric, "type": "SYSTEM_METRIC"},
    }
    response = client.call(
        endpoint,
        lane=lane,
        query={"allow_sampled": "false", "refresh": "false"},
        body=body,
    )
    payload = _require_status(response, lane)
    if (
        payload.get("query_complete") is not True
        or payload.get("query_status") != "complete"
        or payload.get("query_sampled") is not False
    ):
        raise QualificationFailure(
            f"{lane} omitted complete, non-sampled graph metadata"
        )
    if payload.get("metric_name") != metric:
        raise QualificationFailure(f"{lane} returned the wrong graph metric")
    query_exact = payload.get("query_exact")
    query_provenance = payload.get("query_provenance")
    if query_exact is True:
        read_mode = "exact"
    elif (
        not profile_filters
        and query_exact is False
        and query_provenance == "materialized_rollup"
    ):
        read_mode = "materialized_rollup"
    else:
        raise QualificationFailure(
            f"{lane} omitted exact or approved-rollup graph metadata"
        )
    filter_binding_sha256 = None
    if profile_filters:
        filter_binding_sha256 = _graph_filter_binding_sha256(
            project_id=str(client.project.id),
            kind=kind,
            filters=profile_filters,
        )
        if (
            payload.get("query_applied_filter_version") != FILTER_ATTESTATION_VERSION
            or payload.get("query_applied_filter_sha256") != filter_binding_sha256
            or not isinstance(payload.get("query_applied_filter_count"), int)
            or isinstance(payload.get("query_applied_filter_count"), bool)
            or payload.get("query_applied_filter_count") != len(profile_filters)
        ):
            raise QualificationFailure(
                f"{lane} omitted a response-bound applied-filter proof"
            )
    data = payload.get("data")
    if not isinstance(data, list):
        raise QualificationFailure(f"{lane} omitted graph points")
    points = [point for point in data if isinstance(point, dict)]
    if len(points) != len(data):
        raise QualificationFailure(f"{lane} returned malformed graph points")
    normalized_timestamps = _validate_graph_time_contract(
        payload,
        points=points,
        start=start,
        end=end,
        interval=body["interval"],
        lane=lane,
    )
    positive_points = 0
    for point in points:
        raw_value = point.get("value")
        raw_traffic = point.get("primary_traffic")
        value = _finite_number(raw_value)
        traffic = _finite_number(raw_traffic)
        if (
            not isinstance(raw_value, (int, float))
            or isinstance(raw_value, bool)
            or value is None
            or value < 0
        ):
            raise QualificationFailure(f"{lane} returned a non-finite graph value")
        if (
            not isinstance(raw_traffic, int)
            or isinstance(raw_traffic, bool)
            or traffic is None
            or traffic < 0
        ):
            raise QualificationFailure(f"{lane} returned invalid graph traffic")
        if (value is not None and value > 0) or (traffic is not None and traffic > 0):
            positive_points += 1
    return {
        "qualified": True,
        "kind": kind,
        "metric": metric,
        "data_digest": _digest(data),
        "timestamp_digest": _digest(normalized_timestamps),
        "read_mode": read_mode,
        "profile_filter_count": len(profile_filters),
        "profile_filter_sha256": filter_binding_sha256,
        "point_count": len(points),
        "positive_point_count": positive_points,
        "positive": positive_points > 0,
    }


def _qualify_dashboard_query(
    client: DirectDRFClient,
    *,
    start: datetime,
    end: datetime,
    window_name: str,
    lane: str,
    filters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Qualify the public widget-query path against its exact SELECT reader."""

    profile_filters = list(filters or [])
    granularity = _interval_for_window(window_name)
    body = {
        "project_ids": [str(client.project.id)],
        "time_range": {
            "custom_start": start.isoformat(),
            "custom_end": end.isoformat(),
        },
        "granularity": granularity,
        "metrics": [
            {
                "id": "latency",
                "name": "latency",
                "type": "system_metric",
                "source": "traces",
                "aggregation": "avg",
            }
        ],
        "filters": profile_filters,
        "breakdowns": [],
        "allow_sampled": False,
    }
    response = client.call(
        "dashboard_query",
        lane=lane,
        query={"refresh": "false"},
        body=body,
    )
    payload = _require_status(response, lane)
    if (
        payload.get("query_complete") is not True
        or payload.get("query_status") != "complete"
        or payload.get("query_sampled") is not False
        or payload.get("query_exact") is not True
        or payload.get("query_provenance") != "exact_snapshot"
    ):
        raise QualificationFailure(
            f"{lane} omitted a complete, exact dashboard-query proof"
        )

    time_range = payload.get("time_range")
    if not isinstance(time_range, dict):
        raise QualificationFailure(f"{lane} omitted its dashboard query window")
    published_start = _parse_public_datetime(
        time_range.get("start"), lane=lane, field="dashboard-window-start"
    )
    published_end = _parse_public_datetime(
        time_range.get("end"), lane=lane, field="dashboard-window-end"
    )
    if (
        published_start != start.astimezone(UTC)
        or published_end != end.astimezone(UTC)
        or payload.get("granularity") != granularity
    ):
        raise QualificationFailure(f"{lane} returned a different dashboard window")

    metrics = payload.get("metrics")
    if not isinstance(metrics, list) or len(metrics) != 1:
        raise QualificationFailure(f"{lane} omitted its dashboard metric")
    metric = metrics[0]
    if (
        not isinstance(metric, dict)
        or metric.get("id") != "latency"
        or metric.get("aggregation") != "avg"
        or metric.get("query_complete") is not True
        or metric.get("query_status") != "complete"
        or metric.get("query_sampled") is not False
        or metric.get("query_exact") is not True
        or metric.get("query_provenance") != "exact_snapshot"
        or metric.get("error")
    ):
        raise QualificationFailure(f"{lane} returned an inexact dashboard metric")
    series = metric.get("series")
    if (
        not isinstance(series, list)
        or len(series) != 1
        or not isinstance(series[0], dict)
        or series[0].get("name") != "total"
    ):
        raise QualificationFailure(f"{lane} returned an invalid dashboard series")
    points = series[0].get("data")
    if not isinstance(points, list) or any(
        not isinstance(point, dict) for point in points
    ):
        raise QualificationFailure(f"{lane} returned malformed dashboard points")
    expected_timestamps = tuple(
        timestamp.isoformat()
        for timestamp in _expected_graph_timestamps(start, end, granularity)
    )
    observed_timestamps = tuple(
        _parse_public_datetime(
            point.get("timestamp"),
            lane=lane,
            field="dashboard-point",
            assume_utc=True,
        ).isoformat()
        for point in points
    )
    if observed_timestamps != expected_timestamps:
        raise QualificationFailure(
            f"{lane} returned non-contiguous, unordered, duplicate, or out-of-window dashboard buckets"
        )
    positive_points = 0
    populated_points = 0
    for point in points:
        raw_value = point.get("value")
        if raw_value is None:
            continue
        value = _finite_number(raw_value)
        if (
            not isinstance(raw_value, (int, float))
            or isinstance(raw_value, bool)
            or value is None
            or value < 0
        ):
            raise QualificationFailure(f"{lane} returned a non-finite dashboard value")
        populated_points += 1
        if value > 0:
            positive_points += 1
    profile_filter_sha256 = (
        _graph_filter_binding_sha256(
            project_id=str(client.project.id),
            kind="trace",
            filters=profile_filters,
        )
        if profile_filters
        else None
    )
    return {
        "qualified": True,
        "kind": "dashboard",
        "metric": "latency",
        "data_digest": _digest(points),
        "timestamp_digest": _digest(observed_timestamps),
        "read_mode": "exact",
        "profile_filter_count": len(profile_filters),
        "profile_filter_sha256": profile_filter_sha256,
        "point_count": len(points),
        "populated_point_count": populated_points,
        "positive_point_count": positive_points,
        "positive": positive_points > 0,
    }


def _missing_long_window_positive_graphs(
    graph_lanes: Iterable[tuple[str, str, str, dict[str, Any]]],
) -> tuple[list[str], list[str]]:
    lanes = list(graph_lanes)
    long_windows = {"30d", "90d", "180d", "365d"}
    expected_pairs = sorted(
        {(kind, profile) for _window, kind, profile, _record in lanes}
    )
    expected = [f"{kind}.{profile}" for kind, profile in expected_pairs]
    missing = []
    for kind, profile in expected_pairs:
        candidates = [
            record
            for window, candidate_kind, candidate_profile, record in lanes
            if window in long_windows
            and candidate_kind == kind
            and candidate_profile == profile
        ]
        if not any(
            record.get("qualified") is True
            and bool((record.get("evidence") or {}).get("positive"))
            for record in candidates
        ):
            missing.append(f"{kind}.{profile}")
    return expected, missing


def _graph_profile_names(target_name: str) -> tuple[str, ...]:
    density = str(TARGETS[target_name]["density"])
    return (
        "default",
        density,
        "f1.system",
        "f4.system_custom",
        "f5.eval_present",
        "f5.eval_absent",
        "f5.eval_exact",
        "f6.annotation_present",
        "f6.annotation_absent",
        "f6.annotation_exact",
        "f7.custom_eval_annotation",
    )


def _qualify_graph_matrix(target: Target, *, end: datetime) -> dict[str, Any]:
    client = DirectDRFClient(target.project, target.principal)
    profiles = _matrix_filter_profiles(target)
    profile_names = tuple(profile for profile, _filters in profiles)
    if profile_names != _graph_profile_names(target.name):
        raise SafetyViolation("graph profile matrix drifted")

    kinds = ("trace", "span", "session", "dashboard")
    graph_lanes: list[tuple[str, str, str, dict[str, Any]]] = []
    for window_name, duration in WINDOWS:
        start = end - duration
        for kind in kinds:
            for profile, profile_filters in profiles:
                lane = f"graph.{target.name}.{window_name}.{kind}.{profile}"
                qualifier = (
                    _qualify_dashboard_query if kind == "dashboard" else _qualify_graph
                )
                graph_lanes.append(
                    (
                        window_name,
                        kind,
                        profile,
                        _run_lane(
                            lane,
                            lambda qualifier=qualifier, kind=kind, start=start, window_name=window_name, lane=lane, profile_filters=profile_filters: (
                                qualifier(
                                    client,
                                    **({"kind": kind} if kind != "dashboard" else {}),
                                    start=start,
                                    end=end,
                                    window_name=window_name,
                                    lane=lane,
                                    filters=profile_filters,
                                )
                            ),
                        ),
                    )
                )
    expected_graph_profiles, missing_graph_profiles = (
        _missing_long_window_positive_graphs(graph_lanes)
    )
    graph_population = _run_lane(
        f"graph.{target.name}.long_window_population",
        lambda: {
            "qualified": not missing_graph_profiles,
            "expected_profiles": expected_graph_profiles,
            "missing_profiles": missing_graph_profiles,
            "reason": "missing positive long-window graph points",
        },
    )
    return {
        "qualified": all(
            record["qualified"] for _window, _kind, _profile, record in graph_lanes
        )
        and graph_population["qualified"],
        "target_name": target.name,
        "profile_density": str(TARGETS[target.name]["density"]),
        "windows": [name for name, _duration in WINDOWS],
        "kinds": list(kinds),
        "profiles": list(profile_names),
        "lane_count": len(graph_lanes),
        "failed_lanes": [
            record["lane"]
            for _window, _kind, _profile, record in graph_lanes
            if not record["qualified"]
        ],
        "long_window_population": graph_population["evidence"],
    }


def _qualify_metrics_catalog(
    client: DirectDRFClient,
    *,
    lane: str,
    expected_property_ids: Iterable[str] = (),
    required_dataset_representative: DatasetRepresentative | None = None,
) -> dict[str, Any]:
    page_size = METRIC_CATALOG_QUALIFICATION_PAGE_SIZE

    def fetch(
        cursor: str | None,
        suffix: str,
        *,
        search: str = "",
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        query: dict[str, Any] = {
            "cursor_mode": "true",
            "project_ids": str(client.project.id),
            "page_size": page_size,
        }
        if cursor:
            query["cursor"] = cursor
        if search:
            query["search"] = search
        response = client.call(
            "metrics",
            lane=f"{lane}.{suffix}",
            query=query,
        )
        payload = _require_status(response, f"{lane}.{suffix}")
        metrics = payload.get("metrics")
        if not isinstance(metrics, list) or any(
            not isinstance(item, dict) for item in metrics
        ):
            raise QualificationFailure("metrics catalog returned malformed definitions")
        if payload.get("page_size") != page_size:
            raise QualificationFailure("metrics catalog page size changed")
        if (
            payload.get("total") is not None
            or payload.get("total_is_exact") is not False
        ):
            raise QualificationFailure("metrics cursor catalog published a false total")
        has_more = payload.get("has_more")
        next_cursor = payload.get("next_cursor")
        if not isinstance(has_more, bool):
            raise QualificationFailure("metrics catalog omitted continuation truth")
        if has_more and (
            not isinstance(next_cursor, str)
            or not next_cursor
            or len(next_cursor) > 16_384
        ):
            raise QualificationFailure("metrics catalog omitted a bounded cursor")
        if not has_more and next_cursor is not None:
            raise QualificationFailure("metrics terminal page exposed a cursor")
        if len(metrics) > page_size or (has_more and not metrics):
            raise QualificationFailure("metrics catalog returned an invalid page")
        if (
            payload.get("query_complete") is not True
            or payload.get("query_exact") is not True
            or payload.get("query_status") != "complete"
            or payload.get("query_provenance") != "activated_property_catalog"
        ):
            raise QualificationFailure(
                "metrics catalog omitted an exact activation proof"
            )
        epoch = payload.get("catalog_epoch")
        revision = payload.get("catalog_revision")
        fingerprint = payload.get("activation_fingerprint")
        if (
            not isinstance(epoch, int)
            or isinstance(epoch, bool)
            or not 1 <= epoch <= 65_535
            or not isinstance(revision, int)
            or isinstance(revision, bool)
            or revision < 1
            or not re.fullmatch(r"[0-9a-f]{64}", str(fingerprint or ""))
        ):
            raise QualificationFailure("metrics catalog activation identity is invalid")
        identities = [
            (item.get("property_id"), item.get("property_kind")) for item in metrics
        ]
        if any(
            not isinstance(property_id, str)
            or not property_id
            or not isinstance(property_kind, str)
            or not property_kind
            for property_id, property_kind in identities
        ):
            raise QualificationFailure("metrics catalog omitted registry identities")
        if len(set(identities)) != len(identities):
            raise QualificationFailure("metrics catalog page repeated an identity")
        return metrics, payload

    first_metrics, first = fetch(None, "p1")
    repeat_metrics, repeat = fetch(None, "p1_repeat")
    if not first_metrics:
        raise QualificationFailure("metrics catalog omitted system definitions")
    stable_fields = (
        "total",
        "total_is_exact",
        "page_size",
        "has_more",
        "catalog_epoch",
        "catalog_revision",
        "activation_fingerprint",
        "query_complete",
        "query_exact",
        "query_status",
        "query_provenance",
    )

    def require_semantic_repeat(
        left_metrics: list[dict[str, Any]],
        left: dict[str, Any],
        right_metrics: list[dict[str, Any]],
        right: dict[str, Any],
        *,
        suffix: str,
    ) -> None:
        if _digest(left_metrics, 64) != _digest(right_metrics, 64) or any(
            left.get(field) != right.get(field) for field in stable_fields
        ):
            raise QualificationFailure(
                f"metrics catalog {suffix} repeat was semantically unstable"
            )

    require_semantic_repeat(
        first_metrics,
        first,
        repeat_metrics,
        repeat,
        suffix="page-one",
    )
    activation_binding = (
        first["catalog_epoch"],
        first["catalog_revision"],
        first["activation_fingerprint"],
    )
    seen_ids = {str(item["property_id"]) for item in first_metrics}
    if len(seen_ids) != len(first_metrics):
        raise QualificationFailure("metrics catalog repeated a property identity")
    all_metrics = list(first_metrics)
    terminal = first
    page_count = 1
    cursors: set[str] = set()
    if first["has_more"]:
        first_cursor = first["next_cursor"]
        repeat_cursor = repeat["next_cursor"]
        if not isinstance(first_cursor, str) or not isinstance(repeat_cursor, str):
            raise QualificationFailure("metrics catalog omitted a bounded cursor")
        cursors.add(first_cursor)
        page_metrics, terminal = fetch(first_cursor, "p2")
        binding = (
            terminal["catalog_epoch"],
            terminal["catalog_revision"],
            terminal["activation_fingerprint"],
        )
        if binding != activation_binding:
            raise QualificationFailure("metrics cursor changed activation lineage")
        identities = {str(item["property_id"]) for item in page_metrics}
        if len(identities) != len(page_metrics) or seen_ids & identities:
            raise QualificationFailure("metrics catalog pages overlapped")

        repeat_page_metrics, repeat_terminal = fetch(
            repeat_cursor,
            "p1_repeat_p2",
        )
        require_semantic_repeat(
            page_metrics,
            terminal,
            repeat_page_metrics,
            repeat_terminal,
            suffix="continuation",
        )
        if (
            repeat_terminal["catalog_epoch"],
            repeat_terminal["catalog_revision"],
            repeat_terminal["activation_fingerprint"],
        ) != activation_binding:
            raise QualificationFailure("metrics cursor changed activation lineage")
        if (
            repeat_terminal["has_more"]
            and repeat_terminal["next_cursor"] == repeat_cursor
        ):
            raise QualificationFailure("metrics catalog repeated its cursor")

        seen_ids.update(identities)
        all_metrics.extend(page_metrics)
        page_count = 2

    while terminal.get("has_more"):
        cursor = str(terminal["next_cursor"])
        if cursor in cursors:
            raise QualificationFailure("metrics catalog repeated its cursor")
        cursors.add(cursor)
        page_count += 1
        if page_count > METRIC_CATALOG_QUALIFICATION_MAX_PAGES:
            raise QualificationFailure("metrics catalog exceeded its page fuse")
        page_metrics, terminal = fetch(cursor, f"p{page_count}")
        binding = (
            terminal["catalog_epoch"],
            terminal["catalog_revision"],
            terminal["activation_fingerprint"],
        )
        if binding != activation_binding:
            raise QualificationFailure("metrics cursor changed activation lineage")
        identities = {str(item["property_id"]) for item in page_metrics}
        if len(identities) != len(page_metrics) or seen_ids & identities:
            raise QualificationFailure("metrics catalog pages overlapped")
        seen_ids.update(identities)
        all_metrics.extend(page_metrics)
    if terminal.get("has_more") is not False or terminal.get("next_cursor") is not None:
        raise QualificationFailure("metrics catalog terminal page was not truthful")

    expected = {str(value) for value in expected_property_ids if str(value)}
    if required_dataset_representative is not None:
        expected.add(required_dataset_representative.column_property_id)
    missing = sorted(expected - seen_ids)
    if missing:
        raise QualificationFailure(
            "unified metrics catalog omitted expected definitions: "
            + ",".join(_digest(value) for value in missing)
        )

    dataset_definition = None
    if required_dataset_representative is not None:
        dataset_definitions = [
            item
            for item in all_metrics
            if item.get("property_id")
            == required_dataset_representative.column_property_id
        ]
        if (
            len(dataset_definitions) != 1
            or dataset_definitions[0].get("property_kind") != "dataset_column"
        ):
            raise QualificationFailure(
                "metrics catalog omitted the representative dataset-column definition"
            )
        dataset_definition = dataset_definitions[0]

    selected = dataset_definition or next(
        (item for item in all_metrics if str(item.get("property_id")) in expected),
        all_metrics[0],
    )
    # The dataset-column source key is a UUID and therefore selects the exact
    # definition even when several columns share one display name.
    search = str(selected.get("name") or selected.get("display_name") or "").strip()
    if not search:
        raise QualificationFailure("metrics catalog definition was not searchable")
    search_metrics, search_payload = fetch(None, "search", search=search)
    if (
        search_payload["catalog_epoch"],
        search_payload["catalog_revision"],
        search_payload["activation_fingerprint"],
    ) != activation_binding:
        raise QualificationFailure("metrics catalog search changed activation lineage")
    searched_definition = next(
        (
            item
            for item in search_metrics
            if item.get("property_id") == selected["property_id"]
        ),
        None,
    )
    if searched_definition is None or searched_definition.get(
        "property_kind"
    ) != selected.get("property_kind"):
        raise QualificationFailure(
            "metrics catalog search omitted its exact definition"
        )
    return {
        "qualified": True,
        "activation_fingerprint_digest": _digest(first["activation_fingerprint"]),
        "catalog_epoch": first["catalog_epoch"],
        "catalog_revision": first["catalog_revision"],
        "page_count": page_count,
        "metric_count": len(seen_ids),
        "property_kinds": sorted(
            {str(item.get("property_kind")) for item in all_metrics}
        ),
        "continuation_exercised": page_count > 1,
        "page_one_repeat_stable": True,
        "terminal_page": page_count,
        "terminal_has_more": False,
        "search_activation_fingerprint_digest": _digest(
            search_payload["activation_fingerprint"]
        ),
        "dataset_column_definition_proven": dataset_definition is not None,
        "dataset_representative_binding_sha256": (
            required_dataset_representative.binding_sha256
            if required_dataset_representative is not None
            else None
        ),
        "selected_property_id_digest": _digest(str(selected["property_id"]), 64),
        "selected_property_kind": str(selected["property_kind"]),
        "search_proven": True,
    }


def _dataset_exact_page(
    payload: dict[str, Any],
    *,
    lane: str,
    requested_page_index: int,
    expected_page_size: int,
    expected_total_rows: int | None = None,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    metadata = payload.get("metadata")
    rows = payload.get("table")
    if not isinstance(metadata, dict) or not isinstance(rows, list):
        raise QualificationFailure(f"{lane} omitted dataset rows or metadata")
    if (
        metadata.get("is_exact") is not True
        or metadata.get("snapshot_bound") is not True
        or metadata.get("error_messages") != []
    ):
        raise QualificationFailure(f"{lane} was not exact and revision-bound")

    integer_fields = {
        "total_rows": metadata.get("total_rows"),
        "total_pages": metadata.get("total_pages"),
        "page_size": metadata.get("page_size"),
        "current_page_index": metadata.get("current_page_index"),
    }
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in integer_fields.values()
    ):
        raise QualificationFailure(f"{lane} returned invalid dataset page counts")
    total_rows = integer_fields["total_rows"]
    total_pages = integer_fields["total_pages"]
    page_size = integer_fields["page_size"]
    page_index = integer_fields["current_page_index"]
    if page_size != expected_page_size or page_index != requested_page_index:
        raise QualificationFailure(f"{lane} returned the wrong dataset page")
    if expected_total_rows is not None and total_rows != expected_total_rows:
        raise QualificationFailure(f"{lane} changed the dataset snapshot total")
    expected_pages = 0 if total_rows == 0 else math.ceil(total_rows / page_size)
    expected_rows = min(page_size, max(0, total_rows - page_index * page_size))
    if total_pages != expected_pages or len(rows) != expected_rows:
        raise QualificationFailure(f"{lane} returned inconsistent dataset counts")

    row_ids: list[str] = []
    for row in rows:
        row_id = row.get("row_id") if isinstance(row, dict) else None
        if not isinstance(row_id, str) or not row_id:
            raise QualificationFailure(f"{lane} omitted a dataset row_id")
        row_ids.append(row_id)
    if len(set(row_ids)) != len(row_ids):
        raise QualificationFailure(f"{lane} returned duplicate dataset row_ids")

    loaded_rows = page_index * page_size + len(rows)
    expected_has_more = loaded_rows < total_rows
    expected_next_page = page_index + 1 if expected_has_more else None
    has_more = metadata.get("has_more")
    next_page_index = metadata.get("next_page_index")
    next_cursor = metadata.get("next_cursor")
    if (
        not isinstance(has_more, bool)
        or has_more != expected_has_more
        or next_page_index != expected_next_page
        or (expected_has_more and (not isinstance(next_cursor, str) or not next_cursor))
        or (not expected_has_more and next_cursor is not None)
    ):
        raise QualificationFailure(f"{lane} returned inconsistent dataset continuation")
    return metadata, tuple(row_ids)


def _select_dataset_representative(
    client: DirectDRFClient,
) -> DatasetRepresentative:
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT dataset.id::text,
                   COUNT(dataset_row.id) FILTER (WHERE NOT dataset_row.deleted),
                   representative_column.id::text
            FROM model_hub_dataset AS dataset
            INNER JOIN LATERAL (
                SELECT dataset_column.id
                FROM model_hub_column AS dataset_column
                WHERE dataset_column.dataset_id = dataset.id
                  AND NOT dataset_column.deleted
                ORDER BY dataset_column.id
                LIMIT 1
            ) AS representative_column ON TRUE
            LEFT JOIN model_hub_row AS dataset_row
              ON dataset_row.dataset_id = dataset.id
            WHERE NOT dataset.deleted
              AND dataset.organization_id = %s
              AND dataset.workspace_id = %s
            GROUP BY dataset.id, representative_column.id
            ORDER BY
              (COUNT(dataset_row.id) FILTER (WHERE NOT dataset_row.deleted) >= 100)
                DESC,
              COUNT(dataset_row.id) FILTER (WHERE NOT dataset_row.deleted) DESC,
              dataset.id
            LIMIT 1
            """,
            [str(client.project.organization_id), str(client.principal.workspace.id)],
        )
        row = cursor.fetchone()
    if row is None:
        raise PopulationGap("no exact dataset with a catalogued column was authorized")
    dataset_id, raw_active_rows, column_id = row
    try:
        canonical_dataset_id = str(UUID(str(dataset_id)))
        canonical_column_id = str(UUID(str(column_id)))
    except (AttributeError, TypeError, ValueError) as exc:
        raise QualificationFailure(
            "dataset representative identity was invalid"
        ) from exc
    if (
        not isinstance(raw_active_rows, int)
        or isinstance(raw_active_rows, bool)
        or raw_active_rows < 0
    ):
        raise QualificationFailure("dataset representative row count was invalid")
    return DatasetRepresentative(
        dataset_id=canonical_dataset_id,
        active_rows=raw_active_rows,
        column_id=canonical_column_id,
    )


def _qualify_dataset_exact(
    client: DirectDRFClient,
    *,
    lane: str,
    representative: DatasetRepresentative,
) -> dict[str, Any]:
    base_query = {
        "exact_snapshot": "true",
        "page_size": 50,
        "current_page_index": 0,
    }
    first_response = client.call(
        "dataset_exact",
        lane=f"{lane}.p1",
        query=base_query,
        path_kwargs={"dataset_id": representative.dataset_id},
    )
    first = _require_status(first_response, f"{lane}.p1")
    repeat_response = client.call(
        "dataset_exact",
        lane=f"{lane}.p1_repeat",
        query=base_query,
        path_kwargs={"dataset_id": representative.dataset_id},
    )
    repeat = _require_status(repeat_response, f"{lane}.p1_repeat")
    first_meta, first_ids = _dataset_exact_page(
        first,
        lane=f"{lane}.p1",
        requested_page_index=0,
        expected_page_size=50,
    )
    repeat_meta, repeat_ids = _dataset_exact_page(
        repeat,
        lane=f"{lane}.p1_repeat",
        requested_page_index=0,
        expected_page_size=50,
        expected_total_rows=first_meta["total_rows"],
    )
    stable_metadata_fields = (
        "dataset_name",
        "total_rows",
        "total_pages",
        "page_size",
        "current_page_index",
        "is_exact",
        "snapshot_bound",
        "has_more",
        "next_page_index",
        "error_messages",
    )
    if any(
        first_meta.get(key) != repeat_meta.get(key) for key in stable_metadata_fields
    ):
        raise QualificationFailure("dataset exact page-one metadata was unstable")
    if first_ids != repeat_ids:
        raise QualificationFailure("dataset exact page-one repeat was unstable")
    second_count = 0
    if first_meta.get("has_more"):
        first_cursor = first_meta.get("next_cursor")
        repeat_cursor = repeat_meta.get("next_cursor")
        if not isinstance(first_cursor, str) or not isinstance(repeat_cursor, str):
            raise QualificationFailure("dataset exact page omitted its cursor")

        def read_continuation(cursor: str, suffix: str):
            response = client.call(
                "dataset_exact",
                lane=f"{lane}.{suffix}",
                query={
                    "cursor": cursor,
                    "page_size": 50,
                    "current_page_index": 1,
                },
                path_kwargs={"dataset_id": representative.dataset_id},
            )
            payload = _require_status(response, f"{lane}.{suffix}")
            metadata, row_ids = _dataset_exact_page(
                payload,
                lane=f"{lane}.{suffix}",
                requested_page_index=1,
                expected_page_size=50,
                expected_total_rows=first_meta["total_rows"],
            )
            if not row_ids or set(first_ids) & set(row_ids):
                raise QualificationFailure(
                    "dataset continuation overlapped or was empty"
                )
            if metadata.get("has_more") and metadata.get("next_cursor") == cursor:
                raise QualificationFailure(
                    "dataset continuation cursor did not advance"
                )
            return metadata, row_ids

        second_meta, second_ids = read_continuation(first_cursor, "p2")
        repeat_second_meta, repeat_second_ids = read_continuation(
            repeat_cursor,
            "p1_repeat_p2",
        )
        if (
            any(
                second_meta.get(key) != repeat_second_meta.get(key)
                for key in stable_metadata_fields
            )
            or second_ids != repeat_second_ids
        ):
            raise QualificationFailure(
                "dataset timestamped repeat cursors changed continuation semantics"
            )
        second_count = len(second_ids)
    return {
        "qualified": True,
        "dataset_id_digest": _digest(representative.dataset_id),
        "dataset_column_property_id_digest": _digest(
            representative.column_property_id, 64
        ),
        "dataset_representative_binding_sha256": representative.binding_sha256,
        "ledger_active_rows": representative.active_rows,
        "snapshot_total_rows": first_meta["total_rows"],
        "p1_rows": len(first_ids),
        "p2_rows": second_count,
        "continuation_exercised": bool(first_meta.get("has_more")),
    }


def _preview_page(
    client: DirectDRFClient,
    *,
    endpoint: str,
    lane: str,
    path_kwargs: dict[str, Any],
    query: dict[str, Any],
) -> dict[str, Any]:
    response = client.call(
        endpoint,
        lane=lane,
        query=query,
        path_kwargs=path_kwargs,
    )
    payload = _require_status(response, lane)
    if payload.get("exact") is not True:
        raise QualificationFailure(f"{lane} did not return an exact page")
    results = payload.get("results")
    if not isinstance(results, list):
        raise QualificationFailure(f"{lane} omitted results")
    has_more = payload.get("has_more")
    complete = payload.get("complete")
    if not isinstance(has_more, bool) or not isinstance(complete, bool):
        raise QualificationFailure(f"{lane} omitted truthful completion metadata")
    if complete is has_more:
        raise QualificationFailure(f"{lane} returned inconsistent completion metadata")
    cursor = payload.get("next_cursor")
    if has_more and (not isinstance(cursor, str) or not cursor):
        raise QualificationFailure(f"{lane} omitted its continuation cursor")
    if not has_more and cursor is not None:
        raise QualificationFailure(f"{lane} returned a cursor after exhaustion")
    snapshot_total = payload.get("snapshot_total")
    loaded_through = payload.get("loaded_through")
    if (
        not isinstance(snapshot_total, int)
        or isinstance(snapshot_total, bool)
        or not isinstance(loaded_through, int)
        or isinstance(loaded_through, bool)
        or snapshot_total < 0
        or loaded_through < len(results)
        or loaded_through > snapshot_total
    ):
        raise QualificationFailure(f"{lane} returned invalid snapshot progress")
    if complete and loaded_through != snapshot_total:
        raise QualificationFailure(f"{lane} terminal page did not exhaust its snapshot")
    if has_more and (not results or loaded_through >= snapshot_total):
        raise QualificationFailure(f"{lane} continuation progress was inconsistent")
    if not payload.get("snapshot_at"):
        raise QualificationFailure(f"{lane} omitted its snapshot timestamp")
    return payload


def _require_preview_repeat_stability(
    first: dict[str, Any], repeat: dict[str, Any], *, lane: str
) -> None:
    # Fresh exact snapshots and Django's timestamped signatures intentionally
    # carry different wall-clock values. Repeat stability is therefore the
    # public page/progress contract; each cursor is exercised independently
    # below to prove it resumes the same immutable membership revision.
    stable_fields = (
        "has_more",
        "snapshot_total",
        "loaded_through",
        "complete",
        "exact",
    )
    if any(first.get(field) != repeat.get(field) for field in stable_fields):
        raise QualificationFailure(f"{lane} page-one metadata was unstable")


def _qualify_preview_repeat_chain(
    client: DirectDRFClient,
    *,
    endpoint: str,
    lane: str,
    path_kwargs: dict[str, Any],
    query: dict[str, Any],
) -> tuple[tuple[str, ...], int]:
    first = _preview_page(
        client,
        endpoint=endpoint,
        lane=f"{lane}.p1",
        path_kwargs=path_kwargs,
        query=query,
    )
    repeat = _preview_page(
        client,
        endpoint=endpoint,
        lane=f"{lane}.p1_repeat",
        path_kwargs=path_kwargs,
        query=query,
    )
    first_ids = tuple(str(row["id"]) for row in first["results"])
    repeat_ids = tuple(str(row["id"]) for row in repeat["results"])
    _require_preview_repeat_stability(first, repeat, lane=lane)
    if not first_ids or first_ids != repeat_ids:
        raise QualificationFailure(f"{lane} page-one repeat was unstable")
    if len(set(first_ids)) != len(first_ids):
        raise QualificationFailure(f"{lane} page one repeated an identity")

    second_count = 0
    if first["has_more"]:
        first_cursor = first.get("next_cursor")
        repeat_cursor = repeat.get("next_cursor")
        if not isinstance(first_cursor, str) or not isinstance(repeat_cursor, str):
            raise QualificationFailure(f"{lane} omitted its continuation cursor")

        def read_continuation(
            parent: dict[str, Any], cursor: str, suffix: str
        ) -> tuple[dict[str, Any], tuple[str, ...]]:
            page = _preview_page(
                client,
                endpoint=endpoint,
                lane=f"{lane}.{suffix}",
                path_kwargs=path_kwargs,
                query={**query, "cursor": cursor},
            )
            ids = tuple(str(row["id"]) for row in page["results"])
            if not ids or len(set(ids)) != len(ids) or set(first_ids) & set(ids):
                raise QualificationFailure(f"{lane} continuation overlapped")
            if (
                page.get("snapshot_total") != parent.get("snapshot_total")
                or page.get("snapshot_at") != parent.get("snapshot_at")
                or page.get("loaded_through", 0) <= parent.get("loaded_through", 0)
            ):
                raise QualificationFailure(f"{lane} continuation drifted")
            if page["has_more"] and page.get("next_cursor") == cursor:
                raise QualificationFailure(
                    f"{lane} continuation cursor did not advance"
                )
            return page, ids

        second, second_ids = read_continuation(first, first_cursor, "p2")
        repeat_second, repeat_second_ids = read_continuation(
            repeat,
            repeat_cursor,
            "p1_repeat_p2",
        )
        _require_preview_repeat_stability(second, repeat_second, lane=f"{lane}.p2")
        if second_ids != repeat_second_ids:
            raise QualificationFailure(
                f"{lane} timestamped repeat cursors changed continuation semantics"
            )
        second_count = len(second_ids)
    return first_ids, second_count


def _qualify_simulation_previews(
    client: DirectDRFClient, *, lane: str
) -> dict[str, Any]:
    from django.db.models import Count

    from simulate.models import RunTest, TestExecution

    run_test = (
        RunTest.objects.filter(
            organization_id=client.project.organization_id,
            workspace_id=client.principal.workspace.id,
            deleted=False,
        )
        .annotate(execution_count=Count("executions"))
        .filter(execution_count__gt=0)
        .order_by("-execution_count", "id")
        .first()
    )
    if run_test is None:
        raise PopulationGap("no authorized run test has executions")
    first_ids, execution_second_count = _qualify_preview_repeat_chain(
        client,
        endpoint="simulation_executions",
        lane=f"{lane}.executions",
        path_kwargs={"run_test_id": str(run_test.id)},
        query={"page_size": 20},
    )

    test_execution = (
        TestExecution.objects.filter(
            run_test=run_test,
            deleted=False,
            calls__deleted=False,
        )
        .annotate(call_count=Count("calls"))
        .filter(call_count__gt=0)
        .order_by("-call_count", "id")
        .first()
    )
    if test_execution is None:
        raise PopulationGap("no execution under the selected run test has calls")
    call_query = {"page_size": 20, "run_test_id": str(run_test.id)}
    call_first_ids, call_second_count = _qualify_preview_repeat_chain(
        client,
        endpoint="simulation_calls",
        lane=f"{lane}.calls",
        path_kwargs={"test_execution_id": str(test_execution.id)},
        query=call_query,
    )
    return {
        "qualified": True,
        "run_test_id_digest": _digest(str(run_test.id)),
        "execution_p1_rows": len(first_ids),
        "execution_p2_rows": execution_second_count,
        "call_p1_rows": len(call_first_ids),
        "call_p2_rows": call_second_count,
    }


def _select_target(name: str, spec: dict[str, Any]) -> Target:
    anchor, projects = _resolve_tenant_projects(
        anchor_project_id=spec["anchor_project_id"],
        tokens=spec["tokens"],
    )
    required_surface = spec["surface"]
    candidates = [
        project for project in projects if _surface(project) == required_surface
    ]
    if anchor in candidates:
        candidates.remove(anchor)
        candidates.insert(0, anchor)
    evidence = []
    for project in candidates[:MAX_TARGET_PROJECTS]:
        principal = _project_principal(project)
        if principal is None:
            evidence.append(
                {"project": _digest(str(project.id)), "reason": "no_principal"}
            )
            continue
        client = DirectDRFClient(project, principal)
        try:
            key, value, value_type = _discover_property_profile(
                client,
                spec["preferred_keys"],
                lane=f"target.{name}.profile",
            )
            system_value, system_value_type = _discover_system_model(
                client,
                lane=f"target.{name}.system_model",
            )
            eval_profile, annotation_profile = _discover_relational_profiles(
                client,
                lane=f"target.{name}.relational",
            )
            return Target(
                name=name,
                project=project,
                principal=principal,
                key=key,
                value=value,
                value_type=value_type,
                system_value=system_value,
                system_value_type=system_value_type,
                eval_profile=eval_profile,
                annotation_profile=annotation_profile,
            )
        except SafetyViolation:
            raise
        except Exception as exc:
            evidence.append(
                {"project": _digest(str(project.id)), "reason": _redact(exc)}
            )
    raise PopulationGap(f"no positive {name} property target: {_digest(evidence)}")


def _missing_long_window_positive_profiles(
    list_lanes: Iterable[tuple[str, str, str, dict[str, Any]]],
) -> tuple[list[tuple[str, str]], list[str]]:
    lanes = list(list_lanes)
    long_windows = {"30d", "90d", "180d", "365d"}
    expected = sorted({(kind, mode) for _window, kind, mode, _record in lanes})
    missing = []
    for kind, mode in expected:
        candidates = [
            record
            for window, candidate_kind, candidate_mode, record in lanes
            if window in long_windows
            and candidate_kind == kind
            and candidate_mode == mode
        ]
        if not any(
            record.get("qualified")
            and bool((record.get("evidence") or {}).get("positive"))
            for record in candidates
        ):
            missing.append(f"{kind}.{mode}")
    return expected, missing


def _profile_membership_conflicts(
    list_lanes: Iterable[tuple[str, str, str, dict[str, Any]]],
) -> list[str]:
    by_cell = {
        (window, kind, mode): record for window, kind, mode, record in list_lanes
    }

    def identities(record: dict[str, Any] | None) -> set[str]:
        if not record or not record.get("qualified"):
            return set()
        values = (record.get("evidence") or {}).get("row_identity_digests") or []
        return {str(value) for value in values}

    conflicts = []
    windows_and_kinds = sorted(
        {(window, kind) for window, kind, _mode, _record in list_lanes}
    )
    disjoint_pairs = (
        ("f5.eval_present", "f5.eval_absent"),
        ("f5.eval_exact", "f5.eval_absent"),
        ("f6.annotation_present", "f6.annotation_absent"),
        ("f6.annotation_exact", "f6.annotation_absent"),
        ("f7.custom_eval_annotation", "f5.eval_absent"),
        ("f7.custom_eval_annotation", "f6.annotation_absent"),
    )
    for window, kind in windows_and_kinds:
        for positive_mode, negative_mode in disjoint_pairs:
            positive = identities(by_cell.get((window, kind, positive_mode)))
            negative = identities(by_cell.get((window, kind, negative_mode)))
            if positive & negative:
                conflicts.append(
                    f"{window}.{kind}.{positive_mode}_overlaps_{negative_mode}"
                )
    return conflicts


def _full_list_protocol_required(*, mode: str, density: str, window_name: str) -> bool:
    return (
        mode in {"default", density, *SYSTEM_MATRIX_PROFILE_MODES}
        or window_name == "365d"
    )


def _long_window_population_gaps(
    missing_profiles: Iterable[str],
) -> list[dict[str, str]]:
    negative_modes = {"f5.eval_absent", "f6.annotation_absent"}
    return [
        {
            "profile": profile,
            "kind": "negative_complement"
            if any(profile.endswith(f".{mode}") for mode in negative_modes)
            else (
                "observed_exact_value"
                if any(
                    profile.endswith(f".{mode}") for mode in EXACT_VALUE_PROFILE_MODES
                )
                else "positive_witness"
            ),
        }
        for profile in missing_profiles
    ]


def _observed_exact_value_proofs(
    list_lanes: Iterable[tuple[str, str, str, dict[str, Any]]],
) -> list[dict[str, str]]:
    """Bind exact-value profiles to identities returned by filtered lists.

    ``/filter_values`` supplies only a candidate vocabulary. A candidate is
    considered observed only after a long-window public list, with that exact
    leaf applied, returns a concrete row identity. This keeps the proof on the
    public read path without adding another database scan.
    """

    long_windows = {"30d", "90d", "180d", "365d"}
    proofs: dict[tuple[str, str], dict[str, str]] = {}
    for window, kind, mode, record in list_lanes:
        if mode not in EXACT_VALUE_PROFILE_MODES or window not in long_windows:
            continue
        evidence = record.get("evidence") or {}
        identities = evidence.get("row_identity_digests") or []
        key = (kind, mode)
        if (
            key not in proofs
            and record.get("qualified") is True
            and evidence.get("positive") is True
            and identities
        ):
            proofs[key] = {
                "kind": kind,
                "profile": mode,
                "window": window,
                "row_identity_digest": str(identities[0]),
            }
    return [proofs[key] for key in sorted(proofs)]


def _missing_observed_exact_profiles(
    list_lanes: Iterable[tuple[str, str, str, dict[str, Any]]],
    proofs: Iterable[dict[str, str]],
) -> list[str]:
    expected = {
        f"{kind}.{mode}"
        for _window, kind, mode, _record in list_lanes
        if mode in EXACT_VALUE_PROFILE_MODES
    }
    observed = {f"{proof.get('kind')}.{proof.get('profile')}" for proof in proofs}
    return sorted(expected - observed)


def _target_profile_binding(target: Target) -> dict[str, str]:
    """Return a value-redacted identity for one discovered filter profile."""

    if (
        target.eval_profile is None
        or target.annotation_profile is None
        or target.system_value is None
        or target.system_value_type is None
    ):
        raise PopulationGap("target omitted a required filter profile")
    components = {
        "project_id": str(target.project.id),
        "custom_profile_sha256": _digest(
            _custom_filter(str(target.key), target.value, str(target.value_type)),
            64,
        ),
        "system_profile_sha256": _digest(
            _system_model_filter(target.system_value, target.system_value_type),
            64,
        ),
        "eval_profile_sha256": _digest(
            _relational_filter(target.eval_profile),
            64,
        ),
        "annotation_profile_sha256": _digest(
            _relational_filter(target.annotation_profile),
            64,
        ),
    }
    return {
        **components,
        "binding_sha256": _digest(components, 64),
    }


def _qualify_target(
    target: Target,
    *,
    end: datetime,
    profile_partition: str = "all",
    include_property_keys: bool = True,
    include_users: bool = True,
) -> dict[str, Any]:
    client = DirectDRFClient(target.project, target.principal)
    spec = TARGETS[target.name]
    kinds = ("voice",) if spec["surface"] == "voice" else ("trace", "span", "session")
    lanes = []
    list_lanes: list[tuple[str, str, str, dict[str, Any]]] = []
    if include_property_keys:
        lanes.append(
            _run_lane(
                f"target.{target.name}.property_keys.read_more",
                lambda: _qualify_key_read_more(
                    client, lane=f"target.{target.name}.property_keys"
                ),
            )
        )
    profiles = _matrix_filter_profiles(target, partition=profile_partition)
    for window_name, duration in WINDOWS:
        start = end - duration
        for kind in kinds:
            for mode, profile_filters in profiles:
                lane = f"observe.{target.name}.{window_name}.{kind}.{mode}"
                full_protocol = _full_list_protocol_required(
                    mode=mode,
                    density=str(spec["density"]),
                    window_name=window_name,
                )
                record = _run_lane(
                    lane,
                    lambda kind=kind, start=start, filters=profile_filters, lane=lane, full_protocol=full_protocol: (
                        (
                            _qualify_list_protocol
                            if full_protocol
                            else _qualify_list_first_page
                        )(
                            client,
                            kind=kind,
                            filters=[_time_filter(start, end), *filters],
                            lane=lane,
                        )
                    ),
                )
                lanes.append(record)
                list_lanes.append((window_name, kind, mode, record))
        if include_users and target.name in {"whatfix", "colektia"}:
            users_record = _run_lane(
                f"observe.{target.name}.{window_name}.users.default",
                lambda start=start, window_name=window_name: _qualify_list_protocol(
                    client,
                    kind="users",
                    filters=[_time_filter(start, end)],
                    lane=f"observe.{target.name}.{window_name}.users.default",
                ),
            )
            lanes.append(users_record)
            list_lanes.append((window_name, "users", "default", users_record))
    expected_profiles, missing_positive_profiles = (
        _missing_long_window_positive_profiles(list_lanes)
    )
    failed_lanes = [lane["lane"] for lane in lanes if not lane["qualified"]]
    # An exact empty negative page proves the API behaved correctly, but not
    # that this named tenant contains a complement witness. Keep population
    # evidence separate from route failures while still failing release
    # qualification when a required long-window witness is absent.
    observed_exact_value_proofs = _observed_exact_value_proofs(list_lanes)
    missing_observed_exact_profiles = _missing_observed_exact_profiles(
        list_lanes,
        observed_exact_value_proofs,
    )
    gap_profiles = list(
        dict.fromkeys([*missing_positive_profiles, *missing_observed_exact_profiles])
    )
    population_gaps = _long_window_population_gaps(gap_profiles)
    membership_failures = _profile_membership_conflicts(list_lanes)
    return {
        "qualified": not (failed_lanes or population_gaps or membership_failures),
        "project_id": str(target.project.id),
        "project_name": str(target.project.name),
        "tenant_surface": spec["surface"],
        "profile_density": spec["density"],
        "profile_partition": profile_partition,
        "property_key_digest": _digest(target.key),
        "property_value_type": target.value_type,
        "eval_property_digest": _digest(target.eval_profile.property_id),
        "eval_output_type": target.eval_profile.output_type,
        "annotation_property_digest": _digest(target.annotation_profile.property_id),
        "annotation_output_type": target.annotation_profile.output_type,
        "target_profile_binding": _target_profile_binding(target),
        "lane_count": len(lanes),
        "long_window_positive_profiles": [
            profile
            for profile in expected_profiles
            if ".".join(profile) not in missing_positive_profiles
        ],
        "failed_lanes": failed_lanes,
        "population_gaps": population_gaps,
        "membership_failures": membership_failures,
        "observed_exact_value_proofs": observed_exact_value_proofs,
    }


def _qualifier_shard() -> str:
    shard = os.environ.get("QUALIFIER_SHARD", "")
    if shard not in QUALIFIER_SHARDS:
        raise SafetyViolation("qualifier shard is absent or invalid")
    return shard


def _qualifier_run_identity() -> tuple[str, datetime]:
    run_id = os.environ.get("QUALIFIER_RUN_ID", "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", run_id):
        raise SafetyViolation("qualifier run id is absent or invalid")
    raw_end = os.environ.get("QUALIFIER_END_UTC", "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", raw_end):
        raise SafetyViolation("qualifier frozen end is absent or invalid")
    try:
        end = datetime.strptime(raw_end, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise SafetyViolation("qualifier frozen end is invalid") from exc
    now = datetime.now(UTC)
    if end > now + timedelta(minutes=1) or now - end > QUALIFIER_END_MAX_AGE:
        raise SafetyViolation("qualifier frozen end is outside the launch window")
    return run_id, end


def validate_shard_result_set(payloads: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Validate that all separately captured JSON results form one exact run."""

    rows = list(payloads)
    if len(rows) != len(QUALIFIER_SHARDS):
        raise QualificationFailure("shard result set is incomplete")
    by_shard: dict[str, dict[str, Any]] = {}
    for payload in rows:
        shard = str(payload.get("shard") or "")
        if shard not in QUALIFIER_SHARDS or shard in by_shard:
            raise QualificationFailure("shard result set has an invalid identity")
        if payload.get("qualified") is not True or payload.get("exit_code") != 0:
            raise QualificationFailure(f"shard {shard} did not qualify")
        by_shard[shard] = payload
    if set(by_shard) != set(QUALIFIER_SHARDS):
        raise QualificationFailure("shard result set is incomplete")

    target_bindings: dict[tuple[str, str], str] = {}
    for shard, payload in by_shard.items():
        if payload.get("shard_index") != QUALIFIER_SHARDS.index(shard):
            raise QualificationFailure(f"shard {shard} has an invalid index")
        if payload.get("shard_count") != len(QUALIFIER_SHARDS):
            raise QualificationFailure(f"shard {shard} has an invalid count")
        targets = payload.get("targets") or {}
        if not isinstance(targets, dict) or set(targets) != set(
            SHARD_TARGET_NAMES[shard]
        ):
            raise QualificationFailure(f"shard {shard} target set is not exact")
        for target_name in SHARD_TARGET_NAMES[shard]:
            target = targets.get(target_name)
            profile_binding = (
                target.get("target_profile_binding")
                if isinstance(target, dict) and target.get("qualified") is True
                else None
            )
            binding_digest = (
                profile_binding.get("binding_sha256")
                if isinstance(profile_binding, dict)
                else None
            )
            if not isinstance(binding_digest, str) or not re.fullmatch(
                r"[0-9a-f]{64}", binding_digest
            ):
                raise QualificationFailure(
                    f"shard {shard} target binding is incomplete"
                )
            if target.get("profile_partition") != SHARD_PROFILE_PARTITIONS[shard]:
                raise QualificationFailure(
                    f"shard {shard} target partition is incorrect"
                )
            target_bindings[(shard, target_name)] = binding_digest
    binding_groups = {
        "whatfix": ("whatfix", "trace_system", "whatfix_graphs"),
        "colektia": ("colektia", "trace_system", "colektia_graphs"),
    }
    for target_name, shards in binding_groups.items():
        bindings = {target_bindings[(shard, target_name)] for shard in shards}
        if len(bindings) != 1:
            raise QualificationFailure(
                f"{target_name} shards selected different target profiles"
            )

    expected_windows = [name for name, _duration in WINDOWS]
    expected_kinds = ["trace", "span", "session", "dashboard"]
    graph_lane_count = len(expected_windows) * len(expected_kinds) * 11
    for graph_shard, target_name in GRAPH_SHARD_TARGETS.items():
        graph_result = (by_shard[graph_shard].get("ancillary") or {}).get(
            "rollup_safe_graphs"
        ) or {}
        expected_population_profiles = sorted(
            f"{kind}.{profile}"
            for kind in expected_kinds
            for profile in _graph_profile_names(target_name)
        )
        population = graph_result.get("long_window_population") or {}
        if (
            graph_result.get("qualified") is not True
            or graph_result.get("target_name") != target_name
            or graph_result.get("profile_density") != TARGETS[target_name]["density"]
            or graph_result.get("windows") != expected_windows
            or graph_result.get("kinds") != expected_kinds
            or graph_result.get("profiles") != list(_graph_profile_names(target_name))
            or graph_result.get("lane_count") != graph_lane_count
            or graph_result.get("failed_lanes") != []
            or population.get("qualified") is not True
            or population.get("expected_profiles") != expected_population_profiles
            or population.get("missing_profiles") != []
        ):
            raise QualificationFailure(
                f"shard {graph_shard} graph matrix proof is incomplete"
            )

    whatfix_ancillary = by_shard["whatfix_graphs"].get("ancillary") or {}
    required_ancillary_lanes = (
        "dataset_exact",
        "metrics_catalog",
        "model_values",
        "simulation_previews",
    )
    if any(
        not isinstance(whatfix_ancillary.get(name), dict)
        or whatfix_ancillary[name].get("qualified") is not True
        for name in required_ancillary_lanes
    ):
        raise QualificationFailure("Whatfix ancillary API proof is incomplete")
    dataset_evidence = whatfix_ancillary["dataset_exact"].get("evidence") or {}
    metrics_evidence = whatfix_ancillary["metrics_catalog"].get("evidence") or {}
    dataset_binding = dataset_evidence.get("dataset_representative_binding_sha256")
    dataset_property_digest = dataset_evidence.get("dataset_column_property_id_digest")
    if (
        not isinstance(dataset_binding, str)
        or re.fullmatch(r"[0-9a-f]{64}", dataset_binding) is None
        or not isinstance(dataset_property_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", dataset_property_digest) is None
        or metrics_evidence.get("dataset_column_definition_proven") is not True
        or metrics_evidence.get("selected_property_kind") != "dataset_column"
        or metrics_evidence.get("selected_property_id_digest")
        != dataset_property_digest
        or metrics_evidence.get("dataset_representative_binding_sha256")
        != dataset_binding
    ):
        raise QualificationFailure(
            "metrics and dataset lanes do not share one dataset-column representative"
        )

    def binding(payload: dict[str, Any]) -> tuple[Any, ...]:
        source = payload.get("source_identity") or {}
        return (
            payload.get("schema"),
            payload.get("run_id"),
            payload.get("frozen_end"),
            source.get("base_commit"),
            source.get("derived_image_digest"),
            source.get("source_manifest_sha256"),
            source.get("qualifier_sha256"),
        )

    bindings = {binding(payload) for payload in by_shard.values()}
    if len(bindings) != 1:
        raise QualificationFailure("shard results do not share one source-bound run")
    only_binding = next(iter(bindings))
    if any(value in (None, "") for value in only_binding):
        raise QualificationFailure("shard result binding is incomplete")
    return {
        "qualified": True,
        "run_id": only_binding[1],
        "frozen_end": only_binding[2],
        "shards": list(QUALIFIER_SHARDS),
        "graph_targets": list(GRAPH_SHARD_TARGETS.values()),
        "binding_digest": _digest(only_binding),
    }


def _run() -> dict[str, Any]:
    global _qualifier_deadline_monotonic

    if sys.argv != ["/harness/qualify.py"]:
        raise SafetyViolation("qualification accepts no command arguments")
    _qualifier_deadline_monotonic = time.monotonic() + QUALIFIER_WALL_SECONDS
    required_environment = {
        "NO_STARTUP_DB_MUTATIONS": "true",
        "STARTUP_DB_MUTATION_MODE": "disabled",
        "DJANGO_CACHE_BACKEND": "locmem",
        "PROPERTY_CATALOG_READ_MODE": "read",
        "PROPERTY_CATALOG_DEV_READ_ACK": (
            "I_ACKNOWLEDGE_DEV_ONLY_UNIFIED_PROPERTY_CATALOG"
        ),
        "SPAN_ATTRIBUTE_CATALOG_READ_MODE": "off",
        "SPAN_ATTRIBUTE_CATALOG_DEV_SNAPSHOT_ENABLED": "false",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    drift = {
        key: os.environ.get(key)
        for key, expected in required_environment.items()
        if os.environ.get(key) != expected
    }
    if drift:
        raise SafetyViolation("mutation-free environment contract drifted")
    source_identity = _verify_source_identity()
    static_guard_self_test()
    _install_pg_guard()
    _install_ch_guard()

    logging.disable(logging.CRITICAL)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
    import django

    startup_preload, settings = _bootstrap_reviewed_django_runtime(django.setup)
    _install_request_context_hook()
    if "testserver" not in settings.ALLOWED_HOSTS:
        settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, "testserver"]
    database_defaults = _verify_database_defaults()

    shard = _qualifier_shard()
    run_id, end = _qualifier_run_identity()
    shard_target_names = SHARD_TARGET_NAMES[shard]
    graph_target_name = GRAPH_SHARD_TARGETS.get(shard)
    run_target_matrix = graph_target_name is None
    run_whatfix_ancillary = shard == "whatfix_graphs"
    profile_partition = SHARD_PROFILE_PARTITIONS[shard]
    target_results: dict[str, Any] = {}
    selected: dict[str, Target] = {}
    for name in shard_target_names:
        spec = TARGETS[name]
        selection = _run_lane(
            f"target.{name}.rediscovery",
            lambda name=name, spec=spec: {
                "target": (target := _select_target(name, spec)),
                "qualified": True,
                "project_id": str(target.project.id),
                "project_name": str(target.project.name),
            },
        )
        # _run_lane serializes evidence only at final JSON time; retain the
        # actual Target through a second deterministic selection only if the
        # first selection passed. This would duplicate CH profile calls, so
        # instead retrieve the object stored in the evidence before output.
        target_object = selection["evidence"].pop("target", None)
        if isinstance(target_object, Target):
            if str(target_object.principal.workspace.id) not in {
                str(value)
                for value in settings.PROPERTY_CATALOG_DEV_WORKSPACE_ALLOWLIST
            }:
                raise SafetyViolation(
                    f"target {name} is outside the catalog workspace allowlist"
                )
            selected[name] = target_object
            if run_target_matrix:
                target_results[name] = _qualify_target(
                    target_object,
                    end=end,
                    profile_partition=profile_partition,
                    include_property_keys=shard != "trace_system",
                    include_users=shard in {"whatfix", "colektia"},
                )
            else:
                target_results[name] = {
                    "qualified": True,
                    "selection_only": True,
                    "project_id": str(target_object.project.id),
                    "project_name": str(target_object.project.name),
                    "profile_partition": profile_partition,
                    "target_profile_binding": _target_profile_binding(target_object),
                }
        else:
            target_results[name] = {
                "qualified": False,
                "reason": selection.get("reason") or "target_not_selected",
            }

    graph_target = selected.get(graph_target_name or "")
    ancillary: dict[str, Any] = {}
    if graph_target_name is not None and graph_target is not None:
        graph_client = DirectDRFClient(graph_target.project, graph_target.principal)
        if run_whatfix_ancillary:
            representative_selection = _run_lane(
                "api.dataset.representative",
                lambda: {
                    "qualified": True,
                    "representative": _select_dataset_representative(graph_client),
                },
            )
            representative = representative_selection["evidence"].pop(
                "representative", None
            )

            def require_representative() -> DatasetRepresentative:
                if not isinstance(representative, DatasetRepresentative):
                    raise PopulationGap(
                        "the mandatory dataset representative was not selected"
                    )
                return representative

            ancillary["dataset_representative"] = representative_selection
            ancillary["dataset_exact"] = _run_lane(
                "api.dataset.exact_snapshot",
                lambda: _qualify_dataset_exact(
                    graph_client,
                    lane="api.dataset.exact_snapshot",
                    representative=require_representative(),
                ),
            )
            ancillary["metrics_catalog"] = _run_lane(
                "api.dashboard.metrics.registry",
                lambda: _qualify_metrics_catalog(
                    graph_client,
                    lane="api.dashboard.metrics.registry",
                    expected_property_ids=(
                        "system_attribute:traces:model",
                        f"custom_attribute:{graph_target.key}",
                        graph_target.eval_profile.property_id,
                        graph_target.annotation_profile.property_id,
                    ),
                    required_dataset_representative=require_representative(),
                ),
            )
            ancillary["model_values"] = _run_lane(
                "api.dashboard.filter_values.model",
                lambda: _qualify_model_values(
                    graph_client, lane="api.dashboard.filter_values.model"
                ),
            )
            ancillary["simulation_previews"] = _run_lane(
                "api.simulation.exact_previews",
                lambda: _qualify_simulation_previews(
                    graph_client, lane="api.simulation.exact_previews"
                ),
            )
        ancillary["rollup_safe_graphs"] = _qualify_graph_matrix(
            graph_target,
            end=end,
        )
    elif graph_target_name is not None:
        ancillary["qualified"] = False
        ancillary["reason"] = f"no {graph_target_name} graph target was selected"
    else:
        ancillary["qualified"] = True
        ancillary["scheduled_on_shards"] = list(GRAPH_SHARD_TARGETS)

    required_lanes = [lane for lane in _lane_records if lane["required"]]
    optional_lanes = [lane for lane in _lane_records if not lane["required"]]
    qualified = (
        bool(required_lanes)
        and all(lane["qualified"] for lane in required_lanes)
        and all(result.get("qualified") is True for result in target_results.values())
    )
    return {
        "qualified": qualified,
        "source_identity": source_identity,
        "startup_url_preload": startup_preload,
        "database_defaults": database_defaults,
        "catalog_read_mode": "read",
        "catalog_database": settings.PROPERTY_CATALOG_DATABASE,
        "run_id": run_id,
        "shard": shard,
        "shard_index": QUALIFIER_SHARDS.index(shard),
        "shard_count": len(QUALIFIER_SHARDS),
        "frozen_end": end.isoformat(),
        "windows": [name for name, _duration in WINDOWS],
        "targets": target_results,
        "ancillary": ancillary,
        "required_lane_count": len(required_lanes),
        "required_failed_lanes": [
            lane["lane"] for lane in required_lanes if not lane["qualified"]
        ],
        "optional_lane_count": len(optional_lanes),
        "optional_gaps": [
            {"lane": lane["lane"], "reason": lane["reason"]}
            for lane in optional_lanes
            if not lane["qualified"]
        ],
    }


def main() -> int:
    started = time.monotonic()
    payload: dict[str, Any] = {}
    error = None
    exit_code = 1
    try:
        payload = _run()
        blocked = [
            key
            for key in (
                "pg_blocked",
                "ch_blocked",
                "redis_blocked",
                "celery_blocked",
                "temporal_blocked",
                "scheduler_blocked",
                "external_cache_blocked",
            )
            if _snapshot_counts()[key]
        ]
        if blocked:
            raise SafetyViolation("one or more mutation tripwires activated")
        exit_code = 0 if payload.get("qualified") else 1
    except Exception as exc:
        error = {
            "type": type(exc).__name__,
            "detail": _redact(exc),
            "traceback_digest": _digest(traceback.format_exc()),
        }
        exit_code = 2 if isinstance(exc, SafetyViolation) else 1
    finally:
        try:
            from tfc.middleware.workspace_context import clear_workspace_context

            clear_workspace_context()
        except Exception:
            pass
        _active_context.clear()
    output = {
        "schema": SCHEMA,
        "invocation": "direct_authenticated_drf_in_process",
        "select_only": {
            "postgresql_default_read_only": True,
            "postgresql_statement_guard": True,
            "postgresql_statement_timeout_ms": PG_TIMEOUT_MS,
            "clickhouse_readonly": 2,
            "clickhouse_server_enforced": _ch_server_enforced_readonly(),
            "clickhouse_max_execution_seconds": CH_TIMEOUT_SECONDS,
            "external_redis_blocked": True,
            "async_dispatch_blocked": True,
            "catalog_reads_for_deleted_tables": False,
        },
        "counts": _snapshot_counts(),
        "startup_url_preload": dict(_startup_preload_evidence),
        "qualifier_wall_seconds": QUALIFIER_WALL_SECONDS,
        "request_fuse": MAX_REQUESTS,
        "clickhouse_read_fuse": MAX_CH_READS,
        "requests": _request_records,
        "lanes": _lane_records,
        "local_cache_footprint": _cache_footprint,
        "elapsed_s": round(time.monotonic() - started, 3),
        "exit_code": exit_code,
        "error": error,
        **payload,
    }
    print(json.dumps(output, sort_keys=True, separators=(",", ":"), default=str))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
