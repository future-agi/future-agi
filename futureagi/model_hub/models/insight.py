import uuid
from enum import Enum
from typing import Any

from django.core.exceptions import ValidationError
from django.db import models
from pydantic import BaseModel, Field, RootModel

from accounts.models import Organization


class EnvironmentType(str, Enum):
    """Valid environment types"""

    PRODUCTION = "Production"
    TRAINING = "Training"
    VALIDATION = "Validation"
    CORPUS = "Corpus"


class DatasetConfig(BaseModel):
    """Configuration for a single dataset"""

    environment: EnvironmentType
    model_version: str = Field(alias="version")  # Maps to "version" in input data

    class Config:
        protected_namespaces = ()


class DatasetSchema(RootModel):
    """Schema for datasets JSON field"""

    root: list[DatasetConfig] = Field(default_factory=list)

    class Config:
        populate_by_name = True  # Allows both "version" and "model_version"
        json_schema_extra = {
            "example": [{"environment": "Production", "version": "v1"}]
        }


class InsightOptionsSelectedSchema(BaseModel):
    """Schema for insight_options_selected JSON field"""

    distribution_key: list[str] = Field(alias="breakdown")
    filter: list[dict[str, Any]]
    metrics: list[str] | None = Field(
        default_factory=list
    )  # Made optional with default empty list

    class Config:
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "distribution_key": ["programming-language", "chat_language"],
                "filter": [
                    {
                        "key": "age",
                        "value": [60],
                        "data_type": "number",
                        "operator": ">",
                    }
                ],
                "metrics": ["metric_id_1", "metric_id_2"],  # Optional field
            }
        }


class Insight(models.Model):
    class MetricTypes(models.TextChoices):
        WHOLE_USER_OUTPUT = "WholeUserOutput", "Whole User Output"
        STEPWISE_MODEL_INFERENCE = (
            "StepwiseModelInference",
            "Stepwise Model Inference",
        )

        @classmethod
        def get_metric_type(cls, num):
            if num == 1:
                return cls.WHOLE_USER_OUTPUT
            if num == 2:
                return cls.STEPWISE_MODEL_INFERENCE

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now_add=True)
    name = models.CharField(max_length=255)
    model = models.ForeignKey(
        "AIModel", on_delete=models.CASCADE, related_name="model_insights"
    )
    datasets = models.JSONField(null=True)
    text_prompt = models.TextField()
    metric_type = models.CharField(max_length=100, choices=MetricTypes.choices)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="insights"
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        related_name="insights",
        null=True,
        blank=True,
    )
    insight_options_selected = models.JSONField(null=True)

    def __str__(self):
        return str(self.id)

    def clean_datasets(self) -> None:
        """Validate datasets using Pydantic schema"""
        if self.datasets is not None:
            try:
                DatasetSchema(root=self.datasets)
            except ValueError as e:
                raise ValidationError(f"Invalid datasets format: {str(e)}") from e

    def clean_insight_options_selected(self) -> None:
        """Validate insight_options_selected using Pydantic schema"""
        if self.insight_options_selected is not None:
            try:
                InsightOptionsSelectedSchema(**self.insight_options_selected)
            except ValueError as e:
                raise ValidationError(  # noqa: B904
                    f"Invalid insight_options_selected format: {str(e)}"
                )

    def clean(self) -> None:
        """Validate the model"""
        super().clean()
        self.clean_datasets()
        self.clean_insight_options_selected()

    @property
    def typed_datasets(self) -> list[DatasetConfig]:
        """Get type-checked datasets"""
        if self.datasets is None:
            return []
        validated = DatasetSchema(root=self.datasets)
        return validated.root

    @property
    def typed_insight_options_selected(self) -> InsightOptionsSelectedSchema:
        """Get type-checked insight_options_selected"""
        if self.insight_options_selected is None:
            return InsightOptionsSelectedSchema(
                distribution_key=[],
                filter=[],
                # metrics will default to empty list
            )
        return InsightOptionsSelectedSchema(**self.insight_options_selected)
