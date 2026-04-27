import uuid
from typing import Union

from django.core.exceptions import ValidationError
from django.db import models
from pydantic import BaseModel as PydanticBaseModel

from accounts.models import User
from model_hub.models.choices import StatusType
from model_hub.models.develop_dataset import Column, Dataset, Row
from model_hub.models.evals_metric import UserEvalMetric
from model_hub.models.run_prompt import ModelConfig, PromptTemplate, PromptVersion
from tfc.utils.base_model import BaseModel


class Message(PydanticBaseModel):
    role: str
    content: str | list[dict]


class ModelSpec(PydanticBaseModel):
    """Model specification for experiments - supports both string and object formats"""

    name: str
    config: dict | None = None
    display_name: str | None = None


class PromptConfig(PydanticBaseModel):
    name: str
    messages: list[Message]
    model: list[Union[str, ModelSpec]]  # Accept both string and dict formats
    configuration: ModelConfig


def validate_prompt_config(value):
    try:
        if isinstance(value, list):
            [PromptConfig(**item) for item in value]
        return value
    except Exception as e:
        raise ValidationError(f"Invalid prompt config format: {str(e)}")  # noqa: B904


class ExperimentDatasetTable(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    name = models.CharField(max_length=2000)
    legacy_prompt_config = models.JSONField(default=dict, db_column="prompt_config")
    exclude_values = models.JSONField(default=dict)
    status = models.CharField(
        max_length=50,
        choices=StatusType.get_choices(),
        default=StatusType.NOT_STARTED.value,
    )

    columns = models.ManyToManyField(
        Column, blank=True, related_name="experiments_dataset_column"
    )

    # New FK to replace M2M (kept alongside M2M until migration completes)
    experiment = models.ForeignKey(
        "ExperimentsTable",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="experiment_datasets",
    )

    # Config is linked via reverse OneToOne from ExperimentPromptConfig/ExperimentAgentConfig.
    # Access: edt.prompt_config or edt.agent_config

    def __str__(self):
        return f"{self.name}"

    def get_columns_from_experiment(self):
        if self.experiment:
            return self.experiment.column
        return None


class ExperimentsTable(BaseModel):
    EXPERIMENT_TYPE_CHOICES = [
        ("llm", "LLM"),
        ("tts", "TTS"),
        ("stt", "STT"),
        ("image", "Image"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    dataset = models.ForeignKey(
        Dataset, on_delete=models.CASCADE, related_name="experiments_dataset"
    )
    column = models.ForeignKey(Column, on_delete=models.CASCADE, null=True, blank=True)
    prompt_config = models.JSONField(default=list, validators=[validate_prompt_config])
    status = models.CharField(
        max_length=50,
        choices=StatusType.get_choices(),
        default=StatusType.NOT_STARTED.value,
    )

    user_eval_template_ids = models.ManyToManyField(
        UserEvalMetric, blank=True, related_name="experiments_evaluation_ids"
    )

    experiments_datasets = models.ManyToManyField(
        ExperimentDatasetTable, blank=True, related_name="experiments_datasets_created"
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="experiments_user",
        null=True,
        blank=True,
        default=None,
    )

    # New fields for V2
    snapshot_dataset = models.ForeignKey(
        Dataset,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="experiment_snapshots",
    )
    experiment_type = models.CharField(
        max_length=50,
        choices=EXPERIMENT_TYPE_CHOICES,
        default="llm",
        help_text="Determines how the experiment executes: llm, tts, stt, or image.",
    )

    def __str__(self):
        return f"{self.name}"

    def save(self, *args, **kwargs):
        if self.prompt_config:
            validate_prompt_config(self.prompt_config)
        super().save(*args, **kwargs)


class ExperimentPromptConfig(BaseModel):
    """
    Stores prompt configuration for an experiment. Used for ALL experiment types.
    - For experiment_type=llm: links to PromptTemplate + PromptVersion (messages locked).
    - For experiment_type=tts|stt|image: prompt_template/prompt_version are null, messages stored inline.

    Linked 1:1 to ExperimentDatasetTable. Access experiment via experiment_dataset.experiment.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    experiment_dataset = models.OneToOneField(
        ExperimentDatasetTable,
        on_delete=models.CASCADE,
        related_name="prompt_config",
    )

    # LLM experiments: link to PromptTemplate/PromptVersion (messages locked from PromptVersion)
    # TTS/STT/Image experiments: both are null
    prompt_template = models.ForeignKey(
        PromptTemplate,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="experiment_prompt_configs",
    )
    prompt_version = models.ForeignKey(
        PromptVersion,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="experiment_prompt_configs",
    )

    name = models.CharField(max_length=2000)
    model = models.CharField(
        max_length=2000,
        help_text="Single model name (e.g., 'gpt-5', 'openai/tts-1-hd').",
    )
    model_display_name = models.CharField(
        max_length=2000,
        null=True,
        blank=True,
        help_text="Optional display name for the model.",
    )
    model_config = models.JSONField(
        default=dict,
        help_text='Inline config from model spec dict (e.g., {"voice": "alloy"} for TTS). Empty for string-format models.',
    )
    model_params = models.JSONField(
        default=dict,
        help_text='Per-model params from modelParams[model_name]: {"temperature": 0.5, "maxTokens": 4085, "providers": "openai", "logoUrl": "..."}',
    )
    configuration = models.JSONField(
        default=dict,
        help_text='Tools config: {"toolChoice": "auto", "tools": [...]}',
    )
    output_format = models.CharField(
        max_length=50,
        default="string",
        help_text="Output format: string, object, audio, image.",
    )
    order = models.PositiveIntegerField(
        default=0,
        help_text="Deterministic processing order.",
    )

    # TTS/STT/Image: messages stored inline (null for LLM experiments)
    messages = models.JSONField(
        null=True,
        blank=True,
        help_text="Inline messages for tts/stt/image experiments. Null when prompt_version is set.",
    )

    # STT-specific
    voice_input_column = models.ForeignKey(
        Column,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="experiment_stt_configs",
        help_text="STT only: references dataset column containing audio data to transcribe.",
    )

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"{self.name}"

    def get_messages(self):
        """
        Returns messages: locked from PromptVersion if set, otherwise inline.
        """
        if self.prompt_version:
            return self.prompt_version.prompt_config_snapshot
        return self.messages


class ExperimentAgentConfig(BaseModel):
    """
    Links an experiment to a Graph/GraphVersion from agent_playground.
    Only used when experiment_type=llm. Configuration is frozen (GraphVersion is immutable).

    Linked 1:1 to ExperimentDatasetTable. Access experiment via experiment_dataset.experiment.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    experiment_dataset = models.OneToOneField(
        ExperimentDatasetTable,
        on_delete=models.CASCADE,
        related_name="agent_config",
    )
    graph = models.ForeignKey(
        "agent_playground.Graph",
        on_delete=models.PROTECT,
        related_name="experiment_agent_configs",
    )
    graph_version = models.ForeignKey(
        "agent_playground.GraphVersion",
        on_delete=models.PROTECT,
        related_name="experiment_agent_configs",
    )
    name = models.CharField(max_length=2000)
    order = models.PositiveIntegerField(
        default=0,
        help_text="Processing order.",
    )

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"{self.name}"


class ExperimentComparison(BaseModel):
    experiment = models.ForeignKey(
        ExperimentsTable, on_delete=models.CASCADE, related_name="comparisons"
    )
    experiment_dataset = models.ForeignKey(
        ExperimentDatasetTable, on_delete=models.CASCADE, null=True, blank=True
    )

    # Weights
    response_time_weight = models.FloatField()
    scores_weight = models.JSONField(default=dict, null=True, blank=True)
    total_tokens_weight = models.FloatField()
    completion_tokens_weight = models.FloatField()

    # Raw Metrics
    avg_completion_tokens = models.FloatField()
    avg_total_tokens = models.FloatField()
    avg_response_time = models.FloatField()
    avg_score = models.FloatField(null=True)

    # Normalized Scores
    normalized_completion_tokens = models.FloatField()
    normalized_total_tokens = models.FloatField()
    normalized_response_time = models.FloatField()
    normalized_score = models.FloatField(null=True)

    # Final Results
    overall_rating = models.FloatField(default=0.0)
    rank = models.IntegerField(default=1)

    class Meta:
        ordering = ["-created_at"]


class PendingRowTask(BaseModel):
    """
    Model to store row processing tasks for batching with concurrency limits.
    Similar to CreateCallExecution pattern for managing concurrent task execution.
    """

    class TaskStatus(models.TextChoices):
        REGISTERED = "registered", "Registered"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    row = models.ForeignKey(
        Row,
        on_delete=models.CASCADE,
        related_name="pending_row_tasks",
        help_text="The row to process",
    )

    column = models.ForeignKey(
        Column,
        on_delete=models.CASCADE,
        related_name="pending_row_tasks",
        help_text="The column this row processing belongs to",
    )

    dataset = models.ForeignKey(
        Dataset,
        on_delete=models.CASCADE,
        related_name="pending_row_tasks",
        help_text="The dataset this row processing belongs to",
    )

    experiment = models.ForeignKey(
        ExperimentsTable,
        on_delete=models.CASCADE,
        related_name="pending_row_tasks",
        help_text="The experiment this row processing belongs to",
    )

    messages = models.JSONField(help_text="The messages to use for processing the row")

    model = models.CharField(
        max_length=255, help_text="The model to use for processing"
    )

    model_config = models.JSONField(
        help_text="The model configuration to use for processing"
    )

    output_format = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="The output format for the response",
    )

    run_prompt_config = models.JSONField(
        null=True,
        blank=True,
        help_text="The run prompt configuration",
    )

    status = models.CharField(
        max_length=255,
        choices=TaskStatus.choices,
        default=TaskStatus.REGISTERED,
        help_text="The status of the row processing task",
    )

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(
                fields=["status", "created_at"]
            ),  # For process_pending_row_tasks query
            models.Index(
                fields=["experiment_id", "status"]
            ),  # For per-experiment count queries
            models.Index(
                fields=["column_id", "status"]
            ),  # For check_and_update_column_status
            models.Index(
                fields=["row_id", "column_id", "dataset_id", "experiment_id", "status"]
            ),  # For process_row_task lookup
        ]
