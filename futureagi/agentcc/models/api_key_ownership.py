"""Persistence rules for a key's person and tenant binding.

The mutable ``owner`` string is only a display label. The three FKs below
are authority, fixed for the lifetime of an existing key.
"""

from django.core.exceptions import ValidationError
from django.db import models

OWNERSHIP_FIELDS = ("user", "organization", "workspace")
OWNERSHIP_INPUTS = frozenset(
    name for field in OWNERSHIP_FIELDS for name in (field, f"{field}_id")
)
IMMUTABLE_MESSAGE = "API key ownership is immutable; create a new personal key instead."


def reject_ownership_fields(fields):
    refused = OWNERSHIP_INPUTS.intersection(fields)
    if refused:
        raise ValidationError(dict.fromkeys(sorted(refused), IMMUTABLE_MESSAGE))


class APIKeyQuerySet(models.QuerySet):
    """Bulk writes bypass Model.save, so refuse authority columns up front."""

    def update(self, **kwargs):
        reject_ownership_fields(kwargs)
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, batch_size=None):
        fields = tuple(fields)
        reject_ownership_fields(fields)
        return super().bulk_update(objs, fields, batch_size=batch_size)

    def bulk_create(
        self,
        objs,
        batch_size=None,
        ignore_conflicts=False,
        update_conflicts=False,
        update_fields=None,
        unique_fields=None,
    ):
        if update_fields is not None:
            update_fields = tuple(update_fields)
        if update_conflicts:
            reject_ownership_fields(update_fields or ())
        return super().bulk_create(
            objs,
            batch_size=batch_size,
            ignore_conflicts=ignore_conflicts,
            update_conflicts=update_conflicts,
            update_fields=update_fields,
            unique_fields=unique_fields,
        )


def unlink_deleted_key_user(collector, field, sub_objs, using):
    """Keep SET_NULL's deletion semantics, not a general reassignment escape.

    The accounts User pre_delete receiver revokes personal keys first. Hand
    the Collector concrete rows so it uses its internal field update, rather
    than the application QuerySet.update that forbids ownership writes.
    Surviving unlinked rows remain immutable (including NULL -> another user).
    """
    collector.add_field_update(field, None, list(sub_objs))
