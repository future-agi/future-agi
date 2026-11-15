"""Synthetic policy, API-adapter and Git-history tests; no real GitHub requests."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import base64
import hashlib
import importlib.util
import io
import json
import os
import re
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import release_autopilot as policy

# GitEvidenceTests replace published() by default; keep the real one for the retry trace.
REAL_PUBLISHED = policy.published
NOW =datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc)  # Tuesday
SHA = "a" * 40
BASE = "b" * 40
CONFIG = {"RELEASE_AUTOPILOT": "on"}
PR = {
    "number": 42, "state": "open", "draft": False, "merged": False,
    "user": {"login": policy.AUTHOR, "type": "Bot"},
    "head": {"ref": policy.BRANCH, "sha": SHA, "repo": {"full_name": policy.REPOSITORY}},
    "base": {"ref": "main", "sha": BASE, "repo": {"full_name": policy.REPOSITORY}},
    "title": "chore(main): release 1.2.3", "labels": [], "changed_files": 4, "commits": 1,
    "updated_at": "2026-11-10T09:00:00Z", "mergeable": True,
}
FILES = [{"filename": path, "status": "modified"} for path in sorted(policy.RELEASE_FILES)]
APPROVER = "futureagi-release-approver[bot]"
CHECK = {"id": 1, "name": "Backend Tests Pass", "head_sha": SHA,
         "app": {"id": 1}, "status": "completed", "conclusion": "success"}
# Required on main since 12 Nov 2026. A release PR runs the full suite: its
# Chart.yaml change matches the deploy/** path filter in e2e-ci.yml.
E2E_CHECK = {**CHECK, "id": 20, "name": "E2E Tests Pass"}


def blob_entry(text):
    raw = text.encode("utf-8")
    return {"mode": "100644", "type": "blob", "text": text,
            "sha": hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()}


def metadata_files(version):
    return {policy.MANIFEST: blob_entry(json.dumps({".": version}) + "\n"),
            policy.CHART: blob_entry("apiVersion: v2\nname: synthetic\ntype: application\n"
                                     f"version: {version} # x-release-please-version\n"
                                     f'appVersion: "v{version}" # x-release-please-version\n'
                                     'kubeVersion: ">=1.27.0-0"\n'),
            "version.txt": blob_entry(version + "\n"),
            "CHANGELOG.md": blob_entry(f"# Changelog\n\n## {version}\n\nSynthetic release notes.\n")}


METADATA = {"base_sha": BASE, "head_sha": SHA,
            "base": metadata_files("1.2.2"), "head": metadata_files("1.2.3")}


def evidence(pr=None, metadata=None):
    return deepcopy((pr or PR, FILES, [CHECK, E2E_CHECK], [], METADATA if metadata is None else metadata))


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


class MergePolicyTests(unittest.TestCase):
    def decide(self, *, pr=None, files=None, checks=None, statuses=None, metadata=None, config=None, now=NOW):
        return policy.merge_decision(deepcopy(PR if pr is None else pr),
                                     FILES if files is None else files,
                                     [CHECK, E2E_CHECK] if checks is None else checks,
                                     [] if statuses is None else statuses,
                                     METADATA if metadata is None else metadata,
                                     CONFIG if config is None else config, now)[0]

    def test_all_default_conditions_allow(self):
        self.assertTrue(self.decide())
        self.assertEqual(policy.required_checks({}), ["Backend Tests Pass", "E2E Tests Pass"])

    def test_e2e_is_required_and_a_configured_list_cannot_drop_it(self):
        self.assertFalse(self.decide(checks=[CHECK]))
        self.assertFalse(self.decide(checks=[CHECK, {**E2E_CHECK, "conclusion": "failure"}]))
        self.assertFalse(self.decide(checks=[CHECK, {**E2E_CHECK, "conclusion": "skipped"}]))
        config = {**CONFIG, "RELEASE_AUTOPILOT_REQUIRED_CHECKS": "Backend Tests Pass"}
        self.assertEqual(policy.required_checks(config), ["Backend Tests Pass", "E2E Tests Pass"])
        self.assertFalse(self.decide(checks=[CHECK], config=config))

    def test_feature_switch_requires_exact_on(self):
        for value in ("", "off", "true", "ON", " on"):
            with self.subTest(value=value):
                self.assertFalse(self.decide(config={"RELEASE_AUTOPILOT": value}))

    def test_weekday_weekend_and_explicit_override(self):
        saturday = NOW.replace(day=14)
        self.assertFalse(self.decide(now=saturday))
        self.assertTrue(self.decide(now=saturday, config={**CONFIG, "RELEASE_AUTOPILOT_WEEKENDS": "on"}))
        self.assertFalse(self.decide(now=saturday, config={**CONFIG, "RELEASE_AUTOPILOT_WEEKENDS": "true"}))
        self.assertTrue(self.decide(now=NOW.replace(day=13)))

    def test_window_boundaries_overnight_and_invalid_values(self):
        for hour, expected in ((3, False), (4, True), (14, True), (15, False)):
            with self.subTest(hour=hour):
                self.assertEqual(policy.window_allows(NOW.replace(hour=hour), "04-15"), expected)
        self.assertTrue(policy.window_allows(NOW.replace(hour=23), "22-06"))
        self.assertTrue(policy.window_allows(NOW.replace(hour=5), "22-06"))
        self.assertFalse(policy.window_allows(NOW.replace(hour=6), "22-06"))
        self.assertTrue(policy.window_allows(NOW, "00-24"))
        self.assertTrue(policy.window_allows(datetime.fromisoformat("2026-11-10T12:00:00+02:00"), "10-11"))
        for value in ("04-04", "24-25", "4-15", "04:00-15:00", "04-15\n", "99-01"):
            with self.subTest(value=value):
                self.assertFalse(self.decide(config={**CONFIG, "AUTOPILOT_HOURS_UTC": value}))
        self.assertFalse(self.decide(config={**CONFIG, "AUTOPILOT_HOURS_UTC": "11-15"}))
        self.assertTrue(self.decide(config={**CONFIG, "AUTOPILOT_HOURS_UTC": "04-15"}))

    def test_closed_merged_draft_and_unknown_states_block(self):
        for key, value in (("state", "closed"), ("draft", True), ("draft", None),
                           ("merged", True), ("merged", None), ("mergeable", None), ("mergeable", False)):
            with self.subTest(key=key, value=value):
                self.assertFalse(self.decide(pr={**PR, key: value}))

    def test_author_repository_branch_and_title_are_api_bound(self):
        cases = []
        for login, kind in (("other[bot]", "Bot"), (policy.AUTHOR, "User")):
            cases.append({**PR, "user": {"login": login, "type": kind}})
        for side in ("head", "base"):
            for field, value in (("ref", "other"), ("repo", {"full_name": "outsider/fork"})):
                cases.append({**PR, side: {**PR[side], field: value}})
        for side in ("head", "base"):
            cases.append({**PR, side: {**PR[side], "sha": "not-a-sha"}})
        for title in ("chore(main): release 1.2.3\nother", "chore(main): release 1.2.3-beta.1",
                      "chore(main): release 01.2.3", "chore(dev): release 1.2.3"):
            cases.append({**PR, "title": title})
        for candidate in cases:
            with self.subTest(candidate=candidate):
                self.assertFalse(self.decide(pr=candidate))

    def test_hold_and_missing_labels_block(self):
        for name in ("release-hold", "Release-Hold", "RELEASE-HOLD", "ReLeAsE-hOlD"):
            with self.subTest(name=name):
                self.assertFalse(self.decide(pr={**PR, "labels": [{"name": name}]}))
        for labels in (None, {}, [None], ["release-hold"], [{}], [{"name": None}],
                       [{"name": 42}], [{"name": ""}]):
            with self.subTest(labels=labels):
                self.assertFalse(self.decide(pr={**PR, "labels": labels}))
        self.assertTrue(self.decide(pr={**PR, "labels": [{"name": "autorelease: pending"}]}))

    def test_only_complete_release_metadata_file_list_is_allowed(self):
        for filename in (".github/workflows/release-images.yml", "futureagi/app/views.py",
                         "futureagi/accounts/migrations/0099_new.py", "other.md"):
            self.assertFalse(self.decide(files=[*FILES[:-1], {"filename": filename, "status": "modified"}]))
        self.assertFalse(self.decide(files=[]))
        self.assertFalse(self.decide(pr={**PR, "changed_files": 2}))
        self.assertFalse(self.decide(files=[FILES[0]] * len(FILES)))
        for status in ("removed", "renamed", None):
            self.assertFalse(self.decide(files=[*FILES[:-1], {**FILES[-1], "status": status}]))

    def test_only_a_single_release_please_commit_is_allowed(self):
        # Each of the 15 release PRs merged from 4 Oct to 3 Nov had one commit.
        self.assertTrue(self.decide(pr={**PR, "commits": 1}))
        for commits in (0, 2, None, "1", True, 1.0):
            with self.subTest(commits=commits):
                self.assertFalse(self.decide(pr={**PR, "commits": commits}))
        self.assertFalse(self.decide(pr={key: value for key, value in PR.items() if key != "commits"}))

    def test_age_boundary_configuration_unknown_and_future(self):
        self.assertTrue(self.decide(pr={**PR, "updated_at": "2026-11-10T09:30:00Z"}))
        self.assertFalse(self.decide(pr={**PR, "updated_at": "2026-11-10T09:30:01Z"}))
        self.assertTrue(self.decide(config={**CONFIG, "RELEASE_AUTOPILOT_MIN_AGE_MINUTES": "60"}))
        self.assertFalse(self.decide(config={**CONFIG, "RELEASE_AUTOPILOT_MIN_AGE_MINUTES": "61"}))
        for value in ("-1", "nan", "1.5", " 30", "30\n"):
            self.assertFalse(self.decide(config={**CONFIG, "RELEASE_AUTOPILOT_MIN_AGE_MINUTES": value}))
        for value in ("bad", None, "2026-11-10T09:00:00", "2026-11-10T10:01:00Z"):
            self.assertFalse(self.decide(pr={**PR, "updated_at": value}))
        self.assertTrue(self.decide(pr={**PR, "updated_at": "2026-11-10T10:00:00Z"},
                                    config={**CONFIG, "RELEASE_AUTOPILOT_MIN_AGE_MINUTES": "0"}))

    def test_missing_stale_pending_or_unsuccessful_checks_block(self):
        self.assertFalse(self.decide(checks=[]))
        self.assertFalse(self.decide(checks=[{**CHECK, "head_sha": BASE}]))
        self.assertFalse(self.decide(checks=[{**CHECK, "name": "Backend Tests Pass (old)"}]))
        for conclusion in (None, "failure", "cancelled", "skipped", "neutral", "timed_out", "action_required"):
            self.assertFalse(self.decide(checks=[{**CHECK, "conclusion": conclusion}]))
        self.assertFalse(self.decide(checks=[{**CHECK, "status": "in_progress"}]))
        self.assertFalse(self.decide(checks=[{**CHECK, "id": None}]))

    def test_required_list_is_configurable_and_unlisted_checks_are_advisory(self):
        e2e = {**CHECK, "id": 30, "name": "E2E", "conclusion": "failure"}
        self.assertTrue(self.decide(checks=[CHECK, E2E_CHECK, e2e]))
        config = {**CONFIG, "RELEASE_AUTOPILOT_REQUIRED_CHECKS": "Backend Tests Pass, E2E\nBackend Tests Pass"}
        self.assertEqual(policy.required_checks(config), ["Backend Tests Pass", "E2E Tests Pass", "E2E"])
        self.assertFalse(self.decide(checks=[CHECK, E2E_CHECK, e2e], config=config))
        self.assertTrue(self.decide(checks=[CHECK, E2E_CHECK, {**e2e, "conclusion": "success"}], config=config))
        self.assertFalse(self.decide(config={**CONFIG, "RELEASE_AUTOPILOT_REQUIRED_CHECKS": ", \n"}))

    def test_latest_attempt_wins_and_duplicate_producers_must_pass(self):
        failure = {**CHECK, "id": 2, "conclusion": "failure"}
        self.assertFalse(self.decide(checks=[failure, CHECK, E2E_CHECK]))
        self.assertTrue(self.decide(checks=[failure, {**CHECK, "id": 3}, E2E_CHECK]))
        self.assertFalse(self.decide(checks=[CHECK, {**failure, "app": {"id": 2}}, E2E_CHECK]))
        self.assertFalse(self.decide(checks=[CHECK, {**CHECK, "id": 3, "status": "queued"}, E2E_CHECK]))

    def reason(self, checks=None, statuses=None, config=None):
        return policy.merge_decision(deepcopy(PR), FILES, [CHECK, E2E_CHECK] if checks is None else checks,
                                     statuses or [], METADATA, config or CONFIG, NOW)

    def test_a_red_required_check_is_named_and_a_running_one_is_not_finished(self):
        red = {**E2E_CHECK, "conclusion": "failure"}
        for checks, statuses in (([CHECK, red], []), ([CHECK, {**red, "conclusion": "cancelled"}], []),
                                 ([E2E_CHECK], [{"context": "Backend Tests Pass", "sha": SHA, "id": 1,
                                                 "state": "error"}])):
            with self.subTest(checks=checks, statuses=statuses):
                allowed, reason = self.reason(checks, statuses)
                self.assertFalse(allowed)
                self.assertIn("did not succeed on this head: ", reason)
                self.assertIn("a person re-runs it", reason)
        self.assertIn(": E2E Tests Pass.", self.reason([CHECK, red])[1])
        # A queued re-run of the red check, or one in progress, is not finished.
        rerun = {**E2E_CHECK, "id": 21, "status": "queued", "conclusion": None}
        for checks in ([CHECK, red, rerun], [CHECK, {**E2E_CHECK, "status": "in_progress"}]):
            with self.subTest(checks=checks):
                allowed, reason = self.reason(checks)
                self.assertFalse(allowed)
                self.assertEqual(reason, "required checks have not finished on this head: E2E Tests Pass")
        pending = {"context": "Backend Tests Pass", "sha": SHA, "id": 1, "state": "pending"}
        self.assertEqual(self.reason([E2E_CHECK], [pending])[1],
                         "required checks have not finished on this head: Backend Tests Pass")
        self.assertEqual(self.reason([CHECK, {**E2E_CHECK, "id": None}])[1], "required check data is malformed")
        # A re-run that passes clears the wait.
        self.assertTrue(self.reason([CHECK, red, {**E2E_CHECK, "id": 21}])[0])

    NO_RESULT = ("required checks have no result on this head yet (their workflow is still running or "
                 "re-running, or no workflow reports that name): ")

    def test_a_required_name_with_no_row_says_no_result_not_running(self):
        # No row for the name: the aggregate job has not been created yet (a
        # re-run hides the old one until then), or nothing reports that name.
        stale = {**E2E_CHECK, "head_sha": BASE}
        misspelled = {**E2E_CHECK, "name": "E2E Tests Passed"}
        status_elsewhere = {"context": "E2E Tests Pass", "sha": BASE, "id": 5, "state": "success"}
        for checks, statuses in (([CHECK], []), ([CHECK, stale], []), ([CHECK, misspelled], []),
                                 ([CHECK], [status_elsewhere])):
            with self.subTest(checks=checks, statuses=statuses):
                allowed, reason = self.reason(checks, statuses)
                self.assertFalse(allowed)
                self.assertEqual(reason, self.NO_RESULT + "E2E Tests Pass")
                self.assertNotIn("have not finished", reason)
                self.assertNotIn("did not succeed", reason)
        # A configured name that no workflow reports is named, though both real checks are green.
        config = {**CONFIG, "RELEASE_AUTOPILOT_REQUIRED_CHECKS": "Release Checks Pass"}
        self.assertEqual(self.reason(config=config), (False, self.NO_RESULT + "Release Checks Pass"))
        # A running check does not hide a name with no row; both are named.
        config = {**CONFIG, "RELEASE_AUTOPILOT_REQUIRED_CHECKS": "Release Checks Pass, Python Lint Pass"}
        lint = {**CHECK, "id": 40, "name": "Python Lint Pass", "status": "in_progress"}
        self.assertEqual(self.reason([CHECK, E2E_CHECK, lint], config=config)[1],
                         "required checks have not finished on this head: Python Lint Pass; "
                         + self.NO_RESULT + "Release Checks Pass")

    def test_a_running_or_red_check_never_reads_as_no_result(self):
        red = {**E2E_CHECK, "conclusion": "failure"}
        rerun = {**E2E_CHECK, "id": 21, "status": "queued", "conclusion": None}
        pending = {"context": "E2E Tests Pass", "sha": SHA, "id": 3, "state": "pending"}
        for checks, statuses in (([CHECK, {**E2E_CHECK, "status": "in_progress"}], []), ([CHECK, red, rerun], []),
                                 ([CHECK], [pending])):
            with self.subTest(checks=checks, statuses=statuses):
                allowed, reason = self.reason(checks, statuses)
                self.assertFalse(allowed)
                self.assertEqual(reason, "required checks have not finished on this head: E2E Tests Pass")
                self.assertNotIn("no result", reason)
                self.assertNotIn("did not succeed", reason)
        # A red check is named as red even when another required name has no row.
        allowed, reason = self.reason([red])
        self.assertFalse(allowed)
        self.assertTrue(reason.startswith("required check did not succeed on this head: E2E Tests Pass."))
        self.assertNotIn("no result", reason)
        self.assertEqual(policy.check_results(SHA, ["Backend Tests Pass", "E2E Tests Pass"], [red], []),
                         (["E2E Tests Pass"], [], ["Backend Tests Pass"]))

    def test_the_doc_quotes_each_wait_reason_the_code_gives(self):
        doc = " ".join((Path(__file__).resolve().parents[1] / "docs/release-autopilot.md").read_text().split())
        red = {**E2E_CHECK, "conclusion": "failure"}
        quoted = set()
        for checks in ([CHECK, red], [CHECK, {**E2E_CHECK, "status": "in_progress"}], [CHECK]):
            reason = self.reason(checks)[1]
            quoted.add(re.split(r"[:(]", reason, maxsplit=1)[0].strip())
        self.assertEqual(len(quoted), 3)
        for prefix in quoted:
            with self.subTest(prefix=prefix):
                self.assertIn(f'"{prefix}"', doc)

    def test_legacy_statuses_are_bound_to_the_same_commit(self):
        status = {"context": "Backend Tests Pass", "sha": SHA, "id": 1, "state": "success"}
        self.assertTrue(self.decide(checks=[E2E_CHECK], statuses=[status]))
        self.assertFalse(self.decide(checks=[], statuses=[{**status, "sha": BASE}]))
        self.assertFalse(self.decide(statuses=[status, {**status, "id": 2, "state": "pending"}]))
        self.assertFalse(self.decide(checks=[{**CHECK, "conclusion": "failure"}], statuses=[status]))


class MetadataPolicyTests(unittest.TestCase):
    decide = MergePolicyTests.decide

    def test_title_versions_agree_and_only_chart_version_scalars_change(self):
        self.assertTrue(self.decide())
        for ending in ("", "\n"):
            metadata = deepcopy(METADATA)
            metadata["head"]["version.txt"] = blob_entry("1.2.3" + ending)
            self.assertTrue(self.decide(metadata=metadata))

    def test_version_file_can_be_created_or_remain_absent(self):
        metadata = deepcopy(METADATA)
        metadata["base"]["version.txt"] = None
        files = [{**row, "status": "added" if row["filename"] == "version.txt" else "modified"} for row in FILES]
        self.assertTrue(self.decide(metadata=metadata, files=files))
        self.assertFalse(self.decide(metadata=metadata))  # Incorrect modified evidence.
        metadata["head"]["version.txt"] = None
        files = [row for row in FILES if row["filename"] != "version.txt"]
        self.assertTrue(self.decide(pr={**PR, "changed_files": 3}, metadata=metadata, files=files))

    def test_non_version_chart_changes_are_not_release_metadata(self):
        original = METADATA["head"][policy.CHART]["text"]
        for text in (original.replace(">=1.27.0-0", ">=99.0.0"),
                     original.replace("name: synthetic", "name: other"),
                     original.replace("type: application", "type: library"),
                     original + "dependencies:\n- name: other\n  version: 1.0.0\n",
                     original + 'annotations:\n  other: "changed"\n',
                     original + "# unrelated edit\n"):
            with self.subTest(text=text):
                metadata = deepcopy(METADATA)
                metadata["head"][policy.CHART] = blob_entry(text)
                self.assertFalse(self.decide(metadata=metadata))

    def test_inconsistent_and_malformed_release_versions_block(self):
        for path, text in ((policy.MANIFEST, '{".": "not-a-version"}'),
                           (policy.MANIFEST, '{".": "1.2.4"}'),
                           (policy.MANIFEST, '{".": "v1.2.3"}'),
                           (policy.MANIFEST, '{".": "01.2.3"}'),
                           (policy.MANIFEST, '{".": "1.2.3", "other": "1.2.3"}'),
                           (policy.MANIFEST, '{".": "bad", ".": "1.2.3"}'),
                           (policy.MANIFEST, '["1.2.3"]'), (policy.MANIFEST, '{'),
                           ("version.txt", "1.2.4\n"), ("version.txt", "v1.2.3\n"),
                           ("version.txt", "1.2.3\nextra\n"), ("version.txt", "1.2.3\n\n")):
            with self.subTest(path=path, text=text):
                metadata = deepcopy(METADATA)
                metadata["head"][path] = blob_entry(text)
                self.assertFalse(self.decide(metadata=metadata))
        for field in ("version: 1.2.3", 'appVersion: "v1.2.3"'):
            metadata = deepcopy(METADATA)
            metadata["head"][policy.CHART] = blob_entry(metadata["head"][policy.CHART]["text"].replace(field, field.replace("1.2.3", "1.2.4")))
            self.assertFalse(self.decide(metadata=metadata))

    def test_malformed_duplicate_and_unsafe_yaml_block_even_in_base(self):
        for suffix in ("broken: [\n", "version: 1.2.3\n", "appVersion: v1.2.3\n",
                       "kubeVersion: other\n", "bad: !!python/object:os.system {}\n",
                       "one: &one [1]\ntwo: *one\n", "---\nversion: 1.2.3\n"):
            metadata = deepcopy(METADATA)
            # Same unrelated bytes on both sides must not hide malformed YAML.
            for side in ("base", "head"):
                metadata[side][policy.CHART] = blob_entry(metadata[side][policy.CHART]["text"] + suffix)
            with self.subTest(suffix=suffix):
                self.assertFalse(self.decide(metadata=metadata))

    def test_incomplete_stale_or_missing_content_blocks(self):
        for key in ("base_sha", "head_sha", "base", "head"):
            metadata = deepcopy(METADATA)
            metadata.pop(key)
            self.assertFalse(self.decide(metadata=metadata))
        for key in ("base_sha", "head_sha"):
            metadata = deepcopy(METADATA)
            metadata[key] = "c" * 40
            self.assertFalse(self.decide(metadata=metadata))
        for side in ("base", "head"):
            for path in policy.RELEASE_FILES:
                metadata = deepcopy(METADATA)
                metadata[side].pop(path)
                self.assertFalse(self.decide(metadata=metadata))
            for path in (policy.MANIFEST, policy.CHART):
                metadata = deepcopy(METADATA)
                metadata[side][path] = None
                self.assertFalse(self.decide(metadata=metadata))

    def test_symlinks_executables_submodules_and_invalid_text_block(self):
        for side in ("base", "head"):
            for path in policy.RELEASE_FILES:
                for field, value in (("mode", "120000"), ("mode", "100755"), ("mode", "160000"),
                                     ("type", "commit"), ("sha", "bad"), ("text", None),
                                     ("text", ""), ("text", "bad\0text"), ("text", "x" * (policy.MAX_BLOB_BYTES + 1))):
                    with self.subTest(side=side, path=path, field=field, value_type=type(value).__name__):
                        metadata = deepcopy(METADATA)
                        metadata[side][path][field] = value
                        self.assertFalse(self.decide(metadata=metadata))

    def test_unchanged_or_omitted_files_do_not_supply_complete_evidence(self):
        metadata = deepcopy(METADATA)
        metadata["head"][policy.CHART] = metadata["base"][policy.CHART]
        self.assertFalse(self.decide(metadata=metadata))
        files = [row for row in FILES if row["filename"] != policy.CHART]
        self.assertFalse(self.decide(pr={**PR, "changed_files": 3}, files=files))
        # The original reviewer counterexample: only kubeVersion changes.
        metadata = deepcopy(METADATA)
        metadata["head"] = deepcopy(metadata["base"])
        metadata["head"][policy.CHART] = blob_entry(metadata["head"][policy.CHART]["text"].replace(">=1.27.0-0", ">=99.0.0"))
        self.assertFalse(self.decide(pr={**PR, "changed_files": 1}, metadata=metadata,
                                    files=[{"filename": policy.CHART, "status": "modified"}]))

    def test_versions_must_advance_from_a_known_consistent_baseline(self):
        for version in ("1.2.3", "1.2.4", "2.0.0"):
            metadata = deepcopy(METADATA)
            metadata["base"] = metadata_files(version)
            self.assertFalse(self.decide(metadata=metadata))
        for path in (policy.MANIFEST, policy.CHART, "version.txt"):
            metadata = deepcopy(METADATA)
            metadata["base"][path] = metadata_files("1.2.1")[path]
            self.assertFalse(self.decide(metadata=metadata))


class EnvironmentPolicyTests(unittest.TestCase):
    def decide(self, enabled="on", event="push", ref="refs/tags/v1.2.3", version="v1.2.3", count=0, actor=policy.AUTHOR,
               triggering_actor=policy.AUTHOR, attempt="1"):
        return policy.environment_decision(enabled, event, ref, version, count, actor, triggering_actor, attempt)[0]

    def test_only_the_release_app_tag_push_skips_review(self):
        for actor in (None, "", "someone", "github-actions[bot]"):
            self.assertEqual(self.decide(actor=actor), "Production")

    def test_a_persons_rerun_of_the_release_app_run_keeps_review(self):
        # GitHub keeps GITHUB_ACTOR from attempt 1; the person shows only as the triggering actor.
        for triggering_actor in ("someone", None, "", "github-actions[bot]"):
            self.assertEqual(self.decide(triggering_actor=triggering_actor), "Production")
        for attempt in ("2", "10", None, "", 1, "01", " 1"):
            self.assertEqual(self.decide(attempt=attempt), "Production")
        self.assertEqual(self.decide(triggering_actor=policy.AUTHOR, attempt="1"), "production-auto")

    def test_only_opted_in_exact_tag_push_without_new_migrations_is_auto(self):
        self.assertEqual(self.decide(), "production-auto")
        for value in (None, "", "off", "true", "ON"):
            self.assertEqual(self.decide(enabled=value), "Production")
        for event in ("workflow_dispatch", "workflow_run", "pull_request"):
            self.assertEqual(self.decide(event=event), "Production")
        self.assertEqual(self.decide(ref="refs/heads/main"), "Production")
        self.assertEqual(self.decide(ref="refs/tags/v1.2.4"), "Production")
        for count in (1, 100, None, "0", -1, False):
            self.assertEqual(self.decide(count=count), "Production")
        # Prereleases always keep human review.
        self.assertEqual(self.decide(version="v1.2.3-beta.1", ref="refs/tags/v1.2.3-beta.1"), "Production")
        self.assertEqual(self.decide(version="v1.2.3-rc.2", ref="refs/tags/v1.2.3-rc.2"), "Production")

    def test_oss_and_ee_migration_paths_and_initializers(self):
        for path in ("futureagi/accounts/migrations/0002_new.py",
                     "futureagi/ee/billing/migrations/0002_new.py", "ee/billing/migrations/0002_new.py",
                     "futureagi/app/migrations/0002___init___data.py", "futureagi/app/migrations/0003_schema.sql"):
            self.assertTrue(policy.is_migration(path), path)
        for path in ("futureagi/app/migrations/__init__.py", "futureagi/ee/billing/migrations/__init__.py",
                     "futureagi/app/migrations/README.md", "other/app/migrations/new.py", "futureagi/app/views.py"):
            self.assertFalse(policy.is_migration(path), path)

    def test_clickhouse_schema_files_and_appliers_count_but_readers_do_not(self):
        root = "futureagi/tracer/services/clickhouse/"
        for name in ("v2/schema/028_x.sql", "v2/schema/024_spans_created_at_index.sql",
                     "v2/observed_catalog/schema.sql", "schema/dataset_cdc.sql",
                     "oss_cdc_migrations/cdc_003_x.sql", "schema.py", "v2/apply_schema.py",
                     "v2/apply_schema_rewriter.py", "v2/schema_topology.py"):
            self.assertTrue(policy.is_migration(root + name), name)
        # Routine query changes stay automatic, and SQL outside the ClickHouse tree is not schema.
        for path in (root + "query_builders/span_list.py", root + "v2/query_builders/span_list.py",
                     root + "v2/span_reader.py", root + "client.py", root + "v2/schema/README.md",
                     "futureagi/scripts/setup_peerdb_mirrors.sql", "futureagi/bin/test-db-init.sql"):
            self.assertFalse(policy.is_migration(path), path)

    def test_operator_provisioned_grouping_schema_counts_but_other_grouping_code_does_not(self):
        for path in ("futureagi/tracer/services/grouping/feature_store.py",
                     "futureagi/tracer/management/commands/provision_grouping_features.py"):
            self.assertTrue(policy.is_migration(path), path)
        for path in ("futureagi/tracer/services/grouping/clusterer.py",
                     "futureagi/tracer/management/commands/requeue_failed_grouping.py",
                     "futureagi/tracer/services/grouping/feature_store_test.py",
                     "ee/tracer/services/grouping/feature_store.py"):
            self.assertFalse(policy.is_migration(path), path)

    def test_clickhouse_appliers_and_schema_directory_exist_in_this_repository(self):
        # A rename would silently stop counting; fail here instead.
        repo = Path(__file__).resolve().parents[1]
        for path in policy.CLICKHOUSE_APPLIERS | policy.OPERATOR_SCHEMA:
            self.assertTrue((repo / path).is_file(), path)
        self.assertIn("def grouping_feature_ddl(",
                      (repo / "futureagi/tracer/services/grouping/feature_store.py").read_text())
        schema = sorted((repo / policy.CLICKHOUSE / "v2/schema").glob("*.sql"))
        self.assertTrue(schema)
        self.assertTrue(all(policy.is_migration(path.relative_to(repo).as_posix()) for path in schema))


@contextmanager
def working_directory(path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class GitEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cwd = working_directory(self.root)
        self.cwd.__enter__()
        self.addCleanup(self.cwd.__exit__, None, None, None)
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Synthetic release tests")
        self.git("config", "user.email", "synthetic@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.write("futureagi/app/migrations/0001_initial.py")
        self.commit()
        self.git("tag", "v1.0.0")
        self.write("README.txt")
        self.commit()
        self.git("tag", "v1.1.0")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.sha = self.git("rev-parse", "HEAD").strip()
        # Every earlier release shipped unless a test says otherwise (no GitHub API here).
        self.shipped = patch.object(policy, "published", return_value=True)
        self.shipped.start()
        self.addCleanup(self.shipped.stop)

    def git(self, *args):
        return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args],
                              check=True, text=True, capture_output=True).stdout

    def write(self, name, content="synthetic\n"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-m", "synthetic history")

    def release(self, *, event="push", version="v1.1.0", sha=None, env=None, **kwargs):
        output = self.root / "outputs"
        with patch.dict(os.environ, {"GITHUB_EVENT_NAME": event, "GITHUB_REF": f"refs/tags/{version}",
                                     "GITHUB_ACTOR": policy.AUTHOR, "GITHUB_TRIGGERING_ACTOR": policy.AUTHOR,
                                     "GITHUB_RUN_ATTEMPT": "1",
                                     "GITHUB_SHA": sha or self.sha, "RELEASE_AUTO_APPROVE": "on",
                                     "GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(self.root / "summary"),
                                     **(env or {})}, clear=True), \
                patch("sys.stdout", new_callable=io.StringIO):
            policy.release_environment(version, **kwargs)
        return output.read_text()

    def guard_count(self):
        """The previous release and the count since it, from the newest job summary."""
        line = [line for line in (self.root / "summary").read_text().splitlines()
                if line.startswith("Previous release:")][-1]
        match = re.fullmatch(r"Previous release: `(.*)`; migration or schema files since it: "
                             r"([0-9]+|unknown)\. The tag commit is on a reviewed branch\.", line)
        return match[1], match[2]

    def test_reachable_tag_and_no_additions_are_auto(self):
        text = self.release()
        self.assertIn("environment=production-auto\n", text)
        self.assertEqual(self.guard_count(), ("v1.0.0", "0"))
        self.assertIn(f"tag_sha={self.sha}\n", text)
        # The guard's count only picks the environment. The bump counts again
        # from what deployment main runs, so the guard exports neither value.
        self.assertEqual(sorted(line.split("=", 1)[0] for line in text.splitlines()), ["environment", "tag_sha"])

    def test_non_main_tag_is_refused_even_when_manual(self):
        self.git("checkout", "-b", "unreviewed")
        self.write("unreviewed.txt")
        self.commit()
        self.git("tag", "v1.2.0")
        with self.assertRaises(RuntimeError):
            self.release(event="workflow_dispatch", version="v1.2.0")

    def test_prerelease_is_refused_from_dev_and_reviewed_from_main(self):
        # A key environment admits v* tags, so every tag must be on main.
        self.git("tag", "v1.1.1-rc.1")
        text = self.release(version="v1.1.1-rc.1", sha=self.git("rev-parse", "HEAD").strip())
        self.assertIn("environment=Production\n", text)
        self.git("checkout", "-b", "dev")
        self.write("dev.txt")
        self.commit()
        self.git("update-ref", "refs/remotes/origin/dev", "HEAD")
        for version in ("v1.2.0-rc.1", "v1.2.0"):
            with self.subTest(version=version):
                self.git("tag", version)
                (self.root / "summary").unlink(missing_ok=True)
                with self.assertRaises(RuntimeError):
                    self.release(version=version, sha=self.git("rev-parse", "HEAD").strip())

    def decoy(self, name, commit):
        """A tag spelled like a remote-tracking branch, as any writer can push one."""
        self.git("tag", name, commit)
        self.assertEqual(self.git("rev-parse", f"refs/tags/{name}").strip(), commit)
        if name.startswith("origin/"):
            # The trap is armed: Git resolves the short name to the tag, not the branch.
            self.assertEqual(self.git("rev-parse", name).strip(), commit)

    def test_a_tag_named_like_main_on_an_old_commit_does_not_block_a_release_from_main(self):
        old = self.git("rev-parse", "v1.0.0").strip()
        for name in ("origin/main", "remotes/origin/main", "refs/remotes/origin/main"):
            with self.subTest(tag=name):
                self.decoy(name, old)
                try:
                    self.assertIn("environment=production-auto\n", self.release())
                    self.assertEqual(self.guard_count(), ("v1.0.0", "0"))
                finally:
                    self.git("tag", "-d", name)
        # A prerelease on main passes even when a tag named origin/dev exists.
        with self.subTest(tag="origin/dev"):
            self.git("tag", "v1.1.1-rc.1")
            self.decoy("origin/dev", old)
            self.assertIn("environment=Production\n",
                          self.release(version="v1.1.1-rc.1", sha=self.git("rev-parse", "HEAD").strip()))

    def test_a_tag_named_like_main_on_an_unreviewed_commit_does_not_admit_it(self):
        self.git("checkout", "-b", "unreviewed")
        self.write("unreviewed.txt")
        self.commit()
        unreviewed = self.git("rev-parse", "HEAD").strip()
        self.git("tag", "v1.2.0")
        self.git("tag", "v1.2.0-rc.1")
        for name in ("origin/main", "remotes/origin/main", "refs/remotes/origin/main", "origin/dev"):
            with self.subTest(tag=name):
                self.decoy(name, unreviewed)
                try:
                    for version in ("v1.2.0", "v1.2.0-rc.1"):
                        (self.root / "summary").unlink(missing_ok=True)
                        with self.assertRaises(RuntimeError):
                            self.release(version=version, sha=unreviewed)
                        summary = (self.root / "summary").read_text()
                        self.assertIn("could not be proved", summary)
                        self.assertNotIn("The tag commit is on a reviewed branch", summary)
                finally:
                    self.git("tag", "-d", name)

    def test_event_sha_and_approval_sha_must_not_drift(self):
        prior = self.git("rev-parse", "v1.0.0").strip()
        with self.assertRaises(RuntimeError):
            self.release(sha=prior)
        with self.assertRaises(RuntimeError):
            self.release(expected_sha=prior, event="workflow_dispatch")
        self.assertIn("environment=production-auto", self.release(expected_sha=self.sha))

    def test_unshipped_previous_release_is_not_a_baseline(self):
        with patch.object(policy, "published", return_value=False):
            self.assertEqual(policy.migration_evidence("v1.2.0", self.sha), ("v1.1.0", None))
            text = self.release()
        self.assertIn("environment=Production", text)
        # v1.1.0's previous release, v1.0.0, did not ship, so the count is unknown.
        self.assertEqual(self.guard_count(), ("v1.0.0", "unknown"))
        for error in (RuntimeError("api"), KeyError("jobs"), ValueError("json"), TypeError("shape")):
            with self.subTest(error=type(error).__name__), patch.object(policy, "published", side_effect=error):
                self.assertEqual(policy.migration_evidence("v1.2.0", self.sha), ("v1.1.0", None))

    def test_one_transient_api_failure_in_the_approve_recheck_does_not_cost_a_review(self):
        # The previous tag's run failed on a side job, so both GitHub reads happen.
        run = {"id": 7, "head_branch": "v1.0.0", "status": "completed", "conclusion": "failure"}
        jobs = [{"name": name, "conclusion": "success"} for name in policy.SHIPPED_JOBS]
        recheck = {"expected_sha": self.sha, "approved_environment": "production-auto"}
        with patch.object(policy, "published", REAL_PUBLISHED), patch.object(policy.time, "sleep"):
            with patch.object(policy, "api", side_effect=[RuntimeError("HTTP 502"), {"workflow_runs": [run]}]), \
                    patch.object(policy, "pages", side_effect=[RuntimeError("HTTP 502"), jobs]):
                self.assertIn("environment=production-auto\n", self.release(**recheck))
            # A read that fails on every try still means unknown, so the recheck refuses.
            with patch.object(policy, "api", return_value={"workflow_runs": [run]}), \
                    patch.object(policy, "pages", side_effect=RuntimeError("HTTP 502")), \
                    self.assertRaises(RuntimeError):
                self.release(**recheck)

    def test_tag_pushed_by_a_person_keeps_review(self):
        self.assertIn("environment=Production", self.release(env={"GITHUB_ACTOR": "someone"}))

    def test_rerun_of_the_release_app_run_keeps_review(self):
        for env in ({"GITHUB_TRIGGERING_ACTOR": "someone", "GITHUB_RUN_ATTEMPT": "2"},
                    {"GITHUB_RUN_ATTEMPT": "2"}, {"GITHUB_TRIGGERING_ACTOR": "someone"}):
            with self.subTest(env=env):
                self.assertIn("environment=Production\n", self.release(env=env))
                # Re-running only approve of an automatic run stops and says how to get review.
                (self.root / "summary").unlink()
                with self.assertRaises(RuntimeError):
                    self.release(env=env, expected_sha=self.sha, approved_environment="production-auto")
                self.assertIn("Re-run all jobs", (self.root / "summary").read_text())
        for variable in ("GITHUB_TRIGGERING_ACTOR", "GITHUB_RUN_ATTEMPT"):
            with self.subTest(missing=variable):
                self.assertIn("environment=Production\n", self.release(env={variable: ""}))
        self.assertIn("environment=production-auto\n",
                      self.release(expected_sha=self.sha, approved_environment="production-auto"))

    def test_edited_and_deleted_migrations_and_schema_files_count(self):
        self.write("futureagi/app/migrations/0001_initial.py", "edited\n")
        self.write("futureagi/tracer/services/clickhouse/v2/schema/028_x.sql")
        self.commit()
        sha = self.git("rev-parse", "HEAD").strip()
        self.assertEqual(policy.migration_evidence("v1.2.0", sha), ("v1.1.0", 2))

    def test_clickhouse_v2_schema_keeps_review_and_query_builders_stay_auto(self):
        self.write("futureagi/tracer/services/clickhouse/v2/query_builders/span_list.py")
        self.commit()
        self.git("tag", "v1.2.0")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        sha = self.git("rev-parse", "HEAD").strip()
        text = self.release(version="v1.2.0", sha=sha)
        self.assertIn("environment=production-auto\n", text)
        self.assertEqual(self.guard_count()[1], "0")
        self.write("futureagi/tracer/services/clickhouse/v2/schema/028_x.sql")
        self.commit()
        self.git("tag", "v1.3.0")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        sha = self.git("rev-parse", "HEAD").strip()
        text = self.release(version="v1.3.0", sha=sha)
        self.assertIn("environment=Production\n", text)
        self.assertEqual(self.guard_count()[1], "1")

    def test_grouping_schema_in_python_keeps_review_and_other_grouping_code_stays_auto(self):
        self.write("futureagi/tracer/services/grouping/clusterer.py")
        self.commit()
        self.git("tag", "v1.2.0")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        text = self.release(version="v1.2.0", sha=self.git("rev-parse", "HEAD").strip())
        self.assertIn("environment=production-auto\n", text)
        self.write("futureagi/tracer/services/grouping/feature_store.py", "def grouping_feature_ddl():\n")
        self.commit()
        self.git("tag", "v1.3.0")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        text = self.release(version="v1.3.0", sha=self.git("rev-parse", "HEAD").strip())
        self.assertIn("environment=Production\n", text)
        self.assertEqual(self.guard_count()[1], "1")

    def test_added_ee_and_oss_files_count_but_modified_and_init_do_not(self):
        self.write("futureagi/app/migrations/0001_initial.py", "modified\n")
        self.write("futureagi/app/migrations/0002_new.py")
        self.write("futureagi/ee/billing/migrations/0002_new.py")
        self.write("ee/other/migrations/0003_new.py")
        self.write("futureagi/app/migrations/__init__.py")
        self.commit()
        self.git("tag", "v1.2.0")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        sha = self.git("rev-parse", "HEAD").strip()
        # Edits count too now: three additions plus the edited 0001_initial.py.
        self.assertEqual(policy.migration_evidence("v1.2.0", sha), ("v1.1.0", 4))
        self.assertIn("environment=Production", self.release(version="v1.2.0", sha=sha))
        with self.assertRaises(RuntimeError):
            self.release(version="v1.2.0", sha=sha, approved_environment="production-auto")

    def test_rename_into_migrations_is_an_addition(self):
        self.git("mv", "README.txt", "futureagi/app/migrations/0002_data.py")
        self.commit()
        self.assertEqual(policy.migration_evidence("v1.2.0", self.git("rev-parse", "HEAD").strip()),
                         ("v1.1.0", 1))

    def test_current_prerelease_non_ancestor_and_newer_tags_are_not_baselines(self):
        self.git("tag", "v1.0.9-rc.1")
        self.git("tag", "v1.0.8-dev.1")
        self.git("tag", "v9.0.0")
        self.git("checkout", "-b", "other")
        self.write("other.txt")
        self.commit()
        self.git("tag", "v1.0.9")
        self.assertEqual(policy.migration_evidence("v1.1.0", self.sha), ("v1.0.0", 0))

    def test_no_baseline_is_unknown_not_zero(self):
        self.git("tag", "-d", "v1.0.0")
        text = self.release()
        self.assertIn("environment=Production", text)
        self.assertEqual(self.guard_count(), ("unknown", "unknown"))

    def test_diff_failure_is_manual_not_zero(self):
        with patch.object(policy, "migration_evidence", side_effect=RuntimeError("unavailable")):
            self.assertIn("environment=Production", self.release())

    def test_dispatch_retains_review_even_if_migration_free(self):
        self.assertIn("environment=Production", self.release(event="workflow_dispatch", approved_environment="Production"))

    def test_annotated_tag_is_peeled(self):
        self.git("tag", "-a", "v1.2.0", "-m", "synthetic annotated release")
        self.assertIn(f"tag_sha={self.sha}", self.release(version="v1.2.0"))

    def test_version_input_is_validated_before_git(self):
        for version in ("v1.2.3\nfoo=bar", "--all", "v01.2.3", "v1.2.3-dev.1"):
            with self.subTest(version=version), patch.object(policy, "git") as git, self.assertRaises(ValueError):
                policy.release_environment(version)
            git.assert_not_called()

    def migrations_since_command(self, pinned, version):
        result = subprocess.run([sys.executable, str(Path(policy.__file__).resolve()), "migrations-since",
                                 "--from", pinned, "--to", version], cwd=self.root, text=True,
                                capture_output=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, check=True)
        return result.stdout

    def test_migrations_since_counts_from_the_release_main_runs_across_a_skipped_release(self):
        self.write("futureagi/app/migrations/0002_new.py")
        self.commit()
        self.git("tag", "v1.2.0")
        self.write("README.txt", "changed\n")
        self.commit()
        self.git("tag", "-a", "v1.3.0", "-m", "synthetic annotated release")
        # Deployment main still runs v1.1.0: the skipped v1.2.0's migration counts.
        self.assertEqual(policy.migrations_since("v1.1.0", "v1.3.0"), 1)
        self.assertEqual(policy.migrations_since("v1.2.0", "v1.3.0"), 0)
        self.assertEqual(policy.migrations_since("v1.0.0", "v1.1.0"), 0)
        # The command prints only the count: the bump step reads stdout.
        self.assertEqual(self.migrations_since_command("v1.1.0", "v1.3.0"), "1\n")
        self.assertEqual(self.migrations_since_command("v1.2.0", "v1.3.0"), "0\n")

    def test_migrations_since_is_unknown_unless_both_are_ordered_release_tags_on_one_history(self):
        self.git("checkout", "-b", "side", "v1.0.0")
        self.write("futureagi/app/migrations/0009_side.py")
        self.commit()
        self.git("tag", "v1.0.5")
        self.git("checkout", "main")
        self.git("tag", "v1.1.0-rc.1")
        for pinned, version in (("v1.1.0", "v1.0.0"), ("v1.1.0", "v1.1.0"), ("v1.0.5", "v1.1.0"),
                                ("v1.0.9", "v1.1.0"), ("v1.0.0", "v1.9.0"), ("v1.0.0", "v1.1.0-rc.1"),
                                ("unknown", "v1.1.0"), ("", "v1.1.0"), (None, "v1.1.0"), ("v01.0.0", "v1.1.0"),
                                ("main", "v1.1.0"), ("v1.0.0", None), ("v1.0.0\nv1.0.0", "v1.1.0")):
            with self.subTest(pinned=pinned, version=version):
                self.assertIsNone(policy.migrations_since(pinned, version))
        self.assertEqual(self.migrations_since_command("v1.0.5", "v1.1.0"), "unknown\n")
        self.assertEqual(self.migrations_since_command("unknown", "v1.1.0"), "unknown\n")
        # A failed diff is unknown, never zero.
        with patch.object(policy, "count_migrations", side_effect=RuntimeError("release Git evidence unavailable")):
            self.assertIsNone(policy.migrations_since("v1.0.0", "v1.1.0"))
        self.assertEqual(policy.migrations_since("v1.0.0", "v1.1.0"), 0)


class PublishedTests(unittest.TestCase):
    """Whether the previous tag shipped, from synthetic release-images runs and jobs."""

    def run_row(self, **fields):
        return {"id": 7, "head_branch": "v1.1.0", "status": "completed", "conclusion": "failure", **fields}

    def job(self, name, conclusion="success", attempt=1):
        return {"name": name, "conclusion": conclusion, "run_attempt": attempt}

    SHIPPED = ("approve", "backend / publish")

    def setUp(self):
        # Retries never wait in tests; the patch also records each pause.
        sleep = patch.object(policy.time, "sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)

    def published(self, runs, jobs=()):
        with patch.object(policy, "git", return_value=SHA), \
                patch.object(policy, "api", return_value={"workflow_runs": runs}) as api, \
                patch.object(policy, "pages", return_value=list(jobs)) as pages:
            result = policy.published("v1.1.0")
        self.assertIn(f"head_sha={SHA}&event=push", api.call_args.args[0])
        return result, pages

    def test_successful_run_ships_without_reading_jobs(self):
        result, pages = self.published([self.run_row(conclusion="success")])
        self.assertTrue(result)
        pages.assert_not_called()

    def test_run_that_failed_on_a_side_job_after_publishing_shipped(self):
        jobs = [*map(self.job, self.SHIPPED),
                self.job("helm-chart / install the package on kind (published images)", "failure"),
                self.job("helm-chart / push, sign, attest, release", "skipped")]
        result, pages = self.published([self.run_row()], jobs)
        self.assertTrue(result)
        pages.assert_called_once_with("actions/runs/7/jobs?filter=all", "jobs")
        # Success on any attempt counts: a failed-jobs re-run that published the backend.
        jobs = [self.job("approve"), self.job("backend / publish", "failure"),
                self.job("backend / publish", "success", attempt=2)]
        self.assertTrue(self.published([self.run_row(run_attempt=2)], jobs)[0])

    def test_rejected_waiting_or_build_failed_runs_did_not_ship(self):
        cases = [
            # Production review rejected: nothing ran after approve.
            [self.job("approve", "failure"), self.job("backend / publish", "skipped")],
            # Approved, but the backend image never published.
            [self.job("approve"), self.job("backend / publish", "failure")],
            [self.job("approve")],
            [self.job("backend / publish")],
            # Similar names are not the pinned jobs.
            [self.job("approve-extra"), self.job("backend-slim / publish")],
            [], [None, "approve"],
        ]
        for jobs in cases:
            with self.subTest(jobs=jobs):
                self.assertFalse(self.published([self.run_row()], jobs)[0])
        everything = list(map(self.job, self.SHIPPED))
        for run in (self.run_row(status="in_progress", conclusion=None), self.run_row(status="waiting", conclusion=None),
                    self.run_row(head_branch="v1.1.1", conclusion="success"), self.run_row(head_branch="v1.1.1"),
                    self.run_row(id="7"), self.run_row(id=None)):
            with self.subTest(run=run):
                result, pages = self.published([run], everything)
                self.assertFalse(result)
                pages.assert_not_called()
        self.assertFalse(self.published([], everything)[0])

    def test_unreadable_or_malformed_job_evidence_is_not_shipped(self):
        # Transport failures are tried three times; malformed data fails at once.
        for error, tries in ((RuntimeError("api"), 3), (subprocess.TimeoutExpired("gh", 60), 3),
                             (KeyError("jobs"), 1), (TypeError("shape"), 1), (ValueError("json"), 1)):
            self.sleep.reset_mock()
            with self.subTest(error=type(error).__name__), patch.object(policy, "git", return_value=SHA), \
                    patch.object(policy, "api", return_value={"workflow_runs": [self.run_row()]}), \
                    patch.object(policy, "pages", side_effect=error) as pages:
                with self.assertRaises(type(error)):
                    # migration_evidence maps each of these to an unknown count (Production).
                    policy.published("v1.1.0")
                self.assertEqual(pages.call_count, tries)
                self.assertEqual(self.sleep.call_count, tries - 1)
        with patch.object(policy, "git", return_value=SHA), patch.object(policy, "api", return_value={"workflow_runs": None}):
            self.assertFalse(policy.published("v1.1.0"))

    def test_a_transient_failure_of_either_read_is_retried(self):
        jobs = list(map(self.job, self.SHIPPED))
        for error in (RuntimeError("HTTP 502"), subprocess.TimeoutExpired("gh", 60)):
            with self.subTest(read="jobs", error=type(error).__name__), patch.object(policy, "git", return_value=SHA), \
                    patch.object(policy, "api", return_value={"workflow_runs": [self.run_row()]}), \
                    patch.object(policy, "pages", side_effect=[error, jobs]) as pages:
                self.assertTrue(policy.published("v1.1.0"))
                self.assertEqual(pages.call_count, 2)
            with self.subTest(read="runs", error=type(error).__name__), patch.object(policy, "git", return_value=SHA), \
                    patch.object(policy, "api", side_effect=[error, {"workflow_runs": [self.run_row(conclusion="success")]}]) as api, \
                    patch.object(policy, "pages") as pages:
                self.assertTrue(policy.published("v1.1.0"))
                self.assertEqual(api.call_count, 2)
                pages.assert_not_called()

    def test_a_read_that_keeps_failing_still_fails_closed(self):
        with patch.object(policy, "git", return_value=SHA), \
                patch.object(policy, "api", side_effect=RuntimeError("HTTP 502")) as api, \
                self.assertRaises(RuntimeError):
            policy.published("v1.1.0")
        self.assertEqual(api.call_count, policy.READ_TRIES)
        # Fails on every try except a fourth that is never made.
        jobs = list(map(self.job, self.SHIPPED))
        with patch.object(policy, "git", return_value=SHA), \
                patch.object(policy, "api", return_value={"workflow_runs": [self.run_row()]}), \
                patch.object(policy, "pages", side_effect=[RuntimeError("HTTP 502")] * 3 + [jobs]) as pages, \
                self.assertRaises(RuntimeError):
            policy.published("v1.1.0")
        self.assertEqual(pages.call_count, 3)

    def test_shipped_job_names_match_the_release_workflows(self):
        import yaml

        workflows = Path(__file__).resolve().parents[1] / ".github/workflows"

        def jobs(name):
            return yaml.safe_load((workflows / name).read_text())["jobs"]

        release = jobs("release-images.yml")
        self.assertEqual(policy.SHIPPED_JOBS, set(self.SHIPPED))
        # GitHub shows a job without `name:` by its id, and a called workflow's
        # job as "<caller id> / <job id>". A rename here would send every release to a person.
        for name in policy.SHIPPED_JOBS:
            with self.subTest(name=name):
                caller, _, called = name.partition(" / ")
                self.assertNotIn("name", release[caller])
                if called:
                    path = release[caller]["uses"]
                    self.assertTrue(path.startswith("./.github/workflows/"))
                    self.assertNotIn("name", jobs(path.rsplit("/", 1)[1])[called])
                else:
                    self.assertNotIn("uses", release[caller])


def metadata_api_responses(metadata):
    """Synthetic Git REST objects, with no network or repository contents."""
    responses = {}

    def make_tree(nodes):
        entries = []
        for name, node in sorted(nodes.items()):
            if "text" in node:
                raw = node["text"].encode("utf-8")
                responses[f"git/blobs/{node['sha']}"] = {
                    "sha": node["sha"], "size": len(raw), "encoding": "base64",
                    "content": base64.b64encode(raw).decode() + "\n"}
                entries.append({"path": name, **{key: node[key] for key in ("sha", "mode", "type")}})
            else:
                entries.append({"path": name, "mode": "040000", "type": "tree", "sha": make_tree(node)})
        sha = hashlib.sha1(json.dumps(entries, sort_keys=True).encode()).hexdigest()
        responses[f"git/trees/{sha}"] = {"sha": sha, "truncated": False, "tree": entries}
        return sha

    for side in ("base", "head"):
        nodes = {}
        for path, entry in metadata[side].items():
            if entry is None:
                continue
            parent = nodes
            parts = path.split("/")
            for part in parts[:-1]:
                parent = parent.setdefault(part, {})
            parent[parts[-1]] = entry
        sha = metadata[f"{side}_sha"]
        responses[f"git/commits/{sha}"] = {"sha": sha, "tree": {"sha": make_tree(nodes)}}
    return responses


class MetadataAdapterTests(unittest.TestCase):
    def test_blobs_are_bound_to_both_immutable_commits_and_never_checked_out(self):
        responses = metadata_api_responses(METADATA)
        with patch.object(policy, "api", side_effect=lambda path: responses[path]) as api:
            self.assertEqual(policy.release_metadata(BASE, SHA), METADATA)
        requested = [call.args[0] for call in api.call_args_list]
        self.assertIn(f"git/commits/{BASE}", requested)
        self.assertIn(f"git/commits/{SHA}", requested)
        self.assertEqual(len(requested), len(set(requested)))
        self.assertTrue(all(path.startswith(("git/commits/", "git/trees/", "git/blobs/")) for path in requested))
        self.assertTrue(all(len(path.rsplit("/", 1)[1]) == 40 for path in requested))
        self.assertTrue(all(call.kwargs == {} for call in api.call_args_list))

    def test_absence_requires_untruncated_tree_and_optional_addition_works(self):
        metadata = deepcopy(METADATA)
        metadata["base"]["version.txt"] = None
        responses = metadata_api_responses(metadata)
        with patch.object(policy, "api", side_effect=lambda path: responses[path]):
            self.assertEqual(policy.release_metadata(BASE, SHA), metadata)
        for field, value in (("truncated", True), ("truncated", None), ("tree", None), ("sha", "c" * 40)):
            responses = metadata_api_responses(METADATA)
            root = responses[f"git/commits/{BASE}"]["tree"]["sha"]
            responses[f"git/trees/{root}"][field] = value
            with self.subTest(field=field, value=value), patch.object(policy, "api", side_effect=lambda path: responses[path]), self.assertRaises(RuntimeError):
                policy.release_metadata(BASE, SHA)

    def test_tree_and_file_types_cannot_be_hidden_by_contents_api(self):
        for mode, kind in (("120000", "blob"), ("100755", "blob"), ("160000", "commit"), ("040000", "tree")):
            metadata = deepcopy(METADATA)
            metadata["head"][policy.CHART].update(mode=mode, type=kind)
            responses = metadata_api_responses(metadata)
            with self.subTest(mode=mode), patch.object(policy, "api", side_effect=lambda path: responses[path]), self.assertRaises(RuntimeError):
                policy.release_metadata(BASE, SHA)
        responses = metadata_api_responses(METADATA)
        root = responses[f"git/commits/{SHA}"]["tree"]["sha"]
        parent = next(entry for entry in responses[f"git/trees/{root}"]["tree"] if entry["path"] == "deploy")
        parent.update(mode="120000", type="blob")
        with patch.object(policy, "api", side_effect=lambda path: responses[path]), self.assertRaises(RuntimeError):
            policy.release_metadata(BASE, SHA)

    def test_blob_size_encoding_hash_and_content_must_be_complete(self):
        blob_path = f"git/blobs/{METADATA['head'][policy.MANIFEST]['sha']}"
        for field, value in (("sha", "c" * 40), ("encoding", "none"), ("size", 0),
                             ("size", True), ("size", policy.MAX_BLOB_BYTES + 1), ("size", 1),
                             ("content", None), ("content", "not base64"),
                             ("content", base64.b64encode(b'{".":"9.9.9"}').decode())):
            responses = metadata_api_responses(METADATA)
            responses[blob_path][field] = value
            with self.subTest(field=field, value=value), patch.object(policy, "api", side_effect=lambda path: responses[path]), self.assertRaises(RuntimeError):
                policy.release_metadata(BASE, SHA)
        for raw in (b"\xff", b"text\0text"):
            responses = metadata_api_responses(METADATA)
            digest = hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()
            root = responses[f"git/commits/{SHA}"]["tree"]["sha"]
            entry = next(row for row in responses[f"git/trees/{root}"]["tree"] if row["path"] == policy.MANIFEST)
            entry["sha"] = digest
            responses[f"git/blobs/{digest}"] = {"sha": digest, "encoding": "base64", "size": len(raw),
                                               "content": base64.b64encode(raw).decode()}
            with patch.object(policy, "api", side_effect=lambda path: responses[path]), self.assertRaises(RuntimeError):
                policy.release_metadata(BASE, SHA)

    def test_missing_commit_or_duplicate_tree_evidence_blocks(self):
        responses = metadata_api_responses(METADATA)
        responses[f"git/commits/{BASE}"]["sha"] = "c" * 40
        with patch.object(policy, "api", side_effect=lambda path: responses[path]), self.assertRaises(RuntimeError):
            policy.release_metadata(BASE, SHA)
        responses = metadata_api_responses(METADATA)
        root = responses[f"git/commits/{BASE}"]["tree"]["sha"]
        entries = responses[f"git/trees/{root}"]["tree"]
        entries.append(deepcopy(entries[0]))
        with patch.object(policy, "api", side_effect=lambda path: responses[path]), self.assertRaises(RuntimeError):
            policy.release_metadata(BASE, SHA)
        with patch.object(policy, "api", side_effect=RuntimeError("unavailable")), self.assertRaises(RuntimeError):
            policy.release_metadata(BASE, SHA)
        with patch.object(policy, "api") as api, self.assertRaises(RuntimeError):
            policy.release_metadata("main", SHA)
        api.assert_not_called()


class ApiAdapterTests(unittest.TestCase):
    def test_lists_paginate_and_incomplete_responses_fail_closed(self):
        with patch.object(policy, "api", side_effect=[[{}] * 100, [{"id": 101}]]) as api:
            self.assertEqual(len(policy.pages("pulls?state=open")), 101)
            self.assertIn("&per_page=100&page=2", api.call_args.args[0])
        with patch.object(policy, "api", return_value={}), self.assertRaises(RuntimeError):
            policy.pages("pulls")
        with patch.object(policy, "api", return_value=[{}] * 100), self.assertRaises(RuntimeError):
            policy.pages("pulls")

    def test_snapshot_uses_head_endpoints_and_binds_status_sha(self):
        def listing(path, key=None):
            if "/files" in path:
                return FILES
            if "/check-runs" in path:
                self.assertIn(SHA, path)
                return [CHECK, E2E_CHECK]
            self.assertIn(f"commits/{SHA}/statuses", path)
            return [{"id": 1, "context": "Backend Tests Pass", "state": "success"}]
        with patch.object(policy, "api", return_value=PR), patch.object(policy, "pages", side_effect=listing), \
                patch.object(policy, "release_metadata", return_value=METADATA) as metadata:
            result = policy.snapshot(42)
            self.assertEqual(result[3][0]["sha"], SHA)
            self.assertEqual(result[4], METADATA)
            metadata.assert_called_once_with(BASE, SHA)
        with patch.object(policy, "api", side_effect=[PR, {**PR, "labels": [{"name": "release-hold"}]}]), \
                patch.object(policy, "pages", side_effect=listing), patch.object(policy, "release_metadata", return_value=METADATA), \
                self.assertRaises(RuntimeError):
            policy.snapshot(42)

    def test_api_keeps_only_the_http_status_of_a_failure(self):
        # gh (pkg/cmd/api/api.go) prints "gh: <message> (HTTP NNN)" and then
        # only fixed hint lines, so its status is the last line ending that way.
        for stderr, status in (("gh: Pull Request is not mergeable (HTTP 405)\n", 405),
                               # A ruleset refusal: a multi-line message, status on a later line.
                               ("gh: Repository rule violations found\n\nAt least 1 approving review "
                                "is required by reviewers with write access.\n\n (HTTP 405)\n", 405),
                               # Message text that mimics the suffix does not pick the status.
                               ("gh: Validation Failed (HTTP 422)\nprivate detail (HTTP 500)\n", 500),
                               ("gh: private detail (HTTP 405)\n\n (HTTP 502)\n", 502),
                               # gh's hint lines after the status line are skipped.
                               ("gh: Not Found (HTTP 404)\ngh: This API operation needs the \"repo\" "
                                "scope. To request it, run:  gh auth refresh -h github.com -s repo\n", 404),
                               ("gh: Must authenticate via SSO (HTTP 403)\n"
                                "Authorize in your web browser: https://github.com/orgs/x/sso\n", 403),
                               ("gh: request failed\n", None), ("", None),
                               ("gh: (HTTP 405) private text\n", None),
                               ("gh: Repository rule violations found\n\nprivate reason\n", None)):
            with self.subTest(stderr=stderr), \
                    patch.object(policy.subprocess, "run",
                                 return_value=subprocess.CompletedProcess([], 1, "private body", stderr)):
                with self.assertRaises(policy.GitHubError) as error:
                    policy.api("pulls/42/merge", method="PUT", data={"sha": SHA})
                self.assertEqual(error.exception.status, status)
                self.assertNotIn("private", str(error.exception))
                self.assertNotIn("mergeable", str(error.exception))

    def test_api_uses_stdin_payload_and_never_echoes_error_body(self):
        with patch.object(policy.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, '{"merged": true}', '')) as run:
            self.assertTrue(policy.api("pulls/42/merge", method="PUT", data={"sha": SHA}, token="synthetic-app")["merged"])
            self.assertEqual(run.call_args.kwargs["env"]["GH_TOKEN"], "synthetic-app")
            self.assertEqual(json.loads(run.call_args.kwargs["input"]), {"sha": SHA})
        with patch.object(policy.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, '', 'private response')):
            with self.assertRaises(RuntimeError) as error:
                policy.api("pulls/42")
            self.assertNotIn("private response", str(error.exception))


class ExecutionTests(unittest.TestCase):
    def run_merge(self, *, first=None, final=None, first_metadata=None, final_metadata=None,
                  already_approved=False, merge_result=True, prior_marker=None, merge_error=None,
                  extra_comments=(), gates=(True, "synthetic gates passed"), extra_reviews=(),
                  approver=APPROVER, review_user=None):
        first = deepcopy(first or PR)
        final = deepcopy(final or first)
        marker = prior_marker or f"<!-- release-autopilot-v2:{SHA}:{BASE} -->"
        comments = [{"user": {"login": "github-actions[bot]"}, "body": marker}] if already_approved else []
        comments += list(extra_comments)
        reviews = [{"id": 1, "user": {"login": APPROVER, "type": "Bot"}, "body": marker,
                    "state": "APPROVED", "commit_id": SHA}] if already_approved else []
        reviews += list(extra_reviews)
        if already_approved:
            first["labels"].append({"name": policy.APPROVED_LABEL})
            final["labels"].append({"name": policy.APPROVED_LABEL})
        env = {**CONFIG, "EXPECTED_PR": "42", "EXPECTED_HEAD_SHA": SHA,
               "RELEASE_APP_TOKEN": "synthetic-app", "GH_TOKEN": "synthetic-actions",
               "APPROVER_TOKEN": "synthetic-approver", "APPROVER_LOGIN": approver}

        def fake_api(path, **kwargs):
            if path.endswith("/merge") and merge_error is not None:
                raise merge_error
            if path.endswith("/reviews"):
                return {"id": 9, "user": {"login": APPROVER, "type": "Bot"} if review_user is None else review_user}
            return {"merged": merge_result}
        with patch.dict(os.environ, env, clear=True), patch.object(policy, "datetime", FrozenDatetime), \
                patch.object(policy, "snapshot", side_effect=[evidence(first, first_metadata), evidence(final, final_metadata)]), \
                patch.object(policy, "pages", side_effect=[comments, reviews]), \
                patch.object(policy, "api", side_effect=fake_api) as api, \
                patch.object(policy, "release_gates", **({"side_effect": gates} if isinstance(gates, list)
                                                          else {"return_value": gates})), \
                patch("sys.stdout", new_callable=io.StringIO):
            # Kept on the test so a case that expects an exception can still inspect the writes.
            self.calls = api.call_args_list
            policy.autopilot(merge=True)
            return api.call_args_list

    def test_approver_app_approves_release_app_merges_with_expected_sha_and_audit(self):
        calls = self.run_merge()
        approval = next(call for call in calls if call.args[0] == "pulls/42/reviews")
        merge = next(call for call in calls if call.args[0] == "pulls/42/merge")
        self.assertEqual(approval.kwargs["token"], "synthetic-approver")
        # Comments and labels stay on the workflow token.
        for call in calls:
            if call.args[0].startswith("issues/") or call.args[0].startswith("labels/"):
                self.assertNotIn("token", call.kwargs)
        self.assertEqual(approval.kwargs["data"]["event"], "APPROVE")
        self.assertEqual(approval.kwargs["data"]["commit_id"], SHA)
        self.assertEqual(merge.kwargs["token"], "synthetic-app")
        self.assertEqual(merge.kwargs["data"], {"sha": SHA, "merge_method": "squash"})
        labels = [call.kwargs["data"]["labels"] for call in calls if call.args[0] == "issues/42/labels"]
        self.assertEqual(labels, [[policy.APPROVED_LABEL], [policy.LABEL]])
        body = approval.kwargs["data"]["body"]
        self.assertIn("Required checks on this SHA: Backend Tests Pass, E2E Tests Pass (success).", body)
        self.assertNotIn("advisory", body)

    def test_missing_or_unsafe_approver_identity_makes_no_request(self):
        for login in ("", "github-actions[bot]", policy.AUTHOR, "someone", "Upper[bot]",
                      "approver[bot]\n", "-approver[bot]", f"{APPROVER} "):
            with self.subTest(login=login):
                with self.assertRaises(ValueError):
                    self.run_merge(approver=login)
                self.assertEqual(self.calls, [])
        env = {**CONFIG, "EXPECTED_PR": "42", "EXPECTED_HEAD_SHA": SHA, "APPROVER_LOGIN": APPROVER}
        with patch.dict(os.environ, env, clear=True), patch.object(policy, "api") as api, \
                patch.object(policy, "pages") as pages, patch("sys.stdout", new_callable=io.StringIO), \
                self.assertRaises(ValueError):
            policy.autopilot(merge=True)
        api.assert_not_called()
        pages.assert_not_called()

    def test_approval_recorded_under_another_login_stops_before_merge(self):
        for user in ({"login": "github-actions[bot]", "type": "Bot"}, {"login": APPROVER, "type": "User"}, {}, "x"):
            with self.subTest(user=user), self.assertRaises(RuntimeError):
                self.run_merge(review_user=user)
            self.assertFalse(any(call.args[0].endswith("/merge") for call in self.calls))

    def test_workflow_token_reviews_are_neither_reused_nor_a_stop(self):
        marker = f"<!-- release-autopilot-v2:{SHA}:{BASE} -->"
        for state in ("APPROVED", "DISMISSED", "CHANGES_REQUESTED"):
            with self.subTest(state=state):
                other = {"id": 5, "user": {"login": "github-actions[bot]", "type": "Bot"}, "body": marker,
                         "state": state, "commit_id": SHA}
                calls = self.run_merge(extra_reviews=[other])
                approval = next(call for call in calls if call.args[0] == "pulls/42/reviews")
                self.assertEqual(approval.kwargs["token"], "synthetic-approver")
                self.assertTrue(any(call.args[0] == "pulls/42/merge" for call in calls))

    def test_a_dismissed_app_approval_on_this_head_waits_for_a_person(self):
        dismissed = {"id": 7, "user": {"login": APPROVER, "type": "Bot"}, "state": "DISMISSED",
                     "commit_id": SHA, "body": f"<!-- release-autopilot-v2:{SHA}:{BASE} -->"}
        calls = self.run_merge(extra_reviews=[dismissed])
        self.assertFalse(any(call.args[0] == "pulls/42/reviews" for call in calls))
        self.assertFalse(any(call.args[0].endswith("/merge") for call in calls))
        # A dismissal on an older head (for example a stale review after a push) does not stop the new head.
        calls = self.run_merge(extra_reviews=[{**dismissed, "commit_id": "c" * 40}])
        self.assertTrue(any(call.args[0] == "pulls/42/reviews" for call in calls))
        self.assertTrue(any(call.args[0].endswith("/merge") for call in calls))

    def test_update_cooldown_is_not_bypassed_for_own_audit(self):
        calls = self.run_merge(final={**PR, "updated_at": "2026-11-10T10:00:00Z"})
        self.assertFalse(any(call.args[0].endswith("/merge") for call in calls))

    def test_next_eligible_run_does_not_reset_cooldown_with_duplicate_writes(self):
        calls = self.run_merge(already_approved=True)
        self.assertFalse(any(call.args[0] == "pulls/42/reviews" for call in calls))
        self.assertTrue(any(call.args[0] == "pulls/42/merge" for call in calls))
        comments = [call for call in calls if call.args[0] == "issues/42/comments"]
        self.assertEqual(len(comments), 1)  # Success comment only, after merge.

    def test_older_policy_or_base_approval_does_not_replace_current_audit(self):
        for marker in (f"<!-- release-autopilot-v1:{SHA} -->",
                       f"<!-- release-autopilot-v2:{SHA}:{'c' * 40} -->"):
            calls = self.run_merge(already_approved=True, prior_marker=marker)
            approval = next(call for call in calls if call.args[0] == "pulls/42/reviews")
            self.assertTrue(approval.kwargs["data"]["body"].startswith(f"<!-- release-autopilot-v2:{SHA}:{BASE} -->"))
            self.assertIn("content/mode allowlist", approval.kwargs["data"]["body"])
            self.assertEqual(len([call for call in calls if call.args[0] == "issues/42/comments"]), 2)

    def test_changed_head_base_or_hold_after_approval_never_merges(self):
        cases = [{**PR, "head": {**PR["head"], "sha": "c" * 40}},
                 {**PR, "base": {**PR["base"], "sha": "c" * 40}},
                 {**PR, "labels": [{"name": "release-hold"}]}]
        for final in cases:
            with self.subTest(final=final):
                calls = self.run_merge(final=final)
                self.assertFalse(any(call.args[0].endswith("/merge") for call in calls))

    def test_hold_capitalization_blocks_before_writes_and_after_approval(self):
        for name in ("release-hold", "Release-Hold", "RELEASE-HOLD"):
            with self.subTest(name=name):
                held = {**PR, "labels": [{"name": name}]}
                self.assertEqual(self.run_merge(first=held), [])
                for approved in (False, True):
                    calls = self.run_merge(final=held, already_approved=approved)
                    self.assertFalse(any(call.args[0].endswith("/merge") for call in calls))

    def test_missing_or_malformed_labels_never_approve_or_merge(self):
        for labels in (None, [None], [{}], [{"name": None}], [{"name": ""}]):
            candidate = {**PR, "labels": labels}
            self.assertEqual(self.run_merge(first=candidate), [])
            calls = self.run_merge(final=candidate)
            self.assertFalse(any(call.args[0].endswith("/merge") for call in calls))

    def test_metadata_contents_are_checked_initially_and_after_approval(self):
        for path, text in ((policy.CHART, METADATA["head"][policy.CHART]["text"].replace(">=1.27.0-0", ">=99.0.0")),
                           (policy.MANIFEST, '{".": "invalid"}'), ("version.txt", "1.2.4\n")):
            metadata = deepcopy(METADATA)
            metadata["head"][path] = blob_entry(text)
            self.assertEqual(self.run_merge(first_metadata=metadata), [])
            calls = self.run_merge(final_metadata=metadata)
            self.assertTrue(any(call.args[0] == "pulls/42/reviews" for call in calls))
            self.assertFalse(any(call.args[0].endswith("/merge") for call in calls))

    def test_changed_head_since_check_performs_no_writes(self):
        calls = self.run_merge(first={**PR, "head": {**PR["head"], "sha": "c" * 40}})
        self.assertEqual(calls, [])

    def test_failed_merge_is_not_labelled_as_merged(self):
        with self.assertRaises(RuntimeError):
            self.run_merge(merge_result=False)

    REFUSED = f"<!-- release-autopilot-v2-merge-refused:{SHA} -->"

    def test_first_refused_merge_defers_with_the_approval_kept_and_a_head_bound_record(self):
        for status in (405, 422):
            with self.subTest(status=status):
                calls = self.run_merge(merge_error=policy.GitHubError(status))
                self.assertTrue(any(call.args[0] == "pulls/42/reviews" for call in calls))
                labels = [call.kwargs["data"]["labels"] for call in calls if call.args[0] == "issues/42/labels"]
                self.assertEqual(labels, [[policy.APPROVED_LABEL]])
                record = [call.kwargs["data"]["body"] for call in calls if call.args[0] == "issues/42/comments"]
                self.assertTrue(record[-1].startswith(self.REFUSED))
                self.assertNotIn("merged verified head", record[-1])

    def test_second_refusal_of_the_same_head_fails_the_run(self):
        mine = {"user": {"login": "github-actions[bot]"}, "body": self.REFUSED + "\nearlier"}
        with self.assertRaises(policy.GitHubError):
            self.run_merge(merge_error=policy.GitHubError(405), extra_comments=[mine])
        self.assertFalse(any(call.kwargs.get("data", {}).get("body", "").startswith(self.REFUSED)
                             for call in self.calls))
        # A record for another head, or one written by someone else, is not a first refusal.
        for other in ({**mine, "body": f"<!-- release-autopilot-v2-merge-refused:{'c' * 40} -->"},
                      {**mine, "user": {"login": "someone"}}):
            with self.subTest(other=other):
                calls = self.run_merge(merge_error=policy.GitHubError(405), extra_comments=[other])
                self.assertTrue(any(call.kwargs.get("data", {}).get("body", "").startswith(self.REFUSED)
                                    for call in calls))

    def test_a_gate_refusal_such_as_a_strict_main_writes_nothing(self):
        reason = "main requires an up-to-date branch; update the release branch or remove the rule"
        self.assertEqual(self.run_merge(gates=(False, reason)), [])

    def test_a_strict_main_seen_only_at_the_recheck_defers_with_one_approval(self):
        # GitHub can report "blocked" until the approval exists and "behind" after it.
        reason = "main requires an up-to-date branch; update the release branch or remove the rule"
        calls = self.run_merge(gates=[(True, "synthetic gates passed"), (False, reason)])
        self.assertEqual(len([call for call in calls if call.args[0] == "pulls/42/reviews"]), 1)
        self.assertFalse(any(call.args[0].endswith("/merge") for call in calls))
        self.assertFalse(any(call.kwargs.get("data", {}).get("body", "").startswith(self.REFUSED)
                             for call in calls))

    def test_other_merge_failures_still_fail_the_run_without_a_refusal_record(self):
        for status in (None, 409, 500, 502):
            with self.subTest(status=status):
                with self.assertRaises(policy.GitHubError):
                    self.run_merge(merge_error=policy.GitHubError(status))
                self.assertTrue(any(call.args[0] == "pulls/42/merge" for call in self.calls))
                self.assertFalse(any(call.kwargs.get("data", {}).get("body", "").startswith(self.REFUSED)
                                     for call in self.calls))

    def test_off_does_not_even_read_api(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(policy, "api") as api, \
                patch("sys.stdout", new_callable=io.StringIO):
            policy.autopilot()
            api.assert_not_called()

    def test_missing_or_ambiguous_candidate_performs_no_writes(self):
        for prs in ([], [{"number": 1}, {"number": 2}]):
            with patch.dict(os.environ, CONFIG, clear=True), patch.object(policy, "pages", return_value=prs), \
                    patch.object(policy, "api") as api, patch("sys.stdout", new_callable=io.StringIO):
                policy.autopilot()
                api.assert_not_called()

    def poll(self, checks):
        """Run the check job's command on one release PR; return its outputs, summary and mocks."""
        with tempfile.TemporaryDirectory() as temp:
            output, summary = Path(temp) / "output", Path(temp) / "summary"
            env = {**CONFIG, "GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(summary)}
            with patch.dict(os.environ, env, clear=True), patch.object(policy, "datetime", FrozenDatetime), \
                    patch.object(policy, "pages", return_value=[{"number": 42}]), \
                    patch.object(policy, "snapshot", return_value=(deepcopy(PR), FILES, checks, [], METADATA)), \
                    patch.object(policy, "api") as api, \
                    patch.object(policy, "release_gates", return_value=(False, "synthetic gate")) as gates, \
                    patch("sys.stdout", new_callable=io.StringIO):
                policy.autopilot()
            return output.read_text(), summary.read_text(), api, gates

    def test_a_red_required_check_is_a_quiet_wait_that_names_the_check(self):
        output, summary, api, gates = self.poll([CHECK, {**E2E_CHECK, "conclusion": "failure"}])
        # No exception, so the run is green; no write and no further read.
        self.assertEqual(output, f"eligible=false\nnumber=42\nsha={SHA}\n")
        self.assertIn("required check did not succeed on this head: E2E Tests Pass.", summary)
        api.assert_not_called()
        gates.assert_not_called()
        # A running check gives a different reason; a green one goes on to the live gates.
        output, summary, api, gates = self.poll([CHECK, {**E2E_CHECK, "status": "in_progress"}])
        self.assertIn("have not finished on this head: E2E Tests Pass", summary)
        self.assertNotIn("did not succeed", summary)
        self.assertNotIn("no result", summary)
        gates.assert_not_called()
        # No row yet for a required name, as during a re-run: the same quiet wait, with its own reason.
        output, summary, api, gates = self.poll([CHECK])
        self.assertEqual(output, f"eligible=false\nnumber=42\nsha={SHA}\n")
        self.assertIn("have no result on this head yet (their workflow is still running or re-running, or no "
                      "workflow reports that name): E2E Tests Pass", summary)
        self.assertNotIn("have not finished", summary)
        self.assertNotIn("did not succeed", summary)
        api.assert_not_called()
        gates.assert_not_called()
        output, summary, api, gates = self.poll([CHECK, E2E_CHECK])
        gates.assert_called_once()
        self.assertIn("synthetic gate", summary)


def activity_row(kind, after, login=policy.AUTHOR, user_type="Bot", before="0" * 40, **fields):
    """One row of GET /repos/{repo}/activity, shaped like the live release-branch feed."""
    return {"id": 1, "node_id": "synthetic", "before": before, "after": after,
            "ref": f"refs/heads/{policy.BRANCH}", "timestamp": "2026-11-03T14:12:20Z",
            "activity_type": kind, "actor": {"login": login, "type": user_type}, **fields}


# Newest first, as GitHub returns it: this release's creation and pushes by the
# release App, then the previous release's branch, deleted by the person who merged it.
ACTIVITY = [activity_row("force_push", SHA, before="e" * 40), activity_row("push", "e" * 40, before="f" * 40),
            activity_row("branch_creation", "f" * 40),
            activity_row("branch_deletion", "0" * 40, login="a-person", user_type="User", before="9" * 40),
            activity_row("push", "9" * 40, before="8" * 40), activity_row("branch_creation", "8" * 40)]
ACTIVITY_PATH = "activity?ref=refs%2Fheads%2Frelease-please--branches--main&direction=desc&per_page=100"


class ReleaseBranchPushTests(unittest.TestCase):
    def test_a_branch_only_the_release_app_created_and_pushed_is_allowed(self):
        self.assertTrue(policy.release_branch_pushes(ACTIVITY, SHA)[0])
        # The common case: one push right after the creation.
        self.assertTrue(policy.release_branch_pushes(ACTIVITY[1:], "e" * 40)[0])
        # A person's rows from an earlier lifetime of the reused branch name do not count.
        older = [activity_row("push", "7" * 40, login="a-person", user_type="User")]
        self.assertTrue(policy.release_branch_pushes(ACTIVITY + older, SHA)[0])

    def test_a_persons_push_waits_for_a_person_even_after_a_bot_rebuild(self):
        person = activity_row("push", SHA, login="a-writer", user_type="User", before="e" * 40)
        allowed, reason = policy.release_branch_pushes([person] + ACTIVITY[1:], SHA)
        self.assertFalse(allowed)
        self.assertIn("a person merges this release", reason)
        # release-please force-pushes over it, but the branch keeps the same
        # lifetime, so the person's row still counts.
        rebuilt = [activity_row("force_push", SHA, before="d" * 40),
                   activity_row("push", "d" * 40, login="a-writer", user_type="User", before="e" * 40)] + ACTIVITY[1:]
        self.assertFalse(policy.release_branch_pushes(rebuilt, SHA)[0])
        # So does a person or another App re-creating the branch.
        for actor in ({"login": "a-writer", "type": "User"}, {"login": "github-actions[bot]", "type": "Bot"},
                      {"login": policy.AUTHOR, "type": "User"}, {"login": policy.AUTHOR}, {}, None, "x"):
            with self.subTest(actor=actor):
                creation = dict(ACTIVITY[2], actor=actor)
                self.assertFalse(policy.release_branch_pushes(ACTIVITY[:2] + [creation] + ACTIVITY[3:], SHA)[0])
        missing = [{key: value for key, value in row.items() if key != "actor"} for row in ACTIVITY]
        self.assertFalse(policy.release_branch_pushes(missing, SHA)[0])

    def test_unknown_or_incomplete_history_waits(self):
        # The feed has not caught up with the PR head yet.
        allowed, reason = policy.release_branch_pushes(ACTIVITY[1:], SHA)
        self.assertFalse(allowed)
        self.assertIn("not the PR head yet", reason)
        # No creation among the rows read: more pushes than one page holds.
        allowed, reason = policy.release_branch_pushes(ACTIVITY[:2], SHA)
        self.assertFalse(allowed)
        self.assertIn("creation is not among", reason)
        for activity in (None, {}, [], "rows", [None], [{"after": SHA}], ACTIVITY[:1] + [None] + ACTIVITY[1:],
                         [dict(ACTIVITY[0], ref="refs/heads/other")] + ACTIVITY[1:],
                         ACTIVITY[:1] + [dict(ACTIVITY[1], ref=None)] + ACTIVITY[2:]):
            with self.subTest(activity=activity):
                self.assertFalse(policy.release_branch_pushes(activity, SHA)[0])


class ReleaseGateTests(unittest.TestCase):
    NOW = datetime(2026, 11, 11, 12, tzinfo=timezone.utc)

    def gates(self, runs, behind=0, main_date="2026-11-11T10:00:00Z", config=None, pr=None, activity=None,
              jobs=None):
        responses = {"git/ref/heads/main": {"object": {"sha": "c" * 40}},
                     f"compare/{'c' * 40}...{SHA}": {"behind_by": behind},
                     f"commits/{'c' * 40}": {"commit": {"committer": {"date": main_date}}},
                     ACTIVITY_PATH: ACTIVITY if activity is None else activity}
        # Each run's jobs, as the jobs endpoint lists them (latest attempt). By
        # default the check job ends like its run; `jobs` overrides by run id.
        for run in runs:
            if isinstance(run, dict) and type(run.get("id")) is int:
                listed = (jobs[run["id"]] if jobs and run["id"] in jobs
                          else self.callout_jobs(run.get("status"), run.get("conclusion")))
                responses[f"actions/runs/{run['id']}/jobs?per_page=100"] = listed
        with patch.object(policy, "pages", return_value=runs) as pages, \
                patch.object(policy, "api", side_effect=lambda path, **kw: responses[path]):
            self.pages = pages
            return policy.release_gates(pr or PR, config or {}, self.NOW)

    def test_a_persons_push_to_the_release_branch_stops_before_any_other_read(self):
        person = activity_row("push", SHA, login="a-writer", user_type="User", before="e" * 40)
        allowed, reason = self.gates([self.callout_run()], activity=[person] + ACTIVITY[1:])
        self.assertFalse(allowed)
        self.assertIn("someone other than the release App", reason)
        self.pages.assert_not_called()
        # The feed is read once, newest first, by cursor page size; pages() would
        # repeat its first page, because the endpoint ignores page numbers.
        self.assertTrue(self.gates([self.callout_run()])[0])
        self.assertIsNone(re.search(r"[?&]page=", ACTIVITY_PATH))

    # A run's pull_requests entry, in the shape of run 43330391546 (PR #3872).
    RELEASE_PR_ENTRY = {"id": 1, "number": PR["number"], "url": "https://api.github.com/repos/x/pulls/42",
                        "head": {"ref": policy.BRANCH, "sha": SHA}, "base": {"ref": "main", "sha": BASE}}
    # A second PR from the release branch into dev. GitHub lists every open PR
    # from a branch on each of its runs (seen on PRs #549 and #550), so both
    # PRs' runs carry both entries.
    DEV_PR_ENTRY = {**RELEASE_PR_ENTRY, "id": 2, "number": 77, "base": {"ref": "dev", "sha": "d" * 40}}

    def callout_run(self, **fields):
        return {"id": 5, "path": policy.CALLOUT_WORKFLOW, "head_sha": SHA, "status": "completed",
                "conclusion": "success", "head_branch": policy.BRANCH,
                "head_repository": {"full_name": policy.REPOSITORY},
                "pull_requests": [self.RELEASE_PR_ENTRY], **fields}

    @staticmethod
    def callout_jobs(check_status="completed", check_conclusion="success", self_test=True):
        """The jobs endpoint's shape for dev's callout workflow (run 43330391546)."""
        rows = [{"id": 11, "name": "self-test", "status": "completed", "conclusion": "success"}] if self_test else []
        if check_status is not None:
            rows.append({"id": 12, "name": policy.CALLOUT_JOB, "status": check_status, "conclusion": check_conclusion})
        return {"total_count": len(rows), "jobs": rows}

    def dev_pr_run(self, **fields):
        """A run for a PR from the release branch into dev: check skipped, run green."""
        return self.callout_run(id=6, pull_requests=[self.RELEASE_PR_ENTRY, self.DEV_PR_ENTRY], **fields)

    FOREIGN = ({"head_repository": {"full_name": "outsider/future-agi"}},
               {"head_branch": "release-please--x"}, {"head_repository": None}, {"head_branch": None})

    def test_runs_from_other_prs_at_the_same_commit_neither_pass_nor_block(self):
        for foreign in self.FOREIGN:
            with self.subTest(foreign=foreign):
                # A newer foreign success does not override the release PR's own failure.
                self.assertFalse(self.gates([self.callout_run(conclusion="failure"),
                                             self.callout_run(id=6, **foreign)])[0])
                self.assertFalse(self.gates([self.callout_run(**foreign)])[0])
                # A newer foreign skipped, held or failed run does not stall a passed release PR.
                for outcome in ({"conclusion": "skipped"}, {"conclusion": "action_required"},
                                {"conclusion": "failure"}, {"status": "in_progress", "conclusion": None}):
                    self.assertTrue(self.gates([self.callout_run(),
                                                self.callout_run(id=6, **foreign, **outcome)])[0])

    def test_callout_workflow_must_pass_on_this_head(self):
        self.assertTrue(self.gates([self.callout_run()])[0])
        for runs in ([], [self.callout_run(conclusion="failure")], [self.callout_run(status="in_progress")],
                     [self.callout_run(head_sha="d" * 40)], [self.callout_run(path=".github/workflows/other.yml")],
                     [self.callout_run(), self.callout_run(id=6, conclusion="failure")]):
            with self.subTest(runs=runs):
                self.assertFalse(self.gates(runs)[0])

    def test_a_newer_run_for_a_pr_into_dev_neither_passes_nor_blocks(self):
        skipped = {6: self.callout_jobs("completed", "skipped")}
        # The release PR's own check failed. A writer's later PR from the release
        # branch into dev has the same head, branch and repository, a higher id
        # and a green run, but its check job was skipped: it does not pass.
        allowed, reason = self.gates([self.callout_run(conclusion="failure"), self.dev_pr_run()], jobs=skipped)
        self.assertFalse(allowed)
        self.assertEqual(reason, "the migrations-callout workflow has not passed on this head")
        # Its run alone, with nothing older, is no pass either.
        self.assertFalse(self.gates([self.dev_pr_run()], jobs=skipped)[0])
        # It does not block a release PR whose own check passed, even when its
        # self-test failed: a completed run with check skipped is passed over.
        self.assertTrue(self.gates([self.callout_run(), self.dev_pr_run()], jobs=skipped)[0])
        self.assertTrue(self.gates([self.callout_run(), self.dev_pr_run(conclusion="failure")], jobs=skipped)[0])
        # Until the dev run completes, it is not passed over: the gate waits.
        running = {6: self.callout_jobs("completed", "skipped")}
        self.assertFalse(self.gates([self.callout_run(), self.dev_pr_run(status="in_progress", conclusion=None)],
                                    jobs=running)[0])

    def test_the_check_job_must_succeed_not_only_the_run(self):
        # Main's old workflow had only the check job; its run shape still passes.
        self.assertTrue(self.gates([self.callout_run()], jobs={5: self.callout_jobs(self_test=False)})[0])
        for jobs in (self.callout_jobs("completed", "failure"), self.callout_jobs("completed", "cancelled"),
                     self.callout_jobs("completed", "neutral"), self.callout_jobs("in_progress", None),
                     self.callout_jobs(check_status=None)):
            with self.subTest(jobs=jobs):
                self.assertFalse(self.gates([self.callout_run()], jobs={5: jobs})[0])
        # A failed self-test fails the run, even with the check job green.
        self.assertFalse(self.gates([self.callout_run(conclusion="failure")],
                                    jobs={5: self.callout_jobs("completed", "success")})[0])

    def test_a_newer_run_that_is_not_finished_or_has_no_check_job_waits(self):
        # An older pass never counts while the newest run is undecided.
        for run, jobs in ((self.dev_pr_run(status="queued", conclusion=None), {"total_count": 0, "jobs": []}),
                          (self.dev_pr_run(), self.callout_jobs(check_status=None)),
                          (self.dev_pr_run(conclusion="startup_failure"), {"total_count": 0, "jobs": []}),
                          (self.dev_pr_run(), self.callout_jobs("in_progress", None))):
            with self.subTest(run=run, jobs=jobs):
                self.assertFalse(self.gates([self.callout_run(), run], jobs={6: jobs})[0])

    def test_malformed_job_lists_refuse(self):
        two_checks = self.callout_jobs("completed", "skipped")
        two_checks = {"total_count": 3, "jobs": two_checks["jobs"] + [dict(two_checks["jobs"][-1], id=13)]}
        short_page = dict(self.callout_jobs(), total_count=5)
        for jobs in (two_checks, short_page, {"jobs": self.callout_jobs()["jobs"]}, {"total_count": 0},
                     {"total_count": "2", "jobs": self.callout_jobs()["jobs"]}, [], None,
                     {"total_count": 1, "jobs": [None]}):
            with self.subTest(jobs=jobs):
                # Neither as the release PR's own run nor as a newer one to pass over.
                self.assertFalse(self.gates([self.callout_run()], jobs={5: jobs})[0])
                self.assertFalse(self.gates([self.callout_run(), self.dev_pr_run()], jobs={6: jobs})[0])

    def test_a_run_must_list_this_release_pr_into_main(self):
        other = {**self.RELEASE_PR_ENTRY, "number": 41}
        for entries in ([], None, "42", [None], [self.DEV_PR_ENTRY], [other],
                        [{**self.RELEASE_PR_ENTRY, "base": {"ref": "dev"}}],
                        [{**self.RELEASE_PR_ENTRY, "number": "42"}], [{**self.RELEASE_PR_ENTRY, "base": None}]):
            with self.subTest(entries=entries):
                self.assertFalse(self.gates([self.callout_run(pull_requests=entries)])[0])
                # A newer unlisted run is ignored, not counted.
                self.assertTrue(self.gates([self.callout_run(),
                                            self.callout_run(id=6, pull_requests=entries, conclusion="failure")])[0])
        self.assertTrue(self.gates([self.callout_run(pull_requests=[self.DEV_PR_ENTRY, self.RELEASE_PR_ENTRY])])[0])
        # The PR's own number must be known.
        self.assertFalse(self.gates([self.callout_run()], pr={**PR, "number": None})[0])

    def test_behind_main_waits_for_the_cooldown(self):
        self.assertFalse(self.gates([self.callout_run()], behind=1, main_date="2026-11-11T11:50:00Z")[0])
        allowed, reason = self.gates([self.callout_run()], behind=1, main_date="2026-11-11T10:00:00Z")
        self.assertTrue(allowed)
        self.assertIn("behind main", reason)
        self.assertNotIn("current with main", reason)
        self.assertFalse(self.gates([self.callout_run()], behind=1, main_date="not a date")[0])
        self.assertIn("current with main", self.gates([self.callout_run()])[1])

    def test_behind_main_stops_before_approval_when_main_requires_up_to_date_branches(self):
        strict = {**PR, "mergeable_state": "behind"}
        allowed, reason = self.gates([self.callout_run()], behind=1, pr=strict)
        self.assertFalse(allowed)
        self.assertIn("up-to-date", reason)
        # Other states, or a PR that is not behind, are not this case.
        for state in ("clean", "blocked", "unstable", None):
            with self.subTest(state=state):
                self.assertTrue(self.gates([self.callout_run()], behind=1, pr={**PR, "mergeable_state": state})[0])
        self.assertTrue(self.gates([self.callout_run()], behind=0, pr=strict)[0])


class WorkflowContractTests(unittest.TestCase):
    def test_existing_inline_guards_remain_identical_and_policy_follows_both(self):
        text = (Path(__file__).resolve().parents[1] / ".github/workflows/release-images.yml").read_text()
        guard = text.split("  guard:\n", 1)[1].split("  approve:\n", 1)[0]
        approve = text.split("  approve:\n", 1)[1].split("  build:\n", 1)[0]
        def inline_guard(block):
            return block.split("        run: |\n", 1)[1].split('          echo "components: $components"', 1)[0]
        self.assertEqual(inline_guard(guard), inline_guard(approve))
        for block in (guard, approve):
            self.assertIn("ref: main\n", block)
            self.assertIn("persist-credentials: false", block)
            self.assertIn("fetch-depth: 0", block)
            self.assertIn("python3 -I scripts/release_autopilot.py environment", block)
            self.assertLess(block.index('echo "components: $components"'), block.index("actions/checkout@v4"))
        self.assertIn("environment: ${{ needs.guard.outputs.environment }}", approve)
        self.assertIn('--expected-sha "$EXPECTED_SHA" --approved-environment "$APPROVED_ENVIRONMENT"', approve)
        # The count reaches the bump step through env, never by expansion inside the script.
        self.assertNotIn("Migrations: ${{", text)

    AUTOPILOT = Path(__file__).resolve().parents[1] / ".github/workflows/release-autopilot.yml"
    RELEASE = Path(__file__).resolve().parents[1] / ".github/workflows/release-images.yml"
    LINE = re.compile(r"Migrations: (?:[0-9]+|unknown) new files")

    def run_bump_step(self, migrations, baseline="v1.2.2"):
        """Run the real "Open bump PR" script against stub git and gh; nothing leaves the machine."""
        import yaml

        steps = yaml.safe_load(self.RELEASE.read_text())["jobs"]["bump-deployment"]["steps"]
        step = next(step for step in steps if step.get("name") == "Open bump PR")
        # Both come from the count step, which reads what deployment main runs;
        # the guard's count since the previous shipped release is not used here.
        self.assertEqual(step["env"]["MIGRATIONS"], "${{ steps.baseline.outputs.migrations }}")
        self.assertEqual(step["env"]["MIGRATION_BASELINE"], "${{ steps.baseline.outputs.pinned }}")
        self.assertNotIn("${{", step["run"])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "bin").mkdir()
            (root / "gh-config").mkdir()
            log = root / "calls.jsonl"
            stub = ("#!/usr/bin/env python3\nimport json, os, sys\n"
                    "with open(os.environ['STUB_LOG'], 'a') as log:\n"
                    "    log.write(json.dumps([os.path.basename(sys.argv[0])] + sys.argv[1:]) + '\\n')\n"
                    # Staged changes exist, and no bump PR is open yet.
                    "sys.exit(1 if sys.argv[1:2] == ['diff'] else 0)\n")
            for name in ("git", "gh"):
                (root / "bin" / name).write_text(stub)
                (root / "bin" / name).chmod(0o755)
            env = {"PATH": f"{root / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}", "HOME": temp,
                   "STUB_LOG": str(log), "GH_CONFIG_DIR": str(root / "gh-config"), "GH_TOKEN": "synthetic",
                   "VERSION": "v1.2.3", "MIGRATIONS": migrations, "MIGRATION_BASELINE": baseline, "SDK_VERSION": "",
                   "WORKER_SOURCE_SHA": SHA, "WORKER_DIGEST": "sha256:" + "0" * 64}
            subprocess.run(["bash", "-c", step["run"]], env=env, check=True, capture_output=True, text=True)
            calls = [json.loads(line) for line in log.read_text().splitlines()]
        commit = next(call for call in calls if call[:2] == ["git", "commit"])
        paragraphs = [commit[index + 1] for index, arg in enumerate(commit) if arg == "-m"]
        create = next(call for call in calls if call[:3] == ["gh", "pr", "create"])
        body = create[create.index("--body") + 1]
        return "\n\n".join(paragraphs), paragraphs, body

    def test_bump_commit_carries_exactly_one_migration_line_as_its_own_paragraph(self):
        for value, expected in (("0", "Migrations: 0 new files"), ("3", "Migrations: 3 new files"),
                                ("unknown", "Migrations: unknown new files")):
            with self.subTest(value=value):
                message, paragraphs, body = self.run_bump_step(value)
                self.assertEqual(paragraphs, ["chore(release): bump platform images to v1.2.3", expected,
                                              "Migration baseline: v1.2.2"])
                self.assertEqual([line for line in message.splitlines() if self.LINE.fullmatch(line)], [expected])
                # The PR body keeps the same line for readers.
                self.assertEqual([line for line in body.splitlines() if self.LINE.fullmatch(line)], [expected])

    def test_bump_commit_names_the_baseline_or_unknown(self):
        message, paragraphs, body = self.run_bump_step("0", "v1.58.0")
        self.assertEqual(paragraphs[2], "Migration baseline: v1.58.0")
        self.assertIn("Migration baseline: v1.58.0", body.splitlines())
        # Anything but a plain release tag is written as unknown, so the deployment
        # autopilot cannot match it against the version main runs.
        for value in ("", "unknown", "v1.58.0-rc.1", "1.58.0", "v1.58", "v1.58.0\nMigration baseline: v1.57.0",
                      "v1.58.0 "):
            with self.subTest(value=value):
                message, paragraphs, body = self.run_bump_step("0", value)
                self.assertEqual(paragraphs[2], "Migration baseline: unknown")
                for text in (message, body):
                    self.assertEqual([line for line in text.splitlines() if line.startswith("Migration baseline:")],
                                     ["Migration baseline: unknown"])

    def test_a_missing_or_malformed_count_is_written_as_unknown_never_as_zero(self):
        for value in ("", "-1", "0 ", "1.0", "none", "1\nMigrations: 0 new files", "0\n\nMigrations: 0 new files"):
            with self.subTest(value=value):
                message, paragraphs, body = self.run_bump_step(value)
                self.assertEqual(paragraphs[1], "Migrations: unknown new files")
                for text in (message, body):
                    self.assertNotIn("Migrations: 0 new files", text.splitlines())
                    self.assertEqual([line for line in text.splitlines() if self.LINE.fullmatch(line)],
                                     ["Migrations: unknown new files"])

    WORKFLOWS = Path(__file__).resolve().parents[1] / ".github/workflows"

    def test_the_autopilot_wakes_when_either_required_check_finishes(self):
        import yaml

        workflows = {path.name: yaml.safe_load(path.read_text()) for path in sorted(self.WORKFLOWS.glob("*.y*ml"))}
        names = {data.get("name"): data for data in workflows.values() if isinstance(data, dict)}
        # PyYAML reads the "on" key as True.
        wake = yaml.safe_load(self.AUTOPILOT.read_text())[True]["workflow_run"]["workflows"]
        self.assertEqual(wake, ["Backend CI", "E2E CI"])
        # Each name is a real workflow, and together they produce the required checks.
        jobs = set()
        for name in wake:
            self.assertIn(name, names)
            jobs |= {job.get("name") for job in names[name]["jobs"].values()}
        self.assertLessEqual(set(policy.MIN_REQUIRED_CHECKS), jobs)
        # A job name is not a workflow name, so it would never wake the autopilot.
        self.assertNotIn("E2E Tests Pass", names)
        self.assertEqual(workflows["e2e-ci.yml"]["name"], "E2E CI")

    def test_the_callout_workflow_runs_its_check_job_on_every_release_pr_event(self):
        import yaml

        # The release gate reads this file's runs by path and decides on its
        # job named check, which must run for release PRs into main only.
        workflow = yaml.safe_load((self.WORKFLOWS.parents[1] / policy.CALLOUT_WORKFLOW).read_text())
        trigger = workflow[True]["pull_request"]
        # A migration-free release PR often gets only its `opened` run.
        self.assertEqual(trigger["types"], ["opened", "edited", "synchronize", "reopened"])
        self.assertIn("main", trigger["branches"])
        jobs = workflow["jobs"]
        self.assertEqual([key for key, job in jobs.items() if job.get("name", key) == policy.CALLOUT_JOB],
                         [policy.CALLOUT_JOB])
        check = jobs[policy.CALLOUT_JOB]
        condition = " ".join(check["if"].split())
        for fragment in ("github.event_name == 'pull_request'", "github.base_ref == 'main'",
                         "startsWith(github.head_ref, 'release-please--')"):
            self.assertIn(fragment, condition)
        # Full history and tags, at the default ref: the PR merge preview whose
        # id the script reads from GITHUB_SHA.
        checkout = [step for step in check["steps"] if str(step.get("uses", "")).startswith("actions/checkout@")]
        self.assertEqual(len(checkout), 1)
        self.assertEqual(checkout[0]["with"].get("fetch-depth"), 0)
        self.assertNotIn("ref", checkout[0]["with"])
        self.assertIn("python3 -I .github/scripts/migrations_callout.py",
                      " ".join(step.get("run", "") for step in check["steps"]))

    def test_autopilot_is_default_branch_only_and_has_required_triggers(self):
        text = self.AUTOPILOT.read_text()
        for fragment in ('cron: "*/15 * * * *"', 'workflows: ["Backend CI", "E2E CI"]', "workflow_dispatch:",
                         "github.event.repository.default_branch", "persist-credentials: false", "checks: read",
                         "statuses: read", "actions: read", "permission-contents: write", "permission-pull-requests: write",
                         "python3 -I -m pip install --disable-pip-version-check PyYAML==6.0.3"):
            self.assertIn(fragment, text)
        self.assertNotIn("pull_request_target:", text)
        self.assertNotIn("github.event.workflow_run.head_sha", text)
        self.assertNotIn("download-artifact", text)

    def test_only_the_eligible_merge_job_holds_keys_and_it_approves_as_the_approver_app(self):
        import yaml

        jobs = yaml.safe_load(self.AUTOPILOT.read_text())["jobs"]
        self.assertEqual(set(jobs), {"check", "merge"})
        check, merge = jobs["check"], jobs["merge"]
        # The poll is read-only, names no environment and reads no secret.
        self.assertNotIn("environment", check)
        self.assertNotIn("write", check["permissions"].values())
        self.assertNotIn("secrets.", yaml.safe_dump(check))
        self.assertIn("github.event.repository.default_branch", check["if"])
        self.assertIn("vars.RELEASE_AUTOPILOT == 'on'", check["if"])
        self.assertEqual(merge["needs"], "check")
        self.assertEqual(merge["if"], "needs.check.outputs.eligible == 'true'")
        self.assertEqual(merge["environment"], "release-autopilot")
        steps = {step.get("id"): step for step in merge["steps"]}
        approver = steps["approver"]
        self.assertTrue(approver["uses"].startswith("actions/create-github-app-token@"))
        self.assertEqual(approver["with"], {
            "client-id": "${{ vars.AUTOPILOT_APPROVER_CLIENT_ID }}",
            "private-key": "${{ secrets.AUTOPILOT_APPROVER_PRIVATE_KEY }}",
            "permission-pull-requests": "write"})
        release_app = steps["app-token"]["with"]
        self.assertEqual((release_app["permission-contents"], release_app["permission-pull-requests"]),
                         ("write", "write"))
        run = next(step for step in merge["steps"] if "release_autopilot.py merge" in step.get("run", ""))
        self.assertEqual(run["env"]["APPROVER_TOKEN"], "${{ steps.approver.outputs.token }}")
        self.assertEqual(run["env"]["APPROVER_LOGIN"], "${{ steps.approver.outputs.app-slug }}[bot]")
        self.assertEqual(run["env"]["RELEASE_APP_TOKEN"], "${{ steps.app-token.outputs.token }}")
        self.assertEqual(merge["env"]["GH_TOKEN"], "${{ github.token }}")

    def test_autopilot_actions_are_pinned_to_commits_with_version_comments(self):
        lines = [line.strip() for line in self.AUTOPILOT.read_text().splitlines()
                 if re.match(r"\s*(- )?uses:", line)]
        self.assertEqual(len(lines), 6)
        for line in lines:
            with self.subTest(line=line):
                self.assertRegex(line, r"^(- )?uses: [A-Za-z0-9-]+/[A-Za-z0-9-]+@[0-9a-f]{40} # v[0-9]+\.[0-9]+\.[0-9]+$")

    def test_bump_job_actions_are_pinned_to_commits_with_version_comments(self):
        job = self.RELEASE.read_text().split("  bump-deployment:\n", 1)[1]
        lines = [line.strip() for line in job.splitlines() if re.match(r"\s*(- )?uses:", line)]
        # The job holds the release-bot token, persisted in the deployment checkout.
        self.assertEqual(len(lines), 8)
        for line in lines:
            with self.subTest(line=line):
                self.assertRegex(line, r"^(- )?uses: [A-Za-z0-9-]+/[A-Za-z0-9-]+@[0-9a-f]{40} # v[0-9]+\.[0-9]+\.[0-9]+$")

    def test_guard_exports_no_count_and_the_bump_reads_history_it_counts_over(self):
        import yaml

        workflow = yaml.safe_load(self.RELEASE.read_text())
        self.assertEqual(set(workflow["jobs"]["guard"]["outputs"]),
                         {"version", "channel", "components", "environment", "tag-sha"})
        self.assertNotIn("needs.guard.outputs.migration", self.RELEASE.read_text())
        steps = workflow["jobs"]["bump-deployment"]["steps"]
        platform = next(step for step in steps if step.get("with", {}).get("path") == "platform")
        self.assertEqual(platform["with"], {"path": "platform", "sparse-checkout": "scripts",
                                            "fetch-depth": 0, "filter": "blob:none"})
        # The deployment repository's test-release-digest-bump.py picks steps by
        # these names and reads COMMON_PATHS from the job env.
        self.assertEqual(len([step for step in steps if step.get("name", "").startswith("Bump platform tags")]), 1)
        self.assertEqual(len([step for step in steps if step.get("id") == "catalog-images"]), 1)

    CHECKS = Path(__file__).resolve().parents[1] / ".github/workflows/worker-release-checks.yml"
    RELEASE_TEST_MODULES = ("test_release_autopilot", "test_release_error_feed_worker",
                            "test_verify_platform_image_tags")
    # Files the release tests read besides their own modules; a change to any of them must run the tests.
    READ_INPUTS = (".github/workflows/release-images.yml", ".github/workflows/release-autopilot.yml",
                   ".github/workflows/sdk-sync.yml", ".github/workflows/worker-release-checks.yml",
                   ".github/workflows/migrations-callout-check.yml", "docs/release-autopilot.md")
    # Scripts the release tests run whose own tests are elsewhere, with those tests.
    RUN_ONLY = {"scripts/image_size_budget.py": "deploy/tests/test_image_size_budget.py"}

    @classmethod
    def unwatched_inputs(cls, workflow):
        paths = workflow[True]["pull_request"]["paths"]
        # PythonIsolationTests copies and runs each of these scripts.
        ran = [f"scripts/{name}" for name in PythonIsolationTests.SCRIPT_CALLS]
        return [path for path in dict.fromkeys([*cls.READ_INPUTS, *ran]) if path not in paths]

    @classmethod
    def expected_modules(cls, paths):
        """test_<name> for each watched script, except the scripts whose tests are elsewhere."""
        subjects = {path for path in paths if path.startswith("scripts/") and "/test_" not in path} - set(cls.RUN_ONLY)
        return {"test_" + Path(path).stem.replace("-", "_") for path in subjects}

    @staticmethod
    def unittest_modules(workflow):
        """The test modules the job names, or None when it does not name them one by one."""
        runs = [step for step in workflow["jobs"]["test"]["steps"] if "unittest" in step.get("run", "")]
        if len(runs) != 1 or runs[0].get("working-directory") != "scripts":
            return None
        argv = shlex.split(runs[0]["run"])
        modules = argv[4:]
        if argv[:4] != ["python3", "-m", "unittest", "-v"] or not modules:
            return None
        return modules if all(re.fullmatch(r"test_[a-z0-9_]+", name) for name in modules) else None

    def test_release_script_checks_run_exactly_the_tests_of_the_files_they_watch(self):
        import yaml

        workflow = yaml.safe_load(self.CHECKS.read_text())
        modules = self.unittest_modules(workflow)
        self.assertEqual(modules, list(self.RELEASE_TEST_MODULES))
        paths = set(workflow[True]["pull_request"]["paths"])
        scripts = Path(__file__).resolve().parent
        for name in modules:
            self.assertTrue((scripts / f"{name}.py").is_file(), name)
            self.assertIn(f"scripts/{name}.py", paths)
        # Every watched script has its test module in the list, and nothing else is run.
        self.assertEqual(self.expected_modules(paths), set(modules))
        self.assertEqual(self.unwatched_inputs(workflow), [])
        for path in self.READ_INPUTS:
            self.assertTrue((scripts.parent / path).is_file(), path)
        for script, test in self.RUN_ONLY.items():
            self.assertIn(script, paths)
            self.assertTrue((scripts.parent / test).is_file(), test)

    def test_a_discover_run_or_an_unrelated_module_is_not_the_release_set(self):
        import yaml

        workflow = yaml.safe_load(self.CHECKS.read_text())
        step = next(step for step in workflow["jobs"]["test"]["steps"] if "unittest" in step.get("run", ""))
        # The shape that picked up dev's ruff canary test on run 43154266035.
        step["run"] = "python3 -m unittest discover -s scripts -p 'test_*.py' -v"
        self.assertIsNone(self.unittest_modules(workflow))
        step["run"] = "python3 -m unittest -v " + " ".join((*self.RELEASE_TEST_MODULES, "test_check_python_lint_delta"))
        self.assertNotEqual(self.unittest_modules(workflow), list(self.RELEASE_TEST_MODULES))
        del step["working-directory"]
        self.assertIsNone(self.unittest_modules(workflow))
        # A doc-only edit that the tests read must still start the job.
        workflow[True]["pull_request"]["paths"].remove("docs/release-autopilot.md")
        self.assertEqual(self.unwatched_inputs(workflow), ["docs/release-autopilot.md"])

    def test_a_script_the_isolation_test_runs_must_start_the_job(self):
        import yaml

        workflow = yaml.safe_load(self.CHECKS.read_text())
        paths = workflow[True]["pull_request"]["paths"]
        # size-report's script: a change to it alone must run the planted-module test.
        paths.remove("scripts/image_size_budget.py")
        self.assertEqual(self.unwatched_inputs(workflow), ["scripts/image_size_budget.py"])
        # A script added to that test later is watched without a second list.
        with patch.dict(PythonIsolationTests.SCRIPT_CALLS, {"new_tool.py": []}):
            self.assertEqual(self.unwatched_inputs(workflow), ["scripts/image_size_budget.py", "scripts/new_tool.py"])
        # Only the named script is exempt from having a test module in the job.
        self.assertEqual(self.expected_modules([*paths, "scripts/image_size_budget.py"]), set(self.RELEASE_TEST_MODULES))
        self.assertEqual(self.expected_modules([*paths, "scripts/other_tool.py"]),
                         {*self.RELEASE_TEST_MODULES, "test_other_tool"})


class SdkSyncWorkflowTests(unittest.TestCase):
    """sdk-sync holds the release-bot key and runs sdkgen code from its futureagi-sdk checkout."""

    WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/sdk-sync.yml"

    def setUp(self):
        import yaml

        self.workflow = yaml.safe_load(self.WORKFLOW.read_text())
        self.steps = self.workflow["jobs"]["sync"]["steps"]

    def resolve(self, *, dispatched="", run_ref="", sdk_base="dev"):
        """Run the real "Resolve the release" script as GitHub does (bash -e)."""
        step = self.steps[0]
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "output"
            output.touch()
            env = {"PATH": os.environ.get("PATH", ""), "GITHUB_OUTPUT": str(output),
                   "DISPATCHED": dispatched, "RUN_REF": run_ref, "SDK_BASE": sdk_base}
            # check=False: the tests read the exit code of rejected values.
            result = subprocess.run(["bash", "-e", "-c", step["run"]], env=env, text=True, capture_output=True,
                                    check=False)
            return result.returncode, output.read_text().splitlines(), result.stdout + result.stderr

    def test_dev_is_the_only_base_and_it_is_checked_before_any_token_or_checkout(self):
        sdk_base = self.workflow[True]["workflow_dispatch"]["inputs"]["sdk-base"]
        self.assertEqual((sdk_base["type"], sdk_base["options"], sdk_base["default"]), ("choice", ["dev"], "dev"))
        first = self.steps[0]
        self.assertEqual((first.get("id"), first["env"]["SDK_BASE"]), ("release", "${{ inputs.sdk-base || 'dev' }}"))
        self.assertNotIn("uses", first)
        # Later steps read only the validated output, never the input.
        for step in self.steps[1:]:
            self.assertNotIn("inputs.sdk-base", repr(step))
        sdk = next(step for step in self.steps if step.get("with", {}).get("repository") == "future-agi/futureagi-sdk")
        self.assertEqual(sdk["with"]["ref"], "refs/heads/${{ steps.release.outputs.sdk-base }}")
        opener = next(step for step in self.steps if step.get("name") == "Open the pull request")
        self.assertEqual(opener["env"]["BASE"], "${{ steps.release.outputs.sdk-base }}")
        # A dispatch and a release-images completion both resolve with dev.
        for kwargs in ({"dispatched": "v1.58.0"}, {"run_ref": "v1.58.0"}):
            with self.subTest(**kwargs):
                code, outputs, _ = self.resolve(**kwargs)
                self.assertEqual((code, outputs), (0, ["version=v1.58.0", "sdk-base=dev"]))

    def test_any_other_base_fails_before_writing_an_output(self):
        # feat/sdk-docs-reference is the value of the one real dispatch (run 41805312254).
        for base in ("feat/sdk-docs-reference", "main", "refs/pull/50/head", "Dev", "dev ", " dev",
                     "dev\nmain", ""):
            with self.subTest(base=base):
                code, outputs, log = self.resolve(dispatched="v1.58.0", sdk_base=base)
                self.assertNotEqual(code, 0)
                self.assertEqual(outputs, [])
                self.assertIn("::error::sdk-base must be dev", log)

    def test_only_a_whole_release_tag_is_a_version(self):
        for version in ("v1.58.0\nsdk-base=feat/x", "v1.58.0\n", "x\nv1.58.0", "v1.58", "release-images"):
            with self.subTest(version=version):
                code, outputs, log = self.resolve(dispatched=version)
                self.assertEqual((code, outputs), (0, ["skip=true"]))
                self.assertNotIn("\nsdk-base=feat/x", log)
        self.assertEqual(self.resolve(run_ref="v2.0.0")[1], ["version=v2.0.0", "sdk-base=dev"])

    def step(self, name):
        return next(step for step in self.steps if step.get("name") == name)

    @staticmethod
    def evaluate(expression, context):
        """Evaluate a job `if:` the way GitHub does for this grammar: ==, &&, ||, startsWith, case-insensitive."""
        def value(name):
            node = context
            for key in name.split("."):
                node = node.get(key) if isinstance(node, dict) else None
            return node

        def fold(text):
            return text.lower() if isinstance(text, str) else text

        code = []
        for token in re.findall(r"'[^']*'|\|\||&&|==|[(),]|[A-Za-z_][A-Za-z0-9_.-]*|\S", expression):
            if token in ("||", "&&"):
                code.append({"||": " or ", "&&": " and "}[token])
            elif token == "==":
                code.append(" == ")
            elif token in "(),":
                code.append(token)
            elif token == "startsWith":
                code.append("starts")
            elif token.startswith("'"):
                code.append(repr(fold(token[1:-1])))
            elif re.fullmatch(r"(github|inputs)(\.[A-Za-z0-9_-]+)+", token):
                code.append(f"value({token!r})")
            else:
                raise AssertionError(f"unexpected token {token!r} in {expression!r}")
        return eval("".join(code), {"__builtins__": {}},  # noqa: S307 - the workflow's own expression
                    {"value": lambda name: fold(value(name)),
                     "starts": lambda text, prefix: isinstance(text, str) and text.startswith(prefix)})

    def test_only_this_repositorys_successful_release_tag_runs_start_a_sync(self):
        condition = self.workflow["jobs"]["sync"]["if"]

        def run(**fields):
            event = {"conclusion": "success", "head_branch": "v1.58.0", "event": "push",
                     "head_repository": {"full_name": "future-agi/future-agi"}, **fields}
            return {"github": {"event_name": "workflow_run", "repository": "future-agi/future-agi",
                               "event": {"workflow_run": event}}}

        # Real release-images runs: 41655444775 (v1.58.0, tag push) and
        # 39725016867 (v1.53.0, a dispatch from the tag after the push run failed).
        for context in (run(), run(head_branch="v1.53.0", event="workflow_dispatch"),
                        {"github": {"event_name": "workflow_dispatch", "repository": "future-agi/future-agi"}}):
            with self.subTest(context=context):
                self.assertIs(self.evaluate(condition, context), True)
        for context in (run(conclusion="failure"), run(head_branch="main"),
                        run(head_repository={"full_name": "someone/future-agi"}), run(head_repository=None)):
            with self.subTest(context=context):
                self.assertIs(self.evaluate(condition, context), False)

    def test_the_contract_is_checked_out_by_its_full_tag_ref_and_verified_before_any_token(self):
        names = [step.get("name") or step.get("uses") for step in self.steps]
        contract = self.step("Check out the contract at the release")
        self.assertEqual(contract["with"]["ref"], "refs/tags/${{ steps.release.outputs.version }}")
        verify = names.index("Verify the contract is the release tag")
        self.assertEqual(verify, names.index("Check out the contract at the release") + 1)
        token = next(index for index, step in enumerate(self.steps)
                     if "create-github-app-token" in step.get("uses", ""))
        self.assertLess(verify, token)
        sdk = names.index("Check out futureagi-sdk")
        self.assertEqual(names.index("Verify the SDK checkout is the base branch"), sdk + 1)
        # Every checkout names a full ref: a bare name lets a branch stand in for a tag.
        for step in self.steps:
            if "actions/checkout" in step.get("uses", ""):
                with self.subTest(step=step.get("name")):
                    self.assertRegex(step["with"]["ref"], r"^refs/(tags|heads)/\$\{\{ steps\.release\.outputs\.")

    def git_repository(self, root):
        def git(*args):
            return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
                                   "-c", "tag.gpgsign=false", "-c", "user.name=Synthetic",
                                   "-c", "user.email=synthetic@example.invalid", *args],
                                  cwd=root, check=True, capture_output=True, text=True).stdout.strip()
        root.mkdir(parents=True)
        git("init", "-q", "-b", "main")
        return git

    def run_step(self, name, cwd, **env):
        script = self.step(name)["run"]
        result = subprocess.run(["bash", "--noprofile", "--norc", "-e", "-c", script], cwd=cwd, text=True,
                                capture_output=True, env={"PATH": os.environ.get("PATH", ""), "HOME": str(cwd),
                                                          "GIT_CONFIG_NOSYSTEM": "1",
                                                          "GIT_CONFIG_GLOBAL": os.devnull, **env})
        return result.returncode, result.stdout + result.stderr

    def test_the_contract_check_passes_only_on_the_tags_commit(self):
        name = "Verify the contract is the release tag"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "contract"
            git = self.git_repository(root)
            (root / "swagger.json").write_text("{}\n")
            git("add", ".")
            git("commit", "-q", "-m", "release")
            release = git("rev-parse", "HEAD")
            git("tag", "v1.58.0")
            git("tag", "-a", "-m", "annotated", "v1.59.0")
            git("tag", "v1.60.0")
            # A writer's branch named like that release, holding another contract.
            git("checkout", "-q", "-b", "v1.60.0")
            (root / "swagger.json").write_text('{"planted": true}\n')
            git("commit", "-q", "-am", "planted")
            planted = git("rev-parse", "HEAD")
            git("tag", "v1.61.0", f"{release}^{{tree}}")
            # What actions/checkout does for ref: refs/tags/<version>: a detached tag checkout.
            for version in ("v1.58.0", "v1.59.0"):
                with self.subTest(version=version):
                    git("checkout", "-q", "--detach", f"refs/tags/{version}")
                    code, log = self.run_step(name, root, VERSION=version)
                    self.assertEqual(code, 0, log)
                    self.assertIn(f"contract from refs/tags/{version} at {release}", log)
            refusals = (
                # A bare ref resolved to a same-named branch: checkout -B <name> origin/<name>.
                ("v1.60.0", ["checkout", "-q", "-B", "v1.60.0", planted], "on a branch"),
                ("v1.58.0", ["checkout", "-q", "-B", "v1.58.0", release], "on a branch"),
                ("v1.58.0", ["checkout", "-q", "--detach", planted], "not the commit"),
                ("v1.62.0", ["checkout", "-q", "--detach", release], "object type: missing"),
                ("v1.61.0", ["checkout", "-q", "--detach", release], "object type: tree"),
            )
            for version, checkout, reason in refusals:
                with self.subTest(version=version, checkout=checkout):
                    git(*checkout)
                    code, log = self.run_step(name, root, VERSION=version)
                    self.assertNotEqual(code, 0)
                    self.assertIn(reason, log)
                    self.assertNotIn("contract from", log)

    def test_the_sdk_check_passes_only_on_the_fetched_base_branch(self):
        name = "Verify the SDK checkout is the base branch"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "futureagi-sdk"
            git = self.git_repository(root)
            git("commit", "-q", "--allow-empty", "-m", "dev")
            fetched = git("rev-parse", "HEAD")
            git("update-ref", "refs/remotes/origin/dev", fetched)
            git("commit", "-q", "--allow-empty", "-m", "other")
            other = git("rev-parse", "HEAD")
            git("tag", "dev", other)
            # What actions/checkout does for ref: refs/heads/dev.
            git("checkout", "-q", "-B", "dev", "refs/remotes/origin/dev")
            code, log = self.run_step(name, root, BASE="dev")
            self.assertEqual(code, 0, log)
            for checkout, reason in ((["checkout", "-q", "--detach", "refs/tags/dev"], "not on refs/heads/dev"),
                                     (["checkout", "-q", "--detach", fetched], "not on refs/heads/dev"),
                                     (["checkout", "-q", "-B", "dev", other], "not the fetched dev")):
                with self.subTest(checkout=checkout):
                    git(*checkout)
                    code, log = self.run_step(name, root, BASE="dev")
                    self.assertNotEqual(code, 0)
                    self.assertIn(reason, log)
            git("checkout", "-q", "-B", "dev", fetched)
            git("update-ref", "-d", "refs/remotes/origin/dev")
            code, log = self.run_step(name, root, BASE="dev")
            self.assertNotEqual(code, 0)
            self.assertIn("refs/remotes/origin/dev is missing", log)


# A module file that records its own import and stops the interpreter. Planted
# where a checkout could hold it: an isolated (-I) call must never import it.
PLANT = """import os
with open(os.environ["PLANT_LOG"], "a", encoding="utf-8") as log:
    log.write(__name__ + " " + __file__ + "\\n")
raise SystemExit(97)
"""
# uuid and typing as named in the review; the rest are modules the release
# scripts import, so a call without -I that reads a planted directory imports one.
PLANTED = ("uuid", "typing", "json", "argparse", "subprocess", "re", "yaml")


def plant(directory, pip_package=True):
    """Plant shadowing modules, and pip as a pip/ package or as pip.py (a package wins in one directory)."""
    directory.mkdir(parents=True, exist_ok=True)
    for name in PLANTED:
        (directory / f"{name}.py").write_text(PLANT)
    if pip_package:
        (directory / "pip").mkdir()
        for name in ("__init__.py", "__main__.py"):
            (directory / "pip" / name).write_text(PLANT)
    else:
        (directory / "pip.py").write_text(PLANT)


def python_shim(directory, call_log):
    """python3 and python on PATH: this interpreter, and a log of each call's first argument."""
    for name in ("python3", "python"):
        shim = directory / name
        # Only the first argument: later ones, such as a PR body, can span lines.
        shim.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$1\" >> {shlex.quote(str(call_log))}\n"
                        f"exec {shlex.quote(sys.executable)} \"$@\"\n")
        shim.chmod(0o755)


class PythonIsolationTests(unittest.TestCase):
    """Every Python call in the release workflows runs isolated (python -I).

    These workflows hold the release-bot key, the approver App key or a
    contents-write token. Without -I, `python -m` and `python -c` put the
    current directory on sys.path and `python script.py` puts the script's
    directory there, so a module file in the checkout (pip.py, json.py) would
    run first with those credentials.
    """

    ROOT = Path(__file__).resolve().parents[1]
    WORKFLOWS = (".github/workflows/release-images.yml", ".github/workflows/release-autopilot.yml",
                 ".github/workflows/sdk-sync.yml")
    # A call in command position: line start or after a shell operator, past
    # any VAR=value prefixes and the command wrappers named here, of python,
    # python3 or a versioned name such as python3.12.
    CALL = re.compile(r"(?:^|&&|\|\||[;|(`!]|\$\(|\b(?:then|do|else)\b)\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*"
                      r"(?:(?:timeout\s+\S+|env(?:\s+[A-Za-z_][A-Za-z0-9_]*=\S*)*|exec|sudo|time|nice|command|nohup)\s+)*"
                      r"(?P<python>python(?:[23](?:\.\d+)?)?)(?=\s|$)")
    # Every word that starts with "python" in a run line must be a call CALL
    # reads, or be on one of these lines, which name Python without running it.
    # Anything else, such as `xargs python3` or `/usr/bin/python3`, fails the
    # test, so a call the scan cannot read is never skipped silently.
    WORD = re.compile(r"\bpython\w*")
    NOT_CALLS = {
        # sdk-sync compares the generated SDK directories.
        ("sdk-sync.yml", "if git diff --quiet -- python typescript; then"),
    }
    # Every call, by workflow, job and what it runs. A new call must be added
    # here, and run below when it is this repository's script.
    INVENTORY = sorted([
        ("release-autopilot.yml", "check", "-m pip"), ("release-autopilot.yml", "check", "scripts/release_autopilot.py"),
        ("release-autopilot.yml", "merge", "-m pip"), ("release-autopilot.yml", "merge", "scripts/release_autopilot.py"),
        ("release-images.yml", "guard", "scripts/release_autopilot.py"),
        ("release-images.yml", "approve", "scripts/release_autopilot.py"),
        ("release-images.yml", "release-worker", "scripts/release-error-feed-worker.py"),
        ("release-images.yml", "size-report", "scripts/image_size_budget.py"),
        ("release-images.yml", "bump-deployment", "-m pip"),
        ("release-images.yml", "bump-deployment", "tests/test-release-digest-bump.py"),
        ("release-images.yml", "bump-deployment", "scripts/release_autopilot.py"),
        ("release-images.yml", "bump-deployment", "platform/scripts/verify-platform-image-tags.py"),
        ("sdk-sync.yml", "sync", "-m pip"), ("sdk-sync.yml", "sync", "sdkgen/sync_contract.py"),
        ("sdk-sync.yml", "sync", "sdkgen/generate.py"),
    ])
    # Scripts of other repositories, run from their own checkouts: the
    # deployment repository's test (deployment main) and futureagi-sdk's
    # sdkgen (dev). They import only the standard library, PyYAML and, for
    # generate.py, its own package, which it adds to sys.path itself.
    OTHER_REPOSITORIES = {"tests/test-release-digest-bump.py", "sdkgen/sync_contract.py", "sdkgen/generate.py"}

    @staticmethod
    def lines(script):
        """A shell script's lines, continuations joined, whole-line comments left out."""
        return [line for line in script.replace("\\\n", " ").splitlines() if not line.lstrip().startswith("#")]

    @classmethod
    def calls(cls, script):
        """(interpreter, the arguments after it) for each Python call in a shell script."""
        found = []
        for line in cls.lines(script):
            for match in cls.CALL.finditer(line):
                found.append((match["python"], line[match.end("python"):].split()))
        return found

    @classmethod
    def unread(cls, script):
        """Lines that name Python somewhere CALL does not read as a call."""
        found = []
        for line in cls.lines(script):
            calls = {match.start("python") for match in cls.CALL.finditer(line)}
            if any(word.start() not in calls for word in cls.WORD.finditer(line)):
                found.append(line.strip())
        return found

    @staticmethod
    def target(arguments):
        rest = arguments[1:] if arguments[:1] == ["-I"] else arguments
        return " ".join(rest[:2]) if rest[:1] in (["-m"], ["-c"]) else rest[0]

    def workflows(self):
        import yaml

        return {Path(path).name: yaml.safe_load((self.ROOT / path).read_text()) for path in self.WORKFLOWS}

    @staticmethod
    def runs(workflows):
        """(workflow, job, shell, run text) for each run step."""
        for name, workflow in workflows.items():
            default = workflow.get("defaults", {}).get("run", {}).get("shell", "bash")
            for job_name, job in workflow["jobs"].items():
                shell = job.get("defaults", {}).get("run", {}).get("shell", default)
                for step in job.get("steps", []):
                    if "run" in step:
                        yield name, job_name, step.get("shell", shell), step["run"]

    @classmethod
    def problems(cls, workflows):
        """Calls without -I, lines that name Python outside a call CALL reads, and
        steps not run by bash (a `shell: python` step names no interpreter)."""
        found = []
        for name, job, shell, run in cls.runs(workflows):
            if shell != "bash":
                found.append((name, job, f"shell: {shell}"))
            found += [(name, job, " ".join([python, *arguments]))
                      for python, arguments in cls.calls(run) if arguments[:1] != ["-I"]]
            found += [(name, job, line) for line in cls.unread(run) if (name, line) not in cls.NOT_CALLS]
        return found

    def test_every_python_call_in_the_release_workflows_runs_isolated(self):
        workflows = self.workflows()
        calls = [(name, job, arguments) for name, job, _, run in self.runs(workflows)
                 for _, arguments in self.calls(run)]
        self.assertEqual(sorted((name, job, self.target(arguments)) for name, job, arguments in calls),
                         self.INVENTORY)
        self.assertEqual(self.problems(workflows), [])
        # Each allowed line is still there, so the list cannot hide a later line.
        self.assertEqual({(name, line) for name, _, _, run in self.runs(workflows) for line in self.unread(run)},
                         self.NOT_CALLS)
        # This repository's scripts are all run by the planted-checkout test below.
        ours = {target for _, _, target in self.INVENTORY if not target.startswith("-")} - self.OTHER_REPOSITORIES
        self.assertEqual({Path(target).name for target in ours}, set(self.SCRIPT_CALLS))

    def test_the_scan_finds_calls_without_isolation_and_ignores_other_words(self):
        script = "\n".join([
            "python3 -m pip install x",
            'count=$(cd platform && python3 scripts/a.py --to "$V") || count=unknown',
            "python -c 'import yaml' 2>/dev/null || python3 -I -m pip install y",
            "if true; then python3 scripts/b.py \\\n  --flag; fi",
            "FOO=1 python3 scripts/c.py",
            "# python3 scripts/commented.py",
            "if git diff --quiet -- python typescript; then",
            "python3 -I scripts/d.py",
        ])
        self.assertEqual([(python, self.target(arguments), arguments[:1] == ["-I"])
                          for python, arguments in self.calls(script)],
                         [("python3", "-m pip", False), ("python3", "scripts/a.py", False),
                          ("python", "-c 'import", False), ("python3", "-m pip", True),
                          ("python3", "scripts/b.py", False), ("python3", "scripts/c.py", False),
                          ("python3", "scripts/d.py", True)])
        self.assertEqual(self.calls("git diff --quiet -- python typescript"), [])

    def test_the_scan_reads_wrapped_calls_and_reports_words_it_cannot_read(self):
        # A word that names Python outside a call is reported; NOT_CALLS lists the real one.
        self.assertEqual(self.unread("python3 -I x.py\nif git diff --quiet -- python typescript; then"),
                         ["if git diff --quiet -- python typescript; then"])
        # Wrapped and versioned calls are read as calls and held to -I.
        wrapped = "\n".join(["timeout 60 python3 x.py", "env FOO=1 python3 x.py", "exec python3 x.py",
                             "sudo python3 -m pip install z", "python3.12 x.py", "nohup python3 -I x.py"])
        self.assertEqual([(python, self.target(arguments), arguments[:1] == ["-I"])
                          for python, arguments in self.calls(wrapped)],
                         [("python3", "x.py", False), ("python3", "x.py", False), ("python3", "x.py", False),
                          ("python3", "-m pip", False), ("python3.12", "x.py", False), ("python3", "x.py", True)])
        self.assertEqual(self.unread(wrapped), [])
        # Forms CALL does not read are reported, not skipped.
        other = ["xargs python3 x.py", "/usr/bin/python3 -I x.py", "nice -n 5 python3 x.py",
                 "uv run python x.py", "echo ok && python3-config --prefix"]
        self.assertEqual(self.unread("\n".join(other)), other)

    def test_the_real_workflows_fail_when_a_call_is_unisolated_unread_or_not_bash(self):
        real = self.workflows()
        step = next(step for step in real["sdk-sync.yml"]["jobs"]["sync"]["steps"]
                    if "python -I sdkgen/generate.py" in step.get("run", ""))
        # The interpreter and its arguments name a call without -I; an unread line is named whole.
        for call, problem in [("timeout 60 python sdkgen/generate.py", "python sdkgen/generate.py"),
                              ("/usr/bin/python3 sdkgen/generate.py", "/usr/bin/python3 sdkgen/generate.py"),
                              ("python3.12 -I sdkgen/generate.py", None)]:
            with self.subTest(call=call):
                workflows = deepcopy(real)
                changed = next(item for item in workflows["sdk-sync.yml"]["jobs"]["sync"]["steps"]
                               if item.get("run") == step["run"])
                changed["run"] = step["run"].replace("python -I sdkgen/generate.py", call)
                self.assertNotEqual(changed["run"], step["run"])
                self.assertEqual(self.problems(workflows), [("sdk-sync.yml", "sync", problem)] if problem else [])
        workflows = deepcopy(real)
        workflows["release-autopilot.yml"]["jobs"]["check"]["steps"].append({"shell": "python", "run": "print(1)"})
        self.assertEqual(self.problems(workflows), [("release-autopilot.yml", "check", "shell: python")])
        workflows = deepcopy(real)
        workflows["sdk-sync.yml"]["jobs"]["sync"]["defaults"] = {"run": {"shell": "python {0}"}}
        self.assertIn(("sdk-sync.yml", "sync", "shell: python {0}"), self.problems(workflows))

    # Each of this repository's scripts, called as its workflow calls it, with
    # arguments that stay offline. {I} is "-I " or nothing.
    # (working directory, command, environment, exit code, expected text)
    SCRIPT_CALLS = {
        "release_autopilot.py": [
            ("platform", "python3 {I}scripts/release_autopilot.py check", {}, 0, "eligible=false"),
            ("platform", "python3 {I}scripts/release_autopilot.py merge", {}, 0, "eligible=false"),
            # The first release has no baseline, so no GitHub read happens.
            ("platform", 'python3 {I}scripts/release_autopilot.py environment --version "$VERSION"',
             {"VERSION": "v1.0.0"}, 0, "environment=Production"),
            (".", 'count=$(cd platform && python3 {I}scripts/release_autopilot.py migrations-since '
                  '--from v1.0.0 --to "$VERSION") && echo "count=$count"', {}, 0, "count=1"),
        ],
        "release-error-feed-worker.py": [
            # It validates the version before any GitHub call.
            ("platform", "python3 {I}scripts/release-error-feed-worker.py", {"VERSION": "not-a-release"}, 1,
             "Expected a stable platform version"),
        ],
        "verify-platform-image-tags.py": [
            (".", 'python3 {I}platform/scripts/verify-platform-image-tags.py "$VERSION" eu.yaml', {}, 0, ""),
        ],
        # Its report reads registries over the network; --help still imports everything.
        "image_size_budget.py": [
            ("platform", "python3 {I}scripts/image_size_budget.py report --help", {}, 0, "--tag"),
        ],
    }

    def checkout(self, root, pip_package=True):
        """root is the job's working directory, root/platform a checkout of this repository."""
        platform = root / "platform"
        (platform / "scripts").mkdir(parents=True)
        for name in self.SCRIPT_CALLS:
            shutil.copy2(self.ROOT / "scripts" / name, platform / "scripts" / name)

        def git(*args):
            subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
                            "-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid", *args],
                           cwd=platform, check=True, capture_output=True, text=True)

        git("init", "-q", "-b", "main")
        git("add", ".")
        git("commit", "-q", "-m", "scripts")
        git("tag", "v1.0.0")
        (platform / "futureagi/app/migrations").mkdir(parents=True)
        (platform / "futureagi/app/migrations/0002_new.py").write_text("synthetic\n")
        git("add", ".")
        git("commit", "-q", "-m", "a migration")
        git("tag", "v1.1.0")
        git("update-ref", "refs/remotes/origin/main", "HEAD")
        (root / "eu.yaml").write_text(yaml_dump({"backend": {"image": {
            "repository": "futureagi/future-agi-ee", "tag": "v1.1.0"}}}))
        # The job's working directory, the checkout's top level and the
        # script's own directory.
        for directory in (root, platform, platform / "scripts"):
            plant(directory, pip_package)

    def run_call(self, root, cwd, command, overrides, isolated):
        tools, plants, calls = root / "tools", root / "planted.log", root / "calls.log"
        tools.mkdir(exist_ok=True)
        python_shim(tools, calls)
        for log in (plants, calls):
            log.unlink(missing_ok=True)
        output, summary = root / "output", root / "summary"
        for path in (output, summary):
            path.write_text("")
        env = {"PATH": os.pathsep.join([str(tools), os.environ.get("PATH", "")]), "HOME": str(root),
               "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "PLANT_LOG": str(plants),
               "GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(summary), "VERSION": "v1.1.0",
               "GITHUB_EVENT_NAME": "workflow_dispatch", "RELEASE_REQUEST_ID": "1-1", "PIP_NO_INDEX": "1",
               **overrides}
        result = subprocess.run(["bash", "--noprofile", "--norc", "-e", "-c",
                                 command.format(I="-I " if isolated else "")],
                                cwd=root / cwd, env=env, text=True, capture_output=True, timeout=120)
        planted = plants.read_text() if plants.exists() else ""
        text = result.stdout + result.stderr + output.read_text() + summary.read_text()
        return result.returncode, text, planted

    def test_planted_modules_are_never_imported_and_every_script_still_runs(self):
        cases = [case for calls in self.SCRIPT_CALLS.values() for case in calls]
        # pip with the job's own arguments; PIP_NO_INDEX keeps it offline. The
        # release-autopilot form; bump-deployment's and sdk-sync's differ only in flags.
        pip = ("platform", "python3 {I}-m pip install --disable-pip-version-check PyYAML==6.0.3", {}, 0, "PyYAML")
        has_pip = importlib.util.find_spec("pip") is not None
        for pip_package in (True, False):
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                self.checkout(root, pip_package)
                for cwd, command, overrides, code, expected in [*([pip] if has_pip else []), *cases]:
                    with self.subTest(command=command, pip_package=pip_package):
                        returncode, text, planted = self.run_call(root, cwd, command, overrides, isolated=True)
                        self.assertEqual(planted, "")
                        self.assertEqual(returncode, code, text)
                        self.assertIn(expected, text)
                        # The negative control: the same call without -I imports a planted module.
                        returncode, text, planted = self.run_call(root, cwd, command, overrides, isolated=False)
                        self.assertNotEqual(planted, "", text)
                        self.assertEqual(returncode, 97, text)
        if not has_pip:
            self.skipTest("pip is not installed for this interpreter; the pip call was not run")


def yaml_dump(document):
    import yaml

    return yaml.safe_dump(document, sort_keys=False)


def yq_stub(directory):
    """A stand-in for mikefarah yq v4 that accepts only the expressions the bump job uses."""
    script = directory / "yq_stub.py"
    script.write_text(r"""
import os, re, sys
import yaml

PATH = r"(?:\.[A-Za-z_][A-Za-z0-9_]*)+"
args = sys.argv[1:]
in_place = args[:1] == ["-i"]
expression, name = args[1:] if in_place else args
with open(name, encoding="utf-8") as stream:
    document = yaml.safe_load(stream)
if not in_place:
    if not re.fullmatch(PATH, expression):
        sys.exit(98)
    node = document
    for key in expression[1:].split("."):
        node = node.get(key) if isinstance(node, dict) else None
    if node is None:
        print("null")
    elif isinstance(node, bool):
        print(str(node).lower())
    elif isinstance(node, (dict, list)):
        print(yaml.safe_dump(node, sort_keys=False), end="")
    else:
        print(node)
    sys.exit(0)
for assignment in expression.split(" | "):
    match = re.fullmatch(r'(?P<path>' + PATH + r') = (?:"(?P<text>[^"\\]*)"|strenv\((?P<env>[A-Z_]+)\))', assignment)
    if not match:
        sys.exit(98)
    *parents, last = match["path"][1:].split(".")
    node = document
    for key in parents:
        node = node.setdefault(key, {})
    node[last] = match["text"] if match["env"] is None else os.environ[match["env"]]
with open(name, "w", encoding="utf-8") as stream:
    yaml.safe_dump(document, stream, sort_keys=False)
""")
    return script


class BumpJobTests(unittest.TestCase):
    """Run the real bump-deployment steps, in order, on producer-shaped synthetic values.

    The count, bump and verify steps run with real git (a blob-less clone of a
    synthetic product history whose origin is then deleted, so no file contents
    can be fetched) and with real yq when it is on PATH, else a stub. Only the
    final step's git and gh are stubs; nothing leaves the machine.
    """

    RELEASE = WorkflowContractTests.RELEASE
    VERSION = "v1.2.3"
    BACKEND, COLLECTOR, WORKER, OLD = ("sha256:" + c * 64 for c in "abcd")
    MIRROR = "us-east5-docker.pkg.dev/futureagiprimary/dockerhub-mirror/"
    STEPS = ("Count migrations since the release deployment main runs", "Bump platform tags and digests",
             "Verify no platform image was left behind", "Open bump PR")

    @classmethod
    def setUpClass(cls):
        import yaml

        cls.job = yaml.safe_load(cls.RELEASE.read_text())["jobs"]["bump-deployment"]
        cls.paths = cls.job["env"]["COMMON_PATHS"].split()
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        origin = root / "origin"
        scripts = Path(__file__).resolve().parent

        def git(*args, cwd=origin):
            subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
                            "-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid", *args],
                           cwd=cwd, check=True, capture_output=True, text=True)

        def commit(path, tag):
            (origin / path).parent.mkdir(parents=True, exist_ok=True)
            # Distinct contents, so no counted file shares a blob with a checked-out one.
            (origin / path).write_text(f"synthetic {path}\n")
            git("add", ".")
            git("commit", "-m", "synthetic history")
            git("tag", tag)

        origin.mkdir()
        git("init", "-b", "main")
        for name in ("release_autopilot.py", "verify-platform-image-tags.py"):
            (origin / "scripts").mkdir(exist_ok=True)
            (origin / "scripts" / name).write_text((scripts / name).read_text())
        commit("futureagi/app/migrations/0001_initial.py", "v1.2.1")
        # A release on a side branch is not on the bumped release's history.
        git("checkout", "-b", "side")
        commit("futureagi/app/migrations/0001_side.py", "v1.2.0")
        git("checkout", "main")
        commit("futureagi/app/migrations/0002_new.py", "v1.2.2")
        commit("README.txt", cls.VERSION)
        git("config", "uploadpack.allowFilter", "true")
        # The job's checkout: all history and tags, no file contents, sparse scripts/.
        cls.platform = root / "work-template" / "platform"
        git("clone", "--quiet", "--filter=blob:none", "--no-checkout", f"file://{origin}", str(cls.platform), cwd=root)
        git("sparse-checkout", "set", "scripts", cwd=cls.platform)
        git("checkout", "--quiet", cls.VERSION, cwd=cls.platform)
        shutil.rmtree(origin)
        # No migration file's contents were fetched, and none can be now.
        objects = subprocess.run(["git", "rev-list", "--objects", "--missing=print", "--all"], cwd=cls.platform,
                                 check=True, capture_output=True, text=True).stdout.splitlines()
        cls.missing = sum(line.startswith("?") for line in objects)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def values(self, tags, region):
        """Only the 12 path-list tags plus the three digests and the repositories the job checks."""
        document = {}
        for path, tag in zip(self.paths, tags):
            node = document
            *parents, last = path[1:].split(".")
            for key in parents:
                node = node.setdefault(key, {})
            node[last] = tag
        mirror = self.MIRROR if region == "us" else ""
        document["backend"] = {"image": {"repository": mirror + "futureagi/future-agi-ee", "digest": self.OLD}}
        document["fiCollector"] = {"image": {"repository": mirror + "futureagi/fi-collector", "digest": self.OLD}}
        document["errorFeed"]["image"].update({"repository": "futureagi/omega-error-feed-worker", "digest": self.OLD})
        return document

    def run_job(self, eu_tags, us_tags, yq, steps=STEPS):
        """The steps in workflow order; returns step outputs, logs, commit, PR body and values."""
        import yaml

        selected, steps = steps, {step.get("name"): step for step in self.job["steps"]}
        order = [step.get("name") for step in self.job["steps"]]
        self.assertEqual([name for name in order if name in self.STEPS], list(self.STEPS))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            work = root / "work"
            shutil.copytree(self.platform.parent, work, symlinks=True)
            for region, tags in (("eu", eu_tags), ("us", us_tags)):
                path = work / region / "gcp/deployment/values.yaml"
                path.parent.mkdir(parents=True)
                path.write_text(yaml.safe_dump(self.values(tags, region), sort_keys=False))
            # Modules that shadow the standard library, pip and PyYAML, in the
            # deployment checkout (the steps' working directory), at the top of
            # this repository's checkout and beside its scripts.
            for directory in (work, work / "platform", work / "platform" / "scripts"):
                plant(directory)
            planted, python_calls = root / "planted.log", root / "python-calls.log"
            tools, stubs = root / "tools", root / "stubs"
            tools.mkdir()
            stubs.mkdir()
            python_shim(tools, python_calls)
            if yq is None:
                (tools / "yq").write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} "
                                          f"{shlex.quote(str(yq_stub(root)))} \"$@\"\n")
            else:
                (tools / "yq").symlink_to(yq)
            log = root / "calls.jsonl"
            stub = ("#!/usr/bin/env python3\nimport json, os, sys\n"
                    "with open(os.environ['STUB_LOG'], 'a') as log:\n"
                    "    log.write(json.dumps([os.path.basename(sys.argv[0])] + sys.argv[1:]) + '\\n')\n"
                    "sys.exit(1 if sys.argv[1:2] == ['diff'] else 0)\n")
            for name in ("git", "gh"):
                (stubs / name).write_text(stub)
            for path in (*tools.iterdir(), *stubs.iterdir()):
                if not path.is_symlink():
                    path.chmod(0o755)
            known = {"${{ needs.guard.outputs.version }}": self.VERSION,
                     "${{ needs.resolve-sdk.outputs.sdk_version }}": "",
                     "${{ needs.resolve-sdk.outputs.sdk_wheel_sha256 }}": "",
                     "${{ needs.release-worker.outputs.digest }}": self.WORKER,
                     "${{ needs.release-worker.outputs.source_sha }}": SHA,
                     "${{ steps.catalog-images.outputs.backend_digest }}": self.BACKEND,
                     "${{ steps.catalog-images.outputs.collector_digest }}": self.COLLECTOR,
                     "${{ steps.app-token.outputs.token }}": "synthetic-token"}

            def resolve(mapping):
                env = {}
                for key, value in (mapping or {}).items():
                    value = str(value)
                    for expression in re.findall(r"\$\{\{.*?\}\}", value):
                        self.assertIn(expression, known, f"{key} uses an expression this test does not know")
                        value = value.replace(expression, known[expression])
                    env[key] = value
                return env

            # A step inherits the runner's environment; user Git config is ignored.
            base = {**os.environ, "HOME": temp, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                    "GH_CONFIG_DIR": str(root), "STUB_LOG": str(log), "PLANT_LOG": str(planted),
                    **resolve(self.job["env"])}
            outputs, logs = {}, {}
            for name in selected:
                step = steps[name]
                self.assertNotIn("${{", step["run"])
                output = root / f"output-{len(logs)}"
                output.touch()
                path = [tools, stubs] if name == "Open bump PR" else [tools]
                env = {**base, **resolve(step.get("env")), "GITHUB_OUTPUT": str(output),
                       "PATH": os.pathsep.join([*map(str, path), os.environ.get("PATH", "")])}
                script = root / "step.sh"
                script.write_text(step["run"])
                # GitHub's own invocations: shell: bash, or the default bash -e.
                shell = (["bash", "--noprofile", "--norc", "-eo", "pipefail"] if step.get("shell") == "bash"
                         else ["bash", "-e"])
                result = subprocess.run([*shell, str(script)], cwd=work, env=env, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, f"{name}: {result.stdout}{result.stderr}")
                logs[name] = result.stdout + result.stderr
                values = dict(line.split("=", 1) for line in output.read_text().splitlines())
                if step.get("id"):
                    outputs[step["id"]] = values
                    known.update({f"${{{{ steps.{step['id']}.outputs.{key} }}}}": value
                                  for key, value in values.items()})
            calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            files = {region: yaml.safe_load((work / region / "gcp/deployment/values.yaml").read_text())
                     for region in ("eu", "us")}
            # Every Python call the steps made ran isolated, and none imported a
            # planted module. The git and gh stubs run through the shim too.
            self.assertFalse(planted.exists(), planted.read_text() if planted.exists() else "")
            made = [line for line in python_calls.read_text().splitlines()
                    if not line.startswith(str(stubs))] if python_calls.exists() else []
            if "Count migrations since the release deployment main runs" in selected:
                self.assertTrue(made)
            self.assertEqual([line for line in made if line != "-I"], [])
        result = {"outputs": outputs, "logs": logs, "values": files}
        if calls:
            commit = next(call for call in calls if call[:2] == ["git", "commit"])
            create = next(call for call in calls if call[:3] == ["gh", "pr", "create"])
            result["paragraphs"] = [commit[index + 1] for index, arg in enumerate(commit) if arg == "-m"]
            result["body"] = create[create.index("--body") + 1]
        return result

    def yq_variants(self):
        """The stub always; real yq too when it is on PATH (GitHub's Ubuntu runners have it)."""
        real = shutil.which("yq")
        return [None] + ([real] if real else [])

    def run_each(self, eu_tags, us_tags=None, steps=STEPS):
        results = [self.run_job(eu_tags, eu_tags if us_tags is None else us_tags, yq, steps)
                   for yq in self.yq_variants()]
        for other in results[1:]:
            # Real yq and the stub agree on what the job writes.
            for key in ("outputs", "paragraphs", "body", "values"):
                self.assertEqual(other.get(key), results[0].get(key), key)
        return results[0]

    def assert_lines(self, result, migrations, baseline):
        self.assertEqual(result["paragraphs"], [f"chore(release): bump platform images to {self.VERSION}",
                                                f"Migrations: {migrations} new files",
                                                f"Migration baseline: {baseline}"])
        lines = result["body"].splitlines()
        self.assertEqual([line for line in lines if line.startswith("Migration")],
                         [f"Migrations: {migrations} new files", f"Migration baseline: {baseline}"])

    def test_the_fixture_is_producer_shaped(self):
        # The three synthetic migration files exist only as names in trees.
        self.assertEqual(self.missing, 3)
        self.assertEqual(len(self.paths), 12)
        self.assertNotIn(".backend.image.tag", self.paths)
        self.assertNotIn(".fiCollector.image.tag", self.paths)
        self.assertIn(".errorFeed.image.tag", self.paths)

    def test_a_skipped_release_is_counted_from_the_version_main_runs(self):
        # Main runs v1.2.1; v1.2.2 (never deployed) added a migration and v1.2.3
        # added none. The guard's count since v1.2.2 would be 0.
        result = self.run_each(["v1.2.1"] * 12)
        self.assertEqual(result["outputs"]["baseline"], {"pinned": "v1.2.1", "migrations": "1"})
        self.assert_lines(result, "1", "v1.2.1")

    def test_a_migration_free_release_from_the_version_main_runs_is_zero(self):
        result = self.run_each(["v1.2.2"] * 12)
        self.assertEqual(result["outputs"]["baseline"], {"pinned": "v1.2.2", "migrations": "0"})
        self.assert_lines(result, "0", "v1.2.2")
        # The bump wrote all 12 tags and the 3 digests, kept the regional
        # repositories, and added no backend tag.
        for region, values in result["values"].items():
            with self.subTest(region=region):
                expected = self.values([self.VERSION] * 12, region)
                expected["backend"]["image"]["digest"] = self.BACKEND
                expected["fiCollector"]["image"]["digest"] = self.COLLECTOR
                expected["errorFeed"]["image"]["digest"] = self.WORKER
                self.assertEqual(values, expected)
                self.assertNotIn("tag", values["backend"]["image"])

    def test_tags_that_disagree_or_are_not_a_release_give_unknown_and_are_not_printed(self):
        mixed = ["v1.2.2"] * 12
        mixed[self.paths.index(".embedding.image.tag")] = "v1.1.7"
        # One hand-set tag in one region: the whole job, down to the commit.
        result = self.run_each(["v1.2.2"] * 12, mixed)
        self.assertEqual(result["outputs"]["baseline"], {"pinned": "unknown", "migrations": "unknown"})
        self.assert_lines(result, "unknown", "unknown")
        logs = [result["logs"][self.STEPS[0]]]
        # The count step alone for tags that agree but are not a plain release.
        for tag in ("latest", "v1.2.2-hotfix", "v01.2.2", "1.2.2"):
            with self.subTest(tag=tag):
                result = self.run_each([tag] * 12, steps=self.STEPS[:1])
                self.assertEqual(result["outputs"]["baseline"], {"pinned": "unknown", "migrations": "unknown"})
                logs.append(result["logs"][self.STEPS[0]])
        for log in logs:
            for value in ("v1.1.7", "latest", "hotfix", "v01.2.2", "1.2.2", self.OLD, "omega-error-feed-worker"):
                self.assertNotIn(value, log)

    def test_a_pinned_release_not_on_this_history_or_not_in_this_repository_counts_unknown(self):
        # v1.2.0 is on a side branch: the whole job, down to the commit. The
        # baseline still names what main runs; only the count is unknown.
        result = self.run_each(["v1.2.0"] * 12)
        self.assertEqual(result["outputs"]["baseline"], {"pinned": "v1.2.0", "migrations": "unknown"})
        self.assert_lines(result, "unknown", "v1.2.0")
        # Missing here, equal to the release, and newer than it: the count step alone.
        for pinned in ("v1.1.9", self.VERSION, "v1.2.4"):
            with self.subTest(pinned=pinned):
                result = self.run_each([pinned] * 12, steps=self.STEPS[:1])
                self.assertEqual(result["outputs"]["baseline"], {"pinned": pinned, "migrations": "unknown"})

    def test_the_count_step_runs_before_the_bump_rewrites_the_tags(self):
        order = [step.get("name") for step in self.job["steps"]]
        self.assertLess(order.index(self.STEPS[0]), order.index(self.STEPS[1]))
        self.assertEqual(self.job["steps"][order.index(self.STEPS[0])].get("id"), "baseline")


if __name__ == "__main__":
    unittest.main()
