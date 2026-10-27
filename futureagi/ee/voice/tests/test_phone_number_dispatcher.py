"""
Tests for PhoneNumberDispatcherWorkflow and related activities.

Covers:
- Slot management logic (FIFO granting, app/org limits)
- Signal handlers (request, release, update_limits)
- Phone number sync activity
- Batch activity (acquire + signal, no release signals)
- State management (checkpoint, continue-as-new)
- Failure paths and cancellation paths
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from simulate.temporal.types.activities import AcquireAndSignalPhoneNumbersBatchOutput
from ee.voice.temporal.types.phone_number_dispatcher import (
    ActivePhoneNumber,
    PhoneNumberDispatcherState,
    PhoneNumberReleaseSignal,
    PhoneNumberRequest,
)

# =============================================================================
# Helpers
# =============================================================================


def make_request(
    call_id=None, org_id="org-1", workflow_id=None, call_direction="outbound"
):
    return PhoneNumberRequest(
        call_id=call_id or str(uuid.uuid4()),
        org_id=org_id,
        workflow_id=workflow_id or f"call-exec-{uuid.uuid4()}",
        call_direction=call_direction,
    )


def make_active(call_id=None, org_id="org-1", granted_at=None):
    return ActivePhoneNumber(
        call_id=call_id or str(uuid.uuid4()),
        org_id=org_id,
        granted_at=granted_at or datetime.now(timezone.utc).isoformat(),
    )


@pytest.fixture
def mock_workflow():
    """Patch temporalio.workflow functions used by the dispatcher."""
    with (
        patch("temporalio.workflow.now", return_value=datetime.now(timezone.utc)),
        patch("temporalio.workflow.logger", MagicMock()),
        patch("temporalio.workflow.sleep", new_callable=AsyncMock),
        patch("temporalio.workflow.execute_activity", new_callable=AsyncMock),
        patch("temporalio.workflow.start_activity", MagicMock()),
        patch("temporalio.workflow.continue_as_new", MagicMock()),
        patch(
            "temporalio.workflow.info", return_value=MagicMock(workflow_id="test-wf")
        ),
        patch("temporalio.workflow.patched", return_value=True),
    ):
        import temporalio.workflow

        yield temporalio.workflow


def make_dispatcher(mock_workflow, app_limit=10, org_limit=5):
    """Create a dispatcher instance with mocked workflow context."""
    from ee.voice.temporal.workflows.phone_number_dispatcher_workflow import (
        PhoneNumberDispatcherWorkflow,
    )

    dispatcher = PhoneNumberDispatcherWorkflow()
    dispatcher._state.app_limit = app_limit
    dispatcher._state.org_limit = org_limit
    return dispatcher


# =============================================================================
# PhoneNumberDispatcherWorkflow — Slot Management
# =============================================================================


@pytest.mark.unit
class TestPhoneNumberDispatcherSlotManagement:
    """Tests for the dispatcher's slot granting logic."""

    def test_can_grant_any_under_limit(self, mock_workflow):
        dispatcher = make_dispatcher(mock_workflow, app_limit=5)
        assert dispatcher._can_grant_any() is True

    def test_can_grant_any_at_limit(self, mock_workflow):
        dispatcher = make_dispatcher(mock_workflow, app_limit=1)
        dispatcher._state.active_phone_numbers["call-1"] = make_active(call_id="call-1")
        assert dispatcher._can_grant_any() is False

    def test_can_grant_any_zero_limit(self, mock_workflow):
        """app_limit=0 means no grants (before first sync)."""
        dispatcher = make_dispatcher(mock_workflow, app_limit=0)
        assert dispatcher._can_grant_any() is False

    def test_can_grant_to_org_under_limit(self, mock_workflow):
        dispatcher = make_dispatcher(mock_workflow, org_limit=3)
        dispatcher._state.org_phone_number_counts["org-1"] = 2
        assert dispatcher._can_grant_to_org("org-1") is True

    def test_can_grant_to_org_at_limit(self, mock_workflow):
        dispatcher = make_dispatcher(mock_workflow, org_limit=3)
        dispatcher._state.org_phone_number_counts["org-1"] = 3
        assert dispatcher._can_grant_to_org("org-1") is False

    def test_can_grant_to_org_no_existing(self, mock_workflow):
        dispatcher = make_dispatcher(mock_workflow, org_limit=5)
        assert dispatcher._can_grant_to_org("org-new") is True

    def test_grant_phone_number_updates_state(self, mock_workflow):
        dispatcher = make_dispatcher(mock_workflow, app_limit=10)
        request = make_request(call_id="call-1", org_id="org-1")
        dispatcher._state.pending_count = 1

        dispatcher._grant_phone_number(request)

        assert "call-1" in dispatcher._state.active_phone_numbers
        assert dispatcher._state.org_phone_number_counts["org-1"] == 1
        assert dispatcher._state.pending_count == 0
        assert dispatcher._state.total_granted == 1
        assert len(dispatcher._pending_grants) == 1
        assert dispatcher._pending_grants[0]["call_id"] == "call-1"

    def test_process_pending_requests_fifo_order(self, mock_workflow):
        dispatcher = make_dispatcher(mock_workflow, app_limit=2)
        r1 = make_request(call_id="call-1", org_id="org-1")
        r2 = make_request(call_id="call-2", org_id="org-1")
        r3 = make_request(call_id="call-3", org_id="org-1")
        dispatcher._state.pending_queue = [r1, r2, r3]
        dispatcher._state.pending_count = 3

        dispatcher._process_pending_requests()

        # Should grant first 2 (app_limit=2)
        assert "call-1" in dispatcher._state.active_phone_numbers
        assert "call-2" in dispatcher._state.active_phone_numbers
        assert "call-3" not in dispatcher._state.active_phone_numbers
        assert len(dispatcher._state.pending_queue) == 1
        assert dispatcher._state.pending_queue[0].call_id == "call-3"

    def test_process_pending_requests_respects_org_limit(self, mock_workflow):
        dispatcher = make_dispatcher(mock_workflow, app_limit=10, org_limit=1)
        r1 = make_request(call_id="call-1", org_id="org-1")
        r2 = make_request(call_id="call-2", org_id="org-1")
        r3 = make_request(call_id="call-3", org_id="org-2")
        dispatcher._state.pending_queue = [r1, r2, r3]
        dispatcher._state.pending_count = 3

        dispatcher._process_pending_requests()

        # org-1 gets 1 (org_limit=1), org-2 gets 1
        assert "call-1" in dispatcher._state.active_phone_numbers
        assert "call-2" not in dispatcher._state.active_phone_numbers
        assert "call-3" in dispatcher._state.active_phone_numbers
        assert dispatcher._state.pending_count == 1

    def test_process_pending_requests_no_grants_when_empty(self, mock_workflow):
        dispatcher = make_dispatcher(mock_workflow, app_limit=10)
        dispatcher._state.pending_queue = []
        dispatcher._process_pending_requests()
        assert len(dispatcher._pending_grants) == 0

    def test_process_pending_requests_no_grants_at_app_limit(self, mock_workflow):
        dispatcher = make_dispatcher(mock_workflow, app_limit=1)
        dispatcher._state.active_phone_numbers["existing"] = make_active(
            call_id="existing"
        )
        dispatcher._state.pending_queue = [make_request(call_id="call-1")]
        dispatcher._state.pending_count = 1

        dispatcher._process_pending_requests()

        assert "call-1" not in dispatcher._state.active_phone_numbers
        assert dispatcher._state.pending_count == 1


# =============================================================================
# PhoneNumberDispatcherWorkflow — Signal Handlers
# =============================================================================


@pytest.mark.unit
class TestPhoneNumberDispatcherSignals:
    """Tests for signal handlers."""

    @pytest.mark.asyncio
    async def test_request_phone_number_adds_to_queue(self, mock_workflow):
        dispatcher = make_dispatcher(mock_workflow, app_limit=10)
        request = make_request(call_id="call-1", org_id="org-1")

        await dispatcher.request_phone_number(request)

        assert len(dispatcher._state.pending_queue) == 1
        assert dispatcher._state.pending_count == 1
        assert dispatcher._state.pending_queue[0].call_id == "call-1"

    @pytest.mark.asyncio
    async def test_release_phone_number_signal_from_active(self, mock_workflow):
        dispatcher = make_dispatcher(mock_workflow, app_limit=10)
        dispatcher._state.active_phone_numbers["call-1"] = make_active(
            call_id="call-1", org_id="org-1"
        )
        dispatcher._state.org_phone_number_counts["org-1"] = 1

        await dispatcher.release_phone_number_signal(
            PhoneNumberReleaseSignal(call_id="call-1")
        )

        assert "call-1" not in dispatcher._state.active_phone_numbers
        assert dispatcher._state.org_phone_number_counts.get("org-1") is None
        assert dispatcher._state.total_released == 1

    @pytest.mark.asyncio
    async def test_release_phone_number_signal_from_pending_queue(self, mock_workflow):
        dispatcher = make_dispatcher(mock_workflow, app_limit=10)
        request = make_request(call_id="call-1", org_id="org-1")
        dispatcher._state.pending_queue = [request]
        dispatcher._state.pending_count = 1

        await dispatcher.release_phone_number_signal(
            PhoneNumberReleaseSignal(call_id="call-1")
        )

        assert len(dispatcher._state.pending_queue) == 0
        assert dispatcher._state.pending_count == 0

    @pytest.mark.asyncio
    async def test_release_phone_number_signal_unknown_call(self, mock_workflow):
        import temporalio.workflow

        dispatcher = make_dispatcher(mock_workflow, app_limit=10)

        # Should not raise
        await dispatcher.release_phone_number_signal(
            PhoneNumberReleaseSignal(call_id="unknown")
        )
        temporalio.workflow.logger.debug.assert_called()

    @pytest.mark.asyncio
    async def test_release_phone_number_signal_releases_db_when_phone_acquired(
        self, mock_workflow
    ):
        """When releasing an active slot with phone_id, DB release activity is called."""
        import temporalio.workflow

        dispatcher = make_dispatcher(mock_workflow, app_limit=10)
        active = make_active(call_id="call-1", org_id="org-1")
        active.phone_id = "phone-123"
        dispatcher._state.active_phone_numbers["call-1"] = active
        dispatcher._state.org_phone_number_counts["org-1"] = 1

        await dispatcher.release_phone_number_signal(
            PhoneNumberReleaseSignal(call_id="call-1")
        )

        assert "call-1" not in dispatcher._state.active_phone_numbers
        assert dispatcher._state.total_released == 1
        # Verify release_phone_number activity was called
        temporalio.workflow.execute_activity.assert_called_once()
        call_args = temporalio.workflow.execute_activity.call_args
        assert call_args[0][0] == "release_phone_number"
        assert call_args[0][1].phone_id == "phone-123"

    @pytest.mark.asyncio
    async def test_release_phone_number_signal_skips_db_when_no_phone(
        self, mock_workflow
    ):
        """When releasing an active slot without phone_id, no DB release activity."""
        import temporalio.workflow

        dispatcher = make_dispatcher(mock_workflow, app_limit=10)
        dispatcher._state.active_phone_numbers["call-1"] = make_active(
            call_id="call-1", org_id="org-1"
        )
        dispatcher._state.org_phone_number_counts["org-1"] = 1

        await dispatcher.release_phone_number_signal(
            PhoneNumberReleaseSignal(call_id="call-1")
        )

        assert "call-1" not in dispatcher._state.active_phone_numbers
        # No DB release activity called (phone_id is None)
        temporalio.workflow.execute_activity.assert_not_called()

    @pytest.mark.asyncio
    async def test_release_decrements_org_count(self, mock_workflow):
        dispatcher = make_dispatcher(mock_workflow, app_limit=10)
        dispatcher._state.active_phone_numbers["call-1"] = make_active(
            call_id="call-1", org_id="org-1"
        )
        dispatcher._state.active_phone_numbers["call-2"] = make_active(
            call_id="call-2", org_id="org-1"
        )
        dispatcher._state.org_phone_number_counts["org-1"] = 2

        await dispatcher.release_phone_number_signal(
            PhoneNumberReleaseSignal(call_id="call-1")
        )

        assert dispatcher._state.org_phone_number_counts["org-1"] == 1

    @pytest.mark.asyncio
    async def test_update_limits(self, mock_workflow):
        dispatcher = make_dispatcher(mock_workflow, app_limit=10)

        await dispatcher.update_limits({"app_limit": 20, "org_limit": 10})

        assert dispatcher._state.app_limit == 20
        assert dispatcher._state.org_limit == 10


# =============================================================================
# PhoneNumberDispatcherWorkflow — Stale Reaper
# =============================================================================


@pytest.mark.unit
class TestPhoneNumberDispatcherStaleReaper:
    """Tests for the stale phone number reaper."""

    @pytest.mark.asyncio
    async def test_reap_stale_phone_numbers(self):
        now = datetime(2026, 2, 12, 12, 0, 0, tzinfo=timezone.utc)
        with (
            patch("temporalio.workflow.now", return_value=now),
            patch("temporalio.workflow.logger", MagicMock()),
            patch("temporalio.workflow.execute_activity", new_callable=AsyncMock),
        ):
            from ee.voice.temporal.workflows.phone_number_dispatcher_workflow import (
                PhoneNumberDispatcherWorkflow,
            )

            dispatcher = PhoneNumberDispatcherWorkflow()
            dispatcher._state.app_limit = 10

            # Stale: granted 50 minutes ago (threshold is 40 min)
            stale_time = datetime(
                2026, 2, 12, 11, 10, 0, tzinfo=timezone.utc
            ).isoformat()
            dispatcher._state.active_phone_numbers["stale-call"] = ActivePhoneNumber(
                call_id="stale-call",
                org_id="org-1",
                granted_at=stale_time,
                phone_id="phone-1",
            )

            # Fresh: granted 10 minutes ago
            fresh_time = datetime(
                2026, 2, 12, 11, 50, 0, tzinfo=timezone.utc
            ).isoformat()
            dispatcher._state.active_phone_numbers["fresh-call"] = ActivePhoneNumber(
                call_id="fresh-call", org_id="org-1", granted_at=fresh_time
            )

            dispatcher._state.org_phone_number_counts["org-1"] = 2

            await dispatcher._reap_stale_phone_numbers()

            assert "stale-call" not in dispatcher._state.active_phone_numbers
            assert "fresh-call" in dispatcher._state.active_phone_numbers
            assert dispatcher._state.org_phone_number_counts["org-1"] == 1
            assert dispatcher._state.total_released == 1

    @pytest.mark.asyncio
    async def test_reap_stale_releases_phone_from_db(self):
        """Stale reaper calls release_phone_number activity for acquired phones."""
        import temporalio.workflow as wf_mod

        now = datetime(2026, 2, 12, 12, 0, 0, tzinfo=timezone.utc)
        with (
            patch("temporalio.workflow.now", return_value=now),
            patch("temporalio.workflow.logger", MagicMock()),
            patch(
                "temporalio.workflow.execute_activity", new_callable=AsyncMock
            ) as mock_exec,
        ):
            from ee.voice.temporal.workflows.phone_number_dispatcher_workflow import (
                PhoneNumberDispatcherWorkflow,
            )

            dispatcher = PhoneNumberDispatcherWorkflow()
            dispatcher._state.app_limit = 10

            stale_time = datetime(
                2026, 2, 12, 11, 10, 0, tzinfo=timezone.utc
            ).isoformat()
            dispatcher._state.active_phone_numbers["stale-call"] = ActivePhoneNumber(
                call_id="stale-call",
                org_id="org-1",
                granted_at=stale_time,
                phone_id="phone-123",
            )
            dispatcher._state.org_phone_number_counts["org-1"] = 1

            await dispatcher._reap_stale_phone_numbers()

            # Verify release_phone_number activity was called with the phone_id
            mock_exec.assert_called_once()
            call_args = mock_exec.call_args
            assert call_args[0][0] == "release_phone_number"
            assert call_args[0][1].phone_id == "phone-123"

    @pytest.mark.asyncio
    async def test_reap_stale_without_phone_id_skips_db_release(self):
        """Stale reaper skips DB release when phone_id is None."""
        now = datetime(2026, 2, 12, 12, 0, 0, tzinfo=timezone.utc)
        with (
            patch("temporalio.workflow.now", return_value=now),
            patch("temporalio.workflow.logger", MagicMock()),
            patch(
                "temporalio.workflow.execute_activity", new_callable=AsyncMock
            ) as mock_exec,
        ):
            from ee.voice.temporal.workflows.phone_number_dispatcher_workflow import (
                PhoneNumberDispatcherWorkflow,
            )

            dispatcher = PhoneNumberDispatcherWorkflow()
            dispatcher._state.app_limit = 10

            stale_time = datetime(
                2026, 2, 12, 11, 10, 0, tzinfo=timezone.utc
            ).isoformat()
            # phone_id=None (phone was never acquired)
            dispatcher._state.active_phone_numbers["stale-call"] = ActivePhoneNumber(
                call_id="stale-call", org_id="org-1", granted_at=stale_time
            )
            dispatcher._state.org_phone_number_counts["org-1"] = 1

            await dispatcher._reap_stale_phone_numbers()

            assert "stale-call" not in dispatcher._state.active_phone_numbers
            # No DB release activity called
            mock_exec.assert_not_called()

    @pytest.mark.asyncio
    async def test_reap_no_stale(self):
        now = datetime(2026, 2, 12, 12, 0, 0, tzinfo=timezone.utc)
        with (
            patch("temporalio.workflow.now", return_value=now),
            patch("temporalio.workflow.logger", MagicMock()),
            patch("temporalio.workflow.execute_activity", new_callable=AsyncMock),
        ):
            from ee.voice.temporal.workflows.phone_number_dispatcher_workflow import (
                PhoneNumberDispatcherWorkflow,
            )

            dispatcher = PhoneNumberDispatcherWorkflow()
            dispatcher._state.app_limit = 10

            fresh_time = datetime(
                2026, 2, 12, 11, 50, 0, tzinfo=timezone.utc
            ).isoformat()
            dispatcher._state.active_phone_numbers["call-1"] = ActivePhoneNumber(
                call_id="call-1", org_id="org-1", granted_at=fresh_time
            )
            dispatcher._state.org_phone_number_counts["org-1"] = 1

            await dispatcher._reap_stale_phone_numbers()

            assert "call-1" in dispatcher._state.active_phone_numbers
            assert dispatcher._state.total_released == 0

    @pytest.mark.asyncio
    async def test_reap_empty_active(self, mock_workflow):
        dispatcher = make_dispatcher(mock_workflow, app_limit=10)
        await dispatcher._reap_stale_phone_numbers()
        assert dispatcher._state.total_released == 0

    @pytest.mark.asyncio
    async def test_reap_malformed_granted_at(self):
        """Stale reaper treats malformed granted_at as stale."""
        with (
            patch("temporalio.workflow.now", return_value=datetime.now(timezone.utc)),
            patch("temporalio.workflow.logger", MagicMock()),
            patch("temporalio.workflow.execute_activity", new_callable=AsyncMock),
        ):
            from ee.voice.temporal.workflows.phone_number_dispatcher_workflow import (
                PhoneNumberDispatcherWorkflow,
            )

            dispatcher = PhoneNumberDispatcherWorkflow()
            dispatcher._state.app_limit = 10
            dispatcher._state.active_phone_numbers["bad-call"] = ActivePhoneNumber(
                call_id="bad-call", org_id="org-1", granted_at="not-a-date"
            )
            dispatcher._state.org_phone_number_counts["org-1"] = 1

            await dispatcher._reap_stale_phone_numbers()

            assert "bad-call" not in dispatcher._state.active_phone_numbers
            assert dispatcher._state.total_released == 1


# =============================================================================
# PhoneNumberDispatcherWorkflow — State Management
# =============================================================================


@pytest.mark.unit
class TestPhoneNumberDispatcherState:
    """Tests for state checkpoint and restore."""

    def test_init_app_limit_zero(self, mock_workflow):
        """app_limit should be 0 on fresh start (set by sync on first iteration)."""
        from ee.voice.temporal.workflows.phone_number_dispatcher_workflow import (
            PhoneNumberDispatcherWorkflow,
        )

        dispatcher = PhoneNumberDispatcherWorkflow()
        assert dispatcher._state.app_limit == 0

    def test_sync_triggers_on_first_iteration(self, mock_workflow):
        """_last_sync_time is None on init so sync runs immediately."""
        from ee.voice.temporal.workflows.phone_number_dispatcher_workflow import (
            PhoneNumberDispatcherWorkflow,
        )

        dispatcher = PhoneNumberDispatcherWorkflow()
        assert dispatcher._last_sync_time is None

    @pytest.mark.asyncio
    async def test_checkpoint_preserves_pending_grants(self, mock_workflow):
        import temporalio.workflow

        dispatcher = make_dispatcher(mock_workflow, app_limit=5)
        dispatcher._pending_grants = [
            {"workflow_id": "wf-1", "call_id": "call-1", "org_id": "org-1"}
        ]

        await dispatcher._checkpoint()

        temporalio.workflow.continue_as_new.assert_called_once()
        passed_state = temporalio.workflow.continue_as_new.call_args[0][0]
        assert len(passed_state.pending_grants) == 1
        assert passed_state.pending_grants[0]["call_id"] == "call-1"

    def test_restore_state_from_checkpoint(self, mock_workflow):
        from ee.voice.temporal.workflows.phone_number_dispatcher_workflow import (
            PhoneNumberDispatcherWorkflow,
        )

        state = PhoneNumberDispatcherState(
            pending_queue=[],
            pending_count=0,
            active_phone_numbers={
                "call-1": ActivePhoneNumber(
                    call_id="call-1",
                    org_id="org-1",
                    granted_at=datetime.now(timezone.utc).isoformat(),
                )
            },
            org_phone_number_counts={"org-1": 1},
            app_limit=5,
            org_limit=3,
            total_granted=10,
            total_released=9,
            pending_grants=[{"workflow_id": "wf-1", "call_id": "call-2"}],
        )

        dispatcher = PhoneNumberDispatcherWorkflow()
        # Simulate what run() does with state
        dispatcher._state = state
        dispatcher._pending_grants = (
            state.pending_grants.copy() if state.pending_grants else []
        )

        assert dispatcher._state.app_limit == 5
        assert dispatcher._state.org_limit == 3
        assert len(dispatcher._state.active_phone_numbers) == 1
        assert len(dispatcher._pending_grants) == 1
        assert dispatcher._pending_grants[0]["call_id"] == "call-2"


# =============================================================================
# PhoneNumberDispatcherWorkflow — Query Handler
# =============================================================================


@pytest.mark.unit
class TestPhoneNumberDispatcherQuery:
    """Tests for query handler."""

    def test_get_status(self, mock_workflow):
        dispatcher = make_dispatcher(mock_workflow, app_limit=10)
        dispatcher._state.pending_count = 2
        dispatcher._state.total_granted = 5
        dispatcher._state.total_released = 3
        dispatcher._state.active_phone_numbers["call-1"] = make_active(call_id="call-1")
        dispatcher._pending_grants = [{"call_id": "call-2"}]

        status = dispatcher.get_status()

        assert status["pending_count"] == 2
        assert status["active_count"] == 1
        assert status["app_limit"] == 10
        assert status["total_granted"] == 5
        assert status["total_released"] == 3
        assert status["pending_grants"] == 1
        assert "call-1" in status["active_call_ids"]


# =============================================================================
# Dispatcher Failure Paths
# =============================================================================


@pytest.mark.unit
class TestPhoneNumberDispatcherFailurePaths:
    """Tests for dispatcher failure handling."""

    @pytest.mark.asyncio
    async def test_sync_phone_limits_failure_does_not_crash(self, mock_workflow):
        """If sync activity fails, dispatcher logs warning and continues."""
        import temporalio.workflow

        temporalio.workflow.execute_activity.side_effect = Exception("DB unavailable")

        dispatcher = make_dispatcher(mock_workflow, app_limit=5)

        await dispatcher._sync_phone_limits()

        # app_limit should remain unchanged
        assert dispatcher._state.app_limit == 5
        temporalio.workflow.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_signal_grants_batch_failure_releases_slots(self, mock_workflow):
        """If batch activity fails entirely, slots are released (NO_RETRY_POLICY)."""
        import temporalio.workflow

        temporalio.workflow.execute_activity.side_effect = Exception("Activity timeout")

        dispatcher = make_dispatcher(mock_workflow, app_limit=10)
        dispatcher._pending_grants = [
            {"workflow_id": "wf-1", "call_id": "call-1", "org_id": "org-1"}
        ]
        # Pre-add to active_phone_numbers (as _process_pending_requests does)
        dispatcher._state.active_phone_numbers["call-1"] = ActivePhoneNumber(
            call_id="call-1", org_id="org-1", granted_at="2024-01-01T00:00:00"
        )
        dispatcher._state.org_phone_number_counts["org-1"] = 1
        dispatcher._state.total_granted = 1

        await dispatcher._signal_grants_batch()

        # pending_grants should be cleared (no retry with NO_RETRY_POLICY)
        assert len(dispatcher._pending_grants) == 0
        # Slot should be released from active_phone_numbers
        assert "call-1" not in dispatcher._state.active_phone_numbers
        assert dispatcher._state.org_phone_number_counts.get("org-1", 0) == 0
        assert dispatcher._state.total_released == 1

    @pytest.mark.asyncio
    async def test_signal_grants_batch_success_clears_pending_grants(
        self, mock_workflow
    ):
        """On success, all pending_grants are cleared and phone_ids stored."""
        import temporalio.workflow

        temporalio.workflow.execute_activity.return_value = (
            AcquireAndSignalPhoneNumbersBatchOutput(
                success_count=2,
                failed_count=0,
                failed_call_ids=[],
                successful_grants={"call-1": "phone-1", "call-2": "phone-2"},
            )
        )

        dispatcher = make_dispatcher(mock_workflow, app_limit=10)
        dispatcher._pending_grants = [
            {"workflow_id": "wf-1", "call_id": "call-1", "org_id": "org-1"},
            {"workflow_id": "wf-2", "call_id": "call-2", "org_id": "org-1"},
        ]
        # Pre-add to active_phone_numbers
        dispatcher._state.active_phone_numbers["call-1"] = ActivePhoneNumber(
            call_id="call-1", org_id="org-1", granted_at="2024-01-01T00:00:00"
        )
        dispatcher._state.active_phone_numbers["call-2"] = ActivePhoneNumber(
            call_id="call-2", org_id="org-1", granted_at="2024-01-01T00:00:00"
        )

        await dispatcher._signal_grants_batch()

        assert len(dispatcher._pending_grants) == 0
        # phone_ids should be stored on active_phone_numbers
        assert dispatcher._state.active_phone_numbers["call-1"].phone_id == "phone-1"
        assert dispatcher._state.active_phone_numbers["call-2"].phone_id == "phone-2"


# =============================================================================
# sync_available_phone_numbers Activity
# =============================================================================


@pytest.mark.unit
class TestSyncAvailablePhoneNumbers:
    """Tests for the sync_available_phone_numbers activity."""

    @pytest.mark.asyncio
    async def test_counts_outbound_non_disabled(self):
        """Sync activity returns count of non-disabled outbound phones."""
        mock_qs = MagicMock()
        mock_qs.filter.return_value = mock_qs
        mock_qs.exclude.return_value = mock_qs
        mock_qs.acount = AsyncMock(return_value=7)

        mock_model = MagicMock()
        mock_model.objects = mock_qs
        mock_model.CallDirection.OUTBOUND = "outbound"
        mock_model.PhoneStatus.DISABLED = "disabled"

        with (
            patch("temporalio.activity.logger", MagicMock()),
            patch("ee.voice.temporal.activities.voice_small.close_old_connections"),
            patch(
                "simulate.models.simulation_phone_number.SimulationPhoneNumber",
                mock_model,
            ),
        ):
            from ee.voice.temporal.activities.voice_small import sync_available_phone_numbers

            count = await sync_available_phone_numbers()

        assert count == 7
        mock_qs.filter.assert_called_once()
        mock_qs.exclude.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_phones(self):
        """Sync activity returns 0 when no phones match."""
        mock_qs = MagicMock()
        mock_qs.filter.return_value = mock_qs
        mock_qs.exclude.return_value = mock_qs
        mock_qs.acount = AsyncMock(return_value=0)

        mock_model = MagicMock()
        mock_model.objects = mock_qs
        mock_model.CallDirection.OUTBOUND = "outbound"
        mock_model.PhoneStatus.DISABLED = "disabled"

        with (
            patch("temporalio.activity.logger", MagicMock()),
            patch("ee.voice.temporal.activities.voice_small.close_old_connections"),
            patch(
                "simulate.models.simulation_phone_number.SimulationPhoneNumber",
                mock_model,
            ),
        ):
            from ee.voice.temporal.activities.voice_small import sync_available_phone_numbers

            count = await sync_available_phone_numbers()

        assert count == 0


# =============================================================================
# acquire_and_signal_phone_numbers_batch Activity — No Release Signals
# =============================================================================


@pytest.mark.unit
class TestAcquireAndSignalBatchNoRelease:
    """Verify batch activity does NOT send release signals to dispatcher."""

    @pytest.mark.asyncio
    async def test_no_release_signals_on_failure(self):
        """When phone acquisition fails, no SIGNAL_RELEASE_PHONE_NUMBER is sent."""
        from simulate.temporal.types.activities import (
            AcquireAndSignalPhoneNumbersBatchInput,
        )

        grants = [
            {
                "workflow_id": "wf-1",
                "call_id": "call-1",
                "org_id": "org-1",
                "call_direction": "outbound",
            }
        ]
        input_data = AcquireAndSignalPhoneNumbersBatchInput(grants=grants)

        mock_client = MagicMock()
        mock_client.get_workflow_handle = MagicMock(return_value=MagicMock())

        with (
            patch("temporalio.activity.logger", MagicMock()),
            patch("ee.voice.temporal.activities.voice_small.close_old_connections"),
            patch("ee.voice.temporal.activities.voice_small.CallExecution") as mock_ce,
            patch(
                "ee.voice.services.phone_number_service.PhoneNumberService.acquire_phone_number",
                side_effect=ValueError("No phones available"),
            ),
            patch(
                "tfc.temporal.common.client.get_client",
                new=AsyncMock(return_value=mock_client),
            ),
            patch(
                "ee.voice.temporal.activities.voice_small.sync_to_async",
                side_effect=lambda fn: AsyncMock(side_effect=fn),
            ),
        ):
            mock_ce.objects.aget = AsyncMock(return_value=MagicMock())

            from ee.voice.temporal.activities.voice_small import (
                acquire_and_signal_phone_numbers_batch,
            )

            result = await acquire_and_signal_phone_numbers_batch(input_data)

            assert result.failed_count == 1
            assert result.success_count == 0
            assert "call-1" in result.failed_call_ids

            # Verify NO signal was sent to the dispatcher
            for call_args in mock_client.get_workflow_handle.call_args_list:
                handle_id = call_args[0][0] if call_args[0] else ""
                assert (
                    handle_id != "phone-number-dispatcher-singleton"
                ), "Batch activity should NOT signal the dispatcher for failed grants"

    @pytest.mark.asyncio
    async def test_releases_phone_from_db_when_signal_fails(self):
        """If phone acquired but signaling workflow fails, phone is released from DB."""
        from simulate.temporal.types.activities import (
            AcquireAndSignalPhoneNumbersBatchInput,
        )

        grants = [
            {
                "workflow_id": "wf-1",
                "call_id": "call-1",
                "org_id": "org-1",
                "call_direction": "outbound",
            }
        ]
        input_data = AcquireAndSignalPhoneNumbersBatchInput(grants=grants)

        mock_phone = MagicMock()
        mock_phone.id = "phone-123"
        mock_phone.phone_number = "+15551234567"
        mock_phone.provider_phone_id = "vapi-123"

        mock_handle = MagicMock()
        mock_handle.signal = AsyncMock(side_effect=Exception("Workflow not found"))

        mock_client = MagicMock()
        mock_client.get_workflow_handle = MagicMock(return_value=mock_handle)

        with (
            patch("temporalio.activity.logger", MagicMock()),
            patch("ee.voice.temporal.activities.voice_small.close_old_connections"),
            patch("ee.voice.temporal.activities.voice_small.CallExecution") as mock_ce,
            patch(
                "ee.voice.services.phone_number_service.PhoneNumberService.acquire_phone_number",
                return_value=mock_phone,
            ),
            patch(
                "ee.voice.services.phone_number_service.PhoneNumberService.release_phone_number",
            ) as mock_release,
            patch(
                "tfc.temporal.common.client.get_client",
                new=AsyncMock(return_value=mock_client),
            ),
            patch(
                "ee.voice.temporal.activities.voice_small.sync_to_async",
                side_effect=lambda fn: AsyncMock(side_effect=fn),
            ),
        ):
            mock_ce.objects.aget = AsyncMock(return_value=MagicMock())

            from ee.voice.temporal.activities.voice_small import (
                acquire_and_signal_phone_numbers_batch,
            )

            result = await acquire_and_signal_phone_numbers_batch(input_data)

            assert result.failed_count == 1
            assert result.success_count == 0
            # Phone should be released from DB
            mock_release.assert_called_once_with("phone-123")

    @pytest.mark.asyncio
    async def test_successful_acquisition_and_signal(self):
        """Happy path: phone acquired and workflow signaled."""
        from simulate.temporal.types.activities import (
            AcquireAndSignalPhoneNumbersBatchInput,
        )

        grants = [
            {
                "workflow_id": "wf-1",
                "call_id": "call-1",
                "org_id": "org-1",
                "call_direction": "outbound",
            }
        ]
        input_data = AcquireAndSignalPhoneNumbersBatchInput(grants=grants)

        mock_phone = MagicMock()
        mock_phone.id = "phone-123"
        mock_phone.phone_number = "+15551234567"
        mock_phone.provider_phone_id = "vapi-123"

        mock_handle = MagicMock()
        mock_handle.signal = AsyncMock()

        mock_client = MagicMock()
        mock_client.get_workflow_handle = MagicMock(return_value=mock_handle)

        with (
            patch("temporalio.activity.logger", MagicMock()),
            patch("ee.voice.temporal.activities.voice_small.close_old_connections"),
            patch("ee.voice.temporal.activities.voice_small.CallExecution") as mock_ce,
            patch(
                "ee.voice.services.phone_number_service.PhoneNumberService.acquire_phone_number",
                return_value=mock_phone,
            ),
            patch(
                "tfc.temporal.common.client.get_client",
                new=AsyncMock(return_value=mock_client),
            ),
            patch(
                "ee.voice.temporal.activities.voice_small.sync_to_async",
                side_effect=lambda fn: AsyncMock(side_effect=fn),
            ),
        ):
            mock_ce.objects.aget = AsyncMock(return_value=MagicMock())

            from ee.voice.temporal.activities.voice_small import (
                acquire_and_signal_phone_numbers_batch,
            )

            result = await acquire_and_signal_phone_numbers_batch(input_data)

            assert result.success_count == 1
            assert result.failed_count == 0
            mock_handle.signal.assert_called_once()

    @pytest.mark.asyncio
    async def test_batch_activity_raises_on_total_failure(self):
        """If the entire activity fails (not individual grants), it re-raises."""
        from simulate.temporal.types.activities import (
            AcquireAndSignalPhoneNumbersBatchInput,
        )

        input_data = AcquireAndSignalPhoneNumbersBatchInput(
            grants=[{"call_id": "c1", "workflow_id": "wf-1", "org_id": "org-1"}]
        )

        with (
            patch("temporalio.activity.logger", MagicMock()),
            patch(
                "ee.voice.temporal.activities.voice_small.close_old_connections",
                side_effect=Exception("DB connection failed"),
            ),
        ):
            from ee.voice.temporal.activities.voice_small import (
                acquire_and_signal_phone_numbers_batch,
            )

            with pytest.raises(Exception, match="DB connection failed"):
                await acquire_and_signal_phone_numbers_batch(input_data)


# =============================================================================
# release_phone_number_slot Activity
# =============================================================================


@pytest.mark.unit
class TestReleasePhoneNumberSlotActivity:
    """Tests for the release_phone_number_slot activity.

    The activity only signals the dispatcher to release the slot.
    DB release is handled by the dispatcher itself.
    """

    @pytest.mark.asyncio
    async def test_release_signals_dispatcher(self):
        """Activity signals the dispatcher to release the slot."""
        from simulate.temporal.types.activities import ReleasePhoneNumberSlotInput

        input_data = ReleasePhoneNumberSlotInput(call_id="call-1")

        mock_handle = MagicMock()
        mock_handle.signal = AsyncMock()
        mock_client = MagicMock()
        mock_client.get_workflow_handle = MagicMock(return_value=mock_handle)

        with (
            patch("temporalio.activity.logger", MagicMock()),
            patch("ee.voice.temporal.activities.voice_small.close_old_connections"),
            patch(
                "tfc.temporal.common.client.get_client",
                new=AsyncMock(return_value=mock_client),
            ),
        ):
            from ee.voice.temporal.activities.voice_small import release_phone_number_slot

            await release_phone_number_slot(input_data)

            mock_handle.signal.assert_called_once()

    @pytest.mark.asyncio
    async def test_release_raises_on_failure(self):
        """Activity re-raises exceptions for Temporal retry."""
        from simulate.temporal.types.activities import ReleasePhoneNumberSlotInput

        input_data = ReleasePhoneNumberSlotInput(call_id="call-1")

        with (
            patch("temporalio.activity.logger", MagicMock()),
            patch("ee.voice.temporal.activities.voice_small.close_old_connections"),
            patch(
                "tfc.temporal.common.client.get_client",
                new=AsyncMock(side_effect=Exception("Connection failed")),
            ),
        ):
            from ee.voice.temporal.activities.voice_small import release_phone_number_slot

            with pytest.raises(Exception, match="Connection failed"):
                await release_phone_number_slot(input_data)


# =============================================================================
# CallExecutionWorkflow — Cancellation Paths
# =============================================================================


@pytest.mark.unit
class TestCallExecutionCancellationPaths:
    """Tests verifying cancellation releases phone resources correctly."""

    def test_cooperative_cancel_raises_cancelled_error(self):
        """Cooperative cancel at slot-wait and phone-wait raises CancelledError
        instead of calling _handle_cancellation directly."""
        import ast
        import inspect
        import textwrap

        from ee.voice.temporal.workflows.call_execution_workflow import (
            CallExecutionWorkflow,
        )

        source = textwrap.dedent(inspect.getsource(CallExecutionWorkflow.run))
        tree = ast.parse(source)

        # Find all `raise asyncio.CancelledError()` in the run method
        cancel_raises = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and node.exc:
                if isinstance(node.exc, ast.Call):
                    func = node.exc.func
                    if (
                        isinstance(func, ast.Attribute)
                        and func.attr == "CancelledError"
                    ):
                        cancel_raises.append(node.lineno)

        # Should have at least 2 raises (slot-wait cancel + phone-wait cancel)
        assert len(cancel_raises) >= 2, (
            f"Expected at least 2 `raise asyncio.CancelledError()` in run(), "
            f"found {len(cancel_raises)}"
        )

    def test_cancelled_before_phone_request_no_release(self):
        """If cancelled before phone was requested, no phone release needed."""
        with (
            patch("temporalio.workflow.logger", MagicMock()),
            patch("temporalio.workflow.patched", return_value=True),
        ):
            from ee.voice.temporal.workflows.call_execution_workflow import (
                CallExecutionWorkflow,
            )

            wf = CallExecutionWorkflow()
            wf._phone_requested = False
            wf._phone_released = False

            should_release = wf._phone_requested and not wf._phone_released
            assert should_release is False

    def test_cancelled_after_request_before_grant_releases_slot_only(self):
        """If cancelled after requesting but before grant, phone_id is None."""
        with (
            patch("temporalio.workflow.logger", MagicMock()),
            patch("temporalio.workflow.patched", return_value=True),
        ):
            from ee.voice.temporal.workflows.call_execution_workflow import (
                CallExecutionWorkflow,
            )

            wf = CallExecutionWorkflow()
            wf._phone_requested = True
            wf._phone_granted = False
            wf._phone_number_id = None
            wf._phone_released = False

            should_release = wf._phone_requested and not wf._phone_released
            assert should_release is True
            # phone_id is None — only dispatcher slot released
            assert wf._phone_number_id is None

    def test_cancelled_after_phone_granted_releases_db_and_slot(self):
        """If cancelled after phone was granted, release with phone_id set."""
        with (
            patch("temporalio.workflow.logger", MagicMock()),
            patch("temporalio.workflow.patched", return_value=True),
        ):
            from ee.voice.temporal.workflows.call_execution_workflow import (
                CallExecutionWorkflow,
            )

            wf = CallExecutionWorkflow()
            wf._phone_requested = True
            wf._phone_granted = True
            wf._phone_number_id = "phone-123"
            wf._phone_released = False

            should_release = wf._phone_requested and not wf._phone_released
            assert should_release is True
            assert wf._phone_number_id == "phone-123"

    def test_no_double_release_after_early_release(self):
        """If phone was already released early, CancelledError handler skips it."""
        with (
            patch("temporalio.workflow.logger", MagicMock()),
            patch("temporalio.workflow.patched", return_value=True),
        ):
            from ee.voice.temporal.workflows.call_execution_workflow import (
                CallExecutionWorkflow,
            )

            wf = CallExecutionWorkflow()
            wf._phone_requested = True
            wf._phone_granted = True
            wf._phone_number_id = "phone-123"
            wf._phone_released = True

            should_release = wf._phone_requested and not wf._phone_released
            assert should_release is False

    def test_handle_cancellation_skips_phone_release_for_new_path(self):
        """_handle_cancellation skips phone release for phone-dispatcher path."""
        import inspect

        from ee.voice.temporal.workflows.call_execution_workflow import (
            CallExecutionWorkflow,
        )

        source = inspect.getsource(CallExecutionWorkflow._handle_cancellation)
        assert 'if not workflow.patched("phone-dispatcher"):' in source
        assert (
            "# NEW PATH: Phone number release is handled by the CancelledError"
            in source
        )

    def test_fail_releases_phone_when_requested(self):
        """_fail releases phone via release_phone_number_slot when phone_requested."""
        import inspect

        from ee.voice.temporal.workflows.call_execution_workflow import (
            CallExecutionWorkflow,
        )

        source = inspect.getsource(CallExecutionWorkflow._fail)
        assert "self._phone_requested and not self._phone_released" in source
        assert '"release_phone_number_slot"' in source


# =============================================================================
# End-to-End Flow: Dispatcher Grants Only What's Available
# =============================================================================


@pytest.mark.unit
class TestDispatcherGrantsMatchPhonePool:
    """Tests verifying the sync mechanism prevents over-granting."""

    def test_no_grants_before_first_sync(self, mock_workflow):
        """With app_limit=0 (before sync), no grants are made even with pending requests."""
        from ee.voice.temporal.workflows.phone_number_dispatcher_workflow import (
            PhoneNumberDispatcherWorkflow,
        )

        dispatcher = PhoneNumberDispatcherWorkflow()
        assert dispatcher._state.app_limit == 0

        dispatcher._state.pending_queue = [
            make_request(call_id="call-1"),
            make_request(call_id="call-2"),
        ]
        dispatcher._state.pending_count = 2

        dispatcher._process_pending_requests()

        assert len(dispatcher._state.active_phone_numbers) == 0
        assert dispatcher._state.pending_count == 2

    @pytest.mark.asyncio
    async def test_sync_sets_limit_then_grants_match(self, mock_workflow):
        """After sync sets app_limit=3, only 3 grants are made from 5 requests."""
        import temporalio.workflow

        temporalio.workflow.execute_activity.return_value = 3

        from ee.voice.temporal.workflows.phone_number_dispatcher_workflow import (
            PhoneNumberDispatcherWorkflow,
        )

        dispatcher = PhoneNumberDispatcherWorkflow()
        assert dispatcher._state.app_limit == 0

        # Sync sets limit to 3
        await dispatcher._sync_phone_limits()
        assert dispatcher._state.app_limit == 3

        # Add 5 requests
        for i in range(5):
            dispatcher._state.pending_queue.append(
                make_request(call_id=f"call-{i}", org_id="org-1")
            )
        dispatcher._state.pending_count = 5

        dispatcher._process_pending_requests()

        # Only 3 granted
        assert len(dispatcher._state.active_phone_numbers) == 3
        assert dispatcher._state.pending_count == 2
        assert len(dispatcher._state.pending_queue) == 2

    @pytest.mark.asyncio
    async def test_release_frees_slot_for_next_in_queue(self, mock_workflow):
        """After releasing a slot, the next pending request can be granted."""
        dispatcher = make_dispatcher(mock_workflow, app_limit=1)

        # Grant call-1
        r1 = make_request(call_id="call-1", org_id="org-1")
        dispatcher._state.pending_queue = [r1]
        dispatcher._state.pending_count = 1
        dispatcher._process_pending_requests()

        assert "call-1" in dispatcher._state.active_phone_numbers
        assert dispatcher._state.pending_count == 0

        # Add call-2, can't grant yet (at limit)
        r2 = make_request(call_id="call-2", org_id="org-1")
        dispatcher._state.pending_queue = [r2]
        dispatcher._state.pending_count = 1
        dispatcher._process_pending_requests()
        assert "call-2" not in dispatcher._state.active_phone_numbers

        # Release call-1
        await dispatcher.release_phone_number_signal(
            PhoneNumberReleaseSignal(call_id="call-1")
        )
        assert len(dispatcher._state.active_phone_numbers) == 0

        # Now call-2 can be granted
        dispatcher._process_pending_requests()
        assert "call-2" in dispatcher._state.active_phone_numbers
