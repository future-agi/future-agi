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

    where = ("tool", "status", "reset_between_trials", "runs_at", "writes", "effect")
    assert {key: rows["issue_refund"][key] for key in where} == {
        "tool": "issue_refund",
        "status": "simulated",
        "reset_between_trials": True,
        "runs_at": "orders POST /orders/refund/",
        "writes": True,
        "effect": "write",
    }
    assert {key: rows["lookup_order"][key] for key in where} == {
        "tool": "lookup_order",
        "status": "in_process",
        "reset_between_trials": False,
        "runs_at": None,
        "writes": False,
        "effect": "read",
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
    assert fault_targets("nope", RULES, []) == []


def test_fault_targets_name_the_route_as_the_world_action():
    targets = _by_tool(fault_targets(CONTRACT, RULES, []))

    named = ("tool", "action", "reachable", "writes")
    assert {key: targets["issue_refund"][key] for key in named} == {
        "tool": "issue_refund",
        "action": "orders.refund",
        "reachable": True,
        "writes": True,
    }
    assert {key: targets["lookup_order"][key] for key in named} == {
        "tool": "lookup_order",
        "action": "lookup_order",
        "reachable": False,
        "writes": False,
    }


def test_without_entrypoints_the_tools_are_offered_by_name():
    contract = {"tools": [{"name": "a"}, "b"], "tool_entrypoints": []}

    assert [
        (one["tool"], one["action"], one["reachable"], one["writes"], one["modes"])
        for one in fault_targets(contract, RULES, [])
    ] == [("a", "a", False, False, []), ("b", "b", False, False, [])]


def test_a_fault_on_a_tool_inside_the_agent_cannot_work():
    problem = door_problem(
        faults=[{"action": "lookup_order", "mode": "unavailable"}],
        actors=[],
        targets=door_targets(CONTRACT, RULES, []),
    )

    assert problem is not None
    assert problem["code"] == "fault_unreachable"
    assert "lookup_order" in problem["message"]


def test_a_fault_named_by_its_route_on_a_reachable_tool_works():
    assert (
        door_problem(
            faults=[{"action": "orders.refund", "mode": "unavailable"}],
            actors=[{"id": "boss", "actions": [{"action": "orders.refund"}]}],
            targets=door_targets(CONTRACT, RULES, []),
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
        targets=door_targets(contract, RULES, []),
    )
    speaking = door_problem(
        faults=[],
        actors=[{"id": "friend", "instruction": "hi"}],
        targets=door_targets(contract, RULES, []),
    )

    assert acting == {"code": "actor_no_door", "message": NO_WORLD_DOOR}
    assert speaking is None


def test_a_contract_that_says_nothing_of_the_door_raises_nothing():
    assert (
        door_problem(
            faults=[{"action": "x", "mode": "unavailable"}],
            actors=[{"id": "a", "actions": [{"action": "x"}]}],
            targets=door_targets({"tools": ["x"]}, RULES, []),
        )
        is None
    )
    assert (
        door_problem(
            faults="junk", actors="junk", targets=door_targets(CONTRACT, RULES, [])
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

    targets = {one["tool"]: one["action"] for one in fault_targets(contract, rules, [])}
    problem = door_problem(
        faults=[{"action": "orders_hold", "mode": "unavailable"}],
        actors=[],
        targets=door_targets(contract, rules, []),
    )

    assert (targets["issue_refund"], targets["hold"]) == (
        "orders_refund",
        "orders_hold",
    )
    # A fault named the sealed way is found, so its unreachable tool reads as a problem.
    assert problem is not None and problem["code"] == "fault_unreachable"


def test_an_argument_s_unit_reaches_the_tools_card_as_the_contract_writes_it():
    contract = {
        "tools": [
            {
                "name": "issue_refund",
                "args": [
                    "amount",
                    {"name": "currency", "type": "string", "unit": "ISO 4217 code"},
                    "reason",
                ],
                "arg_types": {"amount": "integer"},
                "arg_units": {"amount": "cents, USD"},
            }
        ]
    }

    assert tool_status(contract)[0]["arguments"] == [
        {"name": "amount", "type": "integer", "unit": "cents, USD"},
        {"name": "currency", "type": "string", "unit": "ISO 4217 code"},
        {"name": "reason", "type": None, "unit": None},
    ]


def test_the_tables_a_tool_reads_and_changes_are_shown_when_the_contract_declares_them():
    declared = {
        "name": "issue_refund",
        "tables_read": ["orders"],
        "tables_changed": ["refunds", "orders"],
    }
    rows = _by_tool(tool_status({"tools": [declared, {"name": "audit"}]}))

    tables = rows["issue_refund"]["tables"]
    assert (tables["recorded"], tables["read"], tables["changed"]) == (
        True,
        ["orders"],
        ["refunds", "orders"],
    )
    assert (rows["audit"]["tables"]["recorded"], rows["audit"]["tables"]["read"]) == (
        False,
        [],
    )
