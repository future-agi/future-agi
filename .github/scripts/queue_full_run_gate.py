"""Merge-queue gate under CI_SCOPE_MODE=light: one full internal run per member.

A merge group runs Backend CI without the private EE package. Under the
light plan a pull request run holds the backend suites, so without this gate
a pull request that goes to the queue alone (high tier, code-owned paths, a
revert, too many files, auto-merge, a person's own enqueue) would land with
no internal run at all. The batch train's rollup is fine: its own pull
request run is always full.

For every member of the group, read from the group's merge commits (base_sha
to head_sha, first parents, each "Merge pull request #N" with the member's
head as second parent), this passes when:

- the member is a fork: exempt, as today. A fork never gets the light plan
  and never has an internal run; the dev push run (internal) catches EE
  breakage after merge; or
- a Backend CI pull_request run on that member head passed and recorded a
  merge-candidate receipt with scope "internal" for that pull request and
  head (ci_base_gate.py reads and checks receipts the same way). A held
  (light or draft) run records no receipt.

Any member without one fails the group, with the fix: add the ci:full label
and re-run Backend CI on the pull request (or push). It only reads.

    GH_TOKEN=... REPO=owner/name BASE_SHA=... HEAD_SHA=... python3 -I queue_full_run_gate.py
"""

from __future__ import annotations

import os
import pathlib
import re
import sys
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from ci_base_gate import (  # noqa: E402
    AGGREGATE,
    READ_ERRORS,
    RECEIPT_JOB,
    GitHub,
    Unknown,
    pages,
    producing_attempt,
    read_receipt,
)

PRODUCER = ".github/workflows/backend-ci.yml"
MERGE_SUBJECT = re.compile(r"^Merge pull request #(\d+) ")
# max_entries_to_merge on the dev queue ruleset is 5; leave room, fail past it.
MAX_MEMBERS = 20
FIX = (
    "Add the ci:full label, then re-run Backend CI on the pull request (or push)."
    " Under CI_SCOPE_MODE=light only the full plan runs the internal suite."
)


def members(gh, repo: str, base: str, head: str) -> list[tuple[int, str]]:
    """[(pull request, member head)] from the group's merge commits, newest first."""
    out, sha = [], head
    while sha != base:
        if len(out) >= MAX_MEMBERS:
            raise Unknown(f"more than {MAX_MEMBERS} commits between base and head")
        commit, _ = gh.json(f"/repos/{repo}/commits/{sha}")
        parents = [p["sha"] for p in commit.get("parents") or []]
        match = MERGE_SUBJECT.match(commit["commit"]["message"])
        if len(parents) != 2 or not match:
            raise Unknown(f"{sha[:12]} is not a merge-queue merge commit")
        out.append((int(match[1]), parents[1]))
        sha = parents[0]
    return out


def internal_proof(gh, repo: str, pr: int, head: str) -> tuple[bool, str]:
    """(True, run id) for a passing full internal Backend CI run on head, else (False, why)."""
    runs = [
        run
        for run in pages(
            gh.json,
            f"/repos/{repo}/actions/runs?head_sha={head}&event=pull_request&per_page=100",
            "workflow_runs",
        )
        if run.get("head_sha") == head
        and run.get("event") == "pull_request"
        and run.get("path") == PRODUCER
    ]
    why = []
    for run in sorted(runs, key=lambda run: run["id"], reverse=True):
        jobs = pages(
            gh.json,
            f"/repos/{repo}/actions/runs/{run['id']}/jobs?filter=all&per_page=100",
            "jobs",
        )
        try:
            aggregate, _ = producing_attempt(jobs, AGGREGATE["backend"])
            recorded, attempt = producing_attempt(jobs, RECEIPT_JOB)
        except ValueError as exc:
            why.append(f"run {run['id']}: {exc}")
            continue
        if aggregate is None or aggregate.get("conclusion") != "success":
            why.append(f"run {run['id']}: {AGGREGATE['backend']} did not pass")
            continue
        if recorded is None or recorded.get("conclusion") != "success":
            why.append(f"run {run['id']}: no receipt (suites held or not selected)")
            continue
        try:
            receipt = read_receipt(
                gh, repo, run["id"], f"ci-candidate-backend-attempt-{attempt}"
            )
        except (ValueError, zipfile.BadZipFile) as exc:
            why.append(f"run {run['id']}: {exc}")
            continue
        expected = {"kind": "backend", "pr": pr, "head_sha": head, "run_id": run["id"]}
        bad = [k for k, v in expected.items() if receipt.get(k) != v]
        if bad:
            why.append(f"run {run['id']}: receipt names another {', '.join(bad)}")
        elif receipt.get("scope") != "internal":
            why.append(f"run {run['id']}: scope {receipt.get('scope')!r}, not internal")
        else:
            return True, str(run["id"])
    return False, "; ".join(why[:4]) or "no Backend CI pull_request run on this head"


def decide(gh, repo: str, base: str, head: str) -> tuple[int, list[str]]:
    title = "::{} title=Full internal run::"
    try:
        group = members(gh, repo, base, head)
        lines, failed = [], False
        for pr, member_head in group:
            pull, _ = gh.json(f"/repos/{repo}/pulls/{pr}")
            head_repo = ((pull.get("head") or {}).get("repo") or {}).get("full_name")
            if head_repo != repo:
                lines.append(
                    title.format("notice")
                    + f"#{pr} is from a fork: exempt (no internal run, as before light mode)."
                )
                continue
            ok, detail = internal_proof(gh, repo, pr, member_head)
            if ok:
                lines.append(
                    title.format("notice")
                    + f"#{pr} at {member_head[:12]}: Backend CI run {detail} ran the internal suite."
                )
            else:
                failed = True
                lines.append(
                    title.format("error")
                    + f"#{pr} at {member_head[:12]} has no full internal Backend CI run ({detail}). {FIX}"
                )
    except (Unknown, KeyError, TypeError, AttributeError, *READ_ERRORS) as exc:
        return 1, [title.format("error") + f"Could not prove the group's members: {exc}."]
    return (1 if failed else 0), lines


def main() -> int:
    code, lines = decide(
        GitHub(os.environ["GH_TOKEN"]),
        os.environ["REPO"],
        os.environ["BASE_SHA"],
        os.environ["HEAD_SHA"],
    )
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
