"""F76: default-OFF CORS equivalence for direct callers, not just the UI.

Only the feature's boot-time settings expressions are evaluated. Importing the
whole production settings module again would initialize unrelated services.
"""

import ast
import errno
import importlib
import importlib.util
import logging
import socket
from pathlib import Path
from types import ModuleType, SimpleNamespace
from uuid import UUID

import pytest
from django.test import Client, override_settings

FLAG = "DSX_ERRORS_REQUEST_ID_EXPOSED"
ORIGIN = "http://localhost:3100"
SETTINGS_SOURCE = Path(__file__).resolve().parents[1] / "settings" / "settings.py"


def boot_settings(value=None):
    """Execute the actual feature binding and wiring with only a synthetic env."""
    source = ast.parse(SETTINGS_SOURCE.read_text())
    nodes = [
        node
        for node in source.body
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == FLAG for t in node.targets)
        )
        or (
            isinstance(node, ast.Expr)
            and any(
                isinstance(n, ast.Name) and n.id == "_request_id_cors_overrides"
                for n in ast.walk(node)
            )
        )
    ]
    assert len(nodes) == 2, (
        "settings must wire the default-OFF binding and exposure gate"
    )
    helper = importlib.import_module("tfc.settings.request_id_cors")
    module = ModuleType("f76_test_boot")
    env = {} if value is None else {FLAG: value}
    module.__dict__.update(
        os=SimpleNamespace(getenv=env.get),
        sys=SimpleNamespace(modules={module.__name__: module}),
        _request_id_cors_overrides=helper.request_id_cors_overrides,
    )
    exec(
        compile(ast.Module(body=nodes, type_ignores=[]), str(SETTINGS_SOURCE), "exec"),
        module.__dict__,
    )
    return module


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    attempts = []

    def refuse(operation, destination):
        attempts.append((operation, destination))
        # Redis translates OSError into RedisError so its pre-existing import
        # probe can use the product's local-lock fallback, without any I/O.
        raise ConnectionRefusedError(
            errno.ECONNREFUSED, f"F76 refused {operation} to {destination!r}"
        )

    def deny_connect(_socket, address):
        refuse("connect", address)

    def deny_connect_ex(_socket, address):
        refuse("connect_ex", address)

    def deny_create_connection(address, *args, **kwargs):
        refuse("create_connection", address)

    def deny_getaddrinfo(host, port, *args, **kwargs):
        refuse("getaddrinfo", (host, port))

    monkeypatch.setattr(socket.socket, "connect", deny_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", deny_connect_ex)
    monkeypatch.setattr(socket, "create_connection", deny_create_connection)
    monkeypatch.setattr(socket, "getaddrinfo", deny_getaddrinfo)

    # Import URLconf and its views under the same guard, never by making a
    # warm-up request. Only pre-existing import probes are exempt from zero.
    from django.urls import get_resolver

    try:
        list(get_resolver().url_patterns)
    finally:
        for operation, destination in attempts:
            logging.getLogger(__name__).warning(
                "F76 warm-up refused %s to %r", operation, destination
            )
    attempts.clear()

    def measured(call, *args, **kwargs):
        try:
            return call(*args, **kwargs)
        finally:
            # Also fail when product code catches the refused OSError.
            assert not attempts, f"F76 measured network attempts: {attempts!r}"

    yield measured
    # Cover helper assertions and fixture work outside an individual call too.
    assert not attempts, f"F76 measured network attempts: {attempts!r}"


@pytest.fixture
def cors_boot(settings, no_network):
    from tfc.settings.settings import MIDDLEWARE

    # Exercise the same CORS and correlation emitter chain as production.
    settings.MIDDLEWARE = MIDDLEWARE
    settings.CORS_ALLOW_ALL_ORIGINS = False
    settings.CORS_ALLOWED_ORIGINS = [ORIGIN]
    settings.CORS_ALLOWED_ORIGIN_REGEXES = []

    def configure(value):
        boot = no_network(boot_settings, value)
        settings.DSX_ERRORS_REQUEST_ID_EXPOSED = getattr(boot, FLAG)
        # OFF must leave the name absent, not install an empty or custom value.
        if hasattr(settings, "CORS_EXPOSE_HEADERS"):
            del settings.CORS_EXPOSE_HEADERS
        if hasattr(boot, "CORS_EXPOSE_HEADERS"):
            settings.CORS_EXPOSE_HEADERS = boot.CORS_EXPOSE_HEADERS
        return boot

    return configure


@pytest.mark.parametrize("value", [None, "false", "0", "no", "", "unexpected"])
def test_off_boot_leaves_cors_expose_headers_unset(no_network, value):
    boot = no_network(boot_settings, value)
    assert getattr(boot, FLAG) is False
    assert not hasattr(boot, "CORS_EXPOSE_HEADERS")


def test_missing_predicate_preserves_existing_cors_configuration():
    assert importlib.util.find_spec("tfc.settings.request_id_cors") is not None, (
        "F76 must provide the request-id CORS settings helper"
    )
    helper = importlib.import_module("tfc.settings.request_id_cors")
    baseline = SimpleNamespace(CORS_EXPOSE_HEADERS=["Existing-Header"])
    assert helper.request_id_cors_overrides(baseline) == {}
    assert baseline.CORS_EXPOSE_HEADERS == ["Existing-Header"]


@pytest.mark.parametrize("value", ["true", "1", "yes", "on", " TRUE "])
def test_on_boot_exposes_only_the_request_id(no_network, value):
    boot = no_network(boot_settings, value)
    assert getattr(boot, FLAG) is True
    assert boot.CORS_EXPOSE_HEADERS == ["X-Request-ID"]


@pytest.mark.parametrize("value", [None, "false", "true"])
@pytest.mark.parametrize(
    "path", ["/model-hub/eval-mapping-preview/", "/missing-request-id-route/"]
)
def test_direct_cross_origin_error_exposure_obeys_boot_gate(
    cors_boot, no_network, value, path
):
    boot = cors_boot(value)
    responses = [no_network(Client().get, path, HTTP_ORIGIN=ORIGIN) for _ in range(2)]
    for response in responses:
        assert response.status_code in (401, 403, 404)
        assert response["Access-Control-Allow-Origin"] == ORIGIN
        assert str(UUID(response["X-Request-ID"])) == response["X-Request-ID"]
        if getattr(boot, FLAG):
            assert response["Access-Control-Expose-Headers"].lower() == "x-request-id"
        else:
            assert "Access-Control-Expose-Headers" not in response
    assert responses[0]["X-Request-ID"] != responses[1]["X-Request-ID"]


@pytest.mark.parametrize("value", [None, "true"])
def test_exposure_never_admits_a_disallowed_origin(cors_boot, no_network, value):
    cors_boot(value)
    response = no_network(
        Client().get,
        "/model-hub/eval-mapping-preview/",
        HTTP_ORIGIN="http://localhost:3199",
    )
    assert "Access-Control-Allow-Origin" not in response
    assert "Access-Control-Expose-Headers" not in response


def test_off_preserves_existing_deployment_expose_headers():
    assert importlib.util.find_spec("tfc.settings.request_id_cors") is not None, (
        "F76 must provide the request-id CORS settings helper"
    )
    helper = importlib.import_module("tfc.settings.request_id_cors")
    with override_settings(
        DSX_ERRORS_REQUEST_ID_EXPOSED=False, CORS_EXPOSE_HEADERS=["Existing-Header"]
    ):
        from django.conf import settings

        assert helper.request_id_cors_overrides(settings) == {}
        assert settings.CORS_EXPOSE_HEADERS == ["Existing-Header"]
