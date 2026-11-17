"""Ledger envelopes cover failures raised before a view handler runs."""
import json
from unittest.mock import patch

import jsonschema
import pytest
from django.db import OperationalError
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework.test import APIRequestFactory

from accounts.authentication import APIKeyAuthentication
from ee.usage.serializers.ledger_openapi import READ
from ee.usage.views.ledger_read import (
    BalanceView, EntriesView, InvoicesView, UsageView,
    WorkspaceEntriesView, WorkspaceUsageView,
)


@pytest.mark.django_db
@pytest.mark.parametrize("view", [BalanceView, EntriesView, InvoicesView, UsageView,
                                  WorkspaceEntriesView, WorkspaceUsageView])
@pytest.mark.parametrize("query", ["", "?format=csv"])
@pytest.mark.parametrize("exception,code,statuses", [
    (AuthenticationFailed("Invalid auth token"), "unauthenticated", {401, 403}),
    (AuthenticationFailed("Access token expired"), "unauthenticated", {401, 403}),
    (AuthenticationFailed("Invalid API key or secret key"), "unauthenticated", {401, 403}),
    (PermissionDenied("No active organization"), "forbidden", {403}),
    (OperationalError("private database detail"), "unavailable", {503}),
])
def test_authentication_errors_use_the_ledger_envelope(view, query, exception, code, statuses):
    request = APIRequestFactory().get("/usage/ledger/v1/balance/" + query)
    kwargs = {"workspace_id": "00000000-0000-4000-8000-000000000001"} if view.workspace_route else {}
    with patch.object(APIKeyAuthentication, "authenticate", side_effect=exception):
        response = view.as_view()(request, **kwargs)
    response.render()
    body = json.loads(response.content)
    jsonschema.validate(body, {"$ref": "#/$defs/Error", "$defs": READ["$defs"]})
    assert response.status_code in statuses
    assert body["error"]["code"] == code
    assert response["Content-Type"].startswith("application/json")
    assert "private database detail" not in response.content.decode()
