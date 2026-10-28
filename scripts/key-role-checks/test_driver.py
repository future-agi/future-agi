"""Small stdlib-only checks; no Django, services, or network calls."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import socket
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_results import compare, load, main  # noqa: E402
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

    def test_runtime_errors_are_not_policy_assertion_red(self):
        import xml.etree.ElementTree as ET

        families = (
            ("test_key_role_scope", "test_bootstrap_role_and_authenticated_identity"),
            (
                "test_key_management_scope",
                "test_system_key_changes_require_current_org_admin",
            ),
            ("test_key_tool_scope", "test_key_tools_use_current_org_membership"),
        )
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory)
            for label in (
                "red",
                "green",
                "base_accounts",
                "head_accounts",
                "dev_accounts",
                "replay_accounts",
            ):
                suite = ET.Element("testsuite")
                for module, name in families:
                    case = ET.SubElement(
                        suite,
                        "testcase",
                        classname=f"accounts.tests.{module}",
                        name=name,
                    )
                    if label == "red":
                        failure = ET.SubElement(
                            case, "failure", message="RuntimeError: unavailable service"
                        )
                        failure.text = "E   RuntimeError: unavailable service"
                ET.ElementTree(suite).write(raw / f"{label}.xml")
                (raw / f"{label}.exit").write_text("1" if label == "red" else "0")
            with redirect_stdout(io.StringIO()):
                code = main(raw, raw / "results")
            result = json.loads((raw / "results/comparison.json").read_text())
            self.assertEqual(code, 1)
            self.assertFalse(result["red_assertions_proven"])
            self.assertEqual(result["status"], "BLOCKED")

    def test_only_matching_policy_assertions_count(self):
        import xml.etree.ElementTree as ET

        from check_results import POLICY_ASSERTIONS, policy_assertion_failures

        family = next(iter(POLICY_ASSERTIONS))
        module, name = family.split("::")
        source, _ = POLICY_ASSERTIONS[family]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.xml"
            for message, statement, expected in (
                ("AssertionError: assert 'system' == 'user'", source, True),
                ("RuntimeError: unavailable service", source, False),
                ("AssertionError: assert 1 == 2", source, False),
                (
                    "AssertionError: assert 'system' == 'user'",
                    "assert unrelated == expected_type",
                    False,
                ),
            ):
                suite = ET.Element("testsuite")
                case = ET.SubElement(
                    suite, "testcase", classname=module, name=f"{name}[False-1-0]"
                )
                failure = ET.SubElement(case, "failure", message=message)
                failure.text = f"file.py:45: in {name}\n    {statement}\nE   {message}"
                ET.ElementTree(suite).write(path)
                self.assertEqual(
                    bool(policy_assertion_failures(path)[family]), expected
                )

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
