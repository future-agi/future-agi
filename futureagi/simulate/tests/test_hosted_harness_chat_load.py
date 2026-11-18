"""Load cases for the hosted-harness chat sandbox lifecycle (TH-9533, PR 4180).

Four groups: history size, concurrency, queue depth and the resend sweep. The CI sizes
run by default; the large sizes carry the ``chat_load`` marker, which the default ``-m``
leaves out (``-m chat_load`` runs them). The provider is faked: a "second sandbox" is a
second call to the fake's start.

Each case appends one JSON object to the file named by ``CHAT_LOAD_REPORT`` (one per
line), or prints it when the variable is not set. Wall time is recorded, never asserted.
What is asserted is the query count, compared with the smallest size run in the same
test, and the invariants (one sandbox, one check, one Stop completion, nothing lost,
at most ``limit`` resends per tick).
"""

from __future__ import annotations

import json
import math
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Lock
from time import perf_counter
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from django.db import close_old_connections, connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from simulate.models import (
    HostedHarnessConversation,
    HostedHarnessConversationEvent,
    HostedHarnessConversationLease,
    HostedHarnessConversationMessage,
    HostedHarnessJob,
)
from simulate.services import hosted_harness
from simulate.services import hosted_harness_conversation as conversation_service
from simulate.services.harness_versioning import rebuild
from simulate.services.hosted_harness import create_hosted_job
from simulate.services.hosted_harness_conversation import (
    COMMAND_LIMIT,
    _unacked_commands,
    enqueue_message,
    latest_turn_finished_well,
    pending_commands,
    platform_landing_pending,
    settle_stop_only_queue,
)
from simulate.tasks import harness_nudges as nudge_tasks
from simulate.tests import test_hosted_harness_check_sandbox as sandbox_tests
from simulate.tests.test_hosted_harness_check_sandbox import (
    ENSURE,
    URL,
    _built,
    _checked,
    _conversation,
    _ensure,
    _finished,
    _gateway,
    _job,
    _lease,
    _names,
    _sandbox,
    _turn,
)
from simulate.tests.test_hosted_harness_conversation import _payload

# The check sandbox tests' fixtures, shared by name.
inline = sandbox_tests.inline

Lease = HostedHarnessConversationLease
Message = HostedHarnessConversationMessage
Event = HostedHarnessConversationEvent
Conversation = HostedHarnessConversation

chat_load = pytest.mark.chat_load

BATCH = 5_000
# The smallest size of each group: what the larger sizes are compared with.
HISTORY_BASELINE_ROWS = 1
QUEUE_BASELINE_DEPTH = 3
SWEEP_LIMIT = 100
# More leases are owed a resend than one tick may send, so the cap is exercised.
SWEEP_MARKED = 150


@pytest.fixture(autouse=True)
def public_url(settings):
    settings.HARNESS_PUBLIC_BASE_URL = URL


def _report(case, size, **fields):
    record = {"case": case, "size": size, **fields}
    line = json.dumps(record, sort_keys=True, default=str)
    path = os.environ.get("CHAT_LOAD_REPORT")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    else:
        print(line)


def _measure(call):
    """The call's result, its query count and its wall time in milliseconds."""
    with CaptureQueriesContext(connection) as captured:
        began = perf_counter()
        result = call()
        wall = (perf_counter() - began) * 1000
    return result, len(captured), round(wall, 3)


def _p95(values):
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)], 3)


def _bulk(model, make, count):
    """``count`` rows made by ``make(index)``, written ``BATCH`` at a time."""
    for first in range(0, count, BATCH):
        model.no_workspace_objects.bulk_create(
            [make(index) for index in range(first, min(first + BATCH, count))],
            batch_size=BATCH,
        )


def _race(count, prepare, work):
    """``work`` on ``count`` threads, each on a connection of its own, released together
    by a barrier. Each thread's result and the milliseconds its own call took."""
    barrier = Barrier(count)

    def run(_index):
        close_old_connections()
        try:
            prepared = prepare()
            barrier.wait(timeout=30)
            began = perf_counter()
            result = work(prepared)
            return result, (perf_counter() - began) * 1000
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=count) as pool:
        futures = [pool.submit(run, index) for index in range(count)]
        outcomes = [future.result(timeout=120) for future in futures]
    return [result for result, _ in outcomes], [took for _, took in outcomes]


# 1. History size


def _seed_history(ctx, rows):
    """``rows`` finished messages and ``rows`` events behind the conversation's build,
    as a long-lived conversation has them. Milliseconds taken."""
    began = perf_counter()
    conversation = _conversation(ctx)
    first_message = conversation.next_message_sequence
    first_event = conversation.event_acked_through + 1
    now = timezone.now()

    def message(index):
        return Message(
            conversation_id=conversation.id,
            client_request_id=f"hist-{index}",
            sequence=first_message + index,
            role=Message.Role.USER,
            kind=Message.Kind.MESSAGE,
            state=Message.State.COMPLETED,
            content=f"earlier message {index}",
            payload={"command_kind": "user_message"},
        )

    def event(index):
        step = index % 3
        if step == 0:
            kind, payload = "turn_started", {"command_message_id": str(uuid4())}
        elif step == 1:
            kind, payload = "checkpoint_committed", {"digest": f"sha256:{index:064x}"}
        else:
            kind = "turn_completed"
            payload = {"command_message_id": str(uuid4()), "outcome": "success"}
        return Event(
            event_id=f"hist-{conversation.id}-{index}",
            conversation_id=conversation.id,
            sequence=first_event + index,
            kind=kind,
            stage="scenarios",
            invocation_id=f"hist-{index // 3}",
            payload=payload,
            digest=f"sha256:{index:064x}",
            emitted_at=now,
        )

    _bulk(Message, message, rows)
    _bulk(Event, event, rows)
    Conversation.no_workspace_objects.filter(id=conversation.id).update(
        next_message_sequence=first_message + rows,
        command_acked_through=first_message + rows - 1,
        next_event_sequence=first_event + rows,
        event_acked_through=first_event + rows - 1,
    )
    return round((perf_counter() - began) * 1000, 1)


def _history_run(organization, capture, rows):
    """One conversation with ``rows`` finished messages and events, whose check turn
    ended, then a Stop and a message queued. The queries and wall time of each step
    the lifecycle takes over it."""
    ctx = _built(organization, capture)
    _finished(ctx)
    seed_ms = _seed_history(ctx, rows)
    hosted_harness._ask_builder_to_check(str(ctx.job.id), str(ctx.attempt.id))
    ctx.check = Message.no_workspace_objects.get(
        conversation=ctx.conversation, client_request_id=f"check-{ctx.attempt.id}"
    )
    _sandbox(ctx, "sandbox-check", after=ctx.build.sequence)
    _turn(ctx, ctx.check)
    assert _lease(ctx).state == Lease.State.EXPIRED

    steps = {}
    stop = enqueue_message(
        ctx.job, content="", client_request_id="stop-1", kind="interrupt"
    )[1]
    settled, *steps["settle_stop_only_queue"] = _measure(
        lambda: settle_stop_only_queue(_conversation(ctx))
    )
    assert settled is True
    stop.refresh_from_db()
    assert stop.state == Message.State.COMPLETED

    go = enqueue_message(ctx.job, content="go", client_request_id="go-1")[1]
    job = _job(ctx)
    conversation = _conversation(ctx)
    pending, *steps["platform_landing_pending"] = _measure(
        lambda: platform_landing_pending(job=job, conversation=conversation)
    )
    assert pending is False
    turn, *steps["latest_turn_finished_well"] = _measure(
        lambda: latest_turn_finished_well(ctx.conversation.id)
    )
    assert turn is not None
    polled, *steps["pending_commands"] = _measure(
        lambda: pending_commands(conversation, after=ctx.check.sequence)
    )
    assert [command["sequence"] for command in polled["commands"]] == [go.sequence]
    ensured, *steps["ensure_conversation_runtime"] = _measure(lambda: _ensure(ctx))
    _result, start, _delete = ensured
    start.assert_called_once()
    return {"seed_ms": seed_ms, "steps": steps}


@pytest.mark.django_db
@pytest.mark.parametrize(
    "rows",
    [10, pytest.param(1_000, marks=chat_load), pytest.param(100_000, marks=chat_load)],
)
def test_history_size_does_not_change_the_query_count(
    organization, inline, django_capture_on_commit_callbacks, rows
):
    capture = django_capture_on_commit_callbacks
    # A first run settles what is cached once per process (and is not compared).
    _history_run(organization, capture, HISTORY_BASELINE_ROWS)
    baseline = _history_run(organization, capture, HISTORY_BASELINE_ROWS)
    sized = _history_run(organization, capture, rows)

    queries = {step: counts[0] for step, counts in sized["steps"].items()}
    baseline_queries = {step: counts[0] for step, counts in baseline["steps"].items()}
    _report(
        "history_size",
        rows,
        queries=queries,
        baseline_rows=HISTORY_BASELINE_ROWS,
        baseline_queries=baseline_queries,
        wall_ms={step: counts[1] for step, counts in sized["steps"].items()},
        baseline_wall_ms={
            step: counts[1] for step, counts in baseline["steps"].items()
        },
        seed_ms=sized["seed_ms"],
    )
    assert queries == baseline_queries


# 2. Concurrency


def _race_ensures(ctx, count):
    """``count`` chat starts for one conversation at once, on a fake provider."""
    gateway = _gateway()
    lock = Lock()
    starts, deletes, settles = [], [], []
    real_settle = conversation_service.settle_stop_only_queue

    def start(*_args, **_kwargs):
        with lock:
            starts.append(1)
        return MagicMock(name="started")

    def delete(provider_ref, _conversation_id):
        with lock:
            deletes.append(provider_ref)
        return True

    def settle(conversation):
        settled = real_settle(conversation)
        if settled:
            with lock:
                settles.append(1)
        return settled

    def prepare():
        return _job(ctx), _conversation(ctx)

    def work(prepared):
        job, conversation = prepared
        return gateway.ensure_conversation_runtime(
            job, conversation=conversation, endpoint_base_url=URL
        )

    with (
        patch.object(gateway, "_start_chat_sandbox", start),
        patch.object(gateway, "_delete_chat_sandbox", delete),
        patch.object(conversation_service, "settle_stop_only_queue", settle),
    ):
        results, took = _race(count, prepare, work)
    return results, took, len(starts), len(deletes), len(settles)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("count", [2, 8, pytest.param(20, marks=chat_load)])
def test_concurrent_starts_on_a_fresh_lease_create_one_sandbox(
    organization, inline, django_capture_on_commit_callbacks, count
):
    ctx = _built(organization, django_capture_on_commit_callbacks)
    _finished(ctx)
    message = enqueue_message(ctx.job, content="go", client_request_id="go-1")[1]

    results, took, starts, deletes, _settles = _race_ensures(ctx, count)

    leases = Lease.no_workspace_objects.filter(conversation=ctx.conversation)
    message.refresh_from_db()
    _report(
        "concurrent_ensure",
        count,
        p95_ms=_p95(took),
        max_ms=round(max(took), 3),
        sandbox_creates=starts,
        deleted_sandboxes=deletes,
        leases=leases.count(),
    )
    assert starts == 1
    assert all(result is not None for result in results)
    assert leases.count() == 1
    assert leases.get().state == Lease.State.STARTING
    # The message waits for the process that is starting; none was lost or settled.
    assert message.state == Message.State.QUEUED


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("count", [2, 8, pytest.param(20, marks=chat_load)])
def test_concurrent_build_check_requests_ask_the_check_once(
    organization, inline, django_capture_on_commit_callbacks, count
):
    ctx = _built(organization, django_capture_on_commit_callbacks)
    _finished(ctx)
    scheduled_before = len(_names(inline, ENSURE))
    job_id, attempt_id = str(ctx.job.id), str(ctx.attempt.id)

    _results, took = _race(
        count,
        lambda: None,
        lambda _prepared: hosted_harness._ask_builder_to_check(job_id, attempt_id),
    )

    checks = Message.no_workspace_objects.filter(
        conversation=ctx.conversation, client_request_id__startswith="check-"
    ).count()
    # Recorded, not asserted: a request that finds the check already asked still
    # wakes the runtime.
    dispatches = len(_names(inline, ENSURE)) - scheduled_before
    _report(
        "concurrent_build_check",
        count,
        p95_ms=_p95(took),
        max_ms=round(max(took), 3),
        check_messages=checks,
        schedule_dispatches=dispatches,
    )
    assert checks == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("count", [2, 8, pytest.param(20, marks=chat_load)])
def test_concurrent_starts_over_a_lone_stop_complete_it_once(
    organization, inline, django_capture_on_commit_callbacks, count
):
    ctx = _checked(organization, django_capture_on_commit_callbacks)
    _turn(ctx, ctx.check)
    assert _lease(ctx).state == Lease.State.EXPIRED
    stop = enqueue_message(
        ctx.job, content="", client_request_id="stop-1", kind="interrupt"
    )[1]

    results, took, starts, deletes, settles = _race_ensures(ctx, count)

    stop.refresh_from_db()
    _report(
        "concurrent_stop",
        count,
        p95_ms=_p95(took),
        max_ms=round(max(took), 3),
        sandbox_creates=starts,
        stop_completions=settles,
        deleted_sandboxes=deletes,
    )
    assert settles == 1
    assert starts == 0
    assert results == [None] * count
    assert stop.state == Message.State.COMPLETED
    assert _conversation(ctx).state == Conversation.State.COLD
    assert _lease(ctx).state == Lease.State.EXPIRED


# 3. Queue depth


def _queue(ctx, depth):
    return [
        enqueue_message(ctx.job, content=f"m{index}", client_request_id=f"q{index}")[1]
        for index in range(depth)
    ]


def _polled(ctx, after):
    conversation = _conversation(ctx)
    # A first poll settles what is cached once per process (and is not compared).
    pending_commands(conversation, after=after)
    return _measure(lambda: pending_commands(conversation, after=after))


@pytest.mark.django_db
@pytest.mark.parametrize(
    "depth", [3, 20, pytest.param(100, marks=chat_load)], ids=lambda value: str(value)
)
def test_queued_messages_all_come_back_in_order_and_none_is_lost(
    organization, inline, django_capture_on_commit_callbacks, depth
):
    # One poll returns at most COMMAND_LIMIT commands.
    assert depth <= COMMAND_LIMIT
    capture = django_capture_on_commit_callbacks

    small = _built(organization, capture)
    _finished(small)
    _queue(small, QUEUE_BASELINE_DEPTH)
    _polled_small, baseline_queries, baseline_wall = _polled(
        small, small.build.sequence
    )

    ctx = _built(organization, capture)
    _finished(ctx)
    messages = _queue(ctx, depth)
    polled, queries, wall = _polled(ctx, ctx.build.sequence)
    assert [command["message_id"] for command in polled["commands"]] == [
        str(message.id) for message in messages
    ]
    assert [command["sequence"] for command in polled["commands"]] == [
        message.sequence for message in messages
    ]

    # One start for all of them; a second ask finds that start pending.
    _result, start, _delete = _ensure(ctx)
    start.assert_called_once()
    _result, again, _delete = _ensure(ctx)
    again.assert_not_called()

    _sandbox(ctx, "sandbox-chat", after=ctx.build.sequence)
    began = perf_counter()
    for message in messages:
        _turn(ctx, message)
    turns_ms = round((perf_counter() - began) * 1000, 1)

    conversation = _conversation(ctx)
    assert conversation.command_acked_through == messages[-1].sequence
    assert not _unacked_commands(conversation).exists()
    states = [Message.no_workspace_objects.get(id=m.id).state for m in messages]
    assert all(
        state in (Message.State.DELIVERED, Message.State.COMPLETED) for state in states
    )

    # The sandbox ends, then a Stop arrives with nothing left to stop.
    Lease.no_workspace_objects.filter(conversation=ctx.conversation).update(
        state=Lease.State.EXPIRED
    )
    Conversation.no_workspace_objects.filter(id=ctx.conversation.id).update(
        state=Conversation.State.COLD
    )
    stop = enqueue_message(
        ctx.job, content="", client_request_id="stop-1", kind="interrupt"
    )[1]
    result, third, _delete = _ensure(ctx)
    stop.refresh_from_db()

    _report(
        "queue_depth",
        depth,
        queries={"pending_commands": queries},
        baseline_depth=QUEUE_BASELINE_DEPTH,
        baseline_queries={"pending_commands": baseline_queries},
        wall_ms={"pending_commands": wall},
        baseline_wall_ms={"pending_commands": baseline_wall},
        turns_ms=turns_ms,
        sandbox_starts=1,
        commands_returned=len(polled["commands"]),
        messages_acked=conversation.command_acked_through,
        stop_state=stop.state,
    )
    assert queries == baseline_queries
    assert result is None
    third.assert_not_called()
    assert stop.state == Message.State.COMPLETED


# 4. The resend sweep


def _seed_expired_leases(organization, template, count, marked, tag):
    """``count`` expired chat leases, the first ``marked`` of them marked as owing a
    stop (by the real mark). Each needs a job and a conversation of its own."""
    expires = timezone.now() - timedelta(minutes=10)
    named = []

    for first in range(0, count, BATCH):
        indexes = range(first, min(first + BATCH, count))
        jobs = [
            HostedHarnessJob(
                organization=organization,
                run_id=uuid4(),
                idempotency_key=f"{tag}-{index}",
                request_digest=template.request_digest,
                schema_version=template.schema_version,
                payload=template.payload,
                state=HostedHarnessJob.State.COMPLETED,
                seed=template.seed,
                scenario_count=template.scenario_count,
                artifact_level=template.artifact_level,
                max_artifact_bytes=template.max_artifact_bytes,
                deadline_at=template.deadline_at,
            )
            for index in indexes
        ]
        HostedHarnessJob.no_workspace_objects.bulk_create(jobs, batch_size=BATCH)
        conversations = [
            Conversation(job_id=job.id, organization_id=organization.id) for job in jobs
        ]
        Conversation.no_workspace_objects.bulk_create(conversations, batch_size=BATCH)
        leases = [
            Lease(
                conversation_id=conversation.id,
                provider_ref=f"{tag}-sandbox-{index}",
                state=Lease.State.EXPIRED,
                token_hash="0" * 64,
                fence_hash="",
                expires_at=expires,
            )
            for index, conversation in zip(indexes, conversations, strict=True)
        ]
        Lease.no_workspace_objects.bulk_create(leases, batch_size=BATCH)
        if len(named) < marked:
            named.extend(
                (str(lease.conversation_id), lease.provider_ref) for lease in leases
            )
    for conversation_id, ref in named[:marked]:
        rebuild._mark_sandbox_stop_unsent(conversation_id, ref)
    # The planner has no statistics for rows that were just written.
    with connection.cursor() as cursor:
        cursor.execute(f"ANALYZE {Lease._meta.db_table}")


def _sweep_ticks(sent_log):
    """Three ticks of the sweep: how many each sent, its queries and wall time."""
    ticks = []
    for _ in range(3):
        before = len(sent_log)
        sent, queries, wall = _measure(
            lambda: rebuild.resend_unsent_sandbox_stops(limit=SWEEP_LIMIT)
        )
        assert len(sent_log) - before == sent
        ticks.append({"sent": sent, "queries": queries, "wall_ms": wall})
    return ticks


@pytest.mark.django_db
@pytest.mark.parametrize(
    "leases",
    [100, pytest.param(1_000, marks=chat_load), pytest.param(100_000, marks=chat_load)],
)
def test_the_resend_sweep_sends_at_most_its_limit_per_tick_at_any_table_size(
    organization, inline, monkeypatch, leases
):
    template, _ = create_hosted_job(
        organization, _payload(), idempotency_key=f"sweep-template-{uuid4()}"
    )
    sent_log = []
    monkeypatch.setattr(
        nudge_tasks,
        "schedule_sandbox_stop",
        lambda conversation_id, ref: sent_log.append((conversation_id, ref)),
    )
    # The window reads settings once per process (and is not compared).
    rebuild._sandbox_stop_resend_window()

    # The smallest table: only the leases that owe a stop.
    _seed_expired_leases(organization, template, SWEEP_MARKED, SWEEP_MARKED, "small")
    baseline = _sweep_ticks(sent_log)
    sent_log.clear()

    # The same owed leases among `leases` that owe nothing.
    began = perf_counter()
    _seed_expired_leases(
        organization, template, leases + SWEEP_MARKED, SWEEP_MARKED, "large"
    )
    seed_ms = round((perf_counter() - began) * 1000, 1)
    sized = _sweep_ticks(sent_log)

    _report(
        "resend_sweep",
        leases,
        limit=SWEEP_LIMIT,
        owed=SWEEP_MARKED,
        sent_per_tick=[tick["sent"] for tick in sized],
        queries=[tick["queries"] for tick in sized],
        wall_ms=[tick["wall_ms"] for tick in sized],
        baseline_queries=[tick["queries"] for tick in baseline],
        baseline_wall_ms=[tick["wall_ms"] for tick in baseline],
        resends_total=len(sent_log),
        seed_ms=seed_ms,
    )
    for ticks in (baseline, sized):
        assert all(tick["sent"] <= SWEEP_LIMIT for tick in ticks)
        assert [tick["sent"] for tick in ticks] == [
            SWEEP_LIMIT,
            SWEEP_MARKED - SWEEP_LIMIT,
            0,
        ]
    assert len(sent_log) == SWEEP_MARKED
    assert [tick["queries"] for tick in sized] == [tick["queries"] for tick in baseline]
