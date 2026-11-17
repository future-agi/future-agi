"""Pure-Python recording admission with real pause checks and synthetic stores."""

import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "simulate/temporal/utils/rehost_admission.py"


def load(path, ns):
    tree = ast.parse(path.read_text())
    tree.body = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
    exec(
        compile(
            tree,
            str(path),
            "exec",
            flags=__import__("__future__").annotations.compiler_flag,
        ),
        ns,
    )
    return ns


class AdmissionControls(unittest.TestCase):
    def setUp(self):
        self.redis = Mock()
        self.redis.get.return_value = None
        config = SimpleNamespace(
            get_call_type=lambda _: SimpleNamespace(dimension="storage", per_call=1),
            get_plan=lambda _: SimpleNamespace(usage_caps="soft"),
        )
        meter = load(
            ROOT / "ee/usage/services/metering.py",
            {
                "CheckResult": lambda **kw: SimpleNamespace(
                    **({"error_code": "", "reason": ""} | kw)
                ),
                "BillingConfig": SimpleNamespace(get=lambda: config),
                "get_redis": lambda: self.redis,
                "logger": Mock(),
            },
        )
        meter.update(
            _get_cached_billing_status=lambda _: "active",
            _get_cached_plan=lambda _: "payg",
            _get_current_period=lambda: "2099-01",
        )
        self.check = Mock(side_effect=meter["check_usage"])
        self.mode = SimpleNamespace(is_oss=Mock(return_value=False))
        self.manager = Mock()
        self.manager.filter.return_value.values_list.return_value.first.return_value = (
            "synthetic-org"
        )
        self.modules = patch.dict(
            sys.modules,
            {
                "ee.usage.deployment": SimpleNamespace(DeploymentMode=self.mode),
                "ee.usage.services.metering": SimpleNamespace(check_usage=self.check),
                "ee.usage.services.balance_cache": SimpleNamespace(
                    admission_refusal=lambda *args: None
                ),
                "ee.usage.models.usage": SimpleNamespace(
                    OrganizationStatusChoices=SimpleNamespace(UNPAID="unpaid")
                ),
                "tracer.models.project": SimpleNamespace(
                    Project=SimpleNamespace(with_simulation=self.manager)
                ),
            },
        )
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.ns = load(
            SOURCE,
            {
                "logger": Mock(),
                "importlib": SimpleNamespace(
                    util=SimpleNamespace(find_spec=lambda _: object())
                ),
            },
        )
        self.require = self.ns["require_recording_rehost_admission"]
        self.error = self.ns["RecordingRehostRefused"]

    def test_total_spend_and_storage_pause_are_typed(self):
        for scope in ("total_spend", "storage"):
            with self.subTest(scope=scope):
                self.redis.get.side_effect = lambda key, scope=scope: (
                    b"1" if key == f"pause:synthetic-org:{scope}" else None
                )
                with self.assertRaises(self.error) as caught:
                    self.require("synthetic-project")
                self.assertEqual(caught.exception.error_code, "BUDGET_PAUSED")

    def test_store_and_tenant_resolution_errors_fail_closed(self):
        for target in (self.redis.get, self.manager.filter):
            with self.subTest(target=target):
                target.side_effect = RuntimeError("synthetic unavailable")
                with self.assertRaises(self.error) as caught:
                    self.require("synthetic-project")
                self.assertEqual(
                    caught.exception.error_code, "BUDGET_CHECK_UNAVAILABLE"
                )
                target.side_effect = None

    def test_missing_project_or_org_cannot_bypass_billing(self):
        for project in (None, "synthetic-project"):
            self.manager.filter.return_value.values_list.return_value.first.return_value = None
            with self.assertRaises(self.error) as caught:
                self.require(project)
            self.assertEqual(caught.exception.error_code, "BUDGET_CHECK_UNAVAILABLE")
        self.check.assert_not_called()

    def test_unpaused_uses_canonical_project_org_and_storage_amount(self):
        self.require("synthetic-project", amount=17)
        self.require("synthetic-project", amount=19)
        self.manager.filter.assert_called_with(id="synthetic-project", deleted=False)
        self.manager.filter.return_value.values_list.assert_called_with(
            "organization_id", flat=True
        )
        self.check.assert_called_with("synthetic-org", "observe_add", amount=19)
        self.assertEqual(
            [call.args[0] for call in self.redis.get.call_args_list].count(
                "pause:synthetic-org:total_spend"
            ),
            2,
        )

    def test_other_org_pause_does_not_leak(self):
        self.redis.get.side_effect = lambda key: (
            "1" if key == "pause:other-org:total_spend" else None
        )
        self.require("synthetic-project")

    def test_explicit_oss_is_nonbillable_not_missing_store(self):
        self.mode.is_oss.return_value = True
        self.require(None)
        self.manager.filter.assert_not_called()
        self.check.assert_not_called()

    def test_broken_deployment_detection_does_not_disable_billing(self):
        self.mode.is_oss.side_effect = RuntimeError("synthetic mode failure")
        with self.assertRaises(self.error) as caught:
            self.require("synthetic-project")
        self.assertEqual(caught.exception.error_code, "BUDGET_CHECK_UNAVAILABLE")

    def test_projectless_recording_uses_owning_call_not_an_unmetered_mode(self):
        calls = Mock()
        calls.filter.return_value.values_list.return_value.first.return_value = (
            "synthetic-call-org"
        )
        with patch.dict(
            sys.modules,
            {
                "simulate.models.test_execution": SimpleNamespace(
                    CallExecution=SimpleNamespace(all_objects=calls)
                )
            },
        ):
            self.require(None, call_execution_id="synthetic-call", amount=23)
            calls.filter.assert_called_with(
                id="synthetic-call",
                deleted=False,
                test_execution__deleted=False,
                test_execution__run_test__deleted=False,
            )
            calls.filter.return_value.values_list.assert_called_with(
                "test_execution__run_test__organization_id", flat=True
            )
            self.check.assert_called_with(
                "synthetic-call-org", "voice_recording_storage", amount=23
            )
            self.manager.filter.assert_not_called()
            self.redis.get.return_value = "1"
            with self.assertRaises(self.error) as caught:
                self.require(None, call_execution_id="synthetic-call")
            self.assertEqual(caught.exception.error_code, "BUDGET_PAUSED")

    def test_missing_call_owner_is_typed_refusal(self):
        calls = Mock()
        calls.filter.return_value.values_list.return_value.first.return_value = None
        with patch.dict(
            sys.modules,
            {
                "simulate.models.test_execution": SimpleNamespace(
                    CallExecution=SimpleNamespace(all_objects=calls)
                )
            },
        ):
            with self.assertRaises(self.error) as caught:
                self.require(None, call_execution_id="synthetic-call")
            self.assertEqual(caught.exception.error_code, "BUDGET_CHECK_UNAVAILABLE")
            self.check.assert_not_called()


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
