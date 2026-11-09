"""Execute the real scheduler body with import stubs; stdlib, no Django or IO.

The actual Django/Temporal import test is separately owed to qualification.
Run: python3 -I this_file.py
"""

import ast
import builtins
import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[1] / "tfc/temporal/schedules/billing.py"


def load_activity():
    tree = ast.parse(SOURCE.read_text())
    tree.body = [
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef)
        and n.name == "evaluate_budgets_catchup_activity"
    ]
    tree.body[0].decorator_list = []
    ns = {"datetime": datetime, "UTC": UTC, "logger": Mock()}
    exec(compile(tree, str(SOURCE), "exec"), ns)
    return ns["evaluate_budgets_catchup_activity"], ns["logger"]


class SchedulerSkewControls(unittest.TestCase):
    def run_activity(self, *, total=True, overlay=True):
        activity, logger = load_activity()
        query = Mock()
        for method in ("filter", "exclude", "values_list", "distinct"):
            getattr(query, method).return_value = query
        query.iterator.return_value = iter([("synthetic-org", "tracing_events")])
        dimension = Mock()
        total_spend = Mock(return_value={"evaluated": 2})
        symbols = {"evaluate_budgets_catchup": dimension}
        if total:
            symbols["evaluate_total_spend_budgets_catchup"] = total_spend
        real_import = builtins.__import__

        def import_overlay(name, *args, **kwargs):
            if name == "ee.usage.models.usage":
                return SimpleNamespace(UsageBudget=SimpleNamespace(objects=query))
            if name == "ee.cloud.billing.budget_enforcement":
                if not overlay:
                    raise ImportError("synthetic absent cloud overlay")
                return SimpleNamespace(**symbols)
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=import_overlay):
            result = activity()
        return result, logger, dimension, total_spend, query

    def test_old_overlay_keeps_dimension_catchup(self):
        result, logger, dimension, total, query = self.run_activity(total=False)
        dimension.assert_called_once()
        self.assertEqual(
            dimension.call_args.args[:2], ("synthetic-org", "tracing_events")
        )
        self.assertEqual(result["evaluated"], 1)
        total.assert_not_called()
        query.iterator.assert_called_once_with(chunk_size=500)
        logger.info.assert_not_called()
        logger.error.assert_called_once_with(
            "budget_catchup_symbol_unavailable",
            symbol="evaluate_total_spend_budgets_catchup",
        )
        logger.warning.assert_not_called()

    def test_current_overlay_runs_both_catchups(self):
        result, logger, dimension, total, _ = self.run_activity()
        total.assert_called_once_with(result["period"])
        dimension.assert_called_once_with(
            "synthetic-org", "tracing_events", result["period"]
        )
        self.assertEqual(result["evaluated"], 3)
        logger.warning.assert_not_called()
        logger.error.assert_not_called()

    def test_absent_overlay_is_a_nonbillable_noop(self):
        result, logger, dimension, total, _ = self.run_activity(overlay=False)
        self.assertIsNone(result)
        dimension.assert_not_called()
        total.assert_not_called()
        logger.info.assert_called_once_with(
            "budget_catchup_skipped_billing_is_cloud_only"
        )


if __name__ == "__main__":

    def deny_sockets(event, args):
        if event.startswith("socket."):
            raise PermissionError("socket-free billing scheduler control")

    sys.addaudithook(deny_sockets)
    unittest.main(verbosity=2)
