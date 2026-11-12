"""Socket-free controls for the real admission functions (no Django import).

Run with python3 -I <this file>. Runtime schema/Redis integration is separately
owed on lineage-2; these controls execute the source with synthetic collaborators.
"""

import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "ee/usage/services/metering.py"


def load_metering(redis):
    tree = ast.parse(SOURCE.read_text())
    # Keep the actual function bodies, but don't bootstrap Django or Redis.
    tree.body = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    config = SimpleNamespace(
        get_call_type=lambda _: SimpleNamespace(dimension="evals", per_call=1),
        get_plan=lambda _: SimpleNamespace(usage_caps="soft"),
    )
    ns = {
        "CheckResult": lambda **kw: (
            SimpleNamespace(error_code="", **kw)
            if "error_code" not in kw
            else SimpleNamespace(**kw)
        ),
        "BillingConfig": SimpleNamespace(get=lambda: config),
        "get_redis": lambda: redis,
        "logger": Mock(),
    }
    exec(
        compile(
            tree,
            str(SOURCE),
            "exec",
            flags=__import__("__future__").annotations.compiler_flag,
        ),
        ns,
    )
    ns.update(
        _get_cached_billing_status=lambda _: "active",
        _get_cached_plan=lambda _: "payg",
        _get_current_period=lambda: "2099-01",
    )
    return ns, config


class OrgPauseControls(unittest.TestCase):
    def setUp(self):
        self.redis = Mock()
        self.redis.get.return_value = None
        self.ns, self.config = load_metering(self.redis)
        self.modules = patch.dict(
            sys.modules,
            {
                "ee.usage.services.balance_cache": SimpleNamespace(
                    admission_refusal=Mock(return_value=None)
                ),
                "ee.usage.models.usage": SimpleNamespace(
                    OrganizationStatusChoices=SimpleNamespace(UNPAID="unpaid")
                ),
            },
        )
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def check(self, event="turing_large_evaluator"):
        return self.ns["check_usage"]("synthetic-org", event)

    def test_total_spend_pause_refuses_billable_call_and_ingestion(self):
        self.redis.get.side_effect = lambda key: (
            b"1" if key == "pause:synthetic-org:total_spend" else None
        )
        for event in ("turing_large_evaluator", "tracing_event", "observe_add"):
            with self.subTest(event=event):
                result = self.check(event)
                self.assertFalse(
                    result.allowed, "org-wide pause must block every billable action"
                )
                self.assertEqual(result.error_code, "BUDGET_PAUSED")

    def test_unknown_event_does_not_bypass_org_pause(self):
        self.config.get_call_type = Mock(side_effect=KeyError("synthetic"))
        self.redis.get.return_value = b"1"
        self.assertFalse(self.check("unknown_event").allowed)

    def test_pause_read_failure_is_typed_refusal(self):
        self.redis.get.side_effect = RuntimeError("synthetic store unavailable")
        result = self.check()
        self.assertFalse(result.allowed)
        self.assertEqual(result.error_code, "BUDGET_CHECK_UNAVAILABLE")

    def test_unpaused_unchanged_and_fresh_read_for_each_action(self):
        self.assertTrue(self.check().allowed)
        self.assertTrue(self.check().allowed)
        reads = [c.args[0] for c in self.redis.get.call_args_list]
        self.assertEqual(reads.count("pause:synthetic-org:total_spend"), 2)

    def test_dimension_pause_still_refuses(self):
        self.redis.get.side_effect = lambda key: "1" if key.endswith(":evals") else None
        result = self.check()
        self.assertFalse(result.allowed)
        self.assertEqual(result.error_code, "BUDGET_PAUSED")

    def test_other_org_pause_does_not_leak(self):
        self.redis.get.side_effect = lambda key: (
            "1" if key == "pause:other-org:total_spend" else None
        )
        self.assertTrue(self.check().allowed)


def deny_sockets(event, args):
    if event.startswith("socket."):
        raise PermissionError("socket-free control boundary")


if __name__ == "__main__":
    sys.addaudithook(deny_sockets)
    try:
        sys.audit("socket.connect", None, ("synthetic", 443))
    except PermissionError:
        pass
    else:
        raise AssertionError("socket guard did not refuse")
    unittest.main()
