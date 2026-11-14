"""API key expiry at the MCP entry point (``_authenticate_and_set_context``).

The transport-level checks live in ``test_streamable_transport.py`` (e2e lane);
these run in the default lane so the expiry rule is always covered.
"""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone

from accounts.models.user import OrgApiKey
from mcp_server import mcp_app

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _reset_request_context():
    yield
    mcp_app._clear_context()


def _key(user, workspace, **fields):
    return OrgApiKey.objects.create(
        name=f"mcp-expiry-{uuid4().hex[:8]}",
        organization=user.organization,
        workspace=workspace,
        user=user,
        type="mcp",
        **fields,
    )


@pytest.mark.parametrize("enabled", [True, False])
def test_expired_key_raises_api_key_expired(user, workspace, enabled):
    key = _key(
        user,
        workspace,
        enabled=enabled,
        expires_at=timezone.now() - timedelta(minutes=1),
    )

    with pytest.raises(mcp_app.APIKeyExpired):
        mcp_app._authenticate_and_set_context(key.api_key, key.secret_key)


def test_disabled_key_is_still_invalid_credentials(user, workspace):
    key = _key(user, workspace, enabled=False)

    assert mcp_app._authenticate_and_set_context(key.api_key, key.secret_key) is None


@pytest.mark.parametrize(
    "expires_at",
    [None, timezone.now() + timedelta(days=1)],
    ids=["no-expiry", "future-expiry"],
)
def test_unexpired_key_authenticates(user, workspace, expires_at):
    key = _key(user, workspace, expires_at=expires_at)

    context = mcp_app._authenticate_and_set_context(key.api_key, key.secret_key)

    assert context is not None
    assert context.api_key == key
    assert context.user == user
