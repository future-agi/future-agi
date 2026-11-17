"""Dataset copying defaults to ON; explicit switch values keep their parsed meaning.

Moves remain refused independently of the switch.
"""

import os
import subprocess
import sys

import pytest

from tfc.tests.test_settings_env import BACKEND, _load_settings

pytestmark = pytest.mark.unit

FLAG = "DSX_DATASET_TRANSFER_ENABLED"


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, True),
        ("", True),
        (" \t ", True),
        ("false", False),
        ("False", False),
        ("0", False),
        ("no", False),
        ("off", False),
        (" ture ", False),
        ("enabled", False),
        ("true", True),
        ("TRUE", True),
        (" true ", True),
        ("1", True),
        ("yes", True),
        ("on", True),
    ],
)
def test_dataset_transfer_effective_env_value(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv(FLAG, raising=False)
    else:
        monkeypatch.setenv(FLAG, value)
    loaded = subprocess.run(
        [
            sys.executable,
            "-c",
            "import django; django.setup(); "
            "from model_hub.views.dataset_transfer import transfer_enabled; "
            "print(transfer_enabled())",
        ],
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "tfc.settings.test"},
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.splitlines()[-1] == str(expected)


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
        ("enabled", False),
        ("true", True),
        ("TRUE", True),
        (" true ", True),
        ("1", True),
        ("yes", True),
        ("on", True),
    ],
)
def test_dataset_transfer_raw_env_parse_is_fail_closed(value, expected):
    loaded = _load_settings(
        [FLAG], DJANGO_SETTINGS_MODULE="tfc.settings.test", **{FLAG: value}
    )
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.splitlines()[-1] == str(expected)


@pytest.mark.parametrize("value", ["true", "false"])
def test_dataset_transfer_switch_is_a_plain_boolean(value):
    # An explicit environment value must parse to a bool, never remain a string.
    loaded = subprocess.run(
        [
            sys.executable,
            "-c",
            "import importlib, os; "
            "s = importlib.import_module(os.environ['DJANGO_SETTINGS_MODULE']); "
            f"print(type(s.{FLAG}).__name__)",
        ],
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "tfc.settings.test", FLAG: value},
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.splitlines()[-1] == "bool"
