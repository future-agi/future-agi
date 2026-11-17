"""ClickHouse HTTP bootstrap/retry and native-pool regression; sockets denied."""

import queue
import runpy
import unittest
from http.client import RemoteDisconnected
from math import isfinite
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from clickhouse_connect.driver.httpclient import HttpClient
from urllib3 import PoolManager
from urllib3.exceptions import ProtocolError
from urllib3.util import Timeout

support = runpy.run_path(
    str(Path(__file__).with_name("test_falcon_transport_offline.py"))
)
definitions, module = (support[k] for k in ("definitions", "module"))


class ClickHouseTransportTests(unittest.TestCase):
    setUp = support["TransportTests"].setUp
    install = support["TransportTests"].install
    scope = support["TransportTests"].scope

    def test_real_clickhouse_retry_after_backoff_rechecks(self):
        with self.scope():
            client = HttpClient.__new__(HttpClient)
            client.headers, client.params = {}, {}
            client._send_progress, client._progress_interval = False, None
            client._autogenerate_query_id = False
            client.url = "http://offline.invalid"
            client.timeout, client.http_retries = Timeout(connect=10, read=None), 1
            client.server_host_name = None
            client.http = self.transport.guarded_http_pool()
            self.addCleanup(client.http.clear)
            sends = []

            def wire(pool, method, url, **kwargs):
                sends.append(method)
                self.assertEqual(kwargs["retries"], 0)
                self.assertLessEqual(kwargs["timeout"].read_timeout, 2)
                raise ProtocolError(
                    "synthetic stale connection"
                ) from RemoteDisconnected()

            def backoff(*args):
                self.active = False

            with (
                patch.object(PoolManager, "urlopen", wire),
                patch("clickhouse_connect.driver.httpclient.time.sleep", backoff),
            ):
                with self.assertRaises(self.Aborted):
                    client._raw_request(b"SELECT 1", {}, retries=0)
            self.assertEqual(sends, ["POST"])

    def test_trace_read_bootstrap_installs_pool_before_constructor(self):
        sent = []

        def wire(pool, method, url, **kwargs):
            sent.append(method)
            self.active = False
            return SimpleNamespace(status=200)

        def factory(**kwargs):
            self.assertEqual(kwargs["query_retries"], 0)
            pool = kwargs["pool_mgr"]
            self.addCleanup(pool.clear)
            pool.request(
                "POST",
                "http://offline.invalid/version",
                timeout=Timeout(connect=10, read=10),
            )
            pool.request(
                "POST",
                "http://offline.invalid/timezone",
                timeout=Timeout(connect=10, read=10),
            )
            self.fail("constructor continued after revocation")

        self.modules.update(
            {
                "clickhouse_connect": module("connect", get_client=factory),
                "tfc.telemetry.instrumentation": module(
                    "instrument", instrument_clickhouse_client=lambda c: c
                ),
            }
        )
        self.install()
        fn = definitions(
            "tracer/services/clickhouse/application_read_transport.py",
            {"create_application_read_http_client"},
            {
                "isfinite": isfinite,
                "application_read_settings": lambda s: s,
            },
        )["create_application_read_http_client"]
        with self.scope(), patch.object(PoolManager, "urlopen", wire):
            with self.assertRaises(self.Aborted):
                fn(host="offline.invalid")
        self.assertEqual(sent, ["POST"])

    def test_background_native_clients_never_reuse_or_return_to_interactive_pool(self):
        ns = definitions(
            "tracer/services/clickhouse/client.py",
            {"_get_client", "_return_client"},
            {
                "CHDriver": object,
                "queue": queue,
            },
            owner="ClickHouseClient",
        )
        private = SimpleNamespace(
            _analysis_transport_guard=self.guard, disconnect=Mock()
        )
        owner = SimpleNamespace(_pool=Mock(), _create_client=Mock(return_value=private))
        with self.scope():
            self.assertIs(ns["_get_client"](owner), private)
            ns["_return_client"](owner, private)
        owner._pool.get_nowait.assert_not_called()
        owner._pool.put_nowait.assert_not_called()
        private.disconnect.assert_called_once()


if __name__ == "__main__":
    unittest.main()
