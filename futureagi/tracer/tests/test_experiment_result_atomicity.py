"""An experiment invocation has one live result even if follow-up writes fail."""

from contextlib import contextmanager

import pytest
from django.db import connection, transaction
from django.db.models.signals import post_save

import model_hub.tasks  # noqa: F401 -- initialize the existing task import cycle
from tracer.models.observation_span import EvalEntryStatus, EvalLogger
from tracer.utils.eval import evaluate_observation_span

pytestmark = pytest.mark.django_db


@pytest.fixture
def experiment(
    observation_span, custom_eval_config, project_version, stub_run_eval, stub_cost_log
):
    observation_span.project_version = project_version
    observation_span.save(update_fields=["project_version"])
    project_version.eval_tags = [
        {
            "type": "OBSERVATION_SPAN_TYPE",
            "value": observation_span.observation_type,
            "custom_eval_config_id": str(custom_eval_config.id),
        }
    ]
    project_version.save(update_fields=["eval_tags"])
    return observation_span, custom_eval_config


def run(experiment):
    span, config = experiment
    # Only skip the activity wrapper's connection lifecycle; execution, mapping,
    # project updates, ORM persistence and readback are real.
    return evaluate_observation_span._original_func(span.id, config.id)


def rows(experiment, *, history=False):
    span, config = experiment
    manager = EvalLogger.all_objects if history else EvalLogger.objects
    return manager.filter(observation_span=span, custom_eval_config=config)


def assert_error_result(experiment, auth_client):
    span, config = experiment
    row = rows(experiment).get()
    row.refresh_from_db()
    assert row.trace_id == span.trace_id
    assert row.observation_span_id == span.id
    assert row.custom_eval_config_id == config.id
    assert row.eval_task_id is None
    assert row.status == EvalEntryStatus.COMPLETED
    assert row.error is True
    assert row.error_message
    assert row.output_bool is None
    assert row.output_float is None
    assert row.output_str == "ERROR"
    assert row.output_str_list == []
    response = auth_client.get(
        "/tracer/observation-span/retrieve_loading/",
        {"observation_span_id": span.id},
    )
    assert response.status_code == 200, response.data
    metric = response.data["result"]["evals_metrics"][f"{config.id}**{span.id}"]
    assert metric["error"] is True
    assert not metric.get("loading", False)
    return row


@contextmanager
def fail_after_result_write(*, database_error=False):
    written = []

    def fail(sender, instance, created, **kwargs):
        if not instance.error:
            # The SQL write has actually occurred, not merely been attempted.
            assert sender.objects.filter(pk=instance.pk, output_bool=True).exists()
            written.append(instance.pk)
            if database_error:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1 / 0")
            raise RuntimeError("Synthetic post-write failure")

    post_save.connect(fail, sender=EvalLogger, weak=False)
    try:
        yield written
    finally:
        post_save.disconnect(fail, sender=EvalLogger)


def test_success_persists_one_result_and_project_fields(
    experiment, project, project_version
):
    assert run(experiment) is True
    row = rows(experiment).get()
    assert row.output_bool is True
    assert row.error is False
    assert row.error_message is None
    assert row.eval_task_id is None
    assert row.status == EvalEntryStatus.COMPLETED
    project.refresh_from_db()
    project_version.refresh_from_db()
    config_id = str(experiment[1].id)
    assert any(field["id"] == config_id for field in project.config)
    assert any(field["id"] == config_id for field in project_version.config)


def test_failure_before_result_write(experiment, auth_client, monkeypatch):
    def fail(*args, **kwargs):
        assert not rows(experiment).exists()
        raise RuntimeError("Synthetic early failure")

    monkeypatch.setattr("tracer.utils.eval._process_mapping", fail)
    assert run(experiment) is False
    assert_error_result(experiment, auth_client)
    assert rows(experiment, history=True).count() == 1


@pytest.mark.parametrize("outer_transaction", [False, True])
def test_immediate_post_write_failure_has_one_error(
    experiment, auth_client, outer_transaction
):
    from contextlib import nullcontext

    with transaction.atomic() if outer_transaction else nullcontext():
        with fail_after_result_write() as written:
            assert run(experiment) is False
        assert len(written) == 1
        row = assert_error_result(experiment, auth_client)
        assert row.pk != written[0]
        assert not EvalLogger.all_objects.filter(pk=written[0]).exists()
        assert rows(experiment, history=True).count() == 1


@pytest.mark.parametrize("config_owner", ["project", "version"])
def test_actual_malformed_config_branch_has_one_error(
    experiment, auth_client, project, project_version, config_owner
):
    owner = project if config_owner == "project" else project_version
    owner.config = {"not": "a list"}
    owner.save(update_fields=["config"])
    assert run(experiment) is False
    assert_error_result(experiment, auth_client)
    assert rows(experiment, history=True).count() == 1
    owner.refresh_from_db()
    assert owner.config == {"not": "a list"}


def test_database_failure_does_not_break_callers_transaction(experiment, auth_client):
    with transaction.atomic():
        with fail_after_result_write(database_error=True) as written:
            assert run(experiment) is False
        assert len(written) == 1
        assert not connection.needs_rollback
        assert_error_result(experiment, auth_client)
        assert rows(experiment, history=True).count() == 1


def test_outer_rollback_rolls_back_error_result_too(experiment):
    with transaction.atomic():
        with fail_after_result_write():
            assert run(experiment) is False
        assert rows(experiment).count() == 1
        transaction.set_rollback(True)
    assert not rows(experiment, history=True).exists()


def test_explicit_reruns_retain_separate_history(experiment, auth_client):
    assert run(experiment) is True
    first = rows(experiment).get()
    with fail_after_result_write():
        assert run(experiment) is False
    second = assert_error_result(experiment, auth_client)
    assert first.pk != second.pk
    first.refresh_from_db()
    assert first.deleted is True
    assert first.output_bool is True
    assert first.error is False
    assert rows(experiment, history=True).count() == 2
    assert run(experiment) is True
    third = rows(experiment).get()
    assert third.pk not in {first.pk, second.pk}
    assert third.output_bool is True
    assert third.error is False
    second.refresh_from_db()
    assert second.deleted is True
    assert rows(experiment, history=True).count() == 3


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("malformed", [False, True])
def test_localization_follows_accepted_result_outside_savepoint(
    experiment, project, monkeypatch, malformed
):
    seen = []

    def localize(**kwargs):
        assert not connection.in_atomic_block
        row = rows(experiment).get()
        assert row.pk == kwargs["eval_logger"].pk
        project.refresh_from_db()
        assert any(field["id"] == str(experiment[1].pk) for field in project.config)
        seen.append(row.pk)

    monkeypatch.setattr(
        "model_hub.services.error_localizer_service.error_localizer_enabled",
        lambda config: True,
    )
    monkeypatch.setattr(
        "model_hub.tasks.user_evaluation.trigger_error_localization_for_span", localize
    )
    if malformed:
        project.config = {"not": "a list"}
        project.save(update_fields=["config"])
    assert not connection.in_atomic_block
    assert run(experiment) is not malformed
    assert len(seen) == (0 if malformed else 1)
    assert rows(experiment).count() == 1
    assert rows(experiment).get().error is malformed
