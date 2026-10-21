"""Bedrock evaluator custom-model identifier contract (TH-3486).

A friendly label such as "Claude" is not an AWS invocation target. Creating
one must fail before persistence and before any provider call. The reported
error is:

    NotFoundError: BedrockException - Bedrock Invoke HTTPX: Unknown provider=None, model=Claude.
"""

import pytest
from rest_framework.test import APIClient

from accounts.models import Organization, User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace
from model_hub.models.custom_models import CustomAIModel
from model_hub.views.custom_model import CustomAIModelCreateView


CREATE_URL = "/model-hub/custom_models/create/"
EDIT_URL = "/model-hub/custom_models/edit/"
FOUNDATION_ID = "anthropic.claude-3-haiku-20240307-v1:0"


@pytest.fixture
def organization(db):
    return Organization.objects.create(name="Bedrock Eval Org")


@pytest.fixture
def user(db, organization):
    created = User.objects.create_user(
        email="bedrock-eval@example.com",
        password="testpassword123",
        name="Bedrock Eval User",
        organization=organization,
    )
    OrganizationMembership.no_workspace_objects.create(
        user=created, organization=organization, is_active=True
    )
    return created


@pytest.fixture
def workspace(db, organization, user):
    return Workspace.objects.create(
        name="Default Workspace",
        organization=organization,
        is_default=True,
        created_by=user,
    )


@pytest.fixture
def client(user, workspace):
    api = APIClient()
    api.force_authenticate(user=user)
    api.credentials(HTTP_X_WORKSPACE_ID=str(workspace.id))
    return api


def _payload(model_name):
    return {
        "model_provider": "bedrock",
        "model_name": model_name,
        "input_token_cost": 0,
        "output_token_cost": 0,
        "config_json": {
            "aws_access_key_id": "AKIAEXAMPLE",
            "aws_secret_access_key": "secret-example",
            "aws_region_name": "us-east-1",
        },
    }


@pytest.mark.django_db
def test_friendly_label_is_rejected_before_provider_or_persistence(
    client, organization, monkeypatch
):
    def fail_if_called(**kwargs):
        raise AssertionError(f"provider was called with {kwargs.get('model')}")

    monkeypatch.setattr(
        "model_hub.views.custom_model.validate_model_working", fail_if_called
    )

    response = client.post(CREATE_URL, _payload("Claude"), format="json")

    assert response.status_code == 400
    body = str(response.data)
    assert "Bedrock model ID" in body
    assert "Claude" in body
    assert "Unknown provider" not in body
    assert not CustomAIModel.no_workspace_objects.filter(
        organization=organization, user_model_id="Claude"
    ).exists()


@pytest.mark.django_db
def test_foundation_id_is_forwarded_unchanged(client, organization, monkeypatch):
    seen = {}

    def accept(**kwargs):
        seen.update(kwargs)
        return object()

    monkeypatch.setattr(
        "model_hub.views.custom_model.validate_model_working", accept
    )

    response = client.post(CREATE_URL, _payload(FOUNDATION_ID), format="json")

    assert response.status_code == 200, response.data
    assert seen["model_name"] == FOUNDATION_ID
    assert seen["provider"] == "bedrock"
    saved = CustomAIModel.no_workspace_objects.get(
        organization=organization, user_model_id=FOUNDATION_ID
    )
    assert saved.provider == "bedrock"
    assert "aws_secret_access_key" in saved.actual_json


@pytest.mark.django_db
def test_edit_rejects_friendly_label_before_provider(client, organization, user, workspace, monkeypatch):
    saved = CustomAIModel.objects.create(
        user_model_id=FOUNDATION_ID,
        provider="bedrock",
        input_token_cost=0,
        output_token_cost=0,
        organization=organization,
        workspace=workspace,
        user=user,
        key_config={
            "aws_access_key_id": "AKIAEXAMPLE",
            "aws_secret_access_key": "secret-example",
            "aws_region_name": "us-east-1",
        },
    )

    def fail_if_called(**kwargs):
        raise AssertionError(f"provider was called with {kwargs.get('model_name')}")

    monkeypatch.setattr(
        "model_hub.views.custom_model.validate_model_working", fail_if_called
    )

    response = client.patch(
        EDIT_URL,
        {
            "id": str(saved.id),
            "model_name": "Claude",
            "config_json": {
                "aws_access_key_id": "AKIAEXAMPLE",
                "aws_secret_access_key": "secret-example",
                "aws_region_name": "us-east-1",
            },
        },
        format="json",
    )

    assert response.status_code == 400
    assert "Bedrock model ID" in str(response.data)
    saved.refresh_from_db()
    assert saved.user_model_id == FOUNDATION_ID


def test_identifier_helper_rejects_family_label_only():
    from model_hub.views.custom_model import invalid_bedrock_model_id

    assert invalid_bedrock_model_id("Claude")
    assert invalid_bedrock_model_id("  ")
    assert invalid_bedrock_model_id("My Claude")
    assert not invalid_bedrock_model_id(FOUNDATION_ID)
    assert not invalid_bedrock_model_id(
        "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
    )
    assert not invalid_bedrock_model_id(f"bedrock/{FOUNDATION_ID}")
