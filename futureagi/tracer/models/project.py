import uuid
from enum import Enum
from typing import List, Optional

from django.db import models
from pydantic import BaseModel as PydanticBaseModel

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from tfc.utils.base_model import BaseModel

PROJECT_TYPES = (
    ("experiment", "Experiment"),
    ("observe", "Observe"),
)


class ProjectSourceChoices(Enum):
    DEMO = "demo"
    PROTOTYPE = "prototype"
    SIMULATOR = "simulator"

    @classmethod
    def get_choices(cls):
        return [(tag.value, tag.name.replace("_", " ").title()) for tag in cls]


class Project(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="projects",
        blank=False,
        null=False,
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="projects",
        null=True,
        blank=True,
    )
    model_type = models.CharField(
        max_length=50, choices=AIModel.ModelTypes.choices, null=False, blank=False
    )
    name = models.CharField(max_length=255, null=False, blank=False)
    trace_type = models.CharField(
        max_length=20, choices=PROJECT_TYPES, null=False, blank=False
    )
    metadata = models.JSONField(null=True, blank=True)
    config = models.JSONField(default=list, null=True, blank=True)
    session_config = models.JSONField(default=list, null=True, blank=True)
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="projects_user",
        null=True,
        blank=True,
        default=None,
    )
    source = models.CharField(
        max_length=20,
        choices=ProjectSourceChoices.get_choices(),
        null=False,
        blank=False,
        default=ProjectSourceChoices.PROTOTYPE.value,
    )
    tags = models.JSONField(default=list, blank=True)

    def clean(self):
        super().clean()
        if self.metadata is None:
            self.metadata = {}  # Ensure metadata is a dictionary

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    class Meta:
        db_table = "tracer_project"
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["name", "trace_type", "organization", "workspace"],
                condition=models.Q(deleted=False),
                name="unique_project_per_org_type",
            ),
        ]
        indexes = [
            models.Index(fields=["organization"]),
            models.Index(fields=["model_type"]),
            models.Index(fields=["trace_type"]),
            models.Index(fields=["organization", "name", "trace_type"]),
        ]


class TranscriptEntry(PydanticBaseModel):
    id: Optional[str] = None
    role: Optional[str] = None
    content: Optional[str] = None
    time: Optional[str] = None
    duration: Optional[float] = None


class MonoRecording(PydanticBaseModel):
    combined_url: Optional[str] = None
    customer_url: Optional[str] = None
    assistant_url: Optional[str] = None


class Recording(PydanticBaseModel):
    mono: Optional[MonoRecording] = None
    stereo_url: Optional[str] = None


class MessageEntry(PydanticBaseModel):
    role: Optional[str] = None
    time: Optional[float] = None
    source: Optional[str] = None
    end_time: Optional[float] = None
    message: Optional[str] = None
    duration: Optional[float] = None
    seconds_from_start: Optional[float] = None
    metadata: Optional[dict] = None


class AnalysisData(PydanticBaseModel):
    summary: Optional[str] = None
    success_evaluation: Optional[str] = None


class CostBreakdown(PydanticBaseModel):
    stt: Optional[float] = None
    llm: Optional[float] = None
    tts: Optional[float] = None
    vapi: Optional[float] = None
    transport: Optional[float] = None
    total: Optional[float] = None


class VoiceCallLogs(PydanticBaseModel):
    id: Optional[str] = None
    phone_number: Optional[str] = None
    customer_name: Optional[str] = None
    call_id: Optional[str] = None
    status: Optional[str] = None
    started_at: Optional[str] = None
    duration_seconds: Optional[int] = None
    recording_url: Optional[str] = None
    cost_cents: Optional[float] = None
    cost_breakdown: Optional[CostBreakdown] = None
    call_metadata: Optional[dict] = {}
    error_message: Optional[str] = None
    transcript: Optional[List[TranscriptEntry]] = None
    created_at: Optional[str] = None
    recording: Optional[Recording] = None
    stereo_recording_url: Optional[str] = None
    call_summary: Optional[str] = None
    ended_reason: Optional[str] = None
    overall_score: Optional[float] = None
    response_time_ms: Optional[float] = None
    response_time_seconds: Optional[float] = None
    messages: Optional[List[MessageEntry]] = None
    assistant_id: Optional[str] = None
    assistant_phone_number: Optional[str] = None
    call_type: Optional[str] = None
    ended_at: Optional[str] = None
    analysis_data: Optional[AnalysisData] = None
    evaluation_data: Optional[dict] = None
    message_count: Optional[int] = None
    transcript_available: Optional[bool] = None
    recording_available: Optional[bool] = None
    observation_span: Optional[List[dict]] = None
    talk_ratio: Optional[dict] = None
