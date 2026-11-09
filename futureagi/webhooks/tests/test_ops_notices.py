"""The customer notices on Postgres (DESIGN.md sections 7.7 and 13.3; the email half of WT-12, at exact boundaries).

"Failing": 3 consecutive failed attempts spanning at least 10 min, at most once per 24 h, only while the endpoint is
on, to the endpoint's owner and the workspace's admins (never a plain member). "Turned off": once for each automatic
turn-off. "Creator removed" (G1): once per offboarding turn-off, one email per member and workspace, to the admins
and never the member. A notice whose send fails is given back, logged by its error type only, and goes out on the next pass.
The emails go through ``email_helper`` to Django's test mail sink. WT-12 runs the same rules end to end on the real
dispatcher (``test_ops_health``).
"""

from __future__ import annotations

import logging
import smtplib
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from django.core import mail

from accounts.models import User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from webhooks import health_pass, notices
from webhooks.models import Endpoint, EndpointState, OpsAlert, OpsSnapshot
from webhooks.store_settle import disable_endpoint
from webhooks.tests.factories import make_endpoint, use_test_keys

URL = "https://hooks.acme.ai/omega?token=canary-n1"


@pytest.fixture
def keys(monkeypatch):
    use_test_keys(monkeypatch)


def _member(organization, workspace, ws_role, ws_level, name="Member") -> User:
    u = User.objects.create_user(
        email=f"m-{uuid.uuid4().hex[:8]}@futureagi.com",
        password=uuid.uuid4().hex,
        name=name,
        organization=organization,
        organization_role=OrganizationRoles.MEMBER,
    )
    om, _ = OrganizationMembership.no_workspace_objects.get_or_create(
        user=u,
        organization=organization,
        defaults={
            "role": OrganizationRoles.MEMBER,
            "level": Level.MEMBER,
            "is_active": True,
        },
    )
    WorkspaceMembership.no_workspace_objects.get_or_create(
        user=u,
        workspace=workspace,
        defaults={
            "role": ws_role,
            "level": ws_level,
            "is_active": True,
            "organization_membership": om,
        },
    )
    return u


def _sent(subject: str) -> list:
    return [m for m in mail.outbox if m.subject == subject]


def _failing(ep, run: int, span: timedelta, at: datetime) -> None:
    """``run`` consecutive failures, the first ``span`` before the last; the last one a minute before ``at``."""
    last = at - timedelta(minutes=1)
    EndpointState.objects.filter(endpoint=ep).update(
        consecutive_failed_attempts=run,
        failing_since=last - span,
        last_failure_at=last,
        last_outcome="http_error",
        last_status_code=502,
    )


@pytest.mark.django_db
def test_failing_needs_three_failures_over_ten_minutes_and_repeats_once_a_day(
    organization, workspace, user, keys
):
    ep = make_endpoint(organization, workspace, url=URL, owner=user, name="n1")
    t = datetime.now(UTC)
    _failing(ep, 2, timedelta(minutes=30), t)
    assert notices.send_failing(t) == 0  # two failures, however far apart
    _failing(ep, 3, timedelta(minutes=9, seconds=59), t)
    assert notices.send_failing(t) == 0  # three, but under 10 minutes
    _failing(ep, 3, timedelta(minutes=10, seconds=1), t)
    assert notices.send_failing(t) == 1
    assert notices.send_failing(t + timedelta(minutes=5)) == 0  # one pass later
    assert notices.send_failing(t + timedelta(hours=23, minutes=59)) == 0
    assert notices.send_failing(t + timedelta(hours=24, seconds=1)) == 1
    sent = _sent("Webhook failing: n1")
    assert len(sent) == 2
    body = sent[0].body
    assert "n1" in body and "hooks.acme.ai/omega?token=" in body and "502" in body
    assert "canary-n1" not in body  # the masked URL only

    # A 2xx ends it.
    EndpointState.objects.filter(endpoint=ep).update(
        consecutive_failed_attempts=0, last_success_at=t
    )
    assert notices.send_failing(t + timedelta(hours=49)) == 0
    # Failing again, but turned off by hand: no Failing email until it is on again.
    _failing(ep, 5, timedelta(hours=1), t + timedelta(hours=49))
    Endpoint.objects.filter(id=ep.id).update(status="disabled_manual")
    assert notices.send_failing(t + timedelta(hours=49)) == 0
    Endpoint.objects.filter(id=ep.id).update(status="enabled")
    assert notices.send_failing(t + timedelta(hours=49)) == 1
    assert len(_sent("Webhook failing: n1")) == 3


@pytest.mark.django_db
def test_notices_go_to_the_owner_and_workspace_admins_only(
    organization, workspace, user, keys
):
    admin = _member(
        organization,
        workspace,
        OrganizationRoles.WORKSPACE_ADMIN,
        Level.WORKSPACE_ADMIN,
    )
    plain = _member(
        organization,
        workspace,
        OrganizationRoles.WORKSPACE_MEMBER,
        Level.WORKSPACE_MEMBER,
    )
    ep = make_endpoint(organization, workspace, url=URL, owner=user, name="n2")
    t = datetime.now(UTC)
    _failing(ep, 3, timedelta(minutes=11), t)
    assert notices.send_failing(t) == 1
    (failing,) = _sent("Webhook failing: n2")
    assert failing.to == [user.email, admin.email]
    disable_endpoint(ep.id, "gone_410", t)
    assert notices.send_turned_off(t + timedelta(seconds=1)) == 1
    (off,) = _sent("Webhook turned off: n2")
    assert off.to == [user.email, admin.email]
    assert "410" in off.body and plain.email not in off.to + failing.to


@pytest.mark.django_db
def test_a_notice_that_cannot_be_sent_is_given_back_and_goes_out_next_pass(
    organization, workspace, user, keys, monkeypatch, caplog
):
    import tfc.utils.email as fa_email

    real = fa_email.email_helper

    def refuse(*_a, **_kw):
        raise smtplib.SMTPRecipientsRefused({user.email: (550, b"no such user")})

    caplog.set_level(logging.INFO, logger="webhooks.notices")
    ep = make_endpoint(organization, workspace, url=URL, owner=user, name="n3")
    t = datetime.now(UTC)
    _failing(ep, 4, timedelta(minutes=20), t)

    monkeypatch.setattr(fa_email, "email_helper", refuse)
    assert notices.send_failing(t) == 0
    assert EndpointState.objects.get(endpoint=ep).last_failing_notice_at is None
    monkeypatch.setattr(fa_email, "email_helper", real)
    assert notices.send_failing(t + timedelta(seconds=60)) == 1
    assert len(_sent("Webhook failing: n3")) == 1

    disable_endpoint(ep.id, "failing", t + timedelta(seconds=61))
    monkeypatch.setattr(fa_email, "email_helper", refuse)
    assert notices.send_turned_off(t + timedelta(seconds=62)) == 0
    assert EndpointState.objects.get(endpoint=ep).last_disabled_notice_at is None
    monkeypatch.setattr(fa_email, "email_helper", real)
    assert notices.send_turned_off(t + timedelta(seconds=63)) == 1
    assert notices.send_turned_off(t + timedelta(seconds=64)) == 0  # once per turn-off
    (off,) = _sent("Webhook turned off: n3")
    assert "24 hours" in off.body

    # Turned on, then off again: a new turn-off, a new notice.
    Endpoint.objects.filter(id=ep.id).update(status="enabled")
    disable_endpoint(ep.id, "unsafe_address", t + timedelta(seconds=70))
    assert notices.send_turned_off(t + timedelta(seconds=71)) == 1
    assert len(_sent("Webhook turned off: n3")) == 2

    failed = [
        r
        for r in caplog.records
        if r.getMessage() == "webhooks notice could not be sent"
    ]
    assert len(failed) == 2
    for r in failed:  # by error type only: no address, no server answer
        assert r.levelno == logging.ERROR and r.error_type == "SMTPRecipientsRefused"
        assert r.exc_info is None
        text = r.getMessage() + repr(vars(r))
        assert user.email not in text and "no such user" not in text


def _flat(text: str) -> str:
    return " ".join(text.split())


def _remove_creator(eps, at: datetime) -> None:
    """What WH-2's offboarding writes (``turn_off_manual`` with reason ``creator_removed``)."""
    Endpoint.objects.filter(id__in=[e.id for e in eps]).update(
        status="disabled_manual", disabled_reason="creator_removed", disabled_at=at
    )


@pytest.mark.django_db
def test_creator_removed_one_email_per_member_and_workspace_to_the_admins(
    organization, workspace, user, keys
):
    admin = _member(
        organization,
        workspace,
        OrganizationRoles.WORKSPACE_ADMIN,
        Level.WORKSPACE_ADMIN,
    )
    asha = _member(
        organization,
        workspace,
        OrganizationRoles.WORKSPACE_MEMBER,
        Level.WORKSPACE_MEMBER,
        name="Asha Rao",
    )
    a1 = make_endpoint(organization, workspace, url=URL, owner=asha, name="a1")
    a2 = make_endpoint(organization, workspace, url=URL, owner=asha, name="a2")
    u1 = make_endpoint(organization, workspace, url=URL, owner=user, name="u1")
    t = datetime.now(UTC)
    _remove_creator([a1, a2], t)
    Endpoint.objects.filter(
        id=u1.id
    ).update(  # turned off by hand: no email of this kind
        status="disabled_manual", disabled_reason="manual", disabled_at=t
    )

    assert notices.send_creator_removed(t + timedelta(seconds=1)) == 1
    subject = f"Webhooks turned off: Asha Rao left {workspace.name}"
    (m,) = _sent(subject)
    assert m.to == [user.email, admin.email]  # the admins; never the member
    body = _flat(m.body)
    assert (
        f"Asha Rao left {workspace.name}; their 2 webhooks are off until an admin reviews them."
        in body
    )
    assert "a1: https://hooks.acme.ai/omega?token=" in body and "a2:" in body
    assert "u1" not in body and "canary-n1" not in body
    assert notices.send_creator_removed(t + timedelta(seconds=2)) == 0  # once
    assert (
        notices.send_turned_off(t + timedelta(seconds=2)) == 0
    )  # not an automatic turn-off

    # An admin turns a1 on (and owns it); a later offboarding of its owner is a new turn-off, through the health pass.
    Endpoint.objects.filter(id=a1.id).update(status="enabled")
    _remove_creator([a1], t + timedelta(seconds=10))
    assert health_pass.run(t + timedelta(seconds=11))["creator_removed_notices"] == 1
    assert len(_sent(subject)) == 2
    assert "their 1 webhook is off until an admin reviews it." in _flat(
        _sent(subject)[1].body
    )


@pytest.mark.django_db
def test_creator_removed_that_cannot_be_sent_is_given_back(
    organization, workspace, user, keys, monkeypatch
):
    import tfc.utils.email as fa_email

    def refuse(*_a, **_kw):
        raise smtplib.SMTPServerDisconnected("gone")

    asha = _member(
        organization,
        workspace,
        OrganizationRoles.WORKSPACE_MEMBER,
        Level.WORKSPACE_MEMBER,
        name="Asha Rao",
    )
    eps = [make_endpoint(organization, workspace, owner=asha) for _ in range(2)]
    t = datetime.now(UTC)
    _remove_creator(eps, t)
    real = fa_email.email_helper
    monkeypatch.setattr(fa_email, "email_helper", refuse)
    assert notices.send_creator_removed(t + timedelta(seconds=1)) == 0
    assert not EndpointState.objects.filter(
        endpoint__in=eps, last_disabled_notice_at__isnull=False
    ).exists()
    monkeypatch.setattr(fa_email, "email_helper", real)
    assert notices.send_creator_removed(t + timedelta(seconds=2)) == 1
    (m,) = _sent(f"Webhooks turned off: Asha Rao left {workspace.name}")
    assert "their 2 webhooks" in _flat(m.body)


@pytest.mark.django_db
def test_a_health_step_that_raises_never_skips_the_gauges_alerts_or_other_notices(
    organization, workspace, keys, monkeypatch, caplog
):
    """An exception outside the send (here the whole Failing step) is logged with the step's name and error type; the
    gauges, the stored snapshot, the alerts and the other notice steps still run."""
    ep = make_endpoint(organization, workspace, url=URL, name="n9")
    t = datetime.now(UTC)
    disable_endpoint(ep.id, "gone_410", t)

    def boom(*_a, **_kw):
        raise TimeoutError("the email provider did not answer")

    monkeypatch.setattr(notices, "send_failing", boom)
    caplog.set_level(logging.INFO, logger="webhooks.health")
    out = health_pass.run(t + timedelta(seconds=1))
    assert out["failed_steps"] == ["failing_notices"] and out["failing_notices"] is None
    assert out["turned_off_notices"] == 1 and isinstance(out["alerts_changed"], list)
    assert OpsSnapshot.objects.filter(id="stats").exists()
    assert OpsAlert.objects.filter(id="WA1").exists()
    (line,) = [
        r
        for r in caplog.records
        if getattr(r, "metric", None) == "webhooks.health.step_failed"
    ]
    assert line.levelno == logging.ERROR
    assert (line.step, line.error_type) == ("failing_notices", "TimeoutError")


@pytest.mark.django_db
def test_a_send_that_hangs_is_bounded_given_back_and_holds_one_thread_only(
    organization, workspace, user, keys, monkeypatch, caplog
):
    """FA sets no EMAIL_TIMEOUT, so an SMTP server can stop answering mid-send. The pass returns after
    ``SEND_TIMEOUT_S`` with the notice given back; while that send is stuck the next notice is given back at once,
    never a second thread; once the send ends, the next pass sends both."""
    import tfc.utils.email as fa_email

    real = fa_email.email_helper
    release = threading.Event()
    started: list[str] = []

    def hang(subject, *_a, **_kw):
        started.append(subject)
        release.wait(30)
        raise smtplib.SMTPServerDisconnected("answered at last")

    monkeypatch.setattr(notices, "SEND_TIMEOUT_S", 0.5)
    monkeypatch.setattr(notices, "_stuck", None)
    monkeypatch.setattr(fa_email, "email_helper", hang)
    caplog.set_level(logging.INFO, logger="webhooks.notices")
    failing = make_endpoint(organization, workspace, url=URL, owner=user, name="h1")
    off = make_endpoint(organization, workspace, url=URL, owner=user, name="h2")
    t = datetime.now(UTC)
    _failing(failing, 4, timedelta(minutes=20), t)
    disable_endpoint(off.id, "gone_410", t)
    t0 = time.monotonic()
    out = health_pass.run(t + timedelta(seconds=1))
    assert time.monotonic() - t0 < 5
    assert (out["failing_notices"], out["turned_off_notices"]) == (0, 0)
    assert "failed_steps" not in out and OpsSnapshot.objects.filter(id="stats").exists()
    assert started == ["Webhook failing: h1"]  # one thread held, no second send
    assert EndpointState.objects.get(endpoint=failing).last_failing_notice_at is None
    assert EndpointState.objects.get(endpoint=off).last_disabled_notice_at is None
    stuck = [
        r
        for r in caplog.records
        if getattr(r, "metric", None) == "webhooks.notice.stuck"
    ]
    assert len(stuck) == 2 and {r.levelno for r in stuck} == {logging.ERROR}

    release.set()
    notices._stuck.join(5)
    monkeypatch.setattr(fa_email, "email_helper", real)
    out = health_pass.run(t + timedelta(seconds=2))
    assert (out["failing_notices"], out["turned_off_notices"]) == (1, 1)
    assert len(_sent("Webhook failing: h1")) == 1
    assert len(_sent("Webhook turned off: h2")) == 1
