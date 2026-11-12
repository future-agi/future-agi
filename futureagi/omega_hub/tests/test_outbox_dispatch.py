"""The outbox's request-path cost and its personal data (HUB-18, fix round 3).

The eager Temporal wake-up runs in the decision request's thread on commit. The round-3 verifier
blackholed the Temporal frontend and measured 75 s per dispatch; HUB-18's acceptance is that the
decision request's latency is unchanged. The slow start below stands in for that blackhole.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from omega_hub.models import EmailOutbox, EmailOutboxStatus
from omega_hub.services import outbox
from omega_hub.tests.helpers import (
    approve_payload,
    entry_files,
    run_checks,
    submit,
    token_client,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def temporal(monkeypatch):
    """Replace the Temporal workflow start; `slow` makes it hang like a blackholed frontend."""
    state = {"slow": False, "starts": 0, "cancelled": 0}

    async def start(activity_name, args, kwargs, queue, task_id, *rest):
        state["starts"] += 1
        if state["slow"]:
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                state["cancelled"] += 1
                raise
        return task_id

    monkeypatch.setattr("tfc.temporal.drop_in.runner._start_activity_async", start)
    monkeypatch.setattr(outbox, "_dispatch_paused_until", 0.0)
    return state


def _row(world):
    return EmailOutbox.objects.create(template="test", recipients=[world.maint_a.email], subject="s", body="b")


def test_dispatch_is_bounded_when_temporal_does_not_answer(world, temporal):
    temporal["slow"] = True
    row = _row(world)
    started = time.monotonic()
    assert outbox.dispatch(str(row.pk)) is False
    elapsed = time.monotonic() - started
    assert elapsed < outbox.DISPATCH_TIMEOUT_SECONDS + 1.5, elapsed
    assert temporal["cancelled"] == 1
    assert EmailOutbox.objects.get(pk=row.pk).status == EmailOutboxStatus.PENDING


def test_after_one_failure_the_process_leaves_rows_to_the_drain(world, temporal):
    temporal["slow"] = True
    outbox.dispatch(str(_row(world).pk))
    started = time.monotonic()
    assert outbox.dispatch(str(_row(world).pk)) is False
    assert time.monotonic() - started < 0.2
    assert temporal["starts"] == 1


def test_a_healthy_temporal_is_woken_for_each_row(world, temporal):
    assert outbox.dispatch(str(_row(world).pk)) is True
    assert outbox.dispatch(str(_row(world).pk)) is True
    assert temporal["starts"] == 2


def _decide(world, django_capture_on_commit_callbacks, name):
    """Upload, check and approve one entry (as flows.publish does); time only the decision."""
    files = entry_files(name)
    meta = json.loads(files["omega.hub.json"])
    created = submit(token_client(world.token_a), "future-agi", files, name=meta["id"], version=meta["version"])
    assert created.status_code == 201, created.content
    _, report, _ = run_checks(files)
    started = time.monotonic()
    with django_capture_on_commit_callbacks(execute=True):
        decided = world.b.post(f"/omega-hub/v1/review/submissions/{created.json()['id']}/decision",
                               approve_payload(report, disclosure=meta["disclosure"]), format="json")
    assert decided.status_code == 200, decided.content
    return time.monotonic() - started


def test_a_temporal_outage_does_not_slow_decisions(world, temporal, django_capture_on_commit_callbacks):
    baseline = _decide(world, django_capture_on_commit_callbacks, "first-agent")
    assert temporal["starts"] >= 1
    temporal["slow"] = True
    first = _decide(world, django_capture_on_commit_callbacks, "notes-plugin")
    starts_after_first = temporal["starts"]
    second = _decide(world, django_capture_on_commit_callbacks, "engineering-team")
    assert first < baseline + outbox.DISPATCH_TIMEOUT_SECONDS + 1.5, (baseline, first)
    assert second < baseline + 1.5, (baseline, second)
    assert temporal["starts"] == starts_after_first, "an open pause must not touch Temporal"
    pending = EmailOutbox.objects.filter(template="approved", status=EmailOutboxStatus.PENDING)
    assert pending.count() >= 2


def test_forget_address_erases_sent_rows_and_redacts_pending_ones(world):
    a, b = world.maint_a.email, world.maint_b.email
    sent = EmailOutbox.objects.create(template="t", recipients=[a], subject="s", body="b", status=EmailOutboxStatus.SENT)
    dead = EmailOutbox.objects.create(template="t", recipients=[a, b], subject="s", body="b", status=EmailOutboxStatus.DEAD)
    shared = EmailOutbox.objects.create(template="t", recipients=[a, b], subject="s", body="b")
    alone = EmailOutbox.objects.create(template="t", recipients=[a], subject="s", body="b")
    other = EmailOutbox.objects.create(template="t", recipients=[b], subject="s", body="b", status=EmailOutboxStatus.SENT)
    assert outbox.forget_address(a) == {"deleted": 3, "redacted": 1}
    remaining = set(EmailOutbox.objects.values_list("pk", flat=True))
    assert remaining == {shared.pk, other.pk}
    assert EmailOutbox.objects.get(pk=shared.pk).recipients == [b]
    assert not EmailOutbox.objects.filter(recipients__contains=[a]).exists()
    assert {sent.pk, dead.pk, alone.pk}.isdisjoint(remaining)


def test_deleting_an_account_erases_its_address_from_the_outbox(world, django_capture_on_commit_callbacks):
    # HUB-OPS round 2 listed this wiring as owed: forget_address existed and nothing called it.
    from omega_hub.tests.helpers import make_person

    dave = make_person("dave")
    keep = world.maint_b.email
    sent = EmailOutbox.objects.create(template="t", recipients=[dave.email], subject="s", body="b", status=EmailOutboxStatus.SENT)
    shared = EmailOutbox.objects.create(template="t", recipients=[dave.email, keep], subject="s", body="b")
    with django_capture_on_commit_callbacks(execute=True):
        dave.user.delete()
    assert not EmailOutbox.objects.filter(pk=sent.pk).exists()
    assert EmailOutbox.objects.get(pk=shared.pk).recipients == [keep]
    assert not EmailOutbox.objects.filter(recipients__contains=[dave.email]).exists()
