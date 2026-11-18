from simulate.services.harness_versioning.references import (
    ALL,
    affects,
    depends_on,
    scenario_uses,
)

SCENARIOS = {
    "refund-in-window": {
        "solution": [{"tool": "get_order"}, {"tool": "issue_refund"}],
        "alternatives": [[{"tool": "get_order"}, {"tool": "escalate"}]],
        "sub_goals": ["one_refund", "explains_policy"],
        "actors": [{"id": "manager"}],
    },
    "status-only": {
        "solution": [{"tool": "get_order"}],
        "sub_goals": ["reports_status"],
    },
}


def _change(kind, key, action="changed"):
    return {"id": f"{kind}:{key}", "kind": kind, "key": key, "action": action}


def test_a_scenario_names_what_its_solution_alternatives_sub_goals_and_actors_use():
    uses = scenario_uses(SCENARIOS["refund-in-window"])
    assert uses == {
        "tool": {"get_order", "issue_refund", "escalate"},
        "sub_goal": {"one_refund", "explains_policy"},
        "actor": {"manager"},
    }


def test_a_change_touches_the_scenarios_that_use_it_and_everything_when_nothing_says_which():
    assert affects(_change("tool", "get_order"), SCENARIOS) == [
        "refund-in-window",
        "status-only",
    ]
    assert affects(_change("tool", "escalate"), SCENARIOS) == ["refund-in-window"]
    assert affects(_change("sub_goal", "reports_status"), SCENARIOS) == ["status-only"]
    assert affects(_change("actor", "manager"), SCENARIOS) == ["refund-in-window"]
    assert affects(_change("actor", "auditor"), SCENARIOS) == []
    assert affects(_change("scenario", "status-only"), SCENARIOS) == ["status-only"]
    for kind in ("rule", "contract_field", "file", "source"):
        assert affects(_change(kind, "x"), SCENARIOS) == ALL


def test_a_scenario_needs_the_tools_sub_goals_and_actors_the_draft_adds_or_changes():
    changes = [
        _change("scenario", "refund-in-window", "added"),
        _change("tool", "issue_refund", "added"),
        _change("tool", "get_order", "changed"),
        _change("sub_goal", "one_refund", "added"),
        _change("actor", "auditor", "added"),
    ]
    assert depends_on(changes[0], changes, SCENARIOS) == [
        "sub_goal:one_refund",
        "tool:get_order",
        "tool:issue_refund",
    ]
    assert (
        depends_on(
            _change("scenario", "refund-in-window", "removed"), changes, SCENARIOS
        )
        == []
    )
    assert depends_on(changes[1], changes, SCENARIOS) == []


def test_save_refuses_an_accepted_change_that_needs_a_rejected_one(monkeypatch):
    import pytest

    from simulate.services.harness_versioning import review
    from simulate.services.hosted_harness import HostedHarnessError

    changes = [
        {
            **_change("scenario", "refund-in-window", "added"),
            "piece": "suite",
            "decision": "accepted",
            "depends_on": ["tool:issue_refund"],
        },
        {
            **_change("tool", "issue_refund", "added"),
            "piece": "environment",
            "decision": "rejected",
            "depends_on": [],
        },
    ]
    monkeypatch.setattr(
        review, "draft_review", lambda _env: {"changes": changes, "review_digest": "d"}
    )
    monkeypatch.setattr(review, "heal_suite_index", lambda _env: None)
    with pytest.raises(HostedHarnessError) as refused:
        review.apply_review(None, {"suite", "environment"}, expected_review_digest=None)
    assert refused.value.code == "change_depends_on_rejected"
    assert refused.value.details["pairs"] == [
        {"change": "scenario:refund-in-window", "needs": "tool:issue_refund"}
    ]


def test_removing_a_tool_touches_the_scenarios_that_named_it_before_the_draft():
    draft = {"s": {"solution": [{"tool": "get_order"}]}}
    base = {"s": {"solution": [{"tool": "get_order"}, {"tool": "old_tool"}]}}
    removal = _change("tool", "old_tool", "removed")

    assert affects(removal, draft, base) == ["s"]
    assert affects(removal, draft) == []


def test_removing_a_tool_needs_the_scenario_edit_that_stops_using_it():
    draft = {"s": {"solution": [{"tool": "get_order"}]}}
    base = {"s": {"solution": [{"tool": "get_order"}, {"tool": "old_tool"}]}}
    removal = _change("tool", "old_tool", "removed")
    edit = _change("scenario", "s")
    unrelated = _change("scenario", "t")

    assert depends_on(removal, [removal, edit, unrelated], draft, base) == [
        "scenario:s"
    ]


def test_a_graded_scenario_edit_needs_the_changes_to_definitions_it_names_and_a_rename_needs_none():
    changes = [
        {**_change("scenario", "refund-in-window", "changed"), "fields": ["solution"]},
        _change("tool", "get_order", "changed"),
        _change("tool", "issue_refund", "added"),
    ]
    assert depends_on(changes[0], changes, SCENARIOS) == [
        "tool:get_order",
        "tool:issue_refund",
    ]
    renamed = {**_change("scenario", "refund-in-window", "changed"), "fields": ["name"]}
    assert depends_on(renamed, changes, SCENARIOS) == ["tool:issue_refund"]


def _j(value):
    import json

    return json.dumps(value).encode()


def test_a_whole_file_suite_index_and_each_scenario_change_need_each_other():
    """Review round 1, B5: with scenarios.json reviewed as one file (an entry with no name), rejecting the removal of
    scenario x while accepting the index would drop x from the saved suite. The two are decided together."""
    from simulate.services.harness_versioning.review_items import compute_changes

    default = {
        "scenarios.json": _j([{"name": "a"}, {"name": "x"}, {"description": "orphan"}]),
        "scenarios/a/scenario.json": _j({"name": "a"}),
        "scenarios/x/scenario.json": _j({"name": "x"}),
    }
    draft = {
        "scenarios.json": _j([{"name": "a"}, {"description": "orphan"}]),
        "scenarios/a/scenario.json": _j({"name": "a"}),
    }
    changes = compute_changes(default, draft)
    by_id = {one["id"]: one for one in changes}
    assert set(by_id) == {"file:scenarios.json", "scenario:x"}

    assert depends_on(by_id["file:scenarios.json"], changes, {}) == ["scenario:x"]
    assert depends_on(by_id["scenario:x"], changes, {}) == ["file:scenarios.json"]


def test_removing_a_tool_a_saved_scenario_sets_a_fault_on_needs_its_scenario_edit():
    """Review round 1, SF11: a fault on the tool, or an actor brought in on it, makes the scenario name the tool."""
    removal = _change("tool", "issue_refund", "removed")
    edit = {**_change("scenario", "s1"), "fields": ["faults"]}
    faulted = {
        "s1": {
            "name": "s1",
            "faults": [{"action": "issue_refund", "mode": "timeout_before_commit"}],
        }
    }
    on_tool = {
        "s1": {
            "name": "s1",
            "actors": [
                {
                    "id": "manager",
                    "trigger": {"kind": "on_tool", "tool": "issue_refund"},
                }
            ],
        }
    }

    assert depends_on(removal, [removal, edit], {"s1": {"name": "s1"}}, faulted) == [
        "scenario:s1"
    ]
    assert depends_on(removal, [removal, edit], {"s1": {"name": "s1"}}, on_tool) == [
        "scenario:s1"
    ]


def test_removing_a_tool_an_actor_is_brought_in_on_needs_that_actor_s_edit():
    """Review round 1, SF12: the saved actor joins on the tool; removing the tool needs the actor edit."""
    from simulate.services.harness_versioning.review_items import compute_changes

    tools = [{"name": "lookup"}, {"name": "issue_refund"}]
    actor = {
        "id": "manager",
        "activation": {"trigger": {"kind": "on_tool", "tool": "issue_refund"}},
    }
    default = {
        "contract.json": _j({"tools": tools}),
        "actors.json": _j({"actors": [actor]}),
    }
    draft = {
        "contract.json": _j({"tools": tools[:1]}),
        "actors.json": _j(
            {"actors": [{**actor, "activation": {"trigger": {"kind": "always"}}}]}
        ),
    }
    changes = compute_changes(default, draft)
    by_id = {one["id"]: one for one in changes}
    assert set(by_id) == {"tool:issue_refund", "actor:manager"}

    needed = depends_on(
        by_id["tool:issue_refund"], changes, {}, {}, base_actors={"manager": actor}
    )

    assert needed == ["actor:manager"]


def test_a_scenario_uses_what_alks_schema_names_and_nothing_else():
    """Scheduled actions and an actor's own activation set it going on a tool; an approval fault waits on its action;
    keys ALK's schema does not have (a step's `name`, a fault's `tool`, a sub-goal object) name nothing."""
    from simulate.services.harness_versioning.references import scenario_uses

    scenario = {
        "solution": [{"tool": "lookup"}, {"name": "not_a_tool_key"}],
        "faults": [{"action": "refund", "approval": "approve_refund"}, {"tool": "ignored"}],
        "sub_goals": ["paid", {"name": "not_alks_shape"}],
        "actors": [
            {"id": "manager", "actions": [{"trigger": {"kind": "on_tool", "tool": "escalate"}}]},
            {"id": "auditor"},
        ],
    }
    actors = {"auditor": {"activation": {"trigger": {"kind": "on_tool", "tool": "audit"}}}}

    uses = scenario_uses(scenario, actors)

    assert uses["tool"] == {"lookup", "refund", "approve_refund", "escalate", "audit"}
    assert uses["sub_goal"] == {"paid"}
    assert uses["actor"] == {"manager", "auditor"}
