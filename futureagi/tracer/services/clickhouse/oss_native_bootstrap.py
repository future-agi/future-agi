"""Fresh-only OSS native CREATEs, using fixed packaged declarations.

The caller owns an authorized, database-bound client, transport deadlines and an
exclusive schema window with ALL native writers stopped. The injected transport
must disable automatic retries/redirects as well. This module does not
connect, select an environment, initialize PG/CDC, or run historical migrations.
CH_DATABASE/CH25_DATABASE/FI_CH_DATABASE alignment is a caller prerequisite.

Complete compatible retained layouts are inspect-only. An incomplete selected
layout must have no physical rows in ANY selected existing table: creating empty
rollups/remaps cannot reconstruct history. Partial CREATEs survive failure; there
is no retry, rollback, POPULATE, receipt insertion, or existing-object mutation.

Existing table compatibility reuses the CDC functional column/engine/key/TTL
contract, NOT performance equivalence of codecs/indexes/projections/settings.
Fresh CREATEs retain those packaged declarations. Live CH25 SQL canonicalization,
MV result types, dictionary loading and ingest remain separate qualification.
"""

from __future__ import annotations

import re
from pathlib import Path

from tracer.services.clickhouse import oss_cdc_bootstrap as core

_ROOT = Path(__file__).with_name("v2") / "schema"
_READ = {"readonly": 1, "max_threads": 1, "max_execution_time": 5}
# (filename, exact statement count, selected index, exact declaration prefix).
# No glob, operator SQL/path, generic ALTER interpreter, or legacy file receipts.
_TABLES = {
    "traces": ("015_traces_and_trace_dict.sql", 3, 0),
    "end_users": ("017_end_users.sql", 2, 0),
    "trace_sessions": ("018_trace_sessions.sql", 2, 0),
    "end_user_id_remap": ("019_id_remap.sql", 2, 0),
    "trace_session_id_remap": ("019_id_remap.sql", 2, 1),
}
_ROLLUPS = {
    "spans_per_session": ("008_per_session_rollup.sql", 2, 0),
    "spans_hourly_rollup": ("010_hourly_downsample.sql", 2, 0),
    "trace_count_rollup": ("012_trace_name_and_count_rollup.sql", 5, 3),
}
_BACKFILL = {
    "spans_v2_dead_letter": ("003_dead_letter.sql", 1, 0),
    "backfill_checkpoints": ("006_backfill_checkpoints.sql", 1, 0),
}
_EXTRAS = (
    (
        "007_additional_projections.sql",
        6,
        0,
        "PROJECTION",
        "proj_metrics_hourly_by_project",
    ),
    (
        "007_additional_projections.sql",
        6,
        1,
        "PROJECTION",
        "proj_metrics_hourly_by_obs_type",
    ),
    (
        "007_additional_projections.sql",
        6,
        2,
        "PROJECTION",
        "proj_metrics_hourly_by_model",
    ),
    ("012_trace_name_and_count_rollup.sql", 5, 2, "INDEX", "idx_trace_name"),
    ("021_session_and_user_projections.sql", 4, 0, "PROJECTION", "proj_by_session"),
    ("021_session_and_user_projections.sql", 4, 1, "PROJECTION", "proj_by_end_user"),
    ("022_attr_value_bloom_indexes.sql", 2, 0, "INDEX", "idx_attrs_str_values"),
    ("022_attr_value_bloom_indexes.sql", 2, 1, "INDEX", "idx_attrs_num_values"),
    ("023_attr_value_ngram_index.sql", 1, 0, "INDEX", "idx_attrs_str_ngram"),
    ("024_spans_created_at_index.sql", 1, 0, "INDEX", "auto_minmax_index_created_at"),
)


class NativeBootstrapError(ValueError):
    """Fail closed; errors intentionally omit transport payloads/credentials."""


def _clean(sql):
    # Preserve raw strings/identifier spelling, unlike token-joining SQL.
    return core._TOKEN.sub(
        lambda m: "\n" if m[0].startswith(("--", "/*")) else m[0], sql
    ).strip()


def _statement(spec, prefix):
    filename, count, index = spec
    statements = core.apply_schema.split_statements((_ROOT / filename).read_text())
    if len(statements) != count:
        raise NativeBootstrapError("packaged native statement count changed")
    sql = _clean(statements[index])
    tokens = core._TOKEN.findall(sql)
    # The shared splitter can append the delimiter to a trailing inline comment
    # (002's last SETTINGS line). Comment removal then removes that delimiter.
    if ";" in (tokens[:-1] if tokens[-1:] == [";"] else tokens):
        raise NativeBootstrapError("expected one packaged native statement")
    if tokens[-1:] != [";"]:
        sql += ";"
    match = re.match(re.escape(prefix) + r"(?=\s|\()", sql)
    if match is None:
        raise NativeBootstrapError("packaged native selector changed")
    return sql


def _table_parts(sql):
    """Raw slices of a fixed CREATE body; only balanced delimiters/commas."""
    matches = list(core._TOKEN.finditer(sql))
    first = next(i for i, m in enumerate(matches) if m[0] == "(")
    start, stack, entries = matches[first].end(), ["("], []
    for match in matches[first + 1 :]:
        token = match[0]
        if token in ("(", "["):
            stack.append(token)
        elif token in (")", "]"):
            if not stack or stack.pop() != ("(" if token == ")" else "["):
                raise NativeBootstrapError("unbalanced native declaration")
            if not stack:
                entries.append(sql[start : match.start()].strip())
                return sql[: matches[first].start()], entries, sql[match.end() :]
        elif token == "," and len(stack) == 1:
            entries.append(sql[start : match.start()].strip())
            start = match.end()
    raise NativeBootstrapError("unterminated native declaration")


def _without_ttl(sql):
    # 020 removes TTLs. Fold that final state into NEW CREATEs only.
    head, entries, tail = _table_parts(sql)
    if "TTL" in core._clauses(core._tokens(tail)):
        tail, count = re.subn(r"\bTTL\b[\s\S]*?(?=\bSETTINGS\b)", "", tail)
        if count != 1:
            raise NativeBootstrapError("unexpected packaged TTL clause")
    return head + "(\n" + ",\n".join(entries) + "\n)" + tail


def _spans(database):
    sql = _statement(("002_spans_v2.sql", 2, 0), "CREATE TABLE IF NOT EXISTS spans")
    head, entries, tail = _table_parts(sql)
    overrides = dict(core._SPAN_COLUMNS)
    overrides["attributes_extra"] += " CODEC(ZSTD(3))"
    if overrides["trace_name"].count("'trace_dict'") != 1:
        raise NativeBootstrapError("packaged trace-name dictionary changed")
    overrides["trace_name"] = overrides["trace_name"].replace(
        "'trace_dict'", f"'{database}.trace_dict'"
    )
    entries = [entry for entry in entries if core._tokens(entry)[0] not in overrides]
    entries.extend(f"{name} {declaration}" for name, declaration in overrides.items())
    for filename, count, index, kind, name in _EXTRAS:
        # Whitespace differs in the fixed files; normalize only the prefix.
        statements = core.apply_schema.split_statements((_ROOT / filename).read_text())
        if len(statements) != count:
            raise NativeBootstrapError("packaged native extra count changed")
        extra = _clean(statements[index])
        match = re.match(
            rf"ALTER\s+TABLE spans\s+ADD {kind} IF NOT EXISTS {name}(?=\s|\()", extra
        )
        if match is None or ";" in core._TOKEN.findall(extra)[:-1]:
            raise NativeBootstrapError("packaged native extra selector changed")
        entries.append(f"{kind} {name}" + extra[match.end() :].removesuffix(";"))
    names = [
        core._tokens(entry)[:2]
        if entry.startswith(("INDEX ", "PROJECTION "))
        else core._tokens(entry)[:1]
        for entry in entries
    ]
    if len(set(names)) != len(names):
        raise NativeBootstrapError("duplicate native declaration")
    return _without_ttl(head + "(\n" + ",\n".join(entries) + "\n)" + tail)


def _qualify_reference(sql, keyword, name, database):
    # Locate exact fixed identifiers, never text inside strings/comments. Do not
    # strip an existing qualifier, including one pointing at a foreign database.
    matches = list(core._TOKEN.finditer(sql))
    refs = [
        matches[i + 1]
        for i, match in enumerate(matches[:-2])
        if match[0] == keyword
        and matches[i + 1][0] == name
        and matches[i + 2][0] != "."
    ]
    if len(refs) != 1:
        raise NativeBootstrapError("unexpected native MV source/destination")
    ref = refs[0]
    return sql[: ref.start()] + f"{database}.{name}" + sql[ref.end() :]


def native_definitions(
    database, *, include_backfill=False, include_dashboard_rollup=False
):
    """Ordered fresh CREATEs. Backfill is legacy PG-to-spans, NOT catalog repair.

    Dashboard opt-in only provisions its two objects; it does not change routing
    or claim coverage. No native eval logger or legacy lifecycle catalog is made.
    """
    if (
        not isinstance(database, str)
        or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,127}", database)
        or database.lower() in {"system", "information_schema"}
        or type(include_backfill) is not bool
        or type(include_dashboard_rollup) is not bool
    ):
        raise NativeBootstrapError(
            "explicit native database and boolean options required"
        )
    tables = dict(_TABLES)
    if include_backfill:
        tables.update(_BACKFILL)
    result = {
        name: _without_ttl(_statement(spec, f"CREATE TABLE IF NOT EXISTS {name}"))
        for name, spec in tables.items()
    }
    for table, name in (
        ("traces", "trace_dict"),
        ("end_users", "end_users_dict"),
        ("trace_sessions", "trace_sessions_dict"),
    ):
        filename, count, _ = _TABLES[table]
        prefix = (
            "CREATE OR REPLACE DICTIONARY"
            if table == "traces"
            else "CREATE DICTIONARY IF NOT EXISTS"
        )
        result[name] = _statement((filename, count, 1), f"{prefix} {name}").replace(
            "CREATE OR REPLACE DICTIONARY", "CREATE DICTIONARY IF NOT EXISTS", 1
        )
    result["spans"] = _spans(database)
    rollups = dict(_ROLLUPS)
    if include_dashboard_rollup:
        rollups["dashboard_attr_rollup"] = ("021_dashboard_attr_rollup.sql", 2, 0)
    for name, spec in rollups.items():
        result[name] = _without_ttl(
            _statement(spec, f"CREATE TABLE IF NOT EXISTS {name}")
        )
    for name, (filename, count, index) in rollups.items():
        sql = _statement(
            (filename, count, index + 1),
            f"CREATE MATERIALIZED VIEW IF NOT EXISTS {name}_mv",
        )
        sql = _qualify_reference(sql, "FROM", "spans", database)
        sql = _qualify_reference(sql, "TO", name, database)
        result[name + "_mv"] = sql
    for name, sql in result.items():
        kind = (
            "MATERIALIZED VIEW"
            if name.endswith("_mv")
            else "DICTIONARY"
            if name.endswith("_dict")
            else "TABLE"
        )
        prefix = f"CREATE {kind} IF NOT EXISTS {name}"
        if not sql.startswith(prefix) or ";" in core._TOKEN.findall(sql)[:-1]:
            raise NativeBootstrapError("expected additive native CREATE")
        result[name] = sql.replace(
            prefix, f"CREATE {kind} IF NOT EXISTS {database}.{name}", 1
        )
        if kind == "TABLE":
            core._table(result[name])
    return result


def _rows(client, sql, parameters=None, width=None):
    rows = client.query(
        sql, parameters=parameters or {}, settings=dict(_READ)
    ).result_rows
    if not isinstance(rows, (list, tuple)) or any(
        not isinstance(row, (list, tuple)) or (width is not None and len(row) != width)
        for row in rows
    ):
        raise NativeBootstrapError("invalid native metadata response")
    return [tuple(row) for row in rows]


def _identity(sql, name, kind, database):
    tokens = core._tokens(sql)
    prefix = ("CREATE", *kind.split())
    if tokens[: len(prefix)] != prefix:
        raise NativeBootstrapError(f"{name}: incompatible CREATE kind")
    rest = tokens[len(prefix) :]
    if rest[:3] == ("IF", "NOT", "EXISTS"):
        rest = rest[3:]
    if rest[:3] == (database, ".", name):
        return rest[3:]
    if rest[:1] == (name,) and rest[1:2] != (".",):
        return rest[1:]
    raise NativeBootstrapError(f"{name}: incompatible CREATE identity")


def _mv_select(sql):
    depth = 0
    for match in core._TOKEN.finditer(sql):
        token = match[0]
        if token == "AS" and depth == 0:
            result = sql[match.end() :].strip().removesuffix(";").rstrip()
            if core._tokens(result)[:1] != ("SELECT",) or ";" in core._TOKEN.findall(
                result
            ):
                break
            return result
        depth += (token in ("(", "[")) - (token in (")", "]"))
    raise NativeBootstrapError("invalid native MV SELECT")


def _check_mv(client, name, row, ddl, database):
    if row[0] != "MaterializedView":
        raise NativeBootstrapError(f"{name}: expected MaterializedView")
    rest = _identity(row[-1], name, "MATERIALIZED VIEW", database)
    target = name.removesuffix("_mv")
    if rest[:4] != ("TO", database, ".", target) or rest[4:5] not in (("(",), ("AS",)):
        raise NativeBootstrapError(f"{name}: incompatible MV destination")
    actual, expected = _mv_select(row[-1]), _mv_select(ddl)
    if actual != expected:
        rows = _rows(
            client,
            "SELECT formatQuerySingleLine(%(actual)s), formatQuerySingleLine(%(expected)s)",
            {"actual": actual, "expected": expected},
            2,
        )
        if (
            len(rows) != 1
            or any(not isinstance(x, str) or not x for x in rows[0])
            or rows[0][0] != rows[0][1]
        ):
            raise NativeBootstrapError(f"{name}: incompatible MV SELECT")


def _inspect(client, database, definitions):
    if _rows(client, "SELECT currentDatabase()", width=1) != [(database,)]:
        raise NativeBootstrapError("native client database does not match target")
    if _rows(
        client,
        "SELECT DISTINCT policy_name FROM system.storage_policies WHERE policy_name = 'tiered'",
        width=1,
    ) != [("tiered",)]:
        raise NativeBootstrapError("native tiered storage policy required")
    params = {"database": database, "names": tuple(definitions)}
    rows = _rows(
        client,
        "SELECT name, engine, engine_full, partition_key, sorting_key, primary_key, create_table_query "
        "FROM system.tables WHERE database = %(database)s AND name IN %(names)s",
        params,
        7,
    )
    tables = {}
    for name, *row in rows:
        if (
            not isinstance(name, str)
            or name not in definitions
            or name in tables
            or any(not isinstance(x, str) for x in row)
        ):
            raise NativeBootstrapError("unexpected/duplicate native table metadata")
        tables[name] = tuple(row)
    columns = {name: {} for name in tables}
    for name, column, *shape in _rows(
        client,
        "SELECT table, name, type, default_kind, default_expression FROM system.columns "
        "WHERE database = %(database)s AND table IN %(names)s ORDER BY table, position",
        params,
        5,
    ):
        if (
            any(not isinstance(x, str) for x in (name, column, *shape))
            or name not in tables
            or not column
            or column in columns[name]
        ):
            raise NativeBootstrapError("unexpected/duplicate native column metadata")
        columns[name][column] = tuple(shape)
    missing = tuple(name for name in definitions if name not in tables)
    for name, row in tables.items():
        ddl = definitions[name]
        if name.endswith("_mv"):
            if "spans" not in tables or name.removesuffix("_mv") not in tables:
                raise NativeBootstrapError(f"{name}: missing native MV prerequisite")
            _check_mv(client, name, row, ddl, database)
        elif name.endswith("_dict"):
            source = {
                "trace_dict": "traces",
                "end_users_dict": "end_users",
                "trace_sessions_dict": "trace_sessions",
            }[name]
            if source not in tables:
                raise NativeBootstrapError(f"{name}: missing native dictionary source")
            _identity(row[-1], name, "DICTIONARY", database)
            try:
                core._check_dependent(name, row, ddl)
            except core.BootstrapError:
                raise NativeBootstrapError(
                    f"{name}: incompatible native dictionary"
                ) from None
        else:
            _identity(row[-1], name, "TABLE", database)
            try:
                core._check_table(
                    name, row, columns[name], {}, ddl, database=database, native=True
                )
            except core.BootstrapError:
                raise NativeBootstrapError(
                    f"{name}: incompatible native table contract"
                ) from None
    # Do not start partial initialization over retained rows (including tombstones).
    if missing:
        for name, row in tables.items():
            if row[0] in {"ReplacingMergeTree", "AggregatingMergeTree", "MergeTree"}:
                if _rows(client, f"SELECT 1 FROM {database}.{name} LIMIT 1", width=1):
                    raise NativeBootstrapError(
                        "incomplete native layout contains retained rows"
                    )
    if "spans" in tables:
        for name, ddl in definitions.items():
            if not name.endswith("_mv"):
                continue
            result = client.query(
                f"SELECT * FROM (\n{_mv_select(ddl)}\n) LIMIT 0", settings=dict(_READ)
            )
            header = tuple(
                zip(
                    result.column_names,
                    (t.name for t in result.column_types),
                    strict=True,
                )
            )
            target_columns = core._table(definitions[name.removesuffix("_mv")])[0]

            # LowCardinality(String) is a lossless target encoding, not a different
            # value domain (the optional ARRAY JOIN yields ordinary String).
            def insertion_type(tokens):
                return (
                    ("String",)
                    if tokens == ("LowCardinality", "(", "String", ")")
                    else tokens
                )

            actual_types = {n: insertion_type(core._tokens(t)) for n, t in header}
            target_types = {
                n: insertion_type(shape[0]) for n, shape in target_columns.items()
            }
            # WHERE excludes NULL values but CH keeps Nullable(UUID) in the
            # inferred SELECT header. Only this fixed, null-filtered identity
            # may be inserted into its UUID key; never unwrap aggregate states
            # or permit nullable payloads to acquire default values.
            nonnull_session_suffix = core._tokens(
                "WHERE is_deleted = 0 AND trace_session_id IS NOT NULL "
                "GROUP BY project_id, trace_session_id"
            )
            if (
                name == "spans_per_session_mv"
                and actual_types.get("trace_session_id")
                == ("Nullable", "(", "UUID", ")")
                and target_types.get("trace_session_id") == ("UUID",)
                and core._tokens(_mv_select(ddl))[-len(nonnull_session_suffix) :]
                == nonnull_session_suffix
            ):
                actual_types["trace_session_id"] = ("UUID",)
            if (
                result.result_rows not in ([], ())
                or not header
                or len(dict(header)) != len(header)
                or actual_types != target_types
            ):
                raise NativeBootstrapError(
                    f"{name}: incompatible inferred aggregate header"
                )
            if name in tables:
                core._check_view_columns(name, columns[name], header)
    return missing


def inspect_native(
    client, *, database, include_backfill=False, include_dashboard_rollup=False
):
    """SELECT-only full preflight; return missing names in dependency order."""
    try:
        definitions = native_definitions(
            database,
            include_backfill=include_backfill,
            include_dashboard_rollup=include_dashboard_rollup,
        )
        return _inspect(client, database, definitions)
    except NativeBootstrapError:
        raise
    except Exception:
        raise NativeBootstrapError(
            "native inspection failed; no repair attempted"
        ) from None


def bootstrap_native(
    client, *, database, include_backfill=False, include_dashboard_rollup=False
):
    """Preflight, CREATE absent objects once, postflight; return created names.

    Failure is terminal for this invocation. A caller must inspect partial state
    before an explicitly requested later invocation, not retry uncertain writes.
    """
    try:
        definitions = native_definitions(
            database,
            include_backfill=include_backfill,
            include_dashboard_rollup=include_dashboard_rollup,
        )
        missing = _inspect(client, database, definitions)
        for name in missing:
            try:
                client.command(definitions[name])
            except Exception:
                raise NativeBootstrapError(
                    f"native CREATE failed at {name}; partial state retained, no retry"
                ) from None
            # Empty-server preflight cannot infer SELECT types before spans
            # exists. Verify that newly materialized prerequisite and all target
            # mappings before attaching any rollup/MV to future inserts.
            if name == "spans" and "spans" in _inspect(client, database, definitions):
                raise NativeBootstrapError(
                    "spans CREATE not visible; no rollup/MV creation attempted"
                )
        if missing and _inspect(client, database, definitions):
            raise NativeBootstrapError(
                "native postflight incomplete; partial state retained"
            )
        return missing
    except NativeBootstrapError:
        raise
    except Exception:
        raise NativeBootstrapError(
            "native bootstrap failed; partial state retained, no retry"
        ) from None
