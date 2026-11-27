#!/usr/bin/env python3
"""Approve a low-tier pull request on a passing devflow agent review.

Runs from .github/workflows/admin-pr-approval.yml (pull_request_target) and
.github/workflows/devflow-approval-sweep.yml (schedule) when APPROVAL_MODE is
`devflow` or `observe`. Both run main's copy of this file with the approver
App token, and read the pull request through the API only: no pull request
code runs here. It decides, then does one of these (tier contract v2; tiers
are ordinal, low < medium < high, and the final tier is the highest of the
reviewer's tier, the verdict's diff-only tier_check and the deterministic
scan):

- approve (low): the approver App approves the head and adds devflow:train.
- train (medium): no bot approval; it adds devflow:train, and the train holds
  the pull request until a person with write access approves the head. Tight
  mode and a review that asks for a person make a low tier medium.
- needs-human (high): the needs-human label; the pull request goes alone.
- wait: no trusted verdict on this head yet.

Every result but approve withdraws the approver's live approvals; wait and
needs-human also remove devflow:train. Before each approval or label it adds,
it reads the pull request again. In `observe` mode it only writes the
decision to the job summary.

    python3 -I devflow_approval.py --repo owner/name (--pr 1234 | --sweep) [--mode devflow|observe]

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
import tomllib
import urllib.parse
from typing import Optional, Sequence

REVIEW_MARKER = "<!-- devflow-review:v1 -->"
JSON_BLOCK = re.compile(r"```json\s*\n(.*?)\n\s*```", re.S)
JSON_FENCE = "```json"
WRITE = {"admin", "write"}
STOP_LABEL = "needs-human"
TRAIN_LABEL = "devflow:train"
HOLD_LABEL = "train:hold"  # a person took the pull request off the train
REVIEWED_LABEL = "devflow:reviewed"
BASE = "dev"
MODES = ("devflow", "observe")
TIERS = ("low", "medium", "high")  # ordinal
# Changes to approval, reviewer assignment, CI or queue policy, their
# workflows and the ruleset config always need a person, on either side of a
# rename. The batch train imports this floor too.
FLOOR_PREFIXES = (
    ".github/",
    "scripts/",
    "futureagi/.ci/",
    "e2e/scripts/",
    ".agents/skills/",
    ".claude/",
)
FLOOR_FILES = (
    "futureagi/pytest.ini",
    "futureagi/pyproject.toml",
    "deploy/update/clickhouse-sources.json",
)
# The deterministic risk scan reads only the pull request's changed files, no
# model: the floor, destructive Django operations and ClickHouse statements in
# a migration, any change to a manifest's dependency tables, and any change to
# a lockfile or a requirements*.txt.
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
# What actually gets installed: any change to one, in any directory, is a hit.
LOCKFILES = ("uv.lock", "yarn.lock", "package-lock.json", "pnpm-lock.yaml", "go.sum")
REQUIREMENTS = re.compile(r"^requirements[^/]*\.txt$")
GO_DIRECTIVE = re.compile(r"^(require|replace)\b\s*(\()?\s*(.*)$")
# The pull request files listing stops at this many files.
FILES_CAP = 3000
EDITS_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviews(first: 100) {
        nodes { databaseId userContentEdits(first: 20) { totalCount nodes { editor { login } } } }
      }
    }
  }
}
"""
SWEEP_QUERY = """
query($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: OPEN, first: 50, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number baseRefName
        labels(first: 50) { nodes { name } }
        reviews(states: APPROVED, first: 50) { nodes { author { login } } }
      }
    }
  }
}
"""


def lower(value) -> str:
    return str(value or "").lower()


def login_key(login) -> str:
    """One form for REST ("app[bot]") and GraphQL ("app") logins."""
    return lower(login).removesuffix("[bot]")


def name_of(review: dict) -> str:
    return (review.get("user") or {}).get("login") or ""


class NoVerdict(ValueError):
    """The body does not hold exactly one ```json block after the marker."""


def parse_review(body: str) -> dict:
    """Return the verdict JSON of one review body. Raise NoVerdict unless the
    body holds exactly one ```json fence, after the marker (parsing v2.1: a
    finding's text could carry a fenced fake verdict), and ValueError if the
    verdict is malformed."""
    fences = body.count(JSON_FENCE)
    if fences != 1:
        raise NoVerdict(f"{fences} ```json blocks, not exactly 1")
    match = JSON_BLOCK.search(body)
    if not match or match.start() < body.find(REVIEW_MARKER):
        raise NoVerdict("the ```json block is not a closed block after the marker")
    verdict = json.loads(match.group(1))
    if not isinstance(verdict, dict) or verdict.get("schema") != "devflow-review/v1":
        raise ValueError("schema is not devflow-review/v1")
    if not isinstance(verdict.get("findings"), list):
        raise ValueError("findings is not a list")
    return verdict


def assigned_login(events: list, assigner: str):
    """The reviewer the assigner (the approver App, whose key only merged code
    reads) requested last, from the issue events. A request or removal by
    anyone else, the author or any workflow's GITHUB_TOKEN, changes nothing;
    a comment or a pull request body never assigns."""
    reviewer = None
    for event in events:
        if login_key((event.get("review_requester") or event.get("actor") or {}).get("login")) != login_key(assigner):
            continue
        requested = (event.get("requested_reviewer") or {}).get("login")
        if event.get("event") == "review_requested" and requested:
            reviewer = requested
        elif event.get("event") == "review_request_removed" and login_key(requested) == login_key(reviewer):
            reviewer = None
    return reviewer


def max_tier(*tiers) -> str:
    """The ordinal maximum of low < medium < high. Anything else (a missing,
    misspelt or unknown tier) is high."""
    if any(tier not in TIERS for tier in tiers):
        return "high"
    return max(tiers, key=TIERS.index)


def bot_approvals(reviews: list, approver: str) -> list:
    """Ids of the approver's reviews that still approve, on any commit."""
    return [
        r.get("id")
        for r in reviews
        if login_key(name_of(r)) == login_key(approver) and r.get("state") == "APPROVED"
    ]


def edited_by_others(review: dict, edits: dict) -> bool:
    """True unless every recorded edit of the review body is by its author.
    `edits` maps a review id to its editors' logins, or None when the list is
    incomplete; a review it does not know counts as edited."""
    if review.get("id") not in edits:
        return True
    editors = edits[review.get("id")]
    return editors is None or any(login_key(e) != login_key(name_of(review)) for e in editors)


def floor_path(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return (
        path.startswith(FLOOR_PREFIXES)
        or path in FLOOR_FILES
        or name == "CODEOWNERS"
        or path.startswith("e2e/playwright.config.")
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
    tables: tuple[str, ...]
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


def is_lockfile(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name in LOCKFILES or bool(REQUIREMENTS.match(name))


def is_migration(path: str) -> bool:
    return bool(CLICKHOUSE_MIGRATION.match(path) or DJANGO_MIGRATION.search(path))


def risk_scan(files: list, read) -> list:
    """Hits of the deterministic scan over pulls/{n}/files items. Every rule
    reads both names of a rename or copy.

    `read(path, side)` returns a file's text on side "base" (the merge base)
    or "head", or None when the file cannot be read. A file the scan cannot
    read is a hit, and so is a deleted, renamed or moved-out migration.
    """
    hits = []
    if len(files) >= FILES_CAP:
        hits.append(f"{len(files)} files, the most the listing returns")
    for item in files:
        path, status = item.get("filename") or "", item.get("status")
        old = item.get("previous_filename") or path
        names = sorted({path, old})
        floor = [n for n in names if floor_path(n)]
        if floor:
            hits.append(f"{path} needs a person: {', '.join(floor)} is approval, review, CI or queue policy")
            continue
        if any(is_lockfile(n) for n in names):
            hits.append(f"{path} changes a lockfile")
            continue
        if any(is_migration(n) for n in names):
            if status == "removed":
                hits.append(f"{path} is deleted")
                continue
            if old != path:
                hits.append(f"{path} is renamed from {old}")
                if not is_migration(path):
                    continue
            # A changed migration may keep its RunPython line untouched, and the
            # listing leaves out the patch of a large diff: both scan the whole file.
            patch = item.get("patch") if status == "added" else None
            text = added_lines(patch) if patch else read(path, "head")
            if text is None:
                hits.append(f"{path} could not be read")
                continue
            if CLICKHOUSE_MIGRATION.match(path):
                found = {
                    (m.group(1) or f"ALTER TABLE ... {m.group(2)}").upper()
                    for m in CLICKHOUSE_RISK.finditer(SQL_COMMENT.sub("", text))
                }
            else:
                found = set(DJANGO_RISK.findall(text))
            if found:
                hits.append(f"{path} adds {', '.join(sorted(found))}")
        elif any(n.rsplit("/", 1)[-1] in MANIFESTS for n in names):
            if path.rsplit("/", 1)[-1] != old.rsplit("/", 1)[-1]:
                hits.append(f"{path} is renamed from {old}")
                continue
            before = "" if status == "added" else read(old, "base")
            after = "" if status == "removed" else read(path, "head")
            if before is None or after is None:
                hits.append(f"{path} could not be read")
                continue
            try:
                tables = manifest_changes(path, before, after)
            except ValueError as err:
                hits.append(f"{path} {err}")
                continue
            if tables:
                hits.append(f"{path} changes {', '.join(tables)}")
    return hits


def labels_of(pr: dict) -> set:
    return {label.get("name") for label in pr.get("labels") or []}


def decide(
    pr: dict,
    reviews: list,
    events: list,
    edits: dict,
    permissions: dict,
    assigner: str,
    tight: bool = False,
    scan: Sequence[str] = (),
) -> dict:
    """Return {"action": "skip"|"wait"|"needs-human"|"train"|"approve", "reasons": [...], "head": sha}.

    `events` are the issue events (review requests); `assigner` is the bot
    login whose requests assign the reviewer. `edits` maps a review id
    to its body's editors (see edited_by_others). `permissions` maps a
    lower-case login to its repository permission (admin, write, read or
    none). `tight` (APPROVAL_MODE_TIGHT is "1") makes the lowest tier medium.
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
    head_repo = ((pr.get("head") or {}).get("repo") or {}).get("full_name")
    if head_repo != ((pr.get("base") or {}).get("repo") or {}).get("full_name"):
        return out("skip", "from a fork")
    if pr.get("draft"):
        return out("wait", "draft")
    if (pr.get("base") or {}).get("ref") != BASE:
        return out("wait", f"base is not {BASE}")
    for label in (STOP_LABEL, HOLD_LABEL):
        if label in labels_of(pr):
            return out("wait", f"has {label}: a person decides")

    reviewer = assigned_login(events, assigner)
    if not reviewer:
        return out("wait", f"no reviewer requested by {assigner}")
    if lower(reviewer) == lower(author):
        return out("wait", f"the assigned reviewer {reviewer} is the author")
    if permissions.get(lower(reviewer)) not in WRITE:
        return out("wait", f"the assigned reviewer {reviewer} has no write access")
    own = [r for r in reviews if lower(name_of(r)) == lower(reviewer) and REVIEW_MARKER in (r.get("body") or "")]
    if not own:
        return out("wait", f"no devflow review by {reviewer}")
    review = max(own, key=lambda r: r.get("submitted_at") or "")
    if review.get("state") == "DISMISSED":
        return out("wait", f"the latest devflow review by {reviewer} is dismissed")
    if edited_by_others(review, edits):
        return out("wait", f"review {review.get('id')} was edited by someone other than {reviewer}")
    if review.get("commit_id") != head:
        return out(
            "wait",
            f"the latest devflow review by {reviewer} is on {review.get('commit_id')}, not the head",
        )
    try:
        verdict = parse_review(review.get("body") or "")
    except NoVerdict as err:
        # A missing verdict is high (parsing v2.1): never approve, ask a person.
        return {
            **out("needs-human", f"review {review.get('id')} carries no verdict ({err}), so it counts as high"),
            "tier": "high",
        }
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
        return result("train", *reasons, "medium: the train waits for an approval on the head by a person with write access")
    return result("approve", f"passing low-tier review by {reviewer} on the head")


def write_blockers(pr: dict, head: str) -> list:
    """Why an approval or a new label must not be written now, read live."""
    labels = labels_of(pr)
    why = []
    if pr.get("state") != "open":
        why.append("not open")
    if pr.get("draft"):
        why.append("draft")
    if (pr.get("base") or {}).get("ref") != BASE:
        why.append(f"base is not {BASE}")
    if (pr.get("head") or {}).get("sha") != head:
        why.append(f"head moved to {(pr.get('head') or {}).get('sha')}")
    why += [f"has {label}" for label in (STOP_LABEL, HOLD_LABEL) if label in labels]
    return why


def gh(*args: str, stdin: Optional[str] = None) -> str:
    return subprocess.run(
        ["gh", *args], check=True, capture_output=True, text=True, input=stdin
    ).stdout


def gh_list(path: str) -> list:
    """Every item of a paginated list endpoint."""
    out = gh("api", "--paginate", path, "--jq", ".[]")
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def graphql(query: str, **variables) -> dict:
    args = ["api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        if value is not None:
            args += ["-F" if isinstance(value, int) else "-f", f"{key}={value}"]
    return json.loads(gh(*args))["data"]


def review_edits(repo: str, number: int) -> dict:
    """{review id: [editor logins]}, None for a review with more edits than read."""
    owner, name = repo.split("/", 1)
    nodes = graphql(EDITS_QUERY, owner=owner, name=name, number=number)["repository"]["pullRequest"]["reviews"]["nodes"]
    out = {}
    for node in nodes:
        edits = node.get("userContentEdits") or {}
        logins = [(e.get("editor") or {}).get("login") or "" for e in edits.get("nodes") or []]
        out[node.get("databaseId")] = logins if edits.get("totalCount", 0) <= len(logins) else None
    return out


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


def sweep_numbers(repo: str, approver: str) -> list:
    """Open pull requests the sweep re-decides: into dev with devflow:reviewed
    or devflow:train, and any with a live approval by the approver (a retarget
    away from dev starts no run here)."""
    owner, name = repo.split("/", 1)
    numbers, after = [], None
    while True:
        page = graphql(SWEEP_QUERY, owner=owner, name=name, after=after)["repository"]["pullRequests"]
        for node in page["nodes"]:
            labels = {label["name"] for label in node["labels"]["nodes"]}
            approved = any(
                login_key((r.get("author") or {}).get("login")) == login_key(approver)
                for r in node["reviews"]["nodes"]
            )
            if approved or (node["baseRefName"] == BASE and labels & {REVIEWED_LABEL, TRAIN_LABEL}):
                numbers.append(node["number"])
        if not page["pageInfo"]["hasNextPage"]:
            return numbers
        after = page["pageInfo"]["endCursor"]


def add_label(repo: str, number: int, label: str) -> None:
    gh("api", "-X", "POST", f"repos/{repo}/issues/{number}/labels", "-f", f"labels[]={label}")
    print(f"labelled {label}")


def remove_label(repo: str, number: int, label: str) -> None:
    gh("api", "-X", "DELETE", f"repos/{repo}/issues/{number}/labels/{urllib.parse.quote(label)}")
    print(f"removed {label}")


def write_summary(result: dict, number: int, observe: bool) -> None:
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary:
        return
    with open(summary, "a") as fh:
        prefix = "observe (no action taken): " if observe else ""
        fh.write(f"#{number}: {prefix}**{result['action']}** on `{result['head'][:12]}`\n\n")
        fh.writelines(f"- {reason}\n" for reason in result["reasons"])
        if "votes" in result:
            votes = ", ".join(f"{k} {v!r}" for k, v in result["votes"].items())
            fh.write(f"\nTier: {result['tier']!r} (votes: {votes})\n")
        fh.write("\n")


def act(repo: str, number: int, pr: dict, reviews: list, result: dict, approver: str) -> None:
    """Write the decision. Withdrawing is always safe; an approval or a new
    label is written only if the pull request, read again, still allows it."""
    action, head = result["action"], result["head"]
    if action == "skip":
        return

    def blocked() -> bool:
        why = write_blockers(json.loads(gh("api", f"repos/{repo}/pulls/{number}")), head)
        if why:
            print(f"No write: {'; '.join(why)}.")
        return bool(why)

    if action != "approve":
        for review_id in bot_approvals(reviews, approver):
            gh(
                "api",
                "-X",
                "PUT",
                f"repos/{repo}/pulls/{number}/reviews/{review_id}/dismissals",
                "-f",
                f"message=Withdrawn: the devflow decision on {head[:12]} is {action}.",
                "-f",
                "event=DISMISS",
            )
            print(f"dismissed review {review_id}")
    labels = labels_of(pr)
    if action in ("wait", "needs-human") and TRAIN_LABEL in labels:
        remove_label(repo, number, TRAIN_LABEL)
    if action == "needs-human" and STOP_LABEL not in labels:
        add_label(repo, number, STOP_LABEL)
    if action == "approve":
        on_head = any(
            login_key(name_of(r)) == login_key(approver) and r.get("state") == "APPROVED" and r.get("commit_id") == head
            for r in reviews
        )
        if not on_head:
            if blocked():
                return
            body = (
                "Approved automatically: a passing low-tier devflow agent review on this commit. "
                "The change still lands only through the dev merge queue and its required checks."
            )
            gh(
                "api",
                "-X",
                "POST",
                f"repos/{repo}/pulls/{number}/reviews",
                "-f",
                f"commit_id={head}",
                "-f",
                "event=APPROVE",
                "-f",
                f"body={body}",
            )
            print(f"approved {head}")
    if action in ("approve", "train") and TRAIN_LABEL not in labels and not blocked():
        add_label(repo, number, TRAIN_LABEL)


def process(repo: str, number: int, approver: str, observe: bool, dry_run: bool, tight: bool) -> dict:
    pr = json.loads(gh("api", f"repos/{repo}/pulls/{number}"))
    reviews = gh_list(f"repos/{repo}/pulls/{number}/reviews?per_page=100")
    events = gh_list(f"repos/{repo}/issues/{number}/events?per_page=100")
    edits = review_edits(repo, number)
    logins = {(pr.get("user") or {}).get("login"), assigned_login(events, approver)} - {None}
    permissions = {lower(login): permission(repo, login) for login in logins}
    result = decide(pr, reviews, events, edits, permissions, approver, tight, scan_pr(repo, number, pr))
    print(json.dumps({"pr": number, **result}, indent=2))
    write_summary(result, number, observe)
    if not (observe or dry_run):
        act(repo, number, pr, reviews, result, approver)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    parser.add_argument("--repo", required=True)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--pr", type=int)
    target.add_argument("--sweep", action="store_true")
    parser.add_argument("--mode", choices=MODES, default="devflow")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    # The approver App's bot login (repository variable DEVFLOW_APPROVER_BOT):
    # it approves, and its review requests are the only trusted assignment.
    approver = os.environ.get("DEVFLOW_APPROVER_BOT") or ""
    if not approver.endswith("[bot]") or approver == "[bot]":
        print("::error::DEVFLOW_APPROVER_BOT is not the approver App's bot login; nothing was decided.", file=sys.stderr)
        return 2
    observe, tight = args.mode == "observe", os.environ.get("APPROVAL_MODE_TIGHT") == "1"
    failed = 0
    try:
        numbers = sweep_numbers(args.repo, approver) if args.sweep else [args.pr]
    except subprocess.CalledProcessError as err:
        print(f"gh failed: {' '.join(err.cmd)}\n{err.stderr}", file=sys.stderr)
        return 2
    for number in numbers:
        try:
            process(args.repo, number, approver, observe, args.dry_run, tight)
        except subprocess.CalledProcessError as err:
            # One pull request's failure never stops the sweep; the run still fails.
            print(f"#{number}: gh failed: {' '.join(err.cmd)}\n{err.stderr}", file=sys.stderr)
            failed += 1
    return 2 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
