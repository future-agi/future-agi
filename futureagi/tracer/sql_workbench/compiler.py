"""Fail-closed ClickHouse AST authorization; no user SQL reaches the database.

Two independent passes guard every query:

1. ``_analyze`` accepts only a closed set of syntax. Tables may appear only in
   FROM/JOIN as unqualified catalog names or CTE references; the right side of
   IN is a subquery or a list of constants (ClickHouse reads a bare or
   parenthesized name there as a whole table, including a database-qualified
   one or a table alias).
2. ``_verify_compiled`` re-parses the emitted text with the same dialect. After
   removing exactly the server-generated scoped sources, nothing that names a
   table may remain except renamed CTE references, so a parser or rewriting
   gap fails closed instead of reaching a physical table.
"""

import re
from collections import Counter

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
# The locked service profile's max_query_size; ClickHouse rejects longer text.
MAX_COMPILED_BYTES = 262_144
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z_0-9]{0,127}\Z")
# Every user CTE is renamed to one of these, so no user-chosen name can resolve
# to a physical table if ClickHouse ever scoped a CTE differently from SQLGlot.
GENERATED_CTE = re.compile(r"__sql_cte_[0-9]{1,4}\Z")
QUERY_TYPES = (exp.Select, exp.Union, exp.Intersect, exp.Except)
IN_MESSAGE = (
    "IN accepts a subquery or a list of constant values; a table, alias or "
    "column name is not allowed on its right side."
)
UNVERIFIABLE = "This query cannot be verified as fully scoped."
TOO_LARGE = (
    "The scoped query is too large to run; reference fewer datasets in one query."
)
SCOPED_SOURCE_PLACEHOLDER = "(SELECT 1 AS __sql_scoped_source)"
# Never approve these, even if one is added to FUNCTIONS by mistake: each reads
# a table, dictionary or setting named by its arguments, or is the function
# form of IN, which accepts a bare table name.
TABLE_ARGUMENT_FUNCTIONS = frozenset(
    """
in notin globalin globalnotin nullin notnullin globalnullin globalnotnullin
joinget joingetornull hascolumnintable getsetting getsettingordefault
currentdatabase tablefunction file url s3 remote remotesecure cluster
clusterallreplicas merge mysql postgresql numbers view input generaterandom
dictionary executable jdbc odbc hdfs iceberg deltalake hudi mongodb redis
sqlite values zeros loop format
""".split()
)
CONSTANT_NODES = (
    exp.Literal,
    exp.Null,
    exp.Boolean,
    exp.Neg,
    exp.Paren,
    exp.Tuple,
    exp.Array,
    exp.Cast,
    exp.TryCast,
    exp.DataType,
    exp.DataTypeParam,
)

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
JSONPath JSONPathRoot JSONPathKey JSONPathSubscript
""".split()
)
FUNCTIONS = frozenset(
    """
count countif sum sumif avg avgif min minif max maxif any anylast argmin argmax
uniq uniqif uniqexact uniqexactif uniqcombined median quantile quantiles
quantileexact quantiletdigest stddevpop stddevsamp varpop varsamp corr
avgweighted grouparray groupuniqarray topk
abs round floor ceil ceiling sqrt pow power log ln exp greatest least intdiv
modulo
coalesce ifnull nullif if multiif
lower upper length lengthutf8 substring concat trim ltrim rtrim replace
replaceall position positioncaseinsensitive startswith endswith match
splitbychar splitbystring arrayelement has mapkeys mapvalues mapcontains
jsonextract jsonextractstring jsonextractfloat jsonextractint jsonextractuint
jsonextractbool jsonextractraw jsonextractkeys jsonhas jsonlength jsontype
tostring touuid touuidornull toint64 tofloat64 todate todatetime todatetime64
tostartofminute tostartoffiveminutes tostartoffifteenminutes tostartofinterval
tostartofhour tostartofday tostartofweek tostartofmonth toyear tomonth
todayofmonth todayofweek tohour tominute formatdatetime datediff dateadd now
rownumber rank denserank lag lead firstvalue lastvalue
""".split()
)
# SQLGlot parses several ClickHouse functions into typed nodes and reports
# them by a normalized name (``uniq`` is APPROX_DISTINCT, ``has`` is
# ARRAY_CONTAINS). Each name here was reviewed with its ClickHouse rendering
# under the pinned SQLGlot, and every user-facing name in FUNCTIONS is
# executed on live ClickHouse by test_analytics_clickhouse.
FUNCTION_ALIASES = frozenset(
    """
count_if sum_if avg_if arg_min arg_max stddev_pop stddev_samp variance_pop
variance_samp array_size length lower upper substring time_to_str
str_to_time time_to_unix date_diff date_add timestamp_trunc date_trunc
row_number dense_rank first_value last_value nullif coalesce
ntile percent_rank cume_dist
approx_distinct any_value split array_contains json_extract_scalar
str_position starts_with ends_with regexp_like
""".split()
)
# Aggregates that build a collection per group need an explicit size bound
# (``groupArray(100)(x)``) so one group cannot grow without limit; quantile
# levels are numeric literals in [0, 1].
MAX_COLLECTION_SIZE = 1000
BOUNDED_COLLECTION_AGGREGATES = frozenset({"grouparray", "groupuniqarray", "topk"})
UNBOUNDED_WITHOUT_SIZE = frozenset({"grouparray", "groupuniqarray"})
LEVEL_AGGREGATES = frozenset(
    {"quantile", "quantiles", "quantileexact", "quantiletdigest"}
)
MAX_QUANTILE_LEVELS = 32


# Window functions. A window is accepted only inline (``OVER (...)``) and only
# over these functions; the OVER clause can reference columns of its own
# SELECT, so every table it reads is still a scoped catalog source.
RANKING_WINDOW_FUNCTIONS = (
    exp.RowNumber,
    exp.Rank,
    exp.DenseRank,
    exp.Ntile,
    exp.PercentRank,
    exp.CumeDist,
)
OFFSET_WINDOW_FUNCTIONS = (exp.Lag, exp.Lead)
VALUE_WINDOW_FUNCTIONS = (exp.FirstValue, exp.LastValue)
AGGREGATE_WINDOW_FUNCTIONS = (exp.Sum, exp.Avg, exp.Count, exp.Min, exp.Max)
WINDOW_FUNCTIONS = (
    RANKING_WINDOW_FUNCTIONS
    + OFFSET_WINDOW_FUNCTIONS
    + VALUE_WINDOW_FUNCTIONS
    + AGGREGATE_WINDOW_FUNCTIONS
)
# These have no meaning without OVER (ClickHouse rejects them as aggregates).
WINDOW_ONLY_FUNCTIONS = RANKING_WINDOW_FUNCTIONS + OFFSET_WINDOW_FUNCTIONS
WINDOW_MESSAGE = (
    "OVER (...) supports row_number, rank, dense_rank, ntile, percent_rank, "
    "cume_dist, lag, lead, first_value, last_value, sum, avg, count, min and max."
)
# A frame or offset bound larger than this is never useful against the
# 10,000-row result and the 10M-row read limit.
MAX_WINDOW_OFFSET = 1_000_000


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


def _function_name(node):
    """The name to authorize: the user's spelling where SQLGlot keeps it
    (anonymous and ClickHouse-specific aggregate nodes), otherwise SQLGlot's
    normalized name for a typed node."""
    return (
        node.name
        if isinstance(
            node,
            (
                exp.Anonymous,
                exp.AnonymousAggFunc,
                exp.CombinedAggFunc,
                exp.ParameterizedAgg,
            ),
        )
        else node.sql_name()
    )


def _level(value):
    """A numeric literal quantile level in [0, 1]."""
    if not isinstance(value, exp.Literal) or value.is_string:
        return False
    try:
        return 0 <= float(value.this) <= 1
    except ValueError:
        return False


def _check_aggregate_parameters(node):
    """Bounds for parametric aggregates; parameters are literals only."""
    name = _function_name(node).lower()
    if isinstance(node, exp.AnonymousAggFunc) and name in UNBOUNDED_WITHOUT_SIZE:
        raise UnsafeQuery(
            f"{node.name} needs a size bound: write {node.name}(N)(value) with N "
            f"at most {MAX_COLLECTION_SIZE:,}."
        )
    if isinstance(node, exp.Quantile) and not _level(node.args.get("quantile")):
        raise UnsafeQuery("Quantile levels are numeric literals between 0 and 1.")
    if not isinstance(node, exp.ParameterizedAgg):
        return
    # SQLGlot keeps a parametric aggregate's parameters in ``expressions``.
    parameters = node.expressions
    if name in BOUNDED_COLLECTION_AGGREGATES:
        if not (
            len(parameters) == 1
            and _small_integer(parameters[0])
            and 1 <= int(parameters[0].this) <= MAX_COLLECTION_SIZE
        ):
            raise UnsafeQuery(
                f"{node.name} takes one size bound from 1 to {MAX_COLLECTION_SIZE:,}."
            )
    elif name in LEVEL_AGGREGATES:
        if not (
            1 <= len(parameters) <= MAX_QUANTILE_LEVELS
            and all(_level(value) for value in parameters)
        ):
            raise UnsafeQuery("Quantile levels are numeric literals between 0 and 1.")
    else:
        raise UnsafeQuery(f"Function {node.name} does not take parameters here.")


def _constant(expression):
    """True only for literals and approved scalar calls over literals.

    Column, identifier, table, star, subquery and placeholder nodes are absent
    from this allow-list, so no name that ClickHouse could resolve as a table
    can hide inside an IN list (``x IN (spans)`` reads the table ``spans``).
    """
    return all(
        isinstance(node, CONSTANT_NODES)
        or (isinstance(node, exp.Func) and not isinstance(node, exp.AggFunc))
        for node in expression.walk()
    )


def _check_in(node):
    """Positive allow-list for the right side of IN / NOT IN."""
    if node.args.get("is_global"):
        raise UnsafeQuery(
            "GLOBAL IN is not supported; IN is already evaluated globally here."
        )
    present = {
        key
        for key, value in node.args.items()
        if value is not None and value is not False and value != []
    }
    if not present <= {"this", "expressions", "query"}:
        # ``field`` holds ``x IN spans`` / ``x IN db.table`` / ``x IN tuple(...)``;
        # ``unnest`` and any future argument are not reviewed.
        raise UnsafeQuery(IN_MESSAGE)
    query, values = node.args.get("query"), node.expressions
    if query is not None:
        if (
            values
            or not isinstance(query, exp.Subquery)
            or not isinstance(query.this, QUERY_TYPES)
            or any(value for key, value in query.args.items() if key != "this")
        ):
            raise UnsafeQuery(IN_MESSAGE)
    elif not values or not all(_constant(value) for value in values):
        raise UnsafeQuery(IN_MESSAGE)


def _small_integer(value):
    """True for a non-negative integer literal no larger than MAX_WINDOW_OFFSET."""
    return (
        isinstance(value, exp.Literal)
        and not value.is_string
        and value.this.isdigit()
        and int(value.this) <= MAX_WINDOW_OFFSET
    )


def _check_frame_bound(value, side):
    if value is None:
        return
    if value == "UNBOUNDED" and side in {"PRECEDING", "FOLLOWING"}:
        return
    if value == "CURRENT ROW" and side is None:
        return
    if _small_integer(value) and side in {"PRECEDING", "FOLLOWING"}:
        return
    raise UnsafeQuery(
        "Window frames take UNBOUNDED, CURRENT ROW or a whole-number offset "
        f"of at most {MAX_WINDOW_OFFSET:,} rows or units."
    )


def _check_window(node):
    """Inline windows over the reviewed function set, with bounded frames.

    Ranking functions, lag and lead take no frame: ClickHouse runs lag/lead as
    lagInFrame/leadInFrame, which honour the frame, so ``compile_query`` gives
    them the whole partition to keep standard lag/lead results.
    """
    function = node.this
    if node.args.get("alias") or node.args.get("first"):
        raise UnsafeQuery(
            "Named windows are not supported; write the window inline as OVER (...)."
        )
    if not isinstance(function, WINDOW_FUNCTIONS):
        raise UnsafeQuery(WINDOW_MESSAGE)
    if any(isinstance(child, exp.Distinct) for child in function.iter_expressions()):
        raise UnsafeQuery("DISTINCT is not supported inside a window aggregate.")
    if any(isinstance(child, exp.Window) for child in node.walk() if child is not node):
        raise UnsafeQuery("A window cannot contain another window function.")
    if isinstance(function, (exp.PercentRank, exp.CumeDist)) and function.expressions:
        raise UnsafeQuery("percent_rank() and cume_dist() take no arguments.")
    if isinstance(function, exp.Ntile) and not (
        _small_integer(function.this) and int(function.this.this) > 0
    ):
        raise UnsafeQuery("ntile takes a positive whole number of buckets.")
    if isinstance(function, OFFSET_WINDOW_FUNCTIONS):
        offset = function.args.get("offset")
        if offset is not None and not _small_integer(offset):
            raise UnsafeQuery(
                "lag and lead take a whole-number offset of at most "
                f"{MAX_WINDOW_OFFSET:,} rows."
            )
    spec = node.args.get("spec")
    if spec is None:
        return
    if isinstance(function, WINDOW_ONLY_FUNCTIONS):
        raise UnsafeQuery(
            "Ranking functions, lag and lead use the whole partition; remove the "
            "ROWS/RANGE frame."
        )
    if spec.args.get("kind") not in {"ROWS", "RANGE"} or spec.args.get("exclude"):
        raise UnsafeQuery("Window frames support ROWS or RANGE without EXCLUDE.")
    _check_frame_bound(spec.args.get("start"), spec.args.get("start_side"))
    _check_frame_bound(spec.args.get("end"), spec.args.get("end_side"))


def _check_node(node, *, approved_functions=True):
    """Closed syntax and argument rules shared by validation and verification.

    Verification re-parses SQLGlot's own rendering, which may spell an approved
    function differently (``toStartOfHour`` is emitted as ``dateTrunc``), so it
    keeps every rule except the input-spelling allow-list; table-reading
    functions stay denied in both passes.
    """
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
        name = _function_name(node)
        if (
            name.lower() in TABLE_ARGUMENT_FUNCTIONS
            or name.lower().startswith(("dict", "joinget"))
            or (approved_functions and name.lower() not in FUNCTIONS | FUNCTION_ALIASES)
        ):
            raise UnsafeQuery(f"Function {name} is not in the approved catalog.")
    else:
        raise UnsafeQuery(f"SQL construct {type(node).__name__} is not supported.")
    if approved_functions:
        # Input rules only: verification re-parses SQLGlot's own rendering,
        # which spells lag/lead as lagInFrame/leadInFrame with a frame.
        if isinstance(node, (exp.AggFunc, exp.ParameterizedAgg)):
            _check_aggregate_parameters(node)
        if isinstance(node, exp.Window):
            _check_window(node)
        elif isinstance(node, WINDOW_ONLY_FUNCTIONS) and not (
            isinstance(node.parent, exp.Window) and node.arg_key == "this"
        ):
            raise UnsafeQuery(
                f"{_function_name(node).lower()} needs an OVER (...) clause."
            )
    if isinstance(node, exp.Identifier):
        _identifier(node.this)
    if isinstance(node, exp.In):
        _check_in(node)
    if isinstance(node, exp.Subquery) and not isinstance(node.this, QUERY_TYPES):
        # ``FROM (spans)`` or a parenthesized name: only real subqueries.
        raise UnsafeQuery("A subquery must be a SELECT; use FROM dataset directly.")
    if isinstance(node, (exp.From, exp.Join)) and not isinstance(
        node.this, (exp.Table, exp.Subquery)
    ):
        raise UnsafeQuery("Unsupported table source.")
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
        node.args.get("scalar") or not isinstance(node.this, (exp.Select, exp.Union))
    ):
        raise UnsafeQuery(
            "Only SELECT/UNION relation CTEs are supported; scalar WITH aliases are not allowed."
        )
    if isinstance(node, exp.With) and node.args.get("recursive"):
        raise UnsafeQuery("Recursive CTEs are not supported.")
    if isinstance(node, exp.Table):
        if not isinstance(node.this, exp.Identifier) or any(
            value for key, value in node.args.items() if key not in {"this", "alias"}
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


def _analyze(sql: str):
    """Return the tree, catalog references and (table, CTE) reference links."""
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
        _check_node(node)
    try:
        scopes = list(traverse_scope(tree))
        references = []
        cte_links = []
        for scope in scopes:
            for _, (node, source) in scope.selected_sources.items():
                if isinstance(source, exp.Table):
                    if source.name not in COLUMNS:
                        raise UnsafeQuery(f"Unknown dataset: {source.name}.")
                    references.append(source)
                elif not isinstance(source, QueryScope):
                    raise UnsafeQuery("Unsupported table source.")
                elif isinstance(node, exp.Table):
                    cte = source.expression.parent
                    if not isinstance(cte, exp.CTE):
                        raise UnsafeQuery("Unsupported table source.")
                    cte_links.append((node, cte))
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
    return tree, references, cte_links


def validate(sql: str):
    tree, references, _ = _analyze(sql)
    return tree, references


def _rename_ctes(tree, cte_links):
    """Give every user CTE a generated name and point its references at it.

    A reference keeps the user's name as its alias, so qualified columns such
    as ``x.id`` still resolve. A user-chosen CTE name therefore never reaches
    ClickHouse as a table identifier.
    """
    definitions = list(tree.find_all(exp.CTE))
    if len(definitions) > 9999:
        raise UnsafeQuery("The query is too complex.")
    names = {id(cte): f"__sql_cte_{index}" for index, cte in enumerate(definitions)}
    for table, cte in cte_links:
        if id(cte) not in names:
            raise UnsafeQuery("Unsupported table source.")
        if not table.args.get("alias"):
            table.set("alias", exp.TableAlias(this=exp.to_identifier(table.name)))
        table.set("this", exp.to_identifier(names[id(cte)]))
    for cte in definitions:
        alias = cte.args.get("alias")
        if not isinstance(alias, exp.TableAlias):
            raise UnsafeQuery("Unsupported table source.")
        alias.set("this", exp.to_identifier(names[id(cte)]))


def _whole_partition():
    return exp.WindowSpec(
        kind="ROWS",
        start="UNBOUNDED",
        start_side="PRECEDING",
        end="UNBOUNDED",
        end_side="FOLLOWING",
    )


def _rewrite_windows(tree):
    """Standard window results on ClickHouse, which has no lag, lead or cume_dist.

    * lag/lead render as lagInFrame/leadInFrame, which read only the frame; the
      default frame ends at the current row, so lead would silently return the
      default for every row. They get the whole partition as their frame, and
      without an explicit default their argument is made Nullable so a row
      outside the partition is NULL rather than the type's zero value.
    * cume_dist (rows up to and including the current row's peers, divided by
      the partition size) becomes a ratio of two counts over the same
      PARTITION BY / ORDER BY; the numerator's RANGE frame includes peers.
    """
    for window in list(tree.find_all(exp.Window)):
        function = window.this
        if isinstance(function, OFFSET_WINDOW_FUNCTIONS):
            window.set("spec", _whole_partition())
            if function.args.get("default") is None:
                # Out of range is NULL, as in standard SQL, not the type's zero.
                function.set(
                    "this",
                    exp.Anonymous(this="toNullable", expressions=[function.this]),
                )
        elif isinstance(function, exp.CumeDist):
            partition = window.args.get("partition_by") or []
            order = window.args.get("order")
            peers = exp.Window(
                this=exp.Count(this=exp.Star()),
                partition_by=[value.copy() for value in partition],
                order=order.copy() if order else None,
                spec=(
                    exp.WindowSpec(
                        kind="RANGE",
                        start="UNBOUNDED",
                        start_side="PRECEDING",
                        end="CURRENT ROW",
                    )
                    if order
                    else None
                ),
                over="OVER",
            )
            total = exp.Window(
                this=exp.Count(this=exp.Star()),
                partition_by=[value.copy() for value in partition],
                over="OVER",
            )
            window.replace(exp.Paren(this=exp.Div(this=peers, expression=total)))


NAME_NODES = (
    exp.Column,
    exp.Identifier,
    exp.Table,
    exp.Star,
    exp.Dot,
    exp.Placeholder,
    exp.Parameter,
    exp.Subquery,
    exp.Query,
)


def _unscoped_table_positions(tree, defined_ctes):
    """Every place in a re-parsed remainder where ClickHouse could read a table.

    Written independently of the validation rules on purpose: it must still
    find an IN-table read or a physical table if a validation rule regresses.
    """
    for node in tree.walk():
        if isinstance(node, exp.Table):
            if (
                node.args.get("db")
                or node.args.get("catalog")
                or not GENERATED_CTE.fullmatch(node.name)
                or node.name not in defined_ctes
            ):
                yield node
        elif isinstance(node, exp.In):
            # A name on the right side of IN is a whole-table read in ClickHouse.
            if any(
                node.args.get(key) is not None for key in ("field", "unnest")
            ) or any(
                isinstance(child, NAME_NODES)
                for value in node.expressions
                for child in value.walk()
            ):
                yield node
        elif isinstance(node, exp.Func) and (
            _function_name(node).lower() in TABLE_ARGUMENT_FUNCTIONS
            or _function_name(node).lower().startswith(("dict", "joinget"))
        ):
            yield node
        elif isinstance(node, exp.Column) and (
            node.args.get("db") or node.args.get("catalog")
        ):
            yield node


def _verify_compiled(compiled: str, canonical_sources):
    """Fail closed unless only generated scoped sources can read a table.

    ``canonical_sources`` maps each server-generated source rendering to the
    number of references compiled from it. Each must occur exactly that many
    times as a parenthesized subquery in the emitted text. Those occurrences
    are swapped for an inert subquery and everything else, the user's part of
    the emitted text, is re-parsed with the same dialect and must contain no
    table reference except a generated CTE name defined in the same statement,
    no IN-table form, no qualified column and no unreviewed construct.

    The swap is exact text and cannot match inside user SQL: user identifiers
    are simple ASCII names, rendered string literals escape every quote, and
    comments are not emitted, while each source contains quoted literals.
    """
    if len(compiled.encode()) > MAX_COMPILED_BYTES:
        raise UnsafeQuery(TOO_LARGE)
    remainder = compiled
    for text in sorted(canonical_sources, key=len, reverse=True):
        wrapped = f"({text})"
        if remainder.count(wrapped) != canonical_sources[text]:
            raise UnsafeQuery(UNVERIFIABLE)
        remainder = remainder.replace(wrapped, SCOPED_SOURCE_PLACEHOLDER)
    if remainder.count(SCOPED_SOURCE_PLACEHOLDER) != sum(canonical_sources.values()):
        raise UnsafeQuery(UNVERIFIABLE)
    try:
        tree = sqlglot.parse_one(
            remainder, read="clickhouse", error_level=ErrorLevel.RAISE
        )
        nodes = list(tree.walk())
        defined = {node.alias for node in nodes if isinstance(node, exp.CTE)}
        if next(_unscoped_table_positions(tree, defined), None) is not None:
            raise UnsafeQuery(UNVERIFIABLE)
        for node in nodes:
            _check_node(node, approved_functions=False)
            if isinstance(node, exp.CTE) and not GENERATED_CTE.fullmatch(node.alias):
                raise UnsafeQuery(UNVERIFIABLE)
            if isinstance(node, exp.Table) and (
                not GENERATED_CTE.fullmatch(node.name) or node.name not in defined
            ):
                raise UnsafeQuery(UNVERIFIABLE)
    except (SqlglotError, RecursionError, UnsafeQuery) as exc:
        raise UnsafeQuery(UNVERIFIABLE) from exc
    if sum(
        isinstance(node, exp.Subquery)
        and node.this.sql(dialect="clickhouse") == SCOPED_SOURCE_PLACEHOLDER[1:-1]
        for node in nodes
    ) != sum(canonical_sources.values()):
        raise UnsafeQuery(UNVERIFIABLE)


def compile_query(sql: str, scope: Scope) -> str:
    tree, references, cte_links = _analyze(sql)
    scoped_sources = sources(scope)
    for table in references:
        if table.name not in scoped_sources:
            raise CatalogUnavailable(
                "Derived metrics are unavailable. Apply the metrics schema, reader grants and enable SQL_WORKBENCH_DERIVED_METRICS_ENABLED."
            )
    # Bound parser work before building anything from large source templates.
    if sum(len(scoped_sources[table.name]) for table in references) > (
        MAX_COMPILED_BYTES * 11 // 10
    ):
        raise UnsafeQuery(TOO_LARGE)
    _rename_ctes(tree, cte_links)
    _rewrite_windows(tree)
    parsed, canonical = {}, Counter()
    for table in references:
        alias = table.args.get("alias")
        alias = (
            alias.copy()
            if alias
            else exp.TableAlias(this=exp.to_identifier(table.name))
        )
        if table.name not in parsed:
            source = sqlglot.parse_one(scoped_sources[table.name], read="clickhouse")
            # Qualification prevents user CTE names from shadowing physical sources.
            for physical in source.find_all(exp.Table):
                if physical.name not in SOURCE_CTES:
                    physical.set("db", exp.to_identifier(scope.database))
            parsed[table.name] = (source, source.sql(dialect="clickhouse"))
        source, text = parsed[table.name]
        canonical[text] += 1
        table.replace(exp.Subquery(this=source.copy(), alias=alias))
    # The outer bound also covers UNION, user LIMIT/OFFSET, and aggregation.
    bounded = (
        exp.select("*").from_(tree.subquery("query_result")).limit(MAX_RESULT_ROWS + 1)
    )
    try:
        compiled = bounded.sql(
            dialect="clickhouse", unsupported_level=ErrorLevel.RAISE, comments=False
        )
    except SqlglotError as exc:
        raise UnsafeQuery(
            "This query cannot be represented safely in ClickHouse."
        ) from exc
    _verify_compiled(compiled, canonical)
    return compiled
