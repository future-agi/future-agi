#!/usr/bin/env python3
"""Check the notes release-please publishes, not the surrounding PR prose.

The repository uses a single root package. Its release-please v17 PR format is
parsed by src/util/pull-request-body.ts: content between the first and last
'---' lines, starting with the version heading. Text before that heading can
make release-please publish empty notes; text outside the delimiters is dropped.
"""

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

CALLOUT = "Contains database migrations"
STABLE_TAG = re.compile(r"v\d+\.\d+\.\d+")
FULL_SHA = re.compile(r"[0-9a-f]{40}")
VERSION_HEADING = re.compile(r"^#{2,} \[?\d+\.\d+\.\d+(?:\]|\s|$)")


def git(*args: str, input_text: str | None = None) -> str:
    """Run git without treating lookup failures as an empty migration diff."""
    return subprocess.run(
        ["git", *args], input=input_text, text=True, capture_output=True, check=True
    ).stdout.strip()


def published_pr_notes(body: str) -> str:
    """Extract the current single-package release-please publication section."""
    lines = body.strip().replace("\r\n", "\n").split("\n")
    delimiters = [index for index, line in enumerate(lines) if line == "---"]
    if not delimiters:
        raise ValueError("Release PR has no release-please notes delimiters.")
    first = delimiters[0]
    last = delimiters[-1] if len(delimiters) > 1 else len(lines) - 1
    notes = "\n".join(lines[first + 1 : last]).strip()
    # Fail closed if the configured single-package format changes to components.
    if re.search(r"<details\b", notes, re.IGNORECASE) or not VERSION_HEADING.match(
        notes
    ):
        raise ValueError(
            "Release notes must start with the version heading. Put the migration "
            "callout AFTER that heading, inside the release-please '---' delimiters."
        )
    return notes


def previous_release(ref: str, exclude: str | None = None) -> str:
    """Find the nearest reachable stable tag, excluding the release under review."""
    tags = [
        tag
        for tag in git("tag", "--merged", ref, "--list", "v*").splitlines()
        if STABLE_TAG.fullmatch(tag) and tag != exclude
    ]
    if not tags:
        # Unlike root..HEAD, this also counts migrations in the first commit.
        return git("mktree", input_text="")
    return git("describe", "--tags", "--abbrev=0", *[f"--match={t}" for t in tags], ref)


def pr_merge_preview(pr: dict) -> tuple[str, str]:
    """Return (first parent, merge) of this run's own PR merge preview.

    The payload's merge_commit_sha is not used. GitHub computes the test merge
    after it creates the PR, so an `opened` payload usually carries null, and a
    re-run reuses that payload. GITHUB_SHA is the refs/pull/N/merge commit that
    actions/checkout fetched for this run, and it stays fixed across re-runs.
    """
    merge = os.environ.get("GITHUB_SHA", "")
    if not FULL_SHA.fullmatch(merge):
        raise ValueError(
            "GITHUB_SHA is not a full commit id; no PR merge preview to check."
        )
    # Peeling fails for a missing object and changes the id of anything that
    # is not a commit, such as a tag object.
    if git("rev-parse", "--verify", f"{merge}^{{commit}}") != merge:
        raise ValueError("GITHUB_SHA is not a commit; no PR merge preview to check.")
    parents = git("rev-list", "--parents", "--no-walk", merge).split()[1:]
    # GitHub's preview merges the PR head into the base tip: two parents, the
    # head second. Anything else is not this PR's merge preview.
    if len(parents) != 2 or parents[1] != pr["head"]["sha"]:
        raise ValueError(
            "GITHUB_SHA is not a merge of this PR's head; refresh the PR and rerun."
        )
    return parents[0], merge


def check_event(event_name: str, event: dict) -> None:
    """Validate a PR snapshot or the actual published/edited release body."""
    if event_name == "pull_request":
        pr = event["pull_request"]
        if pr["base"]["ref"] != "main" or not pr["head"]["ref"].startswith(
            "release-please--"
        ):
            return
        # Use the immutable merge preview: a stale PR head can omit migrations
        # already on main, while a moving origin/main can make a rerun empty.
        # Both ends come from the same snapshot: the base side is the preview's
        # first parent, not the payload's base.sha, which can be older or newer.
        first_parent, head = pr_merge_preview(pr)
        base = previous_release(first_parent)
        body = pr.get("body") or ""
    elif event_name == "release":
        release = event["release"]
        tag = release["tag_name"]
        if release["draft"] or release["prerelease"] or not STABLE_TAG.fullmatch(tag):
            return
        head = f"refs/tags/{tag}"
        # The current tag already exists at publication; do not diff it to itself.
        base = previous_release(head, exclude=tag)
        body = release.get("body") or ""
    else:
        raise ValueError(f"Unsupported event: {event_name}")

    paths = git(
        "diff",
        "--name-only",
        "--diff-filter=A",
        "-z",
        base,
        head,
        "--",
        "futureagi/*/migrations/*.py",
    ).split("\0")
    migrations = [p for p in paths if p and Path(p).name != "__init__.py"]
    print(f"Added migrations: {len(migrations)} ({base} -> {head})")
    if not migrations:
        return
    print("\n".join(migrations))
    notes = published_pr_notes(body) if event_name == "pull_request" else body
    if CALLOUT not in notes:
        raise ValueError(
            f"Release notes lack '{CALLOUT}'. For a release PR, add it after the "
            "version heading and before the closing '---', not in the PR header "
            "or footer. Published-release checks are read-only; no notes were edited."
        )
    print("Migration callout present in publication notes.")


def main() -> int:
    """Read GitHub's event JSON as data, never interpolate note text into a shell."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--event-path", type=Path, required=True)
    args = parser.parse_args()
    try:
        check_event(args.event_name, json.loads(args.event_path.read_text()))
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        print(f"::error::{error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
