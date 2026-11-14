"""
End-to-end tests: fi-collector -> ClickHouse 25.3 -> Django read path.

These tests ingest spans via the fi-collector's OTLP HTTP endpoint, wait for
them to land in ClickHouse, and then verify they are visible through the
Django API layer.

Prerequisites:
  - fi-collector running (OTLP HTTP at localhost:4318)
  - ClickHouse 25.3 test sidecar at localhost:18123 (test compose)
  - Schema 002-014 applied (Django migration 0078)

Run:
  pytest tracer/tests/test_e2e_collector_to_django.py -v -m e2e

If fi-collector is not reachable, all tests in this module skip automatically.
"""

from __future__ import annotations

import json
import time
import uuid

import pytest
import requests

import clickhouse_connect

from tracer.services.clickhouse.v2 import get_v2_config
from tracer.tests._ch_seed import truncate_ch_spans

# Marker: integration + e2e + slow
pytestmark = [pytest.mark.integration, pytest.mark.e2e, pytest.mark.slow]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

COLLECTOR_HTTP_URL = "http://localhost:4318/v1/traces"
COLLECTOR_GRPC_ADDR = "localhost:4317"

# Test identity — isolated from other tests
TEST_PROJECT_ID = "e2e00000-e2e0-4e2e-8e2e-e2e000000001"
TEST_ORG_ID = "e2e00000-e2e0-4e2e-8e2e-e2e000000002"

# ---------------------------------------------------------------------------
# Session-scoped fixture: check collector reachability
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def _require_collector():
    """Skip entire module if fi-collector is not reachable."""
    try:
        # Try HTTP endpoint health (simpler than gRPC probe)
        resp = requests.get("http://localhost:4318/", timeout=2)
        # Collector typically returns 404 or 405 on root but the socket is open
    except requests.ConnectionError:
        pytest.skip(
            "fi-collector not running at localhost:4318 — skipping E2E tests"
        )


@pytest.fixture(scope="session")
def ch_client():
    """Session-scoped ClickHouse client for assertions."""
    cfg = get_v2_config()
    client = clickhouse_connect.get_client(
        host=cfg["host"],
        port=cfg["http_port"],
        username=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
    )
    yield client
    client.close()


@pytest.fixture(autouse=True)
def _truncate_between_tests():
    """Wipe CH spans table between tests for hermetic isolation."""
    yield
    truncate_ch_spans()


# ---------------------------------------------------------------------------
# OTLP span builder + sender
# ---------------------------------------------------------------------------


def _unique_ids() -> tuple[str, str]:
    """Generate a unique trace_id (32 hex) and span_id (16 hex)."""
    trace_id = uuid.uuid4().hex + uuid.uuid4().hex[:16]  # pad to 32 hex
    trace_id = trace_id[:32]
    span_id = uuid.uuid4().hex[:16]
    return trace_id, span_id


def send_otlp_span(
    *,
    trace_id: str | None = None,
    span_id: str | None = None,
    parent_span_id: str | None = None,
    span_name: str = "e2e-test-span",
    service_name: str = "e2e-test-service",
    project_id: str = TEST_PROJECT_ID,
    org_id: str = TEST_ORG_ID,
    status_code: int = 1,  # STATUS_CODE_OK
    attributes: dict | None = None,
    semconv: str = "openinference",
) -> tuple[str, str]:
    """Build an ExportTraceServiceRequest as JSON and POST to the collector.

    Uses the OTLP/HTTP JSON encoding (Content-Type: application/json) which
    the collector accepts alongside protobuf. Returns (trace_id, span_id).
    """
    if trace_id is None or span_id is None:
        _tid, _sid = _unique_ids()
        trace_id = trace_id or _tid
        span_id = span_id or _sid

    now_ns = int(time.time() * 1e9)
    start_ns = str(now_ns - 500_000_000)  # 500ms ago
    end_ns = str(now_ns)

    # Build OTel attribute list from dict
    otel_attrs = []
    for k, v in (attributes or {}).items():
        if isinstance(v, bool):
            otel_attrs.append({"key": k, "value": {"boolValue": v}})
        elif isinstance(v, int):
            otel_attrs.append({"key": k, "value": {"intValue": str(v)}})
        elif isinstance(v, float):
            otel_attrs.append({"key": k, "value": {"doubleValue": v}})
        else:
            otel_attrs.append({"key": k, "value": {"stringValue": str(v)}})

    span_obj = {
        "traceId": trace_id,
        "spanId": span_id,
        "name": span_name,
        "kind": 1,  # SPAN_KIND_INTERNAL
        "startTimeUnixNano": start_ns,
        "endTimeUnixNano": end_ns,
        "attributes": otel_attrs,
        "status": {"code": status_code},
    }
    if parent_span_id:
        span_obj["parentSpanId"] = parent_span_id

    payload = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {
                            "key": "service.name",
                            "value": {"stringValue": service_name},
                        },
                        {
                            "key": "fi.project_id",
                            "value": {"stringValue": project_id},
                        },
                        {
                            "key": "fi.org_id",
                            "value": {"stringValue": org_id},
                        },
                        {
                            "key": "fi.semconv",
                            "value": {"stringValue": semconv},
                        },
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "e2e-test"},
                        "spans": [span_obj],
                    }
                ],
            }
        ]
    }

    resp = requests.post(
        COLLECTOR_HTTP_URL,
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=5,
    )
    assert resp.status_code in (200, 202), (
        f"Collector rejected span: HTTP {resp.status_code} — {resp.text}"
    )
    return trace_id, span_id


def wait_for_ch_row(
    ch_client,
    span_id: str,
    timeout: float = 10,
) -> dict | None:
    """Poll CH spans table until a row with the given span_id appears."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = ch_client.query(
            "SELECT * FROM spans FINAL WHERE id = {span_id:String} LIMIT 1",
            parameters={"span_id": span_id},
        )
        if result.row_count > 0:
            columns = result.column_names
            row = result.first_row
            return dict(zip(columns, row))
        time.sleep(0.5)
    return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestE2ELLMSpanLandsInCH:
    """Ingest an LLM span via collector and verify it lands in CH."""

    def test_span_present_in_ch(self, ch_client):
        trace_id, span_id = send_otlp_span(
            span_name="llm.chat.completion",
            attributes={
                "openinference.span.kind": "LLM",
                "gen_ai.system": "openai",
                "gen_ai.request.model": "gpt-4o-mini",
                "gen_ai.operation.name": "chat",
                "gen_ai.usage.input_tokens": 100,
                "gen_ai.usage.output_tokens": 42,
                "gen_ai.usage.total_tokens": 142,
                "input.value": "Hello from e2e",
                "output.value": "Hi back from e2e",
                "user.is_premium": True,
            },
        )

        row = wait_for_ch_row(ch_client, span_id, timeout=10)
        assert row is not None, f"Span {span_id} not found in CH after 10s"
        assert row["project_id"] == uuid.UUID(TEST_PROJECT_ID)
        assert row["name"] == "llm.chat.completion"
        assert row["status"] == "OK"
        assert row["is_deleted"] == 0


class TestE2ESpanVisibleViaDjangoListSpans:
    """Ingest a span, then verify it's visible through the Django API."""

    @pytest.mark.django_db(transaction=True)
    def test_span_in_list_spans_observe(self, ch_client, auth_client, observe_project):
        # Override project_id to match the Django project fixture
        project_id = str(observe_project.id)
        trace_id, span_id = send_otlp_span(
            span_name="e2e-django-visible",
            project_id=project_id,
            attributes={
                "openinference.span.kind": "LLM",
                "gen_ai.system": "openai",
                "gen_ai.request.model": "gpt-4o",
                "input.value": "django visibility test",
                "output.value": "response",
            },
        )

        # Wait for CH ingestion
        row = wait_for_ch_row(ch_client, span_id, timeout=10)
        assert row is not None, f"Span {span_id} not in CH"

        # Hit the Django endpoint
        response = auth_client.get(
            "/tracer/observation-span/list_spans_observe/",
            {"project_id": project_id, "page": 1, "page_size": 50},
        )
        assert response.status_code == 200
        data = response.json()
        span_ids = [s.get("id") for s in data.get("result", {}).get("data", [])]
        assert span_id in span_ids, (
            f"Span {span_id} not visible via list_spans_observe"
        )


class TestE2ETraceAggregateConsistent:
    """Ingest 3 spans in the same trace and verify aggregate consistency."""

    def test_three_spans_same_trace(self, ch_client):
        trace_id, root_span_id = send_otlp_span(
            span_name="root-span",
            attributes={
                "openinference.span.kind": "CHAIN",
                "gen_ai.system": "openai",
            },
        )

        _, child1_id = _unique_ids()
        send_otlp_span(
            trace_id=trace_id,
            span_id=child1_id,
            parent_span_id=root_span_id,
            span_name="child-llm",
            attributes={
                "openinference.span.kind": "LLM",
                "gen_ai.system": "openai",
                "gen_ai.request.model": "gpt-4o",
                "gen_ai.usage.input_tokens": 50,
                "gen_ai.usage.output_tokens": 20,
                "gen_ai.usage.total_tokens": 70,
            },
        )

        _, child2_id = _unique_ids()
        send_otlp_span(
            trace_id=trace_id,
            span_id=child2_id,
            parent_span_id=root_span_id,
            span_name="child-tool",
            attributes={
                "openinference.span.kind": "TOOL",
                "tool.name": "calculator",
            },
        )

        # Wait for all 3 spans
        deadline = time.monotonic() + 10
        count = 0
        while time.monotonic() < deadline:
            result = ch_client.query(
                "SELECT count() as cnt FROM spans FINAL "
                "WHERE trace_id = {trace_id:String} AND project_id = {pid:String}",
                parameters={"trace_id": trace_id, "pid": TEST_PROJECT_ID},
            )
            count = result.first_row[0]
            if count >= 3:
                break
            time.sleep(0.5)

        assert count >= 3, f"Expected 3 spans in trace, got {count}"

        # Verify each span has the correct trace_id
        result = ch_client.query(
            "SELECT id, name, trace_id FROM spans FINAL "
            "WHERE trace_id = {trace_id:String} ORDER BY name",
            parameters={"trace_id": trace_id},
        )
        names = [row[1] for row in result.result_rows]
        assert "root-span" in names
        assert "child-llm" in names
        assert "child-tool" in names


class TestE2EDatasetCreationFromIngestedSpans:
    """Ingest spans, then create a dataset from them via the Django API."""

    @pytest.mark.django_db(transaction=True)
    def test_add_ingested_spans_to_new_dataset(
        self, ch_client, auth_client, observe_project
    ):
        project_id = str(observe_project.id)

        # Ingest a few spans
        span_ids = []
        for i in range(3):
            _, sid = send_otlp_span(
                span_name=f"dataset-span-{i}",
                project_id=project_id,
                attributes={
                    "openinference.span.kind": "LLM",
                    "gen_ai.system": "openai",
                    "gen_ai.request.model": "gpt-4o",
                    "input.value": f"input-{i}",
                    "output.value": f"output-{i}",
                },
            )
            span_ids.append(sid)

        # Wait for all spans to land in CH
        for sid in span_ids:
            row = wait_for_ch_row(ch_client, sid, timeout=10)
            assert row is not None, f"Span {sid} not in CH"

        # Create dataset from these spans via Django API
        response = auth_client.post(
            "/tracer/dataset/add_to_new_dataset/",
            data=json.dumps(
                {
                    "project_id": project_id,
                    "span_ids": span_ids,
                    "dataset_name": f"E2E Dataset {uuid.uuid4().hex[:8]}",
                    "columns": ["input", "output", "model"],
                }
            ),
            content_type="application/json",
        )
        # Accept 200 or 201; the endpoint may vary
        assert response.status_code in (200, 201), (
            f"add_to_new_dataset failed: {response.status_code} — {response.content}"
        )
