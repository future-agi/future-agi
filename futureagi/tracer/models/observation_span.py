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
from tracer.models.trace_session import TraceSession


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


class EvalTargetType(models.TextChoices):
    """The unit of evaluation an EvalLogger row represents.

    The discriminator that distinguishes span-level results (current shape)
    from trace-level results (anchored to the trace's root span — same FK
    columns as a span row, target_type tells them apart) and session-level
    results (FKs nullable on this row, ``trace_session`` set instead).
    See ``EvalLogger.Meta.constraints`` for the per-target_type FK rule.
    """

    SPAN = "span", "Span"
    TRACE = "trace", "Trace"
    SESSION = "session", "Session"


class EvalLogger(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Nullable for ``target_type='session'`` rows; populated otherwise.
    # Per the eval_logger_target_type_fks check constraint:
    #   span/trace target -> observation_span + trace set, trace_session NULL
    #   session target    -> observation_span + trace NULL,  trace_session set
    trace = models.ForeignKey(
        Trace,
        on_delete=models.CASCADE,
        related_name="eval_logs",
        null=True,
        blank=True,
    )
    observation_span = models.ForeignKey(
        ObservationSpan,
        on_delete=models.CASCADE,
        related_name="eval_logs",
        null=True,
        blank=True,
    )
    trace_session = models.ForeignKey(
        TraceSession,
        on_delete=models.CASCADE,
        related_name="eval_logs",
        null=True,
        blank=True,
    )
    target_type = models.CharField(
        max_length=16,
        choices=EvalTargetType.choices,
        default=EvalTargetType.SPAN,
        db_index=True,
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

    def clean(self):
        """Enforce the per-``target_type`` FK shape at the Python layer.

        Mirrors the ``eval_logger_target_type_fks`` DB CHECK constraint so
        single-row ``.save()`` writes raise a clean ``ValidationError`` early
        instead of bubbling an opaque ``IntegrityError`` from Postgres. The
        DB CHECK remains the authoritative defense for paths that bypass
        ``Model.save()`` — ``bulk_create``, raw SQL, the ClickHouse CDC
        mirror.
        """
        super().clean()
        if self.target_type == EvalTargetType.SESSION:
            if self.observation_span_id or self.trace_id:
                raise ValidationError(
                    "Session-target EvalLogger rows must not set "
                    "observation_span or trace."
                )
            if not self.trace_session_id:
                raise ValidationError(
                    "Session-target EvalLogger rows must set trace_session."
                )
        else:
            if self.trace_session_id:
                raise ValidationError(
                    "Span/trace-target EvalLogger rows must not set "
                    "trace_session."
                )
            if not (self.observation_span_id and self.trace_id):
                raise ValidationError(
                    "Span/trace-target EvalLogger rows must set both "
                    "observation_span and trace."
                )

    def save(self, *args, **kwargs):
        # ``full_clean()`` runs field validators + ``clean()``. Single-row
        # writes via ``.save()`` get this validation; ``bulk_create`` / raw
        # inserts rely on the DB CHECK constraint instead (Django skips
        # ``clean()`` for ``bulk_create`` by design).
        self.full_clean()
        super().save(*args, **kwargs)

    class Meta:
        db_table = "tracer_eval_logger"
        ordering = ["-created_at"]

        indexes = [
            models.Index(fields=["trace", "created_at"]),
            models.Index(fields=["observation_span"]),
            models.Index(fields=["trace_session"]),
            models.Index(fields=["custom_eval_config"]),
            models.Index(fields=["output_bool"]),
            models.Index(fields=["output_float"]),
            models.Index(fields=["error"]),
            # Dedup queries from the trace + session evaluators (PR4) and
            # span-only readers narrow on this triple. Created concurrently
            # in the migration to avoid table locks in prod.
            models.Index(
                fields=["eval_task_id", "target_type", "custom_eval_config"],
                name="eval_logger_task_target_idx",
            ),
        ]
        constraints = [
            # Mutual-exclusion rule: span and trace targets share the
            # span+trace FK shape (trace anchors to the trace's root span,
            # disambiguated by ``target_type``); session targets have NULL
            # span/trace and a populated ``trace_session`` instead. The
            # FE-visible "session evals never appear on span/trace
            # surfaces" rule is enforced at the reader level by
            # ``target_type IN ('span','trace')`` filters; this constraint
            # just keeps the DB shape consistent.
            models.CheckConstraint(
                condition=(
                    (
                        models.Q(target_type__in=["span", "trace"])
                        & models.Q(observation_span__isnull=False)
                        & models.Q(trace__isnull=False)
                        & models.Q(trace_session__isnull=True)
                    )
                    | (
                        models.Q(target_type="session")
                        & models.Q(observation_span__isnull=True)
                        & models.Q(trace__isnull=True)
                        & models.Q(trace_session__isnull=False)
                    )
                ),
                name="eval_logger_target_type_fks",
            ),
        ]
