"""SDK eval structure reads only return templates the caller's workspace can see."""

import uuid

import pytest
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate

from accounts.models import Organization, OrgApiKey
from accounts.models.workspace import Workspace
from model_hub.models.evals_metric import EvalTemplate
from sdk.views.evaluations import GetEvalStructureEvalIdView
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = [pytest.mark.django_db, pytest.mark.integration, pytest.mark.api]


@pytest.fixture(params=["api_key", "bearer"])
def structure_client(request, user, workspace):
    client = APIClient()
    if request.param == "api_key":
        key = OrgApiKey.no_workspace_objects.create(
            user=user,
            organization=user.organization,
            workspace=workspace,
            api_key=uuid.uuid4().hex,
            secret_key=uuid.uuid4().hex,
            name="structure-scope",
            enabled=True,
            type="user",
        )
        client.credentials(HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key)
    else:
        response = client.post(
            "/accounts/token/",
            {"email": user.email, "password": "testpassword123"},
            format="json",
        )
        assert response.status_code == 200, response.data
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
    return client


@pytest.fixture
def templates(organization, workspace, user):
    clear_workspace_context()
    other_org = Organization.objects.create(name="Structure other org")
    sibling = Workspace.no_workspace_objects.create(
        name="Structure sibling",
        organization=organization,
        created_by=user,
        is_default=False,
        is_active=True,
    )
    definitions = {
        "own": (organization, workspace, "user", False),
        "legacy": (organization, None, "user", False),
        "other_org": (other_org, None, "user", False),
        "sibling": (organization, sibling, "user", False),
        "global": (None, None, "system", False),
        "orgless_user": (None, None, "user", False),
        "other_org_system": (other_org, None, "system", False),
        "workspace_system": (None, sibling, "system", False),
        "deleted": (organization, workspace, "user", True),
    }
    result = {}
    for index, (name, (org, ws, owner, deleted)) in enumerate(definitions.items()):
        template = EvalTemplate.no_workspace_objects.create(
            name=f"structure-{name}-{uuid.uuid4().hex[:8]}",
            eval_id=193671000 + index,
            organization=org,
            workspace=ws,
            owner=owner,
            config={"required_keys": ["input"], "output": "score"},
            criteria=f"criteria-marker-{name}",
            choices=[name],
            deleted=deleted,
        )
        # Signals can assign request context to nullable scope fields on create.
        EvalTemplate.all_objects.filter(pk=template.pk).update(
            organization=org,
            workspace=ws,
            deleted=deleted,
        )
        result[name] = template
    return result


def _get(client, template):
    return client.get(f"/sdk/api/v1/eval/{template.eval_id}/")


@pytest.mark.parametrize("name", ["own", "legacy", "global"])
def test_structure_read_allowed_for_own_scope_and_global(
    structure_client, templates, name
):
    response = _get(structure_client, templates[name])
    assert response.status_code == 200
    assert response.data["result"]["eval_id"] == templates[name].eval_id


@pytest.mark.parametrize(
    "name",
    [
        "other_org",
        "sibling",
        "orgless_user",
        "other_org_system",
        "workspace_system",
        "deleted",
    ],
)
def test_structure_read_returns_missing_for_other_scopes(
    structure_client, templates, name
):
    response = _get(structure_client, templates[name])
    missing = structure_client.get("/sdk/api/v1/eval/193671099/")
    assert response.status_code == missing.status_code == 400
    assert response.data == missing.data
    assert "criteria-marker" not in str(response.data)


def test_structure_read_requires_authentication(templates):
    assert _get(APIClient(), templates["global"]).status_code in (401, 403)


@pytest.mark.parametrize("scope", ["absent", "orgless", "mismatched"])
def test_structure_read_without_request_scope_returns_missing(
    user, templates, workspace, scope
):
    request = APIRequestFactory().get("/sdk/api/v1/eval/193671004/")
    force_authenticate(request, user=user)
    if scope != "absent":
        request.organization = (
            None if scope == "orgless" else Organization.objects.create(name="Other")
        )
        request.workspace = workspace
    response = GetEvalStructureEvalIdView.as_view()(
        request, eval_id=templates["global"].eval_id
    )
    assert response.status_code == 400
    assert "config" not in response.data
