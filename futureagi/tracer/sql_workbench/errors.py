"""Safe user text for a failed ClickHouse read.

The class of a failure comes only from the integer ClickHouse error code. Driver
message text carries the compiled query: physical database and table names,
internal aliases, and the organization/project filter literals. It is never
returned or logged. The one thing taken from it is a candidate name for the
offending column or function, and that name is shown only when it also appears,
as a whole token, in the SQL the user typed.
"""

import re

GENERIC = "Query failed. Check the query and try again."
NARROW = "Narrow the window or simplify the query."
BUSY = "SQL service is busy. Try again shortly."

# User SQL mistakes: (message naming the identifier, category-only message).
UNKNOWN_IDENTIFIER = (
    "Unknown column or alias `{name}`. Check its spelling against the dataset columns in the catalog.",
    "The query uses an unknown column or alias. Check spelling against the dataset columns in the catalog.",
)
COLUMN_NOT_AVAILABLE = (
    "Column `{name}` is not available at that point in the query. Select it in the subquery or CTE it comes from.",
    "The query uses a column that is not available at that point. Select it in the subquery or CTE it comes from.",
)
UNKNOWN_FUNCTION = (
    "Unknown function `{name}`. Use a function listed in the catalog.",
    "The query uses an unknown function. Use a function listed in the catalog.",
)
NOT_AN_AGGREGATE = (
    "`{name}` must be in GROUP BY or inside an aggregate function such as count(), sum() or avg().",
    "Every selected column must be in GROUP BY or inside an aggregate function such as count(), sum() or avg().",
)
USER_ERRORS = {
    62: (  # SYNTAX_ERROR
        None,
        "The query has a syntax error. Check for typos, missing commas and unbalanced parentheses or quotes.",
    ),
    47: UNKNOWN_IDENTIFIER,  # UNKNOWN_IDENTIFIER
    16: COLUMN_NOT_AVAILABLE,  # NO_SUCH_COLUMN_IN_TABLE
    10: COLUMN_NOT_AVAILABLE,  # NOT_FOUND_COLUMN_IN_BLOCK
    46: UNKNOWN_FUNCTION,  # UNKNOWN_FUNCTION
    43: (  # ILLEGAL_TYPE_OF_ARGUMENT
        None,
        "A function received an argument of the wrong type. Check argument types, or convert with a function such as toString() or toFloat64().",
    ),
    42: (  # NUMBER_OF_ARGUMENTS_DOESNT_MATCH
        None,
        "A function was called with the wrong number of arguments. Check the function's arguments.",
    ),
    215: NOT_AN_AGGREGATE,  # NOT_AN_AGGREGATE
    184: (  # ILLEGAL_AGGREGATION
        None,
        "Aggregate functions cannot be nested or used in WHERE. Filter aggregates in HAVING, or compute the inner aggregate in a subquery.",
    ),
    53: (  # TYPE_MISMATCH
        None,
        "The query compares or combines values of incompatible types. Convert one side so both have the same type, for example with toString() or toFloat64().",
    ),
}

# Real resource limits enforced by the locked profile and request settings.
_RESOURCE_ERRORS = {
    159: f"Query exceeded the 30-second execution limit. {NARROW}",  # TIMEOUT_EXCEEDED
    160: f"Query would exceed the 30-second execution limit. {NARROW}",  # TOO_SLOW
    241: f"Query exceeded the memory limit. {NARROW}",  # MEMORY_LIMIT_EXCEEDED
    158: f"Query exceeded a row or byte limit. {NARROW}",  # TOO_MANY_ROWS
    307: f"Query exceeded a row or byte limit. {NARROW}",  # TOO_MANY_BYTES
    396: f"Query exceeded a row or byte limit. {NARROW}",  # TOO_MANY_ROWS_OR_BYTES
    202: BUSY,  # TOO_MANY_SIMULTANEOUS_QUERIES
}


def _limit_codes():
    """Further profile ceilings, by their driver-registered names only."""
    from clickhouse_driver.errors import ErrorCodes

    messages = {
        # max_rows_in_set / max_bytes_in_set and the join ceilings.
        "SET_SIZE_LIMIT_EXCEEDED": f"Query exceeded a join or IN-set size limit. {NARROW}",
        # max_ast_depth / max_ast_elements.
        "TOO_DEEP_AST": "The query is too complex. Simplify the query.",
        "TOO_BIG_AST": "The query is too complex. Simplify the query.",
    }
    codes: dict[int, str] = {}
    for name, message in messages.items():
        code = getattr(ErrorCodes, name, None)
        if isinstance(code, int) and code not in USER_ERRORS:
            codes.setdefault(code, message)
    return codes


RESOURCE_ERRORS = {**_limit_codes(), **_RESOURCE_ERRORS}

MAX_CHAIN = 16
# The first backtick- or single-quoted name in the driver message is the
# subject of these errors (for example: Unknown expression identifier `x`,
# Missing columns: 'x', Column `x` is not under aggregate function).
QUOTED_NAME = re.compile(r"[`']([A-Za-z_][A-Za-z_0-9.]{0,127})[`']")
NAME = re.compile(r"[A-Za-z_][A-Za-z_0-9]{0,63}\Z")


def clickhouse_code(exc):
    """The integer code of the first ClickHouse server error in the chain."""
    server = server_exception(exc)
    return server.code if server is not None else None


def server_exception(exc):
    """Walk ``__cause__``/``__context__`` safely to a ``ServerException``."""
    from clickhouse_driver.errors import ServerException

    seen: set[int] = set()
    current = exc
    while current is not None and id(current) not in seen and len(seen) < MAX_CHAIN:
        seen.add(id(current))
        if isinstance(current, ServerException):
            code = getattr(current, "code", None)
            if isinstance(code, int) and not isinstance(code, bool):
                return current
        if current.__cause__ is not None:
            current = current.__cause__
        elif not current.__suppress_context__:
            current = current.__context__
        else:
            current = None
    return None


def _user_name(server, user_sql):
    """A name from the driver message, only if the user typed it verbatim."""
    text = getattr(server, "message", None)
    if not isinstance(text, str):
        return None
    match = QUOTED_NAME.search(text)
    if match is None or not isinstance(user_sql, str):
        return None
    name = match.group(1).rsplit(".", 1)[-1]
    if not NAME.match(name) or name.startswith("__"):
        return None
    pattern = rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])"
    return name if re.search(pattern, user_sql) else None


def user_message(exc, user_sql):
    """Safe text for a failed run; ``user_sql`` is the SQL the user typed.

    Never pass the compiled query: names are approved against this text.
    """
    server = server_exception(exc)
    if server is None:
        return GENERIC
    code = server.code
    if code in RESOURCE_ERRORS:
        return RESOURCE_ERRORS[code]
    if code not in USER_ERRORS:
        return GENERIC
    named, category = USER_ERRORS[code]
    if named is not None:
        name = _user_name(server, user_sql)
        if name is not None:
            return named.format(name=name)
    return category
