#!/usr/bin/env python3
"""Approve a low-tier pull request on a passing devflow agent review.

Runs from .github/workflows/devflow-approval.yml when APPROVAL_MODE is
`devflow`. It reads the pull request live by number, decides, and then does
one of three things:

- approve: the bot approves the head commit.
- needs-human: the bot adds the needs-human label and does not approve.
- wait: nothing yet (no trusted review on this head, or the verdict is not pass).

    python3 -I devflow_approval.py --repo owner/name --pr 1234 [--dry-run]

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
from pathlib import Path

REVIEW_MARKER = "<!-- devflow-review:v1 -->"
ASSIGN_MARKER = re.compile(r"<!--\s*devflow-assigned:v1\b([^>]*)-->")
JSON_BLOCK = re.compile(r"```json\s*\n(.*?)\n\s*```", re.S)
# reviewer-assigner writes the assignment marker with GITHUB_TOKEN. Anyone can
# post the marker text on a public repo, so only the bot's comment counts.
BOT = re.compile(r"^github-actions\[bot\]$")  # the REST login of GITHUB_TOKEN writes
WRITE = {"admin", "write"}
STOP_LABEL = "needs-human"
BASE = "dev"
FLOOR_FILE = Path(__file__).resolve().parent.parent / "approval-floor.json"


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


def glob_regex(glob: str):
    """`**/` is any number of directories, `**` is anything, `*` and `?` stay in one segment."""
    out, i = [], 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.compile("".join(out), re.I)


def floor_hits(files: list, floor: list) -> list:
    """Changed paths (old and new names) that match the floor."""
    patterns = [glob_regex(g) for g in floor]
    paths = []
    for f in files:
        paths += [p for p in (f.get("filename"), f.get("previous_filename")) if p]
    return sorted({p for p in paths if any(r.fullmatch(p) for r in patterns)})


def decide(
    pr: dict,
    reviews: list,
    comments: list,
    files: list,
    permissions: dict,
    floor: list,
    tight: bool = False,
) -> dict:
    """Return {"action": "skip"|"wait"|"needs-human"|"approve", "reasons": [...], "head": sha}.

    `permissions` maps a lower-case login to its repository permission
    (admin, write, read or none), from collaborators/{login}/permission.
    `tight` (APPROVAL_MODE_TIGHT is "1") sends every passing review to a person.
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
    if any(
        BOT.match((r.get("user") or {}).get("login") or "")
        and r.get("state") == "APPROVED"
        and r.get("commit_id") == head
        for r in reviews
    ):
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
    if len(files) != pr.get("changed_files"):
        reasons.append(f"read {len(files)} of {pr.get('changed_files')} changed files")
    hits = floor_hits(files, floor)
    if hits:
        reasons.append(
            "touches the approval floor: "
            + ", ".join(hits[:10])
            + (" …" if len(hits) > 10 else "")
        )
    if permissions.get(lower(author)) not in WRITE:
        reasons.append(f"the author {author} has no write access")
    if tight:
        reasons.append("APPROVAL_MODE_TIGHT is 1: a person approves every pull request")
    if reasons:
        return out("needs-human", *reasons)
    return out("approve", f"passing low-tier review by {reviewer} on the head")


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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    repo, number = args.repo, args.pr
    floor = json.loads(FLOOR_FILE.read_text())["paths"]
    try:
        pr = json.loads(gh("api", f"repos/{repo}/pulls/{number}"))
        reviews = gh_list(f"repos/{repo}/pulls/{number}/reviews?per_page=100")
        comments = gh_list(f"repos/{repo}/issues/{number}/comments?per_page=100")
        files = gh_list(f"repos/{repo}/pulls/{number}/files?per_page=100")
        logins = {(pr.get("user") or {}).get("login"), assigned_login(comments)} - {
            None
        }
        permissions = {lower(login): permission(repo, login) for login in logins}
        tight = os.environ.get("APPROVAL_MODE_TIGHT") == "1"
        result = decide(pr, reviews, comments, files, permissions, floor, tight)
        print(json.dumps(result, indent=2))
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a") as fh:
                fh.write(f"**{result['action']}** on `{result['head'][:12]}`\n\n")
                fh.writelines(f"- {reason}\n" for reason in result["reasons"])
        if args.dry_run or result["action"] in ("skip", "wait"):
            return 0
        if result["action"] == "needs-human":
            gh(
                "api",
                "-X",
                "POST",
                f"repos/{repo}/issues/{number}/labels",
                "-f",
                f"labels[]={STOP_LABEL}",
            )
            return 0
        # Approve only the commit that was judged; a later push gets its own run.
        current = gh("api", f"repos/{repo}/pulls/{number}", "--jq", ".head.sha").strip()
        if current != result["head"]:
            print(f"Head moved to {current}; the newer event decides.")
            return 0
        body = (
            "Approved automatically: a passing low-tier devflow agent review on this commit, "
            "and no file on the approval floor. The change still lands only through the dev "
            "merge queue and its required checks."
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
    except subprocess.CalledProcessError as err:
        print(f"gh failed: {' '.join(err.cmd)}\n{err.stderr}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
