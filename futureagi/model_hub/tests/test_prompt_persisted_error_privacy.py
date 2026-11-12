"""Exercise real failure writers before reading their customer-facing output."""

import inspect
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from django.db import OperationalError
from django.test import override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from model_hub.models.choices import OwnerChoices, StatusType
from model_hub.tests.network_isolation import (
    no_external_lookups as no_external_lookups,
)
from model_hub.tests.test_prompt_handler_error_privacy import SENTINEL
from model_hub.views import prompt_template as prompt_views
from model_hub.views import separate_evals
from tfc.constants.api_calls import APICallStatusChoices, APICallTypeChoices
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("no_external_lookups")]


def _assert_private(value):
    text = json.dumps(value, default=str)
    assert SENTINEL not in text
    for fragment in SENTINEL.split():
        assert fragment not in text


@pytest.fixture
def logged_evaluation(prompt_version, organization, workspace):
    # Use the real allocator and real JSON-string APICallLog rows. A non-billable
    # evaluator and an unlimited local test subscription need no billing mocks.
    from ee.usage.models.usage import (
        APICallType,
        OrganizationSubscription,
        PlanChoices,
        SubscriptionTier,
        SubscriptionTierChoices,
    )

    APICallType.objects.get_or_create(name=APICallTypeChoices.DATASET_EVALUATION.value)
    tier = SubscriptionTier.objects.create(
        name=SubscriptionTierChoices.CUSTOM.value,
        description="Prompt error privacy test",
        wallet_refill_amount=0,
    )
    OrganizationSubscription.objects.update_or_create(
        organization=organization,
        defaults={"subscription_tier": tier, "plan": PlanChoices.ENTERPRISE.value},
    )
    eval_template = prompt_views.EvalTemplate.no_workspace_objects.create(
        name="Persisted error privacy evaluation",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.SYSTEM.value,
        config={
            "eval_type_id": "RecallAtK",
            "function_eval": True,
            "required_keys": ["hypothesis", "reference"],
            "output": "score",
        },
    )
    return prompt_views.PromptEvalConfig.no_workspace_objects.create(
        prompt_template=prompt_version.original_template,
        eval_template=eval_template,
        mapping={"hypothesis": "output_prompt", "reference": "input_prompt"},
        config={},
    )


@pytest.mark.requires_ee
@pytest.mark.usefixtures("urlconf_loaded_without_workspace_context")
def test_run_evaluation_persisted_error_is_private(
    logged_evaluation, prompt_version, organization, workspace, auth_client
):
    error = OperationalError(SENTINEL)
    rows = []

    def fail_evaluator(**_kwargs):
        # Prove the failure occurs after the real allocator has saved the
        # processing row, not at an earlier setup step or in run_eval_id's mock.
        row = separate_evals.APICallLog.objects.get(
            organization=organization,
            workspace=workspace,
            source="prompt_template",
            source_id=str(logged_evaluation.eval_template_id),
        )
        assert row.status == APICallStatusChoices.PROCESSING.value
        assert isinstance(row.config, str)
        assert "output" not in json.loads(row.config)
        rows.append(row)
        raise error

    evaluate = Mock(side_effect=fail_evaluator)
    with (
        patch.object(
            prompt_views.EvaluationRunner,
            "_create_eval_instance",
            return_value=SimpleNamespace(run=evaluate),
        ),
        patch.object(prompt_views.logger, "exception") as log_exception,
        pytest.raises(OperationalError) as raised,
    ):
        prompt_views.PromptTemplateViewSet().run_evaluation(
            evaluation=logged_evaluation,
            response="Public response",
            messages=[{"role": "user", "content": "Public reference"}],
            variable_combination={},
            organization_id=organization.id,
            template=prompt_version.original_template,
        )

    assert raised.value is error
    evaluate.assert_called_once_with(
        hypothesis="Public response", reference="Public reference"
    )
    log_exception.assert_called_once_with(f"{error} error")
    assert len(rows) == 1
    row = rows[0]
    row.refresh_from_db()
    assert row.status == APICallStatusChoices.ERROR.value
    assert isinstance(row.config, str)

    # No mocked lookup, parser, or read handler: dispatch the actual GET route.
    response = auth_client.get("/model-hub/get-eval-logs", {"log_id": str(row.log_id)})
    assert response.status_code == 200, response.data
    _assert_private(response.data)
    _assert_private(response.content.decode())
    stored = json.loads(row.config)
    _assert_private(stored)
    expected_output = {"output": None, "reason": "Failed to run evaluation"}
    assert stored["output"] == expected_output
    assert response.data["result"]["output"] == expected_output
    assert str(response.data["result"]["log_id"]) == str(row.log_id)
    assert stored["mappings"] == {
        "hypothesis": "Public response",
        "reference": "Public reference",
    }
    assert stored["prompt_template_id"] == str(prompt_version.original_template_id)
    assert stored["prompt_version_id"] == str(prompt_version.id)


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
def test_run_template_task_error_attribute_is_private(
    prompt_version, organization, workspace, user
):
    error = OperationalError(SENTINEL)
    template = prompt_version.original_template
    # error_message is currently a compatibility attribute, not a Django field.
    # Keep the loaded instance to exercise both the save-boundary assignment and
    # get-run-status's explicit fallback without inventing a persisted DB field.
    with (
        patch.object(prompt_views.PromptTemplate.objects, "get", return_value=template),
        patch.object(
            prompt_views.PromptTemplateViewSet, "run", side_effect=error
        ) as run,
        patch.object(prompt_views.logger, "exception") as log_exception,
        pytest.raises(OperationalError) as raised,
    ):
        inspect.unwrap(prompt_views.run_template_task)(
            template.id, [], organization.id, "prompt", prompt_version.template_version
        )

    assert raised.value is error
    run.assert_called_once()
    log_exception.assert_called_once_with(f"error {error}")
    assert template.status == StatusType.FAILED.value

    request = APIRequestFactory().get(
        f"/model-hub/prompt-templates/{template.id}/get-run-status/",
        {"template_version": prompt_version.template_version},
    )
    request.organization = organization
    request.workspace = workspace
    force_authenticate(request, user=user)
    set_workspace_context(
        organization=organization,
        workspace=workspace,
        user=user,
        require_workspace=True,
    )
    try:
        with patch.object(prompt_views, "get_object_or_404", return_value=template):
            response = prompt_views.PromptTemplateViewSet.as_view(
                {"get": "get_run_status"}
            )(request, pk=str(template.id))
    finally:
        clear_workspace_context()

    assert response.status_code == 200, response.data
    _assert_private(response.data)
    _assert_private(template.error_message)
    assert template.error_message == "Failed to run prompt"
    assert response.data["result"]["error_message"] == "Failed to run prompt"
    assert response.data["result"]["status"] == StatusType.FAILED.value
    fresh_template = prompt_views.PromptTemplate.no_workspace_objects.get(
        id=template.id
    )
    assert not hasattr(fresh_template, "error_message")
