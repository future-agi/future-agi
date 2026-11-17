from django.core.exceptions import (
    FieldDoesNotExist,
    ObjectDoesNotExist,
    ValidationError,
)
from django.db import models
from django.utils import timezone

from tfc.middleware.workspace_context import (
    get_current_organization,
    get_current_user,
    get_current_workspace,
    workspace_scope_is_empty,
)
from tfc.utils.workspace_scope import model_has_workspace_data


def _has_db_field(model, field_name):
    """Check if model has an actual DB field (not just a @property)."""
    try:
        model._meta.get_field(field_name)
        return True
    except FieldDoesNotExist:
        return False


def _is_insert_save(instance, *, force_update=False, update_fields=None):
    """Share INSERT eligibility without treating reconstructed UPDATEs as new."""
    return (
        instance._state.adding
        and not force_update
        and update_fields is None
        and not getattr(instance, "_workspace_autoassign_force_update", False)
    )


def _same_organization(workspace, instance):
    """Compare the two related organizations in one key namespace.

    A fresh row may carry organization_id as a string while the loaded
    workspace carries the typed pk (42 vs "42", UUID vs its string); both name
    the same organization. Values are compared in the instance relation's own
    target-field namespace: when either relation targets a non-pk field, the
    workspace organization's value for that field is read instead of its pk.
    Relations to a different model and unresolvable values never match.
    """
    try:
        right = instance.organization_id
        try:
            field = instance._meta.get_field("organization")
        except FieldDoesNotExist:
            return str(workspace.organization_id) == str(right)
        target = field.target_field
        workspace_field = workspace._meta.get_field("organization")
        if field.related_model is not workspace_field.related_model:
            return False
        if target.primary_key and workspace_field.target_field.primary_key:
            left = workspace.organization_id
        else:
            left = getattr(workspace.organization, target.attname)
        return target.to_python(left) == target.to_python(right)
    except (
        AttributeError,
        FieldDoesNotExist,
        ObjectDoesNotExist,
        TypeError,
        ValueError,
        ValidationError,
    ):
        return False


def _can_assign_workspace(instance, workspace):
    """Shared workspace policy for BaseModel.save and the save signals."""
    if getattr(instance, "disable_auto_workspace_assignment", False):
        return False
    if getattr(instance._meta, "label_lower", "") == "usage.usageeventlog":
        return False
    if not workspace:
        return False
    if not hasattr(instance, "organization_id"):
        return True
    if not instance.organization_id:
        return True
    return _same_organization(workspace, instance)


class WorkspaceAutoAssignMixin:
    """
    Mixin to automatically assign workspace from workspace context.
    Use this for models that don't inherit from BaseModel.
    """

    def save(self, *args, **kwargs):
        # Auto-assign workspace if workspace context is set and model has workspace field
        current_workspace = get_current_workspace()
        if (
            current_workspace
            and hasattr(self, "workspace")
            and not self.pk  # Only for new instances
            and (not hasattr(self, "workspace_id") or not self.workspace_id)
        ):  # Only if workspace is not already set
            self.workspace = current_workspace

        super().save(*args, **kwargs)


class BaseModelManager(models.Manager):
    def get_queryset(self):
        queryset = super().get_queryset()

        # Get the model class for field checking
        model = self.model

        # A denied landing is not a background query. Include child tables
        # whose workspace ownership is through a Dataset, Project or other FK.
        if workspace_scope_is_empty() and model_has_workspace_data(model):
            return queryset.none()

        # Apply workspace filtering if workspace context is set
        current_workspace = get_current_workspace()
        if current_workspace:
            # Filter by workspace if the model has workspace field
            if _has_db_field(model, "workspace"):
                # Check if current workspace is the default workspace
                if getattr(current_workspace, "is_default", False):
                    # If current workspace is default, fetch records with either:
                    # 1. Current workspace
                    # 2. Default workspace within same org (for backward compatibility)
                    # 3. Null workspace (empty workspace filter)
                    q = models.Q(workspace=current_workspace) | models.Q(
                        workspace__is_default=True,
                        workspace__organization_id=current_workspace.organization_id,
                    )
                    if _has_db_field(model, "organization_id"):
                        q |= models.Q(
                            workspace__isnull=True,
                            organization_id=current_workspace.organization_id,
                        )
                    queryset = queryset.filter(q)
                else:
                    # If current workspace is not default, only fetch records with current workspace
                    queryset = queryset.filter(workspace=current_workspace)

        # Apply soft delete filter
        return queryset.filter(deleted=False)


class AllObjectsManager(models.Manager):
    def get_queryset(self):
        queryset = super().get_queryset()

        # Get the model class for field checking
        model = self.model

        # A denied landing is not a background query. Include child tables
        # whose workspace ownership is through a Dataset, Project or other FK.
        if workspace_scope_is_empty() and model_has_workspace_data(model):
            return queryset.none()

        # Apply workspace filtering if workspace context is set
        current_workspace = get_current_workspace()
        if current_workspace:
            # Filter by workspace if the model has workspace field
            if _has_db_field(model, "workspace"):
                # Check if current workspace is the default workspace
                if getattr(current_workspace, "is_default", False):
                    q = models.Q(workspace=current_workspace) | models.Q(
                        workspace__is_default=True,
                        workspace__organization_id=current_workspace.organization_id,
                    )
                    if _has_db_field(model, "organization_id"):
                        q |= models.Q(
                            workspace__isnull=True,
                            organization_id=current_workspace.organization_id,
                        )
                    queryset = queryset.filter(q)
                else:
                    # If current workspace is not default, only fetch records with current workspace
                    queryset = queryset.filter(workspace=current_workspace)

        # Note: No soft delete filter for all_objects
        return queryset


class NoWorkspaceFilterManager(models.Manager):
    def get_queryset(self):
        queryset = super().get_queryset()
        # Bypassing one selected workspace is not permission to bypass a denied
        # authenticated scope. Background jobs retain their unscoped semantics.
        if workspace_scope_is_empty() and model_has_workspace_data(self.model):
            return queryset.none()
        return queryset.filter(deleted=False)


class BaseModel(models.Model):
    """
    Inherit this class when you define a model.
    So no need to specify primary key, added_on, updated_on and deleted in any model class.
    """

    # Always add current timestamp when created.
    created_at = models.DateTimeField(auto_now_add=True)

    # Always update current timestamp when model is updated.
    updated_at = models.DateTimeField(auto_now=True)

    # Flag to mark the model deleted.
    deleted = models.BooleanField(default=False, db_index=True)

    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = BaseModelManager()
    all_objects = AllObjectsManager()
    no_workspace_objects = NoWorkspaceFilterManager()

    class Meta:
        abstract = True  # Django will not create the table if true for this base class.

        # Default order latest first.
        ordering = ("-created_at",)

    def save(self, *args, **kwargs):
        # Django's positional save arguments are force_insert, force_update,
        # using, update_fields. Preserve update-only intent in either spelling.
        insert_save = _is_insert_save(
            self,
            force_update=kwargs.get(
                "force_update", args[1] if len(args) > 1 else False
            ),
            update_fields=kwargs.get(
                "update_fields", args[3] if len(args) > 3 else None
            ),
        )
        # Auto-assign workspace if workspace context is set and model has workspace field
        current_workspace = get_current_workspace()
        if (
            current_workspace
            and hasattr(self, "workspace")
            and insert_save
            and (not hasattr(self, "workspace_id") or not self.workspace_id)
        ):  # Only if workspace is not already set
            if _can_assign_workspace(self, current_workspace):
                self.workspace = current_workspace
            elif getattr(self, "organization_id", None) and not _same_organization(
                current_workspace, self
            ):
                # Log warning but don't fail on an organization mismatch.
                import logging

                logger = logging.getLogger(__name__)
                logger.warning(
                    f"Workspace {current_workspace.id} does not belong to "
                    f"organization {self.organization_id}. Skipping auto-assignment."
                )

        # Auto-assign organization if organization context is set and model has organization field
        current_organization = get_current_organization()
        if (
            current_organization
            and hasattr(self, "organization")
            and insert_save
            and (not hasattr(self, "organization_id") or not self.organization_id)
        ):  # Only if organization is not already set
            self.organization = current_organization

        super().save(*args, **kwargs)

    def save_base(
        self,
        raw=False,
        force_insert=False,
        force_update=False,
        using=None,
        update_fields=None,
    ):
        # Django pre_save includes update_fields but not force_update. Carry
        # that intent through the real signal dispatch, including callers
        # that bypass BaseModel.save and call Model.save/save_base directly.
        attr = "_workspace_autoassign_force_update"
        missing = object()
        previous = self.__dict__.get(attr, missing)
        self.__dict__[attr] = force_update or (previous is not missing and previous)
        try:
            return super().save_base(
                raw=raw,
                force_insert=force_insert,
                force_update=force_update,
                using=using,
                update_fields=update_fields,
            )
        finally:
            if previous is missing:
                self.__dict__.pop(attr, None)
            else:
                self.__dict__[attr] = previous

    def delete(self, using=None, keep_parents=False):
        self.deleted = True
        self.deleted_at = timezone.now()
        self.save()

    @classmethod
    def get_current_context(cls):
        """Get the current workspace and organization context from ContextVars"""
        context = {
            "workspace": get_current_workspace(),
            "organization": get_current_organization(),
            "user": get_current_user(),
        }
        return context

    @classmethod
    def log_context(cls):
        """Log the current context for debugging"""
        context = cls.get_current_context()
        import logging

        logger = logging.getLogger(__name__)
        logger.info(f"Current context for {cls.__name__}: {context}")
        return context

    @classmethod
    def create_with_context(cls, **kwargs):
        """Create an instance with automatic workspace and organization assignment"""
        # Auto-assign workspace if workspace context is set and model has workspace field
        current_workspace = get_current_workspace()
        if (
            current_workspace
            and hasattr(cls, "workspace")
            and "workspace" not in kwargs
            and not kwargs.get("workspace_id")
        ):
            kwargs["workspace"] = current_workspace

        # Auto-assign organization if organization context is set and model has organization field
        current_organization = get_current_organization()
        if (
            current_organization
            and hasattr(cls, "organization")
            and "organization" not in kwargs
            and not kwargs.get("organization_id")
        ):
            kwargs["organization"] = current_organization

        return cls.objects.create(**kwargs)

    @classmethod
    def get_or_create_with_context(cls, defaults=None, **kwargs):
        """Get or create an instance with automatic workspace and organization assignment"""
        if defaults is None:
            defaults = {}

        # Auto-assign workspace if workspace context is set and model has workspace field
        current_workspace = get_current_workspace()
        if (
            current_workspace
            and hasattr(cls, "workspace")
            and "workspace" not in kwargs
            and "workspace" not in defaults
            and not kwargs.get("workspace_id")
        ):
            defaults["workspace"] = current_workspace

        # Auto-assign organization if organization context is set and model has organization field
        current_organization = get_current_organization()
        if (
            current_organization
            and hasattr(cls, "organization")
            and "organization" not in kwargs
            and "organization" not in defaults
            and not kwargs.get("organization_id")
        ):
            defaults["organization"] = current_organization

        return cls.objects.get_or_create(defaults=defaults, **kwargs)

    def ensure_workspace_and_organization(self):
        """Ensure workspace and organization are set on this instance"""
        # Auto-assign workspace if workspace context is set and model has workspace field
        current_workspace = get_current_workspace()
        if current_workspace and hasattr(self, "workspace") and not self.workspace_id:
            self.workspace = current_workspace

        # Auto-assign organization if organization context is set and model has organization field
        current_organization = get_current_organization()
        if (
            current_organization
            and hasattr(self, "organization")
            and not self.organization_id
        ):
            self.organization = current_organization

        # Save if any changes were made
        if self.pk and (hasattr(self, "_state") and not self._state.adding):
            self.save(update_fields=["workspace", "organization"])


class Deprecated:
    """
    A wrapper class to mark any Django model field as deprecated.

    Usage:
        class MyModel(models.Model):
            # Basic usage
            old_field = Deprecated(models.CharField(max_length=100))

            # With custom message
            legacy_field = Deprecated(
                models.IntegerField(),
                message="Use new_field instead. Will be removed in v2.0"
            )

            # With custom warning category
            ancient_field = Deprecated(
                models.TextField(),
                category=PendingDeprecationWarning
            )
    """

    def __init__(self, field, new_field="", message=None, category=DeprecationWarning):
        import warnings

        """
        Initialize a deprecated field wrapper.

        Args:
            field: The Django model field to wrap
            message: Custom deprecation message (optional)
            category: Warning category to use (default: DeprecationWarning)
        """
        self.field = field
        self.message = message or (
            f"{field.__class__.__name__} is deprecated and will be removed in a future version. Use {new_field}"
        )
        self.category = category

        warnings.warn(self.message, self.category, stacklevel=3)

    def __set_name__(self, owner, name):
        """Called when the field is assigned to a class attribute."""
        self.field.set_attributes_from_name(name)

    def contribute_to_class(self, cls, name):
        """
        Hook into Django's model metaclass to properly register the field.
        This method is called by Django when building the model class.
        """
        self.field.contribute_to_class(cls, name)

    def __getattr__(self, name):
        """
        Proxy all attribute access to the wrapped field.
        This ensures the deprecated field behaves exactly like the original.
        """
        return getattr(self.field, name)
