"""/sdk/api/v1/configure-evaluations/ answers a custom code evaluation that is paused with the
refusal sentence as a 400 and stores no configuration, so nothing runs later. Others configure as before."""

import pytest
from django.test import override_settings
from rest_framework import status

from model_hub.models.evals_metric import EvalTemplate

SENTENCE = "Custom code evaluations are temporarily unavailable on Future AGI Cloud."
CUSTOMER_CODE = "def evaluate(input, **kwargs):\n    return 0.5\n"
BUILTIN_CODE = "def evaluate(input, **kwargs):\n    return 1.0\n"
URL = "/sdk/api/v1/configure-evaluations/"


@pytest.fixture
def builtin_code_template(db):
    template = EvalTemplate.no_workspace_objects.create(
        name="pause_configure_builtin_code",
        owner="system",
        organization=None,
        workspace=None,
        eval_type="code",
        config={
            "eval_type_id": "CustomCodeEval",
            "code": BUILTIN_CODE,
            "required_keys": ["input"],
        },
    )
    # save() stamps the request's organization and workspace on a row saved while a
    # workspace context is set; a built-in has neither, so clear them without save().
    EvalTemplate.no_workspace_objects.filter(pk=template.pk).update(
        organization=None, workspace=None
    )
    template.refresh_from_db()
    return template


def customer_template(organization, workspace):
    return EvalTemplate.no_workspace_objects.create(
        name="pause_configure_customer_code",
        owner="user",
        organization=organization,
        workspace=workspace,
        eval_type="code",
        config={
            "eval_type_id": "CustomCodeEval",
            "code": CUSTOMER_CODE,
            "required_keys": ["input"],
        },
    )


def configure(client, name):
    return client.post(
        URL,
        {
            "eval_config": {"eval_templates": name, "inputs": {"input": "input"}},
            "platform": "langfuse",
        },
        format="json",
    )


def stored_configs(name):
    from tracer.models.external_eval_config import ExternalEvalConfig

    return ExternalEvalConfig.objects.filter(eval_template__name=name).count()


@pytest.mark.django_db
@override_settings(CUSTOM_CODE_EVALS_PAUSED=True)
def test_paused_customer_code_is_refused_at_configure_with_the_sentence(
    auth_client, organization, workspace
):
    template = customer_template(organization, workspace)
    response = configure(auth_client, template.name)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert SENTENCE in response.content.decode()
    assert "Configuration is valid" not in response.content.decode()
    assert stored_configs(template.name) == 0


@pytest.mark.django_db
@override_settings(CUSTOM_CODE_EVALS_PAUSED=True)
def test_paused_check_lets_builtin_code_through(builtin_code_template):
    from agentic_eval.core_evals.fi_utils.custom_code_pause import (
        refuse_paused_custom_code_template,
    )

    refuse_paused_custom_code_template(builtin_code_template)  # does not raise


@pytest.mark.django_db
@override_settings(CUSTOM_CODE_EVALS_PAUSED=False)
def test_without_the_setting_the_check_lets_customer_code_through(
    organization, workspace
):
    from agentic_eval.core_evals.fi_utils.custom_code_pause import (
        refuse_paused_custom_code_template,
    )

    refuse_paused_custom_code_template(
        customer_template(organization, workspace)
    )  # does not raise


@pytest.mark.django_db
@override_settings(CUSTOM_CODE_EVALS_PAUSED=False)
def test_without_the_setting_configure_does_not_answer_the_sentence(
    auth_client, organization, workspace
):
    template = customer_template(organization, workspace)
    response = configure(auth_client, template.name)
    assert SENTENCE not in response.content.decode(), response.content.decode()[:500]
