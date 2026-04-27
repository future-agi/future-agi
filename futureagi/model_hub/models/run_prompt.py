import uuid
from enum import Enum
from uuid import uuid4

from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from pydantic import BaseModel as PydanticBaseModel
from pydantic import validator

from accounts.models import User
from accounts.models.organization import Organization
from model_hub.models.choices import StatusType
from model_hub.models.develop_dataset import Dataset, KnowledgeBaseFile
from model_hub.models.eval_groups import EvalGroup
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.openai_tools import Tools
from model_hub.models.prompt_base_template import PromptBaseTemplate
from model_hub.models.prompt_folders import PromptFolder
from model_hub.models.prompt_label import PromptLabel
from tfc.utils.base_model import BaseModel


class RunPrompter(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    # Required fields
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE)
    model = models.CharField(max_length=2000)
    name = models.CharField(max_length=2000)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="run_prompt_org",
        null=True,
        blank=True,
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        related_name="run_prompts",
        null=True,
        blank=True,
    )
    concurrency = models.IntegerField(
        default=5,
        validators=[MinValueValidator(1), MaxValueValidator(10)],
        help_text="Concurrency level, default is 5. Maximum 10.",
    )

    # Messages field
    messages = ArrayField(
        models.JSONField(),
        default=list,
        help_text="List of messages with format [{'role': 'user/assistant', 'content': 'text'}]",
    )

    # Optional fields with validation
    OUTPUT_FORMAT_CHOICES = [
        ("array", "array"),
        ("string", "string"),
        ("number", "number"),
        ("object", "object"),
        ("audio", "audio"),
        ("image", "image"),
    ]
    output_format = models.CharField(
        max_length=10,
        choices=OUTPUT_FORMAT_CHOICES,
        default="string",
        help_text="Output format type. Defaults to 'string'.",
    )
    temperature = models.FloatField(
        null=True,
        blank=True,
        default=None,
        validators=[MinValueValidator(0.0), MaxValueValidator(2.0)],
        help_text="Controls the randomness. Value between 0 and 2.",
    )
    frequency_penalty = models.FloatField(
        null=True,
        blank=True,
        default=None,
        validators=[MinValueValidator(-2.0), MaxValueValidator(2.0)],
        help_text="Penalty for word repetition. Value between -2 and 2.",
    )
    presence_penalty = models.FloatField(
        null=True,
        blank=True,
        default=None,
        validators=[MinValueValidator(-2.0), MaxValueValidator(2.0)],
        help_text="Penalty for new word usage. Value between -2 and 2.",
    )
    max_tokens = models.IntegerField(
        null=True,
        blank=True,
        default=None,
        validators=[MinValueValidator(1), MaxValueValidator(65536)],
        help_text="Maximum number of tokens to generate. Null = use provider default.",
    )
    top_p = models.FloatField(
        null=True,
        blank=True,
        default=None,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text="Controls diversity via nucleus sampling. Value between 0 and 1.",
    )
    response_format = models.JSONField(
        blank=True,
        null=True,
        help_text="JSON schema for response format if required. Defaults to None.",
    )

    TOOL_CHOICES = [
        ("auto", "auto"),
        ("required", "required"),
        (None, None),
    ]
    tool_choice = models.CharField(
        max_length=10,
        choices=TOOL_CHOICES,
        blank=True,
        null=True,
        help_text="Tool selection mode: 'auto' or 'required'.",
    )
    tools = models.ManyToManyField(Tools, blank=True, related_name="runprompters")

    status = models.CharField(
        max_length=100,
        default=StatusType.NOT_STARTED.value,
        choices=StatusType.get_choices(),
    )

    run_prompt_config = models.JSONField(null=True, blank=True, default=dict)

    def clean(self):
        super().clean()

        # Custom validation for messages structure
        if not isinstance(self.messages, list) or not all(
            isinstance(msg, dict)
            and "role" in msg
            and msg["role"] in {"user", "assistant", "system"}
            and "content" in msg
            and isinstance(msg["content"], str)
            for msg in self.messages
        ):
            raise ValidationError(
                "Messages must be a list of dicts with 'role' (user/assistant/system) and 'content' (string)."
            )

    class Meta:
        verbose_name = "Litellm Evaluation"
        verbose_name_plural = "Litellm Evaluations"

    def __str__(self):
        return f"{self.name}"


class ModelConfig(PydanticBaseModel):
    temperature: float | str | None = None
    frequency_penalty: float | str | None = None
    presence_penalty: float | str | None = None
    max_tokens: int | str | None = None
    top_p: float | None | None = None
    response_format: dict | str | uuid.UUID | None = None
    tool_choice: str | dict | None | None = None
    tools: list[uuid.UUID] | None = None

    @validator("tools")
    def validate_tools(cls, v):
        if v is not None:
            tool_ids = set(Tools.objects.values_list("id", flat=True))
            for tool_id in v:
                if tool_id not in tool_ids:
                    raise ValueError(f"Tool with ID {tool_id} does not exist")
        return v

    @validator("response_format")
    def validate_response_format(cls, rf):
        if rf is not None:
            if isinstance(rf, uuid.UUID):
                response_format_ids = set(
                    UserResponseSchema.objects.values_list("id", flat=True)
                )
                if rf not in response_format_ids:
                    raise ValueError(f"Response format with ID {rf} does not exist")
        return rf


class Message(PydanticBaseModel):
    role: str
    content: str


class PromptConfig(PydanticBaseModel):
    messages: list[Message]
    model: str
    variable_names: dict | None | None = None
    configuration: ModelConfig
    providers: str | None = None


def validate_prompt_config(value):
    try:
        if isinstance(value, list):
            [PromptConfig(**item) for item in value]
        return value
    except Exception as e:
        raise ValidationError(f"Invalid prompt config format: {str(e)}")  # noqa: B904


class PromptTemplate(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    name = models.CharField(max_length=2000)
    description = models.TextField(blank=True, null=True)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="prompt_templates",
        null=True,
        blank=True,
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        related_name="prompt_templates",
        null=True,
        blank=True,
    )
    variable_names = models.JSONField(default=list, null=True, blank=True)
    placeholders = models.JSONField(default=dict, null=True, blank=True)
    collaborators = models.ManyToManyField(User, related_name="user_prompts")
    prompt_folder = models.ForeignKey(
        PromptFolder,
        on_delete=models.CASCADE,
        related_name="prompt_templates",
        null=True,
        blank=True,
    )
    is_sample = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="prompt_templates",
        null=True,
        blank=True,
    )


class PromptVersion(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    # Remove the prompt_template ForeignKey since we have the snapshot
    prompt_config_snapshot = models.JSONField(default=list, null=True, blank=True)
    template_version = models.CharField(
        max_length=50
    )  # Store the version for reference
    original_template = models.ForeignKey(
        PromptTemplate,
        on_delete=models.CASCADE,
        related_name="all_executions",
        null=True,
    )
    output = models.JSONField(default=list, null=True, blank=True)
    variable_names = models.JSONField(default=dict, null=True, blank=True)
    metadata = models.JSONField(default=dict, null=True, blank=True)
    evaluation_results = models.JSONField(default=dict, null=True, blank=True)
    evaluation_configs = models.JSONField(default=list, null=True, blank=True)
    commit_message = models.TextField(null=True, blank=True, default="")
    is_default = models.BooleanField(default=False)
    is_draft = models.BooleanField(default=False)
    labels = models.ManyToManyField(
        PromptLabel, related_name="prompt_versions", blank=True
    )
    prompt_base_template = models.ForeignKey(
        PromptBaseTemplate,
        on_delete=models.CASCADE,
        related_name="prompt_versions",
        null=True,
        blank=True,
    )

    placeholders = models.JSONField(default=dict, null=True, blank=True)

    class Meta:
        # Ensure unique version per template to prevent duplicates
        unique_together = [("original_template", "template_version")]
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if self.is_default:
            PromptVersion.objects.filter(
                original_template=self.original_template
            ).exclude(id=self.id).update(is_default=False)

        super().save(*args, **kwargs)

    def add_label(self, label):
        """Add a label to this prompt version, preventing duplicates"""
        if not self.labels.filter(id=label.id).exists():
            self.labels.add(label)

    def remove_label(self, label):
        """Remove a label from this prompt version"""
        self.labels.remove(label)


class SchemaTypeChoices(Enum):
    JSON = "json"
    YAML = "yaml"

    @classmethod
    def get_choices(cls):
        return [(tag.value, tag.name.replace("_", " ").title()) for tag in cls]


class UserResponseSchema(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    name = models.CharField(max_length=2000)
    description = models.TextField(blank=True, null=True, default="")
    schema = models.JSONField(default=dict)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="custom_response_schemas",
        null=True,
        blank=True,
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        related_name="custom_response_schemas",
        null=True,
        blank=True,
    )
    schema_type = models.CharField(
        blank=True,
        null=True,
        choices=SchemaTypeChoices.get_choices(),
        default=SchemaTypeChoices.JSON.value,
    )

    class Meta:
        # Add constraints to ensure name uniqueness within an organization
        constraints = [
            models.UniqueConstraint(
                fields=["name", "organization", "workspace"],
                name="unique_schema_name_per_organization",
            )
        ]

    def __str__(self):
        return self.name


class PromptEvalConfig(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    name = models.CharField(max_length=2000, blank=True, null=True)
    eval_template = models.ForeignKey(
        EvalTemplate, on_delete=models.CASCADE, related_name="prompt_eval_configs"
    )
    prompt_template = models.ForeignKey(
        PromptTemplate, on_delete=models.CASCADE, related_name="prompt_eval_configs"
    )
    mapping = models.JSONField(default=dict, blank=True, null=True)
    config = models.JSONField(default=dict, blank=True, null=True)
    kb = models.ForeignKey(
        KnowledgeBaseFile, on_delete=models.CASCADE, null=True, blank=True
    )
    error_localizer = models.BooleanField(default=False)
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="prompt_eval_configs",
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=50,
        choices=StatusType.get_choices(),
        default=StatusType.COMPLETED.value,
    )

    eval_group = models.ForeignKey(
        EvalGroup,
        on_delete=models.CASCADE,
        related_name="prompt_eval_configs",
        null=True,
        blank=True,
        default=None,
    )
