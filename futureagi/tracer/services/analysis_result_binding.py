"""Legacy results stay legacy; background candidates publish exactly once.

A lifecycle row opts a trace into generation ownership. No interactive call or
historical read creates one. Candidate mutation locks only the candidate key,
never the lifecycle row needed by cancellation.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from uuid import UUID

from django.db import models

from tfc.utils.base_model import (
    AllObjectsManager,
    BaseModelManager,
    NoWorkspaceFilterManager,
)

_attempt = ContextVar("trace_analysis_attempt", default=None)


class ResultOwnershipError(RuntimeError):
    pass


@contextmanager
def attempt_scope(project_id, trace_id, generation):
    token = _attempt.set(tuple(map(str, (project_id, trace_id, generation))))
    try:
        yield
    finally:
        _attempt.reset(token)


def current_attempt():
    return _attempt.get()


def lifecycle_for_result(model):
    from tracer.models.background_trace_analysis_state import (
        BackgroundTraceAnalysisState,
    )

    prefix = "" if model.__name__ == "TraceErrorAnalysis" else "analysis__"
    return BackgroundTraceAnalysisState.objects.filter(
        project_id=models.OuterRef(prefix + "project_id"),
        trace_id=models.OuterRef(prefix + "trace_id"),
    )


@contextmanager
def locked_attempt():
    from tracer.models.background_trace_analysis_state import (
        BackgroundTraceAnalysisState,
    )
    from tracer.services.analysis_result_lock import result_lock

    identity = current_attempt()
    if identity is None:
        raise ResultOwnershipError("analysis_generation_required")
    project, trace, generation = identity
    # Advisory key is disjoint from the lifecycle row. Aborting never needs it.
    # Completion takes the same key, so no writer can mutate a published result.
    with result_lock(generation):
        row = BackgroundTraceAnalysisState.objects.filter(
            project_id=project, trace_id=trace, generation=generation, status="running"
        ).first()
        if row is None:
            raise ResultOwnershipError("analysis_generation_superseded")
        yield identity


class ResultQuerySet(models.QuerySet):
    def _require_legacy(self):
        if (
            current_attempt() is not None
            or self.filter(models.Exists(lifecycle_for_result(self.model))).exists()
        ):
            raise ResultOwnershipError("analysis_results_are_immutable")

    def update(self, **kwargs):
        self._require_legacy()
        legacy = self.filter(~models.Exists(lifecycle_for_result(self.model)))
        return models.QuerySet.update(legacy, **kwargs)

    def delete(self):
        self._require_legacy()
        legacy = self.filter(~models.Exists(lifecycle_for_result(self.model)))
        return models.QuerySet.delete(legacy)

    def bulk_create(self, objs, **kwargs):
        # Background writers use instance saves under the candidate lock.
        if current_attempt() is not None:
            raise ResultOwnershipError("use_generation_locked_instance_save")
        objs = list(objs)
        for obj in objs:
            obj.require_legacy()
        return super().bulk_create(objs, **kwargs)

    def bulk_update(self, objs, fields, **kwargs):
        if current_attempt() is not None:
            raise ResultOwnershipError("use_generation_locked_instance_save")
        objs = list(objs)
        for obj in objs:
            obj.require_legacy()
        return super().bulk_update(objs, fields, **kwargs)


class ResultVisibilityMixin:
    """Legacy rows OR the completed binding; applies to reverse managers too."""

    def get_queryset(self):
        from tracer.models.background_trace_analysis_state import (
            BackgroundTraceAnalysisState,
        )

        results = BackgroundTraceAnalysisState.objects.filter(
            status="completed", result_analysis_id__isnull=False
        ).values("result_analysis_id")
        key = (
            "pk__in"
            if self.model.__name__ == "TraceErrorAnalysis"
            else "analysis_id__in"
        )
        query = (
            super()
            .get_queryset()
            .filter(
                ~models.Exists(lifecycle_for_result(self.model))
                | models.Q(**{key: models.Subquery(results)})
            )
        )
        if self.model.__name__ != "TraceErrorAnalysis":
            query = query.filter(
                ~models.Exists(lifecycle_for_result(self.model))
                | models.Q(analysis__deleted=False)
            )
        return query


class CompletedAnalysisManager(
    ResultVisibilityMixin, BaseModelManager.from_queryset(ResultQuerySet)
):
    pass


class CompletedAllObjectsManager(
    ResultVisibilityMixin, AllObjectsManager.from_queryset(ResultQuerySet)
):
    pass


class CompletedUnscopedManager(
    ResultVisibilityMixin, NoWorkspaceFilterManager.from_queryset(ResultQuerySet)
):
    pass


class AttemptDraftManager(BaseModelManager.from_queryset(ResultQuerySet)):
    """Own background candidate, or the original legacy read/write surface."""

    def get_queryset(self):
        query = super().get_queryset()
        identity = current_attempt()
        if identity is None:
            return query.filter(~models.Exists(lifecycle_for_result(self.model)))
        project, trace, generation = identity
        if self.model.__name__ == "TraceErrorAnalysis":
            return query.filter(pk=generation, project_id=project, trace_id=trace)
        return query.filter(analysis_id=generation)


class AttemptResultMixin:
    def require_legacy(self):
        from tracer.models.trace_error_analysis import TraceErrorAnalysis
        from tracer.queries import background_analysis_state as state

        if self.__class__.__name__ == "TraceErrorAnalysis":
            project, trace = self.project_id, self.trace_id
        else:
            # Resolve the actual parent, never trust a caller-supplied scope.
            parent = TraceErrorAnalysis._base_manager.get(pk=self.analysis_id)
            project, trace = parent.project_id, parent.trace_id
        if state.get_state(trace, project_ids=[project]) is not None:
            raise ResultOwnershipError("analysis_generation_required")

    def save(self, *args, **kwargs):
        if current_attempt() is None:
            self.require_legacy()
            return super().save(*args, **kwargs)
        with locked_attempt() as (project, trace, generation):
            if self.__class__.__name__ == "TraceErrorAnalysis":
                if str(self.project_id) != project or str(self.trace_id) != trace:
                    raise ResultOwnershipError("analysis_identity_mismatch")
                if self._state.adding:
                    self.pk = UUID(generation)
                elif str(self.pk) != generation:
                    raise ResultOwnershipError("analysis_result_not_owned")
            elif str(self.analysis_id) != generation:
                raise ResultOwnershipError("analysis_result_not_owned")
            return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self.require_legacy()
        return super().delete(*args, **kwargs)


@contextmanager
def legacy_attempt(project_id, trace_id):
    """Compatibility scope, not admission: only background workers create rows."""
    from tracer.queries import background_analysis_state as state

    identity = current_attempt()
    if identity is not None:
        if identity[:2] != (str(project_id), str(trace_id)):
            raise ResultOwnershipError("analysis_identity_mismatch")
        with locked_attempt():
            pass
    elif state.get_state(trace_id, project_ids=[project_id]) is not None:
        raise ResultOwnershipError("analysis_generation_required")
    yield


@contextmanager
def background_attempt(project_id, trace_id):
    """Admission for old queued/background payloads, never interactive tools."""
    from tracer.queries import background_analysis_state as state

    generation = state.begin(project_id, trace_id, only_if_absent=True)
    if generation is None or not state.admit(project_id, trace_id, generation):
        raise ResultOwnershipError("analysis_generation_required")
    with attempt_scope(project_id, trace_id, generation):
        try:
            yield
            if not state.complete(project_id, trace_id, generation):
                raise ResultOwnershipError("analysis_result_missing_or_superseded")
        except BaseException:
            state.abort(project_id, trace_id, generation, "analysis_failed")
            raise
