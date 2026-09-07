"""Installed-admission proof stays fresh without repeating startup publication."""

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from uuid import UUID

import pytest

from tracer.services.clickhouse.v2.property_catalog import write_admission as subject
from tracer.services.clickhouse.v2.property_catalog.installation_identity import (
    IDENTITY_FILENAME,
)
from tracer.services.clickhouse.v2.property_catalog.keeper_membership import (
    KeeperMembershipError,
)
from tracer.tests.test_property_catalog_write_admission import (
    Probe,
    admit,
    installation,
)


def lane(tmp_path, count=1):
    identity = installation("production" if count == 3 else "development")
    probe = Probe(count)
    admission = admit(tmp_path, probe, identity=identity)
    probe.calls.clear()

    def check(**kwargs):
        return subject.reattest_catalog_writes(
            tmp_path,
            identity=identity,
            admission=admission,
            connections=probe.connections,
            http_read=probe.http,
            **{"timeout_ms": 5000, **kwargs},
        )

    return probe, admission, check


@pytest.mark.parametrize("count", [1, 2, 3])
def test_fresh_inventory_and_keeper_without_publication_or_route_discovery(
    tmp_path, monkeypatch, count
):
    probe, admission, check = lane(tmp_path, count)
    path = tmp_path / subject.WRITE_ADMISSION_FILENAME
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    monkeypatch.setattr(subject.os, "fsync", lambda *a: pytest.fail("startup fsync"))
    monkeypatch.setattr(subject.fcntl, "flock", lambda *a: pytest.fail("startup lock"))
    for _ in range(2):
        probe.calls.clear()
        check()
        inventory_sql = (
            subject._STANDALONE_INVENTORY_SQL
            if admission.family == "standalone"
            else subject._INVENTORY_SQL
        )
        for member in admission.members:
            calls = [c for c in probe.calls if c[1] == member.name]
            inventory = [c for c in calls if c[2] == inventory_sql]
            assert [c[0] for c in inventory] == (
                ["native", "http", "native"]
                if count == 1
                else ["native", "http", "native", "http"]
            )
            assert sum("system.zookeeper" in c[2] for c in calls) == (
                3 if count > 1 else 0
            )
            assert not any("getServerPort" in c[2] for c in calls)
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_standalone_sql_changes_only_replica_fields_and_join():
    replacements = {
        "ifNull(r.zookeeper_path,'')": "''",
        "ifNull(r.zookeeper_name,'')": "''",
        "ifNull(r.replica_name,'')": "''",
        "arraySort(mapKeys(ifNull(r.replica_is_active,map())))": "CAST([], 'Array(String)')",
        "ifNull(r.total_replicas,0)": "toUInt32(0)",
        "ifNull(r.active_replicas,0)": "toUInt32(0)",
        "ifNull(r.is_readonly,0)": "toUInt8(0)",
        "ifNull(r.is_session_expired,0)": "toUInt8(0)",
        "FROM system.tables AS t LEFT JOIN system.replicas AS r\nON t.database=r.database AND t.name=r.table": "FROM system.tables AS t",
    }
    expected = subject._INVENTORY_SQL
    for original, replacement in replacements.items():
        assert expected.count(original) == 1
        expected = expected.replace(original, replacement)
    assert subject._STANDALONE_INVENTORY_SQL == expected
    assert "JOIN" not in expected and "system.replicas" not in expected
    assert expected.startswith("SELECT ")


def test_all_standalone_fields_and_three_fresh_observations_are_preserved(tmp_path):
    probe, admission, check = lane(tmp_path)
    expected = deepcopy(probe.rows[admission.members[0].name])
    assert len(expected) == 7
    assert all(set(row) == subject._INVENTORY_FIELDS for row in expected)
    observed = []

    def record(kind, name, rows):
        observed.append((kind, name, deepcopy(rows)))
        return rows

    probe.alter = record
    check()
    assert observed == [
        (kind, admission.members[0].name, expected)
        for kind in ("native", "http", "native")
    ]
    assert all(c[2] == subject._STANDALONE_INVENTORY_SQL for c in probe.calls)


@pytest.mark.parametrize("phase", ["first_native", "http", "last_native"])
@pytest.mark.parametrize("table", sorted(subject._ENGINES))
def test_plain_to_replicated_engine_is_not_hidden_by_empty_replica_fields(
    tmp_path, phase, table
):
    probe, _, check = lane(tmp_path)
    installed = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in tmp_path.iterdir()
        if path.suffix == ".json"
    }
    native_reads = 0

    def change(kind, name, rows):
        nonlocal native_reads
        if kind == "native":
            native_reads += 1
        if (
            (phase == "first_native" and kind == "native" and native_reads == 1)
            or (phase == "http" and kind == "http")
            or (phase == "last_native" and kind == "native" and native_reads == 2)
        ):
            row = next(row for row in rows if row["name"] == table)
            row["engine"] = "Replicated" + row["engine"]
            # The no-join query still returns these constant empty fields.
            assert row["replica_names"] == [] and row["total_replicas"] == 0
        return rows

    probe.alter = change
    message = (
        "inventory changed"
        if phase == "last_native"
        else "not every admitted replica is registered, active and writable"
    )
    with pytest.raises(subject.WriteAdmissionError, match=message):
        check()
    assert all(c[2] == subject._STANDALONE_INVENTORY_SQL for c in probe.calls)
    assert {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in tmp_path.iterdir()
        if path.suffix == ".json"
    } == installed


@pytest.mark.parametrize("kind", ["native", "http"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("hostname", "another-node"),
        ("server_uuid", str(UUID(int=998))),
        ("database", "another_database"),
        ("database_uuid", str(UUID(int=998))),
        ("database_engine", "Ordinary"),
        ("name", "another_table"),
        ("uuid", str(UUID(int=998))),
        (
            "create_table_query",
            "CREATE TABLE unpinned (x UInt8) ENGINE=MergeTree ORDER BY x",
        ),
        ("create_sha256", "0" * 64),
    ],
)
def test_standalone_still_observes_actual_table_and_server_fields(
    tmp_path, kind, field, value
):
    probe, _, check = lane(tmp_path)

    def change(observed_kind, name, rows):
        if observed_kind == kind:
            rows[0][field] = value
        return rows

    probe.alter = change
    with pytest.raises(ValueError):
        check()


def test_standalone_rejects_changed_http_proof_username(tmp_path):
    probe, _, check = lane(tmp_path)

    def change(kind, name, rows):
        if kind == "http":
            rows[0]["username"] = "another_user"
        return rows

    probe.alter = change
    with pytest.raises(subject.WriteAdmissionError):
        check()


def test_standalone_query_selection_requires_validated_admission(tmp_path):
    probe, admission, check = lane(tmp_path, 3)
    # Bypass the frozen dataclass only to model a forged in-memory descriptor.
    object.__setattr__(admission, "family", "standalone")
    with pytest.raises(subject.WriteAdmissionError):
        check()
    assert not probe.calls


@pytest.mark.parametrize("kind", ["native", "http"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("server_uuid", str(UUID(int=998))),
        ("database_uuid", str(UUID(int=998))),
        ("uuid", str(UUID(int=998))),
        ("hostname", "another-node"),
        ("engine", "MergeTree"),
        ("readonly", 1),
        ("session_expired", 1),
        ("active_replicas", 2),
        ("replica_names", ["replica1", "replica2"]),
        ("keeper_path", "/foreign"),
        ("create_sha256", "0" * 64),
    ],
)
def test_every_call_rejects_changed_node_table_and_membership(
    tmp_path, kind, field, value
):
    probe, _, check = lane(tmp_path, 3)
    check()

    def alter(observed_kind, name, rows):
        if observed_kind == kind and name == "replica3":
            rows[0][field] = value
        return rows

    probe.alter = alter
    with pytest.raises(ValueError):
        check()


@pytest.mark.parametrize("count", [1, 3])
@pytest.mark.parametrize(
    "field,value", [("uuid", str(UUID(int=999))), ("username", "another_user")]
)
def test_native_change_after_http_observation_is_rejected(
    tmp_path, count, field, value
):
    probe, _, check = lane(tmp_path, count)

    def alter(kind, name, rows):
        if kind == "http":
            probe.rows[name][0][field] = value
        return rows

    probe.alter = alter
    with pytest.raises(subject.WriteAdmissionError, match="changed"):
        check()


@pytest.mark.parametrize("kind", ["native", "http"])
def test_keeper_is_not_a_schema_lock_and_is_bracketed_by_current_inventory(
    tmp_path, kind
):
    probe, _, check = lane(tmp_path, 3)
    keeper_seen = False

    def alter(observed_kind, name, rows):
        nonlocal keeper_seen
        keeper_seen |= observed_kind == "keeper"
        if keeper_seen and observed_kind == kind and name == "replica1":
            rows[0]["uuid"] = str(UUID(int=999))
        return rows

    probe.alter = alter
    with pytest.raises(subject.WriteAdmissionError, match="changed"):
        check()
    assert keeper_seen


def test_fresh_keeper_sessions_are_accepted_but_wrong_ephemeral_owner_is_not(tmp_path):
    probe, _, check = lane(tmp_path, 3)
    probe.sessions = {name: value + 100 for name, value in probe.sessions.items()}
    check()

    def alter(kind, name, rows):
        if kind == "keeper":
            rows[0]["owner"] = 999
        return rows

    probe.alter = alter
    with pytest.raises(KeeperMembershipError):
        check()


@pytest.mark.parametrize(
    "filename", [IDENTITY_FILENAME, subject.WRITE_ADMISSION_FILENAME]
)
@pytest.mark.parametrize("during_proof", [False, True])
def test_changed_or_missing_installed_state_is_never_recreated(
    tmp_path, filename, during_proof
):
    probe, _, check = lane(tmp_path)
    path = tmp_path / filename
    if during_proof:

        def alter(kind, name, rows):
            if kind == "http":
                path.write_bytes(b"changed\n")
            return rows

        probe.alter = alter
    else:
        path.unlink()
    with pytest.raises((subject.WriteAdmissionError, FileNotFoundError)):
        check()
    assert path.read_bytes() == b"changed\n" if during_proof else not path.exists()


def test_even_a_matching_descriptor_hash_cannot_admit_an_unpinned_create(tmp_path):
    probe, admission, _ = lane(tmp_path)
    original = probe.rows["replica1"][0]["create_table_query"]
    create = original.replace("index_granularity = 8192", "index_granularity = 4096")
    assert create != original
    fingerprint = hashlib.sha256(create.encode()).hexdigest()
    probe.rows["replica1"][0].update(
        create_table_query=create, create_sha256=fingerprint
    )
    document = json.loads(admission.encode())
    document["members"][0]["tables"][0]["create_sha256"] = fingerprint
    del document["topology_sha256"]
    document["topology_sha256"] = subject._sha(document)
    changed = subject.WriteAdmission.decode(subject._canonical(document) + b"\n")
    (tmp_path / subject.WRITE_ADMISSION_FILENAME).write_bytes(changed.encode())
    with pytest.raises(ValueError, match="pinned schema"):
        subject.reattest_catalog_writes(
            tmp_path,
            identity=installation(),
            admission=changed,
            connections=probe.connections,
            timeout_ms=5000,
            http_read=probe.http,
        )


def test_last_observation_cannot_overrun_the_whole_proof_deadline(
    tmp_path, monkeypatch
):
    probe, _, check = lane(tmp_path)
    now, native_count = [0.0], [0]
    monkeypatch.setattr(subject, "monotonic", lambda: now[0])

    def alter(kind, name, rows):
        if kind == "native":
            native_count[0] += 1
            if native_count[0] == 2:
                now[0] = 31.0
        return rows

    probe.alter = alter
    with pytest.raises(subject.WriteAdmissionError, match="timed out"):
        check()


@pytest.mark.parametrize(
    "mutation", ["tls", "database", "hostname", "duplicate", "missing"]
)
def test_connection_contract_cannot_silently_change(tmp_path, mutation):
    probe, _, check = lane(tmp_path)
    connection = probe.connections[0]
    if mutation == "database":
        connection.driver.database = "another_database"
    elif mutation == "tls":
        probe.connections = (replace(connection, http_scheme="http"),)
    elif mutation == "hostname":
        probe.connections = (replace(connection, expected_hostname="another-node"),)
    elif mutation == "duplicate":
        probe.connections *= 2
    else:
        probe.connections = ()
    with pytest.raises(subject.WriteAdmissionError):
        check()
    assert not probe.calls


def test_http_transport_remains_mandatory_after_success(tmp_path):
    probe, _, check = lane(tmp_path)
    check()
    probe.http = lambda *a: ()
    with pytest.raises(subject.WriteAdmissionError, match="seven complete rows"):
        check()


@pytest.mark.parametrize("timeout", [0, -1, True, "5000", 30001])
def test_reattestation_keeps_bounded_deadline(tmp_path, timeout):
    probe, _, check = lane(tmp_path)
    with pytest.raises(subject.WriteAdmissionError):
        check(timeout_ms=timeout)
    assert not probe.calls
