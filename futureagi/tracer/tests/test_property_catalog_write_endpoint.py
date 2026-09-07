"""Native listener discovery cannot itself authorize a catalog writer."""

from types import SimpleNamespace
from uuid import UUID

import pytest

from tracer.services.clickhouse.v2.property_catalog import write_endpoint as subject

DATABASE = "property_catalog_dev_endpoint"
DATABASE_UUID = str(UUID(int=101))


def discovery(**overrides):
    calls = []

    def read(sql, params, **kwargs):
        calls.append((sql, params, kwargs))
        return (
            [("node1", DATABASE, DATABASE_UUID, 18123)],
            [
                (name, "")
                for name in (
                    "hostname",
                    "connected_database",
                    "database_uuid",
                    "http_port",
                )
            ],
            {},
        )

    options = {
        "driver": SimpleNamespace(database=DATABASE, execute_read=read),
        "native_member_host": "node1.clickhouse.svc",
        "expected_hostname": "node1",
        "database": DATABASE,
        "scheme": "http",
        "timeout_ms": 500,
    }
    options.update(overrides)
    return options, calls


@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize("host", ["node1.clickhouse.svc", "10.10.2.4", "2001:db8::1"])
def test_discovers_configured_protocol_listener_without_default_ports(scheme, host):
    args, calls = discovery(scheme=scheme, native_member_host=host)
    route = subject.discover_catalog_http_route(**args)
    uri_host = f"[{host}]" if ":" in host else host
    assert route.origin == f"{scheme}://{uri_host}:18123"
    assert route.hostname == "node1" and route.database_uuid == DATABASE_UUID
    assert len(calls) == 1
    assert "getServerPort(%(port_name)s)" in calls[0][0]
    assert calls[0][1] == {"port_name": f"{scheme}_port", "database": DATABASE}
    assert calls[0][2]["settings"]["readonly"] == 2
    route.require_same_http_identity(
        hostname="node1", database=DATABASE, database_uuid=DATABASE_UUID
    )


@pytest.mark.parametrize("field", ["hostname", "database", "database_uuid"])
def test_other_protocol_cannot_serve_a_different_database_or_node(field):
    args, _ = discovery()
    route = subject.discover_catalog_http_route(**args)
    observed = {
        "hostname": "node1",
        "database": DATABASE,
        "database_uuid": DATABASE_UUID,
    }
    observed[field] = "different"
    with pytest.raises(subject.CatalogEndpointError, match="identities differ"):
        route.require_same_http_identity(**observed)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "http://node1",
        "node1:8123",
        "node1\n",
        "node1/path",
        "user@node1",
        "a..b",
        "fe80::1%en0",
    ],
)
def test_invalid_member_host_is_rejected_before_network(bad):
    args, calls = discovery(native_member_host=bad)
    with pytest.raises(subject.CatalogEndpointError):
        subject.discover_catalog_http_route(**args)
    assert not calls


@pytest.mark.parametrize(
    "field,value",
    [
        ("scheme", "ftp"),
        ("timeout_ms", True),
        ("timeout_ms", 0),
        ("timeout_ms", 30_001),
        ("database", "catalog;SELECT1"),
        ("expected_hostname", ""),
    ],
)
def test_invalid_contract_is_rejected_before_network(field, value):
    args, calls = discovery(**{field: value})
    with pytest.raises(subject.CatalogEndpointError):
        subject.discover_catalog_http_route(**args)
    assert not calls


@pytest.mark.parametrize(
    "index,value",
    [
        (0, "other-node"),
        (1, "foreign-db"),
        (2, str(UUID(int=0))),
        (2, "not-a-uuid"),
        (3, 0),
        (3, 65_536),
        (3, True),
    ],
)
def test_partial_or_changed_native_identity_cannot_produce_route(index, value):
    args, _ = discovery()
    original = args["driver"].execute_read

    def altered(*a, **kw):
        rows, columns, stats = original(*a, **kw)
        row = list(rows[0])
        row[index] = value
        return [row], columns, stats

    args["driver"].execute_read = altered
    with pytest.raises(subject.CatalogEndpointError):
        subject.discover_catalog_http_route(**args)


def test_absent_https_listener_does_not_retry_plain_http():
    args, _ = discovery(scheme="https")
    calls = []

    def unavailable(sql, params, **kwargs):
        calls.append(params["port_name"])
        raise RuntimeError("There is no port named https_port")

    args["driver"].execute_read = unavailable
    with pytest.raises(subject.CatalogEndpointError):
        subject.discover_catalog_http_route(**args)
    assert calls == ["https_port"]
