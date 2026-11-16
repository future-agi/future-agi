"""Tag writes require write access to the workspace, from real memberships.

The ClickHouse target read is replaced; the authorization reads, the project
lookup and the assignment write all run against the test PostgreSQL database.
"""

import uuid
from contextlib import nullcontext
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from accounts.models import User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.ai_model import AIModel
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import set_workspace_context
from tracer.models.entity_tag_assignment import EntityTagAssignment
from tracer.models.project import Project
from tracer.services import entity_tag_assignments as assignments
from tracer.services.clickhouse.v2.entity_tag_targets import (
    TRACE_ASSIGNMENT_HOUR,
    TagTarget,
)

HOUR = datetime(2020, 1, 1, tzinfo=UTC)
START = datetime(2020, 1, 1, 0, 30, tzinfo=UTC)
TRACE = "33333333-3333-3333-3333-333333333333"

ORG_ROLES = {
    Level.ADMIN: OrganizationRoles.ADMIN,
    Level.MEMBER: OrganizationRoles.MEMBER,
    Level.VIEWER: OrganizationRoles.MEMBER_VIEW_ONLY,
}
WORKSPACE_ROLES = {
    Level.WORKSPACE_ADMIN: OrganizationRoles.WORKSPACE_ADMIN,
    Level.WORKSPACE_MEMBER: OrganizationRoles.WORKSPACE_MEMBER,
    Level.WORKSPACE_VIEWER: OrganizationRoles.WORKSPACE_VIEWER,
}


@pytest.fixture
def tag_workspace(db, organization, user):
    # A second, non-default workspace: no legacy default-workspace widening.
    return Workspace.objects.create(
        name="Tag Workspace",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )


@pytest.fixture
def tag_project(db, organization, tag_workspace):
    return Project.objects.create(
        name="Tag Project",
        organization=organization,
        workspace=tag_workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
        metadata={},
    )


def _member(organization, workspace, *, org_level, workspace_level):
    member = User.objects.create_user(
        email=f"tag-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Tag Member",
        organization=organization,
        organization_role=ORG_ROLES[org_level],
    )
    org_membership = OrganizationMembership.no_workspace_objects.create(
        user=member,
        organization=organization,
        role=ORG_ROLES[org_level],
        level=org_level,
        is_active=True,
    )
    if workspace_level is not None:
        WorkspaceMembership.no_workspace_objects.create(
            user=member,
            workspace=workspace,
            role=WORKSPACE_ROLES[workspace_level],
            level=workspace_level,
            is_active=True,
            organization_membership=org_membership,
        )
    return member


@pytest.fixture
def ch_target(monkeypatch, tag_project):
    """A verified root span of TRACE in tag_project, without ClickHouse."""
    reads = []

    def resolve(**kwargs):
        reads.append(kwargs)
        row = {
            "project_id": str(tag_project.id),
            "trace_id": TRACE,
            "id": "0123456789abcdef",
            "start_hour": HOUR,
            "start_time": START,
            "observation_type": "span",
            "service_name": "svc",
            "_version": "2",
            "is_deleted": 0,
            "parent_span_id": "",
            "tags": "[]",
        }
        key = (str(tag_project.id), "trace", TRACE, "", TRACE_ASSIGNMENT_HOUR, "", "")
        return TagTarget(key=key, row=row)

    monkeypatch.setattr(assignments, "V2AnalyticsQueryService", lambda: object())
    monkeypatch.setattr(assignments, "resolve_tag_target", resolve)
    # The test database has no statement-timeout role setup to rely on.
    monkeypatch.setattr(assignments, "_postgres_budget", lambda deadline: nullcontext())
    return reads


def _write(member, organization, workspace, project):
    # What request authentication establishes for every API request.
    set_workspace_context(workspace=workspace, organization=organization, user=member)
    request = SimpleNamespace(
        user=member, organization=organization, workspace=workspace
    )
    return assignments.update_entity_tags(
        request=request,
        trace_id=TRACE,
        data={
            "project_id": str(project.id),
            "trace_id": TRACE,
            "span_id": "0123456789abcdef",
            "start_hour": HOUR,
            "observation_type": "span",
            "service_name": "svc",
            "expected_start_time": START,
            "expected_version": "2",
            "tags": [{"name": "reviewed"}],
            "expected_tag_revision": 0,
        },
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "org_level, workspace_level",
    [
        # An org Member who is only a Viewer of this workspace.
        (Level.MEMBER, Level.WORKSPACE_VIEWER),
        (Level.VIEWER, Level.WORKSPACE_VIEWER),
        # An org Member with no membership of this workspace.
        (Level.MEMBER, None),
    ],
)
def test_without_workspace_write_access_nothing_is_written(
    organization, tag_workspace, tag_project, ch_target, org_level, workspace_level
):
    member = _member(
        organization,
        tag_workspace,
        org_level=org_level,
        workspace_level=workspace_level,
    )
    with pytest.raises(assignments.TagAssignmentError) as raised:
        _write(member, organization, tag_workspace, tag_project)
    assert raised.value.code == "tag_write_forbidden"
    assert raised.value.status_code == 403
    assert ch_target == []
    assert not EntityTagAssignment.objects.filter(project=tag_project).exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "org_level, workspace_level",
    [
        (Level.MEMBER, Level.WORKSPACE_MEMBER),
        (Level.VIEWER, Level.WORKSPACE_MEMBER),
        (Level.MEMBER, Level.WORKSPACE_ADMIN),
        # Org Admins write to every workspace of their organization.
        (Level.ADMIN, None),
    ],
)
def test_workspace_writers_save_the_assignment(
    organization, tag_workspace, tag_project, ch_target, org_level, workspace_level
):
    member = _member(
        organization,
        tag_workspace,
        org_level=org_level,
        workspace_level=workspace_level,
    )
    result = _write(member, organization, tag_workspace, tag_project)
    assert result["tag_revision"] == 1
    assert result["manual_tags"] == [{"name": "reviewed"}]
    saved = EntityTagAssignment.objects.get(project=tag_project)
    assert (saved.entity_kind, saved.trace_id, saved.revision) == ("trace", TRACE, 1)
