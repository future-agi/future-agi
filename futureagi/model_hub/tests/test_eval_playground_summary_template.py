"""The eval playground runs the saved summary template picked in the editor,
and says so in plain words when that template cannot be used.

Only the agent evaluator and its licence check are stand-ins. The request goes
through the real view, run_eval_func, EvaluationRunner and engine.
"""

import re
import uuid
from types import SimpleNamespace

import pytest
from rest_framework import status

from accounts.models import Organization
from model_hub.models.choices import OwnerChoices
from model_hub.models.eval_summary_template import EvalSummaryTemplate
from model_hub.models.evals_metric import EvalTemplate
from model_hub.views.utils import evals as eval_utils
from tfc import ee_loader
from tfc.capabilities import service as capability_service

UNAVAILABLE = "Summary template is unavailable. Choose another summary."
# frontend/src/utils/errorUtils.js INTERNAL_ERROR_MARKERS. The playground's
# error handler (getSafeActionErrorMessage) replaces a 400 line that matches
# it with the generic "Please retry" copy, so the line must never match.
FRONTEND_INTERNAL_ERROR_MARKERS = re.compile(
    r"DB::|ClickHouse|Stack\s*trace|Traceback|Code:\s*\d+|SELECT\s|"
    r"maximum:\s*\d+|elapsed\s+\d+",
    re.IGNORECASE,
)


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


def _agent_eval(user, workspace, summary_type):
    return EvalTemplate.no_workspace_objects.create(
        name=f"agent-summary-{uuid.uuid4().hex[:8]}",
        organization=user.organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        eval_type="agent",
        config={
            "eval_type_id": "AgentEvaluator",
            "output": "Pass/Fail",
            "rule_prompt": "Is {{output}} polite?",
            "required_keys": ["output"],
            "model": "gpt-4o-mini",
            "summary": {"type": summary_type},
        },
        visible_ui=True,
        output_type_normalized="pass_fail",
        pass_threshold=0.5,
    )


def _playground(auth_client, template):
    return auth_client.post(
        "/model-hub/eval-playground/",
        {
            "template_id": str(template.id),
            "model": "gpt-4o-mini",
            "mapping": {"output": "Thanks for asking."},
            "config": {},
        },
        format="json",
    )


def _assert_unavailable(response):
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["message"] == UNAVAILABLE
    assert "could not be completed" not in str(response.data)
    assert not FRONTEND_INTERNAL_ERROR_MARKERS.search(response.data["message"])


@pytest.mark.django_db
def test_a_deleted_summary_template_says_so(auth_client, user, workspace, agent_judge):
    saved = EvalSummaryTemplate.objects.create(
        name="Verdict first",
        criteria="Lead with the verdict.",
        organization=user.organization,
    )
    template = _agent_eval(user, workspace, f"custom:{saved.id}")
    saved.delete()

    response = _playground(auth_client, template)

    _assert_unavailable(response)
    assert agent_judge == []


@pytest.mark.django_db
def test_another_organizations_summary_template_is_refused(
    auth_client, user, workspace, agent_judge
):
    other_org = Organization.objects.create(name="summary-template-other-org")
    foreign = EvalSummaryTemplate.objects.create(
        name="Theirs",
        criteria="Another organization's private summary.",
        organization=other_org,
    )
    template = _agent_eval(user, workspace, f"custom:{foreign.id}")

    response = _playground(auth_client, template)

    _assert_unavailable(response)
    assert "private summary" not in str(response.data)
    assert agent_judge == []


@pytest.mark.django_db
def test_a_malformed_summary_reference_says_so(auth_client, user, workspace, agent_judge):
    template = _agent_eval(user, workspace, "custom:not-a-template-id")

    response = _playground(auth_client, template)

    _assert_unavailable(response)
    assert agent_judge == []


@pytest.mark.django_db
def test_a_saved_summary_template_reaches_the_agent_as_inline_criteria(
    auth_client, user, workspace, agent_judge
):
    criteria = "Lead with the verdict, then list each failed check."
    saved = EvalSummaryTemplate.objects.create(
        name="Verdict first", criteria=criteria, organization=user.organization
    )
    template = _agent_eval(user, workspace, f"custom:{saved.id}")

    response = _playground(auth_client, template)

    assert UNAVAILABLE not in str(response.data)
    assert [judge.config["summary"] for judge in agent_judge] == [
        {"type": "custom", "custom": criteria}
    ]
