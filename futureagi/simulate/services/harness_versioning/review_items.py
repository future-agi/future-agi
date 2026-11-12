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


def _unique_keys(entries: Any, keyed: Any) -> bool:
    """Whether every entry is a list member with its own key."""
    if entries is None:
        return True
    if not isinstance(entries, list):
        return False
    keys = [keyed(entry) for entry in entries]
    return all(keys) and len(set(keys)) == len(keys)


def whole_documents(
    files: Mapping[str, bytes], other: Mapping[str, bytes] | None = None
) -> set[str]:
    """The keyed documents that cannot be split into items without losing content: not JSON of the expected shape,
    an entry without its key, or two entries with one key; with ``other`` (the tree it is compared with), also a
    listed document whose data next to its entries differs between the two. Such a document is reviewed as one
    file, so nothing in it can change unseen."""
    whole = _malformed(files)
    if other is not None:
        whole |= _malformed(other)
        for document, (_, field, _) in LISTED.items():
            if document not in files or document not in other:
                continue
            sides = [read_json(tree[document]) for tree in (files, other)]
            rest = [
                {name: one for name, one in side.items() if name != field}
                if isinstance(side, dict)
                else {}
                for side in sides
            ]
            if rest[0] != rest[1]:
                whole.add(document)
    return whole


def _malformed(files: Mapping[str, bytes]) -> set[str]:
    whole = set()
    if "contract.json" in files:
        contract = read_json(files["contract.json"])
        if not isinstance(contract, dict) or not (
            _unique_keys(
                contract.get("tools"),
                lambda one: (
                    isinstance(one, dict) and one.get("name") and str(one["name"])
                ),
            )
            and _unique_keys(
                contract.get("hard_constraints"),
                lambda one: one is not None and rule_key(str(one)),
            )
        ):
            whole.add("contract.json")
    for document, (_, field, key) in LISTED.items():
        if document not in files:
            continue
        body = read_json(files[document])
        entries = body.get(field) if isinstance(body, dict) else body
        if not _unique_keys(
            entries,
            lambda one, key=key: (
                isinstance(one, dict) and one.get(key) and str(one[key])
            ),
        ):
            whole.add(document)
    whole |= _shared_keys(files)
    index = read_json(files.get("scenarios.json", b"null"))
    if "scenarios.json" in files and not _unique_keys(index, index_key):
        whole.add("scenarios.json")
    return whole


def _shared_keys(files: Mapping[str, bytes]) -> set[str]:
    """The listed documents of one kind that hold an entry with the same key (an actor and a sub-actor sharing an id):
    their items would share one review id, so each such document is reviewed as one file."""
    seen: dict[tuple[str, str], str] = {}
    shared: set[str] = set()
    for document, (kind, field, key) in LISTED.items():
        if document not in files:
            continue
        body = read_json(files[document])
        entries = body.get(field) if isinstance(body, dict) else body
        for entry in entries if isinstance(entries, list) else []:
            if not (isinstance(entry, dict) and entry.get(key)):
                continue
            other = seen.setdefault((kind, str(entry[key])), document)
            if other != document:
                shared |= {document, other}
    return shared


# Where a scenario item keeps its entry in the suite index, which runs read as the scenario.
INDEX_ENTRY = "_index"


def index_key(entry: Any) -> str:
    """The scenario folder a suite index entry stands for: its name, else its scenario key; empty when neither."""
    if not isinstance(entry, dict):
        return ""
    return str(entry.get("name") or entry.get("scenario_key") or "")


def _scenario_items(files: Mapping[str, bytes], whole: set[str]) -> list[Item]:
    folders: dict[str, dict[str, bytes]] = {}
    # Like ALK and the suite hash, `scenarios/` wins and the older `scenario/` is read only without it.
    root = next(
        (
            one
            for one in hashing.SCENARIO_ROOTS
            if any(path.startswith(one + "/") for path in files)
        ),
        hashing.SCENARIO_ROOTS[0],
    )
    for path, data in files.items():
        parts = path.split("/")
        if parts[0] == root and len(parts) >= 3:
            folders.setdefault(parts[1], {})["/".join(parts[2:])] = data
    index = read_json(files.get("scenarios.json", b"null"))
    entries = (
        {}
        if "scenarios.json" in whole or not isinstance(index, list)
        else {index_key(one): one for one in index}
    )
    items = []
    for key in sorted(set(folders) | set(entries)):
        inside = folders.get(key, {})
        parsed = read_json(inside.get("scenario.json", b""))
        document = parsed if isinstance(parsed, dict) else {}
        # A scenario.json that is not a JSON object is compared by its bytes, so a change to it is still reviewed.
        others = {
            name: archives.sha256_hex(data)
            for name, data in sorted(inside.items())
            if name != "scenario.json" or not isinstance(parsed, dict)
        }
        value = {
            **document,
            **({"_files": others} if others else {}),
            **({INDEX_ENTRY: entries[key]} if key in entries else {}),
        }
        items.append(Item("scenario", key, f"{root}/{key}/scenario.json", value))
    return items


def index_mismatch(value: Any) -> list[str]:
    """The fields where a scenario's suite index entry and its own document disagree, or `_index` when the entry has
    no scenario folder to agree with."""
    if not isinstance(value, dict) or INDEX_ENTRY not in value:
        return []
    entry = value[INDEX_ENTRY]
    document = {
        name: one for name, one in value.items() if name not in (INDEX_ENTRY, "_files")
    }
    if not document:
        return [INDEX_ENTRY]
    return sorted(
        name for name in set(entry) & set(document) if entry[name] != document[name]
    )


def index_in_step(files: Mapping[str, bytes]) -> dict[str, bytes]:
    """The tree with each suite index entry's fields that its scenario document also has taken from the document, so
    an edit to a scenario's own file (its faults, its limits) never leaves the entry runs read saying otherwise. The
    index is rewritten only when an entry changed."""
    index = read_json(files.get("scenarios.json", b"null"))
    if not isinstance(index, list):
        return dict(files)
    changed = False
    for entry in index:
        key = index_key(entry)
        document = next(
            (
                read_json(files[path])
                for root in hashing.SCENARIO_ROOTS
                if (path := f"{root}/{key}/scenario.json") in files
            ),
            None,
        )
        if not key or not isinstance(document, dict):
            continue
        for name in set(entry) & set(document):
            if entry[name] != document[name]:
                entry[name] = document[name]
                changed = True
    if not changed:
        return dict(files)
    return {**files, "scenarios.json": json.dumps(index, indent=2).encode("utf-8")}


def item_index(
    files: Mapping[str, bytes], whole: set[str] | frozenset[str] = frozenset()
) -> dict[str, Item]:
    """Every reviewable item of an authoring tree, by id. A document in ``whole`` is one file item."""
    whole = set(whole) | _malformed(files)
    items = (
        []
        if "contract.json" in whole
        else _contract_items(read_json(files.get("contract.json", b"null")))
    )
    for document in LISTED:
        if document in files and document not in whole:
            items += _listed_items(document, read_json(files[document]))
    items += _scenario_items(files, whole)
    for path, data in sorted(files.items()):
        # Sealed bundles, suite indexes and session files are not part of what a version means.
        if path not in whole and (
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
                            record = dict(zip(columns, row, strict=True))
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


def _summary(action: str, item: Item, fields: list[str]) -> str:
    noun = item.kind.replace("_", " ")
    # A rule's key is a hash of its text, so the text names it; who last edited an item is not a change to it.
    name = f'"{item.value}"' if item.kind == "rule" else item.key
    shown = [field for field in fields if field != "provenance"]
    if action == "changed" and shown:
        return f"Change {', '.join(shown)} of {noun} {name}"
    verb = {"added": "Add", "removed": "Remove", "changed": "Change"}[action]
    return f"{verb} {noun} {name}"


def _rows_said(rows: list[dict[str, Any]] | None) -> str:
    """Each table's row counts, e.g. "orders 1 row added, 2 changed"; empty when they could not be read."""
    said = []
    for table in rows or []:
        counts = [
            (table.get(what) or 0, what) for what in ("added", "removed", "changed")
        ]
        counts = [(count, what) for count, what in counts if count]
        if counts:
            noun = "row" if counts[0][0] == 1 else "rows"
            words = [f"{count} {what}" for count, what in counts]
            words[0] = f"{counts[0][0]} {noun} {counts[0][1]}"
            said.append(f"{table['table']} {', '.join(words)}")
    return "; ".join(said)


def compute_changes(
    default: Mapping[str, bytes], draft: Mapping[str, bytes]
) -> list[dict[str, Any]]:
    """The draft's changes against the default, one per item, in review order."""
    whole = whole_documents(default, draft)
    before, after = item_index(default, whole), item_index(draft, whole)
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
        rows = (
            world_rows(item.key, old.raw if old else None, new.raw if new else None)
            if item.kind == "world_state"
            else None
        )
        summary = _summary(action, item, fields)
        if _rows_said(rows):
            summary = f"{summary}: {_rows_said(rows)}"
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
                "summary": summary,
                **({"rows": rows} if item.kind == "world_state" else {}),
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
