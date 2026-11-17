"""The evaluation editor's Test runs the definition on screen and saves none
of it: the template, its versions and so its experiments keep what was saved.

The editor sends its unsaved fields in the playground request's
config.unsaved_definition; the view runs an in-memory copy of the template
with them.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rest_framework import status
from tfc import ee_loader
from tfc.capabilities import service as capability_service

from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion
from model_hub.views.utils import evals as eval_utils

PLAYGROUND = "/model-hub/eval-playground/"
SAVED_PROMPT = "Is {{output}} polite?"
UNSAVED_PROMPT = "Is {{output}} kind to {{audience}}?"
RAN = {"output": "Passed", "reason": "fixture reason"}


def _llm_eval(user, workspace):
    return EvalTemplate.no_workspace_objects.create(
        name=f"judge-{uuid.uuid4().hex[:8]}",
        organization=user.organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        eval_type="llm",
        criteria=SAVED_PROMPT,
        model="gpt-4o-mini",
        config={
            "eval_type_id": "CustomPromptEvaluator",
            "output": "Pass/Fail",
            "rule_prompt": SAVED_PROMPT,
            "required_keys": ["output"],
            "model": "gpt-4o-mini",
            "messages": [{"role": "system", "content": SAVED_PROMPT}],
            "template_format": "mustache",
        },
        visible_ui=True,
        output_type_normalized="pass_fail",
        pass_threshold=0.5,
    )


def _code_eval(user, workspace, code):
    return EvalTemplate.no_workspace_objects.create(
        name=f"code-{uuid.uuid4().hex[:8]}",
        organization=user.organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        eval_type="code",
        criteria=code,
        config={
            "eval_type_id": "CustomCodeEval",
            "code": code,
            "language": "python",
            "required_keys": ["output"],
            "output": "Pass/Fail",
        },
        visible_ui=True,
        output_type_normalized="pass_fail",
        pass_threshold=0.5,
    )


def _saved_version(template, user):
    return EvalTemplateVersion.objects.create_version(
        eval_template=template,
        prompt_messages=(template.config or {}).get("messages") or [],
        config_snapshot=dict(template.config or {}),
        criteria=template.criteria or "",
        model=template.model or "",
        user=user,
        organization=template.organization,
        workspace=template.workspace,
    )


def _row(template):
    template = EvalTemplate.no_workspace_objects.get(id=template.id)
    return {
        "model": template.model,
        "config": template.config,
        "criteria": template.criteria,
        "output_type_normalized": template.output_type_normalized,
        "pass_threshold": template.pass_threshold,
        "choice_scores": template.choice_scores,
        "updated_at": template.updated_at,
    }


def _versions(template):
    return [
        (v.id, v.version_number, v.model, v.criteria, v.config_snapshot, v.is_default)
        for v in EvalTemplateVersion.objects.filter(eval_template=template).order_by(
            "version_number"
        )
    ]


def _post(auth_client, template, unsaved, **extra):
    return auth_client.post(
        PLAYGROUND,
        {
            "template_id": str(template.id),
            "mapping": {"output": "Thanks for asking.", "audience": "a customer"},
            "config": {"unsaved_definition": unsaved},
            **extra,
        },
        format="json",
    )


@pytest.mark.django_db
def test_test_runs_the_unsaved_judge_and_instructions_and_saves_nothing(
    auth_client, user, workspace
):
    template = _llm_eval(user, workspace)
    _saved_version(template, user)
    row_before, versions_before = _row(template), _versions(template)
    unsaved = {
        "model": "gpt-4o",
        "instructions": UNSAVED_PROMPT,
        "messages": [{"role": "system", "content": UNSAVED_PROMPT}],
        "output_type": "percentage",
        "pass_threshold": 0.8,
        "choice_scores": None,
        "template_format": "mustache",
    }

    with patch("model_hub.views.separate_evals.run_eval_func", return_value=RAN) as run:
        response = _post(auth_client, template, unsaved, model="gpt-4o")

    assert response.status_code == status.HTTP_200_OK, response.content
    run.assert_called_once()
    runtime_config, _mapping, ran = run.call_args.args[:3]
    assert "unsaved_definition" not in runtime_config
    assert run.call_args.kwargs["model"] == "gpt-4o"
    assert ran.id == template.id
    assert ran.model == "gpt-4o"
    assert ran.config["model"] == "gpt-4o"
    assert ran.criteria == UNSAVED_PROMPT
    assert ran.config["rule_prompt"] == UNSAVED_PROMPT
    assert ran.config["messages"] == unsaved["messages"]
    assert sorted(ran.config["required_keys"]) == ["audience", "output"]
    assert ran.output_type_normalized == "percentage"
    assert ran.config["output"] == "score"
    assert ran.pass_threshold == 0.8
    with pytest.raises(RuntimeError):
        ran.save()

    # The template row and its versions are exactly as saved, so the New
    # experiment card and experiment runs still use the saved judge.
    assert _row(template) == row_before
    assert _versions(template) == versions_before
    assert _row(template)["model"] == "gpt-4o-mini"


@pytest.mark.django_db
def test_test_runs_unsaved_code_without_saving_it(auth_client, user, workspace):
    saved_code = "def evaluate(output, **kwargs):\n    return True\n"
    unsaved_code = "def evaluate(output, **kwargs):\n    return len(output) > 3\n"
    template = _code_eval(user, workspace, saved_code)
    _saved_version(template, user)
    row_before, versions_before = _row(template), _versions(template)

    with patch("model_hub.views.separate_evals.run_eval_func", return_value=RAN) as run:
        response = _post(
            auth_client,
            template,
            {"code": unsaved_code, "code_language": "python"},
        )

    assert response.status_code == status.HTTP_200_OK, response.content
    ran = run.call_args.args[2]
    assert ran.config["code"] == unsaved_code
    assert ran.criteria == unsaved_code
    assert ran.config["eval_type_id"] == "CustomCodeEval"
    assert _row(template) == row_before
    assert _versions(template) == versions_before


@pytest.mark.django_db
def test_a_system_evaluation_refuses_unsaved_changes(auth_client):
    system = EvalTemplate.no_workspace_objects.create(
        name=f"system-judge-{uuid.uuid4().hex[:8]}",
        organization=None,
        workspace=None,
        owner=OwnerChoices.SYSTEM.value,
        eval_type="llm",
        criteria=SAVED_PROMPT,
        config={
            "eval_type_id": "CustomPromptEvaluator",
            "output": "Pass/Fail",
            "rule_prompt": SAVED_PROMPT,
            "required_keys": ["output"],
        },
        visible_ui=True,
        output_type_normalized="pass_fail",
    )

    with patch("model_hub.views.separate_evals.run_eval_func", return_value=RAN) as run:
        response = _post(
            auth_client, system, {"instructions": UNSAVED_PROMPT}, model="gpt-4o"
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
    assert "only on your own evaluations" in str(response.data)
    run.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "unsaved,needle",
    [
        pytest.param(
            {"pass_threshold": 2}, "pass_threshold", id="threshold-out-of-range"
        ),
        pytest.param({"output_type": "stars"}, "output_type", id="unknown-output-type"),
        pytest.param({"judge": "gpt-4o"}, "judge: Unknown field.", id="unknown-field"),
        pytest.param(["gpt-4o"], "must be an object", id="not-an-object"),
    ],
)
def test_invalid_unsaved_changes_are_refused_before_the_run(
    auth_client, user, workspace, unsaved, needle
):
    template = _llm_eval(user, workspace)
    row_before = _row(template)

    with patch("model_hub.views.separate_evals.run_eval_func", return_value=RAN) as run:
        response = _post(auth_client, template, unsaved, model="gpt-4o")

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
    assert needle in str(response.data)
    run.assert_not_called()
    assert _row(template) == row_before


@pytest.mark.django_db
def test_the_unsaved_definition_matches_what_save_writes(auth_client, user, workspace):
    """Test runs what Save would save: the same edit applied by the template
    update and by the unsaved definition gives the same definition."""
    from model_hub.utils.unsaved_eval_definition import unsaved_eval_template

    template = _llm_eval(user, workspace)
    original = EvalTemplate.no_workspace_objects.get(id=template.id)
    edit = {
        "instructions": "Rate {{output}} for {{audience}} using {{row.notes}}.",
        "messages": [
            {"role": "system", "content": "Rate {{output}} for {{audience}}."},
            {"role": "user", "content": "Context: {{context}}"},
        ],
        "model": "gpt-4o",
        "output_type": "deterministic",
        "choice_scores": {"Good": 1.0, "Okay": 0.5, "Bad": 0.0},
        "multi_choice": False,
        "pass_threshold": 0.6,
        "check_internet": True,
        "few_shot_examples": [],
        "error_localizer_enabled": False,
        "template_format": "mustache",
    }

    saved = auth_client.put(
        f"/model-hub/eval-templates/{template.id}/update/", edit, format="json"
    )
    assert saved.status_code == status.HTTP_200_OK, saved.content
    template.refresh_from_db()
    preview = unsaved_eval_template(original, edit)

    def definition(t):
        config = dict(t.config)
        config["required_keys"] = sorted(config.get("required_keys") or [])
        return {
            "config": config,
            "criteria": t.criteria,
            "model": t.model,
            "output_type_normalized": t.output_type_normalized,
            "pass_threshold": t.pass_threshold,
            "choice_scores": t.choice_scores,
            "choices": t.choices,
            "multi_choice": t.multi_choice,
            "error_localizer_enabled": t.error_localizer_enabled,
            "eval_type": t.eval_type,
        }

    assert definition(preview) == definition(template)
    # The copy started from the row as it was before the save.
    assert original.model == "gpt-4o-mini"


@pytest.fixture
def agent_judge(monkeypatch):
    """A stand-in agent evaluator that records the config it is built with."""
    built = []

    class AgentJudge:
        def __init__(self, **config):
            self.config = config
            self.model = config.get("model")
            self.cost = {"total_cost": 0}
            self.token_usage = {}
            built.append(self)

        def run(self, **inputs):
            return SimpleNamespace(
                eval_results=[
                    {
                        "data": "Passed",
                        "failure": False,
                        "reason": "fixture explanation",
                        "runtime": 1,
                        "model": self.model,
                        "metrics": [],
                        "metadata": {},
                    }
                ]
            )

    real_check = capability_service.check
    real_has_ee = ee_loader.has_ee

    def check(feature_id, **kwargs):
        if feature_id == "agentic_eval":
            return SimpleNamespace(allowed=True)
        return real_check(feature_id, **kwargs)

    def has_ee(module):
        return module == "ee.evals" or real_has_ee(module)

    monkeypatch.setattr(capability_service, "check", check)
    monkeypatch.setattr(ee_loader, "has_ee", has_ee)
    monkeypatch.setattr(eval_utils, "AgentEvaluator", AgentJudge, raising=False)
    return built


@pytest.mark.django_db
def test_the_engine_builds_the_judge_from_the_unsaved_definition(
    auth_client, user, workspace, agent_judge
):
    template = EvalTemplate.no_workspace_objects.create(
        name=f"agent-{uuid.uuid4().hex[:8]}",
        organization=user.organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        eval_type="agent",
        criteria=SAVED_PROMPT,
        model="gpt-4o-mini",
        config={
            "eval_type_id": "AgentEvaluator",
            "output": "Pass/Fail",
            "rule_prompt": SAVED_PROMPT,
            "required_keys": ["output"],
            "model": "gpt-4o-mini",
            "agent_mode": "agent",
            "summary": {"type": "concise"},
        },
        visible_ui=True,
        output_type_normalized="pass_fail",
        pass_threshold=0.5,
    )

    _post(
        auth_client,
        template,
        {"instructions": UNSAVED_PROMPT, "mode": "quick", "check_internet": True},
        model="gpt-4o-mini",
    )

    # The real run path leaves this test's database connection closed, so the
    # saved rows are checked by the tests above, which stub the run.
    assert [judge.config["rule_prompt"] for judge in agent_judge] == [UNSAVED_PROMPT]
    assert agent_judge[0].config["agent_mode"] == "quick"
    assert agent_judge[0].config["check_internet"] is True
