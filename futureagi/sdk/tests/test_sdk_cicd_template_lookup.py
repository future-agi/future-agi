"""Which template an SDK CI/CD evaluation run (/sdk/api/v1/evaluate-pipeline/) uses.

CI/CD matches a built-in or any of the caller's organization's templates,
whatever their workspace, as before, and never another organization's. When
rows share a name, the run uses the built-in first, then the caller's
template in its workspace, then its other templates, as the other SDK
endpoints decide (sdk.utils.template_lookup); before, a shared name answered a
server error. The run uses the template the request check accepted.

The real serializers, view and run creation run; only the evaluation dispatch
(_handle_async_eval) is replaced, and it records which template it was given.
"""

from uuid import uuid4

import pytest

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.evals_metric import EvalTemplate

pytestmark = pytest.mark.django_db

URL = "/sdk/api/v1/evaluate-pipeline/"
CONFIG = {"eval_type_id": "TestEval", "required_keys": ["query"], "output": "score"}
# For a row that must lose a shared name: the request's {"query": [...]} checked
# against it would be refused for an unexpected key.
LOSER_CONFIG = {
    "eval_type_id": "TestEval",
    "required_keys": ["other"],
    "output": "score",
}
SHARED = "cicd_lookup_shared_name"


def template(
    name, organization, workspace, owner="user", stored_name=None, config=None
):
    """A template saved with exactly this organization, workspace and name; old
    rows can share a name that save() now refuses, so both are written again
    without save()."""
    row = EvalTemplate.no_workspace_objects.create(
        name=name,
        owner=owner,
        organization=organization,
        workspace=workspace,
        config=dict(config or CONFIG),
    )
    EvalTemplate.no_workspace_objects.filter(pk=row.pk).update(
        organization=organization, workspace=workspace, name=stored_name or name
    )
    row.refresh_from_db()
    assert row.organization_id == (organization.id if organization else None)
    assert row.workspace_id == (workspace.id if workspace else None)
    return row


@pytest.fixture
def second_workspace(user, organization):
    return Workspace.objects.create(
        name="Second Workspace",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )


@pytest.fixture
def pipeline(auth_client, organization, monkeypatch):
    from sdk.utils import cicd_evaluations
    from tracer.models.project import Project

    project, _ = Project.objects.get_or_create(
        name="cicd-lookup-project",
        organization=organization,
        trace_type="observe",
        defaults={"model_type": "GenerativeLLM"},
    )
    dispatched = []

    def dispatch(eval_template, **kwargs):
        dispatched.append(eval_template.pk)
        return []

    monkeypatch.setattr(cicd_evaluations, "_handle_async_eval", dispatch)

    def post(name):
        return auth_client.post(
            URL,
            {
                "project_name": project.name,
                "version": f"v-{uuid4().hex[:8]}",
                "eval_data": [{"eval_template": name, "inputs": {"query": ["q"]}}],
            },
            format="json",
        )

    post.dispatched = dispatched
    return post


def assert_runs(pipeline, row):
    response = pipeline(row.name)
    assert response.status_code == 200, response.content.decode()[:500]
    assert pipeline.dispatched == [row.pk]


# In each shared-name test the row that must lose is saved first, so only the
# rank, not the order of saving, can pick the winner.


def test_builtin_comes_before_an_own_template_of_the_same_name(
    pipeline, organization, workspace
):
    own = template(
        "cicd_shared_own",
        organization,
        workspace,
        stored_name=SHARED,
        config=LOSER_CONFIG,
    )
    builtin = template(
        "cicd_shared_builtin", None, None, owner="system", stored_name=SHARED
    )
    assert own.created_at < builtin.created_at
    assert_runs(pipeline, builtin)


def test_own_template_in_the_workspace_comes_before_one_in_another_workspace(
    pipeline, organization, workspace, second_workspace
):
    # A run that looked the name up again without the caller's workspace would
    # take the older row here; it must use the one the request check accepted.
    elsewhere = template(
        "cicd_shared_elsewhere",
        organization,
        second_workspace,
        stored_name=SHARED,
        config=LOSER_CONFIG,
    )
    here = template("cicd_shared_here", organization, workspace, stored_name=SHARED)
    assert elsewhere.created_at < here.created_at
    assert_runs(pipeline, here)


def test_own_template_in_another_workspace_is_still_found(
    pipeline, organization, second_workspace
):
    assert_runs(
        pipeline, template("cicd_other_workspace", organization, second_workspace)
    )


def test_own_template_without_a_workspace_is_still_found(pipeline, organization):
    assert_runs(pipeline, template("cicd_no_workspace", organization, None))


def test_another_organizations_template_is_not_found(pipeline, user):
    other = Organization.objects.create(name="Other Organization")
    other_workspace = Workspace.objects.create(
        name="Other Workspace",
        organization=other,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    row = template("cicd_other_organization", other, other_workspace)
    response = pipeline(row.name)
    assert response.status_code == 400
    assert "not found" in response.content.decode()
    assert pipeline.dispatched == []
