"""The native binding's recorded version is what its evaluator is built with.

Admission, the worker row and the runner are real. Only the evaluator class
(at the point the engine builds it), the row's prompt call (its provider route
and response), the outer usage preflight and the billing boundary are
replaced, so nothing calls a model or charges anyone.
"""

import json
import uuid
from contextlib import ExitStack, contextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.db import transaction

from evaluations.engine import instance, registry
from model_hub.models.native_experiment_runs import NativeRunBinding
from model_hub.services.experiment_submit import admit_submission, stage_submission
from model_hub.tests.test_native_prompt_create_bridge import (  # noqa: F401
    START,
    _NativeFixtureEvaluator,
    attach_executable_native_eval,
    native_fixture_evaluator,
    native_preflight,
    row,
    setup,
)
from model_hub.tests.test_native_run_binding import native_world as native_world
from model_hub.views import eval_runner
from tfc.constants.api_calls import APICallTypeChoices

pytestmark = pytest.mark.django_db(transaction=True)

LIVE_RUBRIC = "Live rubric: is {{output}} polite?"
PINNED_RUBRIC = "Pinned rubric: is {{output}} correct?"
PINNED_SYSTEM = "Pinned system message"
PINNED_MESSAGES = [
    {"role": "system", "content": PINNED_SYSTEM},
    {"role": "user", "name": "eval_prompt", "content": PINNED_RUBRIC},
]
DEFAULT_MESSAGES = [
    {"role": "system", "content": "Default system message"},
    {"role": "user", "name": "eval_prompt", "content": "Default rubric: {{output}}"},
]
JUDGE_CONFIG = {
    "eval_type_id": "CustomPromptEvaluator",
    "output": "Pass/Fail",
    "required_keys": ["input", "output"],
    "rule_prompt": LIVE_RUBRIC,
    "model": "turing_small",
}
# The native row's prompt call: route, meter and provider in one function,
# imported by the row at call time.
PROMPT_CALL = "tfc.temporal.experiments.provider_policy.prompt_response"
# The row builds the provider client before that call; no route is resolved.
PROMPT_CLIENT = "model_hub.views.experiment_runner.RunPrompt"


@contextmanager
def upstream_output(text="actual output"):
    """The row's prompt step succeeds with ``text``, with no provider or route."""
    with (
        patch(PROMPT_CLIENT) as client,
        patch(PROMPT_CALL, return_value=(text, {})) as call,
    ):
        yield SimpleNamespace(client=client, call=call)


class Built:
    """Records what the engine built the evaluator with. Never runs a model."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs


@contextmanager
def usage_events():
    """Record the usage events the runner emits, with no billing service.

    The same test boundary as the playground metering tests: the usage
    modules the runner imports are stand-ins, so the event type and amount
    the runner computes are observable.
    """
    import sys
    from types import ModuleType

    events = []
    billing = SimpleNamespace(
        get_eval_per_run_fee=lambda: Decimal("0.01"),
        calculate_ai_credits=lambda cost: cost * 100,
    )
    members = {
        "ee.usage.schemas.events": {"UsageEvent": SimpleNamespace},
        "ee.usage.services.config": {
            "BillingConfig": SimpleNamespace(get=lambda: billing)
        },
        "ee.usage.services.emitter": {"emit": events.append},
        "ee.usage.schemas.event_types": {
            "BillingEventType": SimpleNamespace(
                CODE_EVALUATOR=SimpleNamespace(value="code_evaluator")
            )
        },
        "ee.usage.utils.event_properties": {"token_usage_properties": lambda usage: {}},
    }
    modules = {}
    for name, attributes in members.items():
        module = ModuleType(name)
        module.__dict__.update(attributes)
        modules[name] = module
    with (
        patch.dict(sys.modules, modules),
        patch.object(eval_runner, "_emit_jev_usage", return_value=False),
    ):
        yield events


@contextmanager
def native_engine(*, emit_usage=False):
    """Real engine build with the evaluator class swapped; billing recorded.

    The outer usage preflight (evaluate_native_row's check_usage) is allowed,
    as the transport tests do, so the row reaches the evaluator whichever
    usage module the environment has; its calls are recorded as ``limit``.
    With ``emit_usage`` the runner's own usage emit runs and its events are
    recorded; otherwise the emit is replaced and recorded as ``usage``.
    """
    registry._build_registry()
    real = instance.create_eval_instance
    engine = MagicMock(format_result=None)
    engine.cost = {}
    engine.token_usage = {}
    engine.model_route = None  # not a managed judge: no credit settlement
    engine.run.return_value = SimpleNamespace(
        eval_results=[
            {"metrics": [{"id": "score", "value": 1}], "reason": "ok", "failure": False}
        ]
    )
    seen, debits, debit_calls = [], [], []

    def build(eval_class, *args, **kwargs):
        built, _criteria = real(Built, *args, **kwargs)
        seen.append(built.kwargs)
        return engine, None

    def debit(*args, **kwargs):
        debit_calls.append((args, kwargs))
        record = SimpleNamespace(
            status=eval_runner.APICallStatusChoices.PROCESSING.value,
            config=json.dumps(kwargs["config"], default=str),
            log_id=uuid.uuid4(),
            save=MagicMock(),
        )
        debits.append(record)
        return record

    with ExitStack() as stack:
        stack.enter_context(
            patch.dict(registry._REGISTRY, {"NativeFixture": _NativeFixtureEvaluator})
        )
        stack.enter_context(
            patch.object(instance, "create_eval_instance", side_effect=build)
        )
        stack.enter_context(
            patch.object(
                eval_runner,
                "log_and_deduct_cost_for_api_request",
                MagicMock(side_effect=debit),
            )
        )
        refund = stack.enter_context(
            patch.object(eval_runner, "refund_cost_for_api_call", MagicMock())
        )
        usage, events = None, None
        if emit_usage:
            events = stack.enter_context(usage_events())
        else:
            usage = stack.enter_context(
                patch.object(eval_runner.EvaluationRunner, "_emit_cell_usage")
            )
        limit = MagicMock(return_value=SimpleNamespace(allowed=True))
        stack.enter_context(native_preflight(limit))
        yield SimpleNamespace(
            seen=seen,
            debits=debits,
            debit_calls=debit_calls,
            refund=refund,
            usage=usage,
            events=events,
            limit=limit,
            engine=engine,
        )


def admit(world):
    with transaction.atomic():
        state = stage_submission(
            world["experiment"],
            {},
            "create",
            authorized_workspace=world["dataset"].workspace,
        )
    with patch(START, return_value="pinned-native"):
        admit_submission(str(world["experiment"].id), state["admission_id"])


def score_refs(context):
    """The run's recorded evaluator outcomes, to say why nothing was built."""
    run = NativeRunBinding.objects.get(run_id=context["run_id"]).run
    return [item.score_refs for item in run.items.order_by("id")]


def test_native_run_builds_the_recorded_version(native_world):
    w = native_world
    w["experiment"].refresh_from_db()
    _metric, pin = attach_executable_native_eval(w)
    # Before admission, so the recorded definition includes it.
    pin.prompt_messages = PINNED_MESSAGES
    pin.save(update_fields=["prompt_messages"])
    admit(w)
    context, prepared = setup(w["experiment"])

    with native_engine() as run, upstream_output() as upstream:
        result = row(w["experiment"], context, prepared, prepared["row_ids"][0])

    upstream.call.assert_called_once()
    assert result["status"] == "COMPLETED", score_refs(context)
    assert run.seen, f"the native evaluator was never built: {score_refs(context)}"
    assert run.seen[0].get("rule_prompt") == PINNED_RUBRIC
    assert run.seen[0].get("system_prompt") == PINNED_SYSTEM


@pytest.mark.parametrize("lookup", ["no-key", "routing-as-shipped"])
def test_native_pinned_model_without_a_key_is_refused_before_any_call_or_debit(
    native_world, lookup
):
    """No stored key and no managed route: refused, nothing debited.

    "routing-as-shipped" runs the real key lookup with the managed routing
    port as the line ships it (none registered), so the pinned model follows
    the same routing rule as an unpinned evaluator naming it.
    """
    w = native_world
    w["experiment"].refresh_from_db()
    metric, pin = attach_executable_native_eval(w)
    config = {
        "eval_type_id": "CustomPromptEvaluator",
        "output": "Pass/Fail",
        "required_keys": ["input", "output"],
        "rule_prompt": LIVE_RUBRIC,
        "model": "turing_small",
    }
    pin.config_snapshot = dict(config)
    pin.model = "gpt-4o"
    pin.prompt_messages = PINNED_MESSAGES
    pin.save(update_fields=["config_snapshot", "model", "prompt_messages"])
    metric.template.config = dict(config)
    metric.template.save(update_fields=["config"])
    metric.model = "turing_small"
    metric.save(update_fields=["model"])
    admit(w)
    context, prepared = setup(w["experiment"])

    def no_key(model, *args, **kwargs):
        raise ValueError("No API key found for organization")

    key_lookup = (
        patch.object(instance, "_get_api_key", side_effect=no_key)
        if lookup == "no-key"
        else patch.object(instance, "_get_api_key", wraps=instance._get_api_key)
    )
    with (
        native_engine() as run,
        key_lookup as lookups,
        upstream_output() as upstream,
    ):
        row(w["experiment"], context, prepared, prepared["row_ids"][0])

    # The prompt step ran once; the evaluator step is what is under test.
    upstream.call.assert_called_once()
    # The key lookup for the pinned model is what refused it.
    asked = [call.args[0] for call in lookups.call_args_list]
    assert asked == ["gpt-4o"], score_refs(context)
    # Refused before the evaluator existed, so no judge call was possible.
    assert run.seen == []
    run.engine.run.assert_not_called()
    # Refused before the up-front debit: no debit, so nothing to refund, and
    # no usage emitted.
    assert run.debits == []
    run.refund.assert_not_called()
    run.usage.assert_not_called()


@pytest.mark.parametrize("case", ["unpinned", "pin-deleted-before-admission"])
def test_native_run_without_a_live_pin_keeps_the_card_model_and_live_prompt(
    native_world, case
):
    """Admission records the template default here; it must not run as a pin."""
    from model_hub.models.evals_metric import EvalTemplateVersion

    w = native_world
    w["experiment"].refresh_from_db()
    metric, default = attach_executable_native_eval(w)
    # The default version names another model and prompt than the live
    # template and the card.
    default.config_snapshot = dict(JUDGE_CONFIG)
    default.model = "turing_flash"
    default.prompt_messages = DEFAULT_MESSAGES
    default.save(update_fields=["config_snapshot", "model", "prompt_messages"])
    metric.template.config = dict(JUDGE_CONFIG)
    metric.template.save(update_fields=["config"])
    metric.model = "turing_small"
    if case == "unpinned":
        metric.pinned_version = None
    else:
        gone = EvalTemplateVersion.objects.create(
            eval_template=metric.template,
            version_number=2,
            is_default=False,
            organization=default.organization,
            workspace=default.workspace,
            config_snapshot=dict(JUDGE_CONFIG),
            prompt_messages=PINNED_MESSAGES,
            model="turing_flash",
        )
        metric.pinned_version = gone
    metric.save(update_fields=["model", "pinned_version"])
    if case != "unpinned":
        EvalTemplateVersion.all_objects.filter(pk=metric.pinned_version_id).update(
            deleted=True
        )
    admit(w)
    context, prepared = setup(w["experiment"])
    recorded = NativeRunBinding.objects.get(run_id=context["run_id"]).run
    assert [pin["evaluator_version_id"] for pin in recorded.evaluator_versions] == [
        str(default.id)
    ]

    with native_engine() as run, upstream_output() as upstream:
        row(w["experiment"], context, prepared, prepared["row_ids"][0])

    upstream.call.assert_called_once()
    assert run.seen, f"the native evaluator was never built: {score_refs(context)}"
    assert run.seen[0]["model"] == "turing_small"
    assert run.seen[0]["rule_prompt"] == LIVE_RUBRIC
    assert run.seen[0].get("system_prompt") in (None, "")


def _charge_world(w, *, card, version_model, pinned):
    """A NativeFixture evaluator (no key needed) whose card and default
    version name different tiers."""
    metric, version = attach_executable_native_eval(w)
    version.model = version_model
    version.save(update_fields=["model"])
    metric.model = card
    if not pinned:
        metric.pinned_version = None
    metric.save(update_fields=["model", "pinned_version"])
    return metric, version


def _charged_types(run):
    """The call types the row was limit-checked, debited and recorded under."""
    limit = [call.args[1] for call in run.limit.call_args_list]
    debit = [args[1] for args, _kwargs in run.debit_calls]
    event = [event.event_type for event in run.events]
    return limit, debit, event


@pytest.mark.parametrize(
    "card,version_model,expected",
    [
        (
            "turing_small",
            "protect_flash",
            APICallTypeChoices.PROTECT_FLASH_EVALUATOR.value,
        ),
        ("protect", "turing_large", APICallTypeChoices.TURING_LARGE_EVALUATOR.value),
    ],
    ids=["turing-card-pinned-protect", "protect-card-pinned-turing"],
)
def test_a_pinned_tier_is_limit_checked_debited_and_recorded_as_it_runs(
    native_world, card, version_model, expected
):
    w = native_world
    w["experiment"].refresh_from_db()
    _charge_world(w, card=card, version_model=version_model, pinned=True)
    admit(w)
    context, prepared = setup(w["experiment"])

    with native_engine(emit_usage=True) as run, upstream_output():
        row(w["experiment"], context, prepared, prepared["row_ids"][0])

    assert run.seen, f"the native evaluator was never built: {score_refs(context)}"
    assert _charged_types(run) == ([expected], [expected], [expected])


def test_an_unpinned_evaluator_is_charged_exactly_as_before(native_world):
    """The card's tier and the usual amount, though the recorded default
    version names another tier."""
    w = native_world
    w["experiment"].refresh_from_db()
    _charge_world(w, card="turing_flash", version_model="protect", pinned=False)
    admit(w)
    context, prepared = setup(w["experiment"])

    with native_engine(emit_usage=True) as run, upstream_output():
        row(w["experiment"], context, prepared, prepared["row_ids"][0])

    flash = APICallTypeChoices.TURING_FLASH_EVALUATOR.value
    assert run.seen, f"the native evaluator was never built: {score_refs(context)}"
    assert _charged_types(run) == ([flash], [flash], [flash])
    # No judge cost in this fixture: the amount is the per-run fee in credits.
    (event,) = run.events
    assert event.amount == Decimal("0.01") * 100


@pytest.mark.parametrize("admitted", ["pinned", "unpinned"])
def test_a_pin_changed_mid_run_keeps_what_admission_decided(native_world, admitted):
    """Whether the recorded version runs is decided at admission, not read
    from the metric's current pin each time a runner is built."""
    w = native_world
    w["experiment"].refresh_from_db()
    metric, version = attach_executable_native_eval(w)
    version.config_snapshot = dict(JUDGE_CONFIG)
    version.model = "turing_flash"
    version.prompt_messages = PINNED_MESSAGES
    version.save(update_fields=["config_snapshot", "model", "prompt_messages"])
    metric.template.config = dict(JUDGE_CONFIG)
    metric.template.save(update_fields=["config"])
    metric.model = "turing_small"
    if admitted == "unpinned":
        metric.pinned_version = None
    metric.save(update_fields=["model", "pinned_version"])
    admit(w)
    context, prepared = setup(w["experiment"])
    # Mid-run, the pin flips the other way.
    metric.pinned_version = None if admitted == "pinned" else version
    metric.save(update_fields=["pinned_version"])

    with native_engine() as run, upstream_output():
        row(w["experiment"], context, prepared, prepared["row_ids"][0])

    assert run.seen, f"the native evaluator was never built: {score_refs(context)}"
    if admitted == "pinned":
        assert run.seen[0]["model"] == "turing_flash"
        assert run.seen[0]["rule_prompt"] == PINNED_RUBRIC
    else:
        assert run.seen[0]["model"] == "turing_small"
        assert run.seen[0]["rule_prompt"] == LIVE_RUBRIC
