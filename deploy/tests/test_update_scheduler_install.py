"""Finite fake managers/private paths only. NOT real-OS scheduler qualification."""
from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from deploy.tests.test_update_runner_state import NOW, registration
from deploy.update.contracts import AutomationPolicy
from deploy.update.runner_state import RunnerStateStore
from deploy.update.scheduler_assets import render_scheduler
from deploy.update import scheduler_install as si


class Crash(BaseException):
    pass


class Manager:
    """Only fixed argv understood. State changes are observable across retry calls."""
    def __init__(self, fixture):
        self.f = fixture
        self.tab = b"# unrelated \xff\r\n17 * * * * /usr/bin/true\n"
        self.calls, self.effects = [], []
        self.loaded = {}
        self.active = False
        self.cron_running = True
        self.manager_running = True
        self.linger = True
        self.start_works = True
        self.crash_after = None
        self.crash_before = None
        self.dropin = ""
        self.foreign_fragment = ""
        self.job = "0"
        self.before_read = None
        self.reads = 0

    def result(self, args, code=0, out=b"", err=b""):
        return subprocess.CompletedProcess(args, code, out, err)

    def effect(self, kind):
        if self.crash_before == kind:
            self.crash_before = None
            raise Crash()
        self.effects.append(kind)

    def after(self, kind):
        if self.crash_after == kind:
            self.crash_after = None
            raise Crash()

    def __call__(self, args, **kwargs):
        self.f.assertEqual(kwargs["timeout"], 15)
        self.f.assertFalse(kwargs.get("shell", False))
        self.f.assertEqual(kwargs["env"]["HOME"], str(self.f.root))
        self.f.assertEqual(kwargs["env"]["PATH"], self.f.reg.launcher.path)
        self.f.assertNotIn("BASH_ENV", kwargs["env"])
        self.f.assertNotIn("DOCKER_HOST", kwargs["env"])
        self.f.assertTrue(Path(args[0]).is_absolute())
        # Commands must never run while the short metadata lock is held.
        with self.f.store._lock("state.lock"):
            pass
        self.calls.append(args)
        tool = Path(args[0]).name
        argv = args[1:]
        if tool == "crontab":
            self.f.assertNotIn("-u", argv)
            if argv == ["-l"]:
                self.reads += 1
                if self.before_read:
                    self.before_read(self.reads)
                return self.result(args, out=self.tab)
            self.f.assertEqual(argv, ["-"])
            self.effect("crontab")
            self.tab = kwargs["input"]
            self.after("crontab")
            return self.result(args)
        if tool == "pgrep":
            self.f.assertIn(argv, (["-x", "-u", "0", "cron"], ["-x", "-u", "0", "crond"]))
            return self.result(args, 0 if self.cron_running else 1, b"987\n" if self.cron_running else b"")
        if tool == "launchctl":
            if argv == ["print", "system/com.vix.cron"]:
                return self.result(args, out=b"state = running\npid = 987\n" if self.cron_running else b"state = waiting\n")
            self.f.assertEqual(argv, ["print-disabled", "system"])
            return self.result(args, out=b'"com.vix.cron" => false\n')
        if tool == "loginctl":
            self.f.assertEqual(argv, ["--no-pager", "show-user", str(os.geteuid()), "--property=Linger", "--value"])
            return self.result(args, out=b"yes\n" if self.linger else b"no\n")
        self.f.assertEqual(tool, "systemctl")
        self.f.assertIn(argv[0], ("--system", "--user"))
        self.f.assertEqual(argv[1:3], ["--no-pager", "--no-ask-password"])
        argv = argv[3:]
        if argv == ["show", "--property=Version", "--value"]:
            return self.result(args, 0 if self.manager_running else 1, b"256\n" if self.manager_running else b"")
        if argv[0] in ("is-active", "is-enabled"):
            self.f.assertIn(argv[1], ("cron.service", "crond.service"))
            out = b"active\n" if argv[0] == "is-active" else b"enabled\n"
            return self.result(args, 0 if self.cron_running else 1, out if self.cron_running else b"inactive\n")
        if argv[0] == "show":
            name = argv[1]
            path = self.f.units / name
            loaded = name in self.loaded
            link = self.f.units / "timers.target.wants" / name
            data = {
                "LoadState": "loaded" if loaded else "not-found",
                "ActiveState": "active" if name.endswith(".timer") and self.active else "inactive",
                "SubState": "waiting" if name.endswith(".timer") and self.active else "dead",
                "UnitFileState": ("enabled" if link.is_symlink() else "disabled") if loaded else "",
                "FragmentPath": (self.foreign_fragment or str(path)) if loaded else "",
                "DropInPaths": self.dropin,
                "Job": self.job,
                "NeedDaemonReload": "yes" if loaded and (not path.exists() or path.read_bytes() != self.loaded[name]) else "no",
            }
            return self.result(args, out="".join(k + "=" + v + "\n" for k, v in data.items()).encode())
        if argv == ["daemon-reload"]:
            self.effect("reload")
            self.loaded = {str(p.name): p.read_bytes() for p in self.f.units.iterdir()
                           if p.suffix in (".timer", ".service")}
            self.after("reload")
            return self.result(args)
        self.f.assertIn(argv[0], ("start", "stop"))
        self.f.assertEqual(argv[1:], [self.f.name + ".timer"])
        self.effect(argv[0])
        self.active = argv[0] == "start" and self.start_works
        self.after(argv[0])
        return self.result(args)


class ActivationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="scheduler-", dir=os.environ.get("FAGI_SCHEDULER_TEST_ROOT", str(Path.home())))
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "checkout % ' $ é"
        self.root.mkdir(mode=0o700)
        (self.root / ".env").write_text("PRIVATE=keep\n")
        binpath = self.root / "bin"
        binpath.mkdir()
        for name in ("update", "systemctl", "crontab", "launchctl", "loginctl", "pgrep"):
            path = binpath / name
            path.write_text("#!/bin/sh\nexit 99\n")  # Never the real host manager, even if a test misses its seam.
            path.chmod(0o700)
        self.store = RunnerStateStore(self.root, self.root / ".env")
        self.reg = registration(self.store)
        self.reg = dataclasses.replace(self.reg, launcher=dataclasses.replace(
            self.reg.launcher, path=str(binpath) + ":/usr/bin:/bin"))
        self.store.register(self.reg)
        self.name = "futureagi-compose-update-" + self.store.selection_hash
        self.units = self.base / "units"
        self.units.mkdir(mode=0o700)
        self.runtime = self.base / "runtime"
        self.runtime.mkdir(mode=0o700)
        self.manager = Manager(self)
        self.host = si._Host(platform="linux", unit_directory=self.units, runtime_directory=self.runtime, run=self.manager)
        self.policy = AutomationPolicy()
        self.before = self.metadata()

    def metadata(self):
        return {name: (self.store.path / name).read_bytes() for name in ("control.json", "registration.json", "state.json")}

    def call(self, method=si.install_scheduler, backend="systemd", **kwargs):
        return method(self.store, self.policy, backend=backend, _host=self.host, **kwargs)

    def install(self, backend="systemd", **kwargs):
        result = self.call(backend=backend, **kwargs)
        self.assertTrue(result.active, result)
        self.assertEqual(self.metadata(), self.before)
        return result

    def test_actual_install_inspect_duplicate_remove_and_private_evidence(self):
        for backend in ("systemd", "cron"):
            with self.subTest(backend=backend):
                # Separate selection, never cross-adopt another backend's ledger.
                if backend == "cron":
                    self.setUp()
                original_tab = self.manager.tab
                self.install(backend)
                ledger = si._read(self.store)
                self.assertEqual(ledger.phase, "active")
                self.assertEqual(ledger.completed, si.INSTALL[backend])
                self.assertEqual((self.store.path / si.LEDGER).stat().st_mode & 0o777, 0o600)
                effects = list(self.manager.effects)
                self.install(backend)
                self.assertTrue(self.call(si.inspect_activation, backend).active)
                self.assertEqual(self.manager.effects, effects)
                self.store.change_control("disable", NOW)
                self.before = self.metadata()
                result = self.call(si.remove_scheduler, backend)
                self.assertEqual(result.state, "removed", result)
                effects = list(self.manager.effects)
                self.assertEqual(self.call(si.remove_scheduler, backend).state, "removed")
                self.assertEqual(effects, self.manager.effects)
                self.assertTrue((self.store.path / si.LEDGER).is_file())
                self.assertEqual(self.metadata(), self.before)
                self.assertEqual(self.manager.tab, original_tab)
                self.assertEqual(self.call(backend=backend).reason, si.ActivationReason.REMOVED)

    def test_renderer_and_enable_flag_are_not_activation(self):
        for backend in ("cron", "systemd"):
            render_scheduler(self.store, self.policy, backend=backend)
            self.store.change_control("enable", NOW)
            result = self.call(si.inspect_activation, backend)
            self.assertFalse(result.active)
            self.assertEqual(result.reason, si.ActivationReason.ABSENT)
        self.assertFalse(self.manager.effects)
        self.assertFalse((self.store.path / si.LEDGER).exists())

    def test_installed_but_inactive_never_claimed_active_or_restarted(self):
        self.install()
        self.manager.active = False
        before = list(self.manager.effects)
        for method in (si.install_scheduler, si.inspect_activation):
            self.assertEqual(self.call(method).reason, si.ActivationReason.INACTIVE)
        self.assertEqual(before, self.manager.effects)

    def test_cron_daemon_not_running_or_stopped_after_install(self):
        self.manager.cron_running = False
        self.assertEqual(self.call(backend="cron").reason, si.ActivationReason.CRON)
        self.assertFalse((self.store.path / si.LEDGER).exists())
        self.manager.cron_running = True
        self.install("cron")
        self.manager.cron_running = False
        self.assertEqual(self.call(si.inspect_activation, "cron").reason, si.ActivationReason.CRON)
        self.assertEqual(self.call(si.remove_scheduler, "cron").state, "removed")

    def test_mac_cron_and_non_systemd_wsl_daemon(self):
        self.host = dataclasses.replace(self.host, platform="darwin")
        self.install("cron")
        self.assertTrue(any(a[1:] == ["print", "system/com.vix.cron"] for a in self.manager.calls))
        self.setUp()
        self.install("cron", cron_service="cron")
        self.assertTrue(any(a[1:] == ["-x", "-u", "0", "cron"] for a in self.manager.calls))

    def test_user_manager_requires_preexisting_linger_and_explicit_bus(self):
        self.manager.linger = False
        self.assertEqual(self.call(scope="user").reason, si.ActivationReason.LINGER)
        self.assertFalse(self.manager.effects)
        self.manager.linger = True
        self.install(scope="user")
        manager = si._Manager(self.reg, self.host, "user")
        self.assertEqual(manager.env["DBUS_SESSION_BUS_ADDRESS"], "unix:path=" + str(self.runtime / "bus"))
        self.assertFalse(any("enable-linger" in a for a in self.manager.calls))
        service = (self.units / (self.name + ".service")).read_text()
        self.assertIn("User=" + self.reg.launcher.user + "\n", service)
        self.manager.linger = False
        self.assertEqual(self.call(si.inspect_activation, scope="user").reason, si.ActivationReason.LINGER)
        self.assertEqual(self.call(si.remove_scheduler, scope="user").state, "removed")

    def test_fixed_argv_current_user_special_paths_and_explicit_launcher(self):
        with mock.patch.dict(os.environ, {"SUDO_UID": "0", "DOCKER_HOST": "secret", "BASH_ENV": "evil"}):
            self.install("cron")
        self.assertEqual(self.manager.tab.count(("# BEGIN " + self.name).encode()), 1)
        installed = si._read(self.store).assets[0].content
        self.assertNotIn("%", next(line for line in installed.splitlines() if line.startswith("*/5")))
        self.assertIn("DOCKER_CONTEXT=dedicated-context", installed)
        self.assertNotIn("-u", [word for args in self.manager.calls if Path(args[0]).name == "crontab" for word in args])
        self.assertFalse((self.root / "PWNED").exists())

    def test_refuses_foreign_cron_and_duplicate_or_malformed_markers(self):
        assets = render_scheduler(self.store, self.policy, backend="cron")
        block = next(iter(assets.files.values())).encode()
        original = self.manager.tab
        for suffix in (block, block * 2, block.replace(b"# END", b"# END invalid"),
                       block.replace(b"# BEGIN", b" # BEGIN"), block.split(b"# END")[0]):
            with self.subTest(suffix=suffix[:30]):
                self.manager.tab = original + suffix
                result = self.call(backend="cron")
                self.assertEqual(result.reason, si.ActivationReason.FOREIGN)
                self.assertEqual(self.manager.tab, original + suffix)
                self.assertFalse(self.manager.effects)
                self.assertFalse((self.store.path / si.LEDGER).exists())

    def test_edited_owned_cron_refuses_removal_preserves_unrelated_bytes(self):
        self.install("cron")
        self.manager.tab = self.manager.tab.replace(b"*/5", b"*/7") + b"# new unrelated\n"
        before = self.manager.tab
        self.assertEqual(self.call(si.remove_scheduler, "cron").reason, si.ActivationReason.FOREIGN)
        self.assertEqual(self.manager.tab, before)
        self.assertEqual(self.manager.effects, ["crontab"])

    def test_cron_preserves_no_newline_preimage_exactly(self):
        self.manager.tab = b"# original without newline \xfe"
        original = self.manager.tab
        self.install("cron")
        self.assertEqual(self.call(si.remove_scheduler, "cron").state, "removed")
        self.assertEqual(self.manager.tab, original)

    def test_cron_rebases_concurrent_edits_at_compare_and_remove(self):
        original = self.manager.tab
        addition = b"# external edit\n"
        self.manager.before_read = lambda n: setattr(self.manager, "tab", self.manager.tab + addition) if n == 3 else None
        self.install("cron")
        self.assertTrue(self.manager.tab.startswith(original + addition))
        self.manager.before_read = None
        self.manager.tab += b"# appended after owned block\n"
        self.assertEqual(self.call(si.remove_scheduler, "cron").state, "removed")
        self.assertEqual(self.manager.tab, original + addition + b"# appended after owned block\n")

    def test_continuous_cron_edits_refuse_without_lost_updates(self):
        original = self.manager.tab
        self.manager.before_read = lambda n: setattr(self.manager, "tab", self.manager.tab + b"# edit\n")
        result = self.call(backend="cron")
        self.assertEqual(result.reason, si.ActivationReason.CONCURRENT)
        self.assertTrue(self.manager.tab.startswith(original))
        self.assertFalse(self.manager.effects)

    def test_crash_after_crontab_replace_recovers_without_second_replacement(self):
        self.manager.crash_after = "crontab"
        with self.assertRaises(Crash):
            self.call(backend="cron")
        self.install("cron")
        self.assertEqual(self.manager.effects, ["crontab"])
        self.manager.crash_after = "crontab"
        with self.assertRaises(Crash):
            self.call(si.remove_scheduler, "cron")
        self.assertEqual(self.call(si.remove_scheduler, "cron").state, "removed")
        self.assertEqual(self.manager.effects, ["crontab", "crontab"])

    def test_crashes_after_each_manager_side_effect_reconcile_not_replay(self):
        for effect in ("reload", "start"):
            with self.subTest(effect=effect):
                if effect == "start":
                    self.setUp()
                self.manager.crash_after = effect
                with self.assertRaises(Crash):
                    self.call()
                self.install()
                self.assertEqual(self.manager.effects, ["reload", "start"])
        self.manager.crash_after = "stop"
        with self.assertRaises(Crash):
            self.call(si.remove_scheduler)
        self.assertEqual(self.call(si.remove_scheduler).state, "removed")
        self.assertEqual(self.manager.effects.count("stop"), 1)

    def test_uncertain_start_before_command_is_not_blindly_replayed(self):
        self.manager.crash_before = "start"
        with self.assertRaises(Crash):
            self.call()
        self.assertEqual(self.call().reason, si.ActivationReason.UNCERTAIN)
        self.assertNotIn("start", self.manager.effects)
        self.assertEqual(self.call(si.remove_scheduler).state, "removed")
        self.assertNotIn("stop", self.manager.effects)

    def test_start_zero_exit_without_active_timer_is_incomplete(self):
        self.manager.start_works = False
        self.assertEqual(self.call().reason, si.ActivationReason.INACTIVE)
        self.assertEqual(self.call().reason, si.ActivationReason.UNCERTAIN)
        self.assertEqual(self.manager.effects.count("start"), 1)

    def test_crash_after_each_file_write_before_ledger_ack(self):
        for suffix in ("service", "timer"):
            with self.subTest(suffix=suffix):
                if suffix == "timer":
                    self.setUp()
                actual = si._install_file
                def fail(fd, asset):
                    actual(fd, asset)
                    if asset.name.endswith("." + suffix):
                        raise Crash()
                with mock.patch.object(si, "_install_file", side_effect=fail):
                    with self.assertRaises(Crash):
                        self.call()
                inode = (self.units / (self.name + "." + suffix)).stat().st_ino
                self.install()
                self.assertEqual((self.units / (self.name + "." + suffix)).stat().st_ino, inode)
                self.assertEqual(self.manager.effects, ["reload", "start"])

    def test_crash_after_atomic_link_reuses_exact_staging_inode(self):
        actual = os.link
        def fail(*args, **kwargs):
            actual(*args, **kwargs)
            raise Crash()
        with mock.patch.object(si.os, "link", side_effect=fail):
            with self.assertRaises(Crash):
                self.call()
        path = self.units / (self.name + ".service")
        self.assertEqual(path.stat().st_nlink, 2)
        self.install()
        self.assertEqual(path.stat().st_nlink, 1)
        self.assertFalse(any(p.name.endswith(".setup") for p in self.units.iterdir()))

    def test_partial_install_removal_retains_intent_across_removal_crash(self):
        actual = si._install_file
        def fail(fd, asset):
            actual(fd, asset)
            raise Crash()
        with mock.patch.object(si, "_install_file", side_effect=fail):
            with self.assertRaises(Crash):
                self.call()
        actual_remove = si._remove_systemd
        def stop_then_fail(ledger, manager, step, fresh):
            actual_remove(ledger, manager, step, fresh)
            if step == "stop":
                raise Crash()
        with mock.patch.object(si, "_remove_systemd", side_effect=stop_then_fail):
            with self.assertRaises(Crash):
                self.call(si.remove_scheduler)
        self.assertEqual(self.call(si.remove_scheduler).state, "removed")
        self.assertFalse(list(self.units.glob("*.service")))
        self.assertNotIn("start", self.manager.effects)

    def test_missing_manager_path_authority_and_platform_prerequisites(self):
        self.manager.manager_running = False
        self.assertEqual(self.call().reason, si.ActivationReason.MANAGER)
        self.manager.manager_running = True
        access = os.access
        with mock.patch.object(si.os, "access", side_effect=lambda p, mode: False if mode == os.W_OK else access(p, mode)):
            self.assertEqual(self.call().reason, si.ActivationReason.AUTHORITY)
        self.host = dataclasses.replace(self.host, platform="win32")
        self.assertEqual(self.call().reason, si.ActivationReason.UNSUPPORTED)
        self.assertFalse(self.manager.effects)
        self.host = dataclasses.replace(self.host, platform="linux", unit_directory=self.base / "missing")
        self.assertEqual(self.call().reason, si.ActivationReason.UNSAFE)
        self.host = dataclasses.replace(self.host, unit_directory=self.units)
        with mock.patch.object(si.shutil, "which", return_value=None):
            self.assertEqual(self.call().reason, si.ActivationReason.DEPENDENCY)

    def test_symlinks_foreign_files_permissions_and_dropins_refused(self):
        service = self.units / (self.name + ".service")
        service.write_text("foreign")
        service.chmod(0o600)
        self.assertEqual(self.call().reason, si.ActivationReason.FOREIGN)
        self.assertEqual(service.read_text(), "foreign")
        service.unlink()
        service.symlink_to(self.root / ".env")
        self.assertFalse(self.call().active)
        self.assertEqual((self.root / ".env").read_text(), "PRIVATE=keep\n")
        service.unlink()
        self.units.chmod(0o777)
        self.assertEqual(self.call().reason, si.ActivationReason.UNSAFE)
        self.units.chmod(0o700)
        self.install()
        effects = list(self.manager.effects)
        self.manager.dropin = "/foreign/override.conf"
        self.assertEqual(self.call(si.remove_scheduler).reason, si.ActivationReason.FOREIGN)
        self.assertEqual(self.manager.effects, effects)

    def test_unsafe_directory_and_wants_symlink_not_followed(self):
        alias = self.base / "alias"
        alias.symlink_to(self.units, target_is_directory=True)
        self.host = dataclasses.replace(self.host, unit_directory=alias)
        self.assertEqual(self.call().reason, si.ActivationReason.UNSAFE)
        self.host = dataclasses.replace(self.host, unit_directory=self.units)
        (self.units / "timers.target.wants").symlink_to(self.root, target_is_directory=True)
        self.assertEqual(self.call().reason, si.ActivationReason.UNSAFE)
        self.assertFalse(self.manager.effects)

    def test_removal_refuses_edited_unit_and_link_before_stopping(self):
        self.install()
        service = self.units / (self.name + ".service")
        original = service.read_bytes()
        service.write_bytes(original + b"# user edited\n")
        self.assertEqual(self.call(si.remove_scheduler).reason, si.ActivationReason.FOREIGN)
        self.assertNotIn("stop", self.manager.effects)
        service.write_bytes(original)
        link = self.units / "timers.target.wants" / (self.name + ".timer")
        link.unlink()
        link.symlink_to("/foreign.timer")
        self.assertEqual(self.call(si.remove_scheduler).reason, si.ActivationReason.FOREIGN)
        self.assertNotIn("stop", self.manager.effects)

    def test_unrelated_units_links_metadata_and_evidence_survive_removal(self):
        other = self.units / "other.service"
        other.write_bytes(b"unrelated")
        other.chmod(0o600)
        self.install()
        wants = self.units / "timers.target.wants"
        (wants / "other.timer").symlink_to("/other.timer")
        with self.store._directory(create=True, child="evidence") as fd:
            self.store._write_raw(fd, "receipt", b"keep evidence")
        self.assertEqual(self.call(si.remove_scheduler).state, "removed")
        self.assertEqual(other.read_bytes(), b"unrelated")
        self.assertEqual(os.readlink(str(wants / "other.timer")), "/other.timer")
        self.assertEqual((self.store.path / "evidence/receipt").read_bytes(), b"keep evidence")
        self.assertEqual(self.metadata(), self.before)

    def test_closed_bounded_ledger_and_lock_contention(self):
        self.install()
        ledger = self.store.path / si.LEDGER
        original = ledger.read_bytes()
        for raw in (original[:-2] + b',"extra":"secret"}\n', b"x" * 65537,
                    original.replace(b'"schema_version":1', b'"schema_version":true')):
            ledger.write_bytes(raw)
            result = self.call(si.inspect_activation)
            self.assertEqual(result.reason, si.ActivationReason.METADATA)
            self.assertNotIn("secret", repr(result))
        ledger.write_bytes(original)
        with self.store.runner_lock():
            self.assertEqual(self.call().reason, si.ActivationReason.BUSY)
        with self.store._lock("state.lock"):
            # Only a metadata mutation needs state.lock; inspect stays read-only.
            self.assertFalse(self.call(si.remove_scheduler).active)
        self.assertEqual(self.manager.effects, ["reload", "start"])

    def test_command_output_errors_timeout_and_identity_are_sanitized(self):
        def bad(args, **kwargs):
            return subprocess.CompletedProcess(args, 13, b"secret TOKEN\x1b[2J", b"private path /hidden password")
        self.host = dataclasses.replace(self.host, run=bad)
        result = self.call(backend="cron")
        self.assertFalse(result.active)
        self.assertNotIn("TOKEN", repr(result))
        self.assertNotIn("password", repr(result))
        self.host = dataclasses.replace(self.host, run=mock.Mock(side_effect=subprocess.TimeoutExpired("secret", 15)))
        self.assertEqual(self.call().reason, si.ActivationReason.COMMAND)
        with mock.patch.object(si.os, "getuid", return_value=os.geteuid() + 1):
            self.assertEqual(self.call().reason, si.ActivationReason.AUTHORITY)

    def test_altered_shell_cannot_masquerade_as_a_working_cron_opportunity(self):
        original = self.manager.tab
        self.manager.tab = b'SHELL="/bin/false"\n' + original
        self.assertEqual(self.call(backend="cron").reason, si.ActivationReason.CRON)
        self.assertFalse(self.manager.effects)
        self.manager.tab = b'SHELL="/bin/sh"\n' + original
        self.install("cron")
        self.manager.tab = self.manager.tab.replace(b'/bin/sh', b'/bin/false', 1)
        self.assertEqual(self.call(si.inspect_activation, "cron").reason, si.ActivationReason.CRON)
        self.assertEqual(self.call(si.remove_scheduler, "cron").state, "removed")
        self.assertEqual(self.manager.tab, b'SHELL="/bin/false"\n' + original)

    def test_queued_manager_job_is_not_active_proof_or_safe_removal(self):
        self.install()
        self.manager.job = "123"
        effects = list(self.manager.effects)
        self.assertEqual(self.call(si.inspect_activation).reason, si.ActivationReason.UNCERTAIN)
        self.assertEqual(self.call(si.remove_scheduler).reason, si.ActivationReason.UNCERTAIN)
        self.assertEqual(self.manager.effects, effects)
        self.manager.job = "0"
        self.assertEqual(self.call(si.remove_scheduler).state, "removed")

    def test_preexisting_loaded_unit_is_not_adopted_or_stopped(self):
        self.manager.loaded[self.name + ".timer"] = b"foreign"
        self.manager.active = True
        self.manager.foreign_fragment = "/foreign/" + self.name + ".timer"
        result = self.call()
        self.assertEqual(result.reason, si.ActivationReason.FOREIGN)
        self.assertFalse(self.manager.effects)
        self.assertFalse((self.store.path / si.LEDGER).exists())

    def test_intent_crash_before_link_creation_recovers_and_wrong_hardlink_refuses(self):
        actual = si._systemd_step
        def fail(ledger, manager, step, fresh):
            actual(ledger, manager, step, fresh)
            if step == "enable":
                raise Crash()
        with mock.patch.object(si, "_systemd_step", side_effect=fail):
            with self.assertRaises(Crash):
                self.call()
        link = self.units / "timers.target.wants" / (self.name + ".timer")
        inode = link.lstat().st_ino
        self.install()
        self.assertEqual(link.lstat().st_ino, inode)
        path = self.units / (self.name + ".service")
        os.link(str(path), str(self.base / "foreign-hardlink"))
        self.assertEqual(self.call(si.remove_scheduler).reason, si.ActivationReason.UNSAFE)
        self.assertNotIn("stop", self.manager.effects)

    def test_removed_evidence_never_authorizes_deleting_reintroduced_assets(self):
        self.install("cron")
        block = si._read(self.store).assets[0].content.encode()
        self.assertEqual(self.call(si.remove_scheduler, "cron").state, "removed")
        self.manager.tab += block
        before = self.manager.tab
        self.assertEqual(self.call(si.remove_scheduler, "cron").reason, si.ActivationReason.FOREIGN)
        self.assertEqual(self.manager.tab, before)

    def test_ledger_rejects_unknown_steps_invalid_progress_and_symlinks(self):
        self.install("cron")
        path = self.store.path / si.LEDGER
        original = path.read_bytes()
        for changes in ({"intended": []}, {"completed": ["run-arbitrary"]}, {"pending": "start"},
                        {"phase": "active", "removed": ["remove-crontab"]}, {"cron_before_size": True}):
            value = json.loads(original)
            value.update(changes)
            path.write_text(json.dumps(value))
            self.assertEqual(self.call(si.inspect_activation, "cron").reason, si.ActivationReason.METADATA)
        path.unlink()
        path.symlink_to(self.root / ".env")
        self.assertFalse(self.call(si.remove_scheduler, "cron").active)
        self.assertEqual(self.manager.effects, ["crontab"])
        self.assertEqual((self.root / ".env").read_text(), "PRIVATE=keep\n")

    def test_changed_settings_backend_and_registration_not_adopted(self):
        self.install()
        self.assertEqual(self.call(backend="cron").reason, si.ActivationReason.CHANGED)
        self.policy = dataclasses.replace(self.policy, tick_timeout_seconds=16000)
        self.assertEqual(self.call(si.inspect_activation).reason, si.ActivationReason.CHANGED)
        # Exact removal uses retained installed bytes, not freshly rendered policy.
        self.assertEqual(self.call(si.remove_scheduler).state, "removed")

    def test_real_executable_crontab_adapter_with_private_snapshot_only(self):
        tab = self.base / "fake-crontab"
        log = self.base / "fake-calls"
        script = ("#!" + sys.executable + " -I\nimport os,sys\nfrom pathlib import Path\n"
                  + "tab=Path(" + repr(str(tab)) + ")\nlog=Path(" + repr(str(log)) + ")\n"
                  + "with log.open('a') as f: f.write(repr(sys.argv[1:])+'\\n')\n"
                  + "if sys.argv[1:]==['-l']:\n"
                  + " if tab.exists(): sys.stdout.buffer.write(tab.read_bytes())\n"
                  + " else: sys.stderr.write('no crontab for '+os.environ['USER']+'\\n'); sys.exit(1)\n"
                  + "elif sys.argv[1:]==['-']: tab.write_bytes(sys.stdin.buffer.read())\n"
                  + "else: sys.exit(99)\n")
        (self.root / "bin/crontab").write_text(script)
        # The service probe is ALSO a fixed fake executable, never host pgrep.
        (self.root / "bin/pgrep").write_text("#!/bin/sh\nprintf '987\\n'\n")
        self.host = dataclasses.replace(self.host, run=subprocess.run)
        # Real process death after a real (fake-manager) executable replaced the
        # private crontab, before the metadata acknowledgement. No scheduler on
        # the developer host is involved, including the daemon availability check.
        child = (
            "import os,sys,subprocess\nfrom pathlib import Path\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "from deploy.update.runner_state import RunnerStateStore\n"
            "from deploy.update.contracts import AutomationPolicy\n"
            "from deploy.update.scheduler_install import install_scheduler,_Host\n"
            "def run(args, **kw):\n"
            " result=subprocess.run(args, **kw)\n"
            " if Path(args[0]).name=='crontab' and args[1:]==['-']: os._exit(73)\n"
            " return result\n"
            "root=Path(sys.argv[2])\n"
            "install_scheduler(RunnerStateStore(root,root/'.env'),AutomationPolicy(),backend='cron',"
            "cron_service='cron',_host=_Host(platform='linux',run=run))\n"
        )
        result = subprocess.run([sys.executable, "-I", "-B", "-c", child, str(Path(__file__).resolve().parents[2]),
                                 str(self.root)], capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 73, result.stderr)
        self.assertEqual(si._read(self.store).pending, "crontab")
        self.install("cron", cron_service="cron")
        self.assertTrue(self.call(si.inspect_activation, "cron", cron_service="cron").active)
        self.assertEqual(log.read_text().count("['-']"), 1)
        self.assertEqual(self.call(si.remove_scheduler, "cron", cron_service="cron").state, "removed")
        self.assertEqual(tab.read_bytes(), b"")
        self.assertEqual(log.read_text().count("['-']"), 2)


if __name__ == "__main__":
    unittest.main()
