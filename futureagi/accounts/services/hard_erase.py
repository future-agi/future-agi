"""Fail-closed application boundary for Django account/organization hard erasure.

EVERY Collector root is covered, including implicit through tables, fast deletes,
retained field updates and nonidentity ancestors. Roots are frozen; their actual
closure is recollected unfiltered under sorted organization/person lifecycle
locks. No receiver or raw DELETE runs before validation. This is NOT a DB trigger:
only explicitly trusted out-of-application DB administrators are the accepted
session residual. Product/background/management writers must use this protocol.

User deletion alone grants NO organization authority. A surviving User.organization
pointer is not an erased/altered organization. Nonidentity roots grant only their
own ownership scope and selected membership principal, not collateral people's
grants. The fixture caller may supply explicit scope through an internal primitive.
Aliases other than default refuse: legacy receivers have not qualified alias safety.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import reduce
from operator import or_

from django.core.exceptions import PermissionDenied
from django.db import DEFAULT_DB_ALIAS, models, transaction
from django.db.models.deletion import Collector

from accounts.services.hard_erase_graph import (
    OwnershipGraph,
    OwnershipUnresolved,
    projection,
)
from accounts.services.hard_erase_keys import closure_keys
from accounts.services.hard_erase_lifecycle import lifecycle_lock, require_held
from accounts.services.hard_erase_sql import (
    guarded_raw_delete,
    install_sql_guard,
    sql_authority,
)

_SCOPE = ContextVar("hard_erase_scope", default=None)
_original_delete = Collector.delete
_original_collect = Collector.collect
_installed = False
_IDENTITY = {"accounts.user", "accounts.organization"}


class HardEraseRefused(PermissionDenied):
    """Typed, non-receiver refusal; admin reports 403 without a success message."""


def _label(model):
    return getattr(model._meta, "concrete_model", model)._meta.label_lower


def _refuse(reason):
    raise HardEraseRefused(f"Hard erase refused: {reason}")


@contextmanager
def authorized_scope(organization_ids, *, using):
    """Only reviewed lifecycle callers may supply an explicit organization scope."""
    token = _SCOPE.set((using, frozenset(organization_ids)))
    try:
        yield
    finally:
        _SCOPE.reset(token)


def unfiltered_related_objects(self, related_model, related_fields, objs):
    predicate = reduce(
        or_, (models.Q(**{f"{field.name}__in": objs}) for field in related_fields)
    )
    return models.QuerySet(model=related_model, using=self.using).filter(predicate)


def _lock_rows(collector):
    """Lock only physical closure rows, never all tables or unrelated tenants."""
    groups = {}
    for obj in _rows(collector):
        model = obj._meta.concrete_model
        groups.setdefault(model, set()).add(obj.pk)
    for model in sorted(groups, key=lambda m: m._meta.db_table):
        list(
            models.QuerySet(model=model, using=collector.using)
            .filter(pk__in=groups[model])
            .order_by("pk")
            .select_for_update()
            .values_list("pk", flat=True)
        )


def _collect_groups(groups, using, origin):
    collector = Collector(using=using, origin=origin)
    live_roots = []
    for model, ids in groups.items():
        current = list(models.QuerySet(model=model, using=using).filter(pk__in=ids))
        live_roots.extend(current)
        collector.collect(current)
    return collector, live_roots


def _rows(collector):
    """Physical removals AND retained rows whose ownership would be changed."""
    seen = set()
    batches = list(collector.data.values()) + list(collector.fast_deletes)
    for updates in collector.field_updates.values():
        batches.extend(updates)
    for batch in batches:
        if isinstance(batch, models.QuerySet):
            batch = batch.only(*projection(batch.model))
        for obj in batch:
            key = (type(obj), obj.pk)
            if key not in seen:
                seen.add(key)
                yield obj


def _ownership_graph(rows, using):
    from django.apps import apps

    def read(target, field, values):
        if isinstance(target, str):
            target = apps.get_model(target)
        return (
            models.QuerySet(model=target, using=using)
            .filter(**{field + "__in": values})
            .only(*set(projection(target) + [field]))
        )

    try:
        return OwnershipGraph(rows, read)
    except OwnershipUnresolved as exc:
        _refuse(str(exc))


def _organization_scopes(obj, using, seen=None):
    # Compatibility helper; the closure uses ONE graph, never this per-row helper.
    if _label(obj) == "accounts.user":
        return set()
    return _ownership_graph([obj], using).scopes(obj)


def _check_closure(collector, roots, scope):
    from accounts.models.service_grant import ServiceGrant
    from accounts.models.workspace import WorkspaceMembership

    using = collector.using
    root_keys = {(_label(obj), obj.pk) for obj in roots}
    root_orgs = {obj.pk for obj in roots if _label(obj) == "accounts.organization"}
    root_users = {obj.pk for obj in roots if _label(obj) == "accounts.user"}
    root_workspaces = {obj.pk for obj in roots if _label(obj) == "accounts.workspace"}
    erased_users = {
        obj.pk
        for model, rows in collector.data.items()
        if _label(model) == "accounts.user"
        for obj in rows
    }
    for query in collector.fast_deletes:
        if _label(query.model) == "accounts.user":
            erased_users.update(query.values_list("pk", flat=True))
    # UUID grants are effects even though Django cannot collect them.
    grants = list(
        models.QuerySet(model=ServiceGrant, using=using)
        .filter(user__in=erased_users)
        .only(*projection(ServiceGrant))
    )
    rows = list(_rows(collector))
    graph = _ownership_graph(roots + rows + grants, using)
    scope_strings = {str(pk) for pk in scope}
    root_org_strings = {str(pk) for pk in root_orgs}
    principal_scopes = set()
    for obj in roots:
        if _label(obj) not in _IDENTITY:
            scope_strings.update(graph.scopes(obj))
        if _label(obj) in {
            "accounts.organizationmembership",
            "accounts.workspacemembership",
        }:
            principal_scopes.update(
                (str(obj.user_id), org) for org in graph.scopes(obj)
            )
    # One projected existence query for all potentially shared workspaces.
    workspaces = {
        obj.pk
        for obj in rows
        if _label(obj) == "accounts.workspace"
        and obj.organization_id not in root_orgs
        and obj.pk not in root_workspaces
    }
    shared = set(
        models.QuerySet(model=WorkspaceMembership, using=using)
        .filter(workspace_id__in=workspaces)
        .exclude(user_id__in=erased_users)
        .values_list("workspace_id", flat=True)
    )
    for obj in rows + grants:
        scopes = graph.scopes(obj)
        if not scopes.issubset(scope_strings):
            _refuse(
                "the deletion closure retains references outside the authorized organization scope"
            )
        label = _label(obj)
        if (
            label == "accounts.user"
            and obj.pk in erased_users
            and obj.pk not in root_users
        ):
            if not obj.organization_id or obj.organization_id not in root_orgs:
                _refuse("an invited descendant identity was not selected for erasure")
        if label in {"accounts.organizationmembership", "accounts.workspacemembership"}:
            selected = (label, obj.pk) in root_keys
            workspace_selected = (
                label == "accounts.workspacemembership"
                and obj.workspace_id in root_workspaces
            )
            same_principal = bool(scopes) and all(
                (str(obj.user_id), org) in principal_scopes for org in scopes
            )
            if not (
                selected
                or workspace_selected
                or same_principal
                or obj.user_id in erased_users
                or scopes.issubset(root_org_strings)
            ):
                _refuse("the closure would alter another person's grant")
        if label == "accounts.workspace" and obj.pk in shared:
            _refuse("a shared workspace still has retained membership references")


@contextmanager
def locked_preflight(roots, *, using, origin=None):
    """Hold lifecycle/check/receiver effects in ONE atomic unit, including CH preflight."""
    if using != DEFAULT_DB_ALIAS:
        _refuse("this database alias has not qualified its deletion receivers")
    from tfc.migration_database import delegated_database_alias
    from tfc.routers import force_primary

    if delegated_database_alias() not in (None, using):
        _refuse("receiver writes are pinned to a different database alias")
    configured_scope = _SCOPE.get()
    if configured_scope is not None and configured_scope[0] != using:
        _refuse("explicit scope belongs to a different database alias")
    with force_primary(), transaction.atomic(using=using):
        if isinstance(roots, models.QuerySet):
            if roots._db is not None and roots._db != using:
                _refuse("root queryset belongs to a different database alias")
            roots = list(roots.using(using).all())
        else:
            roots = list(roots)
        groups = {}
        for obj in roots:
            if obj._state.db != using:
                _refuse("a selected root belongs to a different database alias")
            groups.setdefault(type(obj), set()).add(obj.pk)
        scope = set(configured_scope[1]) if configured_scope else set()
        scope.update(obj.pk for obj in roots if _label(obj) == "accounts.organization")
        initial, initial_roots = _collect_groups(groups, using, origin)
        keys = closure_keys(initial, initial_roots)
        with lifecycle_lock(using, keys=keys):
            collector, live_roots = _collect_groups(groups, using, origin)
            require_held(using, closure_keys(collector, live_roots))
            _lock_rows(collector)
            collector, live_roots = _collect_groups(groups, using, origin)
            require_held(using, closure_keys(collector, live_roots))
            _check_closure(collector, live_roots, scope)
            from accounts.signals.membership_signals import _organizations_being_deleted

            deleting = _organizations_being_deleted()
            previous_deleting = dict(deleting)
            collector._hard_erase_checked = True
            try:
                with sql_authority(using, "erase"):
                    yield collector
            finally:
                deleting.clear()
                deleting.update(previous_deleting)
                collector._hard_erase_checked = False


def _sensitive(collector):
    # Universal census: even an otherwise unowned root can CASCADE into a protected
    # row. No fixed identity/model allowlist is used to decide whether to check.
    return bool(
        collector.data
        or getattr(collector, "fast_deletes", ())
        or getattr(collector, "field_updates", ())
    )


def remember_roots(self, objs, *args, **kwargs):
    depth = getattr(self, "_hard_erase_collect_depth", 0)
    self._hard_erase_collect_depth = depth + 1
    try:
        origin_model = getattr(self.origin, "model", type(self.origin))
        meta = getattr(origin_model, "_meta", None) or getattr(
            self.origin, "_meta", None
        )
        if depth == 0 and meta:
            roots = list(objs)
            self._hard_erase_roots = getattr(self, "_hard_erase_roots", []) + roots
            objs = roots
        return _original_collect(self, objs, *args, **kwargs)
    finally:
        self._hard_erase_collect_depth = depth


def _delete_without_recheck(collector):
    return _original_delete(collector)


def guarded_delete(self):
    if getattr(self, "_hard_erase_checked", False) or not _sensitive(self):
        return _delete_without_recheck(self)
    origin = self.origin
    roots = getattr(self, "_hard_erase_roots", None)
    if roots is None or not isinstance(origin, (models.QuerySet, models.Model)):
        _refuse("a sensitive collector has no verifiable frozen root selection")
    with locked_preflight(roots, using=self.using, origin=origin) as checked:
        result = _original_delete(checked)
        if isinstance(origin, models.Model):
            setattr(origin, origin._meta.pk.attname, None)
        return result


def install_hard_erase_guard():
    """One process-wide boundary, including previously imported Collector aliases."""
    global _installed
    if not _installed:
        Collector.related_objects = unfiltered_related_objects
        Collector.collect = remember_roots
        Collector.delete = guarded_delete
        models.QuerySet._raw_delete = guarded_raw_delete
        install_sql_guard()
        _installed = True
