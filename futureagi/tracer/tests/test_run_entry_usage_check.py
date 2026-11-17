"""run_entry checks the organization's usage before it evaluates an entry, as a
dataset run checks it before its first row.

An organization the usage check refuses gets no evaluator call, no cost log and
no usage event on a span, a trace or a session entry, and the entry is recorded
errored with the refusal's sentence. An organization the check allows is
checked first, for the organization and call type its charge uses, and is then
evaluated and charged as before; so is every organization of a deployment
without the billing module. An evaluator that calls no model is checked and
charged like any other, and a composite is left to the check each child's run
makes. The engine, the cost log and the usage event are stubbed and watched.
"""

import sys
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

# Breaks the tracer.utils.eval <-> model_hub.tasks import cycle for test-time imports.
import model_hub.tasks  # noqa: F401
from model_hub.models.evals_metric import EvalTemplate
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RunType
from tracer.models.observation_span import (
    EvalEntryStatus,
    EvalLogger,
    EvalTargetType,
    ObservationSpan,
)
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession
from tracer.services.eval_tasks.run_entry import run_entry
from tracer.tests._ch_seed import seed_ch_span, seed_ch_trace, seed_ch_trace_sessions

# The sentence check_usage gives an organization with unpaid invoices.
SENTENCE = (
    "Your subscription has unpaid invoices. "
    "Update your payment method to restore access."
)
# What a refused dataset run shows when the refusal carries no reason.
FALLBACK_SENTENCE = "Usage limit exceeded"
NO_MODEL_CODE = "def evaluate(input, output):\n    return 1.0\n"

TARGETS = ["span", "trace", "session", "code_evaluator"]


def _make_entry(**kwargs):
    """Create an entry the way the materializer does: bulk_create skips
    full_clean, so a span or trace that lives only in ClickHouse is allowed."""
    kwargs.setdefault("status", EvalEntryStatus.RUNNING)
    entry = EvalLogger(**kwargs)
    EvalLogger.objects.bulk_create([entry])
    return entry


def _ch_only_span(project, trace):
    """A span that lives only in ClickHouse, never saved to Postgres."""
    span = ObservationSpan(
        id=f"ch-{uuid.uuid4().hex[:16]}",
        project=project,
        trace=trace,
        parent_span_id="",
        name="ch-span",
        observation_type="llm",
        start_time=timezone.now() - timedelta(seconds=2),
        end_time=timezone.now(),
        input={"messages": [{"role": "user", "content": "hi"}]},
        output={"choices": [{"message": {"content": "yo"}}]},
        status="OK",
    )
    seed_ch_span(span)
    return span


def _no_model_config(project, organization, workspace):
    """A code evaluator: it scores in code and never calls a model."""
    template = EvalTemplate.objects.create(
        name="Code evaluator without a model",
        organization=organization,
        workspace=workspace,
        eval_type="code",
        config={"eval_type_id": "CustomCodeEval", "code": NO_MODEL_CODE},
    )
    return CustomEvalConfig.objects.create(
        name="Code evaluator on spans",
        project=project,
        eval_template=template,
        config={},
        mapping={"input": "input", "output": "output"},
        filters={},
    )


@pytest.fixture
def target(
    request,
    settings,
    organization,
    workspace,
    project,
    observe_project,
    eval_template,
    custom_eval_config,
    eval_task,
):
    """One running entry of the requested kind, ready for run_entry."""
    kind = request.param
    organization_id = str(organization.id)
    if kind == "session":
        session = TraceSession.objects.create(project=observe_project, name="sess")
        seed_ch_trace_sessions([session])
        # "name" is a session field, so the session mapping resolves.
        config = CustomEvalConfig.objects.create(
            name="Session eval",
            project=observe_project,
            eval_template=eval_template,
            config={"threshold": 0.8},
            mapping={"input": "name"},
            filters={},
        )
        task = EvalTask.objects.create(
            project=observe_project,
            name="Session Eval Task",
            filters={},
            sampling_rate=1.0,
            run_type=RunType.CONTINUOUS,
            status=EvalTaskStatus.PENDING,
            spans_limit=100,
        )
        task.evals.add(config)
        entry = _make_entry(
            target_type=EvalTargetType.SESSION,
            trace_session=session,
            custom_eval_config=config,
            eval_task_id=str(task.id),
        )
        return SimpleNamespace(
            entry=entry, task_id=str(task.id), organization_id=organization_id
        )

    config = custom_eval_config
    if kind == "code_evaluator":
        settings.CUSTOM_CODE_EVALS_PAUSED = False
        config = _no_model_config(project, organization, workspace)
        eval_task.evals.add(config)

    if kind == "trace":
        trace = Trace(
            id=uuid.uuid4(),
            project=project,
            name="t",
            input={"q": "hello"},
            output={"a": "world"},
        )
        seed_ch_trace(trace)
        root = _ch_only_span(project, trace)
        entry = _make_entry(
            target_type=EvalTargetType.TRACE,
            observation_span_id=root.id,
            trace_id=str(trace.id),
            custom_eval_config=config,
            eval_task_id=str(eval_task.id),
        )
    else:
        trace = Trace.objects.create(project=project, name="t")
        span = _ch_only_span(project, trace)
        entry = _make_entry(
            target_type=EvalTargetType.SPAN,
            observation_span_id=span.id,
            trace=trace,
            custom_eval_config=config,
            eval_task_id=str(eval_task.id),
        )
    return SimpleNamespace(
        entry=entry, task_id=str(eval_task.id), organization_id=organization_id
    )


@pytest.fixture
def timeline():
    """The usage checks and the cost logs, in the order they happened."""
    return []


@pytest.fixture
def usage(monkeypatch, timeline):
    """The organization's usage check: allows unless told to refuse or fail."""
    state = SimpleNamespace(
        answer=SimpleNamespace(allowed=True, reason=""),
        error=None,
        calls=[],
    )

    def check_usage(org_id, event_type, amount=0):
        state.calls.append((org_id, event_type))
        timeline.append(("check", org_id, event_type))
        if state.error is not None:
            raise state.error
        return state.answer

    def refuse(reason):
        state.answer = SimpleNamespace(
            allowed=False, reason=reason, error_code="PAYMENT_REQUIRED"
        )

    state.refuse = refuse
    monkeypatch.setattr("ee.usage.services.metering.check_usage", check_usage)
    return state


@pytest.fixture
def charges(monkeypatch, stub_cost_log, timeline):
    """Every cost log an evaluation writes, answered as allowed."""
    calls = []

    def _record(**kwargs):
        calls.append(kwargs)
        timeline.append(
            ("charge", str(kwargs["organization"].id), kwargs["api_call_type"])
        )
        return stub_cost_log(**kwargs)

    monkeypatch.setattr(
        "tracer.utils.eval.log_and_deduct_cost_for_api_request", _record
    )
    return calls


@pytest.fixture
def usage_events(monkeypatch):
    """Every usage event an evaluation emits for billing."""
    calls = []
    monkeypatch.setattr(
        "tracer.utils.eval._emit_eval_billing", lambda **kwargs: calls.append(kwargs)
    )
    return calls


def _assert_evaluated_and_charged_once(
    target, status, stub_run_eval, charges, usage_events
):
    entry = target.entry
    entry.refresh_from_db()
    assert status == EvalEntryStatus.COMPLETED, (status, entry.error_message)
    assert entry.status == EvalEntryStatus.COMPLETED
    assert not entry.error
    assert len(stub_run_eval.requests) == 1
    assert len(charges) == 1
    assert len(usage_events) == 1
    assert EvalLogger.objects.filter(eval_task_id=target.task_id).count() == 1


@pytest.mark.integration
@pytest.mark.django_db
class TestRefusedOrganization:
    @pytest.mark.parametrize("target", TARGETS, indirect=True)
    def test_entry_is_refused_before_any_evaluation_or_charge(
        self, target, usage, stub_run_eval, charges, usage_events
    ):
        usage.refuse(SENTENCE)

        status = run_entry(target.entry)

        entry = target.entry
        entry.refresh_from_db()
        assert status == EvalEntryStatus.ERRORED
        assert entry.status == EvalEntryStatus.ERRORED
        assert entry.error is True
        assert entry.error_message == SENTENCE
        assert entry.skipped_reason is None
        assert entry.config_hash
        assert [org_id for org_id, _ in usage.calls] == [target.organization_id]
        assert stub_run_eval.requests == []
        assert charges == []
        assert usage_events == []
        assert EvalLogger.objects.filter(eval_task_id=target.task_id).count() == 1

    @pytest.mark.parametrize("target", ["span"], indirect=True)
    def test_refusal_without_a_reason_records_the_fallback_sentence(
        self, target, usage, stub_run_eval, charges, usage_events
    ):
        usage.refuse("")

        assert run_entry(target.entry) == EvalEntryStatus.ERRORED

        target.entry.refresh_from_db()
        assert target.entry.error_message == FALLBACK_SENTENCE
        assert stub_run_eval.requests == []
        assert charges == []
        assert usage_events == []

    @pytest.mark.parametrize("target", ["span"], indirect=True)
    def test_check_that_cannot_answer_runs_nothing_and_says_why(
        self, target, usage, stub_run_eval, charges, usage_events
    ):
        usage.error = RuntimeError("billing store unreachable")

        assert run_entry(target.entry) == EvalEntryStatus.ERRORED

        target.entry.refresh_from_db()
        assert target.entry.status == EvalEntryStatus.ERRORED
        assert target.entry.error is True
        assert "billing store unreachable" in (target.entry.error_message or "")
        assert stub_run_eval.requests == []
        assert charges == []
        assert usage_events == []


@pytest.mark.integration
@pytest.mark.django_db
class TestAllowedOrganization:
    @pytest.mark.parametrize("target", TARGETS, indirect=True)
    def test_check_comes_first_for_the_charged_organization_and_call_type(
        self, target, usage, stub_run_eval, charges, usage_events, timeline
    ):
        assert run_entry(target.entry) == EvalEntryStatus.COMPLETED

        [charge] = charges
        charged_call_type = charge["api_call_type"]
        assert timeline == [
            ("check", target.organization_id, charged_call_type),
            ("charge", target.organization_id, charged_call_type),
        ]

    @pytest.mark.parametrize("target", TARGETS, indirect=True)
    def test_entry_is_evaluated_and_charged_as_before(
        self, target, usage, stub_run_eval, charges, usage_events
    ):
        status = run_entry(target.entry)

        _assert_evaluated_and_charged_once(
            target, status, stub_run_eval, charges, usage_events
        )

    def test_composite_is_left_to_the_check_each_child_makes(
        self, monkeypatch, organization, workspace, project, eval_task, usage
    ):
        usage.refuse(SENTENCE)
        template = EvalTemplate.objects.create(
            name="Composite of child evaluators",
            organization=organization,
            workspace=workspace,
            template_type="composite",
            config={},
        )
        config = CustomEvalConfig.objects.create(
            name="Composite on spans",
            project=project,
            eval_template=template,
            config={},
            mapping={"input": "input"},
            filters={},
        )
        eval_task.evals.add(config)
        trace = Trace.objects.create(project=project, name="t")
        span = _ch_only_span(project, trace)
        entry = _make_entry(
            target_type=EvalTargetType.SPAN,
            observation_span_id=span.id,
            trace=trace,
            custom_eval_config=config,
            eval_task_id=str(eval_task.id),
        )
        # The children's runs, each of which checks usage for itself, are
        # replaced by one answer; nothing is written for the composite here.
        children_runs = []

        def _children(**kwargs):
            children_runs.append(kwargs)
            return {"value": 1}

        monkeypatch.setattr("tracer.utils.eval._execute_composite_on_span", _children)
        monkeypatch.setattr(
            "tracer.services.eval_tasks.run_entry._reseed_eval_clustering",
            lambda *_args, **_kwargs: None,
        )

        assert run_entry(entry) == EvalEntryStatus.COMPLETED

        assert len(children_runs) == 1
        assert usage.calls == []


@pytest.mark.integration
@pytest.mark.django_db
class TestWithoutBillingModule:
    @pytest.mark.parametrize("target", TARGETS, indirect=True)
    def test_entry_is_evaluated_and_charged_as_before(
        self, monkeypatch, target, stub_run_eval, charges, usage_events
    ):
        monkeypatch.setitem(sys.modules, "ee.usage.services.metering", None)

        status = run_entry(target.entry)

        _assert_evaluated_and_charged_once(
            target, status, stub_run_eval, charges, usage_events
        )
