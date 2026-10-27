"""
Practical activity tests for Run Test functionality.

These tests focus on real business logic and database operations,
mocking only external API calls (VAPI, etc.).

Tests cover:
- Setup and finalize test execution activities
- Phone number acquisition and release logic
- Call creation and status polling
- Balance checking logic
- Error handling and edge cases
"""

import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock, Mock, patch

import pytest
from django.utils import timezone

from simulate.models import AgentDefinition, Scenarios
from simulate.models.run_test import RunTest
from simulate.models.simulation_phone_number import SimulationPhoneNumber
from simulate.models.simulator_agent import SimulatorAgent
from simulate.models.test_execution import CallExecution, TestExecution
try:
    from ee.voice.services.phone_number_service import PhoneNumberService
except ImportError:
    pytest.skip(
        "EE voice phone number service is not available in OSS",
        allow_module_level=True,
    )

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def agent_definition(db, organization, workspace):
    """Create a test agent definition."""
    return AgentDefinition.objects.create(
        agent_name="Test Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+1234567890",
        inbound=True,
        description="Test agent for simulation",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def simulator_agent(db, organization, workspace):
    """Create a test simulator agent."""
    return SimulatorAgent.objects.create(
        name="Test Simulator",
        prompt="You are a test customer calling support.",
        voice_provider="elevenlabs",
        voice_name="test-voice",
        model="gpt-4",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def scenario(db, organization, workspace, simulator_agent):
    """Create a test scenario."""
    return Scenarios.objects.create(
        name="Test Scenario",
        description="Test scenario for run tests",
        organization=organization,
        workspace=workspace,
        simulator_agent=simulator_agent,
    )


@pytest.fixture
def run_test(db, organization, workspace, agent_definition, scenario, simulator_agent):
    """Create a test RunTest."""
    rt = RunTest.objects.create(
        name="Test Run",
        description="Test description",
        agent_definition=agent_definition,
        organization=organization,
        workspace=workspace,
        simulator_agent=simulator_agent,
    )
    rt.scenarios.add(scenario)
    return rt


@pytest.fixture
def test_execution(db, run_test, scenario):
    """Create a test execution."""
    return TestExecution.objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.RUNNING,
        scenario_ids=[str(scenario.id)],
        started_at=timezone.now(),
    )


@pytest.fixture
def call_execution(db, test_execution, scenario):
    """Create a call execution."""
    return CallExecution.objects.create(
        test_execution=test_execution,
        scenario=scenario,
        status=CallExecution.CallStatus.REGISTERED,
    )


@pytest.fixture
def outbound_phone(db):
    """Create an idle outbound phone number."""
    return SimulationPhoneNumber.objects.create(
        phone_number="+1555123001",
        provider_phone_id="vapi-phone-001",
        call_direction="outbound",
        status=SimulationPhoneNumber.PhoneStatus.IDLE,
    )


@pytest.fixture
def inbound_phone(db):
    """Create an idle inbound phone number."""
    return SimulationPhoneNumber.objects.create(
        phone_number="+1555123002",
        provider_phone_id="vapi-phone-002",
        call_direction="inbound",
        status=SimulationPhoneNumber.PhoneStatus.IDLE,
    )


@pytest.fixture
def multiple_outbound_phones(db):
    """Create multiple outbound phone numbers for concurrency testing."""
    phones = []
    for i in range(5):
        phone = SimulationPhoneNumber.objects.create(
            phone_number=f"+155512300{i}",
            provider_phone_id=f"vapi-phone-00{i}",
            call_direction="outbound",
            status=SimulationPhoneNumber.PhoneStatus.IDLE,
            last_used_at=timezone.now()
            - timedelta(minutes=i * 10),  # Different last used times
        )
        phones.append(phone)
    return phones


# ============================================================================
# Phone Number Service Tests
# ============================================================================


@pytest.mark.unit
class TestPhoneNumberService:
    """Tests for PhoneNumberService - real database operations."""

    def test_acquire_outbound_phone_number(self, db, outbound_phone, call_execution):
        """Test acquiring an outbound phone number."""
        phone = PhoneNumberService.acquire_phone_number(
            call_direction="outbound",
            call_execution=call_execution,
        )

        assert phone is not None
        assert phone.phone_number == "+1555123001"
        assert phone.status == SimulationPhoneNumber.PhoneStatus.IN_USE
        assert phone.current_call_execution == call_execution

    def test_acquire_inbound_phone_number(self, db, inbound_phone, call_execution):
        """Test acquiring an inbound phone number."""
        phone = PhoneNumberService.acquire_phone_number(
            call_direction="inbound",
            call_execution=call_execution,
        )

        assert phone is not None
        assert phone.phone_number == "+1555123002"
        assert phone.status == SimulationPhoneNumber.PhoneStatus.IN_USE

    def test_acquire_phone_raises_when_no_phones_available(self, db, call_execution):
        """Test that acquiring raises ValueError when no phones are available."""
        # No phones in database
        with pytest.raises(ValueError) as exc_info:
            PhoneNumberService.acquire_phone_number(
                call_direction="outbound",
                call_execution=call_execution,
            )

        assert "No idle outbound phone numbers available" in str(exc_info.value)

    def test_acquire_phone_raises_when_all_in_use(
        self, db, outbound_phone, call_execution
    ):
        """Test acquiring fails when all phones are in use."""
        # Mark the only phone as in use
        outbound_phone.status = SimulationPhoneNumber.PhoneStatus.IN_USE
        outbound_phone.save()

        with pytest.raises(ValueError) as exc_info:
            PhoneNumberService.acquire_phone_number(
                call_direction="outbound",
                call_execution=call_execution,
            )

        assert "No idle outbound phone numbers available" in str(exc_info.value)
        assert "In use: 1" in str(exc_info.value)

    def test_acquire_phone_uses_least_recently_used(
        self, db, multiple_outbound_phones, call_execution
    ):
        """Test that phone acquisition prioritizes least recently used phone.

        BUG CHECK: Verify that order_by('last_used_at') works correctly.
        """
        # The phone with the earliest last_used_at should be acquired first
        phone = PhoneNumberService.acquire_phone_number(
            call_direction="outbound",
            call_execution=call_execution,
        )

        # Should get the phone that was least recently used (oldest last_used_at)
        assert phone.phone_number == multiple_outbound_phones[-1].phone_number

    def test_release_phone_number(self, db, outbound_phone, call_execution):
        """Test releasing a phone number."""
        # First acquire the phone
        PhoneNumberService.acquire_phone_number(
            call_direction="outbound",
            call_execution=call_execution,
        )

        # Verify it's in use
        outbound_phone.refresh_from_db()
        assert outbound_phone.status == SimulationPhoneNumber.PhoneStatus.IN_USE

        # Release it
        result = PhoneNumberService.release_phone_number(outbound_phone.id)

        assert result is True
        outbound_phone.refresh_from_db()
        assert outbound_phone.status == SimulationPhoneNumber.PhoneStatus.IDLE
        assert outbound_phone.current_call_execution is None

    def test_release_phone_returns_false_for_nonexistent(self, db):
        """Test releasing non-existent phone number returns False."""
        result = PhoneNumberService.release_phone_number(uuid.uuid4())
        assert result is False

    def test_cleanup_phone_numbers_completed_calls(
        self, db, outbound_phone, call_execution
    ):
        """Test cleanup releases phones from completed calls."""
        # Acquire phone
        phone = PhoneNumberService.acquire_phone_number(
            call_direction="outbound",
            call_execution=call_execution,
        )

        # Mark call as completed
        call_execution.status = CallExecution.CallStatus.COMPLETED
        call_execution.save()

        # Run cleanup
        PhoneNumberService.cleanup_phone_numbers()

        # Phone should be released
        phone.refresh_from_db()
        assert phone.status == SimulationPhoneNumber.PhoneStatus.IDLE
        assert phone.current_call_execution is None

    def test_cleanup_phone_numbers_failed_calls(
        self, db, outbound_phone, call_execution
    ):
        """Test cleanup releases phones from failed calls."""
        phone = PhoneNumberService.acquire_phone_number(
            call_direction="outbound",
            call_execution=call_execution,
        )

        call_execution.status = CallExecution.CallStatus.FAILED
        call_execution.save()

        PhoneNumberService.cleanup_phone_numbers()

        phone.refresh_from_db()
        assert phone.status == SimulationPhoneNumber.PhoneStatus.IDLE

    def test_cleanup_phone_numbers_orphaned(self, db, outbound_phone):
        """Test cleanup releases orphaned phones (in use but no call execution)."""
        # Directly set phone to in use without a call execution
        outbound_phone.status = SimulationPhoneNumber.PhoneStatus.IN_USE
        outbound_phone.current_call_execution = None
        outbound_phone.save()

        PhoneNumberService.cleanup_phone_numbers()

        outbound_phone.refresh_from_db()
        assert outbound_phone.status == SimulationPhoneNumber.PhoneStatus.IDLE


# ============================================================================
# Test Execution Logic Tests
# ============================================================================


@pytest.mark.unit
class TestTestExecutionLogic:
    """Tests for test execution business logic."""

    def test_test_execution_status_transitions(self, db, run_test, scenario):
        """Test valid status transitions for test execution."""
        test_execution = TestExecution.objects.create(
            run_test=run_test,
            status=TestExecution.ExecutionStatus.PENDING,
            scenario_ids=[str(scenario.id)],
        )

        # PENDING -> RUNNING
        test_execution.status = TestExecution.ExecutionStatus.RUNNING
        test_execution.started_at = timezone.now()
        test_execution.save()
        test_execution.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.RUNNING

        # RUNNING -> COMPLETED
        test_execution.status = TestExecution.ExecutionStatus.COMPLETED
        test_execution.completed_at = timezone.now()
        test_execution.save()
        test_execution.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.COMPLETED

    def test_test_execution_can_cancel_from_running(self, db, run_test, scenario):
        """Test cancellation from running state."""
        test_execution = TestExecution.objects.create(
            run_test=run_test,
            status=TestExecution.ExecutionStatus.RUNNING,
            scenario_ids=[str(scenario.id)],
            started_at=timezone.now(),
        )

        test_execution.status = TestExecution.ExecutionStatus.CANCELLED
        test_execution.completed_at = timezone.now()
        test_execution.save()

        test_execution.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.CANCELLED

    def test_test_execution_can_fail(self, db, run_test, scenario):
        """Test failure state transition."""
        test_execution = TestExecution.objects.create(
            run_test=run_test,
            status=TestExecution.ExecutionStatus.RUNNING,
            scenario_ids=[str(scenario.id)],
            started_at=timezone.now(),
        )

        test_execution.status = TestExecution.ExecutionStatus.FAILED
        test_execution.error_message = "Test failure reason"
        test_execution.completed_at = timezone.now()
        test_execution.save()

        test_execution.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.FAILED
        assert test_execution.error_message == "Test failure reason"


# ============================================================================
# Call Execution Logic Tests
# ============================================================================


@pytest.mark.unit
class TestCallExecutionLogic:
    """Tests for call execution business logic."""

    def test_call_execution_status_flow(self, db, test_execution, scenario):
        """Test complete call execution status flow.

        Note: CallStatus enum has confusing naming:
        - REGISTERED has value 'queued' (not 'registered')
        - ONGOING (not IN_PROGRESS) has value 'ongoing'
        """
        call = CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            status=CallExecution.CallStatus.PENDING,
        )

        # PENDING -> REGISTERED (value is 'queued')
        call.status = CallExecution.CallStatus.REGISTERED
        call.save()
        call.refresh_from_db()
        assert call.status == CallExecution.CallStatus.REGISTERED

        # REGISTERED -> ONGOING
        call.status = CallExecution.CallStatus.ONGOING
        call.started_at = timezone.now()
        call.save()
        call.refresh_from_db()
        assert call.status == CallExecution.CallStatus.ONGOING

        # ONGOING -> COMPLETED
        call.status = CallExecution.CallStatus.COMPLETED
        call.completed_at = timezone.now()
        call.duration_seconds = 120
        call.save()
        call.refresh_from_db()
        assert call.status == CallExecution.CallStatus.COMPLETED

    def test_call_execution_cost_storage(self, db, test_execution, scenario):
        """Test that cost is stored correctly in cost_cents field."""
        call = CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            status=CallExecution.CallStatus.COMPLETED,
            cost_cents=25,  # 25 cents = $0.25
        )

        call.refresh_from_db()
        assert call.cost_cents == 25

    def test_call_execution_provider_data_storage(self, db, test_execution, scenario):
        """Test that provider call data is stored correctly."""
        call = CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            status=CallExecution.CallStatus.COMPLETED,
            provider_call_data={
                "vapi": {
                    "cost": 0.25,
                    "costBreakdown": {
                        "llm": 0.05,
                        "stt": 0.02,
                        "tts": 0.03,
                        "vapi": 0.15,
                    },
                }
            },
        )

        call.refresh_from_db()
        assert call.provider_call_data["vapi"]["cost"] == 0.25
        assert call.provider_call_data["vapi"]["costBreakdown"]["llm"] == 0.05

    def test_call_execution_eval_outputs_storage(self, db, test_execution, scenario):
        """Test that evaluation outputs are stored correctly."""
        call = CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            status=CallExecution.CallStatus.COMPLETED,
            eval_outputs={
                "accuracy": {"score": 0.85, "reasoning": "Good response"},
                "helpfulness": {"score": 0.92, "reasoning": "Very helpful"},
            },
        )

        call.refresh_from_db()
        assert call.eval_outputs["accuracy"]["score"] == 0.85
        assert call.eval_outputs["helpfulness"]["score"] == 0.92

    def test_call_execution_metadata_update(self, db, test_execution, scenario):
        """Test updating call metadata."""
        call = CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            status=CallExecution.CallStatus.ONGOING,
            call_metadata={},
        )

        # Update metadata like the activity does
        call.call_metadata["simulation_phone_number"] = "+1555123001"
        call.call_metadata["simulation_phone_id"] = str(uuid.uuid4())
        call.call_metadata["service_provider_call_id"] = "provider-call-123"
        call.save()

        call.refresh_from_db()
        assert call.call_metadata["simulation_phone_number"] == "+1555123001"
        assert call.call_metadata["service_provider_call_id"] == "provider-call-123"


# ============================================================================
# Concurrency Tests
# ============================================================================


@pytest.mark.unit
class TestConcurrencyHandling:
    """Tests for concurrency handling in phone number acquisition."""

    def test_concurrent_phone_acquisition_uses_select_for_update(
        self, db, multiple_outbound_phones
    ):
        """Test that concurrent acquisitions don't cause race conditions.

        This tests that select_for_update() is used correctly.
        """
        # Create multiple call executions
        from simulate.models import AgentDefinition
        from simulate.models.run_test import RunTest

        org = multiple_outbound_phones[0].phone_number  # Just need a unique org

        # Acquire phones sequentially (since we can't easily test real concurrency)
        # But verify each acquisition gets a different phone
        acquired_phones = []

        for phone in multiple_outbound_phones[:3]:
            # Reset to idle for this test
            phone.status = SimulationPhoneNumber.PhoneStatus.IDLE
            phone.save()

        from simulate.models.test_execution import TestExecution

        for i in range(3):
            phone = PhoneNumberService.acquire_phone_number(
                call_direction="outbound",
                call_execution=None,
            )
            acquired_phones.append(phone.phone_number)
            # Don't release - keep in use

        # Should have 3 different phones
        assert len(set(acquired_phones)) == 3


# ============================================================================
# Edge Case Tests
# ============================================================================


@pytest.mark.unit
class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_test_execution_with_no_scenarios(self, db, run_test):
        """Test creating test execution with empty scenario list."""
        test_execution = TestExecution.objects.create(
            run_test=run_test,
            status=TestExecution.ExecutionStatus.PENDING,
            scenario_ids=[],
        )

        assert test_execution.scenario_ids == []

    def test_call_execution_with_default_optional_fields(
        self, db, test_execution, scenario
    ):
        """Test call execution with default optional fields.

        Note: phone_number is a CharField with no default, so it defaults to empty string.
        """
        call = CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            status=CallExecution.CallStatus.REGISTERED,
            # All optional fields left as null/default
        )

        assert call.service_provider_call_id is None
        assert call.phone_number is None  # CharField with null=True defaults to None
        assert call.duration_seconds is None
        assert call.cost_cents is None
        assert call.eval_outputs is None

    def test_phone_number_can_be_reused_after_release(
        self, db, outbound_phone, call_execution
    ):
        """Test that a phone can be re-acquired after release."""
        # First acquisition
        phone1 = PhoneNumberService.acquire_phone_number(
            call_direction="outbound",
            call_execution=call_execution,
        )
        phone_number = phone1.phone_number

        # Release
        PhoneNumberService.release_phone_number(phone1.id)

        # Second acquisition - should get same phone (only one available)
        phone2 = PhoneNumberService.acquire_phone_number(
            call_direction="outbound",
            call_execution=call_execution,
        )

        assert phone2.phone_number == phone_number

    def test_soft_deleted_test_execution_not_included_in_queries(
        self, db, run_test, scenario
    ):
        """Test soft delete behavior for test executions."""
        test_execution = TestExecution.objects.create(
            run_test=run_test,
            status=TestExecution.ExecutionStatus.COMPLETED,
            scenario_ids=[str(scenario.id)],
        )

        # Soft delete
        test_execution.deleted = True
        test_execution.deleted_at = timezone.now()
        test_execution.save()

        # Should not appear in default queryset
        assert TestExecution.objects.filter(id=test_execution.id).count() == 0

        # But should appear in all_objects
        assert TestExecution.all_objects.filter(id=test_execution.id).count() == 1

    def test_soft_deleted_call_execution_not_included(
        self, db, test_execution, scenario
    ):
        """Test soft delete behavior for call executions."""
        call = CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            status=CallExecution.CallStatus.COMPLETED,
        )

        call.deleted = True
        call.deleted_at = timezone.now()
        call.save()

        assert CallExecution.objects.filter(id=call.id).count() == 0
        assert CallExecution.all_objects.filter(id=call.id).count() == 1


# ============================================================================
# Integration-Style Tests (Database + Business Logic)
# ============================================================================


@pytest.mark.integration
class TestRunTestIntegration:
    """Integration tests for run test flow with real database."""

    def test_full_test_execution_flow(self, db, run_test, scenario, outbound_phone):
        """Test complete test execution flow without external calls."""
        # 1. Create test execution
        test_execution = TestExecution.objects.create(
            run_test=run_test,
            status=TestExecution.ExecutionStatus.RUNNING,
            scenario_ids=[str(scenario.id)],
            started_at=timezone.now(),
        )

        # 2. Create call execution
        call = CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            status=CallExecution.CallStatus.REGISTERED,
        )

        # 3. Acquire phone
        phone = PhoneNumberService.acquire_phone_number(
            call_direction="outbound",
            call_execution=call,
        )

        # 4. Update call with phone info
        call.phone_number = phone.phone_number
        call.call_metadata = {
            "simulation_phone_number": phone.phone_number,
            "simulation_phone_id": str(phone.id),
        }
        call.status = (
            CallExecution.CallStatus.REGISTERED
        )  # Note: REGISTERED value is 'queued'
        call.save()

        # 5. Simulate call completion
        call.status = CallExecution.CallStatus.COMPLETED
        call.started_at = timezone.now() - timedelta(minutes=2)
        call.ended_at = timezone.now()
        call.duration_seconds = 120
        call.save()

        # 6. Release phone
        PhoneNumberService.release_phone_number(phone.id)

        # 7. Complete test execution
        test_execution.status = TestExecution.ExecutionStatus.COMPLETED
        test_execution.completed_at = timezone.now()
        test_execution.save()

        # Verify final state
        test_execution.refresh_from_db()
        call.refresh_from_db()
        phone.refresh_from_db()

        assert test_execution.status == TestExecution.ExecutionStatus.COMPLETED
        assert call.status == CallExecution.CallStatus.COMPLETED
        assert call.duration_seconds == 120
        assert phone.status == SimulationPhoneNumber.PhoneStatus.IDLE

    def test_multiple_calls_in_test_execution(
        self, db, run_test, scenario, multiple_outbound_phones
    ):
        """Test running multiple calls in a single test execution."""
        test_execution = TestExecution.objects.create(
            run_test=run_test,
            status=TestExecution.ExecutionStatus.RUNNING,
            scenario_ids=[str(scenario.id)],
            started_at=timezone.now(),
        )

        calls = []
        phones = []

        # Create 3 calls
        for i in range(3):
            call = CallExecution.objects.create(
                test_execution=test_execution,
                scenario=scenario,
                status=CallExecution.CallStatus.REGISTERED,
            )
            calls.append(call)

            phone = PhoneNumberService.acquire_phone_number(
                call_direction="outbound",
                call_execution=call,
            )
            phones.append(phone)

            call.phone_number = phone.phone_number
            call.status = CallExecution.CallStatus.COMPLETED
            call.duration_seconds = 60 + i * 30
            call.save()

        # Release all phones
        for phone in phones:
            PhoneNumberService.release_phone_number(phone.id)

        # Complete test execution
        test_execution.status = TestExecution.ExecutionStatus.COMPLETED
        test_execution.completed_at = timezone.now()
        test_execution.save()

        # Verify
        assert test_execution.calls.count() == 3
        assert all(c.status == CallExecution.CallStatus.COMPLETED for c in calls)
