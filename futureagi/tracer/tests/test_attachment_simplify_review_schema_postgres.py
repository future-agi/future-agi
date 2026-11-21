"""Focused real-PostgreSQL qualification of the unpublished attachment drafts.

Run explicitly with --noconftest in an isolated Django process and the published
synthetic connection map. Every case creates a NEW uniquely named TEST database.
No application database tables, existing test history, triggers or policies are
changed. All attachment tables/constraints/triggers come from real 0136-0143;
only the five non-attachment FK parents are minimal schema fixtures. This is
NOT full application graph, native-storage, restore or human-review evidence.
"""

import importlib
import os
from contextlib import contextmanager
from datetime import timedelta
from uuid import uuid4

import django
import psycopg
import pytest

if os.environ.get("ATT_SCHEMA_REVIEW_ISOLATED") != "1":
    pytest.skip(
        "isolated PostgreSQL schema qualification; run with ATT_SCHEMA_REVIEW_ISOLATED=1",
        allow_module_level=True,
    )
from django.conf import settings
from django.db import IntegrityError, OperationalError, connections, models
from django.db.migrations.recorder import MigrationRecorder
from django.db.migrations.state import ModelState, ProjectState
from django.utils import timezone
from psycopg import sql

NAMES = (
    "0136_attachment_control_records",
    "0137_attachment_fences",
    "0138_attachment_binding_claims",
    "0139_attachment_recordings",
    "0140_attachment_value_expansion",
    "0141_attachment_value_backfill",
    "0142_attachment_value_fences",
    "0143_attachment_value_contraction",
)


@pytest.fixture(scope="session")
def isolated_settings():
    assert os.environ.get("ATT_SCHEMA_REVIEW_ISOLATED") == "1"
    assert not settings.configured, "run separately; never reconfigure an application"
    settings.configure(
        INSTALLED_APPS=[],
        AUTH_USER_MODEL="accounts.User",
        USE_TZ=True,
        SECRET_KEY="synthetic-schema-test-only",
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": "unused",
                "HOST": os.environ["PG_HOST"],
                "PORT": os.environ["PG_PORT"],
                "USER": os.environ["PG_USER"],
                "PASSWORD": os.environ["PG_PASSWORD"],
            }
        },
    )
    django.setup()


class Case:
    def __init__(self, connection):
        self.connection = connection
        self.state = ProjectState()
        self.modules = {
            name[:4]: importlib.import_module("tracer.migrations." + name)
            for name in NAMES
        }
        # Only unrelated parent models are reduced. Attachment schema is literal.
        for app, name, extra in [
            ("accounts", "User", []),
            ("accounts", "OrgApiKey", []),
            ("accounts", "Organization", []),
            ("accounts", "Workspace", [("organization_id", models.UUIDField())]),
            (
                "tracer",
                "Project",
                [
                    ("organization_id", models.UUIDField()),
                    ("workspace_id", models.UUIDField()),
                ],
            ),
        ]:
            self.state.add_model(
                ModelState(
                    app,
                    name,
                    [("id", models.UUIDField(primary_key=True)), *extra],
                )
            )
        with connection.schema_editor() as editor:
            for model in self.state.apps.get_models():
                editor.create_model(model)
        self.recorder = MigrationRecorder(connection)
        self.recorder.ensure_schema()
        for number in ("0136", "0137", "0138", "0139", "0140"):
            self.apply(number)

    def apply(self, number):
        module = self.modules[number]
        name = module.__name__.rsplit(".", 1)[-1]
        migration = module.Migration(name, "tracer")
        with self.connection.schema_editor(atomic=migration.atomic) as editor:
            self.state = migration.apply(self.state, editor)
        self.recorder.record_applied("tracer", name)

    def model(self, name, app="tracer"):
        return self.state.apps.get_model(app, name)

    def create(self, name, **kwargs):
        return self.model(name).objects.create(**kwargs)

    def update(self, row, **kwargs):
        self.model(row.__class__.__name__).objects.filter(pk=row.pk).update(**kwargs)
        row.refresh_from_db()

    def activate(self):
        for number in ("0141", "0142", "0143"):
            self.apply(number)

    def seed(self):
        now = timezone.now()
        org = uuid4()
        workspace = uuid4()
        project = uuid4()
        self.model("Organization", "accounts").objects.create(id=org)
        self.model("Workspace", "accounts").objects.create(
            id=workspace, organization_id=org
        )
        self.model("Project").objects.create(
            id=project, workspace_id=workspace, organization_id=org
        )
        scope = self.create(
            "AttachmentScope",
            project_id=project,
            workspace_id=workspace,
            organization_id=org,
            enabled=True,
        )
        session = self.create(
            "CaptureSession",
            scope=scope,
            client_id=uuid4(),
            writer_kind="user",
            writer_id=uuid4(),
            correlation_nonce=uuid4(),
            expires_at=now + timedelta(hours=2),
            request_fingerprint="1" * 64,
            policy_version=scope.policy_version,
            deletion_generation=scope.deletion_generation,
        )
        identity = {
            "provider": "s3",
            "endpoint": "https://storage.invalid",
            "account_identity": "synthetic",
            "region": "fixture",
            "quarantine_bucket": "fixture-quarantine",
            "serving_bucket": "fixture-serving",
            "quarantine_resource_identity": "fixture:quarantine",
            "serving_resource_identity": "fixture:serving",
            "quarantine_owner_identity": "fixture-owner",
            "serving_owner_identity": "fixture-owner",
            "quarantine_created_at": now,
            "serving_created_at": now,
            "quarantine_encryption_mode": "AES256",
            "serving_encryption_mode": "AES256",
            "quarantine_encryption_key_identity": "",
            "serving_encryption_key_identity": "",
        }
        identity["identity_sha256"] = self.modules["0141"].identity_digest(identity)
        binding = self.create("AttachmentStorageBinding", **identity)
        self.identity = dict(identity, id=str(binding.pk))
        self.scope, self.session, self.binding = scope, session, binding
        return self

    def inventory(self, owners, *, extra_tracer_migrations=()):
        settings.FI_ATTACHMENTS_SCHEMA_V1_EVIDENCE = {
            "schema_version": 1,
            "evidence": "synthetic reviewed-inventory fixture (not approval)",
            "history": {
                "file_sha256": self.modules["0141"].HISTORICAL_FILES,
                "applied_tracer_migrations": [*NAMES[:4], *extra_tracer_migrations],
            },
            "quiescence": {
                "old_writers_stopped": True,
                "accepted_writes_terminated": True,
                "sealed_backup_evidence": "synthetic fixture only; no restore claim",
            },
            "bindings": [
                {"identity": self.identity, "evidence": "synthetic original binding"}
            ],
            "owners": owners,
            "budget_before": {},
        }

    def policy(self):
        return {
            "policy_version": self.scope.policy_version,
            "deletion_generation": self.scope.deletion_generation,
        }

    def asset(self, client_id=None):
        return self.create(
            "Attachment",
            scope=self.scope,
            storage_binding=self.binding,
            client_id=client_id or uuid4(),
            source_kind="Bytes",
            **self.policy(),
        )

    def occurrence(self, asset):
        return self.create(
            "AttachmentOccurrence",
            scope=self.scope,
            attachment=asset,
            capture_session=self.session,
            client_id=uuid4(),
            trace_id=uuid4(),
            span_id="0123456789abcdef",
            direction="mixed",
            expires_at=timezone.now() + timedelta(hours=1),
            **self.policy(),
        )

    def upload(self, legacy=False):
        now = timezone.now()
        asset = self.asset()
        occurrence = self.occurrence(asset)
        pk = uuid4()
        extra = (
            {}
            if legacy
            else dict(
                storage_object_id=pk,
                serving_key="original/" + str(pk),
                cleanup_not_before=now + timedelta(hours=1, seconds=15),
                admission_event_id=uuid4(),
                **reservation(now, "global:" + str(pk.int % 16), (5, 1, 1, 0, 4, 1)),
            )
        )
        upload = self.create(
            "AttachmentUpload",
            id=pk,
            scope=self.scope,
            attachment=asset,
            occurrence=occurrence,
            capture_session=self.session,
            idempotency_key=str(pk),
            request_fingerprint="2" * 64,
            expected_bytes=1,
            mime_type="audio/wav",
            quarantine_key="quarantine/" + str(pk),
            expires_at=now + timedelta(hours=1),
            **self.policy(),
            **extra,
        )
        if not legacy:
            self.update(
                upload,
                quarantine_write_attempts=1,
                serving_write_attempts=1,
                write_deadline_at=now + timedelta(seconds=20),
            )
            self.update(
                upload,
                state="validating",
                quarantine_version="q1",
                quarantine_sha256="a" * 64,
                serving_version="v1",
            )
        self.update(asset, state="validating")
        return asset, occurrence, upload

    def publish(self, asset, key):
        now = timezone.now()
        self.update(
            asset,
            state="ready",
            verified_blob_id=uuid4(),
            verified_blob_created_at=now,
            verified_storage_key=key,
            verified_storage_version="v1",
            verified_sha256="a" * 64,
            verified_byte_length=1,
            verified_mime_type="audio/wav",
            verified_at=now,
            verified_sample_rate=8000,
            verified_channels=1,
            verified_frame_count=1,
        )

    def recording(self):
        return self.create(
            "AttachmentRecording",
            scope=self.scope,
            capture_session=self.session,
            client_id=uuid4(),
            trace_id=uuid4(),
            span_id="0123456789abcdef",
            direction="mixed",
            state="importing",
            capture_session_client_id=self.session.client_id,
            correlation_nonce=self.session.correlation_nonce,
            **self.policy(),
        )

    def import_job(self, *, legacy=False, provider=False, provider_file=False):
        now = timezone.now()
        recording = self.recording()
        envelope_id = uuid4()
        extra = {"reserved_bytes": 23, "reserved_segments": 1} if legacy else {}
        if not legacy:
            envelope = self.create(
                "AttachmentSourceEnvelope",
                id=envelope_id,
                scope=self.scope,
                creator_kind="user",
                creator_id=self.session.writer_id,
                direction="mixed",
                source_bytes=10,
                key_id="fixture",
                nonce=b"0" * 12,
                ciphertext=b"fixture",
                expires_at=now + timedelta(minutes=10),
                source_expires_at=now + timedelta(minutes=10),
                authority_generation=0,
                generation_kind="http_strong_etag",
                generation='"fixture-v1"',
                mime_type="audio/wav",
                duration_ms=1,
                trace_id=recording.trace_id,
                span_id=recording.span_id,
            )
            self.envelope = envelope
            if provider:
                self.connection_row = self.create(
                    "AttachmentProviderConnection",
                    scope=self.scope,
                    provider="livekit",
                    api_key_id=str(uuid4()),
                    key_id="fixture",
                    secret_nonce=b"0" * 12,
                    secret_ciphertext=b"fixture",
                    enabled=True,
                    authority_generation=7,
                )
                p = self.create(
                    "AttachmentProviderRecording",
                    scope=self.scope,
                    connection=self.connection_row,
                    registered_output_id=uuid4(),
                    egress_id=str(uuid4()),
                    room_digest="0" * 64,
                    registrar_kind="user",
                    registrar_id=self.session.writer_id,
                    recording_client_id=recording.client_id,
                    trace_id=recording.trace_id,
                    span_id=recording.span_id,
                    capture_session_client_id=self.session.client_id,
                    correlation_nonce=self.session.correlation_nonce,
                    direction="mixed",
                    expires_at=envelope.expires_at,
                )
                self.update(
                    p,
                    provider_state="complete",
                    output_bytes=10,
                    output_duration_ms=1,
                    output_envelope_id=envelope.id,
                    source_generation_kind=envelope.generation_kind,
                    source_generation=envelope.generation,
                    source_mime_type=envelope.mime_type,
                    source_expires_at=envelope.expires_at,
                    source_authority_generation=7,
                    dispatch_due_at=now,
                )
                self.provider = p
            extra.update(
                storage_binding=self.binding,
                capture_session=self.session,
                cleanup_not_before=now + timedelta(hours=1),
                source_generation_kind=envelope.generation_kind,
                source_generation=envelope.generation,
                source_byte_length=10,
                source_mime_type="audio/wav",
                source_duration_ms=1,
                source_expires_at=envelope.expires_at,
                source_authority_generation=7 if provider_file else 0,
                source_creator_kind="user",
                source_creator_id=self.session.writer_id,
                source_trace_id=recording.trace_id,
                source_span_id=recording.span_id,
                source_direction="mixed",
                **reservation(now, "global:imports", (23, 1, 1, 1, 5, 2)),
            )
        pk = uuid4()
        if not legacy:
            extra["scratch_id"] = pk
        job = self.create(
            "AttachmentImportJob",
            id=pk,
            scope=self.scope,
            recording=recording,
            importer_kind="user",
            importer_id=self.session.writer_id,
            request_fingerprint="3" * 64,
            source_kind="ProviderFile" if provider_file else "RemoteUrlEnvelope",
            envelope_id=None if provider_file else envelope_id,
            provider_recording=self.provider if provider_file else None,
            mime_type="audio/wav",
            declared_bytes=10,
            declared_duration_ms=1,
            window_bytes=10,
            max_segment_ms=1,
            recording_bytes=10,
            deadline=now + timedelta(minutes=5),
            **self.policy(),
            **extra,
        )
        return recording, job

    def staged(self):
        recording, job = self.import_job()
        now = timezone.now()
        self.update(
            job,
            lease_phase="recovering",
            lease_epoch=1,
            lease_token=uuid4(),
            lease_until=now + timedelta(minutes=4),
        )
        self.update(job, lease_phase="writing")
        pk = uuid4()
        segment = self.create(
            "AttachmentRecordingSegment",
            id=pk,
            scope=self.scope,
            recording=recording,
            import_job=job,
            storage_binding=self.binding,
            storage_object_id=pk,
            allocation_epoch=1,
            index=0,
            attachment_client_id=uuid4(),
            occurrence_client_id=uuid4(),
            start_frame=0,
            frame_count=1,
            start_ms=0,
            duration_ms=1,
            storage_key="original/" + str(pk),
            quarantine_key="quarantine/" + str(pk),
            sha256="a" * 64,
            byte_length=1,
            cleanup_not_before=now + timedelta(minutes=5),
        )
        self.update(
            segment, write_attempts=1, write_deadline_at=now + timedelta(seconds=20)
        )
        self.update(segment, storage_version="v1")
        asset = self.asset(segment.attachment_client_id)
        self.update(asset, state="validating")
        return asset, segment, job

    def second(self):
        return psycopg.connect(
            dbname=self.connection.settings_dict["NAME"],
            host=os.environ["PG_HOST"],
            port=os.environ["PG_PORT"],
            user=os.environ["PG_USER"],
            password=os.environ["PG_PASSWORD"],
        )


def reservation(now, pool, vector):
    return dict(
        reservation_id=uuid4(),
        reservation_created_at=now,
        reservation_expires_at=now + timedelta(hours=1),
        reservation_pool_key=pool,
        reservation_reserved_bytes=1,
        reservation_decoded_bytes=2,
        reservation_object_count=4,
        reservation_part_count=1,
        **{
            "reservation_" + k: v
            for k, v in zip(
                (
                    "charge_bytes",
                    "transfers",
                    "validation_jobs",
                    "recordings",
                    "objects",
                    "links",
                ),
                vector,
                strict=True,
            )
        },
    )


@pytest.fixture
def case(isolated_settings):
    prefix = os.environ["PG_TEST_DB_PREFIX"]
    assert prefix.startswith("test_att_review_schema_") and len(prefix) <= 40
    name = prefix + uuid4().hex[:12]
    # The published synthetic admin context is used ONLY to create/drop OUR DB.
    admin = psycopg.connect(
        dbname=os.environ["PG_DB"],
        host=os.environ["PG_HOST"],
        port=os.environ["PG_PORT"],
        user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"],
        autocommit=True,
    )
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    connection = connections["default"]
    connection.close()
    connection.settings_dict["NAME"] = name
    if hasattr(settings, "FI_ATTACHMENTS_SCHEMA_V1_EVIDENCE"):
        del settings.FI_ATTACHMENTS_SCHEMA_V1_EVIDENCE
    try:
        yield Case(connection)
    finally:
        connection.close()
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
        admin.close()


@pytest.fixture
def live(case):
    case.activate()
    return case.seed()


@contextmanager
def denied(code="23514"):
    with pytest.raises((IntegrityError, OperationalError, psycopg.Error)) as result:
        yield
    cause = result.value.__cause__ or result.value
    assert cause.sqlstate == code


def binding_values(occurrence):
    now = timezone.now()
    return {
        "bound_at": now,
        "source_started_at": now - timedelta(minutes=1),
        "source_generation": 4,
        "source_expires_at": now + timedelta(days=30),
        "expires_at": now + timedelta(days=30),
    }


def test_first_binding_keeps_full_source_retention_then_forbids_extension(live):
    asset = live.asset()
    occurrence = live.occurrence(asset)
    values = binding_values(occurrence)
    # The source-binding service permits delayed ingestion after capture expiry;
    # the occurrence admission clock and authoritative source must still be live.
    live.update(live.session, expires_at=timezone.now() - timedelta(seconds=1))
    live.update(occurrence, **values)
    assert occurrence.expires_at == values["source_expires_at"]
    live.update(occurrence, bind_checked_at=timezone.now())
    for change in (
        {"expires_at": occurrence.expires_at + timedelta(seconds=1)},
        {"source_expires_at": occurrence.source_expires_at + timedelta(seconds=1)},
        {"bound_at": None},
        {"source_generation": 5},
    ):
        with denied():
            live.update(occurrence, **change)


@pytest.mark.parametrize(
    "condition",
    [
        "expired",
        "deleted",
        "asset_deleted",
        "revoked",
        "policy",
        "identity",
        "partial",
        "source_expired",
    ],
)
def test_initial_binding_is_narrow(live, condition):
    asset = live.asset()
    occurrence = live.occurrence(asset)
    values = binding_values(occurrence)
    if condition == "expired":
        live.update(occurrence, expires_at=timezone.now() - timedelta(seconds=1))
    elif condition == "deleted":
        live.update(occurrence, deleted_at=timezone.now())
    elif condition == "asset_deleted":
        live.update(asset, state="deleted", deleted_at=timezone.now())
    elif condition == "revoked":
        live.update(live.session, revoked_at=timezone.now())
    elif condition == "policy":
        live.update(
            live.scope, enabled=False, policy_version=live.scope.policy_version + 1
        )
    elif condition == "identity":
        values["trace_id"] = uuid4()
    elif condition == "source_expired":
        values["expires_at"] = values["source_expires_at"] = timezone.now() - timedelta(
            seconds=1
        )
    else:
        values["source_generation"] = None
    with denied():
        live.update(occurrence, **values)


def test_new_receipt_obeys_verified_facts_and_remains_immutable(live):
    asset, _, upload = live.upload()
    live.publish(asset, upload.serving_key)
    live.update(upload, state="ready")
    values = {
        "usage_id": uuid4(),
        "usage_created_at": timezone.now(),
        "usage_outcome": "deleted",
        "usage_stored_bytes": 0,
        "settlement_event_id": uuid4(),
    }
    with denied():
        live.update(upload, **values)
    values.update(usage_outcome="ready", usage_stored_bytes=1)
    live.update(upload, **values)
    live.update(asset, state="deleted", deleted_at=timezone.now())
    live.update(upload, state="deleted")
    with denied():
        live.update(upload, usage_stored_bytes=0)


def migrated_deleted_upload(case, *, has_receipt):
    case.seed()
    asset, _, upload = case.upload(legacy=True)
    now = timezone.now()
    case.update(upload, expires_at=now - timedelta(hours=1), state="validating")
    blob = case.create(
        "AttachmentBlob",
        scope=case.scope,
        attachment=asset,
        storage_key="original/" + str(upload.pk),
        storage_version="v1",
        sha256="a" * 64,
        byte_length=1,
        mime_type="audio/wav",
        validated_at=now,
        sample_rate=8000,
        channels=1,
        frame_count=1,
    )
    case.update(asset, state="ready")
    case.update(upload, state="ready")
    case.update(asset, state="deleted", deleted_at=now)
    case.update(upload, state="deleted")
    reservation_row = case.create(
        "UploadReservation",
        scope=case.scope,
        upload=upload,
        reserved_bytes=1,
        decoded_bytes=2,
        expires_at=upload.expires_at,
    )
    # A genuinely historical reservation was created before its old expiry.
    case.update(reservation_row, created_at=upload.expires_at - timedelta(hours=1))
    events = [
        case.create(
            "AttachmentOutbox",
            scope=case.scope,
            upload=upload,
            event=kind,
            generation=0,
            available_at=now,
        )
        for kind in ("admitted", "settled")
    ]
    assert upload.usage_id is None and upload.usage_created_at is None
    assert events[1].processed_at is None
    original = None
    if has_receipt:
        receipt = case.create(
            "AttachmentUsageReceipt",
            scope=case.scope,
            upload=upload,
            outcome="deleted",
            stored_bytes=0,
        )
        original = (
            receipt.id,
            receipt.created_at,
            receipt.outcome,
            receipt.stored_bytes,
        )
    else:
        assert (
            not case.model("AttachmentUsageReceipt")
            .objects.filter(upload=upload)
            .exists()
        )
    proof = {
        "evidence": "synthetic original owner",
        "binding_id": str(case.binding.id),
        "storage": {
            "serving": {"key": blob.storage_key, "version": "v1"},
            "quarantine": {"key": upload.quarantine_key, "version": None},
        },
        "capacity_evidence": "synthetic retained peak",
        "capacity": dict(
            zip(case.modules["0141"].VECTOR, (5, 1, 1, 0, 4, 1), strict=True)
        ),
        "cleanup_not_before": upload.expires_at + timedelta(seconds=15),
    }
    case.inventory(
        {
            "Attachment": {str(asset.id): proof},
            "AttachmentUpload": {str(upload.id): proof},
        }
    )
    case.activate()
    return original, upload, asset, blob, events


def test_legacy_receipt_survives_backfill_contraction_and_cleanup(case):
    original, upload, _, _, events = migrated_deleted_upload(case, has_receipt=True)
    current = case.model("AttachmentUpload").objects.get(pk=upload.id)
    case.update(current, cleanup_checked_at=timezone.now())
    case.update(current, reservation_settled_at=timezone.now())
    assert (
        current.usage_id,
        current.usage_created_at,
        current.usage_outcome,
        current.usage_stored_bytes,
    ) == original
    assert current.settlement_event_id == events[1].id
    assert (
        case.model("AttachmentAuditEvent")
        .objects.filter(event_id=events[1].id, reason="deleted")
        .count()
        == 1
    )
    for change in (
        {"usage_id": uuid4()},
        {"usage_created_at": timezone.now()},
        {"usage_outcome": "ready"},
        {"usage_stored_bytes": 1},
    ):
        with denied():
            case.update(current, **change)


def test_historical_first_settlement_after_delete(case, monkeypatch):
    """The forward migration settles missing usage before any current worker can run."""
    import sys
    from pathlib import Path
    from types import SimpleNamespace

    _, upload, asset, blob, events = migrated_deleted_upload(case, has_receipt=False)
    models = SimpleNamespace(
        **{
            name: case.model(name)
            for name in (
                "Attachment",
                "AttachmentAuditEvent",
                "AttachmentOutbox",
                "AttachmentUpload",
            )
        },
        AttachmentState=SimpleNamespace(READY="ready"),
    )
    current = models.AttachmentUpload.objects.get(pk=upload.id)
    event = models.AttachmentOutbox.objects.get(pk=events[1].pk)
    assert (
        event.upload_id == current.pk
        and event.event == "settled"
        and event.processed_at is not None
    )
    assert current.usage_id is not None and current.usage_created_at is not None
    assert (
        current.usage_outcome == "ready"
        and current.usage_stored_bytes == blob.byte_length > 0
    )
    before = (
        blob.pk,
        blob.created_at,
        blob.storage_key,
        blob.storage_version,
        blob.sha256,
        blob.byte_length,
        asset.deleted_at,
    )
    assert asset.deleted_at is not None

    def retained_asset_facts():
        result = models.Attachment.objects.get(pk=asset.pk)
        assert result.state == "deleted"
        return (
            result.verified_blob_id,
            result.verified_blob_created_at,
            result.verified_storage_key,
            result.verified_storage_version,
            result.verified_sha256,
            result.verified_byte_length,
            result.deleted_at,
        )

    assert retained_asset_facts() == before
    processed_at = event.processed_at
    # Migration-state models point at the real fenced PostgreSQL tables. Keep the
    # service's queries, row locks, transactions and replay logic unmodified.
    with monkeypatch.context() as patch:
        patch.setitem(sys.modules, "tracer.models.attachment", models)
        spec = importlib.util.spec_from_file_location(
            "historical_accounting",
            Path(__file__).resolve().parents[1] / "services/attachments/accounting.py",
        )
        accounting = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(accounting)
    for _ in range(2):
        receipt = accounting.consume_settlement(event.pk)
        assert (
            receipt.usage_id,
            receipt.usage_created_at,
            receipt.usage_outcome,
            receipt.usage_stored_bytes,
        ) == (current.usage_id, current.usage_created_at, "ready", blob.byte_length)
    assert (
        models.AttachmentUpload.objects.filter(
            pk=current.pk, usage_id=current.usage_id
        ).count()
        == 1
    )
    assert (
        models.AttachmentAuditEvent.objects.filter(
            scope_id=current.scope_id,
            entity_id=current.pk,
            event="settlement",
            event_id=event.pk,
        ).count()
        == 1
    )
    assert (
        models.AttachmentAuditEvent.objects.filter(
            scope_id=current.scope_id, entity_id=current.pk, event="settlement"
        ).count()
        == 1
    )
    event.refresh_from_db()
    assert event.processed_at == processed_at
    assert retained_asset_facts() == before


@pytest.mark.parametrize(
    "mode",
    [
        "blank",
        "recorded",
        "unknown_version",
        "wrong_key",
        "wrong_id",
        "evidence_version",
    ],
)
def test_legacy_segment_conversion_is_exact_and_retains_charge(case, mode):
    case.seed()
    recording, job = case.import_job(legacy=True)
    object_id = uuid4()
    key = "original/" + str(object_id)
    stored_key = key if mode == "recorded" else ""
    version = "v1" if mode in ("recorded", "unknown_version") else ""
    if mode == "wrong_key":
        stored_key = "original/" + str(uuid4())
    segment = case.create(
        "AttachmentRecordingSegment",
        scope=case.scope,
        recording=recording,
        index=0,
        attachment_client_id=object_id,
        occurrence_client_id=uuid4(),
        start_frame=0,
        frame_count=1,
        start_ms=0,
        duration_ms=1,
        storage_key=stored_key,
        storage_version=version,
        sha256="a" * 64,
        byte_length=1,
    )
    proof = {
        "evidence": "synthetic original allocation",
        "binding_id": str(case.binding.id),
        "cleanup_not_before": job.deadline + timedelta(seconds=15),
        "capacity_evidence": "synthetic retained charge",
        "capacity": dict(
            zip(case.modules["0141"].VECTOR, (23, 1, 1, 1, 5, 2), strict=True)
        ),
    }
    segment_proof = dict(
        proof,
        storage_object_id=str(uuid4() if mode == "wrong_id" else object_id),
        storage={
            "serving": {
                "key": key,
                "version": "v1" if mode in ("recorded", "evidence_version") else None,
            }
        },
    )
    case.inventory(
        {
            "AttachmentImportJob": {str(job.id): proof},
            "AttachmentRecordingSegment": {str(segment.id): segment_proof},
        }
    )
    if mode not in ("blank", "recorded"):
        with pytest.raises(RuntimeError):
            case.apply("0141")
        segment.refresh_from_db()
        assert (segment.storage_key, segment.storage_version) == (stored_key, version)
        return
    case.apply("0141")
    # Interrupt before switching fences, then resume the exact data-copy again.
    with case.connection.schema_editor(atomic=False) as editor:
        case.modules["0141"].forwards(case.state.apps, editor)
    case.apply("0142")
    case.apply("0143")
    segment = case.model("AttachmentRecordingSegment").objects.get(pk=segment.id)
    job = case.model("AttachmentImportJob").objects.get(pk=job.id)
    assert segment.storage_key == key and segment.storage_object_id == object_id
    assert segment.storage_version == ("v1" if mode == "recorded" else None)
    assert segment.unowned_cleanup_due_at is not None
    assert job.reservation_settled_at is None and job.reservation_charge_bytes == 23
    assert list(
        case.model("AttachmentBudget").objects.values_list("reserved_bytes", flat=True)
    ) == [23, 23, 23]


@pytest.mark.parametrize("cleanup_complete", ["absent", False])
def test_released_legacy_import_needs_cleanup_reconciliation(case, cleanup_complete):
    # A settled job whose staged objects were never reconciled cannot be
    # backfilled into a released reservation: the copy refuses instead of
    # inventing cleanup evidence or releasing the reservation a second time.
    case.seed()
    _, job = case.import_job(legacy=True)
    case.update(job, released_at=job.deadline)
    proof = {
        "evidence": "synthetic original allocation",
        "binding_id": str(case.binding.id),
        "cleanup_not_before": job.deadline + timedelta(seconds=15),
        "capacity_evidence": "synthetic retained charge",
        "capacity": dict(
            zip(case.modules["0141"].VECTOR, (23, 1, 1, 1, 5, 2), strict=True)
        ),
    }
    if cleanup_complete != "absent":
        proof["cleanup_complete"] = cleanup_complete
    case.inventory({"AttachmentImportJob": {str(job.id): proof}})
    budgets = case.model("AttachmentBudget").objects.order_by("pk")
    before = list(budgets.values())
    with pytest.raises(RuntimeError, match="lacks cleanup reconciliation"):
        case.apply("0141")
    assert not case.recorder.migration_qs.filter(name=NAMES[5]).exists()
    row = case.model("AttachmentImportJob").objects.get(pk=job.id)
    assert row.released_at == job.released_at
    assert list(budgets.values()) == before


@pytest.mark.parametrize("owner", ["upload", "segment", "job"])
def test_publication_rechecks_concurrent_retirement_without_waiting(live, owner):
    if owner == "upload":
        asset, _, row = live.upload()
        table, key = "tracer_attachmentupload", row.serving_key
        assignment = "state='failed'"
    else:
        asset, segment, job = live.staged()
        row = segment if owner == "segment" else job
        table = (
            "tracer_attachmentrecordingsegment"
            if owner == "segment"
            else "tracer_attachmentimportjob"
        )
        key = segment.storage_key
        assignment = (
            "state='erased'"
            if owner == "segment"
            else "state='failed',lease_phase='terminal',lease_token=NULL,lease_until=NULL"
        )
    with live.second() as second:
        second.execute(
            sql.SQL("UPDATE {} SET " + assignment + " WHERE id=%s").format(
                sql.Identifier(table)
            ),
            [row.id],
        )
        with live.connection.cursor() as cursor:
            cursor.execute("SET statement_timeout='2s'")
        with denied("55P03"):
            live.publish(asset, key)
        second.commit()
    with denied():
        live.publish(asset, key)
    asset.refresh_from_db()
    assert asset.state == "validating" and asset.verified_blob_id is None


def test_publication_holds_owner_lock_until_transaction_commits(live):
    from django.db import transaction

    asset, _, upload = live.upload()
    with transaction.atomic():
        live.publish(asset, upload.serving_key)
        with live.second() as second:
            second.execute("SET lock_timeout='150ms'")
            with denied("55P03"):
                second.execute(
                    "UPDATE tracer_attachmentupload SET state='failed' WHERE id=%s",
                    [upload.id],
                )
            second.rollback()
        live.update(upload, state="ready")
    with denied():
        live.update(upload, state="failed")


def test_whole_import_allocation_publishes_under_canonical_owner_locks(live):
    from django.db import transaction

    asset, segment, job = live.staged()
    with transaction.atomic():
        live.model("AttachmentImportJob").objects.select_for_update().get(pk=job.id)
        live.model("AttachmentRecordingSegment").objects.select_for_update().get(
            pk=segment.id
        )
        live.model("Attachment").objects.select_for_update().get(pk=asset.id)
        live.publish(asset, segment.storage_key)
        live.update(segment, state="published", attachment_id=asset.id)
    asset.refresh_from_db()
    segment.refresh_from_db()
    assert asset.state == "ready" and segment.state == "published"
    assert asset.verified_storage_version == segment.storage_version == "v1"


@pytest.mark.parametrize("reviewed", [True, False], ids=["accurate", "omitted-metrics"])
def test_populated_migration_checks_parallel_metrics_history(case, reviewed):
    case.seed()
    metrics = (
        "0140_metric_refresh_pause",
        "0141_metric_refresh_busy_guard",
        "0142_merge_metrics_and_eval_put_back",
    )
    for name in metrics:
        case.recorder.record_applied("tracer", name)
    case.inventory({}, extra_tracer_migrations=metrics if reviewed else ())

    if reviewed:
        case.activate()  # 0142 and 0143 recheck the same sealed inventory.
        applied = case.recorder.applied_migrations()
        assert all(("tracer", name) in applied for name in NAMES[5:])
    else:
        with pytest.raises(RuntimeError, match="applied migration inventory differs"):
            case.apply("0141")
        assert ("tracer", NAMES[5]) not in case.recorder.applied_migrations()


def test_populated_migration_does_not_invent_missing_provenance(case):
    case.seed()
    with pytest.raises(RuntimeError, match="populated install requires"):
        case.apply("0141")
    assert not case.recorder.migration_qs.filter(name=NAMES[5]).exists()


@pytest.mark.parametrize("provider_file", [False, True])
def test_provider_envelope_uses_existing_ancestry_and_current_revocation(
    live, provider_file
):
    _, job = live.import_job(provider=True, provider_file=provider_file)
    # Envelope epoch is zero; provider connection epoch is seven. They are
    # independent facts, not an invented equality on unrelated authorities.
    live.update(
        job,
        lease_phase="recovering",
        lease_epoch=1,
        lease_token=uuid4(),
        lease_until=timezone.now() + timedelta(minutes=4),
    )
    live.update(job, lease_phase="writing")
    with live.second() as second:
        second.execute(
            "UPDATE tracer_attachmentproviderconnection SET enabled=false, revoked_at=clock_timestamp(), authority_generation=8 WHERE id=%s",
            [live.connection_row.id],
        )
        with denied("55P03"):
            live.update(job, lease_phase="publishing")
        second.commit()
    with denied():
        live.update(job, lease_phase="publishing")
    job.refresh_from_db()
    assert job.lease_phase == "writing"


def test_provider_envelope_rejects_ambiguous_reverse_ancestry(live):
    _, job = live.import_job(provider=True)
    original = live.provider
    fields = {
        field.attname: getattr(original, field.attname)
        for field in original._meta.concrete_fields
        if field.name not in ("id", "created_at")
    }
    fields.update(
        registered_output_id=uuid4(),
        egress_id=str(uuid4()),
        provider_state="registered",
        dispatch_due_at=None,
    )
    duplicate = live.create("AttachmentProviderRecording", **fields)
    live.update(duplicate, provider_state="complete", dispatch_due_at=timezone.now())
    with denied():
        live.update(
            job,
            lease_phase="recovering",
            lease_epoch=1,
            lease_token=uuid4(),
            lease_until=timezone.now() + timedelta(minutes=4),
        )
