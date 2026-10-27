import structlog
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

logger = structlog.get_logger(__name__)
from simulate.models.simulation_phone_number import SimulationPhoneNumber
from simulate.models.test_execution import CallExecution


class PhoneNumberService:
    """
    Service to manage simulation phone number pool with proper concurrency control
    """

    @classmethod
    def acquire_phone_number(cls, call_direction="outbound", call_execution=None):
        """
        Acquire an idle phone number from the pool and mark it as in use.
        Uses select_for_update() to prevent race conditions.

        Args:
            call_direction: 'inbound' or 'outbound'
            call_execution: CallExecution instance using this phone

        Returns:
            SimulationPhoneNumber instance

        Raises:
            ValueError: If no idle phone numbers are available
        """
        try:
            with transaction.atomic():
                phone_number = (
                    SimulationPhoneNumber.objects.select_for_update()
                    .filter(
                        call_direction=call_direction,
                        status=SimulationPhoneNumber.PhoneStatus.IDLE,
                    )
                    .order_by("last_used_at")
                    .first()
                )

                if not phone_number:
                    total_phones = SimulationPhoneNumber.objects.filter(
                        call_direction=call_direction
                    ).count()

                    in_use_phones = SimulationPhoneNumber.objects.filter(
                        call_direction=call_direction,
                        status=SimulationPhoneNumber.PhoneStatus.IN_USE,
                    ).count()

                    raise ValueError(
                        f"No idle {call_direction} phone numbers available. "
                        f"Total: {total_phones}, In use: {in_use_phones}. "
                        f"Please add more phone numbers or wait for calls to complete."
                    )

                # Mark as in use
                phone_number.status = SimulationPhoneNumber.PhoneStatus.IN_USE
                phone_number.current_call_execution = call_execution
                phone_number.last_used_at = timezone.now()
                phone_number.save()

                logger.info(
                    f"Acquired {call_direction} phone number {phone_number.phone_number} "
                    f"for call execution {call_execution.id if call_execution else 'None'}"
                )
                return phone_number

        except ValueError:
            # Re-raise ValueError for no available phones
            raise
        except Exception as e:
            logger.error(f"Error acquiring phone number: {str(e)}")
            raise Exception(f"Failed to acquire phone number: {str(e)}")

    @classmethod
    def release_phone_number(cls, phone_number_id):
        """
        Release a phone number back to the idle pool.

        Args:
            phone_number_id: UUID of the SimulationPhoneNumber to release

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            with transaction.atomic():
                phone_number = SimulationPhoneNumber.objects.select_for_update().get(
                    id=phone_number_id
                )

                phone_number.status = SimulationPhoneNumber.PhoneStatus.IDLE
                phone_number.current_call_execution = None
                phone_number.save()

                logger.info(
                    f"Released phone number {phone_number.phone_number} "
                    f"(direction: {phone_number.call_direction})"
                )

                return True

        except SimulationPhoneNumber.DoesNotExist:
            logger.warning(f"Phone number {phone_number_id} not found")
            return False
        except Exception as e:
            logger.error(f"Error releasing phone number {phone_number_id}: {str(e)}")
            return False

    @classmethod
    def cleanup_phone_numbers(cls):
        """
        Clean up phone numbers that are no longer in use.

        This handles two cases:
        1. Releases numbers from calls that have finished (completed, failed, or cancelled).
        2. Releases numbers that are 'in_use' but have no associated call execution (orphaned).
        """
        terminal_statuses = [
            CallExecution.CallStatus.COMPLETED,
            CallExecution.CallStatus.ANALYZING,
            CallExecution.CallStatus.FAILED,
            CallExecution.CallStatus.CANCELLED,
        ]

        query = Q(status=SimulationPhoneNumber.PhoneStatus.IN_USE) & (
            Q(current_call_execution__status__in=terminal_statuses)
            | Q(current_call_execution__isnull=True)
        )

        updated_count = SimulationPhoneNumber.objects.filter(query).update(
            status=SimulationPhoneNumber.PhoneStatus.IDLE, current_call_execution=None
        )

        logger.info(
            f"Released {updated_count} phone numbers from completed or orphaned calls."
        )
