"""GET /sdk/api/v1/eval/<eval_id>/ returns templates from the caller's workspace only."""

from types import SimpleNamespace

import pytest
from rest_framework.test import APIClient

from accounts.models import Organization, OrgApiKey, User, Workspace
from accounts.models.organization_membership import OrganizationMembership
from model_hub.models.evals_metric import EvalTemplate
from sdk.views.evaluations import GetEvalStructureEvalIdView
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)


@pytest.fixture
def scoped_templates(user, workspace):
    clear_workspace_context()
    other_org = Organization.objects.create(name="other-eval-org")
    sibling = Workspace.objects.create(
        name="sibling", organization=user.organization, created_by=user
    )
    other_ws = Workspace.objects.create(
        name="other_org", organization=other_org, created_by=user
    )
    templates = {}
    for index, (name, org, ws, owner, deleted) in enumerate(
        [
            ("own", user.organization, workspace, "user", False),
            ("sibling", user.organization, sibling, "user", False),
            ("other_org", other_org, other_ws, "user", False),
            ("builtin", None, None, "system", False),
            ("orgless_user", None, None, "user", False),
            ("other_org_system", other_org, other_ws, "system", False),
            ("deleted", user.organization, workspace, "user", True),
            ("legacy", user.organization, None, "user", False),
            ("other_org_legacy", other_org, None, "user", False),
            ("scoped_system", None, sibling, "system", False),
        ]
    ):
        templates[name] = EvalTemplate.no_workspace_objects.create(
            name=name,
            organization=org,
            workspace=ws,
            owner=owner,
            eval_id=9876100 + index,
            config={},
            criteria="synthetic structure criteria",
            deleted=deleted,
        )
    set_workspace_context(
        workspace=workspace, organization=user.organization, user=user
    )
    return templates


@pytest.fixture
def credential_client(user, workspace):
    key = OrgApiKey.objects.create(
        organization=user.organization,
        workspace=workspace,
        user=user,
        api_key="scope-test-public",
        secret_key="scope-test-secret",
        type="user",
        enabled=True,
    )
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key)
    return client


def read(client, eval_id):
    return client.get(f"/sdk/api/v1/eval/{eval_id}/")


@pytest.mark.django_db
@pytest.mark.parametrize(
    "name",
    [
        "sibling",
        "other_org",
        "orgless_user",
        "other_org_system",
        "deleted",
        "other_org_legacy",
        "scoped_system",
    ],
)
def test_structure_outside_caller_scope_matches_missing(
    credential_client, scoped_templates, name
):
    missing = read(credential_client, 9876999)
    denied = read(credential_client, scoped_templates[name].eval_id)
    assert missing.status_code == 400
    assert denied.status_code == missing.status_code
    assert denied.json() == missing.json()


@pytest.mark.django_db
@pytest.mark.parametrize("name", ["own", "builtin", "legacy"])
def test_structure_in_caller_scope_or_global_is_returned(
    credential_client, scoped_templates, name
):
    response = read(credential_client, scoped_templates[name].eval_id)
    assert response.status_code == 200
    assert response.json()["result"]["eval_id"] == scoped_templates[name].eval_id


@pytest.mark.django_db
@pytest.mark.parametrize(
    "authority",
    [
        "missing_workspace",
        "missing_org",
        "revoked_org",
        "deleted_workspace",
        "inactive_user",
        "mismatched_workspace",
    ],
)
def test_structure_returns_missing_for_stale_request_context(
    user, workspace, scoped_templates, authority
):
    request = SimpleNamespace(
        user=user, organization=user.organization, workspace=workspace
    )
    if authority == "missing_workspace":
        request.workspace = None
    elif authority == "missing_org":
        request.organization = None
    elif authority == "revoked_org":
        OrganizationMembership.no_workspace_objects.filter(user=user).update(
            is_active=False
        )
    elif authority == "deleted_workspace":
        Workspace.no_workspace_objects.filter(pk=workspace.pk).update(deleted=True)
    elif authority == "inactive_user":
        User.objects.filter(pk=user.pk).update(is_active=False)
    else:
        request.workspace = scoped_templates["other_org"].workspace
    response = GetEvalStructureEvalIdView().get(
        request, scoped_templates["own"].eval_id
    )
    missing = GetEvalStructureEvalIdView().get(request, 9876999)
    assert response.status_code == 400
    assert response.data == missing.data


@pytest.mark.django_db
@pytest.mark.parametrize("ambient", ["empty", "other_org"])
def test_structure_lookup_ignores_thread_workspace_context(
    user, workspace, scoped_templates, ambient
):
    clear_workspace_context()
    if ambient == "other_org":
        set_workspace_context(workspace=scoped_templates["other_org"].workspace)
    request = SimpleNamespace(
        user=user, organization=user.organization, workspace=workspace
    )
    assert (
        GetEvalStructureEvalIdView()
        .get(request, scoped_templates["own"].eval_id)
        .status_code
        == 200
    )
    assert (
        GetEvalStructureEvalIdView()
        .get(request, scoped_templates["other_org"].eval_id)
        .status_code
        == 400
    )


@pytest.mark.django_db
@pytest.mark.parametrize("eval_id", ["not-an-integer", "1.5", "null", "9" * 100, "-1"])
def test_malformed_eval_id_matches_missing(
    credential_client, scoped_templates, eval_id
):
    missing = read(credential_client, 9876999)
    denied = read(credential_client, eval_id)
    assert denied.status_code == missing.status_code == 400
    assert denied.json() == missing.json()
    assert "synthetic structure criteria" not in denied.content.decode()


@pytest.mark.django_db
def test_duplicate_eval_id_returns_missing(credential_client, scoped_templates):
    original = scoped_templates["own"]
    EvalTemplate.no_workspace_objects.create(
        name="duplicate",
        organization=original.organization,
        workspace=original.workspace,
        owner="user",
        eval_id=original.eval_id,
        config={},
        criteria="duplicate structure criteria",
    )
    denied = read(credential_client, original.eval_id)
    missing = read(credential_client, 9876999)
    assert denied.status_code == missing.status_code == 400
    assert denied.json() == missing.json()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "state", ["inactive_workspace", "revoked_workspace", "malformed_workspace"]
)
def test_inactive_or_revoked_workspace_returns_missing(
    user, workspace, scoped_templates, state
):
    from accounts.models.workspace import WorkspaceMembership
    from tfc.constants.levels import Level

    request = SimpleNamespace(
        user=user, organization=user.organization, workspace=workspace
    )
    if state == "inactive_workspace":
        Workspace.no_workspace_objects.filter(pk=workspace.pk).update(is_active=False)
    elif state == "malformed_workspace":
        request.workspace = SimpleNamespace(pk="not-a-uuid")
    else:
        OrganizationMembership.no_workspace_objects.filter(user=user).update(
            level=Level.MEMBER
        )
        WorkspaceMembership.no_workspace_objects.filter(
            user=user, workspace=workspace
        ).update(is_active=False)
    denied = GetEvalStructureEvalIdView().get(request, scoped_templates["own"].eval_id)
    missing = GetEvalStructureEvalIdView().get(request, 9876999)
    assert denied.status_code == missing.status_code == 400
    assert denied.data == missing.data


@pytest.mark.django_db
def test_nondefault_workspace_does_not_see_org_level_rows(user, scoped_templates):
    sibling = scoped_templates["sibling"].workspace
    request = SimpleNamespace(
        user=user, organization=user.organization, workspace=sibling
    )
    assert not sibling.is_default
    assert (
        GetEvalStructureEvalIdView()
        .get(request, scoped_templates["sibling"].eval_id)
        .status_code
        == 200
    )
    denied = GetEvalStructureEvalIdView().get(
        request, scoped_templates["legacy"].eval_id
    )
    missing = GetEvalStructureEvalIdView().get(request, 9876999)
    assert denied.status_code == missing.status_code == 400
    assert denied.data == missing.data
