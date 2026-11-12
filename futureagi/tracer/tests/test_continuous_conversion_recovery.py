"""Conversion preserves continuous activation across a failed first dispatch.

Real PATCH, PostgreSQL, ClickHouse, Temporal, sweeper and reconciler; only paid
computation is replaced with deterministic completion. Existing historical
results must remain unchanged and arrivals before conversion must be excluded,
including for rows a pre-deploy conversion or Delete & rerun left without a
persisted activation floor.
"""

import asyncio
import uuid
from datetime import timedelta

import pytest
from asgiref.sync import sync_to_async
from django.core.cache import cache
from django.db import close_old_connections, connection
from django.utils import timezone
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from tfc.temporal.eval_tasks import client as workflow_client
from tfc.temporal.eval_tasks import get_activities, get_workflows
from tfc.temporal.eval_tasks.search_attributes import ALL_KEYS
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RowType, RunType
from tracer.models.observation_span import (
    EvalEntryStatus,
    EvalLogger,
    EvalTargetType,
    ObservationSpan,
)
from tracer.models.trace import Trace
from tracer.services.eval_tasks.config_hash import resolved_config_hash
from tracer.services.eval_tasks.entries import soft_delete_live
from tracer.services.eval_tasks.recovery import recover_stranded_tasks
from tracer.tests._ch_seed import seed_ch_spans, truncate_ch_spans

_REAL_START = workflow_client.start_eval_task_workflow_sync


async def wait_until(fn, timeout=100):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        value = await sync_to_async(fn)()
        if value:
            return value
        await asyncio.sleep(0.25)
    raise AssertionError(f"Timed out waiting for {fn}")


def seed_case(
    project, custom_eval_config, activation, age_days, names=("ordinary", "recovered")
):
    spans = {}
    for label, arrived in {
        "historical": activation - timedelta(days=age_days) + timedelta(minutes=1),
        "pre_conversion": activation - timedelta(days=1),
        "post_conversion": activation + timedelta(minutes=1),
    }.items():
        trace = Trace.objects.create(project=project, name=f"qa3797-{label}")
        span = ObservationSpan.objects.create(
            id=f"qa3797-{label}-{uuid.uuid4().hex[:10]}",
            project=project,
            trace=trace,
            name=label,
            observation_type="llm",
            parent_span_id="",
            start_time=arrived,
            end_time=arrived + timedelta(seconds=1),
            input={"input": "synthetic input"},
            output={"output": "synthetic output"},
        )
        ObservationSpan.all_objects.filter(id=span.id).update(
            created_at=arrived, updated_at=arrived
        )
        span.refresh_from_db()
        spans[label] = span
    seed_ch_spans(spans.values(), version_from_created_at=True)
    cases = {}
    for name in names:
        cfg = CustomEvalConfig.objects.create(
            name=f"qa3797-{name}",
            project=project,
            eval_template=custom_eval_config.eval_template,
            config={},
            mapping={"input": "input"},
            filters={},
        )
        task = EvalTask.objects.create(
            project=project,
            name=f"qa3797-{name}",
            filters={},
            sampling_rate=100.0,
            spans_limit=1,
            run_type=RunType.HISTORICAL,
            status=EvalTaskStatus.COMPLETED,
            row_type=RowType.SPANS,
            start_time=activation - timedelta(days=age_days),
            end_time=activation - timedelta(days=age_days) + timedelta(hours=1),
        )
        task.evals.add(cfg)
        old = EvalLogger.objects.create(
            target_type=EvalTargetType.SPAN,
            observation_span=spans["historical"],
            trace=spans["historical"].trace,
            custom_eval_config=cfg,
            eval_task_id=str(task.id),
            status=EvalEntryStatus.COMPLETED,
            config_hash=resolved_config_hash(cfg),
            error=False,
        )
        cases[name] = (
            task,
            old.pk,
            EvalLogger.all_objects.filter(pk=old.pk).values().get(),
        )
    return spans, cases


def read_case(task, old_id, spans):
    task = EvalTask.no_workspace_objects.get(pk=task.pk)
    entries = list(EvalLogger.all_objects.filter(eval_task_id=str(task.id)).values())
    id_to_label = {s.id: k for k, s in spans.items()}
    return {
        "start_time": task.start_time,
        "cursor": task.continuous_cursor,
        "completed": sorted(
            id_to_label[r["observation_span_id"]]
            for r in entries
            if r["status"] == "completed"
        ),
        "live_completed": sorted(
            id_to_label[r["observation_span_id"]]
            for r in entries
            if r["status"] == "completed" and not r["deleted"]
        ),
        "existing": EvalLogger.all_objects.filter(pk=old_id).values().get(),
        "entries": [
            (id_to_label[r["observation_span_id"]], r["status"]) for r in entries
        ],
    }


@pytest.mark.e2e
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "age_days", [30, 7], ids=["historical_month", "historical_week"]
)
async def test_conversion_recovery_excludes_pre_activation_arrivals(
    auth_client, project, custom_eval_config, monkeypatch, age_days
):
    activation = timezone.now().replace(microsecond=0)
    clock = {"now": activation}
    monkeypatch.setattr(timezone, "now", lambda: clock["now"])
    spans, cases = await sync_to_async(seed_case)(
        project, custom_eval_config, activation, age_days
    )
    paid_calls = []

    def complete_synthetic_eval(entry):
        paid_calls.append((entry.eval_task_id, entry.observation_span_id))
        EvalLogger.all_objects.filter(id=entry.id).update(
            status=EvalEntryStatus.COMPLETED, error=False
        )
        return EvalEntryStatus.COMPLETED

    monkeypatch.setattr(
        "tracer.services.eval_tasks.run_entry.run_entry", complete_synthetic_eval
    )
    monkeypatch.setattr(workflow_client, "start_eval_task_workflow_sync", _REAL_START)
    env = await WorkflowEnvironment.start_local(search_attributes=ALL_KEYS)
    target = env.client.service_client.config.target_host

    async def local_client():
        return await Client.connect(target, namespace=env.client.namespace)

    monkeypatch.setattr("tfc.temporal.common.client.get_client", local_client)
    observations = {}
    try:
        async with Worker(
            env.client,
            task_queue="tasks_s",
            workflows=get_workflows(),
            activities=get_activities(),
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            try:
                for name in ("ordinary", "recovered"):
                    task, old_id, old_snapshot = cases[name]
                    clock["now"] = activation
                    dispatched_after_commit = []

                    def dispatch(
                        current,
                        *,
                        name=name,
                        observed=dispatched_after_commit,
                        **kwargs,
                    ):
                        assert not connection.in_atomic_block
                        observed.append(current.status)
                        if name == "recovered":
                            raise RuntimeError(
                                "QA injected post-commit Temporal outage"
                            )
                        return _REAL_START(current, **kwargs)

                    monkeypatch.setattr(
                        "tracer.views.eval_task.start_eval_task_workflow_sync", dispatch
                    )
                    response = await sync_to_async(auth_client.patch)(
                        "/tracer/eval-task/update_eval_task/",
                        {
                            "eval_task_id": str(task.id),
                            "name": task.name,
                            "run_type": "continuous",
                            "edit_type": "edit_rerun",
                        },
                        format="json",
                    )
                    expected_code = 200 if name == "ordinary" else 400
                    assert response.status_code == expected_code, (
                        response.content.decode()
                    )
                    assert dispatched_after_commit == [EvalTaskStatus.PENDING]
                    if name == "ordinary":
                        await wait_until(
                            lambda task=task: EvalTask.no_workspace_objects.filter(
                                pk=task.pk, status="running"
                            ).exists()
                        )
                    else:
                        saved = await sync_to_async(EvalTask.no_workspace_objects.get)(
                            pk=task.pk
                        )
                        assert (
                            saved.status == "pending" and saved.run_type == "continuous"
                        )
                        assert saved.continuous_cursor is None
                    clock["now"] = activation + timedelta(hours=1)
                    if name == "recovered":
                        await sync_to_async(cache.delete)("eval-task-sweep:position:v1")
                        outcome = await sync_to_async(
                            recover_stranded_tasks, thread_sensitive=False
                        )(limit=100, stale_running_seconds=7200)
                        assert outcome["idle_continuous_candidates"] == 1, outcome
                        assert outcome["restarted"] == 1 and outcome["errors"] == 0, (
                            outcome
                        )
                    await wait_until(
                        lambda task=task: EvalLogger.all_objects.filter(
                            eval_task_id=str(task.id),
                            observation_span_id=spans["post_conversion"].id,
                            status="completed",
                        ).exists()
                    )
                    await wait_until(
                        lambda task=task: (
                            not EvalLogger.all_objects.filter(
                                eval_task_id=str(task.id),
                                status__in=["pending", "running"],
                            ).exists()
                        )
                    )
                    observations[name] = await sync_to_async(read_case)(
                        task, old_id, spans
                    )
                    assert observations[name]["existing"] == old_snapshot, (
                        "Historical paid result was changed"
                    )
                    await env.client.get_workflow_handle(
                        f"eval-task-{task.id}"
                    ).terminate("QA scenario finished")
                    await sync_to_async(
                        EvalTask.no_workspace_objects.filter(pk=task.pk).update
                    )(status="paused")
                print(
                    "QA3797_CONVERSION",
                    age_days,
                    {
                        k: {f: v for f, v in obs.items() if f != "existing"}
                        for k, obs in observations.items()
                    },
                    "evaluator_calls",
                    len(paid_calls),
                )
                assert observations["ordinary"]["completed"] == [
                    "historical",
                    "post_conversion",
                ]
                assert observations["recovered"]["completed"] == [
                    "historical",
                    "post_conversion",
                ], (
                    "Recovered conversion evaluated pre-activation history; ordinary conversion did not. "
                    f"ordinary={observations['ordinary']['completed']} "
                    f"recovered={observations['recovered']['completed']} "
                    f"activation={activation.isoformat()} recovered_floor={observations['recovered']['start_time']}"
                )
            finally:
                for task, _, _ in cases.values():
                    try:
                        await env.client.get_workflow_handle(
                            f"eval-task-{task.id}"
                        ).terminate("QA cleanup")
                    except Exception:
                        pass
    finally:
        await env.shutdown()
        await sync_to_async(truncate_ch_spans)()
        await sync_to_async(close_old_connections)()


def write_pre_deploy_shape(task, activation, age_days, shape):
    """The row the code before this deploy left when the edit's dispatch failed.

    The edit cleared the cursor and stamped ``last_run`` but persisted no
    activation floor: ``start_time`` is still the historical run's
    (``conversion``) or that of the activation a Delete & rerun replaced
    (``rerun``, whose earlier result the rerun soft-deleted).
    """
    fields = {}
    if shape == "rerun":
        soft_delete_live(task)
        fields["end_time"] = None
    EvalTask.no_workspace_objects.filter(pk=task.pk).update(
        run_type=RunType.CONTINUOUS,
        status=EvalTaskStatus.PENDING,
        start_time=activation - timedelta(days=age_days),
        continuous_cursor=None,
        last_run=activation,
        **fields,
    )


@pytest.mark.e2e
@pytest.mark.django_db(transaction=True)
async def test_pre_deploy_rows_recover_from_their_last_acceptance(
    auth_client, project, custom_eval_config, monkeypatch
):
    age_days = 30
    shapes = ("conversion", "rerun")
    activation = timezone.now().replace(microsecond=0)
    clock = {"now": activation}
    monkeypatch.setattr(timezone, "now", lambda: clock["now"])
    spans, cases = await sync_to_async(seed_case)(
        project, custom_eval_config, activation, age_days, names=shapes
    )
    paid_calls = []

    def complete_synthetic_eval(entry):
        paid_calls.append(entry.eval_task_id)
        EvalLogger.all_objects.filter(id=entry.id).update(
            status=EvalEntryStatus.COMPLETED, error=False
        )
        return EvalEntryStatus.COMPLETED

    monkeypatch.setattr(
        "tracer.services.eval_tasks.run_entry.run_entry", complete_synthetic_eval
    )
    monkeypatch.setattr(workflow_client, "start_eval_task_workflow_sync", _REAL_START)
    env = await WorkflowEnvironment.start_local(search_attributes=ALL_KEYS)
    target = env.client.service_client.config.target_host

    async def local_client():
        return await Client.connect(target, namespace=env.client.namespace)

    monkeypatch.setattr("tfc.temporal.common.client.get_client", local_client)
    observations = {}
    try:
        async with Worker(
            env.client,
            task_queue="tasks_s",
            workflows=get_workflows(),
            activities=get_activities(),
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            try:
                for shape in shapes:
                    task, old_id, _ = cases[shape]
                    clock["now"] = activation
                    await sync_to_async(write_pre_deploy_shape)(
                        task, activation, age_days, shape
                    )
                    old_snapshot = await sync_to_async(
                        lambda old_id=old_id: (
                            EvalLogger.all_objects.filter(pk=old_id).values().get()
                        )
                    )()
                    clock["now"] = activation + timedelta(hours=1)
                    await sync_to_async(cache.delete)("eval-task-sweep:position:v1")
                    outcome = await sync_to_async(
                        recover_stranded_tasks, thread_sensitive=False
                    )(limit=100, stale_running_seconds=7200)
                    assert outcome["idle_continuous_candidates"] == 1, outcome
                    assert outcome["restarted"] == 1 and outcome["errors"] == 0, outcome
                    await wait_until(
                        lambda task=task: EvalLogger.all_objects.filter(
                            eval_task_id=str(task.id),
                            observation_span_id=spans["post_conversion"].id,
                            status="completed",
                            deleted=False,
                        ).exists()
                    )
                    await wait_until(
                        lambda task=task: (
                            not EvalLogger.all_objects.filter(
                                eval_task_id=str(task.id),
                                status__in=["pending", "running"],
                            ).exists()
                        )
                    )
                    observations[shape] = await sync_to_async(read_case)(
                        task, old_id, spans
                    )
                    assert observations[shape]["existing"] == old_snapshot, (
                        "Earlier paid result was changed"
                    )
                    await env.client.get_workflow_handle(
                        f"eval-task-{task.id}"
                    ).terminate("QA scenario finished")
                    await sync_to_async(
                        EvalTask.no_workspace_objects.filter(pk=task.pk).update
                    )(status="paused")
                calls = {
                    shape: paid_calls.count(str(cases[shape][0].id)) for shape in shapes
                }
                print(
                    "QA3797_PRE_DEPLOY",
                    {
                        k: {f: v for f, v in obs.items() if f != "existing"}
                        for k, obs in observations.items()
                    },
                    "evaluator_calls",
                    calls,
                )
                floors = {k: obs["start_time"] for k, obs in observations.items()}
                assert observations["conversion"]["completed"] == [
                    "historical",
                    "post_conversion",
                ], f"Pre-deploy conversion backfilled history; floors={floors}"
                assert observations["rerun"]["live_completed"] == ["post_conversion"], (
                    f"Pre-deploy rerun backfilled history; floors={floors}"
                )
                assert calls == {"conversion": 1, "rerun": 1}, calls
                assert floors == {"conversion": activation, "rerun": activation}
            finally:
                for task, _, _ in cases.values():
                    try:
                        await env.client.get_workflow_handle(
                            f"eval-task-{task.id}"
                        ).terminate("QA cleanup")
                    except Exception:
                        pass
    finally:
        await env.shutdown()
        await sync_to_async(truncate_ch_spans)()
        await sync_to_async(close_old_connections)()
