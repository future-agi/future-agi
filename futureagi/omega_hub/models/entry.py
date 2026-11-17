"""Entries and their approved versions (SPEC §8). `Version` is the only source for public reads."""

import uuid

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVector
from django.db import models

from omega_hub import semver
from tfc.utils.base_model import BaseModel


class Entry(BaseModel):
    class Status(models.TextChoices):
        LISTED = "listed", "Listed"
        REMOVED = "removed", "Removed"
        UNLISTED = "unlisted", "Unlisted"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    publisher = models.ForeignKey(
        "omega_hub.Publisher", on_delete=models.PROTECT, related_name="entries"
    )
    name = models.CharField(max_length=40)
    kind = models.CharField(max_length=16)
    latest_version = models.ForeignKey(
        "omega_hub.Version",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.LISTED)
    removed_category = models.CharField(max_length=40, blank=True, default="")
    removed_note = models.TextField(blank=True, default="")
    removed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    removed_at = models.DateTimeField(null=True, blank=True)
    ranked = models.BooleanField(default=True)
    unranked_reason = models.CharField(max_length=40, blank=True, default="")
    unranked_note = models.TextField(blank=True, default="")
    unranked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    unranked_at = models.DateTimeField(null=True, blank=True)
    like_count = models.PositiveIntegerField(default=0)
    search_text = models.TextField(blank=True, default="")
    vendors = ArrayField(models.CharField(max_length=40), default=list, blank=True)
    tags = ArrayField(models.CharField(max_length=40), default=list, blank=True)
    no_key_needed = models.BooleanField(default=False)
    first_published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "omega_hub_entry"
        ordering = ("-first_published_at",)
        constraints = [
            models.UniqueConstraint(fields=["publisher", "name"], name="omega_hub_entry_unique_ref")
        ]
        indexes = [
            GinIndex(SearchVector("search_text", config="english"), name="omega_hub_entry_fts"),
            models.Index(fields=["status", "first_published_at"], name="omega_hub_entry_status_idx"),
            models.Index(fields=["ranked", "status"], name="omega_hub_entry_ranked_idx"),
        ]

    def __str__(self):
        return self.ref

    @property
    def ref(self) -> str:
        return f"{self.publisher.handle}/{self.name}"


class Version(BaseModel):
    class Status(models.TextChoices):
        APPROVED = "approved", "Approved"
        YANKED = "yanked", "Yanked"
        REMOVED = "removed", "Removed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entry = models.ForeignKey("omega_hub.Entry", on_delete=models.PROTECT, related_name="versions")
    version = models.CharField(max_length=32)
    semver_key = models.CharField(max_length=32)
    manifest = models.JSONField()
    readme = models.TextField(blank=True, default="")
    files = models.JSONField(default=list)
    digest = models.CharField(max_length=80)
    bundle_key = models.CharField(max_length=200)
    bundle_sha256 = models.CharField(max_length=64)
    bundle_bytes = models.PositiveIntegerField()
    cast_key = models.CharField(max_length=200, null=True, blank=True)
    capabilities = models.JSONField(default=dict)
    check_summary = models.JSONField(default=dict)
    disclosure = models.JSONField()
    disclosure_draft = models.JSONField(null=True, blank=True)
    rules_version = models.PositiveIntegerField()
    source_commit = models.CharField(max_length=40, null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    remix_of = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="remixes"
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.APPROVED)
    yanked_reason = models.TextField(blank=True, default="")
    yanked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    yanked_at = models.DateTimeField(null=True, blank=True)
    submission = models.OneToOneField(
        "omega_hub.Submission", on_delete=models.PROTECT, related_name="published_version"
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    approved_at = models.DateTimeField()
    # The GOVERNANCE retain-until date the last renewal set on every retained version of bundle_key
    # (services/bundle_locks.py). Null until the first renewal; the check-time put already locks the
    # bundle for 90 days, and the daily retention run keeps a live entry's bundle locked from then on.
    bundle_locked_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "omega_hub_version"
        ordering = ("-semver_key",)
        constraints = [
            models.UniqueConstraint(fields=["entry", "version"], name="omega_hub_version_unique"),
            # Strict SemVer (services/refs.py). Added NOT VALID by migration 0002, so rows stored before
            # it stay readable and only new rows are held to it; `system_checks.legacy_versions` lists the old ones.
            models.CheckConstraint(condition=models.Q(version__regex=semver.SQL_REGEX),
                                   name="omega_hub_version_strict_semver"),
        ]
        indexes = [models.Index(fields=["digest"], name="omega_hub_version_digest_idx")]

    def __str__(self):
        return f"{self.entry_id}@{self.version}"
