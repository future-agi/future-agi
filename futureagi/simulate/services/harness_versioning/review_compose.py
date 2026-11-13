"""A piece composed from its saved version plus exactly the accepted changes, and the digest of what was decided."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

import jsonpatch

from simulate.services.harness_versioning import hashing
from simulate.services.harness_versioning.review_items import (
    LISTED,
    canonical,
    index_key,
    item_index,
    read_json,
    rule_key,
)

# The keyed documents a review may show as one file when they cannot be split into items.
WHOLE_CANDIDATES = frozenset({"contract.json", "scenarios.json", *LISTED})


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
    return "sha256:" + hashlib.sha256(canonical([default_hash, triples])).hexdigest()


def _put(files: dict[str, bytes], document: str, body: Any) -> None:
    files[document] = (json.dumps(body, indent=2, ensure_ascii=False) + "\n").encode()


def _apply_listed(files: dict[str, bytes], change: dict[str, Any]) -> None:
    """Apply one accepted change to its entry of a JSON document, found again by its key, never by position."""
    document, kind, key = change["document"], change["kind"], change["key"]
    body = read_json(files.get(document, b"null"))
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
        _, field, _ = LISTED[document]
        holder = body if isinstance(body, dict) else {field: body or []}
    # A document kept as a bare list stays a bare list.
    bare = document != "contract.json" and isinstance(body, list)
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
    _put(files, document, entries if bare else holder)


def _is_item(kind: str, key: str, entry: Any) -> bool:
    if kind == "rule":
        return rule_key(str(entry)) == key
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
    # Documents reviewed as one file stay one file here, so the items match the ones the person decided.
    whole = {
        one["key"]
        for one in changes
        if one["kind"] in ("file", "world_state") and one["key"] in WHOLE_CANDIDATES
    }
    current = item_index(default, whole)
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
    if "contract.json" not in whole:
        _carry_amendments(default, draft, files, changes, decisions)
    if "scenarios.json" not in whole:
        _compose_suite_index(default, draft, files, changes, decisions)
    return files


def _compose_suite_index(
    default: Mapping[str, bytes],
    draft: Mapping[str, bytes],
    files: dict[str, bytes],
    changes: list[dict[str, Any]],
    decisions: Mapping[str, str],
) -> None:
    """The suite index lists the scenarios a version runs: each accepted scenario change takes the draft's entry (or
    drops it when the scenario was removed), every other scenario keeps the default's entry, in the default's order;
    an entry with no scenario folder in the composed tree is left out.
    """
    accepted = {
        change["key"]
        for change in changes
        if change["kind"] == "scenario" and decisions.get(change["id"]) == "accepted"
    }
    before = read_json(default.get("scenarios.json", b"null"))
    if not accepted or not isinstance(before, list):
        return
    after = read_json(draft.get("scenarios.json", b"null"))
    drafted = {
        index_key(one): one
        for one in (after if isinstance(after, list) else [])
        if isinstance(one, dict)
    }
    index, listed = [], set()
    for one in before:
        name = index_key(one)
        listed.add(name)
        if name not in accepted:
            index.append(one)
        elif name in drafted:
            index.append(drafted[name])
    index += [
        one for name, one in drafted.items() if name in accepted and name not in listed
    ]
    # An entry whose scenario folder is not in the composed tree names a scenario the version does not have.
    index = [
        one
        for one in index
        if any(
            f"{root}/{index_key(one)}/scenario.json" in files
            for root in hashing.SCENARIO_ROOTS
        )
    ]
    _put(files, "scenarios.json", index)


# The em dash the harness puts between an amendment's action and its subject.
_AMENDMENT_DASH = "\u2014"


def _amendment_owner(entry: str, changes: list[dict[str, Any]]) -> str | None:
    """The change an amendment line records, read from the line's subject. The harness writes `rule added`, `rule
    removed`, `tool removed` and `tool corrected` lines as the action, a dash, the rule or tool, then `: <why>`, and a
    contract field change as `<field> <was> -> <now>: <why>`."""
    for change in changes:
        if change["kind"] in ("rule", "tool"):
            subject = (
                change["patch"][0]["value"]
                if change["kind"] == "rule" and change["patch"]
                else change["key"]
            )
            if f"{_AMENDMENT_DASH} {subject}: " in entry:
                return str(change["id"])
        elif change["kind"] == "contract_field" and entry.startswith(
            f"{change['key']} "
        ):
            return str(change["id"])
    return None


def _carry_amendments(
    default: Mapping[str, bytes],
    draft: Mapping[str, bytes],
    files: dict[str, bytes],
    changes: list[dict[str, Any]],
    decisions: Mapping[str, str],
) -> None:
    """The contract's amendment log keeps the lines the draft added for accepted changes, and drops the lines of
    rejected ones. A line that names no change under review is kept only while every contract change was accepted,
    since it may record a rejected one in words the log does not parse."""
    if "contract.json" not in files:
        return
    contract = read_json(files["contract.json"])
    edited = read_json(draft.get("contract.json", b"null"))
    if not isinstance(contract, dict) or not isinstance(edited, dict):
        return
    kept = list(
        (read_json(default.get("contract.json", b"null")) or {}).get("amendments") or []
    )
    all_accepted = all(
        decisions.get(one["id"]) == "accepted"
        for one in changes
        if one["document"] == "contract.json"
    )
    for entry in edited.get("amendments") or []:
        if entry in kept:
            continue
        owner = _amendment_owner(str(entry), changes)
        if (owner is None and all_accepted) or (
            owner is not None and decisions.get(owner) == "accepted"
        ):
            kept.append(entry)
    if kept == list(contract.get("amendments") or []):
        return
    _put(
        files,
        "contract.json",
        {**contract, "amendments": kept} if kept else _without(contract, "amendments"),
    )


def _without(body: dict[str, Any], field: str) -> dict[str, Any]:
    return {name: value for name, value in body.items() if name != field}
