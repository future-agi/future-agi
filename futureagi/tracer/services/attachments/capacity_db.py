"""Transactional in-flight capacity; Budget is the only counter authority.

Callers acquire this handle BEFORE scope/authority/owner locks. Owners must be
created with a complete zero reservation group and immutable pool, then reserved
in the same transaction. No inventory scans, counter repair, or saturation.
"""

from datetime import timedelta
from uuid import uuid4

from django.db import transaction
from django.utils import timezone

from tracer.models.attachment import AttachmentBudget, AttachmentScope, AttachmentUpload
from tracer.models.attachment_recording import (
    AttachmentImportJob,
    AttachmentRecordingDerivative,
)
from tracer.services.attachments import capacity
from tracer.services.attachments.contract import AttachmentError
from tracer.services.attachments.interfaces import (
    CapacityLocks,
    ReservationOwner,
    ResourceVector,
)

BUDGET_FIELDS = (
    "reserved_bytes",
    "active_transfers",
    "validation_jobs",
    "active_recordings",
    "object_count",
    "link_count",
)
OWNER_FIELDS = (
    "reservation_charge_bytes",
    "reservation_transfers",
    "reservation_validation_jobs",
    "reservation_recordings",
    "reservation_objects",
    "reservation_links",
)
_MODELS: dict[
    str,
    type[AttachmentUpload]
    | type[AttachmentImportJob]
    | type[AttachmentRecordingDerivative],
] = {
    "upload": AttachmentUpload,
    "import": AttachmentImportJob,
    "derivative": AttachmentRecordingDerivative,
}
_ORDER = {"import": 0, "upload": 1, "derivative": 2}
_KEY = object()


class _Locks:
    """Private, connection/atomic-block-bound implementation of CapacityLocks."""

    def __init__(self, key, scope, rows):
        if key is not _KEY:
            raise TypeError("use lock_capacity")
        self.scope_id = scope.id
        self.organization_id = scope.organization_id
        self.project_id = scope.project_id
        self.rows = rows
        self.connection = transaction.get_connection()
        self.block = self.connection.atomic_blocks[-1]


def _atomic():
    connection = transaction.get_connection()
    if not connection.in_atomic_block or connection.needs_rollback:
        raise RuntimeError("capacity requires a live atomic transaction")
    return connection


def _check(locks, scope_id):
    connection = _atomic()
    if (
        not isinstance(locks, _Locks)
        or locks.connection is not connection
        or not any(block is locks.block for block in connection.atomic_blocks)
        or locks.scope_id != scope_id
    ):
        raise RuntimeError("invalid or expired capacity locks")
    # Scope identity is immutable, but reject corrupt/mismatched handles explicitly.
    scope = AttachmentScope.objects.only("organization_id", "project_id").get(
        pk=scope_id
    )
    if (scope.organization_id, scope.project_id) != (
        locks.organization_id,
        locks.project_id,
    ):
        raise RuntimeError("capacity scope changed")


def lock_capacity(*, scope_id, pool_keys) -> CapacityLocks:
    _atomic()
    if (
        type(pool_keys) is not tuple
        or not pool_keys
        or len(set(pool_keys)) != len(pool_keys)
    ):
        raise ValueError("explicit distinct capacity pools required")
    for key in pool_keys:
        capacity.pool_cap(key)
    scope = AttachmentScope.objects.only("organization_id", "project_id").get(
        pk=scope_id
    )
    keys = [key for key in capacity.GLOBAL_POOL_KEYS if key in pool_keys]
    keys += [f"org:{scope.organization_id}", f"project:{scope.project_id}"]
    rows = {}
    for key in keys:
        # Creation also takes locks: do NOT bulk create pools in a different order.
        AttachmentBudget.objects.get_or_create(
            key=key, defaults={"window_started_at": timezone.now()}
        )
        rows[key] = AttachmentBudget.objects.select_for_update().get(pk=key)
    return _Locks(_KEY, scope, rows)


def limits_for(*, scope_id, pool_key):
    return capacity.limits_for(scope_id=scope_id, pool_key=pool_key)


def reservation_fields(
    *,
    pool_key,
    expires_at,
    reserved_bytes=0,
    decoded_bytes=0,
    object_count=0,
    part_count=0,
    now=None,
):
    """Complete zero-valued group for INSERT, not a capacity grant."""
    capacity.pool_cap(pool_key)
    now = now or timezone.now()
    if expires_at <= now:
        raise AttachmentError("expired", 409)
    # Strict vector validation rejects bools, negatives and scalar overflow.
    ResourceVector(reserved_bytes, 0, 0, 0, object_count, part_count)
    ResourceVector(decoded_bytes, 0, 0, 0, 0, 0)
    return {
        "reservation_id": uuid4(),
        "reservation_created_at": now,
        "reservation_expires_at": expires_at,
        "reservation_pool_key": pool_key,
        "reservation_reserved_bytes": reserved_bytes,
        "reservation_decoded_bytes": decoded_bytes,
        "reservation_object_count": object_count,
        "reservation_part_count": part_count,
        **dict.fromkeys(OWNER_FIELDS, 0),
    }


def owner_vector(row):
    return ResourceVector(*(getattr(row, name) for name in OWNER_FIELDS))


def _budget_vector(row):
    return ResourceVector(*(getattr(row, name) for name in BUDGET_FIELDS))


def _values(vector):
    if not isinstance(vector, ResourceVector):
        raise ValueError("invalid capacity vector")
    return (
        vector.bytes,
        vector.transfers,
        vector.validation_jobs,
        vector.recordings,
        vector.objects,
        vector.links,
    )


def _owner(locks, owner):
    if not isinstance(owner, ReservationOwner):
        raise ValueError("invalid reservation owner")
    _check(locks, owner.scope_id)
    row = (
        _MODELS[owner.kind]
        .objects.select_for_update()
        .get(pk=owner.id, scope_id=owner.scope_id)
    )
    if not row.reservation_id or row.reservation_pool_key not in locks.rows:
        raise RuntimeError("owner pool was not locked or reservation unresolved")
    expected_pool = (
        capacity.upload_pool_key(row.id)
        if isinstance(row, AttachmentUpload)
        and row.producer_kind == "authenticated_stream"
        else capacity.DERIVATIVE_POOL_KEY
        if owner.kind == "derivative"
        else capacity.IMPORT_POOL_KEY
    )
    if row.reservation_pool_key != expected_pool:
        raise RuntimeError("owner uses an incompatible capacity pool")
    return row


def _rows(locks, pool):
    # Re-read locked values: another operation in this same transaction may have
    # used a different handle. A cached Python model is not counter authority.
    return [
        AttachmentBudget.objects.get(pk=key)
        for key in (pool, f"org:{locks.organization_id}", f"project:{locks.project_id}")
    ]


def _save_vector(row, fields, vector):
    for name, value in zip(fields, _values(vector), strict=True):
        setattr(row, name, value)
    row.save(update_fields=list(fields))


def _change(locks, row, desired):
    old = owner_vector(row)
    caps = limits_for(scope_id=locks.scope_id, pool_key=row.reservation_pool_key)
    increasing = any(
        new > prior for new, prior in zip(_values(desired), _values(old), strict=True)
    )
    if increasing and any(not capacity.fits_within(desired, cap) for cap in caps):
        raise AttachmentError("too_large", 413)
    rows = _rows(locks, row.reservation_pool_key)
    # Subtract first; underflow is corruption, never normalize/clamp. A decrease
    # may drain an over-cap history, but cannot add ANY component while over cap.
    proposed = [_budget_vector(budget) - old + desired for budget in rows]
    if increasing and any(
        not capacity.fits_within(value, cap)
        for value, cap in zip(proposed, caps, strict=True)
    ):
        raise AttachmentError("throttled", 429)
    for budget, value in zip(rows, proposed, strict=True):
        _save_vector(budget, BUDGET_FIELDS, value)
    _save_vector(row, OWNER_FIELDS, desired)


def reserve_locked(locks, *, owner, amount, expires_at):
    _values(amount)
    with transaction.atomic():
        row = _owner(locks, owner)
        if (
            row.reservation_settled_at
            or row.reservation_expires_at != expires_at
            or expires_at <= timezone.now()
        ):
            raise AttachmentError("expired", 409)
        existing = owner_vector(row)
        if existing != capacity.ZERO:
            if existing == amount:
                return  # Exact replay, never a second counter increment.
            raise AttachmentError("idempotency_conflict", 409)
        _change(locks, row, amount)


def resize_locked(locks, *, owner, amount):
    _values(amount)
    with transaction.atomic():
        row = _owner(locks, owner)
        if row.reservation_settled_at:
            raise AttachmentError("expired", 409)
        if row.reservation_expires_at <= timezone.now() and any(
            a > b
            for a, b in zip(_values(amount), _values(owner_vector(row)), strict=True)
        ):
            raise AttachmentError("expired", 409)
        _change(locks, row, amount)


def transfer_locked(locks, *, from_owner, to_owner, amount):
    """Neutral move; creation/fingerprint replay guards belong to admission.

    The caller must atomically create the target and move U exactly once. There
    is deliberately no second transfer receipt/table or inferred budget scan.
    """
    _values(amount)
    if from_owner == to_owner or from_owner.scope_id != to_owner.scope_id:
        raise ValueError("distinct same-scope owners required")
    with transaction.atomic():
        ordered = sorted(
            (from_owner, to_owner), key=lambda value: (_ORDER[value.kind], value.id.int)
        )
        rows = {owner: _owner(locks, owner) for owner in ordered}
        source, target = rows[from_owner], rows[to_owner]
        if source.reservation_pool_key != target.reservation_pool_key:
            raise ValueError("neutral transfer requires the same pool")
        if source.reservation_settled_at or target.reservation_settled_at:
            raise AttachmentError("expired", 409)
        if (
            min(source.reservation_expires_at, target.reservation_expires_at)
            <= timezone.now()
        ):
            raise AttachmentError("expired", 409)
        left, right = capacity.move_vector(
            source=owner_vector(source), destination=owner_vector(target), amount=amount
        )
        _save_vector(source, OWNER_FIELDS, left)
        _save_vector(target, OWNER_FIELDS, right)


def release_locked(locks, *, owner, cleanup_complete, now):
    if type(cleanup_complete) is not bool:
        raise ValueError("explicit cleanup proof required")
    with transaction.atomic():
        row = _owner(locks, owner)
        if row.reservation_settled_at or not cleanup_complete:
            return False
        if (
            row.cleanup_not_before is None
            or row.cleanup_checked_at is None
            or row.cleanup_checked_at < row.cleanup_not_before
            or now < row.cleanup_checked_at
            or getattr(row, "unowned_cleanup_due_at", None) is not None
            or getattr(row, "transfer_started_at", None) is not None
            or getattr(row, "lease_token", None) is not None
            or getattr(row, "lease_until", None) is not None
        ):
            return False
        if owner.kind == "upload" and row.usage_id is None:
            raise RuntimeError("usage settlement must precede capacity release")
        rows = _rows(locks, row.reservation_pool_key)
        remaining = [_budget_vector(budget) - owner_vector(row) for budget in rows]
        for budget, value in zip(rows, remaining, strict=True):
            _save_vector(budget, BUDGET_FIELDS, value)
        # Retain the final charged vector as immutable evidence, subtract once.
        row.reservation_settled_at = now
        row.save(update_fields=["reservation_settled_at"])
        return True


def count_attempt(*, scope_id, pool_key, now):
    """Commit rejected attempts too; call before the admission transaction."""
    with transaction.atomic():
        locks = lock_capacity(scope_id=scope_id, pool_keys=(pool_key,))
        caps = (
            capacity.attempts_per_minute(pool_key),
            capacity.ORGANIZATION_ATTEMPTS_PER_MINUTE,
            capacity.PROJECT_ATTEMPTS_PER_MINUTE,
        )
        allowed = True
        for row, cap in zip(_rows(locks, pool_key), caps, strict=True):
            if now - row.window_started_at >= timedelta(minutes=1):
                row.window_started_at, row.initialization_attempts = now, 0
            # Bounded count preserves rejection without integer overflow under a
            # sustained flood. These are rate attempts, never resource counters.
            if row.initialization_attempts <= cap:
                row.initialization_attempts += 1
            allowed &= row.initialization_attempts <= cap
            row.save(update_fields=["window_started_at", "initialization_attempts"])
        return bool(allowed)
