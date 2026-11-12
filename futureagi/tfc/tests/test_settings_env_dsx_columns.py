"""F44 remains default-OFF; activation uses deployment env, not a source flip."""

import pytest

from tfc.tests.test_settings_env import _load_settings

pytestmark = pytest.mark.unit
FLAG = "DSX_DATASET_LIST_COLUMNS_ENABLED"


@pytest.mark.parametrize(
    "value", [None, "", "false", "False", "0", "no", "off", " ture "]
)
def test_dataset_columns_defaults_off_and_fails_closed(monkeypatch, value):
    monkeypatch.delenv(FLAG, raising=False)
    loaded = _load_settings([FLAG], **({} if value is None else {FLAG: value}))
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.splitlines()[-1] == "False"


@pytest.mark.parametrize("value", ["true", "TRUE", " true ", "1", "yes", "on"])
def test_dataset_columns_explicit_deployment_activation(value):
    loaded = _load_settings([FLAG], **{FLAG: value})
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.splitlines()[-1] == "True"
