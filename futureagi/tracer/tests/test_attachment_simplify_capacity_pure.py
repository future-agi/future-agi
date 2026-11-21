"""Pure frozen-vector/value tests, independent of application services.

No Django/database fixtures, settings, source readers or storage adapters.
Expected numbers are literal independent checks, not copied from helper output.
These tests do not qualify transaction locking, exactly-once settlement or the
single-import atomic charge transfer, which remain integration responsibilities.
"""

from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime, timedelta, timezone
from inspect import Parameter, signature
from uuid import UUID

import pytest

from tracer.services.attachments import capacity as c
from tracer.services.attachments import interfaces as i

pytestmark = [pytest.mark.unit]

SCOPE = UUID("11111111-1111-4111-8111-111111111111")
OWNER = UUID("22222222-2222-4222-8222-222222222222")
TOKEN = UUID("33333333-3333-4333-8333-333333333333")
NOW = datetime(2026, 11, 16, 12, tzinfo=UTC)
COMPONENTS = ("bytes", "transfers", "validation_jobs", "recordings", "objects", "links")


def numbers(vector):
    return tuple(getattr(vector, name) for name in COMPONENTS)


def vector_at(index, value):
    values = [0, 0, 0, 0, 0, 0]
    values[index] = value
    return i.ResourceVector(*values)


def locator(purpose="serving"):
    return i.ObjectLocator(SCOPE, purpose, "original/internal-object")


def identity(purpose="serving"):
    return i.ObjectIdentity(SCOPE, purpose, "original/internal-object", "v1")


def fence(kind="import"):
    return i.LeaseFence(kind, OWNER, TOKEN, 1, NOW + timedelta(minutes=15))


def source_facts(**changes):
    values = {
        "source_kind": "RemoteUrlEnvelope",
        "source_id": OWNER,
        "creator_kind": "user",
        "creator_id": TOKEN,
        "generation_kind": "http_strong_etag",
        "generation": '"revision-1"',
        "byte_length": 100,
        "mime_type": "audio/wav",
        "duration_ms": 1000,
        "expires_at": NOW + timedelta(hours=1),
        "authority_generation": 0,
        "parent_trace_id": SCOPE,
        "parent_span_id": "0123456789abcdef",
        "direction": "mixed",
        "connection_id": None,
    }
    values.update(changes)
    return i.SourceFacts(**values)


@pytest.mark.parametrize(
    "name, expected",
    [
        ("DEPLOYMENT_CAP", (8589934592, 128, 8192, 64, 65536, 65536)),
        ("ORGANIZATION_CAP", (2147483648, 32, 2048, 8, 16384, 16384)),
        ("PROJECT_CAP", (1073741824, 16, 1024, 4, 8192, 8192)),
        ("UPLOAD_POOL_CAP", (4294967296, 64, 4096, 0, 32768, 32768)),
        ("UPLOAD_SHARD_CAP", (268435456, 4, 256, 0, 2048, 2048)),
        ("IMPORT_POOL_CAP", (3221225472, 48, 3072, 64, 24576, 32768)),
        ("DERIVATIVE_POOL_CAP", (1073741824, 16, 1024, 0, 8192, 0)),
    ],
)
def test_exact_frozen_caps(name, expected):
    assert numbers(getattr(c, name)) == expected


def test_disjoint_pools_equal_deployment_in_every_component():
    shards = (c.UPLOAD_SHARD_CAP,) * 16
    assert numbers(c.sum_vectors(*shards)) == (4294967296, 64, 4096, 0, 32768, 32768)
    assert numbers(c.scale_vector(c.UPLOAD_SHARD_CAP, 16)) == (
        4294967296,
        64,
        4096,
        0,
        32768,
        32768,
    )
    total = c.validate_pool_allocations(
        deployment=c.DEPLOYMENT_CAP,
        upload_shards=shards,
        imports=c.IMPORT_POOL_CAP,
        derivatives=c.DERIVATIVE_POOL_CAP,
    )
    assert numbers(total) == (8589934592, 128, 8192, 64, 65536, 65536)


@pytest.mark.parametrize("index", range(6))
def test_one_excess_in_any_pool_component_is_rejected(index):
    excessive = c.IMPORT_POOL_CAP + vector_at(index, 1)
    with pytest.raises(ValueError, match="^capacity exceeds limit$"):
        c.validate_pool_allocations(
            deployment=c.DEPLOYMENT_CAP,
            upload_shards=(c.UPLOAD_SHARD_CAP,) * 16,
            imports=excessive,
            derivatives=c.DERIVATIVE_POOL_CAP,
        )


@pytest.mark.parametrize("shards", [(), (c.ZERO,) * 15, (c.ZERO,) * 17, [c.ZERO] * 16])
def test_pool_validation_requires_exact_shard_count_and_immutable_tuple(shards):
    with pytest.raises(ValueError, match="^invalid upload shard allocation$"):
        c.validate_pool_allocations(
            deployment=c.DEPLOYMENT_CAP,
            upload_shards=shards,
            imports=c.ZERO,
            derivatives=c.ZERO,
        )


def test_sub_ceiling_allocation_is_preserved_not_filled_or_clamped():
    total = c.validate_pool_allocations(
        deployment=c.DEPLOYMENT_CAP,
        upload_shards=(c.UPLOAD_SHARD_CAP,) * 16,
        imports=c.ZERO,
        derivatives=c.ZERO,
    )
    assert numbers(total) == (4294967296, 64, 4096, 0, 32768, 32768)


@pytest.mark.parametrize(
    "raw, expected",
    [(0, 0), (1, 1), (15, 15), (16, 0), (17, 1), (31, 15), (255, 15), (256, 0)],
)
def test_uuid_shards_use_exact_modulo(raw, expected):
    assert c.global_shard(UUID(int=raw)) == expected
    assert c.upload_pool_key(UUID(int=raw)) == f"global:{expected}"


def test_pool_lock_order_is_numeric_then_imports_then_derivatives():
    assert c.GLOBAL_POOL_KEYS == (
        "global:0",
        "global:1",
        "global:2",
        "global:3",
        "global:4",
        "global:5",
        "global:6",
        "global:7",
        "global:8",
        "global:9",
        "global:10",
        "global:11",
        "global:12",
        "global:13",
        "global:14",
        "global:15",
        "global:imports",
        "global:derivatives",
    )


@pytest.mark.parametrize(
    "pool, expected, attempts",
    [(f"global:{shard}", (268435456, 4, 256, 0, 2048, 2048), 30) for shard in range(16)]
    + [
        ("global:imports", (3221225472, 48, 3072, 64, 24576, 32768), 360),
        ("global:derivatives", (1073741824, 16, 1024, 0, 8192, 0), 120),
    ],
)
def test_all_pools_share_org_and_project_caps(pool, expected, attempts):
    global_cap, organization, project = c.limits_for(scope_id=SCOPE, pool_key=pool)
    assert numbers(global_cap) == expected
    assert numbers(organization) == (2147483648, 32, 2048, 8, 16384, 16384)
    assert numbers(project) == (1073741824, 16, 1024, 4, 8192, 8192)
    assert c.attempts_per_minute(pool) == attempts


def test_attempt_split_has_no_extra_import_allowance():
    assert c.DEPLOYMENT_ATTEMPTS_PER_MINUTE == 960
    assert c.ORGANIZATION_ATTEMPTS_PER_MINUTE == 240
    assert c.PROJECT_ATTEMPTS_PER_MINUTE == 120
    assert c.UPLOAD_POOL_ATTEMPTS_PER_MINUTE == 480
    assert 16 * c.UPLOAD_SHARD_ATTEMPTS_PER_MINUTE == 480
    assert sum(c.attempts_per_minute(key) for key in c.GLOBAL_POOL_KEYS) == 960


@pytest.mark.parametrize(
    "pool",
    [
        "",
        "global",
        "global:-1",
        "global:16",
        "global:01",
        "global:1.0",
        "global:imports ",
        "org:any",
        None,
        True,
    ],
)
def test_pool_keys_never_fall_back(pool):
    with pytest.raises(ValueError, match="^invalid capacity pool$"):
        c.pool_cap(pool)
    with pytest.raises(ValueError, match="^invalid capacity pool$"):
        c.attempts_per_minute(pool)


@pytest.mark.parametrize("value", [None, str(SCOPE), 1, True])
def test_scope_and_shard_require_uuid_values(value):
    with pytest.raises(ValueError, match="^invalid capacity scope$"):
        c.limits_for(scope_id=value, pool_key="global:imports")
    with pytest.raises(ValueError, match="^invalid capacity owner$"):
        c.global_shard(value)


@pytest.mark.parametrize("index", range(6))
@pytest.mark.parametrize(
    "value", [-1, True, False, 0.0, 1.0, float("inf"), float("nan"), "1", None]
)
def test_vectors_reject_negative_noninteger_and_boolean_components(index, value):
    with pytest.raises(ValueError, match="^invalid attachment value$"):
        vector_at(index, value)


@pytest.mark.parametrize(
    "index, maximum",
    [
        (0, 9223372036854775807),
        (1, 2147483647),
        (2, 2147483647),
        (3, 2147483647),
        (4, 2147483647),
        (5, 2147483647),
    ],
)
def test_exact_storage_bounds_and_checked_overflow(index, maximum):
    vector = vector_at(index, maximum)
    assert numbers(vector)[index] == maximum
    with pytest.raises(ValueError, match="^invalid attachment value$"):
        vector_at(index, maximum + 1)
    with pytest.raises(ValueError, match="^invalid attachment value$"):
        vector + vector_at(index, 1)
    with pytest.raises(ValueError, match="^invalid attachment value$"):
        c.scale_vector(vector, 2)


def test_add_subtract_and_zero_are_exact_and_immutable():
    left = i.ResourceVector(100, 8, 7, 6, 5, 4)
    right = i.ResourceVector(40, 3, 2, 1, 4, 2)
    assert numbers(left + right) == (140, 11, 9, 7, 9, 6)
    assert numbers(left - right) == (60, 5, 5, 5, 1, 2)
    assert numbers(left - left) == (0, 0, 0, 0, 0, 0)
    assert numbers(c.sum_vectors()) == (0, 0, 0, 0, 0, 0)
    assert numbers(c.scale_vector(left, 0)) == (0, 0, 0, 0, 0, 0)
    assert numbers(left) == (100, 8, 7, 6, 5, 4)
    with pytest.raises(FrozenInstanceError):
        left.bytes = 0


@pytest.mark.parametrize("index", range(6))
def test_any_component_underflow_and_full_double_subtraction_fail(index):
    amount = vector_at(index, 1)
    after = amount - amount
    with pytest.raises(ValueError, match="^invalid attachment value$"):
        after - amount
    with pytest.raises(ValueError, match="^invalid attachment value$"):
        i.ResourceVector(100, 1, 1, 1, 1, 1) - vector_at(index, 101)


@pytest.mark.parametrize("multiplier", [-1, True, 1.0, "2", None, 9223372036854775808])
def test_scale_rejects_invalid_multiplier(multiplier):
    with pytest.raises(ValueError, match="^invalid capacity quantity$"):
        c.scale_vector(c.ZERO, multiplier)


@pytest.mark.parametrize("index", range(6))
def test_headroom_checks_every_component_and_rejects_over_cap_history(index):
    cap = i.ResourceVector(100, 5, 5, 5, 5, 5)
    used = cap + vector_at(index, 1)
    assert not c.fits_within(used, cap)
    with pytest.raises(ValueError, match="^capacity exceeds limit$"):
        c.available_capacity(used=used, cap=cap)
    assert numbers(c.available_capacity(used=cap, cap=cap)) == (0, 0, 0, 0, 0, 0)


def test_zero_pool_components_are_real_limits():
    assert not c.fits_within(i.ResourceVector(0, 0, 0, 1, 0, 0), c.UPLOAD_SHARD_CAP)
    assert not c.fits_within(i.ResourceVector(0, 0, 0, 0, 0, 1), c.DERIVATIVE_POOL_CAP)


@pytest.mark.parametrize(
    "source, decoder, extra, expected_upload, expected_job, expected_total",
    [
        (100, 20, 0, (320, 1, 1, 0, 4, 1), (400, 0, 0, 0, 4, 0), (720, 1, 1, 0, 8, 1)),
        (100, 20, 17, (320, 1, 1, 0, 4, 1), (417, 0, 0, 0, 5, 0), (737, 1, 1, 0, 9, 1)),
        (
            8388608,
            16777216,
            0,
            (41943040, 1, 1, 0, 4, 1),
            (33554432, 0, 0, 0, 4, 0),
            (75497472, 1, 1, 0, 8, 1),
        ),
    ],
)
def test_single_import_has_no_double_source_or_pipeline_charge(
    source, decoder, extra, expected_upload, expected_job, expected_total
):
    upload, job = c.single_import_peaks(
        source_bytes=source,
        decoder_scratch_bytes=decoder,
        extra_reader_scratch_bytes=extra,
    )
    assert numbers(upload) == expected_upload
    assert numbers(job) == expected_job
    total = c.sum_vectors(upload, job)
    assert numbers(total) == expected_total
    # Pure arithmetic only. NOT proof of an atomic persisted transfer/retry.
    job_after, upload_after = c.move_vector(
        source=total, destination=c.ZERO, amount=upload
    )
    assert numbers(job_after) == expected_job
    assert numbers(upload_after) == expected_upload
    assert numbers(job_after + upload_after) == expected_total
    assert numbers(total) == expected_total


def test_neutral_move_and_zero_move_preserve_both_inputs():
    source = i.ResourceVector(10, 3, 2, 1, 4, 2)
    destination = i.ResourceVector(7, 2, 1, 0, 1, 1)
    moved = i.ResourceVector(4, 1, 1, 0, 2, 1)
    remaining, received = c.move_vector(
        source=source, destination=destination, amount=moved
    )
    assert numbers(remaining) == (6, 2, 1, 1, 2, 1)
    assert numbers(received) == (11, 3, 2, 0, 3, 2)
    assert numbers(remaining + received) == (17, 5, 3, 1, 5, 3)
    assert c.move_vector(source=source, destination=destination, amount=c.ZERO) == (
        source,
        destination,
    )
    assert numbers(source) == (10, 3, 2, 1, 4, 2)
    assert numbers(destination) == (7, 2, 1, 0, 1, 1)


def test_invalid_move_does_not_return_partial_results():
    source = i.ResourceVector(10, 1, 1, 0, 4, 1)
    full = i.ResourceVector(9223372036854775807, 0, 0, 0, 0, 0)
    with pytest.raises(ValueError):
        c.move_vector(
            source=source, destination=full, amount=i.ResourceVector(1, 0, 0, 0, 0, 0)
        )
    with pytest.raises(ValueError):
        c.move_vector(
            source=source,
            destination=c.ZERO,
            amount=i.ResourceVector(11, 0, 0, 0, 0, 0),
        )
    assert numbers(source) == (10, 1, 1, 0, 4, 1)
    assert numbers(full) == (9223372036854775807, 0, 0, 0, 0, 0)


def test_frozen_peak_formulas_have_independent_expected_numbers():
    assert numbers(
        c.ordinary_upload_peak(expected_bytes=100, decoder_scratch_bytes=20)
    ) == (320, 1, 1, 0, 4, 1)
    floor = c.whole_import_floor(source_bytes=100, output_scratch_bytes=40)
    assert numbers(floor) == (140, 1, 1, 1, 2, 1)
    assert numbers(c.whole_import_segment_peak(segment_bytes=10)) == (30, 0, 0, 0, 3, 1)
    assert numbers(
        floor
        + c.whole_import_segment_peak(segment_bytes=10)
        + c.whole_import_segment_peak(segment_bytes=20)
    ) == (230, 1, 1, 1, 8, 3)
    assert numbers(
        c.derivative_peak(source_bytes=100, output_bytes=30, decoder_scratch_bytes=20)
    ) == (240, 1, 1, 0, 6, 0)


def test_120_segment_plan_accounts_for_each_link_and_three_versions():
    floor = c.whole_import_floor(source_bytes=1000000, output_scratch_bytes=2000000)
    segments = c.scale_vector(c.whole_import_segment_peak(segment_bytes=10000), 120)
    assert numbers(floor + segments) == (6600000, 1, 1, 1, 362, 121)


def test_whole_import_uses_import_pool_not_an_upload_shard():
    plan = c.whole_import_floor(source_bytes=300000000, output_scratch_bytes=100000000)
    assert numbers(plan) == (400000000, 1, 1, 1, 2, 1)
    assert c.fits_within(plan, c.IMPORT_POOL_CAP)
    assert c.fits_within(plan, c.PROJECT_CAP)
    assert not c.fits_within(plan, c.UPLOAD_SHARD_CAP)


@pytest.mark.parametrize("value", [0, -1, True, 1.0, "100", None])
def test_peaks_require_positive_real_integer_source_sizes(value):
    with pytest.raises(ValueError):
        c.ordinary_upload_peak(expected_bytes=value, decoder_scratch_bytes=0)
    with pytest.raises(ValueError):
        c.whole_import_floor(source_bytes=value, output_scratch_bytes=0)
    with pytest.raises(ValueError):
        c.whole_import_segment_peak(segment_bytes=value)
    with pytest.raises(ValueError):
        c.single_import_peaks(
            source_bytes=value, decoder_scratch_bytes=0, extra_reader_scratch_bytes=0
        )
    with pytest.raises(ValueError):
        c.derivative_peak(source_bytes=value, output_bytes=1, decoder_scratch_bytes=0)


@pytest.mark.parametrize("value", [-1, True, 0.0, "0", None])
def test_scratch_allowances_are_explicit_nonnegative_integers(value):
    with pytest.raises(ValueError):
        c.ordinary_upload_peak(expected_bytes=1, decoder_scratch_bytes=value)
    with pytest.raises(ValueError):
        c.whole_import_floor(source_bytes=1, output_scratch_bytes=value)
    with pytest.raises(ValueError):
        c.single_import_peaks(
            source_bytes=1, decoder_scratch_bytes=0, extra_reader_scratch_bytes=value
        )
    with pytest.raises(ValueError):
        c.derivative_peak(source_bytes=1, output_bytes=1, decoder_scratch_bytes=value)


def test_peak_multiplication_and_combined_single_import_overflow_fail():
    with pytest.raises(ValueError):
        c.ordinary_upload_peak(
            expected_bytes=9223372036854775807, decoder_scratch_bytes=0
        )
    # Each U/J fits bigint, their combined charge does not.
    with pytest.raises(ValueError):
        c.single_import_peaks(
            source_bytes=2000000000000000000,
            decoder_scratch_bytes=0,
            extra_reader_scratch_bytes=0,
        )


@pytest.mark.parametrize(
    "record, names",
    [
        (i.ObjectLocator, ("binding_id", "purpose", "key")),
        (i.ObjectIdentity, ("binding_id", "purpose", "key", "version")),
        (i.StoredObject, ("identity", "byte_length", "sha256")),
        (i.VersionPage, ("versions", "delete_markers", "next_cursor")),
        (i.EraseResult, ("empty", "retryable", "reason", "checked_at")),
        (i.LeaseFence, ("owner_kind", "owner_id", "token", "epoch", "expires_at")),
        (
            i.PutPermit,
            (
                "allocation_kind",
                "allocation_id",
                "locator",
                "fence",
                "write_attempt",
                "deadline_at",
            ),
        ),
        (i.ResourceVector, COMPONENTS),
        (i.ReservationOwner, ("kind", "id", "scope_id")),
        (
            i.VerifiedAssetFacts,
            (
                "object",
                "mime_type",
                "validated_at",
                "sample_rate",
                "channels",
                "frame_count",
            ),
        ),
        (i.SourceRef, ("kind", "id")),
        (
            i.SourceFacts,
            (
                "source_kind",
                "source_id",
                "creator_kind",
                "creator_id",
                "generation_kind",
                "generation",
                "byte_length",
                "mime_type",
                "duration_ms",
                "expires_at",
                "authority_generation",
                "parent_trace_id",
                "parent_span_id",
                "direction",
                "connection_id",
            ),
        ),
        (
            i.RepresentationSelection,
            (
                "representation",
                "identity",
                "byte_length",
                "mime_type",
                "sha256",
                "derivative_id",
            ),
        ),
        (i.DecoderRequirements, ("operation", "mime_type", "scratch_bytes")),
    ],
)
def test_records_keep_exact_frozen_field_order_and_are_frozen(record, names):
    assert tuple(field.name for field in fields(record)) == names
    assert record.__dataclass_params__.frozen


@pytest.mark.parametrize("version", ["", "null", "v" * 256, None, 123])
def test_revision_validation_is_content_free(version):
    with pytest.raises(ValueError, match="^invalid attachment value$"):
        replace(identity(), version=version)


def test_opaque_version_and_key_are_not_normalized_or_revealed_by_repr():
    item = i.ObjectIdentity(
        SCOPE, "serving", "original/private-filename", "Opaque:V/1+"
    )
    assert item.key == "original/private-filename"
    assert item.version == "Opaque:V/1+"
    assert "private-filename" not in repr(item)
    assert "Opaque:V/1+" not in repr(item)
    assert replace(item, version="other") != item
    assert replace(item, binding_id=OWNER) != item
    with pytest.raises(FrozenInstanceError):
        item.version = "other"


@pytest.mark.parametrize(
    "changes",
    [
        {"key": ""},
        {"key": "k" * 256},
        {"binding_id": str(SCOPE)},
        {"purpose": "latest"},
    ],
)
def test_locator_checks_only_typed_internal_identity(changes):
    with pytest.raises(ValueError, match="^invalid attachment value$"):
        replace(locator(), **changes)


@pytest.mark.parametrize(
    "value",
    [
        NOW.replace(tzinfo=None),
        NOW.astimezone(timezone(timedelta(hours=1))),
        "2026-11-16",
    ],
)
def test_fences_require_aware_utc_without_silent_conversion(value):
    with pytest.raises(ValueError, match="^invalid attachment value$"):
        replace(fence(), expires_at=value)


@pytest.mark.parametrize("attempt", [0, 4, -1, True, 1.0])
def test_permit_has_one_to_three_fresh_attempts(attempt):
    with pytest.raises(ValueError, match="^invalid attachment value$"):
        i.PutPermit(
            "segment", TOKEN, locator(), fence(), attempt, NOW + timedelta(seconds=30)
        )


def test_permit_validates_fence_kind_and_deadline_but_does_not_claim_live_authority():
    i.PutPermit("upload", OWNER, locator(), None, 1, NOW)
    i.PutPermit("segment", TOKEN, locator(), fence(), 3, NOW + timedelta(seconds=30))
    i.PutPermit(
        "derivative",
        OWNER,
        locator(),
        fence("derivative"),
        1,
        NOW + timedelta(seconds=30),
    )
    for kind, owner, lease, deadline in (
        ("segment", TOKEN, None, NOW),
        ("upload", OWNER, fence("derivative"), NOW),
        ("derivative", TOKEN, fence("derivative"), NOW),
        ("segment", TOKEN, fence(), NOW + timedelta(minutes=16)),
    ):
        with pytest.raises(ValueError, match="^invalid attachment value$"):
            i.PutPermit(kind, owner, locator(), lease, 1, deadline)


@pytest.mark.parametrize("generation", ['""', '"pinned"', '"Opaque:V/1+"'])
def test_strong_etag_including_empty_quoted_value_is_preserved(generation):
    # The final-repairs suite separately checks matching schema source guards.
    assert source_facts(generation=generation).generation == generation


@pytest.mark.parametrize(
    "generation",
    ["", "null", 'W/"weak"', "unquoted", '"line\nbreak"', '"bad space"', '"a"b"'],
)
def test_source_rejects_weak_missing_or_malformed_strong_generation(generation):
    with pytest.raises(ValueError, match="^invalid attachment value$"):
        source_facts(generation=generation)


@pytest.mark.parametrize(
    "changes",
    [
        {"authority_generation": -1},
        {"byte_length": 0},
        {"duration_ms": 0},
        {"creator_kind": "service"},
        {"parent_span_id": "0000000000000000"},
        {"parent_span_id": "ABCDEF0123456789"},
        {"connection_id": "connection"},
    ],
)
def test_source_facts_reject_invalid_typed_ancestry(changes):
    with pytest.raises(ValueError, match="^invalid attachment value$"):
        source_facts(**changes)


def test_source_value_has_no_locator_or_credential_fields():
    value = source_facts()
    names = {field.name for field in fields(value)}
    assert not names.intersection(
        {"url", "locator", "credentials", "headers", "signed_query"}
    )
    assert "revision-1" not in repr(value)


def test_audio_requires_positive_probed_group_non_audio_preserves_absence():
    stored = i.StoredObject(identity(), 100, "a" * 64)
    audio = i.VerifiedAssetFacts(stored, "audio/wav", NOW, 48000, 2, 48000)
    assert audio.channels == 2
    for name in ("sample_rate", "channels", "frame_count"):
        for value in (None, 0, -1, True):
            with pytest.raises(ValueError, match="^invalid attachment value$"):
                replace(audio, **{name: value})
    image = i.VerifiedAssetFacts(stored, "image/png", NOW, None, None, None)
    assert (image.sample_rate, image.channels, image.frame_count) == (None, None, None)


@pytest.mark.parametrize("representation", ["channel-0", "channel-1", "peaks"])
def test_channel_and_peaks_selection_require_derivative_identity(representation):
    with pytest.raises(ValueError, match="^invalid attachment value$"):
        i.RepresentationSelection(
            representation, identity(), 100, "audio/wav", "a" * 64, None
        )
    value = i.RepresentationSelection(
        representation, identity(), 100, "audio/wav", "a" * 64, OWNER
    )
    assert value.derivative_id == OWNER


def test_original_playable_and_serving_purpose_contract():
    i.RepresentationSelection("original", identity(), 100, "audio/wav", "a" * 64, None)
    i.RepresentationSelection("playable", identity(), 100, "audio/wav", "a" * 64, None)
    i.RepresentationSelection("playable", identity(), 100, "audio/wav", "a" * 64, OWNER)
    with pytest.raises(ValueError):
        i.RepresentationSelection(
            "original", identity(), 100, "audio/wav", "a" * 64, OWNER
        )
    with pytest.raises(ValueError):
        i.RepresentationSelection(
            "original", identity("quarantine"), 100, "audio/wav", "a" * 64, None
        )


def test_version_page_is_immutable_bounded_and_preserves_cursor():
    page = i.VersionPage((identity(),) * 99, (identity(),), "opaque/cursor")
    assert len(page.versions) == 99
    assert page.next_cursor == "opaque/cursor"
    # The literal string is a valid opaque cursor, not a missing revision.
    assert i.VersionPage((), (), "null").next_cursor == "null"
    with pytest.raises(ValueError):
        i.VersionPage((identity(),) * 100, (identity(),), None)
    with pytest.raises(ValueError):
        i.VersionPage([identity()], (), None)
    with pytest.raises(ValueError):
        i.VersionPage((), (), "")


def test_protocols_are_not_fake_runtime_implementations():
    for protocol in (
        i.CapacityLocks,
        i.CapacityOperations,
        i.PrivateMediaStorage,
        i.SourceReader,
    ):
        with pytest.raises(TypeError):
            protocol()
    # The accounting owner may subsequently add real locked operations to c;
    # these pure tests deliberately make no claim about their implementation.


def test_frozen_limits_and_reader_parameter_kinds():
    parameters = signature(c.limits_for).parameters
    assert tuple(parameters) == ("scope_id", "pool_key")
    assert all(p.kind is Parameter.KEYWORD_ONLY for p in parameters.values())
    parameters = signature(i.SourceReader.read_range).parameters
    assert tuple(parameters) == ("self", "start", "end")
    assert parameters["start"].kind is Parameter.KEYWORD_ONLY
    assert parameters["end"].kind is Parameter.KEYWORD_ONLY


@pytest.mark.parametrize(
    "method, positional, keyword_only",
    [
        ("lock_capacity", ("self",), ("scope_id", "pool_keys")),
        ("reserve_locked", ("self", "locks"), ("owner", "amount", "expires_at")),
        ("resize_locked", ("self", "locks"), ("owner", "amount")),
        ("transfer_locked", ("self", "locks"), ("from_owner", "to_owner", "amount")),
        ("release_locked", ("self", "locks"), ("owner", "cleanup_complete", "now")),
        ("count_attempt", ("self",), ("scope_id", "pool_key", "now")),
        ("limits_for", ("self",), ("scope_id", "pool_key")),
    ],
)
def test_frozen_accounting_signatures_remain_declarations(
    method, positional, keyword_only
):
    parameters = signature(getattr(i.CapacityOperations, method)).parameters
    assert tuple(parameters) == positional + keyword_only
    for name in positional:
        assert parameters[name].kind is Parameter.POSITIONAL_OR_KEYWORD
    for name in keyword_only:
        assert parameters[name].kind is Parameter.KEYWORD_ONLY


@pytest.mark.parametrize(
    "method, positional, keyword_only",
    [
        ("verify_identity", ("self",), ("operation",)),
        ("verify_readiness", ("self",), ()),
        ("put_quarantine", ("self", "permit", "stream", "length"), ()),
        ("snapshot", ("self", "identity", "output"), ("expected_length",)),
        (
            "promote",
            ("self", "snapshot"),
            ("permit", "expected_length", "expected_sha256"),
        ),
        ("open_range", ("self", "identity"), ("start", "end", "expected_length")),
        ("list_versions", ("self", "locator"), ("cursor", "limit")),
        ("erase_version", ("self", "identity"), ()),
        ("erase_all_versions", ("self", "locator"), ()),
        ("erase_upload", ("self", "upload_id"), ()),
    ],
)
def test_frozen_storage_parameter_names_and_kinds(method, positional, keyword_only):
    parameters = signature(getattr(i.PrivateMediaStorage, method)).parameters
    assert tuple(parameters) == positional + keyword_only
    for name in positional:
        assert parameters[name].kind is Parameter.POSITIONAL_OR_KEYWORD
    for name in keyword_only:
        assert parameters[name].kind is Parameter.KEYWORD_ONLY
    if method == "list_versions":
        assert parameters["cursor"].default is None
        assert parameters["limit"].default == 100
