"""W10-INSIGHTS-L02: empty orchestration roots still carry readable turns."""

from types import SimpleNamespace

import pytest

from ee.agenthub.insights import units
from ee.agenthub.insights.tests import seeded_support as seed


class WrapperReader(seed.FakeReader):
    def __init__(self):
        super().__init__({"session": [(" \n", [], "", False)]})
        # OBS-SW-31 prices the full trace, not the text fallback or root alone.
        # This wrapper has no own spend; its LLM child owns the known cost.
        root = self.roots[0]
        root.id = "session-t1-root"
        root.parent_span_id = ""
        root.observation_type = "agent"
        root.project_id = "project"
        root.cost = 0.0
        root.prompt_tokens = 200
        root.completion_tokens = 100
        self.llm = SimpleNamespace(
            **(
                vars(root)
                | {
                    "id": "session-t1-llm",
                    "parent_span_id": root.id,
                    "name": "chat",
                    "observation_type": "llm",
                    "cost": 0.001,
                    "attrs_string": {
                        "input.value": '{"messages":[{"role":"user","content":"Where is my order?"}]}',
                        "output.value": '{"content":"It arrives tomorrow."}',
                    },
                    "input": "",
                    "output": "",
                }
            )
        )
        self.cost_reads = []
        self.cost_read_error = None
        self.fallback_reads = []

    def list_by_trace_ids(self, trace_ids, *, project_id, include_heavy):
        assert project_id == "project"
        assert include_heavy is False
        self.cost_reads.append((list(trace_ids), project_id, include_heavy))
        if self.cost_read_error is not None:
            raise self.cost_read_error
        spans = self.roots + ([self.llm] if self.llm is not None else [])
        return [
            span
            for span in spans
            if span.trace_id in trace_ids and span.project_id == project_id
        ]

    def first_span_by_type(self, trace_id, observation_type, *, project_id):
        assert project_id == "project"
        assert trace_id == "session-t1"
        assert observation_type == "llm"
        self.fallback_reads.append(trace_id)
        return self.llm


@pytest.fixture
def reader(monkeypatch):
    reader = WrapperReader()
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: reader)
    monkeypatch.setattr(units, "_user_labels", lambda ids: {})
    return reader


def load(unit, **options):
    unit_id = {"traces": "session-t1", "sessions": "session", "users": "cust_001"}[unit]
    return units.load_units(unit, "project", [unit_id], **options), unit_id


@pytest.mark.parametrize("unit", ["traces", "sessions", "users"])
def test_empty_wrapper_uses_llm_text_for_report_and_preview(reader, unit):
    loaded, unit_id = load(unit)
    assert unit_id in loaded
    result = loaded[unit_id]
    assert "User: Where is my order?" in result.content
    assert "Assistant: It arrives tomorrow." in result.content
    assert result.excerpt == "Where is my order?"
    assert result.output == "It arrives tomorrow."
    assert result.metrics["source_trace_ids"] == ["session-t1"]
    assert result.metrics["source_session_ids"] == ["session"]
    assert result.metrics["cost"] == 0.001
    assert result.metrics["tokens"] == 300
    assert result.time == seed.START
    assert result.source_created_at == seed.START
    assert reader.cost_reads == [(["session-t1"], "project", False)]
    assert reader.fallback_reads == ["session-t1"]
    # Reading the fallback must not rewrite the actual root or its attributes.
    assert reader.roots[0].attrs_string == {"input.value": " \n", "output.value": ""}
    if unit == "traces":
        assert result.label == "support_agent"
    if unit == "sessions":
        assert result.label == "Where is my order?"


@pytest.mark.parametrize("unit", ["traces", "sessions", "users"])
@pytest.mark.parametrize(
    "cost_state,expected",
    [
        pytest.param("additive-root", 0.003, id="additive-root"),
        pytest.param("unknown-child", None, id="unknown-child"),
        pytest.param("unreadable-inventory", None, id="unreadable-inventory"),
    ],
)
def test_descendant_text_does_not_bypass_cost_provenance(
    reader, unit, cost_state, expected
):
    if cost_state == "additive-root":
        reader.roots[0].cost = 0.002
    elif cost_state == "unknown-child":
        reader.llm.cost = None
        reader.llm.attrs_string["fi.cost.kind"] = "unknown"
    else:
        # Even a known root and readable, priced first LLM cannot prove the
        # complete trace total when the full inventory cannot be read.
        reader.roots[0].cost = 0.002
        reader.cost_read_error = RuntimeError("cost inventory unavailable")

    loaded, unit_id = load(unit)

    result = loaded[unit_id]
    assert "User: Where is my order?" in result.content
    assert "Assistant: It arrives tomorrow." in result.content
    assert result.excerpt == "Where is my order?"
    assert result.output == "It arrives tomorrow."
    assert result.metrics["cost"] == expected
    assert result.metrics["tokens"] == 300
    assert reader.cost_reads == [(["session-t1"], "project", False)]
    assert reader.fallback_reads == ["session-t1"]


@pytest.mark.parametrize("unit", ["traces", "sessions"])
def test_empty_wrapper_with_only_tool_content_is_not_skipped(reader, unit):
    reader.llm = None
    reader.steps = [
        seed._step(
            "session-t1",
            "tool-1",
            "lookup_order",
            "tool",
            seed.START,
            '{"order_id":"123"}',
            '{"delivery":"tomorrow"}',
            shown=1,
        )
    ]
    loaded, unit_id = load(unit)
    assert unit_id in loaded
    assert (
        'Tool lookup_order {"order_id":"123"} -> {"delivery":"tomorrow"}'
        in loaded[unit_id].content
    )
    # Tool results are tool evidence, not fabricated assistant speech.
    assert loaded[unit_id].output == ""
    assert loaded[unit_id].content.endswith("Assistant: ")


@pytest.mark.parametrize("unit", ["traces", "sessions"])
@pytest.mark.parametrize(
    "text_in,text_out", [("Root question", ""), ("", "Root answer")]
)
def test_existing_root_content_remains_authoritative(reader, unit, text_in, text_out):
    root = reader.roots[0]
    root.input, root.output = text_in, text_out
    root.attrs_string = {}
    loaded, unit_id = load(unit)
    result = loaded[unit_id]
    assert f"User: {text_in}" in result.content
    assert f"Assistant: {text_out}" in result.content
    assert "tomorrow" not in result.content
    assert result.output == text_out
    assert reader.fallback_reads == []


@pytest.mark.parametrize("unit", ["traces", "sessions"])
def test_selected_root_with_content_does_not_use_an_earlier_empty_root(reader, unit):
    root = SimpleNamespace(**vars(reader.roots[0]))
    root.attrs_string = {"input.value": "Root question", "output.value": "Root answer"}
    reader.roots.append(root)
    loaded, unit_id = load(unit)
    assert "User: Root question" in loaded[unit_id].content
    assert loaded[unit_id].output == "Root answer"
    assert reader.fallback_reads == []


@pytest.mark.parametrize("unit", ["traces", "sessions"])
def test_genuinely_empty_wrapper_stays_empty(reader, unit):
    reader.llm = None
    loaded, unit_id = load(unit)
    assert unit_id not in loaded
    kept, _ = load(unit, keep_empty=True)
    assert kept[unit_id].content == ""
    assert kept[unit_id].output == ""


@pytest.mark.parametrize("unit", ["traces", "sessions"])
def test_v2_keeps_empty_wrapper_without_reading_descendants(reader, unit):
    loaded, unit_id = load(unit, keep_empty=True, read_steps=False)
    assert loaded[unit_id].content == ""
    assert loaded[unit_id].output == ""
    assert reader.fallback_reads == []
    assert reader.asked == []


def test_fallback_preserves_media_scrubbing_and_root_error(reader):
    reader.llm.attrs_string = {
        "input.value": "See data:image/png;base64,AAAA",
        "output.value": "Image received",
    }
    reader.roots[0].status = "ERROR"
    reader.roots[0].status_message = "wrapper failed"
    loaded, unit_id = load("traces")
    content = loaded[unit_id].content
    assert f"User: See {units.IMAGE_NOTE}" in content
    assert "base64" not in content
    assert "Step support_agent (turn) -> ERROR wrapper failed" in content
    assert loaded[unit_id].metrics["errors"] == 1


def test_deleted_roots_are_not_reintroduced_by_fallback(reader, monkeypatch):
    monkeypatch.setattr(units, "retained_spans", lambda project, roots: [])
    loaded, _ = load("traces")
    assert loaded == {}
    assert reader.fallback_reads == []
