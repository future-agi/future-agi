"""Unit tests for VoiceCallListQueryBuilder (query-string building, no DB).

Covers the multi-phase voice-call list query strategy:
  build() (root conversation spans, project scope, ORDER BY, pagination),
  build_count_query, build_id_query, build_content_query, build_eval_query,
  build_annotation_query, build_child_spans_query, empty-input guards,
  the simulation-filter no-op, and filters embedded via the filter builder.

The builder builds SQL STRINGS only — nothing here touches ClickHouse.
"""

import re
from datetime import UTC, datetime, timedelta

import pytest
from django.test import override_settings

from tracer.services.clickhouse.query_builders.voice_call_list import (
    VAPI_PHONE_NUMBERS,
    VoiceCallListQueryBuilder,
)

PROJECT_ID = "proj-123"


def _squash(sql: str) -> str:
    """Collapse whitespace so multi-line SQL substrings match reliably."""
    return re.sub(r"\s+", " ", sql).strip()


def _voice_multi_filters(end: datetime) -> list[dict]:
    return [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [end - timedelta(days=365), end],
            },
        },
        {
            "column_id": "call.total_turns",
            "filter_config": {
                "filter_type": "number",
                "filter_op": "equals",
                "filter_value": 2,
                "col_type": "SPAN_ATTRIBUTE",
            },
        },
        {
            "column_id": "conversation.transcript.16.message.role",
            "filter_config": {
                "filter_type": "text",
                "filter_op": "in",
                "filter_value": ["assistant"],
                "col_type": "SPAN_ATTRIBUTE",
            },
        },
    ]


# ---------------------------------------------------------------------------
# build() — Phase 1 paginated root conversation spans
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_selects_from_spans_table():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID)
    sql, _ = qb.build()
    assert "FROM spans" in _squash(sql)


@pytest.mark.unit
def test_build_root_conversation_span_predicate():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID)
    sql, _ = qb.build()
    s = _squash(sql)
    # Root span: no parent
    assert "(parent_span_id IS NULL OR parent_span_id = '')" in s
    # Voice calls are conversation-type roots
    assert "observation_type = 'conversation'" in s


@pytest.mark.unit
def test_build_scopes_to_single_project():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID)
    sql, params = qb.build()
    s = _squash(sql)
    assert "project_id = %(project_id)s" in s
    assert "is_deleted = 0" in s
    assert params["project_id"] == PROJECT_ID


@pytest.mark.unit
def test_build_scopes_to_multiple_projects():
    qb = VoiceCallListQueryBuilder(project_id=None, project_ids=["p1", "p2"])
    sql, params = qb.build()
    s = _squash(sql)
    assert "project_id IN %(project_ids)s" in s
    assert params["project_ids"] == ("p1", "p2")


@pytest.mark.unit
def test_build_orders_by_start_time_desc():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID)
    sql, _ = qb.build()
    s = _squash(sql)
    assert "ORDER BY start_time DESC" in s
    # Deduplicate to one row per call
    assert "LIMIT 1 BY trace_id" in s


@pytest.mark.unit
def test_build_selects_light_columns_not_heavy_attrs():
    """build() must not pull span_attributes_raw (heavy blob → CH OOM)."""
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID)
    sql, _ = qb.build()
    s = _squash(sql)
    assert "span_attributes_raw" not in s
    for col in ("trace_id", "id AS span_id", "status", "latency_ms", "provider"):
        assert col in s


@pytest.mark.unit
def test_build_pagination_default_page():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID, page_number=0, page_size=10)
    sql, params = qb.build()
    s = _squash(sql)
    assert "LIMIT %(limit)s" in s
    assert "OFFSET %(offset)s" in s
    # Fetch one extra row for has_more detection.
    assert params["limit"] == 11
    assert params["offset"] == 0


@pytest.mark.unit
def test_build_pagination_computes_offset_from_page():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID, page_number=3, page_size=25)
    _, params = qb.build()
    assert params["offset"] == 75  # page_number * page_size
    assert params["limit"] == 26  # page_size + 1


@pytest.mark.unit
def test_build_sets_time_window_params():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID)
    sql, params = qb.build()
    s = _squash(sql)
    assert "start_time >= %(start_date)s" in s
    assert "start_time < %(end_date)s" in s
    assert "created_at >= %(start_date)s - INTERVAL 1 DAY" in s
    assert params["start_date"] is not None
    assert params["end_date"] is not None


@pytest.mark.unit
def test_build_no_filters_omits_filter_fragment():
    """With no frontend filters there must be no dangling `AND` fragment."""
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID, filters=[])
    sql, _ = qb.build()
    s = _squash(sql)
    assert "AND  ORDER BY" not in s
    assert "AND ORDER BY" not in s


# ---------------------------------------------------------------------------
# Simulation filter — SQL is a no-op (filtering happens in Python)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_simulation_filter_sql_is_noop():
    """_build_simulation_filter emits nothing regardless of the flag."""
    qb_on = VoiceCallListQueryBuilder(
        project_id=PROJECT_ID, remove_simulation_calls=True
    )
    qb_off = VoiceCallListQueryBuilder(
        project_id=PROJECT_ID, remove_simulation_calls=False
    )
    assert qb_on._build_simulation_filter() == ""
    assert qb_off._build_simulation_filter() == ""


@pytest.mark.unit
def test_build_does_not_embed_phone_numbers_in_sql():
    """Phone numbers live in the heavy JSON blob; must not leak into SQL."""
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID, remove_simulation_calls=True)
    sql, _ = qb.build()
    for phone in VAPI_PHONE_NUMBERS:
        assert phone not in sql


@pytest.mark.unit
def test_is_simulator_call_vapi_match():
    attrs = {"raw_log": {"customer": {"number": VAPI_PHONE_NUMBERS[0]}}}
    assert VoiceCallListQueryBuilder.is_simulator_call(attrs, "vapi") is True


@pytest.mark.unit
def test_is_simulator_call_vapi_non_match():
    attrs = {"raw_log": {"customer": {"number": "+10000000000"}}}
    assert VoiceCallListQueryBuilder.is_simulator_call(attrs, "vapi") is False


@pytest.mark.unit
def test_is_simulator_call_retell_match():
    attrs = {"raw_log": {"from_number": VAPI_PHONE_NUMBERS[1]}}
    assert VoiceCallListQueryBuilder.is_simulator_call(attrs, "retell") is True


@pytest.mark.unit
def test_is_simulator_call_unknown_provider():
    attrs = {"raw_log": {"customer": {"number": VAPI_PHONE_NUMBERS[0]}}}
    assert VoiceCallListQueryBuilder.is_simulator_call(attrs, "twilio") is False


@pytest.mark.unit
def test_is_simulator_call_missing_raw_log():
    assert VoiceCallListQueryBuilder.is_simulator_call({}, "vapi") is False


# ---------------------------------------------------------------------------
# build_count_query — Phase-1 total
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_count_query_uses_uniq_exact_trace_id():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID)
    qb.build()  # populate start/end params consumed by count query
    sql, params = qb.build_count_query()
    s = _squash(sql)
    assert "uniqExact(trace_id) AS total" in s
    assert "FROM spans" in s
    # Same conversation-root predicate as build()
    assert "observation_type = 'conversation'" in s
    assert "(parent_span_id IS NULL OR parent_span_id = '')" in s
    # No pagination on a count query.
    assert "LIMIT" not in s
    assert "OFFSET" not in s


@pytest.mark.unit
def test_count_query_respects_project_scope():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID)
    qb.build()
    _, params = qb.build_count_query()
    assert params["project_id"] == PROJECT_ID


@pytest.mark.unit
def test_v2_voice_normal_list_count_and_id_use_start_time_only():
    from tracer.services.clickhouse.v2.query_builders.voice_call_list import (
        VoiceCallListQueryBuilderV2,
    )

    qb = VoiceCallListQueryBuilderV2(project_id=PROJECT_ID)
    for method_name in ("build", "build_count_query", "build_id_query"):
        sql, _ = getattr(qb, method_name)()
        s = _squash(sql)
        assert "start_time >= %(start_date)s" in s
        assert "start_time < %(end_date)s" in s
        assert "created_at >= %(start_date)s" not in s


@pytest.mark.unit
def test_interactive_voice_multi_filter_classifies_identity_then_hydrates_page():
    """The call grid must not aggregate presentation columns for every candidate."""
    from tracer.services.clickhouse.v2.query_builders.voice_call_list import (
        VoiceCallListQueryBuilderV2,
    )

    end = datetime(2026, 8, 8, tzinfo=UTC)
    builder = VoiceCallListQueryBuilderV2(
        project_id=PROJECT_ID,
        page_size=25,
        filters=_voice_multi_filters(end),
    )
    seed_rows = [
        {
            "project_id": PROJECT_ID,
            "trace_id": "trace-1",
            "root_span_id": "root-1",
            "start_time": end - timedelta(minutes=1),
        }
    ]

    identity_sql, identity_params = (
        builder.build_filter_identity_match_query_from_seed_rows(seed_rows)
    )
    hydration_sql, hydration_params = builder.build_filter_page_hydration_query(
        seed_rows
    )

    assert builder.use_identity_only_filter_classification() is True
    assert builder.recommended_filter_seed_batch_size() == 200
    assert "SELECT trace_id, canonical_root_identity.1 AS root_span_id" in identity_sql
    assert "latest_trace_name AS trace_name" not in identity_sql
    assert "latest_attr_exists_0" in identity_sql
    assert "latest_attr_exists_1" in identity_sql
    assert identity_params["latest_filter_key_0"] == "call.total_turns"
    assert identity_params["latest_filter_param_0"] == 2
    assert (
        identity_params["latest_filter_key_1"]
        == "conversation.transcript.16.message.role"
    )
    assert identity_params["latest_filter_param_1"] == ("assistant",)
    assert "latest_trace_name AS trace_name" in hydration_sql
    expected_start_us = int(seed_rows[0]["start_time"].timestamp() * 1_000_000)
    assert hydration_params["page_hydration_root_identities"] == (
        (PROJECT_ID, "trace-1", "root-1", expected_start_us),
    )
    assert identity_sql.count("SETTINGS ") == 1
    assert hydration_sql.count("SETTINGS ") == 1


@pytest.mark.unit
def test_voice_multi_filter_exact_zero_probe_intersects_independent_span_witnesses():
    from tracer.services.clickhouse.v2.query_builders.voice_call_list import (
        VoiceCallListQueryBuilderV2,
    )

    end = datetime(2026, 8, 8, tzinfo=UTC)
    builder = VoiceCallListQueryBuilderV2(
        project_id=PROJECT_ID,
        page_size=25,
        filters=_voice_multi_filters(end),
    )

    sql, params = builder.build_filter_exact_zero_probe()
    squashed = _squash(sql)

    assert builder.supports_filter_exact_zero_probe() is True
    assert builder.filter_exact_zero_probe_proves_global_membership() is False
    assert squashed.count("UNION ALL") == 2
    assert "GROUP BY trace_id HAVING countIf(witness_kind = 0) > 0" in squashed
    assert "AND countIf(witness_kind = 1) > 0" in squashed
    assert "AND countIf(witness_kind = 2) > 0 LIMIT 1" in squashed
    # Each leaf is selected in its own branch. Requiring both predicates in one
    # physical-span WHERE would violate trace any-span semantics.
    root_branch, first_leaf_branch, second_leaf_branch = sql.split("UNION ALL")
    assert "parent_span_id IS NULL" in root_branch
    assert "%(latest_filter_key_0)s" in first_leaf_branch
    assert "%(latest_filter_key_1)s" not in first_leaf_branch
    assert "%(latest_filter_key_1)s" in second_leaf_branch
    assert "%(latest_filter_key_0)s" not in second_leaf_branch
    assert params["latest_filter_key_0"] == "call.total_turns"
    assert params["latest_filter_param_0"] == 2
    assert params["latest_filter_key_1"] == "conversation.transcript.16.message.role"
    assert params["latest_filter_param_1"] == ("assistant",)
    assert "attrs_number" in sql
    assert "attrs_string" in sql
    assert "span_attr_num" not in sql
    assert "span_attr_str" not in sql
    assert sql.count("SETTINGS ") == 1


@pytest.mark.unit
def test_v2_voice_long_exact_text_candidate_witness_uses_v2_schema():
    from tracer.services.clickhouse.v2.query_builders.voice_call_list import (
        VoiceCallListQueryBuilderV2,
    )

    end = datetime(2026, 8, 8, tzinfo=UTC)
    recording_url = (
        "https://storage.vapi.ai/019db06c-d54a-7003-9810-cf01cc4aa9d1-1776781471202"
    )
    filters = [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [end - timedelta(days=365), end],
            },
        },
        {
            "column_id": "conversation.recording.mono.assistant",
            "filter_config": {
                "filter_type": "text",
                "filter_op": "in",
                "filter_value": [recording_url],
                "col_type": "SPAN_ATTRIBUTE",
            },
        },
    ]
    builder = VoiceCallListQueryBuilderV2(
        project_id=PROJECT_ID,
        page_size=25,
        filters=filters,
    )

    sql, params = builder.build_filter_candidate_witness_probe(
        [{"project_id": PROJECT_ID, "trace_id": "trace-a"}]
    )

    assert builder.prefer_filter_candidate_witness_probe_first() is True
    assert builder.recommended_filter_cursor_seed_batch_size() == 512
    assert "attrs_string" in sql
    assert "span_attr_str" not in sql
    assert params["latest_filter_key_0"] == "conversation.recording.mono.assistant"
    assert params["latest_filter_param_0"] == (recording_url,)


@pytest.mark.unit
def test_voice_long_exact_text_filter_prefilters_one_finite_seed_then_hydrates():
    from tracer.selectors.trace_filter_reads import read_bounded_filter_page
    from tracer.services.clickhouse.query_service import QueryResult
    from tracer.services.clickhouse.v2.query_builders.voice_call_list import (
        VoiceCallListQueryBuilderV2,
    )

    end = datetime(2026, 8, 8)
    recording_url = (
        "https://storage.vapi.ai/019db06c-d54a-7003-9810-cf01cc4aa9d1-1776781471202"
    )
    filters = [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [end - timedelta(days=365), end],
            },
        },
        {
            "column_id": "conversation.recording.mono.assistant",
            "filter_config": {
                "filter_type": "text",
                "filter_op": "in",
                "filter_value": [recording_url],
                "col_type": "SPAN_ATTRIBUTE",
            },
        },
    ]
    candidates = [
        {
            "project_id": PROJECT_ID,
            "trace_id": f"trace-{index:02d}",
            "root_span_id": f"root-{index:02d}",
            "start_time": end - timedelta(seconds=index + 1),
        }
        for index in range(26)
    ]
    exact_matches = candidates
    hydrated = [{**row, "trace_name": "call"} for row in exact_matches[:25]]

    class Executor:
        supports_per_query_read_settings = True

        def __init__(self):
            self.calls = []
            self.results = [
                candidates,
                exact_matches,
                exact_matches[:10],
                exact_matches[10:20],
                exact_matches[20:],
                hydrated,
            ]

        def execute_ch_query(self, query, params, **kwargs):
            self.calls.append((query, params, kwargs))
            assert self.results, [
                (
                    "hydrate"
                    if "page_hydration_root_identities" in call_params
                    else "prefilter"
                    if "filter_candidate_trace_ids" in call_params
                    else "classify"
                    if "candidate_trace_ids" in call_params
                    else "seed"
                    if "filter_seed_limit" in call_params
                    else sorted(call_params)
                )
                for _call_query, call_params, _call_kwargs in self.calls
            ]
            rows = self.results.pop(0)
            return QueryResult(
                data=rows,
                row_count=len(rows),
                backend_used="clickhouse",
                query_time_ms=1,
            )

    executor = Executor()
    page = read_bounded_filter_page(
        builder=VoiceCallListQueryBuilderV2(
            project_id=PROJECT_ID,
            page_size=25,
            filters=filters,
        ),
        analytics=executor,
        filters=filters,
        key_field="trace_id",
        page_number=0,
        page_size=25,
        deadline_ms=5_000,
    )

    assert page.complete is True
    assert [row["trace_id"] for row in page.rows] == [
        row["trace_id"] for row in exact_matches[:25]
    ]
    assert [attempt.kind for attempt in page.attempts] == [
        "anchor",
        "prefilter",
        "classify",
        "classify",
        "classify",
        "hydrate",
    ]
    assert executor.calls[0][1]["filter_anchor_limit"] == 64
    assert "filter_seed_limit" not in executor.calls[0][1]
    assert executor.results == []


@pytest.mark.unit
def test_voice_exact_zero_probe_rejects_single_or_structured_filter_shapes():
    end = datetime(2026, 8, 8, tzinfo=UTC)
    single_filter = VoiceCallListQueryBuilder(
        project_id=PROJECT_ID,
        filters=_voice_multi_filters(end)[:2],
    )
    structured_filter = VoiceCallListQueryBuilder(
        project_id=PROJECT_ID,
        filters=[
            *_voice_multi_filters(end)[:2],
            {
                "column_id": "metadata",
                "filter_config": {
                    "filter_type": "map",
                    "filter_op": "contains",
                    "filter_value": {"source": "assistant"},
                    "col_type": "SPAN_ATTRIBUTE",
                },
            },
        ],
    )

    assert single_filter.supports_filter_exact_zero_probe() is False
    assert structured_filter.supports_filter_exact_zero_probe() is False
    assert structured_filter.recommended_filter_classify_batch_size() == 10


@pytest.mark.unit
def test_internal_voice_selection_retains_unhydrated_membership_projection():
    builder = VoiceCallListQueryBuilder(
        project_id=PROJECT_ID,
        page_size=10_001,
        bounded_internal_scan=True,
        bounded_identity_only=True,
    )

    assert builder.use_identity_only_filter_classification() is False
    assert builder.fill_bounded_cursor_page_across_slices() is False


@pytest.mark.unit
def test_public_voice_cursor_fills_page_across_adjacent_slices():
    builder = VoiceCallListQueryBuilder(
        project_id=PROJECT_ID,
        page_size=25,
    )

    assert builder.use_identity_only_filter_classification() is True
    assert builder.fill_bounded_cursor_page_across_slices() is True
    assert builder.recommended_filter_cursor_seed_batch_size() == 50


@pytest.mark.unit
def test_sparse_voice_cursor_amortizes_safe_identity_classifier_batches():
    end = datetime(2026, 8, 8, tzinfo=UTC)
    model_filter = {
        "column_id": "model",
        "filter_config": {
            "filter_type": "text",
            "filter_op": "in",
            "filter_value": ["gpt-4o-mini"],
            "col_type": "SYSTEM_METRIC",
        },
    }
    light_builder = VoiceCallListQueryBuilder(
        project_id=PROJECT_ID,
        page_size=15,
        filters=[
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [end - timedelta(days=365), end],
                },
            },
            model_filter,
        ],
    )
    structured_builder = VoiceCallListQueryBuilder(
        project_id=PROJECT_ID,
        page_size=15,
        filters=_voice_multi_filters(end),
    )

    assert light_builder.recommended_filter_cursor_seed_batch_size() > 16
    assert light_builder.recommended_filter_cursor_seed_batch_size() <= 512
    assert structured_builder.recommended_filter_classify_batch_size() == 10
    assert structured_builder.recommended_filter_cursor_seed_batch_size() == 40


@pytest.mark.unit
def test_voice_only_constraints_are_preserved_on_identity_classifier():
    builder = VoiceCallListQueryBuilder(
        project_id=PROJECT_ID,
        page_size=25,
        remove_simulation_calls=True,
        bounded_sampling_salt="task-1",
        bounded_sampling_rate=50,
    )

    sql, params = builder.build_filter_identity_match_query_from_seed_rows(
        [
            {
                "project_id": PROJECT_ID,
                "trace_id": "trace-1",
                "root_span_id": "root-1",
                "start_time": datetime(2026, 8, 8),
            }
        ]
    )

    assert "simulator_phone_numbers" in sql
    assert "cityHash64" in sql
    assert params["simulator_phone_numbers"] == tuple(VAPI_PHONE_NUMBERS)
    assert params["bounded_sampling_salt"] == "task-1"
    assert params["bounded_sampling_rate"] == 50


@pytest.mark.unit
def test_voice_constraints_preserve_grown_bulk_classifier_batch():
    """Simulator/sampling wrappers must not truncate finite bulk batches."""

    candidate_count = 130
    builder = VoiceCallListQueryBuilder(
        project_id=PROJECT_ID,
        page_size=10_001,
        remove_simulation_calls=True,
        bounded_sampling_salt="task-1",
        bounded_sampling_rate=100,
        bounded_internal_scan=True,
        bounded_identity_only=True,
        bounded_bulk_scan=True,
    )
    candidate_rows = [
        {
            "project_id": PROJECT_ID,
            "trace_id": f"trace-{index:03d}",
            "root_span_id": f"root-{index:03d}",
            "start_time": datetime(2026, 8, 8),
        }
        for index in range(candidate_count)
    ]

    sql, params = builder.build_filter_identity_match_query_from_seed_rows(
        candidate_rows
    )
    squashed = _squash(sql)

    assert len(params["candidate_trace_ids"]) == candidate_count
    assert "AS bounded_voice_candidates" in squashed
    assert "AS bounded_sampled_voice_candidates" in squashed
    assert squashed.count(f"LIMIT {candidate_count}") == 3
    assert "LIMIT 50" not in squashed


@pytest.mark.unit
def test_voice_bounded_reader_uses_identity_classification_before_page_hydration():
    from tracer.selectors.trace_filter_reads import read_bounded_filter_page
    from tracer.services.clickhouse.query_service import QueryResult
    from tracer.services.clickhouse.v2.query_builders.voice_call_list import (
        VoiceCallListQueryBuilderV2,
    )

    end = datetime(2026, 8, 8)
    filters = _voice_multi_filters(end)
    filters[0]["filter_config"]["filter_value"] = [
        end - timedelta(minutes=5),
        end,
    ]
    candidates = [
        {
            "project_id": PROJECT_ID,
            "trace_id": f"trace-{index:02d}",
            "root_span_id": f"root-{index:02d}",
            "start_time": end - timedelta(seconds=index + 1),
        }
        for index in range(26)
    ]
    hydrated = [
        {
            **row,
            "trace_name": f"call-{index:02d}",
            "provider": "vapi",
        }
        for index, row in enumerate(candidates[:25])
    ]

    class Executor:
        supports_per_query_read_settings = True

        def __init__(self):
            self.calls = []
            # The request-window child intersection is not a global negative
            # proof. Start directly with canonical roots, then classify their
            # descendants across all history before page hydration.
            self.results = [
                candidates,
                candidates[:10],
                candidates[10:20],
                candidates[20:],
                hydrated,
            ]

        def execute_ch_query(self, query, params, **kwargs):
            self.calls.append((query, params, kwargs))
            rows = self.results.pop(0)
            return QueryResult(
                data=rows,
                row_count=len(rows),
                backend_used="clickhouse",
                query_time_ms=1,
            )

    executor = Executor()
    page = read_bounded_filter_page(
        builder=VoiceCallListQueryBuilderV2(
            project_id=PROJECT_ID,
            page_size=25,
            filters=filters,
        ),
        analytics=executor,
        filters=filters,
        key_field="trace_id",
        page_number=0,
        page_size=25,
        deadline_ms=5_000,
    )

    assert page.complete is True
    assert [row["trace_id"] for row in page.rows] == [
        row["trace_id"] for row in hydrated
    ]
    assert [attempt.kind for attempt in page.attempts] == [
        "seed",
        "classify",
        "classify",
        "classify",
        "hydrate",
    ]
    assert "latest_trace_name AS trace_name" not in executor.calls[1][0]
    assert executor.calls[1][1]["latest_filter_key_0"] == "call.total_turns"
    assert executor.calls[1][1]["latest_filter_param_0"] == 2
    assert (
        executor.calls[1][1]["latest_filter_key_1"]
        == "conversation.transcript.16.message.role"
    )
    assert executor.calls[1][1]["latest_filter_param_1"] == ("assistant",)
    assert "latest_trace_name AS trace_name" in executor.calls[4][0]


@pytest.mark.unit
def test_voice_temporal_exact_zero_probe_never_terminates_an_empty_page():
    from tracer.selectors.trace_filter_reads import read_bounded_filter_page
    from tracer.services.clickhouse.query_service import QueryResult
    from tracer.services.clickhouse.v2.query_builders.voice_call_list import (
        VoiceCallListQueryBuilderV2,
    )

    end = datetime(2026, 8, 8)
    filters = _voice_multi_filters(end)

    class Executor:
        supports_per_query_read_settings = True

        def __init__(self):
            self.calls = []

        def execute_ch_query(self, query, params, **kwargs):
            self.calls.append((query, params, kwargs))
            return QueryResult(
                data=[],
                row_count=0,
                backend_used="clickhouse",
                query_time_ms=950,
            )

    executor = Executor()
    page = read_bounded_filter_page(
        builder=VoiceCallListQueryBuilderV2(
            project_id=PROJECT_ID,
            page_size=25,
            filters=filters,
        ),
        analytics=executor,
        filters=filters,
        key_field="trace_id",
        page_number=0,
        page_size=25,
        deadline_ms=5_000,
    )

    assert page.complete is True
    assert page.status == "complete"
    assert page.error_code is None
    assert page.rows == []
    assert page.has_more is False
    assert page.total_rows_lower_bound == 0
    assert page.attempts
    assert all(attempt.kind == "seed" for attempt in page.attempts)
    assert len(executor.calls) == len(page.attempts)
    assert all(
        "witness_kind" not in query for query, _params, _kwargs in executor.calls
    )


@pytest.mark.unit
def test_voice_root_in_window_is_not_pruned_by_remote_child_witnesses():
    """A child outside the root window is classified globally, never zeroed."""

    from tracer.selectors.trace_filter_reads import read_bounded_filter_page
    from tracer.services.clickhouse.query_service import QueryResult
    from tracer.services.clickhouse.v2.query_builders.voice_call_list import (
        VoiceCallListQueryBuilderV2,
    )

    end = datetime(2026, 8, 8)
    filters = _voice_multi_filters(end)
    filters[0]["filter_config"]["filter_value"] = [
        end - timedelta(minutes=5),
        end,
    ]
    root = {
        "project_id": PROJECT_ID,
        "trace_id": "remote-child-trace",
        "root_span_id": "conversation-root",
        "start_time": end - timedelta(minutes=1),
    }
    hydrated = {**root, "trace_name": "remote-child-call"}

    class Executor:
        supports_per_query_read_settings = True

        def __init__(self):
            self.calls = []
            # The exact classifier result represents two matching descendants
            # written outside the five-minute root window.
            self.results = [[root], [root], [hydrated]]

        def execute_ch_query(self, query, params, **kwargs):
            assert "witness_kind" not in query
            self.calls.append((query, params, kwargs))
            rows = self.results.pop(0)
            return QueryResult(
                data=rows,
                row_count=len(rows),
                backend_used="clickhouse",
                query_time_ms=1,
            )

    executor = Executor()
    page = read_bounded_filter_page(
        builder=VoiceCallListQueryBuilderV2(
            project_id=PROJECT_ID,
            page_size=1,
            filters=filters,
        ),
        analytics=executor,
        filters=filters,
        key_field="trace_id",
        page_number=0,
        page_size=1,
        deadline_ms=5_000,
    )

    assert page.complete is True
    assert [row["trace_id"] for row in page.rows] == ["remote-child-trace"]
    assert [attempt.kind for attempt in page.attempts] == [
        "seed",
        "classify",
        "hydrate",
    ]
    classifier_query, classifier_params, _ = executor.calls[1]
    assert "exact_zero_start_us" not in classifier_query
    assert "exact_zero_end_us" not in classifier_query
    assert "exact_zero_start_us" not in classifier_params
    assert "exact_zero_end_us" not in classifier_params


@pytest.mark.unit
def test_voice_request_window_zero_probe_is_skipped_before_global_exact_scan():
    from tracer.selectors.trace_filter_reads import read_bounded_filter_page
    from tracer.services.clickhouse.query_service import QueryResult
    from tracer.services.clickhouse.v2.query_builders.voice_call_list import (
        VoiceCallListQueryBuilderV2,
    )

    end = datetime(2026, 8, 8)
    filters = _voice_multi_filters(end)
    candidates = [
        {
            "project_id": PROJECT_ID,
            "trace_id": f"trace-{index:02d}",
            "root_span_id": f"root-{index:02d}",
            "start_time": end - timedelta(seconds=index + 1),
        }
        for index in range(26)
    ]
    hydrated = [{**row, "trace_name": "call"} for row in candidates[:25]]

    class Executor:
        supports_per_query_read_settings = True

        def __init__(self):
            self.calls = 0
            self.results = [
                candidates,
                candidates[:10],
                candidates[10:20],
                candidates[20:],
                hydrated,
            ]

        def execute_ch_query(self, query, params, **kwargs):
            self.calls += 1
            rows = self.results.pop(0)
            return QueryResult(
                data=rows,
                row_count=len(rows),
                backend_used="clickhouse",
                query_time_ms=1,
            )

    page = read_bounded_filter_page(
        builder=VoiceCallListQueryBuilderV2(
            project_id=PROJECT_ID,
            page_size=25,
            filters=filters,
        ),
        analytics=Executor(),
        filters=filters,
        key_field="trace_id",
        page_number=0,
        page_size=25,
        deadline_ms=5_000,
    )

    assert page.complete is True
    assert len(page.rows) == 25
    assert [attempt.kind for attempt in page.attempts] == [
        "seed",
        "classify",
        "classify",
        "classify",
        "hydrate",
    ]


@pytest.mark.unit
def test_voice_global_exact_scan_keeps_required_failure_fatal():
    from tracer.selectors.trace_filter_reads import read_bounded_filter_page
    from tracer.services.clickhouse.query_service import QueryResult
    from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded
    from tracer.services.clickhouse.v2.query_builders.voice_call_list import (
        VoiceCallListQueryBuilderV2,
    )

    end = datetime(2026, 8, 8)
    filters = _voice_multi_filters(end)
    candidates = [
        {
            "project_id": PROJECT_ID,
            "trace_id": f"trace-{index:02d}",
            "root_span_id": f"root-{index:02d}",
            "start_time": end - timedelta(seconds=index + 1),
        }
        for index in range(26)
    ]

    class Executor:
        supports_per_query_read_settings = True

        def __init__(self):
            self.calls = 0

        def execute_ch_query(self, query, params, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return QueryResult(
                    data=candidates,
                    row_count=len(candidates),
                    backend_used="clickhouse",
                    query_time_ms=1,
                )
            raise ReadDeadlineExceeded("required classifier cap")

    page = read_bounded_filter_page(
        builder=VoiceCallListQueryBuilderV2(
            project_id=PROJECT_ID,
            page_size=25,
            filters=filters,
        ),
        analytics=Executor(),
        filters=filters,
        key_field="trace_id",
        page_number=0,
        page_size=25,
        deadline_ms=5_000,
    )

    assert page.complete is False
    assert page.rows == []
    assert page.error_code == "read_budget_exceeded"
    assert [attempt.kind for attempt in page.attempts] == [
        "seed",
        "classify",
    ]
    assert page.attempts[-1].error_code == "read_budget_exceeded"


# ---------------------------------------------------------------------------
# build_id_query — same predicate/window, no pagination/order limit params
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_id_query_selects_only_ids():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID)
    sql, _ = qb.build_id_query()
    s = _squash(sql)
    assert "SELECT id FROM spans" in s
    assert "observation_type = 'conversation'" in s
    assert "LIMIT 1 BY trace_id" in s
    # No page-limit/offset — resolver wants the full matched id set.
    assert "%(limit)s" not in s
    assert "%(offset)s" not in s


@pytest.mark.unit
def test_id_query_continuous_floor_windows_on_created_at():
    floor = datetime(2026, 8, 1, 12, 0)
    sql, params = VoiceCallListQueryBuilder(project_id=PROJECT_ID).build_id_query(
        created_at_floor=floor
    )
    s = _squash(sql)
    # Arrival floor replaces the start_time window — a call can start long before
    # its root span reaches CH (Vapi end-of-call flush).
    assert "created_at >= %(created_at_floor)s" in s
    assert "start_time >= %(start_date)s" not in s
    assert params["created_at_floor"] == floor


@pytest.mark.unit
def test_id_query_continuous_ceiling_upper_bounds_arrival():
    floor = datetime(2026, 8, 1, 12, 0)
    ceil = datetime(2026, 8, 1, 12, 5)
    sql, params = VoiceCallListQueryBuilder(project_id=PROJECT_ID).build_id_query(
        created_at_floor=floor, created_at_ceiling=ceil
    )
    s = _squash(sql)
    assert "created_at >= %(created_at_floor)s" in s
    assert "created_at < %(created_at_ceiling)s" in s
    assert params["created_at_ceiling"] == ceil


@pytest.mark.unit
def test_id_query_ceiling_ignored_without_floor():
    sql, params = VoiceCallListQueryBuilder(project_id=PROJECT_ID).build_id_query(
        created_at_ceiling=datetime(2026, 8, 1, 12, 5)
    )
    assert "created_at_ceiling" not in params
    assert "start_time >= %(start_date)s" in _squash(sql)


# ---------------------------------------------------------------------------
# build_content_query — heavy attribute columns for a page of span ids
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_content_query_fetches_heavy_columns():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID)
    qb.build()
    sql, params = qb.build_content_query(["s1", "s2"])
    s = _squash(sql)
    assert "span_attributes_raw" in s
    assert "PREWHERE id IN %(content_span_ids)s" in s
    assert "project_id = %(project_id)s" in s
    assert "argMax(_peerdb_is_deleted, _peerdb_version)" in s
    assert "WHERE latest_is_deleted = 0" in s
    assert params["content_span_ids"] == ("s1", "s2")


@pytest.mark.unit
def test_content_query_empty_span_ids_returns_empty():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID)
    sql, params = qb.build_content_query([])
    assert sql == ""
    assert params == {}


@pytest.mark.unit
def test_v2_content_query_uses_valid_latest_json_aggregate():
    from tracer.services.clickhouse.v2.query_builders.voice_call_list import (
        VoiceCallListQueryBuilderV2,
    )

    sql, _ = VoiceCallListQueryBuilderV2(project_id=PROJECT_ID).build_content_query(
        ["s1"]
    )

    assert "argMax(tuple(attributes_extra), _version).1" in sql
    assert "attributes_extra AS span_attributes_raw" not in sql
    assert "tuple(attributes_extra AS" not in sql


# ---------------------------------------------------------------------------
# build_eval_query — Phase 2 eval scores (NOT rewritten by v2)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_eval_query_groups_by_trace_and_config():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID, eval_config_ids=["c1", "c2"])
    sql, params = qb.build_eval_query(["t1", "t2"])
    s = _squash(sql)
    assert "GROUP BY latest_trace_id, latest_eval_config_id" in s
    assert params["trace_ids"] == ("t1", "t2")
    assert params["eval_config_ids"] == ("c1", "c2")


@pytest.mark.unit
def test_eval_query_averages_across_all_spans():
    """avgIf/countIf/groupArrayIf aggregate over every eval row in the group,
    not just the root span."""
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID, eval_config_ids=["c1"])
    sql, _ = qb.build_eval_query(["t1"])
    s = _squash(sql)
    assert "avgIf(" in s and "latest_output_float" in s
    assert "groupArrayIf(" in s and "latest_output_str_list" in s
    assert "output_str_list" in s
    assert "pass_rate" in s
    assert "avg_score" in s


@pytest.mark.unit
@override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger")
def test_eval_query_replays_legacy_latest_state_without_final():
    """Legacy CDC versions/tombstones are collapsed only for page candidates."""
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID, eval_config_ids=["c1"])
    sql, _ = qb.build_eval_query(["t1"])
    s = _squash(sql)
    assert "FROM tracer_eval_logger PREWHERE trace_id IN" in s
    assert "argMax(_peerdb_is_deleted, _peerdb_version)" in s
    assert "coalesce(argMax(deleted, _peerdb_version), 0)" in s
    assert "WHERE latest_is_deleted = 0" in s
    assert "FINAL" not in s


@pytest.mark.unit
def test_eval_query_excludes_non_terminal_and_errored_rows():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID, eval_config_ids=["c1"])
    sql, _ = qb.build_eval_query(["t1"])
    s = _squash(sql)
    assert "latest_status NOT IN ('pending', 'running', 'skipped', 'errored')" in s
    assert "latest_error = 0" in s


@pytest.mark.unit
@override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
def test_eval_query_uses_direct_write_v2_latest_state_shape():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID, eval_config_ids=["c1"])
    sql, _ = qb.build_eval_query(["t1"])
    s = _squash(sql)
    assert "FROM tracer_eval_logger_v2 PREWHERE" in s
    assert "argMax(is_deleted, _version) AS latest_is_deleted" in s
    assert "'completed' AS latest_status" in s
    assert "_peerdb" not in s
    assert "FINAL" not in s


@pytest.mark.unit
def test_eval_query_empty_trace_ids_returns_empty():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID, eval_config_ids=["c1"])
    sql, params = qb.build_eval_query([])
    assert sql == ""
    assert params == {}


@pytest.mark.unit
def test_eval_query_no_eval_configs_returns_empty():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID, eval_config_ids=[])
    sql, params = qb.build_eval_query(["t1"])
    assert sql == ""
    assert params == {}


# ---------------------------------------------------------------------------
# build_annotation_query — Phase 3
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_annotation_query_joins_spans_and_scopes():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID)
    sql, params = qb.build_annotation_query(["t1"], annotation_label_ids=["l1", "l2"])
    s = _squash(sql)
    assert "FROM model_hub_score AS s FINAL" in s
    assert "LEFT JOIN spans AS sp" in s
    assert "s.label_id IN %(label_ids)s" in s
    assert "s._peerdb_is_deleted = 0" in s
    assert "s.deleted = false" in s
    assert params["trace_ids"] == ("t1",)
    assert params["label_ids"] == ("l1", "l2")


@pytest.mark.unit
def test_annotation_query_empty_trace_ids_returns_empty():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID)
    sql, params = qb.build_annotation_query([], annotation_label_ids=["l1"])
    assert sql == ""
    assert params == {}


@pytest.mark.unit
def test_annotation_query_no_label_ids_returns_empty():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID)
    sql, params = qb.build_annotation_query(["t1"], annotation_label_ids=[])
    assert sql == ""
    assert params == {}


@pytest.mark.unit
def test_annotation_query_none_label_ids_returns_empty():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID)
    sql, params = qb.build_annotation_query(["t1"], annotation_label_ids=None)
    assert sql == ""
    assert params == {}


# ---------------------------------------------------------------------------
# build_child_spans_query — Phase 4
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_child_spans_query_fetches_non_root_spans():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID)
    sql, params = qb.build_child_spans_query(["t1", "t2"])
    s = _squash(sql)
    assert "FROM spans" in s
    assert "parent_span_id IS NOT NULL" in s
    assert "trace_id IN %(trace_ids)s" in s
    assert "project_id = %(project_id)s" in s
    assert "is_deleted = 0" in s
    assert "ORDER BY start_time ASC" in s
    assert params["trace_ids"] == ("t1", "t2")
    assert params["project_id"] == PROJECT_ID


@pytest.mark.unit
def test_child_spans_query_selects_heavy_columns():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID)
    sql, _ = qb.build_child_spans_query(["t1"])
    s = _squash(sql)
    for col in ("span_attributes_raw", "input", "output", "metadata_map", "tags"):
        assert col in s


@pytest.mark.unit
def test_child_spans_query_empty_trace_ids_returns_empty():
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID)
    sql, params = qb.build_child_spans_query([])
    assert sql == ""
    assert params == {}


# ---------------------------------------------------------------------------
# Filters embedded via ClickHouseFilterBuilder
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_embeds_frontend_filter_fragment():
    """A frontend filter must be compiled by the filter builder and spliced
    into build() with an `AND` prefix + bound param."""
    filters = [
        {
            "column_id": "status",
            "filter_config": {
                "filter_type": "string",
                "filter_op": "equals",
                "filter_value": "error",
                "col_type": "ATTRIBUTE",
            },
        }
    ]
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID, filters=filters)
    sql, params = qb.build()
    s = _squash(sql)
    # Filter param value bound (not inlined literal in the ORDER BY tail).
    assert any(v == "error" for v in params.values())
    # status is a case-insensitive system-metric column: the compiled filter
    # emits a trace_id subquery comparing lowerUTF8(toString(status)). That fragment (which
    # only the filter builder produces) must be spliced in before ORDER BY.
    assert "lowerUTF8(toString(status)) =" in s
    assert s.index("lowerUTF8(toString(status)) =") < s.index("ORDER BY")


@pytest.mark.unit
def test_count_query_embeds_same_filter():
    filters = [
        {
            "column_id": "status",
            "filter_config": {
                "filter_type": "string",
                "filter_op": "equals",
                "filter_value": "error",
                "col_type": "ATTRIBUTE",
            },
        }
    ]
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID, filters=filters)
    qb.build()
    _, params = qb.build_count_query()
    assert any(v == "error" for v in params.values())


@pytest.mark.unit
def test_time_range_filter_narrows_window_params():
    filters = [
        {
            "column_id": "start_time",
            "filter_config": {
                "filter_op": "between",
                "filter_value": [
                    "2026-01-01T00:00:00Z",
                    "2026-01-31T00:00:00Z",
                ],
            },
        }
    ]
    qb = VoiceCallListQueryBuilder(project_id=PROJECT_ID, filters=filters)
    _, params = qb.build()
    assert params["start_date"].year == 2026 and params["start_date"].month == 1
    assert params["end_date"].day == 31


@pytest.mark.unit
def test_v2_voice_builder_uses_direct_write_query_service(monkeypatch):
    from tracer.services.clickhouse.query_service import AnalyticsQueryService
    from tracer.services.clickhouse.v2 import query_service as query_service_module
    from tracer.services.clickhouse.v2.query_builders.voice_call_list import (
        VoiceCallListQueryBuilderV2,
    )

    fallback = object.__new__(AnalyticsQueryService)
    direct_write_service = object()
    monkeypatch.setattr(
        query_service_module,
        "V2AnalyticsQueryService",
        lambda: direct_write_service,
    )

    assert (
        query_service_module.query_service_for_builder(
            "VOICE_CALL_LIST", VoiceCallListQueryBuilderV2, fallback
        )
        is direct_write_service
    )
