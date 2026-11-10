"""Bedrock evaluator custom-model identifier contract (TH-3486).

A friendly label such as "Claude" is not an AWS invocation target. Creating
one must fail before persistence and before any provider call. The reported
error is:

    NotFoundError: BedrockException - Bedrock Invoke HTTPX: Unknown provider=None, model=Claude.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from rest_framework.test import APIClient

from accounts.models import Organization, User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace
from model_hub.models.custom_models import CustomAIModel

CREATE_URL = "/model-hub/custom_models/create/"
EDIT_URL = "/model-hub/custom_models/edit/"
FOUNDATION_ID = "anthropic.claude-3-haiku-20240307-v1:0"
PROFILE_ID = "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
AWS_CONFIG = {
    "aws_access_key_id": "AKIAEXAMPLE",
    "aws_secret_access_key": "secret-example",
    "aws_region_name": "us-east-1",
}


def _details_url(model_id):
    return f"/model-hub/custom-models/{model_id}/"


def _fake_completion(seen):
    """A provider boundary stand-in: records the call, never reaches AWS."""

    def completion(**kwargs):
        seen.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="ok"), finish_reason="stop"
                )
            ],
            usage=SimpleNamespace(total_tokens=2, prompt_tokens=1, completion_tokens=1),
        )

    return completion


def _fail_if_called(**kwargs):
    raise AssertionError(f"provider was called with {kwargs.get('model_name')}")


def _assert_model_name_field_error(response):
    from model_hub.views.custom_model import BEDROCK_MODEL_ID_MESSAGE

    assert response.status_code == 400
    assert response.data["attr"] == "model_name"
    assert response.data["details"]["model_name"] == [BEDROCK_MODEL_ID_MESSAGE]


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
    validate_model = Mock(side_effect=_fail_if_called)
    monkeypatch.setattr(
        "model_hub.views.custom_model.validate_model_working", validate_model
    )

    response = client.post(CREATE_URL, _payload("Claude"), format="json")

    _assert_model_name_field_error(response)
    validate_model.assert_not_called()
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

    monkeypatch.setattr("model_hub.views.custom_model.validate_model_working", accept)

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
def test_edit_rejects_friendly_label_before_provider(
    client, organization, user, workspace, monkeypatch
):
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

    validate_model = Mock(side_effect=_fail_if_called)
    monkeypatch.setattr(
        "model_hub.views.custom_model.validate_model_working", validate_model
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

    _assert_model_name_field_error(response)
    validate_model.assert_not_called()
    assert "Bedrock model ID" in str(response.data)
    saved.refresh_from_db()
    assert saved.user_model_id == FOUNDATION_ID


def test_identifier_helper_rejects_family_label_only():
    from model_hub.views.custom_model import invalid_bedrock_model_id

    assert invalid_bedrock_model_id("Claude")
    assert invalid_bedrock_model_id("  ")
    assert invalid_bedrock_model_id("My Claude")
    assert not invalid_bedrock_model_id(FOUNDATION_ID)
    assert not invalid_bedrock_model_id("us.anthropic.claude-3-5-sonnet-20241022-v2:0")
    assert not invalid_bedrock_model_id(f"bedrock/{FOUNDATION_ID}")


def _saved_bedrock_model(organization, workspace, user, model_id):
    return CustomAIModel.objects.create(
        user_model_id=model_id,
        provider="bedrock",
        input_token_cost=1,
        output_token_cost=2,
        organization=organization,
        workspace=workspace,
        user=user,
        key_config=dict(AWS_CONFIG),
    )


@pytest.mark.parametrize(
    "label",
    [
        "Claude 3.5 Sonnet",
        "Llama 3.1 70B",
        "bedrock/Claude",
        "bedrock/converse/claude",
        "anthropic.claude-3-haiku\x7f-20240307-v1:0",
        "anthropic.claude-3-haiku\t20240307-v1:0",
        "a." + "x" * 254,
    ],
)
def test_identifier_helper_rejects_punctuated_display_labels(label):
    from model_hub.views.custom_model import invalid_bedrock_model_id

    assert invalid_bedrock_model_id(label)


@pytest.mark.parametrize(
    "model_id",
    [
        FOUNDATION_ID,
        PROFILE_ID,
        "eu.anthropic.claude-3-haiku-20240307-v1:0",
        "apac.amazon.nova-pro-v1:0",
        "global.anthropic.claude-sonnet-4-20250514-v1:0",
        "meta.llama3-70b-instruct-v1:0",
        "amazon.titan-text-express-v1",
        "cohere.command-r-plus-v1:0",
        f"bedrock/converse/{PROFILE_ID}",
        "arn:aws:bedrock:us-east-1:123456789012:inference-profile/" + PROFILE_ID,
        "arn:aws:bedrock:us-east-1::foundation-model/" + FOUNDATION_ID,
        "a." + "x" * 253,
    ],
)
def test_identifier_helper_accepts_invocation_ids(model_id):
    from model_hub.views.custom_model import invalid_bedrock_model_id

    assert not invalid_bedrock_model_id(model_id)


@pytest.mark.django_db
def test_details_update_rejects_display_label_before_save(
    client, organization, user, workspace, monkeypatch
):
    saved = _saved_bedrock_model(organization, workspace, user, FOUNDATION_ID)
    validate_model = Mock(side_effect=_fail_if_called)
    monkeypatch.setattr(
        "model_hub.views.custom_model.validate_model_working", validate_model
    )

    response = client.post(
        _details_url(saved.id),
        {"model_name": "Claude", "input_token_cost": 5},
        format="json",
    )

    _assert_model_name_field_error(response)
    validate_model.assert_not_called()
    assert "Bedrock model ID" in str(response.data)
    fresh = CustomAIModel.objects.get(id=saved.id)
    assert fresh.user_model_id == FOUNDATION_ID
    assert fresh.input_token_cost == 1
    assert fresh.actual_json["aws_secret_access_key"] == "secret-example"


@pytest.mark.django_db
def test_details_update_saves_profile_id_unchanged(
    client, organization, user, workspace
):
    saved = _saved_bedrock_model(organization, workspace, user, FOUNDATION_ID)

    response = client.post(
        _details_url(saved.id), {"model_name": PROFILE_ID}, format="json"
    )

    assert response.status_code == 200, response.data
    assert CustomAIModel.objects.get(id=saved.id).user_model_id == PROFILE_ID


@pytest.mark.django_db
def test_credential_edit_of_legacy_label_row_is_rejected_before_provider(
    client, organization, user, workspace, monkeypatch
):
    legacy = _saved_bedrock_model(organization, workspace, user, "Claude")
    validate_model = Mock(side_effect=_fail_if_called)
    monkeypatch.setattr(
        "model_hub.views.custom_model.validate_model_working", validate_model
    )

    response = client.patch(
        EDIT_URL,
        {
            "id": str(legacy.id),
            "config_json": {**AWS_CONFIG, "aws_secret_access_key": "rotated"},
        },
        format="json",
    )

    _assert_model_name_field_error(response)
    validate_model.assert_not_called()
    assert "Bedrock model ID" in str(response.data)
    fresh = CustomAIModel.objects.get(id=legacy.id)
    assert fresh.actual_json["aws_secret_access_key"] == "secret-example"


@pytest.mark.django_db
def test_cost_only_edit_keeps_legacy_label_row_editable(
    client, organization, user, workspace, monkeypatch
):
    legacy = _saved_bedrock_model(organization, workspace, user, "Claude")
    monkeypatch.setattr(
        "model_hub.views.custom_model.validate_model_working", _fail_if_called
    )

    response = client.patch(
        EDIT_URL, {"id": str(legacy.id), "input_token_cost": 0.5}, format="json"
    )

    assert response.status_code == 200, response.data
    fresh = CustomAIModel.objects.get(id=legacy.id)
    assert fresh.input_token_cost == 0.5
    assert fresh.user_model_id == "Claude"
    assert fresh.actual_json["aws_secret_access_key"] == "secret-example"


@pytest.mark.django_db
@pytest.mark.parametrize("model_id", [FOUNDATION_ID, PROFILE_ID])
def test_valid_id_reaches_validation_transport_unchanged(
    client, organization, monkeypatch, model_id
):
    seen = []
    monkeypatch.setattr(
        "model_hub.utils.utils.litellm.completion", _fake_completion(seen)
    )

    response = client.post(CREATE_URL, _payload(model_id), format="json")

    assert response.status_code == 200, response.data
    assert len(seen) == 1
    assert seen[0]["model"] == model_id
    assert seen[0]["custom_llm_provider"] == "bedrock"
    assert seen[0]["aws_region_name"] == "us-east-1"
    saved = CustomAIModel.no_workspace_objects.get(
        organization=organization, provider="bedrock"
    )
    assert saved.user_model_id == model_id


@pytest.mark.django_db
@pytest.mark.parametrize("model_id", [FOUNDATION_ID, PROFILE_ID])
def test_saved_id_reaches_evaluator_transport_unchanged(
    client, organization, monkeypatch, model_id
):
    from agentic_eval.core.llm.llm import LLM
    from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager

    seen = []
    monkeypatch.setattr(
        "model_hub.utils.utils.litellm.completion", _fake_completion(seen)
    )
    assert client.post(CREATE_URL, _payload(model_id), format="json").status_code == 200

    # The evaluator resolves provider and credentials from the saved row,
    # then calls LiteLLM. Only the transport is replaced.
    manager = LiteLLMModelManager(model_id, organization_id=organization.id)
    provider = manager.get_provider(model_id)
    credentials = manager.get_api_key(organization.id)
    llm = LLM(provider=provider, model_name=model_id, api_key=credentials)
    seen.clear()
    monkeypatch.setattr(
        "agentic_eval.core.llm.llm.litellm.completion", _fake_completion(seen)
    )

    assert llm.call_llm([{"role": "user", "content": "Score this."}], provider) == "ok"

    assert provider == "bedrock"
    assert len(seen) == 1
    assert seen[0]["model"] == model_id
    assert seen[0]["custom_llm_provider"] == "bedrock"
    assert seen[0]["aws_access_key_id"] == "AKIAEXAMPLE"
    assert seen[0]["aws_region_name"] == "us-east-1"
