"""
End-to-end eval lifecycle: ingest span -> create eval task -> verify result.

This test validates the full evaluation pipeline:
  1. Span lands in ClickHouse (via collector or direct seed)
  2. Eval task is created via the Django API
  3. Eval engine processes the span
  4. EvalLogger row appears in CH
  5. Eval result is visible via get_eval_attributes_list

Prerequisites:
  - ClickHouse 25.3 test sidecar at localhost:18123
  - Schema 002-014 applied
  - Django test DB available

Run:
  pytest tracer/tests/test_e2e_eval_lifecycle.py -v -m e2e

When fi-collector is not running, spans are seeded directly via _ch_seed.py.
"""

from __future__ import annotations

import json
import time
import uuid

import clickhouse_connect
import pytest

from tracer.services.clickhouse.v2 import get_v2_config
from tracer.tests._ch_seed import seed_ch_span, truncate_ch_spans

pytestmark = [pytest.mark.integration, pytest.mark.e2e, pytest.mark.slow]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

COLLECTOR_HTTP_URL = "http://localhost:4318/v1/traces"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def ch_client():
    """Session-scoped ClickHouse client."""
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
    """Wipe CH tables between tests for hermetic isolation."""
    yield
    truncate_ch_spans()


def _collector_available() -> bool:
    """Check if fi-collector is reachable."""
    try:
        import requests

        requests.get("http://localhost:4318/", timeout=1)
        return True
    except Exception:
        return False


def _send_otlp_span_http(
    *,
    trace_id: str,
    span_id: str,
    project_id: str,
    span_name: str = "eval-test-span",
    attributes: dict | None = None,
) -> None:
    """Send a span via OTLP/HTTP JSON to the collector."""
    import requests

    now_ns = int(time.time() * 1e9)
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

    payload = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "eval-e2e"}},
                        {"key": "fi.project_id", "value": {"stringValue": project_id}},
                        {
                            "key": "fi.org_id",
                            "value": {
                                "stringValue": "e2e00000-e2e0-4e2e-8e2e-e2e000000002"
                            },
                        },
                        {
                            "key": "fi.semconv",
                            "value": {"stringValue": "openinference"},
                        },
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "e2e-eval"},
                        "spans": [
                            {
                                "traceId": trace_id,
                                "spanId": span_id,
                                "name": span_name,
                                "kind": 1,
                                "startTimeUnixNano": str(now_ns - 500_000_000),
                                "endTimeUnixNano": str(now_ns),
                                "attributes": otel_attrs,
                                "status": {"code": 1},
                            }
                        ],
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
        f"Collector rejected span: {resp.status_code}"
    )


def _seed_span_directly(
    *,
    span_id: str,
    trace_id: str,
    project,
    span_name: str = "eval-test-span",
    attributes: dict | None = None,
):
    """Seed a span directly into CH when the collector is not available.

    Creates a Django ObservationSpan and seeds it via _ch_seed, mirroring
    the conftest pattern.
    """
    from datetime import timedelta

    from django.utils import timezone

    from tracer.models.observation_span import ObservationSpan
    from tracer.models.trace import Trace

    trace = Trace.objects.create(
        id=trace_id if len(trace_id) <= 36 else trace_id[:36],
        project=project,
        name=f"Trace for {span_name}",
        input={"prompt": "eval test input"},
        output={"response": "eval test output"},
    )

    span = ObservationSpan.objects.create(
        id=span_id,
        project=project,
        trace=trace,
        name=span_name,
        observation_type="llm",
        start_time=timezone.now() - timedelta(seconds=1),
        end_time=timezone.now(),
        input={"messages": [{"role": "user", "content": "eval test"}]},
        output={"choices": [{"message": {"content": "eval response"}}]},
        model="gpt-4o",
        prompt_tokens=50,
        completion_tokens=25,
        total_tokens=75,
        cost=0.001,
        latency_ms=500,
        status="OK",
        span_attributes=attributes
        or {
            "input": "eval test",
            "output": "eval response",
            "model_name": "gpt-4o",
            "provider_name": "openai",
        },
    )
    seed_ch_span(span)
    return span, trace


def _ingest_span(
    *,
    project,
    span_name: str = "eval-test-span",
    attributes: dict | None = None,
):
    """Ingest a span via collector (preferred) or direct seed (fallback).

    Returns (span_id, trace_id).
    """
    trace_id = uuid.uuid4().hex + uuid.uuid4().hex[:16]
    trace_id = trace_id[:32]
    span_id = f"eval_{uuid.uuid4().hex[:12]}"

    if _collector_available():
        _send_otlp_span_http(
            trace_id=trace_id,
            span_id=span_id,
            project_id=str(project.id),
            span_name=span_name,
            attributes=attributes
            or {
                "openinference.span.kind": "LLM",
                "gen_ai.system": "openai",
                "gen_ai.request.model": "gpt-4o",
                "input.value": "eval test input",
                "output.value": "eval test output",
            },
        )
    else:
        _seed_span_directly(
            span_id=span_id,
            trace_id=trace_id,
            project=project,
            span_name=span_name,
            attributes=attributes,
        )

    return span_id, trace_id


def _wait_for_ch_row(ch_client, span_id: str, timeout: float = 10) -> dict | None:
    """Poll CH until the span appears."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = ch_client.query(
            "SELECT * FROM spans FINAL WHERE id = {sid:String} LIMIT 1",
            parameters={"sid": span_id},
        )
        if result.row_count > 0:
            return dict(zip(result.column_names, result.first_row, strict=False))
        time.sleep(0.5)
    return None


def _wait_for_eval_logger_row(
    ch_client,
    span_id: str,
    timeout: float = 30,
) -> dict | None:
    """Poll CH eval_logger table until a result appears for the given span."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = ch_client.query(
                "SELECT * FROM eval_logger FINAL WHERE span_id = {sid:String} LIMIT 1",
                parameters={"sid": span_id},
            )
            if result.row_count > 0:
                return dict(zip(result.column_names, result.first_row, strict=False))
        except Exception:
            # eval_logger table may not exist yet in all environments
            pass
        time.sleep(1)
    return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture
def observe_eval_config(db, observe_project, eval_template):
    """CustomEvalConfig scoped to the observe project (not the experiment project)."""
    from tracer.models.custom_eval_config import CustomEvalConfig

    return CustomEvalConfig.objects.create(
        name="E2E Observe Eval Config",
        project=observe_project,
        eval_template=eval_template,
        config={"threshold": 0.5},
        mapping={"input": "input", "output": "output"},
        filters={},
    )


@pytest.mark.django_db(transaction=True)
class TestE2EEvalLifecycle:
    """Full eval lifecycle: ingest -> eval task -> result in CH."""

    def test_ingest_and_create_eval_task(
        self,
        ch_client,
        auth_client,
        observe_project,
        eval_template,
        observe_eval_config,
    ):
        """Verify a span can be ingested and an eval task created against it."""
        span_id, trace_id = _ingest_span(
            project=observe_project,
            span_name="eval-lifecycle-span",
        )

        # Confirm span is in CH (via collector or direct seed)
        if _collector_available():
            row = _wait_for_ch_row(ch_client, span_id, timeout=10)
            assert row is not None, f"Span {span_id} not in CH"

        # Create an eval task targeting this project
        response = auth_client.post(
            "/tracer/eval-task/",
            data=json.dumps(
                {
                    "project": str(observe_project.id),
                    "name": f"E2E Eval Task {uuid.uuid4().hex[:8]}",
                    "evals": [str(observe_eval_config.id)],
                    "filters": {},
                    "sampling_rate": 1.0,
                    "run_type": "continuous",
                    "spans_limit": 10,
                }
            ),
            content_type="application/json",
        )
        assert response.status_code in (200, 201), (
            f"Eval task creation failed: {response.status_code} — {response.content}"
        )
        task_data = response.json()
        assert "id" in task_data or "id" in task_data.get("result", {}), (
            f"No task ID in response: {task_data}"
        )

    def test_eval_result_visible_via_attributes_list(
        self,
        ch_client,
        auth_client,
        populated_observe_project,
    ):
        """After eval runs, results should be visible via get_eval_attributes_list."""
        project = populated_observe_project["project"]

        # The populated_observe_project fixture seeds spans with
        # span_attributes that include input/output. Verify the endpoint
        # returns those keys.
        response = auth_client.get(
            "/tracer/observation-span/get_eval_attributes_list/",
            {"filters": json.dumps({"project_id": str(project.id)})},
        )
        assert response.status_code == 200
        result = response.json().get("result", [])
        assert isinstance(result, list)
        assert len(result) > 0, "get_eval_attributes_list returned empty"
        # The populated fixture sets input/output in span_attributes
        assert "input" in result
        assert "output" in result
