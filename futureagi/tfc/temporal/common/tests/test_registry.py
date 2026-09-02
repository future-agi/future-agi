from types import SimpleNamespace
from unittest.mock import MagicMock, call

import pytest

from tfc.temporal.common import registry


def test_usage_temporal_registry_prefers_cloud(monkeypatch):
    cloud_registry = MagicMock()
    import_module = MagicMock(
        return_value=SimpleNamespace(get_workflows=cloud_registry)
    )
    monkeypatch.setattr(registry, "import_module", import_module)

    assert registry._load_usage_temporal_registry("get_workflows") is cloud_registry
    import_module.assert_called_once_with("ee.cloud.temporal")


def test_usage_temporal_registry_falls_back_to_legacy(monkeypatch):
    legacy_registry = MagicMock()
    cloud_missing = ModuleNotFoundError(
        "cloud module unavailable", name="ee.cloud.temporal"
    )
    import_module = MagicMock(
        side_effect=[
            cloud_missing,
            SimpleNamespace(get_activities=legacy_registry),
        ],
    )
    monkeypatch.setattr(registry, "import_module", import_module)

    assert registry._load_usage_temporal_registry("get_activities") is legacy_registry
    assert import_module.call_args_list == [
        call("ee.cloud.temporal"),
        call("ee.usage.temporal"),
    ]


def test_usage_temporal_registry_returns_none_when_unavailable(monkeypatch):
    missing_modules = [
        ModuleNotFoundError("cloud unavailable", name="ee.cloud"),
        ModuleNotFoundError("legacy unavailable", name="ee.usage.temporal"),
    ]
    import_module = MagicMock(side_effect=missing_modules)
    monkeypatch.setattr(registry, "import_module", import_module)

    assert registry._load_usage_temporal_registry("get_workflows") is None
    assert import_module.call_args_list == [
        call("ee.cloud.temporal"),
        call("ee.usage.temporal"),
    ]


def test_usage_temporal_registry_does_not_hide_internal_import_error(monkeypatch):
    import_error = ModuleNotFoundError("dependency unavailable", name="redis")
    monkeypatch.setattr(registry, "import_module", MagicMock(side_effect=import_error))

    try:
        registry._load_usage_temporal_registry("get_workflows")
    except ModuleNotFoundError as exc:
        assert exc is import_error
    else:  # pragma: no cover - assertion guard
        raise AssertionError("internal import failure was silently ignored")


@pytest.mark.parametrize(
    ("register", "args"),
    [
        (registry._register_usage_temporal_workflows, ()),
        (registry._register_usage_temporal_activities, (MagicMock(),)),
    ],
)
def test_usage_temporal_registration_does_not_hide_packaging_failure(
    monkeypatch, register, args
):
    import_error = ModuleNotFoundError("dependency unavailable", name="redis")
    monkeypatch.setattr(
        registry,
        "_load_usage_temporal_registry",
        MagicMock(side_effect=import_error),
    )

    with pytest.raises(ModuleNotFoundError) as exc_info:
        register(*args)

    assert exc_info.value is import_error
