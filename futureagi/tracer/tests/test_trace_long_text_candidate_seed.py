"""Long text may change acquisition, never exact membership or child scope."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from clickhouse_driver.util.escape import escape_chars_map, escape_params
from django.test import override_settings

from tracer.services.clickhouse.query_builders import latest_filter_predicates
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    _CLICKHOUSE_MAX_QUERY_SIZE_BYTES,
    _ESCAPED_LITERAL_CHARS,
    _rendered_literal_bytes,
)
from tracer.services.clickhouse.query_builders.trace_list import TraceListQueryBuilder
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    _MAX_NGRAM_ANCHOR_BYTES,
    TraceListQueryBuilderV2,
    _caseless_ascii_ngram_anchor,
    _runs_within_anchor_budget,
)
from tracer.tests.test_bounded_trace_filter_reads import (
    _attribute_filter,
    _render_driver_sql,
    _time_filter,
)
from tracer.tests.test_trace_indexed_coordinate_reads import END, PROJECT, builder
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
    cte = sql.split("SELECT trace_id, start_time", 1)[0]
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
    cte = sql.split("SELECT trace_id, start_time", 1)[0]
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
    cte, roots = sql.split("SELECT trace_id, start_time", 1)
    assert "filter_before" not in cte
    assert "filter_before_start_us" in roots
    assert params["filter_before_id"] == "previous"


def test_the_long_text_lane_is_byte_identical_while_its_slack_setting_is_zero():
    """The default keeps the unbounded, full-width statement this lane ships."""

    subject = builder(value=LONG_TEXT)
    start = END - timedelta(days=365)
    assert subject._uses_wide_candidate_seed()
    assert subject.filter_seed_width_policy() is None
    assert not subject.supports_filter_seed_density_probe()
    assert subject.filter_seed_witness_slack_hours() is None
    assert subject.recommended_filter_initial_slice_width() == timedelta(days=365)
    sql, params = subject.build_filter_candidate_seed_page(
        slice_start=start, slice_end=END, limit=50
    )
    assert "filter_witness_start_us" not in sql
    assert "filter_witness_start" not in params


@override_settings(FILTER_SELECTOR_NUMERIC_LONG_TEXT_SEED_WITNESS_SLACK_HOURS=1)
def test_the_long_text_lane_takes_the_bounded_schedule_once_its_slack_is_set():
    """The ngram anchor and the envelope bound the same witness together."""

    subject = builder(value=LONG_TEXT)
    policy = subject.filter_seed_width_policy()
    assert policy is not None
    assert policy.min_width == timedelta(hours=1)
    assert subject.supports_filter_seed_density_probe()
    assert subject.filter_seed_witness_slack_hours() == 1
    assert subject.recommended_filter_initial_slice_width() == timedelta(hours=1)
    slice_start = END - timedelta(hours=1)
    sql, params = subject.build_filter_candidate_seed_page(
        slice_start=slice_start, slice_end=END, limit=50
    )
    cte = sql.split("SELECT trace_id, id AS root_span_id", 1)[0]
    assert "indexHint(arrayStringConcat(" in cte
    assert "filter_witness_start_us" in cte
    assert params["filter_witness_start"] == slice_start - timedelta(hours=1)
    assert params["filter_witness_end"] == END + timedelta(hours=1)
    _render_driver_sql(sql, params)


@override_settings(FILTER_SELECTOR_NUMERIC_LONG_TEXT_SEED_WITNESS_SLACK_HOURS=4)
def test_the_short_text_lane_keeps_its_own_approved_slack():
    """Each lane reads its own setting; the two contracts never cross."""

    subject = builder(value="001234")
    assert subject._uses_short_text_candidate_seed()
    assert not subject._uses_wide_candidate_seed()
    assert subject.filter_seed_witness_slack_hours() == 1
    assert subject.filter_seed_width_policy().min_width == timedelta(hours=1)
    # And the short lane's absent-field behaviour is untouched by the wide
    # lanes' chain rule: it writes its own slack into every cursor it mints,
    # including zero, so an absent field there is only a pre-field token and
    # returns the builder to its own approved setting, as it always has.
    subject.pin_filter_seed_witness_slack_hours(None)
    assert subject.filter_seed_witness_slack_hours() == 1


# No letter here is an i or a k, so every value below anchors whole: the worst
# case for a statement that inlines the anchor beside the exact literal.
_ANCHORABLE_UNIT = "a common message 000123 another response. "
OVERSIZED_TEXT = _ANCHORABLE_UNIT * 2600
# Past the seed's inline budget even on its own, so the seed must stand down.
UNSEEDABLE_TEXT = _ANCHORABLE_UNIT * 5620
ORDINARY_LONG_TEXT = _ANCHORABLE_UNIT * 60
_WINDOW = {"slice_start": END - timedelta(days=365), "slice_end": END, "limit": 50}


def _rendered_seed_statement(subject) -> str:
    """The statement the route this builder chose actually hands the parser."""

    if subject.supports_filter_candidate_seed_page():
        sql, params = subject.build_filter_candidate_seed_page(**_WINDOW)
    else:
        sql, params = subject.build_filter_seed_page(**_WINDOW)
    return _render_driver_sql(sql, params)


@pytest.mark.parametrize("operation", ["equals", "in"])
def test_oversized_text_keeps_the_statement_inside_the_parser_limit(operation):
    """ClickHouse refuses an oversized statement before it runs, so never send one.

    One oversized value still fits the seed once its anchor is bounded; two do
    not, and fall back to the ordinary exact route. Either way the statement
    the builder chooses must reach the parser intact.
    """

    value = (
        [OVERSIZED_TEXT, OVERSIZED_TEXT + " tail"]
        if operation == "in"
        else OVERSIZED_TEXT
    )
    subject = builder(operation=operation, value=value)
    assert _caseless_ascii_ngram_anchor(OVERSIZED_TEXT) is not None
    # The bounded anchor is small enough that the seed lane survives here.
    assert subject.supports_filter_candidate_seed_page()
    rendered = _rendered_seed_statement(subject)
    assert "indexHint(has(arrayMap" not in rendered
    assert "indexHint(hasAny(arrayMap" not in rendered
    assert len(rendered.encode()) < _CLICKHOUSE_MAX_QUERY_SIZE_BYTES


@pytest.mark.parametrize("operation", ["equals", "in"])
def test_ordinary_long_text_still_seeds_and_still_hints(operation):
    """The size limits drop oversized values only; ordinary long text is untouched."""

    value = (
        [ORDINARY_LONG_TEXT, ORDINARY_LONG_TEXT + " tail"]
        if operation == "in"
        else ORDINARY_LONG_TEXT
    )
    subject = builder(operation=operation, value=value)
    assert subject._public_long_text_candidate_seed_plan() is not None
    assert subject.supports_filter_candidate_seed_page()
    rendered = _rendered_seed_statement(subject)
    assert "arrayMap(x -> lowerUTF8(x), mapValues(attrs_string))" in rendered
    assert len(rendered.encode()) < _CLICKHOUSE_MAX_QUERY_SIZE_BYTES


def test_oversized_mixed_typed_text_keeps_the_statement_parseable():
    """The picker-provenance IN path inlines the same way and takes the same limit."""

    subject = TraceListQueryBuilderV2(
        project_id=PROJECT,
        filters=[
            _time_filter(END - timedelta(days=365), END),
            {
                "column_id": "attribute_0",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": "in",
                    "filter_value": [OVERSIZED_TEXT, OVERSIZED_TEXT + " tail"],
                    "attribute_value_types": ["string", "string"],
                },
            },
        ],
        page_size=25,
    )
    rendered = _rendered_seed_statement(subject)
    assert "indexHint(hasAny(arrayMap" not in rendered
    assert len(rendered.encode()) < _CLICKHOUSE_MAX_QUERY_SIZE_BYTES


def _index_hint_spans(sql: str) -> list[str]:
    """Every ``indexHint(...)`` subexpression, parentheses balanced.

    Safe for the synthetic values in this module, which carry no parenthesis.
    """

    spans = []
    start = sql.find("indexHint(")
    while start >= 0:
        cursor, depth = start + len("indexHint("), 1
        while cursor < len(sql) and depth:
            depth += {"(": 1, ")": -1}.get(sql[cursor], 0)
            cursor += 1
        spans.append(sql[start:cursor])
        start = sql.find("indexHint(", cursor)
    return spans


def _outside_index_hints(sql: str) -> str:
    for span in _index_hint_spans(sql):
        sql = sql.replace(span, "")
    return sql


@pytest.mark.parametrize("operation", ["equals", "in"])
def test_index_companions_exist_only_inside_index_hints(operation, monkeypatch):
    """``indexHint`` is true for every row, so declining one cannot drop a row.

    That is what makes the size limit safe rather than a semantic change, and
    it holds only while the companions live nowhere but inside a hint.
    """

    value = (
        [ORDINARY_LONG_TEXT, ORDINARY_LONG_TEXT + " tail"]
        if operation == "in"
        else ORDINARY_LONG_TEXT
    )
    with_sql, _ = builder(operation=operation, value=value).build_filter_seed_page(
        **_WINDOW
    )
    monkeypatch.setattr(
        latest_filter_predicates, "_MAX_INDEX_COMPANION_VALUE_UTF8_BYTES", 0
    )
    without_sql, _ = builder(operation=operation, value=value).build_filter_seed_page(
        **_WINDOW
    )

    hints = _index_hint_spans(with_sql)
    assert hints, "the companion-bearing statement must carry index hints"
    for companion in (
        "arrayMap(x -> lowerUTF8(x), mapValues(attrs_string))",
        "arrayMap(x -> lower(x), mapValues(attrs_string))",
    ):
        assert with_sql.count(companion) == sum(hint.count(companion) for hint in hints)
        assert companion not in _outside_index_hints(without_sql)

    # Outside every hint the two statements are the same predicate, bound to
    # the same parameters, so they select the same rows.
    assert _outside_index_hints(with_sql).count("latest_filter_param_0") == (
        _outside_index_hints(without_sql).count("latest_filter_param_0")
    )
    assert "latest_filter_legacy_index_" not in _outside_index_hints(with_sql)
    assert "latest_filter_legacy_index_" not in without_sql
    assert "latest_filter_index_" not in without_sql


def _anchor_runs(anchor: str) -> list[str]:
    return [run for run in anchor.split("%") if run]


def _occurs_in_order(fragments: list[str], text: str) -> bool:
    cursor = 0
    for fragment in fragments:
        found = text.find(fragment, cursor)
        if found < 0:
            return False
        cursor = found + len(fragment)
    return True


# Units carrying an i split into many runs, so the anchor must choose among
# them rather than truncate one.
MANY_RUN_TEXT = "an indexed message 000123 with a distinct reply. " * 2400


@pytest.mark.parametrize(
    "value", [OVERSIZED_TEXT, MANY_RUN_TEXT], ids=["one-run", "many-runs"]
)
def test_bounded_anchor_stays_a_necessary_condition(value):
    """Every kept fragment is a substring of the value, in the value's order.

    A row holding the whole value holds every substring of it, so a shorter
    anchor can only widen the granule set, never hide a matching row.

    Stated as substring-in-order on purpose, not as a subset of the runs. A
    value with no run boundary in it is a single run, and the budget then keeps
    a prefix of that one run rather than a subset of several. A prefix is still
    a substring in value order, so the necessity property holds either way and
    this is the invariant that covers both shapes. Please do not narrow it back
    to a subset check.
    """

    bounded = _caseless_ascii_ngram_anchor(value)
    assert bounded is not None
    assert len(bounded.encode()) <= _MAX_NGRAM_ANCHOR_BYTES + 2
    kept = [fragment for fragment in bounded.split("%") if fragment]
    assert kept
    assert _occurs_in_order(kept, value.lower())


def test_bounded_anchor_prefers_the_longest_runs_and_keeps_value_order():
    """Longer runs carry more four-grams per byte, so they are taken first.

    Whatever survives is emitted in the value's own order, because the LIKE
    pattern requires its fragments in that order to stay a necessary condition.
    """

    runs = ["a" * 40, "b" * 4000, "c" * 40, "d" * 4000]
    kept = _runs_within_anchor_budget(runs)
    # The first 4000-run fits and the second no longer does; the short runs
    # then fill the remainder, and the result stays in the original order.
    assert kept == ["a" * 40, "b" * 4000, "c" * 40]
    assert sum(len(run) + 1 for run in kept) <= _MAX_NGRAM_ANCHOR_BYTES
    # An anchor that already fits is returned untouched.
    assert _runs_within_anchor_budget(["e" * 40, "f" * 40]) == ["e" * 40, "f" * 40]


def test_short_anchor_is_unbounded_and_unchanged():
    """The budget must not touch anchors that already fit."""

    assert _caseless_ascii_ngram_anchor("words 123456 words") == "%words 123456 words%"


def test_seed_stands_down_when_even_one_value_will_not_fit():
    """Past the inline budget the ordinary exact route carries the value alone."""

    subject = builder(operation="equals", value=UNSEEDABLE_TEXT)
    assert _caseless_ascii_ngram_anchor(UNSEEDABLE_TEXT) is not None
    assert subject._public_long_text_candidate_seed_plan() is None
    assert not subject.supports_filter_candidate_seed_page()
    rendered = _rendered_seed_statement(subject)
    assert "matching_scalar_trace_identities" not in rendered
    assert len(rendered.encode()) < _CLICKHOUSE_MAX_QUERY_SIZE_BYTES


@pytest.mark.parametrize(
    "value",
    [
        "a common message 000123 another response. " * 40,
        "escaped \\ and quoted ' text. " * 40,
        "line\nline\n" * 200,
        "tab\tcarriage\rbell\anull\0" * 150,
    ],
    ids=["plain", "backslash-and-quote", "newlines", "control-characters"],
)
def test_inline_budget_counts_what_the_driver_actually_renders(value):
    """The budget guards a parser limit, so it must not understate the literal.

    An escaped-text value is mostly characters the driver doubles. Counting
    only the backslash and the quote understates a tab-heavy value by about a
    third, which would let an oversized statement through the budget and fail
    at the parser instead.
    """

    context = SimpleNamespace(server_info=SimpleNamespace(get_timezone=lambda: "UTC"))
    rendered = escape_params({"v": value}, context)["v"]
    assert _rendered_literal_bytes(value) == len(rendered.encode())


def test_escaped_literal_chars_match_the_driver():
    """If clickhouse-driver's escape table moves, this budget must move with it."""

    assert set(_ESCAPED_LITERAL_CHARS) == set(escape_chars_map)
