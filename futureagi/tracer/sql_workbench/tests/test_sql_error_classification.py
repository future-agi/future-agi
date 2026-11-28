"""ClickHouse failures map to safe user text by integer code alone."""

import pytest
from clickhouse_driver.errors import ErrorCodes, ServerException, SocketTimeoutError

from tracer.sql_workbench import errors

PHYSICAL = "test_sql_0a1b2c3d.spans"
ORG = "8f14e45f-ceea-467a-9575-2f4d1c6b7a01"
PROJECT = "c9f0f895-fb98-4b91-8d4e-1a2b3c4d5e6f"


def server(code, message="synthetic"):
    return ServerException(message, code=code)


@pytest.mark.parametrize(
    "code, expected",
    [
        (62, "syntax error"),
        (47, "unknown column or alias"),
        (16, "not available at that point"),
        (10, "not available at that point"),
        (46, "unknown function"),
        (43, "argument of the wrong type"),
        (42, "wrong number of arguments"),
        (
            215,
            "Every selected column must be in GROUP BY or inside an aggregate function",
        ),
        (184, "cannot be nested or used in WHERE"),
        (53, "incompatible types"),
    ],
)
def test_each_user_sql_class_has_its_own_safe_message(code, expected):
    message = errors.user_message(server(code), "SELECT 1 FROM spans")
    assert expected.lower() in message.lower()
    assert errors.NARROW not in message
    assert "resource limit" not in message
    assert message != errors.GENERIC


@pytest.mark.parametrize("code", [159, 160, 241, 158, 307, 396])
def test_real_resource_limits_keep_the_narrowing_advice(code):
    message = errors.user_message(server(code), "SELECT 1 FROM spans")
    assert message.endswith(errors.NARROW)


def test_profile_set_and_ast_ceilings_are_resource_limits():
    set_limit = errors.user_message(
        server(ErrorCodes.SET_SIZE_LIMIT_EXCEEDED), "SELECT 1"
    )
    assert set_limit.endswith(errors.NARROW)
    for name in ("TOO_DEEP_AST", "TOO_BIG_AST"):
        code = getattr(ErrorCodes, name, None)
        if code is not None:
            assert "too complex" in errors.user_message(server(code), "SELECT 1")


def test_too_many_simultaneous_queries_says_try_again_shortly():
    message = errors.user_message(server(202), "SELECT 1")
    assert "try again shortly" in message.lower()
    assert errors.NARROW not in message


@pytest.mark.parametrize("code", [1, 49, 60, 81, 164, 497, 999_999])
def test_other_server_codes_are_neutral(code):
    assert errors.user_message(server(code), "SELECT 1") == errors.GENERIC


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("Code: 215. Column `model` is not under aggregate function"),
        ValueError("TIMEOUT_EXCEEDED"),
        SocketTimeoutError("transport details"),
    ],
)
def test_non_server_errors_are_neutral_whatever_their_text(exc):
    # Classification never reads message text, even when it looks like a code.
    assert errors.user_message(exc, "SELECT model FROM spans") == errors.GENERIC
    assert errors.clickhouse_code(exc) is None


def test_string_or_bool_codes_are_not_clickhouse_codes():
    odd = ServerException("synthetic", code=215)
    odd.code = "215"
    assert errors.clickhouse_code(odd) is None
    odd.code = True
    assert errors.clickhouse_code(odd) is None


@pytest.mark.parametrize(
    "code, message, sql, expected",
    [
        (
            47,
            f"Unknown expression identifier `latncy_ms` in scope SELECT latncy_ms FROM {PHYSICAL}",
            "SELECT latncy_ms FROM spans",
            "Unknown column or alias `latncy_ms`.",
        ),
        (
            47,
            f"Missing columns: 'latncy_ms' while processing query: 'SELECT latncy_ms FROM {PHYSICAL}'",
            "SELECT latncy_ms FROM spans",
            "Unknown column or alias `latncy_ms`.",
        ),
        (
            215,
            "Column `__table1.model` is not under aggregate function and not in GROUP BY keys.",
            "SELECT model, count() FROM spans",
            "`model` must be in GROUP BY",
        ),
        (
            16,
            "There's no column 'tokens' in table 'spans'",
            "SELECT s.tokens FROM spans AS s",
            "Column `tokens` is not available",
        ),
        (
            46,
            "Unknown function `foo_bar`. Maybe you meant: ['floor']",
            "SELECT foo_bar(1) FROM spans",
            "Unknown function `foo_bar`.",
        ),
    ],
)
def test_names_an_identifier_only_when_the_user_typed_it(code, message, sql, expected):
    result = errors.user_message(server(code, message), sql)
    assert result.startswith(expected)
    assert PHYSICAL not in result and "__table1" not in result


@pytest.mark.parametrize(
    "code, message, sql",
    [
        # The name is internal: present in the compiled SQL, not in the user's.
        (
            47,
            f"Unknown expression identifier `project_id` in scope SELECT x FROM {PHYSICAL} WHERE project_id = '{PROJECT}'",
            "SELECT x FROM spans",
        ),
        # Present only as part of a longer user token: not a whole-token match.
        (47, "Unknown expression identifier `cost`", "SELECT total_cost FROM spans"),
        (215, "Column `model` is not under aggregate function", "SELECT models"),
        # Physical table names and organization/project values are never echoed.
        (47, f"Missing columns: '{ORG}'", f"SELECT '{ORG}' FROM spans"),
        (16, f"There's no column `{PHYSICAL}_v2`", "SELECT 1 FROM spans"),
        # Internal aliases are never echoed, even if somehow typed.
        (47, "Unknown expression identifier `__sql_cte_1`", "SELECT __sql_cte_1"),
        # No quoted name at all.
        (215, "not under aggregate function", "SELECT model FROM spans"),
    ],
)
def test_category_only_when_the_name_is_not_the_users(code, message, sql):
    result = errors.user_message(server(code, message), sql)
    assert result == errors.USER_ERRORS[code][1]
    for hidden_value in (PHYSICAL, ORG, PROJECT, "project_id", "__sql_cte"):
        assert hidden_value not in result


def test_identifier_match_is_case_sensitive_and_verbatim():
    result = errors.user_message(
        server(47, "Unknown expression identifier `Model`"), "SELECT model FROM spans"
    )
    assert result == errors.UNKNOWN_IDENTIFIER[1]


def test_classifies_a_server_error_found_on_the_chained_cause():
    class Wrapped(Exception):
        pass

    inner = server(215, "Column `model` is not under aggregate function")
    try:
        try:
            raise inner
        except ServerException as exc:
            raise Wrapped("wrapper with no code") from exc
    except Wrapped as outer:
        caught = outer
    assert errors.clickhouse_code(caught) == 215
    assert errors.user_message(caught, "SELECT model FROM spans").startswith(
        "`model` must be in GROUP BY"
    )


def test_classifies_a_server_error_found_on_the_implicit_context():
    try:
        try:
            raise server(241)
        except ServerException:
            raise RuntimeError("cleanup failed")  # noqa: B904
    except RuntimeError as outer:
        caught = outer
    assert errors.clickhouse_code(caught) == 241
    assert errors.user_message(caught, "SELECT 1").endswith(errors.NARROW)


def test_suppressed_context_is_not_followed():
    try:
        try:
            raise server(241)
        except ServerException:
            raise RuntimeError("replaced") from None
    except RuntimeError as outer:
        caught = outer
    assert errors.clickhouse_code(caught) is None
    assert errors.user_message(caught, "SELECT 1") == errors.GENERIC


def test_cyclic_and_deep_chains_terminate():
    first, second = RuntimeError("a"), RuntimeError("b")
    first.__cause__, second.__cause__ = second, first
    assert errors.clickhouse_code(first) is None
    head = current = RuntimeError("0")
    for index in range(1, 100):
        current.__cause__ = RuntimeError(str(index))
        current = current.__cause__
    current.__cause__ = server(215)
    # Beyond the bounded walk: neutral, never an unbounded traversal.
    assert errors.user_message(head, "SELECT 1") == errors.GENERIC


def test_messages_fit_the_stored_error_cap():
    longest = "a" * 64
    for named, category in errors.USER_ERRORS.values():
        assert len(category) <= 300
        if named is not None:
            assert len(named.format(name=longest)) <= 300
    for message in errors.RESOURCE_ERRORS.values():
        assert len(message) <= 300
