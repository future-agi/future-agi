from __future__ import annotations

import hashlib
import json
import os
import socket
import uuid
from contextlib import ExitStack
from dataclasses import asdict, replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from django.core.management.base import CommandError

from tracer.management.commands import ch25_property_catalog_activate_latest as subject
from tracer.services.clickhouse.v2.property_catalog import (
    durable_native_writer,
    native_write_proof,
    reader_activation,
    write_admission,
)
from tracer.services.clickhouse.v2.property_catalog.activation_control import (
    ACTIVATION_CONTROL_COLUMNS,
    ACTIVATION_CONTROL_TABLE,
    ActivationControlEvent,
    ActivationControlRejected,
    ActivationControlScope,
    ActivationControlTarget,
    QualifiedActivation,
    activation_control_event_sql,
    qualified_activation_sql,
)
from tracer.services.clickhouse.v2.property_catalog.native_write_journal import (
    NativeWriteAttempt,
)
from tracer.services.clickhouse.v2.property_catalog.production_rollout import (
    PRODUCTION_LIFECYCLE_ACK,
)
from tracer.services.clickhouse.v2.property_catalog.write_admission import (
    WRITE_ADMISSION_FILENAME,
)
from tracer.tests.test_property_catalog_replicated_write_startup import (
    context as replicated_context,
)
from tracer.tests.test_property_catalog_replicated_write_startup import (
    setup as replicated_setup,
)

ORG = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
REQUEST = "33333333-3333-4333-8333-333333333333"
AT = datetime(2026, 9, 2, 12, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("activation command unit tests must not open network connections")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)


def _settings(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "ENV_TYPE": "production",
        "CLOUD_DEPLOYMENT": "US",
        "PROPERTY_CATALOG_LIFECYCLE_ENABLED": True,
        "PROPERTY_CATALOG_LIFECYCLE_ACK": PRODUCTION_LIFECYCLE_ACK,
        "PROPERTY_CATALOG_ACTIVATION_CONTROL_ACK": subject.ACTIVATION_CONTROL_ACK,
        "PROPERTY_CATALOG_LIFECYCLE_TARGET_DATABASE": "property_catalog",
        "PROPERTY_CATALOG_LIFECYCLE_WRITE_CH_HOST": "catalog.internal",
        "PROPERTY_CATALOG_LIFECYCLE_WRITE_CH_PORT": 9000,
        "PROPERTY_CATALOG_ACTIVATION_CONTROL_CH_USER": "catalog_control_writer",
        "PROPERTY_CATALOG_ACTIVATION_CONTROL_CH_PASSWORD": "not-logged",
        "PROPERTY_CATALOG_LIFECYCLE_WRITE_CH_USER": "catalog_lifecycle_writer",
        "PROPERTY_CATALOG_CH_USER": "catalog_api_reader",
        "CH25_USER": "catalog_source_reader",
        "PROPERTY_CATALOG_LIFECYCLE_EXPECTED_WRITE_CH_HOSTNAMES": (
            "catalog-0",
            "catalog-1",
        ),
        "PROPERTY_CATALOG_LIFECYCLE_CATALOG_EPOCH": 2,
        "PROPERTY_CATALOG_LIFECYCLE_PROJECTION_VERSION": 1,
        "PROPERTY_CATALOG_LIFECYCLE_WORKSPACE_SCOPE_MODE": "allowlist",
        "PROPERTY_CATALOG_LIFECYCLE_WORKSPACE_ALLOWLIST": (WORKSPACE,),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _target(*, epoch: int = 2) -> ActivationControlTarget:
    return ActivationControlTarget(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=epoch,
        projection_version=1,
        catalog_revision=1,
        build_token=str(uuid.UUID(int=1001)),
        activation_sha256=hashlib.sha256(b"activation").hexdigest(),
    )


class _Store:
    def __init__(self, target: ActivationControlTarget) -> None:
        self.qualified = (QualifiedActivation(target, 1),)
        self.events: list[ActivationControlEvent] = []
        self.confirmed: list[ActivationControlEvent] = []

    def list_qualified_activations(self, _scope: ActivationControlScope):
        return self.qualified

    def list_control_events(self, _scope: ActivationControlScope):
        return tuple(self.events)

    def append_control_event(self, event, *, expected_head):
        head = self.events[-1].head if self.events else None
        if head != expected_head:
            raise ActivationControlRejected("control_concurrent")
        self.events.append(event)
        return event

    def confirm_control_event(self, event):
        assert event in self.events, "cannot confirm an unknown event"
        self.confirmed.append(event)


def test_config_requires_dedicated_control_writer() -> None:
    config = subject.activation_command_config(settings_object=_settings())
    assert config.catalog_epoch == 2
    assert config.user == "catalog_control_writer"
    assert config.expected_hostnames == ("catalog-0", "catalog-1")
    assert config.workspace_scope_mode == "allowlist"

    with pytest.raises(
        subject.ProductionActivationCommandError,
        match="dedicated ClickHouse identity",
    ):
        subject.activation_command_config(
            settings_object=_settings(
                PROPERTY_CATALOG_ACTIVATION_CONTROL_CH_USER=("catalog_lifecycle_writer")
            )
        )


def test_config_accepts_global_workspace_scope_without_an_allowlist() -> None:
    config = subject.activation_command_config(
        settings_object=_settings(
            PROPERTY_CATALOG_LIFECYCLE_WORKSPACE_SCOPE_MODE="all",
            PROPERTY_CATALOG_LIFECYCLE_WORKSPACE_ALLOWLIST=(),
        )
    )

    assert config.workspace_scope_mode == "all"
    assert config.workspace_ids == ()


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        (
            {"PROPERTY_CATALOG_LIFECYCLE_WORKSPACE_SCOPE_MODE": "invalid"},
            "must equal allowlist or all",
        ),
        (
            {
                "PROPERTY_CATALOG_LIFECYCLE_WORKSPACE_SCOPE_MODE": "all",
                "PROPERTY_CATALOG_LIFECYCLE_WORKSPACE_ALLOWLIST": (WORKSPACE,),
            },
            "requires an empty workspace allowlist",
        ),
        (
            {"PROPERTY_CATALOG_LIFECYCLE_WORKSPACE_ALLOWLIST": ()},
            "non-empty unique",
        ),
    ),
)
def test_config_rejects_inconsistent_workspace_scope(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(subject.ProductionActivationCommandError, match=message):
        subject.activation_command_config(settings_object=_settings(**overrides))


def test_status_is_read_only_and_execute_is_exactly_replayable() -> None:
    store = _Store(_target())
    scope = ActivationControlScope(ORG, WORKSPACE)

    status = subject.run_initial_activation(
        store=store,
        scope=scope,
        catalog_epoch=2,
        projection_version=1,
        execute=False,
        request_id=None,
        now=AT,
    )
    assert status["mode"] == "status"
    assert status["control_event_count"] == 0
    assert store.events == []

    first = subject.run_initial_activation(
        store=store,
        scope=scope,
        catalog_epoch=2,
        projection_version=1,
        execute=True,
        request_id=REQUEST,
        now=AT,
    )
    replay = subject.run_initial_activation(
        store=store,
        scope=scope,
        catalog_epoch=2,
        projection_version=1,
        execute=True,
        request_id=REQUEST,
        now=AT,
    )

    assert first["control_sequence"] == 1
    assert first["idempotent"] is False
    assert replay["idempotent"] is True
    assert len(store.events) == 1
    assert store.confirmed == [store.events[0]]


def test_activation_rejects_a_qualified_target_from_another_epoch() -> None:
    with pytest.raises(
        subject.ProductionActivationCommandError,
        match="configured epoch/projection",
    ):
        subject.run_initial_activation(
            store=_Store(_target(epoch=1)),
            scope=ActivationControlScope(ORG, WORKSPACE),
            catalog_epoch=2,
            projection_version=1,
            execute=True,
            request_id=REQUEST,
            now=AT,
        )


def test_activation_rejects_a_workspace_without_qualified_state() -> None:
    store = _Store(_target())
    store.qualified = ()
    with pytest.raises(
        subject.ProductionActivationCommandError,
        match="no qualified catalog activation",
    ):
        subject.run_initial_activation(
            store=store,
            scope=ActivationControlScope(ORG, WORKSPACE),
            catalog_epoch=2,
            projection_version=1,
            execute=False,
            request_id=None,
            now=AT,
        )


class _Driver:
    database = "property_catalog"
    user = "catalog_control_writer"
    server_enforced_readonly = False

    def __init__(
        self,
        *,
        hostname: str = "catalog-0",
        grants: tuple[tuple[str], ...] | None = None,
    ) -> None:
        self.hostname = hostname
        self.grants = grants or (
            (
                "GRANT SELECT ON property_catalog.property_catalog_activations "
                "TO catalog_control_writer",
            ),
            (
                "GRANT SELECT, INSERT ON "
                "property_catalog.property_catalog_activation_control_events "
                "TO catalog_control_writer",
            ),
        )
        self.attestations: list[str] = []
        self.reads: list[str] = []
        self.writes: list[str] = []

    def execute_read(self, sql, _params, **_kwargs):
        self.reads.append(sql)
        return [], [("organization_id", "UUID")], 1.0

    def execute(self, sql, _params=None, **_kwargs):
        if sql == subject._CLICKHOUSE_PROVENANCE_SQL:  # noqa: SLF001
            self.attestations.append("identity")
            return [
                (
                    self.hostname,
                    self.database,
                    self.user,
                    0,
                    0,
                )
            ]
        if sql == subject._CLICKHOUSE_GRANTS_SQL:  # noqa: SLF001
            self.attestations.append("grants")
            return self.grants
        self.writes.append(sql)

    def close(self) -> None:
        return None


def test_native_adapter_is_closed_over_two_reads_and_one_insert() -> None:
    driver = _Driver()
    client = subject._ActivationControlClient(  # noqa: SLF001
        driver,  # type: ignore[arg-type]
        database="property_catalog",
        user="catalog_control_writer",
        expected_hostnames=("catalog-0", "catalog-1"),
    )
    client.query(
        activation_control_event_sql("property_catalog"),
        {
            "catalog_organization_id": ORG,
            "catalog_workspace_id": WORKSPACE,
            "catalog_control_result_limit": 2,
        },
        timeout_ms=500,
    )
    with pytest.raises(
        subject.ProductionActivationCommandError,
        match="non-reviewed read",
    ):
        client.query("SELECT 1", {}, timeout_ms=500)

    row = dict.fromkeys(ACTIVATION_CONTROL_COLUMNS, "value")
    client.insert(
        f"`property_catalog`.`{ACTIVATION_CONTROL_TABLE}`",
        (row,),
        columns=ACTIVATION_CONTROL_COLUMNS,
        timeout_ms=500,
        deduplication_token="request:digest",
    )
    assert len(driver.reads) == 1
    assert len(driver.writes) == 1
    assert driver.attestations == ["identity", "grants"] * 2


def test_native_adapter_rejects_wrong_server_or_extra_grants() -> None:
    with pytest.raises(
        subject.ProductionActivationCommandError,
        match="server identity",
    ):
        subject._ActivationControlClient(  # noqa: SLF001
            _Driver(hostname="catalog-unknown"),  # type: ignore[arg-type]
            database="property_catalog",
            user="catalog_control_writer",
            expected_hostnames=("catalog-0", "catalog-1"),
        )

    grants = _Driver().grants + (
        (
            "GRANT SELECT ON property_catalog.property_catalog_checkpoints "
            "TO catalog_control_writer",
        ),
    )
    with pytest.raises(
        subject.ProductionActivationCommandError,
        match="two-table contract exactly",
    ):
        subject._ActivationControlClient(  # noqa: SLF001
            _Driver(grants=grants),  # type: ignore[arg-type]
            database="property_catalog",
            user="catalog_control_writer",
            expected_hostnames=("catalog-0", "catalog-1"),
        )


def _managed_fixture(tmp_path, monkeypatch):
    """Real three-member admission producer; only native/HTTP I/O replaced."""
    lane = replicated_setup(tmp_path, monkeypatch, production=True, count=3)
    with replicated_context(lane) as (_, admitted, _):
        lane.admission = admitted
    lane.settings = _settings(
        **{
            **vars(lane.settings),
            "PROPERTY_CATALOG_LIFECYCLE_WRITE_CH_PORT": 19999,
            "PROPERTY_CATALOG_LIFECYCLE_CATALOG_EPOCH": 0,
            "PROPERTY_CATALOG_LIFECYCLE_PROJECTION_VERSION": 0,
            "PROPERTY_CATALOG_LIFECYCLE_RUNTIME_DIRECTORY": str(
                tmp_path / "coordinator"
            ),
        }
    )
    (tmp_path / "coordinator").mkdir(mode=0o700)
    lane.drivers, lane.contexts = [], []
    for key, value in lane.env.items():
        monkeypatch.setenv(key, value)

    def context(settings_object, **kwargs):
        assert settings_object is lane.settings
        assert kwargs == {"prefix": "PROPERTY_CATALOG_LIFECYCLE_"}
        lane.contexts.append(kwargs)
        return replicated_context(lane, **kwargs)

    def driver(**kwargs):
        # The dedicated writer must never connect to the discovery seed.
        index = int(kwargs["host"].split(".")[0].removeprefix("replica"))
        result = _Driver(hostname=f"host{index}")
        for key in ("host", "port", "user", "database", "server_enforced_readonly"):
            setattr(result, key, kwargs[key])
        result.closed = 0
        result.close = lambda: setattr(result, "closed", result.closed + 1)
        read = result.execute_read

        def execute_read(sql, params, **limits):
            if sql == reader_activation._SCHEMA_SQL:
                rows = lane.factory.probe.rows[f"replica{index}"]
                return (
                    [
                        tuple(
                            row[k]
                            for k in (
                                "database",
                                "name",
                                "engine",
                                "create_table_query",
                            )
                        )
                        for row in rows
                        if row["name"] in params["tables"]
                    ],
                    (),
                    0,
                )
            return read(sql, params, **limits)

        result.execute_read = execute_read
        lane.drivers.append((result, kwargs))
        return result

    monkeypatch.setattr(subject, "replicated_write_admission_context", context)
    monkeypatch.setattr(subject, "ClickHouseClient", driver)
    return lane


@pytest.mark.parametrize("direct", [False, True])
def test_managed_control_writer_uses_existing_proof_and_exact_dedicated_direct_route(
    tmp_path, monkeypatch, direct
):
    lane = _managed_fixture(tmp_path, monkeypatch)
    if direct:
        lane.settings.PROPERTY_CATALOG_LIFECYCLE_WRITE_CH_HOST = "replica2.internal"
        lane.settings.PROPERTY_CATALOG_LIFECYCLE_WRITE_CH_PORT = 19002
    config = subject.activation_command_config(
        settings_object=lane.settings, require_managed=True
    )
    before = (tmp_path / WRITE_ADMISSION_FILENAME).read_bytes()
    with ExitStack() as resources:
        writer = subject._managed_control_writer(
            config, settings_object=lane.settings, resources=resources
        )
        assert type(writer) is subject.DurableNativeCatalogWriter
        assert type(writer.proof) is subject.NativeWriteProof
        assert writer.proof.admission == lane.admission
        assert writer.proof.identity == config.installation == lane.identity
        assert writer.directory == tmp_path
        assert writer.member.name == ("replica2" if direct else "replica1")
        assert len(writer.proof.connections) == 3
        assert all(not c.driver.closed for c in writer.proof.connections)
        assert writer.driver.user == config.user == "catalog_control_writer"
        assert writer.driver.user != writer.connection.driver.user
        assert lane.drivers[0][1]["password"] == "not-logged"
        # Real dedicated grant/schema adapter accepts the actual writer object.
        client = reader_activation.ReaderActivationClient(
            writer.driver,
            database=config.database,
            user=config.user,
            expected_hostnames=(writer.member.hostname,),
            durable_writer=writer,
        )
        assert client.receipt_confirmation_available is True
        assert not writer.driver.closed
    assert lane.drivers[0][0].closed == 1
    assert all(c.closed for c, _ in lane.factory.clients)
    assert (tmp_path / WRITE_ADMISSION_FILENAME).read_bytes() == before
    assert (tmp_path / subject.IDENTITY_FILENAME).read_bytes() == lane.identity.encode()


@pytest.mark.parametrize(
    "bad",
    [
        "missing_identity",
        "missing_admission",
        "fifo_admission",
        "alias_proof",
        "changed_identity",
        "different_admission",
        "foreign_host",
        "two_hosts",
    ],
)
def test_execute_startup_fails_closed_without_control_insert(
    tmp_path, monkeypatch, bad
):
    lane = _managed_fixture(tmp_path, monkeypatch)
    config = subject.activation_command_config(
        settings_object=lane.settings, require_managed=True
    )
    if bad == "missing_identity":
        (tmp_path / subject.IDENTITY_FILENAME).unlink()
    elif bad in {"missing_admission", "fifo_admission"}:
        (tmp_path / WRITE_ADMISSION_FILENAME).unlink()
        if bad == "fifo_admission":
            os.mkfifo(tmp_path / WRITE_ADMISSION_FILENAME)
    elif bad == "alias_proof":
        monkeypatch.setenv("FI_PROPERTY_CATALOG_LEDGER_CH_USERNAME", config.user)
    elif bad == "changed_identity":
        config = replace(config, installation=replace(lane.identity, catalog_epoch=2))
    elif bad == "different_admission":
        lane.factory.probe.rows["replica1"][0]["uuid"] = str(uuid.UUID(int=7000))
    else:
        lane.settings.PROPERTY_CATALOG_LIFECYCLE_EXPECTED_WRITE_CH_HOSTNAMES = (
            ("foreign", "host2", "host3")
            if bad == "foreign_host"
            else ("host1", "host2")
        )
    with pytest.raises((ValueError, OSError, subject.ProductionActivationCommandError)):
        with ExitStack() as resources:
            subject._managed_control_writer(
                config, settings_object=lane.settings, resources=resources
            )
    assert not lane.drivers
    assert all(c.closed for c, _ in lane.factory.clients)
    assert not tuple((tmp_path / "coordinator").iterdir())


@pytest.mark.parametrize(
    "bad", ["epoch", "projection", "mixed", "bool", "producer", "topic", "missing"]
)
def test_managed_identity_never_uses_environment_versions_to_shift_installation(
    tmp_path, monkeypatch, bad
):
    lane = _managed_fixture(tmp_path, monkeypatch)
    values = {
        "epoch": {
            "PROPERTY_CATALOG_LIFECYCLE_CATALOG_EPOCH": 2,
            "PROPERTY_CATALOG_LIFECYCLE_PROJECTION_VERSION": 1,
        },
        "projection": {
            "PROPERTY_CATALOG_LIFECYCLE_CATALOG_EPOCH": 1,
            "PROPERTY_CATALOG_LIFECYCLE_PROJECTION_VERSION": 3,
        },
        "mixed": {"PROPERTY_CATALOG_LIFECYCLE_CATALOG_EPOCH": 1},
        "bool": {"PROPERTY_CATALOG_LIFECYCLE_CATALOG_EPOCH": False},
        "producer": {"PROPERTY_CATALOG_LIFECYCLE_PRODUCER_STREAM_ID": REQUEST},
        "topic": {"PROPERTY_CATALOG_CANDIDATE_KAFKA_TOPIC": "foreign"},
    }
    for key, value in values.get(bad, {}).items():
        setattr(lane.settings, key, value)
    if bad == "missing":
        (tmp_path / subject.IDENTITY_FILENAME).unlink()
    with pytest.raises((ValueError, subject.ProductionActivationCommandError)):
        subject.activation_command_config(
            settings_object=lane.settings, require_managed=True
        )
    assert not lane.contexts and not lane.drivers


def _wire_command(lane, monkeypatch):
    from tracer.management.commands import (
        ch25_property_catalog_lifecycle_controller as controller,
    )

    scope = SimpleNamespace(organization_id=ORG, workspace_id=WORKSPACE, project_ids=())
    monkeypatch.setattr(
        controller, "discover_workspace_scopes", lambda _: ((scope,), ())
    )
    monkeypatch.setattr(subject, "settings", lane.settings)
    store = _Store(_target(epoch=lane.identity.catalog_epoch))
    captured = []

    def factory(client, **kwargs):
        captured.append((client, kwargs))
        if kwargs["append_coordinator"] is not None:
            assert client.receipt_confirmation_available is True
            assert client._guarded._durable_writer.driver is lane.drivers[-1][0]
            assert kwargs["append_coordinator"].directory == lane.path / "coordinator"
        return store

    monkeypatch.setattr(subject, "ClickHouseActivationControlStore", factory)
    return store, captured


def test_execute_command_wires_real_managed_writer_and_shared_coordinator_and_replays(
    tmp_path, monkeypatch
):
    lane = _managed_fixture(tmp_path, monkeypatch)
    store, captured = _wire_command(lane, monkeypatch)
    first = json.loads(
        subject.Command().handle(
            workspace_id=WORKSPACE, execute=True, request_id=REQUEST
        )
    )
    second = json.loads(
        subject.Command().handle(
            workspace_id=WORKSPACE, execute=True, request_id=REQUEST
        )
    )
    assert first["mode"] == "execute" and first["idempotent"] is False
    assert second["idempotent"] is True
    assert len(store.events) == 1 and store.confirmed == store.events
    assert len(captured) == len(lane.contexts) == 2
    assert all(driver.closed == 1 for driver, _ in lane.drivers)
    assert all(c.closed for c, _ in lane.factory.clients)


def test_status_with_managed_identity_never_creates_journal_or_admission(
    tmp_path, monkeypatch
):
    lane = _managed_fixture(tmp_path, monkeypatch)
    lane.settings.PROPERTY_CATALOG_LIFECYCLE_WRITE_CH_HOST = "replica1.internal"
    lane.settings.PROPERTY_CATALOG_LIFECYCLE_WRITE_CH_PORT = 19001
    del lane.settings.PROPERTY_CATALOG_LIFECYCLE_RUNTIME_DIRECTORY
    (tmp_path / WRITE_ADMISSION_FILENAME).unlink()
    store, captured = _wire_command(lane, monkeypatch)
    before = {
        str(p.relative_to(tmp_path)): p.read_bytes()
        for p in tmp_path.rglob("*")
        if p.is_file()
    }
    monkeypatch.setattr(
        subject,
        "NativeWriteJournal",
        lambda *a, **k: pytest.fail("status opened journal"),
    )
    monkeypatch.setattr(
        subject,
        "replicated_write_admission_context",
        lambda *a, **k: pytest.fail("status mutated admission"),
    )
    result = json.loads(
        subject.Command().handle(workspace_id=WORKSPACE, execute=False, request_id=None)
    )
    assert result["mode"] == "status" and store.events == []
    assert captured[0][1]["append_coordinator"] is None
    assert captured[0][0].receipt_confirmation_available is False
    assert before == {
        str(p.relative_to(tmp_path)): p.read_bytes()
        for p in tmp_path.rglob("*")
        if p.is_file()
    }
    assert not lane.contexts and lane.drivers[0][0].closed == 1


@pytest.mark.parametrize("lost_ack", [False, True])
def test_command_real_store_and_native_journal_require_exact_receipt_on_replay(
    tmp_path, monkeypatch, lost_ack
):
    """Real Store/coordinator/writer/journal; wire reads and sends are offline."""
    lane = _managed_fixture(tmp_path, monkeypatch)
    real_store = subject.ClickHouseActivationControlStore
    _wire_command(lane, monkeypatch)
    monkeypatch.setattr(subject, "ClickHouseActivationControlStore", real_store)
    observed = SimpleNamespace(events=[], sends=[], covers=[], finished=False)
    target = _target(epoch=lane.identity.catalog_epoch)

    def admission(*args, **kwargs):
        return write_admission.reattest_catalog_writes(
            *args, **kwargs, http_read=lane.factory.http
        )

    def read(proof, sql, params, **kwargs):
        assert all(not c.driver.closed for c in proof.connections)
        assert params["catalog_organization_id"] == ORG
        assert params["catalog_workspace_id"] == WORKSPACE
        if sql == activation_control_event_sql("property_catalog"):
            return list(observed.events)
        assert sql == qualified_activation_sql("property_catalog")
        return [dict(asdict(target), activation_sequence=1, latest_variants=1)]

    def cover(proof, table, rows, *, columns, timeout_ms):
        assert table == ACTIVATION_CONTROL_TABLE
        assert columns == ACTIVATION_CONTROL_COLUMNS
        assert len(rows) == 1 and rows[0] in observed.events
        assert timeout_ms > 0
        assert len(proof.connections) == 3
        assert all(not c.driver.closed for c in proof.connections)
        observed.covers.append(tuple(c.name for c in proof.connections))

    def receipts():
        return [
            NativeWriteAttempt(path.read_bytes())
            for path in (tmp_path / "native-write-attempts").glob("*.json")
            if not path.name.endswith(".scope.json")
        ]

    def send(driver, **kwargs):
        assert driver.user == "catalog_control_writer"
        assert driver.host == "replica1.internal" and driver.port == 19001
        assert kwargs["settings"]["insert_quorum"] == 3
        kwargs["before_send"]()
        (attempt,) = receipts()
        assert attempt.state == "sent" and attempt.query_id == kwargs["query_id"]
        assert list(attempt.parameters) == kwargs["values"]
        observed.sends.append(attempt.query_id)
        observed.events.append(
            dict(zip(ACTIVATION_CONTROL_COLUMNS, kwargs["values"][0], strict=True))
        )
        if lost_ack:
            raise TimeoutError("offline native response lost")
        return 1

    # Preserve actual seven-table, HTTP/native and Keeper reattestation; replace
    # only post-write data/query-log observations and the native INSERT transport.
    monkeypatch.setattr(native_write_proof, "reattest_catalog_writes", admission)
    monkeypatch.setattr(native_write_proof.NativeWriteProof, "agreed_read", read)
    monkeypatch.setattr(native_write_proof.NativeWriteProof, "cover", cover)
    monkeypatch.setattr(
        native_write_proof.NativeWriteProof,
        "settled",
        lambda *a, **k: observed.finished,
    )
    monkeypatch.setattr(durable_native_writer, "insert_once", send)

    def execute():
        return json.loads(
            subject.Command().handle(
                workspace_id=WORKSPACE, execute=True, request_id=REQUEST
            )
        )

    if lost_ack:
        with pytest.raises(CommandError, match="response lost"):
            execute()
        (sent,) = receipts()
        assert sent.state == "sent" and observed.events
        with pytest.raises(CommandError, match="unresolved"):
            execute()
        assert receipts()[0].encode() == sent.encode()
        assert len(observed.sends) == 1 and not observed.covers
        observed.finished = True  # Positive original completion, not row visibility.
    else:
        assert execute()["idempotent"] is False
    assert execute()["idempotent"] is True
    assert len(observed.events) == len(observed.sends) == 1
    assert receipts()[0].state == "complete" and observed.covers
    assert all(driver.closed == 1 and not driver.writes for driver, _ in lane.drivers)
    assert all(driver.closed for driver, _ in lane.factory.clients)


@pytest.mark.parametrize(
    "failure", ["adapter", "selection", "authorization", "request", "runtime_directory"]
)
def test_command_failure_closes_direct_and_proof_resources(
    tmp_path, monkeypatch, failure
):
    lane = _managed_fixture(tmp_path, monkeypatch)
    store, _ = _wire_command(lane, monkeypatch)
    if failure == "adapter":
        monkeypatch.setattr(
            reader_activation,
            "ReaderActivationClient",
            lambda *a, **k: (_ for _ in ()).throw(ValueError("adapter failed")),
        )
    elif failure == "selection":
        monkeypatch.setattr(
            subject,
            "run_initial_activation",
            lambda **k: (_ for _ in ()).throw(ValueError("selection failed")),
        )
    elif failure == "runtime_directory":
        lane.settings.PROPERTY_CATALOG_LIFECYCLE_RUNTIME_DIRECTORY = "relative"
    elif failure == "authorization":
        from tracer.management.commands import (
            ch25_property_catalog_lifecycle_controller as controller,
        )

        calls = iter(
            [
                ((SimpleNamespace(organization_id=ORG, workspace_id=WORKSPACE),), ()),
                ((), (WORKSPACE,)),
            ]
        )
        monkeypatch.setattr(
            controller, "discover_workspace_scopes", lambda _: next(calls)
        )
    with pytest.raises(CommandError):
        subject.Command().handle(
            workspace_id=WORKSPACE,
            execute=True,
            request_id="invalid" if failure == "request" else REQUEST,
        )
    assert not store.events
    assert all(d.closed == 1 for d, _ in lane.drivers)
    assert all(c.closed for c, _ in lane.factory.clients)
    if failure in {"request", "runtime_directory"}:
        assert not lane.contexts and not lane.drivers
