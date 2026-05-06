import uuid
from uuid import UUID

from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field, RootModel
from pydantic import ValidationError as PydanticValidationError
from pydantic import validator

from model_hub.models.choices import StatusType
from model_hub.models.develop_dataset import Column, Dataset
from model_hub.models.evals_metric import UserEvalMetric
from tfc.utils.base_model import BaseModel


class ModelConfig(PydanticBaseModel):
    model_name: str
    temperature: float | None = Field(None, ge=0, le=2)
    frequency_penalty: float | None = Field(None, ge=-2, le=2)
    presence_penalty: float | None = Field(None, ge=-2, le=2)
    max_tokens: int | None = Field(None, gt=0)
    top_p: float | None = Field(None, ge=0, le=1)
    response_format: dict | None = None
    tool_choice: str | None = None
    tools: list[dict] | None = None


class UserEvalTemplateMapping(RootModel):
    root: dict[UUID, UUID]

    @validator("root")
    def validate_uuids(cls, v):
        # Convert string UUIDs to UUID objects and validate
        try:
            return {UUID(str(k)): UUID(str(v)) for k, v in v.items()}
        except ValueError:
            raise ValueError("Invalid UUID format in mapping")  # noqa: B904


def validate_model_config(value):
    if value:
        try:
            # Enforce type and schema validation on model_config by creating an instance of ModelConfig
            ModelConfig.model_validate(value)  # This uses Pydantic's strict validation
        except PydanticValidationError as e:
            raise ValidationError(f"Invalid model configuration: {str(e)}") from e


def validate_template_mapping(value):
    if value:
        try:
            UserEvalTemplateMapping.model_validate(value)
        except Exception as e:
            raise ValidationError(
                f"Invalid user eval template mapping: {str(e)}"
            ) from e


class OptimizationDataset(BaseModel):
    OPTIMIZE_TYPE_CHOICES = [
        ("PROMPT_TEMPLATE", "Prompt Template"),
        ("RIGHT_ANSWER", "Right Answer"),
        ("RAG_PROMPT_TEMPLATE", "Rag Prompt Template"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=2000)
    prompt_name = models.CharField(max_length=2000, blank=True, null=True, default="")
    optimize_type = models.CharField(max_length=50, choices=OPTIMIZE_TYPE_CHOICES)
    optimized_k_prompts = ArrayField(models.TextField(), blank=True, null=True)
    messages = ArrayField(
        models.JSONField(),
        default=list,
        help_text="List of messages with format [{'role': 'user/assistant', 'content': 'text'}]",
        blank=True,
        null=True,
    )
    criteria_breakdown = ArrayField(
        models.CharField(),
        default=list,
        blank=True,
    )
    status = models.CharField(
        max_length=50,
        choices=StatusType.get_choices(),
        default=StatusType.NOT_STARTED.value,
    )

    user_eval_template_ids = models.ManyToManyField(
        UserEvalMetric, blank=True, related_name="optimization_datasets_evals"
    )

    model_config = models.JSONField(
        null=True, blank=True, default=None, validators=[validate_model_config]
    )

    user_eval_template_mapping = models.JSONField(
        null=True, blank=True, validators=[validate_template_mapping]
    )

    dataset = models.ForeignKey(
        Dataset, on_delete=models.CASCADE, related_name="optimizations_dataset"
    )
    column = models.ForeignKey(
        Column,
        on_delete=models.CASCADE,
        related_name="optimizations_col",
        blank=True,
        null=True,
    )
    generated_column_id = models.ManyToManyField(
        Column, blank=True, related_name="optimization_columns"
    )

    def __str__(self):
        return f"{self.name} - {self.optimize_type} - {self.status}"

    def save(self, *args, **kwargs):
        if self.model_config:
            validate_model_config(self.model_config)
        if self.user_eval_template_mapping:
            validate_template_mapping(self.user_eval_template_mapping)
        super().save(*args, **kwargs)

    # def save(self, *args, **kwargs):
    #     # Run full_clean() before saving to enforce validators
    #     self.full_clean()
    #     super().save(*args, **kwargs)

    # # Uncomment and update the clean method
    # def clean(self):
    #     super().clean()
    # if self.model_config:
    #     try:
    #         ModelConfig.model_validate(self.model_config)
    #     except Exception as e:
    #         raise ValidationError(f"Invalid model configuration: {str(e)}")

    # if self.user_eval_template_mapping:
    #     try:
    #         UserEvalTemplateMapping.model_validate(self.user_eval_template_mapping)
    #     except Exception as e:
    #         raise ValidationError(f"Invalid user eval template mapping: {str(e)}")
