"""Where each contract tool runs, which tools a fault can name, and why a scenario cannot work as set."""

from __future__ import annotations

from simulate.services.harness_actor_rules import actor_rules
from simulate.services.harness_tool_reach import (
    NO_WORLD_DOOR,
    door_problem,
    door_targets,
    fault_targets,
    tool_status,
)

# The first sealing release's rules: routes named with `/` read as `.`, POST routes only.
RULES = actor_rules({})

# A tools service serves refund (behind the world's door); lookup runs inside the agent as a closure; notify reaches
# someone else's API; audit has no entrypoint the build could read.
CONTRACT = {
    "runtime": {"language": "python"},
    "tools": [
        {"name": "issue_refund"},
        "lookup_order",
        {"name": "notify"},
        {"name": "audit"},
    ],
    "tool_entrypoints": [
        {
            "tool": "issue_refund",
            "mode": "service",
            "service": "orders",
            "endpoint": "/orders/refund/",
            "effect": "write",
        },
        {"tool": "lookup_order", "mode": "closure", "effect": "read"},
        {
            "tool": "notify",
            "mode": "import",
            "module": "agent.tools",
            "callable": "notify",
        },
    ],
    "dependencies": [
        {
            "name": "mailer",
            "reached": {"url": "https://mail.example.com"},
            "used_by": ["notify"],
        }
    ],
}


def _by_tool(rows):
    return {row["tool"]: row for row in rows}


def test_each_tool_says_where_it_runs():
    rows = _by_tool(tool_status(CONTRACT))

    assert rows["issue_refund"] == {
        "tool": "issue_refund",
        "status": "simulated",
        "reset_between_trials": True,
        "runs_at": "orders POST /orders/refund/",
        "writes": True,
    }
    assert rows["lookup_order"] == {
        "tool": "lookup_order",
        "status": "in_process",
        "reset_between_trials": False,
        "runs_at": None,
        "writes": False,
    }
    # Reaching someone else's API wins over being behind the door.
    assert rows["notify"]["status"] == "unsupported"
    assert rows["notify"]["reset_between_trials"] is None
    assert rows["notify"]["runs_at"] == "agent.tools.notify"
    # Nothing read about where it runs.
    assert rows["audit"]["status"] == "unknown"
    assert rows["audit"]["reset_between_trials"] is None


def test_a_stated_method_is_kept():
    contract = {
        "tools": ["ping"],
        "tool_entrypoints": [
            {"tool": "ping", "mode": "service", "endpoint": "ping", "method": "get"}
        ],
    }

    assert tool_status(contract)[0]["runs_at"] == "GET /ping"


def test_a_contract_that_is_not_one_has_no_tools():
    assert tool_status(None) == []
    assert fault_targets("nope", RULES) == []


def test_fault_targets_name_the_route_as_the_world_action():
    targets = _by_tool(fault_targets(CONTRACT, RULES))

    assert targets["issue_refund"] == {
        "tool": "issue_refund",
        "action": "orders.refund",
        "reachable": True,
        "writes": True,
    }
    assert targets["lookup_order"] == {
        "tool": "lookup_order",
        "action": "lookup_order",
        "reachable": False,
        "writes": False,
    }


def test_without_entrypoints_the_tools_are_offered_by_name():
    contract = {"tools": [{"name": "a"}, "b"], "tool_entrypoints": []}

    assert fault_targets(contract, RULES) == [
        {"tool": "a", "action": "a", "reachable": False, "writes": False},
        {"tool": "b", "action": "b", "reachable": False, "writes": False},
    ]


def test_a_fault_on_a_tool_inside_the_agent_cannot_work():
    problem = door_problem(
        faults=[{"action": "lookup_order", "mode": "unavailable"}],
        actors=[],
        targets=door_targets(CONTRACT, RULES),
    )

    assert problem is not None
    assert problem["code"] == "fault_unreachable"
    assert "lookup_order" in problem["message"]


def test_a_fault_named_by_its_route_on_a_reachable_tool_works():
    assert (
        door_problem(
            faults=[{"action": "orders.refund", "mode": "unavailable"}],
            actors=[{"id": "boss", "actions": [{"action": "orders.refund"}]}],
            targets=door_targets(CONTRACT, RULES),
        )
        is None
    )


def test_an_acting_actor_needs_a_door_somewhere():
    contract = {
        "tools": ["lookup_order"],
        "tool_entrypoints": [{"tool": "lookup_order", "mode": "closure"}],
    }

    acting = door_problem(
        faults=[],
        actors=[
            {"id": "clerk", "trigger": {"kind": "on_tool", "tool": "lookup_order"}}
        ],
        targets=door_targets(contract, RULES),
    )
    speaking = door_problem(
        faults=[],
        actors=[{"id": "friend", "instruction": "hi"}],
        targets=door_targets(contract, RULES),
    )

    assert acting == {"code": "actor_no_door", "message": NO_WORLD_DOOR}
    assert speaking is None


def test_a_contract_that_says_nothing_of_the_door_raises_nothing():
    assert (
        door_problem(
            faults=[{"action": "x", "mode": "unavailable"}],
            actors=[{"id": "a", "actions": [{"action": "x"}]}],
            targets=door_targets({"tools": ["x"]}, RULES),
        )
        is None
    )
    assert (
        door_problem(
            faults="junk", actors="junk", targets=door_targets(CONTRACT, RULES)
        )
        is None
    )


def test_a_fault_action_follows_the_sealed_naming():
    rules = {**RULES, "world_actions": {**RULES["world_actions"], "separator": "_"}}
    # A route the door could not stand in front of: the agent started as a script.
    contract = {
        **CONTRACT,
        "tool_entrypoints": [
            *CONTRACT["tool_entrypoints"],
            {
                "tool": "hold",
                "mode": "import",
                "module": "agent.tools",
                "callable": "hold",
                "door_unreached": True,
                "endpoint": "/orders/hold",
            },
        ],
    }

    targets = {one["tool"]: one["action"] for one in fault_targets(contract, rules)}
    problem = door_problem(
        faults=[{"action": "orders_hold", "mode": "unavailable"}],
        actors=[],
        targets=door_targets(contract, rules),
    )

    assert (targets["issue_refund"], targets["hold"]) == (
        "orders_refund",
        "orders_hold",
    )
    # A fault named the sealed way is found, so its unreachable tool reads as a problem.
    assert problem is not None and problem["code"] == "fault_unreachable"
