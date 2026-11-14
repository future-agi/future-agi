"""Rendered-SQL contract for ``end_user_id`` equality and membership.

``toString(end_user_id) IN (...)`` hides the column behind a function, so
neither the ``idx_end_user_id`` bloom filter nor the primary key can prune and
the statement reads every span of the project. ``equals``/``in`` are therefore
compiled against the bare column and ``toUUID`` literals. Every other op keeps
the cast: a bloom filter cannot serve a negation, and substring matching needs
the text form.

The negations still bind a valid UUID literal in the same canonical spelling,
so the positive and negative forms of one value stay complementary: no row can
satisfy both ``equals`` and ``not_equals`` on it.
"""

from __future__ import annotations

import pytest

from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder

pytestmark = pytest.mark.unit

USER_A = "2a2b2c2d-eeee-4fff-8aaa-bbbbccccdddd"
USER_B = "4a4b4c4d-cccc-4ddd-8eee-ffffaaaabbbb"
NOT_A_UUID = "not-a-uuid"


def _condition(column: str, filter_op: str, filter_value: object):
    builder = ClickHouseFilterBuilder()
    sql = builder._build_column_condition(column, "text", filter_op, filter_value)
    return sql, dict(builder._params)


def test_equals_renders_bare_column_against_a_uuid_literal() -> None:
    sql, params = _condition("end_user_id", "equals", USER_A)

    assert sql == "end_user_id IN (toUUID(%(col_1)s))"
    assert "toString(end_user_id)" not in sql
    assert params == {"col_1": USER_A}


def test_in_renders_one_uuid_literal_per_member() -> None:
    sql, params = _condition("end_user_id", "in", [USER_A, USER_B])

    assert sql == "end_user_id IN (toUUID(%(col_1)s), toUUID(%(col_2)s))"
    assert "toString(end_user_id)" not in sql
    assert params == {"col_1": USER_A, "col_2": USER_B}


def test_uppercase_literal_is_bound_in_canonical_form() -> None:
    """ClickHouse renders a UUID lower case, so the cast never matched these."""

    sql, params = _condition("end_user_id", "equals", USER_A.upper())

    assert sql == "end_user_id IN (toUUID(%(col_1)s))"
    assert params == {"col_1": USER_A}


def test_non_uuid_literal_folds_to_no_rows() -> None:
    """A value that is not a UUID can never equal one under these two ops."""

    assert _condition("end_user_id", "equals", NOT_A_UUID) == ("0 = 1", {})
    assert _condition("end_user_id", "in", [NOT_A_UUID]) == ("0 = 1", {})
    assert _condition("end_user_id", "in", []) == ("0 = 1", {})


def test_in_set_drops_only_the_members_that_are_not_uuids() -> None:
    sql, params = _condition("end_user_id", "in", [USER_A, NOT_A_UUID, USER_B])

    assert sql == "end_user_id IN (toUUID(%(col_1)s), toUUID(%(col_2)s))"
    assert params == {"col_1": USER_A, "col_2": USER_B}


@pytest.mark.parametrize(
    "literal",
    [
        f"{{{USER_A}}}",
        f"urn:uuid:{USER_A}",
        USER_A.replace("-", ""),
        f" {USER_A} ",
    ],
)
def test_non_canonical_spellings_keep_matching_nothing(literal: str) -> None:
    """These parse as UUIDs in Python but never equalled ``toString(uuid)``.

    Accepting them would return rows the cast did not, which is a widening
    this change deliberately does not make. Case is the one exception.
    """

    assert _condition("end_user_id", "equals", literal) == ("0 = 1", {})


@pytest.mark.parametrize(
    ("filter_op", "filter_value", "expected"),
    [
        ("not_equals", USER_A, "toString(end_user_id) != %(col_1)s"),
        ("not_in", [USER_A], "toString(end_user_id) NOT IN %(col_1)s"),
    ],
)
def test_negations_keep_the_text_cast(
    filter_op: str, filter_value: object, expected: str
) -> None:
    """A bloom filter cannot serve a negation, so unwrapping buys no pruning.

    It would also have to invert the non-UUID fold and re-argue NULL handling
    on a ``Nullable(UUID)`` column, so the cast and the NULL semantics stay.
    Only the spelling of a valid literal is normalised, asserted below.
    """

    sql, _ = _condition("end_user_id", filter_op, filter_value)

    assert sql == expected


@pytest.mark.parametrize(
    "filter_op", ["contains", "not_contains", "starts_with", "ends_with"]
)
def test_substring_ops_keep_the_text_cast(filter_op: str) -> None:
    sql, _ = _condition("end_user_id", filter_op, "2a2b")

    assert "toString(end_user_id)" in sql


@pytest.mark.parametrize("column", ["session_id", "trace_session_id"])
@pytest.mark.parametrize("filter_op", ["equals", "in"])
def test_sibling_uuid_columns_are_unchanged(column: str, filter_op: str) -> None:
    """The other two members of ``_NULLABLE_UUID_COLUMNS`` are out of scope."""

    value = [USER_A] if filter_op == "in" else USER_A
    sql, _ = _condition(column, filter_op, value)

    assert f"toString({column})" in sql
    assert "toUUID(" not in sql


def test_negations_bind_a_valid_uuid_literal_in_canonical_form() -> None:
    """The cast renders lower case, so an upper-case literal must too."""

    equals_sql, equals_params = _condition("end_user_id", "equals", USER_A.upper())
    not_equals_sql, not_equals_params = _condition(
        "end_user_id", "not_equals", USER_A.upper()
    )
    not_in_sql, not_in_params = _condition("end_user_id", "not_in", [USER_A.upper()])

    assert equals_params == {"col_1": USER_A}
    assert not_equals_sql == "toString(end_user_id) != %(col_1)s"
    assert not_equals_params == {"col_1": USER_A}
    assert not_in_sql == "toString(end_user_id) NOT IN %(col_1)s"
    assert not_in_params == {"col_1": (USER_A,)}
    assert "toUUID(" in equals_sql


def test_no_row_can_satisfy_both_equals_and_not_equals() -> None:
    """The two forms of one value are complementary, whatever its case.

    Before this, ``equals`` was canonicalised while ``not_equals`` compared the
    original text, so an upper-case literal matched a row under both.
    """

    for literal in (USER_A, USER_A.upper()):
        _, equals_params = _condition("end_user_id", "equals", literal)
        _, not_equals_params = _condition("end_user_id", "not_equals", literal)

        assert equals_params["col_1"] == not_equals_params["col_1"] == USER_A


@pytest.mark.parametrize(
    ("filter_op", "filter_value", "expected_param"),
    [
        ("not_equals", NOT_A_UUID, NOT_A_UUID),
        ("not_in", [NOT_A_UUID], (NOT_A_UUID,)),
        ("not_equals", f"{{{USER_A}}}", f"{{{USER_A}}}"),
        ("not_in", [USER_A.upper(), NOT_A_UUID], (USER_A, NOT_A_UUID)),
    ],
)
def test_negations_leave_a_literal_that_is_not_a_uuid_alone(
    filter_op: str, filter_value: object, expected_param: object
) -> None:
    """It matches every non-NULL row under ``!=`` today and still does.

    Folding it away would be a behaviour change the positive ops could make
    only because a non-UUID can never equal a UUID.
    """

    _, params = _condition("end_user_id", filter_op, filter_value)

    assert params == {"col_1": expected_param}
