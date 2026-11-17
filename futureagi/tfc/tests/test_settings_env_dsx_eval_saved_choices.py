"""Pinned versions' saved choices are ON unless the deployment turns them off."""

import pytest

from tfc.tests.test_settings_env import _load_settings

pytestmark = pytest.mark.unit

FLAG = "EVAL_PINNED_VERSION_SAVED_CHOICES_ENABLED"


def test_eval_saved_choices_unset_is_on(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    loaded = _load_settings([FLAG], DJANGO_SETTINGS_MODULE="tfc.settings.test")
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
def test_eval_saved_choices_env_is_fail_closed(value, expected):
    loaded = _load_settings(
        [FLAG], DJANGO_SETTINGS_MODULE="tfc.settings.test", **{FLAG: value}
    )
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.splitlines()[-1] == str(expected)


RETIRED = "_".join(["DSX", "EVAL", "SAVED", "CHOICES", "ENABLED"])


def test_eval_saved_choices_ignore_the_retired_name(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    monkeypatch.delenv(RETIRED, raising=False)
    loaded = _load_settings(
        [FLAG], DJANGO_SETTINGS_MODULE="tfc.settings.test", **{RETIRED: "false"}
    )
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.splitlines()[-1] == "True"
