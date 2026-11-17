"""An external composite evaluation passes the usage check, is charged once,
runs its children through the shared composite helper, saves the result and
sends it once. A refused charge, or a composite without children, stops before
any child runs."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tfc.constants.api_calls import APICallStatusChoices

pytestmark = pytest.mark.django_db

# Placeholder Langfuse settings: the config refuses to save without them, and
# every test patches the send, so they are never used.
LANGFUSE = {
    "langfuse_secret_key": "sk-lf-test",
    "langfuse_public_key": "pk-lf-test",
    "langfuse_host": "http://langfuse.test",
}

CHILD_NAMES = ["first-child", "second-child"]
CHILD_SCORES = [1.0, 0.5]
SUMMARY = "2 of 2 children completed."


def _composite_config(user, workspace, child_names):
    """An external Langfuse config on a composite with one child link per
    name, in that order."""
    from model_hub.models.evals_metric import CompositeEvalChild, EvalTemplate
    from tracer.models.external_eval_config import ExternalEvalConfig, StatusChoices

    parent = EvalTemplate.no_workspace_objects.create(
        name="external-composite",
        organization=user.organization,
        workspace=workspace,
        template_type="composite",
        config={},
    )
    for order, name in enumerate(child_names):
        child = EvalTemplate.no_workspace_objects.create(
            name=name,
            organization=user.organization,
            workspace=workspace,
            config={},
        )
        CompositeEvalChild.objects.create(
            parent=parent, child=child, order=order, weight=1.0, config={}
        )
    return ExternalEvalConfig.objects.create(
        organization=user.organization,
        workspace=workspace,
        eval_template=parent,
        name="external-composite",
        config={},
        mapping={"input": "Agent: I issued a full refund."},
        model="turing_large",
        platform="langfuse",
        credentials=dict(LANGFUSE),
        status=StatusChoices.PENDING,
    )


def _outcome(links):
    """What the composite helper returns once both children have run."""
    from model_hub.types import CompositeChildResult
    from model_hub.utils.composite_execution import CompositeRunOutcome

    return CompositeRunOutcome(
        child_results=[
            CompositeChildResult(
                child_id=str(link.child_id),
                child_name=link.child.name,
                order=link.order,
                score=CHILD_SCORES[index],
                output_type="score",
            )
            for index, link in enumerate(links)
        ],
        aggregate_score=0.75,
        aggregate_pass=True,
        summary=SUMMARY,
        error_localizer_results=None,
    )


@contextmanager
def _boundaries(charge_status=APICallStatusChoices.PROCESSING.value, outcome=None):
    """Stub what the run reaches outside this module: the usage check allows
    it, the charge returns a row with `charge_status`, the composite helper
    returns `outcome`, and the send to Langfuse is recorded."""
    with (
        patch(
            "ee.usage.services.metering.check_usage",
            return_value=SimpleNamespace(allowed=True, reason=""),
        ) as usage,
        patch(
            "tracer.utils.external_eval.log_and_deduct_cost_for_api_request",
            return_value=SimpleNamespace(status=charge_status),
        ) as charge,
        patch(
            "model_hub.utils.composite_execution.execute_composite_children_sync",
            return_value=outcome,
        ) as run,
        patch("tracer.utils.external_eval._send_eval_result") as send,
    ):
        yield SimpleNamespace(usage=usage, charge=charge, run=run, send=send)


def test_an_external_composite_run_is_charged_once_runs_its_children_and_saves_the_result(
    user, workspace
):
    from model_hub.models.evals_metric import CompositeEvalChild
    from tracer.utils.external_eval import _execute_composite_on_external_platform

    config = _composite_config(user, workspace, CHILD_NAMES)
    links = list(
        CompositeEvalChild.objects.filter(parent=config.eval_template)
        .select_related("child")
        .order_by("order")
    )
    with _boundaries(outcome=_outcome(links)) as stubs:
        _execute_composite_on_external_platform(config)

    stubs.usage.assert_called_once()
    stubs.charge.assert_called_once()
    charged = stubs.charge.call_args.kwargs
    assert charged["source"] == "tracer"
    assert charged["source_id"] == config.id
    assert charged["config"]["is_futureagi_eval"] is False
    stubs.run.assert_called_once()
    ran = stubs.run.call_args.kwargs
    assert ran["source"] == "external_composite"
    assert [link.id for link in ran["child_links"]] == [link.id for link in links]
    config.refresh_from_db()
    assert config.eval_results["composite"] is True
    assert config.eval_results["value"] == 0.75
    assert [
        child["child_name"] for child in config.eval_results["children"]
    ] == CHILD_NAMES
    stubs.send.assert_called_once()
    assert stubs.send.call_args.args[1:] == (0.75, SUMMARY)


def test_a_refused_charge_stops_before_any_child_runs(user, workspace):
    from tracer.utils.external_eval import _execute_composite_on_external_platform

    config = _composite_config(user, workspace, CHILD_NAMES)
    refused = APICallStatusChoices.INSUFFICIENT_CREDITS.value
    with (
        _boundaries(charge_status=refused) as stubs,
        pytest.raises(ValueError, match="API call not allowed") as error,
    ):
        _execute_composite_on_external_platform(config)

    assert refused in error.value.args
    stubs.charge.assert_called_once()
    stubs.run.assert_not_called()
    stubs.send.assert_not_called()
    config.refresh_from_db()
    assert config.eval_results == {}


def test_a_composite_with_no_children_refuses_before_any_charge(user, workspace):
    from tracer.utils.external_eval import _execute_composite_on_external_platform

    config = _composite_config(user, workspace, [])
    with (
        _boundaries() as stubs,
        pytest.raises(ValueError, match="has no children"),
    ):
        _execute_composite_on_external_platform(config)

    stubs.usage.assert_not_called()
    stubs.charge.assert_not_called()
    stubs.run.assert_not_called()
    stubs.send.assert_not_called()
