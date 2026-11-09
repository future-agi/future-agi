"""``webhooks.publish()`` on Postgres (DESIGN.md sections 3, 7.4 and 13.1; R4, R12, R13).

Needs the FA test database (pytest-django). The NOTIFY tests open a second, autocommit connection with the same
settings and LISTEN on it, so they prove that the wake-up is delivered on COMMIT and never on ROLLBACK.
"""

from __future__ import annotations

import psycopg
import pytest
from django.db import connection, transaction

from webhooks.models import Event
from webhooks.publish import SourceKeyConflict, UnknownEventType, publish
from webhooks.tests.factories import T0, label_event_data, make_endpoint, use_test_keys


def _publish(org, ws, key="k1", digest="h1", type_="prompt.label.changed"):
    return publish(
        type=type_,
        organization_id=org.id,
        workspace_id=ws.id,
        data=label_event_data(),
        source_key=key,
        source_hash=digest,
        producer="model_hub.prompts",
        occurred_at=T0,
        subject_kind="prompt",
        subject_id="p1",
    )


@pytest.mark.django_db
def test_no_live_endpoint_writes_no_row(organization, workspace):
    with transaction.atomic():
        assert _publish(organization, workspace) == (None, False)
    assert Event.objects.count() == 0


@pytest.mark.django_db
def test_one_event_per_source_key(organization, workspace, monkeypatch):
    use_test_keys(monkeypatch)
    make_endpoint(organization, workspace)
    with transaction.atomic():
        eid, created = _publish(organization, workspace)
        assert created and eid.startswith("evt_") and len(eid) == 30
        again = _publish(organization, workspace)
        assert (
            again == (eid, False) and again.duplicate
        )  # B8 answers 202 with duplicate: true
        with pytest.raises(SourceKeyConflict):
            _publish(organization, workspace, digest="other")
    ev = Event.objects.get(id=eid)
    assert (ev.fanout_state, ev.type, ev.source_key) == (
        "pending",
        "prompt.label.changed",
        "k1",
    )


@pytest.mark.django_db
def test_refuses_a_type_outside_the_catalog(organization, workspace, monkeypatch):
    # Refused even with no endpoint, so a producer bug fails the producer's own tests, never the dispatcher.
    with transaction.atomic(), pytest.raises(UnknownEventType):
        _publish(organization, workspace, type_="prompt.label.moved")
    use_test_keys(monkeypatch)
    make_endpoint(organization, workspace)
    with transaction.atomic(), pytest.raises(UnknownEventType):
        _publish(organization, workspace, type_="prompt.*")
    assert Event.objects.count() == 0


@pytest.mark.django_db(transaction=True)
def test_refuses_to_run_outside_a_transaction(organization, workspace, monkeypatch):
    use_test_keys(monkeypatch)
    make_endpoint(organization, workspace)
    with pytest.raises(RuntimeError):
        _publish(organization, workspace)


def _listener():
    s = connection.settings_dict
    conn = psycopg.connect(
        host=s["HOST"] or None,
        port=s["PORT"] or None,
        user=s["USER"],
        password=s["PASSWORD"],
        dbname=s["NAME"],
        autocommit=True,
    )
    conn.execute("LISTEN webhooks")
    return conn


@pytest.mark.django_db(transaction=True)
def test_notify_arrives_on_commit_only(organization, workspace, monkeypatch):
    use_test_keys(monkeypatch)
    make_endpoint(organization, workspace)
    lis = _listener()
    try:
        with transaction.atomic():
            _publish(organization, workspace, key="commit")
            assert (
                list(lis.notifies(timeout=0.5, stop_after=1)) == []
            )  # nothing before COMMIT
        got = list(lis.notifies(timeout=5, stop_after=1))
        assert [n.channel for n in got] == ["webhooks"]

        with pytest.raises(ZeroDivisionError), transaction.atomic():
            _publish(organization, workspace, key="rollback")
            1 / 0  # noqa: B018 - the producer's change fails after publish()
        assert list(lis.notifies(timeout=1, stop_after=1)) == []
        assert not Event.objects.filter(source_key="rollback").exists()
    finally:
        lis.close()
