"""The audit log is append-only and each state change writes exactly one event (SPEC §8)."""

import pytest

from omega_hub.models import AuditEvent, AuditEventImmutable
from omega_hub.tests.flows import publish
from omega_hub.tests.helpers import entry_files, submit, token_client

pytestmark = pytest.mark.django_db


def count(action, target_id=None):
    rows = AuditEvent.objects.filter(action=action)
    return rows.filter(target_id=str(target_id)).count() if target_id else rows.count()


def test_update_and_delete_raise(world):
    event = AuditEvent.objects.create(actor_kind="system", action="rules.notified", target_type="rules", target_id="1")
    event.action = "something.else"
    with pytest.raises(AuditEventImmutable):
        event.save()
    with pytest.raises(AuditEventImmutable):
        event.delete()
    with pytest.raises(AuditEventImmutable):
        AuditEvent.objects.filter(pk=event.pk).update(action="x")
    with pytest.raises(AuditEventImmutable):
        AuditEvent.objects.filter(pk=event.pk).delete()


def test_each_state_change_writes_one_event(world):
    entry = publish(world, "first-agent")
    sub = entry.latest_version.submission
    for action in ("submission.received", "check.claimed", "check.passed", "review.approved"):
        assert count(action, sub.pk) == 1, action
    assert count("version.published", entry.latest_version.pk) == 1
    eid = entry.pk
    steps = [
        ("post", "/omega-hub/v1/review/entries/future-agi/first-agent/unrank", {"reason": "quality", "note": "n"}, "entry.unranked"),
        ("post", "/omega-hub/v1/review/entries/future-agi/first-agent/rerank", {"note": "n"}, "entry.reranked"),
        ("post", "/omega-hub/v1/review/entries/future-agi/first-agent/remove", {"category": "security issue", "note": "n"}, "entry.removed"),
        ("post", "/omega-hub/v1/review/entries/future-agi/first-agent/restore", {"note": "n"}, "entry.restored"),
    ]
    for method, path, body, action in steps:
        assert getattr(world.b, method)(path, body, format="json").status_code == 200, path
        assert count(action, eid) == 1, action
    assert count("blocklist.added", eid) == 1 and count("blocklist.lifted", eid) == 1
    sub2 = submit(token_client(world.token_a), "future-agi", entry_files("notes-plugin")).json()["id"]
    token_client(world.token_a).post(f"/omega-hub/v1/submissions/{sub2}/withdraw")
    assert count("submission.withdrawn", sub2) == 1
    assert count("token.created") == 2  # the fixture's two tokens


def test_audit_endpoint_is_maintainer_only(world):
    publish(world, "first-agent")
    assert world.c.get("/omega-hub/v1/review/audit").status_code == 403
    page = world.b.get("/omega-hub/v1/review/audit?action=review.approved").json()
    assert page["count"] == 1 and page["results"][0]["actor"] == world.maint_b.email
