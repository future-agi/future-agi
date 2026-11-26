#!/usr/bin/env python3
"""Approve a low-tier pull request on a passing devflow agent review.

Runs from .github/workflows/devflow-approval.yml when APPROVAL_MODE is
`devflow` or `observe`. It reads the pull request live by number, decides,
and then does one of these (tier contract v2; tiers are ordinal, low <
medium < high, and the final tier is the highest of the reviewer's tier, the
verdict's diff-only tier_check and the deterministic risk scan):

- approve (low): the bot approves the head commit and adds devflow:train, so
  the batch train may take it.
- train (medium, after a person approved the head): the bot adds
  devflow:train and does not approve. Tight mode and a review that asks for
  a person make a low tier medium.
- needs-human (high): the bot adds the needs-human label; the pull request
  goes to the queue alone.
- wait: nothing yet (no trusted review on this head, the verdict is not
  pass, or a medium tier has no approval from a person yet).

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
import time
import tomllib
import urllib.parse

REVIEW_MARKER = "<!-- devflow-review:v1 -->"
ASSIGN_MARKER = re.compile(r"<!--\s*devflow-assigned:v1\b([^>]*)-->")
JSON_BLOCK = re.compile(r"```json\s*\n(.*?)\n\s*```", re.S)
# reviewer-assigner writes the assignment marker with GITHUB_TOKEN. Anyone can
# post the marker text on a public repo, so only the bot's comment counts.
BOT = re.compile(r"^github-actions\[bot\]$")  # the REST login of GITHUB_TOKEN writes
WRITE = {"admin", "write"}
STOP_LABEL = "needs-human"
TRAIN_LABEL = "devflow:train"
FULL_LABEL = "ci:full"  # Backend CI's full plan under CI_SCOPE_MODE=light
BASE = "dev"
MODES = ("devflow", "observe")
TIERS = ("low", "medium", "high")  # ordinal
# The deterministic risk scan reads only the pull request's changed files, no
# model: destructive Django operations and ClickHouse statements in a
# migration's added lines, and any change to a manifest's dependency tables.
DJANGO_MIGRATION = re.compile(r"(^|/)migrations/(?!__init__\.py$)[^/]+\.py$")
DJANGO_RISK = re.compile(r"\b(RemoveField|DeleteModel|RenameField|RenameModel|RunSQL|RunPython)\b")
CLICKHOUSE_MIGRATION = re.compile(
    r"^futureagi/tracer/services/clickhouse/(?:v2/)?(?:schema|oss_cdc_migrations)/[^/]+\.sql$"
)
CLICKHOUSE_RISK = re.compile(
    r"\b(DROP|RENAME|TRUNCATE)\b|\bALTER\s+TABLE\b[^;]*?\b(DELETE|UPDATE)\b",
    re.IGNORECASE | re.DOTALL,
)
SQL_COMMENT = re.compile(r"--[^\n]*")
# Dependency tables per manifest, as dotted keys.
PYPROJECT_TABLES = (
    "build-system.requires",
    "project.dependencies",
    "project.optional-dependencies",
    "dependency-groups",
    "tool.uv.dev-dependencies",
    "tool.uv.constraint-dependencies",
    "tool.uv.override-dependencies",
    "tool.uv.sources",
    "tool.uv.index",
    "tool.poetry.dependencies",
    "tool.poetry.dev-dependencies",
    "tool.poetry.group",
)
PACKAGE_TABLES = (
    "dependencies",
    "devDependencies",
    "optionalDependencies",
    "peerDependencies",
    "overrides",
    "resolutions",
)
MANIFESTS = ("pyproject.toml", "package.json", "go.mod")
GO_DIRECTIVE = re.compile(r"^(require|replace)\b\s*(\()?\s*(.*)$")
# The pull request files listing stops at this many files.
FILES_CAP = 3000


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


def max_tier(*tiers) -> str:
    """The ordinal maximum of low < medium < high. Anything else (a missing,
    misspelt or unknown tier) is high."""
    if any(tier not in TIERS for tier in tiers):
        return "high"
    return max(tiers, key=TIERS.index)


def approvers(reviews: list, head: str) -> list:
    """Logins whose newest approving or blocking review approves the head."""
    latest = {}
    for r in sorted(reviews, key=lambda r: r.get("submitted_at") or ""):
        if r.get("state") in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            latest[lower((r.get("user") or {}).get("login"))] = r
    return [
        (r.get("user") or {}).get("login")
        for r in latest.values()
        if r.get("state") == "APPROVED" and r.get("commit_id") == head
    ]


def human_approver(reviews: list, head: str, author: str, permissions: dict):
    """A person, not the author or the bot, with write access who approved the head."""
    for login in approvers(reviews, head):
        if lower(login) != lower(author) and not BOT.match(login or "") and permissions.get(lower(login)) in WRITE:
            return login
    return None


def bot_approved(reviews: list, head: str) -> bool:
    return any(
        BOT.match((r.get("user") or {}).get("login") or "")
        and r.get("state") == "APPROVED"
        and r.get("commit_id") == head
        for r in reviews
    )


def added_lines(patch: str) -> str:
    return "\n".join(
        line[1:]
        for line in patch.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )


def dotted(data, key: str):
    for part in key.split("."):
        if not isinstance(data, dict):
            return None
        data = data.get(part)
    return data


def go_directives(text: str) -> set:
    """Every require and replace entry of a go.mod, one string per entry."""
    out, block = set(), None
    for raw in text.splitlines():
        line = raw.split("//", 1)[0].strip()
        if block:
            if line == ")":
                block = None
            elif line:
                out.add(f"{block} {line}")
            continue
        match = GO_DIRECTIVE.match(line)
        if match and match.group(2):
            block = match.group(1)
        elif match and match.group(3):
            out.add(f"{match.group(1)} {match.group(3)}")
    return out


def manifest_changes(path: str, old: str, new: str) -> list:
    """The dependency tables that differ between two versions of a manifest
    ("" is a missing file). Raise ValueError when a version does not parse."""
    name = path.rsplit("/", 1)[-1]
    if name == "go.mod":
        return ["require/replace"] if go_directives(old) != go_directives(new) else []
    if name == "package.json":
        tables = PACKAGE_TABLES

        def load(text):
            return json.loads(text) if text.strip() else {}

    else:
        tables, load = PYPROJECT_TABLES, tomllib.loads
    try:
        before, after = load(old), load(new)
    except ValueError as err:  # JSONDecodeError and TOMLDecodeError are ValueErrors
        raise ValueError(f"does not parse: {err}") from err
    return [key for key in tables if dotted(before, key) != dotted(after, key)]


def risk_scan(files: list, read) -> list:
    """Hits of the deterministic risk scan over pulls/{n}/files items.

    `read(path, side)` returns a file's text on side "base" (the merge base)
    or "head", or None when the file cannot be read. A file the scan cannot
    read is a hit, and so is a deleted or renamed migration.
    """
    hits = []
    if len(files) >= FILES_CAP:
        hits.append(f"{len(files)} files, the most the listing returns")
    for item in files:
        path, status = item.get("filename") or "", item.get("status")
        clickhouse = bool(CLICKHOUSE_MIGRATION.match(path))
        if clickhouse or DJANGO_MIGRATION.search(path):
            if status == "removed":
                hits.append(f"{path} is deleted")
                continue
            if status == "renamed":
                hits.append(f"{path} is renamed from {item.get('previous_filename')}")
            # A changed migration may keep its RunPython line untouched, and the
            # listing leaves out the patch of a large diff: both scan the whole file.
            patch = item.get("patch") if status == "added" else None
            text = added_lines(patch) if patch else read(path, "head")
            if text is None:
                hits.append(f"{path} could not be read")
                continue
            if clickhouse:
                found = {
                    (m.group(1) or f"ALTER TABLE ... {m.group(2)}").upper()
                    for m in CLICKHOUSE_RISK.finditer(SQL_COMMENT.sub("", text))
                }
            else:
                found = set(DJANGO_RISK.findall(text))
            if found:
                hits.append(f"{path} adds {', '.join(sorted(found))}")
        elif path.rsplit("/", 1)[-1] in MANIFESTS:
            old = "" if status == "added" else read(item.get("previous_filename") or path, "base")
            new = "" if status == "removed" else read(path, "head")
            if old is None or new is None:
                hits.append(f"{path} could not be read")
                continue
            try:
                tables = manifest_changes(path, old, new)
            except ValueError as err:
                hits.append(f"{path} {err}")
                continue
            if tables:
                hits.append(f"{path} changes {', '.join(tables)}")
    return hits


def decide(
    pr: dict,
    reviews: list,
    comments: list,
    permissions: dict,
    tight: bool = False,
    observe: bool = False,
    scan: list = (),
) -> dict:
    """Return {"action": "skip"|"wait"|"needs-human"|"approve", "reasons": [...], "head": sha}.

    `permissions` maps a lower-case login to its repository permission
    (admin, write, read or none), from collaborators/{login}/permission.
    `tight` (APPROVAL_MODE_TIGHT is "1") sends every passing review to a person.
    `observe` decides on every head, even one the bot already approved (the
    admin rule still approves in that mode), so the decisions can be compared.
    `scan` is risk_scan()'s hits on the pull request's files.

    The tier has three votes, and one high vote makes it high: the reviewer's
    `tier`, the verdict's `tier_check` (a separate judgement of the diff
    alone; a missing one counts as high) and the scan. A decision on a
    passing review also returns the three votes.
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

    # A passing review on the head. From here the tier decides (tier contract v2).
    reasons = []
    check = verdict.get("tier_check")
    check = check if isinstance(check, dict) else {}
    diff_only = check.get("input") == "diff-only"
    votes = {
        "reviewer": verdict.get("tier"),
        "tier_check": check.get("tier") if diff_only else None,
        "scan": "high" if scan else "low",
    }
    if votes["reviewer"] != "low":
        reasons.append(
            f"tier is {verdict.get('tier')!r}: {verdict.get('tier_reason') or 'no reason given'}"
        )
    if not check:
        reasons.append("tier_check is missing, so it counts as high")
    elif not diff_only:
        reasons.append(f"tier_check input is {check.get('input')!r}, not 'diff-only', so it counts as high")
    elif votes["tier_check"] != "low":
        reasons.append(
            f"tier_check tier is {votes['tier_check']!r} (engine {check.get('engine') or 'unknown'})"
        )
    if scan:
        reasons.append("scan: " + "; ".join(scan))
    tier = max_tier(*votes.values())
    if permissions.get(lower(author)) not in WRITE:
        reasons.append(f"the author {author} has no write access, so it counts as high")
        tier = "high"
    # A low review that still asks for a person, and tight mode, raise low to
    # medium: a person approves, and tightness never adds needs-human.
    if verdict.get("needs_person") is not False:
        reasons.append("the review asks for a person (needs_person is not false)")
        tier = max_tier(tier, "medium")
    if tight:
        reasons.append("APPROVAL_MODE_TIGHT is 1: the lowest tier is medium")
        tier = max_tier(tier, "medium")

    def result(action, *why):
        return {**out(action, *why), "tier": tier, "votes": votes}

    if tier == "high":
        return result("needs-human", *reasons)
    if tier == "medium":
        approver = human_approver(reviews, head, author, permissions)
        if approver:
            return result("train", *reasons, f"approved on the head by {approver}")
        return result("wait", *reasons, "medium: waiting for an approval on the head by a person with write access")
    return result("approve", f"passing low-tier review by {reviewer} on the head")


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


def read_file(repo: str, path: str, ref: str):
    """A file's text at a commit; None when it is absent there (404)."""
    try:
        return gh(
            "api",
            "-H",
            "Accept: application/vnd.github.raw",
            f"repos/{repo}/contents/{urllib.parse.quote(path)}?ref={ref}",
        )
    except subprocess.CalledProcessError as err:
        if "HTTP 404" in (err.stderr or ""):
            return None
        raise


def scan_pr(repo: str, number: int, pr: dict) -> list:
    """risk_scan() on the pull request's files, read live. The base side is the
    merge base, which is what the files listing compares against."""
    files = gh_list(f"repos/{repo}/pulls/{number}/files?per_page=100")
    head, merge_base = pr["head"]["sha"], {}

    def read(path, side):
        if side == "head":
            return read_file(repo, path, head)
        if "sha" not in merge_base:
            merge_base["sha"] = gh(
                "api",
                f"repos/{repo}/compare/{pr['base']['sha']}...{head}",
                "--jq",
                ".merge_base_commit.sha",
            ).strip()
        return read_file(repo, path, merge_base["sha"])

    return risk_scan(files, read)


def full_run_action(run, jobs: list) -> str:
    """What a high pull request's newest Backend CI run needs so its head gets
    a full run under CI_SCOPE_MODE=light: "none" (full, or no run yet: a new
    run reads ci:full live), "wait" (its plan is not decided yet), "rerun"
    (a finished light run) or "cancel-rerun" (a light run still going).
    The light plan skips the `plan` job."""
    if not run:
        return "none"
    plan = next((j for j in jobs if j.get("name") == "plan"), None)
    if plan is None or plan.get("status") != "completed":
        return "wait"
    if plan.get("conclusion") != "skipped":
        return "none"
    return "rerun" if run.get("status") == "completed" else "cancel-rerun"


def start_full_run(repo: str, head: str) -> None:
    """One whole-run re-run of the newest light Backend CI run on the head, so
    its changes job reads ci:full live and runs the full plan. Not
    rerun-failed-jobs: that keeps the light plan."""
    runs = json.loads(
        gh("api", f"repos/{repo}/actions/workflows/backend-ci.yml/runs?event=pull_request&head_sha={head}&per_page=20")
    ).get("workflow_runs") or []
    run = max(runs, key=lambda r: r["created_at"]) if runs else None
    jobs = []
    if run:
        out = gh("api", "--paginate", f"repos/{repo}/actions/runs/{run['id']}/jobs?filter=latest&per_page=100", "--jq", ".jobs[]")
        jobs = [json.loads(line) for line in out.splitlines() if line.strip()]
    action = full_run_action(run, jobs)
    print(f"full run for {head[:12]}: {action}")
    if action == "cancel-rerun":
        gh("api", "-X", "POST", f"repos/{repo}/actions/runs/{run['id']}/cancel")
        for _ in range(18):
            if json.loads(gh("api", f"repos/{repo}/actions/runs/{run['id']}")).get("status") == "completed":
                break
            time.sleep(5)
        else:
            print(f"::warning::run {run['id']} did not stop in 90 s; re-run it after adding ci:full")
            return
    if action in ("rerun", "cancel-rerun"):
        gh("api", "-X", "POST", f"repos/{repo}/actions/runs/{run['id']}/rerun")
        print(f"re-ran run {run['id']} for the full plan")


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
        # The author, the assigned reviewer, and whoever approved the head (medium).
        logins = {
            (pr.get("user") or {}).get("login"),
            assigned_login(comments),
            *(a for a in approvers(reviews, pr["head"]["sha"]) if not BOT.match(a or "")),
        } - {None}
        permissions = {lower(login): permission(repo, login) for login in logins}
        tight = os.environ.get("APPROVAL_MODE_TIGHT") == "1"
        result = decide(pr, reviews, comments, permissions, tight, observe, scan_pr(repo, number, pr))
        print(json.dumps(result, indent=2))
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a") as fh:
                prefix = "observe (no action taken): " if observe else ""
                fh.write(f"{prefix}**{result['action']}** on `{result['head'][:12]}`\n\n")
                fh.writelines(f"- {reason}\n" for reason in result["reasons"])
                if "votes" in result:
                    votes = ", ".join(f"{k} {v!r}" for k, v in result["votes"].items())
                    fh.write(f"\nTier: {result['tier']!r} (votes: {votes})\n")
        if observe or args.dry_run or result["action"] in ("skip", "wait"):
            return 0
        if result["action"] == "needs-human":
            # High goes to the queue alone, and the train never takes it, so
            # under light CI this job starts its full run (contract v2).
            light = os.environ.get("CI_SCOPE_MODE") == "light"
            add_labels(repo, number, [STOP_LABEL, FULL_LABEL] if light else [STOP_LABEL])
            if light:
                start_full_run(repo, result["head"])
            return 0
        # Act only on the commit that was judged; a later push gets its own run.
        current = json.loads(gh("api", f"repos/{repo}/pulls/{number}"))
        if current["head"]["sha"] != result["head"]:
            print(f"Head moved to {current['head']['sha']}; the newer event decides.")
            return 0
        if result["action"] == "train":
            # Medium: a person approved the head, so it may batch. No bot approval.
            labels = train_labels(current)
            if labels:
                add_labels(repo, number, labels)
                print(f"labelled {', '.join(labels)}")
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
