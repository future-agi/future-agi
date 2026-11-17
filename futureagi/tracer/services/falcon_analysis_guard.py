"""Cooperative lifetime and live authorization for request-bound trace workers.

Only the background error-analysis task installs this guard. Interactive Falcon
and other agents keep their existing dispatch semantics. Already-admitted remote
operations cannot be undone; no later dispatch may reuse their authorization.
"""

import time
from threading import Event

from channels.db import database_sync_to_async
from rest_framework.exceptions import PermissionDenied

from ee.falcon_ai.tool_scope import connector_for_execution
from tracer.services.falcon_analysis_scope import resolve_analysis_scope


class AnalysisAborted(BaseException):
    """Terminal control flow, never a retryable tool/fallback Exception.

    Only the owning worker handles this outcome. Generic provider, tool and
    nested orchestrator error handlers must not turn a stop into more work.
    """

    def __init__(self, code):
        self.code = code
        super().__init__(code)


class AnalysisGuard:
    def __init__(self, project_id, user_id, organization_id, workspace_id, timeout):
        self.scope_ids = (project_id, user_id, organization_id, workspace_id)
        self.deadline = time.monotonic() + timeout
        self.stopped = Event()
        self.code = None
        self.connectors = set()

    @property
    def remaining(self):
        return max(0, self.deadline - time.monotonic())

    def abort(self, code="analysis_access_revoked"):
        if not self.stopped.is_set():
            self.code = code
            self.stopped.set()
        raise AnalysisAborted(self.code)

    def check(self):
        """Rehydrate the principal, never trust a cached User or membership."""
        if self.stopped.is_set():
            raise AnalysisAborted(self.code)
        if not self.remaining:
            self.abort("analysis_deadline_exceeded")
        try:
            _, context = resolve_analysis_scope(*self.scope_ids)
        except PermissionDenied:
            self.abort()
        # The fresh context has no guard yet, avoiding recursive gate calls.
        # Recheck previously used connectors even on an iteration with no tools.
        for connector_id, tool_name in tuple(self.connectors):
            if connector_for_execution(connector_id, tool_name, context) is None:
                self.abort()
        if not self.remaining:
            self.abort("analysis_deadline_exceeded")
        if self.stopped.is_set():
            raise AnalysisAborted(self.code)
        context.background_analysis = self
        return context

    def run_sync(self, operation, *, cleanup=None):
        """Bound a blocking continuation by this job's remaining budget.

        Cancelling an awaiter cannot kill synchronous database/provider calls.
        A daemon continuation owns copied ContextVars and must check this guard
        after each blocking call and before each new effect. On timeout the
        caller returns immediately; the stopped guard suppresses late effects.
        Do not use an executor context manager here: its shutdown waits for the
        very dependency we are bounding.
        """
        from contextvars import copy_context
        from threading import Thread

        if not self.remaining:
            self.abort("analysis_deadline_exceeded")
        finished = Event()
        outcome = []

        def invoke():
            try:
                from tracer.services.background_transport import (
                    analysis_transport_scope,
                )

                with analysis_transport_scope(self):
                    self.check()
                    value = operation()
                    self.check()
                    outcome.append((True, value))
            except BaseException as exc:
                outcome.append((False, exc))
            finally:
                try:
                    if cleanup is not None:
                        cleanup()
                finally:
                    finished.set()

        context = copy_context()
        Thread(target=context.run, args=(invoke,), daemon=True).start()
        if not finished.wait(self.remaining):
            self.abort("analysis_deadline_exceeded")
        if not self.remaining:
            self.abort("analysis_deadline_exceeded")
        if self.stopped.is_set():
            raise AnalysisAborted(self.code)
        success, value = outcome[0]
        if not success:
            raise value
        return value

    async def checkpoint(self):
        return await database_sync_to_async(self.check)()
