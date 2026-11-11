"""Synthetic policy, API-adapter and Git-history tests; no real GitHub requests."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import base64
import hashlib
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
    "title": "chore(main): release 1.2.3", "labels": [], "changed_files": 4,
    "updated_at": "2026-11-10T09:00:00Z", "mergeable": True,
}
FILES = [{"filename": path, "status": "modified"} for path in sorted(policy.RELEASE_FILES)]
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
    def decide(self, enabled="on", event="push", ref="refs/tags/v1.2.3", version="v1.2.3", count=0, actor=policy.AUTHOR):
        return policy.environment_decision(enabled, event, ref, version, count, actor)[0]

    def test_only_the_release_app_tag_push_skips_review(self):
        for actor in (None, "", "someone", "github-actions[bot]"):
            self.assertEqual(self.decide(actor=actor), "Production")

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

    def test_clickhouse_appliers_and_schema_directory_exist_in_this_repository(self):
        # A rename would silently stop counting; fail here instead.
        repo = Path(__file__).resolve().parents[1]
        for path in policy.CLICKHOUSE_APPLIERS:
            self.assertTrue((repo / path).is_file(), path)
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

    def release(self, *, event="push", version="v1.1.0", sha=None, **kwargs):
        output = self.root / "outputs"
        with patch.dict(os.environ, {"GITHUB_EVENT_NAME": event, "GITHUB_REF": f"refs/tags/{version}",
                                     "GITHUB_ACTOR": policy.AUTHOR,
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

    def test_tag_pushed_by_a_person_keeps_review(self):
        output = self.root / "outputs"
        with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/tags/v1.1.0", "GITHUB_ACTOR": "someone",
                                     "GITHUB_SHA": self.sha, "RELEASE_AUTO_APPROVE": "on", "GITHUB_OUTPUT": str(output),
                                     "GITHUB_STEP_SUMMARY": str(self.root / "summary")}, clear=True), \
                patch("sys.stdout", new_callable=io.StringIO):
            policy.release_environment("v1.1.0")
        self.assertIn("environment=Production", output.read_text())

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
        for error in (RuntimeError("api"), KeyError("jobs")):
            with self.subTest(error=type(error).__name__), patch.object(policy, "git", return_value=SHA), \
                    patch.object(policy, "api", return_value={"workflow_runs": [self.run_row()]}), \
                    patch.object(policy, "pages", side_effect=error), self.assertRaises(type(error)):
                # migration_evidence maps each of these to an unknown count (Production).
                policy.published("v1.1.0")
        with patch.object(policy, "git", return_value=SHA), patch.object(policy, "api", return_value={"workflow_runs": None}):
            self.assertFalse(policy.published("v1.1.0"))

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
                  already_approved=False, merge_result=True, prior_marker=None):
        first = deepcopy(first or PR)
        final = deepcopy(final or first)
        marker = prior_marker or f"<!-- release-autopilot-v2:{SHA}:{BASE} -->"
        comments = [{"user": {"login": "github-actions[bot]"}, "body": marker}] if already_approved else []
        reviews = [{"id": 1, "user": {"login": "github-actions[bot]"}, "body": marker,
                    "state": "APPROVED", "commit_id": SHA}] if already_approved else []
        if already_approved:
            first["labels"].append({"name": policy.APPROVED_LABEL})
            final["labels"].append({"name": policy.APPROVED_LABEL})
        env = {**CONFIG, "EXPECTED_PR": "42", "EXPECTED_HEAD_SHA": SHA,
               "RELEASE_APP_TOKEN": "synthetic-app", "GH_TOKEN": "synthetic-actions"}
        with patch.dict(os.environ, env, clear=True), patch.object(policy, "datetime", FrozenDatetime), \
                patch.object(policy, "snapshot", side_effect=[evidence(first, first_metadata), evidence(final, final_metadata)]), \
                patch.object(policy, "pages", side_effect=[comments, reviews]), \
                patch.object(policy, "api", return_value={"merged": merge_result}) as api, \
                patch.object(policy, "release_gates", return_value=(True, "synthetic gates passed")), \
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

    def gates(self, runs, behind=0, main_date="2026-11-11T10:00:00Z", config=None):
        responses = {"git/ref/heads/main": {"object": {"sha": "c" * 40}},
                     f"compare/{'c' * 40}...{SHA}": {"behind_by": behind},
                     f"commits/{'c' * 40}": {"commit": {"committer": {"date": main_date}}}}
        with patch.object(policy, "pages", return_value=runs), \
                patch.object(policy, "api", side_effect=lambda path, **kw: responses[path]):
            return policy.release_gates(PR, config or {}, self.NOW)

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
        self.assertTrue(self.gates([self.callout_run()], behind=1, main_date="2026-11-11T10:00:00Z")[0])
        self.assertFalse(self.gates([self.callout_run()], behind=1, main_date="not a date")[0])


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
                         "statuses: read", "actions: read", "permission-contents: write", "permission-pull-requests: write",
                         "python3 -m pip install --disable-pip-version-check PyYAML==6.0.3"):
            self.assertIn(fragment, text)
        self.assertNotIn("pull_request_target:", text)
        self.assertNotIn("github.event.workflow_run.head_sha", text)
        self.assertNotIn("download-artifact", text)


if __name__ == "__main__":
    unittest.main()
