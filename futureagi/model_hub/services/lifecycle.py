"""Bulk soft-delete lifecycle helpers for ``BaseModel`` querysets."""

from datetime import datetime

from django.db.models import QuerySet
from django.utils import timezone


def bulk_soft_delete(queryset: QuerySet, *, now: datetime | None = None) -> int:
    """Soft-delete a queryset while advancing all lifecycle timestamps."""
    changed_at = now or timezone.now()
    return queryset.update(
        deleted=True,
        deleted_at=changed_at,
        updated_at=changed_at,
    )


def bulk_restore(queryset: QuerySet, *, now: datetime | None = None) -> int:
    """Restore a queryset while advancing its lifecycle timestamp."""
    changed_at = now or timezone.now()
    return queryset.update(
        deleted=False,
        deleted_at=None,
        updated_at=changed_at,
    )
