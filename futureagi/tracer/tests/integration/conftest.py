"""Integration suite fixtures (sub-package scoped).

These do not leak into the parent ``tracer/tests/`` namespace — only tests
collected from ``futureagi/tracer/tests/integration/`` resolve these fixtures.
"""
import os
import uuid

import pytest
from django.conf import settings
from django.test import override_settings

_CH_TABLES_TO_TRUNCATE = [
    "tracer_observation_span",
    "tracer_trace",
    "trace_session",
    "tracer_eval_logger",
    "spans",  # MV target; truncate to keep cross-test isolation
]


class _CHDriverAdapter:
    """Adapter around clickhouse_driver.Client exposing a ``command(sql, ...)``
    surface matching clickhouse_connect.

    The repo ships ``clickhouse-driver`` (native protocol, port 19000); the
    plan was written against ``clickhouse-connect`` (HTTP, port 18123) which
    isn't a runtime dep. Adapting here keeps the seeder generic without
    requiring a new dependency.
    """

    def __init__(self, native_client):
        self._client = native_client

    def command(self, sql, *args, **kwargs):
        rows = self._client.execute(sql)
        # Mirror clickhouse_connect.command: scalar -> scalar, otherwise rows.
        if isinstance(rows, list) and len(rows) == 1 and len(rows[0]) == 1:
            return rows[0][0]
        return rows

    def query(self, sql, *args, **kwargs):
        # Best-effort match for clickhouse_connect.query — returns rows list.
        # Used by smoke tests; production callers don't rely on the object
        # shape returned here.
        rows = self._client.execute(sql)

        class _R:
            def __init__(self, data):
                self.result_rows = data
                self.data = data

        return _R(rows)


@pytest.fixture(scope="session")
def ch_client():
    """Client to the test ClickHouse container (native protocol, port 19000).

    The wrapper exposes a ``.command()`` method so the rest of the suite can
    pretend it's talking to the clickhouse_connect HTTP client.
    """
    try:
        from clickhouse_driver import Client
    except ImportError:
        pytest.skip("clickhouse-driver not installed")
    ch = settings.CLICKHOUSE
    try:
        native = Client(
            host=ch.get("CH_HOST", "localhost"),
            port=int(os.environ.get("CH_PORT", ch.get("CH_PORT", "19000"))),
            user=ch.get("CH_USERNAME", "default"),
            password=ch.get("CH_PASSWORD", ""),
        )
        native.execute("SELECT 1")
        return _CHDriverAdapter(native)
    except Exception as exc:
        pytest.skip(f"ClickHouse not reachable for integration tests: {exc}")


@pytest.fixture(scope="session")
def ch_schema(ch_client):
    """Apply schema DDL once per session. Targets the database in settings.CLICKHOUSE['CH_DATABASE']."""
    from tracer.services.clickhouse.schema import get_all_schema_ddl

    db = settings.CLICKHOUSE["CH_DATABASE"]
    ch_client.command(f"CREATE DATABASE IF NOT EXISTS {db}")
    # Switch session default DB so unqualified table refs in the DDL resolve.
    try:
        ch_client.command(f"USE {db}")
    except Exception:
        pass
    for name, ddl in get_all_schema_ddl():
        rewritten = ddl.replace("futureagi.", f"{db}.")
        try:
            ch_client.command(rewritten)
        except Exception:
            # idempotent — table/view already exists from a previous session,
            # or DDL refers to dependencies that don't materialize here.
            pass
    return ch_client


@pytest.fixture
def clean_ch(ch_schema):
    """Truncate CH tables before AND after the test so cross-test state never leaks."""
    db = settings.CLICKHOUSE["CH_DATABASE"]
    for tbl in _CH_TABLES_TO_TRUNCATE:
        try:
            ch_schema.command(f"TRUNCATE TABLE {db}.{tbl}")
        except Exception:
            pass
    yield ch_schema
    for tbl in _CH_TABLES_TO_TRUNCATE:
        try:
            ch_schema.command(f"TRUNCATE TABLE {db}.{tbl}")
        except Exception:
            pass


@pytest.fixture
def ch_routes_on():
    """Override CH route settings so list endpoints hit ClickHouse, not Postgres.

    ``CH_ENABLED=True`` is required because ``is_clickhouse_enabled()`` short-
    circuits to False otherwise. We also force-reset the lazily-cached
    ``_clickhouse_client`` so the new connection settings (CH_HOST=localhost,
    CH_PORT=19000) take effect — production tooling caches a module-level
    client that may be misconfigured if Django apps initialized before this
    test process picked up the right env vars.
    """
    routes = {
        **settings.CLICKHOUSE,
        "CH_HOST": os.environ.get("CH_HOST", "localhost"),
        "CH_PORT": os.environ.get("CH_PORT", "19000"),
        "CH_USERNAME": os.environ.get("CH_USERNAME", "default"),
        "CH_PASSWORD": os.environ.get("CH_PASSWORD", ""),
        "CH_DATABASE": os.environ.get("CH_DATABASE", "test_tfc"),
        "CH_ENABLED": True,
        "CH_ROUTE_SPAN_LIST": "clickhouse",
        "CH_ROUTE_TRACE_LIST": "clickhouse",
        "CH_ROUTE_TRACE_OF_SESSION_LIST": "clickhouse",
        "CH_ROUTE_SESSION_LIST": "clickhouse",
        "CH_ROUTE_VOICE_CALL_LIST": "clickhouse",
        "CH_SHADOW_MODE": False,
    }
    from tracer.services.clickhouse import client as ch_client_module

    prior_client = ch_client_module._clickhouse_client
    ch_client_module._clickhouse_client = None
    try:
        with override_settings(CLICKHOUSE=routes):
            yield
    finally:
        # Drop any test-scoped client so the next test reads fresh settings.
        ch_client_module._clickhouse_client = prior_client


@pytest.fixture
def dual_writer(clean_ch, db):
    """Per-test seeder — retained for the smoke test only; real suites use
    ``seeded_corpus`` (session-scoped)."""
    from tracer.tests.integration._seed import DualWriter

    return DualWriter(ch=clean_ch, ch_database=settings.CLICKHOUSE["CH_DATABASE"])


@pytest.fixture(scope="session")
def integration_setup(django_db_setup, django_db_blocker, ch_schema):
    """Session-scoped: commit org/user/workspace/project + seed corpus once,
    outside the per-test transaction. Returns SimpleNamespace."""
    from types import SimpleNamespace

    from accounts.models.organization import Organization
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.user import User
    from accounts.models.workspace import Workspace, WorkspaceMembership
    from model_hub.models.ai_model import AIModel
    from tfc.constants.levels import Level
    from tfc.constants.roles import OrganizationRoles
    from tfc.middleware.workspace_context import (
        clear_workspace_context,
        set_workspace_context,
    )
    from tracer.models.project import Project
    from tracer.tests.integration._seed import DualWriter

    db_name = settings.CLICKHOUSE["CH_DATABASE"]

    # Fresh CH state at session start.
    for tbl in _CH_TABLES_TO_TRUNCATE:
        try:
            ch_schema.command(f"TRUNCATE TABLE {db_name}.{tbl}")
        except Exception:
            pass

    with django_db_blocker.unblock():
        clear_workspace_context()
        org = Organization.objects.create(
            name=f"int_test_org_{uuid.uuid4().hex[:8]}"
        )
        set_workspace_context(organization=org)
        user = User.objects.create_user(
            email=f"integration-{uuid.uuid4().hex[:8]}@futureagi.com",
            password="testpassword123",
            name="Integration Test User",
            organization=org,
            organization_role=OrganizationRoles.OWNER,
        )
        OrganizationMembership.no_workspace_objects.get_or_create(
            user=user,
            organization=org,
            defaults={
                "role": OrganizationRoles.OWNER,
                "level": Level.OWNER,
                "is_active": True,
            },
        )
        ws = Workspace.objects.create(
            name="Integration Test Workspace",
            organization=org,
            is_default=True,
            is_active=True,
            created_by=user,
        )
        org_membership = OrganizationMembership.no_workspace_objects.get(
            user=user, organization=org
        )
        WorkspaceMembership.no_workspace_objects.get_or_create(
            user=user,
            workspace=ws,
            defaults={
                "role": "Workspace Owner",
                "level": Level.OWNER,
                "is_active": True,
                "organization_membership": org_membership,
            },
        )
        set_workspace_context(workspace=ws, organization=org, user=user)

        project = Project.objects.create(
            name="Integration Test Observe",
            organization=org,
            workspace=ws,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
            metadata={},
            session_config=[
                {"id": "session_input", "name": "Session Input", "is_visible": True},
            ],
        )

        writer = DualWriter(ch=ch_schema, ch_database=db_name)
        counts = writer.seed_base_corpus(project=project)

    snapshot = SimpleNamespace(
        organization=org,
        workspace=ws,
        user=user,
        project=project,
        rows=writer.seeded,
        eval_config_id=writer.eval_config_id,
        annotation_label_id=writer.annotation_label_id,
        choice_eval_config_id=writer.choice_eval_config_id,
        counts=counts,
    )
    yield snapshot

    with django_db_blocker.unblock():
        try:
            org.delete()
        except Exception:
            pass


# Per-test wrappers — each requests ``db`` so per-test writes roll back.

@pytest.fixture
def organization(integration_setup, db):
    return integration_setup.organization


@pytest.fixture
def workspace(integration_setup, db, organization):
    from tfc.middleware.workspace_context import (
        clear_workspace_context,
        set_workspace_context,
    )

    clear_workspace_context()
    set_workspace_context(
        workspace=integration_setup.workspace,
        organization=organization,
        user=integration_setup.user,
    )
    yield integration_setup.workspace
    clear_workspace_context()


@pytest.fixture
def user(integration_setup, db):
    return integration_setup.user


@pytest.fixture
def observe_project(integration_setup, db):
    return integration_setup.project


@pytest.fixture
def seeded_corpus(integration_setup, db):
    return integration_setup


@pytest.fixture(scope="session")
def voice_integration_setup(django_db_setup, django_db_blocker, ch_schema, integration_setup):
    """Session-scoped voice-only project + corpus for voiceCalls cases."""
    from types import SimpleNamespace

    from model_hub.models.ai_model import AIModel
    from tracer.models.project import Project
    from tracer.tests.integration._seed import DualWriter

    db_name = settings.CLICKHOUSE["CH_DATABASE"]

    with django_db_blocker.unblock():
        voice_project = Project.objects.create(
            name="Integration Test Voice",
            organization=integration_setup.organization,
            workspace=integration_setup.workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
            metadata={},
            session_config=[
                {"id": "session_input", "name": "Session Input", "is_visible": True},
            ],
        )
        writer = DualWriter(ch=ch_schema, ch_database=db_name)
        counts = writer.seed_voice_corpus(project=voice_project)

    snapshot = SimpleNamespace(
        organization=integration_setup.organization,
        workspace=integration_setup.workspace,
        user=integration_setup.user,
        project=voice_project,
        rows=writer.seeded,
        eval_config_id=writer.eval_config_id,
        annotation_label_id=writer.annotation_label_id,
        choice_eval_config_id=writer.choice_eval_config_id,
        counts=counts,
    )
    yield snapshot

    with django_db_blocker.unblock():
        try:
            voice_project.delete()
        except Exception:
            pass


@pytest.fixture
def voice_corpus(voice_integration_setup, db):
    return voice_integration_setup


@pytest.fixture
def custom_eval_config_factory(db, eval_template):
    """Factory that creates an extra CustomEvalConfig per call."""
    from tracer.models.custom_eval_config import CustomEvalConfig

    def _make(project):
        return CustomEvalConfig.objects.create(
            project=project,
            eval_template=eval_template,
            name=f"extra_eval_{uuid.uuid4().hex[:6]}",
            config={"threshold": 0.8},
            mapping={"input": "input", "output": "output"},
            filters={},
        )

    return _make


def test_ch_routes_on_flips_setting(ch_routes_on):
    """Sanity: the override actually flips the route to clickhouse for the test scope."""
    assert settings.CLICKHOUSE["CH_ROUTE_SPAN_LIST"] == "clickhouse"
