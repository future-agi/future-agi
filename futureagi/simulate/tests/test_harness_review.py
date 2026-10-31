import json

import jsonpatch
import pytest

from simulate.services.harness_versioning.review import compute_changes, item_index


def _tree(contract=None, **files):
    tree = {"contract.json": json.dumps(contract or {}).encode()}
    for path, value in files.items():
        tree[path.replace("__", "/")] = value if isinstance(value, bytes) else json.dumps(value).encode()
    return tree


TOOLS = [{"name": "lookup_order", "description": "Find an order"}, {"name": "issue_refund", "description": "Refund an order"}]


def test_a_changed_tool_is_one_change_whose_patch_applies_only_to_what_was_read():
    default = _tree({"tools": TOOLS})
    draft = _tree({"tools": [TOOLS[0], {**TOOLS[1], "description": "Refund an order up to $50"}]})

    [change] = compute_changes(default, draft)
    assert (change["id"], change["action"], change["fields"]) == ("tool:issue_refund", "changed", ["description"])
    assert change["summary"] == "Change description of tool issue_refund"
    assert change["patch"][0] == {"op": "test", "path": "/description", "value": "Refund an order"}
    assert jsonpatch.apply_patch(TOOLS[1], change["patch"]) == {**TOOLS[1], "description": "Refund an order up to $50"}
    # Another save changed the same tool underneath: the decision cannot land on content nobody saw.
    with pytest.raises(jsonpatch.JsonPatchTestFailed):
        jsonpatch.apply_patch({**TOOLS[1], "description": "Refund"}, change["patch"])


def test_reordering_items_is_not_a_change_and_an_unchanged_draft_has_none():
    default = _tree({"tools": TOOLS, "hard_constraints": ["a", "b"]})
    assert compute_changes(default, _tree({"tools": TOOLS[::-1], "hard_constraints": ["b", "a"]})) == []
    assert compute_changes(default, dict(default)) == []


def test_every_kind_of_item_is_found_added_removed_or_changed_in_review_order():
    default = _tree(
        {"tools": TOOLS, "hard_constraints": ["Never refund twice"], "persona": "calm"},
        **{
            "sub_goals.json": {"sub_goals": [{"name": "one_refund", "check": "a"}]},
            "actors.json": {"actors": [{"id": "manager", "role": "approves"}]},
            "scenarios/dup/scenario.json": {"instruction": "ask twice"},
            "world.sqlite": b"rows-1",
        },
    )
    draft = _tree(
        {"tools": TOOLS, "hard_constraints": ["Never refund twice in a day"], "persona": "warm"},
        **{
            "sub_goals.json": {"sub_goals": []},
            "actors.json": {"actors": [{"id": "manager", "role": "approves"}, {"id": "auditor", "role": "reads"}]},
            "sources.json": {"sources": [{"id": "src_1", "role": "policy"}]},
            "scenarios/dup/scenario.json": {"instruction": "ask twice"},
            "scenarios/dup/setup.sql": b"insert",
            "world.sqlite": b"rows-2",
        },
    )
    changes = compute_changes(default, draft)
    # A reworded rule is a new item: its key is the text's digest.
    assert sorted(one["action"] for one in changes[:2]) == ["added", "removed"]
    assert [one["kind"] for one in changes[:2]] == ["rule", "rule"]
    assert [(one["kind"], one["action"]) for one in changes[2:]] == [
        ("contract_field", "changed"),
        ("sub_goal", "removed"),
        ("actor", "added"),
        ("source", "added"),
        ("scenario", "changed"),
        ("file", "changed"),
    ]
    world = changes[-1]
    assert (world["key"], world["patch"]) == ("world.sqlite", [])
    assert world["base_digest"] != world["draft_digest"]
    assert changes[4]["patch"] == [{"op": "add", "path": "", "value": {"id": "auditor", "role": "reads"}}]
    assert changes[3]["patch"] == [{"op": "test", "path": "", "value": {"name": "one_refund", "check": "a"}}]


def test_suite_indexes_sealed_bundles_and_session_files_are_never_review_items():
    tree = _tree(
        {},
        **{
            "scenarios.json": [{"scenario_key": "dup"}],
            "environment-bundle/manifest.json": {"digest": "x"},
            "runtime-validation.json": {},
        },
    )
    assert set(item_index(tree)) == set()
