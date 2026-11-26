"""Dev-flow heartbeat: base-health breaker, escape rate and the merge digest.

Runs from pr-digest.yml on every push to dev, and the breaker also runs at
the end of every merge-queue build (backend-queue-ci.yml), so a red base
trips it even when nothing lands. The breaker (--breaker-only) runs every
time; the full digest at most once an hour (the workflow gates it). It reads
the Actions and git history, decides three things and says them in one
Slack message:

- Breaker: the same job failed on merge-queue builds of >= 3 different pull
  requests inside the window, or on the newest dev push run and also on the
  run before it or on one queue pull request (one red dev run alone is a
  flake until it repeats). Groups are
  cumulative, so a failure that an entry ahead in its group explains (that
  entry failed the job, or a later build without it passed) is not counted.
  The base is probably red: an open issue with
  the base-red label per failing job, plus a Slack line. The batch train
  pauses while any such issue is open. The breaker closes its own issue when
  the job no longer trips and a later queue build of its workflow passed.
- Escape rate: reverts and fix-forwards among the last 100 first-parent
  merges on dev, read from each pull request's title and description. Above
  2% the tight-approval mode is wanted, and it stays wanted until the rate
  is below 1.5% (APPROVAL_MODE_TIGHT is the current state). When devflow-review/v1
  reviews exist, also one rate per review engine.
- Digest: landings, queue red rate by class, queue p90, runner wait p95,
  escape rate, review quality per engine (findings fixed vs answered away
  in the author's devflow-land:v1 replies), the share of open pull requests
  that conflict, and the top flaky jobs. Retries stay at 0: a job is flaky
  when it failed and then passed on a re-run of the same commit, or failed
  on a queue group (not as a cascade) whose pull request then landed with
  the same head.

The repo variable APPROVAL_MODE_TIGHT can only be written with a token that
has the Variables permission, which GITHUB_TOKEN never has. With
DEVFLOW_VARS_TOKEN set the script writes it; without it the script only
reports the signal. It never pauses the merge queue.

The base-red issues (public on this repo) and every Slack post need the
repository variable DEVFLOW_HEARTBEAT=live. Without it a tick only prints,
and the workflows put that in the job summary.

All functions that decide are pure and take plain dicts, so the tests feed
recorded shapes. Only `Api` and `main` touch the network or git.
"""

from __future__ import annotations

import http.client
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone

QUEUE_WORKFLOWS = ("backend-queue-ci.yml", "e2e-ci.yml")
BREAKER_WINDOW = timedelta(hours=2)
BREAKER_MIN_PRS = 3
STATS_WINDOW = timedelta(hours=24)
WAIT_WINDOW = timedelta(hours=1)
ESCAPE_WINDOW = 100
ESCAPE_THRESHOLD = 0.02  # tight mode starts above this rate
ESCAPE_EXIT = 0.015  # and ends below this one
ESCAPE_HISTORY = 400
# A filtered run listing returns at most this many results; split the window.
LISTING_CAP = 1000
MAX_PAGES = 50
# A red run is infra when every failed job failed inside a setup or install
# step, not in the test run. Job names are no signal: apt and DB failures hit
# ordinary shard jobs. Step names seen in red merge-queue builds: the backend
# test-packages, environment and database composite actions, compose pull and
# up ("Start test services"), "Prefetch vendor images", "Install ...",
# "Set up job" and the terminal inventory qualification (shards disagree).
INFRA_STEP_PATTERN = re.compile(
    r"(backend-test-packages|/environment|/database|set up|setup|install|prepare|"
    r"prefetch|start test services|compose|pull|inventory qualification|"
    r"download|checkout|cache)",
    re.IGNORECASE,
)
# Jobs whose failure is not a base signal: the required aggregates fail with
# whatever job failed (that job is counted itself), the full-run gate fails
# per pull request (a light PR without ci:full), and the breaker is this.
NOT_BASE_SIGNALS = frozenset({
    "Backend Tests Pass",
    "backend / Backend Tests Pass",  # the reusable call's aggregate inside the queue
    "E2E Tests Pass",
    "Members ran the internal suite",
    "Base-health breaker",
})
BREAKER_LABEL = "base-red"
BREAKER_TITLE = "Base looks red: "
VARIABLE_NAME = "APPROVAL_MODE_TIGHT"
# The suffix is the group's base: dev's tip, or the head of the entry ahead.
QUEUE_BRANCH = re.compile(r"^gh-readonly-queue/[^/]+/pr-(\d+)-([0-9a-f]{40})?")
# Dev push runs of the queue's suites, and the prefix that names their jobs
# the way the queue does (the queue calls backend-ci.yml as "backend").
DEV_PUSH_WORKFLOWS = {"backend-ci.yml": "backend / ", "e2e-ci.yml": ""}
FIX_REF = re.compile(
    r"\b(?:fix(?:es|ed)?|follow[- ]?ups?(?:\s+(?:to|of|for))?|revert(?:s|ed)?)\s+"
    r"(?:pr\s*)?#(\d+)",
    re.IGNORECASE,
)
# Merge-method landings: the subject names the pull request, the body's first
# line is its title. Squash landings: the subject is the title plus "(#N)".
MERGE_SUBJECT = re.compile(r"^Merge pull request #(\d+) ")
SQUASH_PR = re.compile(r"\(#(\d+)\)\s*$")
# GitHub's revert button ('Revert "..."') and a conventional revert: title.
REVERT_TITLE = re.compile(r'^(?:Revert "|revert(?:\([^)]*\))?!?:)', re.IGNORECASE)
FIX_TITLE = re.compile(r"^fix(?:\([^)]*\))?!?:", re.IGNORECASE)
PR_REF = re.compile(r"#(\d+)\b")
REVIEW_MARKER = "<!-- devflow-review:v1 -->"
JSON_BLOCK = re.compile(r"```json\s*\n(.*?)\n\s*```", re.DOTALL)
WRITE = {"admin", "maintain", "write"}
# The author's replies to an agent review (the land skill writes them).
REPLY_MARKER = re.compile(r"<!--\s*devflow-land:v1\s+round=\d+\s+review=(\d+)\s*-->")
REPLY_LINE = re.compile(r"^\s*-\s*([A-Z]+\d+)\s*:\s*(fixed in [0-9a-f]{7,40}|answer:.*)$", re.MULTILINE)
FLAKY_TOP = 5
# Workflows whose re-runs of the same commit show flaky jobs.
RERUN_WORKFLOWS = ("backend-ci.yml", "e2e-ci.yml", "backend-queue-ci.yml")


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def iso(at: datetime) -> str:
    return at.strftime("%Y-%m-%dT%H:%M:%SZ")


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(pct / 100 * len(ordered)) - 1)]


def pr_number(run: dict) -> int | None:
    match = QUEUE_BRANCH.match(run.get("head_branch") or "")
    return int(match.group(1)) if match else None


def failed_steps(job: dict) -> list[str]:
    return [st["name"] for st in job.get("steps") or [] if st.get("conclusion") == "failure"]


def is_infra_job(job: dict) -> bool:
    """True when the job failed in a setup or install step. No step data means other."""
    steps = failed_steps(job)
    return bool(steps) and all(INFRA_STEP_PATTERN.search(n) for n in steps)


def wants_jobs(run: dict, now: datetime, breaker_only: bool) -> bool:
    """Whose jobs to read. A failed run, or one still going (its own failed jobs
    already count: the queue's breaker job runs inside a run that is not done).
    The digest also reads the last hour's runs for the runner wait."""
    if run.get("conclusion") == "failure" or run.get("status") != "completed":
        return True
    return not breaker_only and now - parse_ts(run["created_at"]) <= WAIT_WINDOW


def queue_failures(
    runs: list[dict], jobs_by_run: dict[int, list[dict]], prefix: str = "", dev: bool = False, prior: bool = False
) -> list[dict]:
    """{job, pr, at, workflow, run} per completed failed job, whether or not its
    run is done. `prefix` names a dev push run's jobs as the queue names them;
    `dev` marks the failures of a dev push run, and `prior` those of the run
    before the newest one."""
    out = []
    for r in runs:
        for j in jobs_by_run.get(r["id"], []):
            if (
                j.get("status", "completed") == "completed"
                and j.get("conclusion") == "failure"
                and j["name"] not in NOT_BASE_SIGNALS
            ):
                out.append({
                    "job": prefix + j["name"], "pr": None if dev else pr_number(r), "workflow": r.get("path"),
                    "at": j.get("completed_at") or r["updated_at"], "run": r["id"],
                    **({"dev": True} if dev else {}), **({"prior": True} if prior else {}),
                })
    return out


def queue_base(run: dict) -> str | None:
    match = QUEUE_BRANCH.match(run.get("head_branch") or "")
    return match.group(2) if match else None


def ahead(run: dict, by_head: dict) -> list[dict]:
    """The same workflow's runs of the entries ahead of this group, nearest first.
    A group is built on the head of the entry ahead, so the chain follows bases."""
    out, base = [], queue_base(run)
    while (run.get("path"), base) in by_head and len(out) < len(by_head):
        prev = by_head[(run.get("path"), base)]
        out.append(prev)
        base = queue_base(prev)
    return out


def job_passed(run: dict, job: str, jobs_by_run: dict[int, list[dict]]) -> bool:
    return run.get("conclusion") == "success" or any(
        j["name"] == job and j.get("conclusion") == "success" for j in jobs_by_run.get(run["id"], [])
    )


def independent(failures: list[dict], runs: list[dict], jobs_by_run: dict[int, list[dict]]) -> list[dict]:
    """The failures a defect ahead in the same group does not explain.

    Queue groups are cumulative, so one bad entry at the front fails every
    group behind it. A failure on PR X's group is dropped when an entry ahead
    in that group failed the same job (it is evicted for it), or when a later
    build of X's group without one of those entries passed the job. Dev push
    failures are the base itself and always stay.
    """
    by_id = {r["id"]: r for r in runs}
    by_head = {(r.get("path"), r.get("head_sha")): r for r in runs if r.get("head_sha")}
    failed = {(f["run"], f["job"]) for f in failures if "run" in f}
    out = []
    for f in failures:
        run = by_id.get(f.get("run"))
        if f.get("dev") or run is None:
            out.append(f)
            continue
        front = ahead(run, by_head)
        if any((r["id"], f["job"]) in failed for r in front):
            continue
        prs = {pr_number(r) for r in front}
        rebuilt = [
            r for r in runs
            if pr_number(r) == f["pr"] and r.get("path") == run.get("path")
            and r["created_at"] > run["created_at"]
            and prs - {pr_number(a) for a in ahead(r, by_head)}
        ]
        if prs and any(job_passed(r, f["job"], jobs_by_run) for r in rebuilt):
            continue
        out.append(f)
    return out


def detect_breaker(failures: list[dict], now: datetime) -> list[dict]:
    """failures: {job, pr, at} per failed job on a merge-queue run, and
    {job, dev: True, at} per failed job on the newest dev push run (with
    prior: True, on the dev push run before it).

    Returns one entry per job name, worst first, that failed inside the window
    for >= BREAKER_MIN_PRS different pull requests, or on the newest dev push
    run and also on the run before it ("dev": 2) or on one queue pull request
    ("dev": 1). One red dev run alone is a flake until it repeats. The run
    before the newest counts at any age. Give it independent() failures.
    """
    prs_by_job: dict[str, set[int]] = defaultdict(set)
    dev_jobs, prior_jobs = set(), set()
    for f in failures:
        if f.get("prior"):
            prior_jobs.add(f["job"])
        elif now - parse_ts(f["at"]) > BREAKER_WINDOW:
            continue
        elif f.get("dev"):
            dev_jobs.add(f["job"])
        elif f["pr"] is not None:
            prs_by_job[f["job"]].add(f["pr"])
    tripped = []
    for job in set(prs_by_job) | dev_jobs:
        dev = 2 if job in prior_jobs else 1
        if job in dev_jobs and (dev == 2 or prs_by_job[job]):
            tripped.append({"job": job, "prs": sorted(prs_by_job[job]), "dev": dev})
        elif len(prs_by_job[job]) >= BREAKER_MIN_PRS:
            tripped.append({"job": job, "prs": sorted(prs_by_job[job])})
    return sorted(tripped, key=lambda t: (-len(t["prs"]), t["job"]))


def resolved_trips(issues: list[dict], tripped: list[dict], failures: list[dict], runs: list[dict]) -> list[dict]:
    """Open base-red issues the breaker may close: the job no longer trips, and a
    queue build of the job's workflow (any queue workflow when unknown) passed
    after both the issue and the job's last failure. An idle queue closes nothing."""
    now_red = {t["job"] for t in tripped}
    out = []
    for issue in issues:
        job = issue["title"][len(BREAKER_TITLE):]
        if not issue["title"].startswith(BREAKER_TITLE) or job in now_red:
            continue
        mine = [f for f in failures if f["job"] == job]
        last_bad = max([parse_ts(issue["created_at"])] + [parse_ts(f["at"]) for f in mine])
        workflows = {f["workflow"] for f in mine if f.get("workflow")}
        green = [
            r for r in runs
            if r.get("conclusion") == "success"
            and (not workflows or r.get("path") in workflows)
            and parse_ts(r.get("updated_at") or r["created_at"]) > last_bad
            and parse_ts(r["created_at"]) > last_bad
        ]
        if green:
            out.append({"number": issue["number"], "job": job, "run": max(green, key=lambda r: r["created_at"])["id"]})
    return out


def landing(c: dict) -> dict:
    """{own, title, text} of one first-parent commit on dev."""
    merge = MERGE_SUBJECT.match(c["subject"])
    body = c.get("body") or ""
    if merge:
        title = next((line.strip() for line in body.splitlines() if line.strip()), "")
        return {"own": int(merge.group(1)), "title": title, "text": body}
    squash = SQUASH_PR.search(c["subject"])
    return {
        "own": int(squash.group(1)) if squash else None,
        "title": c["subject"],
        "text": f"{c['subject']}\n{body}",
    }


def reviewer(r: dict) -> str:
    return ((r.get("author") or {}).get("login") or "").lower()


def trusted(r: dict, writers: set[str]) -> bool:
    """A devflow-review/v1 review by someone with write access. Not the
    authorAssociation: GitHub shows an org member with private membership as
    CONTRIBUTOR to GITHUB_TOKEN."""
    return REVIEW_MARKER in (r.get("body") or "") and reviewer(r) in writers


def review_engine(reviews: list[dict], writers: set[str]) -> str | None:
    """Engine of the newest trusted devflow-review/v1 review; "unknown" when it
    names none; None when the pull request has no such review."""
    trusted_reviews = sorted((r for r in reviews if trusted(r, writers)), key=lambda r: r.get("submittedAt") or "")
    for r in reversed(trusted_reviews):
        verdict = review_verdict(r["body"])
        if verdict:
            engine = verdict.get("engine")
            return engine if isinstance(engine, str) and engine else "unknown"
    return None


def review_verdict(body: str) -> dict | None:
    """The devflow-review/v1 verdict of a review body, or None. Exactly one
    ```json fence, after the marker (parsing v2.1): a finding's text could
    carry a fenced fake verdict, so any other count is no verdict."""
    if body.count("```json") != 1:
        return None
    match = JSON_BLOCK.search(body)
    if not match or match.start() < body.find(REVIEW_MARKER):
        return None
    try:
        verdict = json.loads(match.group(1))
    except ValueError:
        return None
    return verdict if isinstance(verdict, dict) and verdict.get("schema") == "devflow-review/v1" else None


def tight_wanted(rate: float, tight_now: bool) -> bool:
    """Hysteresis: enter tight mode above 2%, leave it below 1.5%."""
    return rate >= ESCAPE_EXIT if tight_now else rate > ESCAPE_THRESHOLD


def escape_rate(commits: list[dict], details: dict[int, dict] | None = None, tight_now: bool = False) -> dict:
    """commits: newest-first first-parent {sha, subject, body} on dev.

    details: {pr: {"body", "engine"}} read from the API (the description a
    merge commit does not carry, and the review engine); may be partial.
    The window is the newest ESCAPE_WINDOW commits. Older commits only tell
    which #N are pull requests, so "fixes #N" on an issue is not counted.
    A revert is an escape by its title. A fix-forward has a fix: title and
    references an earlier landing: "fixes/follow-up to/reverts #N" in the
    title or description, or any "#N" in the title. The escaped pull
    request is #N. `tight_now` is APPROVAL_MODE_TIGHT today (see tight_wanted).
    """
    details = details or {}
    lands = [landing(c) for c in commits]
    known = {land["own"] for land in lands if land["own"]}
    window = list(zip(commits[:ESCAPE_WINDOW], lands[:ESCAPE_WINDOW]))
    escapes = []
    for c, land in window:
        own = land["own"]
        text = f"{land['text']}\n{(details.get(own) or {}).get('body') or ''}"
        revert = bool(REVERT_TITLE.match(land["title"]))
        refs = set()
        # Only a fix or a revert can be a fix-forward: a ci: or chore: title
        # that says "follow-up to #N" is planned work (13 of 100 on dev read
        # that way before this rule, 4 with it).
        if revert or FIX_TITLE.match(land["title"]):
            refs = {int(n) for n in FIX_REF.findall(text)}
            refs |= {int(n) for n in PR_REF.findall(land["title"])}
        refs = {n for n in refs if n in known and n != own and (own is None or n < own)}
        if revert or refs:
            escapes.append({
                "sha": c["sha"][:10], "pr": own, "subject": land["title"],
                "kind": "revert" if revert else "fix-forward", "refs": sorted(refs),
            })
    total = len(window)
    rate = len(escapes) / total if total else 0.0
    return {
        "count": len(escapes), "total": total, "rate": rate, "tight": tight_wanted(rate, tight_now),
        "escapes": escapes, "by_engine": by_engine([land["own"] for _, land in window], escapes, details),
    }


def by_engine(window_prs: list, escapes: list[dict], details: dict[int, dict]) -> dict:
    """{engine: {"count", "total", "rate"}} over the window's reviewed pull requests.

    An escape counts against the engine that reviewed the escaped pull
    request (#N it references), not the one that reviewed the fix. Empty
    when no pull request in the window has a devflow review.
    """
    engine = {n: (details.get(n) or {}).get("engine") for n in set(window_prs) | {r for e in escapes for r in e["refs"]}}
    totals: dict[str, int] = defaultdict(int)
    for n in window_prs:
        if engine.get(n):
            totals[engine[n]] += 1
    if not totals:
        return {}
    counts: dict[str, int] = defaultdict(int)
    for e in escapes:
        for name in {engine.get(r) for r in e["refs"]} - {None}:
            counts[name] += 1
    return {
        name: {"count": counts[name], "total": totals[name], "rate": counts[name] / totals[name]}
        for name in sorted(totals)
    }


def rerun_flakes(runs: list[dict], jobs_all: dict[int, list[dict]]) -> list[dict]:
    """{job, workflow, url} per job that failed on one attempt of a run and
    passed on a later attempt of the same run (the same commit).
    jobs_all: jobs?filter=all of runs with run_attempt > 1."""
    out = []
    for r in runs:
        jobs = jobs_all.get(r["id"]) or []
        for name in {j["name"] for j in jobs}:
            mine = [j for j in jobs if j["name"] == name]
            failed = [j for j in mine if j.get("conclusion") == "failure"]
            first = min((j.get("run_attempt") or 1 for j in failed), default=None)
            if first is not None and any(
                j.get("conclusion") == "success" and (j.get("run_attempt") or 1) > first for j in mine
            ):
                url = min(failed, key=lambda j: j.get("run_attempt") or 1).get("html_url") or r.get("html_url")
                out.append({"job": name, "workflow": r.get("path"), "url": url})
    return out


def queue_flakes(failures: list[dict], runs: list[dict], group_heads: dict[int, str], landed: dict[int, str]) -> list[dict]:
    """{job, workflow, url} per independent() queue failure whose pull request
    later landed with the head that group tested.
    group_heads: {run id: the PR head in that group}; landed: {PR: landed head}."""
    by_id = {r["id"]: r for r in runs}
    return [
        {"job": f["job"], "workflow": f.get("workflow"), "url": (by_id.get(f["run"]) or {}).get("html_url")}
        for f in failures
        if not f.get("dev") and f.get("pr") in landed and group_heads.get(f.get("run")) == landed[f["pr"]]
    ]


def top_flaky(flakes: list[dict], n: int = 5) -> list[dict]:
    """[{job, workflow, count, url}] most frequent first, one link each."""
    counts: dict[tuple, list] = defaultdict(list)
    for f in flakes:
        counts[(f.get("workflow") or "", f["job"])].append(f.get("url"))
    ranked = sorted(counts.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    return [
        {"workflow": wf, "job": job, "count": len(urls), "url": next((u for u in urls if u), None)}
        for (wf, job), urls in ranked[:n]
    ]


def review_quality(reviews: list[dict], comments: list[dict], author: str, writers: set[str]) -> dict:
    """{engine: {"findings", "fixed", "answered"}} on one pull request.

    Findings come from trusted devflow-review/v1 reviews; the author's
    devflow-land:v1 replies (review=<id>) answer each one "fixed in <sha>" or
    "answer: <reason>". A finding without a reply counts in neither."""
    replies: dict[str, dict[str, str]] = defaultdict(dict)
    for c in comments:
        header = REPLY_MARKER.search(c.get("body") or "")
        if header and ((c.get("author") or {}).get("login") or "").lower() == author.lower():
            for fid, answer in REPLY_LINE.findall(c["body"]):
                replies[header.group(1)][fid] = "fixed" if answer.startswith("fixed") else "answered"
    out: dict[str, dict[str, int]] = {}
    for r in reviews:
        if not trusted(r, writers):
            continue
        verdict = review_verdict(r["body"])
        if not verdict:
            continue
        engine = verdict.get("engine") if isinstance(verdict.get("engine"), str) and verdict.get("engine") else "unknown"
        tally = out.setdefault(engine, {"findings": 0, "fixed": 0, "answered": 0})
        answers = replies.get(str(r.get("databaseId")), {})
        for finding in verdict.get("findings") or []:
            fid = finding.get("id") if isinstance(finding, dict) else None
            tally["findings"] += 1
            if fid in answers:
                tally[answers[fid]] += 1
    return out


def quality_by_engine(details: dict[int, dict], window_prs: list) -> dict:
    """review_quality() summed per engine over the window's pull requests."""
    total: dict[str, dict[str, int]] = {}
    for n in set(window_prs):
        for engine, tally in ((details.get(n) or {}).get("quality") or {}).items():
            agg = total.setdefault(engine, {"findings": 0, "fixed": 0, "answered": 0})
            for key, value in tally.items():
                agg[key] += value
    return dict(sorted(total.items()))


def queue_stats(runs: list[dict], jobs_by_run: dict[int, list[dict]], now: datetime, hours: int = 24) -> dict:
    """Red rate by class and queue p90 over completed merge_group runs in the
    window; runner wait p95 over the last hour's runs."""
    since = now - timedelta(hours=hours)
    done = [r for r in runs if r.get("conclusion") in ("success", "failure") and parse_ts(r["created_at"]) >= since]
    red = {"infra": 0, "other": 0}
    for r in done:
        if r["conclusion"] != "failure":
            continue
        failed = [j for j in jobs_by_run.get(r["id"], []) if j.get("conclusion") == "failure"]
        red["infra" if failed and all(is_infra_job(j) for j in failed) else "other"] += 1
    waits = [(parse_ts(r["updated_at"]) - parse_ts(r["created_at"])).total_seconds() for r in done]
    recent = {r["id"] for r in runs if now - parse_ts(r["created_at"]) <= WAIT_WINDOW}
    runner_waits = [
        (parse_ts(j["started_at"]) - parse_ts(j["created_at"])).total_seconds()
        for run_id, js in jobs_by_run.items()
        if run_id in recent
        for j in js
        if j.get("started_at") and j.get("created_at")
    ]
    return {
        "runs": len(done),
        "red": red,
        "red_rate": (red["infra"] + red["other"]) / len(done) if done else 0.0,
        "queue_p90_s": percentile(waits, 90),
        "runner_p95_s": percentile(runner_waits, 95),
    }


def dirty_share(mergeables: list[str]) -> float | None:
    return mergeables.count("CONFLICTING") / len(mergeables) if mergeables else None


def fmt_dur(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    return f"{seconds / 60:.1f}m" if seconds >= 90 else f"{seconds:.0f}s"


def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def render_digest(
    landings: int, stats: dict, esc: dict, dirty: float | None, breaker: list[dict],
    flaky: list[dict] = (), quality: dict | None = None,
) -> str:
    lines = ["*dev flow, last hour*"]
    for b in breaker:
        lines.append(breaker_line(b))
    lines.append(f"- landings in the last hour: {landings}")
    lines.append(
        f"- queue red rate (24h, {stats['runs']} runs): {pct(stats['red_rate'])} "
        f"(infra {stats['red']['infra']}, other {stats['red']['other']})"
    )
    lines.append(f"- queue run time p90: {fmt_dur(stats['queue_p90_s'])}")
    lines.append(f"- runner job wait p95 (1h): {fmt_dur(stats['runner_p95_s'])}")
    flag = " :warning: tight approval wanted (on above 2%, off below 1.5%)" if esc["tight"] else ""
    lines.append(f"- escape rate (last {esc['total']} merges): {pct(esc['rate'])} ({esc['count']}){flag}")
    for name, e in (esc.get("by_engine") or {}).items():
        lines.append(f"  - reviewed by `{name}`: {pct(e['rate'])} ({e['count']} of {e['total']})")
    if quality:
        lines.append(f"- review quality (last {esc['total']} merges): findings fixed vs answered away")
        for name, q in quality.items():
            replied = q["fixed"] + q["answered"]
            share = q["fixed"] / replied if replied else None
            lines.append(
                f"  - `{name}`: {pct(share)} fixed ({q['fixed']} fixed, {q['answered']} answered, "
                f"{q['findings'] - replied} without a reply, of {q['findings']})"
            )
    lines.append(f"- open PRs in conflict (DIRTY): {pct(dirty)}")
    if flaky:
        lines.append("- flaky jobs (24h: failed, then passed on the same commit):")
        for f in flaky:
            link = f" <{f['url']}|run>" if f.get("url") else ""
            lines.append(f"  - `{f['job']}` ({f['workflow'].rsplit('/', 1)[-1]}) x{f['count']}{link}")
    return "\n".join(lines)


class Api:
    def __init__(self, repo: str, token: str):
        self.repo = repo
        self.headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "devflow-tick",
        }
        self.permissions: dict[str, bool] = {}  # login -> has write, for one tick

    def request(self, method: str, url: str, body: dict | None = None, token: str | None = None):
        """(json, Link header)."""
        headers = dict(self.headers)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = json.dumps(body).encode() if body is not None else None
        if not url.startswith("https://"):
            url = f"https://api.github.com{url}"
        if not url.startswith("https://api.github.com/"):
            raise ValueError(f"refusing to send the token to {url.split('?')[0]}")
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=60) as res:
                    raw = res.read()
                    return (json.loads(raw) if raw else None), res.headers.get("Link", "")
            except (TimeoutError, urllib.error.URLError, http.client.IncompleteRead) as err:
                # Reads only: a write is never retried, so it is never doubled.
                transient = not isinstance(err, urllib.error.HTTPError) or err.code in (502, 503, 504)
                if method != "GET" or not transient or attempt == 2:
                    raise
                time.sleep(2 ** attempt)
        raise AssertionError("unreachable")

    def call(self, method: str, path: str, body: dict | None = None, token: str | None = None):
        return self.request(method, path, body, token)[0]

    def get(self, path: str):
        return self.call("GET", f"/repos/{self.repo}{path}")

    def get_all(self, path: str, key: str | None = None) -> list:
        """Every page of a list endpoint, following Link rel="next"."""
        items, url = [], f"/repos/{self.repo}{path}"
        for _ in range(MAX_PAGES):
            page, link = self.request("GET", url)
            items.extend(page[key] if key else page)
            match = re.search(r'<([^>]+)>;\s*rel="next"', link or "")
            if not match:
                return items
            url = match.group(1)
        raise RuntimeError(f"more than {MAX_PAGES} pages at {path}")

    def graphql(self, query: str):
        return self.call("POST", "/graphql", {"query": query})

    def writers(self, logins: set[str]) -> set[str]:
        """The logins with admin, maintain or write on the repo, each read once per tick."""
        cache = self.permissions
        for login in sorted(logins - set(cache) - {""}):
            try:
                data = self.get(f"/collaborators/{urllib.parse.quote(login)}/permission") or {}
            except urllib.error.HTTPError as err:
                if err.code != 404:
                    raise
                data = {}
            cache[login] = data.get("role_name") in WRITE or data.get("permission") in WRITE
        return {login for login in logins if cache.get(login)}


def list_runs(api: Api, workflow: str, since: datetime, until: datetime, event: str = "merge_group") -> list[dict]:
    """Every run of a workflow created in [since, until], of one event ("" is
    every event). A filtered listing stops at LISTING_CAP results, so a fuller
    window is split in two."""
    created = urllib.parse.quote(f"{iso(since)}..{iso(until)}")
    # Every event's runs make large pages, which GitHub answered with 502s
    # (26 Nov, read-only run): smaller pages for that listing.
    size = 100 if event else 30
    path = f"/actions/workflows/{workflow}/runs?{f'event={event}&' if event else ''}per_page={size}&created={created}"
    first = api.get(path)
    if first.get("total_count", 0) >= LISTING_CAP and until - since > timedelta(minutes=2):
        mid = since + (until - since) / 2
        return list_runs(api, workflow, since, mid, event) + list_runs(api, workflow, mid + timedelta(seconds=1), until, event)
    return api.get_all(path, "workflow_runs")


def collect_flaky(api: Api, now: datetime, runs: list[dict], failures: list[dict], jobs_by_run, commits: list[dict]) -> list[dict]:
    """top_flaky() over the stats window: re-runs of the same commit, and queue
    failures (cascades removed) whose pull request landed with the tested head."""
    reruns = [
        r for wf in RERUN_WORKFLOWS for r in list_runs(api, wf, now - STATS_WINDOW, now, event="")
        if (r.get("run_attempt") or 1) > 1
    ]
    jobs_all = {r["id"]: api.get_all(f"/actions/runs/{r['id']}/jobs?filter=all&per_page=100", "jobs") for r in reruns}
    # A merge landing's second parent is the head that landed.
    landed = {land["own"]: c["parents"][1] for c in commits if len(c.get("parents") or []) == 2
              for land in [landing(c)] if land["own"]}
    mine = [f for f in independent(failures, runs, jobs_by_run) if f.get("pr") in landed]
    by_id = {r["id"]: r for r in runs}
    group_heads = {}
    for run_id in {f["run"] for f in mine}:
        try:
            parents = (api.get(f"/commits/{by_id[run_id]['head_sha']}") or {}).get("parents") or []
        except urllib.error.HTTPError as err:
            # A group commit nothing refers to any more can be gone: not counted.
            print(f"flaky: group commit of run {run_id} unreadable ({err.code})")
            continue
        if len(parents) == 2:
            group_heads[run_id] = parents[1]["sha"]
    return top_flaky(rerun_flakes(reruns, jobs_all) + queue_flakes(mine, runs, group_heads, landed), FLAKY_TOP)


def collect_queue(api: Api, now: datetime, breaker_only: bool):
    window = BREAKER_WINDOW if breaker_only else STATS_WINDOW
    runs, jobs_by_run = [], {}
    for wf in QUEUE_WORKFLOWS:
        runs.extend(list_runs(api, wf, now - window, now))
    runs.sort(key=lambda r: r["created_at"], reverse=True)
    for r in runs:
        if wants_jobs(r, now, breaker_only):
            jobs_by_run[r["id"]] = api.get_all(f"/actions/runs/{r['id']}/jobs?filter=latest&per_page=100", "jobs")
    return runs, queue_failures(runs, jobs_by_run), jobs_by_run


def collect_dev(api: Api) -> tuple[list[dict], list[dict]]:
    """(runs, failures) of the two newest finished dev push runs of each queue
    suite; runs holds only the newest. A green newest run clears a dev trip,
    so the run before it is read only when the newest failed."""
    runs, failures = [], []
    for wf, prefix in DEV_PUSH_WORKFLOWS.items():
        listing = api.get(f"/actions/workflows/{wf}/runs?event=push&branch=dev&status=completed&per_page=20")
        done = [r for r in listing.get("workflow_runs") or [] if r.get("conclusion") in ("success", "failure")]
        done.sort(key=lambda r: r["created_at"], reverse=True)
        if not done:
            continue
        runs.append(done[0])
        for rank, run in enumerate(done[:2]):
            if run["conclusion"] != "failure":
                break
            jobs = {run["id"]: api.get_all(f"/actions/runs/{run['id']}/jobs?filter=latest&per_page=100", "jobs")}
            failures += queue_failures([run], jobs, prefix, dev=True, prior=rank == 1)
    return runs, failures


def pr_details(api: Api, numbers: list[int]) -> dict[int, dict]:
    """{pr: {"body", "engine", "quality"}} in batches of 25 per GraphQL query."""
    owner, name = api.repo.split("/")
    out: dict[int, dict] = {}
    numbers = sorted(set(numbers))
    for i in range(0, len(numbers), 25):
        fields = " ".join(
            f"p{n}: pullRequest(number: {n}) {{ number body author {{ login }} "
            "reviews(last: 20) { nodes { databaseId body submittedAt author { login } } } "
            "comments(last: 50) { nodes { body author { login } } } }"
            for n in numbers[i:i + 25]
        )
        data = api.graphql(f'{{ repository(owner: "{owner}", name: "{name}") {{ {fields} }} }}')
        for node in ((data or {}).get("data") or {}).get("repository", {}).values():
            if node:
                reviews = node["reviews"]["nodes"]
                writers = api.writers({reviewer(r) for r in reviews if REVIEW_MARKER in (r.get("body") or "")})
                out[node["number"]] = {
                    "body": node.get("body") or "",
                    "engine": review_engine(reviews, writers),
                    "quality": review_quality(
                        reviews, node["comments"]["nodes"], (node.get("author") or {}).get("login") or "", writers
                    ),
                }
    return out


def open_pr_mergeables(api: Api) -> list[str]:
    out, cursor = [], None
    owner, name = api.repo.split("/")
    for _ in range(5):
        after = f', after: "{cursor}"' if cursor else ""
        q = (f'{{repository(owner:"{owner}", name:"{name}"){{pullRequests(states:OPEN, first:100{after})'
             "{pageInfo{hasNextPage endCursor} nodes{mergeable}}}}")
        page = api.graphql(q)["data"]["repository"]["pullRequests"]
        out += [n["mergeable"] for n in page["nodes"]]
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
    return out


def git_first_parent(n: int) -> list[dict]:
    out = subprocess.run(
        ["git", "log", "--first-parent", f"-{n}", "--format=%H%x1f%P%x1f%s%x1f%b%x1e", "origin/dev"],
        check=True, capture_output=True, text=True,
    ).stdout
    commits = []
    for rec in out.split("\x1e"):
        if rec.strip():
            sha, parents, subject, body = (rec.strip("\n").split("\x1f") + ["", "", ""])[:4]
            commits.append({"sha": sha.strip(), "parents": parents.split(), "subject": subject, "body": body})
    return commits


def open_breaker_issues(api: Api) -> list[dict]:
    """Open issues with the base-red label (the listing also returns pull requests)."""
    items = api.get_all(f"/issues?labels={BREAKER_LABEL}&state=open&per_page=100")
    return [i for i in items if "pull_request" not in i]


def signal_breaker(api: Api, breaker: list[dict], issues: list[dict]) -> list[dict]:
    """Label plus one open tracking issue per failing job; never twice for the same job.

    Returns the entries that are new (no open issue yet).
    """
    open_titles = {i["title"] for i in issues}
    new = [b for b in breaker if BREAKER_TITLE + b["job"] not in open_titles]
    if not new:
        return []
    try:
        api.call("POST", f"/repos/{api.repo}/labels", {"name": BREAKER_LABEL, "color": "b60205",
                 "description": "Same job failing on several merge-queue PRs"})
    except urllib.error.HTTPError:
        pass  # label exists
    for b in new:
        api.call("POST", f"/repos/{api.repo}/issues", {
            "title": BREAKER_TITLE + b["job"], "labels": [BREAKER_LABEL],
            "body": f"`{b['job']}` failed on {trip_where(b)}. "
                    "Groups behind a failing entry are not counted.\n"
                    "Likely a base problem, not the PRs. The batch train pauses while this issue is open. "
                    "The breaker closes it when the job stops tripping and a later queue build passes; "
                    "close it yourself once the base is green.",
        })
    return new


def close_resolved(api: Api, resolved: list[dict]) -> None:
    for r in resolved:
        api.call("POST", f"/repos/{api.repo}/issues/{r['number']}/comments", {
            "body": f"`{r['job']}` no longer trips the breaker, and queue build {r['run']} passed after its last failure. Closing.",
        })
        api.call("PATCH", f"/repos/{api.repo}/issues/{r['number']}", {"state": "closed", "state_reason": "completed"})


def trip_where(b: dict) -> str:
    where = [f"{len(b['prs'])} independent merge-queue PRs ({', '.join(f'#{n}' for n in b['prs'])})"] if b["prs"] else []
    dev = {1: ["the newest dev push run"], 2: ["the two newest dev push runs"]}.get(b.get("dev"), [])
    return " and ".join(where + dev)


def breaker_line(b: dict) -> str:
    return f":rotating_light: base looks red: `{b['job']}` failed on {trip_where(b)}"


def set_variable(api: Api, token: str, value: str) -> None:
    path = f"/repos/{api.repo}/actions/variables"
    try:
        api.call("PATCH", f"{path}/{VARIABLE_NAME}", {"name": VARIABLE_NAME, "value": value}, token)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        api.call("POST", path, {"name": VARIABLE_NAME, "value": value}, token)


def post_slack(url: str, text: str) -> None:
    req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(), headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=30).read()


def run_breaker(api: Api, now: datetime, runs, failures, jobs_by_run, dry: bool) -> tuple[list[dict], list[dict]]:
    """(tripped, new trips). Files new trips and closes resolved ones unless dry."""
    dev_runs, dev_failures = collect_dev(api)
    breaker = detect_breaker(independent(failures, runs, jobs_by_run) + dev_failures, now)
    issues = open_breaker_issues(api)
    # Every failure, cascades too, dates the last bad build; a green dev push counts as green.
    resolved = resolved_trips(issues, breaker, failures + dev_failures, runs + dev_runs)
    for r in resolved:
        print(f"base health: {r['job']} recovered (queue build {r['run']} passed)")
    if dry:
        return breaker, []
    new = signal_breaker(api, breaker, issues) if breaker else []
    close_resolved(api, resolved)
    return breaker, new


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    breaker_only = "--breaker-only" in argv
    # The public base-red issues and the Slack posts start only with
    # DEVFLOW_HEARTBEAT=live; until then a tick writes its job summary only.
    live = os.environ.get("DEVFLOW_HEARTBEAT") == "live"
    slack = os.environ.get("SLACK_WEBHOOK_URL") if live else None
    repo, token = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"]
    api, now = Api(repo, token), datetime.now(timezone.utc)
    runs, failures, jobs_by_run = collect_queue(api, now, breaker_only)
    if not live:
        print("DEVFLOW_HEARTBEAT is not live: no base-red issue and no Slack post.")
    # Every push and every queue build: a new trip is filed once (the open
    # issue is the dedupe), and only that is posted.
    breaker, new = run_breaker(api, now, runs, failures, jobs_by_run, dry or not live)
    if breaker_only:
        print("\n".join(breaker_line(b) for b in breaker) or "base health: no trip")
        if new and slack:
            post_slack(slack, "\n".join(breaker_line(b) for b in new))
        return 0
    stats = queue_stats(runs, jobs_by_run, now)
    commits = git_first_parent(ESCAPE_HISTORY)
    window = [land["own"] for land in map(landing, commits[:ESCAPE_WINDOW]) if land["own"]]
    tight_now = os.environ.get("APPROVAL_MODE_TIGHT") == "1"
    first = escape_rate(commits, tight_now=tight_now)
    details = pr_details(api, window + [r for e in first["escapes"] for r in e["refs"]])
    esc = escape_rate(commits, details, tight_now)
    # Culprits found only through a description need their engine too.
    late = {r for e in esc["escapes"] for r in e["refs"]} - set(details)
    if late:
        details.update(pr_details(api, sorted(late)))
        esc = escape_rate(commits, details, tight_now)
    landings = len(api.get_all(f"/commits?sha=dev&since={iso(now - timedelta(hours=1))}&per_page=100"))
    dirty = dirty_share(open_pr_mergeables(api))
    try:
        flaky = collect_flaky(api, now, runs, failures, jobs_by_run, commits)
    except (OSError, http.client.HTTPException, ValueError) as err:
        # The flaky list is a report: its failure must not stop the digest or
        # the tight-mode write below.
        print(f"::warning::flaky jobs not read: {err}")
        flaky = []
    text = render_digest(landings, stats, esc, dirty, breaker, flaky, quality_by_engine(details, window))
    print(text)
    for e in esc["escapes"]:
        print(f"  escape {e['kind']}: #{e['pr']} {e['subject'][:90]} -> {', '.join(f'#{n}' for n in e['refs']) or 'no PR named'}")
    if dry:
        return 0
    vars_token = os.environ.get("DEVFLOW_VARS_TOKEN")
    if vars_token:
        set_variable(api, vars_token, "1" if esc["tight"] else "0")
    elif esc["tight"] != tight_now:
        want = "1" if esc["tight"] else "0"
        print(f"::warning::escape rate {pct(esc['rate'])}: set {VARIABLE_NAME}={want} (no DEVFLOW_VARS_TOKEN to do it)")
    if slack:
        post_slack(slack, text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
