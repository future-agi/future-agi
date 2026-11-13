import json

import jsonpatch
import pytest

from simulate.services.harness_versioning.review_compose import (
    ReviewRefused,
    compose_accepted,
    review_digest,
)
from simulate.services.harness_versioning.review_items import (
    compute_changes,
    index_mismatch,
    item_index,
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


def test_sealed_bundles_and_session_files_are_never_review_items_and_an_index_entry_is_its_scenarios():
    tree = _tree(
        {},
        **{
            "scenarios.json": [{"scenario_key": "dup"}],
            "environment-bundle/manifest.json": {"digest": "x"},
            "runtime-validation.json": {},
        },
    )
    items = item_index(tree)
    assert set(items) == {"scenario:dup"}
    assert items["scenario:dup"].value == {"_index": {"scenario_key": "dup"}}


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

    from simulate.services.harness_versioning.review_saved import record_sources

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
    from types import SimpleNamespace

    from simulate.services.harness_versioning.review_saved import record_sources

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
        message_id="m2",
    )["tool:issue_refund"]
    assert source["agent"] == {"kind": "harness", "on_behalf_of": "u-1"}
    assert source["used"] == [{"kind": "message", "id": "m2"}]


def test_a_world_store_change_is_world_state_with_its_rows_added_removed_and_changed_per_table():
    import sqlite3
    import tempfile

    from simulate.services.harness_versioning.review_items import world_rows

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
    default["store.json"], draft["store.json"] = (
        default.pop("store/json"),
        draft.pop("store/json"),
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
    # The summary names the rows, so a saved version reads as what changed in the world.
    assert (
        change["summary"]
        == "Change world state store.json: orders 1 row added, 1 removed, 1 changed"
    )
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
    from simulate.services.harness_versioning.review_items import graded

    assert graded({"kind": "rule", "action": "added"})
    assert graded({"kind": "scenario", "action": "changed", "fields": ["sub_goals"]})
    assert not graded({"kind": "scenario", "action": "changed", "fields": ["name"]})
    assert graded({"kind": "scenario", "action": "removed", "fields": []})


RULE = "Never refund more than the order total"
AMENDMENT = f"rule added \u2014 {RULE}: the policy caps refunds"


def _rule_and_tool():
    """A draft where one write added a rule with its amendment line, and another changed a tool."""
    default = _tree(
        {"tools": TOOLS, "amendments": ["tool removed \u2014 old_tool: no seam"]}
    )
    draft = _tree(
        {
            "tools": [TOOLS[0], {**TOOLS[1], "description": "Refund up to $50"}],
            "hard_constraints": [RULE],
            "amendments": ["tool removed \u2014 old_tool: no seam", AMENDMENT],
        }
    )
    return default, draft, compute_changes(default, draft)


def test_accepted_rule_keeps_its_amendment():
    default, draft, changes = _rule_and_tool()
    rule = next(one["id"] for one in changes if one["kind"] == "rule")
    composed = json.loads(
        compose_accepted(
            default, draft, changes, {rule: "accepted", "tool:issue_refund": "rejected"}
        )["contract.json"]
    )
    assert composed["hard_constraints"] == [RULE]
    assert composed["amendments"] == [
        "tool removed \u2014 old_tool: no seam",
        AMENDMENT,
    ]
    assert composed["tools"] == TOOLS


def test_rejected_rule_leaves_no_amendment():
    default, draft, changes = _rule_and_tool()
    rule = next(one["id"] for one in changes if one["kind"] == "rule")
    composed = json.loads(
        compose_accepted(
            default, draft, changes, {rule: "rejected", "tool:issue_refund": "accepted"}
        )["contract.json"]
    )
    assert not composed.get("hard_constraints")
    assert composed["amendments"] == ["tool removed \u2014 old_tool: no seam"]
    # With nothing accepted, the contract is the saved one, byte for byte, so the version is the saved one.
    nothing = compose_accepted(
        default, draft, changes, {rule: "rejected", "tool:issue_refund": "rejected"}
    )
    assert nothing["contract.json"] == default["contract.json"]


def test_a_rule_change_reads_as_the_rule_and_bookkeeping_is_left_out_of_a_summary():
    rule = "The assistant must state the discount percentage back to the customer before applying it."
    default = _tree(
        {"hard_constraints": ["a"]},
        **{"actors.json": {"actors": [{"id": "morgan_lee", "role": "CEO"}]}},
    )
    draft = _tree(
        {"hard_constraints": ["a", rule]},
        **{
            "actors.json": {
                "actors": [
                    {
                        "id": "morgan_lee",
                        "role": "CEO who signs off",
                        "provenance": {"origin": "editor"},
                    }
                ]
            }
        },
    )

    summaries = {one["kind"]: one["summary"] for one in compute_changes(default, draft)}
    assert summaries == {
        "rule": f'Add rule "{rule}"',
        "actor": "Change role of actor morgan_lee",
    }


def test_the_suite_index_follows_the_accepted_scenario_changes_and_keeps_the_rejected_ones_as_they_were():
    def entry(name, **doc):
        return {"name": name, "folder": f"scenarios/{name}", "steps": [], **doc}

    default = _tree(
        **{
            "scenarios/kept/scenario.json": {"name": "kept", "sub_goals": ["a"]},
            "scenarios/edited/scenario.json": {"name": "edited", "sub_goals": ["a"]},
            "scenarios/gone/scenario.json": {"name": "gone"},
            "scenarios/declined/scenario.json": {
                "name": "declined",
                "sub_goals": ["a"],
            },
            "scenarios.json": [
                entry("kept", sub_goals=["a"]),
                entry("edited", sub_goals=["a"]),
                entry("gone"),
                entry("declined", sub_goals=["a"]),
                entry("orphan"),
            ],
        }
    )
    draft = _tree(
        **{
            "scenarios/kept/scenario.json": {"name": "kept", "sub_goals": ["a"]},
            "scenarios/edited/scenario.json": {
                "name": "edited",
                "sub_goals": ["a", "b"],
            },
            "scenarios/declined/scenario.json": {
                "name": "declined",
                "sub_goals": ["a", "c"],
            },
            "scenarios/new/scenario.json": {"name": "new"},
            "scenarios.json": [
                entry("kept", sub_goals=["a"]),
                entry("edited", sub_goals=["a", "b"]),
                entry("declined", sub_goals=["a", "c"]),
                entry("new"),
            ],
        }
    )
    changes = compute_changes(default, draft)
    composed = compose_accepted(
        default,
        draft,
        changes,
        _decide(changes, {"scenario:edited", "scenario:gone", "scenario:new"}),
    )

    index = {one["name"]: one for one in json.loads(composed["scenarios.json"])}
    assert list(index) == ["kept", "edited", "declined", "new"]
    assert index["edited"]["sub_goals"] == ["a", "b"]
    assert index["declined"]["sub_goals"] == ["a"]
    assert "scenarios/gone/scenario.json" not in composed


def test_an_unrecognized_amendment_line_is_dropped_when_a_contract_change_was_rejected():
    default, draft, changes = _rule_and_tool()
    contract = json.loads(draft["contract.json"])
    contract["amendments"].append("issue_refund narrowed after review")
    draft["contract.json"] = json.dumps(contract).encode()
    changes = compute_changes(default, draft)
    rule = next(one["id"] for one in changes if one["kind"] == "rule")

    rejected = json.loads(
        compose_accepted(
            default, draft, changes, {rule: "accepted", "tool:issue_refund": "rejected"}
        )["contract.json"]
    )
    assert "issue_refund narrowed after review" not in rejected["amendments"]
    accepted = json.loads(
        compose_accepted(
            default, draft, changes, {rule: "accepted", "tool:issue_refund": "accepted"}
        )["contract.json"]
    )
    assert "issue_refund narrowed after review" in accepted["amendments"]


def test_a_suite_index_entry_is_part_of_its_scenario_and_a_change_to_it_alone_is_reviewed():
    folder = {"name": "s", "sub_goals": ["a"]}
    default = _tree(**{"scenarios/s/scenario.json": folder, "scenarios.json": [folder]})
    draft = _tree(
        **{
            "scenarios/s/scenario.json": folder,
            "scenarios.json": [{**folder, "max_turns": 99}],
        }
    )

    [change] = compute_changes(default, draft)
    assert (change["id"], change["action"], change["fields"]) == (
        "scenario:s",
        "changed",
        ["_index"],
    )
    # Rejected, the saved suite keeps the reviewed index entry, not the draft's.
    kept = compose_accepted(default, draft, [change], {"scenario:s": "rejected"})
    assert json.loads(kept["scenarios.json"]) == [folder]
    taken = compose_accepted(default, draft, [change], {"scenario:s": "accepted"})
    assert json.loads(taken["scenarios.json"]) == [{**folder, "max_turns": 99}]


def test_an_index_entry_that_disagrees_with_its_scenario_or_has_no_folder_is_flagged():
    from simulate.services.harness_versioning.review_items import index_mismatch

    items = item_index(
        _tree(
            **{
                "scenarios/s/scenario.json": {"name": "s", "sub_goals": ["a"]},
                "scenarios.json": [
                    {"name": "s", "sub_goals": ["b"]},
                    {"name": "orphan", "sub_goals": ["a"]},
                ],
            }
        )
    )
    assert index_mismatch(items["scenario:s"].value) == ["sub_goals"]
    assert index_mismatch(items["scenario:orphan"].value) == ["_index"]


@pytest.mark.parametrize(
    "document,body",
    [
        (
            "actors.json",
            {"actors": [{"id": "a", "role": "x"}, {"id": "a", "role": "y"}]},
        ),
        ("actors.json", {"actors": [{"id": "a"}, {"role": "no id"}]}),
        ("sub_goals.json", {"sub_goals": [{"name": "g"}, {"name": "g", "check": "x"}]}),
        ("scenarios.json", [{"name": "s"}, {"name": "s", "max_turns": 3}]),
    ],
)
def test_a_document_with_duplicate_or_keyless_entries_is_reviewed_whole(document, body):
    default = _tree(**{document: {"actors": []} if document == "actors.json" else []})
    draft = _tree(**{document: body})

    changes = compute_changes(default, draft)
    assert [(one["id"], one["action"]) for one in changes] == [
        (f"file:{document}", "changed")
    ]
    composed = compose_accepted(
        default, draft, changes, {f"file:{document}": "accepted"}
    )
    assert json.loads(composed[document]) == body


def test_duplicate_tools_in_the_contract_review_the_contract_whole():
    default = _tree({"tools": TOOLS})
    draft = _tree({"tools": [*TOOLS, {**TOOLS[0], "description": "shadow"}]})

    assert [one["id"] for one in compute_changes(default, draft)] == [
        "file:contract.json"
    ]


def test_data_beside_a_listed_documents_entries_is_reviewed():
    actors = [{"id": "a"}]
    default = _tree(**{"actors.json": {"actors": actors, "limits": {"max": 1}}})
    draft = _tree(**{"actors.json": {"actors": actors, "limits": {"max": 9}}})

    assert [one["id"] for one in compute_changes(default, draft)] == [
        "file:actors.json"
    ]


def test_accepting_a_removal_while_rejecting_the_scenario_edit_it_needs_is_refused():
    from simulate.services.harness_versioning.references import depends_on
    from simulate.services.harness_versioning.review import check_review

    base = {"s": {"solution": [{"tool": "old_tool"}]}}
    draft = {"s": {"solution": [{"tool": "get_order"}]}}
    removal = {
        "id": "tool:old_tool",
        "kind": "tool",
        "key": "old_tool",
        "action": "removed",
    }
    edit = {"id": "scenario:s", "kind": "scenario", "key": "s", "action": "changed"}
    changes = [removal, edit]
    for one in changes:
        one["depends_on"] = depends_on(one, changes, draft, base)
    removal["decision"], edit["decision"] = "accepted", "rejected"

    with pytest.raises(Exception) as refused:
        check_review(
            {"changes": changes, "review_digest": "d"}, "d", digest_required=True
        )
    assert getattr(refused.value, "code", None) == "change_depends_on_rejected"


@pytest.mark.parametrize(
    "saved",
    [
        {"starts_with": {"actor": "manager", "says": "Approve it"}},
        {"pre_run": [{"actors": ["manager"], "thread": []}]},
        {"pre_run": [{"actors": [], "thread": [{"actor": "manager", "text": "Hi"}]}]},
    ],
    ids=["event_start", "pre_run_session", "pre_run_line"],
)
def test_removing_an_actor_a_scenario_names_only_outside_its_actors_list_needs_that_scenario_edit(
    saved,
):
    from simulate.services.harness_versioning.references import depends_on
    from simulate.services.harness_versioning.review import check_review

    base = {"s": saved}
    draft = {"s": {}}
    removal = {
        "id": "actor:manager",
        "kind": "actor",
        "key": "manager",
        "action": "removed",
    }
    edit = {"id": "scenario:s", "kind": "scenario", "key": "s", "action": "changed"}
    changes = [removal, edit]
    for one in changes:
        one["depends_on"] = depends_on(one, changes, draft, base)
    assert removal["depends_on"] == ["scenario:s"]
    removal["decision"], edit["decision"] = "accepted", "rejected"

    with pytest.raises(Exception) as refused:
        check_review(
            {"changes": changes, "review_digest": "d"}, "d", digest_required=True
        )
    assert getattr(refused.value, "code", None) == "change_depends_on_rejected"


def test_a_scenario_saved_with_a_suite_entry_its_document_disagrees_with_is_refused(
    monkeypatch,
):
    from simulate.services.harness_versioning import review

    # An entry whose scenario has no folder cannot be put in step: it is refused.
    files = _tree(
        **{
            "scenarios/other/scenario.json": {"name": "other"},
            "scenarios.json": [{"name": "s", "sub_goals": ["b"]}],
        }
    )
    monkeypatch.setattr(review, "open_draft", lambda environment, piece: object())
    monkeypatch.setattr(
        review, "piece_files", lambda environment, piece, version: files
    )
    change = {
        "id": "scenario:s",
        "kind": "scenario",
        "action": "changed",
        "decision": "accepted",
        "depends_on": [],
    }

    with pytest.raises(Exception) as refused:
        review.check_review(
            {"changes": [change], "review_digest": "d"},
            "d",
            digest_required=True,
            environment=object(),
        )
    assert getattr(refused.value, "code", None) == "scenario_index_mismatch"
    change["decision"] = "rejected"
    review.check_review(
        {"changes": [change], "review_digest": "d"},
        "d",
        digest_required=True,
        environment=object(),
    )


def test_a_draft_write_takes_the_suite_entry_from_an_edit_to_the_scenario_s_own_file(
    monkeypatch,
):
    from simulate.services.harness_versioning import archives, drafts

    fault = [{"action": "get_order", "mode": "timeout_before_commit", "on_call": 1}]
    files = _tree(
        **{
            "scenarios/s/scenario.json": {"name": "s", "faults": fault},
            "scenarios/t/scenario.json": {"name": "t", "faults": []},
            "scenarios.json": [
                {"name": "s", "faults": [], "listed_only": 1},
                {"name": "t", "faults": []},
            ],
        }
    )
    seen = {}

    def _stop(environment, tree):
        seen.update(tree)
        raise RuntimeError("stop")

    monkeypatch.setattr(drafts, "prepare_tree", _stop)
    with pytest.raises(RuntimeError):
        drafts.record_draft(object(), archives.pack_archive(files), origin="chat")

    assert json.loads(seen["scenarios.json"]) == [
        {"name": "s", "faults": fault, "listed_only": 1},
        {"name": "t", "faults": []},
    ]
    items = item_index(seen)
    assert not index_mismatch(items["scenario:s"].value)


def test_an_index_already_in_step_is_left_byte_for_byte():
    from simulate.services.harness_versioning.review_items import index_in_step

    files = _tree(
        **{
            "scenarios/s/scenario.json": {"name": "s", "faults": []},
            "scenarios.json": b'[{"name":"s","faults":[]}]',
        }
    )
    assert index_in_step(files)["scenarios.json"] == b'[{"name":"s","faults":[]}]'


def test_a_suite_draft_stored_before_index_in_step_reviews_and_saves_in_step(
    monkeypatch,
):
    from simulate.services.harness_versioning import review

    # Written before draft writes kept the index in step: the builder edited only the scenario's own file.
    files = _tree(
        **{
            "scenarios/s/scenario.json": {"name": "s", "faults": [{"on_call": 1}]},
            "scenarios.json": [{"name": "s", "faults": []}],
        }
    )
    monkeypatch.setattr(review, "open_draft", lambda environment, piece: object())
    monkeypatch.setattr(
        review, "piece_files", lambda environment, piece, version: files
    )
    change = {
        "id": "scenario:s",
        "kind": "scenario",
        "action": "changed",
        "decision": None,
        "depends_on": [],
    }
    review.check_review(
        {"changes": [change], "review_digest": "d"},
        "d",
        digest_required=False,
        environment=object(),
    )
    read = review.draft_files(object(), "suite", object())
    assert json.loads(read["scenarios.json"]) == [
        {"name": "s", "faults": [{"on_call": 1}]}
    ]


def test_an_actor_and_a_sub_actor_sharing_an_id_are_reviewed_as_their_files():
    default = _tree(
        **{
            "actors.json": {"actors": [{"id": "a", "name": "A"}]},
            "sub_actors.json": {"sub_actors": [{"id": "a", "name": "S"}]},
        }
    )
    edited_actor = {
        **default,
        "actors.json": json.dumps({"actors": [{"id": "a", "name": "A2"}]}).encode(),
    }
    edited_sub_actor = {
        **default,
        "sub_actors.json": json.dumps(
            {"sub_actors": [{"id": "a", "name": "S2"}]}
        ).encode(),
    }

    assert [one["id"] for one in compute_changes(default, edited_actor)] == [
        "file:actors.json"
    ]
    assert [one["id"] for one in compute_changes(default, edited_sub_actor)] == [
        "file:sub_actors.json"
    ]
    # Rejected, the edit is not saved; accepted, it is.
    [change] = compute_changes(default, edited_actor)
    kept = compose_accepted(default, edited_actor, [change], {change["id"]: "rejected"})
    assert kept["actors.json"] == default["actors.json"]
    taken = compose_accepted(
        default, edited_actor, [change], {change["id"]: "accepted"}
    )
    assert taken["actors.json"] == edited_actor["actors.json"]


def test_a_suite_in_the_older_scenario_folder_saves_the_accepted_edit_and_keeps_its_index():
    default = _tree(
        **{
            "scenario/s1/scenario.json": {"name": "s1", "x": 1},
            "scenario/s2/scenario.json": {"name": "s2", "x": 1},
            "scenarios.json": [{"name": "s1"}, {"name": "s2"}],
        }
    )
    draft = _tree(
        **{
            "scenario/s1/scenario.json": {"name": "s1", "x": 2},
            "scenario/s2/scenario.json": {"name": "s2", "x": 2},
            "scenarios.json": [{"name": "s1"}, {"name": "s2"}],
        }
    )
    changes = compute_changes(default, draft)
    assert [one["document"] for one in changes] == [
        "scenario/s1/scenario.json",
        "scenario/s2/scenario.json",
    ]

    saved = compose_accepted(
        default,
        draft,
        changes,
        {"scenario:s1": "accepted", "scenario:s2": "rejected"},
    )
    assert json.loads(saved["scenario/s1/scenario.json"]) == {"name": "s1", "x": 2}
    assert json.loads(saved["scenario/s2/scenario.json"]) == {"name": "s2", "x": 1}
    assert json.loads(saved["scenarios.json"]) == [{"name": "s1"}, {"name": "s2"}]


@pytest.mark.parametrize(
    ("before", "after"), [(b"{bad1", b"{bad2"), (b"[1]", b"[2]"), (b"{bad", b"[1]")]
)
def test_a_scenario_file_that_is_not_a_json_object_is_still_reviewed(before, after):
    default = {"scenarios/s1/scenario.json": before}
    draft = {"scenarios/s1/scenario.json": after}

    [change] = compute_changes(default, draft)
    assert (change["id"], change["action"]) == ("scenario:s1", "changed")
    saved = compose_accepted(default, draft, [change], {"scenario:s1": "accepted"})
    assert saved["scenarios/s1/scenario.json"] == after


def test_a_bare_list_of_actors_stays_a_bare_list_when_one_change_is_rejected():
    default = _tree(**{"actors.json": [{"id": "a", "n": 1}, {"id": "b", "n": 1}]})
    draft = _tree(**{"actors.json": [{"id": "a", "n": 2}, {"id": "b", "n": 2}]})

    changes = compute_changes(default, draft)
    saved = compose_accepted(
        default, draft, changes, {"actor:a": "accepted", "actor:b": "rejected"}
    )
    assert json.loads(saved["actors.json"]) == [
        {"id": "a", "n": 2},
        {"id": "b", "n": 1},
    ]


def test_an_edit_to_the_suite_index_alone_gives_way_to_the_scenario_s_own_file():
    from simulate.services.harness_versioning.review_items import index_in_step

    files = _tree(
        **{
            "scenarios/s/scenario.json": {"name": "s", "faults": []},
            "scenarios.json": [{"name": "s", "faults": [{"on_call": 1}], "extra": 1}],
        }
    )
    # The scenario's own file is what runs read as the scenario; a field only the index has is kept.
    assert json.loads(index_in_step(files)["scenarios.json"]) == [
        {"name": "s", "faults": [], "extra": 1}
    ]


def test_the_older_scenario_folder_is_read_only_when_the_suite_has_no_scenarios_folder():
    files = _tree(
        **{
            "scenarios/s1/scenario.json": {"name": "s1"},
            "scenario/old/scenario.json": {"name": "old"},
        }
    )
    # Like the suite hash and ALK: `scenarios/` wins, so a stray `scenario/` folder is not a scenario.
    assert [key for key in item_index(files) if key.startswith("scenario:")] == [
        "scenario:s1"
    ]
