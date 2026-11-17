"""Span evaluations that run outside an evaluation task check the
organization's usage before they retire an earlier result or call the
evaluator, as a task entry and a dataset run do.

Three entry points are covered: the scheduled runner that evaluates the new
spans of a project version, a project version's run over all of its spans, and
the re-calculation a person asks for when they edit feedback. An organization
the usage check refuses gets no evaluator call, no cost log and no usage event
on any of them, and keeps the results its spans already show. An organization
the check allows is checked first, for the organization and call type its
charge uses, and is then evaluated and charged as before; so is every
organization of a deployment without the billing module. The engine, the cost
log and the usage event are stubbed and watched.
"""

import sys
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from django.utils import timezone

# Breaks the tracer.utils.eval <-> model_hub.tasks import cycle for test-time imports.
import model_hub.tasks  # noqa: F401
from model_hub.models.choices import StatusType
from model_hub.models.evals_metric import Feedback
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.trace import Trace
from tracer.utils import eval as eval_module
from tracer.utils import span as span_module
from tracer.views import custom_eval_config as config_view
from tracer.views import observation_span as span_view

# The sentence check_usage gives an organization with unpaid invoices.
SENTENCE = (
    "Your subscription has unpaid invoices. "
    "Update your payment method to restore access."
)
# How these paths record an evaluation that could not run on the span.
RECORDED = f"Error during evaluation: {SENTENCE}"
RUN_URL = "/tracer/custom-eval-config/run_evaluation/"
FEEDBACK_URL = "/tracer/observation-span/submit_feedback_action_type/"
PATHS = [
    "scheduled",
    "project_version_run",
    "recalculate_experiment",
    "recalculate_observe",
]
RECALCULATIONS = ["recalculate_experiment", "recalculate_observe"]

# The activities' own bodies. Called through their Temporal wrapper they would
# close the database connection the test runs in.
EVALUATE_SPAN = eval_module.evaluate_observation_span._original_func
EVALUATE_OBSERVE_SPAN = eval_module.evaluate_observation_span_observe._original_func
RUN_SPAN_EVALUATIONS = eval_module.eval_observation_span_runner._original_func
SCHEDULE_SPAN_EVALUATIONS = span_module.run_evals_on_spans._original_func


def _span(project, trace, *, version=None, eval_status=StatusType.COMPLETED.value):
    return ObservationSpan.objects.create(
        id=f"usage-{uuid.uuid4().hex[:16]}",
        project=project,
        project_version=version,
        trace=trace,
        name="Evaluated span",
        observation_type="llm",
        start_time=timezone.now(),
        input={"messages": [{"role": "user", "content": "Hello"}]},
        output={"choices": [{"message": {"content": "Hi there"}}]},
        status="OK",
        eval_status=eval_status,
    )


def _evaluate_new_llm_spans(version, config):
    """Ask the project version to evaluate its new llm spans with config."""
    version.eval_tags = [
        {
            "type": "OBSERVATION_SPAN_TYPE",
            "value": "llm",
            "custom_eval_config_id": str(config.id),
        }
    ]
    version.save(update_fields=["eval_tags"])


def _earlier_result(span, config):
    """A result the span already shows for this evaluation."""
    return EvalLogger.objects.create(
        observation_span=span,
        trace=span.trace,
        custom_eval_config=config,
        output_bool=False,
    )


def _feedback(span, config, *, source, user, organization, workspace):
    return Feedback.objects.create(
        source=source,
        source_id=span.id,
        value="true",
        user=user,
        eval_template=config.eval_template,
        custom_eval_config_id=config.id,
        organization=organization,
        workspace=workspace,
    )


def _span_reader(monkeypatch, spans):
    """The span store the project version's run lists its spans from."""
    reader = MagicMock()
    reader.list_by_project.return_value = [SimpleNamespace(id=s.id) for s in spans]
    factory = MagicMock()
    factory.return_value.__enter__.return_value = reader
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", factory)
    return reader


def _live_results(path):
    return list(
        EvalLogger.objects.filter(
            observation_span=path.span,
            custom_eval_config=path.config,
            deleted=False,
        )
    )


@pytest.fixture
def queued(monkeypatch):
    """Runs every evaluation a path starts in this process: the runner the
    schedule starts, each span evaluation a project version's run queues and
    the evaluation a re-calculation runs. Returns the queued span evaluations.
    """
    calls = []

    def queue(*args, **kwargs):
        calls.append(args)
        return EVALUATE_SPAN(*args, **kwargs)

    monkeypatch.setattr(config_view.evaluate_observation_span, "delay", queue)
    monkeypatch.setattr(
        span_module.eval_observation_span_runner, "delay", RUN_SPAN_EVALUATIONS
    )
    monkeypatch.setattr(eval_module, "evaluate_observation_span", EVALUATE_SPAN)
    monkeypatch.setattr(span_view, "evaluate_observation_span", EVALUATE_SPAN)
    monkeypatch.setattr(
        span_view, "evaluate_observation_span_observe", EVALUATE_OBSERVE_SPAN
    )
    monkeypatch.setattr(span_view, "get_mixpanel_properties", Mock(return_value={}))
    monkeypatch.setattr(span_view, "track_mixpanel_event", Mock())
    monkeypatch.setattr(config_view, "close_old_connections", lambda: None)
    return calls


@pytest.fixture
def path(
    request,
    monkeypatch,
    queued,
    auth_client,
    user,
    organization,
    workspace,
    project,
    project_version,
    trace,
    observe_project,
    eval_template,
    custom_eval_config,
):
    """One span evaluation entry point, its span, its evaluation and what the
    span already shows, ready to run."""
    kind = request.param
    config = custom_eval_config
    earlier = feedback = reader = None

    if kind == "scheduled":
        _evaluate_new_llm_spans(project_version, config)
        span = _span(
            project,
            trace,
            version=project_version,
            eval_status=StatusType.NOT_STARTED.value,
        )

        def run():
            SCHEDULE_SPAN_EVALUATIONS()

    elif kind == "project_version_run":
        _evaluate_new_llm_spans(project_version, config)
        span = _span(project, trace, version=project_version)
        earlier = _earlier_result(span, config)
        reader = _span_reader(monkeypatch, [span])

        def run():
            return auth_client.post(
                RUN_URL,
                {
                    "custom_eval_config_id": str(config.id),
                    "project_version_id": str(project_version.id),
                },
                format="json",
            )

    else:
        if kind == "recalculate_experiment":
            span = _span(project, trace, version=project_version)
            source = "experiment"
        else:
            span = _span(observe_project, Trace.objects.create(project=observe_project))
            config = CustomEvalConfig.objects.create(
                name="Observe eval",
                project=observe_project,
                eval_template=eval_template,
                config={"threshold": 0.8},
                mapping={"input": "input", "output": "output"},
                filters={},
            )
            source = "observe"
        earlier = _earlier_result(span, config)
        feedback = _feedback(
            span,
            config,
            source=source,
            user=user,
            organization=organization,
            workspace=workspace,
        )

        def run():
            return auth_client.post(
                FEEDBACK_URL,
                {
                    "observation_span_id": span.id,
                    "custom_eval_config_id": str(config.id),
                    "feedback_id": str(feedback.id),
                    "action_type": "recalculate",
                },
                format="json",
            )

    return SimpleNamespace(
        kind=kind,
        run=run,
        span=span,
        config=config,
        earlier=earlier,
        feedback=feedback,
        reader=reader,
        organization_id=str(organization.id),
    )


@pytest.fixture
def timeline():
    """The usage checks and the cost logs, in the order they happened."""
    return []


@pytest.fixture
def usage(monkeypatch, timeline):
    """The organization's usage check: allows unless told to refuse."""
    state = SimpleNamespace(calls=[], reason=None, allowed_first=0)

    def check_usage(org_id, event_type, amount=0):
        state.calls.append((org_id, event_type))
        timeline.append(("check", org_id, event_type))
        if state.reason is None or len(state.calls) <= state.allowed_first:
            return SimpleNamespace(allowed=True, reason="")
        return SimpleNamespace(
            allowed=False, reason=state.reason, error_code="PAYMENT_REQUIRED"
        )

    def refuse(reason, *, after=0):
        """Refuse with reason every check after the first ``after``."""
        state.reason = reason
        state.allowed_first = after

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


def _assert_nothing_evaluated_or_charged(stub_run_eval, charges, usage_events):
    assert stub_run_eval.requests == []
    assert charges == []
    assert usage_events == []


def _assert_earlier_result_kept(path):
    path.earlier.refresh_from_db()
    assert path.earlier.deleted is False
    assert path.earlier.deleted_at is None
    assert path.earlier.output_bool is False
    assert [result.id for result in _live_results(path)] == [path.earlier.id]


def _assert_refusal_recorded(path):
    [result] = _live_results(path)
    assert result.error is True
    assert result.output_str == "ERROR"
    assert result.error_message == RECORDED
    assert result.results_explanation == {"reason": SENTENCE}


def _assert_evaluated_and_charged_once(
    path, response, stub_run_eval, charges, usage_events
):
    if response is not None:
        assert response.status_code == 200, response.content
    assert len(stub_run_eval.requests) == 1
    assert len(charges) == 1
    assert len(usage_events) == 1
    if path.earlier is not None:
        path.earlier.refresh_from_db()
        assert path.earlier.deleted is True
    [result] = _live_results(path)
    assert not result.error
    if path.kind == "scheduled":
        path.span.refresh_from_db()
        assert path.span.eval_status == StatusType.COMPLETED.value


@pytest.mark.integration
@pytest.mark.django_db
class TestRefusedOrganization:
    @pytest.mark.parametrize("path", ["scheduled"], indirect=True)
    def test_scheduled_span_records_the_refusal_and_is_not_evaluated_or_charged(
        self, path, usage, stub_run_eval, charges, usage_events
    ):
        usage.refuse(SENTENCE)

        path.run()

        _assert_nothing_evaluated_or_charged(stub_run_eval, charges, usage_events)
        _assert_refusal_recorded(path)
        assert [org_id for org_id, _ in usage.calls] == [path.organization_id]
        path.span.refresh_from_db()
        assert path.span.eval_status == StatusType.COMPLETED.value

    @pytest.mark.parametrize("path", ["scheduled"], indirect=True)
    def test_scheduler_does_not_pick_a_refused_span_again(
        self, path, usage, stub_run_eval, charges, usage_events
    ):
        usage.refuse(SENTENCE)

        path.run()
        path.run()

        _assert_nothing_evaluated_or_charged(stub_run_eval, charges, usage_events)
        assert len(usage.calls) == 1
        _assert_refusal_recorded(path)

    @pytest.mark.parametrize("path", ["project_version_run"], indirect=True)
    def test_span_evaluation_keeps_the_result_the_span_already_shows(
        self, path, usage, stub_run_eval, charges, usage_events
    ):
        usage.refuse(SENTENCE)

        assert EVALUATE_SPAN(path.span.id, str(path.config.id)) is False

        _assert_nothing_evaluated_or_charged(stub_run_eval, charges, usage_events)
        _assert_earlier_result_kept(path)

    @pytest.mark.parametrize("path", ["project_version_run"], indirect=True)
    def test_project_version_run_is_refused_before_any_span_is_read_retired_or_queued(
        self, path, queued, usage, stub_run_eval, charges, usage_events
    ):
        usage.refuse(SENTENCE)

        response = path.run()

        assert response.status_code == 400, response.content
        assert response.json()["message"] == SENTENCE
        path.reader.list_by_project.assert_not_called()
        assert queued == []
        _assert_nothing_evaluated_or_charged(stub_run_eval, charges, usage_events)
        _assert_earlier_result_kept(path)
        assert [org_id for org_id, _ in usage.calls] == [path.organization_id]

    @pytest.mark.parametrize("path", ["project_version_run"], indirect=True)
    def test_queued_span_evaluation_records_a_refusal_that_comes_after_the_request(
        self, path, queued, usage, stub_run_eval, charges, usage_events
    ):
        usage.refuse(SENTENCE, after=1)

        response = path.run()

        assert response.status_code == 200, response.content
        assert queued == [(path.span.id, path.config.id)]
        _assert_nothing_evaluated_or_charged(stub_run_eval, charges, usage_events)
        _assert_refusal_recorded(path)
        assert [org_id for org_id, _ in usage.calls] == [path.organization_id] * 2

    @pytest.mark.parametrize("path", RECALCULATIONS, indirect=True)
    def test_recalculate_is_refused_before_the_edit_is_saved_or_the_result_retired(
        self, path, usage, stub_run_eval, charges, usage_events
    ):
        usage.refuse(SENTENCE)

        response = path.run()

        assert response.status_code == 400, response.content
        assert response.json()["message"] == SENTENCE
        path.feedback.refresh_from_db()
        assert path.feedback.action_type is None
        assert path.feedback.value == "true"
        _assert_nothing_evaluated_or_charged(stub_run_eval, charges, usage_events)
        _assert_earlier_result_kept(path)
        assert [org_id for org_id, _ in usage.calls] == [path.organization_id]

    @pytest.mark.parametrize("path", RECALCULATIONS, indirect=True)
    def test_recalculate_records_a_refusal_that_comes_after_the_request(
        self, path, usage, stub_run_eval, charges, usage_events
    ):
        usage.refuse(SENTENCE, after=1)

        response = path.run()

        assert response.status_code == 200, response.content
        path.feedback.refresh_from_db()
        assert path.feedback.action_type == "recalculate"
        path.earlier.refresh_from_db()
        assert path.earlier.deleted is True
        _assert_nothing_evaluated_or_charged(stub_run_eval, charges, usage_events)
        _assert_refusal_recorded(path)


@pytest.mark.integration
@pytest.mark.django_db
class TestAllowedOrganization:
    @pytest.mark.parametrize("path", PATHS, indirect=True)
    def test_check_comes_first_for_the_charged_organization_and_call_type(
        self, path, usage, stub_run_eval, charges, usage_events, timeline
    ):
        path.run()

        [charge] = charges
        charged_call_type = charge["api_call_type"]
        assert timeline[-1] == ("charge", path.organization_id, charged_call_type)
        checks = timeline[:-1]
        assert checks, "the evaluation was charged without a usage check"
        assert set(checks) == {("check", path.organization_id, charged_call_type)}

    @pytest.mark.parametrize("path", PATHS, indirect=True)
    def test_span_is_evaluated_and_charged_as_before(
        self, path, usage, stub_run_eval, charges, usage_events
    ):
        response = path.run()

        _assert_evaluated_and_charged_once(
            path, response, stub_run_eval, charges, usage_events
        )


@pytest.mark.integration
@pytest.mark.django_db
class TestWithoutBillingModule:
    @pytest.mark.parametrize("path", PATHS, indirect=True)
    def test_span_is_evaluated_and_charged_as_before(
        self, monkeypatch, path, stub_run_eval, charges, usage_events
    ):
        monkeypatch.setitem(sys.modules, "ee.usage.services.metering", None)

        response = path.run()

        _assert_evaluated_and_charged_once(
            path, response, stub_run_eval, charges, usage_events
        )
