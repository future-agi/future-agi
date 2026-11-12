"""Billing reads agree on JSON selection and refusal of unknown query fields."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from rest_framework.test import APIRequestFactory

from agentcc.views.managed_credits import (
    ManagedCreditCheckoutStatusView,
    ManagedCreditSettingsView,
    ManagedCreditSummaryView,
)
from ee.usage.views.ledger_read import _LedgerReadView


@pytest.mark.parametrize("view", [
    ManagedCreditSummaryView, ManagedCreditSettingsView, ManagedCreditCheckoutStatusView,
])
@pytest.mark.django_db
def test_credit_json_reads_reject_unknown_query_before_dependencies(view):
    request = APIRequestFactory().get("/billing/", {"unexpected": "value"})
    request.organization = SimpleNamespace(id="query-test-organization", pk="query-test-organization")
    kwargs = {"session_id": "checkout-fixture"} if view is ManagedCreditCheckoutStatusView else {}
    with patch("agentcc.views.managed_credits._ledger", side_effect=AssertionError("query must be refused before dependency access")) as ledger:
        response = view.as_view(authentication_classes=[], permission_classes=[])(request, **kwargs)
    assert response.status_code == 400
    ledger.assert_not_called()


@pytest.mark.parametrize("params, expected", [
    ({"format": "json"}, {}),
    ({"format": "json", "limit": "10"}, {"limit": "10"}),
    ({"format": "csv"}, {"format": "csv"}),
    ({"unexpected": "value"}, {"unexpected": "value"}),
])
def test_json_representation_is_not_a_ledger_domain_filter(params, expected):
    view = _LedgerReadView()
    organization = object()
    view._caller = Mock(return_value=(organization, None))
    build = Mock(return_value={"rows": []})
    response = view._serve(SimpleNamespace(query_params=params), build)
    assert response.status_code == 200
    build.assert_called_once_with(organization, expected, None)
