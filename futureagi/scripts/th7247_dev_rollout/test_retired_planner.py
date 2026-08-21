#!/usr/bin/env python3
"""Offline proof that the obsolete rollout planner is inert."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PACKAGE_DIR))

import plan  # noqa: E402


class RetiredPlannerTests(unittest.TestCase):
    def test_imported_planner_fails_closed(self) -> None:
        with self.assertRaisesRegex(
            plan.RolloutSafetyError,
            "ch25_property_catalog_dev_rollout",
        ):
            plan.build_plan(object())

    def test_cli_is_zero_io_tombstone(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(PACKAGE_DIR / "plan.py"), "--stale-option"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("ch25_property_catalog_dev_rollout", completed.stderr)
        self.assertEqual(completed.stdout, "")

    def test_deleted_span_only_contract_is_absent(self) -> None:
        source = (PACKAGE_DIR / "plan.py").read_text()

        self.assertNotIn("span_attribute_key_catalog", source)
        self.assertNotIn("ch25_backfill_attribute_catalog", source)
        self.assertNotIn("ch25_activate_attribute_catalog", source)
        self.assertNotIn("clickhouse", source.casefold())


if __name__ == "__main__":
    unittest.main()
