"""Batched lifecycle key discovery for Collector closures.

The ownership graph is shared for a whole batch. People referenced by FK (owner,
creator, inviter) participate even when that reference does not confer authority.
Discovery is not authority; the eraser still applies the full closure policy.
"""

from copy import copy

from django.db import models

from accounts.services.hard_erase_graph import projection
from accounts.services.hard_erase_lifecycle import identity_keys


def _copy_row(row, using):
    try:
        # Django's copy protocol resolves historical labels to installed models.
        # Keep that behavior, and the normal runtime-model path, unchanged.
        return copy(row)
    except LookupError:
        from django.apps import apps
        from django.db import connections

        from accounts.services.hard_erase import _refuse
        from accounts.services.hard_erase_migrations import migration_sql_allowed

        if row._meta.apps is apps:
            raise
        if not migration_sql_allowed(connections[using]):
            _refuse("an unregistered model requires the migration boundary")
        # StateApps-only models and MigrationRecorder's private Apps model have
        # no global label to unpickle. Snapshot without invoking Model.__reduce__;
        # these rows still pass through the complete ownership/closure checks.
        snapshot = object.__new__(type(row))
        snapshot.__dict__ = row.__dict__.copy()
        snapshot._state = copy(row._state)
        snapshot._state.fields_cache = row._state.fields_cache.copy()
        return snapshot


def row_keys(rows, using):
    from accounts.services.hard_erase import _ownership_graph

    rows = [_copy_row(row, using) for row in rows]
    for index, row in enumerate(rows):
        if row.pk is None:
            # Unsaved auto-PK rows in a bulk insert must not collapse into one
            # graph node and lose the other rows' organization/person bindings.
            if row._meta.label_lower in {"accounts.user", "accounts.organization"}:
                from accounts.services.hard_erase import _refuse

                _refuse("identity insertion requires its UUID before locking")
            row.pk = f"unpersisted-{index}"
    graph = _ownership_graph(rows, using)
    organizations, people = set(), set()
    for obj in rows:
        label = obj._meta.label_lower
        organizations.update(graph.scopes(obj))
        if label == "accounts.user":
            people.add(obj.pk)
        for field in obj._meta.concrete_fields:
            if not field.is_relation and field.name not in {
                "user",
                "user_id",
                "person",
                "person_id",
            }:
                continue
            value = getattr(obj, field.attname)
            if field.is_relation:
                target = field.remote_field.model._meta.label_lower
                if target == "accounts.user":
                    people.add(value)
                elif target == "accounts.organization":
                    organizations.add(value)
            elif label == "accounts.servicegrant" and field.name == "user":
                people.add(value)
    return identity_keys(organizations=organizations, people=people)


def closure_keys(collector, roots):
    from accounts.models.service_grant import ServiceGrant
    from accounts.services.hard_erase import _rows

    rows = list(roots) + list(_rows(collector))
    people = {obj.pk for obj in rows if obj._meta.label_lower == "accounts.user"}
    grants = list(
        models.QuerySet(model=ServiceGrant, using=collector.using)
        .filter(user__in=people)
        .only(*projection(ServiceGrant))
    )
    return row_keys(rows + grants, collector.using)
