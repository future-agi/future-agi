"""Offline structural checks for the opt-in full-scale SQL benchmark profile."""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "scripts/sql_workbench/profile.json"
SOURCE = ROOT / "scripts/sql_workbench/benchmark.py"


class SQLBenchmarkProfileTests(unittest.TestCase):
    def test_profile_is_a_bounded_real_join_with_separate_scale_and_time_window(self):
        profile = json.loads(PROFILE.read_text())
        self.assertEqual(profile["required_project_spans"], 1_000_000_000)
        self.assertEqual(profile["duration_seconds"], 86_400)
        self.assertGreaterEqual(profile["interval_seconds"], 10)
        self.assertGreater(profile["window_hours"], 0)
        self.assertLessEqual(profile["window_hours"], 744)
        sql = profile["sql"].lower()
        self.assertIn("join sessions", sql)
        self.assertIn("eval_results", sql)
        self.assertIn("group by", sql)
        self.assertNotIn("file(", sql)
        self.assertNotIn("remote(", sql)

    def test_client_refuses_without_optin_manifest_or_scoped_credential(self):
        source = SOURCE.read_text()
        self.assertIn("args.allow_benchmark_requests", source)
        self.assertIn("SQL_BENCHMARK_TOKEN", source)
        self.assertIn('manifest["span_count"]', source)
        self.assertIn('"fixture_count_independently_verified": False', source)
        self.assertIn('job.get("result", {}).get("row_count", 0) == 0', source)
        self.assertNotRegex(
            source, re.compile(r"clickhouse_driver|CH_USERNAME|CH_PASSWORD")
        )
