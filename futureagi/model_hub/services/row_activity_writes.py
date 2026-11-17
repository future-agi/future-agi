"""Bind a trusted actor to row/cell SQL without per-record audit queries.

Statement-level triggers do the recording, including bulk operations, conflict
handling and raw SQL. This layer only supplies identity from the authenticated
workspace context. Without a user, history explicitly says System. Settings
are transaction-local and restored before returning a pooled connection.
"""

import json
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

from django.db import connections, models, router, transaction

from tfc.middleware.workspace_context import get_current_user
from tfc.utils.base_model import (
    AllObjectsManager,
    BaseModelManager,
    NoWorkspaceFilterManager,
)

_actor_override = ContextVar("row_activity_actor", default=None)
_active_write = ContextVar("row_activity_active_write", default=None)
_SETTING = "futureagi.row_activity_actor"


@contextmanager
def row_activity_actor(user, *, origin="write"):
    """Internal job provenance; does not change authorization or workspace scope."""
    token = _actor_override.set((user, origin))
    try:
        yield
    finally:
        _actor_override.reset(token)


def _actor_json():
    override = _actor_override.get()
    user, origin = override if override is not None else (get_current_user(), "write")
    authenticated = user is not None and getattr(user, "is_authenticated", False)
    return json.dumps({
        "id": str(user.pk) if authenticated else None,
        "name": (str(getattr(user, "name", "") or "Member")[:255]
                 if authenticated else "System"),
        "origin": origin,
    })


@contextmanager
def row_activity_write(using):
    connection = connections[using]
    actor = _actor_json()
    active = (using, actor)
    if _active_write.get() == active and connection.in_atomic_block:
        # bulk_update calls update for each SQL batch; bind identity just once.
        yield
        return
    with transaction.atomic(using=using):
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting(%s, true)", [_SETTING])
            previous = cursor.fetchone()[0] or ""
            cursor.execute("SELECT set_config(%s, %s, true)", [_SETTING, actor])
        token = _active_write.set(active)
        try:
            yield
            # On a database error the atomic block rolls back the local setting;
            # do not issue a reset query into an aborted transaction.
            if not connection.needs_rollback:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT set_config(%s, %s, true)", [_SETTING, previous])
        finally:
            _active_write.reset(token)


class RowActivityQuerySet(models.QuerySet):
    def _write_database(self):
        return self._db or router.db_for_write(self.model, **self._hints)

    def update(self, **kwargs):
        with row_activity_write(self._write_database()):
            return super().update(**kwargs)

    def bulk_create(self, *args, **kwargs):
        with row_activity_write(self._write_database()):
            return super().bulk_create(*args, **kwargs)

    def bulk_update(self, *args, **kwargs):
        with row_activity_write(self._write_database()):
            return super().bulk_update(*args, **kwargs)

    def delete(self):
        with row_activity_write(self._write_database()):
            return super().delete()

    update.alters_data = True
    bulk_create.alters_data = True
    bulk_update.alters_data = True
    delete.alters_data = True
    delete.queryset_only = True


# Preserve every existing manager's workspace/soft-delete policy.
ActivityObjectsManager = BaseModelManager.from_queryset(RowActivityQuerySet)
ActivityAllObjectsManager = AllObjectsManager.from_queryset(RowActivityQuerySet)
ActivityNoWorkspaceManager = NoWorkspaceFilterManager.from_queryset(RowActivityQuerySet)


class RowActivityWriteMixin:
    def save_base(self, *args, **kwargs):
        using = kwargs.get("using") or (args[3] if len(args) > 3 else None)
        using = using or router.db_for_write(type(self), instance=self)
        with row_activity_write(using):
            return super().save_base(*args, **kwargs)


def row_activity_requests(view_class):
    """Bind request.user even when an authentication backend has no context hook."""
    def decorate(handler):
        @wraps(handler)
        def wrapped(self, request, *args, **kwargs):
            with row_activity_actor(request.user):
                return handler(self, request, *args, **kwargs)
        return wrapped

    for method in ("post", "put", "patch", "delete"):
        handler = view_class.__dict__.get(method)
        if handler is not None:
            setattr(view_class, method, decorate(handler))
    return view_class


def file_import_activity(function):
    """The file-creation job has no request; its dataset.user is the uploader.

    This applies only to the existing new-dataset file import, not to arbitrary
    background edits (which must not be attributed to the dataset owner).
    """
    @wraps(function)
    def wrapped(dataset_id, *args, **kwargs):
        from model_hub.models.develop_dataset import Dataset

        dataset = Dataset.objects.select_related("user").get(pk=dataset_id)
        with row_activity_actor(dataset.user, origin="import"):
            return function(dataset_id, *args, **kwargs)

    return wrapped
