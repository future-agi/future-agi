"""Appending a variant executes only its new run and retains earlier evidence."""

from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from model_hub.models.develop_dataset import Cell
from model_hub.models.experiments import ExperimentPromptConfig, ExperimentRun
from model_hub.models.native_experiment_runs import NativeRunBinding
from model_hub.services import credit_ledger_port as meter
from model_hub.services.experiment_submit import state_of
from model_hub.tests import test_native_prompt_create_bridge as fixtures
from tfc.temporal.experiments import activities

pytestmark = pytest.mark.django_db(transaction=True)
tenants = fixtures.tenants


@pytest.fixture
def metered_provider(monkeypatch):
    ledger = meter.MockCreditLedger(default_balance=Decimal("10"))
    dispatched = []
    monkeypatch.setattr(
        meter, "estimate_call_cost", lambda *args, **kwargs: Decimal("0.05")
    )
    meter.reset_meter_safety()

    def respond(provider):
        call = meter.current_managed_call()
        assert call is not None
        meter.require_managed_hold(model_name=provider.model)
        meter.note_gateway_response(
            call,
            SimpleNamespace(
                headers={"x-agentcc-request-id": f"fixture-{call.request_id}"}
            ),
        )
        dispatched.append(
            (call.envelope.row_id, call.envelope.call_ref, provider.model)
        )
        return f"{provider.model} output", {
            "metadata": {
                "usage": {
                    "prompt_tokens": 7,
                    "completion_tokens": 3,
                    "total_tokens": 10,
                },
                "cost": {
                    "total_cost": 0.01,
                    "pricing_source": "available_models",
                },
            }
        }

    try:
        with (
            meter.use_credit_ledger(ledger),
            meter.use_route_resolver(
                lambda **kwargs: meter.PayerRoute.managed(provider="openai")
            ),
            patch(fixtures.PROVIDER, autospec=True, side_effect=respond) as provider,
        ):
            yield ledger, provider, dispatched
    finally:
        meter.reset_meter_safety()


def execute(experiment, context):
    prepared = activities._setup_prompt_v2_sync(
        str(experiment.id),
        str(experiment.snapshot_dataset_id),
        context["config_id"],
        native_context=context,
    )
    assert prepared["row_ids"]
    for row_id in prepared["row_ids"]:
        result = fixtures.row(experiment, context, prepared, row_id)
        assert result["status"] == "COMPLETED"
    fixtures.finish(context, prepared)
    assert activities._check_experiment_status_sync(str(experiment.id))["is_complete"]
    experiment.refresh_from_db()
    return prepared


def usage_rows(run):
    return list(
        run.items.order_by("id").values(
            "id", "run_id", "example_id", "trial_number", "token_usage"
        )
    )


def test_append_executes_one_new_run_and_preserves_prior_results_and_usage(
    tenants, metered_provider
):
    actor = tenants["a"]
    ledger, provider, dispatched = metered_provider
    experiment, original_body = fixtures.seed_native_admission(actor)
    original_context = next(
        iter(state_of(experiment)["workflow_args"]["native_runs"].values())
    )
    original_prepared = execute(experiment, original_context)
    original = NativeRunBinding.objects.get(run_id=original_context["run_id"])
    row_ids = original_prepared["row_ids"]
    assert len(row_ids) == 2
    assert provider.call_count == len(row_ids)
    assert original.run.status == "completed"
    assert all(
        item.trial_number == 1 and item.output == "gpt-4o-mini output"
        for item in original.run.items.all()
    )
    assert set(dispatched) == {
        (str(row_id), str(original_prepared["column_id"]), "gpt-4o-mini")
        for row_id in row_ids
    }

    run_ids = set(
        ExperimentRun.no_workspace_objects.filter(experiment=experiment).values_list(
            "id", flat=True
        )
    )
    assert run_ids == {original.run_id}
    previous_config = list(
        ExperimentPromptConfig.no_workspace_objects.filter(
            pk=original.native_config_id
        ).values()
    )
    previous_results = list(original.run.items.order_by("id").values())
    previous_usage = usage_rows(original.run)
    assert len(previous_usage) == len(row_ids)
    assert all(
        row["token_usage"]
        == {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}
        for row in previous_usage
    )
    previous_cells = list(
        Cell.no_workspace_objects.filter(dataset=experiment.snapshot_dataset)
        .order_by("id")
        .values()
    )
    previous_holds = deepcopy(ledger.holds)
    assert len(previous_holds) == len(row_ids)
    assert all(hold.state == "settled" for hold in previous_holds.values())
    previous_operations = deepcopy(ledger.calls)

    def assert_retained():
        assert list(original.run.items.order_by("id").values()) == previous_results
        assert usage_rows(original.run) == previous_usage
        assert (
            list(
                Cell.no_workspace_objects.filter(
                    id__in=[cell["id"] for cell in previous_cells]
                )
                .order_by("id")
                .values()
            )
            == previous_cells
        )
        assert (
            list(
                ExperimentPromptConfig.no_workspace_objects.filter(
                    pk=original.native_config_id
                ).values()
            )
            == previous_config
        )
        assert {
            hold_id: ledger.holds[hold_id] for hold_id in previous_holds
        } == previous_holds

    body = {
        "append_only": True,
        "prompt_config": [
            {
                **deepcopy(original_body["prompt_config"][0]),
                "name": "Changed variant",
                "model": "gpt-4o",
                "model_params": {"temperature": 0.5, "max_tokens": 128},
            }
        ],
    }
    with patch(fixtures.START, return_value="appended-workflow") as start:
        response = actor["client"].put(
            f"/model-hub/experiments/v2/{experiment.id}/", body, format="json"
        )
    assert response.status_code == 200, response.content
    child = (
        NativeRunBinding.objects.filter(snapshot__experiment=experiment)
        .exclude(pk=original.pk)
        .get()
    )
    assert set(
        ExperimentRun.no_workspace_objects.filter(experiment=experiment).values_list(
            "id", flat=True
        )
    ) == run_ids | {child.run_id}
    assert child.run_id != original.run_id
    assert child.native_config_id != original.native_config_id
    state = state_of(experiment)
    contexts = state["workflow_args"]["native_runs"]
    assert set(contexts) == {str(child.native_config_id)}
    assert set(state["execution_plan"]["targets"]) == set(contexts)
    start.assert_called_once()
    assert start.call_args.kwargs["native_runs"] == contexts
    assert start.call_args.kwargs["rerun_prompt_config_ids"] == [
        str(child.native_config_id)
    ]
    assert start.call_args.kwargs["rerun_agent_config_ids"] == []
    assert start.call_args.kwargs["rerun_eval_template_ids"] == []
    assert_retained()
    assert ledger.calls == previous_operations

    provider.reset_mock()
    dispatched.clear()
    prepared = execute(experiment, contexts[str(child.native_config_id)])
    assert set(prepared["row_ids"]) == set(row_ids)
    assert prepared["column_id"] != original_prepared["column_id"]
    assert provider.call_count == len(row_ids)
    assert set(dispatched) == {
        (str(row_id), str(prepared["column_id"]), "gpt-4o") for row_id in row_ids
    }
    child.run.refresh_from_db()
    assert child.run.status == "completed"
    assert child.run.items.count() == len(row_ids)
    assert all(
        item.trial_number == 1 and item.output == "gpt-4o output"
        for item in child.run.items.all()
    )
    assert len(usage_rows(child.run)) == len(row_ids)
    assert_retained()
    new_holds = set(ledger.holds) - set(previous_holds)
    assert len(new_holds) == len(row_ids)
    assert all(ledger.holds[hold_id].state == "settled" for hold_id in new_holds)
    assert [entry[0] for entry in ledger.calls[len(previous_operations) :]] == [
        operation for _ in row_ids for operation in ("hold", "settle")
    ]
    assert NativeRunBinding.objects.filter(snapshot__experiment=experiment).count() == 2
