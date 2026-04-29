import uuid

from django.db import models
from django.utils import timezone

from tfc.utils.base_model import BaseModel


class SimulationPhoneNumber(BaseModel):
    """
    Model to manage phone number pool for simulation calls
    Tracks phone numbers, their usage status, and call direction
    """

    class CallDirection(models.TextChoices):
        INBOUND = "inbound", "Inbound"
        OUTBOUND = "outbound", "Outbound"

    class PhoneStatus(models.TextChoices):
        IDLE = "idle", "Idle"
        IN_USE = "in_use", "In Use"
        DISABLED = "disabled", "Disabled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    phone_number = models.CharField(
        max_length=20,
        unique=True,
        help_text="Phone number in E.164 format (e.g., +15551234567)",
    )

    provider_phone_id = models.CharField(max_length=255, unique=True)

    call_direction = models.CharField(
        max_length=20, choices=CallDirection.choices, default=CallDirection.OUTBOUND
    )

    status = models.CharField(
        max_length=20, choices=PhoneStatus.choices, default=PhoneStatus.IDLE
    )

    current_call_execution = models.ForeignKey(
        "simulate.CallExecution",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_phone_number",
        help_text="The call execution currently using this phone number",
    )

    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "simulate_phone_numbers"
        indexes = [
            models.Index(fields=["call_direction", "status", "last_used_at"]),
            models.Index(fields=["status", "call_direction"]),
            models.Index(fields=["phone_number"]),
        ]

    def __str__(self):
        return f"{self.phone_number} ({self.get_call_direction_display()}) - {self.get_status_display()}"

    def mark_in_use(self, call_execution):
        """Mark this phone number as in use"""
        self.status = self.PhoneStatus.IN_USE
        self.current_call_execution = call_execution
        self.last_used_at = timezone.now()
        self.save()

    def mark_idle(self):
        """Mark this phone number as idle and available"""
        self.status = self.PhoneStatus.IDLE
        self.current_call_execution = None
        self.save()

    @property
    def is_available(self):
        """Check if this phone number is available for use"""
        return self.status == self.PhoneStatus.IDLE
