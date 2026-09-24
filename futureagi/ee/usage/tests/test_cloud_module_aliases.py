"""Regression coverage for cloud-only modules moved out of ``ee.usage``."""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock

import pytest


@pytest.mark.parametrize(
    ("legacy_path", "canonical_path", "public_symbols"),
    [
        (
            "ee.usage.tasks.retention",
            "ee.cloud.tasks.retention",
            (
                "RETENTION_DATA_TYPES",
                "BATCH_SIZE",
                "soft_delete_expired_data",
                "hard_delete_expired_data",
            ),
        ),
        (
            "ee.usage.services.billing_engine",
            "ee.cloud.billing.billing_engine",
            ("BillingEngine",),
        ),
    ],
)
def test_legacy_module_is_alias_of_canonical_module(
    legacy_path, canonical_path, public_symbols
):
    legacy = importlib.import_module(legacy_path)
    canonical = importlib.import_module(canonical_path)

    assert legacy is canonical
    for symbol in public_symbols:
        assert getattr(legacy, symbol) is getattr(canonical, symbol)


def test_legacy_retention_patch_is_visible_to_canonical_callers(monkeypatch):
    legacy = importlib.import_module("ee.usage.tasks.retention")
    canonical = importlib.import_module("ee.cloud.tasks.retention")
    sentinel = object()

    monkeypatch.setattr(legacy, "_get_model", sentinel)

    assert canonical._get_model is sentinel


@pytest.mark.parametrize(
    ("activity_name", "callable_name", "result"),
    [
        (
            "soft_delete_expired_data_activity",
            "soft_delete_expired_data",
            {"org-1": {"traces": 2}},
        ),
        (
            "hard_delete_expired_data_activity",
            "hard_delete_expired_data",
            {"traces": 2},
        ),
    ],
)
def test_core_retention_activity_observes_canonical_patch(
    monkeypatch, activity_name, callable_name, result
):
    canonical = importlib.import_module("ee.cloud.tasks.retention")
    core_retention = importlib.import_module("tfc.temporal.schedules.retention")
    retention_callable = MagicMock(return_value=result)
    monkeypatch.setattr(canonical, callable_name, retention_callable)

    activity = getattr(core_retention, activity_name)

    assert activity._original_func() == result
    retention_callable.assert_called_once_with()
