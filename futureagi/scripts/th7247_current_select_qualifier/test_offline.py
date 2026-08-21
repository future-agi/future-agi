#!/usr/bin/env python3
"""Offline contract tests for the current TH-7247 SELECT-only qualifier."""

from __future__ import annotations

import ast
import inspect
import json
import os
import re
import socket
import sys
import time
import unittest
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest import mock

PACKAGE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PACKAGE_DIR))

import assemble  # noqa: E402
import openapi_inventory  # noqa: E402
import qualify  # noqa: E402
import query_builder_matrix  # noqa: E402
import safety  # noqa: E402


class Response(SimpleNamespace):
    def __init__(self, data: dict, status_code: int = 200):
        super().__init__(data=data, status_code=status_code)


class QueryBuilderMatrixContractTests(unittest.TestCase):
    def test_builder_uses_public_interactive_mode(self):
        captured = {}

        class Builder:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        query_builder_matrix.build_builder(
            Builder,
            project_id="project-1",
            filters=[],
        )

        self.assertNotIn("bounded_internal_scan", captured)
        self.assertEqual(captured["page_size"], query_builder_matrix.PAGE_SIZE)

    def test_session_cursor_enables_its_public_internal_scan_mode(self):
        captured = {}

        class Builder:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        query_builder_matrix.build_builder(
            Builder,
            project_id="project-1",
            filters=[],
            bounded_internal_scan=True,
        )

        self.assertIs(captured["bounded_internal_scan"], True)

    def test_public_chunk_requires_completion_or_safe_checkpoint(self):
        complete = query_builder_matrix.public_chunk_state(
            SimpleNamespace(
                complete=True,
                status="complete",
                error_code=None,
                continuation_slice_start=None,
                continuation_slice_end=None,
                continuation_before_start_time=None,
                continuation_before_id=None,
            )
        )
        checkpointed = query_builder_matrix.public_chunk_state(
            SimpleNamespace(
                complete=False,
                status="degraded",
                error_code="scan_budget_exceeded",
                continuation_slice_start=datetime(2026, 1, 1),
                continuation_slice_end=datetime(2026, 1, 2),
                continuation_before_start_time=datetime(2026, 1, 1, 12),
                continuation_before_id="trace-1",
            )
        )
        unsafe = query_builder_matrix.public_chunk_state(
            SimpleNamespace(
                complete=False,
                status="degraded",
                error_code="scan_budget_exceeded",
                continuation_slice_start=None,
                continuation_slice_end=None,
                continuation_before_start_time=None,
                continuation_before_id=None,
            )
        )

        self.assertTrue(complete["complete"])
        self.assertFalse(complete["continuation_checkpoint"])
        self.assertTrue(checkpointed["complete"])
        self.assertEqual(checkpointed["status"], "complete")
        self.assertIsNone(checkpointed["error_code"])
        self.assertEqual(
            checkpointed["selector_error_code"], "scan_budget_exceeded"
        )
        self.assertIsNotNone(checkpointed["continuation_checkpoint_digest"])
        self.assertFalse(unsafe["complete"])
        self.assertEqual(unsafe["status"], "degraded")
        self.assertEqual(unsafe["error_code"], "scan_budget_exceeded")

    def test_empty_cursor_page_retains_order_and_advances_private_checkpoint(self):
        first_page = SimpleNamespace(
            has_more=False,
            continuation_slice_start=datetime(2026, 1, 1),
            continuation_slice_end=datetime(2026, 1, 2),
            continuation_before_start_time=datetime(2026, 1, 1, 12),
            continuation_before_id="trace-10",
        )
        first_state = query_builder_matrix.next_cursor_state(
            kind="trace",
            rows=[],
            bounded_page=first_page,
            continuation=None,
            has_more=True,
        )
        first_result = query_builder_matrix.PageResult(
            record={}, rows=[], bounded_page=first_page, cursor_state=first_state
        )
        second_page = SimpleNamespace(
            has_more=False,
            continuation_slice_start=datetime(2025, 12, 31),
            continuation_slice_end=datetime(2026, 1, 1),
            continuation_before_start_time=datetime(2025, 12, 31, 12),
            continuation_before_id="trace-20",
        )

        second_state = query_builder_matrix.next_cursor_state(
            kind="trace",
            rows=[],
            bounded_page=second_page,
            continuation=first_result,
            has_more=True,
        )

        self.assertEqual(second_state.start_time, first_state.start_time)
        self.assertEqual(second_state.order_token, first_state.order_token)
        self.assertEqual(second_state.scan_slice_end, datetime(2026, 1, 1))
        self.assertNotEqual(second_state.fingerprint(), first_state.fingerprint())

    def test_row_cursor_clears_consumed_private_checkpoint(self):
        prior_state = query_builder_matrix.CursorState(
            start_time=datetime(2026, 1, 2),
            order_token="trace-10",
            scan_slice_start=datetime(2026, 1, 1),
            scan_slice_end=datetime(2026, 1, 2),
        )
        prior = query_builder_matrix.PageResult(
            record={}, rows=[], cursor_state=prior_state
        )
        row_time = datetime(2026, 1, 1, 10)
        state = query_builder_matrix.next_cursor_state(
            kind="trace",
            rows=[{"start_time": row_time, "trace_id": "trace-11"}],
            bounded_page=SimpleNamespace(
                has_more=True,
                continuation_slice_start=datetime(2026, 1, 1),
                continuation_slice_end=datetime(2026, 1, 2),
                continuation_before_start_time=row_time,
                continuation_before_id="trace-12",
            ),
            continuation=prior,
            has_more=True,
        )

        self.assertEqual(state.start_time, row_time)
        self.assertEqual(state.order_token, "trace-11")
        self.assertIsNone(state.scan_slice_start)
        self.assertIsNone(state.scan_slice_end)

    def test_terminal_page_has_no_public_cursor_state(self):
        self.assertIsNone(
            query_builder_matrix.next_cursor_state(
                kind="trace",
                rows=[],
                bounded_page=None,
                continuation=None,
                has_more=False,
            )
        )

    def test_empty_cursor_chain_at_sample_limit_fails_closed(self):
        self.assertTrue(
            query_builder_matrix.empty_chain_limit_reached(
                {
                    "pages_checked": 12,
                    "empty_pages": 12,
                    "sample_limit_reached": True,
                }
            )
        )
        self.assertFalse(
            query_builder_matrix.empty_chain_limit_reached(
                {
                    "pages_checked": 12,
                    "empty_pages": 11,
                    "sample_limit_reached": True,
                }
            )
        )
        self.assertFalse(
            query_builder_matrix.empty_chain_limit_reached(
                {
                    "pages_checked": 2,
                    "empty_pages": 2,
                    "sample_limit_reached": False,
                }
            )
        )


def list_response(
    rows: list[dict],
    *,
    has_more: bool = False,
    cursor: str | None = None,
    complete: bool = True,
    status: str = "complete",
    query_exact: bool | None = None,
    config: list[dict] | None = None,
    filters: list[dict] | None = None,
    kind: str = "trace",
    project_id: str = "project-1",
) -> Response:
    metadata = {
        "has_more": has_more,
        "next_cursor": cursor,
        "query_complete": complete,
        "query_status": status,
    }
    if query_exact is not None:
        metadata["query_exact"] = query_exact
    if filters is not None:
        digest, count = qualify._filter_binding_sha256(
            project_id=project_id,
            kind=kind,
            filters=filters,
        )
        metadata.update(
            {
                "query_applied_filter_version": qualify.FILTER_ATTESTATION_VERSION,
                "query_applied_filter_sha256": digest,
                "query_applied_filter_count": count,
            }
        )
    result = (
        {
            **metadata,
            "results": rows,
            "config": list(config or []),
        }
        if kind == "voice"
        else {
            "table": rows,
            "metadata": metadata,
            "config": list(config or []),
        }
    )
    return Response({"status": True, "result": result})


def filter_values_response(values: list[dict]) -> Response:
    return Response(
        {
            "status": True,
            "result": {
                "values": values,
                "query_complete": True,
                "query_status": "complete",
                "has_more": False,
                "browse_status": "exhausted",
                "next_cursor": None,
            },
        }
    )


def model_value_response(
    values: list[dict],
    *,
    has_more: bool = False,
    cursor: str | None = None,
    query_count: object = qualify.MODEL_VALUE_EXPECTED_ACTIVATED_QUERY_COUNT,
    epoch: int = 3,
    revision: int = 7,
    fingerprint: str = "a" * 64,
) -> Response:
    return Response(
        {
            "status": True,
            "result": {
                "values": values,
                "has_more": has_more,
                "browse_status": "continuation" if has_more else "exhausted",
                "next_cursor": cursor,
                "query_complete": True,
                "query_status": "complete",
                "query_count": query_count,
                "catalog_epoch": epoch,
                "catalog_revision": revision,
                "activation_fingerprint": fingerprint,
                "attribute_types": ["string"],
                "attribute_types_exact": True,
                "query_provenance": "activated_property_catalog",
            },
        }
    )


def graph_response(
    points: list[dict],
    *,
    query_exact: bool,
    metric_name: str = "latency",
    provenance: str | None = None,
    complete: bool = True,
    status: str = "complete",
    sampled: bool = False,
    start: datetime = datetime(2026, 1, 1, tzinfo=UTC),
    end: datetime = datetime(2026, 2, 1, tzinfo=UTC),
    applied_filter_sha256: str | None = None,
    applied_filter_count: int | None = None,
) -> Response:
    result = {
        "metric_name": metric_name,
        "data": points,
        "query_complete": complete,
        "query_status": status,
        "query_sampled": sampled,
        "query_exact": query_exact,
        "query_window_start": start.isoformat(),
        "query_window_end": end.isoformat(),
    }
    if provenance is not None:
        result["query_provenance"] = provenance
    if applied_filter_sha256 is not None:
        result["query_applied_filter_version"] = qualify.FILTER_ATTESTATION_VERSION
        result["query_applied_filter_sha256"] = applied_filter_sha256
    if applied_filter_count is not None:
        result["query_applied_filter_count"] = applied_filter_count
    return Response({"status": True, "result": result})


def dashboard_query_response(
    points: list[dict],
    *,
    start: datetime,
    end: datetime,
    granularity: str,
    exact: bool = True,
    complete: bool = True,
    status: str = "complete",
    sampled: bool = False,
    provenance: str = "exact_snapshot",
) -> Response:
    metric = {
        "id": "latency",
        "name": "latency",
        "aggregation": "avg",
        "unit": "ms",
        "series": [{"name": "total", "data": points}],
        "query_complete": complete,
        "query_status": status,
        "query_sampled": sampled,
        "query_exact": exact,
        "query_provenance": provenance,
    }
    return Response(
        {
            "status": True,
            "result": {
                "metrics": [metric],
                "time_range": {
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                },
                "granularity": granularity,
                "query_complete": complete,
                "query_status": status,
                "query_sampled": sampled,
                "query_exact": exact,
                "query_provenance": provenance,
            },
        }
    )


def graph_points(
    start: datetime,
    end: datetime,
    window_name: str,
    *,
    value: float = 12.0,
    traffic: int = 3,
) -> list[dict]:
    interval = qualify._interval_for_window(window_name)
    return [
        {
            "timestamp": timestamp.isoformat(),
            "value": value,
            "primary_traffic": traffic,
        }
        for timestamp in qualify._expected_graph_timestamps(start, end, interval)
    ]


def trace_row(
    trace_id: str,
    *,
    created_at: str = "2026-01-01T12:00:00Z",
    project_id: str = "project-1",
    **values,
) -> dict:
    return {
        "trace_id": trace_id,
        "project_id": project_id,
        "created_at": created_at,
        **values,
    }


class RenderableResponse:
    def __init__(self, data: dict, status_code: int = 200):
        self.data = data
        self.status_code = status_code
        self.rendered_content: bytes | None = None

    def render(self):
        import json

        self.rendered_content = json.dumps(
            self.data,
            allow_nan=False,
            separators=(",", ":"),
        ).encode()
        return self


def metrics_response(
    metrics: list[dict],
    *,
    has_more: bool,
    cursor: str | None = None,
    page_size: int = qualify.METRIC_CATALOG_QUALIFICATION_PAGE_SIZE,
    epoch: int = 3,
    revision: int = 7,
    fingerprint: str = "a" * 64,
) -> Response:
    return Response(
        {
            "status": True,
            "result": {
                "metrics": metrics,
                "total": None,
                "total_is_exact": False,
                "page_size": page_size,
                "has_more": has_more,
                "next_cursor": cursor,
                "catalog_epoch": epoch,
                "catalog_revision": revision,
                "activation_fingerprint": fingerprint,
                "query_complete": True,
                "query_exact": True,
                "query_status": "complete",
                "query_provenance": "activated_property_catalog",
            },
        }
    )


def preview_response(
    rows: list[dict],
    *,
    has_more: bool,
    cursor: str | None,
    snapshot_total: int,
    loaded_through: int,
    complete: bool | None = None,
    exact: bool = True,
    snapshot_at: str = "2026-08-15T00:00:00Z",
) -> Response:
    return Response(
        {
            "results": rows,
            "next_cursor": cursor,
            "has_more": has_more,
            "snapshot_total": snapshot_total,
            "loaded_through": loaded_through,
            "complete": (not has_more if complete is None else complete),
            "exact": exact,
            "snapshot_at": snapshot_at,
        }
    )


def dataset_page(
    *,
    page_index: int,
    total_rows: int,
    row_ids: list[str],
    has_more: bool,
    next_cursor: str | None,
) -> dict:
    page_size = 50
    return {
        "metadata": {
            "dataset_name": "Reference answers",
            "total_rows": total_rows,
            "total_pages": 0 if total_rows == 0 else (total_rows + 49) // 50,
            "page_size": page_size,
            "current_page_index": page_index,
            "has_more": has_more,
            "next_page_index": page_index + 1 if has_more else None,
            "next_cursor": next_cursor,
            "is_exact": True,
            "snapshot_bound": True,
            "error_messages": [],
        },
        "table": [{"row_id": row_id} for row_id in row_ids],
    }


class QueueClient:
    def __init__(self, responses: list[Response]):
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.project = SimpleNamespace(id="project-1")

    def call(self, endpoint: str, **kwargs):
        self.calls.append({"endpoint": endpoint, **kwargs})
        if not self.responses:
            raise AssertionError("unexpected extra request")
        return self.responses.pop(0)


def sample_target(name: str = "whatfix") -> qualify.Target:
    return qualify.Target(
        name=name,
        project=SimpleNamespace(id="project-1"),
        principal=SimpleNamespace(),
        key="customer.key",
        value="customer-1",
        value_type="string",
        system_value="gpt-4.1",
        system_value_type="string",
        eval_profile=qualify.RelationalFilterProfile(
            property_id="eval_config:eval-1",
            column_id="eval-1",
            col_type="EVAL_METRIC",
            filter_type="text",
            filter_op="in",
            filter_value=("Passed",),
            output_type="PASS_FAIL",
        ),
        annotation_profile=qualify.RelationalFilterProfile(
            property_id="annotation:label-1",
            column_id="label-1",
            col_type="ANNOTATION",
            filter_type="categorical",
            filter_op="in",
            filter_value=("approved",),
            output_type="categorical",
        ),
    )


class SafetyTests(unittest.TestCase):
    def test_schema_is_current(self):
        self.assertEqual(safety.SCHEMA, "th7247-current-select-only/v2")

    def test_static_guard_self_test(self):
        safety.static_guard_self_test()

    def test_postgres_allows_only_read_and_minimal_controls(self):
        accepted = (
            ("SELECT 1", None),
            ("WITH source AS (SELECT 1) SELECT * FROM source", None),
            ("SELECT 'UPDATE and DELETE are data here'", None),
            ("SELECT set_config('statement_timeout', %s, true)", ["9000ms"]),
            ("SET LOCAL statement_timeout = %s", ["9.5s"]),
            ("SET LOCAL statement_timeout = '9000ms'", None),
            ('SAVEPOINT "s123_x1"', None),
            ('RELEASE SAVEPOINT "s123_x1"', None),
            ('ROLLBACK TO SAVEPOINT "s123_x1"', None),
            ("SET TRANSACTION READ ONLY", None),
        )
        for statement, params in accepted:
            with self.subTest(statement=statement):
                safety.assert_pg_read(statement, params)

        rejected = (
            "INSERT INTO t VALUES (1)",
            "UPDATE t SET value=1",
            "DELETE FROM t",
            "WITH changed AS (UPDATE t SET value=1 RETURNING value) SELECT * FROM changed",
            "SELECT nextval('seq')",
            "SELECT * FROM t FOR UPDATE",
            "SELECT 1; SELECT 2",
            "SELECT set_config('default_transaction_read_only', 'off', false)",
            "SELECT set_config('statement_timeout', '0', false)",
            "SET LOCAL statement_timeout = '9501ms'",
            "SET LOCAL statement_timeout = '0ms'",
            "SELECT 'unterminated",
        )
        for statement in rejected:
            with self.subTest(statement=statement):
                with self.assertRaises(safety.SafetyViolation):
                    safety.assert_pg_read(statement)

    def test_clickhouse_allows_select_and_rejects_mutation(self):
        for statement in (
            "SELECT 1",
            "SELECT 'DROP is data'",
            "WITH source AS (SELECT 1) SELECT * FROM source",
        ):
            safety.assert_ch_read(statement)
        for statement in (
            "INSERT INTO t VALUES (1)",
            "SYSTEM FLUSH LOGS",
            "WITH source AS (SELECT 1) INSERT INTO t SELECT * FROM source",
            "SELECT 1; SELECT 2",
        ):
            with self.subTest(statement=statement):
                with self.assertRaises(safety.SafetyViolation):
                    safety.assert_ch_read(statement)

    def test_clickhouse_settings_are_forced_and_bounded(self):
        bounded = safety.bounded_ch_settings(
            {
                "readonly": 0,
                "max_execution_time": 999,
                "max_threads": 99,
                "max_memory_usage": 2**63,
                "max_result_rows": 2**63,
                "max_result_bytes": 2**63,
                "max_bytes_to_read": 2**63,
                "max_rows_to_read": 1,
            }
        )
        self.assertEqual(bounded["readonly"], 2)
        self.assertEqual(bounded["max_execution_time"], safety.CH_TIMEOUT_SECONDS)
        self.assertEqual(bounded["max_threads"], safety.CH_MAX_THREADS)
        self.assertNotIn("max_rows_to_read", bounded)
        self.assertEqual(bounded["timeout_overflow_mode"], "throw")
        self.assertEqual(bounded["result_overflow_mode"], "throw")

    def test_every_clickhouse_execute_guard_reserves_before_increment(self):
        original_calls = []

        class NativeClient:
            def execute(self, query, *args, **kwargs):
                original_calls.append(("native.execute", query, args, kwargs))
                return "native.execute"

            def execute_iter(self, query, *args, **kwargs):
                original_calls.append(("native.execute_iter", query, args, kwargs))
                return iter(())

        class HTTPClient:
            def query(self, query, *args, **kwargs):
                original_calls.append(("http.query", query, args, kwargs))
                return "http.query"

            def command(self, query, *args, **kwargs):
                original_calls.append(("http.command", query, args, kwargs))
                return "http.command"

            def raw_query(self, query, *args, **kwargs):
                original_calls.append(("http.raw_query", query, args, kwargs))
                return "http.raw_query"

        native_module = ModuleType("clickhouse_driver")
        native_module.Client = NativeClient
        connect_module = ModuleType("clickhouse_connect")
        connect_module.__path__ = []
        connect_driver_module = ModuleType("clickhouse_connect.driver")
        connect_driver_module.__path__ = []
        connect_client_module = ModuleType("clickhouse_connect.driver.client")
        connect_client_module.Client = HTTPClient
        connect_module.driver = connect_driver_module
        connect_driver_module.client = connect_client_module
        fake_modules = {
            "clickhouse_driver": native_module,
            "clickhouse_connect": connect_module,
            "clickhouse_connect.driver": connect_driver_module,
            "clickhouse_connect.driver.client": connect_client_module,
        }

        with qualify._lock:
            original_counts = dict(qualify._counts)
            qualify._counts["ch_read"] = 0
            qualify._counts["ch_blocked"] = 0
        try:
            with (
                mock.patch.dict(sys.modules, fake_modules),
                mock.patch.object(qualify, "MAX_CH_READS", 2),
                mock.patch.object(qualify, "_child_count_bridge", None),
            ):
                qualify._install_ch_guard()
                native = NativeClient()
                http = HTTPClient()
                self.assertEqual(native.execute("SELECT 1"), "native.execute")
                self.assertEqual(http.query("SELECT 2"), "http.query")

                guarded_calls = (
                    lambda: native.execute("SELECT 3"),
                    lambda: native.execute_iter("SELECT 4"),
                    lambda: http.query("SELECT 5"),
                    lambda: http.command("SELECT 6"),
                    lambda: http.raw_query("SELECT 7"),
                )
                for guarded_call in guarded_calls:
                    with self.assertRaisesRegex(
                        safety.SafetyViolation,
                        "ClickHouse-read fuse reached",
                    ):
                        guarded_call()

                counts = qualify._snapshot_counts()
                self.assertEqual(counts["ch_read"], 2)
                self.assertEqual(counts["ch_blocked"], len(guarded_calls))
                self.assertEqual(
                    [call[0] for call in original_calls],
                    ["native.execute", "http.query"],
                )
        finally:
            with qualify._lock:
                qualify._counts.update(original_counts)

    def test_manifest_paths_fail_closed(self):
        self.assertEqual(
            safety.safe_relative_path("tracer/views/trace.py").as_posix(),
            "tracer/views/trace.py",
        )
        for path in ("", "/absolute", "../escape", "tracer/../escape", "./trace.py"):
            with self.subTest(path=path):
                with self.assertRaises(safety.SafetyViolation):
                    safety.safe_relative_path(path)


class AssemblyTests(unittest.TestCase):
    def test_secret_filter_allows_source_and_templates(self):
        allowed = (
            ".env.example",
            "deploy/.env.production.example",
            "futureagi/.secrets.baseline",
            "futureagi/integrations/services/credentials.py",
            "futureagi/model_hub/views/secrets.py",
        )
        blocked = (
            ".env",
            "deploy/.env.production",
            "credentials",
            "id_rsa",
            "keys/private.pem",
            "config/service-account.json",
            "config/prod-credentials.json",
            "config/client_secret.json",
        )
        for path in allowed:
            with self.subTest(allowed=path):
                self.assertFalse(assemble._is_suspicious_source_path(path))
        for path in blocked:
            with self.subTest(blocked=path):
                self.assertTrue(assemble._is_suspicious_source_path(path))

    def test_full_runtime_overlay_includes_clean_and_dirty_files(self):
        clean = b"clean"
        dirty = b"dirty"
        frontend = b"frontend"
        entries = [
            assemble.SourceEntry(
                path="futureagi/clean.py",
                kind="file",
                mode=0o644,
                size=len(clean),
                sha256=safety.sha256_bytes(clean),
            ),
            assemble.SourceEntry(
                path="futureagi/dirty.py",
                kind="file",
                mode=0o755,
                size=len(dirty),
                sha256=safety.sha256_bytes(dirty),
            ),
            assemble.SourceEntry(
                path="frontend/app.js",
                kind="file",
                mode=0o644,
                size=len(frontend),
                sha256=safety.sha256_bytes(frontend),
            ),
        ]
        runtime_files, members = assemble._runtime_overlay(
            entries,
            {
                "futureagi/clean.py": clean,
                "futureagi/dirty.py": dirty,
                "frontend/app.js": frontend,
            },
        )
        self.assertEqual(set(runtime_files), {"clean.py", "dirty.py"})
        self.assertEqual(
            {(path, data, mode) for path, data, mode in members},
            {("clean.py", clean, 0o644), ("dirty.py", dirty, 0o755)},
        )

    def test_runtime_overlay_rejects_empty_backend(self):
        with self.assertRaises(safety.SafetyViolation):
            assemble._runtime_overlay([], {})

    def test_assembler_is_inert_and_digest_pinned(self):
        source = inspect.getsource(assemble)
        self.assertIn("base image must be pinned", source)
        self.assertIn('"runtime_files": runtime_files', source)
        self.assertNotIn("docker build", source.lower())
        self.assertNotIn("kubectl", source.lower())
        self.assertNotIn("gcloud", source.lower())

    def test_job_has_only_bounded_writable_mounts_and_no_service_token(self):
        template = assemble._job_template(
            source_manifest_sha256="a" * 64,
            qualifier_sha256="b" * 64,
        )
        self.assertIn("name: __READ_ONLY_RUNTIME_SECRET__", template)
        self.assertNotIn("core-backend-secret", template)
        self.assertIn("automountServiceAccountToken: false", template)
        self.assertIn("mountPath: /tmp", template)
        self.assertIn("mountPath: /app/backend/logs", template)
        self.assertEqual(template.count("kind: Job"), 1)
        self.assertEqual(template.count("activeDeadlineSeconds: 5400"), 1)
        self.assertEqual(template.count("- name: QUALIFIER_SHARD"), 1)
        self.assertEqual(template.count('value: "__QUALIFIER_SHARD__"'), 1)
        self.assertEqual(template.count('value: "__QUALIFIER_END_UTC__"'), 1)
        self.assertEqual(template.count('value: "__QUALIFIER_RUN_ID__"'), 1)
        self.assertNotIn("\n---\n", template)


class QualificationContractTests(unittest.TestCase):
    def test_time_matrix_reaches_twelve_months_and_includes_six_hours(self):
        expected = ["30m", "1h", "6h", "24h", "7d", "30d", "90d", "180d", "365d"]
        self.assertEqual([name for name, _duration in qualify.WINDOWS], expected)
        self.assertEqual(dict(qualify.WINDOWS)["365d"], timedelta(days=365))
        self.assertEqual(qualify._interval_for_window("6h"), "hour")
        self.assertEqual(qualify._interval_for_window("365d"), "month")

    def test_named_dense_and_sparse_targets_are_frozen(self):
        self.assertEqual(
            qualify.TARGETS["whatfix"]["anchor_project_id"],
            "4b3d0477-ff0f-4681-9535-9b152152bf25",
        )
        self.assertEqual(qualify.TARGETS["whatfix"]["density"], "dense")
        self.assertEqual(
            qualify.TARGETS["colektia"]["anchor_project_id"],
            "ca3025a9-b5eb-4872-9973-2330956d40d2",
        )
        self.assertEqual(qualify.TARGETS["colektia"]["density"], "sparse")
        self.assertEqual(qualify.TARGETS["colektia"]["tokens"], ("colektia", "colly"))

    def test_qualified_subset_and_openapi_inventory_are_exact(self):
        expected = {
            "property_keys": ("GET", "/api/traces/span-attribute-keys/"),
            "filter_values": ("GET", "/tracer/dashboard/filter_values/"),
            "metrics": ("GET", "/tracer/dashboard/metrics/"),
            "dashboard_query": ("POST", "/tracer/dashboard/query/"),
            "trace_list": ("GET", "/tracer/trace/list_traces_of_session/"),
            "span_list": ("GET", "/tracer/observation-span/list_spans_observe/"),
            "session_list": ("GET", "/tracer/trace-session/list_sessions/"),
            "voice_list": ("GET", "/tracer/trace/list_voice_calls/"),
            "users": ("GET", "/tracer/users/"),
            "trace_graph": ("POST", "/tracer/trace/get_graph_methods/"),
            "span_graph": ("POST", "/tracer/observation-span/get_graph_methods/"),
            "session_graph": ("POST", "/tracer/trace-session/get_session_graph_data/"),
            "dataset_exact": (
                "GET",
                "/model-hub/develops/{dataset_id}/get-dataset-table/",
            ),
            "simulation_executions": (
                "GET",
                "/simulate/run-tests/{run_test_id}/preview-executions/",
            ),
            "simulation_calls": (
                "GET",
                "/simulate/test-executions/{test_execution_id}/preview-calls/",
            ),
        }
        actual = {key: value[:2] for key, value in qualify.ROUTES.items()}
        self.assertEqual(actual, expected)
        inventory = openapi_inventory.verify_repo_inventory(PACKAGE_DIR.parents[2])
        expected_operations = set(openapi_inventory.expected_operations())
        self.assertEqual(len(expected_operations), 65)
        self.assertEqual(len(inventory["direct_operations"]), 32)
        self.assertEqual(len(inventory["transitive_only_operations"]), 33)
        self.assertEqual(len(inventory["changed_definitions"]), 48)

        matrix = (
            PACKAGE_DIR.parents[2] / "docs/TH7247_INTERACTIVE_READ_MATRIX.md"
        ).read_text(encoding="utf-8")
        exact_section = matrix.split("## Exact current contract diff:", 1)[1].split(
            "### Exact live-qualifier boundary", 1
        )[0]
        documented = set(
            re.findall(r"`(GET|POST|PUT|PATCH|DELETE) ([^`]+)`", exact_section)
        )
        self.assertEqual(documented, expected_operations)

        boundary = matrix.split("### Exact live-qualifier boundary", 1)[1].split(
            "The current-source SELECT-only qualifier covers", 1
        )[0]
        excluded = set(
            re.findall(r"`(GET|POST|PUT|PATCH|DELETE) ([^`]+)`", boundary)
        ) - {("GET", "/tracer/users/")}
        live_changed = set(actual.values()) & expected_operations
        self.assertEqual(len(live_changed), 14)
        self.assertEqual(excluded, expected_operations - live_changed)

    def test_qualifier_has_no_high_level_network_client_import(self):
        tree = ast.parse(Path(qualify.__file__).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        forbidden = {"requests", "httpx", "urllib.request", "subprocess"}
        self.assertFalse(imported & forbidden)
        self.assertIn("socket", imported)

    def test_startup_network_block_fails_closed_and_restores_socket_methods(self):
        original_create_connection = socket.create_connection
        original_socket_connect = socket.socket.connect
        events = []

        def unexpected_startup_connect():
            events.append("setup")
            socket.create_connection(("127.0.0.1", 9))

        with (
            mock.patch.object(
                qualify,
                "_validate_runtime_read_settings",
                side_effect=lambda: events.append("validate"),
            ),
            mock.patch.object(
                qualify,
                "_preload_reviewed_url_callbacks",
                side_effect=lambda: events.append("preload"),
            ),
            mock.patch.object(
                qualify,
                "_install_dispatch_tripwires",
                side_effect=lambda: events.append("install"),
            ),
        ):
            with self.assertRaisesRegex(
                safety.SafetyViolation,
                "raw network connection blocked",
            ):
                qualify._bootstrap_reviewed_django_runtime(unexpected_startup_connect)

        self.assertEqual(events, ["setup"])
        self.assertIs(socket.create_connection, original_create_connection)
        self.assertIs(socket.socket.connect, original_socket_connect)

    def test_bootstrap_restores_network_only_after_tripwires_are_active(self):
        original_create_connection = socket.create_connection
        original_socket_connect = socket.socket.connect
        original_evidence = dict(qualify._startup_preload_evidence)
        fake_settings = object()
        events = []

        def assert_blocked(stage):
            self.assertIsNot(socket.create_connection, original_create_connection)
            self.assertIsNot(socket.socket.connect, original_socket_connect)
            self.assertTrue(
                getattr(
                    socket.create_connection,
                    "_qualifier_startup_network_block",
                    False,
                )
            )
            events.append(stage)

        try:
            with (
                mock.patch.object(
                    qualify,
                    "_validate_runtime_read_settings",
                    side_effect=lambda: (
                        assert_blocked("validate"),
                        fake_settings,
                    )[1],
                ),
                mock.patch.object(
                    qualify,
                    "_preload_reviewed_url_callbacks",
                    side_effect=lambda: (
                        assert_blocked("preload"),
                        {"completed": True},
                    )[1],
                ),
                mock.patch.object(
                    qualify,
                    "_install_dispatch_tripwires",
                    side_effect=lambda: assert_blocked("install"),
                ),
                mock.patch.object(
                    qualify,
                    "_dispatch_tripwires_active",
                    side_effect=lambda: (
                        assert_blocked("active"),
                        True,
                    )[1],
                ),
            ):
                startup, settings = qualify._bootstrap_reviewed_django_runtime(
                    lambda: assert_blocked("setup")
                )
        finally:
            qualify._startup_preload_evidence.clear()
            qualify._startup_preload_evidence.update(original_evidence)

        self.assertEqual(
            events,
            ["setup", "validate", "preload", "install", "active"],
        )
        self.assertTrue(startup["callback_tripwires_active"])
        self.assertIs(settings, fake_settings)
        self.assertIs(socket.create_connection, original_create_connection)
        self.assertIs(socket.socket.connect, original_socket_connect)

    def test_url_preload_suppresses_only_frozen_nltk_downloads_and_redis_pings(self):
        network_calls = []

        def actual_nltk_download(*args, **kwargs):
            network_calls.append(("nltk", args, kwargs))
            return False

        class FakeRedis:
            def execute_command(self, *args, **kwargs):
                network_calls.append(("redis", args, kwargs))
                return False

        class FakeAsyncRedis:
            async def execute_command(self, *args, **kwargs):
                network_calls.append(("async_redis", args, kwargs))
                return False

        nltk_module = ModuleType("nltk")
        nltk_module.download = actual_nltk_download
        redis_module = ModuleType("redis")
        redis_module.__path__ = []
        redis_module.Redis = FakeRedis
        async_redis_module = ModuleType("redis.asyncio")
        async_redis_module.Redis = FakeAsyncRedis
        redis_module.asyncio = async_redis_module
        django_module = ModuleType("django")
        django_module.__path__ = []
        django_urls_module = ModuleType("django.urls")

        path_bindings = {
            qualify._route_preload_path(path_template): (method, expected_action)
            for method, path_template, expected_action in qualify.ROUTES.values()
        }
        resolve_calls = []

        def resolve(path):
            if not resolve_calls:
                for package in qualify.EXPECTED_STARTUP_NLTK_DOWNLOADS:
                    nltk_module.download(package, quiet=True)
                for _index in range(qualify.EXPECTED_STARTUP_REDIS_PINGS):
                    FakeRedis().execute_command("PING")
            resolve_calls.append(path)
            method, expected_action = path_bindings[path]
            callback = SimpleNamespace(actions={method.lower(): expected_action})
            return SimpleNamespace(func=callback)

        django_urls_module.Resolver404 = type("Resolver404", (Exception,), {})
        django_urls_module.resolve = resolve
        django_module.urls = django_urls_module
        fake_modules = {
            "django": django_module,
            "django.urls": django_urls_module,
            "nltk": nltk_module,
            "redis": redis_module,
            "redis.asyncio": async_redis_module,
        }
        before = qualify._snapshot_counts()
        original_redis_execute = FakeRedis.execute_command
        original_async_redis_execute = FakeAsyncRedis.execute_command
        with mock.patch.dict(sys.modules, fake_modules):
            evidence = qualify._preload_reviewed_url_callbacks()

        after = qualify._snapshot_counts()
        self.assertEqual(network_calls, [])
        self.assertEqual(resolve_calls, list(path_bindings))
        self.assertEqual(evidence["preloaded_route_count"], len(qualify.ROUTES))
        self.assertRegex(evidence["preloaded_route_binding_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            evidence["nltk_downloads_suppressed"],
            list(qualify.EXPECTED_STARTUP_NLTK_DOWNLOADS),
        )
        self.assertEqual(
            evidence["redis_pings_suppressed"],
            qualify.EXPECTED_STARTUP_REDIS_PINGS,
        )
        self.assertEqual(
            after["startup_nltk_download_suppressed"]
            - before["startup_nltk_download_suppressed"],
            len(qualify.EXPECTED_STARTUP_NLTK_DOWNLOADS),
        )
        self.assertEqual(
            after["startup_redis_ping_suppressed"]
            - before["startup_redis_ping_suppressed"],
            qualify.EXPECTED_STARTUP_REDIS_PINGS,
        )
        self.assertIs(nltk_module.download, actual_nltk_download)
        self.assertIs(FakeRedis.execute_command, original_redis_execute)
        self.assertIs(FakeAsyncRedis.execute_command, original_async_redis_execute)

    def test_url_preload_rejects_unreviewed_io_without_falling_through(self):
        network_calls = []

        def actual_nltk_download(*args, **kwargs):
            network_calls.append(("nltk", args, kwargs))

        class FakeRedis:
            def execute_command(self, *args, **kwargs):
                network_calls.append(("redis", args, kwargs))

        class FakeAsyncRedis:
            async def execute_command(self, *args, **kwargs):
                network_calls.append(("async_redis", args, kwargs))

        nltk_module = ModuleType("nltk")
        nltk_module.download = actual_nltk_download
        redis_module = ModuleType("redis")
        redis_module.__path__ = []
        redis_module.Redis = FakeRedis
        async_redis_module = ModuleType("redis.asyncio")
        async_redis_module.Redis = FakeAsyncRedis
        redis_module.asyncio = async_redis_module
        with mock.patch.dict(
            sys.modules,
            {
                "nltk": nltk_module,
                "redis": redis_module,
                "redis.asyncio": async_redis_module,
            },
        ):
            with qualify._suppress_reviewed_url_import_side_effects():
                with self.assertRaisesRegex(
                    safety.SafetyViolation,
                    "unexpected NLTK download",
                ):
                    nltk_module.download("unreviewed-corpus", quiet=True)
                with self.assertRaisesRegex(
                    safety.SafetyViolation,
                    "unexpected Redis command",
                ):
                    FakeRedis().execute_command("SET", "key", "value")

        self.assertEqual(network_calls, [])

    def test_url_preload_precedes_permanent_callback_tripwires(self):
        source = inspect.getsource(qualify._bootstrap_reviewed_django_runtime)
        setup = source.index("django_setup()")
        preload = source.index("startup_preload = _preload_reviewed_url_callbacks()")
        install = source.index("_install_dispatch_tripwires()")
        assert_active = source.index("if not _dispatch_tripwires_active()")
        restore_scope = source.index("return startup_preload, settings")
        self.assertLess(setup, preload)
        self.assertLess(preload, install)
        self.assertLess(install, assert_active)
        self.assertLess(assert_active, restore_scope)

        run_source = inspect.getsource(qualify._run)
        bootstrap = run_source.index("_bootstrap_reviewed_django_runtime")
        context_hook = run_source.index("_install_request_context_hook()")
        self.assertLess(bootstrap, context_hook)

    def test_simulation_preview_qualifier_accepts_truthful_read_more(self):
        first = preview_response(
            [{"id": "row-1"}],
            has_more=True,
            cursor="signed-next",
            snapshot_total=2,
            loaded_through=1,
        )
        terminal = preview_response(
            [{"id": "row-2"}],
            has_more=False,
            cursor=None,
            snapshot_total=2,
            loaded_through=2,
        )
        client = QueueClient([first, terminal])

        first_payload = qualify._preview_page(
            client,
            endpoint="simulation_executions",
            lane="preview.p1",
            path_kwargs={"run_test_id": "run-1"},
            query={"page_size": 1},
        )
        terminal_payload = qualify._preview_page(
            client,
            endpoint="simulation_executions",
            lane="preview.p2",
            path_kwargs={"run_test_id": "run-1"},
            query={"page_size": 1, "cursor": "signed-next"},
        )

        self.assertFalse(first_payload["complete"])
        self.assertTrue(terminal_payload["complete"])

    def test_simulation_preview_repeat_crosses_issue_time_and_resumes_both_chains(self):
        client = QueueClient(
            [
                preview_response(
                    [{"id": "row-1"}],
                    has_more=True,
                    cursor="snapshot-a.1000.sig",
                    snapshot_total=2,
                    loaded_through=1,
                    snapshot_at="2026-08-15T00:00:00Z",
                ),
                preview_response(
                    [{"id": "row-1"}],
                    has_more=True,
                    cursor="snapshot-b.1001.sig",
                    snapshot_total=2,
                    loaded_through=1,
                    snapshot_at="2026-08-15T00:00:01Z",
                ),
                preview_response(
                    [{"id": "row-2"}],
                    has_more=False,
                    cursor=None,
                    snapshot_total=2,
                    loaded_through=2,
                    snapshot_at="2026-08-15T00:00:00Z",
                ),
                preview_response(
                    [{"id": "row-2"}],
                    has_more=False,
                    cursor=None,
                    snapshot_total=2,
                    loaded_through=2,
                    snapshot_at="2026-08-15T00:00:01Z",
                ),
            ]
        )

        first_ids, second_count = qualify._qualify_preview_repeat_chain(
            client,
            endpoint="simulation_executions",
            lane="preview.timestamp-boundary",
            path_kwargs={"run_test_id": "run-1"},
            query={"page_size": 1},
        )

        self.assertEqual(first_ids, ("row-1",))
        self.assertEqual(second_count, 1)
        self.assertEqual(
            [call["query"].get("cursor") for call in client.calls],
            [None, None, "snapshot-a.1000.sig", "snapshot-b.1001.sig"],
        )

    def test_simulation_preview_repeat_rejects_divergent_second_chain(self):
        client = QueueClient(
            [
                preview_response(
                    [{"id": "row-1"}],
                    has_more=True,
                    cursor="snapshot-a.1000.sig",
                    snapshot_total=2,
                    loaded_through=1,
                ),
                preview_response(
                    [{"id": "row-1"}],
                    has_more=True,
                    cursor="snapshot-b.1001.sig",
                    snapshot_total=2,
                    loaded_through=1,
                    snapshot_at="2026-08-15T00:00:01Z",
                ),
                preview_response(
                    [{"id": "row-2"}],
                    has_more=False,
                    cursor=None,
                    snapshot_total=2,
                    loaded_through=2,
                ),
                preview_response(
                    [{"id": "row-3"}],
                    has_more=False,
                    cursor=None,
                    snapshot_total=2,
                    loaded_through=2,
                    snapshot_at="2026-08-15T00:00:01Z",
                ),
            ]
        )

        with self.assertRaisesRegex(
            qualify.QualificationFailure,
            "changed continuation semantics",
        ):
            qualify._qualify_preview_repeat_chain(
                client,
                endpoint="simulation_executions",
                lane="preview.divergent",
                path_kwargs={"run_test_id": "run-1"},
                query={"page_size": 1},
            )

    def test_simulation_preview_qualifier_rejects_inconsistent_progress(self):
        invalid_responses = (
            preview_response(
                [{"id": "row-1"}],
                has_more=True,
                cursor="signed-next",
                snapshot_total=2,
                loaded_through=1,
                complete=True,
            ),
            preview_response(
                [{"id": "row-1"}],
                has_more=True,
                cursor=None,
                snapshot_total=2,
                loaded_through=1,
            ),
            preview_response(
                [{"id": "row-1"}],
                has_more=False,
                cursor=None,
                snapshot_total=2,
                loaded_through=1,
            ),
        )
        for response in invalid_responses:
            with self.subTest(response=response.data):
                with self.assertRaises(qualify.QualificationFailure):
                    qualify._preview_page(
                        QueueClient([response]),
                        endpoint="simulation_calls",
                        lane="preview.invalid",
                        path_kwargs={"test_execution_id": "execution-1"},
                        query={"page_size": 1, "run_test_id": "run-1"},
                    )

    def test_dataset_exact_page_uses_row_id_and_validates_continuation(self):
        first = dataset_page(
            page_index=0,
            total_rows=51,
            row_ids=[f"row-{index}" for index in range(50)],
            has_more=True,
            next_cursor="signed-page-2",
        )
        first_meta, first_ids = qualify._dataset_exact_page(
            first,
            lane="dataset.p1",
            requested_page_index=0,
            expected_page_size=50,
        )
        second = dataset_page(
            page_index=1,
            total_rows=51,
            row_ids=["row-50"],
            has_more=False,
            next_cursor=None,
        )
        _second_meta, second_ids = qualify._dataset_exact_page(
            second,
            lane="dataset.p2",
            requested_page_index=1,
            expected_page_size=50,
            expected_total_rows=first_meta["total_rows"],
        )
        self.assertEqual(first_ids[0], "row-0")
        self.assertEqual(second_ids, ("row-50",))
        self.assertFalse(set(first_ids) & set(second_ids))

    def test_dataset_exact_page_rejects_missing_ids_and_metadata_drift(self):
        valid = dataset_page(
            page_index=0,
            total_rows=1,
            row_ids=["row-0"],
            has_more=False,
            next_cursor=None,
        )
        invalid_pages = []
        missing_id = {**valid, "table": [{"id": "legacy-row-id"}]}
        invalid_pages.append(missing_id)
        duplicate_id = dataset_page(
            page_index=0,
            total_rows=2,
            row_ids=["same", "same"],
            has_more=False,
            next_cursor=None,
        )
        invalid_pages.append(duplicate_id)
        wrong_cursor = dataset_page(
            page_index=0,
            total_rows=51,
            row_ids=[f"row-{index}" for index in range(50)],
            has_more=True,
            next_cursor=None,
        )
        invalid_pages.append(wrong_cursor)
        wrong_page = dataset_page(
            page_index=1,
            total_rows=51,
            row_ids=["row-50"],
            has_more=False,
            next_cursor=None,
        )
        invalid_pages.append(wrong_page)

        for page in invalid_pages:
            with self.subTest(page=page):
                with self.assertRaises(qualify.QualificationFailure):
                    qualify._dataset_exact_page(
                        page,
                        lane="dataset.invalid",
                        requested_page_index=0,
                        expected_page_size=50,
                    )

    def test_dataset_exact_uses_the_preselected_catalog_representative(self):
        representative = qualify.DatasetRepresentative(
            dataset_id="11111111-1111-1111-1111-111111111111",
            active_rows=2,
            column_id="22222222-2222-2222-2222-222222222222",
        )
        page = dataset_page(
            page_index=0,
            total_rows=2,
            row_ids=["row-1", "row-2"],
            has_more=False,
            next_cursor=None,
        )
        client = QueueClient(
            [
                Response({"status": True, "result": page}),
                Response({"status": True, "result": page}),
            ]
        )
        result = qualify._qualify_dataset_exact(
            client,
            lane="dataset.shared",
            representative=representative,
        )
        self.assertEqual(
            result["dataset_representative_binding_sha256"],
            representative.binding_sha256,
        )
        self.assertEqual(
            result["dataset_column_property_id_digest"],
            qualify._digest(representative.column_property_id, 64),
        )
        self.assertEqual(
            [call["path_kwargs"]["dataset_id"] for call in client.calls],
            [representative.dataset_id, representative.dataset_id],
        )

        selector_source = inspect.getsource(qualify._select_dataset_representative)
        self.assertIn("INNER JOIN LATERAL", selector_source)
        self.assertIn("LIMIT 1", selector_source)
        self.assertNotIn("fetchall", selector_source)

    def test_dataset_repeat_crosses_issue_second_and_resumes_both_cursors(self):
        representative = qualify.DatasetRepresentative(
            dataset_id="11111111-1111-1111-1111-111111111111",
            active_rows=51,
            column_id="22222222-2222-2222-2222-222222222222",
        )
        first_ids = [f"row-{index}" for index in range(50)]
        client = QueueClient(
            [
                Response(
                    {
                        "status": True,
                        "result": dataset_page(
                            page_index=0,
                            total_rows=51,
                            row_ids=first_ids,
                            has_more=True,
                            next_cursor="dataset-state.1000.sig",
                        ),
                    }
                ),
                Response(
                    {
                        "status": True,
                        "result": dataset_page(
                            page_index=0,
                            total_rows=51,
                            row_ids=first_ids,
                            has_more=True,
                            next_cursor="dataset-state.1001.sig",
                        ),
                    }
                ),
                Response(
                    {
                        "status": True,
                        "result": dataset_page(
                            page_index=1,
                            total_rows=51,
                            row_ids=["row-50"],
                            has_more=False,
                            next_cursor=None,
                        ),
                    }
                ),
                Response(
                    {
                        "status": True,
                        "result": dataset_page(
                            page_index=1,
                            total_rows=51,
                            row_ids=["row-50"],
                            has_more=False,
                            next_cursor=None,
                        ),
                    }
                ),
            ]
        )

        result = qualify._qualify_dataset_exact(
            client,
            lane="dataset.timestamp-boundary",
            representative=representative,
        )

        self.assertEqual(result["p2_rows"], 1)
        self.assertEqual(
            [call["query"].get("cursor") for call in client.calls],
            [None, None, "dataset-state.1000.sig", "dataset-state.1001.sig"],
        )

    def test_dataset_and_simulation_qualification_lanes_are_required(self):
        source = inspect.getsource(qualify._run)
        dataset_lane = source.split('ancillary["dataset_exact"]', 1)[1].split(
            'ancillary["metrics_catalog"]', 1
        )[0]
        simulation_lane = source.split('ancillary["simulation_previews"]', 1)[1].split(
            'ancillary["rollup_safe_graphs"]', 1
        )[0]
        self.assertNotIn("required=False", dataset_lane)
        self.assertNotIn("required=False", simulation_lane)

    def test_request_fuse_covers_worst_case_matrix(self):
        # Discovery may inspect every bounded candidate, preferred key, and
        # both bounded metric-catalog categories, plus one exact Model page.
        # Default/custom/F1/F4 list cells use p1+repeat plus both independently
        # resumed p2 chains in every window; the seven relational cells use one
        # page except for the representative 365d dual-continuation proof.
        discovery = {
            name: qualify.MAX_TARGET_PROJECTS
            * (
                len(spec["preferred_keys"])
                + 1
                + 1
                + 2
                * (
                    qualify.METRIC_CATALOG_DISCOVERY_MAX_PAGES
                    + qualify.PROFILE_VALUE_DISCOVERY_MAX_CANDIDATES
                )
            )
            for name, spec in qualify.TARGETS.items()
        }
        windows = len(qualify.WINDOWS)
        short_windows = windows - 1
        profile_count = len(qualify._matrix_filter_profiles(sample_target()))
        core_profile_count = len(
            qualify._matrix_filter_profiles(sample_target(), partition="core")
        )
        system_profile_count = len(
            qualify._matrix_filter_profiles(sample_target(), partition="system")
        )
        relational_profile_count = core_profile_count - 2
        whatfix_or_colektia = (
            4
            + short_windows * (3 * (2 * 4 + relational_profile_count) + 4)
            + (3 * core_profile_count * 4 + 4)
        )
        mudflap = (
            4
            + short_windows
            * (2 * 4 + system_profile_count * 4 + relational_profile_count)
            + profile_count * 4
        )
        trace_system_per_target = windows * 3 * system_profile_count * 4
        graph_matrix = windows * 3 * profile_count
        dashboard_matrix = windows * profile_count
        whatfix_graphs = (
            qualify.METRIC_CATALOG_QUALIFICATION_MAX_PAGES
            + 3
            + 5
            + graph_matrix
            + dashboard_matrix
            + 4
            + 8
        )
        planned_by_shard = {
            "whatfix": discovery["whatfix"] + whatfix_or_colektia,
            "colektia": discovery["colektia"] + whatfix_or_colektia,
            "mudflap": discovery["mudflap"] + mudflap,
            "trace_system": discovery["whatfix"]
            + discovery["colektia"]
            + 2 * trace_system_per_target,
            "whatfix_graphs": discovery["whatfix"] + whatfix_graphs,
            "colektia_graphs": discovery["colektia"] + graph_matrix + dashboard_matrix,
        }
        self.assertEqual(
            planned_by_shard,
            {
                "whatfix": 564,
                "colektia": 564,
                "mudflap": 284,
                "trace_system": 544,
                "whatfix_graphs": 480,
                "colektia_graphs": 452,
            },
        )
        self.assertEqual(sum(planned_by_shard.values()), 2888)
        self.assertEqual(set(planned_by_shard), set(qualify.QUALIFIER_SHARDS))
        self.assertGreater(qualify.MAX_REQUESTS, max(planned_by_shard.values()))
        self.assertEqual(
            qualify.MAX_REQUESTS - max(planned_by_shard.values()),
            36,
        )
        self.assertLess(
            max(planned_by_shard.values()) * qualify.SUPERVISOR_WALL_SECONDS,
            qualify.QUALIFIER_WALL_SECONDS,
        )
        self.assertLess(qualify.QUALIFIER_WALL_SECONDS, 5400)

    def test_new_relational_profiles_use_one_page_except_365d(self):
        for mode in (
            "f5.eval_present",
            "f5.eval_absent",
            "f5.eval_exact",
            "f6.annotation_present",
            "f6.annotation_absent",
            "f6.annotation_exact",
            "f7.custom_eval_annotation",
        ):
            with self.subTest(mode=mode):
                self.assertFalse(
                    qualify._full_list_protocol_required(
                        mode=mode,
                        density="dense",
                        window_name="180d",
                    )
                )
                self.assertTrue(
                    qualify._full_list_protocol_required(
                        mode=mode,
                        density="dense",
                        window_name="365d",
                    )
                )
        for mode in ("default", "dense"):
            self.assertTrue(
                qualify._full_list_protocol_required(
                    mode=mode,
                    density="dense",
                    window_name="30m",
                )
            )
        for mode in qualify.SYSTEM_MATRIX_PROFILE_MODES:
            with self.subTest(mode=mode):
                self.assertTrue(
                    qualify._full_list_protocol_required(
                        mode=mode,
                        density="dense",
                        window_name="30m",
                    )
                )

    def test_live_graph_lanes_cover_the_full_filter_matrix(self):
        graph_source = inspect.getsource(qualify._qualify_graph_matrix)
        run_source = inspect.getsource(qualify._run)
        self.assertIn("profiles = _matrix_filter_profiles(target)", graph_source)
        self.assertIn("filters=profile_filters", graph_source)
        self.assertIn("graph_target_name = GRAPH_SHARD_TARGETS.get(shard)", run_source)
        self.assertIn("_qualify_graph_matrix(", run_source)
        self.assertEqual(
            qualify.GRAPH_SHARD_TARGETS,
            {
                "whatfix_graphs": "whatfix",
                "colektia_graphs": "colektia",
            },
        )
        self.assertEqual(len(qualify._graph_profile_names("whatfix")), 11)
        self.assertEqual(len(qualify._graph_profile_names("colektia")), 11)
        self.assertIn("dense", qualify._graph_profile_names("whatfix"))
        self.assertIn("sparse", qualify._graph_profile_names("colektia"))
        self.assertNotIn("users_graph", qualify.ROUTES)
        self.assertIn("dashboard_query", qualify.ROUTES)
        self.assertIn("filters", inspect.signature(qualify._qualify_graph).parameters)
        self.assertIn(
            "filters",
            inspect.signature(qualify._qualify_dashboard_query).parameters,
        )

    def test_dense_and_sparse_graph_shards_execute_every_matrix_cell(self):
        frozen_end = datetime(2026, 8, 15, tzinfo=UTC)

        def run_lane(lane, operation, *, required=True):
            evidence = operation()
            return {
                "lane": lane,
                "required": required,
                "qualified": evidence.get("qualified") is True,
                "evidence": evidence,
            }

        def graph_qualifier(calls):
            def qualify_graph(_client, **kwargs):
                calls.append(kwargs)
                return {"qualified": True, "positive": True}

            return qualify_graph

        for target_name in ("whatfix", "colektia"):
            graph_calls = []
            dashboard_calls = []

            with (
                mock.patch.object(
                    qualify,
                    "DirectDRFClient",
                    return_value=object(),
                ),
                mock.patch.object(qualify, "_run_lane", side_effect=run_lane),
                mock.patch.object(
                    qualify,
                    "_qualify_graph",
                    side_effect=graph_qualifier(graph_calls),
                ),
                mock.patch.object(
                    qualify,
                    "_qualify_dashboard_query",
                    side_effect=graph_qualifier(dashboard_calls),
                ),
            ):
                result = qualify._qualify_graph_matrix(
                    sample_target(target_name),
                    end=frozen_end,
                )

            expected_lanes = {
                f"graph.{target_name}.{window}.{kind}.{profile}"
                for window, _duration in qualify.WINDOWS
                for kind in ("trace", "span", "session", "dashboard")
                for profile in qualify._graph_profile_names(target_name)
            }
            self.assertTrue(result["qualified"])
            self.assertEqual(result["lane_count"], 396)
            self.assertEqual(len(graph_calls), 297)
            self.assertEqual(len(dashboard_calls), 99)
            self.assertEqual(
                {call["lane"] for call in [*graph_calls, *dashboard_calls]},
                expected_lanes,
            )

    def test_live_entrypoint_requires_one_named_shard_and_common_run_identity(self):
        frozen_end = (
            datetime.now(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        with mock.patch.dict(
            os.environ,
            {
                "QUALIFIER_SHARD": "trace_system",
                "QUALIFIER_RUN_ID": "run-20260814-a",
                "QUALIFIER_END_UTC": frozen_end,
            },
            clear=False,
        ):
            self.assertEqual(qualify._qualifier_shard(), "trace_system")
            run_id, end = qualify._qualifier_run_identity()
            self.assertEqual(run_id, "run-20260814-a")
            self.assertEqual(end.strftime("%Y-%m-%dT%H:%M:%SZ"), frozen_end)
        with mock.patch.dict(
            os.environ,
            {"QUALIFIER_SHARD": "all"},
            clear=False,
        ):
            with self.assertRaises(safety.SafetyViolation):
                qualify._qualifier_shard()

        sequential_end = (
            (datetime.now(UTC) - timedelta(hours=9))
            .replace(microsecond=0)
            .strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        with mock.patch.dict(
            os.environ,
            {
                "QUALIFIER_RUN_ID": "run-sequential",
                "QUALIFIER_END_UTC": sequential_end,
            },
            clear=False,
        ):
            self.assertEqual(qualify._qualifier_run_identity()[0], "run-sequential")

        stale_end = (
            (datetime.now(UTC) - timedelta(hours=11))
            .replace(microsecond=0)
            .strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        with mock.patch.dict(
            os.environ,
            {"QUALIFIER_RUN_ID": "run-stale", "QUALIFIER_END_UTC": stale_end},
            clear=False,
        ):
            with self.assertRaises(safety.SafetyViolation):
                qualify._qualifier_run_identity()

    def test_shard_results_merge_only_with_exact_common_binding(self):
        def payload(shard):
            target_names = qualify.SHARD_TARGET_NAMES[shard]
            partition = qualify.SHARD_PROFILE_PARTITIONS[shard]
            binding_by_target = {
                "whatfix": "d" * 64,
                "colektia": "e" * 64,
                "mudflap": "f" * 64,
            }
            row = {
                "schema": safety.SCHEMA,
                "qualified": True,
                "exit_code": 0,
                "run_id": "run-a",
                "frozen_end": "2026-08-14T12:00:00+00:00",
                "shard": shard,
                "shard_index": qualify.QUALIFIER_SHARDS.index(shard),
                "shard_count": len(qualify.QUALIFIER_SHARDS),
                "targets": {
                    target_name: {
                        "qualified": True,
                        "profile_partition": partition,
                        "target_profile_binding": {
                            "binding_sha256": binding_by_target[target_name]
                        },
                    }
                    for target_name in target_names
                },
                "source_identity": {
                    "base_commit": safety.BASE_COMMIT,
                    "derived_image_digest": "image@sha256:" + "a" * 64,
                    "source_manifest_sha256": "b" * 64,
                    "qualifier_sha256": "c" * 64,
                },
            }
            graph_target = qualify.GRAPH_SHARD_TARGETS.get(shard)
            if graph_target is not None:
                graph_kinds = ("trace", "span", "session", "dashboard")
                expected_population_profiles = sorted(
                    f"{kind}.{profile}"
                    for kind in graph_kinds
                    for profile in qualify._graph_profile_names(graph_target)
                )
                row["ancillary"] = {
                    "rollup_safe_graphs": {
                        "qualified": True,
                        "target_name": graph_target,
                        "profile_density": qualify.TARGETS[graph_target]["density"],
                        "windows": [name for name, _duration in qualify.WINDOWS],
                        "kinds": list(graph_kinds),
                        "profiles": list(qualify._graph_profile_names(graph_target)),
                        "lane_count": len(qualify.WINDOWS) * len(graph_kinds) * 11,
                        "failed_lanes": [],
                        "long_window_population": {
                            "qualified": True,
                            "expected_profiles": expected_population_profiles,
                            "missing_profiles": [],
                        },
                    }
                }
            if shard == "whatfix_graphs":
                dataset_binding = "1" * 64
                dataset_property_digest = "2" * 64
                row["ancillary"].update(
                    {
                        "dataset_exact": {
                            "qualified": True,
                            "evidence": {
                                "dataset_representative_binding_sha256": (
                                    dataset_binding
                                ),
                                "dataset_column_property_id_digest": (
                                    dataset_property_digest
                                ),
                            },
                        },
                        "metrics_catalog": {
                            "qualified": True,
                            "evidence": {
                                "dataset_column_definition_proven": True,
                                "selected_property_kind": "dataset_column",
                                "selected_property_id_digest": (
                                    dataset_property_digest
                                ),
                                "dataset_representative_binding_sha256": (
                                    dataset_binding
                                ),
                            },
                        },
                        "model_values": {"qualified": True, "evidence": {}},
                        "simulation_previews": {
                            "qualified": True,
                            "evidence": {},
                        },
                    }
                )
            return row

        payloads = [payload(shard) for shard in qualify.QUALIFIER_SHARDS]
        merged = qualify.validate_shard_result_set(payloads)
        self.assertTrue(merged["qualified"])
        self.assertEqual(merged["shards"], list(qualify.QUALIFIER_SHARDS))
        payloads[-1]["run_id"] = "other-run"
        with self.assertRaises(qualify.QualificationFailure):
            qualify.validate_shard_result_set(payloads)
        payloads = [payload(shard) for shard in qualify.QUALIFIER_SHARDS]
        system_payload = next(
            item for item in payloads if item["shard"] == "trace_system"
        )
        system_payload["targets"]["colektia"]["target_profile_binding"][
            "binding_sha256"
        ] = "0" * 64
        with self.assertRaises(qualify.QualificationFailure):
            qualify.validate_shard_result_set(payloads)
        payloads = [payload(shard) for shard in qualify.QUALIFIER_SHARDS]
        whatfix_graphs = next(
            item for item in payloads if item["shard"] == "whatfix_graphs"
        )
        whatfix_graphs["targets"]["whatfix"]["target_profile_binding"][
            "binding_sha256"
        ] = "0" * 64
        with self.assertRaises(qualify.QualificationFailure):
            qualify.validate_shard_result_set(payloads)

        payloads = [payload(shard) for shard in qualify.QUALIFIER_SHARDS]
        colektia_graphs = next(
            item for item in payloads if item["shard"] == "colektia_graphs"
        )
        colektia_graphs["ancillary"]["rollup_safe_graphs"]["lane_count"] -= 1
        with self.assertRaisesRegex(
            qualify.QualificationFailure,
            "graph matrix proof",
        ):
            qualify.validate_shard_result_set(payloads)

        payloads = [payload(shard) for shard in qualify.QUALIFIER_SHARDS]
        whatfix_graphs = next(
            item for item in payloads if item["shard"] == "whatfix_graphs"
        )
        whatfix_graphs["ancillary"]["metrics_catalog"]["evidence"][
            "dataset_representative_binding_sha256"
        ] = "2" * 64
        with self.assertRaisesRegex(
            qualify.QualificationFailure,
            "dataset-column representative",
        ):
            qualify.validate_shard_result_set(payloads)

    def test_server_locked_clickhouse_client_keeps_credential_settings(self):
        positional, keyword = qualify._ch_call_settings(
            (None, False, None, "query-id", {"custom": 1}),
            {"settings": {"other": 2}},
            settings_index=4,
            server_enforced=True,
        )
        self.assertEqual(positional[4], {"custom": 1})
        self.assertEqual(keyword["settings"], {"other": 2})

        _positional, bounded_keyword = qualify._ch_call_settings(
            (),
            {"settings": {"readonly": 0, "max_execution_time": 99}},
            settings_index=4,
            server_enforced=False,
        )
        self.assertEqual(bounded_keyword["settings"]["readonly"], 2)
        self.assertEqual(
            bounded_keyword["settings"]["max_execution_time"],
            safety.CH_TIMEOUT_SECONDS,
        )

    def test_cumulative_wall_stops_new_requests_before_job_deadline(self):
        previous = qualify._qualifier_deadline_monotonic
        try:
            qualify._qualifier_deadline_monotonic = 0.0
            with self.assertRaises(safety.SafetyViolation):
                qualify._assert_budget()
        finally:
            qualify._qualifier_deadline_monotonic = previous

    def test_supervised_child_renders_and_returns_strict_public_json(self):
        response = qualify._supervise_drf_response(
            lambda: RenderableResponse(
                {"status": True, "result": {"query_complete": True}}
            ),
            wall_seconds=0.5,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["result"]["query_complete"])
        self.assertGreater(response.rendered_bytes, 0)
        self.assertRegex(response.rendered_sha256, r"^[0-9a-f]{64}$")

    def test_direct_client_routes_every_callback_through_the_supervisor(self):
        source = inspect.getsource(qualify.DirectDRFClient.call)
        self.assertIn("connections.close_all()", source)
        self.assertIn("response = _supervise_drf_response(", source)
        self.assertIn("invoke_callback,", source)
        self.assertIn("wall_seconds=min(SUPERVISOR_WALL_SECONDS, remaining)", source)
        self.assertNotIn("response = callback(", source)
        child_start = source.index("def invoke_callback")
        supervisor_call = source.index("response = _supervise_drf_response(")
        # Membership/project authorization, route resolution, request
        # construction, and workspace stamping must all consume the same
        # supervised wall as the view and response renderer.  Moving any of
        # these SELECT-bearing checks before the child recreates a >19s path
        # from two independent PostgreSQL statement grants.
        for marker in (
            "resolve(path)",
            "WorkspaceMembership.no_workspace_objects.filter",
            "Project.no_workspace_objects.filter",
            "APIKeyAuthentication()._set_workspace_context",
            "force_authenticate(request",
        ):
            self.assertGreater(source.index(marker), child_start)
            self.assertLess(source.index(marker), supervisor_call)
        self.assertLess(source.index("started = time.monotonic()"), child_start)
        self.assertLess(source.index("connections.close_all()"), supervisor_call)
        self.assertIn("public request completed after the 9.8-second wall", source)

    def test_direct_client_supervises_authorization_selects_not_only_the_view(self):
        preflight_calls = []

        class Manager:
            def filter(self, *args, **kwargs):
                return self

            def exists(self):
                preflight_calls.append("select")
                return True

        class Query:
            def __init__(self, **_kwargs):
                pass

            def __or__(self, _other):
                return self

        class RequestFactory:
            def get(self, path, query, **headers):
                return SimpleNamespace(path=path, query=query, headers=headers)

        class Authentication:
            def _set_workspace_context(self, request, user):
                request.user = user

        user = SimpleNamespace(
            is_active=True,
            can_access_organization=lambda _organization: True,
            can_access_workspace=lambda _workspace: True,
            id="user-1",
        )
        organization = SimpleNamespace(id="org-1")
        workspace = SimpleNamespace(id="workspace-1")
        project = SimpleNamespace(
            id="project-1",
            organization=organization,
            organization_id=organization.id,
        )
        principal = qualify.Principal(user=user, workspace=workspace)

        def callback(_request, *_args, **_kwargs):
            return RenderableResponse({"status": True, "result": {}})

        callback.actions = {"get": "get"}
        match = SimpleNamespace(func=callback, args=(), kwargs={})
        fake_modules = {
            "django.db": SimpleNamespace(
                connections=SimpleNamespace(close_all=lambda: None)
            ),
            "django.db.models": SimpleNamespace(Q=Query),
            "django.urls": SimpleNamespace(
                Resolver404=type("Resolver404", (Exception,), {}),
                resolve=lambda _path: match,
            ),
            "rest_framework.test": SimpleNamespace(
                APIRequestFactory=RequestFactory,
                force_authenticate=lambda request, user: setattr(request, "user", user),
            ),
            "accounts.authentication": SimpleNamespace(
                APIKeyAuthentication=Authentication
            ),
            "accounts.models.workspace": SimpleNamespace(
                WorkspaceMembership=SimpleNamespace(no_workspace_objects=Manager())
            ),
            "tracer.models.project": SimpleNamespace(
                Project=SimpleNamespace(no_workspace_objects=Manager())
            ),
        }

        def supervisor(operation, **_kwargs):
            self.assertEqual(preflight_calls, [])
            rendered = qualify._render_and_encode_response(operation())
            return qualify.EncodedDRFResponse(
                status_code=rendered["status_code"],
                data={"status": True, "result": {}},
                rendered_sha256=rendered["rendered_sha256"],
                rendered_bytes=rendered["rendered_bytes"],
            )

        before_records = len(qualify._request_records)
        with (
            mock.patch.dict(sys.modules, fake_modules),
            mock.patch.object(qualify, "_supervise_drf_response", supervisor),
            mock.patch.object(
                qualify,
                "_tenant_context",
                return_value=nullcontext(),
            ),
            mock.patch.object(qualify, "_assert_budget"),
        ):
            response = qualify.DirectDRFClient(project, principal).call(
                "property_keys",
                lane="request.authorization-wall",
                query={"project_id": project.id},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(preflight_calls, ["select", "select"])
        self.assertEqual(len(qualify._request_records), before_records + 1)

    def test_supervised_child_rejects_unrenderable_response(self):
        with self.assertRaisesRegex(
            qualify.QualificationFailure,
            "unrenderable response",
        ):
            qualify._supervise_drf_response(
                lambda: Response({"status": True}),
                wall_seconds=0.5,
            )

    def test_select_only_exact_seam_runs_reviewed_worker_readers_without_cache(self):
        calls = []

        def load_exact_payload(namespace, identity):
            calls.append((namespace, identity))
            return {
                "metric_name": "latency",
                "data": [{"timestamp": "2026-08-15T00:00:00Z", "value": 1}],
                "query_complete": True,
                "query_status": "complete",
                "query_sampled": False,
            }

        fake_task_module = SimpleNamespace(_load_exact_payload=load_exact_payload)
        identity = {"project_id": "project-1", "filters": []}
        with mock.patch.dict(
            sys.modules,
            {"tracer.tasks.exact_aggregation": fake_task_module},
        ):
            result = qualify._select_only_exact_snapshot(
                "observe-system-graph",
                identity,
                refresh=True,
                pending_payload={"query_status": "pending"},
            )
            dashboard_result = qualify._select_only_exact_snapshot(
                "dashboard-query",
                identity,
                refresh=False,
                pending_payload={"query_status": "pending"},
            )

        self.assertTrue(result["query_complete"])
        self.assertTrue(dashboard_result["query_complete"])
        self.assertEqual(
            calls,
            [
                ("observe-system-graph", identity),
                ("dashboard-query", identity),
            ],
        )
        self.assertIsNot(calls[0][1], identity)
        self.assertIsNot(calls[1][1], identity)
        with self.assertRaises(safety.SafetyViolation):
            qualify._select_only_exact_snapshot(
                "eval-usage",
                identity,
                refresh=False,
                pending_payload={},
            )

    def test_dispatch_tripwire_patches_every_reviewed_exact_cache_call_site(self):
        source = inspect.getsource(qualify._install_dispatch_tripwires)
        self.assertIn(
            "graph_dispatch.read_or_schedule_exact_snapshot =",
            source,
        )
        self.assertIn(
            "session_graph.read_or_schedule_exact_snapshot =",
            source,
        )
        self.assertIn(
            "dashboard.read_or_schedule_exact_snapshot =",
            source,
        )

    def test_supervised_parent_kills_a_view_that_swallows_its_alarm(self):
        def swallow_alarm():
            qualify._inc("ch_read")
            until = time.monotonic() + 0.5
            while time.monotonic() < until:
                try:
                    time.sleep(0.02)
                except BaseException:
                    # Simulates the broad view handlers that can swallow the
                    # child's SIGALRM exception and continue doing work.
                    continue
            return RenderableResponse({"status": True})

        started = time.monotonic()
        before = qualify._snapshot_counts()["ch_read"]
        with self.assertRaises(qualify.RequestDeadlineExceeded):
            qualify._supervise_drf_response(
                swallow_alarm,
                wall_seconds=0.05,
            )
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(qualify._snapshot_counts()["ch_read"], before + 1)

    def test_supervisor_rejects_a_child_that_exits_without_a_response(self):
        def abrupt_exit():
            os._exit(7)

        started = time.monotonic()
        with self.assertRaisesRegex(
            qualify.QualificationFailure,
            "exited without a response",
        ):
            qualify._supervise_drf_response(abrupt_exit, wall_seconds=0.5)
        self.assertLess(time.monotonic() - started, 0.5)

    def test_supervisor_fails_closed_without_fork(self):
        with mock.patch.object(
            qualify.multiprocessing,
            "get_all_start_methods",
            return_value=["spawn"],
        ):
            with self.assertRaisesRegex(safety.SafetyViolation, "fork-capable"):
                qualify._supervise_drf_response(
                    lambda: RenderableResponse({"status": True}),
                    wall_seconds=0.5,
                )

    def test_filter_shapes_are_namespaced_and_typed(self):
        start = datetime(2026, 1, 1, tzinfo=UTC)
        end = datetime(2026, 1, 2, tzinfo=UTC)
        time_filter = qualify._time_filter(start, end)
        self.assertEqual(time_filter["filter_config"]["col_type"], "SYSTEM_METRIC")
        self.assertEqual(
            qualify._custom_filter("customer.key", "customer-1", "string"),
            {
                "column_id": "customer.key",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": "in",
                    "filter_value": ["customer-1"],
                    "attribute_value_types": ["string"],
                },
            },
        )
        for value, value_type in ((7.5, "number"), (False, "boolean")):
            with self.subTest(value_type=value_type):
                custom = qualify._custom_filter("customer.key", value, value_type)
                self.assertEqual(custom["filter_config"]["col_type"], "SPAN_ATTRIBUTE")
                self.assertEqual(custom["filter_config"]["filter_type"], value_type)
                self.assertEqual(custom["filter_config"]["filter_op"], "equals")
                self.assertEqual(custom["filter_config"]["filter_value"], value)
                self.assertNotIn("attribute_value_types", custom["filter_config"])
        system = qualify._system_model_filter("gpt-4.1", "string")
        self.assertEqual(system["property_id"], "system_attribute:traces:model")
        self.assertEqual(system["source"], "traces")
        self.assertEqual(
            system["filter_config"],
            {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "text",
                "filter_op": "in",
                "filter_value": ["gpt-4.1"],
            },
        )
        self.assertEqual(
            qualify._presence_filter("has_eval", False)["filter_config"],
            {
                "filter_type": "boolean",
                "filter_op": "equals",
                "filter_value": False,
            },
        )
        eval_profile = qualify.RelationalFilterProfile(
            property_id="eval_config:eval-1",
            column_id="eval-1",
            col_type="EVAL_METRIC",
            filter_type="text",
            filter_op="in",
            filter_value=("Passed",),
            output_type="PASS_FAIL",
        )
        self.assertEqual(
            qualify._relational_filter(eval_profile),
            {
                "column_id": "eval-1",
                "property_id": "eval_config:eval-1",
                "output_type": "PASS_FAIL",
                "filter_config": {
                    "col_type": "EVAL_METRIC",
                    "filter_type": "text",
                    "filter_op": "in",
                    "filter_value": ["Passed"],
                },
            },
        )

    def test_custom_filter_shapes_obey_the_public_span_attribute_contract(self):
        contract = json.loads(
            (PACKAGE_DIR.parents[2] / "api_contracts/filter_contract.json").read_text(
                encoding="utf-8"
            )
        )
        allowed = contract["operators"]["spanAttributeAllowed"]
        list_ops = set(contract["operators"]["list"])
        cases = (
            ("value", "string", "text"),
            (3.25, "number", "number"),
            (True, "boolean", "boolean"),
        )
        for value, value_type, filter_type in cases:
            with self.subTest(value_type=value_type):
                config = qualify._custom_filter("customer.key", value, value_type)[
                    "filter_config"
                ]
                self.assertIn(config["filter_op"], allowed[filter_type])
                if config["filter_op"] in list_ops:
                    self.assertEqual(config["filter_value"], [value])
                    self.assertEqual(config["attribute_value_types"], [value_type])
                else:
                    self.assertEqual(config["filter_value"], value)
                    self.assertNotIn("attribute_value_types", config)

    def test_custom_filter_rejects_type_mismatches_and_nonfinite_numbers(self):
        invalid = (
            (1, "string"),
            (True, "number"),
            ("1", "number"),
            (float("inf"), "number"),
            (float("-inf"), "number"),
            (float("nan"), "number"),
            (10**10_000, "number"),
            (1, "boolean"),
            ("true", "boolean"),
            ("value", "array"),
        )
        for value, value_type in invalid:
            with self.subTest(value_type=value_type, value_class=type(value).__name__):
                with self.assertRaises(qualify.PopulationGap):
                    qualify._custom_filter("customer.key", value, value_type)

    def test_matrix_custom_profiles_keep_each_scalar_shape_canonical(self):
        cases = (
            ("value", "string", "in", ["value"]),
            (3.25, "number", "equals", 3.25),
            (True, "boolean", "equals", True),
        )
        for value, value_type, operator, expected_value in cases:
            with self.subTest(value_type=value_type):
                base = sample_target()
                target = qualify.Target(
                    name=base.name,
                    project=base.project,
                    principal=base.principal,
                    key=base.key,
                    value=value,
                    value_type=value_type,
                    system_value=base.system_value,
                    system_value_type=base.system_value_type,
                    eval_profile=base.eval_profile,
                    annotation_profile=base.annotation_profile,
                )
                profiles = qualify._matrix_filter_profiles(target, partition="all")
                custom = dict(profiles)[str(qualify.TARGETS[target.name]["density"])][0]
                config = custom["filter_config"]
                self.assertEqual(config["filter_op"], operator)
                self.assertEqual(config["filter_value"], expected_value)
                self.assertEqual(
                    "attribute_value_types" in config,
                    value_type == "string",
                )

    def test_catalog_profile_discovery_uses_config_and_label_registry_ids(self):
        client = QueueClient(
            [
                metrics_response(
                    [
                        {
                            "name": "eval-1",
                            "property_id": "eval_config:eval-1",
                            "property_kind": "eval_config",
                            "category": "eval_metric",
                            "output_type": "PASS_FAIL",
                            "choices": ["Passed", "Failed"],
                        }
                    ],
                    has_more=False,
                    page_size=qualify.METRIC_CATALOG_DISCOVERY_PAGE_SIZE,
                ),
                filter_values_response([{"value": "Failed", "label": "Failed"}]),
                metrics_response(
                    [
                        {
                            "name": "label-1",
                            "property_id": "annotation:label-1",
                            "property_kind": "annotation",
                            "category": "annotation_metric",
                            "output_type": "categorical",
                        }
                    ],
                    has_more=False,
                    page_size=qualify.METRIC_CATALOG_DISCOVERY_PAGE_SIZE,
                ),
                filter_values_response(
                    [{"value": "stored-choice", "label": "stored-choice"}]
                ),
            ]
        )
        eval_profile, annotation_profile = qualify._discover_relational_profiles(
            client, lane="discover"
        )
        self.assertEqual(eval_profile.property_id, "eval_config:eval-1")
        self.assertEqual(eval_profile.filter_value, ("Failed",))
        self.assertEqual(annotation_profile.property_id, "annotation:label-1")
        self.assertEqual(annotation_profile.filter_op, "in")
        self.assertEqual(annotation_profile.filter_value, ("stored-choice",))
        self.assertEqual(len(client.calls), 4)
        self.assertEqual(client.calls[0]["query"]["cursor_mode"], "true")
        self.assertEqual(client.calls[0]["query"]["per_eval_config"], "true")
        self.assertEqual(client.calls[0]["query"]["category"], "eval_metric")
        self.assertNotIn("page", client.calls[0]["query"])
        self.assertEqual(client.calls[1]["query"]["property_id"], "eval_config:eval-1")
        self.assertEqual(client.calls[2]["query"]["category"], "annotation_metric")
        self.assertEqual(client.calls[3]["query"]["property_id"], "annotation:label-1")

    def test_score_eval_requires_and_accepts_a_public_numeric_value(self):
        candidate = qualify._eval_profile_from_metric(
            {
                "name": "eval-1",
                "property_id": "eval_config:eval-1",
                "property_kind": "eval_config",
                "output_type": "SCORE",
            }
        )
        self.assertIsNotNone(candidate)
        self.assertIsNone(candidate.filter_value)
        observed = qualify._profile_with_public_value(candidate, "62.5")
        self.assertIsNotNone(observed)
        self.assertEqual(observed.filter_type, "number")
        self.assertEqual(observed.filter_value, 62.5)
        self.assertIsNone(qualify._profile_with_public_value(candidate, "not-a-score"))

    def test_catalog_candidate_order_is_property_id_deterministic(self):
        client = QueueClient(
            [
                metrics_response(
                    [
                        {
                            "name": "z",
                            "property_id": "eval_config:z",
                            "property_kind": "eval_config",
                            "category": "eval_metric",
                            "output_type": "PASS_FAIL",
                        },
                        {
                            "name": "a",
                            "property_id": "eval_config:a",
                            "property_kind": "eval_config",
                            "category": "eval_metric",
                            "output_type": "PASS_FAIL",
                        },
                    ],
                    has_more=False,
                    page_size=qualify.METRIC_CATALOG_DISCOVERY_PAGE_SIZE,
                ),
                filter_values_response([{"value": "Passed", "label": "Passed"}]),
            ]
        )
        profile = qualify._discover_catalog_profile(
            client,
            category="eval_metric",
            lane="sorted",
            factory=qualify._eval_profile_from_metric,
        )
        self.assertEqual(profile.property_id, "eval_config:a")
        self.assertEqual(client.calls[1]["query"]["property_id"], "eval_config:a")

    def test_catalog_profile_discovery_advances_the_activated_cursor(self):
        client = QueueClient(
            [
                metrics_response(
                    [
                        {
                            "name": "unsupported",
                            "property_id": "eval_config:unsupported",
                            "property_kind": "eval_config",
                            "category": "eval_metric",
                            "output_type": "TEXT",
                        }
                    ],
                    has_more=True,
                    cursor="catalog-cursor-1",
                    page_size=qualify.METRIC_CATALOG_DISCOVERY_PAGE_SIZE,
                ),
                metrics_response(
                    [
                        {
                            "name": "eval-2",
                            "property_id": "eval_config:eval-2",
                            "property_kind": "eval_config",
                            "category": "eval_metric",
                            "output_type": "PASS_FAIL",
                        }
                    ],
                    has_more=False,
                    page_size=qualify.METRIC_CATALOG_DISCOVERY_PAGE_SIZE,
                ),
                filter_values_response([{"value": "Passed", "label": "Passed"}]),
            ]
        )

        profile = qualify._discover_catalog_profile(
            client,
            category="eval_metric",
            lane="cursor",
            factory=qualify._eval_profile_from_metric,
        )

        self.assertEqual(profile.property_id, "eval_config:eval-2")
        self.assertNotIn("cursor", client.calls[0]["query"])
        self.assertEqual(client.calls[1]["query"]["cursor"], "catalog-cursor-1")

    def test_catalog_profile_discovery_rejects_overlapping_cursor_pages(self):
        duplicate = {
            "name": "duplicate",
            "property_id": "eval_config:duplicate",
            "property_kind": "eval_config",
            "category": "eval_metric",
            "output_type": "TEXT",
        }
        client = QueueClient(
            [
                metrics_response(
                    [duplicate],
                    has_more=True,
                    cursor="catalog-cursor-1",
                    page_size=qualify.METRIC_CATALOG_DISCOVERY_PAGE_SIZE,
                ),
                metrics_response(
                    [duplicate],
                    has_more=False,
                    page_size=qualify.METRIC_CATALOG_DISCOVERY_PAGE_SIZE,
                ),
            ]
        )

        with self.assertRaisesRegex(
            qualify.QualificationFailure, "catalog pages overlapped"
        ):
            qualify._discover_catalog_profile(
                client,
                category="eval_metric",
                lane="overlap",
                factory=qualify._eval_profile_from_metric,
            )

    def test_system_model_discovery_uses_the_namespaced_public_value(self):
        client = QueueClient(
            [filter_values_response([{"value": "gpt-4.1", "type": "string"}])]
        )
        value, value_type = qualify._discover_system_model(client, lane="system.model")
        self.assertEqual((value, value_type), ("gpt-4.1", "string"))
        self.assertEqual(
            client.calls[0]["query"]["property_id"],
            "system_attribute:traces:model",
        )

    def test_matrix_contains_f1_f4_f5_f6_and_f7_contracts(self):
        target = sample_target()
        profiles = dict(qualify._matrix_filter_profiles(target))
        self.assertEqual(
            set(profiles),
            {
                "default",
                "dense",
                "f1.system",
                "f4.system_custom",
                "f5.eval_present",
                "f5.eval_absent",
                "f5.eval_exact",
                "f6.annotation_present",
                "f6.annotation_absent",
                "f6.annotation_exact",
                "f7.custom_eval_annotation",
            },
        )
        self.assertEqual(
            profiles["f1.system"],
            [qualify._system_model_filter("gpt-4.1", "string")],
        )
        self.assertEqual(
            [
                item["filter_config"]["col_type"]
                for item in profiles["f4.system_custom"]
            ],
            ["SYSTEM_METRIC", "SPAN_ATTRIBUTE"],
        )
        self.assertEqual(
            set(dict(qualify._matrix_filter_profiles(target, partition="system"))),
            qualify.SYSTEM_MATRIX_PROFILE_MODES,
        )
        self.assertTrue(
            qualify.SYSTEM_MATRIX_PROFILE_MODES <= qualify.EXACT_VALUE_PROFILE_MODES
        )
        self.assertEqual(
            profiles["f6.annotation_exact"][0]["filter_config"],
            {
                "col_type": "ANNOTATION",
                "filter_type": "categorical",
                "filter_op": "in",
                "filter_value": ["approved"],
            },
        )
        f7 = profiles["f7.custom_eval_annotation"]
        self.assertEqual(len(f7), 4)
        self.assertEqual(
            [item.get("property_id") for item in f7 if item.get("property_id")],
            ["eval_config:eval-1", "annotation:label-1"],
        )
        self.assertTrue(any(item.get("column_id") == "has_annotation" for item in f7))

    def test_sampled_or_incomplete_response_fails(self):
        for payload in (
            {"query_complete": False, "query_status": "degraded"},
            {"query_complete": False, "query_status": "sampled"},
            {"query_complete": False, "query_status": "pending"},
            {"query_complete": False, "query_status": "failed"},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(qualify.QualificationFailure):
                    qualify._require_status(
                        Response({"status": True, "result": payload}), "lane"
                    )

    def test_list_cursor_repeat_and_read_more(self):
        start = datetime(2026, 1, 1, tzinfo=UTC)
        end = datetime(2026, 1, 2, tzinfo=UTC)
        client = QueueClient(
            [
                list_response([trace_row("a")], has_more=True, cursor="signed-1"),
                list_response([trace_row("a")], has_more=True, cursor="signed-1"),
                list_response([trace_row("b")], has_more=False),
                list_response([trace_row("b")], has_more=False),
            ]
        )
        result = qualify._qualify_list_protocol(
            client,
            kind="trace",
            filters=[qualify._time_filter(start, end)],
            lane="trace.matrix",
        )
        self.assertTrue(result["qualified"])
        self.assertTrue(result["positive"])
        self.assertTrue(result["continuation_exercised"])
        self.assertTrue(result["no_page_overlap"])
        self.assertEqual(len(client.calls), 4)
        self.assertEqual(client.calls[2]["query"]["cursor"], "signed-1")
        self.assertNotIn("page_number", client.calls[2]["query"])
        self.assertEqual(client.calls[3]["query"]["cursor"], "signed-1")
        self.assertNotIn("page_number", client.calls[3]["query"])

    def test_exact_empty_cursor_checkpoints_are_supported_for_entity_lists(self):
        rows = {
            "trace": trace_row("trace-p2"),
            "voice": trace_row("voice-p2"),
            "span": {
                "project_id": "project-1",
                "trace_id": "trace-1",
                "span_id": "span-p2",
                "start_time": "2026-01-01T12:00:00Z",
            },
        }
        for kind, row in rows.items():
            with self.subTest(kind=kind, checkpoint="nonterminal_to_positive"):
                client = QueueClient(
                    [
                        list_response(
                            [], has_more=True, cursor="checkpoint-a", kind=kind
                        ),
                        list_response(
                            [], has_more=True, cursor="checkpoint-b", kind=kind
                        ),
                        list_response([row], kind=kind),
                        list_response([row], kind=kind),
                    ]
                )
                result = qualify._qualify_list_protocol(
                    client,
                    kind=kind,
                    filters=[],
                    lane=f"{kind}.empty-checkpoint",
                )
                self.assertTrue(result["positive"])
                self.assertEqual(result["p1_rows"], 0)
                self.assertEqual(result["p2_rows"], 1)
                self.assertTrue(result["continuation_exercised"])
            with self.subTest(kind=kind, checkpoint="terminal_empty"):
                client = QueueClient(
                    [
                        list_response(
                            [], has_more=True, cursor="checkpoint-a", kind=kind
                        ),
                        list_response(
                            [], has_more=True, cursor="checkpoint-b", kind=kind
                        ),
                        list_response([], kind=kind),
                        list_response([], kind=kind),
                    ]
                )
                result = qualify._qualify_list_protocol(
                    client,
                    kind=kind,
                    filters=[],
                    lane=f"{kind}.terminal-checkpoint",
                )
                self.assertFalse(result["positive"])
                self.assertEqual(result["p1_rows"], 0)
                self.assertEqual(result["p2_rows"], 0)
                self.assertTrue(result["continuation_exercised"])

    def test_empty_cursor_checkpoints_remain_strictly_scoped_and_bound(self):
        for kind in ("session", "users"):
            with self.subTest(kind=kind):
                client = QueueClient(
                    [
                        list_response([], has_more=True, cursor="one", kind=kind),
                        list_response([], has_more=True, cursor="two", kind=kind),
                    ]
                )
                with self.assertRaisesRegex(
                    qualify.QualificationFailure,
                    "continuation from an empty page",
                ):
                    qualify._qualify_list_protocol(
                        client,
                        kind=kind,
                        filters=[],
                        lane=f"{kind}.empty-checkpoint",
                    )

        missing_cursor = QueueClient(
            [list_response([], has_more=True, cursor=None, kind="voice")]
        )
        with self.assertRaisesRegex(
            qualify.QualificationFailure,
            "omitted a bounded continuation cursor",
        ):
            qualify._qualify_list_protocol(
                missing_cursor,
                kind="voice",
                filters=[],
                lane="voice.missing-checkpoint-cursor",
            )

        nonadvancing = QueueClient(
            [
                list_response([], has_more=True, cursor="one", kind="voice"),
                list_response([], has_more=True, cursor="two", kind="voice"),
                list_response([], has_more=True, cursor="one", kind="voice"),
            ]
        )
        with self.assertRaisesRegex(
            qualify.QualificationFailure,
            "second cursor did not advance",
        ):
            qualify._qualify_list_protocol(
                nonadvancing,
                kind="voice",
                filters=[],
                lane="voice.nonadvancing-checkpoint",
            )

        divergent_repeat = QueueClient(
            [
                list_response([], has_more=True, cursor="one", kind="voice"),
                list_response([], has_more=True, cursor="two", kind="voice"),
                list_response([], kind="voice"),
                list_response([], has_more=True, cursor="three", kind="voice"),
            ]
        )
        with self.assertRaisesRegex(
            qualify.QualificationFailure,
            "changed continuation semantics",
        ):
            qualify._qualify_list_protocol(
                divergent_repeat,
                kind="voice",
                filters=[],
                lane="voice.divergent-checkpoint",
            )

    def test_empty_filtered_checkpoint_still_requires_exact_attestation(self):
        filters = [qualify._custom_filter("descendant.number", 7.0, "number")]
        response = list_response(
            [],
            has_more=True,
            cursor="checkpoint",
            kind="voice",
            filters=filters,
        )
        for key in (
            "query_applied_filter_version",
            "query_applied_filter_sha256",
            "query_applied_filter_count",
        ):
            response.data["result"].pop(key)
        with self.assertRaisesRegex(
            qualify.QualificationFailure,
            "response-bound applied-filter proof",
        ):
            qualify._qualify_list_protocol(
                QueueClient([response]),
                kind="voice",
                filters=filters,
                lane="voice.unattested-checkpoint",
            )

    def test_empty_continuation_checkpoint_requires_exact_proof(self):
        for has_more, cursor in ((False, None), (True, "next-checkpoint")):
            with self.subTest(has_more=has_more):
                client = QueueClient(
                    [
                        list_response([], has_more=True, cursor="one", kind="voice"),
                        list_response([], has_more=True, cursor="two", kind="voice"),
                        list_response(
                            [],
                            has_more=has_more,
                            cursor=cursor,
                            query_exact=False,
                            kind="voice",
                        ),
                    ]
                )
                with self.assertRaisesRegex(
                    qualify.QualificationFailure,
                    "continuation returned an empty page without an exact proof",
                ):
                    qualify._qualify_list_protocol(
                        client,
                        kind="voice",
                        filters=[],
                        lane="voice.inexact-continuation-checkpoint",
                    )

    def test_list_repeat_crosses_issue_second_and_resumes_both_cursors(self):
        client = QueueClient(
            [
                list_response(
                    [trace_row("a")],
                    has_more=True,
                    cursor="list-state.1000.sig",
                ),
                list_response(
                    [trace_row("a")],
                    has_more=True,
                    cursor="list-state.1001.sig",
                ),
                list_response([trace_row("b")], has_more=False),
                list_response([trace_row("b")], has_more=False),
            ]
        )

        result = qualify._qualify_list_protocol(
            client,
            kind="trace",
            filters=[],
            lane="trace.timestamp-boundary",
        )

        self.assertTrue(result["p1_repeat_equal"])
        self.assertEqual(
            [call["query"].get("cursor") for call in client.calls],
            [None, None, "list-state.1000.sig", "list-state.1001.sig"],
        )

    def test_list_repeat_rejects_divergent_timestamped_continuation(self):
        client = QueueClient(
            [
                list_response(
                    [trace_row("a")],
                    has_more=True,
                    cursor="list-state.1000.sig",
                ),
                list_response(
                    [trace_row("a")],
                    has_more=True,
                    cursor="list-state.1001.sig",
                ),
                list_response([trace_row("b")], has_more=False),
                list_response([trace_row("c")], has_more=False),
            ]
        )

        with self.assertRaisesRegex(
            qualify.QualificationFailure,
            "changed continuation semantics",
        ):
            qualify._qualify_list_protocol(
                client,
                kind="trace",
                filters=[],
                lane="trace.divergent",
            )

    def test_list_cursor_rejects_overlap(self):
        client = QueueClient(
            [
                list_response([trace_row("a")], has_more=True, cursor="signed-1"),
                list_response([trace_row("a")], has_more=True, cursor="signed-1"),
                list_response([trace_row("a")], has_more=False),
            ]
        )
        with self.assertRaises(qualify.QualificationFailure):
            qualify._qualify_list_protocol(
                client, kind="trace", filters=[], lane="overlap"
            )

    def test_list_page_requires_typed_truthful_continuation_metadata(self):
        missing = list_response([trace_row("a")])
        del missing.data["result"]["metadata"]["has_more"]
        invalid_terminal = list_response([trace_row("a")])
        invalid_terminal.data["result"]["metadata"]["next_cursor"] = "stale"
        invalid_nonterminal = list_response([trace_row("a")])
        invalid_nonterminal.data["result"]["metadata"].update(
            has_more=True,
            next_cursor=None,
        )
        for response in (missing, invalid_terminal, invalid_nonterminal):
            with self.subTest(response=response):
                with self.assertRaises(qualify.QualificationFailure):
                    qualify._qualify_list_first_page(
                        QueueClient([response]),
                        kind="trace",
                        filters=[],
                        lane="continuation.truth",
                    )

    def test_relational_first_page_is_one_request_and_preserves_cursor_truth(self):
        filters = [qualify._presence_filter("has_eval", True)]
        eval_config = [
            {
                "id": "eval-1",
                "property_id": "eval_config:eval-1",
                "property_kind": "eval_config",
            }
        ]
        client = QueueClient(
            [
                list_response(
                    [trace_row("a", **{"eval-1": 100.0})],
                    has_more=True,
                    cursor="signed-1",
                    config=eval_config,
                    filters=filters,
                )
            ]
        )
        result = qualify._qualify_list_first_page(
            client,
            kind="trace",
            filters=filters,
            lane="trace.f5",
        )
        self.assertTrue(result["positive"])
        self.assertTrue(result["continuation_available"])
        self.assertFalse(result["continuation_exercised"])
        self.assertEqual(len(client.calls), 1)

    def test_list_rows_prove_project_time_and_every_conjunction_leaf(self):
        target = sample_target()
        start = datetime(2026, 1, 1, tzinfo=UTC)
        end = datetime(2026, 1, 2, tzinfo=UTC)
        filters = [
            qualify._time_filter(start, end),
            qualify._custom_filter("customer.key", "customer-1", "string"),
            qualify._relational_filter(target.eval_profile),
            qualify._presence_filter("has_annotation", True),
            qualify._relational_filter(target.annotation_profile),
        ]
        config = [
            {
                "id": "eval-1",
                "property_id": "eval_config:eval-1",
                "property_kind": "eval_config",
            },
            {
                "id": "label-1",
                "property_id": "annotation:label-1",
                "property_kind": "annotation",
            },
        ]
        row = trace_row(
            "trace-a",
            **{
                "customer.key": "customer-1",
                "eval-1": 50.0,
                "label-1": "approved",
            },
        )

        proofs = qualify._verify_list_row_semantics(
            kind="trace",
            rows=[row],
            config=config,
            project_id="project-1",
            filters=filters,
            lane="semantic.f7",
        )

        self.assertEqual(len(proofs), 1)
        self.assertRegex(proofs[0], r"^[0-9a-f]{64}$")

    def test_list_semantic_proof_fails_closed_when_public_evidence_is_missing(self):
        start = datetime(2026, 1, 1, tzinfo=UTC)
        end = datetime(2026, 1, 2, tzinfo=UTC)
        time_filter = qualify._time_filter(start, end)
        cases = (
            (
                "cross-project",
                "trace",
                [trace_row("a", project_id="other-project")],
                [],
                [time_filter],
            ),
            (
                "missing-project",
                "session",
                [{"session_id": "a", "created_at": "2026-01-01T12:00:00Z"}],
                [],
                [time_filter],
            ),
            (
                "wrong-custom-value",
                "trace",
                [trace_row("a", **{"customer.key": "wrong"})],
                [],
                [
                    time_filter,
                    qualify._custom_filter("customer.key", "customer-1", "string"),
                ],
            ),
        )
        for name, kind, rows, config, filters in cases:
            with self.subTest(name=name):
                with self.assertRaises(qualify.QualificationFailure):
                    qualify._verify_list_row_semantics(
                        kind=kind,
                        rows=rows,
                        config=config,
                        project_id="project-1",
                        filters=filters,
                        lane=name,
                    )

    def test_aggregate_score_cell_cannot_prove_a_raw_eval_filter_leaf(self):
        score_profile = qualify.RelationalFilterProfile(
            property_id="eval_config:score-1",
            column_id="score-1",
            col_type="EVAL_METRIC",
            filter_type="number",
            filter_op="equals",
            filter_value=62.5,
            output_type="SCORE",
        )
        with self.assertRaisesRegex(
            qualify.QualificationFailure,
            "raw SCORE leaf",
        ):
            qualify._verify_list_row_semantics(
                kind="trace",
                rows=[trace_row("a", **{"score-1": 62.5})],
                config=[
                    {
                        "id": "score-1",
                        "property_id": "eval_config:score-1",
                        "property_kind": "eval_config",
                    }
                ],
                project_id="project-1",
                filters=[qualify._relational_filter(score_profile)],
                lane="score.aggregate",
            )

    def test_aggregate_numeric_annotation_cannot_prove_a_raw_filter_leaf(self):
        profile = qualify.RelationalFilterProfile(
            property_id="annotation:label-1",
            column_id="label-1",
            col_type="ANNOTATION",
            filter_type="number",
            filter_op="equals",
            filter_value=2,
            output_type="numeric",
        )
        with self.assertRaisesRegex(
            qualify.QualificationFailure,
            "raw numeric annotation leaf",
        ):
            qualify._verify_list_row_semantics(
                kind="trace",
                rows=[trace_row("a", **{"label-1": {"score": 2}})],
                config=[
                    {
                        "id": "label-1",
                        "property_id": "annotation:label-1",
                        "property_kind": "annotation",
                    }
                ],
                project_id="project-1",
                filters=[qualify._relational_filter(profile)],
                lane="annotation.aggregate",
            )

    def test_response_bound_filter_attestation_proves_aggregate_raw_membership(self):
        score_profile = qualify.RelationalFilterProfile(
            property_id="eval_config:score-1",
            column_id="score-1",
            col_type="EVAL_METRIC",
            filter_type="number",
            filter_op="equals",
            filter_value=2,
            output_type="SCORE",
        )
        filters = [qualify._relational_filter(score_profile)]
        config = [
            {
                "id": "score-1",
                "property_id": "eval_config:score-1",
                "property_kind": "eval_config",
            }
        ]
        response = list_response(
            [trace_row("a", **{"score-1": 3.5})],
            config=config,
            filters=filters,
        )

        result = qualify._qualify_list_first_page(
            QueueClient([response]),
            kind="trace",
            filters=filters,
            lane="score.attested",
        )

        self.assertTrue(result["positive"])

        wrong = list_response(
            [trace_row("a", **{"score-1": 2.0})],
            config=config,
            filters=filters,
        )
        wrong.data["result"]["metadata"]["query_applied_filter_sha256"] = "0" * 64
        with self.assertRaisesRegex(
            qualify.QualificationFailure,
            "applied-filter proof",
        ):
            qualify._qualify_list_first_page(
                QueueClient([wrong]),
                kind="trace",
                filters=filters,
                lane="score.wrong-attestation",
            )

    def test_voice_descendant_custom_leaf_uses_only_exact_filter_attestation(self):
        filters = [qualify._custom_filter("descendant.number", 7.0, "number")]
        root_without_child_value = trace_row("voice-a")
        root_with_different_value = trace_row("voice-b", **{"descendant.number": 3.0})
        for row in (root_without_child_value, root_with_different_value):
            with self.subTest(root_shape=sorted(row)):
                client = QueueClient(
                    [
                        list_response([row], kind="voice", filters=filters),
                        list_response([row], kind="voice", filters=filters),
                    ]
                )
                result = qualify._qualify_list_protocol(
                    client,
                    kind="voice",
                    filters=filters,
                    lane="voice.descendant-custom",
                )
                self.assertTrue(result["positive"])

        unattested = list_response(
            [root_without_child_value], kind="voice", filters=filters
        )
        unattested.data["result"].pop("query_applied_filter_version")
        with self.assertRaisesRegex(
            qualify.QualificationFailure,
            "response-bound applied-filter proof",
        ):
            qualify._qualify_list_protocol(
                QueueClient([unattested]),
                kind="voice",
                filters=filters,
                lane="voice.missing-attestation",
            )

        wrong = list_response([root_without_child_value], kind="voice", filters=filters)
        wrong.data["result"]["query_applied_filter_sha256"] = "0" * 64
        with self.assertRaisesRegex(
            qualify.QualificationFailure,
            "response-bound applied-filter proof",
        ):
            qualify._qualify_list_protocol(
                QueueClient([wrong]),
                kind="voice",
                filters=filters,
                lane="voice.wrong-attestation",
            )

    def test_descendant_attestation_substitute_remains_voice_only(self):
        filters = [qualify._custom_filter("descendant.number", 7.0, "number")]
        rows = {
            "trace": trace_row("trace-a"),
            "span": {
                "project_id": "project-1",
                "trace_id": "trace-a",
                "span_id": "span-a",
                "start_time": "2026-01-01T12:00:00Z",
            },
        }
        for kind, row in rows.items():
            with self.subTest(kind=kind):
                with self.assertRaisesRegex(
                    qualify.QualificationFailure,
                    "violates its descendant.number filter leaf",
                ):
                    qualify._qualify_list_protocol(
                        QueueClient([list_response([row], kind=kind, filters=filters)]),
                        kind=kind,
                        filters=filters,
                        lane=f"{kind}.strict-descendant",
                    )

    def test_voice_model_attestation_substitute_requires_exact_registry_binding(self):
        exact = qualify._system_model_filter("gpt-test", "string")
        root = trace_row("voice-model")
        client = QueueClient(
            [
                list_response([root], kind="voice", filters=[exact]),
                list_response([root], kind="voice", filters=[exact]),
            ]
        )
        result = qualify._qualify_list_protocol(
            client,
            kind="voice",
            filters=[exact],
            lane="voice.exact-model",
        )
        self.assertTrue(result["positive"])

        invalid_bindings = (
            {**exact, "column_id": "model_alias"},
            {**exact, "property_id": "system_attribute:traces:other"},
            {**exact, "source": "spans"},
            {
                **exact,
                "filter_config": {**exact["filter_config"], "col_type": "NORMAL"},
            },
        )
        for invalid in invalid_bindings:
            with self.subTest(
                column_id=invalid.get("column_id"),
                property_id=invalid.get("property_id"),
                source=invalid.get("source"),
                col_type=invalid["filter_config"].get("col_type"),
            ):
                with self.assertRaisesRegex(
                    qualify.QualificationFailure,
                    "violates its .* filter leaf",
                ):
                    qualify._qualify_list_protocol(
                        QueueClient(
                            [list_response([root], kind="voice", filters=[invalid])]
                        ),
                        kind="voice",
                        filters=[invalid],
                        lane="voice.invalid-model-binding",
                    )

    def test_direct_voice_row_value_proof_precedes_attestation_substitution(self):
        item = qualify._custom_filter("root.number", 7.0, "number")
        qualify._row_matches_filter_leaf(
            kind="voice",
            row={"root.number": 7.0},
            config=[],
            item=item,
            lane="voice.direct-root",
            aggregate_filter_attested=False,
        )
        with self.assertRaisesRegex(
            qualify.QualificationFailure,
            "violates its root.number filter leaf",
        ):
            qualify._row_matches_filter_leaf(
                kind="voice",
                row={"root.number": 3.0},
                config=[],
                item=item,
                lane="voice.direct-root",
                aggregate_filter_attested=False,
            )

    def test_aggregate_categorical_annotation_count_proves_a_raw_leaf(self):
        profile = qualify.RelationalFilterProfile(
            property_id="annotation:label-1",
            column_id="label-1",
            col_type="ANNOTATION",
            filter_type="categorical",
            filter_op="in",
            filter_value=("approved",),
            output_type="categorical",
        )
        proofs = qualify._verify_list_row_semantics(
            kind="trace",
            rows=[trace_row("a", **{"label-1": {"approved": 2}})],
            config=[
                {
                    "id": "label-1",
                    "property_id": "annotation:label-1",
                    "property_kind": "annotation",
                }
            ],
            project_id="project-1",
            filters=[qualify._relational_filter(profile)],
            lane="annotation.categorical",
        )
        self.assertEqual(len(proofs), 1)

    def test_list_time_window_is_end_exclusive(self):
        end = datetime(2026, 1, 2, tzinfo=UTC)
        with self.assertRaisesRegex(
            qualify.QualificationFailure,
            "outside the requested time window",
        ):
            qualify._verify_list_row_semantics(
                kind="trace",
                rows=[trace_row("a", created_at=end.isoformat())],
                config=[],
                project_id="project-1",
                filters=[
                    qualify._time_filter(
                        datetime(2026, 1, 1, tzinfo=UTC),
                        end,
                    )
                ],
                lane="time.end-exclusive",
            )

    def test_positive_and_negative_membership_pages_must_not_overlap(self):
        positive = {
            "qualified": True,
            "evidence": {"row_identity_digests": ["same"]},
        }
        negative = {
            "qualified": True,
            "evidence": {"row_identity_digests": ["same"]},
        }
        conflicts = qualify._profile_membership_conflicts(
            [
                ("365d", "trace", "f5.eval_present", positive),
                ("365d", "trace", "f5.eval_absent", negative),
            ]
        )
        self.assertEqual(
            conflicts,
            ["365d.trace.f5.eval_present_overlaps_f5.eval_absent"],
        )

        annotation_conflicts = qualify._profile_membership_conflicts(
            [
                ("365d", "span", "f6.annotation_exact", positive),
                ("365d", "span", "f6.annotation_absent", negative),
            ]
        )
        self.assertEqual(
            annotation_conflicts,
            ["365d.span.f6.annotation_exact_overlaps_f6.annotation_absent"],
        )

    def test_stable_exact_empty_short_window_passes(self):
        client = QueueClient([list_response([]), list_response([])])
        result = qualify._qualify_list_protocol(
            client,
            kind="trace",
            filters=[],
            lane="short.empty",
        )
        self.assertTrue(result["qualified"])
        self.assertFalse(result["positive"])
        self.assertFalse(result["continuation_exercised"])

    def test_empty_page_without_complete_exact_proof_fails(self):
        client = QueueClient([list_response([], complete=False, status="degraded")])
        with self.assertRaises(qualify.QualificationFailure):
            qualify._qualify_list_protocol(
                client, kind="trace", filters=[], lane="unsafe.empty"
            )
        inexact_client = QueueClient([list_response([], query_exact=False)])
        with self.assertRaises(qualify.QualificationFailure):
            qualify._qualify_list_protocol(
                inexact_client,
                kind="trace",
                filters=[],
                lane="inexact.empty",
            )

    def test_long_window_positive_evidence_is_required_per_profile(self):
        passed = {"qualified": True, "evidence": {"positive": True}}
        empty = {"qualified": True, "evidence": {"positive": False}}
        expected, missing = qualify._missing_long_window_positive_profiles(
            [
                ("30m", "trace", "default", empty),
                ("30d", "trace", "default", passed),
                ("365d", "trace", "sparse", empty),
            ]
        )
        self.assertEqual(expected, [("trace", "default"), ("trace", "sparse")])
        self.assertEqual(missing, ["trace.sparse"])

    def test_negative_empty_witness_is_a_population_gap_not_route_failure(self):
        gaps = qualify._long_window_population_gaps(
            [
                "trace.f5.eval_absent",
                "span.f6.annotation_absent",
                "trace.f5.eval_exact",
                "trace.default",
            ]
        )
        self.assertEqual(
            [gap["kind"] for gap in gaps],
            [
                "negative_complement",
                "negative_complement",
                "observed_exact_value",
                "positive_witness",
            ],
        )

    def test_exact_value_is_observed_only_through_a_filtered_row_identity(self):
        configured_only_lane = (
            "365d",
            "trace",
            "f5.eval_exact",
            {
                "qualified": True,
                "evidence": {"positive": True, "row_identity_digests": []},
            },
        )
        configured_only = qualify._observed_exact_value_proofs([configured_only_lane])
        self.assertEqual(configured_only, [])
        self.assertEqual(
            qualify._missing_observed_exact_profiles(
                [configured_only_lane], configured_only
            ),
            ["trace.f5.eval_exact"],
        )

        proofs = qualify._observed_exact_value_proofs(
            [
                (
                    "365d",
                    "trace",
                    "f5.eval_exact",
                    {
                        "qualified": True,
                        "evidence": {
                            "positive": True,
                            "row_identity_digests": ["observed-row"],
                        },
                    },
                ),
                (
                    "365d",
                    "trace",
                    "f6.annotation_exact",
                    {
                        "qualified": True,
                        "evidence": {
                            "positive": False,
                            "row_identity_digests": [],
                        },
                    },
                ),
            ]
        )
        self.assertEqual(
            proofs,
            [
                {
                    "kind": "trace",
                    "profile": "f5.eval_exact",
                    "window": "365d",
                    "row_identity_digest": "observed-row",
                }
            ],
        )

    def test_graph_requires_exact_or_approved_rollup_metadata(self):
        start = datetime(2026, 1, 1, tzinfo=UTC)
        end = datetime(2026, 2, 1, tzinfo=UTC)
        points = graph_points(start, end, "30d")
        rollup_client = QueueClient(
            [
                graph_response(
                    points,
                    query_exact=False,
                    provenance="materialized_rollup",
                    start=start,
                    end=end,
                )
            ]
        )
        rollup = qualify._qualify_graph(
            rollup_client,
            kind="trace",
            start=start,
            end=end,
            window_name="30d",
            lane="graph.rollup",
        )
        self.assertEqual(rollup["read_mode"], "materialized_rollup")
        self.assertTrue(rollup["positive"])

        exact_client = QueueClient(
            [graph_response(points, query_exact=True, start=start, end=end)]
        )
        exact = qualify._qualify_graph(
            exact_client,
            kind="span",
            start=start,
            end=end,
            window_name="30d",
            lane="graph.exact",
        )
        self.assertEqual(exact["read_mode"], "exact")

        rejected = (
            graph_response(
                points,
                query_exact=False,
                provenance="bounded_candidates",
                start=start,
                end=end,
            ),
            graph_response(
                points,
                query_exact=False,
                provenance="materialized_rollup",
                sampled=True,
                start=start,
                end=end,
            ),
            Response(
                {
                    "status": True,
                    "result": {
                        "data": points,
                        "query_complete": True,
                        "query_status": "complete",
                        "query_sampled": False,
                        "query_window_start": start.isoformat(),
                        "query_window_end": end.isoformat(),
                    },
                }
            ),
        )
        for response in rejected:
            with self.subTest(response=response):
                with self.assertRaises(qualify.QualificationFailure):
                    qualify._qualify_graph(
                        QueueClient([response]),
                        kind="session",
                        start=start,
                        end=end,
                        window_name="30d",
                        lane="graph.rejected",
                    )

    def test_filtered_graph_requires_response_bound_leaf_proof_and_exact_read(self):
        start = datetime(2026, 1, 1, tzinfo=UTC)
        end = datetime(2026, 2, 1, tzinfo=UTC)
        filters = [qualify._custom_filter("customer.key", "customer-1", "string")]
        binding = qualify._graph_filter_binding_sha256(
            project_id="project-1",
            kind="trace",
            filters=filters,
        )
        points = graph_points(start, end, "30d")
        client = QueueClient(
            [
                graph_response(
                    points,
                    query_exact=True,
                    start=start,
                    end=end,
                    applied_filter_sha256=binding,
                    applied_filter_count=1,
                )
            ]
        )

        result = qualify._qualify_graph(
            client,
            kind="trace",
            start=start,
            end=end,
            window_name="30d",
            lane="graph.filtered",
            filters=filters,
        )

        self.assertEqual(result["profile_filter_sha256"], binding)
        self.assertEqual(len(client.calls[0]["body"]["filters"]), 2)

        with self.assertRaisesRegex(
            qualify.QualificationFailure,
            "applied-filter proof",
        ):
            qualify._qualify_graph(
                QueueClient(
                    [
                        graph_response(
                            points,
                            query_exact=True,
                            start=start,
                            end=end,
                        )
                    ]
                ),
                kind="trace",
                start=start,
                end=end,
                window_name="30d",
                lane="graph.unproven",
                filters=filters,
            )

        with self.assertRaisesRegex(
            qualify.QualificationFailure,
            "exact or approved-rollup",
        ):
            qualify._qualify_graph(
                QueueClient(
                    [
                        graph_response(
                            points,
                            query_exact=False,
                            provenance="materialized_rollup",
                            start=start,
                            end=end,
                            applied_filter_sha256=binding,
                            applied_filter_count=1,
                        )
                    ]
                ),
                kind="trace",
                start=start,
                end=end,
                window_name="30d",
                lane="graph.filtered-rollup",
                filters=filters,
            )

    def test_graph_timestamp_matrix_rejects_gaps_order_and_window_drift(self):
        start = datetime(2026, 1, 1, tzinfo=UTC)
        end = datetime(2026, 1, 2, tzinfo=UTC)
        valid = graph_points(start, end, "24h")
        invalid = (
            list(reversed(valid)),
            valid[:-1],
            [*valid, valid[-1]],
        )
        for points in invalid:
            with self.subTest(points=points):
                with self.assertRaisesRegex(
                    qualify.QualificationFailure,
                    "graph buckets",
                ):
                    qualify._qualify_graph(
                        QueueClient(
                            [
                                graph_response(
                                    points,
                                    query_exact=True,
                                    start=start,
                                    end=end,
                                )
                            ]
                        ),
                        kind="span",
                        start=start,
                        end=end,
                        window_name="24h",
                        lane="graph.timestamps",
                    )
        with self.assertRaisesRegex(
            qualify.QualificationFailure,
            "different graph window",
        ):
            qualify._qualify_graph(
                QueueClient(
                    [
                        graph_response(
                            valid,
                            query_exact=True,
                            start=start - timedelta(hours=1),
                            end=end,
                        )
                    ]
                ),
                kind="span",
                start=start,
                end=end,
                window_name="24h",
                lane="graph.window",
            )

    def test_graph_rejects_wrong_metric_or_malformed_numeric_points(self):
        start = datetime(2026, 1, 1, tzinfo=UTC)
        end = datetime(2026, 1, 2, tzinfo=UTC)
        valid = graph_points(start, end, "24h")
        wrong_metric = graph_response(
            valid,
            query_exact=True,
            metric_name="session_count",
            start=start,
            end=end,
        )
        malformed = [dict(point) for point in valid]
        malformed[0]["primary_traffic"] = "3"
        malformed_response = graph_response(
            malformed,
            query_exact=True,
            start=start,
            end=end,
        )
        for response in (wrong_metric, malformed_response):
            with self.subTest(response=response):
                with self.assertRaises(qualify.QualificationFailure):
                    qualify._qualify_graph(
                        QueueClient([response]),
                        kind="trace",
                        start=start,
                        end=end,
                        window_name="24h",
                        lane="graph.shape",
                    )

    def test_dashboard_query_qualifies_exact_filtered_series_and_request_shape(self):
        start = datetime(2026, 1, 1, tzinfo=UTC)
        end = datetime(2026, 2, 1, tzinfo=UTC)
        filters = [qualify._custom_filter("customer.key", "customer-1", "string")]
        points = [
            {"timestamp": point["timestamp"], "value": point["value"]}
            for point in graph_points(start, end, "30d")
        ]
        client = QueueClient(
            [
                dashboard_query_response(
                    points,
                    start=start,
                    end=end,
                    granularity="day",
                )
            ]
        )

        result = qualify._qualify_dashboard_query(
            client,
            start=start,
            end=end,
            window_name="30d",
            lane="dashboard.filtered",
            filters=filters,
        )

        self.assertTrue(result["qualified"])
        self.assertTrue(result["positive"])
        self.assertEqual(result["read_mode"], "exact")
        self.assertEqual(result["profile_filter_count"], 1)
        self.assertEqual(client.calls[0]["endpoint"], "dashboard_query")
        self.assertEqual(client.calls[0]["query"], {"refresh": "false"})
        self.assertEqual(client.calls[0]["body"]["filters"], filters)
        self.assertEqual(client.calls[0]["body"]["project_ids"], ["project-1"])
        self.assertFalse(client.calls[0]["body"]["allow_sampled"])

    def test_dashboard_query_rejects_inexact_or_malformed_series(self):
        start = datetime(2026, 1, 1, tzinfo=UTC)
        end = datetime(2026, 1, 2, tzinfo=UTC)
        points = [
            {"timestamp": point["timestamp"], "value": point["value"]}
            for point in graph_points(start, end, "24h")
        ]
        inexact = dashboard_query_response(
            points,
            start=start,
            end=end,
            granularity="hour",
            exact=False,
            provenance="materialized_rollup",
        )
        gap = dashboard_query_response(
            points[:-1],
            start=start,
            end=end,
            granularity="hour",
        )
        malformed = dashboard_query_response(
            [{**points[0], "value": "12"}, *points[1:]],
            start=start,
            end=end,
            granularity="hour",
        )
        for response in (inexact, gap, malformed):
            with self.subTest(response=response):
                with self.assertRaises(qualify.QualificationFailure):
                    qualify._qualify_dashboard_query(
                        QueueClient([response]),
                        start=start,
                        end=end,
                        window_name="24h",
                        lane="dashboard.rejected",
                    )

    def test_graphs_require_positive_long_window_points_per_profile(self):
        positive = {"qualified": True, "evidence": {"positive": True}}
        empty = {"qualified": True, "evidence": {"positive": False}}
        expected, missing = qualify._missing_long_window_positive_graphs(
            [
                ("30m", "trace", "default", empty),
                ("30d", "trace", "default", positive),
                ("365d", "span", "f1.system", empty),
                ("30m", "session", "default", positive),
            ]
        )
        self.assertEqual(
            expected,
            ["session.default", "span.f1.system", "trace.default"],
        )
        self.assertEqual(missing, ["session.default", "span.f1.system"])

    def test_metrics_catalog_repeats_and_exhausts_exact_cursor_pages(self):
        page_size = qualify.METRIC_CATALOG_QUALIFICATION_PAGE_SIZE
        representative = qualify.DatasetRepresentative(
            dataset_id="11111111-1111-1111-1111-111111111111",
            active_rows=75,
            column_id="22222222-2222-2222-2222-222222222222",
        )

        def metric(index: int) -> dict:
            return {
                "name": f"metric-{index}",
                "property_id": f"system_attribute:traces:metric-{index}",
                "property_kind": "system_attribute",
            }

        first = [metric(index) for index in range(page_size)]
        dataset_definition = {
            "name": representative.column_id,
            "display_name": "Reference answer",
            "property_id": representative.column_property_id,
            "property_kind": "dataset_column",
        }
        second = [
            *[metric(index) for index in range(page_size, page_size + 5)],
            dataset_definition,
        ]
        client = QueueClient(
            [
                metrics_response(
                    first,
                    has_more=True,
                    cursor="metrics-state.1000.sig",
                ),
                metrics_response(
                    first,
                    has_more=True,
                    cursor="metrics-state.1001.sig",
                ),
                metrics_response(second, has_more=False),
                metrics_response(second, has_more=False),
                metrics_response([dataset_definition], has_more=False),
            ]
        )
        result = qualify._qualify_metrics_catalog(
            client,
            lane="metrics",
            expected_property_ids=(first[0]["property_id"],),
            required_dataset_representative=representative,
        )
        self.assertEqual(result["metric_count"], page_size + 6)
        self.assertEqual(result["page_count"], 2)
        self.assertTrue(result["continuation_exercised"])
        self.assertTrue(result["dataset_column_definition_proven"])
        self.assertEqual(result["selected_property_kind"], "dataset_column")
        self.assertEqual(
            result["dataset_representative_binding_sha256"],
            representative.binding_sha256,
        )
        self.assertEqual(
            [call["query"].get("cursor") for call in client.calls],
            [
                None,
                None,
                "metrics-state.1000.sig",
                "metrics-state.1001.sig",
                None,
            ],
        )
        self.assertTrue(
            all(call["query"]["cursor_mode"] == "true" for call in client.calls)
        )
        self.assertEqual(client.calls[-1]["query"]["search"], representative.column_id)

    def test_metrics_catalog_rejects_unstable_or_overlapping_pages(self):
        page_size = qualify.METRIC_CATALOG_QUALIFICATION_PAGE_SIZE

        def metric(index: int) -> dict:
            return {
                "name": f"metric-{index}",
                "property_id": f"system_attribute:traces:metric-{index}",
                "property_kind": "system_attribute",
            }

        first = [metric(index) for index in range(page_size)]
        with self.assertRaises(qualify.QualificationFailure):
            qualify._qualify_metrics_catalog(
                QueueClient(
                    [
                        metrics_response(first, has_more=False),
                        metrics_response(list(reversed(first)), has_more=False),
                    ]
                ),
                lane="metrics.unstable",
            )

        with self.assertRaises(qualify.QualificationFailure):
            qualify._qualify_metrics_catalog(
                QueueClient(
                    [
                        metrics_response(first, has_more=True, cursor="cursor-1"),
                        metrics_response(first, has_more=True, cursor="cursor-1"),
                        metrics_response([first[-1]], has_more=False),
                    ]
                ),
                lane="metrics.overlap",
            )

    def test_metrics_catalog_rejects_activation_drift_and_missing_definitions(self):
        first = [
            {
                "name": "Model",
                "property_id": "system_attribute:traces:model",
                "property_kind": "system_attribute",
            }
        ]
        with self.assertRaisesRegex(
            qualify.QualificationFailure,
            "activation lineage",
        ):
            qualify._qualify_metrics_catalog(
                QueueClient(
                    [
                        metrics_response(first, has_more=True, cursor="cursor-1"),
                        metrics_response(first, has_more=True, cursor="cursor-1"),
                        metrics_response(
                            [
                                {
                                    "name": "customer.plan",
                                    "property_id": "custom_attribute:customer.plan",
                                    "property_kind": "custom_attribute",
                                }
                            ],
                            has_more=False,
                            revision=8,
                            fingerprint="b" * 64,
                        ),
                    ]
                ),
                lane="metrics.drift",
            )

        with self.assertRaisesRegex(
            qualify.QualificationFailure,
            "search changed activation lineage",
        ):
            qualify._qualify_metrics_catalog(
                QueueClient(
                    [
                        metrics_response(first, has_more=False),
                        metrics_response(first, has_more=False),
                        metrics_response(
                            first,
                            has_more=False,
                            revision=8,
                            fingerprint="b" * 64,
                        ),
                    ]
                ),
                lane="metrics.search_drift",
            )

        with self.assertRaisesRegex(
            qualify.QualificationFailure,
            "omitted expected definitions",
        ):
            qualify._qualify_metrics_catalog(
                QueueClient(
                    [
                        metrics_response(first, has_more=False),
                        metrics_response(first, has_more=False),
                    ]
                ),
                lane="metrics.missing",
                expected_property_ids=("custom_attribute:customer.plan",),
            )

        representative = qualify.DatasetRepresentative(
            dataset_id="11111111-1111-1111-1111-111111111111",
            active_rows=1,
            column_id="22222222-2222-2222-2222-222222222222",
        )
        wrong_kind = [
            {
                "name": representative.column_id,
                "property_id": representative.column_property_id,
                "property_kind": "custom_attribute",
            }
        ]
        with self.assertRaisesRegex(
            qualify.QualificationFailure,
            "dataset-column definition",
        ):
            qualify._qualify_metrics_catalog(
                QueueClient(
                    [
                        metrics_response(wrong_kind, has_more=False),
                        metrics_response(wrong_kind, has_more=False),
                    ]
                ),
                lane="metrics.wrong_dataset_kind",
                required_dataset_representative=representative,
            )

    def test_property_key_repeat_crosses_issue_second_and_resumes_both_cursors(self):
        def key_response(rows, has_more=False, cursor=None):
            return Response(
                {
                    "status": True,
                    "result": rows,
                    "has_more": has_more,
                    "next_cursor": cursor,
                    "query_complete": True,
                    "query_status": "complete",
                }
            )

        client = QueueClient(
            [
                key_response(
                    [{"key": "a", "type": "string"}],
                    True,
                    "property-state.1000.sig",
                ),
                key_response(
                    [{"key": "a", "type": "string"}],
                    True,
                    "property-state.1001.sig",
                ),
                key_response([{"key": "b", "type": "number"}], False),
                key_response([{"key": "b", "type": "number"}], False),
            ]
        )

        result = qualify._qualify_key_read_more(client, lane="keys")

        self.assertEqual(result["p2_count"], 1)
        self.assertEqual(
            [call["query"].get("cursor") for call in client.calls],
            [
                None,
                None,
                "property-state.1000.sig",
                "property-state.1001.sig",
            ],
        )

    def test_property_key_read_more_rejects_overlap(self):
        def key_response(rows, has_more=False, cursor=None):
            return Response(
                {
                    "status": True,
                    "result": rows,
                    "has_more": has_more,
                    "next_cursor": cursor,
                    "query_complete": True,
                    "query_status": "complete",
                }
            )

        client = QueueClient(
            [
                key_response([{"key": "a", "type": "string"}], True, "c1"),
                key_response([{"key": "a", "type": "string"}], True, "c2"),
                key_response([{"key": "a", "type": "string"}], False),
            ]
        )
        with self.assertRaises(qualify.QualificationFailure):
            qualify._qualify_key_read_more(client, lane="keys")

    def test_model_value_read_more_advances_without_overlap(self):
        client = QueueClient(
            [
                model_value_response(
                    [{"value": "gpt-4.1", "type": "string"}],
                    has_more=True,
                    cursor="model-state.1000.sig",
                ),
                model_value_response(
                    [{"value": "gpt-4.1", "type": "string"}],
                    has_more=True,
                    cursor="model-state.1001.sig",
                ),
                model_value_response([{"value": "gpt-4o-mini", "type": "string"}]),
                model_value_response([{"value": "gpt-4o-mini", "type": "string"}]),
                model_value_response([{"value": "gpt-4.1", "type": "string"}]),
            ]
        )
        result = qualify._qualify_model_values(
            client,
            lane="models",
            page_size=1,
        )
        self.assertTrue(result["continuation_exercised"])
        self.assertEqual(result["p1_values"], 1)
        self.assertEqual(result["p2_values"], 1)
        self.assertEqual(result["page_size"], 1)
        self.assertTrue(result["search_proven"])
        self.assertEqual(result["catalog_read_mode"], "read")
        self.assertEqual(
            [call["query"]["page_size"] for call in client.calls],
            [1, 1, 1, 1, 1],
        )
        self.assertEqual(
            [call["query"].get("cursor") for call in client.calls],
            [
                None,
                None,
                "model-state.1000.sig",
                "model-state.1001.sig",
                None,
            ],
        )

    def test_model_value_page_accepts_more_values_than_query_count(self):
        self.assertEqual(qualify.MODEL_VALUE_EXPECTED_ACTIVATED_QUERY_COUNT, 4)
        values = [{"value": f"model-{index}", "type": "string"} for index in range(5)]
        client = QueueClient(
            [
                model_value_response(values, query_count=4),
                model_value_response(values, query_count=4),
                model_value_response([values[0]], query_count=4),
            ]
        )

        result = qualify._qualify_model_values(client, lane="models.cardinality")

        self.assertEqual(result["p1_values"], 5)
        self.assertEqual(result["page_size"], 10)
        self.assertFalse(result["continuation_exercised"])
        self.assertTrue(result["search_proven"])
        self.assertEqual(len(client.calls), 3)
        self.assertEqual(
            [call["query"]["page_size"] for call in client.calls],
            [10, 10, 10],
        )

    def test_model_value_page_size_rejects_non_allowlisted_values_pre_client(self):
        for page_size in (None, True, 1.0, 0, -1, 2, 11, 50):
            with self.subTest(page_size=page_size):
                client = QueueClient([])
                with self.assertRaisesRegex(
                    qualify.SafetyViolation,
                    "Model value page size is invalid",
                ):
                    qualify._qualify_model_values(
                        client,
                        lane="models.page_size",
                        page_size=page_size,
                    )
                self.assertEqual(client.calls, [])

    def test_model_value_page_rejects_oversized_payloads(self):
        one = [{"value": "model-a", "type": "string"}]
        two = [
            {"value": "model-a", "type": "string"},
            {"value": "model-b", "type": "string"},
        ]
        cases = (
            (
                "p1",
                [model_value_response(two), model_value_response(two)],
            ),
            (
                "p2",
                [
                    model_value_response(one, has_more=True, cursor="cursor-a"),
                    model_value_response(one, has_more=True, cursor="cursor-b"),
                    model_value_response(two),
                ],
            ),
            (
                "search",
                [
                    model_value_response(one),
                    model_value_response(one),
                    model_value_response(two),
                ],
            ),
        )
        for suffix, responses in cases:
            with self.subTest(suffix=suffix):
                client = QueueClient(responses)
                with self.assertRaisesRegex(
                    qualify.QualificationFailure,
                    rf"models\.oversized\.{suffix} omitted an activated Model value proof",
                ):
                    qualify._qualify_model_values(
                        client,
                        lane="models.oversized",
                        page_size=1,
                    )

    def test_model_value_page_rejects_query_count_drift(self):
        values = [{"value": "model-a", "type": "string"}]
        for query_count in (True, 4.0, 0, 3, 5):
            with self.subTest(query_count=query_count):
                client = QueueClient(
                    [
                        model_value_response(values, query_count=query_count),
                        model_value_response(values, query_count=query_count),
                    ]
                )
                with self.assertRaisesRegex(
                    qualify.QualificationFailure,
                    r"models\.query_count\.p1 omitted an activated Model value proof",
                ):
                    qualify._qualify_model_values(
                        client,
                        lane="models.query_count",
                    )

    def test_model_value_page_preserves_empty_and_unstable_failures(self):
        cases = (
            ([], [], "empty"),
            (
                [{"value": "model-a", "type": "string"}],
                [{"value": "model-b", "type": "string"}],
                "unstable",
            ),
        )
        for first, repeat, label in cases:
            with self.subTest(label=label):
                client = QueueClient(
                    [model_value_response(first), model_value_response(repeat)]
                )
                with self.assertRaisesRegex(
                    qualify.QualificationFailure,
                    "Model values were absent or unstable",
                ):
                    qualify._qualify_model_values(client, lane=f"models.{label}")

    def test_model_value_page_preserves_lineage_failure(self):
        values = [{"value": "model-a", "type": "string"}]
        client = QueueClient(
            [
                model_value_response(values),
                model_value_response(values, revision=8, fingerprint="b" * 64),
            ]
        )

        with self.assertRaisesRegex(
            qualify.QualificationFailure,
            "Model repeat changed activation lineage",
        ):
            qualify._qualify_model_values(client, lane="models.lineage")

    def test_model_value_page_preserves_continuation_failure(self):
        values = [{"value": "model-a", "type": "string"}]
        client = QueueClient(
            [
                model_value_response(values, has_more=True, cursor="cursor-a"),
                model_value_response(values, has_more=True, cursor="cursor-b"),
                model_value_response(values),
            ]
        )

        with self.assertRaisesRegex(
            qualify.QualificationFailure,
            "Model continuation overlapped or was empty",
        ):
            qualify._qualify_model_values(client, lane="models.continuation")

    def test_model_value_page_preserves_exact_search_failure(self):
        values = [{"value": "model-a", "type": "string"}]
        client = QueueClient(
            [
                model_value_response(values),
                model_value_response(values),
                model_value_response([{"value": "model-b", "type": "string"}]),
            ]
        )

        with self.assertRaisesRegex(
            qualify.QualificationFailure,
            "Model value search omitted its exact value",
        ):
            qualify._qualify_model_values(client, lane="models.search")


if __name__ == "__main__":
    unittest.main(verbosity=2)
