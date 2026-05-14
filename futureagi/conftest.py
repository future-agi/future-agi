"""
Root conftest.py for core-backend tests.
Provides common fixtures for all test modules.
"""

import sys
from pathlib import Path

_project_root = Path(__file__).parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def pytest_configure(config):
    """Configure pytest before Django is set up.

    This hook runs before Django settings are loaded, ensuring
    the project root is in sys.path for imports like 'utils.utils'.
    """
    project_root = Path(__file__).parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))


from unittest.mock import patch

import pytest
from rest_framework.test import APIClient
from rest_framework.views import APIView


@pytest.fixture(autouse=True, scope="session")
def _force_flush_cascade():
    """Force TRUNCATE ... CASCADE in TransactionTestCase teardown.

    pytest-django's ``transaction=True`` tests fall back to a Django
    ``TransactionTestCase`` whose teardown calls ``connection.ops.sql_flush``
    with ``allow_cascade=False``. On PostgreSQL this raises
    ``cannot truncate a table referenced in a foreign key constraint`` whenever
    a model has FK references from a table outside the truncate set, which
    leaks data into subsequent tests and breaks fixtures relying on a clean
    DB. Forcing CASCADE keeps teardown working across the whole project.
    """
    from django.db.backends.postgresql.operations import DatabaseOperations as _PgOps

    _original = _PgOps.sql_flush

    def _cascade_flush(
        self, style, tables, *, reset_sequences=False, allow_cascade=False
    ):
        return _original(
            self,
            style,
            tables,
            reset_sequences=reset_sequences,
            allow_cascade=True,
        )

    _PgOps.sql_flush = _cascade_flush
    try:
        yield
    finally:
        _PgOps.sql_flush = _original


from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

# Store original APIView.initial for patching
_original_apiview_initial = APIView.initial


# Registry of all live WorkspaceAwareAPIClient instances. An autouse fixture
# below tears down any clients that weren't explicitly stopped by the test,
# preventing their injected APIView.initial patch from leaking into later tests
# in the same pytest process. Several helper functions across the test suite
# (`_make_client` and friends) skip the cleanup step — centralising it here
# makes the leak impossible regardless of how the client is instantiated.
_LIVE_WORKSPACE_AWARE_CLIENTS: list = []
_WORKSPACE_INITIAL_PATCH_ACTIVE = False


def _initial_with_workspace(view_self, request, *args, **view_kwargs):
    # Only inject workspace + organization for requests that carry the
    # X-Workspace-Id header (set by set_workspace credentials). Resolve from
    # the header so multiple clients in the same test can target different
    # workspaces without nested client-specific APIView.initial patches.
    ws_header = request.META.get("HTTP_X_WORKSPACE_ID")
    if ws_header:
        from accounts.models.workspace import Workspace

        workspace = (
            Workspace.no_workspace_objects.select_related("organization")
            .filter(id=ws_header, is_active=True)
            .first()
        )
    else:
        workspace = None
    if workspace:
        request.workspace = workspace
        request.organization = workspace.organization
        # Also set thread-local context so permission checks (which use
        # get_current_organization()) and model managers work correctly.
        # This runs AFTER URL resolution/view import, so class-level viewset
        # querysets are already evaluated cleanly.
        set_workspace_context(
            workspace=workspace,
            organization=workspace.organization,
        )
    return _original_apiview_initial(view_self, request, *args, **view_kwargs)


class WorkspaceAwareAPIClient(APIClient):
    """Custom APIClient that injects request.workspace for tests.

    This is needed because force_authenticate bypasses the authentication
    class that normally sets request.workspace.

    Thread-local workspace context is NOT set during requests to avoid
    polluting class-level ViewSet querysets (BaseModelManager applies
    _apply_workspace_filter using thread-local context). Instead, the
    BaseModelViewSetMixin correctly filters using request.workspace and
    request.organization attributes injected by the patcher.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._workspace = None
        self._patcher = None
        _LIVE_WORKSPACE_AWARE_CLIENTS.append(self)

    def set_workspace(self, workspace):
        """Set the workspace for subsequent requests."""
        self._workspace = workspace
        if workspace:
            self.credentials(
                HTTP_X_WORKSPACE_ID=str(workspace.id),
                HTTP_X_ORGANIZATION_ID=str(workspace.organization_id),
            )
            # Start patching APIView.initial to inject workspace + organization
            self._start_workspace_injection()

    def _start_workspace_injection(self):
        """Patch APIView.initial to inject workspace into requests."""
        global _WORKSPACE_INITIAL_PATCH_ACTIVE
        if (
            _WORKSPACE_INITIAL_PATCH_ACTIVE
            and APIView.__dict__.get("initial") is _initial_with_workspace
        ):
            return
        APIView.initial = _initial_with_workspace
        _WORKSPACE_INITIAL_PATCH_ACTIVE = True

    def _request_with_clean_context(self, method, *args, **kwargs):
        """Clear thread-local workspace context before and after each request.

        Before: prevents BaseModelManager._apply_workspace_filter from
        polluting class-level ViewSet querysets when view modules are lazily
        imported during the first request.

        During: initial_with_workspace sets thread-local context so permission
        checks (get_current_organization) and managers work correctly.

        After: prevents thread-local context from leaking into subsequent ORM
        queries in test code (e.g. WorkspaceMembership.objects.filter).

        This mimics the production auth middleware lifecycle.
        """
        if self._workspace is not None:
            self._start_workspace_injection()
            # Keep workspace routing tied to this client instance on every
            # request. Some tests create multiple authenticated clients in the
            # same function; passing headers per request avoids any process-
            # global DRF client credential state from making both requests use
            # the last-created workspace.
            self.credentials(
                HTTP_X_WORKSPACE_ID=str(self._workspace.id),
                HTTP_X_ORGANIZATION_ID=str(self._workspace.organization_id),
            )
            kwargs.setdefault("HTTP_X_WORKSPACE_ID", str(self._workspace.id))
            kwargs.setdefault(
                "HTTP_X_ORGANIZATION_ID", str(self._workspace.organization_id)
            )
        clear_workspace_context()
        try:
            return method(*args, **kwargs)
        finally:
            clear_workspace_context()

    def get(self, *args, **kwargs):
        return self._request_with_clean_context(super().get, *args, **kwargs)

    def post(self, *args, **kwargs):
        return self._request_with_clean_context(super().post, *args, **kwargs)

    def put(self, *args, **kwargs):
        return self._request_with_clean_context(super().put, *args, **kwargs)

    def patch(self, *args, **kwargs):
        return self._request_with_clean_context(super().patch, *args, **kwargs)

    def delete(self, *args, **kwargs):
        return self._request_with_clean_context(super().delete, *args, **kwargs)

    def options(self, *args, **kwargs):
        return self._request_with_clean_context(super().options, *args, **kwargs)

    def head(self, *args, **kwargs):
        return self._request_with_clean_context(super().head, *args, **kwargs)

    def stop_workspace_injection(self):
        """Stop the workspace injection patch."""
        from rest_framework.views import APIView

        global _WORKSPACE_INITIAL_PATCH_ACTIVE
        if APIView.__dict__.get("initial") is _initial_with_workspace:
            APIView.initial = _original_apiview_initial
            _WORKSPACE_INITIAL_PATCH_ACTIVE = False
        self._patcher = None


@pytest.fixture(autouse=True)
def clean_workspace_context():
    """Clean workspace thread-local context before and after each test.

    Also ensures all view modules are imported (and class-level querysets
    evaluated) while no thread-local context is active, preventing
    queryset pollution.
    """
    clear_workspace_context()
    yield
    clear_workspace_context()


@pytest.fixture(autouse=True)
def _teardown_workspace_aware_clients():
    """Stop any APIView.initial patches left behind by leaked clients.

    Several test helpers (e.g. ``_make_client``) create a
    ``WorkspaceAwareAPIClient``, call ``set_workspace`` (which installs a
    process-global ``APIView.initial`` patch) and never tear it down. Without
    this fixture, the patch survives and contaminates every subsequent test
    in the pytest process — causing ``request.workspace`` in later tests to
    point at a workspace from a long-finished test, which typically surfaces
    as 404/400/403 responses where 200 was expected.

    Forcibly restore ``APIView.initial`` to the original method captured when
    this module was imported. Restoring to a per-test snapshot is insufficient:
    if a prior test already leaked a patch, the snapshot itself is
    contaminated and cross-org tests will keep using a stale workspace.
    """
    from rest_framework.views import APIView

    global _WORKSPACE_INITIAL_PATCH_ACTIVE
    APIView.initial = _original_apiview_initial
    _WORKSPACE_INITIAL_PATCH_ACTIVE = False
    yield
    # Drain the registry, stopping each live patcher.
    while _LIVE_WORKSPACE_AWARE_CLIENTS:
        client = _LIVE_WORKSPACE_AWARE_CLIENTS.pop()
        try:
            client.stop_workspace_injection()
        except Exception:
            pass
    # Forcibly restore APIView.initial. If it differs, a leaked patch
    # survived stop_workspace_injection (e.g. out-of-order stop or silent
    # exception). Restoring the class attribute directly is the only
    # reliable way to unwind it.
    APIView.initial = _original_apiview_initial
    _WORKSPACE_INITIAL_PATCH_ACTIVE = False


@pytest.fixture
def organization(db):
    """Create a test organization."""
    return Organization.objects.create(name="Test Organization")


@pytest.fixture
def user(db, organization):
    """Create a test user with organization.

    Uses @futureagi.com email to bypass recaptcha verification in tests.
    Also creates a default workspace and sets up thread-local context.
    """
    clear_workspace_context()
    set_workspace_context(organization=organization)

    # Create user first
    # Use unique email to avoid duplicate-key collisions when prior test
    # teardown (flush) fails to clean rows in transaction=True tests.
    import uuid as _uuid

    user = User.objects.create_user(
        email=f"test-{_uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Test User",
        organization=organization,
        organization_role=OrganizationRoles.OWNER,
    )

    # Create OrganizationMembership (source of truth for org access)
    from accounts.models.organization_membership import OrganizationMembership
    from tfc.constants.levels import Level

    OrganizationMembership.no_workspace_objects.get_or_create(
        user=user,
        organization=organization,
        defaults={
            "role": OrganizationRoles.OWNER,
            "level": Level.OWNER,
            "is_active": True,
        },
    )

    # Create workspace with user as creator
    workspace = Workspace.objects.create(
        name="Test Workspace",
        organization=organization,
        is_default=True,
        is_active=True,
        created_by=user,
    )

    # Create WorkspaceMembership so user appears in workspace-scoped queries
    from accounts.models.workspace import WorkspaceMembership

    org_membership = OrganizationMembership.no_workspace_objects.filter(
        user=user, organization=organization
    ).first()
    WorkspaceMembership.no_workspace_objects.get_or_create(
        user=user,
        workspace=workspace,
        defaults={
            "role": "Workspace Owner",
            "level": Level.OWNER,
            "is_active": True,
            "organization_membership": org_membership,
        },
    )

    # Now set the workspace context for subsequent operations
    set_workspace_context(workspace=workspace, organization=organization, user=user)

    return user


@pytest.fixture
def workspace(db, user):
    """Get the test workspace (created by user fixture)."""
    return Workspace.objects.get(organization=user.organization, is_default=True)


@pytest.fixture
def api_client():
    """Unauthenticated API client."""
    return WorkspaceAwareAPIClient()


@pytest.fixture
def auth_client(user, workspace):
    """Authenticated API client with workspace context."""
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(workspace)
    yield client
    # Clean up the workspace injection patcher
    client.stop_workspace_injection()
