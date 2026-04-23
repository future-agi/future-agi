import json
import uuid

import pydantic
import structlog
from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from accounts.models.organization import Organization
from accounts.models.user import User

logger = structlog.get_logger(__name__)
from model_hub.models.choices import (
    CellStatus,
    DatasetSourceChoices,
    DataTypeChoices,
    EvalExplanationSummaryStatus,
    ModelTypes,
    SourceChoices,
    StatusType,
)
from tfc.utils.base_model import BaseModel


# Pydantic schema for eval reasons
class EvalReasonSchema(pydantic.BaseModel):
    theme: str = pydantic.Field(
        ..., description="Theme or category of the evaluation reason"
    )
    triggers: list[str] = pydantic.Field(
        default_factory=list, description="List of trigger conditions"
    )
    evidence_summary: str = pydantic.Field(
        ..., description="Summary of evidence for this evaluation"
    )
    guidance: str = pydantic.Field(..., description="Guidance or recommendations")
    confidence: str = pydantic.Field(
        ..., description="Confidence level (e.g., high, medium, low)"
    )
    eval_template_id: str = pydantic.Field(
        ..., description="Evaluation template identifier"
    )


def validate_eval_reasons(value):
    """Validator function for eval_reasons JSONField"""
    try:
        # Handle both dict and json dumps
        if isinstance(value, str):
            json_data = json.loads(value)
        else:
            json_data = value

        # If it's not a list, wrap it in a list for validation
        if not isinstance(json_data, dict):
            raise ValidationError("eval_reasons must be a dict of dictionaries")

        # Validate each item in the list
        for item in json_data:
            if not isinstance(json_data[item], dict):
                raise ValidationError("Each item in eval_reasons must be a dictionary")
            for i in range(len(json_data[item]["summary"])):
                EvalReasonSchema(**json_data[item]["summary"][i])

    except pydantic.ValidationError as e:
        raise ValidationError(f"Invalid eval_reasons format: {e}")  # noqa: B904
    except json.JSONDecodeError:
        raise ValidationError("Invalid JSON format")  # noqa: B904
    except (TypeError, ValueError) as e:
        raise ValidationError(f"Invalid eval_reasons data: {e}")  # noqa: B904


def validate_model_type_choice(value):
    valid_choices = [choice.value for choice in ModelTypes]
    if value not in valid_choices:
        raise ValidationError(
            f"Invalid model type choice. Valid choices are: {', '.join(valid_choices)}"
        )


def validate_dataset_source_choice(value):
    valid_choices = [choice.value for choice in DatasetSourceChoices]
    if value not in valid_choices:
        raise ValidationError(
            f"Invalid source choice. Valid choices are: {', '.join(valid_choices)}"
        )


class Dataset(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.CharField(
        max_length=50,
        choices=DatasetSourceChoices.get_choices(),
        validators=[validate_dataset_source_choice],
        default=DatasetSourceChoices.BUILD.value,
    )
    name = models.CharField(max_length=2000)
    column_order = ArrayField(
        models.CharField(max_length=100),  # Specify the max length for each tag
        blank=True,  # Allows an empty list as a default
        default=list,  # Uses an empty list as the default value
    )
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="dataset_org"
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        related_name="datasets",
        null=True,
        blank=True,
    )
    model_type = models.CharField(
        max_length=50,
        choices=ModelTypes.get_choices(),
        validators=[validate_model_type_choice],
        default=ModelTypes.GENERATIVE_LLM.value,
    )
    column_config = models.JSONField(
        default=dict, blank=True, null=True
    )  # todo: add structure validation in pydantic
    dataset_config = models.JSONField(default=dict, blank=True, null=True)
    # Store validated_data for synthetic dataset creation/editing
    synthetic_dataset_config = models.JSONField(default=dict, blank=True, null=True)
    eval_reasons = models.JSONField(default=list, blank=True, null=True)
    eval_reason_last_updated = models.DateTimeField(null=True, blank=True)
    eval_reason_status = models.CharField(
        max_length=20,
        choices=EvalExplanationSummaryStatus.choices,
        default=EvalExplanationSummaryStatus.PENDING,
    )

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="datasets_user",
        null=True,
        blank=True,
        default=None,
    )

    class Meta(BaseModel.Meta):
        indexes = [
            models.Index(fields=["organization", "workspace"]),
            models.Index(fields=["source"]),
            models.Index(
                fields=["organization", "deleted", "source"],
                name="dataset_org_del_source_idx",
            ),
        ]

    def clean(self):
        super().clean()
        validate_model_type_choice(self.model_type)
        # validate_eval_reasons(self.eval_reasons)

    def save(self, *args, **kwargs):
        self.full_clean()
        if (
            self.column_order
        ):  # todo: while column delete need to handle column_config value also.
            # Convert all values to UUID objects
            try:
                uuids = [uuid.UUID(value) for value in self.column_order]
            except ValueError:
                raise ValidationError("Invalid UUID in column_order")  # noqa: B904

            # Check which UUIDs exist in the Column table
            existing_ids = set(
                Column.objects.filter(id__in=uuids).values_list("id", flat=True)
            )

            # Filter out missing UUIDs
            original_count = len(uuids)
            uuids = [uid for uid in uuids if uid in existing_ids]
            if original_count != len(uuids):
                missing = set(self.column_order) - {str(uid) for uid in uuids}
                logger.warning(f"Ignoring missing UUIDs: {missing}")
            # Update the cleaned column_order
            self.column_order = [str(uid) for uid in uuids]

        super().save(*args, **kwargs)


def validate_source_choice(value):
    valid_choices = [choice.value for choice in SourceChoices]
    if value not in valid_choices:
        raise ValidationError(
            f"Invalid source choice. Valid choices are: {', '.join(valid_choices)}"
        )


def validate_data_type_choice(value):
    valid_choices = [choice.value for choice in DataTypeChoices]
    if value not in valid_choices:
        raise ValidationError(
            f"Invalid data type choice. Valid choices are: {', '.join(valid_choices)}"
        )


class Column(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=2000)
    data_type = models.CharField(
        max_length=50,
        choices=DataTypeChoices.get_choices(),
        validators=[validate_data_type_choice],
    )
    dataset = models.ForeignKey(
        Dataset, on_delete=models.CASCADE, null=True, blank=True
    )
    source = models.CharField(
        max_length=50,
        choices=SourceChoices.get_choices(),
        validators=[validate_source_choice],
    )
    source_id = models.CharField(max_length=2000, blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True, null=True)
    status = models.CharField(
        max_length=50,
        choices=StatusType.get_choices(),
        default=StatusType.COMPLETED.value,
    )

    class Meta(BaseModel.Meta):
        indexes = [
            models.Index(fields=["dataset"]),
        ]

    def clean(self):
        super().clean()
        validate_source_choice(self.source)
        validate_data_type_choice(self.data_type)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class Row(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE)
    order = models.IntegerField()
    metadata = models.JSONField(default=dict, blank=True, null=True)

    class Meta(BaseModel.Meta):
        indexes = [
            models.Index(fields=["dataset", "order"]),
            models.Index(
                fields=["dataset", "deleted"],
                name="row_dataset_deleted_idx",
            ),
        ]


def validate_cell_choice(value):
    valid_choices = [choice.value for choice in CellStatus]
    if value not in valid_choices:
        raise ValidationError(
            f"Invalid model type choice. Valid choices are: {', '.join(valid_choices)}"
        )


# Pydantic schema for column metadata
class ColumnMetadataSchema(pydantic.BaseModel):
    audio_duration_seconds: float = pydantic.Field(..., ge=0.0)


def validate_column_metadata(value):
    """Validator function for JSONField"""
    try:
        # Handle both dict and json dumps
        if isinstance(value, str):
            json_data = json.loads(value)
        else:
            json_data = value

        ColumnMetadataSchema(**json_data)
    except pydantic.ValidationError as e:
        raise ValidationError(f"Invalid column_metadata format: {e}")  # noqa: B904
    except json.JSONDecodeError:
        raise ValidationError("Invalid JSON format")  # noqa: B904


class Cell(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(
        Dataset, on_delete=models.CASCADE, null=True, blank=True
    )
    column = models.ForeignKey(Column, on_delete=models.CASCADE)
    row = models.ForeignKey(Row, on_delete=models.CASCADE)
    value = models.TextField(null=True, blank=True)
    value_infos = models.JSONField(default=list, blank=True, null=True)
    feedback_info = models.JSONField(
        default=dict, blank=True, null=True
    )  # description , annotation {user_id , auto_annotate , verified,label_id,annotation_id}
    status = models.CharField(
        max_length=50,
        choices=CellStatus.get_choices(),
        validators=[validate_cell_choice],
        default=CellStatus.PASS.value,
    )
    column_metadata = models.JSONField(
        default=dict, blank=True, null=True, validators=[validate_column_metadata]
    )
    prompt_tokens = models.IntegerField(null=True, blank=True)
    completion_tokens = models.IntegerField(null=True, blank=True)
    response_time = models.FloatField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        indexes = [
            models.Index(fields=["row", "column", "dataset"]),
            GinIndex(
                fields=["value"],
                opclasses=["gin_trgm_ops"],
                name="model_hub_cell_value_trgm_idx",
                condition=Q(deleted=False, status="pass", value__isnull=False)
                & ~Q(value=""),
            ),
        ]

    def clean(self):
        super().clean()
        validate_cell_choice(self.status)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


def validate_status_choices(value):
    valid_choices = [choice.value for choice in StatusType]
    if value not in valid_choices:
        raise ValidationError(
            f"Invalid status choice. Valid choices are: {', '.join(valid_choices)}"
        )


class Files(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=5000)
    status = models.CharField(
        choices=StatusType.get_choices(),
        validators=[validate_status_choices],
        default=StatusType.PROCESSING.value,
    )
    metadata = models.JSONField(default=dict, blank=True, null=True)
    updated_by = models.CharField(max_length=5000, blank=True, null=True)
    uploaded_url = models.URLField(max_length=5000)

    def __str__(self):
        return self.name


class KnowledgeBaseFile(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=5000)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="knowledge_base",
        default=1,
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        related_name="knowledge_base_files",
        null=True,
        blank=True,
    )
    status = models.CharField(
        choices=StatusType.get_choices(),
        validators=[validate_status_choices],
        default=StatusType.PROCESSING.value,
    )
    last_error = models.CharField(max_length=10000, blank=True, null=True)
    files = models.ManyToManyField(Files, related_name="knowledge_base_files")
    created_by = models.CharField(max_length=5000, blank=True, null=True)
    size = models.BigIntegerField(blank=True, null=True)

    def __str__(self):
        return self.name
