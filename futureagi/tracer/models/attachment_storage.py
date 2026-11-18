"""Immutable private resource identity and embedded reservation values.

A binding is neither credentials nor authorization. Old owners never consult the
current default. Nullable value columns support provenance-fenced historical
rows; the switch migration requires complete values on every new owner.
"""

import hashlib
import uuid
from datetime import UTC

from django.db import models

BINDING_IDENTITY_FIELDS = (
    "provider",
    "endpoint",
    "account_identity",
    "region",
    "quarantine_bucket",
    "quarantine_resource_identity",
    "quarantine_owner_identity",
    "quarantine_created_at",
    "quarantine_encryption_mode",
    "quarantine_encryption_key_identity",
    "serving_bucket",
    "serving_resource_identity",
    "serving_owner_identity",
    "serving_created_at",
    "serving_encryption_mode",
    "serving_encryption_key_identity",
)
RESERVATION_REQUIRED_FIELDS = (
    "reservation_id",
    "reservation_created_at",
    "reservation_expires_at",
    "reservation_pool_key",
    "reservation_reserved_bytes",
    "reservation_decoded_bytes",
    "reservation_object_count",
    "reservation_part_count",
    "reservation_charge_bytes",
    "reservation_transfers",
    "reservation_validation_jobs",
    "reservation_recordings",
    "reservation_objects",
    "reservation_links",
)


def all_or_none(*fields):
    """A scalar value group is absent or complete; never a partial receipt."""
    return models.Q(**{f + "__isnull": True for f in fields}) | models.Q(
        **{f + "__isnull": False for f in fields}
    )


def binding_identity_sha256(values):
    """Canonical digest, not equality or authorization: also compare the tuple.

    In field order, join UTF-8 byte-length-prefixed values with LF. Incarnation
    times use UTC with six fractional digits and Z. Reject embedded CR/LF.
    """
    parts = []
    for field in BINDING_IDENTITY_FIELDS:
        value = values[field]
        if field.endswith("created_at"):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("binding incarnation must be timezone-aware")
            value = (
                value.astimezone(UTC)
                .isoformat(timespec="microseconds")
                .replace("+00:00", "Z")
            )
        if not isinstance(value, str) or "\n" in value or "\r" in value:
            raise ValueError("binding identity must be a single-line string")
        parts.append(str(len(value.encode("utf-8"))) + ":" + value)
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


class AttachmentStorageBinding(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.CharField(
        max_length=8, choices=[("s3", "S3"), ("gcs", "GCS"), ("minio", "MinIO")]
    )
    endpoint = models.CharField(max_length=512)
    account_identity = models.CharField(max_length=255)
    region = models.CharField(max_length=64)
    quarantine_bucket = models.CharField(max_length=255)
    quarantine_resource_identity = models.CharField(max_length=512)
    quarantine_owner_identity = models.CharField(max_length=255)
    quarantine_created_at = models.DateTimeField()
    quarantine_encryption_mode = models.CharField(max_length=32)
    quarantine_encryption_key_identity = models.CharField(max_length=512, blank=True)
    serving_bucket = models.CharField(max_length=255)
    serving_resource_identity = models.CharField(max_length=512)
    serving_owner_identity = models.CharField(max_length=255)
    serving_created_at = models.DateTimeField()
    serving_encryption_mode = models.CharField(max_length=32)
    serving_encryption_key_identity = models.CharField(max_length=512, blank=True)
    identity_sha256 = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(provider__in=["s3", "gcs", "minio"]),
                name="att_binding_provider",
            ),
            models.CheckConstraint(
                condition=~models.Q(
                    quarantine_resource_identity=models.F("serving_resource_identity")
                ),
                name="att_binding_distinct_resources",
            ),
            models.CheckConstraint(
                condition=~models.Q(quarantine_bucket=models.F("serving_bucket")),
                name="att_binding_distinct_buckets",
            ),
        ]


class AttachmentReservationFields(models.Model):
    """One capacity ledger embedded in each owner; not another table.

    Identity/time/pool are immutable. The live vector is preserved after the
    one-way settled stamp, but settled vectors do not contribute to Budget.
    Services acquire ordered capacity locks before changing a live vector.
    """

    reservation_id = models.UUIDField(null=True, unique=True)
    reservation_created_at = models.DateTimeField(null=True)
    reservation_expires_at = models.DateTimeField(null=True)
    reservation_pool_key = models.CharField(max_length=80, null=True)
    reservation_reserved_bytes = models.PositiveBigIntegerField(null=True)
    reservation_decoded_bytes = models.PositiveBigIntegerField(null=True)
    reservation_object_count = models.PositiveIntegerField(null=True)
    reservation_part_count = models.PositiveIntegerField(null=True)
    reservation_charge_bytes = models.PositiveBigIntegerField(null=True)
    reservation_transfers = models.PositiveIntegerField(null=True)
    reservation_validation_jobs = models.PositiveIntegerField(null=True)
    reservation_recordings = models.PositiveIntegerField(null=True)
    reservation_objects = models.PositiveIntegerField(null=True)
    reservation_links = models.PositiveIntegerField(null=True)
    reservation_settled_at = models.DateTimeField(null=True)

    class Meta:
        abstract = True
