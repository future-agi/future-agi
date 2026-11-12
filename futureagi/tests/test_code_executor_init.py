"""The code executor starts: init installs its egress policy from a private file, never from stdin,
and a run whose jail cannot start says why.

Debian 12's nft (1.0.6) with -j first tries its JSON parser on -f input. Reading stdin that way consumes
the script, the native-syntax fallback then reads nothing, and nft exits 0 with nothing installed, so
init's readback finds no table and every code evaluation fails.
"""

import importlib.util
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

EXECUTOR = Path(__file__).resolve().parents[1] / "code-executor"


def load_init():
    stubs = {
        "egress_guard": SimpleNamespace(
            EVAL_UID=65534,
            nft_script=lambda rules: "",
            policy_rules=lambda *args, **kwargs: [],
            read_resolvers=lambda: (),
        ),
        "egress_runtime": SimpleNamespace(
            NET_ADMIN=1 << 12,
            assert_monitor_quiet=lambda fd: None,
            drain_install_events=lambda monitor: None,
            drop_capabilities=lambda: None,
            open_monitor=lambda: None,
            process_status=lambda: {},
            seal_witness=lambda script, fd: 3,
            verify_policy=lambda script: None,
        ),
    }
    spec = importlib.util.spec_from_file_location("executor_init_under_test", EXECUTOR / "executor_init.py")
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, stubs):
        spec.loader.exec_module(module)
    return module


class PolicyInstallTests(unittest.TestCase):
    def setUp(self):
        self.init = load_init()
        self.directory = tempfile.TemporaryDirectory(prefix="executor-init-")
        self.addCleanup(self.directory.cleanup)
        self.init.POLICY_DIR = self.directory.name
        self.script = "add table inet fa_code_eval\nadd chain inet fa_code_eval output\n"

    def test_policy_is_installed_from_a_private_file_not_stdin(self):
        seen = {}

        def fake_run(argv, **kwargs):
            seen["argv"] = list(argv)
            seen["kwargs"] = kwargs
            seen["content"] = Path(argv[-1]).read_text()
            seen["mode"] = stat.S_IMODE(os.stat(argv[-1]).st_mode)
            return SimpleNamespace(stdout='{"nftables": []}', stderr="", returncode=0)

        with mock.patch.object(self.init.subprocess, "run", side_effect=fake_run):
            result = self.init.install_policy("/usr/sbin/nft", self.script)

        self.assertEqual(seen["argv"][:5], ["/usr/sbin/nft", "-j", "-e", "-n", "-f"])
        self.assertNotEqual(seen["argv"][-1], "-")
        self.assertNotIn("input", seen["kwargs"])
        self.assertTrue(seen["kwargs"].get("check"))
        self.assertEqual(seen["content"], self.script)
        self.assertEqual(seen["mode"], 0o600)
        self.assertFalse(Path(seen["argv"][-1]).exists())
        self.assertEqual(result.stdout, '{"nftables": []}')

    def test_the_policy_file_is_removed_when_nft_fails(self):
        seen = {}

        def failing(argv, **kwargs):
            seen["path"] = argv[-1]
            raise self.init.subprocess.CalledProcessError(1, argv)

        with mock.patch.object(self.init.subprocess, "run", side_effect=failing):
            with self.assertRaises(self.init.subprocess.CalledProcessError):
                self.init.install_policy("/usr/sbin/nft", self.script)
        self.assertFalse(Path(seen["path"]).exists())
        self.assertEqual(os.listdir(self.directory.name), [])


def load_server():
    modules = {"falcon": SimpleNamespace(App=lambda: mock.Mock())}
    guard = EXECUTOR / "egress_guard.py"
    if guard.exists():
        spec = importlib.util.spec_from_file_location("egress_guard", guard)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules["egress_guard"] = module
    spec = importlib.util.spec_from_file_location("executor_server_under_test", EXECUTOR / "server.py")
    server = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, modules):
        spec.loader.exec_module(server)
    return server


class SandboxFailureMessageTests(unittest.TestCase):
    NSJAIL_STDERR = "[F][2026-11-11T08:40:36+0000][1] runChild():486 Launching child process failed"

    def test_a_jail_that_cannot_start_names_the_cause(self):
        server = load_server()
        result = server._no_output_error("Exit code", 255, self.NSJAIL_STDERR)
        self.assertEqual(result["status"], "error")
        self.assertIn("could not start a run on this host", result["data"])
        self.assertIn("INSTALLATION.md", result["data"])
        self.assertNotIn("Launching child process failed", result["data"])

    def test_other_empty_runs_keep_their_exit_code_and_stderr(self):
        server = load_server()
        result = server._no_output_error("Exit code", 1, "Traceback: boom")
        self.assertEqual(result["data"], "No output. Exit code: 1. Traceback: boom")


@unittest.skipUnless(os.environ.get("CODE_EXECUTOR_TEST_IMAGE") and shutil.which("docker"),
                     "set CODE_EXECUTOR_TEST_IMAGE to a built futureagi/code-executor image (needs Docker)")
class ImageInitTests(unittest.TestCase):
    """This checkout's init in the image, under the Compose service's options, with the image's own nft."""

    OPTIONS = (
        "--user", "65534:65534", "--cap-drop", "ALL", "--cap-add", "NET_ADMIN", "--read-only",
        "--security-opt", "seccomp=unconfined", "--security-opt", "apparmor=unconfined",
        "--tmpfs", "/tmp:rw,nosuid,nodev,mode=1777",
        "--tmpfs", "/sandbox/scripts:rw,nosuid,nodev,noexec,uid=65534,gid=65534,mode=0700",
        "-e", "CODE_EXECUTOR_EGRESS_PORTS=80,443", "-e", "CODE_EXECUTOR_INTERNAL_CIDRS=",
    )
    HEALTH = ("python3", "-I", "-S", "-c",
              "import urllib.request as u; u.build_opener(u.ProxyHandler({})).open('http://127.0.0.1:8060/health', timeout=5).close()")

    def test_init_installs_the_policy_and_the_server_answers(self):
        image = os.environ["CODE_EXECUTOR_TEST_IMAGE"]
        name = "executor-init-test-%d" % os.getpid()
        source = str(EXECUTOR / "executor_init.py")
        started = subprocess.run(["docker", "run", "-d", "--name", name, *self.OPTIONS,
                                  "-v", source + ":/sandbox/executor_init.py:ro", image],
                                 capture_output=True, text=True, timeout=120)
        self.addCleanup(subprocess.run, ["docker", "rm", "-f", name], capture_output=True, timeout=60)
        self.assertEqual(started.returncode, 0, started.stderr)
        healthy = False
        for _ in range(30):
            if subprocess.run(["docker", "exec", name, *self.HEALTH], capture_output=True, timeout=30).returncode == 0:
                healthy = True
                break
            state = subprocess.run(["docker", "inspect", "--format", "{{.State.Running}}", name],
                                   capture_output=True, text=True, timeout=30).stdout.strip()
            if state != "true":
                break
            time.sleep(2)
        logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True, timeout=60)
        self.assertTrue(healthy, "executor did not start: " + (logs.stdout + logs.stderr)[-2000:])


if __name__ == "__main__":
    unittest.main()
