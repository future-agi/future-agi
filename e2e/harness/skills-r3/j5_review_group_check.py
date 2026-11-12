"""Fallibility check for the campaign exporter's review grouping.

Run with the installed SDK interpreter: ``python -I j5_review_group_check.py``.
It needs no hub or network. Positive control: two repositories with different
local instructions share one key, because the managed delta is identical and
every local byte is proven preserved. Negative controls: a repository with a
different existing entrypoint, a plan that drops a local byte, a plan whose
managed bytes differ and a stale local preimage never join that group.
"""

import importlib.util
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from fi.context.journal import decode, parse_plan
from fi.context.lock import (
    Identity,
    Lease,
    Selector,
    SourceBundle,
    canonical_json,
    manifest_digest,
)
from fi.context.materialize import apply_plan, plan_install

HERE = Path(__file__).resolve()
EXPORT = HERE.parents[3] / "bin" / "lib" / "context-campaign-export.py"
spec = importlib.util.spec_from_file_location("context_campaign_export", EXPORT)
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


def uid(number: int) -> str:
    return str(UUID(int=number))


def bundle(version: int) -> SourceBundle:
    files = {"AGENTS.md": f"Reviewed managed release {version}.\n".encode()}
    files[".futureagi/context.json"] = canonical_json(
        {
            "schema": 1,
            "kind": "agent_context",
            "name": "team-policy",
            "description": "Reviewed policy",
            "entrypoint": "AGENTS.md",
            "owned_paths": ["AGENTS.md"],
            "links": [],
        }
    )
    return SourceBundle(
        identity=Identity(
            origin="https://registry.example.invalid",
            organization_id=uid(1),
            workspace_id=uid(2),
            context_id=uid(4),
            name="team-policy",
            requested=Selector(label="production"),
            version=version,
            version_id=uid(40 + version),
            digest=manifest_digest(files),
        ),
        lease=Lease(
            resolution_id="synthetic-lease",
            resolution_expires_at=(
                datetime.now(timezone.utc) + timedelta(minutes=10)
            ).isoformat(),
        ),
        files=files,
    )


def project(root: Path, name: str, files: dict[str, bytes]) -> Path:
    path = root / name
    for relative, content in files.items():
        (path / relative).parent.mkdir(parents=True, exist_ok=True)
        (path / relative).write_bytes(content)
    return path


def plan(source, path, operation="install"):
    return plan_install(
        source,
        project=path,
        profile="claude-code",
        adopt_local=True,
        allow_preview=True,
        operation=operation,
    )


def key(value):
    group, exception = exporter.review_group(value, decode(value.previous_lock))
    return (group["key"] if group else None), exception


def with_after(value, path: str, content: bytes):
    import base64

    changes = [
        change.model_copy(update={"after": base64.b64encode(content).decode()})
        if change.path == path
        else change
        for change in value.changes
    ]
    return value.model_copy(update={"changes": changes})


def main() -> int:
    results = {}
    with tempfile.TemporaryDirectory() as directory:
        os.environ["HOME"] = directory
        root = Path(directory)
        one = project(root, "one", {"AGENTS.md": b"Local policy: never disclose records.\n", "CLAUDE.local.md": b"one\n"})
        two = project(root, "two", {"AGENTS.md": b"Existing local instructions remain owned.\n", "CLAUDE.local.md": b"two\n"})
        claude = project(root, "claude", {"AGENTS.md": b"Existing local instructions remain owned.\n", "CLAUDE.md": b"Existing Claude policy\n"})
        first = bundle(1)
        a, b, c = plan(first, one), plan(first, two), plan(first, claude)
        ka, kb, kc = key(a)[0], key(b)[0], key(c)[0]
        results["positive_same_group"] = ka is not None and ka == kb
        results["different_entrypoint_separate"] = kc is not None and kc != ka
        local = (one / "AGENTS.md").read_bytes()
        block = decode(next(row.after for row in a.changes if row.path == "AGENTS.md"))[: -len(local)]
        dropped = key(with_after(a, "AGENTS.md", block + local[:-2] + b"\n"))
        results["dropped_local_byte_excluded"] = dropped == (None, "local_segment_changed")
        changed = key(with_after(a, "AGENTS.md", block.replace(b"release 1", b"release 9") + local))
        results["changed_managed_bytes_excluded"] = changed[0] != ka
        (two / "AGENTS.md").write_bytes(b"Edited after planning.\n")
        results["stale_preimage_excluded"] = key(b) == (None, "stale_local_preimage")
        # Recurring update after install: same delta across differing locals.
        (two / "AGENTS.md").write_bytes(b"Existing local instructions remain owned.\n")
        b = plan(first, two)
        for value in (a, b):
            apply_plan(parse_plan(value.to_bytes()), authorize=lambda auth: None, quiescent=True)
        second = bundle(2)
        ua, ub = plan(second, one, "update"), plan(second, two, "update")
        results["update_same_group"] = key(ua)[0] is not None and key(ua)[0] == key(ub)[0]
        results["update_distinct_from_install"] = key(ua)[0] != ka
    print(json.dumps(results, sort_keys=True))
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
