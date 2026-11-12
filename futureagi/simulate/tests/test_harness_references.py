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
        "sub_goals": ["one_refund", {"name": "explains_policy"}],
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


def test_a_scenario_needs_the_tools_sub_goals_and_actors_only_the_draft_adds():
    changes = [
        _change("scenario", "refund-in-window", "added"),
        _change("tool", "issue_refund", "added"),
        _change("tool", "get_order", "changed"),
        _change("sub_goal", "one_refund", "added"),
        _change("actor", "auditor", "added"),
    ]
    assert depends_on(changes[0], changes, SCENARIOS) == [
        "sub_goal:one_refund",
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
