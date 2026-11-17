"""Read-after-write paths of the replica-opted-in tracer models (TH-3119).

Every test runs with the nine models in READ_REPLICA_OPT_IN and a lagging
replica (`tfc.tests.lagging_replica`): a real second connection whose view is
frozen before the code under test runs, so reads the router sends to it cannot
see the writes that code makes.
"""

import pytest

from tfc.tests.lagging_replica import lagging_replica  # noqa: F401  (fixture)
from tracer.models.trace import Trace


@pytest.mark.django_db(transaction=True)
def test_lagging_replica_serves_applied_rows_and_misses_later_writes(
    lagging_replica,  # noqa: F811
    project,
    project_version,
):
    applied = Trace.objects.create(project=project, project_version=project_version)
    lagging_replica.freeze()
    late = Trace.objects.create(project=project, project_version=project_version)

    with lagging_replica.capture() as replica_queries:
        assert Trace.objects.filter(pk=applied.pk).exists()
        assert not Trace.objects.filter(pk=late.pk).exists()
    assert len(replica_queries.captured_queries) == 2

    assert Trace.objects.using("default").filter(pk=late.pk).exists()


# -- eval task create / edit: configs saved a moment before ------------------


@pytest.mark.django_db(transaction=True)
def test_new_task_accepts_eval_configs_created_after_the_replica_froze(
    lagging_replica,  # noqa: F811
    project,
):
    from tracer.serializers.eval_task import EvalTaskSerializer
    from tracer.tests.eval_task_factories import make_config, make_template

    template = make_template(
        organization=project.organization, workspace=project.workspace
    )
    lagging_replica.freeze()
    config = make_config(project=project, template=template, name="new config")

    field = EvalTaskSerializer().fields["evals"]
    assert [c.id for c in field.to_internal_value([str(config.id)])] == [config.id]


@pytest.mark.django_db(transaction=True)
def test_task_edit_accepts_eval_configs_created_after_the_replica_froze(
    lagging_replica,  # noqa: F811
    project,
):
    from tracer.serializers.eval_task import EditEvalTaskSerializer
    from tracer.tests.eval_task_factories import make_config, make_template

    template = make_template(
        organization=project.organization, workspace=project.workspace
    )
    lagging_replica.freeze()
    config = make_config(project=project, template=template, name="new config")

    assert EditEvalTaskSerializer().validate_evals([config.id]) == [config.id]


# -- span ingestion: a trace's second export batch ---------------------------


@pytest.mark.django_db(transaction=True)
def test_second_batch_of_a_trace_reuses_the_trace_the_first_batch_created(
    lagging_replica,  # noqa: F811
    project,
    project_version,
):
    import uuid

    from django.db import transaction

    from tracer.utils.trace_ingestion import _fetch_or_create_traces

    trace_id = uuid.uuid4()
    batch = [
        {
            "trace": str(trace_id),
            "project": project,
            "project_version": project_version,
        }
    ]
    lagging_replica.freeze()
    with transaction.atomic():
        _fetch_or_create_traces(batch)  # first export batch creates the trace
    with transaction.atomic():
        traces = _fetch_or_create_traces(batch)  # next batch, moments later

    assert traces[trace_id].id == trace_id
    assert Trace.objects.using("default").filter(id=trace_id).count() == 1


# -- eval-task engine: an entry claimed moments before it runs ----------------


def _claimed_entry(task, span, config):
    from tracer.models.observation_span import (
        EvalEntryStatus,
        EvalLogger,
        EvalTargetType,
    )

    return EvalLogger.objects.create(
        target_type=EvalTargetType.SPAN,
        observation_span=span,
        trace=span.trace,
        custom_eval_config=config,
        eval_task_id=str(task.id),
        status=EvalEntryStatus.RUNNING,
    )


@pytest.mark.django_db(transaction=True)
def test_run_entry_runs_an_entry_claimed_after_the_replica_froze(
    lagging_replica,  # noqa: F811
    observation_span,
    custom_eval_config,
    eval_task,
    stub_run_eval,
    stub_cost_log,
):
    from tracer.models.observation_span import EvalEntryStatus, EvalLogger
    from tracer.services.eval_tasks.run_entry import run_entry

    lagging_replica.freeze()
    entry = _claimed_entry(eval_task, observation_span, custom_eval_config)

    assert run_entry(entry) == EvalEntryStatus.COMPLETED
    assert (
        EvalLogger.objects.using("default").get(id=entry.id).status
        == EvalEntryStatus.COMPLETED
    )


@pytest.mark.django_db(transaction=True)
def test_run_entry_activity_finds_an_entry_claimed_after_the_replica_froze(
    lagging_replica,  # noqa: F811
    monkeypatch,
    observation_span,
    custom_eval_config,
    eval_task,
):
    from tfc.temporal.eval_tasks.activities import _run_entry_sync

    ran = []
    monkeypatch.setattr(
        "tracer.services.eval_tasks.run_entry.run_entry",
        lambda entry: ran.append(entry.id) or "completed",
    )
    lagging_replica.freeze()
    entry = _claimed_entry(eval_task, observation_span, custom_eval_config)

    result = _run_entry_sync(str(entry.id))

    assert result["status"] == "completed"
    assert ran == [entry.id]
