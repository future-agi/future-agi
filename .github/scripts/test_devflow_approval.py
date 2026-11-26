#!/usr/bin/env python3
"""Stdlib-only tests for devflow_approval.decide(); every rule has a fixture."""

import copy
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci_scope  # noqa: E402
import devflow_approval as da  # noqa: E402

REPO = "org/repo"
HEAD = "a" * 40
OLD = "b" * 40
MERGE_BASE = "c" * 40
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
        "tier_check": {"tier": "low", "engine": "second-model", "input": "diff-only"},
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
            "base": {"ref": "dev", "sha": OLD, "repo": {"full_name": REPO}},
            "labels": [{"name": "devflow:reviewed"}],
        }
        self.reviews = [review()]
        self.comments = [marker()]
        self.permissions = {"author": "write", "rev": "write"}
        self.scan = []
        # For main(): the pulls/{n}/files items and {(path, sha): text}.
        self.files = []
        self.texts = {}

    def decide(self, tight=False, observe=False):
        return da.decide(
            self.pr,
            self.reviews,
            self.comments,
            self.permissions,
            tight,
            observe,
            self.scan,
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

    def test_needs_person_other_than_false_makes_a_low_tier_medium(self):
        # Contract v2: no bot approval and no needs-human; a person approves.
        for value in (True, None, "false", 0):
            with self.subTest(value=value):
                case = Case()
                case.reviews = [review(needs_person=value)]
                self.assertEqual(self.check(case, "wait", "needs_person")["tier"], "medium")

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
    def test_tight_mode_makes_a_low_tier_medium_and_never_needs_human(self):
        result = self.check(Case(), "wait", "APPROVAL_MODE_TIGHT", tight=True)
        self.assertEqual(result["tier"], "medium")
        case = Case()
        case.reviews.append(review("lead", state="APPROVED", body="ok", at="2026-11-26T11:00:00Z"))
        case.permissions["lead"] = "write"
        self.check(case, "train", "approved on the head by lead", tight=True)
        # Tight never lowers a high tier.
        case.reviews[0] = review(tier="high")
        self.check(case, "needs-human", "tier is", tight=True)

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


WORKFLOW = CODEOWNERS.parent / "workflows" / "devflow-approval.yml"


def run_main(mode, case, author_permission="write"):
    """(exit code, gh calls, the decision main() printed) with a fake gh."""
    calls, printed = [], []
    real_gh = da.gh
    args = ["--mode", "devflow", "--dry-run"] if mode == "dry" else ["--mode", mode]

    def fake(*args, stdin=None):
        calls.append(args)
        if args[:2] == ("api", f"repos/{REPO}/pulls/1"):
            return json.dumps(case.pr)
        if "--paginate" in args:
            items = {
                "/reviews?per_page=100": case.reviews,
                "/files?per_page=100": case.files,
            }.get(args[2][args[2].rindex("/"):], case.comments)
            return "\n".join(json.dumps(item) for item in items)
        if "/compare/" in args[1]:
            return MERGE_BASE + "\n"
        if "/contents/" in args[-1]:
            path, ref = args[-1].split("/contents/", 1)[1].split("?ref=")
            text = case.texts.get((urllib.parse.unquote(path), ref))
            if text is None:
                raise subprocess.CalledProcessError(1, ["gh"], stderr="gh: Not Found (HTTP 404)")
            return text
        if args[1].endswith("/collaborators/author/permission"):
            return author_permission + "\n"
        return "write\n"

    da.gh = fake
    da.print = lambda *a, **k: printed.append(" ".join(map(str, a)))
    try:
        code = da.main(["--repo", REPO, "--pr", "1", *args])
    finally:
        da.gh = real_gh
        del da.print
    decision = json.loads(next(p for p in printed if p.startswith("{")))
    return code, calls, decision


class ObserveModeTests(unittest.TestCase):
    """observe = the admin rule exactly, plus the devflow decision in the log."""

    def admin_step(self):
        text = WORKFLOW.read_text()
        job = text.split("\n  admin:\n", 1)[1].split("\n  devflow:\n", 1)[0]
        return job, textwrap.dedent(job.split("        run: |\n", 1)[1])

    def run_admin(self, author, sender="", permissions=None):
        """Run the admin job's script with a fake gh; return the approvals it posted."""
        _, script = self.admin_step()
        permissions = permissions or {}
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "calls"
            fake = Path(tmp) / "gh"
            cases = "\n".join(
                f'  *"/collaborators/{login}/permission"*) echo {perm} ;;' for login, perm in permissions.items()
            )
            fake.write_text(
                "#!/usr/bin/env bash\n"
                f'echo "$*" >> "{log}"\n'
                'case "$*" in\n'
                f"{cases}\n"
                '  *"/collaborators/"*) echo write ;;\n'
                f'  *"/pulls/1 --jq .head.sha"*) echo {HEAD} ;;\n'
                '  *"--paginate"*) ;;\n'
                '  *"-X POST"*) echo "approved" ;;\n'
                "esac\n"
            )
            fake.chmod(0o755)
            env = {
                "PATH": f"{tmp}:/usr/bin:/bin",
                "GH_TOKEN": "x", "REPO": REPO, "PR": "1",
                "AUTHOR": author, "SENDER": sender or author, "HEAD_SHA": HEAD,
            }
            done = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, check=False)
            self.assertEqual(done.returncode, 0, done.stderr)
            return [line for line in log.read_text().splitlines() if "-X POST" in line]

    def test_the_admin_job_runs_in_observe_mode_and_not_in_devflow_mode(self):
        job, _ = self.admin_step()
        self.assertIn("vars.APPROVAL_MODE != 'devflow' &&", job)
        devflow = WORKFLOW.read_text().split("\n  devflow:\n", 1)[1]
        self.assertIn("(vars.APPROVAL_MODE == 'devflow' || vars.APPROVAL_MODE == 'observe')", devflow)
        self.assertIn('--mode "$APPROVAL_MODE"', devflow)

    def test_observe_approves_an_admin_pr_as_admin_mode_does(self):
        posted = self.run_admin("boss", permissions={"boss": "admin"})
        self.assertEqual(len(posted), 1)
        self.assertIn(f"commit_id={HEAD}", posted[0])
        self.assertIn("event=APPROVE", posted[0])

    def test_observe_never_approves_a_non_admin_pr(self):
        # Neither rule: the admin script stops, and the devflow job writes nothing.
        self.assertEqual(self.run_admin("dev", permissions={"dev": "write"}), [])
        self.assertEqual(self.run_admin("boss", "dev", {"boss": "admin", "dev": "write"}), [])
        code, calls, decision = run_main("observe", Case())
        self.assertEqual((code, decision["action"]), (0, "approve"))
        self.assertEqual([c for c in calls if "-X" in c], [])

    def test_the_logged_decision_is_what_devflow_mode_would_do(self):
        for name, change in [
            ("approve", lambda c: None),
            ("high tier", lambda c: setattr(c, "reviews", [review(tier="high")])),
            ("no review", lambda c: setattr(c, "reviews", [])),
            ("draft", lambda c: c.pr.update(draft=True)),
        ]:
            with self.subTest(case=name):
                case = Case()
                change(case)
                _, _, observed = run_main("observe", case)
                _, _, devflow = run_main("dry", case)
                self.assertEqual(observed, devflow)

    def test_observe_still_decides_a_head_the_admin_rule_approved(self):
        case = Case()
        case.reviews.append(review("github-actions[bot]", state="APPROVED", body="Approved automatically."))
        _, _, observed = run_main("observe", case)
        self.assertEqual(observed["action"], "approve")

    def test_devflow_mode_approves_then_admits_to_the_train(self):
        code, calls, decision = run_main("devflow", Case())
        writes = [c for c in calls if "-X" in c]
        self.assertEqual((code, decision["action"]), (0, "approve"))
        self.assertIn("event=APPROVE", writes[0])
        self.assertEqual(writes[1][-1], "labels[]=devflow:train")


class TierVoteTests(unittest.TestCase):
    """Three votes: the reviewer's tier, the diff-only tier_check, the scan."""

    def decide(self, scan=(), **verdict):
        case = Case()
        case.reviews = [review(**verdict)]
        case.scan = list(scan)
        return case.decide()

    def test_three_low_votes_approve_and_are_reported(self):
        result = self.decide()
        self.assertEqual(result["action"], "approve")
        self.assertEqual(result["votes"], {"reviewer": "low", "tier_check": "low", "scan": "low"})

    def test_a_missing_tier_check_counts_as_high(self):
        for value in (None, "low", {}):
            with self.subTest(tier_check=value):
                result = self.decide(tier_check=value)
                self.assertEqual(result["action"], "needs-human")
                self.assertIn("tier_check is missing, so it counts as high", result["reasons"])

    def test_a_high_tier_check_alone_needs_a_human(self):
        result = self.decide(tier_check={"tier": "high", "engine": "e2", "input": "diff-only"})
        self.assertEqual(result["action"], "needs-human")
        self.assertEqual(result["votes"]["tier_check"], "high")
        self.assertEqual(result["reasons"], ["tier_check tier is 'high' (engine e2)"])

    def test_a_tier_check_that_saw_more_than_the_diff_counts_as_high(self):
        result = self.decide(tier_check={"tier": "low", "engine": "e2", "input": "diff+review"})
        self.assertEqual(result["action"], "needs-human")
        self.assertIsNone(result["votes"]["tier_check"])

    def test_a_scan_hit_alone_needs_a_human(self):
        result = self.decide(scan=["a/migrations/0002_x.py adds RemoveField"])
        self.assertEqual(result["action"], "needs-human")
        self.assertEqual(result["votes"]["scan"], "high")
        self.assertEqual(result["reasons"], ["scan: a/migrations/0002_x.py adds RemoveField"])

    def test_observe_logs_all_three_votes(self):
        case = Case()
        case.reviews = [review(tier="high", tier_check={"tier": "low", "input": "diff-only"})]
        case.files = [{"filename": "go.mod", "status": "modified", "patch": "+require x v1"}]
        case.texts = {("go.mod", MERGE_BASE): "module m\n", ("go.mod", HEAD): "module m\nrequire x v1\n"}
        with tempfile.TemporaryDirectory() as tmp:
            summary = Path(tmp) / "summary"
            os.environ["GITHUB_STEP_SUMMARY"] = str(summary)
            try:
                _, _, decision = run_main("observe", case)
            finally:
                del os.environ["GITHUB_STEP_SUMMARY"]
            text = summary.read_text()
        self.assertEqual(decision["votes"], {"reviewer": "high", "tier_check": "low", "scan": "high"})
        self.assertEqual(decision["tier"], "high")
        self.assertIn("Tier: 'high' (votes: reviewer 'high', tier_check 'low', scan 'high')", text)
        self.assertIn("scan: go.mod changes require/replace", text)


def approval(login, commit=HEAD, at="2026-11-26T11:00:00Z", state="APPROVED"):
    return review(login, commit=commit, at=at, state=state, body="looks fine")


class TierContractTests(unittest.TestCase):
    """tier contract v2: ordinal low < medium < high; unknown is high."""

    def case(self, *extra, **verdict):
        case = Case()
        case.reviews = [review(**verdict), *extra]
        case.permissions.update(lead="write", reader="read")
        return case

    def test_medium_is_never_auto_approved_and_never_treated_as_low(self):
        medium_check = {"tier": "medium", "engine": "e2", "input": "diff-only"}
        for verdict in ({"tier": "medium"}, {"tier_check": medium_check}):
            with self.subTest(verdict=verdict):
                waiting = self.case(**verdict).decide()
                self.assertEqual((waiting["action"], waiting["tier"]), ("wait", "medium"))
                approved = self.case(approval("lead"), **verdict).decide()
                self.assertEqual((approved["action"], approved["tier"]), ("train", "medium"))
        # Only a person with write access, not the author, approving this head.
        for who, commit, perm in (("author", HEAD, "write"), ("reader", HEAD, "read"), ("lead", OLD, "write")):
            with self.subTest(approver=who, commit=commit):
                case = self.case(approval(who, commit), tier="medium")
                case.permissions[who] = perm
                self.assertEqual(case.decide()["action"], "wait")
        withdrawn = self.case(approval("lead"), approval("lead", at="2026-11-26T11:30:00Z", state="CHANGES_REQUESTED"), tier="medium")
        self.assertEqual(withdrawn.decide()["action"], "wait")
        # main(): a medium approval adds devflow:train and posts no bot approval.
        code, calls, decision = run_main("devflow", self.case(approval("lead"), tier="medium"))
        writes = [c for c in calls if "-X" in c]
        self.assertEqual((code, decision["action"]), (0, "train"))
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][-1], "labels[]=devflow:train")
        self.assertFalse(any("event=APPROVE" in c for c in writes[0]))

    def test_an_unknown_tier_string_is_high(self):
        for tier in ("Medium", "", None, "critical", "LOW", ["low"]):
            with self.subTest(tier=tier):
                result = self.case(approval("lead"), tier=tier).decide()
                self.assertEqual((result["action"], result["tier"]), ("needs-human", "high"))
                check = {"tier": tier, "engine": "e2", "input": "diff-only"}
                self.assertEqual(self.case(tier_check=check).decide()["tier"], "high")

    def test_the_final_tier_is_the_ordinal_max(self):
        for votes, expected in (
            (("low", "medium"), "medium"),
            (("medium", "high"), "high"),
            (("medium", "low", "low"), "medium"),
            (("low", "low"), "low"),
            (("low", "critical"), "high"),
        ):
            with self.subTest(votes=votes):
                self.assertEqual(da.max_tier(*votes), expected)
        result = self.case(tier="medium", tier_check={"tier": "high", "input": "diff-only"}).decide()
        self.assertEqual((result["action"], result["tier"]), ("needs-human", "high"))


def scan(*files, base=None, head=None):
    texts = {"base": base or {}, "head": head or {}}
    return da.risk_scan(list(files), lambda path, side: texts[side].get(path))


def patch(*lines):
    return "@@ -1,0 +1,9 @@\n" + "\n".join(lines)


MIGRATION = "futureagi/tracer/migrations/0121_x.py"
CH = "futureagi/tracer/services/clickhouse/v2/schema/039_x.sql"


class RiskScanTests(unittest.TestCase):
    def test_destructive_django_operations_hit(self):
        for op in ("RemoveField", "DeleteModel", "RenameField", "RenameModel", "RunSQL", "RunPython"):
            with self.subTest(op=op):
                hits = scan({"filename": MIGRATION, "status": "added", "patch": patch(f"+        migrations.{op}(")})
                self.assertEqual(hits, [f"{MIGRATION} adds {op}"])

    def test_an_additive_migration_is_low(self):
        body = patch("+        migrations.AddField(", "+            model_name='span',", " # RunPython stays")
        self.assertEqual(scan({"filename": MIGRATION, "status": "added", "patch": body}), [])

    def test_only_added_lines_count(self):
        body = patch("-        migrations.RunPython(noop),", "+        migrations.AddField(")
        self.assertEqual(scan({"filename": MIGRATION, "status": "modified", "patch": body}), [])

    def test_a_migration_without_a_patch_is_read_whole(self):
        item = {"filename": MIGRATION, "status": "added"}
        self.assertEqual(scan(item, head={MIGRATION: "migrations.RunSQL('x')"}), [f"{MIGRATION} adds RunSQL"])
        self.assertEqual(scan(item), [f"{MIGRATION} could not be read"])

    def test_deleted_or_renamed_migrations_hit(self):
        self.assertEqual(scan({"filename": MIGRATION, "status": "removed"}), [f"{MIGRATION} is deleted"])
        hits = scan(
            {"filename": MIGRATION, "status": "renamed", "previous_filename": "a/migrations/0001.py"},
            head={MIGRATION: "migrations.AddField("},
        )
        self.assertEqual(hits, [f"{MIGRATION} is renamed from a/migrations/0001.py"])

    def test_clickhouse_destructive_statements_hit(self):
        for sql, what in [
            ("+DROP TABLE IF EXISTS spans;", "DROP"),
            ("+ALTER TABLE spans RENAME COLUMN a TO b;", "RENAME"),
            ("+ALTER TABLE spans\n+    DELETE WHERE x = 1;", "ALTER TABLE ... DELETE"),
            ("+alter table spans update x = 1 where y;", "ALTER TABLE ... UPDATE"),
            ("+TRUNCATE TABLE spans;", "TRUNCATE"),
        ]:
            with self.subTest(sql=sql):
                self.assertEqual(scan({"filename": CH, "status": "added", "patch": patch(sql)}), [f"{CH} adds {what}"])

    def test_additive_clickhouse_and_comments_are_low(self):
        body = patch("+-- never DROP this table", "+ALTER TABLE spans ADD COLUMN IF NOT EXISTS x String;")
        self.assertEqual(scan({"filename": CH, "status": "added", "patch": body}), [])
        # Not a migration directory.
        self.assertEqual(scan({"filename": "futureagi/scripts/x.sql", "status": "added", "patch": patch("+DROP TABLE x;")}), [])

    def test_package_json_dependency_changes_hit(self):
        old = json.dumps({"scripts": {"a": "x"}, "dependencies": {"react": "18.3.1"}})
        new = json.dumps({"scripts": {"a": "x"}, "dependencies": {"react": "19.0.0"}, "devDependencies": {"x": "1"}})
        hits = scan({"filename": "frontend/package.json", "status": "modified", "patch": "+"},
                    base={"frontend/package.json": old}, head={"frontend/package.json": new})
        self.assertEqual(hits, ["frontend/package.json changes dependencies, devDependencies"])

    def test_a_scripts_only_package_json_change_is_low(self):
        old = json.dumps({"scripts": {"a": "x"}, "dependencies": {"react": "18.3.1"}})
        new = json.dumps({"scripts": {"a": "y", "b": "z"}, "dependencies": {"react": "18.3.1"}})
        hits = scan({"filename": "frontend/package.json", "status": "modified", "patch": "+"},
                    base={"frontend/package.json": old}, head={"frontend/package.json": new})
        self.assertEqual(hits, [])

    def test_pyproject_dependency_tables(self):
        old = '[project]\nname = "x"\ndependencies = ["a==1"]\n[tool.ruff]\nline-length = 88\n'
        lint = old.replace("88", "100")
        dep = old.replace("a==1", "a==2") + '[dependency-groups]\ndev = ["pytest"]\n'
        path = "futureagi/pyproject.toml"
        item = {"filename": path, "status": "modified", "patch": "+"}
        self.assertEqual(scan(item, base={path: old}, head={path: lint}), [])
        self.assertEqual(
            scan(item, base={path: old}, head={path: dep}),
            [f"{path} changes project.dependencies, dependency-groups"],
        )
        (broken,) = scan(item, base={path: old}, head={path: "[project"})
        self.assertTrue(broken.startswith(f"{path} does not parse: "), broken)

    def test_go_mod_require_blocks(self):
        old = "module m\n\ngo 1.22\n\nrequire (\n\ta v1.0.0\n\tb v1.0.0 // indirect\n)\n"
        item = {"filename": "agentcc-gateway/go.mod", "status": "modified", "patch": "+"}
        go = old.replace("go 1.22", "go 1.23")
        bump = old.replace("a v1.0.0", "a v1.1.0")
        single = old + "require c v0.1.0\n"
        base = {"agentcc-gateway/go.mod": old}
        self.assertEqual(scan(item, base=base, head={"agentcc-gateway/go.mod": go}), [])
        for new in (bump, single, old + "replace a => ../a\n"):
            with self.subTest(new=new):
                self.assertEqual(scan(item, base=base, head={"agentcc-gateway/go.mod": new}),
                                 ["agentcc-gateway/go.mod changes require/replace"])

    def test_added_and_removed_manifests(self):
        new = json.dumps({"dependencies": {"x": "1"}})
        self.assertEqual(scan({"filename": "e2e/package.json", "status": "added"}, head={"e2e/package.json": new}),
                         ["e2e/package.json changes dependencies"])
        self.assertEqual(scan({"filename": "e2e/package.json", "status": "added"}, head={"e2e/package.json": "{}"}), [])
        self.assertEqual(scan({"filename": "e2e/package.json", "status": "removed"}, base={"e2e/package.json": new}),
                         ["e2e/package.json changes dependencies"])

    def test_an_unread_listing_is_a_hit(self):
        files = [{"filename": f"docs/{i}.md", "status": "added"} for i in range(da.FILES_CAP)]
        self.assertEqual(scan(*files), [f"{da.FILES_CAP} files, the most the listing returns"])

    def test_main_scans_against_the_merge_base(self):
        case = Case()
        case.files = [{"filename": "frontend/package.json", "status": "modified", "patch": "+"}]
        case.texts = {
            ("frontend/package.json", MERGE_BASE): '{"dependencies": {"a": "1"}}',
            ("frontend/package.json", HEAD): '{"dependencies": {"a": "2"}}',
        }
        code, calls, decision = run_main("dry", case)
        self.assertEqual((code, decision["action"]), (0, "needs-human"))
        self.assertIn(f"repos/{REPO}/compare/{OLD}...{HEAD}", [c[1] for c in calls])
        self.assertIn("scan: frontend/package.json changes dependencies", decision["reasons"])
        case.texts[("frontend/package.json", HEAD)] = case.texts[("frontend/package.json", MERGE_BASE)]
        self.assertEqual(run_main("dry", case)[2]["action"], "approve")


def owners(path: str) -> list:
    """Owners on the last CODEOWNERS line that matches path: the matcher Backend
    CI's planner uses to keep a code-owned pull request on the full plan."""
    return ci_scope.owners(CODEOWNERS.read_text(), path)


codeowners_regex = ci_scope.codeowners_regex


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
