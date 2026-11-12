"""OBS-SW-26: issue trace rows retain recorded, coverage-aware monetary cost."""

from contextlib import nullcontext
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tracer.queries import feed

PROJECT = "project-1"
STAMP = datetime(2026, 11, 9, tzinfo=UTC)


def _span(
    span_id="span-1",
    *,
    trace_id="trace-1",
    project_id=PROJECT,
    cost=1.25,
    prompt=1000,
    completion=500,
    total=None,
    attrs=None,
    name="request",
    kind="llm",
    parent="",
):
    return SimpleNamespace(
        id=span_id,
        trace_id=trace_id,
        project_id=project_id,
        cost=cost,
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion if total is None else total,
        attrs_string=attrs or {},
        name=name,
        observation_type=kind,
        parent_span_id=parent,
    )


@pytest.fixture
def fetch_rows(monkeypatch):
    """Keep the row builder real; replace only its PG/CH and enrichment I/O."""

    def fetch(spans, *, trace_ids=("trace-1",), limit=50, offset=0, totals=None):
        members = MagicMock()
        for method in ("filter", "order_by", "values", "annotate"):
            getattr(members, method).return_value = members
        members.count.return_value = len(trace_ids)
        members.__getitem__.side_effect = lambda key: [
            {"trace_id": tid} for tid in trace_ids
        ][key]
        monkeypatch.setattr(feed, "_members_with_occurrence_time", lambda: members)
        monkeypatch.setattr(feed, "_is_omega_group", lambda *_: True)
        monkeypatch.setattr(
            feed,
            "_resolve_member_traces",
            lambda ids, *_args, **_kwargs: {
                tid: SimpleNamespace(id=tid, created_at=STAMP, input="fallback")
                for tid in ids
            },
        )
        if totals is None:
            totals = {}
            for tid in trace_ids:
                owned = [
                    s for s in spans if s.trace_id == tid and s.project_id == PROJECT
                ]
                if owned:
                    totals[tid] = (
                        150,
                        sum(s.prompt_tokens for s in owned),
                        sum(s.completion_tokens for s in owned),
                    )
        monkeypatch.setattr(feed, "_get_trace_totals_batch", lambda *_: totals)
        monkeypatch.setattr(
            feed, "_get_trace_scores_batch", lambda *_: dict.fromkeys(trace_ids, 0.4)
        )
        monkeypatch.setattr(feed, "_session_judges_batch", lambda *_: {})
        monkeypatch.setattr(
            feed,
            "_get_root_spans_batch",
            lambda *_: {
                tid: SimpleNamespace(attrs_string={"input.value": f"input:{tid}"})
                for tid in trace_ids
            },
        )
        monkeypatch.setattr(
            feed,
            "_get_investigation_reports_batch",
            lambda *_: {tid: SimpleNamespace(turn_count=2) for tid in trace_ids},
        )
        reader = MagicMock(spec=["list_by_trace_ids"])

        def read(ids, *, project_id=None, include_heavy=True):
            return [
                s
                for s in spans
                if s.trace_id in ids
                and (project_id is None or s.project_id == project_id)
            ]

        reader.list_by_trace_ids.side_effect = read
        reader_factory = MagicMock(return_value=nullcontext(reader))
        monkeypatch.setattr(feed, "get_reader", reader_factory)
        rows, count = feed._fetch_trace_rows(
            "issue-1", PROJECT, limit=limit, offset=offset
        )
        return SimpleNamespace(
            rows=rows, total=count, reader=reader, reader_factory=reader_factory
        )

    return fetch


@pytest.mark.parametrize(
    ("cost", "prompt", "completion", "kind"),
    [
        pytest.param(1.25, 1000, 500, "llm", id="reported-price"),
        pytest.param(0.0, 1000, 500, "llm", id="reported-zero-with-tokens"),
        pytest.param(0.0, 0, 0, "llm", id="reported-zero-without-tokens"),
        pytest.param(1.25, 0, 0, "llm", id="tokenless-price"),
        pytest.param(2.5, 0, 0, "tool", id="non-llm-recorded-price"),
        pytest.param(0.000000007, 1000, 500, "llm", id="sub-micro-price"),
    ],
)
def test_rows_preserve_recorded_cost_without_token_repricing(
    fetch_rows, cost, prompt, completion, kind
):
    result = fetch_rows(
        [
            _span(
                cost=cost,
                prompt=prompt,
                completion=completion,
                kind=kind,
                attrs={"fi.cost.kind": "provider_reported"},
            )
        ]
    )

    row = result.rows[0]
    assert row.cost == cost
    assert row.tokens == ((prompt + completion) or None)
    assert (row.id, row.input, row.timestamp, row.latency_ms, row.score, row.turns) == (
        "trace-1",
        "input:trace-1",
        STAMP,
        150,
        0.4,
        2,
    )
    assert result.total == 1


@pytest.mark.parametrize(
    ("kind", "prompt", "completion", "total", "scope"),
    [
        pytest.param("llm", 0, 0, 0, "", id="tokenless-llm"),
        pytest.param("tool", 5, 0, 5, "", id="prompt-usage"),
        pytest.param("tool", 0, 5, 5, "", id="completion-usage"),
        pytest.param("tool", 0, 0, 5, "", id="total-only-usage"),
        pytest.param("chain", 0, 0, 0, "turn", id="turn-usage"),
    ],
)
def test_unknown_billable_cost_makes_the_whole_trace_unknown(
    fetch_rows, kind, prompt, completion, total, scope
):
    # CHSpanReader decodes fi.cost.kind=unknown as cost=None, not numeric zero.
    result = fetch_rows(
        [
            _span("known", cost=1.25),
            _span(
                "unknown",
                cost=None,
                prompt=prompt,
                completion=completion,
                total=total,
                kind=kind,
                attrs={"fi.cost.kind": "unknown", "fi.usage.scope": scope},
            ),
        ]
    )

    assert result.rows[0].cost is None


def test_unknown_price_is_not_overwritten_by_a_later_known_span(fetch_rows):
    result = fetch_rows(
        [
            _span("unknown", cost=None, attrs={"fi.cost.kind": "unknown"}),
            _span("known", cost=1.25),
        ]
    )

    assert result.rows[0].cost is None


def test_nonadditive_summary_and_tokenless_activity_do_not_poison_cost(fetch_rows):
    result = fetch_rows(
        [
            _span(
                "summary",
                cost=None,
                kind="chain",
                attrs={
                    "fi.usage.contribution": "none",
                    "fi.usage.scope": "turn",
                    "fi.cost.kind": "unknown",
                },
            ),
            _span(
                "activity",
                cost=None,
                prompt=0,
                completion=0,
                kind="tool",
                attrs={"fi.cost.kind": "unknown"},
            ),
            _span("request", cost=1.25),
        ]
    )

    assert result.rows[0].cost == 1.25


def test_nonadditive_numeric_summary_is_not_added(fetch_rows):
    result = fetch_rows(
        [
            _span("summary", cost=90.0, attrs={"fi.usage.contribution": "none"}),
            _span("request", cost=1.25),
        ]
    )

    assert result.rows[0].cost == 1.25


def test_repeated_parent_usage_is_not_double_counted_but_sibling_calls_are(fetch_rows):
    result = fetch_rows(
        [
            _span(
                "summary",
                cost=2.5,
                prompt=2000,
                completion=1000,
                kind="chain",
                name="pydantic_ai.agent.run",
            ),
            _span("first", parent="summary", cost=1.25),
            _span("second", parent="summary", cost=1.25),
        ]
    )

    assert result.rows[0].cost == 2.5


def test_distinct_parent_usage_and_parent_only_prices_remain_additive(fetch_rows):
    result = fetch_rows(
        [
            _span("summary", cost=2.0, kind="chain", name="pydantic_ai.agent.run"),
            _span("child", cost=1.25, parent="summary"),
            _span("parent-only", cost=3.0, kind="chain", name="pydantic_ai.agent.run"),
        ]
    )

    assert result.rows[0].cost == 6.25


def test_cost_read_is_one_project_scoped_lean_batch_for_only_the_page(fetch_rows):
    result = fetch_rows(
        [
            _span("off-page", trace_id="trace-0", cost=99.0),
            _span("shared", trace_id="trace-1", cost=1.25),
            _span("shared", trace_id="trace-1", project_id="other-project", cost=99.0),
            _span("shared", trace_id="trace-2", cost=2.5),
            _span("off-page", trace_id="trace-3", cost=99.0),
        ],
        trace_ids=("trace-0", "trace-1", "trace-2", "trace-3"),
        limit=2,
        offset=1,
    )

    result.reader.list_by_trace_ids.assert_called_once_with(
        ["trace-1", "trace-2"], project_id=PROJECT, include_heavy=False
    )
    assert [(row.id, row.cost) for row in result.rows] == [
        ("trace-1", 1.25),
        ("trace-2", 2.5),
    ]
    assert result.total == 4


def test_missing_cost_rows_do_not_fall_back_to_a_token_estimate(fetch_rows):
    result = fetch_rows([], totals={"trace-1": (150, 1000, 500)})

    assert result.rows[0].cost is None
    assert result.rows[0].tokens == 1500


def test_missing_span_totals_keep_null_cost_without_an_extra_read(fetch_rows):
    result = fetch_rows([])

    assert result.rows[0].cost is None
    result.reader_factory.assert_not_called()


def test_empty_page_does_not_read_cost_spans(fetch_rows):
    result = fetch_rows([_span()], limit=1, offset=1)

    assert result.rows == []
    assert result.total == 1
    result.reader_factory.assert_not_called()
