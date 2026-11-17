"""Routed evaluation template isolation between workspaces.

No xfails: each isolation assertion must pass as written.
Use an isolated PostgreSQL test database. This packet was checked statically,
not executed. The feedback case requires the real EE APICallLog model.
Shared fixtures below are explicitly imported by the other two packet modules.
"""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.authentication import APIKeyAuthentication, generate_encrypted_message
from accounts.models.auth_token import AuthToken, AuthTokenType
from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.choices import FeedbackSourceChoices, OwnerChoices
from model_hub.models.evals_metric import (
    EvalSettings,
    EvalTemplate,
    EvalTemplateVersion,
    Feedback,
)
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = pytest.mark.django_db


@pytest.fixture
def sweep_scope(
    urlconf_loaded_without_workspace_context, user, organization, workspace
):
    """Reuse IR factories, but remove org-owner access to every workspace.

    Do not use auth_client: its force-authentication and APIView.initial patch
    would bypass precisely the ambient/authentication boundary under test.
    """
    clear_workspace_context()
    OrganizationMembership.no_workspace_objects.filter(
        user=user, organization=organization
    ).update(role=OrganizationRoles.MEMBER, level=Level.MEMBER, is_active=True)
    user.organization_role = OrganizationRoles.MEMBER
    user.save(update_fields=["organization_role"])
    WorkspaceMembership.no_workspace_objects.filter(
        user=user, workspace=workspace
    ).update(
        role=OrganizationRoles.WORKSPACE_MEMBER,
        level=Level.WORKSPACE_MEMBER,
        is_active=True,
    )
    other_workspace = Workspace.no_workspace_objects.create(
        name=f"sweep-w2-{uuid4().hex}",
        organization=organization,
        is_active=True,
        created_by=user,
    )
    foreign_org = Organization.objects.create(name=f"sweep-org-b-{uuid4().hex}")
    foreign_workspace = Workspace.no_workspace_objects.create(
        name=f"sweep-b-w1-{uuid4().hex}",
        organization=foreign_org,
        is_active=True,
        is_default=True,
        created_by=user,
    )
    # created_by is a required creator, not access: drop any membership a
    # creation signal may have granted so the actor stays outside both.
    WorkspaceMembership.no_workspace_objects.filter(
        user=user, workspace__in=[other_workspace, foreign_workspace]
    ).delete()
    OrganizationMembership.no_workspace_objects.filter(
        user=user, organization=foreign_org
    ).delete()
    assert user.can_access_workspace(workspace)
    assert user.can_write_to_workspace(workspace)
    assert not user.can_access_workspace(other_workspace)
    assert not user.can_access_organization(foreign_org)
    assert not user.can_access_workspace(foreign_workspace)
    token = AuthToken.objects.create(
        user=user,
        auth_type=AuthTokenType.ACCESS,
        is_active=True,
        last_used_at=timezone.now(),
    )
    # Same production browser-bearer construction as a21_authority_support.py.
    bearer = generate_encrypted_message({"user_id": str(user.pk), "id": str(token.pk)})
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {bearer}")
    yield SimpleNamespace(
        user=user,
        organization=organization,
        workspace=workspace,
        other_workspace=other_workspace,
        foreign_org=foreign_org,
        foreign_workspace=foreign_workspace,
        client=client,
    )
    clear_workspace_context()


def target_space(scope, boundary):
    return {
        "own": scope.workspace,
        "workspace": scope.other_workspace,
        "organization": scope.foreign_workspace,
    }[boundary]


def routed(scope, method, path, data=None):
    """Real URL resolution, bearer decoding, membership and ambient binding."""
    clear_workspace_context()
    try:
        response = getattr(scope.client, method)(
            path,
            {} if data is None else data,
            format="json",
            HTTP_X_WORKSPACE_ID=str(scope.workspace.pk),
            HTTP_X_ORGANIZATION_ID=str(scope.organization.pk),
        )
        request = response.renderer_context["request"]
        assert type(request.successful_authenticator) is APIKeyAuthentication
        assert request.credential_provenance == "interactive_session"
        assert request.user.pk == scope.user.pk
        assert request.organization.pk == scope.organization.pk
        assert request.workspace.pk == scope.workspace.pk
        assert request._request is response.wsgi_request
        return response
    finally:
        clear_workspace_context()


def error_shape(response, requested_id):
    # Some legacy errors echo the caller's supplied id. Compare every other
    # byte of the JSON body; do not mask messages, codes, or field names.
    return json.dumps(response.json(), sort_keys=True).replace(
        str(requested_id), "<requested-id>"
    )


def assert_missing_parity(denied, missing, foreign_id, missing_id, statuses=(404,)):
    assert denied.status_code in statuses, denied.data
    assert missing.status_code == denied.status_code, missing.data
    assert error_shape(denied, foreign_id) == error_shape(missing, missing_id)


@pytest.fixture
def eval_factory(sweep_scope):
    """Small model factory using the real model's validation/save path."""

    def create(boundary="own", **overrides):
        space = target_space(sweep_scope, boundary)
        fields = {
            "name": f"sweep-{uuid4().hex}",
            "organization": space.organization,
            "workspace": space,
            "owner": OwnerChoices.USER.value,
            "eval_type": "code",
            "criteria": f"tenant-marker-{uuid4().hex}",
            "config": {
                "eval_type_id": f"sweep_function_{uuid4().hex}",
                "function_eval": True,
                "required_keys": ["input"],
                "optional_keys": [],
                "output": "Pass/Fail",
                "config": {},
            },
        }
        fields.update(overrides)
        return EvalTemplate.no_workspace_objects.create(**fields)

    return create


def seed_read_payload(template, user, suffix):
    if suffix == "versions":
        return [
            EvalTemplateVersion.no_workspace_objects.create(
                eval_template=template,
                version_number=n,
                is_default=n == 1,
                organization=template.organization,
                workspace=template.workspace,
                created_by=user,
                config_snapshot=template.config,
                criteria=template.criteria,
            )
            for n in (1, 2)
        ]
    if suffix == "feedback-list":
        from model_hub.views import separate_evals

        # OSS's APICallLog=None shortcut returns 200 even for a missing id;
        # that is not evidence about the row this packet is certifying.
        assert separate_evals.APICallLog is not None, (
            "R-06 requires real EE usage models"
        )
        return [
            Feedback.no_workspace_objects.create(
                source=FeedbackSourceChoices.EVAL_PLAYGROUND.value,
                source_id=str(uuid4()),
                eval_template=template,
                value="1",
                explanation=template.criteria,
                user=user,
                organization=template.organization,
                workspace=template.workspace,
            )
        ]
    return []


@pytest.mark.parametrize(
    "row,suffix",
    [("R-04", "detail"), ("R-05", "versions"), ("R-06", "feedback-list")],
    ids=["R-04", "R-05", "R-06"],
)
@pytest.mark.parametrize("boundary", ["workspace", "organization"])
def test_template_reads_hide_foreign_ids(
    sweep_scope, eval_factory, row, suffix, boundary
):
    """R-06 is an existence oracle, not a claim of feedback-content leakage."""
    template = eval_factory(boundary)
    seed_read_payload(template, sweep_scope.user, suffix)
    missing_id = uuid4()
    missing = routed(
        sweep_scope, "get", f"/model-hub/eval-templates/{missing_id}/{suffix}/"
    )
    denied = routed(
        sweep_scope, "get", f"/model-hub/eval-templates/{template.pk}/{suffix}/"
    )
    assert_missing_parity(denied, missing, template.pk, missing_id)
    assert template.criteria not in json.dumps(denied.json()), row


@pytest.mark.parametrize(
    "row,suffix",
    [("R-04", "detail"), ("R-05", "versions"), ("R-06", "feedback-list")],
    ids=["R-04", "R-05", "R-06"],
)
def test_template_reads_allow_same_workspace(sweep_scope, eval_factory, row, suffix):
    template = eval_factory()
    seeded = seed_read_payload(template, sweep_scope.user, suffix)
    response = routed(
        sweep_scope, "get", f"/model-hub/eval-templates/{template.pk}/{suffix}/"
    )
    assert response.status_code == 200, (row, response.data)
    result = response.data["result"]
    if suffix == "detail":
        assert result["id"] == str(template.pk)
        assert result["config"] == template.config
        assert result["instructions"] == template.criteria
    elif suffix == "versions":
        assert result["template_id"] == str(template.pk)
        assert result["total"] == 2
        assert {v["id"] for v in result["versions"]} == {str(v.pk) for v in seeded}
    else:
        assert result["template_id"] == str(template.pk)
        assert result["total"] == 1
        assert result["items"][0]["id"] == str(seeded[0].pk)
        assert result["items"][0]["explanation"] == template.criteria


@pytest.mark.parametrize(
    "row,path,key",
    [
        ("R-08", "/model-hub/eval-sdk-code/", "template_id"),
        ("R-11", "/model-hub/get-eval-config", "eval_id"),
    ],
    ids=["R-08", "R-11"],
)
@pytest.mark.parametrize("boundary", ["workspace", "organization"])
def test_eval_configuration_hides_foreign_ids(
    sweep_scope, eval_factory, row, path, key, boundary
):
    template = eval_factory(boundary)
    missing_id = uuid4()
    missing = routed(sweep_scope, "get", path, {key: str(missing_id)})
    denied = routed(sweep_scope, "get", path, {key: str(template.pk)})
    # These two rows explicitly preserve MISSING_EVAL_TEMPLATE (400), or 404
    # if remediation standardizes not-found responses.
    assert_missing_parity(denied, missing, template.pk, missing_id, statuses=(400, 404))
    assert template.name not in json.dumps(denied.json()), row


@pytest.mark.parametrize(
    "row,path,key",
    [
        ("R-08", "/model-hub/eval-sdk-code/", "template_id"),
        ("R-11", "/model-hub/get-eval-config", "eval_id"),
    ],
    ids=["R-08", "R-11"],
)
def test_eval_configuration_allows_same_workspace(
    sweep_scope, eval_factory, row, path, key
):
    template = eval_factory()
    response = routed(sweep_scope, "get", path, {key: str(template.pk)})
    assert response.status_code == 200, (row, response.data)
    result = response.data["result"]
    if row == "R-08":
        assert template.name in result["python"]
        assert str(template.pk) in result["curl"]
        assert result["javascript"]
    else:
        assert result["eval"]["id"] == str(template.pk)
        assert result["eval"]["criteria"] == template.criteria
        assert result["owner"] == "user"


@pytest.mark.parametrize("boundary", ["workspace", "organization"])
def test_r10_foreign_template_cannot_create_log_settings(
    sweep_scope, eval_factory, boundary
):
    template = eval_factory(boundary)
    missing_id = uuid4()
    payload = {
        "column_config": [{"id": "score", "name": "Score", "is_visible": True}],
        "source": "eval_playground",
    }
    before = list(
        EvalSettings.no_workspace_objects.filter(user=sweep_scope.user).values()
    )
    missing = routed(
        sweep_scope,
        "patch",
        "/model-hub/get-eval-logs",
        {**payload, "eval_id": str(missing_id)},
    )
    denied = routed(
        sweep_scope,
        "patch",
        "/model-hub/get-eval-logs",
        {**payload, "eval_id": str(template.pk)},
    )
    after = list(
        EvalSettings.no_workspace_objects.filter(user=sweep_scope.user).values()
    )
    # Check persistence separately from the response code, so a refusal after
    # writing a settings row cannot pass the regression.
    assert after == before
    assert_missing_parity(denied, missing, template.pk, missing_id)


def test_r10_same_workspace_can_create_log_settings(sweep_scope, eval_factory):
    template = eval_factory()
    columns = [{"id": "score", "name": "Score", "is_visible": True}]
    response = routed(
        sweep_scope,
        "patch",
        "/model-hub/get-eval-logs",
        {
            "eval_id": str(template.pk),
            "column_config": columns,
            "source": "eval_playground",
        },
    )
    assert response.status_code == 200, response.data
    setting = EvalSettings.no_workspace_objects.get(
        user=sweep_scope.user, eval_id=template.pk, source="eval_playground"
    )
    assert setting.column_config == columns
