"""Explicit write destinations must never silently become the landing workspace."""

from uuid import uuid4

import pytest

from accounts.models.organization import Organization
from accounts.models.user import OrgApiKey
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.tests import workspace_write_support as support
from accounts.tests.test_workspace_landing import landing as landing
from accounts.tests.test_workspace_landing_boundary import CREATE_BODY, CREATE_DATASET
from model_hub.models.develop_dataset import Dataset
from tfc.utils.api_errors import first_error_code

scope_no_egress = support.scope_no_egress
write_effects = support.write_effects
pytestmark = pytest.mark.django_db


def unavailable_selector(state, kind):
    if kind == "missing":
        return str(uuid4())
    if kind == "malformed":
        return "not-a-workspace-id"
    if kind == "inactive":
        Workspace._base_manager.filter(pk=state.other.pk).update(is_active=False)
    elif kind == "foreign":
        org = Organization.objects.create(name="Foreign selector organization")
        Workspace._base_manager.filter(pk=state.other.pk).update(organization=org)
    elif kind == "unjoined":
        WorkspaceMembership._base_manager.filter(user=state.user).delete()
    return str(state.other.pk)


@pytest.mark.parametrize(
    "kind", ["missing", "inactive", "foreign", "unjoined", "malformed"]
)
@pytest.mark.parametrize("selector", ["header", "query"])
@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_unavailable_write_has_no_effects(
    landing, write_effects, kind, selector, method
):
    target = unavailable_selector(landing, kind)
    headers = support.credential_headers(landing.user, landing.org)
    path = CREATE_DATASET
    if selector == "header":
        headers["HTTP_X_WORKSPACE_ID"] = target
    else:
        headers["QUERY_STRING"] = f"workspace_id={target}"
    before = support.write_snapshot()
    memberships = support.membership_snapshot()
    response = support.request_view(path, headers, CREATE_BODY, method=method)
    assert response.status_code == 403, response.data
    assert first_error_code(response.data) == "workspace_unavailable"
    assert support.write_snapshot() == before
    assert support.membership_snapshot() == memberships
    write_effects.quota.assert_not_called()
    write_effects.create.assert_not_called()


@pytest.mark.parametrize("selector", ["absent", "valid"])
@pytest.mark.parametrize("credential", ["session", "key"])
def test_available_write_uses_exact_destination(
    landing, write_effects, selector, credential
):
    headers = support.credential_headers(landing.user, landing.org, credential)
    destination = landing.default if selector == "absent" else landing.other
    if selector == "valid":
        headers["HTTP_X_WORKSPACE_ID"] = str(destination.pk)
    response = support.request_view(CREATE_DATASET, headers, CREATE_BODY)
    assert response.status_code in (200, 201), response.data
    rows = Dataset._base_manager.filter(name=CREATE_BODY["dataset_name"])
    assert rows.count() == 1
    assert rows.get().workspace_id == destination.pk
    write_effects.create.assert_called_once()


@pytest.mark.parametrize("kind", ["missing", "inactive", "foreign", "unjoined"])
@pytest.mark.parametrize("selector", ["header", "query"])
def test_unavailable_read_keeps_landing_behavior(
    landing, write_effects, kind, selector
):
    target = unavailable_selector(landing, kind)
    headers = support.credential_headers(landing.user, landing.org)
    headers["HTTP_X_WORKSPACE_ID" if selector == "header" else "QUERY_STRING"] = (
        target if selector == "header" else f"workspace_id={target}"
    )
    before = support.write_snapshot()
    response = support.request_view("/accounts/user-info/", headers, method="get")
    assert response.status_code == 200, response.data
    assert support.write_snapshot() == before
    write_effects.quota.assert_not_called()


@pytest.mark.parametrize("method", ["post", "get"])
def test_deactivated_key_pin_never_redirects_writes(landing, write_effects, method):
    key = OrgApiKey.no_workspace_objects.create(
        name="Pinned unavailable",
        user=landing.user,
        organization=landing.org,
        workspace=landing.other,
        type="user",
    )
    Workspace._base_manager.filter(pk=landing.other.pk).update(is_active=False)
    headers = {"HTTP_X_API_KEY": key.api_key, "HTTP_X_SECRET_KEY": key.secret_key}
    before = support.write_snapshot()
    response = support.request_view(
        CREATE_DATASET if method == "post" else "/accounts/user-info/",
        headers,
        CREATE_BODY if method == "post" else None,
        method=method,
    )
    # Existing personal-key admission already rejects an inactive binding on
    # reads. Preserve that 401, rather than mistakenly expecting a fallback.
    assert response.status_code == (403 if method == "post" else 401), response.data
    assert first_error_code(response.data) == (
        "workspace_unavailable" if method == "post" else "authentication_failed"
    )
    assert support.write_snapshot() == before
    write_effects.quota.assert_not_called()
    write_effects.create.assert_not_called()
