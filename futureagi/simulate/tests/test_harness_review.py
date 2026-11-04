import json

import jsonpatch
import pytest

from simulate.services.harness_versioning.review import (
    ReviewRefused,
    compose_accepted,
    compute_changes,
    item_index,
    review_digest,
)


def _tree(contract=None, **files):
    tree = {"contract.json": json.dumps(contract or {}).encode()}
    for path, value in files.items():
        tree[path.replace("__", "/")] = (
            value if isinstance(value, bytes) else json.dumps(value).encode()
        )
    return tree


TOOLS = [
    {"name": "lookup_order", "description": "Find an order"},
    {"name": "issue_refund", "description": "Refund an order"},
]


def test_a_changed_tool_is_one_change_whose_patch_applies_only_to_what_was_read():
    default = _tree({"tools": TOOLS})
    draft = _tree(
        {"tools": [TOOLS[0], {**TOOLS[1], "description": "Refund an order up to $50"}]}
    )

    [change] = compute_changes(default, draft)
    assert (change["id"], change["action"], change["fields"]) == (
        "tool:issue_refund",
        "changed",
        ["description"],
    )
    assert change["summary"] == "Change description of tool issue_refund"
    assert change["patch"][0] == {
        "op": "test",
        "path": "/description",
        "value": "Refund an order",
    }
    assert jsonpatch.apply_patch(TOOLS[1], change["patch"]) == {
        **TOOLS[1],
        "description": "Refund an order up to $50",
    }
    # Another save changed the same tool underneath: the decision cannot land on content nobody saw.
    with pytest.raises(jsonpatch.JsonPatchTestFailed):
        jsonpatch.apply_patch({**TOOLS[1], "description": "Refund"}, change["patch"])


def test_reordering_items_is_not_a_change_and_an_unchanged_draft_has_none():
    default = _tree({"tools": TOOLS, "hard_constraints": ["a", "b"]})
    assert (
        compute_changes(
            default, _tree({"tools": TOOLS[::-1], "hard_constraints": ["b", "a"]})
        )
        == []
    )
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
        {
            "tools": TOOLS,
            "hard_constraints": ["Never refund twice in a day"],
            "persona": "warm",
        },
        **{
            "sub_goals.json": {"sub_goals": []},
            "actors.json": {
                "actors": [
                    {"id": "manager", "role": "approves"},
                    {"id": "auditor", "role": "reads"},
                ]
            },
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
        ("world_state", "changed"),
        ("source", "added"),
        ("scenario", "changed"),
    ]
    world = changes[5]
    # Not a database it can open, so no row summary rather than a wrong one.
    assert (world["key"], world["patch"], world["rows"]) == ("world.sqlite", [], None)
    assert world["base_digest"] != world["draft_digest"]
    assert changes[4]["patch"] == [
        {"op": "add", "path": "", "value": {"id": "auditor", "role": "reads"}}
    ]
    assert changes[3]["patch"] == [
        {"op": "test", "path": "", "value": {"name": "one_refund", "check": "a"}}
    ]


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


def _pair():
    default = _tree(
        {"tools": TOOLS, "persona": "calm"},
        **{
            "actors.json": {"actors": [{"id": "manager"}]},
            "scenarios/a/scenario.json": {"i": 1},
            "world.sqlite": b"w1",
        },
    )
    draft = _tree(
        {
            "tools": [
                {**TOOLS[0], "description": "Look up"},
                {**TOOLS[1], "description": "Refund up to $50"},
            ],
            "persona": "warm",
        },
        **{
            "actors.json": {"actors": [{"id": "manager"}, {"id": "auditor"}]},
            "scenarios/a/scenario.json": {"i": 2},
            "scenarios/a/setup.sql": b"insert",
            "world.sqlite": b"w2",
        },
    )
    return default, draft, compute_changes(default, draft)


def _decide(changes, accepted):
    return {
        one["id"]: "accepted" if one["id"] in accepted else "rejected"
        for one in changes
    }


def test_save_applies_exactly_the_accepted_changes_and_nothing_rejected():
    default, draft, changes = _pair()
    accepted = {
        "tool:issue_refund",
        "actor:auditor",
        "scenario:a",
        "contract_field:persona",
    }
    composed = compose_accepted(default, draft, changes, _decide(changes, accepted))

    assert {one["id"] for one in compute_changes(default, composed)} == accepted
    contract = json.loads(composed["contract.json"])
    assert [tool["description"] for tool in contract["tools"]] == [
        "Find an order",
        "Refund up to $50",
    ]
    assert (
        composed["scenarios/a/setup.sql"] == b"insert"
        and composed["world.sqlite"] == b"w1"
    )


def test_an_accepted_change_lands_on_its_item_even_after_the_list_was_reordered():
    default, draft, changes = _pair()
    reordered = {
        **default,
        "contract.json": json.dumps({"tools": TOOLS[::-1], "persona": "calm"}).encode(),
    }
    composed = compose_accepted(
        reordered, draft, changes, _decide(changes, {"tool:issue_refund"})
    )
    by_name = {
        tool["name"]: tool for tool in json.loads(composed["contract.json"])["tools"]
    }
    assert by_name["issue_refund"]["description"] == "Refund up to $50"
    assert by_name["lookup_order"]["description"] == "Find an order"


def test_save_refuses_an_undecided_change_and_an_accepted_one_whose_item_moved_after_review():
    default, draft, changes = _pair()
    decisions = _decide(changes, {"tool:issue_refund"})
    del decisions["actor:auditor"]
    with pytest.raises(ReviewRefused) as undecided:
        compose_accepted(default, draft, changes, decisions)
    assert (undecided.value.code, undecided.value.changes) == (
        "review_undecided",
        ["actor:auditor"],
    )

    moved = {
        **default,
        "contract.json": json.dumps(
            {
                "tools": [TOOLS[0], {**TOOLS[1], "description": "Refund"}],
                "persona": "calm",
            }
        ).encode(),
    }
    with pytest.raises(ReviewRefused) as stale:
        compose_accepted(moved, draft, changes, _decide(changes, {"tool:issue_refund"}))
    assert (stale.value.code, stale.value.changes) == (
        "review_stale",
        ["tool:issue_refund"],
    )
    # A rejected change whose item moved does not stop the save.
    assert compose_accepted(moved, draft, changes, _decide(changes, {"actor:auditor"}))


def test_the_review_digest_moves_with_any_decision_draft_content_or_default():
    _, _, changes = _pair()
    decisions = _decide(changes, {"tool:issue_refund"})
    digest = review_digest("v1", changes, decisions)
    assert digest == review_digest("v1", list(reversed(changes)), decisions)
    assert digest != review_digest("v2", changes, decisions)
    assert digest != review_digest(
        "v1", changes, {**decisions, "tool:issue_refund": "rejected"}
    )
    edited = [
        dict(one, draft_digest="sha256:other") if one["id"] == "actor:auditor" else one
        for one in changes
    ]
    assert digest != review_digest("v1", edited, decisions)


def test_an_accepted_item_that_gained_a_field_after_review_is_stale_even_where_its_patch_still_applies():
    default, draft, changes = _pair()
    grown = {
        **default,
        "contract.json": json.dumps(
            {
                "tools": [TOOLS[0], {**TOOLS[1], "limits": {"max": 100}}],
                "persona": "calm",
            }
        ).encode(),
    }
    with pytest.raises(ReviewRefused) as stale:
        compose_accepted(grown, draft, changes, _decide(changes, {"tool:issue_refund"}))
    assert stale.value.code == "review_stale"


def test_a_write_adds_one_source_to_each_item_it_moved_and_a_retry_adds_none():
    from types import SimpleNamespace

    from simulate.services.harness_versioning.review import record_sources

    default = _tree({"tools": TOOLS})
    edited = _tree(
        {"tools": [TOOLS[0], {**TOOLS[1], "description": "Refund up to $50"}]}
    )
    user = SimpleNamespace(id="u-1")
    first = record_sources(
        None, default, edited, origin="manual", user=user, revision=1, at="t1"
    )
    assert first == {
        "tool:issue_refund": [
            {
                "origin": "manual",
                "agent": {"kind": "user", "user_id": "u-1"},
                "at": "t1",
                "draft_revision": 1,
            }
        ]
    }
    assert (
        record_sources(
            first, default, edited, origin="manual", user=user, revision=1, at="t2"
        )
        == first
    )
    harness = record_sources(
        first, edited, default, origin="chat", user=None, revision=2, at="t3"
    )
    assert [one["agent"]["kind"] for one in harness["tool:issue_refund"]] == [
        "user",
        "harness",
    ]


def test_a_chat_change_is_the_person_s_through_the_harness_with_the_message_it_answered():
    import json as _json
    from types import SimpleNamespace

    from simulate.services.harness_versioning.review import (
        PROVENANCE_FILE,
        chat_citations,
        record_sources,
    )

    published = {
        PROVENANCE_FILE: b"\n".join(
            _json.dumps(one).encode()
            for one in (
                {
                    "message_id": "m1",
                    "tool": "fix_tool",
                    "items": ["tool:issue_refund"],
                },
                "not an object",
                {
                    "message_id": "m2",
                    "tool": "change_world",
                    "items": ["tool:issue_refund", "file:world.sqlite"],
                },
            )
        )
        + b"\nnot json"
    }
    cited = chat_citations(published)
    assert cited["tool:issue_refund"] == {
        "message_id": "m2",
        "tool": "change_world",
        "used": [],
        "note": "",
    }

    default = _tree({"tools": TOOLS})
    edited = _tree(
        {"tools": [TOOLS[0], {**TOOLS[1], "description": "Refund up to $50"}]}
    )
    [source] = record_sources(
        None,
        default,
        edited,
        origin="chat",
        user=SimpleNamespace(id="u-1"),
        revision=4,
        at="t",
        cited=cited,
    )["tool:issue_refund"]
    assert source["agent"] == {"kind": "harness", "on_behalf_of": "u-1"}
    assert (source["tool"], source["used"]) == (
        "change_world",
        [{"kind": "message", "id": "m2"}],
    )


def test_a_world_store_change_is_world_state_with_its_rows_added_removed_and_changed_per_table():
    import sqlite3
    import tempfile

    from simulate.services.harness_versioning.review import world_rows

    default = _tree(
        store__json={
            "orders": [{"id": 1, "total": 10}, {"id": 2, "total": 20}],
            "users": [{"id": "a"}],
        }
    )
    draft = _tree(
        store__json={
            "orders": [{"id": 1, "total": 15}, {"id": 3, "total": 30}],
            "users": [{"id": "a"}],
        }
    )
    default["store.json"], draft["store.json"] = default.pop("store/json"), draft.pop(
        "store/json"
    )

    [change] = compute_changes(default, draft)

    assert (change["kind"], change["key"], change["patch"]) == (
        "world_state",
        "store.json",
        [],
    )
    assert change["rows"] == [
        {"table": "orders", "added": 1, "removed": 1, "changed": 1}
    ]
    assert (
        compose_accepted(default, draft, [change], {change["id"]: "accepted"})[
            "store.json"
        ]
        == draft["store.json"]
    )

    def database(rows):
        with tempfile.NamedTemporaryFile(suffix=".sqlite") as handle:
            connection = sqlite3.connect(handle.name)
            connection.execute(
                "create table refunds (id integer primary key, amount integer)"
            )
            connection.executemany("insert into refunds values (?, ?)", rows)
            connection.commit()
            connection.close()
            return open(handle.name, "rb").read()

    assert world_rows(
        "world.sqlite", database([(1, 5), (2, 6)]), database([(1, 5), (2, 9), (4, 1)])
    ) == [{"table": "refunds", "added": 1, "removed": 0, "changed": 1}]
    assert world_rows("world.sqlite", b"not a database", b"") is None


def test_every_change_but_a_scenario_rename_is_graded():
    from simulate.services.harness_versioning.review import graded

    assert graded({"kind": "rule", "action": "added"})
    assert graded({"kind": "scenario", "action": "changed", "fields": ["sub_goals"]})
    assert not graded({"kind": "scenario", "action": "changed", "fields": ["name"]})
    assert graded({"kind": "scenario", "action": "removed", "fields": []})


def test_a_source_names_the_file_and_row_a_change_came_from_and_the_harness_note():
    import json as _json
    from types import SimpleNamespace

    from simulate.services.harness_versioning.review import (
        PROVENANCE_FILE,
        chat_citations,
        record_sources,
    )

    row = {
        "kind": "source",
        "source_id": "src-1",
        "version": 2,
        "locator": "row 14",
        "secret": "dropped",
    }
    published = {
        PROVENANCE_FILE: _json.dumps(
            {
                "message_id": "m1",
                "tool": "fix_tool",
                "items": ["tool:issue_refund"],
                "used": [row, "junk"],
                "note": "the policy caps refunds at $50",
            }
        ).encode()
    }
    default = _tree({"tools": TOOLS})
    edited = _tree(
        {"tools": [TOOLS[0], {**TOOLS[1], "description": "Refund up to $50"}]}
    )

    [chat] = record_sources(
        None,
        default,
        edited,
        origin="chat",
        user=SimpleNamespace(id="u-1"),
        revision=1,
        at="t",
        cited=chat_citations(published),
    )["tool:issue_refund"]
    [imported] = record_sources(
        None,
        default,
        edited,
        origin="file_import",
        user=SimpleNamespace(id="u-1"),
        revision=1,
        at="t",
        used=[{"kind": "source", "source_id": "src-1", "locator": "row 3"}],
    )["tool:issue_refund"]

    assert chat["used"] == [
        {"kind": "message", "id": "m1"},
        {"kind": "source", "source_id": "src-1", "version": "2", "locator": "row 14"},
    ]
    assert chat["note"] == "the policy caps refunds at $50"
    assert (imported["origin"], imported["used"]) == (
        "file_import",
        [{"kind": "source", "source_id": "src-1", "locator": "row 3"}],
    )
