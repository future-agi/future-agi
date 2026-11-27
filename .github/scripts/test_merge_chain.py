#!/usr/bin/env python3
"""Stdlib-only tests for merge_chain."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import merge_chain as mc  # noqa: E402

REPO = "org/repo"


def commit(message, *parents):
    return {"commit": {"message": message}, "parents": [{"sha": p} for p in parents]}


def fake(commits, merge_base="base"):
    def get(path):
        if "/compare/" in path:
            return {"merge_base_commit": {"sha": merge_base}}
        return commits[path.rsplit("/", 1)[1]]

    return get


class MembersTests(unittest.TestCase):
    def test_members_come_from_merge_commits_newest_first(self):
        get = fake({
            "m2": commit(mc.merge_subject(12, "org", "b") + "\n\ntitle", "m1", "h12"),
            "m1": commit(mc.merge_subject(11, "org", "a"), "base", "h11"),
        })
        self.assertEqual(mc.members(get, REPO, "base", "m2"), [(12, "h12"), (11, "h11")])
        self.assertEqual(mc.landed_base(get, REPO, "dev", "m2"), "base")

    def test_a_non_merge_commit_breaks_the_chain(self):
        for bad in (commit("fix: x", "base"), commit("Squash #12", "base", "h"), commit("Merge pull request #12 x", "base")):
            with self.subTest(bad=bad["commit"]["message"]):
                with self.assertRaises(mc.NotAChain):
                    mc.members(fake({"m": bad}), REPO, "base", "m")

    def test_a_chain_longer_than_the_limit_fails(self):
        commits = {f"m{i}": commit(mc.merge_subject(i, "o", "b"), f"m{i - 1}" if i else "base", f"h{i}") for i in range(4)}
        with self.assertRaises(mc.NotAChain):
            mc.members(fake(commits), REPO, "base", "m3", limit=3)
        self.assertEqual(len(mc.members(fake(commits), REPO, "base", "m3", limit=4)), 4)

    def test_the_subject_the_train_writes_matches(self):
        self.assertEqual(mc.MERGE_SUBJECT.match(mc.merge_subject(4493, "org", "chore/x"))[1], "4493")


if __name__ == "__main__":
    unittest.main()
