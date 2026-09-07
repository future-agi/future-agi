"""Database evidence must precede automatic installation identity allocation."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse.v2.property_catalog import installation_bootstrap as ib
from tracer.services.clickhouse.v2.property_catalog.activation import ManifestStreamRole
from tracer.services.clickhouse.v2.property_catalog.installation_identity import (
    IDENTITY_FILENAME,
    InstallationIdentityError,
    load_identity,
)
from tracer.tests.test_property_catalog_physical_snapshot_lifecycle import (
    _PhysicalSnapshot,
)

DATABASE = "property_catalog_dev_unit"
DESTINATION = {
    "environment": "development",
    "target_database": DATABASE,
    "candidate_topic": "test.candidates.v1",
    "ordered_topic": "test.ordered.v1",
}


class Client:
    database = DATABASE

    def __init__(self, *, replicated=False, plans=(), occupied=()):
        engine = "ReplicatedMergeTree" if replicated else "MergeTree"
        self.metadata = [(table, engine) for table in ib.PROPERTY_CATALOG_TABLES]
        self.replicas = [(table, 0, 0, 0, 3, 3) for table, _ in self.metadata]
        self.plans = plans
        self.occupied = set(occupied)
        self.conflicts = []
        self.calls = []
        self.closed = False

    def execute_read(self, sql, params, *, timeout_ms, settings):
        assert sql.startswith("SELECT ") and " FINAL" not in sql
        assert 0 < timeout_ms <= 30_000
        assert settings["readonly"] == 2
        self.calls.append((sql, params, timeout_ms))
        if "FROM system.tables" in sql:
            assert "name IN %(tables)s" in sql
            assert set(params["tables"]) == set(ib.PROPERTY_CATALOG_TABLES)
            rows = self.metadata
        elif "FROM system.replicas" in sql:
            assert "table IN %(tables)s" in sql
            assert set(params["tables"]) == set(ib.PROPERTY_CATALOG_TABLES)
            rows = self.replicas
        elif "ARRAY JOIN" in sql:
            rows = self.plans
        elif "catalog_epoch !=" in sql:
            rows = self.conflicts
        else:
            rows = [(1,)] if sql.split(" FROM ")[1].split()[0] in self.occupied else []
        return rows, [], {}

    def close(self):
        self.closed = True


def existing_plan():
    plan = _PhysicalSnapshot().plan
    hot = next(s for s in plan.streams if s.role is ManifestStreamRole.HOT_VALUES)
    return (
        plan.catalog_epoch,
        plan.projection_version,
        hot.producer_stream_id,
        (plan.canonical_json, plan.sha256),
    )


@pytest.mark.parametrize("replicated", [False, True])
def test_empty_schema_allocates_identity_only_after_all_six_tables_checked(replicated):
    client = Client(replicated=replicated)
    identity = ib.inspect_installation(client, **DESTINATION)
    assert identity.catalog_epoch == identity.projection_version == 1
    probes = [sql for sql, _, _ in client.calls if sql.startswith("SELECT 1 FROM")]
    assert len(probes) == 6
    assert len(set(probes)) == 6


@pytest.mark.parametrize("table", ib.PROPERTY_CATALOG_TABLES)
def test_any_unidentified_existing_data_prevents_fresh_identity(table):
    client = Client(occupied=(f"{DATABASE}.{table}",))
    with pytest.raises(InstallationIdentityError, match="non-empty"):
        ib.inspect_installation(client, **DESTINATION)


def test_existing_physical_snapshot_preserves_projection_and_producer():
    evidence = existing_plan()
    client = Client(plans=[evidence])
    identity = ib.inspect_installation(client, **DESTINATION)
    assert identity.projection_version == 3
    assert identity.producer_stream_id == evidence[2]
    assert not any(
        sql.startswith(f"SELECT 1 FROM {DATABASE}.property_catalog_values")
        for sql, _, _ in client.calls
    )


def test_mixed_runtime_identities_are_not_resolved_by_taking_maximum():
    first = existing_plan()
    client = Client(plans=[first, (2, *first[1:])])
    with pytest.raises(InstallationIdentityError, match="multiple runtime"):
        ib.inspect_installation(client, **DESTINATION)


@pytest.mark.parametrize("broken", ["digest", "projection", "other_history"])
def test_adoption_requires_consistent_control_evidence(broken):
    row = list(existing_plan())
    if broken == "digest":
        row[3] = (row[3][0], "0" * 64)
    elif broken == "projection":
        row[1] = 2
    client = Client(plans=[tuple(row)])
    if broken == "other_history":
        client.conflicts = [(1,)]
    with pytest.raises(InstallationIdentityError):
        ib.inspect_installation(client, **DESTINATION)


@pytest.mark.parametrize("bad", ["missing", "readonly", "expired", "queue", "inactive"])
def test_replication_must_be_caught_up_before_inspection(bad):
    client = Client(replicated=True)
    row = list(client.replicas[0])
    if bad == "missing":
        client.replicas.pop()
    else:
        row[{"readonly": 1, "expired": 2, "queue": 3, "inactive": 4}[bad]] = 1
        client.replicas[0] = tuple(row)
    with pytest.raises(InstallationIdentityError, match="replica evidence"):
        ib.inspect_installation(client, **DESTINATION)


@pytest.mark.parametrize("bad", ["missing_table", "distributed", "database"])
def test_schema_or_connection_mismatch_never_initializes(bad):
    client = Client()
    if bad == "missing_table":
        client.metadata.pop()
    elif bad == "distributed":
        client.metadata[0] = (client.metadata[0][0], "Distributed")
    else:
        client.database = "default"
    with pytest.raises(InstallationIdentityError):
        ib.inspect_installation(client, **DESTINATION)


def test_overall_inspection_deadline_is_not_restarted_for_each_statement():
    client = Client()
    ticks = iter([0, 1, 20, 21, 61])
    with pytest.raises(InstallationIdentityError, match="timed out"):
        ib.inspect_installation(client, monotonic=lambda: next(ticks), **DESTINATION)
    assert len(client.calls) == 2


def resolve(tmp_path, factory, *, readonly=False, **overrides):
    settings = SimpleNamespace(
        TEST_WRITE_CH_HOST="isolated",
        TEST_WRITE_CH_PORT=9000,
        TEST_WRITE_CH_USER="reader",
        TEST_WRITE_CH_PASSWORD="fixture-only",
    )
    return ib.resolve_installation(
        settings_object=settings,
        prefix="TEST_",
        revision_fence_file=str(tmp_path / "revision-fence-v2.json"),
        client_factory=factory,
        readonly=readonly,
        **(DESTINATION | overrides),
    )


def test_initialize_once_then_restart_without_querying_database(tmp_path):
    client = Client(plans=[existing_plan()])
    options = []

    def factory(**kwargs):
        options.append(kwargs)
        return client

    identity = resolve(tmp_path, factory)
    assert identity.projection_version == 3 and client.closed
    assert options[0]["server_enforced_readonly"] is True
    assert resolve(tmp_path, factory) == identity
    assert resolve(tmp_path, factory, readonly=True) == identity
    assert len(options) == 1
    assert load_identity(tmp_path / IDENTITY_FILENAME) == identity
    with pytest.raises(InstallationIdentityError, match="ordered_topic"):
        resolve(tmp_path, factory, ordered_topic="other.ordered")


def test_failed_probe_closes_client_and_preserves_absence(tmp_path):
    client = Client()
    client.metadata.clear()
    with pytest.raises(InstallationIdentityError):
        resolve(tmp_path, lambda **_: client)
    assert client.closed and not (tmp_path / IDENTITY_FILENAME).exists()


def test_status_does_not_allocate_identity_or_query_database(tmp_path):
    def forbidden(**_):
        pytest.fail("status must not initialize")

    with pytest.raises(InstallationIdentityError):
        resolve(tmp_path, forbidden, readonly=True)
    assert not (tmp_path / IDENTITY_FILENAME).exists()


def test_legacy_settings_can_confirm_but_cannot_override_identity(tmp_path):
    identity = resolve(tmp_path, lambda **_: Client(plans=[existing_plan()]))
    ib.require_legacy_identity(
        identity, epoch=1, projection=3, producer=identity.producer_stream_id
    )
    with pytest.raises(InstallationIdentityError, match="conflict"):
        ib.require_legacy_identity(
            replace(identity, projection_version=1),
            epoch=1,
            projection=3,
            producer=identity.producer_stream_id,
        )
