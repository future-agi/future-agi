"""Unit spend includes retained children without changing root-based transcripts."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from ee.agenthub.insights import units

PROJECT = "project-a"
START = datetime(2026, 10, 1, tzinfo=UTC)


def _span(trace_id, *, child=False, at=0, **overrides):
    fields = {
        "id": f"{trace_id}-child" if child else f"{trace_id}-root",
        "parent_span_id": f"{trace_id}-root" if child else "",
        "observation_type": "llm" if child else "agent",
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "trace_id": trace_id,
        "trace_session_id": "session-a",
        "end_user_id": "user-a",
        "project_id": PROJECT,
        "attrs_string": {},
        "input": f"question {trace_id}",
        "output": "child-only answer" if child else f"answer {trace_id}",
        "start_time": START + timedelta(seconds=at),
        "end_time": START + timedelta(seconds=at + 1),
        "created_at": START,
        "trace_name": "agent",
        "name": "model" if child else "agent",
        "status": "ERROR" if child else "OK",
        "status_message": "",
        "latency_ms": 9000 if child else 100,
        "cost": 0,
        "total_tokens": 1000 if child else 7,
    }
    return SimpleNamespace(**(fields | overrides))


class _Reader:
    def __init__(self):
        self.roots = []
        self.spans = []
        self.cost_reads = []
        self.retention_reads = []
        self.excluded_sessions = set()
        self.read_error = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def _roots(self, field, ids, project_id):
        assert project_id == PROJECT
        return [
            span
            for span in self.roots
            if getattr(span, field) in ids and span.project_id == project_id
        ]

    def roots_by_trace_ids(self, ids, *, project_id):
        return self._roots("trace_id", ids, project_id)

    def roots_by_session_ids(self, ids, *, project_id):
        return self._roots("trace_session_id", ids, project_id)

    def roots_by_end_user_ids(self, ids, *, project_id, since, until):
        return [
            span
            for span in self._roots("end_user_id", ids, project_id)
            if (since is None or span.start_time >= since)
            and (until is None or span.start_time < until)
        ]

    def source_arrivals_by_trace_ids(self, ids, *, project_id, version_cutoff=None):
        assert project_id == PROJECT
        return dict.fromkeys(ids, START)

    def activity_by_trace_ids(self, ids, *, project_id, **_):
        assert project_id == PROJECT
        return []

    def list_by_trace_ids(self, ids, *, project_id, include_heavy):
        self.cost_reads.append((list(ids), project_id, include_heavy))
        assert project_id == PROJECT
        assert include_heavy is False
        if self.read_error:
            raise self.read_error
        return [
            span
            for span in self.spans
            if span.trace_id in ids and span.project_id == project_id
        ]

    def retained(self, project_id, spans):
        assert project_id == PROJECT
        spans = list(spans)
        self.retention_reads.append(spans)
        return [
            span
            for span in spans
            if span.trace_session_id not in self.excluded_sessions
        ]


@pytest.fixture
def reader(monkeypatch):
    reader = _Reader()
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: reader)
    monkeypatch.setattr(units, "_user_labels", lambda ids: {})
    monkeypatch.setattr(units, "retained_spans", reader.retained)
    return reader


def _load(kind, **kwargs):
    ids = {
        "traces": ["trace-a", "trace-b"],
        "sessions": ["session-a"],
        "users": ["user-a"],
    }[kind]
    return units.load_units(kind, PROJECT, ids, **kwargs)


@pytest.mark.parametrize("kind", ["traces", "sessions", "users"])
@pytest.mark.parametrize("read_steps", [True, False], ids=["with-steps", "no-steps"])
def test_cost_includes_all_selected_spans_but_other_metrics_use_roots(
    reader, kind, read_steps
):
    first = _span("trace-a")
    second = _span("trace-b", at=2, cost=0.005, latency_ms=300)
    reader.roots = [first, second]
    reader.spans = [
        first,
        _span("trace-a", child=True, cost=0.02),
        _span("trace-a", child=True, cost=0.003),
        second,
        _span("trace-b", child=True, cost=0.04),
        _span("unselected-trace", child=True, cost=99),
        _span("trace-a", child=True, project_id="project-b", cost=99),
    ]

    loaded = _load(kind, read_steps=read_steps)

    if kind == "traces":
        assert loaded["trace-a"].metrics["cost"] == pytest.approx(0.023)
        assert loaded["trace-b"].metrics["cost"] == pytest.approx(0.045)
        assert loaded["trace-a"].metrics["latency_ms"] == 100
        assert loaded["trace-b"].metrics["latency_ms"] == 300
        assert (
            loaded["trace-a"].content
            == "User: question trace-a\nAssistant: answer trace-a"
        )
        assert loaded["trace-a"].metrics["traces"] == 1
    else:
        text = next(iter(loaded.values()))
        assert text.metrics["cost"] == pytest.approx(0.068)
        assert text.metrics["latency_ms"] == 200
        assert text.metrics["traces"] == text.metrics["turns"] == 2
        assert text.metrics["tokens"] == 14
        assert text.output == "answer trace-b"
    for text in loaded.values():
        assert text.metrics["errors"] == 0
        assert "child-only answer" not in text.content
        assert text.source_created_at == START
    assert reader.cost_reads == [(["trace-a", "trace-b"], PROJECT, False)]


@pytest.mark.parametrize("kind", ["traces", "sessions", "users"])
@pytest.mark.parametrize("missing", ["unknown-child", "unknown-root", "absent-trace"])
def test_incomplete_trace_cost_is_unknown_and_propagates_to_groups(
    reader, kind, missing
):
    first = _span("trace-a", cost=None if missing == "unknown-root" else 0)
    second = _span("trace-b", at=2, cost=0.03)
    reader.roots = [first, second]
    reader.spans = [second]
    if missing != "absent-trace":
        reader.spans += [
            first,
            _span(
                "trace-a", child=True, cost=None if missing == "unknown-child" else 0.02
            ),
            _span("trace-a", child=True, cost=0.01),
        ]

    loaded = _load(kind, read_steps=False)

    if kind == "traces":
        assert loaded["trace-a"].metrics["cost"] is None
        assert loaded["trace-b"].metrics["cost"] == pytest.approx(0.03)
    else:
        assert next(iter(loaded.values())).metrics["cost"] is None


@pytest.mark.parametrize("kind", ["traces", "sessions", "users"])
def test_unreadable_cost_does_not_replace_unknown_with_root_only_total(reader, kind):
    reader.roots = [_span("trace-a", cost=0.005)]
    reader.read_error = RuntimeError("cost read unavailable")

    loaded = _load(kind, read_steps=False)

    assert loaded
    for text in loaded.values():
        assert text.metrics["cost"] is None
        assert "question trace-a" in text.content
        assert text.output == "answer trace-a"


@pytest.mark.parametrize("kind", ["traces", "sessions", "users"])
def test_cost_uses_retained_spans_and_reads_each_selected_trace_once(reader, kind):
    root = _span("trace-a", cost=0.005)
    discarded_root = _span("trace-b", trace_session_id="deleted-session", cost=90)
    child = _span("trace-a", child=True, cost=0.02)
    discarded_child = _span(
        "trace-a", child=True, trace_session_id="deleted-session", cost=90
    )
    reader.roots = [root, root, discarded_root]
    reader.spans = [root, child, discarded_child, discarded_root]
    reader.excluded_sessions.add("deleted-session")

    loaded = _load(kind, read_steps=False)

    assert len(loaded) == 1
    text = next(iter(loaded.values()))
    assert text.metrics["cost"] == pytest.approx(0.025)
    assert text.metrics["traces"] == text.metrics["turns"] == 1
    assert reader.cost_reads == [(["trace-a"], PROJECT, False)]
    assert any(
        child in spans and discarded_child in spans for spans in reader.retention_reads
    )


@pytest.mark.parametrize("kind", ["traces", "sessions"])
def test_empty_root_kept_for_v2_still_has_child_cost(reader, kind):
    root = _span("trace-a", input="", output="")
    reader.roots = [root]
    reader.spans = [root, _span("trace-a", child=True, cost=0.02)]

    loaded = _load(kind, keep_empty=True, read_steps=False)

    assert len(loaded) == 1
    text = next(iter(loaded.values()))
    assert text.metrics["cost"] == pytest.approx(0.02)
    assert text.content == text.output == ""


def test_user_cost_includes_sessions_and_turns_not_shown_in_transcript(reader):
    for session in range(units.USER_SESSIONS + 1):
        for turn in range(units.USER_TURNS_PER_SESSION + 1):
            trace_id = f"trace-{session}-{turn}"
            root = _span(
                trace_id,
                at=session * 100 + turn,
                trace_session_id=f"session-{session}",
            )
            reader.roots.append(root)
            reader.spans += [root, _span(trace_id, child=True, cost=0.02)]

    text = _load("users", read_steps=False)["user-a"]

    assert text.metrics["cost"] == pytest.approx(len(reader.roots) * 0.02)
    assert text.metrics["traces"] == len(reader.roots)
    assert "question trace-0-0" not in text.content
    assert "question trace-1-4" not in text.content
    assert "earlier sessions not shown" in text.content
    assert len(reader.cost_reads[0][0]) == len(reader.roots)


@pytest.mark.parametrize("kind", ["sessions", "users"])
def test_group_cost_rounds_after_summing_trace_totals(reader, kind):
    reader.roots = [_span("trace-a"), _span("trace-b", at=2)]
    reader.spans = reader.roots + [
        _span("trace-a", child=True, cost=0.0000004),
        _span("trace-b", child=True, cost=0.0000004),
    ]

    text = next(iter(_load(kind, read_steps=False).values()))

    assert text.metrics["cost"] == 0.000001


@pytest.mark.parametrize("kind", ["traces", "sessions", "users"])
def test_recorded_zero_cost_is_still_zero(reader, kind):
    root = _span("trace-a")
    reader.roots = [root]
    reader.spans = [root, _span("trace-a", child=True, cost=0)]

    text = next(iter(_load(kind, read_steps=False).values()))

    assert text.metrics["cost"] == 0.0


@pytest.mark.parametrize("kind", ["traces", "sessions", "users"])
def test_no_retained_roots_does_not_read_full_spans(reader, kind):
    assert _load(kind, read_steps=False) == {}
    assert reader.cost_reads == []


@pytest.mark.parametrize("kind", ["traces", "sessions", "users"])
def test_nonadditive_root_preserves_child_spend(reader, kind):
    # Native normalization records contribution=none and cost.kind=unknown;
    # the reader decodes the stored zero as None, not a billable unknown.
    root = _span(
        "trace-a",
        cost=None,
        attrs_string={
            "fi.cost.kind": "unknown",
            "fi.usage.contribution": "none",
            "fi.usage.scope": "turn",
        },
    )
    reader.roots = [root]
    reader.spans = [root, _span("trace-a", child=True, cost=1.25)]

    text = next(iter(_load(kind, read_steps=False).values()))

    assert text.metrics["cost"] == 1.25


@pytest.mark.parametrize("kind", ["traces", "sessions", "users"])
@pytest.mark.parametrize(
    "activity_type,usage,markers,expected",
    [
        pytest.param("tool", {}, {}, 1.25, id="tokenless-tool"),
        pytest.param("chain", {}, {}, 1.25, id="tokenless-chain"),
        pytest.param("agent", {}, {}, 1.25, id="tokenless-agent"),
        pytest.param("llm", {}, {}, None, id="unpriced-llm"),
        pytest.param("tool", {"total_tokens": 1}, {}, None, id="total-usage"),
        pytest.param("agent", {"prompt_tokens": 1}, {}, None, id="prompt-usage"),
        pytest.param(
            "chain", {"completion_tokens": 1}, {}, None, id="completion-usage"
        ),
        pytest.param("tool", {}, {"fi.usage.scope": "turn"}, None, id="turn-usage"),
        pytest.param(
            "llm",
            {"total_tokens": 120},
            {"fi.usage.contribution": "none"},
            1.25,
            id="nonadditive-llm",
        ),
        pytest.param(
            "agent",
            {},
            {"fi.usage.contribution": "none", "fi.usage.scope": "turn"},
            1.25,
            id="nonadditive-turn",
        ),
    ],
)
def test_native_activity_cost_completeness(
    reader, kind, activity_type, usage, markers, expected
):
    root = _span("trace-a", total_tokens=0)
    activity = _span(
        "trace-a",
        child=True,
        **(
            {
                "id": "activity",
                "observation_type": activity_type,
                "total_tokens": 0,
                "cost": None,
                "attrs_string": {"fi.cost.kind": "unknown", **markers},
            }
            | usage
        ),
    )
    reader.roots = [root]
    reader.spans = [root, activity, _span("trace-a", child=True, cost=1.25)]

    text = next(iter(_load(kind, read_steps=False).values()))

    assert text.metrics["cost"] == expected


@pytest.mark.parametrize("kind", ["traces", "sessions", "users"])
@pytest.mark.parametrize(
    "parent_changes,child_changes,include_child,expected",
    [
        pytest.param({}, {}, True, 1.25, id="known-repeat"),
        pytest.param({}, {}, False, 1.25, id="parent-only"),
        pytest.param({}, {"cost": 0.5}, True, 1.75, id="different-cost"),
        pytest.param(
            {},
            {"prompt_tokens": 90, "completion_tokens": 30},
            True,
            2.5,
            id="different-token-components",
        ),
        pytest.param({"observation_type": "llm"}, {}, True, 2.5, id="llm-not-summary"),
        pytest.param(
            {"name": "other.agent.run"}, {}, True, 2.5, id="unknown-convention"
        ),
        pytest.param(
            {"cost": None, "attrs_string": {"fi.cost.kind": "unknown"}},
            {"cost": 0.0},
            True,
            0.0,
            id="unknown-wrapper-known-zero",
        ),
        pytest.param(
            {},
            {
                "cost": 0.0,
                "total_tokens": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
            },
            True,
            1.25,
            id="missing-child-usage",
        ),
    ],
)
def test_repeated_summary_attribution(
    reader, kind, parent_changes, child_changes, include_child, expected
):
    usage = {
        "cost": 1.25,
        "total_tokens": 120,
        "prompt_tokens": 100,
        "completion_tokens": 20,
    }
    root = _span(
        "trace-a", **({"name": "pydantic_ai.agent.run"} | usage | parent_changes)
    )
    reader.roots = [root]
    reader.spans = [root]
    if include_child:
        reader.spans.append(_span("trace-a", child=True, **(usage | child_changes)))

    text = next(iter(_load(kind, read_steps=False).values()))

    assert text.metrics["cost"] == expected


@pytest.mark.parametrize("kind", ["traces", "sessions", "users"])
@pytest.mark.parametrize(
    "summary", [False, True], ids=["plain-parent", "summary-parent"]
)
def test_equal_independent_calls_remain_additive(reader, kind, summary):
    root = _span(
        "trace-a",
        name="pydantic_ai.agent.run" if summary else "agent",
        cost=2.5 if summary else 0,
        total_tokens=240 if summary else 0,
        prompt_tokens=200 if summary else 0,
        completion_tokens=40 if summary else 0,
    )
    reader.roots = [root]
    reader.spans = [root] + [
        _span(
            "trace-a",
            child=True,
            id=span_id,
            cost=1.25,
            total_tokens=120,
            prompt_tokens=100,
            completion_tokens=20,
        )
        for span_id in ("call-1", "call-2")
    ]

    text = next(iter(_load(kind, read_steps=False).values()))

    assert text.metrics["cost"] == 2.5


@pytest.mark.parametrize("kind", ["traces", "sessions", "users"])
def test_summary_children_are_matched_within_their_trace(reader, kind):
    usage = {
        "cost": 1.25,
        "total_tokens": 120,
        "prompt_tokens": 100,
        "completion_tokens": 20,
    }
    first = _span("trace-a", id="shared-id", name="pydantic_ai.agent.run", **usage)
    second = _span("trace-b", id="shared-id", total_tokens=0, at=2)
    reader.roots = [first, second]
    reader.spans = [
        first,
        second,
        _span("trace-b", child=True, parent_span_id="shared-id", **usage),
    ]

    loaded = _load(kind, read_steps=False)

    if kind == "traces":
        assert loaded["trace-a"].metrics["cost"] == 1.25
        assert loaded["trace-b"].metrics["cost"] == 1.25
    else:
        assert next(iter(loaded.values())).metrics["cost"] == 2.5


@pytest.mark.parametrize("kind", ["traces", "sessions", "users"])
@pytest.mark.parametrize(
    "depth", [0, 1, 8, 40], ids=["direct", "chain", "deep", "deeper"]
)
@pytest.mark.parametrize(
    "nested", [False, True], ids=["root-summary", "nested-summary"]
)
def test_genai_summary_cost_uses_descendant_llms(reader, kind, depth, nested):
    usage = {
        "cost": 0.25,
        "total_tokens": 193,
        "prompt_tokens": 190,
        "completion_tokens": 3,
    }
    summary = _span(
        "trace-a",
        id="summary",
        parent_span_id="trace-a-root" if nested else "",
        name="request",
        attrs_string={"gen_ai.operation.name": "invoke_agent"},
        **usage,
    )
    root = _span("trace-a", cost=0.05, total_tokens=0) if nested else summary
    reader.roots = [root]
    reader.spans = [root, summary] if nested else [summary]
    parent_id = summary.id
    for index in range(depth):
        chain = _span(
            "trace-a",
            id=f"loop-{index}",
            parent_span_id=parent_id,
            observation_type="chain",
            total_tokens=0,
        )
        reader.spans.append(chain)
        parent_id = chain.id
    reader.spans.append(_span("trace-a", child=True, parent_span_id=parent_id, **usage))

    text = next(iter(_load(kind, read_steps=False).values()))

    assert text.metrics["cost"] == (0.30 if nested else 0.25)
    assert text.metrics["traces"] == text.metrics["turns"] == 1
    assert text.metrics["latency_ms"] == 100
    assert text.metrics["errors"] == 0
    assert text.output == "answer trace-a"
    assert "child-only answer" not in text.content
    assert reader.cost_reads == [(["trace-a"], PROJECT, False)]


@pytest.mark.parametrize("kind", ["traces", "sessions", "users"])
@pytest.mark.parametrize(
    "case,expected",
    [
        pytest.param("parent-only", 0.25, id="parent-only"),
        pytest.param("different-cost", 0.375, id="different-cost"),
        pytest.param(
            "different-token-components", 0.5, id="different-token-components"
        ),
        pytest.param("llm-not-summary", 0.5, id="llm-not-summary"),
        pytest.param("unmarked-parent", 0.5, id="unmarked-parent"),
        pytest.param("unknown-child", None, id="unknown-child"),
        pytest.param("nonadditive-parent", 0.25, id="nonadditive-parent"),
        pytest.param("same-id-llm", 0.375, id="same-id-llm"),
    ],
)
def test_genai_summary_cost_preserves_additive_and_unknown_usage(
    reader, kind, case, expected
):
    usage = {
        "cost": 0.25,
        "total_tokens": 193,
        "prompt_tokens": 190,
        "completion_tokens": 3,
    }
    summary = _span(
        "trace-a",
        name="request",
        attrs_string={"gen_ai.operation.name": "invoke_agent"},
        **usage,
    )
    child = _span("trace-a", child=True, **usage)
    if case == "different-cost":
        child.cost = 0.125
    elif case == "different-token-components":
        child.prompt_tokens = 189
        child.completion_tokens = 4
    elif case == "llm-not-summary":
        summary.observation_type = "llm"
    elif case == "unmarked-parent":
        summary.attrs_string = {}
    elif case == "unknown-child":
        child.cost = None
        child.attrs_string = {"fi.cost.kind": "unknown"}
    elif case == "nonadditive-parent":
        summary.cost = None
        summary.attrs_string.update(
            {"fi.usage.contribution": "none", "fi.cost.kind": "unknown"}
        )
    reader.roots = [summary]
    reader.spans = [summary] if case == "parent-only" else [summary, child]
    if case == "same-id-llm":
        # A retained physical LLM can share a summary's logical id. Suppress
        # only the non-LLM summary, never this independent request's spend.
        reader.spans.append(
            _span(
                "trace-a",
                child=True,
                id=summary.id,
                parent_span_id="",
                **(usage | {"cost": 0.125}),
            )
        )

    text = next(iter(_load(kind, read_steps=False).values()))

    assert text.metrics["cost"] == expected


@pytest.mark.parametrize("kind", ["traces", "sessions", "users"])
def test_genai_summary_descendant_proof_stays_within_each_trace(reader, kind):
    usage = {
        "cost": 0.25,
        "total_tokens": 193,
        "prompt_tokens": 190,
        "completion_tokens": 3,
    }
    reader.roots = [
        _span(
            trace_id,
            id="shared-id",
            name="request",
            attrs_string={"gen_ai.operation.name": "invoke_agent"},
            **usage,
        )
        for trace_id in ("trace-a", "trace-b")
    ]
    reader.spans = reader.roots + [
        _span(
            "trace-b",
            id="loop",
            parent_span_id="shared-id",
            observation_type="chain",
            total_tokens=0,
        ),
        _span("trace-b", child=True, parent_span_id="loop", **usage),
    ]

    loaded = _load(kind, read_steps=False)

    if kind == "traces":
        assert loaded["trace-a"].metrics["cost"] == 0.25
        assert loaded["trace-b"].metrics["cost"] == 0.25
    else:
        assert next(iter(loaded.values())).metrics["cost"] == 0.5
