"""MCP state keys through existing Updater.apply; collect on the unmodified base."""
from __future__ import annotations

import hashlib
import json
import sys
from contextlib import nullcontext
from types import SimpleNamespace
import time
from unittest import mock

from deploy.tests.test_update_apply import BACKUP, TARGET, UpdaterCase
from deploy.tests.test_install_mcp_state_keys import KEY, CURRENT, PREVIOUS, HEX64
from deploy.update.apply import Blocked, Updater
from deploy.update.envfile import read_env
from deploy.update.plan import UP_TO_DATE
from deploy.update.receipt import secret_values
from deploy.update.settings import load_settings


class UpdateMcpStateKeysTests(UpdaterCase):
    def render_env(self, values):
        return "".join(line for line in super().render_env(values).splitlines(True)
                       if not line.startswith(KEY + "="))

    def with_lines(self, extra):
        self.build()
        self.env_path.write_bytes(self.env_bytes() + extra.encode("utf-8"))
        self.original = self.env_bytes()
        self.inst.reload_env()
        self.state.secrets = secret_values(read_env(self.env_path))

    def writes(self):
        return [row[1] for row in self.log if row[0] == "env_write"]

    def generator(self, **behavior):
        # Base has no policy: still exercise apply and fail its outcome assertion.
        # Patch only the leaf's dependency, never envcommit's unrelated randomness.
        policy = getattr(sys.modules[Updater.__module__], "mcp_state_keys", None)
        if policy is None:
            return nullcontext()
        return mock.patch.object(policy, "secrets", SimpleNamespace(token_hex=mock.Mock(**behavior)))

    def guarded(self, automatic=False):
        self.boundaries = []
        owner = self

        class Guard:
            def validate(self, boundary, context):
                owner.boundaries.append(boundary)

        guard = Guard()
        if automatic:
            guard.automatic = SimpleNamespace()
            self.inst.runner = SimpleNamespace(deadline=time.monotonic() + 86400)
        settings = load_settings(self.root, {"FUTURE_AGI_UPDATE_BACKUP_COMMAND": BACKUP}, environ={})
        return Updater(self.inst, settings, self.state, out=self.out, readiness=self.ready,
                       hook_runner=self.hook, automation_guard=guard, guard_context=lambda engine: "locked")

    def refused_unchanged(self):
        with self.assertRaises(Blocked) as caught:
            self.apply()
        message = str(caught.exception)
        self.assertIn(KEY, message)
        self.assertTrue(CURRENT not in message and PREVIOUS not in message, "value in refusal")
        self.assertTrue(self.env_bytes() == self.original, "refusal changed env bytes")
        self.assertEqual((self.writes(), self.hook.calls), ([], []))
        self.assertIsNone(self.journal())

    def test_absent_and_empty_journaled_before_backup_and_up(self):
        for extra in ("", f"{KEY}=\n", f"{KEY}=''\n"):
            self.with_lines(extra)
            self.apply()
            value = read_env(self.env_path).get(KEY) or ""
            self.assertTrue(HEX64.fullmatch(value) is not None, "missing generated 64-hex key")
            self.assertEqual([sorted(write) for write in self.writes()], [[KEY], ["FUTURE_AGI_VERSION"]])
            self.assertTrue(bool(self.env_write_kwargs[-2].get("recovery")), "key write bypassed recovery")
            kinds = [row[0] for row in self.log]
            self.assertLess(kinds.index("env_write"), kinds.index("backup"))
            ups = [i for i, row in enumerate(self.log) if row[0] == "compose" and row[1][0] == "up"]
            self.assertTrue(ups and kinds.index("backup") < ups[0])
            observed = self.hook.observed[0]["env"]
            self.assertTrue(value.encode() in observed, "backup did not see persisted key")
            snapshot = json.loads(self.state.journal_texts[0])["env_recovery"]
            self.assertEqual(snapshot["before_sha256"], hashlib.sha256(observed).hexdigest())
            self.assertTrue(value not in self.out.getvalue() and value not in self.state_text(), "key leaked")

    def test_second_apply_keeps_generated_key_and_env_bytes(self):
        self.apply()
        value = read_env(self.env_path).get(KEY) or ""
        self.assertTrue(HEX64.fullmatch(value) is not None, "first apply did not generate")
        after = self.env_bytes()
        self.log.clear()
        self.apply(self.plan(decision=UP_TO_DATE, env_changes={}))
        self.assertTrue(self.env_bytes() == after and not self.writes(), "up-to-date apply rewrote env")
        self.apply(self.plan(target="v1.2.0"))
        self.assertTrue(self.env_bytes() == self.with_versions({"FUTURE_AGI_VERSION": "v1.2.0"}, base=after))
        self.assertTrue(read_env(self.env_path).get(KEY) == value, "next update rotated key")

    def test_configured_lists_are_byte_identical(self):
        for value in (CURRENT, f"  {CURRENT}, ,{PREVIOUS}, ", "CHANGEME-" + "x" * 32):
            self.with_lines(f"{KEY}='{value}' # preserve\n")
            self.apply()
            self.assertTrue(self.env_bytes() == self.with_versions({"FUTURE_AGI_VERSION": TARGET}))
            self.assertEqual([sorted(write) for write in self.writes()], [["FUTURE_AGI_VERSION"]])
            self.assertTrue(read_env(self.env_path)[KEY] == value, "operator list changed")

    def test_utf8_byte_boundary(self):
        self.with_lines(f"{KEY}='{'é' * 16}'\n")
        self.apply()
        self.assertTrue(self.env_bytes() == self.with_versions({"FUTURE_AGI_VERSION": TARGET}))
        self.with_lines(f"{KEY}='{'é' * 15}'\n")
        self.refused_unchanged()

    def test_unicode_trim_boundary(self):
        self.with_lines(f"{KEY}=' {'é' * 15}　'\n")
        self.refused_unchanged()

    def test_short_or_keyless_lists_refuse(self):
        for value in (f"{CURRENT},short", "   ", ", ,", " ,　"):
            self.with_lines(f"{KEY}='{value}'\n")
            self.refused_unchanged()

    def test_unsafe_or_ambiguous_dotenv_refuses(self):
        for extra in (f'{KEY}="${{SYNTHETIC_STATE}}"\n', f'{KEY}="{CURRENT}\\n"\n',
                      f"{KEY}='unclosed\n", f"{KEY}={CURRENT}\n{KEY}=\n",
                      f"export {KEY}={CURRENT}\n", KEY + "\n"):
            self.with_lines(extra)
            self.refused_unchanged()

    def test_exported_empty_and_nonempty_refuse(self):
        for value in ("", CURRENT):
            self.with_lines(f"{KEY}='{CURRENT}'\n")
            self.inst.environ = {KEY: value}
            self.refused_unchanged()

    def test_generator_bad_output_refuses(self):
        # Absent policy on base still fails the apply outcome assertion, not collection.
        with self.generator(return_value="synthetic-invalid"):
            self.refused_unchanged()

    def test_valid_list_does_not_generate(self):
        self.with_lines(f"{KEY}='{CURRENT},{PREVIOUS}'\n")
        with self.generator(side_effect=AssertionError("unexpected generator")):
            self.apply()
        self.assertEqual([sorted(write) for write in self.writes()], [["FUTURE_AGI_VERSION"]])

    def test_dry_run_never_mutates_or_generates(self):
        with self.generator(side_effect=AssertionError("dry-run generated")):
            self.apply(dry_run=True)
        self.assertTrue(self.env_bytes() == self.original)
        self.assertEqual((self.writes(), self.hook.calls), ([], []))

    def test_automatic_key_policy_is_unchanged_without_mutation(self):
        # Refusal/setter boundary only: this fixture is not an enrolled rollout.
        # Base has no MCP setter, so its existing no-op is the PASS/PASS control.
        for automatic in (False, True):
            for extra, exported in (("", {}), (f"{KEY}=\n", {}),
                                    (f"{KEY}=short\n", {}),
                                    (f'{KEY}="${{SYNTHETIC_STATE}}"\n', {}),
                                    ("", {KEY: ""}), ("", {KEY: CURRENT})):
                with self.subTest(automatic=automatic, extra=extra, exported=bool(exported)):
                    self.with_lines(extra)
                    self.inst.environ = exported
                    updater = self.guarded(automatic)
                    with self.generator(side_effect=AssertionError("automatic generated")):
                        refusal = updater._refusal(
                            self.plan(), {"version": TARGET}, None, None,
                            stage=False, dry_run=False, backup_command=BACKUP,
                            allow_no_backup=False)
                        self.assertIsNone(refusal)
                        setter = getattr(updater, "_set_mcp_state_keys", None)
                        if setter is not None:
                            self.assertFalse(setter())
                    self.assertTrue(self.env_bytes() == self.original)
                    self.assertEqual((self.writes(), self.hook.calls), ([], []))
                    self.assertIsNone(self.journal())
                    self.assertEqual(self.boundaries, [])

    def test_production_manual_setter_fills_once(self):
        # Synthetic setter proof only, not a production deployment or setup run.
        for extra in ("", f"{KEY}=\n", f"{KEY}=''\n"):
            self.build(mode="production")
            self.env_path.write_bytes(self.env_bytes() + extra.encode("utf-8"))
            self.inst.reload_env()
            updater = self.updater()
            setter = getattr(updater, "_set_mcp_state_keys", None)
            self.assertIsNotNone(setter, "manual production MCP setter is absent")
            self.assertTrue(setter())
            value = read_env(self.env_path).get(KEY) or ""
            self.assertTrue(HEX64.fullmatch(value) is not None, "missing generated 64-hex key")
            self.assertEqual([sorted(write) for write in self.writes()], [[KEY]])
            self.assertTrue(bool(self.env_write_kwargs[-1].get("recovery")))
            after = self.env_bytes()
            with self.generator(side_effect=AssertionError("production setter rotated key")):
                self.assertFalse(setter())
            self.assertTrue(self.env_bytes() == after)
            self.assertEqual([sorted(write) for write in self.writes()], [[KEY]])
            self.assertEqual(self.hook.calls, [])
            self.assertTrue(value not in self.out.getvalue(), "key leaked")

    def test_unrelated_multiline_does_not_hide_absent_key(self):
        # An apparent assignment inside another value is not a persisted MCP key.
        extra = f"NOTES='first\n{KEY}=short\nlast'\n"
        self.with_lines(extra)
        try:
            self.apply()
        except Blocked:
            self.fail("valid unrelated multiline dotenv was refused")
        value = read_env(self.env_path).get(KEY) or ""
        self.assertTrue(HEX64.fullmatch(value) is not None, "absent key was not filled")
        self.assertTrue(extra.encode() in self.env_bytes(), "unrelated multiline bytes changed")
        self.assertEqual([sorted(write) for write in self.writes()], [[KEY], ["FUTURE_AGI_VERSION"]])

    def test_unrelated_multiline_preserves_configured_list(self):
        extra = f"NOTES='first\n{KEY}=short\nlast'\n{KEY}='{CURRENT},{PREVIOUS}'\n"
        self.with_lines(extra)
        with self.generator(side_effect=AssertionError("configured list generated")):
            try:
                self.apply()
            except Blocked:
                self.fail("valid unrelated multiline dotenv was refused")
        self.assertTrue(self.env_bytes() == self.with_versions({"FUTURE_AGI_VERSION": TARGET}))
        self.assertTrue(read_env(self.env_path)[KEY] == f"{CURRENT},{PREVIOUS}")
        self.assertEqual([sorted(write) for write in self.writes()], [["FUTURE_AGI_VERSION"]])

    def test_unrelated_formatting_preserves_configured_list(self):
        self.with_lines(f'  export NOTE : "keep"\r\nA="first" B=second\r\n'
                        f'{KEY}=\'{CURRENT},{PREVIOUS}\'\r\n')
        self.env_path.write_bytes(b'\xef\xbb\xbf' + self.env_bytes())
        self.original = self.env_bytes()
        self.inst.reload_env()
        with self.generator(side_effect=AssertionError("configured list generated")):
            try:
                self.apply()
            except Blocked:
                self.fail("valid unrelated BOM/indentation/inline definitions were refused")
        self.assertTrue(self.env_bytes() == self.with_versions({"FUTURE_AGI_VERSION": TARGET}))
        self.assertTrue(read_env(self.env_path)[KEY] == f"{CURRENT},{PREVIOUS}")
        self.assertEqual([sorted(write) for write in self.writes()], [["FUTURE_AGI_VERSION"]])

    def test_formatted_file_does_not_relax_target_refusals(self):
        for target in (KEY + "\n", f"export {KEY}={CURRENT}\n",
                       f"{KEY}='{CURRENT}\n{PREVIOUS}'\n",
                       f"{KEY}={CURRENT}\n{KEY}=\n", f"{KEY}='unclosed\n"):
            self.with_lines("NOTES='first\nlast'\n" + target)
            self.refused_unchanged()

    def test_automatic_valid_list_is_retained(self):
        self.with_lines(f"{KEY}='{CURRENT},{PREVIOUS}'\n")
        self.apply(updater=self.guarded())
        self.assertTrue(self.env_bytes() == self.with_versions({"FUTURE_AGI_VERSION": TARGET}))
        self.assertEqual([sorted(write) for write in self.writes()], [["FUTURE_AGI_VERSION"]])
