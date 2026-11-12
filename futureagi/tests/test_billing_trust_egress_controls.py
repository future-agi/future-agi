"""No-socket command/policy controls; Linux packet qualification is still owed."""

import errno
import importlib.util
import json
import platform
import subprocess
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

    def test_seccomp_program_refuses_new_user_namespaces(self):
        allow, eperm, enosys = 0x7FFF0000, 0x50000 | errno.EPERM, 0x50000 | errno.ENOSYS
        newuser, fork_flags, vfork_flags = 0x10000000, 0x01200011, 0x4111
        for machine, arch, clone, unshare, setns, sock in (
            ("x86_64", 0xC000003E, 56, 272, 308, 41),
            ("aarch64", 0xC00000B7, 220, 97, 268, 198),
        ):
            program = self.guard.socket_filter(machine)
            with self.subTest(machine=machine):
                self.assertEqual(run_bpf(program, unshare, arch, newuser), eperm)
                self.assertEqual(run_bpf(program, unshare, arch, 0), eperm)
                self.assertEqual(run_bpf(program, setns, arch, 0), eperm)
                self.assertEqual(run_bpf(program, clone, arch, newuser | 17), eperm)
                self.assertEqual(
                    run_bpf(program, clone, arch, 0x7E020000 | newuser), eperm
                )
                self.assertEqual(
                    run_bpf(program, 435, arch, 0), enosys
                )  # clone3: libc falls back to clone
                self.assertEqual(run_bpf(program, clone, arch, fork_flags), allow)
                self.assertEqual(run_bpf(program, clone, arch, vfork_flags), allow)
                self.assertEqual(run_bpf(program, sock, arch, 2), allow)
                self.assertNotEqual(run_bpf(program, sock, arch, 1), allow)
                self.assertEqual(run_bpf(program, 1, arch, 0), allow)

    @unittest.skipUnless(
        sys.platform.startswith("linux")
        and platform.machine() in ("x86_64", "aarch64"),
        "needs Linux seccomp on x86_64 or aarch64",
    )
    def test_the_installed_filter_refuses_new_user_namespaces(self):
        """Install the filter in a child, as restrict_child does, and try each way into a user namespace."""
        guard = str(EXECUTOR / "egress_guard.py")

        def probe(install):
            run = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    NAMESPACE_PROBE,
                    guard,
                    "1" if install else "0",
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertEqual(run.returncode, 0, run.stderr[-2000:])
            return json.loads(run.stdout.strip().splitlines()[-1])

        host = probe(install=False)
        if host["unshare"][0] != 0:
            self.skipTest(
                "this host refuses user namespaces itself, so the filter's refusal cannot be told apart"
            )
        jailed = probe(install=True)
        self.assertEqual(jailed["clone_newuser"], [-1, errno.EPERM])
        self.assertEqual(jailed["clone3"][1], errno.ENOSYS)
        self.assertEqual(jailed["unshare"], [-1, errno.EPERM])
        self.assertEqual(jailed["spawn"], 0)
        self.assertEqual(jailed["fork"], 7)


def run_bpf(program, nr, arch, arg0):
    """Run a classic seccomp program over seccomp_data (nr at 0, arch at 4, args[0] low word at 16)."""
    data = {0: nr, 4: arch, 16: arg0 & 0xFFFFFFFF}
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
            raise AssertionError(f"unknown BPF opcode {code:#x}")
        pc += 1
    raise AssertionError("BPF fell off the end")


# Runs in its own interpreter: installs egress_guard.socket_filter (argv[2] == "1") with
# no_new_privs as restrict_child does, then tries clone(CLONE_NEWUSER), clone3, a spawn,
# a fork and unshare(CLONE_NEWUSER), last because success moves the process. Prints JSON.
NAMESPACE_PROBE = r"""
import ctypes, importlib.util, json, os, platform, subprocess, sys
spec = importlib.util.spec_from_file_location("egress_guard_probe", sys.argv[1])
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
libc = ctypes.CDLL(None, use_errno=True)
libc.syscall.restype = ctypes.c_long
if sys.argv[2] == "1":
    class Filter(ctypes.Structure):
        _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte), ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint32)]
    class Program(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(Filter))]
    rows = guard.socket_filter(platform.machine())
    program = Program(len(rows), (Filter * len(rows))(*(Filter(*row) for row in rows)))
    if libc.prctl(38, ctypes.c_ulong(1), ctypes.c_ulong(0), ctypes.c_ulong(0), ctypes.c_ulong(0)) != 0:
        raise OSError(ctypes.get_errno(), "PR_SET_NO_NEW_PRIVS")
    if libc.prctl(22, ctypes.c_ulong(2), ctypes.byref(program), ctypes.c_ulong(0), ctypes.c_ulong(0)) != 0:
        raise OSError(ctypes.get_errno(), "PR_SET_SECCOMP")
CLONE, CLONE3 = {"x86_64": (56, 435), "aarch64": (220, 435)}[platform.machine()]
NEWUSER = 0x10000000
def call(fn):
    ctypes.set_errno(0)
    result = fn()
    return [result, ctypes.get_errno()]
def clone_newuser():
    pid = libc.syscall(ctypes.c_long(CLONE), ctypes.c_ulong(NEWUSER | 17), ctypes.c_ulong(0), ctypes.c_ulong(0), ctypes.c_ulong(0), ctypes.c_ulong(0))
    if pid == 0:
        os._exit(0)
    if pid > 0:
        os.waitpid(pid, 0)
        return 0
    return -1
out = {"clone_newuser": call(clone_newuser)}
out["clone3"] = call(lambda: libc.syscall(ctypes.c_long(CLONE3), ctypes.c_ulong(0), ctypes.c_ulong(0)))
out["spawn"] = subprocess.run(["/bin/true"]).returncode
pid = os.fork()
if pid == 0:
    os._exit(7)
out["fork"] = os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1])
out["unshare"] = call(lambda: libc.unshare(ctypes.c_int(NEWUSER)))
print(json.dumps(out))
"""


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
