"""Long text may change acquisition, never exact membership or child scope."""

from datetime import timedelta

import pytest

from tracer.services.clickhouse.query_builders.trace_list import TraceListQueryBuilder
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    _caseless_ascii_ngram_anchor,
)
from tracer.tests.test_bounded_trace_filter_reads import (
    _attribute_filter,
    _render_driver_sql,
)
from tracer.tests.test_trace_indexed_coordinate_reads import END, builder
from tracer.tests.test_trace_root_physical_replay import assert_coherent_classifier

pytestmark = pytest.mark.unit
LONG_TEXT = "long literal %_\\ café " * 8


@pytest.mark.parametrize(
    "operation", ["equals", "in", "contains", "starts_with", "ends_with"]
)
@pytest.mark.parametrize("width", [1, 2, 5, 10])
def test_long_text_candidate_is_root_window_scoped_not_child_time_scoped(
    operation, width
):
    value = [LONG_TEXT, LONG_TEXT + "second"] if operation == "in" else LONG_TEXT
    subject = builder(width, operation=operation, value=value)
    assert subject._public_long_text_candidate_seed_plan() is not None
    assert subject.supports_filter_candidate_seed_page()
    assert not subject.supports_filter_anchor_probe()
    assert not subject.filter_candidate_seed_is_optional()
    assert not subject.supports_filter_windowed_candidate_seed_page()
    assert subject.recommended_filter_initial_slice_width() == timedelta(days=365)
    sql, params = subject.build_filter_candidate_seed_page(
        slice_start=END - timedelta(days=365), slice_end=END, limit=50
    )
    cte = sql.split("SELECT trace_id, id AS root_span_id", 1)[0]
    assert "matching_scalar_trace_identities" in cte
    assert "AND trace_id IN (" in cte
    assert "WHERE parent_span_id IS NULL OR parent_span_id = ''" in cte
    assert "attrs_string[" in cte
    assert (
        "indexHint(arrayStringConcat(arrayMap(x -> lower(x), mapValues(attrs_string))) LIKE"
        in cte
    )
    assert "LIMIT" not in cte
    assert "is_deleted" not in cte
    assert "filter_before" not in cte
    assert cte.count("start_time >=") == 1  # Only raw root population, not children.
    assert cte.count("start_time <") == 1
    assert "ORDER BY start_time DESC, trace_id DESC" in sql
    assert "LIMIT %(filter_seed_limit)s" in sql
    assert params["filter_seed_limit"] == 50
    assert LONG_TEXT not in sql
    _render_driver_sql(sql, params)
    exact, exact_params = subject.build_filter_identity_match_query_from_seed_rows(
        [{"trace_id": "candidate", "root_span_id": "not-authoritative"}]
    )
    assert_coherent_classifier(exact)
    assert "attrs_string[" in exact.split("AS _physical_winner", 1)[0]
    assert (
        "GROUP BY observation_type, service_name, toStartOfHour(start_time), trace_id, id"
        in exact
    )
    assert "WHERE latest_is_deleted = 0" in exact
    assert "not-authoritative" not in str(exact_params)
    for index in range(width):
        assert f"latest_attr_value_{index}" in exact
        assert (
            f"AS latest_attr_value_{index}" in exact.split("AS _physical_winner", 1)[1]
        )


@pytest.mark.parametrize("value", ["ik" * 100, "É" * 100, "K" * 100, "İ" * 100])
def test_unanchorable_text_keeps_existing_exact_route(value):
    assert _caseless_ascii_ngram_anchor(value) is None
    assert builder(value=value)._public_long_text_candidate_seed_plan() is None


def test_index_anchor_escapes_literal_wildcards_and_preserves_nonletters():
    assert _caseless_ascii_ngram_anchor("words 123456 words") == "%words 123456 words%"
    assert _caseless_ascii_ngram_anchor("a%_\\  b") == "%a\\%\\_\\\\  b%"
    assert _caseless_ascii_ngram_anchor("K 00001 İ") == "% 00001 %"


def test_each_multi_value_branch_must_have_a_necessary_index_anchor():
    subject = builder(operation="in", value=[LONG_TEXT, "ik" * 100])
    assert subject._public_long_text_candidate_seed_plan() is None
    subject = builder(operation="in", value=[LONG_TEXT, "a 987654 b" * 10])
    plan = subject._public_long_text_candidate_seed_plan()
    assert plan is not None
    assert " OR " in plan.raw_graph_value_witness_predicate
    assert plan.params["long_text_ngram_1"] == "%" + "a 987654 b" * 10 + "%"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("É Outbound Response İ", "% outbound response %"),
        ("K bOOKkeepER ReSponSe İ", "% boo%eeper response %"),
        ("Kİ" + "A" * 100 + "É", "%" + "a" * 100 + "%"),
        ("abcİdefKghi", None),
        (
            "a common messageα000123βanother response",
            "%a common message%000123%another response%",
        ),
        ("abcİdefKlast_%\\fragment", "%last\\_\\%\\\\fragment%"),
    ],
)
def test_letter_anchors_split_unicode_ambiguous_ascii_and_lower_only_safe_run(
    value, expected
):
    assert _caseless_ascii_ngram_anchor(value) == expected


@pytest.mark.parametrize(
    "operation", ["equals", "in", "contains", "starts_with", "ends_with"]
)
def test_prose_without_four_digits_can_use_necessary_index_hint(operation):
    text = "É Outbound response and customer message " * 3
    value = [text, text + " end"] if operation == "in" else text
    subject = builder(operation=operation, value=value)
    plan = subject._public_long_text_candidate_seed_plan()
    assert plan is not None
    assert "lowerUTF8" in plan.raw_graph_value_witness_predicate
    assert "indexHint(arrayStringConcat" in plan.raw_graph_value_witness_predicate
    assert all(
        "i" not in v and "k" not in v
        for k, v in plan.params.items()
        if k.startswith("long_text_ngram_")
    )


@pytest.mark.parametrize("speculative_caps", [False, True])
@pytest.mark.parametrize("has_match", [False, True])
def test_long_text_primary_seed_runs_without_speculative_caps_and_replays_exactly(
    speculative_caps, has_match
):
    from tracer.selectors.trace_filter_reads import read_bounded_filter_page
    from tracer.services.clickhouse.query_service import QueryResult
    from tracer.tests.test_trace_root_physical_replay import complete_root_row

    subject = builder(value="É Outbound response and customer message " * 3)
    start, end = subject.parse_time_range(subject.filters)
    row = complete_root_row(
        {
            "trace_id": "matched-trace",
            "root_span_id": "root",
            "start_time": start + timedelta(days=1),
        },
        project_id=str(subject.project_id),
    )
    calls = []

    class Transport:
        supports_bounded_speculative_reads = speculative_caps

        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            calls.append(query)
            if len(calls) == 1:
                assert "matching_scalar_trace_identities" in query
                assert "indexHint(arrayStringConcat" in query
                assert params["filter_slice_start"] == start
                assert params["filter_slice_end"] == end
                assert timeout_ms == 2500  # Normal caller value, not the 1500ms probe.
                assert settings["max_memory_usage"] > 0
            elif len(calls) == 2:
                assert_coherent_classifier(query)
                assert params["candidate_trace_ids"] == ("matched-trace",)
            else:
                assert len(calls) == 3  # Page hydration, never an unrelated root walk.
            rows = [row] if has_match else []
            return QueryResult(
                data=rows,
                row_count=len(rows),
                backend_used="clickhouse",
                query_time_ms=1,
            )

    page = read_bounded_filter_page(
        builder=subject,
        analytics=Transport(),
        filters=subject.filters,
        key_field="trace_id",
        page_number=0,
        page_size=25,
        deadline_ms=9500,
        query_timeout_ms=2500,
        max_query_count=8,
        max_seed_attempts=1,
        include_incomplete_rows=True,
        bounded_continuation=True,
    )
    assert page.complete and not page.has_more
    assert page.rows == ([row] if has_match else [])
    assert len(calls) == (3 if has_match else 1)


@pytest.mark.parametrize(
    "operation,value",
    [
        ("equals", "short"),
        ("contains", "short"),
        ("in", [LONG_TEXT, "short"]),
        ("not_equals", LONG_TEXT),
        ("not_in", [LONG_TEXT]),
        ("not_contains", LONG_TEXT),
        ("is_null", None),
        ("is_not_null", None),
    ],
)
def test_dense_missing_and_negative_text_keep_existing_acquisition(operation, value):
    subject = builder(operation=operation, value=value)
    assert subject._public_long_text_candidate_seed_plan() is None
    assert subject._public_scalar_candidate_seed_plan() is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"bounded_internal_scan": True},
        {"bounded_identity_only": True},
        {"search": LONG_TEXT},
        {"bounded_sampling_salt": "test", "bounded_sampling_rate": 10},
    ],
)
def test_other_modes_do_not_acquire_the_public_text_seed(kwargs):
    assert (
        builder(value=LONG_TEXT, **kwargs)._public_long_text_candidate_seed_plan()
        is None
    )


def test_numeric_candidate_priority_and_windowed_fallback_are_unchanged():
    subject = builder(kind="number", operation="greater_than", value=0.01)
    subject.filters.append(_attribute_filter("description", LONG_TEXT))
    assert (
        "span_attr_num["
        in subject._public_scalar_candidate_seed_plan().raw_graph_value_witness_predicate
    )
    assert subject.supports_filter_windowed_candidate_seed_page()
    sql, _ = subject.build_filter_candidate_seed_page(
        slice_start=END - timedelta(days=365), slice_end=END, limit=50
    )
    cte = sql.split("SELECT trace_id, id AS root_span_id", 1)[0]
    assert "attrs_number[" in cte
    assert "parent_span_id" not in cte


def test_legacy_and_structured_json_keep_their_existing_path():
    assert (
        builder(
            value=LONG_TEXT, cls=TraceListQueryBuilder
        )._public_scalar_candidate_seed_plan()
        is None
    )
    assert (
        builder(
            kind="map", value={"text": LONG_TEXT}
        )._public_long_text_candidate_seed_plan()
        is None
    )


def test_page_keyset_does_not_limit_the_necessary_child_witness():
    subject = builder(value=LONG_TEXT)
    sql, params = subject.build_filter_candidate_seed_page(
        slice_start=END - timedelta(days=365),
        slice_end=END,
        limit=50,
        before_start_time=END - timedelta(days=1),
        before_id="previous",
    )
    cte, roots = sql.split("SELECT trace_id, id AS root_span_id", 1)
    assert "filter_before" not in cte
    assert "filter_before_start_us" in roots
    assert params["filter_before_id"] == "previous"
