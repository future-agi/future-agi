"""
Regression tests for TH-5313:

`EvaluationRunner._prepare_eval_config` (AgentEvaluator branch) must read
`summary` from `user_eval_metric.config['run_config']['summary']` first and
fall back to the template default only when the binding has no value.

Before the fix, summary was read from `eval_template.config` only, so the
user's per-binding setting (saved correctly to the DB) was silently ignored
at execution time.
"""
import uuid

import pytest

from model_hub.models.choices import StatusType
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.views.eval_runner import EvaluationRunner


@pytest.fixture
def organization(db):
    from accounts.models.organization import Organization

    return Organization.objects.create(name=f"org-{uuid.uuid4().hex[:6]}")


@pytest.fixture
def workspace(db, organization):
    from accounts.models.user import User
    from accounts.models.workspace import Workspace

    user = User.objects.create_user(
        email=f"{uuid.uuid4().hex[:6]}@example.com",
        password="x",
        name="t",
        organization=organization,
    )
    return Workspace.objects.create(
        name="Default", organization=organization, is_default=True, created_by=user
    )


@pytest.fixture
def dataset(db, organization, workspace):
    from model_hub.models.choices import DatasetSourceChoices
    from model_hub.models.develop_dataset import Dataset

    return Dataset.objects.create(
        name=f"ds-{uuid.uuid4().hex[:6]}",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
    )


@pytest.fixture
def agent_template(db, organization, workspace):
    """An AgentEvaluator template with its own summary config."""
    return EvalTemplate.objects.create(
        name=f"agent-{uuid.uuid4().hex[:6]}",
        organization=organization,
        workspace=workspace,
        config={
            "eval_type_id": "AgentEvaluator",
            "rule_prompt": "test",
            "summary": {"type": "concise"},  # template default
            "model": "turing_large",
        },
    )


def _make_runner(metric):
    """Build an EvaluationRunner without going through DB initialization.

    The init path loads `user_eval_metric` from DB which requires the column
    setup we don't need here. We construct with `format_output=True` to skip
    `_initialize_eval_metric` and wire the fields by hand.
    """
    runner = EvaluationRunner(
        user_eval_metric_id=metric.id,
        format_output=True,  # skips DB load in __init__
    )
    runner.user_eval_metric = metric
    runner.eval_template = metric.template
    runner.organization_id = metric.organization_id
    runner.workspace_id = metric.workspace_id if metric.workspace_id else None
    return runner


@pytest.mark.django_db
class TestSummaryConfigFromBinding:
    """The binding's run_config.summary must reach the executor."""

    def test_binding_summary_overrides_template_default(
        self, agent_template, organization, workspace, dataset
    ):
        # The user explicitly chose "long" on their dataset attachment.
        metric = UserEvalMetric.objects.create(
            name="m",
            dataset=dataset,
            organization=organization,
            workspace=workspace,
            template=agent_template,
            status=StatusType.NOT_STARTED.value,
            config={"run_config": {"summary": {"type": "long"}}},
        )
        runner = _make_runner(metric)
        config = runner._prepare_eval_config({})
        assert config["summary"] == {"type": "long"}, (
            "binding summary must reach the executor — not the template default"
        )

    def test_falls_back_to_template_when_binding_has_no_run_config(
        self, agent_template, organization, workspace, dataset
    ):
        # Binding stored without any run_config at all.
        metric = UserEvalMetric.objects.create(
            name="m",
            dataset=dataset,
            organization=organization,
            workspace=workspace,
            template=agent_template,
            status=StatusType.NOT_STARTED.value,
            config={},
        )
        runner = _make_runner(metric)
        config = runner._prepare_eval_config({})
        assert config["summary"] == {"type": "concise"}

    def test_falls_back_to_template_when_run_config_missing_summary_key(
        self, agent_template, organization, workspace, dataset
    ):
        # Binding has run_config but no `summary` key inside it.
        metric = UserEvalMetric.objects.create(
            name="m",
            dataset=dataset,
            organization=organization,
            workspace=workspace,
            template=agent_template,
            status=StatusType.NOT_STARTED.value,
            config={"run_config": {"data_injection": {"variables_only": True}}},
        )
        runner = _make_runner(metric)
        config = runner._prepare_eval_config({})
        assert config["summary"] == {"type": "concise"}

    def test_explicit_empty_dict_summary_honors_binding_not_template(
        self, agent_template, organization, workspace, dataset
    ):
        # Nikhil's specific case: an explicit empty dict at the binding must
        # NOT silently fall back to the template — the user set it that way
        # for a reason. The fix uses ``is not None``, not ``or``, exactly to
        # preserve this null-vs-falsy distinction.
        metric = UserEvalMetric.objects.create(
            name="m",
            dataset=dataset,
            organization=organization,
            workspace=workspace,
            template=agent_template,
            status=StatusType.NOT_STARTED.value,
            config={"run_config": {"summary": {}}},
        )
        runner = _make_runner(metric)
        config = runner._prepare_eval_config({})
        assert config["summary"] == {}, (
            "explicit empty-dict summary must be honored (not silently swapped for template)"
        )

    def test_default_when_template_has_no_summary_at_all(
        self, organization, workspace, dataset
    ):
        # No summary on template, no run_config on binding → built-in default.
        template = EvalTemplate.objects.create(
            name=f"agent-{uuid.uuid4().hex[:6]}",
            organization=organization,
            workspace=workspace,
            config={
                "eval_type_id": "AgentEvaluator",
                "rule_prompt": "test",
                "model": "turing_large",
            },
        )
        metric = UserEvalMetric.objects.create(
            name="m",
            dataset=dataset,
            organization=organization,
            workspace=workspace,
            template=template,
            status=StatusType.NOT_STARTED.value,
            config={},
        )
        runner = _make_runner(metric)
        config = runner._prepare_eval_config({})
        assert config["summary"] == {"type": "concise"}
