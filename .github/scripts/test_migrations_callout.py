#!/usr/bin/env python3
"""Stdlib-only CLI regressions; every test owns a throwaway git repository."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("migrations_callout.py").resolve()
CALLOUT = "Contains database migrations"
FOOTER = "This PR was generated with Release Please."
# Actual added files from the four affected release tag ranges.
RELEASES = {
    "1.53.0": (
        "1.52.0",
        [
            "accounts/0026_orgapikey_ingest_type",
            "accounts/0027_orgapikey_simulation_agent_id",
            "accounts/0028_merge_authtoken_scope_and_simulation_agent_key",
            "simulate/0098_agent_version_setup",
            "tracer/0115_project_simulation_agent",
            "tracer/0117_merge_error_feed_and_simulation_project",
        ],
    ),
    "1.55.0": ("1.54.0", ["tracer/0118_sharedlink_error_feed_issue_resource_type"]),
    "1.56.1": ("1.56.0", ["agentcc/0029_request_log_unique_request_id"]),
    "1.58.0": (
        "1.57.0",
        [
            "simulate/0100_agent_fix",
            "simulate/0101_agent_fix_candidate_override",
        ],
    ),
}


class MigrationsCalloutTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repo = Path(self.directory.name)
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Migration callout test")
        self.git("commit", "--allow-empty", "-qm", "Initial commit")

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.repo, check=True, capture_output=True, text=True
        ).stdout.strip()

    def commit_files(self, names):
        for name in names:
            app, migration = name.split("/")
            path = self.repo / "futureagi" / app / "migrations" / f"{migration}.py"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"# Migration {migration}\n")
        self.git("add", "futureagi")
        self.git("commit", "-qm", "Add migrations")

    def prepare(self, version="1.53.0"):
        previous, names = RELEASES[version]
        self.git("tag", f"v{previous}")
        self.commit_files(names)
        self.base_sha = self.git("rev-parse", "HEAD")
        self.git("commit", "--allow-empty", "-qm", f"Release {version}")
        self.head_sha = self.git("rev-parse", "HEAD")
        return self.notes(version)

    def notes(self, version="1.53.0", callout=False):
        warning = f"\n\n{CALLOUT}" if callout else ""
        return f"## [{version}](https://example.invalid/compare) (2026-11-03){warning}\n\n### Fixes\n\n* A fix"

    def pr(self, notes, header="Release bot", footer=FOOTER):
        return {
            "pull_request": {
                "base": {"ref": "main", "sha": self.base_sha},
                "head": {"ref": "release-please--branches--main", "sha": self.head_sha},
                "merge_commit_sha": self.head_sha,
                "body": f"{header}\n---\n\n{notes}\n\n---\n{footer}",
            }
        }

    def release(self, version, notes):
        self.git("tag", f"v{version}", self.head_sha)
        return {
            "release": {
                "tag_name": f"v{version}",
                "body": notes,
                "draft": False,
                "prerelease": False,
            }
        }

    def check(self, event_name, event, *, success, count=None):
        event_path = self.repo / "event.json"
        event_path.write_text(json.dumps(event))
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                str(SCRIPT),
                "--event-name",
                event_name,
                "--event-path",
                str(event_path),
            ],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode, 0 if success else 1, result.stdout + result.stderr
        )
        if count is not None:
            self.assertIn(f"Added migrations: {count} (", result.stdout)
        return result.stdout

    def test_v1530_header_callout_is_not_published(self):
        notes = self.prepare("1.53.0")
        event = self.pr(notes, header=f"{CALLOUT}\n\nRelease bot")
        self.assertIn(CALLOUT, event["pull_request"]["body"])  # Old grep passed.
        self.check("pull_request", event, success=False, count=6)
        self.check("release", self.release("1.53.0", notes), success=False, count=6)

    def test_v1550_merged_despite_failed_pr_check_is_flagged_on_publication(self):
        notes = self.prepare("1.55.0")
        self.check("pull_request", self.pr(notes), success=False, count=1)
        # No PR status is consulted by the release event: bypassing it cannot
        # silence the publication check. No release or PR write API is used.
        self.check("release", self.release("1.55.0", notes), success=False, count=1)

    def test_v1561_warning_before_version_heading_causes_empty_notes(self):
        notes = self.prepare("1.56.1")
        event = self.pr(
            f"> {CALLOUT}: agentcc/0029_request_log_unique_request_id\n\n{notes}"
        )
        self.assertIn(CALLOUT, event["pull_request"]["body"])  # Old grep passed.
        output = self.check("pull_request", event, success=False, count=1)
        self.assertIn("must start with the version heading", output)
        self.check("release", self.release("1.56.1", ""), success=False, count=1)

    def test_v1580_header_migration_list_is_not_published(self):
        notes = self.prepare("1.58.0")
        header = f"{CALLOUT}\n\n- simulate 0100_agent_fix\n- simulate 0101_agent_fix_candidate_override\n\nRelease bot"
        self.check(
            "pull_request", self.pr(notes, header=header), success=False, count=2
        )
        self.check("release", self.release("1.58.0", notes), success=False, count=2)

    def test_callout_after_version_heading_survives_publication(self):
        self.prepare()
        notes = self.notes(callout=True)
        self.check("pull_request", self.pr(notes), success=True, count=6)
        self.check("release", self.release("1.53.0", notes), success=True, count=6)

    def test_footer_callout_does_not_satisfy_pr_check(self):
        notes = self.prepare()
        self.check(
            "pull_request", self.pr(notes, footer=CALLOUT), success=False, count=6
        )

    def test_later_note_edit_removing_callout_is_flagged(self):
        self.prepare()
        event = self.release("1.53.0", self.notes(callout=True))
        self.check("release", event, success=True, count=6)
        event["action"] = "edited"
        event["release"]["body"] = self.notes()
        self.check("release", event, success=False, count=6)

    def test_current_release_and_dev_tags_do_not_hide_migrations(self):
        notes = self.prepare()
        self.git("tag", "v9.0.0-dev")
        self.git("tag", "v9.0.0-rc.1")
        self.check("release", self.release("1.53.0", notes), success=False, count=6)

    def test_later_main_release_does_not_change_pr_event_range(self):
        notes = self.prepare()
        self.git("commit", "--allow-empty", "-qm", "Later release")
        self.git("tag", "v1.54.0")
        self.check("pull_request", self.pr(notes), success=False, count=6)

    def test_no_migrations_does_not_require_callout(self):
        self.git("tag", "v1.52.0")
        self.base_sha = self.git("rev-parse", "HEAD")
        self.commit_files(["tracer/__init__"])
        self.head_sha = self.git("rev-parse", "HEAD")
        self.check("pull_request", self.pr(self.notes()), success=True, count=0)
        self.check(
            "release", self.release("1.53.0", self.notes()), success=True, count=0
        )

    def test_existing_migration_edit_does_not_count_as_added(self):
        self.commit_files(["tracer/0001_initial"])
        self.git("tag", "v1.52.0")
        path = self.repo / "futureagi/tracer/migrations/0001_initial.py"
        path.write_text("# Updated existing migration\n")
        self.git("commit", "-am", "Edit existing migration")
        self.base_sha = self.head_sha = self.git("rev-parse", "HEAD")
        self.check("pull_request", self.pr(self.notes()), success=True, count=0)

    def test_first_release_with_no_prior_tag(self):
        self.commit_files(["tracer/0001_initial"])
        self.base_sha = self.head_sha = self.git("rev-parse", "HEAD")
        self.check("release", self.release("1.0.0", ""), success=False, count=1)

    def test_missing_git_ref_fails_closed(self):
        self.prepare()
        event = self.pr(self.notes(callout=True))
        event["pull_request"]["merge_commit_sha"] = "0" * 40
        self.check("pull_request", event, success=False)

    def test_stale_pr_head_does_not_hide_migrations_already_on_main(self):
        self.git("tag", "v1.52.0")
        self.git("checkout", "-qb", "release-pr")
        self.git("commit", "--allow-empty", "-qm", "Release version bump")
        self.head_sha = self.git("rev-parse", "HEAD")
        self.git("checkout", "-qb", "base-main", "v1.52.0")
        self.commit_files(["tracer/0001_new_on_main"])
        self.base_sha = self.git("rev-parse", "HEAD")
        self.git("checkout", "release-pr")
        self.git("merge", "--no-edit", "base-main")
        event = self.pr(self.notes())
        event["pull_request"]["merge_commit_sha"] = self.git("rev-parse", "HEAD")
        self.check("pull_request", event, success=False, count=1)

    def test_missing_merge_preview_fails_closed(self):
        self.prepare()
        event = self.pr(self.notes(callout=True))
        event["pull_request"]["merge_commit_sha"] = None
        self.check("pull_request", event, success=False)

    def test_crlf_body_and_single_delimiter_follow_release_please(self):
        self.prepare()
        event = self.pr(self.notes(callout=True))
        event["pull_request"]["body"] = event["pull_request"]["body"].replace(
            "\n", "\r\n"
        )
        self.check("pull_request", event, success=True, count=6)
        event["pull_request"][
            "body"
        ] = f"Release bot\n---\n{self.notes(callout=True)}\n{FOOTER}"
        self.check("pull_request", event, success=True, count=6)

    def test_unknown_pr_format_fails_closed(self):
        self.prepare()
        event = self.pr(self.notes(callout=True))
        event["pull_request"]["body"] = self.notes(callout=True)
        self.check("pull_request", event, success=False, count=6)
        for name in ["details", "DETAILS"]:
            event = self.pr(
                f"{self.notes(callout=True)}\n<{name}><summary>1.53.0</summary></{name}>"
            )
            self.check("pull_request", event, success=False, count=6)

    def test_drafts_prereleases_and_unrelated_prs_are_ignored(self):
        self.prepare()
        event = self.pr(self.notes())
        event["pull_request"]["head"]["ref"] = "fix/ordinary-pr"
        self.check("pull_request", event, success=True)
        event = self.pr(self.notes())
        event["pull_request"]["base"]["ref"] = "dev"
        self.check("pull_request", event, success=True)
        for fields in [
            {"draft": True},
            {"prerelease": True},
            {"tag_name": "v1.53.0-dev"},
        ]:
            event = {
                "release": {
                    "tag_name": "v1.53.0",
                    "draft": False,
                    "prerelease": False,
                    **fields,
                }
            }
            self.check("release", event, success=True)


if __name__ == "__main__":
    unittest.main()
