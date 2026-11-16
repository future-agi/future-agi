"""Ingesting a user-stopped call: the call reads cancelled, its sealed evidence is accepted, and the trial keeps the
records two participants named and the workers a notice names."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from simulate.models import CallExecution
from simulate.services import hosted_harness_ingestion as ingestion

pytestmark = pytest.mark.django_db


def test_user_canceled_receipt_marks_the_call_cancelled() -> None:
    body = {
        "status": "errored",
        "failure": {"code": "user_canceled", "domain": "platform_sync"},
        # The call genuinely started and ended: still cancelled, never completed.
        "call": {
            "started_at": "2026-11-14T10:00:00.000Z",
            "ended_at": "2026-11-14T10:00:40.000Z",
        },
    }
    assert ingestion._call_lifecycle_status(body) == CallExecution.CallStatus.CANCELLED
    other = {**body, "failure": {"code": "call_failed", "domain": "infrastructure"}}
    assert ingestion._call_lifecycle_status(other) == CallExecution.CallStatus.COMPLETED


def test_artifact_upload_and_receipt_after_cancel_keep_the_transcript() -> None:
    # Cancel marks the attempt canceled before the guest seals; the upload and receipt guards read only supersede and
    # expiry, so the sealed transcript and the receipt naming it are still accepted.
    attempt = SimpleNamespace(
        attempt_number=1,
        job=SimpleNamespace(current_attempt_number=1),
        expires_at=timezone.now() + timedelta(minutes=5),
        terminal_stage="canceled",
    )
    ingestion._assert_current_attempt(attempt)


def test_shared_records_and_bypass_notice_are_kept_and_served(
    organization, workspace
) -> None:
    from simulate.services.hosted_harness_ingestion import TRIAL_STATE_KEY
    from simulate.tests.test_harness_call_actors import _ingest
    from simulate.views.run_results_v3 import TrialStateSerializer

    shared = [
        {
            "key": "order_id=1042",
            "argument": "order_id",
            "value": "1042",
            "actions": [
                {
                    "who": "worker:order_lookup",
                    "tool": "list_refunds",
                    "outcome": "committed",
                    "write": None,
                },
                {
                    "who": "worker:refund",
                    "tool": "issue_refund",
                    "outcome": "committed",
                    "write": True,
                },
            ],
            "committed_writes": 1,
            "truncated": False,
        }
    ]
    notice = {
        "code": "worker_channel_bypassed",
        "message": "Refund worker reached the world without their own channel.",
        "workers": ["refund"],
    }
    metadata: dict = {}
    _ingest(
        organization,
        workspace,
        "multi-agent-shared-records",
        CallExecution.SimulationCallType.VOICE,
        {
            "messages": [],
            "world_actors": {
                "actions": [],
                "state_changes": [],
                "shared_records": shared,
            },
            "notices": [notice, {**notice, "workers": [1]}],
        },
        metadata,
    )
    trial = metadata[TRIAL_STATE_KEY]
    assert trial["shared_records"] == shared
    assert trial["notices"][0]["workers"] == ["refund"]
    # A malformed list is dropped, never kept half-read.
    assert "workers" not in trial["notices"][1]
    served = TrialStateSerializer(trial).data
    assert served["notices"][0]["workers"] == ["refund"]
    assert served["shared_records"][0]["committed_writes"] == 1


def _shared_record(key, truncated, **sealed):
    return {
        "key": f"order_id={key}",
        "argument": "order_id",
        "value": str(key),
        "actions": [{"who": "agent", "tool": "get_order", "outcome": "read"}],
        "committed_writes": 0,
        "truncated": truncated,
        **sealed,
    }


def _served_trial(trial):
    from simulate.selectors.call_detail import trial_state
    from simulate.services.hosted_harness_ingestion import TRIAL_STATE_KEY
    from simulate.views.run_results_v3 import TrialStateSerializer

    call = SimpleNamespace(id="c1", call_metadata={})
    return TrialStateSerializer(
        trial_state(call, metadata={TRIAL_STATE_KEY: trial})
    ).data


def test_the_sealed_truncation_flags_are_kept_and_served_as_sealed(
    organization, workspace
) -> None:
    from simulate.services.hosted_harness_ingestion import TRIAL_STATE_KEY
    from simulate.tests.test_harness_call_actors import _ingest

    shared = [
        _shared_record(1, True, actions_truncated=True),
        _shared_record(2, True, actions_truncated=False),
    ]
    metadata: dict = {}
    _ingest(
        organization,
        workspace,
        "multi-agent-shared-records-cut",
        CallExecution.SimulationCallType.VOICE,
        {
            "messages": [],
            "world_actors": {
                "actions": [],
                "state_changes": [],
                "shared_records": shared,
                "shared_records_truncated": True,
            },
        },
        metadata,
    )
    trial = metadata[TRIAL_STATE_KEY]
    assert trial["shared_records_truncated"] is True
    served = _served_trial(trial)
    assert served["shared_records_truncated"] is True
    assert [one["actions_truncated"] for one in served["shared_records"]] == [
        True,
        False,
    ]
    assert [one["truncation"] for one in served["shared_records"]] == [
        "actions",
        "records",
    ]


def test_a_trial_sealed_before_the_flags_serves_neither_as_known() -> None:
    # Only the record's own `truncated`: which bound was reached is not known, so neither flag is guessed.
    served = _served_trial({"shared_records": [_shared_record(1, True)]})
    assert served["shared_records_truncated"] is None
    assert served["shared_records"][0]["actions_truncated"] is None
    assert served["shared_records"][0]["truncated"] is True
    assert served["shared_records"][0]["truncation"] == "unknown"


def test_a_shared_record_nothing_cut_serves_no_truncation() -> None:
    served = _served_trial(
        {"shared_records": [_shared_record(1, False, actions_truncated=False)]}
    )
    assert served["shared_records"][0]["truncation"] is None
