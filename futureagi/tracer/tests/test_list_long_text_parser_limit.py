"""The span and voice lists keep long attribute filters inside the parser limit.

ClickHouse refuses a statement longer than ``max_query_size`` (262,144 bytes)
with code 62 before it runs, so a filtered read that renders past it is a
failure, not a slow page. #2797 held the TRACE list's long-text seed under the
limit; the span list has its own seed builder and the voice list its own
delegate, and neither route was measured. On production one span-list IN
filter with two ~100 KiB values rendered 428,335 bytes - each value once in
the exact post-collapse comparison and once more in the population-prefix
subquery - and was refused on every window.

These tests render the statements each route actually issues, exactly as
clickhouse-driver renders them, at the measured value sizes. Each guard is
paired with a negative control that re-admits the defect through the budget
it relies on and checks that the same measurement catches it, so a green run
means the guard can fail, not that it cannot see.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tracer.services.clickhouse.query_builders import latest_filter_predicates
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    _CLICKHOUSE_MAX_QUERY_SIZE_BYTES,
    _rendered_literal_bytes,
    _values_fit_second_inline_budget,
    compile_span_filter_plans,
)
from tracer.services.clickhouse.v2.query_builders import trace_list as trace_list_module
from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    _MAX_NGRAM_ANCHOR_BYTES,
    _caseless_ascii_ngram_anchor,
)
from tracer.services.clickhouse.v2.query_builders.voice_call_list import (
    VoiceCallListQueryBuilderV2,
)
from tracer.tests.test_bounded_trace_filter_reads import _render_driver_sql
from tracer.tests.test_trace_root_physical_replay import complete_root_row

pytestmark = pytest.mark.unit

PROJECT = "11111111-1111-4111-8111-111111111111"
KEY = "raw_log"
END = datetime(2026, 9, 12)
START = END - timedelta(days=365)

# The measured shape: one filter over values of these exact sizes. The text is
# JSON-like prose with quotes, digits and newlines, as the real values are, so
# the driver's escaping and the anchor's letter runs both come into play. The
# two values are built from different wording so that, as on production,
# their bounded anchors are different literals.
_UNIT = (
    '{"turn": 7, "role": "assistant", '
    '"text": "Thanks for calling, how can I help you today?"}\n'
)
_SECOND_UNIT = (
    '{"turn": 8, "role": "customer", '
    '"text": "My order has not arrived and the tracking page shows nothing."}\n'
)


def _text(size: int, unit: str = _UNIT) -> str:
    return (unit * (size // len(unit) + 1))[:size]


FIRST_VALUE = _text(105_762)
SECOND_VALUE = _text(101_994, _SECOND_UNIT)
# Past the index-companion budget, inside the second-inline budget: the
# ordinary long-text regime, whose statements this change must leave alone.
MID_VALUE = _text(48 * 1024)
ORDINARY_VALUE = _text(8 * 1024)


def _time_filter() -> dict:
    return {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [START.isoformat(), END.isoformat()],
        },
    }


def _attribute_filter(operation: str, value, *, typed: bool = False) -> dict:
    config = {
        "col_type": "SPAN_ATTRIBUTE",
        "filter_type": "text",
        "filter_op": operation,
        "filter_value": value,
    }
    if typed:
        # The picker's provenance shape, which the measured IN cases carry.
        config["attribute_value_types"] = ["string"] * len(value)
    return {"column_id": KEY, "filter_config": config}


def _span_builder(operation: str, value, *, typed: bool = False):
    # As views/observation_span.py builds it for a bounded filtered page.
    return SpanListQueryBuilderV2(
        project_id=PROJECT,
        filters=[_time_filter(), _attribute_filter(operation, value, typed=typed)],
        page_number=0,
        page_size=25,
        bounded_internal_scan=True,
    )


def _voice_builder(operation: str, value, *, typed: bool = False):
    # As views/trace.py builds it for a public voice page.
    return VoiceCallListQueryBuilderV2(
        project_id=PROJECT,
        filters=[_time_filter(), _attribute_filter(operation, value, typed=typed)],
        page_number=0,
        page_size=30,
        eval_config_ids=[],
        remove_simulation_calls=False,
        annotation_label_ids=[],
    )


def _span_seed(builder) -> str:
    sql, params = builder.build_filter_seed_page(
        slice_start=END - timedelta(hours=1), slice_end=END, limit=26
    )
    return _render_driver_sql(sql, params)


def _span_classifier(builder) -> str:
    row = {
        "project_id": PROJECT,
        "id": "span-1",
        "trace_id": "trace-1",
        "start_time": END - timedelta(hours=2),
        "observation_type": "SPAN",
        "service_name": "svc",
        "_version": 1,
    }
    sql, params = builder.build_filter_match_query_from_seed_rows([row])
    return _render_driver_sql(sql, params)


def _voice_seed(builder) -> str:
    assert builder.supports_filter_candidate_seed_page()
    sql, params = builder.build_filter_candidate_seed_page(
        slice_start=START, slice_end=END, limit=31
    )
    return _render_driver_sql(sql, params)


def _voice_classifier(builder) -> str:
    row = complete_root_row(
        {
            "trace_id": "trace-1",
            "root_span_id": "root",
            "start_time": END - timedelta(hours=2),
            "_root_observation_type": "conversation",
        },
        project_id=PROJECT,
    )
    assert builder.use_identity_only_filter_classification()
    sql, params = builder.build_filter_identity_match_query_from_seed_rows([row])
    return _render_driver_sql(sql, params)


def _literal(value: str) -> str:
    """The literal a statement carries: the lowercased value, driver-escaped."""

    return _render_driver_sql("%(v)s", {"v": value.lower()})


def _copies(rendered: str, value: str) -> int:
    return rendered.count(_literal(value))


def _fits(rendered: str) -> bool:
    return len(rendered.encode()) < _CLICKHOUSE_MAX_QUERY_SIZE_BYTES


# ---------------------------------------------------------------------------
# Span list
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("typed", [False, True], ids=["plain", "picker"])
def test_span_seed_with_the_measured_in_values_renders_each_once_and_parses(typed):
    """Two ~100 KiB values: one copy apiece, and the statement reaches the parser."""

    rendered = _span_seed(_span_builder("in", [FIRST_VALUE, SECOND_VALUE], typed=typed))
    assert _fits(rendered)
    assert _copies(rendered, FIRST_VALUE) == 1
    assert _copies(rendered, SECOND_VALUE) == 1
    # The one copy is the exact comparison on collapsed latest state - after
    # the FROM source - not the prefix discovery inside it, which keeps the
    # key witness alone.
    collapse_end = rendered.index("AS latest_seed_spans")
    assert rendered.index(_literal(FIRST_VALUE)) > collapse_end
    assert "has(attrs_string.keys, 'raw_log')" in rendered[:collapse_end]


def test_span_seed_guard_sees_the_second_copy_when_it_is_admitted_back(monkeypatch):
    """Negative control: with the budget lifted the measured shape is refused."""

    monkeypatch.setattr(
        latest_filter_predicates, "_MAX_TWICE_INLINED_VALUES_RENDERED_BYTES", 1 << 40
    )
    rendered = _span_seed(_span_builder("in", [FIRST_VALUE, SECOND_VALUE], typed=True))
    assert _copies(rendered, FIRST_VALUE) == 2
    assert _copies(rendered, SECOND_VALUE) == 2
    assert not _fits(rendered)


def test_span_seed_with_one_measured_equals_value_is_unchanged():
    """One ~100 KiB value fits twice; that shape parsed before and still does."""

    rendered = _span_seed(_span_builder("equals", FIRST_VALUE))
    assert _fits(rendered)
    assert _copies(rendered, FIRST_VALUE) == 2


@pytest.mark.parametrize(
    "value", [ORDINARY_VALUE, MID_VALUE], ids=["ordinary", "past-companions"]
)
def test_span_ordinary_long_text_still_prunes_prefixes_by_value_and_classifies(value):
    """The other side: ordinary long text keeps its raw value witness twice."""

    builder = _span_builder("in", [value, value + " tail"], typed=True)
    rendered = _span_seed(builder)
    assert _fits(rendered)
    assert _copies(rendered, value) == 2
    classifier = _span_classifier(builder)
    assert _fits(classifier)
    assert _copies(classifier, value) == 1


def test_span_classifier_carries_the_measured_values_once_and_parses():
    classifier = _span_classifier(
        _span_builder("in", [FIRST_VALUE, SECOND_VALUE], typed=True)
    )
    assert _fits(classifier)
    assert _copies(classifier, FIRST_VALUE) == 1
    assert _copies(classifier, SECOND_VALUE) == 1


def _span_discovery(builder) -> str:
    sql, params = builder.build_filter_population_time_discovery_query(
        slice_start=END - timedelta(days=1), slice_end=END
    )
    return _render_driver_sql(sql, params)


_VALUE_COMPARISON = "lowerUTF8(toString(attrs_string['raw_log'])) IN ("


@pytest.mark.parametrize("typed", [False, True], ids=["plain", "picker"])
@pytest.mark.parametrize(
    "values",
    [[MID_VALUE, MID_VALUE + " tail"], [FIRST_VALUE, SECOND_VALUE]],
    ids=["past-companions", "measured"],
)
def test_span_population_discovery_locates_by_value_once_companions_stand_down(
    typed, values
):
    """Discovery carries no comparison of its own, so past the companion
    budget it inlines the value once and finds the matching hour by it. Key
    presence alone is no discovery for a key most spans carry: every hour is
    a hit, and production walked six hours in twelve statements that way."""

    rendered = _span_discovery(_span_builder("in", values, typed=typed))
    assert _fits(rendered)
    for value in values:
        assert _copies(rendered, value) == 1
    assert _VALUE_COMPARISON in rendered
    assert "indexHint(hasAny(arrayMap" not in rendered
    assert "has(attrs_string.keys, 'raw_log')" in rendered


def test_span_population_discovery_guard_sees_the_bare_key_witness(monkeypatch):
    """Negative control: preferring a companion-less index witness is what the
    measurement must catch."""

    monkeypatch.setattr(
        SpanListQueryBuilderV2,
        "_population_discovery_witness",
        lambda self, plan: (
            plan.raw_index_witness_predicate or plan.raw_witness_predicate
        ),
    )
    rendered = _span_discovery(
        _span_builder("in", [FIRST_VALUE, SECOND_VALUE], typed=True)
    )
    assert _copies(rendered, FIRST_VALUE) == 0
    assert _VALUE_COMPARISON not in rendered


def test_span_population_discovery_keeps_its_companions_for_ordinary_values():
    """Ordinary values still discover through the deployed blooms, reading no
    Map value: the literal rides inside the companion only."""

    # One 8 KiB value: inside the 16 KiB companion budget (two of them would
    # not be, and would take the value-discovery route above instead).
    rendered = _span_discovery(_span_builder("in", [ORDINARY_VALUE], typed=True))
    assert _fits(rendered)
    assert "indexHint(hasAny(arrayMap" in rendered
    assert _copies(rendered, ORDINARY_VALUE) == 1
    assert _VALUE_COMPARISON not in rendered


def test_the_compiler_hands_back_key_presence_only_past_the_budget():
    oversized = compile_span_filter_plans(
        [
            _time_filter(),
            _attribute_filter("in", [FIRST_VALUE, SECOND_VALUE], typed=True),
        ]
    )[0]
    assert oversized.raw_population_witness_predicate is not None
    assert (
        oversized.population_witness_predicate == oversized.raw_index_witness_predicate
    )
    assert "latest_filter_param" not in oversized.population_witness_predicate
    assert "latest_filter_param" in oversized.raw_witness_predicate

    ordinary = compile_span_filter_plans(
        [
            _time_filter(),
            _attribute_filter("in", [MID_VALUE, ORDINARY_VALUE], typed=True),
        ]
    )[0]
    assert ordinary.raw_population_witness_predicate is None
    assert ordinary.population_witness_predicate == ordinary.raw_witness_predicate

    plain = compile_span_filter_plans(
        [_time_filter(), _attribute_filter("equals", FIRST_VALUE + SECOND_VALUE)]
    )[0]
    assert plain.population_witness_predicate == plain.raw_key_witness_predicate


def test_the_second_inline_budget_measures_what_the_driver_renders():
    """A value of tabs doubles when escaped; raw bytes would admit it twice."""

    tabs = "\t" * (60 * 1024)
    assert _rendered_literal_bytes(tabs) == 2 * 60 * 1024 + 2
    assert not _values_fit_second_inline_budget((tabs,))
    assert _values_fit_second_inline_budget(("a" * (100 * 1024),))
    assert _values_fit_second_inline_budget((1.5, True))


# ---------------------------------------------------------------------------
# Voice list
# ---------------------------------------------------------------------------

_VOICE_SHAPES = [
    pytest.param("equals", FIRST_VALUE, False, id="equals"),
    pytest.param("in", [FIRST_VALUE, SECOND_VALUE], True, id="in"),
]


@pytest.mark.parametrize(("operation", "value", "typed"), _VOICE_SHAPES)
def test_voice_seed_with_the_measured_values_renders_each_once_and_parses(
    operation, value, typed
):
    """The voice delegate takes the trace list's long-text lane, bounded."""

    values = value if isinstance(value, list) else [value]
    rendered = _voice_seed(_voice_builder(operation, value, typed=typed))
    assert _fits(rendered)
    for item in values:
        assert _copies(rendered, item) == 1
        anchor = _caseless_ascii_ngram_anchor(item)
        assert anchor is not None
        assert len(anchor.encode()) <= _MAX_NGRAM_ANCHOR_BYTES + 2
        assert rendered.count(_render_driver_sql("%(a)s", {"a": anchor})) == 1
    assert "indexHint(has(arrayMap" not in rendered
    assert "indexHint(hasAny(arrayMap" not in rendered


@pytest.mark.parametrize(("operation", "value", "typed"), _VOICE_SHAPES)
def test_voice_classifier_carries_the_measured_values_once_and_parses(
    operation, value, typed
):
    values = value if isinstance(value, list) else [value]
    rendered = _voice_classifier(_voice_builder(operation, value, typed=typed))
    assert _fits(rendered)
    for item in values:
        assert _copies(rendered, item) == 1


def test_voice_seed_guard_sees_the_unbounded_anchor_when_it_is_admitted_back(
    monkeypatch,
):
    """Negative control: the tip's shape - a whole-value anchor plus the
    companion copy - is what the measurement must catch."""

    monkeypatch.setattr(trace_list_module, "_MAX_NGRAM_ANCHOR_BYTES", 1 << 40)
    monkeypatch.setattr(
        latest_filter_predicates, "_MAX_INDEX_COMPANION_VALUE_UTF8_BYTES", 1 << 40
    )
    rendered = _voice_seed(_voice_builder("equals", FIRST_VALUE))
    assert _copies(rendered, FIRST_VALUE) == 2
    assert not _fits(rendered)


def test_voice_ordinary_long_text_still_seeds_with_its_anchor_and_companion():
    """The other side: ordinary long text keeps every hint it had."""

    rendered = _voice_seed(_voice_builder("equals", ORDINARY_VALUE))
    assert _fits(rendered)
    assert _copies(rendered, ORDINARY_VALUE) == 2
    assert "indexHint(arrayStringConcat" in rendered
    assert "arrayMap(x -> lowerUTF8(x), mapValues(attrs_string))" in rendered
