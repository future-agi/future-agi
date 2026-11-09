"""Copying or moving datasets between workspaces stays OFF unless the deployment
explicitly opts in."""

import os
import subprocess
import sys

import pytest

from tfc.tests.test_settings_env import BACKEND, _load_settings

pytestmark = pytest.mark.unit

FLAG = "DSX_DATASET_TRANSFER_ENABLED"


def test_dataset_transfer_unset_is_off(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    loaded = _load_settings([FLAG], DJANGO_SETTINGS_MODULE="tfc.settings.test")
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.splitlines()[-1] == "False"


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
def test_dataset_transfer_env_is_fail_closed(value, expected):
    loaded = _load_settings(
        [FLAG], DJANGO_SETTINGS_MODULE="tfc.settings.test", **{FLAG: value}
    )
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.splitlines()[-1] == str(expected)


@pytest.mark.parametrize("value", ["true", "false"])
def test_dataset_transfer_switch_is_a_plain_boolean(value):
    # The view's gate compares with ``is True``, so the binding must parse to
    # a bool, never leave the raw string behind.
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
