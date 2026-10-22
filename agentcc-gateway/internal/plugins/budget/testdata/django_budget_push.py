"""Run Django's own budget functions on the Set Budget dialog's
bodies, with nothing imported from Django.

Usage: python3 -I django_budget_push.py <repo root>

Extracts, by AST, the set-budget storage functions from
futureagi/agentcc/views/gateway.py and every top-level budget function and
constant of futureagi/agentcc/services/config_push.py, runs the dialog saves
of each case (the same cases as futureagi/agentcc/tests/test_budget_push.py)
and prints {case: pushed budgets} as JSON, None values dropped as the push's
exclude_none dump drops them. The gateway's TestBudget* tests compare it with
testdata/console_push.json, so a transform that drifts from the fixture
fails on the Go side too.
"""

import ast
import json
import sys

root = sys.argv[1]
views = open(f"{root}/futureagi/agentcc/views/gateway.py").read()
push = open(f"{root}/futureagi/agentcc/services/config_push.py").read()


def extract(src, wanted):
    out = []
    for node in ast.parse(src).body:
        names = []
        if isinstance(node, ast.FunctionDef):
            names = [node.name]
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if any(wanted(n) for n in names):
            out.append(ast.get_source_segment(src, node))
    return "\n\n".join(out)


ns = {}
exec(  # noqa: S102 - the repository's own source
    extract(
        views,
        lambda n: n
        in {
            "_BUDGET_LEVEL_KEY_ALIASES",
            "_normalize_budget_level_key",
            "_set_budget_level_config",
            "_remove_budget_level_config",
        },
    ),
    ns,
)
exec(extract(push, lambda n: "budget" in n.lower()), ns)  # noqa: S102


class _Log:
    def __getattr__(self, _):
        return lambda *a, **k: None


ns.setdefault("logger", _Log())

T1 = "2026-10-21T08:00:00.000Z"
T2 = "2026-10-21T09:00:00.000Z"


def dialog(limit, on_exceed, alert=80, saved_at=T1, **extra):
    # SetBudgetDialog.jsx handleSave's config for these field values.
    return {
        "limit": limit,
        "alert_threshold": alert,
        "on_exceed": on_exceed,
        "saved_at": saved_at,
        **extra,
    }


CASES = {
    "org_block": ({}, [("org_limit", dialog(0.000001, "block"))]),
    "org_throttle": ({}, [("org_limit", dialog(0.000001, "throttle", throttle_rpm=2))]),
    "org_alert_50": ({}, [("org_limit", dialog(100, "warn", alert=50))]),
    "hard_limit_later": (
        {},
        [
            ("org_limit", dialog(100, "block", saved_at=T1)),
            ("hard_limit", dialog(0.000001, "block", alert=90, saved_at=T2)),
        ],
    ),
    "org_limit_later": (
        {},
        [
            ("hard_limit", dialog(0.000001, "block", alert=90, saved_at=T1)),
            ("org_limit", dialog(100, "block", saved_at=T2)),
        ],
    ),
    "explicit_off": ({"enabled": False}, [("org_limit", dialog(0.000001, "block"))]),
}


def drop_none(v):
    if isinstance(v, dict):
        return {k: drop_none(x) for k, x in v.items() if x is not None}
    return v


out = {}
for case, (stored, saves) in CASES.items():
    budgets = stored
    for level, config in saves:
        budgets = ns["_set_budget_level_config"](budgets, level, config)
    out[case] = drop_none(ns["_transform_budgets"](budgets))
print(json.dumps(out, sort_keys=True))
