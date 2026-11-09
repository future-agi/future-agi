"""The pinned transport against real local receivers on 127.0.0.1, and the extended ``classify_ip`` (DESIGN.md
sections 6.2 and 13.1). No Django: ``tfc.utils.ssrf_guard`` needs only urllib3 and certifi.

The transport is handed the address to connect to; vetting is the core's job (``test_core_send``), so a loopback
receiver is fine here and nothing here could reach another host.
"""

from __future__ import annotations

import socket
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tfc.utils import ssrf_guard, ssrf_post
from webhooks.core.ipclass import classify
from webhooks.core.ports import PinnedPost
from webhooks.transport import PinnedTransport


class _Handler(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *_args):
        pass

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(n)
        _Handler.seen.append((self.path, dict(self.headers.items()), body))
        mode = self.path.split("/")[1] if "/" in self.path else ""
        if mode == "redirect":
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest")
            self.end_headers()
            return
        if mode == "trickle":
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            for _ in range(20):
                try:
                    self.wfile.write(b"x")
                    self.wfile.flush()
                except OSError:
                    return
                time.sleep(0.4)
            return
        if mode == "slowheaders":
            time.sleep(3)
        payload = b"y" * 10_000 if mode == "big" else b'{"ok":true}'
        self.send_response(204 if mode == "nocontent" else 200)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Content-Encoding", "gzip" if mode == "big" else "identity")
        self.end_headers()
        self.wfile.write(payload)


class TransportRedactionTests(unittest.TestCase):
    def test_an_unexpected_error_leaves_no_frame_variables_below_the_adapter(self):
        from unittest import mock

        def broken_safe_post(**kwargs):
            secret_headers = dict(kwargs["headers"])  # noqa: F841 - held in this frame, like safe_post's own
            raise RuntimeError("bug in the sender")

        req = PinnedPost(
            scheme="https",
            host="hooks.acme.ai",
            port=443,
            target="/x?token=q-tok-5d1e",
            addresses=("93.184.215.14",),
            headers=(("Authorization", "Bearer hv-77a1"),),
            body=b"body-mark-31c",
            deadline=time.monotonic() + 5,
        )
        with mock.patch.object(ssrf_post, "safe_post", broken_safe_post):
            with self.assertRaises(RuntimeError) as info:
                PinnedTransport().post(req)
        tb = info.exception.__traceback__
        while tb is not None:
            seen = repr(dict(tb.tb_frame.f_locals))
            for needle in ("hv-77a1", "q-tok-5d1e", "body-mark-31c"):
                self.assertNotIn(needle, seen, tb.tb_frame.f_code.co_name)
            tb = tb.tb_next


class TransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.server.daemon_threads = True
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def post(self, target, timeout_s=5.0, addresses=("127.0.0.1",)):
        _Handler.seen.clear()
        req = PinnedPost(
            scheme="http",
            host="hooks.acme.test",
            port=self.port,
            target=target,
            addresses=tuple(addresses),
            headers=(
                ("content-type", "application/json; charset=utf-8"),
                ("webhook-id", "evt_X"),
            ),
            body=b'{"id":"evt_X"}',
            deadline=time.monotonic() + timeout_s,
        )
        return PinnedTransport().post(req)

    def test_posts_the_exact_body_with_host_and_identity(self):
        r = self.post("/ok?team=7")
        self.assertEqual((r.status, r.error, r.connected_ip), (200, None, "127.0.0.1"))
        path, headers, body = _Handler.seen[0]
        self.assertEqual((path, body), ("/ok?team=7", b'{"id":"evt_X"}'))
        low = {k.lower(): v for k, v in headers.items()}
        self.assertEqual(low["host"], f"hooks.acme.test:{self.port}")
        self.assertEqual(low["accept-encoding"], "identity")
        self.assertEqual(low["content-length"], "14")
        self.assertNotIn("cookie", low)

    def test_redirect_is_not_followed(self):
        r = self.post("/redirect")
        self.assertEqual(r.status, 302)
        self.assertEqual(len(_Handler.seen), 1)

    def test_reads_at_most_4_kb_raw(self):
        r = self.post("/big")
        self.assertEqual((r.status, len(r.body)), (200, 4096))

    def test_trickling_receiver_is_cut_at_the_deadline(self):
        t0 = time.monotonic()
        r = self.post("/trickle", timeout_s=1.5)
        took = time.monotonic() - t0
        # The status line arrived, but the body trickles: the read stops at the deadline, never at 20 x 0.4 s,
        # and the attempt is a timeout.
        self.assertLess(took, 3.0)
        self.assertEqual((r.error, r.status), ("timeout", None))

    def test_a_lost_lease_closes_the_socket_at_once(self):
        from webhooks.core.ports import AbortHandle

        abort = AbortHandle()
        threading.Timer(0.4, abort.abort).start()
        _Handler.seen.clear()
        req = PinnedPost(
            scheme="http",
            host="hooks.acme.test",
            port=self.port,
            target="/slowheaders",
            addresses=("127.0.0.1",),
            headers=(),
            body=b"{}",
            deadline=time.monotonic() + 10,
            abort=abort,
        )
        t0 = time.monotonic()
        r = PinnedTransport().post(req)
        self.assertEqual(r.error, "aborted")
        self.assertLess(time.monotonic() - t0, 2.0)

    def test_slow_headers_time_out(self):
        t0 = time.monotonic()
        r = self.post("/slowheaders", timeout_s=1.0)
        self.assertEqual(r.error, "timeout")
        self.assertLess(time.monotonic() - t0, 2.5)

    def test_a_refused_address_is_a_connect_error(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        dead_port = s.getsockname()[1]
        s.close()
        req = PinnedPost(
            scheme="http",
            host="hooks.acme.test",
            port=dead_port,
            target="/",
            addresses=("127.0.0.1",),
            headers=(),
            body=b"{}",
            deadline=time.monotonic() + 3,
        )
        r = PinnedTransport().post(req)
        self.assertEqual(r.error, "connect_error")

    def test_a_refused_first_address_falls_to_the_second(self):
        # Nothing listens on [::1] at the server's port, so the first vetted address is refused.
        r = self.post("/ok", addresses=("::1", "127.0.0.1"))
        self.assertEqual((r.status, r.connected_ip), (200, "127.0.0.1"))

    def test_at_most_two_addresses_are_tried(self):
        r = self.post("/ok", addresses=("::1", "::1", "127.0.0.1"))
        self.assertEqual(r.error, "connect_error")
        self.assertEqual(_Handler.seen, [])

    def test_resolver_uses_the_absolute_name_and_its_budget(self):
        from unittest import mock

        seen = []

        def fake(name, *_a, **_k):
            seen.append(name)
            if name.startswith("slow"):
                time.sleep(1.0)
            if name.startswith("bad"):
                raise socket.gaierror(8, "nodename nor servname provided")
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 0)),
                (
                    socket.AF_INET6,
                    socket.SOCK_STREAM,
                    6,
                    "",
                    ("2606:2800:21f:cb07:6820:80da:af6b:8b2c", 0, 0, 0),
                ),
            ]

        with mock.patch.object(ssrf_post.socket, "getaddrinfo", fake):
            self.assertEqual(
                ssrf_post.resolve_all("hooks.acme.ai", 2.0),
                ["93.184.215.14", "2606:2800:21f:cb07:6820:80da:af6b:8b2c"],
            )
            self.assertEqual(seen[-1], "hooks.acme.ai.")
            t0 = time.monotonic()
            with self.assertRaises(ssrf_post.ResolveFailed):
                ssrf_post.resolve_all("slow.acme.ai", 0.2)
            self.assertLess(time.monotonic() - t0, 0.8)
            with self.assertRaises(ssrf_post.ResolveFailed):
                ssrf_post.resolve_all("bad.acme.ai", 1.0)


class HostedAddressGuardTests(unittest.TestCase):
    def test_public_passes_and_everything_else_fails(self):
        from webhooks.transport import hosted_address_guard

        self.assertTrue(hosted_address_guard("93.184.215.14"))
        self.assertTrue(hosted_address_guard("2606:2800:21f:cb07:6820:80da:af6b:8b2c"))
        for addr in (
            "10.0.0.1",
            "127.0.0.1",
            "169.254.169.254",
            "100.64.0.1",
            "::1",
            "64:ff9b::a9fe:a9fe",
            "2002:a00:1::",
            "192.0.2.1",
            "fd00:ec2::254",
        ):
            with self.subTest(addr=addr):
                self.assertFalse(hosted_address_guard(addr))


class ClassifyIpUnwrapTests(unittest.TestCase):
    """ssrf_guard.classify_ip now unwraps like the core (WH-0 finding 5: the first five were PRIVATE on dev; the last
    two are the EE review's of 10-22, conformance 1.2.0)."""

    def test_s6_cases_are_never(self):
        for addr in (
            "64:ff9b::a9fe:a9fe",
            "64:ff9b:1:a9fe:a9:fe00::",
            "2002:7f00:1::",
            "::127.0.0.1",
            "2001:0:4136:e378:8000:63bf:80ff:fffe",
            # Conformance 1.2.0, EE review 10-22: IPv4-translated, and the compatible form spelled in hex.
            "::ffff:0:7f00:1",
            "::7f00:1",
        ):
            with self.subTest(addr=addr):
                self.assertIs(ssrf_guard.classify_ip(addr), ssrf_guard.IPClass.NEVER)

    def test_agrees_with_the_core_on_unwrapped_classes(self):
        # The core is stricter in one place: it refuses IPv4-mapped forms of public addresses (outside 2000::/3),
        # where the general guard judges them as the IPv4 address. Webhook sends are gated by the core.
        for addr in (
            "::ffff:10.0.0.1",
            "64:ff9b::a00:1",
            "2002:a00:1::",
            "64:ff9b::808:808",
            "93.184.215.14",
            "10.0.0.1",
            "169.254.169.254",
            "100.64.0.1",
            "fd00:ec2::254",
            "::ffff:169.254.169.254",
        ):
            with self.subTest(addr=addr):
                core = classify(addr)
                guard = ssrf_guard.classify_ip(addr).value
                if core == "never":
                    self.assertEqual(guard, "never")
                elif core == "private":
                    self.assertIn(guard, ("private", "never"))


if __name__ == "__main__":
    unittest.main()
