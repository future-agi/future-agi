"""Tests for integration Temporal activities."""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
import requests

from integrations.models import (
    ConnectionStatus,
    IntegrationConnection,
    SyncLog,
    SyncStatus,
)
from integrations.temporal.activities import (
    _retry_on_429,
    check_integration_error_alerts,
    dispatch_connection_sync,
    poll_active_integrations,
    start_backfill_workflow,
    sync_integration_connection,
)
from integrations.transformers.langfuse_transformer import LangfuseTransformer

# ---------------------------------------------------------------------------
# poll_active_integrations
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestPollActiveIntegrations:
    @patch("integrations.temporal.activities.dispatch_connection_sync")
    def test_dispatches_due_connections(self, mock_dispatch, integration_connection):
        integration_connection.last_synced_at = datetime.now(timezone.utc) - timedelta(
            minutes=10
        )
        integration_connection.sync_interval_seconds = 300
        integration_connection.save(
            update_fields=["last_synced_at", "sync_interval_seconds"]
        )

        poll_active_integrations()

        mock_dispatch.assert_called_once_with(str(integration_connection.id))

    @patch("integrations.temporal.activities.dispatch_connection_sync")
    def test_skips_not_yet_due(self, mock_dispatch, integration_connection):
        integration_connection.last_synced_at = datetime.now(timezone.utc) - timedelta(
            seconds=120
        )
        integration_connection.sync_interval_seconds = 300
        integration_connection.save(
            update_fields=["last_synced_at", "sync_interval_seconds"]
        )

        poll_active_integrations()

        mock_dispatch.assert_not_called()

    @patch("integrations.temporal.activities.dispatch_connection_sync")
    def test_skips_paused(self, mock_dispatch, paused_connection):
        poll_active_integrations()
        mock_dispatch.assert_not_called()

    @patch("integrations.temporal.activities.dispatch_connection_sync")
    def test_dispatches_never_synced(self, mock_dispatch, integration_connection):
        integration_connection.last_synced_at = None
        integration_connection.save(update_fields=["last_synced_at"])

        poll_active_integrations()

        mock_dispatch.assert_called_once()

    @patch("integrations.temporal.activities.dispatch_connection_sync")
    def test_skips_deleted(self, mock_dispatch, integration_connection):
        integration_connection.deleted = True
        integration_connection.save(update_fields=["deleted"])

        poll_active_integrations()

        mock_dispatch.assert_not_called()

    @patch("integrations.temporal.activities.dispatch_connection_sync")
    def test_dispatch_exception_does_not_crash(
        self, mock_dispatch, integration_connection
    ):
        """If dispatch raises, poll continues without crashing."""
        integration_connection.last_synced_at = None
        integration_connection.save(update_fields=["last_synced_at"])
        mock_dispatch.side_effect = RuntimeError("Temporal unavailable")

        # Should not raise
        poll_active_integrations()

    @patch("integrations.temporal.activities.dispatch_connection_sync")
    def test_skips_error_status(self, mock_dispatch, error_connection):
        """Error connections are not polled for sync."""
        poll_active_integrations()
        mock_dispatch.assert_not_called()

    @patch("integrations.temporal.activities.logger")
    @patch("integrations.temporal.activities.dispatch_connection_sync")
    def test_sync_already_running_is_skipped_quietly(
        self, mock_dispatch, mock_logger, integration_connection
    ):
        """A sync still running for the connection is expected, not a failure."""
        from temporalio.exceptions import WorkflowAlreadyStartedError

        integration_connection.last_synced_at = None
        integration_connection.save(update_fields=["last_synced_at"])
        mock_dispatch.side_effect = WorkflowAlreadyStartedError(
            f"task-integration-sync-{integration_connection.id}", "TaskRunnerWorkflow"
        )

        poll_active_integrations()

        mock_dispatch.assert_called_once_with(str(integration_connection.id))
        mock_logger.warning.assert_not_called()


# ---------------------------------------------------------------------------
# dispatch_connection_sync / start_backfill_workflow
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDispatchConnectionSync:
    """Every sync start for a connection shares one Temporal workflow id.

    Manual sync, the scheduled poller and the initial backfill all go through
    ``dispatch_connection_sync``; with a fixed id and the FAIL conflict policy
    Temporal itself refuses a second start while one is running.
    """

    @patch("tfc.temporal.drop_in.runner.start_activity", return_value="wf")
    def test_uses_per_connection_workflow_id(self, mock_start_activity):
        from temporalio.common import WorkflowIDConflictPolicy

        dispatch_connection_sync("conn-1")

        mock_start_activity.assert_called_once()
        args, kwargs = mock_start_activity.call_args
        assert args == ("sync_integration_connection",)
        assert kwargs["args"] == ("conn-1",)
        assert kwargs["queue"] == "tasks_l"
        assert kwargs["task_id"] == "integration-sync-conn-1"
        assert kwargs["id_conflict_policy"] == WorkflowIDConflictPolicy.FAIL

    @patch("tfc.temporal.drop_in.runner.start_activity", return_value="wf")
    def test_backfill_shares_the_sync_workflow_id(self, mock_start_activity):
        start_backfill_workflow("conn-1")

        mock_start_activity.assert_called_once()
        assert mock_start_activity.call_args.kwargs["task_id"] == (
            "integration-sync-conn-1"
        )


# ---------------------------------------------------------------------------
# sync_integration_connection
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestSyncIntegrationConnection:
    def _mock_service(self, traces=None, full_trace=None):
        svc = MagicMock()
        svc.fetch_traces.return_value = {
            "traces": traces or [],
            "has_more": False,
            "next_page": 2,
            "total_items": len(traces or []),
        }
        svc.fetch_trace_detail.return_value = full_trace or {}
        return svc

    @patch("time.sleep")
    @patch("integrations.transformers.base.get_transformer")
    @patch("integrations.services.base.get_integration_service")
    @patch("integrations.services.credentials.CredentialManager")
    def test_sync_success(
        self,
        mock_cred_mgr,
        mock_get_svc,
        mock_get_tf,
        mock_sleep,
        integration_connection,
        raw_langfuse_trace,
    ):
        mock_cred_mgr.decrypt.return_value = {"public_key": "pk", "secret_key": "sk"}
        mock_get_svc.return_value = self._mock_service(
            traces=[{"id": "lf-trace-001"}],
            full_trace=raw_langfuse_trace,
        )
        mock_get_tf.return_value = LangfuseTransformer()

        sync_integration_connection(str(integration_connection.id))

        integration_connection.refresh_from_db()
        assert integration_connection.status == ConnectionStatus.ACTIVE
        assert integration_connection.last_synced_at is not None

        # SyncLog created
        log = SyncLog.objects.filter(connection=integration_connection).first()
        assert log is not None
        assert log.status == SyncStatus.SUCCESS
        assert log.traces_fetched == 1
        assert log.spans_synced == 3  # GENERATION + SPAN + EVENT

    @patch("time.sleep")
    @patch("integrations.transformers.base.get_transformer")
    @patch("integrations.services.base.get_integration_service")
    @patch("integrations.services.credentials.CredentialManager")
    def test_sync_creates_sync_log(
        self,
        mock_cred_mgr,
        mock_get_svc,
        mock_get_tf,
        mock_sleep,
        integration_connection,
    ):
        mock_cred_mgr.decrypt.return_value = {"public_key": "pk", "secret_key": "sk"}
        mock_get_svc.return_value = self._mock_service(traces=[])
        mock_get_tf.return_value = LangfuseTransformer()

        sync_integration_connection(str(integration_connection.id))

        log = SyncLog.objects.filter(connection=integration_connection).first()
        assert log is not None
        assert log.status == SyncStatus.NO_NEW_DATA

    def test_not_found_returns_early(self):
        sync_integration_connection(str(uuid.uuid4()))
        # Should not raise

    def test_paused_returns_early(self, paused_connection):
        sync_integration_connection(str(paused_connection.id))
        paused_connection.refresh_from_db()
        assert paused_connection.status == ConnectionStatus.PAUSED

    def test_no_project_pauses(self, integration_connection):
        integration_connection.project = None
        integration_connection.save(update_fields=["project"])

        sync_integration_connection(str(integration_connection.id))

        integration_connection.refresh_from_db()
        assert integration_connection.status == ConnectionStatus.PAUSED

    @patch("time.sleep")
    @patch("integrations.transformers.base.get_transformer")
    @patch("integrations.services.base.get_integration_service")
    @patch("integrations.services.credentials.CredentialManager")
    def test_401_error_sets_error_status(
        self,
        mock_cred_mgr,
        mock_get_svc,
        mock_get_tf,
        mock_sleep,
        integration_connection,
    ):
        mock_cred_mgr.decrypt.return_value = {"public_key": "pk", "secret_key": "sk"}
        mock_response = MagicMock()
        mock_response.status_code = 401
        svc = MagicMock()
        svc.fetch_traces.side_effect = requests.exceptions.HTTPError(
            response=mock_response
        )
        mock_get_svc.return_value = svc
        mock_get_tf.return_value = LangfuseTransformer()

        sync_integration_connection(str(integration_connection.id))

        integration_connection.refresh_from_db()
        assert integration_connection.status == ConnectionStatus.ERROR
        assert "Authentication" in (integration_connection.status_message or "")

    @patch("time.sleep")
    @patch("integrations.transformers.base.get_transformer")
    @patch("integrations.services.base.get_integration_service")
    @patch("integrations.services.credentials.CredentialManager")
    def test_429_error_keeps_active(
        self,
        mock_cred_mgr,
        mock_get_svc,
        mock_get_tf,
        mock_sleep,
        integration_connection,
    ):
        mock_cred_mgr.decrypt.return_value = {"public_key": "pk", "secret_key": "sk"}
        mock_response = MagicMock()
        mock_response.status_code = 429
        svc = MagicMock()
        svc.fetch_traces.side_effect = requests.exceptions.HTTPError(
            response=mock_response
        )
        mock_get_svc.return_value = svc
        mock_get_tf.return_value = LangfuseTransformer()

        sync_integration_connection(str(integration_connection.id))

        integration_connection.refresh_from_db()
        assert integration_connection.status == ConnectionStatus.ACTIVE

        log = SyncLog.objects.filter(connection=integration_connection).first()
        assert log.status == SyncStatus.RATE_LIMITED

    @patch("time.sleep")
    @patch("integrations.transformers.base.get_transformer")
    @patch("integrations.services.base.get_integration_service")
    @patch("integrations.services.credentials.CredentialManager")
    def test_404_error_sets_error(
        self,
        mock_cred_mgr,
        mock_get_svc,
        mock_get_tf,
        mock_sleep,
        integration_connection,
    ):
        mock_cred_mgr.decrypt.return_value = {"public_key": "pk", "secret_key": "sk"}
        mock_response = MagicMock()
        mock_response.status_code = 404
        svc = MagicMock()
        svc.fetch_traces.side_effect = requests.exceptions.HTTPError(
            response=mock_response
        )
        mock_get_svc.return_value = svc
        mock_get_tf.return_value = LangfuseTransformer()

        sync_integration_connection(str(integration_connection.id))

        integration_connection.refresh_from_db()
        assert integration_connection.status == ConnectionStatus.ERROR
        assert "not found" in (integration_connection.status_message or "").lower()

    @patch("time.sleep")
    @patch("integrations.transformers.base.get_transformer")
    @patch("integrations.services.base.get_integration_service")
    @patch("integrations.services.credentials.CredentialManager")
    def test_generic_error_sets_error(
        self,
        mock_cred_mgr,
        mock_get_svc,
        mock_get_tf,
        mock_sleep,
        integration_connection,
    ):
        mock_cred_mgr.decrypt.return_value = {"public_key": "pk", "secret_key": "sk"}
        svc = MagicMock()
        svc.fetch_traces.side_effect = RuntimeError("unexpected")
        mock_get_svc.return_value = svc
        mock_get_tf.return_value = LangfuseTransformer()

        sync_integration_connection(str(integration_connection.id))

        integration_connection.refresh_from_db()
        assert integration_connection.status == ConnectionStatus.ERROR

    @patch("time.sleep")
    @patch("integrations.transformers.base.get_transformer")
    @patch("integrations.services.base.get_integration_service")
    @patch("integrations.services.credentials.CredentialManager")
    def test_marks_backfill_complete(
        self,
        mock_cred_mgr,
        mock_get_svc,
        mock_get_tf,
        mock_sleep,
        integration_connection,
    ):
        integration_connection.backfill_completed = False
        integration_connection.save(update_fields=["backfill_completed"])

        mock_cred_mgr.decrypt.return_value = {"public_key": "pk", "secret_key": "sk"}
        mock_get_svc.return_value = self._mock_service(traces=[])
        mock_get_tf.return_value = LangfuseTransformer()

        sync_integration_connection(str(integration_connection.id))

        integration_connection.refresh_from_db()
        assert integration_connection.backfill_completed is True

    @patch("time.sleep")
    @patch("integrations.transformers.base.get_transformer")
    @patch("integrations.services.base.get_integration_service")
    @patch("integrations.services.credentials.CredentialManager")
    def test_backfilling_connection_syncs(
        self,
        mock_cred_mgr,
        mock_get_svc,
        mock_get_tf,
        mock_sleep,
        backfilling_connection,
    ):
        """BACKFILLING status is a syncable state."""
        mock_cred_mgr.decrypt.return_value = {"public_key": "pk", "secret_key": "sk"}
        mock_get_svc.return_value = self._mock_service(traces=[])
        mock_get_tf.return_value = LangfuseTransformer()

        sync_integration_connection(str(backfilling_connection.id))

        backfilling_connection.refresh_from_db()
        assert backfilling_connection.status == ConnectionStatus.ACTIVE
        assert backfilling_connection.backfill_completed is True

    @patch("time.sleep")
    @patch("integrations.transformers.base.get_transformer")
    @patch("integrations.services.base.get_integration_service")
    @patch("integrations.services.credentials.CredentialManager")
    def test_claimed_retry_runs_the_pending_backfill(
        self,
        mock_cred_mgr,
        mock_get_svc,
        mock_get_tf,
        mock_sleep,
        integration_connection,
    ):
        """Sync Now on a connection whose backfill never started.

        sync_now claims the row as SYNCING before dispatching; the worker must
        run it from the beginning of history and mark the backfill done.
        """
        integration_connection.status = ConnectionStatus.SYNCING
        integration_connection.backfill_completed = False
        integration_connection.last_synced_at = None
        integration_connection.save(
            update_fields=["status", "backfill_completed", "last_synced_at"]
        )
        mock_cred_mgr.decrypt.return_value = {"public_key": "pk", "secret_key": "sk"}
        service = self._mock_service(traces=[])
        mock_get_svc.return_value = service
        mock_get_tf.return_value = LangfuseTransformer()

        sync_integration_connection(str(integration_connection.id))

        assert service.fetch_traces.call_args.kwargs["from_timestamp"].startswith(
            "2020-01-01"
        )
        integration_connection.refresh_from_db()
        assert integration_connection.status == ConnectionStatus.ACTIVE
        assert integration_connection.backfill_completed is True

    @patch("time.sleep")
    @patch("integrations.transformers.base.get_transformer")
    @patch("integrations.services.base.get_integration_service")
    @patch("integrations.services.credentials.CredentialManager")
    def test_decrypt_failure_sets_error(
        self,
        mock_cred_mgr,
        mock_get_svc,
        mock_get_tf,
        mock_sleep,
        integration_connection,
    ):
        """Credential decryption failure sets connection to ERROR."""
        mock_cred_mgr.decrypt.side_effect = ValueError("Failed to decrypt")
        mock_get_tf.return_value = LangfuseTransformer()

        sync_integration_connection(str(integration_connection.id))

        integration_connection.refresh_from_db()
        assert integration_connection.status == ConnectionStatus.ERROR
        assert "decrypt" in (integration_connection.status_message or "").lower()

    @patch("time.sleep")
    @patch("integrations.transformers.base.get_transformer")
    @patch("integrations.services.base.get_integration_service")
    @patch("integrations.services.credentials.CredentialManager")
    def test_sync_success_updates_counters(
        self,
        mock_cred_mgr,
        mock_get_svc,
        mock_get_tf,
        mock_sleep,
        integration_connection,
        raw_langfuse_trace,
    ):
        """Successful sync increments total_traces_synced and total_spans_synced."""
        mock_cred_mgr.decrypt.return_value = {"public_key": "pk", "secret_key": "sk"}
        mock_get_svc.return_value = self._mock_service(
            traces=[{"id": "lf-trace-001"}],
            full_trace=raw_langfuse_trace,
        )
        mock_get_tf.return_value = LangfuseTransformer()

        sync_integration_connection(str(integration_connection.id))

        integration_connection.refresh_from_db()
        assert integration_connection.total_traces_synced >= 1
        assert integration_connection.total_spans_synced >= 3

    def test_error_connection_returns_early(self, error_connection):
        """ERROR status is not syncable — returns early without modifying."""
        sync_integration_connection(str(error_connection.id))
        error_connection.refresh_from_db()
        assert error_connection.status == ConnectionStatus.ERROR


# ---------------------------------------------------------------------------
# _upsert_trace tests removed: function was replaced by
# tracer.utils.langfuse_upsert.upsert_langfuse_trace with a different
# signature. Those trace upsert flows are now exercised through
# sync_integration_connection tests above and tracer-level tests.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _retry_on_429
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRetryOn429:
    @patch("time.sleep")
    def test_succeeds_after_retry(self, mock_sleep):
        mock_response = MagicMock()
        mock_response.status_code = 429

        call_count = 0

        def fn():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise requests.exceptions.HTTPError(response=mock_response)
            return "ok"

        result = _retry_on_429(fn, max_retries=3)
        assert result == "ok"

    @patch("time.sleep")
    def test_exhausts_retries(self, mock_sleep):
        mock_response = MagicMock()
        mock_response.status_code = 429

        def fn():
            raise requests.exceptions.HTTPError(response=mock_response)

        with pytest.raises(requests.exceptions.HTTPError):
            _retry_on_429(fn, max_retries=2)

    @patch("time.sleep")
    def test_non_429_raises_immediately(self, mock_sleep):
        mock_response = MagicMock()
        mock_response.status_code = 500

        def fn():
            raise requests.exceptions.HTTPError(response=mock_response)

        with pytest.raises(requests.exceptions.HTTPError):
            _retry_on_429(fn, max_retries=3)
        mock_sleep.assert_not_called()

    @patch("time.sleep")
    def test_max_retries_zero_raises_on_first_429(self, mock_sleep):
        """max_retries=0 means no retries — first 429 raises immediately."""
        mock_response = MagicMock()
        mock_response.status_code = 429

        def fn():
            raise requests.exceptions.HTTPError(response=mock_response)

        with pytest.raises(requests.exceptions.HTTPError):
            _retry_on_429(fn, max_retries=0)
        mock_sleep.assert_not_called()

    def test_succeeds_immediately_no_retry_needed(self):
        """If fn succeeds on first call, no retries occur."""

        def fn():
            return "instant"

        result = _retry_on_429(fn, max_retries=3)
        assert result == "instant"

    @patch("time.sleep")
    def test_non_http_error_raises_immediately(self, mock_sleep):
        """Non-HTTP exceptions are not caught by retry logic."""

        def fn():
            raise ValueError("not an HTTP error")

        with pytest.raises(ValueError, match="not an HTTP error"):
            _retry_on_429(fn, max_retries=3)
        mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# check_integration_error_alerts
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestCheckIntegrationErrorAlerts:
    @patch("integrations.temporal.activities._send_error_notification")
    def test_sends_email_for_old_error(self, mock_send, error_connection):
        # Use queryset.update() to bypass auto_now on updated_at
        IntegrationConnection.no_workspace_objects.filter(
            id=error_connection.id
        ).update(
            updated_at=datetime.now(timezone.utc) - timedelta(hours=2),
            last_error_notified_at=None,
        )

        check_integration_error_alerts()

        mock_send.assert_called_once()

    @patch("integrations.temporal.activities._send_error_notification")
    def test_skips_recently_notified(self, mock_send, error_connection):
        IntegrationConnection.no_workspace_objects.filter(
            id=error_connection.id
        ).update(
            updated_at=datetime.now(timezone.utc) - timedelta(hours=2),
            last_error_notified_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )

        check_integration_error_alerts()

        mock_send.assert_not_called()

    @patch("integrations.temporal.activities._send_error_notification")
    def test_skips_active_connections(self, mock_send, integration_connection):
        """Active connections are not included in error alerts."""
        check_integration_error_alerts()
        mock_send.assert_not_called()

    @patch("integrations.temporal.activities._send_error_notification")
    def test_skips_recently_errored(self, mock_send, error_connection):
        """Connections in error for less than 1 hour are not notified."""
        # updated_at is auto_now so it's "now" — less than 1 hour ago
        check_integration_error_alerts()
        mock_send.assert_not_called()

    @patch("integrations.temporal.activities._send_error_notification")
    def test_notification_exception_does_not_crash(self, mock_send, error_connection):
        """If _send_error_notification raises, alert loop continues."""
        IntegrationConnection.no_workspace_objects.filter(
            id=error_connection.id
        ).update(
            updated_at=datetime.now(timezone.utc) - timedelta(hours=2),
            last_error_notified_at=None,
        )
        mock_send.side_effect = RuntimeError("SMTP down")

        # Should not raise
        check_integration_error_alerts()
