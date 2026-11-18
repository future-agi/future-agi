"""Pin the cluster-RCA span aliasing contract (no DB).

The agent reads span dicts by key, so the raw-SQL (``_rows_to_dicts``) and
CHSpan-folded (``_chspan_to_agent_dict``) paths must emit the identical key set
— drift = a silently-dropped field.
"""

from datetime import datetime

from tracer.services.clickhouse.cluster_rca_spans import (
    _AGENT_SPAN_KEYS,
    _chspan_to_agent_dict,
)
from tracer.services.clickhouse.v2.span_reader import CHSpan


def _sample_span() -> CHSpan:
    return CHSpan(
        id="11111111-1111-1111-1111-111111111111",
        project_id="22222222-2222-2222-2222-222222222222",
        trace_id="33333333-3333-3333-3333-333333333333",
        parent_span_id="",
        name="llm.call",
        observation_type="llm",
        operation_name="chat",
        start_time=datetime(2026, 6, 22, 1, 2, 3),
        end_time=datetime(2026, 6, 22, 1, 2, 4),
        latency_ms=1000,
        model="gpt",
        provider="openai",
        prompt_tokens=10,
        completion_tokens=20,
        total_tokens=30,
        cost=0.001,
        status="OK",
        status_message="",
        org_id=None,
        project_version_id=None,
        end_user_id=None,
        trace_session_id=None,
        prompt_version_id=None,
        prompt_label_id=None,
        custom_eval_config_id=None,
        input="hi",
        output="hello",
        tags="[]",
        span_events="",
        metadata="{}",
        resource_attrs="{}",
        attributes_extra="{}",
        trace_name="my-trace",
    )


def test_aliasing_produces_exactly_the_agent_key_contract():
    """The CHSpan-folded path emits exactly the keys the raw-SQL path does."""
    d = _chspan_to_agent_dict(_sample_span())
    assert set(d.keys()) == set(_AGENT_SPAN_KEYS)


def test_aliasing_maps_id_trace_name_and_stringifies_times():
    d = _chspan_to_agent_dict(_sample_span())
    assert d["span_id"] == "11111111-1111-1111-1111-111111111111"  # CHSpan.id alias
    assert d["trace_name"] == "my-trace"  # trace-level denormalized field
    assert d["start_time"] == "2026-06-22 01:02:03"  # stringified, prior shape
    assert d["end_time"] == "2026-06-22 01:02:04"
