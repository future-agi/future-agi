#!/usr/bin/env python3
"""Read-only preflight/readback for the two-build dev pilot. No settings writes."""

import argparse
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

NAME = "dev-merge-queue"
REVIEW_ID = 2289447
ISSUER = 15368
CONTEXTS = {"Backend Tests Pass", "E2E Tests Pass", "Python Lint Pass"}
RULE = {
    "type": "merge_queue",
    "parameters": {
        "merge_method": "MERGE",
        "max_entries_to_build": 2,
        "min_entries_to_merge": 1,
        "max_entries_to_merge": 1,
        "min_entries_to_merge_wait_minutes": 0,
        "grouping_strategy": "ALLGREEN",
        "check_response_timeout_minutes": 240,
    },
}
TEMPLATE = {
    "name": NAME,
    "target": "branch",
    "enforcement": "disabled",
    "bypass_actors": [],
    "conditions": {"ref_name": {"include": ["refs/heads/dev"], "exclude": []}},
    "rules": [RULE],
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def get(endpoint, *, pages=False):
    cmd = ["gh", "api", "--hostname", "github.com", "--method", "GET"]
    if pages:
        cmd += ["--paginate", "--slurp"]
    result = json.loads(subprocess.check_output([*cmd, endpoint], timeout=120))
    if pages:
        require(isinstance(result, list) and all(isinstance(p, list) for p in result),
                f"unexpected pagination format for {endpoint}")
        return [row for page in result for row in page]
    return result


def native_queue_id(repo):
    owner, name = repo.split("/")
    query = '''query($owner: String!, $name: String!) {
      repository(owner: $owner, name: $name) { nameWithOwner mergeQueue(branch: "dev") { id } }
    }'''
    response = json.loads(subprocess.check_output([
        "gh", "api", "--hostname", "github.com", "graphql", "-f", f"query={query}",
        "-f", f"owner={owner}", "-f", f"name={name}",
    ], timeout=120))
    require(not response.get("errors"), "native queue lookup returned errors")
    repository = response["data"]["repository"]
    require(repository["nameWithOwner"].lower() == repo.lower(), "native queue repository mismatch")
    queue = repository["mergeQueue"]
    if queue is None:
        return None
    require(isinstance(queue["id"], str) and queue["id"], "missing native queue ID")
    return queue["id"]


def snapshot(repo):
    require(re.fullmatch(r"[\w.-]+/[\w.-]+", repo, flags=re.ASCII) and
            not {".", ".."}.intersection(repo.split("/")), "expected OWNER/REPO")
    root = f"repos/{repo}"
    meta = get(root)
    listed = get(f"{root}/rulesets?includes_parents=true&per_page=100", pages=True)
    matches = [r for r in listed if r["name"] == NAME]
    require(len(matches) <= 1, f"ambiguous rulesets named {NAME}")
    queue = get(f"{root}/rulesets/{int(matches[0]['id'])}") if matches else None
    rules = get(f"{root}/rules/branches/dev?per_page=100", pages=True)
    return {
        "repo": meta["full_name"],
        "allow_merge_commit": meta["allow_merge_commit"],
        "allow_squash_merge": meta["allow_squash_merge"],
        "allow_rebase_merge": meta["allow_rebase_merge"],
        "allow_auto_merge": meta["allow_auto_merge"],
        "native_queue_id": native_queue_id(meta["full_name"]),
        "classic": get(f"{root}/branches/dev/protection"),
        "non_queue_rules": sorted((r for r in rules if r["type"] != "merge_queue"), key=canonical),
        "effective_queue": [r for r in rules if r["type"] == "merge_queue"],
        "queue_ruleset": queue,
    }


def verify(s, expect):
    require(s["allow_merge_commit"] is True, "repository must allow merge commits")
    classic = s["classic"]
    status = classic["required_status_checks"]
    require(status["strict"] is False, "classic strict setting changed")
    require(classic["required_linear_history"]["enabled"] is False,
            "classic linear history conflicts with MERGE")
    expected = sorted((name, ISSUER) for name in CONTEXTS)
    require(sorted(status["contexts"]) == sorted(CONTEXTS) and
            sorted((c["context"], c["app_id"]) for c in status["checks"]) == expected,
            "expected exactly the three classic contexts bound to GitHub Actions")

    review = False
    for rule in s["non_queue_rules"]:
        kind = rule["type"]
        require(kind != "required_linear_history", "effective linear history conflicts with MERGE")
        if kind == "pull_request":
            p = rule["parameters"]
            require("merge" in p["allowed_merge_methods"], "effective review rule excludes MERGE")
            if rule["ruleset_id"] == REVIEW_ID and rule["ruleset_source_type"] == "Organization":
                review = p["required_approving_review_count"] == 1 and p["dismiss_stale_reviews_on_push"] is True
        elif kind == "required_status_checks":
            p = rule["parameters"]
            require(p["strict_required_status_checks_policy"] is False and
                    all(c["context"] in CONTEXTS and c.get("integration_id") == ISSUER
                        for c in p["required_status_checks"]),
                    "unexpected effective status check or strictness")
    require(review, "missing inherited one-approval rule with stale-review dismissal")

    q = s["queue_ruleset"]
    if q is not None:
        require(q["source_type"] == "Repository" and q["source"].lower() == s["repo"].lower(),
                "named queue must belong to this repository")
        desired = {**TEMPLATE, "enforcement": "active" if expect == "active" else "disabled"}
        for key, value in desired.items():
            require(canonical(q[key]) == canonical(value), f"queue {key} does not match pilot")
    effective = s["effective_queue"]
    if expect == "active":
        require(bool(s["native_queue_id"]), "native dev queue is absent")
        require(q is not None and len(effective) == 1 and effective[0]["ruleset_id"] == q["id"]
                and canonical(effective[0]["parameters"]) == canonical(RULE["parameters"]),
                "expected exactly the named pilot queue to apply to dev")
    else:
        require(effective == [], "a queue already applies to dev")
        require(s["native_queue_id"] is None, "native dev queue still exists")


def unchanged(before, after):
    verify(before, "inactive")
    for key in ("repo", "allow_merge_commit", "allow_squash_merge", "allow_rebase_merge",
                "allow_auto_merge", "classic", "non_queue_rules"):
        require(canonical(before[key]) == canonical(after[key]), f"baseline drift: {key}")
    if before["queue_ruleset"] is not None:
        same_ruleset(before, after)


def same_ruleset(previous, current):
    require(current["queue_ruleset"] is not None and
            previous["queue_ruleset"]["id"] == current["queue_ruleset"]["id"],
            "queue ruleset missing, renamed or replaced")


def verify_rollback(before, active, after):
    verify(active, "active")
    unchanged(before, active)
    verify(after, "inactive")
    unchanged(before, after)
    same_ruleset(active, after)


class ReadbackTests(unittest.TestCase):
    def setUp(self):
        self.s = {
            "repo": "example/project", "allow_merge_commit": True, "allow_auto_merge": False,
            "classic": {"required_status_checks": {"strict": False, "contexts": sorted(CONTEXTS),
                        "checks": [{"context": c, "app_id": ISSUER} for c in sorted(CONTEXTS)]},
                        "required_linear_history": {"enabled": False}},
            "non_queue_rules": [{"type": "pull_request", "ruleset_id": REVIEW_ID,
                                 "ruleset_source_type": "Organization", "parameters": {
                                     "required_approving_review_count": 1, "allowed_merge_methods": ["merge"],
                                     "dismiss_stale_reviews_on_push": True}}],
            "queue_ruleset": None, "effective_queue": [], "native_queue_id": None,
            "allow_squash_merge": True, "allow_rebase_merge": False,
        }

    def activate(self):
        self.s["queue_ruleset"] = {**TEMPLATE, "enforcement": "active", "source": self.s["repo"],
                                   "source_type": "Repository", "id": 42}
        self.s["effective_queue"] = [{**RULE, "ruleset_id": 42}]
        self.s["native_queue_id"] = "queue-node"

    def test_round_trip(self):
        verify(self.s, "inactive")
        before = json.loads(json.dumps(self.s))
        self.activate()
        verify(self.s, "active")
        unchanged(before, self.s)
        active = json.loads(json.dumps(self.s))
        self.s["queue_ruleset"]["enforcement"] = "disabled"
        self.s["effective_queue"] = []
        self.s["native_queue_id"] = None
        verify_rollback(before, active, self.s)
        self.s["queue_ruleset"]["id"] = 43
        with self.assertRaisesRegex(ValueError, "replaced"):
            verify_rollback(before, active, self.s)
        self.s["queue_ruleset"] = None
        with self.assertRaisesRegex(ValueError, "missing"):
            verify_rollback(before, active, self.s)

    def test_missing_or_renamed_baseline_ruleset(self):
        before = {**self.s, "queue_ruleset": {**TEMPLATE, "source": self.s["repo"],
                  "source_type": "Repository", "id": 42}}
        with self.assertRaisesRegex(ValueError, "missing"):
            unchanged(before, self.s)

    @patch("subprocess.check_output")
    def test_native_queue_lookup(self, call):
        repository = {"nameWithOwner": self.s["repo"], "mergeQueue": None}
        response = {"data": {"repository": repository}}
        call.return_value = json.dumps(response)
        self.assertIsNone(native_queue_id(self.s["repo"]))
        repository["mergeQueue"] = {"id": "queue-node"}
        call.return_value = json.dumps(response)
        self.assertEqual(native_queue_id(self.s["repo"]), "queue-node")
        del repository["mergeQueue"]
        call.return_value = json.dumps(response)
        with self.assertRaises(KeyError):
            native_queue_id(self.s["repo"])
        repository["mergeQueue"] = None
        call.return_value = json.dumps({**response, "errors": [{"message": "unavailable"}]})
        with self.assertRaisesRegex(ValueError, "lookup returned errors"):
            native_queue_id(self.s["repo"])

    def test_classic_only_queue_even_if_empty(self):
        self.s["native_queue_id"] = "queue-node"
        with self.assertRaisesRegex(ValueError, "native dev queue still exists"):
            verify(self.s, "inactive")

    def test_missing_native_queue_while_active(self):
        self.activate()
        self.s["native_queue_id"] = None
        with self.assertRaisesRegex(ValueError, "native dev queue is absent"):
            verify(self.s, "active")

    def test_other_merge_methods_drift(self):
        for key in ("allow_squash_merge", "allow_rebase_merge"):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, f"drift: {key}"):
                unchanged(self.s, {**self.s, key: not self.s[key]})

    def test_review_and_method(self):
        params = self.s["non_queue_rules"][0]["parameters"]
        for key, bad in [("allowed_merge_methods", ["squash"]), ("required_approving_review_count", 0),
                         ("dismiss_stale_reviews_on_push", False)]:
            with self.subTest(key=key):
                old, params[key] = params[key], bad
                with self.assertRaises(ValueError):
                    verify(self.s, "inactive")
                params[key] = old

    def test_issuer(self):
        self.s["classic"]["required_status_checks"]["checks"][0]["app_id"] = -1
        with self.assertRaises(ValueError):
            verify(self.s, "inactive")

    def test_candidate_rule_must_be_exact(self):
        self.activate()
        self.s["queue_ruleset"]["conditions"] = {"ref_name": {"include": ["~ALL"], "exclude": []}}
        with self.assertRaises(ValueError):
            verify(self.s, "active")

    def test_hidden_bypass_list_is_not_empty(self):
        self.activate()
        del self.s["queue_ruleset"]["bypass_actors"]
        with self.assertRaises(KeyError):
            verify(self.s, "active")

    def test_effective_rule_not_just_template(self):
        self.activate()
        self.s["effective_queue"][0]["ruleset_id"] = 43
        with self.assertRaises(ValueError):
            verify(self.s, "active")
        with self.assertRaises(ValueError):
            verify(self.s, "inactive")

    def test_unrelated_drift(self):
        before = json.loads(json.dumps(self.s))
        self.s["classic"]["enforce_admins"] = {"enabled": False}
        with self.assertRaisesRegex(ValueError, "drift: classic"):
            unchanged(before, self.s)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--self-test", action="store_true", help="offline unit tests, no gh calls")
    mode.add_argument("--expect", choices=["inactive", "active"], help="live dev state")
    parser.add_argument("--repo", default="future-agi/future-agi", help="github.com OWNER/REPO")
    parser.add_argument("--baseline", type=Path, help="successful pre-pilot inactive JSON snapshot")
    parser.add_argument("--active-snapshot", type=Path, help="successful active JSON snapshot for rollback")
    args = parser.parse_args()
    require(canonical(json.loads(Path(__file__).with_name("dev.ruleset.json").read_text())) == canonical(TEMPLATE),
            "checked-in template differs from the disabled pilot contract")
    if args.self_test:
        tests = unittest.defaultTestLoader.loadTestsFromTestCase(ReadbackTests)
        return not unittest.TextTestRunner().run(tests).wasSuccessful()
    if args.expect == "active" and not args.baseline:
        parser.error("active readback requires --baseline")
    rollback = args.expect == "inactive" and args.baseline is not None
    if rollback != (args.active_snapshot is not None):
        parser.error("rollback requires --expect inactive, --baseline and --active-snapshot together")
    s = snapshot(args.repo)
    if rollback:
        verify_rollback(json.loads(args.baseline.read_text()), json.loads(args.active_snapshot.read_text()), s)
    else:
        verify(s, args.expect)
        if args.baseline:
            unchanged(json.loads(args.baseline.read_text()), s)
    print(json.dumps(s, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyError, AttributeError, OSError, TypeError, ValueError, subprocess.SubprocessError) as exc:
        sys.exit(f"Cannot verify dev queue: {exc}")
