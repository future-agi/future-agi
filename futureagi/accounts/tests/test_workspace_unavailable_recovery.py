"""Stale selectors refuse data writes, not account recovery or query reads."""

from functools import wraps
from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.urls import resolve
from rest_framework.response import Response

from accounts.models.auth_token import AuthToken
from accounts.models.user import OrgApiKey, User
from accounts.models.workspace import Workspace
from accounts.tests import workspace_write_support as support
from accounts.tests.test_workspace_landing import landing as landing
from accounts.tests.test_workspace_landing_boundary import CREATE_BODY, CREATE_DATASET
from accounts.tests.test_workspace_unavailable import unavailable_selector
from tfc.utils.api_errors import first_error_code

scope_no_egress = support.scope_no_egress
write_effects = support.write_effects
pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("kind", ["missing", "inactive", "foreign"])
@pytest.mark.parametrize(
    "operation", ["workspace", "organization", "logout", "2fa", "passkey"]
)
def test_stale_selector_allows_account_recovery(
    landing, write_effects, kind, operation
):
    headers = support.credential_headers(landing.user, landing.org)
    headers["HTTP_X_WORKSPACE_ID"] = unavailable_selector(landing, kind)
    paths = {
        "workspace": "/accounts/workspace/switch/",
        "organization": "/accounts/organizations/switch/",
        "logout": "/accounts/logout/",
        "2fa": "/accounts/2fa/totp/setup/",
        # Passkeys are registered with this POST; /passkeys/ itself is GET-only.
        "passkey": "/accounts/passkey/register/options/",
    }
    body = {
        "workspace": {"new_workspace_id": str(landing.default.pk)},
        "organization": {"organization_id": str(landing.org.pk)},
    }.get(operation, {})
    response = support.request_view(paths[operation], headers, body)
    assert response.status_code == 200, response.data
    if operation == "logout":
        assert not AuthToken.objects.get(user=landing.user).is_active
    elif operation == "workspace":
        landing.user.refresh_from_db()
        assert landing.user.config["currentWorkspaceId"] == str(landing.default.pk)
    # A new session after logout must still refuse the exact same data write.
    headers.update(support.credential_headers(landing.user, landing.org))
    before = support.write_snapshot()
    refused = support.request_view(CREATE_DATASET, headers, CREATE_BODY)
    assert refused.status_code == 403, refused.data
    assert first_error_code(refused.data) == "workspace_unavailable"
    assert support.write_snapshot() == before
    write_effects.create.assert_not_called()
    write_effects.quota.assert_not_called()


def read_probe(monkeypatch):
    """Keep the real route/action/read marker and auth; replace only query I/O."""
    path = "/tracer/trace/list_traces_of_session/"
    callback = resolve(path).func
    name = callback.actions["post"]
    handler = getattr(callback.cls, name)
    assert handler._read_query_post is True

    @wraps(handler)
    def probe(self, request, *args, **kwargs):
        return Response({"workspace_id": str(request.workspace.pk)})

    monkeypatch.setattr(callback.cls, name, probe)
    return path


@pytest.mark.parametrize("credential", ["session", "key"])
def test_read_post_stale_selector_keeps_landing(
    landing, write_effects, monkeypatch, credential
):
    path = read_probe(monkeypatch)
    headers = support.credential_headers(landing.user, landing.org, credential)
    headers["HTTP_X_WORKSPACE_ID"] = str(uuid4())
    response = support.request_view(path, headers)
    assert response.status_code == 200, response.data
    assert response.data["workspace_id"] == str(landing.default.pk)
    write_effects.quota.assert_not_called()


def test_read_post_inactive_key_pin_keeps_authentication_failure(
    landing, write_effects, monkeypatch
):
    path = read_probe(monkeypatch)
    key = OrgApiKey.no_workspace_objects.create(
        name="Read pin",
        user=landing.user,
        organization=landing.org,
        workspace=landing.other,
        type="user",
    )
    Workspace._base_manager.filter(pk=landing.other.pk).update(is_active=False)
    response = support.request_view(
        path,
        {
            "HTTP_X_API_KEY": key.api_key,
            "HTTP_X_SECRET_KEY": key.secret_key,
        },
    )
    assert response.status_code == 401, response.data
    assert first_error_code(response.data) == "authentication_failed"


def test_inactive_key_user_precedes_unavailable_pin(
    landing, write_effects, monkeypatch
):
    from rest_framework.exceptions import AuthenticationFailed

    from accounts import api_key_policy

    key = OrgApiKey.no_workspace_objects.create(
        name="Inactive owner",
        user=landing.user,
        organization=landing.org,
        workspace=landing.other,
        type="user",
    )
    membership = api_key_policy.live_membership(landing.user.pk, landing.org.pk)
    # Simulate deactivation after membership was read, before the fresh user read.
    monkeypatch.setattr(
        api_key_policy, "live_membership", Mock(return_value=membership)
    )
    User._base_manager.filter(pk=landing.user.pk).update(is_active=False)
    Workspace._base_manager.filter(pk=landing.other.pk).update(is_active=False)
    with pytest.raises(AuthenticationFailed):
        api_key_policy.admit_api_key(key, workspace_unavailable_on_write=True)
