"""Organization-wide workspace authorization follows platform role precedence."""

import pytest

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from tracer.sql_workbench import service
from tracer.sql_workbench.access import AccessDenied, authorize
from tracer.sql_workbench.tests.test_permissions import create_job
from tracer.sql_workbench.tests.test_permissions import tenants as _tenants

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenants(monkeypatch):
    return _tenants.__wrapped__(monkeypatch)


@pytest.mark.parametrize("org_level", [8, 15])
@pytest.mark.parametrize("workspace_role", [None, 1])
def test_org_admin_without_workspace_membership_can_use_sql(
    tenants, org_level, workspace_role
):
    (actor, project, _), _ = tenants
    OrganizationMembership.no_workspace_objects.filter(user_id=actor.user_id).update(
        level=org_level
    )
    rows = WorkspaceMembership.no_workspace_objects.filter(user_id=actor.user_id)
    if workspace_role is None:
        rows.delete()
    else:
        rows.update(level=workspace_role, is_active=False)
    assert authorize(actor, project.id, write=True) == project
    assert rows.count() == (workspace_role is not None)
    job = create_job(
        actor, project, state="succeeded", result={"columns": [], "rows": []}
    )
    assert service.execution(actor, project.id, job.id) == job
    # Demotion invalidates even an existing result owned by this actor.
    OrganizationMembership.no_workspace_objects.filter(user_id=actor.user_id).update(
        level=3
    )
    with pytest.raises(AccessDenied):
        service.execution(actor, project.id, job.id)


@pytest.mark.parametrize("org_level", [1, 3])
def test_ordinary_nonmember_cannot_access_workspace(tenants, org_level):
    (actor, project, _), _ = tenants
    OrganizationMembership.no_workspace_objects.filter(user_id=actor.user_id).update(
        level=org_level
    )
    WorkspaceMembership.no_workspace_objects.filter(user_id=actor.user_id).delete()
    with pytest.raises(AccessDenied):
        authorize(actor, project.id)


@pytest.mark.parametrize(
    "invalid",
    ["workspace_inactive", "workspace_deleted", "user_inactive", "org_inactive"],
)
def test_admin_access_still_requires_active_scope(tenants, invalid):
    (actor, project, _), _ = tenants
    OrganizationMembership.no_workspace_objects.filter(user_id=actor.user_id).update(
        level=8
    )
    WorkspaceMembership.no_workspace_objects.filter(user_id=actor.user_id).delete()
    if invalid.startswith("workspace"):
        Workspace.no_workspace_objects.filter(id=actor.workspace_id).update(
            **(
                {"deleted": True}
                if invalid.endswith("deleted")
                else {"is_active": False}
            )
        )
    elif invalid == "user_inactive":
        User.objects.filter(id=actor.user_id).update(is_active=False)
    else:
        OrganizationMembership.no_workspace_objects.filter(
            user_id=actor.user_id
        ).update(is_active=False)
    with pytest.raises(AccessDenied):
        authorize(actor, project.id)
