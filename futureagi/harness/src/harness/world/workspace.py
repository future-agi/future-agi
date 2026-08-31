"""Standing the environment up in containers, with the harness deciding what that means.

The harness has read the agent's repository, so it knows what running that agent's code takes:
which base image, which install command, which store, which services. Encoding any of that here
would be guessing on behalf of an agent nobody has seen yet, and would be wrong for the next one.

So this provides two things and no opinions:

- a place to write files, under the session's own ``env`` directory
- a way to run container commands from there, and read back what happened

Everything else, the Dockerfile, the compose file, the schema, the entrypoint, is written by
whoever read the repository. What is enforced is only what keeps this safe to run on somebody's
machine: files stay inside the environment directory, and the only commands that run are container
commands.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ENV = "env"

# Only these. Not a general shell: a tool that can run anything is a tool with no guardrail, and
# the whole point of routing through here is that what happens is inspectable and bounded.
ALLOWED = ("docker", "docker-compose")

# Long enough for an image build that downloads a base layer, short enough that a hung build is
# reported rather than waited on forever.
PATIENCE = 900


def env_root(destination: Path) -> Path:
    """Where this agent's environment definition lives, beside its world."""
    root = Path(destination) / ENV
    root.mkdir(parents=True, exist_ok=True)
    return root


def inside(destination: Path, path: str) -> Path:
    """The full path for a file the harness wants to write, refused if it escapes.

    A path arrives as text from a model, so it is resolved and then checked rather than trusted.
    Writing outside the environment directory would mean the harness could touch anything on the
    machine it happens to be running on, which is not a thing to leave to a prompt.
    """
    root = env_root(destination).resolve()
    asked = (root / str(path).lstrip("/")).resolve()
    if not asked.is_relative_to(root):
        raise ValueError(
            f"{path!r} is outside the environment directory. Everything the environment needs "
            "lives under env/, so that building it cannot reach the rest of the machine."
        )
    return asked


def write(destination: Path, path: str, contents: str) -> Path:
    """Put one file into the environment definition."""
    target = inside(destination, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(contents, encoding="utf-8")
    return target


def listing(destination: Path) -> list[str]:
    root = env_root(destination)
    return sorted(
        str(found.relative_to(root)) for found in root.rglob("*") if found.is_file()
    )


def available() -> str:
    """Why containers cannot be used here, or an empty string when they can."""
    if not shutil.which("docker"):
        return "docker is not installed, or not on the path"
    done = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if done.returncode != 0:
        return f"docker is installed but not running: {(done.stderr or '').strip()[:200]}"
    return ""


def run(destination: Path, command: str, *, patience: int = PATIENCE) -> tuple[int, str]:
    """Run one container command from the environment directory.

    Returns the exit code and the output, both streams together, because a build failure explains
    itself across the two and reading only one is how the actual cause gets lost.
    """
    words = command.split()
    if not words:
        return 1, "no command given"
    if words[0] not in ALLOWED:
        return 1, (
            f"{words[0]!r} is not something this can run. Only {' and '.join(ALLOWED)} commands, "
            "because a general shell here would be a guardrail with nothing behind it. Everything "
            "the environment needs should be in a file it builds from, not in a command."
        )
    blocked = available()
    if blocked:
        return 1, blocked
    # When the daemon is remote (DOCKER_HOST at a socket proxy), a bind mount
    # names a path on the daemon's host — this container's own filesystem is
    # invisible to it. The mount comes up empty and the failure reads as a
    # missing file three steps later, so it is refused here with the reason.
    if os.environ.get("DOCKER_HOST") and (" -v " in f" {command} " or "--volume" in command):
        return 1, (
            "bind mounts cannot work in this deployment: the docker daemon runs "
            "outside this container and does not see these paths. Run the script "
            "inline instead (sh -c '<script>'), or COPY files into an image with "
            "a Dockerfile — build contexts do transfer."
        )
    try:
        done = subprocess.run(
            words,
            cwd=str(env_root(destination)),
            capture_output=True,
            text=True,
            timeout=patience,
        )
    except subprocess.TimeoutExpired:
        return 1, (
            f"gave up after {patience}s. An install that takes this long usually means a "
            "dependency is being fetched that is not going to arrive; check what the last step "
            "was trying to reach."
        )
    output = ((done.stdout or "") + (done.stderr or "")).strip()
    return done.returncode, output
