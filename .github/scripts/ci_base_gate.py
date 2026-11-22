"""The "CI ran on this base" check for pull requests (not a required check).

Backend CI and E2E CI start only on opened, synchronize and reopened: a run for
an edit or a label would become their newest suite and hide their required
checks. A retarget (by hand, or GitHub's automatic one when a stacked PR's base
branch is merged and deleted) therefore starts no run of them, and their
newest suites stay planned against the old base. Into dev the merge queue
re-tests every merge. Main has no queue, so this check turns red there until a
push or a reopen runs CI against the new base.

The verdict is recomputed from the API on every run; nothing is copied from
another check, and nothing is written:

- no base change since the PR opened: ok;
- otherwise stale when the newest base change (timeline events
  base_ref_changed and automatic_base_change_succeeded) is later than the
  created_at of the newest pull_request run of Backend CI or of E2E CI for the
  head commit, or when either has no such run. A re-run keeps created_at, and
  re-tests the old merge commit, so it does not make a stale PR fresh.

On a push or reopen the runs this same event starts may not be listed yet, so
the check polls for them before judging. On base main a stale verdict or an
API error fails; on any other base it passes with a notice saying what it
would do on main.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

API = "https://api.github.com"
WORKFLOWS = (".github/workflows/backend-ci.yml", ".github/workflows/e2e-ci.yml")
BASE_EVENTS = frozenset({"base_ref_changed", "automatic_base_change_succeeded"})
# Events that start the workflows above, so their runs may still be appearing.
STARTING = frozenset({"opened", "synchronize", "reopened"})
GUARDED_BASE = "main"
POLLS = 6
POLL_SECONDS = 20
MAX_PAGES = 30


def newest_base_change(timeline):
    """The created_at of the newest base change, or None."""
    times = [
        event["created_at"]
        for event in timeline
        if event.get("event") in BASE_EVENTS and event.get("created_at")
    ]
    return max(times) if times else None


def newest_runs(runs, head_sha):
    """The newest pull_request run created_at per guarded workflow."""
    newest = {}
    for run in runs:
        if (
            run.get("event") == "pull_request"
            and run.get("head_sha") == head_sha
            and run.get("path") in WORKFLOWS
        ):
            path = run["path"]
            newest[path] = max(newest.get(path, ""), run["created_at"])
    return newest


def verdict(timeline, runs, head_sha):
    """("ok" | "stale", detail) for one reading of the timeline and runs."""
    change = newest_base_change(timeline)
    if change is None:
        return "ok", "the base has not changed since the pull request opened"
    newest = newest_runs(runs, head_sha)
    behind = [path for path in WORKFLOWS if newest.get(path, "") <= change]
    if behind:
        names = ", ".join(path.rsplit("/", 1)[-1] for path in behind)
        return (
            "stale",
            f"the base changed at {change}, after the newest run of {names}"
            f" for {head_sha[:12]}",
        )
    return "ok", f"Backend CI and E2E CI ran after the base changed at {change}"


def fetch_json(url, token):
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        link = response.headers.get("Link", "")
        return json.load(response), link


def paginate(fetch, url, key=None):
    """Every item of a paginated list endpoint; fails closed past MAX_PAGES."""
    items = []
    for _ in range(MAX_PAGES):
        page, link = fetch(url)
        items.extend(page[key] if key else page)
        match = re.search(r'<([^>]+)>;\s*rel="next"', link or "")
        if not match:
            return items
        url = match.group(1)
    raise ValueError(f"more than {MAX_PAGES} pages at {url}")


def decide(fetch, *, repo, pr, head_sha, base_ref, action, sleep=time.sleep):
    """(exit code, annotation lines) for this pull request now."""
    guarded = base_ref == GUARDED_BASE
    timeline_url = f"{API}/repos/{repo}/issues/{pr}/timeline?per_page=100"
    runs_url = (
        f"{API}/repos/{repo}/actions/runs"
        f"?head_sha={head_sha}&event=pull_request&per_page=100"
    )
    try:
        timeline = paginate(fetch, timeline_url)
        polls = POLLS if action in STARTING else 0
        for attempt in range(polls + 1):
            runs = paginate(fetch, runs_url, "workflow_runs")
            state, detail = verdict(timeline, runs, head_sha)
            if state == "ok" or attempt == polls:
                break
            sleep(POLL_SECONDS)
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as exc:
        text = f"could not read the pull request's timeline or CI runs: {exc}"
        if guarded:
            return 1, [f"::error title=CI ran on this base::{text}"]
        return 0, [f"::warning title=CI ran on this base::{text}"]
    if state == "ok":
        return 0, [f"::notice title=CI ran on this base::{detail}"]
    fix = (
        "Push a commit, or close and reopen the pull request, to run Backend CI"
        " and E2E CI against the new base. A push dismisses approvals; closing"
        " turns auto-merge off and takes the pull request out of the queue."
    )
    if guarded:
        return 1, [f"::error title=CI ran on this base::{detail}. {fix}"]
    return 0, [
        f"::notice title=CI ran on this base::{detail}. Into {base_ref} this"
        " passes: the merge queue re-tests every merge into dev. Into main it"
        f" would fail. {fix}"
    ]


def main():
    token = os.environ["GH_TOKEN"]
    code, lines = decide(
        lambda url: fetch_json(url, token),
        repo=os.environ["REPO"],
        pr=os.environ["PR"],
        head_sha=os.environ["HEAD_SHA"],
        base_ref=os.environ["BASE_REF"],
        action=os.environ.get("ACTION", ""),
    )
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
