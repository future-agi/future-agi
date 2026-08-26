#!/usr/bin/env python3
"""DEV-only Kartik analogue callback smoke; never release qualification."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import ipaddress
import json
import logging
import math
import os
import re
import socket
import stat
import sys
import tempfile
import time
import traceback
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

# Suppress every library/runtime write.  The one controlled JSON record is
# emitted through the saved descriptor after all callbacks and cleanup finish.
_CONTROLLED_OUTPUT_FD = os.dup(1)
_DEVNULL_FD = os.open(os.devnull, os.O_WRONLY)
os.dup2(_DEVNULL_FD, 1)
os.dup2(_DEVNULL_FD, 2)

q: Any = None
SafetyViolation: Any = None
SCHEMA: Any = None
SMOKE_SCHEMA = "fi-kartik-dev-analogue-functional-smoke/0816h/v1"
EXPECTED_RUNTIME_WRAPPER_PATH = "/run/fi-kartik-smoke-0816h/kartik-smoke-0816h.py"
REGISTRY_HANDOFF_PATH = "/run/fi-kartik-smoke-0816h/registry-profile-handoff.json"
ATTRIBUTE_CURSOR_CACHE_TMPFS = Path("/tmp")
ATTRIBUTE_CURSOR_CACHE_PREFIX = "fi-kartik-smoke-0816h-cursor-"
ATTRIBUTE_CURSOR_CACHE_MIN_FREE_BYTES = 8 * 1024 * 1024
ATTRIBUTE_CURSOR_CACHE_MAX_ENTRIES = 8192
ATTRIBUTE_CURSOR_CACHE_MAX_BYTES = 48 * 1024 * 1024
METRIC_CATALOG_FROZEN_PAGE_FUSE = 8
METRIC_CATALOG_DEV_PAGE_FUSE = 64
MODEL_VALUES_FROZEN_LOOKBACK_DAYS = 7
MODEL_VALUES_DEV_LOOKBACK_DAYS = 366
MODEL_VALUES_QUALIFICATION_PAGE_SIZE = 1
PHASES = ("registry", "matrix")
EXPECTED_WINDOWS = ("30m", "1h", "6h", "24h", "7d", "30d", "90d", "180d", "365d")
FROZEN_QUALIFIER_SHA256 = (
    "b2f6eb21b1850c60569ced2d7ba4b1e9f067d3c008e908c63e0293dc84da1ca7"
)
FROZEN_BASE_COMMIT = "041084a8bfea8e5e7f66b87d7f6883c57659729b"
FROZEN_IMAGE_ID = (
    "sha256:2cca69ea9577f041635825c48ffe464443e21b838fb6ff25ea659e36208aa85a"
)
FROZEN_LOCAL_IMAGE_TAG = "fi-current-select:0816k-df6e48b0"
FROZEN_SOURCE_MANIFEST_SHA256 = (
    "df6e48b0bc1909e0dbaa8cb4a1188c126282f0658e6abdcb374b8eaeffe19309"
)
FROZEN_RUNTIME_OVERLAY_SHA256 = (
    "77cbff5c5a31d765f70232232e6f404783c4eeb0ff625afc0d8820c28c5d9b34"
)
FROZEN_HARNESS_SHA256 = (
    "2ab4031c526e76b32e8eafe4ea90d06ee272c10edf26f49b2ba2a02b9fed239b"
)
FROZEN_BUNDLE_MANIFEST_SHA256 = (
    "f346c4276f61452311c6b573c907e70ba1997f8bafbdc363b076b16cf15d07dd"
)
FROZEN_DOCKERFILE_SHA256 = (
    "394bbc47c9460c9f4480d1d6e4752f7288197770bfb818f57c98f5da7956bcde"
)
FROZEN_JOB_TEMPLATE_SHA256 = (
    "fac4d9cc514c473ede38cf94c2d0a9ac2d1dc43828991eb8f95ef6defdfb8393"
)
FROZEN_CATALOG_ACTIVATION_MANIFEST_SHA256 = (
    "cb2d0f5b02c1d3583b88714a19024a2749cdebccdc68c1152d928d213bc5e7f5"
)
FROZEN_CATALOG_ACTIVATION_SHA256 = (
    "eaf46e66b153c2e91ed6c9aaa8aeab15c3f029933b834de24d9ea18a1e3694f3"
)
FROZEN_CATALOG_DATABASE = "fi_catalog_dev_kartik_0816h"
FROZEN_CATALOG_EPOCH = 5
FROZEN_CATALOG_REVISION = 1
FROZEN_VOICE_PROJECT_UUID_SHA256 = (
    "66adaefb7add50829843d2d401ef5de69f393b9d487c3880b154dfe7f79e9334"
)
FROZEN_TRACE_PROJECT_UUID_SHA256 = (
    "e0408cb8030c36cfe32a48695b39fbc6027a3a0ab6a89b8a3c61fa0722a16a75"
)
FROZEN_EXCLUDED_PROJECT_UUID_SHA256 = (
    "a3bb9114b6a35293787e8e1be13006b2f5ec6892b20fe949b1d465b400c48b24"
)
FROZEN_CANONICAL_TENANT_BINDING_SHA256 = (
    "04f7a572abf94e02707a1766fdade8660b1693bcefede1c0bb3be18a867a1ce0"
)
LONG_WINDOWS = {"30d", "90d", "180d", "365d"}
CATALOG_TABLES = (
    "property_catalog_activations",
    "property_catalog_checkpoints",
    "property_catalog_deliveries",
    "property_catalog_source_streams",
    "property_definition_catalog",
    "span_attribute_value_catalog",
)
SOURCE_SELECT_GRANT_COLUMNS = (
    ("futureagi.end_user_id_remap", ("new_id", "old_id")),
    (
        "futureagi.end_users",
        ("end_user_id", "is_deleted", "project_id", "user_id", "version"),
    ),
    (
        "futureagi.model_hub_score",
        (
            "_peerdb_is_deleted",
            "_peerdb_version",
            "created_at",
            "deleted",
            "id",
            "label_id",
            "observation_span_id",
            "trace_id",
            "value",
        ),
    ),
    ("futureagi.spans", ("*",)),
    (
        "futureagi.tracer_eval_logger",
        (
            "_peerdb_is_deleted",
            "_peerdb_version",
            "created_at",
            "custom_eval_config_id",
            "deleted",
            "error",
            "id",
            "observation_span_id",
            "output_bool",
            "output_float",
            "output_str",
            "output_str_list",
            "skipped_reason",
            "status",
            "trace_id",
        ),
    ),
    ("futureagi.traces", ("_version", "id", "is_deleted", "project_id", "tags")),
    ("system.parts", ("active", "database", "min_time", "table")),
)
SOURCE_DICTGET_GRANT_ATTRIBUTES = (
    ("futureagi.end_users_dict", ("user_id", "user_id_type", "user_id_hash")),
)
SOURCE_PROBES = (
    "SELECT new_id,old_id FROM futureagi.end_user_id_remap WHERE 0",
    "SELECT end_user_id,is_deleted,project_id,user_id,version "
    "FROM futureagi.end_users WHERE 0",
    "SELECT _peerdb_is_deleted,_peerdb_version,created_at,deleted,id,label_id,"
    "observation_span_id,trace_id,value FROM futureagi.model_hub_score WHERE 0",
    "SELECT 1 FROM futureagi.spans WHERE 0",
    "SELECT _peerdb_is_deleted,_peerdb_version,created_at,"
    "custom_eval_config_id,deleted,error,id,observation_span_id,output_bool,"
    "output_float,output_str,output_str_list,skipped_reason,status,trace_id "
    "FROM futureagi.tracer_eval_logger WHERE 0",
    "SELECT _version,id,is_deleted,project_id,tags FROM futureagi.traces WHERE 0",
    "SELECT dictGetOrNull('futureagi.end_users_dict','user_id',"
    "toUUID('00000000-0000-0000-0000-000000000000')),"
    "dictGetOrNull('futureagi.end_users_dict','user_id_type',"
    "toUUID('00000000-0000-0000-0000-000000000000')),"
    "dictGetOrNull('futureagi.end_users_dict','user_id_hash',"
    "toUUID('00000000-0000-0000-0000-000000000000'))",
    "SELECT active,database,min_time,table FROM system.parts WHERE 0",
)
SOURCE_PROBE_KINDS = (
    "getSetting",
    "futureagi.end_user_id_remap",
    "futureagi.end_users",
    "futureagi.model_hub_score",
    "futureagi.spans",
    "futureagi.tracer_eval_logger",
    "futureagi.traces",
    "futureagi.end_users_dict",
    "system.parts",
)
FROZEN_SOURCE_GRANT_INVENTORY_SHA256 = (
    "0ee7e421090dd879dcd7e8cf520e45e69240dc910684948fe204bf6466e35c58"
)
FROZEN_SOURCE_SHOW_GRANTS_COUNT = 8
FROZEN_SOURCE_SHOW_GRANTS_SHA256 = (
    "238cf2b033ffba3190b029984b813348eb1d17b5674943b30b7a4c222ef8d8c8"
)
FROZEN_SOURCE_SYSTEM_GRANTS_ROW_COUNT = 42
FROZEN_SOURCE_SYSTEM_GRANTS_SHA256 = (
    "1404d513e2af97dafa041aba113e5542fa2f41d79d01d29e4097bfb22ad691cb"
)
FIXED = {
    "ANNOTATION_SCORE_VALUE_PROJECTION_READ_ENABLED": "false",
    "AWS_EC2_METADATA_DISABLED": "true",
    "CH25_QUERY_TYPES_DISABLED": "",
    "CH25_QUERY_TYPES_SHADOW": "",
    "CH25_QUERY_TYPES_V2_ONLY": "TRACE_LIST,SPAN_LIST",
    "CH25_QUERY_TYPES_V2_PRIMARY": "",
    "CH25_SERVER_ENFORCED_READONLY": "true",
    "CH_DUAL_WRITE": "false",
    "CH_SERVER_ENFORCED_READONLY": "true",
    "CLOUD_DEPLOYMENT": "DEV",
    "DJANGO_CACHE_BACKEND": "locmem",
    "DJANGO_SETTINGS_MODULE": "tfc.settings.settings",
    "ENABLE_INTEGRATIONS": "false",
    "ENV_TYPE": "development",
    "KARTIK_SMOKE_EVIDENCE_LABEL": "0816h",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "NO_STARTUP_DB_MUTATIONS": "true",
    "PGBOUNCER_READ_HOST": "",
    "PG_DIRECT_HOST": "",
    "PGOPTIONS": "-c default_transaction_read_only=on -c statement_timeout=9500",
    "PROPERTY_CATALOG_DEV_READ_ACK": "I_ACKNOWLEDGE_DEV_ONLY_UNIFIED_PROPERTY_CATALOG",
    "PROPERTY_CATALOG_DEV_RECONCILE_ENABLED": "false",
    "PROPERTY_CATALOG_CH_DATABASE": FROZEN_CATALOG_DATABASE,
    "PROPERTY_CATALOG_DATABASE": FROZEN_CATALOG_DATABASE,
    "PROPERTY_CATALOG_READ_MODE": "read",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONPATH": "/harness:/app/backend",
    "QUALIFIER_SOS_FORBIDDEN": "true",
    "READ_REPLICA_OPT_IN": "",
    "SENTRY_DSN": "",
    "SPAN_ATTRIBUTE_CATALOG_DEV_SNAPSHOT_ENABLED": "false",
    "SPAN_ATTRIBUTE_CATALOG_READ_MODE": "off",
    "STARTUP_DB_MUTATION_MODE": "disabled",
    "TZ": "UTC",
}
REQUIRED = (
    "CH25_DATABASE",
    "CH25_HOST",
    "CH25_HTTP_PORT",
    "CH25_PASSWORD",
    "CH25_TCP_PORT",
    "CH25_USER",
    "CH_DATABASE",
    "CH_HOST",
    "CH_PASSWORD",
    "CH_PORT",
    "CH_USERNAME",
    "EXPECTED_BASE_COMMIT",
    "EXPECTED_IMAGE_ID",
    "EXPECTED_KARTIK_SMOKE_0816H_SHA256",
    "EXPECTED_QUALIFIER_SHA256",
    "EXPECTED_SOURCE_MANIFEST_SHA256",
    "KARTIK_CANONICAL_VOICE_PROJECT_ID",
    "KARTIK_CANONICAL_TRACE_PROJECT_ID",
    "KARTIK_EXCLUDED_PROJECT_UUID_SHA256",
    "KARTIK_SMOKE_END_UTC",
    "KARTIK_SMOKE_RUN_ID",
    "KARTIK_SMOKE_SOURCE_AUTH_IPV4",
    "PGBOUNCER_HOST",
    "PGBOUNCER_PORT",
    "PG_DB",
    "PG_PASSWORD",
    "PG_USER",
    "PROPERTY_CATALOG_CH_DATABASE",
    "PROPERTY_CATALOG_CH_HOST",
    "PROPERTY_CATALOG_CH_PASSWORD",
    "PROPERTY_CATALOG_CH_PORT",
    "PROPERTY_CATALOG_CH_USER",
    "PROPERTY_CATALOG_DATABASE",
    "PROPERTY_CATALOG_DEV_WORKSPACE_ALLOWLIST",
    "SECRET_KEY",
)
FORBIDDEN_ENV_KEYS = (
    "KARTIK_ACTIVATED_DENSE_VOICE_PROJECT_ID",
    "KARTIK_ACTIVATED_SPARSE_TRACE_PROJECT_ID",
    "KARTIK_NONACTIVATED_DENSE_TRACE_PROJECT_ID",
    "FI_KARTIK_ACTIVATED_DENSE_VOICE_PROJECT_ID",
    "FI_KARTIK_ACTIVATED_SPARSE_TRACE_PROJECT_ID",
    "FI_KARTIK_END_UTC",
    "FI_KARTIK_NONACTIVATED_DENSE_TRACE_PROJECT_ID",
)
EXPECTED_ENV_KEYS = tuple(sorted(set(FIXED) | set(REQUIRED)))
EXPECTED_ENV_KEY_COUNT = 68
EXPECTED_ENV_KEY_SHA256 = (
    "10620b57ca02f6275ea2df4545954e6f1dfcfd3abd63b2371313ded6f0be2e09"
)
REASON_CODES = frozenset(
    {
        "NO_LONG_WINDOW_CONTINUATION_WITNESS",
        "NO_POSITIVE_LONG_WINDOW_WITNESS",
        "POPULATION_GAP",
        "ROUTE_EXCEPTION",
        "TERMINAL_MODEL_PAGE",
        "TERMINAL_PROPERTY_KEY_PAGE",
        "TERMINAL_REGISTRY_PAGE",
    }
)
failures: list[dict[str, Any]] = []
gaps: list[dict[str, Any]] = []
cells: list[dict[str, Any]] = []
_attribute_cursor_cache: Any = None
_attribute_cursor_cache_original: Any = None
_attribute_cursor_cache_path: Path | None = None
_attribute_cursor_cache_installed = False
_attribute_cursor_cache_cleanup_complete = False


def _install_attribute_cursor_cache(phase: str) -> dict[str, Any]:
    """Share opaque cursor state across individually supervised fork children."""

    global _attribute_cursor_cache, _attribute_cursor_cache_original
    global _attribute_cursor_cache_path, _attribute_cursor_cache_installed
    global _attribute_cursor_cache_cleanup_complete
    if (
        phase not in PHASES
        or _attribute_cursor_cache is not None
        or _attribute_cursor_cache_original is not None
        or _attribute_cursor_cache_path is not None
        or _attribute_cursor_cache_installed
    ):
        raise SafetyViolation("attribute cursor cache lifecycle drifted")
    root_info = ATTRIBUTE_CURSOR_CACHE_TMPFS.lstat()
    if (
        not stat.S_ISDIR(root_info.st_mode)
        or stat.S_ISLNK(root_info.st_mode)
        or stat.S_IMODE(root_info.st_mode) != 0o1777
    ):
        raise SafetyViolation("attribute cursor cache tmpfs root drifted")
    try:
        mount_rows = (
            Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
        )
    except OSError as exc:
        raise SafetyViolation(
            "attribute cursor cache tmpfs cannot be attested"
        ) from exc
    tmp_mount = None
    for row in mount_rows:
        fields = row.split()
        if len(fields) > 9 and fields[4] == "/tmp" and "-" in fields:
            separator = fields.index("-")
            tmp_mount = (set(fields[5].split(",")), fields[separator + 1])
            break
    if (
        tmp_mount is None
        or tmp_mount[1] != "tmpfs"
        or not {
            "rw",
            "nosuid",
            "nodev",
            "noexec",
        }.issubset(tmp_mount[0])
    ):
        raise SafetyViolation("attribute cursor cache tmpfs mount drifted")
    filesystem = os.statvfs(ATTRIBUTE_CURSOR_CACHE_TMPFS)
    if (
        filesystem.f_frsize * filesystem.f_blocks < ATTRIBUTE_CURSOR_CACHE_MAX_BYTES
        or filesystem.f_frsize * filesystem.f_bavail
        < ATTRIBUTE_CURSOR_CACHE_MIN_FREE_BYTES
    ):
        raise SafetyViolation("attribute cursor cache tmpfs capacity is insufficient")

    import multiprocessing

    from django.core.cache import cache as default_cache_proxy, caches
    from django.core.cache.backends.filebased import FileBasedCache
    from django.core.cache.backends.locmem import LocMemCache
    from django.db import connections
    from tracer.services.clickhouse import attribute_cursor_state

    if (
        not isinstance(caches["default"], LocMemCache)
        or attribute_cursor_state.cache is not default_cache_proxy
    ):
        raise SafetyViolation("default cache isolation drifted")
    path = Path(
        tempfile.mkdtemp(
            prefix=f"{ATTRIBUTE_CURSOR_CACHE_PREFIX}{phase}-",
            dir=ATTRIBUTE_CURSOR_CACHE_TMPFS,
        )
    )
    path.chmod(0o700)
    path_info = path.lstat()
    if (
        not stat.S_ISDIR(path_info.st_mode)
        or stat.S_ISLNK(path_info.st_mode)
        or path_info.st_uid != os.geteuid()
        or stat.S_IMODE(path_info.st_mode) != 0o700
    ):
        path.rmdir()
        raise SafetyViolation("attribute cursor cache directory is unsafe")
    try:
        cache = FileBasedCache(
            str(path),
            {
                "TIMEOUT": 24 * 60 * 60,
                "KEY_PREFIX": "fi-0816h",
                "OPTIONS": {
                    "MAX_ENTRIES": ATTRIBUTE_CURSOR_CACHE_MAX_ENTRIES,
                    "CULL_FREQUENCY": 3,
                },
            },
        )
    except BaseException:
        path.rmdir()
        raise
    _attribute_cursor_cache_original = default_cache_proxy
    _attribute_cursor_cache_path = path
    _attribute_cursor_cache = cache
    _attribute_cursor_cache_installed = True
    _attribute_cursor_cache_cleanup_complete = False
    attribute_cursor_state.cache = cache
    sentinel_key = "cross-fork-startup-self-test"
    sentinel = {"schema": "fi-attribute-cursor-cache/0816h/v1"}

    def write_sentinel() -> None:
        if cache.add(sentinel_key, sentinel, timeout=60) is not True:
            raise RuntimeError("cross-fork cursor cache self-test write failed")

    if "fork" not in multiprocessing.get_all_start_methods():
        raise SafetyViolation("attribute cursor cache requires fork")
    connections.close_all()
    process = multiprocessing.get_context("fork").Process(target=write_sentinel)
    process.start()
    process.join(2.0)
    if process.is_alive():
        process.kill()
        process.join(0.5)
        raise SafetyViolation("attribute cursor cache self-test timed out")
    if process.exitcode != 0 or cache.get(sentinel_key) != sentinel:
        raise SafetyViolation("attribute cursor cache is not visible across forks")
    cache.delete(sentinel_key)
    if cache.get(sentinel_key) is not None:
        raise SafetyViolation("attribute cursor cache self-test could not be deleted")
    return {
        "backend": "FileBasedCache",
        "scope": "private_pod_local_cross_fork",
        "tmpfs": True,
        "max_entries": ATTRIBUTE_CURSOR_CACHE_MAX_ENTRIES,
        "max_bytes": ATTRIBUTE_CURSOR_CACHE_MAX_BYTES,
        "cross_fork_self_test": True,
        "cleanup_required": True,
    }


def _cleanup_attribute_cursor_cache() -> None:
    """Restore LocMem and remove the exact private cache directory."""

    global _attribute_cursor_cache, _attribute_cursor_cache_original
    global _attribute_cursor_cache_path, _attribute_cursor_cache_installed
    global _attribute_cursor_cache_cleanup_complete
    if not _attribute_cursor_cache_installed:
        if any(
            value is not None
            for value in (
                _attribute_cursor_cache,
                _attribute_cursor_cache_original,
                _attribute_cursor_cache_path,
            )
        ):
            raise RuntimeError("attribute cursor cache cleanup state drifted")
        return
    if (
        _attribute_cursor_cache is None
        or _attribute_cursor_cache_original is None
        or _attribute_cursor_cache_path is None
    ):
        raise RuntimeError("attribute cursor cache cleanup state is incomplete")
    from tracer.services.clickhouse import attribute_cursor_state

    if attribute_cursor_state.cache is not _attribute_cursor_cache:
        raise RuntimeError("attribute cursor cache binding drifted before cleanup")
    cache_bytes = sum(
        entry.stat().st_size
        for entry in _attribute_cursor_cache_path.iterdir()
        if entry.is_file() and not entry.is_symlink()
    )
    if cache_bytes > ATTRIBUTE_CURSOR_CACHE_MAX_BYTES:
        raise RuntimeError("attribute cursor cache exceeded its byte ceiling")
    attribute_cursor_state.cache = _attribute_cursor_cache_original
    _attribute_cursor_cache.clear()
    if any(_attribute_cursor_cache_path.iterdir()):
        raise RuntimeError("attribute cursor cache cleanup left state behind")
    _attribute_cursor_cache_path.rmdir()
    if _attribute_cursor_cache_path.exists():
        raise RuntimeError("attribute cursor cache directory removal failed")
    _attribute_cursor_cache = None
    _attribute_cursor_cache_original = None
    _attribute_cursor_cache_path = None
    _attribute_cursor_cache_installed = False
    _attribute_cursor_cache_cleanup_complete = True


def _sha256(value: str | bytes) -> str:
    wire = value if isinstance(value, bytes) else value.encode("utf-8")
    return hashlib.sha256(wire).hexdigest()


def _source_grant_inventory() -> dict[str, list[dict[str, Any]]]:
    return {
        "dictGet": [
            {"object": name, "attributes_exercised": list(attributes)}
            for name, attributes in SOURCE_DICTGET_GRANT_ATTRIBUTES
        ],
        "select": [
            {"object": name, "columns": list(columns)}
            for name, columns in SOURCE_SELECT_GRANT_COLUMNS
        ],
    }


def _source_grant_inventory_sha256() -> str:
    return _sha256(
        json.dumps(
            _source_grant_inventory(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
    )


def _source_system_grant_inventory() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for object_name, columns in SOURCE_SELECT_GRANT_COLUMNS:
        database, table = object_name.split(".", 1)
        rows.extend(
            {
                "access_type": "SELECT",
                "database": database,
                "table": table,
                "column": None if column == "*" else column,
                "is_partial_revoke": 0,
                "grant_option": 0,
            }
            for column in columns
        )
    for object_name, _attributes in SOURCE_DICTGET_GRANT_ATTRIBUTES:
        database, table = object_name.split(".", 1)
        rows.append(
            {
                "access_type": "dictGet",
                "database": database,
                "table": table,
                "column": None,
                "is_partial_revoke": 0,
                "grant_option": 0,
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            row["access_type"],
            row["database"],
            row["table"],
            row["column"] or "",
        ),
    )


def _is_exact_self_show_grants(query: Any) -> bool:
    return type(query) is str and query == "SHOW GRANTS"


def _execute_self_show_grants(client: Any) -> Any:
    original_guard = getattr(q, "assert_ch_read", None)
    if not callable(original_guard):
        raise SafetyViolation("ClickHouse read guard is unavailable")
    request_records = getattr(q, "_request_records", None)
    before_requests = q._snapshot_counts().get("requests")
    if (
        type(before_requests) is not int
        or before_requests != 0
        or type(request_records) is not list
        or request_records
    ):
        raise SafetyViolation("self grant audit escaped pre-callback scope")

    def scoped_guard(query: Any) -> None:
        if _is_exact_self_show_grants(query):
            return
        original_guard(query)

    q.assert_ch_read = scoped_guard
    try:
        return client.execute("SHOW GRANTS")
    finally:
        binding_drifted = q.assert_ch_read is not scoped_guard
        q.assert_ch_read = original_guard
        after_requests = q._snapshot_counts().get("requests")
        if (
            binding_drifted
            or type(after_requests) is not int
            or after_requests != before_requests
            or q._request_records is not request_records
            or request_records
        ):
            raise SafetyViolation("self grant audit guard scope drifted")


def _source_grant_audit(
    client: Any,
    principal: Any,
    expected_principal: Any,
) -> dict[str, Any]:
    if (
        not isinstance(principal, str)
        or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", principal) is None
        or principal != expected_principal
    ):
        raise SafetyViolation("source grant principal shape drifted")
    inventory_sha256 = _source_grant_inventory_sha256()
    expected_rows = _source_system_grant_inventory()
    expected_rows_wire = (
        json.dumps(
            expected_rows, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
        + "\n"
    ).encode("ascii")
    if (
        inventory_sha256 != FROZEN_SOURCE_GRANT_INVENTORY_SHA256
        or len(expected_rows) != FROZEN_SOURCE_SYSTEM_GRANTS_ROW_COUNT
        or _sha256(expected_rows_wire) != FROZEN_SOURCE_SYSTEM_GRANTS_SHA256
    ):
        raise SafetyViolation("source grant inventory binding drifted")

    role_rows = client.execute("SELECT currentRoles()")
    if (
        not isinstance(role_rows, list)
        or len(role_rows) != 1
        or not isinstance(role_rows[0], (list, tuple))
        or len(role_rows[0]) != 1
        or not isinstance(role_rows[0][0], (list, tuple))
        or role_rows[0][0]
    ):
        raise SafetyViolation("source active role set drifted")

    show_rows = _execute_self_show_grants(client)
    normalized: list[str] = []
    terminals = (f" TO {principal}", f" TO `{principal}`")
    for row in show_rows:
        if (
            not isinstance(row, (list, tuple))
            or len(row) != 1
            or not isinstance(row[0], str)
        ):
            raise SafetyViolation("source SHOW GRANTS shape drifted")
        statement = row[0]
        try:
            statement.encode("ascii", "strict")
        except UnicodeEncodeError as exc:
            raise SafetyViolation("source SHOW GRANTS encoding drifted") from exc
        if (
            not statement.startswith("GRANT ")
            or any(
                ord(character) < 0x20 or ord(character) > 0x7E
                for character in statement
            )
            or statement.endswith(" WITH GRANT OPTION")
        ):
            raise SafetyViolation("source SHOW GRANTS content drifted")
        matches = [terminal for terminal in terminals if statement.endswith(terminal)]
        if len(matches) != 1:
            raise SafetyViolation("source SHOW GRANTS principal drifted")
        normalized.append(statement[: -len(matches[0])] + " TO <SOURCE_ROLE>")
    if len(normalized) != len(set(normalized)):
        raise SafetyViolation("source SHOW GRANTS contains duplicates")
    normalized.sort(key=lambda value: value.encode("ascii"))
    show_wire = b"".join(value.encode("ascii") + b"\n" for value in normalized)
    show_sha256 = _sha256(show_wire)
    if (
        len(normalized) != FROZEN_SOURCE_SHOW_GRANTS_COUNT
        or show_sha256 != FROZEN_SOURCE_SHOW_GRANTS_SHA256
    ):
        raise SafetyViolation("source SHOW GRANTS inventory drifted")
    return {
        "grant_inventory_sha256": inventory_sha256,
        "show_grants_normalized_count": len(normalized),
        "show_grants_normalized_sha256": show_sha256,
        "active_role_count": len(role_rows[0][0]),
    }


def _tenant_binding_sha256(organization_id: Any, workspace_id: Any) -> str:
    wire = json.dumps(
        [str(organization_id), str(workspace_id)],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(wire).hexdigest()


def _traceback_sha256() -> str:
    return _sha256(traceback.format_exc())


def _lane(value: str) -> str:
    if re.fullmatch(r"[a-z0-9_.-]{1,240}", value) is None:
        raise RuntimeError("invalid controlled lane")
    return value


def gap(
    lane: str,
    reason_code: str,
    required: bool,
    exc: BaseException | None = None,
) -> None:
    if reason_code not in REASON_CODES:
        raise RuntimeError("invalid controlled reason code")
    record: dict[str, Any] = {
        "lane": _lane(lane),
        "required": bool(required),
        "reason_code": reason_code,
    }
    if exc is not None:
        record.update(
            {
                "exception_type": type(exc).__name__,
                "traceback_sha256": _traceback_sha256(),
            }
        )
    gaps.append(record)


def fail(lane: str, exc: BaseException) -> None:
    failures.append(
        {
            "lane": _lane(lane),
            "reason_code": "ROUTE_EXCEPTION",
            "exception_type": type(exc).__name__,
            "traceback_sha256": _traceback_sha256(),
        }
    )


def _load_runtime() -> None:
    global q, CH_MAX_BYTES_TO_READ, CH_MAX_MEMORY_BYTES, CH_MAX_RESULT_BYTES
    global CH_MAX_RESULT_ROWS, CH_MAX_THREADS, CH_TIMEOUT_SECONDS, SCHEMA
    global SafetyViolation, static_guard_self_test, verify_regular_file
    import qualify as qualifier
    from safety import (
        CH_MAX_BYTES_TO_READ as max_read_bytes,
        CH_MAX_MEMORY_BYTES as max_memory,
        CH_MAX_RESULT_BYTES as max_result_bytes,
        CH_MAX_RESULT_ROWS as max_result_rows,
        CH_MAX_THREADS as max_threads,
        CH_TIMEOUT_SECONDS as timeout_seconds,
        SCHEMA as qualifier_schema,
        SafetyViolation as safety_violation,
        static_guard_self_test as guard_self_test,
        verify_regular_file as verify_file,
    )

    q = qualifier
    CH_MAX_BYTES_TO_READ = max_read_bytes
    CH_MAX_MEMORY_BYTES = max_memory
    CH_MAX_RESULT_BYTES = max_result_bytes
    CH_MAX_RESULT_ROWS = max_result_rows
    CH_MAX_THREADS = max_threads
    CH_TIMEOUT_SECONDS = timeout_seconds
    SCHEMA = qualifier_schema
    SafetyViolation = safety_violation
    static_guard_self_test = guard_self_test
    verify_regular_file = verify_file


def _verify_local_image_source_identity() -> dict[str, Any]:
    """Verify the assembled runtime while binding only a local Docker image ID."""
    expected_manifest_hash = os.environ["EXPECTED_SOURCE_MANIFEST_SHA256"]
    expected_qualifier_hash = os.environ["EXPECTED_QUALIFIER_SHA256"]
    expected_image_id = os.environ["EXPECTED_IMAGE_ID"]
    if os.environ["EXPECTED_BASE_COMMIT"] != q.BASE_COMMIT:
        raise SafetyViolation("base commit environment pin mismatch")
    if (
        expected_image_id != FROZEN_IMAGE_ID
        or re.fullmatch(r"sha256:[0-9a-f]{64}", expected_image_id) is None
    ):
        raise SafetyViolation("local image ID pin is absent or invalid")
    q.verify_regular_file(q.SOURCE_MANIFEST_PATH, expected_manifest_hash)
    q.verify_regular_file(Path(q.__file__), expected_qualifier_hash)
    manifest = json.loads(q.SOURCE_MANIFEST_PATH.read_text(encoding="utf-8"))
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") != q.SCHEMA
        or manifest.get("base_commit") != q.BASE_COMMIT
        or re.fullmatch(
            r"[^\s@]+@sha256:[0-9a-f]{64}",
            str(manifest.get("base_image") or ""),
        )
        is None
    ):
        raise SafetyViolation("source manifest identity is invalid")
    runtime_hashes: dict[str, str] = {}
    values = manifest.get("runtime_files")
    if not isinstance(values, dict):
        raise SafetyViolation("source manifest runtime_files map is invalid")
    for relative, expected_hash in values.items():
        path = q.safe_relative_path(str(relative)).as_posix()
        if path in runtime_hashes and runtime_hashes[path] != expected_hash:
            raise SafetyViolation("source manifest runtime hashes conflict")
        runtime_hashes[path] = str(expected_hash)
    required = manifest.get("runtime_required")
    dirty = manifest.get("runtime_dirty")
    if not isinstance(required, dict) or not isinstance(dirty, dict):
        raise SafetyViolation("source manifest runtime subset maps are invalid")
    for subset_name, subset in (
        ("runtime_required", required),
        ("runtime_dirty", dirty),
    ):
        for relative, expected_hash in subset.items():
            path = q.safe_relative_path(str(relative)).as_posix()
            if runtime_hashes.get(path) != expected_hash:
                raise SafetyViolation(
                    f"source manifest {subset_name} is not bound to runtime_files"
                )
    raw_deletions = manifest.get("runtime_deletions")
    if not isinstance(raw_deletions, list) or not all(
        isinstance(value, str) for value in raw_deletions
    ):
        raise SafetyViolation("source manifest runtime_deletions list is invalid")
    deletions = [q.safe_relative_path(value).as_posix() for value in raw_deletions]
    if deletions != sorted(set(deletions)):
        raise SafetyViolation("source manifest runtime_deletions are not canonical")
    if set(deletions) & set(runtime_hashes):
        raise SafetyViolation("source manifest runtime file/deletion overlap")
    mismatches = []
    for relative, expected_hash in sorted(runtime_hashes.items()):
        path = q.BACKEND_ROOT / relative
        actual = q.sha256_bytes(path.read_bytes()) if path.is_file() else None
        if actual != expected_hash:
            mismatches.append(relative)
    for relative in deletions:
        if os.path.lexists(q.BACKEND_ROOT / relative):
            mismatches.append(relative)
    if mismatches:
        raise SafetyViolation(
            "runtime source hash mismatch: " + ",".join(mismatches[:20])
        )
    return {
        "base_commit": q.BASE_COMMIT,
        "derived_image_id": expected_image_id,
        "local_image_tag": FROZEN_LOCAL_IMAGE_TAG,
        "base_image_digest": manifest["base_image"],
        "source_manifest_sha256": expected_manifest_hash,
        "qualifier_sha256": expected_qualifier_hash,
        "verified_runtime_files": len(runtime_hashes),
        "verified_runtime_deletions": len(deletions),
        "dirty_file_count": len(manifest.get("dirty") or {}),
        "dirty_runtime_file_count": len(dirty),
    }


def environment() -> dict[str, Any]:
    if (
        len(sys.argv) != 3
        or sys.argv[0] != EXPECTED_RUNTIME_WRAPPER_PATH
        or sys.argv[1] != "--phase"
        or sys.argv[2] not in PHASES
    ):
        raise SafetyViolation("wrapper argv contract drifted")
    phase = sys.argv[2]
    if Path(__file__).resolve().as_posix() != EXPECTED_RUNTIME_WRAPPER_PATH:
        raise SafetyViolation("wrapper runtime path drifted")
    key_wire = "\n".join(EXPECTED_ENV_KEYS)
    if (
        len(EXPECTED_ENV_KEYS) != EXPECTED_ENV_KEY_COUNT
        or _sha256(key_wire) != EXPECTED_ENV_KEY_SHA256
    ):
        raise SafetyViolation("embedded environment key contract drifted")
    if tuple(sorted(os.environ)) != EXPECTED_ENV_KEYS:
        raise SafetyViolation("exact environment key set drifted")
    if any(key in os.environ for key in FORBIDDEN_ENV_KEYS):
        raise SafetyViolation("forbidden environment key present")
    if any(key.startswith("PROPERTY_CATALOG_DEV_WRITE_") for key in os.environ):
        raise SafetyViolation("catalog writer environment is present")
    if any(os.environ.get(k) != v for k, v in FIXED.items()):
        raise SafetyViolation("fixed DEV smoke environment drifted")
    if any(not os.environ.get(k) for k in REQUIRED):
        raise SafetyViolation("required environment value is absent")
    if (
        os.environ["EXPECTED_QUALIFIER_SHA256"] != FROZEN_QUALIFIER_SHA256
        or os.environ["EXPECTED_BASE_COMMIT"] != FROZEN_BASE_COMMIT
    ):
        raise SafetyViolation("frozen source identity pin drifted")
    exact_assembly_pins = {
        "EXPECTED_IMAGE_ID": FROZEN_IMAGE_ID,
        "EXPECTED_SOURCE_MANIFEST_SHA256": FROZEN_SOURCE_MANIFEST_SHA256,
        "KARTIK_EXCLUDED_PROJECT_UUID_SHA256": FROZEN_EXCLUDED_PROJECT_UUID_SHA256,
    }
    if any(
        os.environ[key] != value for key, value in exact_assembly_pins.items()
    ) or any(
        "__PENDING_" in value
        for value in (
            *exact_assembly_pins.values(),
            FROZEN_CATALOG_ACTIVATION_MANIFEST_SHA256,
            FROZEN_CATALOG_ACTIVATION_SHA256,
            FROZEN_VOICE_PROJECT_UUID_SHA256,
            FROZEN_TRACE_PROJECT_UUID_SHA256,
            FROZEN_CANONICAL_TENANT_BINDING_SHA256,
            FROZEN_RUNTIME_OVERLAY_SHA256,
            FROZEN_HARNESS_SHA256,
            FROZEN_BUNDLE_MANIFEST_SHA256,
            FROZEN_DOCKERFILE_SHA256,
            FROZEN_JOB_TEMPLATE_SHA256,
        )
    ):
        raise SafetyViolation("0816h assembly pins are not bound")
    if (
        re.fullmatch(r"sha256:[0-9a-f]{64}", os.environ["EXPECTED_IMAGE_ID"]) is None
        or re.fullmatch(r"[0-9a-f]{64}", os.environ["EXPECTED_SOURCE_MANIFEST_SHA256"])
        is None
        or any(
            re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in (
                FROZEN_CATALOG_ACTIVATION_MANIFEST_SHA256,
                FROZEN_CATALOG_ACTIVATION_SHA256,
                FROZEN_VOICE_PROJECT_UUID_SHA256,
                FROZEN_TRACE_PROJECT_UUID_SHA256,
                FROZEN_EXCLUDED_PROJECT_UUID_SHA256,
                FROZEN_CANONICAL_TENANT_BINDING_SHA256,
                FROZEN_RUNTIME_OVERLAY_SHA256,
                FROZEN_HARNESS_SHA256,
                FROZEN_BUNDLE_MANIFEST_SHA256,
                FROZEN_DOCKERFILE_SHA256,
                FROZEN_JOB_TEMPLATE_SHA256,
            )
        )
    ):
        raise SafetyViolation("0816h assembly pin format drifted")
    if (
        os.environ["CH25_DATABASE"] != "futureagi"
        or os.environ["CH_DATABASE"] != os.environ["CH25_DATABASE"]
        or os.environ["CH_HOST"] != os.environ["CH25_HOST"]
        or os.environ["CH_USERNAME"] != os.environ["CH25_USER"]
        or os.environ["CH_PASSWORD"] != os.environ["CH25_PASSWORD"]
        or os.environ["CH_PORT"] != os.environ["CH25_TCP_PORT"]
    ):
        raise SafetyViolation("source identity mapping drifted")
    catalog_db = os.environ["PROPERTY_CATALOG_CH_DATABASE"]
    if (
        os.environ["PROPERTY_CATALOG_DATABASE"] != catalog_db
        or catalog_db != FROZEN_CATALOG_DATABASE
        or os.environ["PROPERTY_CATALOG_CH_USER"] == os.environ["CH25_USER"]
    ):
        raise SafetyViolation("catalog identity mapping drifted")
    try:
        voice = UUID(os.environ["KARTIK_CANONICAL_VOICE_PROJECT_ID"])
        trace = UUID(os.environ["KARTIK_CANONICAL_TRACE_PROJECT_ID"])
    except ValueError as exc:
        raise SafetyViolation("analogue UUID is invalid") from exc
    forbidden = {UUID(str(x["anchor_project_id"])) for x in q.TARGETS.values()}
    selected = {voice, trace}
    voice_sha256 = FROZEN_VOICE_PROJECT_UUID_SHA256
    trace_sha256 = FROZEN_TRACE_PROJECT_UUID_SHA256
    excluded_sha256 = os.environ["KARTIK_EXCLUDED_PROJECT_UUID_SHA256"]
    if (
        any(
            re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in (voice_sha256, trace_sha256, excluded_sha256)
        )
        or len({voice_sha256, trace_sha256, excluded_sha256}) != 3
        or len(selected) != 2
        or selected & forbidden
        or _sha256(str(voice)) != voice_sha256
        or _sha256(str(trace)) != trace_sha256
    ):
        raise SafetyViolation("analogue target identity is unsafe")
    raw_end = os.environ["KARTIK_SMOKE_END_UTC"]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", raw_end) is None:
        raise SafetyViolation("frozen end is invalid")
    end = datetime.strptime(raw_end, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    now = datetime.now(UTC)
    if end > now + timedelta(minutes=1) or now - end > q.QUALIFIER_END_MAX_AGE:
        raise SafetyViolation("frozen end is outside launch window")
    run_id = os.environ["KARTIK_SMOKE_RUN_ID"]
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", run_id) is None:
        raise SafetyViolation("run id is invalid")
    expected_ip = str(
        ipaddress.IPv4Address(os.environ["KARTIK_SMOKE_SOURCE_AUTH_IPV4"])
    )
    actual = {
        x[4][0] for x in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    }
    if expected_ip not in actual:
        raise SafetyViolation("container IPv4 binding drifted")
    expected_wrapper_sha = os.environ["EXPECTED_KARTIK_SMOKE_0816H_SHA256"]
    if re.fullmatch(r"[0-9a-f]{64}", expected_wrapper_sha) is None:
        raise SafetyViolation("wrapper hash pin is invalid")
    verify_regular_file(Path(__file__), expected_wrapper_sha)
    return {
        "voice": voice,
        "trace": trace,
        "end": end,
        "run_id": run_id,
        "phase": phase,
        "excluded_target": {
            "selected": False,
            "target_selection_count": 0,
            "pg_query_count": 0,
            "catalog_query_count": 0,
            "client_count": 0,
            "callback_count": 0,
            "profile_count": 0,
            "matrix_cell_count": 0,
            "target_profile_handoff_entry_count": 0,
            "raw_identity_handoff_entry_count": 0,
            "exclusion_digest_bound": True,
            "uuid_sha256_pin": excluded_sha256,
        },
        "source_auth_ipv4_sha256": q._digest(expected_ip, 64),
        "environment_key_count": EXPECTED_ENV_KEY_COUNT,
        "environment_key_sha256": EXPECTED_ENV_KEY_SHA256,
        "wrapper_sha256": expected_wrapper_sha,
    }


def startup() -> dict[str, Any]:
    q._qualifier_deadline_monotonic = time.monotonic() + q.QUALIFIER_WALL_SECONDS
    identity = _verify_local_image_source_identity()
    static_guard_self_test()
    q._install_pg_guard()
    q._install_ch_guard()
    logging.disable(logging.CRITICAL)
    # A transitive audio import indexes PATH during Django URL preload. Keep
    # executable discovery disabled while satisfying that import contract.
    os.environ["PATH"] = ""
    import django

    preload, settings = q._bootstrap_reviewed_django_runtime(django.setup)
    route_set = {
        x.strip().upper()
        for x in str(settings.CLICKHOUSE_V2.get("QUERY_TYPES_V2_ONLY") or "").split(",")
        if x.strip()
    }
    if route_set != {"TRACE_LIST", "SPAN_LIST"}:
        raise SafetyViolation("source route set drifted")
    if not q._dispatch_tripwires_active():
        raise SafetyViolation("dispatch tripwires are inactive")
    cursor_cache = _install_attribute_cursor_cache(controlled_phase())
    q._install_request_context_hook()
    if "testserver" not in settings.ALLOWED_HOSTS:
        settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, "testserver"]
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not callable(getattr(AESGCM, "encrypt", None)) or not callable(
        getattr(AESGCM, "decrypt", None)
    ):
        raise SafetyViolation("registry handoff AEAD is unavailable")
    database_defaults = q._verify_database_defaults()
    aliases = database_defaults.get("postgresql_aliases") or []
    return {
        "source_identity": identity,
        "startup_url_preload": preload,
        "database_defaults": {
            "postgresql_alias_count": len(aliases),
            "clickhouse_readonly": database_defaults.get("clickhouse_readonly"),
            "clickhouse_server_enforced": database_defaults.get(
                "clickhouse_server_enforced"
            ),
        },
        "scoped_startup_network_block": True,
        "registry_handoff_aead": "AES-256-GCM",
        "supervised_cursor_cache": cursor_cache,
    }


def num(values: dict[str, str], key: str, integer: bool = False) -> int | float:
    try:
        value = float(values[key])
    except (KeyError, ValueError) as exc:
        raise SafetyViolation(f"invalid setting {key}") from exc
    if not math.isfinite(value) or (integer and not value.is_integer()):
        raise SafetyViolation(f"invalid setting {key}")
    return int(value) if integer else value


def ch_audit(
    label: str,
    *,
    host: str,
    port: str,
    user: str,
    password: str,
    database: str,
    probes: tuple[str, ...],
    source: bool,
) -> dict[str, Any]:
    from clickhouse_driver import Client

    names = (
        "readonly",
        "max_execution_time",
        "max_threads",
        "max_memory_usage",
        "max_bytes_to_read",
        "max_result_rows",
        "max_result_bytes",
        "max_rows_to_read",
        "read_overflow_mode",
        "result_overflow_mode",
        "timeout_overflow_mode",
    )
    sql = "SELECT " + ",".join(f"toString(getSetting('{name}'))" for name in names)
    client = Client(
        host=host,
        port=int(port),
        user=user,
        password=password,
        database=database,
        connect_timeout=2,
        send_receive_timeout=9,
    )
    try:
        rows = client.execute(sql)
        if len(rows) != 1 or len(rows[0]) != len(names):
            raise SafetyViolation(f"{label} cap probe failed")
        values = dict(zip(names, map(str, rows[0]), strict=True))
        readonly, execution = (
            num(values, "readonly", True),
            num(values, "max_execution_time"),
        )
        threads, memory = (
            num(values, "max_threads", True),
            num(values, "max_memory_usage", True),
        )
        read_bytes = num(values, "max_bytes_to_read", True)
        result_rows, result_bytes = (
            num(values, "max_result_rows", True),
            num(values, "max_result_bytes", True),
        )
        max_rows = num(values, "max_rows_to_read", True)
        if (
            readonly != 2
            or not 0 < execution <= CH_TIMEOUT_SECONDS
            or not 0 < threads <= CH_MAX_THREADS
            or not 0 < memory <= CH_MAX_MEMORY_BYTES
            or not 0 < read_bytes <= CH_MAX_BYTES_TO_READ
            or not 0 < result_rows <= CH_MAX_RESULT_ROWS
            or not 0 < result_bytes <= CH_MAX_RESULT_BYTES
            or values["read_overflow_mode"] != "throw"
            or values["result_overflow_mode"] != "throw"
            or values["timeout_overflow_mode"] != "throw"
            or (source and max_rows != 0)
        ):
            raise SafetyViolation(f"{label} safety caps drifted")
        who = client.execute("SELECT currentUser()")
        if len(who) != 1 or len(who[0]) != 1:
            raise SafetyViolation(f"{label} identity probe failed")
        grant_closure = (
            _source_grant_audit(client, who[0][0], os.environ["CH_USERNAME"])
            if source
            else None
        )
        for probe in probes:
            client.execute(probe)
    finally:
        client.disconnect()
    result = {
        "identity_sha256": q._digest(who[0][0], 64),
        "probe_count": len(probes),
        "caps": {
            "readonly": readonly,
            "max_execution_time": execution,
            "max_threads": threads,
            "max_memory_usage": memory,
            "max_bytes_to_read": read_bytes,
            "max_result_rows": result_rows,
            "max_result_bytes": result_bytes,
            "max_rows_to_read": max_rows,
            "read_overflow_mode": values["read_overflow_mode"],
            "result_overflow_mode": values["result_overflow_mode"],
            "timeout_overflow_mode": values["timeout_overflow_mode"],
        },
    }
    if source:
        result["probe_kinds"] = list(SOURCE_PROBE_KINDS)
        result["grant_closure"] = grant_closure
    return result


def database_audit() -> dict[str, Any]:
    catalog_db = os.environ["PROPERTY_CATALOG_CH_DATABASE"]
    return {
        "source": ch_audit(
            "source",
            host=os.environ["CH25_HOST"],
            port=os.environ["CH25_TCP_PORT"],
            user=os.environ["CH25_USER"],
            password=os.environ["CH25_PASSWORD"],
            database=os.environ["CH25_DATABASE"],
            source=True,
            probes=SOURCE_PROBES,
        ),
        "catalog": ch_audit(
            "catalog",
            host=os.environ["PROPERTY_CATALOG_CH_HOST"],
            port=os.environ["PROPERTY_CATALOG_CH_PORT"],
            user=os.environ["PROPERTY_CATALOG_CH_USER"],
            password=os.environ["PROPERTY_CATALOG_CH_PASSWORD"],
            database=catalog_db,
            source=False,
            probes=tuple(
                f"SELECT 1 FROM `{catalog_db}`.`{table}` WHERE 0"
                for table in CATALOG_TABLES
            ),
        ),
    }


def catalog_population_audit(targets) -> dict[str, dict[str, Any]]:
    from tracer.services.clickhouse.v2.property_catalog.connection import (
        PropertyCatalogReadExecutor,
        reset_property_catalog_read_client,
    )
    from tracer.services.clickhouse.v2.property_catalog.reader import (
        PropertyCatalogReader,
        _ReadBudget,
    )

    targets = tuple(targets)
    project_ids = tuple(
        str(project.id) for _label, project, _principal, _expected in targets
    )
    labels = tuple(label for label, _project, _principal, _expected in targets)
    if len(targets) != 2 or len(set(project_ids)) != 2 or len(set(labels)) != 2:
        raise SafetyViolation("analogue project binding drifted")
    catalog_db = os.environ["PROPERTY_CATALOG_CH_DATABASE"]
    sql = f"""
WITH requested_projects AS
(
    SELECT toUUID(%(catalog_project_id)s) AS project_id
), lineage_versioned AS
(
    SELECT *, max(_version) OVER (
        PARTITION BY organization_id, workspace_id,
                     catalog_epoch, catalog_revision, build_token
    ) AS latest_version
    FROM `{catalog_db}`.`property_catalog_activations`
    PREWHERE organization_id = %(catalog_organization_id)s
      AND workspace_id = %(catalog_workspace_id)s
      AND catalog_epoch = %(catalog_epoch)s
      AND catalog_revision >= %(catalog_lineage_anchor_revision)s
      AND catalog_revision <= %(catalog_revision)s
), lineage_states AS
(
    SELECT
        versioned_rows.catalog_epoch,
        versioned_rows.catalog_revision,
        versioned_rows.build_token,
        argMax(versioned_rows.projection_version, versioned_rows._version)
            AS projection_version,
        argMax(versioned_rows.lifecycle_mode, versioned_rows._version)
            AS lifecycle_mode,
        argMax(versioned_rows.lineage_anchor_revision, versioned_rows._version)
            AS lineage_anchor_revision,
        argMax(versioned_rows.status, versioned_rows._version) AS status,
        argMax(versioned_rows.qualified_at, versioned_rows._version) AS qualified_at,
        uniqExactIf(tuple(
            versioned_rows.projection_version,
            versioned_rows.lifecycle_mode,
            versioned_rows.lineage_anchor_revision,
            versioned_rows.status,
            versioned_rows.qualified_at
        ), versioned_rows._version = versioned_rows.latest_version)
            AS latest_state_variants
    FROM lineage_versioned AS versioned_rows
    GROUP BY versioned_rows.catalog_epoch, versioned_rows.catalog_revision,
             versioned_rows.build_token
), active_lineage_candidates AS
(
    SELECT * FROM lineage_states
    WHERE latest_state_variants = 1
      AND status = 'active'
      AND qualified_at IS NOT NULL
), active_lineage AS
(
    SELECT
        candidate.catalog_epoch,
        candidate.catalog_revision,
        any(candidate.build_token) AS build_token,
        any(candidate.projection_version) AS projection_version,
        count() AS active_builds
    FROM active_lineage_candidates AS candidate
    GROUP BY candidate.catalog_epoch, candidate.catalog_revision
    HAVING active_builds = 1
), project_definition_rows AS
(
    SELECT
        rows.visibility_id AS visibility_id,
        rows.binding_id AS binding_id,
        rows.catalog_revision AS catalog_revision,
        rows.source_version AS source_version,
        rows.property_id AS property_id,
        rows.definition_sha256 AS definition_sha256,
        rows.is_deleted AS is_deleted,
        rows.state_sha256 AS state_sha256
    FROM `{catalog_db}`.`property_definition_catalog` AS rows
    INNER JOIN active_lineage AS lineage
        ON rows.catalog_epoch = lineage.catalog_epoch
       AND rows.catalog_revision = lineage.catalog_revision
       AND rows.build_token = lineage.build_token
       AND rows.projection_version = lineage.projection_version
    INNER JOIN requested_projects AS requested
        ON rows.visibility_id = requested.project_id
    WHERE rows.organization_id = %(catalog_organization_id)s
      AND rows.workspace_id = %(catalog_workspace_id)s
      AND rows.catalog_epoch = %(catalog_epoch)s
      AND rows.catalog_revision >= %(catalog_lineage_anchor_revision)s
      AND rows.catalog_revision <= %(catalog_revision)s
      AND rows.visibility_scope = 'project'
), binding_maxima AS
(
    SELECT binding.visibility_id AS project_id, binding.binding_id,
           max(tuple(binding.catalog_revision, binding.source_version))
               AS latest_source_version
    FROM project_definition_rows AS binding
    GROUP BY binding.visibility_id, binding.binding_id
), latest_binding_rows AS
(
    SELECT
        rows.visibility_id AS visibility_id,
        rows.binding_id AS binding_id,
        rows.catalog_revision AS catalog_revision,
        rows.source_version AS source_version,
        rows.property_id AS property_id,
        rows.definition_sha256 AS definition_sha256,
        rows.is_deleted AS is_deleted,
        rows.state_sha256 AS state_sha256
    FROM project_definition_rows AS rows
    INNER JOIN binding_maxima AS maxima
        ON rows.visibility_id = maxima.project_id
       AND rows.binding_id = maxima.binding_id
    WHERE tuple(rows.catalog_revision, rows.source_version)
        = maxima.latest_source_version
), resolved_bindings AS
(
    SELECT
        binding.visibility_id AS project_id,
        binding.binding_id,
        any(binding.property_id) AS property_id,
        any(binding.definition_sha256) AS definition_sha256,
        any(binding.is_deleted) AS is_deleted,
        uniqExact(binding.state_sha256) AS state_variants,
        uniqExact(tuple(
            binding.property_id,
            binding.definition_sha256,
            binding.is_deleted,
            binding.state_sha256
        ))
            AS binding_variants
    FROM latest_binding_rows AS binding
    GROUP BY binding.visibility_id, binding.binding_id
), live_bindings AS
(
    SELECT resolved.* FROM resolved_bindings AS resolved
    WHERE resolved.state_variants = 1
      AND resolved.binding_variants = 1
      AND resolved.is_deleted = 0
), resolved_properties AS
(
    SELECT binding.project_id, binding.property_id,
           uniqExact(binding.definition_sha256) AS definition_variants
    FROM live_bindings AS binding
    GROUP BY binding.project_id, binding.property_id
), property_counts AS
(
    SELECT property.project_id,
           countIf(property.definition_variants = 1) AS live_definition_count,
           countIf(property.definition_variants != 1) AS property_conflict_count
    FROM resolved_properties AS property
    GROUP BY property.project_id
), binding_conflict_counts AS
(
    SELECT binding.project_id,
           countIf(binding.state_variants != 1 OR binding.binding_variants != 1)
               AS binding_conflict_count
    FROM resolved_bindings AS binding
    GROUP BY binding.project_id
), definition_counts AS
(
    SELECT
        requested.project_id AS project_id,
        ifNull(properties.live_definition_count, 0) AS live_definition_count,
        ifNull(properties.property_conflict_count, 0)
            + ifNull(bindings.binding_conflict_count, 0)
            AS definition_conflict_count
    FROM requested_projects AS requested
    LEFT JOIN property_counts AS properties
        ON requested.project_id = properties.project_id
    LEFT JOIN binding_conflict_counts AS bindings
        ON requested.project_id = bindings.project_id
), value_counts AS
(
    SELECT
        requested.project_id AS project_id,
        uniqExact(tuple(
            value_rows.source_kind, value_rows.attribute_key,
            value_rows.attribute_type, value_rows.value_fingerprint
        )) AS live_value_count
    FROM requested_projects AS requested
    LEFT JOIN `{catalog_db}`.`span_attribute_value_catalog` AS value_rows
        ON requested.project_id = value_rows.project_id
    INNER JOIN active_lineage AS lineage
        ON value_rows.catalog_epoch = lineage.catalog_epoch
       AND value_rows.catalog_revision = lineage.catalog_revision
       AND value_rows.build_token = lineage.build_token
    WHERE value_rows.organization_id = %(catalog_organization_id)s
      AND value_rows.workspace_id = %(catalog_workspace_id)s
      AND value_rows.catalog_epoch = %(catalog_epoch)s
      AND value_rows.catalog_revision >= %(catalog_lineage_anchor_revision)s
      AND value_rows.catalog_revision <= %(catalog_revision)s
    GROUP BY requested.project_id
)
SELECT
    toString(requested.project_id) AS project_id,
    definitions.live_definition_count AS live_definition_count,
    definitions.definition_conflict_count AS definition_conflict_count,
    ifNull(values.live_value_count, 0) AS live_value_count,
    (SELECT count() FROM lineage_states WHERE latest_state_variants != 1)
        AS activation_state_conflicts,
    (SELECT count() FROM (
        SELECT catalog_epoch, catalog_revision
        FROM active_lineage_candidates
        GROUP BY catalog_epoch, catalog_revision
        HAVING count() != 1
    )) AS activation_lineage_conflicts,
    (SELECT if(
        count() = 0,
        0,
        uniqExact(lineage.projection_version) != 1
    ) FROM active_lineage AS lineage) AS activation_projection_conflicts,
    (SELECT count() FROM active_lineage_candidates
        WHERE lineage_anchor_revision != %(catalog_lineage_anchor_revision)s
           OR lineage_anchor_revision > catalog_revision
           OR (catalog_revision = lineage_anchor_revision
               AND lifecycle_mode NOT IN ('initial_backfill', 'full_repair'))
           OR (catalog_revision > lineage_anchor_revision
               AND lifecycle_mode != 'incremental'))
        AS activation_anchor_conflicts
FROM requested_projects AS requested
INNER JOIN definition_counts AS definitions
    ON requested.project_id = definitions.project_id
LEFT JOIN value_counts AS values
    ON requested.project_id = values.project_id
ORDER BY project_id
"""

    def count(row: dict[str, Any], key: str) -> int:
        value = row.get(key)
        if type(value) is not int or value < 0:
            raise SafetyViolation("project catalog population count is invalid")
        return value

    evidence: dict[str, dict[str, Any]] = {}
    canonical_tenant = None
    canonical_activation = None
    for label, project, principal, expected_population in targets:
        started = time.monotonic()
        organization_id = str(project.organization_id)
        workspace_id = str(principal.workspace.id)
        project_id = str(project.id)
        activation_executor = PropertyCatalogReadExecutor()
        count_executor = None
        try:
            reader = PropertyCatalogReader(
                activation_executor, catalog_database=catalog_db
            )
            activation = reader._activation(
                scope={
                    "organization_id": organization_id,
                    "workspace_id": workspace_id,
                },
                cursor=None,
                budget=_ReadBudget.start(),
            )
            if (
                activation.source_manifest_sha256
                != FROZEN_CATALOG_ACTIVATION_MANIFEST_SHA256
                or activation.activation_sha256 != FROZEN_CATALOG_ACTIVATION_SHA256
                or activation.catalog_epoch != FROZEN_CATALOG_EPOCH
                or activation.catalog_revision != FROZEN_CATALOG_REVISION
            ):
                raise SafetyViolation("fresh catalog activation pin drifted")
            count_executor = PropertyCatalogReadExecutor()
            result = count_executor.execute(
                sql,
                {
                    "catalog_organization_id": organization_id,
                    "catalog_workspace_id": workspace_id,
                    "catalog_project_id": project_id,
                    "catalog_epoch": activation.catalog_epoch,
                    "catalog_revision": activation.catalog_revision,
                    "catalog_lineage_anchor_revision": activation.lineage_anchor_revision,
                },
                timeout_ms=2_000,
                settings={
                    "max_execution_time": 2,
                    "max_result_rows": 1,
                    "max_result_bytes": 64 * 1024,
                },
            )
            rows = result.data
        finally:
            activation_executor.close()
            if count_executor is not None:
                count_executor.close()
            reset_property_catalog_read_client()
        if len(rows) != 1 or str(rows[0].get("project_id")) != project_id:
            raise SafetyViolation("project catalog population proof is incomplete")
        row = rows[0]
        definitions = count(row, "live_definition_count")
        values = count(row, "live_value_count")
        conflicts = sum(
            count(row, key)
            for key in (
                "definition_conflict_count",
                "activation_state_conflicts",
                "activation_lineage_conflicts",
                "activation_projection_conflicts",
                "activation_anchor_conflicts",
            )
        )
        if conflicts:
            raise SafetyViolation("project catalog population proof conflicted")
        if expected_population is not True:
            raise SafetyViolation("0816h target population contract drifted")
        if definitions < 1 or values < 1:
            raise SafetyViolation("expected project catalog population is absent")
        tenant_identity = (organization_id, workspace_id)
        activation_identity = (
            activation.catalog_epoch,
            activation.catalog_revision,
            activation.build_token,
            activation.projection_version,
            activation.lifecycle_mode,
            activation.lineage_anchor_revision,
            activation.activation_sequence,
            activation.source_manifest_sha256,
            activation.activation_sha256,
        )
        if canonical_tenant is None:
            canonical_tenant = tenant_identity
            canonical_activation = activation_identity
        elif (
            tenant_identity != canonical_tenant
            or activation_identity != canonical_activation
        ):
            raise SafetyViolation("canonical tenant activation binding drifted")
        activation_binding = (
            organization_id,
            workspace_id,
            project_id,
            activation.catalog_epoch,
            activation.catalog_revision,
            activation.lineage_anchor_revision,
            activation.build_token,
            activation.projection_version,
            activation.lifecycle_mode,
            activation.activation_sequence,
            activation.source_manifest_sha256,
            activation.activation_sha256,
            definitions,
            values,
        )
        evidence[label] = {
            "workspace_admitted": True,
            "project_population_expected": expected_population,
            "live_definition_count": definitions,
            "live_value_count": values,
            "active_catalog_epoch": activation.catalog_epoch,
            "active_catalog_revision": activation.catalog_revision,
            "lineage_anchor_revision": activation.lineage_anchor_revision,
            "projection_version": activation.projection_version,
            "activation_sequence": activation.activation_sequence,
            "activation_source_manifest_sha256": activation.source_manifest_sha256,
            "activation_sha256": activation.activation_sha256,
            "activation_binding_sha256": q._digest(activation_binding, 64),
            "elapsed_s": round(time.monotonic() - started, 3),
        }
    return evidence


def target(
    project_id: UUID,
    label: str,
    *,
    expected_surface: str,
):
    from django.conf import settings
    from tracer.models.project import Project

    project = (
        Project.no_workspace_objects.filter(
            id=project_id, trace_type="observe", deleted=False
        )
        .select_related("organization", "workspace")
        .first()
    )
    if project is None or q._surface(project) != expected_surface:
        raise q.PopulationGap(f"{label} {expected_surface} analogue unavailable")
    forbidden = {str(t).lower() for spec in q.TARGETS.values() for t in spec["tokens"]}
    if any(t in q._target_text(project) for t in forbidden):
        raise SafetyViolation("analogue resolved to named release target")
    principal = q._project_principal(project)
    if principal is None:
        raise q.PopulationGap(f"{label} has no authorized principal")
    allowed = {str(x) for x in settings.PROPERTY_CATALOG_DEV_WORKSPACE_ALLOWLIST}
    if (
        project.workspace_id is None
        or project.workspace_id != principal.workspace.id
        or allowed != {str(principal.workspace.id)}
    ):
        raise SafetyViolation(f"{label} workspace admission drifted")
    return project, principal


def _profile_from_property_candidate(
    client: q.DirectDRFClient,
    candidate: dict[str, Any],
    *,
    lane: str,
) -> tuple[str, Any, str]:
    """Bind one already enumerated scalar key through public filter-values."""

    selected_key = candidate.get("key")
    selected_type = candidate.get("type")
    if (
        not isinstance(selected_key, str)
        or not selected_key
        or selected_type not in {"string", "number", "boolean"}
    ):
        raise q.QualificationFailure(
            "property profile candidate was not a scalar custom key"
        )
    query = {
        "metric_name": selected_key,
        "metric_type": "custom_attribute",
        "source": "traces",
        "project_ids": str(client.project.id),
        "page_size": 10,
        "attribute_type": selected_type,
    }
    response = client.call(
        "filter_values",
        lane=f"{lane}.values.p1",
        query=query,
    )
    payload = q._require_status(response, f"{lane}.values.p1")
    values = payload.get("values")
    if not isinstance(values, list):
        raise q.QualificationFailure("property value response omitted values")
    for option in values:
        if not isinstance(option, dict):
            continue
        value = option.get("value")
        value_type = str(option.get("type") or selected_type)
        if value_type in {"string", "number", "boolean"} and value is not None:
            return selected_key, value, value_type
    raise q.PopulationGap("property profile candidate had no scalar filter value")


def key_protocol(client: q.DirectDRFClient, lane: str):
    # The frozen helper follows both independently timestamped cursor chains
    # and compares their page-two semantics, never their signed cursor bytes.
    proof = q._qualify_key_read_more(client, lane=lane)
    candidates, _payload = q._property_key_page(
        client, lane=f"{lane}.profile_candidates", page_size=25
    )
    for row in candidates[:10]:
        key, typ = row.get("key"), row.get("type")
        if (
            not isinstance(key, str)
            or not key
            or typ not in {"string", "number", "boolean"}
        ):
            continue
        try:
            selected_key, value, selected_type = _profile_from_property_candidate(
                client,
                row,
                lane=f"{lane}.profile.{q._digest(key)}",
            )
        except q.PopulationGap:
            continue
        return (
            selected_key,
            value,
            selected_type,
            {
                **{k: v for k, v in proof.items() if k != "qualified"},
                "candidate_page_proven": True,
                "filter_value_binding_proven": True,
                "key_sha256": q._digest(selected_key, 64),
                "value_sha256": q._digest(value, 64),
                "value_type": selected_type,
            },
        )
    raise q.PopulationGap(f"{lane} no bounded scalar custom profile")


def metrics_protocol(client: q.DirectDRFClient, lane: str, custom_key: str):
    # The patched helper follows both page-one cursors and compares semantic
    # continuation payloads rather than the independently timestamped tokens.
    baseline = getattr(q, "METRIC_CATALOG_QUALIFICATION_MAX_PAGES", None)
    if baseline != METRIC_CATALOG_FROZEN_PAGE_FUSE:
        raise SafetyViolation("metrics catalog page fuse baseline drifted")
    q.METRIC_CATALOG_QUALIFICATION_MAX_PAGES = METRIC_CATALOG_DEV_PAGE_FUSE
    try:
        evidence = q._qualify_metrics_catalog(
            client,
            lane=lane,
            expected_property_ids=(
                "system_attribute:traces:model",
                f"custom_attribute:{custom_key}",
            ),
        )
        if not isinstance(evidence, dict):
            raise SafetyViolation("metrics catalog helper returned invalid evidence")
        return {
            **evidence,
            "page_fuse_frozen_baseline": METRIC_CATALOG_FROZEN_PAGE_FUSE,
            "page_fuse_effective": METRIC_CATALOG_DEV_PAGE_FUSE,
            "page_fuse_restored_on_return": True,
        }
    finally:
        observed = getattr(q, "METRIC_CATALOG_QUALIFICATION_MAX_PAGES", None)
        q.METRIC_CATALOG_QUALIFICATION_MAX_PAGES = baseline
        if (
            q.METRIC_CATALOG_QUALIFICATION_MAX_PAGES != baseline
            or observed != METRIC_CATALOG_DEV_PAGE_FUSE
        ):
            raise SafetyViolation("metrics catalog page fuse lifecycle drifted")


def metrics_output(evidence: dict[str, Any] | None) -> dict[str, Any] | None:
    if evidence is None:
        return None
    kinds = evidence.get("property_kinds")
    if not isinstance(kinds, list) or any(
        not isinstance(value, str) for value in kinds
    ):
        raise SafetyViolation("metrics evidence property kinds are invalid")
    return {
        key: evidence.get(key)
        for key in (
            "qualified",
            "activation_fingerprint_digest",
            "catalog_epoch",
            "catalog_revision",
            "page_count",
            "metric_count",
            "continuation_exercised",
            "page_one_repeat_stable",
            "terminal_page",
            "terminal_has_more",
            "page_fuse_frozen_baseline",
            "page_fuse_effective",
            "page_fuse_restored_on_return",
            "search_activation_fingerprint_digest",
            "dataset_column_definition_proven",
            "dataset_representative_binding_sha256",
            "selected_property_id_digest",
            "search_proven",
        )
    } | {
        "property_kind_count": len(kinds),
        "property_kinds_sha256": q._digest(kinds, 64),
        "selected_property_kind_sha256": q._digest(
            str(evidence.get("selected_property_kind") or ""), 64
        ),
    }


def _with_model_value_lookback(operation):
    """Run bounded Model qualification helpers under a wider DEV lookback."""

    from django.conf import settings
    from tracer.views.dashboard import DashboardViewSet

    setting_name = "FILTER_VALUES_DEFAULT_LOOKBACK_DAYS"
    frozen_default = getattr(DashboardViewSet, setting_name, None)
    setting_was_present = hasattr(settings, setting_name)
    baseline = getattr(settings, setting_name, frozen_default)
    if (
        type(frozen_default) is not int
        or frozen_default != MODEL_VALUES_FROZEN_LOOKBACK_DAYS
        or type(baseline) is not int
        or baseline != MODEL_VALUES_FROZEN_LOOKBACK_DAYS
    ):
        raise SafetyViolation("Model value lookback baseline drifted")
    try:
        setattr(settings, setting_name, MODEL_VALUES_DEV_LOOKBACK_DAYS)
    except BaseException as exc:
        raise SafetyViolation("Model value lookback override failed") from exc
    try:
        if getattr(settings, setting_name, None) != MODEL_VALUES_DEV_LOOKBACK_DAYS:
            raise SafetyViolation("Model value lookback override drifted")
        value = operation()
        return value, {
            "lookback_frozen_baseline_days": MODEL_VALUES_FROZEN_LOOKBACK_DAYS,
            "lookback_effective_days": MODEL_VALUES_DEV_LOOKBACK_DAYS,
            "lookback_restored_on_return": True,
        }
    finally:
        observed = getattr(settings, setting_name, None)
        restore_error = None
        try:
            if setting_was_present:
                setattr(settings, setting_name, baseline)
            elif hasattr(settings, setting_name):
                delattr(settings, setting_name)
        except BaseException as exc:
            restore_error = exc
        restored_present = hasattr(settings, setting_name)
        restored = getattr(settings, setting_name, frozen_default)
        if (
            restore_error is not None
            or observed != MODEL_VALUES_DEV_LOOKBACK_DAYS
            or restored_present != setting_was_present
            or restored != baseline
        ):
            raise SafetyViolation("Model value lookback lifecycle drifted") from (
                restore_error
            )


def required(scope: str, operation):
    try:
        value = operation()
        if value is None:
            raise SafetyViolation("required operation returned no evidence")
        return value
    except q.PopulationGap as exc:
        gap(scope, "POPULATION_GAP", True, exc)
        return None


def run_matrix(
    label: str,
    client: q.DirectDRFClient,
    *,
    kinds: tuple[str, ...],
    profiles,
    end: datetime,
):
    for window, duration in q.WINDOWS:
        for kind in kinds:
            for profile, leaves in profiles:
                lane, started = (
                    f"kartik.{label}.{window}.{kind}.{profile}",
                    time.monotonic(),
                )
                try:
                    evidence = q._qualify_list_protocol(
                        client,
                        kind=kind,
                        filters=[q._time_filter(end - duration, end), *leaves],
                        lane=lane,
                    )
                    cells.append(
                        {
                            "target": label,
                            "window": window,
                            "kind": kind,
                            "profile": profile,
                            "passed": True,
                            "elapsed_s": round(time.monotonic() - started, 3),
                            "positive": bool(evidence.get("positive")),
                            "p1_rows": evidence.get("p1_rows"),
                            "p2_rows": evidence.get("p2_rows"),
                            "continuation_exercised": bool(
                                evidence.get("continuation_exercised")
                            ),
                            "semantic_filter_sha256": evidence.get(
                                "semantic_filter_sha256"
                            ),
                            "row_identity_digests": evidence.get(
                                "row_identity_digests"
                            ),
                            "semantic_row_digests": evidence.get(
                                "semantic_row_digests"
                            ),
                        }
                    )
                except SafetyViolation:
                    raise
                except q.PopulationGap as exc:
                    gap(lane, "POPULATION_GAP", True, exc)
                    cells.append(
                        {
                            "target": label,
                            "window": window,
                            "kind": kind,
                            "profile": profile,
                            "passed": False,
                            "elapsed_s": round(time.monotonic() - started, 3),
                            "positive": False,
                            "p1_rows": None,
                            "p2_rows": None,
                            "continuation_exercised": False,
                        }
                    )
                except Exception as exc:
                    fail(lane, exc)
                    raise


def assess(shapes: dict[str, dict[str, tuple[str, ...]]]) -> None:
    windows = tuple(name for name, _duration in q.WINDOWS)
    expected = {
        (target_label, window, kind, profile)
        for target_label, shape in shapes.items()
        for window in windows
        for kind in shape["kinds"]
        for profile in shape["profiles"]
    }
    actual = {
        (item["target"], item["window"], item["kind"], item["profile"])
        for item in cells
    }
    if (
        windows != EXPECTED_WINDOWS
        or len(expected) != 108
        or len(actual) != len(cells)
        or actual != expected
    ):
        raise SafetyViolation("matrix cell identity contract drifted")
    for target_label, shape in shapes.items():
        for kind in shape["kinds"]:
            for profile in shape["profiles"]:
                candidates = [
                    x
                    for x in cells
                    if x["target"] == target_label
                    and x["kind"] == kind
                    and x["profile"] == profile
                    and x["window"] in LONG_WINDOWS
                ]
                scope = f"{target_label}.{kind}.{profile}"
                if not any(x["passed"] and x["positive"] for x in candidates):
                    gap(scope, "NO_POSITIVE_LONG_WINDOW_WITNESS", True)
                if not any(
                    x["passed"] and x["continuation_exercised"] for x in candidates
                ):
                    gap(scope, "NO_LONG_WINDOW_CONTINUATION_WITNESS", True)


def timing_summary():
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in q._request_records:
        grouped[str(row["endpoint"])].append(row)
    output = {}
    for endpoint, rows in sorted(grouped.items()):
        elapsed = sorted(float(x["elapsed_s"]) for x in rows)

        def pct(percentile):
            return round(elapsed[max(0, math.ceil(len(elapsed) * percentile) - 1)], 3)

        output[endpoint] = {
            "callbacks": len(rows),
            "statuses": dict(
                sorted(Counter(str(x.get("status")) for x in rows).items())
            ),
            "p50_s": pct(0.5),
            "p95_s": pct(0.95),
            "max_s": round(max(elapsed), 3),
            "under_9_8s": all(x.get("within_9_8s_harness_wall") is True for x in rows),
            "postgresql_selects": sum(int(x.get("pg_selects") or 0) for x in rows),
            "clickhouse_reads": sum(int(x.get("ch_reads") or 0) for x in rows),
        }
    slow = [
        {
            k: row.get(k)
            for k in (
                "endpoint",
                "lane",
                "status",
                "elapsed_s",
                "pg_selects",
                "ch_reads",
                "rendered_sha256",
            )
        }
        for row in sorted(
            q._request_records, key=lambda x: float(x["elapsed_s"]), reverse=True
        )[:20]
    ]
    return output, slow


def _profile_value(value: Any, value_type: str) -> Any:
    if value_type == "string" and isinstance(value, str) and len(value) <= 4096:
        return value
    if (
        value_type == "number"
        and type(value) in {int, float}
        and math.isfinite(float(value))
    ):
        return value
    if value_type == "boolean" and type(value) is bool:
        return value
    raise SafetyViolation("registry profile scalar is invalid")


def _profile(key: Any, value: Any, value_type: Any) -> dict[str, Any]:
    if (
        not isinstance(key, str)
        or not key
        or len(key.encode("utf-8")) > 1024
        or value_type not in {"string", "number", "boolean"}
    ):
        raise SafetyViolation("registry profile identity is invalid")
    return {
        "key": key,
        "value": _profile_value(value, value_type),
        "value_type": value_type,
    }


def _handoff_expected(context: dict[str, Any]) -> dict[str, Any]:
    contract = context["contract"]
    return {
        "run_id": contract["run_id"],
        "frozen_end": contract["end"].isoformat(),
        "base_commit": os.environ["EXPECTED_BASE_COMMIT"],
        "image_id": os.environ["EXPECTED_IMAGE_ID"],
        "local_image_tag": FROZEN_LOCAL_IMAGE_TAG,
        "source_manifest_sha256": os.environ["EXPECTED_SOURCE_MANIFEST_SHA256"],
        "runtime_overlay_sha256": FROZEN_RUNTIME_OVERLAY_SHA256,
        "harness_sha256": FROZEN_HARNESS_SHA256,
        "bundle_manifest_sha256": FROZEN_BUNDLE_MANIFEST_SHA256,
        "dockerfile_sha256": FROZEN_DOCKERFILE_SHA256,
        "job_template_sha256": FROZEN_JOB_TEMPLATE_SHA256,
        "catalog_activation_source_manifest_sha256": FROZEN_CATALOG_ACTIVATION_MANIFEST_SHA256,
        "catalog_activation_sha256": FROZEN_CATALOG_ACTIVATION_SHA256,
        "qualifier_sha256": os.environ["EXPECTED_QUALIFIER_SHA256"],
        "wrapper_sha256": os.environ["EXPECTED_KARTIK_SMOKE_0816H_SHA256"],
        "catalog_database": FROZEN_CATALOG_DATABASE,
        "catalog_epoch": FROZEN_CATALOG_EPOCH,
        "catalog_revision": FROZEN_CATALOG_REVISION,
        "excluded_project_uuid_sha256": os.environ[
            "KARTIK_EXCLUDED_PROJECT_UUID_SHA256"
        ],
        "canonical_tenant_binding_sha256": FROZEN_CANONICAL_TENANT_BINDING_SHA256,
    }


def _handoff_wire(payload: dict[str, Any]) -> bytes:
    wire = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    if not 1 <= len(wire) <= 64 * 1024:
        raise SafetyViolation("registry handoff size is invalid")
    return wire


def _handoff_public(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "fi-kartik-registry-handoff/0816h/v1",
        "registry_ok": True,
        "pins": _handoff_expected(context),
        "project_digests": {
            "canonical_voice": _sha256(str(context["voice_project"].id)),
            "canonical_trace": _sha256(str(context["trace_project"].id)),
        },
        "catalog_population_bindings": {
            label: value["activation_binding_sha256"]
            for label, value in context["catalog_population"].items()
        },
    }


def _handoff_key(aad: bytes) -> bytes:
    secret = os.environ["SECRET_KEY"].encode("utf-8")
    if not 32 <= len(secret) <= 4096:
        raise SafetyViolation("registry handoff seal key is invalid")
    return hmac.new(
        secret,
        b"fi-kartik-smoke-0816h-registry-handoff\0" + hashlib.sha256(aad).digest(),
        hashlib.sha256,
    ).digest()


def seal_handoff(context: dict[str, Any], profiles: dict[str, Any]) -> dict[str, Any]:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    public = _handoff_public(context)
    aad = _handoff_wire(public)
    plaintext = _handoff_wire(profiles)
    nonce = os.urandom(12)
    ciphertext = AESGCM(_handoff_key(aad)).encrypt(nonce, plaintext, aad)
    return {
        **public,
        "sealed_profiles": {
            "algorithm": "AES-256-GCM",
            "nonce_b64": base64.b64encode(nonce).decode("ascii"),
            "ciphertext_b64": base64.b64encode(ciphertext).decode("ascii"),
        },
    }


def _handoff_stat(path: str) -> os.stat_result:
    info = os.lstat(path)
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or not 1 <= info.st_size <= 64 * 1024
    ):
        raise SafetyViolation("registry handoff file contract drifted")
    return info


def write_handoff(payload: dict[str, Any]) -> str:
    parent = Path(REGISTRY_HANDOFF_PATH).parent
    parent_info = os.lstat(parent)
    if (
        not stat.S_ISDIR(parent_info.st_mode)
        or stat.S_ISLNK(parent_info.st_mode)
        or stat.S_IMODE(parent_info.st_mode) != 0o700
        or parent_info.st_uid != os.geteuid()
        or parent.resolve() != parent
        or os.path.lexists(REGISTRY_HANDOFF_PATH)
    ):
        raise SafetyViolation("registry handoff path is unsafe")
    wire = _handoff_wire(payload)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(REGISTRY_HANDOFF_PATH, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(wire):
            offset += os.write(descriptor, wire[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    info = _handoff_stat(REGISTRY_HANDOFF_PATH)
    if info.st_size != len(wire):
        raise SafetyViolation("registry handoff write was incomplete")
    return _sha256(wire)


def read_handoff(context: dict[str, Any]) -> tuple[dict[str, Any], str]:
    before = _handoff_stat(REGISTRY_HANDOFF_PATH)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(REGISTRY_HANDOFF_PATH, flags)
    try:
        chunks, remaining = [], 64 * 1024 + 1
        while remaining:
            chunk = os.read(descriptor, min(8192, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        after_fd = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    after = _handoff_stat(REGISTRY_HANDOFF_PATH)
    if (
        remaining == 0
        or (before.st_dev, before.st_ino, before.st_size)
        != (after_fd.st_dev, after_fd.st_ino, after_fd.st_size)
        or (before.st_dev, before.st_ino, before.st_size)
        != (after.st_dev, after.st_ino, after.st_size)
    ):
        raise SafetyViolation("registry handoff changed while reading")
    wire = b"".join(chunks)
    try:
        payload = json.loads(
            wire.decode("ascii"),
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("non-finite handoff value")
            ),
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise SafetyViolation("registry handoff encoding is invalid") from exc
    public = _handoff_public(context)
    if (
        not isinstance(payload, dict)
        or set(payload) != {*public, "sealed_profiles"}
        or any(payload.get(key) != value for key, value in public.items())
    ):
        raise SafetyViolation("registry handoff shape is invalid")
    sealed = payload["sealed_profiles"]
    if (
        not isinstance(sealed, dict)
        or set(sealed) != {"algorithm", "nonce_b64", "ciphertext_b64"}
        or sealed.get("algorithm") != "AES-256-GCM"
    ):
        raise SafetyViolation("registry handoff seal shape is invalid")
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        nonce = base64.b64decode(sealed["nonce_b64"], validate=True)
        ciphertext = base64.b64decode(sealed["ciphertext_b64"], validate=True)
        if len(nonce) != 12 or not 17 <= len(ciphertext) <= 48 * 1024:
            raise ValueError("sealed handoff length")
        aad = _handoff_wire(public)
        plaintext = AESGCM(_handoff_key(aad)).decrypt(nonce, ciphertext, aad)
        profiles = json.loads(
            plaintext.decode("ascii"),
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("non-finite handoff value")
            ),
        )
    except (
        binascii.Error,
        InvalidTag,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise SafetyViolation("registry handoff seal is invalid") from exc
    if not isinstance(profiles, dict) or set(profiles) != {
        "voice_custom",
        "voice_system",
        "trace_custom",
        "trace_system",
    }:
        raise SafetyViolation("registry handoff profiles are invalid")
    voice = profiles["voice_custom"]
    trace = profiles["trace_custom"]
    voice_system = profiles["voice_system"]
    trace_system = profiles["trace_system"]
    if (
        not isinstance(voice, dict)
        or set(voice) != {"key", "value", "value_type"}
        or not isinstance(trace, dict)
        or set(trace) != {"key", "value", "value_type"}
        or not isinstance(voice_system, dict)
        or set(voice_system) != {"value", "value_type"}
        or not isinstance(trace_system, dict)
        or set(trace_system) != {"value", "value_type"}
    ):
        raise SafetyViolation("registry handoff profile shape is invalid")
    checked = {
        "voice_custom": _profile(voice["key"], voice["value"], voice["value_type"]),
        "trace_custom": _profile(trace["key"], trace["value"], trace["value_type"]),
    }
    for name, system in (
        ("voice_system", voice_system),
        ("trace_system", trace_system),
    ):
        if (
            system["value_type"] != "string"
            or not isinstance(system["value"], str)
            or not system["value"]
            or len(system["value"].encode("utf-8")) > 4096
        ):
            raise SafetyViolation("registry Model profile is invalid")
        checked[name] = dict(system)
    latest = _handoff_stat(REGISTRY_HANDOFF_PATH)
    if (before.st_dev, before.st_ino, before.st_size) != (
        latest.st_dev,
        latest.st_ino,
        latest.st_size,
    ):
        raise SafetyViolation("registry handoff changed before destruction")
    os.unlink(REGISTRY_HANDOFF_PATH)
    if os.path.lexists(REGISTRY_HANDOFF_PATH):
        raise SafetyViolation("registry handoff destruction failed")
    return checked, _sha256(wire)


def common_gates() -> dict[str, Any]:
    contract, boot = environment(), startup()
    audit = database_audit()
    loaded = (
        required(
            "canonical_voice.target",
            lambda: target(
                contract["voice"], "canonical_voice", expected_surface="voice"
            ),
        ),
        required(
            "canonical_trace.target",
            lambda: target(
                contract["trace"], "canonical_trace", expected_surface="trace"
            ),
        ),
    )
    if None in loaded:
        raise q.PopulationGap("canonical analogue pair did not resolve")
    (voice_project, voice_principal), (trace_project, trace_principal) = loaded
    if (
        voice_project.organization_id != trace_project.organization_id
        or voice_principal.workspace.id != trace_principal.workspace.id
    ):
        raise SafetyViolation("canonical target tenant binding drifted")
    if (
        _tenant_binding_sha256(
            voice_project.organization_id, voice_principal.workspace.id
        )
        != FROZEN_CANONICAL_TENANT_BINDING_SHA256
    ):
        raise SafetyViolation("canonical tenant digest pin drifted")
    catalog_population = catalog_population_audit(
        (
            ("canonical_voice", voice_project, voice_principal, True),
            ("canonical_trace", trace_project, trace_principal, True),
        )
    )
    return {
        "contract": contract,
        "boot": boot,
        "database_audit": audit,
        "voice_project": voice_project,
        "voice_principal": voice_principal,
        "trace_project": trace_project,
        "trace_principal": trace_principal,
        "voice_client": q.DirectDRFClient(voice_project, voice_principal),
        "trace_client": q.DirectDRFClient(trace_project, trace_principal),
        "catalog_population": catalog_population,
    }


def guard_and_timings() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    blocked = [
        name
        for name in (
            "pg_blocked",
            "ch_blocked",
            "redis_blocked",
            "celery_blocked",
            "temporal_blocked",
            "scheduler_blocked",
            "external_cache_blocked",
        )
        if q._snapshot_counts()[name]
    ]
    if blocked:
        raise SafetyViolation("mutation tripwire activated")
    return timing_summary()


def common_evidence(context: dict[str, Any]) -> dict[str, Any]:
    contract = context["contract"]
    return {
        "evidence_label": "0816h",
        "environment": "DEV",
        "release_qualified": False,
        "release_qualification_attempted": False,
        "named_target_matrix_executed": False,
        "production_touched": False,
        "run_id": contract["run_id"],
        "frozen_end": contract["end"].isoformat(),
        "source_auth_ipv4_sha256": contract["source_auth_ipv4_sha256"],
        "canonical_tenant_binding_sha256": FROZEN_CANONICAL_TENANT_BINDING_SHA256,
        **context["boot"],
        "database_identity_audit": context["database_audit"],
        "targets": {
            "canonical_voice": {
                "project_id_sha256": _sha256(str(context["voice_project"].id)),
                "workspace_catalog_admitted": True,
                "surface": "voice",
                "project_catalog_population": context["catalog_population"][
                    "canonical_voice"
                ],
            },
            "canonical_trace": {
                "project_id_sha256": _sha256(str(context["trace_project"].id)),
                "workspace_catalog_admitted": True,
                "surface": "trace",
                "project_catalog_population": context["catalog_population"][
                    "canonical_trace"
                ],
            },
        },
        "excluded_target": {
            **contract["excluded_target"],
            "registry_operation_count": 0,
            "population_gap_count": 0,
        },
    }


def query_evidence(timings: dict[str, Any], helpers: list[str]) -> dict[str, Any]:
    return {
        "public_callback_routes": sorted(timings),
        "postgresql": ["SELECT", "read-only WITH"],
        "clickhouse": ["SELECT", "read-only WITH"],
        "source_probe_count": len(SOURCE_PROBES),
        "source_probe_kinds": list(SOURCE_PROBE_KINDS),
        "source_grant_inventory": _source_grant_inventory(),
        "source_grant_inventory_sha256": _source_grant_inventory_sha256(),
        "clickhouse_identity_function": "currentUser",
        "catalog_probe_tables": list(CATALOG_TABLES),
        "catalog_population_tables": [
            "property_catalog_activations",
            "property_definition_catalog",
            "span_attribute_value_catalog",
        ],
        "semantic_dual_chain_helpers": helpers,
    }


def registry_phase(context: dict[str, Any]) -> dict[str, Any]:
    voice_client, trace_client = context["voice_client"], context["trace_client"]
    voice_key = required(
        "canonical_voice.property_keys",
        lambda: key_protocol(voice_client, "canonical_voice.property_keys"),
    )
    trace_key = required(
        "canonical_trace.property_keys",
        lambda: key_protocol(trace_client, "canonical_trace.property_keys"),
    )
    if voice_key is None or trace_key is None:
        raise q.PopulationGap("activated custom profiles unavailable")
    voice_name, voice_value, voice_type, voice_key_evidence = voice_key
    trace_name, trace_value, trace_type, trace_key_evidence = trace_key
    for scope, evidence in (
        ("canonical_voice.property_keys", voice_key_evidence),
        ("canonical_trace.property_keys", trace_key_evidence),
    ):
        if not evidence["continuation_exercised"]:
            gap(scope, "TERMINAL_PROPERTY_KEY_PAGE", True)
    voice_metrics = required(
        "canonical_voice.metrics",
        lambda: metrics_protocol(voice_client, "canonical_voice.metrics", voice_name),
    )
    trace_metrics = required(
        "canonical_trace.metrics",
        lambda: metrics_protocol(trace_client, "canonical_trace.metrics", trace_name),
    )
    for scope, evidence in (
        ("canonical_voice.metrics", voice_metrics),
        ("canonical_trace.metrics", trace_metrics),
    ):
        if evidence is not None and not evidence["continuation_exercised"]:
            gap(scope, "TERMINAL_REGISTRY_PAGE", True)
    voice_model = required(
        "canonical_voice.model_profile",
        lambda: q._discover_system_model(
            voice_client, lane="canonical_voice.model_profile"
        ),
    )

    def qualify_model_registry():
        voice_models = required(
            "canonical_voice.model_values",
            lambda: q._qualify_model_values(
                voice_client,
                lane="canonical_voice.model_values",
                page_size=MODEL_VALUES_QUALIFICATION_PAGE_SIZE,
            ),
        )
        trace_models = required(
            "canonical_trace.model_values",
            lambda: q._qualify_model_values(
                trace_client,
                lane="canonical_trace.model_values",
                page_size=MODEL_VALUES_QUALIFICATION_PAGE_SIZE,
            ),
        )
        trace_model = required(
            "canonical_trace.model_profile",
            lambda: q._discover_system_model(
                trace_client, lane="canonical_trace.model_profile"
            ),
        )
        return voice_models, trace_models, trace_model

    (
        (voice_models, trace_models, trace_model),
        model_lookback_evidence,
    ) = _with_model_value_lookback(qualify_model_registry)
    if voice_models is not None:
        voice_models = {**voice_models, **model_lookback_evidence}
    if trace_models is not None:
        trace_models = {**trace_models, **model_lookback_evidence}
    for scope, evidence in (
        ("canonical_voice.model_values", voice_models),
        ("canonical_trace.model_values", trace_models),
    ):
        if evidence is not None and (
            type(evidence.get("page_size")) is not int
            or evidence.get("page_size") != MODEL_VALUES_QUALIFICATION_PAGE_SIZE
            or not evidence.get("continuation_exercised")
        ):
            gap(scope, "TERMINAL_MODEL_PAGE", True)
    if voice_model is None or trace_model is None:
        raise q.PopulationGap("activated Model profiles unavailable")
    q._system_model_filter(*voice_model)
    q._system_model_filter(*trace_model)
    timings, slowest = guard_and_timings()
    if set(timings) != {"property_keys", "filter_values", "metrics"}:
        raise SafetyViolation("registry phase escaped registry routes")
    required_gaps = [item for item in gaps if item["required"]]
    functional = not failures
    coverage = functional and not required_gaps
    handoff_sha256 = None
    if coverage:
        handoff = seal_handoff(
            context,
            {
                "trace_custom": _profile(trace_name, trace_value, trace_type),
                "trace_system": {"value": trace_model[0], "value_type": trace_model[1]},
                "voice_custom": _profile(voice_name, voice_value, voice_type),
                "voice_system": {"value": voice_model[0], "value_type": voice_model[1]},
            },
        )
        handoff_sha256 = write_handoff(handoff)
    targets = common_evidence(context)["targets"]
    targets["canonical_voice"].update(
        {
            "property_keys": voice_key_evidence,
            "metrics": metrics_output(voice_metrics),
            "model_values": voice_models,
        }
    )
    targets["canonical_trace"].update(
        {
            "property_keys": trace_key_evidence,
            "metrics": metrics_output(trace_metrics),
            "model_values": trace_models,
        }
    )
    return {
        "classification": "DEV_KARTIK_ANALOGUE_REGISTRY_SMOKE_0816H",
        "phase": "registry",
        **common_evidence(context),
        "targets": targets,
        "functional_smoke_passed": functional,
        "coverage_complete": coverage,
        "registry": {
            "executed": True,
            "passed": coverage,
            "handoff_created": handoff_sha256 is not None,
            "handoff_sha256": handoff_sha256,
        },
        "matrix": {"executed": False, "expected_cell_count": 108},
        "required_population_gap_count": len(required_gaps),
        "timings_by_route": timings,
        "slowest_requests": slowest,
        "query_kinds": query_evidence(
            timings,
            [
                "_qualify_key_read_more",
                "_qualify_metrics_catalog",
                "_qualify_model_values",
            ],
        ),
        "counts": q._snapshot_counts(),
        "request_fuse": q.MAX_REQUESTS,
        "clickhouse_read_fuse": q.MAX_CH_READS,
        "qualifier_wall_seconds": q.QUALIFIER_WALL_SECONDS,
        "coverage_exit_code": 0 if coverage else 1,
    }


def matrix_phase(context: dict[str, Any]) -> dict[str, Any]:
    profiles, handoff_sha256 = read_handoff(context)
    voice = profiles["voice_custom"]
    trace = profiles["trace_custom"]
    voice_system_profile = profiles["voice_system"]
    trace_system_profile = profiles["trace_system"]
    voice_custom = q._custom_filter(voice["key"], voice["value"], voice["value_type"])
    trace_custom = q._custom_filter(trace["key"], trace["value"], trace["value_type"])
    voice_system = q._system_model_filter(
        voice_system_profile["value"], voice_system_profile["value_type"]
    )
    trace_system = q._system_model_filter(
        trace_system_profile["value"], trace_system_profile["value_type"]
    )
    voice_profiles = (
        ("default", []),
        ("custom", [voice_custom]),
        ("system", [voice_system]),
        ("combined", [voice_system, voice_custom]),
    )
    trace_profiles = (
        ("default", []),
        ("custom", [trace_custom]),
        ("system", [trace_system]),
        ("combined", [trace_system, trace_custom]),
    )
    run_matrix(
        "canonical_voice",
        context["voice_client"],
        kinds=("voice",),
        profiles=voice_profiles,
        end=context["contract"]["end"],
    )
    run_matrix(
        "canonical_trace",
        context["trace_client"],
        kinds=("trace", "span"),
        profiles=trace_profiles,
        end=context["contract"]["end"],
    )
    shapes = {
        "canonical_voice": {
            "kinds": ("voice",),
            "profiles": ("default", "custom", "system", "combined"),
        },
        "canonical_trace": {
            "kinds": ("trace", "span"),
            "profiles": ("default", "custom", "system", "combined"),
        },
    }
    assess(shapes)
    timings, slowest = guard_and_timings()
    if set(timings) != {"trace_list", "span_list", "voice_list"}:
        raise SafetyViolation("matrix phase escaped list routes")
    required_gaps = [item for item in gaps if item["required"]]
    functional = (
        not failures and len(cells) == 108 and all(item["passed"] for item in cells)
    )
    coverage = functional and not required_gaps
    return {
        "classification": "DEV_KARTIK_ANALOGUE_MATRIX_SMOKE_0816H",
        "phase": "matrix",
        **common_evidence(context),
        "analogue_matrix_executed": True,
        "functional_smoke_passed": functional,
        "coverage_complete": coverage,
        "registry": {
            "executed": False,
            "prerequisite_verified": True,
            "handoff_loaded": True,
            "handoff_sha256": handoff_sha256,
        },
        "matrix": {
            "executed": True,
            "windows": [item[0] for item in q.WINDOWS],
            "shapes": {
                key: {
                    "kinds": list(value["kinds"]),
                    "profiles": list(value["profiles"]),
                }
                for key, value in shapes.items()
            },
            "expected_cell_count": 108,
            "executed_cell_count": len(cells),
            "passed_cell_count": sum(item["passed"] for item in cells),
            "positive_cell_count": sum(item["positive"] for item in cells),
            "continuation_cell_count": sum(
                item["continuation_exercised"] for item in cells
            ),
            "cells": cells,
        },
        "required_population_gap_count": len(required_gaps),
        "timings_by_route": timings,
        "slowest_requests": slowest,
        "query_kinds": query_evidence(timings, ["_qualify_list_protocol"]),
        "counts": q._snapshot_counts(),
        "request_fuse": q.MAX_REQUESTS,
        "clickhouse_read_fuse": q.MAX_CH_READS,
        "qualifier_wall_seconds": q.QUALIFIER_WALL_SECONDS,
        "coverage_exit_code": 0 if coverage else 1,
    }


def run() -> dict[str, Any]:
    context = common_gates()
    if context["contract"]["phase"] == "registry":
        return registry_phase(context)
    if context["contract"]["phase"] == "matrix":
        return matrix_phase(context)
    raise SafetyViolation("wrapper phase dispatch drifted")


def controlled_phase() -> str:
    if (
        len(sys.argv) == 3
        and sys.argv[0] == EXPECTED_RUNTIME_WRAPPER_PATH
        and sys.argv[1] == "--phase"
        and sys.argv[2] in PHASES
    ):
        return sys.argv[2]
    return "invalid"


def main() -> int:
    started, payload, error, code = time.monotonic(), {}, None, 1
    cleanup_error = None
    try:
        _load_runtime()
        payload = run()
        code = int(payload["coverage_exit_code"])
    except BaseException as exc:
        safety_type = SafetyViolation if isinstance(SafetyViolation, type) else ()
        population_type = getattr(q, "PopulationGap", ()) if q is not None else ()
        if safety_type and isinstance(exc, safety_type):
            reason_code, code = "SAFETY_VIOLATION", 2
        elif population_type and isinstance(exc, population_type):
            reason_code, code = "POPULATION_GAP", 1
        else:
            reason_code, code = "UNHANDLED_EXCEPTION", 1
        error = {
            "lane": "main",
            "reason_code": reason_code,
            "exception_type": type(exc).__name__,
            "traceback_sha256": _traceback_sha256(),
        }
    finally:
        try:
            from tfc.middleware.workspace_context import clear_workspace_context

            clear_workspace_context()
        except Exception:
            pass
        try:
            if q is not None:
                q._active_context.clear()
        except Exception:
            pass
        try:
            _cleanup_attribute_cursor_cache()
        except BaseException as exc:
            cleanup_error = {
                "lane": "cursor_cache_cleanup",
                "reason_code": "SAFETY_VIOLATION",
                "exception_type": type(exc).__name__,
                "traceback_sha256": _traceback_sha256(),
            }
    if cleanup_error is not None:
        error, code = cleanup_error, 2
        payload["functional_smoke_passed"] = False
        payload["coverage_complete"] = False
        payload["coverage_exit_code"] = 2
    output = {
        "schema": SMOKE_SCHEMA,
        "frozen_qualifier_schema": SCHEMA if isinstance(SCHEMA, str) else None,
        **payload,
        "phase": controlled_phase(),
        "invocation": "direct_authenticated_drf_in_process",
        "select_only": True,
        "release_qualified": False,
        "release_qualification_attempted": False,
        "named_target_matrix_executed": False,
        "production_touched": False,
        "elapsed_s": round(time.monotonic() - started, 3),
        "exit_code": code,
        "error": error,
        "route_failures": list(failures),
        "population_gaps": list(gaps),
    }
    try:
        wire = (
            json.dumps(
                output,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("ascii")
            + b"\n"
        )
    except BaseException as exc:
        wire = (
            json.dumps(
                {
                    "schema": SMOKE_SCHEMA,
                    "phase": controlled_phase(),
                    "release_qualified": False,
                    "release_qualification_attempted": False,
                    "named_target_matrix_executed": False,
                    "production_touched": False,
                    "exit_code": 2,
                    "error": {
                        "lane": "output",
                        "reason_code": "OUTPUT_SERIALIZATION_FAILURE",
                        "exception_type": type(exc).__name__,
                        "traceback_sha256": _traceback_sha256(),
                    },
                    "route_failures": list(failures),
                    "population_gaps": list(gaps),
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("ascii")
            + b"\n"
        )
        code = 2
    offset = 0
    while offset < len(wire):
        offset += os.write(_CONTROLLED_OUTPUT_FD, wire[offset:])
    return code


if __name__ == "__main__":
    raise SystemExit(main())
