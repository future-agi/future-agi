"""Real PostgreSQL CAS with deliberately lagging, independent databases.

No ORM, queryset, database router, or checkpoint writer is mocked. The replica
contains a real table with the model's column types but no FK constraints; its
provider row is copied once and stays stale. Only ObservabilityProvider is opted
into replica routing, so related objects remain on the real primary DB.
"""

from collections import Counter
from contextlib import ExitStack, nullcontext
from copy import deepcopy
from datetime import timedelta
from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.conf import settings as django_settings
from django.db import connections
from django.utils import timezone

from simulate.models.agent_definition import AgentDefinition
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.routers import ReadReplicaRouter, force_primary
from tracer.models.observability_provider import ObservabilityProvider
from tracer.models.project import Project
from tracer.services.observability_providers import ObservabilityService
from tracer.tests.test_retell_poll_orchestration import _page
from tracer.utils import observability_provider as op

pytestmark = [
    pytest.mark.django_db(databases=["default", "replica"]),
    pytest.mark.integration,
]


@pytest.fixture(scope="module")
def django_db_setup(django_db_setup, django_db_blocker):
    """Add an independent replica only after the normal test DB is ready.

    Overriding pytest-django's fixture here keeps registration ahead of each
    test's transaction setup without changing session-wide database creation.
    The unique database is always dropped, including under --reuse-db/xdist.
    """
    assert "replica" not in django_settings.DATABASES
    assert "replica" not in connections
    primary = connections["default"]
    replica_config = deepcopy(primary.settings_dict)
    replica_name = f"test_retell_replica_{uuid4().hex}"
    replica_config["NAME"] = replica_name
    replica_config["TEST"] = {**replica_config["TEST"], "NAME": replica_name}
    quoted_name = primary.ops.quote_name(replica_name)
    with django_db_blocker.unblock(), primary.cursor() as cursor:
        cursor.execute(f"CREATE DATABASE {quoted_name}")
    try:
        with pytest.MonkeyPatch.context() as patch:
            # Django caches its connection settings separately from settings.
            # Restore both mappings and remove the cached wrapper on teardown.
            patch.setitem(django_settings.DATABASES, "replica", replica_config)
            patch.setitem(connections.databases, "replica", replica_config)
            replica = connections["replica"]
            try:
                yield
            finally:
                replica.close()
                del connections["replica"]
    finally:
        with django_db_blocker.unblock(), primary.cursor() as cursor:
            cursor.execute(f"DROP DATABASE {quoted_name}")


@pytest.fixture
def lagging_provider(user, workspace, settings):
    clear_workspace_context()
    project = Project.objects.using("default").create(
        name="Synthetic replica QA",
        organization=workspace.organization,
        workspace=workspace,
        model_type="GenerativeLLM",
        trace_type="observe",
    )
    provider = ObservabilityProvider.objects.using("default").create(
        project=project,
        organization=workspace.organization,
        workspace=workspace,
        provider="retell",
        enabled=True,
        last_fetched_at=timezone.now() - timedelta(hours=1),
        poll_state={"retell": {"bootstrapped": True}, "other": {"keep": True}},
    )
    AgentDefinition.objects.using("default").create(
        agent_name="Synthetic healthy Retell",
        agent_type="voice",
        provider="retell",
        organization=workspace.organization,
        workspace=workspace,
        inbound=True,
        observability_provider=provider,
        assistant_id="synthetic-retell-assistant",
        api_key="synthetic-retell-key",
    )
    primary, replica = connections["default"], connections["replica"]
    with primary.cursor() as cursor:
        cursor.execute("SELECT current_database()")
        primary_name = cursor.fetchone()[0]
    with replica.cursor() as cursor:
        cursor.execute("SELECT current_database()")
        replica_name = cursor.fetchone()[0]
        assert primary_name != replica_name, "Must not use Django TEST.MIRROR"
        # ReadReplicaRouter intentionally disallows replica migrations. Build
        # only the real provider table read by the code under test, using the
        # unchanged model's database column types. FK rows are on primary.
        columns = ", ".join(
            f"{replica.ops.quote_name(field.column)} {field.db_type(replica)}"
            for field in ObservabilityProvider._meta.concrete_fields
        )
        cursor.execute(
            f"CREATE TABLE IF NOT EXISTS {replica.ops.quote_name(provider._meta.db_table)} ({columns})"
        )
    copied = deepcopy(provider)
    copied.save(using="replica", force_insert=True)
    settings.READ_REPLICA_OPT_IN = ["ObservabilityProvider"]
    assert ReadReplicaRouter().db_for_read(ObservabilityProvider) == "replica"
    assert ReadReplicaRouter().db_for_write(ObservabilityProvider) == "default"
    assert ObservabilityService._resolve_retell_key(provider) == (
        "synthetic-retell-key",
        "synthetic-retell-assistant",
    )
    print("INDEPENDENT_DATABASES", primary_name, replica_name)
    return provider


def state(provider, alias):
    return (
        ObservabilityProvider.all_objects.using(alias)
        .values_list(
            "poll_state",
            flat=True,
        )
        .get(pk=provider.pk)
    )


class SQLTrace:
    def __init__(self):
        self.operations = []

    def __call__(self, execute, sql, params, many, context):
        if "tracer_observability_provider" in sql:
            self.operations.append((context["connection"].alias, sql.split()[0]))
        return execute(sql, params, many, context)

    def collect(self):
        stack = ExitStack()
        for alias in ("default", "replica"):
            stack.enter_context(connections[alias].execute_wrapper(self))
        return stack


@pytest.mark.parametrize(
    "pin_primary", [False, True], ids=["replica-routing", "primary-pinned-control"]
)
def test_cas_reads_current_primary_and_preserves_health(lagging_provider, pin_primary):
    provider = lagging_provider
    old = state(provider, "replica")
    health = {
        "status": "misconfigured",
        "reason_code": "missing_key",
        "failure_count": 1,
        "first_failed_at": timezone.now().isoformat(),
        "last_failed_at": timezone.now().isoformat(),
        "next_retry_at": (timezone.now() + timedelta(minutes=10)).isoformat(),
    }
    current = {
        **old,
        "retell": {"bootstrapped": True, "checkpoint": "S1"},
        "configuration_health": health,
    }
    ObservabilityProvider.all_objects.using("default").filter(pk=provider.pk).update(
        poll_state=current
    )
    next_retell = {"bootstrapped": True, "checkpoint": "S2"}
    trace = SQLTrace()
    with trace.collect(), force_primary() if pin_primary else nullcontext():
        result = op._write_retell_state(provider.pk, next_retell)
    primary = state(provider, "default")
    observed = {
        "result": result,
        "SQL": dict(Counter(trace.operations)),
        "primary": primary,
        "replica": state(provider, "replica"),
    }
    print("STALE_CAS_OBSERVED", observed)
    assert result is True, observed
    assert primary["retell"] == next_retell, observed
    assert primary["configuration_health"] == health, observed
    assert primary["other"] == old["other"], observed
    assert state(provider, "replica") == old


@pytest.mark.parametrize(
    "pin_primary", [False, True], ids=["replica-routing", "primary-pinned-control"]
)
def test_two_consecutive_checkpoints_do_not_lose_second(lagging_provider, pin_primary):
    provider = lagging_provider
    old = state(provider, "replica")
    first = {"bootstrapped": True, "checkpoint": "S1"}
    second = {"bootstrapped": True, "checkpoint": "S2"}
    trace = SQLTrace()
    with trace.collect(), force_primary() if pin_primary else nullcontext():
        outcomes = (
            op._write_retell_state(provider.pk, first),
            op._write_retell_state(provider.pk, second),
        )
    observed = {
        "outcomes": outcomes,
        "SQL": dict(Counter(trace.operations)),
        "primary": state(provider, "default"),
        "replica": state(provider, "replica"),
    }
    print("CONSECUTIVE_CAS_OBSERVED", observed)
    assert outcomes == (True, True), observed
    assert state(provider, "default")["retell"] == second
    assert state(provider, "replica") == old


@pytest.mark.parametrize(
    "pin_primary", [False, True], ids=["replica-routing", "primary-pinned-control"]
)
def test_scheduled_two_page_poll_checkpoints_successfully(
    lagging_provider, monkeypatch, pin_primary
):
    provider = lagging_provider
    now = timezone.now()
    monkeypatch.setattr(op.timezone, "now", lambda: now)
    exported = []

    def call(number):
        return {
            "call_id": f"synthetic-retell-{number}",
            "call_status": "ended",
            "start_timestamp": int((now - timedelta(minutes=10)).timestamp() * 1000),
            "end_timestamp": int((now - timedelta(minutes=9)).timestamp() * 1000),
        }

    pages = Mock(
        side_effect=[
            _page([call(1)], has_more=True, next_key="synthetic-cursor-1"),
            _page([call(2)], has_more=False),
        ]
    )
    monkeypatch.setattr(ObservabilityService, "fetch_retell_page", pages)

    def export(span, provider_type, call_id):
        exported.append(call_id)
        return 1

    monkeypatch.setattr(op, "_export_provider_call_to_collector", export)
    trace = SQLTrace()
    with trace.collect(), force_primary() if pin_primary else nullcontext():
        outcome = op.fetch_logs_for_provider(
            provider.pk,
            scheduled=True,
            start_time=None,
            end_time=None,
        )
    primary = ObservabilityProvider.all_objects.using("default").get(pk=provider.pk)
    observed = {
        "outcome": outcome,
        "exported": exported,
        "pages_fetched": pages.call_count,
        "SQL": dict(Counter(trace.operations)),
        "primary_state": primary.poll_state,
        "watermark_advanced": primary.last_fetched_at > provider.last_fetched_at,
    }
    print("TWO_PAGE_POLL_OBSERVED", observed)
    # Prove both real normalization/span paths completed before checking CAS.
    assert exported == ["synthetic-retell-1", "synthetic-retell-2"], observed
    assert pages.call_count == 2, observed
    assert outcome == op.StoreOutcome(1, 0, 0), observed
    assert "window" not in primary.poll_state["retell"], observed
    assert primary.last_fetched_at > provider.last_fetched_at, observed
