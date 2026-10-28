"""Small stdlib-only checks; no Django, services, or network calls."""

from pathlib import Path
import socket
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_results import compare, load  # noqa: E402
from socket_guard import _audit, _loopback  # noqa: E402


class SocketBoundaryTests(unittest.TestCase):
    def test_only_explicit_loopback_addresses_are_allowed(self):
        for host in ("localhost", "127.0.0.1", "::1", "::ffff:127.0.0.1"):
            self.assertTrue(_loopback(host))
        for host in ("198.51.100.7", "192.168.1.1", "example.test", "0.0.0.0", None):
            self.assertFalse(_loopback(host))

    def test_remote_dns_is_refused(self):
        for event in (
            "socket.getaddrinfo",
            "socket.gethostbyname",
            "socket.gethostbyaddr",
        ):
            with self.assertRaises(PermissionError):
                _audit(event, ("example.test", 443, 0, 0, 0))

    def test_remote_connect_and_datagram_are_refused(self):
        fake_socket = SimpleNamespace(family=socket.AF_INET)
        with self.assertRaises(PermissionError):
            _audit("socket.connect", (fake_socket, ("198.51.100.7", 443)))
        with self.assertRaises(PermissionError):
            _audit("socket.sendto", (fake_socket, ("198.51.100.7", 53)))

    def test_loopback_and_unix_socket_events_are_allowed(self):
        _audit(
            "socket.connect",
            (SimpleNamespace(family=socket.AF_INET), ("127.0.0.1", 15432)),
        )
        _audit(
            "socket.connect", (SimpleNamespace(family=socket.AF_UNIX), "/tmp/test.sock")
        )


class ComparisonTests(unittest.TestCase):
    def test_regressions_and_new_skips_are_not_shared_failures(self):
        pair = compare(
            {"a": "passed", "b": "passed", "c": "failed"},
            {"a": "failed", "b": "skipped", "c": "failed", "d": "error"},
        )
        self.assertEqual(pair["regressions"], ["a", "d"])
        self.assertEqual(pair["new_skips"], ["b"])
        self.assertEqual(pair["shared_failures"], ["c"])

    def test_missing_baseline_case_is_reported(self):
        self.assertEqual(compare({"a": "passed"}, {})["missing"], ["a"])

    def test_loader_keeps_assertion_failure_distinct_from_setup_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.xml"
            path.write_text(
                '<testsuites><testsuite><testcase classname="m" name="a"><failure/></testcase>'
                '<testcase classname="m" name="b"><error/></testcase></testsuite></testsuites>'
            )
            self.assertEqual(load(path), {"m::a": "failed", "m::b": "error"})

    def test_empty_or_duplicate_results_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.xml"
            for xml in (
                "<testsuites/>",
                '<testsuite><testcase name="a"/><testcase name="a"/></testsuite>',
            ):
                path.write_text(xml)
                with self.assertRaises(ValueError):
                    load(path)


if __name__ == "__main__":
    unittest.main()
