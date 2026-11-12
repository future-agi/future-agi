"""Live JSON ingestion regressions; real PG persistence with all CH I/O faked."""

from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from django.db import transaction

from integrations.transformers.langfuse_transformer import LangfuseTransformer
from tracer.models.observation_span import ObservationSpan
from tracer.models.trace import Trace
from tracer.services.clickhouse.v2 import curated_writer, trace_writer
from tracer.utils.langfuse_upsert import upsert_langfuse_trace
from tracer.views import langfuse_ingestion

START = "2026-11-09T12:00:00Z"
END = "2026-11-09T12:00:02Z"


class RecordingClient:
    def __init__(self):
        self.inserts = []
        self.fail_spans = False
        self.span_attempts = 0

    def insert(self, table, rows, column_names):
        if table == "spans":
            self.span_attempts += 1
            if self.fail_spans:
                raise RuntimeError("injected span mirror outage")
        self.inserts.append(
            (table, [dict(zip(column_names, row, strict=True)) for row in rows])
        )

    @property
    def spans(self):
        return [row for table, rows in self.inserts if table == "spans" for row in rows]


@pytest.fixture
def live(
    monkeypatch,
    observe_project,
    organization,
    workspace,
    django_capture_on_commit_callbacks,
):
    client = RecordingClient()
    monkeypatch.setenv("CH25_DROP_LEGACY_CDC_CHAIN", "true")
    monkeypatch.delenv("CH25_SPAN_DUAL_WRITE", raising=False)
    monkeypatch.setattr(trace_writer, "_get_client", lambda: client)
    monkeypatch.setattr(trace_writer, "_reset_client", lambda: None)
    monkeypatch.setattr(curated_writer, "_get_client", lambda: client)
    monkeypatch.setattr(
        langfuse_ingestion, "_resolve_project", lambda *args: observe_project
    )

    def send(trace_body, observations=()):
        with django_capture_on_commit_callbacks(execute=True):
            langfuse_ingestion._process_traces(
                trace_bodies={trace_body["id"]: deepcopy(trace_body)},
                observation_bodies={trace_body["id"]: deepcopy(list(observations))},
                score_bodies={},
                org=organization,
                workspace=workspace,
                workspace_id=str(workspace.id),
                user=None,
            )

    def snapshot(body):
        return upsert_langfuse_trace(
            assembled_trace=deepcopy(body),
            transformer=LangfuseTransformer(),
            project_id=str(observe_project.id),
            org=organization,
            workspace=workspace,
            org_id=organization.id,
        )

    return SimpleNamespace(
        send=send,
        snapshot=snapshot,
        client=client,
        capture=django_capture_on_commit_callbacks,
    )


def _trace(trace_id="trace-live"):
    return {
        "id": trace_id,
        "name": "customer question",
        "input": "trace question",
        "output": "trace answer",
        "metadata": {"route": "support"},
        "tags": ["production"],
        "userId": "customer-17",
        "sessionId": "session-17",
    }


def _generation(span_id="generation-live", **overrides):
    return {
        "id": span_id,
        "type": "GENERATION",
        "name": "answer",
        "model": "gpt-4o",
        "input": "question",
        "startTime": START,
        "metadata": {"step": "answer"},
        "modelParameters": {"temperature": 0},
        **overrides,
    }


def _completion(span_id="generation-live", **overrides):
    return {
        "id": span_id,
        "type": "GENERATION",
        "output": "answer",
        "endTime": END,
        "usage": {"input": 10, "output": 4},
        **overrides,
    }


def test_separate_flush_preserves_generation_and_trace(live):
    live.send(_trace(), [_generation(level="ERROR")])
    live.send({"id": "trace-live"}, [_completion()])
    span = ObservationSpan.no_workspace_objects.get(id="generation-live")
    trace = Trace.no_workspace_objects.get(id=span.trace_id)
    root = ObservationSpan.no_workspace_objects.get(id="root-trace-live")
    assert (span.name, span.model, span.input, span.output) == (
        "answer",
        "gpt-4o",
        "question",
        "answer",
    )
    assert span.start_time == datetime(2026, 11, 9, 12, tzinfo=UTC)
    assert span.latency_ms == root.latency_ms == 2000
    assert span.status == "ERROR"
    assert span.metadata == {"step": "answer"}
    assert span.model_parameters == {"temperature": 0}
    assert (span.prompt_tokens, span.completion_tokens, span.total_tokens) == (
        10,
        4,
        14,
    )
    assert trace.name == root.name == "customer question"
    assert trace.input == root.input == "trace question"
    assert trace.output == root.output == "trace answer"
    assert trace.tags == ["production", "langfuse"]
    assert trace.metadata == {"route": "support", "integration_source": "langfuse"}
    assert span.span_attributes["input.value"] == "question"
    assert span.span_attributes["llm.model_name"] == "gpt-4o"
    assert span.span_attributes["user.id"] == "customer-17"
    assert span.span_attributes["session.id"] == "session-17"


def test_split_flush_matches_single_flush(live):
    live.send(_trace("split"), [_generation("split-span")])
    live.send({"id": "split"}, [_completion("split-span")])
    live.send(
        _trace("single"), [_generation("single-span"), _completion("single-span")]
    )
    split = ObservationSpan.no_workspace_objects.get(id="split-span")
    single = ObservationSpan.no_workspace_objects.get(id="single-span")
    for field in (
        "name",
        "model",
        "input",
        "output",
        "start_time",
        "end_time",
        "latency_ms",
        "status",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "metadata",
        "model_parameters",
        "span_attributes",
        "eval_attributes",
        "end_user_id",
    ):
        assert getattr(split, field) == getattr(single, field), field


def test_existing_parent_survives_observation_only_update(live):
    live.send(
        _trace(),
        [
            {"id": "parent-live", "type": "SPAN", "name": "parent", "startTime": START},
            _generation(parentObservationId="parent-live"),
        ],
    )
    live.send({"id": "trace-live"}, [_completion()])
    span = ObservationSpan.no_workspace_objects.get(id="generation-live")
    assert span.parent_span_id == "parent-live"


def test_later_usage_patch_preserves_other_counter_and_recomputes_total(live):
    live.send(_trace(), [_generation(usage={"input": 10, "output": 4})])
    live.send(
        {"id": "trace-live"},
        [
            {
                "id": "generation-live",
                "type": "GENERATION",
                "usage": {"completionTokens": 0},
            }
        ],
    )
    span = ObservationSpan.no_workspace_objects.get(id="generation-live")
    assert (span.prompt_tokens, span.completion_tokens, span.total_tokens) == (
        10,
        0,
        10,
    )
    assert span.input == "question"


@pytest.mark.parametrize("usage_key", ["usage", "usageDetails"])
@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        pytest.param(
            {"input": None, "promptTokens": 10, "output": 4},
            (10, 4, 14),
            id="null-input",
        ),
        pytest.param(
            {"input": 10, "output": None, "completionTokens": 4},
            (10, 4, 14),
            id="null-output",
        ),
        pytest.param(
            {"input": 10, "output": 4, "total": None, "totalTokens": 99},
            (10, 4, 99),
            id="null-total",
        ),
        pytest.param(
            {"input": 0, "promptTokens": 10, "output": 4},
            (0, 4, 4),
            id="zero-input",
        ),
        pytest.param(
            {"input": 10, "output": 0, "completionTokens": 4},
            (10, 0, 10),
            id="zero-output",
        ),
        pytest.param(
            {"input": 10, "output": 4, "total": 0, "totalTokens": 99},
            (10, 4, 0),
            id="zero-total",
        ),
    ],
)
def test_usage_alias_precedence_survives_separate_flush(
    live, usage_key, usage, expected
):
    live.send(_trace(), [_generation(**{usage_key: usage})])
    span = ObservationSpan.no_workspace_objects.get(id="generation-live")
    assert (span.prompt_tokens, span.completion_tokens, span.total_tokens) == expected

    live.send(
        {"id": "trace-live"},
        [
            {
                "id": "generation-live",
                "type": "GENERATION",
                "endTime": END,
                usage_key: usage,
            }
        ],
    )
    span.refresh_from_db()
    assert (span.prompt_tokens, span.completion_tokens, span.total_tokens) == expected
    assert (span.name, span.model, span.input) == ("answer", "gpt-4o", "question")
    assert span.start_time == datetime(2026, 11, 9, 12, tzinfo=UTC)
    assert span.latency_ms == 2000


def test_cost_details_patch_replaces_saved_calculated_cost(live):
    live.send(_trace(), [_generation(calculatedTotalCost=1.5)])
    live.send(
        {"id": "trace-live"},
        [{"id": "generation-live", "type": "GENERATION", "costDetails": {"total": 0}}],
    )
    span = ObservationSpan.no_workspace_objects.get(id="generation-live")
    assert span.cost == 0
    assert span.model == "gpt-4o"


def test_explicit_null_and_empty_are_not_omissions(live):
    live.send(_trace(), [_generation()])
    live.send(
        {"id": "trace-live", "input": None, "tags": [], "metadata": {}},
        [
            {
                "id": "generation-live",
                "type": "GENERATION",
                "input": None,
                "metadata": {},
            }
        ],
    )
    span = ObservationSpan.no_workspace_objects.get(id="generation-live")
    trace = Trace.no_workspace_objects.get(id=span.trace_id)
    assert span.input is None and span.metadata == {}
    assert trace.input is None and trace.tags == ["langfuse"]
    assert trace.metadata == {"integration_source": "langfuse"}
    assert trace.name == "customer question"
    assert trace.output == "trace answer"
    assert span.model == "gpt-4o"
    assert "input.value" not in span.span_attributes


def test_committed_spans_and_synthetic_root_are_mirrored(live):
    live.send(_trace(), [_generation(endTime=END)])
    rows = {row["id"]: row for row in live.client.spans}
    assert set(rows) == {"generation-live", "root-trace-live"}
    span = ObservationSpan.no_workspace_objects.get(id="generation-live")
    trace = Trace.no_workspace_objects.get(id=span.trace_id)
    child = rows["generation-live"]
    root = rows["root-trace-live"]
    assert child["parent_span_id"] == root["id"]
    assert root["parent_span_id"] == ""
    assert child["trace_id"] == root["trace_id"] == str(trace.id)
    assert child["project_id"] == str(trace.project_id)
    assert child["trace_session_id"] == str(trace.session_id)
    assert child["end_user_id"] == str(span.end_user_id)
    assert child["input"] == "question"
    assert child["latency_ms"] == root["latency_ms"] == 2000
    assert child["_version"] > 0 and root["_version"] > 0


def test_mirror_waits_for_commit_and_rollback_discards(live):
    body = {**_trace(), "observations": [_generation(endTime=END)]}
    with live.capture(execute=True):
        with pytest.raises(RuntimeError, match="roll back"):
            with transaction.atomic():
                live.snapshot(body)
                assert live.client.spans == []
                raise RuntimeError("roll back")
    assert live.client.spans == []
    assert not ObservationSpan.no_workspace_objects.filter(
        id="generation-live"
    ).exists()
    with live.capture(execute=True):
        with transaction.atomic():
            live.snapshot(body)
            assert live.client.spans == []
    assert {row["id"] for row in live.client.spans} == {
        "generation-live",
        "root-trace-live",
    }


def test_updated_span_versions_and_root_relocation(live):
    live.send(_trace(), [_generation(endTime=END)])
    first = {row["id"]: row for row in live.client.spans}
    live.client.inserts.clear()
    live.send({"id": "trace-live"}, [_completion()])
    updated = next(row for row in live.client.spans if row["id"] == "generation-live")
    assert updated["_version"] > first["generation-live"]["_version"]
    assert updated["start_time"] == first["generation-live"]["start_time"]
    live.client.inserts.clear()
    live.send(
        {"id": "trace-live"},
        [_generation("earlier-span", startTime="2026-11-09T11:00:00Z", endTime=END)],
    )
    roots = [row for row in live.client.spans if row["id"] == "root-trace-live"]
    assert len(roots) == 2
    old = next(row for row in roots if row["is_deleted"] == 1)
    new = next(row for row in roots if row["is_deleted"] == 0)
    assert old["start_time"] == first["root-trace-live"]["start_time"]
    assert new["start_time"].hour == 11
    assert old["_version"] == new["_version"] > first["root-trace-live"]["_version"]


def test_span_mirror_failure_keeps_pg_ingestion(live):
    live.client.fail_spans = True
    live.send(_trace(), [_generation(endTime=END)])
    assert live.client.span_attempts == 1
    assert ObservationSpan.no_workspace_objects.filter(id="generation-live").exists()
    assert ObservationSpan.no_workspace_objects.filter(id="root-trace-live").exists()


def test_snapshot_imports_still_replace_missing_fields(live):
    live.snapshot({**_trace(), "observations": [_generation()]})
    live.snapshot({"id": "trace-live", "observations": [_completion()]})
    span = ObservationSpan.no_workspace_objects.get(id="generation-live")
    trace = Trace.no_workspace_objects.get(id=span.trace_id)
    assert span.name == "" and span.model == "" and span.input is None
    assert span.start_time is None and span.latency_ms == 0
    assert trace.input is None and trace.tags == ["langfuse"]


def test_disabled_span_mirror_does_not_write(live, monkeypatch):
    monkeypatch.setenv("CH25_SPAN_DUAL_WRITE", "false")
    live.send(_trace(), [_generation(endTime=END)])
    assert live.client.spans == []
    assert ObservationSpan.no_workspace_objects.filter(id="generation-live").exists()
