"""
# Usage examples
meta = DatasetInsightMeta()

# All these are valid:
add_insight_option(meta, "languages", ["Python", "JavaScript"])
add_insight_option(meta, "scores", [0.95, 0.87, 0.76])
add_insight_option(meta, "counts", [1, 2, 3])
add_insight_option(meta, "mixed", ["high", 0.99, 1])

# These will raise ValidationError:
add_insight_option(meta, "invalid", [{"dict": "not allowed"}])  # Type error: dict not allowed
add_insight_option(meta, "", ["value"])  # Empty key
add_insight_option(meta, "dupes", ["a", "a"])  # Duplicate values
"""

import uuid
from typing import Any

from django.core.exceptions import ValidationError
from django.db import models
from pydantic import BaseModel, Field, RootModel, validator

from accounts.models import Organization


class EnvTypes(models.TextChoices):
    PRODUCTION = "Production", "Production"
    TRAINING = "Training", "Training"
    VALIDATION = "Validation", "Validation"
    CORPUS = "Corpus", "Corpus"

    @classmethod
    def get_env_types(cls, num):
        if num == 1:
            return cls.TRAINING
        if num == 2:
            return cls.VALIDATION
        if num == 3:
            return cls.PRODUCTION
        if num == 4:
            return cls.CORPUS

    @classmethod
    def validate_env_type(cls, env_type: str) -> None:
        if env_type not in [choice[0] for choice in cls.choices]:
            raise ValidationError(
                f"Invalid environment type: {env_type}. Must be one of {[choice[0] for choice in cls.choices]}"
            )


class InsightOptionsSchema(RootModel):
    """Pydantic schema for insight_options JSON field"""

    root: dict[str, list[str | float | int]] = Field(default_factory=dict)

    @validator("root")
    def validate_structure(
        cls, v: dict[str, Any]
    ) -> dict[str, list[str | float | int]]:
        """Validate the entire structure matches Dict[str, List[Union[str, float, int]]]"""
        for key, value in v.items():
            # Validate key is string
            if not isinstance(key, str):
                raise ValueError(f"Key must be string, got {type(key)}")

            # Validate value is list
            if not isinstance(value, list):
                raise ValueError(
                    f"Value for key '{key}' must be a list, got {type(value)}"
                )

            # Validate all elements in list are of allowed types
            for item in value:
                if not isinstance(item, str | float | int):
                    raise ValueError(
                        f"Items in list for key '{key}' must be string, float, or int. "
                        f"Got {type(item)} for value: {item}"
                    )

            # Validate uniqueness
            if len(value) != len(
                set(map(str, value))
            ):  # Convert to string for comparison
                raise ValueError(f"Values in list for key '{key}' must be unique")

            # Validate non-empty key
            if not key.strip():
                raise ValueError("Keys cannot be empty strings")

        return v

    class Config:
        json_schema_extra = {
            "example": {
                "chat_language": ["English", "Spanish"],
                "scores": [0.95, 0.87, 0.76],
                "counts": [1, 2, 3],
                "mixed_values": ["high", 0.99, 1],
            }
        }


class PropertyMetadata(BaseModel):
    """Schema for each property's metadata"""

    explanation: str


class InsightMetaSchema(RootModel):
    """Pydantic schema for insight_meta JSON field"""

    root: dict[str, PropertyMetadata] = Field(default_factory=dict)

    class Config:
        json_schema_extra = {
            "example": {
                "tone": {"explanation": "Represents the tone of the conversation"},
                "sentiment": {"explanation": "Indicates the sentiment analysis result"},
            }
        }


class DatasetInsightMeta(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now_add=True)
    model = models.ForeignKey(
        "AIModel",
        on_delete=models.CASCADE,
        related_name="model_dataset_insight_meta",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="dataset_insight_meta",
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        related_name="dataset_insight_meta",
        null=True,
        blank=True,
    )
    insight_options = models.JSONField(null=True)
    insight_meta = models.JSONField(null=True)
    environment = models.CharField(
        max_length=100,
        choices=EnvTypes.choices,
        default=None,
        blank=True,
        null=True,
    )
    version = models.CharField(max_length=255, default=None, blank=True, null=True)

    def clean_insight_options(self) -> None:
        """Validate insight_options using Pydantic schema"""
        if self.insight_options is not None:
            try:
                # Validate using Pydantic schema
                InsightOptionsSchema(root=self.insight_options)
            except ValueError as e:
                raise ValidationError(
                    f"Invalid insight_options format: {str(e)}"
                ) from e

    def clean_insight_meta(self) -> None:
        """Validate insight_meta using Pydantic schema"""
        if self.insight_meta is not None:
            try:
                InsightMetaSchema(root=self.insight_meta)
            except ValueError as e:
                raise ValidationError(f"Invalid insight_meta format: {str(e)}") from e

    def clean_environment(self) -> None:
        """Validate environment is one of the allowed choices"""
        if self.environment is not None:
            EnvTypes.validate_env_type(self.environment)

    def clean(self) -> None:
        """Validate the model"""
        super().clean()
        self.clean_insight_options()
        self.clean_insight_meta()
        self.clean_environment()

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Save the model with validation"""
        self.clean()
        super().save(*args, **kwargs)

    @property
    def typed_insight_options(self) -> dict[str, list[str | float | int]]:
        """Get type-checked insight_options"""
        if self.insight_options is None:
            return {}
        validated = InsightOptionsSchema(root=self.insight_options)
        return validated.root

    def update_insight_options(
        self, new_options: dict[str, list[str | float | int]]
    ) -> None:
        """Update insight_options with type checking"""
        validated = InsightOptionsSchema(root=new_options)
        self.insight_options = validated.root

    @property
    def typed_insight_meta(self) -> dict[str, PropertyMetadata]:
        """Get type-checked insight_meta"""
        if self.insight_meta is None:
            return {}
        validated = InsightMetaSchema(root=self.insight_meta)
        return validated.root

    def __str__(self):
        return str(self.id)


def add_insight_option(
    meta: DatasetInsightMeta, key: str, values: list[str | float | int]
) -> None:
    current_options = meta.typed_insight_options

    if key in current_options:
        # Merge and deduplicate values
        # Convert to strings for comparison, but keep original values
        existing_values = set(map(str, current_options[key]))
        new_values = [v for v in values if str(v) not in existing_values]
        current_options[key] = current_options[key] + new_values
    else:
        current_options[key] = values

    meta.update_insight_options(current_options)
    meta.save()
