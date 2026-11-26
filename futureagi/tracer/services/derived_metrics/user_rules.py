"""B20: exact, bounded user reductions of pinned committed conversation fields.

No routes, user/window persistence, raw-span reads, or rollout enablement live here.
The application adapter reuses B07's exact physical branches. Its caller must hold
``pinned_read`` and read admission through assembly and supply the lifecycle guard
and authoritative retention boundary. B21 owns those request/surface lifetimes.

R2 deliberately trades repeated source scans for bounded live state. Neither this
implementation nor a SMALL development run qualifies M13's 10M/complete-request
gates. The source client must be a local, admitted native streaming read client;
never pass the eager interactive client or change the writer's memory settings.
"""

from __future__ import annotations

import math
import struct
import time
from bisect import bisect_right
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from threading import BoundedSemaphore
from uuid import UUID

from tracer.services.clickhouse.application_read_policy import application_read_context
from tracer.services.clickhouse.native_read_stream import _ManagedNativeReadStream
from tracer.services.derived_metrics.storage import FIELD_VALUES

MAX_COUNT = (1 << 64) - 1
MAX_COEFFICIENT = ((1 << 53) - 1) << 2045
RULES = frozenset({"any", "every", "average", "total"})
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
SNAP_US = 900_000_000
# Hard maxima, not expected hash occupancy. Tests may use smaller caps.
MAX_ROWS = 65_536
MAX_PAGE_BYTES = 16 * 1024 * 1024
MAX_STATES = 32_768
MAX_KEY_BYTES = 1024
MAX_LIVE_KEY_BYTES = 16 * 1024 * 1024
MAX_DESCRIPTORS = 256
SOURCE_SETTINGS = {
    "max_threads": 1,
    "max_threads_for_indexes": 1,
    "max_memory_usage": 1610612736,
    "max_bytes_before_external_group_by": 0,
    "max_bytes_ratio_before_external_group_by": 0,
    "max_bytes_before_external_sort": 0,
    "max_bytes_ratio_before_external_sort": 0,
    "group_by_two_level_threshold": 0,
    "group_by_two_level_threshold_bytes": 0,
    "group_by_overflow_mode": "throw",
    "set_overflow_mode": "throw",
    "max_bytes_in_set": 134217728,
    "max_result_rows": MAX_ROWS + 1,
    "max_result_bytes": MAX_PAGE_BYTES,
    "result_overflow_mode": "throw",
    "timeout_overflow_mode": "throw",
    "read_overflow_mode": "throw",
    "use_query_cache": 0,
    "max_execution_time": 15,
    "max_block_size": 1024,
    "preferred_block_size_bytes": 65536,
    "optimize_aggregation_in_order": 0,
    "optimize_use_projections": 0,
    # Byte strings preserve identities, including invalid UTF-8 and NUL.
    "strings_as_bytes": True,
}
# Reserve worst-size CPython counters/keys/tuple/dict growth BEFORE insertion.
# Numeric integer state is <=2162 bits, independent of conversation cardinality.
STATE_RESERVATION = 2048
ROW_RESERVATION = 768


class UserRulesUnavailable(RuntimeError):
    """A complete result could not be proven; never expose tentative values."""


class _Split(Exception):
    def __init__(self, pivot: bytes | None = None):
        self.pivot = pivot


def _rule(rule):
    if rule not in RULES:
        raise ValueError("unsupported user rule")
    return rule


def _count(value):
    if type(value) is not int or not 0 <= value <= MAX_COUNT:
        raise UserRulesUnavailable("corrupt_state")
    return value


def _add_count(left, right=1):
    result = _count(left) + _count(right)
    if result > MAX_COUNT:
        raise UserRulesUnavailable("count_limit")
    return result


def coefficient(value: float) -> int:
    """Exact binary64 coefficient on the 2**-1074 lattice; no float sum."""
    if type(value) not in (int, float) or not math.isfinite(value):
        raise UserRulesUnavailable("corrupt_state")
    bits: int = struct.unpack(">Q", struct.pack(">d", value))[0]
    exponent = (bits >> 52) & 2047
    fraction = bits & ((1 << 52) - 1)
    magnitude = fraction if exponent == 0 else ((1 << 52) | fraction) << (exponent - 1)
    return -magnitude if bits >> 63 else magnitude


def _rounded(numerator, denominator):
    if type(numerator) is not int or type(denominator) is not int or denominator <= 0:
        raise UserRulesUnavailable("corrupt_state")
    try:
        value = float(Fraction(numerator, denominator))
    except OverflowError:
        return None
    return value if math.isfinite(value) else None


@dataclass(slots=True)
class ExactSum:
    count: int = 0
    total: int = 0

    def validate(self):
        _count(self.count)
        if (
            type(self.total) is not int
            or abs(self.total) > self.count * MAX_COEFFICIENT
        ):
            raise UserRulesUnavailable("corrupt_state")

    def add(self, value):
        # Internal values are already normalized: corruption is NOT missing.
        self.validate()
        count = _add_count(self.count)
        total = self.total + coefficient(value)
        self.count, self.total = count, total

    def merge(self, other):
        self.validate()
        other.validate()
        count = _add_count(self.count, other.count)
        self.count, self.total = count, self.total + other.total

    def value(self, *, average):
        self.validate()
        if not self.count:
            return None
        result = _rounded(self.total, (self.count if average else 1) << 1074)
        if average and result is None:
            raise UserRulesUnavailable("corrupt_state")
        return result

    def leave_one_out(self, value):
        """Full mean minus mean without one *eligible finalized* user."""
        self.validate()
        target = coefficient(value)
        if self.count <= 1:
            return None
        return _rounded(
            self.count * target - self.total,
            (self.count * (self.count - 1)) << 1074,
        )


@dataclass(frozen=True, slots=True)
class UserValue:
    value: bool | float | None
    state: str
    conversations: int
    with_value: int
    missing: int
    sampled: int


@dataclass(slots=True)
class RuleState:
    rule: str
    conversations: int = 0
    with_value: int = 0
    decisive: int = 0
    sampled: int = 0
    numeric: ExactSum = field(default_factory=ExactSum)

    def validate(self):
        _rule(self.rule)
        for count in (self.conversations, self.with_value, self.decisive, self.sampled):
            _count(count)
        if not (
            self.decisive <= self.with_value <= self.conversations
            and self.sampled <= self.conversations
        ):
            raise UserRulesUnavailable("corrupt_state")
        self.numeric.validate()
        expected = self.with_value if self.rule in {"average", "total"} else 0
        if self.numeric.count != expected:
            raise UserRulesUnavailable("corrupt_state")

    def add(self, value, *, probability=1.0, invalid_probability=False):
        self.validate()
        conversations = _add_count(self.conversations)
        bad = invalid_probability or not (
            type(probability) in (int, float)
            and math.isfinite(probability)
            and probability == 1
        )
        observed = False
        if self.rule in {"any", "every"}:
            observed = type(value) in (bool, int) and value in (0, 1)
        elif type(value) in (int, float):
            try:
                observed = math.isfinite(value)
            except OverflowError:
                observed = False
        if observed:
            if self.rule in {"average", "total"}:
                self.numeric.add(value)
            else:
                decisive = bool(value) if self.rule == "any" else not bool(value)
                self.decisive = _add_count(self.decisive, int(decisive))
            self.with_value = _add_count(self.with_value)
        self.conversations = conversations
        self.sampled = _add_count(self.sampled, int(bad))

    def merge(self, other):
        self.validate()
        other.validate()
        if self.rule != other.rule:
            raise UserRulesUnavailable("corrupt_state")
        counts = tuple(
            _add_count(getattr(self, name), getattr(other, name))
            for name in ("conversations", "with_value", "decisive", "sampled")
        )
        self.numeric.merge(other.numeric)
        self.conversations, self.with_value, self.decisive, self.sampled = counts

    def finish(self):
        self.validate()
        value = None
        state = "no_observed_value"
        if self.conversations:
            if self.rule in {"any", "every"}:
                if self.decisive:
                    value = self.rule == "any"
                elif self.with_value == self.conversations:
                    value = self.rule == "every"
                else:
                    state = "incomplete_inputs"
            else:
                value = self.numeric.value(average=self.rule == "average")
                if value is None and self.with_value:
                    state = "non_finite_result"
        return UserValue(
            value,
            "value" if value is not None else state,
            self.conversations,
            self.with_value,
            self.conversations - self.with_value,
            self.sampled,
        )


def _micros(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("user windows require timezone-aware datetimes")
    delta = value.astimezone(UTC) - EPOCH
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


@dataclass(frozen=True, slots=True)
class Window:
    """UTC half-open, outward snapped range returned to the consumer."""

    start: datetime
    end: datetime

    def __post_init__(self):
        start, end = _micros(self.start), _micros(self.end)
        if start >= end or start % SNAP_US or end % SNAP_US:
            raise ValueError("user windows must be increasing and 15-minute aligned")
        object.__setattr__(self, "start", self.start.astimezone(UTC))
        object.__setattr__(self, "end", self.end.astimezone(UTC))

    @classmethod
    def snap(cls, start, end):
        lo, hi = _micros(start), _micros(end)
        if hi <= lo:
            raise ValueError("user windows must be increasing")
        return cls(
            EPOCH + timedelta(microseconds=(lo // SNAP_US) * SNAP_US),
            EPOCH + timedelta(microseconds=-(-hi // SNAP_US) * SNAP_US),
        )

    def previous(self):
        return Window(self.start - (self.end - self.start), self.start)


@dataclass(frozen=True, slots=True)
class PointValue:
    window: Window
    point_status: str
    value: float | None
    count_value: int | None
    counted_users: int
    eligible_users: int
    no_value_users: int
    conversations_without_user: int
    sampled_conversations: int
    non_finite_users: int


@dataclass(slots=True)
class _PointState:
    counted: int = 0
    eligible: int = 0
    missing: int = 0
    unassigned: int = 0
    sampled: int = 0
    non_finite: int = 0
    numeric: ExactSum = field(default_factory=ExactSum)

    def user(self, result):
        if result.value is None:
            self.missing = _add_count(self.missing)
            self.non_finite = _add_count(
                self.non_finite, int(result.state == "non_finite_result")
            )
        else:
            self.eligible = _add_count(self.eligible)
            # Numeric rules count every eligible user, including observed zero.
            self.counted = _add_count(self.counted, int(result.value is not False))
            self.numeric.add(float(result.value))

    def merge(self, other):
        for name in (
            "counted",
            "eligible",
            "missing",
            "unassigned",
            "sampled",
            "non_finite",
        ):
            setattr(self, name, _add_count(getattr(self, name), getattr(other, name)))
        self.numeric.merge(other.numeric)

    def finish(self, window):
        status = (
            "sampled_inputs_not_supported"
            if self.sampled
            else "value"
            if self.eligible
            else "no_value"
        )
        return PointValue(
            window,
            status,
            self.numeric.value(average=True) if status == "value" else None,
            self.counted if status == "value" else None,
            self.counted,
            self.eligible,
            self.missing,
            self.unassigned,
            self.sampled,
            self.non_finite,
        )


@dataclass(frozen=True, slots=True)
class Limits:
    rows: int = MAX_ROWS
    page_bytes: int = MAX_PAGE_BYTES
    states: int = MAX_STATES
    key_bytes: int = MAX_LIVE_KEY_BYTES
    seconds: float = 15.0

    def __post_init__(self):
        for name, maximum in (
            ("rows", MAX_ROWS),
            ("page_bytes", MAX_PAGE_BYTES),
            ("states", MAX_STATES),
            ("key_bytes", MAX_LIVE_KEY_BYTES),
        ):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError("invalid user reduction limit")
        if not math.isfinite(self.seconds) or not 0 < self.seconds <= 15:
            raise ValueError("invalid user reduction deadline")


@dataclass(frozen=True, slots=True)
class _Range:
    """Hash routing only. Equality is always the complete byte-string identity."""

    depth: int = 0
    bucket: int = 0
    lower: bytes | None = None
    upper: bytes | None = None
    single: bytes | None = None

    @property
    def mask(self):
        return (MAX_COUNT << (64 - self.depth)) & MAX_COUNT if self.depth else 0

    def contains(self, key, hash_value):
        return (
            hash_value & self.mask == self.bucket
            and (self.single is None or key == self.single)
            and (self.lower is None or key > self.lower)
            and (self.upper is None or key < self.upper)
        )

    def split(self, pivot):
        if self.single is not None:
            raise UserRulesUnavailable("resource_limit")
        if self.depth < 64:
            return (
                replace(self, depth=self.depth + 1),
                replace(
                    self,
                    depth=self.depth + 1,
                    bucket=self.bucket | (1 << (63 - self.depth)),
                ),
            )
        if pivot is None or not self.contains(pivot, self.bucket):
            raise UserRulesUnavailable("resource_limit")
        return (
            replace(self, upper=pivot),
            replace(self, single=pivot, lower=None, upper=None),
            replace(self, lower=pivot),
        )


@dataclass(frozen=True, slots=True)
class _SourceRange(_Range):
    """Immutable byte ranges prune the existing unit-ordered primary index.

    Unlike user routing, unit routing must not subdivide an unindexed hash.
    Every revision of a key remains in the same exhaustive/exclusive leaf.
    A singleton still resolves all its retries without a preceding LIMIT.
    """

    def split(self, pivot):
        if self.single is not None or pivot is None or not self.contains(pivot, 0):
            raise UserRulesUnavailable("resource_limit")
        return (
            replace(self, upper=pivot),
            replace(self, single=pivot, lower=None, upper=None),
            replace(self, lower=pivot),
        )


def _push(stack, children):
    if len(stack) + len(children) > MAX_DESCRIPTORS:
        raise UserRulesUnavailable("resource_limit")
    stack.extend(reversed(children))


# Narrow, bounded transport: SQL emits at most limit+1 bytes per identity. The
# sentinel is rejected, NEVER used as a truncated identity. The native decoder
# therefore cannot allocate an arbitrarily wide String before Python admission.
# Full identifiers still determine immutable ranges, hashes and argMax keys.
_COLUMNS = (
    "unit_id",
    "generation",
    "_version",
    "user_id",
    "unit_start",
    "status",
    "value_bool",
    "value_number",
    "sample_probability",
    "invalid_probability",
    "is_deleted",
    "settled_at",
    "workspace_id",
)
_PROJECTION = (
    f"substring(unit_id, 1, {MAX_KEY_BYTES + 1})",
    "generation",
    "_version",
    f"substring(user_id, 1, {MAX_KEY_BYTES + 1})",
    "cityHash64(user_id)",
    "toUnixTimestamp64Micro(unit_start)",
    "substring(status, 1, 65)",
    "value_bool",
    "value_number",
    "sample_probability",
    "invalid_probability",
    "is_deleted",
    "isNotNull(settled_at)",
    "workspace_id",
)


def source_query(branch: str, partition: _Range, *, rows=MAX_ROWS, ordered=False):
    """Wrap ONE shared committed branch, never an unrestricted caller relation.

    The only aggregate is a one-immutable-unit argMax across all its revisions.
    Workspace, mutable user/time/status and tombstones are checked AFTER latest.
    """
    params: dict[str, object] = {}
    where = "1"
    if partition.depth:
        params.update(ur_mask=partition.mask, ur_bucket=partition.bucket)
        where = "bitAnd(cityHash64(unit_id), %(ur_mask)s) = %(ur_bucket)s"
    if partition.single is not None:
        params["ur_unit"] = partition.single.hex()
        # Two fixed-size extrema at the winning revision detect contradictory
        # retries without GROUP BY over its arbitrarily many older revisions.
        # Float bit patterns (including NaN payloads and signed zero) are ordered
        # as integers; nullable floats must not be skipped by an aggregate.
        canonical = list(_PROJECTION)
        canonical[7] = "tuple(isNull(value_bool), ifNull(value_bool, 0))"
        for index, name in ((8, "value_number"), (9, "sample_probability")):
            canonical[index] = (
                f"tuple(isNull({name}), ifNull(reinterpretAsUInt64({name}), 0))"
            )
        identity = f"tuple({', '.join(canonical)})"
        high = f"tuple(generation, _version, {identity})"
        low = f"tuple(-toInt128(generation), -toInt128(_version), {identity})"
        return (
            f"SELECT max(toUInt8(1)), argMax(tuple({', '.join(_PROJECTION)}), {high}), "
            f"argMax({identity}, {high}) != argMin({identity}, {low}) "
            f"FROM ({branch}) WHERE unit_id = unhex(%(ur_unit)s)",
            params,
        )
    if partition.lower is not None:
        params["ur_lower"] = partition.lower.hex()
        where += " AND unit_id > unhex(%(ur_lower)s)"
    if partition.upper is not None:
        params["ur_upper"] = partition.upper.hex()
        where += " AND unit_id < unhex(%(ur_upper)s)"
    order = " ORDER BY unit_id" if ordered else ""
    return (
        f"SELECT {', '.join(_PROJECTION)} FROM ({branch}) WHERE {where}{order} LIMIT {rows + 1}",
        params,
    )


@dataclass(slots=True, repr=False)
class Conversation:
    unit: bytes
    generation: int
    version: int
    user: bytes
    user_hash: int
    start_us: int
    status: bytes
    boolean: int | None
    number: float | None
    probability: float | None
    invalid_probability: int
    deleted: int
    settled: int
    workspace: UUID

    @property
    def reservation(self):
        return ROW_RESERVATION + len(self.unit) + len(self.user) + len(self.status)

    def validate(self):
        if (
            type(self.unit) is not bytes
            or type(self.user) is not bytes
            or type(self.status) is not bytes
            or len(self.unit) > MAX_KEY_BYTES
            or len(self.user) > MAX_KEY_BYTES
            or len(self.status) > 64
        ):
            raise UserRulesUnavailable("identity_or_payload_limit")
        for count in (self.generation, self.version, self.user_hash):
            _count(count)
        if self.deleted not in (0, 1) or self.settled not in (0, 1):
            raise UserRulesUnavailable("corrupt_source")


class _BorrowedNativeOwner:
    """Test/internal leased-native adapter; application reads use their real pool.

    A raw native client is already exclusively borrowed by the source's caller.
    This adapter only lets it reuse the identical managed transport lifetime.
    It is not a replacement for application read admission or a manifest pin.
    """

    read_timeout_ceiling_ms = 15000

    def __init__(self, client):
        self.client = client
        self._read_admission = BoundedSemaphore(1)

    def _get_client(self):
        return self.client

    def _return_client(self, client):
        pass


class NativeSource:
    """Internal stream of bounded complete source leaves; not a public read API.

    ``branches`` come exclusively from ``committed_branches`` via the factory
    below. Tests may supply synthetic exact-allowlisted branches. ``guard`` must
    raise when the caller's existing pin/admission is unusable, including after
    the final source statement. No new head or retention default is obtained.
    """

    def __init__(
        self,
        client,
        branches,
        params,
        *,
        workspace_id,
        guard: Callable,
        limits=Limits(),
    ):
        if not callable(guard) or not any(
            callable(getattr(client, name, None))
            for name in ("execute_read_block_stream", "execute_iter")
        ):
            raise ValueError("user rules require a guarded streaming read")
        if not isinstance(branches, Sequence) or len(branches) > 2:
            raise ValueError("expected checkpoint and delta branches")
        self.client = client
        self.branches = tuple(branches)
        self.params = dict(params)
        self.workspace = UUID(str(workspace_id))
        self.guard = guard
        self.limits = limits
        self.statements = 0
        self.splits = 0
        self.peak_page_bytes = 0

    def _stream(self, sql, params, check):
        remaining = check()
        # The public wrapper accepts integer milliseconds. Never round up a
        # depleted sub-millisecond budget, or reset it to a full statement cap.
        timeout_ms = math.floor(remaining * 1000)
        if timeout_ms < 1:
            raise UserRulesUnavailable("deadline")
        settings = {**SOURCE_SETTINGS, "max_execution_time": timeout_ms / 1000}
        managed = getattr(self.client, "execute_read_block_stream", None)
        if callable(managed):
            if getattr(self.client, "server_enforced_readonly", False) and not getattr(
                self.client, "allow_query_settings_with_server_readonly", False
            ):
                raise UserRulesUnavailable("bounded_stream_settings_required")
            # General application mode intentionally removes result/time caps.
            # B20 is explicitly bounded: capture the existing opt-out here, not
            # in a caller or global configuration, preserving all B20 settings.
            with application_read_context(False):
                stream = managed(
                    sql,
                    params,
                    timeout_ms=timeout_ms,
                    block_size=1024,
                    settings=settings,
                )
        else:
            stream = _ManagedNativeReadStream(
                _BorrowedNativeOwner(self.client),
                sql,
                params,
                timeout_ms=timeout_ms,
                block_size=1024,
                query_settings=settings,
            )
        with stream as blocks:
            for block in blocks:
                yield from block

    def _page(self, partition, check, *, ordered=False):
        latest: dict[bytes, Conversation]
        latest, total_bytes, boundary = {}, 0, None
        for branch in self.branches:
            self.guard()
            check()
            selected = (
                replace(partition, upper=boundary)
                if boundary is not None
                else partition
            )
            sql, bound = source_query(
                branch, selected, rows=self.limits.rows, ordered=ordered
            )
            iterator = self._stream(sql, {**self.params, **bound}, check)
            self.statements += 1
            physical_rows, page_bytes, pivot = 0, 0, None
            try:
                for raw in iterator:
                    check()
                    if partition.single is not None:
                        present, raw, conflict = raw
                        if not present:
                            continue
                        if conflict:
                            raise UserRulesUnavailable("conflicting_retry")
                    row = Conversation(*raw)
                    row.validate()
                    pivot = row.unit
                    physical_rows += 1
                    reservation = row.reservation
                    page_bytes += reservation
                    if (
                        physical_rows > self.limits.rows
                        or page_bytes > self.limits.page_bytes
                    ):
                        raise _Split(pivot)
                    # Charge conservative objects+hash growth, before retaining.
                    total_bytes += reservation
                    if total_bytes > 2 * self.limits.page_bytes:
                        raise _Split(pivot)
                    old = latest.get(row.unit)
                    if old is None or (row.generation, row.version) > (
                        old.generation,
                        old.version,
                    ):
                        latest[row.unit] = row
                    elif (row.generation, row.version) == (old.generation, old.version):
                        # Receipt-backed retries must be identical. NaNs are valid
                        # source missing inputs, so compare their binary payload.
                        if not _same_row(old, row):
                            raise UserRulesUnavailable("conflicting_retry")
            except BaseException as error:
                # CH TOO_MANY_ROWS_OR_BYTES is the only native overflow that
                # permits a split. Memory/time/replica errors never become a
                # successful prefix or a retry with relaxed settings.
                from clickhouse_driver.errors import ServerException

                overflow = isinstance(error, _Split) or (
                    isinstance(error, ServerException) and error.code == 396
                )
                if not overflow:
                    raise
                if not ordered or pivot is None:
                    raise _Split(pivot) from None
                if isinstance(error, ServerException):
                    # No clean terminal: do not accept even a sorted prefix.
                    raise UserRulesUnavailable("source_result_limit") from error
                # Drain the bounded LIMIT result without retaining its tail.
                # A later server/transport/deadline failure invalidates the
                # prefix too; observing a pivot alone is not a successful EOF.
                for _ in iterator:
                    check()
                # In native unit order, seeing pivot proves *all* revisions of
                # keys strictly below it were read. The boundary key itself is
                # never accepted from a prefix: resolve it separately without
                # LIMIT. The next branch uses the same exclusive upper bound.
                boundary = pivot
                for unit in tuple(latest):
                    if unit >= boundary:
                        del latest[unit]
            finally:
                self.peak_page_bytes = max(
                    self.peak_page_bytes, min(total_bytes, 2 * self.limits.page_bytes)
                )
                close = getattr(iterator, "close", None)
                if close:
                    close()
        self.guard()
        check()  # An empty or successful LIMIT result still needs clean EOF.
        return (latest, boundary) if ordered else latest

    def rows(self, check):
        # Indexed continuation, like committed_key_ranges: never restart at the
        # root or reread a completed prefix merely to discover its upper bound.
        cursor = None
        while True:
            check()
            latest, boundary = self._page(
                _SourceRange(lower=cursor), check, ordered=True
            )
            yield from latest.values()
            del latest
            if boundary is None:
                return
            if cursor is not None and boundary <= cursor:
                raise UserRulesUnavailable("source_cursor_did_not_advance")
            self.splits += 1
            latest = self._page(_SourceRange(single=boundary), check)
            yield from latest.values()
            del latest
            cursor = boundary


def _same_row(left, right):
    for name in Conversation.__slots__:
        a, b = getattr(left, name), getattr(right, name)
        if isinstance(a, float) and isinstance(b, float):
            if struct.pack(">d", a) != struct.pack(">d", b):
                return False
        elif a != b:
            return False
    return True


def source_for_manifest(
    store, manifest, *, scope, field_id, field_version, guard, limits=Limits()
):
    """Application adapter; invoke only inside B07 pinned_read + read admission.

    Scope is the permission-checked Scope, version is the pin's active definition,
    and guard rechecks that same pin. No raw attempt list/overlay is accepted.
    This does not authorize an API read or replace workspace permissions.
    """
    from tracer.services.derived_metrics.bounded_reads import committed_branches
    from tracer.services.derived_metrics.feature_flag import metrics_enabled

    if not metrics_enabled():
        raise UserRulesUnavailable("metrics_disabled")
    if (
        manifest is None
        or type(field_version) is not int
        or not 1 <= field_version < 1 << 32
    ):
        raise ValueError("a pinned definition and manifest are required")
    guard()
    branches, params = committed_branches(
        FIELD_VALUES,
        manifest,
        org_id=scope.organization,
        project_id=scope.project,
        key_in={
            "field_id": (UUID(str(field_id)),),
            "field_version": (field_version,),
            "level": ("conversation",),
        },
        columns=_COLUMNS,
        param_prefix="user_rule",
    )
    return NativeSource(
        store.client,
        branches,
        params,
        workspace_id=scope.workspace,
        guard=guard,
        limits=limits,
    )


def reduce_users(source, rule, windows, *, retained_from, consumer=None):
    """Reduce one scalar window OR 1..90 disjoint ordered trend intervals.

    No O(users) output is returned. An optional transactional bounded consumer
    receives (point_index, full_user_bytes, UserValue) only after a partition is
    complete; it must buffer only its top-k/page and discard ALL output if this
    function raises. Sampling is window-global even for that consumer.

    KPI and previous use independent calls under the SAME caller-held pin. Trend
    points share a scan, not one read per point; user/day values are never summed
    into the KPI. B21 must include all calls and assembly in its request deadline.
    """
    _rule(rule)
    if not isinstance(windows, Sequence) or not 1 <= len(windows) <= 90:
        raise ValueError("expected 1..90 user windows")
    windows = tuple(windows)
    ends: list[int]
    starts, ends = [], []
    for window in windows:
        if not isinstance(window, Window):
            raise ValueError("expected snapped user windows")
        lo, hi = _micros(window.start), _micros(window.end)
        if ends and lo < ends[-1]:
            raise ValueError("trend windows must be ordered and disjoint")
        starts.append(lo)
        ends.append(hi)
    # There is no unlimited/zero fallback. B07 supplies the real boundary.
    if _micros(retained_from) > starts[0]:
        raise UserRulesUnavailable("retention_expired")
    deadline = time.monotonic() + source.limits.seconds

    source.guard()

    def check():
        # Durable pin checks belong at bounded page/partition boundaries, not
        # once per row (the real guard may perform an ORM query).
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise UserRulesUnavailable("deadline")
        return remaining

    totals = [_PointState() for _ in windows]
    partitions = [_Range()]
    while partitions:
        source.guard()
        check()
        partition = partitions.pop()
        states: dict[tuple[int, bytes], tuple[int, RuleState]]
        states, key_bytes = {}, 0
        tentative = [_PointState() for _ in windows]
        unassigned_hash = None
        # A full source pass also proves which hash ranges are empty. Track two
        # scalars across ALL eligible rows, including siblings and unassigned
        # users; a prefix cannot prove absence. Prune only after clean EOF.
        minimum_hash, maximum_hash = MAX_COUNT, -1
        rows = source.rows(check)
        try:
            for row in rows:
                if (
                    row.workspace != source.workspace
                    or row.deleted
                    or row.status == b"open"
                    or not row.settled
                ):
                    continue
                point = bisect_right(starts, row.start_us) - 1
                if point < 0 or row.start_us >= ends[point]:
                    continue
                minimum_hash = min(minimum_hash, row.user_hash)
                maximum_hash = max(maximum_hash, row.user_hash)
                if not partition.contains(row.user, row.user_hash):
                    continue
                summary = tentative[point]
                bad = (
                    row.invalid_probability
                    or row.probability is None
                    or not math.isfinite(row.probability)
                    or row.probability != 1
                )
                if not row.user:
                    unassigned_hash = row.user_hash
                    summary.sampled = _add_count(summary.sampled, int(bool(bad)))
                    summary.unassigned = _add_count(summary.unassigned)
                    continue
                key = (point, row.user)
                while key not in states and (
                    len(states) >= source.limits.states
                    or key_bytes + len(row.user) > source.limits.key_bytes
                ):
                    children = partition.split(row.user)
                    # Continue one child on the CURRENT source stream. Its
                    # retained states contain all of that child's prefix; only
                    # disjoint siblings need a fresh scan. No tentative output
                    # has escaped, and live state never exceeds the parent cap.
                    partition, *siblings = children
                    _push(partitions, siblings)
                    for old_key in tuple(states):
                        user_hash, _ = states[old_key]
                        if not partition.contains(old_key[1], user_hash):
                            key_bytes -= len(old_key[1])
                            del states[old_key]
                    if unassigned_hash is not None and not partition.contains(
                        b"", unassigned_hash
                    ):
                        tentative = [_PointState() for _ in windows]
                        unassigned_hash = None
                    if not partition.contains(row.user, row.user_hash):
                        break
                if not partition.contains(row.user, row.user_hash):
                    continue
                if key not in states:
                    key_bytes += len(row.user)
                    states[key] = (row.user_hash, RuleState(rule))
                value = row.boolean if rule in {"any", "every"} else row.number
                states[key][1].add(
                    value if row.status == b"ok" else None,
                    probability=row.probability,
                    invalid_probability=row.invalid_probability,
                )
        finally:
            rows.close()
        source.guard()
        check()
        partitions[:] = [
            pending
            for pending in partitions
            if pending.bucket <= maximum_hash
            and (pending.bucket | (MAX_COUNT ^ pending.mask)) >= minimum_hash
        ]
        for (point, user), (_, state) in states.items():
            result = state.finish()
            tentative[point].sampled = _add_count(
                tentative[point].sampled, result.sampled
            )
            tentative[point].user(result)
            if consumer is not None:
                consumer(point, user, result)
        for total, partial in zip(totals, tentative, strict=True):
            total.merge(partial)
        states.clear()
    source.guard()
    check()
    return tuple(
        total.finish(window) for total, window in zip(totals, windows, strict=True)
    )
