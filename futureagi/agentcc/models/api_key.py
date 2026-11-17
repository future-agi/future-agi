import uuid

from django.core.exceptions import ValidationError
from django.db import models, router

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from agentcc.models.api_key_ownership import (
    IMMUTABLE_MESSAGE,
    OWNERSHIP_FIELDS,
    APIKeyQuerySet,
    unlink_deleted_key_user,
)
from tfc.utils.base_model import (
    AllObjectsManager,
    BaseModel,
    BaseModelManager,
    NoWorkspaceFilterManager,
)


class AgentccAPIKey(BaseModel):
    # Personal keys are org-wide; a browsing workspace must never re-own them.
    # This also covers the full save performed by BaseModel's soft delete.
    disable_auto_workspace_assignment = True

    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"
    PENDING = "pending"
    FAILED = "failed"

    STATUS_CHOICES = [
        (ACTIVE, "Active"),
        (REVOKED, "Revoked"),
        (EXPIRED, "Expired"),
        (PENDING, "Pending"),
        (FAILED, "Failed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="agentcc_api_keys",
        blank=False,
        null=False,
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="agentcc_api_keys",
        null=True,
        blank=True,
    )
    project = models.ForeignKey(
        "prism.AgentccProject",
        on_delete=models.SET_NULL,
        related_name="api_keys",
        null=True,
        blank=True,
    )
    user = models.ForeignKey(
        User,
        on_delete=unlink_deleted_key_user,
        related_name="agentcc_api_keys",
        null=True,
        blank=True,
    )
    gateway_key_id = models.CharField(max_length=255)
    key_prefix = models.CharField(max_length=20, blank=True, default="")
    key_hash = models.CharField(max_length=64, blank=True, default="")
    name = models.CharField(max_length=255)
    owner = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=ACTIVE,
    )
    allowed_models = models.JSONField(default=list, blank=True)
    allowed_providers = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    objects = BaseModelManager.from_queryset(APIKeyQuerySet)()
    all_objects = AllObjectsManager.from_queryset(APIKeyQuerySet)()
    no_workspace_objects = NoWorkspaceFilterManager.from_queryset(APIKeyQuerySet)()
    unscoped_objects = models.Manager.from_queryset(APIKeyQuerySet)()

    class Meta:
        base_manager_name = "unscoped_objects"
        db_table = "agentcc_api_key"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["gateway_key_id"],
                condition=models.Q(deleted=False),
                name="unique_agentcc_api_key_id",
            ),
        ]
        indexes = [
            models.Index(fields=["organization"]),
            models.Index(fields=["gateway_key_id"]),
            models.Index(fields=["key_hash"]),
            models.Index(fields=["status"]),
        ]

    def assert_ownership_unchanged(self, using=None, fields=None):
        """Compare against storage, including deleted rows and explicit-PK saves.

        _state.adding alone does not prove creation: Django can UPDATE a row
        when a new instance is constructed with an existing primary key.
        """
        selected = [
            field
            for field in OWNERSHIP_FIELDS
            if fields is None or field in fields or f"{field}_id" in fields
        ]
        if not selected:
            return
        using = using or router.db_for_write(type(self), instance=self)
        names = tuple(f"{field}_id" for field in selected)
        stored = (
            type(self)
            ._base_manager.using(using)
            .filter(pk=self.pk)
            .values(*names)
            .first()
        )
        if stored is not None:
            changed = {
                field: IMMUTABLE_MESSAGE
                for field, name in zip(selected, names, strict=True)
                if str(getattr(self, name)) != str(stored[name])
            }
            if changed:
                raise ValidationError(changed)

    def save_base(
        self,
        raw=False,
        force_insert=False,
        force_update=False,
        using=None,
        update_fields=None,
    ):
        self.assert_ownership_unchanged(using, fields=update_fields)
        return super().save_base(
            raw=raw,
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )

    def _do_update(self, base_qs, using, pk_val, values, update_fields, forced_update):
        # This is also reached by save_base and update_or_create. Checking here
        # covers both normal saves and an explicit PK on a new instance.
        self.assert_ownership_unchanged(
            using, fields={value[0].name for value in values}
        )
        # Never include authority columns in an UPDATE, even unchanged ones.
        # A concurrent person deletion must not be undone by a stale full save.
        values = [value for value in values if value[0].name not in OWNERSHIP_FIELDS]
        return super()._do_update(
            base_qs, using, pk_val, values, update_fields, forced_update
        )

    def __str__(self):
        return f"{self.name} ({self.key_prefix}...)"
