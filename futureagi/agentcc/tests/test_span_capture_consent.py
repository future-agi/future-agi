"""Span body capture requires explicit, audited organization-admin consent."""

from unittest.mock import patch

import pytest
from rest_framework.exceptions import PermissionDenied

from accounts.models.audit_log import AuditLog
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.permissions import GATEWAY_ADMIN_MESSAGE
from agentcc.services.org_config_writer import (
    activate_config_version,
    write_config_version,
)
from tfc.constants.levels import Level

pytestmark = pytest.mark.integration
ACTION = "agentcc.trace_content_changed"


def write(org, user, enabled):
    return write_config_version(
        org,
        {"privacy": {"trace_content": enabled}},
        user=user,
        push=lambda config: True,
    )[0]


def test_new_organization_has_no_capture_consent(organization):
    config = AgentccOrgConfig(organization=organization)
    assert config.privacy.get("trace_content", False) is False


def test_non_admin_enable_refused_without_changes(user):
    org = user.organization
    before = AgentccOrgConfig.no_workspace_objects.filter(organization=org).count()
    with patch.object(type(user), "get_membership_level", return_value=Level.MEMBER):
        with pytest.raises(PermissionDenied, match=GATEWAY_ADMIN_MESSAGE):
            write(org, user, True)
    assert (
        AgentccOrgConfig.no_workspace_objects.filter(organization=org).count() == before
    )
    assert not AuditLog.objects.filter(organization=org, action=ACTION).exists()


def test_admin_enable_disable_audits_actor_and_values(user):
    org = user.organization
    with patch.object(type(user), "get_membership_level", return_value=Level.ADMIN):
        for enabled in (True, False):
            config = write(org, user, enabled)
            assert config.privacy["trace_content"] is enabled
    rows = list(
        AuditLog.objects.filter(organization=org, action=ACTION).order_by("created_at")
    )
    assert len(rows) == 2
    for row, enabled in zip(rows, (True, False), strict=True):
        assert row.actor_id == user.id
        assert row.organization_id == org.id
        assert row.created_at is not None
        assert row.changes == {"trace_content": {"old": not enabled, "new": enabled}}


def test_capture_enable_without_actor_refused(organization):
    with pytest.raises(PermissionDenied, match=GATEWAY_ADMIN_MESSAGE):
        write(organization, None, True)


def test_non_admin_api_enable_uses_existing_permission_error(auth_client, user):
    before = AgentccOrgConfig.no_workspace_objects.filter(
        organization=user.organization
    ).count()
    with patch.object(type(user), "get_membership_level", return_value=Level.MEMBER):
        response = auth_client.post(
            "/agentcc/org-configs/", {"privacy": {"trace_content": True}}, format="json"
        )
    assert response.status_code == 403
    assert GATEWAY_ADMIN_MESSAGE in str(response.data)
    assert (
        AgentccOrgConfig.no_workspace_objects.filter(
            organization=user.organization
        ).count()
        == before
    )


def test_admin_activation_is_audited(user):
    with patch.object(type(user), "get_membership_level", return_value=Level.ADMIN):
        enabled = write(user.organization, user, True)
        write(user.organization, user, False)
        activate_config_version(enabled, user=user, push=lambda config: True)
    rows = AuditLog.objects.filter(organization=user.organization, action=ACTION)
    assert rows.count() == 3


def test_new_workspace_defaults_off_through_config_api(auth_client, user, workspace):
    assert workspace.organization_id == user.organization_id
    with patch(
        "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway",
        return_value=True,
    ):
        response = auth_client.post("/agentcc/org-configs/", {}, format="json")
    assert response.status_code == 200
    active = AgentccOrgConfig.no_workspace_objects.get(
        organization=user.organization, is_active=True
    )
    assert active.privacy.get("trace_content", False) is False
    assert not AuditLog.objects.filter(
        organization=user.organization, action=ACTION
    ).exists()


def test_admin_api_enable_and_disable_are_audited(auth_client, user):
    with (
        patch(
            "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway",
            return_value=True,
        ),
        patch("tfc.ee_gating.check_ee_feature"),
        patch.object(type(user), "get_membership_level", return_value=Level.ADMIN),
    ):
        for enabled in (True, False):
            response = auth_client.post(
                "/agentcc/org-configs/",
                {"privacy": {"trace_content": enabled}},
                format="json",
            )
            assert response.status_code == 200
            active = AgentccOrgConfig.no_workspace_objects.get(
                organization=user.organization, is_active=True
            )
            assert active.privacy["trace_content"] is enabled
    rows = list(
        AuditLog.objects.filter(organization=user.organization, action=ACTION).order_by(
            "created_at"
        )
    )
    assert len(rows) == 2
    for row, enabled in zip(rows, (True, False), strict=True):
        assert row.actor_id == user.id and row.organization_id == user.organization_id
        assert row.changes == {"trace_content": {"old": not enabled, "new": enabled}}
        assert row.created_at is not None


def test_api_capture_fields_expose_the_disclosure():
    from agentcc.serializers.contracts import (
        GatewayConfigPatchRequestSerializer,
        GatewayConfigResultSerializer,
        OrgConfigBulkItemSerializer,
    )
    from agentcc.serializers.org_config import (
        AgentccOrgConfigSerializer,
        AgentccOrgConfigWriteSerializer,
    )
    from agentcc.trace_content import DISCLOSURE

    for serializer in (
        GatewayConfigPatchRequestSerializer,
        GatewayConfigResultSerializer,
        OrgConfigBulkItemSerializer,
        AgentccOrgConfigSerializer,
        AgentccOrgConfigWriteSerializer,
    ):
        assert DISCLOSURE in serializer().fields["privacy"].help_text


def test_audit_failure_does_not_enable_capture(user):
    before = AgentccOrgConfig.no_workspace_objects.filter(
        organization=user.organization
    ).count()
    with (
        patch.object(type(user), "get_membership_level", return_value=Level.ADMIN),
        patch("tfc.utils.audit.log_audit", return_value=None),
    ):
        with pytest.raises(RuntimeError, match="audit"):
            write(user.organization, user, True)
    assert (
        AgentccOrgConfig.no_workspace_objects.filter(
            organization=user.organization
        ).count()
        == before
    )
