"""GET /api/deployment-info/: the mode the frontend gates Cloud and EE UI on,
and whether the installed billing package pauses usage at a total-spend budget
and sends budget e-mail notices (Billing says so on a budget when it does not).
A total-spend budget counts as enforced only when the package compares its
threshold with this billing period's usage charges (Usage charges on the Usage
summary page).
"""

import sys
import types

import pytest
from rest_framework.test import APIRequestFactory

from tfc.views import deployment
from tfc.views.deployment import DeploymentInfoView

pytestmark = pytest.mark.unit

ENFORCEMENT_MODULE = "ee.cloud.billing.budget_enforcement"
ENFORCEMENT_NAME = "evaluate_total_spend_budgets_catchup"
MEASURE_NAME = "TOTAL_SPEND_BUDGET_MEASURE"
MEASURE = "period_usage_charges"
NOTICES_MODULE = "ee.cloud.billing.notifications"
NOTICES_NAME = "BUDGET_NOTICE_RECIPIENT_ROLES"


def _get():
    request = APIRequestFactory().get("/api/deployment-info/")
    return DeploymentInfoView.as_view()(request)


def _module(name, **attributes):
    module = types.ModuleType(name)
    for attribute, value in attributes.items():
        setattr(module, attribute, value)
    return module


def _catchup(period):
    return {"evaluated": 0, "period": period}


class _UnreadableModule(types.ModuleType):
    """A billing module whose names cannot be read (a lazy attribute that
    fails while it loads)."""

    def __getattr__(self, name):
        raise RuntimeError("billing module failed to load " + name)


def _install(monkeypatch, enforcement, notices):
    """Lay the given billing modules where the check imports them from.

    ``None`` makes that import fail with ImportError, as on OSS.
    """
    monkeypatch.setitem(sys.modules, ENFORCEMENT_MODULE, enforcement)
    monkeypatch.setitem(sys.modules, NOTICES_MODULE, notices)


def _with_both():
    return (
        _module(
            ENFORCEMENT_MODULE, **{ENFORCEMENT_NAME: _catchup, MEASURE_NAME: MEASURE}
        ),
        _module(NOTICES_MODULE, **{NOTICES_NAME: ("Owner", "Admin")}),
    )


@pytest.fixture(autouse=True)
def fresh_budget_check():
    """Each test reads the billing modules it lays down, not ones an earlier
    test left in the per-process answer."""
    clear = getattr(getattr(deployment, "budget_support", None), "cache_clear", None)
    if clear is not None:
        clear()
    yield
    if clear is not None:
        clear()


@pytest.mark.parametrize(
    "deployment_region, license_key, mode",
    [
        ("US", "", "cloud"),
        (" us ", "", "cloud"),
        ("false", "key", "ee"),
        ("false", "", "oss"),
        ("", "", "oss"),
    ],
)
def test_reads_the_region_as_is_cloud_env_does(
    monkeypatch, deployment_region, license_key, mode
):
    monkeypatch.setenv("CLOUD_DEPLOYMENT", deployment_region)
    monkeypatch.setenv("EE_LICENSE_KEY", license_key)

    response = _get()

    assert response.status_code == 200
    assert response.data["result"]["mode"] == mode


def test_answers_the_mode_and_both_budget_features_as_booleans(monkeypatch):
    _install(monkeypatch, *_with_both())

    result = _get().data["result"]

    assert set(result) == {
        "mode",
        "total_spend_budgets_enforced",
        "budget_notices_delivered",
    }
    assert isinstance(result["total_spend_budgets_enforced"], bool)
    assert isinstance(result["budget_notices_delivered"], bool)


def test_total_spend_budgets_are_enforced_and_notices_delivered_when_the_package_provides_both(
    monkeypatch,
):
    _install(monkeypatch, *_with_both())

    response = _get()

    assert response.status_code == 200
    assert response.data["result"]["total_spend_budgets_enforced"] is True
    assert response.data["result"]["budget_notices_delivered"] is True


def test_nothing_is_enforced_or_delivered_when_the_billing_package_is_not_installed(
    monkeypatch,
):
    _install(monkeypatch, None, None)

    response = _get()

    assert response.status_code == 200
    assert response.data["result"]["total_spend_budgets_enforced"] is False
    assert response.data["result"]["budget_notices_delivered"] is False


def test_a_package_without_the_total_spend_catchup_does_not_enforce_total_spend_budgets(
    monkeypatch,
):
    # The package's other enforcement entry point and the period usage
    # measure are present; only the total-spend catch-up the scheduler calls
    # is missing.
    enforcement = _module(
        ENFORCEMENT_MODULE,
        evaluate_total_spend_budget=lambda org_id, period: None,
        **{MEASURE_NAME: MEASURE},
    )
    _, notices = _with_both()
    _install(monkeypatch, enforcement, notices)

    result = _get().data["result"]

    assert result["total_spend_budgets_enforced"] is False
    assert result["budget_notices_delivered"] is True


_NO_MEASURE = object()


@pytest.mark.parametrize(
    "measure",
    [_NO_MEASURE, "invoice_total", "PERIOD_USAGE_CHARGES", "", None],
    ids=[
        "no-measure",
        "invoice-total",
        "measure-in-other-case",
        "empty-measure",
        "measure-none",
    ],
)
def test_a_package_whose_total_spend_is_not_the_period_usage_charges_does_not_enforce_total_spend_budgets(
    monkeypatch, measure
):
    # The package runs the total-spend catch-up but does not say it compares
    # the threshold with this billing period's usage charges, so a total-spend
    # budget does not count as enforced. Budget notices are unaffected.
    attributes = {ENFORCEMENT_NAME: _catchup}
    if measure is not _NO_MEASURE:
        attributes[MEASURE_NAME] = measure
    _, notices = _with_both()
    _install(monkeypatch, _module(ENFORCEMENT_MODULE, **attributes), notices)

    response = _get()

    assert response.status_code == 200
    assert response.data["result"]["total_spend_budgets_enforced"] is False
    assert response.data["result"]["budget_notices_delivered"] is True


def test_a_package_without_the_recipient_roles_does_not_deliver_budget_notices(
    monkeypatch,
):
    enforcement, _ = _with_both()
    _install(monkeypatch, enforcement, _module(NOTICES_MODULE))

    result = _get().data["result"]

    assert result["total_spend_budgets_enforced"] is True
    assert result["budget_notices_delivered"] is False


def test_a_check_that_fails_reports_not_enforced_and_not_delivered_without_failing_the_request(
    monkeypatch,
):
    _install(
        monkeypatch,
        _UnreadableModule(ENFORCEMENT_MODULE),
        _UnreadableModule(NOTICES_MODULE),
    )

    response = _get()

    assert response.status_code == 200
    assert response.data["result"]["total_spend_budgets_enforced"] is False
    assert response.data["result"]["budget_notices_delivered"] is False


def test_the_billing_package_is_checked_once_per_process(monkeypatch):
    _install(monkeypatch, *_with_both())
    first = _get().data["result"]

    # The package can no longer be imported; the first answer stands.
    _install(monkeypatch, None, None)
    second = _get().data["result"]

    assert first["total_spend_budgets_enforced"] is True
    assert first["budget_notices_delivered"] is True
    assert second["total_spend_budgets_enforced"] is True
    assert second["budget_notices_delivered"] is True
