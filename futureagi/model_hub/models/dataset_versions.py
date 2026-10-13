"""Immutable dataset version projections used by evaluation runs.

The existing ``Dataset -> Row -> Cell`` tables remain the source of truth while
the new run contract is rolled out.  These two small models give callers a
stable version and example identity without copying the cell table.
"""

import uuid

from django.core.exceptions import ValidationError
from django.db import models

from accounts.models.organization import Organization
from accounts.models.user import User
from model_hub.models.develop_dataset import Dataset, Row
from tfc.utils.base_model import BaseModel


class DatasetVersion(BaseModel):
    """An immutable view of a dataset at a point in time.

    Version records are append-only by contract.  The model deliberately does
    not duplicate ``Cell`` records; ``DatasetVersionItem.legacy_row`` provides
    the compatibility projection while migrations are in progress.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(
        Dataset,
        on_delete=models.CASCADE,
        related_name="dataset_versions",
    )
    parent_version = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="child_versions",
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dataset_versions_created",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="dataset_versions",
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="dataset_versions",
    )
    content_hash = models.CharField(max_length=64, blank=True, default="")
    description = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    source_revision = models.CharField(max_length=255, blank=True, default="")

    class Meta(BaseModel.Meta):
        db_table = "model_hub_dataset_version"
        indexes = [
            models.Index(fields=["dataset", "created_at"]),
            models.Index(fields=["organization", "workspace"]),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.dataset_id:
            dataset = self.dataset
            if self.organization_id != dataset.organization_id:
                errors["organization"] = (
                    "Dataset version organization must match its dataset."
                )
            if self.workspace_id != dataset.workspace_id:
                errors["workspace"] = (
                    "Dataset version workspace must match its dataset."
                )
        if self.parent_version_id and self.parent_version:
            if self.parent_version.dataset_id != self.dataset_id:
                errors["parent_version"] = (
                    "Parent version must belong to the same dataset."
                )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        # Keep tenant fields aligned when callers construct a version from a
        # dataset.  They remain explicit columns so normal workspace scoping
        # works without joining through Dataset on every query.
        if self.dataset_id:
            if self.organization_id is None:
                self.organization_id = self.dataset.organization_id
            if self.workspace_id is None:
                self.workspace_id = self.dataset.workspace_id
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.dataset.name} version {self.pk}"


class DatasetVersionItem(BaseModel):
    """One stable example in a :class:`DatasetVersion`.

    ``example_id`` is intentionally separate from the row primary key.  The
    same example ID can therefore be copied into a later version while old
    version items remain immutable.  ``legacy_row`` is nullable because future
    imports may not originate from the current row/cell tables.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset_version = models.ForeignKey(
        DatasetVersion,
        on_delete=models.CASCADE,
        related_name="items",
    )
    example_id = models.UUIDField(default=uuid.uuid4, db_index=True)
    external_id = models.CharField(max_length=500, null=True, blank=True)
    input = models.JSONField(default=dict, blank=True)
    expected = models.JSONField(null=True, blank=True, default=None)
    metadata = models.JSONField(default=dict, blank=True)
    source_trace_id = models.CharField(max_length=255, blank=True, default="")
    source_span_id = models.CharField(max_length=255, blank=True, default="")
    content_hash = models.CharField(max_length=64, blank=True, default="")
    legacy_row = models.ForeignKey(
        Row,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dataset_version_items",
    )

    class Meta(BaseModel.Meta):
        db_table = "model_hub_dataset_version_item"
        constraints = [
            models.UniqueConstraint(
                fields=["dataset_version", "example_id"],
                condition=models.Q(deleted=False),
                name="unique_dataset_version_example",
            ),
            models.UniqueConstraint(
                fields=["dataset_version", "external_id"],
                condition=models.Q(deleted=False, external_id__isnull=False)
                & ~models.Q(external_id=""),
                name="unique_dataset_version_external_id",
            ),
        ]
        indexes = [
            models.Index(fields=["dataset_version", "external_id"]),
            models.Index(fields=["dataset_version", "legacy_row"]),
        ]

    @staticmethod
    def example_id_for_legacy_row(row_id):
        """Return a deterministic ID for a legacy row projection.

        This lets backfills preserve identity across versions without changing
        the legacy ``Row`` schema.  New imports should generate and persist a
        UUID instead.
        """

        return uuid.uuid5(uuid.NAMESPACE_URL, f"futureagi:dataset-row:{row_id}")

    @staticmethod
    def calculate_content_hash(*, input, expected, metadata):
        from model_hub.services.dataset_version_service import canonical_content_hash

        return canonical_content_hash(
            input=input,
            expected=expected,
            metadata=metadata,
        )

    def save(self, *args, **kwargs):
        if not self.content_hash:
            self.content_hash = self.calculate_content_hash(
                input=self.input,
                expected=self.expected,
                metadata=self.metadata,
            )
        self.full_clean()
        return super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.legacy_row_id and self.legacy_row:
            if self.legacy_row.dataset_id != self.dataset_version.dataset_id:
                raise ValidationError(
                    {"legacy_row": "Legacy row must belong to the version's dataset."}
                )

    def __str__(self):
        return f"{self.dataset_version_id}:{self.example_id}"
