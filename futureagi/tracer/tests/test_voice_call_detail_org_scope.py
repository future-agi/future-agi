"""Ownership scoping for the voice call detail endpoint.

The endpoint resolves a trace's project from ClickHouse and then checks that the
caller owns it. That check must use the *request-scoped* organization (the one
the user is currently acting in, injected from ``X-Organization-Id``), not the
organization stored on the user row. A user whose active organization differs
from their home organization owns projects the user-row check cannot see, and
every lookup 404s — which strands the drawer on its list-row stub.

Scope note: the test client injects ``request.organization`` straight from the
header, so these cases pin the view's *scoping* behaviour only. That the header
itself cannot name an organization the user has no membership in is enforced
upstream in authentication and covered by ``accounts.tests.test_multi_org_auth``.
"""

import uuid
from types import SimpleNamespace

import pytest

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from tracer.models.project import Project

VOICE_CALL_DETAIL_URL = "/tracer/trace/voice_call_detail/"
CH_QUERY_PATH = (
    "tracer.services.clickhouse.query_service."
    "AnalyticsQueryService.execute_ch_query"
)


def _make_org_project(user, label):
    """An organization + workspace + project that is NOT the user's home org."""
    suffix = uuid.uuid4().hex[:8]
    org = Organization.objects.create(name=f"{label} Org {suffix}")
    workspace = Workspace.no_workspace_objects.create(
        name=f"{label} Workspace {suffix}",
        organization=org,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    project = Project.objects.create(
        name=f"{label} Project",
        organization=org,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
        metadata={},
        config=[],
    )
    return org, workspace, project


def _ch_returning(project_id):
    """Stub CH: resolve the trace to ``project_id``, then find no root span.

    Only the first query (project resolution) matters here. The second returns
    empty so the request stops right after the ownership gate with a distinct
    message — that message is how the tests tell "ownership passed" apart from
    "ownership rejected", without standing up a full span fixture.
    """
    calls = {"n": 0}

    def fake(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return SimpleNamespace(data=[{"project_id": str(project_id)}])
        return SimpleNamespace(data=[])

    return fake


@pytest.mark.django_db
def test_detail_allows_project_in_active_org_not_user_home_org(
    auth_client, user, monkeypatch
):
    # The user's home organization stays whatever the fixture made it; the
    # request acts in a different organization that owns the project.
    _, active_workspace, project = _make_org_project(user, "Active")
    assert project.organization_id != user.organization_id
    auth_client.set_workspace(active_workspace)
    monkeypatch.setattr(CH_QUERY_PATH, _ch_returning(project.id))

    response = auth_client.get(
        VOICE_CALL_DETAIL_URL, {"trace_id": str(uuid.uuid4())}
    )

    # Ownership passed: the request reached the root-span lookup. Scoping the
    # check to the user row instead would have stopped at "trace_id not found".
    assert "trace_id not found" not in str(response.data)
    assert "root span" in str(response.data)


@pytest.mark.django_db
def test_detail_still_rejects_project_in_unrelated_org(
    auth_client, user, monkeypatch
):
    # Guard on the loosened check: acting in one organization must not grant
    # access to a project owned by a different one.
    _, active_workspace, _ = _make_org_project(user, "Active")
    _, _, foreign_project = _make_org_project(user, "Foreign")
    auth_client.set_workspace(active_workspace)
    monkeypatch.setattr(CH_QUERY_PATH, _ch_returning(foreign_project.id))

    response = auth_client.get(
        VOICE_CALL_DETAIL_URL, {"trace_id": str(uuid.uuid4())}
    )

    assert "trace_id not found" in str(response.data)
