"""Export an installed SDK's reviewed local plan for the campaign executor.

Called with Python -I so repository code cannot shadow SDK imports. Only the
review document and changed file bytes are returned, never resolution leases
or credentials. Export itself neither uploads local files nor authorizes Git.

The export also derives a review-group key. Two targets share a key only when
their managed change is byte-identical and every other byte (the preserved
local segment and every unchanged file) is proven unchanged by this process.
Anything it cannot prove stays an individually reviewed exception.
"""

import copy
import hashlib
import json
import sys
from pathlib import Path

from fi.context.adapters.codex import split_managed
from fi.context.campaigns import target_plan
from fi.context.journal import decode, parse_plan
from fi.context.lock import CONTEXT_LOCK, canonical_json, parse_lock


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _normalized_lock(raw: bytes, planned: dict, local: bytes | None) -> dict:
    """Replace only hashes this process re-derived from the same plan."""
    lock = parse_lock(raw)  # Validates the derived materialization digest.
    value = copy.deepcopy(json.loads(raw))
    files = value["materialization"]["files"]
    for row in files:
        content = planned.get(row["destination"])
        if content is None or _sha(content) != row["sha256"]:
            raise ValueError("lock_file_not_planned_bytes")
        row["sha256"] = "verified:planned-bytes"
    if local is None or _sha(local) != lock.materialization.local_segment_sha256:
        raise ValueError("lock_local_segment_unverified")
    value["materialization"]["local_segment_sha256"] = "verified:preserved-local"
    value["materialization"]["materialization_digest"] = "verified:derived"
    return value


def review_group(plan, previous_raw: bytes | None) -> tuple[dict | None, str | None]:
    project = Path(plan.project)
    desired_raw = plan.changes[-1].require_content()
    desired = parse_lock(desired_raw)
    previous = parse_lock(previous_raw) if previous_raw is not None else None
    context_id = desired.context_id
    old = (
        {row.destination: row for row in previous.materialization.files}
        if previous
        else {}
    )
    new = {row.destination: row for row in desired.materialization.files}
    planned = {}
    changes = []
    local = None
    for change in plan.changes:
        if change.path == CONTEXT_LOCK:
            continue
        current = project / change.path
        before = current.read_bytes() if current.is_file() else None
        if (None if before is None else _sha(before)) != change.before_sha256:
            return None, "stale_local_preimage"
        after = None if change.after is None else change.require_content()
        if after is not None:
            planned[change.path] = after
        row = new.get(change.path)
        if after is None:
            changes.append({"path": change.path, "action": "remove"})
            continue
        if row is not None and row.transformation in ("entrypoint", "import"):
            prefix, block, suffix = split_managed(after, context_id)
            if before is None:
                if prefix or suffix:
                    return None, "unexpected_local_bytes"
                previous_block = None
            elif change.path in old:
                old_prefix, previous_block, old_suffix = split_managed(
                    before, context_id
                )
                if (old_prefix, old_suffix) != (prefix, suffix):
                    return None, "local_segment_changed"
                previous_block = previous_block.decode("utf-8")
            else:
                if prefix + suffix != before:
                    return None, "local_segment_changed"
                previous_block = None
            if change.path == "AGENTS.md":
                local = prefix + suffix
            changes.append(
                {
                    "path": change.path,
                    "action": "managed_block",
                    "ownership": row.ownership,
                    "before_block": previous_block,
                    "after_block": block.decode("utf-8"),
                }
            )
        else:
            changes.append(
                {
                    "path": change.path,
                    "action": "replace" if before is not None else "add",
                    "before_sha256": change.before_sha256,
                    "after_sha256": _sha(after),
                }
            )
    # Unchanged managed files still define the installed tree.
    for destination in new:
        if destination not in planned:
            current = project / destination
            if not current.is_file():
                return None, "managed_file_missing"
            planned[destination] = current.read_bytes()
    if local is None and "AGENTS.md" in new:
        content = planned["AGENTS.md"]
        prefix, _, suffix = split_managed(content, context_id)
        local = prefix + suffix
    try:
        lock = _normalized_lock(desired_raw, planned, local)
        prior = (
            _normalized_lock(previous_raw, _current(project, previous), _local(project, previous))
            if previous_raw is not None
            else None
        )
    except ValueError as error:
        return None, str(error)
    body = {
        "operation": plan.operation,
        "desired_version_id": desired.version_id,
        "profile": desired.materialization.profile,
        "changes": sorted(changes, key=lambda item: item["path"]),
        "lock": lock,
        "previous_lock": prior,
    }
    key = "sha256:" + _sha(canonical_json(body))
    return {"key": key, "delta": body, "local_preserved": True}, None


def _current(project: Path, lock) -> dict:
    files = {}
    for row in lock.materialization.files:
        current = project / row.destination
        if current.is_file():
            files[row.destination] = current.read_bytes()
    return files


def _local(project: Path, lock) -> bytes | None:
    current = project / "AGENTS.md"
    if not current.is_file():
        return None
    prefix, _, suffix = split_managed(current.read_bytes(), lock.context_id)
    return prefix + suffix


def main() -> None:
    plan_path, repository_id, base_commit, binding_revision, campaign_revision = sys.argv[1:]
    plan = parse_plan(Path(plan_path).read_bytes())
    wire = target_plan(
        plan,
        repository_id=repository_id,
        base_commit=base_commit,
        binding_revision=int(binding_revision),
        campaign_revision=int(campaign_revision),
    )
    changed = {row["path"] for row in wire["changes"]}
    contents = {
        change.path: change.after
        for change in plan.changes
        if change.path in changed and change.after is not None
    }
    group, exception = review_group(plan, decode(plan.previous_lock))
    print(
        json.dumps(
            {
                "plan": wire,
                "contents": contents,
                "review_group": group,
                "review_exception": exception,
            }
        )
    )


if __name__ == "__main__":
    main()
