"""Fixtures the pairwise judge test modules share through ``pytest_plugins``.

A support module, not a test module: pytest registers its fixtures for every
module that names it, whatever order the test modules are collected in.
"""

from unittest.mock import Mock

import pytest

from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion
from model_hub.models.experiments import ExperimentRun, ExperimentRunItem
from model_hub.services import pairwise_judges as service
from model_hub.tests.test_pairwise_preferences import pair  # noqa: F401


@pytest.fixture
def prepared(pair, organization, workspace):  # noqa: F811
    experiment, runs, examples = pair
    version = runs[0].dataset_version
    ExperimentRun.no_workspace_objects.filter(pk__in=[r.pk for r in runs]).update(
        dataset_version=version, status="completed"
    )
    runs = sorted(runs, key=lambda run: str(run.pk))
    for index, run in enumerate(runs):
        run.refresh_from_db()
        for example_id in examples:
            ExperimentRunItem.no_workspace_objects.create(
                run=run,
                example_id=example_id,
                status="completed",
                input={"question": "Explain"},
                output=f"Answer {index}",
            )
    template = EvalTemplate.no_workspace_objects.create(
        name="compare_answers",
        owner=OwnerChoices.USER.value,
        organization=organization,
        workspace=workspace,
        eval_type="llm",
        config={
            "eval_type_id": "CustomPromptEvaluator",
            "output": "choices",
            "required_keys": ["input", "output_a", "output_b"],
            "choices": ["A", "B", "tie"],
            "multi_choice": False,
            "rule_prompt": "Compare {{output_a}} with {{output_b}} for {{input}}.",
        },
        choices=["A", "B", "tie"],
    )
    pin = EvalTemplateVersion.objects.create_version(
        template,
        model="turing_flash",
        config_snapshot=template.config,
        organization=organization,
        workspace=workspace,
    )
    return experiment, runs, examples, pin


@pytest.fixture
def dispatch(monkeypatch):
    mock = Mock()
    monkeypatch.setattr(
        "model_hub.tasks.pairwise_judges.evaluate_pairwise_item.apply_async", mock
    )
    monkeypatch.setattr(
        "model_hub.tasks.pairwise_judges.dispatch_pairwise_job.apply_async",
        lambda *, args, queue: service.dispatch_job(args[0]),
    )
    return mock
