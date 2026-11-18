"""Asset-owned, bounded channel/peaks work on the asset's historical binding.

A row (including both cleanup keys) exists before any PUT. Public client IDs
never allocate storage. Occurrences authorize use; Segment is provenance only.
The row's lease and immutable verified version fence every publication. Unknown
versions are never adopted, and stale workers may erase only their own exact,
unreferenced result. Capacity remains charged until scratch and unknown writes
are reconciled through the accepted-write horizon.
"""

import contextlib
import fcntl
import hashlib
import os
import shutil
import uuid
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from tracer.models.attachment import (
    Attachment,
    AttachmentOccurrence,
    AttachmentScope,
    AttachmentState,
    AttachmentWriteGrant,
    CaptureSession,
)
from tracer.models.attachment_recording import AttachmentRecordingDerivative
from tracer.services.attachments import sandbox
from tracer.services.attachments.bootstrap import check_session, session_policy
from tracer.services.attachments.capacity import DERIVATIVE_POOL_KEY, derivative_peak
from tracer.services.attachments.contract import AttachmentError, admit_audio
from tracer.services.attachments.interfaces import (
    DecoderRequirements,
    EraseResult,
    LeaseFence,
    ObjectIdentity,
    ObjectLocator,
    PutPermit,
    RepresentationSelection,
    ReservationOwner,
    StoredObject,
    VerifiedAssetFacts,
)
from tracer.services.attachments.recordings import SegmentPlan

HEADER_BYTES = 44
PEAK_LEVEL_FACTORS = (1, 10, 100)
REPRESENTATIONS = ("channel-0", "channel-1", "peaks")
MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_SECONDS = 30
LEASE_SECONDS = 15 * 60
WORK_SECONDS = 3 * 60 * 60
DRAIN_SECONDS = 15
MAX_WRITES = 3


def peaks_bytes(*, frame_count, rate, channels):
    base = max(1, rate * 10 // 1000)
    buckets = -(-frame_count // base)
    return sum(-(-buckets // factor) * channels * 4 for factor in PEAK_LEVEL_FACTORS)


def _asset_facts(asset):
    try:
        return VerifiedAssetFacts(
            object=StoredObject(
                ObjectIdentity(
                    asset.storage_binding_id,
                    "serving",
                    asset.verified_storage_key,
                    asset.verified_storage_version,
                ),
                asset.verified_byte_length,
                asset.verified_sha256,
            ),
            mime_type=asset.verified_mime_type,
            validated_at=asset.verified_at,
            sample_rate=asset.verified_sample_rate,
            channels=asset.verified_channels,
            frame_count=asset.verified_frame_count,
        )
    except (ValueError, TypeError):
        raise AttachmentError("media_unavailable", 503) from None


def _plan(asset, representation):
    facts = _asset_facts(asset)
    if (
        not facts.mime_type.startswith("audio/")
        or facts.sample_rate is None
        or facts.frame_count is None
        or facts.channels not in (1, 2)
        or not 8000 <= facts.sample_rate <= 96000
        or facts.frame_count < 1
        or facts.object.byte_length > MAX_INPUT_BYTES
        or facts.frame_count > MAX_SECONDS * facts.sample_rate
    ):
        raise AttachmentError("too_large", 413)
    if representation == "peaks":
        output = peaks_bytes(
            frame_count=facts.frame_count,
            rate=facts.sample_rate,
            channels=facts.channels,
        )
    elif representation in {"channel-0", "channel-1"} and facts.channels == 2:
        output = HEADER_BYTES + facts.frame_count * 2
    else:
        raise AttachmentError("validation_rejected", 422)
    # The sandbox receives bounded PCM scratch even for encoded inputs. The
    # shared decoder owns actual format support; no permissive local decoder.
    decoded = HEADER_BYTES + facts.frame_count * facts.channels * 2
    if max(output, decoded) > MAX_INPUT_BYTES:
        raise AttachmentError("too_large", 413)
    return (
        facts,
        output,
        decoded,
        derivative_peak(
            source_bytes=facts.object.byte_length,
            output_bytes=output,
            decoder_scratch_bytes=decoded,
        ),
    )


def enqueue_for_asset_locked(attachment):
    """Caller holds scope/asset publication locks; enqueue once for either producer.

    Scheduling does not allocate capacity or run decoders, so a playable long
    original (including the existing 36.468s capture) is not rejected here.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("derivative enqueue requires publication transaction")
    if attachment.state != AttachmentState.READY or attachment.deleted_at:
        raise AttachmentError("validation_fenced", 409)
    facts = _asset_facts(attachment)
    if not facts.mime_type.startswith("audio/"):
        return ()
    wanted = REPRESENTATIONS if facts.channels == 2 else ("peaks",)
    now = timezone.now()
    rows = []
    for representation in wanted:
        # The server UUID is the durable row identity, NOT a scoped client UUID.
        row_id = uuid.uuid4()
        row, _ = AttachmentRecordingDerivative.objects.get_or_create(
            attachment=attachment,
            representation=representation,
            defaults={
                "id": row_id,
                "scope_id": attachment.scope_id,
                "storage_object_id": row_id,
                "storage_key": f"original/{row_id}",
                "quarantine_key": f"quarantine/{row_id}",
                "policy_version": attachment.policy_version,
                "deletion_generation": attachment.deletion_generation,
                "state": "queued",
                "available_at": now,
                "cleanup_not_before": now,
                "reservation_id": uuid.uuid4(),
                "reservation_created_at": now,
                "reservation_expires_at": now + timedelta(seconds=WORK_SECONDS),
                "reservation_pool_key": DERIVATIVE_POOL_KEY,
                "reservation_reserved_bytes": facts.object.byte_length,
                "reservation_decoded_bytes": 0,
                "reservation_object_count": 6,
                "reservation_part_count": 1,
                "reservation_charge_bytes": 0,
                "reservation_transfers": 0,
                "reservation_validation_jobs": 0,
                "reservation_recordings": 0,
                "reservation_objects": 0,
                "reservation_links": 0,
            },
        )
        rows.append(row.id)
    return tuple(rows)


def identity_for(*, attachment_id, representation):
    """Selection metadata, never read authority. No imported-only segment filter."""
    if representation not in (*REPRESENTATIONS, "playable"):
        raise AttachmentError("not_found", 404)
    row = (
        AttachmentRecordingDerivative.objects.select_related("attachment")
        .filter(
            attachment_id=attachment_id,
            representation=representation,
            state="ready",
            published_at__isnull=False,
        )
        .first()
    )
    if row is None:
        raise AttachmentError("not_found", 404)
    asset = row.attachment
    if (
        asset.state != AttachmentState.READY
        or asset.deleted_at
        or row.scope_id != asset.scope_id
        or row.policy_version != asset.policy_version
        or row.deletion_generation != asset.deletion_generation
    ):
        raise AttachmentError("not_found", 404)
    try:
        return RepresentationSelection(
            representation,
            ObjectIdentity(
                asset.storage_binding_id,
                "serving",
                row.storage_key,
                row.storage_version,
            ),
            row.byte_length,
            row.mime_type,
            row.sha256,
            row.id,
        )
    except (TypeError, ValueError):
        raise AttachmentError("not_found", 404) from None


def _source_authority(asset):
    """Bounded current parent batch outside locks; denied rows grant no work.

    The frozen authority primitive includes committed session deletion/remap
    checks. A survivor may authorize asset work, never a different occurrence's
    gateway request. Publication checks these exact local snapshots again.
    """
    from tracer.services.attachments.authority import authorize_parent_batch
    from tracer.services.attachments.control_plane import Retention, read_spans
    from tracer.services.attachments.occurrences import Parent

    now = timezone.now()
    scope = AttachmentScope.objects.get(pk=asset.scope_id)
    rows = list(
        AttachmentOccurrence.objects.select_related("capture_session")
        .filter(
            attachment_id=asset.id,
            scope_id=asset.scope_id,
            deleted_at__isnull=True,
            bound_at__isnull=False,
            expires_at__gt=now,
            source_expires_at__gt=now,
            policy_version=scope.policy_version,
            deletion_generation=scope.deletion_generation,
        )
        .order_by("id")[:100]
    )
    if not rows:
        raise AttachmentError("source_unavailable", 403)
    spans = read_spans(
        scope.project_id,
        {r.span_id for r in rows},
        trace_ids={r.trace_id for r in rows},
    )
    states = authorize_parent_batch(
        scope=scope,
        parents=tuple(Parent(r.trace_id, r.span_id) for r in rows),
        spans=spans,
        retention=Retention(),
    )
    authorized = {}
    for occurrence in rows:
        source = states.get((occurrence.trace_id, occurrence.span_id))
        if (
            source is None
            or not source.readable
            or source.scope_id != scope.id
            or source.started_at != occurrence.source_started_at
            or source.generation != occurrence.source_generation
            or source.expires_at <= now
        ):
            continue
        try:
            check_session(occurrence.capture_session)
            admit_audio(
                scope,
                session_policy(occurrence.capture_session),
                direction=occurrence.direction,
                source_kind=asset.source_kind,
            )
        except AttachmentError:
            continue
        authorized[occurrence.id] = (occurrence.capture_session_id, source)
    if not authorized:
        raise AttachmentError("source_unavailable", 403)
    return authorized


@contextlib.contextmanager
def _locked(derivative_id, scope_id, *, capacity=False, authorized=None):
    """Budget -> scope -> creator -> session -> asset -> occurrence -> derivative."""
    seed = AttachmentRecordingDerivative.objects.get(
        pk=derivative_id, scope_id=scope_id
    )
    with transaction.atomic():
        locks = None
        if capacity:
            from tracer.services.attachments.capacity_db import lock_capacity

            locks = lock_capacity(scope_id=scope_id, pool_keys=(DERIVATIVE_POOL_KEY,))
        scope = AttachmentScope.objects.select_for_update().get(pk=scope_id)
        sessions = {}
        if authorized:
            from accounts.models import OrgApiKey, User

            session_ids = {value[0] for value in authorized.values()}
            candidates = list(
                CaptureSession.objects.filter(pk__in=session_ids, scope_id=scope_id)
            )
            key_ids = [s.writer_id for s in candidates if s.writer_kind == "api_key"]
            keys = list(OrgApiKey.no_workspace_objects.filter(pk__in=key_ids))
            grants = list(
                AttachmentWriteGrant.objects.filter(
                    scope_id=scope_id, api_key_id__in=key_ids
                )
            )
            user_ids = {s.writer_id for s in candidates if s.writer_kind == "user"}
            user_ids.update(g.granted_by_id for g in grants if g.granted_by_id)
            user_ids.update(k.user_id for k in keys if k.user_id)
            user_ids.update(k.minted_by_id for k in keys if k.minted_by_id)
            list(
                User.objects.select_for_update().filter(pk__in=user_ids).order_by("id")
            )
            list(
                OrgApiKey.no_workspace_objects.select_for_update()
                .filter(pk__in=key_ids)
                .order_by("id")
            )
            list(
                AttachmentWriteGrant.objects.select_for_update()
                .filter(scope_id=scope_id, api_key_id__in=key_ids)
                .order_by("id")
            )
            sessions = {
                s.id: s
                for s in CaptureSession.objects.select_for_update()
                .filter(pk__in=session_ids, scope_id=scope_id)
                .order_by("id")
            }
        asset = Attachment.objects.select_for_update().get(
            pk=seed.attachment_id, scope_id=scope_id
        )
        occurrences = list(
            AttachmentOccurrence.objects.select_for_update()
            .filter(
                attachment_id=asset.id,
                scope_id=scope_id,
                **({"pk__in": authorized} if authorized else {}),
            )
            .order_by("id")
        )
        row = AttachmentRecordingDerivative.objects.select_for_update().get(
            pk=seed.id, scope_id=scope_id, attachment_id=asset.id
        )
        if authorized:
            _live(scope, asset, row, occurrences, sessions, authorized)
        yield locks, asset, row


def _live(scope, asset, row, occurrences, sessions, authorized):
    now = timezone.now()
    if (
        not getattr(settings, "FI_ATTACHMENTS_ENABLED", False)
        or not scope.enabled
        or asset.state != AttachmentState.READY
        or asset.deleted_at
        or asset.policy_version != scope.policy_version
        or row.policy_version != scope.policy_version
        or asset.deletion_generation != scope.deletion_generation
        or row.deletion_generation != scope.deletion_generation
    ):
        raise AttachmentError("validation_fenced", 409)
    for occurrence in occurrences:
        session_id, source = authorized[occurrence.id]
        if (
            occurrence.capture_session_id != session_id
            or session_id not in sessions
            or occurrence.deleted_at
            or not occurrence.bound_at
            or occurrence.expires_at <= now
            or not occurrence.source_expires_at
            or occurrence.source_expires_at <= now
            or source.expires_at <= now
            or occurrence.source_generation != source.generation
            or occurrence.source_started_at != source.started_at
            or occurrence.policy_version != scope.policy_version
            or occurrence.deletion_generation != scope.deletion_generation
        ):
            continue
        try:
            check_session(sessions[session_id])
            admit_audio(
                scope,
                session_policy(sessions[session_id]),
                direction=occurrence.direction,
                source_kind=asset.source_kind,
            )
        except AttachmentError:
            continue
        return
    raise AttachmentError("source_unavailable", 403)


def _holds(row, fence, *, states):
    return (
        row.id == fence.owner_id
        and row.lease_token == fence.token
        and row.lease_epoch == fence.epoch
        and row.lease_until == fence.expires_at
        and row.state in states
        and row.lease_until > timezone.now()
        and row.reservation_expires_at > timezone.now()
        and row.reservation_settled_at is None
    )


def _require(row, fence, *, states):
    if not _holds(row, fence, states=states):
        raise AttachmentError("conflict", 409)


def _claim(row, authorized):
    from tracer.services.attachments.capacity_db import count_attempt, reserve_locked

    now = timezone.now()
    if not count_attempt(scope_id=row.scope_id, pool_key=DERIVATIVE_POOL_KEY, now=now):
        raise AttachmentError("throttled", 429)
    with _locked(row.id, row.scope_id, capacity=True, authorized=authorized) as (
        locks,
        asset,
        current,
    ):
        if (
            current.state not in {"queued", "recovering", "writing"}
            or current.reservation_expires_at <= now
            or current.reservation_settled_at
            or (current.lease_until and current.lease_until > now)
            or (current.write_deadline_at and current.cleanup_not_before > now)
        ):
            return None
        _, _, decoded, peak = _plan(asset, current.representation)
        if not current.reservation_charge_bytes:
            reserve_locked(
                locks,
                owner=ReservationOwner("derivative", current.id, current.scope_id),
                amount=peak,
                expires_at=current.reservation_expires_at,
            )
        current.lease_token = uuid.uuid4()
        current.lease_epoch += 1
        current.lease_until = min(
            now + timedelta(seconds=LEASE_SECONDS), current.reservation_expires_at
        )
        current.state = "recovering"
        current.cleanup_not_before = max(
            current.cleanup_not_before,
            current.lease_until + timedelta(seconds=DRAIN_SECONDS),
        )
        current.reservation_decoded_bytes = decoded
        current.save(
            update_fields=[
                "lease_token",
                "lease_epoch",
                "lease_until",
                "state",
                "cleanup_not_before",
                "reservation_decoded_bytes",
            ]
        )
        return LeaseFence(
            "derivative",
            current.id,
            current.lease_token,
            current.lease_epoch,
            current.lease_until,
        )


def _locators(asset, row):
    return (
        ObjectLocator(asset.storage_binding_id, "quarantine", row.quarantine_key),
        ObjectLocator(asset.storage_binding_id, "serving", row.storage_key),
    )


def _referenced(asset, row, identity):
    return (
        identity.binding_id == asset.storage_binding_id
        and identity.purpose == "serving"
        and (
            (
                identity.key == row.storage_key
                and identity.version == row.storage_version
            )
            or (
                identity.key == asset.verified_storage_key
                and identity.version == asset.verified_storage_version
            )
        )
    )


def _erase_unknown(storage, row, asset, *, fence=None):
    """Enumerate only exact locators in a no-current-PUT phase; never adopt."""
    for locator in _locators(asset, row):
        cursor = None
        seen = set()
        for _ in range(100):
            page = storage.list_versions(locator, cursor=cursor, limit=100)
            for identity in page.versions + page.delete_markers:
                if (identity.binding_id, identity.purpose, identity.key) != (
                    locator.binding_id,
                    locator.purpose,
                    locator.key,
                ):
                    raise AttachmentError("cleanup_unavailable", 503)
                with _locked(row.id, row.scope_id) as (_, locked_asset, current):
                    if fence:
                        _require(current, fence, states={"recovering"})
                    elif current.state != "ready":
                        raise AttachmentError("conflict", 409)
                    owned = _referenced(locked_asset, current, identity)
                if not owned:
                    result = storage.erase_version(identity)
                    # The frozen exact-erase contract returns None only after
                    # proven physical absence. EraseResult belongs to all-version
                    # cleanup; neither it nor any other value proves exact success.
                    # Observe the horizon locally: exact success has no timestamp.
                    # Exceptions deliberately propagate, keeping cleanup pending.
                    if result is not None or (
                        fence is None and timezone.now() < row.cleanup_not_before
                    ):
                        raise AttachmentError("cleanup_unavailable", 503)
            cursor = page.next_cursor
            if cursor is None:
                break
            if cursor in seen:
                raise AttachmentError("cleanup_unavailable", 503)
            seen.add(cursor)
        else:
            raise AttachmentError("cleanup_unavailable", 503)


def _prepare(row, fence, authorized):
    with _locked(row.id, row.scope_id, authorized=authorized) as (_, asset, current):
        _require(current, fence, states={"writing"})
        if current.write_attempts >= MAX_WRITES or current.storage_version is not None:
            raise AttachmentError("validation_rejected", 422)
        now = timezone.now()
        deadline = min(
            now + timedelta(seconds=29),
            current.lease_until,
            current.reservation_expires_at,
        )
        current.write_attempts += 1
        current.write_deadline_at = deadline
        current.cleanup_not_before = max(
            current.cleanup_not_before,
            current.lease_until + timedelta(seconds=DRAIN_SECONDS),
            deadline + timedelta(seconds=DRAIN_SECONDS),
        )
        current.unowned_cleanup_due_at = current.cleanup_not_before
        current.save(
            update_fields=[
                "write_attempts",
                "write_deadline_at",
                "cleanup_not_before",
                "unowned_cleanup_due_at",
            ]
        )
        return PutPermit(
            "derivative",
            current.id,
            ObjectLocator(asset.storage_binding_id, "serving", current.storage_key),
            fence,
            current.write_attempts,
            deadline,
        )


def _record(row, permit, identity, facts, digest, length, authorized):
    if (identity.binding_id, identity.purpose, identity.key) != (
        permit.locator.binding_id,
        permit.locator.purpose,
        permit.locator.key,
    ):
        raise AttachmentError("media_unavailable", 503)
    with _locked(row.id, row.scope_id, authorized=authorized) as (_, asset, current):
        if _referenced(asset, current, identity):
            return "already_owned"
        if (
            not _holds(current, permit.fence, states={"writing"})
            or current.write_attempts != permit.write_attempt
        ):
            return "stale_unowned"
        _, output, _, _ = _plan(asset, current.representation)
        if (
            length != output
            or facts["frame_count"] != asset.verified_frame_count
            or facts["sample_rate"] != asset.verified_sample_rate
            or facts["channels"]
            != (asset.verified_channels if current.representation == "peaks" else 1)
        ):
            raise AttachmentError("snapshot_changed", 409)
        current.storage_version, current.sha256, current.byte_length = (
            identity.version,
            digest,
            length,
        )
        current.mime_type = (
            "application/octet-stream"
            if current.representation == "peaks"
            else "audio/wav"
        )
        current.sample_rate, current.channels, current.frame_count = (
            facts["sample_rate"],
            facts["channels"],
            facts["frame_count"],
        )
        current.facts = {
            key: facts[key]
            for key in ("sample_rate", "channels", "frame_count", "levels")
            if key in facts
        }
        current.state, current.published_at = "ready", timezone.now()
        current.lease_token, current.lease_until = None, None
        current.unowned_cleanup_due_at = current.cleanup_not_before
        current.save(
            update_fields=[
                "storage_version",
                "sha256",
                "byte_length",
                "mime_type",
                "sample_rate",
                "channels",
                "frame_count",
                "facts",
                "state",
                "published_at",
                "lease_token",
                "lease_until",
                "unowned_cleanup_due_at",
            ]
        )
        return "recorded"


def _cleanup_returned(row, identity, storage):
    # Same scope/asset/row ordering as publication. Unknown results are NEVER
    # eligible for adoption, so exact deletion outside the lock is safe.
    with _locked(row.id, row.scope_id) as (_, asset, current):
        if _referenced(asset, current, identity):
            return "already_owned"
        if identity not in (
            ObjectIdentity(loc.binding_id, loc.purpose, loc.key, identity.version)
            for loc in _locators(asset, current)
        ):
            raise AttachmentError("cleanup_unavailable", 503)
    if storage.erase_version(identity) is not None:
        raise AttachmentError("cleanup_unavailable", 503)
    return "stale_unowned"


def _work_root():
    value = getattr(settings, "FI_ATTACHMENTS_WORK_DIR", "")
    if not value or not Path(value).is_absolute():
        raise AttachmentError("validation_unavailable", 503)
    root = Path(value)
    if root.is_symlink() or not root.is_dir() or root.resolve() != root:
        raise AttachmentError("validation_unavailable", 503)
    return root


@contextlib.contextmanager
def _scratch_lock(row):
    # One deterministic directory per persisted row, with only server epoch
    # children. No arbitrary caller paths, symlink walks, or system-temp fallback.
    root = _work_root()
    parent = root / "derivative"
    parent.mkdir(mode=0o700, exist_ok=True)
    if parent.is_symlink():
        raise AttachmentError("validation_unavailable", 503)
    work = parent / str(row.id)
    work.mkdir(mode=0o700, exist_ok=True)
    if work.is_symlink():
        raise AttachmentError("validation_unavailable", 503)
    fd = os.open(
        work / ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600
    )
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AttachmentError("conflict", 409) from None
        yield work
    finally:
        os.close(fd)


def _clear_scratch(work):
    entries = list(work.iterdir())
    if len(entries) > 100:
        raise AttachmentError("cleanup_unavailable", 503)
    for path in entries:
        if path.name == ".lock":
            continue
        if not path.name.isdecimal() or path.is_symlink() or not path.is_dir():
            raise AttachmentError("cleanup_unavailable", 503)
        if not shutil.rmtree.avoids_symlink_attacks:
            raise AttachmentError("cleanup_unavailable", 503)
        shutil.rmtree(path)


def _storage(asset, operation, injected=None):
    from tracer.services.attachments.storage import for_binding

    storage = (
        injected
        if injected is not None
        else for_binding(asset.storage_binding_id, operation=operation)
    )
    if storage.binding_id != asset.storage_binding_id:
        raise AttachmentError("storage_unverified", 503)
    storage.verify_identity(operation=operation)
    return storage


def _settle(row, storage, work):
    """No decoder/source/default readiness here. Preserve published exact bytes."""
    row = AttachmentRecordingDerivative.objects.select_related("attachment").get(
        pk=row.id
    )
    asset = row.attachment
    now = timezone.now()
    if row.cleanup_not_before > now or (row.lease_until and row.lease_until > now):
        return False
    _clear_scratch(work)
    if row.state == "ready":
        _erase_unknown(storage, row, asset)
    elif row.state in {"cleanup", "failed", "erased"}:
        # Published derivatives can only be fully erased behind the asset's
        # irreversible tombstone, never because their work deadline expired.
        if row.published_at and not asset.deleted_at:
            return False
        for locator in _locators(asset, row):
            result = storage.erase_all_versions(locator)
            if (
                not isinstance(result, EraseResult)
                or not result.empty
                or result.retryable
                or result.reason is not None
                or result.checked_at < row.cleanup_not_before
                or result.checked_at > timezone.now()
            ):
                return False
    else:
        return False
    from tracer.services.attachments.capacity_db import release_locked

    with _locked(row.id, row.scope_id, capacity=True) as (
        locks,
        current_asset,
        current,
    ):
        if (
            current.state != row.state
            or current.lease_epoch != row.lease_epoch
            or current.cleanup_not_before > now
        ):
            return False
        if (
            current.published_at
            and current.state != "ready"
            and not current_asset.deleted_at
        ):
            return False
        current.cleanup_checked_at = timezone.now()
        current.unowned_cleanup_due_at = None
        current.lease_token, current.lease_until = None, None
        if current.state != "ready":
            current.state = "erased"
            current.erased_at = current.erased_at or timezone.now()
        current.save(
            update_fields=[
                "cleanup_checked_at",
                "unowned_cleanup_due_at",
                "lease_token",
                "lease_until",
                "state",
                "erased_at",
            ]
        )
        release_locked(
            locks,
            owner=ReservationOwner("derivative", current.id, current.scope_id),
            cleanup_complete=True,
            now=timezone.now(),
        )
    return True


def generate(derivative_id, *, scope_id, storage=None):
    row = AttachmentRecordingDerivative.objects.select_related("attachment").get(
        pk=derivative_id, scope_id=scope_id
    )
    if not getattr(settings, "FI_ATTACHMENTS_ENABLED", False):
        return "disabled"
    with _scratch_lock(row) as work:
        if row.state in {"ready", "cleanup", "failed", "erased"}:
            store = _storage(row.attachment, "erase", storage)
            return "settled" if _settle(row, store, work) else "cleanup"
        from tracer.services.attachments.readiness import require_decoder

        source, output_limit, decoded, _ = _plan(row.attachment, row.representation)
        store = _storage(row.attachment, "write", storage)
        require_decoder(DecoderRequirements("derive", source.mime_type, decoded))
        fence = _claim(row, _source_authority(row.attachment))
        if fence is None:
            return "pending"
        _clear_scratch(work)
        _erase_unknown(store, row, row.attachment, fence=fence)
        with _locked(
            row.id, scope_id, authorized=_source_authority(row.attachment)
        ) as (_, _, current):
            _require(current, fence, states={"recovering"})
            current.state = "writing"
            current.save(update_fields=["state"])
        epoch = work / str(fence.epoch)
        epoch.mkdir(mode=0o700)
        try:
            with (
                (epoch / "source").open("x+b") as snapshot,
                (epoch / "output").open("x+b") as output,
                (epoch / "normalized").open("x+b") as normalized,
            ):
                observed = store.snapshot(
                    source.object.identity,
                    snapshot,
                    expected_length=source.object.byte_length,
                )
                # BinaryIO snapshot writers need not flush our buffered file.
                # Persist buffered bytes before fstat or handing the FD to the
                # confined decoder; otherwise valid small inputs look empty.
                snapshot.flush()
                if (
                    observed != source.object.sha256
                    or os.fstat(snapshot.fileno()).st_size != source.object.byte_length
                ):
                    raise AttachmentError("snapshot_changed", 409)
                snapshot.flush()
                _source_authority(row.attachment)
                # N + D + M scratch was reserved before snapshotting. Decode
                # through the existing confined worker, using only immutable
                # verified facts. EOF and decoder exit must agree before any
                # split/peaks output can be published; no original-byte alias.
                normalized_facts = {
                    "sample_rate": source.sample_rate,
                    "channels": source.channels,
                    "frame_count": source.frame_count,
                }
                plan = SegmentPlan(
                    index=0,
                    start_frame=0,
                    frame_count=source.frame_count,
                    start_ms=0,
                    duration_ms=-(-source.frame_count * 1000 // source.sample_rate),
                    byte_length=decoded,
                )
                with sandbox.pcm_stream(
                    str(epoch / "source"),
                    source.mime_type,
                    normalized_facts,
                    deadline=fence.expires_at,
                ) as pcm:
                    sandbox.write_wav_segment(pcm, normalized, plan, normalized_facts)
                if os.fstat(normalized.fileno()).st_size != decoded:
                    raise AttachmentError("validation_rejected", 422)
                probed = sandbox.probe(str(epoch / "normalized"))
                if any(
                    probed.get(key) != value for key, value in normalized_facts.items()
                ):
                    raise AttachmentError("validation_rejected", 422)
                operation, args = (
                    ("peaks", ())
                    if row.representation == "peaks"
                    else ("split", (int(row.representation[-1]),))
                )
                facts = sandbox.run(
                    operation,
                    str(epoch / "normalized"),
                    *args,
                    output=output,
                    output_limit=output_limit,
                )
                output.flush()
                expected_channels = (
                    source.channels if row.representation == "peaks" else 1
                )
                if (
                    os.fstat(output.fileno()).st_size != output_limit
                    or facts.get("sample_rate") != source.sample_rate
                    or facts.get("frame_count") != source.frame_count
                    or facts.get("channels") != expected_channels
                ):
                    raise AttachmentError("validation_rejected", 422)
                if row.representation != "peaks":
                    probed = sandbox.probe(str(epoch / "output"))
                    if any(
                        probed.get(key) != facts[key]
                        for key in ("sample_rate", "channels", "frame_count")
                    ):
                        raise AttachmentError("validation_rejected", 422)
                output.seek(0)
                digest = hashlib.sha256()
                for chunk in iter(lambda: output.read(256 * 1024), b""):
                    digest.update(chunk)
                output.seek(0)
                permit = _prepare(row, fence, _source_authority(row.attachment))
                identity = store.promote(
                    output,
                    permit=permit,
                    expected_length=output_limit,
                    expected_sha256=digest.hexdigest(),
                )
                try:
                    result = _record(
                        row,
                        permit,
                        identity,
                        facts,
                        digest.hexdigest(),
                        output_limit,
                        _source_authority(row.attachment),
                    )
                except Exception:
                    _cleanup_returned(row, identity, store)
                    raise
                if result == "stale_unowned":
                    _cleanup_returned(row, identity, store)
                    return "stale"
        finally:
            _clear_scratch(work)
    return "ready"


def erase_for_asset(*, attachment_id, scope_id, now):
    """Only unified retention may call this after the survivor/tombstone fence."""
    with transaction.atomic():
        AttachmentScope.objects.select_for_update().get(pk=scope_id)
        asset = Attachment.objects.select_for_update().get(
            pk=attachment_id, scope_id=scope_id
        )
        if asset.state != AttachmentState.DELETED or not asset.deleted_at:
            return False
        # Unknown source state is NOT proof of death. A live link keeps all
        # representations, even if its current authority lookup is unavailable.
        survivors = (
            AttachmentOccurrence.objects.select_for_update()
            .filter(
                attachment_id=asset.id,
                deleted_at__isnull=True,
                expires_at__gt=now,
            )
            .filter(Q(source_expires_at__isnull=True) | Q(source_expires_at__gt=now))
        )
        if survivors.exists():
            return False
        rows = list(
            AttachmentRecordingDerivative.objects.select_for_update()
            .filter(attachment_id=asset.id, scope_id=scope_id)
            .order_by("id")
        )
        for row in rows:
            if row.state != "erased":
                row.state = "cleanup"
                row.available_at = now
                row.unowned_cleanup_due_at = max(now, row.cleanup_not_before)
                row.lease_token, row.lease_until = None, None
                row.save(
                    update_fields=[
                        "state",
                        "available_at",
                        "unowned_cleanup_due_at",
                        "lease_token",
                        "lease_until",
                    ]
                )
    storage = _storage(asset, "erase") if rows else None
    complete = True
    for row in rows:
        with _scratch_lock(row) as work:
            complete = _settle(row, storage, work) and complete
    return complete


def pending(*, limit=20):
    now = timezone.now()
    return list(
        AttachmentRecordingDerivative.objects.filter(available_at__lte=now)
        .filter(
            Q(state__in=["queued", "recovering", "writing"], scope__enabled=True)
            | Q(state__in=["cleanup", "failed"])
            | Q(state="ready", unowned_cleanup_due_at__lte=now)
        )
        .order_by("available_at", "id")
        .values_list("id", "scope_id")[:limit]
    )


def defer(derivative_id):
    AttachmentRecordingDerivative.objects.filter(pk=derivative_id).update(
        available_at=timezone.now() + timedelta(seconds=60)
    )


def purge(*, limit=1000):
    """Retire work eligibility, never locator rows or unproven physical objects."""
    now = timezone.now()
    candidates = list(
        AttachmentRecordingDerivative.objects.filter(
            state__in=["queued", "recovering", "writing"]
        )
        .filter(
            Q(attachment__deleted_at__isnull=False)
            | Q(reservation_expires_at__lte=now)
            | Q(write_attempts__gte=MAX_WRITES, lease_until__lte=now)
        )
        .order_by("available_at", "id")
        .values_list("id", "scope_id")[:limit]
    )
    count = 0
    for derivative_id, scope_id in candidates:
        with _locked(derivative_id, scope_id) as (_, asset, row):
            if row.state not in {"queued", "recovering", "writing"}:
                continue
            if not (
                asset.deleted_at
                or row.reservation_expires_at <= now
                or (row.write_attempts >= MAX_WRITES and row.lease_until <= now)
            ):
                continue
            row.state, row.available_at = "cleanup", now
            row.unowned_cleanup_due_at = max(now, row.cleanup_not_before)
            row.lease_token, row.lease_until = None, None
            row.save(
                update_fields=[
                    "state",
                    "available_at",
                    "unowned_cleanup_due_at",
                    "lease_token",
                    "lease_until",
                ]
            )
            count += 1
    return count
