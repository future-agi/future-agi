"""Socket-free S1 DNS/readiness controls; packet-level qualification is owed."""

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, mock_open, patch

EXECUTOR = Path(__file__).resolve().parents[1] / "code-executor"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ResolverControls(unittest.TestCase):
    def setUp(self):
        self.guard = load("dns_guard_controls", EXECUTOR / "egress_guard.py")

    def test_resolver_parser_accepts_only_literal_configured_addresses(self):
        read = getattr(self.guard, "read_resolvers", None)
        self.assertIsNotNone(read, "no resolv.conf integration")
        text = (
            "search synthetic.invalid\nnameserver 127.0.0.11 # local DNS\n"
            "nameserver fd00::53\nnameserver 127.0.0.11\n"
        )
        with patch("builtins.open", mock_open(read_data=text)) as opened:
            self.assertEqual(read(), ("127.0.0.11", "fd00::53"))
            opened.assert_called_once_with("/etc/resolv.conf", encoding="utf-8")
        for text in (
            "search synthetic.invalid\n",
            "nameserver not-an-address\n",
            "nameserver 0.0.0.0\n",
            "nameserver ff02::1\n",
            "nameserver fe80::1%interface\n",
            "nameserver 127.0.0.11 extra-token\n",
        ):
            with (
                self.subTest(text=text),
                patch("builtins.open", mock_open(read_data=text)),
                self.assertRaises(ValueError),
            ):
                read()

    def test_dns_exception_is_only_for_configured_resolver_port_53(self):
        rules = self.guard.policy_rules((53, 443), resolvers=("127.0.0.11", "fd00::53"))
        for address, port, permitted in (
            ("127.0.0.11", 53, True),
            ("fd00::53", 53, True),
            ("127.0.0.11", 443, False),
            ("fd00::53", 443, False),
            ("127.0.0.12", 53, False),
            ("10.1.2.3", 53, False),
            ("1.1.1.1", 53, False),
            ("1.1.1.1", 443, True),
            ("169.254.169.254", 443, False),
        ):
            for mode in ("nsjail", "fallback"):
                with self.subTest(address=address, port=port, mode=mode):
                    self.assertEqual(
                        self.guard.allows_destination(rules, address, port), permitted
                    )

    def test_internal_answer_and_rebinding_still_fail_at_connect(self):
        rules = self.guard.policy_rules((443,), resolvers=("127.0.0.11",))
        # Synthetic answers only: never resolve a real hostname or open a socket.
        answers = {
            "internal.synthetic.invalid": ["10.2.3.4"],
            "rebind.synthetic.invalid": ["1.1.1.1", "10.2.3.4"],
        }
        for mode in ("nsjail", "fallback"):
            with self.subTest(mode=mode):
                self.assertFalse(
                    self.guard.allows_destination(
                        rules, answers["internal.synthetic.invalid"][0], 443
                    )
                )
                self.assertEqual(
                    [
                        self.guard.allows_destination(rules, answer, 443)
                        for answer in answers["rebind.synthetic.invalid"]
                    ],
                    [True, False],
                )

    def test_generated_dns_exception_accounts_for_local_resolver_dnat(self):
        rules = self.guard.policy_rules((443,), resolvers=("127.0.0.11",))
        text = self.guard.nft_script(rules)
        # Docker's embedded resolver DNATs port 53 before filter/output. Match
        # the conntrack ORIGINAL destination, not an unrestricted local port.
        self.assertIn("ct original ip daddr { 127.0.0.11 }", text)
        self.assertIn("ct original proto-dst 53", text)
        self.assertIn("meta l4proto { tcp, udp }", text)
        self.assertLess(
            text.index("ct original ip daddr"), text.index("fib daddr type local")
        )
        self.assertNotIn("udp dport { 53,", text)

    def test_install_uses_resolv_conf_not_an_unrestricted_dns_exception(self):
        with (
            patch.object(self.guard.sys, "platform", "linux"),
            patch.object(self.guard.os, "geteuid", return_value=0),
            patch.object(self.guard.shutil, "which", return_value="/synthetic/nft"),
            patch.dict(self.guard.os.environ, {}, clear=True),
            patch("builtins.open", mock_open(read_data="nameserver 127.0.0.11\n")),
            patch.object(self.guard.subprocess, "run") as run,
        ):
            self.guard.prepare_egress((443,))
            self.assertIn("127.0.0.11", run.call_args.kwargs["input"])
            self.assertIn("ct original proto-dst 53", run.call_args.kwargs["input"])


class ReadinessControls(unittest.TestCase):
    def setUp(self):
        guard = load("egress_guard", EXECUTOR / "egress_guard.py")
        falcon = SimpleNamespace(App=lambda: Mock(), HTTP_503="503 Service Unavailable")
        with patch.dict(sys.modules, {"egress_guard": guard, "falcon": falcon}):
            self.server = load("dns_server_controls", EXECUTOR / "server.py")

    def test_missing_policy_fails_health_with_typed_refusal_and_log(self):
        response = SimpleNamespace(status="200 OK", media=None)
        with (
            patch.object(
                self.server,
                "prepare_egress",
                side_effect=RuntimeError("synthetic policy absent"),
            ) as prepare,
            self.assertLogs(level="ERROR") as logs,
        ):
            self.server.HealthResource().on_get(None, response)
        prepare.assert_called_once()
        self.assertEqual(response.status, "503 Service Unavailable")
        self.assertEqual(response.media["error_code"], "EGRESS_POLICY_UNAVAILABLE")
        self.assertNotEqual(response.media["status"], "ok")
        self.assertTrue(
            any("EGRESS_POLICY_UNAVAILABLE" in line for line in logs.output)
        )

    def test_verified_policy_keeps_health_ok(self):
        response = SimpleNamespace(status="200 OK", media=None)
        with patch.object(self.server, "prepare_egress") as prepare:
            self.server.HealthResource().on_get(None, response)
        prepare.assert_called_once()
        self.assertEqual(response.status, "200 OK")
        self.assertEqual(response.media["status"], "ok")
        self.assertEqual(response.media["egress_ports"], [80, 443])

    def test_eval_policy_failure_is_logged_without_exception_contents(self):
        with (
            patch.object(
                self.server,
                "prepare_egress",
                side_effect=RuntimeError("synthetic details must not be emitted"),
            ),
            self.assertLogs(level="ERROR") as logs,
        ):
            result = self.server._prepare_network()
        self.assertEqual(result["error_code"], "EGRESS_POLICY_UNAVAILABLE")
        self.assertTrue(
            any("EGRESS_POLICY_UNAVAILABLE" in line for line in logs.output)
        )
        self.assertNotIn("synthetic details", "\n".join(logs.output))


def deny_sockets(event, args):
    if event.startswith("socket."):
        raise PermissionError("socket-free control boundary")


if __name__ == "__main__":
    sys.addaudithook(deny_sockets)
    try:
        sys.audit("socket.connect", None, ("synthetic", 443))
    except PermissionError:
        pass
    else:
        raise AssertionError("socket guard did not refuse")
    unittest.main()
