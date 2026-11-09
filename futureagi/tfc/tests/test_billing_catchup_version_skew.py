"""Django/Temporal integration import controls; run only in FA qualification."""

import builtins
from types import SimpleNamespace
from unittest.mock import Mock


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
