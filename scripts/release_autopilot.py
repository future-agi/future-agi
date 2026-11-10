#!/usr/bin/env python3
"""Fail-closed release policy; only the `merge` command performs GitHub writes."""

import argparse
from datetime import datetime, timezone
import json
import os
import re
import subprocess
from urllib.parse import quote

REPOSITORY = "future-agi/future-agi"
AUTHOR = "futureagi-release-bot[bot]"
BRANCH = "release-please--branches--main"
LABEL = "autopilot-merged"
APPROVED_LABEL = "autopilot-approved"
STABLE = r"(0|[1-9][0-9]{0,17})\.(0|[1-9][0-9]{0,17})\.(0|[1-9][0-9]{0,17})"
TAG = re.compile(r"v" + STABLE + r"(?:-(?:beta|rc)\.(?:0|[1-9][0-9]{0,17}))?")
SHA = re.compile(r"[0-9a-f]{40}")
# release-please-config.json uses the simple releaser plus the chart version.
RELEASE_FILES = {"CHANGELOG.md", ".release-please-manifest.json", "version.txt",
                 "deploy/helm/futureagi/Chart.yaml"}


def required_checks(config):
    value = config.get("RELEASE_AUTOPILOT_REQUIRED_CHECKS", "")
    if not value:
        return ["Backend Tests Pass"]
    names = [name.strip() for name in re.split(r"[,\n]", value) if name.strip()]
    if not names:
        raise ValueError("required-check list is empty")
    return list(dict.fromkeys(names))


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


def checks_pass(sha, names, checks, statuses):
    """Latest attempt per check name/app or status context, on this exact SHA.

    Distinct producers with the same name must all succeed. A queued rerun
    supersedes its old green result; skipped and neutral are not success.
    """
    latest = {}
    for row in checks:
        if row.get("head_sha") != sha or row.get("name") not in names:
            continue
        key = (row["name"], "check", row.get("app", {}).get("id"))
        if type(row.get("id")) is not int:
            return False
        if key not in latest or row["id"] > latest[key]["id"]:
            latest[key] = row
    for row in statuses:
        # The adapter binds statuses (whose REST representation omits SHA) to
        # the immutable commit endpoint that returned them.
        if row.get("sha") != sha or row.get("context") not in names:
            continue
        key = (row["context"], "status", None)
        if type(row.get("id")) is not int:
            return False
        if key not in latest or row["id"] > latest[key]["id"]:
            latest[key] = row
    for name in names:
        matches = [(key, row) for key, row in latest.items() if key[0] == name]
        if not matches:
            return False
        for key, row in matches:
            if key[1] == "check":
                if row.get("status") != "completed" or row.get("conclusion") != "success":
                    return False
            elif row.get("state") != "success":
                return False
    return True


def merge_decision(pr, files, checks, statuses, config, now):
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
            or not SHA.fullmatch(head.get("sha", ""))):
        return False, "PR branch, repository or head SHA is not trusted"
    if not re.fullmatch(r"chore\(main\): release " + STABLE, pr.get("title", "")):
        return False, "PR title is not a stable release"
    labels = pr.get("labels")
    if not isinstance(labels, list) or any(label.get("name") == "release-hold" for label in labels):
        return False, "release-hold is set or labels are unknown"
    if (not files or len(files) != pr.get("changed_files")
            or any(row.get("filename") not in RELEASE_FILES
                   or row.get("status") not in {"added", "modified"} for row in files)):
        return False, "changed files are unknown or outside release metadata"
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
    if not checks_pass(head["sha"], names, checks, statuses):
        return False, "required checks are missing, stale, pending or unsuccessful"
    return True, "release metadata, author, age, hours, hold and current-head checks passed"


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
        raise RuntimeError("GitHub API request failed; no further writes attempted")
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


def snapshot(number):
    pr = api(f"pulls/{number}")
    sha = pr.get("head", {}).get("sha", "")
    if not SHA.fullmatch(sha):
        raise RuntimeError("missing PR head SHA")
    files = pages(f"pulls/{number}/files")
    checks = pages(f"commits/{sha}/check-runs?filter=latest", "check_runs")
    statuses = [{**row, "sha": sha} for row in pages(f"commits/{sha}/statuses")]
    # A PR can change during the paginated reads. Do not pair old evidence with
    # new metadata (including a newly applied hold or a moved base branch).
    if api(f"pulls/{number}") != pr:
        raise RuntimeError("PR changed while evidence was being collected")
    return pr, files, checks, statuses


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
    allowed, reason = merge_decision(*evidence, os.environ, datetime.now(timezone.utc))
    if expected_sha is not None and sha != expected_sha:
        allowed, reason = False, "head changed since the initial decision"
    summary(f"Release autopilot: PR #{number}, head `{sha}`: {reason}.")
    outputs(eligible=str(allowed).lower(), number=number, sha=sha)
    if not allowed or not merge:
        return
    names = required_checks(os.environ)
    audit = (f"Release autopilot policy v1 approved PR #{number} at head `{sha}`.\n\n"
             f"Required checks on this SHA: {', '.join(names)} (success).\n"
             f"Minimum update age: {os.environ.get('RELEASE_AUTOPILOT_MIN_AGE_MINUTES') or '30'} minutes.\n"
             f"UTC window: {os.environ.get('AUTOPILOT_HOURS_UTC') or 'unrestricted'}; "
             f"weekends: {'on' if os.environ.get('RELEASE_AUTOPILOT_WEEKENDS') == 'on' else 'off'}.\n"
             "Trusted release App, same-repository release branch, stable title, release-metadata "
             "allowlist, no release-hold and mergeability verified. E2E is advisory unless listed "
             "in the required-check variable.\n\n"
             f"Run: {os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{REPOSITORY}/actions/runs/"
             f"{os.environ.get('GITHUB_RUN_ID', '')}")
    # Audit/approval are idempotent per head. They can reset updated_at, so the
    # final gate honours the cooldown again, possibly on a later scheduled run.
    # Never waive the age or hold check merely because the bot wrote a comment.
    marker = f"<!-- release-autopilot-v1:{sha} -->"
    for label in (APPROVED_LABEL, LABEL):
        api(f"labels/{quote(label, safe='')}")
    comments = pages(f"issues/{number}/comments")
    if not any(row.get("user", {}).get("login") == "github-actions[bot]"
               and row.get("body", "").startswith(marker) for row in comments):
        api(f"issues/{number}/comments", method="POST", data={"body": marker + "\n" + audit})
    if not any(row.get("name") == APPROVED_LABEL for row in pr["labels"]):
        api(f"issues/{number}/labels", method="POST", data={"labels": [APPROVED_LABEL]})
    reviews = [row for row in pages(f"pulls/{number}/reviews")
               if row.get("user", {}).get("login") == "github-actions[bot]"
               and row.get("state") != "COMMENTED"]
    latest = max(reviews, key=lambda row: row["id"], default={})
    if latest.get("commit_id") == sha and latest.get("state") in {"DISMISSED", "CHANGES_REQUESTED"}:
        summary("Release autopilot: existing review was dismissed or requested changes; a person must resolve it.")
        return
    if not (latest.get("state") == "APPROVED" and latest.get("commit_id") == sha
            and latest.get("body", "").startswith(marker)):
        api(f"pulls/{number}/reviews", method="POST",
            data={"event": "APPROVE", "commit_id": sha, "body": marker + "\n" + audit})
    summary(audit)
    final = snapshot(number)
    allowed, reason = merge_decision(*final, os.environ, datetime.now(timezone.utc))
    if (final[0]["head"]["sha"] != sha
            or final[0]["base"].get("sha") != pr["base"].get("sha")):
        allowed, reason = False, "head or base changed after approval"
    if not allowed:
        summary(f"Release autopilot: approval recorded; merge deferred: {reason}.")
        return
    result = api(f"pulls/{number}/merge", method="PUT", token=os.environ["RELEASE_APP_TOKEN"],
                 data={"sha": sha, "merge_method": "squash"})
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


def version_tuple(tag):
    match = re.fullmatch(r"v" + STABLE, tag)
    return tuple(map(int, match.groups())) if match else None


def is_migration(path):
    # Same added-Python-file rule as migrations-callout-check.yml, expanded
    # explicitly to nested futureagi/ee and an ee/ tree. Initializers are not migrations.
    parts = path.split("/")
    return (len(parts) >= 4 and parts[0] in {"futureagi", "ee"}
            and parts[-2] == "migrations" and parts[-1].endswith(".py")
            and parts[-1] != "__init__.py")


def migration_evidence(version, sha):
    current = tuple(map(int, version.split("-", 1)[0][1:].split(".")))
    tags = git("tag", "--merged", sha, "--list", "v*").splitlines()
    previous = [tag for tag in tags if version_tuple(tag) is not None and version_tuple(tag) < current]
    if not previous:
        return None, None
    last = max(previous, key=version_tuple)
    # --no-renames treats files moved into a migration directory as additions.
    # NUL delimiting prevents unusual filenames from hiding migration files.
    added = git("diff", "--name-only", "-z", "--no-renames", "--diff-filter=A",
                f"refs/tags/{last}..{sha}", "--", "futureagi/", "ee/")
    return last, sum(is_migration(path) for path in added.split("\0") if path)


def environment_decision(enabled, event, ref, version, count):
    if enabled != "on":
        return "Production", "RELEASE_AUTO_APPROVE is not on"
    if event != "push" or ref != f"refs/tags/{version}":
        return "Production", "only an exact tag push may skip review; dispatches require a person"
    if type(count) is not int or count < 0:
        return "Production", "migration baseline or diff is unknown"
    if count:
        return "Production", f"release adds {count} migration files"
    return "production-auto", "tag push with zero new migration files since the previous stable tag"


def release_environment(version, expected_sha=None, approved_environment=None):
    if not TAG.fullmatch(version):
        raise ValueError("invalid release version")
    sha = git("rev-parse", "--verify", f"refs/tags/{version}^{{commit}}")
    if not SHA.fullmatch(sha):
        raise RuntimeError("tag does not resolve to a commit")
    # Ancestry is mandatory even when automatic approval is off or this is a
    # dispatch. A valid tag spelling alone does not prove the code was reviewed.
    try:
        git("merge-base", "--is-ancestor", sha, "origin/main")
    except RuntimeError:
        summary("Release refused: tag commit reachability from origin/main could not be proved.")
        raise
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
                                               os.environ.get("GITHUB_REF"), version, count)
    summary(f"Release approval: `{environment}` for `{version}` at `{sha}`. {reason}.\n\n"
            f"Migration baseline: `{last or 'unknown'}`; new files: {count if count is not None else 'unknown'}. "
            "The tag commit is reachable from origin/main.")
    if approved_environment is not None:
        if approved_environment not in {"Production", "production-auto"}:
            raise ValueError("invalid approved environment")
        if approved_environment == "production-auto" and environment != "production-auto":
            raise RuntimeError("automatic approval is no longer safe; start a new run for human review")
    outputs(environment=environment, migrations=count if count is not None else "unknown",
            tag_sha=sha)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check", "merge", "environment"])
    parser.add_argument("--version")
    parser.add_argument("--expected-sha")
    parser.add_argument("--approved-environment")
    args = parser.parse_args()
    try:
        if args.command == "environment":
            release_environment(args.version or "", args.expected_sha, args.approved_environment)
        else:
            autopilot(merge=args.command == "merge")
    except (RuntimeError, ValueError, KeyError, TypeError, AttributeError, OSError,
            subprocess.TimeoutExpired) as error:
        summary(f"Release autopilot stopped ({type(error).__name__}); evidence is incomplete or policy changed. No fallback approval.")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
