"""The ran-on label stays OFF unless the deployment opts in."""

import pytest

from tfc.tests.test_settings_env import _load_settings

pytestmark = pytest.mark.unit

FLAG = "EVAL_RAN_ON_LABEL_ENABLED"


def test_ran_on_label_unset_is_off(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    loaded = _load_settings([FLAG], DJANGO_SETTINGS_MODULE="tfc.settings.test")
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.splitlines()[-1] == "False"


@pytest.mark.parametrize(
    "value, expected",
    [
        ("", False),
        ("false", False),
        ("0", False),
        ("off", False),
        (" ture ", False),
        ("true", True),
        (" TRUE ", True),
        ("1", True),
        ("yes", True),
        ("on", True),
    ],
)
def test_ran_on_label_env_is_fail_closed(value, expected):
    loaded = _load_settings(
        [FLAG], DJANGO_SETTINGS_MODULE="tfc.settings.test", **{FLAG: value}
    )
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.splitlines()[-1] == str(expected)
