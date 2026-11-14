import uuid

from django.contrib.postgres.fields import ArrayField
from django.db import models

from tfc.utils.base_model import BaseModel


class AgentVersionSetup(BaseModel):
    class Status(models.TextChoices):
        STORED = "stored", "Stored"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent_version = models.OneToOneField(
        "simulate.AgentVersion", on_delete=models.CASCADE, related_name="setup"
    )
    status = models.CharField(max_length=8, choices=Status.choices)
    failure_code = models.CharField(max_length=64, default="", blank=True)
    failure_message = models.TextField(default="", blank=True)
    setup_hash = models.CharField(max_length=80, default="", blank=True)
    schema_version = models.CharField(max_length=16, default="", blank=True)
    sdk_version = models.CharField(max_length=32, default="", blank=True)
    agent_type = models.CharField(max_length=16, default="", blank=True)
    agent_kind = models.CharField(max_length=16, default="", blank=True)
    name = models.CharField(max_length=255, default="", blank=True)
    provider = models.CharField(max_length=64, default="", blank=True)
    provider_agent_id = models.CharField(max_length=255, default="", blank=True)
    provider_version = models.CharField(max_length=64, null=True, blank=True)
    model_provider = models.CharField(max_length=255, null=True, blank=True)
    model_name = models.CharField(max_length=255, null=True, blank=True)
    model_temperature = models.FloatField(null=True, blank=True)
    model_max_tokens = models.IntegerField(null=True, blank=True)
    voice_provider = models.CharField(max_length=255, null=True, blank=True)
    voice_id = models.CharField(max_length=255, null=True, blank=True)
    voice_language = models.CharField(max_length=255, null=True, blank=True)
    voice_speed = models.FloatField(null=True, blank=True)
    supports_inbound = models.BooleanField(default=False)
    supports_outbound = models.BooleanField(default=False)
    direction_answer = models.CharField(max_length=8, null=True, blank=True)
    gaps = ArrayField(models.CharField(max_length=32), default=list, blank=True)
    instrumentation = models.CharField(max_length=16, default="none")
    raw_setup = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "simulate_agent_version_setup"


class AgentBehaviourUnit(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    setup = models.ForeignKey(
        AgentVersionSetup, on_delete=models.CASCADE, related_name="behaviour_units"
    )
    position = models.PositiveIntegerField()
    unit_id = models.CharField(max_length=128)
    shape = models.CharField(max_length=16)
    instructions = models.TextField(null=True, blank=True)
    tool_names = ArrayField(models.CharField(max_length=255), default=list, blank=True)
    transitions = ArrayField(models.CharField(max_length=128), default=list, blank=True)

    class Meta:
        db_table = "simulate_agent_behaviour_unit"
        constraints = [
            models.UniqueConstraint(
                fields=["setup", "position"], name="agent_behaviour_unit_position"
            ),
            models.UniqueConstraint(
                fields=["setup", "unit_id"], name="agent_behaviour_unit_unit_id"
            ),
        ]


class AgentTool(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    setup = models.ForeignKey(
        AgentVersionSetup, on_delete=models.CASCADE, related_name="tools"
    )
    position = models.PositiveIntegerField()
    name = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    kind = models.CharField(max_length=16)
    parameters = models.JSONField(null=True, blank=True)
    endpoint_url = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "simulate_agent_tool"
        constraints = [
            models.UniqueConstraint(
                fields=["setup", "position"], name="agent_tool_position"
            ),
        ]


class AgentTelephony(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    setup = models.ForeignKey(
        AgentVersionSetup, on_delete=models.CASCADE, related_name="telephony"
    )
    position = models.PositiveIntegerField()
    number = models.CharField(max_length=32)
    provider = models.CharField(max_length=64, null=True, blank=True)
    inbound = models.BooleanField()
    outbound = models.BooleanField()

    class Meta:
        db_table = "simulate_agent_telephony"
        constraints = [
            models.UniqueConstraint(
                fields=["setup", "position"], name="agent_telephony_position"
            ),
        ]
