"""A row with per-row limits on Future AGI's managed access says how to run it.

When a key can be saved for the model's provider, it says to add that key or
to create the experiment again without limits; when none can, it says only
the second. It never tells a person to add a key they cannot add.

Real admission, setup, row execution and recording. The workflow start, the
evaluator engine and the provider call are stand-ins, and a managed route
stands in for the gateway's admission (no gateway runs in tests). A saved
provider key is a real stored key, found by the product's own route lookup.
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db import transaction

from model_hub.models.api_key import ApiKey
from model_hub.models.choices import StatusType
from model_hub.models.experiments import ExperimentPromptConfig, ExperimentsTable
from model_hub.services import credit_ledger_port as cl
from model_hub.services.experiment_submit import admit_submission, stage_submission
from model_hub.tests.test_experiment_row_failure_codes import (
    bound_run,
    first_error,
    grid_error,
    output_cells,
    reason_of,
)
from model_hub.tests.test_model_route_support import BYOK_FIXTURE_VALUE
from model_hub.tests.test_native_prompt_create_bridge import (  # noqa: F401
    PROVIDER,
    START,
    finish,
    native_fixture_evaluator,
    row,
    setup,
)
from model_hub.tests.test_native_run_binding import native_world as native_world

# Row execution manages worker connections, as the native bridge tests do.
pytestmark = pytest.mark.django_db(transaction=True)

SENTENCE = (
    "Per-row limits need your own provider key for this model. "
    "Add your key and re-run, or create the experiment again without limits."
)
NO_KEY_SENTENCE = (
    "Per-row limits can't be used with this model. "
    "Create the experiment again without limits."
)
MODEL = "gpt-4o-mini"  # in the catalog; its provider is openai
LIMITS = {"timeout_s": 30}


@pytest.fixture(autouse=True)
def meter_safety(monkeypatch):
    monkeypatch.setattr(cl, "estimate_call_cost", lambda *a, **kw: Decimal("0.10"))
    monkeypatch.setattr(cl, "_sleep", lambda _delay: None)
    cl.reset_meter_safety()
    yield
    cl.reset_meter_safety()


def managed_route(**_kwargs):
    return cl.PayerRoute.managed(provider="openai")


def catalog_route(*, model_name, **_kwargs):
    """Managed access for any model, under the provider the catalog names for
    it, as the product's own route lookup names it."""
    from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager

    provider = LiteLLMModelManager(model_name).get_provider(model_name)
    return cl.PayerRoute.managed(provider=provider)


def model_without_own_key():
    """A catalog chat model whose provider no key can be saved for (it is not
    in the AI Providers list)."""
    from agentic_eval.core_evals.run_prompt.available_models import AVAILABLE_MODELS
    from model_hub.models.choices import LiteLlmModelProvider

    keyed = {choice.value for choice in LiteLlmModelProvider}
    names = sorted(
        model["model_name"]
        for model in AVAILABLE_MODELS
        if model.get("mode") == "chat" and model.get("providers") not in keyed
    )
    assert names, "every catalog chat model's provider takes a saved key"
    return names[0]


def claimed_answer(*_args, **_kwargs):
    """The managed transport: claim the open hold, note the gateway's request
    id, then answer."""
    call = cl.current_managed_call()
    assert call is not None
    cl.require_managed_hold(model_name=call.envelope.model)
    cl.note_gateway_response(
        call, SimpleNamespace(headers={"x-agentcc-request-id": "row-limits-call"})
    )
    return "answer", {
        "data": {"response": "answer"},
        "metadata": {
            "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
            "cost": {"total_cost": 0.02, "prompt_cost": 0.0, "completion_cost": 0.02},
            "response_time": 0.2,
        },
    }


def admit(w, operation):
    w["experiment"].refresh_from_db()
    with transaction.atomic():
        state = stage_submission(
            w["experiment"], {}, operation, authorized_workspace=w["dataset"].workspace
        )
    with patch(START, return_value=f"row-limits-{operation}"):
        admit_submission(str(w["experiment"].id), state["admission_id"])
    return setup(w["experiment"])


def started(w, limits, model=MODEL):
    """The native world's prompt on a catalogued model, with these per-row
    limits (or none), admitted and set up."""
    ExperimentPromptConfig.no_workspace_objects.filter(pk=w["config"].pk).update(
        model=model
    )
    ExperimentsTable.no_workspace_objects.filter(pk=w["experiment"].pk).update(
        run_policy={"limits": limits} if limits else {}
    )
    return admit(w, "create")


def run_rows(w, context, prepared, **provider):
    with patch(PROVIDER, **provider) as call:
        results = [
            row(w["experiment"], context, prepared, row_id)
            for row_id in prepared["row_ids"]
        ]
        finish(context, prepared)
    return [r["status"] for r in results], call


def assert_rows_say(context, prepared, error_type, code, sentence):
    run = bound_run(context)
    items = list(run.items.all())
    assert len(items) == len(prepared["row_ids"])
    for item in items:
        assert item.error == {"type": error_type, "code": code, "message": sentence}
        assert grid_error(run, item) == sentence
    for cell in output_cells(prepared):
        assert (cell.value, reason_of(cell)) == (sentence, {"reason": sentence})
    assert first_error(run) == sentence


def test_the_refusal_is_its_own_kind_of_managed_refusal():
    from model_hub.services.credit_ledger_port import ManagedRowLimitsUnavailableError
    from model_hub.services.row_failures import coded_message

    refusal = ManagedRowLimitsUnavailableError()
    # Every caller that handles the general refusal still does, and the
    # experiment row records it without a retry.
    assert isinstance(refusal, cl.ManagedDispatchUnavailableError)
    assert isinstance(refusal, ValueError)
    assert str(refusal) == SENTENCE
    assert (
        coded_message({"code": "row_limits_need_own_key", "message": "stored"})
        == SENTENCE
    )


def test_the_refusal_for_a_model_with_no_own_key_is_the_same_kind_with_its_own_words():
    from model_hub.services.credit_ledger_port import (
        ManagedRowLimitsUnavailableError,
        ManagedRowLimitsUnsupportedModelError,
    )
    from model_hub.services.row_failures import coded_message

    refusal = ManagedRowLimitsUnsupportedModelError()
    assert isinstance(refusal, ManagedRowLimitsUnavailableError)
    assert isinstance(refusal, ValueError)
    assert str(refusal) == NO_KEY_SENTENCE
    assert (
        coded_message({"code": "row_limits_unsupported", "message": "stored"})
        == NO_KEY_SENTENCE
    )


def test_a_limited_row_on_managed_access_says_how_to_run_it(native_world):
    w = native_world
    context, prepared = started(w, LIMITS)
    with (
        cl.use_credit_ledger(cl.MockCreditLedger()),
        cl.use_route_resolver(catalog_route),
    ):
        statuses, call = run_rows(w, context, prepared)
    call.assert_not_called()
    assert statuses == ["FAILED"] * len(prepared["row_ids"])
    assert_rows_say(
        context,
        prepared,
        "ManagedRowLimitsUnavailableError",
        "row_limits_need_own_key",
        SENTENCE,
    )


def test_a_limited_row_on_a_model_no_key_can_be_saved_for_says_limits_cant_be_used(
    native_world,
):
    w = native_world
    context, prepared = started(w, LIMITS, model=model_without_own_key())
    with (
        cl.use_credit_ledger(cl.MockCreditLedger()),
        cl.use_route_resolver(catalog_route),
    ):
        statuses, call = run_rows(w, context, prepared)
    call.assert_not_called()
    assert statuses == ["FAILED"] * len(prepared["row_ids"])
    assert_rows_say(
        context,
        prepared,
        "ManagedRowLimitsUnsupportedModelError",
        "row_limits_unsupported",
        NO_KEY_SENTENCE,
    )


def test_a_saved_provider_key_lets_the_rerun_go_with_its_limits(native_world, user):
    w = native_world
    context, prepared = started(w, LIMITS)
    with (
        cl.use_credit_ledger(cl.MockCreditLedger()),
        cl.use_route_resolver(managed_route),
    ):
        run_rows(w, context, prepared)
    refused = bound_run(context)
    assert {item.error["type"] for item in refused.items.all()} == {
        "ManagedRowLimitsUnavailableError"
    }
    # The organization saves its own key for the model's provider and re-runs.
    ApiKey.no_workspace_objects.create(
        provider="openai",
        organization=w["dataset"].organization,
        workspace=w["dataset"].workspace,
        key=BYOK_FIXTURE_VALUE,
        user=user,
    )
    ExperimentsTable.no_workspace_objects.filter(pk=w["experiment"].pk).update(
        status=StatusType.COMPLETED.value
    )
    context, prepared = admit(w, "rerun")
    # No route stand-in: the product's own lookup finds the saved key.
    with cl.use_credit_ledger(cl.MockCreditLedger()):
        statuses, call = run_rows(w, context, prepared, return_value=("answer", {}))
    assert statuses == ["COMPLETED"] * len(prepared["row_ids"])
    assert call.call_count == len(prepared["row_ids"])
    rerun = bound_run(context)
    assert rerun.id != refused.id
    assert {(item.status, item.output) for item in rerun.items.all()} == {
        ("completed", "answer")
    }
    # The re-run kept its limits: each row's answer was checked against them.
    for cell in output_cells(prepared):
        assert cell.value == "answer"
        assert reason_of(cell)["execution_limits"] == LIMITS


def test_a_new_experiment_without_limits_runs_on_managed_access(native_world):
    w = native_world
    context, prepared = started(w, None)
    ledger = cl.MockCreditLedger(balances={w["dataset"].organization_id: Decimal("1")})
    with cl.use_credit_ledger(ledger), cl.use_route_resolver(managed_route):
        statuses, call = run_rows(w, context, prepared, side_effect=claimed_answer)
    assert statuses == ["COMPLETED"] * len(prepared["row_ids"])
    assert call.call_count == len(prepared["row_ids"])
    run = bound_run(context)
    assert {(item.status, item.output) for item in run.items.all()} == {
        ("completed", "answer")
    }
