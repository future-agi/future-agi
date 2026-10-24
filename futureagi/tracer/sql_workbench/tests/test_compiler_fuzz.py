"""Property test: every generated variant is rejected or provably fully scoped.

Variants combine table-like targets (catalog, physical, system, qualified,
quoted, case-changed, CTE and alias names) with IN operators (NOT / GLOBAL /
case), right-hand-side wrappers, left-hand sides, query positions and
whitespace/comment separators. The generator is seeded, so a failure
reproduces exactly; ``variants()`` is reused by the live ClickHouse test.

The scope check below is written independently of the compiler's own
verification: it parses the emitted SQL, prunes subqueries that render exactly
like a server-generated source, and inspects everything else structurally.
CI runs the pinned SQLGlot; run this file with a newer SQLGlot first on
``sys.path`` to check an upgrade before it lands.
"""

import random
import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import sqlglot
from sqlglot import exp

from tracer.sql_workbench.catalog import CatalogUnavailable, Scope, sources
from tracer.sql_workbench.compiler import UnsafeQuery, compile_query

SEED = 8685
SAMPLE = 1500
CATALOG = ["spans", "traces", "sessions", "users", "eval_results", "annotations"]
TARGETS = CATALOG + [
    "derived_metrics",
    "tracer_eval_logger",
    "tracer_eval_logger_v2",
    "trace_sessions",
    "end_users",
    "model_hub_score",
    "system.tables",
    "system.one",
    "information_schema.tables",
    "default.spans",
    "analytics.spans",
    "analytics.trace_sessions",
    "own",
    "s",
]
OPERATORS = [
    "IN",
    "NOT IN",
    "GLOBAL IN",
    "GLOBAL NOT IN",
    "in",
    "not in",
    "Global In",
]
# ``{t}`` is a name ClickHouse would read as a table; ``{q}`` is a subquery.
RHS = [
    "{t}",
    "({t})",
    "(({t}))",
    "({t}, 'x')",
    "('x', {t})",
    "[{t}]",
    "tuple({t})",
    "{q}",
    "('x', 'y')",
]
LHS = ["id", "(id, trace_id)", "'x'", "toString(id)"]
POSITIONS = [
    "SELECT id FROM spans AS s WHERE {pred}",
    "SELECT {pred} AS hit FROM spans AS s",
    "SELECT id FROM spans AS s GROUP BY id HAVING {pred}",
    "SELECT CASE WHEN {pred} THEN 1 ELSE 0 END FROM spans AS s",
    "SELECT id FROM spans AS s ORDER BY {pred}",
    "SELECT id FROM (SELECT id, trace_id FROM spans AS s WHERE {pred}) AS d",
    "SELECT id FROM spans AS s WHERE id IN (SELECT id FROM spans WHERE {pred})",
    "WITH own AS (SELECT id, trace_id FROM spans WHERE {pred}) SELECT id FROM own",
    "WITH own AS (SELECT id, trace_id FROM spans) SELECT id FROM own WHERE {pred}",
    "SELECT id FROM spans UNION ALL SELECT id FROM spans AS s WHERE {pred}",
    "SELECT (SELECT count() FROM spans WHERE {pred}) FROM spans AS s",
    "SELECT s.id FROM spans AS s JOIN traces AS t ON s.trace_id = t.name WHERE {pred}",
]
SEPARATORS = [" ", "\n", "\t", " /* c */ ", "\n-- c\n", "  "]
GENERATED_CTE = re.compile(r"__sql_cte_[0-9]+\Z")
TABLE_READING_FUNCTIONS = re.compile(
    r"(global)?(not)?(null)?in\Z|dict.*|joinget.*|merge|remote.*|cluster.*|numbers|file|url|s3|view|input"
)


def _quoted(name, style):
    parts = name.split(".")
    if style == "backtick":
        return ".".join(f"`{part}`" for part in parts)
    if style == "double":
        return ".".join(f'"{part}"' for part in parts)
    if style == "upper":
        return name.upper()
    if style == "title":
        return name.title()
    return name


def variants(seed=SEED, sample=SAMPLE):
    """(sql, reads_a_bare_name) pairs: a deterministic core plus a seeded sample."""

    def build(target, style, operator, rhs, lhs, position, separator):
        name = _quoted(target, style)
        right = rhs.format(t=name, q=f"(SELECT id FROM {name})")
        predicate = f"{lhs}{separator}{operator}{separator}{right}"
        return position.format(pred=predicate), "{t}" in rhs

    dimensions = [
        TARGETS,
        ["plain", "backtick", "double", "upper", "title"],
        OPERATORS,
        RHS,
        LHS,
        POSITIONS,
        SEPARATORS,
    ]
    seen, result = set(), []
    # Core: every value of every dimension appears at least once.
    for axis, values in enumerate(dimensions):
        for value in values:
            choice = [dimension[0] for dimension in dimensions]
            choice[axis] = value
            item = build(*choice)
            if item not in seen:
                seen.add(item)
                result.append(item)
    generator = random.Random(seed)
    while len(result) < sample:
        item = build(*(generator.choice(dimension) for dimension in dimensions))
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


@pytest.fixture(scope="module")
def scope():
    end = datetime(2026, 10, 21, tzinfo=UTC)
    return Scope(
        uuid4(),
        uuid4(),
        uuid4(),
        end - timedelta(days=1),
        end,
        (uuid4(),),
        database="analytics",
    )


def canonical_sources(scope):
    rendered = set()
    for text in sources(scope).values():
        tree = sqlglot.parse_one(text, read="clickhouse")
        for table in tree.find_all(exp.Table):
            if table.name != "__sql_eval_candidates":
                table.set("db", exp.to_identifier(scope.database))
        rendered.add(tree.sql(dialect="clickhouse"))
    return rendered


def assert_fully_scoped(compiled, scope, canonical):
    """Independent structural check of one emitted statement."""
    tree = sqlglot.parse_one(compiled, read="clickhouse")
    remainder, found, pending = [], 0, [tree]
    while pending:
        node = pending.pop()
        if isinstance(node, exp.Subquery) and node.this.sql(dialect="clickhouse") in (
            canonical
        ):
            found += 1
            continue
        remainder.append(node)
        pending.extend(node.iter_expressions())
    assert found, compiled
    defined = {node.alias for node in remainder if isinstance(node, exp.CTE)}
    for node in remainder:
        if isinstance(node, exp.Table):
            assert not node.db and GENERATED_CTE.fullmatch(node.name), compiled
            assert node.name in defined, compiled
        elif isinstance(node, exp.CTE):
            assert GENERATED_CTE.fullmatch(node.alias), compiled
        elif isinstance(node, exp.In):
            assert node.args.get("field") is None, compiled
            assert node.args.get("unnest") is None, compiled
            assert not node.args.get("is_global"), compiled
            for value in node.expressions:
                assert not any(
                    isinstance(child, (exp.Column, exp.Identifier, exp.Table))
                    for child in value.walk()
                ), compiled
        elif isinstance(node, exp.Column):
            assert not node.args.get("db") and not node.args.get("catalog"), compiled
        elif isinstance(node, exp.Anonymous):
            assert not TABLE_READING_FUNCTIONS.fullmatch(node.name.lower()), compiled
    assert str(scope.project_id) in compiled


def test_generator_is_deterministic_and_covers_every_dimension():
    first, second = variants(), variants()
    assert first == second and len(first) == SAMPLE
    text = "\n".join(sql for sql, _ in first)
    for value in TARGETS + OPERATORS + LHS:
        assert value in text or value.upper() in text or value.title() in text


def test_every_variant_is_rejected_or_fully_scoped(scope):
    canonical = canonical_sources(scope)
    outcomes = {"rejected": 0, "scoped": 0}
    for sql, reads_a_bare_name in variants():
        try:
            compiled = compile_query(sql, scope)
        except (UnsafeQuery, CatalogUnavailable):
            outcomes["rejected"] += 1
            continue
        # A bare, quoted or qualified name on the right of IN is a table read
        # in ClickHouse and can never be scoped, so it must never compile.
        assert not reads_a_bare_name, sql
        assert_fully_scoped(compiled, scope, canonical)
        outcomes["scoped"] += 1
    print(f"sqlglot {sqlglot.__version__}: {outcomes}")
    assert outcomes["rejected"] and outcomes["scoped"]
