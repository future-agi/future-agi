"""No-socket command/policy controls; Linux packet qualification is still owed."""

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


def executor():
    modules = {"falcon": SimpleNamespace(App=lambda: Mock())}
    guard_path = EXECUTOR / "egress_guard.py"
    if guard_path.exists():
        modules["egress_guard"] = load("egress_guard", guard_path)
    with patch.dict(sys.modules, modules):
        return load("billing_trust_executor", EXECUTOR / "server.py")


class EgressLaunchControls(unittest.TestCase):
    def setUp(self):
        self.server = executor()
        self.server.NODE_PATH = "/synthetic/node"
        self.server.NSJAIL_PATH = "/synthetic/nsjail"

    def test_ports_identical_in_all_modes_even_without_landlock(self):
        for nsjail in (True, False):
            for landlock in (True, False):
                with self.subTest(nsjail=nsjail, landlock=landlock):
                    self.server.NSJAIL_AVAILABLE = nsjail
                    self.server.LANDLOCK_TCP_RULES = landlock
                    self.assertEqual(self.server._egress_ports(), [80, 443])

    def test_nsjail_python_and_javascript_install_and_enter_guard(self):
        self.server.NSJAIL_AVAILABLE = True
        for method in (
            self.server._execute_python_nsjail,
            self.server._execute_javascript,
        ):
            with (
                self.subTest(method=method.__name__),
                patch.object(self.server, "prepare_egress", create=True) as prepare,
                patch.object(self.server.os, "makedirs"),
                patch.object(self.server.os, "remove"),
                patch("builtins.open", mock_open()),
                patch.object(
                    self.server.subprocess,
                    "run",
                    return_value=SimpleNamespace(
                        stdout='{"status":"success"}', stderr="", returncode=0
                    ),
                ) as run,
            ):
                method("def evaluate(): return 1", {}, 1)
                prepare.assert_called_once()
                argv = run.call_args.args[0]
                self.assertIn(str(EXECUTOR / "egress_guard.py"), argv)
                self.assertIn("--user", argv)
                self.assertEqual(argv[argv.index("--user") + 1], "65534")
                self.assertLess(argv.index("--user"), argv.index("--"))

    def test_fallback_enters_same_guard_and_drops_identity(self):
        with (
            patch.object(self.server, "prepare_egress", create=True) as prepare,
            patch.object(self.server.os, "chown"),
            patch.object(
                self.server.subprocess,
                "Popen",
                side_effect=RuntimeError("synthetic stop before spawn"),
            ) as popen,
        ):
            self.server._execute_python_fallback("def evaluate(): return 1", {}, 1)
            prepare.assert_called_once()
            argv = popen.call_args.args[0]
            self.assertIn(str(EXECUTOR / "egress_guard.py"), argv)
            self.assertEqual(popen.call_args.kwargs["user"], 65534)
            self.assertEqual(popen.call_args.kwargs["group"], 65534)
            self.assertEqual(popen.call_args.kwargs["extra_groups"], [])

    def test_unavailable_policy_refuses_before_any_eval_process(self):
        for mode in ("nsjail-python", "nsjail-js", "fallback"):
            with (
                self.subTest(mode=mode),
                patch.object(
                    self.server,
                    "prepare_egress",
                    create=True,
                    side_effect=RuntimeError("synthetic unavailable"),
                ),
                patch.object(self.server.os, "makedirs"),
                patch.object(self.server.os, "remove"),
                patch("builtins.open", mock_open()),
                patch.object(self.server.subprocess, "run") as run,
                patch.object(self.server.subprocess, "Popen") as popen,
            ):
                self.server.NSJAIL_AVAILABLE = mode != "fallback"
                if mode == "nsjail-js":
                    result = self.server._execute_javascript(
                        "function evaluate() { return 1; }", {}, 1
                    )
                elif mode == "nsjail-python":
                    result = self.server._execute_python_nsjail(
                        "def evaluate(): return 1", {}, 1
                    )
                else:
                    result = self.server._execute_python_fallback(
                        "def evaluate(): return 1", {}, 1
                    )
                self.assertEqual(result.get("error_code"), "EGRESS_POLICY_UNAVAILABLE")
                run.assert_not_called()
                popen.assert_not_called()


class EgressPolicyControls(unittest.TestCase):
    def test_destination_and_port_matrix_used_by_both_launch_modes(self):
        path = EXECUTOR / "egress_guard.py"
        self.assertTrue(path.exists(), "base executor has no destination policy")
        guard = load("egress_guard_controls", path)
        rules = guard.policy_rules((443,), ("11.42.0.0/16", "2001:4860:1234::/48"))
        self.assertTrue(
            guard.allows_destination(guard.policy_rules((443,)), "11.42.1.2", 443)
        )
        # Evaluate the generated structured rules, not an independent allowlist.
        for address, port, allowed in (
            ("169.254.169.254", 443, False),
            ("10.1.2.3", 443, False),
            ("172.17.1.2", 443, False),
            ("192.168.1.2", 443, False),
            ("127.0.0.1", 443, False),
            ("::1", 443, False),
            ("fc00::1", 443, False),
            ("fe80::1", 443, False),
            ("::ffff:10.1.2.3", 443, False),
            ("64:ff9b::a01:203", 443, False),
            ("11.42.1.2", 443, False),
            ("2001:4860:1234::1", 443, False),
            ("1.1.1.1", 80, False),
            ("1.1.1.1", 443, True),
            ("2606:4700:4700::1111", 443, True),
        ):
            for mode in ("nsjail", "fallback"):
                with self.subTest(address=address, port=port, mode=mode):
                    self.assertEqual(
                        guard.allows_destination(rules, address, port), allowed
                    )
        text = guard.nft_script(rules)
        self.assertIn("meta skuid 65534", text)
        self.assertIn("tcp dport { 443 }", text)
        self.assertIn("fib daddr type local", text)
        self.assertLess(text.index("169.254.0.0/16"), text.index("tcp dport"))
        self.assertIn("reject", text)


class KernelPolicyControls(unittest.TestCase):
    def setUp(self):
        self.guard = load("egress_kernel_controls", EXECUTOR / "egress_guard.py")

    def test_prepare_applies_exact_policy_and_propagates_installer_failure(self):
        with (
            patch.object(self.guard.sys, "platform", "linux"),
            patch.object(self.guard.os, "geteuid", return_value=0),
            patch.object(self.guard.shutil, "which", return_value="/synthetic/nft"),
            patch.dict(
                self.guard.os.environ, {"CODE_EXECUTOR_INTERNAL_CIDRS": "11.42.0.0/16"}
            ),
            patch("builtins.open", mock_open(read_data="nameserver 127.0.0.11\n")),
            patch.object(self.guard.subprocess, "run") as run,
        ):
            self.guard.prepare_egress((443,))
            self.assertEqual(run.call_args.args[0], ["/synthetic/nft", "-f", "-"])
            self.assertEqual(
                run.call_args.kwargs["input"],
                self.guard.nft_script(
                    self.guard.policy_rules(
                        (443,), ("11.42.0.0/16",), resolvers=("127.0.0.11",)
                    )
                ),
            )
            self.assertTrue(run.call_args.kwargs["check"])
            run.side_effect = RuntimeError("synthetic kernel refusal")
            with self.assertRaises(RuntimeError):
                self.guard.prepare_egress((443,))

    def test_invalid_or_overlapping_deployment_ranges(self):
        rules = self.guard.policy_rules((443,), ("10.0.0.0/8", "10.1.0.0/16"))
        self.assertEqual(sum(str(n) == "10.0.0.0/8" for n in rules["networks"]), 1)
        self.assertNotIn("10.1.0.0/16", self.guard.nft_script(rules))
        with self.assertRaises(ValueError):
            self.guard.policy_rules((443,), ("invalid; accept",))
        for ports in ((), (0,), (65536,), (True,)):
            with self.assertRaises(ValueError):
                self.guard.policy_rules(ports)

    def test_unprivileged_parent_and_wrong_child_identity_refuse(self):
        with (
            patch.object(self.guard.sys, "platform", "linux"),
            patch.object(self.guard.os, "geteuid", return_value=1000),
            patch.object(self.guard.subprocess, "run") as run,
        ):
            with self.assertRaises(RuntimeError):
                self.guard.prepare_egress((443,))
            run.assert_not_called()
        with (
            patch.object(self.guard.sys, "platform", "linux"),
            patch.object(self.guard.os, "getuid", return_value=0),
            patch.object(self.guard.ctypes, "CDLL") as libc,
        ):
            with self.assertRaises(RuntimeError):
                self.guard.restrict_child()
            libc.assert_not_called()

    def test_seccomp_program_blocks_non_ip_alternate_abis_and_socket_apis(self):
        def evaluate(program, nr, arch, domain):
            data = {0: nr, 4: arch, 16: domain}
            pc, value = 0, 0
            while pc < len(program):
                code, yes, no, operand = program[pc]
                if code == 0x20:
                    value = data[operand]
                elif code == 0x15:
                    pc += yes if value == operand else no
                elif code == 0x45:
                    pc += yes if value & operand else no
                elif code == 0x06:
                    return operand
                else:
                    self.fail("unknown BPF opcode")
                pc += 1
            self.fail("BPF fell off end")

        allow = 0x7FFF0000
        for machine, arch, sock, pair in (
            ("x86_64", 0xC000003E, 41, 53),
            ("aarch64", 0xC00000B7, 198, 199),
        ):
            program = self.guard.socket_filter(machine)
            for domain in (1, 16, 17):  # Unix, netlink, packet
                self.assertNotEqual(evaluate(program, sock, arch, domain), allow)
            for domain in (2, 10):
                self.assertEqual(evaluate(program, sock, arch, domain), allow)
            for nr in (pair, 425, sock | 0x40000000):
                self.assertNotEqual(evaluate(program, nr, arch, 2), allow)
            self.assertNotEqual(evaluate(program, sock, 0x40000003, 2), allow)
            self.assertEqual(evaluate(program, 1, arch, 0), allow)


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
