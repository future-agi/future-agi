"""Read every census input even in a sparse checkout; never skip missing files."""

import subprocess
from pathlib import Path


def read_source(root, path):
    """Prefer working edits; only skip-worktree entries may fall back to index."""
    root, path = Path(root), Path(path)
    relative = path.relative_to(root)
    if relative.suffix not in {".py", ".sql"} or any(
        word in part.lower()
        for part in relative.parts
        for word in ("settings", "secret", ".env", "values")
    ):
        raise ValueError("source census refuses excluded path")
    try:
        return path.read_text()
    except FileNotFoundError:
        name = relative.as_posix()
        entry = subprocess.check_output(
            ["git", "-C", str(root), "ls-files", "-t", "-z", "--", name], text=True
        )
        if entry != "S " + name + "\0":
            raise  # A deleted/untracked input is not a sparse checkout fixture.
        return subprocess.check_output(
            ["git", "-C", str(root), "show", ":" + name], text=True
        )
