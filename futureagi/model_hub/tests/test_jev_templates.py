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


@pytest.mark.django_db
def test_r03_ui_draft_switched_to_llm_judge_saves_with_jev(auth_client):
    """The create page drafts an agent eval, then saves the user's LLM judge."""
    with patch("tfc.ee_gates._jev_denied_off_cloud", return_value=False):
        draft = auth_client.post(
            "/model-hub/eval-templates/create-v2/",
            # EvalCreatePage's draft request.
            {
                "is_draft": True,
                "eval_type": "agent",
                "output_type": "pass_fail",
                "model": "turing_large",
                "pass_threshold": 0.5,
            },
            format="json",
        )
        assert draft.status_code == 200, draft.data
        template_id = draft.data["result"]["id"]
        response = auth_client.put(
            f"/model-hub/eval-templates/{template_id}/update/",
            {
                "eval_type": "llm",
                "instructions": "Did the agent resolve {{conversation}}?",
                "model": "jev-latest",
                "output_type": "pass_fail",
                "pass_threshold": 0.5,
                "choice_scores": None,
                "multi_choice": False,
                "check_internet": False,
                "messages": [
                    {
                        "role": "system",
                        "content": "Did the agent resolve {{conversation}}?",
                    }
                ],
                "few_shot_examples": [],
                "template_format": "mustache",
            },
            format="json",
        )
    assert response.status_code == 200, response.data
    template = EvalTemplate.objects.get(id=template_id)
    assert template.config["agent_mode"] == "agent"
    assert template.config["jev_mapping"]["question_type"] == "noul"


@pytest.mark.django_db
def test_a_held_jev_autosave_saves_the_prompt_and_keeps_the_runnable_setup(
    auth_client,
):
    """The create page's autosave while a Jev Score has no levels yet: the
    whole payload is refused, so the page holds back the fields Jev's check
    reads and sends the rest. The prompt saves; the last runnable setup
    stays."""
    prompt = "Was the issue resolved in {{conversation}}?"
    with patch("tfc.ee_gates._jev_denied_off_cloud", return_value=False):
        draft = auth_client.post(
            "/model-hub/eval-templates/create-v2/",
            {
                "is_draft": True,
                "eval_type": "llm",
                "output_type": "pass_fail",
                "model": "turing_large",
                "pass_threshold": 0.5,
            },
            format="json",
        )
        assert draft.status_code == 200, draft.data
        url = f"/model-hub/eval-templates/{draft.data['result']['id']}/update/"
        runnable = auth_client.put(
            url,
            {
                "eval_type": "llm",
                "model": "jev-latest",
                "output_type": "pass_fail",
                "jev_mapping": {"revision": "jev-map-v1", "question_type": "noul"},
            },
            format="json",
        )
        assert runnable.status_code == 200, runnable.data
        unfinished = auth_client.put(
            url,
            {
                "instructions": prompt,
                "model": "jev-latest",
                "output_type": "percentage",
                "jev_mapping": {
                    "revision": "jev-map-v1",
                    "question_type": "score",
                    "score": {"levels": []},
                },
            },
            format="json",
        )
        assert unfinished.status_code == 400, unfinished.data
        held = auth_client.put(
            url,
            {
                "instructions": prompt,
                "messages": [{"role": "system", "content": prompt}],
                "pass_threshold": 0.6,
                "error_localizer_enabled": False,
                "template_format": "mustache",
            },
            format="json",
        )
    assert held.status_code == 200, held.data
    template = EvalTemplate.objects.get(id=draft.data["result"]["id"])
    assert template.criteria == prompt
    assert template.config["messages"] == [{"role": "system", "content": prompt}]
    assert template.model == "jev-latest"
    assert template.output_type_normalized == "pass_fail"
    assert template.config["jev_mapping"]["question_type"] == "noul"


@pytest.mark.django_db
def test_jev_create_refuses_choice_scores_outside_0_1_like_any_model(auth_client):
    """Jev skips only the generic "choice_scores is required" (it answers a
    missing choice with its own typed code). Its scores are checked as for
    any model and as the update checks them: the score of the label Jev picks
    is the eval's score."""
    with patch("tfc.ee_gates._jev_denied_off_cloud", return_value=False):
        response = auth_client.post(
            "/model-hub/eval-templates/create-v2/",
            payload("deterministic", choice_scores={"Yes": 5, "No": 0}),
            format="json",
        )
    assert response.status_code == 400, response.data
    assert "between 0 and 1" in str(response.data)
    assert not EvalTemplate.objects.filter(name="jev-test").exists()


@pytest.mark.django_db
@pytest.mark.parametrize("with_mapping", [False, True])
def test_a_jev_draft_switched_to_code_saves_and_forgets_the_jev_model(
    auth_client, with_mapping
):
    """Pick Jev latest, let autosave run, switch to Code: the page leaves the
    model out for code evals, so the stored jev-latest used to make every
    save fail with JEV_EVAL_TYPE_UNSUPPORTED (an older page also sent the
    mapping)."""
    with patch("tfc.ee_gates._jev_denied_off_cloud", return_value=False):
        response = auth_client.post(
            "/model-hub/eval-templates/create-v2/",
            payload("pass_fail"),
            format="json",
        )
        assert response.status_code == 200, response.data
        template_id = response.data["result"]["id"]
        body = {
            "eval_type": "code",
            "code": "def evaluate(**kwargs): return True",
            "code_language": "python",
            "output_type": "pass_fail",
            "pass_threshold": 0.5,
            "template_format": "mustache",
        }
        if with_mapping:
            body["jev_mapping"] = {}
        saved = auth_client.put(
            f"/model-hub/eval-templates/{template_id}/update/", body, format="json"
        )
        assert saved.status_code == 200, saved.data
        again = auth_client.put(
            f"/model-hub/eval-templates/{template_id}/update/",
            {"code": "def evaluate(**kwargs): return False"},
            format="json",
        )
        assert again.status_code == 200, again.data
    template = EvalTemplate.objects.get(id=template_id)
    assert template.config["eval_type_id"] == "CustomCodeEval"
    assert template.model is None
    assert "model" not in template.config
    assert "jev_mapping" not in template.config


@pytest.mark.django_db
def test_r08_choice_template_still_binds_after_jsonb_reorders_labels(auth_client):
    from model_hub.utils.jev_templates import validate_jev_binding

    with patch("tfc.ee_gates._jev_denied_off_cloud", return_value=False):
        response = auth_client.post(
            "/model-hub/eval-templates/create-v2/",
            payload("deterministic", choice_scores={"Yes": 1, "No": 0}),
            format="json",
        )
        assert response.status_code == 200, response.data
        template = EvalTemplate.objects.get(id=response.data["result"]["id"])
        # jsonb stores shorter keys first.
        assert list(template.choice_scores) == ["No", "Yes"]
        assert template.config["jev_mapping"]["choice"]["labels"] == ["Yes", "No"]
        assert validate_jev_binding(template, model="jev-latest") is None


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


JEV_OK = {
    "model": "jev-1.13.0",
    "answers": {"q1": {"type": "noul", "noul": 0.91}},
    "usage": {"input_tokens": 320, "output_tokens": 20},
}


def _jev_playground(auth_client, model="jev-latest"):
    with patch("tfc.ee_gates._jev_denied_off_cloud", return_value=False):
        created = auth_client.post(
            "/model-hub/eval-templates/create-v2/", payload(), format="json"
        )
    assert created.status_code == 200, created.data
    body = {
        "template_id": created.data["result"]["id"],
        "mapping": {"input": "Agent: I issued a full refund."},
        "config": {"params": {}},
        "model": model,
    }
    if model is None:
        # The request names no model (null; an absent one defaults to
        # turing_large); the run's binding config carries the Jev model.
        body["config"]["model"] = "jev-latest"
    return lambda: auth_client.post("/model-hub/eval-playground/", body, format="json")


@pytest.fixture
def jev_gateway(monkeypatch):
    """A gateway address and internal key for the default Jev transport. Every
    test patches httpx.post, so neither is ever dialled."""
    for name in ("AGENTCC_INTERNAL_URL", "AGENTCC_GATEWAY_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(
        "AGENTCC_GATEWAY_INTERNAL_URL", "http://agentcc-gateway.test:8080"
    )
    monkeypatch.setenv("AGENTCC_INTERNAL_API_KEY", "internal-gateway-key")


# The eval path calls close_old_connections(), so these need real transactions.
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "transport,url,headers,model",
    [
        (
            "gateway",
            "http://agentcc-gateway.test:8080/v1/systemone",
            {
                "Authorization": "Bearer internal-gateway-key",
                "x-agentcc-provider-key": "org-typesafe-key",
                "Content-Type": "application/json",
            },
            "typesafe/jev-latest",
        ),
        (
            "direct",
            "https://api.typesafe.ai/v1/systemone",
            {
                "Authorization": "Bearer org-typesafe-key",
                "Content-Type": "application/json",
            },
            "jev-latest",
        ),
    ],
)
def test_byok_playground_runs_on_the_org_typesafe_key(
    auth_client, user, settings, jev_gateway, transport, url, headers, model
):
    import httpx

    from model_hub.models.api_key import ApiKey

    settings.JEV_TRANSPORT = transport
    run = _jev_playground(auth_client)
    # Saved the way Settings -> AI Providers saves it.
    ApiKey.objects.create(
        provider="typesafe", organization=user.organization, key="org-typesafe-key"
    )
    with (
        patch("tfc.ee_gates._jev_denied_off_cloud", return_value=False),
        patch("tfc.capabilities.service.check_or_raise"),
        patch("httpx.post", return_value=httpx.Response(200, json=JEV_OK)) as post,
    ):
        response = run()
    assert response.status_code == 200, response.data
    assert post.call_count == 1
    assert post.call_args.args == (url,)
    assert post.call_args.kwargs["headers"] == headers
    assert post.call_args.kwargs["json"]["model"] == model
    assert "org-typesafe-key" not in str(response.data)
    assert "internal-gateway-key" not in str(response.data)


# The eval path calls close_old_connections(), so these need real transactions.
@pytest.mark.django_db(transaction=True)
def test_byok_playground_rejected_key_through_the_gateway_is_not_retried(
    auth_client, user, jev_gateway, caplog
):
    import httpx

    from model_hub.models.api_key import ApiKey

    run = _jev_playground(auth_client)
    ApiKey.objects.create(
        provider="typesafe", organization=user.organization, key="org-typesafe-key"
    )
    rejected = httpx.Response(
        403,
        json={
            "error": {
                "type": "authentication_error",
                "code": "provider_key_rejected",
                "message": "org-typesafe-key internal-gateway-key",
                "param": None,
            }
        },
    )
    with (
        patch("tfc.ee_gates._jev_denied_off_cloud", return_value=False),
        patch("tfc.capabilities.service.check_or_raise"),
        patch("httpx.post", return_value=rejected) as post,
    ):
        response = run()
    assert response.status_code == 400
    assert post.call_count == 1
    assert "JEV_KEY_INVALID" in caplog.text
    for secret in ("org-typesafe-key", "internal-gateway-key"):
        assert secret not in str(response.data)
        assert secret not in caplog.text


# The eval path calls close_old_connections(), so these need real transactions.
@pytest.mark.django_db(transaction=True)
def test_byok_playground_without_a_typesafe_key_says_which_key_to_add(
    auth_client, jev_gateway
):
    run = _jev_playground(auth_client)
    with (
        patch("tfc.ee_gates._jev_denied_off_cloud", return_value=False),
        patch("tfc.capabilities.service.check_or_raise"),
        patch("httpx.post") as post,
    ):
        response = run()
    assert response.status_code == 400
    assert "API key not configured for typesafe" in str(response.data)
    # Typed like the SDK path, so the frontend can key off the code.
    assert response.data["code"] == "JEV_KEY_MISSING"
    post.assert_not_called()


# The eval path calls close_old_connections(), so these need real transactions.
@pytest.mark.django_db(transaction=True)
def test_playground_bills_jev_with_the_metering_events(auth_client, user, jev_gateway):
    """One Jev billing shape on every path: the playground emits the
    ee.jev.metering event, keyed to its run, and returns a string reason."""
    import httpx

    from ee.jev.metering import usage_event_id
    from ee.usage.services.config import BillingConfig
    from model_hub.models.api_key import ApiKey

    run = _jev_playground(auth_client)
    ApiKey.objects.create(
        provider="typesafe", organization=user.organization, key="org-typesafe-key"
    )
    with (
        patch("tfc.ee_gates._jev_denied_off_cloud", return_value=False),
        patch("tfc.capabilities.service.check_or_raise"),
        patch("httpx.post", return_value=httpx.Response(200, json=JEV_OK)),
        patch("ee.usage.services.emitter.emit") as emit,
    ):
        response = run()
    assert response.status_code == 200, response.data
    result = response.data["result"]
    assert result["reason"] == "Jev probability 0.91 vs threshold 0.50 → Pass"
    # The declared response contract: reason is a string, never null.
    from model_hub.serializers.contracts import EvalExecutionResponseSerializer

    contract = EvalExecutionResponseSerializer(data=response.data)
    assert contract.is_valid(), contract.errors
    [event] = [call.args[0] for call in emit.call_args_list]
    billing = BillingConfig.get()
    assert event.event_type == "jev_evaluator"
    assert event.amount == billing.calculate_ai_credits(billing.get_eval_per_run_fee())
    assert event.event_id == usage_event_id(result["log_id"], 1)
    assert event.org_id == str(user.organization.id)
    assert event.properties["provider"] == "typesafe"
    assert event.properties["requested_model"] == "jev-latest"
    assert event.properties["actual_model"] == "jev-1.13.0"
    assert event.properties["mapping_revision"].startswith("jev-map-v1:")
    assert event.properties["attempt"] == 1


# The eval path calls close_old_connections(), so these need real transactions.
@pytest.mark.django_db(transaction=True)
def test_playground_without_a_model_logs_and_limits_a_saved_jev_template_as_jev(
    auth_client, user, jev_gateway
):
    """A run whose request names no model runs Jev from its binding: the
    credit check and the API call log go by the template and the run's config
    (what the engine runs), never Turing Large (whose 50/min cap Jev does not
    have)."""
    import httpx

    from ee.usage.services import metering
    from model_hub.models.api_key import ApiKey
    from model_hub.views.utils import evals as eval_utils

    run = _jev_playground(auth_client, model=None)
    ApiKey.objects.create(
        provider="typesafe", organization=user.organization, key="org-typesafe-key"
    )
    with (
        patch("tfc.ee_gates._jev_denied_off_cloud", return_value=False),
        patch("tfc.capabilities.service.check_or_raise"),
        patch("httpx.post", return_value=httpx.Response(200, json=JEV_OK)),
        patch(
            "ee.usage.services.metering.check_usage", wraps=metering.check_usage
        ) as check_usage,
        patch.object(
            eval_utils,
            "log_and_deduct_cost_for_api_request",
            wraps=eval_utils.log_and_deduct_cost_for_api_request,
        ) as log_row,
    ):
        response = run()
    assert response.status_code == 200, response.data
    assert check_usage.call_args.args[1] == "jev_evaluator"
    assert log_row.call_args.kwargs["api_call_type"] == "jev_evaluator"


# The eval path calls close_old_connections(), so these need real transactions.
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "answer,code,calls,message",
    [
        (
            (403, "provider_key_rejected"),
            "JEV_KEY_INVALID",
            1,
            "TypeSafe rejected the API key saved under Settings → AI Providers. "
            "Update the key and try again.",
        ),
        (
            (503, "provider_overloaded"),
            "JEV_PROVIDER_ERROR",
            3,
            "TypeSafe couldn't complete this evaluation. Try again.",
        ),
    ],
)
def test_playground_says_what_went_wrong_with_typesafe(
    auth_client, user, jev_gateway, answer, code, calls, message
):
    """The gateway's answer goes through the real client, so the failed
    attempts are recorded and billing them by mistake would emit events."""
    import httpx

    from model_hub.models.api_key import ApiKey

    run = _jev_playground(auth_client)
    ApiKey.objects.create(
        provider="typesafe", organization=user.organization, key="org-typesafe-key"
    )
    status, gateway_code = answer
    gateway_answer = httpx.Response(
        status,
        json={"error": {"type": "upstream_error", "code": gateway_code}},
        # A retried run stays instant.
        headers={"Retry-After": "0"},
    )
    with (
        patch("tfc.ee_gates._jev_denied_off_cloud", return_value=False),
        patch("tfc.capabilities.service.check_or_raise"),
        patch("httpx.post", return_value=gateway_answer) as post,
        patch("ee.usage.services.emitter.emit") as emit,
    ):
        response = run()
    assert post.call_count == calls
    assert response.status_code == 400
    assert response.data["code"] == code
    assert response.data["message"] == message
    assert response.data["feature"] == "jev"
    assert "org-typesafe-key" not in str(response.data)
    # A failed Jev run emits no billing event.
    emit.assert_not_called()


@pytest.mark.django_db
def test_dataset_preview_gates_jev_like_the_dataset_run(
    auth_client, organization, workspace
):
    """The dataset Test button: an unknown Jev model or an incompatible
    binding is one 400, and no row runs."""
    from model_hub.models.choices import DatasetSourceChoices
    from model_hub.models.develop_dataset import Dataset, Row

    dataset = Dataset.objects.create(
        name="jev-preview",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
    )
    Row.objects.create(dataset=dataset, order=0)
    with patch("tfc.ee_gates._jev_denied_off_cloud", return_value=False):
        created = auth_client.post(
            "/model-hub/eval-templates/create-v2/", payload(), format="json"
        )
    assert created.status_code == 200, created.data
    turing_score = EvalTemplate.objects.create(
        name="turing-score",
        organization=organization,
        workspace=workspace,
        model="turing_large",
        config={
            "eval_type_id": "CustomPromptEvaluator",
            "output": "score",
            "model": "turing_large",
            "rule_prompt": "Judge {{input}}",
        },
    )
    url = f"/model-hub/develops/{dataset.id}/preview_run_eval/"
    with (
        patch("tfc.ee_gates._jev_denied_off_cloud", return_value=False),
        patch("model_hub.views.develop_dataset.process_eval_for_single_row") as row,
        patch("ee.jev.client.JevSystemOneClient.evaluate") as client,
    ):
        unknown = auth_client.post(
            url,
            {
                "template_id": created.data["result"]["id"],
                "model": "jev-2.0.0",
                "config": {"mapping": {"input": "x"}},
            },
            format="json",
        )
        unmapped = auth_client.post(
            url,
            {
                "template_id": str(turing_score.id),
                "model": "jev-latest",
                "config": {"mapping": {"input": "x"}},
            },
            format="json",
        )
        # Gated first, as the dataset run is: before the dataset or template
        # is even looked up.
        unknown_first = auth_client.post(
            f"/model-hub/develops/{uuid4()}/preview_run_eval/",
            {
                "template_id": str(uuid4()),
                "model": "jev-2.0.0",
                "config": {"mapping": {"input": "x"}},
            },
            format="json",
        )
    assert unknown.status_code == 400, unknown.data
    assert unknown.data["code"] == "JEV_MODEL_UNKNOWN"
    assert unknown_first.status_code == 400, unknown_first.data
    assert unknown_first.data["code"] == "JEV_MODEL_UNKNOWN"
    assert unmapped.status_code == 400, unmapped.data
    assert unmapped.data["code"] == "JEV_MAPPING_INVALID"
    assert unmapped.data["details"] == {
        "jev_mapping.score.levels": ["JEV_SCORE_LEVELS_REQUIRED"]
    }
    row.assert_not_called()
    client.assert_not_called()


@pytest.mark.django_db
def test_usage_tab_reads_a_run_stored_with_a_null_reason(
    auth_client, organization, workspace
):
    """Jev runs stored reason=null before reasons were strings; the Usage tab
    must still load for them (and for any evaluator that does the same)."""
    from ee.usage.models.usage import APICallLog, APICallStatusChoices

    template = EvalTemplate.no_workspace_objects.create(
        name="jev-null-reason",
        organization=organization,
        workspace=workspace,
        owner="user",
        config={"output": "Pass/Fail", "eval_type_id": "CustomPromptEvaluator"},
        model="jev-latest",
        visible_ui=True,
    )
    log = APICallLog.objects.create(
        organization=organization,
        workspace=workspace,
        status=APICallStatusChoices.SUCCESS.value,
        cost=0.01,
        deducted_cost=0,
        source="eval_playground",
        source_id=str(template.id),
        config={
            "output": {"output": "Passed", "reason": None},
            "mappings": {"input": "hello"},
        },
    )
    response = auth_client.get(
        f"/model-hub/eval-templates/{template.id}/usage/",
        {"page": 0, "page_size": 25, "period": "30d"},
    )
    assert response.status_code == 200, response.content
    [row] = response.json()["result"]["table"]
    assert row["row_id"] == str(log.log_id)
    assert row["reason"]["cell_value"] == ""


@pytest.mark.django_db
def test_ai_providers_names_typesafe_with_its_brand_casing(auth_client):
    from model_hub.models.choices import LiteLlmModelProvider

    response = auth_client.get("/model-hub/develops/provider-status/")
    assert response.status_code == 200, response.data
    providers = {row["provider"]: row for row in response.data["result"]["providers"]}
    assert providers["typesafe"]["display_name"] == "TypeSafe"
    # Only the label changes; the stored provider value stays lowercase.
    assert LiteLlmModelProvider("typesafe").value == "typesafe"
    assert providers["openai"]["display_name"] == "Openai"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "error,message",
    [
        (
            ("JEV_TIMEOUT", True),
            "TypeSafe didn't respond in time. Try again.",
        ),
        (
            "missing-key",
            "API key not configured for typesafe. Please add your API key in settings.",
        ),
    ],
)
def test_template_test_endpoint_says_what_went_wrong_with_typesafe(
    auth_client, user, workspace, error, message
):
    from agentic_eval.core_evals.run_prompt.litellm_models import (
        MissingProviderKeyError,
    )
    from ee.jev.client import JevGatewayError

    template = EvalTemplate.no_workspace_objects.create(
        name="jev-template-test",
        organization=user.organization,
        workspace=workspace,
        owner="user",
        eval_type="code",
        config={
            "code": "def evaluate(**kwargs): return True",
            "output": "Pass/Fail",
            "eval_type_id": "CustomCodeEval",
            "required_keys": ["output"],
        },
    )
    raised = (
        MissingProviderKeyError("typesafe")
        if error == "missing-key"
        else JevGatewayError(*error)
    )
    with patch(
        "model_hub.views.separate_evals.run_eval_func", side_effect=raised
    ) as run:
        response = auth_client.post(
            "/model-hub/test-evaluation/",
            {
                "name": "jev-template-test",
                "template_type": "Function",
                "template_id": str(template.id),
                "eval_type_id": "CustomCodeEval",
                "model": "",
                "output_type": "Pass/Fail",
                "required_keys": ["output"],
                "input_data_types": {"output": "text"},
                "config": {
                    "mapping": {"output": "same"},
                    "config": {"code": "def evaluate(**kwargs): return True"},
                },
            },
            format="json",
        )
    run.assert_called_once()
    assert response.status_code == 400
    assert response.data["message"] == message
    # Typed like the SDK path, so the frontend can key off the code.
    assert response.data["code"] == (
        "JEV_KEY_MISSING" if error == "missing-key" else "JEV_TIMEOUT"
    )


@pytest.mark.django_db
def test_a_composite_child_shows_the_jev_message(organization, workspace):
    """A Jev child that fails shows the fixed text, not the bare code."""
    from ee.jev.client import JevGatewayError
    from model_hub.models.evals_metric import CompositeEvalChild
    from model_hub.utils.composite_execution import execute_composite_children_sync

    child = EvalTemplate.no_workspace_objects.create(
        name="jev-child",
        organization=organization,
        workspace=workspace,
        owner="user",
        model="jev-latest",
        config={"output": "Pass/Fail", "eval_type_id": "CustomPromptEvaluator"},
    )
    parent = EvalTemplate.no_workspace_objects.create(
        name="jev-composite",
        organization=organization,
        workspace=workspace,
        owner="user",
        template_type="composite",
        config={},
    )
    link = CompositeEvalChild.objects.create(
        parent=parent, child=child, order=0, weight=1.0
    )
    with patch(
        "model_hub.views.utils.evals.run_eval_func",
        side_effect=JevGatewayError("JEV_KEY_INVALID"),
    ):
        outcome = execute_composite_children_sync(
            parent=parent,
            child_links=[link],
            mapping={"input": "hello"},
            config={},
            org=organization,
        )
    [child_result] = outcome.child_results
    assert child_result.status == "failed"
    assert child_result.error == (
        "TypeSafe rejected the API key saved under Settings → AI Providers. "
        "Update the key and try again."
    )
