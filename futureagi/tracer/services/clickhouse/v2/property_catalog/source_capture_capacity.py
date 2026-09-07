"""Source-local disk headroom admission, NOT a filesystem byte reservation.

The capture preserves the source storage policy. Qualify every policy disk and
require all active source-part disks to belong to it. Each relevant disk must be
writable Local storage; unrelated server disks do not confer or deny capacity.
Metadata is bounded and carries the source server/table identity in one query.

ATTACH hardlinks existing parts, so proposed_bytes is not an allocation estimate.
Spare headroom is checked independently on each disk, without requiring a second
copy of the source bytes or summing paths that might share a filesystem. Other
writers can consume it immediately. The manager's durable reservations bound
potential tables; native part/retained-byte bounds remain independently required.
"""

from __future__ import annotations

import re
from typing import Any

from .source_capture import SourceCaptureError, SourceCaptureSpec
from .source_capture_reservations import SourceCaptureBackpressure

MIN_CAPTURE_HEADROOM_BYTES = 64 << 20
MAX_CAPTURE_POLICY_DISKS = 32
_UINT64_MAX = 2**64 - 1
_NAME = re.compile(r"[A-Za-z0-9_-]{1,256}\Z", re.ASCII)
_FIELDS = (
    "name",
    "path",
    "total_space",
    "unreserved_space",
    "keep_free_space",
    "type",
    "is_read_only",
)
_SELECTED = ", ".join(f"`{name}`" for name in _FIELDS)
_DISKS_SQL = f"""WITH capture_source_table AS (
 SELECT toString(uuid) AS table_uuid, storage_policy FROM system.tables
 WHERE database=%(database)s AND name='spans' LIMIT 2
), capture_policy_disk_names AS (
 SELECT DISTINCT arrayJoin(disks) AS disk_name FROM system.storage_policies
 WHERE policy_name IN (SELECT storage_policy FROM capture_source_table)
 ORDER BY disk_name LIMIT {MAX_CAPTURE_POLICY_DISKS + 1}
)
SELECT toString(serverUUID()) AS capture_server_uuid,
 (SELECT groupArray(tuple(table_uuid, storage_policy)) FROM capture_source_table) AS capture_source,
 (SELECT groupArray(disk_name) FROM capture_policy_disk_names) AS capture_policy_disks,
 (SELECT groupArray(disk_name) FROM (
  SELECT DISTINCT disk_name FROM system.parts
  WHERE database=%(database)s AND table='spans' AND active
  ORDER BY disk_name LIMIT {MAX_CAPTURE_POLICY_DISKS + 1}
 )) AS capture_part_disks,
 (SELECT groupArray(tuple({_SELECTED})) FROM (
  SELECT {_SELECTED} FROM system.disks
  WHERE name IN (SELECT disk_name FROM capture_policy_disk_names)
  ORDER BY name LIMIT {MAX_CAPTURE_POLICY_DISKS + 1}
 )) AS capture_disks"""
_RESULT_FIELDS = (
    "capture_server_uuid",
    "capture_source",
    "capture_policy_disks",
    "capture_part_disks",
    "capture_disks",
)


def _disk_names(values, *, empty=False):
    if (
        not isinstance(values, (list, tuple))
        or not (0 if empty else 1) <= len(values) <= MAX_CAPTURE_POLICY_DISKS
        or any(not isinstance(v, str) or not _NAME.fullmatch(v) for v in values)
        or len(set(values)) != len(values)
    ):
        raise SourceCaptureError(
            "capture disk inventory is incomplete or exceeds its bound"
        )
    return set(values)


class SourceCaptureCapacity:
    """Bound callable for NativeSourceCaptureBackend.capacity_reservation.

    Call under the manager's existing namespace lock, not as a cached factory
    check. This does not replace NativeSourceCaptureBackend.check_capacity or
    reserve any filesystem space. No new configuration or query settings are
    sent to the normal readonly=1 profile.
    """

    def __init__(self, spec: SourceCaptureSpec, readonly_reader: Any):
        if type(spec) is not SourceCaptureSpec:
            raise TypeError("capture capacity requires its exact validated spec")
        spec.__post_init__()
        self._spec, self._reader = spec, readonly_reader
        self._user = getattr(readonly_reader, "user", None)
        self._validate(spec)

    def _validate(self, spec):
        if type(spec) is not SourceCaptureSpec or spec != self._spec:
            raise SourceCaptureError("capture capacity cannot change its bound spec")
        spec.__post_init__()
        if (
            getattr(self._reader, "database", None) != spec.source_database
            or getattr(self._reader, "server_enforced_readonly", None) is not True
            or not isinstance(self._user, str)
            or not self._user
            or getattr(self._reader, "user", None) != self._user
            or not callable(getattr(self._reader, "execute_read", None))
        ):
            raise SourceCaptureError(
                "capture capacity requires its source read-only identity"
            )

    def __call__(self, spec: SourceCaptureSpec, proposed_bytes: int) -> None:
        self._validate(spec)
        if type(proposed_bytes) is not int or not 0 <= proposed_bytes < _UINT64_MAX:
            raise SourceCaptureError("capture proposed bytes must be a finite UInt64")
        for disk in self._disk_space():
            headroom = max(MIN_CAPTURE_HEADROOM_BYTES, (disk["total_space"] + 9) // 10)
            if headroom > disk["unreserved_space"]:
                raise SourceCaptureBackpressure(
                    "source disk has insufficient capture headroom"
                )

    def resource_budget(self):
        """Derive retained-byte accounting from actual source-local capacity."""
        from .source_capture_native import CaptureResourceBudget

        total = min(disk["total_space"] for disk in self._disk_space())
        # Two hardlinked captures can account for the same physical bytes twice.
        # Use the smallest disk ceiling, never the sum of possible filesystem
        # aliases. Actual spare headroom is checked separately before CREATE.
        return CaptureResourceBudget(2, 10_000, min(_UINT64_MAX, 2 * total))

    def _disk_space(self):
        self._validate(self._spec)
        spec = self._spec
        result = self._reader.execute_read(
            _DISKS_SQL, {"database": spec.source_database}, timeout_ms=30_000
        )
        try:
            rows, columns, _ = result
            names = tuple(c[0] if isinstance(c, tuple) else c for c in columns)
            if (
                names != _RESULT_FIELDS
                or len(rows) != 1
                or len(rows[0]) != len(_RESULT_FIELDS)
            ):
                raise ValueError("result shape changed")
            server, source, policy_names, part_names, disks = rows[0]
            if server != spec.source_server_uuid:
                raise SourceCaptureError(
                    "capture capacity reached another source member"
                )
            if (
                not isinstance(source, (list, tuple))
                or len(source) != 1
                or not isinstance(source[0], (list, tuple))
                or len(source[0]) != 2
                or source[0][0] != spec.source_table_uuid
                or not isinstance(source[0][1], str)
                or not _NAME.fullmatch(source[0][1])
            ):
                raise SourceCaptureError(
                    "capture source table UUID or storage policy changed"
                )
            policy_names = _disk_names(policy_names)
            if not _disk_names(part_names, empty=True) <= policy_names:
                raise SourceCaptureError(
                    "source parts use disks outside the capture policy"
                )
            if (
                not isinstance(disks, (tuple, list))
                or not 1 <= len(disks) <= MAX_CAPTURE_POLICY_DISKS
            ):
                raise SourceCaptureError(
                    "capture policy disk metadata is incomplete or exceeds its bound"
                )
            if any(not isinstance(disk, (tuple, list)) for disk in disks):
                raise ValueError("disk row is not a tuple")
            disks = tuple(dict(zip(_FIELDS, disk, strict=True)) for disk in disks)
            if _disk_names([disk["name"] for disk in disks]) != policy_names:
                raise SourceCaptureError("capture policy disk metadata is incomplete")
        except (TypeError, ValueError, IndexError) as exc:
            raise SourceCaptureError("capture disk metadata is malformed") from exc
        for disk in disks:
            if (
                disk["type"] != "Local"
                or type(disk["is_read_only"]) is not int
                or disk["is_read_only"] != 0
                or not isinstance(disk["path"], str)
                or not disk["path"].startswith("/")
                or len(disk["path"]) > 4096
                or "\x00" in disk["path"]
            ):
                raise SourceCaptureError("capture policy requires writable Local disks")
            for field in ("total_space", "unreserved_space", "keep_free_space"):
                if type(disk[field]) is not int or not 0 <= disk[field] < _UINT64_MAX:
                    raise SourceCaptureError(
                        "capture disk capacity is not a finite UInt64"
                    )
            if (
                not disk["total_space"]
                or disk["unreserved_space"] > disk["total_space"]
            ):
                raise SourceCaptureError(
                    "capture disk capacity is inconsistent or unavailable"
                )
        # ClickHouse 25.3 DiskLocal already subtracts keep_free_space from both
        # total_space and unreserved_space. Do not subtract it a second time.
        # src/Disks/DiskLocal.cpp:getTotalSpace/getAvailableSpace/getUnreservedSpace
        return disks
