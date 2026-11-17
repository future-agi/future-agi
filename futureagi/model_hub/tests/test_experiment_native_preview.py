import json
import sys
import uuid
from copy import deepcopy
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest
from django.db import connection
from rest_framework.test import APIRequestFactory, force_authenticate

from agentic_eval.core.embeddings import embedding_manager
from evaluations.engine import registry
from model_hub.models.develop_dataset import Cell, Column
from model_hub.models.evals_metric import EvalTemplateVersion, UserEvalMetric
from model_hub.models.native_experiment_runs import NativeRunBinding
from model_hub.tests.test_native_run_binding import (
    add_native_eval,
    bind,
    native_world,  # noqa: F401
)
from model_hub.views import eval_runner
from model_hub.views.separate_evals import EvalPlayGroundAPIView

pytestmark = pytest.mark.django_db


def post(world, body):
    request = APIRequestFactory().post(
        "/model-hub/eval-playground/", body, format="json"
    )
    request.organization = world["dataset"].organization
    request.workspace = world["dataset"].workspace
    force_authenticate(request, user=world["created_by"])
    return EvalPlayGroundAPIView.as_view()(request)


@pytest.fixture(params=[("text", "candidate"), ("float", 1.25), ("images", ["a", "b"])])
def prepared(native_world, request):  # noqa: F811
    w = native_world
    output_type, output = request.param
    w["output_column"].data_type = output_type
    w["output_column"].save()
    metric, pin = add_native_eval(w)
    template = metric.template
    template.config = {
        "eval_type_id": "CustomPromptEvaluator",
        "output": "choices",
        "required_keys": ["input", "output"],
        "multi_choice": False,
        "choices": ["Live"],
        "rule_prompt": "Live {{input}}",
    }
    template.choice_scores = {"Live": 0.9}
    template.pass_threshold = 0.9
    template.save()
    pin.config_snapshot = {
        **template.config,
        "multi_choice": True,
        "choices": ["Saved", "Other"],
        "rule_prompt": "Saved {{input}}",
    }
    pin.choice_scores = {"Saved": 0.25, "Other": 0.75}
    pin.output_type_normalized = "choices"
    pin.pass_threshold = 0.4
    pin.model = "turing_flash"
    pin.is_default = False
    pin.save()
    EvalTemplateVersion.objects.create(
        eval_template=template,
        version_number=2,
        is_default=True,
        organization=template.organization,
        workspace=template.workspace,
        config_snapshot=template.config,
        choice_scores={"Live": 0.9},
        pass_threshold=0.9,
        model="turing_large",
    )
    metric.config = {
        "mapping": {"input": str(w["source_column"].id), "output": "output"},
        "config": {},
        "version_pin": "v1",
        "reason_column": True,
    }
    metric.model = "turing_large"
    metric.save()
    run = bind(w)
    binding = NativeRunBinding.objects.get(run=run)
    links = binding.snapshot.examples.select_related("version_item")
    link = links.order_by("row_order").first()
    body = {
        "experiment_preview": {
            "dataset_id": str(w["dataset"].id),
            "dataset_version_id": str(run.dataset_version_id),
            "version_item_id": str(link.version_item_id),
            "evaluator": {
                "template_id": str(template.id),
                "name": metric.name,
                "config": deepcopy(metric.config),
                "model": "turing_large",
            },
            "output": output,
            "output_type": output_type,
            "output_name": w["output_column"].name,
        }
    }
    return w, metric, pin, run, binding, link, body


@pytest.fixture
def feedback_transport(monkeypatch):
    # Keep the real manager lifecycle; only vector I/O is replaced.
    client = SimpleNamespace(close=Mock())
    monkeypatch.setattr(embedding_manager, "ClickHouseVectorDB", lambda: client)
    retrieve = Mock(return_value=[])
    monkeypatch.setattr(
        embedding_manager.EmbeddingManager, "retrieve_avg_rag_based_examples", retrieve
    )
    return client, retrieve


@pytest.fixture
def transports(monkeypatch, feedback_transport):
    constructed, inputs, logs, events = [], [], [], []

    class Evaluator:
        cost = {}
        token_usage = {}

        def __init__(self, **config):
            constructed.append(config)
            self.config = config

        def run(self, **values):
            inputs.append(values)
            return SimpleNamespace(
                eval_results=[
                    {
                        "data": values
                        if "limit" in self.config
                        else ["Saved", "Other"],
                        "failure": False,
                        "reason": "saved evidence",
                    }
                ]
            )

    def log(*args, **kwargs):
        logs.append((args, kwargs))
        return SimpleNamespace(
            config=json.dumps(kwargs["config"], default=str),
            log_id=uuid.uuid4(),
            status=eval_runner.APICallStatusChoices.PROCESSING.value,
            save=Mock(),
        )

    registry._build_registry()
    monkeypatch.setitem(registry._REGISTRY, "CustomPromptEvaluator", Evaluator)
    monkeypatch.setitem(registry._REGISTRY, "PreviewFunction", Evaluator)
    monkeypatch.setattr(eval_runner, "log_and_deduct_cost_for_api_request", log)
    monkeypatch.setattr(eval_runner, "refund_cost_for_api_call", Mock())
    check = Mock(return_value=SimpleNamespace(allowed=True))
    modules = {
        "services.metering": {"check_usage": check},
        "services.emitter": {"emit": events.append},
        "services.config": {"BillingConfig": SimpleNamespace(get=lambda: None)},
        "schemas.events": {"UsageEvent": lambda **kw: SimpleNamespace(**kw)},
        "schemas.event_types": {
            "BillingEventType": SimpleNamespace(
                CODE_EVALUATOR=SimpleNamespace(value="code_evaluator")
            )
        },
        "utils.event_properties": {"token_usage_properties": lambda _: {}},
    }
    for name, attrs in modules.items():
        module = ModuleType("ee.usage." + name)
        module.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, module.__name__, module)
    return constructed, inputs, logs, events, check


@pytest.mark.parametrize(
    "mode",
    [
        "choices",
        "function",
        "preflight",
        "failure",
        "prompt",
        "unlogged",
        "unlogged_entry",
        "unlogged_template",
    ],
)
@pytest.mark.parametrize("saved_choices", [False, True])
def test_preview_matches_native_row(
    prepared, transports, mode, saved_choices, settings, monkeypatch
):
    w, metric, pin, run, binding, link, body = prepared
    settings.EVAL_PINNED_VERSION_SAVED_CHOICES_ENABLED = saved_choices
    function = mode == "function"
    entry = body["experiment_preview"]["evaluator"]
    diagnostics = mode in ("unlogged_entry", "unlogged_template")
    if mode.startswith("unlogged"):
        monkeypatch.setattr(eval_runner, "log_and_deduct_cost_for_api_request", None)
        assert entry["model"] == "turing_large"
    if diagnostics:
        metric.error_localizer = mode == "unlogged_entry"
        metric.save(update_fields=["error_localizer"])
        pin.error_localizer_enabled = mode == "unlogged_template"
        pin.save(update_fields=["error_localizer_enabled"])
        entry["error_localizer"] = metric.error_localizer
        column_diagnostics = Mock()
        monkeypatch.setattr(
            "model_hub.tasks.user_evaluation.trigger_error_localization_for_column",
            column_diagnostics,
        )
    if mode == "preflight":
        transports[4].return_value.allowed = False
    if mode == "failure":
        monkeypatch.setattr(
            registry._REGISTRY["CustomPromptEvaluator"],
            "run",
            Mock(side_effect=ValueError("No input received")),
        )
    if mode == "prompt":
        metric.config["mapping"]["input"] = "prompt_chain"
        metric.save()
        body["experiment_preview"]["evaluator"]["config"] = metric.config
    if function:
        for target, default in ((metric.template, 2), (pin, 1)):
            config = {
                "eval_type_id": "PreviewFunction",
                "function_eval": True,
                "output": "Pass/Fail",
                "required_keys": ["input", "output"],
                "function_params_schema": {
                    "limit": {"type": "integer", "default": default}
                },
            }
            field = "config_snapshot" if target is pin else "config"
            setattr(target, field, config)
            target.save(update_fields=[field])
    if function or mode == "prompt" or diagnostics:
        # Admit the changed definition, not a stale native hash.
        w["execution_id"] = uuid.uuid4()
        run = bind(w)
        binding = NativeRunBinding.objects.get(run=run)
    native = eval_runner.NativeEvaluationRunner(
        dataset=w["dataset"], run=run, binding=binding, pin=run.evaluator_versions[0]
    )
    models = (Cell, Column, UserEvalMetric)
    before = [model.objects.count() for model in models]
    Cell.objects.filter(dataset=w["dataset"]).update(value="changed live input")
    cells_before = list(Cell.objects.order_by("id").values())
    response = post(w, body)
    assert response.status_code == 200, response.data
    assert connection.closed_in_transaction is False
    preview = response.data["result"]
    assert before == [model.objects.count() for model in models]
    assert list(Cell.objects.order_by("id").values()) == cells_before
    if diagnostics:
        assert column_diagnostics.call_count == 0
        assert preview["metadata"]["diagnostics"] == {
            "status": "unavailable",
            "reason": "evaluation_log_unavailable",
        }
    source = w["source_column"]
    assessment = native.evaluate_native_row(
        link.snapshot_row_id,
        values=link.version_item.input["values"],
        columns=[
            {
                "source_id": str(source.id),
                "snapshot_id": binding.snapshot.column_map[str(source.id)],
                "name": source.name,
                "data_type": source.data_type,
            }
        ],
        output=body["experiment_preview"]["output"],
        messages=None,
    )
    assert {
        **preview["metadata"]["assessment"],
        "metric_id": str(metric.id),
    } == assessment
    if diagnostics:
        assert column_diagnostics.call_count == 1
        assert column_diagnostics.call_args.kwargs["log_id"] is None
    result = Cell.objects.get(
        row=link.snapshot_row, column=native._native_result_column()
    )
    assert json.loads(result.value_infos)["reason"] == preview["reason"]
    if mode in ("preflight", "failure", "prompt"):
        assert assessment["status"] == ("not_assessed" if mode == "prompt" else "error")
        assert assessment["reason"] == (
            "prompt_chain_unavailable"
            if mode == "prompt"
            else "evaluator_execution_failed"
        )
        return
    expected = "Passed" if function else {"score": 0.5, "choices": ["Saved", "Other"]}
    assert preview["output"] == expected
    assert assessment["value"] == (1 if function else 0.5)
    assert assessment["status"] == "assessed"
    assert preview["reason"] == "saved evidence"
    assert result.value == Cell._meta.get_field("value").to_python(expected)
    built, inputs, logs, events, check = transports
    assert built[0] == built[1]
    expected_inputs = {
        "input": "input-0",
        "output": body["experiment_preview"]["output"],
    }
    if not function:
        expected_inputs.update(
            few_shots=[],
            required_keys=["input", "output"],
            optional_keys=["input", "output"],
        )
    assert inputs[0] == inputs[1] == expected_inputs
    if function:
        assert built == [{"limit": 1}, {"limit": 1}]
    else:
        assert built[0]["model"] == "turing_flash"
        assert built[0]["pass_threshold"] == 0.4
        assert built[0]["choices"] == ["Saved", "Other"]
        assert built[0]["multi_choice"] is True
    assert preview["model"] == native._charged_model() == "turing_flash"
    if mode.startswith("unlogged"):
        assert logs == []
        assert preview["log_id"] is None
        assert len(events) == check.call_count == 2
        assert events[0].event_type == events[1].event_type == check.call_args.args[1]
        return
    assert len(logs) == len(events) == check.call_count == 2
    assert logs[0][0][1] == logs[1][0][1] == check.call_args.args[1]
    assert logs[0][1]["config"]["version_number"] == 1
    assert events[0].event_type == events[1].event_type == logs[0][0][1]


@pytest.mark.parametrize("failure", [None, "retrieve", "close"])
def test_feedback_cleanup_preserves_the_caller_transaction(
    prepared, feedback_transport, failure
):
    w, metric, _, run, binding, _, _ = prepared
    client, retrieve = feedback_transport
    if failure == "retrieve":
        retrieve.side_effect = RuntimeError("Feedback unavailable")
    elif failure == "close":
        client.close.side_effect = RuntimeError("Feedback client unavailable")
    runner = eval_runner.NativeEvaluationRunner(
        dataset=w["dataset"], run=run, binding=binding, pin=run.evaluator_versions[0]
    )
    assert runner.get_few_shot_examples(["input-0"], ["input"]) == []
    assert retrieve.call_count == client.close.call_count == 1
    assert connection.closed_in_transaction is False
    assert UserEvalMetric.objects.get(pk=metric.pk).pk == metric.pk


@pytest.mark.parametrize(
    "invalid",
    [
        "item",
        "version",
        "pin",
        "deleted",
        "foreign",
        "mixed",
        "deleted_item",
        "foreign_item",
    ],
)
def test_preview_refuses_unavailable_references(prepared, transports, invalid):
    w, metric, pin, _, _, link, body = prepared
    data = body["experiment_preview"]
    if invalid in ("item", "version"):
        field = "version_item_id" if invalid == "item" else "dataset_version_id"
        data[field] = str(uuid.uuid4())
    elif invalid == "deleted_item":
        link.version_item.delete()
    elif invalid == "foreign_item":
        # A different dataset's item cannot be read through the requested dataset.
        from accounts.models.organization import Organization
        from accounts.models.workspace import Workspace
        from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem
        from model_hub.models.develop_dataset import Dataset, Row

        organization = Organization.objects.create(name="Other")
        workspace = Workspace.objects.create(
            name="Other", organization=organization, created_by=w["created_by"]
        )
        other = Dataset.objects.create(
            name="Other source", organization=organization, workspace=workspace
        )
        row = Row.objects.create(dataset=other, order=10)
        version = DatasetVersion.objects.create(dataset=other)
        item = DatasetVersionItem.objects.create(
            dataset_version=version, input=link.version_item.input, legacy_row=row
        )
        data["dataset_version_id"] = str(version.id)
        data["version_item_id"] = str(item.id)
    elif invalid == "pin":
        data["evaluator"]["config"]["version_pin"] = "v999"
    elif invalid == "deleted":
        EvalTemplateVersion.no_workspace_objects.filter(pk=pin.pk).update(deleted=True)
    elif invalid == "foreign":
        other, other_pin = add_native_eval(w)
        data["evaluator"]["config"]["version_pin"] = str(other_pin.pk)
    else:
        body["template_id"] = str(metric.template_id)
    response = post(w, body)
    assert response.status_code == 400
    if invalid == "foreign_item":
        assert response.data["message"] == (
            "Saved version item is unavailable in this dataset."
        )
    assert transports[0] == transports[2] == transports[3] == []
    assert transports[4].call_count == 0
