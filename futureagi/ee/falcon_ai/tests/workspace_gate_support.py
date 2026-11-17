"""Real membership and licensed-deployment inputs for interactive gate controls."""

from types import SimpleNamespace

import pytest

from accounts.models.workspace import WorkspaceMembership
from ai_tools.base import ToolContext
from ee.falcon_ai.tests import test_workspace_scope as native_scope
from tfc.capabilities import service
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.licensing.types import (
    DeploymentFlavor,
    DeploymentLocation,
    LicenseSnapshot,
    LicenseState,
)

scope_state = native_scope.scope_state


def grant_workspace(state):
    grant, _ = WorkspaceMembership._base_manager.update_or_create(
        user=state.member,
        workspace=state.private,
        defaults={
            "organization_membership": state.membership,
            "is_active": True,
            "role": OrganizationRoles.WORKSPACE_MEMBER,
            "level": Level.WORKSPACE_MEMBER,
        },
    )
    return grant


@pytest.fixture
def active_context(scope_state):
    grant_workspace(scope_state)
    assert scope_state.membership.is_active
    assert scope_state.member.can_access_workspace(scope_state.private)
    return ToolContext(
        scope_state.member,
        scope_state.org,
        scope_state.private,
        interactive_falcon_socket=True,
    )


@pytest.fixture
def licensed_features(monkeypatch):
    """Configure test license inputs, not an allow-returning feature/access mock."""
    snapshot = LicenseSnapshot(
        state=LicenseState.ACTIVE,
        features=frozenset({"falcon_ai", "agentic_eval"}),
    )
    monkeypatch.setattr(service, "_configured", True)
    monkeypatch.setattr(service, "_deployment_flavor", DeploymentFlavor.SELF_HOSTED_EE)
    monkeypatch.setattr(service, "_deployment_location", DeploymentLocation.SELF_HOSTED)
    monkeypatch.setattr(
        service, "_license_resolver", SimpleNamespace(get_snapshot=lambda: snapshot)
    )
    assert service.check("falcon_ai").allowed
    assert service.check("agentic_eval").allowed
