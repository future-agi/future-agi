"""Real judge construction and accounting; only external ports are replaced."""

import json
import sys
from decimal import Decimal
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from evaluations.usage_step import step_event_id
from model_hub.models.api_key import ApiKey
from model_hub.models.dataset_versions import DatasetVersion
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.pairwise_judges import PairwiseJudgeJob
from model_hub.services import credit_ledger_port as ledger
from model_hub.services import model_route
from model_hub.services import pairwise_judges as service
from model_hub.tests.test_model_route_support import (
    BYOK_FIXTURE_VALUE,
    OPENAI_CHAT_MODEL,
    FakeGatewayClient,
    chat_response,
    eligible_port,
)
from model_hub.tests.test_pairwise_judges import start, submit
from model_hub.views import eval_runner
from tfc.constants.levels import Level

pytest_plugins = ["model_hub.tests.pairwise_judge_support"]

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
    # Shared through pytest_plugins, so modules opt in instead of autouse.
    pytest.mark.usefixtures("dispatch"),
]


def module(monkeypatch, name, **attributes):
    stub = ModuleType(name)
    vars(stub).update(attributes)
    monkeypatch.setitem(sys.modules, name, stub)


@pytest.fixture
def execution_ports(monkeypatch, organization):
    from agentic_eval.core.llm import llm

    answer = json.dumps({"result": "A", "explanation": "Clearer answer"})
    response = chat_response(answer, model=OPENAI_CHAT_MODEL)
    gateway = FakeGatewayClient(response=response)
    port = eligible_port(model_route, organization, {OPENAI_CHAT_MODEL}, client=gateway)
    direct = Mock(return_value=response)
    monkeypatch.setattr(llm.litellm, "completion", direct)
    monkeypatch.setattr(llm, "guarded_completion", direct)
    monkeypatch.setattr(llm, "OpenAI", Mock(return_value=FakeGatewayClient()))
    monkeypatch.setattr(llm, "AsyncOpenAI", Mock(return_value=FakeGatewayClient()))
    monkeypatch.setattr(llm, "log_to_clickhouse", Mock())
    # LLM construction acquires these clients before the Turing adapter
    # runs. Keep the factories offline too, without replacing any guard.
    module(
        monkeypatch,
        "ee.usage.services.gateway_llm_client",
        get_gateway_client=lambda: None,
        get_async_gateway_client=lambda: None,
    )
    turing = SimpleNamespace(
        chat_completion=Mock(return_value=answer),
        token_usage={"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        cost={"total_cost": 0.125, "prompt_cost": 0.0625, "completion_cost": 0.0625},
    )
    module(
        monkeypatch,
        "ee.turing.client",
        TuringClient=lambda: turing,
        ModalityNotSupportedError=type("ModalityNotSupportedError", (Exception,), {}),
    )
    # Model the supported deployment: no legacy up-front combined debit. The
    # real runner decides whether to opt in; no evaluator guard is patched.
    monkeypatch.setattr(eval_runner, "log_and_deduct_cost_for_api_request", None)
    monkeypatch.setattr(ledger, "legacy_billing_installed", lambda: False)
    emit = Mock()
    config = SimpleNamespace(
        get_eval_per_run_fee=lambda: 0.125,
        calculate_ai_credits=lambda amount: Decimal(str(amount)) * 100,
    )
    # The shared helper imports its ID factory from this same module.
    # Record its inputs rather than replacing the runner's step wiring.
    step_ids = Mock(return_value="00000000-0000-5000-8000-000000000001")
    module(
        monkeypatch,
        "ee.usage.schemas.events",
        UsageEvent=SimpleNamespace,
        stable_evaluation_event_id=step_ids,
    )
    module(
        monkeypatch,
        "ee.usage.services.config",
        BillingConfig=SimpleNamespace(get=lambda: config),
    )
    module(monkeypatch, "ee.usage.services.emitter", emit=emit)
    # These judges are neither optional evaluator type. Keep the shared type
    # check real without loading unrelated enterprise implementations.
    module(
        monkeypatch,
        "ee.evals.llm.jev_evaluator.evaluator",
        JevEvaluator=type("JevEvaluator", (), {}),
    )
    module(
        monkeypatch,
        "ee.decisions.evaluator",
        DecisionEvaluator=type("DecisionEvaluator", (), {}),
    )
    module(
        monkeypatch,
        "ee.usage.schemas.event_types",
        BillingEventType=SimpleNamespace(CODE_EVALUATOR=SimpleNamespace(value="code")),
    )
    module(
        monkeypatch,
        "ee.usage.utils.event_properties",
        token_usage_properties=lambda usage: {"total_tokens": usage["total_tokens"]},
    )
    usage = []
    original = eval_runner.EvaluationRunner._emit_cell_usage

    def record(runner, instance, log, row, **kwargs):
        usage.append((runner, instance, row, kwargs["judge_settlement"]))
        return original(runner, instance, log, row, **kwargs)

    monkeypatch.setattr(eval_runner.EvaluationRunner, "_emit_cell_usage", record)
    meter = ledger.MockCreditLedger(default_balance=Decimal("100"))
    ledger.reset_meter_safety()
    with model_route.managed_routing_port(port), ledger.use_credit_ledger(meter):
        yield SimpleNamespace(
            gateway=gateway,
            route=port,
            direct=direct,
            turing=turing,
            meter=meter,
            emit=emit,
            step_ids=step_ids,
            usage=usage,
        )
    ledger.reset_meter_safety()


@pytest.mark.parametrize("connection", ["managed", "byok", "turing"])
def test_supported_judges_execute_once_with_the_shared_accounting_contract(
    auth_client,
    prepared,
    execution_ports,
    organization,
    workspace,
    user,
    connection,
):
    ports = execution_ports
    pin = prepared[3]
    pin.model = "turing_flash" if connection == "turing" else OPENAI_CHAT_MODEL
    pin.save()
    if connection == "byok":
        ApiKey.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            user=user,
            provider="openai",
            key=BYOK_FIXTURE_VALUE,
        )
    job = start(auth_client, prepared)
    assert ports.route.authorize_calls == []
    assert ports.meter.calls == []
    item = job.items.first()
    item.presentation_order = "ba"
    item.save()
    # Mutable editor state must not replace the saved model or choice contract.
    EvalTemplate.no_workspace_objects.filter(pk=pin.eval_template_id).update(
        model="turing_large",
        config={"choices": ["Other"]},
        choices=["Other"],
    )
    service.execute_item(item.pk)
    service.execute_item(item.pk)
    item.refresh_from_db()
    assert (item.status, item.winner, item.reason, item.error) == (
        "completed",
        "b",
        "Clearer answer",
        "",
    )
    assert len(ports.usage) == 1
    runner, instance, row, settlement = ports.usage[0]
    assert instance._model == pin.model
    assert instance._choices == ["A", "B", "tie"]
    assert instance.token_usage["total_tokens"] == 3
    assert row.pk == item.pk
    assert runner._usage_run == str(job.pk)
    assert runner.user_eval_metric.pk == pin.pk
    assert runner.organization_id == organization.pk
    assert runner.workspace_id == workspace.pk
    if connection == "managed":
        assert len(ports.route.authorize_calls) == 1
        assert len(ports.gateway.calls) == 1
        assert ports.direct.call_count == 0
        assert ports.turing.chat_completion.call_count == 0
        assert ports.emit.call_count == 0
        assert ports.step_ids.call_count == 0
        (held,) = ports.meter.holds.values()
        assert (held.state, held.debits, held.workspace_id) == (
            "settled",
            1,
            str(workspace.pk),
        )
        assert [call[0] for call in ports.meter.calls] == ["hold", "settle"]
        assert ports.meter.calls[-1] == ("settle", held.hold_id, held.settled_amount)
        assert ports.meter.balance_of(organization) == (
            Decimal("100") - held.settled_amount
        )
        assert len(settlement) == 1
        assert (
            settlement[0]["request_id"]
            == (ports.route.authorize_calls[0]["request_id"])
        )
        messages = ports.gateway.calls[0]["kwargs"]["messages"]
    else:
        assert ports.route.authorize_calls == []
        assert ports.gateway.calls == []
        assert ports.meter.calls == []
        assert settlement is None
        assert ports.emit.call_count == 1
        event = ports.emit.call_args.args[0]
        expected_amount = instance.cost["total_cost"] + 0.125
        assert event.amount == Decimal(str(expected_amount)) * 100
        assert event.org_id == str(organization.pk)
        assert event.properties["raw_cost_usd"] == str(expected_amount)
        ports.step_ids.assert_called_once_with(
            evaluated=(item.pk, None, None),
            run=str(job.pk),
            evaluator=(pin.pk, pin.version_number),
        )
        assert event.event_id == step_event_id(
            {
                "evaluated": (item.pk, None, None),
                "run": str(job.pk),
                "evaluator": (pin.pk, pin.version_number),
            }
        )
        if connection == "byok":
            assert ports.direct.call_count == 1
            assert ports.direct.call_args.kwargs["api_key"] == BYOK_FIXTURE_VALUE
            assert ports.turing.chat_completion.call_count == 0
            messages = ports.direct.call_args.kwargs["messages"]
        else:
            assert event.amount == Decimal("25")
            assert ports.direct.call_count == 0
            assert ports.turing.chat_completion.call_count == 1
            messages = ports.turing.chat_completion.call_args.kwargs["messages"]
    assert [
        line
        for line in messages[-1]["content"].splitlines()
        if line.startswith("<output_")
    ] == ["<output_a>Answer 1</output_a>", "<output_b>Answer 0</output_b>"]


def test_dataset_examples_are_refused_before_a_restricted_member_admits_a_job(
    auth_client,
    prepared,
    execution_ports,
    organization,
    workspace,
    user,
):
    ports = execution_ports
    restricted = Dataset.no_workspace_objects.create(
        name="Default workspace examples",
        organization=organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )
    row = Row.no_workspace_objects.create(dataset=restricted, order=1)
    for name, text in (
        ("input", "Restricted example"),
        ("output", "Restricted answer"),
    ):
        column = Column.no_workspace_objects.create(
            dataset=restricted,
            name=name,
            data_type="text",
            source="OTHERS",
        )
        Cell.no_workspace_objects.create(
            dataset=restricted,
            row=row,
            column=column,
            value=text,
        )
    private = Workspace.no_workspace_objects.create(
        organization=organization,
        name="Member workspace",
        created_by=user,
    )
    OrganizationMembership.no_workspace_objects.filter(
        organization=organization,
        user=user,
    ).update(level=Level.MEMBER, role="Member")
    WorkspaceMembership.no_workspace_objects.filter(
        workspace=workspace,
        user=user,
    ).update(deleted=True, is_active=False)
    WorkspaceMembership.no_workspace_objects.update_or_create(
        workspace=private,
        user=user,
        defaults={
            "level": Level.WORKSPACE_MEMBER,
            "role": "workspace_member",
            "is_active": True,
            "deleted": False,
        },
    )
    dataset = prepared[0].dataset
    dataset.workspace = private
    dataset.save()
    DatasetVersion.no_workspace_objects.filter(dataset=dataset).update(
        workspace=private
    )
    pin = prepared[3]
    pin.eval_template.workspace = private
    pin.eval_template.save()
    pin.workspace = private
    pin.model = OPENAI_CHAT_MODEL
    pin.config_snapshot["few_shot_examples"] = [{"id": str(restricted.pk)}]
    pin.save()
    auth_client.set_workspace(private)
    assert user.can_write_to_workspace(private) is True
    assert user.can_write_to_workspace(workspace) is False
    refused = submit(auth_client, prepared)
    assert refused.status_code == 400
    assert (
        refused.data["detail"] == "Pairwise judges require literal few-shot examples."
    )
    assert PairwiseJudgeJob.objects.count() == 0
    assert ports.gateway.calls == []
    assert ports.meter.calls == []
    # A literal example still runs through the REAL engine and provider adapter.
    # Neither live template config nor later pinned expansion can widen scope.
    pin.config_snapshot["few_shot_examples"] = [
        {"input": "Allowed example", "output": "Allowed answer", "score": "A"},
    ]
    pin.save()
    job = start(auth_client, prepared)
    item = job.items.first()
    service.execute_item(item.pk)
    item.refresh_from_db()
    assert (item.status, item.reason) == ("completed", "Clearer answer")
    assert len(ports.gateway.calls) == 1
    runner, instance, _, _ = ports.usage[0]
    assert runner.workspace_id == private.pk
    assert instance._few_shot_examples == pin.config_snapshot["few_shot_examples"]
    sent = json.dumps(ports.gateway.calls[0]["kwargs"]["messages"])
    assert "Allowed example" in sent
    assert "Restricted example" not in sent
    assert "Restricted answer" not in sent


def test_worker_rejects_later_dataset_example_references_without_dispatch(
    auth_client,
    prepared,
    execution_ports,
):
    job = start(auth_client, prepared)
    pin = prepared[3]
    pin.config_snapshot["few_shot_examples"] = [{"id": str(prepared[0].dataset_id)}]
    pin.save()
    item = job.items.first()
    service.execute_item(item.pk)
    item.refresh_from_db()
    assert (item.status, item.error) == ("failed", "scope_unavailable")
    assert execution_ports.gateway.calls == []
    assert execution_ports.direct.call_count == 0
    assert execution_ports.turing.chat_completion.call_count == 0
    assert execution_ports.usage == []
    assert execution_ports.meter.calls == []
