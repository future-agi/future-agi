"""The items of an authoring tree and the changes a draft makes to them, one per item.

A change is one item whose content differs between the default (the saved version) and the draft: a tool, a rule or
any other contract field, a sub-goal, an actor, a source, a scenario, or any other file. Items are found by one rule
per document, so a writer the review has never heard of still shows up, as a file. Each change carries an RFC 6902
patch of the item that starts with `test` operations on what it read, so a decision cannot land on an item that moved
underneath it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import jsonpatch

from simulate.services.harness_versioning import archives, hashing
from simulate.services.harness_versioning.diffs import _CONTRACT_IGNORED_FIELDS

# Kinds in the order a review lists them.
KINDS = (
    "tool",
    "rule",
    "contract_field",
    "sub_goal",
    "actor",
    "world_state",
    "source",
    "scenario",
    "file",
)
LISTED = {
    "sub_goals.json": ("sub_goal", "sub_goals", "name"),
    "actors.json": ("actor", "actors", "id"),
    "sub_actors.json": ("actor", "sub_actors", "id"),
    "sources.json": ("source", "sources", "id"),
}
# Files whose whole content is read as items above, never as a file of its own.
_ITEM_DOCUMENTS = frozenset({"contract.json", *LISTED})


@dataclass(frozen=True)
class Item:
    kind: str
    key: str
    document: str
    value: Any
    # The item's bytes when it is not JSON (a world file, a PDF): compared by digest only.
    raw: bytes | None = None

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.key}"

    @property
    def digest(self) -> str:
        body = self.raw if self.raw is not None else canonical(self.value)
        return "sha256:" + hashlib.sha256(body).hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()


def read_json(data: bytes) -> Any:
    try:
        return json.loads(data)
    except ValueError:
        return None


def rule_key(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def _contract_items(contract: Any) -> list[Item]:
    if not isinstance(contract, dict):
        return []
    items = [
        Item("tool", str(tool["name"]), "contract.json", tool)
        for tool in contract.get("tools") or []
        if isinstance(tool, dict) and tool.get("name")
    ]
    items += [
        Item("rule", rule_key(str(rule)), "contract.json", rule)
        for rule in contract.get("hard_constraints") or []
        if rule is not None
    ]
    items += [
        Item("contract_field", field, "contract.json", value)
        for field, value in contract.items()
        if field not in ("tools", "hard_constraints")
        and field not in _CONTRACT_IGNORED_FIELDS
    ]
    return items


def _listed_items(document: str, body: Any) -> list[Item]:
    kind, field, key = LISTED[document]
    entries = body.get(field) if isinstance(body, dict) else body
    return [
        Item(kind, str(entry[key]), document, entry)
        for entry in entries or []
        if isinstance(entry, dict) and entry.get(key)
    ]


def _scenario_items(files: Mapping[str, bytes]) -> list[Item]:
    folders: dict[str, dict[str, bytes]] = {}
    for path, data in files.items():
        parts = path.split("/")
        if parts[0] in hashing.SCENARIO_ROOTS and len(parts) >= 3:
            folders.setdefault(parts[1], {})["/".join(parts[2:])] = data
    items = []
    for key, inside in sorted(folders.items()):
        document = read_json(inside.get("scenario.json", b"")) or {}
        others = {
            name: archives.sha256_hex(data)
            for name, data in sorted(inside.items())
            if name != "scenario.json"
        }
        value = {**document, **({"_files": others} if others else {})}
        items.append(Item("scenario", key, f"scenarios/{key}/scenario.json", value))
    return items


def item_index(files: Mapping[str, bytes]) -> dict[str, Item]:
    """Every reviewable item of an authoring tree, by id."""
    items = _contract_items(read_json(files.get("contract.json", b"null")))
    for document in LISTED:
        if document in files:
            items += _listed_items(document, read_json(files[document]))
    items += _scenario_items(files)
    for path, data in sorted(files.items()):
        # Sealed bundles, suite indexes and session files are not part of what a version means.
        if (
            path in _ITEM_DOCUMENTS
            or path.split("/")[0] in hashing.SCENARIO_ROOTS
            or hashing.is_environment_unhashed(path)
        ):
            continue
        kind = (
            "world_state"
            if path.rsplit("/", 1)[-1] in hashing.WORLD_SEED_FILES
            else "file"
        )
        items.append(Item(kind, path, path, None, raw=data))
    return {item.id: item for item in items}


def _world_tables(path: str, data: bytes | None) -> dict[str, dict[str, str]] | None:
    """A world store's rows per table, each keyed by its primary key (or its position) with the row's digest; None when
    the store cannot be read as tables."""
    if data is None:
        return {}
    if path.endswith(".json"):
        body = read_json(data)
        if not isinstance(body, dict):
            return None
        tables = {}
        for name, rows in body.items():
            if isinstance(rows, list):
                tables[name] = {
                    (
                        str(row.get("id", index))
                        if isinstance(row, dict)
                        else str(index)
                    ): hashlib.sha256(canonical(row)).hexdigest()
                    for index, row in enumerate(rows)
                }
        return tables
    if path.endswith(".sqlite"):
        import sqlite3
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".sqlite") as handle:
            handle.write(data)
            handle.flush()
            try:
                connection = sqlite3.connect(f"file:{handle.name}?mode=ro", uri=True)
                try:
                    names = [
                        row[0]
                        for row in connection.execute(
                            "select name from sqlite_master where type = 'table' and name not like 'sqlite_%'"
                        )
                    ]
                    tables = {}
                    for name in names:
                        quoted = '"' + name.replace('"', '""') + '"'
                        keys = [
                            row[1]
                            for row in connection.execute(
                                f"pragma table_info({quoted})"
                            )
                            if row[5]
                        ]
                        tables[name] = {}
                        rows = connection.execute(f"select * from {quoted}")
                        columns = [one[0] for one in rows.description]
                        for index, row in enumerate(rows):
                            record = dict(zip(columns, row))
                            key = (
                                "|".join(str(record[k]) for k in keys)
                                if keys
                                else str(index)
                            )
                            tables[name][key] = hashlib.sha256(
                                repr(sorted(record.items())).encode()
                            ).hexdigest()
                    return tables
                finally:
                    connection.close()
            except sqlite3.DatabaseError:
                return None
    return None


def world_rows(
    path: str, before: bytes | None, after: bytes | None
) -> list[dict[str, Any]] | None:
    """Per table, how many rows a world store change adds, removes and changes, keyed by primary key; None when either
    side cannot be read as tables."""
    old, new = _world_tables(path, before), _world_tables(path, after)
    if old is None or new is None:
        return None
    out = []
    for table in sorted(set(old) | set(new)):
        was, now = old.get(table, {}), new.get(table, {})
        counts = {
            "added": len(set(now) - set(was)),
            "removed": len(set(was) - set(now)),
            "changed": sum(was[key] != now[key] for key in set(was) & set(now)),
        }
        if any(counts.values()):
            out.append({"table": table, **counts})
    return out


def _tested(before: Any, patch: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The patch with a `test` on each value it replaces or removes, so it applies only to what was read."""
    tests = []
    for op in patch:
        if op["op"] in ("replace", "remove"):
            pointer = jsonpatch.JsonPointer(op["path"])
            tests.append(
                {"op": "test", "path": op["path"], "value": pointer.resolve(before)}
            )
    return tests + patch


def _fields(before: Any, after: Any) -> list[str]:
    if isinstance(before, dict) and isinstance(after, dict):
        return sorted(
            name
            for name in set(before) | set(after)
            if before.get(name) != after.get(name)
        )
    return []


def _summary(action: str, kind: str, key: str, fields: list[str]) -> str:
    noun = kind.replace("_", " ")
    if action == "changed" and fields:
        return f"Change {', '.join(fields)} of {noun} {key}"
    verb = {"added": "Add", "removed": "Remove", "changed": "Change"}[action]
    return f"{verb} {noun} {key}"


def compute_changes(
    default: Mapping[str, bytes], draft: Mapping[str, bytes]
) -> list[dict[str, Any]]:
    """The draft's changes against the default, one per item, in review order."""
    before, after = item_index(default), item_index(draft)
    changes = []
    for item_id in sorted(set(before) | set(after)):
        old, new = before.get(item_id), after.get(item_id)
        if old is not None and new is not None and old.digest == new.digest:
            continue
        item = new or old
        action = "added" if old is None else "removed" if new is None else "changed"
        if item.raw is not None or (old is not None and old.raw is not None):
            patch: list[dict[str, Any]] = []
        elif action == "added":
            patch = [{"op": "add", "path": "", "value": new.value}]
        elif action == "removed":
            patch = [{"op": "test", "path": "", "value": old.value}]
        else:
            patch = _tested(old.value, jsonpatch.make_patch(old.value, new.value).patch)
        fields = _fields(old.value if old else None, new.value if new else None)
        changes.append(
            {
                "id": item_id,
                "kind": item.kind,
                "key": item.key,
                "document": item.document,
                "action": action,
                "fields": fields,
                "patch": patch,
                "base_digest": old.digest if old else None,
                "draft_digest": new.digest if new else None,
                "summary": _summary(action, item.kind, item.key, fields),
                **(
                    {
                        "rows": world_rows(
                            item.key, old.raw if old else None, new.raw if new else None
                        )
                    }
                    if item.kind == "world_state"
                    else {}
                ),
            }
        )
    changes.sort(key=lambda one: (KINDS.index(one["kind"]), one["key"]))
    return changes


# A scenario's own name is shown, never graded.
_UNGRADED_SCENARIO_FIELDS = frozenset({"name", "title"})


def graded(change: Mapping[str, Any]) -> bool:
    """Whether a change alters what a trial is graded on: every change except a scenario's rename (fields the diff
    ignores never become changes at all)."""
    if change["kind"] == "scenario" and change["action"] == "changed":
        return not set(change.get("fields") or []) <= _UNGRADED_SCENARIO_FIELDS
    return True
