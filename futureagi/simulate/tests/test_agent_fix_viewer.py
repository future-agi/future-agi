"""A viewer reads a fix and cannot change one. The workspace write check runs in the auth
class, so these log in for real: ``force_authenticate`` skips it."""

from unittest.mock import patch

import pytest
from rest_framework import status

from accounts.models import Workspace
from accounts.models.workspace import WorkspaceMembership
from accounts.tests.test_rbac_regressions import (
    WORKSPACE_WRITE_DENIED,
    _jwt_client,
    _make_workspace_user,
    _member_client,
    _viewer_client,
)
from simulate.models import AgentFixCandidate, AgentFixRun
from simulate.pydantic_schemas.agent_fix import Surface
from simulate.tests.test_agent_fix_services import KEYS, _candidate, _fix_run
from simulate.tests.test_agent_fix_services import (  # noqa: F401 - fixtures
    environment as environment,
)
from simulate.tests.test_agent_fix_services import source_run as source_run
from simulate.tests.test_agent_fix_services import temporal as temporal
from simulate.tests.test_agent_fix_services import versioning_on as versioning_on
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixtures
    storage as storage,
)
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

pytestmark = pytest.mark.django_db

BASE = "/simulate/api/agent-fix-runs"


@pytest.fixture(autouse=True)
def entitled():
    with patch("simulate.views.agent_fix.check_ee_feature") as gate:
        yield gate


@pytest.fixture
def fix(source_run):
    fix_run = _fix_run(source_run)
    candidate = _candidate(fix_run)
    AgentFixCandidate.no_workspace_objects.filter(id=candidate.id).update(
        outcome=AgentFixCandidate.Outcome.WINNER
    )
    return fix_run, candidate


def _launch_body(source_run):
    return {
        "source_run_id": str(source_run.test_execution_id),
        "target_scenario_keys": [KEYS[0]],
        "surfaces": [Surface.PROMPT],
        "budget_credits": 500,
        "max_minutes": 30,
    }


def _writes(fix_run, candidate):
    base = f"{BASE}/{fix_run.id}"
    return [
        f"{BASE}/",
        f"{base}/stop/",
        f"{base}/retry/",
        f"{base}/candidates/{candidate.id}/make-default/",
        f"{base}/candidates/{candidate.id}/pull-request/",
    ]


def test_a_viewer_reads_a_fix(organization, workspace, environment, fix):
    viewer = _viewer_client(organization, workspace)

    read = viewer.get(f"{BASE}/{fix[0].id}/")
    listed = viewer.get(f"{BASE}/", {"environment_id": str(environment.id)})

    assert (read.status_code, listed.status_code) == (200, 200), read.content
    assert [one["id"] for one in listed.json()["results"]] == [str(fix[0].id)]


def test_a_viewer_cannot_change_a_fix(organization, workspace, fix):
    viewer = _viewer_client(organization, workspace)
    fixes = AgentFixRun.no_workspace_objects.count()

    for url in _writes(*fix):
        response = viewer.post(url, {}, format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN, (url, response.data)
        assert WORKSPACE_WRITE_DENIED in str(response.data), url

    assert AgentFixRun.no_workspace_objects.count() == fixes
    fix[0].refresh_from_db()
    assert fix[0].state == AgentFixRun.State.RUNNING


def test_a_member_passes_the_same_check(organization, workspace, fix):
    member = _member_client(organization, workspace)

    response = member.post(f"{BASE}/{fix[0].id}/stop/", {}, format="json")

    assert response.status_code == status.HTTP_200_OK, response.data
    fix[0].refresh_from_db()
    assert fix[0].state == AgentFixRun.State.STOPPING


def test_write_access_elsewhere_does_not_reach_this_workspace(
    organization, workspace, source_run, fix
):
    """A viewer here, a member there: the header names there, and the fix stays out of reach."""
    user = _make_workspace_user(
        organization,
        workspace,
        org_role=OrganizationRoles.MEMBER_VIEW_ONLY,
        org_level=Level.VIEWER,
        ws_role=OrganizationRoles.WORKSPACE_VIEWER,
        ws_level=Level.WORKSPACE_VIEWER,
        prefix="fix-two-workspaces",
    )
    elsewhere = Workspace.no_workspace_objects.create(
        name="elsewhere", organization=organization, created_by=user
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=elsewhere,
        role=OrganizationRoles.WORKSPACE_MEMBER,
        level=Level.WORKSPACE_MEMBER,
        is_active=True,
    )
    client = _jwt_client(user, organization, elsewhere)

    launch, *on_the_fix = _writes(*fix)
    started = client.post(launch, _launch_body(source_run), format="json")
    assert started.status_code == status.HTTP_404_NOT_FOUND, started.data
    for url in on_the_fix:
        response = client.post(url, {}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND, (url, response.data)

    fix[0].refresh_from_db()
    assert fix[0].state == AgentFixRun.State.RUNNING
    assert AgentFixRun.no_workspace_objects.count() == 1
