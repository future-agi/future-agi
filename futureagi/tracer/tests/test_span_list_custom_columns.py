"""Custom columns on the spans-observe grid = typed attr maps ∪ attributes_extra.

Regression: `build_content_query` fetched only `attributes_extra`, so custom
columns backed by the typed maps (string/number/bool) rendered as "-". These are
pure unit tests — no DB / no ClickHouse.
"""
from __future__ import annotations

import json

from tracer.services.clickhouse.query_builders.span_list import SpanListQueryBuilder
from tracer.services.clickhouse.query_builders.trace_list import TraceListQueryBuilder
from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.span_selectors import (
    flatten_span_attributes_into_entry,
)

PROJECT_ID = "11111111-1111-1111-1111-111111111111"


def _flatten(**row):
    entry: dict = {}
    flatten_span_attributes_into_entry(entry, row)
    return entry


# --------------------------------------------------------------------------- #
# Flatten helper — the merge that feeds custom columns
# --------------------------------------------------------------------------- #
class TestFlattenSpanAttributes:
    def test_typed_maps_surface_as_top_level_keys(self):
        entry = _flatten(
            attrs_string={"test_string": "hi"},
            attrs_number={"test_number": 42},
            attrs_bool={"test_bool": 1},
            attributes_extra="{}",
        )
        assert entry["test_string"] == "hi"
        assert entry["test_number"] == 42
        assert entry["test_bool"] is True  # UInt8 map coerced to real bool

    def test_attributes_extra_still_surfaces(self):
        entry = _flatten(attributes_extra=json.dumps({"from_extra": "v"}))
        assert entry["from_extra"] == "v"

    def test_maps_and_extra_both_present(self):
        entry = _flatten(
            attrs_string={"s": "map"},
            attributes_extra=json.dumps({"e": "extra"}),
        )
        assert entry["s"] == "map" and entry["e"] == "extra"

    def test_extra_overrides_map_on_collision(self):
        entry = _flatten(
            attrs_string={"dupe": "from_map"},
            attributes_extra=json.dumps({"dupe": "from_extra"}),
        )
        assert entry["dupe"] == "from_extra"

    def test_standard_columns_not_clobbered(self):
        entry = {"name": "keep"}
        flatten_span_attributes_into_entry(
            entry, {"attrs_string": {"name": "attr"}, "attributes_extra": "{}"}
        )
        assert entry["name"] == "keep"

    def test_skip_prefixes_are_hidden(self):
        entry = _flatten(
            attrs_string={"input.value": "x", "output.value": "y", "keep": "z"},
            attributes_extra=json.dumps({"raw.thing": 1, "llm.input_messages": []}),
        )
        assert "input.value" not in entry
        assert "output.value" not in entry
        assert "raw.thing" not in entry
        assert "llm.input_messages" not in entry
        assert entry["keep"] == "z"

    def test_long_string_truncated(self):
        entry = _flatten(attrs_string={"big": "a" * 600})
        assert entry["big"].endswith("...") and len(entry["big"]) == 503

    def test_missing_maps_falls_back_to_extra_only(self):
        # v1 rows before the maps were added / spans with no typed attrs
        entry = _flatten(attributes_extra=json.dumps({"only": "extra"}))
        assert entry == {"only": "extra"}

    def test_bad_attributes_extra_json_is_ignored(self):
        entry = _flatten(attrs_string={"s": "v"}, attributes_extra="{not json")
        assert entry == {"s": "v"}


# --------------------------------------------------------------------------- #
# Content query — must SELECT the typed maps in both schemas
# --------------------------------------------------------------------------- #
_MAP_ALIASES = ("attrs_string", "attrs_number", "attrs_bool")


def _v1_content_sql():
    b = SpanListQueryBuilder(
        project_id=PROJECT_ID, page_number=0, page_size=10,
        filters=[], sort_params=[], eval_config_ids=[], annotation_label_ids=[],
    )
    return b.build_content_query(span_ids=["sp1"])[0]


def _v2_content_sql():
    b = SpanListQueryBuilderV2(
        project_id=PROJECT_ID, page_number=0, page_size=10,
        filters=[], sort_params=[], eval_config_ids=[], annotation_label_ids=[],
    )
    return b.build_content_query(span_ids=["sp1"])[0]


class TestContentQuerySelectsTypedMaps:
    def test_v1_selects_legacy_maps_aliased(self):
        sql = _v1_content_sql()
        assert "span_attr_str AS attrs_string" in sql
        assert "span_attr_num AS attrs_number" in sql
        assert "span_attr_bool AS attrs_bool" in sql

    def test_v2_exposes_map_aliases(self):
        sql = _v2_content_sql()
        for alias in _MAP_ALIASES:
            assert alias in sql

    def test_v2_has_no_legacy_column_leak(self):
        # rewrite renames span_attr_* -> attrs_*; no legacy token should survive
        sql = _v2_content_sql()
        for legacy in ("span_attr_str", "span_attr_num", "span_attr_bool"):
            assert legacy not in sql


# --------------------------------------------------------------------------- #
# Trace ("group by trace") content query — root-span attrs feed custom columns
# --------------------------------------------------------------------------- #
def _v1_trace_content_sql():
    b = TraceListQueryBuilder(
        project_id=PROJECT_ID, page_number=0, page_size=10,
        filters=[], sort_params=[], eval_config_ids=[], annotation_label_ids=[],
    )
    return b.build_content_query(trace_ids=["t1"])[0]


def _v2_trace_content_sql():
    b = TraceListQueryBuilderV2(
        project_id=PROJECT_ID, page_number=0, page_size=10,
        filters=[], sort_params=[], eval_config_ids=[], annotation_label_ids=[],
    )
    return b.build_content_query(trace_ids=["t1"])[0]


class TestTraceContentQuerySelectsAttrs:
    def test_v1_selects_all_typed_maps_and_extra(self):
        sql = _v1_trace_content_sql()
        for col in ("attrs_string", "attrs_number", "attrs_bool", "attributes_extra"):
            assert col in sql

    def test_v2_selects_all_typed_maps_and_extra(self):
        sql = _v2_trace_content_sql()
        for col in ("attrs_string", "attrs_number", "attrs_bool", "attributes_extra"):
            assert col in sql
