"""FA EE5 adapter controls: actual scheduler/EE bodies, synthetic boundaries.

Run with the same scheduler_source and --ee-root arguments as
cap_scheduler_controls.py. No Django imports, sockets or real storage.
"""

import runpy
import unittest
from pathlib import Path
from unittest.mock import Mock

C = runpy.run_path(str(Path(__file__).with_name("cap_scheduler_controls.py")))
ActionHarness, attach_scheduler, Row = (
    C[key] for key in ("ActionHarness", "attach_scheduler", "Row")
)
F = C["F"]
ORG, PAUSE, COUNT, REASON = (F[key] for key in ("ORG", "PAUSE", "COUNT", "REASON"))
PERIOD = "2026-01"
KNOWN = "cap_evaluation:total_spend:last_known_capped"


def harness():
    h = ActionHarness()
    original = h.env["evaluate_total_spend_budget"]
    h.evaluations = Mock()

    def evaluate(*args, **kwargs):
        # The real evaluator re-enters once with its held org lock; count only
        # scheduler calls, not that private recursive implementation detail.
        if "state" not in kwargs:
            h.evaluations(*args, **kwargs)
        return original(*args, **kwargs)

    h.env["evaluate_total_spend_budget"] = evaluate
    env = attach_scheduler(h)
    return h, env, env["evaluate_budgets_catchup_activity"]


class LazyFailure:
    """Yield an unknown org before failing, as a partial DB cursor might."""

    def exclude(self, *args, **kwargs):
        return self

    def values_list(self, *keys, flat=False):
        self.flat = flat
        return self

    def distinct(self):
        return self

    def __iter__(self):
        yield "never-capped" if self.flat else ("never-capped", "storage")
        raise RuntimeError("synthetic discovery fault")

    def iterator(self, **kwargs):
        return iter(self)


def break_discovery(h, point, selector=lambda kwargs: True):
    manager = h.env["UsageBudget"].objects
    original = manager.filter

    def query(**kwargs):
        if selector(kwargs):
            if point == "filter":
                raise RuntimeError("synthetic discovery fault")
            return LazyFailure()
        return original(**kwargs)

    manager.filter = Mock(side_effect=query)


class SchedulerDiscoveryControls(unittest.TestCase):
    def assert_never_capped_untouched(self, h):
        self.assertIsNone(h.redis.get("pause:never-capped:total_spend"))
        self.assertIsNone(
            h.redis.get("cap_evaluation:never-capped:total_spend:failures")
        )
        self.assertFalse(h.redis.sismember(KNOWN, "never-capped"))

    def test_whole_query_failure_counts_known_org_once_before_dimension_failure(self):
        for point in ("filter", "materialize"):
            with self.subTest(point=point):
                h, env, activity = harness()
                h.evaluate("4")
                self.assertTrue(h.redis.sismember(KNOWN, ORG))
                h.evaluations.reset_mock()
                break_discovery(h, point)
                for count in range(1, 4):
                    with self.assertRaisesRegex(RuntimeError, "synthetic discovery"):
                        activity()
                    self.assertEqual(h.redis.get(COUNT), str(count))
                    self.assertEqual(h.metric.add.call_count, count * 2)
                    self.assertEqual(h.redis.get(PAUSE), "1" if count == 3 else None)
                    self.assertEqual(
                        env["evaluate_total_spend_budgets_catchup"].call_count, count
                    )
                    self.assert_never_capped_untouched(h)
                self.assertEqual(h.redis.get(REASON), "CAP_EVALUATION_UNAVAILABLE")
                self.assertEqual(h.redis.ttls[PAUSE], 35 * 86400)
                h.evaluations.assert_not_called()
                events = h.env["logger"].error.call_args_list
                self.assertEqual(
                    sum(
                        c.args == ("CAP_EVALUATION_FAILED",)
                        and c.kwargs["org_id"] is None
                        for c in events
                    ),
                    3,
                )
                self.assertNotIn("synthetic discovery fault", str(events))

    def test_empty_known_set_alerts_without_pausing_partial_or_unknown_orgs(self):
        for point in ("filter", "materialize"):
            with self.subTest(point=point):
                h, env, activity = harness()
                break_discovery(h, point)
                for _ in range(3):
                    with self.assertRaisesRegex(RuntimeError, "synthetic discovery"):
                        activity()
                self.assertEqual(h.metric.add.call_count, 3)
                self.assertEqual(h.redis.writes, [])
                self.assertIsNone(h.redis.get(PAUSE))
                self.assertIsNone(h.redis.get(COUNT))
                self.assert_never_capped_untouched(h)
                self.assertEqual(
                    env["evaluate_total_spend_budgets_catchup"].call_count, 3
                )

    def test_dimension_discovery_cannot_prevent_total_spend_enforcement(self):
        for point in ("filter", "materialize"):
            with self.subTest(point=point):
                h, env, activity = harness()
                h.env["calculate_current_period_spend"] = Mock(
                    return_value=F["D"]("10")
                )
                # Match scheduled dimension discovery, not the producer's
                # separate per-budget notification-claim lookup by id.
                break_discovery(
                    h, point, lambda kw: kw == {"is_active": True, "deleted": False}
                )
                with self.assertRaisesRegex(RuntimeError, "synthetic discovery"):
                    activity()
                self.assertEqual(h.redis.get(PAUSE), "1")
                self.assertTrue(h.redis.sismember(KNOWN, ORG))
                env["evaluate_total_spend_budgets_catchup"].assert_called_once_with(
                    PERIOD
                )
                h.evaluations.assert_called_once_with(ORG, PERIOD)

    def test_total_query_failure_still_allows_dimension_processing(self):
        for point in ("filter", "materialize"):
            with self.subTest(point=point):
                h, env, activity = harness()
                h.evaluate("4")
                h.env["UsageBudget"].objects.rows.append(
                    Row(**{**vars(h.budget), "id": "storage", "scope": "storage"})
                )
                break_discovery(
                    h,
                    point,
                    lambda kw: (
                        kw.get("scope") == "total_spend" and "organization_id" not in kw
                    ),
                )
                for count in range(1, 4):
                    self.assertEqual(activity(), {"evaluated": 1, "period": PERIOD})
                    self.assertEqual(h.redis.get(COUNT), str(count))
                    self.assertEqual(h.metric.add.call_count, count * 2)
                    self.assert_never_capped_untouched(h)
                self.assertEqual(h.redis.get(PAUSE), "1")
                self.assertEqual(env["evaluate_budgets_catchup"].call_count, 3)
                env["evaluate_budgets_catchup"].assert_called_with(
                    ORG, "storage", PERIOD
                )

    def test_failure_count_not_doubled_and_recovery_retains_rd03_dedup(self):
        h, env, activity = harness()
        h.evaluate("10")
        self.assertEqual(h.budget.last_triggered_period, PERIOD)
        h.evaluations.reset_mock()
        h.env["calculate_current_period_spend"] = Mock(
            side_effect=ValueError("synthetic")
        )
        for count in range(1, 4):
            self.assertEqual(activity(), {"evaluated": 1, "period": PERIOD})
            self.assertEqual(h.redis.get(COUNT), str(count))
            self.assertEqual(h.metric.add.call_count, count)
        self.assertEqual(h.redis.get(REASON), "CAP_EVALUATION_UNAVAILABLE")
        h.env["calculate_current_period_spend"] = Mock(return_value=F["D"]("4"))
        self.assertEqual(activity()["evaluated"], 1)
        self.assertIsNone(h.redis.get(PAUSE))
        self.assertIsNone(h.redis.get(COUNT))
        self.assertIsNone(h.redis.get(REASON))
        self.assertEqual(env["evaluate_total_spend_budgets_catchup"].call_count, 4)
        self.assertEqual(h.evaluations.call_count, 4)
        env["evaluate_budgets_catchup"].assert_not_called()
        h.notify.assert_called_once()

    def test_seed_count_distinct_dimensions_and_per_org_isolation(self):
        h, env, activity = harness()
        rows = h.env["UsageBudget"].objects.rows
        for overrides in (
            {"id": "second-cap", "organization_id": "org-second"},
            {"id": "storage-fails", "scope": "storage"},
            {"id": "storage-ok", "scope": "storage", "organization_id": "org-second"},
            {
                "id": "storage-duplicate",
                "scope": "storage",
                "organization_id": "org-second",
            },
            {
                "id": "storage-fired",
                "scope": "storage",
                "organization_id": "org-fired",
                "last_triggered_period": PERIOD,
            },
            {"id": "storage-inactive", "scope": "storage", "is_active": False},
            {"id": "storage-deleted", "scope": "storage", "deleted": True},
        ):
            rows.append(Row(**{**vars(h.budget), **overrides}))
        h.env["calculate_current_period_spend"] = Mock(return_value=F["D"]("4"))
        env["evaluate_budgets_catchup"].side_effect = [ValueError("synthetic"), None]
        self.assertEqual(activity(), {"evaluated": 3, "period": PERIOD})
        env["evaluate_total_spend_budgets_catchup"].assert_called_once_with(PERIOD)
        self.assertEqual(h.evaluations.call_count, 2)
        self.assertEqual(
            [c.args for c in env["evaluate_budgets_catchup"].call_args_list],
            [(ORG, "storage", PERIOD), ("org-second", "storage", PERIOD)],
        )
        h.env["logger"].exception.assert_called_once_with(
            "budget_catchup_evaluation_failed",
            org_id=ORG,
            scope="storage",
            period=PERIOD,
        )

    def test_missing_cloud_entry_point_skips_without_querying(self):
        for missing in (
            "evaluate_total_spend_budgets_catchup",
            "evaluate_budgets_catchup",
        ):
            with self.subTest(missing=missing):
                h, env, activity = harness()

                class MissingOverlay(dict):
                    def __getitem__(self, key, missing=missing):
                        if key == missing:
                            raise ImportError("synthetic cloud-only boundary")
                        return super().__getitem__(key)

                env["__boundaries"] = MissingOverlay(env["__boundaries"])
                manager = h.env["UsageBudget"].objects
                query = manager.filter = Mock(wraps=manager.filter)
                self.assertIsNone(activity())
                query.assert_not_called()
                h.metric.add.assert_not_called()
                h.env["logger"].info.assert_called_once_with(
                    "budget_catchup_skipped_billing_is_cloud_only"
                )


if __name__ == "__main__":
    unittest.main(argv=[__file__, *C["REST"]], verbosity=2)
