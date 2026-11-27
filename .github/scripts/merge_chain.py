"""The pull requests in a chain of merge-queue merge commits, read from git.

A merge group, and a batch train rollup, is a chain of merge commits on
first parents, each "Merge pull request #N from owner/branch" with the
member's head as its second parent. The members come from these commits,
never from an editable pull request body. The merge-queue gate and the batch
train import this; the train writes the same subject.

`get(path)` returns the parsed JSON of a GitHub REST path.
"""

from __future__ import annotations

import re
from typing import Callable

MERGE_SUBJECT = re.compile(r"^Merge pull request #(\d+) ")
# max_entries_to_merge on the dev queue ruleset is 5; leave room, fail past it.
MAX_MEMBERS = 20


class NotAChain(ValueError):
    """A commit between base and head is not a member merge commit."""


def merge_subject(number: int, owner: str, branch: str) -> str:
    return f"Merge pull request #{number} from {owner}/{branch}"


def landed_base(get: Callable[[str], dict], repo: str, branch: str, head: str) -> str:
    """Where the chain leaves the target branch: the merge base of the branch
    and head. A merge_group event's base_sha is the group's parent, which
    under HEADGREEN can be the entry ahead (also unlanded)."""
    return get(f"/repos/{repo}/compare/{branch}...{head}")["merge_base_commit"]["sha"]


def members(
    get: Callable[[str], dict], repo: str, base: str, head: str, limit: int = MAX_MEMBERS
) -> list[tuple[int, str]]:
    """[(pull request, member head)] from the merge commits base..head on
    first parents, newest first. Raise NotAChain on any other commit."""
    out, sha = [], head
    while sha != base:
        if len(out) >= limit:
            raise NotAChain(f"more than {limit} commits between base and head")
        commit = get(f"/repos/{repo}/commits/{sha}")
        parents = [p["sha"] for p in commit.get("parents") or []]
        match = MERGE_SUBJECT.match(commit["commit"]["message"])
        if len(parents) != 2 or not match:
            raise NotAChain(f"{sha[:12]} is not a merge-queue merge commit")
        out.append((int(match[1]), parents[1]))
        sha = parents[0]
    return out
