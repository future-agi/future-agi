"""An empty workspace landing must not prevent personal account maintenance."""

import pytest

from accounts.models.user import OrgApiKey
from accounts.tests import test_workspace_landing_boundary as boundary_tests
from accounts.tests import workspace_write_support as write_support
from accounts.tests.workspace_write_support import (
    credential_headers,
    membership_snapshot,
    request_view,
    write_snapshot,
)
from tfc.utils.api_errors import first_error_code

boundary = boundary_tests.boundary
landing = boundary_tests.landing
scope_no_egress = write_support.scope_no_egress
write_effects = write_support.write_effects


@pytest.mark.parametrize("boundary", ["absent", "inactive-default"], indirect=True)
def test_empty_landing_can_set_own_timezone(boundary, write_effects):
    headers = credential_headers(boundary.user, boundary.landing.org)
    before = membership_snapshot()
    response = request_view(
        "/accounts/me/timezone/", headers, {"timezone": "Asia/Kolkata"}
    )
    assert response.status_code == 200, response.data
    boundary.user.refresh_from_db()
    assert boundary.user.last_timezone == "Asia/Kolkata"
    assert membership_snapshot() == before


@pytest.mark.parametrize("boundary", ["absent", "inactive-default"], indirect=True)
def test_empty_landing_can_create_and_delete_own_personal_key(
    boundary, write_effects, monkeypatch
):
    # Keep credential telemetry/revocation publication off the network; retain
    # the real route, session authentication, ownership check and persistence.
    monkeypatch.setattr(
        "tfc.deployment_telemetry.events.record_event", lambda *a, **k: None
    )
    monkeypatch.setattr("accounts.views.keys._publish_key_revocation", lambda key: None)
    headers = credential_headers(boundary.user, boundary.landing.org)
    before = membership_snapshot()
    created = request_view(
        "/accounts/key/generate_secret_key/",
        headers,
        {"key_name": "Empty landing personal key"},
    )
    assert created.status_code == 200
    key = OrgApiKey._base_manager.get(
        user=boundary.user, name="Empty landing personal key", deleted=False
    )
    assert key.organization_id == boundary.landing.org.pk
    assert key.type == "user"
    assert key.workspace_id is None
    deleted = request_view(
        "/accounts/key/delete_secret_key/",
        headers,
        {"key_id": str(key.pk)},
        method="delete",
    )
    assert deleted.status_code == 200
    key.refresh_from_db()
    assert key.deleted and not key.enabled
    assert membership_snapshot() == before

    # Account exemptions must not open workspace data writes, even for the same
    # live session that just created and revoked a credential.
    data_before = write_snapshot()
    refused = request_view(
        boundary_tests.CREATE_DATASET, headers, boundary_tests.CREATE_BODY
    )
    assert refused.status_code == 403, refused.data
    assert first_error_code(refused.data) == "workspace_required"
    assert write_snapshot() == data_before
    assert membership_snapshot() == before
    write_effects.quota.assert_not_called()
    write_effects.create.assert_not_called()
