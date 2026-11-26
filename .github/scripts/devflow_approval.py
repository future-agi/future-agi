#!/usr/bin/env python3
"""Approve a low-tier pull request on a passing devflow agent review.

Runs from .github/workflows/devflow-approval.yml when APPROVAL_MODE is
`devflow` or `observe`. It reads the pull request live by number, decides,
and then does one of three things:

- approve: the bot approves the head commit and adds devflow:train, so the
  batch train may take it.
- needs-human: the bot adds the needs-human label and does not approve.
- wait: nothing yet (no trusted review on this head, or the verdict is not pass).

In `observe` mode it only writes the decision to the job summary: no
approval, no label. Paths that need a person are not decided here: the
floor block in .github/CODEOWNERS is the one source, and GitHub's code owner
review enforces it, whatever the bot approves.

    python3 -I devflow_approval.py --repo owner/name --pr 1234 [--mode devflow|observe]

The rules are in `decide()`, which is pure so test_devflow_approval.py feeds
it fixtures. The review format is the devflow-review/v1 contract that /land
and review-inbox use.
"""

import argparse
import json
import os
import re
import subprocess
import sys

REVIEW_MARKER = "<!-- devflow-review:v1 -->"
ASSIGN_MARKER = re.compile(r"<!--\s*devflow-assigned:v1\b([^>]*)-->")
JSON_BLOCK = re.compile(r"```json\s*\n(.*?)\n\s*```", re.S)
# reviewer-assigner writes the assignment marker with GITHUB_TOKEN. Anyone can
# post the marker text on a public repo, so only the bot's comment counts.
BOT = re.compile(r"^github-actions\[bot\]$")  # the REST login of GITHUB_TOKEN writes
WRITE = {"admin", "write"}
STOP_LABEL = "needs-human"
TRAIN_LABEL = "devflow:train"
BASE = "dev"
MODES = ("devflow", "observe")


def lower(value) -> str:
    return str(value or "").lower()


def parse_review(body: str) -> dict:
    """Return the verdict JSON of one review body. Raise ValueError if it is malformed."""
    match = JSON_BLOCK.search(body)
    if not match:
        raise ValueError("no ```json block after the marker")
    verdict = json.loads(match.group(1))
    if not isinstance(verdict, dict) or verdict.get("schema") != "devflow-review/v1":
        raise ValueError("schema is not devflow-review/v1")
    if not isinstance(verdict.get("findings"), list):
        raise ValueError("findings is not a list")
    return verdict


def assigned_login(comments: list):
    """The login in the newest bot comment that carries the assignment marker."""
    for comment in reversed(comments):
        if not BOT.match((comment.get("user") or {}).get("login") or ""):
            continue
        match = ASSIGN_MARKER.search(comment.get("body") or "")
        if not match:
            continue
        pairs = dict(p.split("=", 1) for p in match.group(1).split() if "=" in p)
        if pairs.get("login"):
            return pairs["login"]
    return None


def bot_approved(reviews: list, head: str) -> bool:
    return any(
        BOT.match((r.get("user") or {}).get("login") or "")
        and r.get("state") == "APPROVED"
        and r.get("commit_id") == head
        for r in reviews
    )


def decide(
    pr: dict,
    reviews: list,
    comments: list,
    permissions: dict,
    tight: bool = False,
    observe: bool = False,
) -> dict:
    """Return {"action": "skip"|"wait"|"needs-human"|"approve", "reasons": [...], "head": sha}.

    `permissions` maps a lower-case login to its repository permission
    (admin, write, read or none), from collaborators/{login}/permission.
    `tight` (APPROVAL_MODE_TIGHT is "1") sends every passing review to a person.
    `observe` decides on every head, even one the bot already approved (the
    admin rule still approves in that mode), so the decisions can be compared.
    """
    head = (pr.get("head") or {}).get("sha") or ""
    author = (pr.get("user") or {}).get("login") or ""

    def out(action, *reasons):
        return {"action": action, "reasons": list(reasons), "head": head}

    if pr.get("state") != "open":
        return out("skip", "not open")
    if pr.get("draft"):
        return out("skip", "draft")
    if (pr.get("base") or {}).get("ref") != BASE:
        return out("skip", f"base is not {BASE}")
    head_repo = ((pr.get("head") or {}).get("repo") or {}).get("full_name")
    if head_repo != ((pr.get("base") or {}).get("repo") or {}).get("full_name"):
        return out("skip", "from a fork")
    if STOP_LABEL in [label.get("name") for label in pr.get("labels") or []]:
        return out("skip", f"already has {STOP_LABEL}")
    if not observe and bot_approved(reviews, head):
        return out("skip", "the bot already approved this head")

    reviewer = assigned_login(comments)
    if not reviewer:
        return out("wait", "no devflow-assigned marker from the bot")
    if lower(reviewer) == lower(author):
        return out("wait", f"the assigned reviewer {reviewer} is the author")
    if permissions.get(lower(reviewer)) not in WRITE:
        return out("wait", f"the assigned reviewer {reviewer} has no write access")
    own = [
        r
        for r in reviews
        if lower((r.get("user") or {}).get("login")) == lower(reviewer)
        and REVIEW_MARKER in (r.get("body") or "")
    ]
    if not own:
        return out("wait", f"no devflow review by {reviewer}")
    review = max(own, key=lambda r: r.get("submitted_at") or "")
    if review.get("commit_id") != head:
        return out(
            "wait",
            f"the latest devflow review by {reviewer} is on {review.get('commit_id')}, not the head",
        )
    try:
        verdict = parse_review(review.get("body") or "")
    except ValueError as err:  # json.JSONDecodeError is a ValueError
        return out("wait", f"review {review.get('id')} is malformed: {err}")
    if verdict.get("head") != head:
        return out("wait", "the verdict JSON names another head")
    if verdict.get("verdict") != "pass":
        return out("wait", f"verdict is {verdict.get('verdict')!r}")

    # A passing review on the head. From here the bot either approves or asks a person.
    reasons = []
    if verdict.get("tier") != "low":
        reasons.append(
            f"tier is {verdict.get('tier')!r}: {verdict.get('tier_reason') or 'no reason given'}"
        )
    if verdict.get("needs_person") is not False:
        reasons.append("the review asks for a person (needs_person is not false)")
    if permissions.get(lower(author)) not in WRITE:
        reasons.append(f"the author {author} has no write access")
    if tight:
        reasons.append("APPROVAL_MODE_TIGHT is 1: a person approves every pull request")
    if reasons:
        return out("needs-human", *reasons)
    return out("approve", f"passing low-tier review by {reviewer} on the head")


def train_labels(pr: dict) -> list:
    """Labels to add after an approval: devflow:train, unless the pull request is
    a draft, from a fork or labelled needs-human (read again after approving)."""
    labels = {label.get("name") for label in pr.get("labels") or []}
    head_repo = ((pr.get("head") or {}).get("repo") or {}).get("full_name")
    base_repo = ((pr.get("base") or {}).get("repo") or {}).get("full_name")
    if pr.get("draft") or head_repo != base_repo or STOP_LABEL in labels or TRAIN_LABEL in labels:
        return []
    return [TRAIN_LABEL]


def gh(*args: str, stdin: str = None) -> str:
    return subprocess.run(
        ["gh", *args], check=True, capture_output=True, text=True, input=stdin
    ).stdout


def gh_list(path: str) -> list:
    """Every item of a paginated list endpoint."""
    out = gh("api", "--paginate", path, "--jq", ".[]")
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def permission(repo: str, login: str) -> str:
    try:
        return gh(
            "api",
            f"repos/{repo}/collaborators/{login}/permission",
            "--jq",
            ".permission",
        ).strip()
    except subprocess.CalledProcessError as err:
        # A public repo answers "read" for any real user; 404 means no such user.
        # Any other failure exits, so a transient error never adds needs-human.
        if "HTTP 404" in (err.stderr or ""):
            return "none"
        raise


def add_labels(repo: str, number: int, labels: list) -> None:
    args = ["api", "-X", "POST", f"repos/{repo}/issues/{number}/labels"]
    for label in labels:
        args += ["-f", f"labels[]={label}"]
    gh(*args)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--mode", choices=MODES, default="devflow")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    repo, number = args.repo, args.pr
    observe = args.mode == "observe"
    try:
        pr = json.loads(gh("api", f"repos/{repo}/pulls/{number}"))
        reviews = gh_list(f"repos/{repo}/pulls/{number}/reviews?per_page=100")
        comments = gh_list(f"repos/{repo}/issues/{number}/comments?per_page=100")
        logins = {(pr.get("user") or {}).get("login"), assigned_login(comments)} - {
            None
        }
        permissions = {lower(login): permission(repo, login) for login in logins}
        tight = os.environ.get("APPROVAL_MODE_TIGHT") == "1"
        result = decide(pr, reviews, comments, permissions, tight, observe)
        print(json.dumps(result, indent=2))
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a") as fh:
                prefix = "observe (no action taken): " if observe else ""
                fh.write(f"{prefix}**{result['action']}** on `{result['head'][:12]}`\n\n")
                fh.writelines(f"- {reason}\n" for reason in result["reasons"])
        if observe or args.dry_run or result["action"] in ("skip", "wait"):
            return 0
        if result["action"] == "needs-human":
            add_labels(repo, number, [STOP_LABEL])
            return 0
        # Approve only the commit that was judged; a later push gets its own run.
        current = json.loads(gh("api", f"repos/{repo}/pulls/{number}"))
        if current["head"]["sha"] != result["head"]:
            print(f"Head moved to {current['head']['sha']}; the newer event decides.")
            return 0
        body = (
            "Approved automatically: a passing low-tier devflow agent review on this commit. "
            "Paths in the CODEOWNERS floor still need a code owner. The change still lands "
            "only through the dev merge queue and its required checks."
        )
        gh(
            "api",
            "-X",
            "POST",
            f"repos/{repo}/pulls/{number}/reviews",
            "-f",
            f"commit_id={result['head']}",
            "-f",
            "event=APPROVE",
            "-f",
            f"body={body}",
        )
        print(f"approved {result['head']}")
        # GITHUB_TOKEN's label starts no workflow; the train's next tick reads it.
        labels = train_labels(current)
        if labels:
            add_labels(repo, number, labels)
            print(f"labelled {', '.join(labels)}")
    except subprocess.CalledProcessError as err:
        print(f"gh failed: {' '.join(err.cmd)}\n{err.stderr}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
