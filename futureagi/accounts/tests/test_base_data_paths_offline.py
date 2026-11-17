"""Demo data imports are independent of the caller's working directory.

DB-free: execute with python -I -B; no application settings or network imports.
"""

import json
import os
import runpy
import tempfile
import unittest
from pathlib import Path


ACCOUNTS = Path(__file__).resolve().parents[1]
DATA_FILES = {
    "dataset_data": "table_data.json",
    "run_prompt_config": "run_prompt_config.json",
    "experiment_data": "experiment.json",
    "image_dataset_data": "img_dataset_data.json",
}


class BaseDataPathTests(unittest.TestCase):
    def load_from(self, directory):
        previous = Path.cwd()
        try:
            os.chdir(directory)
            return runpy.run_path(str(ACCOUNTS / "base_data.py"))
        finally:
            os.chdir(previous)

    def assert_packaged_data(self, namespace):
        for name, filename in DATA_FILES.items():
            with self.subTest(name=name):
                expected = json.loads((ACCOUNTS / "demo_dataset" / filename).read_text())
                self.assertTrue(namespace[name] == expected, "packaged data was not loaded")

    def test_import_from_an_empty_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assert_packaged_data(self.load_from(directory))

    def test_working_directory_cannot_shadow_packaged_data(self):
        with tempfile.TemporaryDirectory() as directory:
            shadow = Path(directory) / "accounts" / "demo_dataset"
            shadow.mkdir(parents=True)
            for filename in DATA_FILES.values():
                (shadow / filename).write_text('{"wrong_working_directory": true}')
            self.assert_packaged_data(self.load_from(directory))


if __name__ == "__main__":
    unittest.main()
