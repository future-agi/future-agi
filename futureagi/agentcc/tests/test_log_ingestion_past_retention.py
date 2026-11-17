"""A request delivered after its org's retention window is metered, not stored.

The retention purge removes request logs older than the org's
privacy.retention.request_log_days. A late delivery of such a request (a batch
the gateway kept through an outage) is not stored, since that would only bring
a purged row back, but it is still a gateway request: it is metered, one event
per request, so a redelivery in another batch meters the same event again and
the usage consumer keeps one.
"""

import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from accounts.models import Organization
from agentcc.models import AgentccRequestLog
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services.log_ingestion import ingest_request_logs


def _org(retention_days):
    org = Organization.objects.create(name=f"Retention {uuid.uuid4().hex[:6]}")
    privacy = {}
    if retention_days is not None:
        privacy = {"retention": {"request_log_days": retention_days}}
    AgentccOrgConfig.no_workspace_objects.create(
        organization=org, version=1, is_active=True, privacy=privacy
    )
    return org


def _entry(org, request_id, days_old):
    return {
        "request_id": request_id,
        "model": "gpt-4o-mini",
        "provider": "openai",
        "status_code": 200,
        "cost": 0.01,
        "timestamp": (timezone.now() - timedelta(days=days_old)).isoformat(),
        "metadata": {"org_id": str(org.id)},
    }


def _stored(org):
    return set(
        AgentccRequestLog.all_objects.filter(organization=org).values_list(
            "request_id", flat=True
        )
    )


def _metered_ids(emit):
    """The request ids of each metering call, one tuple per call."""
    return [
        tuple(entry["request_id"] for entry in call.args[1])
        for call in emit.call_args_list
    ]


@pytest.mark.integration
@pytest.mark.django_db
def test_an_entry_past_retention_is_not_stored_but_is_metered():
    org = _org(retention_days=7)
    with patch("agentcc.services.log_ingestion._emit_usage_events") as emit:
        added = ingest_request_logs(
            [_entry(org, "req_late", 10), _entry(org, "req_recent", 1)]
        )
    assert added == 1
    assert _stored(org) == {"req_recent"}
    metered = _metered_ids(emit)
    assert ("req_recent",) in metered
    assert ("req_late",) in metered


@pytest.mark.integration
@pytest.mark.django_db
def test_a_redelivered_late_entry_is_metered_under_its_own_event():
    # One metering call per late request: its event is the same in any batch.
    org = _org(retention_days=7)
    with patch("agentcc.services.log_ingestion._emit_usage_events") as emit:
        ingest_request_logs([_entry(org, "req_late", 10)])
        ingest_request_logs([_entry(org, "req_late", 10), _entry(org, "req_other", 9)])
    assert _stored(org) == set()
    assert _metered_ids(emit) == [("req_late",), ("req_late",), ("req_other",)]


@pytest.mark.integration
@pytest.mark.django_db
def test_an_org_without_retention_stores_every_entry():
    org = _org(retention_days=None)
    with patch("agentcc.services.log_ingestion._emit_usage_events"):
        added = ingest_request_logs([_entry(org, "req_old", 400)])
    assert added == 1
    assert _stored(org) == {"req_old"}
