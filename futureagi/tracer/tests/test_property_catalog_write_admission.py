"""Admission needs actual complete observations; no services are contacted here."""

import hashlib
import json
import os
import shutil
import subprocess
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from tracer.services.clickhouse.v2 import catalog_prod_schema as schema
from tracer.services.clickhouse.v2.property_catalog import write_admission as subject
from tracer.services.clickhouse.v2.property_catalog.installation_identity import (
    IDENTITY_FILENAME,
    InstallationIdentity,
)
from tracer.services.clickhouse.v2.property_catalog.keeper_membership import (
    KeeperMembershipError,
)

DATABASE = "property_catalog_dev_admission"


def installation(environment="development"):
    return InstallationIdentity(
        environment,
        DATABASE,
        "catalog.candidates",
        "catalog.ordered",
        1,
        1,
        str(UUID(int=99)),
    )


def inventory(number=1, count=1):
    statements = (
        *schema._load_canonical_statements(),
        schema._activation_control_statement(),
    )
    specs = (*schema._TABLE_SPECS, schema._ACTIVATION_CONTROL_SPEC)
    rows = []
    for index, (statement, spec) in enumerate(zip(statements, specs, strict=True)):
        create = statement.sql
        if count > 1:
            create = schema._render_table(
                statement,
                spec,
                target_database=DATABASE,
                cluster="runtime_schema_verification",
                keeper_path_prefix="/clickhouse/tables",
            ).sql
        rows.append(
            {
                "hostname": f"host{number}",
                "server_uuid": str(UUID(int=number)),
                "database": DATABASE,
                "database_uuid": str(UUID(int=number + 10)),
                "database_engine": "Atomic",
                "username": "catalog_proof",
                "name": statement.table,
                "uuid": str(UUID(int=100 * number + index)),
                "engine": spec.replicated_engine if count > 1 else statement.engine,
                "create_table_query": create,
                "create_sha256": hashlib.sha256(create.encode()).hexdigest(),
                "keeper_path": f"/clickhouse/tables/{DATABASE}/1/{statement.table}"
                if count > 1
                else "",
                "keeper_name": "default" if count > 1 else "",
                "replica_name": f"replica{number}" if count > 1 else "",
                "replica_names": [f"replica{i}" for i in range(1, count + 1)]
                if count > 1
                else [],
                "total_replicas": count if count > 1 else 0,
                "active_replicas": count if count > 1 else 0,
                "readonly": 0,
                "session_expired": 0,
            }
        )
    return sorted(rows, key=lambda row: row["name"])


class Probe:
    def __init__(self, count=1):
        self.rows = {f"replica{i}": inventory(i, count) for i in range(1, count + 1)}
        self.sessions = {name: i for i, name in enumerate(self.rows, 1)}
        self.calls = []
        self.alter = lambda kind, name, rows: rows
        self.connections = tuple(
            subject.DirectCatalogConnection(
                name=name,
                native_member_host=f"{name}.internal",
                expected_hostname=rows[0]["hostname"],
                driver=SimpleNamespace(
                    database=DATABASE, execute_read=self.driver_read(name)
                ),
                http_scheme="https",
                proof_username="catalog_proof",
                proof_password="SECRET",
            )
            for name, rows in self.rows.items()
        )

    def driver_read(self, name):
        def read(sql, params, *, timeout_ms, settings):
            assert sql.startswith("SELECT") and settings["readonly"] == 2
            assert 0 < timeout_ms <= 30_000
            self.calls.append(("native", name, sql, params))
            if "getServerPort" in sql:
                assert params == {"port_name": "https_port", "database": DATABASE}
                first = self.rows[name][0]
                rows = [
                    {
                        "hostname": first["hostname"],
                        "connected_database": DATABASE,
                        "database_uuid": first["database_uuid"],
                        "http_port": 18123,
                    }
                ]
                kind = "route"
            elif "system.zookeeper_connection" in sql:
                first = self.rows[name][0]
                rows = [
                    {
                        "hostname": first["hostname"],
                        "server_uuid": first["server_uuid"],
                        "name": "default",
                        "client_id": self.sessions[name],
                        "is_expired": 0,
                    }
                ]
                kind = "session"
            elif "system.zookeeper" in sql:
                rows = [
                    {
                        "path": f"{table['keeper_path']}/replicas/{member}",
                        "value": f"UUID_'{table['server_uuid']}'",
                        "owner": self.sessions[member],
                    }
                    for member, tables in self.rows.items()
                    for table in tables
                ]
                assert set(params["paths"]) == {row["path"] for row in rows}
                kind = "keeper"
            else:
                assert sql in (
                    subject._INVENTORY_SQL,
                    subject._STANDALONE_INVENTORY_SQL,
                )
                rows, kind = deepcopy(self.rows[name]), "native"
            columns = tuple(rows[0])
            rows = self.alter(kind, name, rows)
            return (
                [tuple(row[c] for c in columns) for row in rows],
                [(c, "") for c in columns],
                {},
            )

        return read

    def http(self, connection, route, sql, timeout_ms, limit):
        assert route.origin == f"https://{connection.name}.internal:18123"
        assert sql in (subject._INVENTORY_SQL, subject._STANDALONE_INVENTORY_SQL)
        assert limit == 8 and 0 < timeout_ms <= 30_000
        self.calls.append(("http", connection.name, sql, {}))
        return self.alter("http", connection.name, deepcopy(self.rows[connection.name]))


def admit(tmp_path, probe=None, identity=None, **kwargs):
    identity = identity or installation()
    path = tmp_path / IDENTITY_FILENAME
    if not path.exists():
        path.write_bytes(identity.encode())
        path.chmod(0o600)
    probe = probe or Probe()
    return subject.admit_catalog_writes(
        tmp_path,
        identity=identity,
        connections=probe.connections,
        http_read=probe.http,
        **kwargs,
    )


def persisted(tmp_path):
    return tmp_path / subject.WRITE_ADMISSION_FILENAME


@pytest.mark.parametrize(
    "count,environment", [(1, "development"), (2, "development"), (3, "production")]
)
def test_real_producer_full_schema_identity_and_keeper_helper(
    tmp_path, count, environment
):
    probe = Probe(count)
    value = admit(tmp_path, probe, installation(environment))
    assert value == subject.WriteAdmission.decode(persisted(tmp_path).read_bytes())
    assert len(value.members) == count
    assert value.members[0].server_uuid == str(UUID(int=1))
    assert len(value.members[0].tables) == 7
    assert value.family == ("standalone" if count == 1 else "replicated")
    assert (
        value.installation_sha256
        == json.loads(installation(environment).encode())["identity_sha256"]
    )
    assert b"SECRET" not in value.encode() and b"catalog_proof" not in value.encode()
    keeper_reads = [c for c in probe.calls if "system.zookeeper" in c[2]]
    assert len(keeper_reads) == (3 * count if count > 1 else 0)
    assert not any(c[2] == subject._STANDALONE_INVENTORY_SQL for c in probe.calls)
    for member in value.members:
        assert member.url.endswith(":18123")
        for table in member.tables:
            assert table.replica_names == (
                tuple(f"replica{i}" for i in range(1, count + 1)) if count > 1 else ()
            )
    assert persisted(tmp_path).stat().st_mode & 0o777 == 0o600
    assert persisted(tmp_path).stat().st_nlink == 1
    assert not list(tmp_path.glob("*.tmp"))


def test_restart_reattests_actual_keeper_sessions_without_changing_descriptor(tmp_path):
    probe = Probe(2)
    first = admit(tmp_path, probe)
    probe.sessions = {"replica1": 101, "replica2": 202}
    probe.calls.clear()
    second = admit(tmp_path, probe)
    assert (
        second == first
        and len([c for c in probe.calls if "system.zookeeper" in c[2]]) == 6
    )
    assert b"client_id" not in first.encode() and b"ephemeral" not in first.encode()


def test_standalone_admission_restart_still_discovers_with_full_inventory(tmp_path):
    probe = Probe()
    first = admit(tmp_path, probe)
    probe.calls.clear()
    assert admit(tmp_path, probe) == first
    assert any(c[2] == subject._INVENTORY_SQL for c in probe.calls)
    assert not any(c[2] == subject._STANDALONE_INVENTORY_SQL for c in probe.calls)


@pytest.mark.parametrize(
    "field,value",
    [
        ("database_engine", "Ordinary"),
        ("server_uuid", str(UUID(int=0))),
        ("uuid", str(UUID(int=0))),
        ("database_uuid", str(UUID(int=0))),
        ("create_sha256", "0" * 64),
        ("readonly", 1),
        ("readonly", False),
        ("session_expired", 1),
        ("total_replicas", 1),
        ("active_replicas", 1),
        ("replica_names", None),
        ("replica_names", ["replica1"]),
        ("keeper_path", "/unproven"),
        ("replica_name", "replica1"),
        ("keeper_name", "default"),
        ("engine", "ReplacingMergeTree"),
    ],
)
def test_invalid_local_inventory_never_publishes(tmp_path, field, value):
    probe = Probe()
    probe.rows["replica1"][0][field] = value
    with pytest.raises((ValueError, schema.CatalogProdSchemaError)):
        admit(tmp_path, probe)
    assert not persisted(tmp_path).exists()


@pytest.mark.parametrize(
    "mutation", ["missing", "extra", "duplicate", "unsorted", "column", "mixed_node"]
)
def test_exact_schema_not_just_observed_hash(tmp_path, mutation):
    probe = Probe()
    rows = probe.rows["replica1"]
    if mutation == "missing":
        rows.pop()
    elif mutation == "extra":
        rows.append(deepcopy(rows[-1]))
    elif mutation == "duplicate":
        rows[-1] = deepcopy(rows[0])
    elif mutation == "unsorted":
        rows.reverse()
    elif mutation == "mixed_node":
        rows[-1]["server_uuid"] = str(UUID(int=1000))
    else:
        rows[0]["create_table_query"] = rows[0]["create_table_query"].replace(
            "UInt64", "UInt32"
        )
        rows[0]["create_sha256"] = hashlib.sha256(
            rows[0]["create_table_query"].encode()
        ).hexdigest()
    with pytest.raises(ValueError):
        admit(tmp_path, probe)
    assert not persisted(tmp_path).exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("server_uuid", str(UUID(int=800))),
        ("uuid", str(UUID(int=900))),
        ("hostname", "foreign"),
        ("database", "foreign"),
        ("database_uuid", str(UUID(int=300))),
        ("username", "foreign"),
        ("create_sha256", "0" * 64),
    ],
)
def test_http_route_must_attest_same_native_identity(tmp_path, field, value):
    probe = Probe()

    def alter(kind, name, rows):
        if kind == "http":
            for row in rows:
                row[field] = value
        return rows

    probe.alter = alter
    with pytest.raises(ValueError):
        admit(tmp_path, probe)
    assert not persisted(tmp_path).exists()


@pytest.mark.parametrize("kind,nth", [("native", 2), ("native", 3), ("http", 2)])
def test_rechecks_inventory_drift_before_publication(tmp_path, kind, nth):
    probe = Probe()
    seen = 0

    def alter(current, name, rows):
        nonlocal seen
        if current == kind:
            seen += 1
            if seen == nth:
                rows[0]["uuid"] = str(UUID(int=9000))
        return rows

    probe.alter = alter
    with pytest.raises(ValueError):
        admit(tmp_path, probe)
    assert seen == nth and not persisted(tmp_path).exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "independent",
        "missing",
        "reconnect",
        "auxiliary",
        "inactive",
        "subset",
        "aliased_server",
    ],
)
def test_replicated_producer_cannot_accept_copied_paths_or_partial_membership(
    tmp_path, mutation
):
    probe = Probe(2)
    seen = 0

    def alter(kind, name, rows):
        nonlocal seen
        if kind == "keeper" and name == "replica2":
            if mutation == "independent":
                rows[0]["value"] = f"UUID_'{UUID(int=999)}'"
            elif mutation == "missing":
                rows.pop()
        if kind == "session" and name == "replica1":
            seen += 1
            if mutation == "reconnect" and seen == 2:
                rows[0]["client_id"] += 9
        return rows

    probe.alter = alter
    for row in probe.rows["replica2"]:
        if mutation == "auxiliary":
            row["keeper_name"] = "aux"
        elif mutation == "inactive":
            row["active_replicas"] = 1
        elif mutation == "subset":
            row["replica_names"] = ["replica2"]
        elif mutation == "aliased_server":
            row["server_uuid"] = str(UUID(int=1))
    with pytest.raises((ValueError, KeeperMembershipError)):
        admit(tmp_path, probe)
    assert not persisted(tmp_path).exists()


@pytest.mark.parametrize("count", [1, 2])
def test_production_still_requires_exact_three_members(tmp_path, count):
    with pytest.raises(ValueError):
        admit(tmp_path, Probe(count), installation("production"))
    assert not persisted(tmp_path).exists()


def test_schema_callback_receives_observed_exact_inventory_and_failure_is_closed(
    tmp_path,
):
    probe = Probe()
    seen = []

    def verify(database, rows):
        assert database == DATABASE and rows == tuple(probe.rows["replica1"])
        seen.append(True)
        raise RuntimeError("schema unproven")

    with pytest.raises(RuntimeError, match="schema unproven"):
        admit(tmp_path, probe, verify_schema=verify)
    assert seen == [True] and not persisted(tmp_path).exists()


def test_missing_identity_is_not_initialized_or_probed(tmp_path):
    probe = Probe()
    with pytest.raises(FileNotFoundError):
        subject.admit_catalog_writes(
            tmp_path,
            identity=installation(),
            connections=probe.connections,
            http_read=probe.http,
        )
    assert not probe.calls and not list(tmp_path.iterdir())


@pytest.mark.parametrize("when", ["before", "during"])
def test_runtime_identity_is_immutable_and_rechecked(tmp_path, when):
    probe = Probe()
    path = tmp_path / IDENTITY_FILENAME
    changed = replace(installation(), producer_stream_id=str(UUID(int=222)))
    path.write_bytes((changed if when == "before" else installation()).encode())

    def alter(kind, name, rows):
        if when == "during" and kind == "http":
            path.write_bytes(changed.encode())
        return rows

    probe.alter = alter
    with pytest.raises(ValueError, match="identity changed"):
        admit(tmp_path, probe)
    assert not persisted(tmp_path).exists()
    if when == "before":
        assert not probe.calls


def test_existing_descriptor_is_immutable_even_after_valid_new_observations(tmp_path):
    first = admit(tmp_path)
    probe = Probe()
    for row in probe.rows["replica1"]:
        row["server_uuid"] = str(UUID(int=2000))
    with pytest.raises(ValueError, match="immutable"):
        admit(tmp_path, probe)
    assert persisted(tmp_path).read_bytes() == first.encode()


@pytest.mark.parametrize(
    "bad", [b"", b"{}\n", b"x" * (subject.MAX_ADMISSION_BYTES + 1)]
)
def test_unknown_existing_descriptor_is_not_overwritten(tmp_path, bad):
    persisted(tmp_path).write_bytes(bad)
    with pytest.raises(ValueError):
        admit(tmp_path)
    assert persisted(tmp_path).read_bytes() == bad


@pytest.mark.parametrize(
    "kind", ["symlink", "hardlink", "fifo", "directory", "writable"]
)
def test_unsafe_admission_file_is_not_followed_or_replaced(tmp_path, kind):
    path = persisted(tmp_path)
    if kind in {"symlink", "hardlink"}:
        other = tmp_path / "unowned"
        other.write_bytes(b"preserve")
        if kind == "symlink":
            path.symlink_to(other)
        else:
            os.link(other, path)
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "directory":
        path.mkdir()
    else:
        path.write_bytes(b"preserve")
        path.chmod(0o666)
    with pytest.raises((ValueError, OSError)):
        admit(tmp_path)
    if kind in {"symlink", "hardlink"}:
        assert other.read_bytes() == b"preserve"


@pytest.mark.parametrize("step", ["file_fsync", "rename", "directory_fsync"])
def test_crash_uncertainty_leaves_absent_or_exact_single_link_descriptor(
    tmp_path, monkeypatch, step
):
    real_fsync, real_rename = os.fsync, os.rename

    def fsync(fd):
        directory = os.fstat(fd).st_mode & 0o170000 == 0o040000
        if (step == "directory_fsync" and directory) or (
            step == "file_fsync" and not directory
        ):
            raise OSError("lost acknowledgement")
        real_fsync(fd)

    def rename(*args, **kwargs):
        if step == "rename":
            raise OSError("rename failed")
        real_rename(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(subject.os, "fsync", fsync)
        patch.setattr(subject.os, "rename", rename)
        with pytest.raises(OSError):
            admit(tmp_path)
    assert not list(tmp_path.glob("*.tmp"))
    if step != "directory_fsync":
        assert not persisted(tmp_path).exists()
    else:
        assert persisted(tmp_path).stat().st_nlink == 1
        subject.WriteAdmission.decode(persisted(tmp_path).read_bytes())
    # Reattest and fsync exact state on restart, never produce a new identity.
    value = admit(tmp_path)
    assert value == subject.WriteAdmission.decode(persisted(tmp_path).read_bytes())


def test_concurrent_producer_does_not_wait_or_overwrite(tmp_path, monkeypatch):
    import fcntl

    lock = os.open(tmp_path / ".write-admission-v1.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with pytest.raises(BlockingIOError):
            admit(tmp_path)
        assert not persisted(tmp_path).exists()
    finally:
        os.close(lock)


def test_canonical_go_encoding_escapes_html_but_preserves_utf8():
    assert (
        subject._canonical({"x": "café<>&\u2028\u2029"})
        == b'{"x":"caf\xc3\xa9\\u003c\\u003e\\u0026\\u2028\\u2029"}'
    )


def test_owned_nat_route_changes_only_origin_and_keeps_all_identity_proofs(tmp_path):
    probe = Probe()
    mapped = []

    def resolve(connection, route):
        assert connection is probe.connections[0]
        assert route.origin == "https://replica1.internal:18123"
        mapped.append(route)
        return "https://127.0.0.1:49127"

    def http(connection, route, sql, timeout_ms, limit):
        assert route.origin == "https://127.0.0.1:49127"
        assert (route.hostname, route.database, route.database_uuid) == (
            "host1",
            DATABASE,
            str(UUID(int=11)),
        )
        return deepcopy(probe.rows[connection.name])

    (tmp_path / IDENTITY_FILENAME).write_bytes(installation().encode())
    result = subject.admit_catalog_writes(
        tmp_path,
        identity=installation(),
        connections=probe.connections,
        route_resolver=resolve,
        http_read=http,
    )
    assert result.members[0].url == "https://127.0.0.1:49127" and len(mapped) == 1
    assert sum("system.tables" in call[2] for call in probe.calls) == 3


@pytest.mark.parametrize(
    "bad",
    ["foreign", "alias", "credentials", "downgrade", "default_port", "redirect_path"],
)
def test_mapped_route_is_not_an_admission_bypass(tmp_path, bad):
    probe = Probe(2 if bad == "alias" else 1)
    urls = {
        "credentials": "https://user:SECRET@127.0.0.1:1234",
        "downgrade": "http://127.0.0.1:1234",
        "default_port": "https://127.0.0.1",
        "redirect_path": "https://127.0.0.1:1234/route",
    }

    def http(connection, route, sql, timeout_ms, limit):
        rows = deepcopy(probe.rows[connection.name])
        if bad == "foreign":
            for row in rows:
                row["server_uuid"] = str(UUID(int=300))
        return rows

    (tmp_path / IDENTITY_FILENAME).write_bytes(installation().encode())
    with pytest.raises(ValueError):
        subject.admit_catalog_writes(
            tmp_path,
            identity=installation(),
            connections=probe.connections,
            route_resolver=lambda *_: urls.get(bad, "https://127.0.0.1:49127"),
            http_read=http,
        )
    assert not persisted(tmp_path).exists()


def test_schema_drift_during_keeper_proof_is_rechecked(tmp_path):
    probe = Probe(2)

    def alter(kind, name, rows):
        if kind == "keeper":
            probe.rows["replica1"][0]["uuid"] = str(UUID(int=9000))
        return rows

    probe.alter = alter
    with pytest.raises(ValueError, match="native inventory changed"):
        admit(tmp_path, probe)
    assert not persisted(tmp_path).exists()


def test_current_go_parser_accepts_produced_canonical_descriptors(tmp_path):
    """Cross-language contract test; compiler/cache only, dependencies strictly offline."""
    go = shutil.which("go")
    if go is None:
        pytest.skip(
            "Go compiler unavailable for optional cross-language contract check"
        )
    helper = tmp_path / "parse_admission.go"
    helper.write_text("""package main
import (
 "bytes"
 "encoding/json"
 "fmt"
 "io"
 "os"
 catalog "github.com/future-agi/future-agi/fi-collector/pkg/propertycatalog"
)
func main() {
 raw, err := io.ReadAll(os.Stdin)
 if err != nil { panic(err) }
 if _, err = catalog.ParseWriteAdmission(raw); err != nil { panic(err) }
 var document catalog.WriteAdmission
 if err = json.Unmarshal(raw, &document); err != nil { panic(err) }
 encoded, err := json.Marshal(document)
 if err != nil || !bytes.Equal(append(encoded, '\\n'), raw) { panic("canonical mismatch") }
 fmt.Print(document.TopologySHA256)
}
""")
    executable = tmp_path / "parse_admission"
    module = Path(__file__).resolve().parents[3] / "fi-collector"
    env = {**os.environ, "GOPROXY": "off", "GOSUMDB": "off", "GOTOOLCHAIN": "local"}
    compiled = subprocess.run(
        [go, "build", "-o", str(executable), str(helper)],
        cwd=module,
        env=env,
        capture_output=True,
        timeout=60,
    )
    assert compiled.returncode == 0, compiled.stderr.decode()
    for count, environment in (
        (1, "development"),
        (2, "development"),
        (3, "production"),
    ):
        directory = tmp_path / str(count)
        directory.mkdir(mode=0o700)
        value = admit(directory, Probe(count), installation(environment))
        decoded = subprocess.run(
            [str(executable)], input=value.encode(), capture_output=True, timeout=10
        )
        assert decoded.returncode == 0, decoded.stderr.decode()
        assert decoded.stdout.decode() == value.topology_sha256
        if count == 2:
            # This is a codec vector, NOT admission of an unverified alternate schema.
            members = tuple(
                replace(
                    m,
                    tables=tuple(
                        replace(
                            t,
                            keeper_path=t.keeper_path.replace(
                                "/clickhouse/", "/café<>&\u2028\u2029/"
                            ),
                        )
                        for t in m.tables
                    ),
                )
                for m in value.members
            )
            document = json.loads(value.encode())
            document["members"] = [asdict(m) for m in members]
            document["keeper_identity_sha256"] = subject._keeper_digest(members)
            del document["topology_sha256"]
            document["topology_sha256"] = subject._sha(document)
            raw = subject._canonical(document) + b"\n"
            subject.WriteAdmission.decode(raw)
            decoded = subprocess.run(
                [str(executable)], input=raw, capture_output=True, timeout=10
            )
            assert decoded.returncode == 0, decoded.stderr.decode()
            assert decoded.stdout.decode() == document["topology_sha256"]


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown",
        "missing",
        "bool",
        "null_replicas",
        "digest",
        "duplicate",
        "whitespace",
        "no_newline",
    ],
)
def test_descriptor_decoder_requires_exact_go_contract(tmp_path, mutation):
    raw = admit(tmp_path).encode()
    document = json.loads(raw)
    if mutation == "unknown":
        document["guess"] = True
    elif mutation == "missing":
        del document["format"]
    elif mutation == "bool":
        document["version"] = True
    elif mutation == "null_replicas":
        document["members"][0]["tables"][0]["replica_names"] = None
    elif mutation == "digest":
        document["topology_sha256"] = "0" * 64
    raw = subject._canonical(document) + b"\n"
    if mutation == "duplicate":
        raw = raw.replace(b'{"database":', b'{"database":"duplicate","database":', 1)
    elif mutation == "whitespace":
        raw += b" "
    elif mutation == "no_newline":
        raw = raw[:-1]
    with pytest.raises(ValueError):
        subject.WriteAdmission.decode(raw)


class HTTPResponse:
    status = 200
    headers = {}
    raw = b'{"a":1}\n'

    def getheader(self, name):
        return self.headers.get(name)

    def read(self, bound):
        return self.raw[:bound]


def http_transport(monkeypatch, response):
    calls = []

    class Client:
        def __init__(self, host, port, timeout):
            calls.append((host, port, timeout))

        def request(self, *args, **kwargs):
            calls.append((args, kwargs))

        def getresponse(self):
            return response

        def close(self):
            calls.append("closed")

    monkeypatch.setattr(subject.http.client, "HTTPSConnection", Client)
    probe = Probe()
    route = subject.CatalogHTTPRouteCandidate(
        "https://replica1.internal:18123", "host1", DATABASE, str(UUID(int=11))
    )
    return calls, lambda: subject.read_catalog_http(
        probe.connections[0], route, subject._INVENTORY_SQL, 500, 8
    )


def test_real_http_transport_uses_explicit_route_auth_bounded_readonly_and_no_retry(
    monkeypatch,
):
    calls, read = http_transport(monkeypatch, HTTPResponse())
    assert read() == ({"a": 1},)
    assert calls[0] == ("replica1.internal", 18123, 0.5) and calls[-1] == "closed"
    args, kwargs = calls[1]
    assert (
        args[0] == "POST"
        and "readonly=2" in args[1]
        and "wait_end_of_query=1" in args[1]
    )
    assert "output_format_json_quote_64bit_integers=0" in args[1]
    assert "SECRET" not in args[1]
    assert kwargs["headers"]["Authorization"].startswith("Basic ")
    assert kwargs["body"].endswith(b"FORMAT JSONEachRow")
    assert len(calls) == 3


@pytest.mark.parametrize(
    "bad",
    [
        "redirect",
        "exception",
        "truncated",
        "length",
        "oversize",
        "duplicate",
        "extra_rows",
        "bad_json",
    ],
)
def test_http_transport_rejects_uncertain_or_malformed_complete_response(
    monkeypatch, bad
):
    response = HTTPResponse()
    if bad == "redirect":
        response.status = 302
    elif bad == "exception":
        response.headers = {"X-ClickHouse-Exception-Code": "241"}
    elif bad == "truncated":
        response.raw = b'{"a":1}'
    elif bad == "length":
        response.headers = {"Content-Length": "100"}
    elif bad == "oversize":
        response.raw = b"x" * subject._MAX_PROBE_BYTES + b"\n"
    elif bad == "duplicate":
        response.raw = b'{"a":1,"a":2}\n'
    elif bad == "extra_rows":
        response.raw *= 9
    else:
        response.raw = b"SECRET\n"
    calls, read = http_transport(monkeypatch, response)
    with pytest.raises(ValueError, match="observation failed") as failure:
        read()
    assert (
        "SECRET" not in str(failure.value) and calls[-1] == "closed" and len(calls) == 3
    )
