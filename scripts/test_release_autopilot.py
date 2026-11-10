"""Synthetic policy, API-adapter and Git-history tests; no real GitHub requests."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import release_autopilot as policy

NOW = datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc)  # Tuesday
SHA = "a" * 40
BASE = "b" * 40
CONFIG = {"RELEASE_AUTOPILOT": "on"}
PR = {
    "number": 42, "state": "open", "draft": False, "merged": False,
    "user": {"login": policy.AUTHOR, "type": "Bot"},
    "head": {"ref": policy.BRANCH, "sha": SHA, "repo": {"full_name": policy.REPOSITORY}},
    "base": {"ref": "main", "sha": BASE, "repo": {"full_name": policy.REPOSITORY}},
    "title": "chore(main): release 1.2.3", "labels": [], "changed_files": 1,
    "updated_at": "2026-11-10T09:00:00Z", "mergeable": True,
}
FILES = [{"filename": "CHANGELOG.md", "status": "modified"}]
CHECK = {"id": 1, "name": "Backend Tests Pass", "head_sha": SHA,
         "app": {"id": 1}, "status": "completed", "conclusion": "success"}


def evidence(pr=None):
    return deepcopy((pr or PR, FILES, [CHECK], []))


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


class MergePolicyTests(unittest.TestCase):
    def decide(self, *, pr=None, files=None, checks=None, statuses=None, config=None, now=NOW):
        return policy.merge_decision(deepcopy(PR if pr is None else pr),
                                     FILES if files is None else files,
                                     [CHECK] if checks is None else checks,
                                     [] if statuses is None else statuses,
                                     CONFIG if config is None else config, now)[0]

    def test_all_default_conditions_allow(self):
        self.assertTrue(self.decide())
        self.assertEqual(policy.required_checks({}), ["Backend Tests Pass"])

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
        cases.append({**PR, "head": {**PR["head"], "sha": "not-a-sha"}})
        for title in ("chore(main): release 1.2.3\nother", "chore(main): release 1.2.3-beta.1",
                      "chore(main): release 01.2.3", "chore(dev): release 1.2.3"):
            cases.append({**PR, "title": title})
        for candidate in cases:
            with self.subTest(candidate=candidate):
                self.assertFalse(self.decide(pr=candidate))

    def test_hold_and_missing_labels_block(self):
        self.assertFalse(self.decide(pr={**PR, "labels": [{"name": "release-hold"}]}))
        self.assertFalse(self.decide(pr={**PR, "labels": None}))
        self.assertTrue(self.decide(pr={**PR, "labels": [{"name": "autorelease: pending"}]}))

    def test_only_complete_release_metadata_file_list_is_allowed(self):
        for filename in policy.RELEASE_FILES:
            self.assertTrue(self.decide(files=[{"filename": filename, "status": "modified"}]))
        for filename in (".github/workflows/release-images.yml", "futureagi/app/views.py",
                         "futureagi/accounts/migrations/0099_new.py", "other.md"):
            self.assertFalse(self.decide(files=[{"filename": filename, "status": "modified"}]))
        self.assertFalse(self.decide(files=[]))
        self.assertFalse(self.decide(pr={**PR, "changed_files": 2}))
        for status in ("removed", "renamed", None):
            self.assertFalse(self.decide(files=[{"filename": "CHANGELOG.md", "status": status}]))

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

    def test_required_list_is_configurable_and_e2e_is_advisory(self):
        e2e = {**CHECK, "name": "E2E", "conclusion": "failure"}
        self.assertTrue(self.decide(checks=[CHECK, e2e]))
        config = {**CONFIG, "RELEASE_AUTOPILOT_REQUIRED_CHECKS": "Backend Tests Pass, E2E\nBackend Tests Pass"}
        self.assertEqual(policy.required_checks(config), ["Backend Tests Pass", "E2E"])
        self.assertFalse(self.decide(checks=[CHECK, e2e], config=config))
        self.assertTrue(self.decide(checks=[CHECK, {**e2e, "conclusion": "success"}], config=config))
        self.assertFalse(self.decide(config={**CONFIG, "RELEASE_AUTOPILOT_REQUIRED_CHECKS": ", \n"}))

    def test_latest_attempt_wins_and_duplicate_producers_must_pass(self):
        failure = {**CHECK, "id": 2, "conclusion": "failure"}
        self.assertFalse(self.decide(checks=[failure, CHECK]))
        self.assertTrue(self.decide(checks=[failure, {**CHECK, "id": 3}]))
        self.assertFalse(self.decide(checks=[CHECK, {**failure, "app": {"id": 2}}]))
        self.assertFalse(self.decide(checks=[CHECK, {**CHECK, "id": 3, "status": "queued"}]))

    def test_legacy_statuses_are_bound_to_the_same_commit(self):
        status = {"context": "Backend Tests Pass", "sha": SHA, "id": 1, "state": "success"}
        self.assertTrue(self.decide(checks=[], statuses=[status]))
        self.assertFalse(self.decide(checks=[], statuses=[{**status, "sha": BASE}]))
        self.assertFalse(self.decide(statuses=[status, {**status, "id": 2, "state": "pending"}]))
        self.assertFalse(self.decide(checks=[{**CHECK, "conclusion": "failure"}], statuses=[status]))


class EnvironmentPolicyTests(unittest.TestCase):
    def decide(self, enabled="on", event="push", ref="refs/tags/v1.2.3", version="v1.2.3", count=0):
        return policy.environment_decision(enabled, event, ref, version, count)[0]

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
        self.assertEqual(self.decide(version="v1.2.3-beta.1", ref="refs/tags/v1.2.3-beta.1"), "production-auto")

    def test_oss_and_ee_migration_paths_and_initializers(self):
        for path in ("futureagi/accounts/migrations/0002_new.py",
                     "futureagi/ee/billing/migrations/0002_new.py", "ee/billing/migrations/0002_new.py",
                     "futureagi/app/migrations/0002___init___data.py"):
            self.assertTrue(policy.is_migration(path), path)
        for path in ("futureagi/app/migrations/__init__.py", "futureagi/ee/billing/migrations/__init__.py",
                     "futureagi/app/migrations/README.md", "other/app/migrations/new.py", "futureagi/app/views.py"):
            self.assertFalse(policy.is_migration(path), path)


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

    def release(self, *, event="push", version="v1.1.0", sha=None, **kwargs):
        output = self.root / "outputs"
        with patch.dict(os.environ, {"GITHUB_EVENT_NAME": event, "GITHUB_REF": f"refs/tags/{version}",
                                     "GITHUB_SHA": sha or self.sha, "RELEASE_AUTO_APPROVE": "on",
                                     "GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(self.root / "summary")}, clear=True), \
                patch("sys.stdout", new_callable=io.StringIO):
            policy.release_environment(version, **kwargs)
        return output.read_text()

    def test_reachable_tag_and_no_additions_are_auto(self):
        text = self.release()
        self.assertIn("environment=production-auto\n", text)
        self.assertIn("migrations=0\n", text)
        self.assertIn(f"tag_sha={self.sha}\n", text)

    def test_non_main_tag_is_refused_even_when_manual(self):
        self.git("checkout", "-b", "unreviewed")
        self.write("unreviewed.txt")
        self.commit()
        self.git("tag", "v1.2.0")
        with self.assertRaises(RuntimeError):
            self.release(event="workflow_dispatch", version="v1.2.0")

    def test_event_sha_and_approval_sha_must_not_drift(self):
        prior = self.git("rev-parse", "v1.0.0").strip()
        with self.assertRaises(RuntimeError):
            self.release(sha=prior)
        with self.assertRaises(RuntimeError):
            self.release(expected_sha=prior, event="workflow_dispatch")
        self.assertIn("environment=production-auto", self.release(expected_sha=self.sha))

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
        self.assertEqual(policy.migration_evidence("v1.2.0", sha), ("v1.1.0", 3))
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
        self.assertIn("migrations=unknown", text)

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
                return [CHECK]
            self.assertIn(f"commits/{SHA}/statuses", path)
            return [{"id": 1, "context": "Backend Tests Pass", "state": "success"}]
        with patch.object(policy, "api", return_value=PR), patch.object(policy, "pages", side_effect=listing):
            result = policy.snapshot(42)
            self.assertEqual(result[3][0]["sha"], SHA)
        with patch.object(policy, "api", side_effect=[PR, {**PR, "labels": [{"name": "release-hold"}]}]), \
                patch.object(policy, "pages", side_effect=listing), self.assertRaises(RuntimeError):
            policy.snapshot(42)

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
    def run_merge(self, *, first=None, final=None, already_approved=False, merge_result=True):
        first = deepcopy(first or PR)
        final = deepcopy(final or first)
        marker = f"<!-- release-autopilot-v1:{SHA} -->"
        comments = [{"user": {"login": "github-actions[bot]"}, "body": marker}] if already_approved else []
        reviews = [{"id": 1, "user": {"login": "github-actions[bot]"}, "body": marker,
                    "state": "APPROVED", "commit_id": SHA}] if already_approved else []
        if already_approved:
            first["labels"] = final["labels"] = [{"name": policy.APPROVED_LABEL}]
        env = {**CONFIG, "EXPECTED_PR": "42", "EXPECTED_HEAD_SHA": SHA,
               "RELEASE_APP_TOKEN": "synthetic-app", "GH_TOKEN": "synthetic-actions"}
        with patch.dict(os.environ, env, clear=True), patch.object(policy, "datetime", FrozenDatetime), \
                patch.object(policy, "snapshot", side_effect=[evidence(first), evidence(final)]), \
                patch.object(policy, "pages", side_effect=[comments, reviews]), \
                patch.object(policy, "api", return_value={"merged": merge_result}) as api, \
                patch("sys.stdout", new_callable=io.StringIO):
            policy.autopilot(merge=True)
            return api.call_args_list

    def test_actions_approves_app_merges_with_expected_sha_and_audit(self):
        calls = self.run_merge()
        approval = next(call for call in calls if call.args[0] == "pulls/42/reviews")
        merge = next(call for call in calls if call.args[0] == "pulls/42/merge")
        self.assertNotIn("token", approval.kwargs)
        self.assertEqual(approval.kwargs["data"]["event"], "APPROVE")
        self.assertEqual(approval.kwargs["data"]["commit_id"], SHA)
        self.assertEqual(merge.kwargs["token"], "synthetic-app")
        self.assertEqual(merge.kwargs["data"], {"sha": SHA, "merge_method": "squash"})
        labels = [call.kwargs["data"]["labels"] for call in calls if call.args[0] == "issues/42/labels"]
        self.assertEqual(labels, [[policy.APPROVED_LABEL], [policy.LABEL]])
        self.assertIn("Backend Tests Pass", approval.kwargs["data"]["body"])

    def test_update_cooldown_is_not_bypassed_for_own_audit(self):
        calls = self.run_merge(final={**PR, "updated_at": "2026-11-10T10:00:00Z"})
        self.assertFalse(any(call.args[0].endswith("/merge") for call in calls))

    def test_next_eligible_run_does_not_reset_cooldown_with_duplicate_writes(self):
        calls = self.run_merge(already_approved=True)
        self.assertFalse(any(call.args[0] == "pulls/42/reviews" for call in calls))
        self.assertTrue(any(call.args[0] == "pulls/42/merge" for call in calls))
        comments = [call for call in calls if call.args[0] == "issues/42/comments"]
        self.assertEqual(len(comments), 1)  # Success comment only, after merge.

    def test_changed_head_base_or_hold_after_approval_never_merges(self):
        cases = [{**PR, "head": {**PR["head"], "sha": "c" * 40}},
                 {**PR, "base": {**PR["base"], "sha": "c" * 40}},
                 {**PR, "labels": [{"name": "release-hold"}]}]
        for final in cases:
            with self.subTest(final=final):
                calls = self.run_merge(final=final)
                self.assertFalse(any(call.args[0].endswith("/merge") for call in calls))

    def test_changed_head_since_check_performs_no_writes(self):
        calls = self.run_merge(first={**PR, "head": {**PR["head"], "sha": "c" * 40}})
        self.assertEqual(calls, [])

    def test_failed_merge_is_not_labelled_as_merged(self):
        with self.assertRaises(RuntimeError):
            self.run_merge(merge_result=False)

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
            self.assertIn("python3 scripts/release_autopilot.py environment", block)
            self.assertLess(block.index('echo "components: $components"'), block.index("actions/checkout@v4"))
        self.assertIn("environment: ${{ needs.guard.outputs.environment }}", approve)
        self.assertIn('--expected-sha "$EXPECTED_SHA" --approved-environment "$APPROVED_ENVIRONMENT"', approve)
        self.assertIn("Migrations: ${{ needs.guard.outputs.migrations }} new files", text)

    def test_autopilot_is_default_branch_only_and_has_required_triggers(self):
        text = (Path(__file__).resolve().parents[1] / ".github/workflows/release-autopilot.yml").read_text()
        for fragment in ('cron: "*/15 * * * *"', 'workflows: ["Backend CI"]', "workflow_dispatch:",
                         "github.event.repository.default_branch", "persist-credentials: false", "checks: read",
                         "statuses: read", "permission-contents: write", "permission-pull-requests: write"):
            self.assertIn(fragment, text)
        self.assertNotIn("pull_request_target:", text)
        self.assertNotIn("github.event.workflow_run.head_sha", text)
        self.assertNotIn("download-artifact", text)


if __name__ == "__main__":
    unittest.main()
