"""Load the real migration assets from multiple launch directories; no services."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]

CHILD = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from runtime_paths import BACKEND_ROOT, enter_backend_root, initialize_runtime
fixture = initialize_runtime(sys.argv[2])
assert fixture == Path(sys.argv[3])
assert Path.cwd() == BACKEND_ROOT
assert sys.path[0] == str(BACKEND_ROOT)
enter_backend_root()
assert sys.path.count(str(BACKEND_ROOT)) == 1
# This is the exact import that failed in accounts migration 0020. Do not
# replace any data, intercept open(), or mock a successful migration.
from accounts import base_data
assert Path(base_data.__file__).resolve() == BACKEND_ROOT / "accounts/base_data.py"
for name in ("dataset_data", "run_prompt_config", "experiment_data", "image_dataset_data"):
    assert isinstance(getattr(base_data, name), (dict, list)) and getattr(base_data, name)
print("real-migration-assets-ok")
"""


class RuntimePathsTests(unittest.TestCase):
    def test_accounts_demo_assets_are_present_in_the_checkout(self):
        backend = ROOT / "futureagi"
        for name in (
            "table_data.json",
            "run_prompt_config.json",
            "experiment.json",
            "img_dataset_data.json",
        ):
            self.assertTrue((backend / "accounts/demo_dataset" / name).is_file(), name)

    def test_repo_root_and_unrelated_cwd_preserve_relative_fixture_location(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary) / "fixture"
            fixture.mkdir()
            for cwd in (ROOT, Path(temporary)):
                with self.subTest(cwd=str(cwd)):
                    result = subprocess.run(
                        [
                            sys.executable,
                            "-I",
                            "-B",
                            "-c",
                            CHILD,
                            str(HERE),
                            os.path.relpath(fixture, cwd),
                            str(fixture.resolve()),
                        ],
                        cwd=cwd,
                        text=True,
                        capture_output=True,
                        timeout=20,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("real-migration-assets-ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
