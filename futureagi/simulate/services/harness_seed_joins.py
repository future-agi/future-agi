"""Broken joins between the seed-record files attached to an environment.

A seed-records file (role `seed_records` in `knowledge/roles.json`) holds rows the world starts with, as CSV or as a
JSON list of objects. A field named `<thing>_id` points at the row whose `id` it names in the seed file named after
`<thing>` (`order_id` reads `orders.csv`, `order.csv` or the JSON twins). A value with no such row is a broken join:
the scenarios that cite that file cannot start from what they presume, so they are held back with the fix. A field
pointing at a file that is not attached is not checked: nothing says where its rows live.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath

from simulate.services.harness_actor_rules import KNOWLEDGE_ROLES, file_roles
from simulate.services.harness_scenario_sources import file_bodies

# The field suffix that names a row of another seed file, and the field that names a row itself.
JOIN_SUFFIX = "_id"
ROW_ID = "id"
# How many broken rows a reason names before it counts the rest.
SHOWN = 3
SEED_FORMATS = (".csv", ".json")


@dataclass(frozen=True)
class BrokenJoin:
    path: str
    field: str
    value: str
    target: str


def _rows(path: str, body: bytes) -> list[Mapping[str, object]]:
    """The file's rows as objects; a file that cannot be read as rows has none."""
    try:
        text = body.decode("utf-8")
        if path.endswith(".csv"):
            return list(csv.DictReader(io.StringIO(text)))
        value = json.loads(text)
    except (UnicodeDecodeError, ValueError, csv.Error):
        return []
    return (
        [one for one in value if isinstance(one, Mapping)]
        if isinstance(value, list)
        else []
    )


def _table(path: str) -> str:
    return PurePosixPath(path).stem.lower()


def _target(field: str, tables: Mapping[str, str]) -> str | None:
    """The seed file a `<thing>_id` field points at, by its file name, when one is attached."""
    thing = field[: -len(JOIN_SUFFIX)].lower()
    return next(
        (tables[name] for name in (thing, f"{thing}s", f"{thing}es") if name in tables),
        None,
    )


def broken_joins(files: Mapping[str, bytes]) -> dict[str, list[BrokenJoin]]:
    """Each seed-records file with rows whose `<thing>_id` names no row of the file it points at, by path."""
    seeds = {
        path: _rows(path, files[path])
        for path, role in file_roles(dict(files)).items()
        if role == "seed_records" and path in files and path.endswith(SEED_FORMATS)
    }
    tables = {_table(path): path for path in seeds}
    ids = {
        path: {str(row[ROW_ID]) for row in rows if row.get(ROW_ID) not in (None, "")}
        for path, rows in seeds.items()
    }
    found: dict[str, list[BrokenJoin]] = {}
    for path, rows in seeds.items():
        for row in rows:
            for field, value in row.items():
                if (
                    not isinstance(field, str)
                    or not field.endswith(JOIN_SUFFIX)
                    or field == ROW_ID
                    or value in (None, "")
                ):
                    continue
                target = _target(field, tables)
                if target is not None and str(value) not in ids[target]:
                    found.setdefault(path, []).append(
                        BrokenJoin(path, field, str(value), target)
                    )
    return found


def environment_broken_joins(
    version: object, cited: set[str]
) -> dict[str, list[BrokenJoin]]:
    """The broken joins of the seed files a version attaches, read only when a case cites an attached file."""
    if not cited:
        return {}
    roles = file_bodies(version, {KNOWLEDGE_ROLES})
    if not roles:
        return {}
    seeds = [
        path
        for path, role in file_roles(roles).items()
        if role == "seed_records" and path.endswith(SEED_FORMATS)
    ]
    if not seeds:
        return {}
    return broken_joins({**roles, **file_bodies(version, set(seeds))})


def broken_join_text(joins: list[BrokenJoin]) -> str:
    """Why the file's rows cannot all be joined, naming the first few."""
    named = "; ".join(
        f"{one.field} {one.value} has no row in {PurePosixPath(one.target).name}"
        for one in joins[:SHOWN]
    )
    more = f" and {len(joins) - SHOWN} more" if len(joins) > SHOWN else ""
    return f"{PurePosixPath(joins[0].path).name}: {named}{more}"
