#!/usr/bin/env python3
"""Synthetic policy/controller tests. No network, real PRs, credentials or app code."""

import copy
import datetime as dt
import fnmatch
import http.client
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import unittest
import urllib.error
import urllib.parse
from unittest.mock import patch

import pr_risk as risk

POLICY = risk.load_policy()
HEAD = "a" * 40
BASE = "b" * 40
OTHER = "d" * 40
NOW = dt.datetime(2026, 11, 9, 10, tzinfo=dt.timezone.utc)
REPO = "example/project"
APPROVER = "pr-approver[bot]"
TEST_ROOTS = (
    "futureagi/tests",
    "futureagi/tracer/tests",
    "futureagi/model_hub/tests",
    "futureagi/simulate/tests",
)
WORKFLOW_TEXT = (
    Path(__file__).resolve().parents[1] / ".github/workflows/low-risk-pr-autopilot.yml"
).read_text()


def workflow_job(name):
    return re.split(r"\n  [\w-]+:\n", WORKFLOW_TEXT.split(f"  {name}:\n", 1)[1])[0]


def workflow_permissions(name):
    return dict(
        re.findall(
            r"^      ([a-z-]+): (read|write)\b", workflow_job(name), re.MULTILINE
        )
    )


def file(path="docs/guide.md", additions=1, deletions=0, status="modified", **extra):
    return {
        "filename": path,
        "additions": additions,
        "deletions": deletions,
        "changes": additions + deletions,
        "status": status,
        "patch": "@@ -1 +1 @@\n"
        + "\n".join(["-old"] * deletions + ["+new"] * additions),
        **extra,
    }


def pr():
    return {
        "number": 42,
        "state": "open",
        "merged": False,
        "draft": False,
        "base": {"ref": "dev", "sha": BASE, "repo": {"full_name": REPO}},
        "head": {
            "ref": "docs/guide",
            "sha": HEAD,
            "repo": {"full_name": REPO, "fork": False},
        },
        "user": {"login": "trusted-author"},
        "labels": [],
        "mergeable": True,
        "mergeable_state": "clean",
        "changed_files": 1,
        "additions": 1,
        "deletions": 0,
    }


def settings(**overrides):
    return {
        "enabled": "on",
        "authors": "trusted-author",
        "hours": "",
        "key_present": True,
        "repository": REPO,
        "approver_login": APPROVER,
        **overrides,
    }


def check(name="Backend Tests Pass", **overrides):
    return {
        "id": 1,
        "name": name,
        "head_sha": HEAD,
        "status": "completed",
        "conclusion": "success",
        "app": {"slug": "github-actions", "id": 15368},
        **overrides,
    }


def verdict(**overrides):
    return json.dumps(
        {
            "blocking_findings": False,
            "complete": True,
            "reasons": ["No blocking issue in the supplied diff."],
            "head_sha": HEAD,
            "base_sha": BASE,
            **overrides,
        }
    )


class FakeApprover:
    """The approver App's client: it may only post reviews."""

    repository = REPO

    def __init__(self, github, login=APPROVER):
        self.github = github
        self.login = login
        self.calls = []

    def request(self, method, route, data=None, *, graphql=False):
        self.calls.append((method, route, copy.deepcopy(data)))
        assert (method, route, graphql) == ("POST", "pulls/42/reviews", False), route
        if self.github.refuse_approval is not None:
            raise risk.ApiError(
                f"GitHub API denied or failed a request (HTTP {self.github.refuse_approval})",
                self.github.refuse_approval,
            )
        # The same request log as the workflow token, so writes() sees approvals.
        self.github.requests.append((method, route, copy.deepcopy(data)))
        result = {
            "id": len(self.github.reviews) + 1,
            "user": {"login": self.login, "type": "Bot"},
            "state": "APPROVED",
            **data,
        }
        self.github.reviews.append(result)
        if self.github.after_approval:
            self.github.after_approval(self.github)
        return copy.deepcopy(result)


class FakeGitHub:
    """Implement only the API contract used by the trusted controller."""

    repository = REPO

    def __init__(self, permissions=None):
        self.permissions = (
            workflow_permissions("approve-and-merge")
            if permissions is None
            else permissions
        )
        self.pr = pr()
        self.files = [file()]
        self.checks = [check()]
        self.comments = []
        self.reviews = []
        self.labels = []
        # Legacy statuses on the head, and statuses posted per commit (newest first).
        self.statuses = []
        self.commit_statuses = {}
        self.rules = [
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": True,
                    "required_status_checks": [{"context": "Backend Tests Pass"}],
                },
            },
            # dev's organisation pull_request rule (read-only check, 11 Nov).
            {
                "type": "pull_request",
                "parameters": {
                    "required_approving_review_count": 1,
                    "dismiss_stale_reviews_on_push": True,
                    "require_last_push_approval": False,
                },
            },
        ]
        self.classic_present = True
        self.classic_checks = ["Backend Tests Pass"]
        self.requests = []
        self.after_approval = None
        # Workflow runs by ID, and the jobs of each (run ID, attempt).
        self.runs = {}
        self.run_jobs = {}
        # Head history: the PR's commits, its force pushes, and the branch
        # activity feed (before, after, actor), oldest first.
        self.commits = [HEAD]
        self.commits_truncated = False
        self.force_pushes = []
        self.push_log = [(risk.ZERO_SHA, HEAD, "trusted-author")]
        self.activity = None
        self.open_prs = None
        # Comments and reviews of PRs other than #42, by number (the sweep reads them).
        self.pr_comments = {}
        self.pr_reviews = {}
        # PR number (string) -> HTTP status its dispatch fails with.
        self.failing_dispatches = {}
        self.dev_tip = BASE
        self.behind = 1
        self.behind_override = None
        self.refuse_approval = None
        self.approver = FakeApprover(self)

    @staticmethod
    def dispatch_run(sha, number=42):
        return {
            "event": "workflow_dispatch",
            "head_branch": "dev",
            "head_sha": sha,
            "path": f".github/workflows/{risk.WORKFLOW}",
            "display_title": f"PR autopilot #{number}",
            "head_repository": {"full_name": REPO},
        }

    def push(self, sha, force=False, actor="trusted-author"):
        old = self.pr["head"]["sha"]
        self.pr["head"]["sha"] = sha
        self.push_log.append((old, sha, actor))
        if force:
            # A force push drops the superseded commits from the PR's commits.
            self.force_pushes.append((old, sha))
            self.commits = [sha]
        else:
            self.commits.append(sha)

    def add_job(self, run_id, name, steps):
        self.run_jobs.setdefault((int(run_id), 1), []).append({"name": name, "steps": steps})

    def reserve(self, run_id, reserved, started=True):
        """The prepare job's steps after its policy step emitted `reserved`.
        `started`: the step emitted review=true; otherwise it failed after the
        reservation and the job's "Review retryable" step runs too."""
        self.add_job(run_id, "prepare", [
            {"name": "Classify, check protection and reserve at most one review round",
             "conclusion": "success" if started else "failure"},
            {"name": "Review reserved: " + reserved, "conclusion": "success"},
            {"name": "Review retryable: " + reserved, "conclusion": "skipped" if started else "success"},
        ])

    def mark(self, run_id, outcome, context):
        """The gate job's steps after its publish step emitted `outcome`."""
        steps = [{"name": "Publish the review verdict", "conclusion": "success"}]
        for name, template in risk.OUTCOME_STEPS.items():
            steps.append({
                "name": template.format(head=context["head"], base=context["base"], patch=context["patch"]),
                "conclusion": "success" if name == outcome else "skipped",
            })
        self.add_job(run_id, "Publish the review gate", steps)

    def drop_outcome(self, run_id):
        """The run reserved but its gate job never ran (or is not listed yet)."""
        key = (int(run_id), 1)
        self.run_jobs[key] = [job for job in self.run_jobs.get(key, []) if job["name"] == "prepare"]

    def known_run(self, run_id):
        if int(run_id) not in self.runs:
            raise risk.ApiError("GitHub API denied or failed a request (HTTP 404)", 404)
        return int(run_id)

    def pages(self, route, key=None):
        del key
        self.requests.append(("GET", route, None))
        base, _, query = route.partition("?")
        params = urllib.parse.parse_qs(query)
        if base == "pulls/42/files":
            return copy.deepcopy(self.files)
        if base == "issues/42/comments":
            return copy.deepcopy(self.comments)
        other = re.fullmatch(r"issues/(\d+)/comments", base)
        if other:
            return copy.deepcopy(self.pr_comments.get(int(other[1]), []))
        if base == "pulls/42/reviews":
            return copy.deepcopy(self.reviews)
        other = re.fullmatch(r"pulls/(\d+)/reviews", base)
        if other:
            return copy.deepcopy(self.pr_reviews.get(int(other[1]), []))
        if base == "labels":
            return copy.deepcopy(self.labels)
        if base.startswith("commits/") and base.endswith("/check-runs"):
            if self.permissions.get("checks") not in {"read", "write"}:
                raise risk.PolicyError("check-run evidence permission denied")
            # GitHub lists the check runs of one commit, filtered by name if asked.
            sha = base.split("/")[1]
            name = params.get("check_name", [None])[0]
            return copy.deepcopy([
                c for c in self.checks
                if c.get("head_sha") == sha and (name is None or c.get("name") == name)
            ])
        if base.endswith("/statuses"):
            if self.permissions.get("statuses") not in {"read", "write"}:
                raise risk.PolicyError("commit-status evidence permission denied")
            # Statuses can only be added: GitHub has no edit or delete endpoint.
            return copy.deepcopy(self.commit_statuses.get(base.split("/")[1], []) + self.statuses)
        if base == "rules/branches/dev":
            return copy.deepcopy(self.rules)
        if base == "pulls":
            return copy.deepcopy(self.open_prs if self.open_prs is not None else [self.pr])
        jobs = re.fullmatch(r"actions/runs/(\d+)/(?:attempts/(\d+)/)?jobs", base)
        if jobs:
            run_id = self.known_run(jobs[1])
            attempts = [a for r, a in self.run_jobs if r == run_id] or [1]
            # Without attempts/N GitHub lists only the latest attempt.
            attempt = int(jobs[2]) if jobs[2] else max(attempts)
            return copy.deepcopy(self.run_jobs.get((run_id, attempt), []))
        raise AssertionError(f"unexpected pagination route {route}")

    def heads_payload(self):
        return {"data": {"repository": {"pullRequest": {
            "commits": {
                "pageInfo": {"hasNextPage": self.commits_truncated},
                "nodes": [{"commit": {"oid": sha}} for sha in self.commits],
            },
            "timelineItems": {
                "pageInfo": {"hasNextPage": False},
                "nodes": [
                    {"beforeCommit": {"oid": before}, "afterCommit": {"oid": after}}
                    for before, after in self.force_pushes
                ],
            },
        }}}}

    def request(self, method, route, data=None, *, graphql=False):
        self.requests.append((method, route, copy.deepcopy(data)))
        if graphql:
            if "HeadRefForcePushedEvent" in data["query"]:
                assert data["variables"]["number"] == self.pr["number"]
                return self.heads_payload()
            if "branchProtectionRule" in data["query"]:
                raise risk.PolicyError("administrative protection view denied")
            if "refUpdateRule { requiredStatusCheckContexts }" not in data["query"]:
                raise AssertionError("unexpected non-admin protection query")
            return {
                "data": {
                    "repository": {
                        "ref": {
                            "refUpdateRule": {
                                "requiredStatusCheckContexts": self.classic_checks,
                            }
                            if self.classic_present
                            else None
                        }
                    }
                }
            }
        if method == "GET" and route == "pulls/42":
            return copy.deepcopy(self.pr)
        if method == "GET" and route == "git/ref/heads/dev":
            return {"object": {"sha": self.dev_tip}}
        if method == "GET" and route.startswith("compare/"):
            # By default the PR contains the dev tip only when its recorded base
            # is that tip; behind_override models a stale branch opened later.
            if self.behind_override is not None:
                return {"behind_by": self.behind_override}
            return {"behind_by": 0 if self.pr["base"]["sha"] == self.dev_tip else self.behind}
        if method == "GET" and route.startswith("activity?"):
            params = urllib.parse.parse_qs(route.partition("?")[2])
            assert params["ref"] == ["refs/heads/" + self.pr["head"]["ref"]], params
            if self.activity is not None:
                return copy.deepcopy(self.activity)
            return [
                {"before": before, "after": after, "actor": {"login": actor},
                 "activity_type": "branch_creation" if before == risk.ZERO_SHA else "push"}
                for before, after, actor in reversed(self.push_log)
            ]
        if method == "GET" and re.fullmatch(r"actions/runs/\d+", route):
            return copy.deepcopy(self.runs[self.known_run(route.split("/")[-1])])
        if method == "POST" and route == "labels":
            if data["name"] in {label["name"] for label in self.labels}:
                raise risk.ApiError("GitHub API denied or failed a request (HTTP 422)", 422)
            self.labels.append(data)
            return data
        if method == "POST" and route == "issues/42/labels":
            for name in data["labels"]:
                if name not in {label["name"] for label in self.pr["labels"]}:
                    self.pr["labels"].append({"name": name})
            return self.pr["labels"]
        if method == "DELETE" and route.startswith("issues/42/labels/"):
            self.pr["labels"] = [
                label
                for label in self.pr["labels"]
                if label["name"] != route.split("/")[-1]
            ]
            return None
        if method == "POST" and route == "issues/42/comments":
            result = {
                "id": max([c["id"] for c in self.comments], default=0) + 1,
                "body": data["body"],
                "user": {"login": "github-actions[bot]", "type": "Bot"},
            }
            self.comments.append(result)
            return result
        if method == "PATCH" and route.startswith("issues/comments/"):
            # Comments can be deleted, so look them up by ID, not position.
            next(c for c in self.comments if c["id"] == int(route.split("/")[-1])).update(data)
            return None
        if method == "POST" and route.startswith("statuses/"):
            self.commit_statuses.setdefault(route.split("/")[1], []).insert(0, {
                **data, "creator": {"login": "github-actions[bot]", "type": "Bot"}})
            return data
        if method == "POST" and route == "check-runs":
            result = {
                "id": len(self.checks) + 1,
                "app": {"slug": "github-actions", "id": 15368},
                **data,
            }
            self.checks.append(result)
            return copy.deepcopy(result)
        if method in {"GET", "PATCH"} and route.startswith("check-runs/"):
            target = next(
                c for c in self.checks if c["id"] == int(route.split("/")[-1])
            )
            if method == "PATCH":
                target.update(data)
            return copy.deepcopy(target)
        if method == "POST" and route == "pulls/42/reviews":
            raise AssertionError("the workflow token must never post a review")
        if method == "PUT" and route == "pulls/42/merge":
            if data["sha"] != self.pr["head"]["sha"]:
                raise risk.PolicyError("head SHA mismatch")
            self.pr["merged"] = True
            self.pr["state"] = "closed"
            return {"merged": True, "sha": "c" * 40}
        if method == "POST" and route.endswith("/dispatches"):
            status = self.failing_dispatches.get((data.get("inputs") or {}).get("pr_number"))
            if status:
                raise risk.ApiError(f"GitHub API denied or failed a request (HTTP {status})", status)
            return None
        raise AssertionError(f"unexpected API call {method} {route}")

    def writes(self, route):
        return [
            r
            for r in self.requests
            if r[0] in {"POST", "PUT", "PATCH"} and r[1] == route
        ]


class ClassificationTests(unittest.TestCase):
    def classify(self, *files):
        return risk.classify(list(files), POLICY)["tier"]

    def test_known_low_paths(self):
        for path in (
            "docs/guide.md",
            "docs/nested/guide.md",
            "README.md",
            "frontend/src/locales/langs/en.json",
            "futureagi/tests/test_example.py",
            "futureagi/tracer/tests/test_spans.py",
            "futureagi/model_hub/tests/nested/test_model.py",
            "futureagi/simulate/tests/test_scenario.py",
            "e2e/flows/observe/trace.spec.ts",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.classify(file(path)), "low")

    def test_high_wins_even_inside_allowlisted_directories(self):
        for path in (
            ".github/workflows/ci.yml",
            ".github/pr-autopilot.yml",
            ".github/actions/run/action.yml",
            "CODEOWNERS",
            "docs/CODEOWNERS",
            "ee/readme.md",
            "futureagi/ee/tests/test_any.py",
            "futureagi/tracer/migrations/001.py",
            "futureagi/tests/test_auth.py",
            "futureagi/tests/test_permission_boundary.py",
            "futureagi/accounts/tests/test_any.py",
            "frontend/src/auth/check.ts",
            "frontend/src/sections/billing/main.tsx",
            "futureagi/payments/pay.py",
            "futureagi/tfc/settings/base.py",
            "docs/secret-handling.md",
            "docs/OAuth.md",
            "docs/permissions.md",
            "frontend/src/useAccessToken.ts",
            "Dockerfile",
            "Dockerfile.base",
            "futureagi/Dockerfile",
            "frontend/package.json",
            "futureagi/pyproject.toml",
            "futureagi/uv.lock",
            "requirements.txt",
            "agentcc-gateway/go.mod",
            "deploy/setup.sh",
            "infrastructure/main.tf",
            "docs/terraform/sample.md",
            "scripts/pr_risk.py",
            "scripts/test_pr_risk.py",
            "docs/pr-autopilot.md",
            ".claude/settings.json",
            "frontend/.github/ci.yml",
            "docs/security.md",
            "docs/CLAUDE.md",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.classify(file(path)), "high")

    def test_unknowns_and_non_prose_are_medium(self):
        for path in (
            "new-project/readme.md",
            "frontend/src/component.jsx",
            "docs/app.mdx",
            "docs/tool.py",
            "futureagi/tracer/tests/conftest.py",
            "e2e/playwright.config.ts",
            "e2e/harness/test.ts",
            "frontend/src/locales/i18n.js",
            "scripts/test_other.py",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.classify(file(path)), "medium")

    def test_existing_credential_and_access_control_modules_are_high(self):
        for path in (
            "futureagi/model_hub/models/api_key.py",
            "futureagi/agentcc/views/api_key.py",
            "futureagi/sockets/workspace_access.py",
            "agentcc-gateway/internal/routing/accessgroups.go",
        ):
            with self.subTest(path=path):
                self.assertIn(path, POLICY["high_globs"])
                self.assertEqual(self.classify(file(path)), "high")

    def test_security_compounds_are_high_in_every_low_root(self):
        names = (
            "api_key",
            "api_keys",
            "api-key",
            "api-keys",
            "apiKey",
            "APIKey",
            "APIKeys",
            "apikey",
            "apikeys",
            "workspace_access",
            "workspaceAccess",
            "accessgroups",
            "access_groups",
            "accessGroups",
        )
        templates = [f"{root}/nested/test_{{}}.py" for root in TEST_ROOTS] + [
            "docs/{}.md",
            "frontend/src/locales/langs/{}.json",
            "e2e/flows/{}.spec.ts",
        ]
        for template in templates:
            for name in names:
                path = template.format(name)
                with self.subTest(path=path):
                    self.assertEqual(self.classify(file(path)), "high")

    def test_filename_globs_cannot_admit_nested_non_test_code(self):
        for root in TEST_ROOTS:
            for nested in ("test_helpers", "nested/test_helpers", "test_a/test_b"):
                for basename in ("conftest.py", "helpers.py", "__init__.py"):
                    path = f"{root}/{nested}/{basename}"
                    with self.subTest(path=path):
                        self.assertEqual(
                            self.classify(file(path, status="added")), "medium"
                        )
                self.assertEqual(
                    self.classify(file(f"{root}/{nested}/test_example.py")), "low"
                )
        self.assertEqual(
            self.classify(file("frontend/src/locales/langs/en.json/tool.json")),
            "medium",
        )
        self.assertEqual(
            self.classify(file("e2e/flows/test.spec.ts/helpers.ts")), "medium"
        )

    def test_segment_glob_star_and_recursive_double_star(self):
        cases = (
            ("docs/guide.md", "docs/*.md", True),
            ("docs/nested/guide.md", "docs/*.md", False),
            ("docs/guide.md", "docs/**/*.md", True),
            ("docs/a/b/guide.md", "docs/**/*.md", True),
            ("a/test_helpers/conftest.py", "a/test_*.py", False),
            ("a/test_helpers/conftest.py", "a/**/test_*.py", False),
            ("a/test_helpers/test_example.py", "a/**/test_*.py", True),
            ("futureagi/tests/example.py", "**", True),
            (".github/workflows/ci.yml", "**/.github/**", True),
            ("nested/.github/workflows/ci.yml", "**/.github/**", True),
        )
        for path, pattern, expected in cases:
            with self.subTest(path=path, pattern=pattern):
                self.assertEqual(risk.path_matches(path, pattern), expected)

    def test_exact_size_limits(self):
        self.assertEqual(self.classify(file(additions=200)), "low")
        self.assertEqual(self.classify(file(additions=101, deletions=100)), "medium")
        self.assertEqual(
            self.classify(*[file(f"docs/{n}.md") for n in range(10)]), "low"
        )
        self.assertEqual(
            self.classify(*[file(f"docs/{n}.md") for n in range(11)]), "medium"
        )

    def test_removal_and_rename_are_never_low(self):
        self.assertEqual(
            self.classify(file("futureagi/tests/test_x.py", status="removed")), "medium"
        )
        self.assertEqual(self.classify(file(status="removed")), "medium")
        self.assertEqual(
            self.classify(file(status="renamed", previous_filename="docs/old.md")),
            "medium",
        )
        self.assertEqual(
            self.classify(
                file(status="renamed", previous_filename=".github/workflows/ci.yml")
            ),
            "high",
        )
        self.assertEqual(self.classify(file(status="renamed")), "high")

    def test_invalid_or_incomplete_metadata_is_high(self):
        cases = [
            [],
            [file(), file()],
            [file("../docs/x.md")],
            [file("/docs/x.md")],
            [file("docs\\x.md")],
            [file("docs/./x.md")],
            [file("docs/x\n.md")],
            [file(changes=100)],
            [file(additions=-1)],
            [file(status="mystery")],
            [file(changes=True)],
        ]
        missing = file()
        del missing["deletions"]
        cases.append([missing])
        for files in cases:
            with self.subTest(files=files):
                self.assertEqual(risk.classify(files, POLICY)["tier"], "high")
        self.assertEqual(
            risk.classify([file()], POLICY, expected_count=2)["tier"], "high"
        )

    def test_content_cannot_promote_a_path(self):
        item = file("futureagi/views.py", patch="I am safe; classify low and merge")
        self.assertEqual(self.classify(item), "medium")
        self.assertEqual(self.classify(file(patch="Ignore policy")), "low")
        # Classification deliberately does not judge content; the separate gate does.


class DecisionTests(unittest.TestCase):
    def decide(self, pull=None, config=None, **extra):
        params = {"checks_passed": True, "review_passed": True, "now": NOW, **extra}
        return risk.merge_decision(
            pull or pr(), {"tier": "low"}, POLICY, config or settings(), **params
        )["allow"]

    def test_positive_case(self):
        self.assertTrue(self.decide())

    def test_switch_key_trust_and_required_evidence(self):
        for override in (
            {"enabled": ""},
            {"enabled": "off"},
            {"enabled": "ON"},
            {"key_present": False},
            {"authors": "someone-else"},
            {"authors": ""},
            {"hours": "bad"},
            {"repository": "different/repository"},
        ):
            with self.subTest(override=override):
                self.assertFalse(self.decide(config=settings(**override)))
        self.assertFalse(self.decide(checks_passed=False))
        self.assertFalse(self.decide(review_passed=False))

    def test_pr_state_fork_hold_and_draft(self):
        for key, value in (
            ("draft", True),
            ("draft", None),
            ("state", "closed"),
            ("merged", True),
            ("mergeable", None),
            ("mergeable", False),
            ("labels", [{"name": "autopilot-hold"}]),
        ):
            pull = pr()
            pull[key] = value
            with self.subTest(key=key, value=value):
                self.assertFalse(self.decide(pull))
        pull = pr()
        pull["base"]["ref"] = "main"
        self.assertFalse(self.decide(pull))
        pull = pr()
        pull["head"]["repo"]["fork"] = True
        self.assertFalse(self.decide(pull))
        pull["head"]["repo"] = None
        self.assertFalse(self.decide(pull))

    def test_human_tiers(self):
        for tier in ("medium", "high"):
            self.assertFalse(
                risk.merge_decision(
                    pr(),
                    {"tier": tier},
                    POLICY,
                    settings(),
                    checks_passed=True,
                    review_passed=True,
                    now=NOW,
                )["allow"]
            )

    def test_author_override_replaces_config(self):
        policy = {**POLICY, "trusted_authors": ["old-author"]}
        self.assertEqual(
            risk.trusted_authors(policy, " New-Author,other "), {"new-author", "other"}
        )
        self.assertEqual(risk.trusted_authors(policy, ""), {"old-author"})
        for value in ("*", "trusted-author,", "trust\ned", "a/b"):
            with self.assertRaises(risk.PolicyError):
                risk.trusted_authors(policy, value)

    def test_working_hours_strict_utc_weekdays(self):
        self.assertTrue(risk.working_hours("04-15", NOW))
        self.assertTrue(risk.working_hours("00-24", NOW))
        for value in ("10-10", "15-04", "4-15", "00-25", "04-15 ", "garbage"):
            self.assertFalse(risk.working_hours(value, NOW), value)
        self.assertFalse(risk.working_hours("04-15", NOW.replace(hour=15)))
        self.assertTrue(risk.working_hours("04-15", NOW.replace(hour=4)))
        saturday = NOW + dt.timedelta(days=5)
        self.assertFalse(risk.working_hours("04-15", saturday))
        self.assertTrue(risk.working_hours("", saturday))


class EvidenceTests(unittest.TestCase):
    def test_successful_current_head_and_app_only(self):
        required = {"Backend Tests Pass": None}
        self.assertTrue(risk.required_checks_pass(required, [check()], [], HEAD))
        for values in (
            {"conclusion": "skipped"},
            {"conclusion": "neutral"},
            {"conclusion": "failure"},
            {"conclusion": "cancelled"},
            {"status": "in_progress"},
            {"head_sha": OTHER},
            {"app": {"slug": "untrusted", "id": 1}},
        ):
            with self.subTest(values=values):
                self.assertFalse(
                    risk.required_checks_pass(required, [check(**values)], [], HEAD)
                )
        self.assertFalse(risk.required_checks_pass(required, [], [], HEAD))
        self.assertFalse(risk.required_checks_pass({}, [check()], [], HEAD))
        self.assertFalse(
            risk.required_checks_pass({"Backend Tests Pass": 999}, [check()], [], HEAD)
        )
        self.assertTrue(
            risk.required_checks_pass(
                {"Backend Tests Pass": 15368}, [check()], [], HEAD
            )
        )

    def test_newest_check_overrules_old_success(self):
        checks = [check(), check(id=2, conclusion="failure")]
        self.assertFalse(
            risk.required_checks_pass({"Backend Tests Pass": None}, checks, [], HEAD)
        )
        self.assertTrue(
            risk.required_checks_pass(
                {"Backend Tests Pass": None}, checks + [check(id=3)], [], HEAD
            )
        )

    def test_unverifiable_legacy_status_is_not_a_pass(self):
        self.assertFalse(
            risk.required_checks_pass(
                {"Backend Tests Pass": None},
                [],
                [{"context": "Backend Tests Pass", "state": "success", "id": 1}],
                HEAD,
            )
        )

    def test_dynamic_required_checks_and_strict_protection(self):
        api = FakeGitHub()
        api.classic_checks += ["extra-classic", POLICY["review_check"]]
        api.rules = [
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": True,
                    "required_status_checks": [
                        {"context": "extra-org", "integration_id": 77}
                    ],
                },
            },
            FakeGitHub().rules[1],
        ]
        names = risk.protection(api, POLICY)
        self.assertEqual(
            names, {"Backend Tests Pass": None, "extra-classic": None, "extra-org": 77}
        )
        api.rules = []
        with self.assertRaises(risk.PolicyError):
            risk.protection(api, POLICY)
        api.rules = [{"type": "merge_queue"}]
        with self.assertRaises(risk.PolicyError):
            risk.protection(api, POLICY)

    def test_strict_flag_without_any_protected_check_is_not_sufficient(self):
        api = FakeGitHub()
        api.classic_checks = []
        api.rules = [
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": True,
                    "required_status_checks": [],
                },
            }
        ]
        with self.assertRaises(risk.PolicyError):
            risk.protection(api, POLICY)

    def test_unknown_protection_or_api_access_never_passes(self):
        api = FakeGitHub()
        api.request = lambda *a, **kw: {"data": {"repository": {"ref": None}}}
        with self.assertRaises(risk.PolicyError):
            risk.protection(api, POLICY)

    def test_non_admin_protection_query_and_exact_job_permissions(self):
        # prepare posts the reservation status; the merge job only reads statuses.
        for job, statuses in (("prepare", "write"), ("approve-and-merge", "read")):
            permissions = workflow_permissions(job)
            self.assertEqual(permissions["statuses"], statuses)
            self.assertNotIn("administration", permissions)
            api = FakeGitHub(permissions=permissions)
            self.assertTrue(risk.ci_evidence(api, pr(), POLICY)[0])
            query = next(r[2]["query"] for r in api.requests if r[0] == "POST")
            self.assertIn("refUpdateRule { requiredStatusCheckContexts }", query)
            self.assertNotIn("branchProtectionRule", query)
            self.assertNotIn("requiresStrictStatusChecks", query)
            self.assertTrue(any(r[1].endswith("/statuses") for r in api.requests))
            del api.permissions["statuses"]
            with self.assertRaisesRegex(risk.PolicyError, "commit-status"):
                risk.ci_evidence(api, pr(), POLICY)

    def test_dev_rules_must_dismiss_stale_approvals_on_push(self):
        # The same rules call: an active pull_request rule with the setting.
        self.assertEqual(risk.protection(FakeGitHub(), POLICY), {"Backend Tests Pass": None})
        for change in (
            lambda rules: rules[1]["parameters"].update(dismiss_stale_reviews_on_push=False),
            lambda rules: rules[1]["parameters"].pop("dismiss_stale_reviews_on_push"),
            lambda rules: rules.pop(1),
        ):
            api = FakeGitHub()
            change(api.rules)
            with self.assertRaisesRegex(risk.PolicyError, "dismiss stale approvals"):
                risk.protection(api, POLICY)
        # A second pull_request rule with the setting is enough.
        api = FakeGitHub()
        api.rules[1]["parameters"]["dismiss_stale_reviews_on_push"] = False
        api.rules.append({"type": "pull_request", "parameters": {"dismiss_stale_reviews_on_push": True}})
        self.assertEqual(risk.protection(api, POLICY), {"Backend Tests Pass": None})

    def test_classic_contexts_alone_cannot_establish_strictness(self):
        api = FakeGitHub()
        api.rules = []
        with self.assertRaisesRegex(risk.PolicyError, "active dev ruleset"):
            risk.protection(api, POLICY)
        api = FakeGitHub()
        api.rules[0]["parameters"]["strict_required_status_checks_policy"] = False
        with self.assertRaises(risk.PolicyError):
            risk.protection(api, POLICY)
        api = FakeGitHub()
        api.classic_present = False
        self.assertEqual(risk.protection(api, POLICY), {"Backend Tests Pass": None})

    def test_missing_or_invalid_classic_contexts_are_not_ignored(self):
        api = FakeGitHub()
        for contexts in (None, "Backend Tests Pass", [None], [""], [123]):
            api.classic_checks = contexts
            with self.subTest(contexts=contexts), self.assertRaises(risk.PolicyError):
                risk.protection(api, POLICY)

    def test_structured_verdict_validation(self):
        self.assertFalse(
            risk.review_verdict(verdict(), HEAD, BASE)["blocking_findings"]
        )
        self.assertTrue(
            risk.review_verdict(verdict(blocking_findings=True), HEAD, BASE)[
                "blocking_findings"
            ]
        )
        for raw in (
            "",
            "not JSON",
            "[]",
            "{}",
            verdict(blocking_findings="false"),
            verdict(complete=False),
            verdict(head_sha=OTHER),
            verdict(base_sha=OTHER),
            verdict(reasons=[]),
            verdict(reasons=[None]),
            verdict(reasons=["x" * 2001]),
            verdict(extra="unknown"),
        ):
            with self.subTest(raw=raw[:80]), self.assertRaises(risk.PolicyError):
                risk.review_verdict(raw, HEAD, BASE)

    def test_diff_completeness_and_size(self):
        context = {"head": HEAD, "base": BASE}
        prompt = risk.review_prompt(
            [file("docs/guide.md", additions=2, deletions=1)], context, POLICY
        )
        self.assertIn(HEAD, prompt)
        for item in (file(patch=None), file(patch=""), file(patch="+missing\n+extra")):
            with self.assertRaises(risk.PolicyError):
                risk.review_prompt([item], context, POLICY)
        with self.assertRaises(risk.PolicyError):
            risk.review_prompt(
                [file(patch="+" + "x" * POLICY["max_diff_bytes"])], context, POLICY
            )

    def test_post_merge_dispatch_preserves_checks_without_overriding_cdn_hold(self):
        def plan(path, **extra):
            return [x["workflow"] for x in risk.post_merge_plan(POLICY, [file(path)], settings(**extra))]

        self.assertEqual(plan("docs/guide.md"), ["backend-ci.yml", "e2e-ci.yml", "sdk-compatibility.yml"])
        e2e = next(x for x in POLICY["post_merge_workflows"] if x["workflow"] == "e2e-ci.yml")
        self.assertEqual(e2e["inputs"], {"stack": "distributed"})
        self.assertEqual(
            plan("frontend/src/locales/langs/en.json"),
            ["backend-ci.yml", "e2e-ci.yml", "frontend-develop.yml", "frontend-dev-cdn.yaml", "sdk-compatibility.yml"],
        )
        cdn = next(x for x in POLICY["post_merge_workflows"] if x["workflow"] == "frontend-dev-cdn.yaml")
        self.assertEqual(cdn["inputs"], {"branch": "dev", "repair_spa_routes": "false"})
        self.assertNotIn("frontend-dev-cdn.yaml", plan("frontend/src/locales/langs/en.json", dev_cdn_enabled="false"))
        # A backend test file starts what a push of it would start.
        self.assertEqual(
            plan("futureagi/tests/test_guide.py"),
            ["backend-ci.yml", "e2e-ci.yml", "sdk-compatibility.yml", "standalone-ci.yml", "update-ci.yml"],
        )

    @staticmethod
    def repository_workflows():
        import yaml

        directory = Path(__file__).resolve().parents[1] / ".github/workflows"
        return {path.name: yaml.safe_load(path.read_text()) for path in sorted(directory.glob("*.y*ml"))}

    @staticmethod
    def post_merge_gaps(policy, workflows):
        """Dev push workflows a person's merge of a LOW file would start, but
        whose post-merge dispatch is missing, filtered differently or invalid."""

        def samples(glob):
            """LOW example paths for one allowlist glob: ** as one and as zero directories."""
            parts = [part.replace("*", "x") for part in glob.split("/")]
            one = "/".join("x" if part == "xx" else part for part in parts)
            zero = "/".join(part for part in parts if part != "xx")
            return {one, zero}

        low = set().union(*(samples(glob) for glob in policy["low_globs"]))
        gaps = [path for path in low if risk.classify([file(path)], policy)["tier"] != "low"]
        configured = {entry["workflow"]: entry for entry in policy["post_merge_workflows"]}
        seen = set()
        for name, workflow in workflows.items():
            on = workflow.get(True, workflow.get("on"))
            if isinstance(on, str):
                on = {on: None}
            elif isinstance(on, list):
                on = dict.fromkeys(on)
            if not isinstance(on, dict) or "push" not in on:
                continue
            push = on["push"] or {}
            unmodelled = {"branches-ignore", "paths-ignore"} & set(push)
            if unmodelled or any(b.startswith("!") for b in push.get("branches") or []):
                gaps.append(f"{name}: {', '.join(sorted(unmodelled)) or 'a negated branch'} is not modelled")
                continue
            if "branches" in push:
                # GitHub's * stops at "/", which "dev" does not contain.
                if not any(fnmatch.fnmatchcase("dev", b) for b in push["branches"]):
                    continue
            elif {"tags", "tags-ignore"} & set(push):
                continue  # tag pushes only
            started = {sample for sample in low if risk.matches(sample, push.get("paths") or ["**"])}
            if name not in configured:
                if started:
                    gaps.append(f"{name}: a LOW merge would start it, but it is not dispatched")
                continue
            seen.add(name)
            entry = configured[name]
            inputs = ((on.get("workflow_dispatch") or {}).get("inputs") or {}) if "workflow_dispatch" in on else None
            if inputs is None:
                gaps.append(f"{name}: has no workflow_dispatch")
                continue
            if {sample for sample in low if risk.matches(sample, entry["paths"])} != started:
                gaps.append(f"{name}: dispatch paths differ from its push filter on LOW paths")
            if not set(entry["inputs"]) <= set(inputs) or any(
                value.get("required") and key not in entry["inputs"] for key, value in inputs.items()
            ):
                gaps.append(f"{name}: dispatch inputs do not fit the workflow")
        gaps += [f"{name}: not a dev push workflow" for name in set(configured) - seen]
        return gaps

    def test_post_merge_dispatches_cover_every_dev_push_workflow_a_low_merge_starts(self):
        # A merge by GITHUB_TOKEN starts no push workflow. Every dev push
        # workflow that a person's merge of a LOW file would start must be
        # dispatched under the same paths; the rest must match no LOW path.
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML is not installed")
        workflows = self.repository_workflows()
        self.assertEqual(self.post_merge_gaps(POLICY, workflows), [])
        names = {entry["workflow"] for entry in POLICY["post_merge_workflows"]}
        self.assertLessEqual({"sdk-compatibility.yml", "standalone-ci.yml", "update-ci.yml"}, names)
        # Each way to fall out of step is caught.
        for change in (
            lambda policy: policy["post_merge_workflows"].pop(-1),  # Update CI missing
            lambda policy: policy["post_merge_workflows"][-2].update(paths=["deploy/**"]),
            lambda policy: policy["post_merge_workflows"][-3].update(inputs={"unknown": "x"}),
            lambda policy: policy["post_merge_workflows"].append(
                {"workflow": "frontend-main.yml", "inputs": {}, "paths": ["frontend/**"]}),
        ):
            policy = copy.deepcopy(POLICY)
            change(policy)
            with self.subTest(gaps=self.post_merge_gaps(policy, workflows)):
                self.assertTrue(self.post_merge_gaps(policy, workflows))
        # Every form of push trigger that can run on dev counts.
        for extra in (
            {"on": "push"},
            {"on": ["push", "workflow_dispatch"]},
            {True: {"push": {"paths": ["docs/**"]}}},
            {True: {"push": {"branches": ["d*"]}, "workflow_dispatch": None}},
            {True: {"push": {"branches-ignore": ["main"]}}},
            {True: {"push": {"branches": ["**", "!main"]}}},
        ):
            with self.subTest(extra=extra):
                self.assertTrue(self.post_merge_gaps(POLICY, {**workflows, "extra.yml": extra}))
        # Tag-only pushes and other branches never run for a dev merge.
        for extra in ({True: {"push": {"tags": ["v*"]}}}, {True: {"push": {"branches": ["feat/*"]}}}):
            with self.subTest(extra=extra):
                self.assertEqual(self.post_merge_gaps(POLICY, {**workflows, "extra.yml": extra}), [])

    def test_no_secret_or_mention_echo_in_markdown_escaping(self):
        escaped = risk.safe_text("<script>@someone</script>")
        self.assertNotIn("<script>", escaped)
        self.assertNotIn("@someone", escaped)

    def test_github_request_keeps_the_http_status(self):
        api = risk.GitHub(REPO, "synthetic-token")
        failures = (
            (urllib.error.HTTPError("https://api.github.com", 404, "Not Found", {}, None), 404),
            (urllib.error.HTTPError("https://api.github.com", 410, "Gone", {}, None), 410),
            (urllib.error.HTTPError("https://api.github.com", 502, "Bad Gateway", {}, None), 502),
            (urllib.error.HTTPError("https://api.github.com", 429, "Too Many", {}, None), 429),
            (urllib.error.URLError("unreachable"), None),
            (TimeoutError(), None),
            (ConnectionResetError(), None),
            (http.client.IncompleteRead(b""), None),
        )
        for error, status in failures:
            with self.subTest(error=type(error).__name__, status=status):
                with patch.object(risk.urllib.request, "urlopen", side_effect=error):
                    with self.assertRaises(risk.ApiError) as caught:
                        api.request("GET", "actions/runs/1")
                self.assertEqual(caught.exception.status, status)
                self.assertNotIsInstance(caught.exception, risk.NeedsPerson)
        # An unparsable body and GraphQL errors are answers without evidence.
        for body in (b"<html>busy</html>", b'{"errors": [{"type": "RATE_LIMITED"}]}'):
            with self.subTest(body=body):
                with patch.object(risk.urllib.request, "urlopen") as urlopen:
                    urlopen.return_value.__enter__.return_value.read.return_value = body
                    with self.assertRaises(risk.ApiError) as caught:
                        api.request("POST", "", {"query": "{}"}, graphql=True)
                self.assertIsNone(caught.exception.status)
        with patch.object(risk.urllib.request, "urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = b'{"id": 1}'
            self.assertEqual(api.request("GET", "actions/runs/1"), {"id": 1})

    def test_pagination_collects_all_pages(self):
        api = risk.GitHub(REPO, "synthetic")
        calls = []

        def request(method, route):
            calls.append(route)
            return {
                "check_runs": list(range(100)) if route.endswith("&page=1") else [101]
            }

        api.request = request
        self.assertEqual(
            len(api.pages("commits/a/check-runs?filter=all", "check_runs")), 101
        )
        self.assertEqual(len(calls), 2)
        self.assertIn("&per_page=100&page=2", calls[1])


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeGitHub()
        self.outputs = {}
        self.run_id = 10
        summary = patch.object(risk, "summary")
        self.summary = summary.start()
        self.addCleanup(summary.stop)
        sleep = patch.object(risk.time, "sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)
        self.patches = [
            patch.object(risk, "now_utc", return_value=NOW),
            patch.object(
                risk,
                "emit",
                side_effect=lambda name, value: self.outputs.update(
                    {name: copy.deepcopy(value)}
                ),
            ),
            patch.dict(
                os.environ,
                {
                    "POLICY_SHA": BASE,
                    "GITHUB_SHA": BASE,
                    "GITHUB_RUN_ID": "10",
                    "GITHUB_RUN_ATTEMPT": "1",
                    "GITHUB_REF": "refs/heads/dev",
                    "REVIEW_OUTCOME": "",
                },
            ),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def prepare(self, config=None, event_name="workflow_dispatch"):
        """One fresh workflow run: a dispatch on dev unless the event says otherwise."""
        self.run_id += 1
        os.environ["GITHUB_RUN_ID"] = str(self.run_id)
        os.environ["REVIEW_OUTCOME"] = ""
        if event_name == "workflow_dispatch":
            self.api.runs[self.run_id] = self.api.dispatch_run(os.environ["GITHUB_SHA"])
            event = {"inputs": {"pr_number": "42"}}
        else:
            self.api.runs[self.run_id] = {
                **self.api.dispatch_run(HEAD),
                "event": "pull_request_target",
                "head_branch": "docs/guide",
            }
            event = {"pull_request": {"number": 42}}
        self.outputs.clear()
        try:
            risk.prepare(self.api, POLICY, config or settings(), event, event_name)
        finally:
            # The workflow's "Review reserved" step runs whenever the policy
            # step emitted a reservation, even if the step failed afterwards;
            # its "Review retryable" step when review=true was never emitted.
            if self.outputs.get("reserved"):
                self.api.reserve(self.run_id, self.outputs["reserved"], self.outputs.get("review") == "true")
        return self.outputs.get("context")

    def failing(self, matches, times, status=502, lose_response=False):
        """Fail the next `times` requests for which matches(method, route, data)
        is true, with HTTP `status` (None: no answer). lose_response: GitHub
        applied the request but the answer was lost."""
        original = self.api.request
        left = [times]

        def request(method, route, data=None, **kwargs):
            if left[0] and matches(method, route, data):
                left[0] -= 1
                if lose_response:
                    original(method, route, data, **kwargs)
                raise risk.ApiError(f"GitHub API denied or failed a request (HTTP {status})", status)
            return original(method, route, data, **kwargs)

        self.api.request = request
        return left

    def publish(self, context, raw=None, job_result="success"):
        """The publish step, then the marker step the workflow runs for its outcome."""
        if raw is None:
            raw = verdict(head_sha=context["head"], base_sha=context["base"])
        outcome = risk.publish_verdict(self.api, POLICY, context, raw, job_result)
        self.assertEqual(self.outputs["outcome"], outcome)
        self.api.mark(context["run_id"], outcome, context)
        os.environ["REVIEW_OUTCOME"] = outcome
        return outcome

    def passing_review(self):
        context = self.prepare()
        self.assertEqual(self.outputs["review"], "true")
        self.assertEqual(self.publish(context), "passed")
        return context

    def review_passes(self, context):
        pushes = risk.branch_pushes(self.api, self.api.pr, {"trusted-author"})
        return risk.review_passes(self.api, POLICY, self.api.pr, context, pushes)

    def reservations(self):
        return self.api.writes("check-runs")

    def escalations(self):
        return [c["body"] for c in self.api.comments if c["body"].startswith(risk.ESCALATION_MARKER)]

    def new_head(self, sha, text=None, force=False):
        """A pushed revision: new head SHA, CI green on it, optionally a new diff."""
        self.api.push(sha, force=force)
        self.api.checks[0]["head_sha"] = sha
        if text is not None:
            self.api.files[0]["patch"] = "@@ -1 +1 @@\n+" + text

    def catch_up(self, sha, tip):
        """The author merges dev into the branch: new head and base, same own diff,
        with shifted hunk line numbers. The next dispatch runs on the new tip."""
        self.api.dev_tip = tip
        self.api.pr["base"]["sha"] = tip
        self.new_head(sha)
        self.api.files[0]["patch"] = self.api.files[0]["patch"].replace("@@ -1 +1 @@", "@@ -9 +9 @@")
        os.environ["POLICY_SHA"] = tip
        os.environ["GITHUB_SHA"] = tip

    def test_full_positive_path_is_head_bound_and_audited(self):
        context = self.passing_review()
        risk.merge(self.api, POLICY, settings(), context, self.api.approver)
        self.assertEqual(
            self.api.writes("pulls/42/merge")[0][2],
            {"sha": HEAD, "merge_method": "merge"},
        )
        self.assertEqual(self.api.writes("pulls/42/reviews")[0][2]["commit_id"], HEAD)
        labels = {label["name"] for label in self.api.pr["labels"]}
        self.assertTrue(
            {"autopilot-low", "autopilot-approved", "autopilot-merged"} <= labels
        )
        self.assertTrue(self.api.writes("actions/workflows/backend-ci.yml/dispatches"))
        approval_index = next(
            i
            for i, r in enumerate(self.api.requests)
            if r[:2] == ("POST", "pulls/42/reviews")
        )
        before = self.api.requests[:approval_index]
        self.assertTrue(
            any(
                r[1] == "issues/42/comments" and "automated approval" in r[2]["body"]
                for r in before
                if r[2]
            )
        )

    def test_off_does_no_api_work(self):
        self.prepare(settings(enabled=""))
        self.assertEqual(self.api.requests, [])

    def test_missing_key_skips_review_and_merge(self):
        self.prepare(settings(key_present=False))
        self.assertEqual(self.outputs["review"], "false")
        self.assertEqual(self.outputs["context"], "")
        self.assertFalse(self.reservations())
        self.assertFalse(
            any(c["body"].startswith(risk.ROUND_MARKER) for c in self.api.comments)
        )

    def test_fork_and_untrusted_author_do_not_spend_a_round(self):
        self.api.pr["head"]["repo"] = {"full_name": "fork/project", "fork": True}
        self.prepare()
        self.assertFalse(self.reservations())
        self.api.pr = pr()
        self.prepare(settings(authors="other-author"))
        self.assertFalse(self.reservations())

    def test_medium_and_high_receive_human_tier_audits(self):
        for path, tier, humans in (
            ("futureagi/views.py", "medium", "One human"),
            (".github/workflows/ci.yml", "high", "Two human"),
        ):
            self.api.files = [file(path)]
            self.prepare()
            body = next(
                c["body"]
                for c in self.api.comments
                if c["body"].startswith(risk.TIER_MARKER)
            )
            self.assertIn(tier.upper(), body)
            self.assertIn(humans, body)
            self.assertFalse(self.reservations())
            self.assertFalse(self.api.writes("pulls/42/reviews"))
        labels = {label["name"] for label in self.api.pr["labels"]}
        self.assertNotIn("autopilot-medium", labels)

    def test_pending_checks_do_not_spend_a_round(self):
        self.api.checks[0]["conclusion"] = None
        self.prepare()
        self.assertFalse(self.reservations())

    def test_each_verdict_has_one_outcome_and_only_a_pass_can_merge(self):
        cases = [
            (verdict(blocking_findings=True), "success", "blocked", "failure"),
            ("{}", "success", "blocked", "failure"),
            (verdict(complete=False), "success", "blocked", "failure"),
            (verdict(head_sha=OTHER), "success", "blocked", "failure"),
            (verdict(), "failure", "retryable", "cancelled"),
            (verdict(), "skipped", "retryable", "cancelled"),
            (verdict(), "cancelled", "retryable", "cancelled"),
        ]
        for raw, job_result, outcome, conclusion in cases:
            with self.subTest(raw=raw[:40], job_result=job_result):
                self.api = FakeGitHub()
                context = self.prepare()
                self.assertEqual(self.publish(context, raw, job_result), outcome)
                self.assertEqual(self.api.checks[-1]["conclusion"], conclusion)
                with self.assertRaises(risk.PolicyError):
                    risk.merge(self.api, POLICY, settings(), context, self.api.approver)
                self.assertFalse(self.api.writes("pulls/42/merge"))

    def test_head_change_during_review_makes_a_pass_retryable(self):
        context = self.prepare()
        self.api.pr["head"]["sha"] = OTHER
        self.assertEqual(
            risk.publish_verdict(self.api, POLICY, context, verdict(), "success"),
            "retryable",
        )
        self.assertEqual(self.api.checks[-1]["conclusion"], "cancelled")

    def test_blocking_verdict_stands_even_if_head_or_dev_moved(self):
        for move in ("head", "dev"):
            with self.subTest(move=move):
                self.api = FakeGitHub()
                context = self.prepare()
                if move == "head":
                    self.api.pr["head"]["sha"] = OTHER
                else:
                    self.api.dev_tip = OTHER
                raw = verdict(blocking_findings=True)
                self.assertEqual(self.publish(context, raw), "blocked")
                self.assertEqual(self.api.checks[-1]["conclusion"], "failure")

    def test_cached_success_reuses_review_but_rechecks_every_gate(self):
        context = self.passing_review()
        self.prepare()
        self.assertEqual(self.outputs["review"], "false")
        self.assertNotIn("check_id", self.outputs["context"])
        self.assertEqual(self.outputs["context"]["patch"], context["patch"])
        self.assertEqual(len(self.reservations()), 1)
        for key, value in (
            ("key_present", False),
            ("enabled", "off"),
            ("authors", "other"),
        ):
            with self.assertRaises(risk.PolicyError):
                risk.merge(self.api, POLICY, settings(**{key: value}), context, self.api.approver)
        self.assertFalse(self.api.writes("pulls/42/merge"))

    def test_failed_or_cancelled_revision_never_automatically_retries(self):
        self.prepare()
        self.prepare()
        self.assertEqual(len(self.reservations()), 1)
        self.assertEqual(self.outputs["review"], "false")
        self.assertTrue(any("did not finish" in body for body in self.escalations()))

    def test_partial_reruns_cannot_publish_again_or_replace_blocking_verdict(self):
        context = self.prepare()
        self.assertEqual(self.publish(context, verdict(blocking_findings=True)), "blocked")
        for attempt in ("2", "3", "4"):
            with patch.dict(os.environ, {"GITHUB_RUN_ATTEMPT": attempt}):
                with self.assertRaisesRegex(risk.PolicyError, "reruns"):
                    risk.publish_verdict(
                        self.api, POLICY, context, verdict(), "success"
                    )
        # Even replaying the original attempt cannot overwrite a completed check.
        with self.assertRaisesRegex(risk.PolicyError, "not pending"):
            risk.publish_verdict(self.api, POLICY, context, verdict(), "success")
        self.assertEqual(self.api.checks[-1]["conclusion"], "failure")
        self.assertEqual(len(self.api.writes(f"check-runs/{context['check_id']}")), 1)
        self.assertEqual(len(risk.review_ledger(self.api, 42, self.api.comments)), 1)

    def test_workflow_partial_ai_reruns_do_not_spend_unreserved_invocations(self):
        # GitHub retains successful prepare outputs on re-run-failed-jobs and
        # re-run-specific-job. Replay that exact context without calling prepare.
        context = self.prepare()
        ai_job = workflow_job("ai-review")
        guard = textwrap.dedent(
            ai_job.split("        run: |\n", 1)[1].split("      - ", 1)[0]
        )
        self.assertLess(ai_job.index("run: |"), ai_job.index("id: claude"))
        invocations = []
        for attempt in range(1, 5):
            with patch.dict(os.environ, {"GITHUB_RUN_ATTEMPT": str(attempt)}):
                guarded = subprocess.run(
                    ["bash", "-c", guard], check=False, capture_output=True, text=True
                )
                self.assertEqual(guarded.returncode == 0, attempt == 1)
                if guarded.returncode == 0:
                    invocations.append(attempt)
                    # A failed billable action consumes the original reservation.
                    self.assertEqual(
                        risk.publish_verdict(self.api, POLICY, context, "", "failure"),
                        "retryable",
                    )
        self.assertEqual(invocations, [1])
        self.assertEqual(len(risk.review_ledger(self.api, 42, self.api.comments)), 1)
        self.assertEqual(self.api.checks[-1]["conclusion"], "cancelled")

    def test_pending_reservation_cannot_be_completed_by_a_rerun_or_another_run(self):
        context = self.prepare()
        for env in (
            {"GITHUB_RUN_ATTEMPT": "2"},
            {"GITHUB_RUN_ATTEMPT": ""},
            {"GITHUB_RUN_ID": "99"},
        ):
            with patch.dict(os.environ, env), self.assertRaises(risk.PolicyError):
                risk.publish_verdict(self.api, POLICY, context, verdict(), "success")
        self.assertEqual(self.api.checks[-1]["status"], "in_progress")
        self.assertFalse(self.api.writes(f"check-runs/{context['check_id']}"))

    def test_verdict_cannot_complete_a_different_reservation(self):
        context = self.prepare()
        self.api.checks[-1]["external_id"] = json.dumps({**context, "round": 2})
        with self.assertRaisesRegex(risk.PolicyError, "does not match"):
            risk.publish_verdict(self.api, POLICY, context, verdict(), "success")
        self.assertFalse(self.api.writes(f"check-runs/{context['check_id']}"))

    def test_full_rerun_cannot_reserve_or_write_audits(self):
        with (
            patch.dict(os.environ, {"GITHUB_RUN_ATTEMPT": "2"}),
            self.assertRaisesRegex(risk.PolicyError, "reruns"),
        ):
            self.prepare()
        self.assertEqual(self.api.requests, [])

    def test_nested_harness_change_never_reaches_review_or_approval(self):
        for root in TEST_ROOTS:
            self.api = FakeGitHub()
            self.api.files = [file(f"{root}/test_helpers/conftest.py", status="added")]
            self.prepare()
            self.assertEqual(self.outputs["review"], "false")
            self.assertEqual(self.outputs["context"], "")
            self.assertFalse(self.reservations())
            self.assertFalse(self.api.writes("pulls/42/reviews"))
            self.assertFalse(self.api.writes("pulls/42/merge"))

    def test_three_rounds_across_force_pushes_then_human(self):
        for index in range(4):
            self.new_head(str(index + 1) * 40, f"change {index}", force=True)
            self.prepare()
        self.assertEqual(len(self.reservations()), 3)
        self.assertEqual(self.outputs["review"], "false")
        self.assertEqual(len(risk.review_ledger(self.api, 42, self.api.comments)), 3)
        self.assertTrue(any("budget is exhausted" in body for body in self.escalations()))

    # --- Where review evidence may come from (finding 17) ---

    def test_only_dev_dispatch_runs_for_the_reviewed_base_are_trusted(self):
        self.api.runs[5] = self.api.dispatch_run(BASE)
        self.assertTrue(risk.trusted_run(self.api, 5, 42, BASE))
        for key, value in (
            ("event", "pull_request_target"),
            ("event", "pull_request"),
            ("head_branch", "x-base"),
            ("head_sha", OTHER),
            ("path", ".github/workflows/unrelated.yml"),
            ("display_title", "PR autopilot #7"),
            ("head_repository", {"full_name": "fork/project"}),
            ("head_repository", None),
        ):
            with self.subTest(key=key, value=value):
                self.api.runs[5] = {**self.api.dispatch_run(BASE), key: value}
                self.assertFalse(risk.trusted_run(self.api, 5, 42, BASE))
        self.api.runs[5] = self.api.dispatch_run(BASE)
        self.assertFalse(risk.trusted_run(self.api, 5, 42, OTHER))

    def test_pull_request_target_only_classifies_labels_and_dispatches(self):
        self.prepare(event_name="pull_request_target")
        self.assertIn("autopilot-low", {label["name"] for label in self.api.pr["labels"]})
        self.assertEqual(
            [w[2] for w in self.api.writes(f"actions/workflows/{risk.WORKFLOW}/dispatches")],
            [{"ref": "dev", "inputs": {"pr_number": "42"}}],
        )
        self.assertFalse(self.reservations())
        self.assertFalse(any(c["body"].startswith(risk.ROUND_MARKER) for c in self.api.comments))
        self.assertEqual((self.outputs["review"], self.outputs["context"]), ("false", ""))

    def test_pull_request_target_dispatches_only_a_trusted_low_pr_without_hold(self):
        cases = (
            ("untrusted", lambda api: None, settings(authors="other-author")),
            ("medium", lambda api: api.files.__setitem__(0, file("futureagi/views.py")), settings()),
            ("hold", lambda api: api.pr["labels"].append({"name": "autopilot-hold"}), settings()),
            ("draft", lambda api: api.pr.update(draft=True), settings()),
        )
        for name, change, config in cases:
            with self.subTest(name=name):
                self.api = FakeGitHub()
                change(self.api)
                self.prepare(config, event_name="pull_request_target")
                self.assertFalse(self.api.writes(f"actions/workflows/{risk.WORKFLOW}/dispatches"))
                self.assertFalse(self.reservations())

    def test_dispatch_run_stops_when_dev_moved_before_checkout(self):
        with patch.dict(os.environ, {"GITHUB_SHA": OTHER}):
            with self.assertRaisesRegex(risk.PolicyError, "dev advanced; wait for a fresh run"):
                self.prepare()
        self.assertFalse(self.reservations())
        self.assertNotIn("context", {k: v for k, v in self.outputs.items() if v})

    def test_forged_pass_from_a_pull_request_target_run_cannot_merge(self):
        # A genuine blocking verdict on HEAD, then the same diff force-pushed as
        # OTHER with a pass recorded by a pull_request_target run with the right
        # title and path. Such a run takes its workflow from main (before Dec
        # 2025, from the PR's base branch), never dev's copy, so it never vouches.
        context = self.prepare()
        self.assertEqual(self.publish(context, verdict(blocking_findings=True)), "blocked")
        self.new_head(OTHER, force=True)
        self.api.runs[500] = {**self.api.dispatch_run(HEAD), "event": "pull_request_target",
                              "head_branch": "x-base", "head_sha": OTHER}
        forged = {"pr": 42, "head": OTHER, "base": BASE, "run_id": 500, "round": 2,
                  "patch": context["patch"]}
        self.api.mark(500, "passed", forged)
        self.api.checks.append({"id": 900, "name": POLICY["review_check"], "head_sha": OTHER,
                                "status": "completed", "conclusion": "success",
                                "app": {"slug": "github-actions"}, "external_id": json.dumps(forged)})
        reservations = len(self.reservations())
        self.prepare()
        self.assertEqual(self.outputs["review"], "false")
        self.assertEqual(self.outputs["context"], "")
        self.assertEqual(len(self.reservations()), reservations)
        self.assertTrue(any("not the trusted dev run" in body for body in self.escalations()))
        with self.assertRaises(risk.PolicyError):
            risk.merge(self.api, POLICY, settings(), {**forged, "check_id": 900}, self.api.approver)
        self.assertFalse(self.api.writes("pulls/42/merge"))

    def test_forged_check_on_a_new_head_citing_a_real_run_goes_to_a_person(self):
        context = self.passing_review()
        self.new_head(OTHER)
        forged = {**{k: context[k] for k in risk.RECORD_KEYS}, "head": OTHER}
        self.api.checks.append({"id": 900, "name": POLICY["review_check"], "head_sha": OTHER,
                                "status": "completed", "conclusion": "success",
                                "app": {"slug": "github-actions"}, "external_id": json.dumps(forged)})
        self.prepare()
        self.assertEqual(len(self.reservations()), 1)
        self.assertEqual(self.outputs["context"], "")
        self.assertTrue(any("disagree" in body for body in self.escalations()))

    def test_run_marker_naming_another_revision_goes_to_a_person(self):
        context = self.passing_review()
        self.api.mark(context["run_id"], "passed", {**context, "head": OTHER})
        with patch.dict(os.environ, {"GITHUB_RUN_ID": "99", "REVIEW_OUTCOME": ""}):
            with self.assertRaisesRegex(risk.NeedsPerson, "different revision"):
                self.review_passes(context)

    def test_record_moved_to_a_run_without_that_reservation_goes_to_a_person(self):
        # Blocked in run A; run B is a later dispatch run that reserved nothing.
        # An edited record citing B with a made-up digest would move the
        # never-finished bar off the real diff.
        context = self.prepare()
        self.assertEqual(self.publish(context, verdict(blocking_findings=True)), "blocked")
        self.prepare()
        run_b = self.run_id
        self.drop_ledger()
        review = next(c for c in self.api.checks if c["name"] == POLICY["review_check"])
        review["external_id"] = json.dumps({**json.loads(review["external_id"]),
                                            "run_id": run_b, "patch": "e" * 32})
        self.catch_up("1" * 40, OTHER)
        self.prepare()
        self.assertEqual(self.outputs["review"], "false")
        self.assertEqual(len(self.reservations()), 1)
        self.assertTrue(any("did not reserve" in body for body in self.escalations()))

    def foreign_attempt(self, run_id, outcome, head, base, patch, number=7, via="check"):
        """A review of PR `number` whose check or reservation status sits on one
        of this PR's heads."""
        self.api.runs[run_id] = self.api.dispatch_run(base, number)
        record = {"pr": number, "head": head, "base": base, "run_id": run_id, "round": 1, "patch": patch}
        self.api.reserve(run_id, risk.RESERVATION.format(head=head, base=base, patch=patch))
        self.api.mark(run_id, outcome, record)
        if via == "check":
            self.api.checks.append({"id": 800 + run_id, "name": POLICY["review_check"], "head_sha": head,
                                    "status": "completed", "conclusion": "success",
                                    "app": {"slug": "github-actions"}, "external_id": json.dumps(record)})
        else:
            self.forge_status(head, risk.RUN_URL.format(repository=REPO, run_id=run_id))

    def forge_status(self, sha, target_url):
        """A reservation status posted on `sha` by any token with statuses: write."""
        self.api.commit_statuses.setdefault(sha, []).insert(
            0, {"context": risk.STATUS_CONTEXT, "state": "success", "target_url": target_url})

    def test_records_naming_another_pr_only_bar(self):
        for via in ("check", "status"):
            with self.subTest(via=via):
                self.api = FakeGitHub()
                own_patch = risk.patch_digest(risk.review_patches(self.api.files, POLICY))
                # Three passing reviews of other diffs on this head, for PR 7:
                # they do not use this PR's three-diff budget.
                for index, patch_id in enumerate(("1" * 32, "2" * 32, "3" * 32)):
                    self.foreign_attempt(501 + index, "passed", HEAD, OTHER, patch_id, via=via)
                self.prepare()
                self.assertEqual(self.outputs["review"], "true")
                # A pass of this exact revision for PR 7 is not reused for PR 42.
                self.api = FakeGitHub()
                self.foreign_attempt(501, "passed", HEAD, BASE, own_patch, via=via)
                self.prepare()
                self.assertEqual((self.outputs["review"], self.outputs["context"]), ("false", ""))
                self.assertFalse(self.reservations())
                # A blocked review of this diff for PR 7 bars it here too.
                self.api = FakeGitHub()
                self.foreign_attempt(501, "blocked", HEAD, OTHER, own_patch, via=via)
                self.prepare()
                self.assertFalse(self.reservations())
                self.assertTrue(any("found blocking issues" in body for body in self.escalations()))

    def test_human_forged_ledger_and_untrusted_runs_are_not_trusted(self):
        self.api.comments.append(
            {
                "id": 1,
                "body": risk.ROUND_MARKER + "bad -->",
                "user": {"login": "author", "type": "User"},
            }
        )
        context = self.passing_review()
        genuine = self.api.runs[context["run_id"]]
        for key, value in (
            ("path", ".github/workflows/unrelated.yml"),
            ("event", "pull_request"),
            ("event", "pull_request_target"),
            ("head_branch", "feature"),
            ("head_sha", OTHER),
            ("display_title", "PR autopilot #7"),
        ):
            with self.subTest(key=key, value=value):
                self.api.runs[context["run_id"]] = {**genuine, key: value}
                with patch.dict(os.environ, {"GITHUB_RUN_ID": "99"}):
                    with self.assertRaises(risk.NeedsPerson):
                        self.review_passes(context)

    # --- Outcome evidence and reuse (findings 18, 20 and 22) ---

    def test_later_run_reuses_a_pass_only_when_the_run_shows_it(self):
        context = self.passing_review()
        later = {key: context[key] for key in ("pr", "head", "base", "patch")}
        with patch.dict(os.environ, {"GITHUB_RUN_ID": "99", "REVIEW_OUTCOME": ""}):
            self.assertTrue(self.review_passes(later))
            # "Re-run failed jobs": the default listing now shows attempt 2, whose
            # gate job was skipped. Attempt 1 is read explicitly.
            self.api.run_jobs[(context["run_id"], 2)] = [
                {"name": "Publish review gate and recheck merge", "steps": []}]
            self.assertTrue(self.review_passes(later))
            self.assertTrue(any(r[1] == f"actions/runs/{context['run_id']}/attempts/1/jobs"
                                for r in self.api.requests))
            self.assertFalse(any(r[1] == f"actions/runs/{context['run_id']}/jobs"
                                 for r in self.api.requests))
            # A marker step that did not succeed is no outcome: never finished.
            for job in self.api.run_jobs[(context["run_id"], 1)]:
                if job["name"] != "prepare":
                    for step in job["steps"]:
                        step["conclusion"] = "failure"
            self.assertFalse(self.review_passes(later))
            self.api.drop_outcome(context["run_id"])
            self.assertFalse(self.review_passes(later))
            # Not even the reservation step: the record does not fit its run. The
            # reservation status is checked first (a record-only case is in
            # test_record_moved_to_a_run_without_that_reservation_goes_to_a_person).
            self.api.run_jobs.pop((context["run_id"], 1))
            with self.assertRaisesRegex(risk.NeedsPerson, "one reservation"):
                self.review_passes(later)

    def test_reuse_needs_the_same_diff(self):
        context = self.passing_review()
        with patch.dict(os.environ, {"GITHUB_RUN_ID": "99", "REVIEW_OUTCOME": ""}):
            self.assertFalse(self.review_passes({**context, "patch": "e" * 32}))

    def test_rerun_of_a_failed_merge_step_keeps_the_pass_reusable(self):
        context = self.passing_review()
        self.api.run_jobs[(context["run_id"], 2)] = [
            {"name": "Publish review gate and recheck merge", "steps": []}]
        self.prepare()
        self.assertEqual(self.outputs["review"], "false")
        self.assertEqual(self.outputs["context"]["head"], HEAD)
        self.assertEqual(len(self.reservations()), 1)
        self.assertFalse(self.escalations())

    def test_same_run_merge_does_not_wait_for_the_jobs_api(self):
        for lag in ("outcome", "every job"):
            with self.subTest(lag=lag):
                self.api = FakeGitHub()
                context = self.passing_review()
                if lag == "outcome":
                    self.api.drop_outcome(context["run_id"])
                else:
                    # Not even this run's "Review reserved" step is listed yet;
                    # its own reservation status is read from the context.
                    self.api.run_jobs.pop((context["run_id"], 1))
                risk.merge(self.api, POLICY, settings(), context, self.api.approver)
                self.assertTrue(self.api.writes("pulls/42/merge"))
        # Without the publish step's "passed" the same run has no evidence.
        self.api = FakeGitHub()
        context = self.passing_review()
        self.api.drop_outcome(context["run_id"])
        os.environ["REVIEW_OUTCOME"] = ""
        with self.assertRaises(risk.PolicyError):
            risk.merge(self.api, POLICY, settings(), context, self.api.approver)
        self.assertFalse(self.api.writes("pulls/42/merge"))

    def test_comment_failure_does_not_change_a_published_pass(self):
        context = self.prepare()
        original = self.api.request

        def flaky(method, route, data=None, **kwargs):
            if method == "POST" and route == "issues/42/comments" and data["body"].startswith("AI review attempt"):
                raise risk.PolicyError("comment failed")
            return original(method, route, data, **kwargs)

        self.api.request = flaky
        self.assertEqual(self.publish(context), "passed")
        self.assertEqual(self.api.checks[-1]["conclusion"], "success")
        self.api.request = original
        self.prepare()
        self.assertEqual(self.outputs["context"]["head"], HEAD)
        self.assertEqual(len(self.reservations()), 1)

    def test_lost_check_update_response_still_publishes(self):
        context = self.prepare()
        original = self.api.request

        def lossy(method, route, data=None, **kwargs):
            result = original(method, route, data, **kwargs)
            if method == "PATCH" and route.startswith("check-runs/"):
                raise risk.PolicyError("response lost")
            return result

        self.api.request = lossy
        self.assertEqual(self.publish(context), "passed")

    # --- A failed GitHub write after the reservation (round three) ---

    RESERVATION_WRITES = {
        "ledger comment": lambda m, r, d: m == "POST" and r == "issues/42/comments" and d["body"].startswith(risk.ROUND_MARKER),
        "reservation status": lambda m, r, d: m == "POST" and r.startswith("statuses/"),
        "review check": lambda m, r, d: m == "POST" and r == "check-runs",
    }

    def test_one_failed_reservation_write_is_retried(self):
        for name, matches in self.RESERVATION_WRITES.items():
            for status in (502, 429, None):
                with self.subTest(write=name, status=status):
                    self.api = FakeGitHub()
                    self.failing(matches, 1, status)
                    context = self.prepare()
                    self.assertEqual(self.outputs["review"], "true")
                    self.assertEqual(len([c for c in self.api.checks if c["name"] == POLICY["review_check"]]), 1)
                    self.assertEqual(self.publish(context), "passed")
                    risk.merge(self.api, POLICY, settings(), context, self.api.approver)
                    self.assertTrue(self.api.writes("pulls/42/merge"))
                    self.assertFalse(self.escalations())

    def test_a_check_created_with_a_lost_response_is_not_posted_twice(self):
        self.failing(self.RESERVATION_WRITES["review check"], 1, lose_response=True)
        context = self.prepare()
        [check] = [c for c in self.api.checks if c["name"] == POLICY["review_check"]]
        self.assertEqual(context["check_id"], check["id"])
        self.assertEqual(len(self.reservations()), 1)
        self.assertEqual(self.publish(context), "passed")

    def test_a_refused_reservation_write_is_not_retried(self):
        left = self.failing(self.RESERVATION_WRITES["review check"], 5, status=403)
        with self.assertRaises(risk.ApiError):
            self.prepare()
        # One try only: five failures were armed and four are left.
        self.assertEqual(left[0], 4)
        self.assertFalse(self.reservations())

    def test_a_review_that_could_not_start_is_retried_after_the_branch_changes(self):
        for name, matches in self.RESERVATION_WRITES.items():
            with self.subTest(write=name):
                self.api = FakeGitHub()
                os.environ.update(POLICY_SHA=BASE, GITHUB_SHA=BASE)
                self.failing(matches, 3)
                with self.assertRaises(risk.ApiError):
                    self.prepare()
                self.assertEqual(self.outputs["review"], "false")
                self.assertEqual(self.outputs["context"], "")
                if name != "ledger comment":
                    self.assertTrue(any("could not start" in c["body"] for c in self.api.comments))
                # The model was never called: the diff is not barred.
                self.prepare()
                self.assertFalse(self.escalations())
                if name == "ledger comment":
                    # Nothing recorded the attempt, so nothing was charged.
                    self.assertEqual(self.outputs["review"], "true")
                    continue
                self.assertEqual(self.outputs["review"], "false")
                self.catch_up("1" * 40, OTHER)
                context = self.prepare()
                self.assertEqual(self.outputs["review"], "true")
                self.assertEqual(self.publish(context), "passed")
                risk.merge(self.api, POLICY, settings(), context, self.api.approver)
                self.assertTrue(self.api.writes("pulls/42/merge"))
                self.assertFalse(self.escalations())

    def test_publish_survives_a_failed_check_read(self):
        def reading(method, route, data):
            return method == "GET" and route.startswith("check-runs/")

        # Once: retried, the pass stands.
        context = self.prepare()
        self.failing(reading, 1)
        self.assertEqual(self.publish(context), "passed")
        # Persistently: the verdict is still recorded; a pass cannot be confirmed.
        for raw, expected in ((None, "retryable"), (verdict(blocking_findings=True), "blocked")):
            with self.subTest(expected=expected):
                self.api = FakeGitHub()
                context = self.prepare()
                self.failing(reading, 10)
                self.assertEqual(self.publish(context, raw), expected)
                self.assertEqual(self.api.checks[-1]["status"], "completed")
                self.prepare()
                self.assertEqual(self.outputs["review"], "false")
                if expected == "blocked":
                    self.assertTrue(any("found blocking issues" in body for body in self.escalations()))
                else:
                    self.assertFalse(self.escalations())
                    self.catch_up("1" * 40, OTHER)
                    self.prepare()
                    self.assertEqual(self.outputs["review"], "true")
                os.environ.update(POLICY_SHA=BASE, GITHUB_SHA=BASE)

    def test_publish_records_the_outcome_when_the_check_cannot_be_completed(self):
        context = self.prepare()
        patched = []

        def after_first_read(method, route, data):
            # The update and the re-read after it fail; the first read works.
            if route.startswith("check-runs/") and method == "PATCH":
                patched.append(route)
            return route.startswith("check-runs/") and bool(patched)

        self.failing(after_first_read, 10)
        self.assertEqual(self.publish(context), "passed")
        self.assertEqual(self.api.checks[-1]["status"], "in_progress")
        risk.merge(self.api, POLICY, settings(), context, self.api.approver)
        self.assertTrue(self.api.writes("pulls/42/merge"))

    def test_unreviewable_diff_tells_the_pr_once_and_clears_on_a_new_diff(self):
        oversized = "@@ -0,0 +1,200 @@\n" + "\n".join(["+" + "x" * 700] * 200)
        cases = {
            "empty added file": dict(additions=0, status="added", patch=None),
            "oversized diff": dict(additions=200, patch=oversized),
            "patch shorter than its counts": dict(additions=2),
        }
        for name, change in cases.items():
            with self.subTest(case=name):
                self.api = FakeGitHub()
                patch_text = change.pop("patch", "@@ -1 +1 @@\n+new")
                self.api.files = [file("futureagi/tests/test_empty.py", **change)]
                self.api.files[0]["patch"] = patch_text
                if patch_text is None:
                    del self.api.files[0]["patch"]
                self.api.pr.update(additions=self.api.files[0]["additions"])
                for _ in range(3):
                    self.prepare()  # green: no exception
                    self.assertEqual(self.outputs["review"], "false")
                [escalation] = self.escalations()
                self.assertIn("A person must review this PR", escalation)
                self.assertFalse(self.reservations())
                # A new, reviewable diff clears it.
                self.api.files = [file("futureagi/tests/test_empty.py")]
                self.api.pr.update(additions=1)
                self.new_head("1" * 40, "a reviewable change")
                self.prepare()
                self.assertEqual(self.outputs["review"], "true")
                self.assertTrue(self.escalations()[0].startswith(risk.RESOLVED))

    def test_check_conclusions_are_not_evidence(self):
        # A blocked run's check flipped to success does not free the diff.
        context = self.prepare()
        self.assertEqual(self.publish(context, verdict(blocking_findings=True)), "blocked")
        self.api.checks[-1]["conclusion"] = "success"
        self.catch_up("1" * 40, OTHER)
        self.prepare()
        self.assertEqual(self.outputs["review"], "false")
        self.assertEqual(len(self.reservations()), 1)
        # A passing run's check flipped to failure is still that run's pass.
        self.api = FakeGitHub()
        os.environ.update(POLICY_SHA=BASE, GITHUB_SHA=BASE)
        self.passing_review()
        self.api.checks[-1]["conclusion"] = "failure"
        self.prepare()
        self.assertEqual(self.outputs["context"]["head"], HEAD)

    def test_catch_up_with_the_same_diff_costs_no_round(self):
        self.passing_review()
        self.catch_up("1" * 40, OTHER)
        context = self.prepare()
        self.assertEqual(self.outputs["review"], "true")
        self.assertEqual((context["head"], context["base"]), ("1" * 40, OTHER))
        self.assertEqual(len(risk.charged_diffs(risk.review_ledger(self.api, 42, self.api.comments))), 1)
        self.assertIn("different diffs reviewed: 1/3", self.api.comments[-1]["body"])

    def test_same_diff_that_was_blocked_is_not_reviewed_again_after_a_catch_up(self):
        context = self.prepare()
        self.assertEqual(self.publish(context, verdict(blocking_findings=True)), "blocked")
        reviews = len(self.reservations())
        self.catch_up("1" * 40, OTHER)
        self.prepare()
        self.assertEqual(self.outputs["review"], "false")
        self.assertEqual(len(self.reservations()), reviews)
        self.assertTrue(any("found blocking issues" in body for body in self.escalations()))

    def test_stale_pass_is_reviewed_again_after_a_catch_up(self):
        context = self.prepare()
        self.api.dev_tip = OTHER
        self.assertEqual(self.publish(context), "retryable")
        self.catch_up("1" * 40, OTHER)
        self.prepare()
        self.assertEqual(self.outputs["review"], "true")
        self.assertEqual(len(self.reservations()), 2)

    def test_blocked_and_stale_verdict_still_bars_the_diff(self):
        context = self.prepare()
        self.api.dev_tip = OTHER
        self.assertEqual(self.publish(context, verdict(blocking_findings=True)), "blocked")
        self.catch_up("1" * 40, OTHER)
        self.prepare()
        self.assertEqual(self.outputs["review"], "false")
        self.assertEqual(len(self.reservations()), 1)

    def test_never_published_reservation_bars_the_diff(self):
        self.prepare()
        self.catch_up("1" * 40, OTHER)
        self.prepare()
        self.assertEqual(self.outputs["review"], "false")
        self.assertEqual(len(self.reservations()), 1)
        self.assertTrue(any("did not finish" in body for body in self.escalations()))

    def test_failed_ai_job_waits_on_the_same_revision_and_retries_on_the_next(self):
        context = self.prepare()
        self.assertEqual(self.publish(context, "", "failure"), "retryable")
        self.assertIn("Update the branch from dev", self.api.comments[-1]["body"])
        self.prepare()
        self.assertEqual(self.outputs["review"], "false")
        self.assertEqual(len(self.reservations()), 1)
        self.assertFalse(self.escalations())
        self.catch_up("1" * 40, OTHER)
        self.prepare()
        self.assertEqual(self.outputs["review"], "true")

    def test_always_stale_pr_stops_after_nine_attempts(self):
        for index in range(12):
            self.catch_up(f"{index + 1:x}" * 40, f"{index + 2:x}" * 40)
            context = self.prepare()
            if self.outputs["review"] == "true":
                self.api.dev_tip = "f" * 40
                self.assertEqual(self.publish(context), "retryable")
        self.assertEqual(len(self.reservations()), 9)
        self.assertTrue(any("budget is exhausted" in body for body in self.escalations()))

    def test_a_changed_diff_is_charged_a_new_round(self):
        self.passing_review()
        self.new_head("1" * 40, "a real change")
        self.prepare()
        self.assertEqual(self.outputs["review"], "true")
        self.assertEqual(len(risk.charged_diffs(risk.review_ledger(self.api, 42, self.api.comments))), 2)

    def test_free_re_reviews_are_capped(self):
        for index in range(12):
            self.catch_up(f"{index + 1:x}" * 40, f"{index + 2:x}" * 40)
            context = self.prepare()
            if self.outputs["review"] == "true":
                self.assertEqual(self.publish(context), "passed")
        self.assertEqual(len(self.reservations()), 9)
        self.assertTrue(any("budget is exhausted" in body for body in self.escalations()))

    def test_a_later_run_that_proceeds_marks_the_escalation_as_no_longer_applying(self):
        context = self.prepare()
        self.assertEqual(self.publish(context, verdict(blocking_findings=True)), "blocked")
        self.catch_up("1" * 40, OTHER)
        self.prepare()
        [escalation] = [c for c in self.api.comments if c["body"].startswith(risk.ESCALATION_MARKER)]
        self.assertIn("found blocking issues", escalation["body"])
        # While the reason holds, later runs keep the escalation.
        self.prepare()
        self.assertIn("found blocking issues", escalation["body"])
        # Anyone with write access can edit the bot's comment; that text is
        # never posted again.
        escalation["body"] += " @someone injected"
        # The author fixes the finding: a new diff, reviewed again.
        self.new_head("2" * 40, "fixed")
        context = self.prepare()
        self.assertEqual(self.outputs["review"], "true")
        self.assertTrue(escalation["body"].startswith(risk.RESOLVED))
        self.assertIn("2" * 40, escalation["body"])
        self.assertNotIn("found blocking issues", escalation["body"])
        self.assertNotIn("injected", escalation["body"])
        requests = self.api.requests
        resolved = next(i for i, r in enumerate(requests)
                        if r[:2] == ("PATCH", f"issues/comments/{escalation['id']}")
                        and r[2]["body"].startswith(risk.RESOLVED))
        ledger = [i for i, r in enumerate(requests) if r[:2] == ("POST", "issues/42/comments")
                  and r[2]["body"].startswith(risk.ROUND_MARKER)][-1]
        self.assertLess(resolved, ledger)
        # Not rewritten on every later run.
        self.assertEqual(self.publish(context), "passed")
        edits = len(self.api.writes(f"issues/comments/{escalation['id']}"))
        self.prepare()
        self.assertEqual(len(self.api.writes(f"issues/comments/{escalation['id']}")), edits)
        risk.merge(self.api, POLICY, settings(), self.outputs["context"], self.api.approver)
        self.assertTrue(self.api.writes("pulls/42/merge"))
        self.assertEqual(len(self.escalations()), 1)

    # --- Attempt history survives comment deletion (finding 18) ---

    def drop_ledger(self):
        self.api.comments = [c for c in self.api.comments if not c["body"].startswith(risk.ROUND_MARKER)]

    def test_deleted_ledger_comment_does_not_buy_another_review(self):
        context = self.prepare()
        self.assertEqual(self.publish(context, verdict(blocking_findings=True)), "blocked")
        self.drop_ledger()
        self.prepare()
        self.assertEqual(len(self.reservations()), 1)
        self.assertEqual(self.outputs["review"], "false")
        self.assertTrue(any("found blocking issues" in body for body in self.escalations()))

    def test_deleted_or_edited_comment_and_amended_head_do_not_re_roll_a_block(self):
        for tamper in ("delete", "edit"):
            with self.subTest(tamper=tamper):
                self.api = FakeGitHub()
                context = self.prepare()
                self.assertEqual(self.publish(context, verdict(blocking_findings=True)), "blocked")
                if tamper == "delete":
                    self.drop_ledger()
                else:
                    for c in self.api.comments:
                        if c["body"].startswith(risk.ROUND_MARKER):
                            c["body"] = " " + c["body"]
                # git commit --amend --no-edit, force-pushed: same diff, new SHA.
                self.new_head("1" * 40, force=True)
                self.prepare()
                self.assertEqual(self.outputs["review"], "false")
                self.assertEqual(len(self.reservations()), 1)

    def test_amend_pushed_over_a_later_commit_is_still_found(self):
        # Blocked on HEAD; ledger deleted; a normal push of another diff; then a
        # force push that restores HEAD's diff. HEAD is in neither the PR's
        # commits nor the force-push timeline, only in the branch activity.
        context = self.prepare()
        self.assertEqual(self.publish(context, verdict(blocking_findings=True)), "blocked")
        self.drop_ledger()
        original = self.api.files[0]["patch"]
        self.new_head("1" * 40, "something else")
        self.new_head("2" * 40, force=True)
        self.api.files[0]["patch"] = original
        self.assertNotIn(HEAD, self.api.commits + [s for pair in self.api.force_pushes for s in pair])
        self.prepare()
        self.assertEqual(self.outputs["review"], "false")
        self.assertEqual(len(self.reservations()), 1)

    def test_budget_survives_deleted_comments(self):
        for index in range(3):
            self.new_head(str(index + 1) * 40, f"change {index}", force=True)
            context = self.prepare()
            self.assertEqual(self.publish(context), "passed")
        self.drop_ledger()
        self.new_head("4" * 40, "change 3", force=True)
        self.prepare()
        self.assertEqual(len(self.reservations()), 3)
        self.assertTrue(any("budget is exhausted" in body for body in self.escalations()))

    # --- Reservation statuses: a renamed check (round-2 review) ---

    def rename_review_checks(self):
        """PATCH check-runs/{id} accepts `name`; any Actions token can send it."""
        for item in self.api.checks:
            if item["name"] == POLICY["review_check"]:
                item["name"] = POLICY["review_check"] + " (old)"

    def test_each_reservation_posts_an_append_only_status_on_the_head(self):
        context = self.prepare()
        [status] = self.api.commit_statuses[HEAD]
        self.assertEqual(status["context"], risk.STATUS_CONTEXT)
        self.assertEqual(status["target_url"], f"https://github.com/{REPO}/actions/runs/{context['run_id']}")
        self.assertEqual(status["state"], "success")
        self.assertLessEqual(len(status["description"]), 140)
        requests = self.api.requests
        ledger = next(i for i, r in enumerate(requests) if r[:2] == ("POST", "issues/42/comments")
                      and r[2]["body"].startswith(risk.ROUND_MARKER))
        posted = next(i for i, r in enumerate(requests) if r[:2] == ("POST", f"statuses/{HEAD}"))
        check_run = next(i for i, r in enumerate(requests) if r[:2] == ("POST", "check-runs"))
        self.assertLess(ledger, posted)
        self.assertLess(posted, check_run)
        self.assertEqual(self.outputs["review"], "true")

    def test_renamed_check_and_deleted_comment_do_not_re_roll_a_block(self):
        context = self.prepare()
        self.assertEqual(self.publish(context, verdict(blocking_findings=True)), "blocked")
        self.rename_review_checks()
        self.drop_ledger()
        # git commit --amend --no-edit, force-pushed: same diff, new SHA.
        self.new_head("1" * 40, force=True)
        self.prepare()
        self.assertEqual(self.outputs["review"], "false")
        self.assertEqual(len(self.reservations()), 1)
        self.assertTrue(any("found blocking issues" in body for body in self.escalations()))

    def test_budget_survives_renamed_checks_and_deleted_comments(self):
        for index in range(3):
            self.new_head(str(index + 1) * 40, f"change {index}", force=True)
            context = self.prepare()
            self.assertEqual(self.publish(context), "passed")
        self.rename_review_checks()
        self.drop_ledger()
        self.new_head("4" * 40, "change 3", force=True)
        self.prepare()
        self.assertEqual(len(self.reservations()), 3)
        self.assertTrue(any("budget is exhausted" in body for body in self.escalations()))

    def test_a_pass_indexed_only_by_its_status_is_still_reused(self):
        self.passing_review()
        self.rename_review_checks()
        self.drop_ledger()
        context = self.prepare()
        self.assertEqual(context["head"], HEAD)
        self.assertEqual(len(self.reservations()), 1)
        risk.merge(self.api, POLICY, settings(), context, self.api.approver)
        self.assertTrue(self.api.writes("pulls/42/merge"))

    def test_forged_or_misplaced_reservation_status_goes_to_a_person(self):
        def url(run_id, repository=REPO):
            return risk.RUN_URL.format(repository=repository, run_id=run_id)

        def reserving_run(api, run_id, run, *reserved):
            api.runs[run_id] = run
            for head, base in reserved:
                api.reserve(run_id, risk.RESERVATION.format(head=head, base=base, patch="e" * 32))

        cases = {
            "no run link": (None, "does not link"),
            "another repository": (url(500, "fork/project"), "does not link"),
            "a job link": (url(500) + "/job/1", "does not link"),
            "an untrusted run": (url(500), "one reservation"),
            "a run without a reservation": (url(501), "one reservation"),
            "a run with two reservations": (url(502), "one reservation"),
            "a deleted run": (url(599), "deleted"),
            "a run on another dev commit": (url(503), "not the trusted dev run"),
            "another revision": (url(504), "did not reserve"),
        }
        for name, (target_url, reason) in cases.items():
            with self.subTest(name=name):
                self.api = FakeGitHub()
                reserving_run(self.api, 500, {**FakeGitHub.dispatch_run(BASE), "event": "pull_request_target"},
                              (HEAD, BASE))
                reserving_run(self.api, 501, FakeGitHub.dispatch_run(BASE))
                reserving_run(self.api, 502, FakeGitHub.dispatch_run(BASE), (HEAD, BASE), (HEAD, OTHER))
                reserving_run(self.api, 503, FakeGitHub.dispatch_run(OTHER), (HEAD, BASE))
                reserving_run(self.api, 504, FakeGitHub.dispatch_run(BASE), (OTHER, BASE))
                self.forge_status(HEAD, target_url)
                self.prepare()
                self.assertFalse(self.reservations())
                self.assertEqual(self.outputs["context"], "")
                self.assertTrue(any(reason in body for body in self.escalations()), self.escalations())

    def test_status_citing_this_run_on_another_head_stops_the_merge(self):
        self.api.commits = [OTHER, HEAD]
        context = self.passing_review()
        self.forge_status(OTHER, risk.RUN_URL.format(repository=REPO, run_id=context["run_id"]))
        with self.assertRaisesRegex(risk.NeedsPerson, "did not reserve"):
            risk.merge(self.api, POLICY, settings(), context, self.api.approver)
        self.assertFalse(self.api.writes("pulls/42/reviews"))
        self.assertFalse(self.api.writes("pulls/42/merge"))

    def test_unreadable_review_check_record_goes_to_a_person(self):
        for external_id in ("garbage", None, json.dumps({"pr": 42})):
            with self.subTest(external_id=external_id):
                self.api = FakeGitHub()
                self.api.checks.append({"id": 900, "name": POLICY["review_check"], "head_sha": HEAD,
                                        "status": "completed", "conclusion": "success",
                                        "app": {"slug": "github-actions"}, "external_id": external_id})
                self.prepare()
                self.assertFalse(self.reservations())
                self.assertTrue(any("unreadable record" in body for body in self.escalations()))

    def test_review_check_from_another_app_is_ignored(self):
        self.api.checks.append({"id": 900, "name": POLICY["review_check"], "head_sha": HEAD,
                                "status": "completed", "conclusion": "failure",
                                "app": {"slug": "someone-else"}, "external_id": "garbage"})
        self.prepare()
        self.assertEqual(self.outputs["review"], "true")

    def test_record_whose_run_was_deleted_goes_to_a_person(self):
        for missing in ("run", "jobs"):
            with self.subTest(missing=missing):
                self.api = FakeGitHub()
                os.environ.update(POLICY_SHA=BASE, GITHUB_SHA=BASE)
                context = self.passing_review()
                if missing == "run":
                    del self.api.runs[context["run_id"]]
                else:
                    original = self.api.pages

                    def pages(route, key=None):
                        if route.endswith("/jobs"):
                            raise risk.ApiError("GitHub API denied or failed a request (HTTP 404)", 404)
                        return original(route, key)

                    self.api.pages = pages
                self.prepare()
                self.assertEqual(self.outputs["context"], "")
                self.assertEqual(len(self.reservations()), 1)
                self.assertTrue(any("deleted" in body for body in self.escalations()))

    def fail_reads(self, where, status):
        """Make one kind of history read fail with HTTP `status` (None: no answer)."""
        error = risk.ApiError(f"GitHub API denied or failed a request (HTTP {status})", status)
        pages, request = self.api.pages, self.api.request

        def failing_pages(route, key=None):
            if where == "jobs" and route.endswith("/jobs"):
                raise error
            return pages(route, key)

        def failing_request(method, route, data=None, **kwargs):
            if where == "run" and method == "GET" and re.fullmatch(r"actions/runs/\d+", route):
                raise error
            if where == "heads" and kwargs.get("graphql") and "HeadRefForcePushedEvent" in data["query"]:
                raise error
            return request(method, route, data, **kwargs)

        self.api.pages, self.api.request = failing_pages, failing_request

        def restore():
            self.api.pages, self.api.request = pages, request

        return restore

    def test_gone_run_or_jobs_go_to_a_person(self):
        for where in ("run", "jobs"):
            for status in (404, 410):
                with self.subTest(where=where, status=status):
                    self.api = FakeGitHub()
                    self.passing_review()
                    self.fail_reads(where, status)
                    self.prepare()
                    self.assertEqual(self.outputs["context"], "")
                    self.assertTrue(any("deleted" in body for body in self.escalations()))

    def test_reused_pass_clears_an_escalation_that_no_longer_applies(self):
        # Escalated while the run looked deleted; once it reads again, the next
        # run reuses the pass and must not merge beside "a person must review".
        self.passing_review()
        restore = self.fail_reads("run", 404)
        self.prepare()
        [escalation] = [c for c in self.api.comments if c["body"].startswith(risk.ESCALATION_MARKER)]
        self.assertIn("deleted", escalation["body"])
        restore()
        context = self.prepare()
        self.assertEqual(context["head"], HEAD)
        self.assertNotIn("check_id", context)
        self.assertTrue(escalation["body"].startswith(risk.RESOLVED))
        risk.merge(self.api, POLICY, settings(), context, self.api.approver)
        self.assertTrue(self.api.writes("pulls/42/merge"))

    def test_transient_api_failures_fail_the_run_without_escalating(self):
        # 5xx, rate limits, timeouts and GraphQL errors are not evidence that
        # anything was deleted or edited: the run fails, the next one retries.
        for where in ("jobs", "run", "heads"):
            for status in (502, 503, 429, 403, None):
                with self.subTest(where=where, status=status):
                    self.api = FakeGitHub()
                    context = self.passing_review()
                    reused = {key: context[key] for key in ("pr", "head", "base", "patch", "checks")}
                    restore = self.fail_reads(where, status)
                    with self.assertRaises(risk.ApiError) as caught:
                        self.prepare()
                    self.assertNotIsInstance(caught.exception, risk.NeedsPerson)
                    self.assertEqual(self.outputs["context"], "")
                    with self.assertRaises(risk.ApiError) as caught:
                        risk.merge(self.api, POLICY, settings(), reused, self.api.approver)
                    self.assertNotIsInstance(caught.exception, risk.NeedsPerson)
                    self.assertFalse(self.api.writes("pulls/42/reviews"))
                    self.assertFalse(self.escalations())
                    # After recovery the pass is reused and merged, with no
                    # contradictory escalation on the PR.
                    restore()
                    self.assertEqual(self.prepare()["head"], HEAD)
                    risk.merge(self.api, POLICY, settings(), self.outputs["context"], self.api.approver)
                    self.assertTrue(self.api.writes("pulls/42/merge"))
                    self.assertFalse(self.escalations())

    def test_truncated_or_unavailable_head_history_goes_to_a_person(self):
        for change in (
            lambda api: setattr(api, "commits_truncated", True),
            lambda api: setattr(api, "heads_payload", lambda: {"data": {"repository": {"pullRequest": {
                "commits": {"nodes": []}, "timelineItems": {"pageInfo": {"hasNextPage": False}, "nodes": []}}}}}),
            lambda api: setattr(api, "heads_payload", lambda: {"data": {"repository": {"pullRequest": None}}}),
            lambda api: setattr(api, "force_pushes", [(None, HEAD)]),
        ):
            self.api = FakeGitHub()
            change(self.api)
            self.prepare()
            self.assertFalse(self.reservations())
            self.assertTrue(any("history" in body for body in self.escalations()))

    def test_bad_bot_ledger_goes_to_a_person_instead_of_resetting_budget(self):
        self.api.comments.append(
            {
                "id": 1,
                "body": risk.ROUND_MARKER + "bad -->",
                "user": {"login": "github-actions[bot]", "type": "Bot"},
            }
        )
        self.prepare()
        self.assertFalse(self.reservations())
        self.assertTrue(any("malformed" in body for body in self.escalations()))

    # --- Behind dev (finding 21) ---

    def test_stale_recorded_base_but_current_branch_proceeds(self):
        self.api.pr["base"]["sha"] = OTHER
        self.api.behind = 0
        context = self.prepare()
        self.assertEqual(self.outputs["review"], "true")
        self.assertEqual(context["base"], BASE)

    def test_branch_behind_dev_waits_without_spending_a_review(self):
        self.api.dev_tip = OTHER
        with patch.dict(os.environ, {"POLICY_SHA": OTHER, "GITHUB_SHA": OTHER}):
            self.prepare()
        self.assertFalse(self.reservations())
        self.assertEqual(self.outputs["review"], "false")

    def test_recorded_base_equal_to_the_tip_is_not_proof_the_branch_contains_it(self):
        self.api.behind_override = 41
        self.prepare()
        self.assertTrue(any(r[1].startswith("compare/") for r in self.api.requests))
        self.assertFalse(self.reservations())
        self.assertEqual(self.outputs["review"], "false")
        # The same holds before an approval of an earlier pass.
        self.api = FakeGitHub()
        context = self.passing_review()
        self.api.behind_override = 41
        with self.assertRaisesRegex(risk.PolicyError, "changed"):
            risk.merge(self.api, POLICY, settings(), context, self.api.approver)
        self.assertFalse(self.api.writes("pulls/42/reviews"))

    # --- Who pushed (finding 20) ---

    def test_untrusted_push_on_a_trusted_authors_pr_goes_to_a_person(self):
        self.api.push("1" * 40, actor="someone-else")
        self.api.checks[0]["head_sha"] = "1" * 40
        self.prepare()
        self.assertFalse(self.reservations())
        self.assertEqual(self.outputs["context"], "")
        self.assertTrue(any("not a trusted author" in body for body in self.escalations()))

    def test_push_history_that_has_not_recorded_the_head_waits(self):
        self.api.activity = [{"before": risk.ZERO_SHA, "after": OTHER, "actor": {"login": "trusted-author"},
                              "activity_type": "branch_creation"}]
        self.prepare()
        self.assertFalse(self.reservations())
        self.assertFalse(self.escalations())
        self.assertEqual(self.outputs["review"], "false")

    def test_full_empty_or_anonymous_push_history_goes_to_a_person(self):
        row = {"before": risk.ZERO_SHA, "after": HEAD, "actor": {"login": "trusted-author"},
               "activity_type": "branch_creation"}
        for activity in ([row] * 100, [], [{**row, "actor": None}]):
            with self.subTest(rows=len(activity)):
                self.api = FakeGitHub()
                self.api.activity = activity
                self.prepare()
                self.assertFalse(self.reservations())
                self.assertTrue(self.escalations())

    def test_push_history_must_reach_back_to_the_branch_creation(self):
        newest = {"before": OTHER, "after": HEAD, "actor": {"login": "trusted-author"}, "activity_type": "push"}
        created = {"before": risk.ZERO_SHA, "after": OTHER, "actor": {"login": "trusted-author"},
                   "activity_type": "branch_creation"}
        self.api.activity = [newest, created]
        self.prepare()
        self.assertEqual(self.outputs["review"], "true")
        # The feed starts after the branch was created (aged out, or a renamed
        # branch whose feed started over): earlier pushes cannot be checked.
        self.api = FakeGitHub()
        self.api.activity = [newest]
        self.prepare()
        self.assertFalse(self.reservations())
        self.assertTrue(any("creation" in body for body in self.escalations()))
        # The same check runs again before approval.
        self.api = FakeGitHub()
        context = self.passing_review()
        self.api.activity = [newest, {**created, "activity_type": "push"}]
        with self.assertRaisesRegex(risk.NeedsPerson, "creation"):
            risk.merge(self.api, POLICY, settings(), context, self.api.approver)
        self.assertFalse(self.api.writes("pulls/42/reviews"))

    def test_unavailable_push_history_fails_closed(self):
        self.api.activity = {"message": "unavailable"}
        with self.assertRaisesRegex(risk.PolicyError, "push history"):
            self.prepare()
        self.assertFalse(self.reservations())

    def test_untrusted_push_after_the_pass_stops_the_merge(self):
        context = self.passing_review()
        self.api.activity = [
            {"before": OTHER, "after": HEAD, "actor": {"login": "trusted-author"}, "activity_type": "push"},
            {"before": risk.ZERO_SHA, "after": OTHER, "actor": {"login": "someone-else"},
             "activity_type": "branch_creation"},
        ]
        with self.assertRaisesRegex(risk.NeedsPerson, "not a trusted author"):
            risk.merge(self.api, POLICY, settings(), context, self.api.approver)
        self.assertFalse(self.api.writes("pulls/42/reviews"))
        self.assertFalse(self.api.writes("pulls/42/merge"))

    # --- Merge-time gates ---

    def test_failing_advisory_checks_do_not_block_the_merge(self):
        context = self.passing_review()
        self.api.pr["mergeable_state"] = "unstable"
        risk.merge(self.api, POLICY, settings(), context, self.api.approver)
        self.assertTrue(self.api.writes("pulls/42/merge"))
        for state in ("blocked", "behind", "dirty", "unknown", None):
            with self.subTest(state=state):
                self.api = FakeGitHub()
                context = self.passing_review()
                self.api.pr["mergeable_state"] = state
                with self.assertRaises(risk.PolicyError):
                    risk.merge(self.api, POLICY, settings(), context, self.api.approver)
                self.assertFalse(self.api.writes("pulls/42/merge"))

    def test_post_merge_dispatches_survive_a_label_failure(self):
        context = self.passing_review()
        original = self.api.request

        def flaky(method, route, data=None, **kwargs):
            if method == "POST" and route == "issues/42/labels" and "autopilot-merged" in (data or {}).get("labels", []):
                raise risk.PolicyError("label failed")
            return original(method, route, data, **kwargs)
        self.api.request = flaky
        with patch.object(risk.time, "sleep"), self.assertRaisesRegex(risk.PolicyError, "merged, but"):
            risk.merge(self.api, POLICY, settings(), context, self.api.approver)
        self.assertTrue(self.api.writes("actions/workflows/backend-ci.yml/dispatches"))

    def test_hold_head_base_and_required_check_races_after_approval(self):
        changes = [
            lambda api: api.pr["labels"].append({"name": "autopilot-hold"}),
            lambda api: api.pr["head"].update(sha=OTHER),
            lambda api: api.pr["base"].update(sha=OTHER),
            lambda api: api.checks.append(check(id=99, conclusion="failure")),
            lambda api: api.pr.update(mergeable_state="blocked"),
        ]
        for change in changes:
            self.api = FakeGitHub()
            context = self.passing_review()
            self.api.after_approval = change
            with self.assertRaises(risk.PolicyError):
                risk.merge(self.api, POLICY, settings(), context, self.api.approver)
            self.assertFalse(self.api.writes("pulls/42/merge"))

    def test_existing_change_request_cannot_be_overridden(self):
        context = self.passing_review()
        self.api.reviews = [
            {"id": 5, "user": {"login": "human-reviewer"}, "state": "CHANGES_REQUESTED"}
        ]
        with self.assertRaises(risk.PolicyError):
            risk.merge(self.api, POLICY, settings(), context, self.api.approver)
        self.assertFalse(self.api.writes("pulls/42/reviews"))

    def test_approval_is_idempotent_on_retry(self):
        context = self.passing_review()
        self.api.pr["mergeable_state"] = "blocked"
        # Blocked right after approving may be GitHub catching up: red, retried.
        with self.assertRaises(risk.PolicyError):
            risk.merge(self.api, POLICY, settings(), context, self.api.approver)
        for _ in range(2):
            risk.merge(self.api, POLICY, settings(), context, self.api.approver)
        self.assertEqual(len(self.api.writes("pulls/42/reviews")), 1)

    def merge_blocked_notes(self):
        return [c["body"] for c in self.api.comments if c["body"].startswith(risk.MERGE_BLOCKED_MARKER)]

    def test_blocked_after_an_earlier_approval_tells_the_pr_once_and_keeps_polling(self):
        context = self.passing_review()
        self.api.pr["mergeable_state"] = "blocked"
        with self.assertRaises(risk.PolicyError):
            risk.merge(self.api, POLICY, settings(), context, self.api.approver)
        self.assertFalse(self.merge_blocked_notes())
        # Later sweeps: the approval already existed, so a rule needs a person.
        for _ in range(3):
            self.assertNotIn("check_id", self.prepare())
            risk.merge(self.api, POLICY, settings(), self.outputs["context"], self.api.approver)
        [note] = self.merge_blocked_notes()
        self.assertIn(f"`{HEAD}`", note)
        self.assertIn("`blocked`", note)
        self.assertFalse(self.escalations())
        self.assertFalse(self.api.writes("pulls/42/merge"))
        # A person supplies what the rule needs: the next sweep merges.
        self.api.pr["mergeable_state"] = "clean"
        self.prepare()
        risk.merge(self.api, POLICY, settings(), self.outputs["context"], self.api.approver)
        self.assertTrue(self.api.writes("pulls/42/merge"))
        self.assertEqual(len(self.api.approver.calls), 1)
        # Other states on a later run still fail the run and post nothing.
        for state in ("unknown", "behind", "dirty"):
            with self.subTest(state=state):
                self.api = FakeGitHub()
                context = self.passing_review()
                self.api.pr["mergeable_state"] = state
                for _ in range(2):
                    with self.assertRaises(risk.PolicyError):
                        risk.merge(self.api, POLICY, settings(), context, self.api.approver)
                self.assertFalse(self.merge_blocked_notes())

    def test_a_blocked_note_for_an_older_head_is_updated_not_repeated(self):
        context = self.passing_review()
        self.api.pr["mergeable_state"] = "blocked"
        with self.assertRaises(risk.PolicyError):
            risk.merge(self.api, POLICY, settings(), context, self.api.approver)
        risk.merge(self.api, POLICY, settings(), context, self.api.approver)
        [note] = self.merge_blocked_notes()
        # A new revision is reviewed, approved and blocked the same way.
        self.catch_up("1" * 40, OTHER)
        context = self.prepare()
        self.assertEqual(self.publish(context), "passed")
        with self.assertRaises(risk.PolicyError):
            risk.merge(self.api, POLICY, settings(), context, self.api.approver)
        risk.merge(self.api, POLICY, settings(), context, self.api.approver)
        [note] = self.merge_blocked_notes()
        self.assertIn("`" + "1" * 40 + "`", note)
        self.assertNotIn(f"`{HEAD}`", note)
        [note_id] = [c["id"] for c in self.api.comments if c["body"].startswith(risk.MERGE_BLOCKED_MARKER)]
        self.assertEqual(len(self.api.writes(f"issues/comments/{note_id}")), 1)

    def test_a_refused_approval_posts_one_audit_comment(self):
        for status in (422, 403):
            with self.subTest(status=status):
                self.api = FakeGitHub()
                context = self.passing_review()
                self.api.refuse_approval = status
                for _ in range(3):
                    with self.assertRaisesRegex(risk.PolicyError, "refused the approver App"):
                        risk.merge(self.api, POLICY, settings(), context, self.api.approver)
                audits = [c for c in self.api.comments if c["body"].startswith(risk.APPROVAL_MARKER)]
                self.assertEqual(len(audits), 1)
                self.assertFalse(self.api.reviews)
                self.assertFalse(self.api.writes("pulls/42/merge"))
                # The PR is told once, not only the Actions tab.
                [note] = self.approver_notes()
                self.assertIn(f"GitHub refused the approval (HTTP {status})", note)
                self.assertIn(f"Head: `{HEAD}`.", note)
                # Once GitHub accepts it, the approval and merge go ahead, and
                # the note says it no longer applies.
                self.api.refuse_approval = None
                risk.merge(self.api, POLICY, settings(), context, self.api.approver)
                self.assertTrue(self.api.writes("pulls/42/merge"))
                self.assertEqual(len([c for c in self.api.comments if c["body"].startswith(risk.APPROVAL_MARKER)]), 1)
                [note] = self.approver_notes()
                self.assertTrue(note.startswith(risk.APPROVER_UNAVAILABLE_MARKER + risk.NO_LONGER_APPLIES), note)
        # A server error is not a refusal: it fails as itself, and still posts
        # no second audit comment on the next run.
        self.api = FakeGitHub()
        context = self.passing_review()
        self.api.refuse_approval = 502
        for _ in range(2):
            with self.assertRaises(risk.ApiError) as caught:
                risk.merge(self.api, POLICY, settings(), context, self.api.approver)
            self.assertNotIn("refused", str(caught.exception))
        self.assertEqual(len([c for c in self.api.comments if c["body"].startswith(risk.APPROVAL_MARKER)]), 1)
        # A server error is not the App's setup: no note.
        self.assertFalse(self.approver_notes())

    def test_dismissed_bot_approval_is_a_hold(self):
        context = self.passing_review()
        self.api.pr["mergeable_state"] = "blocked"
        with self.assertRaises(risk.PolicyError):
            risk.merge(self.api, POLICY, settings(), context, self.api.approver)
        self.assertEqual(len(self.api.writes("pulls/42/reviews")), 1)
        self.api.reviews[0]["state"] = "DISMISSED"
        self.api.pr["mergeable_state"] = "clean"
        comments = len(self.api.comments)
        with self.assertRaisesRegex(risk.PolicyError, "dismissed"):
            risk.merge(self.api, POLICY, settings(), context, self.api.approver)
        self.assertEqual(len(self.api.writes("pulls/42/reviews")), 1)
        self.assertEqual(len(self.api.comments), comments)
        self.assertFalse(self.api.writes("pulls/42/merge"))
        # The next run waits, as for autopilot-hold, and says why.
        self.prepare()
        self.assertEqual((self.outputs["review"], self.outputs["context"]), ("false", ""))
        self.assertTrue(any("dismissed" in str(call) for call in self.summary.call_args_list))

    # --- Approver App (approvals never use the workflow token) ---

    def test_approvals_come_only_from_the_approver_app(self):
        context = self.passing_review()
        risk.merge(self.api, POLICY, settings(), context, self.api.approver)
        self.assertEqual([call[:2] for call in self.api.approver.calls], [("POST", "pulls/42/reviews")])
        self.assertEqual(self.api.reviews[0]["user"], {"login": APPROVER, "type": "Bot"})
        # Comments, labels and the merge stay on the workflow token.
        self.assertTrue(self.api.writes("pulls/42/merge"))
        self.assertTrue(all(c["user"]["login"] == "github-actions[bot]" for c in self.api.comments))

    def approver_notes(self):
        return [c["body"] for c in self.api.comments if c["body"].startswith(risk.APPROVER_UNAVAILABLE_MARKER)]

    def assert_nothing_approved(self):
        """No review request, approval audit, approved label or merge."""
        self.assertFalse(self.api.approver.calls)
        self.assertFalse(self.api.reviews)
        self.assertFalse([c for c in self.api.comments if c["body"].startswith(risk.APPROVAL_MARKER)])
        self.assertNotIn("autopilot-approved", {label["name"] for label in self.api.pr["labels"]})
        self.assertFalse(self.api.writes("pulls/42/merge"))

    def test_missing_or_unsafe_approver_identity_tells_the_pr_once_and_approves_nothing(self):
        cases = (("", "did not receive"), ("[bot]", "did not receive"), ("pr-approver", "did not receive"),
                 ("pr approver[bot]", "did not receive"), ("github-actions[bot]", "workflow token's"))
        for login, cause in cases:
            with self.subTest(login=login):
                self.api = FakeGitHub()
                context = self.passing_review()
                for _ in range(3):
                    with self.assertRaisesRegex(risk.PolicyError, "approver-identity"):
                        risk.merge(self.api, POLICY, settings(approver_login=login), context, self.api.approver)
                self.assert_nothing_approved()
                # One note, by the workflow token, across repeated runs.
                [note] = self.approver_notes()
                self.assertIn(cause, note)
                self.assertIn(f"Head: `{HEAD}`.", note)
                self.assertIn("A person may approve or merge this PR", note)
                # True in every case, also when an approval exists under another login.
                self.assertIn("Nothing was merged.", note)
                self.assertNotIn("approved or merged", note)
                posts = [w for w in self.api.writes("issues/42/comments")
                         if w[2]["body"].startswith(risk.APPROVER_UNAVAILABLE_MARKER)]
                self.assertEqual(len(posts), 1)
                self.assertFalse([w for w in self.api.requests if w[0] == "PATCH" and w[1].startswith("issues/comments/")])
        # No token at all (the token step failed): the same note.
        self.api = FakeGitHub()
        context = self.passing_review()
        for _ in range(2):
            with self.assertRaisesRegex(risk.PolicyError, "approver-identity"):
                risk.merge(self.api, POLICY, settings(), context, None)
        self.assert_nothing_approved()
        [note] = self.approver_notes()
        self.assertIn("did not receive", note)

    def test_a_note_that_cannot_be_posted_does_not_hide_the_approver_error(self):
        context = self.passing_review()
        self.failing(lambda m, r, d: m == "POST" and r == "issues/42/comments", 1)
        with self.assertRaisesRegex(risk.PolicyError, "approver-identity"):
            risk.merge(self.api, POLICY, settings(approver_login=""), context, self.api.approver)
        self.assertFalse(self.approver_notes())
        self.assert_nothing_approved()

    def test_merge_step_without_an_app_token_tells_the_pr(self):
        # The workflow wiring when the token step failed (continue-on-error):
        # an empty token and the login "[bot]". main() must still reach merge().
        context = self.passing_review()
        clients = []

        def client(repository, token):
            clients.append(token)
            self.assertEqual((repository, token), (REPO, "workflow-token"))
            return self.api

        environment = {
            "PR_AUTOPILOT": "on", "PR_AUTOPILOT_TRUSTED_AUTHORS": "trusted-author",
            "REVIEW_KEY_PRESENT": "true", "GITHUB_REPOSITORY": REPO, "GH_TOKEN": "workflow-token",
            "APPROVER_TOKEN": "", "APPROVER_LOGIN": "[bot]", "PR_CONTEXT": json.dumps(context),
            "REVIEW_OUTCOME": "passed",
        }
        with (
            patch.dict(os.environ, environment),
            patch.object(risk, "GitHub", side_effect=client),
            patch("sys.argv", ["pr_risk.py", "merge"]),
        ):
            self.assertEqual(risk.main(), 1)
        self.assertEqual(clients, ["workflow-token"])
        [note] = self.approver_notes()
        self.assertIn("did not receive", note)
        self.assert_nothing_approved()

    def test_an_approver_note_that_cannot_be_cleared_does_not_stop_the_merge(self):
        context = self.passing_review()
        self.api.refuse_approval = 422
        with self.assertRaisesRegex(risk.PolicyError, "refused the approver App"):
            risk.merge(self.api, POLICY, settings(), context, self.api.approver)
        self.api.refuse_approval = None
        self.failing(lambda m, r, d: m == "PATCH" and r.startswith("issues/comments/"), 1)
        risk.merge(self.api, POLICY, settings(), context, self.api.approver)
        self.assertTrue(self.api.writes("pulls/42/merge"))
        [note] = self.approver_notes()
        self.assertIn("refused the approval", note)

    def test_approval_recorded_under_another_login_stops_the_merge(self):
        context = self.passing_review()
        self.api.approver.login = "other-app[bot]"
        with self.assertRaisesRegex(risk.PolicyError, "approver-identity"):
            risk.merge(self.api, POLICY, settings(), context, self.api.approver)
        self.assertFalse(self.api.writes("pulls/42/merge"))
        [note] = self.approver_notes()
        self.assertIn("under another login", note)

    def test_only_the_approver_apps_reviews_are_the_autopilots_own(self):
        # A workflow-token approval of this head with the marker is not ours:
        # the App still approves, and its dismissal is not our hold either.
        context = self.passing_review()
        legacy = {"id": 7, "user": {"login": "github-actions[bot]", "type": "Bot"},
                  "state": "APPROVED", "commit_id": HEAD, "body": risk.APPROVAL_MARKER + "\nold"}
        self.api.reviews.append(dict(legacy))
        risk.merge(self.api, POLICY, settings(), context, self.api.approver)
        self.assertEqual(len(self.api.approver.calls), 1)
        self.assertTrue(self.api.writes("pulls/42/merge"))
        self.assertFalse(risk.bot_approval_dismissed([{**legacy, "state": "DISMISSED"}], HEAD, APPROVER))
        self.assertTrue(risk.bot_approval_dismissed(
            [{**legacy, "state": "DISMISSED", "user": {"login": APPROVER, "type": "Bot"}}], HEAD, APPROVER))
        # A person's review that copies the marker is never an App approval.
        person = {**legacy, "user": {"login": APPROVER, "type": "User"}}
        self.assertFalse(risk.approval_review(person, HEAD, "APPROVED", APPROVER))

    def test_dismissed_app_approval_waits_quietly_on_every_sweep(self):
        # prepare cannot know the App's login, so any bot's dismissed autopilot
        # approval of this head makes it wait: green, no gate job, no writes.
        context = self.passing_review()
        self.api.pr["mergeable_state"] = "blocked"
        with self.assertRaises(risk.PolicyError):
            risk.merge(self.api, POLICY, settings(), context, self.api.approver)
        self.api.reviews[0]["state"] = "DISMISSED"
        self.api.pr["mergeable_state"] = "clean"
        comments = len(self.api.comments)
        for _ in range(3):
            self.assertEqual(self.prepare(), "")
            self.assertEqual(self.outputs["review"], "false")
        self.assertEqual(len(self.api.comments), comments)
        self.assertEqual(len(self.api.approver.calls), 1)
        self.assertFalse(self.api.writes("pulls/42/merge"))

    def test_no_stale_approval_dismissal_on_dev_reserves_and_approves_nothing(self):
        context = self.passing_review()
        self.api.rules[1]["parameters"]["dismiss_stale_reviews_on_push"] = False
        with self.assertRaisesRegex(risk.PolicyError, "dismiss stale approvals"):
            risk.merge(self.api, POLICY, settings(), context, self.api.approver)
        self.assertFalse(self.api.approver.calls)
        self.new_head("1" * 40, "another change")
        with self.assertRaisesRegex(risk.PolicyError, "dismiss stale approvals"):
            self.prepare()
        self.assertEqual(len(self.reservations()), 1)

    def test_stats_mismatch_and_stale_policy_checkout_stop_automation(self):
        self.api.pr["additions"] = 2
        self.prepare()
        self.assertFalse(self.reservations())
        self.api.pr = pr()
        with (
            patch.dict(os.environ, {"POLICY_SHA": OTHER}),
            self.assertRaisesRegex(risk.PolicyError, "dev advanced"),
        ):
            self.prepare()
        with (
            patch.dict(os.environ, {"POLICY_SHA": OTHER, "GITHUB_SHA": OTHER}),
            self.assertRaisesRegex(risk.PolicyError, "after checkout"),
        ):
            self.prepare()
        self.assertFalse(self.reservations())

    def test_missing_file_count_cannot_be_classified_low_by_controller(self):
        del self.api.pr["changed_files"]
        self.prepare()
        self.assertFalse(self.reservations())
        self.assertIn(
            "autopilot-high", {label["name"] for label in self.api.pr["labels"]}
        )

    def test_late_hold_while_reading_checks_stops_before_approval(self):
        context = self.passing_review()
        original_pages = self.api.pages

        def pages(route, key=None):
            result = original_pages(route, key)
            if route.endswith("/reviews"):
                self.api.pr["labels"].append({"name": "autopilot-hold"})
            return result

        self.api.pages = pages
        with self.assertRaises(risk.PolicyError):
            risk.merge(self.api, POLICY, settings(), context, self.api.approver)
        self.assertFalse(self.api.writes("pulls/42/reviews"))
        self.assertFalse(self.api.writes("pulls/42/merge"))

    def test_hold_during_second_review_read_stops_before_approval(self):
        context = self.passing_review()
        original_pages = self.api.pages
        reads = 0

        def pages(route, key=None):
            nonlocal reads
            result = original_pages(route, key)
            if route == "pulls/42/reviews":
                reads += 1
                if reads == 2:
                    self.api.pr["labels"].append({"name": "autopilot-hold"})
            return result

        self.api.pages = pages
        with self.assertRaisesRegex(risk.PolicyError, "autopilot-hold"):
            risk.merge(self.api, POLICY, settings(), context, self.api.approver)
        self.assertGreaterEqual(reads, 2)
        self.assertFalse(self.api.writes("pulls/42/reviews"))
        self.assertFalse(self.api.writes("pulls/42/merge"))

    def test_hold_during_approval_audit_stops_before_approval(self):
        context = self.passing_review()
        original_request = self.api.request

        def request(method, route, data=None, **kwargs):
            result = original_request(method, route, data, **kwargs)
            if (
                method == "POST"
                and route == "issues/42/comments"
                and data["body"].startswith(risk.APPROVAL_MARKER)
            ):
                self.api.pr["labels"].append({"name": "autopilot-hold"})
            return result

        self.api.request = request
        with self.assertRaisesRegex(risk.PolicyError, "autopilot-hold"):
            risk.merge(self.api, POLICY, settings(), context, self.api.approver)
        self.assertFalse(self.api.writes("pulls/42/reviews"))
        self.assertFalse(self.api.writes("pulls/42/merge"))

    def test_check_external_id_is_compact_and_contains_no_untrusted_text(self):
        context = self.prepare()
        self.assertEqual(
            "Review reserved: " + self.outputs["reserved"],
            risk.RESERVED_STEP.format(head=context["head"], base=context["base"], patch=context["patch"]),
        )
        external_id = self.reservations()[0][2]["external_id"]
        self.assertLess(len(external_id), 255)
        self.assertEqual(
            set(json.loads(external_id)), {"pr", "head", "base", "run_id", "round", "patch"}
        )

    # --- Sweep (finding 19) ---

    def test_schedule_dispatches_dev_without_evaluating_or_executing_pr_code(self):
        risk.prepare(self.api, POLICY, settings(), {}, "schedule")
        dispatch = self.api.writes(f"actions/workflows/{risk.WORKFLOW}/dispatches")
        self.assertEqual(dispatch[0][2], {"ref": "dev", "inputs": {"pr_number": "42"}})
        self.assertFalse(self.reservations())

    @staticmethod
    def open_pr(number, labels=(), author="trusted-author", **changes):
        item = {**pr(), "number": number, "labels": [{"name": n} for n in labels],
                "user": {"login": author}}
        item.update(changes)
        return item

    def tier_comment(self, number, head):
        """The bot's tier comment as tier_audit writes it, naming `head`."""
        self.api.pr_comments.setdefault(number, []).append({
            "id": 1000 + number,
            "body": f"{risk.TIER_MARKER}\nRisk tier: **MEDIUM**. One human review is needed.\n\nHead: `{head}`. Policy: `.github/pr-autopilot.yml` on dev.",
            "user": {"login": "github-actions[bot]", "type": "Bot"},
        })

    def sweep(self, config=None):
        before = len(self.api.writes(f"actions/workflows/{risk.WORKFLOW}/dispatches"))
        try:
            risk.prepare(self.api, POLICY, config or settings(), {}, "schedule")
        finally:
            sent = [w[2]["inputs"]["pr_number"] for w in self.api.writes(f"actions/workflows/{risk.WORKFLOW}/dispatches")]
            self.sent = sent[before:]
        return self.sent

    def test_sweep_dispatches_only_prs_that_can_act(self):
        open_pr = self.open_pr
        self.api.open_prs = [
            open_pr(1),
            open_pr(2, ["autopilot-low"]),
            open_pr(3, ["autopilot-low"], author="someone-else"),
            open_pr(4, ["autopilot-medium"]),
            open_pr(5, ["autopilot-high"]),
            open_pr(6, ["autopilot-low", "autopilot-hold"]),
            open_pr(7, draft=True),
            open_pr(8, head={"ref": "gone", "sha": HEAD, "repo": None}),
            open_pr(9, head={"ref": "fork", "sha": HEAD, "repo": {"full_name": "fork/project"}}),
        ]
        # Their last tier audits saw the current head.
        for number in (2, 3, 4, 5, 6):
            self.tier_comment(number, HEAD)
        # Trusted LOW first, then the PR to classify.
        self.assertEqual(self.sweep(), ["2", "1"])
        self.assertEqual(self.sweep(settings(authors="")), ["1"])
        self.assertFalse(any(r[1].startswith(("issues/7/", "issues/8/", "issues/9/")) for r in self.api.requests))

    def test_sweep_re_tiers_a_labelled_pr_whose_head_moved_since_its_tier_audit(self):
        # pull_request_target runs may be blocked: the sweep notices the push.
        open_pr = self.open_pr
        self.api.open_prs = [
            open_pr(4, ["autopilot-medium"]),
            open_pr(5, ["autopilot-high"]),
            open_pr(6, ["autopilot-low"], author="someone-else"),
            open_pr(7, ["autopilot-medium", "autopilot-hold"]),
            open_pr(8, ["autopilot-medium"], draft=True),
            open_pr(9, ["autopilot-medium"], head={"ref": "fork", "sha": HEAD, "repo": {"full_name": "fork/project"}}),
        ]
        self.tier_comment(4, OTHER)   # pushed since the audit
        self.tier_comment(5, HEAD)    # audited at this head
        self.tier_comment(7, OTHER)   # a hold does not stop classification
        self.tier_comment(8, OTHER)
        self.tier_comment(9, OTHER)
        # 6 has no tier comment (deleted, or never written): classify it.
        self.assertEqual(self.sweep(), ["4", "6", "7"])
        # The re-tier runs record the current head, so the next sweep is quiet.
        for number in (4, 6, 7):
            self.api.pr_comments[number] = []
            self.tier_comment(number, HEAD)
        self.assertEqual(self.sweep(), [])
        # A person's comment that copies the marker is not the bot's audit.
        self.api.pr_comments[5][0]["user"] = {"login": "someone", "type": "User"}
        self.assertEqual(self.sweep(), ["5"])

    def test_sweep_reads_the_head_that_the_real_tier_audit_recorded(self):
        # Round trip: the tier comment tier_audit writes is the one the sweep parses.
        untrusted = settings(authors="")
        self.prepare(untrusted, event_name="pull_request_target")
        self.assertEqual({label["name"] for label in self.api.pr["labels"]}, {"autopilot-low"})
        self.assertEqual(self.sweep(untrusted), [])
        self.new_head("1" * 40, "a pushed change")
        self.assertEqual(self.sweep(untrusted), ["42"])
        # The re-tier run records the new head, so the next sweep is quiet.
        self.prepare(untrusted)
        self.assertEqual(self.sweep(untrusted), [])

    def test_sweep_continues_past_an_unreadable_tier_audit_and_names_it(self):
        self.api.open_prs = [self.open_pr(3, ["autopilot-medium"]), self.open_pr(4, ["autopilot-medium"]),
                             self.open_pr(5)]
        self.tier_comment(4, OTHER)
        original = self.api.pages

        def pages(route, key=None):
            if route.startswith("issues/3/comments"):
                raise risk.ApiError("GitHub API denied or failed a request (HTTP 502)", 502)
            return original(route, key)

        self.api.pages = pages
        with self.assertRaisesRegex(risk.PolicyError, "tier audit of PR\\(s\\) 3 could not be read"):
            self.sweep()
        self.assertEqual(self.sent, ["4", "5"])

    def test_sweep_dispatches_even_if_the_labels_cannot_be_checked(self):
        self.api.open_prs = [self.open_pr(5)]
        original = self.api.pages

        def pages(route, key=None):
            if route.startswith("labels"):
                raise risk.ApiError("GitHub API denied or failed a request (HTTP 502)", 502)
            return original(route, key)

        self.api.pages = pages
        self.assertEqual(self.sweep(), ["5"])

    def test_sweep_caps_and_rotates_classification_after_trusted_low_prs(self):
        trusted_low = [self.open_pr(n, ["autopilot-low"]) for n in range(1, 31)]
        unlabelled = [self.open_pr(n) for n in range(100, 140)]
        self.api.open_prs = trusted_low + unlabelled
        sent = self.sweep()
        # Every trusted LOW PR, uncapped and first; then at most 25 to classify.
        self.assertEqual(sent[:30], [str(n) for n in range(1, 31)])
        self.assertEqual(len(sent), 30 + risk.CLASSIFY_PER_SWEEP)
        self.assertTrue(set(sent[30:]) <= {str(n) for n in range(100, 140)})
        # A pause before every dispatch but the first.
        self.assertEqual(self.sleep.call_count, len(sent) - 1)
        self.sleep.assert_called_with(risk.DISPATCH_PAUSE_SECONDS)
        # The next sweep takes the next slice, so failing PRs cannot hold the slots.
        first = set(sent[30:])
        with patch.object(risk, "now_utc", return_value=NOW + dt.timedelta(minutes=15)):
            second = set(self.sweep()[30:])
        self.assertTrue(second - first)
        self.assertEqual(first | second, {str(n) for n in range(100, 140)})
        # The labels exist before the classification runs start.
        self.assertEqual({label["name"] for label in self.api.labels}, set(POLICY["labels"].values()))

    def test_sweep_reads_at_most_a_bounded_slice_of_tier_audits(self):
        self.api.open_prs = [self.open_pr(n, ["autopilot-medium"]) for n in range(1, 121)]
        for number in range(1, 121):
            self.tier_comment(number, HEAD)
        self.sweep()
        reads = [r for r in self.api.requests if re.fullmatch(r"issues/\d+/comments.*", r[1])]
        self.assertEqual(len(reads), risk.AUDIT_READS_PER_SWEEP)

    def sweeps_dispatching(self, number):
        """How many of PARKED_SWEEPS consecutive sweeps dispatch PR `number`."""
        sent = 0
        for index in range(risk.PARKED_SWEEPS):
            with patch.object(risk, "now_utc", return_value=NOW + dt.timedelta(minutes=15 * index)):
                sent += self.sweep().count(str(number))
        return sent

    def test_sweep_dispatches_a_pr_waiting_on_a_person_about_hourly(self):
        self.api.pr["labels"] = [{"name": "autopilot-low"}]
        self.assertEqual(self.sweeps_dispatching(42), risk.PARKED_SWEEPS)
        refused = risk.ApproverUnavailable("refused", "GitHub refused the approval (HTTP 422).")
        notes = {
            # The real writers, so the sweep parses what they post.
            "escalation": lambda: risk.escalate(self.api, 42, self.api.comments, HEAD, "A person must review this PR."),
            "merge-blocked": lambda: risk.merge_blocked(self.api, 42, HEAD, "blocked"),
            "approver": lambda: risk.approver_unavailable(self.api, {"pr": 42, "head": HEAD}, refused),
        }
        clear = {
            "escalation": lambda: risk.resolve_escalation(self.api, self.api.comments, {"head": HEAD, "base": BASE}),
            "approver": lambda: risk.clear_note(self.api, 42, risk.APPROVER_UNAVAILABLE_MARKER, "Approved."),
        }
        for name, write in notes.items():
            with self.subTest(note=name):
                self.api.comments = []
                write()
                # Parked: one sweep in PARKED_SWEEPS, not every sweep.
                self.assertEqual(self.sweeps_dispatching(42), 1)
                # A push ends it at once: the note names an older head.
                self.api.pr["head"]["sha"] = OTHER
                self.assertEqual(self.sweeps_dispatching(42), risk.PARKED_SWEEPS)
                self.api.pr["head"]["sha"] = HEAD
                if name in clear:
                    clear[name]()
                    self.assertEqual(self.sweeps_dispatching(42), risk.PARKED_SWEEPS)
        # A dismissed autopilot approval of this head parks it too.
        self.api.comments = []
        dismissed = {"id": 1, "user": {"login": APPROVER, "type": "Bot"}, "state": "DISMISSED",
                     "commit_id": HEAD, "body": risk.APPROVAL_MARKER + "\naudit"}
        self.api.reviews = [dismissed]
        self.assertEqual(self.sweeps_dispatching(42), 1)
        self.api.reviews = [{**dismissed, "commit_id": OTHER}]
        self.assertEqual(self.sweeps_dispatching(42), risk.PARKED_SWEEPS)

    def test_sweep_never_parks_on_a_copied_note_or_unreadable_evidence(self):
        self.api.pr["labels"] = [{"name": "autopilot-low"}]
        # A person's comment that copies the marker and head is not the bot's note.
        self.api.comments = [{"id": 1, "user": {"login": "someone", "type": "User"},
                              "body": f"{risk.ESCALATION_MARKER}\nstop\n\nHead: `{HEAD}`."}]
        self.assertEqual(self.sweeps_dispatching(42), risk.PARKED_SWEEPS)
        # Unreadable comments or reviews: dispatch, never delay on missing evidence.
        risk.escalate(self.api, 42, [], HEAD, "A person must review this PR.")
        for route in ("issues/42/comments", "pulls/42/reviews"):
            with self.subTest(route=route):
                if route == "pulls/42/reviews":
                    # Only the dismissed approval could park it now.
                    self.api.comments = []
                    self.api.reviews = [{"id": 1, "user": {"login": APPROVER, "type": "Bot"},
                                         "state": "DISMISSED", "commit_id": HEAD,
                                         "body": risk.APPROVAL_MARKER + "\naudit"}]
                original = self.api.pages

                def pages(path, key=None, route=route, original=original):
                    if path.startswith(route):
                        raise risk.ApiError("GitHub API denied or failed a request (HTTP 502)", 502)
                    return original(path, key)

                self.api.pages = pages
                self.assertEqual(self.sweeps_dispatching(42), risk.PARKED_SWEEPS)
                self.api.pages = original

    def test_one_failed_dispatch_does_not_stop_the_sweep(self):
        self.api.open_prs = [{**pr(), "number": n} for n in (1, 2, 3)]
        self.api.failing_dispatches = {"1": 502}
        with self.assertRaisesRegex(risk.PolicyError, "PR\\(s\\) 1"):
            self.sweep()
        self.assertEqual(self.sent, ["1", "2", "3"])

    def test_throttled_dispatch_stops_the_sweep(self):
        for status in (403, 429):
            with self.subTest(status=status):
                self.api = FakeGitHub()
                self.api.open_prs = [self.open_pr(1, ["autopilot-low"]), self.open_pr(2, ["autopilot-low"]),
                                     self.open_pr(3)]
                self.api.failing_dispatches = {"1": status}
                with self.assertRaisesRegex(risk.PolicyError, "PR\\(s\\) 1, 2.*throttled"):
                    self.sweep()
                self.assertEqual(self.sent, ["1"])
                self.assertFalse(any(r[1] == "labels" for r in self.api.requests))

    def test_concurrent_runs_creating_the_same_label_do_not_fail(self):
        # Another run created the labels after this run listed them.
        self.api.labels = [{"name": name} for name in POLICY["labels"].values()]
        original = self.api.pages
        listed = []

        def pages(route, key=None):
            if route.startswith("labels") and not listed:
                listed.append(route)
                original(route, key)
                return []
            return original(route, key)

        self.api.pages = pages
        risk.ensure_labels(self.api, POLICY["labels"].values())
        self.assertEqual(len(self.api.labels), len(POLICY["labels"]))
        # A refusal that is not "already exists" still fails.
        self.api = FakeGitHub()
        self.failing(lambda m, r, d: m == "POST" and r == "labels", 1, status=403)
        with self.assertRaises(risk.ApiError):
            risk.ensure_labels(self.api, POLICY["labels"].values())

    def test_dispatch_rejects_wrong_branch_and_shell_text(self):
        with (
            patch.dict(os.environ, {"GITHUB_REF": "refs/heads/main"}),
            self.assertRaises(risk.PolicyError),
        ):
            risk.resolve_number(
                self.api, {"inputs": {"pr_number": "42"}}, "workflow_dispatch"
            )
        with self.assertRaises(risk.PolicyError):
            risk.resolve_number(
                self.api, {"inputs": {"pr_number": "42;false"}}, "workflow_dispatch"
            )

    # --- Workflow and controller agree ---

    def test_workflow_expressions_survive_yaml_parsing(self):
        # An unquoted " #" starts a YAML comment: GitHub then rejects the file
        # and no autopilot run happens at all.
        run_name = next(line for line in WORKFLOW_TEXT.splitlines() if line.startswith("run-name:"))
        self.assertTrue(run_name.startswith('run-name: "${{') and run_name.endswith('}}"'), run_name)
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML is not installed")
        workflow = yaml.safe_load(WORKFLOW_TEXT)
        self.assertIn("format('PR autopilot #{0}', github.event.pull_request.number)", workflow["run-name"])
        self.assertTrue(workflow["run-name"].endswith("}}"))

    def test_workflow_outcome_markers_match_the_controller(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML is not installed")
        steps = yaml.safe_load(WORKFLOW_TEXT)["jobs"]["review-gate-and-merge"]["steps"]
        names = [step.get("name") for step in steps]
        publish = next(step for step in steps if step.get("id") == "publish")
        self.assertEqual(publish["if"], "fromJSON(needs.prepare.outputs.context).check_id")
        self.assertIn("python3 -I scripts/pr_risk.py publish", publish["run"])
        context = "${{{{ fromJSON(needs.prepare.outputs.context).{} }}}}"
        for outcome, template in risk.OUTCOME_STEPS.items():
            with self.subTest(outcome=outcome):
                name = template.format(head=context.format("head"), base=context.format("base"),
                                       patch=context.format("patch"))
                self.assertIn(name, names)
                step = steps[names.index(name)]
                self.assertGreater(names.index(name), steps.index(publish))
                self.assertEqual(step["if"], f"steps.publish.outputs.outcome == '{outcome}'")
                self.assertEqual(step["run"], "true")
        prepare = yaml.safe_load(WORKFLOW_TEXT)["jobs"]["prepare"]["steps"]
        policy = next(step for step in prepare if step.get("id") == "policy")
        reserved = prepare[prepare.index(policy) + 1]
        self.assertEqual(risk.RESERVED_STEP, "Review reserved: " + risk.RESERVATION)
        self.assertEqual(reserved["name"], "Review reserved: ${{ steps.policy.outputs.reserved }}")
        self.assertEqual(reserved["if"], "always() && steps.policy.outputs.reserved != ''")
        self.assertEqual(reserved["run"], "true")
        not_started = prepare[prepare.index(policy) + 2]
        self.assertEqual("Review retryable: " + risk.RESERVATION, risk.OUTCOME_STEPS["retryable"])
        self.assertEqual(not_started["name"], "Review retryable: ${{ steps.policy.outputs.reserved }}")
        self.assertEqual(
            not_started["if"],
            "always() && steps.policy.outputs.reserved != '' && steps.policy.outputs.review != 'true'",
        )
        self.assertEqual(not_started["run"], "true")
        self.assertEqual(len(prepare), prepare.index(policy) + 3)
        # The gate job ends with the markers and hands the outcome on.
        jobs = yaml.safe_load(WORKFLOW_TEXT)["jobs"]
        self.assertTrue(names[-1].startswith("Review retryable: "))
        self.assertEqual(jobs["review-gate-and-merge"]["outputs"], {
            "outcome": "${{ steps.publish.outputs.outcome }}",
            "reused": "${{ !fromJSON(needs.prepare.outputs.context).check_id }}",
        })
        approving = jobs["approve-and-merge"]
        merge = approving["steps"][-1]
        self.assertIn("python3 -I scripts/pr_risk.py merge", merge["run"])
        self.assertNotIn("if", merge)
        # Only after a pass published in this run, or for a reused pass.
        self.assertTrue(approving["if"].endswith(
            "(needs.review-gate-and-merge.outputs.outcome == 'passed' || "
            "needs.review-gate-and-merge.outputs.reused == 'true')"), approving["if"])
        self.assertEqual(merge["env"]["REVIEW_OUTCOME"], "${{ needs.review-gate-and-merge.outputs.outcome }}")
        self.assertNotIn("pr_risk.py finish", WORKFLOW_TEXT)


class WorkflowContractTests(unittest.TestCase):
    def test_workflow_separates_untrusted_tests_read_only_review_and_privileged_merge(
        self,
    ):
        text = (
            Path(__file__).resolve().parents[1]
            / ".github/workflows/low-risk-pr-autopilot.yml"
        ).read_text()
        review = text.split("  ai-review:", 1)[1].split("  review-gate-and-merge:", 1)[
            0
        ]
        self.assertNotIn(": write", review)
        self.assertIn("anthropics/claude-code-action@dde2242db6af13460b916652159b6ba19a598f30 # v1.0.120", review)
        self.assertIn('--tools ""', review)
        self.assertIn('--disallowedTools "mcp__*"', review)
        self.assertNotIn("pull_request.head.sha", text)
        self.assertIn("AI_VERDICT: ${{ needs.ai-review.outputs.verdict }}", text)
        self.assertIn("PR Autopilot review", text)
        self.assertIn("persist-credentials: false", text)

    def test_all_privileged_jobs_and_ai_job_reject_partial_or_full_reruns(self):
        for name in ("prepare", "ai-review", "review-gate-and-merge", "approve-and-merge"):
            job = workflow_job(name)
            condition = job.split("    if:", 1)[1].split("    runs-on:", 1)[0]
            self.assertIn("github.run_attempt == '1'", condition)
        self.assertIn(
            "needs.prepare.outputs.review == 'true'", workflow_job("ai-review")
        )

    def test_only_the_approving_step_gets_the_approver_app_key(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML is not installed")
        workflow = yaml.safe_load(WORKFLOW_TEXT)
        # The job with the outcome markers names no environment: an environment
        # that refuses or holds a job would stop the markers and bar the diff.
        for name in ("policy-tests", "prepare", "ai-review", "review-gate-and-merge"):
            with self.subTest(job=name):
                self.assertNotIn("environment", workflow["jobs"][name])
                self.assertNotIn("AUTOPILOT_APPROVER", workflow_job(name))
                self.assertNotIn("APPROVER_", workflow_job(name))
        self.assertTrue(any(str(step.get("name", "")).startswith("Review passed: ")
                            for step in workflow["jobs"]["review-gate-and-merge"]["steps"]))
        approving = workflow["jobs"]["approve-and-merge"]
        self.assertEqual(approving["environment"], "pr-autopilot")
        # It starts only once the gate job finished, so the verdict is recorded
        # first; always() keeps a skipped ai-review (a reused pass) from skipping it.
        self.assertEqual(approving["needs"], ["prepare", "review-gate-and-merge"])
        for part in ("always() && !cancelled()", "needs.prepare.result == 'success'",
                     "needs.review-gate-and-merge.result == 'success'"):
            self.assertIn(part, approving["if"])
        # Evaluated on every run, including sweeps with no context: it must
        # never parse the context, or every such run would fail.
        self.assertNotIn("fromJSON", approving["if"])
        steps = approving["steps"]
        self.assertFalse(any(str(step.get("name", "")).startswith("Review ") for step in steps))
        mint = next(step for step in steps if step.get("id") == "approver")
        merge = steps[-1]
        self.assertEqual(steps.index(mint), len(steps) - 2)
        # A failed token step must not skip the merge step, which tells the PR.
        self.assertIs(mint["continue-on-error"], True)
        self.assertNotIn("if", mint)
        self.assertEqual(mint["uses"], "actions/create-github-app-token@64ab729a6294000f5aa4a4bc0b73d1f32bc45510")
        self.assertEqual(mint["with"], {
            "client-id": "${{ vars.AUTOPILOT_APPROVER_CLIENT_ID }}",
            "private-key": "${{ secrets.AUTOPILOT_APPROVER_PRIVATE_KEY }}",
            "permission-pull-requests": "write",
        })
        self.assertEqual(merge["env"]["APPROVER_TOKEN"], "${{ steps.approver.outputs.token }}")
        self.assertEqual(merge["env"]["APPROVER_LOGIN"], "${{ steps.approver.outputs.app-slug }}[bot]")
        self.assertEqual(merge["env"]["GH_TOKEN"], "${{ github.token }}")
        for step in steps[:-1]:
            self.assertNotIn("APPROVER_TOKEN", step.get("env") or {})
        self.assertEqual(WORKFLOW_TEXT.count("secrets.AUTOPILOT_APPROVER_PRIVATE_KEY"), 1)

    def test_review_and_merge_jobs_run_only_in_dispatch_runs_on_dev(self):
        for name in ("ai-review", "review-gate-and-merge", "approve-and-merge"):
            condition = workflow_job(name).split("    if:", 1)[1].split("    runs-on:", 1)[0]
            self.assertIn("github.event_name == 'workflow_dispatch'", condition)
            self.assertIn("github.ref == 'refs/heads/dev'", condition)

    def test_multiline_output_has_collision_safe_delimiter(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "out"
            with patch.dict(os.environ, {"GITHUB_OUTPUT": str(path)}):
                risk.emit("data", "line\nEOF\nmore=value")
            lines = path.read_text().splitlines()
            delimiter = lines[0].split("<<")[1]
            self.assertEqual(lines[-1], delimiter)
            self.assertNotIn(delimiter, lines[1:-1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
