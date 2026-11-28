"""Regression test: accounts.base_data must load its demo fixtures regardless
of the process working directory.

base_data opens its fixtures at import time and is reached from
accounts/migrations/0020_reseed_broken_demo_data.py, so a CWD-relative path
breaks `migrate` (and any test run that migrates) unless the process happens
to start in futureagi/.

The import runs in a fresh subprocess so the check cannot be satisfied by a
module already cached in sys.modules, and so the CWD is genuinely different.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

FUTUREAGI_DIR = Path(__file__).resolve().parents[2]
DEMO_DATASET_DIR = FUTUREAGI_DIR / "accounts" / "demo_dataset"

# module attribute -> fixture file it is loaded from
FIXTURES = {
    "dataset_data": "table_data.json",
    "run_prompt_config": "run_prompt_config.json",
    "experiment_data": "experiment.json",
    "image_dataset_data": "img_dataset_data.json",
}

_PROBE = """
import json, sys
import accounts.base_data as m
json.dump({name: getattr(m, name) for name in sys.argv[1:]}, sys.stdout)
"""


@pytest.mark.unit
@pytest.mark.parametrize(
    "cwd_kind", ["repo_root", "tmp_dir"], ids=["cwd=repo-root", "cwd=tmp-dir"]
)
def test_base_data_loads_fixtures_outside_futureagi_dir(cwd_kind, tmp_path):
    cwd = FUTUREAGI_DIR.parent if cwd_kind == "repo_root" else tmp_path
    assert cwd.resolve() != FUTUREAGI_DIR

    env = {**os.environ, "PYTHONPATH": str(FUTUREAGI_DIR)}
    result = subprocess.run(
        [sys.executable, "-c", _PROBE, *FIXTURES],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"importing accounts.base_data from {cwd} failed:\n{result.stderr}"
    )

    loaded = json.loads(result.stdout)
    for name, filename in FIXTURES.items():
        expected = json.loads((DEMO_DATASET_DIR / filename).read_text())
        assert loaded[name] == expected, f"{name} differs from {filename}"
