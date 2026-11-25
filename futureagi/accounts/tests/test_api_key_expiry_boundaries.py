"""Exact expiry boundaries across the three OrgApiKey authentication surfaces."""

import base64
from datetime import UTC, datetime, timedelta, timezone
from functools import partial
from unittest.mock import patch

import pytest
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIRequestFactory

from accounts.authentication import APIKeyAuthentication, LangfuseBasicAuthentication
from accounts.models.user import OrgApiKey
from mcp_server import mcp_app
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_context():
    yield
    mcp_app._clear_context()
    clear_workspace_context()


@pytest.mark.parametrize("transport", ["header", "basic", "mcp"])
@pytest.mark.parametrize("offset_hours", [-8, 0, 5.5])
@pytest.mark.parametrize("delta_us", [-1, 0, 1, None])
def test_expiry_boundary_and_timezone(
    user, workspace, transport, offset_hours, delta_us
):
    now = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
    expires_at = (
        None
        if delta_us is None
        else (now + timedelta(microseconds=delta_us)).astimezone(
            timezone(timedelta(hours=offset_hours))
        )
    )
    key = OrgApiKey.no_workspace_objects.create(
        name="boundary-key",
        organization=user.organization,
        workspace=workspace,
        user=user,
        type="user",
        expires_at=expires_at,
    )
    if transport == "mcp":
        authenticate = partial(
            mcp_app._authenticate_and_set_context, key.api_key, key.secret_key
        )
        error_type = mcp_app.APIKeyExpired
    else:
        headers = {"HTTP_X_API_KEY": key.api_key, "HTTP_X_SECRET_KEY": key.secret_key}
        auth = APIKeyAuthentication()
        if transport == "basic":
            encoded = base64.b64encode(
                f"{key.api_key}:{key.secret_key}".encode()
            ).decode("ascii")
            headers = {"HTTP_AUTHORIZATION": f"Basic {encoded}"}
            auth = LangfuseBasicAuthentication()
        request = APIRequestFactory().get("/accounts/user-info/", **headers)
        authenticate = partial(auth.authenticate, request)
        error_type = AuthenticationFailed

    with patch("django.utils.timezone.now", return_value=now):
        if delta_us is not None and delta_us <= 0:
            with pytest.raises(error_type) as exc:
                authenticate()
            if transport != "mcp":
                assert exc.value.get_codes() == "api_key_expired"
        else:
            result = authenticate()
            assert result is not None
            assert (result.user if transport == "mcp" else result[0]).id == user.id
