"""Real native protocol writes with fake I/O; Python 3.12, sockets denied."""

import io
import runpy
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from clickhouse_driver import Client

support = runpy.run_path(
    str(Path(__file__).with_name("test_falcon_transport_offline.py"))
)


class DeadlineTests(unittest.TestCase):
    setUp = support["TransportTests"].setUp
    install = support["TransportTests"].install
    scope = support["TransportTests"].scope

    def native(self, socket_timeout, operation_timeout, *, budget=60, ping=False):
        self.guard.deadline = time.monotonic() + budget
        client = Client(host="offline.invalid", send_receive_timeout=operation_timeout)
        connection = client.connection
        connection.connected = True
        connection.server_info = SimpleNamespace(used_revision=0)
        connection.context.settings = {}
        connection.context.client_settings = {}
        observed = []

        class Socket:
            timeout = socket_timeout

            def gettimeout(self):
                return self.timeout

            def settimeout(self, value):
                self.timeout = value

        class Wire(io.BytesIO):
            def flush(wire):
                observed.append(connection.socket.gettimeout())
                super().flush()

        connection.socket = Socket()
        connection.fout = Wire()

        # Real driver ping reads a PONG packet; no monkeypatch of send_query/ping.
        class Incoming(io.BytesIO):
            def read_one(self):
                return self.read(1)[0]

        connection.fin = Incoming(b"\x04")
        connection.sync_request_timeout = operation_timeout
        with self.scope():
            self.transport.guard_native_client(client)
            if ping:
                self.assertTrue(connection.ping())
            else:
                connection.send_query("SELECT 1")
        self.assertTrue(connection.fout.getvalue(), "the real driver must send bytes")
        self.assertEqual(len(observed), 1)
        return observed[0], connection.socket.gettimeout()

    def test_real_query_preserves_caller_existence_read_deadline(self):
        during, after = self.native(9.5, 300)
        self.assertEqual((during, after), (9.5, 9.5))

    def test_real_query_preserves_shorter_configured_operation_timeout(self):
        during, after = self.native(None, 0.1)
        self.assertEqual((during, after), (0.1, 0.1))

    def test_real_query_long_operation_is_capped_by_analysis_budget(self):
        for socket_timeout, configured in ((300, 300), (None, None)):
            with self.subTest(socket_timeout=socket_timeout, configured=configured):
                during, after = self.native(socket_timeout, configured, budget=2)
                self.assertGreater(during, 0)
                self.assertLessEqual(during, 2)
                self.assertEqual(during, after)

    def test_real_ping_cannot_temporarily_widen_socket_deadline(self):
        during, after = self.native(0.05, 10, ping=True)
        self.assertEqual((during, after), (0.05, 0.05))

    def test_extracted_annotations_do_not_load_requests_session(self):
        # The same full class that failed on 3.12, with intentionally absent
        # requests.Session and Image annotation dependencies. Bodies unchanged.
        ns = support["definitions"](
            "agentic_eval/core/embeddings/serving_client.py",
            {"ModelServingClient"},
            {},
        )
        cls = ns["ModelServingClient"]
        self.assertEqual(
            cls._create_session.__annotations__["return"], "requests.Session"
        )


if __name__ == "__main__":
    unittest.main()
