"""Batch train for the dev merge queue (devflow-train.yml).

GitHub's merge queue runs the full CI once per queue entry. The train puts
several approved pull requests into ONE entry: a rollup pull request whose
head is dev plus each member head, merged in order by GitHub's merges API
with no other change. The rollup's own pull_request run is the full internal
suite (merge groups run without the private EE package); the queue then
tests and lands it like any other entry. The queue stays the only writer to
dev and nobody bypasses a rule.

One tick reads GitHub, plans, and (live mode only) acts:

1. Open rollups. A member that moved, closed, lost its admit label or gained
   a high label makes the rollup stale: close it and let the rest re-batch.
   A rollup that is no longer approved on its head was pushed to by someone:
   close it. A rollup whose required checks failed, or that the queue
   removed, is retried once only if every failed job matches a known infra
   signature; otherwise it splits in halves, and a red single member leaves
   with train:red. A rollup removed for a conflict is rebuilt on the new dev.
2. Admitted pull requests (label devflow:train). Exclusions send a pull
   request down the normal path: drafts, forks, high tier, reverts, natively
   enqueued or auto-merge on, conflicts with dev, high-risk paths, required
   checks not green on the head, no approval on the head.
3. Batches. Size B = clamp(ceil(eligible / free train slots), 1, MAX_BATCH),
   so under load B grows and when idle a pull request goes alone at once.
   Two members that each add a migration to the same app never share a
   batch: together they would leave two migration leaves.

Dark mode (the default) only prints the plan. Live mode needs the train App
token (APP_TOKEN: branch, merges, pull request, auto-merge, dequeue, close)
and GITHUB_TOKEN (GH_TOKEN: approve as github-actions[bot], labels,
comments, re-runs). No pull request code runs here: there is no checkout.

    python3 -I .github/scripts/devflow_train.py tick [--mode dark|live] [--loop-minutes N]
    python3 -I .github/scripts/devflow_train.py parity --candidate SHA --landed SHA

State lives only in GitHub (labels, the rollup body manifest, marker
comments), so every tick rebuilds it and a crash loses nothing.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
import sys
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
BRANCH_PREFIX = "chore/devflow-train-"
BASE = "dev"
TRUSTED = {"OWNER", "MEMBER", "COLLABORATOR"}
ACTIONS_LOGIN = "github-actions"
POLICY_FILE = ".github/pr-autopilot.yml"
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
        self.require_verdict = (
            os.environ.get("DEVFLOW_TRAIN_REQUIRE_VERDICT", "") == "true"
        )
        self.train_login = bot_login(
            os.environ.get("DEVFLOW_TRAIN_LOGIN") or "futureagi-devflow-train[bot]"
        )
        self.high_globs = tuple(FLOOR_GLOBS) + tuple(high_globs or ())
        self.signatures: list = []
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
    reviews: list, author: str, assigned: str | None = None
) -> dict | None:
    """The newest devflow-review/v1 verdict posted by someone with write access
    who is not the author. The assigned reviewer's newest one wins when there
    is one: a later verdict by someone else does not replace it."""
    trusted = [
        r
        for r in reviews
        if VERDICT_MARKER in (r.get("body") or "")
        and r.get("authorAssociation") in TRUSTED
        and lower_login(r) != author.lower()
    ]
    trusted.sort(key=lambda r: r.get("submittedAt") or "")
    own = [r for r in trusted if assigned and lower_login(r) == assigned.lower()]
    return parse_verdict(own) or parse_verdict(trusted)


def lower_login(item: dict) -> str:
    return bot_login((item.get("author") or {}).get("login")).lower()


def parse_verdict(reviews: list) -> dict | None:
    """The verdict JSON of the newest well-formed review in an oldest-first list."""
    for review in reversed(reviews):
        match = JSON_BLOCK.search(review.get("body") or "")
        if not match:
            continue
        try:
            verdict = json.loads(match.group(1))
        except ValueError:
            continue
        if isinstance(verdict, dict) and verdict.get("schema") == "devflow-review/v1":
            return verdict
    return None


def approved_on_head(reviews: list, head: str, by: str | None = None) -> bool:
    return any(
        r.get("state") == "APPROVED"
        and (r.get("commit") or {}).get("oid") == head
        and (by is None or bot_login((r.get("author") or {}).get("login")) == by)
        for r in reviews
    )


def check_states(contexts: list, required: list) -> dict:
    """State of each required check on the head: success, failure, pending or missing.

    Only runs from the required app count. Skipped runs are ignored and the
    newest other run decides, so a newer failure overrules an older success.
    """
    states = {}
    for req in required:
        runs = [
            c
            for c in contexts
            if c.get("__typename") == "CheckRun"
            and c.get("name") == req["context"]
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
        else:
            states[req["context"]] = (
                "success" if newest.get("conclusion") == "SUCCESS" else "failure"
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
        "verdict": latest_verdict(
            reviews,
            bot_login((node.get("author") or {}).get("login")),
            assigned_reviewer(node),
        ),
        "checks": check_states(head_contexts(node), required),
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
    if m["auto_merge"]:
        return "auto-merge is on"
    if m["mergeable"] == "CONFLICTING":
        return "conflicts with dev"
    if not m["files_complete"]:
        return "too many files to classify: goes alone"
    hit = high_path(m["files"], cfg.high_globs)
    if hit:
        return f"high-risk path ({hit}): goes alone"
    if m["review_decision"] != "APPROVED" or not m["approved_on_head"]:
        return "not approved on this head"
    verdict = m["verdict"]
    if verdict is not None and verdict.get("head") == m["head_sha"]:
        if verdict.get("tier") != "low" or verdict.get("needs_person"):
            return f"review tier {verdict.get('tier')}: goes alone"
        if verdict.get("verdict") != "pass":
            return f"review verdict {verdict.get('verdict')}"
    elif cfg.require_verdict:
        return "no review verdict on this head"
    # Last, and the only rule that costs API calls (see needs_checks).
    bad = {name: state for name, state in m["checks"].items() if state != "success"}
    if bad:
        return "required checks not green: " + ", ".join(
            f"{k}={v}" for k, v in sorted(bad.items())
        )
    return None


def needs_checks(node: dict, required: list, cfg: Config) -> bool:
    """Whether this admitted pull request's checks decide anything: only if
    every other rule passes. Saves the REST calls for the rest."""
    m = member_view(node, required)
    m["checks"] = {r["context"]: "success" for r in required}
    return exclusion(m, cfg, {}) is None


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
            or ADMIT_LABEL not in m["labels"]
            or any(label in m["labels"] for label in HIGH_LABELS)
        ):
            stale.append(member["pr"])
    return stale


def rollup_action(rollup: dict, live: dict, failure: str | None) -> dict:
    """What one tick does with one open rollup pull request."""
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
    if signal:
        act = on_red(manifest, failure or "red")
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


def plan(data: dict, required: list, cfg: Config, failures: dict | None = None) -> dict:
    """The whole tick as data. Pure: tests feed it built payloads.

    `failures` maps a red rollup's number to 'infra' or 'red' (from its
    failed job logs); a red rollup missing from it counts as 'red'.
    """
    failures = failures or {}
    repo = data["repository"]
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

    actions, reserved, evicted = [], {}, {}
    for rollup in rollups:
        act = rollup_action(rollup, live, failures.get(rollup["number"]))
        act["rollup"] = rollup["number"]
        actions.append(act)
        if act["action"] in ("wait", "ensure", "retry", "split"):
            for member in rollup["manifest"]["members"]:
                reserved[member["pr"]] = rollup["number"]
        elif act["action"] == "evict":
            evicted[act["pr"]] = act["sha"]
    still_open = sum(1 for a in actions if a["action"] in ("wait", "ensure", "retry"))
    new_from_splits = sum(len(a["halves"]) for a in actions if a["action"] == "split")

    labels, excluded, eligible = [], {}, []
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
        if reason:
            excluded[m["number"]] = reason
        else:
            eligible.append(m)
    ordered = round_robin(eligible)
    slots = train_slots(cfg, still_open + new_from_splits, len(queue))
    batches = plan_batches(ordered, slots, cfg.max_batch)
    return {
        "queue_entries": len(queue),
        "rollup_actions": actions,
        "label_changes": labels,
        "excluded": excluded,
        "eligible": [m["number"] for m in ordered],
        "train_slots": slots,
        "batches": [[m["number"] for m in b] for b in batches],
        "busy": bool(still_open or new_from_splits or batches),
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


def build_query() -> str:
    # Check runs are not read here: a CI run on one head has over 100 check
    # runs and the aggregates finish last, so a head's first 100 contexts
    # miss them, and filtered lookups per pull request exceed GraphQL's
    # resource limits. GitHub.attach_checks reads them by name over REST.
    def prs(alias: str, label: str, count: int) -> str:
        return f'{alias}: pullRequests(states: OPEN, baseRefName: "{BASE}", labels: ["{label}"], first: {count}) {{ nodes {{ {PR_FIELDS} }} }}'

    return (
        "query($owner: String!, $name: String!) { repository(owner: $owner, name: $name) { "
        f'mergeQueue(branch: "{BASE}") {{ entries(first: 100) {{ nodes {{ pullRequest {{ number }} }} }} }} '
        f"{prs('admitted', ADMIT_LABEL, 100)} {prs('rollups', ROLLUP_LABEL, 20)} }} }}"
    )


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class GitHub:
    def __init__(self, token: str, repo: str):
        self.token, self.repo = token, repo

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
        try:
            with opener.open(req, timeout=60) as resp:
                status, text = resp.status, resp.read().decode(errors="replace")
        except urllib.error.HTTPError as err:
            status, text = err.code, err.read().decode(errors="replace")
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

    def gather(self) -> dict:
        owner, name = self.repo.split("/")
        return self.graphql(build_query(), owner=owner, name=name)

    def check_runs(self, sha: str, required: list) -> list:
        """Every run of each required check on one commit, by name and app."""
        runs = []
        for req in required:
            name = urllib.parse.quote(req["context"])
            out = self.call(
                "GET",
                f"repos/{self.repo}/commits/{sha}/check-runs?check_name={name}&app_id={req['app_id']}&filter=all&per_page=50",
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

    def attach_checks(self, data: dict, required: list, cfg: Config):
        """Read checks for every rollup, and for each admitted pull request
        that every other rule lets through."""
        repo = data["repository"]
        for node in repo["rollups"]["nodes"]:
            node["_check_runs"] = self.check_runs(node["headRefOid"], required)
        for node in repo["admitted"]["nodes"]:
            if needs_checks(node, required, cfg):
                node["_check_runs"] = self.check_runs(node["headRefOid"], required)

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

    def failed_job_logs(self, runs: list, required: list) -> list:
        """Log tails of the failed jobs, leaving out the required aggregates.

        Only runs that produce a required check count: another workflow's
        failure does not block the rollup, so it must not decide the retry.
        """
        aggregates = {r["context"] for r in required}
        logs = []
        for run in runs:
            jobs = self.call(
                "GET",
                f"repos/{self.repo}/actions/runs/{run['id']}/jobs?filter=latest&per_page=100",
            )
            if not any(
                job["name"].split(" / ")[-1] in aggregates for job in jobs["jobs"]
            ):
                continue
            for job in jobs["jobs"]:
                if (
                    job.get("conclusion") != "failure"
                    or job["name"].split(" / ")[-1] in aggregates
                ):
                    continue
                status, text = self.request(
                    "GET", f"repos/{self.repo}/actions/jobs/{job['id']}/logs", raw=True
                )
                logs.append(
                    "\n".join(text.splitlines()[-LOG_TAIL_LINES:])
                    if status == 200
                    else ""
                )
        return logs


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
    for batch in result["batches"]:
        live.open_rollup([members[pr] for pr in batch])


def summary(result: dict) -> str:
    lines = [
        f"## Batch train ({result['mode']})",
        f"- Queue entries: {result['queue_entries']}; free train slots: {result['train_slots']}",
        f"- Eligible in order: {result['eligible'] or 'none'}",
        f"- Planned batches: {result['batches'] or 'none'}",
    ]
    for a in result["rollup_actions"]:
        lines.append(
            f"- Rollup #{a['rollup']}: {a['action']} {a.get('drop') or a.get('halves') or ''}"
        )
    lines += [
        f"- #{pr} excluded: {why}" for pr, why in sorted(result["excluded"].items())
    ]
    return "\n".join(lines) + "\n"


def tick(reader: GitHub, required: list, cfg: Config, live: Live | None) -> dict:
    data = reader.gather()
    reader.attach_checks(data, required, cfg)
    red_runs, failures = {}, {}
    for node in data["repository"]["rollups"]["nodes"]:
        rollup = rollup_view(node, cfg, required)
        signal = rollup and red_signal(rollup)
        if signal:
            runs = reader.failed_runs(rollup, signal)
            red_runs[rollup["number"]] = runs
            failures[rollup["number"]] = classify_failure(
                reader.failed_job_logs(runs, required), cfg.signatures
            )
    result = plan(data, required, cfg, failures)
    result["failures"] = failures
    if live:
        execute(result, live, red_runs)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    tick_cmd = sub.add_parser("tick")
    tick_cmd.add_argument("--mode", choices=["dark", "live"], default="dark")
    tick_cmd.add_argument(
        "--loop-minutes",
        type=float,
        default=0,
        help="keep ticking while work is in flight",
    )
    tick_cmd.add_argument("--interval", type=float, default=90)
    parity = sub.add_parser("parity")
    parity.add_argument("--candidate", required=True)
    parity.add_argument("--landed", required=True)
    args = parser.parse_args(argv)

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

    cfg = Config(
        high_globs=parse_high_globs(reader.file_text(POLICY_FILE)),
        signatures=parse_signatures(reader.file_text(SIGNATURES_FILE)),
    )
    required = reader.required_checks()
    live = (
        Live(repo, GitHub(os.environ["APP_TOKEN"], repo), reader)
        if args.mode == "live"
        else None
    )
    deadline = time.monotonic() + args.loop_minutes * 60
    while True:
        result = tick(reader, required, cfg, live)
        result["mode"] = args.mode
        public = {k: v for k, v in result.items() if not k.startswith("_")}
        print(json.dumps(public, indent=2, sort_keys=True), flush=True)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as out:
                out.write(summary(result))
        if not result["busy"] or time.monotonic() + args.interval > deadline:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
