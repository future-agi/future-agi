import logging
from contextlib import contextmanager
from contextvars import ContextVar

from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger(__name__)

# Sentinel object used to distinguish "not passed" from an explicit None.
_UNSET = object()

# ContextVar-based storage for workspace context — works in both sync and async.
# Each request/coroutine gets its own isolated copy automatically.
_current_workspace: ContextVar = ContextVar("current_workspace", default=None)
_current_organization: ContextVar = ContextVar("current_organization", default=None)
_current_user: ContextVar = ContextVar("current_user", default=None)


def get_current_workspace():
    """Return the workspace for the current request, or None."""
    return _current_workspace.get()


def get_current_organization():
    """Return the organization for the current request, or None."""
    return _current_organization.get()


def get_current_user():
    """Return the user for the current request, or None."""
    return _current_user.get()


def set_workspace_context(*, workspace=_UNSET, organization=_UNSET, user=_UNSET):
    """Set the per-request context.

    Called by ``APIKeyAuthentication._set_workspace_context`` after the
    request is authenticated so that model-layer code (managers, signals,
    fields) can access the workspace without needing a ``request`` object.

    Only fields that are explicitly passed are updated; omitted fields
    retain their current value.  Pass ``None`` explicitly to clear a field.
    """
    if workspace is not _UNSET:
        _current_workspace.set(workspace)
    if organization is not _UNSET:
        _current_organization.set(organization)
    if user is not _UNSET:
        _current_user.set(user)


def clear_workspace_context():
    """Remove all per-request context."""
    _current_workspace.set(None)
    _current_organization.set(None)
    _current_user.set(None)


@contextmanager
def workspace_context(workspace, organization=None, user=None):
    """Context manager for background tasks, threads, and any non-request code.

    Guarantees cleanup even if the task raises an exception.
    Logs a warning if overwriting an existing context (likely a bug).

    Usage::

        workspace = Workspace.objects.get(id=workspace_id, organization_id=org_id)
        with workspace_context(workspace, organization=workspace.organization):
            # all .objects queries are scoped to this workspace
            data = Dataset.objects.all()

    For threads / multiprocessing — call this inside the target function,
    NOT in the parent. contextvars do NOT propagate to child threads.
    """
    old_workspace = _current_workspace.get()
    if old_workspace and old_workspace != workspace:
        logger.warning(
            "workspace_context overwriting existing context: %s -> %s",
            old_workspace.id if hasattr(old_workspace, "id") else old_workspace,
            workspace.id if hasattr(workspace, "id") else workspace,
        )

    set_workspace_context(workspace=workspace, organization=organization, user=user)
    try:
        yield
    finally:
        clear_workspace_context()


class WorkspaceContextMiddleware(MiddlewareMixin):

    def process_request(self, request):
        """Start every request from an empty model-manager scope.

        Authentication binds the authorized workspace later in the request.
        Clearing here is intentionally redundant with ``process_response``:
        ASGI workers may reuse a sync execution context after a cancelled or
        otherwise incomplete request, and an inherited workspace would make
        explicit request-scoped ORM queries silently return no rows.
        """
        clear_workspace_context()

    def process_response(self, request, response):
        clear_workspace_context()
        return response

    def process_exception(self, request, exception):
        clear_workspace_context()
