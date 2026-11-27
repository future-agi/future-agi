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
    build_trajectory_items_query,
    hydrate_chspans,
    hydrate_span_dicts,
    reconstruct_payload,
    restore_promoted_in_attributes_extra,
)

PROJECT_ID = "11111111-1111-4111-8111-111111111111"
SESSION_ID = "22222222-2222-4222-8222-222222222222"
TRACE_ID = "33333333-3333-4333-8333-333333333333"


def _h(tag: str) -> str:
    """Deterministic 32-char lowercase hex hash for test fixtures."""
    return (tag.encode().hex() + "0" * 32)[:32]


# --------------------------------------------------------------------------- #
# 1. Payload reconstruction across provider formats (Go -> Python contract)
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_reconstruct_openai_chat_with_tools_and_choices():
    h_sys, h_u1, h_t1, h_out = _h("sys"), _h("u1"), _h("tool1"), _h("out1")
    store = {
        (
            PROJECT_SCOPE_ID,
            h_sys,
        ): '{"role":"system","content":"You are a coding agent."}',
        (SESSION_ID, h_u1): '{"role":"user","content":"Fix the bug"}',
        (PROJECT_SCOPE_ID, h_t1): '{"type":"function","function":{"name":"read_file"}}',
        (
            SESSION_ID,
            h_out,
        ): '{"index":0,"message":{"role":"assistant","content":"Checking file."}}',
    }

    def resolve(prefixed: str) -> str | None:
        if prefixed.startswith(PROJECT_HASH_PREFIX):
            return store.get((PROJECT_SCOPE_ID, prefixed[len(PROJECT_HASH_PREFIX) :]))
        return store.get((SESSION_ID, prefixed))

    in_shape = {
        "f": "obj_messages",
        "mk": "messages",
        "tk": "tools",
        "w": {"model": "gpt-4o", "temperature": 0.2},
    }
    in_json = reconstruct_payload(
        in_shape,
        [f"{PROJECT_HASH_PREFIX}{h_sys}", h_u1],
        [f"{PROJECT_HASH_PREFIX}{h_t1}"],
        resolve,
    )
    assert in_json is not None
    parsed_in = json.loads(in_json)
    assert parsed_in == {
        "model": "gpt-4o",
        "temperature": 0.2,
        "tools": [{"type": "function", "function": {"name": "read_file"}}],
        "messages": [
            {"role": "system", "content": "You are a coding agent."},
            {"role": "user", "content": "Fix the bug"},
        ],
    }

    out_shape = {"f": "obj_messages", "mk": "choices", "w": {"id": "chatcmpl-1"}}
    out_json = reconstruct_payload(out_shape, [h_out], None, resolve)
    assert out_json is not None
    assert json.loads(out_json) == {
        "id": "chatcmpl-1",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "Checking file."}}
        ],
    }


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
    store = {
        f"{PROJECT_HASH_PREFIX}{h_sys}": '"Follow all safety rules."',
        h_u1: '{"role":"user","content":"Hello"}',
    }
    shape = {
        "f": "obj_messages",
        "mk": msg_key,
        "sk": sys_key,
        "sh": f"{PROJECT_HASH_PREFIX}{h_sys}",
        "w": {"model": model},
    }
    rebuilt = reconstruct_payload(shape, [h_u1], None, store.get)
    assert rebuilt is not None
    assert json.loads(rebuilt) == {
        "model": model,
        sys_key: "Follow all safety rules.",
        msg_key: [{"role": "user", "content": "Hello"}],
    }


@pytest.mark.unit
def test_reconstruct_msg_array_and_missing_item_safety():
    h1, h2 = _h("m1"), _h("m2")
    store = {
        f"{PROJECT_HASH_PREFIX}{h1}": '{"role":"developer","content":"Strict"}',
        h2: '{"role":"user","content":"Ping"}',
    }
    rebuilt = reconstruct_payload(
        {"f": "msg_array"}, [f"{PROJECT_HASH_PREFIX}{h1}", h2], None, store.get
    )
    assert rebuilt is not None
    assert json.loads(rebuilt) == [
        {"role": "developer", "content": "Strict"},
        {"role": "user", "content": "Ping"},
    ]

    # Missing item -> returns None so caller keeps inline preview safely.
    assert (
        reconstruct_payload({"f": "msg_array"}, [h2, _h("missing")], None, store.get)
        is None
    )


# --------------------------------------------------------------------------- #
# 2. Zero-query guarantee on inline spans + single batched query on mixed trace
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_hydrate_span_dicts_zero_queries_for_inline_and_single_batch_for_mixed():
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

    env = json.dumps(
        {
            "v": 1,
            "in": {"f": "obj_messages", "mk": "messages", "w": {"model": "gpt-4o"}},
            "out": {"f": "msg_array"},
        }
    )
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
    assert json.loads(hydrated["input"]) == {
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": "Question?"},
        ],
    }
    assert json.loads(hydrated["output"]) == [
        {"role": "assistant", "content": "Answer!"}
    ]
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
    env = json.dumps({"v": 1, "in": {"f": "msg_array"}})

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
    assert json.loads(detail.spans[0]["input"]) == [
        {"role": "user", "content": "Hydrated question"}
    ]
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
    assert json.loads(phys["input"]) == [
        {"role": "user", "content": "Hydrated question"}
    ]
    assert json.loads(phys["span_attributes"]) == {"input.value": phys["input"]}


@pytest.mark.unit
def test_ch_span_reader_hydrates_get_list_and_export(settings):
    settings.FI_TRAJECTORY_STORE_READ_ENABLED = True
    h_u1 = _h("u1")
    env = json.dumps({"v": 1, "in": {"f": "msg_array"}})
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
    assert json.loads(sp.input) == [{"role": "user", "content": "Full eval prompt"}]
    # eval_loader consumes _ch_span_attributes(sp) -> Phase 0 sentinel restored with hydrated input!
    attrs = span_reader._ch_span_attributes(sp)
    assert attrs["input.value"] == sp.input
    assert SENTINEL_PROMOTED_INPUT_KEY not in attrs

    trace_spans = reader.list_by_trace(TRACE_ID, project_id=PROJECT_ID)
    assert (
        len(trace_spans) == 1
        and json.loads(trace_spans[0].input)[0]["content"] == "Full eval prompt"
    )

    id_spans = reader.list_by_ids(["sp-1"], project_id=PROJECT_ID)
    assert (
        len(id_spans) == 1
        and json.loads(id_spans[0].input)[0]["content"] == "Full eval prompt"
    )

    exported = reader.export_fields_by_ids(
        ["sp-1"], {"input", "span_attributes"}, project_id=PROJECT_ID
    )
    assert json.loads(exported["sp-1"]["input"]) == [
        {"role": "user", "content": "Full eval prompt"}
    ]
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
        _MAX_HASHES_PER_QUERY,
        fetch_trajectory_items_dict_rows,
    )

    schema_sql = (
        Path(apply_schema.__file__).parent / "schema" / "040_trajectory_items.sql"
    ).read_text()
    assert "PRIMARY KEY (project_id, scope_id, item_hash)" in schema_sql

    sql, _params = build_trajectory_items_query(
        project_id=PROJECT_ID,
        scope_ids=[PROJECT_SCOPE_ID, SESSION_ID],
        item_hashes=["a" * 32],
    )
    assert "PREWHERE project_id = %(traj_project_id)s" in sql
    assert "AND item_hash IN %(traj_item_hashes)s" in sql
    assert "WHERE item_hash" not in sql

    # 3,200 hashes under a single scope must split into 3 bounded queries.
    total = _MAX_HASHES_PER_QUERY * 2 + 200
    refs = [(PROJECT_ID, PROJECT_SCOPE_ID, f"{i:032x}") for i in range(total)]
    batch_sizes: list[int] = []

    def fake_query(_sql, params):
        hs = params["traj_item_hashes"]
        batch_sizes.append(len(hs))
        return [
            {"scope_id": PROJECT_SCOPE_ID, "item_hash": h, "content": f"c:{h}"}
            for h in hs
        ]

    resolved = fetch_trajectory_items_dict_rows(fake_query, refs)
    assert len(batch_sizes) == 3
    assert all(n <= _MAX_HASHES_PER_QUERY for n in batch_sizes)
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
    env_json = json.dumps({"in": {"f": "msg_array"}})
    full_msg = json.dumps({"role": "user", "content": "Full lossless dataset question"})
    expected_in = json.dumps([json.loads(full_msg)], separators=(",", ":"))
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
