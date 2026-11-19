from __future__ import annotations

import difflib
import hashlib
import io
import tarfile
import time
from collections.abc import Iterable
from typing import Any

import structlog
from django.core.cache import cache

from simulate.models import AgentVersion
from simulate.services.agent_code import (
    _CACHE_SECONDS,
    _ROOT,
    KIND_BINARY,
    KIND_TEXT,
    KIND_TOO_LARGE,
    Entry,
    VersionCode,
    _runtime,
    _within,
    archive_index,
    previous_version,
    safe_path,
    version_code,
)
from simulate.services.hosted_harness import HostedHarnessError

logger = structlog.get_logger(__name__)

MAX_COMPARE_FILES = 300
MAX_PATCH_BATCH = 25
AUTO_LOAD_LINES = 400
AUTO_LOAD_BYTES = 20 * 1024
MAX_DIFF_LINES = 5_000
MAX_DIFF_BYTES = 500 * 1024
DIFF_BUDGET_SECONDS = 5.0
CONTEXT_LINES = 3
TOO_LARGE: dict[str, Any] = {"too_large": True}

ADDED = "added"
REMOVED = "removed"
MODIFIED = "modified"
RENAMED = "renamed"


def _texts(code: VersionCode, paths: Iterable[str]) -> dict[str, str]:
    wanted = {_ROOT + path: path for path in paths}
    found: dict[str, str] = {}
    if not wanted:
        return found
    with tarfile.open(fileobj=io.BytesIO(code.archive), mode="r:gz") as tar:
        for member in tar:
            path = wanted.get(member.name)
            if path is None or not member.isfile():
                continue
            handle = tar.extractfile(member)
            if handle is None:
                continue
            try:
                found[path] = handle.read().decode("utf-8")
            except UnicodeDecodeError:
                continue
            if len(found) == len(wanted):
                break
    return found


def _patch(before: str, after: str) -> dict[str, Any]:
    old, new = before.splitlines(), after.splitlines()
    if old == new:
        return {"hunks": [], "additions": 0, "deletions": 0}
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    hunks = []
    additions = deletions = 0
    for group in matcher.get_grouped_opcodes(CONTEXT_LINES):
        lines: list[str] = []
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                lines.extend(" " + line for line in old[i1:i2])
                continue
            if tag in ("replace", "delete"):
                lines.extend("-" + line for line in old[i1:i2])
                deletions += i2 - i1
            if tag in ("replace", "insert"):
                lines.extend("+" + line for line in new[j1:j2])
                additions += j2 - j1
        first, last = group[0], group[-1]
        old_lines, new_lines = last[2] - first[1], last[4] - first[3]
        hunks.append(
            {
                "old_start": first[1] + 1 if old_lines else first[1],
                "old_lines": old_lines,
                "new_start": first[3] + 1 if new_lines else first[3],
                "new_lines": new_lines,
                "lines": lines,
            }
        )
    return {"hunks": hunks, "additions": additions, "deletions": deletions}


def _line_count(text: str) -> int:
    return text.count("\n") + (1 if text and not text.endswith("\n") else 0)


def _counted(hunks: list[dict[str, Any]]) -> dict[str, Any]:
    lines = [line for hunk in hunks for line in hunk["lines"]]
    return {
        "hunks": hunks,
        "additions": sum(1 for line in lines if line.startswith("+")),
        "deletions": sum(1 for line in lines if line.startswith("-")),
    }


def _patch_key(before: Entry | None, after: Entry | None) -> str:
    return (
        f"agent-code:patch:{before.sha256 if before else ''}"
        f":{after.sha256 if after else ''}"
    )


def _diffable(entry: Entry | None) -> bool:
    return entry is None or (entry.kind == KIND_TEXT and entry.size <= MAX_DIFF_BYTES)


def _kind_of(before: Entry | None, after: Entry | None) -> str:
    kinds = {one.kind for one in (before, after) if one is not None}
    if KIND_BINARY in kinds:
        return KIND_BINARY
    if KIND_TOO_LARGE in kinds or not (_diffable(before) and _diffable(after)):
        return KIND_TOO_LARGE
    return KIND_TEXT


def _pairs(
    before: dict[str, Entry], after: dict[str, Entry]
) -> list[tuple[str, str | None, str, Entry | None, Entry | None]]:
    removed = {path: before[path] for path in set(before) - set(after)}
    added = {path: after[path] for path in set(after) - set(before)}
    by_content: dict[str, list[str]] = {}
    for path in sorted(removed):
        by_content.setdefault(removed[path].sha256, []).append(path)
    pairs = []
    for path in sorted(added):
        moved_from = by_content.get(added[path].sha256)
        if moved_from:
            old_path = moved_from.pop(0)
            pairs.append((path, old_path, RENAMED, removed.pop(old_path), added[path]))
        else:
            pairs.append((path, None, ADDED, None, added[path]))
    pairs += [(path, None, REMOVED, entry, None) for path, entry in removed.items()]
    pairs += [
        (path, None, MODIFIED, before[path], after[path])
        for path in set(before) & set(after)
        if before[path].sha256 != after[path].sha256
    ]
    pairs.sort(key=lambda one: one[0])
    return pairs


def _sides(
    version: AgentVersion, against: AgentVersion
) -> tuple[VersionCode, VersionCode, dict[str, Entry], dict[str, Entry]]:
    code = version_code(version)
    base = version_code(against)
    before = {
        path: entry
        for path, entry in archive_index(base).items()
        if _within(path, code.subfolder)
    }
    after = {
        path: entry
        for path, entry in archive_index(code).items()
        if _within(path, code.subfolder)
    }
    return code, base, before, after


def _unavailable(version: AgentVersion, against: AgentVersion | None) -> dict | None:
    version_code(version)
    if against is None:
        return {"against": None, "unavailable": "no_previous_version"}
    try:
        version_code(against)
    except HostedHarnessError as exc:
        if exc.code != "no_code":
            raise
        return {"against": str(against.id), "unavailable": "no_code"}
    return None


def _env_changed(version: AgentVersion, against: AgentVersion) -> bool:
    mine, theirs = _runtime(version), _runtime(against)
    return (getattr(mine, "credential_set_hash", None) or "") != (
        getattr(theirs, "credential_set_hash", None) or ""
    )


def _empty_summary(against: str | None, unavailable: str) -> dict[str, Any]:
    return {
        "against": against,
        "unavailable": unavailable,
        "same_code": False,
        "env_changed": False,
        "files": [],
        "total_files": 0,
        "additions": 0,
        "deletions": 0,
        "truncated": False,
    }


def _computed(
    code: VersionCode,
    base: VersionCode,
    pairs: list[tuple[str, str | None, str, Entry | None, Entry | None]],
    *,
    deadline: float,
) -> dict[str, dict[str, Any] | None]:
    computed: dict[str, dict[str, Any] | None] = {}
    missing = []
    for pair in pairs:
        if _kind_of(pair[3], pair[4]) != KIND_TEXT:
            continue
        hunks = cache.get(_patch_key(pair[3], pair[4]))
        if hunks is None:
            missing.append(pair)
        else:
            computed[pair[0]] = _counted(hunks)
    texts_before = _texts(
        base,
        [
            old_path or path
            for path, old_path, _s, old, _n in missing
            if old is not None
        ],
    )
    texts_after = _texts(
        code, [path for path, _o, _s, _old, new in missing if new is not None]
    )
    for index, (path, old_path, _status, old, new) in enumerate(missing):
        if index and time.monotonic() > deadline:
            computed[path] = None
            continue
        before = texts_before.get(old_path or path, "") if old is not None else ""
        after = texts_after.get(path, "") if new is not None else ""
        if max(_line_count(before), _line_count(after)) > MAX_DIFF_LINES:
            computed[path] = TOO_LARGE
            continue
        patch = _patch(before, after)
        cache.set(_patch_key(old, new), patch["hunks"], _CACHE_SECONDS)
        computed[path] = patch
    return computed


def compare(version: AgentVersion, against: AgentVersion | None) -> dict[str, Any]:
    missing = _unavailable(version, against)
    if missing is not None:
        return _empty_summary(missing["against"], missing["unavailable"])
    code, base, before, after = _sides(version, against)
    pair = f"{base.identity}:{code.identity}:{code.subfolder}"
    key = f"agent-code:compare:{hashlib.sha256(pair.encode()).hexdigest()}"
    flags = {
        "same_code": base.identity == code.identity,
        "env_changed": _env_changed(version, against),
    }
    cached = cache.get(key)
    if cached is not None:
        return {"against": str(against.id), **flags, **cached}
    pairs = _pairs(before, after)
    shown = pairs[:MAX_COMPARE_FILES]
    computed = _computed(
        code, base, shown, deadline=time.monotonic() + DIFF_BUDGET_SECONDS
    )
    files = []
    additions = deletions = 0
    deferred = False
    for path, old_path, status, old, new in shown:
        row: dict[str, Any] = {
            "path": path,
            "previous_path": old_path,
            "status": status,
            "kind": _kind_of(old, new),
            "additions": None,
            "deletions": None,
            "collapsed": False,
        }
        if path not in computed:
            files.append(row)
            continue
        patch = computed[path]
        if patch is None:
            row["collapsed"] = True
            deferred = True
        elif patch is TOO_LARGE:
            row["kind"] = KIND_TOO_LARGE
        else:
            changed = patch["additions"] + patch["deletions"]
            size = sum(
                len(line) + 1 for hunk in patch["hunks"] for line in hunk["lines"]
            )
            row["additions"] = patch["additions"]
            row["deletions"] = patch["deletions"]
            row["collapsed"] = changed > AUTO_LOAD_LINES or size > AUTO_LOAD_BYTES
            additions += patch["additions"]
            deletions += patch["deletions"]
        files.append(row)
    summary = {
        "files": files,
        "total_files": len(pairs),
        "additions": additions,
        "deletions": deletions,
        "truncated": len(pairs) > len(shown),
    }
    if not deferred:
        cache.set(key, summary, _CACHE_SECONDS)
    return {"against": str(against.id), **flags, **summary}


def warm_compare(version: AgentVersion) -> None:
    try:
        compare(version, previous_version(version))
    except (HostedHarnessError, tarfile.TarError, OSError):
        logger.warning(
            "agent_code_compare_not_warmed", version_id=str(version.id), exc_info=True
        )


def patches(
    version: AgentVersion, against: AgentVersion, raw_paths: Iterable[str]
) -> dict[str, Any]:
    paths = list(dict.fromkeys(safe_path(one) for one in raw_paths))
    if len(paths) > MAX_PATCH_BATCH:
        raise HostedHarnessError(
            "too_many_paths",
            f"ask for at most {MAX_PATCH_BATCH} files at a time",
            status_code=400,
        )
    code, base, before, after = _sides(version, against)
    by_path = {pair[0]: pair for pair in _pairs(before, after)}
    found: dict[str, Any] = {}
    misses = []
    for path in paths:
        pair = by_path.get(path)
        if pair is None:
            found[path] = {"path": path, "kind": None, "unchanged": True, "hunks": []}
            continue
        _path, old_path, _status, old, new = pair
        kind = _kind_of(old, new)
        if kind != KIND_TEXT:
            found[path] = {"path": path, "kind": kind, "unchanged": False, "hunks": []}
            continue
        hunks = cache.get(_patch_key(old, new))
        if hunks is None:
            misses.append(pair)
            continue
        found[path] = {"path": path, "kind": kind, "unchanged": False, "hunks": hunks}
    computed = (
        _computed(code, base, misses, deadline=time.monotonic() + DIFF_BUDGET_SECONDS)
        if misses
        else {}
    )
    for path, patch in computed.items():
        shown = patch is not None and patch is not TOO_LARGE
        found[path] = {
            "path": path,
            "kind": KIND_TEXT if shown else KIND_TOO_LARGE,
            "unchanged": False,
            "hunks": patch["hunks"] if shown else [],
        }
    return {"against": str(against.id), "files": [found[path] for path in paths]}
