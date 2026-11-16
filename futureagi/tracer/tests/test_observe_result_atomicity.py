"""Observe's post-insert read must not leave contradictory live results."""

from contextlib import contextmanager, nullcontext

import pytest
from django.db import OperationalError, connection, transaction
from django.db.models import QuerySet
from django.db.models.signals import post_save

import model_hub.tasks  # noqa: F401 -- initialize the existing task import cycle
from model_hub.models.choices import FeedbackSourceChoices
from model_hub.models.evals_metric import Feedback
from tracer.models.observation_span import EvalEntryStatus, EvalLogger
from tracer.tests._ch_seed import (
    _EVAL_LOGGER_INSERT_COLUMNS,
    _ensure_ch_eval_logger_table,
    _eval_logger_row_from_django,
    _get_ch_client,
    truncate_ch_eval_logger,
)
from tracer.utils.eval import evaluate_observation_span_observe

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def observe(observation_span, custom_eval_config, stub_run_eval, stub_cost_log):
    assert observation_span.project_version_id is None
    project = observation_span.project
    project.trace_type = "observe"
    project.save(update_fields=["trace_type"])
    trace = observation_span.trace
    trace.project_version = None
    trace.save(update_fields=["project_version"])
    return observation_span, custom_eval_config


def run(observe):
    span, config = observe
    # Keep the test connection; all execution, mapping, writes and reads are real.
    return evaluate_observation_span_observe._original_func(span.id, config.id)


def rows(observe, *, history=False):
    span, config = observe
    manager = EvalLogger.all_objects if history else EvalLogger.objects
    return manager.filter(observation_span=span, custom_eval_config=config)


def assert_result(observe, auth_client, *, error):
    span, config = observe
    row = rows(observe).get()
    assert row.trace_id == span.trace_id
    assert row.observation_span_id == span.pk
    assert row.custom_eval_config_id == config.pk
    assert row.eval_task_id is None
    assert row.status == EvalEntryStatus.COMPLETED
    assert row.error is error
    assert row.output_bool is (None if error else True)
    assert row.output_float is None
    assert row.output_str == ("ERROR" if error else None)
    assert bool(row.error_message) is error
    # The Observe detail endpoint reads CH. Mirror the actual PG history using
    # the shared CDC fixture helper; this tests readback, not a CDC deployment.
    truncate_ch_eval_logger()
    client = _get_ch_client()
    try:
        _ensure_ch_eval_logger_table(client)
        client.insert(
            "tracer_eval_logger",
            [
                _eval_logger_row_from_django(item)
                + (item.observation_span_id, str(item.trace_id))
                for item in rows(observe, history=True)
            ],
            column_names=[
                *_EVAL_LOGGER_INSERT_COLUMNS,
                "observation_span_id",
                "trace_id",
            ],
        )
    finally:
        client.close()
    try:
        response = auth_client.get(
            "/tracer/observation-span/get_evaluation_details/",
            {"observation_span_id": span.pk, "custom_eval_config_id": str(config.pk)},
        )
        assert response.status_code == 200, response.data
        result = response.data["result"]
        assert result.get("error", False) is error
        assert result["score"] == (None if error else True)
        assert result["explanation"] == (
            row.error_message if error else row.eval_explanation
        )
    finally:
        truncate_ch_eval_logger()
    return row


@contextmanager
def fail_joined_read(monkeypatch, *, database_error=False):
    written = []
    reads = []
    original_get = QuerySet.get

    def record(sender, instance, created, **kwargs):
        if created and not instance.error:
            assert sender.objects.filter(pk=instance.pk, output_bool=True).exists()
            written.append(instance.pk)

    def get(queryset, *args, **kwargs):
        if (
            queryset.model is EvalLogger
            and queryset.query.select_related
            and kwargs.get("pk") in written
            and not reads
        ):
            # Complete the real joined SELECT after the real INSERT, then model
            # a transient delivery failure. Do not replace persistence itself.
            row = original_get(queryset, *args, **kwargs)
            reads.append(row.pk)
            if database_error:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1 / 0")
            raise OperationalError("Synthetic joined result read unavailable")
        return original_get(queryset, *args, **kwargs)

    post_save.connect(record, sender=EvalLogger, weak=False)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(QuerySet, "get", get)
            yield written, reads
    finally:
        post_save.disconnect(record, sender=EvalLogger)


def test_success_and_existing_result_guard(observe, auth_client, stub_run_eval):
    assert run(observe) is True
    first = assert_result(observe, auth_client, error=False)
    assert run(observe) is None
    assert rows(observe).get().pk == first.pk
    assert rows(observe, history=True).count() == 1
    assert len(stub_run_eval.requests) == 1


def test_failure_before_write(observe, auth_client, monkeypatch):
    def fail(*args, **kwargs):
        assert not rows(observe).exists()
        raise RuntimeError("Synthetic early failure")

    monkeypatch.setattr("tracer.utils.eval._process_mapping", fail)
    assert run(observe) is False
    assert_result(observe, auth_client, error=True)
    assert rows(observe, history=True).count() == 1


@pytest.mark.parametrize("outer_transaction", [False, True])
@pytest.mark.parametrize("database_error", [False, True])
def test_post_insert_read_failure_has_one_error(
    observe, auth_client, monkeypatch, outer_transaction, database_error
):
    with transaction.atomic() if outer_transaction else nullcontext():
        with fail_joined_read(monkeypatch, database_error=database_error) as evidence:
            assert run(observe) is False
        written, reads = evidence
        assert len(written) == 1 and reads == written
        assert not connection.needs_rollback
        row = assert_result(observe, auth_client, error=True)
        assert row.pk != written[0]
        assert not EvalLogger.all_objects.filter(pk=written[0]).exists()
        assert rows(observe, history=True).count() == 1
        assert run(observe) is None
        assert rows(observe).get().pk == row.pk


def test_outer_rollback_includes_fallback(observe, monkeypatch):
    with transaction.atomic():
        with fail_joined_read(monkeypatch):
            assert run(observe) is False
        assert rows(observe).count() == 1
        transaction.set_rollback(True)
    assert not rows(observe, history=True).exists()


def test_feedback_recalculation_after_read_failure_retains_history(
    observe, auth_client, user, monkeypatch, stub_run_eval
):
    span, config = observe
    monkeypatch.setattr(
        "tracer.views.observation_span.track_mixpanel_event", lambda *a, **k: None
    )
    # Only bypass the activity connection lifecycle, not its body or the API.
    monkeypatch.setattr(
        "tracer.views.observation_span.evaluate_observation_span_observe",
        evaluate_observation_span_observe._original_func,
    )
    feedback = Feedback.objects.create(
        source=FeedbackSourceChoices.OBSERVE.value,
        source_id=str(span.pk),
        value="Failed",
        eval_template=config.eval_template,
        user=user,
        custom_eval_config_id=config.pk,
        organization=span.project.organization,
        workspace=span.project.workspace,
    )
    with fail_joined_read(monkeypatch):
        assert run(observe) is False
    first = assert_result(observe, auth_client, error=True)
    payload = {
        "observation_span_id": span.pk,
        "action_type": "recalculate",
        "custom_eval_config_id": str(config.pk),
        "feedback_id": str(feedback.pk),
    }
    for history_count in (2, 3):
        response = auth_client.post(
            "/tracer/observation-span/submit_feedback_action_type/",
            payload,
            format="json",
        )
        assert response.status_code == 200, response.data
        replacement = assert_result(observe, auth_client, error=False)
        assert replacement.pk != first.pk
        first.refresh_from_db()
        assert first.deleted is True
        assert rows(observe, history=True).count() == history_count
        first = replacement
    assert len(stub_run_eval.requests) == 3


@pytest.mark.parametrize("fail_read", [False, True])
def test_external_work_outside_result_transaction(
    observe, auth_client, monkeypatch, stub_cost_log, fail_read
):
    from evaluations.engine import run_eval

    seen = []

    def evaluate(request):
        assert not connection.in_atomic_block
        seen.append("provider")
        return run_eval(request)

    def bill(**kwargs):
        assert not connection.in_atomic_block
        seen.append("billing")
        return stub_cost_log(**kwargs)

    def localize(**kwargs):
        assert not connection.in_atomic_block
        assert kwargs["eval_logger"].pk == rows(observe).get().pk
        seen.append("localization")

    monkeypatch.setattr("evaluations.engine.run_eval", evaluate)
    monkeypatch.setattr("tracer.utils.eval.log_and_deduct_cost_for_api_request", bill)
    monkeypatch.setattr(
        "model_hub.services.error_localizer_service.error_localizer_enabled",
        lambda config: True,
    )
    monkeypatch.setattr(
        "model_hub.tasks.user_evaluation.trigger_error_localization_for_span", localize
    )
    with fail_joined_read(monkeypatch) if fail_read else nullcontext():
        assert run(observe) is not fail_read
    assert seen == (
        ["billing", "provider"]
        if fail_read
        else ["billing", "provider", "localization"]
    )
    assert_result(observe, auth_client, error=fail_read)
