"""Tests for the ``user_id`` filter path in the ClickHouse filter builder.

Regression coverage for TH-4436: the cross-project user-detail page injects
``userScopeFilter = [{column_id: "user_id", filter_value: <user_id_string>}]``
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
        # Default to NORMAL because that's what the frontend's user scope
        # filter sends when no explicit ``col_type`` is present.
        return dict(
            col_id="user_id",
            col_type=col_type or ClickHouseFilterBuilder.NORMAL,
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
            col_type=ClickHouseFilterBuilder.NORMAL,
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
                col_type=ClickHouseFilterBuilder.NORMAL,
                filter_type="text",
                filter_op="in",
                filter_value=[None, ""],
            )
        )

    def test_user_id_negation_ops(self):
        """``not_equals`` / ``not_in`` flip the outer membership to NOT IN."""
        for op in ("not_equals", "not_in"):
            b = self._build()
            sql = b._build_condition(
                col_id="user_id",
                col_type=ClickHouseFilterBuilder.NORMAL,
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
            # Inner resolve-users predicate is positive; we flip at the outer layer.
            self.assertTrue("user_id =" in sql or "user_id IN" in sql)
            self.assertNotIn("user_id !=", sql)
            self.assertNotIn("user_id NOT IN", sql)

    def test_user_id_integer_value_coerced_to_string(self):
        """``filter_value=9281`` (int) must be stringified before binding."""
        b = self._build()
        sql = b._build_condition(
            col_id="user_id",
            col_type=ClickHouseFilterBuilder.NORMAL,
            filter_type="text",
            filter_op="equals",
            filter_value=9281,
        )
        self.assertIsNotNone(sql)
        self.assertEqual(b._params.get("col_1"), "9281")

    def test_user_id_fires_regardless_of_col_type(self):
        """Fix must work for both NORMAL (frontend default) and SYSTEM_METRIC.

        The ``userScopeFilter`` on the cross-project user detail page omits
        ``col_type`` so it arrives as NORMAL. Earlier versions of this fix
        guarded on ``col_type == SYSTEM_METRIC`` which meant the branch
        never fired in practice; this test locks in the fix.
        """
        for col_type_val in (
            ClickHouseFilterBuilder.NORMAL,
            ClickHouseFilterBuilder.SYSTEM_METRIC,
            "",  # explicitly empty string — still fires
        ):
            b = self._build()
            sql = b._build_condition(
                col_id="user_id",
                col_type=col_type_val,
                filter_type="text",
                filter_op="equals",
                filter_value="9281",
            )
            self.assertIsNotNone(
                sql, f"user_id filter must fire for col_type={col_type_val!r}"
            )
            self.assertIn("FROM tracer_enduser", sql)

    def test_user_id_does_not_affect_plain_user_filter(self):
        """``col_id == 'user'`` (UUID-valued) must still go to TRACE_END_USER."""
        b = self._build()
        sql = b._build_condition(
            col_id="user",
            col_type=ClickHouseFilterBuilder.SYSTEM_METRIC,
            filter_type="text",
            filter_op="equals",
            filter_value="08ad78f8-1974-45c1-b6bc-4f2b2ba0b243",
        )
        self.assertIsNotNone(sql)
        # The classic TRACE_END_USER path wraps on end_user_id directly — no
        # tracer_enduser subquery required because the caller already has
        # the UUID.
        self.assertIn("end_user_id IN", sql)
        self.assertNotIn("FROM tracer_enduser", sql)

    def test_user_id_contains(self):
        b = self._build()
        sql = b._build_condition(
            col_id="user_id",
            col_type=ClickHouseFilterBuilder.SYSTEM_METRIC,
            filter_type="text",
            filter_op="contains",
            filter_value="admin",
        )
        self.assertIsNotNone(sql)
        self.assertIn("trace_id IN (", sql)
        self.assertIn("FROM tracer_enduser", sql)
        self.assertIn("user_id LIKE", sql)
        self.assertEqual(b._params.get("col_1"), "%admin%")

    def test_user_id_not_contains_flips_outer(self):
        b = self._build()
        sql = b._build_condition(
            col_id="user_id",
            col_type=ClickHouseFilterBuilder.SYSTEM_METRIC,
            filter_type="text",
            filter_op="not_contains",
            filter_value="admin",
        )
        self.assertIsNotNone(sql)
        self.assertIn("trace_id NOT IN (", sql)
        self.assertIn("user_id LIKE", sql)
        self.assertNotIn("user_id NOT LIKE", sql)
        self.assertEqual(b._params.get("col_1"), "%admin%")

    def test_user_id_null_ops_do_not_query_tracer_enduser(self):
        for op, outer in (("is_null", "trace_id NOT IN ("), ("is_not_null", "trace_id IN (")):
            b = self._build()
            sql = b._build_condition(
                col_id="user_id",
                col_type=ClickHouseFilterBuilder.SYSTEM_METRIC,
                filter_type="text",
                filter_op=op,
                filter_value=None,
            )
            self.assertIsNotNone(sql)
            self.assertIn(outer, sql)
            self.assertNotIn("FROM tracer_enduser", sql)
            self.assertIn("end_user_id !=", sql)
            self.assertIn("00000000-0000-0000-0000-000000000000", sql)


class EndUserAndIdColumnFilterTests(unittest.TestCase):
    def _build(self):
        return ClickHouseFilterBuilder(table="spans")

    def test_user_id_type_filter_resolves_via_enduser_table(self):
        b = self._build()
        sql = b._build_condition(
            col_id="user_id_type",
            col_type=ClickHouseFilterBuilder.SYSTEM_METRIC,
            filter_type="text",
            filter_op="in",
            filter_value=["email", "phone"],
        )
        self.assertIsNotNone(sql)
        self.assertIn("FROM tracer_enduser", sql)
        self.assertIn("user_id_type IN", sql)
        self.assertEqual(b._params.get("col_1"), ("email", "phone"))

    def test_trace_id_in_multi_value(self):
        b = self._build()
        sql = b._build_condition(
            col_id="trace_id",
            col_type=ClickHouseFilterBuilder.SYSTEM_METRIC,
            filter_type="text",
            filter_op="in",
            filter_value=[
                "0037bb41-c09b-4616-96d2-857ab075afe0",
                "01810b1a-1677-4a9b-bf08-8d43ce11fde9",
            ],
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

    def test_span_id_in_uses_id_column(self):
        b = self._build()
        sql = b._build_condition(
            col_id="span_id",
            col_type=ClickHouseFilterBuilder.SYSTEM_METRIC,
            filter_type="text",
            filter_op="in",
            filter_value=["c55aeff2afd24d8c"],
        )
        self.assertIsNotNone(sql)
        self.assertIn("id IN", sql)
        self.assertNotIn("span_id IN", sql)

    def test_session_aliases_map_to_trace_session_id(self):
        for col_id in ("session", "session_id", "trace_session_id"):
            b = self._build()
            sql = b._build_condition(
                col_id=col_id,
                col_type=ClickHouseFilterBuilder.SYSTEM_METRIC,
                filter_type="text",
                filter_op="in",
                filter_value=["003b76f1-2b4a-4af5-b0dc-224d687374d4"],
            )
            self.assertIsNotNone(sql)
            self.assertIn("toString(trace_session_id) IN", sql)

    def test_nullable_uuid_null_checks_do_not_compare_to_empty_string(self):
        b = self._build()
        self.assertEqual(
            b._build_column_condition("trace_session_id", "text", "is_null", None),
            "trace_session_id IS NULL",
        )
        self.assertEqual(
            b._build_column_condition("end_user_id", "text", "is_not_null", None),
            "end_user_id IS NOT NULL",
        )
