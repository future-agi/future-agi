"""S01 conservative configuration only: no daemon, timer or runtime adapters."""
from __future__ import annotations

import dataclasses
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from zoneinfo import ZoneInfoNotFoundError

from deploy.update.contracts import (AUTOMATIC_POLICY_BOUNDS, QUALIFICATION_POLICY, AutomationPolicy,
                                     ContractError, MaintenanceWindowSpec, POLICY_BOUNDS)
from deploy.update.settings import AUTOMATION_DEFAULTS, DEFAULTS, SettingsError, load_settings

PREFIX = "FUTURE_AGI_UPDATE_"
ROOT = Path(__file__).resolve().parents[2]
# Independent values for every NEW setting; legacy settings retain their matrix.
LAYERS = {
    "AUTOMATION_MODE": ("notify", "download", "apply"),
    "ENABLED": ("true", "false", "yes"),
    "INTERVAL_SECONDS": ("1000", "2000", "3000"),
    "MAX_BACKOFF_SECONDS": ("30000", "40000", "50000"),
    "TIMEZONE": ("Etc/UTC", "Etc/GMT", "UTC"),
    "MAINTENANCE_WINDOW": ("01:00-03:00", "22:00-04:00", "always"),
    "MAINTENANCE_DAYS": ("MON,WED", "FRI,SAT", "SUN"),
    "MIN_START_BUDGET_SECONDS": ("1000", "1100", "1200"),
    "BACKUP_MAX_AGE_SECONDS": ("1000", "2000", "3000"),
    "STAGE_MAX_AGE_SECONDS": ("1000", "2000", "3000"),
    "AUTOMATIC_ROLLBACK": ("true", "false", "yes"),
    "PROBE_TIMEOUT_SECONDS": ("10", "20", "30"),
    "PULL_TIMEOUT_SECONDS": ("1000", "1100", "1200"),
    "TICK_TIMEOUT_SECONDS": ("15000", "16000", "17000"),
    "QUALIFICATION_POLICY": ("", QUALIFICATION_POLICY, ""),
    "RESTORE_TIMEOUT_SECONDS": ("3500", "3600", "3700"),
    "RECOVERY_STARTUP_TIMEOUT_SECONDS": ("1200", "1300", "1400"),
    "STABILITY_PERIOD_SECONDS": ("30", "60", "90"),
    "QUIESCENCE_RESERVE_SECONDS": ("300", "310", "320"),
    "METADATA_RESERVE_SECONDS": ("300", "310", "320"),
    "ESTIMATE_SAFETY_FACTOR": ("2", "3", "4"),
    "MIGRATION_TIMEOUT_SECONDS": ("1200", "1300", "1400"),
    "MIGRATION_STALL_TIMEOUT_SECONDS": ("200", "300", "400"),
    "DISK_WATERMARK_RESERVE_BYTES": ("1073741824", "2147483648", "3221225472"),
}


class AutomationSettingsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.conf = self.root / "update.conf"

    def load(self, **values):
        return load_settings(self.root, environ={PREFIX + key: value for key, value in values.items()})

    def test_exact_manual_off_defaults(self):
        expected = {
            "AUTOMATION_MODE": "manual", "ENABLED": "false", "INTERVAL_SECONDS": "21600",
            "MAX_BACKOFF_SECONDS": "86400", "TIMEZONE": "UTC", "MAINTENANCE_WINDOW": "",
            "MAINTENANCE_DAYS": "MON,TUE,WED,THU,FRI,SAT,SUN", "MIN_START_BUDGET_SECONDS": "900",
            "BACKUP_MAX_AGE_SECONDS": "3600", "STAGE_MAX_AGE_SECONDS": "86400",
            "AUTOMATIC_ROLLBACK": "false", "PROBE_TIMEOUT_SECONDS": "60",
            "PULL_TIMEOUT_SECONDS": "1800", "TICK_TIMEOUT_SECONDS": "14400",
            "QUALIFICATION_POLICY": "", "RESTORE_TIMEOUT_SECONDS": "3600",
            "RECOVERY_STARTUP_TIMEOUT_SECONDS": "1200", "STABILITY_PERIOD_SECONDS": "60",
            "QUIESCENCE_RESERVE_SECONDS": "300", "METADATA_RESERVE_SECONDS": "300",
            "ESTIMATE_SAFETY_FACTOR": "2", "MIGRATION_TIMEOUT_SECONDS": "1200",
            "MIGRATION_STALL_TIMEOUT_SECONDS": "300", "DISK_WATERMARK_RESERVE_BYTES": "1073741824",
        }
        self.assertEqual(AUTOMATION_DEFAULTS, {PREFIX + key: value for key, value in expected.items()})
        loaded = self.load()
        self.assertEqual(loaded.automation_policy, AutomationPolicy())
        self.assertEqual((loaded.channel, loaded.policy), ("stable", "minor"))
        self.assertEqual(set(loaded.sources.values()), {"default"})
        self.assertIsNone(loaded.automation_policy.maintenance_window)
        self.assertNotIn(PREFIX + "MODE", DEFAULTS)

    def test_all_new_keys_and_all_precedence_combinations(self):
        self.assertEqual(set(AUTOMATION_DEFAULTS), {PREFIX + key for key in LAYERS})
        for suffix, (file, env, cli) in LAYERS.items():
            key = PREFIX + suffix
            for use_file, use_env, use_cli in ((True, False, False), (False, True, False),
                                              (False, False, True), (True, True, False),
                                              (True, False, True), (False, True, True), (True, True, True)):
                with self.subTest(key=key, layers=(use_file, use_env, use_cli)):
                    self.conf.write_text(f'{key}="{file}"\n' if use_file else "", encoding="utf-8")
                    loaded = load_settings(self.root, overrides={key: cli if use_cli else None},
                                           environ={key: env} if use_env else {})
                    self.assertEqual(loaded.get(key), cli if use_cli else env if use_env else file)
                    self.assertEqual(loaded.sources[key], "command line" if use_cli else "environment" if use_env else "update.conf")
                    self.assertTrue(all(value == "default" for name, value in loaded.sources.items() if name != key))

    def test_complete_policy_from_each_layer(self):
        for layer in range(3):
            values = {PREFIX + key: parts[layer] for key, parts in LAYERS.items()}
            self.conf.write_text("".join(f"{key}={parts[0]}\n" for key, parts in
                                        ((PREFIX + key, parts) for key, parts in LAYERS.items())), encoding="utf-8")
            loaded = load_settings(self.root, values if layer == 2 else None, values if layer > 0 else {})
            for key, value in values.items():
                self.assertEqual(loaded.get(key), value)
            self.assertIsInstance(loaded.automation_policy, AutomationPolicy)

    def test_invalid_shadowed_values_and_explicit_empty_window(self):
        self.conf.write_text(PREFIX + "AUTOMATION_MODE=typo\n" + PREFIX + "MAINTENANCE_WINDOW=always\n")
        loaded = load_settings(self.root, {PREFIX + "MAINTENANCE_WINDOW": ""}, {PREFIX + "AUTOMATION_MODE": "manual"})
        self.assertEqual(loaded.automation_policy.mode, "manual")
        self.assertIsNone(loaded.automation_policy.maintenance_window)
        self.assertEqual(self.load(AUTOMATION_MODE="notify").automation_policy.mode, "notify")

    def test_modes_are_exact_and_enable_does_not_choose_apply(self):
        for mode in ("manual", "notify", "download", "apply"):
            for enabled in ("true", "false"):
                policy = self.load(AUTOMATION_MODE=mode, ENABLED=enabled).automation_policy
                self.assertEqual(policy.mode, mode)
                self.assertEqual(policy.enabled, enabled == "true")
        for bad in ("", "Apply", "auto", "enabled", "off", " apply", "notify\n"):
            with self.subTest(bad=bad), self.assertRaises(SettingsError):
                self.load(AUTOMATION_MODE=bad)

    def test_boolean_conventions_preserved(self):
        for name in ("ENABLED", "AUTOMATIC_ROLLBACK"):
            for value in ("true", "1", "yes", "ON", " false ", "0", "no", "off", ""):
                self.load(**{name: value})
            for value in ("enabled", "disabled", "2", "t", "null", "auto"):
                with self.subTest(name=name, value=value), self.assertRaises(SettingsError):
                    self.load(**{name: value})

    def test_all_integer_boundaries_and_malformed_inputs(self):
        for field, (lower, upper) in POLICY_BOUNDS.items():
            name = "INTERVAL_SECONDS" if field == "check_interval_seconds" else field.upper()
            companion = {"INTERVAL_SECONDS": "300"} if name == "MAX_BACKOFF_SECONDS" else {}
            for value in (str(lower), str(upper), "0" * 5000 + str(lower)):
                with self.subTest(field=field, value=value[-20:]):
                    p = self.load(**dict(companion, **{name: value})).automation_policy
                    self.assertEqual(getattr(p, field), int(value.lstrip("0")))
            for bad in (str(lower - 1), str(upper + 1), "", "-1", "+300", "300.0", " 300", "300 ",
                        "300\n", "1e3", "١٢٣", "9" * 5000):
                with self.subTest(field=field, bad=bad[:15]), self.assertRaises(SettingsError):
                    self.load(**dict(companion, **{name: bad}))

    def test_restore_policy_budget_and_legacy_rollback_cannot_remove_reserve(self):
        for rollback in ('true', 'false'):
            policy = self.load(AUTOMATION_MODE='apply', QUALIFICATION_POLICY=QUALIFICATION_POLICY,
                               AUTOMATIC_ROLLBACK=rollback, TICK_TIMEOUT_SECONDS='13920').automation_policy
            self.assertEqual(policy.required_tick_budget_seconds, 13920)
            self.assertEqual(policy.required_recovery_budget_seconds, 5460)
            with self.assertRaisesRegex(SettingsError, 'TICK_TIMEOUT_SECONDS'):
                self.load(AUTOMATION_MODE='apply', QUALIFICATION_POLICY=QUALIFICATION_POLICY,
                          AUTOMATIC_ROLLBACK=rollback, TICK_TIMEOUT_SECONDS='13919')
        with self.assertRaisesRegex(SettingsError, 'MIGRATION_STALL_TIMEOUT_SECONDS'):
            self.load(QUALIFICATION_POLICY=QUALIFICATION_POLICY, MIGRATION_TIMEOUT_SECONDS='10')
        with self.assertRaises(SettingsError):
            self.load(QUALIFICATION_POLICY='unknown-policy')

    def test_all_automatic_integer_boundaries_are_strict(self):
        for field, (lower, upper) in AUTOMATIC_POLICY_BOUNDS.items():
            for value in (str(lower), str(upper)):
                self.assertEqual(getattr(self.load(**{field.upper(): value}).automation_policy, field), int(value))
            for bad in (str(lower - 1), str(upper + 1), '1.0', '1e9', '-1', 'true', '9' * 5000):
                with self.subTest(field=field, bad=bad[:20]), self.assertRaises(SettingsError):
                    self.load(**{field.upper(): bad})

    def test_backoff_cannot_be_less_than_interval(self):
        with self.assertRaisesRegex(SettingsError, "MAX_BACKOFF_SECONDS"):
            self.load(INTERVAL_SECONDS="1000", MAX_BACKOFF_SECONDS="999")
        self.load(INTERVAL_SECONDS="1000", MAX_BACKOFF_SECONDS="1000")

    def test_apply_budget_exact_thresholds_even_when_disabled(self):
        for rollback, threshold in (("false", 8400), ("true", 13200)):
            for enabled in ("false", "true"):
                values = dict(AUTOMATION_MODE="apply", ENABLED=enabled, AUTOMATIC_ROLLBACK=rollback)
                self.assertEqual(self.load(**dict(values, TICK_TIMEOUT_SECONDS=str(threshold))).automation_policy
                                 .required_tick_budget_seconds, threshold)
                with self.assertRaisesRegex(SettingsError, "TICK_TIMEOUT_SECONDS"):
                    self.load(**dict(values, TICK_TIMEOUT_SECONDS=str(threshold - 1)))
            self.load(**dict(values, TICK_TIMEOUT_SECONDS="14400"))
        # Manual/notify/download do not run apply or backup, so the cross-budget
        # condition must not make existing explicit CLI invocations unusable.
        for mode in ("manual", "notify", "download"):
            self.load(AUTOMATION_MODE=mode, TICK_TIMEOUT_SECONDS="900", BACKUP_TIMEOUT_SECONDS="86400")

    def test_custom_backup_and_wait_are_never_silently_shortened(self):
        with self.assertRaises(SettingsError):
            self.load(AUTOMATION_MODE="apply", BACKUP_TIMEOUT_SECONDS="86400", AUTOMATIC_ROLLBACK="true")
        policy = self.load(AUTOMATION_MODE="apply", BACKUP_TIMEOUT_SECONDS="86400", WAIT_TIMEOUT_SECONDS="7200",
                           PULL_TIMEOUT_SECONDS="7200", AUTOMATIC_ROLLBACK="true", TICK_TIMEOUT_SECONDS="172800").automation_policy
        self.assertEqual((policy.backup_timeout_seconds, policy.wait_timeout_seconds), (86400, 7200))

    def test_windows_and_iso_start_weekdays(self):
        self.assertEqual(self.load(MAINTENANCE_WINDOW="always").automation_policy.maintenance_window,
                         MaintenanceWindowSpec("always", "UTC"))
        policy = self.load(MAINTENANCE_WINDOW="23:30-02:15", MAINTENANCE_DAYS="SUN,MON,FRI",
                           TIMEZONE="America/New_York").automation_policy
        self.assertEqual(policy.maintenance_window,
                         MaintenanceWindowSpec("interval", "America/New_York", (7, 1, 5), "23:30", "02:15"))
        for bad in ("00:00-00:00", "24:00-01:00", "01:60-03:00", "1:00-03:00", "Always", " always",
                    "* * * * *", "01:00 - 02:00", "always\n"):
            with self.subTest(window=bad), self.assertRaises(SettingsError):
                self.load(MAINTENANCE_WINDOW=bad)
        for bad in ("", "MON,MON", "mon", "MON, TUE", "MON,", "1,2", "FUNDAY"):
            with self.subTest(days=bad), self.assertRaises(SettingsError):
                self.load(MAINTENANCE_DAYS=bad)

    def test_timezones_fail_closed_without_install_or_fallback(self):
        for bad in ("", "../UTC", "/etc/localtime", "UTC\n", "No/Such_Zone", "X" * 65):
            with self.subTest(zone=bad), self.assertRaisesRegex(SettingsError, "TIMEZONE"):
                self.load(TIMEZONE=bad)
        with mock.patch("deploy.update.contracts.ZoneInfo", side_effect=ZoneInfoNotFoundError):
            self.load(TIMEZONE="UTC")
            with self.assertRaisesRegex(SettingsError, "install system tzdata"):
                self.load(TIMEZONE="America/New_York")

    def test_unknown_config_and_explicit_keys_rejected_but_ambient_hook_ignored(self):
        for name in ("MODE", "AUTOMATON_MODE", "ENABELD"):
            self.conf.write_text(PREFIX + name + "=apply\n")
            with self.assertRaises(SettingsError):
                self.load()
            self.conf.unlink()
            with self.assertRaises(SettingsError):
                load_settings(self.root, {PREFIX + name: "apply"}, {})
            self.assertEqual(self.load(**{name: "apply"}).automation_policy, AutomationPolicy())

    def test_nonstring_inputs_are_settings_errors_not_type_errors(self):
        for key in AUTOMATION_DEFAULTS:
            for value in (True, 1, None, [], {}):
                with self.subTest(key=key, value=value), self.assertRaises(SettingsError):
                    load_settings(self.root, environ={key: value})

    def test_configuration_is_not_read_from_dotenv_or_durable_controls(self):
        (self.root / ".env").write_text(PREFIX + "ENABLED=true\n" + PREFIX + "AUTOMATION_MODE=apply\n")
        (self.root / ".futureagi-updates").mkdir()
        (self.root / ".futureagi-updates/control.json").write_text('{"enabled_override": true}')
        self.assertEqual(self.load().automation_policy, AutomationPolicy())

    def test_import_and_loading_do_not_invoke_commands_network_or_write(self):
        # Fresh isolated interpreter: guards apply BEFORE importing either module.
        script = '''
import pathlib, socket, subprocess, sys, urllib.request
root, settings_root = sys.argv[1:]
sys.path.insert(0, root)
def denied(*args, **kwargs):
    raise AssertionError("unexpected host action")
subprocess.Popen = denied
socket.socket = denied
urllib.request.urlopen = denied
from deploy.update.settings import load_settings
before = list(pathlib.Path(settings_root).iterdir())
for mode in ("manual", "notify", "download", "apply"):
    for enabled in ("false", "true"):
        settings = load_settings(pathlib.Path(settings_root), environ={
            "FUTURE_AGI_UPDATE_AUTOMATION_MODE": mode, "FUTURE_AGI_UPDATE_ENABLED": enabled})
        assert settings.automation_policy.mode == mode
assert before == list(pathlib.Path(settings_root).iterdir())
'''
        result = subprocess.run([sys.executable, "-I", "-c", script, str(ROOT), str(self.root)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_private_policy_is_immutable_and_validates_direct_construction(self):
        policy = self.load().automation_policy
        with self.assertRaises(dataclasses.FrozenInstanceError):
            policy.enabled = True
        for kwargs in ({"mode": "auto"}, {"enabled": 1}, {"check_interval_seconds": True},
                       {"automatic_rollback": "false"}, {"channel": "nightly"},
                       {"tick_timeout_seconds": 8399, "mode": "apply"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ContractError):
                AutomationPolicy(**kwargs)
        days = [1, 3]
        window = MaintenanceWindowSpec("interval", "UTC", days, "01:00", "02:00")
        days.append(5)
        self.assertEqual(window.days_of_week, (1, 3))
        with self.assertRaises(ContractError):
            AutomationPolicy(timezone="UTC", maintenance_window=MaintenanceWindowSpec("always", "Etc/UTC"))
        for days in ([True], [1, 1], [0], [8], [[]]):
            with self.assertRaises(ContractError):
                MaintenanceWindowSpec("interval", "UTC", days, "01:00", "02:00")


if __name__ == "__main__":
    unittest.main()
