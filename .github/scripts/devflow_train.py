"""Batch train for the dev merge queue (devflow-train.yml).

GitHub's merge queue runs the full CI once per queue entry. The train puts
several approved pull requests into ONE entry: a rollup pull request whose
head is dev plus each member head, merged in order by GitHub's merges API
with no other change. The rollup's own pull_request run is the full internal
suite (merge groups run without the private EE package); the queue then
tests and lands it like any other entry. The queue stays the only writer to
dev and nobody bypasses a rule.

One tick reads GitHub, plans, and (live mode only) acts:

1. Open rollups. A member that moved, closed, lost its admit label or its
   approval, gained a high label or was enqueued alone makes the rollup
   stale: close it and let the rest re-batch. A rollup that is no longer
   approved on its head was pushed to by someone: close it. A rollup whose required checks
   failed, or that the queue removed, is retried once only if every failed
   job matches a known infra signature; otherwise it splits in halves at
   once, and a red single member leaves with train:red. A red single member
   whose failed job also fails on dev's newest finished push run is held
   instead (the breaker may not have tripped yet): no train:red, and its
   rollup is rebuilt once dev passes that job. A rollup removed for
   a conflict is rebuilt on the new dev. While an open issue carries the
   base-red label, no rollup starts, and a red one is closed without a split.
2. Admitted pull requests (label devflow:train). Exclusions send a pull
   request down the normal path: drafts, forks, high tier (tier contract
   v2), reverts, natively enqueued, auto-merge on a full head, conflicts with
   dev, high-risk or code-owned paths, required checks not green on the
   head, no approval on the head (medium: a person's). One that goes alone
   on a light-mode (deferred) head gets ci:full and a full re-run. A member
   whose checks are still running keeps a live loop ticking.
3. Batches. Size B = clamp(ceil(eligible / free train slots), 1, MAX_BATCH),
   so under load B grows and when idle a pull request goes alone at once.
   Two members that each add a migration to the same app never share a
   batch: together they would leave two migration leaves.

Dark mode (the default) only prints the plan. Live mode needs the train App
(TRAIN_APP_ID, TRAIN_APP_PRIVATE_KEY: branch, merges, pull request,
auto-merge, dequeue, close) and GITHUB_TOKEN (GH_TOKEN: approve as
github-actions[bot], labels, comments, re-runs). The script mints the App's
installation token itself and again before it expires, so one live run can
keep ticking for hours while rollups are in flight. No pull request code
runs here: there is no checkout.

    python3 -I .github/scripts/devflow_train.py tick [--mode dark|live] [--loop-minutes N] [--idle-minutes N]
    python3 -I .github/scripts/devflow_train.py parity --candidate SHA --landed SHA

State lives only in GitHub (labels, the rollup body manifest, marker
comments), so every tick rebuilds it and a crash loses nothing. A live run
that exits on an error posts one Slack line when DEVFLOW_HEARTBEAT=live.
"""

from __future__ import annotations

import argparse
import base64
import calendar
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

SCHEMA = "devflow-train/v1"
MANIFEST_MARKER = "<!-- devflow-train:v1 -->"
RED_MARKER = re.compile(
    r"<!--\s*devflow-train:red\s+pr=(\d+)\s+sha=([0-9a-f]{40})\s*-->"
)
VERDICT_MARKER = "<!-- devflow-review:v1 -->"
# reviewer-assigner names the reviewer it asked, as github-actions[bot].
ASSIGN_MARKER = re.compile(r"<!--\s*devflow-assigned:v1\b([^>]*)-->")
JSON_BLOCK = re.compile(r"```json\s*\n(.*?)\n\s*```", re.DOTALL)
MIGRATION = re.compile(r"^(?P<app>.+)/migrations/\d{4}_[^/]+\.py$")

ADMIT_LABEL = "devflow:train"
ROLLUP_LABEL = "devflow:rollup"
RED_LABEL = "train:red"
CONFLICT_LABEL = "train:conflict"
STALE_LABEL = "train:stale"
HIGH_LABELS = ("needs-human", "autopilot-high")
# The base-health breaker (devflow_tick.py) files an open issue with this
# label per job that fails across queue builds: "Base looks red: <job>".
BASE_RED_LABEL = "base-red"
BRANCH_PREFIX = "chore/devflow-train-"
# Under CI_SCOPE_MODE=light a pull request run that defers the backend
# suites reports this name in place of the required "Backend Tests Pass", so
# GitHub cannot enqueue that head alone. It is green enough to ride a rollup,
# whose own run is always full. A member that goes alone instead gets the
# ci:full label and a full re-run of Backend CI (rerun-failed-jobs would keep
# the light plan).
DEFERRED_CHECKS = {"Backend Tests Pass": "Backend Tests Deferred"}
FULL_LABEL = "ci:full"
BACKEND_WORKFLOW = "backend-ci.yml"
GREEN = ("success", "deferred")
CHECKS_NOT_GREEN = "required checks not green: "
BASE = "dev"
WRITE = {"admin", "write"}
# Tier contract v2: ordinal, and anything else (case, empty, null) is high.
TIERS = ("low", "medium", "high")
ACTIONS_LOGIN = "github-actions"
POLICY_FILE = ".github/pr-autopilot.yml"
# A path with a code owner needs that owner's review, which the bot's
# approval never gives: the pull request goes alone.
CODEOWNERS_FILE = ".github/CODEOWNERS"
# Always goes alone: a change to CI would change the batch's own checks.
FLOOR_GLOBS = (".github/**",)
# Failures a retry can fix, shared with registry-retry.sh. Every failed job
# of a red rollup must match one, or the rollup splits. Without the file on
# dev, nothing counts as infra.
SIGNATURES_FILE = ".github/ci-infra-signatures.json"
INFRA_CLASSES = {"rate_limit", "transient", "apt"}
POSIX_CLASSES = {
    "[:space:]": r"\s",
    "[:digit:]": r"\d",
    "[:alpha:]": "a-zA-Z",
    "[:alnum:]": "a-zA-Z0-9",
    "[:upper:]": "A-Z",
    "[:lower:]": "a-z",
}
LOG_TAIL_LINES = 400
# Dev's push runs of the suite a red rollup failed: the queue calls
# backend-ci.yml as "backend", so its jobs read "backend / <job>".
DEV_TWINS = {"backend-queue-ci.yml": ("backend-ci.yml", "backend / ")}
# Pages of dev push runs read to find the newest finished one: a burst of
# cancelled runs can fill the first page.
DEV_PUSH_PAGES = 5
# A member head's check runs are read again only when the head moves, while
# a check is not green, or after this long (a person may re-run a check).
CHECK_CACHE_SECONDS = 900
# Below this many calls left in a rate limit window, wait for its reset.
RATE_FLOOR = 200
# A long loop reads the policy files and required checks on dev again.
CONFIG_SECONDS = 600
# Consecutive failed ticks before a live loop stops with the error.
MAX_FAILED_TICKS = 3


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    return int(value) if value else default


class Config:
    def __init__(self, high_globs=None, **overrides):
        self.max_batch = env_int("DEVFLOW_TRAIN_MAX_BATCH", 6)
        # Train rollups in flight; the rest of the queue's builds stay free
        # for pull requests that go alone.
        self.max_rollups = env_int("DEVFLOW_TRAIN_MAX_ROLLUPS", 4)
        # max_entries_to_build on the dev queue ruleset (27819469).
        self.queue_builds = env_int("DEVFLOW_TRAIN_QUEUE_BUILDS", 6)
        # Off only as an explicit operator opt-out; see member_tier().
        self.require_verdict = (
            os.environ.get("DEVFLOW_TRAIN_REQUIRE_VERDICT", "") != "false"
        )
        # APPROVAL_MODE_TIGHT=1: the minimum final tier is medium.
        self.tight = os.environ.get("APPROVAL_MODE_TIGHT", "") == "1"
        self.train_login = bot_login(
            os.environ.get("DEVFLOW_TRAIN_LOGIN") or "futureagi-devflow-train[bot]"
        )
        self.high_globs = tuple(FLOOR_GLOBS) + tuple(high_globs or ())
        self.signatures: list = []
        self.codeowners = ""  # CODEOWNERS text on dev
        for key, value in overrides.items():
            setattr(self, key, value)


def bot_login(login: str) -> str:
    """GraphQL says 'name', REST says 'name[bot]'; compare without the suffix."""
    return (login or "").removesuffix("[bot]")


def parse_high_globs(text: str | None) -> list:
    """high_globs from the autopilot policy (JSON, a YAML subset); [] if absent."""
    if text is None:
        return []
    globs = json.loads(text).get("high_globs", [])
    if not isinstance(globs, list) or not all(isinstance(g, str) for g in globs):
        raise ValueError(f"{POLICY_FILE}: high_globs is not a list of strings")
    return globs


# Globs as the autopilot policy defines them: * matches within one path
# segment; only ** as a whole segment spans zero or more directories.
def glob_to_regex(glob: str) -> re.Pattern:
    parts = glob.split("/")
    out = ""
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        if part == "**":
            out += ".*" if last else "(?:[^/]+/)*"
            continue
        seg = ""
        for ch in part:
            seg += "[^/]*" if ch == "*" else ("[^/]" if ch == "?" else re.escape(ch))
        out += seg if last else seg + "/"
    return re.compile(out + r"\Z")


def high_path(files: list, globs: tuple) -> str | None:
    compiled = [(g, glob_to_regex(g)) for g in globs]
    for path in files:
        for glob, rx in compiled:
            if rx.match(path):
                return f"{path} matches {glob}"
    return None


# The same matcher as ci_scope.codeowners_regex (the light plan's code-owned
# rule), so the train and CI agree on which pull requests go alone; a test
# compares the two.
def codeowners_regex(pattern: str) -> re.Pattern:
    """GitHub's CODEOWNERS matching (gitignore rules without ! and [ ]).
    Case-sensitive, like GitHub."""
    anchored = "/" in pattern.rstrip("/")
    directory = pattern.endswith("/")
    body, out, i = pattern.strip("/"), [], 0
    while i < len(body):
        if body.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif body[i] == "*":
            out.append("[^/]*")
            i += 1
        else:
            out.append(re.escape(body[i]))
            i += 1
    tail = "/.*" if directory else "(?:/.*)?"
    return re.compile(("" if anchored else "(?:.*/)?") + "".join(out) + tail, re.DOTALL)


def code_owned(files: list, text: str) -> str | None:
    """The first path whose last matching CODEOWNERS line names owners."""
    rules = []
    for line in text.splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            pattern, *who = line.split()
            rules.append((codeowners_regex(pattern), who))
    for path in files:
        found = []
        for rx, who in rules:
            if rx.fullmatch(path):
                found = who
        if found:
            return path
    return None


def migration_apps(files: list) -> set:
    """Apps in which this change adds a migration file."""
    apps = set()
    for f in files:
        match = MIGRATION.match(f["path"])
        if match and f.get("changeType") == "ADDED":
            apps.add(match.group("app"))
    return apps


# --- Reading a pull request -------------------------------------------------


def labels_of(node: dict) -> set:
    return {label["name"] for label in (node.get("labels") or {}).get("nodes", [])}


def by_actions_bot(item: dict) -> bool:
    """Written by GITHUB_TOKEN: a user may be called github-actions, a Bot may not."""
    author = item.get("author") or {}
    return (
        author.get("__typename") == "Bot"
        and bot_login(author.get("login")) == ACTIONS_LOGIN
    )


def assigned_reviewer(node: dict) -> str | None:
    """The login in the newest bot comment that carries the assignment marker."""
    for comment in reversed((node.get("comments") or {}).get("nodes", [])):
        if not by_actions_bot(comment):
            continue
        match = ASSIGN_MARKER.search(comment.get("body") or "")
        if match:
            pairs = dict(p.split("=", 1) for p in match.group(1).split() if "=" in p)
            if pairs.get("login"):
                return pairs["login"]
    return None


def latest_verdict(
    reviews: list,
    author: str,
    assigned: str | None = None,
    permissions: dict | None = None,
) -> dict | None:
    """The verdict of the newest devflow-review/v1 review posted by someone with
    write access who is not the author. The assigned reviewer's newest review
    wins when there is one: a later review by someone else does not replace
    it. A malformed newest review means no verdict; an older one never stands
    in for it.

    `permissions` maps a lower-case login to its repository permission
    (GitHub.attach_reviewer_permissions). The review's authorAssociation is
    not used: GitHub shows an org member with private membership as
    CONTRIBUTOR to the bot token."""
    permissions = permissions or {}
    trusted = [
        r
        for r in reviews
        if VERDICT_MARKER in (r.get("body") or "")
        and permissions.get(lower_login(r)) in WRITE
        and lower_login(r) != author.lower()
    ]
    trusted.sort(key=lambda r: r.get("submittedAt") or "")
    own = [r for r in trusted if assigned and lower_login(r) == assigned.lower()]
    newest = (own or trusted)[-1:]
    return parse_verdict(newest[0].get("body") or "") if newest else None


def lower_login(item: dict) -> str:
    return bot_login((item.get("author") or {}).get("login")).lower()


def parse_verdict(body: str) -> dict | None:
    """The verdict JSON of one review body (tier contract v2.1 parsing): the
    body must hold exactly one ```json fence, or a fake verdict fenced inside
    a finding's text could be read first. The block comes after the marker."""
    if body.count("```json") != 1:
        return None
    match = JSON_BLOCK.search(body)
    marker = body.find(VERDICT_MARKER)
    if not match or marker < 0 or match.start() < marker:
        return None
    try:
        verdict = json.loads(match.group(1))
    except ValueError:
        return None
    if isinstance(verdict, dict) and verdict.get("schema") == "devflow-review/v1":
        return verdict
    return None


def tier_rank(tier) -> int:
    return TIERS.index(tier) if isinstance(tier, str) and tier in TIERS else 2


def final_tier(verdict: dict, tight: bool = False) -> str:
    """The ordinal max of the reviewer's tier and the diff-only tier_check (a
    missing one, or one whose input is not "diff-only", is high). Low that
    asks for a person, or tight mode, is at least medium. The deterministic
    path scan is high_path and code_owned."""
    check = verdict.get("tier_check")
    diff_only = isinstance(check, dict) and check.get("input") == "diff-only"
    ranks = [
        tier_rank(verdict.get("tier")),
        tier_rank(check.get("tier") if diff_only else None),
    ]
    if verdict.get("needs_person") is not False or tight:
        ranks.append(1)
    return TIERS[max(ranks)]


def human_approvers(reviews: list, head: str, author: str) -> set:
    """Logins, lower case, that approved this head and are neither a bot nor the author."""
    return {
        lower_login(r)
        for r in reviews
        if r.get("state") == "APPROVED"
        and (r.get("commit") or {}).get("oid") == head
        and (r.get("author") or {}).get("__typename") != "Bot"
        and not ((r.get("author") or {}).get("login") or "").endswith("[bot]")
        and lower_login(r) not in ("", author.lower())
    }


def approved_on_head(reviews: list, head: str, by: str | None = None) -> bool:
    return any(
        r.get("state") == "APPROVED"
        and (r.get("commit") or {}).get("oid") == head
        and (by is None or bot_login((r.get("author") or {}).get("login")) == by)
        for r in reviews
    )


def check_states(contexts: list, required: list, deferred: bool = False) -> dict:
    """State of each required check on the head: success, failure, pending or
    missing, and with `deferred` (members only) also deferred.

    Only runs from the required app count. Skipped runs are ignored and the
    newest other run decides, so a newer failure overrules an older success.
    With `deferred`, a check's deferred name counts as that check, and the
    newest run of the two names decides.
    """
    states = {}
    for req in required:
        names = {req["context"]}
        if deferred and req["context"] in DEFERRED_CHECKS:
            names.add(DEFERRED_CHECKS[req["context"]])
        runs = [
            c
            for c in contexts
            if c.get("__typename") == "CheckRun"
            and c.get("name") in names
            and ((c.get("checkSuite") or {}).get("app") or {}).get("databaseId")
            == req["app_id"]
            and c.get("conclusion") != "SKIPPED"
        ]
        if not runs:
            states[req["context"]] = "missing"
            continue
        # A queued re-run has no start time yet; it is the newest run.
        newest = max(runs, key=lambda c: c.get("startedAt") or "9999")
        if newest.get("status") != "COMPLETED":
            states[req["context"]] = "pending"
        elif newest.get("conclusion") != "SUCCESS":
            states[req["context"]] = "failure"
        else:
            states[req["context"]] = (
                "success" if newest.get("name") == req["context"] else "deferred"
            )
    return states


def red_sha(node: dict, number: int) -> str | None:
    """The head on which the train last saw this pull request red alone."""
    sha = None
    for comment in (node.get("comments") or {}).get("nodes", []):
        if not by_actions_bot(comment):
            continue
        for match in RED_MARKER.finditer(comment.get("body") or ""):
            if int(match.group(1)) == number:
                sha = match.group(2)
    return sha


def head_contexts(node: dict) -> list:
    """The head's runs of the required checks, attached by GitHub.attach_checks."""
    return node.get("_check_runs") or []


def member_view(node: dict, required: list) -> dict:
    """The fields the train judges, from one GraphQL pull request node."""
    head = node["headRefOid"]
    files = (node.get("files") or {}).get("nodes", [])
    reviews = (node.get("reviews") or {}).get("nodes", [])
    return {
        "number": node["number"],
        "title": node.get("title") or "",
        "author": bot_login((node.get("author") or {}).get("login")),
        "head_sha": head,
        "branch": node.get("headRefName") or "",
        "base": node.get("baseRefName"),
        "draft": bool(node.get("isDraft")),
        "fork": bool(node.get("isCrossRepository")),
        "labels": labels_of(node),
        "mergeable": node.get("mergeable"),
        "auto_merge": node.get("autoMergeRequest") is not None,
        "in_queue": bool(node.get("isInMergeQueue")),
        "review_decision": node.get("reviewDecision"),
        "approved_on_head": approved_on_head(reviews, head),
        "human_approvers": human_approvers(
            reviews, head, bot_login((node.get("author") or {}).get("login"))
        ),
        # Reviewer and approver -> repository permission, from
        # GitHub.attach_reviewer_permissions and attach_permissions.
        "permissions": node.get("_permissions") or {},
        "verdict": latest_verdict(
            reviews,
            bot_login((node.get("author") or {}).get("login")),
            assigned_reviewer(node),
            node.get("_permissions") or {},
        ),
        "checks": check_states(head_contexts(node), required, deferred=True),
        "files": [f["path"] for f in files],
        "files_complete": len(files) >= (node.get("changedFiles") or 0),
        "migration_apps": migration_apps(files),
        "red_sha": red_sha(node, node["number"]),
    }


def exclusion(m: dict, cfg: Config, reserved: dict) -> str | None:
    """Why this admitted pull request cannot ride a batch now, or None."""
    if m["number"] in reserved:
        return f"in rollup #{reserved[m['number']]}"
    if m["draft"]:
        return "draft"
    if m["fork"]:
        return "fork: goes alone"
    if m["base"] != BASE:
        return f"base is {m['base']}"
    for label in HIGH_LABELS:
        if label in m["labels"]:
            return f"high tier (label {label}): goes alone"
    if RED_LABEL in m["labels"] and m["red_sha"] in (None, m["head_sha"]):
        return "red alone on this head"
    if m["title"].startswith('Revert "'):
        return "revert: goes alone"
    if m["in_queue"]:
        return "already in the merge queue"
    if m["mergeable"] == "CONFLICTING":
        return "conflicts with dev"
    if not m["files_complete"]:
        return "too many files to classify: goes alone"
    hit = high_path(m["files"], cfg.high_globs)
    if hit:
        return f"high-risk path ({hit}): goes alone"
    owned = code_owned(m["files"], cfg.codeowners)
    if owned:
        return f"code-owned path, goes alone ({owned})"
    if not approved(m):
        return "not approved on this head"
    tier = member_tier(m, cfg)
    if tier is None:
        return "no review verdict on this head"
    if tier == "high":
        return "review tier high: goes alone"
    if m["verdict"] is not None and m["verdict"].get("verdict") != "pass":
        return f"review verdict {m['verdict'].get('verdict')}"
    if tier == "medium" and not writer_approved(m):
        return (
            "medium tier: waits for an approval on this head by a person"
            " with write access who is not the author"
        )
    # Last, and the only rules that cost API calls (see needs_checks).
    bad = {name: state for name, state in m["checks"].items() if state not in GREEN}
    if bad:
        return CHECKS_NOT_GREEN + ", ".join(f"{k}={v}" for k, v in sorted(bad.items()))
    if m["auto_merge"] and "deferred" not in m["checks"].values():
        # A full head with auto-merge: GitHub enqueues it alone any moment.
        return "auto-merge is on and the head ran the full plan: GitHub lands it alone"
    return None


def approved(m: dict) -> bool:
    return m["review_decision"] == "APPROVED" and m["approved_on_head"]


def waits_on_checks(m: dict, reason: str | None) -> bool:
    """Excluded only because required checks are still running: work in
    flight, so the loop stays awake. A failed or missing check is not."""
    bad = [state for state in m["checks"].values() if state not in GREEN]
    return bool(
        reason
        and reason.startswith(CHECKS_NOT_GREEN)
        and bad
        and all(state == "pending" for state in bad)
    )


def wants_full_run(m: dict, reason: str | None) -> bool:
    """A member that goes alone on a deferred head needs its full run."""
    return bool(
        reason
        and "goes alone" in reason
        and not m["fork"]
        and not m["draft"]
        and not m["in_queue"]
        and m["approved_on_head"]
        and "deferred" in m["checks"].values()
    )


def member_tier(m: dict, cfg: Config) -> str | None:
    """Final tier on this head, or None when a verdict is required and missing.
    With require_verdict off, no verdict gets the medium rule."""
    verdict = m["verdict"]
    if verdict is not None and verdict.get("head") == m["head_sha"]:
        return final_tier(verdict, cfg.tight)
    return None if cfg.require_verdict else "medium"


def writer_approved(m: dict) -> bool:
    return any(m["permissions"].get(login) in WRITE for login in m["human_approvers"])


def needs_checks(node: dict, required: list, cfg: Config) -> bool:
    """Whether this admitted pull request's checks decide anything: only if
    every other rule passes. Saves the REST calls for the rest."""
    m = member_view(node, required)
    # The most permissive checks: deferred lets auto-merge and go-alone through.
    m["checks"] = {r["context"]: "deferred" for r in required}
    reason = exclusion(m, cfg, {})
    return reason is None or wants_full_run(m, reason)


# --- Batching ----------------------------------------------------------------


def round_robin(members: list) -> list:
    """Oldest first within an author; authors take turns, oldest PR first."""
    by_author: dict = {}
    for m in sorted(members, key=lambda m: m["number"]):
        by_author.setdefault(m["author"], []).append(m)
    queues = sorted(by_author.values(), key=lambda q: q[0]["number"])
    out = []
    while queues:
        out.extend(q.pop(0) for q in queues)
        queues = [q for q in queues if q]
    return out


def train_slots(cfg: Config, open_rollups: int, queue_entries: int) -> int:
    return max(0, min(cfg.max_rollups - open_rollups, cfg.queue_builds - queue_entries))


def plan_batches(eligible: list, slots: int, max_batch: int) -> list:
    """Fill up to `slots` batches of size B in order; a member whose migration
    app is already in a batch waits for a later one (or the next tick)."""
    if slots <= 0 or not eligible:
        return []
    size = min(max_batch, max(1, math.ceil(len(eligible) / slots)))
    batches: list = []
    waiting = list(eligible)
    while waiting and len(batches) < slots:
        batch, apps, rest = [], set(), []
        for m in waiting:
            mine = m.get("migration_apps") or set()
            if len(batch) < size and not (apps & mine):
                batch.append(m)
                apps |= mine
            else:
                rest.append(m)
        batches.append(batch)
        waiting = rest
    return batches


# --- The rollup manifest -----------------------------------------------------


def new_manifest(base_sha: str, members: list, parent: str | None = None) -> dict:
    ident = (
        time.strftime("%Y%m%d%H%M%S", time.gmtime())
        + "-"
        + "-".join(str(m["number"]) for m in members)
    )
    return {
        "schema": SCHEMA,
        "id": ident,
        "base_sha": base_sha,
        "head_sha": None,
        "parent": parent,
        "infra_retries": 0,
        "removals_seen": 0,
        "members": [
            {
                "pr": m["number"],
                "head_sha": m["head_sha"],
                "branch": m["branch"],
                "title": m["title"],
            }
            for m in members
        ],
    }


def rollup_title(manifest: dict) -> str:
    return "chore(train): land " + ", ".join(f"#{m['pr']}" for m in manifest["members"])


def render_manifest(manifest: dict) -> str:
    lines = [
        "Batch train rollup. The merge queue tests these pull requests together and lands them in one entry.",
        "Each member head is merged in order with no other change; each member is marked merged when this lands.",
        "",
    ]
    lines += [
        f"- #{m['pr']} at `{m['head_sha'][:12]}`: {m['title']}"
        for m in manifest["members"]
    ]
    lines += [
        "",
        "Docs: none (batch train rollup; each member pull request declares its own docs)",
    ]
    lines += [
        "",
        MANIFEST_MARKER,
        "```json",
        json.dumps(manifest, indent=1, sort_keys=True),
        "```",
    ]
    return "\n".join(lines)


def parse_manifest(body: str) -> dict:
    if MANIFEST_MARKER not in (body or ""):
        raise ValueError("no train manifest marker")
    match = JSON_BLOCK.search(body.split(MANIFEST_MARKER, 1)[1])
    if not match:
        raise ValueError("no json block after the manifest marker")
    manifest = json.loads(match.group(1))
    if manifest.get("schema") != SCHEMA or not manifest.get("members"):
        raise ValueError("not a devflow-train/v1 manifest with members")
    return manifest


def merge_message(member: dict, owner: str) -> str:
    """The message GitHub writes for a merge commit, so release notes read the same."""
    return f"Merge pull request #{member['pr']} from {owner}/{member['branch']}\n\n{member['title']}"


# --- Red rollups ---------------------------------------------------------------


def removal_kind(reason: str | None) -> str:
    """Why the queue removed an entry: conflict, manual or red (everything else)."""
    text = (reason or "").lower()
    if "conflict" in text:
        return "conflict"
    if "manual" in text or "dequeued" in text:
        return "manual"
    return "red"


def parse_signatures(text: str | None) -> list:
    """[(name, class, regex)] from the shared file. Its regexes are POSIX ERE
    read by grep -Ei, so translate the bracket classes, ignore case, and match
    within one line. Only the train reads class "apt": its regexes may span
    lines (a download line, then the timeout's exit code)."""
    if text is None:
        return []
    out = []
    for entry in json.loads(text):
        regex = entry["regex"]
        for posix, python in POSIX_CLASSES.items():
            regex = regex.replace(posix, python)
        if re.search(r"\[:[a-z]+:\]", regex):
            raise ValueError(
                f"{SIGNATURES_FILE}: {entry['name']} uses an untranslated POSIX class"
            )
        flags = re.IGNORECASE | (re.DOTALL if entry["class"] == "apt" else re.MULTILINE)
        out.append((entry["name"], entry["class"], re.compile(regex, flags)))
    return out


# A job log opens each step with a group that echoes the step's script and
# env ("##[group]Run <first line>" ... "##[endgroup]"). That is the
# workflow's source, not what ran: a script that names a signature (e2e-ci's
# image prefetch lists "toomanyrequests") must not make a real failure look
# like infra. Other groups (checkout's fetch, a tool's own) are output: kept.
LOG_LINE_PREFIX = re.compile(r"^\ufeff?(?:\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z )?")


def step_output(text: str) -> str:
    """A job log without the echoed step scripts."""
    out, echo = [], False
    for line in text.splitlines():
        body = LOG_LINE_PREFIX.sub("", line, count=1)
        if body.startswith("##[group]Run "):
            echo = True
        elif echo:
            echo = not body.startswith("##[endgroup]")
        else:
            out.append(line)
    return "\n".join(out)


def classify_failure(logs: list, signatures: list) -> str:
    """'infra' only if there are failed jobs and every one is infra: it matches
    rate_limit, or an infra class and no permanent entry (a 429 also matches
    the permanent 4xx rule, so rate_limit wins)."""
    if not logs:
        return "red"
    for log in logs:
        classes = {cls for _, cls, rx in signatures if rx.search(log)}
        if "rate_limit" in classes:
            continue
        if not (classes & INFRA_CLASSES) or "permanent" in classes:
            return "red"
    return "infra"


def on_red(manifest: dict, failure: str) -> dict:
    """Retry an infra failure once; otherwise split; a red single member leaves."""
    members = manifest["members"]
    if failure == "infra" and manifest.get("infra_retries", 0) < 1:
        return {"action": "retry"}
    if len(members) == 1:
        return {
            "action": "evict",
            "pr": members[0]["pr"],
            "sha": members[0]["head_sha"],
        }
    mid = (len(members) + 1) // 2
    return {
        "action": "split",
        "halves": [[m["pr"] for m in members[:mid]], [m["pr"] for m in members[mid:]]],
    }


def red_signal(rollup: dict) -> str | None:
    """Where a rollup went red: 'queue' (removed), 'checks' (its own PR run) or None."""
    if rollup["in_queue"]:
        return None
    if rollup["removals"] > rollup["manifest"].get("removals_seen", 0):
        if removal_kind(rollup["last_removal_reason"]) == "red":
            return "queue"
        return None
    if "failure" in rollup["checks"].values():
        return "checks"
    return None


def stale_members(manifest: dict, live: dict) -> list:
    """Members whose pull request moved since the rollup was built."""
    stale = []
    for member in manifest["members"]:
        m = live.get(member["pr"])
        if (
            m is None
            or m["head_sha"] != member["head_sha"]
            # Dismissed, or changes requested since the build.
            or not approved(m)
            or ADMIT_LABEL not in m["labels"]
            or any(label in m["labels"] for label in HIGH_LABELS)
            # Enqueued on its own (a person, or auto-merge on a full head).
            or m["in_queue"]
        ):
            stale.append(member["pr"])
    return stale


def rollup_action(
    rollup: dict,
    live: dict,
    failure: str | None,
    paused: bool = False,
    dev_red: list | None = None,
) -> dict:
    """What one tick does with one open rollup pull request.

    While the base is red (paused), a red rollup is closed, not split and
    not evicted: its red says nothing about its members, which re-batch
    once dev is fixed. `dev_red` is the rollup's failed (or held) jobs that
    dev's newest finished push run also fails: a red single member is held,
    not evicted, until dev passes them, and then its rollup is rebuilt."""
    manifest = rollup["manifest"]
    stale = stale_members(manifest, live)
    if stale:
        return {"action": "rebuild", "drop": stale}
    if not rollup["approved_by_actions"] or rollup["head_sha"] != manifest.get(
        "head_sha"
    ):
        # The train approves a rollup once, right after it builds it. A
        # missing approval means someone pushed to it: never re-approve.
        return {
            "action": "rebuild",
            "drop": [],
            "why": "rollup head is not the one the train built",
        }
    if rollup["in_queue"]:
        return {"action": "wait"}
    if rollup["removals"] > manifest.get("removals_seen", 0):
        kind = removal_kind(rollup["last_removal_reason"])
        if kind == "conflict":
            return {"action": "rebuild", "drop": []}
        if kind == "manual":
            return {"action": "release"}
    signal = red_signal(rollup)
    held = manifest.get("held")
    if paused and (signal or held):
        return {"action": "close-red", "signal": signal or "held"}
    if held:
        # Never ensure: auto-merge stays as the red left it.
        if dev_red:
            return {"action": "hold", "jobs": held}
        return {"action": "rebuild", "drop": [], "why": "dev passes the held jobs"}
    if signal:
        act = on_red(manifest, failure or "red")
        if act["action"] == "evict" and dev_red:
            return {"action": "hold", "signal": signal, "jobs": dev_red, "new": True}
        act["signal"] = signal
        return act
    return {"action": "ensure"}


def rollup_view(node: dict, cfg: Config, required: list) -> dict | None:
    """An open rollup pull request, or None if it is not the train's own."""
    if bot_login((node.get("author") or {}).get("login")) != cfg.train_login:
        return None
    if not (node.get("headRefName") or "").startswith(BRANCH_PREFIX) or node.get(
        "isCrossRepository"
    ):
        return None
    try:
        manifest = parse_manifest(node.get("body") or "")
    except ValueError:
        return None
    events = (node.get("timelineItems") or {}).get("nodes", [])
    removals = [
        e for e in events if e.get("__typename") == "RemovedFromMergeQueueEvent"
    ]
    head = node["headRefOid"]
    return {
        "number": node["number"],
        "id": node.get("id"),
        "head_sha": head,
        "branch": node["headRefName"],
        "in_queue": bool(node.get("isInMergeQueue")),
        "auto_merge": node.get("autoMergeRequest") is not None,
        "approved_by_actions": approved_on_head(
            (node.get("reviews") or {}).get("nodes", []), head, ACTIONS_LOGIN
        ),
        "checks": check_states(head_contexts(node), required),
        "removals": len(removals),
        "last_removal_reason": removals[-1].get("reason") if removals else None,
        "manifest": manifest,
    }


def plan(
    data: dict,
    required: list,
    cfg: Config,
    failures: dict | None = None,
    dev_red: dict | None = None,
) -> dict:
    """The whole tick as data. Pure: tests feed it built payloads.

    `failures` maps a red rollup's number to 'infra' or 'red' (from its
    failed job logs); a red rollup missing from it counts as 'red'.
    `dev_red` maps a rollup's number to its jobs that dev's newest finished
    push run also fails (see rollup_action).
    """
    failures, dev_red = failures or {}, dev_red or {}
    repo = data["repository"]
    paused = list(data.get("base_red") or [])
    queue = [
        e["pullRequest"]["number"]
        for e in ((repo.get("mergeQueue") or {}).get("entries") or {}).get("nodes", [])
    ]
    admitted = [member_view(n, required) for n in repo["admitted"]["nodes"]]
    live = {m["number"]: m for m in admitted}
    rollups = [
        r
        for r in (rollup_view(n, cfg, required) for n in repo["rollups"]["nodes"])
        if r
    ]

    actions, reserved, evicted, held = [], {}, {}, {}
    for rollup in rollups:
        number = rollup["number"]
        act = rollup_action(
            rollup, live, failures.get(number), bool(paused), dev_red.get(number)
        )
        act["rollup"] = number
        actions.append(act)
        if act["action"] in ("wait", "ensure", "retry", "split", "hold"):
            for member in rollup["manifest"]["members"]:
                reserved[member["pr"]] = number
                if act["action"] == "hold":
                    held[member["pr"]] = number
        elif act["action"] == "evict":
            evicted[act["pr"]] = act["sha"]
    still_open = sum(
        1 for a in actions if a["action"] in ("wait", "ensure", "retry", "hold")
    )
    new_from_splits = sum(len(a["halves"]) for a in actions if a["action"] == "split")

    labels, excluded, eligible, full_runs = [], {}, [], []
    waiting = 0
    for m in admitted:
        if m["number"] in evicted:
            # This tick labels it red; it must not ride the next batch.
            m["labels"] = m["labels"] | {RED_LABEL}
            m["red_sha"] = evicted[m["number"]]
        elif RED_LABEL in m["labels"] and m["red_sha"] not in (None, m["head_sha"]):
            labels.append(
                {"pr": m["number"], "remove": RED_LABEL, "why": "new head since red"}
            )
            m["labels"] = m["labels"] - {RED_LABEL}
        reason = exclusion(m, cfg, reserved)
        if m["number"] in held:
            reason = f"held: base red (rollup #{held[m['number']]})"
        if reason:
            excluded[m["number"]] = reason
            waiting += waits_on_checks(m, reason)
            if wants_full_run(m, reason):
                full_runs.append(
                    {
                        "pr": m["number"],
                        "head_sha": m["head_sha"],
                        "labelled": FULL_LABEL in m["labels"],
                    }
                )
        else:
            eligible.append(m)
    ordered = round_robin(eligible)
    slots = train_slots(cfg, still_open + new_from_splits, len(queue))
    batches = [] if paused else plan_batches(ordered, slots, cfg.max_batch)
    return {
        "paused_by": paused,
        "queue_entries": len(queue),
        "rollup_actions": actions,
        "label_changes": labels,
        "excluded": excluded,
        "eligible": [m["number"] for m in ordered],
        "full_runs": full_runs,
        "train_slots": slots,
        "batches": [[m["number"] for m in b] for b in batches],
        # Eligible work waiting (paused, or no free slot) keeps the loop
        # awake: closing a base-red issue is not a push to dev. So does a
        # member whose checks are still running: it is eligible when they end.
        "busy": bool(still_open or new_from_splits or batches or ordered or waiting),
        "_members": live,
        "_rollups": {r["number"]: r for r in rollups},
    }


def tree_parity(candidate: dict, landed: dict) -> bool:
    """A landing is valid only if dev got exactly the tree the queue tested."""
    tree = (candidate.get("tree") or {}).get("sha")
    return bool(tree) and tree == (landed.get("tree") or {}).get("sha")


# --- GitHub ------------------------------------------------------------------

PR_FIELDS = """
  id number title body isDraft isCrossRepository baseRefName headRefName headRefOid
  author { login } mergeable isInMergeQueue reviewDecision changedFiles
  autoMergeRequest { enabledAt }
  labels(first: 50) { nodes { name } }
  files(first: 100) { nodes { path changeType } }
  reviews(last: 50) { nodes { state authorAssociation submittedAt body commit { oid } author { __typename login } } }
  comments(last: 50) { nodes { body author { __typename login } } }
  timelineItems(last: 20, itemTypes: [ADDED_TO_MERGE_QUEUE_EVENT, REMOVED_FROM_MERGE_QUEUE_EVENT]) {
    nodes { __typename ... on RemovedFromMergeQueueEvent { reason createdAt } }
  }
"""


PAGE_SIZE = 50
MAX_PAGES = 40  # 2,000 labelled pull requests; past that something is wrong


def build_query(label: str | None = None) -> str:
    """One page of open pull requests into dev with this label, or (no label)
    the merge queue's entries.

    Check runs are not read here: a CI run on one head has over 100 check
    runs and the aggregates finish last, so a head's first 100 contexts miss
    them, and filtered lookups per pull request exceed GraphQL's resource
    limits. GitHub.attach_checks reads them by name over REST."""
    head = "query($owner: String!, $name: String!) { repository(owner: $owner, name: $name) { "
    if label is None:
        return head + (
            f'mergeQueue(branch: "{BASE}") {{ entries(first: 100) {{ nodes {{ pullRequest {{ number }} }} }} }} }} }}'
        )
    # GraphQL rejects a declared variable that the query does not use.
    head = head.replace("$name: String!)", "$name: String!, $after: String)")
    return head + (
        f'pullRequests(states: OPEN, baseRefName: "{BASE}", labels: ["{label}"], '
        f"first: {PAGE_SIZE}, after: $after) {{ pageInfo {{ hasNextPage endCursor }} "
        f"nodes {{ {PR_FIELDS} }} }} }} }}"
    )


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class GitHub:
    def __init__(self, token: str, repo: str):
        self.token, self.repo = token, repo
        self.permissions: dict = {}
        self.calls = 0
        # Rate limit per resource (core, graphql, ...) from the last response.
        self.limits: dict = {}
        self.check_cache: dict = {}  # head sha -> (monotonic time, runs)

    def request(self, method: str, path: str, body=None, raw: bool = False):
        """Returns (status, parsed JSON or text). Raises on any 4xx/5xx it is not told to expect."""
        url = (
            path
            if path.startswith("https://")
            else f"https://api.github.com/{path.lstrip('/')}"
        )
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "devflow-train",
            },
        )
        opener = urllib.request.build_opener(NoRedirect)
        self.calls += 1
        try:
            with opener.open(req, timeout=60) as resp:
                status, text = resp.status, resp.read().decode(errors="replace")
                self.note_limits(resp.headers)
        except urllib.error.HTTPError as err:
            status, text = err.code, err.read().decode(errors="replace")
            self.note_limits(err.headers)
            if status == 302 and raw:
                # Job logs redirect to a signed URL that must not get our token.
                with urllib.request.urlopen(
                    err.headers["Location"], timeout=60
                ) as resp:
                    return resp.status, resp.read().decode(errors="replace")
        if raw:
            return status, text
        try:
            return status, (json.loads(text) if text.strip() else None)
        except ValueError:  # a gateway error page; call() reports the status
            return status, text[:300]

    def note_limits(self, headers):
        remaining = headers.get("x-ratelimit-remaining") if headers else None
        if remaining is not None:
            self.limits[headers.get("x-ratelimit-resource") or "core"] = {
                "remaining": int(remaining),
                "reset": int(headers.get("x-ratelimit-reset") or 0),
            }

    def call(self, method: str, path: str, body=None, ok=(200, 201, 204)):
        status, out = self.request(method, path, body)
        if status not in ok:
            raise RuntimeError(f"{method} {path}: {status} {str(out)[:500]}")
        return out

    def graphql(self, query: str, **variables):
        out = self.call("POST", "graphql", {"query": query, "variables": variables})
        if out.get("errors"):
            raise RuntimeError(f"GraphQL errors: {out['errors']}")
        return out["data"]

    def required_checks(self) -> list:
        """Required checks on dev, readable with a read-only token."""
        branch = self.call("GET", f"repos/{self.repo}/branches/{BASE}")
        checks = (
            (branch.get("protection") or {}).get("required_status_checks") or {}
        ).get("checks") or []
        if not checks:
            raise RuntimeError(
                "dev reports no required checks; refusing to judge without them"
            )
        return checks

    def file_text(self, path: str) -> str | None:
        """A file on dev, or None if dev does not have it."""
        status, out = self.request(
            "GET", f"repos/{self.repo}/contents/{urllib.parse.quote(path)}?ref={BASE}"
        )
        if status == 404:
            return None
        if status != 200:
            raise RuntimeError(f"reading {path}: {status}")
        return base64.b64decode(out["content"]).decode()

    def base_red(self) -> list:
        """Open issues (not pull requests) with the base-red label."""
        out = self.call(
            "GET",
            f"repos/{self.repo}/issues?labels={BASE_RED_LABEL}&state=open&per_page=100",
        )
        return sorted(i["number"] for i in out if "pull_request" not in i)

    def gather(self) -> dict:
        """The queue and every admitted and rollup pull request, all pages."""
        owner, name = self.repo.split("/")
        repo = {
            "mergeQueue": self.graphql(build_query(), owner=owner, name=name)[
                "repository"
            ]["mergeQueue"]
        }
        for key, label in (("admitted", ADMIT_LABEL), ("rollups", ROLLUP_LABEL)):
            nodes, after = [], None
            for _ in range(MAX_PAGES):
                page = self.graphql(
                    build_query(label), owner=owner, name=name, after=after
                )["repository"]["pullRequests"]
                nodes += page["nodes"]
                if not page["pageInfo"]["hasNextPage"]:
                    break
                after = page["pageInfo"]["endCursor"]
            else:
                raise RuntimeError(f"more than {MAX_PAGES} pages of {label}")
            repo[key] = {"nodes": nodes}
        return {"repository": repo}

    def check_runs(self, sha: str, required: list, deferred: bool = False) -> list:
        """Every run of each required check on one commit, by name and app,
        and with `deferred` (members) of each check's deferred name too."""
        runs = []
        names = [(r["context"], r["app_id"]) for r in required]
        if deferred:
            names += [
                (DEFERRED_CHECKS[r["context"]], r["app_id"])
                for r in required
                if r["context"] in DEFERRED_CHECKS
            ]
        for context, app_id in names:
            name = urllib.parse.quote(context)
            out = self.call(
                "GET",
                f"repos/{self.repo}/commits/{sha}/check-runs?check_name={name}&app_id={app_id}&filter=all&per_page=50",
            )
            for r in out["check_runs"]:
                runs.append(
                    {
                        "__typename": "CheckRun",
                        "name": r["name"],
                        "status": (r.get("status") or "").upper(),
                        "conclusion": (r.get("conclusion") or "").upper() or None,
                        "startedAt": r.get("started_at"),
                        "checkSuite": {
                            "app": {"databaseId": (r.get("app") or {}).get("id")}
                        },
                    }
                )
        return runs

    def permission(self, login: str, now=time.monotonic) -> str:
        """A login's repository permission (admin, write, read or none), read
        again after CHECK_CACHE_SECONDS."""
        hit = self.permissions.get(login)
        if hit and now() - hit[0] < CHECK_CACHE_SECONDS:
            return hit[1]
        status, out = self.request(
            "GET",
            f"repos/{self.repo}/collaborators/{urllib.parse.quote(login)}/permission",
        )
        if status == 404:
            value = "none"
        elif status == 200:
            value = out.get("permission") or "none"
        else:
            raise RuntimeError(f"permission of {login}: {status} {str(out)[:300]}")
        self.permissions[login] = (now(), value)
        return value

    def attach_reviewer_permissions(self, data: dict):
        """Permissions of every devflow-review/v1 reviewer who is not the
        author: a verdict counts only from someone with write access."""
        for node in data["repository"]["admitted"]["nodes"]:
            author = bot_login((node.get("author") or {}).get("login")).lower()
            logins = {
                lower_login(r)
                for r in (node.get("reviews") or {}).get("nodes", [])
                if VERDICT_MARKER in (r.get("body") or "")
            } - {"", author}
            node["_permissions"] = {
                login: self.permission(login) for login in sorted(logins)
            }

    def attach_permissions(self, data: dict, required: list, cfg: Config):
        """Approvers' permissions, only where the tier is medium."""
        for node in data["repository"]["admitted"]["nodes"]:
            m = member_view(node, required)
            if m["human_approvers"] and member_tier(m, cfg) == "medium":
                node.setdefault("_permissions", {}).update(
                    {
                        login: self.permission(login)
                        for login in sorted(m["human_approvers"])
                    }
                )

    def member_check_runs(self, sha: str, required: list, now=time.monotonic) -> list:
        """A member head's runs, from the cache while they are all green."""
        hit = self.check_cache.get(sha)
        if hit and now() - hit[0] < CHECK_CACHE_SECONDS:
            return hit[1]
        runs = self.check_runs(sha, required, deferred=True)
        if all(v in GREEN for v in check_states(runs, required, True).values()):
            self.check_cache[sha] = (now(), runs)
        else:
            self.check_cache.pop(sha, None)
        return runs

    def forget(self, sha: str):
        """Drop a head from the cache: the train just re-ran its checks."""
        self.check_cache.pop(sha, None)

    def attach_checks(self, data: dict, required: list, cfg: Config):
        """Read checks for every rollup (always fresh: a red one may be
        re-run), and for each admitted pull request that every other rule
        lets through."""
        repo = data["repository"]
        for node in repo["rollups"]["nodes"]:
            node["_check_runs"] = self.check_runs(node["headRefOid"], required)
        heads = set()
        for node in repo["admitted"]["nodes"]:
            heads.add(node["headRefOid"])
            if needs_checks(node, required, cfg):
                node["_check_runs"] = self.member_check_runs(
                    node["headRefOid"], required
                )
        for sha in set(self.check_cache) - heads:
            del self.check_cache[sha]

    def failed_runs(self, rollup: dict, signal: str) -> list:
        """The workflow runs whose failure turned this rollup red."""
        if signal == "checks":
            runs = self.call(
                "GET",
                f"repos/{self.repo}/actions/runs?event=pull_request&head_sha={rollup['head_sha']}&per_page=50",
            )
        else:
            runs = self.call(
                "GET", f"repos/{self.repo}/actions/runs?event=merge_group&per_page=100"
            )
            prefix = f"gh-readonly-queue/{BASE}/pr-{rollup['number']}-"
            runs = {
                "workflow_runs": [
                    r
                    for r in runs["workflow_runs"]
                    if (r.get("head_branch") or "").startswith(prefix)
                ]
            }
            newest = max((r["created_at"] for r in runs["workflow_runs"]), default=None)
            if newest:
                # Only the latest queue attempt: one head_sha, the newest group.
                sha = next(
                    r["head_sha"]
                    for r in runs["workflow_runs"]
                    if r["created_at"] == newest
                )
                runs["workflow_runs"] = [
                    r for r in runs["workflow_runs"] if r["head_sha"] == sha
                ]
        return [r for r in runs["workflow_runs"] if r.get("conclusion") == "failure"]

    def failed_jobs(self, runs: list, required: list) -> list:
        """[{id, name, workflow}] of the failed jobs, leaving out the required
        aggregates.

        Only runs that produce a required check count: another workflow's
        failure does not block the rollup, so it must not decide the retry.
        """
        aggregates = {r["context"] for r in required}
        out = []
        for run in runs:
            jobs = self.call(
                "GET",
                f"repos/{self.repo}/actions/runs/{run['id']}/jobs?filter=latest&per_page=100",
            )
            if not any(
                job["name"].split(" / ")[-1] in aggregates for job in jobs["jobs"]
            ):
                continue
            workflow = os.path.basename(run.get("path") or "")
            out += [
                {"id": job["id"], "name": job["name"], "workflow": workflow}
                for job in jobs["jobs"]
                if job.get("conclusion") == "failure"
                and job["name"].split(" / ")[-1] not in aggregates
            ]
        return out

    def job_logs(self, jobs: list) -> list:
        """Each job's log tail, without the echoed step scripts."""
        logs = []
        for job in jobs:
            status, text = self.request(
                "GET", f"repos/{self.repo}/actions/jobs/{job['id']}/logs", raw=True
            )
            logs.append(
                "\n".join(step_output(text).splitlines()[-LOG_TAIL_LINES:])
                if status == 200
                else ""
            )
        return logs

    def failed_job_logs(self, runs: list, required: list) -> list:
        """Log tails of the failed jobs, leaving out the required aggregates."""
        return self.job_logs(self.failed_jobs(runs, required))

    def dev_red_jobs(self, workflow: str, required: list) -> set:
        """The jobs that failed on dev's newest finished (passed or failed)
        push run of `workflow`'s suite, named as `workflow` names them.
        Empty when that run passed or dev has no finished run."""
        twin, prefix = DEV_TWINS.get(workflow, (workflow, ""))
        for page in range(1, DEV_PUSH_PAGES + 1):
            batch = (
                self.call(
                    "GET",
                    f"repos/{self.repo}/actions/workflows/{twin}/runs?event=push"
                    f"&branch={BASE}&status=completed&per_page=20&page={page}",
                ).get("workflow_runs")
                or []
            )
            done = [r for r in batch if r.get("conclusion") in ("success", "failure")]
            if done:
                newest = done[0]  # the listing is newest first
                if newest["conclusion"] == "success":
                    return set()
                return {
                    prefix + job["name"] for job in self.failed_jobs([newest], required)
                }
            if len(batch) < 20:
                break
        return set()


class Live:
    """The writes one tick makes. Only constructed in live mode."""

    def __init__(self, repo: str, app: GitHub, actions: GitHub):
        self.repo, self.app, self.actions = repo, app, actions
        self.owner = repo.split("/")[0]

    def open_rollup(self, members: list, parent: str | None = None) -> dict:
        """Branch from the dev tip, merge each member head server-side, open and approve the rollup."""
        base_sha = self.app.call("GET", f"repos/{self.repo}/branches/{BASE}")["commit"][
            "sha"
        ]
        manifest = new_manifest(base_sha, members, parent)
        branch = BRANCH_PREFIX + manifest["id"]
        self.app.call(
            "POST",
            f"repos/{self.repo}/git/refs",
            {"ref": f"refs/heads/{branch}", "sha": base_sha},
        )
        head, included, conflicts = base_sha, [], []
        for member in manifest["members"]:
            status, out = self.app.request(
                "POST",
                f"repos/{self.repo}/merges",
                {
                    "base": branch,
                    "head": member["head_sha"],
                    "commit_message": merge_message(member, self.owner),
                },
            )
            if status == 201:
                head = out["sha"]
                included.append(member)
            elif status == 409:
                conflicts.append(member["pr"])
            elif status != 204:  # 204: already in dev, so nothing to land
                raise RuntimeError(
                    f"merging #{member['pr']} into {branch}: {status} {str(out)[:300]}"
                )
        for pr in conflicts:
            self.label(pr, CONFLICT_LABEL)
        if not included:
            self.app.call("DELETE", f"repos/{self.repo}/git/refs/heads/{branch}")
            return {"opened": None, "conflicts": conflicts}
        manifest["members"], manifest["head_sha"] = included, head
        pr = self.app.call(
            "POST",
            f"repos/{self.repo}/pulls",
            {
                "title": rollup_title(manifest),
                "head": branch,
                "base": BASE,
                "body": render_manifest(manifest),
            },
        )
        self.app.call(
            "POST",
            f"repos/{self.repo}/issues/{pr['number']}/labels",
            {"labels": [ROLLUP_LABEL]},
        )
        if pr["head"]["sha"] != head:
            self.close(
                {
                    "number": pr["number"],
                    "id": pr["node_id"],
                    "in_queue": False,
                    "branch": branch,
                },
                "Closed: the branch moved while the train built it.",
            )
            raise RuntimeError(
                f"rollup #{pr['number']} head is {pr['head']['sha']}, built {head}"
            )
        self.actions.call(
            "POST",
            f"repos/{self.repo}/pulls/{pr['number']}/reviews",
            {
                "commit_id": head,
                "event": "APPROVE",
                "body": "Approved by the batch train. Every member is approved and green on its own head, "
                "and this head is only GitHub's merges of those heads onto dev. The full suite runs on this "
                "pull request and again in the merge queue.",
            },
        )
        self.enable_auto_merge(pr["node_id"])
        for member in included:
            for label in (CONFLICT_LABEL, STALE_LABEL):
                self.unlabel(member["pr"], label)
        return {"opened": pr["number"], "conflicts": conflicts}

    def enable_auto_merge(self, node_id: str):
        self.app.graphql(
            "mutation($id: ID!) { enablePullRequestAutoMerge(input: {pullRequestId: $id, mergeMethod: MERGE}) "
            "{ clientMutationId } }",
            id=node_id,
        )

    def retry(self, rollup: dict, signal: str, runs: list):
        manifest = {
            **rollup["manifest"],
            "infra_retries": rollup["manifest"].get("infra_retries", 0) + 1,
            "removals_seen": rollup["removals"],
        }
        self.app.call(
            "PATCH",
            f"repos/{self.repo}/pulls/{rollup['number']}",
            {"body": render_manifest(manifest)},
        )
        if signal == "checks":
            for run in runs:
                self.actions.call(
                    "POST",
                    f"repos/{self.repo}/actions/runs/{run['id']}/rerun-failed-jobs",
                )
        elif not rollup["auto_merge"]:
            self.enable_auto_merge(rollup["id"])

    def ensure(self, rollup: dict):
        if not rollup["auto_merge"] and not rollup["in_queue"]:
            self.enable_auto_merge(rollup["id"])

    def close(self, rollup: dict, why: str):
        if rollup["in_queue"]:
            self.app.graphql(
                "mutation($id: ID!) { dequeuePullRequest(input: {id: $id}) { clientMutationId } }",
                id=rollup["id"],
            )
        self.actions.call(
            "POST",
            f"repos/{self.repo}/issues/{rollup['number']}/comments",
            {"body": why},
        )
        self.app.call(
            "PATCH", f"repos/{self.repo}/pulls/{rollup['number']}", {"state": "closed"}
        )
        self.app.call(
            "DELETE",
            f"repos/{self.repo}/git/refs/heads/{rollup['branch']}",
            ok=(204, 422),
        )

    def hold(self, rollup: dict, jobs: list):
        """Mark a lone red rollup held in its manifest, once. It stays open,
        and its member is not marked red."""
        self.app.call(
            "PATCH",
            f"repos/{self.repo}/pulls/{rollup['number']}",
            {"body": render_manifest({**rollup["manifest"], "held": jobs})},
        )
        names = ", ".join(f"`{j['job']}` ({j['workflow']})" for j in jobs)
        self.actions.call(
            "POST",
            f"repos/{self.repo}/issues/{rollup['number']}/comments",
            {
                "body": f"Held by the batch train: red on {names}, which dev's newest finished "
                "push run fails too, so the red may be the base's. Nothing is marked red. "
                "The train rebuilds this rollup once dev passes that job."
            },
        )

    def evict(self, pr: int, sha: str, rollup: int, runs: list):
        self.label(pr, RED_LABEL)
        links = ", ".join(r["html_url"] for r in runs if r.get("html_url"))
        self.actions.call(
            "POST",
            f"repos/{self.repo}/issues/{pr}/comments",
            {
                "body": f"The batch train saw this pull request red on its own in rollup #{rollup} "
                f"(head `{sha[:12]}`). It leaves the train until a new head is pushed. Failing run: "
                f"{links or f'see #{rollup}'}.\n\n<!-- devflow-train:red pr={pr} sha={sha} -->"
            },
        )

    def full_run(self, pr: int, head: str, labelled: bool) -> str:
        """ci:full, then one full re-run of the head's light Backend CI run."""
        if not labelled:
            self.label(pr, FULL_LABEL)
        runs = self.actions.call(
            "GET",
            f"repos/{self.repo}/actions/workflows/{BACKEND_WORKFLOW}/runs?event=pull_request&head_sha={head}&per_page=20",
        )["workflow_runs"]
        if not runs:
            return "no Backend CI run on the head"
        newest = max(runs, key=lambda r: r.get("created_at") or "")
        if newest.get("status") != "completed":
            return "Backend CI is running"
        if (newest.get("run_attempt") or 1) > 1:
            return "already re-run once; a person takes it from here"
        self.actions.call(
            "POST", f"repos/{self.repo}/actions/runs/{newest['id']}/rerun"
        )
        self.actions.forget(head)
        return f"re-ran run {newest['id']} with {FULL_LABEL}"

    def label(self, pr: int, name: str):
        self.actions.call(
            "POST", f"repos/{self.repo}/issues/{pr}/labels", {"labels": [name]}
        )

    def unlabel(self, pr: int, name: str):
        self.actions.call(
            "DELETE",
            f"repos/{self.repo}/issues/{pr}/labels/{urllib.parse.quote(name)}",
            ok=(200, 204, 404),
        )


def execute(result: dict, live: Live, red_runs: dict):
    """Carry out a plan. Rollup actions first, so freed members re-batch."""
    members, rollups = result["_members"], result["_rollups"]
    for act in result["rollup_actions"]:
        rollup = rollups[act["rollup"]]
        kind = act["action"]
        if kind == "ensure":
            live.ensure(rollup)
        elif kind == "retry":
            live.retry(rollup, act["signal"], red_runs.get(rollup["number"], []))
        elif kind == "rebuild":
            for pr in act["drop"]:
                if pr in members:
                    live.label(pr, STALE_LABEL)
            live.close(
                rollup,
                f"Closed by the batch train: rebuilding{': ' + act['why'] if act.get('why') else ''}."
                f" Dropped: {act['drop'] or 'none'}.",
            )
        elif kind == "hold" and act.get("new"):
            live.hold(rollup, act["jobs"])
        elif kind == "close-red":
            live.close(
                rollup,
                f"Closed by the batch train: red ({act['signal']}) while the base is red "
                f"({', '.join(f'#{n}' for n in result['paused_by'])}). Nothing is split "
                "or marked red; the members re-batch once dev is fixed.",
            )
        elif kind == "release":
            live.close(
                rollup,
                "Closed by the batch train: removed from the queue by hand. Its members are free.",
            )
        elif kind == "split":
            for half in act["halves"]:
                chosen = [members[pr] for pr in half if pr in members]
                if chosen:
                    live.open_rollup(chosen, parent=rollup["manifest"]["id"])
            live.close(
                rollup,
                f"Closed by the batch train: red ({act['signal']}), split into {act['halves']}.",
            )
        elif kind == "evict":
            live.evict(
                act["pr"],
                act["sha"],
                rollup["number"],
                red_runs.get(rollup["number"], []),
            )
            live.close(
                rollup, f"Closed by the batch train: #{act['pr']} is red on its own."
            )
    for change in result["label_changes"]:
        live.unlabel(change["pr"], change["remove"])
    for full in result["full_runs"]:
        try:
            full["done"] = live.full_run(full["pr"], full["head_sha"], full["labelled"])
        except RuntimeError as err:  # one member's re-run must not stop the batches
            full["done"] = f"failed: {err}"
            print(f"::warning title=train full run #{full['pr']}::{err}", flush=True)
    for batch in result["batches"]:
        live.open_rollup([members[pr] for pr in batch])


def summary(result: dict) -> str:
    lines = [
        f"## Batch train ({result['mode']})",
        f"- Paused by base-red issues: {result['paused_by'] or 'none'}",
        f"- Queue entries: {result['queue_entries']}; free train slots: {result['train_slots']}",
        f"- Eligible in order: {result['eligible'] or 'none'}",
        f"- Planned batches: {result['batches'] or 'none'}",
    ]
    lines += [
        f"- #{f['pr']} goes alone on a deferred head: {f.get('done') or 'full run planned'}"
        for f in result["full_runs"]
    ]
    for a in result["rollup_actions"]:
        lines.append(
            f"- Rollup #{a['rollup']}: {a['action']} {a.get('drop') or a.get('halves') or ''}"
        )
    lines += [
        f"- #{pr} excluded: {why}" for pr, why in sorted(result["excluded"].items())
    ]
    return "\n".join(lines) + "\n"


def backoff_seconds(limits: dict, now: float, floor: int) -> float:
    """How long to wait for a rate limit reset: 0 while every resource has
    more than `floor` calls left."""
    waits = [
        max(0.0, lim["reset"] - now + 5)
        for lim in limits.values()
        if lim["remaining"] < floor
    ]
    return max(waits, default=0.0)


def tick(reader: GitHub, required: list, cfg: Config, live: Live | None) -> dict:
    before = reader.calls, live.app.calls if live else 0
    data = reader.gather()
    data["base_red"] = reader.base_red()
    reader.attach_reviewer_permissions(data)
    reader.attach_permissions(data, required, cfg)
    reader.attach_checks(data, required, cfg)
    red_runs, failures, dev_red, on_dev = {}, {}, {}, {}

    def shared(jobs: list) -> list:
        """The jobs dev's newest finished push run also fails, read once a
        tick per workflow."""
        for job in jobs:
            if job["workflow"] not in on_dev:
                on_dev[job["workflow"]] = reader.dev_red_jobs(job["workflow"], required)
        return [j for j in jobs if j["job"] in on_dev[j["workflow"]]]

    for node in data["repository"]["rollups"]["nodes"]:
        rollup = rollup_view(node, cfg, required)
        if not rollup or data["base_red"]:
            continue
        number, manifest = rollup["number"], rollup["manifest"]
        if manifest.get("held"):
            # Only dev decides now; its own red was read when it was held.
            dev_red[number] = shared(manifest["held"])
            continue
        signal = red_signal(rollup)
        if not signal:
            continue
        runs = reader.failed_runs(rollup, signal)
        red_runs[number] = runs
        jobs = reader.failed_jobs(runs, required)
        failures[number] = classify_failure(reader.job_logs(jobs), cfg.signatures)
        if on_red(manifest, failures[number])["action"] == "evict":
            dev_red[number] = shared(
                [{"workflow": j["workflow"], "job": j["name"]} for j in jobs]
            )
    result = plan(data, required, cfg, failures, dev_red)
    result["failures"] = failures
    if live:
        execute(result, live, red_runs)
    result["api"] = {
        "calls": reader.calls - before[0],
        "app_calls": (live.app.calls - before[1]) if live else 0,
        "cached_heads": len(reader.check_cache),
        "limits": reader.limits,
        "app_limits": live.app.limits if live else {},
    }
    return result


# --- The App token --------------------------------------------------------------

# Mint a new installation token when the current one has less than this left.
TOKEN_MARGIN_SECONDS = 600


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def openssl_sign(data: bytes, key: str) -> bytes:
    """RS256 over data (the stdlib has no RSA). The key sits in a 0600 file
    only for the call."""
    fd, path = tempfile.mkstemp(dir=os.environ.get("RUNNER_TEMP"))
    try:
        with os.fdopen(fd, "w") as out:
            out.write(key)
        done = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", path],
            input=data,
            check=False,
            capture_output=True,
        )
    finally:
        os.unlink(path)
    if done.returncode:
        raise RuntimeError(
            f"openssl could not sign the App JWT (exit {done.returncode})"
        )
    return done.stdout


class AppToken:
    """The train App's installation token for this repository only."""

    def __init__(
        self,
        app_id: str,
        key: str,
        repo: str,
        sign=openssl_sign,
        client=None,
        now=time.time,
    ):
        self.app_id, self.key, self.repo = app_id, key, repo
        self.sign, self.now = sign, now
        self.client = client or GitHub
        self.token, self.expires = None, 0.0
        self.minted = 0

    def jwt(self) -> str:
        now = int(self.now())
        head = b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        # iat 60 s back for clock drift; GitHub caps exp at 10 minutes.
        body = b64url(
            json.dumps(
                {"iat": now - 60, "exp": now + 540, "iss": str(self.app_id)}
            ).encode()
        )
        return f"{head}.{body}.{b64url(self.sign(f'{head}.{body}'.encode(), self.key))}"

    def get(self) -> str:
        if self.token and self.expires - self.now() > TOKEN_MARGIN_SECONDS:
            return self.token
        app = self.client(self.jwt(), self.repo)
        install = app.call("GET", f"repos/{self.repo}/installation")
        out = app.call(
            "POST",
            f"app/installations/{install['id']}/access_tokens",
            {"repositories": [self.repo.split("/")[1]]},
        )
        print(f"::add-mask::{out['token']}", flush=True)
        self.token = out["token"]
        self.expires = calendar.timegm(
            time.strptime(out["expires_at"], "%Y-%m-%dT%H:%M:%SZ")
        )
        self.minted += 1
        return self.token

    def revoke(self):
        if self.token:
            self.client(self.token, self.repo).request("DELETE", "installation/token")
            self.token = None


def keep_looping(
    now: float, deadline: float, last_busy: float, idle_s: float, interval: float
) -> bool:
    """Tick again unless the next tick passes the deadline or nothing has
    been in flight for idle_s."""
    return now + interval <= deadline and now - last_busy < idle_s


def post_slack(url: str, text: str) -> None:
    req = urllib.request.Request(
        url,
        data=json.dumps({"text": text}).encode(),
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(req, timeout=30).read()


def alert_crash(err: Exception) -> None:
    """One Slack line when a live run exits on an error. Only with
    DEVFLOW_HEARTBEAT=live; the post never hides the error itself."""
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if os.environ.get("DEVFLOW_HEARTBEAT") != "live" or not url:
        return
    run = "{}/{}/actions/runs/{}".format(
        os.environ.get("GITHUB_SERVER_URL", "https://github.com"),
        os.environ.get("GITHUB_REPOSITORY", ""),
        os.environ.get("GITHUB_RUN_ID", ""),
    )
    detail = " ".join(f"{type(err).__name__}: {err}".split())[:300]
    try:
        post_slack(url, f":rotating_light: batch train stopped: {detail} {run}")
    except (OSError, ValueError) as post_err:
        print(f"::warning::crash alert not posted: {post_err}", flush=True)


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    tick_cmd = sub.add_parser("tick")
    tick_cmd.add_argument("--mode", choices=["dark", "live"], default="dark")
    tick_cmd.add_argument(
        "--loop-minutes",
        type=float,
        default=0,
        help="keep ticking up to this long while work is in flight (0: one tick)",
    )
    tick_cmd.add_argument(
        "--idle-minutes",
        type=float,
        default=10,
        help="stop after this long with nothing in flight",
    )
    tick_cmd.add_argument("--interval", type=float, default=90)
    parity = sub.add_parser("parity")
    parity.add_argument("--candidate", required=True)
    parity.add_argument("--landed", required=True)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        return run_cmd(args)
    except Exception as err:
        if args.cmd == "tick" and args.mode == "live":
            alert_crash(err)
        raise


def run_cmd(args: argparse.Namespace) -> int:
    repo = os.environ["REPO"]
    reader = GitHub(os.environ["GH_TOKEN"], repo)
    if args.cmd == "parity":
        ok = tree_parity(
            reader.call("GET", f"repos/{repo}/git/commits/{args.candidate}"),
            reader.call("GET", f"repos/{repo}/git/commits/{args.landed}"),
        )
        print(
            json.dumps(
                {"candidate": args.candidate, "landed": args.landed, "tree_parity": ok}
            )
        )
        return 0 if ok else 1

    def load() -> tuple:
        """Policy from dev: read again every CONFIG_SECONDS, so a change to
        CODEOWNERS or the required checks reaches a long loop."""
        cfg = Config(
            high_globs=parse_high_globs(reader.file_text(POLICY_FILE)),
            signatures=parse_signatures(reader.file_text(SIGNATURES_FILE)),
            codeowners=reader.file_text(CODEOWNERS_FILE) or "",
        )
        return time.monotonic(), cfg, reader.required_checks()

    loaded, cfg, required = load()
    minter = live = None
    if args.mode == "live":
        minter = AppToken(
            os.environ["TRAIN_APP_ID"], os.environ["TRAIN_APP_PRIVATE_KEY"], repo
        )
        live = Live(repo, GitHub(minter.get(), repo), reader)
    start = last_busy = time.monotonic()
    deadline = start + args.loop_minutes * 60
    failed = 0
    try:
        while True:
            if time.monotonic() - loaded > CONFIG_SECONDS:
                loaded, cfg, required = load()
            if minter:
                live.app.token = minter.get()
            try:
                result = tick(reader, required, cfg, live)
                failed = 0
            except (RuntimeError, OSError) as err:
                # State lives in GitHub, so the next tick starts clean. A
                # run of failures is not transient: stop and show it.
                failed += 1
                print(f"::error title=train tick failed ({failed})::{err}", flush=True)
                if failed >= MAX_FAILED_TICKS or not args.loop_minutes:
                    raise
                time.sleep(args.interval)
                continue
            result["mode"] = args.mode
            result["loop"] = {
                "minutes": round((time.monotonic() - start) / 60, 1),
                "app_tokens_minted": minter.minted if minter else 0,
            }
            public = {k: v for k, v in result.items() if not k.startswith("_")}
            print(json.dumps(public, indent=2, sort_keys=True), flush=True)
            if os.environ.get("GITHUB_STEP_SUMMARY"):
                with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as out:
                    out.write(summary(result))
            now = time.monotonic()
            if result["busy"]:
                last_busy = now
            if not keep_looping(
                now, deadline, last_busy, args.idle_minutes * 60, args.interval
            ):
                return 0
            limits = {**reader.limits}
            if live:
                limits.update({f"app {k}": v for k, v in live.app.limits.items()})
            wait = backoff_seconds(limits, time.time(), RATE_FLOOR)
            if wait:
                print(
                    f"rate limit below {RATE_FLOOR}: waiting {wait:.0f} s", flush=True
                )
            time.sleep(
                min(max(args.interval, wait), max(0.0, deadline - time.monotonic()))
            )
    finally:
        if minter:
            minter.revoke()


if __name__ == "__main__":
    sys.exit(main())
