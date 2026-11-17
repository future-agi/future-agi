"""Workers must not inherit request pinning or read lagging replica snapshots."""

from contextvars import Context
from unittest.mock import Mock, patch

import pytest
from django.db import connections, router, transaction
from django.db.models.sql.compiler import SQLCompiler
from django.db.models.sql.constants import MULTI
from django.utils import timezone

from model_hub.models.pairwise_judges import PairwiseJudgeItem, PairwiseJudgeJob
from model_hub.services import pairwise_judges as service
from model_hub.tasks import pairwise_judges as tasks
from model_hub.tests.test_pairwise_judges import start
from tfc import routers

pytest_plugins = ["model_hub.tests.pairwise_judge_support"]

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
    # Shared through pytest_plugins, so modules opt in instead of autouse.
    pytest.mark.usefixtures("dispatch"),
]


@pytest.fixture
def lagging_replica(monkeypatch):
    # Use the production router and real ORM queries. Only the replica SQL
    # boundary is replaced with an empty, pre-commit snapshot; primary SQL
    # still executes on the test database. No second database is required.
    monkeypatch.setattr(routers, "_replica_configured", lambda: True)
    monkeypatch.setattr(
        router,
        "routers",
        [routers.ReadReplicaRouter(opt_in=["ALL_MODELS_USE_READ_REPLICA"])],
    )
    monkeypatch.setattr(
        connections._connections, "replica", connections["default"], raising=False
    )
    execute = SQLCompiler.execute_sql
    reads = []

    def replica_snapshot(compiler, result_type=MULTI, **kwargs):
        if compiler.using == "replica":
            reads.append(compiler.query.model.__name__)
            compiler.setup_query()
            return [] if result_type == MULTI else None
        return execute(compiler, result_type=result_type, **kwargs)

    monkeypatch.setattr(SQLCompiler, "execute_sql", replica_snapshot)
    return reads


@pytest.mark.parametrize("entry", ["service", "activity"])
def test_committed_items_dispatch_and_settle_without_replica_reads(
    auth_client,
    prepared,
    dispatch,
    monkeypatch,
    request,
    entry,
):
    # Retain the committed request, but deliver its activity separately with
    # a fresh Context, as a worker does instead of inheriting the view's pin.
    with patch.object(tasks.dispatch_pairwise_job, "apply_async") as queued:
        job = start(auth_client, prepared)
    assert queued.call_args.kwargs == {"args": (str(job.pk),), "queue": "tasks_s"}
    assert queued.call_count == 1
    ids = sorted(str(pk) for pk in job.items.values_list("pk", flat=True))
    finished = timezone.now()
    reads = request.getfixturevalue("lagging_replica")
    assert routers._is_force_primary() is False
    assert router.db_for_read(PairwiseJudgeItem) == "replica"
    # Demonstrate the failure condition, including the atomic-block trap.
    with transaction.atomic():
        assert list(PairwiseJudgeItem.objects.filter(job=job)) == []
    assert reads == ["PairwiseJudgeItem"]
    reads.clear()

    dispatch_fn, execute_fn = service.dispatch_job, service.execute_item
    if entry == "activity":
        # Exercise the registered activity boundary independently of the
        # service decorators, so both public entry layers are protected.
        monkeypatch.setattr(
            tasks,
            "dispatch_job",
            getattr(service.dispatch_job, "__wrapped__", service.dispatch_job),
        )
        monkeypatch.setattr(
            tasks,
            "execute_item",
            getattr(service.execute_item, "__wrapped__", service.execute_item),
        )
        dispatch_fn = tasks.dispatch_pairwise_job.run_sync
        execute_fn = tasks.evaluate_pairwise_item.run_sync

    Context().run(dispatch_fn, str(job.pk))
    assert sorted(call.kwargs["args"][0] for call in dispatch.call_args_list) == ids
    assert [call.kwargs["queue"] for call in dispatch.call_args_list] == ["tasks_s"] * 4
    evaluate = Mock(
        return_value={
            "output": "tie",
            "reason": "Equivalent",
            "log_id": "saved-log",
        }
    )
    with (
        patch.object(service, "run_judge", evaluate),
        patch.object(service.timezone, "now", return_value=finished),
    ):
        for index, item_id in enumerate(ids):
            Context().run(execute_fn, item_id)
            state = PairwiseJudgeJob.objects.using("default").get(pk=job.pk)
            assert state.status == ("completed" if index == 3 else "queued")
            assert state.finished_at == (finished if index == 3 else None)
    assert evaluate.call_count == 4
    assert (
        list(
            PairwiseJudgeItem.objects.using("default")
            .filter(job=job)
            .order_by("pk")
            .values_list("status", "winner", "reason", "log_id", "finished_at")
        )
        == [("completed", "tie", "Equivalent", "saved-log", finished)] * 4
    )
    assert reads == []
    assert routers._is_force_primary() is False
    assert router.db_for_read(PairwiseJudgeItem) == "replica"


def test_dispatch_failure_settles_from_primary_outside_a_request(
    auth_client,
    prepared,
    request,
):
    with patch.object(tasks.dispatch_pairwise_job, "apply_async"):
        job = start(auth_client, prepared)
    reads = request.getfixturevalue("lagging_replica")
    with patch.object(
        tasks.dispatch_pairwise_job, "apply_async", side_effect=RuntimeError("offline")
    ):
        Context().run(service.enqueue_job, job.pk)
    assert PairwiseJudgeJob.objects.using("default").get(pk=job.pk).status == "failed"
    assert (
        list(
            PairwiseJudgeItem.objects.using("default")
            .filter(job=job)
            .values_list("status", "error")
        )
        == [("failed", "dispatch_failed")] * 4
    )
    assert reads == []
    assert routers._is_force_primary() is False
