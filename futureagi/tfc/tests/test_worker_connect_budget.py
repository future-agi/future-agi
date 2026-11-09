"""Process-class defaults and a delayed PostgreSQL handshake, without retries."""
import select
import socket
import socketserver
import threading
import time

import psycopg
import pytest
from django.db import connection

from tfc.settings.runtime_setting_specs import (
    INTERACTIVE_READ_SETTING_SPECS, load_numeric_settings,
)


def budget(service, override=None):
    source = {"SERVICE_TYPE": service}
    if override is not None:
        source["PG_CONNECT_TIMEOUT_SECONDS"] = override
    return load_numeric_settings(
        {"PG_CONNECT_TIMEOUT_SECONDS": INTERACTIVE_READ_SETTING_SPECS["PG_CONNECT_TIMEOUT_SECONDS"]},
        source=source,
    )["PG_CONNECT_TIMEOUT_SECONDS"]


@pytest.mark.parametrize("service", ["worker", "temporal-worker"])
def test_workers_default_to_five_seconds(service):
    assert budget(service) == 5


@pytest.mark.parametrize("service", ["", "backend", "web", "api", "grpc", "beat", "flower", "bootstrap"])
def test_request_and_other_processes_keep_one_second(service):
    assert budget(service) == 1


@pytest.mark.parametrize("service", ["worker", "temporal-worker", "backend"])
@pytest.mark.parametrize("override", ["1", "3", "5"])
def test_explicit_override_wins(service, override):
    assert budget(service, override) == int(override)


@pytest.mark.parametrize("override", ["0", "6", "invalid"])
def test_override_bounds_still_apply(override):
    with pytest.raises(ValueError):
        budget("worker", override)


@pytest.mark.django_db(transaction=True)
def test_worker_handshake_delay_succeeds_without_a_retry():
    params = connection.get_connection_params()
    upstream_host, upstream_port = params.get("host") or "127.0.0.1", int(params.get("port") or 5432)
    accepted = []
    stop = threading.Event()

    class DelayedHandshake(socketserver.BaseRequestHandler):
        def handle(self):
            accepted.append(1)
            # Longer than libpq's effective minimum two-second timeout, but
            # safely inside the worker's five-second connect budget.
            time.sleep(2.5)
            with socket.create_connection((upstream_host, upstream_port), timeout=5) as upstream:
                peers = (self.request, upstream)
                while not stop.is_set():
                    readable, _, _ = select.select(peers, [], [], 0.1)
                    for sock in readable:
                        data = sock.recv(65536)
                        if not data:
                            return
                        (upstream if sock is self.request else self.request).sendall(data)

    class Proxy(socketserver.ThreadingTCPServer):
        daemon_threads = True

    with Proxy(("127.0.0.1", 0), DelayedHandshake) as proxy:
        thread = threading.Thread(target=proxy.serve_forever, daemon=True)
        thread.start()
        try:
            params.update(host="127.0.0.1", port=proxy.server_address[1], connect_timeout=budget("worker"))
            with psycopg.connect(**params) as db:
                assert db.execute("SELECT 1").fetchone() == (1,)
            assert accepted == [1]
        finally:
            stop.set()
            proxy.shutdown()
            thread.join(timeout=5)
