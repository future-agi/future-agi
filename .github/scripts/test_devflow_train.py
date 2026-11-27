"""Tests for devflow_train.py.

Every GitHub payload here is CONSTRUCTED in the shape of the GraphQL query in
devflow_train.build_query(), the check-runs REST endpoint and the REST responses the train reads; no train has run
yet, so there is nothing to record. The live-write tests use a fake client
that records each call.

    python3 -I .github/scripts/test_devflow_train.py
"""

import base64
import json
import pathlib
import re
import sys
import time
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import devflow_train as train

REQUIRED = [
    {"context": c, "app_id": 15368}
    for c in ("Backend Tests Pass", "E2E Tests Pass", "Python Lint Pass")
]
APP = "futureagi-devflow-train"


def sha(n: int) -> str:
    return format(n, "040x")


def run(
    name,
    conclusion="SUCCESS",
    status="COMPLETED",
    started="2026-11-26T10:00:00Z",
    app=15368,
):
    return {
        "__typename": "CheckRun",
        "name": name,
        "status": status,
        "conclusion": conclusion if status == "COMPLETED" else None,
        "startedAt": started,
        "checkSuite": {"app": {"databaseId": app}},
    }


def green():
    return [run(r["context"]) for r in REQUIRED]


def pr_node(
    number,
    author="author-a",
    head=None,
    labels=(train.ADMIT_LABEL,),
    files=("futureagi/x.py",),
    **over,
):
    head = head or sha(number)
    files = [f if isinstance(f, tuple) else (f, "MODIFIED") for f in files]
    node = {
        "id": f"PR_{number}",
        "number": number,
        "title": f"feat: change {number}",
        "body": "",
        "isDraft": False,
        "isCrossRepository": False,
        "baseRefName": "dev",
        "headRefName": f"feat/change-{number}",
        "headRefOid": head,
        "author": {"login": author},
        "mergeable": "MERGEABLE",
        "isInMergeQueue": False,
        "reviewDecision": "APPROVED",
        "changedFiles": len(files),
        "autoMergeRequest": None,
        "labels": {"nodes": [{"name": n} for n in labels]},
        "files": {"nodes": [{"path": p, "changeType": c} for p, c in files]},
        "reviews": {
            "nodes": [
                {
                    "state": "APPROVED",
                    "commit": {"oid": head},
                    "author": {"__typename": "Bot", "login": "github-actions"},
                },
                # Before the promotion a person approves every member and rollup.
                {
                    "state": "APPROVED",
                    "commit": {"oid": head},
                    "author": {"__typename": "User", "login": "bob"},
                },
                verdict_review(head),
            ]
        },
        "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "SUCCESS"}}}]},
        "comments": {"nodes": []},
        "_check_runs": green(),
        "timelineItems": {"nodes": []},
        # Reviewer and approver -> repository permission (GitHub.attach_*).
        "_permissions": {"reviewer": "write", "bob": "write"},
    }
    node.update(over)
    return node


def view(node):
    return train.member_view(node, REQUIRED)


def without_person(node):
    """The node with no person's approval: only the bot's stays."""
    node["reviews"]["nodes"] = [
        r for r in node["reviews"]["nodes"] if (r.get("author") or {}).get("login") != "bob"
    ]
    return node


def bot_only(node):
    """Approved on the head by github-actions[bot] only."""
    return without_person(node)


# Repository permissions of the reviewers in the Verdicts tests.
WRITERS = {"reviewer": "write", "bob": "write", "carol": "write", "author-a": "admin"}


def latest(reviews, author, assigned=None):
    return train.latest_verdict(reviews, author, assigned, WRITERS)


def verdict_review(
    head,
    tier="low",
    verdict="pass",
    assoc="MEMBER",
    when="2026-11-26T11:00:00Z",
    needs_person=False,
    login="reviewer",
    tier_check="low",
):
    body = {
        "schema": "devflow-review/v1",
        "head": head,
        "tier": tier,
        "verdict": verdict,
        "needs_person": needs_person,
        "findings": [],
    }
    if tier_check is not None:
        body["tier_check"] = {"tier": tier_check, "engine": "x", "input": "diff-only"}
    return {
        "state": "COMMENTED",
        "authorAssociation": assoc,
        "submittedAt": when,
        "commit": {"oid": head},
        "author": {"__typename": "User", "login": login},
        "body": f"{train.VERDICT_MARKER}\n```json\n{json.dumps(body)}\n```",
    }


def rollup_node(
    number,
    members,
    infra_retries=0,
    removals=0,
    removals_seen=0,
    in_queue=False,
    author=APP,
    reason="failed_checks",
    checks=None,
    held=None,
):
    manifest = {
        "schema": train.SCHEMA,
        "id": f"r{number}",
        "base_sha": sha(9000),
        "head_sha": sha(number),
        "parent": None,
        "infra_retries": infra_retries,
        "removals_seen": removals_seen,
        "members": [
            {
                "pr": n,
                "head_sha": h,
                "branch": f"feat/change-{n}",
                "title": f"feat: change {n}",
            }
            for n, h in members
        ],
    }
    if held is not None:
        manifest["held"] = held
    node = pr_node(
        number,
        author=author,
        labels=(train.ROLLUP_LABEL,),
        headRefName=f"{train.BRANCH_PREFIX}r{number}",
    )
    node["body"] = train.render_manifest(manifest)
    node["isInMergeQueue"] = in_queue
    node["timelineItems"] = {
        "nodes": [
            {"__typename": "RemovedFromMergeQueueEvent", "reason": reason}
            for _ in range(removals)
        ]
    }
    if checks is not None:
        node["_check_runs"] = checks
    # The members as git has them (GitHub.attach_chains).
    node["_chain"] = list(members)
    return node


def data(admitted=(), rollups=(), queue=(), base_red=()):
    return {
        "base_red": list(base_red),
        "repository": {
            "mergeQueue": {
                "entries": {"nodes": [{"pullRequest": {"number": n}} for n in queue]}
            },
            "admitted": {"nodes": list(admitted)},
            "rollups": {"nodes": list(rollups)},
        },
    }


def cfg(**over):
    return train.Config(
        **{
            "train_login": APP,
            "max_batch": 6,
            "max_rollups": 4,
            "queue_builds": 6,
            "require_verdict": True,
            "tight": False,
            **over,
        }
    )


class Globs(unittest.TestCase):
    def match(self, glob, path):
        return bool(train.glob_to_regex(glob).match(path))

    def test_policy_semantics(self):
        self.assertTrue(self.match(".github/**", ".github/workflows/ci.yml"))
        self.assertTrue(self.match("**/package.json", "package.json"))
        self.assertTrue(self.match("**/package.json", "frontend/a/package.json"))
        self.assertTrue(self.match("docs/*.md", "docs/a.md"))
        self.assertFalse(self.match("docs/*.md", "docs/a/b.md"))
        self.assertTrue(self.match("**/*lock*", "frontend/yarn.lock"))
        self.assertFalse(self.match("Dockerfile*", "futureagi/Dockerfile"))
        self.assertTrue(self.match("**/Dockerfile*", "futureagi/Dockerfile.slim"))
        self.assertFalse(self.match(".github/**", "futureagi/.githubx"))

    def test_high_globs_from_policy_text(self):
        self.assertEqual(train.parse_high_globs(None), [])
        self.assertEqual(
            train.parse_high_globs(json.dumps({"high_globs": ["ee/**", "**/go.mod"]})),
            ["ee/**", "**/go.mod"],
        )
        self.assertEqual(train.parse_high_globs("{}"), [])
        with self.assertRaises(ValueError):
            train.parse_high_globs(json.dumps({"high_globs": "ee/**"}))

    def test_batch_and_rollup_defaults_are_variables(self):
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                (train.Config().max_batch, train.Config().max_rollups), (6, 4)
            )
        with mock.patch.dict(
            os.environ,
            {"DEVFLOW_TRAIN_MAX_BATCH": "8", "DEVFLOW_TRAIN_MAX_ROLLUPS": "8"},
        ):
            self.assertEqual(
                (train.Config().max_batch, train.Config().max_rollups), (8, 8)
            )

    def test_floor_always_applies(self):
        # The floor is B1's floor_path; the policy file only adds globs.
        self.assertEqual(train.Config(high_globs=["ee/**"]).high_globs, ("ee/**",))
        for path in (".github/scripts/devflow_train.py", "scripts/check.py", "futureagi/.ci/x.py",
                     "e2e/scripts/shard.mjs", "futureagi/pytest.ini", "a/CODEOWNERS"):
            with self.subTest(path=path):
                reason = train.exclusion(view(pr_node(1, files=(path,))), cfg(), {})
                self.assertEqual(reason, f"high-risk path ({path}): goes alone")

    def test_a_rename_out_of_the_floor_is_a_floor_hit(self):
        node = pr_node(1, files=(("futureagi/x.yml", "RENAMED"),))
        self.assertIsNone(train.exclusion(view(node), cfg(), {}), "GraphQL has only the new path")

        class Files(train.GitHub):
            def request(self, method, path, body=None, raw=False):
                assert path == "repos/o/r/pulls/1/files?per_page=100&page=1", path
                return 200, [{"filename": "futureagi/x.yml", "previous_filename": ".github/workflows/a.yml"}]

        Files("t", "o/r").attach_rename_sources(data([node]))
        self.assertEqual(
            train.exclusion(view(node), cfg(), {}), "high-risk path (.github/workflows/a.yml): goes alone"
        )

    def test_migration_apps(self):
        files = [
            {
                "path": "futureagi/tracer/migrations/0123_add_x.py",
                "changeType": "ADDED",
            },
            {
                "path": "futureagi/tracer/migrations/0122_old.py",
                "changeType": "MODIFIED",
            },
            {"path": "futureagi/ee/usage/migrations/0007_y.py", "changeType": "ADDED"},
            {"path": "futureagi/tracer/migrations/__init__.py", "changeType": "ADDED"},
            {"path": "futureagi/tracer/models.py", "changeType": "ADDED"},
        ]
        self.assertEqual(
            train.migration_apps(files), {"futureagi/tracer", "futureagi/ee/usage"}
        )


class Checks(unittest.TestCase):
    def test_every_declared_variable_is_used(self):
        for query in (train.build_query(), train.build_query(train.ADMIT_LABEL)):
            declared = re.findall(r"\$(\w+):", query)
            for name in declared:
                with self.subTest(query=query[:60], name=name):
                    self.assertGreater(query.count(f"${name}"), 1, f"${name} is unused")

    def test_permission_is_read_again_after_the_cache_window(self):
        reads, clock = [], [0.0]

        class Perm(train.GitHub):
            def request(self, method, path, body=None, raw=False):
                reads.append(path)
                return 200, {"permission": "write"}

        gh = Perm("t", "o/r")
        self.assertEqual(gh.permission("w", lambda: clock[0]), "write")
        gh.permission("w", lambda: clock[0])
        clock[0] += train.CHECK_CACHE_SECONDS
        gh.permission("w", lambda: clock[0])
        self.assertEqual(len(reads), 2)

    def test_query_reads_no_check_runs(self):
        for query in (train.build_query(), train.build_query(train.ADMIT_LABEL)):
            self.assertNotIn("checkRuns", query)
            # The rollup state only, never its contexts (too many per head).
            self.assertNotIn("contexts", query)

    def test_member_checks_are_cached_only_while_green(self):
        reads = []
        answer = {"runs": green()}

        class Counting(train.GitHub):
            def check_runs(self, sha, required, deferred=False):
                reads.append(sha)
                return answer["runs"]

        gh, clock = Counting("t", "o/r"), [1000.0]
        now = lambda: clock[0]
        gh.member_check_runs(sha(1), REQUIRED, now)
        gh.member_check_runs(sha(1), REQUIRED, now)
        self.assertEqual(reads, [sha(1)], "green on the same head: read once")
        gh.member_check_runs(sha(2), REQUIRED, now)
        self.assertEqual(reads, [sha(1), sha(2)], "a new head is read")
        clock[0] += train.CHECK_CACHE_SECONDS
        gh.member_check_runs(sha(1), REQUIRED, now)
        self.assertEqual(reads.count(sha(1)), 2, "the cache expires")
        gh.forget(sha(1))
        gh.member_check_runs(sha(1), REQUIRED, now)
        self.assertEqual(reads.count(sha(1)), 3, "a re-run by the train drops it")
        answer["runs"] = green()[:2] + [run("Python Lint Pass", status="IN_PROGRESS")]
        gh.member_check_runs(sha(3), REQUIRED, now)
        gh.member_check_runs(sha(3), REQUIRED, now)
        self.assertEqual(reads.count(sha(3)), 2, "pending is never cached")
        answer["runs"] = green()[:2] + [run("Python Lint Pass", "FAILURE")]
        gh.member_check_runs(sha(4), REQUIRED, now)
        gh.member_check_runs(sha(4), REQUIRED, now)
        self.assertEqual(
            reads.count(sha(4)), 2, "red is never cached: a re-run may fix it"
        )

    def test_rollup_checks_are_always_fresh(self):
        reads = []

        class Counting(train.GitHub):
            def check_runs(self, sha, required, deferred=False):
                reads.append(sha)
                return green()

        gh = Counting("t", "o/r")
        payload = data([pr_node(1)], [rollup_node(70, [(1, sha(1))])])
        gh.attach_checks(payload, REQUIRED, cfg())
        gh.attach_checks(payload, REQUIRED, cfg())
        # A member inside a rollup is read fresh every tick too: no cache.
        self.assertEqual(reads, [sha(70), sha(1), sha(70), sha(1)])

    def test_backoff_waits_for_the_low_resource_reset(self):
        limits = {
            "core": {"remaining": 4000, "reset": 2000},
            "graphql": {"remaining": 50, "reset": 1300},
        }
        self.assertEqual(train.backoff_seconds(limits, 1000, 200), 305)
        self.assertEqual(train.backoff_seconds({"core": limits["core"]}, 1000, 200), 0)
        self.assertEqual(train.backoff_seconds({}, 1000, 200), 0)

    def test_rate_limit_headers_are_kept_per_resource(self):
        gh = train.GitHub("t", "o/r")
        gh.note_limits(
            {
                "x-ratelimit-remaining": "17",
                "x-ratelimit-reset": "99",
                "x-ratelimit-resource": "graphql",
            }
        )
        gh.note_limits({})
        self.assertEqual(gh.limits, {"graphql": {"remaining": 17, "reset": 99}})

    def test_gather_reads_every_page(self):
        pages = {
            train.ADMIT_LABEL: [
                [pr_node(n) for n in range(1, 51)],
                [pr_node(n) for n in range(51, 121)],
            ],
            train.ROLLUP_LABEL: [[]],
        }

        class Paged(train.GitHub):
            def graphql(self, query, **variables):
                if "mergeQueue" in query:
                    return {"repository": {"mergeQueue": {"entries": {"nodes": []}}}}
                label = (
                    train.ADMIT_LABEL
                    if train.ADMIT_LABEL in query
                    else train.ROLLUP_LABEL
                )
                index = int(variables["after"] or 0)
                more = index + 1 < len(pages[label])
                return {
                    "repository": {
                        "pullRequests": {
                            "pageInfo": {
                                "hasNextPage": more,
                                "endCursor": str(index + 1),
                            },
                            "nodes": pages[label][index],
                        }
                    }
                }

        out = Paged("t", "o/r").gather()
        self.assertEqual(len(out["repository"]["admitted"]["nodes"]), 120)
        self.assertEqual(out["repository"]["rollups"]["nodes"], [])

    def test_excluded_old_prs_do_not_starve_newer_ones(self):
        old = [pr_node(n, isDraft=True) for n in range(1, 111)]
        result = train.plan(data(old + [pr_node(111)]), REQUIRED, cfg())
        self.assertEqual(result["eligible"], [111])
        self.assertEqual(len(result["excluded"]), 110)

    def test_checks_are_read_only_when_they_decide(self):
        self.assertTrue(train.needs_checks(pr_node(1), REQUIRED, cfg()))
        for node in (
            pr_node(1, isDraft=True),
            pr_node(1, reviewDecision="REVIEW_REQUIRED"),
            pr_node(1, isInMergeQueue=True),
        ):
            self.assertFalse(train.needs_checks(node, REQUIRED, cfg()))

    def test_exclusion_names_the_real_reason_before_checks(self):
        node = pr_node(1, reviewDecision="REVIEW_REQUIRED")
        node["_check_runs"] = []
        self.assertEqual(
            train.exclusion(view(node), cfg(), {}), "not approved on this head"
        )

    def test_all_green(self):
        self.assertEqual(
            set(train.check_states(green(), REQUIRED).values()), {"success"}
        )

    def test_newer_failure_overrules_older_success(self):
        runs = green() + [
            run("Backend Tests Pass", "FAILURE", started="2026-11-26T12:00:00Z")
        ]
        self.assertEqual(
            train.check_states(runs, REQUIRED)["Backend Tests Pass"], "failure"
        )

    def test_skipped_is_ignored_and_only_skipped_is_missing(self):
        runs = [
            run("Backend Tests Pass", "SKIPPED", started="2026-11-26T12:00:00Z")
        ] + green()
        self.assertEqual(
            train.check_states(runs, REQUIRED)["Backend Tests Pass"], "success"
        )
        self.assertEqual(
            train.check_states([run("Backend Tests Pass", "SKIPPED")], REQUIRED)[
                "Backend Tests Pass"
            ],
            "missing",
        )

    def test_other_app_does_not_count(self):
        self.assertEqual(
            train.check_states([run("Backend Tests Pass", app=999)], REQUIRED)[
                "Backend Tests Pass"
            ],
            "missing",
        )

    def test_queued_rerun_is_pending(self):
        runs = green() + [run("E2E Tests Pass", status="QUEUED", started=None)]
        self.assertEqual(
            train.check_states(runs, REQUIRED)["E2E Tests Pass"], "pending"
        )


class Deferred(unittest.TestCase):
    """Light-mode heads report "Backend Tests Deferred" instead of "Backend Tests Pass"."""

    def deferred_runs(self, conclusion="SUCCESS"):
        return [run("Backend Tests Deferred", conclusion)] + green()[1:]

    def test_deferred_is_green_for_members_only(self):
        states = train.check_states(self.deferred_runs(), REQUIRED, deferred=True)
        self.assertEqual(states["Backend Tests Pass"], "deferred")
        self.assertEqual(
            train.check_states(self.deferred_runs(), REQUIRED)["Backend Tests Pass"],
            "missing",
        )
        self.assertEqual(
            train.check_states(self.deferred_runs("FAILURE"), REQUIRED, deferred=True)[
                "Backend Tests Pass"
            ],
            "failure",
        )
        rerun = self.deferred_runs() + [
            run("Backend Tests Pass", started="2026-11-26T12:00:00Z")
        ]
        self.assertEqual(
            train.check_states(rerun, REQUIRED, deferred=True)["Backend Tests Pass"],
            "success",
        )
        node = pr_node(1)
        node["_check_runs"] = self.deferred_runs()
        self.assertIsNone(train.exclusion(view(node), cfg(), {}))

    def test_rollup_needs_a_real_pass(self):
        node = rollup_node(70, [(1, sha(1))], checks=self.deferred_runs())
        rollup = train.rollup_view(node, cfg(), REQUIRED)
        self.assertEqual(rollup["checks"]["Backend Tests Pass"], "missing")

    def test_auto_merge_is_admitted_only_on_a_deferred_head(self):
        node = pr_node(1, autoMergeRequest={"enabledAt": "x"})
        node["_check_runs"] = self.deferred_runs()
        self.assertIsNone(train.exclusion(view(node), cfg(), {}))
        self.assertTrue(train.needs_checks(node, REQUIRED, cfg()))
        node["_check_runs"] = green()
        self.assertIn("GitHub lands it alone", train.exclusion(view(node), cfg(), {}))

    def test_member_enqueued_alone_makes_its_rollup_stale(self):
        rollup = rollup_node(70, [(1, sha(1)), (2, sha(2))])
        result = train.plan(
            data([pr_node(1, isInMergeQueue=True), pr_node(2)], [rollup]),
            REQUIRED,
            cfg(),
        )
        self.assertEqual(
            result["rollup_actions"][0],
            {"action": "rebuild", "drop": [1], "rollup": 70},
        )

    def test_no_full_run_when_queued_or_not_approved(self):
        queued = pr_node(1, title='Revert "feat: x"', isInMergeQueue=True)
        unapproved = pr_node(2, title='Revert "feat: y"')
        unapproved["reviews"]["nodes"] = [
            r for r in unapproved["reviews"]["nodes"] if r["state"] != "APPROVED"
        ]
        for node in (queued, unapproved):
            node["_check_runs"] = self.deferred_runs()
        self.assertEqual(
            train.plan(data([queued, unapproved]), REQUIRED, cfg())["full_runs"], []
        )

    def test_a_failing_full_run_does_not_stop_the_batches(self):
        alone = pr_node(1, title='Revert "feat: x"')
        alone["_check_runs"] = self.deferred_runs()
        result = train.plan(data([alone, pr_node(2)]), REQUIRED, cfg())
        opened = []

        class Recorder(train.Live):
            def full_run(self, pr, head, labelled):
                raise RuntimeError("POST rerun: 403")

            def open_rollup(self, members, parent=None):
                opened.append([m["number"] for m in members])

        train.execute(result, Recorder("o/r", FakeGitHub({}), FakeGitHub({})), {})
        self.assertEqual(opened, [[2]])
        self.assertIn("failed", result["full_runs"][0]["done"])

    def test_go_alone_member_on_a_deferred_head_gets_a_full_run(self):
        alone = pr_node(1, title='Revert "feat: x"')
        alone["_check_runs"] = self.deferred_runs()
        full = pr_node(2, title='Revert "feat: y"')
        draft = pr_node(3, title='Revert "feat: z"', isDraft=True)
        draft["_check_runs"] = self.deferred_runs()
        self.assertTrue(train.needs_checks(alone, REQUIRED, cfg()))
        result = train.plan(data([alone, full, draft]), REQUIRED, cfg())
        self.assertEqual(
            result["full_runs"], [{"pr": 1, "head_sha": sha(1), "labelled": False}]
        )
        self.assertEqual(result["batches"], [])

    def test_full_run_labels_then_reruns_the_whole_light_run_once(self):
        def runs(status="completed", attempt=1):
            return (
                200,
                {
                    "workflow_runs": [
                        {
                            "id": 7,
                            "status": "completed",
                            "run_attempt": 1,
                            "created_at": "2026-11-26T09:00:00Z",
                        },
                        {
                            "id": 8,
                            "status": status,
                            "run_attempt": attempt,
                            "created_at": "2026-11-26T10:00:00Z",
                        },
                    ]
                },
            )

        actions = FakeGitHub(
            {("GET", "repos/o/r/actions/workflows/backend-ci.yml/runs"): runs()}
        )
        app = FakeGitHub({})
        live = train.Live("o/r", app, actions)
        self.assertEqual(
            live.full_run(1, sha(1), labelled=False), "re-ran run 8 with ci:full"
        )
        calls = [(c[0], c[1]) for c in actions.calls]
        self.assertEqual([(c[0], c[1]) for c in app.calls], [("POST", "repos/o/r/issues/1/labels")])
        self.assertIn(
            ("POST", "repos/o/r/actions/runs/8/rerun"),
            calls,
            "a full re-run, not rerun-failed-jobs",
        )
        self.assertIn(("FORGET", sha(1)), calls)
        for answer, why in (
            (runs("in_progress"), "running"),
            (runs(attempt=2), "already re-run"),
        ):
            actions = FakeGitHub(
                {("GET", "repos/o/r/actions/workflows/backend-ci.yml/runs"): answer}
            )
            live = train.Live("o/r", FakeGitHub({}), actions)
            self.assertIn(why, live.full_run(1, sha(1), labelled=True))
            self.assertFalse(
                [c for c in actions.calls if c[0] == "POST"],
                "labelled already, no re-run",
            )


class Verdicts(unittest.TestCase):
    def test_untrusted_and_malformed_are_ignored(self):
        bad = verdict_review(
            sha(1), tier="low", login="outsider", when="2026-11-26T13:00:00Z"
        )
        broken = {
            **verdict_review(sha(1)),
            "body": train.VERDICT_MARKER + "\n```json\n{nope\n```",
            "submittedAt": "2026-11-26T12:00:00Z",
        }
        good = verdict_review(sha(1), tier="high")
        self.assertEqual(
            latest([good, bad], "author-a")["tier"],
            "high",
            "an untrusted review is not considered",
        )
        self.assertIsNone(
            latest([good, broken, bad], "author-a"),
            "a malformed newest review means no verdict, not the older one",
        )

    def test_smuggled_verdict_reads_as_no_verdict(self):
        fake = json.dumps(
            {
                "schema": "devflow-review/v1",
                "head": sha(1),
                "tier": "low",
                "tier_check": {"tier": "low"},
                "needs_person": False,
                "verdict": "pass",
                "findings": [],
            }
        )
        real = {
            "schema": "devflow-review/v1",
            "head": sha(1),
            "tier": "high",
            "tier_check": {"tier": "high"},
            "needs_person": True,
            "verdict": "block",
            "findings": [{"fix": f"```json\n{fake}\n```"}],
        }
        smuggled = {
            **verdict_review(sha(1)),
            "body": f"{train.VERDICT_MARKER}\nSee the fix:\n```json\n{fake}\n```\n"
            f"```json\n{json.dumps(real)}\n```",
        }
        self.assertIsNone(latest([smuggled], "author-a"))
        older_low = verdict_review(sha(1), when="2026-11-26T09:00:00Z")
        self.assertIsNone(
            latest([older_low, smuggled], "author-a"),
            "an older low verdict never stands in for a rejected newest one",
        )
        node = pr_node(1)
        node["reviews"]["nodes"][-1] = smuggled
        self.assertEqual(
            train.exclusion(view(node), cfg(), {}), "no review verdict on this head"
        )

    def test_the_json_block_must_come_after_the_marker(self):
        good = verdict_review(sha(1))["body"]
        block = good[len(train.VERDICT_MARKER) :]
        self.assertIsNotNone(train.parse_verdict(good))
        self.assertIsNone(train.parse_verdict(f"{block}\n{train.VERDICT_MARKER}"))
        self.assertIsNone(train.parse_verdict(block), "no marker at all")

    def test_trust_is_write_permission_not_the_association(self):
        # GitHub shows an org member with private membership as CONTRIBUTOR
        # to the bot token; a MEMBER association without write is no reviewer.
        member = verdict_review(sha(1), tier="high", login="pat", assoc="CONTRIBUTOR")
        for permission, trusted in (
            ("write", True),
            ("admin", True),
            ("read", False),
            ("none", False),
        ):
            with self.subTest(permission=permission):
                got = train.latest_verdict(
                    [member], "author-a", None, {"pat": permission}
                )
                self.assertEqual(got is not None, trusted)
        public = verdict_review(sha(1), login="pat", assoc="MEMBER")
        self.assertIsNone(
            train.latest_verdict([public], "author-a", None, {"pat": "read"})
        )
        self.assertIsNone(
            train.latest_verdict([public], "author-a"),
            "no permission read: not trusted",
        )
        node = pr_node(1)
        node["reviews"]["nodes"][-1] = verdict_review(
            sha(1), login="Pat", assoc="CONTRIBUTOR"
        )
        node["_permissions"] = {}

        asked = []

        class Perm(train.GitHub):
            def request(self, method, path, body=None, raw=False):
                asked.append(path)
                return 200, {"permission": "write"}

        reader = Perm("t", "o/r")
        twice = data([node, {**node, "number": 2}])
        reader.attach_reviewer_permissions(twice)
        self.assertEqual(node["_permissions"], {"pat": "write"})
        self.assertEqual(
            asked,
            ["repos/o/r/collaborators/pat/permission"],
            "one read per login",
        )
        reader.attach_permissions(twice)
        self.assertIsNone(train.exclusion(view(node), cfg(), {}))

    def test_newest_trusted_wins(self):
        old = verdict_review(sha(1), tier="high", when="2026-11-26T10:00:00Z")
        new = verdict_review(sha(1), tier="low", when="2026-11-26T11:00:00Z")
        self.assertEqual(latest([new, old], "author-a")["tier"], "low")

    def test_the_authors_own_verdict_never_counts(self):
        own = verdict_review(sha(1), login="author-a", when="2026-11-26T12:00:00Z")
        self.assertIsNone(latest([own], "author-a"))
        self.assertIsNone(latest([own], "Author-A"), "logins compare without case")
        other = verdict_review(sha(1), tier="high", when="2026-11-26T10:00:00Z")
        self.assertEqual(latest([own, other], "author-a")["tier"], "high")

    def test_the_assigned_reviewer_wins_over_a_newer_verdict(self):
        assigned = verdict_review(
            sha(1), tier="high", login="bob", when="2026-11-26T10:00:00Z"
        )
        newer = verdict_review(
            sha(1), tier="low", login="carol", when="2026-11-26T11:00:00Z"
        )
        self.assertEqual(latest([assigned, newer], "a", "bob")["tier"], "high")
        self.assertEqual(latest([assigned, newer], "a", "dave")["tier"], "low")

    def test_assignment_marker_only_from_the_bot(self):
        marker = "<!-- devflow-assigned:v1 login={} at=2026-11-26T05:00:00Z by=reviewer-assigner round=1 -->"
        bot = {"__typename": "Bot", "login": "github-actions"}
        node = {
            "comments": {
                "nodes": [
                    {"author": bot, "body": marker.format("bob")},
                    {"author": bot, "body": marker.format("carol")},
                    {
                        "author": {"__typename": "User", "login": "github-actions"},
                        "body": marker.format("mallory"),
                    },
                ]
            }
        }
        self.assertEqual(train.assigned_reviewer(node), "carol")
        self.assertIsNone(train.assigned_reviewer({"comments": {"nodes": []}}))


class CodeOwners(unittest.TestCase):
    TEXT = """# comment
/futureagi/accounts/ @team
*billing* @team
/futureagi/accounts/public/
  # indented comment
/.github/CODEOWNERS @lint
"""
    VECTORS = (
        ("/.github/", ".github/workflows/a.yml", True),
        ("/.github/", "x/.github/a.yml", False),
        ("/futureagi/accounts/", "futureagi/accounts_v2/a.py", False),
        ("*billing*", "a/billing/b.py", True),
        ("*billing*", "a/Billing.jsx", False),
        ("/futureagi/**/*api_key*", "futureagi/a/b/api_key.py", True),
        ("/futureagi/tfc/ee_gating.py", "futureagi/tfc/ee_gatingXpy", False),
        ("*billing*", "a\nbilling", True),
        ("apps/", "x/apps/a.md", True),  # a trailing slash alone does not anchor
        ("apps/", "x/apps", False),
    )

    def test_matcher_vectors(self):
        for pattern, path, expected in self.VECTORS:
            with self.subTest(pattern=pattern, path=path):
                self.assertEqual(
                    bool(train.codeowners_regex(pattern).fullmatch(path)), expected
                )

    def test_same_matcher_as_ci_scope(self):
        try:
            import ci_scope
        except ImportError:
            self.skipTest("no ci_scope.py next to the train")
        if not hasattr(ci_scope, "codeowners_regex"):
            self.skipTest("ci_scope has no CODEOWNERS matcher yet")
        for pattern, path, _ in self.VECTORS:
            with self.subTest(pattern=pattern, path=path):
                self.assertEqual(
                    bool(train.codeowners_regex(pattern).fullmatch(path)),
                    bool(ci_scope.codeowners_regex(pattern).fullmatch(path)),
                )

    def test_last_match_wins_and_needs_owners(self):
        self.assertEqual(
            train.code_owned(["futureagi/accounts/a.py"], self.TEXT),
            "futureagi/accounts/a.py",
        )
        self.assertIsNone(
            train.code_owned(["futureagi/accounts/public/a.py"], self.TEXT),
            "a later line with no owners",
        )
        self.assertIsNone(train.code_owned(["futureagi/x.py"], self.TEXT))
        self.assertIsNone(
            train.code_owned(["futureagi/accounts/a.py"], ""), "no CODEOWNERS on dev"
        )

    def test_code_owned_member_goes_alone(self):
        node = pr_node(
            1, files=("futureagi/x.py", "frontend/src/BillingPage.jsx", "a/billing.py")
        )
        self.assertEqual(
            train.exclusion(view(node), cfg(codeowners=self.TEXT), {}),
            "code-owned path, goes alone (a/billing.py)",
        )
        self.assertIsNone(
            train.exclusion(view(pr_node(1)), cfg(codeowners=self.TEXT), {})
        )

    def test_real_codeowners_on_this_tree(self):
        path = SCRIPTS.parent / "CODEOWNERS"
        if not path.exists():
            self.skipTest("no CODEOWNERS")
        text = path.read_text()
        self.assertIsNotNone(train.code_owned([".github/CODEOWNERS"], text))
        self.assertIsNone(train.code_owned(["futureagi/tracer/views/span.py"], text))


class Exclusions(unittest.TestCase):
    def reason(self, node, c=None, reserved=None):
        return train.exclusion(view(node), c or cfg(), reserved or {})

    def test_clean_pr_is_eligible(self):
        self.assertIsNone(self.reason(pr_node(1)))

    def test_each_rule(self):
        cases = {
            "draft": pr_node(1, isDraft=True),
            "fork": pr_node(1, isCrossRepository=True),
            "base is main": pr_node(1, baseRefName="main"),
            "label needs-human": pr_node(1, labels=(train.ADMIT_LABEL, "needs-human")),
            "label autopilot-high": pr_node(
                1, labels=(train.ADMIT_LABEL, "autopilot-high")
            ),
            "revert": pr_node(1, title='Revert "feat: x"'),
            "already in the merge queue": pr_node(1, isInMergeQueue=True),
            "auto-merge is on": pr_node(1, autoMergeRequest={"enabledAt": "x"}),
            "conflicts with dev": pr_node(1, mergeable="CONFLICTING"),
            "too many files": pr_node(1, changedFiles=500),
            "high-risk path (.github/workflows/x.yml": pr_node(
                1, files=("a.py", ".github/workflows/x.yml")
            ),
            "not approved on this head": pr_node(1, reviewDecision="REVIEW_REQUIRED"),
        }
        for expected, node in cases.items():
            with self.subTest(expected):
                self.assertIn(expected, self.reason(node) or "eligible")

    def test_reserved(self):
        self.assertEqual(self.reason(pr_node(1), reserved={1: 77}), "in rollup #77")

    def test_red_check(self):
        node = pr_node(1)
        node["_check_runs"] = green()[:2]
        self.assertEqual(
            self.reason(node), "checks not green: Python Lint Pass=missing"
        )

    def test_approval_must_be_on_this_head(self):
        node = pr_node(1)
        for review in node["reviews"]["nodes"]:
            if review["state"] == "APPROVED":
                review["commit"]["oid"] = sha(99)
        self.assertEqual(self.reason(node), "not approved on this head")

    def test_high_globs_from_policy(self):
        node = pr_node(1, files=("ee/billing.py",))
        self.assertIsNone(self.reason(node))
        self.assertIn(
            "matches ee/**", self.reason(node, cfg(high_globs=(".github/**", "ee/**")))
        )

    def with_verdict(self, *reviews, **over):
        node = pr_node(1, **over)
        node["reviews"]["nodes"][-1:] = list(reviews)
        return node

    def test_verdict_tier(self):
        node = self.with_verdict(verdict_review(sha(1), tier="high"))
        self.assertEqual(self.reason(node), "review tier high: goes alone")
        node = self.with_verdict(verdict_review(sha(1), verdict="changes"))
        self.assertEqual(self.reason(node), "review verdict changes")

    def test_contract_1_medium_is_never_treated_as_low(self):
        medium = "medium tier: waits for an approval"
        for review in (
            verdict_review(sha(1), tier="medium"),
            verdict_review(sha(1), tier_check="medium"),
            verdict_review(sha(1), needs_person=True),
        ):
            node = without_person(self.with_verdict(review))
            with self.subTest(review=review["body"][-160:]):
                # The bot's approval is on the head; medium still waits for a person.
                self.assertIn(medium, self.reason(node))
        self.assertIn(
            medium, self.reason(without_person(pr_node(1)), cfg(tight=True)), "tight: low is medium"
        )

    def test_medium_needs_a_writer_who_is_not_the_author_nor_a_bot(self):
        def approval(login, kind="User"):
            return {
                "state": "APPROVED",
                "commit": {"oid": sha(1)},
                "author": {"__typename": kind, "login": login},
            }

        verdict = verdict_review(sha(1), tier="medium")
        cases = [
            ([approval("author-a")], {"author-a": "admin"}, False),
            ([approval("devbot", "Bot")], {"devbot": "write"}, False),
            ([approval("reader")], {"reader": "read"}, False),
            ([approval("Writer")], {"writer": "write"}, True),
            ([approval("boss")], {"boss": "admin"}, True),
        ]
        for approvals, permissions, eligible in cases:
            node = without_person(self.with_verdict(verdict, *approvals))
            node["_permissions"].update(permissions)
            with self.subTest(approvals=approvals):
                self.assertEqual(self.reason(node) is None, eligible, self.reason(node))
        stale = without_person(self.with_verdict(verdict, {**approval("w"), "commit": {"oid": sha(7)}}))
        stale["_permissions"].update({"w": "write"})
        self.assertIsNotNone(self.reason(stale), "the approval must be on this head")

    def test_contract_2_unknown_tier_is_high(self):
        for tier in ("Medium", "", None, "critical", ["low"], 1):
            with self.subTest(tier=tier):
                self.assertEqual(
                    train.final_tier(
                        {
                            "tier": tier,
                            "tier_check": {"tier": "low"},
                            "needs_person": False,
                        }
                    ),
                    "high",
                )
                self.assertEqual(
                    train.final_tier(
                        {
                            "tier": "low",
                            "tier_check": {"tier": tier},
                            "needs_person": False,
                        }
                    ),
                    "high",
                )
        self.assertEqual(
            train.final_tier({"tier": "low", "needs_person": False}),
            "high",
            "missing tier_check",
        )
        node = self.with_verdict(verdict_review(sha(1), tier_check=None))
        self.assertEqual(self.reason(node), "review tier high: goes alone")

    def test_contract_3_ordinal_max(self):
        def tier(a, b):
            return train.final_tier(
                {
                    "tier": a,
                    "tier_check": {"tier": b, "input": "diff-only"},
                    "needs_person": False,
                }
            )

        self.assertEqual(tier("low", "medium"), "medium")
        self.assertEqual(tier("medium", "low"), "medium")
        self.assertEqual(tier("medium", "high"), "high")
        self.assertEqual(tier("high", "medium"), "high")
        self.assertEqual(tier("low", "low"), "low")

    def test_a_tier_check_that_is_not_diff_only_counts_as_high(self):
        # The approval job's rule: only a judgement of the diff alone votes.
        for check in (
            {"tier": "low", "input": "diff"},
            {"tier": "low"},
            {"tier": "low", "input": "Diff-only"},
        ):
            with self.subTest(check=check):
                self.assertEqual(
                    train.final_tier(
                        {"tier": "low", "tier_check": check, "needs_person": False}
                    ),
                    "high",
                )
        review = verdict_review(sha(1))
        review["body"] = review["body"].replace('"diff-only"', '"diff"')
        node = self.with_verdict(review)
        self.assertEqual(self.reason(node), "review tier high: goes alone")

    def test_missing_or_stale_verdict(self):
        stale = self.with_verdict(verdict_review(sha(42)))
        self.assertEqual(self.reason(stale), "no review verdict on this head")
        self.assertEqual(
            train.Config().require_verdict, True, "required unless set to false"
        )

    def test_verdict_off_applies_the_medium_rule(self):
        # The operator opt-out: no verdict needs a person's approval, not the bot's.
        node = without_person(self.with_verdict())
        off = cfg(require_verdict=False)
        self.assertIn("medium tier", self.reason(node, off))
        node["reviews"]["nodes"].append(
            {
                "state": "APPROVED",
                "commit": {"oid": sha(1)},
                "author": {"__typename": "User", "login": "w"},
            }
        )
        node["_permissions"].update({"w": "write"})
        self.assertIsNone(self.reason(node, off))

    def test_red_label_holds_only_on_the_red_head(self):
        node = pr_node(1, labels=(train.ADMIT_LABEL, train.RED_LABEL))
        self.assertEqual(
            self.reason(node), "red alone on this head", "no marker: fail closed"
        )
        node["comments"] = {
            "nodes": [
                {
                    "author": {"__typename": "Bot", "login": "github-actions"},
                    "body": f"<!-- devflow-train:red pr=1 sha={sha(1)} -->",
                }
            ]
        }
        self.assertEqual(self.reason(node), "red alone on this head")
        node["comments"] = {
            "nodes": [
                {
                    "author": {"login": "someone"},
                    "body": f"<!-- devflow-train:red pr=1 sha={sha(5)} -->",
                }
            ]
        }
        self.assertIsNone(
            train.red_sha(node, 1), "only github-actions[bot] markers count"
        )


class Batching(unittest.TestCase):
    def ms(self, *numbers, apps=None):
        apps = apps or {}
        return [
            {"number": n, "author": "x", "migration_apps": apps.get(n, set())}
            for n in numbers
        ]

    def test_round_robin(self):
        ms = [
            {"number": n, "author": a}
            for n, a in [(5, "x"), (1, "x"), (2, "y"), (9, "z"), (3, "x"), (4, "y")]
        ]
        self.assertEqual(
            [m["number"] for m in train.round_robin(ms)], [1, 2, 9, 3, 4, 5]
        )

    def numbers(self, batches):
        return [[m["number"] for m in b] for b in batches]

    def test_dynamic_size(self):
        items = self.ms(*range(10))
        self.assertEqual(
            self.numbers(train.plan_batches(items, 4, 6)),
            [[0, 1, 2], [3, 4, 5], [6, 7, 8], [9]],
        )
        self.assertEqual(
            self.numbers(train.plan_batches(items, 1, 6)), [[0, 1, 2, 3, 4, 5]]
        )
        self.assertEqual(self.numbers(train.plan_batches(items[:2], 4, 6)), [[0], [1]])
        self.assertEqual(train.plan_batches(items, 0, 6), [])
        self.assertEqual(train.plan_batches([], 3, 6), [])

    def test_one_migration_leaf_per_app_per_batch(self):
        items = self.ms(
            1,
            2,
            3,
            4,
            apps={
                1: {"futureagi/tracer"},
                2: {"futureagi/tracer"},
                3: {"futureagi/simulate"},
            },
        )
        self.assertEqual(
            self.numbers(train.plan_batches(items, 1, 6)),
            [[1, 3, 4]],
            "#2 waits for the next tick",
        )
        self.assertEqual(
            self.numbers(train.plan_batches(items, 2, 6)), [[1, 3], [2, 4]]
        )

    def test_slots(self):
        c = cfg()
        self.assertEqual(train.train_slots(c, open_rollups=0, queue_entries=0), 4)
        self.assertEqual(train.train_slots(c, open_rollups=1, queue_entries=4), 2)
        self.assertEqual(train.train_slots(c, open_rollups=4, queue_entries=4), 0)
        self.assertEqual(train.train_slots(c, open_rollups=0, queue_entries=9), 0)


class Manifest(unittest.TestCase):
    def test_round_trip(self):
        manifest = train.new_manifest(sha(9000), [view(pr_node(3)), view(pr_node(1))])
        body = train.render_manifest(manifest)
        self.assertEqual(train.parse_manifest(body), manifest)
        self.assertEqual(train.rollup_title(manifest), "chore(train): land #3, #1")
        self.assertRegex(
            train.BRANCH_PREFIX + manifest["id"], r"^chore/[A-Za-z0-9][A-Za-z0-9._-]+$"
        )

    def test_body_passes_docs_declaration(self):
        body = train.render_manifest(train.new_manifest(sha(9000), [view(pr_node(1))]))
        lines = [
            line for line in body.splitlines() if line.lstrip().startswith("Docs:")
        ]
        self.assertEqual(
            lines,
            [
                "Docs: none (batch train rollup; each member pull request declares its own docs)"
            ],
        )

    def test_rejects_other_bodies(self):
        for body in (
            "",
            "hello",
            train.MANIFEST_MARKER + "\nno json",
            train.MANIFEST_MARKER + '\n```json\n{"schema":"x"}\n```',
        ):
            with self.subTest(body=body[:20]), self.assertRaises(ValueError):
                train.parse_manifest(body)

    def test_merge_message_matches_github(self):
        msg = train.merge_message(
            {"pr": 7, "branch": "feat/x", "title": "feat: x"}, "future-agi"
        )
        self.assertEqual(msg, "Merge pull request #7 from future-agi/feat/x\n\nfeat: x")


class RedHandling(unittest.TestCase):
    def manifest(self, n, retries=0):
        return {
            "infra_retries": retries,
            "members": [{"pr": i, "head_sha": sha(i)} for i in range(1, n + 1)],
        }

    def test_retry_only_infra_and_only_once(self):
        self.assertEqual(train.on_red(self.manifest(6), "infra"), {"action": "retry"})
        self.assertEqual(
            train.on_red(self.manifest(6, retries=1), "infra"),
            {"action": "split", "halves": [[1, 2, 3], [4, 5, 6]]},
        )
        self.assertEqual(
            train.on_red(self.manifest(6), "red"),
            {"action": "split", "halves": [[1, 2, 3], [4, 5, 6]]},
        )
        self.assertEqual(train.on_red(self.manifest(3), "red")["halves"], [[1, 2], [3]])
        self.assertEqual(train.on_red(self.manifest(1), "infra"), {"action": "retry"})
        self.assertEqual(
            train.on_red(self.manifest(1), "red"),
            {"action": "evict", "pr": 1, "sha": sha(1)},
        )

    # The shape of .github/ci-infra-signatures.json (ci-gate's registry-retry.sh
    # reads it with grep -Ei): entries copied from its first version, plus the
    # proposed apt entry.
    SIGNATURES = json.dumps(
        [
            {
                "name": "registry rate limit",
                "class": "rate_limit",
                "regex": "too many requests|toomanyrequests|(unexpected status.*|status code[:= ]*|HTTP(/[0-9.]+)?[[:space:]]+)429([^0-9]|$)",
            },
            {
                "name": "network timeout or reset",
                "class": "transient",
                "regex": "i/o timeout|TLS handshake timeout|connection reset by peer",
            },
            {
                "name": "auth, tag or configuration error",
                "class": "permanent",
                "regex": "unauthorized|denied|manifest unknown|not found|(unexpected status.*|status code[:= ]*|HTTP(/[0-9.]+)?[[:space:]]+)4[0-9]{2}([^0-9]|$)",
            },
            {
                "name": "apt index download timeout",
                "class": "apt",
                "regex": "Get:[0-9]+ [^[:space:]]*ubuntu\\.com.*exit code 124",
            },
        ]
    )

    def test_classify_failure(self):
        sigs = train.parse_signatures(self.SIGNATURES)
        apt = "Get:12 http://azure.archive.ubuntu.com/ubuntu noble/main amd64 Packages\nmore\n##[error]Process completed with exit code 124."
        rate = "Error response from daemon: toomanyrequests: rate limit, allowed: 44000/minute"
        rate_429 = "Error: unexpected status from HEAD request: 429 Too Many Requests"
        reset = "read tcp 10.1.0.4:443: connection reset by peer"
        reset_and_denied = reset + "\nError: pull access denied for x"
        test = "FAILED futureagi/tracer/tests/test_x.py::test_y - AssertionError"
        self.assertEqual(
            train.classify_failure([apt, rate, rate_429, reset], sigs), "infra"
        )
        self.assertEqual(
            train.classify_failure([rate_429], sigs),
            "infra",
            "a 429 also hits the permanent 4xx rule; rate_limit wins",
        )
        self.assertEqual(
            train.classify_failure([reset_and_denied], sigs),
            "red",
            "transient plus permanent is not infra",
        )
        self.assertEqual(
            train.classify_failure([apt, test], sigs),
            "red",
            "one real failure makes it red",
        )
        self.assertEqual(
            train.classify_failure([], sigs),
            "red",
            "no failed job found: unknown is red",
        )
        self.assertEqual(
            train.classify_failure(["exit code 124 after pytest"], sigs), "red"
        )
        self.assertEqual(
            train.classify_failure([rate], []),
            "red",
            "without the file nothing is infra",
        )

    # A real job log: the step header echoes the script (here one that names
    # the rate-limit signature), then the step's output follows.
    ECHO = (
        "2026-11-26T16:55:22.4700000Z ##[group]Run set -eu\n"
        '2026-11-26T16:55:22.4769612Z \x1b[36;1m    red "Error response from daemon: toomanyrequests: rate limit"\x1b[0m\n'
        "2026-11-26T16:55:22.4800000Z shell: /usr/bin/bash -e {0}\n"
        "2026-11-26T16:55:22.4843742Z ##[endgroup]\n"
    )

    def logs_of(self, text):
        """failed_job_logs over one failed suite job whose log is `text`."""

        class OneJob(train.GitHub):
            def call(self, method, path, body=None, ok=(200, 201, 204)):
                return {
                    "jobs": [
                        {"id": 1, "name": "Backend suite", "conclusion": "failure"},
                        {
                            "id": 2,
                            "name": "Backend Tests Pass",
                            "conclusion": "failure",
                        },
                    ]
                }

            def request(self, method, path, body=None, raw=False):
                return 200, text

        return OneJob("t", "o/r").failed_job_logs([{"id": 9}], REQUIRED)

    def test_echoed_script_is_not_infra(self):
        sigs = train.parse_signatures(self.SIGNATURES)
        log = self.ECHO + (
            "2026-11-26T16:58:42.5093011Z fullfail/ present: the full suite is red\n"
            "2026-11-26T16:58:42.5108015Z ##[error]Process completed with exit code 1.\n"
        )
        self.assertEqual(
            train.classify_failure(self.logs_of(log), sigs),
            "red",
            "a signature in the echoed step script is source, not output",
        )

    def test_infra_output_after_an_echo_is_infra(self):
        sigs = train.parse_signatures(self.SIGNATURES)
        log = self.ECHO + (
            "2026-11-26T16:58:42.5093011Z Error response from daemon: toomanyrequests: rate limit\n"
            "2026-11-26T16:58:42.5108015Z ##[error]Process completed with exit code 1.\n"
        )
        self.assertEqual(train.classify_failure(self.logs_of(log), sigs), "infra")

    def test_infra_inside_another_group_is_kept(self):
        sigs = train.parse_signatures(self.SIGNATURES)
        log = (
            "2026-11-26T16:55:20.1000000Z ##[group]Fetching the repository\n"
            "2026-11-26T16:55:50.1000000Z Error: net/http: TLS handshake timeout\n"
            "2026-11-26T16:55:50.2000000Z ##[endgroup]\n"
            "2026-11-26T16:55:50.3000000Z ##[error]The process '/usr/bin/git' failed with exit code 128\n"
        )
        self.assertEqual(
            train.classify_failure(self.logs_of(log), sigs),
            "infra",
            "only the echoed Run header is dropped; a tool's own group is output",
        )

    def test_signature_lines(self):
        sigs = {name: rx for name, _, rx in train.parse_signatures(self.SIGNATURES)}
        spanning = "unexpected status from GET\nsomething else 429 x"
        self.assertIsNone(
            sigs["registry rate limit"].search(spanning),
            "grep semantics: .* stays within one line",
        )
        with self.assertRaises(ValueError):
            train.parse_signatures(
                json.dumps(
                    [{"name": "x", "class": "transient", "regex": "[[:blank:]]"}]
                )
            )
        self.assertEqual(train.parse_signatures(None), [])

    def test_removal_kind(self):
        # The reasons the dev queue writes (read 27 Nov 2026).
        self.assertEqual(train.removal_kind("merge_conflict"), "conflict")
        self.assertEqual(train.removal_kind("manual"), "manual")
        self.assertEqual(train.removal_kind("failed_checks"), "red")
        # Unknown is never red: a person decides and no member is marked red.
        for reason in (None, "", "CI_FAILURE", "something_new"):
            with self.subTest(reason=reason):
                self.assertEqual(train.removal_kind(reason), "manual")


class Plan(unittest.TestCase):
    MEMBERS = ((1, sha(1)), (2, sha(2)))

    def test_dark_tick_batches_eligible(self):
        prs = [
            pr_node(n, author=a)
            for n, a in [(1, "x"), (2, "x"), (3, "y"), (4, "needs")]
        ]
        prs[3]["labels"]["nodes"].append({"name": "needs-human"})
        out = train.plan(data(prs, queue=[50]), REQUIRED, cfg())
        self.assertEqual(out["eligible"], [1, 3, 2])
        self.assertEqual(
            (out["train_slots"], out["batches"], out["busy"]),
            (4, [[1], [3], [2]], True),
        )
        self.assertIn("needs-human", out["excluded"][4])

    def test_eligible_work_without_a_slot_keeps_the_loop_awake(self):
        result = train.plan(data([pr_node(1)], queue=range(100, 106)), REQUIRED, cfg())
        self.assertEqual((result["train_slots"], result["batches"]), (0, []))
        self.assertTrue(result["busy"])

    def test_idle_is_not_busy(self):
        self.assertFalse(train.plan(data(), REQUIRED, cfg())["busy"])

    def test_a_member_waiting_only_on_running_checks_keeps_the_loop_awake(self):
        def member(*states):
            node = pr_node(1)
            node["_check_runs"] = [
                run(r["context"], conclusion, status)
                for r, (status, conclusion) in zip(REQUIRED, states)
            ]
            return node

        done, running = ("COMPLETED", "SUCCESS"), ("IN_PROGRESS", None)
        failed = ("COMPLETED", "FAILURE")
        for states, busy in (
            ((done, running, running), True),
            ((done, done, running), True),
            ((done, failed, running), False),
            ((done, done), False),  # Python Lint Pass missing: no run started
        ):
            with self.subTest(states=states):
                result = train.plan(data([member(*states)]), REQUIRED, cfg())
                self.assertIn("checks not green", result["excluded"][1])
                self.assertEqual(result["busy"], busy)

    def test_open_rollup_reserves_and_uses_a_slot(self):
        prs = [pr_node(n) for n in (1, 2, 3)]
        out = train.plan(
            data(prs, [rollup_node(70, self.MEMBERS, in_queue=True)], queue=[70]),
            REQUIRED,
            cfg(),
        )
        self.assertEqual(out["rollup_actions"], [{"action": "wait", "rollup": 70}])
        self.assertEqual(out["excluded"], {1: "in rollup #70", 2: "in rollup #70"})
        self.assertEqual((out["train_slots"], out["batches"]), (3, [[3]]))

    def test_stale_member_rebuilds_and_frees_the_rest(self):
        moved = without_person(pr_node(1, head=sha(111)))
        moved["reviews"]["nodes"][0]["commit"]["oid"] = sha(
            1
        )  # dismissed: approval was on the old head
        prs = [moved, pr_node(2)]
        out = train.plan(
            data(prs, [rollup_node(70, self.MEMBERS, in_queue=True)], queue=[70]),
            REQUIRED,
            cfg(),
        )
        self.assertEqual(
            out["rollup_actions"], [{"action": "rebuild", "drop": [1], "rollup": 70}]
        )
        self.assertEqual(
            out["eligible"], [2], "#1's new head has no approval; #2 re-batches at once"
        )

    def test_member_gone_or_high_is_stale(self):
        out = train.plan(
            data([pr_node(2)], [rollup_node(70, self.MEMBERS)]), REQUIRED, cfg()
        )
        self.assertEqual(out["rollup_actions"][0]["drop"], [1])
        high = pr_node(1, labels=(train.ADMIT_LABEL, "needs-human"))
        out = train.plan(
            data([high, pr_node(2)], [rollup_node(70, self.MEMBERS)]), REQUIRED, cfg()
        )
        self.assertEqual(out["rollup_actions"][0]["drop"], [1])

    def test_a_member_whose_approval_was_withdrawn_is_stale(self):
        dismissed = pr_node(1)
        dismissed["reviews"]["nodes"][0]["state"] = "DISMISSED"
        dismissed["reviewDecision"] = "REVIEW_REQUIRED"
        changes = pr_node(1, reviewDecision="CHANGES_REQUESTED")
        for name, node in (("dismissed", dismissed), ("changes requested", changes)):
            with self.subTest(name):
                out = train.plan(
                    data([node, pr_node(2)], [rollup_node(70, self.MEMBERS)]),
                    REQUIRED,
                    cfg(),
                )
                self.assertEqual(
                    out["rollup_actions"][0],
                    {"action": "rebuild", "drop": [1], "rollup": 70},
                )

    def test_pushed_rollup_is_never_reapproved(self):
        moved = rollup_node(70, self.MEMBERS)
        moved["headRefOid"] = sha(7777)
        moved["reviews"]["nodes"][0]["commit"]["oid"] = sha(7777)
        out = train.plan(data([pr_node(1), pr_node(2)], [moved]), REQUIRED, cfg())
        self.assertEqual(out["rollup_actions"][0]["action"], "rebuild")
        foreign = rollup_node(70, self.MEMBERS)
        foreign["_chain"] = None  # a commit that is not a member merge
        out = train.plan(data([pr_node(1), pr_node(2)], [foreign]), REQUIRED, cfg())
        self.assertEqual(out["rollup_actions"][0]["action"], "rebuild")
        self.assertEqual(out["batches"], [[1], [2]])

    def test_red_own_checks_split_without_the_queue(self):
        red = green()[:2] + [run("Python Lint Pass", "FAILURE")]
        node = rollup_node(70, self.MEMBERS, checks=red)
        out = train.plan(
            data([pr_node(1), pr_node(2)], [node]), REQUIRED, cfg(), {70: "red"}
        )
        self.assertEqual(
            out["rollup_actions"][0],
            {"action": "split", "halves": [[1], [2]], "signal": "checks", "rollup": 70},
        )
        self.assertEqual(out["train_slots"], 2, "two halves take two of the four slots")
        infra = train.plan(
            data([pr_node(1), pr_node(2)], [node]), REQUIRED, cfg(), {70: "infra"}
        )
        self.assertEqual(infra["rollup_actions"][0]["action"], "retry")

    def test_queue_removal_retries_once_and_is_seen_once(self):
        prs = [pr_node(1), pr_node(2)]
        first = train.plan(
            data(prs, [rollup_node(70, self.MEMBERS, removals=1)]),
            REQUIRED,
            cfg(),
            {70: "infra"},
        )
        self.assertEqual(
            first["rollup_actions"][0],
            {"action": "retry", "signal": "queue", "rollup": 70},
        )
        self.assertEqual(first["excluded"], {1: "in rollup #70", 2: "in rollup #70"})
        seen = train.plan(
            data(
                prs,
                [
                    rollup_node(
                        70, self.MEMBERS, infra_retries=1, removals=1, removals_seen=1
                    )
                ],
            ),
            REQUIRED,
            cfg(),
        )
        self.assertEqual(
            seen["rollup_actions"][0]["action"],
            "enqueue",
            "an already-handled removal is not red again",
        )
        again = train.plan(
            data(
                prs,
                [
                    rollup_node(
                        70, self.MEMBERS, infra_retries=1, removals=2, removals_seen=1
                    )
                ],
            ),
            REQUIRED,
            cfg(),
            {70: "infra"},
        )
        self.assertEqual(again["rollup_actions"][0]["action"], "split")

    def test_unclassified_red_is_red(self):
        out = train.plan(
            data([pr_node(1), pr_node(2)], [rollup_node(70, self.MEMBERS, removals=1)]),
            REQUIRED,
            cfg(),
        )
        self.assertEqual(out["rollup_actions"][0]["action"], "split")

    def test_base_red_pauses_new_rollups_and_closes_red_ones(self):
        one = rollup_node(
            80,
            [(1, sha(1)), (2, sha(2))],
            checks=[run("Backend Tests Pass", "FAILURE")] + green()[1:],
        )
        solo = rollup_node(
            81,
            [(3, sha(3))],
            checks=[run("Backend Tests Pass", "FAILURE")] + green()[1:],
        )
        busy = rollup_node(82, [(4, sha(4))], checks=[run("Backend Tests Pass", status="IN_PROGRESS")] + green()[1:])
        members = [pr_node(n) for n in (1, 2, 3, 4, 5)]
        result = train.plan(
            data(members, [one, solo, busy], base_red=[900]),
            REQUIRED,
            cfg(),
            {80: "red", 81: "red"},
        )
        acts = {a["rollup"]: a["action"] for a in result["rollup_actions"]}
        self.assertEqual(acts, {80: "close-red", 81: "close-red", 82: "wait"})
        self.assertEqual(result["batches"], [], "no new rollups while paused")
        self.assertEqual(
            result["eligible"], [1, 2, 3, 5], "closed members stay eligible"
        )
        self.assertTrue(result["busy"], "waiting work keeps the loop awake")
        self.assertEqual(result["paused_by"], [900])
        resumed = train.plan(data(members, [], base_red=[]), REQUIRED, cfg())
        self.assertEqual(resumed["batches"], [[1, 2], [3, 4], [5]])

    def test_execute_close_red_neither_splits_nor_evicts(self):
        app, actions = FakeGitHub({}), FakeGitHub({})
        live = train.Live("o/r", app, actions)
        red = rollup_node(
            81,
            [(3, sha(3))],
            checks=[run("Backend Tests Pass", "FAILURE")] + green()[1:],
        )
        result = train.plan(
            data([pr_node(3)], [red], base_red=[900]), REQUIRED, cfg(), {81: "red"}
        )
        train.execute(result, live, {})
        writes = [(c[0], c[1]) for c in app.calls + actions.calls]
        self.assertIn(("PATCH", "repos/o/r/pulls/81"), writes)
        self.assertFalse(
            any("/merges" in w[1] or "/labels" in w[1] for w in writes), writes
        )
        comment = next(
            c[2]["body"] for c in app.calls if c[1].endswith("/issues/81/comments")
        )
        self.assertIn("#900", comment)

    def test_conflict_and_manual_removals(self):
        prs = [pr_node(1)]
        conflict = train.plan(
            data(
                prs,
                [rollup_node(70, [(1, sha(1))], removals=1, reason="MERGE_CONFLICT")],
            ),
            REQUIRED,
            cfg(),
        )
        self.assertEqual(conflict["rollup_actions"][0]["action"], "rebuild")
        self.assertEqual(conflict["batches"], [[1]])
        manual = train.plan(
            data(prs, [rollup_node(70, [(1, sha(1))], removals=1, reason="MANUAL")]),
            REQUIRED,
            cfg(),
        )
        self.assertEqual(manual["rollup_actions"][0]["action"], "release")

    def test_evicted_member_does_not_rebatch_in_the_same_tick(self):
        out = train.plan(
            data([pr_node(1)], [rollup_node(70, [(1, sha(1))], removals=1)]),
            REQUIRED,
            cfg(),
            {70: "red"},
        )
        self.assertEqual(out["rollup_actions"][0]["action"], "evict")
        self.assertEqual(out["excluded"], {1: "red alone on this head"})
        self.assertEqual(out["batches"], [])

    def test_red_label_cleared_on_new_head(self):
        node = pr_node(1, labels=(train.ADMIT_LABEL, train.RED_LABEL))
        node["comments"] = {
            "nodes": [
                {
                    "author": {"__typename": "Bot", "login": "github-actions"},
                    "body": f"<!-- devflow-train:red pr=1 sha={sha(5)} -->",
                }
            ]
        }
        out = train.plan(data([node]), REQUIRED, cfg())
        self.assertEqual(
            out["label_changes"],
            [{"pr": 1, "remove": train.RED_LABEL, "why": "new head since red"}],
        )
        self.assertEqual(out["batches"], [[1]])

    def test_foreign_rollup_is_ignored(self):
        fake = rollup_node(70, [(1, sha(1))], author="someone")
        out = train.plan(data([pr_node(1)], [fake]), REQUIRED, cfg())
        self.assertEqual((out["rollup_actions"], out["batches"]), ([], [[1]]))

    def test_tree_parity(self):
        self.assertTrue(
            train.tree_parity({"tree": {"sha": "t"}}, {"tree": {"sha": "t"}})
        )
        self.assertFalse(
            train.tree_parity({"tree": {"sha": "t"}}, {"tree": {"sha": "u"}})
        )
        self.assertFalse(train.tree_parity({}, {}))


class FakeGitHub:
    """Records calls; answers from a routing table of (method, path prefix) -> (status, body)."""

    def __init__(self, routes, token="t"):
        self.routes, self.calls, self.token = routes, [], token

    def request(self, method, path, body=None, raw=False):
        self.calls.append((method, path, body))
        for (m, prefix), answer in self.routes.items():
            if m == method and path.startswith(prefix):
                return answer(body) if callable(answer) else answer
        return (204, None) if method == "DELETE" else (200, {})

    def call(self, method, path, body=None, ok=(200, 201, 204)):
        status, out = self.request(method, path, body)
        if status not in ok:
            raise RuntimeError(f"{method} {path}: {status}")
        return out

    def graphql(self, query, **variables):
        self.calls.append(
            ("GRAPHQL", re.search(r"\{\s*(\w+)", query).group(1), variables)
        )
        return {}

    def forget(self, sha):
        self.calls.append(("FORGET", sha, None))


class Liveness(unittest.TestCase):
    def test_loop_runs_while_busy_and_stops_after_idle(self):
        keep = train.keep_looping
        self.assertTrue(
            keep(now=100, deadline=19800, last_busy=100, idle_s=600, interval=90)
        )
        self.assertTrue(
            keep(now=699, deadline=19800, last_busy=100, idle_s=600, interval=90)
        )
        self.assertFalse(
            keep(now=700, deadline=19800, last_busy=100, idle_s=600, interval=90),
            "idle 10 min",
        )
        self.assertFalse(
            keep(now=19750, deadline=19800, last_busy=19750, idle_s=600, interval=90),
            "deadline",
        )
        self.assertFalse(
            keep(now=0, deadline=0, last_busy=0, idle_s=600, interval=90),
            "loop 0: one tick",
        )

    def test_app_token_is_minted_for_this_repo_and_again_before_it_expires(self):
        clock, signed, made = [1_800_000_000.0], [], []

        class Client:
            def __init__(self, token, repo):
                self.token, self.repo = token, repo
                made.append(self)
                self.calls = []

            def call(self, method, path, body=None):
                self.calls.append((method, path, body))
                if path.endswith("/installation"):
                    return {"id": 42}
                expires = time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(clock[0] + 3600)
                )
                return {"token": f"ghs_{len(made)}", "expires_at": expires}

            def request(self, method, path, body=None):
                self.calls.append((method, path, body))
                return 204, None

        def sign(data, key):
            signed.append((data, key))
            return b"sig"

        minter = train.AppToken(
            "123", "KEY", "o/r", sign=sign, client=Client, now=lambda: clock[0]
        )
        self.assertEqual(minter.get(), "ghs_1")
        jwt = made[0].token
        head, body, _sig = jwt.split(".")
        claims = json.loads(base64.urlsafe_b64decode(body + "=="))
        self.assertEqual(
            claims,
            {"iat": 1_800_000_000 - 60, "exp": 1_800_000_000 + 540, "iss": "123"},
        )
        self.assertEqual(
            json.loads(base64.urlsafe_b64decode(head + "==")),
            {"alg": "RS256", "typ": "JWT"},
        )
        self.assertEqual(signed, [(f"{head}.{body}".encode(), "KEY")])
        self.assertEqual(
            made[0].calls[1],
            ("POST", "app/installations/42/access_tokens", {"repositories": ["r"]}),
        )
        clock[0] += 2999  # 601 s left: keep it
        self.assertEqual(minter.get(), "ghs_1")
        clock[0] += 2  # under 10 min left: mint again
        self.assertEqual(minter.get(), "ghs_2")
        self.assertEqual(minter.minted, 2)
        minter.revoke()
        self.assertEqual(made[-1].token, "ghs_2")
        self.assertEqual(made[-1].calls, [("DELETE", "installation/token", None)])

    def test_a_live_crash_posts_one_slack_line_only_when_the_heartbeat_is_live(self):
        import os
        from unittest import mock

        env = {
            "REPO": "o/r",
            "GH_TOKEN": "t",
            "SLACK_WEBHOOK_URL": "https://hooks.example/x",
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REPOSITORY": "o/r",
            "GITHUB_RUN_ID": "77",
        }
        cases = (
            (["tick", "--mode", "live"], {"DEVFLOW_HEARTBEAT": "live"}, 1),
            (["tick", "--mode", "live"], {}, 0),
            (["tick", "--mode", "live"], {"DEVFLOW_HEARTBEAT": "dry"}, 0),
            (["tick"], {"DEVFLOW_HEARTBEAT": "live"}, 0),  # dark mode
        )
        for argv, extra, posts in cases:
            with (
                self.subTest(argv=argv, env=extra),
                mock.patch.dict(os.environ, {**env, **extra}, clear=True),
                mock.patch.object(
                    train, "GitHub", side_effect=ValueError("bad policy file")
                ),
                mock.patch.object(train, "post_slack") as post,
            ):
                with self.assertRaisesRegex(ValueError, "bad policy file"):
                    train.main(argv)
                self.assertEqual(post.call_count, posts)
                if posts:
                    url, text = post.call_args.args
                    self.assertEqual(url, "https://hooks.example/x")
                    self.assertEqual(len(text.splitlines()), 1)
                    self.assertIn("ValueError: bad policy file", text)
                    self.assertIn("https://github.com/o/r/actions/runs/77", text)

        def down(url, text):
            raise OSError("slack is down")

        with (
            mock.patch.dict(
                os.environ, {**env, "DEVFLOW_HEARTBEAT": "live"}, clear=True
            ),
            mock.patch.object(
                train, "GitHub", side_effect=ValueError("bad policy file")
            ),
            mock.patch.object(train, "post_slack", down),
            self.assertRaisesRegex(ValueError, "bad policy file"),
        ):
            train.main(["tick", "--mode", "live"])

    def test_the_live_job_passes_the_heartbeat_and_the_webhook(self):
        text = (SCRIPTS.parent / "workflows" / "devflow-train.yml").read_text()
        live = text.split("\n  live:\n", 1)[1]
        self.assertIn("SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}", live)
        self.assertIn("DEVFLOW_HEARTBEAT: ${{ vars.DEVFLOW_HEARTBEAT }}", live)
        plan = text.split("\n  plan:\n", 1)[1].split("\n  live:\n", 1)[0]
        self.assertNotIn("SLACK_WEBHOOK_URL", plan, "a pull request run gets no secret")

    def test_workflow_loops_past_the_token_hour_from_dev_only(self):
        text = (SCRIPTS.parent / "workflows" / "devflow-train.yml").read_text()
        self.assertNotIn(
            "create-github-app-token", text, "the script mints its own tokens"
        )
        self.assertNotIn("workflow_dispatch:", text, "it would need the file on main")
        self.assertIn("timeout-minutes: 345", text)
        self.assertIn("DEVFLOW_TRAIN_LOOP_MINUTES || 330", text)
        self.assertIn("environment: devflow-train", text)


class LiveWrites(unittest.TestCase):
    REPO = "future-agi/canary"

    def members(self, *numbers):
        return [view(pr_node(n)) for n in numbers]

    def test_open_rollup_merges_in_order_skips_conflicts_and_never_approves(self):
        heads = iter(["c1", "c3"])

        def merge(body):
            if body["head"] == sha(2):
                return 409, {"message": "Merge conflict"}
            return 201, {"sha": next(heads)}

        app = FakeGitHub(
            {
                ("GET", f"repos/{self.REPO}/branches/dev"): (
                    200,
                    {"commit": {"sha": "base"}},
                ),
                ("POST", f"repos/{self.REPO}/merges"): merge,
                ("POST", f"repos/{self.REPO}/pulls"): (
                    201,
                    {"number": 80, "node_id": "PR_80", "head": {"sha": "c3"}},
                ),
            }
        )
        actions = FakeGitHub({})
        out = train.Live(self.REPO, app, actions).open_rollup(self.members(1, 2, 3))
        self.assertEqual(out, {"opened": 80, "conflicts": [2]})
        merges = [c[2] for c in app.calls if c[1].endswith("/merges")]
        self.assertEqual([m["head"] for m in merges], [sha(1), sha(2), sha(3)])
        self.assertTrue(all(m["base"].startswith(train.BRANCH_PREFIX) for m in merges))
        self.assertEqual(
            merges[0]["commit_message"],
            "Merge pull request #1 from future-agi/feat/change-1\n\nfeat: change 1",
        )
        body = next(
            c[2] for c in app.calls if c[:2] == ("POST", f"repos/{self.REPO}/pulls")
        )
        manifest = train.parse_manifest(body["body"])
        self.assertEqual(
            ([m["pr"] for m in manifest["members"]], manifest["head_sha"]),
            ([1, 3], "c3"),
        )
        self.assertIn(
            (
                "POST",
                f"repos/{self.REPO}/issues/2/labels",
                {"labels": [train.CONFLICT_LABEL]},
            ),
            app.calls,
        )
        # A person approves the rollup; it is enqueued only once green.
        self.assertFalse([c for c in app.calls + actions.calls if c[1].endswith("/reviews")])
        self.assertFalse([c for c in app.calls if c[0] == "GRAPHQL"], "no auto-merge, no enqueue")
        self.assertEqual(actions.calls, [], "GITHUB_TOKEN writes nothing here")

    def test_open_rollup_refuses_a_moved_branch(self):
        app = FakeGitHub(
            {
                ("GET", f"repos/{self.REPO}/branches/dev"): (
                    200,
                    {"commit": {"sha": "base"}},
                ),
                ("POST", f"repos/{self.REPO}/merges"): (201, {"sha": "c1"}),
                ("POST", f"repos/{self.REPO}/pulls"): (
                    201,
                    {"number": 80, "node_id": "PR_80", "head": {"sha": "evil"}},
                ),
            }
        )
        actions = FakeGitHub({})
        with self.assertRaises(RuntimeError):
            train.Live(self.REPO, app, actions).open_rollup(self.members(1))
        self.assertFalse([c for c in actions.calls if c[1].endswith("/reviews")])
        self.assertIn(
            ("PATCH", f"repos/{self.REPO}/pulls/80", {"state": "closed"}), app.calls
        )

    def test_all_conflicts_delete_the_branch(self):
        app = FakeGitHub(
            {
                ("GET", f"repos/{self.REPO}/branches/dev"): (
                    200,
                    {"commit": {"sha": "base"}},
                ),
                ("POST", f"repos/{self.REPO}/merges"): (409, {}),
            }
        )
        out = train.Live(self.REPO, app, FakeGitHub({})).open_rollup(self.members(1))
        self.assertEqual(out, {"opened": None, "conflicts": [1]})
        self.assertTrue(
            any(
                c[0] == "DELETE" and "/git/refs/heads/chore/devflow-train-" in c[1]
                for c in app.calls
            )
        )
        self.assertFalse([c for c in app.calls if c[1] == f"repos/{self.REPO}/pulls"])

    def test_retry_reruns_failed_jobs_for_own_checks(self):
        app, actions = FakeGitHub({}), FakeGitHub({})
        node = rollup_node(70, [(1, sha(1))])
        rollup = train.rollup_view(node, cfg(), REQUIRED)
        train.Live(self.REPO, app, actions).retry(rollup, "checks", [{"id": 555}])
        self.assertIn(
            ("POST", f"repos/{self.REPO}/actions/runs/555/rerun-failed-jobs", None),
            actions.calls,
        )
        patched = train.parse_manifest(
            next(c[2]["body"] for c in app.calls if c[0] == "PATCH")
        )
        self.assertEqual(patched["infra_retries"], 1)

    def test_execute_split_opens_halves_then_closes(self):
        node = rollup_node(
            70,
            [(1, sha(1)), (2, sha(2))],
            checks=green()[:2] + [run("Python Lint Pass", "FAILURE")],
        )
        result = train.plan(
            data([pr_node(1), pr_node(2)], [node]), REQUIRED, cfg(), {70: "red"}
        )
        opened = []

        class Recorder(train.Live):
            def open_rollup(self, members, parent=None):
                opened.append(([m["number"] for m in members], parent))

        app = FakeGitHub({})
        train.execute(result, Recorder(self.REPO, app, FakeGitHub({})), {})
        self.assertEqual(opened, [([1], "r70"), ([2], "r70")])
        self.assertIn(
            ("PATCH", f"repos/{self.REPO}/pulls/70", {"state": "closed"}), app.calls
        )

    def test_non_infra_red_splits_at_once_with_no_rerun(self):
        node = rollup_node(
            70,
            [(1, sha(1)), (2, sha(2))],
            checks=green()[:2] + [run("Python Lint Pass", "FAILURE")],
        )
        result = train.plan(
            data([pr_node(1), pr_node(2)], [node]), REQUIRED, cfg(), {70: "red"}
        )
        self.assertEqual(result["rollup_actions"][0]["action"], "split")
        app, actions = FakeGitHub({}), FakeGitHub({})

        class Recorder(train.Live):
            def open_rollup(self, members, parent=None):
                pass

        train.execute(result, Recorder(self.REPO, app, actions), {70: [{"id": 5}]})
        self.assertFalse(
            [c for c in app.calls + actions.calls if "rerun" in str(c[1])],
            "only an all-infra red gets its one re-run",
        )

    def test_execute_evict_labels_red_and_links_the_failing_run(self):
        node = rollup_node(
            70, [(1, sha(1))], checks=green()[:2] + [run("Python Lint Pass", "FAILURE")]
        )
        result = train.plan(data([pr_node(1)], [node]), REQUIRED, cfg(), {70: "red"})
        app, actions = FakeGitHub({}), FakeGitHub({})
        url = f"https://github.com/{self.REPO}/actions/runs/555"
        train.execute(
            result,
            train.Live(self.REPO, app, actions),
            {70: [{"id": 555, "html_url": url}]},
        )
        self.assertIn(
            (
                "POST",
                f"repos/{self.REPO}/issues/1/labels",
                {"labels": [train.RED_LABEL]},
            ),
            app.calls,
        )
        comment = next(
            c[2]["body"] for c in app.calls if c[1].endswith("/issues/1/comments")
        )
        self.assertIn(url, comment)
        self.assertIn(f"<!-- devflow-train:red pr=1 sha={sha(1)} -->", comment)


SHARD = "pytest shard 3"
RED_CHECKS = [run("Backend Tests Pass", "FAILURE")] + green()[1:]


def held_on(*pairs):
    return [{"workflow": w, "job": j} for w, j in pairs]


class RedReader(train.GitHub):
    """tick()'s reader: the GraphQL payload is given, and REST answers come
    from (path prefix, body) routes, first match wins."""

    def __init__(self, payload, routes):
        super().__init__("t", "o/r")
        self.payload, self.routes, self.paths = payload, routes, []

    def gather(self):
        return json.loads(json.dumps(self.payload))

    def base_red(self):
        return list(self.payload["base_red"])

    def attach_reviewer_permissions(self, data):
        pass

    def attach_permissions(self, data):
        pass

    def attach_chains(self, data):
        pass  # the payload carries each rollup's _chain

    def attach_checks(self, data, required, cfg):
        pass

    def request(self, method, path, body=None, raw=False):
        self.paths.append(path)
        for prefix, answer in self.routes:
            if path.startswith(prefix):
                return 200, answer
        raise AssertionError(path)


def failed_job(i, name):
    return {"id": i, "name": name, "conclusion": "failure"}


def dev_push(conclusion, run_id=600):
    """The listing of dev's finished push runs of backend-ci.yml, newest first:
    a cancelled run, then one that finished."""
    return [
        (
            "repos/o/r/actions/workflows/backend-ci.yml/runs?event=push",
            {
                "workflow_runs": [
                    {"id": 601, "conclusion": "cancelled"},
                    {"id": run_id, "conclusion": conclusion},
                ]
            },
        ),
        (
            f"repos/o/r/actions/runs/{run_id}/jobs",
            {
                "jobs": [
                    failed_job(61, SHARD),
                    failed_job(62, "Backend Tests Pass"),
                ]
            },
        ),
    ]


PR_RUN_RED = [
    (
        "repos/o/r/actions/runs?event=pull_request",
        {
            "workflow_runs": [
                {
                    "id": 500,
                    "conclusion": "failure",
                    "path": ".github/workflows/backend-ci.yml",
                    "html_url": "https://github.com/o/r/actions/runs/500",
                }
            ]
        },
    ),
    (
        "repos/o/r/actions/runs/500/jobs",
        {
            "jobs": [
                failed_job(1, SHARD),
                failed_job(2, "pytest shard 4"),
                failed_job(3, "Backend Tests Pass"),
            ]
        },
    ),
    ("repos/o/r/actions/jobs/", "FAILED tests/test_x.py::test_y\n"),
]


class HeldOnRedBase(unittest.TestCase):
    """A lone red member is not evicted while dev's own push run is red on
    the same job (the breaker may not have tripped yet): it is held, and its
    rollup is rebuilt once dev is green."""

    def solo(self, **over):
        return rollup_node(81, [(3, sha(3))], checks=RED_CHECKS, **over)

    def test_a_lone_red_with_dev_red_on_the_same_job_is_held_not_evicted(self):
        jobs = held_on(("backend-ci.yml", SHARD))
        out = train.plan(
            data([pr_node(3)], [self.solo()]),
            REQUIRED,
            cfg(),
            {81: "red"},
            {81: jobs},
        )
        self.assertEqual(
            out["rollup_actions"],
            [
                {
                    "action": "hold",
                    "rollup": 81,
                    "signal": "checks",
                    "jobs": jobs,
                    "new": True,
                }
            ],
        )
        self.assertEqual(out["excluded"], {3: "held: base red (rollup #81)"})
        self.assertEqual(out["batches"], [])
        self.assertTrue(out["busy"], "a held rollup keeps the loop awake")
        green_dev = train.plan(
            data([pr_node(3)], [self.solo()]), REQUIRED, cfg(), {81: "red"}, {}
        )
        self.assertEqual(green_dev["rollup_actions"][0]["action"], "evict")

    def test_a_held_rollup_waits_then_rebuilds_once_dev_is_green(self):
        jobs = held_on(("backend-ci.yml", SHARD))
        node = self.solo(held=jobs)
        still = train.plan(data([pr_node(3)], [node]), REQUIRED, cfg(), {}, {81: jobs})
        self.assertEqual(
            still["rollup_actions"],
            [{"action": "hold", "rollup": 81, "jobs": jobs}],
            "no new write while it stays held",
        )
        self.assertTrue(still["busy"])
        fixed = train.plan(data([pr_node(3)], [node]), REQUIRED, cfg(), {}, {})
        self.assertEqual(fixed["rollup_actions"][0]["action"], "rebuild")
        self.assertEqual(fixed["batches"], [[3]], "never evicted on the old red")
        self.assertNotIn(3, fixed["excluded"])

    def test_base_red_still_closes_a_held_rollup(self):
        node = self.solo(held=held_on(("backend-ci.yml", SHARD)))
        out = train.plan(
            data([pr_node(3)], [node], base_red=[900]), REQUIRED, cfg(), {}, {}
        )
        self.assertEqual(out["rollup_actions"][0]["action"], "close-red")

    def test_execute_hold_writes_the_manifest_once_and_never_labels_red(self):
        jobs = held_on(("backend-ci.yml", SHARD))
        result = train.plan(
            data([pr_node(3)], [self.solo()]), REQUIRED, cfg(), {81: "red"}, {81: jobs}
        )
        app, actions = FakeGitHub({}), FakeGitHub({})
        train.execute(result, train.Live("o/r", app, actions), {})
        patch = next(c for c in app.calls if c[:2] == ("PATCH", "repos/o/r/pulls/81"))
        manifest = train.parse_manifest(patch[2]["body"])
        self.assertEqual(manifest["held"], jobs)
        self.assertEqual(manifest["removals_seen"], 0)
        self.assertFalse(any("/labels" in c[1] for c in app.calls + actions.calls))
        self.assertFalse(any("state" in (c[2] or {}) for c in app.calls), "stays open")
        comment = next(
            c[2]["body"] for c in app.calls if c[1].endswith("/issues/81/comments")
        )
        self.assertIn(SHARD, comment)
        again = train.plan(
            data([pr_node(3)], [self.solo(held=jobs)]), REQUIRED, cfg(), {}, {81: jobs}
        )
        app, actions = FakeGitHub({}), FakeGitHub({})
        train.execute(again, train.Live("o/r", app, actions), {})
        self.assertEqual(app.calls + actions.calls, [], "held: no write per tick")

    def tick(self, rollup, routes):
        reader = RedReader(data([pr_node(3)], [rollup]), routes)
        return train.tick(reader, REQUIRED, cfg(), None), reader

    def test_tick_holds_on_a_shared_job_and_reads_only_dev_while_held(self):
        # The rollup fails shards 3 and 4; dev's newest finished push run
        # fails shard 3. One shared job is enough to hold.
        out, _ = self.tick(self.solo(), PR_RUN_RED + dev_push("failure"))
        act = out["rollup_actions"][0]
        self.assertEqual(
            (act["action"], act["jobs"]),
            ("hold", held_on(("backend-ci.yml", SHARD))),
        )
        held = self.solo(held=act["jobs"])
        out, reader = self.tick(held, dev_push("failure"))
        self.assertEqual(out["rollup_actions"][0]["action"], "hold")
        self.assertFalse(
            any("event=pull_request" in p or "/logs" in p for p in reader.paths),
            "a held rollup's logs are not read again",
        )
        out, _ = self.tick(held, dev_push("success"))
        self.assertEqual(out["rollup_actions"][0]["action"], "rebuild")

    def test_tick_evicts_when_dev_has_no_red_run_on_that_job(self):
        no_run = [
            (
                "repos/o/r/actions/workflows/backend-ci.yml/runs?event=push",
                {"workflow_runs": []},
            )
        ]
        other_job = dev_push("failure")
        other_job[1] = (
            other_job[1][0],
            {"jobs": [failed_job(61, "pytest shard 9")]},
        )
        for name, dev in (
            ("dev green", dev_push("success")),
            ("dev red elsewhere", other_job),
            ("no finished dev run", no_run),
        ):
            with self.subTest(name):
                out, _ = self.tick(self.solo(), PR_RUN_RED + dev)
                self.assertEqual(out["rollup_actions"][0]["action"], "evict")

    def test_tick_maps_a_queue_red_to_devs_backend_run(self):
        queue_red = [
            (
                "repos/o/r/actions/runs?event=merge_group",
                {
                    "workflow_runs": [
                        {
                            "id": 700,
                            "conclusion": "failure",
                            "created_at": "2026-11-26T11:00:00Z",
                            "head_sha": sha(700),
                            "head_branch": f"gh-readonly-queue/dev/pr-81-{sha(9000)}",
                            "path": ".github/workflows/backend-queue-ci.yml",
                        }
                    ]
                },
            ),
            (
                "repos/o/r/actions/runs/700/jobs",
                {
                    "jobs": [
                        failed_job(7, f"backend / {SHARD}"),
                        failed_job(8, "backend / Backend Tests Pass"),
                    ]
                },
            ),
            ("repos/o/r/actions/jobs/", "FAILED tests/test_x.py::test_y\n"),
        ]
        node = rollup_node(81, [(3, sha(3))], removals=1)
        out, _ = self.tick(node, queue_red + dev_push("failure"))
        act = out["rollup_actions"][0]
        self.assertEqual(
            (act["action"], act["signal"], act["jobs"]),
            ("hold", "queue", held_on(("backend-queue-ci.yml", f"backend / {SHARD}"))),
        )


class WakeAfterBaseRed(unittest.TestCase):
    """No new trigger types: a paused live loop stays awake to see the
    base-red issue close, and the push that turns dev green starts a run."""

    def test_a_paused_loop_outlives_the_idle_window_and_batches_once_it_closes(self):
        from unittest import mock

        clock, ticks = [0.0], []

        class Reader(RedReader):
            def __init__(self, token, repo):
                super().__init__(data([pr_node(1)]), [])

            def file_text(self, path):
                return None

            def required_checks(self):
                return REQUIRED

            def base_red(self):
                # Open for 12 ticks of 90 s: 18 min, past the 10 min idle stop.
                return [100] if len(ticks) < 12 else []

        real_tick = train.tick

        def tick(*args):
            ticks.append(real_tick(*args))
            return ticks[-1]

        def sleep(seconds):
            clock[0] += seconds

        with (
            mock.patch.dict("os.environ", {"REPO": "o/r", "GH_TOKEN": "t"}, clear=True),
            mock.patch.object(train, "GitHub", Reader),
            mock.patch.object(train, "tick", tick),
            mock.patch.object(train.time, "monotonic", lambda: clock[0]),
            mock.patch.object(train.time, "sleep", sleep),
            mock.patch("sys.stdout"),
        ):
            rc = train.run_cmd(train.parse_args(["tick", "--loop-minutes", "30"]))
        self.assertEqual(rc, 0)
        self.assertGreater(len(ticks), 12, "the paused loop kept ticking")
        self.assertEqual(
            {(tuple(t["paused_by"]), str(t["batches"])) for t in ticks[:12]},
            {((100,), "[]")},
        )
        self.assertEqual(ticks[12]["batches"], [[1]], "batches on the first clear tick")

    def test_the_push_that_turns_dev_green_starts_a_train_run(self):
        text = (SCRIPTS.parent / "workflows" / "devflow-train.yml").read_text()
        triggers = text.split("\non:\n", 1)[1].split("\npermissions:\n", 1)[0]
        push = triggers.split("  push:\n", 1)[1].split("\n  pull_request:\n", 1)[0]
        self.assertEqual(push.strip(), "branches: [dev]", "no path filter on the push")
        self.assertNotIn("issues:", triggers, "an issues event runs main's copy")
        live = text.split("\n  live:\n", 1)[1]
        self.assertIn(
            "if: github.event_name == 'push' && github.ref == 'refs/heads/dev'", live
        )
        self.assertIn("cancel-in-progress: false", text, "a newer push waits its turn")


class ReviewFixes(unittest.TestCase):
    """Each test fails without its fix (review items 3, 4b, 5, 6, 8d)."""

    MEMBERS = [(1, sha(1)), (2, sha(2))]

    def plan(self, admitted, rollups=(), **kw):
        return train.plan(data(admitted, rollups), REQUIRED, kw.pop("config", cfg()), **kw)

    def action(self, rollup, admitted=None, **kw):
        admitted = admitted if admitted is not None else [pr_node(1), pr_node(2)]
        return self.plan(admitted, [rollup], **kw)["rollup_actions"][0]

    # 5: an unrun suite never counts as a pass
    def test_a_newer_deferred_never_hides_an_older_failed_full_run(self):
        runs = [
            run("Backend Tests Pass", "FAILURE", started="2026-11-26T10:00:00Z"),
            run("Backend Tests Deferred", "SUCCESS", started="2026-11-26T11:00:00Z"),
        ]
        self.assertEqual(train.check_states(runs, REQUIRED[:1], True), {"Backend Tests Pass": "failure"})
        runs[0] = run("Backend Tests Pass", "SUCCESS", started="2026-11-26T10:00:00Z")
        self.assertEqual(train.check_states(runs, REQUIRED[:1], True), {"Backend Tests Pass": "success"})
        self.assertEqual(train.check_states(runs[1:], REQUIRED[:1], True), {"Backend Tests Pass": "deferred"})

    # 6a: red non-required checks count
    def test_a_red_check_that_is_not_required_excludes_a_member(self):
        node = pr_node(1)
        node["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["state"] = "FAILURE"
        self.assertEqual(train.exclusion(view(node), cfg(), {}), "checks not green: all checks=FAILURE")
        node["commits"] = {"nodes": []}
        self.assertEqual(train.exclusion(view(node), cfg(), {}), "checks not green: all checks=none")

    def test_a_red_check_that_is_not_required_turns_a_rollup_red(self):
        rollup = rollup_node(70, self.MEMBERS)
        rollup["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["state"] = "FAILURE"
        act = self.action(rollup, failures={70: "red"})
        self.assertEqual((act["action"], act["signal"]), ("split", "checks"))

    # 6b: enqueue with expectedHeadOid, only once the rollup's own checks are green
    def test_enqueue_only_when_approved_and_every_check_is_green(self):
        pending = rollup_node(70, self.MEMBERS, checks=green()[:2] + [run("Python Lint Pass", status="IN_PROGRESS")])
        self.assertEqual(self.action(pending)["action"], "wait")
        other = rollup_node(70, self.MEMBERS)
        other["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["state"] = "PENDING"
        self.assertEqual(self.action(other)["action"], "wait")
        result = self.plan([pr_node(1), pr_node(2)], [rollup_node(70, self.MEMBERS)])
        self.assertEqual(result["rollup_actions"][0]["action"], "enqueue")
        app = FakeGitHub({})
        train.execute(result, train.Live("o/r", app, FakeGitHub({})), {})
        self.assertIn(("GRAPHQL", "enqueuePullRequest", {"id": "PR_70", "head": sha(70)}), app.calls)
        live = pathlib.Path(train.__file__).read_text()
        self.assertIn("expectedHeadOid: $head", live)

    # 6d: never plain auto-merge; no queue pauses the train
    def test_no_merge_queue_builds_and_enqueues_nothing(self):
        payload = data([pr_node(1), pr_node(2), pr_node(3)], [rollup_node(70, self.MEMBERS)])
        payload["repository"]["mergeQueue"] = None
        out = train.plan(payload, REQUIRED, cfg())
        self.assertEqual(out["batches"], [])
        self.assertEqual(out["rollup_actions"][0]["action"], "wait")
        self.assertTrue(out["no_queue"])
        self.assertNotIn("AutoMerge", pathlib.Path(train.__file__).read_text())

    # 6c: a member that turns red after batching is dropped
    def test_a_member_that_turns_red_is_dropped_and_a_running_one_waits(self):
        red = pr_node(1)
        red["_check_runs"] = green()[:2] + [run("Python Lint Pass", "FAILURE")]
        self.assertEqual(self.action(rollup_node(70, self.MEMBERS), [red, pr_node(2)]),
                         {"action": "rebuild", "drop": [1], "rollup": 70})
        verdict = pr_node(1)
        verdict["reviews"]["nodes"][-1] = verdict_review(sha(1), verdict="changes")
        self.assertEqual(self.action(rollup_node(70, self.MEMBERS), [verdict, pr_node(2)])["drop"], [1])
        running = pr_node(1)
        running["_check_runs"] = green()[:2] + [run("Python Lint Pass", status="IN_PROGRESS")]
        self.assertEqual(self.action(rollup_node(70, self.MEMBERS), [running, pr_node(2)])["action"], "enqueue")

    # 6e: members come from the merge commits, never the editable body
    def test_a_member_removed_from_the_body_is_still_monitored(self):
        rollup = rollup_node(70, [(2, sha(2))])
        rollup["_chain"] = list(self.MEMBERS)  # git still has #1
        moved = pr_node(1, head=sha(111))
        self.assertEqual(self.action(rollup, [moved, pr_node(2)]), {"action": "rebuild", "drop": [1], "rollup": 70})
        view_ = train.rollup_view(rollup, cfg(), REQUIRED)
        self.assertEqual([m["pr"] for m in view_["manifest"]["members"]], [1, 2])

    def test_the_chain_is_read_from_git_once_per_head(self):
        commits = {
            sha(70): {"parents": [{"sha": sha(60)}, {"sha": sha(2)}], "commit": {"message": "Merge pull request #2 from o/b"}},
            sha(60): {"parents": [{"sha": sha(9000)}, {"sha": sha(1)}], "commit": {"message": "Merge pull request #1 from o/a"}},
        }
        reads = []

        class Git(train.GitHub):
            def call(self, method, path, body=None, ok=(200, 201, 204)):
                reads.append(path)
                if "/compare/" in path:
                    return {"merge_base_commit": {"sha": sha(9000)}}
                return commits[path.rsplit("/", 1)[1]]

        gh, payload = Git("t", "o/r"), data([], [rollup_node(70, self.MEMBERS)])
        gh.attach_chains(payload)
        gh.attach_chains(payload)
        self.assertEqual(payload["repository"]["rollups"]["nodes"][0]["_chain"], [(1, sha(1)), (2, sha(2))])
        self.assertEqual(len(reads), 3, "a head's chain never changes")
        commits[sha(60)]["commit"]["message"] = "a pushed commit"
        gh.chains.clear()
        gh.attach_chains(payload)
        self.assertIsNone(payload["repository"]["rollups"]["nodes"][0]["_chain"])

    # 6f: a person's dequeue is respected
    def test_a_manual_dequeue_releases_with_train_hold_and_rebatches_nothing(self):
        for reason in ("manual", "something_new"):
            with self.subTest(reason=reason):
                rollup = rollup_node(70, self.MEMBERS, removals=1, reason=reason)
                out = self.plan([pr_node(1), pr_node(2)], [rollup])
                self.assertEqual(out["rollup_actions"][0]["action"], "release")
                self.assertEqual(out["batches"], [])
                app = FakeGitHub({})
                train.execute(out, train.Live("o/r", app, FakeGitHub({})), {})
                labelled = [c[1] for c in app.calls if c[2] == {"labels": [train.HOLD_LABEL]}]
                self.assertEqual(labelled, ["repos/o/r/issues/1/labels", "repos/o/r/issues/2/labels"])
        held = pr_node(1, labels=(train.ADMIT_LABEL, train.HOLD_LABEL))
        self.assertEqual(self.plan([held])["batches"], [])

    # 3: a person approves every member and every rollup; the train never approves
    def test_an_unapproved_or_bot_approved_rollup_waits(self):
        unapproved = rollup_node(70, self.MEMBERS)
        unapproved["reviews"]["nodes"] = []
        self.assertEqual(self.action(unapproved)["action"], "wait")
        self.assertEqual(self.action(bot_only(rollup_node(70, self.MEMBERS)))["action"], "wait")
        reader = rollup_node(70, self.MEMBERS)
        reader["_permissions"] = {"bob": "read"}
        self.assertEqual(self.action(reader)["action"], "wait")
        self.assertEqual(self.action(rollup_node(70, self.MEMBERS))["action"], "enqueue")

    def test_a_member_needs_a_person_before_the_promotion(self):
        self.assertTrue(train.Config().person_approval, "a code constant, on")
        reason = train.exclusion(view(bot_only(pr_node(1))), cfg(), {})
        self.assertEqual(reason, "waits for an approval on this head by a person with write access who is not the author")

    def test_the_train_never_approves_and_holds_no_pull_request_write(self):
        src = pathlib.Path(train.__file__).read_text()
        self.assertNotIn("/reviews", src)
        self.assertNotIn('"APPROVE"', src)
        wf = (SCRIPTS.parent / "workflows/devflow-train.yml").read_text()
        self.assertNotRegex(wf, r"pull-requests:\s*write")

    # MUST KEEP: a medium member waits for a non-author writer, also after the promotion
    def test_the_medium_hold_stands_without_the_person_rule(self):
        after = cfg(person_approval=False)
        low = bot_only(pr_node(1))
        self.assertIsNone(train.exclusion(view(low), after, {}), "low rides on the bot's approval")
        medium = bot_only(pr_node(2))
        medium["reviews"]["nodes"][-1] = verdict_review(sha(2), tier="medium")
        reason = train.exclusion(view(medium), after, {})
        self.assertIsNotNone(reason, "the medium hold is gone")
        self.assertTrue(reason.startswith("medium tier: waits for an approval on this head by a person"), reason)
        self.assertEqual(train.plan(data([low, medium]), REQUIRED, after)["batches"], [[1]])

    # 4b: dismissed or edited verdicts do not count
    def test_a_dismissed_or_edited_verdict_is_no_verdict(self):
        review = verdict_review(sha(1))
        self.assertIsNotNone(latest([review], "author-a"))
        self.assertIsNone(latest([{**review, "state": "DISMISSED"}], "author-a"))
        self.assertIsNone(latest([{**review, "editor": {"login": "bob"}}], "author-a"))
        self.assertIsNotNone(latest([{**review, "editor": {"login": "reviewer"}}], "author-a"))

    # queue builds = the live ruleset
    def test_queue_builds_default_matches_the_ruleset_file(self):
        ruleset = json.loads((SCRIPTS.parent / "merge-queue/dev.ruleset.json").read_text())
        params = ruleset["rules"][0]["parameters"]
        self.assertEqual(train.Config().queue_builds, params["max_entries_to_build"])
        self.assertEqual((params["grouping_strategy"], params["max_entries_to_build"], params["max_entries_to_merge"]),
                         ("HEADGREEN", 10, 5))

    # 8d
    def test_every_action_in_the_train_workflow_is_pinned_by_sha(self):
        wf = (SCRIPTS.parent / "workflows/devflow-train.yml").read_text()
        uses = re.findall(r"uses: (\S+)", wf)
        self.assertTrue(uses)
        for ref in uses:
            self.assertRegex(ref, r"@[0-9a-f]{40}$")


if __name__ == "__main__":
    unittest.main()
