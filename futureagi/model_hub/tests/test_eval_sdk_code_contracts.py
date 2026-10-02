import json

import pytest
from rest_framework import status

from accounts.models import OrgApiKey, Organization
from model_hub.constants import SDK_API_KEY_PLACEHOLDER, SDK_SECRET_KEY_PLACEHOLDER
from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate


def _create_user_eval_template(organization, workspace=None, name="eval-sdk-snippet"):
    return EvalTemplate.no_workspace_objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={
            "output": "Pass/Fail",
            "eval_type_id": "CustomPromptEvaluator",
            "required_keys": ["response"],
        },
        eval_tags=["llm"],
        criteria="Check {{response}}.",
        model="turing_large",
        visible_ui=True,
        output_type_normalized="pass_fail",
        pass_threshold=0.5,
    )


def _get_eval_sdk_code(auth_client, template_id):
    return auth_client.get(
        "/model-hub/eval-sdk-code/",
        {
            "template_id": str(template_id),
            "model": "gpt-4o-mini",
            "mapping": json.dumps({"response": "Hello"}),
        },
    )


def _assert_eval_sdk_placeholders(result):
    response_text = json.dumps(result)
    assert SDK_API_KEY_PLACEHOLDER in response_text
    assert SDK_SECRET_KEY_PLACEHOLDER in response_text
    for key in ("python", "curl", "javascript"):
        assert result[key]


@pytest.mark.django_db
def test_eval_sdk_code_uses_placeholders_without_creating_user_key(
    auth_client, user, workspace
):
    template = _create_user_eval_template(
        user.organization, workspace, name="eval-sdk-placeholder"
    )
    OrgApiKey.no_workspace_objects.filter(
        organization=user.organization,
        type="user",
        user=user,
    ).delete()

    response = _get_eval_sdk_code(auth_client, template.id)

    assert response.status_code == status.HTTP_200_OK
    _assert_eval_sdk_placeholders(response.json()["result"])
    assert OrgApiKey.no_workspace_objects.filter(
        organization=user.organization,
        type="user",
        user=user,
    ).count() == 0


@pytest.mark.django_db
def test_eval_sdk_code_does_not_expose_existing_user_key(auth_client, user, workspace):
    template = _create_user_eval_template(
        user.organization, workspace, name="eval-sdk-existing-key"
    )
    raw_api_key = "0123456789abcdef0123456789abcdef"
    raw_secret_key = "abcdef0123456789abcdef0123456789"
    OrgApiKey.no_workspace_objects.create(
        organization=user.organization,
        type="user",
        enabled=True,
        user=user,
        api_key=raw_api_key,
        secret_key=raw_secret_key,
    )

    response = _get_eval_sdk_code(auth_client, template.id)

    assert response.status_code == status.HTTP_200_OK
    result = response.json()["result"]
    response_text = json.dumps(result)
    assert raw_api_key not in response_text
    assert raw_secret_key not in response_text
    _assert_eval_sdk_placeholders(result)


@pytest.mark.django_db
def test_eval_sdk_code_rejects_template_from_another_organization(
    auth_client, user
):
    other_org = Organization.objects.create(name="Other Eval SDK Snippet Org")
    template = _create_user_eval_template(
        other_org, name="eval-sdk-other-organization"
    )
    OrgApiKey.no_workspace_objects.filter(
        organization=user.organization,
        type="user",
        user=user,
    ).delete()

    response = _get_eval_sdk_code(auth_client, template.id)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert OrgApiKey.no_workspace_objects.filter(
        organization=user.organization,
        type="user",
        user=user,
    ).count() == 0
