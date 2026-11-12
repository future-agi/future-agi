"""Django/Temporal integration import controls; run only in FA qualification."""

import builtins
import sys
import types
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ENFORCEMENT_MODULE = "ee.cloud.billing.budget_enforcement"
USAGE_MODULE = "ee.usage.models.usage"
TOTAL_SPEND_CATCHUP = "evaluate_total_spend_budgets_catchup"
MEASURE_NAME = "TOTAL_SPEND_BUDGET_MEASURE"
PERIOD_USAGE_CHARGES = "period_usage_charges"


def test_older_ee_overlay_preserves_dimension_catchup(monkeypatch):
    from tfc.temporal.schedules import billing

    query = Mock()
    for method in ("filter", "exclude", "values_list", "distinct"):
        getattr(query, method).return_value = query
    query.iterator.return_value = iter([("synthetic-org", "tracing_events")])
    catchup = Mock()
    logger = Mock()
    real_import = builtins.__import__

    def older_overlay(name, *args, **kwargs):
        if name == "ee.usage.models.usage":
            return SimpleNamespace(UsageBudget=SimpleNamespace(objects=query))
        if name == "ee.cloud.billing.budget_enforcement":
            # The pre-total-spend EE overlay exports only this existing symbol.
            return SimpleNamespace(evaluate_budgets_catchup=catchup)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", older_overlay)
    monkeypatch.setattr(billing, "logger", logger)
    result = billing.evaluate_budgets_catchup_activity()

    assert result["evaluated"] == 1
    catchup.assert_called_once_with("synthetic-org", "tracing_events", result["period"])
    logger.info.assert_not_called()
    logger.error.assert_called_once_with(
        "budget_catchup_symbol_unavailable",
        symbol="evaluate_total_spend_budgets_catchup",
    )
    logger.warning.assert_not_called()


@pytest.fixture
def fresh_budget_check():
    """Each test reads the billing package it lays down, not an answer an
    earlier test left in the per-process check Billing reads."""
    from tfc.views.deployment import budget_support

    budget_support.cache_clear()
    yield
    budget_support.cache_clear()


def _install_billing_package(monkeypatch, **enforcement):
    """Lay down a billing package whose enforcement module has the dimension
    catch-up plus ``enforcement``, with one active dimension budget to catch
    up. Both the scheduler and Billing's check import it from here.

    Returns the dimension catch-up.
    """
    query = Mock()
    for method in ("filter", "exclude", "values_list", "distinct"):
        getattr(query, method).return_value = query
    query.iterator.return_value = iter([("synthetic-org", "tracing_events")])
    usage = types.ModuleType(USAGE_MODULE)
    usage.UsageBudget = SimpleNamespace(objects=query)
    dimension_catchup = Mock()
    module = types.ModuleType(ENFORCEMENT_MODULE)
    module.evaluate_budgets_catchup = dimension_catchup
    for name, value in enforcement.items():
        setattr(module, name, value)
    monkeypatch.setitem(sys.modules, USAGE_MODULE, usage)
    monkeypatch.setitem(sys.modules, ENFORCEMENT_MODULE, module)
    return dimension_catchup


_NO_MEASURE = object()


@pytest.mark.parametrize(
    "measure",
    [_NO_MEASURE, "invoice_total"],
    ids=["no-measure", "invoice-total"],
)
def test_a_total_spend_catchup_whose_measure_is_not_the_period_usage_charges_is_skipped_with_an_alert_and_dimension_catchup_still_runs(
    monkeypatch, fresh_budget_check, measure
):
    # Billing says total-spend budgets are not enforced for this package, so
    # its total-spend catch-up must not pause usage either.
    from tfc.temporal.schedules import billing

    total_spend_catchup = Mock(return_value={"evaluated": 2, "period": "unused"})
    enforcement = {TOTAL_SPEND_CATCHUP: total_spend_catchup}
    if measure is not _NO_MEASURE:
        enforcement[MEASURE_NAME] = measure
    dimension_catchup = _install_billing_package(monkeypatch, **enforcement)
    logger = Mock()
    monkeypatch.setattr(billing, "logger", logger)

    result = billing.evaluate_budgets_catchup_activity()

    total_spend_catchup.assert_not_called()
    dimension_catchup.assert_called_once_with(
        "synthetic-org", "tracing_events", result["period"]
    )
    assert result["evaluated"] == 1
    logger.error.assert_called_once_with(
        "budget_catchup_total_spend_skipped",
        symbol="evaluate_total_spend_budgets_catchup",
        reason="total_spend_measure_not_period_usage_charges",
    )
    logger.warning.assert_not_called()
    logger.info.assert_not_called()


def test_a_total_spend_catchup_measured_as_the_period_usage_charges_runs_once_and_dimension_catchup_still_runs(
    monkeypatch, fresh_budget_check
):
    from tfc.temporal.schedules import billing

    total_spend_catchup = Mock(return_value={"evaluated": 2, "period": "unused"})
    dimension_catchup = _install_billing_package(
        monkeypatch,
        **{
            TOTAL_SPEND_CATCHUP: total_spend_catchup,
            MEASURE_NAME: PERIOD_USAGE_CHARGES,
        },
    )
    logger = Mock()
    monkeypatch.setattr(billing, "logger", logger)

    result = billing.evaluate_budgets_catchup_activity()

    total_spend_catchup.assert_called_once_with(result["period"])
    dimension_catchup.assert_called_once_with(
        "synthetic-org", "tracing_events", result["period"]
    )
    assert result["evaluated"] == 3
    logger.error.assert_not_called()
    logger.warning.assert_not_called()
    logger.info.assert_not_called()
