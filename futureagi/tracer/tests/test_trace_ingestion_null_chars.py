"""Regression: NUL (\\u0000) in span input/output must not break OTLP ingestion.

Production raised ``DataError: unsupported Unicode escape sequence`` from the
core backend. That is Postgres rejecting a jsonb value that holds ``\\u0000``.
The span COPY in ``_bulk_create_with_copy`` already strips NUL, but the root
span's raw input/output is also copied onto ``Trace.input`` / ``Trace.output``
and written through the ORM (``Trace.objects.bulk_update``), which sends the
NUL through to jsonb unchanged and fails the whole batch.

These tests drive the real ``bulk_create_observation_span_task`` body with an
OTLP JSON payload exactly as the collector stores it (``payload_storage``),
so the NUL arrives the way it does in production: as the ``\\u0000`` escape in
the JSON wire text.
"""

import base64
import json
import time
import uuid
from unittest.mock import patch

import pytest

from tracer.models.observation_span import ObservationSpan
from tracer.models.trace import Trace


def _to_b64(hex_str: str) -> str:
    return base64.b64encode(bytes.fromhex(hex_str)).decode()


def _kv(key: str, value: str) -> dict:
    return {"key": key, "value": {"stringValue": value}}


def _root_span(trace_id: str, span_id: str, input_value: str, output_value: str):
    now = time.time()
    return {
        "trace_id": _to_b64(trace_id),
        "span_id": _to_b64(span_id),
        "name": "pdf-summary",
        "start_time_unix_nano": str(int((now - 1.0) * 1e9)),
        "end_time_unix_nano": str(int(now * 1e9)),
        "attributes": [
            _kv("fi.span.kind", "LLM"),
            _kv("input.value", input_value),
            _kv("output.value", output_value),
        ],
        "status": {"code": "STATUS_CODE_OK"},
        "events": [],
    }


def _payload_bytes(project_name: str, spans: list[dict]) -> bytes:
    payload = {
        "resource_spans": [
            {
                "resource": {
                    "attributes": [
                        _kv("project_name", project_name),
                        _kv("project_type", "observe"),
                    ]
                },
                "scope_spans": [{"scope": {"name": "t", "version": "1"}, "spans": spans}],
            }
        ]
    }
    # json.dumps writes a real "\x00" as the six-char escape \u0000, which is
    # what the SDK/collector put on the wire.
    raw = json.dumps(payload).encode("utf-8")
    assert b"\\u0000" in raw
    return raw


def _run_task(payload: bytes, organization, user, workspace):
    """Run the task body; return the exception it raised (or None)."""
    from tracer.utils import trace_ingestion

    task = trace_ingestion.bulk_create_observation_span_task._original_func
    with (
        patch.object(trace_ingestion.payload_storage, "retrieve", return_value=payload),
        patch("ee.usage.deployment.DeploymentMode.is_oss", return_value=True),
    ):
        try:
            task(
                "payload-key",
                str(organization.id),
                str(user.id),
                str(workspace.id),
                payload_format="json",
            )
        except Exception as exc:  # noqa: BLE001 - asserted on below
            return exc
    return None


def _as_text(value) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


@pytest.mark.django_db
def test_root_span_with_nul_in_input_is_ingested(
    observe_project, organization, user, workspace
):
    trace_id = uuid.uuid4().hex
    span_id = uuid.uuid4().hex[:16]
    payload = _payload_bytes(
        observe_project.name,
        [_root_span(trace_id, span_id, "page one\x00page two", "summary ok")],
    )

    error = _run_task(payload, organization, user, workspace)

    assert error is None, f"ingestion failed: {type(error).__name__}: {error}"
    trace = Trace.objects.get(id=uuid.UUID(trace_id))
    assert _as_text(trace.input) == "page onepage two"
    assert _as_text(trace.output) == "summary ok"
    assert ObservationSpan.objects.filter(trace_id=trace.id).count() == 1


@pytest.mark.django_db
def test_batch_with_nested_and_only_nul_values_keeps_other_unicode(
    observe_project, organization, user, workspace
):
    nested_trace = uuid.uuid4().hex
    only_nul_trace = uuid.uuid4().hex
    clean_trace = uuid.uuid4().hex
    nested_input = json.dumps(
        {"doc": {"pages": ["café\x00 \U0001f600", "日本\x00語"]}}
    )
    payload = _payload_bytes(
        observe_project.name,
        [
            _root_span(nested_trace, uuid.uuid4().hex[:16], nested_input, "a\x00b"),
            _root_span(only_nul_trace, uuid.uuid4().hex[:16], "prompt", "\x00\x00"),
            _root_span(
                clean_trace, uuid.uuid4().hex[:16], "café \U0001f600", "日本"
            ),
        ],
    )

    error = _run_task(payload, organization, user, workspace)

    assert error is None, f"ingestion failed: {type(error).__name__}: {error}"
    assert ObservationSpan.objects.filter(project=observe_project).count() == 3

    nested = Trace.objects.get(id=uuid.UUID(nested_trace))
    nested_text = _as_text(nested.input)
    assert "\x00" not in nested_text and "\\u0000" not in nested_text
    assert "café \U0001f600" in nested_text
    assert "日本語" in nested_text
    assert _as_text(nested.output) == "ab"

    only_nul = Trace.objects.get(id=uuid.UUID(only_nul_trace))
    assert only_nul.output in (None, "")

    clean = Trace.objects.get(id=uuid.UUID(clean_trace))
    assert _as_text(clean.input) == "café \U0001f600"
    assert _as_text(clean.output) == "日本"
