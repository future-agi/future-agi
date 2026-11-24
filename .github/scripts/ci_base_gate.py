"""The "CI ran on this base" check for pull requests (advisory, not required).

Backend CI and E2E CI start only on opened, synchronize and reopened: a run
for an edit or a label would become their newest suite and hide their
required checks. So a retarget starts neither. Retarget proof
(retarget-proof.yml) runs both, unchanged, when a pull request's base really
changes, and every Backend CI or E2E CI run that tested a pull request records
the merge candidate it tested (artifact ci-candidate-<kind>-attempt-<n>).

For a retargeted pull request into main this check passes only when, read
from the API now:

- GitHub's merge candidate is the head H merged onto the current tip T of
  the base B (parents [T, H]); and
- a Backend CI and an E2E CI producer each succeeded and recorded that same
  pull request, head, base, base tip and merge tree. A receipt from another
  pull request on the same head, from an older base tip, from a run whose
  required aggregate was skipped, or that is missing, does not count.

It re-reads the pull request after judging and fails closed if anything
moved. It only reads; it never writes or copies a check. A green verdict
means the candidate was proved against the base tip at its last run, not at
merge time: the base can move after it. A pull request that was never
retargeted passes, since its own Backend CI and E2E CI ran on its base.
Into other bases it passes and says what it would require into main.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

API = "https://api.github.com"
GUARDED_BASE = "main"
BASE_EVENTS = frozenset({"base_ref_changed", "automatic_base_change_succeeded"})
# Producer workflow path -> how its jobs prefix the job names below.
PRODUCERS = {
    "backend": {
        ".github/workflows/backend-ci.yml": "",
        ".github/workflows/retarget-proof.yml": "retarget-backend / ",
    },
    "e2e": {
        ".github/workflows/e2e-ci.yml": "",
        ".github/workflows/retarget-proof.yml": "retarget-e2e / ",
    },
}
AGGREGATE = {"backend": "Backend Tests Pass", "e2e": "E2E Tests Pass"}
RECEIPT_JOB = "Record the tested merge candidate"
IDENTITY_POLLS = 12
IDENTITY_SECONDS = 10
# Into main, wait for this head's producer runs before judging.
EVIDENCE_POLLS = 120
EVIDENCE_SECONDS = 60
MAX_PAGES = 30


class Unknown(Exception):
    """A fact the verdict needs could not be established; fail closed."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class GitHub:
    """Read-only REST access. Artifact downloads never forward the token."""

    def __init__(self, token):
        self.token = token
        self.bare = urllib.request.build_opener(_NoRedirect)

    def _request(self, url):
        return urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def json(self, path):
        url = path if path.startswith("https://") else API + path
        with urllib.request.urlopen(self._request(url), timeout=30) as response:
            return json.load(response), response.headers.get("Link", "")

    def download(self, url):
        try:
            with self.bare.open(self._request(url), timeout=60) as response:
                return response.read()
        except urllib.error.HTTPError as redirect:
            location = redirect.headers.get("Location")
            if redirect.code not in (301, 302, 303, 307, 308) or not location:
                raise
        # The signed storage URL needs no token; do not send it there.
        with urllib.request.urlopen(location, timeout=60) as response:
            return response.read()


def pages(fetch, path, key=None):
    """Every item of a paginated list; fails closed past MAX_PAGES."""
    items = []
    for _ in range(MAX_PAGES):
        page, link = fetch(path)
        items.extend(page[key] if key else page)
        match = re.search(r'<([^>]+)>;\s*rel="next"', link or "")
        if not match:
            return items
        path = match.group(1)
    raise Unknown(f"more than {MAX_PAGES} pages at {path}")


def identity(gh, repo, pr, sleep):
    """The pull request's live head, base and GitHub's merge candidate."""
    for attempt in range(IDENTITY_POLLS + 1):
        pull, _ = gh.json(f"/repos/{repo}/pulls/{pr}")
        if pull.get("state") != "open":
            raise Unknown("the pull request is not open")
        head, base = pull["head"]["sha"], pull["base"]["ref"]
        if pull.get("mergeable") is False:
            raise Unknown(
                "the pull request conflicts with its base, so there is no merge candidate"
            )
        merge = pull.get("merge_commit_sha")
        if pull.get("mergeable") is True and merge:
            branch, _ = gh.json(
                f"/repos/{repo}/branches/{urllib.parse.quote(base, safe='')}"
            )
            tip = branch["commit"]["sha"]
            commit, _ = gh.json(f"/repos/{repo}/commits/{merge}")
            if [parent["sha"] for parent in commit["parents"]] == [tip, head]:
                return {
                    "head": head,
                    "base_ref": base,
                    "tip": tip,
                    "merge_tree": commit["commit"]["tree"]["sha"],
                }
        if attempt < IDENTITY_POLLS:
            sleep(IDENTITY_SECONDS)
    raise Unknown("GitHub has not computed a merge of the head onto the base tip")


def retargeted(gh, repo, pr, base_changed):
    """Whether the base ever changed. This event's own payload counts too, so
    a timeline that has not caught up yet cannot hide a retarget."""
    if base_changed:
        return True
    timeline = pages(gh.json, f"/repos/{repo}/issues/{pr}/timeline?per_page=100")
    return any(event.get("event") in BASE_EVENTS for event in timeline)


def read_receipt(gh, repo, run_id, name):
    artifacts = [
        artifact
        for artifact in pages(
            gh.json,
            f"/repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100",
            "artifacts",
        )
        if artifact.get("name") == name and not artifact.get("expired")
    ]
    if len(artifacts) != 1:
        raise ValueError(f"expected one artifact {name}, found {len(artifacts)}")
    blob = gh.download(artifacts[0]["archive_download_url"])
    if artifacts[0].get("digest") != "sha256:" + hashlib.sha256(blob).hexdigest():
        raise ValueError(f"artifact {name} does not match its digest")
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        if archive.namelist() != ["receipt.json"]:
            raise ValueError(f"artifact {name} holds {archive.namelist()}")
        return json.loads(archive.read("receipt.json"))


def check_run(gh, repo, pr, now, kind, prefix, run):
    """(True, "") when this run proved the current candidate for `kind`."""
    jobs = pages(
        gh.json,
        f"/repos/{repo}/actions/runs/{run['id']}/jobs?filter=latest&per_page=100",
        "jobs",
    )
    aggregate = [job for job in jobs if job.get("name") == prefix + AGGREGATE[kind]]
    if len(aggregate) != 1 or aggregate[0].get("conclusion") != "success":
        state = aggregate[0].get("conclusion") if len(aggregate) == 1 else "absent"
        return False, f"{AGGREGATE[kind]} is {state or 'still running'}"
    recorded = [job for job in jobs if job.get("name") == prefix + RECEIPT_JOB]
    if len(recorded) != 1 or recorded[0].get("conclusion") != "success":
        return (
            False,
            "it recorded no merge candidate (the suite did not run, or not yet)",
        )
    attempt = recorded[0]["run_attempt"]
    try:
        receipt = read_receipt(
            gh, repo, run["id"], f"ci-candidate-{kind}-attempt-{attempt}"
        )
    except (ValueError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        return False, str(exc)
    expected = {
        "schema": 1,
        "kind": kind,
        "pr": int(pr),
        "head_sha": now["head"],
        "base_ref": now["base_ref"],
        "base_sha": now["tip"],
        "merge_tree": now["merge_tree"],
        "run_id": run["id"],
        "run_attempt": attempt,
    }
    for key, value in expected.items():
        if receipt.get(key) != value:
            return False, f"it tested {key} {receipt.get(key)!r}; now {value!r}"
    return True, ""


def evidence(gh, repo, pr, now):
    """({kind: run id}, [rejections], whether a producer run is still going)."""
    runs = [
        run
        for run in pages(
            gh.json,
            f"/repos/{repo}/actions/runs?head_sha={now['head']}&event=pull_request&per_page=100",
            "workflow_runs",
        )
        if run.get("head_sha") == now["head"] and run.get("event") == "pull_request"
    ]
    found, rejected, pending = {}, [], False
    for kind, paths in PRODUCERS.items():
        mine = [run for run in runs if run.get("path") in paths]
        for run in sorted(mine, key=lambda run: run["id"], reverse=True):
            pending = pending or run.get("status") != "completed"
            proved, why = check_run(gh, repo, pr, now, kind, paths[run["path"]], run)
            if proved:
                found[kind] = run["id"]
                break
            rejected.append(f"{kind} run {run['id']}: {why}")
    return found, rejected, pending


FIX = (
    "Push a commit, or close and reopen the pull request, to run Backend CI and"
    " E2E CI on the new base (a push dismisses approvals; closing turns auto-merge"
    " off and takes the pull request out of the queue)."
)


def decide(gh, *, repo, pr, base_changed, sleep=time.sleep):
    """(exit code, annotation lines) for this pull request now."""
    title = "::{} title=CI ran on this base::"
    guarded = True  # Until the base is known, fail closed.
    try:
        now = identity(gh, repo, pr, sleep)
        guarded = now["base_ref"] == GUARDED_BASE
        if not retargeted(gh, repo, pr, base_changed):
            return 0, [
                title.format("notice")
                + "The base never changed, so this pull request's own Backend CI"
                " and E2E CI ran on it."
            ]
        polls = EVIDENCE_POLLS if guarded else 0
        for attempt in range(polls + 1):
            found, rejected, pending = evidence(gh, repo, pr, now)
            if len(found) == len(PRODUCERS) or not pending or attempt == polls:
                break
            sleep(EVIDENCE_SECONDS)
        if identity(gh, repo, pr, sleep) != now:
            raise Unknown("the pull request or its base moved while it was judged")
    except (
        Unknown,
        urllib.error.URLError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
    ) as exc:
        text = f"Could not prove the merge candidate: {exc}."
        if guarded:
            return 1, [title.format("error") + text]
        return 0, [title.format("warning") + text]
    proved = (
        f"head {now['head'][:12]} merged onto {now['base_ref']} at"
        f" {now['tip'][:12]} (tree {now['merge_tree'][:12]})"
    )
    if len(found) == len(PRODUCERS):
        return 0, [
            title.format("notice")
            + f"Backend CI run {found['backend']} and E2E CI run {found['e2e']}"
            f" tested {proved}. This is proved against the base tip at this run,"
            " not at merge time: the base can move after it."
        ]
    missing = ", ".join(kind for kind in PRODUCERS if kind not in found)
    why = "; ".join(rejected[:6]) or "no producer run for this head"
    text = (
        f"The base changed, and no successful {missing} run tested {proved}"
        f" ({why}). {FIX}"
    )
    if guarded:
        return 1, [title.format("error") + text]
    return 0, [
        title.format("notice")
        + f"Into {now['base_ref']} this passes: the merge queue re-tests every"
        f" merge into dev. Into main it would fail: {text}"
    ]


def main():
    code, lines = decide(
        GitHub(os.environ["GH_TOKEN"]),
        repo=os.environ["REPO"],
        pr=os.environ["PR"],
        base_changed=os.environ.get("BASE_CHANGED") == "true",
    )
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
