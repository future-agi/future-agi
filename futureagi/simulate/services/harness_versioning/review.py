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

# Kinds in the order a review lists them.
KINDS = ("tool", "rule", "contract_field", "sub_goal", "actor", "source", "scenario", "file")
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
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


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
        if field not in ("tools", "hard_constraints") and field not in _CONTRACT_IGNORED_FIELDS
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
        others = {name: archives.sha256_hex(data) for name, data in sorted(inside.items()) if name != "scenario.json"}
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
        items.append(Item("file", path, path, None, raw=data))
    return {item.id: item for item in items}


def _tested(before: Any, patch: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The patch with a `test` on each value it replaces or removes, so it applies only to what was read."""
    tests = []
    for op in patch:
        if op["op"] in ("replace", "remove"):
            pointer = jsonpatch.JsonPointer(op["path"])
            tests.append({"op": "test", "path": op["path"], "value": pointer.resolve(before)})
    return tests + patch


def _fields(before: Any, after: Any) -> list[str]:
    if isinstance(before, dict) and isinstance(after, dict):
        return sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))
    return []


def _summary(action: str, kind: str, key: str, fields: list[str]) -> str:
    noun = kind.replace("_", " ")
    if action == "changed" and fields:
        return f"Change {', '.join(fields)} of {noun} {key}"
    verb = {"added": "Add", "removed": "Remove", "changed": "Change"}[action]
    return f"{verb} {noun} {key}"


def compute_changes(default: Mapping[str, bytes], draft: Mapping[str, bytes]) -> list[dict[str, Any]]:
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


def review_digest(default_hash: str, changes: list[dict[str, Any]], decisions: Mapping[str, str]) -> str:
    """What the person saw and decided: every change's id, draft content and decision, on this default."""
    triples = sorted((one["id"], one["draft_digest"] or "", decisions.get(one["id"], "")) for one in changes)
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
    if undecided := [one["id"] for one in changes if decisions.get(one["id"]) not in ("accepted", "rejected")]:
        raise ReviewRefused("review_undecided", "Every change needs a decision before Save.", undecided)
    current = item_index(default)
    moved = [
        one["id"] for one in changes
        if decisions[one["id"]] == "accepted"
        and (current[one["id"]].digest if one["id"] in current else None) != one["base_digest"]
    ]
    if moved:
        raise ReviewRefused("review_stale", "These changed after the review; review them again.", moved)
    files = dict(default)
    for change in changes:
        if decisions[change["id"]] != "accepted":
            continue
        try:
            if change["kind"] == "scenario":
                prefix = change["document"].rsplit("/", 1)[0] + "/"
                for path in [one for one in files if one.startswith(prefix)]:
                    del files[path]
                files.update({path: data for path, data in draft.items() if path.startswith(prefix)})
            elif change["kind"] == "file":
                if change["action"] == "removed":
                    files.pop(change["key"], None)
                else:
                    files[change["key"]] = draft[change["key"]]
            else:
                _apply_listed(files, change)
        except jsonpatch.JsonPatchException as exc:
            raise ReviewRefused("review_stale", f"{change['id']} changed after the review: {exc}", [change["id"]]) from exc
    return files


DECISIONS = ("accepted", "rejected")


def _decisions(draft: Any) -> dict[str, dict[str, Any]]:
    return dict(((draft.documents or {}).get("review") or {}).get("decisions") or {})


def _piece_changes(environment: Any, piece: str, draft: Any) -> list[dict[str, Any]]:
    from simulate.services.harness_versioning.drafts import _piece_files

    before = _piece_files(environment, piece, draft.parent) if draft.parent_id else {}
    after = _piece_files(environment, piece, draft)
    return [{**change, "piece": piece} for change in compute_changes(before, after)]


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
        drafts[piece] = {"id": str(draft.id), "revision": draft.revision, "parent_id": str(draft.parent_id or "")}
        defaults.append(str(getattr(draft.parent, "hash", "") or ""))
        recorded = _decisions(draft)
        for change in _piece_changes(environment, piece, draft):
            entry = recorded.get(change["id"]) or {}
            decision = entry.get("decision") if entry.get("draft_digest") == change["draft_digest"] else None
            change["decision"] = decision
            if decision:
                decided[change["id"]] = decision
            changes.append(change)
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
    environment: Any, decisions: list[dict[str, Any]], *, expected_revisions: dict[str, int], user: Any
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
        current = {piece: open_draft(environment, piece, lock=True) for piece in DRAFT_PIECES}
        current = {piece: draft for piece, draft in current.items() if draft is not None}
        if not current:
            raise _error("draft_not_found", "No unsaved changes", 404)
        for piece, draft in current.items():
            if piece in expected_revisions and expected_revisions[piece] != draft.revision:
                raise draft_changed(piece=piece)
        changes = [change for piece, draft in current.items() for change in _piece_changes(environment, piece, draft)]
        by_id = {change["id"]: change for change in changes}
        for item in decisions:
            decision = item.get("decision")
            if decision not in DECISIONS:
                raise _error("decision_invalid", "A decision is accepted or rejected", 400)
            if item.get("id"):
                if item["id"] not in by_id:
                    raise _error("change_not_found", "That change is not in the draft any more", 404, id=item["id"])
                targets = [by_id[item["id"]]]
            elif item.get("kind"):
                targets = [change for change in changes if change["kind"] == item["kind"]]
            else:
                targets = changes
            for change in targets:
                draft = current[change["piece"]]
                recorded = _decisions(draft)
                if item.get("remaining") and (recorded.get(change["id"]) or {}).get("draft_digest") == change[
                    "draft_digest"
                ]:
                    continue
                recorded[change["id"]] = {"decision": decision, "draft_digest": change["draft_digest"]}
                review = {**((draft.documents or {}).get("review") or {}), "decisions": recorded}
                draft.documents = {**(draft.documents or {}), "review": review}
        for draft in current.values():
            draft.last_edited_by = user
            draft.save(update_fields=["documents", "last_edited_by", "updated_at"])
    return draft_review(environment)
