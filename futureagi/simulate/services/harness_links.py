import json
import os
import tempfile
from pathlib import Path


def links_root():
    return Path(
        os.environ.get("HARNESS_LINKS_DIR", "/app/harness-artifacts/platform-links")
    )


def _path_for(session_id):
    # Session ids are harness folder names; anything path-like is hostile.
    if not session_id or "/" in session_id or "\\" in session_id or ".." in session_id:
        return None
    return links_root() / f"{session_id}.json"


def remember(session_id, run_test_id, execution_id):
    path = _path_for(session_id)
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, staged = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(handle, "w", encoding="utf-8") as file:
        json.dump({"run_test_id": run_test_id, "execution_id": execution_id}, file)
    # Rename, not write-in-place: a reader must never see half a link.
    os.replace(staged, path)


def lookup(session_id):
    path = _path_for(session_id)
    if path is None:
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}
