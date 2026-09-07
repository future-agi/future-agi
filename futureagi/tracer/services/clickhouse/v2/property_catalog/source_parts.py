"""Durable positive notices from committed canonical parts, not row clocks.

Part replacement/removal is conservatively treated as a possible historical
change. This does not replace the independent content audit. In particular a
background merge can cause extra work; merge-neutrality and large-source cost
must be established before calling this a production performance optimization.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .codec import canonical_uuid
from .coordinator import FileSupersessionJournal
from .repair_files import locked_file
from .source_repair import record_source_repair

MAX_SOURCE_PARTS = 10_000
_FORMAT = "futureagi.property-catalog-source-parts.v1"


def checked_parts(parts) -> tuple[tuple[str, str], ...]:
    if not isinstance(parts, (tuple, list)) or len(parts) > MAX_SOURCE_PARTS:
        raise ValueError("source part inventory exceeds its metadata bound")
    checked = []
    for item in parts:
        if (
            not isinstance(item, (tuple, list))
            or len(item) != 2
            or not isinstance(item[0], str)
            or re.fullmatch(r"[A-Za-z0-9_-]{1,256}", item[0]) is None
            or not isinstance(item[1], str)
            or re.fullmatch(r"[a-f0-9]{1,32}", item[1]) is None
        ):
            raise ValueError("source part identity/checksum is invalid")
        checked.append(tuple(item))
    if checked != sorted(checked) or len({item[0] for item in checked}) != len(checked):
        raise ValueError("source part inventory is unordered or conflicting")
    return tuple(checked)


def notice_part_changes(
    *,
    reader,
    state_directory: str,
    organization_id: str,
    workspace_id: str,
    source_database: str,
    project_ids: tuple[str, ...],
    since: datetime,
    until: datetime,
    observed_at: datetime,
    full_replacement: bool,
    started_parts: tuple[tuple[str, str], ...] | None,
    scan_since: datetime | None = None,
    scope_audit_check=None,
    positive_only: bool = False,
) -> bool:
    """Persist any notice BEFORE acknowledging an inventory, before activation.

    For a newly scanned full replacement, compare with parts captured before
    its scan. A resumed fenced build has no fresh scan and passes None instead;
    it must compare with the durable prior inventory (or request repair).
    Increments distinguish earlier history from their just-audited interval:
    a part arriving after that audit cannot be acknowledged as covered merely
    because its event time is inside the increment rather than before it.

    Preplanning uses positive_only against the durable prior inventory: it may
    request repair, but must not advance any inventory or claim scan coverage.
    """
    if type(positive_only) is not bool or (
        positive_only
        and (
            full_replacement
            or started_parts is not None
            or scan_since is not None
            or scope_audit_check is not None
        )
    ):
        raise ValueError("positive-only observation cannot claim scan coverage")
    org = canonical_uuid(organization_id, field="organization_id")
    workspace = canonical_uuid(workspace_id, field="workspace_id")
    projects = tuple(
        sorted({canonical_uuid(p, field="project_id") for p in project_ids})
    )
    if not projects or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", source_database) is None:
        raise ValueError("source part watch requires exact authorized scope")
    root = Path(state_directory)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ValueError("source part watch requires the persistent control directory")
    journal = FileSupersessionJournal(state_directory)
    key = f"source-parts:{org}:{workspace}:{source_database}"
    identity = {
        "format": _FORMAT,
        "source_database": source_database,
        "source_table": "spans",
        "organization_id": org,
        "workspace_id": workspace,
    }
    with locked_file(root / f"parts-{org}-{workspace}"):
        previous = journal.load_record(key)
        if previous is not None:
            if set(previous) != {*identity, "project_ids", "parts"} or any(
                previous.get(k) != v for k, v in identity.items()
            ):
                raise ValueError("source part inventory changed scope or format")
            old = checked_parts(previous["parts"])
            prior_projects = previous["project_ids"]
            if (
                not isinstance(prior_projects, list)
                or prior_projects
                != sorted(
                    {canonical_uuid(p, field="project_id") for p in prior_projects}
                )
                or not prior_projects
            ):
                raise ValueError("source part project inventory is corrupt")
        else:
            old = None
        current = checked_parts(reader.parts_snapshot())
        if full_replacement and started_parts is not None:
            old = checked_parts(started_parts)
        elif previous is not None and previous["project_ids"] != list(projects):
            old = None

        probed_parts = False

        def changed_event(baseline, lower, upper):
            nonlocal probed_parts
            if lower >= upper:
                return None
            if baseline is None or set(dict(baseline)) - set(dict(current)):
                # Removed parts might have held the last surviving value. An
                # empty new-part query cannot prove the old catalog still fits.
                return lower
            old_map = dict(baseline)
            changed = tuple(
                name for name, checksum in current if old_map.get(name) != checksum
            )
            if changed:
                probed_parts = True
                return reader.history_in_parts(
                    project_ids=projects,
                    since=lower,
                    until=upper,
                    part_names=changed,
                )
            return None

        if scan_since is not None and not since <= scan_since <= until:
            raise ValueError("increment scan boundary escapes source watch scope")
        scope_result = None
        if scope_audit_check is not None:
            try:
                scope_result = scope_audit_check(
                    current=current,
                    previous=old,
                    started=started_parts,
                    since=since,
                    until=until,
                )
                if scope_result is not None and type(scope_result) is not bool:
                    raise ValueError("invalid accepted-scope verification outcome")
            except Exception:
                # Failed proof work may have persisted dirty coverage. It may
                # never become an empty/negative source observation.
                scope_result = False
        if scope_result is True:
            seen_at = None
            probed_parts = True  # Preserve the final metadata race recheck.
        elif scope_result is False:
            seen_at = since
        elif not full_replacement and scan_since is not None:
            if not since <= scan_since <= until:
                raise ValueError("increment scan boundary escapes source watch scope")
            seen_at = changed_event(old, since, scan_since)
            if seen_at is None:
                # Only the pre-scan inventory can establish that these parts
                # existed before this interval's values/audit. No snapshot on
                # a resumed FENCED attempt means conservative pending repair.
                start = (
                    checked_parts(started_parts) if started_parts is not None else None
                )
                seen_at = changed_event(start, scan_since, until)
        else:
            seen_at = changed_event(old, since, until)
        if (
            seen_at is None
            and probed_parts
            and checked_parts(reader.parts_snapshot()) != current
        ):
            # Metadata and a _part-filtered source query are not one snapshot.
            # A merge/removal between them can make an unseen historical row
            # disappear from that query without disappearing from canonical
            # history. Do not acknowledge its empty result as coverage. Keep
            # the originally observed inventory and persist a repair first;
            # subsequent changes remain visible to the next comparison.
            seen_at = since
        if seen_at is not None:
            record_source_repair(
                state_directory=state_directory,
                organization_id=org,
                workspace_id=workspace,
                source_database=source_database,
                seen_at=seen_at,
                observed_at=observed_at,
            )
        document = {
            **identity,
            "project_ids": list(projects),
            "parts": [list(p) for p in current],
        }
        if not positive_only and document != previous:
            journal.save_record(key, document)
        return seen_at is not None
