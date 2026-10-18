"""Regression tests for the lazy billing imports in tfc.temporal.

The billing Temporal code imports cloud billing services lazily with an
``except ImportError → None`` fallback so the public tree can ship without
them. That pattern fails silently: if the import path is stale (e.g. the
ee#185 move of billing services from ``ee.usage.services`` to
``ee.cloud.billing``), the symbol becomes ``None`` on the cloud image and
dunning/invoice/budget runs break at call time instead of import time.

These tests pin the import paths to the modules that actually exist.
"""

import builtins
import pathlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

_TEMPORAL_DIR = pathlib.Path(__file__).resolve().parent.parent / "temporal"

# Billing services that ee#185 moved out of ee.usage.services. Their only
# valid home is ee.cloud.billing — a reference through the old path silently
# resolves to None under the lazy-import pattern.
_MOVED_BILLING_MODULES = (
    "ee.usage.services.dunning",
    "ee.usage.services.invoice_generation",
    "ee.usage.services.budget_enforcement",
    "ee.usage.services.stripe_service",
    "ee.usage.services.billing_engine",
    "ee.usage.tasks.monthly_reset",
)


def _temporal_source(relative: str) -> str:
    return (_TEMPORAL_DIR / relative).read_text()


@pytest.mark.parametrize(
    "relative",
    ["billing/activities.py", "schedules/billing.py"],
)
def test_temporal_billing_does_not_use_retired_usage_paths(relative):
    source = _temporal_source(relative)
    stale = [module for module in _MOVED_BILLING_MODULES if module in source]
    assert not stale, (
        f"{relative} imports retired module(s) {stale}; these moved to "
        "ee.cloud.billing.* in ee#185 and the lazy fallback would leave "
        "them as None on the cloud image."
    )


def test_cloud_billing_symbols_resolve_when_cloud_package_present():
    """With ee.cloud installed, the lazily-imported symbols must exist."""
    pytest.importorskip("ee.cloud.billing.dunning")

    from ee.cloud.billing.budget_enforcement import (  # noqa: F401
        evaluate_budgets_catchup,
        evaluate_total_spend_budget,
    )
    from ee.cloud.billing.dunning import DunningService
    from ee.cloud.billing.invoice_generation import InvoiceGenerationService

    assert callable(DunningService.process_dunning_step)
    assert InvoiceGenerationService is not None


def test_monthly_reset_uses_cloud_overlay_path():
    source = _temporal_source("billing/activities.py")
    assert "from ee.cloud.tasks.monthly_reset import run_monthly_reset" in source
    assert "ee.usage.tasks.monthly_reset" not in source


def test_monthly_reset_runs_when_cloud_overlay_is_present(monkeypatch):
    from tfc.temporal.billing import activities

    run_monthly_reset = Mock()
    real_import = builtins.__import__

    def import_with_cloud_reset(name, *args, **kwargs):
        if name == "ee.cloud.tasks.monthly_reset":
            return SimpleNamespace(run_monthly_reset=run_monthly_reset)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_with_cloud_reset)
    monkeypatch.setattr(activities, "close_old_connections", Mock())

    activities._run_monthly_reset_sync("2026-08")

    run_monthly_reset.assert_called_once_with(period="2026-08")


def test_monthly_reset_skips_cleanly_without_cloud_overlay(monkeypatch):
    from tfc.temporal.billing import activities

    real_import = builtins.__import__

    def import_without_cloud_reset(name, *args, **kwargs):
        if name == "ee.cloud.tasks.monthly_reset":
            raise ImportError("cloud overlay is not installed")
        return real_import(name, *args, **kwargs)

    logger = SimpleNamespace(info=Mock())
    monkeypatch.setattr(builtins, "__import__", import_without_cloud_reset)
    monkeypatch.setattr(activities.activity, "logger", logger)

    activities._run_monthly_reset_sync("2026-08")

    logger.info.assert_called_once_with(
        "monthly_reset_skipped_billing_is_cloud_only"
    )
