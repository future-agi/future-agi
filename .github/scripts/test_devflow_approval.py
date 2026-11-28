#!/usr/bin/env python3
"""Stdlib-only tests for devflow_approval and its workflows; every rule has a fixture."""

import copy
import json
import re
import subprocess
import sys
import unittest
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import devflow_approval as da  # noqa: E402

REPO = "org/repo"
HEAD = "a" * 40
OLD = "b" * 40
MERGE_BASE = "c" * 40
APPROVER = "devflow-approver[bot]"
ROOT = Path(__file__).resolve().parent.parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
APPROVAL = WORKFLOWS / "admin-pr-approval.yml"
SWEEP = WORKFLOWS / "devflow-approval-sweep.yml"


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
        "tier_check": {"tier": "low", "engine": "second-model", "input": "diff-only"},
    }
    verdict.update(overrides)
    return (
        f"{da.REVIEW_MARKER}\n**Agent review** · round 1\n"
        f"<details><summary>verdict JSON</summary>\n\n```json\n{json.dumps(verdict)}\n```\n</details>"
    )


def review(login="rev", commit=HEAD, at="2026-11-26T10:00:00Z", body=None, state="COMMENTED", **verdict):
    return {
        "id": hash((login, commit, at, state)) & 0xFFFFFF,
        "user": {"login": login},
        "commit_id": commit,
        "submitted_at": at,
        "state": state,
        "body": review_body(**verdict) if body is None else body,
    }


def request(reviewer="rev", by=APPROVER, event="review_requested"):
    return {"event": event, "actor": {"login": by}, "review_requester": {"login": by}, "requested_reviewer": {"login": reviewer}}


class Case:
    """One pull request, valid for approval until a test breaks one rule."""

    def __init__(self):
        self.pr = {
            "number": 1,
            "state": "open",
            "draft": False,
            "user": {"login": "author"},
            "head": {"sha": HEAD, "repo": {"full_name": REPO}},
            "base": {"ref": "dev", "sha": OLD, "repo": {"full_name": REPO}},
            "labels": [{"name": "devflow:reviewed"}],
        }
        self.reviews = [review()]
        self.events = [request()]
        self.edits = None  # None: every review unedited
        self.permissions = {"author": "write", "rev": "write"}
        self.scan = []
        # For main(): the files listing, {(path, sha): text}, and the pull
        # request as later reads see it (the write-time re-read).
        self.files = []
        self.texts = {}
        self.live_pr = None

    def edit_map(self):
        return self.edits if self.edits is not None else {r["id"]: [] for r in self.reviews}

    def decide(self, tight=False):
        return da.decide(self.pr, self.reviews, self.events, self.edit_map(), self.permissions, APPROVER, tight, self.scan)


def run_main(case, mode="devflow", extra=(), author_permission="write"):
    """(exit code, gh calls, the decisions main() printed) with a fake gh."""
    calls, printed, reads = [], [], []
    real_gh = da.gh

    def fake(*args, stdin=None):
        calls.append(args)
        if args[:2] == ("api", f"repos/{REPO}/pulls/1"):
            reads.append(1)
            live = case.live_pr if case.live_pr is not None and len(reads) > 1 else case.pr
            return json.dumps(live)
        if args[:2] == ("api", "graphql"):
            query = next(a for a in args if a.startswith("query="))
            if "userContentEdits" in query:
                nodes = [
                    {"databaseId": i, "userContentEdits": {"totalCount": len(e), "nodes": [{"editor": {"login": x}} for x in e]}}
                    for i, e in case.edit_map().items()
                ]
                return json.dumps({"data": {"repository": {"pullRequest": {"reviews": {"nodes": nodes}}}}})
            node = {
                "number": 1,
                "baseRefName": case.pr["base"]["ref"],
                "labels": {"nodes": case.pr["labels"]},
                "reviews": {"nodes": [{"author": {"login": da.login_key(r["user"]["login"])}} for r in case.reviews if r["state"] == "APPROVED"]},
            }
            return json.dumps({"data": {"repository": {"pullRequests": {"pageInfo": {"hasNextPage": False}, "nodes": [node]}}}})
        if "--paginate" in args:
            items = {"/reviews": case.reviews, "/files": case.files, "/events": case.events}
            return "\n".join(json.dumps(i) for i in items[re.search(r"/(reviews|files|events)\?", args[2])[0][:-1]])
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
        if "/permission" in args[1]:
            return "write\n"
        return "{}"

    da.gh = fake
    da.print = lambda *a, **k: printed.append(" ".join(map(str, a)))
    env = {"DEVFLOW_APPROVER_BOT": APPROVER}
    saved = {k: da.os.environ.get(k) for k in env}
    da.os.environ.update(env)
    try:
        code = da.main(["--repo", REPO, *(extra or ["--pr", "1"]), "--mode", mode])
    finally:
        da.gh = real_gh
        del da.print
        for k, v in saved.items():
            if v is None:
                da.os.environ.pop(k, None)
            else:
                da.os.environ[k] = v
    decisions = [json.loads(p) for p in printed if p.startswith("{")]
    return code, calls, decisions


def writes(calls):
    return [c for c in calls if "-X" in c]


class DecideTests(unittest.TestCase):
    def check(self, case, action, reason=None, tight=False):
        result = case.decide(tight)
        self.assertEqual(result["action"], action, result)
        if reason:
            self.assertTrue(any(reason in r for r in result["reasons"]), result)
        return result

    def test_passing_low_tier_review_on_head_approves(self):
        self.assertEqual(self.check(Case(), "approve")["head"], HEAD)

    def test_skip_closed_and_fork(self):
        for key, value, reason in [
            ("state", "closed", "not open"),
            ("head", {"sha": HEAD, "repo": {"full_name": "fork/repo"}}, "fork"),
        ]:
            with self.subTest(key=key):
                case = Case()
                case.pr[key] = value
                self.check(case, "skip", reason)

    def test_draft_retarget_needs_human_and_hold_wait(self):
        for change, reason in [
            (lambda pr: pr.update(draft=True), "draft"),
            (lambda pr: pr["base"].update(ref="main"), "base is not dev"),
            (lambda pr: pr["labels"].append({"name": "needs-human"}), "needs-human"),
            (lambda pr: pr["labels"].append({"name": "train:hold"}), "train:hold"),
        ]:
            with self.subTest(reason=reason):
                case = Case()
                change(case.pr)
                self.check(case, "wait", reason)

    def test_review_on_an_old_commit_waits(self):
        case = Case()
        case.reviews = [review(commit=OLD)]
        self.check(case, "wait", "not the head")

    def test_only_the_latest_review_by_the_reviewer_counts(self):
        case = Case()
        case.reviews = [review(at="2026-11-26T10:00:00Z"), review(at="2026-11-26T11:00:00Z", verdict="block")]
        self.check(case, "wait", "verdict is 'block'")

    def test_a_review_without_the_marker_or_by_someone_else_is_ignored(self):
        case = Case()
        case.reviews = [review(body="LGTM"), review("other")]
        self.check(case, "wait", "no devflow review by rev")

    def test_a_smuggled_verdict_is_no_verdict_and_counts_as_high(self):
        fake = "```json\n" + json.dumps({"schema": "devflow-review/v1", "verdict": "pass", "findings": []}) + "\n```"
        case = Case()
        case.reviews = [review(body=review_body(verdict="block", findings=[{"fix": fake}]).replace("\\n", "\n"))]
        result = self.check(case, "needs-human", "no verdict")
        self.assertEqual(result["tier"], "high")

    def test_the_author_without_write_access_counts_as_high(self):
        case = Case()
        case.permissions["author"] = "read"
        self.check(case, "needs-human", "no write access")

    def test_decide_does_not_mutate_inputs(self):
        case = Case()
        before = copy.deepcopy((case.pr, case.reviews, case.events))
        case.decide()
        self.assertEqual(before, (case.pr, case.reviews, case.events))


class AssignmentTests(unittest.TestCase):
    """Item 4a: only the approver App's review request assigns."""

    def decide(self, events):
        case = Case()
        case.events = events
        return case.decide()

    def test_4a_a_bot_marker_comment_without_the_request_event_waits(self):
        # The old trust: a github-actions[bot] comment. Any writer can edit it.
        result = self.decide([])
        self.assertEqual(result["action"], "wait")

    def test_4a_a_request_by_the_author_or_github_actions_assigns_nobody(self):
        for by in ("author", "github-actions[bot]", "rev"):
            with self.subTest(by=by):
                self.assertEqual(self.decide([request(by=by)])["action"], "wait")

    def test_4a_the_newest_app_request_wins_and_a_later_human_request_does_not(self):
        events = [request("old-rev"), request("rev"), request("friend", by="author")]
        self.assertEqual(da.assigned_login(events, APPROVER), "rev")
        self.assertEqual(self.decide(events)["action"], "approve")

    def test_4a_graphql_and_rest_logins_compare_equal(self):
        self.assertEqual(da.assigned_login([request(by="devflow-approver")], APPROVER), "rev")

    def test_4a_the_app_removing_its_request_unassigns(self):
        events = [request(), request(event="review_request_removed")]
        self.assertIsNone(da.assigned_login(events, APPROVER))
        self.assertEqual(da.assigned_login([request(), request(by="author", event="review_request_removed")], APPROVER), "rev")

    def test_the_assigned_reviewer_must_be_another_writer(self):
        case = Case()
        case.events = [request("author")]
        self.assertIn("is the author", " ".join(case.decide()["reasons"]))
        case = Case()
        case.permissions["rev"] = "read"
        self.assertIn("no write access", " ".join(case.decide()["reasons"]))


class VerdictLifecycleTests(unittest.TestCase):
    """Item 4b: dismissed and edited verdicts never count."""

    def test_4b_a_dismissed_pass_waits(self):
        case = Case()
        case.reviews = [review(state="DISMISSED")]
        self.assertEqual(case.decide()["action"], "wait")

    def test_4b_a_newest_dismissed_review_does_not_fall_back_to_an_older_pass(self):
        case = Case()
        case.reviews = [review(at="2026-11-26T10:00:00Z"), review(at="2026-11-26T11:00:00Z", state="DISMISSED")]
        self.assertEqual(case.decide()["action"], "wait")

    def test_4b_a_verdict_edited_by_another_login_waits(self):
        case = Case()
        rid = case.reviews[0]["id"]
        for editors in (["rev", "author"], ["github-actions"], [""], None):
            with self.subTest(editors=editors):
                case.edits = {rid: editors}
                result = case.decide()
                self.assertEqual(result["action"], "wait")
        case.edits = {}  # a review the edit query did not return
        self.assertEqual(case.decide()["action"], "wait")

    def test_an_edit_by_the_reviewer_alone_counts(self):
        case = Case()
        case.edits = {case.reviews[0]["id"]: ["rev", "Rev"]}
        self.assertEqual(case.decide()["action"], "approve")


class TierContractTests(unittest.TestCase):
    def case(self, scan=(), **verdict):
        case = Case()
        case.reviews = [review(**verdict)]
        case.scan = list(scan)
        return case

    def test_medium_is_never_auto_approved_and_joins_the_train(self):
        for verdict in ({"tier": "medium"}, {"needs_person": True}, {"tier_check": {"tier": "medium", "input": "diff-only"}}):
            with self.subTest(verdict=verdict):
                result = self.case(**verdict).decide()
                self.assertEqual((result["action"], result["tier"]), ("train", "medium"))
        code, calls, _ = run_main(self.case(tier="medium"))
        self.assertFalse(any("event=APPROVE" in c for c in writes(calls)))
        self.assertIn(("labels[]=devflow:train",), [c[-1:] for c in writes(calls)])

    def test_tight_mode_makes_low_medium_and_never_adds_needs_human(self):
        result = Case().decide(tight=True)
        self.assertEqual((result["action"], result["tier"]), ("train", "medium"))

    def test_a_missing_or_non_diff_only_tier_check_counts_as_high(self):
        for check in (None, "low", {}, {"tier": "low", "input": "full"}):
            with self.subTest(check=check):
                self.assertEqual(self.case(tier_check=check).decide()["action"], "needs-human")

    def test_an_unknown_tier_string_is_high(self):
        for tier in ("Medium", "", None, "critical", "LOW", ["low"]):
            with self.subTest(tier=tier):
                self.assertEqual(self.case(tier=tier).decide()["tier"], "high")

    def test_the_final_tier_is_the_ordinal_max(self):
        for votes, expected in ((("low", "medium"), "medium"), (("medium", "high"), "high"), (("low", "low"), "low"), (("low", "x"), "high")):
            with self.subTest(votes=votes):
                self.assertEqual(da.max_tier(*votes), expected)

    def test_a_scan_hit_alone_needs_a_human(self):
        result = self.case(scan=["x changes a lockfile"]).decide()
        self.assertEqual((result["action"], result["votes"]["scan"]), ("needs-human", "high"))


def scan(*files, base=None, head=None):
    texts = {"base": base or {}, "head": head or {}}
    return da.risk_scan(list(files), lambda path, side: texts[side].get(path))


def patch(*lines):
    return "@@ -1,0 +1,9 @@\n" + "\n".join(lines)


MIGRATION = "futureagi/tracer/migrations/0121_x.py"
CH = "futureagi/tracer/services/clickhouse/v2/schema/039_x.sql"


class FloorTests(unittest.TestCase):
    """Item 2: approval, assignment, CI and queue policy always need a person."""

    def test_2_policy_files_alone_need_a_human(self):
        for path in (
            ".github/scripts/devflow_train.py",
            ".github/scripts/ci_scope.py",
            ".github/scripts/reviewer-rotation.mjs",
            ".github/merge-queue/dev.ruleset.json",
            ".github/workflows/admin-pr-approval.yml",
            ".github/CODEOWNERS",
            "docs/CODEOWNERS",
            "scripts/check_update_safety.py",
            "futureagi/.ci/test_db_dump.py",
            "e2e/scripts/shard.mjs",
            "e2e/playwright.config.ts",
            "futureagi/pytest.ini",
            "futureagi/pyproject.toml",
            "deploy/update/clickhouse-sources.json",
            ".agents/skills/reviewing-prs/SKILL.md",
            ".claude/settings.json",
        ):
            with self.subTest(path=path):
                hits = scan({"filename": path, "status": "modified", "patch": "+x"})
                self.assertEqual(len(hits), 1)
                case = Case()
                case.scan = hits
                self.assertEqual(case.decide()["action"], "needs-human")

    def test_ordinary_paths_are_not_on_the_floor(self):
        for path in ("futureagi/tracer/views/span.py", "x/.github/a.yml", "e2e/flows/a.spec.ts", "docs/scripts/a.md"):
            with self.subTest(path=path):
                self.assertFalse(da.floor_path(path))


class RiskScanTests(unittest.TestCase):
    def test_destructive_django_operations_hit(self):
        for op in ("RemoveField", "DeleteModel", "RenameField", "RenameModel", "RunSQL", "RunPython"):
            with self.subTest(op=op):
                hits = scan({"filename": MIGRATION, "status": "added", "patch": patch(f"+        migrations.{op}(")})
                self.assertEqual(hits, [f"{MIGRATION} adds {op}"])

    def test_an_additive_migration_is_low(self):
        self.assertEqual(scan({"filename": MIGRATION, "status": "added", "patch": patch("+ migrations.AddField(")}), [])

    def test_a_changed_migration_is_scanned_whole(self):
        hits = scan({"filename": MIGRATION, "status": "modified", "patch": "+x"}, head={MIGRATION: "migrations.RunPython(f)"})
        self.assertEqual(hits, [f"{MIGRATION} adds RunPython"])

    def test_clickhouse_destructive_statements_hit_and_comments_do_not(self):
        self.assertEqual(scan({"filename": CH, "status": "added", "patch": patch("+DROP TABLE x;")}), [f"{CH} adds DROP"])
        self.assertEqual(scan({"filename": CH, "status": "added", "patch": patch("+-- DROP old", "+ALTER TABLE x ADD COLUMN y")}), [])

    def test_manifest_dependency_changes_hit(self):
        path = "frontend/package.json"
        item = {"filename": path, "status": "modified"}
        self.assertEqual(
            scan(item, base={path: '{"dependencies": {"a": "1"}}'}, head={path: '{"dependencies": {"a": "2"}}'}),
            [f"{path} changes dependencies"],
        )
        self.assertEqual(scan(item, base={path: '{"scripts": {}}'}, head={path: '{"scripts": {"x": "y"}}'}), [])

    def test_a_lockfile_or_requirements_change_is_a_hit_in_any_directory(self):
        for path in ("uv.lock", "frontend/yarn.lock", "x/requirements-dev.txt", "agentcc-gateway/go.sum"):
            with self.subTest(path=path):
                self.assertEqual(scan({"filename": path, "status": "modified"}), [f"{path} changes a lockfile"])

    def test_an_unread_listing_is_a_hit(self):
        files = [{"filename": f"docs/{i}.md", "status": "added"} for i in range(da.FILES_CAP)]
        self.assertEqual(scan(*files), [f"{da.FILES_CAP} files, the most the listing returns"])


class RenameTests(unittest.TestCase):
    """Item 4e: every rule reads both names of a rename."""

    def test_4e_a_workflow_renamed_out_of_github_is_a_floor_hit(self):
        hits = scan({"filename": "docs/ci.yml", "previous_filename": ".github/workflows/ci.yml", "status": "renamed"})
        self.assertEqual(len(hits), 1)
        self.assertIn(".github/workflows/ci.yml", hits[0])

    def test_4e_a_migration_moved_out_of_migrations_is_a_hit(self):
        hits = scan({"filename": "futureagi/tracer/old_0121_x.py", "previous_filename": MIGRATION, "status": "renamed"})
        self.assertEqual(hits, [f"futureagi/tracer/old_0121_x.py is renamed from {MIGRATION}"])

    def test_4e_a_lockfile_renamed_away_is_a_hit(self):
        self.assertEqual(
            scan({"filename": "frontend/yarn.lock.bak", "previous_filename": "frontend/yarn.lock", "status": "renamed"}),
            ["frontend/yarn.lock.bak changes a lockfile"],
        )

    def test_4e_a_manifest_renamed_away_is_a_hit_and_a_moved_one_reads_the_old_path(self):
        hits = scan({"filename": "frontend/package.json.old", "previous_filename": "frontend/package.json", "status": "renamed"})
        self.assertEqual(hits, ["frontend/package.json.old is renamed from frontend/package.json"])
        moved = {"filename": "web/package.json", "previous_filename": "frontend/package.json", "status": "renamed"}
        same = '{"dependencies": {"a": "1"}}'
        self.assertEqual(scan(moved, base={"frontend/package.json": same}, head={"web/package.json": same}), [])


class MainTests(unittest.TestCase):
    def test_help_survives_a_missing_docstring(self):
        # python -OO strips docstrings, so __doc__ is None.
        saved, da.__doc__ = da.__doc__, None
        try:
            with self.assertRaises(SystemExit) as raised:
                da.main(["--help"])
        finally:
            da.__doc__ = saved
        self.assertEqual(raised.exception.code, 0)

    def test_devflow_mode_approves_then_admits_to_the_train(self):
        code, calls, decisions = run_main(Case())
        posted = writes(calls)
        self.assertEqual((code, decisions[0]["action"]), (0, "approve"))
        self.assertIn("event=APPROVE", posted[0])
        self.assertEqual(posted[1][-1], "labels[]=devflow:train")
        self.assertEqual(len(posted), 2)

    def test_an_approved_and_labelled_head_writes_nothing(self):
        case = Case()
        case.reviews.append(review(APPROVER, state="APPROVED", body="Approved automatically."))
        case.pr["labels"].append({"name": "devflow:train"})
        self.assertEqual(writes(run_main(case)[1]), [])

    def test_4c_a_later_block_on_the_same_head_withdraws_the_approval(self):
        case = Case()
        bot = review(APPROVER, state="APPROVED", body="Approved automatically.")
        case.reviews = [review(at="2026-11-26T10:00:00Z"), bot, review(at="2026-11-26T11:00:00Z", verdict="block")]
        case.pr["labels"].append({"name": "devflow:train"})
        code, calls, decisions = run_main(case)
        self.assertEqual((code, decisions[0]["action"]), (0, "wait"))
        posted = writes(calls)
        self.assertEqual(posted[0][3], f"repos/{REPO}/pulls/1/reviews/{bot['id']}/dismissals")
        self.assertEqual(posted[1][2:4], ("DELETE", f"repos/{REPO}/issues/1/labels/devflow%3Atrain"))
        self.assertEqual(len(posted), 2)

    def test_4c_high_withdraws_and_asks_for_a_person(self):
        case = Case()
        bot = review(APPROVER, state="APPROVED", body="Approved automatically.")
        case.reviews = [review(tier="high"), bot]
        posted = writes(run_main(case)[1])
        self.assertIn("dismissals", posted[0][3])
        self.assertEqual(posted[1][-1], "labels[]=needs-human")

    def test_4c_medium_keeps_its_train_label_and_loses_a_bot_approval(self):
        case = Case()
        bot = review(APPROVER, state="APPROVED", body="Approved automatically.")
        case.reviews = [review(tier="medium"), bot]
        case.pr["labels"].append({"name": "devflow:train"})
        posted = writes(run_main(case)[1])
        self.assertEqual(len(posted), 1)
        self.assertIn("dismissals", posted[0][3])

    def test_a_wait_keeps_the_admin_rules_approval_on_an_admins_pull_request(self):
        # The first switch to devflow mode withdrew every queued admin
        # approval this way: no reviewer requested, so wait.
        case = Case()
        case.events = []
        case.reviews = [review(APPROVER, state="APPROVED", body=da.ADMIN_APPROVAL_BODY)]
        code, calls, decisions = run_main(case, author_permission="admin")
        self.assertEqual((code, decisions[0]["action"], decisions[0]["withdraw"]), (0, "wait", []))
        self.assertEqual(writes(calls), [])

    def test_needs_human_keeps_the_admin_rules_approval_and_adds_the_label(self):
        case = Case()
        case.reviews = [review(tier="high"), review(APPROVER, state="APPROVED", body=da.ADMIN_APPROVAL_BODY)]
        posted = writes(run_main(case, author_permission="admin")[1])
        self.assertEqual([p[-1] for p in posted], ["labels[]=needs-human"])

    def test_every_other_bot_approval_is_still_withdrawn(self):
        own = "Approved automatically: a passing low-tier devflow agent review on this commit."
        for name, body, author_permission, editors in [
            ("devflow's own approval on an admin's pull request", own, "admin", []),
            ("the admin body on a non-admin's pull request", da.ADMIN_APPROVAL_BODY, "write", []),
            ("the admin body edited by another login", da.ADMIN_APPROVAL_BODY, "admin", ["someone"]),
        ]:
            with self.subTest(name=name):
                case = Case()
                case.events = []
                bot = review(APPROVER, state="APPROVED", body=body)
                case.reviews = [bot]
                case.edits = {bot["id"]: editors}
                posted = writes(run_main(case, author_permission=author_permission)[1])
                self.assertEqual([p[3] for p in posted], [f"repos/{REPO}/pulls/1/reviews/{bot['id']}/dismissals"])

    def test_4d_a_draft_or_needs_human_at_write_time_gets_no_approval(self):
        for name, change in [
            ("draft", lambda pr: pr.update(draft=True)),
            ("needs-human", lambda pr: pr["labels"].append({"name": "needs-human"})),
            ("train:hold", lambda pr: pr["labels"].append({"name": "train:hold"})),
            ("retarget", lambda pr: pr["base"].update(ref="main")),
            ("new head", lambda pr: pr["head"].update(sha=OLD)),
        ]:
            with self.subTest(name=name):
                case = Case()
                case.live_pr = copy.deepcopy(case.pr)
                change(case.live_pr)
                code, calls, decisions = run_main(case)
                self.assertEqual((code, decisions[0]["action"]), (0, "approve"))
                self.assertEqual(writes(calls), [])

    def test_observe_writes_nothing(self):
        case = Case()
        case.reviews = [review(verdict="block"), review(APPROVER, state="APPROVED", body="x")]
        for case, action in ((Case(), "approve"), (case, "wait")):
            code, calls, decisions = run_main(case, mode="observe")
            self.assertEqual((code, decisions[0]["action"], writes(calls)), (0, action, []))

    def test_main_scans_against_the_merge_base(self):
        case = Case()
        case.files = [{"filename": "frontend/package.json", "status": "modified", "patch": "+"}]
        case.texts = {
            ("frontend/package.json", MERGE_BASE): '{"dependencies": {"a": "1"}}',
            ("frontend/package.json", HEAD): '{"dependencies": {"a": "2"}}',
        }
        code, calls, decisions = run_main(case, mode="observe")
        self.assertEqual(decisions[0]["action"], "needs-human")
        self.assertIn(f"repos/{REPO}/compare/{OLD}...{HEAD}", [c[1] for c in calls])

    def test_the_sweep_decides_each_candidate(self):
        code, calls, decisions = run_main(Case(), extra=["--sweep"])
        self.assertEqual((code, [d["pr"] for d in decisions]), (0, [1]))
        case = Case()
        case.pr["labels"] = []
        self.assertEqual(run_main(case, extra=["--sweep"])[2], [])

    def test_a_missing_approver_login_decides_nothing(self):
        saved = da.os.environ.pop("DEVFLOW_APPROVER_BOT", None)
        try:
            for value in (None, "", "[bot]", "devflow-approver"):
                with self.subTest(value=value):
                    if value is not None:
                        da.os.environ["DEVFLOW_APPROVER_BOT"] = value
                    self.assertEqual(da.main(["--repo", REPO, "--pr", "1"]), 2)
        finally:
            da.os.environ.pop("DEVFLOW_APPROVER_BOT", None)
            if saved is not None:
                da.os.environ["DEVFLOW_APPROVER_BOT"] = saved


def job(path: Path, name: str) -> str:
    body = path.read_text().split(f"\n  {name}:\n", 1)[1]
    return re.split(r"\n  [a-z][a-z-]*:\n", body, maxsplit=1)[0]


class WorkflowTests(unittest.TestCase):
    def test_1_approval_never_runs_pull_request_code(self):
        # The bundle's merge-ref approval workflow must not come back.
        self.assertFalse((WORKFLOWS / "devflow-approval.yml").exists())
        approval, sweep = APPROVAL.read_text(), SWEEP.read_text()
        self.assertRegex(approval, r"(?m)^on:\n  pull_request_target:\n")
        self.assertNotRegex(approval, r"(?m)^  pull_request:")
        self.assertRegex(sweep, r"(?m)^on:\n  schedule:\n")
        for text in (approval, sweep):
            self.assertNotIn("github.event.pull_request.head.sha }}\n          persist", text)
            for ref in re.findall(r"ref: (.+)", text):
                self.assertEqual(ref.strip(), "${{ github.workflow_sha }}")

    def test_1_only_base_controlled_files_read_the_approver_key(self):
        for path in sorted(WORKFLOWS.glob("*.y*ml")):
            if "DEVFLOW_APPROVER_PRIVATE_KEY" in path.read_text():
                with self.subTest(path=path.name):
                    self.assertIn(path.name, {"admin-pr-approval.yml", "devflow-approval-sweep.yml", "reviewer-assigner.yml"})

    def test_3a_the_default_mode_approves_admins_only(self):
        admin, devflow = job(APPROVAL, "admin"), job(APPROVAL, "devflow")
        # The admin rule runs in every mode; the devflow job decides dev only.
        self.assertNotIn("APPROVAL_MODE", admin)
        self.assertIn("github.event.pull_request.base.ref == 'dev' &&", devflow)
        self.assertIn('contains(fromJSON(\'["opened", "reopened", "synchronize", "ready_for_review"]\'), github.event.action)', admin)
        self.assertIn("(vars.APPROVAL_MODE == 'devflow' || vars.APPROVAL_MODE == 'observe')", devflow)
        self.assertIn("vars.APPROVAL_MODE == 'devflow' || vars.APPROVAL_MODE == 'observe'", job(SWEEP, "sweep"))

    def test_the_admin_rules_body_is_the_one_devflow_keeps(self):
        self.assertIn(f'-f body="{da.ADMIN_APPROVAL_BODY}"', job(APPROVAL, "approve"))

    def test_8d_every_action_is_pinned_and_github_token_never_writes(self):
        for path in (APPROVAL, SWEEP):
            text = path.read_text()
            for ref in re.findall(r"uses:\s*(\S+)", text):
                with self.subTest(path=path.name, uses=ref):
                    self.assertRegex(ref, r"@[0-9a-f]{40}$")
            grants = re.findall(r"^\s+([a-z-]+):\s*write\b", text, re.M)
            self.assertEqual([g for g in grants if not g.startswith("permission-")], [], path.name)

    def test_the_devflow_jobs_check_the_key_and_the_bot_login_first(self):
        for text in (job(APPROVAL, "devflow"), job(SWEEP, "sweep")):
            self.assertIn("name: devflow-approver", text)
            self.assertLess(text.index("Fail without the approver App"), text.index("create-github-app-token"))
            self.assertIn("DEVFLOW_APPROVER_BOT: ${{ vars.DEVFLOW_APPROVER_BOT }}", text)
            self.assertIn("GH_TOKEN: ${{ steps.approver.outputs.token }}", text)

    def test_4f_no_workflow_or_script_writes_repository_variables(self):
        # The digest once switched APPROVAL_MODE_TIGHT off with a variables token.
        for path in sorted((ROOT / ".github").rglob("*")):
            if path.suffix in {".yml", ".yaml", ".py", ".mjs", ".js", ".sh"} and not path.name.startswith("test_"):
                with self.subTest(path=str(path.relative_to(ROOT))):
                    text = path.read_text()
                    self.assertNotIn("DEVFLOW_VARS_TOKEN", text)
                    self.assertNotRegex(text, r"actions/variables")


if __name__ == "__main__":
    unittest.main()
