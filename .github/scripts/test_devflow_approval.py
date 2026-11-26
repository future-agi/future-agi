#!/usr/bin/env python3
"""Stdlib-only tests for devflow_approval.decide(); every rule has a fixture."""

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import devflow_approval as da  # noqa: E402

REPO = "org/repo"
HEAD = "a" * 40
OLD = "b" * 40
FLOOR = json.loads(da.FLOOR_FILE.read_text())["paths"]


def review_body(**overrides):
    verdict = {
        "schema": "devflow-review/v1",
        "verdict": "pass",
        "tier": "low",
        "tier_reason": "small change",
        "needs_person": False,
        "round": 1,
        "head": HEAD,
        "reviewer": "rev",
        "findings": [],
        "engine": "unknown keys are ignored",
    }
    verdict.update(overrides)
    return (
        f"{da.REVIEW_MARKER}\n**Agent review** · round 1 · tier: low · verdict: pass\n"
        f"<details><summary>verdict JSON</summary>\n\n```json\n{json.dumps(verdict)}\n```\n</details>"
    )


def review(
    login="rev",
    commit=HEAD,
    at="2026-11-26T10:00:00Z",
    body=None,
    state="COMMENTED",
    **verdict,
):
    return {
        "id": hash((login, commit, at)) & 0xFFFF,
        "user": {"login": login},
        "commit_id": commit,
        "submitted_at": at,
        "state": state,
        "body": review_body(**verdict) if body is None else body,
    }


def marker(login="rev", by="github-actions[bot]"):
    return {
        "user": {"login": by},
        "body": f"<!-- devflow-assigned:v1 login={login} at=2026-11-26T09:00:00Z by=reviewer-assigner round=1 -->\nRequested.",
    }


class Case:
    """One pull request, valid for approval until a test breaks one rule."""

    def __init__(self):
        self.pr = {
            "state": "open",
            "draft": False,
            "user": {"login": "author"},
            "head": {"sha": HEAD, "repo": {"full_name": REPO}},
            "base": {"ref": "dev", "repo": {"full_name": REPO}},
            "labels": [{"name": "devflow:reviewed"}],
            "changed_files": 1,
        }
        self.reviews = [review()]
        self.comments = [marker()]
        self.files = [{"filename": "futureagi/tracer/views/span.py"}]
        self.permissions = {"author": "write", "rev": "write"}

    def files_are(self, *names):
        self.files = [{"filename": n} for n in names]
        self.pr["changed_files"] = len(names)

    def decide(self):
        return da.decide(
            self.pr, self.reviews, self.comments, self.files, self.permissions, FLOOR
        )


class DecideTests(unittest.TestCase):
    def check(self, case, action, reason=None):
        result = case.decide()
        self.assertEqual(result["action"], action, result)
        if reason:
            self.assertTrue(any(reason in r for r in result["reasons"]), result)
        return result

    # approve
    def test_passing_low_tier_review_on_head_approves(self):
        result = self.check(Case(), "approve")
        self.assertEqual(result["head"], HEAD)

    def test_admin_permission_counts_as_write(self):
        case = Case()
        case.permissions = {"author": "admin", "rev": "admin"}
        self.check(case, "approve")

    def test_migrations_are_left_to_the_review_tier(self):
        case = Case()
        case.files_are(
            "futureagi/tracer/migrations/0120_add_column.py",
            "migrations/0001_initial.py",
        )
        self.check(case, "approve")

    def test_newest_marker_names_the_reviewer(self):
        case = Case()
        case.comments = [marker("old-rev"), marker("rev")]
        case.reviews = [review("old-rev", verdict="block"), review("rev")]
        self.check(case, "approve")

    def test_logins_compare_case_insensitively(self):
        case = Case()
        case.comments = [marker("Rev")]
        self.check(case, "approve")

    # skip
    def test_skip_closed_draft_wrong_base_and_fork(self):
        for key, value, reason in [
            ("state", "closed", "not open"),
            ("draft", True, "draft"),
            ("base", {"ref": "main", "repo": {"full_name": REPO}}, "base is not dev"),
            ("head", {"sha": HEAD, "repo": {"full_name": "fork/repo"}}, "fork"),
        ]:
            with self.subTest(key=key):
                case = Case()
                case.pr[key] = value
                self.check(case, "skip", reason)

    def test_skip_when_needs_human_is_already_set(self):
        case = Case()
        case.pr["labels"].append({"name": "needs-human"})
        self.check(case, "skip", "needs-human")

    def test_skip_when_the_bot_already_approved_the_head(self):
        case = Case()
        case.reviews.append(
            review(
                "github-actions[bot]", state="APPROVED", body="Approved automatically."
            )
        )
        self.check(case, "skip", "already approved")

    def test_bot_approval_of_an_old_head_does_not_skip(self):
        case = Case()
        case.reviews.append(
            review("github-actions[bot]", commit=OLD, state="APPROVED", body="ok")
        )
        self.check(case, "approve")

    # wait: marker trust
    def test_wait_without_a_marker(self):
        case = Case()
        case.comments = []
        self.check(case, "wait", "no devflow-assigned marker")

    def test_marker_from_a_person_is_ignored(self):
        case = Case()
        case.comments = [marker("rev", by="author")]
        self.check(case, "wait", "no devflow-assigned marker")

    def test_marker_needs_the_exact_bot_login(self):
        case = Case()
        case.comments = [marker("rev", by="github-actions")]
        self.check(case, "wait", "no devflow-assigned marker")

    def test_marker_naming_the_author_waits(self):
        case = Case()
        case.comments = [marker("author")]
        case.reviews = [review("author")]
        self.check(case, "wait", "is the author")

    def test_reviewer_without_write_access_waits(self):
        for perm in ("read", "none", None):
            with self.subTest(perm=perm):
                case = Case()
                case.permissions["rev"] = perm
                self.check(case, "wait", "no write access")

    # wait: the review itself
    def test_review_by_someone_not_assigned_is_ignored(self):
        case = Case()
        case.reviews = [review("other")]
        case.permissions["other"] = "write"
        self.check(case, "wait", "no devflow review by rev")

    def test_review_without_the_marker_is_ignored(self):
        case = Case()
        case.reviews = [review(body="LGTM")]
        self.check(case, "wait", "no devflow review by rev")

    def test_review_on_an_old_commit_waits(self):
        case = Case()
        case.reviews = [review(commit=OLD, head=OLD)]
        self.check(case, "wait", "not the head")

    def test_only_the_latest_review_by_the_reviewer_counts(self):
        case = Case()
        case.reviews = [
            review(at="2026-11-26T10:00:00Z"),
            review(commit=OLD, at="2026-11-26T11:00:00Z", head=OLD),
        ]
        self.check(case, "wait", "not the head")

    def test_malformed_reviews_wait(self):
        for body, reason in [
            (f"{da.REVIEW_MARKER}\nno json here", "no ```json block"),
            (f"{da.REVIEW_MARKER}\n```json\n{{not json\n```", "malformed"),
            (review_body(schema="devflow-review/v2"), "schema"),
            (review_body(findings=None), "findings"),
        ]:
            with self.subTest(reason=reason):
                case = Case()
                case.reviews = [review(body=body)]
                self.check(case, "wait", reason)

    def test_verdict_naming_another_head_waits(self):
        case = Case()
        case.reviews = [review(head=OLD)]
        self.check(case, "wait", "another head")

    def test_verdict_other_than_pass_waits(self):
        for verdict in ("block", "PASS", None):
            with self.subTest(verdict=verdict):
                case = Case()
                case.reviews = [review(verdict=verdict)]
                self.check(case, "wait", "verdict is")

    # needs-human
    def test_tier_other_than_low_needs_a_human(self):
        for tier in ("high", None, "LOW"):
            with self.subTest(tier=tier):
                case = Case()
                case.reviews = [review(tier=tier)]
                self.check(case, "needs-human", "tier is")

    def test_needs_person_other_than_false_needs_a_human(self):
        for value in (True, None, "false", 0):
            with self.subTest(value=value):
                case = Case()
                case.reviews = [review(needs_person=value)]
                self.check(case, "needs-human", "needs_person")

    def test_floor_paths_need_a_human(self):
        for path in (
            ".github/workflows/admin-pr-approval.yml",
            ".github/CODEOWNERS",
            "futureagi/accounts/views/login.py",
            "futureagi/accounts/migrations/0052_x.py",
            "futureagi/saml2_auth/views.py",
            "futureagi/tfc/permissions/rbac.py",
            "futureagi/tfc/middleware/jwt_auth.py",
            "futureagi/ee/usage/deployment.py",
            "fi-collector/pkg/pricing/table.go",
            "agentcc-gateway/internal/plugins/credits/credits.go",
            "frontend/src/auth/guard/auth-guard.jsx",
            "frontend/src/pages/dashboard/settings/BillingPage.jsx",
            "agentcc-gateway/scripts/merge_pricing.py",
            "billing.py",
            "agentcc-gateway/internal/middleware/auth.go",
            "agentcc-gateway/internal/middleware/license_auth.go",
            "agentcc-gateway/internal/plugins/rbac/rbac.go",
            "agentcc-gateway/internal/plugins/quota/quota.go",
            "futureagi/agentcc/views/api_key.py",
            "futureagi/agentcc/permissions.py",
            "futureagi/tracer/views/feed/_permissions.py",
            "futureagi/mcp_server/views/oauth.py",
            "futureagi/tfc/licensing/types.py",
            "futureagi/simulate/authentication.py",
        ):
            with self.subTest(path=path):
                case = Case()
                case.files_are("futureagi/tracer/views/span.py", path)
                self.check(case, "needs-human", path)

    def test_rename_out_of_the_floor_needs_a_human(self):
        case = Case()
        case.files = [
            {
                "filename": "futureagi/common/login.py",
                "previous_filename": "futureagi/accounts/login.py",
            }
        ]
        self.check(case, "needs-human", "futureagi/accounts/login.py")

    def test_unread_files_need_a_human(self):
        case = Case()
        case.pr["changed_files"] = 3001
        self.check(case, "needs-human", "of 3001 changed files")

    def test_author_without_write_access_needs_a_human(self):
        case = Case()
        case.permissions["author"] = "read"
        self.check(case, "needs-human", "author author has no write access")

    def test_every_reason_is_reported(self):
        case = Case()
        case.reviews = [review(tier="high", needs_person=True)]
        case.files_are(".github/workflows/x.yml")
        self.assertEqual(len(case.decide()["reasons"]), 3)

    def test_decide_does_not_mutate_inputs(self):
        case = Case()
        before = copy.deepcopy((case.pr, case.reviews, case.comments, case.files))
        case.decide()
        self.assertEqual(before, (case.pr, case.reviews, case.comments, case.files))


class FloorTests(unittest.TestCase):
    def test_glob_semantics(self):
        cases = [
            (".github/**", ".github/workflows/a.yml", True),
            (".github/**", "x/.github/a.yml", False),
            ("futureagi/accounts/**", "futureagi/accounts_v2/a.py", False),
            ("**/*billing*", "frontend/src/BillingPage.jsx", True),
            ("**/*billing*", "billing.py", True),
            ("**/*billing*", "a/billing/b.py", False),
            ("**/*billing*/**", "a/billing/b.py", True),
            ("futureagi/tfc/ee_gating.py", "futureagi/tfc/ee_gatingXpy", False),
        ]
        for glob, path, expected in cases:
            with self.subTest(glob=glob, path=path):
                self.assertEqual(bool(da.glob_regex(glob).fullmatch(path)), expected)

    def test_floor_has_no_migration_glob(self):
        self.assertFalse([g for g in FLOOR if "migrations" in g])

    def test_floor_covers_github(self):
        self.assertIn(".github/**", FLOOR)


if __name__ == "__main__":
    unittest.main()
