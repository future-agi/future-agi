"""On a cache miss, use verified timings from the newest successful Backend CI dev push."""

import hashlib
import io
import json
import math
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ARTIFACT = "test-durations-saved"
RUNS = "workflows/backend-ci.yml/runs?branch=dev&event=push&status=success&per_page=10"
# The full file is currently ~10 MiB. Reject oversized archives before unpacking.
MAX_BYTES = 64 << 20


class TimingsError(ValueError):
    """A diagnostic safe to include in a workflow annotation."""


def newest_run_id(runs, repository):
    # Only this repository's own dev pushes: never a fork, a pull request or
    # another branch, even if the API filter were ignored.
    eligible = (
        run
        for run in runs
        if run["event"] == "push"
        and run["head_branch"] == "dev"
        and run["conclusion"] == "success"
        and (run.get("head_repository") or {}).get("full_name") == repository
        and (run.get("repository") or {}).get("full_name") == repository
    )
    run = max(eligible, key=lambda r: (r["created_at"], r["id"]), default=None)
    if run is None:
        raise TimingsError("no successful Backend CI dev push")
    return int(run["id"])


def verified_timings(raw, digest):
    if digest != "sha256:" + hashlib.sha256(raw).hexdigest():
        raise TimingsError("artifact digest mismatch")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if archive.namelist() != [".test_durations"]:
            raise TimingsError("expected only .test_durations in the archive")
        member = archive.getinfo(".test_durations")
        if member.file_size > MAX_BYTES:
            raise TimingsError("timings file too large")
        data = archive.read(member)
    # The test-to-seconds check from merge_test_durations.py. Also reject
    # non-finite/negative weights before handing the file to pytest-split.
    document = json.loads(data.decode("utf-8"))
    if (
        not isinstance(document, dict)
        or not document
        or not all(
            isinstance(seconds, (int, float))
            and not isinstance(seconds, bool)
            and math.isfinite(seconds)
            and seconds >= 0
            for seconds in document.values()
        )
    ):
        raise TimingsError("not a test-to-seconds object")
    return data


def api(endpoint):
    # This is a planning hint, not a prerequisite: bound each of the three
    # reads and do not forward API bodies or gh stderr to the public log.
    return subprocess.run(
        ["gh", "api", endpoint], capture_output=True, check=True, timeout=15
    ).stdout


def fetch(destination):
    repository = os.environ["GITHUB_REPOSITORY"]
    root = f"repos/{repository}/actions"
    run = newest_run_id(json.loads(api(f"{root}/{RUNS}"))["workflow_runs"], repository)
    # Ask for this name server-side; a dev run produces many shard artifacts.
    listing = json.loads(api(f"{root}/runs/{run}/artifacts?name={ARTIFACT}"))
    matches = [
        a for a in listing["artifacts"] if a["name"] == ARTIFACT and not a["expired"]
    ]
    if len(matches) != 1:
        raise TimingsError(f"no unique current {ARTIFACT} in dev run {run}")
    artifact = matches[0]
    if artifact["size_in_bytes"] > MAX_BYTES:
        raise TimingsError("timings archive too large")
    raw = api(f"{root}/artifacts/{int(artifact['id'])}/zip")
    data = verified_timings(raw, artifact["digest"])
    # Atomic on the checkout's filesystem, even if writing runs out of space.
    with tempfile.TemporaryDirectory(dir=destination.parent) as tmp:
        staged = Path(tmp) / destination.name
        staged.write_bytes(data)
        staged.replace(destination)
    return f"artifact `{ARTIFACT}` from dev run `{run}` (`{artifact['digest']}`)"


def main(destination=Path("futureagi/.test_durations")):
    # A partial cache extraction can leave a damaged working copy on a miss.
    # Establish the real fallback before any API call. If git itself fails,
    # let the workflow's unexpected-exit handler retry the restore.
    subprocess.run(
        ["git", "restore", "--source=HEAD", "--worktree", "--", destination.name],
        cwd=destination.parent,
        check=True,
    )
    try:
        source = fetch(destination)
    except Exception as exc:  # noqa: BLE001 - a missing planning hint must not fail the gate
        reason = str(exc) if isinstance(exc, TimingsError) else type(exc).__name__
        sys.stdout.write(
            f"::warning title=Timings::{reason}; using committed fallback\n"
        )
        source = "committed fallback (`futureagi/.test_durations`)"
    line = f"Timing source (before floors) — {source}"
    sys.stdout.write(line + "\n")
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        try:
            with open(summary, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            sys.stdout.write(
                "::warning title=Timings::could not write timing source to step summary\n"
            )


if __name__ == "__main__":
    main()
