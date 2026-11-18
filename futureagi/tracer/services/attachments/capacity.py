"""Pure attachment capacity arithmetic for frozen interface v1.

These are IN-FLIGHT resource bounds, not retained-storage billing or new quota
settings. No database locks, admission, settlement, retries or lease operations
are implemented here. In particular a neutral vector move is not an atomic
reservation transfer: the accounting owner must implement CapacityOperations.

Pool constants cannot be overridden per process. Changing the profile requires
quiescence, persisted-counter reconciliation and all writers switching together.
Organization/project counters aggregate ALL pools; never give each producer its
own organization/project allowance. The historical extra import percentage is
not a source of capacity.
"""

from uuid import UUID

from tracer.services.attachments.interfaces import INT64_MAX, ResourceVector

ZERO = ResourceVector(0, 0, 0, 0, 0, 0)
DEPLOYMENT_CAP = ResourceVector(8_589_934_592, 128, 8192, 64, 65_536, 65_536)
ORGANIZATION_CAP = ResourceVector(2_147_483_648, 32, 2048, 8, 16_384, 16_384)
PROJECT_CAP = ResourceVector(1_073_741_824, 16, 1024, 4, 8192, 8192)
UPLOAD_POOL_CAP = ResourceVector(4_294_967_296, 64, 4096, 0, 32_768, 32_768)
UPLOAD_SHARD_CAP = ResourceVector(268_435_456, 4, 256, 0, 2048, 2048)
IMPORT_POOL_CAP = ResourceVector(3_221_225_472, 48, 3072, 64, 24_576, 32_768)
DERIVATIVE_POOL_CAP = ResourceVector(1_073_741_824, 16, 1024, 0, 8192, 0)
GLOBAL_SHARDS = 16
UPLOAD_POOL_KEYS = tuple(f"global:{index}" for index in range(GLOBAL_SHARDS))
IMPORT_POOL_KEY = "global:imports"
DERIVATIVE_POOL_KEY = "global:derivatives"
# Frozen Budget global lock order, not lexicographic sorting (10 precedes 2).
GLOBAL_POOL_KEYS = UPLOAD_POOL_KEYS + (IMPORT_POOL_KEY, DERIVATIVE_POOL_KEY)

# Attempts per minute; retries/rejections also count. Counting is NOT done here.
DEPLOYMENT_ATTEMPTS_PER_MINUTE = 960
ORGANIZATION_ATTEMPTS_PER_MINUTE = 240
PROJECT_ATTEMPTS_PER_MINUTE = 120
UPLOAD_POOL_ATTEMPTS_PER_MINUTE = 480
UPLOAD_SHARD_ATTEMPTS_PER_MINUTE = 30
IMPORT_ATTEMPTS_PER_MINUTE = 360
DERIVATIVE_ATTEMPTS_PER_MINUTE = 120


def _vector(value: ResourceVector) -> None:
    if not isinstance(value, ResourceVector):
        raise ValueError("invalid capacity vector")


def _size(value: int, *, positive: bool = False) -> None:
    minimum = 1 if positive else 0
    if type(value) is not int or not minimum <= value <= INT64_MAX:
        raise ValueError("invalid capacity quantity")


def _components(value: ResourceVector) -> tuple[int, int, int, int, int, int]:
    _vector(value)
    return (
        value.bytes,
        value.transfers,
        value.validation_jobs,
        value.recordings,
        value.objects,
        value.links,
    )


def sum_vectors(*vectors: ResourceVector) -> ResourceVector:
    """Exact componentwise sum with scalar overflow checks, no quota clamping."""
    total = ZERO
    for vector in vectors:
        _vector(vector)
        total = total + vector
    return total


def scale_vector(vector: ResourceVector, multiplier: int) -> ResourceVector:
    """Checked integer multiplication, e.g. sixteen disjoint upload shards."""
    values = _components(vector)
    _size(multiplier)
    return ResourceVector(*(component * multiplier for component in values))


def fits_within(amount: ResourceVector, cap: ResourceVector) -> bool:
    """Componentwise <=; never lexicographic or based only on bytes."""
    return all(
        a <= c for a, c in zip(_components(amount), _components(cap), strict=True)
    )


def require_within(amount: ResourceVector, cap: ResourceVector) -> None:
    if not fits_within(amount, cap):
        raise ValueError("capacity exceeds limit")


def available_capacity(*, used: ResourceVector, cap: ResourceVector) -> ResourceVector:
    """Refuse an over-cap history; do not silently normalize it to zero."""
    require_within(used, cap)
    return cap - used


def validate_pool_allocations(
    *,
    deployment: ResourceVector,
    upload_shards: tuple[ResourceVector, ...],
    imports: ResourceVector,
    derivatives: ResourceVector,
) -> ResourceVector:
    """Check a 16-shard decomposition without granting any capacity.

    This is a validation helper, not a profile/configuration interface. The
    fixed profile below sums exactly to its deployment ceiling. Candidate or
    historical allocations are rejected if ANY component exceeds the ceiling.
    """
    _vector(deployment)
    if type(upload_shards) is not tuple or len(upload_shards) != GLOBAL_SHARDS:
        raise ValueError("invalid upload shard allocation")
    total = sum_vectors(*upload_shards, imports, derivatives)
    require_within(total, deployment)
    return total


def global_shard(upload_id: UUID) -> int:
    """For ordinary uploads only; single imports always use global:imports."""
    if not isinstance(upload_id, UUID):
        raise ValueError("invalid capacity owner")
    return upload_id.int % GLOBAL_SHARDS


def upload_pool_key(upload_id: UUID) -> str:
    """Admission helper only; existing owners retain their persisted pool key."""
    return UPLOAD_POOL_KEYS[global_shard(upload_id)]


def pool_cap(pool_key: str) -> ResourceVector:
    if type(pool_key) is not str:
        raise ValueError("invalid capacity pool")
    if pool_key in UPLOAD_POOL_KEYS:
        return UPLOAD_SHARD_CAP
    if pool_key == IMPORT_POOL_KEY:
        return IMPORT_POOL_CAP
    if pool_key == DERIVATIVE_POOL_KEY:
        return DERIVATIVE_POOL_CAP
    raise ValueError("invalid capacity pool")


def limits_for(
    *, scope_id: UUID, pool_key: str
) -> tuple[ResourceVector, ResourceVector, ResourceVector]:
    """Frozen (global pool, organization, project) caps, with no DB lookup.

    scope_id preserves the frozen signature; it does NOT prove scope membership
    or select Budget rows. The locking implementation must bind all three rows
    to this scope before checking current usage.
    """
    if not isinstance(scope_id, UUID):
        raise ValueError("invalid capacity scope")
    return pool_cap(pool_key), ORGANIZATION_CAP, PROJECT_CAP


def attempts_per_minute(pool_key: str) -> int:
    pool_cap(pool_key)  # Reject unknown/noncanonical keys; never fall back.
    if pool_key == IMPORT_POOL_KEY:
        return IMPORT_ATTEMPTS_PER_MINUTE
    if pool_key == DERIVATIVE_POOL_KEY:
        return DERIVATIVE_ATTEMPTS_PER_MINUTE
    return UPLOAD_SHARD_ATTEMPTS_PER_MINUTE


def ordinary_upload_peak(
    *, expected_bytes: int, decoder_scratch_bytes: int
) -> ResourceVector:
    """U=(3N+D,1,1,0,4,1); replay does not reserve again.

    Fresh ambiguous replacement versions need additional charge before PUT.
    No attempt, ownership or replay decisions are made by this calculation.
    """
    _size(expected_bytes, positive=True)
    _size(decoder_scratch_bytes)
    return ResourceVector(3 * expected_bytes + decoder_scratch_bytes, 1, 1, 0, 4, 1)


def whole_import_floor(
    *, source_bytes: int, output_scratch_bytes: int
) -> ResourceVector:
    """Job floor S+W; every planned segment adds whole_import_segment_peak."""
    _size(source_bytes, positive=True)
    _size(output_scratch_bytes)
    return ResourceVector(source_bytes + output_scratch_bytes, 1, 1, 1, 2, 1)


def whole_import_segment_peak(*, segment_bytes: int) -> ResourceVector:
    """Three potential physical versions and one new logical occurrence link."""
    _size(segment_bytes, positive=True)
    return ResourceVector(3 * segment_bytes, 0, 0, 0, 3, 1)


def single_import_peaks(
    *, source_bytes: int, decoder_scratch_bytes: int, extra_reader_scratch_bytes: int
) -> tuple[ResourceVector, ResourceVector]:
    """Return (Upload U, Job J), BOTH charged to global:imports.

    U=(3N+D,1,1,0,4,1), J=(E+4N,0,0,0,4+int(E>0),0).
    U already includes the source snapshot: do not charge N again to Job.
    Job covers two additional quarantine and two additional serving versions,
    plus independent reader scratch E when needed. E is a trusted preflight
    bound, not inferred here; direct bounded ingest declares E=0 explicitly.

    Admission must reserve U+J once on Job and move U to Upload atomically with
    target creation. Job remains charged for J after Upload READY until its own
    cleanup is proven. These arithmetic values do NOT implement that transfer.
    """
    upload = ordinary_upload_peak(
        expected_bytes=source_bytes, decoder_scratch_bytes=decoder_scratch_bytes
    )
    _size(extra_reader_scratch_bytes)
    job = ResourceVector(
        extra_reader_scratch_bytes + 4 * source_bytes,
        0,
        0,
        0,
        4 + (1 if extra_reader_scratch_bytes > 0 else 0),
        0,
    )
    # The combined reservation must also fit scalar storage, not just each row.
    sum_vectors(upload, job)
    return upload, job


def derivative_peak(
    *, source_bytes: int, output_bytes: int, decoder_scratch_bytes: int
) -> ResourceVector:
    """N+D+M+3M; source/decoder/output scratch and up to three output versions.

    Caller separately checks the <=8 MiB / 30s input segment and output format
    limits; those trusted media facts are not implied by a resource vector.
    """
    _size(source_bytes, positive=True)
    _size(output_bytes, positive=True)
    _size(decoder_scratch_bytes)
    return ResourceVector(
        source_bytes + decoder_scratch_bytes + 4 * output_bytes, 1, 1, 0, 6, 0
    )


def move_vector(
    *, source: ResourceVector, destination: ResourceVector, amount: ResourceVector
) -> tuple[ResourceVector, ResourceVector]:
    """Pure neutral arithmetic: return (source-amount, destination+amount).

    Not transfer_locked and not idempotent: no owners, pools, locks, settlement
    stamps or counters are inspected/changed. A second subtraction that would
    go negative fails, but nonnegative arithmetic cannot detect a repeated
    logical release/transfer while other charge remains. Persistent exactly-once
    protection belongs to the accounting owner, NOT this helper.
    """
    _vector(source)
    _vector(destination)
    _vector(amount)
    return source - amount, destination + amount
