"""A scenario's starting records, read-only: the seeded rows its fixture's facts name, each looked up by its key in
the world's seed files (``store.json``, else ``world.sqlite``) of the draft, else the saved version. A record the seed
does not hold reads as missing."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Literal, Protocol, TypeAlias, TypedDict, TypeVar

import structlog
from minio.error import S3Error
from urllib3.exceptions import HTTPError

from simulate.models import HostedHarnessJob
from simulate.services.harness_versioning import drafts
from simulate.services.harness_versioning.versions import suite_documents
from simulate.services.hosted_harness import HostedHarnessError

logger = structlog.get_logger(__name__)

JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)
Key: TypeAlias = str | int
Row: TypeAlias = dict[str, JsonValue]
_T = TypeVar("_T")

# Other values a row's one-line summary carries after its key.
SUMMARY_VALUES = 2
# Rows a record offers to start from instead.
CHOICES_MAX = 50
# Facts that describe the fixture itself, never a record.
_NOT_A_FACT = frozenset({"origin"})


class StartingRecord(TypedDict):
    # Where the fact sits in the fixture, one segment per level (a list position as its digits).
    path: list[str]
    label: str
    table: str
    key_field: str
    key: str
    # `seeded`: the seed holds the row. `missing`: it does not, and `note` says so.
    status: Literal["seeded", "missing"]
    # The row in one line (its key, then its first other values); null when missing.
    summary: str | None
    fields: Row | None
    note: str | None
    # Other seeded rows of the table the scenario could start from instead, the first few.
    choices: list[SeededRow]


class SeededRow(TypedDict):
    # The row's key as text, as a rework request sends it back.
    key: str
    summary: str


class ReworkRequest(TypedDict):
    """The latest request to the builder to start a scenario from another record, as its turn left it."""

    id: str
    state: Literal["pending", "waiting", "proven", "refused"]
    table: str
    key_field: str
    key: str
    # Why it was refused, in words to show; null otherwise.
    reason: str | None
    requested_at: str
    # The builder chat's message asking the person to confirm the change, while `waiting`; null otherwise.
    confirm_message_id: str | None
    # On a refusal the builder explained: its reply as written (Markdown), and that chat message's id.
    builder_reply: str | None
    reply_message_id: str | None


class RowsUnavailable(TypedDict):
    code: Literal["no_seed_rows", "files_unreadable"]
    reason: str


class StartingRecords(TypedDict):
    scenario_key: str
    origin: str | None
    records: list[StartingRecord]
    # Set when the scenario's own setup changes the world before the run, so the rows above are as seeded.
    setup_note: str | None
    rows_unavailable: RowsUnavailable | None
    rework: ReworkRequest | None


@dataclass(frozen=True)
class ScenarioFiles:
    """One scenario's document with the files the next run reads: the draft's, else the saved ones."""

    document: dict[str, JsonValue]
    suite: Mapping[str, bytes]
    environment: Mapping[str, bytes]


class _Table(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def fields(self) -> frozenset[str]: ...
    @property
    def primary(self) -> frozenset[str]: ...
    @property
    def unique(self) -> frozenset[str]: ...
    # Key fields that point at another table's row: the row is that one's, not this table's own.
    @property
    def referencing(self) -> frozenset[str]: ...

    def lookup(self, key_field: str, key: Key) -> Row | None: ...

    def others(self, key_field: str, key: Key, limit: int) -> list[Row]: ...


@dataclass(frozen=True)
class _Fact:
    path: tuple[str, ...]
    name: str
    parent: str
    value: Key


@dataclass(frozen=True)
class _Schema:
    primary: frozenset[str]
    unique: frozenset[str]
    referencing: frozenset[str]
    fields: frozenset[str]


@dataclass(frozen=True)
class _StoreTable:
    """A table of `store.json`: its rows are in the file."""

    name: str
    fields: frozenset[str]
    primary: frozenset[str]
    unique: frozenset[str]
    referencing: frozenset[str]
    rows: list[Row] = field(repr=False)

    def lookup(self, key_field: str, key: Key) -> Row | None:
        return next((row for row in self.rows if _same(row.get(key_field), key)), None)

    def others(self, key_field: str, key: Key, limit: int) -> list[Row]:
        rows = (
            row
            for row in self.rows
            if row.get(key_field) is not None and not _same(row.get(key_field), key)
        )
        return [row for _, row in zip(range(limit), rows, strict=False)]


@dataclass(frozen=True)
class _SqliteTable:
    """A table of `world.sqlite`, read by key from the database held in memory."""

    name: str
    fields: frozenset[str]
    primary: frozenset[str]
    unique: frozenset[str]
    referencing: frozenset[str]
    connection: sqlite3.Connection = field(repr=False)

    def lookup(self, key_field: str, key: Key) -> Row | None:
        if key_field not in self.fields:
            return None
        # Identifiers cannot be bound parameters; both come from the database's own catalogue and are quoted.
        found = self.connection.execute(
            f"select * from {_quoted(self.name)} where {_quoted(key_field)} = ? limit 1",
            (key,),
        ).fetchone()
        return _row(found) if found else None

    def others(self, key_field: str, key: Key, limit: int) -> list[Row]:
        if key_field not in self.fields:
            return []
        return [
            _row(found)
            for found in self.connection.execute(
                f"select * from {_quoted(self.name)} where {_quoted(key_field)} is not null "
                f"and {_quoted(key_field)} != ? limit ?",
                (key, limit),
            )
        ]


def _row(found: sqlite3.Row) -> Row:
    return {str(name): _scalar(found[name]) for name in found.keys()}


_NO_ROWS = RowsUnavailable(
    code="no_seed_rows",
    reason=(
        "This environment's world keeps no seeded rows the platform can read: it was built before they were kept, "
        "or its data lives in a running service."
    ),
)
_UNREADABLE = RowsUnavailable(
    code="files_unreadable", reason="This environment's world files can't be read."
)
_SETUP_NOTE = "This scenario's setup changes the world before each run, so the rows shown are as seeded."


def _same(value: JsonValue, key: Key) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (str, int))
        and str(value) == str(key)
    )


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _json(files: Mapping[str, bytes], name: str) -> object:
    raw = files.get(name)
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None


def _scalar(value: object) -> JsonValue:
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _schema(columns: Mapping[str, object]) -> _Schema:
    kinds = {str(name): str(kind).upper() for name, kind in columns.items()}
    return _Schema(
        primary=frozenset(
            name for name, kind in kinds.items() if "PRIMARY KEY" in kind
        ),
        unique=frozenset(name for name, kind in kinds.items() if "UNIQUE" in kind),
        referencing=frozenset(
            name for name, kind in kinds.items() if "REFERENCES" in kind
        ),
        fields=frozenset(kinds),
    )


def _store_tables(
    seeded: Mapping[str, object], schemas: Mapping[str, _Schema]
) -> list[_Table]:
    tables: list[_Table] = []
    for name, rows in sorted(seeded.items()):
        if not isinstance(rows, list):
            continue
        held = [row for row in rows if isinstance(row, dict)]
        schema = schemas.get(str(name)) or _schema({})
        tables.append(
            _StoreTable(
                name=str(name),
                fields=schema.fields
                | frozenset(str(column) for row in held for column in row),
                primary=schema.primary,
                unique=schema.unique,
                referencing=schema.referencing,
                rows=held,
            )
        )
    return tables


def _sqlite_tables(
    connection: sqlite3.Connection, schemas: Mapping[str, _Schema]
) -> list[_Table]:
    """Each table's columns and keys from the database's catalogue; no row is read until one is looked up."""
    tables: list[_Table] = []
    names = connection.execute(
        "select name from sqlite_master where type = 'table' and name not like 'sqlite_%' order by name"
    ).fetchall()
    for (name,) in names:
        info = connection.execute(
            "select name, pk from pragma_table_info(?)", (name,)
        ).fetchall()
        primary = [row["name"] for row in info if row["pk"]]
        unique = set()
        for index in connection.execute(
            'select name, "unique" from pragma_index_list(?)', (name,)
        ).fetchall():
            columns = connection.execute(
                "select name from pragma_index_info(?)", (index["name"],)
            ).fetchall()
            if index["unique"] and len(columns) == 1:
                unique.add(str(columns[0]["name"]))
        schema = schemas.get(name) or _schema({})
        tables.append(
            _SqliteTable(
                name=str(name),
                fields=schema.fields | frozenset(str(row["name"]) for row in info),
                # A composite primary key names no one row by one fact.
                primary=schema.primary
                | (frozenset(primary) if len(primary) == 1 else frozenset()),
                unique=schema.unique | frozenset(unique),
                referencing=schema.referencing,
                connection=connection,
            )
        )
    return tables


def _facts(value: object, path: tuple[str, ...] = ()) -> Iterator[_Fact]:
    """Every scalar the fixture holds, with the name it is held under and the name of the block around it."""
    if isinstance(value, dict):
        for name, child in value.items():
            yield from _facts(child, (*path, str(name)))
        return
    if isinstance(value, list):
        for position, child in enumerate(value):
            yield from _facts(child, (*path, str(position)))
        return
    if isinstance(value, bool) or not isinstance(value, (str, int)) or value == "":
        return
    named = [segment for segment in path if not segment.isdigit()]
    if not named or named[-1] in _NOT_A_FACT:
        return
    yield _Fact(
        path=path,
        name=named[-1],
        parent=named[-2] if len(named) > 1 else "",
        value=value,
    )


def _nouns(table: str) -> tuple[str, ...]:
    return (table, table[:-1]) if table.endswith("s") else (table,)


def _names_key(fact: _Fact, table: _Table, key_field: str) -> bool:
    """Whether a fact is meant as ``key_field`` of ``table``: `rider_id`, `order_ids` for orders.id, `product` for
    products.product_id. A bare `id` names a table only from inside a block that names it."""
    if fact.name == key_field:
        return key_field != "id" or fact.parent in _nouns(table.name)
    return any(
        fact.name in (f"{noun}_{key_field}", f"{noun}_{key_field}s")
        or (fact.name == noun and key_field in ("id", f"{noun}_id"))
        for noun in _nouns(table.name)
    )


def _summary(key_field: str, row: Row) -> str:
    values = [
        str(value)
        for name, value in row.items()
        if name != key_field
        and value is not None
        and value != ""
        and not isinstance(value, (list, dict))
    ]
    return " · ".join([str(row[key_field]), *values[:SUMMARY_VALUES]])


def _record_of(
    fact: _Fact, tables: list[_Table], named: frozenset[str]
) -> StartingRecord | None:
    """The record a fact names, looked up by its key. A held row beats a missing one; then the table the block
    around the fact names, the table sharing most of the fixture's names, a primary key over a unique one, and a
    table's own row over one that points at another table's. Tables come sorted by name and `max` keeps the first
    of equals, so a tie goes to the first table by name."""
    candidates = []
    for table in tables:
        for key_field in sorted(table.primary | table.unique):
            if not _names_key(fact, table, key_field):
                continue
            row = table.lookup(key_field, fact.value)
            rank = (
                row is not None,
                fact.parent in _nouns(table.name),
                len(named & table.fields),
                key_field in table.primary,
                key_field not in table.referencing,
            )
            candidates.append((rank, table, key_field, row))
    if not candidates:
        return None
    _, table, key_field, row = max(candidates, key=lambda one: one[0])
    return StartingRecord(
        path=list(fact.path),
        label=".".join(fact.path),
        table=table.name,
        key_field=key_field,
        key=str(fact.value),
        status="seeded" if row is not None else "missing",
        summary=_summary(key_field, row) if row is not None else None,
        fields=row,
        note=None
        if row is not None
        else f"{table.name} has no seeded row with {key_field} {fact.value}.",
        choices=[
            SeededRow(key=str(one[key_field]), summary=_summary(key_field, one))
            for one in table.others(key_field, fact.value, CHOICES_MAX)
        ],
    )


def _records(fixture: object, tables: list[_Table]) -> list[StartingRecord]:
    facts = list(_facts(fixture))
    named = frozenset(fact.name for fact in facts)
    return [
        record
        for fact in facts
        if (record := _record_of(fact, tables, named)) is not None
    ]


def scenario_token(value: JsonValue) -> str:
    """One spelling for a scenario's folder, name and key, which older outputs wrote differently."""
    return PurePosixPath(str(value or "").strip()).name.lower().replace("-", "_")


def _scenario(
    suite: list[dict[str, JsonValue]], scenario_key: str
) -> dict[str, JsonValue]:
    wanted = scenario_token(scenario_key)
    for document in suite:
        if (
            wanted
            and scenario_token(document.get("scenario_key") or document.get("name"))
            == wanted
        ):
            return document
    raise HostedHarnessError(
        "scenario_not_found", "Scenario not found", status_code=404
    )


def _has_setup(
    document: Mapping[str, JsonValue], suite_files: Mapping[str, bytes]
) -> bool:
    """Whether the scenario's setup changes the world: its `setup_code`, or the `setup.py` in its folder."""
    if str(document.get("setup_code") or "").strip():
        return True
    folder = str(document.get("name") or "")
    code = suite_files.get(f"scenarios/{folder}/setup.py", b"")
    return b"def setup(" in code


def _with_tables(
    environment_files: Mapping[str, bytes],
    use: Callable[[list[_Table]], _T],
) -> tuple[_T | None, RowsUnavailable | None]:
    """``use`` applied to the world's seeded tables, held open for it; else why they can't be read."""
    contract = _json(environment_files, "contract.json")
    data_schema = contract.get("data_schema") if isinstance(contract, dict) else None
    schemas = {
        str(name): _schema(columns)
        for name, columns in (
            data_schema.items() if isinstance(data_schema, dict) else ()
        )
        if isinstance(columns, dict)
    }
    store = _json(environment_files, "store.json")
    seeded = store.get("rows") if isinstance(store, dict) else None
    if isinstance(seeded, dict):
        return use(_store_tables(seeded, schemas)), None
    database = environment_files.get("world.sqlite")
    if not database:
        return None, _NO_ROWS
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(database)
        connection.row_factory = sqlite3.Row
        return use(_sqlite_tables(connection, schemas)), None
    except sqlite3.DatabaseError:
        logger.warning("starting_records_sqlite_unreadable", size=len(database))
        return None, _UNREADABLE
    finally:
        connection.close()


def scenario_records(
    files: ScenarioFiles,
) -> tuple[list[StartingRecord], RowsUnavailable | None]:
    """The records a scenario's facts name, and why the world's rows can't be read when they can't."""
    fixture = files.document.get("fixture")
    records, unavailable = _with_tables(
        files.environment, lambda tables: _records(fixture, tables)
    )
    return records or [], unavailable


def seeded_row(
    files: ScenarioFiles, *, table: str, key_field: str, key: str
) -> tuple[Row | None, RowsUnavailable | None]:
    """One seeded row of ``table`` by its key, looked up in the world's own files."""

    def find(tables: list[_Table]) -> Row | None:
        held = next((one for one in tables if one.name == table), None)
        return held.lookup(key_field, key) if held is not None else None

    return _with_tables(files.environment, find)


def archive_scenario_files(archive: bytes, scenario_key: str) -> ScenarioFiles | None:
    """The scenario as a builder's save holds it, with that save's world; None when the save has no such scenario."""
    from simulate.services.harness_versioning import archives

    split = archives.split_authoring(archives.read_archive(archive))
    try:
        document = _scenario(suite_documents(split.suite), scenario_key)
    except HostedHarnessError:
        return None
    return ScenarioFiles(
        document=document, suite=split.suite, environment=split.environment
    )


def scenario_files(job: HostedHarnessJob, scenario_key: str) -> ScenarioFiles:
    """The scenario as the next run would read it; storage that can't serve the files raises ``HostedHarnessError``."""
    try:
        sides = drafts.edit_sides(job)
    except (S3Error, HTTPError) as exc:
        logger.warning(
            "starting_records_files_unreadable", job_id=str(job.id), exc_info=True
        )
        raise HostedHarnessError(
            _UNREADABLE["code"], _UNREADABLE["reason"], status_code=503
        ) from exc
    return ScenarioFiles(
        document=_scenario(suite_documents(sides["suite"]), scenario_key),
        suite=sides["suite"],
        environment=sides["environment"],
    )


def starting_records(job: HostedHarnessJob, scenario_key: str) -> StartingRecords:
    """The records ``scenario_key`` starts from as the next run would read them, and its latest rework request."""
    from simulate.services.harness_starting_record_rework import latest_rework

    try:
        files = scenario_files(job, scenario_key)
    except HostedHarnessError as exc:
        if exc.code == "scenario_not_found":
            raise
        return StartingRecords(
            scenario_key=scenario_key,
            origin=None,
            records=[],
            setup_note=None,
            rows_unavailable=_UNREADABLE,
            rework=None,
        )
    records, unavailable = scenario_records(files)
    fixture = files.document.get("fixture")
    origin = fixture.get("origin") if isinstance(fixture, dict) else None
    return StartingRecords(
        scenario_key=str(
            files.document.get("scenario_key") or files.document.get("name") or ""
        ),
        origin=str(origin) if origin else None,
        records=records,
        setup_note=_SETUP_NOTE if _has_setup(files.document, files.suite) else None,
        rows_unavailable=unavailable,
        rework=latest_rework(job, files, records),
    )
