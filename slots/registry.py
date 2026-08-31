"""Cross-worktree registry discovery, locking and atomic persistence."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import subprocess
from typing import Callable, Iterator, Sequence

from .models import Registry

CommandRunner = Callable[[Sequence[str], Path], str]


def _subprocess_runner(command: Sequence[str], cwd: Path) -> str:
    return subprocess.check_output(command, cwd=cwd, text=True).strip()


def discover_primary_worktree(
    cwd: Path, runner: CommandRunner = _subprocess_runner
) -> Path:
    """Return the primary worktree, falling back to cwd outside Git."""
    try:
        output = runner(("git", "worktree", "list", "--porcelain"), cwd)
    except (OSError, subprocess.CalledProcessError):
        return cwd.resolve()
    for line in output.splitlines():
        if line.startswith("worktree "):
            return Path(line.removeprefix("worktree ")).resolve()
    return cwd.resolve()


def discover_state_dir(
    cwd: Path | None = None,
    environ: dict[str, str] | None = None,
    runner: CommandRunner = _subprocess_runner,
) -> Path:
    environment = os.environ if environ is None else environ
    if override := environment.get("SLOTS_STATE_DIR"):
        return Path(override).expanduser().resolve()
    return discover_primary_worktree((cwd or Path.cwd()).resolve(), runner) / ".slots"


class RegistryStore:
    """A locked JSON store. Callers mutate only while ``locked`` is active."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.registry_path = state_dir / "registry.json"
        self.lock_path = state_dir / "registry.lock"

    def load(self) -> Registry:
        if not self.registry_path.exists():
            return Registry()
        with self.registry_path.open(encoding="utf-8") as handle:
            return Registry.from_dict(json.load(handle))

    def save(self, registry: Registry) -> None:
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = self.registry_path.with_suffix(".json.tmp")
        payload = json.dumps(registry.to_dict(), sort_keys=True, indent=2) + "\n"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.registry_path)
            os.chmod(self.registry_path, 0o600)
        finally:
            if temporary.exists():
                temporary.unlink()

    @contextmanager
    def locked(self) -> Iterator[Registry]:
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            os.chmod(self.lock_path, 0o600)
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            registry = self.load()
            try:
                yield registry
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
