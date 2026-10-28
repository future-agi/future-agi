"""Worker admission, portable address classes and default TLS trust."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest
import requests

from tfc.utils import outbound_destination as destination


class CompletedWorker:
    returncode = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def communicate(self, *args, **kwargs):
        return b'{"status": 200, "headers": {}, "body": ""}', b""


def test_worker_admission_is_bounded_and_queue_uses_deadline(monkeypatch):
    entered = threading.Condition()
    release = threading.Event()
    started = 0

    class HeldWorker(CompletedWorker):
        def communicate(self, encoded, **kwargs):
            nonlocal started
            payload = json.loads(encoded)
            if payload["url"].endswith("/held"):
                with entered:
                    started += 1
                    entered.notify_all()
                assert release.wait(10), "test must release held workers"
            return super().communicate()

    spawn = Mock(side_effect=lambda *args, **kwargs: HeldWorker())
    monkeypatch.setattr(destination.subprocess, "Popen", spawn)
    with ThreadPoolExecutor(max_workers=32) as executor:
        futures = [
            executor.submit(
                destination.request,
                method="GET",
                url="http://public.fixture.test/held",
                timeout=10,
            )
            for _ in range(32)
        ]
        try:
            with entered:
                assert entered.wait_for(lambda: started == 32, timeout=5)
            with pytest.raises(destination.DestinationError, match="^deadline$"):
                destination.request(
                    method="GET", url="http://public.fixture.test/excess", timeout=0.1
                )
            assert spawn.call_count == 32, "no worker may start without capacity"
        finally:
            release.set()
        assert all(future.result().status_code == 200 for future in futures)
    assert (
        destination.request(
            method="GET", url="http://public.fixture.test/available", timeout=1
        ).status_code
        == 200
    )
    assert spawn.call_count == 33


def test_failed_worker_start_releases_capacity(monkeypatch):
    spawn = Mock(side_effect=OSError("fixture startup failure"))
    monkeypatch.setattr(destination.subprocess, "Popen", spawn)
    for _ in range(33):
        with pytest.raises(destination.DestinationError, match="^OSError$"):
            destination.request(
                method="GET", url="http://public.fixture.test/", timeout=0.1
            )
    assert spawn.call_count == 33


def test_protocol_assignment_range_is_explicitly_refused(monkeypatch):
    # Emulate older interpreter classification, leaving the production guard intact.
    for name in ("is_global", "is_private", "is_reserved"):
        monkeypatch.setattr(
            destination.ipaddress.IPv4Address,
            name,
            property(lambda self, value=name == "is_global": value),
        )
    spawn = Mock(side_effect=AssertionError("preflight must precede worker startup"))
    monkeypatch.setattr(destination.subprocess, "Popen", spawn)
    with pytest.raises(destination.DestinationError, match="^address_class$"):
        destination.request(method="GET", url="http://192.0.0.192/")
    spawn.assert_not_called()


def test_https_keeps_requests_default_ca_bundle(monkeypatch):
    context = Mock()
    make_context = Mock(return_value=context)
    connection = Mock()
    response = connection.getresponse.return_value
    response.status = 200
    response.getheader.side_effect = lambda key, default=None: default
    response.getheaders.return_value = []
    response.read.return_value = b""
    monkeypatch.setattr(destination.ssl, "create_default_context", make_context)
    monkeypatch.setattr(
        destination.http.client, "HTTPSConnection", Mock(return_value=connection)
    )
    monkeypatch.setattr(
        destination,
        "_resolve",
        lambda *args: destination.ipaddress.ip_address("93.184.216.34"),
    )
    destination._exchange(
        {
            "method": "GET",
            "url": "https://public.fixture.test/",
            "timeout": 1,
            "max_bytes": 20,
        }
    )
    make_context.assert_called_once_with(cafile=requests.certs.where())
    assert connection._create_connection is not None
