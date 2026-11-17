"""A group applied to an experiment with no model sent scores every row.

The experiment is made by the real create view and its rows run through the
real worker bodies (with the customer's own provider key, as the sweeper
tests set up). The group is applied through the real apply-eval-group view,
as the Apply dialog sends it: no model. Its evaluations run through the real
evaluation step. Replaced, as in those tests: the evaluator engine and the
API call log boundary, the usage preflight, the usage event emitter, the
row's provider call and the workflow start.
"""

import uuid
from unittest.mock import patch

import pytest

from model_hub.models.eval_groups import EvalGroup
from model_hub.models.evals_metric import UserEvalMetric
from model_hub.services.eval_group import create_eval_group
from model_hub.tests.test_native_prompt_create_bridge import (  # noqa: F401
    START,
    native_fixture_evaluator,
)
from model_hub.tests.test_native_prompt_create_bridge import tenants as tenants
from model_hub.tests.test_sweeper_leaves_experiment_metrics import (
    background_tick,
    completed_experiment,
    fixture_template,
    run_evaluation_step,
    scoring,
    sweeper,
)

pytestmark = pytest.mark.django_db(transaction=True)

TURING_DENIED = "tfc.ee_gates._turing_denied_off_cloud"


def apply_without_a_model(actor, experiment, templates, expect=200):
    """Apply on the experiment page as the Apply dialog sends it."""
    created = create_eval_group(
        "group-model-" + uuid.uuid4().hex[:12],
        "",
        [str(template.id) for template in templates],
        actor["user"],
        actor["workspace"],
    )
    group = EvalGroup.no_workspace_objects.get(id=created["id"])
    body = {
        "eval_group_id": str(group.id),
        "page_id": "EXPERIMENT",
        "filters": {"experiment_id": str(experiment.id)},
        "mapping": {"output": "output"},
    }
    with patch(START, return_value="admitted-workflow") as started:
        response = actor["client"].post(
            "/model-hub/eval-groups/apply-eval-group/", body, format="json"
        )
    assert response.status_code == expect, response.content
    metrics = list(
        UserEvalMetric.no_workspace_objects.filter(
            eval_group=group, source_id=str(experiment.id), deleted=False
        )
    )
    return metrics, started, response


def test_a_group_applied_with_no_model_scores_every_row(tenants):
    actor = tenants["a"]
    experiment = completed_experiment(actor)
    templates = [fixture_template(actor, "group-model")[0] for _ in range(2)]

    with patch(TURING_DENIED, return_value=False):
        metrics, started, response = apply_without_a_model(actor, experiment, templates)

    assert response.json()["result"] == {"run": "started"}
    assert "refused" not in response.json()
    assert len(metrics) == 2
    # Neither template names a model: each has Add evaluation's default.
    assert [metric.model for metric in metrics] == ["turing_large", "turing_large"]
    started.assert_called_once()
    assert sorted(started.call_args.kwargs["rerun_eval_template_ids"]) == sorted(
        str(metric.id) for metric in metrics
    )

    with sweeper() as sweep:
        with scoring() as scores:
            resolved = run_evaluation_step(
                experiment, metrics, between=lambda: background_tick(sweep)
            )

    report = scores.describe()
    rows = sorted(str(row_id) for row_id in resolved["row_ids"])
    assert rows, report
    per_metric = len(rows) * len(resolved["edt_column_ids"])
    for metric in metrics:
        assert scores.rows(metric) == sorted(rows * len(resolved["edt_column_ids"])), (
            report
        )
        assert scores.api_log_entries(metric) == per_metric, report
        assert scores.events_for(metric) == per_metric, report
    assert sweep.dispatched == [], report


def test_a_member_refused_by_add_evaluations_rule_adds_nothing_to_score(tenants):
    """Where Turing models are not available, a member whose template names
    one and a member that would get the Turing default are both refused, so
    nothing is added, no run starts and no row is scored or failed."""
    actor = tenants["a"]
    experiment = completed_experiment(actor)
    own, _pin = fixture_template(actor, "group-model-own")
    own.model = "turing_flash"
    own.save(update_fields=["model"])
    plain, _pin = fixture_template(actor, "group-model-plain")

    with patch(TURING_DENIED, return_value=True), scoring() as scores:
        metrics, started, response = apply_without_a_model(
            actor, experiment, [own, plain], expect=400
        )

    assert metrics == []
    # Each member is named, with the model it would store.
    message = response.json()["message"]
    assert message.startswith(
        (
            f"{own.name} was not added: its model, ",
            f"{plain.name} was not added: its model, ",
        )
    ), message
    assert message.endswith(
        ", can't run on this deployment. Change the evaluation's model, then "
        "apply again."
    ), message
    # Both members are named.
    assert f"{own.name} was not added: " in message, message
    assert f"{plain.name} was not added: " in message, message
    started.assert_not_called()
    assert scores.events == []
