"""Every Jev version the decision catalog offers runs as Jev, alike.

jev-1.14.0 (the catalog's typesafe/jev-1.14) is accepted when a binding is
saved, runs on the Jev evaluator with the organization's TypeSafe key behind
the same plan check, and its answer is recorded once by the Jev meter, exactly
as jev-1.13.0. Only the EE boundary is stubbed: the TypeSafe key lookup, the
plan check, the ground truth read and the meter's emitters.
"""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

pytest.importorskip("ee.evals.llm.jev_evaluator.evaluator")
pytest.importorskip("ee.jev.mapping")
pytest.importorskip("ee.jev.metering")
pytest.importorskip("ee.usage.services.emitter")

from ee.evals.llm.jev_evaluator.evaluator import JevEvaluator  # noqa: E402
from ee.jev.mapping import JevMappingError  # noqa: E402
from ee.usage.schemas.event_types import BillingEventType  # noqa: E402
from evaluations.decision_models import get_decision_model  # noqa: E402
from evaluations.engine.instance import create_eval_instance  # noqa: E402
from model_hub.services.ground_truth_service import GroundTruthService  # noqa: E402
from model_hub.utils.jev_templates import validate_jev_binding  # noqa: E402
from model_hub.views.eval_runner import EvaluationRunner  # noqa: E402
from sdk.utils.helpers import (  # noqa: E402
    _get_api_call_type,
    _get_billing_event_type,
)
from tfc.constants.api_calls import APICallTypeChoices  # noqa: E402

pytestmark = pytest.mark.requires_ee

JEV_VERSIONS = ["jev-1.13.0", "jev-1.14.0"]
ORG_KEY = "org-typesafe-key"
METER = "ee.jev.metering.emit_usage_events"
EMIT = "ee.usage.services.emitter.emit"
EVENT = "ee.usage.schemas.events.UsageEvent"


@pytest.fixture(autouse=True)
def typesafe_key():
    """Jev is bring your own key: the run looks up the org's TypeSafe key."""
    with patch(
        "evaluations.engine.instance._get_typesafe_api_key", return_value=ORG_KEY
    ) as lookup:
        yield lookup


@pytest.fixture(autouse=True)
def meter_config():
    config = Mock()
    config.get_eval_per_run_fee.return_value = 0.0005
    config.calculate_ai_credits.side_effect = lambda usd: usd * 100
    with patch("ee.usage.services.config.BillingConfig.get", return_value=config):
        yield config


def template(model):
    return SimpleNamespace(
        id="template-1",
        config={
            "eval_type_id": "CustomPromptEvaluator",
            "model": model,
            "rule_prompt": "Judge {{input}}",
            "output": "Pass/Fail",
            "required_keys": ["input"],
        },
        model=model,
        organization=SimpleNamespace(id="trusted-org"),
        organization_id="trusted-org",
        workspace=None,
        workspace_id=None,
        eval_type="llm",
        choice_scores=None,
        choices=[],
        pass_threshold=0.5,
        multi_choice=False,
    )


def build(model):
    """The evaluator a run of a template on ``model`` gets, with the plan
    check recorded and no other provider's key reachable."""
    with (
        patch("evaluations.engine.instance.resolve_version", return_value=None),
        patch(
            "evaluations.engine.instance._get_api_key",
            side_effect=AssertionError("a chat provider key was looked up"),
        ),
        patch(
            "evaluations.engine.instance._get_decision_api_key",
            side_effect=AssertionError("another decision provider key was used"),
        ),
        patch("tfc.capabilities.service.check_or_raise") as plan_check,
    ):
        instance, _ = create_eval_instance(Mock(), template(model), model=None)
    return instance, plan_check


def runner(model):
    run = EvaluationRunner.__new__(EvaluationRunner)
    run.user_eval_metric = SimpleNamespace(
        id="metric-1",
        model=model,
        template=SimpleNamespace(id="template-1"),
        organization=SimpleNamespace(id="org-1"),
    )
    run.organization_id = "org-1"
    run.eval_template = SimpleNamespace(eval_type="llm")
    run.version_number = None
    run.source = "dataset"
    run.source_id = "dataset-1"
    run.replace_column_id = "column-1"
    return run


@pytest.mark.parametrize("model", JEV_VERSIONS)
def test_the_catalog_offers_the_version_on_the_typesafe_connection(model):
    offered = get_decision_model(model)

    assert offered is not None
    assert offered.connection == "typesafe"
    assert offered.author == "TypeSafe"


@pytest.mark.parametrize("model", JEV_VERSIONS)
def test_a_binding_on_the_version_is_accepted_when_saved(model):
    with (
        patch("tfc.ee_gates._jev_denied_off_cloud", return_value=False),
        patch.object(GroundTruthService, "load_active_gt", return_value=None),
    ):
        refused = validate_jev_binding(template(model), model=model)

    assert refused is None


def test_a_jev_id_the_catalog_does_not_offer_is_still_refused_when_saved():
    with patch("tfc.ee_gates._jev_denied_off_cloud", return_value=False):
        refused = validate_jev_binding(template("jev-9.9.9"), model="jev-9.9.9")

    assert refused is not None
    assert refused.status_code == 400
    assert "JEV_MODEL_UNKNOWN" in str(refused.data)


@pytest.mark.parametrize("model", JEV_VERSIONS)
def test_a_run_on_the_version_is_the_jev_evaluator_on_the_typesafe_key(
    model, typesafe_key
):
    instance, plan_check = build(model)

    assert isinstance(instance, JevEvaluator)
    assert instance._model == model
    assert instance.provider == "typesafe"
    # The org's key reaches only the TypeSafe client, never the config.
    assert instance.api_key is None
    assert instance.client.api_key == ORG_KEY
    assert "api_key" not in instance._jev_config
    typesafe_key.assert_called_once_with("trusted-org", None)
    plan_check.assert_called_once_with("jev_models", org_id="trusted-org")


@pytest.mark.parametrize("model", JEV_VERSIONS)
def test_a_plan_without_jev_refuses_the_version_before_the_key_lookup(
    model, typesafe_key
):
    with (
        patch("evaluations.engine.instance.resolve_version", return_value=None),
        patch(
            "tfc.capabilities.service.check_or_raise",
            side_effect=PermissionError("denied"),
        ),
        patch("ee.jev.client.JevSystemOneClient.evaluate") as client,
    ):
        with pytest.raises(PermissionError):
            create_eval_instance(Mock(), template(model), model=None)

    typesafe_key.assert_not_called()
    client.assert_not_called()


def test_an_unoffered_jev_id_is_still_refused_when_run():
    with (
        patch("evaluations.engine.instance.resolve_version", return_value=None),
        patch("ee.jev.client.JevSystemOneClient.evaluate") as client,
    ):
        with pytest.raises(JevMappingError, match="JEV_MODEL_UNKNOWN"):
            create_eval_instance(Mock(), template("jev-9.9.9"), model=None)

    client.assert_not_called()


@pytest.mark.parametrize("model", JEV_VERSIONS)
def test_a_run_on_the_version_is_logged_and_recorded_as_a_jev_run(model):
    logged_as = APICallTypeChoices.TURING_LARGE_EVALUATOR.value

    assert _get_api_call_type(model) == APICallTypeChoices.JEV_EVALUATOR.value
    assert (
        _get_billing_event_type(logged_as, model)
        == BillingEventType.JEV_EVALUATOR.value
    )


@pytest.mark.parametrize("model", JEV_VERSIONS)
def test_the_answer_goes_to_the_jev_meter_once_and_not_to_the_usage_emitter(
    model,
):
    instance, _ = build(model)
    log = SimpleNamespace(log_id="log-1")

    with (
        patch.object(JevEvaluator, "cost", {"total_cost": 0.0}, create=True),
        patch.object(JevEvaluator, "token_usage", {}, create=True),
        patch(METER) as meter,
        patch(EMIT) as emit,
        patch(EVENT) as event,
    ):
        runner(model)._emit_cell_usage(instance, log, SimpleNamespace(id="row-1"))

    meter.assert_called_once_with(instance, org_id="org-1", cell_or_run_id="log-1")
    event.assert_not_called()
    emit.assert_not_called()
