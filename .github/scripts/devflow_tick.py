"""Dev-push heartbeat: base-health breaker, escape rate and the merge digest.

Runs from pr-digest.yml on every push to dev (at most once an hour, the
workflow gates it). It reads the Actions and git history, decides three
things and says them in one Slack message:

- Breaker: the same job failed on merge-queue builds of >= 3 different pull
  requests inside the window. The base is probably red; people see a label
  and a tracking issue, plus a Slack line.
- Escape rate: reverts and fix-forwards among the last 100 first-parent
  merges on dev. Above the threshold the tight-approval mode is wanted.
- Digest: landings, queue red rate by class, queue p90, runner wait p95,
  escape rate and the share of open pull requests that conflict.

The repo variable APPROVAL_MODE_TIGHT can only be written with a token that
has the Variables permission, which GITHUB_TOKEN never has. With
DEVFLOW_VARS_TOKEN set the script writes it; without it the script only
reports the signal. It never pauses the merge queue.

All functions that decide are pure and take plain dicts, so the tests feed
recorded shapes. Only `Api` and `main` touch the network or git.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone

QUEUE_WORKFLOWS = ("backend-queue-ci.yml", "e2e-ci.yml")
BREAKER_WINDOW = timedelta(hours=2)
BREAKER_MIN_PRS = 3
ESCAPE_WINDOW = 100
ESCAPE_THRESHOLD = 0.02
ESCAPE_HISTORY = 400
INFRA_JOB_PATTERN = re.compile(
    r"(runner|provision|setup|set up|checkout|cache|image|build|docker|stack|services?)",
    re.IGNORECASE,
)
BREAKER_LABEL = "base-red"
VARIABLE_NAME = "APPROVAL_MODE_TIGHT"
QUEUE_BRANCH = re.compile(r"^gh-readonly-queue/[^/]+/pr-(\d+)-")
FIX_REF = re.compile(
    r"\b(?:fix(?:es|ed)?|follow[- ]?ups?(?:\s+(?:to|of|for))?|revert(?:s|ed)?)\s+"
    r"(?:pr\s*)?#(\d+)",
    re.IGNORECASE,
)
OWN_PR = re.compile(r"\(#(\d+)\)\s*$|^Merge pull request #(\d+)")


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(pct / 100 * len(ordered)) - 1)]


def pr_number(run: dict) -> int | None:
    match = QUEUE_BRANCH.match(run.get("head_branch") or "")
    return int(match.group(1)) if match else None


def is_infra(job_name: str) -> bool:
    return bool(INFRA_JOB_PATTERN.search(job_name))


def detect_breaker(failures: list[dict], now: datetime) -> list[dict]:
    """failures: {job, pr, at} per failed job on a merge-queue run.

    Returns one entry per job name failing for >= BREAKER_MIN_PRS different
    pull requests inside the window, worst first.
    """
    prs_by_job: dict[str, set[int]] = defaultdict(set)
    for f in failures:
        if f["pr"] is not None and now - parse_ts(f["at"]) <= BREAKER_WINDOW:
            prs_by_job[f["job"]].add(f["pr"])
    tripped = [
        {"job": job, "prs": sorted(prs)}
        for job, prs in prs_by_job.items()
        if len(prs) >= BREAKER_MIN_PRS
    ]
    return sorted(tripped, key=lambda t: (-len(t["prs"]), t["job"]))


def _own_pr(subject: str) -> int | None:
    m = OWN_PR.search(subject)
    return int(m.group(1) or m.group(2)) if m else None


def escape_rate(commits: list[dict]) -> dict:
    """commits: newest-first first-parent {sha, subject, body} on dev.

    The window is the newest ESCAPE_WINDOW commits. Older commits only tell
    which #N are pull requests, so "fixes #N" on an issue is not counted.
    """
    window = commits[:ESCAPE_WINDOW]
    known_prs = {n for n in (_own_pr(c["subject"]) for c in commits) if n}
    escapes = []
    for c in window:
        own = _own_pr(c["subject"])
        text = f"{c['subject']}\n{c.get('body', '')}"
        refs = {int(n) for n in FIX_REF.findall(text)}
        refs = {n for n in refs if n in known_prs and n != own and (own is None or n < own)}
        if c["subject"].startswith("Revert ") or refs:
            escapes.append({"sha": c["sha"][:10], "subject": c["subject"], "refs": sorted(refs)})
    total = len(window)
    rate = len(escapes) / total if total else 0.0
    return {"count": len(escapes), "total": total, "rate": rate, "tight": rate > ESCAPE_THRESHOLD, "escapes": escapes}


def queue_stats(runs: list[dict], jobs_by_run: dict[int, list[dict]], now: datetime, hours: int = 24) -> dict:
    """Red rate by class, queue p90 and runner wait p95 over completed merge_group runs."""
    since = now - timedelta(hours=hours)
    done = [r for r in runs if r.get("conclusion") in ("success", "failure") and parse_ts(r["created_at"]) >= since]
    red = {"infra": 0, "other": 0}
    for r in done:
        if r["conclusion"] != "failure":
            continue
        failed = [j["name"] for j in jobs_by_run.get(r["id"], []) if j.get("conclusion") == "failure"]
        red["infra" if failed and all(is_infra(n) for n in failed) else "other"] += 1
    waits = [(parse_ts(r["updated_at"]) - parse_ts(r["created_at"])).total_seconds() for r in done]
    runner_waits = [
        (parse_ts(j["started_at"]) - parse_ts(j["created_at"])).total_seconds()
        for js in jobs_by_run.values()
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


def render_digest(landings: int, stats: dict, esc: dict, dirty: float | None, breaker: list[dict]) -> str:
    lines = ["*dev flow, last hour*"]
    for b in breaker:
        prs = ", ".join(f"#{n}" for n in b["prs"])
        lines.append(f":rotating_light: base looks red: `{b['job']}` failed on {len(b['prs'])} PRs in the queue ({prs})")
    lines.append(f"- landings in the last hour: {landings}")
    lines.append(
        f"- queue red rate (24h, {stats['runs']} runs): {pct(stats['red_rate'])} "
        f"(infra {stats['red']['infra']}, other {stats['red']['other']})"
    )
    lines.append(f"- queue run time p90: {fmt_dur(stats['queue_p90_s'])}")
    lines.append(f"- runner job wait p95: {fmt_dur(stats['runner_p95_s'])}")
    flag = " :warning: above 2%, tight approval wanted" if esc["tight"] else ""
    lines.append(f"- escape rate (last {esc['total']} merges): {pct(esc['rate'])} ({esc['count']}){flag}")
    lines.append(f"- open PRs in conflict (DIRTY): {pct(dirty)}")
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

    def call(self, method: str, path: str, body: dict | None = None, token: str | None = None):
        headers = dict(self.headers)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"https://api.github.com{path}", data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as res:
            raw = res.read()
            return json.loads(raw) if raw else None

    def get(self, path: str):
        return self.call("GET", f"/repos/{self.repo}{path}")

    def graphql(self, query: str):
        return self.call("POST", "/graphql", {"query": query})


def git_first_parent(n: int) -> list[dict]:
    out = subprocess.run(
        ["git", "log", "--first-parent", f"-{n}", "--format=%H%x1f%s%x1f%b%x1e", "origin/dev"],
        check=True, capture_output=True, text=True,
    ).stdout
    commits = []
    for rec in out.split("\x1e"):
        if rec.strip():
            sha, subject, body = (rec.strip("\n").split("\x1f") + ["", ""])[:3]
            commits.append({"sha": sha.strip(), "subject": subject, "body": body})
    return commits


def collect_queue(api: Api, now: datetime):
    runs, failures, jobs_by_run = [], [], {}
    since = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for wf in QUEUE_WORKFLOWS:
        data = api.get(f"/actions/workflows/{wf}/runs?event=merge_group&per_page=100&created=>={since}")
        runs.extend(data.get("workflow_runs", []))
    runs.sort(key=lambda r: r["created_at"], reverse=True)
    for r in runs[:60]:
        if r.get("status") != "completed":
            continue
        jobs = api.get(f"/actions/runs/{r['id']}/jobs?filter=latest&per_page=100").get("jobs", [])
        jobs_by_run[r["id"]] = jobs
        for j in jobs:
            if j.get("conclusion") == "failure":
                failures.append({"job": j["name"], "pr": pr_number(r), "at": j.get("completed_at") or r["updated_at"]})
    return runs, failures, jobs_by_run


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


def signal_breaker(api: Api, breaker: list[dict]) -> None:
    """Label plus one open tracking issue per failing job; never twice for the same job."""
    try:
        api.call("POST", f"/repos/{api.repo}/labels", {"name": BREAKER_LABEL, "color": "b60205",
                 "description": "Same job failing on several merge-queue PRs"})
    except urllib.error.HTTPError:
        pass  # label exists
    open_titles = {i["title"] for i in api.get(f"/issues?labels={BREAKER_LABEL}&state=open&per_page=100")}
    for b in breaker:
        title = f"Base looks red: {b['job']}"
        if title in open_titles:
            continue
        prs = ", ".join(f"#{n}" for n in b["prs"])
        api.call("POST", f"/repos/{api.repo}/issues", {
            "title": title, "labels": [BREAKER_LABEL],
            "body": f"`{b['job']}` failed on merge-queue builds of {len(b['prs'])} different PRs in the last 2 hours: {prs}.\n"
                    "Likely a base problem, not the PRs. Close this issue when the base is green again.",
        })


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


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    repo, token = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"]
    api, now = Api(repo, token), datetime.now(timezone.utc)
    runs, failures, jobs_by_run = collect_queue(api, now)
    breaker = detect_breaker(failures, now)
    stats = queue_stats(runs, jobs_by_run, now)
    esc = escape_rate(git_first_parent(ESCAPE_HISTORY))
    hour_ago = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    landings = len(api.get(f"/commits?sha=dev&since={hour_ago}&per_page=100"))
    dirty = dirty_share(open_pr_mergeables(api))
    text = render_digest(landings, stats, esc, dirty, breaker)
    print(text)
    if dry:
        return 0
    if breaker:
        signal_breaker(api, breaker)
    vars_token = os.environ.get("DEVFLOW_VARS_TOKEN")
    if vars_token:
        set_variable(api, vars_token, "1" if esc["tight"] else "0")
    elif esc["tight"]:
        print(f"::warning::escape rate {pct(esc['rate'])} is above 2%; set {VARIABLE_NAME}=1 (no DEVFLOW_VARS_TOKEN to do it)")
    if os.environ.get("SLACK_WEBHOOK_URL"):
        post_slack(os.environ["SLACK_WEBHOOK_URL"], text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
