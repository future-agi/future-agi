"""Regression tests for ``build_sql_filters``.

Covers two defects in ``model_hub.utils.SQL_queries.build_sql_filters``:

  * Filter operations that matched no branch used to be silently dropped, so
    the caller ran an unfiltered query that looked filtered. They now raise.
  * Text ILIKE operations interpolated the raw value into the pattern, so a
    literal ``%`` or ``_`` acted as a wildcard. They are now escaped and the
    clause carries an explicit ``ESCAPE`` character.

The function is pure (no DB access); these tests exercise the SQL fragment and
parameters it returns.
"""

import unittest

from model_hub.utils.SQL_queries import build_sql_filters


def _text_filter(op, value):
    return [
        {
            "column_id": "name",
            "filter_config": {
                "filter_op": op,
                "filter_value": value,
                "filter_type": "text",
            },
        }
    ]


class BuildSqlFiltersTextTests(unittest.TestCase):
    def test_contains_escapes_wildcards_and_sets_escape_clause(self):
        sql, params = build_sql_filters(
            _text_filter("contains", "100%_off"), {"name": "eu.user_id"}
        )
        self.assertEqual(sql, " AND eu.user_id ILIKE %s ESCAPE '\\'")
        # % and _ are escaped so they match literally, wrapped for "contains".
        self.assertEqual(params, ["%100\\%\\_off%"])

    def test_equals_is_exact_not_wildcard(self):
        sql, params = build_sql_filters(
            _text_filter("equals", "100%"), {"name": "eu.user_id"}
        )
        self.assertEqual(sql, " AND eu.user_id ILIKE %s ESCAPE '\\'")
        self.assertEqual(params, ["100\\%"])

    def test_starts_with_and_ends_with(self):
        _, start_params = build_sql_filters(
            _text_filter("starts_with", "a_b"), {"name": "eu.user_id"}
        )
        self.assertEqual(start_params, ["a\\_b%"])
        _, end_params = build_sql_filters(
            _text_filter("ends_with", "a_b"), {"name": "eu.user_id"}
        )
        self.assertEqual(end_params, ["%a\\_b"])

    def test_in_builds_placeholders(self):
        sql, params = build_sql_filters(
            _text_filter("in", ["a", "b", "c"]), {"name": "eu.user_id"}
        )
        self.assertEqual(sql, " AND eu.user_id IN (%s, %s, %s)")
        self.assertEqual(params, ["a", "b", "c"])


class BuildSqlFiltersUnsupportedOpTests(unittest.TestCase):
    def test_text_op_with_default_number_type_raises(self):
        # A text op sent without filter_type defaults to "number" and used to be
        # silently dropped, returning an unfiltered result set.
        filters = [
            {
                "column_id": "name",
                "filter_config": {"filter_op": "contains", "filter_value": "abc"},
            }
        ]
        with self.assertRaises(ValueError):
            build_sql_filters(filters, {"name": "eu.user_id"})

    def test_unhandled_text_op_raises(self):
        with self.assertRaises(ValueError):
            build_sql_filters(
                _text_filter("is_null", ""), {"name": "eu.user_id"}
            )

    def test_unhandled_number_op_raises(self):
        filters = [
            {
                "column_id": "score",
                "filter_config": {
                    "filter_op": "is_null",
                    "filter_value": 1,
                    "filter_type": "number",
                },
            }
        ]
        with self.assertRaises(ValueError):
            build_sql_filters(filters, {"score": "os.score"})

    def test_unknown_data_type_raises(self):
        filters = [
            {
                "column_id": "name",
                "filter_config": {
                    "filter_op": "equals",
                    "filter_value": "x",
                    "filter_type": "boolean",
                },
            }
        ]
        with self.assertRaises(ValueError):
            build_sql_filters(filters, {"name": "eu.user_id"})

    def test_unknown_column_still_raises(self):
        with self.assertRaises(Exception):
            build_sql_filters(
                _text_filter("contains", "x"), {"other": "eu.user_id"}
            )


class BuildSqlFiltersMiscTests(unittest.TestCase):
    def test_number_equals(self):
        filters = [
            {
                "column_id": "score",
                "filter_config": {
                    "filter_op": "equals",
                    "filter_value": 5,
                    "filter_type": "number",
                },
            }
        ]
        sql, params = build_sql_filters(filters, {"score": "os.score"})
        self.assertEqual(sql, " AND os.score = %s")
        self.assertEqual(params, [5])

    def test_created_at_is_skipped(self):
        filters = [
            {
                "column_id": "created_at",
                "filter_config": {"filter_op": "equals", "filter_value": "x"},
            }
        ]
        self.assertEqual(build_sql_filters(filters, {}), ("", []))

    def test_empty_and_default_args(self):
        self.assertEqual(build_sql_filters(), ("", []))
        self.assertEqual(build_sql_filters([], {}), ("", []))

    def test_mutable_default_not_shared_between_calls(self):
        # Guards against the previous mutable default-argument signature.
        first_sql, first_params = build_sql_filters(
            _text_filter("contains", "a"), {"name": "eu.user_id"}
        )
        second_sql, second_params = build_sql_filters(
            _text_filter("contains", "b"), {"name": "eu.user_id"}
        )
        self.assertEqual(first_params, ["%a%"])
        self.assertEqual(second_params, ["%b%"])
        self.assertEqual(first_sql, second_sql)


if __name__ == "__main__":
    unittest.main()
