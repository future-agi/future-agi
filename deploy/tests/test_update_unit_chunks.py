"""The unit-job chunk plan runs every test_update_*.py module exactly once."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

import update_unit_chunks as chunks

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "update-ci.yml"


class UnitChunkPlanTests(unittest.TestCase):
    def test_plan_is_a_partition_balanced_by_time(self) -> None:
        durations = {"a": 10.0, "b": 6.0, "c": 5.0, "d": 4.0}
        groups = chunks.plan(["a", "b", "c", "d", "e"], durations, 3)
        self.assertEqual(sorted(sum(groups, [])), ["a", "b", "c", "d", "e"])
        # "e" has no time and counts as the median module (6.0).
        self.assertEqual(groups, [["a"], ["b", "c"], ["d", "e"]])

    def test_every_real_module_runs_in_exactly_one_chunk(self) -> None:
        names = chunks.modules()
        self.assertIn("test_update_unit_chunks", names)
        durations = json.loads(chunks.DURATIONS.read_text(encoding="utf-8"))
        for total in (1, 2, 3, 4):
            groups = chunks.plan(names, durations, total)
            self.assertEqual(sorted(sum(groups, [])), names)
            self.assertTrue(all(groups))

    def test_cli_prints_one_chunk_and_refuses_a_bad_spec(self) -> None:
        script = str(Path(chunks.__file__))
        run = lambda spec: subprocess.run(  # noqa: E731
            [sys.executable, script, spec], capture_output=True, text=True
        )
        printed = [run(f"{i}/3").stdout.split() for i in (1, 2, 3)]
        self.assertEqual(sorted(sum(printed, [])), chunks.modules())
        for spec in ("0/3", "4/3", "3", "a/b"):
            self.assertNotEqual(run(spec).returncode, 0, spec)

    def test_workflow_legs_cover_every_chunk(self) -> None:
        # Stdlib only (the job installs no YAML parser): read the unit job's text.
        text = WORKFLOW.read_text(encoding="utf-8")
        unit = text[text.index("\n  unit:\n") : text.index("\n  compose:\n")]
        self.assertIn('      CHUNKS: "3"\n', unit)
        self.assertIn("        chunk: [1, 2, 3]\n", unit)
        self.assertIn('update_unit_chunks.py "$CHUNK/$CHUNKS"', unit)
        self.assertIn('python -m unittest -v "${modules[@]}"', unit)
        self.assertNotIn("discover -s deploy/tests -p 'test_update_*.py'", unit)


if __name__ == "__main__":
    unittest.main()
