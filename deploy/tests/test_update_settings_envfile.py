"""bin/update settings (FUTURE_AGI_UPDATE_* precedence, update.conf) and the
Compose dotenv helpers (read, render, atomic write). No Docker, no network.

The expected dotenv semantics are Docker Compose's (envfile.read_env promises
"values as Compose reads them"); the quirky cases below were checked against
`docker-compose config` v5.1.4.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple
from unittest import mock

from deploy.update import envfile
from deploy.update.envfile import EnvFileError, example_keys, read_env, render_changes, write_changes
from deploy.update.manifest import CHANNELS
from deploy.update.settings import (
    AUTOMATION_DEFAULTS,
    CONFIG_FILE,
    DEFAULT_COSIGN_IMAGE,
    DEFAULT_MANIFEST_URL,
    DEFAULT_TRUSTED_IDENTITY,
    DEFAULT_TRUSTED_ISSUER,
    DEFAULTS,
    POLICIES,
    Settings,
    SettingsError,
    load_settings,
    read_config_file,
)

ROOT = Path(__file__).resolve().parents[2]
IS_SUPERUSER = hasattr(os, "geteuid") and os.geteuid() == 0

POLICY = "FUTURE_AGI_UPDATE_POLICY"
CHANNEL = "FUTURE_AGI_UPDATE_CHANNEL"
MANIFEST_URL = "FUTURE_AGI_UPDATE_MANIFEST_URL"
BACKUP_COMMAND = "FUTURE_AGI_UPDATE_BACKUP_COMMAND"
BACKUP_TIMEOUT = "FUTURE_AGI_UPDATE_BACKUP_TIMEOUT_SECONDS"
WAIT_TIMEOUT = "FUTURE_AGI_UPDATE_WAIT_TIMEOUT_SECONDS"
IDENTITY = "FUTURE_AGI_UPDATE_TRUSTED_IDENTITY"
ISSUER = "FUTURE_AGI_UPDATE_TRUSTED_ISSUER"
PUBLIC_KEY = "FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY"
OFFLINE = "FUTURE_AGI_UPDATE_COSIGN_OFFLINE"
COSIGN_IMAGE = "FUTURE_AGI_UPDATE_COSIGN_IMAGE"

# A valid value per key for each layer: update.conf, environment, command line.
# Each layer's value differs from the layer below it, so a winner is visible.
LAYER_VALUES: Dict[str, Tuple[str, str, str]] = {
    MANIFEST_URL: ("/srv/futureagi/conf-manifest.json", "https://env.example/m.json", "https://cli.example/m.json"),
    CHANNEL: ("beta", "stable", "beta"),
    POLICY: ("patch", "major", "patch"),
    BACKUP_COMMAND: ("/usr/local/bin/conf-backup", "/usr/local/bin/env-backup", "/usr/local/bin/cli-backup"),
    BACKUP_TIMEOUT: ("10", "20", "30"),
    WAIT_TIMEOUT: ("100", "200", "300"),
    IDENTITY: ("conf-identity@{version}", "env-identity@{version}", "cli-identity@{version}"),
    ISSUER: ("https://conf.issuer.example", "https://env.issuer.example", "https://cli.issuer.example"),
    PUBLIC_KEY: ("/keys/conf.pub", "/keys/env.pub", "/keys/cli.pub"),
    OFFLINE: ("true", "yes", "on"),
    COSIGN_IMAGE: ("registry.example/conf/cosign:1", "registry.example/env/cosign:1", "registry.example/cli/cosign:1"),
}

SOURCES = ("update.conf", "environment", "command line")


# ============================================================== settings ==


class _SettingsCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.conf_path = self.root / CONFIG_FILE

    def write_conf(self, text: str) -> Path:
        self.conf_path.write_bytes(text.encode("utf-8"))
        return self.conf_path

    def load(self, overrides: Optional[Mapping[str, Optional[str]]] = None,
             environ: Optional[Mapping[str, str]] = None) -> Settings:
        return load_settings(self.root, overrides, {} if environ is None else environ)

    def rejection(self, overrides: Optional[Mapping[str, Optional[str]]] = None,
                  environ: Optional[Mapping[str, str]] = None) -> str:
        with self.assertRaises(SettingsError) as caught:
            self.load(overrides, environ)
        return str(caught.exception)

    def conf_rejection(self, text: str) -> str:
        path = self.write_conf(text)
        with self.assertRaises(SettingsError) as caught:
            read_config_file(path)
        return str(caught.exception)

    def via(self, source: str, key: str, value: str):
        """(overrides, environ) that make `value` reach `key` from `source`."""

        if self.conf_path.exists():
            self.conf_path.unlink()
        if source == "command line":
            return {key: value}, {}
        if source == "environment":
            return None, {key: value}
        assert source == "update.conf"
        assert "\n" not in value and '"' not in value
        self.write_conf(f'{key}="{value}"\n')
        return None, {}


class UpdateConfParsingTests(_SettingsCase):
    def test_missing_file_is_empty(self) -> None:
        self.assertEqual(read_config_file(self.root / "absent.conf"), {})

    def test_missing_file_gives_defaults(self) -> None:
        settings = self.load()
        for key, default in DEFAULTS.items():
            self.assertEqual(settings.get(key), default)

    def test_key_value_lines(self) -> None:
        path = self.write_conf(f"{POLICY}=major\n{CHANNEL}=beta\n")
        self.assertEqual(read_config_file(path), {POLICY: "major", CHANNEL: "beta"})

    def test_comments_and_blank_lines_are_skipped(self) -> None:
        path = self.write_conf(
            "# update.conf\n"
            "\n"
            "   \n"
            "\t\n"
            "   # indented comment\n"
            f"#{POLICY}=patch\n"
            f"# {CHANNEL}=nonsense\n"
            f"{POLICY}=major\n"
            "# trailing comment"
        )
        self.assertEqual(read_config_file(path), {POLICY: "major"})

    def test_commented_out_garbage_is_not_validated(self) -> None:
        path = self.write_conf("# PG_PASSWORD=x\n# not a key=value line at all\n#=\n")
        self.assertEqual(read_config_file(path), {})

    def test_double_quotes_are_removed(self) -> None:
        path = self.write_conf(f'{BACKUP_COMMAND}="/usr/local/bin/backup --to /mnt/backups"\n')
        self.assertEqual(read_config_file(path)[BACKUP_COMMAND], "/usr/local/bin/backup --to /mnt/backups")

    def test_single_quotes_are_removed(self) -> None:
        path = self.write_conf(f"{BACKUP_COMMAND}='/usr/local/bin/backup --to /mnt/backups'\n")
        self.assertEqual(read_config_file(path)[BACKUP_COMMAND], "/usr/local/bin/backup --to /mnt/backups")

    def test_inner_quotes_survive_outer_quote_removal(self) -> None:
        path = self.write_conf(f"{BACKUP_COMMAND}=\"/bin/sh -c 'pg_dump > x'\"\n")
        self.assertEqual(read_config_file(path)[BACKUP_COMMAND], "/bin/sh -c 'pg_dump > x'")

    def test_spaces_inside_quotes_are_kept(self) -> None:
        path = self.write_conf(f'{BACKUP_COMMAND}="  padded  "\n')
        self.assertEqual(read_config_file(path)[BACKUP_COMMAND], "  padded  ")

    def test_empty_quotes_give_empty_value(self) -> None:
        for quoted in ('""', "''"):
            with self.subTest(quoted=quoted):
                path = self.write_conf(f"{BACKUP_COMMAND}={quoted}\n")
                self.assertEqual(read_config_file(path)[BACKUP_COMMAND], "")

    def test_unbalanced_or_mismatched_quotes_are_kept(self) -> None:
        for raw in ('"minor', "minor'", "\"minor'", "'minor\"", '"', "'"):
            with self.subTest(raw=raw):
                path = self.write_conf(f"{BACKUP_COMMAND}={raw}\n")
                self.assertEqual(read_config_file(path)[BACKUP_COMMAND], raw)

    def test_empty_value(self) -> None:
        path = self.write_conf(f"{BACKUP_COMMAND}=\n")
        self.assertEqual(read_config_file(path), {BACKUP_COMMAND: ""})

    def test_whitespace_around_key_equals_and_value_is_stripped(self) -> None:
        path = self.write_conf(f"   {POLICY}   =   major   \n\t{CHANNEL}\t=\tbeta\t\n")
        self.assertEqual(read_config_file(path), {POLICY: "major", CHANNEL: "beta"})

    def test_value_may_contain_equals(self) -> None:
        path = self.write_conf(f"{BACKUP_COMMAND}=/bin/backup --target=s3://bucket/a=b\n")
        self.assertEqual(read_config_file(path)[BACKUP_COMMAND], "/bin/backup --target=s3://bucket/a=b")

    def test_last_definition_wins(self) -> None:
        path = self.write_conf(f"{POLICY}=patch\n{POLICY}=major\n{POLICY}=minor\n")
        self.assertEqual(read_config_file(path), {POLICY: "minor"})

    def test_crlf_line_endings(self) -> None:
        path = self.write_conf(f"# conf\r\n{POLICY}=major\r\n\r\n{CHANNEL}='beta'\r\n")
        self.assertEqual(read_config_file(path), {POLICY: "major", CHANNEL: "beta"})

    def test_every_known_key_is_accepted(self) -> None:
        text = "".join(f"{key}={values[0]}\n" for key, values in LAYER_VALUES.items())
        path = self.write_conf(text)
        self.assertEqual(read_config_file(path), {key: values[0] for key, values in LAYER_VALUES.items()})

    def test_non_updater_key_is_rejected_with_line_number(self) -> None:
        message = self.conf_rejection(f"# header\n{POLICY}=minor\n\nPG_PASSWORD=hunter2-secret\n")
        self.assertIn(f"{self.conf_path}:4:", message)
        self.assertIn("PG_PASSWORD", message)
        self.assertIn("FUTURE_AGI_UPDATE_", message)
        self.assertNotIn("hunter2-secret", message)

    def test_app_settings_that_belong_in_env_are_rejected(self) -> None:
        for key in ("COMPOSE_FILE", "FUTURE_AGI_VERSION", "SECRET_KEY", "INSTALL_READY_TIMEOUT"):
            with self.subTest(key=key):
                message = self.conf_rejection(f"{key}=value\n")
                self.assertIn(f"{self.conf_path}:1:", message)
                self.assertIn(f"{key} is not an updater setting", message)

    def test_unknown_updater_key_is_rejected_with_line_number(self) -> None:
        message = self.conf_rejection(f"{POLICY}=minor\nFUTURE_AGI_UPDATE_POLCY=major\n")
        self.assertIn(f"{self.conf_path}:2:", message)
        self.assertIn("unknown setting FUTURE_AGI_UPDATE_POLCY", message)

    def test_bare_prefix_is_an_unknown_setting(self) -> None:
        message = self.conf_rejection("FUTURE_AGI_UPDATE_=x\n")
        self.assertIn(":1:", message)
        self.assertIn("unknown setting", message)

    def test_line_without_equals_is_rejected_with_line_number(self) -> None:
        message = self.conf_rejection(f"# one\n\n{POLICY}=minor\n   # four\nghp_pastedTokenWithoutKey\n")
        self.assertIn(f"{self.conf_path}:5: expected KEY=value", message)
        self.assertNotIn("ghp_pastedTokenWithoutKey", message)

    def test_line_numbers_count_crlf_lines(self) -> None:
        message = self.conf_rejection(f"# a\r\n\r\n{POLICY}=minor\r\nbroken\r\n")
        self.assertIn(":4:", message)

    def test_lowercase_key_is_invalid(self) -> None:
        message = self.conf_rejection("future_agi_update_policy=minor\n")
        self.assertIn(":1: invalid key 'future_agi_update_policy'", message)

    def test_malformed_keys_are_invalid(self) -> None:
        for key in ("", "1FUTURE", "FUTURE-AGI", "FUTURE AGI", "FUTURE_AGI_UPDATE_POLICY.x", f"export {POLICY}"):
            with self.subTest(key=key):
                message = self.conf_rejection(f"{key}=secret-value-123\n")
                self.assertIn(":1: invalid key", message)
                self.assertIn(repr(key), message)
                self.assertNotIn("secret-value-123", message)

    def test_error_names_the_file(self) -> None:
        message = self.conf_rejection("nope\n")
        self.assertIn(str(self.conf_path), message)

    def test_conf_errors_surface_through_load_settings(self) -> None:
        self.write_conf("NOT_OURS=1\n")
        with self.assertRaises(SettingsError) as caught:
            self.load(environ={POLICY: "minor"})
        self.assertIn("NOT_OURS is not an updater setting", str(caught.exception))

    def test_conf_is_validated_even_when_every_key_is_overridden(self) -> None:
        self.write_conf("FUTURE_AGI_UPDATE_TYPO=1\n")
        overrides = {key: values[2] for key, values in LAYER_VALUES.items()}
        with self.assertRaises(SettingsError):
            self.load(overrides)

    def test_directory_in_place_of_conf_is_reported(self) -> None:
        self.conf_path.mkdir()
        with self.assertRaises(SettingsError) as caught:
            self.load()
        self.assertIn("cannot read", str(caught.exception))
        self.assertIn(str(self.conf_path), str(caught.exception))

    @unittest.skipIf(IS_SUPERUSER, "root can read a 0000 file")
    def test_unreadable_conf_is_reported(self) -> None:
        self.write_conf(f"{POLICY}=major\n")
        os.chmod(self.conf_path, 0)
        self.addCleanup(os.chmod, self.conf_path, 0o600)
        with self.assertRaises(SettingsError) as caught:
            read_config_file(self.conf_path)
        self.assertIn("cannot read", str(caught.exception))

    def test_non_utf8_conf_is_a_settings_error(self) -> None:
        # bin/update only turns SettingsError into a clean exit 2; anything
        # else becomes a traceback.
        self.conf_path.write_bytes(f"{BACKUP_COMMAND}=/bin/b\xe4ckup\n".encode("latin-1"))
        with self.assertRaises(SettingsError) as caught:
            read_config_file(self.conf_path)
        self.assertIn(str(self.conf_path), str(caught.exception))

    def test_utf8_values_are_read(self) -> None:
        path = self.write_conf(f"{BACKUP_COMMAND}=/opt/sauvegarde/réserve\n")
        self.assertEqual(read_config_file(path)[BACKUP_COMMAND], "/opt/sauvegarde/réserve")


class SettingsPrecedenceTests(_SettingsCase):
    def test_layer_table_covers_every_setting(self) -> None:
        # New host automation settings have their own exhaustive layer table
        # in test_update_automation_settings; retain this legacy matrix intact.
        self.assertEqual(set(LAYER_VALUES), set(DEFAULTS) - set(AUTOMATION_DEFAULTS))

    def test_defaults_when_nothing_is_set(self) -> None:
        settings = self.load()
        for key, default in DEFAULTS.items():
            with self.subTest(key=key):
                self.assertEqual(settings.get(key), default)
                self.assertEqual(settings.sources[key], "default")

    def test_update_conf_beats_default_for_every_key(self) -> None:
        self.write_conf("".join(f"{key}={values[0]}\n" for key, values in LAYER_VALUES.items()))
        settings = self.load()
        for key, values in LAYER_VALUES.items():
            with self.subTest(key=key):
                self.assertEqual(settings.get(key), values[0])
                self.assertEqual(settings.sources[key], CONFIG_FILE)

    def test_environment_beats_update_conf_for_every_key(self) -> None:
        self.write_conf("".join(f"{key}={values[0]}\n" for key, values in LAYER_VALUES.items()))
        settings = self.load(environ={key: values[1] for key, values in LAYER_VALUES.items()})
        for key, values in LAYER_VALUES.items():
            with self.subTest(key=key):
                self.assertEqual(settings.get(key), values[1])
                self.assertEqual(settings.sources[key], "environment")

    def test_command_line_beats_environment_for_every_key(self) -> None:
        self.write_conf("".join(f"{key}={values[0]}\n" for key, values in LAYER_VALUES.items()))
        settings = self.load(
            overrides={key: values[2] for key, values in LAYER_VALUES.items()},
            environ={key: values[1] for key, values in LAYER_VALUES.items()},
        )
        for key, values in LAYER_VALUES.items():
            with self.subTest(key=key):
                self.assertEqual(settings.get(key), values[2])
                self.assertEqual(settings.sources[key], "command line")

    def test_each_key_layers_independently(self) -> None:
        for key, (conf, env, cli) in LAYER_VALUES.items():
            for layers, expected, source in (
                (("conf",), conf, CONFIG_FILE),
                (("env",), env, "environment"),
                (("cli",), cli, "command line"),
                (("conf", "env"), env, "environment"),
                (("conf", "cli"), cli, "command line"),
                (("env", "cli"), cli, "command line"),
                (("conf", "env", "cli"), cli, "command line"),
            ):
                with self.subTest(key=key, layers=layers):
                    if self.conf_path.exists():
                        self.conf_path.unlink()
                    if "conf" in layers:
                        self.write_conf(f"{key}={conf}\n")
                    environ = {key: env} if "env" in layers else {}
                    # Round 4 (S20): an offline verification needs a public key.
                    companion = PUBLIC_KEY if key == OFFLINE else None
                    if companion:
                        environ[companion] = "/keys/offline.pub"
                    settings = self.load(
                        overrides={key: cli} if "cli" in layers else None,
                        environ=environ,
                    )
                    self.assertEqual(settings.get(key), expected)
                    self.assertEqual(settings.sources[key], source)
                    for other in DEFAULTS:
                        if other not in (key, companion):
                            self.assertEqual(settings.sources[other], "default")
                            self.assertEqual(settings.get(other), DEFAULTS[other])

    def test_mixed_sources_are_reported_per_key(self) -> None:
        self.write_conf(f"{POLICY}=patch\n{CHANNEL}=beta\n")
        settings = self.load(overrides={BACKUP_COMMAND: "/bin/b"}, environ={CHANNEL: "stable", WAIT_TIMEOUT: "90"})
        self.assertEqual(settings.sources[POLICY], CONFIG_FILE)
        self.assertEqual(settings.sources[CHANNEL], "environment")
        self.assertEqual(settings.sources[WAIT_TIMEOUT], "environment")
        self.assertEqual(settings.sources[BACKUP_COMMAND], "command line")
        self.assertEqual(settings.sources[ISSUER], "default")
        self.assertEqual(set(settings.sources), set(DEFAULTS))

    def test_none_override_falls_through(self) -> None:
        # __main__ passes None for every flag the user did not give.
        self.write_conf(f"{POLICY}=patch\n")
        settings = self.load(
            overrides={POLICY: None, CHANNEL: None, BACKUP_COMMAND: None},
            environ={CHANNEL: "beta"},
        )
        self.assertEqual((settings.policy, settings.sources[POLICY]), ("patch", CONFIG_FILE))
        self.assertEqual((settings.channel, settings.sources[CHANNEL]), ("beta", "environment"))
        self.assertEqual((settings.get(BACKUP_COMMAND), settings.sources[BACKUP_COMMAND]), ("", "default"))

    def test_empty_command_line_value_still_wins(self) -> None:
        self.write_conf(f"{BACKUP_COMMAND}=/bin/conf-backup\n")
        settings = self.load(overrides={BACKUP_COMMAND: ""}, environ={BACKUP_COMMAND: "/bin/env-backup"})
        self.assertEqual(settings.get(BACKUP_COMMAND), "")
        self.assertEqual(settings.sources[BACKUP_COMMAND], "command line")

    def test_empty_environment_value_still_wins(self) -> None:
        self.write_conf(f"{BACKUP_COMMAND}=/bin/conf-backup\n")
        settings = self.load(environ={BACKUP_COMMAND: ""})
        self.assertEqual(settings.get(BACKUP_COMMAND), "")
        self.assertEqual(settings.sources[BACKUP_COMMAND], "environment")

    def test_process_environment_is_used_by_default(self) -> None:
        with mock.patch.dict(os.environ, {POLICY: "major", CHANNEL: "beta"}):
            settings = load_settings(self.root)
        self.assertEqual((settings.policy, settings.sources[POLICY]), ("major", "environment"))
        self.assertEqual((settings.channel, settings.sources[CHANNEL]), ("beta", "environment"))

    def test_explicit_empty_environ_ignores_process_environment(self) -> None:
        with mock.patch.dict(os.environ, {POLICY: "major"}):
            settings = load_settings(self.root, None, {})
        self.assertEqual((settings.policy, settings.sources[POLICY]), ("minor", "default"))

    def test_settings_never_come_from_env_files(self) -> None:
        (self.root / ".env").write_text(f"{POLICY}=major\n{CHANNEL}=nightly\n{WAIT_TIMEOUT}=1\n", encoding="utf-8")
        (self.root / "deploy").mkdir()
        (self.root / "deploy" / ".env.production").write_text(f"{POLICY}=patch\n", encoding="utf-8")
        settings = self.load()
        for key, default in DEFAULTS.items():
            self.assertEqual(settings.get(key), default)
            self.assertEqual(settings.sources[key], "default")

    def test_update_conf_is_read_from_the_root_only(self) -> None:
        (self.root / "deploy").mkdir()
        (self.root / "deploy" / CONFIG_FILE).write_text(f"{POLICY}=major\n", encoding="utf-8")
        self.assertEqual(self.load().sources[POLICY], "default")

    def test_unrelated_environment_variables_are_ignored(self) -> None:
        settings = self.load(environ={
            "PATH": "/usr/bin", "FUTURE_AGI_UPDATE_TYPO": "x", "future_agi_update_policy": "yolo",
            "COMPOSE_FILE": "x.yml",
        })
        self.assertEqual(set(settings.sources.values()), {"default"})

    def test_invalid_lower_layer_is_irrelevant_once_overridden(self) -> None:
        cases = {
            POLICY: ("yolo", "major"),
            CHANNEL: ("nightly", "beta"),
            BACKUP_TIMEOUT: ("0", "5"),
            WAIT_TIMEOUT: ("forever", "61"),
            OFFLINE: ("maybe", "no"),
            MANIFEST_URL: ("http://insecure.example/m.json", "https://secure.example/m.json"),
            IDENTITY: ("", "someone"),
        }
        for key, (bad, good) in cases.items():
            with self.subTest(key=key, layer="environment over update.conf"):
                self.write_conf(f'{key}="{bad}"\n')
                self.assertEqual(self.load(environ={key: good}).get(key), good)
            with self.subTest(key=key, layer="command line over environment"):
                self.conf_path.unlink()
                self.assertEqual(self.load(overrides={key: good}, environ={key: bad}).get(key), good)

    def test_settings_get_rejects_unknown_key(self) -> None:
        with self.assertRaises(KeyError):
            self.load().get("FUTURE_AGI_UPDATE_NOPE")


class SettingsValidationTests(_SettingsCase):
    # ---------------------------------------------------------- policy --
    def test_every_policy_is_accepted(self) -> None:
        self.assertEqual(POLICIES, ("patch", "minor", "major"))
        for policy in POLICIES:
            for source in SOURCES:
                with self.subTest(policy=policy, source=source):
                    self.assertEqual(self.load(*self.via(source, POLICY, policy)).policy, policy)

    def test_bad_policy_names_key_value_choices_and_source(self) -> None:
        for source in SOURCES:
            with self.subTest(source=source):
                message = self.rejection(*self.via(source, POLICY, "yolo"))
                self.assertIn(POLICY, message)
                self.assertIn("'yolo'", message)
                self.assertIn("patch, minor, major", message)
                self.assertIn(f"(from {source})", message)

    def test_policy_is_exact(self) -> None:
        for bad in ("Minor", "MAJOR", " minor", "minor ", "", "minor\n", "any", "none", "patch,minor"):
            with self.subTest(value=bad):
                message = self.rejection(environ={POLICY: bad})
                self.assertIn(POLICY, message)
                self.assertIn(repr(bad), message)

    # --------------------------------------------------------- channel --
    def test_every_channel_is_accepted(self) -> None:
        self.assertEqual(CHANNELS, ("stable", "beta"))
        for channel in CHANNELS:
            for source in SOURCES:
                with self.subTest(channel=channel, source=source):
                    self.assertEqual(self.load(*self.via(source, CHANNEL, channel)).channel, channel)

    def test_bad_channel_names_key_value_choices_and_source(self) -> None:
        for source in SOURCES:
            with self.subTest(source=source):
                message = self.rejection(*self.via(source, CHANNEL, "nightly"))
                self.assertIn(CHANNEL, message)
                self.assertIn("'nightly'", message)
                self.assertIn("stable, beta", message)
                self.assertIn(f"(from {source})", message)

    def test_channel_is_exact(self) -> None:
        for bad in ("Stable", "BETA", " stable", "stable ", "", "latest", "dev"):
            with self.subTest(value=bad):
                self.assertIn(CHANNEL, self.rejection(environ={CHANNEL: bad}))

    # -------------------------------------------------------- integers --
    def test_backup_timeout_bounds(self) -> None:
        for good in ("1", "2", "3600", "86399", "86400"):
            with self.subTest(value=good):
                settings = self.load(environ={BACKUP_TIMEOUT: good})
                self.assertEqual(settings.integer(BACKUP_TIMEOUT, 1, 86400), int(good))
        for bad, shown in (("0", "0"), ("86401", "86401"), ("999999999999", "999999999999")):
            with self.subTest(value=bad):
                message = self.rejection(environ={BACKUP_TIMEOUT: bad})
                self.assertIn(BACKUP_TIMEOUT, message)
                self.assertIn("between 1 and 86400", message)
                self.assertIn(shown, message)

    def test_wait_timeout_bounds(self) -> None:
        for good in ("60", "61", "1200", "7199", "7200"):
            with self.subTest(value=good):
                settings = self.load(environ={WAIT_TIMEOUT: good})
                self.assertEqual(settings.integer(WAIT_TIMEOUT, 60, 7200), int(good))
        for bad in ("0", "1", "59", "7201", "86400"):
            with self.subTest(value=bad):
                message = self.rejection(environ={WAIT_TIMEOUT: bad})
                self.assertIn(WAIT_TIMEOUT, message)
                self.assertIn("between 60 and 7200", message)

    def test_integers_must_be_plain_ascii_digits(self) -> None:
        for key in (BACKUP_TIMEOUT, WAIT_TIMEOUT):
            for bad in ("", " ", "-1", "+120", "120.0", "1.5", "120s", " 120", "120 ", "120\n", "1e3", "0x78",
                        "\u0661\u0662\u0660", "\uff11\uff12\uff10", "1_200", "twenty"):
                with self.subTest(key=key, value=bad):
                    message = self.rejection(environ={key: bad})
                    self.assertIn(key, message)
                    self.assertIn("whole number", message)
                    self.assertIn(repr(bad), message)

    def test_integers_from_update_conf_are_validated(self) -> None:
        self.write_conf(f"{WAIT_TIMEOUT}=30\n")
        self.assertIn(WAIT_TIMEOUT, self.rejection())

    def test_integer_errors_name_the_source(self) -> None:
        for key, bad in ((BACKUP_TIMEOUT, "0"), (BACKUP_TIMEOUT, "soon"), (WAIT_TIMEOUT, "7201"),
                         (WAIT_TIMEOUT, "1.5")):
            for source in SOURCES:
                with self.subTest(key=key, value=bad, source=source):
                    message = self.rejection(*self.via(source, key, bad))
                    self.assertIn(key, message)
                    self.assertIn(source, message)

    # -------------------------------------------------------- booleans --
    def test_boolean_spellings_are_accepted(self) -> None:
        for value in ("1", "true", "TRUE", "True", "yes", "YES", "on", "On", " true ", "0", "false", "FALSE",
                      "no", "No", "off", "OFF", "", "  "):
            with self.subTest(value=value):
                # Round 4 (S20): a true OFFLINE needs a public key.
                self.load(environ={OFFLINE: value, PUBLIC_KEY: "/keys/cosign.pub"})

    def test_bad_boolean_names_key_and_value(self) -> None:
        for bad in ("maybe", "2", "-1", "t", "f", "y", "n", "truee", "enabled", "true false", "none", "null"):
            with self.subTest(value=bad):
                message = self.rejection(environ={OFFLINE: bad})
                self.assertIn(OFFLINE, message)
                self.assertIn("true or false", message)
                self.assertIn(repr(bad), message)

    def test_boolean_errors_name_the_source(self) -> None:
        for source in SOURCES:
            with self.subTest(source=source):
                message = self.rejection(*self.via(source, OFFLINE, "maybe"))
                self.assertIn(OFFLINE, message)
                self.assertIn(source, message)

    # ---------------------------------------------------- manifest URL --
    def test_https_urls_and_local_paths_are_accepted(self) -> None:
        for value in ("https://example.com/m.json", "https://", "https://user@host:8443/a/b?c=d",
                      "/srv/manifests/m.json", "manifests/m.json", "./m.json", "~/m.json", "m.json",
                      "C:\\manifests\\m.json", ""):
            for source in SOURCES:
                with self.subTest(value=value, source=source):
                    self.assertEqual(self.load(*self.via(source, MANIFEST_URL, value)).get(MANIFEST_URL), value)

    def test_non_https_schemes_are_rejected(self) -> None:
        for bad in ("http://example.com/m.json", "HTTP://example.com/m.json", "HTTPS://example.com/m.json",
                    "ftp://example.com/m.json", "file:///srv/m.json", "s3://bucket/m.json",
                    "git+https://example.com/m.json", " https://example.com/m.json", "hxxps://x",
                    "://example.com/m.json"):
            with self.subTest(value=bad):
                message = self.rejection(environ={MANIFEST_URL: bad})
                self.assertIn(MANIFEST_URL, message)
                self.assertIn("https://", message)

    def test_manifest_url_errors_name_the_source(self) -> None:
        for source in SOURCES:
            with self.subTest(source=source):
                message = self.rejection(*self.via(source, MANIFEST_URL, "http://example.com/m.json"))
                self.assertIn(MANIFEST_URL, message)
                self.assertIn(source, message)

    # -------------------------------------------------------- identity --
    def test_empty_identity_is_rejected(self) -> None:
        for bad in ("", " ", "   ", "\t", "\n", " \t\r\n "):
            with self.subTest(value=bad):
                self.assertIn(IDENTITY, self.rejection(environ={IDENTITY: bad}))

    def test_quoted_blank_identity_in_conf_is_rejected(self) -> None:
        for raw in ('""', "''", '"   "', "", "   "):
            with self.subTest(raw=raw):
                self.write_conf(f"{IDENTITY}={raw}\n")
                self.assertIn(IDENTITY, self.rejection())

    def test_identity_errors_name_the_source(self) -> None:
        for source in SOURCES:
            with self.subTest(source=source):
                message = self.rejection(*self.via(source, IDENTITY, "  "))
                self.assertIn(IDENTITY, message)
                self.assertIn(source, message)

    def test_any_non_blank_identity_is_accepted(self) -> None:
        for value in ("x", "https://github.com/fork/repo/.github/workflows/w.yml@refs/tags/{version}", " padded "):
            with self.subTest(value=value):
                self.assertEqual(self.load(overrides={IDENTITY: value}).get(IDENTITY), value)

    # ----------------------------------------------------------- other --
    def test_unvalidated_free_text_keys_accept_anything(self) -> None:
        for key in (BACKUP_COMMAND, ISSUER, PUBLIC_KEY, COSIGN_IMAGE):
            with self.subTest(key=key):
                self.assertEqual(self.load(environ={key: "any thing; $(x)"}).get(key), "any thing; $(x)")

    def test_settings_error_is_a_value_error(self) -> None:
        self.assertTrue(issubclass(SettingsError, ValueError))


class SettingsObjectTests(unittest.TestCase):
    def make(self, **values: str) -> Settings:
        merged = dict(DEFAULTS)
        merged.update(values)
        return Settings(merged, {key: "test" for key in merged})

    def test_integer_returns_int(self) -> None:
        settings = self.make(**{WAIT_TIMEOUT: "0090"})
        self.assertEqual(settings.integer(WAIT_TIMEOUT, 60, 7200), 90)
        self.assertIsInstance(settings.integer(WAIT_TIMEOUT, 60, 7200), int)

    def test_integer_bounds_are_inclusive(self) -> None:
        settings = self.make(**{BACKUP_TIMEOUT: "10"})
        self.assertEqual(settings.integer(BACKUP_TIMEOUT, 10, 10), 10)
        with self.assertRaises(SettingsError):
            settings.integer(BACKUP_TIMEOUT, 11, 20)
        with self.assertRaises(SettingsError):
            settings.integer(BACKUP_TIMEOUT, 1, 9)

    def test_boolean_truthy(self) -> None:
        for value in ("1", "true", "TrUe", "yes", "on", "  ON  ", "\tyes\n"):
            with self.subTest(value=value):
                self.assertIs(self.make(**{OFFLINE: value}).boolean(OFFLINE), True)

    def test_boolean_falsy(self) -> None:
        for value in ("", "0", "false", "FALSE", "no", "off", "  off  ", " "):
            with self.subTest(value=value):
                self.assertIs(self.make(**{OFFLINE: value}).boolean(OFFLINE), False)

    def test_boolean_error_shows_the_raw_value(self) -> None:
        with self.assertRaises(SettingsError) as caught:
            self.make(**{OFFLINE: " Maybe "}).boolean(OFFLINE)
        self.assertIn("' Maybe '", str(caught.exception))

    def test_policy_and_channel_properties(self) -> None:
        settings = self.make(**{POLICY: "major", CHANNEL: "beta"})
        self.assertEqual((settings.policy, settings.channel), ("major", "beta"))

    def test_sources_are_copied(self) -> None:
        sources = {key: "default" for key in DEFAULTS}
        settings = Settings(dict(DEFAULTS), sources)
        sources[POLICY] = "mutated"
        self.assertEqual(settings.sources[POLICY], "default")


class SettingsDefaultsTests(unittest.TestCase):
    def test_every_key_is_an_updater_key(self) -> None:
        for key in DEFAULTS:
            self.assertTrue(re.fullmatch(r"FUTURE_AGI_UPDATE_[A-Z0-9_]+", key), key)

    def test_defaults_pass_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = load_settings(Path(directory), None, {})
        self.assertEqual(settings.policy, "minor")
        self.assertEqual(settings.channel, "stable")
        self.assertEqual(settings.get(BACKUP_COMMAND), "")
        self.assertEqual(settings.integer(BACKUP_TIMEOUT, 1, 86400), 3600)
        self.assertEqual(settings.integer(WAIT_TIMEOUT, 60, 7200), 1200)
        self.assertIs(settings.boolean(OFFLINE), False)
        self.assertEqual(settings.get(PUBLIC_KEY), "")

    def test_default_manifest_url_is_the_latest_github_release(self) -> None:
        self.assertEqual(DEFAULTS[MANIFEST_URL], DEFAULT_MANIFEST_URL)
        self.assertTrue(DEFAULT_MANIFEST_URL.startswith("https://github.com/future-agi/future-agi/releases/"))
        self.assertTrue(DEFAULT_MANIFEST_URL.endswith("/future-agi-update-manifest.json"))

    def test_default_identity_pins_the_release_workflow_at_the_manifest_tag(self) -> None:
        self.assertEqual(DEFAULTS[IDENTITY], DEFAULT_TRUSTED_IDENTITY)
        self.assertEqual(
            DEFAULT_TRUSTED_IDENTITY,
            "https://github.com/future-agi/future-agi/.github/workflows/release-update-manifest.yml"
            "@refs/tags/{version}",
        )
        self.assertEqual(DEFAULT_TRUSTED_IDENTITY.count("{version}"), 1)

    def test_default_issuer_is_github_actions(self) -> None:
        self.assertEqual(DEFAULTS[ISSUER], DEFAULT_TRUSTED_ISSUER)
        self.assertEqual(DEFAULT_TRUSTED_ISSUER, "https://token.actions.githubusercontent.com")

    def test_default_cosign_image_is_pinned_by_digest(self) -> None:
        self.assertEqual(DEFAULTS[COSIGN_IMAGE], DEFAULT_COSIGN_IMAGE)
        self.assertRegex(DEFAULT_COSIGN_IMAGE, r"^ghcr\.io/sigstore/cosign/cosign:v[0-9.]+@sha256:[0-9a-f]{64}$")

    def test_every_setting_is_documented(self) -> None:
        readme = (ROOT / "deploy" / "update" / "README.md").read_text(encoding="utf-8")
        # This checks the existing operator README. Automation operator docs
        # ship with runner/CLI integration, not the S01 settings-only slice.
        for key in LAYER_VALUES:
            with self.subTest(key=key):
                self.assertIn(f"`{key}`", readme)


# =============================================================== envfile ==


class _EnvCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.dir = Path(temporary.name)

    def write(self, text: str, name: str = ".env", mode: Optional[int] = None) -> Path:
        path = self.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        if mode is not None:
            os.chmod(path, mode)
        return path

    def parse(self, text: str) -> Dict[str, str]:
        return read_env(self.write(text))

    @staticmethod
    def listing(directory: Path) -> list:
        return sorted(entry.name for entry in directory.iterdir())


class ReadEnvTests(_EnvCase):
    def test_simple_pairs(self) -> None:
        self.assertEqual(self.parse("A=1\nB=two\nC_3=x\n_D=y\n"), {"A": "1", "B": "two", "C_3": "x", "_D": "y"})

    def test_last_definition_wins(self) -> None:
        self.assertEqual(self.parse("V=1\nA=a\nV=2\nV=3\n")["V"], "3")
        self.assertEqual(self.parse("V=1\nexport V=2\n")["V"], "2")
        self.assertEqual(self.parse("export V=1\nV=\n")["V"], "")

    def test_later_commented_definition_does_not_win(self) -> None:
        self.assertEqual(self.parse("V=1\n# V=2\n  #V=3\n")["V"], "1")

    def test_export_prefix(self) -> None:
        self.assertEqual(self.parse("export A=1\nexport\tB=2\n  export   C=3\n"), {"A": "1", "B": "2", "C": "3"})

    def test_export_needs_whitespace_after_it(self) -> None:
        self.assertEqual(self.parse("exportI=x\nexport=1\n"), {"exportI": "x", "export": "1"})

    def test_single_quotes(self) -> None:
        values = self.parse(
            "A='a b'\n"
            "B='x # y'\n"
            "C='z' # trailing comment\n"
            "D='$NOT_EXPANDED ${X}'\n"
            "E='say \"hi\"'\n"
        )
        self.assertEqual(values, {"A": "a b", "B": "x # y", "C": "z", "D": "$NOT_EXPANDED ${X}", "E": 'say "hi"'})

    def test_double_quotes(self) -> None:
        values = self.parse('A="a b"\nB="x # y"\nC="z" # trailing comment\nD="it\'s"\n')
        self.assertEqual(values, {"A": "a b", "B": "x # y", "C": "z", "D": "it's"})

    def test_escaped_double_quotes(self) -> None:
        values = self.parse(r'A="a\"b"' "\n" r'B="\"quoted\""' "\n" r'C="a\\b"' "\n" r'D="a\\\"b"' "\n")
        self.assertEqual(values, {"A": 'a"b', "B": '"quoted"', "C": "a\\b", "D": 'a\\"b'})

    def test_escaped_single_quote(self) -> None:
        # Compose: A='it\'s' is "it's".
        self.assertEqual(self.parse("A='it\\'s'\n")["A"], "it's")

    def test_escaped_backslash_before_closing_double_quote(self) -> None:
        # Compose: A="x\\" is x\ and B="C:\\dir\\" is C:\dir\ (the quote closes).
        values = self.parse('A="x\\\\"\nB="C:\\\\dir\\\\" # comment\n')
        self.assertEqual(values, {"A": "x\\", "B": "C:\\dir\\"})

    def test_inline_comments_on_unquoted_values(self) -> None:
        values = self.parse("A=v # c\nB=v#c\nC=v  #c\nD= #c\nE=a #b #c\nF=http://h/#frag\n")
        self.assertEqual(values, {"A": "v", "B": "v#c", "C": "v", "D": "#c", "E": "a", "F": "http://h/#frag"})

    def test_hash_after_tab_is_part_of_an_unquoted_value(self) -> None:
        # Compose ends an unquoted value only at " #": A=v<TAB># c is "v\t# c".
        self.assertEqual(self.parse("A=v\t# c\n")["A"], "v\t# c")

    def test_hash_inside_quotes_is_kept(self) -> None:
        values = self.parse("A='#x # y'\nB=\"p#ss # word\"\n")
        self.assertEqual(values, {"A": "#x # y", "B": "p#ss # word"})

    def test_commented_lines_are_ignored(self) -> None:
        self.assertEqual(self.parse("# A=1\n   # B=2\n#C=3\n\t# D=4\n#\n"), {})

    def test_crlf_line_endings(self) -> None:
        values = self.parse("# c\r\nA=1\r\nB='x'\r\nC=\"y\" # c\r\nD=v # c\r\nexport E=e\r\n\r\n")
        self.assertEqual(values, {"A": "1", "B": "x", "C": "y", "D": "v", "E": "e"})
        for value in values.values():
            self.assertNotIn("\r", value)

    def test_whitespace_around_key_and_value(self) -> None:
        self.assertEqual(self.parse("  A = spaced  \nB=  lead\nC=trail   \n"),
                         {"A": "spaced", "B": "lead", "C": "trail"})

    def test_empty_values(self) -> None:
        self.assertEqual(self.parse("A=\nB=''\nC=\"\"\nD= \n"), {"A": "", "B": "", "C": "", "D": ""})

    def test_value_may_contain_equals(self) -> None:
        self.assertEqual(self.parse("A=a=b==\n")["A"], "a=b==")

    def test_unparseable_lines_are_ignored(self) -> None:
        # A bare word (Compose takes it from the environment), an empty key and a key with a space
        # (Compose rejects that file) define nothing; 1A and A-B are keys Compose reads.
        self.assertEqual(self.parse("garbage\n1A=x\nA-B=y\n=z\nA B=c\nOK=1\n"), {"1A": "x", "A-B": "y", "OK": "1"})

    def test_last_line_without_newline(self) -> None:
        self.assertEqual(self.parse("A=1\nB=2"), {"A": "1", "B": "2"})

    def test_empty_file(self) -> None:
        self.assertEqual(self.parse(""), {})

    def test_unicode_values(self) -> None:
        self.assertEqual(self.parse("A=ünïcødé\nB='日本語'\n"), {"A": "ünïcødé", "B": "日本語"})

    def test_variables_are_not_expanded_or_executed(self) -> None:
        self.assertEqual(self.parse("A=${HOME}\nB=$(id)\nC=`id`\n"), {"A": "${HOME}", "B": "$(id)", "C": "`id`"})

    def test_utf8_bom_is_ignored(self) -> None:
        # Compose strips a leading UTF-8 BOM, so the first key still counts
        # (COMPOSE_FILE on line 1 decides Standalone vs Distributed).
        path = self.dir / ".env"
        path.write_bytes(b"\xef\xbb\xbfCOMPOSE_FILE=docker-compose.distributed.yml\nB=2\n")
        self.assertEqual(read_env(path), {"COMPOSE_FILE": "docker-compose.distributed.yml", "B": "2"})

    def test_missing_file_is_an_env_file_error(self) -> None:
        path = self.dir / "absent.env"
        with self.assertRaises(EnvFileError) as caught:
            read_env(path)
        self.assertIn("cannot read", str(caught.exception))
        self.assertIn(str(path), str(caught.exception))

    def test_directory_is_an_env_file_error(self) -> None:
        with self.assertRaises(EnvFileError):
            read_env(self.dir)

    def test_non_utf8_file_is_read_like_compose(self) -> None:
        # Compose accepts bytes that are not UTF-8 (no UnicodeDecodeError
        # traceback either); they come back as surrogate escapes, which
        # os.fsencode turns into the original bytes for a subprocess.
        path = self.dir / ".env"
        path.write_bytes("PG_PASSWORD=caf\xe9\n".encode("latin-1"))
        self.assertEqual(read_env(path), {"PG_PASSWORD": "caf\udce9"})
        self.assertEqual(os.fsencode(read_env(path)["PG_PASSWORD"]), b"caf\xe9")

    def test_env_file_error_is_a_value_error(self) -> None:
        self.assertTrue(issubclass(EnvFileError, ValueError))


class ExampleKeysTests(_EnvCase):
    def test_missing_file_has_no_keys(self) -> None:
        self.assertEqual(example_keys(self.dir / ".env.example"), [])

    def test_keys_in_file_order_without_duplicates(self) -> None:
        path = self.write("B=1\nA=2\nB=3\nC=\n", ".env.example")
        self.assertEqual(example_keys(path), ["B", "A", "C"])

    def test_commented_and_unparseable_lines_are_skipped(self) -> None:
        # 1BAD is a key to Compose (the app container gets it), so it is one here too.
        path = self.write("# OPTIONAL=1\n   #ALSO=2\nnot a pair\n1BAD=x\nexport REAL=1\n  SPACED = 2\n", ".env.example")
        self.assertEqual(example_keys(path), ["1BAD", "REAL", "SPACED"])

    def test_crlf_example(self) -> None:
        path = self.write("A=1\r\n# B=2\r\nC=3\r\n", ".env.example")
        self.assertEqual(example_keys(path), ["A", "C"])

    def test_returns_a_list(self) -> None:
        self.assertIsInstance(example_keys(self.write("A=1\n", ".env.example")), list)

    def test_values_do_not_matter(self) -> None:
        path = self.write("A='quoted # x'\nB=\"x\"\nC=${OTHER}\n", ".env.example")
        self.assertEqual(example_keys(path), ["A", "B", "C"])


class RenderChangesTests(unittest.TestCase):
    def test_replaces_a_value(self) -> None:
        self.assertEqual(render_changes("A=1\nV=1.0.0\nB=2\n", {"V": "1.1.0"}), "A=1\nV=1.1.0\nB=2\n")

    def test_replaces_every_occurrence(self) -> None:
        self.assertEqual(
            render_changes("V=1.0.0\nA=1\nexport V=0.9.0\nV = 0.8.0\n", {"V": "1.1.0"}),
            "V=1.1.0\nA=1\nexport V=1.1.0\nV = 1.1.0\n",
        )

    def test_keeps_every_other_byte(self) -> None:
        text = (
            "# Future AGI\r\n"
            "SECRET_KEY='s3cr3t # not a comment'   \r\n"
            "\r\n"
            "   # V=commented out stays\r\n"
            "export V=1.0.0\r\n"
            "B=two words # trailing comment\t \r\n"
            "\tINDENTED=1\r\n"
            "ünï=cødé\r\n"
            "Z=last"
        )
        expected = text.replace("export V=1.0.0\r\n", "export V=1.1.0\r\n")
        self.assertEqual(render_changes(text, {"V": "1.1.0"}), expected)

    def test_keeps_crlf_on_replaced_and_untouched_lines(self) -> None:
        self.assertEqual(render_changes("A=1\r\nV=old\r\nB=2\r\n", {"V": "new"}), "A=1\r\nV=new\r\nB=2\r\n")

    def test_keeps_leading_whitespace_and_export(self) -> None:
        self.assertEqual(render_changes("  export   V=old\n\tV=old\n", {"V": "new"}), "  export   V=new\n\tV=new\n")

    def test_replaced_last_line_without_newline_stays_without_newline(self) -> None:
        self.assertEqual(render_changes("A=1\nV=old", {"V": "new"}), "A=1\nV=new")

    def test_quoted_and_commented_value_is_replaced(self) -> None:
        rendered = render_changes("V='1.0.0' # pinned\nA=1\n", {"V": "1.1.0"})
        self.assertTrue(rendered.startswith("V='1.1.0'"))
        self.assertTrue(rendered.endswith("\nA=1\n"))
        self.assertNotIn("1.0.0", rendered)

    def test_commented_out_definition_is_untouched_and_key_appended(self) -> None:
        self.assertEqual(render_changes("# V=old\n", {"V": "new"}), "# V=old\nV=new\n")

    def test_similar_keys_are_untouched(self) -> None:
        text = "V_EXTRA=1\nXV=2\nexportV=3\nv=4\n"
        self.assertEqual(render_changes(text, {"V": "9"}), text + "V=9\n")

    def test_none_removes_every_definition(self) -> None:
        self.assertEqual(
            render_changes("A=1\nV=x\nB=2\nexport V=y\n  V = z\n# V=comment\n", {"V": None}),
            "A=1\nB=2\n# V=comment\n",
        )

    def test_none_removal_keeps_crlf_of_other_lines(self) -> None:
        self.assertEqual(render_changes("A=1\r\nV=x\r\nB=2\r\n", {"V": None}), "A=1\r\nB=2\r\n")

    def test_none_for_missing_key_changes_nothing(self) -> None:
        self.assertEqual(render_changes("A=1\n", {"V": None}), "A=1\n")
        self.assertEqual(render_changes("A=1", {"V": None}), "A=1")
        self.assertEqual(render_changes("", {"V": None}), "")

    def test_no_changes_is_identity(self) -> None:
        for text in ("", "A=1", "A=1\n", "A=1\r\n# c\r\n", "garbage\n\n"):
            with self.subTest(text=text):
                self.assertEqual(render_changes(text, {}), text)

    def test_missing_key_is_appended(self) -> None:
        self.assertEqual(render_changes("A=1\n", {"V": "1.2.3"}), "A=1\nV=1.2.3\n")

    def test_append_fixes_a_missing_final_newline(self) -> None:
        self.assertEqual(render_changes("A=1", {"V": "1.2.3"}), "A=1\nV=1.2.3\n")
        self.assertEqual(render_changes("# only a comment", {"V": "1.2.3"}), "# only a comment\nV=1.2.3\n")

    def test_append_to_crlf_file_keeps_existing_lines(self) -> None:
        rendered = render_changes("A=1\r\nB=2\r\n", {"V": "1.2.3"})
        self.assertTrue(rendered.startswith("A=1\r\nB=2\r\n"))
        self.assertEqual(rendered.splitlines()[2], "V=1.2.3")
        self.assertTrue(rendered.endswith("\n"))

    def test_append_to_empty_text(self) -> None:
        self.assertEqual(render_changes("", {"V": "1.2.3"}), "V=1.2.3\n")

    def test_several_missing_keys_are_appended_in_order(self) -> None:
        self.assertEqual(
            render_changes("A=1", {"Z": "3", "V": "1", "M": None, "Q": "2"}),
            "A=1\nZ=3\nV=1\nQ=2\n",
        )

    def test_mixed_replace_remove_append(self) -> None:
        self.assertEqual(
            render_changes("A=1\nR=gone\nV=old\n", {"V": "new", "R": None, "N": "add"}),
            "A=1\nV=new\nN=add\n",
        )

    def test_empty_string_writes_key_equals(self) -> None:
        self.assertEqual(render_changes("V=1.0.0\n", {"V": ""}), "V=\n")
        self.assertEqual(render_changes("A=1\n", {"V": ""}), "A=1\nV=\n")

    def test_image_tags_are_accepted(self) -> None:
        for tag in ("1.2.3", "v1.2.3", "latest", "local", "1.2.3-rc.1", "1.2.3-gpu", "sha-abc_DEF.9", "_x", "0",
                    "a" * 128):
            with self.subTest(tag=tag):
                self.assertEqual(render_changes("", {"V": tag}), f"V={tag}\n")

    def test_injection_values_are_refused(self) -> None:
        for bad in ("${SECRET}", "$SECRET", "1.0 0", " 1.0", "1.0 ", '"1.0"', "'1.0'", "1.0\nSECRET_KEY=x",
                    "1.0\n", "1.0\r", "1.0\r\nX=1", "$", "`id`", "$(id)", "1.0;rm", ";", "a#b", "#", "a\\b",
                    "-1.0", ".hidden", "a/b", "a:b", "1.0@sha256:" + "0" * 64, "a=b", "1.0\t", "\x00",
                    "ünï", "\uff11.0", "a" * 129, "*", "a|b", "a&b", "a>b", "a<b"):
            with self.subTest(value=bad):
                with self.assertRaises(EnvFileError) as caught:
                    render_changes("FRONTEND_VERSION=1.0.0\n", {"FRONTEND_VERSION": bad})
                self.assertIn("FRONTEND_VERSION", str(caught.exception))
                self.assertIn("refusing", str(caught.exception))

    def test_one_bad_value_refuses_the_whole_change(self) -> None:
        with self.assertRaises(EnvFileError) as caught:
            render_changes("A=1\n", {"GOOD": "1.2.3", "BAD": "${X}", "LATER": "1.0.0"})
        self.assertIn("BAD", str(caught.exception))

    def test_invalid_key_names_are_refused(self) -> None:
        # Keys reach render_changes from journals and --receipt files; a key
        # with a newline or '=' would write arbitrary lines into .env.
        for bad in ("", "BAD KEY", "A=B", "X\nSECRET_KEY", "COMPOSE_FILE=evil.yml\nFUTURE_AGI_VERSION",
                    "1ABC", "A-B", "# A", "export A"):
            with self.subTest(key=bad):
                with self.assertRaises(EnvFileError):
                    render_changes("A=1\n", {bad: "1.2.3"})

    def test_rendered_values_read_back_through_read_env(self) -> None:
        text = "A='q'\r\nV=old # c\r\nexport W=old\r\nB=\"x # y\"\r\n"
        rendered = render_changes(text, {"V": "1.1.0", "W": "", "N": "2.0.0"})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_bytes(rendered.encode("utf-8"))
            self.assertEqual(read_env(path), {"A": "q", "V": "1.1.0", "W": "", "B": "x # y", "N": "2.0.0"})

    def test_utf8_bom_line_is_still_edited(self) -> None:
        # Compose ignores a leading BOM, so a version key on line 1 must still
        # be found (otherwise a removal leaves the old value in force).
        self.assertEqual(render_changes("\ufeffV=1.0.0\nA=1\n", {"V": None}), "\ufeffA=1\n")
        self.assertEqual(render_changes("\ufeffV=1.0.0\nA=1\n", {"V": "1.1.0"}), "\ufeffV=1.1.0\nA=1\n")


class WriteChangesTests(_EnvCase):
    VERSION = "FUTURE_AGI_VERSION"

    def spy_replace(self):
        # Observe the actual env exchange, excluding metadata publication and probes.
        calls = []
        real = envfile.envcommit.exchange
        def exchange(a, b, c, d):
            if b == 'slot':
                op = next(p for p in self.dir.glob('**/env-commits/*/*')
                          if p.stat().st_ino == os.fstat(a).st_ino)
                body = json.loads((op / 'manifest.json').read_text())
                calls.append((str(op / b), body['target']))
            return real(a, b, c, d)
        return calls, mock.patch.object(envfile.envcommit, 'exchange', side_effect=exchange)

    # ------------------------------------------------------------ basics --
    def test_writes_the_change(self) -> None:
        path = self.write(f"A=1\n{self.VERSION}=1.0.0\n")
        write_changes(path, {self.VERSION: "1.1.0"})
        self.assertEqual(path.read_bytes(), f"A=1\n{self.VERSION}=1.1.0\n".encode())
        self.assertEqual(read_env(path)[self.VERSION], "1.1.0")

    def test_keeps_every_other_byte(self) -> None:
        original = (
            "# Future AGI .env\n"
            "SECRET_KEY='s3cr3t # x'   \n"
            "\n"
            "PG_PASSWORD=ünïcødé-🔑\t\n"
            f"export {self.VERSION}=1.0.0  # pinned\n"
            "  # trailing comment\n"
            "LAST=1"
        )
        path = self.write(original)
        write_changes(path, {self.VERSION: "1.1.0"})
        expected = original.replace(f"export {self.VERSION}=1.0.0  # pinned\n", f"export {self.VERSION}=1.1.0  # pinned\n")
        self.assertEqual(path.read_bytes(), expected.encode("utf-8"))

    def test_crlf_file_keeps_crlf_bytes(self) -> None:
        original = f"# top\r\nA=1\r\n{self.VERSION}=1.0.0\r\nB=2  \r\n"
        path = self.write(original)
        write_changes(path, {self.VERSION: "1.1.0"})
        self.assertEqual(path.read_bytes(), original.replace("1.0.0", "1.1.0").encode())

    def test_appends_and_removes(self) -> None:
        path = self.write("A=1\nOLD=x\n")
        write_changes(path, {"OLD": None, self.VERSION: "1.1.0"})
        self.assertEqual(path.read_text(encoding="utf-8"), f"A=1\n{self.VERSION}=1.1.0\n")

    def test_append_to_file_without_final_newline(self) -> None:
        path = self.write("A=1")
        write_changes(path, {self.VERSION: "1.1.0"})
        self.assertEqual(path.read_bytes(), f"A=1\n{self.VERSION}=1.1.0\n".encode())

    # ---------------------------------------------------------- atomicity --
    def test_replaces_the_file_atomically(self) -> None:
        path = self.write(f"{self.VERSION}=1.0.0\n")
        before = os.stat(path).st_ino
        calls, patch = self.spy_replace()
        with open(path, encoding="utf-8") as old_reader, patch:
            write_changes(path, {self.VERSION: "1.1.0"})
            self.assertEqual(old_reader.read(), f"{self.VERSION}=1.0.0\n")
        self.assertNotEqual(os.stat(path).st_ino, before)
        self.assertEqual(len(calls), 1)
        temp, destination = calls[0]
        self.assertEqual(Path(destination), path.resolve())
        self.assertEqual(Path(temp).parent.parents[3].resolve(), path.resolve().parent)

    def test_candidate_starts_private_and_is_contained_after_mode_preservation(self):
        path = self.write(f"{self.VERSION}=1.0.0\n", mode=0o644)
        seen = []
        real = os.fchmod
        def chmod(fd, mode):
            seen.append((stat.S_IMODE(os.fstat(fd).st_mode), mode))
            return real(fd, mode)
        calls, patch = self.spy_replace()
        with patch, mock.patch.object(envfile.os, 'fchmod', side_effect=chmod):
            write_changes(path, {self.VERSION: '1.1.0'})
        slot = Path(calls[0][0])
        self.assertEqual(slot.name, 'slot')
        self.assertEqual(stat.S_IMODE(slot.parent.stat().st_mode), 0o700)
        self.assertEqual(seen, [(0o600, 0o644)])
        self.assertEqual(stat.S_IMODE((slot.parent / 'manifest.json').stat().st_mode), 0o600)

    def test_success_retains_each_displaced_file_and_candidate(self):
        path = self.write(f"{self.VERSION}=1.0.0\nA=1\n")
        before = path.read_bytes()
        write_changes(path, {self.VERSION: '1.1.0'})
        middle = path.read_bytes()
        write_changes(path, {'A': None, 'NEW': 'x'})
        slots = list(self.dir.glob('.futureagi-updates/env-commits/*/*/slot'))
        self.assertEqual(len(slots), 2)
        self.assertCountEqual([p.read_bytes() for p in slots], [before, middle])
        self.assertEqual(len(list(self.dir.glob('.futureagi-updates/env-commits/*/*/candidate'))), 2)
        self.assertFalse(list(self.dir.glob('.env.futureagi-update-*.tmp')))

    # ------------------------------------------------------ mode / owner --
    def test_keeps_file_mode(self) -> None:
        for mode in (0o600, 0o644, 0o640, 0o400, 0o664):
            with self.subTest(mode=oct(mode)):
                path = self.write(f"{self.VERSION}=1.0.0\n", name=f"env-{mode:o}", mode=mode)
                write_changes(path, {self.VERSION: "1.1.0"})
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), mode)
                self.assertEqual(read_env(path)[self.VERSION], "1.1.0")

    def test_mode_does_not_follow_umask(self) -> None:
        path = self.write(f"{self.VERSION}=1.0.0\n", mode=0o664)
        previous = os.umask(0o077)
        try:
            write_changes(path, {self.VERSION: "1.1.0"})
        finally:
            os.umask(previous)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o664)

    @unittest.skipUnless(hasattr(os, "chown"), "no chown on this platform")
    def test_keeps_owner_when_permitted(self):
        path = self.write(f"{self.VERSION}=1.0.0\n")
        before = path.stat()
        real = os.fchown
        with mock.patch.object(envfile.os, 'fchown', wraps=real) as chown:
            write_changes(path, {self.VERSION: '1.1.0'})
        self.assertEqual(chown.call_count, 1)
        self.assertEqual(chown.call_args.args[1:], (before.st_uid, before.st_gid))
        self.assertEqual((path.stat().st_uid, path.stat().st_gid), (before.st_uid, before.st_gid))

    @unittest.skipUnless(hasattr(os, "chown"), "no chown on this platform")
    def test_owner_preservation_failure_is_refused_before_exchange(self):
        path = self.write(f"{self.VERSION}=1.0.0\n", mode=0o640)
        original = path.read_bytes()
        with mock.patch.object(envfile.os, 'fchown', side_effect=PermissionError(1, 'fixture')):
            with self.assertRaises(EnvFileError):
                write_changes(path, {self.VERSION: '1.1.0'})
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)

    # ----------------------------------------------------------- symlinks --
    def test_symlink_target_is_edited_and_link_kept(self) -> None:
        target = self.write(f"A=1\n{self.VERSION}=1.0.0\n", name="shared/real.env", mode=0o640)
        checkout = self.dir / "checkout"
        checkout.mkdir()
        link = checkout / ".env"
        os.symlink(os.path.join("..", "shared", "real.env"), link)
        before_checkout, before_shared = self.listing(checkout), self.listing(target.parent)
        before_link = link.lstat()
        calls, patch = self.spy_replace()
        with patch:
            write_changes(link, {self.VERSION: "1.1.0"})
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), os.path.join("..", "shared", "real.env"))
        self.assertEqual(link.lstat().st_ino, before_link.st_ino)
        self.assertEqual(target.read_bytes(), f"A=1\n{self.VERSION}=1.1.0\n".encode())
        self.assertEqual(stat.S_IMODE(os.stat(target).st_mode), 0o640)
        self.assertEqual(len(calls), 1)
        self.assertEqual(Path(calls[0][1]), target.resolve())
        self.assertEqual(Path(calls[0][0]).parent.parents[3].resolve(), target.resolve().parent)
        self.assertEqual(self.listing(checkout), before_checkout)
        self.assertEqual(self.listing(target.parent), sorted(before_shared + ['.futureagi-updates']))

    def test_symlinked_parent_keeps_alias_and_uses_target_retention(self) -> None:
        # Exercise a /var -> /private/var-style alias even with a canonical TMPDIR.
        target = self.write(f"{self.VERSION}=1.0.0\n", name="actual/.env")
        alias = self.dir / "alias"
        alias.symlink_to("actual", target_is_directory=True)
        path = alias / ".env"
        self.assertNotEqual(path, target.resolve())
        self.assertTrue(path.samefile(target))
        before_alias = alias.lstat()
        calls, patch = self.spy_replace()
        with open(path, encoding="utf-8") as old_reader, patch:
            write_changes(path, {self.VERSION: "1.1.0"})
            self.assertEqual(old_reader.read(), f"{self.VERSION}=1.0.0\n")
        self.assertTrue(alias.is_symlink())
        self.assertEqual(os.readlink(alias), "actual")
        self.assertEqual(alias.lstat().st_ino, before_alias.st_ino)
        self.assertEqual(path.read_bytes(), f"{self.VERSION}=1.1.0\n".encode())
        self.assertTrue(path.samefile(target))
        self.assertEqual(len(calls), 1)
        temp, destination = calls[0]
        self.assertEqual(Path(destination), target.resolve())
        self.assertEqual(Path(temp).parent.parents[3].resolve(), target.parent.resolve())
        self.assertEqual(Path(temp).read_bytes(), f"{self.VERSION}=1.0.0\n".encode())

    def test_symlink_chain_is_followed(self) -> None:
        target = self.write(f"{self.VERSION}=1.0.0\n", name="real.env")
        middle = self.dir / "middle.env"
        os.symlink(str(target), middle)
        link = self.dir / ".env.link"
        os.symlink("middle.env", link)
        write_changes(link, {self.VERSION: "1.1.0"})
        self.assertTrue(link.is_symlink())
        self.assertTrue(middle.is_symlink())
        self.assertEqual(read_env(target)[self.VERSION], "1.1.0")

    def test_dangling_symlink_is_an_env_file_error(self) -> None:
        link = self.dir / ".env"
        os.symlink("missing.env", link)
        with self.assertRaises(EnvFileError):
            write_changes(link, {self.VERSION: "1.1.0"})
        self.assertFalse((self.dir / "missing.env").exists())
        self.assertEqual(self.listing(self.dir), [".env"])

    def test_symlink_loop_is_an_env_file_error(self) -> None:
        os.symlink("b.env", self.dir / "a.env")
        os.symlink("a.env", self.dir / "b.env")
        with self.assertRaises(EnvFileError):
            write_changes(self.dir / "a.env", {self.VERSION: "1.1.0"})
        self.assertEqual(self.listing(self.dir), ["a.env", "b.env"])

    # ------------------------------------------------------------ failures --
    def test_missing_file_is_an_env_file_error(self) -> None:
        path = self.dir / ".env"
        with self.assertRaises(EnvFileError) as caught:
            write_changes(path, {self.VERSION: "1.1.0"})
        self.assertIn(str(path), str(caught.exception))
        self.assertEqual(self.listing(self.dir), [])

    def test_refused_value_leaves_file_untouched(self) -> None:
        path = self.write(f"{self.VERSION}=1.0.0\nSECRET_KEY=x\n")
        before = os.stat(path)
        listing = self.listing(self.dir)
        with self.assertRaises(EnvFileError):
            write_changes(path, {self.VERSION: "1.1.0", "SECRET_KEY": "${PG_PASSWORD}"})
        after = os.stat(path)
        self.assertEqual((after.st_ino, after.st_mtime_ns), (before.st_ino, before.st_mtime_ns))
        self.assertEqual(path.read_text(encoding="utf-8"), f"{self.VERSION}=1.0.0\nSECRET_KEY=x\n")
        self.assertEqual(self.listing(self.dir), listing)

    def test_failed_replace_leaves_original_and_retains_private_evidence(self):
        path = self.write(f"{self.VERSION}=1.0.0\n", mode=0o600)
        before = path.read_bytes()
        with mock.patch.object(envfile.envcommit, 'exchange', side_effect=OSError(28, 'fixture')):
            with self.assertRaises(EnvFileError):
                write_changes(path, {self.VERSION: '1.1.0'})
        self.assertEqual(path.read_bytes(), before)
        self.assertTrue((self.dir / '.futureagi-updates').is_dir())

    def test_failed_fsync_leaves_original_and_retains_private_evidence(self):
        path = self.write(f"{self.VERSION}=1.0.0\n", mode=0o600)
        before = path.read_bytes()
        with mock.patch.object(envfile.envcommit, 'sync_file', side_effect=OSError(28, 'fixture')):
            with self.assertRaises(EnvFileError):
                write_changes(path, {self.VERSION: '1.1.0'})
        self.assertEqual(path.read_bytes(), before)
        self.assertTrue((self.dir / '.futureagi-updates').is_dir())

    def test_failed_chmod_preserves_original_and_private_candidate(self):
        path = self.write(f"{self.VERSION}=1.0.0\n")
        before = path.read_bytes()
        with mock.patch.object(envfile.os, 'fchmod', side_effect=OSError(1, 'fixture')):
            with self.assertRaises(EnvFileError):
                write_changes(path, {self.VERSION: '1.1.0'})
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(len(list(self.dir.glob('.futureagi-updates/env-commits/*/*/candidate'))), 1)

    def test_ctrl_c_during_write_retains_evidence_and_propagates(self):
        path = self.write(f"{self.VERSION}=1.0.0\n")
        before = path.read_bytes()
        with mock.patch.object(envfile.envcommit, 'sync_file', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                write_changes(path, {self.VERSION: '1.1.0'})
        self.assertEqual(path.read_bytes(), before)
        self.assertTrue((self.dir / '.futureagi-updates').is_dir())

    def test_failed_rewrite_is_an_env_file_error(self) -> None:
        # bin/update reports EnvFileError cleanly (exit 2); a raw OSError from
        # the rewrite (read-only directory, full disk) becomes a traceback,
        # e.g. when apply/rollback recovers a journal.
        path = self.write(f"{self.VERSION}=1.0.0\n")
        with mock.patch.object(envfile.os, "replace", side_effect=PermissionError(13, "Permission denied")):
            with self.assertRaises(EnvFileError) as caught:
                write_changes(path, {self.VERSION: "1.1.0"})
        self.assertIn(str(path), str(caught.exception))

    @unittest.skipIf(IS_SUPERUSER, "root can write into a read-only directory")
    def test_read_only_directory_leaves_original(self) -> None:
        original = f"{self.VERSION}=1.0.0\n"
        path = self.write(original, name="ro/.env")
        os.chmod(path.parent, 0o500)
        self.addCleanup(os.chmod, path.parent, 0o700)
        with self.assertRaises(Exception):
            write_changes(path, {self.VERSION: "1.1.0"})
        self.assertEqual(path.read_text(encoding="utf-8"), original)
        self.assertEqual(self.listing(path.parent), [".env"])

    def test_non_utf8_bytes_are_kept(self) -> None:
        path = self.dir / ".env"
        raw = f"PG_PASSWORD=caf\xe9\n{self.VERSION}=1.0.0\n".encode("latin-1")
        path.write_bytes(raw)
        write_changes(path, {self.VERSION: "1.1.0"})
        self.assertEqual(path.read_bytes(), raw.replace(b"1.0.0", b"1.1.0"))

    # -------------------------------------------------------------- no-op --
    def assert_untouched(self, original: str, changes: Mapping[str, Optional[str]]) -> None:
        path = self.write(original)
        past = 1_000_000_000
        os.utime(path, ns=(past * 10**9, past * 10**9))
        before = os.stat(path)
        with mock.patch.object(envfile.envcommit, "commit", side_effect=AssertionError("must not write")):
            write_changes(path, changes)
        after = os.stat(path)
        self.assertEqual((after.st_ino, after.st_mtime_ns), (before.st_ino, before.st_mtime_ns))
        self.assertEqual(path.read_bytes(), original.encode("utf-8"))

    def test_no_change_is_a_no_op(self) -> None:
        self.assert_untouched(f"{self.VERSION}=1.0.0\n", {})

    def test_same_value_is_a_no_op(self) -> None:
        self.assert_untouched(f"A=1\n{self.VERSION}=1.0.0\n", {self.VERSION: "1.0.0"})

    def test_removing_an_absent_key_is_a_no_op(self) -> None:
        self.assert_untouched("A=1\n", {self.VERSION: None})

    def test_same_value_in_crlf_file_is_a_no_op(self) -> None:
        self.assert_untouched(f"A=1\r\n{self.VERSION}=1.0.0\r\n", {self.VERSION: "1.0.0"})

    def test_same_empty_value_is_a_no_op(self) -> None:
        self.assert_untouched(f"{self.VERSION}=\n", {self.VERSION: ""})

    # ---------------------------------------------------------- gitignore --
    def test_temp_names_are_ignored_by_the_repository(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git is not installed")
        # The helper installs its own ignore file in private retention. Exercise
        # that actual runtime layout, including a nested production env target.
        subprocess.run(["git", "init", "-q", str(self.dir)], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        def check_ignore(relative: str) -> int:
            return subprocess.run(["git", "-C", str(self.dir), "check-ignore", "-q", relative],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE).returncode

        # The check itself works: a plain source path is not ignored.
        self.assertEqual(check_ignore("deploy/update/probe-not-ignored.py"), 1)
        for relative in (".env", "deploy/.env.production"):
            with self.subTest(env_file=relative):
                path = self.write(f"{self.VERSION}=1.0.0\n", name=relative)
                calls, patch = self.spy_replace()
                with patch:
                    write_changes(path, {self.VERSION: "1.1.0"})
                self.assertEqual(len(calls), 1)
                temp = Path(calls[0][0]).resolve()
                self.assertEqual(temp.parent.parents[3], path.resolve().parent)
                in_repo = (Path(relative).parent / temp.relative_to(path.resolve().parent)).as_posix()
                self.assertEqual(check_ignore(in_repo), 0, f"{in_repo} is not ignored by .gitignore")


# ===================================================== settings additions ==


class SettingsMessageAdditions(_SettingsCase):
    """Every settings error names the file line or the layer the bad value came from."""

    def test_non_utf8_conf_names_the_line_not_the_bytes(self) -> None:
        self.conf_path.write_bytes(
            f"# backups\n{POLICY}=major\n{BACKUP_COMMAND}=/bin/backup --password=s\xe4cret\n".encode("latin-1")
        )
        with self.assertRaises(SettingsError) as caught:
            read_config_file(self.conf_path)
        message = str(caught.exception)
        self.assertEqual(message, f"{self.conf_path}:3: not UTF-8 text; save the file as UTF-8")
        self.assertIsInstance(caught.exception.__cause__, UnicodeDecodeError)

    def test_non_utf8_conf_surfaces_through_load_settings(self) -> None:
        self.conf_path.write_bytes(b"\xff\xfeF\x00")
        with self.assertRaises(SettingsError) as caught:
            self.load(environ={POLICY: "minor"})
        self.assertIn(f"{self.conf_path}:1: not UTF-8", str(caught.exception))

    def test_integer_messages_end_with_the_source(self) -> None:
        for source in SOURCES:
            with self.subTest(source=source, problem="format"):
                self.assertEqual(
                    self.rejection(*self.via(source, WAIT_TIMEOUT, "soon")),
                    f"{WAIT_TIMEOUT} must be a whole number of seconds, not 'soon' (from {source})",
                )
            with self.subTest(source=source, problem="bounds"):
                self.assertEqual(
                    self.rejection(*self.via(source, BACKUP_TIMEOUT, "0")),
                    f"{BACKUP_TIMEOUT} must be between 1 and 86400, not 0 (from {source})",
                )

    def test_integer_called_after_loading_names_the_source(self) -> None:
        # apply.py reads the timeouts again with Settings.integer.
        settings = Settings(dict(DEFAULTS, **{WAIT_TIMEOUT: "x", BACKUP_TIMEOUT: "90000"}),
                            {WAIT_TIMEOUT: "environment", BACKUP_TIMEOUT: CONFIG_FILE})
        with self.assertRaises(SettingsError) as caught:
            settings.integer(WAIT_TIMEOUT, 60, 7200)
        self.assertTrue(str(caught.exception).endswith("(from environment)"), str(caught.exception))
        with self.assertRaises(SettingsError) as caught:
            settings.integer(BACKUP_TIMEOUT, 1, 86400)
        self.assertTrue(str(caught.exception).endswith(f"(from {CONFIG_FILE})"), str(caught.exception))

    def test_default_source_is_named(self) -> None:
        settings = self.load()
        with self.assertRaises(SettingsError) as caught:
            settings.integer(WAIT_TIMEOUT, 60, 600)
        self.assertEqual(str(caught.exception), f"{WAIT_TIMEOUT} must be between 60 and 600, not 1200 (from default)")

    def test_boolean_message_ends_with_the_source(self) -> None:
        for source in SOURCES:
            with self.subTest(source=source):
                self.assertEqual(
                    self.rejection(*self.via(source, OFFLINE, "maybe")),
                    f"{OFFLINE} must be true or false, not 'maybe' (from {source})",
                )

    def test_manifest_url_message_shows_the_scheme_and_the_source(self) -> None:
        for source in SOURCES:
            with self.subTest(source=source):
                self.assertEqual(
                    self.rejection(*self.via(source, MANIFEST_URL, "HTTPS://example.com/m.json")),
                    f"{MANIFEST_URL} must be an https:// URL or a local file path, not a 'HTTPS://' URL "
                    f"(from {source})",
                )

    def test_manifest_url_message_hides_credentials(self) -> None:
        message = self.rejection(environ={MANIFEST_URL: "http://deploy:hunter2-secret@mirror.example/m.json"})
        self.assertIn("'http://'", message)
        self.assertNotIn("hunter2-secret", message)
        self.assertNotIn("mirror.example", message)

    def test_identity_message_ends_with_the_source(self) -> None:
        for source in SOURCES:
            with self.subTest(source=source):
                self.assertEqual(
                    self.rejection(*self.via(source, IDENTITY, "  ")),
                    f"{IDENTITY} must not be empty (from {source})",
                )

    def test_policy_and_channel_messages_are_unchanged(self) -> None:
        self.write_conf(f"{POLICY}=huge\n")
        self.assertEqual(self.rejection(),
                         f"{POLICY} must be one of patch, minor, major, not 'huge' (from {CONFIG_FILE})")
        self.assertEqual(self.rejection(environ={POLICY: "minor", CHANNEL: "nightly"}),
                         f"{CHANNEL} must be one of stable, beta, not 'nightly' (from environment)")

    def test_unknown_source_adds_no_suffix(self) -> None:
        settings = Settings(dict(DEFAULTS, **{OFFLINE: "maybe"}), {})
        with self.assertRaises(SettingsError) as caught:
            settings.boolean(OFFLINE)
        self.assertEqual(str(caught.exception), f"{OFFLINE} must be true or false, not 'maybe'")
        self.assertEqual(settings.origin(OFFLINE), "")


# ====================================================== envfile additions ==


class EnvFileParsingAdditions(_EnvCase):
    """More of Compose's dotenv rules (checked against docker-compose v5.1.4)."""

    def test_double_quotes_expand_compose_escapes(self) -> None:
        # Compose: "t\tx\qy\$z\0101\012" is t<TAB>x\qy$zA\12 (an unknown escape
        # stays as written; a bad octal loses its 0).
        self.assertEqual(self.parse('A="t\\tx\\qy\\$z\\0101\\012"\n')["A"], "t\tx\\qy$zA\\12")

    def test_double_quote_control_escapes(self) -> None:
        # Compose: \c matches its escape pattern but is no escape.
        self.assertEqual(self.parse('A="1\\a2\\b3\\f4\\v5\\c6\\r7"\n')["A"], "1\a2\b3\f4\v5\\c6\r7")
        self.assertEqual(self.parse('B="x\\0400y\\08z\\0w"\n')["B"], "x\\400y\\8z\\w")

    def test_single_quotes_keep_backslashes(self) -> None:
        # Only \' is special inside single quotes: 'a\\b' is a\\b, 'a\nb' is a\nb.
        self.assertEqual(self.parse("A='a\\\\b'\nB='a\\nb'\nC='x\\\\'\n"),
                         {"A": "a\\\\b", "B": "a\\nb", "C": "x\\\\"})

    def test_quoted_value_may_span_lines(self) -> None:
        values = self.parse("C=\"multi\nline\" # c\nD=after\nE='one\ntwo'\n")
        self.assertEqual(values, {"C": "multi\nline", "D": "after", "E": "one\ntwo"})

    def test_definition_inside_a_multi_line_value_does_not_count(self) -> None:
        values = self.parse('KEY="-----BEGIN\nFUTURE_AGI_VERSION=evil\n-----END"\nA=1\n')
        self.assertEqual(values, {"KEY": "-----BEGIN\nFUTURE_AGI_VERSION=evil\n-----END", "A": "1"})

    def test_crlf_inside_a_multi_line_value_stays_crlf(self) -> None:
        # Every Compose release from v2.24.0 to v5.6.0 keeps the CR of a quoted value's line break.
        self.assertEqual(self.parse('C="a\r\nb"\r\nD=1\r\n'), {"C": "a\r\nb", "D": "1"})
        self.assertEqual(self.parse("C='a\r\nb'\r\nD=\"x\\\r\ny\"\r\n"), {"C": "a\r\nb", "D": "x\\\r\ny"})

    def test_unterminated_quote_does_not_swallow_the_file(self) -> None:
        # Compose rejects the file; read_env keeps the quote in the value and
        # reads on, so `docker compose config` is what reports the problem.
        self.assertEqual(self.parse('A=1\nB="open\nC=2\n'), {"A": "1", "B": '"open', "C": "2"})

    def test_text_after_the_closing_quote_is_ignored(self) -> None:
        self.assertEqual(self.parse("F=\"x\"junk\nG='y'#c\nH=\"z\"\t# c\n"), {"F": "x", "G": "y", "H": "z"})

    def test_only_a_newline_ends_a_line(self) -> None:
        # str.splitlines() also splits at these; Compose does not.
        for separator in ("\r", "\x0b", "\x0c", "\x1c", "\x85", " "):
            with self.subTest(separator=separator):
                self.assertEqual(self.parse(f"A=x{separator}B=1\n"), {"A": f"x{separator}B=1"})

    def test_bom_and_crlf_together(self) -> None:
        path = self.dir / ".env"
        path.write_bytes(b"\xef\xbb\xbfexport COMPOSE_FILE=a.yml\r\nB='2' # c\r\n")
        self.assertEqual(read_env(path), {"COMPOSE_FILE": "a.yml", "B": "2"})

    @unittest.skipIf(IS_SUPERUSER, "root can read a mode 000 file")
    def test_unreadable_file_is_an_env_file_error(self) -> None:
        path = self.write("A=1\n", mode=0o000)
        with self.assertRaises(EnvFileError) as caught:
            read_env(path)
        self.assertIn(f"cannot read {path}", str(caught.exception))

    def test_example_keys_handles_bom_crlf_multi_line_and_non_utf8(self) -> None:
        path = self.dir / ".env.example"
        path.write_bytes(b"\xef\xbb\xbfA=caf\xe9\r\n# B=1\r\nC=\"x\nNOT_A_KEY=1\"\r\nD=1\r\n")
        self.assertEqual(example_keys(path), ["A", "C", "D"])

    def test_example_keys_of_a_directory_is_an_env_file_error(self) -> None:
        directory = self.dir / ".env.example"
        directory.mkdir()
        with self.assertRaises(EnvFileError) as caught:
            example_keys(directory)
        self.assertIn(str(directory), str(caught.exception))


class EnvFileRenderAdditions(_EnvCase):
    """Only the value changes, so writing the old value back restores the text."""

    # (line, the value Compose reads from it)
    LINES = (
        ("V=1.0.0", "1.0.0"),
        ("V=1.0.0  # pinned until QA signs off v1.1", "1.0.0"),
        ("V=1.0.0 \t ", "1.0.0"),
        ("  export   V = 1.0.0 # c ", "1.0.0"),
        ("V='1.0.0'", "1.0.0"),
        ("V='1.0.0' # pinned", "1.0.0"),
        ('V="1.0.0"#c', "1.0.0"),
        ("V='1.0.0'\t# note", "1.0.0"),
        ('V="1.0.0"junk', "1.0.0"),
        ("V=", ""),
        ("V=   ", ""),
        ("V = ", ""),
        ("V=''", ""),
        ('V=""  # empty on purpose', ""),
    )

    def texts(self, line: str):
        yield f"A=1\n{line}\nB=2\n"
        yield f"A=1\r\n{line}\r\nB=2\r\n"
        yield f"A=1\n{line}"
        yield f"﻿{line}\n"

    def test_write_then_restore_gives_back_the_original_text(self) -> None:
        for line, old in self.LINES:
            for text in self.texts(line):
                with self.subTest(text=text):
                    self.assertEqual(self.parse(text)["V"], old)
                    written = render_changes(text, {"V": "1.1.0"})
                    self.assertNotEqual(written, text)
                    self.assertEqual(self.parse(written)["V"], "1.1.0")
                    self.assertEqual(render_changes(written, {"V": old}), text)

    def test_writing_the_value_already_there_changes_nothing(self) -> None:
        for line, old in self.LINES:
            for text in self.texts(line):
                with self.subTest(text=text):
                    self.assertEqual(render_changes(text, {"V": old}), text)

    def test_inline_comment_and_trailing_whitespace_are_kept(self) -> None:
        self.assertEqual(render_changes("V=1.0.0  # pinned \r\n", {"V": "1.1.0"}), "V=1.1.0  # pinned \r\n")

    def test_quotes_are_kept_so_text_after_them_stays_ignored(self) -> None:
        # Written bare, the value would swallow "#note" and "\t# note".
        for text in ("V='1.0.0'#note\n", 'V="1.0.0"\t# note\n'):
            with self.subTest(text=text):
                written = render_changes(text, {"V": "1.1.0"})
                self.assertEqual(written, text.replace("1.0.0", "1.1.0"))
                self.assertEqual(self.parse(written)["V"], "1.1.0")

    def test_tab_hash_is_part_of_the_replaced_value(self) -> None:
        # Compose reads V=1.0.0<TAB># c as "1.0.0\t# c": all of it is the value.
        self.assertEqual(render_changes("V=1.0.0\t# c\n", {"V": "1.1.0"}), "V=1.1.0\n")

    def test_space_only_value_gets_the_new_value_after_the_equals(self) -> None:
        self.assertEqual(render_changes("V=   \n", {"V": "1.1.0"}), "V=1.1.0   \n")

    def test_definition_inside_a_multi_line_value_is_left_alone(self) -> None:
        text = 'KEY="-----BEGIN\nV=1.0.0\n-----END"\n'
        self.assertEqual(render_changes(text, {"V": "1.1.0"}), text + "V=1.1.0\n")
        self.assertEqual(render_changes(text, {"V": None}), text)

    def test_multi_line_value_is_replaced_or_removed_whole(self) -> None:
        text = "A=1\nK='x\ny' # c\nB=2\n"
        self.assertEqual(render_changes(text, {"K": "v1"}), "A=1\nK='v1' # c\nB=2\n")
        self.assertEqual(render_changes(text, {"K": None}), "A=1\nB=2\n")

    def test_only_a_newline_ends_a_line(self) -> None:
        text = "A=x V=1.0.0\n"
        self.assertEqual(render_changes(text, {"V": "1.1.0"}), text + "V=1.1.0\n")

    def test_appended_keys_use_the_file_line_ending(self) -> None:
        self.assertEqual(render_changes("A=1\r\nB=2\r\n", {"V": "1", "W": "2"}), "A=1\r\nB=2\r\nV=1\r\nW=2\r\n")
        self.assertEqual(render_changes("A=1\r\nB=2", {"V": "1"}), "A=1\r\nB=2\r\nV=1\r\n")
        self.assertEqual(render_changes("A=1\n", {"V": "1"}), "A=1\nV=1\n")

    def test_append_after_a_lone_carriage_return_starts_a_new_line(self) -> None:
        # Compose only breaks lines at \n: appending after "A=1\r" without one
        # would make V part of A's value.
        rendered = render_changes("A=1\r", {"V": "1"})
        self.assertEqual(rendered, "A=1\r\nV=1\r\n")
        self.assertEqual(self.parse(rendered), {"A": "1", "V": "1"})

    def test_append_to_a_bom_only_text(self) -> None:
        self.assertEqual(render_changes("﻿", {"V": "1"}), "﻿V=1\n")

    def test_bom_line_keeps_its_prefix_quotes_and_comment(self) -> None:
        self.assertEqual(render_changes("﻿export V='1.0.0' # c\r\n", {"V": "1.1.0"}),
                         "﻿export V='1.1.0' # c\r\n")

    def test_non_string_keys_and_values_are_refused(self) -> None:
        # A tampered journal or receipt could hold any JSON value.
        for changes in ({"V": 5}, {"V": ["1.0"]}, {"V": True}, {5: "1.0"}, {None: "1.0"}):
            with self.subTest(changes=changes):
                with self.assertRaises(EnvFileError) as caught:
                    render_changes("V=1.0.0\n", changes)
                self.assertIn("refusing", str(caught.exception))

    def test_invalid_key_error_quotes_the_key(self) -> None:
        with self.assertRaises(EnvFileError) as caught:
            render_changes("A=1\n", {"GOOD": "1.2.3", "X\nSECRET_KEY": "1.2.3"})
        message = str(caught.exception)
        self.assertEqual(message, "refusing to write unexpected key 'X\\nSECRET_KEY'")


class EnvFileWriteAdditions(_EnvCase):
    VERSION = "FUTURE_AGI_VERSION"

    def test_write_then_restore_gives_back_the_original_bytes(self) -> None:
        raw = (b"\xef\xbb\xbf# top\r\nPG_PASSWORD=caf\xe9\r\n"
               b"export FUTURE_AGI_VERSION='v1.0.0'  # pinned\r\nSECRET='a\\'b'\r\nLAST=1\r\n")
        path = self.dir / ".env"
        path.write_bytes(raw)
        found = read_env(path)[self.VERSION]
        write_changes(path, {self.VERSION: "v1.1.0", "FRONTEND_VERSION": "v2.0.0"})
        self.assertEqual(path.read_bytes(), raw.replace(b"v1.0.0", b"v1.1.0") + b"FRONTEND_VERSION=v2.0.0\r\n")
        write_changes(path, {self.VERSION: found, "FRONTEND_VERSION": None})
        self.assertEqual(path.read_bytes(), raw)

    def test_crlf_file_without_final_newline_gets_crlf_appends(self) -> None:
        path = self.write("A=1\r\nB=2")
        write_changes(path, {self.VERSION: "1.1.0"})
        self.assertEqual(path.read_bytes(), f"A=1\r\nB=2\r\n{self.VERSION}=1.1.0\r\n".encode())

    def test_bom_stays_when_its_line_is_removed(self) -> None:
        path = self.dir / ".env"
        path.write_bytes(b"\xef\xbb\xbf" + f"{self.VERSION}=1.0.0\nA=1\n".encode())
        write_changes(path, {self.VERSION: None})
        self.assertEqual(path.read_bytes(), b"\xef\xbb\xbfA=1\n")

    def test_non_utf8_file_with_nothing_to_change_is_untouched(self) -> None:
        path = self.dir / ".env"
        path.write_bytes(f"PG_PASSWORD=caf\xe9\n{self.VERSION}=1.0.0\n".encode("latin-1"))
        before = os.stat(path)
        with mock.patch.object(envfile.envcommit, "commit", side_effect=AssertionError("must not write")):
            write_changes(path, {self.VERSION: "1.0.0"})
        self.assertEqual(os.stat(path).st_mtime_ns, before.st_mtime_ns)

    def test_failed_candidate_creation_is_an_env_file_error(self):
        path = self.write(f"{self.VERSION}=1.0.0\n")
        real = os.open
        def opening(name, *args, **kwargs):
            if name == 'candidate':
                raise PermissionError(13, 'fixture')
            return real(name, *args, **kwargs)
        with mock.patch.object(envfile.os, 'open', side_effect=opening):
            with self.assertRaises(EnvFileError) as caught:
                write_changes(path, {self.VERSION: '1.1.0'})
        self.assertIn(str(path), str(caught.exception))
        self.assertEqual(path.read_bytes(), f"{self.VERSION}=1.0.0\n".encode())

    def test_every_failed_step_retains_evidence_and_reports_an_env_file_error(self):
        for operation in ('fdopen', 'fsync', 'fchmod', 'replace', 'link'):
            with self.subTest(step=operation):
                path = self.write(f"{self.VERSION}=1.0.0\n", name=operation + '/.env')
                before = path.read_bytes()
                with mock.patch.object(envfile.os, operation, side_effect=OSError(28, 'fixture')):
                    with self.assertRaises(EnvFileError):
                        write_changes(path, {self.VERSION: '1.1.0'})
                self.assertEqual(path.read_bytes(), before)

    def test_invalid_key_leaves_the_file_untouched(self) -> None:
        original = f"{self.VERSION}=1.0.0\n"
        path = self.write(original)
        with mock.patch.object(envfile.envcommit, "commit", side_effect=AssertionError("must not write")):
            with self.assertRaises(EnvFileError):
                write_changes(path, {self.VERSION: "1.1.0", "COMPOSE_FILE=evil.yml\nX": "1"})
        self.assertEqual(path.read_bytes(), original.encode())


# ============================================ envfile round 2: verbatim ==


class EnvFileRound2VerbatimRenderTests(_EnvCase):
    """render_changes(..., verbatim=True) puts back a value exactly as found."""

    INTERPOLATED = ("${RELEASE}", "${FUTURE_AGI_VERSION:-v1}", "$RELEASE", "${FUTURE_AGI_VERSION-v1.0.0}",
                    "v${RELEASE}", "${RELEASE:?set RELEASE first}")
    # How a version key may be written; {} is the value.
    FORMS = ("V={}", "export V={}", "  V = {}  # follows the backend", "V='{}'", 'V="{}" # c', "V='{}'\t# note")

    def texts(self, line: str):
        yield f"A=1\n{line}\nB=2\n"
        yield f"A=1\r\n{line}\r\nB=2\r\n"
        yield f"A=1\n{line}"
        yield f"﻿{line}\n"

    def test_interpolated_values_require_a_lexical_record_not_last_value_reencoding(self):
        for value in self.INTERPOLATED:
            for form in self.FORMS:
                for text in self.texts(form.format(value)):
                    with self.subTest(text=text):
                        path = self.write(text)
                        found = read_env(path)['V']
                        recovery = envfile.recovery_record(path, {'V': 'v1.1.0'})
                        write_changes(path, {'V': 'v1.1.0'}, recovery=recovery)
                        with self.assertRaises(EnvFileError):
                            render_changes(path.read_text(), {'V': found}, verbatim=True)
                        envfile.restore_changes(path, recovery)
                        self.assertEqual(path.read_bytes(), text.encode())

    def test_the_two_reported_setups_round_trip_with_exact_records(self):
        for text in ('RELEASE=v1.0.0\nFUTURE_AGI_VERSION=${RELEASE}\n',
                     'FUTURE_AGI_VERSION=v1.0.0\nFRONTEND_VERSION=${FUTURE_AGI_VERSION}\n'):
            with self.subTest(text=text):
                path = self.write(text)
                changes = {key: 'v1.1.0' for key in read_env(path) if key != 'RELEASE'}
                recovery = envfile.recovery_record(path, changes)
                write_changes(path, changes, recovery=recovery)
                self.assertNotEqual(path.read_bytes(), text.encode())
                envfile.restore_changes(path, recovery)
                self.assertEqual(path.read_bytes(), text.encode())

    def test_verbatim_dollar_is_ambiguous_even_if_the_last_value_matches(self):
        for value in self.INTERPOLATED:
            for form in self.FORMS:
                for text in self.texts(form.format(value)):
                    with self.subTest(text=text), self.assertRaises(EnvFileError):
                        render_changes(text, {'V': value}, verbatim=True)

    def test_verbatim_still_refuses_line_breaks_and_nul(self) -> None:
        for bad in ("${X}\nSECRET_KEY=x", "v1\n", "\n", "v1\r", "\r", "v1\r\nX=1", "\x00", "v1\x00", "${X}\x00"):
            with self.subTest(value=bad):
                with self.assertRaises(EnvFileError) as caught:
                    render_changes("FRONTEND_VERSION=v1.0.0\n", {"FRONTEND_VERSION": bad}, verbatim=True)
                self.assertIn("FRONTEND_VERSION", str(caught.exception))
                self.assertIn("refusing", str(caught.exception))

    def test_verbatim_still_refuses_bad_keys(self) -> None:
        for bad in ("", "BAD KEY", "A=B", "X\nSECRET_KEY", "COMPOSE_FILE=evil.yml\nFUTURE_AGI_VERSION",
                    "1ABC", "A-B", "# A", "export A", 5, None):
            with self.subTest(key=bad):
                with self.assertRaises(EnvFileError) as caught:
                    render_changes("A=1\n", {"GOOD": "v1", bad: "v1"}, verbatim=True)
                self.assertIn("refusing to write unexpected key", str(caught.exception))

    def test_verbatim_still_refuses_values_that_are_not_strings(self) -> None:
        for value in (5, ["${X}"], True, {"a": 1}):
            with self.subTest(value=value):
                with self.assertRaises(EnvFileError) as caught:
                    render_changes("V=v1\n", {"V": value}, verbatim=True)
                self.assertIn("refusing", str(caught.exception))

    def test_verbatim_refuses_a_value_that_would_read_back_differently(self) -> None:
        # The value goes inside the quotes the line already has (or bare).
        cases = (
            ("V='v1.1.0'\n", "it's"),
            ('V="v1.1.0"\n', 'a"b'),
            ('V="v1.1.0"\n', "a\\b"),  # "a\b" reads as a<BS>
            ("V=v1.1.0\n", "a #b"),
            ("V=v1.1.0\n", " lead"),
            ("V=v1.1.0\n", "trail "),
            ("V=v1.1.0\n", "'quoted'"),
            ("V=v1.1.0 # c\n", ""),  # V= # c reads as "# c"
            ("A=1\n", "x #y"),  # appended bare
        )
        for text, value in cases:
            with self.subTest(text=text, value=value):
                with self.assertRaises(EnvFileError) as caught:
                    render_changes(text, {"V": value}, verbatim=True)
                message = str(caught.exception)
                self.assertIn("refusing to write back V", message)
                self.assertIn("Set V in the env file by hand", message)

    def test_verbatim_refuses_a_value_that_would_swallow_the_lines_after_it(self) -> None:
        text = 'V=v1.1.0\nB="y"\nC=1\n'
        with self.assertRaises(EnvFileError) as caught:
            render_changes(text, {"V": '"x'}, verbatim=True)
        self.assertIn("refusing to write back V", str(caught.exception))

    def test_verbatim_dollar_cannot_be_appended_from_a_logical_value(self):
        with self.assertRaises(EnvFileError):
            render_changes('A=1\r\n', {'V': '${RELEASE}'}, verbatim=True)

    def test_verbatim_accepts_what_the_default_accepts_and_renders_it_the_same(self) -> None:
        text = "A=1\nV='v1.0.0' # c\nexport W=x\r\nR=gone\n"
        changes = {"V": "v1.1.0", "W": "", "R": None, "N": "1.2.3"}
        self.assertEqual(render_changes(text, changes, verbatim=True), render_changes(text, changes))

    def test_verbatim_is_keyword_only_and_off_by_default(self) -> None:
        with self.assertRaises(TypeError):
            render_changes("V=v1\n", {"V": "${X}"}, True)  # type: ignore[misc]
        with self.assertRaises(EnvFileError):
            render_changes("V=v1\n", {"V": "${X}"}, verbatim=False)


class EnvFileRound2VerbatimWriteTests(_EnvCase):
    """write_changes(..., verbatim=True): the before-restart restore and journal recovery."""

    RAW = (b"\xef\xbb\xbf# top\r\nPG_PASSWORD=caf\xe9\r\nRELEASE=v1.0.0\r\n"
           b"FUTURE_AGI_VERSION=${RELEASE}\r\n"
           b"export FRONTEND_VERSION=\"${FUTURE_AGI_VERSION:-v1}\"  # follows the backend\r\n"
           b"SECRET='a\\'b'\r\nLAST=1\r\n")

    def test_interpolated_values_are_put_back_byte_for_byte(self) -> None:
        path = self.dir / ".env"
        path.write_bytes(self.RAW)
        values = read_env(path)
        found = {key: values[key] for key in ("FUTURE_AGI_VERSION", "FRONTEND_VERSION")}
        found["AGENT_VERSION"] = None  # absent before the run: the restore removes it
        self.assertEqual(found["FUTURE_AGI_VERSION"], "${RELEASE}")
        self.assertEqual(found["FRONTEND_VERSION"], "${FUTURE_AGI_VERSION:-v1}")
        changes = {"FUTURE_AGI_VERSION": "v1.1.0", "FRONTEND_VERSION": "v1.1.0", "AGENT_VERSION": "v1.1.0"}
        recovery = envfile.recovery_record(path, changes)
        write_changes(path, changes, recovery=recovery)
        written = path.read_bytes()
        self.assertNotEqual(written, self.RAW)
        with self.assertRaises(EnvFileError):
            write_changes(path, found)
        self.assertEqual(path.read_bytes(), written)
        envfile.restore_changes(path, recovery)
        self.assertEqual(path.read_bytes(), self.RAW)

    def test_refused_verbatim_write_leaves_the_file_untouched(self) -> None:
        path = self.dir / ".env"
        path.write_bytes(self.RAW)
        for changes in ({"FUTURE_AGI_VERSION": "${RELEASE}\nCOMPOSE_FILE=evil.yml"},
                        {"FUTURE_AGI_VERSION": "${RELEASE}\x00"},
                        {"FUTURE_AGI_VERSION": "${RELEASE}", "BAD KEY": "${RELEASE}"},
                        {"FRONTEND_VERSION": 'a"b'}):
            with self.subTest(changes=changes):
                with mock.patch.object(envfile.envcommit, "commit", side_effect=AssertionError("must not write")):
                    with self.assertRaises(EnvFileError):
                        write_changes(path, changes, verbatim=True)
                self.assertEqual(path.read_bytes(), self.RAW)
                self.assertEqual(self.listing(self.dir), [".env"])

    def test_verbatim_write_of_the_values_already_there_does_not_touch_the_file(self) -> None:
        path = self.dir / ".env"
        path.write_bytes(self.RAW)
        before = os.stat(path)
        with mock.patch.object(envfile.envcommit, "commit", side_effect=AssertionError("must not write")), \
                self.assertRaises(EnvFileError):
            write_changes(path, {"FUTURE_AGI_VERSION": "${RELEASE}", "FRONTEND_VERSION": "${FUTURE_AGI_VERSION:-v1}",
                                 "AGENT_VERSION": None}, verbatim=True)
        self.assertEqual(os.stat(path).st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(path.read_bytes(), self.RAW)

    def test_verbatim_write_keeps_the_mode_and_follows_a_symlink(self) -> None:
        target = self.write("FUTURE_AGI_VERSION=${RELEASE}\n", "real.env", mode=0o640)
        link = self.dir / ".env"
        link.symlink_to(target.name)
        recovery = envfile.recovery_record(link, {"FUTURE_AGI_VERSION": "v1.1.0"})
        write_changes(link, {"FUTURE_AGI_VERSION": "v1.1.0"}, recovery=recovery)
        envfile.restore_changes(link, recovery)
        self.assertTrue(link.is_symlink())
        self.assertEqual(target.read_bytes(), b"FUTURE_AGI_VERSION=${RELEASE}\n")
        self.assertEqual(stat.S_IMODE(os.stat(target).st_mode), 0o640)



class TrustRound4OfflineSettingsTests(_SettingsCase):
    """S20: FUTURE_AGI_UPDATE_COSIGN_OFFLINE needs FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY (keyless needs the log)."""

    def expected(self, source: str) -> str:
        return (f"{OFFLINE} is true (from {source}), but {PUBLIC_KEY} is not set: offline verification needs a "
                "public key, because keyless verification (the default) checks Sigstore's transparency log over the "
                f"network. Set {PUBLIC_KEY} to the public key the release is signed with, or set {OFFLINE}=false")

    def test_offline_without_a_key_is_refused_from_every_source(self) -> None:
        for source in SOURCES:
            for value in ("true", "1", "yes", "on", " TRUE "):
                if source == "update.conf" and value != value.strip():
                    continue  # update.conf strips the value before it is read
                with self.subTest(source=source, value=value):
                    self.assertEqual(self.rejection(*self.via(source, OFFLINE, value)), self.expected(source))

    def test_a_key_that_is_blank_is_no_key(self) -> None:
        for key in ("", "   "):
            with self.subTest(key=repr(key)):
                self.assertEqual(self.rejection(environ={OFFLINE: "true", PUBLIC_KEY: key}),
                                 self.expected("environment"))
        # Offline with a key from any layer, or a false OFFLINE without one, loads.
        for source in SOURCES:
            with self.subTest(source=source):
                overrides, environ = self.via(source, PUBLIC_KEY, "/keys/cosign.pub")
                settings = self.load(overrides, dict(environ, **{OFFLINE: "true"}))
                self.assertIs(settings.boolean(OFFLINE), True)
                self.assertEqual(settings.get(PUBLIC_KEY), "/keys/cosign.pub")
        for value in ("false", "0", "no", "off", ""):
            with self.subTest(value=value):
                self.assertIs(self.load(environ={OFFLINE: value}).boolean(OFFLINE), False)

    def test_a_bad_boolean_is_still_reported_as_one(self) -> None:
        self.assertEqual(self.rejection(environ={OFFLINE: "maybe"}),
                         f"{OFFLINE} must be true or false, not 'maybe' (from environment)")
        self.assertIn(f"{OFFLINE} is true (from command line)",
                      self.rejection({OFFLINE: "on"}, {PUBLIC_KEY: "   "}))



class TrustRound5VerifySettingsTests(_SettingsCase):
    """G3 (T28, T29): OFFLINE without a public key is refused only for a command that verifies a signature.

    `status` and `rollback` never verify one (__main__ loads their settings
    with verify=False), so an operator with that setting can still roll back.
    """

    REFUSAL = TrustRound4OfflineSettingsTests.expected

    def load_for(self, verify: bool, overrides: Optional[Mapping[str, Optional[str]]] = None,
                 environ: Optional[Mapping[str, str]] = None) -> Settings:
        return load_settings(self.root, overrides, {} if environ is None else environ, verify=verify)

    def test_a_command_that_does_not_verify_accepts_offline_without_a_key_from_every_source(self) -> None:
        for source in SOURCES:
            for value in ("true", "1", "yes", "on"):
                with self.subTest(source=source, value=value):
                    overrides, environ = self.via(source, OFFLINE, value)
                    settings = self.load_for(False, overrides, environ)
                    # The value itself is kept as given: only the cross-check is left out.
                    self.assertIs(settings.boolean(OFFLINE), True)
                    self.assertEqual(settings.get(PUBLIC_KEY), "")
                    self.assertEqual(settings.sources[OFFLINE], source)
        self.assertIs(self.load_for(False, environ={OFFLINE: "true", PUBLIC_KEY: "   "}).boolean(OFFLINE), True)

    def test_a_command_that_verifies_still_refuses_it_and_that_is_the_default(self) -> None:
        for source in SOURCES:
            with self.subTest(source=source):
                overrides, environ = self.via(source, OFFLINE, "true")
                with self.assertRaises(SettingsError) as caught:
                    self.load_for(True, overrides, environ)
                self.assertEqual(str(caught.exception), self.REFUSAL(source))
                with self.assertRaises(SettingsError) as caught:
                    load_settings(self.root, overrides, environ)
                self.assertEqual(str(caught.exception), self.REFUSAL(source))

    def test_every_other_setting_is_still_checked_without_verification(self) -> None:
        for key, bad, expected in (
            (OFFLINE, "maybe", f"{OFFLINE} must be true or false, not 'maybe' (from environment)"),
            (POLICY, "weekly", f"{POLICY} must be one of patch, minor, major, not 'weekly' (from environment)"),
            (CHANNEL, "nightly", f"{CHANNEL} must be one of {', '.join(CHANNELS)}, not 'nightly' (from environment)"),
            (BACKUP_TIMEOUT, "0", f"{BACKUP_TIMEOUT} must be between 1 and 86400, not 0 (from environment)"),
            (WAIT_TIMEOUT, "soon", f"{WAIT_TIMEOUT} must be a whole number of seconds, not 'soon' (from environment)"),
            (MANIFEST_URL, "http://mirror.example/m.json",
             f"{MANIFEST_URL} must be an https:// URL or a local file path, not a 'http://' URL (from environment)"),
            (IDENTITY, "  ", f"{IDENTITY} must not be empty (from environment)"),
        ):
            with self.subTest(key=key):
                with self.assertRaises(SettingsError) as caught:
                    self.load_for(False, environ={key: bad})
                self.assertEqual(str(caught.exception), expected)

    def test_verify_is_keyword_only(self) -> None:
        self.assertIs(self.load_for(False).boolean(OFFLINE), False)
        with self.assertRaises(TypeError):
            load_settings(self.root, None, {}, False)  # type: ignore[misc]


# ================================================= hardening: Compose parsing ==


class EnvFileHardeningComposeParsingTests(_EnvCase):
    """More of Compose's dotenv rules, each checked against `docker-compose config` v5.1.4."""

    VERSION = "FUTURE_AGI_VERSION"

    def test_octal_escapes_decode_up_to_0377(self) -> None:
        # \0 and three octal digits up to 377 is one character; 400 and above stays as written without its 0.
        self.assertEqual(self.parse('A="x\\0377y"\nB="x\\0376y"\nC="x\\0350\\0400"\nD="\\0101\\0177"\n'),
                         {"A": "x\xffy", "B": "x\xfey", "C": "x\xe8\\400", "D": "A\x7f"})

    def test_a_leading_blank_line_defines_nothing_and_ends_at_the_newline(self) -> None:
        for text in ("\nA=1\nB=2 # c\n", "\r\nA=1\r\nB=2 # c\r\n", "\n\nA=1\nB=2 # c"):
            with self.subTest(text=text):
                self.assertEqual(self.parse(text), {"A": "1", "B": "2"})
                self.assertEqual(example_keys(self.write(text, name=".env.example")), ["A", "B"])
                self.assertEqual(render_changes(text, {"A": "v9"}), text.replace("A=1", "A=v9"))
                self.assertEqual(render_changes(text, {"B": None}), text[:text.index("B=")])

    def test_a_colon_separates_a_key_from_its_value_like_an_equals_sign(self) -> None:
        # Compose also reads the YAML-like KEY: value. The first '=' or ':' ends the key.
        text = ("A: v1\nB:v2\nexport C : 'x y' # c\nD:=1\nE::1\nF=x:y\nG:\nH: \"multi\nline\"\n"
                "http://example.com/a\nI=2\nI: 3\n")
        self.assertEqual(self.parse(text), {"A": "v1", "B": "v2", "C": "x y", "D": "=1", "E": ":1", "F": "x:y",
                                            "G": "", "H": "multi\nline", "http": "//example.com/a", "I": "3"})
        self.assertEqual(example_keys(self.write(text, name=".env.example")),
                         ["A", "B", "C", "D", "E", "F", "G", "H", "http", "I"])

    def test_a_colon_definition_is_the_one_compose_reads_and_the_one_rewritten(self) -> None:
        # An `=` line followed by a `:` line of the same key: Compose reads the second.
        path = self.write(f"{self.VERSION}=v1.0.0\nA=1\nexport {self.VERSION}: 'v1.0.1'  # hotfix\n")
        self.assertEqual(read_env(path)[self.VERSION], "v1.0.1")
        write_changes(path, {self.VERSION: "v1.1.0"})
        self.assertEqual(path.read_bytes(),
                         f"{self.VERSION}=v1.1.0\nA=1\nexport {self.VERSION}: 'v1.1.0'  # hotfix\n".encode())
        self.assertEqual(read_env(path)[self.VERSION], "v1.1.0")
        write_changes(path, {self.VERSION: "v1.0.1"})
        write_changes(path, {self.VERSION: None})
        self.assertEqual(path.read_bytes(), b"A=1\n")

    def test_a_colon_only_definition_is_replaced_in_place_not_appended(self) -> None:
        text = f"{self.VERSION}: v1.0.0  # pinned\r\nA=1\r\n"
        rendered = render_changes(text, {self.VERSION: "v1.1.0"})
        self.assertEqual(rendered, f"{self.VERSION}: v1.1.0  # pinned\r\nA=1\r\n")
        self.assertEqual(render_changes(rendered, {self.VERSION: "v1.0.0"}, verbatim=True), text)

    def test_compose_reads_another_definition_after_a_closing_quote(self) -> None:
        # Compose does not skip the rest of a line after a closing quote: a definition there counts.
        cases = (
            ('A="x" B=1\n', {"A": "x", "B": "1"}),
            ('A="x"B=1\n', {"A": "x", "B": "1"}),
            ("A='x' export B=1\n", {"A": "x", "B": "1"}),
            ('A="x"\tB="y" C=2 # c\nD=4\n', {"A": "x", "B": "y", "C": "2", "D": "4"}),
            ('A="x" B="multi\nline" C=3\nD=4\n', {"A": "x", "B": "multi\nline", "C": "3", "D": "4"}),
            ('A="x" B=1\r\nC=3\r\n', {"A": "x", "B": "1", "C": "3"}),
            ('A="x" B:1\n', {"A": "x", "B": "1"}),
            # The last line, without a line ending.
            ('A="x" B=1', {"A": "x", "B": "1"}),
            ("C=3\nA='x' B=\"y\"", {"C": "3", "A": "x", "B": "y"}),
            # A comment or a word that defines nothing still ends the line.
            ('A="x" # B=1\nC=3\n', {"A": "x", "C": "3"}),
            ('A="x" junk\nB=2\n', {"A": "x", "B": "2"}),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(self.parse(text), expected)
                self.assertEqual(example_keys(self.write(text, name=".env.example")), list(expected))

    def test_a_version_key_after_a_closing_quote_is_the_one_compose_reads_and_is_rewritten(self) -> None:
        path = self.write(f"{self.VERSION}=v1.0.0\nNAME=\"fagi\" {self.VERSION}=v1.0.1 # hotfix\nA=1\n")
        self.assertEqual(read_env(path)[self.VERSION], "v1.0.1")
        write_changes(path, {self.VERSION: "v1.1.0"})
        self.assertEqual(path.read_bytes(),
                         f"{self.VERSION}=v1.1.0\nNAME=\"fagi\" {self.VERSION}=v1.1.0 # hotfix\nA=1\n".encode())
        write_changes(path, {self.VERSION: "v0.9.0"}, verbatim=True)
        self.assertEqual(read_env(path), {self.VERSION: "v0.9.0", "NAME": "fagi", "A": "1"})
        write_changes(path, {self.VERSION: None})
        # Only the definition goes: the quoted value before it, the comment and the line ending stay.
        self.assertEqual(path.read_bytes(), b'NAME="fagi" # hotfix\nA=1\n')
        self.assertEqual(read_env(path), {"NAME": "fagi", "A": "1"})

    def test_removing_definitions_that_share_a_line_keeps_the_others_and_the_line_ending(self) -> None:
        text = 'A="x" B=1 # c\r\nC=\'y\' D="z"\r\nE=5\r\n'
        cases = (
            ({"B": None}, 'A="x" # c\r\nC=\'y\' D="z"\r\nE=5\r\n', {"A": "x", "C": "y", "D": "z", "E": "5"}),
            ({"A": None}, ' B=1 # c\r\nC=\'y\' D="z"\r\nE=5\r\n', {"B": "1", "C": "y", "D": "z", "E": "5"}),
            ({"D": None}, 'A="x" B=1 # c\r\nC=\'y\'\r\nE=5\r\n', {"A": "x", "B": "1", "C": "y", "E": "5"}),
            ({"C": None}, 'A="x" B=1 # c\r\n D="z"\r\nE=5\r\n', {"A": "x", "B": "1", "D": "z", "E": "5"}),
            ({"C": None, "D": None}, 'A="x" B=1 # c\r\n\r\nE=5\r\n', {"A": "x", "B": "1", "E": "5"}),
        )
        for changes, expected_text, expected_values in cases:
            with self.subTest(changes=changes):
                rendered = render_changes(text, changes)
                self.assertEqual(rendered, expected_text)
                self.assertEqual(self.parse(rendered), expected_values)
        self.assertEqual(render_changes(text, {"B": "v2", "D": "v3"}),
                         'A="x" B=v2 # c\r\nC=\'y\' D="v3"\r\nE=5\r\n')
        # The same on a last line without a line ending.
        self.assertEqual(render_changes('E=5\nC=\'y\' D="z"', {"D": None}), "E=5\nC='y'")
        self.assertEqual(render_changes('E=5\nC=\'y\' D="z"', {"C": None}), 'E=5\n D="z"')

    def test_a_definition_alone_on_the_first_line_is_removed_whole_when_the_file_ends_with_a_quote(self) -> None:
        # The first line follows no closing quote, whatever the file's last character is.
        for text, expected in (('A=1\nB="x"', 'B="x"'), ('A="1"\nB=\'x\'', "B='x'"), ('A=1 # c\nB="x"', 'B="x"')):
            with self.subTest(text=text):
                self.assertEqual(render_changes(text, {"A": None}), expected)

    def test_a_verbatim_value_that_would_start_a_definition_after_its_quote_is_refused(self) -> None:
        path = self.write(f'{self.VERSION}="v1.0.0"\nA=1\n')
        with self.assertRaises(EnvFileError) as caught:
            write_changes(path, {self.VERSION: f'v1" {self.VERSION}=evil'}, verbatim=True)
        self.assertTrue(str(caught.exception).startswith(f"refusing to write back {self.VERSION}: "),
                        str(caught.exception))
        self.assertEqual(path.read_bytes(), f'{self.VERSION}="v1.0.0"\nA=1\n'.encode())


# =================================================== hardening: render_changes ==


class EnvFileHardeningRenderTests(_EnvCase):
    def test_append_after_a_crlf_blank_first_line_keeps_crlf(self) -> None:
        self.assertEqual(render_changes("\r\nA=1", {"B": "2"}), "\r\nA=1\r\nB=2\r\n")
        self.assertEqual(render_changes("\r\n", {"B": "2"}), "\r\nB=2\r\n")
        self.assertEqual(render_changes("\n", {"B": "2"}), "\nB=2\n")
        self.assertEqual(render_changes("\nA=1", {"B": "2"}), "\nA=1\nB=2\n")

    def test_verbatim_refusal_names_the_key_that_would_read_back_differently(self) -> None:
        # Only B's value cannot go back inside its double quotes; A, listed first, can.
        text = 'A=v1.1.0\nB="v1.1.0"\nC=1\n'
        for changes in ({"A": "v0", "B": 'a"b'}, {"A": "v0", "B": 'a"b', "C": None},
                        {"C": None, "A": "v0", "B": 'a"b'}):
            with self.subTest(changes=changes):
                with self.assertRaises(EnvFileError) as caught:
                    render_changes(text, changes, verbatim=True)
                self.assertEqual(str(caught.exception),
                                 "refusing to write back B: 'a\"b' would not read back as the same value where B is "
                                 "defined. Set B in the env file by hand")

    def test_verbatim_refusal_names_the_first_of_several_keys_in_the_order_given(self) -> None:
        text = 'A="v1.1.0"\nB="v1.1.0"\n'
        for first, second in (("A", "B"), ("B", "A")):
            with self.subTest(first=first):
                with self.assertRaises(EnvFileError) as caught:
                    render_changes(text, {first: 'p"q', second: 'a"b'}, verbatim=True)
                self.assertTrue(str(caught.exception).startswith(f"refusing to write back {first}: 'p\"q' "),
                                str(caught.exception))


# ============================================ hardening: write_changes edges ==


class EnvFileHardeningWriteEdgeTests(_EnvCase):
    """The rarely taken paths of write_changes and _fsync_dir: a file that vanishes, a
    platform without chown, a failed clean-up, and a directory that cannot be synced."""

    VERSION = "FUTURE_AGI_VERSION"

    def test_a_file_removed_after_it_was_read_is_an_env_file_error_naming_the_given_path(self) -> None:
        target = self.write(f"A=1\n{self.VERSION}=1.0.0\n", name="real.env")
        link = self.dir / ".env"
        link.symlink_to(target.name)
        real_read = envfile._read_text

        def read_then_vanish(path: Path, shown: Path) -> str:
            text = real_read(path, shown)
            os.unlink(path)  # another process removes it between the read and the stat
            return text

        with mock.patch.object(envfile, "_read_text", read_then_vanish), \
                mock.patch.object(envfile.envcommit, "commit", side_effect=AssertionError("must not write")):
            with self.assertRaises(EnvFileError) as caught:
                write_changes(link, {self.VERSION: "1.1.0"})
        self.assertTrue(str(caught.exception).startswith(f"cannot write {link}: "), str(caught.exception))
        self.assertIsInstance(caught.exception.__cause__, FileNotFoundError)
        self.assertFalse(target.exists())
        self.assertEqual(self.listing(self.dir), [".env"])

    def test_a_platform_without_chown_still_writes_the_file_with_its_mode(self) -> None:
        path = self.write(f"A=1\n{self.VERSION}=1.0.0\n", mode=0o640)
        saved = getattr(os, "chown", None)
        if saved is not None:
            del os.chown
        try:
            self.assertFalse(hasattr(envfile.os, "chown"))
            write_changes(path, {self.VERSION: "1.1.0"})
        finally:
            if saved is not None:
                os.chown = saved
        self.assertEqual(path.read_bytes(), f"A=1\n{self.VERSION}=1.1.0\n".encode())
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o640)
        self.assertEqual(self.listing(self.dir), ['.env', '.futureagi-updates'])

    def test_ctrl_c_after_exchange_keeps_both_inodes_and_propagates(self):
        path = self.write(f"{self.VERSION}=1.0.0\n", mode=0o640)
        real = envfile.envcommit.exchange
        def exchange(a, b, c, d):
            real(a, b, c, d)
            if b == 'slot':
                raise KeyboardInterrupt
        with mock.patch.object(envfile.envcommit, 'exchange', side_effect=exchange):
            with self.assertRaises(KeyboardInterrupt):
                write_changes(path, {self.VERSION: '1.1.0'})
        self.assertEqual(path.read_bytes(), f"{self.VERSION}=1.1.0\n".encode())
        slot, = self.dir.glob('.futureagi-updates/env-commits/*/*/slot')
        self.assertEqual(slot.read_bytes(), f"{self.VERSION}=1.0.0\n".encode())

    def test_exchange_error_never_unlinks_displaced_data(self):
        path = self.write(f"{self.VERSION}=1.0.0\n")
        real = envfile.envcommit.exchange
        def exchange(a, b, c, d):
            real(a, b, c, d)
            if b == 'slot':
                raise OSError(5, 'fixture after syscall')
        with mock.patch.object(envfile.envcommit, 'exchange', side_effect=exchange):
            with self.assertRaises(EnvFileError):
                write_changes(path, {self.VERSION: '1.1.0'})
        slot, = self.dir.glob('.futureagi-updates/env-commits/*/*/slot')
        self.assertEqual(slot.read_bytes(), f"{self.VERSION}=1.0.0\n".encode())
        self.assertEqual(path.read_bytes(), f"{self.VERSION}=1.1.0\n".encode())

    def test_directory_open_failure_is_reported_after_replace(self) -> None:
        path = self.write(f"{self.VERSION}=1.0.0\n")
        real_open = os.open
        refused = []

        def open_(name, flags, *args, **kwargs):
            if os.path.realpath(str(name)) == os.path.realpath(str(self.dir)):
                refused.append(flags)
                raise PermissionError(13, "Permission denied")
            return real_open(name, flags, *args, **kwargs)

        with mock.patch.object(envfile.os, "open", open_), self.assertRaises(EnvFileError):
            write_changes(path, {self.VERSION: "1.1.0"})
        self.assertEqual(len(refused), 1)
        self.assertEqual(path.read_bytes(), f"{self.VERSION}=1.0.0\n".encode())
        self.assertEqual(self.listing(self.dir), [".env"])

    def test_directory_fsync_failure_is_reported_and_fd_is_closed(self) -> None:
        path = self.write(f"{self.VERSION}=1.0.0\n")
        real_fsync, real_close = os.fsync, os.close
        directory_fds, closed = [], []

        def fsync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                directory_fds.append(fd)
                raise OSError(22, "Invalid argument")  # some file systems cannot sync a directory
            return real_fsync(fd)

        def close(fd):
            closed.append(fd)
            return real_close(fd)

        with mock.patch.object(envfile.os, "fsync", fsync), mock.patch.object(envfile.os, "close", close), \
                self.assertRaises(EnvFileError):
            write_changes(path, {self.VERSION: "1.1.0"})
        self.assertEqual(len(directory_fds), 1)
        self.assertIn(directory_fds[0], closed)
        self.assertEqual(path.read_bytes(), f"{self.VERSION}=1.0.0\n".encode())
        self.assertEqual(self.listing(self.dir), ['.env', '.futureagi-updates'])

    def test_both_directories_are_synced_after_exchange_and_closed(self):
        path = self.write(f"{self.VERSION}=1.0.0\n")
        events = []
        real_sync, real_exchange, real_close = os.fsync, envfile.envcommit.exchange, os.close
        def sync(fd):
            events.append(('sync-dir' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'sync-file', fd))
            return real_sync(fd)
        def exchange(a, b, c, d):
            result = real_exchange(a, b, c, d)
            if b == 'slot':
                events.append(('exchange', (a, c)))
            return result
        def close(fd):
            events.append(('close', fd))
            return real_close(fd)
        with mock.patch.object(envfile.os, 'fsync', side_effect=sync), \
                mock.patch.object(envfile.envcommit, 'exchange', side_effect=exchange), \
                mock.patch.object(envfile.os, 'close', side_effect=close):
            write_changes(path, {self.VERSION: '1.1.0'})
        boundary = next(i for i, event in enumerate(events) if event[0] == 'exchange')
        for fd in events[boundary][1]:
            self.assertIn(('sync-dir', fd), events[boundary + 1:])
            self.assertIn(('close', fd), events[boundary + 1:])

    def test_fsync_dir_of_a_missing_directory_reports_failure(self):
        with self.assertRaises(FileNotFoundError):
            envfile._fsync_dir(self.dir / 'missing')

    def test_the_new_bytes_are_in_the_temporary_file_when_it_is_synced(self) -> None:
        # A crash after the rename must find the whole new file on disk, not a buffered part of it.
        path = self.write(f"A=1\n{self.VERSION}=1.0.0\n")
        synced = []
        real_fsync = os.fsync

        def fsync(fd):
            if stat.S_ISREG(os.fstat(fd).st_mode):
                try:
                    synced.append(os.pread(fd, 1 << 16, 0))
                except OSError as exc:
                    if exc.errno != 9:
                        raise  # Metadata files are opened write-only; candidate is read/write.
            return real_fsync(fd)

        with mock.patch.object(envfile.os, "fsync", fsync):
            write_changes(path, {self.VERSION: "1.1.0"})
        expected = f"A=1\n{self.VERSION}=1.1.0\n".encode()
        self.assertIn(expected, synced)
        self.assertEqual(path.read_bytes(), expected)

    def test_every_permission_bit_of_the_original_mode_is_kept(self) -> None:
        for mode in (0o400, 0o600, 0o601, 0o640, 0o645, 0o711, 0o755):
            with self.subTest(mode=oct(mode)):
                path = self.write(f"{self.VERSION}=1.0.0\n", name=f"env-{mode:o}", mode=mode)
                write_changes(path, {self.VERSION: "1.1.0"})
                self.assertEqual(path.read_bytes(), f"{self.VERSION}=1.1.0\n".encode())
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), mode)


# ============================================== hardening: Compose's spaces ==


class EnvFileHardeningComposeSpacesTests(_EnvCase):
    """Compose's white space is Go's, not Python's: \\s and str.strip also take \\x1c-\\x1f and more."""

    VERSION = "FUTURE_AGI_VERSION"

    def test_export_must_be_followed_by_a_tab_space_form_feed_or_carriage_return(self) -> None:
        # Compose's `^export\s+` is Go's \s: a vertical tab, NEL or no-break space makes `export\vA` the key.
        # \u3000 may not be part of a key: Compose rejects that line.
        text = ("export\vA=1\nexport\x85B=2\nexport\xa0C=3\nexport\fD=4\nexport\rE=5\nexport \vF=6\n"
                "export\t\x85\xa0G=7\nexport\u3000H=8\n")
        self.assertEqual(self.parse(text), {"export\vA": "1", "export\x85B": "2", "export\xa0C": "3",
                                            "D": "4", "E": "5", "F": "6", "G": "7"})
        self.assertEqual(render_changes(text, {"A": "v9"}), text + "A=v9\n")
        self.assertEqual(render_changes(text, {"G": "v9"}), text.replace("G=7", "G=v9"))

    def test_a_version_key_after_a_vertical_tab_export_is_appended_not_rewritten(self) -> None:
        path = self.write(f"export\v{self.VERSION}=v1.0.0\n")
        self.assertEqual(read_env(path), {f"export\v{self.VERSION}": "v1.0.0"})
        write_changes(path, {self.VERSION: "v1.1.0"})
        self.assertEqual(path.read_bytes(), f"export\v{self.VERSION}=v1.0.0\n{self.VERSION}=v1.1.0\n".encode())
        self.assertEqual(read_env(path), {f"export\v{self.VERSION}": "v1.0.0", self.VERSION: "v1.1.0"})

    def test_only_gos_white_space_starts_a_line_or_separates_a_key(self) -> None:
        # Compose rejects a line starting with \x1c or a key followed by \u3000: neither is a definition here.
        text = ("\x1cA=1\n\x1fB=2\nC\u3000=3\nD\x1c=4\n\vE=5\n\x85F=6\n\xa0G=7\n\u3000H=8\n\u2028I=9\n\u2003J=10\n"
                "K\x85=11\nL\xa0:12\nM\v\f\r=13\n")
        self.assertEqual(self.parse(text), {"E": "5", "F": "6", "G": "7", "H": "8", "I": "9", "J": "10",
                                            "K": "11", "L": "12", "M": "13"})

    def test_an_unquoted_value_keeps_trailing_separator_controls(self) -> None:
        # Go's unicode.IsSpace trims \u3000, NEL and the like, but not \x1c-\x1f (str.rstrip would).
        text = "A=v1\x1c\nB=v2\x1d \nC=v3\x1e\t\nD=v4\x1f\nE=v5\u3000\nF=v6\x85\nG=v7\xa0\u2028\nH=v8 \x1f # c\n"
        self.assertEqual(self.parse(text), {"A": "v1\x1c", "B": "v2\x1d", "C": "v3\x1e", "D": "v4\x1f",
                                            "E": "v5", "F": "v6", "G": "v7", "H": "v8 \x1f"})
        rendered = render_changes(text, {"A": "w1", "B": "w2", "E": "w5", "H": "w8"})
        self.assertEqual(rendered, "A=w1\nB=w2 \nC=v3\x1e\t\nD=v4\x1f\nE=w5\u3000\nF=v6\x85\nG=v7\xa0\u2028\n"
                                   "H=w8 # c\n")

    def test_a_definition_after_a_closing_quote_may_follow_any_go_white_space(self) -> None:
        text = 'A="x"\vB=1\nC=\'y\'\u3000D=2\nE="z" \x85 F=3\nG="w"\xa0H=4\n'
        self.assertEqual(self.parse(text), {"A": "x", "B": "1", "C": "y", "D": "2", "E": "z", "F": "3", "G": "w",
                                            "H": "4"})
        # Compose rejects \x1c there; it does not start a definition here either.
        self.assertEqual(self.parse('G="w"\x1cH=4\n'), {"G": "w"})


# ================================= hardening: every key Compose reads ==


class EnvFileComposeKeyTests(_EnvCase):
    """Every key Compose reads is a definition, so its value is read past even where no variable can name it.

    Compose's key ends at the first '=' or ':' and may hold letters and digits
    of any script, '_', '.', '-', '[', ']' and spaces other than ' ' (checked
    against docker-compose v2.24.0 and v5.6.0).
    """

    VERSION = "FUTURE_AGI_VERSION"

    def test_keys_no_variable_can_name_are_read_like_any_other(self) -> None:
        text = "a.b=1\nmy-key=2\n1A=3\nÄ=4\nA[0]=5\nA\tB=6\nexport\vX=7\nexport\xa0Y: 8\nOK=9\n"
        expected = {"a.b": "1", "my-key": "2", "1A": "3", "Ä": "4", "A[0]": "5", "A\tB": "6",
                    "export\vX": "7", "export\xa0Y": "8", "OK": "9"}
        self.assertEqual(self.parse(text), expected)
        self.assertEqual(example_keys(self.write(text, name=".env.example")), list(expected))

    def test_a_quoted_value_of_such_a_key_is_not_read_line_by_line(self) -> None:
        # What looks like a definition inside the value is part of the value: rewriting the
        # version key must leave it alone.
        for key, quote in (("a.b", '"'), ("my-key", "'"), ("1A", '"'), ("Ä", "'"), ("A[0]", '"'),
                           ("A\tB", '"'), ("export\vX", "'")):
            inner = f"\n{self.VERSION}=v0\n"
            text = f"{key}={quote}{inner}{quote}\n{self.VERSION}=v1\n"
            with self.subTest(key=key):
                self.assertEqual(self.parse(text), {key: inner, self.VERSION: "v1"})
                self.assertEqual(render_changes(text, {self.VERSION: "v9"}),
                                 f"{key}={quote}{inner}{quote}\n{self.VERSION}=v9\n")
                self.assertEqual(render_changes(text, {self.VERSION: None}), f"{key}={quote}{inner}{quote}\n")

    def test_a_definition_after_such_a_keys_closing_quote_is_the_one_rewritten(self) -> None:
        text = f'export\vX="v1" {self.VERSION}=v2 # c\nA=1\n'
        self.assertEqual(self.parse(text), {"export\vX": "v1", self.VERSION: "v2", "A": "1"})
        self.assertEqual(render_changes(text, {self.VERSION: "v9"}), f'export\vX="v1" {self.VERSION}=v9 # c\nA=1\n')
        self.assertEqual(render_changes(text, {self.VERSION: None}), 'export\vX="v1" # c\nA=1\n')

    def test_an_empty_key_defines_nothing_but_its_value_is_read_past(self) -> None:
        # export, then a space, then '=' or ':' is an empty key too (v2.24 rejects the file, v5.6 ignores it).
        for prefix in ("=", " = ", "export =", "export\t:", "export :"):
            text = f'{prefix}"\n{self.VERSION}=v0\n"\n{self.VERSION}=v1\n'
            with self.subTest(prefix=prefix):
                self.assertEqual(self.parse(text), {self.VERSION: "v1"})
                self.assertEqual(render_changes(text, {self.VERSION: "v9"}),
                                 f'{prefix}"\n{self.VERSION}=v0\n"\n{self.VERSION}=v9\n')
        self.assertEqual(self.parse("export=1\nexport:2\nexport =3\n"), {"export": "2"})

    def test_a_bare_key_defines_nothing_and_the_next_line_is_read(self) -> None:
        # Compose takes a bare KEY from the environment, which bin/update never passes a version key in.
        text = f'{self.VERSION}\nB="\n{self.VERSION}=v0\n"\n{self.VERSION}=v1\n'
        self.assertEqual(self.parse(text), {"B": f"\n{self.VERSION}=v0\n", self.VERSION: "v1"})
        self.assertEqual(render_changes(text, {self.VERSION: "v9"}), text.replace("=v1\n", "=v9\n"))


class EnvFileUnterminatedVerbatimTests(_EnvCase):
    VERSION = "FUTURE_AGI_VERSION"

    def test_a_verbatim_value_that_opens_a_quote_it_never_closes_is_refused(self) -> None:
        # Bare, it would read back as itself here, but Compose rejects a quote that is never closed.
        for original in (f"{self.VERSION}=v1.0.0\nA=1\n", f"A=1\n{self.VERSION}=v1.0.0", f"{self.VERSION}= \nA=1\n"):
            for value in ('"v1', "'v1", '"', "'v1 B=2"):
                with self.subTest(original=original, value=value):
                    path = self.write(original)
                    with self.assertRaises(EnvFileError) as caught:
                        write_changes(path, {self.VERSION: value}, verbatim=True)
                    self.assertTrue(str(caught.exception).startswith(f"refusing to write back {self.VERSION}: "))
                    self.assertEqual(path.read_bytes(), original.encode())

    def test_a_file_that_already_has_an_unterminated_quote_elsewhere_can_still_be_restored(self) -> None:
        path = self.write(f'{self.VERSION}=v1.1.0\nB="open\n')
        write_changes(path, {self.VERSION: "v0.9.0"}, verbatim=True)
        self.assertEqual(path.read_bytes(), f'{self.VERSION}=v0.9.0\nB="open\n'.encode())


class EnvFileHardeningFileEndTests(_EnvCase):
    """Where a file starts or ends in the middle of what the parser looks for (checked against Compose)."""

    VERSION = "FUTURE_AGI_VERSION"

    def test_go_white_space_ends_at_u200a(self) -> None:
        # U+2000 to U+200A are trimmed off an unquoted value; U+1FFF and U+200B (no white space) are not.
        text = "A=v1῿\nB=v2 \nC=v3 \nD=v4​\nE=v5 \n"
        self.assertEqual(self.parse(text), {"A": "v1῿", "B": "v2", "C": "v3", "D": "v4​", "E": "v5"})

    def test_a_last_comment_without_a_line_ending_ends_the_file(self) -> None:
        for text in (f"{self.VERSION}=v1\n# trailing comment ", f"{self.VERSION}=v1\n#", f"{self.VERSION}=v1 # c\n  # x\t"):
            with self.subTest(text=text):
                self.assertEqual(self.parse(text), {self.VERSION: "v1"})
                self.assertEqual(render_changes(text, {self.VERSION: "v2"}), text.replace("=v1", "=v2"))

    def test_an_export_that_ends_the_file_defines_nothing(self) -> None:
        # Compose rejects such a file ("zero length string"); here it simply ends.
        for text in ("A=1\nexport ", "A=1\nexport\t", "A=1\nexport \t\v"):
            with self.subTest(text=text):
                self.assertEqual(self.parse(text), {"A": "1"})
                self.assertEqual(render_changes(text, {"B": "v1"}), text + "\nB=v1\n")

    def test_removing_the_first_definition_after_a_blank_first_line_keeps_that_line(self) -> None:
        for text, expected in ((f"\n{self.VERSION}=v1\nB=2\n", "\nB=2\n"),
                               (f"\r\n{self.VERSION}='v1'\r\nB=2\r\n", "\r\nB=2\r\n"),
                               (f"\n\n  {self.VERSION}: v1 # c\nB=2", "\n\nB=2")):
            with self.subTest(text=text):
                self.assertEqual(render_changes(text, {self.VERSION: None}), expected)

    def test_removing_a_quoted_definition_on_the_last_line_without_a_line_ending(self) -> None:
        for text in (f'A=1\n{self.VERSION}="v1"', f"A=1\n{self.VERSION}='v1' # c", f'A=1\n  {self.VERSION}="v1"\t'):
            with self.subTest(text=text):
                self.assertEqual(render_changes(text, {self.VERSION: None}), "A=1\n")
                self.assertEqual(self.parse(render_changes(text, {self.VERSION: None})), {"A": "1"})


class ExactEnvRecoveryTests(_EnvCase):
    """The private inverse is byte-exact, idempotent and never merges an operator edit."""

    def transaction(self, raw, changes):
        path = self.dir / '.env'
        path.write_bytes(raw)
        path.chmod(0o640)
        record = envfile.recovery_record(path, changes)
        # Exercise the durable JSON representation, not a Python-only object.
        record = json.loads(json.dumps(record, sort_keys=True))
        envfile.write_changes(path, changes, recovery=record)
        return path, record

    def test_every_definition_append_remove_and_unrelated_byte_round_trips(self):
        cases = (
            (b'\xef\xbb\xbf# top\r\nSECRET_KEY=keep-secret-123\xff\r\n'
             b' export V : "v0" # first\r\nMIDDLE=${V}\r\nV= v1 # last', {'V': 'v2', 'NEW': 'v3'}),
            (b'A="x" V="\\$X" W=one\r\nV=\'literal$X\' # comment\r\nTAIL=no-newline', {'V': None, 'NEW': 'v3'}),
            (b'V="multi\r\nline\\n\\${X" # comment\r\nZ=x\r', {'V': 'v2', 'NEW': 'v3'}),
            (b'V=v1', {'V': None, 'NEW': 'v3'}),
            (b'\xef\xbb\xbf', {'V': 'v2'}),
            (b'', {'V': 'v2'}),
        )
        for raw, changes in cases:
            with self.subTest(raw=raw, changes=changes):
                path, record = self.transaction(raw, changes)
                self.assertNotIn('keep-secret-123', json.dumps(record))
                envfile.restore_changes(path, record)
                self.assertEqual(path.read_bytes(), raw)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
                envfile.restore_changes(path, record)
                self.assertEqual(path.read_bytes(), raw)
                self.assertFalse(list(self.dir.glob('.env.futureagi-update-*')))

    def test_expected_preimage_binds_the_record_to_the_exact_bytes_that_were_approved(self):
        import hashlib
        # A permitted path can contain a short version substring; the secrecy
        # oracle must match the distinct env value, not random tempfile bytes.
        path = self.write('A=1\nV=v1\n', name='v9.env', mode=0o600)
        approved = hashlib.sha256(path.read_bytes()).hexdigest()
        bound = envfile.recovery_record(path, {'V': 'v2'}, expected_before_sha256=approved)
        plain = envfile.recovery_record(path, {'V': 'v2'})
        for record in (bound, plain):
            record.pop('commit')  # random operation ids; everything else is the same inverse
        self.assertEqual(bound, plain)
        self.assertEqual(bound['before_sha256'], approved)
        # An operator edit after the approval is refused (typed), never adopted as the baseline, and no byte moves.
        private_value = 'private-edited-env-value-9f71e83c'
        path.write_bytes(path.read_bytes() + ('V='+private_value+'\n').encode())
        edited = path.read_bytes()
        entries = set(self.dir.iterdir())
        with self.assertRaises(envfile.EnvPreimageError) as caught:
            envfile.recovery_record(path, {'V': 'v2'}, expected_before_sha256=approved)
        self.assertIsInstance(caught.exception, EnvFileError)
        self.assertNotIn(private_value, str(caught.exception))
        self.assertEqual(path.read_bytes(), edited)
        self.assertEqual(set(self.dir.iterdir()), entries)
        # A byte-for-byte restore of the approved text is accepted again; comments count as bytes too.
        path.write_bytes(b'A=1\nV=v1\n# comment\n')
        with self.assertRaises(envfile.EnvPreimageError):
            envfile.recovery_record(path, {'V': 'v2'}, expected_before_sha256=approved)

    def test_record_before_write_is_recoverable_without_mutating_bytes(self):
        path = self.write('V="\\$X"\nV=v1\n', mode=0o600)
        before = path.read_bytes()
        record = envfile.recovery_record(path, {'V': 'v2'})
        envfile.restore_changes(path, record)
        self.assertEqual(path.read_bytes(), before)

    def test_concurrent_bytes_mode_and_symlink_edits_are_preserved(self):
        for change in ('version', 'unrelated', 'mode', 'symlink'):
            with self.subTest(change=change):
                path, record = self.transaction(b'V=v1\nSECRET_KEY=secret-original\n', {'V': 'v2'})
                if change == 'mode':
                    path.chmod(0o600)
                elif change == 'symlink':
                    target = self.write('V=v2\nSECRET_KEY=secret-original\n', name='operator.env', mode=0o640)
                    path.unlink()
                    path.symlink_to(target)
                else:
                    path.write_bytes(path.read_bytes() + (b'V=v9\n' if change == 'version' else b'OPERATOR=kept\n'))
                before, mode = path.read_bytes(), path.stat().st_mode
                with self.assertRaises(EnvFileError):
                    envfile.restore_changes(path, record)
                self.assertEqual((path.read_bytes(), path.stat().st_mode), (before, mode))
                if path.is_symlink():
                    path.unlink()

    def test_symlink_target_is_preserved_on_write_and_recovery(self):
        path = self.write('V="\\$X"\n', name='actual.env', mode=0o600)
        alias = self.dir / '.env'
        alias.symlink_to(path)
        before = path.read_bytes()
        record = envfile.recovery_record(alias, {'V': 'v2'})
        envfile.write_changes(alias, {'V': 'v2'}, recovery=record)
        envfile.restore_changes(alias, record)
        self.assertTrue(alias.is_symlink())
        self.assertEqual(path.read_bytes(), before)

    def test_changed_file_after_record_preparation_never_gets_written(self):
        path = self.write('V=v1\n')
        record = envfile.recovery_record(path, {'V': 'v2'})
        path.write_bytes(b'V=v9\nOPERATOR=kept\n')
        before = path.read_bytes()
        with self.assertRaisesRegex(EnvFileError, 'changed'):
            envfile.write_changes(path, {'V': 'v2'}, recovery=record)
        self.assertEqual(path.read_bytes(), before)

    def test_malformed_inverse_is_refused_before_or_after_env_write(self):
        path, record = self.transaction(b'V=v1\n', {'V': 'v2'})
        corrupt = [None, {}, dict(record, format=True), dict(record, format=2), dict(record, mode=-1),
                   dict(record, edits=[None]), dict(record, edits=[{'offset': True, 'before': 'v1', 'after': 'v2'}]),
                   dict(record, edits=[{'offset': 99, 'before': 'v1', 'after': 'v2'}]),
                   dict(record, edits=[{'offset': 2, 'before': 'wrong', 'after': 'v2'}]),
                   dict(record, edits=[]), dict(record, after_sha256='x' * 64)]
        for raw in (b'V=v1\n', b'V=v2\n'):
            for bad in corrupt:
                with self.subTest(raw=raw, record=bad):
                    path.write_bytes(raw)
                    with self.assertRaises(EnvFileError):
                        envfile.restore_changes(path, bad)
                    self.assertEqual(path.read_bytes(), raw)

    def test_recovery_exchange_and_durability_failures_reconcile_without_toggle(self):
        for after in (False, True):
            with self.subTest(after=after):
                path, record = self.transaction(b'V=v1\n', {'V': 'v2'})
                real = envfile.envcommit.exchange
                def exchange(a, b, c, d):
                    if b == 'slot':
                        if after:
                            real(a, b, c, d)
                        raise OSError(28, 'fixture')
                    return real(a, b, c, d)
                with mock.patch.object(envfile.envcommit, 'exchange', side_effect=exchange):
                    with self.assertRaises(EnvFileError):
                        envfile.restore_changes(path, record)
                self.assertEqual(path.read_bytes(), b'V=v1\n' if after else b'V=v2\n')
                envfile.restore_changes(path, record)
                envfile.restore_changes(path, record)
                self.assertEqual(path.read_bytes(), b'V=v1\n')

    def test_edit_during_temp_write_is_not_overwritten(self):
        path, record = self.transaction(b'V=v1\n', {'V': 'v2'})
        fsync = envfile.os.fsync
        def edit(fd):
            fsync(fd)
            if stat.S_ISREG(os.fstat(fd).st_mode):
                path.write_bytes(b'V=v9\nOPERATOR=kept\n')
        with mock.patch.object(envfile.os, 'fsync', side_effect=edit):
            with self.assertRaisesRegex(EnvFileError, 'changed'):
                envfile.restore_changes(path, record)
        self.assertEqual(path.read_bytes(), b'V=v9\nOPERATOR=kept\n')
        self.assertFalse(list(self.dir.glob('.env.futureagi-update-*')))

    def test_logical_dollar_values_are_never_used_as_lexical_proof(self):
        for token in ('"\\$X"', '"$X"', "'$X'", '$X'):
            with self.subTest(token=token):
                path = self.write('V=' + token + '\n')
                self.assertEqual(read_env(path)['V'], '$X')
                with self.assertRaises(EnvFileError):
                    render_changes('V=v2\n', {'V': read_env(path)['V']}, verbatim=True)

    def test_recovery_text_rejects_invalid_edits_at_both_hash_endpoints(self):
        path = self.write('V=v1\nPRIVATE=not-for-diagnostics\n', mode=0o600)
        record = envfile.recovery_record(path, {'V': 'v2'})
        # Test the lexical validator directly: physical commit checks otherwise
        # reject a changed inode before these independently necessary checks.
        for raw, field in ((b'V=v1\nPRIVATE=not-for-diagnostics\n', 'after_sha256'),
                           (b'V=v2\nPRIVATE=not-for-diagnostics\n', 'before_sha256')):
            path.write_bytes(raw)
            for edits in ([{'offset': 999, 'before': 'v1', 'after': 'v2'}],
                          [{'offset': 2, 'before': 'wrong', 'after': 'wrong'}], []):
                with self.subTest(raw=raw, edits=edits), self.assertRaises(EnvFileError) as caught:
                    envfile.recovery_text(path, dict(record, edits=edits))
                self.assertNotIn('not-for-diagnostics', str(caught.exception))
                self.assertEqual(path.read_bytes(), raw)
            with self.assertRaisesRegex(EnvFileError, 'file hash'):
                envfile.recovery_text(path, dict(record, **{field: '0' * 64}))
            self.assertEqual(path.read_bytes(), raw)
        path.write_bytes(b'V=operator\n')
        with self.assertRaisesRegex(EnvFileError, 'changed since the interrupted run'):
            envfile.recovery_text(path, record)
        self.assertEqual(path.read_bytes(), b'V=operator\n')

    def test_legacy_reference_path_uses_exact_lexical_recovery(self):
        path = self.write('V=v1\n', mode=0o600)
        record = envfile.recovery_record(path, {'V': 'v2'})
        del record['commit']
        self.assertEqual(envfile.check_commit(path, record), 'legacy')
        path.write_bytes(b'V=v2\n')
        envfile.restore_changes(path, record)
        self.assertEqual(path.read_bytes(), b'V=v1\n')
        slots = list(self.dir.glob('.futureagi-updates/env-commits/*/*/slot'))
        self.assertEqual([slot.read_bytes() for slot in slots], [b'V=v2\n'])

    def test_mode_change_after_journal_preparation_prevents_write(self):
        path = self.write('V=v1\n', mode=0o600)
        record = envfile.recovery_record(path, {'V': 'v2'})
        path.chmod(0o640)
        with self.assertRaisesRegex(EnvFileError, 'mode changed since the journal'):
            envfile.write_changes(path, {'V': 'v2'}, recovery=record)
        self.assertEqual(path.read_bytes(), b'V=v1\n')
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
        self.assertFalse((self.dir / '.futureagi-updates').exists())

    def test_record_stat_failure_after_read_is_not_a_recovery_record(self):
        path = self.write('V=v1\n')
        real = envfile.render_changes

        def removed(*args, **kwargs):
            result = real(*args, **kwargs)
            path.unlink()
            return result

        with mock.patch.object(envfile, 'render_changes', side_effect=removed):
            with self.assertRaisesRegex(EnvFileError, 'cannot read'):
                envfile.recovery_record(path, {'V': 'v2'})
        self.assertFalse(path.exists())
        self.assertFalse((self.dir / '.futureagi-updates').exists())

    def assert_noop_rechecks_bytes_after_stat(self, path):
        # Resolve before patching: resolve()/samefile() inside a Path.stat hook
        # can recurse, and the production stat uses the canonical target.
        resolved = path.resolve(strict=True)
        real_stat, real_read = Path.stat, envfile._read_text
        events = []

        def read(target, shown):
            self.assertEqual((target, shown), (resolved, path))
            text = real_read(target, shown)
            events.append(('read', text))
            return text

        def stat_and_edit(target, *args, **kwargs):
            result = real_stat(target, *args, **kwargs)
            if target == resolved:
                # Inject exactly once, after the original read and the actual
                # stat, but before the no-op guard's second read.
                self.assertEqual(events, [('read', 'V=v1\n')])
                events.append(('stat', target))
                path.write_bytes(b'V=operator\n')
                events.append(('edit', b'V=operator\n'))
            return result

        with mock.patch.object(Path, 'stat', stat_and_edit), \
                mock.patch.object(envfile, '_read_text', side_effect=read):
            with self.assertRaisesRegex(EnvFileError, 'changed during the write'):
                envfile.write_changes(path, {})
        self.assertEqual(events, [('read', 'V=v1\n'), ('stat', resolved),
                                  ('edit', b'V=operator\n'), ('read', 'V=operator\n')])
        self.assertEqual(path.read_bytes(), b'V=operator\n')
        self.assertFalse((resolved.parent / '.futureagi-updates').exists())

    def test_noop_rechecks_bytes_after_stat_before_returning_success(self):
        self.assert_noop_rechecks_bytes_after_stat(self.write('V=v1\n'))

    def test_noop_rechecks_bytes_through_a_symlinked_parent(self):
        target = self.write('V=v1\n', name='actual/.env')
        alias = self.dir / 'alias'
        alias.symlink_to('actual', target_is_directory=True)
        path = alias / '.env'
        self.assertNotEqual(path, target.resolve())
        self.assertTrue(path.samefile(target))
        before_alias = alias.lstat()
        self.assert_noop_rechecks_bytes_after_stat(path)
        self.assertTrue(alias.is_symlink())
        self.assertEqual(os.readlink(alias), 'actual')
        self.assertEqual(alias.lstat().st_ino, before_alias.st_ino)
        self.assertTrue(path.samefile(target))

    def test_two_real_reads_with_intervening_editor_restore_do_not_authorize_write(self):
        path = self.write('V=v1\n')
        record = envfile.recovery_record(path, {'V': 'v2'})
        del record['commit']  # A supported legacy lexical journal has no sidecar to inspect.
        path.write_bytes(b'V=operator\n')
        real = envfile._read_text
        reads = []

        def read_and_edit(*args):
            text = real(*args)
            reads.append(text)
            if len(reads) == 1:
                path.write_bytes(b'V=v2\n')
            return text

        with mock.patch.object(envfile, '_read_text', side_effect=read_and_edit):
            with self.assertRaisesRegex(EnvFileError, 'changed during recovery'):
                envfile.restore_changes(path, record)
        self.assertEqual(reads, ['V=operator\n', 'V=v2\n'])
        self.assertEqual(path.read_bytes(), b'V=v2\n')
        self.assertFalse((self.dir / '.futureagi-updates').exists())

    def test_failed_durable_cancel_does_not_claim_untouched_forward_is_finished(self):
        path = self.write('V=v1\n')
        record = envfile.recovery_record(path, {'V': 'v2'})
        real = envfile.envcommit.exchange

        def stop(a, b, c, d):
            if b == 'slot':
                raise SystemExit(137)
            return real(a, b, c, d)

        with mock.patch.object(envfile.envcommit, 'exchange', side_effect=stop):
            with self.assertRaises(SystemExit):
                envfile.write_changes(path, {'V': 'v2'}, recovery=record)
        publish = envfile.envcommit._json

        def fail_cancel(fd, name, body):
            if body.get('phase') == 'not_applied':
                raise OSError(5, 'private-cancel-fault')
            return publish(fd, name, body)

        with mock.patch.object(envfile.envcommit, '_json', side_effect=fail_cancel):
            with self.assertRaises(envfile.EnvCommitError) as caught:
                envfile.restore_changes(path, record)
        self.assertNotIn('private-cancel-fault', str(caught.exception))
        self.assertEqual(path.read_bytes(), b'V=v1\n')
        operation = envfile.envcommit.directory(path, record['commit']['forward'])
        self.assertEqual(json.loads((operation / 'manifest.json').read_text())['phase'], 'armed')
        self.assertEqual((operation / 'candidate').read_bytes(), b'V=v2\n')
        envfile.restore_changes(path, record)
        self.assertEqual(json.loads((operation / 'manifest.json').read_text())['phase'], 'not_applied')

    def test_older_unicode_table_preserves_new_letters_quote_boundaries(self):
        import unicodedata
        for char in ('\u0870', '\U0001e030', '\U00031350'):
            with self.subTest(char=char), mock.patch.object(envfile, 'unicodedata', unicodedata.ucd_3_2_0):
                raw = ('V=v1\n' + char + '=\'first\nV=not-a-definition\nlast\'\n').encode()
                path, record = self.transaction(raw, {'V': 'v2'})
                self.assertEqual(path.read_bytes(), raw.replace(b'V=v1', b'V=v2'))
                envfile.restore_changes(path, record)
                self.assertEqual(path.read_bytes(), raw)


# ============================================ conformance: real Compose ==

# Compose reads .env with its own Go parser. `config` runs it on each file
# below (no daemon, no network), and envfile must read, rewrite and remove
# exactly what Compose sees. FUTURE_AGI_UPDATE_COMPOSE_BINARY names the
# docker-compose binary to check (run once per Compose release); without it,
# `docker compose` is used when it runs, else the tests are skipped.
COMPOSE_BINARY = "FUTURE_AGI_UPDATE_COMPOSE_BINARY"
UNSET = "<unset>"
# Every file here is one Compose accepts. It has no "$" outside single quotes
# (Compose expands it, read_env does not) and no name the subprocess environment
# sets, as Compose prefers that environment to the env file.
COMPOSE_CORPUS = (
    "A=1\nB = 2\n  C=3  \nexport D=4\nexport\tE=5\n  export   F=6\nexportG=7\nexport=8\nexport  export=9\n",
    "A: v1\nB:v2\nexport C : 'x y' # c\nD:=1\nE::1\nF=x:y\nG:\nH: \"multi\nline\"\nhttp://example.com/a\nI=2\nI: 3\n",
    "export\vA=1\nexport \vB=2\nexport\x85C=3\nexport \x85D=4\nexport\xa0E=5\nexport\fF=6\nexport\rG=7\n"
    "export\t\x85\xa0H=8\n",
    "\vA=1\n\fB=2\n\x85C=3\n\xa0D=4\n\u3000E=5\n\u2028F=6\nG\x85=7\nH\xa0:8\nI\v=9\nJ=\v\x85 10\n\u2003K=11\n",
    "A=v1\x1c\nB=v2\x1f \nC=v3\u3000\nD=v4\x85\nE=v5 \t\nF=v6\xa0\nG=v7\x1d\x1e\t\nH=v8 \x1f # c\n",
    "# A=1\n  # B=2\nC=3 # c\nD=4#c\nE=5\t#c\nF= #c\nG='x' # c\nH=\"y\"#c\n#\nI=a #b #c\nJ=http://h/#frag\n",
    "A=1\r\nB='x'\r\nC=\"y\" # c\r\nD=v # c\r\nexport E=e\r\n\r\nF: \"multi\r\nline\"\r\nG=\r\n",
    "\ufeffA=1\nB=2\n",
    "\ufeffexport A: 'x'\r\n",
    'A="x" B=1\n',
    'A="x"B=1\n',
    "A='x' export B=1\n",
    'A="x"\tB="y" C=2 # c\nD=4\n',
    'A="x" B="multi\nline" C=3\nD=4\n',
    'A="x" B=1\r\nC=3\r\n',
    'A="x" B:1\n',
    'A="x" B=1',
    "C=3\nA='x' B=\"y\"",
    'A="x" # B=1\nC=3\n',
    'A="x" junk\nB=2\n',
    "A='x'junk\nB=2\n",
    'A="x"\vB=1\nC=\'y\'\u3000D=2\nE="z" \x85 F=3\nG="w"\xa0H=4\n',
    'A="x" B="y"C=\'z\' D: "w" # E=5\nF=6\n',
    'A="x"   \n B=1\n',
    'A="x" B= # c\nC="y" D=1 E=2\nF="z" G=\n',
    "A=\"a\\\"b\"\nB=\"\\\"q\\\"\"\nC=\"a\\\\b\"\nD=\"a\\\\\\\"b\"\nE='it\\'s'\nF=\"x\\\\\" # c\n",
    'A="x\\0377y"\nB="\\0101\\0400\\0"\nC="t\\tn\\nr\\rv\\vf\\fb\\ba\\a"\nD="\\c\\d\\q"\nE="a\\$b"\nF=\'a\\nb\'\n',
    "A='$X ${Y}'\nB='\\$Z'\n",
    "A=ünïcødé\nB='日本語'\nC=\"emoji 🎉 \"\nD=ü # c\n",
    "A=\nB=''\nC=\"\"\nD= \nE:\nF=  \t\nG\t=\t''\n",
    "A=1\nB=2",
    "A='x'",
    "A='line1\nline2' B=2\nC=3\n",
    'A="line1\nline2 \\" still"\nB=2\n',
    'A="cr\r\nlf\\\r\nx"\r\nB=1\r\n',
    "V=1\nexport V: 2\nV:3 # c\nW=1\nW=\n",
    "garbage\n1A=x\nA-B=y\nA.B=z\nA[0]=w\nOK=1\n",
    "A=it's\nB=say \"hi\"\nC=x'y'z\nD=a=b==\nE=v\t# c\n",
    "A= 'x' \nB=\v\"y\"\nC=\x85'z' # c\n",
    "FUTURE_AGI_VERSION=v1.0.0\nNAME=\"fagi\" FUTURE_AGI_VERSION=v1.0.1 # hotfix\nA=1\n",
    "FUTURE_AGI_VERSION: v1.0.0  # pinned\r\nA=1\r\nexport FRONTEND_VERSION : 'v1.0.0' # c\r\n",
    "FUTURE_AGI_VERSION=v1.0.0\nexport FUTURE_AGI_VERSION: \"v1.0.1\"  # hotfix\nFRONTEND_VERSION=v1.0.0\n",
    # Keys no variable can name, whose quoted values hold what looks like a definition.
    'a.b="\nFUTURE_AGI_VERSION=v0\n"\nFUTURE_AGI_VERSION=v1\n',
    "my-key='\nFRONTEND_VERSION=v0\n' FRONTEND_VERSION=v1\n\u00c4=\"x\nFUTURE_AGI_VERSION=v0\"\n",
    'export\vX="v1" FUTURE_AGI_VERSION=v2 # c\nA[0]="x\ny"\n1A=\'\nB=2\'\nA\tB: "\nC=3\n" C=4\n',
    # Only "\n" ends a line, and Go's white space (trimmed off a value) ends at U+200A, without \x1c-\x1f or \x7f.
    "A=1\rB=2\nC=3\r\r\n",
    "A=v1῿\nB=v2 \nC=v3 \nD=v4​\nE=v5 \nF=v6\x7f\n",
    # Files that define nothing, and ones whose last line has no line ending.
    "", "\n", "\r\n", "﻿", "﻿# c\r\nA=1\r\n", "\n\nA=1\nB=2 # c",
    "FUTURE_AGI_VERSION=v1\n# trailing comment ",
    "A=1\nFUTURE_AGI_VERSION='v1' # c",
    'A="\\0\\01\\012\\08"\nB="\\0350\\0400"\n',
    # A bare KEY is looked up in the (empty) environment: an earlier definition of it stands.
    "A=1\nA\nB\nC=\"x\" D\n",
    # Bytes that are not UTF-8, as their surrogate escapes (envfile reads them so; config prints U+FFFD).
    "A=x\udcff\udcfe\nB='\udce2\udc82'\nC=\"\udcc3\" # c\nFUTURE_AGI_VERSION=v1\udcff\n",
)


def _printed(values: Mapping[str, str]) -> Dict[str, str]:
    """`values` as `config --format json` prints them: each byte that is not UTF-8 as U+FFFD."""

    return {re.sub("[\udc80-\udcff]", "�", key): re.sub("[\udc80-\udcff]", "�", value)
            for key, value in values.items()}


def _compose_environment() -> Dict[str, str]:
    # PATH and HOME find the binary and its plugins; a dead DOCKER_HOST keeps any daemon out of it.
    environment = {"PATH": os.environ.get("PATH", os.defpath),
                   "DOCKER_HOST": "unix:///nonexistent/future-agi-update-test.sock"}
    if "HOME" in os.environ:
        environment["HOME"] = os.environ["HOME"]
    return environment


def _compose_command() -> Optional[List[str]]:
    configured = os.environ.get(COMPOSE_BINARY)
    if configured:
        return [configured]
    docker = shutil.which("docker")
    if docker is None:
        return None
    try:
        probe = subprocess.run([docker, "compose", "version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               env=_compose_environment(), timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return [docker, "compose"] if probe.returncode == 0 else None


class EnvFileComposeConformanceTests(_EnvCase):
    """read_env, example_keys, render_changes and write_changes against Compose itself."""

    command: Optional[List[str]] = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.command = _compose_command()
        if cls.command is None:
            raise unittest.SkipTest(f"no Compose: set {COMPOSE_BINARY} to a docker-compose binary")

    def write(self, text: str, name: str = ".env", mode: Optional[int] = None) -> Path:
        path = self.dir / name
        path.write_bytes(text.encode("utf-8", "surrogateescape"))
        return path

    @staticmethod
    def names(text: str) -> list:
        return sorted(set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text)))

    def compose_reads(self, data: bytes, names: list) -> Dict[str, str]:
        """Every key Compose reads from `data` as its --env-file.

        `names` are interpolated (what image tags see); the same file as the
        service's env_file adds the keys no variable can name (a.b, 1A).
        """

        assert self.command is not None
        directory = Path(tempfile.mkdtemp(dir=self.dir))
        (directory / ".env").write_bytes(data)
        project = {"name": "envcheck", "services": {"probe": {
            "image": "example.invalid/probe", "env_file": [".env"],
            "environment": {name: f"${{{name}-{UNSET}}}" for name in names}}}}
        (directory / "compose.json").write_text(json.dumps(project))
        result = subprocess.run(
            [*self.command, "--project-directory", str(directory), "-f", str(directory / "compose.json"),
             "--env-file", str(directory / ".env"), "config", "--format", "json"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_compose_environment(), timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        environment = json.loads(result.stdout.decode("utf-8"))["services"]["probe"].get("environment") or {}
        # config writes a "$" in a value as "$$", so its output interpolates to the same value. A bare
        # KEY line Compose looks up in the (empty) environment comes out as null.
        return {name: value.replace("$$", "$") for name, value in environment.items()
                if value is not None and value != UNSET}

    @staticmethod
    def variables(values: Mapping[str, str]) -> list:
        """The keys bin/update may write: the ones a variable can name."""

        return sorted(key for key in values if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key))

    def test_the_corpus_is_read_as_compose_reads_it(self) -> None:
        reserved = set(_compose_environment())
        for text in COMPOSE_CORPUS:
            with self.subTest(text=text):
                names = self.names(text)
                self.assertFalse(reserved & set(names))
                path = self.write(text)
                expected = self.compose_reads(path.read_bytes(), names)
                self.assertEqual(_printed(read_env(path)), expected)
                self.assertEqual(sorted(example_keys(self.write(text, name=".env.example"))), sorted(expected))

    def test_rewriting_and_removing_keys_changes_only_what_compose_reads_for_them(self) -> None:
        # Per file, one third of the keys get a new tag, one third are removed and one third are kept.
        for number, text in enumerate(COMPOSE_CORPUS):
            with self.subTest(text=text):
                names = self.names(text)
                before = read_env(self.write(text))
                keys = self.variables(before)
                changes: Dict[str, Optional[str]] = {}
                for index, key in enumerate(keys):
                    group = (index + number) % 3
                    if group < 2:
                        changes[key] = f"v9.{index}.{group}" if group == 0 else None
                rendered = render_changes(text, changes)
                expected = {key: value for key, value in before.items() if key not in changes}
                expected.update((key, value) for key, value in changes.items() if value is not None)
                self.assertEqual(self.compose_reads(rendered.encode("utf-8", "surrogateescape"), names),
                                 _printed(expected))
                path = self.write(text, name="written.env")
                write_changes(path, changes)
                self.assertEqual(path.read_bytes(), rendered.encode("utf-8", "surrogateescape"))
                self.assertEqual(read_env(path), expected)

    def test_a_new_version_key_appended_to_each_file_is_read_by_compose(self) -> None:
        for text in COMPOSE_CORPUS:
            with self.subTest(text=text):
                names = self.names(text) + ["NEW_VERSION"]
                rendered = render_changes(text, {"NEW_VERSION": "v9.9.9"})
                expected = dict(read_env(self.write(text)), NEW_VERSION="v9.9.9")
                self.assertEqual(self.compose_reads(rendered.encode("utf-8", "surrogateescape"), names),
                                 _printed(expected))

    def test_a_value_put_back_verbatim_is_read_back_by_compose(self) -> None:
        for text in COMPOSE_CORPUS:
            with self.subTest(text=text):
                names = self.names(text)
                before = read_env(self.write(text))
                keys = self.variables(before)
                rewritten = render_changes(text, {key: "v9.9.9" for key in keys})
                # Not a value with "$": read_env gives "a$b" for "a\$b", which Compose would expand once
                # written back, but neither is ever an image tag.
                found = {key: before[key] for key in keys if "$" not in before[key]}
                try:
                    restored = render_changes(rewritten, found, verbatim=True)
                except EnvFileError:
                    continue  # refused: nothing is written
                expected = dict(before, **{key: "v9.9.9" for key in keys if key not in found})
                self.assertEqual(self.compose_reads(restored.encode("utf-8", "surrogateescape"), names),
                                 _printed(expected))

    def compose_reads_each(self, files: List[str]) -> List[Dict[str, str]]:
        """What Compose reads from each of `files` as a service's env_file, all in one `config`."""

        assert self.command is not None
        directory = Path(tempfile.mkdtemp(dir=self.dir))
        services = {}
        for index, text in enumerate(files):
            (directory / f"{index}.env").write_bytes(text.encode("utf-8", "surrogateescape"))
            services[f"s{index}"] = {"image": "example.invalid/probe", "env_file": [f"{index}.env"]}
        (directory / "compose.json").write_text(json.dumps({"name": "envcheck", "services": services}))
        result = subprocess.run(
            [*self.command, "--project-directory", str(directory), "-f", str(directory / "compose.json"),
             "config", "--format", "json"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_compose_environment(), timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        services = json.loads(result.stdout.decode("utf-8"))["services"]
        return [{name: value for name, value in (services[f"s{index}"].get("environment") or {}).items()
                 if value is not None} for index in range(len(files))]

    def test_any_value_put_back_verbatim_is_refused_or_read_back_by_compose_as_itself(self) -> None:
        # A restore puts back whatever read_env found on one line. Wherever the key is defined (or appended),
        # each value is refused, or Compose reads exactly that value for it and every other value as before.
        version, other = "FUTURE_AGI_VERSION", "FRONTEND_VERSION"
        files = (f"{version}=v1\n{other}=v2\n", f"{version}='v1' # c\n{other}=v2\n", f'{version}="v1"\n{other}=v2\n',
                 f'NAME="n" {version}=v1 # c\n{other}=v2\n', f"NAME='n' {version}=\"v1\" {other}=v2\n",
                 f"export {version} : v1\r\n{other}=v2\r\n", f"{version}=\n{other}=v2", f"{other}=v2\n",
                 f'a.b="\n{version}=v0\n" {version}=v1\n')
        values = ('"abc', "'abc", 'a"b', "a'b", " lead", "trail ", "x #y", "x#y", "x # y", "a\\", "a\\\\b", '\\"',
                  "\\0101", "a\\nb", "\x1f", "\xa0", "x\xa0", " x", "x ", "\x85", '"x" ' + other + "=v9",
                  "'x' " + other + "=v9", '"x"' + other + "=v9", "#c", "=", ":", "= x", "é", "\udcff", "''", '""',
                  "'a' b", '"a\\"', "'a'#c", "x'", 'x"')
        cases = []
        for text in files:
            for value in values:
                try:
                    cases.append((text, value, render_changes(text, {version: value}, verbatim=True)))
                except EnvFileError:
                    continue
        self.assertGreater(len(cases), len(files) * 3)
        read = self.compose_reads_each(list(files) + [rendered for _, _, rendered in cases])
        originals = dict(zip(files, read))
        for (text, value, rendered), after in zip(cases, read[len(files):]):
            with self.subTest(text=text, value=value):
                self.assertEqual(after, _printed(dict(originals[text], **{version: value})))
                self.assertEqual(read_env(self.write(rendered)), dict(read_env(self.write(text)), **{version: value}))


    def test_new_unicode_letters_keep_quote_boundaries_with_an_old_python_table(self):
        import unicodedata
        for char in ('ࡰ', '\U0001e030', '\U00031350'):
            with self.subTest(char=char):
                raw = ('V=v1\n' + char + '="first\nV=hidden\nlast"\n').encode()
                original = self.compose_reads(raw, ['V'])
                self.assertEqual(original['V'], 'v1')
                path = self.dir / '.env'
                path.write_bytes(raw)
                with mock.patch.object(envfile, 'unicodedata', unicodedata.ucd_3_2_0):
                    record = envfile.recovery_record(path, {'V': 'v2'})
                    write_changes(path, {'V': 'v2'}, recovery=record)
                    self.assertEqual(path.read_bytes(), raw.replace(b'V=v1', b'V=v2'))
                    envfile.restore_changes(path, record)
                self.assertEqual(self.compose_reads(path.read_bytes(), ['V']), original)

    def test_compose_interpolation_and_literal_dollars_survive_exact_recovery(self):
        for token in ('"\\$X"', '"v\\${X"', "'literal$X'", '"${X}"'):
            with self.subTest(token=token):
                raw = ('﻿X=v9\r\nV=v0\r\nMIDDLE=${V}\r\nV=' + token + ' # last').encode()
                path = self.dir / '.env'
                path.write_bytes(raw)
                original = self.compose_reads(raw, ['V', 'MIDDLE'])
                self.assertEqual(original['MIDDLE'], 'v0')
                changes = {'V': 'v2', 'Z_ADDED': 'v3', 'A_ADDED': 'v4'}
                record = json.loads(json.dumps(envfile.recovery_record(path, changes), sort_keys=True))
                write_changes(path, changes, recovery=record)
                self.assertEqual(self.compose_reads(path.read_bytes(), ['V', 'MIDDLE'])['MIDDLE'], 'v2')
                envfile.restore_changes(path, record)
                self.assertEqual(path.read_bytes(), raw)
                self.assertEqual(self.compose_reads(path.read_bytes(), ['V', 'MIDDLE']), original)


if __name__ == "__main__":
    unittest.main()
