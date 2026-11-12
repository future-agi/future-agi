"""Imagine's real admission and polling endpoints must not reveal existence.

All comparisons use complete parsed JSON envelopes, with no fields removed.
Only the external Temporal workflow starter is replaced.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from tracer.models.imagine_analysis import ImagineAnalysis
from tracer.models.project import Project
from tracer.models.saved_view import SavedView
from tracer.models.trace import Trace
from tracer.tests.test_imagine_analysis_api import (
    IMAGINE_ANALYSIS_PATH,
    create_imagine_saved_view,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def imagine_world(organization, workspace, user, observe_project):
    foreign_org = Organization.objects.create(name="NF Imagine foreign organization")
    foreign_workspace = Workspace.no_workspace_objects.create(
        name="NF Imagine foreign workspace",
        organization=foreign_org,
        created_by=user,
        is_active=True,
    )
    sibling_workspace = Workspace.no_workspace_objects.create(
        name="NF Imagine sibling workspace",
        organization=organization,
        created_by=user,
        is_active=True,
    )
    saved_view = create_imagine_saved_view(observe_project, workspace, user)
    local_trace = Trace.no_workspace_objects.create(
        project=observe_project, name="NF Imagine local trace"
    )
    traces = {}
    for name, org, ws in (
        ("organization", foreign_org, foreign_workspace),
        ("workspace", organization, sibling_workspace),
        ("project", organization, workspace),
    ):
        project = Project.no_workspace_objects.create(
            name=f"NF Imagine {name} project",
            organization=org,
            workspace=ws,
            model_type=observe_project.model_type,
            trace_type="observe",
        )
        traces[name] = Trace.no_workspace_objects.create(
            project=project, name=f"NF Imagine {name} trace"
        )
        assert project.pk != observe_project.pk, (
            "SETUP_AQ-XWS-NF: each foreign trace must belong to a different project"
        )
    assert traces["organization"].project.organization_id != organization.pk, (
        "SETUP_AQ-XWS-NF: foreign organization trace must cross the organization"
    )
    assert traces["workspace"].project.organization_id == organization.pk, (
        "SETUP_AQ-XWS-NF: sibling trace must share the caller organization"
    )
    assert traces["workspace"].project.workspace_id != workspace.pk, (
        "SETUP_AQ-XWS-NF: sibling trace must cross the workspace"
    )
    assert traces["project"].project.workspace_id == workspace.pk, (
        "SETUP_AQ-XWS-NF: other-project trace must share the caller workspace"
    )
    foreign_view = create_imagine_saved_view(
        traces["workspace"].project, sibling_workspace, user, name="NF foreign view"
    )
    assert foreign_view.workspace_id != saved_view.workspace_id, (
        "SETUP_AQ-XWS-NF: foreign saved view must belong to another workspace"
    )
    assert not ImagineAnalysis.no_workspace_objects.exists(), (
        "SETUP_AQ-XWS-NF: no pre-existing Imagine analysis may mask writes"
    )
    return SimpleNamespace(
        view=saved_view, trace=local_trace, traces=traces, foreign_view=foreign_view
    )


def _request(client, method, saved_view_id, project_id, trace_id):
    data = {"saved_view_id": str(saved_view_id), "trace_id": str(trace_id)}
    if method == "post":
        data.update(
            {
                "project_id": str(project_id),
                "widgets": [
                    {"widget_id": "summary", "prompt": "Summarize this trace."}
                ],
            }
        )
        return client.post(IMAGINE_ANALYSIS_PATH, data, format="json")
    return client.get(IMAGINE_ANALYSIS_PATH, data)


def _assert_no_analysis_writes(queries, starter):
    assert starter.call_count == 0, (
        "AQ-XWS-NF: unavailable Imagine targets must never start a workflow"
    )
    assert not ImagineAnalysis.no_workspace_objects.exists(), (
        "AQ-XWS-NF: unavailable Imagine targets must never persist an analysis"
    )
    assert not any(
        f'{verb} "{ImagineAnalysis._meta.db_table}"' in query["sql"]
        for query in queries
        for verb in ("INSERT INTO", "UPDATE", "DELETE FROM")
    ), "AQ-XWS-NF: unavailable Imagine targets must not attempt analysis writes"


@pytest.mark.parametrize("method", ["post", "get"])
@pytest.mark.parametrize("foreign_scope", ["organization", "workspace", "project"])
def test_imagine_foreign_and_absent_traces_have_identical_notfound_envelopes(
    auth_client, imagine_world, method, foreign_scope
):
    world = imagine_world
    foreign = world.traces[foreign_scope]
    missing = uuid.uuid4()
    assert Trace.no_workspace_objects.filter(pk=foreign.pk).exists(), (
        "SETUP_AQ-XWS-NF: foreign Imagine trace must actually exist"
    )
    assert not Trace.no_workspace_objects.filter(pk=missing).exists(), (
        "SETUP_AQ-XWS-NF: missing Imagine trace ID must never have been used"
    )
    control = _request(
        auth_client, "get", world.view.pk, world.view.project_id, world.trace.pk
    )
    assert control.status_code == 200, (
        "SETUP_AQ-XWS-NF: saved view and local trace must pass real polling guards"
    )
    with (
        patch("tfc.temporal.imagine.client.start_imagine_analysis") as starter,
        CaptureQueriesContext(connection) as queries,
    ):
        responses = [
            _request(
                auth_client, method, world.view.pk, world.view.project_id, trace_id
            )
            for trace_id in (foreign.pk, missing)
        ]
    observed = [(response.status_code, response.json()) for response in responses]
    assert [status for status, _ in observed] == [404, 404], (
        "AQ-XWS-NF: foreign and absent Imagine traces must both return HTTP 404"
    )
    assert observed[0] == observed[1], (
        "AQ-XWS-NF: complete foreign and absent Imagine trace envelopes must match"
    )
    assert observed[0][1]["code"] == "not_found", (
        "AQ-XWS-NF: trace refusal must retain the not-found error code"
    )
    assert "Trace not found" in str(observed[0][1]), (
        "AQ-XWS-NF: trace refusal must reach the trace guard, not saved-view lookup"
    )
    _assert_no_analysis_writes(queries, starter)


@pytest.mark.parametrize("method", ["post", "get"])
def test_imagine_foreign_and_absent_saved_views_have_identical_notfound_envelopes(
    auth_client, imagine_world, method
):
    world = imagine_world
    foreign = world.foreign_view
    missing = uuid.uuid4()
    assert SavedView.no_workspace_objects.filter(pk=foreign.pk).exists(), (
        "SETUP_AQ-XWS-NF: foreign saved view must actually exist"
    )
    assert not SavedView.no_workspace_objects.filter(pk=missing).exists(), (
        "SETUP_AQ-XWS-NF: missing saved view ID must never have been used"
    )
    # POST's project ID agrees with the existing foreign view: a project/view
    # mismatch must not accidentally substitute for the workspace boundary.
    with (
        patch("tfc.temporal.imagine.client.start_imagine_analysis") as starter,
        CaptureQueriesContext(connection) as queries,
    ):
        responses = [
            _request(
                auth_client,
                method,
                view_id,
                foreign.project_id,
                world.traces["workspace"].pk,
            )
            for view_id in (foreign.pk, missing)
        ]
    observed = [(response.status_code, response.json()) for response in responses]
    assert [status for status, _ in observed] == [404, 404], (
        "AQ-XWS-NF: foreign and absent saved views must both return HTTP 404"
    )
    assert observed[0] == observed[1], (
        "AQ-XWS-NF: complete foreign and absent saved-view envelopes must match"
    )
    assert observed[0][1]["code"] == "not_found", (
        "AQ-XWS-NF: saved-view refusal must retain the not-found error code"
    )
    assert "Saved view not found" in str(observed[0][1]), (
        "AQ-XWS-NF: foreign saved view must be refused at scoped view lookup"
    )
    _assert_no_analysis_writes(queries, starter)
