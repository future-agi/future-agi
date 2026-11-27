"""Find the newest saved dump for this checkout's migrate inputs.

Only a dump that a successful Backend CI dev push of this repository saved:
never one from a fork, a pull request, another branch or another workflow.
Called once, by the plan job, which hands it to this run's jobs as a same-run
artifact. Every job that restores it still checks the full recipe, and the
control job checks it against a fresh migrate.

E2E CI saves a dump of its own (.ci/e2e_db.py) and fetches it the same way:
--workflow names the workflow whose dev pushes are trusted, --name the dump.
"""

import argparse
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


def eligible(run, repository, workflow=WORKFLOW):
    return (
        run["event"] == "workflow_dispatch"
        and run["head_branch"] == "test/TH-10965-e2e-db-proof"
        and run["conclusion"] == "success"
        and run["path"] == workflow
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
        run.get("head_branch") == "test/TH-10965-e2e-db-proof"
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


def fetch(destination, workflow=WORKFLOW, name=None):
    repository = os.environ["GITHUB_REPOSITORY"]
    root = f"repos/{repository}/actions"
    if name is None:
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
        if eligible(run, repository, workflow):
            break
    else:
        raise FetchError(f"no dump from a successful dev push for `{name}`")
    if artifact["size_in_bytes"] > MAX_BYTES:
        raise FetchError("saved dump archive too large")
    raw = api(f"{root}/artifacts/{int(artifact['id'])}/zip")
    recipe = unpack(raw, artifact["digest"], name, destination)
    return recipe, f"dev run `{run['id']}` (`{artifact['digest']}`)"


def main(destination, workflow=WORKFLOW, name=None):
    try:
        recipe, source = fetch(Path(destination), workflow, name)
    except Exception as exc:  # noqa: BLE001 - a missing dump must not fail the gate
        reason = str(exc) if isinstance(exc, FetchError) else type(exc).__name__
        sys.stdout.write(
            f"::notice title=Database dump::{reason}; shards migrate from zero\n"
        )
        return 1
    sys.stdout.write(f"Saved database dump from {source}: {recipe}\n")
    return 0


def cli(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("destination")
    parser.add_argument("--workflow", default=WORKFLOW)
    parser.add_argument("--name")
    args = parser.parse_args(argv)
    return main(args.destination, args.workflow, args.name)


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))
