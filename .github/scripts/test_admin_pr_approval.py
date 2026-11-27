#!/usr/bin/env python3
"""Stdlib-only tests for .github/workflows/admin-pr-approval.yml.

The approval must come from the approver App token, minted in the
devflow-approver environment, and never from GITHUB_TOKEN. Each step's
script runs here with a fake gh.
"""

import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parent.parent / "workflows" / "admin-pr-approval.yml"
REPO = "org/repo"
HEAD = "a" * 40


def text() -> str:
    return WORKFLOW.read_text()


def job(name: str) -> str:
    """The text of one job, up to the next job."""
    body = text().split(f"\n  {name}:\n", 1)[1]
    return re.split(r"\n  [a-z][a-z-]*:\n", body, maxsplit=1)[0]


def step_script(job_name: str, step_name: str) -> str:
    step = job(job_name).split(f"- name: {step_name}\n", 1)[1]
    step = re.split(r"\n      - ", step, maxsplit=1)[0]
    return textwrap.dedent(step.split("run: |\n", 1)[1])


def run(script: str, env: dict, gh_cases: str = "") -> tuple[subprocess.CompletedProcess, list, str]:
    """Run a step's script with a fake gh; return (process, gh calls, GITHUB_OUTPUT)."""
    with tempfile.TemporaryDirectory() as tmp:
        log, output = Path(tmp) / "calls", Path(tmp) / "output"
        log.touch()
        output.touch()
        fake = Path(tmp) / "gh"
        fake.write_text(f'#!/usr/bin/env bash\necho "$*" >> "{log}"\ncase "$*" in\n{gh_cases}\nesac\n')
        fake.chmod(0o755)
        done = subprocess.run(
            ["bash", "-c", script],
            env={"PATH": f"{tmp}:/usr/bin:/bin", "GITHUB_OUTPUT": str(output), **env},
            capture_output=True,
            text=True,
            check=False,
        )
        return done, log.read_text().splitlines(), output.read_text()


class TokenTests(unittest.TestCase):
    def test_github_token_cannot_write(self):
        # No job may give GITHUB_TOKEN a write scope, so it cannot be the fallback.
        # The App token's own scope is an action input: permission-<scope>: write.
        grants = re.findall(r"^\s+([a-z-]+):\s*write\b", text(), re.M)
        self.assertEqual([g for g in grants if not g.startswith("permission-")], [])

    def test_the_approval_uses_the_app_token_in_the_main_only_environment(self):
        approve = job("approve")
        self.assertIn("name: devflow-approver", approve)
        self.assertIn("GH_TOKEN: ${{ steps.approver.outputs.token }}", approve)
        self.assertNotIn("github.token", approve)
        self.assertIn("needs.admin.outputs.approve == 'true'", approve)

    def test_every_action_is_pinned_by_full_commit_sha(self):
        uses = re.findall(r"uses:\s*(\S+)", text())
        self.assertTrue(uses)
        for ref in uses:
            with self.subTest(uses=ref):
                self.assertRegex(ref, r"@[0-9a-f]{40}$")

    def test_the_key_check_runs_before_the_token_is_minted(self):
        approve = job("approve")
        self.assertLess(approve.index("Fail without the approver App key"), approve.index("create-github-app-token"))


class KeyCheckTests(unittest.TestCase):
    script = staticmethod(lambda: step_script("approve", "Fail without the approver App key"))

    def test_a_missing_secret_fails_loudly(self):
        for env in ({}, {"CLIENT_ID": "1"}, {"PRIVATE_KEY": "k"}, {"CLIENT_ID": "", "PRIVATE_KEY": ""}):
            with self.subTest(env=env):
                done, _, _ = run(self.script(), env)
                self.assertEqual(done.returncode, 1)
                self.assertIn("::error::", done.stdout)
                self.assertIn("never approves with GITHUB_TOKEN", done.stdout)

    def test_both_secrets_pass(self):
        done, _, _ = run(self.script(), {"CLIENT_ID": "1", "PRIVATE_KEY": "k"})
        self.assertEqual(done.returncode, 0, done.stdout)


class AdminCheckTests(unittest.TestCase):
    def check(self, author, sender, permissions, base="dev", head_ref="feat/x"):
        cases = "\n".join(f'  *"/collaborators/{login}/permission"*) echo {p} ;;' for login, p in permissions.items())
        env = {"GH_TOKEN": "x", "REPO": REPO, "AUTHOR": author, "SENDER": sender, "BASE": base, "HEAD_REF": head_ref}
        done, _, output = run(step_script("admin", "Check that the author and the sender are repository admins"), env, cases)
        self.assertEqual(done.returncode, 0, done.stderr)
        return output

    def test_admin_author_and_sender_approve(self):
        self.assertIn("approve=true", self.check("boss", "boss", {"boss": "admin"}))

    def test_main_approves_only_an_admin_hotfix_branch(self):
        admin = {"boss": "admin"}
        self.assertIn("approve=true", self.check("boss", "boss", admin, "main", "fix/frontend-hotfix-login"))
        self.assertEqual(self.check("boss", "boss", admin, "main", "feat/new-page"), "")
        self.assertEqual(self.check("dev", "dev", {"dev": "write"}, "main", "hotfix/x"), "")

    def test_main_and_dev_both_trigger(self):
        self.assertIn("branches: [dev, main]", text())

    def test_the_admin_rule_runs_in_every_mode(self):
        # Devflow mode switched it off on dev once, and the devflow job then
        # withdrew every queued admin approval.
        self.assertNotIn("APPROVAL_MODE", job("admin"))
        self.assertIn("github.event.pull_request.base.ref == 'dev'", job("devflow"))

    def test_a_non_admin_author_or_sender_does_not(self):
        self.assertEqual(self.check("dev", "dev", {"dev": "write"}), "")
        self.assertEqual(self.check("boss", "dev", {"boss": "admin", "dev": "write"}), "")


class ApproveTests(unittest.TestCase):
    def approve(self, current):
        cases = f'  *"/pulls/1 --jq .head.sha"*) echo {current} ;;\n  *"-X POST"*) echo approved ;;'
        env = {"GH_TOKEN": "app", "REPO": REPO, "PR": "1", "HEAD_SHA": HEAD}
        done, calls, _ = run(step_script("approve", "Approve the head commit"), env, cases)
        self.assertEqual(done.returncode, 0, done.stderr)
        return [c for c in calls if "-X POST" in c]

    def test_approves_the_event_head(self):
        posted = self.approve(HEAD)
        self.assertEqual(len(posted), 1)
        self.assertIn(f"commit_id={HEAD}", posted[0])
        self.assertIn("event=APPROVE", posted[0])

    def test_a_moved_head_is_not_approved(self):
        self.assertEqual(self.approve("b" * 40), [])


if __name__ == "__main__":
    unittest.main()
