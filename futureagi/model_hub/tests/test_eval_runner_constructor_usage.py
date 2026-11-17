"""A runner built by its own constructor emits the usage event of an evaluated cell.

The usage event reads the model the run is charged for, which reads the
pinned version number. A lookup error there is swallowed by the emit path,
so a missing field would silently drop the event. This builds the runner the
way a real run does, pinned and unpinned, and checks the event is emitted.
"""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from model_hub.tests.test_pinned_version_runs import metric_for
from model_hub.tests.test_pinned_version_runs import world as world

pytestmark = [pytest.mark.django_db, pytest.mark.requires_ee]

USAGE = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}


@pytest.fixture
def billing_config():
    pytest.importorskip("ee.usage.services.emitter")
    config = Mock()
    config.get_eval_per_run_fee.return_value = 0.0005
    config.calculate_ai_credits.side_effect = lambda usd: usd * 100
    with patch("ee.usage.services.config.BillingConfig.get", return_value=config):
        yield config


def emitted(metric, version_number):
    from model_hub.views.eval_runner import EvaluationRunner

    runner = EvaluationRunner(
        user_eval_metric_id=metric.id,
        is_only_eval=True,
        source="dataset_evaluation",
        source_id=str(metric.template_id),
        source_configs={"dataset_id": str(metric.dataset_id)},
        version_number=version_number,
    )
    runner.eval_template = metric.template
    instance = SimpleNamespace(cost={"total_cost": 0.001}, token_usage=dict(USAGE))
    with (
        patch("model_hub.views.eval_runner._emit_jev_usage", return_value=False),
        patch("ee.usage.services.emitter.emit") as emit,
    ):
        runner._emit_cell_usage(
            instance, SimpleNamespace(log_id="log-1"), SimpleNamespace(id="row-1")
        )
    return emit


def test_a_pinned_run_built_by_its_constructor_emits_the_cell_usage_event(
    world, billing_config
):
    metric = metric_for(world, pinned_version=world["pinned"])

    emit = emitted(metric, world["pinned"].version_number)

    emit.assert_called_once()


def test_an_unpinned_run_built_by_its_constructor_emits_the_cell_usage_event(
    world, billing_config
):
    metric = metric_for(world)

    emit = emitted(metric, None)

    emit.assert_called_once()
