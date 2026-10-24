"""Retention of the gateway's request records.

``purge_expired_request_logs`` deletes, per org with a numeric
``privacy.retention.request_log_days``, the request logs and outcomes older
than that and the attempts older than ``attempt_days`` (default: the log
TTL). Ledger entries are never touched; a null TTL keeps rows forever.
"""

import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import Organization
from accounts.models.audit_log import AuditLog
from agentcc.models import AgentccRequestLog
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.models.request_attempt import AgentccRequestAttempt
from agentcc.models.request_outcome import AgentccRequestOutcome
from agentcc.services import retention
from agentcc.services.retention import (
    AUDIT_ACTION_RETENTION_PURGE,
    RetentionPurgeError,
    purge_expired_request_logs,
    retention_policy,
)

NOW = timezone.now()


def _days_ago(days):
    return NOW - timedelta(days=days)


def _org(name, privacy):
    org = Organization.objects.create(name=f"{name} {uuid.uuid4().hex[:6]}")
    AgentccOrgConfig.no_workspace_objects.create(
        organization=org, version=1, is_active=True, privacy=privacy
    )
    return org


def _log(org, days_old, *, started=True):
    log = AgentccRequestLog.no_workspace_objects.create(
        organization=org,
        request_id=f"req_{uuid.uuid4().hex[:10]}",
        started_at=_days_ago(days_old) if started else None,
    )
    if not started:  # created_at is auto_now_add; age it by hand
        AgentccRequestLog.all_objects.filter(pk=log.pk).update(
            created_at=_days_ago(days_old)
        )
    return log


def _attempt(org, days_old, log=None):
    request_id = log.request_id if log else f"req_{uuid.uuid4().hex[:10]}"
    attempt = AgentccRequestAttempt.objects.create(
        organization=org,
        request_log=log,
        request_id=request_id,
        attempt_id=f"{request_id}.{uuid.uuid4().hex[:4]}",
        index=1,
        kind=AgentccRequestAttempt.Kind.PRIMARY,
        outcome=AgentccRequestAttempt.Outcome.SUCCESS,
    )
    AgentccRequestAttempt.objects.filter(pk=attempt.pk).update(
        created_at=_days_ago(days_old)
    )
    return attempt


def _outcome(org, days_old):
    return AgentccRequestOutcome.objects.create(
        organization=org,
        request_id=f"req_{uuid.uuid4().hex[:10]}",
        outcome=AgentccRequestOutcome.Outcome.OK,
        source=AgentccRequestOutcome.Source.FEEDBACK,
        event_id=f"evt_{uuid.uuid4().hex[:10]}",
        occurred_at=_days_ago(days_old),
    )


def _logs(org):
    return set(
        AgentccRequestLog.all_objects.filter(organization=org).values_list(
            "pk", flat=True
        )
    )


def _attempts(org):
    return set(
        AgentccRequestAttempt.objects.filter(organization=org).values_list(
            "pk", flat=True
        )
    )


def _outcomes(org):
    return set(
        AgentccRequestOutcome.objects.filter(organization=org).values_list(
            "pk", flat=True
        )
    )


@pytest.mark.parametrize(
    "privacy,want",
    [
        ({}, (None, None)),
        ({"retention": None}, (None, None)),
        ({"retention": {"request_log_days": 7}}, (7, 7)),
        ({"retention": {"request_log_days": 7, "attempt_days": 2}}, (7, 2)),
        ({"retention": {"attempt_days": 2}}, (None, 2)),
        ({"retention": {"request_log_days": 7, "attempt_days": None}}, (7, 7)),
        ({"retention": {"request_log_days": 0}}, (None, None)),
        ({"retention": {"request_log_days": -3}}, (None, None)),
        ({"retention": {"request_log_days": "7"}}, (None, None)),
        ({"retention": {"request_log_days": True}}, (None, None)),
        ({"retention": {"request_log_days": 7.5}}, (None, None)),
        # JSON Schema's integer type accepts 30.0, so a whole float counts.
        ({"retention": {"request_log_days": 30.0}}, (30, 30)),
        ({"retention": {"request_log_days": 7.0, "attempt_days": 2.0}}, (7, 2)),
        ({"retention": {"request_log_days": 0.0}}, (None, None)),
        ({"retention": {"request_log_days": float("inf")}}, (None, None)),
        ({"retention": {"request_log_days": float("nan")}}, (None, None)),
        ({"retention": "forever"}, (None, None)),
    ],
)
def test_retention_policy_reads_positive_whole_days(privacy, want):
    policy = retention_policy(privacy)
    assert (policy.request_log_days, policy.attempt_days) == want


@pytest.mark.integration
@pytest.mark.django_db
def test_only_orgs_with_a_ttl_lose_old_rows():
    org_a = _org("A", {"retention": {"request_log_days": 7}})
    org_b = _org("B", {"retention": {"request_log_days": None}})
    org_c = _org("C", {})
    old_a, new_a = _log(org_a, 8), _log(org_a, 6)
    old_a_unstarted, new_a_unstarted = (
        _log(org_a, 8, started=False),
        _log(org_a, 1, started=False),
    )
    keep_b, keep_c = {_log(org_b, 400).pk}, {_log(org_c, 400).pk}

    result = purge_expired_request_logs(now=NOW)

    assert _logs(org_a) == {new_a.pk, new_a_unstarted.pk}
    assert old_a.pk not in _logs(org_a) and old_a_unstarted.pk not in _logs(org_a)
    assert _logs(org_b) == keep_b
    assert _logs(org_c) == keep_c
    assert result == {str(org_a.id): {"request_logs": 2, "outcomes": 0, "attempts": 0}}


@pytest.mark.integration
@pytest.mark.django_db
def test_soft_deleted_logs_are_purged_too():
    org = _org("A", {"retention": {"request_log_days": 7}})
    log = _log(org, 30)
    AgentccRequestLog.all_objects.filter(pk=log.pk).update(deleted=True)

    purge_expired_request_logs(now=NOW)

    assert not AgentccRequestLog.all_objects.filter(pk=log.pk).exists()


@pytest.mark.integration
@pytest.mark.django_db
def test_attempts_go_by_their_own_age():
    org = _org("A", {"retention": {"request_log_days": 30, "attempt_days": 7}})
    young_log = _log(org, 1)
    # An old attempt of a log that stays, and a young attempt of a log that goes.
    old_attempt = _attempt(org, 10, log=young_log)
    old_log = _log(org, 40)
    young_attempt = _attempt(org, 2, log=old_log)
    orphan_old = _attempt(org, 10)

    result = purge_expired_request_logs(now=NOW)

    assert _logs(org) == {young_log.pk}
    assert _attempts(org) == {young_attempt.pk}
    young_attempt.refresh_from_db()
    assert young_attempt.request_log_id is None  # its log went first (SET_NULL)
    assert old_attempt.pk not in _attempts(org) and orphan_old.pk not in _attempts(org)
    assert result[str(org.id)] == {"request_logs": 1, "outcomes": 0, "attempts": 2}


@pytest.mark.integration
@pytest.mark.django_db
def test_attempt_days_default_to_the_log_ttl():
    org = _org("A", {"retention": {"request_log_days": 7}})
    old, young = _attempt(org, 8), _attempt(org, 6)

    purge_expired_request_logs(now=NOW)

    assert _attempts(org) == {young.pk}
    assert old.pk not in _attempts(org)


@pytest.mark.integration
@pytest.mark.django_db
def test_outcomes_go_by_the_log_ttl():
    org = _org("A", {"retention": {"request_log_days": 7, "attempt_days": 365}})
    old, young = _outcome(org, 8), _outcome(org, 6)
    only_attempts = _org("B", {"retention": {"attempt_days": 1}})
    kept = _outcome(only_attempts, 100)

    purge_expired_request_logs(now=NOW)

    assert _outcomes(org) == {young.pk}
    assert old.pk not in _outcomes(org)
    assert _outcomes(only_attempts) == {kept.pk}


@pytest.mark.integration
@pytest.mark.django_db
def test_ledger_entries_remain():
    managed_credits = pytest.importorskip("ee.usage.models.managed_credits")
    org = _org("A", {"retention": {"request_log_days": 1, "attempt_days": 1}})
    _log(org, 30)
    entry = managed_credits.ManagedCreditEntry.objects.create(
        organization=org,
        kind="spend",
        amount_micros=-1200,
        idempotency_key=f"retention-{uuid.uuid4().hex}",
        request_id="req_old",
        occurred_at=_days_ago(400),
    )

    purge_expired_request_logs(now=NOW)

    assert managed_credits.ManagedCreditEntry.objects.filter(pk=entry.pk).exists()
    assert _logs(org) == set()


@pytest.mark.integration
@pytest.mark.django_db
def test_batching_loops_until_done():
    org = _org("A", {"retention": {"request_log_days": 7}})
    for _ in range(5):
        _log(org, 10)
        _attempt(org, 10)
    keep = _log(org, 1)

    with patch.object(
        retention, "_delete_batch", wraps=retention._delete_batch
    ) as batches:
        result = purge_expired_request_logs(now=NOW, batch_size=2)

    per_model = [c.args[0] for c in batches.call_args_list]
    assert per_model.count(AgentccRequestLog) == 3  # 2 + 2 + 1
    assert per_model.count(AgentccRequestAttempt) == 3
    assert all(len(c.args[1]) <= 2 for c in batches.call_args_list)
    assert _logs(org) == {keep.pk}
    assert result[str(org.id)] == {"request_logs": 5, "outcomes": 0, "attempts": 5}


@pytest.mark.integration
@pytest.mark.django_db
def test_batches_never_load_request_bodies():
    # A log row can hold large bodies; deleting it must not read them, or a
    # batch of 5,000 would load them all into memory.
    org = _org("A", {"retention": {"request_log_days": 7}})
    for _ in range(3):
        log = _log(org, 10)
        AgentccRequestLog.all_objects.filter(pk=log.pk).update(
            request_body={"messages": [{"content": "x" * 1000}]}
        )

    with CaptureQueriesContext(connection) as queries:
        result = purge_expired_request_logs(now=NOW)

    assert result[str(org.id)]["request_logs"] == 3
    log_reads = [
        q["sql"]
        for q in queries.captured_queries
        if q["sql"].startswith("SELECT") and '"agentcc_request_log"' in q["sql"]
    ]
    assert log_reads, "the purge read no request-log rows"
    assert not [sql for sql in log_reads if "request_body" in sql], log_reads


@pytest.mark.integration
@pytest.mark.django_db
def test_one_org_failing_does_not_stop_the_others():
    broken = _org("A", {"retention": {"request_log_days": 7}})
    healthy = _org("B", {"retention": {"request_log_days": 7}})
    broken_log, healthy_log = _log(broken, 10), _log(healthy, 10)
    purge_org = retention._purge_org

    def fail_for_broken(org, *args):
        if org.id == broken.id:
            raise RuntimeError("statement timeout")
        return purge_org(org, *args)

    with patch.object(retention, "_purge_org", side_effect=fail_for_broken):
        with pytest.raises(RetentionPurgeError) as raised:
            purge_expired_request_logs(now=NOW)

    assert str(broken.id) in str(raised.value)
    assert healthy_log.pk not in _logs(healthy)
    assert _logs(broken) == {broken_log.pk}
    # The org that went through still has its audit entry.
    assert AuditLog.objects.filter(
        organization=healthy, action=AUDIT_ACTION_RETENTION_PURGE
    ).exists()


@pytest.mark.integration
@pytest.mark.django_db
def test_counts_go_to_the_audit_log():
    org = _org("A", {"retention": {"request_log_days": 7}})
    _log(org, 9)
    _outcome(org, 9)
    idle = _org("B", {"retention": {"request_log_days": 7}})

    purge_expired_request_logs(now=NOW)

    entry = AuditLog.objects.get(organization=org, action=AUDIT_ACTION_RETENTION_PURGE)
    assert entry.target_id == org.id
    assert entry.actor is None
    assert entry.changes == {
        "deleted": {"request_logs": 1, "outcomes": 1, "attempts": 0}
    }
    assert entry.metadata["request_log_days"] == 7
    assert entry.metadata["attempt_days"] == 7
    # An org whose purge removed nothing gets no entry.
    assert not AuditLog.objects.filter(
        organization=idle, action=AUDIT_ACTION_RETENTION_PURGE
    ).exists()


@pytest.mark.integration
@pytest.mark.django_db
def test_inactive_config_versions_do_not_set_retention():
    org = Organization.objects.create(name=f"Versions {uuid.uuid4().hex[:6]}")
    AgentccOrgConfig.no_workspace_objects.create(
        organization=org,
        version=1,
        is_active=False,
        privacy={"retention": {"request_log_days": 1}},
    )
    AgentccOrgConfig.no_workspace_objects.create(
        organization=org, version=2, is_active=True, privacy={}
    )
    log = _log(org, 30)

    assert purge_expired_request_logs(now=NOW) == {}
    assert _logs(org) == {log.pk}


def test_retention_schedule_runs_the_purge_daily():
    from tfc.temporal.drop_in.decorator import _ACTIVITY_REGISTRY
    from tfc.temporal.schedules import ALL_SCHEDULES
    from tfc.temporal.schedules.agentcc_retention import AGENTCC_RETENTION_SCHEDULES

    (schedule,) = AGENTCC_RETENTION_SCHEDULES
    assert schedule.schedule_id == "agentcc-log-retention"
    assert schedule.interval_seconds == 86400
    assert schedule in ALL_SCHEDULES
    assert schedule.activity_name in _ACTIVITY_REGISTRY


@pytest.mark.integration
@pytest.mark.django_db
def test_retention_activity_calls_the_purge():
    from tfc.temporal.schedules import agentcc_retention

    with patch(
        "agentcc.services.retention.purge_expired_request_logs",
        return_value={"org": {"request_logs": 3, "outcomes": 0, "attempts": 1}},
    ) as purge:
        result = agentcc_retention.purge_expired_agentcc_request_logs_activity._original_func()

    purge.assert_called_once_with()
    assert result == {"org": {"request_logs": 3, "outcomes": 0, "attempts": 1}}
