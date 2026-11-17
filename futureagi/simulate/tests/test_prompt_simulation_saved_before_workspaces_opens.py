"""A prompt simulation whose template was saved before workspaces opens.

Its run test is in the organization's default workspace and its prompt
template, made before workspaces existed, has none. Reading the run test, the
template's workspace is resolved by the shared workspace rule, so the detail
answers with the prompt-based simulation and the run pages know it is one.
The selected version and the scenarios that name that template are still read
in the request workspace exactly, so the opened page shows the template's name
but no version, and lists only the scenarios that name no such template.

The shared rule opens nothing else. The default workspace does not open a
template that sits in another, named workspace of the organization, and a
named workspace does not open a template that has no workspace. The
organization stays bound: a template of another organization keeps the run
test hidden, as does a run test of another organization. Writing is not
widened: an edit through the same scoping still answers as for a run test that
does not exist and changes nothing.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from simulate.models import RunTest, Scenarios

# Where a run test and its template sit when the shared rule does not admit the
# template from the run test's workspace. A named workspace is one of the
# organization's workspaces that is not its default one.
OUTSIDE_THE_SHARED_RULE = {
    "template-in-a-named-workspace": ("default", "named"),
    "named-workspace-template-saved-before-workspaces": ("named", None),
}


@pytest.fixture
def old_prompt_simulation(organization, workspace):
    """A prompt simulation on v2 of a template that has no workspace."""
    template = PromptTemplate.objects.create(
        name="Prompt saved before workspaces",
        organization=organization,
        workspace=workspace,
    )
    versions = [
        PromptVersion.objects.create(original_template=template, template_version=label)
        for label in ("v1", "v2")
    ]
    run_test = RunTest.objects.create(
        name="Prompt simulation saved before workspaces",
        source_type=RunTest.SourceTypes.PROMPT,
        prompt_template=template,
        prompt_version=versions[1],
        organization=organization,
        workspace=workspace,
    )
    # Use update so saving cannot fill the default workspace back in.
    PromptTemplate.no_workspace_objects.filter(pk=template.pk).update(workspace=None)
    return SimpleNamespace(template=template, run_test=run_test)


@pytest.fixture
def named_workspace(organization, user):
    """One of the organization's workspaces that is not its default one."""
    return Workspace.no_workspace_objects.create(
        name="Named workspace",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )


def _place(model, row, workspace):
    # Use update so saving cannot fill the request workspace back in.
    model.no_workspace_objects.filter(pk=row.pk).update(workspace=workspace)


def _body(response):
    data = response.json()
    return data.get("result", data) if isinstance(data, dict) else data


def _detail(auth_client, run_test_id):
    return auth_client.get(f"/simulate/run-tests/{run_test_id}/")


def _another_organization(user):
    organization = Organization.objects.create(name="Another organization")
    workspace, _ = Workspace.no_workspace_objects.get_or_create(
        organization=organization,
        is_default=True,
        defaults={"name": "Another organization's workspace", "created_by": user},
    )
    return SimpleNamespace(organization=organization, workspace=workspace)


@pytest.mark.django_db
def test_a_prompt_simulation_saved_before_workspaces_opens_from_the_default_workspace(
    auth_client, workspace, old_prompt_simulation
):
    template = old_prompt_simulation.template
    assert workspace.is_default
    assert PromptTemplate.no_workspace_objects.get(pk=template.pk).workspace_id is None

    response = _detail(auth_client, old_prompt_simulation.run_test.id)

    assert response.status_code == 200, response.json()
    body = _body(response)
    assert body["id"] == str(old_prompt_simulation.run_test.id)
    assert body["source_type"] == RunTest.SourceTypes.PROMPT
    assert body["prompt_template_detail"]["name"] == template.name


@pytest.mark.django_db
def test_the_opened_simulation_shows_no_version_and_no_scenario_naming_its_template(
    auth_client, organization, workspace, old_prompt_simulation
):
    """Its version and the scenarios naming its template are read exactly.

    Both are resolved in the request workspace exactly, so the page shows the
    template's name, no version, and only the scenarios that name no template
    saved before workspaces.
    """
    template = old_prompt_simulation.template
    run_test = old_prompt_simulation.run_test
    naming_the_template, naming_no_template = (
        Scenarios.objects.create(
            name=name,
            source="Prompt simulation scenario",
            source_type=Scenarios.SourceTypes.PROMPT,
            prompt_template=prompt_template,
            organization=organization,
            workspace=workspace,
        )
        for name, prompt_template in (
            ("Scenario naming the template", template),
            ("Scenario naming no template", None),
        )
    )
    run_test.scenarios.add(naming_the_template, naming_no_template)

    response = _detail(auth_client, run_test.id)

    assert response.status_code == 200, response.json()
    body = _body(response)
    assert body["source_type"] == RunTest.SourceTypes.PROMPT
    assert body["prompt_template_detail"]["name"] == template.name
    assert body["prompt_version"] is None
    assert body["prompt_version_detail"] is None
    assert body["scenarios"] == [str(naming_no_template.id)]
    assert [scenario["id"] for scenario in body["scenarios_detail"]] == [
        str(naming_no_template.id)
    ]


@pytest.mark.django_db
@pytest.mark.parametrize("placement", list(OUTSIDE_THE_SHARED_RULE))
def test_a_template_the_shared_workspace_rule_does_not_admit_keeps_the_run_hidden(
    auth_client, workspace, named_workspace, old_prompt_simulation, placement
):
    workspaces = {"default": workspace, "named": named_workspace, None: None}
    run_place, template_place = OUTSIDE_THE_SHARED_RULE[placement]
    run_workspace = workspaces[run_place]
    run_test = old_prompt_simulation.run_test
    template = old_prompt_simulation.template
    _place(RunTest, run_test, run_workspace)
    auth_client.set_workspace(run_workspace)
    # In the run test's own workspace the template opens the run test.
    _place(PromptTemplate, template, run_workspace)
    opened = _detail(auth_client, run_test.id)

    _place(PromptTemplate, template, workspaces[template_place])
    response = _detail(auth_client, run_test.id)
    missing = _detail(auth_client, uuid4())

    assert opened.status_code == 200, opened.json()
    assert _body(opened)["source_type"] == RunTest.SourceTypes.PROMPT
    assert response.status_code == missing.status_code == 404
    assert response.json() == missing.json()
    assert template.name not in response.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "other", ["template-of-another-organization", "run-test-of-another-organization"]
)
def test_a_prompt_simulation_of_another_organization_stays_hidden(
    auth_client, user, old_prompt_simulation, other
):
    elsewhere = _another_organization(user)
    PromptTemplate.no_workspace_objects.filter(
        pk=old_prompt_simulation.template.pk
    ).update(organization=elsewhere.organization)
    if other == "run-test-of-another-organization":
        RunTest.no_workspace_objects.filter(
            pk=old_prompt_simulation.run_test.pk
        ).update(organization=elsewhere.organization, workspace=elsewhere.workspace)

    response = _detail(auth_client, old_prompt_simulation.run_test.id)
    missing = _detail(auth_client, uuid4())

    assert response.status_code == missing.status_code == 404
    assert response.json() == missing.json()
    assert old_prompt_simulation.template.name not in response.content.decode()


@pytest.mark.django_db
def test_an_edit_of_a_prompt_simulation_saved_before_workspaces_is_not_widened(
    auth_client, old_prompt_simulation
):
    run_test = old_prompt_simulation.run_test

    response = auth_client.patch(
        f"/simulate/run-tests/{run_test.id}/", {"name": "Renamed"}, format="json"
    )
    missing = auth_client.patch(
        f"/simulate/run-tests/{uuid4()}/", {"name": "Renamed"}, format="json"
    )

    assert response.status_code == missing.status_code == 404
    assert response.json() == missing.json()
    assert RunTest.no_workspace_objects.get(pk=run_test.pk).name == run_test.name
