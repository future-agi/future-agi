import uuid

from django.contrib.postgres.indexes import GinIndex
from django.core.exceptions import ValidationError
from django.db import models

from accounts.models import Organization
from model_hub.models.choices import StatusType
from model_hub.models.prompt_label import PromptLabel
from model_hub.models.run_prompt import PromptVersion
from tfc.utils.base_model import BaseModel
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.project import Project
from tracer.models.project_version import ProjectVersion
from tracer.models.trace import Trace


def validate_span_status(value):
    valid_choices = [choice[0] for choice in ObservationSpan.SPAN_STATUS]
    if value not in valid_choices:
        raise ValidationError(
            f"Invalid span status. Valid choices are: {', '.join(valid_choices)}"
        )


class UserIdType(models.TextChoices):
    EMAIL = "email", "Email"
    PHONE = "phone", "Phone"
    UUID = "uuid", "UUID"
    CUSTOM = "custom", "Custom"


class EndUser(BaseModel):
    """ """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        related_name="end_users",
        null=True,
        blank=True,
    )
    user_id = models.CharField(max_length=255, null=False, blank=False)
    user_id_type = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        choices=UserIdType.choices,
    )
    user_id_hash = models.CharField(max_length=255, null=True, blank=True)
    metadata = models.JSONField(null=True, blank=True)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
    )

    class Meta:
        unique_together = ("project", "organization", "user_id", "user_id_type")


class ObservationSpan(BaseModel):
    OBSERVATION_SPAN_TYPES = (
        ("tool", "Tool"),
        ("chain", "Chain"),
        ("llm", "LLM"),
        ("retriever", "Retriever"),
        ("embedding", "Embedding"),
        ("agent", "Agent"),
        ("reranker", "Reranker"),
        ("unknown", "Unknown"),
        ("guardrail", "Guardrail"),
        ("evaluator", "Evaluator"),
        ("conversation", "Conversation"),
    )

    OBSERVATION_SPAN_LOGGER_STATUS = (
        ("COMPLETED", "completed"),
        ("ERROR", "error"),
    )

    SPAN_STATUS = (
        ("UNSET", "unset"),
        ("OK", "ok"),
        ("ERROR", "error"),
    )

    id = models.CharField(primary_key=True, max_length=255, editable=False)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="observation_spans",
        null=False,
        blank=False,
    )
    project_version = models.ForeignKey(
        ProjectVersion,
        on_delete=models.CASCADE,
        related_name="observation_spans",
        null=True,
        blank=True,
    )
    trace = models.ForeignKey(
        Trace,
        on_delete=models.CASCADE,
        related_name="observation_spans",
        null=False,
        blank=False,
    )
    parent_span_id = models.CharField(max_length=255, null=True, blank=True)
    name = models.CharField(max_length=2000, null=False, blank=False)
    observation_type = models.CharField(
        max_length=20, choices=OBSERVATION_SPAN_TYPES, null=False, blank=False
    )
    operation_name = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text="Operation type within span kind (e.g., chat, image_generation, speech_to_text)",
    )
    start_time = models.DateTimeField(null=True, blank=True)
    end_time = models.DateTimeField(null=True, blank=True)

    input = models.JSONField(null=True, blank=True)
    output = models.JSONField(null=True, blank=True)

    model = models.CharField(max_length=255, null=True, blank=True)
    model_parameters = models.JSONField(null=True, blank=True)
    latency_ms = models.IntegerField(null=True, blank=True)

    org_id = models.UUIDField(blank=True, null=True)
    org_user_id = models.UUIDField(null=True, blank=True)

    prompt_tokens = models.IntegerField(null=True, blank=True)
    completion_tokens = models.IntegerField(null=True, blank=True)
    total_tokens = models.IntegerField(null=True, blank=True)
    response_time = models.FloatField(null=True, blank=True)

    eval_id = models.CharField(max_length=255, null=True, blank=True)
    cost = models.FloatField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=SPAN_STATUS,
        null=True,
        blank=True,
        validators=[validate_span_status],
    )
    status_message = models.TextField(null=True, blank=True)
    tags = models.JSONField(default=list, blank=True, null=True)
    metadata = models.JSONField(null=True, blank=True)
    span_events = models.JSONField(default=list, blank=True, null=True)

    provider = models.CharField(max_length=255, null=True, blank=True)

    input_images = models.JSONField(default=list, blank=True, null=True)
    eval_input = models.JSONField(default=list, blank=True, null=True)

    eval_attributes = models.JSONField(default=dict, blank=True, null=True)
    custom_eval_config = models.ForeignKey(
        CustomEvalConfig,
        on_delete=models.CASCADE,
        related_name="observation_spans",
        blank=True,
        null=True,
    )
    # TODO(tech-debt): eval_status on the span is a design flaw. It's a denormalized
    # snapshot that goes stale when evals are added/removed. Status should be derived
    # from EvalLogger rows (per-eval-per-span) rather than a single flag here.
    # See: run_evals_on_spans() in span.py, eval_observation_span_runner() in eval.py.
    eval_status = models.CharField(
        max_length=50,
        choices=[(status.value, status.value) for status in StatusType],
        null=True,
        blank=True,
        default=StatusType.INACTIVE.value,
    )

    end_user = models.ForeignKey(
        EndUser,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        default=None,
    )

    prompt_version = models.ForeignKey(
        PromptVersion,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        default=None,
    )

    prompt_label = models.ForeignKey(
        PromptLabel,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        default=None,
    )

    # GenAI Schema Foundation - Flexible attribute storage
    span_attributes = models.JSONField(
        default=dict,
        blank=True,
        help_text="Raw OTEL span attributes in their original form. "
        "Stored for ClickHouse migration and future-proofing.",
    )
    resource_attributes = models.JSONField(
        default=dict,
        blank=True,
        help_text="Raw OTEL resource attributes (service.name, project info, etc.)",
    )
    semconv_source = models.CharField(
        max_length=50,
        default="traceai",
        help_text="Semantic convention source: traceai, otel_genai, openinference, openllmetry",
    )

    def __str__(self):
        return self.name

    class Meta:
        db_table = "tracer_observation_span"
        ordering = ["-start_time"]

        indexes = [
            models.Index(fields=["trace", "created_at"]),
            models.Index(fields=["project", "created_at"]),
            models.Index(fields=["project_version"]),
            models.Index(fields=["parent_span_id"]),
            models.Index(fields=["observation_type"]),
            models.Index(fields=["custom_eval_config"]),
            GinIndex(fields=["metadata"]),
            models.Index(fields=["start_time"]),
            models.Index(fields=["trace", "start_time"]),
            # GenAI Schema Foundation indexes
            GinIndex(fields=["span_attributes"], name="tracer_obse_span_at_gin"),
        ]


class EvalLogger(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    trace = models.ForeignKey(
        Trace,
        on_delete=models.CASCADE,
        related_name="eval_logs",
        null=False,
        blank=False,
    )
    observation_span = models.ForeignKey(
        ObservationSpan,
        on_delete=models.CASCADE,
        related_name="eval_logs",
        null=False,
        blank=False,
    )
    # Denormalised from `trace.project`. Populated automatically by ``save()``
    # for individual creates; bulk_create call sites must set this explicitly
    # (see `inline_evals.py`, `accounts/user_onboard.py`). The CH-side filter
    # for `has_eval` reads this directly via PeerDB CDC, eliminating the
    # JOIN-to-spans previously required to scope eval queries by project.
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="eval_logs",
        null=True,  # nullable during rollout; backfilled by data migration
        blank=True,
    )
    eval_type_id = models.CharField(max_length=255, null=True, blank=True)
    output_metadata = models.JSONField(null=True, blank=True)
    results_tags = models.JSONField(default=list, blank=True)
    results_explanation = models.JSONField(default=dict, blank=True)
    eval_tags = models.JSONField(default=list, blank=True)
    eval_explanation = models.TextField(null=True, blank=True)
    output_bool = models.BooleanField(null=True, blank=True)
    output_float = models.FloatField(null=True, blank=True)
    output_str = models.TextField(null=True, blank=True)
    output_str_list = models.JSONField(default=list, blank=True)
    eval_id = models.CharField(max_length=255, null=True, blank=True)
    eval_task_id = models.CharField(max_length=255, null=True, blank=True)
    custom_eval_config = models.ForeignKey(
        CustomEvalConfig,
        on_delete=models.CASCADE,
        related_name="eval_loggers",
        blank=True,
        null=True,
    )
    error = models.BooleanField(default=False)
    error_message = models.TextField(null=True, blank=True)

    def __str__(self):
        return f"Eval Log {self.id}"

    def save(self, *args, **kwargs):
        # Auto-populate denormalised project_id from trace if unset. Skip
        # for explicit `update_fields` paths to avoid surprising the caller.
        if self.project_id is None and self.trace_id is not None:
            update_fields = kwargs.get("update_fields")
            if not update_fields:
                # Avoid triggering a full Trace fetch if the FK descriptor
                # already cached one; fall back to ID lookup otherwise.
                trace_obj = getattr(self, "_state_trace_cache", None) or self.trace
                if trace_obj is not None:
                    self.project_id = trace_obj.project_id
        super().save(*args, **kwargs)

    class Meta:
        db_table = "tracer_eval_logger"
        ordering = ["-created_at"]

        indexes = [
            models.Index(fields=["trace", "created_at"]),
            models.Index(fields=["observation_span"]),
            models.Index(fields=["custom_eval_config"]),
            models.Index(fields=["output_bool"]),
            models.Index(fields=["output_float"]),
            models.Index(fields=["error"]),
            # Project-scoped scans on the CDC-replicated CH copy. PG-side
            # value too: bulk_selection.py and dashboard counts also benefit.
            models.Index(fields=["project", "created_at"]),
        ]
