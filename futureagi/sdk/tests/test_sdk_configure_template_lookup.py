"""/sdk/api/v1/configure-evaluations/ finds the template a request names the way the other SDK
evaluation views do: a built-in, or a template of the caller's organization in the caller's
workspace. It answers 200 and stores one configuration for that template. Any other name, such
as another organization's template, answers 400 with the same sentence as a name no template has,
and stores nothing. Templates saved with no workspace, and names old rows share, are covered by
test_sdk_template_name_lookup.py."""

import pytest
from django.test import override_settings
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.evals_metric import EvalTemplate
from tracer.models.external_eval_config import ExternalEvalConfig, PlatformChoices

URL = "/sdk/api/v1/configure-evaluations/"
CODE = "def evaluate(input, **kwargs):\n    return 1.0\n"
UNKNOWN = "configure_lookup_no_such_template"


def code_template(name, organization, workspace, owner="user"):
    """A code template saved with exactly this organization and workspace. save() stamps
    the context's organization and workspace on a row saved while a workspace context is
    set, so they are written again without save()."""
    template = EvalTemplate.no_workspace_objects.create(
        name=name,
        owner=owner,
        organization=organization,
        workspace=workspace,
        eval_type="code",
        config={
            "eval_type_id": "CustomCodeEval",
            "code": CODE,
            "required_keys": ["input"],
        },
    )
    EvalTemplate.no_workspace_objects.filter(pk=template.pk).update(
        organization=organization, workspace=workspace
    )
    template.refresh_from_db()
    assert template.organization_id == (organization.id if organization else None)
    assert template.workspace_id == (workspace.id if workspace else None)
    return template


@pytest.fixture
def builtin_template(db):
    return code_template("configure_lookup_builtin", None, None, owner="system")


@pytest.fixture
def other_organization_template(db, user):
    other = Organization.objects.create(name="Other Organization")
    other_workspace = Workspace.objects.create(
        name="Other Workspace",
        organization=other,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    return code_template("configure_lookup_other_organization", other, other_workspace)


def configure(client, name):
    # The stored configuration requires every Langfuse setting; these are never used.
    settings = dict.fromkeys(
        ExternalEvalConfig.get_required_credentials(PlatformChoices.LANGFUSE), "unused"
    )
    return client.post(
        URL,
        {
            "eval_config": {"eval_templates": name, "inputs": {"input": "input"}},
            "platform": "langfuse",
            "custom_eval_name": f"{name} on langfuse",
            **settings,
        },
        format="json",
    )


def stored(template):
    return ExternalEvalConfig.no_workspace_objects.filter(
        eval_template=template
    ).count()


@pytest.mark.django_db
@pytest.mark.parametrize("paused", [False, True], ids=["setting-off", "paused"])
def test_builtin_template_is_configured_and_stored(
    auth_client, organization, builtin_template, paused
):
    with override_settings(CUSTOM_CODE_EVALS_PAUSED=paused):
        response = configure(auth_client, builtin_template.name)
    assert response.status_code == status.HTTP_200_OK, response.content.decode()[:500]
    assert stored(builtin_template) == 1
    config = ExternalEvalConfig.no_workspace_objects.get(eval_template=builtin_template)
    assert config.organization_id == organization.id


@pytest.mark.django_db
@override_settings(CUSTOM_CODE_EVALS_PAUSED=False)
def test_another_organizations_template_is_not_found_and_nothing_is_stored(
    auth_client, other_organization_template
):
    response = configure(auth_client, other_organization_template.name)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert stored(other_organization_template) == 0


@pytest.mark.django_db
@override_settings(CUSTOM_CODE_EVALS_PAUSED=False)
def test_own_template_in_own_workspace_is_configured_and_stored(
    auth_client, organization, workspace
):
    template = code_template("configure_lookup_own", organization, workspace)
    response = configure(auth_client, template.name)
    assert response.status_code == status.HTTP_200_OK, response.content.decode()[:500]
    assert stored(template) == 1


@pytest.mark.django_db
@override_settings(CUSTOM_CODE_EVALS_PAUSED=False)
def test_builtin_is_the_template_stored_when_an_own_template_without_a_workspace_shares_its_name(
    auth_client, organization, builtin_template
):
    # Made before workspaces and before saving refused a built-in's name, so the name is
    # written without save(). The request names the built-in, which is the template the
    # request check accepts, so it is the one stored.
    own = code_template("configure_lookup_legacy_same_name", organization, None)
    EvalTemplate.no_workspace_objects.filter(pk=own.pk).update(
        name=builtin_template.name
    )
    response = configure(auth_client, builtin_template.name)
    assert response.status_code == status.HTTP_200_OK, response.content.decode()[:500]
    assert stored(builtin_template) == 1
    assert stored(own) == 0


@pytest.mark.django_db
@override_settings(CUSTOM_CODE_EVALS_PAUSED=False)
def test_unknown_name_answers_the_same_sentence_as_another_organizations_template(
    auth_client, other_organization_template
):
    before = ExternalEvalConfig.no_workspace_objects.count()
    unknown = configure(auth_client, UNKNOWN)
    elsewhere = configure(auth_client, other_organization_template.name)
    assert unknown.status_code == status.HTTP_400_BAD_REQUEST
    assert elsewhere.status_code == status.HTTP_400_BAD_REQUEST
    assert "not found" in unknown.content.decode()
    assert ExternalEvalConfig.no_workspace_objects.count() == before
    # The answer does not tell a name that exists in another organization from one that does not.
    assert unknown.content.decode().replace(
        UNKNOWN, "<name>"
    ) == elsewhere.content.decode().replace(other_organization_template.name, "<name>")
