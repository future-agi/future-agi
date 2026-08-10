"""
Root conftest.py for core-backend tests.
Provides common fixtures for all test modules.
"""

import os
import sys
import types
from pathlib import Path

_project_root = Path(__file__).parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# Stub live-service credentials so import-time constructors do not fail during collection.
os.environ.setdefault("VAPI_API_KEY", "test-api-key-for-testing")
os.environ.setdefault("VAPI_API_BASE_URL", "https://test.vapi.local")

from tfc.ee_loader import has_ee
from tfc.logging.config import configure_structlog

EE_AVAILABLE = has_ee("ee")

_EE_CLOUD_DIR = _project_root / "ee" / "cloud"


def pytest_ignore_collect(collection_path, config):
    """Keep cloud-only suites out of non-cloud test runs.

    ee/cloud tests exercise Django apps (ee.cloud.control_plane, cloud
    billing/temporal) that only cloud-mode settings install. Collecting
    them under tfc.settings.test fails at import time ("Model class ...
    isn't in an application in INSTALLED_APPS"), so ignore the tree unless
    the control-plane app is installed.
    """
    try:
        Path(collection_path).relative_to(_EE_CLOUD_DIR)
    except ValueError:
        return None
    try:
        from django.apps import apps

        return not apps.is_installed("ee.cloud.control_plane")
    except Exception:
        # Apps registry not ready — the cloud suites can't import either.
        return True


def _install_ee_usage_stubs_if_missing() -> None:
    """Stub ``ee.usage.*`` patch targets in OSS builds; ``__spec__`` stays None so ``has_ee``/``is_oss`` don't flip — callers must catch ``ValueError`` alongside ``ModuleNotFoundError`` when using ``find_spec``."""
    if (_project_root / "ee").is_dir():
        return

    def _make(name: str) -> types.ModuleType:
        mod = types.ModuleType(name)
        mod.__path__ = []
        # Keep __spec__ unset/None so importlib.find_spec stays falsy.
        mod.__spec__ = None
        sys.modules[name] = mod
        if "." in name:
            parent_name, child_name = name.rsplit(".", 1)
            parent = sys.modules.get(parent_name)
            if parent is not None:
                setattr(parent, child_name, mod)
        return mod

    _make("ee")
    _make("ee.usage")
    _make("ee.usage.services")
    _make("ee.usage.schemas")
    _make("ee.usage.utils")

    entitlements = _make("ee.usage.services.entitlements")

    class Entitlements:
        @staticmethod
        def check_feature(*args, **kwargs):
            return types.SimpleNamespace(allowed=True, reason="")

        @staticmethod
        def can_create(*args, **kwargs):
            return types.SimpleNamespace(allowed=True, reason="")

    entitlements.Entitlements = Entitlements

    metering = _make("ee.usage.services.metering")

    def check_usage(*args, **kwargs):
        return types.SimpleNamespace(allowed=True, reason="")

    metering.check_usage = check_usage

    emitter = _make("ee.usage.services.emitter")
    emitter.emit = lambda *args, **kwargs: None

    # Patch targets for tracer eval dual-write (_emit_eval_billing) and
    # tracer/tests/test_eval_credits_emit.py — OSS-safe mocks only.
    config = _make("ee.usage.services.config")

    class BillingConfig:
        @classmethod
        def get(cls):
            return cls()

        def get_eval_per_run_fee(self):
            return 0.0

        def calculate_ai_credits(self, cost_usd):
            try:
                return float(cost_usd or 0) * 100.0
            except (TypeError, ValueError):
                return 0.0

    config.BillingConfig = BillingConfig

    events = _make("ee.usage.schemas.events")

    class UsageEvent:
        def __init__(self, org_id, event_type, amount=0, properties=None, **kwargs):
            self.org_id = org_id
            self.event_type = event_type
            self.amount = amount
            self.properties = properties or {}
            for key, value in kwargs.items():
                setattr(self, key, value)

    events.UsageEvent = UsageEvent

    event_properties = _make("ee.usage.utils.event_properties")

    def token_usage_properties(token_usage):
        if not token_usage:
            return {}
        return {
            "prompt_tokens": token_usage.get("prompt_tokens", 0),
            "completion_tokens": token_usage.get("completion_tokens", 0),
            "total_tokens": token_usage.get("total_tokens", 0),
        }

    event_properties.token_usage_properties = token_usage_properties

    usage_entries = _make("ee.usage.utils.usage_entries")
    usage_entries.log_and_deduct_cost_for_api_request = None


_install_ee_usage_stubs_if_missing()


def pytest_configure(config):
    """Configure pytest before Django is set up.

    This hook runs before Django settings are loaded, ensuring
    the project root is in sys.path for imports like 'utils.utils'.
    """
    project_root = Path(__file__).parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    _apply_ch25_schema_for_tests()


def _strict_ch25_apply() -> bool:
    import os as _os

    return _os.getenv("FI_CH25_SCHEMA_APPLY_STRICT", "").lower() in ("1", "true", "yes")


def _apply_ch25_schema_for_tests():
    """Apply CH 25.3 v2 schema (002-013) to the test ClickHouse BEFORE
    Django app startup runs `model_hub.apps._ensure_analytics_schema`.

    The legacy analytics path creates a `spans` table with the old
    `metadata_map` / `_peerdb_is_deleted` / `span_attributes_raw` columns.
    The CH25 reader needs the v2 typed-JSON `spans` table (`metadata` as
    JSON, `is_deleted` UInt8, `attributes_extra` String). Both layers try
    to own the same table name. Running v2 schema FIRST means the legacy
    `CREATE TABLE IF NOT EXISTS spans` issued during Django startup is a
    no-op (table already exists) and the v2 typed-JSON schema wins.

    Production matches this ordering via `manage.py ch25_apply_schema` in
    the deploy entrypoint, which runs before gunicorn boots. Tests don't
    have that entrypoint, so we hook it in here.

    Skipped if not running tests with a configured CH host, or if
    `FI_SKIP_CH25_SCHEMA_APPLY=1`. Apply failures print a warning and
    continue unless `FI_CH25_SCHEMA_APPLY_STRICT=1`, which re-raises them
    (CI sets this so a broken schema apply fails the session loudly).
    """
    import os as _os

    if _os.getenv("FI_SKIP_CH25_SCHEMA_APPLY", "").lower() in ("1", "true", "yes"):
        return

    # Outside Docker, the `clickhouse` hostname from the dev .env doesn't
    # resolve; force the test sidecar at localhost:18123.
    is_test = (
        _os.getenv("DJANGO_SETTINGS_MODULE", "").endswith(".test")
        or _os.getenv("TESTING") == "true"
    )
    ch_host = _os.getenv("CH25_HOST")
    if not ch_host:
        env_host = _os.getenv("CH_HOST")
        if env_host and env_host != "clickhouse":
            ch_host = env_host
        else:
            ch_host = "localhost" if is_test else env_host
    if not ch_host:
        return

    ch_http_port = int(
        _os.getenv("CH25_HTTP_PORT") or _os.getenv("CH_HTTP_PORT") or 18123
    )
    ch_user = _os.getenv("CH25_USER") or _os.getenv("CH_USERNAME") or "default"
    ch_db = _os.getenv("CH25_DATABASE") or _os.getenv("CH_DATABASE") or "test_tfc"
    ch_password = _os.getenv("CH25_PASSWORD") or _os.getenv("CH_PASSWORD") or ""

    schema_dir = (
        Path(__file__).parent / "tracer" / "services" / "clickhouse" / "v2" / "schema"
    )
    if not schema_dir.is_dir():
        return

    try:
        _os.environ.setdefault("CH_PASSWORD", ch_password)

        from tracer.services.clickhouse.v2 import apply_schema as _v2_apply

        rc = _v2_apply.main(
            [
                "--schema-dir",
                str(schema_dir),
                "--ch-host",
                ch_host,
                "--ch-http-port",
                str(ch_http_port),
                "--ch-user",
                ch_user,
                "--ch-database",
                ch_db,
            ]
        )
        if rc not in (0, 2):
            if _strict_ch25_apply():
                raise RuntimeError(f"CH25 schema apply failed with rc={rc}")
            import sys as _sys

            print(
                f"⚠️  CH25 schema apply returned rc={rc} during pytest_configure",
                file=_sys.stderr,
            )
    except Exception as exc:
        if _strict_ch25_apply():
            raise
        import sys as _sys

        print(
            f"⚠️  CH25 schema apply skipped during pytest_configure: {exc}",
            file=_sys.stderr,
        )


_CH25_SKIP_PATH = Path(__file__).parent / "tracer" / "tests" / "_ch25_skip.txt"
_CH25_IGNORE_PATH = Path(__file__).parent / "tracer" / "tests" / "_ch25_ignore.txt"
_CH25_SKIP_REASON = (
    "CH25 migration test debt — see internal-docs repo: "
    "clickhouse-analytics/migration-to-ch25/MIGRATION_TEST_DEBT.md"
)


def _load_ch25_ignore_paths():
    if not _CH25_IGNORE_PATH.exists():
        return []
    paths = []
    for raw in _CH25_IGNORE_PATH.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        paths.append(line)
    return paths


collect_ignore_glob = _load_ch25_ignore_paths()


def _load_ch25_skip_set():
    if not _CH25_SKIP_PATH.exists():
        return frozenset()
    ids = set()
    for raw in _CH25_SKIP_PATH.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        ids.add(line)
    return frozenset(ids)


_QUARANTINE_PATH = Path(__file__).parent / ".test_quarantine.json"
_QUARANTINE_REQUIRED_KEYS = ("id", "reason", "owner", "expires")


def _load_quarantine_entries():
    """Active (unexpired), well-formed quarantine entries. Fail-open: any
    problem reading the file disables quarantine rather than breaking
    collection, and a malformed entry is dropped rather than taking the whole
    session down when the marker code subscripts it."""
    import datetime as _dt
    import json as _json

    try:
        raw = _json.loads(_QUARANTINE_PATH.read_text())
        today = _dt.date.today().isoformat()
        return [
            e
            for e in raw["entries"]
            if all(isinstance(e.get(k), str) for k in _QUARANTINE_REQUIRED_KEYS)
            and e["expires"] >= today
        ]
    except Exception:
        return []


def pytest_collection_modifyitems(config, items):
    """Auto-skip requires_ee tests when ee/ is absent + the CH25 frozen skip list."""
    import pytest as _pytest

    skip_ids = _load_ch25_skip_set()
    ch25_marker = _pytest.mark.skip(reason=_CH25_SKIP_REASON) if skip_ids else None
    ee_marker = (
        _pytest.mark.skip(reason="requires ee/ (skipped in OSS lane)")
        if not EE_AVAILABLE
        else None
    )
    quarantine = _load_quarantine_entries()

    for item in items:
        if ch25_marker is not None and item.nodeid in skip_ids:
            item.add_marker(ch25_marker)
        if ee_marker is not None and item.get_closest_marker("requires_ee") is not None:
            item.add_marker(ee_marker)
        for entry in quarantine:
            sel = entry["id"]
            if item.nodeid == sel or item.nodeid.startswith(sel + "::"):
                reason = f"quarantined: {entry['reason']} (owner {entry['owner']})"
                if entry.get("mode", "run") == "skip":
                    item.add_marker(_pytest.mark.skip(reason=reason))
                else:
                    # Strict unless the entry opts out with a literal JSON
                    # ``false``; a quarantined test that starts passing then
                    # fails the run as XPASS and the entry has to be removed.
                    # ``is not False`` always yields a bool, so a hand-edited
                    # non-bool value cannot crash the session.
                    strict = entry.get("strict", True) is not False
                    item.add_marker(_pytest.mark.xfail(reason=reason, strict=strict))
                break


import pytest
from rest_framework.test import APIClient
from rest_framework.views import APIView


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    """Drop the legacy ``spans_mv`` / ``span_metrics_hourly_mv`` once Django
    has finished booting. These MVs are recreated by
    ``model_hub.apps._ensure_analytics_schema`` and they read
    ``_peerdb_is_deleted`` from ``spans`` — a column that doesn't exist on
    the v2 typed-JSON schema (the v2 column is ``is_deleted``). Every test
    seed INSERT into ``spans`` would otherwise blow up trying to feed those
    MVs.

    Runs AFTER Django startup (pytest fixture order guarantees this) so the
    drop sticks; the same MVs are not re-created by anything else.
    """
    try:
        import clickhouse_connect

        from tracer.services.clickhouse.v2 import get_v2_config

        cfg = get_v2_config()
        host = cfg["host"]
        # `clickhouse` is the dev compose hostname; in tests force localhost.
        if host == "clickhouse":
            host = "localhost"
        client = clickhouse_connect.get_client(
            host=host,
            port=cfg["http_port"],
            username=cfg["user"],
            password=cfg["password"],
            database=cfg["database"],
        )
        try:
            for mv in ("spans_mv", "span_metrics_hourly_mv"):
                client.command(f"DROP VIEW IF EXISTS {mv}")
        finally:
            client.close()
    except Exception:
        # Don't fail the suite if the CH test sidecar isn't reachable; the
        # tests that actually need CH will fail with a clearer error.
        pass
    yield


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
def _structlog_capturable():
    """Uncached structlog before each test so capture_logs()/caplog survive
    global reconfig leaked by other tests in a full session (some suites reset
    structlog defaults per test - hence function scope). The logging.disable
    reset undoes a global stdlib disable that a few collected integration test
    scripts apply at import; no product code calls logging.disable, so it masks
    nothing."""
    import logging

    configure_structlog(cache_logger_on_first_use=False)
    logging.disable(logging.NOTSET)


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


def create_categorical_label(auth_client, name="Default Queue Label"):
    """Create a categorical annotation label via the API and return its id.

    Shared across annotation test modules so queue-creation helpers can attach
    the label the serializer now requires (>=1 label per queue). Exposed as a
    plain function (not only a fixture) because several call sites are
    module-level helpers, not fixtures/tests.
    """
    auth_client.post(
        "/model-hub/annotations-labels/",
        {
            "name": name,
            "type": "categorical",
            "settings": {
                "options": [{"label": "A"}, {"label": "B"}],
                "multi_choice": False,
                "rule_prompt": "",
                "auto_annotate": False,
                "strategy": None,
            },
        },
        format="json",
    )
    resp = auth_client.get("/model-hub/annotations-labels/", {"search": name})
    return resp.data["results"][0]["id"]


@pytest.fixture
def make_label(auth_client):
    """Factory fixture wrapping create_categorical_label for the active client."""

    def _make(name="Default Queue Label"):
        return create_categorical_label(auth_client, name=name)

    return _make
