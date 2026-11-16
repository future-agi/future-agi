"""A scenario's starting records: the seeded rows whose keys its ``fixture`` facts name.

Choosing another record goes through the Scenarios draft write, so it is proved again on Save.
"""

from __future__ import annotations

import copy
import json
import sqlite3
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import TypeAlias, TypedDict

import structlog
from minio.error import S3Error

from simulate.models import HostedHarnessJob
from simulate.services.harness_actor_validation import Problem, problem
from simulate.services.harness_versioning import drafts
from simulate.services.harness_versioning.versions import suite_documents
from simulate.services.hosted_harness import HostedHarnessError

logger = structlog.get_logger(__name__)

JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)
Key: TypeAlias = str | int

# Rows a record offers instead of its own; a bigger table lists its first ones and says how many it holds.
ALTERNATIVES_MAX = 50
# Rows read from each table of a `world.sqlite`.
ROWS_READ_MAX = 1000
# Facts that describe the fixture itself, never a record.
_NOT_A_FACT = frozenset({"origin"})


class SeededRow(TypedDict):
    key: Key
    fields: dict[str, JsonValue]


class StartingRecord(TypedDict):
    # Where the fact sits in the fixture, one segment per level (a list position as its digits).
    path: list[str]
    label: str
    table: str
    key_field: str
    key: Key
    fields: dict[str, JsonValue]
    alternatives: list[SeededRow]
    alternatives_total: int


class RowsUnavailable(TypedDict):
    code: str
    reason: str


class StartingRecords(TypedDict):
    scenario_key: str
    origin: str | None
    # Whether a choice can be written: only where edits go to the unsaved changes.
    editable: bool
    records: list[StartingRecord]
    rows_unavailable: RowsUnavailable | None


@dataclass(frozen=True)
class _Table:
    name: str
    fields: frozenset[str]
    primary: frozenset[str]
    # Key fields that point at another table's row: the row is that one's, not this table's own.
    referencing: frozenset[str]
    # Each key field whose values are unique across the rows, by the value's text.
    indexes: Mapping[str, Mapping[str, dict[str, JsonValue]]]
    rows: list[dict[str, JsonValue]]


@dataclass(frozen=True)
class _Fact:
    path: tuple[str, ...]
    name: str
    parent: str
    value: Key


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
    if value is None or isinstance(value, (str, int, float, bool, list, dict)):
        return value  # type: ignore[return-value]  # a JSON document's own values
    return str(value)


def _sqlite_tables(
    data: bytes,
) -> dict[str, tuple[list[dict[str, JsonValue]], frozenset[str]]] | None:
    """Each table's rows and primary key, from a `world.sqlite` held in memory; none when it is not a database."""
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(data)
        connection.row_factory = sqlite3.Row
        names = [
            str(row[0])
            for row in connection.execute(
                "select name from sqlite_master where type = 'table' and name not like 'sqlite_%'"
            )
        ]
        tables = {}
        for name in names:
            # A table name cannot be a bound parameter; it comes from the database itself and is quoted as one.
            quoted = '"' + name.replace('"', '""') + '"'
            primary = frozenset(
                str(row["name"])
                for row in connection.execute(f"pragma table_info({quoted})")  # noqa: S608 - quoted identifier
                if row["pk"]
            )
            rows = [
                {str(field): _scalar(row[field]) for field in row.keys()}
                for row in connection.execute(
                    f"select * from {quoted} limit ?",  # noqa: S608 - quoted identifier
                    (ROWS_READ_MAX,),
                )
            ]
            tables[name] = (rows, primary)
        return tables
    except sqlite3.DatabaseError:
        logger.warning("starting_records_sqlite_unreadable", size=len(data))
        return None
    finally:
        connection.close()


def _table(
    name: str,
    rows: list[dict[str, JsonValue]],
    schema: Mapping[str, object],
    primary: frozenset[str],
) -> _Table:
    kinds = {str(field): str(kind).upper() for field, kind in schema.items()}
    primary = primary | {
        field for field, kind in kinds.items() if "PRIMARY KEY" in kind
    }
    keys = primary | {field for field, kind in kinds.items() if "UNIQUE" in kind}
    indexes: dict[str, dict[str, dict[str, JsonValue]]] = {}
    for field in keys:
        index = {
            str(row[field]): row
            for row in rows
            if isinstance(row.get(field), (str, int))
            and not isinstance(row.get(field), bool)
        }
        if index and len(index) == len(rows):
            indexes[field] = index
    return _Table(
        name=name,
        fields=frozenset(kinds) | frozenset(field for row in rows for field in row),
        primary=primary,
        referencing=frozenset(
            field for field in keys if "REFERENCES" in kinds.get(field, "")
        ),
        indexes=indexes,
        rows=rows,
    )


def _world(files: Mapping[str, bytes]) -> list[_Table] | RowsUnavailable:
    """The world's seeded tables with their key fields, from the environment's own files."""
    contract = _json(files, "contract.json")
    schema = contract.get("data_schema") if isinstance(contract, dict) else None
    schema = schema if isinstance(schema, dict) else {}

    def columns(name: str) -> Mapping[str, object]:
        found = schema.get(name)
        return found if isinstance(found, dict) else {}

    store = _json(files, "store.json")
    seeded = store.get("rows") if isinstance(store, dict) else None
    if isinstance(seeded, dict):
        return [
            _table(
                str(name),
                [row for row in rows if isinstance(row, dict)],
                columns(str(name)),
                frozenset(),
            )
            for name, rows in sorted(seeded.items())
            if isinstance(rows, list)
        ]
    database = files.get("world.sqlite")
    if database:
        tables = _sqlite_tables(database)
        if tables is None:
            return _UNREADABLE
        return [
            _table(name, rows, columns(name), primary)
            for name, (rows, primary) in sorted(tables.items())
        ]
    return _NO_ROWS


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


def _names_key(name: str, table: _Table, field: str) -> bool:
    """Whether a fact held under ``name`` is meant as ``field`` of ``table``: `rider_id`, `order_ids` for orders.id,
    `product` for products.product_id."""
    if name == field:
        return True
    return any(
        name in (f"{noun}_{field}", f"{noun}_{field}s")
        or (name == noun and field in ("id", f"{noun}_id"))
        for noun in _nouns(table.name)
    )


def _record_of(
    fact: _Fact, tables: list[_Table], named: frozenset[str]
) -> StartingRecord | None:
    """The seeded row a fact names, if any. Where several tables hold it, the one the block around it names wins,
    then the table sharing most of the fixture's names, then a primary key over a unique one, then a table's own
    row over one that points at another table's."""
    candidates = [
        (
            fact.parent in _nouns(table.name),
            len(named & table.fields),
            field in table.primary,
            field not in table.referencing,
            table,
            field,
        )
        for table in tables
        for field, index in table.indexes.items()
        if str(fact.value) in index and _names_key(fact.name, table, field)
    ]
    if not candidates:
        return None
    # Tables come sorted by name, and `max` keeps the first of equals, so a tie goes to the first table by name.
    *_, table, field = max(candidates, key=lambda one: one[:4])
    row = table.indexes[field][str(fact.value)]
    others = [one for one in table.rows if str(one.get(field)) != str(fact.value)]
    return StartingRecord(
        path=list(fact.path),
        label=".".join(fact.path),
        table=table.name,
        key_field=field,
        key=fact.value,
        fields=row,
        alternatives=[
            SeededRow(key=one[field], fields=one)  # type: ignore[typeddict-item]  # an indexed key is str or int
            for one in others[:ALTERNATIVES_MAX]
        ],
        alternatives_total=len(others),
    )


def _records(fixture: object, tables: list[_Table]) -> list[StartingRecord]:
    facts = list(_facts(fixture))
    named = frozenset(fact.name for fact in facts)
    return [
        record
        for fact in facts
        if (record := _record_of(fact, tables, named)) is not None
    ]


def _scenario(
    suite: list[dict[str, JsonValue]], scenario_key: str
) -> dict[str, JsonValue]:
    from simulate.services.hosted_harness_gateway import _scenario_token

    wanted = _scenario_token(scenario_key)
    for document in suite:
        if (
            wanted
            and _scenario_token(document.get("scenario_key") or document.get("name"))
            == wanted
        ):
            return document
    raise HostedHarnessError(
        "scenario_not_found", "Scenario not found", status_code=404
    )


def _answer(
    job: HostedHarnessJob,
    document: dict[str, JsonValue],
    world: list[_Table] | RowsUnavailable,
) -> StartingRecords:
    fixture = document.get("fixture")
    origin = fixture.get("origin") if isinstance(fixture, dict) else None
    unavailable = world if isinstance(world, dict) else None
    return StartingRecords(
        scenario_key=str(document.get("scenario_key") or document.get("name") or ""),
        origin=str(origin) if origin else None,
        editable=drafts.drafts_apply(job),
        records=[]
        if unavailable or not isinstance(world, list)
        else _records(fixture, world),
        rows_unavailable=unavailable,
    )


def starting_records(job: HostedHarnessJob, scenario_key: str) -> StartingRecords:
    """The records ``scenario_key`` starts from as the next run would read them: the draft's, else the saved ones."""
    try:
        sides = drafts.edit_sides(job)
    except (S3Error, HostedHarnessError):
        logger.warning(
            "starting_records_files_unreadable", job_id=str(job.id), exc_info=True
        )
        return StartingRecords(
            scenario_key=scenario_key,
            origin=None,
            editable=False,
            records=[],
            rows_unavailable=_UNREADABLE,
        )
    document = _scenario(suite_documents(sides["suite"]), scenario_key)
    return _answer(job, document, _world(sides["environment"]))


def record_invalid(problems: list[Problem]) -> HostedHarnessError:
    return HostedHarnessError(
        "starting_record_invalid",
        " ".join(one["message"] for one in problems),
        status_code=400,
        details={"problems": problems},
    )


def _with_fact(
    fixture: dict[str, JsonValue], path: list[str], value: Key
) -> dict[str, JsonValue]:
    edited = copy.deepcopy(fixture)
    holder: JsonValue = edited
    for segment in path[:-1]:
        holder = holder[int(segment)] if isinstance(holder, list) else holder[segment]  # type: ignore[index]
    last = path[-1]
    if isinstance(holder, list):
        holder[int(last)] = value
    elif isinstance(holder, dict):
        holder[last] = value
    return edited


def choose_starting_record(
    job: HostedHarnessJob,
    scenario_key: str,
    *,
    path: list[str],
    table: str,
    key: str,
    user: object,
) -> StartingRecords:
    """Start ``scenario_key`` from another seeded row of ``table``: the fact at ``path`` becomes that row's key, in the
    suite draft. Only a fact that names a seeded record today can be changed, and only to a key the table holds."""
    from simulate.services.hosted_harness_gateway import AuthoringArchiveKept

    if not drafts.drafts_apply(job):
        raise HostedHarnessError(
            "starting_records_read_only",
            "This environment was built before scenarios could be changed here; rebuild it to choose its records.",
            status_code=409,
        )
    drafts.require_no_rebuild(job, piece="suite")
    sides = drafts.edit_sides(job)
    suite = suite_documents(sides["suite"])
    document = _scenario(suite, scenario_key)
    world = _world(sides["environment"])
    if isinstance(world, dict):
        raise HostedHarnessError(world["code"], world["reason"], status_code=409)
    fixture = document.get("fixture")
    record = next(
        (one for one in _records(fixture, world) if one["path"] == path), None
    )
    if record is None:
        raise record_invalid(
            [
                problem(
                    "path", "This fact doesn't name a seeded record of this scenario."
                )
            ]
        )
    if record["table"] != table:
        raise record_invalid(
            [
                problem(
                    "table",
                    f"This fact names a record of {record['table']}, not {table}.",
                )
            ]
        )
    seeded = next(one for one in world if one.name == table)
    row = seeded.indexes[record["key_field"]].get(key)
    if row is None:
        raise record_invalid(
            [
                problem(
                    "key",
                    f"{table} has no seeded row with {record['key_field']} {key}.",
                )
            ]
        )
    chosen = row[record["key_field"]]
    value: Key = str(chosen) if isinstance(record["key"], str) else chosen  # type: ignore[assignment]  # indexed key
    edited = {**document, "fixture": _with_fact(fixture, path, value)}  # type: ignore[arg-type]  # a fixture with a record is a dict
    try:
        written = drafts.write_suite_edit(
            job, [edited if one is document else one for one in suite], user=user
        )
    except AuthoringArchiveKept as kept:
        raise HostedHarnessError("not_saved", str(kept), status_code=409) from kept
    if written is None:
        raise HostedHarnessError(
            "not_saved",
            "The change could not be saved; read the scenario again.",
            status_code=409,
        )
    logger.info(
        "starting_record_chosen",
        job_id=str(job.id),
        scenario_key=scenario_key,
        table=table,
        key_field=record["key_field"],
    )
    return starting_records(job, scenario_key)
