"""Tests for the ``user_id`` filter path in the ClickHouse filter builder.

Regression coverage for TH-4436: the cross-project user-detail page injects
``userScopeFilter = [{columnId: "user_id", filterValue: <user_id_string>}]``
into the traces view. The frontend sends the ``tracer_enduser.user_id``
string (e.g. ``"9281"`` or ``"user-11771490488.8493178"``), **not** the
UUID primary key. Before the fix the builder treated ``user_id`` as a
span-attribute filter and looked up ``span_attributes.user_id`` — which
OTel instrumentation stores under ``user.id`` (dot), so the filter
either silently returned zero traces or matched the wrong ones. The fix
(filters.py) resolves the string to end-user UUIDs via a subquery on
``tracer_enduser`` and wraps the result in the standard
``trace_id IN (...)`` pattern.
"""

import unittest

from tracer.services.clickhouse.query_builders.filters import (
    ClickHouseFilterBuilder,
)


class UserIdFilterTests(unittest.TestCase):
    def _build(self, table="spans"):
        return ClickHouseFilterBuilder(table=table)

    def _user_id_filter(self, value, col_type=None):
        # The frontend's ``userScopeFilter`` sends col_type=SYSTEM_METRIC — the
        # `user_id` (string) early-return lives at the top of the SYSTEM_METRIC
        # dispatch in `_build_system_metric_condition`.
        return dict(
            col_id="user_id",
            col_type=col_type or ClickHouseFilterBuilder.SYSTEM_METRIC,
            filter_type="text",
            filter_op="equals",
            filter_value=value,
        )

    def test_user_id_single_string_resolves_via_tracer_enduser(self):
        b = self._build()
        sql = b._build_condition(**self._user_id_filter("9281"))
        self.assertIsNotNone(sql, "user_id filter should produce a condition")
        # Wraps in trace_id IN (...) so trace-list/span-list both see matching traces.
        self.assertIn("trace_id IN (", sql)
        # Resolves via tracer_enduser.user_id — not a raw span_attribute match.
        self.assertIn("FROM tracer_enduser", sql)
        # Scalar equals → `user_id = %(p)s`; list/multi-value → `user_id IN %(p)s`.
        self.assertIn("user_id =", sql)
        # Must NOT fall through to the generic span-attribute path,
        # which would JSONExtract(span_attributes, 'user_id') — spans
        # don't store the attribute under that key in OTel convention.
        self.assertNotIn("JSONExtract", sql)
        self.assertNotIn("span_attr", sql)
        # Uses a bound parameter, not a literal, for the user id.
        self.assertNotIn("'9281'", sql)
        self.assertEqual(b._params.get("col_1"), "9281")

    def test_user_id_special_chars(self):
        """Dots / hyphens in the user_id string shouldn't be treated as SQL."""
        b = self._build()
        sql = b._build_condition(
            **self._user_id_filter("user-11771490488.8493178")
        )
        self.assertIsNotNone(sql)
        # Value always passes via bound parameter — never inlined into SQL.
        self.assertNotIn("user-11771490488.8493178", sql)
        self.assertEqual(
            b._params.get("col_1"),
            "user-11771490488.8493178",
        )

    def test_user_id_list_values(self):
        b = self._build()
        sql = b._build_condition(
            col_id="user_id",
            col_type=ClickHouseFilterBuilder.SYSTEM_METRIC,
            filter_type="text",
            filter_op="in",
            filter_value=["9281", "106749"],
        )
        self.assertIsNotNone(sql)
        self.assertIn("user_id IN", sql)
        self.assertEqual(b._params.get("col_1"), ("9281", "106749"))

    def test_user_id_empty_value_returns_none(self):
        b = self._build()
        self.assertIsNone(b._build_condition(**self._user_id_filter(None)))
        self.assertIsNone(b._build_condition(**self._user_id_filter("")))
        self.assertIsNone(
            b._build_condition(
                col_id="user_id",
                col_type=ClickHouseFilterBuilder.SYSTEM_METRIC,
                filter_type="text",
                filter_op="in",
                filter_value=[None, ""],
            )
        )

    def test_user_id_negation_ops(self):
        """``not_equals`` / ``not_in`` flip the outer membership to NOT IN.

        Inner predicate is positivized (``not_equals`` → ``equals``,
        ``not_in`` → ``in``) and the flip happens at the outer ``trace_id``
        layer so multi-user trace semantics stay correct.
        """
        for op in ("not_equals", "not_in", "!=", "is_not"):
            b = self._build()
            sql = b._build_condition(
                col_id="user_id",
                col_type=ClickHouseFilterBuilder.SYSTEM_METRIC,
                filter_type="text",
                filter_op=op,
                filter_value="9281",
            )
            self.assertIsNotNone(sql, f"negation op {op!r} should build a clause")
            self.assertIn(
                "trace_id NOT IN (",
                sql,
                f"op {op!r} should produce `trace_id NOT IN`, got: {sql}",
            )
            # Inner resolve-users predicate is always positive
            # (not_equals/!=/is_not → `user_id =`; not_in → `user_id IN`).
            self.assertTrue(
                ("user_id =" in sql) or ("user_id IN" in sql),
                f"op {op!r} should emit positive inner predicate, got: {sql}",
            )
            self.assertNotIn("user_id !=", sql)
            self.assertNotIn("user_id NOT IN", sql)

    def test_user_id_integer_value_coerced_to_string(self):
        """``filter_value=9281`` (int) must be stringified before binding."""
        b = self._build()
        sql = b._build_condition(
            col_id="user_id",
            col_type=ClickHouseFilterBuilder.SYSTEM_METRIC,
            filter_type="text",
            filter_op="equals",
            filter_value=9281,
        )
        self.assertIsNotNone(sql)
        self.assertEqual(b._params.get("col_1"), "9281")

    def test_user_id_requires_system_metric_col_type(self):
        """The ``user_id`` resolver lives at the top of the SYSTEM_METRIC
        dispatch. FE must tag ``userScopeFilter`` with ``col_type=SYSTEM_METRIC``.
        """
        b = self._build()
        sql = b._build_condition(
            col_id="user_id",
            col_type=ClickHouseFilterBuilder.SYSTEM_METRIC,
            filter_type="text",
            filter_op="equals",
            filter_value="9281",
        )
        self.assertIsNotNone(sql)
        self.assertIn("FROM tracer_enduser", sql)

    def test_user_filter_always_resolves_via_tracer_enduser(self):
        """``col_id == 'user'`` is treated as a string filter against
        ``tracer_enduser.user_id`` regardless of value shape. UUID-shaped
        back-compat that previously routed directly to ``end_user_id`` has
        been removed — every value is matched as a user_id string."""
        b = self._build()
        sql = b._build_condition(
            col_id="user",
            col_type=ClickHouseFilterBuilder.SYSTEM_METRIC,
            filter_type="text",
            filter_op="equals",
            filter_value="08ad78f8-1974-45c1-b6bc-4f2b2ba0b243",
        )
        self.assertIsNotNone(sql)
        self.assertIn("FROM tracer_enduser", sql)
        self.assertIn("user_id =", sql)
        self.assertEqual(
            b._params.get("col_1"), "08ad78f8-1974-45c1-b6bc-4f2b2ba0b243"
        )

    # --- string ops beyond equals/not_equals ---

    def test_user_id_contains(self):
        b = self._build()
        sql = b._build_condition(
            **self._user_id_filter("admin")
            | {"filter_op": "contains"}  # override
        )
        self.assertIsNotNone(sql)
        self.assertIn("trace_id IN (", sql)
        self.assertIn("FROM tracer_enduser", sql)
        # Inner predicate is a LIKE with %val% bound as a parameter.
        self.assertIn("user_id LIKE", sql)
        self.assertEqual(b._params.get("col_1"), "%admin%")

    def test_user_id_not_contains_flips_outer(self):
        b = self._build()
        sql = b._build_condition(
            **self._user_id_filter("admin")
            | {"filter_op": "not_contains"}
        )
        self.assertIsNotNone(sql)
        # Outer flips to NOT IN; inner stays positive (positivized).
        self.assertIn("trace_id NOT IN (", sql)
        self.assertIn("user_id LIKE", sql)
        self.assertNotIn("user_id NOT LIKE", sql)
        self.assertEqual(b._params.get("col_1"), "%admin%")

    def test_user_id_starts_with(self):
        b = self._build()
        sql = b._build_condition(
            **self._user_id_filter("admin")
            | {"filter_op": "starts_with"}
        )
        self.assertIsNotNone(sql)
        self.assertIn("trace_id IN (", sql)
        self.assertIn("user_id LIKE", sql)
        self.assertEqual(b._params.get("col_1"), "admin%")

    def test_user_id_ends_with(self):
        b = self._build()
        sql = b._build_condition(
            **self._user_id_filter("admin")
            | {"filter_op": "ends_with"}
        )
        self.assertIsNotNone(sql)
        self.assertIn("trace_id IN (", sql)
        self.assertIn("user_id LIKE", sql)
        self.assertEqual(b._params.get("col_1"), "%admin")

    def test_user_id_is_null(self):
        """`is_null` short-circuits — no `tracer_enduser` lookup; checks
        spans.end_user_id directly against the zero-UUID sentinel.
        """
        b = self._build()
        sql = b._build_condition(
            col_id="user_id",
            col_type=ClickHouseFilterBuilder.SYSTEM_METRIC,
            filter_type="text",
            filter_op="is_null",
            filter_value=None,
        )
        self.assertIsNotNone(sql)
        self.assertIn("trace_id IN (", sql)
        self.assertNotIn("FROM tracer_enduser", sql)
        self.assertIn("end_user_id =", sql)
        self.assertIn("00000000-0000-0000-0000-000000000000", sql)

    def test_user_id_is_not_null(self):
        """`is_not_null` — same shape as is_null but outer flips to IN."""
        b = self._build()
        sql = b._build_condition(
            col_id="user_id",
            col_type=ClickHouseFilterBuilder.SYSTEM_METRIC,
            filter_type="text",
            filter_op="is_not_null",
            filter_value=None,
        )
        self.assertIsNotNone(sql)
        self.assertIn("trace_id IN (", sql)
        self.assertNotIn("trace_id NOT IN (", sql)
        self.assertNotIn("FROM tracer_enduser", sql)
        self.assertIn("end_user_id !=", sql)


class UserFilterStringResolutionTests(unittest.TestCase):
    """`user` and `user_id_type` resolve against tracer_enduser string
    columns, not against end_user_id UUIDs on spans."""

    def _build(self):
        return ClickHouseFilterBuilder(table="spans")

    def _enduser_filter(self, col_id, value, op="in"):
        return dict(
            col_id=col_id,
            col_type=ClickHouseFilterBuilder.SYSTEM_METRIC,
            filter_type="text",
            filter_op=op,
            filter_value=value,
        )

    def test_user_filter_strings_resolve_via_enduser(self):
        b = self._build()
        sql = b._build_condition(
            **self._enduser_filter("user", ["alice", "bob"])
        )
        self.assertIsNotNone(sql)
        self.assertIn("FROM tracer_enduser", sql)
        self.assertIn("user_id IN", sql)
        self.assertNotIn("end_user_id IN %(", sql)
        self.assertEqual(b._params.get("col_1"), ("alice", "bob"))

    def test_user_id_type_filter_string(self):
        b = self._build()
        sql = b._build_condition(
            **self._enduser_filter("user_id_type", ["email", "phone"])
        )
        self.assertIsNotNone(sql)
        self.assertIn("FROM tracer_enduser", sql)
        self.assertIn("user_id_type IN", sql)
        self.assertEqual(b._params.get("col_1"), ("email", "phone"))

    def test_user_filter_is_not_null_no_uuid_cast(self):
        """`is_not_null` on `user` filter must not emit `end_user_id != ''`."""
        b = self._build()
        sql = b._build_condition(
            **self._enduser_filter("user", None, op="is_not_null")
        )
        self.assertIsNotNone(sql)
        # Uses the NULL-sentinel comparison, not the empty-string comparison.
        self.assertIn("end_user_id !=", sql)
        self.assertIn("toUUID(", sql)
        self.assertNotIn("!= ''", sql)

    def test_session_id_is_not_null_no_uuid_empty_string(self):
        """Direct is_not_null on a Nullable(UUID) column drops the `!= ''` clause."""
        b = self._build()
        sql = b._build_column_condition(
            "session_id", "text", "is_not_null", None
        )
        self.assertEqual(sql, "session_id IS NOT NULL")

    def test_parent_span_id_is_not_null_keeps_empty_string_check(self):
        """String columns retain the `!= ''` defence."""
        b = self._build()
        sql = b._build_column_condition(
            "parent_span_id", "text", "is_not_null", None
        )
        self.assertIn("parent_span_id IS NOT NULL", sql)
        self.assertIn("!= ''", sql)


class IdColumnFilterTests(unittest.TestCase):
    """Direct UUID/ID columns on `spans` (`trace_id`, `span_id`, `session`)
    are now mapped through `SYSTEM_METRIC_MAP` and accept multi-select
    `in` / `not_in` ops. The FE picker restricts the operator set; these
    tests pin the BE SQL output."""

    def _build(self):
        return ClickHouseFilterBuilder(table="spans")

    def _filter(self, col_id, value, op="in"):
        return dict(
            col_id=col_id,
            col_type=ClickHouseFilterBuilder.SYSTEM_METRIC,
            filter_type="text",
            filter_op=op,
            filter_value=value,
        )

    # --- trace_id ---

    def test_trace_id_in_multi_value(self):
        b = self._build()
        sql = b._build_condition(
            **self._filter(
                "trace_id",
                [
                    "0037bb41-c09b-4616-96d2-857ab075afe0",
                    "01810b1a-1677-4a9b-bf08-8d43ce11fde9",
                ],
            )
        )
        self.assertIsNotNone(sql)
        self.assertIn("trace_id IN", sql)
        self.assertEqual(
            b._params.get("col_1"),
            (
                "0037bb41-c09b-4616-96d2-857ab075afe0",
                "01810b1a-1677-4a9b-bf08-8d43ce11fde9",
            ),
        )

    def test_trace_id_not_in_multi_value(self):
        b = self._build()
        sql = b._build_condition(
            **self._filter(
                "trace_id",
                ["0037bb41-c09b-4616-96d2-857ab075afe0"],
                op="not_in",
            )
        )
        self.assertIsNotNone(sql)
        self.assertIn("trace_id NOT IN", sql)

    # --- span_id ---

    def test_span_id_in_uses_id_column(self):
        """`span_id` maps to the `id` column on spans."""
        b = self._build()
        sql = b._build_condition(
            **self._filter("span_id", ["c55aeff2afd24d8c"])
        )
        self.assertIsNotNone(sql)
        self.assertIn("id IN", sql)
        self.assertNotIn("span_id IN", sql)

    # --- session ---

    def test_session_in_maps_to_trace_session_id(self):
        b = self._build()
        sql = b._build_condition(
            **self._filter(
                "session",
                [
                    "003b76f1-2b4a-4af5-b0dc-224d687374d4",
                    "00c2e695-746f-4730-9735-572e487d8c4a",
                ],
            )
        )
        self.assertIsNotNone(sql)
        self.assertIn("trace_session_id IN", sql)
        self.assertEqual(
            b._params.get("col_1"),
            (
                "003b76f1-2b4a-4af5-b0dc-224d687374d4",
                "00c2e695-746f-4730-9735-572e487d8c4a",
            ),
        )

    def test_session_not_in(self):
        b = self._build()
        sql = b._build_condition(
            **self._filter(
                "session",
                ["003b76f1-2b4a-4af5-b0dc-224d687374d4"],
                op="not_in",
            )
        )
        self.assertIsNotNone(sql)
        self.assertIn("trace_session_id NOT IN", sql)

    def test_session_single_value_in(self):
        """Scalar value with op=in still works (wraps to 1-element tuple)."""
        b = self._build()
        sql = b._build_condition(
            **self._filter(
                "session", "003b76f1-2b4a-4af5-b0dc-224d687374d4"
            )
        )
        self.assertIsNotNone(sql)
        self.assertIn("trace_session_id IN", sql)
        self.assertEqual(
            b._params.get("col_1"),
            ("003b76f1-2b4a-4af5-b0dc-224d687374d4",),
        )

    def test_session_id_alias_falls_back_to_span_attr(self):
        """`session_id` is NOT a SYSTEM_METRIC_MAP key — only canonical
        `session` is. An external caller using `session_id` falls through
        to the span_attr_str path (documents the contract)."""
        b = self._build()
        sql = b._build_condition(
            **self._filter("session_id", ["abc"])
        )
        # Falls through to span_attr fallback; emits a Map lookup, not the
        # denormalised trace_session_id column.
        self.assertIsNotNone(sql)
        self.assertIn("span_attr_str", sql)

    # --- is_null / is_not_null on UUID columns ---

    def test_trace_session_id_is_null_uuid_safe(self):
        b = self._build()
        sql = b._build_column_condition(
            "trace_session_id", "text", "is_null", None
        )
        self.assertEqual(sql, "trace_session_id IS NULL")

    def test_end_user_id_is_not_null_uuid_safe(self):
        b = self._build()
        sql = b._build_column_condition(
            "end_user_id", "text", "is_not_null", None
        )
        self.assertEqual(sql, "end_user_id IS NOT NULL")
