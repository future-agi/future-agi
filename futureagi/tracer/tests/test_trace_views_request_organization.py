"""Trace navigation and the users list use the organization a request is made in.

A person can belong to two organizations: one is stored on the account and the
other is the one they are working in. Moving to the next or previous trace
builds its annotation columns for the organization the request is made in, and
the users list asks for the users of that organization. A person with one
organization gets both built for their only organization.
"""

import uuid
from unittest.mock import patch

import pytest

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.ai_model import AIModel
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tracer.models.project import Project
from tracer.models.project_version import ProjectVersion
from tracer.models.trace import Trace
from tracer.utils.annotations import build_annotation_subqueries

TRACE_NAVIGATION = "/tracer/trace/get_trace_id_by_index/"
USERS = "/tracer/users/"
USERS_PAGE = {"table": [], "total_count": 0, "total_pages": 0}


def _make_second_org_workspace(user):
    organization = Organization.objects.create(
        name=f"Second Org {uuid.uuid4().hex[:8]}"
    )
    organization_membership = OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=organization,
        role=OrganizationRoles.OWNER,
        level=Level.OWNER,
        is_active=True,
    )
    workspace = Workspace.no_workspace_objects.create(
        name=f"Second Workspace {uuid.uuid4().hex[:8]}",
        organization=organization,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=workspace,
        role="Workspace Owner",
        level=Level.OWNER,
        is_active=True,
        organization_membership=organization_membership,
    )
    return organization, workspace


def _make_project(organization, workspace, trace_type):
    return Project.no_workspace_objects.create(
        name=f"Request organization {uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type=trace_type,
        metadata={},
        config=[],
    )


def _make_trace(organization, workspace):
    project = _make_project(organization, workspace, "experiment")
    project_version = ProjectVersion.no_workspace_objects.create(
        project=project,
        name="Run",
        version="v1",
        metadata={},
    )
    trace = Trace.no_workspace_objects.create(
        project=project,
        project_version=project_version,
        name="Trace",
        metadata={},
        input={"prompt": "hello"},
        output={"response": "world"},
    )
    return project_version, trace


def _navigate(auth_client, project_version, trace):
    with patch(
        "tracer.views.trace._build_annotation_subqueries_impl",
        wraps=build_annotation_subqueries,
    ) as build:
        response = auth_client.get(
            TRACE_NAVIGATION,
            {
                "project_version_id": str(project_version.id),
                "trace_id": str(trace.id),
            },
        )
    return response, build


def _list_users(auth_client, project):
    with patch("tracer.views.trace.UsersListManager") as manager_cls:
        manager_cls.return_value.list_payload.return_value = USERS_PAGE
        response = auth_client.get(USERS, {"project_id": str(project.id)})
    return response, manager_cls


@pytest.mark.django_db
def test_trace_navigation_in_second_organization_builds_its_annotations(
    auth_client, user
):
    second, second_workspace = _make_second_org_workspace(user)
    project_version, trace = _make_trace(second, second_workspace)
    assert user.organization_id != second.id
    auth_client.set_workspace(second_workspace)

    response, build = _navigate(auth_client, project_version, trace)

    assert response.status_code == 200
    assert [call.args[2].id for call in build.call_args_list] == [second.id]


@pytest.mark.django_db
def test_trace_navigation_in_one_organization_builds_its_annotations(
    auth_client, organization, project_version, trace
):
    response, build = _navigate(auth_client, project_version, trace)

    assert response.status_code == 200
    assert [call.args[2].id for call in build.call_args_list] == [organization.id]


@pytest.mark.django_db
def test_users_list_in_second_organization_asks_for_its_users(
    auth_client, user, organization, workspace
):
    _make_project(organization, workspace, "observe")
    second, second_workspace = _make_second_org_workspace(user)
    project = _make_project(second, second_workspace, "observe")
    assert user.organization_id == organization.id
    auth_client.set_workspace(second_workspace)

    response, manager_cls = _list_users(auth_client, project)

    assert response.status_code == 200
    assert response.json()["result"] == USERS_PAGE
    kwargs = manager_cls.call_args.kwargs
    assert kwargs["organization_id"] == str(second.id)
    assert kwargs["allowed_project_ids"] == [str(project.id)]


@pytest.mark.django_db
def test_users_list_in_one_organization_asks_for_its_users(
    auth_client, organization, observe_project
):
    response, manager_cls = _list_users(auth_client, observe_project)

    assert response.status_code == 200
    assert response.json()["result"] == USERS_PAGE
    kwargs = manager_cls.call_args.kwargs
    assert kwargs["organization_id"] == str(organization.id)
    assert kwargs["allowed_project_ids"] == [str(observe_project.id)]
