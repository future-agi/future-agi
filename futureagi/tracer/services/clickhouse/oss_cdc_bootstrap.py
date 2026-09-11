"""Bounded OSS CDC schema core; no CLI, connections, PeerDB calls or PG writes.

The caller owns target authorization, transport and whole-job deadlines, an
exclusive bootstrap window, and a fresh, complete mirror inventory for this
database. Never supply an empty inventory on inspection failure. Existing mirrors
are NOT created, changed, or qualified here.
Only the fixed CDC CREATEs and the two named additive upgrades can be executed.

The small declaration reader below reads our packaged CREATE grammar, not arbitrary
migrations. Unknown syntax/physical shapes fail closed. SQL formatting/transport
compatibility and fresh-install readiness still require separate live qualification.
The native gate covers the four writer tables and three dictionaries, not every
native rollup, projection, codec or storage setting. There is no distributed lock
or transactional DDL: retain partial results on error; inspect before a later run.
View headers come from the server's canonical SELECT analysis with LIMIT 0, not
from existing view metadata. The five-second server setting is NOT a transport
deadline; dictionary analysis/loading can still perform read work with LIMIT 0.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tracer.services.clickhouse import oss_cdc_upgrade as upgrade
from tracer.services.clickhouse import schema
from tracer.services.clickhouse.oss_cdc_inventory import PeerTarget, inspect_mappings
from tracer.services.clickhouse.oss_cdc_source import SourceInventory, SourceTable
from tracer.services.clickhouse.v2 import apply_schema

# Supported CDC destinations; never use get_all_schema_ddl or POST_DDL_ALTERS.
LANDING = {
    "tracer_trace": schema.CDC_TRACE,
    "tracer_eval_logger": schema.CDC_EVAL_LOGGER,
    "trace_annotation": schema.CDC_TRACE_ANNOTATION,
    "model_hub_score": schema.CDC_MODEL_HUB_SCORE,
    "trace_session": schema.CDC_TRACE_SESSION,
    "model_hub_dataset": schema.CDC_MODEL_HUB_DATASET,
    "model_hub_column": schema.CDC_MODEL_HUB_COLUMN,
    "model_hub_row": schema.CDC_MODEL_HUB_ROW,
    "model_hub_cell": schema.CDC_MODEL_HUB_CELL,
    "simulate_test_execution": schema.CDC_SIMULATE_TEST_EXECUTION,
    "simulate_call_execution": schema.CDC_SIMULATE_CALL_EXECUTION,
    "usage_apicalllog": schema.CDC_USAGE_APICALLLOG,
    "simulate_scenarios": schema.CDC_SIMULATE_SCENARIOS,
    "simulate_agent_definition": schema.CDC_SIMULATE_AGENT_DEFINITION,
    "simulate_agent_version": schema.CDC_SIMULATE_AGENT_VERSION,
    "simulate_run_test": schema.CDC_SIMULATE_RUN_TEST,
    "model_hub_promptversion": schema.CDC_MODEL_HUB_PROMPTVERSION,
    "model_hub_prompttemplate": schema.CDC_MODEL_HUB_PROMPTTEMPLATE,
    "model_hub_promptlabel": schema.CDC_MODEL_HUB_PROMPTLABEL,
    "tracer_enduser": schema.CDC_TRACER_ENDUSER,
}
DEPENDENT = {
    "prompt_dict": schema.PROMPT_DICT,
    "prompt_label_dict": schema.PROMPT_LABEL_DICT,
    "column_dict": schema.COLUMN_DICT,
    "dataset_dict": schema.DATASET_DICT,
    "dataset_cells": schema.DATASET_CELLS_VIEW,
    "simulate_scenario_dict": schema.SIMULATE_SCENARIO_DICT,
    "simulate_agent_dict": schema.SIMULATE_AGENT_DICT,
    "simulate_version_dict": schema.SIMULATE_VERSION_DICT,
    "simulate_run_test_dict": schema.SIMULATE_RUN_TEST_DICT,
    "simulate_test_execution_dict": schema.SIMULATE_TEST_EXECUTION_DICT,
    "simulate_calls": schema.SIMULATE_CALLS_VIEW,
}
# Fixed transitive prerequisites, not a dependency discovery/inference framework.
_VIEW_PREREQUISITES = {
    "dataset_cells": (
        "model_hub_cell",
        "model_hub_column",
        "model_hub_dataset",
        "column_dict",
        "dataset_dict",
    ),
    "simulate_calls": (
        "simulate_call_execution",
        "simulate_scenarios",
        "simulate_agent_definition",
        "simulate_agent_version",
        "simulate_scenario_dict",
        "simulate_agent_dict",
        "simulate_version_dict",
    ),
}
_ADDITIONS = frozenset(
    key for columns in upgrade.MIGRATIONS.values() for key in columns
)
# Older CREATEs declared these fields even though PG never supplied them. Only
# validate them when already present; never add, drop or change them on upgrade.
_RETAINED_SESSION_COLUMNS = {
    "external_id": "Nullable(String)",
    "end_user_id": "Nullable(UUID)",
    "status": "LowCardinality(Nullable(String))",
    "attributes": "String DEFAULT '{}'",
    "started_at": "Nullable(DateTime64(3))",
}
_NATIVE_FILES = {
    "spans": "002_spans_v2.sql",
    "traces": "015_traces_and_trace_dict.sql",
    "end_users": "017_end_users.sql",
    "trace_sessions": "018_trace_sessions.sql",
}
# Final native column overrides/additions from 002/005/013/014/015. Inspection
# data only: deliberately not an ALTER interpreter or a native apply path.
_SPAN_COLUMNS = {
    "attributes_extra": "String DEFAULT '{}'",
    "trace_name": "String MATERIALIZED ifNull(dictGetOrDefault('trace_dict', 'name', toUUID(trace_id), ''), '')",
    "_peerdb_is_deleted": "UInt8 ALIAS is_deleted",
    "llm_request_model": "LowCardinality(String) MATERIALIZED attrs_string['gen_ai.request.model']",
    "llm_response_model": "LowCardinality(String) MATERIALIZED attrs_string['gen_ai.response.model']",
    "llm_finish_reason": "LowCardinality(String) MATERIALIZED attrs_string['gen_ai.response.finish_reason']",
    "embedding_model": "LowCardinality(String) MATERIALIZED attrs_string['llm.embedding.model']",
    "streaming": "Nullable(UInt8) MATERIALIZED if(mapContains(attrs_bool, 'streaming'), attrs_bool['streaming'], NULL)",
    "temperature": "Nullable(Float64) MATERIALIZED if(mapContains(attrs_number, 'gen_ai.request.temperature'), attrs_number['gen_ai.request.temperature'], NULL)",
    "top_p": "Nullable(Float64) MATERIALIZED if(mapContains(attrs_number, 'gen_ai.request.top_p'), attrs_number['gen_ai.request.top_p'], NULL)",
    "max_tokens": "Nullable(Int32) MATERIALIZED if(mapContains(attrs_number, 'gen_ai.request.max_tokens'), toInt32(attrs_number['gen_ai.request.max_tokens']), NULL)",
}
_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS schema_versions (
    filename String, sha256 FixedString(64),
    applied_at DateTime64(3, 'UTC') DEFAULT now64(3, 'UTC'),
    applied_by String DEFAULT '', notes String DEFAULT ''
) ENGINE = MergeTree ORDER BY (filename, applied_at)
"""


class BootstrapError(ValueError):
    """Unknown/incomplete physical contract; no automatic repair or cleanup."""


@dataclass(frozen=True)
class MirrorInventory:
    """Caller-attested complete destination inventory, not a peer health witness."""

    database: str
    # Exact (mirror name, unqualified destination table) pairs in this database.
    mappings: tuple[tuple[str, str], ...]
    source: PeerTarget | None = None

    @classmethod
    def from_peerdb(cls, request, *, source: PeerTarget, destination: PeerTarget):
        """Inspect effective mappings; transport/validation errors propagate."""
        return cls(
            destination.database,
            inspect_mappings(request, source=source, destination=destination),
            source,
        )


@dataclass(frozen=True)
class Inspection:
    missing: tuple[str, ...]
    upgrade_recorded: bool
    ledger_present: bool


@dataclass(frozen=True)
class BootstrapResult:
    created: tuple[str, ...]
    upgrade_applied: bool


# Preserve quoted text, token boundaries and case; only whitespace/comments and
# identifier quoting are irrelevant. This deliberately is not SQL equivalence.
_TOKEN = re.compile(
    r"'(?:\\.|''|[^'\\])*'|`(?:``|[^`])*`|--[^\n]*|/\*[\s\S]*?\*/|"
    r"[A-Za-z_][A-Za-z_0-9]*|[0-9]+(?:\.[0-9]+)?|[^\s]"
)


def _tokens(sql: str) -> tuple[str, ...]:
    return tuple(
        token[1:-1] if token.startswith("`") else token
        for token in _TOKEN.findall(sql)
        if not token.startswith(("--", "/*")) and token != ";"
    )


def _groups(tokens: tuple[str, ...]) -> list[tuple[str, ...]]:
    """Comma-separated declarations/arguments, respecting strings and nesting."""
    result, start, stack = [], 0, []
    for i, token in enumerate(tokens):
        if token in ("(", "["):
            stack.append(token)
        elif token in (")", "]"):
            if not stack or stack.pop() != ("(" if token == ")" else "["):
                raise BootstrapError("unbalanced packaged declaration")
        elif token == "," and not stack:
            result.append(tokens[start:i])
            start = i + 1
    if stack:
        raise BootstrapError("unbalanced packaged declaration")
    result.append(tokens[start:])
    return result


def _body(tokens: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    start = tokens.index("(")
    depth = 0
    for i in range(start, len(tokens)):
        depth += (tokens[i] == "(") - (tokens[i] == ")")
        if depth == 0:
            return tokens[start + 1 : i], tokens[i + 1 :]
    raise BootstrapError("unterminated packaged CREATE")


def _column(tokens: tuple[str, ...]) -> tuple[tuple[str, ...], str, tuple[str, ...]]:
    stop = next(
        (
            i
            for i, t in enumerate(tokens)
            if t in {"DEFAULT", "MATERIALIZED", "ALIAS", "CODEC"}
        ),
        len(tokens),
    )
    kind = tokens[stop] if stop < len(tokens) and tokens[stop] != "CODEC" else ""
    expr = tokens[stop + 1 :] if kind else ()
    if "CODEC" in expr:
        expr = expr[: expr.index("CODEC")]
    return tokens[:stop], kind, expr


def _clauses(tokens: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    """Only top-level clauses used in the fixed packaged CREATEs."""
    starts, depth = [], 0
    for i, token in enumerate(tokens):
        if depth == 0 and token in {
            "ENGINE",
            "PARTITION",
            "ORDER",
            "PRIMARY",
            "TTL",
            "SETTINGS",
            "SOURCE",
            "LAYOUT",
            "LIFETIME",
            "AS",
        }:
            starts.append((i, token))
        depth += (token in ("(", "[")) - (token in (")", "]"))
    result = {}
    for n, (i, key) in enumerate(starts):
        if key in result:
            raise BootstrapError(f"duplicate CREATE clause: {key}")
        value = tokens[i + 1 : starts[n + 1][0] if n + 1 < len(starts) else len(tokens)]
        if value and value[0] in ("=", "BY", "KEY"):
            value = value[1:]
        result[key] = value
    return result


def _key(tokens: tuple[str, ...]) -> tuple[str, ...]:
    if tokens[:1] == ("(",) and _body(tokens)[1] == ():
        return tokens[1:-1]
    return tokens


def _table(ddl: str):
    body, tail = _body(_tokens(ddl))
    columns, indexes = {}, {}
    for entry in _groups(body):
        if not entry:
            raise BootstrapError("empty packaged declaration")
        if entry[0] == "PROJECTION":
            continue  # native projections are outside this CDC prerequisite gate
        if entry[0] == "INDEX":
            if entry[1] in indexes:
                raise BootstrapError(f"duplicate packaged index: {entry[1]}")
            indexes[entry[1]] = entry[2:]
        else:
            if entry[0] in columns:
                raise BootstrapError(f"duplicate packaged column: {entry[0]}")
            columns[entry[0]] = _column(entry[1:])
    return columns, indexes, _clauses(tail)


def _definitions(database: str) -> tuple[dict[str, str], dict[str, str]]:
    for name in (database, schema._CH_DATABASE):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,127}", name):
            raise BootstrapError("database must be an explicit safe identifier")
    if database.lower() in {"system", "information_schema"}:
        raise BootstrapError("system database is not an OSS CDC target")
    create = {name: schema._to_single_node_engine(ddl) for name, ddl in LANDING.items()}
    create.update(
        {
            name: ddl.replace(f"{schema._CH_DATABASE}.", f"{database}.").replace(
                f"DB '{schema._CH_DATABASE}'", f"DB '{database}'"
            )
            for name, ddl in DEPENDENT.items()
        }
    )
    # ClickHouse persists resolved view references. Bind only our fixed packaged
    # dependencies before CREATE/comparison; never strip a stored foreign scope.
    for name, dependencies in _VIEW_PREREQUISITES.items():
        for dependency in dependencies:
            create[name] = (
                create[name]
                .replace(f"'{dependency}'", f"'{database}.{dependency}'")
                .replace(f"FROM {dependency} AS ", f"FROM {database}.{dependency} AS ")
            )
    for name, ddl in create.items():
        kind = (
            "TABLE"
            if name in LANDING
            else "VIEW"
            if name in _VIEW_PREREQUISITES
            else "DICTIONARY"
        )
        if len(apply_schema.split_statements(ddl)) != 1 or _tokens(ddl)[:6] != (
            "CREATE",
            kind,
            "IF",
            "NOT",
            "EXISTS",
            name,
        ):
            raise BootstrapError(f"{name}: expected one packaged additive CREATE")
        if name in LANDING:
            # Parse absent definitions too: a bad packaged contract is not a reason
            # to start creating earlier tables and discover the error afterward.
            _table(ddl)
        elif name in _VIEW_PREREQUISITES:
            _view_select(name, ddl)
    # Read exact packaged native CREATEs for inspection only. No native statement
    # enters the executable map. Never discover/replay the native migration set.
    native = {}
    root = Path(apply_schema.__file__).with_name("schema")
    for name, filename in _NATIVE_FILES.items():
        statements = apply_schema.split_statements((root / filename).read_text())
        native[name] = statements[0]
        if name != "spans":
            native[
                {
                    "traces": "trace_dict",
                    "end_users": "end_users_dict",
                    "trace_sessions": "trace_sessions_dict",
                }[name]
            ] = statements[1]
    return create, native


def _check_table(
    name,
    row,
    columns,
    indexes,
    ddl,
    *,
    database: str,
    allow_additions=False,
    native=False,
    additions=_ADDITIONS,
    source_owned=False,
):
    expected, expected_indexes, clauses = _table(ddl)
    if name == "spans":
        expected.update(
            {key: _column(_tokens(value)) for key, value in _SPAN_COLUMNS.items()}
        )
    if name == "trace_session" and not native and not source_owned:
        expected.update(
            {
                key: _column(_tokens(value))
                for key, value in _RETAINED_SESSION_COLUMNS.items()
                if key in columns
            }
        )
    actual = {key: (_tokens(t), k, _tokens(e)) for key, (t, k, e) in columns.items()}
    missing = set(expected) - set(actual)
    eligible = (
        {column for table, column in additions if table == name}
        if allow_additions
        else set()
    )
    if missing - eligible or set(actual) - set(expected):
        raise BootstrapError(
            f"{name}: missing/unexpected columns {sorted(missing - eligible | (set(actual) - set(expected)))}"
        )
    for key in expected.keys() & actual.keys():
        if expected[key] != actual[key]:
            if name == "usage_apicalllog" and key == "eval_score" and not native:
                data_type, kind, expression = upgrade.MIGRATIONS[
                    "cdc_002_usage_eval_fields.sql"
                ][name, key]
                if actual[key] == (_tokens(data_type), kind, _tokens(expression)):
                    continue  # exact server spelling, not arbitrary expression equivalence
            if native and name == "spans" and key == "trace_name":
                # Only this complete native expression has a qualified equivalent.
                # database comes from the explicit target/currentDatabase check,
                # never an environment fallback or a stripped foreign qualifier.
                data_type, kind, expression = expected[key]
                qualified = tuple(
                    f"'{database}.trace_dict'" if token == "'trace_dict'" else token
                    for token in expression
                )
                if actual[key] == (data_type, kind, qualified):
                    continue
            raise BootstrapError(f"{name}.{key}: incompatible type/default expression")
    engine, engine_full, partition, sorting, primary, create_query = row
    wanted_engine = clauses["ENGINE"]
    physical_clauses = _clauses(_body(_tokens(create_query))[1])
    # MergeTree and MergeTree() have the same empty engine argument list.
    # engine_full can include the storage clauses, not just engine arguments.
    actual_engine = _clauses(("ENGINE", "=", *_tokens(engine_full)))["ENGINE"]
    if actual_engine[-2:] == ("(", ")"):
        actual_engine = actual_engine[:-2]
    if wanted_engine[-2:] == ("(", ")"):
        wanted_engine = wanted_engine[:-2]
    if engine != wanted_engine[0] or actual_engine != wanted_engine:
        raise BootstrapError(f"{name}: incompatible engine/version/tombstone")
    for label, value, wanted in (
        ("partition", partition, clauses.get("PARTITION", ())),
        ("sorting", sorting, clauses["ORDER"]),
        ("primary", primary, clauses.get("PRIMARY", clauses["ORDER"])),
    ):
        if _key(_tokens(value)) != _key(wanted):
            raise BootstrapError(f"{name}: incompatible {label} key")
    if "TTL" in physical_clauses:
        raise BootstrapError(f"{name}: unexpected TTL")
    if not native and indexes != expected_indexes:
        raise BootstrapError(f"{name}: incompatible skipping indexes")


def _source_definitions(source: SourceInventory) -> dict[str, str]:
    """Inspection-only profile from independent PG metadata, never executed.

    PeerDB owns all source columns and the ID-keyed transport layout. Only the
    fixed derived eval columns are optional before their named migration. Keep
    the application's required column names without copying its old, lossy types.
    """
    if not isinstance(source, SourceInventory) or set(source.tables) != set(LANDING):
        raise BootstrapError("complete PostgreSQL source schema required")
    result = {}
    derived = upgrade.MIGRATIONS["cdc_002_usage_eval_fields.sql"]
    for name, table in source.tables.items():
        if not isinstance(table, SourceTable):
            raise BootstrapError("invalid PostgreSQL source table profile")
        columns = dict(table.columns)
        required = {
            column
            for column in _table(LANDING[name])[0]
            if not column.startswith("_peerdb_") and (name, column) not in derived
        }
        if required - columns.keys():
            raise BootstrapError(f"{name}: missing application source columns")
        fields = [f"`{column}` {data_type}" for column, data_type in table.columns]
        fields.extend(
            f"`{column}` {data_type} {kind} {expression}"
            for (target, column), (data_type, kind, expression) in derived.items()
            if target == name
        )
        fields.extend(
            (
                "_peerdb_synced_at DateTime64(9) DEFAULT now64()",
                "_peerdb_is_deleted UInt8",
                "_peerdb_version UInt64",
            )
        )
        result[name] = (
            f"CREATE TABLE IF NOT EXISTS {name} ({', '.join(fields)}) "
            "ENGINE = ReplacingMergeTree(_peerdb_version, _peerdb_is_deleted) "
            f"ORDER BY ({', '.join(table.primary_key)})"
        )
    return result


def _check_dependent(name, row, ddl, *, client=None):
    actual = _tokens(row[-1])
    expected = _tokens(ddl)
    if "DICTIONARY" in expected[:4]:
        if row[0] != "Dictionary":
            raise BootstrapError(f"{name}: expected Dictionary")
        actual_body, actual_tail = _body(actual)
        body, tail = _body(expected)
        if _groups(actual_body) != _groups(body) or _clauses(actual_tail) != _clauses(
            tail
        ):
            raise BootstrapError(f"{name}: incompatible dictionary definition/source")
    else:
        # Token offsets locate the delimiter without changing SQL spelling.
        # In particular, `c.id` is one identifier, unlike qualified c.id.
        start = next((m.end() for m in _TOKEN.finditer(row[-1]) if m[0] == "AS"), None)
        if row[0] != "View" or start is None:
            raise BootstrapError(f"{name}: incompatible view definition")
        actual_select = row[-1][start:].strip().removesuffix(";").rstrip()
        expected_select = _view_select(name, ddl)
        if actual_select == expected_select:
            return
        if client is None:
            raise BootstrapError(f"{name}: incompatible view definition")
        # Parse/format string parameters only; never execute the stored SELECT.
        # The server adds parentheses when persisting expressions. Its formatter
        # recognizes that spelling without relaxing source/predicate identity.
        rows = client.query(
            "SELECT formatQuerySingleLine(%(actual)s), formatQuerySingleLine(%(expected)s)",
            parameters={
                "actual": actual_select,
                "expected": expected_select,
            },
            settings={"readonly": 1, "max_threads": 1, "max_execution_time": 5},
        ).result_rows
        if (
            not isinstance(rows, (tuple, list))
            or len(rows) != 1
            or not isinstance(rows[0], (tuple, list))
            or len(rows[0]) != 2
            or any(not isinstance(value, str) or not value for value in rows[0])
        ):
            raise BootstrapError(f"{name}: invalid view formatter response")
        if rows[0][0] != rows[0][1]:
            raise BootstrapError(f"{name}: incompatible view definition")


def _view_select(name: str, ddl: str) -> str:
    """Extract only the two fixed packaged CREATE VIEW ... AS SELECT bodies."""
    match = (
        re.fullmatch(
            rf"\s*CREATE VIEW IF NOT EXISTS {name} AS\s+(SELECT\b[\s\S]*?);\s*",
            ddl,
        )
        if name in _VIEW_PREREQUISITES
        else None
    )
    if match is None or ";" in _TOKEN.findall(match[1]):
        raise BootstrapError(f"{name}: expected packaged view SELECT")
    return match[1].strip()


def _require_view_prerequisites(name, missing):
    absent = sorted(set(_VIEW_PREREQUISITES[name]) & set(missing))
    if absent:
        raise BootstrapError(f"{name}: missing view prerequisites {absent}")


def _infer_view_header(client, name, ddl) -> tuple[tuple[str, str], ...]:
    """Call only after physical prerequisite checks; caller bounds transport."""
    result = client.query(
        f"SELECT * FROM (\n{_view_select(name, ddl)}\n) LIMIT 0",
        settings={"readonly": 1, "max_threads": 1, "max_execution_time": 5},
    )
    names = getattr(result, "column_names", ())
    types = getattr(result, "column_types", ())
    rows = result.result_rows
    if (
        not isinstance(names, (tuple, list))
        or not isinstance(types, (tuple, list))
        or not names
        or len(names) != len(types)
        or any(not isinstance(n, str) or not n for n in names)
        or len(set(names)) != len(names)
        or any(
            not isinstance(getattr(t, "name", None), str) or not t.name for t in types
        )
        or not isinstance(rows, (tuple, list))
        or rows
    ):
        raise BootstrapError(f"{name}: invalid inferred view header")
    return tuple((n, t.name) for n, t in zip(names, types, strict=True))


def _check_view_columns(name, columns, header):
    # system.columns is ordered by physical position; preserve wrappers/precision
    # exactly as the server supplies them. Packaged views declare no defaults.
    if tuple((n, t) for n, (t, _, _) in columns.items()) != header or any(
        (kind, expression) != ("", "") for _, kind, expression in columns.values()
    ):
        raise BootstrapError(
            f"{name}: incompatible view output names/types/order/defaults"
        )


def inspect_bootstrap(
    client,
    *,
    database: str,
    inspect_mirrors: Callable[[], MirrorInventory],
    inspect_source: Callable[[], SourceInventory] | None = None,
    require_complete=False,
) -> Inspection:
    """SELECT-only preflight; all existing objects checked before any CREATE."""
    create, native = _definitions(database)
    if client.query(
        "SELECT currentDatabase()", settings={"readonly": 1}
    ).result_rows != [(database,)]:
        raise BootstrapError("client database does not match explicit target")
    names = tuple(create) + tuple(native) + ("schema_versions",)
    params = {"names": names}
    rows = client.query(
        """SELECT name, engine, engine_full, partition_key, sorting_key, primary_key, create_table_query
        FROM system.tables WHERE database = currentDatabase() AND name IN %(names)s""",
        parameters=params,
        settings={"readonly": 1},
    ).result_rows
    tables = {}
    for name, *row in rows:
        if name not in names or name in tables:
            raise BootstrapError("unexpected/duplicate table metadata")
        tables[name] = tuple(row)
    columns = {name: {} for name in tables}
    for name, column, *shape in client.query(
        """SELECT table, name, type, default_kind, default_expression FROM system.columns
        WHERE database = currentDatabase() AND table IN %(names)s
        ORDER BY table, position""",
        parameters=params,
        settings={"readonly": 1},
    ).result_rows:
        if name not in tables or column in columns[name]:
            raise BootstrapError("unexpected/duplicate column metadata")
        columns[name][column] = tuple(shape)
    indexes = {name: {} for name in tables}
    for name, index, expr, kind, granularity in client.query(
        """SELECT table, name, expr, type_full, granularity FROM system.data_skipping_indices
        WHERE database = currentDatabase() AND table IN %(names)s""",
        parameters=params,
        settings={"readonly": 1},
    ).result_rows:
        if name not in tables or index in indexes[name]:
            raise BootstrapError("unexpected/duplicate index metadata")
        indexes[name][index] = _tokens(f"{expr} TYPE {kind} GRANULARITY {granularity}")
    inventory = inspect_mirrors()
    if (
        not isinstance(inventory, MirrorInventory)
        or inventory.database != database
        or not isinstance(inventory.mappings, tuple)
        or any(
            not isinstance(pair, tuple)
            or len(pair) != 2
            or not all(isinstance(value, str) for value in pair)
            for pair in inventory.mappings
        )
    ):
        raise BootstrapError("complete mirror inventory for exact database required")
    seen = set()
    for mirror, destination in inventory.mappings:
        if destination not in LANDING or not mirror.strip() or destination in seen:
            raise BootstrapError("unknown/duplicate mirror mapping")
        # The inventory verifies actual endpoints and public source mappings.
        # A mirror's display name is not identity; one mirror may own many tables.
        if destination not in tables:
            raise BootstrapError(
                f"{destination}: mirror exists but destination is absent"
            )
        seen.add(destination)
    if inspect_source is not None:
        source = inspect_source()
        if (
            not isinstance(source, SourceInventory)
            or inventory.source is None
            or source.source != inventory.source
            or seen != set(LANDING)
        ):
            raise BootstrapError("all source-owned mappings and exact PG peer required")
        source_definitions = _source_definitions(source)
        # No source migration may compensate for incomplete replication.
        upgrade.inspect_source_fields(client)
    else:
        source_definitions = {}
    for name, ddl in native.items():
        if name not in tables:
            raise BootstrapError(
                f"{name}: missing native prerequisite; no native replay"
            )
        if name in _NATIVE_FILES:
            _check_table(
                name,
                tables[name],
                columns[name],
                indexes[name],
                ddl,
                database=database,
                native=True,
            )
        else:
            _check_dependent(name, tables[name], ddl, client=client)
    ledger = "schema_versions" in tables
    if ledger:
        _check_table(
            "schema_versions",
            tables["schema_versions"],
            columns["schema_versions"],
            indexes["schema_versions"],
            _LEDGER_DDL,
            database=database,
        )
    recorded = apply_schema.fetch_applied(client) if ledger else {}
    eligible = set()
    for migration_name, added_columns in upgrade.MIGRATIONS.items():
        prior = recorded.get(migration_name)
        if prior is not None:
            if prior != upgrade.migration_file(migration_name).sha256:
                raise BootstrapError("named CDC migration hash drift")
            if {table for table, _ in added_columns} - tables.keys():
                raise BootstrapError("recorded upgrade has missing destinations")
        elif not require_complete and (
            inspect_source is None or migration_name != upgrade.MIGRATION_NAME
        ):
            eligible.update(added_columns)
    required_migrations = set(upgrade.MIGRATIONS)
    if inspect_source is not None:
        required_migrations.remove(upgrade.MIGRATION_NAME)
    all_recorded = required_migrations <= recorded.keys()
    for name, ddl in create.items():
        if name not in tables:
            continue
        if name in LANDING:
            _check_table(
                name,
                tables[name],
                columns[name],
                indexes[name],
                source_definitions.get(name, ddl),
                database=database,
                allow_additions=not require_complete,
                additions=eligible,
                source_owned=inspect_source is not None,
            )
        else:
            _check_dependent(name, tables[name], ddl, client=client)
    missing = tuple(name for name in create if name not in tables)
    if require_complete and (missing or not all_recorded):
        raise BootstrapError("incomplete CDC objects or named upgrade record")
    # All existing table and dictionary shapes (including native prerequisites)
    # have passed before the first inference. Never create a missing dependency
    # to make an existing view's expected header obtainable.
    for name in _VIEW_PREREQUISITES:
        if name in tables:
            _require_view_prerequisites(name, missing)
    for name in _VIEW_PREREQUISITES:
        if name in tables:
            _check_view_columns(
                name, columns[name], _infer_view_header(client, name, create[name])
            )
    return Inspection(missing, all_recorded, ledger)


def bootstrap_cdc(
    client,
    *,
    database: str,
    inspect_mirrors: Callable[[], MirrorInventory],
    applied_by: str,
    inspect_source: Callable[[], SourceInventory] | None = None,
) -> BootstrapResult:
    """One serialized apply, no retries/cleanup. Any failure stops subsequent DDL.

    With inspect_source, PeerDB-created tables are prerequisites, never CREATE or
    ALTER targets except for the four derived eval columns. No cdc001 is applied
    or recorded. Without it the separately qualified canonical path is retained.
    """
    create, _ = _definitions(database)
    before = inspect_bootstrap(
        client,
        database=database,
        inspect_mirrors=inspect_mirrors,
        inspect_source=inspect_source,
    )
    created = []
    for name in () if inspect_source is not None else LANDING:
        if name in before.missing:
            client.command(create[name])
            created.append(name)
    # IF NOT EXISTS may have raced an incompatible CREATE: inspect again before ADD.
    middle = inspect_bootstrap(
        client,
        database=database,
        inspect_mirrors=inspect_mirrors,
        inspect_source=inspect_source,
    )
    if set(middle.missing) & LANDING.keys():
        raise BootstrapError("landing CREATE did not establish its postcondition")
    # Inspect both before any ADD, so an incompatible later upgrade cannot
    # leave a newly applied earlier one behind. Do not replay native migrations.
    states = [
        upgrade.inspect_upgrade(client, migration_name=name)
        for name in upgrade.MIGRATIONS
        if inspect_source is None or name != upgrade.MIGRATION_NAME
    ]
    if any(not state.recorded for state in states) and not middle.ledger_present:
        apply_schema.ensure_versions_table(client)
    for state in states:
        if not state.recorded:
            apply_schema.apply_file(client, state.migration, applied_by)
        if not upgrade.inspect_upgrade(
            client, migration_name=state.migration.path.name, require_complete=True
        ).recorded:
            raise BootstrapError("named upgrade record missing after apply")
    for name in DEPENDENT:
        if name in middle.missing:
            if name in _VIEW_PREREQUISITES:
                # Earlier additive CREATEs may have been ignored or raced. Read
                # and verify their physical state before asking for this header.
                ready = inspect_bootstrap(
                    client,
                    database=database,
                    inspect_mirrors=inspect_mirrors,
                    inspect_source=inspect_source,
                )
                _require_view_prerequisites(name, ready.missing)
                _infer_view_header(client, name, create[name])
            client.command(create[name])
            created.append(name)
            if name in _VIEW_PREREQUISITES:
                # Fresh inference + actual physical comparison, before proceeding
                # to later CREATEs. No cached fixture/header or repair fallback.
                after = inspect_bootstrap(
                    client,
                    database=database,
                    inspect_mirrors=inspect_mirrors,
                    inspect_source=inspect_source,
                )
                if name in after.missing:
                    raise BootstrapError(
                        f"{name}: view CREATE did not establish its postcondition"
                    )
    inspect_bootstrap(
        client,
        database=database,
        inspect_mirrors=inspect_mirrors,
        require_complete=True,
        inspect_source=inspect_source,
    )
    return BootstrapResult(tuple(created), any(not state.recorded for state in states))
