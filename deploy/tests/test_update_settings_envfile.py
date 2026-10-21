"""bin/update settings (FUTURE_AGI_UPDATE_* precedence, update.conf) and the
Compose dotenv helpers (read, render, atomic write). No Docker, no network.

The expected dotenv semantics are Docker Compose's (envfile.read_env promises
"values as Compose reads them"); the quirky cases below were checked against
`docker-compose config` v5.1.4.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Dict, Mapping, Optional, Tuple
from unittest import mock

from deploy.update import envfile
from deploy.update.envfile import EnvFileError, example_keys, read_env, render_changes, write_changes
from deploy.update.manifest import CHANNELS
from deploy.update.settings import (
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
        self.assertEqual(set(LAYER_VALUES), set(DEFAULTS))

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
        for key in DEFAULTS:
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
        self.assertEqual(self.parse("garbage\n1A=x\nA-B=y\n=z\nA B=c\nOK=1\n"), {"OK": "1"})

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
        path = self.write("# OPTIONAL=1\n   #ALSO=2\nnot a pair\n1BAD=x\nexport REAL=1\n  SPACED = 2\n", ".env.example")
        self.assertEqual(example_keys(path), ["REAL", "SPACED"])

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
        calls = []
        real = os.replace

        def replace(src, dst, *args, **kwargs):
            calls.append((str(src), str(dst)))
            return real(src, dst, *args, **kwargs)

        return calls, mock.patch.object(envfile.os, "replace", replace)

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
        self.assertEqual(Path(temp).parent, path.resolve().parent)

    def test_temp_file_name_and_private_mode(self) -> None:
        path = self.write(f"{self.VERSION}=1.0.0\n", mode=0o644)
        seen = []
        real_chmod = os.chmod

        def chmod(target, mode, *args, **kwargs):
            seen.append((str(target), stat.S_IMODE(os.stat(target).st_mode), mode))
            return real_chmod(target, mode, *args, **kwargs)

        calls, patch = self.spy_replace()
        with patch, mock.patch.object(envfile.os, "chmod", chmod):
            write_changes(path, {self.VERSION: "1.1.0"})
        temp = Path(calls[0][0])
        self.assertTrue(temp.name.startswith(".env.futureagi-update-"), temp.name)
        self.assertTrue(temp.name.endswith(".tmp"), temp.name)
        self.assertEqual(seen, [(str(temp), 0o600, 0o644)])

    def test_no_temp_files_left_after_success(self) -> None:
        path = self.write(f"{self.VERSION}=1.0.0\nA=1\n")
        before = self.listing(self.dir)
        write_changes(path, {self.VERSION: "1.1.0"})
        write_changes(path, {"A": None, "NEW": "x"})
        self.assertEqual(self.listing(self.dir), before)

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
    def test_keeps_owner_when_permitted(self) -> None:
        path = self.write(f"{self.VERSION}=1.0.0\n")
        info = os.stat(path)
        calls = []
        real_chown = os.chown

        def chown(target, uid, gid, *args, **kwargs):
            calls.append((Path(target).name, uid, gid))
            return real_chown(target, uid, gid, *args, **kwargs)

        with mock.patch.object(envfile.os, "chown", chown):
            write_changes(path, {self.VERSION: "1.1.0"})
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][0].startswith(".env.futureagi-update-"))
        self.assertEqual(calls[0][1:], (info.st_uid, info.st_gid))
        after = os.stat(path)
        self.assertEqual((after.st_uid, after.st_gid), (info.st_uid, info.st_gid))

    @unittest.skipUnless(hasattr(os, "chown"), "no chown on this platform")
    def test_chown_permission_error_is_tolerated(self) -> None:
        path = self.write(f"{self.VERSION}=1.0.0\n", mode=0o640)
        before = self.listing(self.dir)
        with mock.patch.object(envfile.os, "chown", side_effect=PermissionError(1, "Operation not permitted")):
            write_changes(path, {self.VERSION: "1.1.0"})
        self.assertEqual(read_env(path)[self.VERSION], "1.1.0")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o640)
        self.assertEqual(self.listing(self.dir), before)

    # ----------------------------------------------------------- symlinks --
    def test_symlink_target_is_edited_and_link_kept(self) -> None:
        target = self.write(f"A=1\n{self.VERSION}=1.0.0\n", name="shared/real.env", mode=0o640)
        checkout = self.dir / "checkout"
        checkout.mkdir()
        link = checkout / ".env"
        os.symlink(os.path.join("..", "shared", "real.env"), link)
        before_checkout, before_shared = self.listing(checkout), self.listing(target.parent)
        calls, patch = self.spy_replace()
        with patch:
            write_changes(link, {self.VERSION: "1.1.0"})
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), os.path.join("..", "shared", "real.env"))
        self.assertEqual(target.read_bytes(), f"A=1\n{self.VERSION}=1.1.0\n".encode())
        self.assertEqual(stat.S_IMODE(os.stat(target).st_mode), 0o640)
        self.assertEqual(Path(calls[0][0]).parent, target.resolve().parent)
        self.assertEqual(self.listing(checkout), before_checkout)
        self.assertEqual(self.listing(target.parent), before_shared)

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

    def test_failed_replace_leaves_original_and_no_temp_file(self) -> None:
        original = f"A=1\n{self.VERSION}=1.0.0\n"
        path = self.write(original, mode=0o600)
        listing = self.listing(self.dir)
        with mock.patch.object(envfile.os, "replace", side_effect=OSError(28, "No space left on device")):
            with self.assertRaises(Exception):
                write_changes(path, {self.VERSION: "1.1.0"})
        self.assertEqual(path.read_text(encoding="utf-8"), original)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(self.listing(self.dir), listing)

    def test_failed_fsync_leaves_original_and_no_temp_file(self) -> None:
        original = f"{self.VERSION}=1.0.0\n"
        path = self.write(original)
        listing = self.listing(self.dir)
        with mock.patch.object(envfile.os, "fsync", side_effect=OSError(5, "Input/output error")):
            with self.assertRaises(Exception):
                write_changes(path, {self.VERSION: "1.1.0"})
        self.assertEqual(path.read_text(encoding="utf-8"), original)
        self.assertEqual(self.listing(self.dir), listing)

    def test_failed_chmod_leaves_original_and_no_temp_file(self) -> None:
        original = f"{self.VERSION}=1.0.0\n"
        path = self.write(original)
        listing = self.listing(self.dir)
        with mock.patch.object(envfile.os, "chmod", side_effect=OSError(1, "Operation not permitted")):
            with self.assertRaises(Exception):
                write_changes(path, {self.VERSION: "1.1.0"})
        self.assertEqual(path.read_text(encoding="utf-8"), original)
        self.assertEqual(self.listing(self.dir), listing)

    def test_ctrl_c_during_write_removes_temp_and_propagates(self) -> None:
        original = f"{self.VERSION}=1.0.0\n"
        path = self.write(original)
        listing = self.listing(self.dir)
        with mock.patch.object(envfile.os, "fsync", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                write_changes(path, {self.VERSION: "1.1.0"})
        self.assertEqual(path.read_text(encoding="utf-8"), original)
        self.assertEqual(self.listing(self.dir), listing)

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
        with mock.patch.object(envfile.tempfile, "mkstemp", side_effect=AssertionError("must not write")):
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
        probe = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--is-inside-work-tree"],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        if probe.returncode != 0 or probe.stdout.strip() != "true":
            self.skipTest(f"{ROOT} is not a git work tree")

        def check_ignore(relative: str) -> int:
            return subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", relative],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE).returncode

        # The check itself works: a plain source path is not ignored.
        self.assertEqual(check_ignore("deploy/update/probe-not-ignored.py"), 1)
        for relative in (".env", "deploy/.env.production"):
            with self.subTest(env_file=relative):
                path = self.write(f"{self.VERSION}=1.0.0\n", name=relative)
                calls, patch = self.spy_replace()
                with patch:
                    write_changes(path, {self.VERSION: "1.1.0"})
                temp = Path(calls[0][0])
                self.assertEqual(temp.parent, path.resolve().parent)
                in_repo = (Path(relative).parent / temp.name).as_posix()
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

    def test_crlf_inside_a_multi_line_value_reads_as_lf(self) -> None:
        self.assertEqual(self.parse('C="a\r\nb"\r\nD=1\r\n'), {"C": "a\nb", "D": "1"})

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
        with mock.patch.object(envfile.tempfile, "mkstemp", side_effect=AssertionError("must not write")):
            write_changes(path, {self.VERSION: "1.0.0"})
        self.assertEqual(os.stat(path).st_mtime_ns, before.st_mtime_ns)

    def test_failed_temp_file_creation_is_an_env_file_error(self) -> None:
        original = f"{self.VERSION}=1.0.0\n"
        path = self.write(original)
        with mock.patch.object(envfile.tempfile, "mkstemp", side_effect=PermissionError(13, "Permission denied")):
            with self.assertRaises(EnvFileError) as caught:
                write_changes(path, {self.VERSION: "1.1.0"})
        self.assertIn(f"cannot write {path}", str(caught.exception))
        self.assertEqual(path.read_text(encoding="utf-8"), original)
        self.assertEqual(self.listing(self.dir), [".env"])

    def test_every_failed_step_is_an_env_file_error_and_leaves_no_temp_file(self) -> None:
        real_fdopen = os.fdopen

        class FullDisk:
            def __init__(self, fd: int, *args, **kwargs) -> None:
                self.handle = real_fdopen(fd, *args, **kwargs)

            def __enter__(self) -> "FullDisk":
                return self

            def __exit__(self, *exc) -> None:
                self.handle.close()

            def write(self, data: bytes) -> int:
                raise OSError(28, "No space left on device")

        failures = (
            ("fdopen", FullDisk),
            ("fsync", mock.Mock(side_effect=OSError(5, "Input/output error"))),
            ("chmod", mock.Mock(side_effect=OSError(1, "Operation not permitted"))),
            ("replace", mock.Mock(side_effect=OSError(18, "Invalid cross-device link"))),
        )
        for name, replacement in failures:
            with self.subTest(step=name):
                original = f"A=1\n{self.VERSION}=1.0.0\n"
                path = self.write(original)
                with mock.patch.object(envfile.os, name, replacement):
                    with self.assertRaises(EnvFileError) as caught:
                        write_changes(path, {self.VERSION: "1.1.0"})
                self.assertIn(f"cannot write {path}", str(caught.exception))
                self.assertIsInstance(caught.exception.__cause__, OSError)
                self.assertEqual(path.read_bytes(), original.encode())
                self.assertEqual(self.listing(self.dir), [".env"])

    def test_invalid_key_leaves_the_file_untouched(self) -> None:
        original = f"{self.VERSION}=1.0.0\n"
        path = self.write(original)
        with mock.patch.object(envfile.tempfile, "mkstemp", side_effect=AssertionError("must not write")):
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

    def test_interpolated_value_round_trips_byte_for_byte_only_with_verbatim(self) -> None:
        for value in self.INTERPOLATED:
            for form in self.FORMS:
                for text in self.texts(form.format(value)):
                    with self.subTest(text=text):
                        found = self.parse(text)["V"]
                        self.assertEqual(found, value)
                        written = render_changes(text, {"V": "v1.1.0"})
                        self.assertEqual(self.parse(written)["V"], "v1.1.0")
                        with self.assertRaises(EnvFileError) as caught:
                            render_changes(written, {"V": found})
                        self.assertIn("refusing", str(caught.exception))
                        self.assertEqual(render_changes(written, {"V": found}, verbatim=True), text)

    def test_the_two_reported_setups_round_trip(self) -> None:
        # Finding 11: FUTURE_AGI_VERSION=${RELEASE}; finding 5: FRONTEND_VERSION=${FUTURE_AGI_VERSION}.
        for text in ("RELEASE=v1.0.0\nFUTURE_AGI_VERSION=${RELEASE}\n",
                     "FUTURE_AGI_VERSION=v1.0.0\nFRONTEND_VERSION=${FUTURE_AGI_VERSION}\n"):
            with self.subTest(text=text):
                found = {key: value for key, value in self.parse(text).items() if key != "RELEASE"}
                written = render_changes(text, {key: "v1.1.0" for key in found})
                self.assertNotEqual(written, text)
                self.assertEqual(render_changes(written, found, verbatim=True), text)

    def test_verbatim_writing_the_value_already_there_changes_nothing(self) -> None:
        for value in self.INTERPOLATED:
            for form in self.FORMS:
                for text in self.texts(form.format(value)):
                    with self.subTest(text=text):
                        self.assertEqual(render_changes(text, {"V": value}, verbatim=True), text)

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
                    render_changes("A=1\n", {"GOOD": "${RELEASE}", bad: "${RELEASE}"}, verbatim=True)
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

    def test_verbatim_appends_a_missing_key_bare(self) -> None:
        self.assertEqual(render_changes("A=1\r\n", {"V": "${RELEASE}"}, verbatim=True), "A=1\r\nV=${RELEASE}\r\n")
        self.assertEqual(self.parse(render_changes("A=1", {"V": "${R:-v1}"}, verbatim=True))["V"], "${R:-v1}")

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
        write_changes(path, {"FUTURE_AGI_VERSION": "v1.1.0", "FRONTEND_VERSION": "v1.1.0", "AGENT_VERSION": "v1.1.0"})
        written = path.read_bytes()
        self.assertNotEqual(written, self.RAW)
        with self.assertRaises(EnvFileError):
            write_changes(path, found)
        self.assertEqual(path.read_bytes(), written)
        write_changes(path, found, verbatim=True)
        self.assertEqual(path.read_bytes(), self.RAW)

    def test_refused_verbatim_write_leaves_the_file_untouched(self) -> None:
        path = self.dir / ".env"
        path.write_bytes(self.RAW)
        for changes in ({"FUTURE_AGI_VERSION": "${RELEASE}\nCOMPOSE_FILE=evil.yml"},
                        {"FUTURE_AGI_VERSION": "${RELEASE}\x00"},
                        {"FUTURE_AGI_VERSION": "${RELEASE}", "BAD KEY": "${RELEASE}"},
                        {"FRONTEND_VERSION": 'a"b'}):
            with self.subTest(changes=changes):
                with mock.patch.object(envfile.tempfile, "mkstemp", side_effect=AssertionError("must not write")):
                    with self.assertRaises(EnvFileError):
                        write_changes(path, changes, verbatim=True)
                self.assertEqual(path.read_bytes(), self.RAW)
                self.assertEqual(self.listing(self.dir), [".env"])

    def test_verbatim_write_of_the_values_already_there_does_not_touch_the_file(self) -> None:
        path = self.dir / ".env"
        path.write_bytes(self.RAW)
        before = os.stat(path)
        with mock.patch.object(envfile.tempfile, "mkstemp", side_effect=AssertionError("must not write")):
            write_changes(path, {"FUTURE_AGI_VERSION": "${RELEASE}", "FRONTEND_VERSION": "${FUTURE_AGI_VERSION:-v1}",
                                 "AGENT_VERSION": None}, verbatim=True)
        self.assertEqual(os.stat(path).st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(path.read_bytes(), self.RAW)

    def test_verbatim_write_keeps_the_mode_and_follows_a_symlink(self) -> None:
        target = self.write("FUTURE_AGI_VERSION=v1.1.0\n", "real.env", mode=0o640)
        link = self.dir / ".env"
        link.symlink_to(target.name)
        write_changes(link, {"FUTURE_AGI_VERSION": "${RELEASE}"}, verbatim=True)
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


if __name__ == "__main__":
    unittest.main()
