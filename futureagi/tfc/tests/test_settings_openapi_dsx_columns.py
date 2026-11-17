"""Contract generation advertises optional fields independently of runtime values."""

import pytest

from tfc.tests.test_settings_env import _load_settings
from tfc.tests.test_settings_env_dsx_columns import _load_effective_columns

pytestmark = pytest.mark.unit
FLAG = "DATASET_LIST_COLUMNS_ENABLED"


@pytest.mark.parametrize("value", [None, "false", " ture ", "true"])
def test_openapi_columns_schema_is_independent_of_deployment_env(monkeypatch, value):
    monkeypatch.delenv(FLAG, raising=False)
    loaded = _load_settings(
        [FLAG],
        DJANGO_SETTINGS_MODULE="tfc.settings.openapi",
        **({} if value is None else {FLAG: value}),
    )
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.splitlines()[-1] == "True"


@pytest.mark.parametrize("module", ["tfc.settings.settings", "tfc.settings.test"])
@pytest.mark.parametrize(
    "value, expected",
    [
        (None, "True"),
        ("", "True"),
        (" \t ", "True"),
        ("false", "False"),
        ("0", "False"),
        ("off", "False"),
        (" ture ", "False"),
        ("true", "True"),
    ],
)
def test_runtime_columns_use_effective_deployment_value(
    monkeypatch, module, value, expected
):
    monkeypatch.delenv(FLAG, raising=False)
    loaded = _load_effective_columns(
        DJANGO_SETTINGS_MODULE=module,
        **({} if value is None else {FLAG: value}),
    )
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.splitlines()[-1] == expected
