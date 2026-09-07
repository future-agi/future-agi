"""Exercise default source wiring; no live services or simulated timing claims."""

from contextlib import ExitStack
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tracer.services.clickhouse.v2.property_catalog import (
    source_capture_factory as factory,
)
from tracer.services.clickhouse.v2.property_catalog.publisher import (
    SharedCatalogDeadline,
)
from tracer.services.clickhouse.v2.property_catalog.source_capture import (
    SourceCaptureError,
)
from tracer.services.clickhouse.v2.property_catalog.source_capture_runtime import (
    CaptureAwareCutoffFreezer,
)
from tracer.services.clickhouse.v2.property_catalog.write_admission import WriteMember
from tracer.tests.test_property_catalog_dev_rollout import _unit_runtime_config
from tracer.tests.test_property_catalog_source_capture import specification


@pytest.fixture
def case(tmp_path):
    config = _unit_runtime_config(str(tmp_path))
    spec = replace(
        specification(),
        source_database=config.source.database,
        catalog_database=config.catalog.database,
    )
    member = WriteMember(
        str(uuid4()),
        "direct-node",
        "node-a",
        spec.source_server_uuid,
        (),
        "http://direct.invalid:8123",
    )
    connection = SimpleNamespace(
        name=member.name, driver=SimpleNamespace(host="direct.invalid", port=9000)
    )
    proof = SimpleNamespace(
        identity=SimpleNamespace(producer_stream_id=spec.installation_id),
        admission=SimpleNamespace(members=(member,)),
        connections=(connection,),
    )
    created, closed = [], []

    def native(connection):
        driver = SimpleNamespace(
            **{
                key: getattr(connection, key)
                for key in (
                    "host",
                    "port",
                    "user",
                    "database",
                    "server_enforced_readonly",
                )
            },
            close=lambda: closed.append(connection),
        )
        created.append(driver)
        return driver

    with ExitStack() as resources:
        owner = factory.ManagedSourceCaptureFactory(
            config=config,
            writer=SimpleNamespace(proof=proof),
            source_driver=object(),
            native_client_factory=native,
            resources=resources,
        )
        yield SimpleNamespace(
            owner=owner,
            config=config,
            spec=spec,
            member=member,
            connection=connection,
            created=created,
            closed=closed,
            resources=resources,
        )


def test_default_capture_factory_is_lazy_and_fenced_completion_needs_no_source(case):
    live = SimpleNamespace(freeze=lambda **kw: pytest.fail("construction read source"))
    capture, freezer = case.owner.build(
        live_reader=live,
        deadline=SharedCatalogDeadline(wall_ms=1000),
        now=lambda: case.config.span_until,
    )
    assert isinstance(freezer, CaptureAwareCutoffFreezer)
    assert capture.cutoff_freezer is freezer
    assert capture._source == case.config.source.database
    assert not case.created


def test_resume_uses_persisted_direct_member_without_live_source_discovery(
    case, monkeypatch
):
    monkeypatch.setattr(
        case.owner, "live_driver", lambda: pytest.fail("resume resampled live source")
    )
    driver = case.owner.source_for(case.spec, captured=True)
    assert (driver.host, driver.port) == ("direct.invalid", 9000)
    assert driver.database == case.spec.capture_database
    assert driver.user == case.config.source.user
    assert driver.server_enforced_readonly is True
    assert case.owner.source_for(case.spec, captured=True) is driver
    assert len(case.created) == 1
    case.resources.close()
    assert len(case.closed) == 1


def test_resume_never_substitutes_a_different_admitted_member(case):
    with pytest.raises(SourceCaptureError, match="not admitted"):
        case.owner.source_for(replace(case.spec, source_server_uuid=str(uuid4())))
    assert not case.created


def test_live_and_captured_readers_never_share_table_binding(case):
    live = case.owner.source_for(case.spec)
    captured = case.owner.source_for(case.spec, captured=True)
    assert live is not captured
    assert live.database == case.spec.source_database
    assert captured.database == case.spec.capture_database
    assert live.user == captured.user == case.config.source.user


def test_captured_scanner_uses_exact_persisted_table_and_source_credentials(case):
    capture, _ = case.owner.build(
        live_reader=SimpleNamespace(
            freeze=lambda **kw: pytest.fail("construction read source")
        ),
        deadline=SharedCatalogDeadline(wall_ms=1000),
        now=lambda: case.config.span_until,
    )
    reader = capture._reader_factory(case.spec)
    assert reader._client.source_database == case.spec.capture_database
    assert reader._client.source_table == case.spec.capture_table
    assert case.created[0].database == case.spec.capture_database
    assert case.created[0].user == case.config.source.user


@pytest.mark.parametrize(
    "field,value",
    [
        ("database", "foreign"),
        ("user", "writer"),
        ("host", "lb.invalid"),
        ("port", 9001),
        ("server_enforced_readonly", False),
    ],
)
def test_pinned_source_driver_refuses_changed_identity_before_query(
    case, monkeypatch, field, value
):
    driver = case.owner.source_for(case.spec)
    setattr(case.created[0], field, value)
    with pytest.raises(SourceCaptureError, match="identity changed"):
        driver.execute_read("SELECT 1", {}, timeout_ms=1000)


def test_pinned_empty_read_uses_native_socket_proof_and_does_not_forward_settings(
    case, monkeypatch
):
    from tracer.services.clickhouse.v2.property_catalog import native_read_transport

    calls = []

    def read_once(driver, **kwargs):
        calls.append((driver, kwargs))
        return [], [("id", "String")]

    monkeypatch.setattr(native_read_transport, "read_once", read_once)
    pinned = case.owner.source_for(case.spec, captured=True)
    result = pinned.execute_read(
        "SELECT id FROM t WHERE id=%(id)s",
        {"id": "x"},
        timeout_ms=1000,
        settings={"readonly": 2},
    )
    assert result == ([], [("id", "String")], None)
    assert calls[0][1] == {
        "member": case.member,
        "database": case.spec.capture_database,
        "user": case.config.source.user,
        "sql": "SELECT id FROM t WHERE id=%(id)s",
        "params": {"id": "x"},
        "timeout_ms": 1000,
    }


def test_capture_backend_never_uses_source_role_for_ddl(case, monkeypatch):
    from tracer.services.clickhouse.v2.property_catalog import (
        source_capture_capacity,
        source_capture_native,
    )

    marker = object()
    monkeypatch.setattr(
        source_capture_capacity.SourceCaptureCapacity,
        "resource_budget",
        lambda self: marker,
    )
    monkeypatch.setattr(
        source_capture_native,
        "NativeSourceCaptureBackend",
        lambda driver, **kwargs: (driver, kwargs),
    )
    writer, options = case.owner.backend(case.spec, object())
    assert writer.user == case.config.catalog.user
    assert writer.database == case.config.source.database
    assert writer.host == "direct.invalid"
    assert writer.server_enforced_readonly is False
    assert options["source_reader"].user == case.config.source.user
    assert options["budget"] is marker


def test_fresh_source_selection_preserves_source_credentials(case, monkeypatch):
    from tracer.services.clickhouse.v2.property_catalog import source_capture_route

    calls = []
    metadata = {
        "server_uuid": case.spec.source_server_uuid,
        "table_uuid": case.spec.source_table_uuid,
        "create_table_query": "source DDL",
    }

    def resolve(original, **kwargs):
        calls.append(kwargs)
        raw = kwargs["driver_factory"](case.connection)
        return case.member, raw, metadata

    monkeypatch.setattr(source_capture_route, "resolve_capture_source", resolve)
    assert case.owner.metadata() == metadata
    assert len(calls) == 1
    assert case.created[0].user == case.config.source.user
    assert case.created[0].database == case.spec.source_database
    assert case.owner.source_for(case.spec) is case.owner.live_driver()
