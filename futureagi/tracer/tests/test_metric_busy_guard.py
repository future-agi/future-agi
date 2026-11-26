# ruff: noqa: F811 -- shared real PostgreSQL fixtures
"""Control-plane regressions; dense native sizing is in the ClickHouse suite."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.core.exceptions import ImproperlyConfigured

from tracer.models.derived_metric import MetricProjectRefresh
from tracer.serializers.derived_metric import DerivedMetricSerializer
from tracer.services.derived_metrics import admission, busy_guard, reads
from tracer.services.derived_metrics.lease import MetricPassYield
from tracer.tests.test_derived_metric_admission import (  # noqa: F401
    NOW,
    enabled,
    state_factory,
)
from tracer.tests.test_derived_metric_api import ROOT, saved  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def _yield(lease, purpose):
    raise MetricPassYield()


def test_three_stalled_turns_pause_but_peer_keeps_fair_turns(enabled, state_factory):
    stuck = state_factory(pending_repair=True)
    healthy = state_factory(pending_backfill=True)
    seen = []
    for tick in range(6):
        now = NOW + timedelta(minutes=tick)
        (reservation,) = admission.reserve_projects(clock=lambda now=now: now)
        seen.append(reservation.grant.project_id)
        if reservation.grant.project_id == stuck.project_id:
            assert not admission.execute_reservation(
                reservation.payload(), clock=lambda now=now: now, runner=_yield
            )
        else:
            assert admission.execute_reservation(
                reservation.payload(), clock=lambda now=now: now, runner=lambda *_: None
            )
    assert seen.count(stuck.project_id) == seen.count(healthy.project_id) == 3
    stuck.refresh_from_db()
    assert stuck.paused_reason == "too_busy" and stuck.busy_yields == 3
    assert stuck.watermark_ns == 0 and stuck.head_manifest_id is None
    assert not stuck.lease_token
    assert all(type(value) is int for value in stuck.busy_counts.values())
    # No ordinary turns until explicitly resumed or capacity actually improves.
    assert busy_guard.resume_within_budget(now=now) == 0
    assert stuck.project_id not in [
        r.grant.project_id
        for r in admission.reserve_projects(clock=lambda now=now: now)
    ]


def test_watermark_or_pending_work_progress_never_counts_as_stalled(
    enabled, state_factory
):
    state = state_factory()
    for tick in range(5):
        now = NOW + timedelta(minutes=tick)
        (reservation,) = admission.reserve_projects(clock=lambda now=now: now)

        def progressing(lease, purpose, *, tick=tick):
            MetricProjectRefresh.objects.filter(pk=state.pk).update(
                watermark_ns=tick + 1
            )
            raise MetricPassYield()

        assert not admission.execute_reservation(
            reservation.payload(), clock=lambda now=now: now, runner=progressing
        )
        state.refresh_from_db()
        assert not state.paused_reason and state.busy_yields == 0


def test_explicit_resume_retries_existing_request_and_repauses(enabled, state_factory):
    state = state_factory(
        paused_reason="too_busy",
        paused_at=NOW,
        busy_yields=3,
        refresh_requested_at=NOW - timedelta(hours=1),
    )
    assert admission.request_refresh(state.project_id, clock=lambda: NOW)
    state.refresh_from_db()
    assert not state.paused_reason and state.busy_yields == 0
    for tick in range(3):
        now = NOW + timedelta(minutes=tick)
        (reservation,) = admission.reserve_projects(clock=lambda now=now: now)
        admission.execute_reservation(
            reservation.payload(), clock=lambda now=now: now, runner=_yield
        )
    state.refresh_from_db()
    assert state.paused_reason == "too_busy"


def test_auto_resume_requires_a_larger_fitting_capacity(
    enabled, state_factory, settings
):
    state = state_factory(
        paused_reason="too_busy",
        paused_at=NOW,
        busy_yields=3,
        busy_counts={"capture_capacity": 136, "estimated_captures": 176},
    )
    assert busy_guard.resume_within_budget(now=NOW) == 0
    settings.METRICS_BUSY_CAPTURE_MS = 2000  # 170, still too small
    assert busy_guard.resume_within_budget(now=NOW) == 0
    settings.METRICS_BUSY_CAPTURE_MS = 1500  # 226, now fits
    assert busy_guard.resume_within_budget(now=NOW) == 1
    state.refresh_from_db()
    assert (
        not state.paused_reason and state.busy_counts == {} and state.busy_yields == 0
    )


def test_paused_privacy_turn_does_not_reset_or_resume_busy_work(enabled, state_factory):
    state = state_factory(
        paused_reason="too_busy",
        paused_at=NOW,
        busy_yields=3,
        pending_expiry=True,
        busy_counts={"capture_capacity": 136, "estimated_captures": 176},
    )
    (reservation,) = admission.reserve_projects(clock=lambda: NOW)
    assert reservation.purpose == "expiry"

    def privacy_yield(lease, purpose):
        assert purpose == "expiry"
        raise MetricPassYield()

    assert not admission.execute_reservation(
        reservation.payload(), clock=lambda: NOW, runner=privacy_yield
    )
    state.refresh_from_db()
    assert state.paused_reason == "too_busy" and state.busy_yields == 3


def test_status_and_finality_expose_busy_reason_without_private_work(state_factory):
    work = {
        "owners": 3200,
        "conversation_definitions": 25,
        "span_definitions": 1,
        "captures": 1,
        "estimated_captures": 176,
        "capture_capacity": 136,
    }
    state = state_factory(paused_reason="too_busy", paused_at=NOW, busy_counts=work)
    metric = SimpleNamespace(project=state.project)
    status = DerivedMetricSerializer().get_calculation_paused(metric)
    assert status["reason"] == "too_busy" and status["work"] == work
    finality = reads.Finality(NOW, NOW, {}, paused=state.paused_reason)
    assert finality.final(NOW - timedelta(minutes=15))
    assert not finality.final(NOW)
    assert finality.reason({"window_start": NOW}) == "too_busy"


def test_estimate_uses_current_attempt_not_successful_earlier_pages(settings):
    writer = SimpleNamespace(
        lease=SimpleNamespace(),
        pass_captures=100,
        pass_span_captures=200,
        _busy_span_start=199,
    )
    definitions = [SimpleNamespace(level="conversation")] * 25 + [
        SimpleNamespace(level="span")
    ]
    work = busy_guard.counts(writer, owners=1601, definitions=definitions)
    assert work["captures"] == 300
    assert work["estimated_captures"] == 101 < busy_guard.capture_capacity()


@pytest.mark.parametrize(
    "name,value",
    [
        ("METRICS_BUSY_MAX_YIELDS", 1),
        ("METRICS_BUSY_MAX_YIELDS", True),
        ("METRICS_BUSY_CAPTURE_MS", 0),
    ],
)
def test_invalid_settings_fail_closed(settings, name, value):
    setattr(settings, name, value)
    with pytest.raises(ImproperlyConfigured):
        busy_guard.max_yields() if name.endswith(
            "YIELDS"
        ) else busy_guard.capture_capacity()


def test_expired_deadline_records_backstop_outside_the_writer_lock_budget(
    enabled, state_factory
):
    state = state_factory()

    def expired(lease, purpose):
        lease.deadline = lease.ticker() - 1
        lease.check()

    for number in range(3):
        now = NOW + timedelta(minutes=number)
        (reservation,) = admission.reserve_projects(clock=lambda now=now: now)
        assert not admission.execute_reservation(
            reservation.payload(), clock=lambda now=now: now, runner=expired
        )
        state.refresh_from_db()
        assert state.busy_yields == number + 1
        assert state.lease_token is None
    assert state.paused_reason == "too_busy"


def test_yield_receipt_accepts_only_nonidentifying_integer_counts(
    enabled, state_factory
):
    state = state_factory()
    (reservation,) = admission.reserve_projects(clock=lambda: NOW)

    def yielded(lease, purpose):
        lease.metric_work_counts = {
            "owners": 1,
            "span_definitions": 1,
            "raw_subject": "must-not-persist",
            "captures": "not a count",
            "estimated_captures": 1,
            "capture_capacity": 136,
        }
        raise MetricPassYield()

    assert not admission.execute_reservation(
        reservation.payload(), clock=lambda: NOW, runner=yielded
    )
    state.refresh_from_db()
    assert state.busy_counts == {
        "owners": 1,
        "conversation_definitions": 0,
        "span_definitions": 1,
        "captures": 0,
        "estimated_captures": 1,
        "capture_capacity": 136,
        "head_generation": 0,
    }


def test_old_worker_insert_gets_database_defaults_for_new_guard_columns(state_factory):
    from django.db import connection

    template, destination = state_factory(), state_factory()
    destination_id = destination.pk
    MetricProjectRefresh.objects.filter(pk=destination_id).delete()
    # An old worker writes all the columns it knows, omitting BOTH new fields.
    # Python model defaults cannot make this INSERT rolling-deployment safe.
    columns, selected, params = [], [], []
    quote = connection.ops.quote_name
    for field in MetricProjectRefresh._meta.concrete_fields:
        if field.name in {"busy_yields", "busy_counts"}:
            continue
        columns.append(quote(field.column))
        if field.name in {"id", "project"}:
            selected.append("%s")
            params.append(
                destination_id if field.name == "id" else destination.project_id
            )
        else:
            selected.append(quote(field.column))
    params.append(template.pk)
    table = quote(MetricProjectRefresh._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {table} ({', '.join(columns)}) SELECT {', '.join(selected)} FROM {table} WHERE id = %s",
            params,
        )
    restored = MetricProjectRefresh.objects.get(pk=destination_id)
    assert restored.busy_yields == 0 and restored.busy_counts == {}


def test_http_exposes_busy_counts_and_run_now_resumes(enabled, auth_client, saved):
    from django.utils import timezone

    now = timezone.now()
    work = {
        "owners": 3200,
        "conversation_definitions": 25,
        "span_definitions": 1,
        "captures": 1,
        "estimated_captures": 176,
        "capture_capacity": 136,
    }
    state, _ = MetricProjectRefresh.objects.update_or_create(
        project_id=saved["project_id"],
        defaults={
            "paused_reason": "too_busy",
            "paused_at": now,
            "paused_rows": None,
            "busy_counts": work,
            "busy_yields": 3,
            "last_started_at": now - timedelta(minutes=2),
            "refresh_requested_at": now - timedelta(minutes=3),
        },
    )
    url = f"{ROOT}{saved['id']}/"
    detail = auth_client.get(url)
    assert detail.status_code == 200
    status = detail.data["calculation_paused"]
    assert status["reason"] == "too_busy" and status["rows"] is None
    assert status["work"] == work
    listing = auth_client.get(ROOT, {"project_id": saved["project_id"]})
    assert listing.status_code == 200
    (item,) = [row for row in listing.data["results"] if row["id"] == saved["id"]]
    assert item["calculation_paused"]["work"] == work
    resumed = auth_client.post(url + "refresh/", {}, format="json")
    assert resumed.status_code == 202 and resumed.data == {"status": "queued"}
    state.refresh_from_db()
    assert (
        not state.paused_reason and state.busy_counts == {} and state.busy_yields == 0
    )
    assert state.refresh_requested_at >= now
