#!/usr/bin/env python3
"""Fail-closed release policy; only the `merge` command performs GitHub writes."""

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import subprocess
import time
from urllib.parse import quote

REPOSITORY = "future-agi/future-agi"
AUTHOR = "futureagi-release-bot[bot]"
# Comments and labels use the workflow token (this login). Approvals never do:
# they come from a dedicated approver App, whose login the workflow passes in.
ACTIONS_BOT = "github-actions[bot]"
# Login of a GitHub App's bot user: the App slug plus "[bot]".
APP_LOGIN = re.compile(r"[a-z0-9][a-z0-9-]{0,99}\[bot\]\Z")
BRANCH = "release-please--branches--main"
LABEL = "autopilot-merged"
APPROVED_LABEL = "autopilot-approved"
STABLE = r"(0|[1-9][0-9]{0,17})\.(0|[1-9][0-9]{0,17})\.(0|[1-9][0-9]{0,17})"
TAG = re.compile(r"v" + STABLE + r"(?:-(?:beta|rc)\.(?:0|[1-9][0-9]{0,17}))?")
SHA = re.compile(r"[0-9a-f]{40}")
# release-please-config.json uses the simple releaser plus the chart version.
MANIFEST = ".release-please-manifest.json"
CHART = "deploy/helm/futureagi/Chart.yaml"
RELEASE_FILES = {"CHANGELOG.md", MANIFEST, "version.txt", CHART}
MAX_BLOB_BYTES = 2_000_000


def unique_mapping(pairs):
    result = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in result:
            raise ValueError("metadata keys must be unique strings")
        result[key] = value
    return result


def chart_without_versions(text, version):
    # Only the merge policy needs PyYAML; the image environment guard stays
    # stdlib-only. Never evaluate YAML tags or execute code from the PR.
    import yaml

    class MetadataLoader(yaml.SafeLoader):
        def compose_node(self, parent, index):
            if self.check_event(yaml.AliasEvent):
                raise ValueError("chart aliases are not release metadata")
            return super().compose_node(parent, index)

        def construct_mapping(self, node, deep=False):
            return unique_mapping((self.construct_object(key, deep=deep),
                                   self.construct_object(value, deep=deep))
                                  for key, value in node.value)

    try:
        chart = yaml.load(text, Loader=MetadataLoader)
    except yaml.YAMLError:
        raise ValueError("chart metadata is malformed") from None
    if (not isinstance(chart, dict) or chart.get("apiVersion") != "v2"
            or chart.get("version") != version or chart.get("appVersion") != f"v{version}"):
        raise ValueError("chart versions do not match the release")
    # Compare bytes apart from the two scalar values, not just parsed objects:
    # no kubeVersion, dependency, annotation, comment or formatting edits slip in.
    pattern = re.compile(r"^(?:version|appVersion):[ \t]*(?P<quote>[\"']?)"
                         r"(?P<version>v?" + STABLE + r")(?P=quote)"
                         r"(?=[ \t]*(?:#[^\r\n]*)?\r?$)", re.MULTILINE)

    def replace(match):
        start, end = match.span("version")
        return match[0][:start - match.start()] + "RELEASE_VERSION" + match[0][end - match.start():]

    normalized, count = pattern.subn(replace, text)
    if count != 2:
        raise ValueError("chart version fields have an unsupported layout")
    return normalized


def metadata_allows(pr, files, metadata, version):
    """Validate immutable, complete file evidence, never PR-supplied patches."""
    try:
        if (metadata["base_sha"] != pr["base"]["sha"]
                or metadata["head_sha"] != pr["head"]["sha"]):
            return False
        before, after = metadata["base"], metadata["head"]
        if set(before) != RELEASE_FILES or set(after) != RELEASE_FILES:
            return False
        for side in (before, after):
            for entry in side.values():
                if entry is None:
                    continue  # Absence was proved by an untruncated tree.
                if (entry["mode"] != "100644" or entry["type"] != "blob"
                        or not SHA.fullmatch(entry["sha"])
                        or not isinstance(entry["text"], str) or not entry["text"]
                        or "\0" in entry["text"]
                        or len(entry["text"].encode("utf-8")) > MAX_BLOB_BYTES):
                    return False
        changed = {path for path in RELEASE_FILES if before[path] != after[path]}
        if changed != {row["filename"] for row in files}:
            return False
        for row in files:
            path = row["filename"]
            if after[path] is None or row["status"] != ("added" if before[path] is None else "modified"):
                return False
        old = json.loads(before[MANIFEST]["text"], object_pairs_hook=unique_mapping)
        new = json.loads(after[MANIFEST]["text"], object_pairs_hook=unique_mapping)
        if (not isinstance(old, dict) or set(old) != {"."}
                or not isinstance(old["."], str) or not re.fullmatch(STABLE, old["."])
                or new != {".": version}
                or tuple(map(int, version.split("."))) <= tuple(map(int, old["."].split(".")))):
            return False
        if (chart_without_versions(before[CHART]["text"], old["."])
                != chart_without_versions(after[CHART]["text"], version)):
            return False
        for side, expected in ((before, old["."]), (after, version)):
            if side["version.txt"] is not None and side["version.txt"]["text"] not in {expected, expected + "\n"}:
                return False
        return True
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError):
        return False


# Branch protection on main requires both since 12 Nov 2026. The configured
# list adds names to this minimum; it can never remove one.
MIN_REQUIRED_CHECKS = ("Backend Tests Pass", "E2E Tests Pass")


def required_checks(config):
    value = config.get("RELEASE_AUTOPILOT_REQUIRED_CHECKS", "")
    names = []
    if value:
        names = [name.strip() for name in re.split(r"[,\n]", value) if name.strip()]
        if not names:
            raise ValueError("required-check list is empty")
    return list(dict.fromkeys([*MIN_REQUIRED_CHECKS, *names]))


def window_allows(now, value):
    if not value:
        return True
    if not re.fullmatch(r"[0-9]{2}-[0-9]{2}", value):
        raise ValueError("hours must be HH-HH in UTC")
    start, end = map(int, value.split("-"))
    if not (0 <= start < 24 and 0 <= end <= 24) or start == end:
        raise ValueError("invalid UTC hours window")
    hour = now.astimezone(timezone.utc).hour
    return start <= hour < end if start < end else hour >= start or hour < end


def result_state(kind, row):
    """One latest check run or status: success, not finished, or did not succeed."""
    if kind == "check":
        if row.get("status") != "completed":
            return "not finished"
        return "success" if row.get("conclusion") == "success" else "did not succeed"
    if row.get("state") == "pending":
        return "not finished"
    return "success" if row.get("state") == "success" else "did not succeed"


def check_results(sha, names, checks, statuses):
    """Latest attempt per check name/app or status context, on this exact SHA.

    Distinct producers with the same name must all succeed. A queued rerun
    supersedes its old green result; skipped and neutral are not success.
    Returns (did_not_succeed, not_finished, no_result) as lists of required
    names, all empty when every name passed, or None when the data is malformed.
    """
    latest = {}
    for row in checks:
        if row.get("head_sha") != sha or row.get("name") not in names:
            continue
        key = (row["name"], "check", row.get("app", {}).get("id"))
        if type(row.get("id")) is not int:
            return None
        if key not in latest or row["id"] > latest[key]["id"]:
            latest[key] = row
    for row in statuses:
        # The adapter binds statuses (whose REST representation omits SHA) to
        # the immutable commit endpoint that returned them.
        if row.get("sha") != sha or row.get("context") not in names:
            continue
        key = (row["context"], "status", None)
        if type(row.get("id")) is not int:
            return None
        if key not in latest or row["id"] > latest[key]["id"]:
            latest[key] = row
    did_not_succeed, not_finished, no_result = [], [], []
    for name in names:
        states = {result_state(kind, row) for (key_name, kind, _), row in latest.items() if key_name == name}
        if "did not succeed" in states:
            did_not_succeed.append(name)
        elif not states:
            # No row at all. An aggregate job's check run appears only once the
            # jobs it needs finish, and a re-run hides the old one until then;
            # a name that nothing reports looks the same, so the reason says both.
            no_result.append(name)
        elif states != {"success"}:
            # A run that is queued or in progress, or a pending status.
            not_finished.append(name)
    return did_not_succeed, not_finished, no_result


def merge_decision(pr, files, checks, statuses, metadata, config, now):
    """Return (allowed, reason); no API, filesystem or clock side effects."""
    if config.get("RELEASE_AUTOPILOT") != "on":
        return False, "RELEASE_AUTOPILOT is not on"
    now = now.astimezone(timezone.utc)
    try:
        names = required_checks(config)
        age = config.get("RELEASE_AUTOPILOT_MIN_AGE_MINUTES", "") or "30"
        if not re.fullmatch(r"[0-9]{1,6}", age):
            raise ValueError("minimum age must be non-negative integer minutes")
        if not window_allows(now, config.get("AUTOPILOT_HOURS_UTC", "")):
            return False, "outside the UTC hours window"
    except ValueError as error:
        return False, str(error)
    if now.weekday() >= 5 and config.get("RELEASE_AUTOPILOT_WEEKENDS") != "on":
        return False, "weekend automation is off"
    if pr.get("state") != "open" or pr.get("draft") is not False or pr.get("merged") is not False:
        return False, "PR is not an open, non-draft, unmerged release"
    if pr.get("user", {}).get("login") != AUTHOR or pr.get("user", {}).get("type") != "Bot":
        return False, "PR author is not the release App"
    head, base = pr.get("head", {}), pr.get("base", {})
    if (head.get("ref") != BRANCH or base.get("ref") != "main"
            or head.get("repo", {}).get("full_name") != REPOSITORY
            or base.get("repo", {}).get("full_name") != REPOSITORY
            or not SHA.fullmatch(head.get("sha", ""))
            or not SHA.fullmatch(base.get("sha", ""))):
        return False, "PR branch, repository or commit SHA is not trusted"
    if not re.fullmatch(r"chore\(main\): release " + STABLE, pr.get("title", "")):
        return False, "PR title is not a stable release"
    labels = pr.get("labels")
    if (not isinstance(labels, list)
            or any(not isinstance(label, dict) or not isinstance(label.get("name"), str)
                   or not label["name"] or label["name"].casefold() == "release-hold" for label in labels)):
        return False, "release-hold is set or labels are unknown"
    if (not isinstance(files, list) or not files or len(files) != pr.get("changed_files")
            or any(not isinstance(row, dict) or row.get("filename") not in RELEASE_FILES
                   or row.get("status") not in {"added", "modified"} for row in files)
            or len({row["filename"] for row in files}) != len(files)):
        return False, "changed files are unknown or outside release metadata"
    # release-please writes one commit and force-pushes it on each rebuild.
    if type(pr.get("commits")) is not int or pr["commits"] != 1:
        return False, "the release PR is not a single release-please commit"
    if not metadata_allows(pr, files, metadata, pr["title"].removeprefix("chore(main): release ")):
        return False, "release metadata contents, versions or file types are not permitted"
    try:
        updated = datetime.fromisoformat(pr["updated_at"].replace("Z", "+00:00"))
        if updated.tzinfo is None:
            raise ValueError("timestamp has no timezone")
        seconds = (now - updated).total_seconds()
    except (KeyError, TypeError, ValueError, AttributeError):
        return False, "PR update time is unknown"
    if seconds < int(age) * 60:
        return False, "PR was updated too recently or has a future update time"
    if pr.get("mergeable") is not True:
        return False, "PR mergeability is false or unknown"
    # A red required check is a wait, not an error: the run stays green and
    # names the check, and a passing re-run lets a later poll merge.
    results = check_results(head["sha"], names, checks, statuses)
    if results is None:
        return False, "required check data is malformed"
    if results[0]:
        return False, (f"required check did not succeed on this head: {', '.join(results[0])}. The autopilot "
                       "waits until a person re-runs it and it passes, or an administrator merges by hand")
    waits = []
    if results[1]:
        waits.append(f"required checks have not finished on this head: {', '.join(results[1])}")
    if results[2]:
        waits.append("required checks have no result on this head yet (their workflow is still running or "
                     f"re-running, or no workflow reports that name): {', '.join(results[2])}")
    if waits:
        return False, "; ".join(waits)
    return True, "release metadata, author, age, hours, hold and current-head checks passed"


CALLOUT_WORKFLOW = ".github/workflows/migrations-callout-check.yml"
# The job that checks release notes. The workflow also runs for PRs into dev,
# where this job is skipped and only its self-test job runs.
CALLOUT_JOB = "check"
ACTIVITY_ROWS = 100


def release_branch_pushes(activity, sha):
    """Allow only a release branch that the release App alone created and pushed.

    Who opened the PR is not enough: a writer's commit on the branch could set
    any higher version. GitHub lists the branch's activity newest first. The
    branch name is reused for every release, so rows before its most recent
    creation, such as the merger's deletion of the previous branch, do not count.
    """
    if not isinstance(activity, list) or not activity or not isinstance(activity[0], dict):
        return False, "the release branch's push history is unknown"
    if activity[0].get("after") != sha:
        return False, "the release branch's newest push is not the PR head yet"
    for row in activity:
        if not isinstance(row, dict) or row.get("ref") != f"refs/heads/{BRANCH}":
            return False, "the release branch's push history is unknown"
        actor = row.get("actor")
        if not isinstance(actor, dict) or actor.get("login") != AUTHOR or actor.get("type") != "Bot":
            return False, "someone other than the release App pushed to the release branch; a person merges this release"
        if row.get("activity_type") == "branch_creation":
            return True, "only the release App created and pushed the release branch"
    return False, f"the release branch's creation is not among its newest {ACTIVITY_ROWS} pushes"


def lists_release_pr(run, number):
    """True if the run lists this release PR, into main, among its pull requests.

    GitHub lists every open PR from the run's head branch on each of its runs,
    so a second PR from the release branch shows on this PR's runs and the
    reverse. This binds a run to the PR but cannot tell the two apart. A
    skipped check job sets apart only a PR into dev (see the doc).
    """
    entries = run.get("pull_requests")
    return isinstance(entries, list) and any(
        isinstance(entry, dict) and type(entry.get("number")) is int and entry["number"] == number
        and isinstance(entry.get("base"), dict) and entry["base"].get("ref") == "main"
        for entry in entries)


def callout_job(run_id):
    """The run's single check job from its latest attempt, or None if unknown."""
    data = api(f"actions/runs/{run_id}/jobs?per_page=100")
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, list) or type(data.get("total_count")) is not int or data["total_count"] != len(jobs):
        return None
    checks = [job for job in jobs if isinstance(job, dict) and job.get("name") == CALLOUT_JOB]
    return checks[0] if len(checks) == 1 else None


def callout_passed(runs, sha, number):
    """Decide on the newest release-branch callout run that ran its check job.

    A completed run whose check job was skipped (a PR not into main, such as
    one into dev) is passed over. The first other run decides: its run and
    check job must both have completed with success. A run still queued or
    running, or one with no check job listed, decides too, so no older pass counts.
    """
    callout = [run for run in runs if run.get("path") == CALLOUT_WORKFLOW and run.get("head_sha") == sha
               and run.get("head_branch") == BRANCH
               and (run.get("head_repository") or {}).get("full_name") == REPOSITORY
               and type(run.get("id")) is int and lists_release_pr(run, number)]
    for run in sorted(callout, key=lambda run: run["id"], reverse=True):
        job = callout_job(run["id"])
        completed = run.get("status") == "completed"
        if (completed and job is not None and job.get("status") == "completed"
                and job.get("conclusion") == "skipped"):
            continue
        return (completed and run.get("conclusion") == "success" and job is not None
                and job.get("status") == "completed" and job.get("conclusion") == "success")
    return False


def release_gates(pr, config, now):
    """Live evidence beyond the PR itself: who pushed, the callout workflow and main's position."""
    sha = pr["head"]["sha"]
    # One page only: this endpoint pages by cursor and ignores page numbers, so
    # pages() would read the same rows again. A release branch's lifetime has a
    # handful of rows; one with more waits for a person.
    activity = api(f"activity?ref={quote('refs/heads/' + BRANCH, safe='')}"
                   f"&direction=desc&per_page={ACTIVITY_ROWS}")
    allowed, reason = release_branch_pushes(activity, sha)
    if not allowed:
        return False, reason
    runs = pages(f"actions/runs?head_sha={sha}&event=pull_request", "workflow_runs")
    # Keep only runs from the same-repository release branch before choosing: a
    # PR from a fork or another branch at the same commit can then neither pass
    # nor block this gate. Any PR from the release branch still counts (see the
    # doc). Identified by workflow path: its job is named "check", like others.
    if type(pr.get("number")) is not int or not callout_passed(runs, sha, pr["number"]):
        return False, "the migrations-callout workflow has not passed on this head"
    main_sha = (api("git/ref/heads/main").get("object") or {}).get("sha", "")
    if not SHA.fullmatch(main_sha):
        return False, "main's tip is unknown"
    if api(f"compare/{main_sha}...{sha}").get("behind_by") == 0:
        return True, "migrations-callout passed and the release PR is current with main"
    # GitHub reports "behind" when main requires up-to-date branches. It would
    # refuse the merge below, so stop here, before any approval is written.
    if pr.get("mergeable_state") == "behind":
        return False, "main requires an up-to-date branch; update the release branch or remove the rule"
    # release-please may not rebuild the PR for hidden commit types. Wait the
    # same cooldown after main last moved, then release what main contains.
    try:
        moved = datetime.fromisoformat(api(f"commits/{main_sha}")["commit"]["committer"]["date"].replace("Z", "+00:00"))
        age = config.get("RELEASE_AUTOPILOT_MIN_AGE_MINUTES", "") or "30"
        if (now.astimezone(timezone.utc) - moved).total_seconds() < int(age) * 60:
            return False, "main moved recently; waiting for the release PR to catch up"
    except (KeyError, TypeError, ValueError, AttributeError):
        return False, "main's last change time is unknown"
    return True, "migrations-callout passed; the release PR is behind main, which has been quiet for the cooldown"


class GitHubError(RuntimeError):
    """A failed GitHub request. It keeps only the HTTP status, never response text."""

    def __init__(self, status=None):
        super().__init__("GitHub API request failed; no further writes attempted")
        self.status = status


def api(path, *, token=None, method="GET", data=None):
    env = dict(os.environ)
    if token is not None:
        env["GH_TOKEN"] = token
    args = ["gh", "api", "--method", method, "-H", "Accept: application/vnd.github+json",
            "-H", "X-GitHub-Api-Version: 2022-11-28", f"repos/{REPOSITORY}/{path}"]
    if data is not None:
        args += ["--input", "-"]
    result = subprocess.run(args, input=json.dumps(data) if data is not None else None,
                            text=True, capture_output=True, env=env, timeout=60)
    if result.returncode:
        # Never echo credentials, response bodies or user-controlled API text.
        # gh prints "gh: <message> (HTTP NNN)". The message can span lines, such
        # as a ruleset refusal, and only fixed hint lines follow it, so gh's own
        # status is the last line that ends in "(HTTP NNN)". An earlier match is
        # message text. Keep only that number.
        statuses = [match[1] for line in (result.stderr or "").splitlines()
                    if (match := re.search(r"\(HTTP ([1-5][0-9]{2})\)\s*\Z", line))]
        raise GitHubError(int(statuses[-1]) if statuses else None)
    return json.loads(result.stdout)


def pages(path, key=None):
    rows = []
    for page in range(1, 101):
        sep = "&" if "?" in path else "?"
        data = api(f"{path}{sep}per_page=100&page={page}")
        batch = data[key] if key else data
        if not isinstance(batch, list):
            raise RuntimeError("unexpected GitHub list response")
        rows.extend(batch)
        if len(batch) < 100:
            return rows
    raise RuntimeError("GitHub pagination limit reached; evidence is incomplete")


def release_metadata(base_sha, head_sha):
    """Read only allowlisted blobs, as data, using immutable Git object APIs.

    The Contents API can dereference symlinks. Trees instead prove file modes
    and absence; untruncated, non-recursive reads avoid repo-size truncation.
    """
    trees, blobs = {}, {}

    def tree(sha):
        if not isinstance(sha, str) or not SHA.fullmatch(sha):
            raise RuntimeError("missing metadata tree SHA")
        if sha not in trees:
            data = api(f"git/trees/{sha}")
            if (data.get("sha") != sha or data.get("truncated") is not False
                    or not isinstance(data.get("tree"), list)):
                raise RuntimeError("metadata tree evidence is incomplete")
            entries = {}
            for entry in data["tree"]:
                path = entry.get("path")
                if not isinstance(path, str) or not path or "/" in path or path in entries:
                    raise RuntimeError("metadata tree entries are malformed")
                entries[path] = entry
            trees[sha] = entries
        return trees[sha]

    def blob(entry):
        sha = entry.get("sha")
        if (entry.get("type") != "blob" or entry.get("mode") != "100644"
                or not isinstance(sha, str) or not SHA.fullmatch(sha)):
            raise RuntimeError("release metadata must be regular non-executable files")
        if sha not in blobs:
            data = api(f"git/blobs/{sha}")
            if (data.get("sha") != sha or data.get("encoding") != "base64"
                    or type(data.get("size")) is not int or not 0 < data["size"] <= MAX_BLOB_BYTES
                    or not isinstance(data.get("content"), str)
                    or len(data["content"]) > MAX_BLOB_BYTES * 2):
                raise RuntimeError("metadata blob evidence is incomplete")
            try:
                raw = base64.b64decode(data["content"].replace("\n", ""), validate=True)
                digest = hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()
                if len(raw) != data["size"] or digest != sha or b"\0" in raw:
                    raise ValueError("invalid blob")
                text = raw.decode("utf-8")
            except ValueError:
                raise RuntimeError("metadata blob evidence is malformed") from None
            blobs[sha] = {"type": "blob", "mode": "100644", "sha": sha, "text": text}
        return blobs[sha]

    def at_commit(sha):
        if not isinstance(sha, str) or not SHA.fullmatch(sha):
            raise RuntimeError("missing metadata commit SHA")
        commit = api(f"git/commits/{sha}")
        if commit.get("sha") != sha:
            raise RuntimeError("metadata commit evidence is incomplete")
        root = commit["tree"]["sha"]
        result = {}
        for path in sorted(RELEASE_FILES):
            current = root
            parts = path.split("/")
            result[path] = None
            for index, part in enumerate(parts):
                entry = tree(current).get(part)
                if entry is None:
                    break
                if index == len(parts) - 1:
                    result[path] = blob(entry)
                else:
                    if entry.get("type") != "tree" or entry.get("mode") != "040000":
                        raise RuntimeError("metadata parent is not a directory")
                    current = entry["sha"]
        return result

    return {"base_sha": base_sha, "head_sha": head_sha,
            "base": at_commit(base_sha), "head": at_commit(head_sha)}


def snapshot(number):
    pr = api(f"pulls/{number}")
    sha, base_sha = pr.get("head", {}).get("sha", ""), pr.get("base", {}).get("sha", "")
    if not SHA.fullmatch(sha) or not SHA.fullmatch(base_sha):
        raise RuntimeError("missing PR commit SHA")
    files = pages(f"pulls/{number}/files")
    metadata = release_metadata(base_sha, sha)
    checks = pages(f"commits/{sha}/check-runs?filter=latest", "check_runs")
    statuses = [{**row, "sha": sha} for row in pages(f"commits/{sha}/statuses")]
    # A PR can change during the paginated reads. Do not pair old evidence with
    # new metadata (including a newly applied hold or a moved base branch).
    if api(f"pulls/{number}") != pr:
        raise RuntimeError("PR changed while evidence was being collected")
    return pr, files, checks, statuses, metadata


def summary(message):
    print(message)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write(message + "\n\n")


def outputs(**values):
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
            for key, value in values.items():
                text = str(value)
                if "\n" in text or "\r" in text:
                    raise ValueError("multiline output is not allowed")
                stream.write(f"{key}={text}\n")


def approver_login_ok(login):
    """An App bot login that is neither the PR author nor the workflow token."""
    return isinstance(login, str) and bool(APP_LOGIN.match(login)) and login not in (AUTHOR, ACTIONS_BOT)


def autopilot(merge=False):
    if os.environ.get("RELEASE_AUTOPILOT") != "on":
        summary("Release autopilot: off.")
        outputs(eligible="false")
        return
    if merge:
        number = os.environ.get("EXPECTED_PR", "")
        expected_sha = os.environ.get("EXPECTED_HEAD_SHA", "")
        if not re.fullmatch(r"[1-9][0-9]*", number) or not SHA.fullmatch(expected_sha):
            raise ValueError("missing validated PR number or SHA")
        # Never fall back to the workflow token: with Actions approvals off GitHub
        # refuses it, and with them on any writer's branch workflow could approve too.
        approver_token = os.environ.get("APPROVER_TOKEN", "")
        approver = os.environ.get("APPROVER_LOGIN", "")
        if not approver_token or not approver_login_ok(approver):
            summary("Release autopilot: the approver App token or login is missing or invalid; nothing was written.")
            raise ValueError("approver identity is missing or invalid")
    else:
        prs = pages(f"pulls?state=open&base=main&head=future-agi:{BRANCH}")
        if len(prs) != 1:
            summary("Release autopilot: waiting for exactly one release PR.")
            outputs(eligible="false")
            return
        number = str(prs[0]["number"])
        expected_sha = None
    evidence = snapshot(number)
    pr = evidence[0]
    sha = pr["head"]["sha"]
    now = datetime.now(timezone.utc)
    allowed, reason = merge_decision(*evidence, os.environ, now)
    if allowed:
        allowed, reason = release_gates(pr, os.environ, now)
    if expected_sha is not None and sha != expected_sha:
        allowed, reason = False, "head changed since the initial decision"
    summary(f"Release autopilot: PR #{number}, head `{sha}`: {reason}.")
    outputs(eligible=str(allowed).lower(), number=number, sha=sha)
    if not allowed or not merge:
        return
    names = required_checks(os.environ)
    audit = (f"Release autopilot policy v2 approved PR #{number} at head `{sha}`, base `{pr['base']['sha']}`.\n\n"
             f"Required checks on this SHA: {', '.join(names)} (success).\n"
             f"Minimum update age: {os.environ.get('RELEASE_AUTOPILOT_MIN_AGE_MINUTES') or '30'} minutes.\n"
             f"UTC window: {os.environ.get('AUTOPILOT_HOURS_UTC') or 'unrestricted'}; "
             f"weekends: {'on' if os.environ.get('RELEASE_AUTOPILOT_WEEKENDS') == 'on' else 'off'}.\n"
             "Trusted release App, same-repository release branch, stable title, release-metadata "
             "content/mode allowlist, title-aligned versions, no release-hold (case-insensitive) "
             "and mergeability verified.\n\n"
             f"Run: {os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{REPOSITORY}/actions/runs/"
             f"{os.environ.get('GITHUB_RUN_ID', '')}")
    # Audit/approval are idempotent per head/base pair. They can reset updated_at, so the
    # final gate honours the cooldown again, possibly on a later scheduled run.
    # Never waive the age or hold check merely because the bot wrote a comment.
    marker = f"<!-- release-autopilot-v2:{sha}:{pr['base']['sha']} -->"
    for label in (APPROVED_LABEL, LABEL):
        api(f"labels/{quote(label, safe='')}")
    comments = pages(f"issues/{number}/comments")
    if not any(row.get("user", {}).get("login") == ACTIONS_BOT
               and row.get("body", "").startswith(marker) for row in comments):
        api(f"issues/{number}/comments", method="POST", data={"body": marker + "\n" + audit})
    if not any(row["name"].casefold() == APPROVED_LABEL for row in pr["labels"]):
        api(f"issues/{number}/labels", method="POST", data={"labels": [APPROVED_LABEL]})
    # Only the approver App's reviews are the autopilot's own. An approval or a
    # dismissal under any other login is neither reused nor treated as a stop.
    reviews = [row for row in pages(f"pulls/{number}/reviews")
               if row.get("user", {}).get("login") == approver and row.get("user", {}).get("type") == "Bot"
               and row.get("state") != "COMMENTED"]
    latest = max(reviews, key=lambda row: row["id"], default={})
    if latest.get("commit_id") == sha and latest.get("state") in {"DISMISSED", "CHANGES_REQUESTED"}:
        summary("Release autopilot: existing review was dismissed or requested changes; a person must resolve it.")
        return
    if not (latest.get("state") == "APPROVED" and latest.get("commit_id") == sha
            and latest.get("body", "").startswith(marker)):
        review = api(f"pulls/{number}/reviews", method="POST", token=approver_token,
                     data={"event": "APPROVE", "commit_id": sha, "body": marker + "\n" + audit})
        user = review.get("user") if isinstance(review, dict) else None
        # A review recorded under another login would never be found as our own
        # again: a new approval on every run and a person's dismissal ignored.
        if not isinstance(user, dict) or user.get("login") != approver or user.get("type") != "Bot":
            raise RuntimeError("the approval was not recorded under the approver App's login")
    summary(audit)
    final = snapshot(number)
    now = datetime.now(timezone.utc)
    allowed, reason = merge_decision(*final, os.environ, now)
    if allowed:
        allowed, reason = release_gates(final[0], os.environ, now)
    if (final[0]["head"]["sha"] != sha
            or final[0]["base"].get("sha") != pr["base"].get("sha")):
        allowed, reason = False, "head or base changed after approval"
    if not allowed:
        summary(f"Release autopilot: approval recorded; merge deferred: {reason}.")
        return
    refused = f"<!-- release-autopilot-v2-merge-refused:{sha} -->"
    try:
        result = api(f"pulls/{number}/merge", method="PUT", token=os.environ["RELEASE_APP_TOKEN"],
                     data={"sha": sha, "merge_method": "squash"})
    except GitHubError as error:
        if error.status not in (405, 422):
            raise
        # GitHub refused the merge, so nothing merged; the approval stays. A first
        # refusal can be transient (main just moved), so the next eligible run
        # retries. A second refusal of the same head fails the run, so a standing
        # refusal, such as main requiring up-to-date branches, is not silent.
        if any(row.get("user", {}).get("login") == ACTIONS_BOT
               and row.get("body", "").startswith(refused) for row in comments):
            summary(f"Release autopilot: GitHub refused the merge of head `{sha}` again "
                    f"(HTTP {error.status}). A person must resolve it.")
            raise
        api(f"issues/{number}/comments", method="POST",
            data={"body": refused + f"\nRelease autopilot: GitHub refused the merge of head `{sha}` "
                  f"(HTTP {error.status}). The approval stays. The next eligible run retries once; "
                  "a second refusal of this head fails the run."})
        summary(f"Release autopilot: approval recorded; merge deferred: GitHub refused the merge (HTTP {error.status}).")
        return
    if result.get("merged") is not True:
        raise RuntimeError("GitHub refused the SHA-bound merge")
    api(f"issues/{number}/labels", method="POST", data={"labels": [LABEL]})
    api(f"issues/{number}/comments", method="POST",
        data={"body": f"Release autopilot merged verified head `{sha}` with the release App.\n\n" + audit})
    summary(f"Release autopilot merged PR #{number} at verified head `{sha}`; label `{LABEL}` applied.\n\n{audit}")


def git(*args):
    result = subprocess.run(["git", *args], text=True, capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError("release Git evidence unavailable")
    return result.stdout.strip()


def reachable(sha, branch):
    try:
        git("merge-base", "--is-ancestor", sha, branch)
        return True
    except RuntimeError:
        return False


def version_tuple(tag):
    match = re.fullmatch(r"v" + STABLE, tag)
    return tuple(map(int, match.groups())) if match else None


CLICKHOUSE = "futureagi/tracer/services/clickhouse/"
# Every .sql file under CLICKHOUSE is schema (v2/schema/, v2/observed_catalog/,
# the legacy schema/ and oss_cdc_migrations/), plus the code that applies it.
# Query builders and other readers are not schema and stay uncounted.
CLICKHOUSE_APPLIERS = frozenset(CLICKHOUSE + name for name in (
    "schema.py", "v2/apply_schema.py", "v2/apply_schema_rewriter.py", "v2/schema_topology.py"))
# ClickHouse schema written in Python outside CLICKHOUSE: the Error Feed grouping
# tables (grouping_feature_ddl) and the operator command that applies them. The
# module also holds queries, so a non-schema edit to it selects Production too.
OPERATOR_SCHEMA = frozenset({
    "futureagi/tracer/services/grouping/feature_store.py",
    "futureagi/tracer/management/commands/provision_grouping_features.py"})


def is_migration(path):
    # Django migration files (nested futureagi/ee and an ee/ tree; initializers
    # are not migrations), SQL under any migrations/ directory, and the
    # ClickHouse schema sources. Callers count additions, edits and deletions.
    parts = path.split("/")
    if path.startswith(CLICKHOUSE) and (path.endswith(".sql") or path in CLICKHOUSE_APPLIERS):
        return True
    if path in OPERATOR_SCHEMA:
        return True
    return (len(parts) >= 4 and parts[0] in {"futureagi", "ee"}
            and parts[-2] == "migrations" and parts[-1].endswith((".py", ".sql"))
            and parts[-1] != "__init__.py")


# Jobs of a release-images run whose success proves the release shipped:
# approve passed review (or the automatic lane) and backend / publish pushed
# the image that carries the migrations. A test pins both names to the workflows.
SHIPPED_JOBS = frozenset({"approve", "backend / publish"})
READ_TRIES = 3


def retried(read, *args):
    """Repeat a GitHub read that failed in transit, then re-raise the last error.

    A person's re-run always selects Production review, so a single HTTP 502
    here would otherwise cost a review that no retry can clear. Malformed data
    (KeyError, TypeError, ValueError) is not transient and raises at once.
    """
    for attempt in range(1, READ_TRIES + 1):
        try:
            return read(*args)
        except (RuntimeError, subprocess.TimeoutExpired):
            if attempt == READ_TRIES:
                raise
            time.sleep(5 * attempt)


def published(tag):
    """A stable tag whose release-images push run approved and published the backend image."""
    sha = git("rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}")
    if not SHA.fullmatch(sha):
        return False
    data = retried(api, f"actions/workflows/release-images.yml/runs?head_sha={sha}&event=push&per_page=50")
    runs = data.get("workflow_runs") if isinstance(data, dict) else None
    if not isinstance(runs, list):
        return False
    runs = [run for run in runs if isinstance(run, dict) and run.get("head_branch") == tag]
    if any(run.get("conclusion") == "success" for run in runs):
        return True
    # A side job such as the Helm chart can fail after the images went out. Such
    # a finished run still shipped when approve and the backend publish job
    # succeeded on any attempt. A waiting, rejected or build-failed run did not.
    for run in runs:
        if run.get("status") != "completed" or type(run.get("id")) is not int:
            continue
        jobs = retried(pages, f"actions/runs/{run['id']}/jobs?filter=all", "jobs")
        passed = {job.get("name") for job in jobs if isinstance(job, dict) and job.get("conclusion") == "success"}
        if SHIPPED_JOBS <= passed:
            return True
    return False


def migration_evidence(version, sha):
    current = tuple(map(int, version.split("-", 1)[0][1:].split(".")))
    tags = git("tag", "--merged", sha, "--list", "v*").splitlines()
    previous = [tag for tag in tags if version_tuple(tag) is not None and version_tuple(tag) < current]
    if not previous:
        return None, None
    last = max(previous, key=version_tuple)
    # The previous tag is a baseline only if that release actually shipped. A
    # tag whose run is still waiting, was rejected or failed before publishing
    # the backend would hide its own migrations from the next release. API
    # errors and malformed responses mean not shipped.
    try:
        shipped = published(last)
    except (RuntimeError, OSError, subprocess.TimeoutExpired, KeyError, TypeError, ValueError):
        shipped = False
    if not shipped:
        return last, None
    return last, count_migrations(f"refs/tags/{last}", sha)


def count_migrations(base, head):
    """Migration and schema files that differ between two commits (see is_migration)."""
    # --no-renames treats files moved into a migration directory as additions.
    # NUL delimiting prevents unusual filenames from hiding migration files.
    # Additions, edits and deletions all count as schema changes. Names come
    # from trees only, so a clone without file contents is enough.
    changed = git("diff", "--name-only", "-z", "--no-renames", "--diff-filter=ACDMRT",
                  f"{base}..{head}", "--", "futureagi/", "ee/")
    return sum(is_migration(path) for path in changed.split("\0") if path)


def migrations_since(pinned, version):
    """Migration and schema files from the release deployment main runs to `version`.

    The deployment bump writes this count. It starts at what main runs, not at
    the previous release, so it also covers a release whose bump never merged.
    None means unknown: either value is not a stable tag in this repository,
    `pinned` is not lower than `version`, or it is not an ancestor of it.
    """
    old, new = version_tuple(pinned or ""), version_tuple(version or "")
    if old is None or new is None or old >= new:
        return None
    try:
        base = git("rev-parse", "--verify", "--quiet", f"refs/tags/{pinned}^{{commit}}")
        head = git("rev-parse", "--verify", "--quiet", f"refs/tags/{version}^{{commit}}")
        if not SHA.fullmatch(base) or not SHA.fullmatch(head) or not reachable(base, head):
            return None
        return count_migrations(base, head)
    except (RuntimeError, subprocess.TimeoutExpired):
        return None


def environment_decision(enabled, event, ref, version, count, actor=None, triggering_actor=None, attempt=None):
    if enabled != "on":
        return "Production", "RELEASE_AUTO_APPROVE is not on"
    if event != "push" or ref != f"refs/tags/{version}":
        return "Production", "only an exact tag push may skip review; dispatches require a person"
    if actor != AUTHOR:
        return "Production", "the tag was not pushed by the release App"
    # A re-run keeps GITHUB_ACTOR from the first attempt; only the triggering
    # actor and the attempt number show that a person started it.
    if triggering_actor != AUTHOR or attempt != "1":
        return "Production", "a re-run keeps human review; only the release App's first attempt may skip it"
    if version_tuple(version) is None:
        return "Production", "prerelease tags keep human review"
    if type(count) is not int or count < 0:
        return "Production", "migration baseline or diff is unknown (or the previous release did not ship)"
    if count:
        return "Production", f"release changes {count} migration or schema files"
    return "production-auto", "release App tag push with no migration or schema changes since the previous shipped release"


def release_environment(version, expected_sha=None, approved_environment=None):
    if not TAG.fullmatch(version):
        raise ValueError("invalid release version")
    sha = git("rev-parse", "--verify", f"refs/tags/{version}^{{commit}}")
    if not SHA.fullmatch(sha):
        raise RuntimeError("tag does not resolve to a commit")
    # Ancestry is mandatory even when automatic approval is off or this is a
    # dispatch. A valid tag spelling alone does not prove the code was reviewed.
    # Every tag, prereleases included, must be on main. A run on a tag uses
    # the tag's own workflow files, and the environments that hold the
    # release-bot key admit v* tags, so a tag at a dev commit would run dev's
    # files with the key; dev also takes merges from the low-risk autopilot
    # that no person reviewed. Prereleases still keep human review
    # (environment_decision). The remote-tracking ref is spelled in full: Git
    # reads a short name such as origin/main as a tag first, and any writer can
    # push a tag named origin/main.
    main = "refs/remotes/origin/main"
    branches = [main]
    if not any(reachable(sha, branch) for branch in branches):
        summary("Release refused: tag commit reachability from " + " or ".join(branches) + " could not be proved.")
        raise RuntimeError("tag commit is not on a reviewed branch")
    if expected_sha is not None and sha != expected_sha:
        raise RuntimeError("tag moved while approval was waiting")
    if os.environ.get("GITHUB_EVENT_NAME") == "push":
        event_sha = os.environ.get("GITHUB_SHA", "")
        if not SHA.fullmatch(event_sha) or git("rev-parse", f"{event_sha}^{{commit}}") != sha:
            raise RuntimeError("tag no longer matches the triggering push")
    try:
        last, count = migration_evidence(version, sha)
    except RuntimeError:
        last, count = None, None
    environment, reason = environment_decision(os.environ.get("RELEASE_AUTO_APPROVE"),
                                               os.environ.get("GITHUB_EVENT_NAME"),
                                               os.environ.get("GITHUB_REF"), version, count,
                                               os.environ.get("GITHUB_ACTOR"),
                                               os.environ.get("GITHUB_TRIGGERING_ACTOR"),
                                               os.environ.get("GITHUB_RUN_ATTEMPT"))
    summary(f"Release approval: `{environment}` for `{version}` at `{sha}`. {reason}.\n\n"
            f"Previous release: `{last or 'unknown'}`; migration or schema files since it: "
            f"{count if count is not None else 'unknown'}. The tag commit is on a reviewed branch.")
    if approved_environment is not None:
        if approved_environment not in {"Production", "production-auto"}:
            raise ValueError("invalid approved environment")
        if approved_environment == "production-auto" and environment != "production-auto":
            # main() prints only the error type, so say what to do here. Re-running
            # all jobs repeats the guard, which then selects Production review.
            summary("Release refused: automatic approval no longer applies to this run. "
                    "Use Re-run all jobs so the guard selects Production review.")
            raise RuntimeError("automatic approval is no longer safe; re-run all jobs for human review")
    # This count only picks the approval environment. The deployment bump
    # counts again from the release deployment main runs (migrations-since).
    outputs(environment=environment, tag_sha=sha)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check", "merge", "environment", "migrations-since"])
    parser.add_argument("--version")
    parser.add_argument("--expected-sha")
    parser.add_argument("--approved-environment")
    parser.add_argument("--from", dest="pinned", help="migrations-since: the release deployment main runs")
    parser.add_argument("--to", dest="target", help="migrations-since: the release being bumped to")
    args = parser.parse_args()
    try:
        if args.command == "migrations-since":
            # The bump step reads stdout, so it carries only the count or "unknown".
            count = migrations_since(args.pinned, args.target)
            print("unknown" if count is None else count)
        elif args.command == "environment":
            release_environment(args.version or "", args.expected_sha, args.approved_environment)
        else:
            autopilot(merge=args.command == "merge")
    except (RuntimeError, ValueError, KeyError, TypeError, AttributeError, OSError, ImportError,
            subprocess.TimeoutExpired) as error:
        summary(f"Release autopilot stopped ({type(error).__name__}); evidence is incomplete or policy changed. No fallback approval.")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
