"""Tests for devflow_train.py.

Every GitHub payload here is CONSTRUCTED in the shape of the GraphQL query in
devflow_train.build_query(), the check-runs REST endpoint and the REST responses the train reads; no train has run
yet, so there is nothing to record. The live-write tests use a fake client
that records each call.

    python3 -I .github/scripts/test_devflow_train.py
"""

import json
import pathlib
import re
import sys
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
                }
            ]
        },
        "comments": {"nodes": []},
        "_check_runs": green(),
        "timelineItems": {"nodes": []},
    }
    node.update(over)
    return node


def view(node):
    return train.member_view(node, REQUIRED)


def verdict_review(
    head,
    tier="low",
    verdict="pass",
    assoc="MEMBER",
    when="2026-11-26T11:00:00Z",
    needs_person=False,
    login="reviewer",
):
    body = {
        "schema": "devflow-review/v1",
        "head": head,
        "tier": tier,
        "verdict": verdict,
        "needs_person": needs_person,
        "findings": [],
    }
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
    reason="CI failed",
    checks=None,
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
    return node


def data(admitted=(), rollups=(), queue=()):
    return {
        "repository": {
            "mergeQueue": {
                "entries": {"nodes": [{"pullRequest": {"number": n}} for n in queue]}
            },
            "admitted": {"nodes": list(admitted)},
            "rollups": {"nodes": list(rollups)},
        }
    }


def cfg(**over):
    return train.Config(
        **{
            "train_login": APP,
            "max_batch": 6,
            "max_rollups": 4,
            "queue_builds": 6,
            "require_verdict": False,
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

    def test_floor_always_applies(self):
        self.assertEqual(cfg().high_globs, (".github/**",))
        self.assertEqual(
            train.Config(high_globs=["ee/**"]).high_globs, (".github/**", "ee/**")
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
    def test_query_reads_no_check_runs(self):
        query = train.build_query()
        self.assertNotIn("checkRuns", query)
        self.assertNotIn("statusCheckRollup", query)

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


class Verdicts(unittest.TestCase):
    def test_untrusted_and_malformed_are_ignored(self):
        bad = verdict_review(
            sha(1), tier="low", assoc="NONE", when="2026-11-26T13:00:00Z"
        )
        broken = {
            **verdict_review(sha(1)),
            "body": train.VERDICT_MARKER + "\n```json\n{nope\n```",
            "submittedAt": "2026-11-26T12:00:00Z",
        }
        good = verdict_review(sha(1), tier="high")
        self.assertEqual(train.latest_verdict([good, broken, bad], "author-a")["tier"], "high")

    def test_newest_trusted_wins(self):
        old = verdict_review(sha(1), tier="high", when="2026-11-26T10:00:00Z")
        new = verdict_review(sha(1), tier="low", when="2026-11-26T11:00:00Z")
        self.assertEqual(train.latest_verdict([new, old], "author-a")["tier"], "low")


    def test_the_authors_own_verdict_never_counts(self):
        own = verdict_review(sha(1), login="author-a", when="2026-11-26T12:00:00Z")
        self.assertIsNone(train.latest_verdict([own], "author-a"))
        self.assertIsNone(
            train.latest_verdict([own], "Author-A"), "logins compare without case"
        )
        other = verdict_review(sha(1), tier="high", when="2026-11-26T10:00:00Z")
        self.assertEqual(train.latest_verdict([own, other], "author-a")["tier"], "high")

    def test_the_assigned_reviewer_wins_over_a_newer_verdict(self):
        assigned = verdict_review(sha(1), tier="high", login="bob", when="2026-11-26T10:00:00Z")
        newer = verdict_review(sha(1), tier="low", login="carol", when="2026-11-26T11:00:00Z")
        self.assertEqual(train.latest_verdict([assigned, newer], "a", "bob")["tier"], "high")
        self.assertEqual(train.latest_verdict([assigned, newer], "a", "dave")["tier"], "low")

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
            self.reason(node), "required checks not green: Python Lint Pass=missing"
        )

    def test_approval_must_be_on_this_head(self):
        node = pr_node(1)
        node["reviews"]["nodes"][0]["commit"]["oid"] = sha(99)
        self.assertEqual(self.reason(node), "not approved on this head")

    def test_high_globs_from_policy(self):
        node = pr_node(1, files=("ee/billing.py",))
        self.assertIsNone(self.reason(node))
        self.assertIn(
            "matches ee/**", self.reason(node, cfg(high_globs=(".github/**", "ee/**")))
        )

    def test_verdict_tier(self):
        node = pr_node(1)
        node["reviews"]["nodes"].append(verdict_review(sha(1), tier="high"))
        self.assertEqual(self.reason(node), "review tier high: goes alone")
        node["reviews"]["nodes"][-1] = verdict_review(sha(1), needs_person=True)
        self.assertEqual(self.reason(node), "review tier low: goes alone")
        node["reviews"]["nodes"][-1] = verdict_review(sha(1), verdict="changes")
        self.assertEqual(self.reason(node), "review verdict changes")

    def test_missing_or_stale_verdict(self):
        stale = pr_node(1)
        stale["reviews"]["nodes"].append(verdict_review(sha(42), tier="high"))
        self.assertIsNone(
            self.reason(stale), "a stale verdict counts as missing; lenient by default"
        )
        self.assertEqual(
            self.reason(stale, cfg(require_verdict=True)),
            "no review verdict on this head",
        )
        self.assertEqual(
            self.reason(pr_node(1), cfg(require_verdict=True)),
            "no review verdict on this head",
        )

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
        self.assertEqual(train.removal_kind("MERGE_CONFLICT"), "conflict")
        self.assertEqual(train.removal_kind("Manually dequeued"), "manual")
        self.assertEqual(train.removal_kind("CI_FAILURE"), "red")
        self.assertEqual(train.removal_kind(None), "red")


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

    def test_idle_is_not_busy(self):
        self.assertFalse(train.plan(data(), REQUIRED, cfg())["busy"])

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
        moved = pr_node(1, head=sha(111))
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

    def test_pushed_rollup_is_never_reapproved(self):
        moved = rollup_node(70, self.MEMBERS)
        moved["headRefOid"] = sha(7777)
        moved["reviews"]["nodes"][0]["commit"]["oid"] = sha(7777)
        out = train.plan(data([pr_node(1), pr_node(2)], [moved]), REQUIRED, cfg())
        self.assertEqual(out["rollup_actions"][0]["action"], "rebuild")
        unapproved = rollup_node(70, self.MEMBERS)
        unapproved["reviews"]["nodes"] = []
        out = train.plan(data([pr_node(1), pr_node(2)], [unapproved]), REQUIRED, cfg())
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
            "ensure",
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


class LiveWrites(unittest.TestCase):
    REPO = "future-agi/canary"

    def members(self, *numbers):
        return [view(pr_node(n)) for n in numbers]

    def test_open_rollup_merges_in_order_skips_conflicts_and_approves_the_built_head(
        self,
    ):
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
        review = next(c[2] for c in actions.calls if c[1].endswith("/reviews"))
        self.assertEqual(
            (review["commit_id"], review["event"]),
            ("c3", "APPROVE"),
            "approved as github-actions, on the built head",
        )
        self.assertIn(
            (
                "POST",
                f"repos/{self.REPO}/issues/2/labels",
                {"labels": [train.CONFLICT_LABEL]},
            ),
            actions.calls,
        )
        self.assertIn(
            ("GRAPHQL", "enablePullRequestAutoMerge", {"id": "PR_80"}), app.calls
        )
        self.assertFalse(
            [c for c in app.calls if c[1].endswith("/reviews")],
            "the App never approves",
        )

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
            actions.calls,
        )
        comment = next(
            c[2]["body"] for c in actions.calls if c[1].endswith("/issues/1/comments")
        )
        self.assertIn(url, comment)
        self.assertIn(f"<!-- devflow-train:red pr=1 sha={sha(1)} -->", comment)


if __name__ == "__main__":
    unittest.main()
