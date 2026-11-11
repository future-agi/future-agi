"""Synthetic policy, API-adapter and Git-history tests; no real GitHub requests."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import base64
import hashlib
import io
import json
import os
import re
from pathlib import Path
import subprocess
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
    "title": "chore(main): release 1.2.3", "labels": [], "changed_files": 4,
    "updated_at": "2026-11-10T09:00:00Z", "mergeable": True,
}
FILES = [{"filename": path, "status": "modified"} for path in sorted(policy.RELEASE_FILES)]
APPROVER = "futureagi-release-approver[bot]"
CHECK = {"id": 1, "name": "Backend Tests Pass", "head_sha": SHA,
         "app": {"id": 1}, "status": "completed", "conclusion": "success"}


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
    return deepcopy((pr or PR, FILES, [CHECK], [], METADATA if metadata is None else metadata))


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


class MergePolicyTests(unittest.TestCase):
    def decide(self, *, pr=None, files=None, checks=None, statuses=None, metadata=None, config=None, now=NOW):
        return policy.merge_decision(deepcopy(PR if pr is None else pr),
                                     FILES if files is None else files,
                                     [CHECK] if checks is None else checks,
                                     [] if statuses is None else statuses,
                                     METADATA if metadata is None else metadata,
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

    def test_reachable_tag_and_no_additions_are_auto(self):
        text = self.release()
        self.assertIn("environment=production-auto\n", text)
        self.assertIn("migrations=0\n", text)
        # The previous shipped release is the baseline the bump commit carries.
        self.assertIn("baseline=v1.0.0\n", text)
        self.assertIn(f"tag_sha={self.sha}\n", text)

    def test_non_main_tag_is_refused_even_when_manual(self):
        self.git("checkout", "-b", "unreviewed")
        self.write("unreviewed.txt")
        self.commit()
        self.git("tag", "v1.2.0")
        with self.assertRaises(RuntimeError):
            self.release(event="workflow_dispatch", version="v1.2.0")

    def test_prerelease_from_dev_is_allowed_with_human_review(self):
        self.git("checkout", "-b", "dev")
        self.write("dev.txt")
        self.commit()
        self.git("update-ref", "refs/remotes/origin/dev", "HEAD")
        self.git("tag", "v1.2.0-rc.1")
        text = self.release(version="v1.2.0-rc.1", sha=self.git("rev-parse", "HEAD").strip())
        self.assertIn("environment=Production\n", text)
        self.git("tag", "v1.2.0")
        with self.assertRaises(RuntimeError):
            self.release(version="v1.2.0", sha=self.git("rev-parse", "HEAD").strip())

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
        self.assertIn("migrations=unknown", text)
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
        self.assertIn("migrations=0\n", text)
        self.write("futureagi/tracer/services/clickhouse/v2/schema/028_x.sql")
        self.commit()
        self.git("tag", "v1.3.0")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        sha = self.git("rev-parse", "HEAD").strip()
        text = self.release(version="v1.3.0", sha=sha)
        self.assertIn("environment=Production\n", text)
        self.assertIn("migrations=1\n", text)

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
        self.assertIn("migrations=1\n", text)

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
                return [CHECK]
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
        self.assertIn("Backend Tests Pass", approval.kwargs["data"]["body"])

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


class ReleaseGateTests(unittest.TestCase):
    NOW = datetime(2026, 11, 11, 12, tzinfo=timezone.utc)

    def gates(self, runs, behind=0, main_date="2026-11-11T10:00:00Z", config=None, pr=None):
        responses = {"git/ref/heads/main": {"object": {"sha": "c" * 40}},
                     f"compare/{'c' * 40}...{SHA}": {"behind_by": behind},
                     f"commits/{'c' * 40}": {"commit": {"committer": {"date": main_date}}}}
        with patch.object(policy, "pages", return_value=runs), \
                patch.object(policy, "api", side_effect=lambda path, **kw: responses[path]):
            return policy.release_gates(pr or PR, config or {}, self.NOW)

    def callout_run(self, **fields):
        return {"id": 5, "path": policy.CALLOUT_WORKFLOW, "head_sha": SHA, "status": "completed",
                "conclusion": "success", "head_branch": policy.BRANCH,
                "head_repository": {"full_name": policy.REPOSITORY}, **fields}

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
            self.assertIn("python3 scripts/release_autopilot.py environment", block)
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
        self.assertEqual(step["env"]["MIGRATIONS"], "${{ needs.guard.outputs.migrations }}")
        self.assertEqual(step["env"]["MIGRATION_BASELINE"], "${{ needs.guard.outputs.migration-baseline }}")
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

    def test_autopilot_is_default_branch_only_and_has_required_triggers(self):
        text = self.AUTOPILOT.read_text()
        for fragment in ('cron: "*/15 * * * *"', 'workflows: ["Backend CI"]', "workflow_dispatch:",
                         "github.event.repository.default_branch", "persist-credentials: false", "checks: read",
                         "statuses: read", "actions: read", "permission-contents: write", "permission-pull-requests: write",
                         "python3 -m pip install --disable-pip-version-check PyYAML==6.0.3"):
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


if __name__ == "__main__":
    unittest.main()
