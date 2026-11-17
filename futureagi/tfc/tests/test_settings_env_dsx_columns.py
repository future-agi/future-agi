"""Dataset list metadata defaults on and honors explicit deployment values."""

import os
import subprocess
import sys

import pytest

from tfc.tests.test_settings_env import BACKEND, _load_settings

pytestmark = pytest.mark.unit
FLAG = "DATASET_LIST_COLUMNS_ENABLED"


def _load_effective_columns(**env):
    """Load deployment settings, then print the effective metadata switch."""
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from django.conf import settings; "
            f"getattr(settings, {FLAG!r}, False); "
            "from tfc.utils.default_on import on_unless_turned_off; "
            f"print(on_unless_turned_off({FLAG!r}))",
        ],
        env={**os.environ, **env},
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.parametrize("value", [None, "", " \t "])
def test_dataset_columns_effective_default_is_on(monkeypatch, value):
    monkeypatch.delenv(FLAG, raising=False)
    loaded = _load_effective_columns(**({} if value is None else {FLAG: value}))
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.splitlines()[-1] == "True"


@pytest.mark.parametrize("value", ["false", "False", "0", "no", "off", " ture "])
def test_dataset_columns_explicit_values_fail_closed(value):
    for load in (
        lambda: _load_settings([FLAG], **{FLAG: value}),
        lambda: _load_effective_columns(**{FLAG: value}),
    ):
        loaded = load()
        assert loaded.returncode == 0, loaded.stderr
        assert loaded.stdout.splitlines()[-1] == "False"


@pytest.mark.parametrize("value", ["true", "TRUE", " true ", "1", "yes", "on"])
def test_dataset_columns_explicit_deployment_activation(value):
    for load in (
        lambda: _load_settings([FLAG], **{FLAG: value}),
        lambda: _load_effective_columns(**{FLAG: value}),
    ):
        loaded = load()
        assert loaded.returncode == 0, loaded.stderr
        assert loaded.stdout.splitlines()[-1] == "True"


RETIRED = "_".join(["DSX", "DATASET", "LIST", "COLUMNS", "ENABLED"])


def test_dataset_columns_raw_parse_ignores_the_retired_name(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    monkeypatch.delenv(RETIRED, raising=False)
    loaded = _load_settings([FLAG], **{RETIRED: "true"})
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.splitlines()[-1] == "False"


@pytest.mark.parametrize("value", ["true", "false"])
def test_dataset_columns_retired_name_does_not_change_effective_default(
    monkeypatch, value
):
    monkeypatch.delenv(FLAG, raising=False)
    loaded = _load_effective_columns(**{RETIRED: value})
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.splitlines()[-1] == "True"
