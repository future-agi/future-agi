"""Workspace roles on Instant eval and Insights: a Workspace Viewer reads every
run, summary, row, export, report and estimate, and is refused everything
that writes or spends (a start, a run control, a report run, a draft, a
preview, a question to a report, a config edit) with the workspace write
check's 403, before any handler runs. A Workspace Member is let through.

The clients sign in for real, so the authentication class's write check runs
as it does in the app (the forced-auth fixture skips it).
"""

from __future__ import annotations

import uuid

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import WorkspaceMembership
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tracer.models.eval_task import EvalTask
from tracer.models.insights import InsightConfig, InsightRun
from tracer.tests.test_instant_eval_insights_licensing import (
    _keep_service_state as _keep_service_state,
)
from tracer.tests.test_instant_eval_insights_licensing import (
    endpoints,
    enterprise,
)
from tracer.views import insights as insight_views

pytestmark = pytest.mark.django_db

REFUSED = "Write access denied to this workspace"
# POSTs that write nothing, open to Viewers: a query too long for a URL, and
# the counts and prices the Ask page and a new report show.
READ_POSTS = {
    "/tracer/instant-eval/rows/",
    "/tracer/instant-eval/export/",
    "/tracer/instant-eval/estimate/",
    "/tracer/insights/estimate/",
}


def _signed_in(organization, workspace, *, org_role, org_level, ws_role, ws_level):
    member = User.objects.create_user(
        email=f"role-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Role tester",
        organization=organization,
        organization_role=org_role,
    )
    org_membership, _ = OrganizationMembership.no_workspace_objects.update_or_create(
        user=member,
        organization=organization,
        defaults={"role": org_role, "level": org_level, "is_active": True},
    )
    WorkspaceMembership.no_workspace_objects.update_or_create(
        user=member,
        workspace=workspace,
        defaults={
            "role": ws_role,
            "level": ws_level,
            "organization_membership": org_membership,
            "is_active": True,
        },
    )
    client = APIClient()
    login = client.post(
        "/accounts/token/",
        {"email": member.email, "password": "testpassword123"},
        format="json",
    )
    assert login.status_code == status.HTTP_200_OK, login.content
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {login.data['access']}",
        HTTP_X_ORGANIZATION_ID=str(organization.id),
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )
    return client


@pytest.fixture
def viewer(organization, workspace):
    return _signed_in(
        organization,
        workspace,
        org_role=OrganizationRoles.MEMBER_VIEW_ONLY,
        org_level=Level.VIEWER,
        ws_role=OrganizationRoles.WORKSPACE_VIEWER,
        ws_level=Level.WORKSPACE_VIEWER,
    )


@pytest.fixture
def member(organization, workspace):
    return _signed_in(
        organization,
        workspace,
        org_role=OrganizationRoles.MEMBER,
        org_level=Level.MEMBER,
        ws_role=OrganizationRoles.WORKSPACE_MEMBER,
        ws_level=Level.WORKSPACE_MEMBER,
    )


@pytest.fixture(autouse=True)
def licensed(monkeypatch):
    enterprise("instant_eval", "insights")
    # Nothing reaches Temporal or spends, whoever gets through.
    monkeypatch.setattr(insight_views, "_start", lambda run: None)
    monkeypatch.setattr(insight_views, "_spend", lambda *a: True)


def _every_endpoint():
    return endpoints("instant-eval/") + endpoints("insights/")


def _writes(method, path) -> bool:
    return method != "get" and path not in READ_POSTS


def _call(client, method, path, project):
    body = {"project_id": str(project.id)}
    if method == "get":
        return client.get(path, body)
    return getattr(client, method)(path, body, format="json")


def _written():
    return (
        EvalTask.objects.count(),
        InsightConfig.objects.count(),
        InsightRun.objects.count(),
    )


def test_a_viewer_is_refused_every_write_before_its_handler(viewer, observe_project):
    before = _written()
    writes = [(m, p) for m, p, _ in _every_endpoint() if _writes(m, p)]
    # Start and control; config patch and delete, draft, preview, run create,
    # run stop and ask.
    assert len(writes) == 2 + 7
    for method, path in writes:
        response = _call(viewer, method, path, observe_project)
        assert response.status_code == status.HTTP_403_FORBIDDEN, (method, path)
        assert REFUSED in str(response.data), (method, path)
    assert _written() == before


def test_a_viewer_reads_every_run_summary_row_export_report_and_estimate(
    viewer, observe_project
):
    reads = [(m, p) for m, p, _ in _every_endpoint() if not _writes(m, p)]
    # Runs, summary, estimate, rows and export (GET and POST); config list
    # and detail, estimate, run detail, units and a unit's cited image.
    assert len(reads) == 7 + 6
    for method, path in reads:
        response = _call(viewer, method, path, observe_project)
        # Each reaches its handler: a 200, or its own answer to these ids.
        assert response.status_code != status.HTTP_403_FORBIDDEN, (method, path)
        assert REFUSED not in str(response.data), (method, path)


def test_a_member_reaches_every_handler(member, observe_project):
    for method, path, _ in _every_endpoint():
        response = _call(member, method, path, observe_project)
        assert response.status_code != status.HTTP_403_FORBIDDEN, (method, path)
        assert REFUSED not in str(response.data), (method, path)


def test_a_viewer_cant_save_the_jev_evaluator_a_question_becomes(
    viewer, observe_project
):
    """The Ask page saves each question as an evaluator before it starts:
    that write is refused a Viewer the same way."""
    response = viewer.post(
        "/tracer/custom-eval-config/",
        {"project": str(observe_project.id), "name": "Was it resolved?"},
        format="json",
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert REFUSED in str(response.data)
