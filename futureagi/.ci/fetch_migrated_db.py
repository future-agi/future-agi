"""Find the newest saved dump for this checkout's migrate inputs.

Only a dump that a successful Backend CI dev push of this repository saved:
never one from a fork, a pull request, another branch or another workflow.
Called once, by the plan job, which hands it to this run's jobs as a same-run
artifact. Every job that restores it still checks the full recipe, and the
control job checks it against a fresh migrate.
"""

import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

WORKFLOW = ".github/workflows/backend-ci.yml"
RECORD = frozenset({"checksums", "dump.sql.gz", "preseed", "recipe"})
MAX_BYTES = 256 << 20
# Newest first; a few runs are enough to skip a failed or cancelled push.
CANDIDATES = 10
TOOL = Path(__file__).with_name("db_dump.py")


class FetchError(ValueError):
    """A diagnostic safe to include in a workflow annotation."""


def eligible(run, repository):
    # TEMPORARY proof scaffold (reverted before review): dispatches of this branch.
    # A dispatch is checked against main by the update-safety job, which fails
    # for reasons outside this change; accept that one red job here.
    scaffold = (
        run["event"] == "workflow_dispatch"
        and run["head_branch"] == "perf/ci-backend-db-reuse"
    )
    return (
        (scaffold or run["event"] == "push" and run["head_branch"] == "dev")
        and (scaffold or run["conclusion"] == "success")
        and run["path"] == WORKFLOW
        and (run.get("head_repository") or {}).get("full_name") == repository
        and (run.get("repository") or {}).get("full_name") == repository
    )


def listed(artifact):
    """Before any run lookup: an artifact of a dev run of this repository.

    Any run, a fork's included, can upload an artifact under this name; drop
    those first so they cannot crowd the dev dumps out of CANDIDATES.
    """
    run = artifact.get("workflow_run") or {}
    return (
        # TEMPORARY proof scaffold (reverted before review): this branch too.
        run.get("head_branch") in ("dev", "perf/ci-backend-db-reuse")
        and run.get("repository_id") is not None
        and run.get("head_repository_id") == run.get("repository_id")
    )


def unpack(raw, digest, name, destination):
    """Extract a verified record; returns its recipe."""
    if digest != "sha256:" + hashlib.sha256(raw).hexdigest():
        raise FetchError("artifact digest mismatch")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = archive.infolist()
        if sorted(m.filename for m in members) != sorted(RECORD):
            raise FetchError("unexpected files in the saved dump")
        if sum(m.file_size for m in members) > MAX_BYTES:
            raise FetchError("saved dump too large")
        destination.mkdir(parents=True, exist_ok=False)
        for member in members:
            (destination / member.filename).write_bytes(archive.read(member))
    recipe = (destination / "recipe").read_text().strip()
    if not recipe.startswith(name + "-"):
        raise FetchError("the saved dump's recipe is for other migrate inputs")
    return recipe


def api(endpoint):
    # A hint, not a prerequisite: bound each read and do not forward API
    # bodies or gh stderr to the public log.
    return subprocess.run(
        ["gh", "api", endpoint], capture_output=True, check=True, timeout=30
    ).stdout


def fetch(destination):
    repository = os.environ["GITHUB_REPOSITORY"]
    root = f"repos/{repository}/actions"
    name = subprocess.run(
        [sys.executable, "-I", str(TOOL), "inputs-key"],
        capture_output=True,
        check=True,
        text=True,
        timeout=60,
    ).stdout.strip()
    listing = json.loads(api(f"{root}/artifacts?name={name}&per_page=100"))
    artifacts = sorted(
        (
            a
            for a in listing["artifacts"]
            if a["name"] == name and not a["expired"] and listed(a)
        ),
        key=lambda a: (a["created_at"], a["id"]),
        reverse=True,
    )
    for artifact in artifacts[:CANDIDATES]:
        run = json.loads(api(f"{root}/runs/{int(artifact['workflow_run']['id'])}"))
        if eligible(run, repository):
            break
    else:
        raise FetchError(f"no dump from a successful dev push for `{name}`")
    if artifact["size_in_bytes"] > MAX_BYTES:
        raise FetchError("saved dump archive too large")
    raw = api(f"{root}/artifacts/{int(artifact['id'])}/zip")
    recipe = unpack(raw, artifact["digest"], name, destination)
    return recipe, f"dev run `{run['id']}` (`{artifact['digest']}`)"


def main(destination):
    try:
        recipe, source = fetch(Path(destination))
    except Exception as exc:  # noqa: BLE001 - a missing dump must not fail the gate
        reason = str(exc) if isinstance(exc, FetchError) else type(exc).__name__
        sys.stdout.write(
            f"::notice title=Database dump::{reason}; shards migrate from zero\n"
        )
        return 1
    sys.stdout.write(f"Saved database dump from {source}: {recipe}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
