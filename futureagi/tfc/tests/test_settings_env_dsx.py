"""Dataset stars are on unless the deployment explicitly turns them off."""

import os
import subprocess
import sys

import pytest

from tfc.tests.test_settings_env import BACKEND, _load_settings

pytestmark = pytest.mark.unit

FLAG = "DSX_DATASET_STARS_ENABLED"


@pytest.mark.parametrize("value", [None, "", "  "], ids=["unset", "empty", "blank"])
def test_dataset_stars_unset_or_blank_is_on(monkeypatch, value):
    if value is None:
        monkeypatch.delenv(FLAG, raising=False)
    else:
        monkeypatch.setenv(FLAG, value)
    loaded = subprocess.run(
        [
            sys.executable,
            "-c",
            "import django; django.setup(); "
            "from tfc.utils.default_on import on_unless_turned_off; "
            f"print(on_unless_turned_off({FLAG!r}))",
        ],
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "tfc.settings.test"},
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.splitlines()[-1] == "True"


@pytest.mark.parametrize(
    "value, expected",
    [
        ("", False),
        ("false", False),
        ("False", False),
        ("0", False),
        ("no", False),
        ("off", False),
        (" ture ", False),
        ("true", True),
        ("TRUE", True),
        (" true ", True),
        ("1", True),
        ("yes", True),
        ("on", True),
    ],
)
def test_dataset_stars_raw_env_parse_is_fail_closed(value, expected):
    loaded = _load_settings(
        [FLAG], DJANGO_SETTINGS_MODULE="tfc.settings.test", **{FLAG: value}
    )
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.splitlines()[-1] == str(expected)
