"""Saved-version playground regressions; all service/ORM/provider seams are fake.

The real engine, runner construction method and playground function are used.
AST extraction avoids importing the view's unrelated Django/EE dependencies;
it does not replace any selection, execution or metering logic under test.
"""

import ast
import json
import sys
import time
from decimal import Decimal
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from evaluations.engine import instance as engine

ROOT = Path(__file__).resolve().parents[3]


def _module(monkeypatch, name, **members):
    module = ModuleType(name)
    module.__dict__.update(members)
    monkeypatch.setitem(sys.modules, name, module)
    return module


def _function(path, name, namespace, class_name=None):
    tree = ast.parse(path.read_text())
    body = tree.body
    if class_name:
        body = next(node for node in body if isinstance(node, ast.ClassDef)
                    and node.name == class_name).body
    function = next(node for node in body if isinstance(node, ast.FunctionDef)
                    and node.name == name)
    function.decorator_list = []
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


@pytest.fixture
def harness(monkeypatch):
    org = SimpleNamespace(id="organization-1")
    workspace = SimpleNamespace(id="workspace-1")
    choices = SimpleNamespace(**{
        name: SimpleNamespace(value=value)
        for name, value in {
            "TURING_LARGE": "turing_large", "TURING_SMALL": "turing_small",
            "TURING_FLASH": "turing_flash",
        }.items()
    })
    template = SimpleNamespace(
        id="eval-1", name="Saved judge", organization=org, workspace=workspace,
        model="turing_flash", eval_type="llm", owner="user",
        pass_threshold=0.5, choices=[], choice_scores={}, multi_choice=False,
        config={"eval_type_id": "CustomPromptEvaluator", "model": "turing_flash",
                "provider": "turing", "output": "score", "rule_prompt": "Judge {{text}}"},
    )
    version = SimpleNamespace(
        id="version-1", version_number=1, model="turing_large",
        pass_threshold=0.5, prompt_messages=[],
    )
    resolve = Mock(return_value=version)
    monkeypatch.setattr(engine, "resolve_version", resolve)
    credentials = Mock(side_effect=lambda model, *args, **kwargs: f"fixture-for-{model}")
    monkeypatch.setattr(engine, "_get_api_key", credentials)
    _module(monkeypatch, "model_hub.models.choices", ModelChoices=choices)
    _module(monkeypatch, "accounts.models.organization", Organization=SimpleNamespace(
        objects=SimpleNamespace(filter=lambda **kwargs: SimpleNamespace(first=lambda: org))))
    _module(monkeypatch, "model_hub.utils.function_eval_params",
            has_function_params_schema=lambda config: False,
            normalize_eval_runtime_config=lambda config, runtime: runtime)
    _module(monkeypatch, "model_hub.utils.prompt_migration",
            prompt_messages_to_flat_config=lambda messages: {})
    _module(monkeypatch, "evaluations.engine.version_choices",
            version_choice_config=lambda *args: {})
    _module(monkeypatch, "agentic_eval.core_evals.fi_evals.eval_type",
            is_function_eval=lambda name: False)
    _module(monkeypatch, "model_hub.utils.llm_providers",
            get_provider_for_model=lambda model, *args: f"provider-for-{model}")
    _module(monkeypatch, "model_hub.services.model_route",
            is_managed_route_binding=lambda credential: False)
    _module(monkeypatch, "tfc.ee_gates", is_jev_model=lambda model: False,
            jev_error_message=lambda error: None)

    instances, calls, events, charges = [], [], [], []
    prices = {"turing_large": Decimal("0.20"), "turing_flash": Decimal("0.02"),
              "workspace-judge": Decimal("0.04")}

    class Judge:
        def __init__(self, **config):
            self.config = config
            self.model = config["model"]
            self.cost = {"total_cost": Decimal("0")}
            self.token_usage = {"prompt_tokens": 10, "completion_tokens": 2}
            instances.append(self)

        def run(self, **inputs):
            calls.append({"model": self.model, "inputs": inputs})
            self.cost = {"total_cost": prices[self.model]}
            return SimpleNamespace(eval_results=[{
                "data": 0.9, "failure": False, "reason": "fixture explanation",
                "runtime": 1, "model": self.model, "metrics": [], "metadata": {},
            }])

    _module(monkeypatch, "evaluations.engine.registry", get_eval_class=lambda name: Judge)
    method = _function(ROOT / "model_hub/views/eval_runner.py", "_create_eval_instance",
                       {"ModelChoices": choices}, "EvaluationRunner")

    class Runner:
        _create_eval_instance = method

        def __init__(self, *args, **kwargs):
            self.source = kwargs.get("source", "eval_playground")
            self.user_eval_metric_id = kwargs.get("user_eval_metric_id")
            self.organization_id = kwargs.get("organization_id")
            self.workspace_id = kwargs.get("workspace_id")
            self.eval_template = template
            self.eval_class = Judge
            self.user_eval_metric = None
            self.version_number = None
            self.futureagi_eval = False
            self.criteria = None

        def _saved_choice_version(self):
            return None

        def map_fields(self, required_field, mapping, **kwargs):
            return dict(zip(required_field, mapping))

        def format_output(self, result_data, **kwargs):
            return result_data["data"]

    statuses = SimpleNamespace(**{
        key: SimpleNamespace(value=key.lower()) for key in ("PROCESSING", "SUCCESS", "ERROR")
    })
    log = SimpleNamespace(log_id="log-1", status="processing", config="{}", save=Mock())

    def charge(**kwargs):
        charges.append(kwargs)
        log.config = json.dumps(kwargs["config"])
        return log

    billing = SimpleNamespace(get_eval_per_run_fee=lambda: Decimal("0.01"),
                              calculate_ai_credits=lambda cost: cost * 100)
    _module(monkeypatch, "ee.usage.schemas.event_types",
            BillingEventType=SimpleNamespace(CODE_EVALUATOR=SimpleNamespace(value="code")))
    _module(monkeypatch, "ee.usage.schemas.events", UsageEvent=SimpleNamespace)
    _module(monkeypatch, "ee.usage.services.config",
            BillingConfig=SimpleNamespace(get=lambda: billing))
    _module(monkeypatch, "ee.usage.services.emitter", emit=events.append)
    _module(monkeypatch, "ee.usage.services.metering",
            check_usage=lambda *args: SimpleNamespace(allowed=True))
    _module(monkeypatch, "ee.usage.exceptions", UsageLimitExceeded=RuntimeError)
    _module(monkeypatch, "ee.usage.utils.event_properties",
            token_usage_properties=lambda usage: {"token_usage": usage})
    _module(monkeypatch, "agentic_eval.core_evals.fi_utils.token_count_helper",
            calculate_total_cost=lambda model, usage: {"total_cost": prices[model]})
    _module(monkeypatch, "model_hub.utils.eval_input_validation",
            validate_eval_inputs=lambda template, inputs: (None, inputs))
    _module(monkeypatch, "tfc.capabilities.service", is_configured=lambda: True,
            check=lambda *args, **kwargs: SimpleNamespace(allowed=True))
    namespace = {
        "json": json, "time": time, "Decimal": Decimal, "logger": Mock(),
        "ModelChoices": choices, "FUTUREAGI_EVAL_TYPES": set(), "EvaluationRunner": Runner,
        "EvalTemplateVersion": SimpleNamespace(objects=SimpleNamespace(get_default=lambda t: version)),
        "APICallStatusChoices": statuses, "log_and_deduct_cost_for_api_request": charge,
        "_get_api_call_type": lambda model, **kwargs: f"eval:{model}",
        "_get_billing_event_type": lambda call_type, model, **kwargs: call_type,
        "_emit_jev_usage": lambda *args, **kwargs: False,
        "GroundTruthService": SimpleNamespace(inject_context=lambda *args, **kwargs: None,
                                              resolve_preview_examples=lambda **kwargs: None),
        "_canonical_playground_output": lambda value, response, template, log: response,
    }
    run = _function(ROOT / "model_hub/views/utils/evals.py", "run_eval_func", namespace)
    return SimpleNamespace(**locals())


@pytest.mark.parametrize("selected,saved", [
    ("turing_flash", "turing_large"), ("turing_large", "turing_flash"),
    ("workspace-judge", "turing_large"),
])
def test_playground_selected_model_drives_call_and_both_meters(harness, selected, saved):
    h = harness
    h.version.model = saved
    # This matches the editor's live update, while V1 remains unchanged.
    h.template.model = selected
    h.template.config["model"] = selected
    result = h.run({"config": {}}, {"text": "hello"}, h.template, h.org,
                   model=selected, source="eval_playground", workspace=h.workspace)
    assert h.calls == [{"model": selected, "inputs": {"text": "hello"}}]
    assert result["model"] == selected
    assert h.version.model == saved
    assert h.charges[0]["api_call_type"] == f"eval:{selected}"
    assert h.charges[0]["config"]["model"] == selected
    assert len(h.events) == 1
    event = h.events[0]
    assert event.event_type == f"eval:{selected}"
    assert Decimal(event.properties["raw_cost_usd"]) == h.prices[selected] + Decimal("0.01")
    assert event.amount == (h.prices[selected] + Decimal("0.01")) * 100
    assert event.properties["model"] == selected
    if selected == "workspace-judge":
        assert h.instances[0].config["api_key"] == "fixture-for-workspace-judge"
        assert h.instances[0].config["provider"] == "provider-for-workspace-judge"
        h.credentials.assert_called_once_with(selected, h.org.id, h.workspace.id,
                                               **({"allow_managed": True} if hasattr(engine, "_DEFAULT_MODEL") else {}))


def test_playground_stale_runtime_model_cannot_relabel_usage(harness):
    h = harness
    h.run({"config": {}, "model": "turing_large"}, {"text": "hello"}, h.template, h.org,
          model="turing_flash", source="eval_playground")
    assert h.calls[0]["model"] == "turing_flash"
    assert h.charges[0]["config"]["model"] == "turing_flash"
    assert h.events[0].properties["model"] == "turing_flash"


WIZARD_ROW = {"source": "experiment_create", "dataset_id": "dataset-1", "row_id": "row-1"}


def test_wizard_card_test_scores_and_bills_with_the_model_the_run_uses(harness):
    # A wizard card with no judge model of its own sends "turing_large"; the
    # run it previews uses the saved version's model, and so does the Test.
    h = harness
    h.version.model = "turing_flash"
    h.run({"config": {}}, {"text": "hello"}, h.template, h.org, model="turing_large",
          source="eval_playground", workspace=h.workspace, row_context=WIZARD_ROW)
    assert [call["model"] for call in h.calls] == ["turing_flash"]
    assert h.version.model == "turing_flash"
    assert len(h.events) == 1
    event = h.events[0]
    assert Decimal(event.properties["raw_cost_usd"]) == h.prices["turing_flash"] + Decimal("0.01")
    assert "model" not in event.properties


def test_editor_playground_without_wizard_context_keeps_the_selected_model(harness):
    h = harness
    h.version.model = "turing_large"
    h.run({"config": {}}, {"text": "hello"}, h.template, h.org, model="turing_flash",
          source="eval_playground", workspace=h.workspace,
          row_context={"dataset_id": "dataset-1", "row_id": "row-1"})
    assert [call["model"] for call in h.calls] == ["turing_flash"]
    assert h.events[0].properties["model"] == "turing_flash"


def _call_type_by_template(model, **kwargs):
    return "template:binding" if "template" in kwargs else f"eval:{model}"


def test_playground_without_a_picked_model_meters_by_the_template(harness):
    # No model in the request: the check and the log go by the template and the
    # run's config (what the engine runs), never by a default model.
    h = harness
    h.namespace["_get_api_call_type"] = _call_type_by_template
    h.run({"config": {}}, {"text": "hello"}, h.template, h.org, model=None,
          source="eval_playground", workspace=h.workspace)
    # Credit check and log row (one api_call_type), the log row's model, and
    # the usage meter's model all go by the template.
    assert h.charges[0]["api_call_type"] == "template:binding"
    assert h.charges[0]["config"].get("model") is None
    assert "model" not in h.events[0].properties
    assert h.events[0].event_type == "template:binding"


def test_playground_with_a_picked_model_meters_by_that_model(harness):
    h = harness
    h.namespace["_get_api_call_type"] = _call_type_by_template
    h.run({"config": {}}, {"text": "hello"}, h.template, h.org, model="turing_flash",
          source="eval_playground", workspace=h.workspace)
    assert h.charges[0]["api_call_type"] == "eval:turing_flash"
    assert h.charges[0]["config"]["model"] == "turing_flash"
    assert h.events[0].properties["model"] == "turing_flash"
    assert h.events[0].event_type == "eval:turing_flash"


@pytest.mark.parametrize("source,version_number", [("dataset", 1), ("eval_playground", 1), ("dataset", None)])
def test_non_playground_and_explicit_version_keep_saved_model(harness, source, version_number):
    h = harness
    runner = h.Runner(source=source)
    runner.version_number = version_number
    judge = runner._create_eval_instance({}, model="turing_flash")
    assert judge.model == "turing_large"
    assert h.version.model == "turing_large"


@pytest.mark.parametrize("binding_model,runtime_model", [
    ("turing_flash", "turing_large"), ("turing_large", "turing_flash"),
])
def test_simulation_runtime_model_keeps_base_usage_attribution(harness, binding_model, runtime_model):
    h = harness
    h.template.eval_type = "agent"
    h.template.model = binding_model
    h.template.config.update(eval_type_id="AgentEvaluator", model=binding_model)
    h.version.model = binding_model
    # Simulation forwards the binding model separately from its runtime config.
    config = {"model": runtime_model, "run_config": {"model": runtime_model}}
    result = h.run(config, {"text": "hello"}, h.template, h.org,
                   model=binding_model, source="simulate", workspace=h.workspace)

    assert h.calls == [{"model": runtime_model, "inputs": {"text": "hello"}}]
    executed_model = h.calls[0]["model"]
    assert result["model"] == executed_model
    assert len(h.charges) == 1
    assert h.charges[0]["source"] == "simulate"
    assert h.charges[0]["config"]["model"] == executed_model
    assert json.loads(h.log.config)["model"] == executed_model
    assert h.log.status == "success"
    assert len(h.events) == 1
    event = h.events[0]
    assert event.properties["source"] == "simulate"
    # Only the repaired playground path adds the new authoritative model field.
    assert "model" not in event.properties
    expected_cost = h.prices[executed_model] + Decimal("0.01")
    assert Decimal(event.properties["raw_cost_usd"]) == expected_cost
    assert event.amount == expected_cost * 100
    assert h.version.model == binding_model
    assert config == {"model": runtime_model, "run_config": {"model": runtime_model}}


SAVED_SUMMARY_ID = "6f1c2b9e-4a3d-4c8e-9b7a-2d5e8f1a3c40"
OTHER_ORG_SUMMARY_ID = "0b8d7e6c-5f4a-4b3c-8d2e-1f0a9b8c7d65"
DELETED_SUMMARY_ID = "3c2b1a09-8f7e-4d6c-9b5a-4e3d2c1b0a99"
SAVED_SUMMARY_CRITERIA = "Lead with the verdict, then list each failed check."
UNAVAILABLE_SUMMARY = "Summary template is unavailable. Choose another summary."


@pytest.fixture
def agent_summary(harness, monkeypatch):
    """The harness judge as an agent eval whose summary names a saved template."""
    h = harness
    h.template.eval_type = "agent"
    h.template.config.update(eval_type_id="AgentEvaluator", model="turing_flash")
    saved = {
        SAVED_SUMMARY_ID: SimpleNamespace(
            organization_id=h.org.id, criteria=SAVED_SUMMARY_CRITERIA),
        OTHER_ORG_SUMMARY_ID: SimpleNamespace(
            organization_id="organization-2", criteria="Another organization's summary"),
    }
    lookups = []

    def filter_templates(**kwargs):
        lookups.append(kwargs)
        found = saved.get(str(kwargs["id"]))
        if found is not None and str(found.organization_id) != str(kwargs["organization_id"]):
            found = None
        return SimpleNamespace(first=lambda: found)

    _module(monkeypatch, "model_hub.models.eval_summary_template",
            EvalSummaryTemplate=SimpleNamespace(objects=SimpleNamespace(filter=filter_templates)))
    log = Mock()
    monkeypatch.setattr(engine, "logger", log)

    def pick(summary):
        h.template.config["summary"] = summary

    return SimpleNamespace(h=h, pick=pick, log=log, lookups=lookups)


def test_playground_runs_the_saved_summary_template_as_inline_criteria(agent_summary):
    h = agent_summary.h
    agent_summary.pick({"type": f"custom:{SAVED_SUMMARY_ID}"})
    h.run({"config": {}}, {"text": "hello"}, h.template, h.org, model="turing_flash",
          source="eval_playground", workspace=h.workspace, strict_summary=True)

    assert [judge.config["summary"] for judge in h.instances] == [
        {"type": "custom", "custom": SAVED_SUMMARY_CRITERIA}]
    assert agent_summary.lookups == [
        {"id": SAVED_SUMMARY_ID, "organization_id": str(h.org.id)}]
    assert len(h.calls) == 1
    assert h.log.status == "success"
    # The eval's own setting keeps naming the saved template.
    assert h.template.config["summary"] == {"type": f"custom:{SAVED_SUMMARY_ID}"}


@pytest.mark.parametrize("summary_type", [
    f"custom:{DELETED_SUMMARY_ID}", f"custom:{OTHER_ORG_SUMMARY_ID}", "custom:not-a-template-id",
], ids=["deleted", "other-organization", "malformed"])
def test_playground_refuses_a_summary_template_it_cannot_use(agent_summary, summary_type):
    h = agent_summary.h
    agent_summary.pick({"type": summary_type})
    with pytest.raises(engine.SummaryTemplateUnavailable) as raised:
        h.run({"config": {}}, {"text": "hello"}, h.template, h.org, model="turing_flash",
              source="eval_playground", workspace=h.workspace, strict_summary=True)

    assert str(raised.value) == UNAVAILABLE_SUMMARY
    # Refused before the evaluator is built: no judge call, no usage row, no event.
    assert (h.instances, h.calls, h.charges, h.events) == ([], [], [], [])


@pytest.mark.parametrize("source", ["dataset", "experiment", "tracer", "simulate", "sdk"])
@pytest.mark.parametrize("summary_id", [SAVED_SUMMARY_ID, DELETED_SUMMARY_ID],
                         ids=["saved", "deleted"])
def test_background_runs_fall_back_to_the_concise_summary(agent_summary, source, summary_id):
    h = agent_summary.h
    agent_summary.pick({"type": f"custom:{summary_id}"})
    h.run({"config": {}}, {"text": "hello"}, h.template, h.org, model="turing_flash",
          source=source, workspace=h.workspace)

    assert [judge.config["summary"] for judge in h.instances] == [{"type": "concise"}]
    assert len(h.calls) == 1
    assert len(h.charges) == 1
    assert h.log.status == "success"
    assert agent_summary.lookups == []
    agent_summary.log.warning.assert_any_call(
        "summary_template_not_applied", summary_template_id=summary_id, fallback="concise")


def test_dataset_runner_falls_back_to_the_concise_summary(agent_summary):
    h = agent_summary.h
    agent_summary.pick({"type": f"custom:{DELETED_SUMMARY_ID}"})
    judge = h.Runner(source="dataset")._create_eval_instance({}, model="turing_flash")

    assert judge.config["summary"] == {"type": "concise"}
    agent_summary.log.warning.assert_any_call(
        "summary_template_not_applied", summary_template_id=DELETED_SUMMARY_ID,
        fallback="concise")


def test_wizard_card_test_previews_the_runs_concise_summary(agent_summary):
    # The New experiment card Test reaches the playground but previews the run,
    # which falls back to the concise summary instead of failing.
    h = agent_summary.h
    agent_summary.pick({"type": f"custom:{DELETED_SUMMARY_ID}"})
    h.run({"config": {}}, {"text": "hello"}, h.template, h.org, model="turing_large",
          source="eval_playground", workspace=h.workspace, row_context=WIZARD_ROW,
          strict_summary=True)

    assert [judge.config["summary"] for judge in h.instances] == [{"type": "concise"}]
    assert len(h.calls) == 1


@pytest.mark.parametrize("summary", [
    {"type": "concise"}, {"type": "detailed"},
    {"type": "custom", "custom": "Inline explanation rules"},
], ids=["concise", "preset", "inline"])
@pytest.mark.parametrize("strict", [True, False], ids=["playground", "background"])
def test_presets_and_inline_summaries_are_unchanged(agent_summary, summary, strict):
    # Guard: passes before and after the fix; only custom:<id> is resolved.
    h = agent_summary.h
    agent_summary.pick(dict(summary))
    h.run({"config": {}}, {"text": "hello"}, h.template, h.org, model="turing_flash",
          source="eval_playground" if strict else "dataset", workspace=h.workspace,
          strict_summary=strict)

    assert [judge.config["summary"] for judge in h.instances] == [summary]
    assert agent_summary.lookups == []
    agent_summary.log.warning.assert_not_called()
