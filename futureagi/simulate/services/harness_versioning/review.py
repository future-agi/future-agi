"""The changes a draft makes, item by item, for a person to accept or reject before Save.

A change is one item of the authoring tree whose content differs between the default (the saved version) and the
draft: a tool, a rule or any other contract field, a sub-goal, an actor, a source, a scenario, or any other file.
Items are found by one rule per document, so a writer the review has never heard of still shows up, as a file.
Each change carries an RFC 6902 patch of the item that starts with `test` operations on what it read, so Save can
refuse a decision whose item moved underneath it.
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
from simulate.services.harness_versioning.references import ALL, affects, depends_on

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
_LISTS = {
    "sub_goals.json": ("sub_goal", "sub_goals", "name"),
    "actors.json": ("actor", "actors", "id"),
    "sources.json": ("source", "sources", "id"),
}
# Files whose whole content is read as items above, never as a file of its own.
_ITEM_DOCUMENTS = frozenset({"contract.json", *_LISTS})


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
        body = self.raw if self.raw is not None else _canonical(self.value)
        return "sha256:" + hashlib.sha256(body).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()


def _json(data: bytes) -> Any:
    try:
        return json.loads(data)
    except ValueError:
        return None


def _rule_key(text: str) -> str:
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
        Item("rule", _rule_key(str(rule)), "contract.json", rule)
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
    kind, field, key = _LISTS[document]
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
        document = _json(inside.get("scenario.json", b"")) or {}
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
    items = _contract_items(_json(files.get("contract.json", b"null")))
    for document in _LISTS:
        if document in files:
            items += _listed_items(document, _json(files[document]))
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
        body = _json(data)
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
                    ): hashlib.sha256(_canonical(row)).hexdigest()
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


class ReviewRefused(Exception):
    """A review that cannot be saved as decided: `code` says why, `changes` names the items."""

    def __init__(self, code: str, message: str, changes: list[str]) -> None:
        super().__init__(message)
        self.code = code
        self.changes = changes


def review_digest(
    default_hash: str, changes: list[dict[str, Any]], decisions: Mapping[str, str]
) -> str:
    """What the person saw and decided: every change's id, draft content and decision, on this default."""
    triples = sorted(
        (one["id"], one["draft_digest"] or "", decisions.get(one["id"], ""))
        for one in changes
    )
    return "sha256:" + hashlib.sha256(_canonical([default_hash, triples])).hexdigest()


def _put(files: dict[str, bytes], document: str, body: Any) -> None:
    files[document] = (json.dumps(body, indent=2, ensure_ascii=False) + "\n").encode()


def _apply_listed(files: dict[str, bytes], change: dict[str, Any]) -> None:
    """Apply one accepted change to its entry of a JSON document, found again by its key, never by position."""
    document, kind, key = change["document"], change["kind"], change["key"]
    body = _json(files.get(document, b"null"))
    if document == "contract.json":
        body = body if isinstance(body, dict) else {}
        if kind == "contract_field":
            if change["action"] == "removed":
                body.pop(key, None)
            elif change["action"] == "added":
                body[key] = change["patch"][0]["value"]
            else:
                body[key] = jsonpatch.apply_patch(body.get(key), change["patch"])
            return _put(files, document, body)
        field = "tools" if kind == "tool" else "hard_constraints"
        holder = body
    else:
        _, field, _ = _LISTS[document]
        holder = body if isinstance(body, dict) else {field: body or []}
    entries = list(holder.get(field) or [])
    found = [index for index, entry in enumerate(entries) if _is_item(kind, key, entry)]
    if change["action"] == "added":
        if found:
            raise jsonpatch.JsonPatchTestFailed(f"{change['id']} already exists")
        entries.append(change["patch"][0]["value"])
    else:
        if not found:
            raise jsonpatch.JsonPatchTestFailed(f"{change['id']} is no longer there")
        index = found[0]
        if change["action"] == "removed":
            jsonpatch.apply_patch(entries[index], change["patch"])
            entries.pop(index)
        else:
            entries[index] = jsonpatch.apply_patch(entries[index], change["patch"])
    holder[field] = entries
    _put(files, document, holder)


def _is_item(kind: str, key: str, entry: Any) -> bool:
    if kind == "rule":
        return _rule_key(str(entry)) == key
    name = "name" if kind in ("tool", "sub_goal") else "id"
    return isinstance(entry, dict) and str(entry.get(name)) == key


def compose_accepted(
    default: Mapping[str, bytes],
    draft: Mapping[str, bytes],
    changes: list[dict[str, Any]],
    decisions: Mapping[str, str],
) -> dict[str, bytes]:
    """The default with exactly the accepted changes applied, or a refusal naming the items.

    Each accepted change is applied to its item as the default holds it now; a `test` that fails means the default
    moved after the review, and nothing is applied (`review_stale`).
    """
    if undecided := [
        one["id"]
        for one in changes
        if decisions.get(one["id"]) not in ("accepted", "rejected")
    ]:
        raise ReviewRefused(
            "review_undecided", "Every change needs a decision before Save.", undecided
        )
    current = item_index(default)
    moved = [
        one["id"]
        for one in changes
        if decisions[one["id"]] == "accepted"
        and (current[one["id"]].digest if one["id"] in current else None)
        != one["base_digest"]
    ]
    if moved:
        raise ReviewRefused(
            "review_stale", "These changed after the review; review them again.", moved
        )
    files = dict(default)
    for change in changes:
        if decisions[change["id"]] != "accepted":
            continue
        try:
            if change["kind"] == "scenario":
                prefix = change["document"].rsplit("/", 1)[0] + "/"
                for path in [one for one in files if one.startswith(prefix)]:
                    del files[path]
                files.update(
                    {
                        path: data
                        for path, data in draft.items()
                        if path.startswith(prefix)
                    }
                )
            elif change["kind"] in ("file", "world_state"):
                if change["action"] == "removed":
                    files.pop(change["key"], None)
                else:
                    files[change["key"]] = draft[change["key"]]
            else:
                _apply_listed(files, change)
        except jsonpatch.JsonPatchException as exc:
            raise ReviewRefused(
                "review_stale",
                f"{change['id']} changed after the review: {exc}",
                [change["id"]],
            ) from exc
    return files


DECISIONS = ("accepted", "rejected")


def _decisions(draft: Any) -> dict[str, dict[str, Any]]:
    return dict(((draft.documents or {}).get("review") or {}).get("decisions") or {})


def _piece_changes(environment: Any, piece: str, draft: Any) -> list[dict[str, Any]]:
    from simulate.services.harness_versioning.drafts import _piece_files

    before = _piece_files(environment, piece, draft.parent) if draft.parent_id else {}
    after = _piece_files(environment, piece, draft)
    return [{**change, "piece": piece} for change in compute_changes(before, after)]


def _scenarios(environment: Any) -> dict[str, dict[str, Any]]:
    """The scenarios as the drafts would leave them (the suite draft, else the saved suite), by key."""
    from simulate.services.harness_versioning.drafts import _piece_files, open_draft

    version = open_draft(environment, "suite") or environment.suite_version
    if version is None:
        return {}
    files = _piece_files(environment, "suite", version)
    return {
        item.key: item.value
        for item in item_index(files).values()
        if item.kind == "scenario"
    }


# A scenario's own name is shown, never graded.
_UNGRADED_SCENARIO_FIELDS = frozenset({"name", "title"})


def graded(change: Mapping[str, Any]) -> bool:
    """Whether a change alters what a trial is graded on: every change except a scenario's rename (fields the diff
    ignores never become changes at all)."""
    if change["kind"] == "scenario" and change["action"] == "changed":
        return not set(change.get("fields") or []) <= _UNGRADED_SCENARIO_FIELDS
    return True


def draft_review(environment: Any) -> dict[str, Any]:
    """Every change the open drafts make, each with the decision taken on it.

    A decision holds only for the content it was taken on: once the draft changes that item again, the item is
    undecided until someone decides it again.
    """
    from simulate.services.harness_versioning.drafts import DRAFT_PIECES, open_draft

    drafts: dict[str, dict[str, Any]] = {}
    changes: list[dict[str, Any]] = []
    decided: dict[str, str] = {}
    defaults: list[str] = []
    for piece in DRAFT_PIECES:
        draft = open_draft(environment, piece)
        if draft is None:
            continue
        drafts[piece] = {
            "id": str(draft.id),
            "revision": draft.revision,
            "parent_id": str(draft.parent_id or ""),
        }
        defaults.append(str(getattr(draft.parent, "hash", "") or ""))
        recorded = _decisions(draft)
        sources = (draft.documents or {}).get("provenance") or {}
        for change in _piece_changes(environment, piece, draft):
            change["sources"] = list(reversed(sources.get(change["id"]) or []))
            entry = recorded.get(change["id"]) or {}
            decision = (
                entry.get("decision")
                if entry.get("draft_digest") == change["draft_digest"]
                else None
            )
            change["decision"] = decision
            if decision:
                decided[change["id"]] = decision
            changes.append(change)
    scenarios = _scenarios(environment)
    for change in changes:
        change["affects"] = affects(change, scenarios)
        change["depends_on"] = depends_on(change, changes, scenarios)
        change["graded"] = graded(change)
        touched = sorted(scenarios) if change["affects"] == ALL else change["affects"]
        change["revalidate"] = touched if change["graded"] else []
    counts = {
        "total": len(changes),
        "accepted": sum(one["decision"] == "accepted" for one in changes),
        "rejected": sum(one["decision"] == "rejected" for one in changes),
    }
    counts["undecided"] = counts["total"] - counts["accepted"] - counts["rejected"]
    return {
        "drafts": drafts,
        "changes": changes,
        "counts": counts,
        "review_digest": review_digest("|".join(defaults), changes, decided),
        "can_save": bool(changes) and counts["undecided"] == 0,
    }


def record_decisions(
    environment: Any,
    decisions: list[dict[str, Any]],
    *,
    expected_revisions: dict[str, int],
    user: Any,
) -> dict[str, Any]:
    """Store accept or reject on changes, by id, by kind, or for every remaining one, and return the review.

    A decision is kept with the content it was taken on. A draft that moved since the person looked refuses the
    whole request (`draft_changed`), so nobody decides on content they did not see.
    """
    from django.db import transaction

    from simulate.services.harness_versioning.drafts import (
        DRAFT_PIECES,
        _error,
        draft_changed,
        open_draft,
    )

    with transaction.atomic():
        current = {
            piece: open_draft(environment, piece, lock=True) for piece in DRAFT_PIECES
        }
        current = {
            piece: draft for piece, draft in current.items() if draft is not None
        }
        if not current:
            raise _error("draft_not_found", "No unsaved changes", 404)
        for piece, draft in current.items():
            if (
                piece in expected_revisions
                and expected_revisions[piece] != draft.revision
            ):
                raise draft_changed(piece=piece)
        changes = [
            change
            for piece, draft in current.items()
            for change in _piece_changes(environment, piece, draft)
        ]
        by_id = {change["id"]: change for change in changes}
        for item in decisions:
            decision = item.get("decision")
            if decision not in DECISIONS:
                raise _error(
                    "decision_invalid", "A decision is accepted or rejected", 400
                )
            if item.get("id"):
                if item["id"] not in by_id:
                    raise _error(
                        "change_not_found",
                        "That change is not in the draft any more",
                        404,
                        id=item["id"],
                    )
                targets = [by_id[item["id"]]]
            elif item.get("kind"):
                targets = [
                    change for change in changes if change["kind"] == item["kind"]
                ]
            else:
                targets = changes
            for change in targets:
                draft = current[change["piece"]]
                recorded = _decisions(draft)
                if (
                    item.get("remaining")
                    and (recorded.get(change["id"]) or {}).get("draft_digest")
                    == change["draft_digest"]
                ):
                    continue
                recorded[change["id"]] = {
                    "decision": decision,
                    "draft_digest": change["draft_digest"],
                }
                review = {
                    **((draft.documents or {}).get("review") or {}),
                    "decisions": recorded,
                }
                draft.documents = {**(draft.documents or {}), "review": review}
        for draft in current.values():
            draft.last_edited_by = user
            draft.save(update_fields=["documents", "last_edited_by", "updated_at"])
    return draft_review(environment)


def apply_review(
    locked: Any, pieces: set[str], *, expected_review_digest: str | None
) -> None:
    """Before Save makes drafts live: when the person rejected any change, rewrite each draft as the saved version
    plus exactly the accepted changes. With no decision at all, Save keeps today's behaviour (the whole draft).

    Refuses a review that moved since the person looked (`review_stale`) or one with a change still undecided
    (`review_undecided`), naming the changes.
    """
    from simulate.services.harness_versioning import archives
    from simulate.services.harness_versioning.drafts import (
        _content,
        _error,
        _piece_files,
        _prepared,
        open_draft,
    )

    review = draft_review(locked)
    if expected_review_digest and expected_review_digest != review["review_digest"]:
        raise _error(
            "review_stale",
            "The changes moved since you reviewed them; review them again",
            409,
        )
    refused = {one["id"] for one in review["changes"] if one["decision"] == "rejected"}
    needs = [
        (one["id"], other)
        for one in review["changes"]
        if one["decision"] == "accepted"
        for other in one.get("depends_on") or []
        if other in refused
    ]
    if needs:
        raise _error(
            "change_depends_on_rejected",
            "An accepted change needs one you rejected; accept both or reject both",
            409,
            pairs=[{"change": change, "needs": other} for change, other in needs],
        )
    rejected = {
        one["piece"] for one in review["changes"] if one["decision"] == "rejected"
    } & pieces
    if any(one["decision"] for one in review["changes"]):
        # What the person decided, kept with the version it becomes, so its summary and diff are the approved ones.
        for piece in pieces:
            draft = open_draft(locked, piece, lock=True)
            kept = [
                {
                    name: one[name]
                    for name in (
                        "id",
                        "kind",
                        "key",
                        "action",
                        "summary",
                        "decision",
                        "affects",
                        "graded",
                        "revalidate",
                    )
                }
                for one in review["changes"]
                if one["piece"] == piece
            ]
            draft.documents = {**(draft.documents or {}), "review_changes": kept}
            draft.save(update_fields=["documents", "updated_at"])
    if not rejected:
        return
    decisions = {
        one["id"]: one["decision"] for one in review["changes"] if one["decision"]
    }
    for piece in sorted(rejected):
        draft = open_draft(locked, piece, lock=True)
        default = draft.parent
        changes = [one for one in review["changes"] if one["piece"] == piece]
        try:
            composed = compose_accepted(
                _piece_files(locked, piece, default) if default is not None else {},
                _piece_files(locked, piece, draft),
                changes,
                decisions,
            )
        except ReviewRefused as refused:
            raise _error(
                refused.code, str(refused), 409, changes=refused.changes
            ) from refused
        other = "suite" if piece == "environment" else "environment"
        other_draft = open_draft(locked, other)
        other_version = other_draft or getattr(locked, f"{other}_version")
        files = {**composed, **_piece_files(locked, other, other_version)}
        _, prepared = _prepared(locked, files)
        body = (
            (prepared.environment_key, prepared.environment_body)
            if piece == "environment"
            else (prepared.suite_key, prepared.suite_body)
        )
        archives.put_archive(*body)
        values = _content(locked, piece, prepared, origin=draft.origin, default=default)
        values["documents"]["review"] = (draft.documents or {}).get("review")
        values["documents"]["review_changes"] = (draft.documents or {}).get(
            "review_changes"
        )
        for name, value in values.items():
            setattr(draft, name, value)
        draft.save()


SUMMARY_ITEMS = 5


def review_summary(review: dict[str, Any]) -> str:
    """A saved version's summary, from what the person accepted: the first few changes and how many more."""
    accepted = [
        one["summary"]
        for one in (review or {}).get("changes") or []
        if one.get("decision") == "accepted"
    ]
    head = "; ".join(accepted[:SUMMARY_ITEMS])
    more = len(accepted) - SUMMARY_ITEMS
    return head + (f"; and {more} more" if more > 0 else "")


def carry_untouched_proofs(
    parent: Any, version: Any, suite_version: Any, reviewed: list[dict[str, Any]]
) -> None:
    """Scenarios no accepted change touched keep the proofs they had against the environment version this one came
    from. A change that may touch every scenario (a rule, a contract field, a world file) carries nothing.
    """
    from simulate.models import HarnessSuiteValidation

    accepted = [one for one in reviewed if one.get("decision") == "accepted"]
    if (
        parent is None
        or suite_version is None
        or any(one.get("affects") == ALL for one in accepted)
    ):
        return
    earlier = HarnessSuiteValidation.no_workspace_objects.filter(
        suite_version=suite_version, environment_version=parent
    ).first()
    if earlier is None or not isinstance(earlier.proofs, dict):
        return
    touched = {key for one in accepted for key in one.get("affects") or []}
    kept = {key: proof for key, proof in earlier.proofs.items() if key not in touched}
    if not kept:
        return
    holding = [
        key
        for key, proof in kept.items()
        if isinstance(proof, dict) and proof.get("holds") is True
    ]
    status = (
        HarnessSuiteValidation.Status.PASSED
        if len(kept) == len(earlier.proofs) and len(holding) == len(kept)
        else HarnessSuiteValidation.Status.PARTIAL
    )
    HarnessSuiteValidation.no_workspace_objects.get_or_create(
        suite_version=suite_version,
        environment_version=version,
        defaults={
            "status": status,
            "proofs": kept,
            "job": earlier.job,
            "proved_at": earlier.proved_at,
        },
    )


def name_saved(activated: dict[str, Any], *, name: str) -> None:
    """Give each version a save created the person's name and, when it was reviewed, a summary of what they
    accepted. A version the save reused keeps what it had."""
    for version, created in activated.values():
        if not created:
            continue
        fields = []
        if name:
            version.name, fields = name[:80], [*fields, "name"]
        reviewed = (version.documents or {}).get("review_changes")
        if reviewed:
            version.summary, fields = review_summary({"changes": reviewed})[:1024], [
                *fields,
                "summary",
            ]
            if version.__class__.__name__ == "HarnessEnvironmentVersion":
                carry_untouched_proofs(
                    version.parent, version, version.job.suite_version, reviewed
                )
        if fields:
            version.save(update_fields=[*fields, "updated_at"])


SOURCES_PER_CHANGE = 20


def record_sources(
    provenance: dict[str, list[dict[str, Any]]] | None,
    before: Mapping[str, bytes],
    after: Mapping[str, bytes],
    *,
    origin: str,
    user: Any,
    revision: int,
    at: str,
    cited: Mapping[str, Mapping[str, Any]] | None = None,
    used: list[dict[str, Any]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Where each change came from: every item this write moved gets one source (who, through what, at which draft
    revision), appended to the sources it already had. A retried write at the same revision adds nothing. `used`
    is what the writer read for every item it moved, such as the file and row an import took it from.
    """
    out = {key: list(value) for key, value in (provenance or {}).items()}
    old, new = item_index(before), item_index(after)
    moved = sorted(
        item_id
        for item_id in set(old) | set(new)
        if (old[item_id].digest if item_id in old else None)
        != (new[item_id].digest if item_id in new else None)
    )
    source = {
        "origin": origin,
        "agent": (
            {"kind": "user", "user_id": str(getattr(user, "id", "") or "")}
            if user is not None
            else {"kind": "harness"}
        ),
        "at": at,
        "draft_revision": revision,
        **({"used": list(used)} if used else {}),
    }
    for item_id in moved:
        listed = out.setdefault(item_id, [])
        if any(one.get("draft_revision") == revision for one in listed):
            continue
        chat = (cited or {}).get(item_id)
        if chat:
            # A chat change belongs to the person whose message it answered, made through the harness.
            listed.append(
                {
                    **source,
                    "agent": {
                        "kind": "harness",
                        "on_behalf_of": source["agent"].get("user_id", ""),
                    },
                    "tool": chat.get("tool", ""),
                    "used": [
                        {"kind": "message", "id": chat.get("message_id", "")},
                        *(chat.get("used") or []),
                    ],
                    **({"note": chat["note"]} if chat.get("note") else {}),
                }
            )
        else:
            listed.append(source)
        del listed[:-SOURCES_PER_CHANGE]
    return out


PROVENANCE_FILE = ".futureagi-provenance.jsonl"


def _used(value: Any) -> list[dict[str, Any]]:
    """The files and rows a chat call read, as the provenance line names them; anything else is dropped."""
    out = []
    for one in value if isinstance(value, list) else []:
        if (
            isinstance(one, dict)
            and one.get("kind") == "source"
            and one.get("source_id")
        ):
            out.append(
                {
                    name: str(one[name])
                    for name in ("kind", "source_id", "version", "digest", "locator")
                    if one.get(name)
                }
            )
    return out


def chat_citations(published: Mapping[str, bytes]) -> dict[str, dict[str, Any]]:
    """Per item, the chat tool call that last moved it, the person's message that call answered, what the call read
    (a file and its row) and its note, from the builder chat's provenance file in a published tree.
    """
    out: dict[str, dict[str, Any]] = {}
    for line in (
        (published.get(PROVENANCE_FILE) or b"").decode("utf-8", "replace").splitlines()
    ):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        for item_id in entry.get("items") or []:
            out[str(item_id)] = {
                "message_id": str(entry.get("message_id") or ""),
                "tool": str(entry.get("tool") or ""),
                "used": _used(entry.get("used")),
                "note": str(entry.get("note") or "")[:280],
            }
    return out
