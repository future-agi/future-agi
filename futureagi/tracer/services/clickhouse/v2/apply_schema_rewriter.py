"""Pure-function rewriters used by apply_schema.py.

Kept separate from apply_schema.py so they can be unit-tested without
importing clickhouse_connect (which the integration script requires).

The rewriter is load-bearing for the production rollout: a silent
regression would create non-replicated tables in prod, causing silent
split-brain across replicas. Sibling test file:
`tracer/tests/test_ch25_apply_schema_replicated.py`.
"""

from __future__ import annotations

import re


# ──────────────────────────────────────────────────────────────────────────────
# Statement splitter — extracted so the rewriter is self-contained.
# ──────────────────────────────────────────────────────────────────────────────
def split_statements(sql: str) -> list[str]:
    """Split a SQL file into individual statements.

    We split on `;` followed by a newline, then strip + filter empties. This is
    simpler than a full SQL parser and works for our schema files (no
    semicolons inside string literals or comments today). If a file ever
    needs a semicolon in a string, wrap the affected statement in its own file.
    """
    parts = sql.split(";\n")
    out = []
    for part in parts:
        stripped = "\n".join(
            line
            for line in part.splitlines()
            if line.strip() and not line.strip().startswith("--")
        ).strip()
        if stripped:
            out.append(stripped + ";")
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Replicated-mode rewriter
# ──────────────────────────────────────────────────────────────────────────────
#
# We deliberately do NOT maintain two parallel schema directories (one for
# local / non-Replicated, one for prod / Replicated). Same source of truth,
# rewritten at apply time. This keeps DECISIONS.md and the schema files
# small enough to grep through, and means a schema edit can't accidentally
# diverge between environments.
#
# Engines we rewrite:
#   ReplacingMergeTree(...)     → ReplicatedReplacingMergeTree('zk_path', '{replica}', ...)
#   AggregatingMergeTree        → ReplicatedAggregatingMergeTree('zk_path', '{replica}')
#   MergeTree                   → ReplicatedMergeTree('zk_path', '{replica}')
#   SummingMergeTree(...)       → ReplicatedSummingMergeTree('zk_path', '{replica}', ...)
#
# We also append ` ON CLUSTER '<cluster>' ` to:
#   CREATE TABLE IF NOT EXISTS <name>
#   CREATE MATERIALIZED VIEW IF NOT EXISTS <name> TO <target>
#   CREATE DICTIONARY IF NOT EXISTS <name>      (dictionaries are node-local —
#                                                without ON CLUSTER the DDL lands
#                                                only on the connected replica)
#   ALTER TABLE <name>                          (for projection adds in 007)

# Object-name pattern: each segment can be bare, backtick-quoted, or
# double-quoted; a qualified name is two segments joined by `.`.
# Schema-qualified names are codex review P1 finding.
_SEGMENT = r"(?:`[^`]+`|\"[^\"]+\"|[A-Za-z_][A-Za-z0-9_]*)"
_OBJECT_NAME = rf"(?P<obj>{_SEGMENT}(?:\.{_SEGMENT})?)"
_CREATE_TABLE_RE = re.compile(
    r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    + _OBJECT_NAME,
    re.IGNORECASE,
)
_CREATE_MV_RE = re.compile(
    r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?MATERIALIZED\s+VIEW\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    + _OBJECT_NAME,
    re.IGNORECASE,
)
_CREATE_DICTIONARY_RE = re.compile(
    r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?DICTIONARY\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    + _OBJECT_NAME,
    re.IGNORECASE,
)
_ALTER_TABLE_RE = re.compile(
    r"^\s*ALTER\s+TABLE\s+" + _OBJECT_NAME,
    re.IGNORECASE,
)


def _object_name_from_match(m: re.Match) -> str:
    """Pick the captured object name; for `<db>.<table>` shapes return
    just `<table>` (last segment) so the ZK path uses the table identity.
    Quoting is stripped from the returned segment.
    """
    raw = m.group("obj") or ""
    if "." in raw:
        raw = raw.rsplit(".", 1)[-1]
    # Strip backticks / double-quotes if the segment was quoted.
    if len(raw) >= 2 and raw[0] in ("`", '"') and raw[-1] == raw[0]:
        raw = raw[1:-1]
    return raw


# Match the ENGINE = clause. We support both multi-line and single-line
# SQL — the leading group accepts a newline, a space, or the start of the
# string so the rewriter doesn't miss inline statements.
#
# We capture optional parentheses to preserve constructor arguments
# (e.g. ReplacingMergeTree(_version, is_deleted) → keep _version, is_deleted).
_ENGINE_RE = re.compile(
    r"(?P<lead>^|[\s\n])(?P<indent>\s*)ENGINE\s*=\s*"
    r"(?P<name>ReplacingMergeTree|AggregatingMergeTree|SummingMergeTree|MergeTree)"
    r"(?P<args>\s*\([^)]*\))?",
    re.IGNORECASE,
)


def extract_table_name(stmt: str) -> str | None:
    """Return the table, MV, or dictionary name for an apply-time rewrite. Returns None
    for statements that aren't subject to engine rewriting (INSERTs, DROPs, etc.).
    Handles bare identifiers, backtick/double-quoted, and <db>.<table> shapes.
    """
    for rx in (_CREATE_TABLE_RE, _CREATE_MV_RE, _CREATE_DICTIONARY_RE, _ALTER_TABLE_RE):
        m = rx.match(stmt)
        if m:
            return _object_name_from_match(m)
    return None


class ReplicatedRewriteError(Exception):
    """Raised when a CREATE TABLE in replicated mode uses an engine the
    rewriter doesn't know how to replace, or when the rewrite is otherwise
    ambiguous. Fail-closed prevents the apply runner from silently writing
    a non-replicated table on a production cluster (codex review P1).
    """


# Only CREATE TABLE statements *require* an engine declaration. Other
# CREATE shapes that we rewrite (CREATE MATERIALIZED VIEW ... TO <target>,
# the only MV pattern we use) inherit the engine from their TO target
# table — they correctly have no ENGINE = clause.
_KIND_REQUIRES_ENGINE = ("CREATE TABLE",)


def rewrite_for_replicated(
    stmt: str, *, table_name: str, cluster: str, zk_prefix: str
) -> str:
    """Apply the prod-mode rewrites to a single statement. Pure function;
    given the same inputs, always produces the same output (so schema
    hashing in the versions table stays deterministic per-mode).

    Raises `ReplicatedRewriteError` when a CREATE TABLE statement has an
    ENGINE that's not in the supported set, or when string/comment
    metadata seems to interfere with engine detection. Fail-closed by
    design — silent partial rewrites in replicated mode have caused
    multi-day operational incidents in other CH-adopting shops.
    """
    # 1. Insert ON CLUSTER right after the object name on CREATE/ALTER.
    #    We only do this if not already present (idempotent).
    if "ON CLUSTER" not in stmt.upper():
        # CREATE TABLE [IF NOT EXISTS] <name>
        stmt = _CREATE_TABLE_RE.sub(
            lambda m: f"{m.group(0)} ON CLUSTER '{cluster}'", stmt, count=1
        )
        # CREATE MATERIALIZED VIEW [IF NOT EXISTS] <name>
        stmt = _CREATE_MV_RE.sub(
            lambda m: f"{m.group(0)} ON CLUSTER '{cluster}'", stmt, count=1
        )
        # CREATE DICTIONARY [IF NOT EXISTS] <name>
        stmt = _CREATE_DICTIONARY_RE.sub(
            lambda m: f"{m.group(0)} ON CLUSTER '{cluster}'", stmt, count=1
        )
        # ALTER TABLE <name>
        stmt = _ALTER_TABLE_RE.sub(
            lambda m: f"{m.group(0)} ON CLUSTER '{cluster}'", stmt, count=1
        )

    # 2. Rewrite engine. Only one ENGINE = per statement in our schema files.
    def _swap(m: re.Match) -> str:
        engine = m.group("name")
        args = m.group("args") or ""
        # Strip outer parens for clean concatenation; if args present like
        # "(_version, is_deleted)", we want "_version, is_deleted" inside
        # the new constructor.
        inner = args.strip()
        if inner.startswith("(") and inner.endswith(")"):
            inner = inner[1:-1].strip()
        zk_path = f"{zk_prefix}/{{shard}}/{table_name}"
        new_args_parts = [f"'{zk_path}'", "'{replica}'"]
        if inner:
            new_args_parts.append(inner)
        new_args = ", ".join(new_args_parts)
        return f"{m.group('lead')}{m.group('indent')}ENGINE = Replicated{engine}({new_args})"

    new_stmt, n = _ENGINE_RE.subn(_swap, stmt, count=1)
    if n == 0:
        # No supported engine matched. If this was a CREATE TABLE / MV,
        # that's a hard failure in replicated mode — we'd otherwise apply
        # an un-replicated table to a production cluster.
        upper = stmt.upper()
        is_create = any(k in upper for k in _KIND_REQUIRES_ENGINE)
        if is_create:
            # Strip the ON CLUSTER we just injected from the error message
            # so the operator sees the original statement they wrote.
            preview = stmt[:240].replace("\n", " ")
            raise ReplicatedRewriteError(
                f"replicated mode: CREATE statement for '{table_name}' has no "
                f"recognised engine to rewrite. Supported: ReplacingMergeTree, "
                f"AggregatingMergeTree, SummingMergeTree, MergeTree. If you're "
                f"adding a new engine (CollapsingMergeTree, VersionedCollapsingMergeTree, "
                f"etc.), extend _ENGINE_RE in apply_schema_rewriter.py and add a unit "
                f"test before running --replicated against production. Statement preview: "
                f"{preview}"
            )
        # For non-CREATE (i.e., ALTER) statements, missing engine is normal.
    return new_stmt
