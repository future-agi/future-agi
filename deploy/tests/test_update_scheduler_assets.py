"""Grammar-specific renderer tests. Never install a crontab or enable a timer."""
from __future__ import annotations

import dataclasses
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from deploy.tests.test_update_runner_state import registration
from deploy.update.contracts import AutomationPolicy
from deploy.update.runner_state import LauncherConfig, RunnerStateStore, StoreError
from deploy.update.scheduler_assets import render_scheduler


def systemd_words(line, command=False):
    """Independent parser for generated quoted unit words, NOT shlex/shell.

    Only the two backslash escapes emitted here are accepted. Check doubled
    specifiers/variable escapes before decoding, so shell-style quoting cannot
    accidentally appear to work in this fixture.
    """
    result, offset = [], 0
    while offset < len(line):
        match = re.match(r'"((?:[^"\\]|\\[\\"])*)"(?: |$)', line[offset:])
        if match is None:
            raise AssertionError("invalid unit word at " + str(offset))
        value = re.sub(r'\\([\\"])', r'\1', match[1])
        if re.search(r"(?<!%)%(?!%)", value.replace("%%%%", "")):
            raise AssertionError("unescaped specifier")
        value = value.replace("%%", "%")
        if command:
            if re.search(r"(?<!\$)\$(?!\$)", value.replace("$$$$", "")):
                raise AssertionError("unescaped variable")
            value = value.replace("$$", "$")
        result.append(value)
        offset += match.end()
    return result


class AssetsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        # Real shell metacharacters, unicode, percent, quotes and backslashes.
        self.root = Path(directory.name).resolve() / "checkout é % ' \" \\ $(touch PWNED) ; & |"
        self.root.mkdir()
        (self.root / ".env").touch()
        (self.root / "bin").mkdir()
        self.capture = self.root / "captured.json"
        script = self.root / "capture.py"
        script.write_text("import json,os,sys\nfrom pathlib import Path\n"
                          "Path(sys.argv[1]).write_text(json.dumps({'argv':sys.argv[2:], 'env':dict(os.environ), "
                          "'mask':os.umask(0), 'stdin':sys.stdin.read()}))\n")
        # Owned stub only: executing generated assets never invokes the updater.
        launcher = self.root / "bin" / "update"
        import shlex
        launcher.write_text("#!/bin/bash\nexec \"$PYTHON\" -I " + shlex.quote(str(script))
                            + " " + shlex.quote(str(self.capture)) + ' "$@"\n')
        launcher.chmod(0o700)
        self.store = RunnerStateStore(self.root, self.root / ".env")
        self.reg = registration(self.store)
        context = "context %n $HOME ${HOME} ' \" \\ ; $(touch PWNED) é"
        self.reg = dataclasses.replace(self.reg, binding=dataclasses.replace(self.reg.binding, docker_context=context))
        self.store.register(self.reg)

    def render(self, backend):
        return render_scheduler(self.store, AutomationPolicy(), backend=backend)

    def test_cron_shell_syntax_and_real_argv_env_roundtrip(self):
        assets = self.render("cron")
        source = next(iter(assets.files.values()))
        line = next(line for line in source.splitlines() if line.startswith("*/5"))
        command = line.split(" ", 5)[5]
        self.assertNotIn("%", command)  # cron never gets a percent/input delimiter
        syntax = subprocess.run(["/bin/sh", "-n"], input=command, text=True, capture_output=True)
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        env = dict(os.environ, DOCKER_HOST="bad-daemon", PYTHON="relative/evil", COMPOSE_FILE="evil.yml",
                   BASH_ENV=str(self.root / "never-load-this"))
        result = subprocess.run(["/bin/sh", "-c", command], cwd=str(self.root), env=env, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        observed = json.loads(self.capture.read_text())
        self.assertEqual(observed["argv"], ["run", "--env-file", str(self.root / ".env")])
        self.assertEqual(observed["env"]["DOCKER_CONTEXT"], self.reg.binding.docker_context)
        self.assertEqual(observed["env"]["HOME"], str(self.root))
        self.assertEqual(observed["env"]["PATH"], "/usr/bin:/bin")
        self.assertEqual(observed["env"]["PYTHON"], str(self.reg.launcher.python))
        for key in ("BASH_ENV", "DOCKER_HOST", "COMPOSE_FILE"):
            self.assertNotIn(key, observed["env"])
        self.assertEqual(observed["mask"], 0o77)
        self.assertEqual(observed["stdin"], "")
        self.assertFalse((self.root / "PWNED").exists())

    def test_systemd_grammar_decodes_exact_argv_and_environment(self):
        assets = self.render("systemd")
        service = assets.files[assets.name + ".service"]
        directives = dict(line.split("=", 1) for line in service.splitlines() if "=" in line and not line.startswith("#"))
        argv = systemd_words(directives["ExecStart"], command=True)
        self.assertEqual(argv[:2], ["/usr/bin/env", "-i"])
        self.assertEqual(argv[2:6], ["HOME=" + str(self.root), "PATH=/usr/bin:/bin",
                                    "PYTHON=" + str(self.reg.launcher.python),
                                    "DOCKER_CONTEXT=" + self.reg.binding.docker_context])
        self.assertEqual(argv[6:], ["/bin/bash", "--", str(self.root / "bin" / "update"),
                                   "run", "--env-file", str(self.root / ".env")])
        self.assertEqual(systemd_words(directives["Environment"]), argv[2:6])
        # WorkingDirectory is a raw path directive: unlike ExecStart it does
        # NOT remove quotes or C-unescape. systemd appends no shell semantics.
        self.assertEqual(Path(directives["WorkingDirectory"].replace("%%", "%")).resolve(), self.root)
        self.assertEqual(directives["User"], pwd.getpwuid(os.geteuid()).pw_name)
        self.assertEqual(directives["Type"], "oneshot")
        self.assertEqual(directives["KillMode"], "control-group")
        self.assertEqual(directives["Restart"], "no")
        self.assertEqual(directives["UMask"], "0077")
        self.assertEqual(directives["StandardInput"], "null")
        self.assertEqual(directives["StandardOutput"], "null")
        self.assertEqual(directives["StandardError"], "null")
        self.assertGreater(int(directives["TimeoutStartSec"]), AutomationPolicy().tick_timeout_seconds + 600)
        self.assertEqual(directives["TimeoutStopSec"], "60")
        self.assertEqual(subprocess.run(argv, cwd=str(self.root), stdin=subprocess.DEVNULL,
                                        capture_output=True, timeout=15).returncode, 0)
        self.assertFalse((self.root / "PWNED").exists())
        self.assertEqual(json.loads(self.capture.read_text())["env"]["DOCKER_CONTEXT"], self.reg.binding.docker_context)

    def test_interpreter_with_percent_unicode_and_spaces_runs_exact_file(self):
        python = self.root.parent / "python % é"
        python.symlink_to(Path(sys.executable))
        env = self.root / "second.env"
        env.touch()
        other = RunnerStateStore(self.root, env)
        reg = registration(other)
        reg = dataclasses.replace(reg, launcher=dataclasses.replace(reg.launcher, python=python))
        other.register(reg)
        assets = render_scheduler(other, AutomationPolicy(), backend="cron")
        command = next(line for line in next(iter(assets.files.values())).splitlines() if line.startswith("*/5")).split(" ", 5)[5]
        result = subprocess.run(["/bin/sh", "-c", command], cwd=str(self.root), capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.capture.read_text())["env"]["PYTHON"], str(python))

    def test_cron_length_bound_and_systemd_trailing_path_characters(self):
        root = self.root / "trailing \\ "
        root.mkdir()
        (root / ".env").touch()
        (root / "bin").mkdir()
        (root / "bin" / "update").write_text("#!/bin/bash\nexit 0\n")
        (root / "bin" / "update").chmod(0o700)
        other = RunnerStateStore(root, root / ".env")
        reg = registration(other)
        reg = dataclasses.replace(reg, binding=dataclasses.replace(reg.binding, docker_context="x" * 2000))
        other.register(reg)
        with self.assertRaises(StoreError):
            render_scheduler(other, AutomationPolicy(), backend="cron")
        assets = render_scheduler(other, AutomationPolicy(), backend="systemd")
        line = next(line for line in assets.files[assets.name + ".service"].splitlines() if line.startswith("WorkingDirectory="))
        self.assertTrue(line.endswith("/."))
        self.assertEqual(Path(line.split("=", 1)[1].replace("%%", "%")).resolve(), root)

    def test_systemd_timer_period_persistence_and_delay(self):
        assets = self.render("systemd")
        text = assets.files[assets.name + ".timer"]
        self.assertIn("OnCalendar=*-*-* *:0/5:00", text)
        self.assertIn("Persistent=true", text)
        self.assertIn("RandomizedDelaySec=60", text)
        self.assertIn("Unit=" + assets.name + ".service", text)
        self.assertNotIn("ExecStart", text)

    def test_render_is_read_only_and_disabled_manual_stay_disabled(self):
        before = {str(p): p.read_bytes() for p in self.store.path.rglob("*") if p.is_file()}
        for backend in ("cron", "systemd"):
            assets = self.render(backend)
            self.assertIn(assets.name, assets.removal_instructions)
            self.assertNotIn("sudo", "".join(assets.files.values()))
        after = {str(p): p.read_bytes() for p in self.store.path.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertIsNone(self.store.read_control().enabled_override)

    def test_missing_registration_refuses_runnable_assets(self):
        (self.store.path / "registration.json").unlink()
        for backend in ("cron", "systemd"):
            with self.assertRaises(StoreError):
                self.render(backend)

    def test_controls_newlines_null_relative_python_and_path_entries_rejected(self):
        for python in (Path("python3"), Path("venv/bin/python"), Path("/tmp/python\nINJECT=1"), Path("/tmp/python\0")):
            with self.assertRaises(StoreError):
                LauncherConfig(python, self.root, "/usr/bin:/bin", self.reg.launcher.user)
        for path in ("", ":/bin", "/bin:", "/bin:.", "bin:/usr/bin", "/bin\nEVIL=1", "/bin\r", "/bin\t"):
            with self.assertRaises(StoreError):
                dataclasses.replace(self.reg.launcher, path=path)
        for context in ("ctx\nExecStart=/bad", "ctx\r", "ctx\0", "ctx\t"):
            with self.assertRaises(StoreError):
                dataclasses.replace(self.reg, binding=dataclasses.replace(self.reg.binding, docker_context=context))

    def test_original_user_home_and_interpreter_required(self):
        for changes in ({"host_uid": os.geteuid() + 1},):
            with self.assertRaises(StoreError):
                self.store.register(dataclasses.replace(self.reg, binding=dataclasses.replace(self.reg.binding, **changes)))
        launcher = self.root / "bin" / "update"
        launcher.chmod(0o600)
        with self.assertRaises(StoreError):
            self.render("cron")

    def test_production_selector_and_unique_installation_name(self):
        second = RunnerStateStore(self.root, self.root / ".env", True, "distributed")
        second.register(registration(second))
        assets = render_scheduler(second, AutomationPolicy(), backend="systemd")
        command = next(line.split("=", 1)[1] for line in assets.files[assets.name + ".service"].splitlines()
                       if line.startswith("ExecStart="))
        self.assertEqual(systemd_words(command, command=True)[-1], "--production")
        self.assertNotEqual(assets.name, self.render("systemd").name)
        with self.assertRaises(StoreError):
            self.render("launchd")

    @unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("systemd-analyze"),
                         "systemd-analyze requires Linux; renderer grammar and real argv are tested on all hosts")
    def test_linux_systemd_tool_verification_without_install(self):
        # The tool is optional on macOS, never provisioned/installed by tests.
        # A non-Linux unit manager is not simulated or claimed as qualified.
        assets = self.render("systemd")
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for name, text in assets.files.items():
                path = Path(directory) / name
                path.write_text(text)
                paths.append(str(path))
            result = subprocess.run(["systemd-analyze", "verify", *paths], capture_output=True, text=True, timeout=30,
                                    env=dict(os.environ, SYSTEMD_UNIT_PATH=directory + ":"))
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
