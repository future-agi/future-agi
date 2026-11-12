"""RD-03 controls adapted from EE tests/cap_scheduler_controls.py at 624d2044b7.

Run: python3 -I -B cap_scheduler_controls.py /absolute/path/to/FA/billing.py \
    --ee-root /absolute/path/to/ee-billing-trust-708
The EE checkout is read-only input. No FA/Django imports: execute the scheduler
activity AST and real EE evaluator/producer with synthetic storage boundaries.
These two controls and their assertions are retained from the EE-authored tests.
"""

import argparse
import ast
import runpy
import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock


def deny_external(event, args):
    if event.startswith(("socket.", "subprocess.")) or event in (
        "os.system",
        "os.posix_spawn",
    ):
        raise AssertionError(f"External IO forbidden: {event}")
    if event == "import" and args[0].split(".")[0] in {
        "django",
        "stripe",
        "ee",
        "accounts",
    }:
        raise AssertionError(f"Application import forbidden: {args[0]}")


sys.addaudithook(deny_external)
# Synthetic audit events prove refusal without opening a socket or importing Django.
for audit_event, audit_args in (
    ("socket.connect", (None, ("203.0.113.1", 443))),
    ("import", ("django", None, None, None, None)),
):
    try:
        sys.audit(audit_event, *audit_args)
    except AssertionError:
        pass
    else:
        raise AssertionError("source-control IO guard did not refuse")

ARGS = argparse.ArgumentParser()
ARGS.add_argument("scheduler_source", type=Path)
ARGS.add_argument("--ee-root", type=Path, required=True)
OPTIONS, REST = ARGS.parse_known_args()
F = runpy.run_path(str(OPTIONS.ee_root.resolve() / "tests/cap_action_fixtures.py"))
ActionHarness, MutableQuery, Row, load, PAUSE, REASON = (
    F[k] for k in ("ActionHarness", "MutableQuery", "Row", "load", "PAUSE", "REASON")
)


class Q:
    def __init__(self, **kwargs):
        self.matches = lambda row: all(
            getattr(row, key) == value for key, value in kwargs.items()
        )

    def __invert__(self):
        result = Q()
        result.matches = lambda row: not self.matches(row)
        return result

    def __and__(self, other):
        result = Q()
        result.matches = lambda row: self.matches(row) and other.matches(row)
        return result


class ScheduleQuery(MutableQuery):
    def filter(self, **kwargs):
        result = super().filter(**kwargs)
        return ScheduleQuery(result.rows, self.queries)

    def exclude(self, *conditions, **kwargs):
        checks = [*conditions, Q(**kwargs)]
        return ScheduleQuery(
            [r for r in self.rows if not all(q.matches(r) for q in checks)],
            self.queries,
        )

    def values_list(self, *keys, flat=False):
        if flat:
            assert len(keys) == 1
            return ScheduleQuery([getattr(row, keys[0]) for row in self.rows])
        return ScheduleQuery(
            [tuple(getattr(row, key) for key in keys) for row in self.rows]
        )

    def distinct(self):
        return ScheduleQuery(list(dict.fromkeys(self.rows)))


class Clock:
    @staticmethod
    def now(**kwargs):
        return datetime(2026, 1, 15, tzinfo=UTC)


def attach_scheduler(h):
    h.env["UsageBudget"].objects = ScheduleQuery([h.budget])
    # EE5 owns scheduled discovery; bind its actual body, not a successful stub.
    load(
        "billing/budget_enforcement.py",
        {"evaluate_total_spend_budgets_catchup"},
        h.env,
    )
    h.env["evaluate_total_spend_budgets_catchup"] = Mock(
        wraps=h.env["evaluate_total_spend_budgets_catchup"]
    )
    env = dict(
        h.env,
        Q=Q,
        # FA's normal hook modernizes timezone.utc to its identical UTC alias.
        UTC=UTC,
        datetime=Clock,
        temporal_activity=lambda **kw: lambda fn: fn,
        evaluate_budgets_catchup=Mock(),
        Organization=NS(),
        _total_spend_budgets_enforced=lambda: True,
    )

    class Imports(ast.NodeTransformer):
        def visit_ImportFrom(self, node):
            # Preserve local name binding in try/import/except fallback blocks.
            return [
                ast.copy_location(
                    ast.Assign(
                        targets=[
                            ast.Name(id=alias.asname or alias.name, ctx=ast.Store())
                        ],
                        value=ast.Subscript(
                            value=ast.Name(id="__boundaries", ctx=ast.Load()),
                            slice=ast.Constant(alias.name),
                            ctx=ast.Load(),
                        ),
                    ),
                    node,
                )
                for alias in node.names
            ]

    source = OPTIONS.scheduler_source.resolve()
    tree = ast.parse(source.read_text(), filename=str(source))
    tree.body = [
        n
        for n in tree.body
        if getattr(n, "name", None) == "evaluate_budgets_catchup_activity"
    ]
    env["__boundaries"] = dict(env)
    exec(
        compile(ast.fix_missing_locations(Imports().visit(tree)), str(source), "exec"),
        env,
    )
    return env


class SchedulerRecoveryControls(unittest.TestCase):
    def test_scheduled_already_triggered_cap_recovers_failure_pause(self):
        h = ActionHarness()
        env = attach_scheduler(h)
        activity = env["evaluate_budgets_catchup_activity"]
        h.env["calculate_current_period_spend"] = Mock(
            side_effect=ValueError("synthetic")
        )
        for _ in range(3):
            self.assertEqual(activity()["evaluated"], 1)
        self.assertEqual(h.redis.get(REASON), "CAP_EVALUATION_UNAVAILABLE")
        h.env["calculate_current_period_spend"] = Mock(return_value=F["D"]("10"))
        self.assertEqual(activity()["evaluated"], 1)
        self.assertEqual(h.budget.last_triggered_period, "2026-01")
        h.env["calculate_current_period_spend"] = Mock(return_value=F["D"]("4"))
        self.assertEqual(activity()["evaluated"], 1)
        self.assertNotIn(PAUSE, h.redis.data)
        h.notify.assert_called_once()

    def test_current_total_spend_is_eligible_other_scopes_keep_dedup(self):
        h = ActionHarness()
        env = attach_scheduler(h)
        h.budget.last_triggered_period = "2026-01"
        rows = h.env["UsageBudget"].objects.rows
        for overrides in (
            {"id": "deleted", "deleted": True},
            {"id": "inactive", "is_active": False},
            {"id": "fired-storage", "scope": "storage"},
            {"id": "fresh-storage", "scope": "storage", "last_triggered_period": ""},
            {"id": "second-cap"},
        ):
            rows.append(Row(**{**vars(h.budget), **overrides}))
        h.env["calculate_current_period_spend"] = Mock(return_value=F["D"]("4"))
        self.assertEqual(env["evaluate_budgets_catchup_activity"]()["evaluated"], 2)
        h.env["calculate_current_period_spend"].assert_called_once()
        env["evaluate_budgets_catchup"].assert_called_once_with(
            "org-synthetic", "storage", "2026-01"
        )


if __name__ == "__main__":
    unittest.main(argv=[__file__, *REST], verbosity=2)
