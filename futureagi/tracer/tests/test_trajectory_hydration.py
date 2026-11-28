"""Unit tests for Phase 0 sentinel compaction, Phase 1 Trajectory Store hydration,
and 040_trajectory_items.sql replicated cluster rewriting.

Runs completely offline (no live ClickHouse or Postgres required).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tracer.services.clickhouse.v2 import (
    adapter,
    apply_schema,
    apply_schema_rewriter,
    physical_span_detail_reads,
    span_reader,
    trace_detail_reads,
    trajectory_hydration,
)
from tracer.services.clickhouse.v2.trajectory_hydration import (
    ENCODING_INLINE,
    ENCODING_ITEMS_V1,
    PROJECT_HASH_PREFIX,
    PROJECT_SCOPE_ID,
    SENTINEL_PROMOTED_INPUT_JSON,
    SENTINEL_PROMOTED_INPUT_KEY,
    SENTINEL_PROMOTED_OUTPUT_JSON,
    SENTINEL_PROMOTED_OUTPUT_KEY,
    TrajectoryContentUnavailable,
    _parse_envelope,
    _resolve_scope_and_hash,
    build_trajectory_items_query,
    hydrate_chspans,
    hydrate_span_dicts,
    payload_checksum,
    reconstruct_payload,
    restore_promoted_in_attributes_extra,
)
from tracer.tests.trajectory_read_state_support import install_default_schema_probe

PROJECT_ID = "11111111-1111-4111-8111-111111111111"
SESSION_ID = "22222222-2222-4222-8222-222222222222"
TRACE_ID = "33333333-3333-4333-8333-333333333333"


@pytest.fixture(autouse=True)
def _scripted_read_state(monkeypatch):
    """The read state follows FI_TRAJECTORY_STORE_READ_ENABLED as before the
    schema probe: a scripted complete-040 schema with no items, so true is
    READY, false NOT_READY, and no probe statement reaches a fake client."""
    install_default_schema_probe(monkeypatch)
    yield
    trajectory_hydration.reset_read_state_cache()


def _h(tag: str) -> str:
    """Deterministic 32-char lowercase hex hash for test fixtures."""
    return (tag.encode().hex() + "0" * 32)[:32]


def _side(
    gaps: list[str], slots: str, contents: list[str], **extra
) -> tuple[dict, str]:
    """A v2 side template plus the original text it rebuilds to."""
    assert len(gaps) == len(slots) + 1 == len(contents) + 1
    original = gaps[0] + "".join(c + g for c, g in zip(contents, gaps[1:], strict=True))
    shape = {
        "g": gaps,
        "r": slots,
        **extra,
        "il": len(original),
        "x": payload_checksum(original),
    }
    return shape, original


def _envelope(**sides) -> str:
    return json.dumps({"v": 2, **sides})


CONTRACT_DIR = Path(__file__).parent / "fixtures" / "trajectory_contract"


def _jsonl(name: str) -> list[dict]:
    with (CONTRACT_DIR / name).open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


# --------------------------------------------------------------------------- #
# 1. Payload reconstruction across provider formats (Go -> Python contract)
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_reconstruct_openai_chat_with_tools_and_choices():
    h_sys, h_u1, h_t1, h_out = _h("sys"), _h("u1"), _h("tool1"), _h("out1")
    sys_c = '{"role":"system","content":"You are a coding agent."}'
    u1_c = '{"role": "user", "content": "Fix the bug"}'
    t1_c = '{"type":"function","function":{"name":"read_file"}}'
    out_c = '{"index":0,"message":{"role":"assistant","content":"Checking file."}}'
    store = {
        (PROJECT_SCOPE_ID, h_sys): sys_c,
        (SESSION_ID, h_u1): u1_c,
        (PROJECT_SCOPE_ID, h_t1): t1_c,
        (SESSION_ID, h_out): out_c,
    }

    def resolve(prefixed: str) -> str | None:
        if prefixed.startswith(PROJECT_HASH_PREFIX):
            return store.get((PROJECT_SCOPE_ID, prefixed[len(PROJECT_HASH_PREFIX) :]))
        return store.get((SESSION_ID, prefixed))

    # Wrapper text, key order and whitespace survive verbatim in the gaps.
    in_shape, in_original = _side(
        [
            '{"model": "gpt-4o", "temperature": 0.20, "tools": [',
            '], "messages": [',
            ",\n  ",
            "]}",
        ],
        "tmm",
        [t1_c, sys_c, u1_c],
    )
    in_json = reconstruct_payload(
        in_shape,
        [f"{PROJECT_HASH_PREFIX}{h_sys}", h_u1],
        [f"{PROJECT_HASH_PREFIX}{h_t1}"],
        resolve,
    )
    assert in_json == in_original
    assert in_json.encode("utf-8") == in_original.encode("utf-8")

    out_shape, out_original = _side(
        ['{"id":"chatcmpl-1","choices":[', "]}"], "m", [out_c]
    )
    assert reconstruct_payload(out_shape, [h_out], None, resolve) == out_original


@pytest.mark.unit
@pytest.mark.parametrize(
    "sys_key,msg_key,model",
    [
        ("system", "messages", "claude-3-7-sonnet"),
        ("instructions", "input", "gpt-5"),
    ],
)
def test_reconstruct_anthropic_and_openai_responses_formats(sys_key, msg_key, model):
    h_sys, h_u1 = _h("s"), _h("u")
    sys_c, u1_c = '"Follow all safety rules."', '{"role":"user","content":"Hello"}'
    store = {f"{PROJECT_HASH_PREFIX}{h_sys}": sys_c, h_u1: u1_c}
    shape, original = _side(
        [f'{{"model":"{model}","{sys_key}":', f',"{msg_key}":[', "]}"],
        "sm",
        [sys_c, u1_c],
        sh=f"{PROJECT_HASH_PREFIX}{h_sys}",
    )
    assert reconstruct_payload(shape, [h_u1], None, store.get) == original

    # An "s" slot needs sh: without it the template is rejected, not guessed.
    no_sh = {k: v for k, v in shape.items() if k != "sh"}
    assert reconstruct_payload(no_sh, [h_u1], None, store.get) is None


@pytest.mark.unit
def test_reconstruct_msg_array_and_missing_item_safety():
    h1, h2 = _h("m1"), _h("m2")
    c1, c2 = (
        '{"role":"developer","content":"Strict"}',
        '{"role":"user","content":"Ping"}',
    )
    store = {f"{PROJECT_HASH_PREFIX}{h1}": c1, h2: c2}
    shape, original = _side(["[", ",", "]"], "mm", [c1, c2])
    assert (
        reconstruct_payload(shape, [f"{PROJECT_HASH_PREFIX}{h1}", h2], None, store.get)
        == original
    )

    # Missing item -> returns None so caller keeps inline preview safely.
    assert reconstruct_payload(shape, [h2, _h("missing")], None, store.get) is None


@pytest.mark.unit
def test_reconstruct_rejects_slot_count_mismatch():
    c = '{"role":"user","content":"x"}'
    shape, _ = _side(["[", "]"], "m", [c])
    resolve = {"a": c, "b": c}.get
    assert reconstruct_payload(shape, ["a", "b"], None, resolve) is None
    assert reconstruct_payload(shape, [], None, resolve) is None
    assert reconstruct_payload(shape, ["a"], ["b"], resolve) is None
    assert reconstruct_payload({**shape, "g": ["["]}, ["a"], None, resolve) is None
    assert reconstruct_payload({**shape, "r": "q"}, ["a"], None, resolve) is None


@pytest.mark.unit
def test_checksum_mismatch_returns_unavailable(settings, monkeypatch):
    settings.FI_TRAJECTORY_STORE_READ_ENABLED = True
    log = MagicMock()
    monkeypatch.setattr(trajectory_hydration, "logger", log)
    h = _h("u1")
    shape, original = _side(["[", "]"], "m", ['{"role":"user","content":"stored"}'])
    tampered = '{"role":"user","content":"STORED"}'
    with pytest.raises(TrajectoryContentUnavailable):
        reconstruct_payload(shape, [h], None, {h: tampered}.get)

    rows = [
        {
            "project_id": PROJECT_ID,
            "id": "span-x",
            "input": "preview",
            "output": "out",
            "input_item_hashes": [h],
            "output_item_hashes": [],
            "tool_def_hashes": [],
            "item_scope_id": SESSION_ID,
            "payload_encoding": ENCODING_ITEMS_V1,
            "payload_envelope": _envelope(**{"in": shape}),
        }
    ]

    def fake_query(_sql, _params):
        return [{"scope_id": SESSION_ID, "item_hash": h, "content": tampered}]

    hydrate_span_dicts(rows, fake_query)
    # The preview stays; a wrong rebuild is never shown as the full content.
    assert rows[0]["input"] == "preview"
    messages = [c.args[0] for c in log.warning.call_args_list]
    # Logged once, under its own reason; not again as missing items.
    assert len(messages) == 1, messages
    assert messages[0].startswith("trajectory_hydration_checksum_mismatch")
    assert original != tampered


def _failing_side_rows() -> tuple[list[dict], list[dict], str]:
    """Dict rows whose input side cannot rebuild, one per failure reason.

    Each row carries a promoted input sentinel; the output side is inline.
    Returns the rows, the item rows a query returns and the stored content.
    """
    h = _h("u1")
    stored = '{"role":"user","content":"stored"}'
    shape, _original = _side(["[", "]"], "m", [stored])
    extra = json.dumps({"keep": 1, SENTINEL_PROMOTED_INPUT_KEY: "input.value"})

    def row(span_id: str, **overrides) -> dict:
        return {
            "project_id": PROJECT_ID,
            "id": span_id,
            "input": "preview",
            "output": "inline out",
            "input_item_hashes": [h],
            "output_item_hashes": [],
            "tool_def_hashes": [],
            "item_scope_id": SESSION_ID,
            "payload_encoding": ENCODING_ITEMS_V1,
            "payload_envelope": _envelope(**{"in": shape}),
            "span_attributes": extra,
            **overrides,
        }

    rows = [
        # Two m-slots for one hash: _collect_required_refs returns no refs.
        row(
            "bad-count",
            payload_envelope=_envelope(
                **{"in": {**shape, "r": "mm", "g": ["[", ",", "]"]}}
            ),
        ),
        # An s-slot without sh.
        row(
            "no-sh",
            payload_envelope=_envelope(**{"in": {**shape, "r": "s"}}),
            input_item_hashes=[],
        ),
        row("missing", input_item_hashes=[_h("absent")]),
        row("tampered"),
        # items_v1 with an envelope the reader rejects.
        row("v1", payload_envelope=json.dumps({"v": 1, "in": {"f": "msg_array"}})),
    ]
    items = [
        {
            "scope_id": SESSION_ID,
            "item_hash": h,
            "content": stored.replace("stored", "STORED"),
        }
    ]
    return rows, items, stored


@pytest.mark.unit
def test_failed_side_logs_once_per_reason_and_keeps_preview(settings, monkeypatch):
    settings.FI_TRAJECTORY_STORE_READ_ENABLED = True
    log = MagicMock()
    monkeypatch.setattr(trajectory_hydration, "logger", log)
    rows, items, _stored = _failing_side_rows()
    calls = []

    def fake_query(sql, params):
        calls.append(params)
        return items

    hydrate_span_dicts(rows, fake_query)
    assert len(calls) == 1
    assert [r["input"] for r in rows] == ["preview"] * len(rows)
    assert [r["output"] for r in rows] == ["inline out"] * len(rows)
    events = [
        (c.args[0].split(" ", 1)[0], c.args[3])
        for c in log.warning.call_args_list
        if c.args[0].startswith("trajectory_hydration_")
    ]
    # Malformed templates are logged, not silently served as previews, and
    # every failure is logged exactly once.
    assert sorted(events) == sorted(
        [
            ("trajectory_hydration_invalid_template", "bad-count"),
            ("trajectory_hydration_invalid_template", "no-sh"),
            ("trajectory_hydration_missing_items", "missing"),
            ("trajectory_hydration_checksum_mismatch", "tampered"),
        ]
    )


@pytest.mark.unit
def test_promoted_attribute_is_never_restored_from_a_preview(settings):
    settings.FI_TRAJECTORY_STORE_READ_ENABLED = True
    rows, items, _stored = _failing_side_rows()
    hydrate_span_dicts(rows, lambda _sql, _params: items)
    for row in rows:
        attrs = json.loads(row["span_attributes"])
        assert attrs == {"keep": 1}, (row["id"], attrs)

    # The same rows as CHSpan instances.
    rows, items, _stored = _failing_side_rows()
    spans = [
        span_reader.CHSpan(
            id=row["id"],
            project_id=PROJECT_ID,
            trace_id=TRACE_ID,
            parent_span_id="",
            name="llm.call",
            observation_type="llm",
            operation_name="chat",
            start_time=datetime(2026, 11, 22, 0, 0, tzinfo=UTC),
            end_time=datetime(2026, 11, 22, 0, 0, 1, tzinfo=UTC),
            latency_ms=1000,
            model="gpt-4o",
            provider="openai",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            cost=0.001,
            status="OK",
            status_message="",
            org_id=None,
            project_version_id=None,
            end_user_id=None,
            trace_session_id=SESSION_ID,
            prompt_version_id=None,
            prompt_label_id=None,
            custom_eval_config_id=None,
            input=row["input"],
            output=row["output"],
            tags="[]",
            span_events="[]",
            metadata="{}",
            resource_attrs="{}",
            attributes_extra=row["span_attributes"],
            input_item_hashes=tuple(row["input_item_hashes"]),
            output_item_hashes=(),
            tool_def_hashes=(),
            item_scope_id=SESSION_ID,
            payload_encoding=ENCODING_ITEMS_V1,
            payload_envelope=row["payload_envelope"],
        )
        for row in rows
    ]
    tuple_items = [(i["scope_id"], i["item_hash"], i["content"]) for i in items]
    for span in hydrate_chspans(spans, lambda _sql, _params: tuple_items):
        assert span.input == "preview", span.id
        assert json.loads(span.attributes_extra) == {"keep": 1}, span.id

    # An inline side next to a failed one is still restored.
    row = _failing_side_rows()[0][3]
    row["span_attributes"] = json.dumps(
        {
            SENTINEL_PROMOTED_INPUT_KEY: "input.value",
            SENTINEL_PROMOTED_OUTPUT_KEY: "output.value",
        }
    )
    hydrate_span_dicts([row], lambda _sql, _params: items)
    assert json.loads(row["span_attributes"]) == {"output.value": "inline out"}


@pytest.mark.unit
def test_envelope_version_other_than_2_is_rejected(monkeypatch):
    log = MagicMock()
    monkeypatch.setattr(trajectory_hydration, "logger", log)
    assert _parse_envelope(json.dumps({"v": 1, "in": {"f": "msg_array"}})) is None
    assert _parse_envelope(json.dumps({"in": {"g": ["[", "]"], "r": ""}})) is None
    messages = [c.args[0] for c in log.warning.call_args_list]
    assert sum("trajectory_envelope_version_rejected" in m for m in messages) == 2
    assert _parse_envelope(_envelope(**{"in": {"g": [""], "r": ""}})) is not None


@pytest.mark.unit
def test_reconstruct_payload_v2_golden():
    """Go writes the contract fixtures from the real converter
    (fi-collector TestTrajectoryContractFixturesUpToDate); every extracted
    side must rebuild to the exact dedup-off bytes."""
    payloads = _jsonl("payloads.jsonl")
    spans = {row["id"]: row for row in _jsonl("spans.jsonl")}
    items = {
        (row["project_id"], row["scope_id"], row["item_hash"]): row["content"]
        for row in _jsonl("items.jsonl")
    }
    assert len(payloads) >= 15
    rebuilt = 0
    for case in payloads:
        row = spans[case["span_id"]]
        env = _parse_envelope(row.get("payload_envelope"))
        scope = row.get("item_scope_id") or ""

        def resolve(ref: str, row=row, scope=scope) -> str | None:
            s, h = _resolve_scope_and_hash(ref, scope)
            return items.get((row["project_id"], s, h))

        for side, col, hashes_col, tools_col in (
            ("in", "input", "input_item_hashes", "tool_def_hashes"),
            ("out", "output", "output_item_hashes", None),
        ):
            want = case[col]
            if not case[f"{col}_extracted"]:
                assert row[col] == want, (case["case"], side)
                assert env is None or env.get(side) is None, (case["case"], side)
                continue
            assert env is not None and env.get(side) is not None, case["case"]
            got = reconstruct_payload(
                env[side],
                row[hashes_col],
                row[tools_col] if tools_col else None,
                resolve,
            )
            assert got == want, (case["case"], side)
            assert got.encode("utf-8") == want.encode("utf-8"), (case["case"], side)
            assert env[side]["il"] == len(want), (case["case"], side)
            rebuilt += 1
    assert rebuilt >= 20
    # JSON escapes reach the reader as the six-character sequences and come
    # back unchanged (fixture case unicode_escape_sequences).
    escaped = [c for c in payloads if c["case"] == "unicode_escape_sequences"]
    assert len(escaped) == 1 and escaped[0]["input_extracted"]
    for escape in ("\\u00e9", "\\ud83d\\ude80", "\\u2028"):
        assert escape in escaped[0]["input"], escape
    assert {c["case"] for c in payloads if not c["input_extracted"]} >= {
        "non_utf8_stays_inline",
        "plain_text_both_sides",
    }


# --------------------------------------------------------------------------- #
# 2. Zero-query guarantee on inline spans + single batched query on mixed trace
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_hydrate_span_dicts_zero_queries_for_inline_and_single_batch_for_mixed(
    settings,
):
    settings.FI_TRAJECTORY_STORE_READ_ENABLED = True
    query_fn = MagicMock()
    inline_rows = [
        {
            "project_id": PROJECT_ID,
            "id": "root",
            "input": "hello",
            "output": "world",
            "payload_encoding": ENCODING_INLINE,
            "span_attributes": '{"custom":"ok"}',
        },
        {
            "project_id": PROJECT_ID,
            "id": "legacy",
            "input": "legacy-in",
            "output": "legacy-out",
            # No payload_encoding column at all (pre-040 fixture)
        },
    ]
    hydrate_span_dicts(inline_rows, query_fn)
    query_fn.assert_not_called()

    # Now test a mixed batch (1 inline + 1 items_v1 with Phase 0 sentinels + bytes hash).
    h_sys, h_u1, h_out = _h("sys"), _h("u1"), _h("out")
    calls = []

    def fake_query(sql, params):
        calls.append((sql, params))
        return [
            {
                "scope_id": PROJECT_SCOPE_ID,
                # ClickHouse FixedString(32) may return bytes
                "item_hash": h_sys.encode("ascii"),
                "content": '{"role":"system","content":"Be brief."}',
            },
            {
                "scope_id": SESSION_ID,
                "item_hash": h_u1,
                "content": '{"role":"user","content":"Question?"}',
            },
            {
                "scope_id": SESSION_ID,
                "item_hash": h_out,
                "content": '{"role":"assistant","content":"Answer!"}',
            },
        ]

    in_shape, in_original = _side(
        ['{"model": "gpt-4o",\n "messages": [', ", ", "]}"],
        "mm",
        [
            '{"role":"system","content":"Be brief."}',
            '{"role":"user","content":"Question?"}',
        ],
    )
    out_shape, out_original = _side(
        ["[", "]"], "m", ['{"role":"assistant","content":"Answer!"}']
    )
    env = _envelope(**{"in": in_shape, "out": out_shape})
    mixed_rows = [
        dict(inline_rows[0]),
        {
            "project_id": PROJECT_ID,
            "id": "llm-1",
            "input": "preview-in...",
            "output": "preview-out...",
            "input_item_hashes": [f"{PROJECT_HASH_PREFIX}{h_sys}", h_u1],
            "output_item_hashes": [h_out],
            "tool_def_hashes": [],
            "item_scope_id": SESSION_ID,
            "payload_encoding": ENCODING_ITEMS_V1,
            "payload_envelope": env,
            "span_attributes": json.dumps(
                {
                    "custom": "v",
                    SENTINEL_PROMOTED_INPUT_KEY: "input.value",
                    SENTINEL_PROMOTED_OUTPUT_KEY: "output.value",
                }
            ),
        },
    ]
    hydrate_span_dicts(mixed_rows, fake_query)
    assert len(calls) == 1
    assert "FROM trajectory_items" in calls[0][0]
    assert set(calls[0][1]["traj_scope_ids"]) == {PROJECT_SCOPE_ID, SESSION_ID}

    hydrated = mixed_rows[1]
    assert hydrated["input"] == in_original
    assert hydrated["output"] == out_original
    # Phase 0 sentinels popped and replaced with the hydrated full payload!
    attrs = json.loads(hydrated["span_attributes"])
    assert SENTINEL_PROMOTED_INPUT_KEY not in attrs
    assert SENTINEL_PROMOTED_OUTPUT_KEY not in attrs
    assert attrs["input.value"] == hydrated["input"]
    assert attrs["output.value"] == hydrated["output"]
    assert attrs["custom"] == "v"


# --------------------------------------------------------------------------- #
# 3. Phase 0 sentinel restoration (string vs JSON-decoded map/list)
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_merge_span_attributes_and_chspan_restore_phase0_sentinels(settings):
    settings.FI_TRAJECTORY_STORE_READ_ENABLED = True
    extra = json.dumps(
        {
            "keep": 1,
            SENTINEL_PROMOTED_INPUT_KEY: "input.value",
            SENTINEL_PROMOTED_INPUT_JSON: True,
            SENTINEL_PROMOTED_OUTPUT_KEY: "output.value",
        }
    )
    merged = span_reader.merge_span_attributes(
        {"a": "b"},
        {"n": 2.0},
        {"flag": 1},
        extra,
        span_input='{"messages":[{"role":"user","content":"hi"}]}',
        span_output="plain answer",
    )
    assert SENTINEL_PROMOTED_INPUT_KEY not in merged
    assert SENTINEL_PROMOTED_INPUT_JSON not in merged
    assert SENTINEL_PROMOTED_OUTPUT_KEY not in merged
    assert merged["input.value"] == {"messages": [{"role": "user", "content": "hi"}]}
    assert merged["output.value"] == "plain answer"
    assert merged["keep"] == 1 and merged["a"] == "b" and merged["flag"] is True

    # Fast-path untouched string when no sentinel exists
    raw = '{"plain":"no_sentinel"}'
    assert restore_promoted_in_attributes_extra(raw, span_input="x") is raw

    # Read flag off: a customer key that shares a sentinel name keeps its value.
    settings.FI_TRAJECTORY_STORE_READ_ENABLED = False
    assert restore_promoted_in_attributes_extra(extra, span_input="x") is extra
    off = span_reader.merge_span_attributes(
        {}, {}, {}, extra, span_input="in", span_output="out"
    )
    assert off[SENTINEL_PROMOTED_INPUT_KEY] == "input.value"
    assert "output.value" not in off


# --------------------------------------------------------------------------- #
# 4. Integration with trace_detail_reads, physical_span_detail_reads, CHSpanReader
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_read_trace_detail_and_physical_span_detail_hydrate_items_v1(settings):
    settings.FI_TRAJECTORY_STORE_READ_ENABLED = True
    now = datetime(2026, 11, 20, 12, 15, tzinfo=UTC)
    hour = now.replace(minute=0, second=0, microsecond=0)
    h_u1 = _h("u1")
    in_shape, expected_in = _side(
        ["[", "]"], "m", ['{"role":"user","content":"Hydrated question"}']
    )
    env = _envelope(**{"in": in_shape})

    span_row = {
        "project_id": PROJECT_ID,
        "trace_id": TRACE_ID,
        "id": "span-1",
        "observation_type": "llm",
        "service_name": "agent",
        "start_time": now,
        "start_hour": hour,
        "_version": "7",
        "is_deleted": 0,
        "parent_span_id": "",
        "name": "llm.call",
        "end_time": now + timedelta(seconds=1),
        "input": "preview...",
        "output": "done",
        "model": "gpt-4o",
        "latency_ms": 1000,
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "cost": 0.001,
        "status": "OK",
        "status_message": "",
        "tags": "[]",
        "span_events": "[]",
        "provider": "openai",
        "span_attributes": json.dumps({SENTINEL_PROMOTED_INPUT_KEY: "input.value"}),
        "project_version_id": None,
        "custom_eval_config_id": None,
        "trace_session_id": SESSION_ID,
        "metadata_json": "{}",
        "resource_attrs": "{}",
        "attrs_string": {},
        "attrs_number": {},
        "attrs_bool": {},
        "input_item_hashes": [h_u1],
        "output_item_hashes": [],
        "tool_def_hashes": [],
        "item_scope_id": SESSION_ID,
        "payload_encoding": ENCODING_ITEMS_V1,
        "payload_envelope": env,
    }

    class _FakeAnalytics:
        def __init__(self):
            self.queries = []

        def execute_ch_query(self, query, params=None, **kwargs):
            self.queries.append((query, params))
            if "FROM trajectory_items" in query:
                return SimpleNamespace(
                    data=[
                        {
                            "scope_id": SESSION_ID,
                            "item_hash": h_u1,
                            "content": '{"role":"user","content":"Hydrated question"}',
                        }
                    ],
                    complete=True,
                )
            if "latest_physical_spans" in query:
                return SimpleNamespace(data=[dict(span_row)], complete=True)
            if "AS span_id" in query:
                return SimpleNamespace(
                    data=[
                        {
                            "project_id": PROJECT_ID,
                            "trace_id": TRACE_ID,
                            "span_id": "span-1",
                            "start_time": now,
                            "latest_is_deleted": 0,
                            "latest_version": 7,
                        }
                    ],
                    complete=True,
                )
            if "FROM spans FINAL" in query:
                return SimpleNamespace(data=[dict(span_row)], complete=True)
            return SimpleNamespace(data=[], complete=True)

    # trace_detail_reads
    analytics = _FakeAnalytics()
    detail = trace_detail_reads.read_trace_detail(
        analytics=analytics,
        project_ids=[PROJECT_ID],
        trace_id=TRACE_ID,
        include_annotations=False,
    )
    assert len(detail.spans) == 1
    assert detail.spans[0]["input"] == expected_in
    assert json.loads(detail.spans[0]["span_attributes"]) == {
        "input.value": detail.spans[0]["input"]
    }
    assert any("FROM trajectory_items" in q for q, _ in analytics.queries)

    # physical_span_detail_reads
    phys_analytics = _FakeAnalytics()
    phys = physical_span_detail_reads.read_physical_span_detail(
        analytics=phys_analytics,
        span_id="span-1",
        reference={
            "project_id": PROJECT_ID,
            "trace_id": TRACE_ID,
            "start_hour": hour,
            "observation_type": "llm",
            "service_name": "agent",
            "expected_start_time": now,
            "expected_version": "7",
        },
        authorized_project_id=PROJECT_ID,
    )
    assert phys["input"] == expected_in
    assert json.loads(phys["span_attributes"]) == {"input.value": phys["input"]}


@pytest.mark.unit
def test_ch_span_reader_hydrates_get_list_and_export(settings):
    settings.FI_TRAJECTORY_STORE_READ_ENABLED = True
    h_u1 = _h("u1")
    in_shape, expected_in = _side(
        ["[", "]"], "m", ['{"role":"user","content":"Full eval prompt"}']
    )
    env = _envelope(**{"in": in_shape})
    now = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)

    row_map = dict.fromkeys(span_reader._TRAJECTORY_DATA_KEYS, "")
    row_map.update(
        {
            "id": "sp-1",
            "project_id": PROJECT_ID,
            "trace_id": TRACE_ID,
            "start_time": now,
            "end_time": now,
            "latency_ms": 50,
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
            "cost": 0.0,
            "input": "preview...",
            "output": "ok",
            "attributes_extra": json.dumps(
                {SENTINEL_PROMOTED_INPUT_KEY: "input.value"}
            ),
            "attrs_string": {},
            "attrs_number": {},
            "attrs_bool": {},
            "is_deleted": 0,
            "created_at": now,
            "input_item_hashes": [h_u1],
            "output_item_hashes": [],
            "tool_def_hashes": [],
            "item_scope_id": SESSION_ID,
            "payload_encoding": ENCODING_ITEMS_V1,
            "payload_envelope": env,
        }
    )
    full_tuple = tuple(row_map[k] for k in span_reader._TRAJECTORY_DATA_KEYS)

    class _FakeClient:
        def __init__(self):
            self.queries = []

        def query(self, sql, parameters=None, settings=None):
            self.queries.append((sql, parameters))
            if "FROM trajectory_items" in sql:
                return SimpleNamespace(
                    result_rows=[
                        (
                            SESSION_ID,
                            h_u1,
                            '{"role":"user","content":"Full eval prompt"}',
                        )
                    ]
                )
            if "GROUP BY id" in sql:
                # export_fields_by_ids query: SELECT id, argMax(..., _version) AS alias, ...
                import re

                # Regression guard: aliasing an aggregate to bare `project_id` breaks
                # ClickHouse 25.3 analyzer on `PREWHERE project_id = ...` (Code 184).
                assert "AS project_id," not in sql and "AS project_id " not in sql
                cols_sql = sql.split("SELECT ", 1)[1].split(" FROM spans", 1)[0]
                aliases = ["id", *re.findall(r"\bAS\s+(\w+)", cols_sql)]
                lookup = {**row_map, "project_id_str": PROJECT_ID}
                return SimpleNamespace(
                    result_rows=[tuple(lookup.get(a, "") for a in aliases)]
                )
            return SimpleNamespace(result_rows=[full_tuple])

    reader = span_reader.CHSpanReader.__new__(span_reader.CHSpanReader)
    reader._client = _FakeClient()

    sp = reader.get("sp-1", project_id=PROJECT_ID)
    assert sp is not None
    assert sp.input == expected_in
    # eval_loader consumes _ch_span_attributes(sp) -> Phase 0 sentinel restored with hydrated input!
    attrs = span_reader._ch_span_attributes(sp)
    assert attrs["input.value"] == sp.input
    assert SENTINEL_PROMOTED_INPUT_KEY not in attrs

    trace_spans = reader.list_by_trace(TRACE_ID, project_id=PROJECT_ID)
    assert len(trace_spans) == 1 and trace_spans[0].input == expected_in

    id_spans = reader.list_by_ids(["sp-1"], project_id=PROJECT_ID)
    assert len(id_spans) == 1 and id_spans[0].input == expected_in

    exported = reader.export_fields_by_ids(
        ["sp-1"], {"input", "span_attributes"}, project_id=PROJECT_ID
    )
    assert exported["sp-1"]["input"] == expected_in
    assert json.loads(exported["sp-1"]["attributes_extra"]) == {
        "input.value": exported["sp-1"]["input"]
    }
    # Export dict must not leak internal pointer columns that weren't requested.
    assert "payload_encoding" not in exported["sp-1"]


# --------------------------------------------------------------------------- #
# 5. Phase 0 adapter.py lockstep checks + 040 replicated cluster rewriter
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_adapter_split_attributes_preserves_dev_routing_by_default():
    # Blocker #5 guard: default split_attributes must keep Error Feed, scalar
    # filters (gen_ai.completion.0.finish_reason), and numeric raw.* dashboards
    # in their typed Maps (exact dev routing).
    large_str = "b" * 20_000
    s, n, b, extra = adapter.split_attributes(
        {
            "llm.input_messages.0.message.content": "hello",
            "llm.tools.0.tool.json_schema": "{}",
            "gen_ai.completion.0.finish_reason": "length",
            "raw.latency_ms": 12.5,
            "custom.large": large_str,
            "input.value": '{"messages":[]}',
            "output.value": '{"choices":[]}',
        }
    )
    for k in (
        "llm.input_messages.0.message.content",
        "llm.tools.0.tool.json_schema",
        "gen_ai.completion.0.finish_reason",
        "custom.large",
    ):
        assert k in s and k not in extra
    assert n == {"raw.latency_ms": 12.5}
    assert b == {}
    assert "input.value" in extra and "output.value" in extra


@pytest.mark.unit
def test_migration_028_replicated_rewriter_and_no_ttl():
    from tracer.services.clickhouse import oss_cdc_bootstrap as cdc

    schema_path = (
        Path(apply_schema.__file__).parent / "schema" / "040_trajectory_items.sql"
    )
    raw = schema_path.read_text()
    stmts = apply_schema.split_statements(raw)
    assert len(stmts) == 2
    for stmt in stmts:
        tokens = [t.upper() for t in cdc._tokens(stmt)]
        assert "TTL" not in tokens, "cold storage / TTL must not be in 028"

    rewritten_create = apply_schema_rewriter.rewrite_for_replicated(
        stmts[0],
        table_name=apply_schema_rewriter.extract_table_name(stmts[0]),
        cluster="fi_cluster",
        zk_prefix="/clickhouse/tables/{cluster}",
    )
    assert (
        "CREATE TABLE IF NOT EXISTS trajectory_items ON CLUSTER 'fi_cluster'"
        in rewritten_create
    )
    assert (
        "ReplicatedReplacingMergeTree('/clickhouse/tables/{cluster}/{shard}/trajectory_items', '{replica}', persisted_at)"
        in rewritten_create
    )

    rewritten_alter = apply_schema_rewriter.rewrite_for_replicated(
        stmts[1],
        table_name=apply_schema_rewriter.extract_table_name(stmts[1]),
        cluster="fi_cluster",
        zk_prefix="/clickhouse/tables/{cluster}",
    )
    assert "ALTER TABLE spans ON CLUSTER 'fi_cluster'" in rewritten_alter
    for col in (
        "input_item_hashes",
        "output_item_hashes",
        "tool_def_hashes",
        "item_scope_id",
        "payload_encoding",
        "payload_envelope",
    ):
        assert col in rewritten_alter


@pytest.mark.unit
def test_session_detail_sql_projects_trajectory_pointers_and_list_strips_sentinels(
    settings,
):
    from tracer.services.clickhouse.v2.query_builders.session_detail import (
        build_session_detail_traces_query,
    )
    from tracer.views.trace_session import _merge_session_attribute_sources

    # Disabled (default): zero references to 040 trajectory columns.
    settings.FI_TRAJECTORY_STORE_READ_ENABLED = False
    sql_off, _ = build_session_detail_traces_query(
        PROJECT_ID, (SESSION_ID,), limit=10, offset=0
    )
    for col in (
        "input_item_hashes",
        "output_item_hashes",
        "tool_def_hashes",
        "item_scope_id",
        "payload_encoding",
        "payload_envelope",
    ):
        assert col not in sql_off

    # Enabled: projects all 6 trajectory pointer columns.
    settings.FI_TRAJECTORY_STORE_READ_ENABLED = True
    sql, _params = build_session_detail_traces_query(
        PROJECT_ID, (SESSION_ID,), limit=10, offset=0
    )
    for col in (
        "input_item_hashes",
        "output_item_hashes",
        "tool_def_hashes",
        "item_scope_id",
        "payload_encoding",
        "payload_envelope",
    ):
        assert col in sql

    merged = _merge_session_attribute_sources(
        {
            "span_attributes_raw": json.dumps(
                {
                    "custom.env": "prod",
                    SENTINEL_PROMOTED_INPUT_KEY: "input.value",
                    SENTINEL_PROMOTED_INPUT_JSON: True,
                    SENTINEL_PROMOTED_OUTPUT_KEY: "output.value",
                    SENTINEL_PROMOTED_OUTPUT_JSON: False,
                }
            ),
            "attrs_string": {"team": "payments"},
            "attrs_number": {"score": 9.5},
            "attrs_bool": {"vip": 1},
        }
    )
    # Session attribute aggregation is unchanged from dev: no sentinel handling.
    assert merged == {
        "custom.env": "prod",
        SENTINEL_PROMOTED_INPUT_KEY: "input.value",
        SENTINEL_PROMOTED_INPUT_JSON: True,
        SENTINEL_PROMOTED_OUTPUT_KEY: "output.value",
        SENTINEL_PROMOTED_OUTPUT_JSON: False,
        "team": "payments",
        "score": 9.5,
        "vip": True,
    }


@pytest.mark.unit
def test_trajectory_query_chunking_three_key_pk_and_prewhere():
    from tracer.services.clickhouse.v2.trajectory_hydration import (
        _MAX_PAIRS_PER_QUERY,
        fetch_trajectory_items_dict_rows,
    )

    schema_sql = (
        Path(apply_schema.__file__).parent / "schema" / "040_trajectory_items.sql"
    ).read_text()
    assert "PRIMARY KEY (project_id, scope_id, item_hash)" in schema_sql

    sql, params = build_trajectory_items_query(
        project_id=PROJECT_ID,
        pairs=[(PROJECT_SCOPE_ID, "a" * 32), (SESSION_ID, "b" * 32)],
    )
    assert "PREWHERE project_id = %(traj_project_id)s" in sql
    assert "AND scope_id IN %(traj_scope_ids)s" in sql
    assert "AND (scope_id, item_hash) IN %(traj_pairs)s" in sql
    assert "WHERE item_hash" not in sql
    assert params["traj_pairs"] == (
        (PROJECT_SCOPE_ID, "a" * 32),
        (SESSION_ID, "b" * 32),
    )

    # 3,200 hashes under a single scope must split into 3 bounded queries.
    total = _MAX_PAIRS_PER_QUERY * 2 + 200
    refs = [(PROJECT_ID, PROJECT_SCOPE_ID, f"{i:032x}") for i in range(total)]
    batch_sizes: list[int] = []

    def fake_query(_sql, params):
        pairs = set(params["traj_pairs"])
        batch_sizes.append(len(pairs))
        return [
            {"scope_id": scope, "item_hash": h, "content": f"c:{h}"}
            for scope, h in sorted(pairs)
        ]

    resolved = fetch_trajectory_items_dict_rows(fake_query, refs)
    assert len(batch_sizes) == 3
    assert all(n <= _MAX_PAIRS_PER_QUERY for n in batch_sizes)
    assert len(resolved) == total


@pytest.mark.unit
def test_hydrate_chspans_restores_extra_sentinels_and_prompt_template_metadata_stays_in_attrs_string(
    settings,
):
    settings.FI_TRAJECTORY_STORE_READ_ENABLED = True
    from tracer.services.clickhouse.v2.adapter import split_attributes
    from tracer.services.clickhouse.v2.span_reader import CHSpanReader

    # 1. Scalar gen_ai.prompt.* metadata stays in attrs_string (dev-compatible).
    s, _n, _b, _extra = split_attributes(
        {
            "gen_ai.prompt.name": "checkout-v2",
            "gen_ai.prompt.template.variables": '{"tier":"gold"}',
        }
    )
    assert s["gen_ai.prompt.name"] == "checkout-v2"
    assert s["gen_ai.prompt.template.variables"] == '{"tier":"gold"}'

    # 2. hydrate_chspans & attributes_extra_as_dict restore Phase 0 promoted keys
    #    and never leak __fi_promoted_* sentinels.
    raw_extra = json.dumps(
        {
            "custom": "ok",
            SENTINEL_PROMOTED_INPUT_KEY: "input.value",
            SENTINEL_PROMOTED_INPUT_JSON: True,
        }
    )
    sp = span_reader.CHSpan(
        id="span-sentinel-1",
        project_id=PROJECT_ID,
        trace_id=TRACE_ID,
        parent_span_id="",
        name="llm.call",
        observation_type="llm",
        operation_name="chat",
        start_time=datetime(2026, 11, 22, 0, 0, tzinfo=UTC),
        end_time=datetime(2026, 11, 22, 0, 0, 1, tzinfo=UTC),
        latency_ms=1000,
        model="gpt-4o",
        provider="openai",
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        cost=0.001,
        status="OK",
        status_message="",
        org_id=None,
        project_version_id=None,
        end_user_id=None,
        trace_session_id=SESSION_ID,
        prompt_version_id=None,
        prompt_label_id=None,
        custom_eval_config_id=None,
        input='{"hello":"world"}',
        output="plain answer",
        tags="[]",
        span_events="[]",
        metadata="{}",
        resource_attrs="{}",
        attributes_extra=raw_extra,
        payload_encoding=ENCODING_INLINE,
    )
    hydrated = hydrate_chspans([sp], lambda _q, _p: [])[0]
    parsed = json.loads(hydrated.attributes_extra)
    assert parsed == {"custom": "ok", "input.value": {"hello": "world"}}
    assert CHSpanReader.attributes_extra_as_dict(sp) == {
        "custom": "ok",
        "input.value": {"hello": "world"},
    }


@pytest.mark.unit
def test_dataset_export_child_tree_and_columns_projection_hydrate_items_v1(settings):
    settings.FI_TRAJECTORY_STORE_READ_ENABLED = True
    from tracer.services.clickhouse.v2.span_reader import CHSpanReader

    item_hash = "d" * 32
    full_msg = json.dumps({"role": "user", "content": "Full lossless dataset question"})
    in_shape, expected_in = _side(["[", "]"], "m", [full_msg])
    env_json = _envelope(**{"in": in_shape})
    raw_extra = json.dumps(
        {
            "meta": "keep",
            SENTINEL_PROMOTED_INPUT_KEY: "input.value",
            SENTINEL_PROMOTED_INPUT_JSON: True,
        }
    )

    class _FakeClient:
        def __init__(self):
            self.queries: list[str] = []

        def query(self, sql, parameters=None, settings=None):
            self.queries.append(sql)

            class _Res:
                pass

            res = _Res()
            if "FROM trajectory_items" in sql:
                res.result_rows = [(SESSION_ID, item_hash, full_msg)]
            elif "GROUP BY id, trace_id" in sql:
                # child_tree_spans_by_trace_ids
                res.result_rows = [
                    (
                        "child-1",
                        "trace-1",
                        "root-1",
                        "llm.call",
                        "llm",
                        "chat",
                        "OK",
                        "",
                        "gpt-4o",
                        "openai",
                        "512B preview",
                        "plain out",
                        "{}",
                        {},
                        {},
                        {"flag": 1},
                        raw_extra,
                        10,
                        5,
                        15,
                        50,
                        0.001,
                        "[]",
                        "[]",
                        "2026-11-22 00:00:00",
                        PROJECT_ID,
                        [item_hash],
                        [],
                        [],
                        SESSION_ID,
                        ENCODING_ITEMS_V1,
                        env_json,
                    )
                ]
            elif "GROUP BY id" in sql:
                import re

                cols_sql = sql.split("SELECT ", 1)[1].split(" FROM spans", 1)[0]
                aliases = ["id", *re.findall(r"\bAS\s+(\w+)", cols_sql)]
                lookup = {
                    "id": "span-1",
                    "attributes_extra": raw_extra,
                    "attrs_bool": {"flag": 1},
                    "attrs_number": {},
                    "attrs_string": {"env": "prod"},
                    "input": "512B preview",
                    "output": "plain out",
                    "input_item_hashes": [item_hash],
                    "output_item_hashes": [],
                    "tool_def_hashes": [],
                    "item_scope_id": SESSION_ID,
                    "payload_encoding": ENCODING_ITEMS_V1,
                    "payload_envelope": env_json,
                    "project_id_str": PROJECT_ID,
                    "created_at": datetime(2026, 11, 20, 12, 0, tzinfo=UTC),
                }
                res.result_rows = [tuple(lookup[a] for a in aliases)]
            else:
                # list_by_ids(columns=["id", "input"])
                res.result_rows = [
                    (
                        "span-1",
                        "512B preview",
                        PROJECT_ID,
                        [item_hash],
                        [],
                        [],
                        SESSION_ID,
                        ENCODING_ITEMS_V1,
                        env_json,
                    )
                ]
            return res

    client = _FakeClient()
    reader = CHSpanReader.__new__(CHSpanReader)
    reader._client = client

    # 1. export_fields_by_ids with ONLY {"span_attributes"} must NOT select
    #    input/output in the initial query (Blocker #6c), and still restores
    #    promoted input.value via targeted follow-up when a sentinel is present.
    exported = reader.export_fields_by_ids(
        ["span-1"], {"span_attributes"}, project_id=PROJECT_ID
    )
    assert "argMax(input," not in client.queries[0]
    assert "argMax(output," not in client.queries[0]
    assert "input" not in exported["span-1"]
    restored_extra = json.loads(exported["span-1"]["attributes_extra"])
    assert restored_extra["meta"] == "keep"
    assert restored_extra["input.value"] == [json.loads(full_msg)]
    assert not any(k.startswith("__fi_promoted_") for k in restored_extra)

    # 2. child_tree_spans_by_trace_ids hydrates items_v1 child spans & strips helper cols.
    tree = reader.child_tree_spans_by_trace_ids(["trace-1"], project_id=PROJECT_ID)
    child = tree["trace-1"][0]
    assert child["input"] == expected_in
    assert "payload_encoding" not in child
    assert json.loads(child["attributes_extra"])["input.value"] == [
        json.loads(full_msg)
    ]

    # 3. list_by_ids(columns=["id", "input"]) hydrates items_v1 and keeps exact keys.
    proj_rows = reader.list_by_ids(
        ["span-1"], project_id=PROJECT_ID, columns=["id", "input"]
    )
    assert proj_rows == [{"id": "span-1", "input": expected_in}]


# --------------------------------------------------------------------------- #
# 6. Pre-040 deploy safety (Blocker #4), SQL LIMIT BY (Blocker #6),
#    eval_loader ValueError/RecursionError (Blocker #6b), export cost (#6c),
#    and retention/project deletion (Blocker #9)
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_disabled_mode_sql_has_zero_trajectory_columns(settings, monkeypatch):
    from ee.agenthub.insights.evidence import EvidenceQueryBuilder
    from tracer.services.clickhouse import cluster_rca_spans
    from tracer.services.clickhouse.v2.query_builders.session_detail import (
        build_session_detail_traces_query,
    )
    from tracer.services.clickhouse.v2.query_builders.span_list import (
        SpanListQueryBuilderV2,
    )
    from tracer.services.clickhouse.v2.trajectory_hydration import (
        TRAJECTORY_SPAN_COLUMNS,
    )

    settings.FI_TRAJECTORY_STORE_READ_ENABLED = False
    now = datetime(2026, 11, 20, 12, 15, tzinfo=UTC)
    hour = now.replace(minute=0, second=0, microsecond=0)
    ident = trace_detail_reads.PhysicalSpanIdentity(
        project_id=PROJECT_ID, trace_id=TRACE_ID, span_id="sp-1", start_time=now
    )

    sqls: list[tuple[str, str]] = []

    # 1. trace_detail_reads
    td_sql, _ = trace_detail_reads.TraceDetailReadBuilder(
        project_ids=[PROJECT_ID], trace_id=TRACE_ID
    ).build_content_query([ident])
    sqls.append(("trace_detail_reads", td_sql))

    # 2. physical_span_detail_reads
    phys_sql, _ = physical_span_detail_reads.build_physical_span_detail_query(
        span_id="sp-1",
        reference={
            "project_id": PROJECT_ID,
            "trace_id": TRACE_ID,
            "start_hour": hour,
            "observation_type": "llm",
            "service_name": "agent",
        },
    )
    sqls.append(("physical_span_detail_reads", phys_sql))

    # 3. session_detail
    sd_sql, _ = build_session_detail_traces_query(
        PROJECT_ID, (SESSION_ID,), limit=10, offset=0
    )
    sqls.append(("session_detail", sd_sql))

    # 4. span_list v2
    sl_sql, _ = SpanListQueryBuilderV2(
        project_ids=[PROJECT_ID],
        start_date=now - timedelta(hours=1),
        end_date=now,
    ).build_content_query(["sp-1"])
    sqls.append(("span_list_v2", sl_sql))

    # 5. insights evidence
    ev_sql, _ = EvidenceQueryBuilder(project_id=PROJECT_ID).content_query(
        [ident], heavy_chars=4096, status_chars=512
    )
    sqls.append(("insights_evidence", ev_sql))

    # 6. CHSpanReader (get, list_by_trace, activity_by_trace_ids, export_fields_by_ids, child_tree)
    captured_reader_sql: list[tuple[str, str]] = []

    class _RecordingClient:
        def query(self, sql, parameters=None, settings=None):
            captured_reader_sql.append(("span_reader", sql))
            return SimpleNamespace(result_rows=[])

    reader = span_reader.CHSpanReader.__new__(span_reader.CHSpanReader)
    reader._client = _RecordingClient()
    reader.get("sp-1", project_id=PROJECT_ID)
    reader.list_by_trace(TRACE_ID, project_id=PROJECT_ID)
    reader.activity_by_trace_ids(
        [TRACE_ID], project_id=PROJECT_ID, media_patterns=("img", "aud"), per_trace=25
    )
    reader.export_fields_by_ids(["sp-1"], {"span_attributes"}, project_id=PROJECT_ID)
    reader.child_tree_spans_by_trace_ids([TRACE_ID], project_id=PROJECT_ID)
    sqls.extend(captured_reader_sql)

    # Verify Blocker #6: activity_by_trace_ids keeps SQL LIMIT %(per)s BY trace_id
    activity_sql = captured_reader_sql[2][1]
    assert "LIMIT %(per)s BY trace_id" in activity_sql

    # Verify Blocker #6c: span_attributes-only export does NOT read input/output
    export_sql = captured_reader_sql[3][1]
    assert "argMax(input," not in export_sql
    assert "argMax(output," not in export_sql

    # 7. cluster_rca_spans
    captured_rca_sql: list[tuple[str, str]] = []
    monkeypatch.setattr(
        cluster_rca_spans,
        "_execute_read",
        lambda q, _p: captured_rca_sql.append(("cluster_rca_spans", q)) or [],
    )
    cluster_rca_spans.list_spans_in_traces(PROJECT_ID, [TRACE_ID])
    cluster_rca_spans.search_spans_in_traces(PROJECT_ID, [TRACE_ID], "needle")
    cluster_rca_spans.trace_roots(PROJECT_ID, [TRACE_ID])
    cluster_rca_spans.traces_in_session(PROJECT_ID, SESSION_ID)
    sqls.extend(captured_rca_sql)

    for label, sql in sqls:
        for col in TRAJECTORY_SPAN_COLUMNS:
            assert col not in sql, f"{label} referenced {col} while disabled"


@pytest.mark.unit
def test_activity_by_trace_ids_retains_sql_limit_by_when_enabled(settings):
    settings.FI_TRAJECTORY_STORE_READ_ENABLED = True
    seen_sql: list[tuple[str, dict]] = []

    class _RecordingClient:
        def query(self, sql, parameters=None, settings=None):
            seen_sql.append((sql, dict(parameters or {})))
            return SimpleNamespace(result_rows=[])

    reader = span_reader.CHSpanReader.__new__(span_reader.CHSpanReader)
    reader._client = _RecordingClient()
    reader.activity_by_trace_ids(
        [TRACE_ID], project_id=PROJECT_ID, media_patterns=("img", "aud"), per_trace=42
    )
    assert len(seen_sql) == 1
    assert "LIMIT %(per)s BY trace_id" in seen_sql[0][0]
    assert seen_sql[0][1]["per"] == 42


@pytest.mark.unit
def test_eval_loader_handles_value_error_on_huge_int_json():
    from tracer.services.clickhouse.v2.eval_loader import _construct_from_chspan

    huge_int = "1" * 5000
    sp = span_reader.CHSpan(
        id="sp-huge",
        project_id=PROJECT_ID,
        trace_id=TRACE_ID,
        parent_span_id="",
        name="llm",
        observation_type="llm",
        operation_name="chat",
        start_time=datetime(2026, 11, 22, 0, 0, tzinfo=UTC),
        end_time=datetime(2026, 11, 22, 0, 0, 1, tzinfo=UTC),
        latency_ms=1000,
        model="gpt-4o",
        provider="openai",
        prompt_tokens=1,
        completion_tokens=1,
        total_tokens=2,
        cost=0.0,
        status="OK",
        status_message="",
        org_id=None,
        project_version_id=None,
        end_user_id=None,
        trace_session_id=None,
        prompt_version_id=None,
        prompt_label_id=None,
        custom_eval_config_id=None,
        input=huge_int,
        output=huge_int,
        tags="[]",
        span_events="[]",
        metadata="{}",
        resource_attrs="{}",
        attributes_extra="{}",
    )
    obs = _construct_from_chspan(sp)
    assert obs.input == huge_int
    assert obs.output == huge_int


@pytest.mark.unit
def test_bounded_export_content_queries_gate_trajectory_columns(settings):
    """Export content queries include trajectory columns only when export + flag are ON."""
    from datetime import UTC, datetime

    from tracer.services.clickhouse.v2.query_builders.span_list import (
        SpanListQueryBuilderV2,
    )
    from tracer.services.clickhouse.v2.query_builders.trace_list import (
        TraceListQueryBuilderV2,
    )

    pid = PROJECT_ID
    tid = "00000000-0000-0000-0000-000000000010"
    sid = "abcdef0123456789"
    ts_us = int(datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC).timestamp() * 1_000_000)

    span_builder = SpanListQueryBuilderV2(project_id=pid)
    trace_builder = TraceListQueryBuilderV2(project_id=pid)
    root_id = (pid, tid, sid, ts_us, "llm", "svc", ts_us, 1)

    # Flag OFF -> zero trajectory columns even when include_trajectory=True.
    settings.FI_TRAJECTORY_STORE_READ_ENABLED = False
    sql_span_off, _ = span_builder.build_content_query([sid], include_trajectory=True)
    sql_trace_off, _ = trace_builder.build_content_query(
        [tid], root_identities=[root_id], include_trajectory=True
    )
    assert "payload_encoding" not in sql_span_off
    assert "payload_encoding" not in sql_trace_off

    # Flag ON, default list view (include_trajectory=False) -> zero trajectory columns.
    settings.FI_TRAJECTORY_STORE_READ_ENABLED = True
    sql_span_list, _ = span_builder.build_content_query([sid])
    sql_trace_list, _ = trace_builder.build_content_query(
        [tid], root_identities=[root_id]
    )
    assert "payload_encoding" not in sql_span_list
    assert "payload_encoding" not in sql_trace_list

    # Flag ON + include_trajectory=True (bounded CSV export) -> all 6 trajectory columns.
    sql_span_exp, _ = span_builder.build_content_query([sid], include_trajectory=True)
    sql_trace_exp, _ = trace_builder.build_content_query(
        [tid], root_identities=[root_id], include_trajectory=True
    )
    for col in ("input_item_hashes", "output_item_hashes", "payload_encoding"):
        assert col in sql_span_exp
        assert col in sql_trace_exp


@pytest.mark.unit
def test_040_schema_has_no_materialized_columns_or_ttls():
    """040_trajectory_items.sql must stay purely additive: no MATERIALIZED, ALIAS, or TTL."""
    import re
    from pathlib import Path

    from tracer.services.clickhouse.v2 import apply_schema

    schema_file = (
        Path(apply_schema.__file__).parent / "schema" / "040_trajectory_items.sql"
    )
    stmts = apply_schema.split_statements(schema_file.read_text())
    assert len(stmts) == 2
    combined = "\n".join(stmts)
    assert not re.search(r"\bMATERIALIZED\b", combined, re.IGNORECASE)
    assert not re.search(r"\bALIAS\b", combined, re.IGNORECASE)
    assert not re.search(r"\bTTL\b", combined, re.IGNORECASE)
    assert not re.search(r"\bMATERIALIZE\b", combined, re.IGNORECASE)
    assert "content_bytes" not in combined


@pytest.mark.unit
def test_trace_list_content_query_preserves_legacy_root_replay_signature(settings):
    """Subclasses overriding _root_replay_query without include_trajectory stay compatible."""
    from datetime import UTC, datetime

    from tracer.services.clickhouse.v2.query_builders.trace_list import (
        TraceListQueryBuilderV2,
    )

    class LegacyRootReplay(TraceListQueryBuilderV2):
        def _root_replay_query(self, identities, *, content, trace_rollup=False):
            return super()._root_replay_query(
                identities, content=content, trace_rollup=trace_rollup
            )

    settings.FI_TRAJECTORY_STORE_READ_ENABLED = True
    pid = PROJECT_ID
    tid = "00000000-0000-0000-0000-000000000011"
    sid = "abcdef0123456780"
    ts_us = int(datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC).timestamp() * 1_000_000)
    root_id = (pid, tid, sid, ts_us, "llm", "svc", ts_us, 1)

    sql, _params = LegacyRootReplay(project_id=pid).build_content_query(
        [tid], root_identities=[root_id]
    )
    assert "payload_encoding" not in sql


# --------------------------------------------------------------------------- #
# 7. Read state (RD-3), failure vocabulary (RD-4) and the pair-exact query (RD-5)
# --------------------------------------------------------------------------- #
from tracer.services.clickhouse.v2.trajectory_hydration import (  # noqa: E402
    CONTENT_HYDRATED,
    CONTENT_INLINE,
    CONTENT_MISSING,
    CONTENT_STATE_KEY,
    CSV_UNAVAILABLE_PREFIX,
    ReadStateProbe,
    TrajectoryHydrationUnavailable,
    TrajectoryReadState,
    content_unavailable_sides,
    fetch_trajectory_items_dict_rows,
    fetch_trajectory_items_tuple_rows,
    items_query_row_cap,
    items_query_settings,
    mark_csv_unavailable_previews,
    pinned_read_state,
    raise_if_content_unavailable,
    read_state_snapshot,
    trajectory_read_state,
)
from tracer.tests.trajectory_read_state_support import SchemaProbe  # noqa: E402

READY = TrajectoryReadState.READY
NOT_READY = TrajectoryReadState.NOT_READY
KILLED = TrajectoryReadState.KILLED


class _Clock:
    def __init__(self):
        self.now = 1_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(trajectory_hydration, "_monotonic", c)
    return c


def _one_side_row(span_id: str, item_hash: str, *, created_at=None, **extra) -> dict:
    """An items_v1 dict row whose input is a one-message array of ``item_hash``
    and whose input.value was promoted (sentinel in span_attributes)."""
    shape, _original = _side(["[", "]"], "m", ['{"role":"user","content":"full"}'])
    row = {
        "project_id": PROJECT_ID,
        "id": span_id,
        "input": "preview",
        "output": "inline out",
        "input_item_hashes": [item_hash],
        "output_item_hashes": [],
        "tool_def_hashes": [],
        "item_scope_id": SESSION_ID,
        "payload_encoding": ENCODING_ITEMS_V1,
        "payload_envelope": _envelope(**{"in": shape}),
        "span_attributes": json.dumps(
            {"keep": 1, SENTINEL_PROMOTED_INPUT_KEY: "input.value"}
        ),
        **extra,
    }
    if created_at is not None:
        row["created_at"] = created_at
    return row


@pytest.mark.unit
def test_read_state_cache_positive_latched_negative_15s_errors_uncached(
    settings, clock
):
    settings.FI_TRAJECTORY_STORE_READ_ENABLED = None
    absent = SchemaProbe(columns=(), table=False, endpoint="db-a")
    source = absent.probe

    assert trajectory_read_state(source) is NOT_READY
    assert len(absent.statements) == 2
    # Negative result: cached for 15 s, then probed again.
    clock.now += 14.0
    assert trajectory_read_state(source) is NOT_READY
    assert len(absent.statements) == 2
    clock.now += 2.0
    absent.columns = trajectory_hydration.TRAJECTORY_SPAN_COLUMNS
    absent.table = True
    assert trajectory_read_state(source) is READY
    assert len(absent.statements) == 4
    # Positive result: latched for the process, even when the probe breaks.
    absent.error = RuntimeError("system.columns unreachable")
    clock.now += 10_000.0
    assert trajectory_read_state(source) is READY
    assert len(absent.statements) == 4

    # A probe error is never cached: with no recent state it raises, every
    # call probes again, and it never falls back to NOT_READY.
    failing = SchemaProbe(error=RuntimeError("boom"), endpoint="db-b")
    for _ in range(2):
        with pytest.raises(TrajectoryHydrationUnavailable) as exc:
            trajectory_read_state(failing.probe)
        assert exc.value.code == "read_state_probe_failed"
    assert len(failing.statements) == 2

    # Within 60 s of a successful probe, an error reuses that state.
    flaky = SchemaProbe(columns=(), table=False, endpoint="db-c")
    assert trajectory_read_state(flaky.probe) is NOT_READY
    flaky.error = RuntimeError("transient")
    clock.now += 20.0
    assert trajectory_read_state(flaky.probe) is NOT_READY
    clock.now += 50.0
    with pytest.raises(TrajectoryHydrationUnavailable):
        trajectory_read_state(flaky.probe)

    # Two endpoints never share a latch.
    assert (
        trajectory_read_state(
            SchemaProbe(columns=(), table=False, endpoint="db-d").probe
        )
        is NOT_READY
    )


@pytest.mark.unit
def test_read_flag_true_still_probes_and_refuses_absent_columns(settings, monkeypatch):
    log = MagicMock()
    monkeypatch.setattr(trajectory_hydration, "logger", log)
    settings.FI_TRAJECTORY_STORE_READ_ENABLED = True
    absent = SchemaProbe(columns=(), table=False, endpoint="pre040")
    assert trajectory_read_state(absent.probe) is NOT_READY
    assert absent.statements, "true must not skip the probe"
    errors = [c.args[0] for c in log.error.call_args_list]
    assert any(m.startswith("trajectory_read_flag_refused") for m in errors)
    # With the schema present, true is READY.
    assert trajectory_read_state(SchemaProbe(endpoint="post040").probe) is READY


@pytest.mark.unit
def test_read_flag_false_honoured_only_while_items_are_empty(settings, monkeypatch):
    log = MagicMock()
    monkeypatch.setattr(trajectory_hydration, "logger", log)
    settings.FI_TRAJECTORY_STORE_READ_ENABLED = False
    assert trajectory_read_state(SchemaProbe(total_rows=0, endpoint="e").probe) is (
        NOT_READY
    )
    assert trajectory_read_state(SchemaProbe(total_rows=3, endpoint="f").probe) is (
        READY
    )
    # total_rows NULL is not "empty": the flag is not honoured.
    assert trajectory_read_state(SchemaProbe(total_rows=None, endpoint="g").probe) is (
        READY
    )
    overridden = [
        c
        for c in log.error.call_args_list
        if c.args[0].startswith("trajectory_read_flag_overridden")
    ]
    assert len(overridden) == 1, "logged once per process"


@pytest.mark.unit
def test_partial_040_schema_is_not_ready_and_logged(settings, monkeypatch):
    log = MagicMock()
    monkeypatch.setattr(trajectory_hydration, "logger", log)
    settings.FI_TRAJECTORY_STORE_READ_ENABLED = None
    three = trajectory_hydration.TRAJECTORY_SPAN_COLUMNS[:3]
    assert trajectory_read_state(SchemaProbe(columns=three, endpoint="p1").probe) is (
        NOT_READY
    )
    # All six columns but no trajectory_items table is partial too.
    assert trajectory_read_state(SchemaProbe(table=False, endpoint="p2").probe) is (
        NOT_READY
    )
    partial = [
        c
        for c in log.error.call_args_list
        if c.args[0].startswith("trajectory_schema_partial")
    ]
    assert len(partial) == 2

    # A column declared with another type is not the 040 column.
    class _WrongType(SchemaProbe):
        def run(self, sql, params, names):
            rows = super().run(sql, params, names)
            if "system.columns" in sql:
                rows = [
                    ("payload_encoding", "String", "DEFAULT", "'inline'")
                    if r[0] == "payload_encoding"
                    else r
                    for r in rows
                ]
            return rows

    assert trajectory_read_state(_WrongType(endpoint="p3").probe) is NOT_READY


@pytest.mark.unit
def test_cluster_probe_requires_every_replica(settings):
    settings.FI_TRAJECTORY_STORE_READ_ENABLED = None
    settings.FI_TRAJECTORY_READ_PROBE_CLUSTER = "cluster"
    from tracer.tests.trajectory_read_state_support import _column_row

    def run_for(lagging_host_columns):
        statements = []

        def run(sql, params, names):
            statements.append(sql)
            if "clusterAllReplicas" in sql and "system.one" in sql:
                return [("r1",), ("r2",)]
            if "clusterAllReplicas" in sql:
                assert params["traj_probe_database"] == "db"
                rows = [("r1", *_column_row(c)) for c in TRAJECTORY_COLUMNS]
                rows += [("r2", *_column_row(c)) for c in lagging_host_columns]
                return rows
            if "currentDatabase() AS database" in sql:
                return [("db",)]
            if "system.columns" in sql:
                return [_column_row(c) for c in TRAJECTORY_COLUMNS]
            return [(0,)]

        return ReadStateProbe(f"cluster-{len(lagging_host_columns)}", run), statements

    TRAJECTORY_COLUMNS = trajectory_hydration.TRAJECTORY_SPAN_COLUMNS
    lagging, statements = run_for(TRAJECTORY_COLUMNS[:2])
    assert trajectory_read_state(lagging) is NOT_READY
    assert any("clusterAllReplicas" in s for s in statements)
    applied, _ = run_for(TRAJECTORY_COLUMNS)
    assert trajectory_read_state(applied) is READY


@pytest.mark.unit
def test_kill_switch_turns_ready_into_killed_and_never_a_preview(settings):
    settings.FI_TRAJECTORY_STORE_READ_ENABLED = None
    settings.FI_TRAJECTORY_HYDRATION_KILL = True
    # KILLED only modifies READY: before 040 the kill switch projects nothing.
    assert (
        trajectory_read_state(SchemaProbe(columns=(), table=False, endpoint="k0").probe)
        is NOT_READY
    )
    assert trajectory_read_state(SchemaProbe(endpoint="k1").probe) is KILLED
    assert KILLED.projects_pointers and not KILLED.fetches_items

    query_fn = MagicMock()
    # A fresh span: the kill switch must not turn into an endless retry.
    rows = [_one_side_row("k", _h("k"), created_at=datetime.now(UTC))]
    hydrate_span_dicts(rows, query_fn, state=KILLED)
    query_fn.assert_not_called()
    assert rows[0]["input"] == "preview"
    assert rows[0][CONTENT_STATE_KEY] == {
        "input": CONTENT_MISSING,
        "output": CONTENT_INLINE,
    }
    assert json.loads(rows[0]["span_attributes"]) == {"keep": 1}
    assert content_unavailable_sides(rows[0]) == ["input"]


@pytest.mark.unit
def test_query_failure_raises_trajectory_hydration_unavailable():
    rows = [_one_side_row("q", _h("q"))]

    def broken(_sql, _params):
        raise ConnectionError("clickhouse went away")

    with pytest.raises(TrajectoryHydrationUnavailable) as exc:
        hydrate_span_dicts(rows, broken)
    assert exc.value.code == "items_query_failed"
    assert isinstance(exc.value.__cause__, ConnectionError)
    # Nothing was applied: no content state, no rebuilt preview.
    assert rows[0]["input"] == "preview" and CONTENT_STATE_KEY not in rows[0]

    with pytest.raises(TrajectoryHydrationUnavailable):
        fetch_trajectory_items_tuple_rows(broken, [(PROJECT_ID, SESSION_ID, _h("q"))])


@pytest.mark.unit
def test_items_query_rejects_rows_it_did_not_request():
    refs = [(PROJECT_ID, SESSION_ID, _h("a")), (PROJECT_ID, PROJECT_SCOPE_ID, _h("b"))]
    # The cross product a scopes x hashes filter returns: (SESSION, b) was
    # never asked for.
    cross = [
        {"scope_id": SESSION_ID, "item_hash": _h("a"), "content": "a"},
        {"scope_id": SESSION_ID, "item_hash": _h("b"), "content": "b?"},
    ]
    with pytest.raises(TrajectoryHydrationUnavailable) as exc:
        fetch_trajectory_items_dict_rows(lambda _s, _p: cross, refs)
    assert exc.value.code == "items_query_unrequested_row"
    # More rows than pairs: the client-side row cap.
    many = cross + [
        {"scope_id": PROJECT_SCOPE_ID, "item_hash": _h("b"), "content": "b"}
    ]
    with pytest.raises(TrajectoryHydrationUnavailable) as exc:
        fetch_trajectory_items_dict_rows(lambda _s, _p: many, refs)
    assert exc.value.code == "items_query_row_cap"


@pytest.mark.unit
def test_chunking_bounds_pairs_and_query_size():
    from tracer.services.clickhouse.v2.trajectory_hydration import (
        _MAX_PAIRS_PER_QUERY,
        _MAX_SCOPES_PER_QUERY,
        _chunk_project_refs,
    )

    other = "44444444-4444-4444-8444-444444444444"
    refs = [
        (PROJECT_ID, f"scope-{i % 700:04d}-{'x' * 30}", f"{i:032x}")
        for i in range(4000)
    ]
    refs += [(other, SESSION_ID, _h("o"))]
    refs += refs[:10]  # duplicates are read once
    chunks = _chunk_project_refs(refs)
    seen = set()
    for project_id, scopes, pairs in chunks:
        assert len(pairs) <= _MAX_PAIRS_PER_QUERY
        assert len(scopes) <= _MAX_SCOPES_PER_QUERY
        assert set(scopes) == {s for s, _h2 in pairs}
        seen.update((project_id, s, h) for s, h in pairs)
        sql, params = build_trajectory_items_query(project_id=project_id, pairs=pairs)
        # The rendered statement stays far below max_query_size (256 KiB).
        rendered = sql + repr(params)
        assert len(rendered.encode()) < 200 * 1024
        assert items_query_row_cap(params) == len(set(pairs)) + 1
    assert seen == set(refs)
    # A single pair binds as a set, never as one bare tuple.
    _sql, params = build_trajectory_items_query(
        project_id=PROJECT_ID, pairs=[(SESSION_ID, _h("one"))]
    )
    assert params["traj_pairs"] == ((SESSION_ID, _h("one")), (SESSION_ID, _h("one")))
    assert items_query_row_cap(params) == 2

    settings = items_query_settings(params)
    assert settings["max_result_rows"] == 2
    assert settings["result_overflow_mode"] == "throw"
    assert settings["max_execution_time"] == 10
    assert settings["max_threads"] == 1
    assert settings["log_comment"] == "trajectory_hydration"


@pytest.mark.unit
def test_missing_item_marks_state_and_does_not_restore_promoted_attr(monkeypatch):
    sent = []
    monkeypatch.setattr(
        trajectory_hydration,
        "_report_content_unavailable",
        lambda reason, **kw: sent.append((reason, kw["surface"], kw["side"])),
    )
    old = datetime(2026, 11, 1, tzinfo=UTC)
    present, absent = _h("present"), _h("absent")
    rows = [
        _one_side_row("hydrated", present, created_at=old),
        _one_side_row("missing", absent, created_at=old),
        {"project_id": PROJECT_ID, "id": "inline", "input": "x", "output": "y"},
    ]
    items = [
        {
            "scope_id": SESSION_ID,
            "item_hash": present,
            "content": '{"role":"user","content":"full"}',
        }
    ]
    hydrate_span_dicts(rows, lambda _s, _p: items, surface="trace_detail")

    hydrated, missing, inline = rows
    assert hydrated["input"] == '[{"role":"user","content":"full"}]'
    assert hydrated[CONTENT_STATE_KEY] == {
        "input": CONTENT_HYDRATED,
        "output": CONTENT_INLINE,
    }
    assert json.loads(hydrated["span_attributes"])["input.value"] == hydrated["input"]

    assert missing["input"] == "preview"
    assert missing[CONTENT_STATE_KEY] == {
        "input": CONTENT_MISSING,
        "output": CONTENT_INLINE,
    }
    # The sentinel is gone and input.value is NOT rebuilt from the preview.
    assert json.loads(missing["span_attributes"]) == {"keep": 1}
    assert content_unavailable_sides(missing) == ["input"]

    assert CONTENT_STATE_KEY not in inline
    assert content_unavailable_sides(inline) == []
    assert sent == [("missing_items", "trace_detail", "in")]

    # The same row as a CHSpan.
    span = span_reader.CHSpan(
        **{
            **dict.fromkeys(span_reader._DATA_KEYS, ""),
            "id": "missing",
            "project_id": PROJECT_ID,
            "start_time": old,
            "end_time": old,
            "latency_ms": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cost": None,
            "org_id": None,
            "project_version_id": None,
            "end_user_id": None,
            "trace_session_id": None,
            "prompt_version_id": None,
            "prompt_label_id": None,
            "custom_eval_config_id": None,
            "attrs_string": {},
            "attrs_number": {},
            "attrs_bool": {},
            "streaming": None,
            "temperature": None,
            "top_p": None,
            "max_tokens": None,
            "is_deleted": 0,
            "created_at": old,
            "input": "preview",
            "output": "inline out",
            "attributes_extra": json.dumps(
                {"keep": 1, SENTINEL_PROMOTED_INPUT_KEY: "input.value"}
            ),
            "input_item_hashes": (absent,),
            "item_scope_id": SESSION_ID,
            "payload_encoding": ENCODING_ITEMS_V1,
            "payload_envelope": _one_side_row("x", absent)["payload_envelope"],
        }
    )
    (out,) = hydrate_chspans([span], lambda _s, _p: [])
    assert out.input == "preview"
    assert out.content_state == (CONTENT_MISSING, CONTENT_INLINE)
    assert content_unavailable_sides(out) == ["input"]
    assert "input.value" not in span_reader._ch_span_attributes(out)
    with pytest.raises(TrajectoryContentUnavailable) as exc:
        raise_if_content_unavailable([out], surface="eval")
    assert exc.value.span_ids == ("missing",)


@pytest.mark.unit
def test_missing_item_on_fresh_span_is_retryable_unavailable():
    fresh = datetime.now(UTC) - timedelta(minutes=2)
    rows = [_one_side_row("fresh", _h("lagging"), created_at=fresh)]
    with pytest.raises(TrajectoryHydrationUnavailable) as exc:
        hydrate_span_dicts(rows, lambda _s, _p: [])
    assert exc.value.code == "items_not_yet_visible"
    assert CONTENT_STATE_KEY not in rows[0]

    # Past the window it is reported as missing, not retried forever.
    rows = [
        _one_side_row("old", _h("lagging"), created_at=fresh - timedelta(minutes=20))
    ]
    hydrate_span_dicts(rows, lambda _s, _p: [])
    assert content_unavailable_sides(rows[0]) == ["input"]
    # A checksum mismatch on a fresh span is not lag: it is missing at once.
    tampered = [{"scope_id": SESSION_ID, "item_hash": _h("t"), "content": "WRONG"}]
    rows = [_one_side_row("tampered", _h("t"), created_at=fresh)]
    hydrate_span_dicts(rows, lambda _s, _p: tampered)
    assert content_unavailable_sides(rows[0]) == ["input"]
    # A row with no created_at is treated as old (documented choice).
    rows = [_one_side_row("no-created-at", _h("lagging"))]
    hydrate_span_dicts(rows, lambda _s, _p: [])
    assert content_unavailable_sides(rows[0]) == ["input"]


@pytest.mark.unit
def test_rca_hydration_failure_is_not_silent_preview(monkeypatch):
    from tracer.services.clickhouse import cluster_rca_spans

    class _BrokenAnalytics:
        def execute_ch_query(self, query, params, timeout_ms=None, **_kw):
            raise ConnectionError("items read failed")

    monkeypatch.setattr(cluster_rca_spans, "_analytics", _BrokenAnalytics())
    rows = [_one_side_row("rca", _h("rca"))]
    with pytest.raises(TrajectoryHydrationUnavailable):
        cluster_rca_spans._hydrate_rca_rows(PROJECT_ID, rows, READY)
    # Before: the swallowing reader returned [] and the preview was served.
    assert cluster_rca_spans._execute_read("SELECT 1", {}) == []

    class _EmptyAnalytics:
        def execute_ch_query(self, query, params, timeout_ms=None, **_kw):
            return SimpleNamespace(
                columns=["scope_id", "item_hash", "content"], data=[]
            )

    monkeypatch.setattr(cluster_rca_spans, "_analytics", _EmptyAnalytics())
    rows = [
        _one_side_row("rca", _h("rca"), created_at=datetime(2026, 11, 1, tzinfo=UTC))
    ]
    with pytest.raises(TrajectoryContentUnavailable):
        cluster_rca_spans._hydrate_rca_rows(PROJECT_ID, rows, READY)


@pytest.mark.unit
def test_rca_span_reads_surface_hydration_failures(monkeypatch):
    from tracer.services.clickhouse import cluster_rca_spans

    class _Reader:
        def list_by_trace(self, *_a, **_k):
            raise TrajectoryHydrationUnavailable("items_query_failed")

        def get(self, *_a, **_k):
            raise TrajectoryHydrationUnavailable("items_query_failed")

        def close(self):
            return None

    monkeypatch.setattr(cluster_rca_spans, "get_reader", lambda: _Reader())
    with pytest.raises(TrajectoryHydrationUnavailable):
        cluster_rca_spans.spans_for_trace(PROJECT_ID, TRACE_ID)
    with pytest.raises(TrajectoryHydrationUnavailable):
        cluster_rca_spans.read_span(PROJECT_ID, "s")


@pytest.mark.unit
def test_csv_marks_a_missing_side_preview():
    rows = [
        {
            "input": "preview",
            "output": "out",
            CONTENT_STATE_KEY: {"input": CONTENT_MISSING, "output": CONTENT_INLINE},
        },
        {
            "input": "full",
            "output": "out",
            CONTENT_STATE_KEY: {"input": CONTENT_HYDRATED, "output": CONTENT_INLINE},
        },
        {"input": "inline", "output": "out"},
        {
            "input": None,
            "output": "p",
            CONTENT_STATE_KEY: {"input": CONTENT_MISSING, "output": CONTENT_MISSING},
        },
    ]
    mark_csv_unavailable_previews(rows)
    mark_csv_unavailable_previews(rows)  # idempotent
    assert rows[0]["input"] == CSV_UNAVAILABLE_PREFIX + "preview"
    assert rows[0]["output"] == "out"
    assert rows[1]["input"] == "full"
    assert rows[2]["input"] == "inline"
    assert rows[3]["input"] == CSV_UNAVAILABLE_PREFIX
    assert rows[3]["output"] == CSV_UNAVAILABLE_PREFIX + "p"
    assert CSV_UNAVAILABLE_PREFIX == "[preview — full content unavailable] "


@pytest.mark.unit
def test_state_snapshot_pins_one_state_per_call(settings):
    settings.FI_TRAJECTORY_STORE_READ_ENABLED = None
    probe = SchemaProbe(columns=(), table=False, endpoint="snap")
    with read_state_snapshot(probe.probe) as state:
        assert state is NOT_READY
        probe.columns = trajectory_hydration.TRAJECTORY_SPAN_COLUMNS
        probe.table = True
        trajectory_hydration.reset_read_state_cache()
        # Inside the call the pinned state wins over a changed schema.
        assert trajectory_read_state(probe.probe) is NOT_READY
        with read_state_snapshot(probe.probe) as nested:
            assert nested is NOT_READY
    assert trajectory_read_state(probe.probe) is READY
    with pinned_read_state(KILLED):
        assert trajectory_read_state() is KILLED


@pytest.mark.unit
def test_eval_loader_refuses_a_span_whose_content_is_unavailable():
    from tracer.services.clickhouse.v2.eval_loader import (
        SpanContentUnavailable,
        _construct_from_chspan,
    )

    base = {
        **dict.fromkeys(span_reader._DATA_KEYS, ""),
        "id": "s1",
        "project_id": PROJECT_ID,
        "start_time": datetime(2026, 11, 1, tzinfo=UTC),
        "end_time": None,
        "latency_ms": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost": None,
        "org_id": None,
        "project_version_id": None,
        "end_user_id": None,
        "trace_session_id": None,
        "prompt_version_id": None,
        "prompt_label_id": None,
        "custom_eval_config_id": None,
        "attrs_string": {},
        "attrs_number": {},
        "attrs_bool": {},
        "streaming": None,
        "temperature": None,
        "top_p": None,
        "max_tokens": None,
        "is_deleted": 0,
        "created_at": None,
        "input": '"preview"',
        "output": '"out"',
        "attributes_extra": "{}",
        "tags": "[]",
        "span_events": "[]",
        "metadata": "{}",
        "resource_attrs": "{}",
    }
    missing = span_reader.CHSpan(
        **base, content_state=(CONTENT_MISSING, CONTENT_INLINE)
    )
    with pytest.raises(SpanContentUnavailable) as exc:
        _construct_from_chspan(missing)
    assert exc.value.sides == ["input"]
    # Hydrated and inline spans still load.
    ok = span_reader.CHSpan(**base, content_state=(CONTENT_HYDRATED, CONTENT_INLINE))
    assert _construct_from_chspan(ok).input == "preview"


@pytest.mark.unit
def test_dataset_rows_refuse_unavailable_content():
    from tracer.tasks import dataset as dataset_tasks

    child = {
        "id": "c1",
        "parent_span_id": "root",
        "input": "preview",
        CONTENT_STATE_KEY: {"input": CONTENT_MISSING, "output": CONTENT_INLINE},
    }
    with pytest.raises(TrajectoryContentUnavailable) as exc:
        dataset_tasks._build_subtree_from_dicts({"root": [child]}, "root")
    assert exc.value.span_ids == ("c1",)
    inline_child = {"id": "c2", "parent_span_id": "root", "input": "x"}
    assert dataset_tasks._build_subtree_from_dicts({"root": [inline_child]}, "root")

    # The chunk task refuses rows whose export carries a missing side.
    with pytest.raises(TrajectoryContentUnavailable):
        raise_if_content_unavailable(
            [
                {
                    "id": "s1",
                    CONTENT_STATE_KEY: {
                        "input": CONTENT_INLINE,
                        "output": CONTENT_MISSING,
                    },
                }
            ],
            surface="dataset",
        )


@pytest.mark.unit
def test_trace_detail_hydration_failure_is_typed_unavailable(settings):
    """read_trace_detail turns an items failure into its typed 503 code and
    resolves the read state once for the whole read."""
    settings.FI_TRAJECTORY_STORE_READ_ENABLED = True
    now = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    span = _one_side_row("span-1", _h("u1"), created_at=now)
    span.update(trace_id=TRACE_ID, start_time=now, parent_span_id="", name="n")

    class _Analytics:
        def __init__(self):
            self.queries = []

        def execute_ch_query(self, query, params=None, **_kw):
            self.queries.append(query)
            if "FROM trajectory_items" in query:
                raise ConnectionError("items replica down")
            if "latest_physical_spans" in query:
                return SimpleNamespace(data=[dict(span)], complete=True)
            if "AS span_id" in query:
                return SimpleNamespace(
                    data=[
                        {
                            "project_id": PROJECT_ID,
                            "trace_id": TRACE_ID,
                            "span_id": "span-1",
                            "start_time": now,
                            "latest_is_deleted": 0,
                            "latest_version": 1,
                        }
                    ],
                    complete=True,
                )
            return SimpleNamespace(data=[], complete=True)

    with pytest.raises(trace_detail_reads.TraceDetailReadUnavailable) as exc:
        trace_detail_reads.read_trace_detail(
            analytics=_Analytics(),
            project_ids=[PROJECT_ID],
            trace_id=TRACE_ID,
            include_annotations=False,
        )
    assert exc.value.code == "trajectory_hydration_unavailable"

    # A probe failure is the same typed 503, never a preview.
    class _Probe(_Analytics):
        trajectory_read_probe = SchemaProbe(
            error=RuntimeError("down"), endpoint="td"
        ).probe

    with pytest.raises(trace_detail_reads.TraceDetailReadUnavailable) as exc:
        trace_detail_reads.read_trace_detail(
            analytics=_Probe(),
            project_ids=[PROJECT_ID],
            trace_id=TRACE_ID,
            include_annotations=False,
        )
    assert exc.value.code == "trajectory_read_state_unavailable"


@pytest.mark.unit
def test_trace_detail_reads_content_and_pointers_from_one_tuple(settings):
    ident = trace_detail_reads.PhysicalSpanIdentity(
        project_id=PROJECT_ID,
        trace_id=TRACE_ID,
        span_id="s",
        start_time=datetime(2026, 11, 1, tzinfo=UTC),
    )
    builder = trace_detail_reads.TraceDetailReadBuilder(
        project_ids=[PROJECT_ID], trace_id=TRACE_ID
    )
    off, _ = builder.build_content_query([ident], state=NOT_READY)
    on, _ = builder.build_content_query([ident], state=READY)
    # Flag-off SQL keeps the per-column projections.
    assert "argMax(input, _version) AS latest_input," in off
    assert "latest_content_tuple" not in off
    for col in trajectory_hydration.TRAJECTORY_SPAN_COLUMNS:
        assert col not in off
    # With pointers, one argMax picks input, output, attributes_extra and every
    # pointer from the same physical row.
    assert "argMax(input, _version)" not in on
    assert "argMax(output, _version)" not in on
    assert "argMax(tuple(attributes_extra), _version)" not in on
    assert "argMax(input_item_hashes" not in on
    assert "AS latest_content_tuple" in on
    assert "latest_created_at AS created_at" in on
