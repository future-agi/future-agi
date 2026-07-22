"""
Pin every CH 25.3 (v2) filter-compiler rewrite case.

The v2 filter compiler post-rewrites v1-compiled SQL to swap legacy column
references for the new CH 25.3 schema. These tests pin every rewrite — every
column rename, every JSONExtract* path-access translation, every JSONHas
translation, and the negative cases (substrings inside identifiers must NOT
be rewritten).

If the v1 base ever emits a new pattern the rewriter doesn't anticipate,
the shadow harness will catch it in production — but a test failure here
catches it in CI before any of that.
"""
from __future__ import annotations

import pytest

from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder
from tracer.services.clickhouse.v2.query_builders import columns as cols
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
    rewrite_v1_sql_to_v2,
)


class TestRewriteV1SqlToV2:
    """Direct tests against the pure rewrite function — no DB, no builder."""

    # ─── Simple column renames ───────────────────────────────────────────────
    def test_soft_delete_column_renamed(self):
        assert rewrite_v1_sql_to_v2("WHERE _peerdb_is_deleted = 0") == "WHERE is_deleted = 0"

    def test_version_column_renamed(self):
        assert rewrite_v1_sql_to_v2("ORDER BY _peerdb_version DESC") == "ORDER BY _version DESC"

    def test_span_attr_str_renamed(self):
        assert rewrite_v1_sql_to_v2("span_attr_str['key']") == "attrs_string['key']"

    def test_span_attr_num_renamed(self):
        assert rewrite_v1_sql_to_v2("mapContains(span_attr_num, 'k')") == \
               "mapContains(attrs_number, 'k')"

    def test_span_attr_bool_renamed(self):
        assert rewrite_v1_sql_to_v2("span_attr_bool['streaming'] = 1") == \
               "attrs_bool['streaming'] = 1"

    def test_multiple_column_renames_in_one_string(self):
        v1 = ("SELECT span_attr_str['a'], span_attr_num['b'] "
              "FROM spans WHERE is_deleted = 0")
        v2 = ("SELECT attrs_string['a'], attrs_number['b'] "
              "FROM spans WHERE is_deleted = 0")
        assert rewrite_v1_sql_to_v2(v1) == v2

    # ─── Dictionary-name renames ─────────────────────────────────────────────
    def test_legacy_enduser_dict_renamed(self):
        # The legacy CDC dict (source `tracer_enduser`) → the v2 CH-native dict.
        out = rewrite_v1_sql_to_v2(
            "dictGetOrDefault('enduser_dict', 'user_id', any(end_user_id), '')"
        )
        assert "end_users_dict" in out
        assert "'enduser_dict'" not in out

    def test_enduser_dict_and_soft_delete_renamed_together(self):
        v1 = (
            "SELECT dictGetOrDefault('enduser_dict', 'user_id', any(end_user_id), '') "
            "FROM spans WHERE _peerdb_is_deleted = 0"
        )
        out = rewrite_v1_sql_to_v2(v1)
        assert "end_users_dict" in out and "'enduser_dict'" not in out
        assert "is_deleted = 0" in out and "_peerdb_is_deleted" not in out

    # ─── Negative: don't rewrite substrings of unrelated identifiers ─────────
    def test_does_not_rewrite_substring_of_another_identifier(self):
        # `is_deleted_extra` should NOT be rewritten — only the
        # exact word `is_deleted` is the column we target.
        assert "is_deleted_extra" in \
               rewrite_v1_sql_to_v2("SELECT is_deleted_extra FROM x")

    def test_does_not_rewrite_quoted_string_literal_token(self):
        # If a v1 query contained the LITERAL STRING "span_attr_str" inside
        # quotes (e.g. a value), we WOULD still rewrite it because it's a
        # whole word — this is a known limitation, documented in the docstring.
        # Test the limitation IS visible so anyone reading the code knows.
        v1 = "WHERE description = 'span_attr_str related'"
        v2 = rewrite_v1_sql_to_v2(v1)
        # The literal value IS rewritten (limitation). If this becomes a real
        # issue, switch to a SQL-tokenizing rewriter.
        assert "attrs_string" in v2

    # ─── JSON path access rewrites ───────────────────────────────────────────
    def test_jsonextractstring_attributes_extra(self):
        v1 = "JSONExtractString(span_attributes_raw, 'gen_ai.request.model')"
        v2 = "attributes_extra.gen_ai.request.model.:String"
        assert rewrite_v1_sql_to_v2(v1) == v2

    def test_jsonextractfloat_attributes_extra(self):
        v1 = "JSONExtractFloat(span_attributes_raw, 'gen_ai.request.temperature')"
        v2 = "attributes_extra.gen_ai.request.temperature.:Float64"
        assert rewrite_v1_sql_to_v2(v1) == v2

    def test_jsonextractint_attributes_extra(self):
        v1 = "JSONExtractInt(span_attributes_raw, 'gen_ai.request.max_tokens')"
        v2 = "attributes_extra.gen_ai.request.max_tokens.:Int64"
        assert rewrite_v1_sql_to_v2(v1) == v2

    def test_jsonextractbool_attributes_extra(self):
        v1 = "JSONExtractBool(span_attributes_raw, 'streaming')"
        v2 = "attributes_extra.streaming.:Bool"
        assert rewrite_v1_sql_to_v2(v1) == v2

    def test_jsonextract_resource_attrs(self):
        v1 = "JSONExtractString(resource_attributes_raw, 'service.name')"
        v2 = "resource_attrs.service.name.:String"
        assert rewrite_v1_sql_to_v2(v1) == v2

    def test_jsonextract_metadata(self):
        v1 = "JSONExtractString(metadata_map, 'env')"
        v2 = "metadata.env.:String"
        assert rewrite_v1_sql_to_v2(v1) == v2

    def test_jsonhas_attributes_extra(self):
        v1 = "JSONHas(span_attributes_raw, 'streaming')"
        v2 = "(attributes_extra.streaming.:String IS NOT NULL)"
        assert rewrite_v1_sql_to_v2(v1) == v2

    def test_jsonhas_resource_attrs(self):
        v1 = "JSONHas(resource_attributes_raw, 'host.name')"
        v2 = "(resource_attrs.host.name.:String IS NOT NULL)"
        assert rewrite_v1_sql_to_v2(v1) == v2

    def test_jsonextract_with_whitespace_variations(self):
        # The base v1 sometimes emits extra whitespace around args.
        v1 = "JSONExtractString( span_attributes_raw , 'model' )"
        v2 = "attributes_extra.model.:String"
        assert rewrite_v1_sql_to_v2(v1) == v2

    # ─── Compound expression — JSON access AND column rename together ────────
    def test_compound_rewrite_jsonhas_inside_full_clause(self):
        v1 = (
            "WHERE _peerdb_is_deleted = 0 "
            "AND JSONHas(span_attributes_raw, 'streaming') "
            "AND span_attr_str['model'] = 'gpt-4o'"
        )
        v2 = rewrite_v1_sql_to_v2(v1)
        assert "is_deleted = 0" in v2
        assert "_peerdb_is_deleted" not in v2
        assert "(attributes_extra.streaming.:String IS NOT NULL)" in v2
        assert "attrs_string['model'] = 'gpt-4o'" in v2
        assert "span_attr_str" not in v2

    # ─── Order matters: JSON access rewritten BEFORE column rename ───────────
    def test_order_jsonextract_before_naked_column_rename(self):
        # If naked rename ran first, "span_attributes_raw" inside JSONExtract
        # would NOT be rewritten (because span_attributes_raw isn't in our
        # rename map), but the outer JSONExtract pattern wouldn't match either
        # after JSON access rewrite. Just verify the end-to-end is correct.
        v1 = "JSONExtractString(span_attributes_raw, 'a') AS x, span_attr_num['b'] AS y"
        v2 = rewrite_v1_sql_to_v2(v1)
        assert "attributes_extra.a.:String" in v2
        assert "attrs_number['b']" in v2

    # ─── Bare SELECT-list ref — the one NON-idempotent rewrite ───────────────
    def test_bare_select_list_ref_wrapped_with_alias(self):
        v1 = "SELECT span_attributes_raw FROM spans"
        v2 = "SELECT toJSONString(attributes_extra) AS span_attributes_raw FROM spans"
        assert rewrite_v1_sql_to_v2(v1) == v2

    def test_bare_select_list_rewrite_is_not_idempotent(self):
        # Re-running re-wraps the alias. This is WHY the v2 filter builder may
        # only double-rewrite WHERE/ORDER fragments (which never carry a bare
        # SELECT-list ref), never full statements — see _rewrite.py docstring
        # and ClickHouseFilterBuilderV2.translate.
        once = rewrite_v1_sql_to_v2("SELECT span_attributes_raw FROM spans")
        twice = rewrite_v1_sql_to_v2(once)
        assert once != twice
        assert "AS toJSONString(attributes_extra) AS span_attributes_raw" in twice


class TestClickHouseFilterBuilderV2:
    """End-to-end tests via the subclass — proves the rewrite happens at the
    public surface (translate, translate_sort) and parameters are unchanged.
    """

    def test_subclass_is_drop_in_for_v1(self):
        # No filters → empty WHERE — works in both v1 and v2 trivially.
        builder = ClickHouseFilterBuilderV2(table="spans")
        sql, params = builder.translate(filters=[])
        # Whatever v1 produces for an empty filter set, v2 should produce the
        # same thing modulo column rewrites (there are none here — no filters).
        assert sql == ""
        assert params == {}

    def test_span_attr_type_meta_uses_v2_columns(self):
        b = ClickHouseFilterBuilderV2(table="spans")
        # The instance-level constant we override
        meta = b.SPAN_ATTR_TYPE_META
        assert meta["text"][0]    == cols.ATTRS_STRING
        assert meta["number"][0]  == cols.ATTRS_NUMBER
        assert meta["boolean"][0] == cols.ATTRS_BOOL


class TestEndUserDimensionSource:
    """Pin the v1→v2 end-user dimension swap on the user/user_id filter path.

    A `user` / `user_id` / `user_id_type` filter compiles a
    `trace_id IN (SELECT ... FROM <enduser dim>)` subquery. v1 reads the
    dropped legacy CDC table `tracer_enduser`; v2 must read the `end_users`
    RMT. Distinct from the `enduser_dict` rename covered above.
    """

    @staticmethod
    def _filter(col_id: str, filter_op: str, filter_value=None) -> list[dict]:
        config: dict = {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "text",
            "filter_op": filter_op,
        }
        if filter_value is not None:
            config["filter_value"] = filter_value
        return [{"column_id": col_id, "filter_config": config}]

    # ─── Value path: v1 legacy table vs v2 RMT ───────────────────────────────
    def test_v1_in_targets_legacy_tracer_enduser(self):
        sql, _ = ClickHouseFilterBuilder(table="spans").translate(
            self._filter("user", "in", ["bob", "carol"])
        )
        assert "trace_id IN (" in sql
        assert "SELECT id FROM tracer_enduser FINAL" in sql
        assert "_peerdb_is_deleted = 0 AND deleted = 0" in sql

    def test_v2_in_targets_end_users_rmt(self):
        sql, _ = ClickHouseFilterBuilderV2(table="spans").translate(
            self._filter("user", "in", ["bob", "carol"])
        )
        assert "trace_id IN (" in sql
        assert "SELECT end_user_id FROM end_users FINAL" in sql
        assert "is_deleted = 0" in sql
        # The dropped legacy table must NOT appear on the v2 path.
        assert "tracer_enduser" not in sql
        assert "_peerdb_is_deleted" not in sql

    def test_v2_user_id_type_column_also_targets_end_users(self):
        # A second enduser column id must route through the same v2 dim swap.
        sql, _ = ClickHouseFilterBuilderV2(table="spans").translate(
            self._filter("user_id_type", "equals", "external")
        )
        assert "FROM end_users FINAL" in sql
        assert "tracer_enduser" not in sql

    def test_v2_negation_emits_not_in_against_end_users(self):
        # not_in inverts the outer membership; the dim source is still end_users.
        sql, _ = ClickHouseFilterBuilderV2(table="spans").translate(
            self._filter("user", "not_in", ["bob"])
        )
        assert "trace_id NOT IN (" in sql
        assert "FROM end_users FINAL" in sql
        assert "tracer_enduser" not in sql

    # ─── No-value path (is_null) — a different branch that skips the dim ──────
    def test_v1_is_null_uses_legacy_soft_delete(self):
        # is_null never queries the dim table; it checks end_user_id directly,
        # gated by the legacy soft-delete column on v1.
        sql, _ = ClickHouseFilterBuilder(table="spans").translate(
            self._filter("user", "is_null")
        )
        assert "tracer_enduser" not in sql
        assert "_peerdb_is_deleted = 0" in sql

    def test_v2_is_null_rewrites_soft_delete(self):
        sql, _ = ClickHouseFilterBuilderV2(table="spans").translate(
            self._filter("user", "is_null")
        )
        assert "tracer_enduser" not in sql
        assert "is_deleted = 0" in sql
        assert "_peerdb_is_deleted" not in sql


class TestNumericIsNullNoEmptyStringCast:
    """is_null / is_not_null on numeric SYSTEM_METRIC columns must emit `IS NULL`,
    never `= ''` — comparing a non-nullable Float64/Int32 (cost/tokens/latency) to
    '' raises a ClickHouse cast error. Text columns keep the empty-string fallback.
    """

    @staticmethod
    def _filter(col_id: str, filter_op: str, filter_type: str) -> list[dict]:
        return [
            {
                "column_id": col_id,
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": filter_type,
                    "filter_op": filter_op,
                },
            }
        ]

    def _span_sql(self, col_id: str, filter_op: str, filter_type: str) -> str:
        b = ClickHouseFilterBuilderV2(
            table="spans", query_mode=ClickHouseFilterBuilderV2.QUERY_MODE_SPAN
        )
        sql, _ = b.translate(self._filter(col_id, filter_op, filter_type))
        return sql

    def test_numeric_is_null_uses_is_null_not_empty_string(self):
        sql = self._span_sql("cost", "is_null", "number")
        assert "IS NULL" in sql
        assert "= ''" not in sql
        assert "= 0" not in sql  # 0 is a legitimate value, not "null"

    def test_numeric_is_not_null_uses_is_not_null_not_empty_string(self):
        sql = self._span_sql("cost", "is_not_null", "number")
        assert "IS NOT NULL" in sql
        assert "!= ''" not in sql

    def test_text_is_null_keeps_empty_string_fallback(self):
        sql = self._span_sql("model", "is_null", "text")
        assert "IS NULL" in sql
        assert "= ''" in sql


class TestFilterBuilderWiring:
    """The in-scope v2 list builders must construct the v2 filter compiler so
    the user filter reads `end_users`; the v1 builders keep the v1 compiler.
    Asserts both the binding (class attribute) and the end-to-end emission
    through ``build()`` — the latter catches an inherited build method that
    constructs the v1 compiler directly instead of via ``_FILTER_BUILDER_CLS``.
    """

    USER_FILTER = [
        {
            "column_id": "user",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "text",
                "filter_op": "in",
                "filter_value": ["bob", "carol"],
            },
        }
    ]

    def test_span_list_binds_filter_builder_by_generation(self):
        from tracer.services.clickhouse.query_builders.span_list import (
            SpanListQueryBuilder,
        )
        from tracer.services.clickhouse.v2.query_builders.span_list import (
            SpanListQueryBuilderV2,
        )

        assert SpanListQueryBuilder._FILTER_BUILDER_CLS is ClickHouseFilterBuilder
        assert SpanListQueryBuilderV2._FILTER_BUILDER_CLS is ClickHouseFilterBuilderV2

    def test_trace_list_binds_filter_builder_by_generation(self):
        from tracer.services.clickhouse.query_builders.trace_list import (
            TraceListQueryBuilder,
        )
        from tracer.services.clickhouse.v2.query_builders.trace_list import (
            TraceListQueryBuilderV2,
        )

        assert TraceListQueryBuilder._FILTER_BUILDER_CLS is ClickHouseFilterBuilder
        assert TraceListQueryBuilderV2._FILTER_BUILDER_CLS is ClickHouseFilterBuilderV2

    def test_span_list_v1_build_emits_legacy_table(self):
        from tracer.services.clickhouse.query_builders.span_list import (
            SpanListQueryBuilder,
        )

        sql, _ = SpanListQueryBuilder(project_id="p1", filters=self.USER_FILTER).build()
        assert "tracer_enduser" in sql
        assert "end_users" not in sql

    def test_span_list_v2_build_emits_end_users(self):
        from tracer.services.clickhouse.v2.query_builders.span_list import (
            SpanListQueryBuilderV2,
        )

        sql, _ = SpanListQueryBuilderV2(project_id="p1", filters=self.USER_FILTER).build()
        assert "end_users" in sql
        assert "tracer_enduser" not in sql

    def test_trace_list_v2_build_emits_end_users(self):
        from tracer.services.clickhouse.v2.query_builders.trace_list import (
            TraceListQueryBuilderV2,
        )

        sql, _ = TraceListQueryBuilderV2(project_id="p1", filters=self.USER_FILTER).build()
        assert "end_users" in sql
        assert "tracer_enduser" not in sql
