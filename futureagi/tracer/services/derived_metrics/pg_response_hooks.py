"""Narrow, opt-in hooks for five cooperative response-producing model families.

No global QuerySet/Collector patch and no signals. Default-OFF means the native
method is called unchanged (including routing, validations, return values and
bulk semantics). Enable only after migration and producer-owner review. Enabled
hooks cover named managers, not Django's auto-created _base_manager, save_base,
historical models, raw SQL/COPY or cascades. PK updates, DISTINCT ON mutations
and conflict-update inserts are refused while enabled. Update/direct-overlay
DELETE intersect current predicates with captured, locked IDs: a concurrently
expanded predicate cannot enroll new rows in that operation. Ordinary DISTINCT
and aggregate-annotated updates use an unlocked candidate SELECT then base-row
locks. A03 evidence never qualifies a lane.
"""

from contextlib import nullcontext
from functools import wraps
from uuid import UUID

from django.apps import apps
from django.conf import settings
from django.db import models, router

from tfc.utils.base_model import (
    AllObjectsManager,
    BaseModelManager,
    NoWorkspaceFilterManager,
)
from tracer.services.derived_metrics.base_list_current_pg_state import (
    PgProjectScope,
    PgRevisionUnavailable,
    pg_response_mutation,
    pg_response_writer,
)


def pg_response_hooks_enabled() -> bool:
    # No production settings change. Serving/maintenance flags remain separate.
    return getattr(settings, "FI_SESSIONS_PG_REVISIONS_ENABLED", False) is True


def cooperative_pg_response_writer(*, using="default"):
    """Admission seam for existing multi-model atomic write blocks, OFF=no-op."""
    return (
        pg_response_writer(using=using)
        if pg_response_hooks_enabled()
        else nullcontext()
    )


def _raw_rows(model, using):
    # Read/lock only: no workspace/deleted manager filter may hide an OLD owner.
    return models.QuerySet(model=model, using=using)


def _scope_fields(model):
    name = model._meta.label_lower
    if name == "tracer.project":
        return ("pk",)
    if name in ("tracer.tracesessionoverlay", "model_hub.annotationslabels"):
        return ("project_id",)
    if name == "model_hub.score":
        return ("label_id",)
    if name == "accounts.user":
        return ()
    raise PgRevisionUnavailable("unsupported PG response model")


def _scopes_for(model, rows, *, using):
    name = model._meta.label_lower
    if name == "accounts.user":
        return set()
    if name == "model_hub.score":
        label_ids = {row.label_id for row in rows if row.label_id is not None}
        label_model = apps.get_model("model_hub", "AnnotationsLabels")
        # Lock label ownership before resolving it. The cooperative writer gate
        # excludes a simultaneous enrolled label move. Off-page/session-null
        # scores conservatively invalidate the entire label's project.
        project_ids = list(
            _raw_rows(label_model, using)
            .filter(pk__in=label_ids)
            .order_by("pk")
            .select_for_update(of=("self",))
            .values_list("project_id", flat=True)
        )
    elif name == "tracer.project":
        project_ids = [row.pk for row in rows]
    else:
        project_ids = [row.project_id for row in rows]
    scope_ids = {
        value if isinstance(value, UUID) else UUID(str(value))
        for value in project_ids
        if value is not None
    }
    project_model = apps.get_model("tracer", "Project")
    projects = _raw_rows(project_model, using).filter(pk__in=scope_ids)
    if name == "tracer.project":
        # Bulk inputs may not exist yet: only persisted visible Projects enroll.
        scope_ids = projects.filter(simulation_agent_id__isnull=True).values_list(
            "pk", flat=True
        )
    else:
        # Exclude known hidden owners, not deleted/workspace-filtered owners or
        # soft IDs whose Project was hard-deleted. Their fences outlive sources.
        scope_ids.difference_update(
            projects.filter(simulation_agent_id__isnull=False).values_list(
                "pk", flat=True
            )
        )
    return {PgProjectScope(value) for value in scope_ids}


def _locked_rows(model, ids, *, using):
    fields = _scope_fields(model)
    return list(
        _raw_rows(model, using)
        .filter(pk__in=[value for value in ids if value is not None])
        .only("pk", *fields)
        .order_by("pk")
        .select_for_update(of=("self",))
    )


def _mutation_rows(queryset, *, using):
    # DISTINCT ON can select fewer identities than native UPDATE (which ignores
    # DISTINCT). Do not silently change that mutation's domain.
    if queryset.query.distinct_fields:
        raise PgRevisionUnavailable("DISTINCT ON mutation is unenrolled")
    # Candidate selection may contain DISTINCT / GROUP BY. PostgreSQL forbids
    # FOR UPDATE on those shapes, so lock only unannotated base rows afterwards.
    # Preserve the original predicate and ordering in the candidate query.
    candidates = queryset.using(using).values_list("pk", flat=True)
    candidates.query.select_for_update = False
    ids = list(candidates)
    return _locked_rows(queryset.model, ids, using=using)


def _bump(model, scopes, *, using):
    with pg_response_mutation(
        scopes, identity_changed=model._meta.label_lower == "accounts.user", using=using
    ):
        pass


def pg_response_save(save):
    """Wrap the WHOLE native save, including validation/history and follow-ups."""

    @wraps(save)
    def wrapped(instance, *args, **kwargs):
        if not pg_response_hooks_enabled():
            return save(instance, *args, **kwargs)
        # Django accepts positional force_insert, force_update, using,
        # update_fields; preserve their semantics rather than silently choosing
        # another connection. Duplicate argument errors remain native errors.
        using = kwargs.get("using") or (args[2] if len(args) > 2 else None)
        using = using or router.db_for_write(type(instance), instance=instance)
        with pg_response_writer(using=using):
            previous = _locked_rows(type(instance), [instance.pk], using=using)
            scopes = _scopes_for(type(instance), previous, using=using)
            result = save(instance, *args, **kwargs)
            current = _locked_rows(type(instance), [instance.pk], using=using)
            scopes |= _scopes_for(type(instance), current, using=using)
            _bump(type(instance), scopes, using=using)
            return result

    return wrapped


class PgResponseQuerySet(models.QuerySet):
    """Cooperative managers only. Hard deletes stay with the single A06 owner."""

    # _db, _hints, _fields and _not_support_combined_queries are Django-private
    # QuerySet members that exist at runtime but are absent from django-stubs.
    def _write_alias(self):
        return self._db or router.db_for_write(self.model, **self._hints)  # type: ignore[attr-defined]

    def update_or_create(self, *args, **kwargs):
        if not pg_response_hooks_enabled():
            return super().update_or_create(*args, **kwargs)
        # Django takes a source row lock before save: the gate must precede it.
        with pg_response_writer(using=self._write_alias()):
            return super().update_or_create(*args, **kwargs)

    def get_or_create(self, *args, **kwargs):
        if not pg_response_hooks_enabled():
            return super().get_or_create(*args, **kwargs)
        with pg_response_writer(using=self._write_alias()):
            return super().get_or_create(*args, **kwargs)

    def update(self, **kwargs):
        if not pg_response_hooks_enabled():
            return super().update(**kwargs)
        pk = self.model._meta.pk
        if {"pk", pk.name, pk.attname}.intersection(kwargs):
            # Post-write scope resolution relies on stable identities. Native
            # PK reassignment must remain unavailable, not lose its new owner.
            raise PgRevisionUnavailable("primary-key update is unenrolled")
        self._not_support_combined_queries("update")  # type: ignore[attr-defined]
        if self.query.is_sliced:
            raise TypeError("Cannot update a query once a slice has been taken.")
        using = self._write_alias()
        with pg_response_writer(using=using):
            rows = _mutation_rows(self, using=using)
            ids = [row.pk for row in rows]
            scopes = _scopes_for(self.model, rows, using=using)
            # Intersect, never replace, the original tenant/membership predicate.
            # A related non-response writer can expand that predicate without
            # taking our gate: it must not add an uncaptured row to this write.
            targets = self.using(using).filter(pk__in=ids)
            self._for_write = True
            result = models.QuerySet.update(targets, **kwargs)
            self._result_cache = None
            scopes |= _scopes_for(
                self.model, _locked_rows(self.model, ids, using=using), using=using
            )
            if result:
                _bump(self.model, scopes, using=using)
            return result

    def bulk_create(self, objs, *args, **kwargs):
        if not pg_response_hooks_enabled():
            return super().bulk_create(objs, *args, **kwargs)
        # update_conflicts can target another existing PK via a unique key, so
        # input PKs do not prove OLD ownership. No silent partial enrollment.
        if kwargs.get("update_conflicts") or (len(args) > 2 and args[2]):
            raise PgRevisionUnavailable("bulk conflict-update is unenrolled")
        using = self._write_alias()
        objs = list(objs)
        with pg_response_writer(using=using):
            previous = _locked_rows(self.model, [obj.pk for obj in objs], using=using)
            scopes = _scopes_for(self.model, previous, using=using)
            scopes |= _scopes_for(self.model, objs, using=using)
            result = super().bulk_create(objs, *args, **kwargs)
            scopes |= _scopes_for(self.model, result, using=using)
            if objs:
                # ignore_conflicts can conservatively bump even if no insert.
                _bump(self.model, scopes, using=using)
            return result

    def delete(self):
        # Overlay has only soft UUID references and no incoming FK graph. Its
        # direct deletion can use the native Collector unchanged. All other
        # hard deletes / field_updates remain explicitly A06-owned, unenrolled.
        if (
            not pg_response_hooks_enabled()
            or self.model._meta.label_lower != "tracer.tracesessionoverlay"
        ):
            return super().delete()
        self._not_support_combined_queries("delete")  # type: ignore[attr-defined]
        if self.query.is_sliced:
            raise TypeError("Cannot use 'limit' or 'offset' with delete().")
        if self._fields is not None:  # type: ignore[attr-defined]
            raise TypeError("Cannot call delete() after .values() or .values_list()")
        using = self._write_alias()
        with pg_response_writer(using=using):
            rows = _mutation_rows(self, using=using)
            scopes = _scopes_for(self.model, rows, using=using)
            targets = self.using(using).filter(pk__in=[row.pk for row in rows])
            result = models.QuerySet.delete(targets)
            self._result_cache = None
            if result[0]:
                _bump(self.model, scopes, using=using)
            return result

    def bulk_update(self, objs, fields, batch_size=None):
        if not pg_response_hooks_enabled():
            return super().bulk_update(objs, fields, batch_size=batch_size)
        using = self._write_alias()
        with pg_response_writer(using=using):
            # Django dispatches through this class's update() for each CASE
            # batch. All batches and revisions share this outer transaction.
            return super().bulk_update(objs, fields, batch_size=batch_size)


class PgResponseManagerMixin:
    # Existing manager filtering/get_queryset behavior is unchanged. from_queryset
    # is not needed: Django's Manager methods proxy to this QuerySet class.
    _queryset_class = PgResponseQuerySet


class PgResponseBaseManager(PgResponseManagerMixin, BaseModelManager):
    pass


class PgResponseAllManager(PgResponseManagerMixin, AllObjectsManager):
    pass


class PgResponseNoWorkspaceManager(PgResponseManagerMixin, NoWorkspaceFilterManager):
    pass
