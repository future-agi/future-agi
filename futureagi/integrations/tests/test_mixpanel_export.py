"""Export state must not advance after ambiguous or partial acknowledgements."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from integrations.services.mixpanel_service import MixpanelService
from integrations.tests.test_mixpanel_service import CREDENTIALS, acknowledged, response


@pytest.mark.parametrize(
    "ack,expected_status",
    [
        (response(0), "error"),
        (response(1), "error"),
        (acknowledged(1), "error"),
        (response({"num_records_imported": 1}, status=400), "error"),
        (response({"error": "Unauthorized"}, status=401), "error"),
        (response({"error": "Too many requests"}, status=429), "active"),
    ],
)
def test_failed_export_keeps_checkpoint_and_backfill_state(ack, expected_status):
    from integrations.temporal.activities import _sync_export_integration

    checkpoint = datetime(2026, 1, 1, tzinfo=UTC)
    connection = SimpleNamespace(
        id="synthetic-connection",
        platform="mixpanel",
        last_synced_at=checkpoint,
        backfill_from=None,
        backfill_completed=False,
        encrypted_credentials=b"synthetic-encrypted",
        save=Mock(),
    )
    sync_log = SimpleNamespace(save=Mock())
    rows = [_request_log(i, checkpoint + timedelta(minutes=1)) for i in range(2)]
    with (
        patch("integrations.services.registry.ensure_services_loaded"),
        patch(
            "integrations.services.credentials.CredentialManager.decrypt",
            return_value=CREDENTIALS,
        ),
        patch(
            "integrations.services.base.get_integration_service",
            return_value=MixpanelService(),
        ),
        patch("integrations.models.SyncLog.objects.create", return_value=sync_log),
        patch(
            "integrations.temporal.activities._get_export_queryset", return_value=rows
        ),
        patch("integrations.services.mixpanel_service.requests.post", return_value=ack),
    ):
        _sync_export_integration(connection)

    assert connection.last_synced_at == checkpoint
    assert connection.backfill_completed is False
    assert connection.status == expected_status
    assert sync_log.status == (
        "rate_limited" if expected_status == "active" else "failed"
    )
    assert not hasattr(sync_log, "traces_created")
    for call in connection.save.call_args_list:
        assert "last_synced_at" not in call.kwargs["update_fields"]
        assert "backfill_completed" not in call.kwargs["update_fields"]


def test_complete_export_advances_checkpoint_with_exact_count():
    from integrations.temporal.activities import _sync_export_integration

    checkpoint = datetime(2026, 1, 1, tzinfo=UTC)
    connection = SimpleNamespace(
        id="synthetic-connection",
        platform="mixpanel",
        last_synced_at=checkpoint,
        backfill_from=None,
        backfill_completed=False,
        encrypted_credentials=b"synthetic-encrypted",
        save=Mock(),
    )
    sync_log = SimpleNamespace(save=Mock())
    rows = [_request_log(i, checkpoint + timedelta(minutes=1)) for i in range(2)]
    with (
        patch("integrations.services.registry.ensure_services_loaded"),
        patch(
            "integrations.services.credentials.CredentialManager.decrypt",
            return_value=CREDENTIALS,
        ),
        patch(
            "integrations.services.base.get_integration_service",
            return_value=MixpanelService(),
        ),
        patch("integrations.models.SyncLog.objects.create", return_value=sync_log),
        patch(
            "integrations.temporal.activities._get_export_queryset", return_value=rows
        ),
        patch(
            "integrations.services.mixpanel_service.requests.post",
            return_value=acknowledged(2),
        ),
    ):
        _sync_export_integration(connection)

    assert connection.last_synced_at > checkpoint
    assert connection.backfill_completed is True
    assert connection.status == "active"
    assert sync_log.status == "success"
    assert sync_log.traces_created == 2


def _request_log(index, timestamp):
    return SimpleNamespace(
        started_at=timestamp,
        created_at=timestamp,
        user_id="synthetic-user",
        request_id=f"synthetic-request-{index}",
        model="test-model",
        provider="test",
        resolved_model="test-model",
        latency_ms=10,
        input_tokens=1,
        output_tokens=1,
        total_tokens=2,
        cost=0,
        status_code=200,
        is_stream=False,
        is_error=False,
        cache_hit=False,
        fallback_used=False,
        guardrail_triggered=False,
    )
