"""Reload pushes configuration and needs the same org role as other writes.

Exercise DRF dispatch with a recording push boundary; no gateway, database,
license service or provider is contacted. Authentication is supplied by the
request fixture so these checks isolate the org-role requirement.
"""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from agentcc.permissions import GATEWAY_ADMIN_MESSAGE
from agentcc.views.gateway import AgentccGatewayViewSet
from tfc.constants.levels import Level


def _reload(level, *, with_org=True):
    organization = SimpleNamespace(id="fixture-org") if with_org else None
    membership = Mock(return_value=level)
    user = SimpleNamespace(is_authenticated=True, get_membership_level=membership)
    request = APIRequestFactory().post(
        "/agentcc/gateways/default/reload/", {}, format="json"
    )
    request.organization = organization
    force_authenticate(request, user=user)
    with patch.object(
        AgentccGatewayViewSet, "_push_current_config", return_value=True
    ) as push:
        response = AgentccGatewayViewSet.as_view({"post": "reload"})(
            request, pk="default"
        )
    return response, push, membership, organization


@pytest.mark.parametrize("level", [Level.MEMBER, Level.VIEWER, None])
def test_reload_refuses_non_admin_before_any_config_push(level):
    response, push, membership, organization = _reload(level)

    assert response.status_code == 403
    assert GATEWAY_ADMIN_MESSAGE in str(response.data)
    push.assert_not_called()
    membership.assert_called_with(organization)


@pytest.mark.parametrize("level", [Level.OWNER, Level.ADMIN])
def test_reload_keeps_owner_and_admin_access(level):
    response, push, membership, organization = _reload(level)

    assert response.status_code == 200
    assert response.data["result"]["gateway_synced"] is True
    push.assert_called_once_with(organization)
    membership.assert_called_with(organization)


def test_reload_without_an_organization_never_pushes():
    response, push, _, _ = _reload(Level.OWNER, with_org=False)

    assert response.status_code == 403
    push.assert_not_called()


def test_configuration_reads_remain_open_to_members():
    view = AgentccGatewayViewSet()
    view.action = "config"
    organization = SimpleNamespace(id="fixture-org")
    request = SimpleNamespace(
        organization=organization,
        user=SimpleNamespace(
            is_authenticated=True,
            get_membership_level=Mock(return_value=Level.MEMBER),
        ),
    )

    assert all(
        permission.has_permission(request, view)
        for permission in view.get_permissions()
    )
