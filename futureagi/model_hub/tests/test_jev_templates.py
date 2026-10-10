"""R-02..R-04, R-10, R-14: save/readback contracts (C19, C20)."""

from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion
from model_hub.views.separate_evals import (
    EvalTemplateCreateV2View,
    EvalTemplateUpdateView,
    EvalTemplateVersionCreateView,
)

pytestmark = pytest.mark.requires_ee


def payload(output="pass_fail", mapping=None, **extra):
    return {
        "name": "jev-test",
        "instructions": "Judge {{input}}",
        "model": "jev-latest",
        "output_type": output,
        "jev_mapping": mapping or {},
        **extra,
    }


def invoke(view, data, method="post", template_id=None):
    organization = SimpleNamespace(id=uuid4())
    user = SimpleNamespace(is_authenticated=True, organization=organization, id=uuid4())
    request = getattr(APIRequestFactory(), method)("/", data, format="json")
    request.organization = organization
    request.workspace = None
    force_authenticate(request, user=user)
    kwargs = {"template_id": template_id} if template_id else {}
    return view.as_view()(request, **kwargs)


@pytest.mark.parametrize(
    "output,mapping,scores,question_type",
    [
        ("pass_fail", {}, None, "noul"),
        ("deterministic", {}, {"Yes": 1, "No": 0}, "choice"),
        ("percentage", {"score": {"levels": ["bad", "ok", "good"]}}, None, "score"),
    ],
)
def test_r03_create_persists_mapping_before_version_snapshot(
    output, mapping, scores, question_type
):
    with (
        patch.object(EvalTemplate.objects, "filter") as objects,
        patch.object(EvalTemplate.no_workspace_objects, "filter") as system,
        patch.object(
            EvalTemplate.objects,
            "create",
            return_value=SimpleNamespace(id=uuid4(), name="jev-test"),
        ) as create,
        patch.object(EvalTemplateVersion.objects, "create_version") as version,
        patch("tfc.ee_gates._jev_denied_off_cloud", return_value=False),
    ):
        objects.return_value.exists.return_value = False
        system.return_value.exists.return_value = False
        result = invoke(
            EvalTemplateCreateV2View, payload(output, mapping, choice_scores=scores)
        )
    assert result.status_code == 200, result.data
    stored = create.call_args.kwargs["config"]["jev_mapping"]
    assert stored["question_type"] == question_type
    assert stored["revision"] == "jev-map-v1"
    assert len(stored["content_hash"]) == 64
    assert version.call_args.kwargs["config_snapshot"]["jev_mapping"] == stored


@pytest.mark.parametrize(
    "extra,code",
    [
        ({"output_type": "percentage"}, "JEV_SCORE_LEVELS_REQUIRED"),
        (
            {
                "output_type": "percentage",
                "jev_mapping": {"score": {"levels": ["only"]}},
            },
            "JEV_SCORE_LEVELS_INVALID",
        ),
        (
            {"output_type": "deterministic", "choice_scores": {"Yes": 1, "yes": 0}},
            "JEV_CHOICE_LABELS_INVALID",
        ),
        ({"multi_choice": True}, "JEV_MULTI_CHOICE_UNSUPPORTED"),
        (
            {
                "messages": [
                    {"role": "user", "content": "first"},
                    {"role": "user", "content": "second"},
                ]
            },
            "JEV_MESSAGES_REQUIRE_CONFIRMATION",
        ),
        (
            {"few_shot_examples": [{"input": "x", "output": "y"}]},
            "JEV_FEW_SHOT_UNSUPPORTED",
        ),
        ({"eval_type": "agent", "mode": "agent"}, "JEV_AGENT_MODE_UNSUPPORTED"),
        (
            {"eval_type": "agent", "mode": "quick", "tools": {"internet": True}},
            "JEV_TOOLS_UNSUPPORTED",
        ),
        (
            {"eval_type": "agent", "mode": "quick", "knowledge_bases": ["kb"]},
            "JEV_KNOWLEDGE_BASE_UNSUPPORTED",
        ),
        ({"check_internet": True}, "JEV_INTERNET_UNSUPPORTED"),
        (
            {
                "eval_type": "agent",
                "mode": "quick",
                "data_injection": {"full_row": True},
            },
            "JEV_DATA_INJECTION_UNSUPPORTED",
        ),
        (
            {"eval_type": "code", "code": "def evaluate(): return True"},
            "JEV_EVAL_TYPE_UNSUPPORTED",
        ),
        ({"jev_mapping": {"extra": "unknown"}}, "JEV_MAPPING_INVALID"),
    ],
)
def test_r03_r10_create_rejection_codes_zero_writes(extra, code):
    with (
        patch.object(EvalTemplate.objects, "filter") as objects,
        patch.object(EvalTemplate.no_workspace_objects, "filter") as system,
        patch.object(EvalTemplate.objects, "create") as create,
        patch("tfc.ee_gates._jev_denied_off_cloud", return_value=False),
        patch("ee.jev.client.JevSystemOneClient.evaluate") as gateway,
    ):
        objects.return_value.exists.return_value = False
        system.return_value.exists.return_value = False
        result = invoke(EvalTemplateCreateV2View, {**payload(), **extra})
    assert result.status_code == 400, result.data
    assert result.data["code"] == "JEV_MAPPING_INVALID"
    assert result.data["feature"] == "jev"
    assert code in result.data["details"][result.data["attr"]]
    create.assert_not_called()
    gateway.assert_not_called()


def test_r02_c19_update_existing_jev_allowed_after_capability_loss():
    template = EvalTemplate(
        id=uuid4(),
        name="jev-test",
        model="jev-latest",
        config={
            "eval_type_id": "CustomPromptEvaluator",
            "model": "jev-latest",
            "output": "Pass/Fail",
            "rule_prompt": "Judge {{input}}",
        },
    )
    with (
        patch.object(EvalTemplate.objects, "select_related") as query,
        patch.object(template, "save") as save,
        patch("tfc.ee_gates._jev_denied_off_cloud", return_value=True),
    ):
        query.return_value.get.return_value = template
        result = invoke(
            EvalTemplateUpdateView,
            {"description": "edited", "jev_mapping": {}},
            method="put",
            template_id=template.id,
        )
    assert result.status_code == 200, result.data
    assert template.model == "jev-latest"
    assert template.config["jev_mapping"]["revision"] == "jev-map-v1"
    save.assert_called_once()


def test_r14_model_change_denied_without_save():
    template = EvalTemplate(
        id=uuid4(),
        name="jev-test",
        model="turing_large",
        config={"eval_type_id": "CustomPromptEvaluator", "output": "Pass/Fail"},
    )
    with (
        patch.object(EvalTemplate.objects, "select_related") as query,
        patch.object(template, "save") as save,
        patch("tfc.ee_gates._jev_denied_off_cloud", return_value=True),
    ):
        query.return_value.get.return_value = template
        result = invoke(
            EvalTemplateUpdateView,
            {"model": "jev-latest"},
            method="put",
            template_id=template.id,
        )
    assert result.status_code == 402, result.data
    assert result.data["feature"] == "jev"
    save.assert_not_called()


def test_r03_version_omitted_mapping_defaults_from_template():
    template = EvalTemplate(
        id=uuid4(),
        name="jev-test",
        model="jev-latest",
        config={
            "eval_type_id": "CustomPromptEvaluator",
            "output": "score",
            "jev_mapping": {"score": {"levels": ["bad", "good"]}},
        },
    )
    with (
        patch.object(EvalTemplate.objects, "get", return_value=template),
        patch.object(EvalTemplateVersion.objects, "create_version") as create,
        patch.object(EvalTemplateVersion.objects, "filter"),
    ):
        create.return_value = SimpleNamespace(
            id=uuid4(), version_number=2, is_default=False
        )
        result = invoke(
            EvalTemplateVersionCreateView,
            {"config_snapshot": {}},
            template_id=template.id,
        )
    assert result.status_code == 200, result.data
    assert (
        create.call_args.kwargs["config_snapshot"]["jev_mapping"]["revision"]
        == "jev-map-v1"
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "output,mapping,scores",
    [
        ("pass_fail", {}, None),
        ("deterministic", {}, {"Yes": 1, "No": 0}),
        ("percentage", {"score": {"levels": ["bad", "good"]}}, None),
    ],
)
def test_r03_api_auth_client_save_and_c19_readback(
    auth_client, output, mapping, scores
):
    with patch("tfc.ee_gates._jev_denied_off_cloud", return_value=False):
        response = auth_client.post(
            "/model-hub/eval-templates/create-v2/",
            payload(output, mapping, choice_scores=scores),
            format="json",
        )
    assert response.status_code == 200, response.data
    template_id = response.data["result"]["id"]
    with patch("tfc.ee_gates._jev_denied_off_cloud", return_value=True):
        detail = auth_client.get(f"/model-hub/eval-templates/{template_id}/detail/")
    assert detail.status_code == 200
    assert detail.data["result"]["model"] == "jev-latest"
    assert detail.data["result"]["jev_mapping"]["revision"] == "jev-map-v1"


def test_r02_c19_detail_and_version_readback_under_denial():
    from django.utils import timezone

    from model_hub.views.separate_evals import (
        EvalTemplateDetailView,
    )

    stored = {
        "revision": "jev-map-v1",
        "question_type": "noul",
        "content_hash": "stored-verbatim",
    }
    template = EvalTemplate(
        id=uuid4(),
        name="jev-test",
        owner="system",
        model="jev-1.13.0",
        config={
            "eval_type_id": "CustomPromptEvaluator",
            "output": "Pass/Fail",
            "jev_mapping": stored,
        },
        created_at=timezone.now(),
        updated_at=timezone.now(),
    )
    with (
        patch.object(EvalTemplate.no_workspace_objects, "get", return_value=template),
        patch.object(EvalTemplateVersion.objects, "filter") as versions,
        patch.object(EvalTemplateVersion.objects, "get_default", return_value=None),
        patch("tfc.ee_gates._jev_denied_off_cloud", return_value=True),
    ):
        versions.return_value.count.return_value = 0
        detail = invoke(
            EvalTemplateDetailView, {}, method="get", template_id=template.id
        )
    assert detail.status_code == 200, detail.data
    assert detail.data["result"]["model"] == "jev-1.13.0"
    assert detail.data["result"]["jev_mapping"] == stored


def test_r04_c20_binding_preflight_rejects_before_dispatch():
    from model_hub.utils.jev_templates import validate_jev_binding

    template = EvalTemplate(
        config={
            "eval_type_id": "CustomPromptEvaluator",
            "output": "score",
            "model": "turing_large",
        }
    )
    with (
        patch("tfc.ee_gates._jev_denied_off_cloud", return_value=False),
        patch("ee.jev.client.JevSystemOneClient.evaluate") as client,
    ):
        response = validate_jev_binding(
            template, model=None, runtime_config={"run_config": {"model": "jev-latest"}}
        )
    assert response.status_code == 400
    assert response.data["details"] == {
        "jev_mapping.score.levels": ["JEV_SCORE_LEVELS_REQUIRED"]
    }
    client.assert_not_called()


def test_r14_binding_stored_model_is_gated_when_request_omits_model():
    from model_hub.utils.jev_templates import validate_jev_binding

    template = EvalTemplate(
        model="jev-latest",
        config={
            "eval_type_id": "CustomPromptEvaluator",
            "output": "Pass/Fail",
            "model": "jev-latest",
        },
    )
    with patch("tfc.ee_gates._jev_denied_off_cloud", return_value=True):
        response = validate_jev_binding(template, model=None)
    assert response.status_code == 402


@pytest.mark.django_db
@pytest.mark.parametrize(
    "extra,code",
    [
        ({"output_type": "percentage"}, "JEV_SCORE_LEVELS_REQUIRED"),
        (
            {
                "output_type": "percentage",
                "jev_mapping": {"score": {"levels": ["one"]}},
            },
            "JEV_SCORE_LEVELS_INVALID",
        ),
        (
            {"output_type": "deterministic", "choice_scores": {"Yes": 1, "yes": 0}},
            "JEV_CHOICE_LABELS_INVALID",
        ),
        ({"multi_choice": True}, "JEV_MULTI_CHOICE_UNSUPPORTED"),
        ({"check_internet": True}, "JEV_INTERNET_UNSUPPORTED"),
        ({"input_data_types": {"input": "pdf"}}, "JEV_INPUT_UNSUPPORTED"),
        (
            {"few_shot_examples": [{"input": "x", "output": "y"}]},
            "JEV_FEW_SHOT_UNSUPPORTED",
        ),
        (
            {"messages": [{"role": "user", "content": "{{input}}"}]},
            "JEV_MESSAGES_REQUIRE_CONFIRMATION",
        ),
        ({"eval_type": "agent", "mode": "auto"}, "JEV_AGENT_MODE_UNSUPPORTED"),
        ({"tools": {"internet": True}}, "JEV_TOOLS_UNSUPPORTED"),
        ({"knowledge_bases": ["kb"]}, "JEV_KNOWLEDGE_BASE_UNSUPPORTED"),
        ({"data_injection": {"trace_context": True}}, "JEV_DATA_INJECTION_UNSUPPORTED"),
        (
            {"eval_type": "code", "code": "def evaluate(): return True"},
            "JEV_EVAL_TYPE_UNSUPPORTED",
        ),
        ({"jev_mapping": {"revision": "bad"}}, "JEV_MAPPING_INVALID"),
    ],
)
def test_r03_auth_client_rejection_codes(auth_client, extra, code):
    with (
        patch("tfc.ee_gates._jev_denied_off_cloud", return_value=False),
        patch("ee.jev.client.JevSystemOneClient.evaluate") as client,
    ):
        response = auth_client.post(
            "/model-hub/eval-templates/create-v2/",
            {**payload(), **extra},
            format="json",
        )
    assert response.status_code == 400
    assert response.data["code"] == "JEV_MAPPING_INVALID"
    assert code in response.data["details"][response.data["attr"]]
    client.assert_not_called()
