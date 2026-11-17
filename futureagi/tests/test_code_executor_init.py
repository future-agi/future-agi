"""The code executor starts: init installs its egress policy from a private file, never from stdin,
and a run whose jail cannot start says why.

Debian 12's nft (1.0.6) with -j first tries its JSON parser on -f input. Reading stdin that way consumes
the script, the native-syntax fallback then reads nothing, and nft exits 0 with nothing installed, so
init's readback finds no table and every code evaluation fails.
"""

import importlib.util
import json
import os
import shutil
import signal
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


class InitSupervisionTests(unittest.TestCase):
    """Init forks gunicorn and stays the container's first process. It passes
    gunicorn's control signals on to it, reaps every other child whatever its exit
    status, and exits with gunicorn's status."""

    GUNICORN = 4321
    SIGNALS = (
        signal.SIGTERM,
        signal.SIGINT,
        signal.SIGHUP,
        signal.SIGQUIT,
        signal.SIGUSR1,
        signal.SIGUSR2,
        signal.SIGTTIN,
        signal.SIGTTOU,
        signal.SIGWINCH,
    )

    def setUp(self):
        self.init = load_init()
        self.handlers = {}

    def supervise(self, waitpid):
        with (
            mock.patch.object(
                self.init.signal, "signal", side_effect=self.handlers.__setitem__
            ),
            mock.patch.object(self.init.os, "waitpid", side_effect=waitpid) as wait,
            mock.patch.object(self.init.os, "kill") as kill,
        ):
            code = self.init.supervise(self.GUNICORN)
        return code, wait, kill

    def test_the_child_execs_gunicorn_and_the_parent_gets_its_pid(self):
        with (
            mock.patch.object(self.init.os, "fork", return_value=0),
            mock.patch.object(self.init.os, "execv") as execv,
        ):
            self.init.start_server()
        execv.assert_called_once_with(
            "/usr/local/bin/gunicorn",
            [
                "gunicorn",
                "--bind",
                "0.0.0.0:8060",
                "--workers",
                "4",
                "--timeout",
                "60",
                "server:app",
            ],
        )
        with (
            mock.patch.object(self.init.os, "fork", return_value=self.GUNICORN),
            mock.patch.object(self.init.os, "execv") as execv,
        ):
            self.assertEqual(self.init.start_server(), self.GUNICORN)
        execv.assert_not_called()

    def test_each_control_signal_is_passed_on_to_gunicorn(self):
        def wait(target, options):
            if options:
                raise ChildProcessError
            for signum, handler in self.handlers.items():
                handler(signum, None)
            return self.GUNICORN, 0

        code, _, kill = self.supervise(wait)
        self.assertEqual(code, 0)
        self.assertEqual(set(self.handlers), set(self.SIGNALS))
        self.assertEqual(
            sorted(call.args for call in kill.call_args_list),
            sorted((self.GUNICORN, signum) for signum in self.SIGNALS),
        )

    def test_a_signal_after_gunicorn_has_exited_raises_nothing(self):
        self.supervise([(self.GUNICORN, 0), ChildProcessError()])
        with mock.patch.object(self.init.os, "kill", side_effect=ProcessLookupError):
            self.handlers[signal.SIGTERM](signal.SIGTERM, None)

    def test_other_children_are_reaped_whatever_their_exit_status(self):
        waits = [
            (101, 3 << 8),
            (102, 4 << 8),
            (103, 1 << 8),
            (104, signal.SIGKILL),
            (self.GUNICORN, 0),
            (105, 3 << 8),
            (0, 0),
        ]
        code, wait, kill = self.supervise(waits)
        self.assertEqual(code, 0)
        self.assertEqual(
            wait.call_args_list,
            [mock.call(-1, 0)] * 5 + [mock.call(-1, os.WNOHANG)] * 2,
        )
        kill.assert_not_called()

    def test_init_exits_with_gunicorns_status(self):
        for status, expected in (
            (0, 0),
            (1 << 8, 1),
            (3 << 8, 3),
            (4 << 8, 4),
            (signal.SIGTERM, 143),
            (signal.SIGKILL, 137),
        ):
            with self.subTest(status=status):
                waits = [(self.GUNICORN, status), ChildProcessError()]
                self.assertEqual(self.supervise(waits)[0], expected)

    def test_an_interrupted_wait_is_retried(self):
        waits = [InterruptedError(), (self.GUNICORN, 2 << 8), ChildProcessError()]
        code, wait, _ = self.supervise(waits)
        self.assertEqual(code, 2)
        self.assertEqual(wait.call_count, 3)


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


class SandboxFailureCauseTests(unittest.TestCase):
    """A jail that cannot start names the host setting that stops it, when the executor can see one:
    Docker's userns-remap (the container's uid map is not the host's) or the user-namespace
    restriction of Ubuntu 23.10 and later without the fa-code-executor profile. Otherwise the
    sentence stays as it is."""

    NSJAIL_STDERR = SandboxFailureMessageTests.NSJAIL_STDERR
    FIRST = (
        "The code sandbox could not start a run on this host: nsjail could not "
        "create the run's isolated environment (its Linux namespaces)."
    )

    def message(self, uid_map=None, restrict=None, label=None):
        server = load_server()
        with tempfile.TemporaryDirectory() as tmp:
            paths = {}
            for name, value in (("uid_map", uid_map), ("restrict", restrict), ("label", label)):
                path = os.path.join(tmp, name)
                if value is not None:
                    Path(path).write_text(value)
                paths[name] = path
            server.UID_MAP_PATH = paths["uid_map"]
            server.USERNS_RESTRICT_PATH = paths["restrict"]
            server.APPARMOR_LABEL_PATH = paths["label"]
            return server._no_output_error("Exit code", 255, self.NSJAIL_STDERR)["data"]

    def test_names_userns_remap_when_the_container_uid_map_is_remapped(self):
        data = self.message(uid_map="         0     100000      65536\n", restrict="0\n")
        self.assertTrue(data.startswith(self.FIRST))
        self.assertIn("userns-remap", data)
        self.assertNotIn("apparmor_restrict_unprivileged_userns", data)
        self.assertTrue(data.endswith("See INSTALLATION.md, Code evals and the sandbox."))

    def test_names_the_restriction_and_the_profile_when_the_executor_runs_without_it(self):
        data = self.message(uid_map="         0          0 4294967295\n", restrict="1\n", label="unconfined\n")
        self.assertTrue(data.startswith(self.FIRST))
        self.assertIn("kernel.apparmor_restrict_unprivileged_userns=1", data)
        self.assertIn("CODE_EXECUTOR_APPARMOR_PROFILE=fa-code-executor", data)
        self.assertNotIn("userns-remap", data)

    def test_does_not_blame_the_restriction_under_the_profile(self):
        data = self.message(
            uid_map="         0          0 4294967295\n", restrict="1\n", label="fa-code-executor (unconfined)\n"
        )
        self.assertNotIn("apparmor_restrict_unprivileged_userns", data)
        self.assertEqual(data, load_server().SANDBOX_UNAVAILABLE_MESSAGE)

    def test_keeps_the_sentence_when_nothing_can_be_read(self):
        data = self.message()
        self.assertEqual(data, load_server().SANDBOX_UNAVAILABLE_MESSAGE)

    def test_names_remap_before_the_restriction_when_both_apply(self):
        data = self.message(uid_map="0 100000 65536\n", restrict="1\n", label="unconfined\n")
        self.assertIn("userns-remap", data)
        self.assertNotIn("apparmor_restrict_unprivileged_userns", data)


class JailCommandTests(unittest.TestCase):
    """The jail must start inside the executor's container. nsjail's -T takes a mount point only
    (a ":size=" suffix becomes part of the path, which the read-only root cannot hold), and the
    kernel refuses a fresh procfs over Docker's masked /proc. Both were measured on a stock
    Ubuntu 24.04 host with Docker 29."""

    def jail_flags(self, run):
        server = load_server()
        server.NSJAIL_AVAILABLE = True
        server.NSJAIL_PATH = "/synthetic/nsjail"
        server.NODE_PATH = "/synthetic/node"
        seen = {}

        def fake_run(argv, **kwargs):
            seen["argv"] = list(argv)
            kwargs["stdout"].write(b'{"status": "success", "data": {"result": 1.0}}')
            return SimpleNamespace(returncode=0)

        def write_run_script(script, suffix):
            seen["host_script"] = f"/sandbox/scripts/eval_{'0' * 32}{suffix}"
            return seen["host_script"]

        with (
            mock.patch.object(server, "_prepare_network", return_value=None),
            mock.patch.object(server, "_write_run_script", side_effect=write_run_script, create=True),
            mock.patch.object(server.os, "makedirs"),
            mock.patch("builtins.open", mock.mock_open()),
            mock.patch.object(server.subprocess, "run", side_effect=fake_run),
        ):
            result = run(server)
        self.assertEqual(result["status"], "success")
        argv = seen["argv"]
        self.assertEqual(argv[0], "/synthetic/nsjail")
        self.seen = seen
        return argv[: argv.index("--")], argv[argv.index("--") + 1 :]

    def pairs(self, flags):
        return [(flags[i], flags[i + 1]) for i in range(len(flags) - 1) if flags[i] in ("-m", "-R")]

    def check(self, flags, command, jail_script):
        self.assertNotIn("-T", flags)
        mounts = self.pairs(flags)
        self.assertEqual(
            mounts,
            [
                ("-R", "/"),
                ("-m", "none:/tmp:tmpfs:size=16777216"),
                ("-m", "none:/dev/shm:tmpfs:size=16777216"),
                ("-m", "none:/sandbox/scripts:tmpfs:size=65536"),
                ("-R", f"{self.seen.get('host_script')}:{jail_script}"),
                ("-m", "none:/proc:tmpfs:size=4096"),
            ],
        )
        self.assertIn("--disable_proc", flags)
        self.assertLess(flags.index("--disable_proc"), flags.index("none:/proc:tmpfs:size=4096"))
        # The run sees its own script at a fixed path, never the shared directory's files.
        self.assertEqual(command[-1], jail_script)
        self.assertNotIn(self.seen.get("host_script"), command)

    def test_python_run_sees_only_its_own_script_and_private_tmp_shm_and_proc(self):
        flags, command = self.jail_flags(lambda server: server._execute_python_nsjail("def evaluate():\n    return 1", {}, 5))
        self.check(flags, command, "/sandbox/scripts/run.py")

    def test_javascript_run_sees_only_its_own_script_and_private_tmp_shm_and_proc(self):
        flags, command = self.jail_flags(lambda server: server._execute_javascript("function evaluate() { return 1; }", {}, 5))
        self.check(flags, command, "/sandbox/scripts/run.js")

    def test_both_jails_stop_a_runs_file_writes_at_the_output_limit(self):
        """A run's output file stops growing at MAX_OUTPUT_BYTES in either language."""
        server = load_server()
        runs = {
            "python": lambda s: s._execute_python_nsjail("def evaluate():\n    return 1", {}, 5),
            "javascript": lambda s: s._execute_javascript("function evaluate() { return 1; }", {}, 5),
        }
        for language, run in runs.items():
            with self.subTest(language=language):
                flags, _ = self.jail_flags(run)
                self.assertIn("--rlimit_fsize", flags)
                limit_mb = int(flags[flags.index("--rlimit_fsize") + 1])
                self.assertEqual(limit_mb * server.MB, server.MAX_OUTPUT_BYTES)

    def test_an_output_limit_below_one_mebibyte_is_rounded_up(self):
        """nsjail's --rlimit_fsize is in whole MB and 0 stops every write, so a limit
        under 1 MiB still gives each jail 1 MB."""

        def with_half_a_mebibyte(execute, code):
            def run(server):
                server.MAX_OUTPUT_BYTES = server.MB // 2
                return getattr(server, execute)(code, {}, 5)

            return run

        runs = {
            "python": ("_execute_python_nsjail", "def evaluate():\n    return 1"),
            "javascript": ("_execute_javascript", "function evaluate() { return 1; }"),
        }
        for language, (execute, code) in runs.items():
            with self.subTest(language=language):
                flags, _ = self.jail_flags(with_half_a_mebibyte(execute, code))
                self.assertEqual(flags[flags.index("--rlimit_fsize") + 1], "1")


# The tests below swap tempfile.TemporaryFile for CountingFile, which wraps this one.
REAL_TEMPORARY_FILE = tempfile.TemporaryFile


class CountingFile:
    """An unlinked temp file that counts the bytes read back from it."""

    def __init__(self, *args, **kwargs):
        self.file = REAL_TEMPORARY_FILE(*args, **kwargs)
        self.bytes_read = 0

    def read(self, size=-1):
        data = self.file.read(size)
        self.bytes_read += len(data)
        return data

    def __getattr__(self, name):
        return getattr(self.file, name)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.file.close()


class JailOutputTests(unittest.TestCase):
    """A jailed run that prints more than MAX_OUTPUT_BYTES, or whose output the jail's
    file-size limit cuts off at exactly MAX_OUTPUT_BYTES, is answered "Output too
    large", and the server reads back at most MAX_OUTPUT_BYTES + 1 bytes of what the
    run printed, however much that was."""

    TOO_LARGE = {"status": "error", "data": "Output too large"}

    def setUp(self):
        self.server = load_server()
        self.server.NSJAIL_AVAILABLE = True
        self.server.NSJAIL_PATH = "/synthetic/nsjail"
        self.server.NODE_PATH = "/synthetic/node"

    def a_3_mb_result(self):
        """A valid JSON result of a little over 3 MB."""
        reason = "x" * (3 * self.server.MB)
        return json.dumps(
            {"status": "success", "data": {"result": 1.0, "reason": reason}}
        ).encode()

    def run_printing(self, execute, code, printed):
        """Run code with a fake nsjail whose run prints the given bytes to wherever the
        server sends its output. Returns the answer and how many bytes of that output
        the server took in."""
        sinks = []

        def nsjail(argv, **kwargs):
            sink = kwargs.get("stdout")
            sinks.append(sink)
            if hasattr(sink, "write"):
                sink.write(printed)
                return SimpleNamespace(returncode=0)
            return SimpleNamespace(stdout=printed.decode(), stderr="", returncode=0)

        with (
            mock.patch.object(self.server, "_prepare_network", return_value=None),
            mock.patch.object(
                self.server, "_write_run_script", return_value="/x/eval.py"
            ),
            mock.patch.object(self.server.tempfile, "TemporaryFile", CountingFile),
            mock.patch.object(self.server.subprocess, "run", side_effect=nsjail),
        ):
            answer = execute(code, {}, 5)
        [sink] = sinks
        if hasattr(sink, "write"):
            return answer, sink.bytes_read
        # Through a pipe, the server takes in every byte the run prints.
        return answer, len(printed)

    def test_a_jailed_runs_output_is_read_up_to_the_limit_only(self):
        answer, taken = self.run_printing(
            self.server._execute_python_nsjail,
            "def evaluate():\n    return 1",
            self.a_3_mb_result(),
        )
        self.assertLessEqual(taken, self.server.MAX_OUTPUT_BYTES + 1)
        self.assertEqual(answer, self.TOO_LARGE)

    def test_a_javascript_runs_output_has_the_same_limit(self):
        answer, taken = self.run_printing(
            self.server._execute_javascript,
            "function evaluate() { return 1; }",
            self.a_3_mb_result(),
        )
        self.assertLessEqual(taken, self.server.MAX_OUTPUT_BYTES + 1)
        self.assertEqual(answer, self.TOO_LARGE)

    def test_python_output_cut_off_at_the_file_size_limit_is_too_large(self):
        """The jail's file-size limit stops a run that prints more at exactly
        MAX_OUTPUT_BYTES, in the middle of its JSON result."""
        cut_off = self.a_3_mb_result()[: self.server.MAX_OUTPUT_BYTES]
        answer, _ = self.run_printing(
            self.server._execute_python_nsjail,
            "def evaluate():\n    return 1",
            cut_off,
        )
        self.assertEqual(answer, self.TOO_LARGE)

    def test_javascript_output_cut_off_at_the_file_size_limit_is_too_large(self):
        cut_off = self.a_3_mb_result()[: self.server.MAX_OUTPUT_BYTES]
        answer, _ = self.run_printing(
            self.server._execute_javascript,
            "function evaluate() { return 1; }",
            cut_off,
        )
        self.assertEqual(answer, self.TOO_LARGE)


class FallbackOutputTests(unittest.TestCase):
    """Without nsjail, a run whose output RLIMIT_FSIZE cuts off at exactly
    MAX_OUTPUT_BYTES is answered "Output too large", as in the jails."""

    TOO_LARGE = {"status": "error", "data": "Output too large"}

    def setUp(self):
        self.server = load_server()
        self.server.NSJAIL_AVAILABLE = False
        self.server.NODE_PATH = "/synthetic/node"

    def run_printing(self, execute, code, printed):
        """Run code with a fake fallback process that prints the given bytes."""

        def popen(argv, **kwargs):
            kwargs["stdout"].write(printed)
            return SimpleNamespace(pid=0, returncode=0, wait=lambda timeout=None: 0)

        with (
            mock.patch.object(self.server, "_prepare_network", return_value=None),
            mock.patch.object(self.server, "_kill_session"),
            mock.patch.object(self.server.os, "chown"),
            mock.patch.object(self.server.subprocess, "Popen", side_effect=popen),
        ):
            return execute(code, {}, 5)

    def test_the_fallback_answers_too_large_at_the_limit(self):
        reason = "x" * self.server.MAX_OUTPUT_BYTES
        printed = json.dumps({"status": "success", "data": {"reason": reason}})
        cut_off = printed.encode()[: self.server.MAX_OUTPUT_BYTES]
        # A cut that ends in a space still fills the output file to the limit.
        cuts = {"in a word": cut_off, "after a space": cut_off[:-1] + b" "}
        runs = (
            (self.server._execute_python_fallback, "def evaluate():\n    return 1"),
            (self.server._execute_javascript, "function evaluate() { return 1; }"),
        )
        for execute, code in runs:
            for cut, printed_bytes in cuts.items():
                with self.subTest(run=execute.__name__, cut=cut):
                    answer = self.run_printing(execute, code, printed_bytes)
                    self.assertEqual(answer, self.TOO_LARGE)


class WriteRunScriptTests(unittest.TestCase):
    """Each run's script is a new file of its own with an unguessable name, readable only by the
    server's uid when the evals share it."""

    def test_scripts_are_private_new_files_with_unguessable_names(self):
        server = load_server()
        with tempfile.TemporaryDirectory(prefix="run-scripts-") as directory:
            with (
                mock.patch.object(server, "JAIL_SCRIPTS_DIR", directory, create=True),
                mock.patch.object(server, "EVAL_UID", os.geteuid()),
            ):
                first = server._write_run_script("print(1)\n", ".py")
                second = server._write_run_script("print(2)\n", ".py")
            self.assertNotEqual(first, second)
            for path, text in ((first, "print(1)\n"), (second, "print(2)\n")):
                self.assertEqual(os.path.dirname(path), directory)
                self.assertRegex(os.path.basename(path), r"^eval_[0-9a-f]{32}\.py$")
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
                self.assertEqual(Path(path).read_text(), text)


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

    def start(self):
        image = os.environ["CODE_EXECUTOR_TEST_IMAGE"]
        name = f"executor-init-test-{os.getpid()}-{self._testMethodName[5:]}"
        source = str(EXECUTOR / "executor_init.py")
        started = subprocess.run(["docker", "run", "-d", "--name", name, *self.OPTIONS,
                                  "-v", source + ":/sandbox/executor_init.py:ro", image],
                                 capture_output=True, text=True, timeout=120)
        self.addCleanup(subprocess.run, ["docker", "rm", "-f", name], capture_output=True, timeout=60)
        self.assertEqual(started.returncode, 0, started.stderr)
        healthy = False
        for _ in range(30):
            if self.healthy(name):
                healthy = True
                break
            if self.inspect(name, "{{.State.Running}}") != "true":
                break
            time.sleep(2)
        self.assertTrue(healthy, "executor did not start: " + self.logs(name))
        return name

    def healthy(self, name):
        return subprocess.run(["docker", "exec", name, *self.HEALTH], capture_output=True, timeout=30).returncode == 0

    def inspect(self, name, field):
        return subprocess.run(["docker", "inspect", "--format", field, name],
                              capture_output=True, text=True, timeout=30).stdout.strip()

    def logs(self, name):
        logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True, timeout=60)
        return (logs.stdout + logs.stderr)[-2000:]

    def stop_cleanly(self, name):
        """docker stop with a 10 s grace period returns within it, and the container
        exits 0."""
        began = time.monotonic()
        stopped = subprocess.run(["docker", "stop", "-t", "10", name], capture_output=True, text=True, timeout=60)
        seconds = time.monotonic() - began
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        self.assertLess(seconds, 10, self.logs(name))
        self.assertEqual(self.inspect(name, "{{.State.ExitCode}}"), "0", self.logs(name))

    def test_init_installs_the_policy_and_the_server_answers(self):
        self.start()

    def test_init_reaps_background_processes_and_the_server_keeps_answering(self):
        name = self.start()
        for code in range(1, 6):
            # Returns at once and leaves a background process that exits two seconds later.
            command = f'(sh -c "sleep 2; exit {code}" </dev/null >/dev/null 2>&1 &); exit 0'
            ran = subprocess.run(["docker", "exec", name, "sh", "-c", command],
                                 capture_output=True, text=True, timeout=30)
            self.assertEqual(ran.returncode, 0, ran.stderr)
        for _ in range(10):
            time.sleep(1)
            if self.inspect(name, "{{.State.Running}}") != "true":
                break
        self.assertEqual(self.inspect(name, "{{.State.Running}}"), "true", self.logs(name))
        self.assertTrue(self.healthy(name), self.logs(name))
        self.stop_cleanly(name)

    def test_a_stop_signal_ends_the_server_cleanly_within_the_grace_period(self):
        self.stop_cleanly(self.start())


if __name__ == "__main__":
    unittest.main()
