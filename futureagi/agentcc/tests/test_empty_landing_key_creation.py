"""F04: refuse invisible personal-key issuance, not empty-landing reads/removal."""

from unittest.mock import Mock

import pytest
from django.urls import resolve

from accounts.models.audit_log import AuditLog
from accounts.models.workspace import WorkspaceMembership
from accounts.tests.workspace_write_support import scope_no_egress as scope_no_egress
from agentcc.models import AgentccAPIKey
from agentcc.tests.test_agentcc_permissions import _user_with_level
from agentcc.tests.test_personal_key_self_service import gateway as gateway
from agentcc.tests.test_personal_key_self_service import session_client
from agentcc.tests.test_personal_key_workspace_boundary import own_key
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = pytest.mark.django_db
KEYS = "/agentcc/api-keys/"
REFUSAL = "Choose or join a workspace to create API keys."


@pytest.mark.parametrize("level", [Level.MEMBER, Level.VIEWER])
@pytest.mark.parametrize("membership_state", ["removed", "inactive"])
@pytest.mark.parametrize("operation", ["create", "replace"])
def test_empty_landing_refuses_before_personal_key_issuance(
    organization, workspace, gateway, scope_no_egress, monkeypatch,
    level, membership_state, operation,
):
    clear_workspace_context()
    user = _user_with_level(organization, level)
    source = own_key(user, organization) if operation == "replace" else None
    memberships = WorkspaceMembership._base_manager.filter(user=user)
    if membership_state == "removed":
        memberships.delete()
    else:
        memberships.update(is_active=False)
    client = session_client(user, organization, workspace)
    client._credentials.pop("HTTP_X_WORKSPACE_ID")
    # Neither body scope nor replacement of an existing key grants a landing.
    payload = {"name": "private-request-name", "workspace": str(workspace.pk)}
    if source is not None:
        payload.update(rotates_key_id=str(source.pk), revoke_rotated_key=True)
    before_keys = list(AgentccAPIKey._base_manager.order_by("pk").values())
    before_audits = list(AuditLog._base_manager.order_by("pk").values_list("pk", flat=True))
    view_globals = resolve(KEYS).func.cls.create.__globals__
    issuance = Mock(wraps=view_globals["key_creation_transaction"])
    monkeypatch.setitem(view_globals, "key_creation_transaction", issuance)
    try:
        response = client.post(KEYS, payload, format="json")
        assert response.status_code == 403, response.data
        assert response.data == {
            "status": False,
            "type": "permission_error",
            "code": "personal_key_workspace_required",
            "detail": REFUSAL,
            "message": REFUSAL,
            "error": REFUSAL,
            "result": REFUSAL,
        }
        issuance.assert_not_called()
        gateway.create_key.assert_not_called()
        gateway.import_committed_key.assert_not_called()
        gateway.revoke_key_confirmed.assert_not_called()
        gateway.revoke_key.assert_not_called()
        assert list(AgentccAPIKey._base_manager.order_by("pk").values()) == before_keys
        assert list(
            AuditLog._base_manager.order_by("pk").values_list("pk", flat=True)
        ) == before_audits
    finally:
        clear_workspace_context()


@pytest.mark.parametrize("level", [Level.OWNER, Level.ADMIN, Level.MEMBER, Level.VIEWER])
@pytest.mark.parametrize("explicit_workspace", [True, False])
def test_personal_key_creation_with_resolved_landing_still_succeeds(
    organization, workspace, gateway, scope_no_egress, level, explicit_workspace,
):
    clear_workspace_context()
    user = _user_with_level(organization, level)
    client = session_client(user, organization, workspace)
    if not explicit_workspace:
        # An accessible default landing is sufficient; no new header requirement.
        client._credentials.pop("HTTP_X_WORKSPACE_ID")
    try:
        response = client.post(KEYS, {"name": "visible-personal-key"}, format="json")
        assert response.status_code == 201, response.data
        result = response.data["result"]
        row = AgentccAPIKey._base_manager.get(pk=result["id"])
        assert row.user_id == user.pk
        assert row.organization_id == organization.pk
        assert row.workspace_id is None  # Keys remain org-wide, not workspace-owned.
        assert row.status == AgentccAPIKey.ACTIVE
        assert result["key"]
        gateway.import_committed_key.assert_called_once()
        detail = client.get(f"{KEYS}{row.pk}/")
        assert detail.status_code == 200, detail.data
        assert "key" not in detail.data["result"]
        listed = client.get(KEYS)
        assert listed.status_code == 200, listed.data
        assert result["id"] in [key["id"] for key in listed.data["result"]]
    finally:
        clear_workspace_context()
