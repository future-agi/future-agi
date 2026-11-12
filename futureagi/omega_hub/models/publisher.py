"""Publishers (SPEC §2.3, §2.4, §6.5): who an entry belongs to, and the tier the server gives it."""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

from omega_hub.models.deletion import erase_publisher_owner
from tfc.utils.base_model import BaseModel


class Publisher(BaseModel):
    class Kind(models.TextChoices):
        USER = "user", "User"
        ORGANIZATION = "organization", "Organization"

    class Tier(models.TextChoices):
        OFFICIAL = "official", "Official"
        VERIFIED = "verified", "Verified publisher"
        COMMUNITY = "community", "Community reviewed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    handle = models.CharField(max_length=40, unique=True)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    # Deleting the owner tombstones the publisher (models/deletion.py, HUB-09): its entries stay.
    owner_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=erase_publisher_owner,
        related_name="omega_hub_publishers",
    )
    # Not named `organization`: tfc/utils/signals.py assigns the request's organisation to any model
    # with an `organization` attribute on save, which would turn a user publisher into an org one.
    owner_org = models.ForeignKey(
        "accounts.Organization",
        null=True,
        blank=True,
        on_delete=erase_publisher_owner,
        related_name="omega_hub_publishers",
        db_column="organization_id",
    )
    display_name = models.CharField(max_length=60)
    bio = models.CharField(max_length=160, blank=True, default="")
    website = models.URLField(max_length=300, blank=True, default="")
    github_login = models.CharField(max_length=39, blank=True, default="")
    avatar_key = models.CharField(max_length=200, blank=True, default="")
    tier = models.CharField(max_length=16, choices=Tier.choices, default=Tier.COMMUNITY)
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    verification_note = models.TextField(blank=True, default="")
    verification_request = models.TextField(null=True, blank=True)
    suspended_at = models.DateTimeField(null=True, blank=True)
    suspended_reason = models.TextField(blank=True, default="")
    first_published_at = models.DateTimeField(null=True, blank=True)
    # Set when the owning account or organisation was deleted; the owner fields are then both empty.
    erased_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "omega_hub_publisher"
        ordering = ("handle",)
        constraints = [
            models.CheckConstraint(
                name="omega_hub_publisher_one_owner",
                condition=(
                    Q(owner_user__isnull=False, owner_org__isnull=True)
                    | Q(owner_user__isnull=True, owner_org__isnull=False)
                    | Q(owner_user__isnull=True, owner_org__isnull=True, erased_at__isnull=False)
                ),
            ),
            models.UniqueConstraint(
                fields=["owner_user"],
                condition=Q(kind="user"),
                name="omega_hub_one_user_publisher_per_user",
            ),
        ]

    def __str__(self):
        return self.handle

    @property
    def is_erased(self) -> bool:
        return self.erased_at is not None

    @property
    def is_suspended(self) -> bool:
        return self.suspended_at is not None
