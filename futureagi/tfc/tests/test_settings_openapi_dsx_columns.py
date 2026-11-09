"""Contract generation advertises optional F44 fields without enabling runtime."""

import pytest

from tfc.tests.test_settings_env import _load_settings

pytestmark = pytest.mark.unit
FLAG = "DSX_DATASET_LIST_COLUMNS_ENABLED"


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
@pytest.mark.parametrize("value", [None, "false", " ture "])
def test_runtime_columns_stay_default_off(monkeypatch, module, value):
    monkeypatch.delenv(FLAG, raising=False)
    loaded = _load_settings(
        [FLAG],
        DJANGO_SETTINGS_MODULE=module,
        **({} if value is None else {FLAG: value}),
    )
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.splitlines()[-1] == "False"
