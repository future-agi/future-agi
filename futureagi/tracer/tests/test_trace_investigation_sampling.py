import uuid
from copy import deepcopy
from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone

from tracer.models.trace_investigation import (
    TraceInvestigationAttempt,
    TraceInvestigationDelivery,
    TraceInvestigationJob,
)
from tracer.models.trace_scan import TraceScanConfig
from tracer.queries.trace_scanner import is_trace_sampled
from tracer.services.trace_investigation import (
    InvestigationConflict,
    claim_due_investigations,
    record_trace_notifications,
)
from tracer.tests.test_trace_investigation_control import _configure, _delivery
from tracer.tests.test_trace_investigation_reconciliation import (
    FakeRootReader,
    _root,
    _run,
)

pytestmark = pytest.mark.django_db


def test_reused_broker_offsets_preserve_both_event_receipts(observe_project):
    _configure(observe_project)
    first = _delivery(observe_project, offset=0)
    replacement = _delivery(observe_project, offset=0)
    record_trace_notifications(deliveries=[first])
    assert record_trace_notifications(deliveries=[replacement])["accepted_events"] == 1
    assert TraceInvestigationDelivery.no_workspace_objects.count() == 2
    assert TraceInvestigationJob.no_workspace_objects.count() == 2

    relocated = deepcopy(first)
    relocated["offset"] = 99
    assert record_trace_notifications(deliveries=[relocated])["duplicate_events"] == 1
    assert TraceInvestigationDelivery.no_workspace_objects.count() == 2
    assert set(
        TraceInvestigationJob.no_workspace_objects.values_list("generation", flat=True)
    ) == {1}
    relocated["value"]["traces"][0]["root_span_id"] = "fedcba9876543210"
    with pytest.raises(InvestigationConflict):
        record_trace_notifications(deliveries=[relocated])


@pytest.mark.parametrize("rate", [0, 0.5, 1])
def test_notification_admission_uses_stable_project_sample(observe_project, rate):
    config = _configure(observe_project)
    config.sampling_rate = rate
    config.save(update_fields=["sampling_rate"])
    delivery = _delivery(observe_project)
    delivery["value"]["traces"] = [
        {**delivery["value"]["traces"][0], "trace_id": uuid.UUID(int=index)}
        for index in range(1, 17)
    ]
    expected = {
        row["trace_id"]
        for row in delivery["value"]["traces"]
        if is_trace_sampled(str(row["trace_id"]), rate)
    }
    record_trace_notifications(deliveries=[delivery])
    record_trace_notifications(deliveries=[deepcopy(delivery)])
    assert (
        set(
            TraceInvestigationJob.no_workspace_objects.values_list(
                "trace_id", flat=True
            )
        )
        == expected
    )
    assert TraceInvestigationDelivery.no_workspace_objects.count() == 1


def test_default_zero_rate_creates_no_jobs(observe_project):
    config = _configure(observe_project)
    config.sampling_rate = TraceScanConfig._meta.get_field(
        "sampling_rate"
    ).get_default()
    config.save(update_fields=["sampling_rate"])
    assert config.sampling_rate == 0
    delivery = _delivery(observe_project)
    record_trace_notifications(deliveries=[delivery])
    config.sampling_rate = 1
    config.save(update_fields=["sampling_rate"])
    record_trace_notifications(deliveries=[deepcopy(delivery)])
    assert not TraceInvestigationJob.no_workspace_objects.exists()


@pytest.mark.parametrize("rate", [0, 0.5, 1])
@override_settings(
    ERROR_FEED_OMEGA_ENABLED=True,
    ERROR_FEED_OMEGA_DELAY_SECONDS=0,
    ERROR_FEED_OMEGA_PROJECT_CONCURRENCY=32,
)
def test_claim_rechecks_rate_after_enqueue(observe_project, rate):
    config = _configure(observe_project)
    deliveries = [
        _delivery(observe_project, offset=index, trace_id=uuid.UUID(int=index))
        for index in range(1, 17)
    ]
    record_trace_notifications(deliveries=deliveries)
    config.sampling_rate = rate
    config.save(update_fields=["sampling_rate"])
    claimed = []
    for _ in deliveries:
        claimed.extend(
            claim_due_investigations(
                worker_id="test", engine_version="omega-v1", limit=1
            )["claims"]
        )
    expected = {
        item["value"]["traces"][0]["trace_id"]
        for item in deliveries
        if is_trace_sampled(str(item["value"]["traces"][0]["trace_id"]), rate)
    }
    assert {claim["trace_id"] for claim in claimed} == expected
    assert TraceInvestigationAttempt.no_workspace_objects.count() == len(expected)


@pytest.mark.parametrize("rate", [0, 0.5, 1])
def test_reconciliation_samples_and_advances_past_excluded_traces(
    observe_project, rate
):
    config = _configure(observe_project)
    config.sampling_rate = rate
    config.save(update_fields=["sampling_rate"])
    now = timezone.now()
    traces = [str(uuid.UUID(int=index)) for index in range(1, 17)]
    reader = FakeRootReader(
        now,
        {
            str(observe_project.id): {
                trace: _root(now - timedelta(minutes=1)) for trace in traces
            }
        },
    )
    _run(reader)
    assert set(
        map(
            str,
            TraceInvestigationJob.no_workspace_objects.values_list(
                "trace_id", flat=True
            ),
        )
    ) == {trace for trace in traces if is_trace_sampled(trace, rate)}
    assert _run(reader)["jobs_created"] == 0
    if rate == 0:
        assert reader.page_calls == []
