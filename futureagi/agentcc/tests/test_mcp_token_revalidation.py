"""A post-refresh check serves current vault state without refreshing again."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from agentcc.services import mcp_upstream_oauth as connect
from agentcc.services import mcp_upstream_tokens as tokens
from agentcc.tests.mcp_upstream_oauth_helpers import SERVER_ID
from agentcc.tests.test_mcp_internal_tokens import (
    _ask,
    _connected,
    _schema,
)
from agentcc.tests.test_mcp_internal_tokens import _settings as _settings
from agentcc.tests.test_mcp_internal_tokens import config as config
from agentcc.tests.test_mcp_internal_tokens import vendor as vendor

pytestmark = pytest.mark.django_db


def current_request(org, user):
    return {
        "org_id": str(org.pk),
        "user_id": str(user.pk),
        "server_id": SERVER_ID,
        "refresh": False,
    }


@pytest.mark.parametrize("seconds", [90, 120])
def test_short_refreshed_token_survives_authoritative_revalidation(
    organization, user, config, vendor, seconds
):
    _connected(organization, user, vendor, expires_in=timedelta(seconds=-1))
    replacement = connect._Tokens(
        access=connect.Sealed("synthetic-short-refresh"),
        refresh=connect.Sealed("synthetic-refresh"),
        expires_at=timezone.now() + timedelta(seconds=seconds),
        scopes=("mcp",),
    )
    with patch.object(tokens, "_refresh", return_value=tokens.RefreshResult(tokens=replacement)) as refresh:
        initial = _ask(organization, user)
        assert initial.status_code == 200 and "error" not in initial.json()
        body = current_request(organization, user)
        checked = _ask(organization, user, body=body)
        assert checked.status_code == 200
        assert "error" not in checked.json()
        assert checked.json() == initial.json()
        assert refresh.call_count == 1
        _schema("Request").validate(body)
        _schema("Success").validate(checked.json())


@pytest.mark.parametrize("state", ["revoked", "expired", "needs_reauth", "pending"])
def test_revalidation_preserves_authoritative_refusals(
    organization, user, config, vendor, state
):
    row = _connected(organization, user, vendor)
    if state == "pending":
        row.gateway_purge_pending = True
    elif state == "expired":
        row.token_expires_at = timezone.now() - timedelta(seconds=1)
    else:
        row.status = state
    row.save()
    with patch.object(tokens, "_refresh") as refresh:
        response = _ask(organization, user, body=current_request(organization, user))
    assert response.status_code == 200
    assert response.json()["error"] == ("needs_reauth" if state == "pending" else state)
    refresh.assert_not_called()


@pytest.mark.parametrize("registered", [True, False])
def test_revalidation_keeps_adapter_registration_check_without_refreshing(
    organization, user, config, vendor, registered
):
    _connected(organization, user, vendor, expires_in=timedelta(seconds=90))
    with (
        patch.object(
            tokens.connector_k2, "issued_by_registration", return_value=registered
        ) as registration,
        patch.object(tokens, "_refresh") as refresh,
    ):
        response = _ask(organization, user, body=current_request(organization, user))
    assert response.status_code == 200
    registration.assert_called_once()
    refresh.assert_not_called()
    if registered:
        assert "error" not in response.json()
        _schema("Success").validate(response.json())
    else:
        assert response.json()["error"] == "needs_reauth"


@pytest.mark.parametrize("value", [None, "false", 0, [], {}])
def test_revalidation_flag_has_boolean_contract(organization, user, value):
    body = current_request(organization, user)
    body["refresh"] = value
    assert _ask(organization, user, body=body).status_code == 400
