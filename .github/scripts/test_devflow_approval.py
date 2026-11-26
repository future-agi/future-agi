#!/usr/bin/env python3
"""Stdlib-only tests for devflow_approval.decide(); every rule has a fixture."""

import copy
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import devflow_approval as da  # noqa: E402

REPO = "org/repo"
HEAD = "a" * 40
OLD = "b" * 40
CODEOWNERS = Path(__file__).resolve().parent.parent / "CODEOWNERS"
FLOOR_TEAM = "@future-agi/internal-all-write"


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
        }
        self.reviews = [review()]
        self.comments = [marker()]
        self.permissions = {"author": "write", "rev": "write"}

    def decide(self, tight=False, observe=False):
        return da.decide(
            self.pr,
            self.reviews,
            self.comments,
            self.permissions,
            tight,
            observe,
        )


class DecideTests(unittest.TestCase):
    def check(self, case, action, reason=None, tight=False):
        result = case.decide(tight)
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
        # Observe mode still decides it: the admin rule approves in that mode.
        self.assertEqual(case.decide(observe=True)["action"], "approve")

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

    def test_author_without_write_access_needs_a_human(self):
        case = Case()
        case.permissions["author"] = "read"
        self.check(case, "needs-human", "author author has no write access")

    def test_every_reason_is_reported(self):
        case = Case()
        case.reviews = [review(tier="high", needs_person=True)]
        case.permissions["author"] = "read"
        self.assertEqual(len(case.decide()["reasons"]), 3)

    def test_paths_are_not_decided_here(self):
        # The CODEOWNERS floor is the one source; GitHub enforces it. Red before:
        # decide() took a file list and a floor and sent .github/** to a person.
        self.check(Case(), "approve")
        self.assertFalse(hasattr(da, "floor_hits"))
        self.assertFalse((CODEOWNERS.parent / "approval-floor.json").exists())

    # tight mode (APPROVAL_MODE_TIGHT == "1")
    def test_tight_mode_sends_a_passing_low_tier_review_to_a_person(self):
        self.check(Case(), "needs-human", "APPROVAL_MODE_TIGHT", tight=True)

    def test_tight_mode_still_waits_on_a_block(self):
        case = Case()
        case.reviews = [review(verdict="block")]
        self.check(case, "wait", "verdict is", tight=True)

    def test_tight_mode_still_skips_a_draft(self):
        case = Case()
        case.pr["draft"] = True
        self.check(case, "skip", "draft", tight=True)

    def test_decide_does_not_mutate_inputs(self):
        case = Case()
        before = copy.deepcopy((case.pr, case.reviews, case.comments))
        case.decide()
        self.assertEqual(before, (case.pr, case.reviews, case.comments))


class TrainLabelTests(unittest.TestCase):
    def test_an_approved_pull_request_joins_the_train(self):
        self.assertEqual(da.train_labels(Case().pr), ["devflow:train"])

    def test_draft_fork_needs_human_or_already_labelled_get_none(self):
        for key, value in [
            ("draft", True),
            ("head", {"sha": HEAD, "repo": {"full_name": "fork/repo"}}),
            ("head", {"sha": HEAD, "repo": None}),
            ("labels", [{"name": "needs-human"}]),
            ("labels", [{"name": "devflow:train"}]),
        ]:
            with self.subTest(key=key, value=value):
                case = Case()
                case.pr[key] = value
                self.assertEqual(da.train_labels(case.pr), [])


class ModeTests(unittest.TestCase):
    WORKFLOW = CODEOWNERS.parent / "workflows" / "devflow-approval.yml"

    def test_observe_mode_takes_no_action(self):
        # main() returns before any gh write in observe mode.
        calls = []
        real = da.gh

        def fake(*args, stdin=None):
            calls.append(args)
            if args[:2] == ("api", "repos/org/repo/pulls/1"):
                return json.dumps(Case().pr)
            if "--paginate" in args:
                path = args[2]
                if path.endswith("/reviews?per_page=100"):
                    return json.dumps(review())
                return json.dumps(marker())
            return "write\n"

        da.gh = fake
        try:
            self.assertEqual(da.main(["--repo", REPO, "--pr", "1", "--mode", "observe"]), 0)
        finally:
            da.gh = real
        writes = [c for c in calls if "-X" in c]
        self.assertEqual(writes, [])

    def test_the_workflow_runs_the_devflow_job_in_both_modes(self):
        text = self.WORKFLOW.read_text()
        self.assertIn("(vars.APPROVAL_MODE == 'devflow' || vars.APPROVAL_MODE == 'observe')", text)
        self.assertIn('--mode "$APPROVAL_MODE"', text)
        # The admin rule keeps approving until the mode is devflow.
        self.assertIn("vars.APPROVAL_MODE != 'devflow' &&", text)


def codeowners_regex(pattern: str):
    """GitHub's CODEOWNERS matching (gitignore rules without ! and [ ]): enough to
    check which line owns a path. Case-sensitive, like GitHub."""
    anchored = "/" in pattern.rstrip("/")
    directory = pattern.endswith("/")
    body, out, i = pattern.strip("/"), [], 0
    while i < len(body):
        if body.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif body[i] == "*":
            out.append("[^/]*")
            i += 1
        else:
            out.append(re.escape(body[i]))
            i += 1
    tail = "/.*" if directory else "(?:/.*)?"
    return re.compile(("" if anchored else "(?:.*/)?") + "".join(out) + tail)


def owners(path: str) -> list:
    """Owners on the last CODEOWNERS line that matches path."""
    found = []
    for line in CODEOWNERS.read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            pattern, *who = line.split()
            if codeowners_regex(pattern).fullmatch(path):
                found = who
    return found


class CodeownersFloorTests(unittest.TestCase):
    def test_every_floor_path_is_owned_by_the_team(self):
        # The paths the removed approval-floor.json sent to a person.
        for path in (
            ".github/workflows/devflow-approval.yml",
            "futureagi/accounts/views/login.py",
            "futureagi/accounts/migrations/0052_x.py",
            "futureagi/saml2_auth/views.py",
            "futureagi/tfc/permissions/rbac.py",
            "futureagi/tfc/middleware/jwt_auth.py",
            "futureagi/tfc/ee_gating.py",
            "futureagi/ee/usage/deployment.py",
            "futureagi/ee/licensing/keys.py",
            "fi-collector/pkg/pricing/table.go",
            "fi-collector/pkg/auth/auth.go",
            "agentcc-gateway/internal/plugins/credits/credits.go",
            "agentcc-gateway/internal/redisstate/credits.go",
            "frontend/src/auth/guard/auth-guard.jsx",
            "frontend/src/pages/dashboard/settings/BillingPage.jsx",
            "frontend/src/pages/dashboard/settings/Pricing/PricingV2.jsx",
            "frontend/src/pages/mcp/OAuthConsent.jsx",
            "agentcc-gateway/scripts/merge_pricing.py",
            "billing.py",
            "futureagi/tfc/templates/billing/invoice.html",
            "LICENSE-EE",
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
            "futureagi/payments/stripe_client.py",
        ):
            with self.subTest(path=path):
                self.assertEqual(owners(path), [FLOOR_TEAM])

    def test_ordinary_paths_and_migrations_have_no_owner(self):
        for path in (
            "futureagi/tracer/views/span.py",
            "futureagi/tracer/migrations/0120_add_column.py",
            "futureagi/accounts_v2/a.py",
            "x/.github/a.yml",
        ):
            with self.subTest(path=path):
                self.assertEqual(owners(path), [])

    def test_the_specific_lines_come_last_and_keep_their_owners(self):
        lint = ["@nik13", "@atharva-bhange", "@hadarishav"]
        for path in (
            ".github/CODEOWNERS",
            ".github/workflows/python-lint.yml",
            "futureagi/pyproject.toml",
            "scripts/check_python_lint_delta.py",
        ):
            with self.subTest(path=path):
                self.assertEqual(owners(path), lint)

    def test_the_matcher_follows_codeowners_rules(self):
        for pattern, path, expected in [
            ("/.github/", ".github/workflows/a.yml", True),
            ("/.github/", "x/.github/a.yml", False),
            ("/futureagi/accounts/", "futureagi/accounts_v2/a.py", False),
            ("*billing*", "a/billing/b.py", True),
            ("*billing*", "a/Billing.jsx", False),  # case-sensitive
            ("/futureagi/**/*api_key*", "futureagi/a/b/api_key.py", True),
            ("/futureagi/tfc/ee_gating.py", "futureagi/tfc/ee_gatingXpy", False),
        ]:
            with self.subTest(pattern=pattern, path=path):
                self.assertEqual(bool(codeowners_regex(pattern).fullmatch(path)), expected)


if __name__ == "__main__":
    unittest.main()
