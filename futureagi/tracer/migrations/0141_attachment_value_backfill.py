"""Resumable value-copy and evidence-bound provenance backfill (one release).

Run only after admission/old workers are quiesced and accepted writes have a
recorded termination inventory. This migration NEVER reads storage, source URLs,
credentials, current defaults, or current service code. Unknown provenance is
left NULL with cleanup pending; 0142 refuses activation until storage ownership
and capacity reconciliation are complete. Unprovable pending sources terminalize
without fetching or publishing. Times not recorded historically are not invented.

The qualification/deployment owner supplies an in-memory settings mapping named
FI_ATTACHMENTS_SCHEMA_V1_EVIDENCE, not a secret/environment discovery mechanism.
The mapping has schema_version=1, evidence, history (applied_tracer_migrations,
file_sha256), quiescence (old_writers_stopped, accepted_writes_terminated,
sealed_backup_evidence), bindings [{identity: complete binding fields, evidence}],
owners {ModelName: {UUID: typed per-owner evidence}}, and budget_before {key: six
integer vector}. Per-owner entries carry evidence, binding_id where applicable,
storage {serving/quarantine: {key, version}}, cleanup_not_before, capacity (six
named vector components), capacity_evidence, and cleanup_complete. Source evidence
has a separate source_evidence and typed source fields. This is installation
provenance input, not arbitrary model updates or an authority payload. Entries
must be prepared from sealed original records/provider evidence and reviewed;
current defaults/currently observed generations are expressly inadmissible.

0140 remains reversible expansion. This data copy and the eventual contraction
are not a lossy reverse-migration path: rollback uses the qualified sealed restore.
"""

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from django.conf import settings
from django.db import migrations, transaction
from django.utils import timezone

# Deliberately invalid until a real human review supplies these two values.
# Empty references must fail the repository's UPDATE_SAFETY gate.
UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": [
        "postgres:django_migrations",
        "postgres:tracer_attachmentscope",
        "postgres:tracer_attachmentstoragebinding",
        "postgres:tracer_attachment",
        "postgres:tracer_attachmentblob",
        "postgres:tracer_attachmentupload",
        "postgres:tracer_uploadreservation",
        "postgres:tracer_attachmentusagereceipt",
        "postgres:tracer_attachmentoutbox",
        "postgres:tracer_attachmentauditevent",
        "postgres:tracer_attachmentbudget",
        "postgres:tracer_capturesession",
        "postgres:tracer_attachmentrecording",
        "postgres:tracer_attachmentimportjob",
        "postgres:tracer_attachmentrecordingsegment",
        "postgres:tracer_attachmentrecordingderivative",
        "postgres:tracer_attachmentsourceenvelope",
        "postgres:tracer_attachmentproviderrecording",
        "postgres:tracer_attachmentproviderconnection",
        "postgres:tracer_attachmentoccurrence",
    ],
    "mitigation": "Quiesce all old writers and accepted operations, preserve sealed backup and applied-history fingerprints, copy exact IDs/times in resumable 250-row batches, reject unproven provenance, reconcile rather than clamp counters, then qualify populated interruption and cold restore before activation.",
    "reviewer": "",
    "review": "",
    "large_volume_rehearsal": True,
}

BATCH = 250
HISTORICAL_FILES = {
    "0136_attachment_control_records": "9632b3082a904290f4a413d36b73cb9e2ae1144e77e36c97745a0994645a5c02",
    "0137_attachment_fences": "78a7069c6c7ba315a8369df5df7b513b5c0aeee3c04be1f83290c0eedf7188c7",
    "0138_attachment_binding_claims": "2b21dbc43a56ec9b42d3fd0daf0e9ce14cc4418745bfa51f574812f1ed42232e",
    "0139_attachment_recordings": "f8dde92abd8f54f0a6345cea8fc2e0ef8b7cfe2b6eab5bf77cee60a5a2e1890a",
}
VECTOR = (
    "charge_bytes",
    "transfers",
    "validation_jobs",
    "recordings",
    "objects",
    "links",
)
COUNTERS = (
    "reserved_bytes",
    "active_transfers",
    "validation_jobs",
    "active_recordings",
    "object_count",
    "link_count",
)
TERMINAL = ("ready", "failed", "rejected", "expired", "deleted")
SOURCE_FIELDS = (
    "source_generation_kind",
    "source_generation",
    "source_byte_length",
    "source_mime_type",
    "source_duration_ms",
    "source_expires_at",
    "source_authority_generation",
    "source_creator_kind",
    "source_creator_id",
    "source_trace_id",
    "source_span_id",
    "source_direction",
)
BINDING_FIELDS = (
    "provider",
    "endpoint",
    "account_identity",
    "region",
    "quarantine_bucket",
    "quarantine_resource_identity",
    "quarantine_owner_identity",
    "quarantine_created_at",
    "quarantine_encryption_mode",
    "quarantine_encryption_key_identity",
    "serving_bucket",
    "serving_resource_identity",
    "serving_owner_identity",
    "serving_created_at",
    "serving_encryption_mode",
    "serving_encryption_key_identity",
)


def fail(message):
    # Content-free: no UUID, locator, signed query or input value in errors.
    raise RuntimeError("attachment schema provenance: " + message)


def stamp(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        fail("aware timestamp evidence required")
    return value


def evidence_ref(value):
    if not isinstance(value, str) or not value.strip():
        fail("explicit evidence reference required")
    return value


def identity_digest(values):
    parts = []
    for field in BINDING_FIELDS:
        value = values[field]
        if field.endswith("created_at"):
            value = (
                stamp(value)
                .astimezone(UTC)
                .isoformat(timespec="microseconds")
                .replace("+00:00", "Z")
            )
        if not isinstance(value, str) or "\n" in value or "\r" in value:
            fail("invalid resource identity")
        parts.append(str(len(value.encode("utf-8"))) + ":" + value)
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def db(apps, name, alias):
    return apps.get_model("tracer", name).objects.using(alias)


def batches(manager):
    cursor = None
    while True:
        query = manager.order_by("id")
        if cursor is not None:
            query = query.filter(id__gt=cursor)
        rows = list(query[:BATCH])
        if not rows:
            return
        yield rows
        cursor = rows[-1].id


def has_owners(apps, alias):
    return any(
        db(apps, name, alias).exists()
        for name in (
            "Attachment",
            "AttachmentUpload",
            "AttachmentImportJob",
            "AttachmentRecordingDerivative",
            "AttachmentSourceEnvelope",
            "AttachmentProviderRecording",
            "AttachmentProviderConnection",
            "AttachmentScope",
            "CaptureSession",
            "AttachmentRecording",
        )
    )


def provenance(apps, connection):
    value = getattr(settings, "FI_ATTACHMENTS_SCHEMA_V1_EVIDENCE", None)
    if value is None:
        if has_owners(apps, connection.alias):
            fail(
                "populated install requires reviewed inventory and quiescence evidence"
            )
        return {"bindings": [], "owners": {}, "budget_before": {}}
    if (
        not isinstance(value, dict)
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
    ):
        fail("unsupported inventory format")
    evidence_ref(value.get("evidence"))
    q = value.get("quiescence", {})
    if (
        q.get("old_writers_stopped") is not True
        or q.get("accepted_writes_terminated") is not True
    ):
        fail("old writers/accepted operations have not been proven stopped")
    evidence_ref(q.get("sealed_backup_evidence"))
    history = value.get("history", {})
    if history.get("file_sha256") != HISTORICAL_FILES:
        fail(
            "historical source fingerprints differ; reviewed compatibility mapping required"
        )
    # This verifies the actual ledger, not just a source-directory leaf.
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT name FROM django_migrations WHERE app = %s ORDER BY name",
            ["tracer"],
        )
        actual = [
            r[0]
            for r in cursor.fetchall()
            if not r[0].startswith(("0140_", "0141_", "0142_", "0143_"))
        ]
    if actual != sorted(history.get("applied_tracer_migrations", [])):
        fail("applied migration inventory differs")
    for name, digest in HISTORICAL_FILES.items():
        if (
            hashlib.sha256(
                Path(__file__).with_name(name + ".py").read_bytes()
            ).hexdigest()
            != digest
        ):
            fail("historical migration file changed")
    return value


def owner_map(data, name, pk):
    item = data.get("owners", {}).get(name, {}).get(str(pk))
    if item is not None:
        if not isinstance(item, dict):
            fail("owner mapping must be typed")
        evidence_ref(item.get("evidence"))
    return item


def exact_existing(row, values):
    """Idempotence never overwrites a different already-populated value."""
    for name, value in values.items():
        old = getattr(row, name)
        field = row._meta.get_field(
            name.removesuffix("_id")
            if name.endswith("_id")
            and name.removesuffix("_id")
            in {f.name for f in row._meta.fields if f.is_relation}
            else name
        )
        value = (
            field.target_field.to_python(value)
            if field.is_relation
            else field.to_python(value)
        )
        if old is not None and old != value:
            fail("existing copied value disagrees with evidence")
        values[name] = value


def put_values(manager, row, values, *, strict=True):
    if values.get("unowned_cleanup_due_at") and row.unowned_cleanup_due_at is not None:
        values["unowned_cleanup_due_at"] = row.unowned_cleanup_due_at
    if strict:
        exact_existing(row, values)
    manager.filter(pk=row.pk).update(**values)


def storage_proof(item, purpose, key, version):
    observed = item.get("storage", {}).get(purpose, {})
    if observed != {"key": key, "version": version}:
        fail("exact historical object key/version evidence mismatch")


def binding_id(item, bindings):
    value = UUID(str(item.get("binding_id")))
    if value not in bindings:
        fail("owner references unverified resource binding")
    return value


def live_vector(item):
    evidence_ref(item.get("capacity_evidence"))
    values = item.get("capacity", {})
    if set(values) != set(VECTOR) or any(
        type(values[k]) is not int or values[k] < 0 for k in VECTOR
    ):
        fail("complete nonnegative reconciled capacity vector required")
    return {"reservation_" + k: values[k] for k in VECTOR}


def cleanup_values(item, *, minimum):
    horizon = stamp(item.get("cleanup_not_before"))
    if horizon < minimum:
        fail("cleanup horizon precedes historically accepted operation")
    values = {"cleanup_not_before": horizon}
    if item.get("cleanup_complete") is True:
        evidence_ref(item.get("cleanup_evidence"))
        checked = stamp(item.get("cleanup_checked_at"))
        if checked < horizon or checked > timezone.now():
            fail("cleanup completion not proven beyond write horizon")
        values["cleanup_checked_at"] = checked
    else:
        values["unowned_cleanup_due_at"] = timezone.now()
    return values


def create_bindings(apps, alias, data):
    manager = db(apps, "AttachmentStorageBinding", alias)
    result = set()
    for item in data.get("bindings", []):
        evidence_ref(item.get("evidence"))
        facts = dict(item.get("identity", {}))
        if set(facts) != set(BINDING_FIELDS) | {"id", "identity_sha256"}:
            fail("complete immutable binding tuple required")
        from urllib.parse import urlsplit

        endpoint = urlsplit(facts["endpoint"])
        if (
            facts["provider"] not in ("s3", "gcs", "minio")
            or endpoint.scheme != "https"
            or not endpoint.hostname
            or endpoint.username
            or endpoint.password
            or endpoint.path
            or endpoint.query
            or endpoint.fragment
            or endpoint.port == 443
            or facts["endpoint"] != "https://" + endpoint.netloc.lower()
            or facts["quarantine_bucket"] == facts["serving_bucket"]
            or facts["quarantine_resource_identity"]
            == facts["serving_resource_identity"]
        ):
            fail("canonical distinct private resource identity required")
        for field in BINDING_FIELDS:
            if not field.endswith("encryption_key_identity") and not facts[field]:
                fail("complete verified resource identity required")
        if facts["identity_sha256"] != identity_digest(facts):
            fail("binding identity digest mismatch")
        for name in ("quarantine_created_at", "serving_created_at"):
            facts[name] = stamp(facts[name])
        key = UUID(str(facts.pop("id")))
        row, created = manager.get_or_create(pk=key, defaults=facts)
        # Digest equality alone never substitutes for complete tuple equality.
        if not created and any(getattr(row, k) != v for k, v in facts.items()):
            fail("binding identity tuple differs")
        result.add(key)
    return result


def copy_assets(apps, alias, data, bindings):
    assets = db(apps, "Attachment", alias)
    for batch in batches(assets):
        with transaction.atomic(using=alias):
            for asset in batch:
                blob = (
                    db(apps, "AttachmentBlob", alias)
                    .filter(attachment_id=asset.id)
                    .first()
                )
                values = {}
                if blob:
                    if blob.scope_id != asset.scope_id:
                        fail("blob scope mismatch")
                    values = {
                        "verified_blob_id": blob.id,
                        "verified_blob_created_at": blob.created_at,
                        "verified_storage_key": blob.storage_key,
                        "verified_storage_version": blob.storage_version,
                        "verified_sha256": blob.sha256,
                        "verified_byte_length": blob.byte_length,
                        "verified_mime_type": blob.mime_type,
                        "verified_at": blob.validated_at,
                        "verified_sample_rate": blob.sample_rate,
                        "verified_channels": blob.channels,
                        "verified_frame_count": blob.frame_count,
                    }
                item = owner_map(data, "Attachment", asset.id)
                if item:
                    if blob:
                        storage_proof(
                            item, "serving", blob.storage_key, blob.storage_version
                        )
                    values["storage_binding_id"] = binding_id(item, bindings)
                put_values(assets, asset, values)


def audit_event(apps, alias, upload, event, event_kind, reason, now):
    audits = db(apps, "AttachmentAuditEvent", alias)
    values = {
        "actor_id": None,
        "entity_id": upload.id,
        "event": event_kind,
        "reason": reason,
    }
    existing, created = audits.get_or_create(
        scope_id=upload.scope_id, event_id=event.id, defaults=values
    )
    if not created and any(getattr(existing, k) != v for k, v in values.items()):
        fail("historical audit identity/outcome conflict")
    db(apps, "AttachmentOutbox", alias).filter(
        pk=event.id, processed_at__isnull=True
    ).update(processed_at=now)


def copy_uploads(apps, alias, data):
    manager = db(apps, "AttachmentUpload", alias)
    for batch in batches(manager):
        with transaction.atomic(using=alias):
            for candidate in batch:
                # Preserve the required Upload-before-Outbox order even while quiesced.
                row = manager.select_for_update().get(pk=candidate.pk)
                events = list(
                    db(apps, "AttachmentOutbox", alias)
                    .select_for_update()
                    .filter(upload_id=row.id, event__in=["admitted", "settled"])
                    .order_by("created_at", "id")
                )
                reservation = db(apps, "UploadReservation", alias).get(upload_id=row.id)
                receipt = (
                    db(apps, "AttachmentUsageReceipt", alias)
                    .filter(upload_id=row.id)
                    .first()
                )
                if reservation.scope_id != row.scope_id or (
                    receipt and receipt.scope_id != row.scope_id
                ):
                    fail("upload accounting scope mismatch")
                admitted = [e for e in events if e.event == "admitted"]
                settled = [e for e in events if e.event == "settled"]
                if len(admitted) != 1 or len(settled) > 1:
                    fail(
                        "ambiguous historical accounting events require reviewed compatibility mapping"
                    )
                values = {
                    "reservation_id": reservation.id,
                    "reservation_created_at": reservation.created_at,
                    "reservation_expires_at": reservation.expires_at,
                    "reservation_pool_key": "global:" + str(row.id.int % 16),
                    "reservation_reserved_bytes": reservation.reserved_bytes,
                    "reservation_decoded_bytes": reservation.decoded_bytes,
                    "reservation_object_count": reservation.object_count,
                    "reservation_part_count": reservation.part_count,
                    "reservation_settled_at": reservation.settled_at,
                    "storage_object_id": row.id,
                    "serving_key": "original/" + str(row.id),
                    "admission_event_id": admitted[0].id,
                }
                asset = db(apps, "Attachment", alias).get(pk=row.attachment_id)
                if asset.verified_storage_version:
                    if asset.verified_storage_key != values["serving_key"]:
                        fail("historical upload serving key is not the proven original")
                    values["serving_version"] = asset.verified_storage_version
                now = timezone.now()
                if receipt:
                    values.update(
                        usage_id=receipt.id,
                        usage_created_at=receipt.created_at,
                        usage_outcome=receipt.outcome,
                        usage_stored_bytes=receipt.stored_bytes,
                    )
                elif settled:
                    # Recover READY from exact immutable facts, not later DELETED state.
                    audit = (
                        db(apps, "AttachmentAuditEvent", alias)
                        .filter(
                            scope_id=row.scope_id,
                            event_id=settled[0].id,
                            entity_id=row.id,
                            event="settlement",
                            actor_id__isnull=True,
                        )
                        .first()
                    )
                    item = owner_map(data, "AttachmentUpload", row.id)
                    if asset.verified_blob_id:
                        outcome = "ready"
                    elif audit:
                        outcome = audit.reason
                    elif item and item.get("terminal_outcome"):
                        evidence_ref(item.get("terminal_evidence"))
                        outcome = item["terminal_outcome"]
                    elif row.state in ("failed", "rejected", "expired"):
                        # Under the historical state fence these states cannot
                        # have followed another terminal outcome (only DELETED
                        # can). They are recoverable original terminal evidence.
                        outcome = row.state
                    else:
                        fail(
                            "deleted missing receipt requires accepted terminal outcome evidence"
                        )
                    if outcome not in TERMINAL or (
                        outcome == "ready" and not asset.verified_blob_id
                    ):
                        fail("missing terminal receipt has no recoverable outcome")
                    values.update(
                        usage_id=row.usage_id or uuid4(),
                        usage_created_at=row.usage_created_at or now,
                        usage_outcome=outcome,
                        usage_stored_bytes=asset.verified_byte_length or 0,
                    )
                if settled:
                    values["settlement_event_id"] = settled[0].id
                item = owner_map(data, "AttachmentUpload", row.id)
                if item:
                    if (
                        item.get("producer_kind", "authenticated_stream")
                        != "authenticated_stream"
                    ):
                        fail(
                            "legacy import uploads require an explicit reviewed compatibility migration"
                        )
                    storage_proof(
                        item,
                        "quarantine",
                        row.quarantine_key or "quarantine/" + str(row.id),
                        row.quarantine_version or None,
                    )
                    storage_proof(
                        item,
                        "serving",
                        values["serving_key"],
                        values.get("serving_version"),
                    )
                    values.update(live_vector(item))
                    values.update(
                        cleanup_values(
                            item, minimum=row.expires_at + timedelta(seconds=15)
                        )
                    )
                    if (
                        reservation.settled_at
                        and item.get("cleanup_complete") is not True
                    ):
                        fail("historical released upload lacks erasure reconciliation")
                    if (
                        not reservation.settled_at
                        and item.get("cleanup_complete") is not True
                    ):
                        minimum = (
                            3 * row.expected_bytes + reservation.decoded_bytes,
                            1,
                            1,
                            0,
                            4,
                            1,
                        )
                        if any(
                            values["reservation_" + k] < v
                            for k, v in zip(VECTOR, minimum, strict=True)
                        ):
                            fail("upload live vector does not cover bounded peak")
                # Optional absence is NULL. The old snapshot trigger permits only
                # the empty-to-null conversion, never changing a recorded version.
                manager.filter(pk=row.pk, quarantine_version="").update(
                    quarantine_version=None
                )
                manager.filter(pk=row.pk, quarantine_sha256="").update(
                    quarantine_sha256=None
                )
                if not row.quarantine_key:
                    manager.filter(pk=row.pk).update(
                        quarantine_key="quarantine/" + str(row.id)
                    )
                put_values(manager, row, values)
                audit_event(apps, alias, row, admitted[0], "admission", "accepted", now)
                if settled:
                    audit_event(
                        apps,
                        alias,
                        row,
                        settled[0],
                        "settlement",
                        values["usage_outcome"],
                        now,
                    )


def source_values(item):
    evidence_ref(item.get("source_evidence"))
    values = dict(item.get("source", {}))
    if set(values) != set(SOURCE_FIELDS):
        fail("complete pinned source evidence required")
    if (
        values["source_generation_kind"]
        not in ("s3_version", "gcs_generation", "http_strong_etag")
        or not values["source_generation"]
        or values["source_generation"] == "null"
    ):
        fail("immutable source generation required")
    if values["source_generation_kind"] == "http_strong_etag" and (
        values["source_generation"].startswith("W/")
        or not values["source_generation"].startswith('"')
        or not values["source_generation"].endswith('"')
    ):
        fail("strong HTTP source generation required")
    values["source_expires_at"] = stamp(values["source_expires_at"])
    if (
        type(values["source_authority_generation"]) is not int
        or values["source_authority_generation"] < 0
    ):
        fail("source authority epoch required")
    if (
        not isinstance(values["source_mime_type"], str)
        or not values["source_mime_type"]
        or len(values["source_mime_type"]) > 64
    ):
        fail("trusted source MIME required")
    if values["source_duration_ms"] is not None and (
        type(values["source_duration_ms"]) is not int
        or values["source_duration_ms"] <= 0
    ):
        fail("trusted positive source duration required")
    if (
        type(values["source_byte_length"]) is not int
        or values["source_byte_length"] <= 0
    ):
        fail("trusted source byte length required")
    if values["source_creator_kind"] not in ("user", "api_key") or values[
        "source_direction"
    ] not in ("input", "output", "mixed"):
        fail("source creator/ancestry evidence invalid")
    for name in ("source_creator_id", "source_trace_id"):
        values[name] = UUID(str(values[name]))
    return values


def copy_sources(apps, alias, data):
    manager = db(apps, "AttachmentSourceEnvelope", alias)
    for batch in batches(manager):
        with transaction.atomic(using=alias):
            for row in batch:
                item = owner_map(data, "AttachmentSourceEnvelope", row.id)
                if not item:
                    # Existing sealed bytes are retained until ordinary expiry;
                    # missing generation cannot authorize a new import.
                    continue
                facts = source_values(item)
                if (
                    facts["source_creator_kind"],
                    facts["source_creator_id"],
                    facts["source_direction"],
                    facts["source_byte_length"],
                ) != (
                    row.creator_kind,
                    row.creator_id,
                    row.direction,
                    row.source_bytes,
                ):
                    fail("source envelope creator/bytes differ")
                values = {
                    k.removeprefix("source_"): facts[k]
                    for k in (
                        "source_generation_kind",
                        "source_generation",
                        "source_mime_type",
                        "source_duration_ms",
                        "source_authority_generation",
                        "source_trace_id",
                        "source_span_id",
                    )
                }
                values["source_expires_at"] = facts["source_expires_at"]
                put_values(manager, row, values)
    manager = db(apps, "AttachmentProviderRecording", alias)
    for batch in batches(manager):
        with transaction.atomic(using=alias):
            for row in batch:
                item = owner_map(data, "AttachmentProviderRecording", row.id)
                if not item:
                    continue
                facts = source_values(item)
                if (
                    facts["source_creator_kind"],
                    facts["source_creator_id"],
                    facts["source_trace_id"],
                    facts["source_span_id"],
                    facts["source_direction"],
                    facts["source_byte_length"],
                    facts["source_duration_ms"],
                ) != (
                    row.registrar_kind,
                    row.registrar_id,
                    row.trace_id,
                    row.span_id,
                    row.direction,
                    row.output_bytes,
                    row.output_duration_ms,
                ):
                    fail("provider registered source evidence differs")
                values = {
                    k: facts[k]
                    for k in (
                        "source_generation_kind",
                        "source_generation",
                        "source_mime_type",
                        "source_expires_at",
                        "source_authority_generation",
                    )
                }
                if item.get("output_envelope_id"):
                    values["output_envelope_id"] = UUID(item["output_envelope_id"])
                # Only queue rows that satisfy the complete dispatch fence.
                # Incomplete historical evidence remains non-runnable and can
                # be retried after the sealed inventory is corrected.
                if (
                    row.provider_state == "complete"
                    and row.import_job_id is None
                    and row.dispatch_due_at is None
                    and values.get("output_envelope_id") is not None
                    and values.get("source_generation") not in (None, "", "null")
                    and values.get("source_generation_kind")
                    and values.get("source_mime_type")
                    and values.get("source_expires_at") is not None
                    and values.get("source_authority_generation") is not None
                    and row.output_bytes is not None
                    and row.output_bytes > 0
                    and row.output_duration_ms is not None
                    and row.output_duration_ms > 0
                ):
                    values["dispatch_due_at"] = timezone.now()
                put_values(manager, row, values)


def copy_jobs(apps, alias, data, bindings):
    manager = db(apps, "AttachmentImportJob", alias)
    for batch in batches(manager):
        with transaction.atomic(using=alias):
            for row in batch:
                recording = db(apps, "AttachmentRecording", alias).get(
                    pk=row.recording_id, scope_id=row.scope_id
                )
                session = db(apps, "CaptureSession", alias).get(
                    pk=recording.capture_session_id, scope_id=row.scope_id
                )
                if (row.importer_kind, row.importer_id) != (
                    session.writer_kind,
                    session.writer_id,
                ):
                    fail("historical import creator/session mismatch")
                values = {
                    "capture_session_id": session.id,
                    "scratch_id": row.id,
                    "reservation_id": row.reservation_id or uuid4(),
                    "reservation_created_at": row.created_at,
                    "reservation_expires_at": row.reservation_expires_at
                    or row.deadline,
                    "reservation_pool_key": "global:imports",
                    "reservation_reserved_bytes": row.reserved_bytes,
                    "reservation_decoded_bytes": row.window_bytes,
                    "reservation_object_count": row.reserved_segments + 2,
                    "reservation_part_count": row.reserved_segments,
                    "reservation_settled_at": row.released_at,
                }
                item = owner_map(data, "AttachmentImportJob", row.id)
                if item:
                    values["storage_binding_id"] = binding_id(item, bindings)
                    values.update(live_vector(item))
                    cleanup = cleanup_values(
                        item,
                        minimum=max(row.deadline, row.lease_until or row.deadline)
                        + timedelta(seconds=15),
                    )
                    cleanup.pop(
                        "unowned_cleanup_due_at", None
                    )  # jobs are selected by deadline/state
                    values.update(cleanup)
                    if row.released_at and item.get("cleanup_complete") is not True:
                        fail("historical released import lacks cleanup reconciliation")
                    if not row.released_at and item.get("cleanup_complete") is not True:
                        segments = db(apps, "AttachmentRecordingSegment", alias).filter(
                            recording_id=row.recording_id
                        )
                        lengths = list(segments.values_list("byte_length", flat=True))
                        minimum = (
                            row.declared_bytes + row.window_bytes + 3 * sum(lengths),
                            1,
                            1,
                            1,
                            2 + 3 * len(lengths),
                            1 + len(lengths),
                        )
                        if any(
                            values["reservation_" + k] < v
                            for k, v in zip(VECTOR, minimum, strict=True)
                        ):
                            fail(
                                "import live vector below conservative source/scratch/segment peak"
                            )
                facts = source_values(item) if item and item.get("source") else None
                if facts and row.recording_id is not None and facts["source_duration_ms"] is None:
                    fail("recording import requires evidenced positive source duration")
                if facts:
                    if (
                        facts["source_creator_kind"],
                        facts["source_creator_id"],
                        facts["source_trace_id"],
                        facts["source_span_id"],
                        facts["source_direction"],
                    ) != (
                        row.importer_kind,
                        row.importer_id,
                        recording.trace_id,
                        recording.span_id,
                        recording.direction,
                    ):
                        fail("pinned import source ancestry mismatch")
                    values.update(facts)
                    # Deadline can shrink but never grow to today's source expiry.
                    values["deadline"] = min(
                        row.deadline,
                        facts["source_expires_at"],
                        session.expires_at,
                        row.created_at + timedelta(hours=3),
                    )
                elif row.state not in TERMINAL:
                    # A newly observed source generation is NOT a backfill.
                    manager.filter(pk=row.pk).update(
                        state="failed",
                        reason="source_unavailable",
                        finished_at=timezone.now(),
                    )
                    db(apps, "AttachmentRecording", alias).filter(
                        pk=recording.pk, state__in=["importing", "validating"]
                    ).update(state="failed")
                # Quiesced old leases cannot survive into token+epoch writers.
                manager.filter(pk=row.pk).update(
                    lease_token=None,
                    lease_until=None,
                    lease_phase="terminal"
                    if row.state in TERMINAL or facts is None
                    else "idle",
                )
                deadline = values.pop("deadline", None)
                put_values(manager, row, values)
                if deadline:
                    manager.filter(pk=row.pk).update(deadline=deadline)


def copy_segments(apps, alias, data, bindings):
    manager = db(apps, "AttachmentRecordingSegment", alias)
    for batch in batches(manager):
        with transaction.atomic(using=alias):
            for row in batch:
                job = db(apps, "AttachmentImportJob", alias).get(
                    recording_id=row.recording_id, scope_id=row.scope_id
                )
                values = {"import_job_id": job.id}
                item = owner_map(data, "AttachmentRecordingSegment", row.id)
                if item:
                    object_id = UUID(str(item.get("storage_object_id")))
                    cleanup_key = "original/" + str(object_id)
                    if object_id != row.attachment_client_id:
                        fail("historical segment object identity differs")
                    if row.storage_key == "":
                        # The frozen legacy allocator persisted this server UUID
                        # before PUT; a crash could leave BOTH locator fields blank.
                        # Reviewed evidence must name that exact cleanup key, never
                        # an observed generation. The live job remains charged.
                        if (
                            row.storage_version not in ("", None)
                            or row.state != "staged"
                            or item.get("cleanup_complete") is True
                            or job.reservation_settled_at is not None
                            or not job.reservation_charge_bytes
                            or not job.reservation_objects
                        ):
                            fail(
                                "unrecorded legacy allocation requires retained cleanup charge"
                            )
                    elif row.storage_key != cleanup_key:
                        fail("historical segment object identity differs")
                    storage_proof(
                        item, "serving", cleanup_key, row.storage_version or None
                    )
                    values.update(
                        storage_object_id=object_id,
                        quarantine_key="quarantine/" + str(object_id),
                        storage_binding_id=binding_id(item, bindings),
                    )
                    if values["storage_binding_id"] != job.storage_binding_id:
                        fail("segment and job binding differ")
                    values.update(
                        cleanup_values(
                            item, minimum=job.deadline + timedelta(seconds=15)
                        )
                    )
                    if row.state == "erased":
                        # Old erased rows had no erased_at. Evidence must supply an
                        # actually recorded timestamp or record recovery now after proof.
                        if item.get("cleanup_complete") is not True:
                            fail("erased legacy allocation lacks cleanup proof")
                        values["erased_at"] = row.erased_at or timezone.now()
                asset = (
                    db(apps, "Attachment", alias)
                    .filter(scope_id=row.scope_id, client_id=row.attachment_client_id)
                    .first()
                )
                if asset:
                    if (
                        asset.verified_storage_key,
                        asset.verified_storage_version,
                        asset.verified_sha256,
                        asset.verified_byte_length,
                    ) != (
                        row.storage_key,
                        row.storage_version,
                        row.sha256,
                        row.byte_length,
                    ):
                        fail("published segment facts differ from asset")
                    values["attachment_id"] = asset.id
                elif row.state == "published":
                    fail("published segment has no historical asset")
                # Validate all ownership/evidence first, then fill only the
                # historically blank key. Never rewrite a recorded key/version.
                # A batch retry sees the same exact key and NULL version.
                if item and row.storage_key == "":
                    manager.filter(pk=row.pk, storage_key="").update(
                        storage_key=cleanup_key
                    )
                manager.filter(pk=row.pk, storage_version="").update(
                    storage_version=None
                )
                put_values(manager, row, values)


def copy_derivatives(apps, alias, data):
    manager = db(apps, "AttachmentRecordingDerivative", alias)
    for batch in batches(manager):
        with transaction.atomic(using=alias):
            for row in batch:
                segment = db(apps, "AttachmentRecordingSegment", alias).get(
                    pk=row.segment_id, scope_id=row.scope_id
                )
                if segment.attachment_id is None:
                    fail("historical derivative lacks a proven asset")
                values = {"attachment_id": segment.attachment_id}
                item = owner_map(data, "AttachmentRecordingDerivative", row.id)
                if item:
                    storage_proof(
                        item, "serving", row.storage_key, row.storage_version or None
                    )
                    object_id = UUID(str(item.get("storage_object_id")))
                    if row.storage_key != "original/" + str(object_id):
                        fail("historical derivative key differs")
                    values.update(
                        storage_object_id=object_id,
                        quarantine_key="quarantine/" + str(object_id),
                    )
                    values.update(cleanup_values(item, minimum=row.created_at))
                    facts = item.get("verified", {})
                    if set(facts) != {
                        "mime_type",
                        "sample_rate",
                        "channels",
                        "frame_count",
                    }:
                        fail("derivative verified format evidence missing")
                    for field in ("sample_rate", "channels", "frame_count"):
                        if field in row.facts and facts[field] != row.facts[field]:
                            fail("historical derivative probe facts differ")
                    values.update(facts)
                    values.update(live_vector(item))
                    now = timezone.now()
                    values.update(
                        reservation_id=row.reservation_id or uuid4(),
                        reservation_created_at=row.reservation_created_at or now,
                        reservation_expires_at=row.reservation_expires_at or now,
                        reservation_pool_key="global:derivatives",
                        reservation_reserved_bytes=row.byte_length,
                        reservation_decoded_bytes=item.get("decoded_bytes", 0),
                        reservation_object_count=0,
                        reservation_part_count=1,
                        available_at=row.available_at or now,
                    )
                    if item.get("cleanup_complete") is True:
                        # New recovery ledger, actual reconciliation time. Never
                        # mislabel published_at as an old capacity settlement.
                        values["reservation_settled_at"] = (
                            row.reservation_settled_at or now
                        )
                put_values(manager, row, values)
                manager.filter(pk=row.pk).update(
                    state="ready" if row.published_at else "cleanup"
                )


def totals(apps, alias):
    result = {}
    for name in (
        "AttachmentUpload",
        "AttachmentImportJob",
        "AttachmentRecordingDerivative",
    ):
        manager = db(apps, name, alias)
        fields = [
            "scope__organization_id",
            "scope__project_id",
            "reservation_pool_key",
        ] + ["reservation_" + k for k in VECTOR]
        for row in (
            manager.filter(reservation_settled_at__isnull=True)
            .values_list(*fields)
            .iterator(chunk_size=BATCH)
        ):
            org, project, pool, *vector = row
            if pool is None or any(v is None for v in vector):
                fail("unresolved live capacity owner blocks counter switch")
            for key in (pool, "org:" + str(org), "project:" + str(project)):
                current = result.setdefault(key, [0] * 6)
                for i, value in enumerate(vector):
                    current[i] += value
    return result


def pool_order(key):
    if key.startswith("global:") and key[7:].isdigit():
        return (0, int(key[7:]))
    if key in ("global:imports", "global:derivatives"):
        return (1 if key == "global:imports" else 2, "")
    return (3 if key.startswith("org:") else 4, key)


def reconcile_budgets(apps, alias, data):
    expected = totals(apps, alias)
    manager = db(apps, "AttachmentBudget", alias)
    before = data.get("budget_before", {})
    keys = set(manager.values_list("key", flat=True)) | set(expected)
    with transaction.atomic(using=alias):
        for key in sorted(keys, key=pool_order):
            row, created = manager.select_for_update().get_or_create(
                key=key, defaults={"window_started_at": timezone.now()}
            )
            actual = [getattr(row, k) for k in COUNTERS]
            desired = expected.get(key, [0] * 6)
            if not created and actual != before.get(key) and actual != desired:
                fail("budget changed since sealed reconciliation inventory")
            # No saturation or cap-based truncation. Over-cap history stays
            # charged and naturally blocks new admissions until it drains.
            manager.filter(pk=key).update(**dict(zip(COUNTERS, desired, strict=True)))


def forwards(apps, schema_editor):
    connection = schema_editor.connection
    alias = connection.alias
    data = provenance(apps, connection)
    bindings = create_bindings(apps, alias, data)
    copy_assets(apps, alias, data, bindings)
    copy_uploads(apps, alias, data)
    copy_sources(apps, alias, data)
    copy_jobs(apps, alias, data, bindings)
    copy_segments(apps, alias, data, bindings)
    copy_derivatives(apps, alias, data)
    reconcile_budgets(apps, alias, data)
    # Do not record 0141 as applied while a retryable owner mapping is absent.
    # The copy is idempotent, so corrected sealed evidence can be retried.
    if db(apps, "Attachment", alias).filter(storage_binding_id__isnull=True).exists():
        fail("unresolved historical asset owner evidence")


class Migration(migrations.Migration):
    atomic = False  # each bounded batch commits; retry checks exact prior values
    dependencies = [("tracer", "0140_attachment_value_expansion")]
    operations = [migrations.RunPython(forwards)]
