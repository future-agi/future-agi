"""Chat, rebuild and fix-engine sandboxes are timed until their deletion is confirmed,
and each is billed once, free when it ended on our failure."""

from __future__ import annotations

from datetime import UTC, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID, uuid4, uuid5

import pytest
from django.db import OperationalError, connection, transaction
from django.db.utils import DataError
from django.utils import timezone as django_timezone
from rest_framework.test import APIClient
from temporalio.common import WorkflowIDConflictPolicy

from accounts.models import Organization
from simulate.models import (
    HarnessNudge,
    HostedHarnessConversation,
    HostedHarnessConversationLease,
    HostedHarnessConversationMessage,
    HostedHarnessJob,
    HostedSandboxLifetime,
)
from simulate.services import sandbox_usage
from simulate.services.harness_versioning import rebuild
from simulate.services.hosted_harness import canonical_digest, create_hosted_job
from simulate.services.hosted_harness_conversation import (
    ensure_conversation,
    issue_conversation_capability,
)
from simulate.tasks import harness_nudges as nudge_tasks
from simulate.tasks import hosted_harness_usage as usage_tasks
from simulate.tests.test_harness_agent_versions import (  # noqa: F401 - fixture
    scheduled as scheduled,
)
from simulate.tests.test_harness_nudges import (  # noqa: F401 - fixture
    started as started,
)
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)
from simulate.tests.test_harness_versioning_rebuild import (
    GUEST,
    _queued,
)
from simulate.tests.test_harness_versioning_rebuild import (  # noqa: F401 - fixture
    inline as inline,
)
from simulate.tests.test_hosted_harness_channels import _payload
from simulate.tests.test_hosted_harness_conversation import _headers

MINUTE = timedelta(minutes=1)
ACTIVE = HostedHarnessConversationLease.State.ACTIVE
EXPIRED = HostedHarnessConversationLease.State.EXPIRED
STARTING = HostedHarnessConversationLease.State.STARTING
NO_COUNTS = {"expired": 0, "settled": 0, "waiting": 0, "errors": 0}


# --- fixtures and builders -----------------------------------------------------------


@pytest.fixture
def cloud(monkeypatch):
    """A deployment with billing: rows are written."""
    monkeypatch.setattr(sandbox_usage, "is_oss", lambda: False)


@pytest.fixture
def billed(cloud, monkeypatch):
    """The events the seal sends, recorded at the emitter."""
    emitter = pytest.importorskip("ee.usage.services.emitter")
    events = []

    def emit(event, *, raise_on_failure=False):
        if event.event_type != "sandbox_runtime":
            return
        # A seal only marks a charge sent when the stream write raised nothing.
        assert raise_on_failure is True
        events.append(event)

    monkeypatch.setattr(emitter, "emit", emit)
    return events


@pytest.fixture
def seals(monkeypatch):
    """Seal enqueues recorded at the Temporal boundary instead of connecting to it."""
    requests = []
    monkeypatch.setattr(
        usage_tasks.seal_hosted_sandbox_lifetime,
        "apply_async",
        lambda args, **options: requests.append((list(args), options)),
    )
    return requests


@pytest.fixture
def clock(monkeypatch):
    """Sets the lifecycle's own clock; ``None`` puts the real one back."""

    def set_to(moment):
        monkeypatch.setattr(
            sandbox_usage,
            "timezone",
            django_timezone if moment is None else SimpleNamespace(now=lambda: moment),
        )

    return set_to


@pytest.fixture
def stops(monkeypatch):
    """The sandbox stops and rebuild checks a closed rebuild asks for, recorded."""
    requests = []
    monkeypatch.setattr(
        nudge_tasks,
        "schedule_sandbox_stop",
        lambda conversation_id, ref: requests.append((str(conversation_id), ref)),
    )
    monkeypatch.setattr(nudge_tasks, "schedule_nudge_check_task", lambda _id: None)
    return requests


def _now():
    return django_timezone.now()


def _environment(organization):
    job, _ = create_hosted_job(organization, _payload(), idempotency_key=str(uuid4()))
    return job, ensure_conversation(job)


def _row(
    organization,
    *,
    start,
    lifetime=30 * 60,
    ended=None,
    kind=HostedSandboxLifetime.Kind.CHAT,
    provider="daytona",
    owner_id=None,
    end_reason="",
    failure_domain="",
    workspace=None,
):
    return HostedSandboxLifetime.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        kind=kind,
        provider=provider,
        provider_ref=f"sandbox-{uuid4()}",
        owner_id=owner_id or uuid4(),
        started_at=start,
        lifetime_seconds=lifetime,
        armed_at=start,
        lifetime_ends_at=start + timedelta(seconds=lifetime),
        ended_at=ended,
        end_reason=end_reason,
        failure_domain=failure_domain,
    )


def _open(organization, ref, *, armed_at, lifetime=30 * 60):
    sandbox_usage.open_sandbox_lifetime(
        kind=HostedSandboxLifetime.Kind.CHAT,
        provider_name="daytona",
        provider_ref=ref,
        organization_id=organization.id,
        workspace_id=None,
        owner_id=uuid4(),
        lifetime_seconds=lifetime,
        armed_at=armed_at,
    )


def _lease(conversation, ref, *, heartbeat_at, state=ACTIVE, expires_at=None):
    return HostedHarnessConversationLease.no_workspace_objects.create(
        conversation=conversation,
        provider_ref=ref,
        state=state,
        token_hash="t" * 64,
        fence_hash="f" * 64,
        expires_at=expires_at or _now() + 20 * MINUTE,
        heartbeat_at=heartbeat_at,
    )


def _turn(refs, ending=None, start=None, end=None, key=None):
    """One rebuild attempt's entry as the rebuild records it: its sandboxes from each
    ``turn_started``, its times and ending once it closes."""
    entry = {
        "command_message_id": key or str(uuid4()),
        "sandbox_refs": list(refs),
        "ending": ending,
    }
    if start is not None:
        entry["turn_started_at"] = start.isoformat()
    if end is not None:
        entry["turn_ended_at"] = end.isoformat()
    return entry


def _rebuild(job, *entries, state=HarnessNudge.State.FAILED):
    return HarnessNudge.no_workspace_objects.create(
        job=job,
        organization=job.organization,
        workspace=job.workspace,
        target=HarnessNudge.Target.ENVIRONMENT,
        state=state,
        usage={"attempts": list(entries)},
    )


def _close_turn(nudge, entry, *, ending, start, end):
    """The attempt closes: the rebuild writes its ending and times into its entry."""
    entry = {
        **entry,
        "ending": ending,
        "turn_started_at": start.isoformat(),
        "turn_ended_at": end.isoformat(),
    }
    HarnessNudge.no_workspace_objects.filter(id=nudge.id).update(
        usage={"attempts": [entry]}, state=HarnessNudge.State.FAILED
    )


def _dispatched_rebuild(job, conversation, *, taken_at=None, refs=()):
    """A rebuild whose builder message went out; started (``taken_at``) or not."""
    message = HostedHarnessConversationMessage.no_workspace_objects.create(
        conversation=conversation,
        sequence=1,
        role=HostedHarnessConversationMessage.Role.USER,
        state=HostedHarnessConversationMessage.State.DELIVERED,
    )
    attempts = [_turn(refs, key=str(message.id))] if refs else []
    return HarnessNudge.no_workspace_objects.create(
        job=job,
        organization=job.organization,
        workspace=job.workspace,
        target=HarnessNudge.Target.ENVIRONMENT,
        state=(HarnessNudge.State.RUNNING if taken_at else HarnessNudge.State.QUEUED),
        command_message=message,
        draft_snapshot={"taken_at": taken_at.isoformat()} if taken_at else None,
        usage={"attempts": attempts},
    )


def _close_rebuild(job, nudge, code, capture):
    """Close the rebuild as the rebuild check does, and run what follows its commit."""
    with capture(execute=True):
        with transaction.atomic():
            locked = HostedHarnessJob.no_workspace_objects.select_for_update().get(
                id=job.id
            )
            current = HarnessNudge.no_workspace_objects.select_for_update().get(
                id=nudge.id
            )
            rebuild.close_nudge(
                current,
                locked=locked,
                state=HarnessNudge.State.FAILED,
                error={"code": code},
                restore=False,
            )


class _Provider:
    """A sandbox provider whose deletes succeed."""

    name = "daytona"
    runtime_name = "chat-runtime"
    runtime_digest = "build-1"

    def __init__(self):
        self.deleted = []

    def get(self, provider_ref, request_timeout=None):
        return SimpleNamespace(id=provider_ref)

    def delete(self, target, *args, **kwargs):
        self.deleted.append(target.id)
        return True


def _gateway():
    from simulate.services.hosted_harness_gateway import HostedHarnessGateway

    gateway = object.__new__(HostedHarnessGateway)
    gateway.client = _Provider()
    return gateway


def _stop(stops):
    """The stop task's confirmed delete of the sandbox a closed rebuild let go."""
    [(conversation_id, ref)] = stops
    _gateway()._delete_chat_sandbox(ref, conversation_id)


def _seal(row):
    assert sandbox_usage.seal_sandbox_lifetime(str(row.id)) == "settled"
    row.refresh_from_db()
    return row


def _amounts(events):
    return [event.amount for event in events]


# --- open, renew, reason, close ------------------------------------------------------


@pytest.mark.django_db
def test_open_is_idempotent_per_provider_ref(organization, cloud, clock):
    first = _now() - 5 * MINUTE
    clock(first)
    _open(organization, "sandbox-a", armed_at=first)
    clock(first + 2 * MINUTE)
    _open(organization, "sandbox-a", armed_at=first + 2 * MINUTE, lifetime=600)

    [row] = HostedSandboxLifetime.no_workspace_objects.filter(provider_ref="sandbox-a")
    assert (row.started_at, row.armed_at, row.lifetime_seconds) == (first, first, 1800)


@pytest.mark.django_db
def test_open_arms_at_the_given_moment(organization, billed, clock):
    now = _now() - timedelta(hours=4)
    armed = now - timedelta(seconds=300)
    clock(now)
    _open(organization, "sandbox-armed", armed_at=armed)

    row = HostedSandboxLifetime.no_workspace_objects.get(provider_ref="sandbox-armed")
    assert row.started_at == now
    assert row.lifetime_ends_at == armed + timedelta(seconds=1800)

    HostedSandboxLifetime.no_workspace_objects.filter(id=row.id).update(
        ended_at=now + timedelta(hours=3)
    )
    row = _seal(row)
    # The provider's clock started before the create returned: lifetime less 300 s.
    assert _amounts(billed) == [25.0]
    assert row.billed_minutes == Decimal("25.0000")


@pytest.mark.django_db
@pytest.mark.parametrize("renews", [True, False], ids=["renewing", "not_renewing"])
def test_rearm_moves_the_cap_only_on_a_renewing_provider(organization, renews):
    start = _now() - 20 * MINUTE
    row = _row(organization, start=start)
    renewed_at = start + 10 * MINUTE
    provider = SimpleNamespace(renews_ttl=True) if renews else SimpleNamespace()

    sandbox_usage.rearm_sandbox_lifetime(
        provider=provider,
        provider_ref=row.provider_ref,
        lifetime_seconds=600,
        renewed_at=renewed_at,
    )

    row.refresh_from_db()
    if renews:
        expected = (renewed_at, 600, renewed_at + timedelta(seconds=600))
    else:
        expected = (start, 1800, start + timedelta(seconds=1800))
    assert (row.armed_at, row.lifetime_seconds, row.lifetime_ends_at) == expected


@pytest.mark.django_db
def test_rearm_after_the_end_changes_nothing(organization):
    start = _now() - 20 * MINUTE
    row = _row(organization, start=start, ended=start + 5 * MINUTE)

    sandbox_usage.rearm_sandbox_lifetime(
        provider=SimpleNamespace(renews_ttl=True),
        provider_ref=row.provider_ref,
        lifetime_seconds=600,
        renewed_at=start + 10 * MINUTE,
    )

    row.refresh_from_db()
    assert (row.armed_at, row.lifetime_seconds, row.lifetime_ends_at) == (
        start,
        1800,
        start + timedelta(seconds=1800),
    )


@pytest.mark.django_db
def test_first_reason_wins_and_an_our_failure_domain_is_kept(organization):
    row = _row(organization, start=_now() - 5 * MINUTE)

    for reason, domain in (
        ("turn_over", ""),
        ("chat_stopped_responding", "infrastructure"),
        ("deleted", ""),
    ):
        sandbox_usage.record_sandbox_end_reason(
            provider_ref=row.provider_ref, reason=reason, failure_domain=domain
        )

    row.refresh_from_db()
    assert (row.end_reason, row.failure_domain) == ("turn_over", "infrastructure")


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("found", "seen", "expired"),
    [
        pytest.param(5 * MINUTE, timedelta(seconds=-30), True, id="T-30s"),
        pytest.param(5 * MINUTE, timedelta(seconds=-90), True, id="T-90s"),
        pytest.param(5 * MINUTE, timedelta(seconds=-91), False, id="T-91s"),
        pytest.param(
            timedelta(seconds=-1),
            timedelta(seconds=-2),
            False,
            id="found_before_the_cap",
        ),
        pytest.param(5 * MINUTE, None, False, id="never_seen"),
    ],
)
def test_found_gone_at_the_lifetime_is_an_expiry(
    organization, billed, seals, clock, found, seen, expired
):
    row = _row(organization, start=_now() - timedelta(hours=2))
    cap = row.lifetime_ends_at
    clock(cap + found)

    sandbox_usage.record_sandbox_end_reason(
        provider_ref=row.provider_ref,
        reason="chat_runtime_stopped",
        failure_domain="infrastructure",
        last_seen=None if seen is None else cap + seen,
    )
    sandbox_usage.close_sandbox_lifetime(provider_ref=row.provider_ref)

    row.refresh_from_db()
    assert (row.end_reason, row.failure_domain) == (
        ("lifetime_elapsed", "")
        if expired
        else ("chat_runtime_stopped", "infrastructure")
    )
    _seal(row)
    assert _amounts(billed) == ([30.0] if expired else [])


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("seen", "expected"),
    [
        (timedelta(seconds=-30), ("lifetime_elapsed", "")),
        (timedelta(seconds=-91), ("chat_runtime_stopped", "infrastructure")),
    ],
    ids=["seen_up_to_its_limit", "silent_before_it"],
)
def test_close_records_the_ending_when_the_write_before_it_was_lost(
    organization, billed, seals, clock, seen, expected
):
    row = _row(organization, start=_now() - timedelta(hours=2))
    cap = row.lifetime_ends_at
    clock(cap + 5 * MINUTE)

    sandbox_usage.close_sandbox_lifetime(
        provider_ref=row.provider_ref,
        reason="chat_runtime_stopped",
        failure_domain="infrastructure",
        last_seen=cap + seen,
    )

    row.refresh_from_db()
    assert (row.end_reason, row.failure_domain) == expected
    _seal(row)
    assert _amounts(billed) == ([30.0] if expected[1] == "" else [])


@pytest.mark.django_db
def test_close_ends_once_and_enqueues_one_seal_after_commit(
    organization, seals, django_capture_on_commit_callbacks
):
    row = _row(organization, start=_now() - 5 * MINUTE)

    with django_capture_on_commit_callbacks(execute=True):
        sandbox_usage.close_sandbox_lifetime(provider_ref=row.provider_ref)
        assert seals == []

    row.refresh_from_db()
    ended_at = row.ended_at
    assert ended_at is not None
    assert (row.end_reason, row.failure_domain) == ("deleted", "")
    assert seals == [
        (
            [str(row.id)],
            {
                "task_id": f"hosted-sandbox-lifetime-seal-{row.id}",
                "id_conflict_policy": WorkflowIDConflictPolicy.USE_EXISTING,
                "dispatch_timeout_seconds": 30,
            },
        )
    ]

    with django_capture_on_commit_callbacks(execute=True):
        sandbox_usage.close_sandbox_lifetime(
            provider_ref=row.provider_ref,
            reason="claim_released",
            failure_domain="infrastructure",
        )

    row.refresh_from_db()
    assert (row.ended_at, row.end_reason, row.failure_domain) == (
        ended_at,
        "deleted",
        "",
    )
    assert len(seals) == 1


@pytest.mark.django_db
def test_failed_seal_enqueue_leaves_the_row_closed_for_the_sweep(
    organization, monkeypatch, django_capture_on_commit_callbacks
):
    row = _row(organization, start=_now() - 5 * MINUTE)
    log = MagicMock()
    monkeypatch.setattr(usage_tasks, "logger", log)

    def refuse(args, **options):
        raise RuntimeError("temporal unreachable")

    monkeypatch.setattr(usage_tasks.seal_hosted_sandbox_lifetime, "apply_async", refuse)

    with django_capture_on_commit_callbacks(execute=True):
        sandbox_usage.close_sandbox_lifetime(provider_ref=row.provider_ref)

    row.refresh_from_db()
    assert row.ended_at is not None
    log.error.assert_called_once()
    assert log.error.call_args.kwargs["lifetime_id"] == str(row.id)


LIFECYCLE = {
    "open": lambda row: sandbox_usage.open_sandbox_lifetime(
        kind=HostedSandboxLifetime.Kind.CHAT,
        provider_name="daytona",
        provider_ref="sandbox-new",
        organization_id=row.organization_id,
        workspace_id=None,
        owner_id=uuid4(),
        lifetime_seconds=1800,
        armed_at=django_timezone.now(),
    ),
    "rearm": lambda row: sandbox_usage.rearm_sandbox_lifetime(
        provider=SimpleNamespace(renews_ttl=True),
        provider_ref=row.provider_ref,
        lifetime_seconds=600,
        renewed_at=django_timezone.now(),
    ),
    "record": lambda row: sandbox_usage.record_sandbox_end_reason(
        provider_ref=row.provider_ref,
        reason="chat_runtime_stopped",
        failure_domain="infrastructure",
    ),
    "close": lambda row: sandbox_usage.close_sandbox_lifetime(
        provider_ref=row.provider_ref
    ),
}


def _broken_database(*args, **kwargs):
    """A statement that fails inside the database, leaving its transaction aborted
    unless the failing block rolls back to its own savepoint."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1/0")
    except DataError as exc:
        raise OperationalError("the database refused the statement") from exc


@pytest.mark.django_db
@pytest.mark.parametrize("step", list(LIFECYCLE))
def test_lifecycle_failures_never_raise(organization, cloud, monkeypatch, step):
    row = _row(organization, start=_now() - 5 * MINUTE)
    manager = HostedSandboxLifetime.no_workspace_objects
    monkeypatch.setattr(manager, "get_or_create", _broken_database)
    monkeypatch.setattr(manager, "select_for_update", _broken_database)
    log = MagicMock()
    monkeypatch.setattr(sandbox_usage, "logger", log)

    with transaction.atomic():
        LIFECYCLE[step](row)
        # The caller's own write goes on in the same transaction.
        Organization.objects.filter(id=organization.id).update(name="still writable")

    organization.refresh_from_db()
    assert organization.name == "still writable"
    log.exception.assert_called_once()


@pytest.mark.django_db
def test_oss_opens_no_row(organization, monkeypatch):
    monkeypatch.setattr(sandbox_usage, "is_oss", lambda: True)

    _open(organization, "sandbox-oss", armed_at=_now() - timedelta(hours=1))

    assert not HostedSandboxLifetime.no_workspace_objects.exists()
    assert sandbox_usage.sweep_sandbox_lifetimes() == NO_COUNTS


# --- the seal ------------------------------------------------------------------------


@pytest.mark.django_db
def test_seal_bills_fractional_minutes_with_a_stable_id(
    organization, workspace, billed
):
    start = _now() - timedelta(hours=1)
    owner = uuid4()
    row = _row(
        organization,
        workspace=workspace,
        start=start,
        ended=start + timedelta(seconds=150),
        owner_id=owner,
    )

    row = _seal(row)

    [event] = billed
    assert event.event_id == str(uuid5(UUID(str(row.id)), "sandbox_runtime"))
    assert (event.org_id, event.event_type, event.amount) == (
        str(organization.id),
        "sandbox_runtime",
        2.5,
    )
    assert event.timestamp == start + timedelta(seconds=150)
    assert event.properties == {
        "source": "hosted_sandbox",
        "source_id": str(row.id),
        "sandbox_kind": "chat",
        "owner_id": str(owner),
        "workspace_id": str(workspace.id),
        "provider": "daytona",
        "failure_domain": None,
    }
    assert row.billed_minutes == Decimal("2.5000")
    assert row.settled_at is not None

    assert sandbox_usage.seal_sandbox_lifetime(str(row.id)) == "skipped"
    assert len(billed) == 1


@pytest.mark.django_db
def test_seal_task_reports_the_outcome(organization, billed):
    start = _now() - timedelta(hours=1)
    row = _row(organization, start=start, ended=start + 2 * MINUTE)

    result = usage_tasks.seal_hosted_sandbox_lifetime._original_func(str(row.id))

    assert result == {"lifetime_id": str(row.id), "outcome": "settled"}
    assert _amounts(billed) == [2.0]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("after_the_cap", "minutes"),
    [
        pytest.param(timedelta(0), 30.0, id="ended_exactly_at_the_cap"),
        pytest.param(timedelta(hours=3), 30.0, id="ended_3_h_after_the_cap"),
        pytest.param(timedelta(seconds=-1), 29.9833, id="ended_1_s_before_the_cap"),
    ],
)
def test_seal_caps_at_the_lifetime(organization, billed, after_the_cap, minutes):
    start = _now() - timedelta(hours=5)
    row = _row(organization, start=start, ended=start + 30 * MINUTE + after_the_cap)

    _seal(row)

    assert _amounts(billed) == [minutes]


@pytest.mark.django_db
@pytest.mark.parametrize("provider", ["daytona-byo", "daytona"])
@pytest.mark.parametrize(
    "domain",
    ["", "infrastructure", "connectivity", "platform_sync", "environment", "agent"],
)
def test_seal_is_free_for_byo_and_our_failures(organization, billed, provider, domain):
    start = _now() - timedelta(hours=1)
    row = _row(
        organization,
        start=start,
        ended=start + 10 * MINUTE,
        provider=provider,
        end_reason="deleted",
        failure_domain=domain,
    )

    row = _seal(row)

    free = provider == "daytona-byo" or domain in (
        "infrastructure",
        "connectivity",
        "platform_sync",
    )
    assert _amounts(billed) == ([] if free else [10.0])
    assert row.billed_minutes == (0 if free else Decimal("10.0000"))


@pytest.mark.django_db
def test_seal_waits_for_the_end(organization, billed):
    row = _row(organization, start=_now() - 5 * MINUTE)

    assert sandbox_usage.seal_sandbox_lifetime(str(row.id)) == "skipped"

    row.refresh_from_db()
    assert (row.settled_at, row.billed_minutes) == (None, None)
    assert billed == []


# --- the sweep -----------------------------------------------------------------------


@pytest.mark.django_db
def test_idle_sandbox_reaped_at_minute_seven_meters_seven(organization, billed, seals):
    t0 = _now() - timedelta(minutes=17, seconds=1)
    _job, conversation = _environment(organization)
    row = _row(organization, start=t0, lifetime=420, owner_id=conversation.id)
    # An idle chat process that kept polling until its sandbox expired.
    _lease(
        conversation,
        row.provider_ref,
        heartbeat_at=t0 + timedelta(minutes=6, seconds=59),
    )

    sandbox_usage.sweep_sandbox_lifetimes()

    row.refresh_from_db()
    assert row.ended_at == t0 + 7 * MINUTE
    assert row.end_reason == "lifetime_elapsed"
    assert _amounts(billed) == [7.0]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("kind", "lifetime", "lease", "prior", "ending", "minutes"),
    [
        pytest.param(
            "chat",
            1800,
            (ACTIVE, 5 * MINUTE),
            None,
            ("chat_runtime_stopped", "infrastructure"),
            None,
            id="active_lease_silent_since_5",
        ),
        # A recovery sandbox whose start never finished.
        pytest.param(
            "chat",
            1800,
            (STARTING, 5 * MINUTE),
            None,
            ("chat_runtime_stopped", "infrastructure"),
            None,
            id="start_never_finished",
        ),
        pytest.param(
            "chat",
            1800,
            (ACTIVE, timedelta(minutes=28, seconds=30)),
            None,
            ("lifetime_elapsed", ""),
            30.0,
            id="active_lease_seen_exactly_90_s_before",
        ),
        pytest.param(
            "chat",
            1800,
            (EXPIRED, 5 * MINUTE),
            None,
            ("lifetime_elapsed", ""),
            30.0,
            id="fenced_lease",
        ),
        pytest.param(
            "chat", 1800, None, None, ("lifetime_elapsed", ""), 30.0, id="no_lease"
        ),
        pytest.param(
            "fix_engine",
            1200,
            (ACTIVE, 5 * MINUTE),
            None,
            ("lifetime_elapsed", ""),
            20.0,
            id="fix_engine_row",
        ),
        # A reason recorded before a delete that was never confirmed: the silence
        # since minute 5 still makes the end ours.
        pytest.param(
            "chat",
            1800,
            (ACTIVE, 5 * MINUTE),
            ("idle_window_over", ""),
            ("idle_window_over", "infrastructure"),
            None,
            id="reason_already_recorded",
        ),
        pytest.param(
            "chat",
            1800,
            (ACTIVE, timedelta(minutes=29, seconds=30)),
            ("idle_window_over", ""),
            ("idle_window_over", ""),
            30.0,
            id="reason_recorded_and_seen_to_its_limit",
        ),
    ],
)
def test_sweep_frees_a_chat_whose_process_died_early(
    organization, billed, seals, kind, lifetime, lease, prior, ending, minutes
):
    t0 = _now() - 41 * MINUTE
    _job, conversation = _environment(organization)
    row = _row(
        organization,
        start=t0,
        lifetime=lifetime,
        kind=kind,
        owner_id=conversation.id,
        end_reason=(prior or ("", ""))[0],
        failure_domain=(prior or ("", ""))[1],
    )
    if lease is not None:
        state, heartbeat = lease
        _lease(conversation, row.provider_ref, state=state, heartbeat_at=t0 + heartbeat)

    sandbox_usage.sweep_sandbox_lifetimes()

    row.refresh_from_db()
    assert row.ended_at == row.lifetime_ends_at
    assert (row.end_reason, row.failure_domain) == ending
    assert row.settled_at is not None
    assert _amounts(billed) == ([] if minutes is None else [minutes])


@pytest.mark.django_db
def test_sweep_closes_a_sandbox_whose_deletion_was_never_confirmed(
    organization, billed, seals
):
    row = _row(organization, start=_now() - 41 * MINUTE)

    first = sandbox_usage.sweep_sandbox_lifetimes()

    row.refresh_from_db()
    assert row.ended_at == row.lifetime_ends_at
    assert (row.end_reason, row.failure_domain) == ("lifetime_elapsed", "")
    assert _amounts(billed) == [30.0]
    assert first == {**NO_COUNTS, "expired": 1, "settled": 1}

    assert sandbox_usage.sweep_sandbox_lifetimes() == NO_COUNTS
    assert len(billed) == 1


@pytest.mark.django_db
def test_sweep_expires_past_a_failing_row(organization, billed, seals, monkeypatch):
    t0 = _now() - 41 * MINUTE
    rows = []
    for start in (t0, t0 - MINUTE):
        _job, conversation = _environment(organization)
        row = _row(organization, start=start, owner_id=conversation.id)
        _lease(conversation, row.provider_ref, heartbeat_at=t0 + 5 * MINUTE)
        rows.append(row)
    failing, plain = rows
    decide = sandbox_usage.reached_its_lifetime

    def refuse_for_the_first(*, cap, last_seen, found_at):
        if cap == failing.lifetime_ends_at:
            raise OperationalError("the row could not be read")
        return decide(cap=cap, last_seen=last_seen, found_at=found_at)

    monkeypatch.setattr(sandbox_usage, "reached_its_lifetime", refuse_for_the_first)

    counts = sandbox_usage.sweep_sandbox_lifetimes()

    failing.refresh_from_db()
    plain.refresh_from_db()
    assert (counts["expired"], counts["errors"]) == (1, 1)
    # Nothing of the failing row's end was written: the next sweep decides it whole.
    assert (failing.ended_at, failing.end_reason, failing.failure_domain) == (
        None,
        "",
        "",
    )
    assert plain.ended_at == plain.lifetime_ends_at
    assert (plain.end_reason, plain.failure_domain) == (
        "chat_runtime_stopped",
        "infrastructure",
    )


@pytest.mark.django_db
def test_sweep_leaves_a_sandbox_inside_its_grace(organization, billed, seals):
    row = _row(organization, start=_now() - 39 * MINUTE)

    assert sandbox_usage.sweep_sandbox_lifetimes() == NO_COUNTS

    row.refresh_from_db()
    assert row.ended_at is None
    assert billed == []


@pytest.mark.django_db
def test_sweep_keeps_a_close_that_won_the_race(
    organization, billed, seals, monkeypatch
):
    row = _row(organization, start=_now() - 41 * MINUTE)
    read_lease = sandbox_usage._lease_of

    def close_lands_meanwhile(provider_ref):
        sandbox_usage.close_sandbox_lifetime(provider_ref=provider_ref)
        return read_lease(provider_ref)

    monkeypatch.setattr(sandbox_usage, "_lease_of", close_lands_meanwhile)

    counts = sandbox_usage.sweep_sandbox_lifetimes()

    row.refresh_from_db()
    assert row.ended_at > row.lifetime_ends_at
    assert (row.end_reason, row.failure_domain) == ("deleted", "")
    assert counts["expired"] == 0


@pytest.mark.django_db
def test_sweep_settles_an_ended_row_whose_seal_never_ran(organization, billed):
    now = _now()
    row = _row(organization, start=now - 21 * MINUTE, ended=now - 11 * MINUTE)

    counts = sandbox_usage.sweep_sandbox_lifetimes()

    row.refresh_from_db()
    assert counts == {**NO_COUNTS, "settled": 1}
    assert row.billed_minutes == Decimal("10.0000")
    assert _amounts(billed) == [10.0]


def _failing_for(monkeypatch, *failing_refs, tried=None):
    """The rebuild reader raises for these sandboxes; every call is recorded."""
    read = rebuild.failed_turn_intervals

    def spy(*, conversation_id, provider_ref, cut_off_at=None):
        if tried is not None:
            tried.append(provider_ref)
        if provider_ref in failing_refs:
            raise OperationalError("the conversation could not be read")
        return read(
            conversation_id=conversation_id,
            provider_ref=provider_ref,
            cut_off_at=cut_off_at,
        )

    monkeypatch.setattr(rebuild, "failed_turn_intervals", spy)


@pytest.mark.django_db
def test_sweep_continues_past_a_failing_row(organization, billed, monkeypatch):
    now = _now()
    failing = _row(organization, start=now - 31 * MINUTE, ended=now - 21 * MINUTE)
    plain = _row(organization, start=now - 30 * MINUTE, ended=now - 20 * MINUTE)
    HostedSandboxLifetime.no_workspace_objects.filter(id=failing.id).update(
        updated_at=now - 40 * MINUTE
    )
    HostedSandboxLifetime.no_workspace_objects.filter(id=plain.id).update(
        updated_at=now - 30 * MINUTE
    )
    _failing_for(monkeypatch, failing.provider_ref)

    counts = sandbox_usage.sweep_sandbox_lifetimes()

    failing.refresh_from_db()
    plain.refresh_from_db()
    assert counts == {**NO_COUNTS, "settled": 1, "errors": 1}
    assert (failing.settled_at, failing.billed_minutes) == (None, None)
    assert failing.updated_at > now
    assert plain.billed_minutes == Decimal("10.0000")
    assert _amounts(billed) == [10.0]


@pytest.mark.django_db
def test_sweep_retries_a_row_whose_event_did_not_reach_the_stream(
    organization, cloud, monkeypatch, settings
):
    emitter = pytest.importorskip("ee.usage.services.emitter")
    redis = pytest.importorskip("redis")
    settings.USAGE_EVENTS_ENABLED = True
    # The consumer workflow is already running: no Temporal call from the emitter.
    monkeypatch.setattr(emitter, "_consumer_started", True)

    class Stream:
        def __init__(self):
            self.entries = []
            self.failures = 1

        def xadd(self, key, data, maxlen=None):
            if self.failures:
                self.failures -= 1
                raise redis.ConnectionError("the stream is unavailable")
            self.entries.append(data)

    stream = Stream()
    monkeypatch.setattr(emitter, "get_redis", lambda: stream)
    now = _now()
    row = _row(organization, start=now - 21 * MINUTE, ended=now - 11 * MINUTE)

    first = sandbox_usage.sweep_sandbox_lifetimes()

    row.refresh_from_db()
    assert first["errors"] == 1
    assert (row.settled_at, row.billed_minutes) == (None, None)
    assert stream.entries == []

    second = sandbox_usage.sweep_sandbox_lifetimes()

    row.refresh_from_db()
    assert second["settled"] == 1
    assert row.billed_minutes == Decimal("10.0000")
    assert [entry["event_id"] for entry in stream.entries] == [
        str(uuid5(UUID(str(row.id)), "sandbox_runtime"))
    ]


@pytest.mark.django_db
def test_sweep_rotates_past_rows_that_do_not_settle(organization, billed, monkeypatch):
    now = _now()
    job, conversation = _environment(organization)
    # Ended in this order too, so an order by end would also start at the first.
    waiting = _row(
        organization,
        start=now - 60 * MINUTE,
        ended=now - 40 * MINUTE,
        owner_id=conversation.id,
    )
    _rebuild(job, _turn([waiting.provider_ref]), state=HarnessNudge.State.RUNNING)
    failing = _row(organization, start=now - 59 * MINUTE, ended=now - 39 * MINUTE)
    plain = _row(organization, start=now - 58 * MINUTE, ended=now - 38 * MINUTE)
    for row, age in ((waiting, 50), (failing, 40), (plain, 30)):
        HostedSandboxLifetime.no_workspace_objects.filter(id=row.id).update(
            updated_at=now - age * MINUTE
        )
    tried = []
    _failing_for(monkeypatch, failing.provider_ref, tried=tried)

    outcomes = [sandbox_usage.sweep_sandbox_lifetimes(limit=1) for _ in range(3)]

    assert tried == [waiting.provider_ref, failing.provider_ref, plain.provider_ref]
    assert outcomes == [
        {**NO_COUNTS, "waiting": 1},
        {**NO_COUNTS, "errors": 1},
        {**NO_COUNTS, "settled": 1},
    ]
    plain.refresh_from_db()
    assert plain.settled_at is not None


def test_sweep_is_scheduled():
    from tfc.temporal.schedules.simulate import SIMULATE_SCHEDULES

    [schedule] = [
        one
        for one in SIMULATE_SCHEDULES
        if one.activity_name == "sweep_hosted_sandbox_lifetimes"
    ]
    assert (schedule.interval_seconds, schedule.queue) == (600, "default")
    assert (
        usage_tasks.sweep_hosted_sandbox_lifetimes._activity_name
        == schedule.activity_name
    )


# --- failed rebuild turns ------------------------------------------------------------


def _thirty_minute_row(organization, conversation, start):
    """A chat row that ended after 30 minutes, well inside its lifetime limit."""
    return _row(
        organization,
        start=start,
        lifetime=60 * 60,
        ended=start + 30 * MINUTE,
        owner_id=conversation.id,
    )


FAILED_TURN_CASES = {
    "spans_the_row": ([("turn_failed", -1, 31)], None),
    "partly_outside": ([("turn_failed", -10, 5)], 25.0),
    "two_overlapping": ([("turn_failed", 5, 15), ("turn_failed", 10, 20)], 15.0),
    "success_then_failure": ([("applied", 2, 10), ("turn_failed", 12, 20)], 22.0),
    "after_the_cap": ([("turn_failed", 40, 50)], 30.0),
    "environment_deleted": ([("applied", 2, 10), ("turn_failed", 12, 20)], 22.0),
}


@pytest.mark.django_db
@pytest.mark.parametrize("case", list(FAILED_TURN_CASES))
def test_failed_turn_time_is_taken_off(organization, billed, case):
    turns, minutes = FAILED_TURN_CASES[case]
    t0 = _now() - timedelta(hours=5)
    job, conversation = _environment(organization)
    if case == "after_the_cap":
        # Its 30-minute lifetime ended long before the confirmed delete.
        row = _row(
            organization,
            start=t0,
            ended=t0 + timedelta(hours=3, minutes=30),
            owner_id=conversation.id,
        )
    else:
        row = _thirty_minute_row(organization, conversation, t0)
    nudge = _rebuild(
        job,
        *(
            _turn([row.provider_ref], ending, t0 + a * MINUTE, t0 + b * MINUTE)
            for ending, a, b in turns
        ),
    )
    if case == "environment_deleted":
        HostedHarnessConversation._base_manager.filter(id=conversation.id).update(
            deleted=True
        )
        HarnessNudge._base_manager.filter(id=nudge.id).update(deleted=True)

    row = _seal(row)

    assert _amounts(billed) == ([] if minutes is None else [minutes])
    assert row.billed_minutes == (
        0 if minutes is None else Decimal(str(minutes)).quantize(Decimal("0.0001"))
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "code", ["turn_failed", "turn_interrupted", "no_report", "landing_failed"]
)
def test_each_failed_turn_code_deducts_only_its_turn(organization, billed, code):
    t0 = _now() - timedelta(hours=5)
    job, conversation = _environment(organization)
    row = _thirty_minute_row(organization, conversation, t0)
    _rebuild(
        job,
        _turn([row.provider_ref], "applied", t0 + 2 * MINUTE, t0 + 10 * MINUTE),
        _turn([row.provider_ref], code, t0 + 12 * MINUTE, t0 + 20 * MINUTE),
    )

    _seal(row)

    assert _amounts(billed) == [22.0]


@pytest.mark.django_db
def test_failed_turn_without_an_end_is_not_taken_off(organization, billed, monkeypatch):
    t0 = _now() - timedelta(hours=5)
    job, conversation = _environment(organization)
    row = _thirty_minute_row(organization, conversation, t0)
    _rebuild(job, _turn([row.provider_ref], "turn_failed", t0 + 5 * MINUTE))
    log = MagicMock()
    monkeypatch.setattr(rebuild, "logger", log)

    _seal(row)

    assert _amounts(billed) == [30.0]
    log.warning.assert_called_once()


@pytest.mark.django_db
def test_seal_waits_for_a_turn_still_running_in_the_sandbox(organization, billed):
    t0 = _now() - timedelta(hours=1)
    job, conversation = _environment(organization)
    row = _thirty_minute_row(organization, conversation, t0)
    entry = _turn([row.provider_ref])
    nudge = _rebuild(job, entry, state=HarnessNudge.State.RUNNING)
    HostedSandboxLifetime.no_workspace_objects.filter(id=row.id).update(updated_at=t0)

    assert sandbox_usage.seal_sandbox_lifetime(str(row.id)) == "waiting"

    row.refresh_from_db()
    assert row.settled_at is None
    assert row.updated_at > t0 + 30 * MINUTE
    assert billed == []

    _close_turn(
        nudge,
        entry,
        ending="turn_failed",
        start=t0 + 5 * MINUTE,
        end=t0 + 20 * MINUTE,
    )
    _seal(row)
    assert _amounts(billed) == [15.0]

    old_job, old_conversation = _environment(organization)
    long_ago = _now() - timedelta(hours=25, minutes=30)
    old = _thirty_minute_row(organization, old_conversation, long_ago)
    _rebuild(old_job, _turn([old.provider_ref]), state=HarnessNudge.State.RUNNING)

    _seal(old)
    assert _amounts(billed) == [15.0, 30.0]


@pytest.mark.django_db
def test_naive_turn_times_are_read_as_utc(organization, billed):
    t0 = _now() - timedelta(hours=5)
    job, conversation = _environment(organization)
    row = _thirty_minute_row(organization, conversation, t0)
    utc = t0.astimezone(UTC).replace(tzinfo=None)
    _rebuild(
        job,
        {
            "command_message_id": str(uuid4()),
            "sandbox_refs": [row.provider_ref],
            "ending": "turn_failed",
            "turn_started_at": (utc + 5 * MINUTE).isoformat(),
            "turn_ended_at": (utc + 20 * MINUTE).isoformat(),
        },
    )

    _seal(row)

    assert _amounts(billed) == [15.0]


@pytest.mark.django_db
def test_stopped_rebuild_turn_is_charged(organization, billed):
    t0 = _now() - timedelta(hours=5)
    job, conversation = _environment(organization)
    row = _thirty_minute_row(organization, conversation, t0)
    _rebuild(
        job,
        _turn([row.provider_ref], "stopped_by_user", t0 + 5 * MINUTE, t0 + 20 * MINUTE),
    )

    _seal(row)

    assert _amounts(billed) == [30.0]


@pytest.mark.django_db
def test_fix_rows_never_read_rebuild_turns(organization, billed, monkeypatch):
    start = _now() - timedelta(hours=1)
    row = _row(
        organization,
        start=start,
        lifetime=1200,
        ended=start + 10 * MINUTE,
        kind=HostedSandboxLifetime.Kind.FIX_ENGINE,
    )
    tried = []
    _failing_for(monkeypatch, tried=tried)

    _seal(row)

    assert tried == []
    assert _amounts(billed) == [10.0]


# --- rebuilds that were lost, and turns cut off at the lifetime limit -----------------


def _retire_at_message(monkeypatch, job, conversation, row):
    """A person's message finds the chat process silent: the sandbox is retired and a
    newer one carries the lease."""
    from simulate.services.hosted_harness_gateway import HostedHarnessGateway

    newer = f"sandbox-{uuid4()}"

    def start_newer(
        self, job, *, conversation, endpoint_base_url, agent_version, claim_ref
    ):
        HostedHarnessConversationLease.no_workspace_objects.filter(
            conversation=conversation
        ).update(provider_ref=newer, state=ACTIVE, heartbeat_at=_now())
        return HostedHarnessConversationLease.no_workspace_objects.get(
            conversation=conversation
        )

    monkeypatch.setattr(HostedHarnessGateway, "_start_chat_sandbox", start_newer)
    HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(
        state=HostedHarnessJob.State.COMPLETED
    )
    job.refresh_from_db()
    gateway = _gateway()
    gateway.ensure_conversation_runtime(
        job, conversation=conversation, endpoint_base_url="https://platform.example"
    )
    assert gateway.client.deleted == [row.provider_ref]
    return newer


@pytest.mark.django_db
@pytest.mark.parametrize(
    "case", ["silent_since_5", "reported_to_its_limit", "retired_by_a_message"]
)
def test_stuck_rebuild_frees_its_sandbox_in_production_order(
    organization, billed, seals, monkeypatch, case
):
    retire = case == "retired_by_a_message"
    t0 = _now() - (35 if retire else 62) * MINUTE
    job, conversation = _environment(organization)
    row = _row(organization, start=t0, owner_id=conversation.id)
    heartbeat = (
        timedelta(minutes=29, seconds=30)
        if case == "reported_to_its_limit"
        else 5 * MINUTE
    )
    _lease(conversation, row.provider_ref, heartbeat_at=t0 + heartbeat)
    # The rebuild's turn started in this sandbox at minute 2 and is still open.
    entry = _turn([row.provider_ref])
    nudge = _rebuild(job, entry, state=HarnessNudge.State.RUNNING)

    if retire:
        newer = _retire_at_message(monkeypatch, job, conversation, row)
        row.refresh_from_db()
        assert (row.end_reason, row.failure_domain) == (
            "chat_runtime_stopped",
            "infrastructure",
        )
        assert (
            HostedHarnessConversationLease.no_workspace_objects.get(
                conversation=conversation
            ).provider_ref
            == newer
        )
    else:
        assert sandbox_usage.sweep_sandbox_lifetimes()["waiting"] == 1
        row.refresh_from_db()
        assert row.settled_at is None
        assert (row.end_reason, row.failure_domain) == (
            ("lifetime_elapsed", "")
            if case == "reported_to_its_limit"
            else ("chat_runtime_stopped", "infrastructure")
        )

    # An hour on, the rebuild is declared stuck.
    _close_turn(
        nudge,
        entry,
        ending="rebuild_stuck",
        start=t0 + 2 * MINUTE,
        end=t0 + 62 * MINUTE,
    )
    if retire:
        row = _seal(row)
    else:
        assert sandbox_usage.sweep_sandbox_lifetimes()["settled"] == 1
        row.refresh_from_db()

    if case == "reported_to_its_limit":
        # It lived to its limit: charged up to it, less the turn it cut off.
        assert _amounts(billed) == [2.0]
    else:
        assert (row.billed_minutes, billed) == (0, [])
        assert (row.end_reason, row.failure_domain) == (
            "rebuild_stuck",
            "infrastructure",
        )


def _found_gone(organization, clock, conversation, *, start, at, last_seen):
    """A sandbox retired at minute ``at`` after its process was last seen at
    ``last_seen`` (minutes from ``start``), the way a person's next message retires it.
    """
    row = _row(organization, start=start, owner_id=conversation.id)
    clock(start + at)
    sandbox_usage.record_sandbox_end_reason(
        provider_ref=row.provider_ref,
        reason="chat_runtime_stopped",
        failure_domain="infrastructure",
        last_seen=start + last_seen,
    )
    sandbox_usage.close_sandbox_lifetime(provider_ref=row.provider_ref)
    clock(None)
    row.refresh_from_db()
    return row


def _closed(organization, conversation, start, ended, reason="deleted", domain=""):
    return _row(
        organization,
        start=start,
        ended=ended,
        owner_id=conversation.id,
        end_reason=reason,
        failure_domain=domain,
    )


def _cut_off_case(case, organization, clock, job, conversation, t0):
    """The rows and attempt of one case, and the minutes each row bills."""
    at = lambda minutes: t0 + timedelta(minutes=minutes)  # noqa: E731
    a_at_its_limit = _closed(
        organization, conversation, t0, at(30), reason="lifetime_elapsed"
    )
    if case in ("restarted_and_applied", "restarted_and_failed"):
        b = _closed(organization, conversation, at(31), at(50))
        ending = "applied" if case == "restarted_and_applied" else "turn_failed"
        _rebuild(
            job,
            _turn([a_at_its_limit.provider_ref, b.provider_ref], ending, at(2), at(45)),
        )
        # B's own 19 minutes, less the failed turn's 14 inside it.
        return [(a_at_its_limit, 2.0), (b, 19.0 if ending == "applied" else 5.0)]
    if case in ("ended_before_the_limit", "ended_exactly_at_the_limit"):
        end = at(20) if case == "ended_before_the_limit" else at(30)
        _rebuild(job, _turn([a_at_its_limit.provider_ref], "applied", at(2), end))
        return [(a_at_its_limit, 30.0)]
    if case == "deleted_before_the_limit_then_applied":
        a = _closed(organization, conversation, t0, at(25), reason="agent_changed")
        b = _closed(organization, conversation, at(25), at(40))
        _rebuild(job, _turn([a.provider_ref, b.provider_ref], "applied", at(2), at(35)))
        return [(a, 25.0)]
    if case == "started_at_the_limit":
        b = _closed(organization, conversation, at(30), at(50))
        _rebuild(job, _turn([b.provider_ref], "turn_failed", at(30), at(45)))
        return [(a_at_its_limit, 30.0)]
    if case == "restarted_then_stuck":
        a = _found_gone(
            organization,
            clock,
            conversation,
            start=t0,
            at=31 * MINUTE,
            last_seen=timedelta(minutes=29, seconds=50),
        )
        assert (a.end_reason, a.failure_domain) == ("lifetime_elapsed", "")
        b = _row(organization, start=at(31), owner_id=conversation.id)
        clock(at(62))
        sandbox_usage.record_sandbox_end_reason(
            provider_ref=b.provider_ref,
            reason="rebuild_stuck",
            failure_domain="infrastructure",
            last_seen=at(35),
        )
        sandbox_usage.close_sandbox_lifetime(provider_ref=b.provider_ref)
        clock(None)
        _rebuild(
            job, _turn([a.provider_ref, b.provider_ref], "rebuild_stuck", at(2), at(62))
        )
        return [(a, 2.0), (b, None)]
    if case == "fenced_then_stuck_in_the_next":
        # A is let go at minute 20 (a newer run took the chat over); the rebuild moved to
        # B and was lost there: only B, its last sandbox, is freed.
        a = _closed(organization, conversation, t0, at(20), reason="lease_expired")
        b = _closed(organization, conversation, at(20), at(50))
        _rebuild(
            job, _turn([a.provider_ref, b.provider_ref], "rebuild_stuck", at(2), at(62))
        )
        return [(a, 20.0), (b, None)]
    raise AssertionError(case)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "case",
    [
        "no_restart",
        "restarted_and_applied",
        "restarted_and_failed",
        "ended_before_the_limit",
        "ended_exactly_at_the_limit",
        "deleted_before_the_limit_then_applied",
        "started_at_the_limit",
        "restarted_then_stuck",
        "restart_never_took_the_turn",
        "fenced_then_stuck_in_the_next",
    ],
)
def test_turn_cut_off_at_the_lifetime_is_taken_off(
    organization,
    billed,
    seals,
    clock,
    stops,
    django_capture_on_commit_callbacks,
    case,
):
    t0 = _now() - 62 * MINUTE
    at = lambda minutes: t0 + timedelta(minutes=minutes)  # noqa: E731
    job, conversation = _environment(organization)

    if case == "no_restart":
        row = _row(organization, start=t0, owner_id=conversation.id)
        _lease(
            conversation, row.provider_ref, heartbeat_at=at(29) + timedelta(seconds=50)
        )
        entry = _turn([row.provider_ref])
        nudge = _rebuild(job, entry, state=HarnessNudge.State.RUNNING)
        assert sandbox_usage.sweep_sandbox_lifetimes()["waiting"] == 1
        row.refresh_from_db()
        assert row.end_reason == "lifetime_elapsed"
        _close_turn(nudge, entry, ending="rebuild_stuck", start=at(2), end=at(62))
        expected = [(row, 2.0)]
    elif case == "restart_never_took_the_turn":
        a = _found_gone(
            organization,
            clock,
            conversation,
            start=t0,
            at=31 * MINUTE,
            last_seen=timedelta(minutes=29, seconds=50),
        )
        # The recovery sandbox: started at 31, silent from 35, never took the turn.
        b = _row(organization, start=at(31), owner_id=conversation.id)
        _lease(conversation, b.provider_ref, heartbeat_at=at(35))
        nudge = _dispatched_rebuild(
            job, conversation, taken_at=at(2), refs=[a.provider_ref]
        )
        _close_rebuild(job, nudge, "rebuild_stuck", django_capture_on_commit_callbacks)
        b.refresh_from_db()
        assert (b.end_reason, b.failure_domain) == ("rebuild_stuck", "infrastructure")
        _stop(stops)
        expected = [(a, 2.0), (b, None)]
    else:
        expected = _cut_off_case(case, organization, clock, job, conversation, t0)

    for row, minutes in expected:
        sealed = _seal(row)
        assert sealed.billed_minutes == (
            0 if minutes is None else Decimal(str(minutes)).quantize(Decimal("0.0001"))
        ), (case, row.started_at)
    assert sorted(_amounts(billed)) == sorted(
        minutes for _row_, minutes in expected if minutes is not None
    )


@pytest.mark.django_db
@pytest.mark.parametrize("case", ["silent", "reported_to_its_limit", "still_starting"])
def test_stuck_rebuild_frees_the_silent_sandbox_it_waited_on(
    organization, billed, seals, stops, django_capture_on_commit_callbacks, case
):
    reported = case == "reported_to_its_limit"
    t0 = _now() - (31 if reported else 10) * MINUTE
    job, conversation = _environment(organization)
    row = _row(organization, start=t0, owner_id=conversation.id)
    if case == "still_starting":
        # A recovery sandbox whose start wrote its heartbeat a minute ago; its process
        # has not polled yet, so it never took a turn.
        _lease(
            conversation,
            row.provider_ref,
            heartbeat_at=_now() - MINUTE,
            state=STARTING,
        )
    else:
        heartbeat = timedelta(minutes=29, seconds=30) if reported else 4 * MINUTE
        _lease(conversation, row.provider_ref, heartbeat_at=t0 + heartbeat)
    # No attempt of the rebuild ever ran in this sandbox.
    nudge = _dispatched_rebuild(job, conversation, taken_at=t0 + MINUTE)

    _close_rebuild(job, nudge, "rebuild_stuck", django_capture_on_commit_callbacks)

    row.refresh_from_db()
    assert (row.end_reason, row.failure_domain) == (
        ("lifetime_elapsed", "") if reported else ("rebuild_stuck", "infrastructure")
    )
    _stop(stops)
    row.refresh_from_db()
    assert row.ended_at is not None
    assert row.end_reason == ("lifetime_elapsed" if reported else "rebuild_stuck")

    _seal(row)
    assert _amounts(billed) == ([30.0] if reported else [])


@pytest.mark.django_db
def test_stuck_rebuild_whose_ending_was_not_recorded_leaves_the_lease_to_the_sweep(
    organization,
    billed,
    seals,
    stops,
    django_capture_on_commit_callbacks,
    monkeypatch,
):
    t0 = _now() - 41 * MINUTE
    job, conversation = _environment(organization)
    row = _row(organization, start=t0, owner_id=conversation.id)
    lease = _lease(conversation, row.provider_ref, heartbeat_at=t0 + 4 * MINUTE)
    nudge = _dispatched_rebuild(job, conversation, taken_at=t0 + MINUTE)
    open_row = sandbox_usage._open_row
    tried = []

    def lost_once(provider_ref):
        tried.append(provider_ref)
        if len(tried) == 1:
            raise OperationalError("the connection was lost")
        return open_row(provider_ref)

    monkeypatch.setattr(sandbox_usage, "_open_row", lost_once)

    _close_rebuild(job, nudge, "rebuild_stuck", django_capture_on_commit_callbacks)

    # The rebuild's ending was not written, so the lease was neither fenced nor stopped.
    lease.refresh_from_db()
    row.refresh_from_db()
    assert tried == [row.provider_ref]
    assert lease.state == ACTIVE
    assert stops == []
    assert (row.ended_at, row.end_reason, row.failure_domain) == (None, "", "")

    # The sweep reads the lease, silent since minute 4, at the sandbox's limit.
    sandbox_usage.sweep_sandbox_lifetimes()

    row.refresh_from_db()
    assert row.ended_at == row.lifetime_ends_at
    assert (row.end_reason, row.failure_domain) == (
        "chat_runtime_stopped",
        "infrastructure",
    )
    assert billed == []


@pytest.mark.django_db
@pytest.mark.parametrize("case", ["not_live", "reported_to_its_limit", "live_sandbox"])
def test_not_started_rebuild_frees_its_open_sandbox(
    organization, billed, seals, stops, django_capture_on_commit_callbacks, case
):
    t0 = _now() - (32 if case == "reported_to_its_limit" else 20) * MINUTE
    job, conversation = _environment(organization)
    row = _row(organization, start=t0, owner_id=conversation.id)
    heartbeat = {
        "not_live": t0 + 5 * MINUTE,
        # 10 s before its limit, and more than the liveness window before the close.
        "reported_to_its_limit": t0 + timedelta(minutes=29, seconds=50),
        # A long turn of the person's own, still reporting.
        "live_sandbox": _now() - timedelta(seconds=5),
    }[case]
    _lease(conversation, row.provider_ref, heartbeat_at=heartbeat)
    nudge = _dispatched_rebuild(job, conversation)

    _close_rebuild(
        job, nudge, "rebuild_not_started", django_capture_on_commit_callbacks
    )

    row.refresh_from_db()
    assert (row.end_reason, row.failure_domain) == {
        "not_live": ("rebuild_not_started", "infrastructure"),
        "reported_to_its_limit": ("lifetime_elapsed", ""),
        "live_sandbox": ("", ""),
    }[case]
    _stop(stops)
    row = _seal(row)

    if case == "not_live":
        assert (row.billed_minutes, billed) == (0, [])
    elif case == "reported_to_its_limit":
        assert _amounts(billed) == [30.0]
    else:
        assert (row.end_reason, row.failure_domain) == ("deleted", "")
        [event] = billed
        assert event.amount == pytest.approx(20.0, abs=0.1)


def _send_at(client, conversation, headers, acknowledged, *events):
    """Turn events from the chat process, each with the moment it was emitted."""
    signed = []
    for sequence, kind, payload, emitted_at in events:
        event = {
            "schema_version": "futureagi.harness-conversation-event.v1",
            "event_id": f"ce-{conversation.id}-{sequence}",
            "conversation_id": str(conversation.id),
            "sequence": sequence,
            "kind": kind,
            "message_id": None,
            "stage": "scenarios",
            "invocation_id": "turn-1",
            "function_call_id": None,
            "emitted_at": emitted_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "payload": payload,
        }
        event["digest"] = canonical_digest(event)
        signed.append(event)
    return client.post(
        f"{GUEST}/{conversation.id}/events/",
        {
            "schema_version": "futureagi.harness-conversation-event.v1",
            "acknowledged_through": acknowledged,
            "events": signed,
        },
        format="json",
        **headers,
    )


@pytest.mark.django_db
def test_rebuild_turn_to_seal_end_to_end(
    user,
    workspace,
    storage,
    scheduled,
    started,
    inline,
    auth_client,
    billed,
    seals,
    settings,
    django_capture_on_commit_callbacks,
):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_VERSIONING_NUDGES_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False
    settings.HARNESS_PUBLIC_BASE_URL = "https://platform.example"
    environment, nudge, _a1, _a2 = _queued(
        user,
        workspace,
        storage,
        "r-sandbox-minutes",
        auth_client,
        django_capture_on_commit_callbacks,
    )
    assert rebuild.dispatch_nudge(environment.id, start_runtime=False)
    nudge.refresh_from_db()
    command = nudge.command_message
    conversation = command.conversation
    ref = f"sandbox-{uuid4()}"
    capability = issue_conversation_capability(
        conversation,
        endpoint_base_url="https://platform.example",
        provider_ref=ref,
        attempt=None,
        ttl_seconds=3600,
    )
    now = _now()
    _open(environment.organization, ref, armed_at=now - 30 * MINUTE, lifetime=3600)
    # Started half an hour ago, for this conversation.
    HostedSandboxLifetime.no_workspace_objects.filter(provider_ref=ref).update(
        started_at=now - 30 * MINUTE, owner_id=conversation.id
    )

    with django_capture_on_commit_callbacks(execute=True):
        response = _send_at(
            APIClient(),
            conversation,
            _headers(capability),
            0,
            (
                1,
                "turn_started",
                {"command_message_id": str(command.id)},
                now - 20 * MINUTE,
            ),
            (
                2,
                "turn_completed",
                {"command_message_id": str(command.id), "outcome": "failed"},
                now - 5 * MINUTE,
            ),
        )
    assert response.status_code < 300, response.content

    nudge.refresh_from_db()
    assert nudge.error == {"code": "turn_failed"}
    [attempt] = nudge.usage["attempts"]
    assert (attempt["sandbox_refs"], attempt["ending"]) == ([ref], "turn_failed")
    stopped = [args for task, args in inline if task == "stop_harness_nudge_sandbox"]
    assert stopped == [[str(conversation.id), ref]]

    _gateway()._delete_chat_sandbox(ref, conversation.id)
    row = _seal(HostedSandboxLifetime.no_workspace_objects.get(provider_ref=ref))

    # The sandbox's own time, less the 15 minutes of the failed turn.
    seconds = (row.ended_at - row.started_at).total_seconds() - 15 * 60
    expected = (Decimal(str(seconds)) / Decimal(60)).quantize(Decimal("0.0001"))
    assert row.end_reason == "deleted"
    assert abs(row.billed_minutes - expected) <= Decimal("0.0001")
    assert row.billed_minutes < Decimal("16")
