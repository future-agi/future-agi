"""Playground response shape stays canonical whether or not the metering helper is loaded (TH-6719)."""

from types import SimpleNamespace

import pytest

from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate
from model_hub.views.utils import evals as evals_module
from model_hub.views.utils.evals import run_eval_func
from tfc.billing.boundary import _NoopBilling
from tfc.constants.api_calls import APICallStatusChoices


class _MeteringBilling(_NoopBilling):
    """Billing seam with metering available: ``log_and_deduct`` returns a live
    APICallLog-shaped row (the EE behavior), everything else stays no-op.

    Subclassing _NoopBilling keeps the fake honest — any new Billing method a
    future run_eval_func change calls resolves to the real OSS no-op instead
    of a MagicMock that silently returns truthy garbage.
    """

    has_ee_billing = True

    def __init__(self, log_row):
        self._log_row = log_row

    def log_and_deduct(self, **_kwargs):
        return self._log_row


_CANONICAL_KEYS = {
    "output",
    "reason",
    "model",
    "metadata",
    "output_type",
    "log_id",
}


class _FakeBatchResult:
    def __init__(self, results):
        self.eval_results = results


class _FakeEvalInstance:
    def __init__(self):
        self.cost = {"total_cost": 0.0}
        self.token_usage = {}

    def run(self, **_kwargs):
        return _FakeBatchResult(
            [
                {
                    "data": {"result": "Pass"},
                    "failure": False,
                    "reason": "not toxic",
                    "runtime": 42,
                    "model": "gpt-4.1",
                    "metrics": [{"id": "custom_eval_score", "value": "Pass"}],
                    "metadata": {},
                }
            ]
        )


@pytest.fixture
def pass_fail_template(organization, workspace):
    return EvalTemplate.no_workspace_objects.create(
        name="toxicity-shape-fixture",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={
            "output": "Pass/Fail",
            "eval_type_id": "CustomPromptEvaluator",
            "required_keys": ["input"],
            "template_format": "mustache",
        },
        eval_tags=["llm"],
        criteria="Is {{input}} toxic?",
        model="turing_large",
        visible_ui=True,
        output_type_normalized="pass_fail",
        pass_threshold=0.5,
    )


def _patch_runner(monkeypatch, format_output_return):
    monkeypatch.setattr(
        "model_hub.views.utils.evals.EvaluationRunner._create_eval_instance",
        lambda *_args, **_kwargs: _FakeEvalInstance(),
    )
    monkeypatch.setattr(
        "model_hub.views.utils.evals.EvaluationRunner.map_fields",
        lambda *_args, **_kwargs: {"input": "hello"},
    )
    monkeypatch.setattr(
        "model_hub.views.utils.evals.EvaluationRunner.format_output",
        lambda *_args, **_kwargs: format_output_return,
    )
    # Ground-truth preview call talks to CH; short-circuit it.
    # resolve_preview_examples returns None when GT is not configured on the
    # template; the canonical response omits the key in that case.
    monkeypatch.setattr(
        "model_hub.views.utils.evals.GroundTruthService.resolve_preview_examples",
        lambda **_kwargs: None,
    )


@pytest.mark.django_db
def test_response_shape_is_canonical_when_metering_helper_absent(
    monkeypatch, pass_fail_template, organization
):
    _patch_runner(monkeypatch, format_output_return="Passed")
    # Simulate the OSS build: the billing boundary hands out _NoopBilling,
    # so log_and_deduct returns None and no APICallLog row exists.
    monkeypatch.setattr(evals_module, "get_billing", lambda: _NoopBilling())

    output = run_eval_func(
        {"config": {}, "params": {}},
        {"input": "hello"},
        pass_fail_template,
        organization,
        source="eval_playground",
    )

    assert set(output.keys()) >= _CANONICAL_KEYS
    assert output["output"] == "Passed", (
        "Canonical verdict from format_output must land in `output` "
        "regardless of whether an APICallLog row was created."
    )
    assert output["output_type"] == "Pass/Fail"
    assert output["log_id"] is None
    assert "ground_truth_examples" not in output
    assert output["reason"] == "not toxic"
    assert output["model"] == "gpt-4.1"
    # Old shape's fields must not leak through as top-level keys.
    for stale_key in (
        "data",
        "failure",
        "runtime",
        "metrics",
        "start_time",
        "end_time",
        "duration",
    ):
        assert stale_key not in output, (
            f"legacy raw-response key {stale_key!r} leaked into canonical output"
        )


@pytest.mark.django_db
def test_response_shape_is_canonical_when_metering_is_available(
    monkeypatch, pass_fail_template, organization
):
    _patch_runner(monkeypatch, format_output_return="Failed")

    log_row = SimpleNamespace(
        log_id="log-abc",
        config="{}",
        status=APICallStatusChoices.PROCESSING.value,
        input_token_count=0,
        save=lambda *a, **k: None,
    )
    monkeypatch.setattr(
        evals_module, "get_billing", lambda: _MeteringBilling(log_row)
    )

    output = run_eval_func(
        {"config": {}, "params": {}},
        {"input": "hello"},
        pass_fail_template,
        organization,
        source="eval_playground",
    )

    assert set(output.keys()) >= _CANONICAL_KEYS
    assert output["output"] == "Failed"
    assert output["output_type"] == "Pass/Fail"
    assert output["log_id"] == "log-abc"
    assert "ground_truth_examples" not in output


@pytest.mark.parametrize(
    "value",
    [
        0.7,
        "Good",
        ["A", "B"],
        {"score": 0.7, "choice": "Good"},
        {"score": 0.5, "choices": ["A", "B"]},
    ],
    ids=["float", "str-choice", "list-multi", "dict-single-pick", "dict-multi-choice"],
)
def test_response_output_preserves_polymorphic_shapes(
    monkeypatch, pass_fail_template, organization, value
):
    _patch_runner(monkeypatch, format_output_return=value)
    monkeypatch.setattr(evals_module, "get_billing", lambda: _NoopBilling())

    output = run_eval_func(
        {"config": {}, "params": {}},
        {"input": "hello"},
        pass_fail_template,
        organization,
        source="eval_playground",
    )
    assert output["output"] == value
