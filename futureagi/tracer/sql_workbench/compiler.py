"""Fail-closed ClickHouse AST authorization; no user SQL reaches the database."""

import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel, SqlglotError
from sqlglot.optimizer.scope import Scope as QueryScope, traverse_scope

from .catalog import COLUMNS, SOURCE_CTES, CatalogUnavailable, Scope, sources

MAX_SQL_BYTES = 32_768
MAX_AST_NODES = 1500
MAX_TABLE_REFERENCES = 16
MAX_RESULT_ROWS = 10_000
MAX_RESULT_BYTES = 8_000_000
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z_0-9]{0,127}\Z")

# Keep this list explicit. New SQLGlot nodes/functions remain denied after an
# upgrade until their ClickHouse rendering and effects have been reviewed.
NODE_NAMES = frozenset(
    """
Select Union Intersect Except Subquery With CTE Table TableAlias Identifier
Column Star From Join Where Group Having Order Ordered Limit Offset Distinct
Alias Literal Null Boolean Paren Tuple Bracket Array Map
And Or Not EQ NEQ GT GTE LT LTE Is In Between Like ILike
Add Sub Mul Div Mod Neg Case If When Cast TryCast DataType DataTypeParam
Interval Window WindowSpec Filter WithinGroup
""".split()
)
FUNCTIONS = frozenset(
    """
count countif sum sumif avg avgif min max any argmin argmax uniq uniqexact
quantile quantileexact median stddevpop stddevsamp varpop varsamp
abs round floor ceil ceiling sqrt pow power log ln exp greatest least
coalesce ifnull nullif if multiif lower upper length lengthutf8 substring
concat trim ltrim rtrim replace splitbychar splitbystring arrayelement
has mapkeys mapvalues jsonextractstring jsonextractfloat jsonextractint
jsonextractbool tostring touuid touuidornull toint64 tofloat64 todate
todatetime todatetime64 tostartofhour tostartofday tostartofweek
tostartofmonth toyear tomonth todayofweek formatdatetime datediff
dateadd now rownumber rank denserank lag lead firstvalue lastvalue
""".split()
)
# SQLGlot uses standard function names internally for several CH functions.
FUNCTION_ALIASES = frozenset(
    """
count_if sum_if avg_if arg_min arg_max stddev_pop stddev_samp variance_pop
variance_samp array_size length lower upper substring time_to_str
str_to_time time_to_unix date_diff date_add timestamp_trunc date_trunc
row_number dense_rank first_value last_value nullif coalesce
""".split()
)


DATE_UNITS = frozenset(
    {
        "YEAR",
        "QUARTER",
        "MONTH",
        "WEEK",
        "DAY",
        "HOUR",
        "MINUTE",
        "SECOND",
        "MILLISECOND",
        "MICROSECOND",
        "NANOSECOND",
    }
)
DATE_UNIT_PARENTS = (
    exp.Interval,
    exp.DateAdd,
    exp.DateDiff,
    exp.TimestampTrunc,
    exp.DateTrunc,
)


class UnsafeQuery(ValueError):
    pass


def _identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise UnsafeQuery(
            "Use simple SQL identifiers (letters, numbers and underscores)."
        )


def validate(sql: str):
    if not isinstance(sql, str) or not sql.strip() or len(sql.encode()) > MAX_SQL_BYTES:
        raise UnsafeQuery("Enter one SELECT query of at most 32 KiB.")
    try:
        statements = sqlglot.parse(sql, read="clickhouse", error_level=ErrorLevel.RAISE)
    except (SqlglotError, RecursionError) as exc:
        raise UnsafeQuery("Invalid ClickHouse SELECT syntax.") from exc
    if len(statements) != 1 or not isinstance(statements[0], (exp.Select, exp.Union)):
        raise UnsafeQuery("Only a single SELECT or UNION query is allowed.")
    tree = statements[0]
    nodes = list(tree.walk())
    if len(nodes) > MAX_AST_NODES:
        raise UnsafeQuery("The query is too complex.")
    for node in nodes:
        if isinstance(node, exp.Var):
            if (
                node.arg_key != "unit"
                or not isinstance(node.parent, DATE_UNIT_PARENTS)
                or node.name.upper() not in DATE_UNITS
            ):
                raise UnsafeQuery(
                    "Only approved date units in date/interval expressions are supported."
                )
        elif type(node).__name__ in NODE_NAMES:
            # Boolean operators and safe constructors are Func subclasses too;
            # authorize their syntax here, then validate every child normally.
            pass
        elif isinstance(node, exp.Func):
            name = (
                node.name
                if isinstance(
                    node, (exp.Anonymous, exp.CombinedAggFunc, exp.ParameterizedAgg)
                )
                else node.sql_name()
            )
            if name.lower() not in FUNCTIONS | FUNCTION_ALIASES:
                raise UnsafeQuery(f"Function {name} is not in the approved catalog.")
        else:
            raise UnsafeQuery(f"SQL construct {type(node).__name__} is not supported.")
        if isinstance(node, exp.Identifier):
            _identifier(node.this)
        if isinstance(node, exp.Select) and any(
            node.args.get(key)
            for key in (
                "settings",
                "format",
                "into",
                "locks",
                "operation_modifiers",
                "sample",
                "options",
            )
        ):
            raise UnsafeQuery(
                "Settings, output formats, sampling and statement modifiers are not allowed."
            )
        if isinstance(node, exp.CTE) and (
            node.args.get("scalar")
            or not isinstance(node.this, (exp.Select, exp.Union))
        ):
            raise UnsafeQuery(
                "Only SELECT/UNION relation CTEs are supported; scalar WITH aliases are not allowed."
            )
        if isinstance(node, exp.With) and node.args.get("recursive"):
            raise UnsafeQuery("Recursive CTEs are not supported.")
        if isinstance(node, exp.Table):
            if not isinstance(node.this, exp.Identifier) or any(
                value
                for key, value in node.args.items()
                if key not in {"this", "alias"}
            ):
                raise UnsafeQuery(
                    "Only unqualified catalog tables are allowed; table functions are forbidden."
                )
        if isinstance(node, exp.Column) and (
            node.args.get("db") or node.args.get("catalog")
        ):
            raise UnsafeQuery("Database-qualified columns are not allowed.")
        if isinstance(node, exp.Join) and (
            node.args.get("kind") in {"CROSS", "ARRAY"}
            or node.args.get("method") in {"GLOBAL", "NATURAL"}
            or node.args.get("global")
        ):
            raise UnsafeQuery(
                "Use bounded equality joins, not CROSS, ARRAY, GLOBAL or NATURAL joins."
            )
        if isinstance(node, exp.Join):
            condition = node.args.get("on")
            if not (condition or node.args.get("using")):
                raise UnsafeQuery("Joins require an explicit ON or USING condition.")
            if condition:
                terms = (
                    list(condition.flatten())
                    if isinstance(condition, exp.And)
                    else [condition]
                )
                if any(
                    not isinstance(term, exp.EQ)
                    or not isinstance(term.this, exp.Column)
                    or not isinstance(term.expression, exp.Column)
                    or not term.this.table
                    or not term.expression.table
                    or term.this.table == term.expression.table
                    for term in terms
                ):
                    raise UnsafeQuery(
                        "ON requires equality between columns of different table aliases."
                    )
    try:
        scopes = list(traverse_scope(tree))
        references = []
        for scope in scopes:
            for _, (node, source) in scope.selected_sources.items():
                if isinstance(source, exp.Table):
                    if source.name not in COLUMNS:
                        raise UnsafeQuery(f"Unknown dataset: {source.name}.")
                    references.append(source)
                elif not isinstance(source, QueryScope):
                    raise UnsafeQuery("Unsupported table source.")
        # Scalar subqueries can also contain tables; no unvisited relation may
        # survive as a raw identifier, including unused CTE bodies.
        handled = {id(table) for table in references}
        cte_tables = {
            id(t)
            for s in scopes
            for _, (t, source) in s.selected_sources.items()
            if isinstance(source, QueryScope)
        }
        if any(
            id(table) not in handled | cte_tables for table in tree.find_all(exp.Table)
        ):
            raise UnsafeQuery("Unresolved table source.")
        if len(references) > MAX_TABLE_REFERENCES or not references:
            raise UnsafeQuery("Select from 1 to 16 catalog relations.")
    except (SqlglotError, ValueError) as exc:
        if isinstance(exc, UnsafeQuery):
            raise
        raise UnsafeQuery("Ambiguous or unsupported table references.") from exc
    return tree, references


def compile_query(sql: str, scope: Scope) -> str:
    tree, references = validate(sql)
    scoped_sources = sources(scope)
    for table in references:
        if table.name not in scoped_sources:
            raise CatalogUnavailable(
                "Derived metrics are unavailable. Apply the metrics schema, reader grants and enable SQL_WORKBENCH_DERIVED_METRICS_ENABLED."
            )
        alias = table.args.get("alias")
        alias = (
            alias.copy()
            if alias
            else exp.TableAlias(this=exp.to_identifier(table.name))
        )
        source = sqlglot.parse_one(scoped_sources[table.name], read="clickhouse")
        # Qualification prevents user CTE names from shadowing physical sources.
        for physical in source.find_all(exp.Table):
            if physical.name not in SOURCE_CTES:
                physical.set("db", exp.to_identifier(scope.database))
        replacement = exp.Subquery(this=source, alias=alias)
        table.replace(replacement)
    # The outer bound also covers UNION, user LIMIT/OFFSET, and aggregation.
    bounded = (
        exp.select("*").from_(tree.subquery("query_result")).limit(MAX_RESULT_ROWS + 1)
    )
    try:
        return bounded.sql(dialect="clickhouse", unsupported_level=ErrorLevel.RAISE)
    except SqlglotError as exc:
        raise UnsafeQuery(
            "This query cannot be represented safely in ClickHouse."
        ) from exc
