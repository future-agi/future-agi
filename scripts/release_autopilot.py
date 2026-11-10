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
    audit = (f"Release autopilot policy v2 approved PR #{number} at head `{sha}`, base `{pr['base']['sha']}`.\n\n"
             f"Required checks on this SHA: {', '.join(names)} (success).\n"
             f"Minimum update age: {os.environ.get('RELEASE_AUTOPILOT_MIN_AGE_MINUTES') or '30'} minutes.\n"
             f"UTC window: {os.environ.get('AUTOPILOT_HOURS_UTC') or 'unrestricted'}; "
             f"weekends: {'on' if os.environ.get('RELEASE_AUTOPILOT_WEEKENDS') == 'on' else 'off'}.\n"
             "Trusted release App, same-repository release branch, stable title, release-metadata "
             "content/mode allowlist, title-aligned versions, no release-hold (case-insensitive) "
             "and mergeability verified. E2E is advisory unless listed in the required-check variable.\n\n"
             f"Run: {os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{REPOSITORY}/actions/runs/"
             f"{os.environ.get('GITHUB_RUN_ID', '')}")
    # Audit/approval are idempotent per head/base pair. They can reset updated_at, so the
    # final gate honours the cooldown again, possibly on a later scheduled run.
    # Never waive the age or hold check merely because the bot wrote a comment.
    marker = f"<!-- release-autopilot-v2:{sha}:{pr['base']['sha']} -->"
    for label in (APPROVED_LABEL, LABEL):
        api(f"labels/{quote(label, safe='')}")
    comments = pages(f"issues/{number}/comments")
    if not any(row.get("user", {}).get("login") == "github-actions[bot]"
               and row.get("body", "").startswith(marker) for row in comments):
        api(f"issues/{number}/comments", method="POST", data={"body": marker + "\n" + audit})
    if not any(row["name"].casefold() == APPROVED_LABEL for row in pr["labels"]):
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
    except (RuntimeError, ValueError, KeyError, TypeError, AttributeError, OSError, ImportError,
            subprocess.TimeoutExpired) as error:
        summary(f"Release autopilot stopped ({type(error).__name__}); evidence is incomplete or policy changed. No fallback approval.")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
