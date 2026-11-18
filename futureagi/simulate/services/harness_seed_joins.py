"""Broken joins between the seed-record files attached to an environment.

A seed-records file (role `seed_records` in `knowledge/roles.json`) holds rows the world starts with, as CSV or as a
JSON list of objects. A field named `<thing>_id` points at the row whose `id` it names in the seed file named after
`<thing>` (`order_id` reads `orders.csv`, `order.csv` or the JSON twins). A value with no such row is a broken join:
the scenarios that cite that file cannot start from what they presume, so they are held back with the fix. A field
pointing at a file that is not attached is not checked: nothing says where its rows live.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath

from django.core.cache import cache

from simulate.services.harness_actor_rules import KNOWLEDGE_ROLES, file_roles
from simulate.services.harness_scenario_sources import read_files

# The field suffix that names a row of another seed file, and the field that names a row itself.
JOIN_SUFFIX = "_id"
ROW_ID = "id"
# How many broken rows a reason names before it counts the rest.
SHOWN = 3
# How long an archive's broken joins are remembered; the archive itself never changes.
JOINS_TTL_SECONDS = 7 * 24 * 3600
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
) -> dict[str, list[BrokenJoin]] | None:
    """The broken joins of the seed files a version attaches, read only when a case cites an attached file. The
    archive is streamed once, for the roles file and every file that could hold seed rows (named by the version's
    file digests), and what it holds is remembered by the archive, which never changes. ``None`` when the archive
    could not be read just now: unknown, never remembered, and never read as "nothing broken"."""
    archive = getattr(version, "archive_object_key", None)
    if not cited or not archive:
        return {}
    remembered = cache.get(_cache_key(archive))
    if isinstance(remembered, dict):
        return {
            path: [BrokenJoin(**one) for one in joins]
            for path, joins in remembered.items()
        }
    digests = (getattr(version, "documents", None) or {}).get("file_digests") or {}
    candidates = {
        path
        for path in digests
        if isinstance(path, str)
        and path.startswith("knowledge/")
        and path.endswith(SEED_FORMATS)
    }
    files, readable = read_files(version, {KNOWLEDGE_ROLES, *candidates})
    if not readable:
        return None
    if KNOWLEDGE_ROLES in files:
        # A seed file the digests did not name (a version saved before they were kept) is read in a second pass.
        unread = {
            path
            for path, role in file_roles(files).items()
            if role == "seed_records"
            and path.endswith(SEED_FORMATS)
            and path not in files
        }
        if unread:
            more, readable = read_files(version, unread)
            if not readable:
                return None
            files = {**files, **more}
    found = broken_joins(files) if KNOWLEDGE_ROLES in files else {}
    cache.set(
        _cache_key(archive),
        {path: [asdict(one) for one in joins] for path, joins in found.items()},
        timeout=JOINS_TTL_SECONDS,
    )
    return found


def _cache_key(archive: str) -> str:
    return "harness-seed-joins:v1:" + hashlib.sha256(archive.encode()).hexdigest()


def broken_join_text(joins: list[BrokenJoin]) -> str:
    """Why the file's rows cannot all be joined, naming the first few."""
    named = "; ".join(
        f"{one.field} {one.value} has no row in {PurePosixPath(one.target).name}"
        for one in joins[:SHOWN]
    )
    more = f" and {len(joins) - SHOWN} more" if len(joins) > SHOWN else ""
    return f"{PurePosixPath(joins[0].path).name}: {named}{more}"
