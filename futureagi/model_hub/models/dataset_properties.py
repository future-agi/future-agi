import uuid

from django.contrib.postgres.fields import ArrayField
from django.db import models
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field, validator

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from tfc.utils.base_model import BaseModel


class DatasetPropertiesValidation(PydanticBaseModel):
    id: uuid.UUID | None = None
    model_id: uuid.UUID
    environment: str = Field(..., min_length=1, max_length=255)
    version: str = Field(..., min_length=1, max_length=255)
    name: str = Field(..., min_length=1, max_length=255)
    datatype: str = Field(..., min_length=1, max_length=255)
    values: list[str] = Field(default_factory=list)
    explanation: str = Field(..., min_length=1, max_length=255)
    organization_id: uuid.UUID

    @validator("datatype")
    def validate_datatype(cls, v):
        allowed_types = ["numeric", "string", "boolean", "date"]
        if v.lower() not in allowed_types:
            raise ValueError(f"datatype must be one of {allowed_types}")
        return v.lower()

    class Config:
        from_attributes = True
        protected_namespaces = ()


class DatasetProperties(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    model = models.ForeignKey(
        AIModel, on_delete=models.CASCADE, related_name="model_properties"
    )
    environment = models.CharField(max_length=255)
    version = models.CharField(max_length=255)

    name = models.CharField(max_length=255)
    datatype = models.CharField(max_length=255)
    values: list[str] = ArrayField(models.CharField(), blank=True, default=[])
    explanation = models.CharField(max_length=255)

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="properties"
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="dataset_properties",
        null=True,
        blank=True,
    )

    def __str__(self):
        return self.name

    def validate_data(self):
        try:
            validation_data = {
                "id": self.id,
                "model_id": self.model.id,  # Access id through model relationship
                "environment": self.environment,
                "version": self.version,
                "name": self.name,
                "datatype": self.datatype,
                "values": self.values,
                "explanation": self.explanation,
                "organization_id": self.organization.id,  # Access id through organization relationship
            }
            return DatasetPropertiesValidation(**validation_data)
        except AttributeError as e:
            raise ValueError(f"Missing required field: {str(e)}") from e
        except Exception as e:
            raise ValueError(f"Validation failed: {str(e)}") from e

    def save(self, *args, **kwargs):
        self.validate_data()  # Validate before saving
        super().save(*args, **kwargs)
