"""Missing model keys are actionable, fixed HTTP 400 responses in eval views.

Exercise the real request serializers and views with unsaved model instances.
Only template/trace lookups, entitlement checks and evaluation dispatch are
stand-ins; no provider call or database access is needed.
"""

from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models.organization import Organization
from agentic_eval.core_evals.run_prompt.litellm_models import MissingProviderKeyError
from model_hub.models.evals_metric import EvalTemplate
from model_hub.utils.model_key import ModelKeyMissing
from model_hub.views import separate_evals

KEY_MESSAGE = "Add an API key for this model. Go to Settings → AI providers."
PRIVATE_DETAIL = "synthetic-private-evaluation-detail"
CODE = "def evaluate(**kwargs): return True"


@pytest.fixture(params=["playground", "template-test", "trace"])
def view_case(request):
    organization = Organization(id=uuid4(), name="Evaluation error tests")
    template = EvalTemplate(
        id=uuid4(),
        name="model-key-error",
        organization=organization,
        owner="user",
        eval_type="code",
        config={
            "code": CODE,
            "output": "Pass/Fail",
            "eval_type_id": "CustomCodeEval",
            "required_keys": ["output"],
        },
        visible_ui=True,
        output_type_normalized="pass_fail",
        pass_threshold=0.5,
    )
    trace = SimpleNamespace(id=uuid4(), input={}, output={"output": "same"})
    name = request.param
    kwargs = {}
    if name == "playground":
        view = separate_evals.EvalPlayGroundAPIView
        body = {
            "template_id": str(template.id),
            "model": "",
            "mapping": {"output": "same"},
            "config": {"params": {}},
        }
    elif name == "template-test":
        view = separate_evals.TestEvaluationTemplateAPIView
        body = {
            "name": "model-key-error-test",
            "template_type": "Function",
            "template_id": str(template.id),
            "eval_type_id": "CustomCodeEval",
            "model": "",
            "output_type": "Pass/Fail",
            "required_keys": ["output"],
            "input_data_types": {"output": "text"},
            "config": {
                "mapping": {"output": "same"},
                "config": {"code": CODE},
            },
        }
    else:
        view = separate_evals.TraceEvalView
        body = {"trace_id": str(trace.id), "model": "gpt-4o-mini"}
        kwargs = {"template_id": template.id}

    dispatch = (
        "model_hub.views.utils.evals.run_eval_func"
        if name == "trace"
        else "model_hub.views.separate_evals.run_eval_func"
    )

    def post():
        http_request = APIRequestFactory().post("/", body, format="json")
        http_request.organization = organization
        http_request.workspace = None
        force_authenticate(
            http_request,
            user=SimpleNamespace(
                id=uuid4(), is_authenticated=True, organization=organization
            ),
        )
        return view.as_view()(http_request, **kwargs)

    with (
        patch("tfc.ee_gates.jev_gate_for_template", return_value=None),
        patch("tfc.ee_gates.turing_oss_gate_for_template", return_value=None),
        patch.object(
            separate_evals,
            "_get_accessible_eval_template_for_request",
            return_value=template,
        ),
        patch.object(
            EvalTemplate.no_workspace_objects, "get", return_value=template
        ),
        # Function config preparation also resolves the template via filter().first().
        patch.object(
            EvalTemplate.no_workspace_objects,
            "filter",
            return_value=SimpleNamespace(first=lambda: template),
        ),
        patch("tracer.models.trace.Trace.objects.get", return_value=trace),
        patch(dispatch) as execute,
    ):
        yield SimpleNamespace(name=name, post=post, execute=execute)


@pytest.mark.parametrize("tampered_text", [False, True], ids=["fixed", "private"])
def test_missing_model_key_returns_fixed_actionable_400(view_case, tampered_text):
    error = ModelKeyMissing()
    if tampered_text:
        # The response must not depend on even this typed exception's text.
        error.args = (PRIVATE_DETAIL,)
        error.__cause__ = RuntimeError(PRIVATE_DETAIL)
    view_case.execute.side_effect = error

    response = view_case.post()

    view_case.execute.assert_called_once()
    assert response.status_code == 400, response.data
    assert response.data["message"] == KEY_MESSAGE
    assert "Please retry" not in str(response.data)
    assert PRIVATE_DETAIL not in str(response.data)


@pytest.mark.parametrize("view_case", ["playground", "template-test"], indirect=True)
def test_existing_missing_provider_message_is_preserved(view_case):
    error = MissingProviderKeyError("openai")
    error.args = (PRIVATE_DETAIL,)
    view_case.execute.side_effect = error

    response = view_case.post()

    view_case.execute.assert_called_once()
    assert response.status_code == 400, response.data
    assert response.data["message"] == (
        "API key not configured for openai. Please add your API key in settings."
    )
    assert PRIVATE_DETAIL not in str(response.data)


def test_unrelated_execution_errors_keep_the_existing_safe_response(view_case):
    view_case.execute.side_effect = RuntimeError(PRIVATE_DETAIL)

    response = view_case.post()

    view_case.execute.assert_called_once()
    assert PRIVATE_DETAIL not in str(response.data)
    assert KEY_MESSAGE not in str(response.data)
    if view_case.name == "trace":
        assert response.status_code == 200
        assert response.data["result"]["status"] == "failed"
        assert response.data["result"]["reason"] == (
            "Evaluation could not be completed. Please retry."
        )
    else:
        assert response.status_code == 400
        assert response.data["message"] == (
            "Evaluation could not be completed. Please retry."
            if view_case.name == "playground"
            else "Evaluation template test could not be completed"
        )
