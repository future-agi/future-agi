"""Pure synthetic init/witness controls. No kernel operations or sockets run."""

import importlib.util
import json
import stat
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

EXECUTOR = Path(__file__).resolve().parents[1] / "code-executor"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, EXECUTOR / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def status():
    return {
        "Uid": "65534 65534 65534 65534",
        "Gid": "65534 65534 65534 65534",
        "CapEff": "0",
        "CapPrm": "0",
        "CapInh": "0",
        "CapAmb": "0",
        "NoNewPrivs": "1",
    }


class RuntimeWitnessControls(unittest.TestCase):
    def setUp(self):
        self.guard = load("egress_guard", "egress_guard.py")
        with patch.dict(sys.modules, {"egress_guard": self.guard}):
            self.runtime = load("egress_runtime", "egress_runtime.py")

    def test_control_exception_is_only_the_own_tcp_socket(self):
        rules = self.guard.policy_rules((443,), resolvers=("127.0.0.11",))
        self.assertTrue(self.guard.allows_destination(rules, "127.0.0.1", 8060))
        for address, port, transport in (
            ("127.0.0.1", 8060, "udp"),
            ("127.0.0.2", 8060, "tcp"),
            ("127.0.0.1", 443, "tcp"),
            ("169.254.169.254", 8060, "tcp"),
            ("10.0.0.1", 8060, "tcp"),
        ):
            self.assertFalse(
                self.guard.allows_destination(rules, address, port, transport)
            )
        script = self.guard.nft_script(rules)
        self.assertIn("ct direction reply tcp sport 8060 accept", script)
        self.assertIn(
            "ct direction original meta l4proto tcp ip daddr 127.0.0.1 th dport 8060 accept",
            script,
        )

    def test_unprivileged_fallback_does_not_attempt_setgroups(self):
        with patch.dict(
            sys.modules,
            {"egress_guard": self.guard, "falcon": SimpleNamespace(App=lambda: Mock())},
        ):
            server = load("runtime_server_controls", "server.py")
        with (
            patch.object(server, "prepare_egress"),
            patch.object(server.os, "geteuid", return_value=65534),
            patch.object(server.os, "chown"),
            patch.object(
                server.subprocess, "Popen", side_effect=RuntimeError("synthetic stop")
            ) as run,
        ):
            server._execute_python_fallback("def evaluate(): return 1", {}, 1)
        self.assertNotIn("extra_groups", run.call_args.kwargs)
        self.assertEqual(run.call_args.kwargs["user"], 65534)
        self.assertEqual(run.call_args.kwargs["group"], 65534)
        self.assertTrue(run.call_args.kwargs["close_fds"])

    def test_verified_immutable_policy_keeps_runtime_ready(self):
        self.check_witness()

    def check_witness(self, *, seals=15, change=None, events=(), caps=None):
        runtime = self.runtime
        script = "synthetic reviewed policy"
        witness = {
            "policy": runtime.hashlib.sha256(script.encode()).hexdigest(),
            "namespace": [10, 20],
            "monitor": 18,
        }
        witness.update(change or {})
        poller = Mock()
        poller.poll.return_value = events
        with (
            patch.object(runtime, "process_status", return_value=caps or status()),
            patch.object(runtime, "namespace_identity", return_value=[10, 20]),
            patch.dict(
                runtime.os.environ, {"CODE_EXECUTOR_POLICY_FD": "17"}, clear=True
            ),
            patch.object(runtime.fcntl, "fcntl", return_value=seals),
            patch.object(
                runtime.os, "pread", return_value=json.dumps(witness).encode()
            ),
            patch.object(
                runtime.os, "fstat", return_value=SimpleNamespace(st_mode=stat.S_IFSOCK)
            ),
            patch.object(runtime.select, "poll", return_value=poller),
        ):
            runtime.verify_policy(script)
            poller.register.assert_called_once()
            poller.poll.assert_called_once_with(0)

    def test_missing_or_mutable_witness_is_refused(self):
        for seals in (0, 1, 7, 14):
            with self.subTest(seals=seals), self.assertRaises(RuntimeError):
                self.check_witness(seals=seals)
        with (
            patch.object(self.runtime, "process_status", return_value=status()),
            patch.dict(self.runtime.os.environ, {}, clear=True),
            self.assertRaises(KeyError),
        ):
            self.runtime.verify_policy("synthetic")

    def test_wrong_policy_namespace_or_monitor_is_refused(self):
        for change in (
            {"policy": "other"},
            {"namespace": [10, 21]},
            {"monitor": 0},
            {"monitor": 17},
            {"monitor": "18"},
        ):
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                self.check_witness(change=change)

    def test_mutation_and_monitor_errors_are_sticky_for_all_workers(self):
        for event in (1, 8, 16, 32):  # POLLIN, ERR, HUP, NVAL
            for _ in range(2):
                with self.subTest(event=event), self.assertRaises(RuntimeError):
                    self.check_witness(events=[(18, event)])
        # Readiness must never drain the shared event, including through a helper.
        source = (EXECUTOR / "egress_runtime.py").read_text()
        section = source.split("def assert_monitor_quiet", 1)[1].split(
            "def seal_witness", 1
        )[0]
        self.assertNotIn(".recv(", section)
        self.assertNotIn("os.read(", section)

    def test_retained_capability_fails_witness_verification(self):
        with self.assertRaises(RuntimeError):
            self.check_witness(caps={**status(), "CapPrm": "1000"})

    def test_notification_loss_cannot_be_drained_as_success(self):
        monitor = Mock()
        monitor.recv.side_effect = OSError("synthetic ENOBUFS")
        with self.assertRaises(OSError):
            self.runtime.drain_install_events(monitor)
        monitor.recv.assert_called_once()

    def test_sealed_witness_carries_monitor_and_namespace(self):
        runtime = self.runtime
        with (
            patch.object(
                runtime.os, "memfd_create", create=True, return_value=17
            ) as create,
            patch.object(runtime.os, "MFD_ALLOW_SEALING", 2, create=True),
            patch.object(runtime, "namespace_identity", return_value=[10, 20]),
            patch.object(
                runtime.os, "write", side_effect=lambda fd, data: len(data)
            ) as write,
            patch.object(runtime.fcntl, "fcntl") as seal,
            patch.object(runtime.os, "set_inheritable") as inherit,
        ):
            self.assertEqual(runtime.seal_witness("synthetic", 18), 17)
        create.assert_called_once()
        self.assertEqual(json.loads(write.call_args.args[1])["monitor"], 18)
        seal.assert_called_once_with(17, runtime.F_ADD_SEALS, runtime.SEALS)
        self.assertEqual(inherit.call_args_list[0].args, (17, True))
        self.assertEqual(inherit.call_args_list[1].args, (18, True))

    def test_capability_clear_is_verified_and_failures_propagate(self):
        runtime = self.runtime
        libc = Mock()
        libc.prctl.return_value = 0
        libc.capset.return_value = 0
        with (
            patch.object(runtime.ctypes, "CDLL", return_value=libc),
            patch.object(runtime, "process_status", return_value=status()) as read,
        ):
            runtime.drop_capabilities()
            self.assertEqual(libc.prctl.call_count, 2)
            libc.capset.assert_called_once()
            read.assert_called_once()
            libc.capset.return_value = -1
            with self.assertRaises(OSError):
                runtime.drop_capabilities()


class InitLifecycleControls(unittest.TestCase):
    def setUp(self):
        guard = load("egress_guard", "egress_guard.py")
        with patch.dict(sys.modules, {"egress_guard": guard}):
            runtime = load("egress_runtime", "egress_runtime.py")
        with patch.dict(
            sys.modules, {"egress_guard": guard, "egress_runtime": runtime}
        ):
            self.init = load("executor_init_controls", "executor_init.py")

    def test_readback_rejects_missing_extra_or_changed_rules(self):
        objects = [
            {"table": {"family": "inet", "name": "fa_code_eval", "handle": 1}},
            {
                "chain": {
                    "family": "inet",
                    "name": "output",
                    "table": "fa_code_eval",
                    "handle": 2,
                }
            },
            {
                "rule": {
                    "family": "inet",
                    "table": "fa_code_eval",
                    "chain": "output",
                    "expr": [{"reject": None}],
                    "handle": 3,
                }
            },
        ]
        echo = json.dumps({"nftables": [{"add": row} for row in objects]})
        snapshot = json.dumps({"nftables": objects})
        self.init.verify_readback(echo, snapshot, 1)
        for changed in (
            objects[:-1],
            objects + [objects[-1]],
            objects[:2] + [{"rule": {"expr": [{"accept": None}]}}],
        ):
            with self.subTest(changed=changed), self.assertRaises(RuntimeError):
                self.init.verify_readback(echo, json.dumps({"nftables": changed}), 1)
        with self.assertRaises(RuntimeError):
            self.init.verify_readback('{"nftables":[]}', '{"nftables":[]}', 0)

    def test_init_verifies_drops_and_only_then_execs_unprivileged_server(self):
        self.exercise_init()

    def test_readback_or_capability_drop_failure_never_execs_server(self):
        for failure in ("verify_readback", "drop_capabilities", "verify_policy"):
            with self.subTest(failure=failure):
                self.exercise_init(failure)

    def exercise_init(self, failure=None):
        init = self.init
        order = []
        monitor = Mock()
        monitor.fileno.return_value = 18

        def step(name, result=None):
            def call(*args, **kwargs):
                order.append(name)
                if failure == name:
                    raise RuntimeError("synthetic refusal")
                return result

            return call

        installed = []

        def nft(argv, **kwargs):
            # The policy reaches nft as a private file; read it while nft would.
            if not installed:
                installed.append(Path(argv[-1]).read_text())
            return step("nft", SimpleNamespace(stdout="synthetic"))(argv, **kwargs)

        caps = dict.fromkeys(("CapEff", "CapPrm", "CapInh", "CapAmb", "CapBnd"), "1000")
        with (
            patch.object(init, "process_status", return_value=caps),
            patch.object(init.os, "getuid", return_value=65534),
            patch.object(init.os, "geteuid", return_value=65534),
            patch.object(init.os, "getgid", return_value=65534),
            patch.object(init.os, "getgroups", return_value=[]),
            patch.dict(init.os.environ, {}, clear=True),
            patch.object(init, "read_resolvers", return_value=("127.0.0.11",)),
            patch.object(init.shutil, "which", return_value="/synthetic/nft"),
            patch.object(init, "open_monitor", side_effect=step("subscribe", monitor)),
            patch.object(
                init.subprocess,
                "run",
                side_effect=nft,
            ) as run,
            patch.object(init, "drain_install_events", side_effect=step("drain")),
            patch.object(init, "verify_readback", side_effect=step("verify_readback")),
            patch.object(init, "assert_monitor_quiet", side_effect=step("quiet")),
            patch.object(init, "seal_witness", side_effect=step("seal", 17)),
            patch.object(
                init, "drop_capabilities", side_effect=step("drop_capabilities")
            ),
            patch.object(init, "verify_policy", side_effect=step("verify_policy")),
            patch.object(init.os, "execv", side_effect=step("exec")) as execute,
        ):
            if failure:
                with self.assertRaises(RuntimeError):
                    init.initialize()
                execute.assert_not_called()
            else:
                init.initialize()
                self.assertEqual(
                    order,
                    [
                        "subscribe",
                        "nft",
                        "drain",
                        "nft",
                        "verify_readback",
                        "quiet",
                        "seal",
                        "drop_capabilities",
                        "verify_policy",
                        "exec",
                    ],
                )
                self.assertEqual(
                    run.call_args_list[0].args[0][:5],
                    ["/synthetic/nft", "-j", "-e", "-n", "-f"],
                )
                self.assertNotIn("input", run.call_args_list[0].kwargs)
                self.assertIn("ct original proto-dst 53", installed[0])
                execute.assert_called_once()
                monitor.detach.assert_called_once()


if __name__ == "__main__":

    def deny_sockets(event, args):
        if event.startswith("socket."):
            raise PermissionError("socket-free control boundary")

    sys.addaudithook(deny_sockets)
    try:
        sys.audit("socket.connect", None, ("synthetic", 443))
    except PermissionError:
        pass
    else:
        raise AssertionError("socket guard did not refuse")
    unittest.main()
