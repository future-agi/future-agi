"""bin/update gives an install still on the published API secret pepper its own, before the update.

A v1.58.0 Standalone or Distributed install has no FI_API_SECRET_PEPPER in .env,
so Compose hands the app and the collector the default printed in the
repository, and the release that hashes API secrets would seal every one of
them under it. bin/update applies bin/install's rule first (deploy/update/pepper.py,
ported from bin/lib/secrets.sh rotate_published_api_secret_pepper; one case table
below runs against both): an unset, empty, published or CHANGEME- pepper gets a
new 64-hex value, with the value Compose used kept in FI_API_SECRET_PEPPERS_RETIRED
so keys sealed under it still verify. It is written by its own env commit, after
the refusals and before the backup and the update's own env snapshot. A shell
export of either name refuses, an unattended run refuses (required_env_missing),
and a production install is sent to deploy/setup.sh.

The world is test_update_apply's UpdaterCase (fake Docker, real env file, real
state directory and env commits), with the pepper line taken out of its .env.
Values are compared in memory and never printed (assertTrue, not assertEqual).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploy.tests.test_update_apply import BACKUP, TARGET, UpdaterCase  # noqa: E402
from deploy.update import envfile as envfile_module  # noqa: E402
from deploy.update.apply import Blocked, Updater  # noqa: E402
from deploy.update.contracts import ReasonCode  # noqa: E402
from deploy.update.envfile import read_env  # noqa: E402
from deploy.update.plan import UP_TO_DATE  # noqa: E402
from deploy.update.receipt import secret_values  # noqa: E402
from deploy.update.settings import load_settings  # noqa: E402

PEPPER = "FI_API_SECRET_PEPPER"
RETIRED = "FI_API_SECRET_PEPPERS_RETIRED"
PUBLISHED = "local-dev-only-api-secret-pepper-replace-me"
OPERATOR = "operator-pepper-from-the-secret-store-0123456789"
OLDER = "an-older-operator-pepper-0123456789abcdef"
PLACEHOLDER = "CHANGEME-api-secret-pepper"
HEX64 = re.compile(r"[0-9a-f]{64}")
GATE = ROOT / "scripts/check_update_safety.py"
GENERATORS = ROOT / "deploy/update/env-generators.json"

# The shared rule's cases: .env text, then what both rules must do with it
# ("rotated" with the retired entries expected, "unchanged" or "refused").
CASES = (
    ("unset", "", "rotated", [PUBLISHED]),
    ("empty", f"{PEPPER}=\n", "rotated", [PUBLISHED]),
    ("published", f"{PEPPER}={PUBLISHED}\n", "rotated", [PUBLISHED]),
    ("placeholder", f"{PEPPER}={PLACEHOLDER}\n", "rotated", [PLACEHOLDER]),
    ("operator value", f"{PEPPER}={OPERATOR}\n", "unchanged", None),
    ("operator value and a retired list", f"{PEPPER}={OPERATOR}\n{RETIRED}='{OLDER}'\n",
     "unchanged", None),
    ("retired list without the default", f"{RETIRED}='{OLDER}'\n", "rotated", [OLDER, PUBLISHED]),
    ("retired list with the default",
     f"{RETIRED}=' {OLDER} ,{PUBLISHED},{OLDER}'\n{PEPPER}={PUBLISHED}\n",
     "rotated", [OLDER, PUBLISHED]),
    ("a retired value with a quote", f'{RETIRED}="o\'ld-pepper"\n', "refused", None),
    ("a placeholder with a quote", f'{PEPPER}="CHANGEME-it\'s"\n', "refused", None),
    ("a retired value with two backslashes", f"{RETIRED}='a\\\\\\\\b'\n", "refused", None),
)

# bin/lib/secrets.sh's rule as bin/install runs it (output helpers print no
# values), on the .env of the working directory, with no pepper exported.
SHELL_RULE = r"""
set -euo pipefail
ok() { printf 'ok: %s\n' "$*"; }
warn() { printf 'warn: %s\n' "$*" >&2; }
die() { printf 'die: %s\n' "$*" >&2; exit 4; }
. "$FI_ROOT/bin/lib/env.sh"
. "$FI_ROOT/bin/lib/secrets.sh"
rotate_published_api_secret_pepper
"""


def pepper_module():
    # Imported here, not at module level: a missing module fails these tests one by one.
    from deploy.update import pepper
    return pepper


def retired(values):
    entries = (values.get(RETIRED) or "").split(",")
    return [part.strip() for part in entries if part.strip()]


def text_values(text):
    return {definition.key: definition.value for definition in envfile_module._definitions(text)}


class SharedRuleTests(unittest.TestCase):
    """bin/update's rule and bin/install's decide every case alike."""

    def shell_rule(self, text):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory, ".env")
            env_file.write_text(text, encoding="utf-8")
            keep = ("PATH", "HOME", "TMPDIR", "LANG")
            environ = {key: value for key, value in os.environ.items() if key in keep}
            environ["FI_ROOT"] = str(ROOT)
            result = subprocess.run(["bash", "-c", SHELL_RULE], cwd=directory, env=environ,
                                    text=True, capture_output=True, timeout=60)
            after = env_file.read_text(encoding="utf-8")
        if result.returncode:
            return "refused", None, after
        if after == text:
            return "unchanged", None, after
        return "rotated", text_values(after), after

    def python_rule(self, text):
        pepper = pepper_module()
        try:
            current, retired_value = pepper.values(text)
            changes = pepper.rotation(current, retired_value)
        except pepper.PepperRefused:
            return "refused", None
        if changes is None:
            return "unchanged", None
        return "rotated", changes

    def test_both_rules_agree_on_every_case(self):
        pepper = pepper_module()
        for name, text, outcome, entries in CASES:
            with self.subTest(case=name):
                before = text_values(text).get(PEPPER)
                kind, changes = self.python_rule(text)
                self.assertEqual(kind, outcome, "bin/update's rule")
                shell_kind, shell_values, after = self.shell_rule(text)
                self.assertEqual(shell_kind, outcome, "bin/install's rule")
                if outcome != "rotated":
                    self.assertTrue(after == text, "bin/install changed .env")
                    continue
                for values in (changes, shell_values):
                    self.assertEqual(retired(values), entries)
                    self.assertTrue(HEX64.fullmatch(values[PEPPER]) and values[PEPPER] != before,
                                    "no new 64-hex pepper")
                # bin/update writes both as literals; Compose's reading gives the same values.
                literal = {key: envfile_module.Literal(value) for key, value in changes.items()}
                written = text_values(envfile_module.render_changes(text, literal))
                self.assertTrue(written[PEPPER] == changes[PEPPER], "the pepper did not read back")
                self.assertEqual(retired(written), entries)
                self.assertEqual(list(changes), [pepper.RETIRED, pepper.PEPPER])


class PepperStepCase(UpdaterCase):
    """UpdaterCase with a v1.58.0-shaped .env: no pepper line."""

    def render_env(self, values):
        text = super().render_env(values)
        return "".join(line for line in text.splitlines(True) if not line.startswith(PEPPER))

    def with_lines(self, extra, mode="standalone"):
        """Rebuild the world, then add these lines to its .env."""

        self.build(mode)
        self.env_path.write_bytes(self.env_bytes() + extra.encode("utf-8"))
        self.original = self.env_bytes()
        self.inst.reload_env()
        self.state.secrets = secret_values(read_env(self.env_path))

    # Not `values`: UpdaterCase keeps the env it rendered in self.values (a dict).
    def env_values(self):
        return read_env(self.env_path)

    def env_writes(self):
        return [entry[1] for entry in self.log if entry[0] == "env_write"]

    def version_writes_only(self):
        return [sorted(write) for write in self.env_writes()] == [["FUTURE_AGI_VERSION"]]

    def guarded(self, *, automatic=False):
        """A scheduled run: a guard that allows every boundary and records them."""

        self.boundaries = []
        owner = self

        class Guard:
            def validate(self, boundary, context):
                owner.boundaries.append(boundary)

        guard = Guard()
        if automatic:
            guard.automatic = SimpleNamespace()
            self.inst.runner = SimpleNamespace(deadline=time.monotonic() + 86400)
        overrides = {"FUTURE_AGI_UPDATE_BACKUP_COMMAND": BACKUP}
        settings = load_settings(self.root, overrides, environ={})
        return Updater(self.inst, settings, self.state, out=self.out, readiness=self.ready,
                       hook_runner=self.hook, automation_guard=guard,
                       guard_context=lambda engine: "locked")


class PepperStepTests(PepperStepCase):
    def test_a_v1_58_env_gets_its_own_pepper_in_one_commit_before_the_backup_and_up(self):
        self.assertNotIn(PEPPER.encode(), self.env_bytes())
        self.apply()
        values = self.env_values()
        self.assertTrue(HEX64.fullmatch(values.get(PEPPER) or ""), "no new 64-hex pepper")
        self.assertEqual(retired(values), [PUBLISHED])
        writes = self.env_writes()
        self.assertEqual(sorted(writes[0]), [PEPPER, RETIRED])  # both keys, one commit
        self.assertEqual(sorted(writes[1]), ["FUTURE_AGI_VERSION"])
        kinds = [entry[0] for entry in self.log]
        first_write = kinds.index("env_write")
        backup = kinds.index("backup")
        ups = [index for index, entry in enumerate(self.log)
               if entry[0] == "compose" and entry[1][0] == "up"]
        self.assertLess(first_write, backup)
        self.assertTrue(ups and backup < ups[0])
        # The backup ran on the env with the pepper; the update's snapshot was taken after it.
        seen = self.hook.observed[0]["env"]
        self.assertTrue(text_values(seen.decode()).get(PEPPER) == values[PEPPER],
                        "the backup saw no pepper")
        snapshot = json.loads(self.state.journal_texts[0])["env_recovery"]
        self.assertEqual(snapshot["before_sha256"], hashlib.sha256(seen).hexdigest())
        self.assertEqual(self.last_receipt()["status"], "succeeded")
        for text in (self.state_text(), self.out.getvalue()):
            self.assertTrue(values[PEPPER] not in text, "the pepper reached state or output")
        self.assertIn(f"Set {PEPPER}", self.out.getvalue())

    def test_a_second_apply_changes_no_pepper(self):
        self.apply()
        self.assertTrue(HEX64.fullmatch(self.env_values().get(PEPPER) or ""), "the first set none")
        after = self.env_bytes()
        del self.log[:]
        self.apply(self.plan(decision=UP_TO_DATE, env_changes={}))
        self.assertTrue(self.env_bytes() == after, "an up-to-date apply changed the env file")
        self.assertEqual(self.env_writes(), [])
        self.apply(self.plan(target="v1.2.0"))
        self.assertTrue(self.version_writes_only(), "the next update wrote more than a version")
        expected = self.with_versions({"FUTURE_AGI_VERSION": "v1.2.0"}, base=after)
        self.assertTrue(self.env_bytes() == expected, "the next update changed more bytes")

    def test_an_operator_pepper_is_never_touched(self):
        self.with_lines(f"{PEPPER}={OPERATOR}\n{RETIRED}='{OLDER}'\n")
        self.apply()
        self.assertTrue(self.version_writes_only(), "the step wrote")
        values = self.env_values()
        self.assertTrue(values[PEPPER] == OPERATOR and values[RETIRED] == OLDER, "a pepper changed")

    def test_an_exported_pepper_or_retired_list_refuses_and_changes_nothing(self):
        cases = (({PEPPER: ""}, ""), ({PEPPER: OPERATOR}, ""), ({RETIRED: ""}, ""),
                 ({PEPPER: PUBLISHED, RETIRED: OLDER}, ""),
                 ({PEPPER: OPERATOR}, f"{PEPPER}={OPERATOR}\n"))
        for exported, lines in cases:
            with self.subTest(exported=sorted(exported), env_has_one=bool(lines)):
                self.with_lines(lines)
                self.inst.environ = dict(exported)
                with self.assertRaises(Blocked) as caught:
                    self.apply()
                message = str(caught.exception)
                for name in exported:
                    self.assertIn(name, message)
                self.assertIn("unset", message)
                for value in (OPERATOR, OLDER):
                    self.assertTrue(value not in message, "a value was printed")
                self.assertTrue(self.env_bytes() == self.original, "the env file changed")
                self.assertEqual((self.env_writes(), self.hook.calls), ([], []))
                self.assertIsNone(self.journal())

    def test_an_automatic_update_refuses_before_its_backup_and_changes_nothing(self):
        from deploy.update.orchestrator import GuardRefused
        for automatic in (False, True):
            with self.subTest(enrolled=automatic):
                self.build()
                updater = self.guarded(automatic=automatic)
                with self.assertRaises(GuardRefused) as caught:
                    if automatic:
                        updater.apply(lambda history: self.plan(), {"version": TARGET})
                    else:
                        self.apply(updater=updater)
                self.assertEqual(caught.exception.reason_code, ReasonCode.REQUIRED_ENV_MISSING)
                # Refused before the backup boundary: no attempt reserved, no backup taken.
                self.assertEqual(self.boundaries, ["before_recovery"])
                self.assertTrue(self.env_bytes() == self.original, "the env file changed")
                self.assertEqual((self.env_writes(), self.hook.calls), ([], []))
                self.assertIsNone(self.journal())

    def test_an_automatic_update_of_an_install_with_its_own_pepper_goes_ahead(self):
        self.with_lines(f"{PEPPER}={OPERATOR}\n")
        self.apply(updater=self.guarded())
        self.assertEqual(self.last_receipt()["status"], "succeeded")
        self.assertTrue(self.env_values()[PEPPER] == OPERATOR, "the pepper changed")

    def test_the_refusal_line_for_automatic_updates_names_the_command(self):
        readme = (ROOT / "deploy/update/README.md").read_text(encoding="utf-8")
        rows = [line for line in readme.splitlines() if line.startswith("| `required_env_missing`")]
        self.assertEqual(len(rows), 1)
        self.assertIn(PEPPER, rows[0])
        self.assertIn("`./bin/update apply`", rows[0])
        # An auto-update operator is told that the one manual apply ends automatic updates.
        self.assertIn("turns automatic updates off", rows[0])
        self.assertIn("`configuration_changed`", rows[0])
        self.assertIn("no command that turns them back on", rows[0])

    def test_production_without_a_pepper_of_its_own_refuses_before_planning(self):
        for lines in ("", f"{PEPPER}=\n", f"{PEPPER}={PUBLISHED}\n", f"{PEPPER}={PLACEHOLDER}\n"):
            for dry_run in (False, True):
                with self.subTest(lines=lines, dry_run=dry_run):
                    self.with_lines(lines, mode="production")
                    with self.assertRaises(Blocked) as caught:
                        self.apply(dry_run=dry_run)
                    self.assertIn("deploy/setup.sh", str(caught.exception))
                    self.assertIn(PEPPER, str(caught.exception))
                    self.assertEqual(self.plans_seen, [])  # refused before planning
                    self.assertTrue(self.env_bytes() == self.original, "the env file changed")
                    self.assertEqual((self.env_writes(), self.hook.calls), ([], []))
        self.with_lines(f"{PEPPER}={OPERATOR}\n", mode="production")
        self.apply()
        self.assertEqual(self.last_receipt()["status"], "succeeded")


class RepositoryTests(unittest.TestCase):
    def test_the_update_safety_gate_finds_a_registered_generator_for_the_pepper(self):
        spec = importlib.util.spec_from_file_location("update_safety_gate_pepper", GATE)
        gate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gate)
        generators = gate.check_generators(ROOT, gate.document(GENERATORS))
        self.assertIn(PEPPER, generators)

    def test_the_env_reference_row_says_bin_update_sets_it_and_automatic_updates_refuse(self):
        lines = (ROOT / "deploy/env-reference.toml").read_text(encoding="utf-8").splitlines()
        row = lines.index(f'    keys = ["{PEPPER}"]')
        text = next(line for line in lines[row:row + 5] if line.lstrip().startswith("text = "))
        self.assertIn("`bin/update apply`", text)
        self.assertIn("automatic updates", text)


if __name__ == "__main__":
    unittest.main()
