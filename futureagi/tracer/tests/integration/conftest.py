"""Integration suite fixtures (sub-package scoped).

These do not leak into the parent ``tracer/tests/`` namespace — only tests
collected from ``futureagi/tracer/tests/integration/`` resolve these fixtures.
"""

import uuid

import pytest
from django.conf import settings
from django.test import override_settings

_CH_TABLES_TO_TRUNCATE = [
    "spans",
    "traces",
    "trace_sessions",
    "tracer_eval_logger",
    "tracer_eval_logger_v2",
    "model_hub_score",
    "usage_apicalllog",
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
            port=int(ch.get("CH_PORT", "19000")),
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
    for _name, ddl in get_all_schema_ddl():
        rewritten = ddl.replace("futureagi.", f"{db}.")
        try:
            ch_client.command(rewritten)
        except Exception:
            # idempotent — table/view already exists from a previous session,
            # or DDL refers to dependencies that don't materialize here.
            pass
    # The eval filter subqueries hardcode ``tracer_eval_logger`` with the v2
    # column shape (``is_deleted``), but the legacy DDL above and the django
    # boot hook create that name CDC-shaped. Reshape it to a structural clone
    # of ``tracer_eval_logger_v2`` (applied by the root conftest's v2 schema
    # pass) so the whole suite reads v2-shaped eval rows. The rollup MVs
    # attached to the old name expect CDC columns and would fail every
    # insert — drop them (the list endpoints under test never read them).
    for mv in ("eval_metrics_hourly_mv", "eval_per_config_mv"):
        ch_client.command(f"DROP VIEW IF EXISTS {db}.{mv}")
    # Work-item columns the eval score phase reads — POST_DDL_ALTERS adds them
    # to the legacy table only; no v2 schema file adds them to _v2 yet.
    for col_ddl in (
        "status LowCardinality(String) DEFAULT 'completed'",
        "config_hash Nullable(String)",
        "skipped_reason Nullable(String)",
        "attempts Int32 DEFAULT 0",
    ):
        ch_client.command(
            f"ALTER TABLE {db}.tracer_eval_logger_v2 ADD COLUMN IF NOT EXISTS {col_ddl}"
        )
    ch_client.command(f"DROP TABLE IF EXISTS {db}.tracer_eval_logger")
    # Coverage boundary: this rebuilds tracer_eval_logger as a v2 clone with CDC
    # columns bolted on (below) — a hybrid shape that exists NOWHERE in prod. The
    # matrix therefore validates filter SEMANTICS but cannot catch prod-shape
    # missing-column failures (the exact bug class this PR's eval-filter fix
    # addresses).
    ch_client.command(
        f"CREATE TABLE {db}.tracer_eval_logger AS {db}.tracer_eval_logger_v2"
    )
    # ``tracer_eval_logger`` serves two readers with different shapes: the eval
    # *filter* subqueries use the v2 shape (``is_deleted``, above), but the
    # traces/voice eval-metrics *enrichment* (avg_score/pass_rate) reads it as
    # the PeerDB CDC mirror it is in prod — ``WHERE _peerdb_is_deleted = 0 AND
    # (deleted = 0 OR deleted IS NULL)`` (see query_builders/eval_metrics.py,
    # schema.py:CDC_EVAL_LOGGER). Add those CDC columns (defaulting to
    # not-deleted) so both readers resolve against the seeded rows.
    for col_ddl in (
        "_peerdb_is_deleted UInt8 DEFAULT 0",
        "deleted UInt8 DEFAULT 0",
    ):
        ch_client.command(
            f"ALTER TABLE {db}.tracer_eval_logger ADD COLUMN IF NOT EXISTS {col_ddl}"
        )
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
        "CH_ENABLED": True,
        "CH_ROUTE_SPAN_LIST": "clickhouse",
        "CH_ROUTE_TRACE_LIST": "clickhouse",
        "CH_ROUTE_TRACE_OF_SESSION_LIST": "clickhouse",
        "CH_ROUTE_SESSION_LIST": "clickhouse",
        "CH_ROUTE_VOICE_CALL_LIST": "clickhouse",
        "CH_SHADOW_MODE": False,
    }
    # Pin the v1↔v2 dispatch to the v2 builders — the suite tests the v2 read
    # path only, and tfc.settings.test's CLICKHOUSE_V2 carries no routing keys
    # (which get_routing_mode resolves as v1-only). V2_ONLY (not V2_PRIMARY)
    # so no v1 shadow query runs — the shadow hits the v2-shaped eval table
    # with v1 CDC columns (_peerdb_is_deleted) and doubles CH load.
    # Pin ONLY the four list builders to v2. The EVAL_METRICS enrichment
    # (avg_score/pass_rate) is v1-coupled even in the v2 trace/voice builders:
    # it queries the v1-shaped `tracer_eval_logger` (`_peerdb_is_deleted`), which
    # carries the v2 shape in the test CH → Code 47. Routing EVAL_METRICS to v2
    # makes eval-config discovery succeed, which then *triggers* that broken
    # enrichment on every traces/voice request (breaking non-eval cases too).
    # Leaving it unrouted keeps the enrichment dormant unless an eval filter
    # forces discovery.
    # NOTE: the endpoint xfail (test_list_endpoints_filter_count) triggers ONLY
    # on ``case.contract_gap``, and no eval/has_eval traces/voice case currently
    # carries one — so any v1/v2-coupling breakage on those cases would surface
    # as a hard failure, not an xfail. Not covered as xfail today.
    routes_v2 = {
        **getattr(settings, "CLICKHOUSE_V2", {}),
        "QUERY_TYPES_V2_ONLY": "SPAN_LIST,TRACE_LIST,SESSION_LIST,VOICE_CALL_LIST",
    }
    from tracer.services.clickhouse import client as ch_client_module

    prior_client = ch_client_module._clickhouse_client
    ch_client_module._clickhouse_client = None
    try:
        with override_settings(CLICKHOUSE=routes, CLICKHOUSE_V2=routes_v2):
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
        org = Organization.objects.create(name=f"int_test_org_{uuid.uuid4().hex[:8]}")
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
        pf_eval_config_id=writer.pf_eval_config_id,
        text_label_id=writer.text_label_id,
        thumbs_label_id=writer.thumbs_label_id,
        categorical_label_id=writer.categorical_label_id,
        annotator_user_id=writer.annotator_user_id,
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
def voice_integration_setup(
    django_db_setup, django_db_blocker, ch_schema, integration_setup
):
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
        pf_eval_config_id=writer.pf_eval_config_id,
        text_label_id=writer.text_label_id,
        thumbs_label_id=writer.thumbs_label_id,
        categorical_label_id=writer.categorical_label_id,
        annotator_user_id=writer.annotator_user_id,
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
