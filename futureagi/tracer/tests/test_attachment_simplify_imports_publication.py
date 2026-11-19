"""Unit proof of publication control flow with deterministic model doubles.

The target doubles retain their model's manager and real double save behavior.
Rollback is simulated here; PostgreSQL atomicity/locking remains an integration
qualification gate, not a claim made by this suite.
"""

import importlib.util
import sys
import types
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
from uuid import uuid4

import pytest

from tracer.tests import test_attachment_simplify_imports_fences as fences
from tracer.tests.test_attachment_simplify_imports_fences import (  # noqa: F401 - shared lane fixture
    Row,
    lease_fixture,
)

lane = fences.lane


@pytest.mark.parametrize("fail_at", [None, 1, 2])
def test_whole_publication_is_atomic_with_current_manifest_and_receipt(
    lane, monkeypatch, fail_at
):
    job, row, target, fence, _, _, _, _ = lease_fixture(lane, monkeypatch)
    scope, session = Row(id=job.scope_id), Row()
    target = lane.AttachmentRecording.objects.create(
        id=job.recording_id, sample_rate=target.sample_rate, channels=target.channels
    )
    saved_states = []
    save = target.save

    def save_target(**kwargs):
        saved_states.append(target.state)
        return save(**kwargs)

    monkeypatch.setattr(target, "save", Mock(side_effect=save_target))
    target.client_id, target.direction, target.manifest_revision = uuid4(), "mixed", 0
    target.trace_id, target.span_id = uuid4(), "1234567890abcdef"
    target.state, target.manifest = "importing", None
    scope.policy_version, scope.deletion_generation = 1, 0
    job.source_duration_ms, job.source_expires_at = (
        4,
        datetime.now(UTC) + timedelta(hours=1),
    )
    row.import_job, row.index, row.start_ms, row.duration_ms = job, 0, 0, 2
    row.occurrence_client_id, row.attachment_client_id = uuid4(), uuid4()
    row.storage_version, row.erased_at = "version1", None
    second = lane.AttachmentRecordingSegment.objects.create(
        **{
            **{
                k: v
                for k, v in row.__dict__.items()
                if k not in {"id", "pk", "_persisted"}
            },
            "index": 1,
            "start_ms": 2,
            "storage_key": "original/second",
            "storage_version": "version2",
            "occurrence_client_id": uuid4(),
            "attachment_client_id": uuid4(),
        }
    )

    @contextmanager
    def locked(*_args, **_kwargs):
        yield job, scope, session, target, None

    monkeypatch.setattr(lane, "_locked_job", locked)
    monkeypatch.setattr(lane, "_remote_recheck", lambda *_: None)
    managers = [
        lane.AttachmentRecording.objects,
        lane.Attachment.objects,
        lane.AttachmentOccurrence.objects,
        lane.AttachmentManifestRevision.objects,
        lane.AttachmentRecordingSegment.objects,
    ]

    @contextmanager
    def atomic():
        lists = [(manager, list(manager.rows)) for manager in managers]
        states = [(item, dict(item.__dict__)) for item in [job, target, row, second]]
        try:
            yield
        except Exception:
            for manager, rows in lists:
                manager.rows = rows
            for item, values in states:
                item.__dict__.clear()
                item.__dict__.update(values)
            raise

    monkeypatch.setattr(lane.transaction, "atomic", atomic)
    published = []
    publication = types.ModuleType("tracer.services.attachments.publication")

    def publish_verified_locked(**kw):
        assert job.lease_phase == "publishing" and job.state == "validating"
        assert kw["fence"] == fence
        assert kw["allocation"].attachment == kw["attachment"]
        assert kw["facts"].object.identity.version == kw["allocation"].storage_version
        published.append(kw["attachment"].id)
        if len(published) == fail_at:
            raise lane.AttachmentError("conflict", 409)
        kw["attachment"].state = "ready"

    publication.publish_verified_locked = publish_verified_locked
    derivatives = types.ModuleType("tracer.services.attachments.derivatives")
    derivatives.enqueue_for_asset_locked = lambda asset: None
    validation = types.ModuleType("tracer.services.attachments.validation")
    for module in (publication, derivatives, validation):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    manifest = lane.recordings.build_manifest(
        recording_id=target.client_id,
        rate=16000,
        channels=1,
        direction="mixed",
        track_ids=[uuid4()],
        duration_ms=4,
        capture_complete=True,
        segments=[
            (
                item.occurrence_client_id,
                NS(index=item.index, start_ms=item.start_ms, duration_ms=2),
            )
            for item in (row, second)
        ],
    )
    if fail_at:
        with pytest.raises(lane.AttachmentError):
            lane.publish(
                job_id=job.id,
                fence=fence,
                segment_ids=(row.id, second.id),
                manifest=manifest,
            )
        assert (
            not lane.Attachment.objects.rows
            and not lane.AttachmentOccurrence.objects.rows
        )
        assert not lane.AttachmentManifestRevision.objects.rows
        assert target.manifest is None and target.state == job.state == "importing"
        assert row.state == second.state == "staged"
        assert saved_states == ["validating"]
        target.save.assert_called_once_with(update_fields=["state"])
    else:
        lane.publish(
            job_id=job.id,
            fence=fence,
            segment_ids=(row.id, second.id),
            manifest=manifest,
        )
        assert (
            len(lane.Attachment.objects.rows)
            == len(lane.AttachmentOccurrence.objects.rows)
            == 2
        )
        assert target.manifest == manifest and target.manifest_revision == 1
        receipt = lane.AttachmentManifestRevision.objects.rows[0]
        assert receipt.idempotency_key == f"import:{job.id}" and receipt.revision == 1
        assert (
            job.state == target.state == "ready"
            and row.state == second.state == "published"
        )
        assert (
            job.lease_phase == "terminal"
            and job.lease_token is None
            and job.lease_until is None
        )
        assert saved_states == ["validating", "ready"]
    assert lane.AttachmentRecording.objects.get(pk=target.id) is target


def test_single_validation_outside_transaction_then_atomic_publication(
    lane, monkeypatch
):
    job, _, _, fence, _, _, _, _ = lease_fixture(lane, monkeypatch)
    scope, session = Row(id=job.scope_id), Row()
    target = lane.AttachmentUpload.objects.create(state="validating")
    asset = lane.Attachment.objects.create(state="validating")
    monkeypatch.setattr(target, "save", Mock(wraps=target.save))
    job.upload_id, job.recording_id = target.id, None
    active, calls = [], []

    @contextmanager
    def atomic():
        active.append(True)
        try:
            yield
        finally:
            active.pop()

    @contextmanager
    def locked(*_args, **_kwargs):
        yield job, scope, session, target, asset

    monkeypatch.setattr(lane.transaction, "atomic", atomic)
    monkeypatch.setattr(lane, "_locked_job", locked)

    def remote(*_):
        assert not active
        calls.append("remote")

    monkeypatch.setattr(lane, "_remote_recheck", remote)
    verified = object()
    validation = types.ModuleType("tracer.services.attachments.validation")

    def validate(**kw):
        assert not active and kw["fence"] == fence
        calls.append("validate")
        return verified

    validation.validate_import_snapshot = validate
    publication = types.ModuleType("tracer.services.attachments.publication")

    def publish(**kw):
        assert active and kw["facts"] is verified and kw["allocation"] is target
        assert job.state == "validating" and job.lease_phase == "publishing"
        asset.state = "ready"
        calls.append("publish")

    publication.publish_verified_locked = publish
    derivatives = types.ModuleType("tracer.services.attachments.derivatives")
    derivatives.enqueue_for_asset_locked = lambda *_: calls.append("derivatives")
    uploads = types.ModuleType("tracer.services.attachments.uploads")

    def settle(*_args, **_kwargs):
        assert not active and job.state == "ready"
        calls.append("settle")

    uploads.settle = settle
    # The unified inline receipt precedes enqueue in the same transaction.
    # Keep the legacy post-commit settlement expectation visible below.
    accounting = types.ModuleType("tracer.services.attachments.accounting")

    def receipt(allocation, *, attachment, outcome):
        assert active and allocation is target and attachment is asset
        assert outcome == target.state == asset.state == "ready"

    accounting.receipt_locked = Mock(side_effect=receipt)
    for module in (validation, publication, derivatives, uploads, accounting):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    lane.publish(job_id=job.id, fence=fence, segment_ids=(), manifest=None)
    accounting.receipt_locked.assert_called_once()
    assert calls == ["remote", "validate", "remote", "publish", "derivatives", "settle"]
    assert target.state == job.state == asset.state == "ready"
    target.save.assert_called_once_with(update_fields=["state"])
    assert lane.AttachmentUpload.objects.get(pk=target.id) is target


def task_module(monkeypatch):
    temporal = types.ModuleType("tfc.temporal.drop_in")
    temporal.temporal_activity = lambda **_: lambda fn: fn
    monkeypatch.setitem(sys.modules, temporal.__name__, temporal)
    path = Path(__file__).parents[1] / "tasks" / "attachment_recordings.py"
    spec = importlib.util.spec_from_file_location("import_tasks_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.settings = NS(FI_ATTACHMENTS_ENABLED=True)
    return module


def test_task_one_bad_binding_does_not_skip_next_or_derivatives(lane, monkeypatch):
    task = task_module(monkeypatch)
    events = []
    first, second, derivative, scope = uuid4(), uuid4(), uuid4(), uuid4()
    imports = types.ModuleType("tracer.services.attachments.imports")

    def pending(**_):
        events.append("select")
        return [(first, scope), (second, scope)]

    def run(job_id, **kw):
        assert "storage" not in kw and events[0] == "select"
        events.append(job_id)
        if job_id == first:
            raise RuntimeError("unavailable historical binding")

    imports.pending, imports.run = pending, run
    derivatives = types.ModuleType("tracer.services.attachments.derivatives")
    derivatives.pending = lambda **_: [(derivative, scope)]
    derivatives.generate = lambda identifier, **kw: events.append(identifier)
    provider = types.ModuleType("tracer.services.attachments.provider_recordings")
    provider.resume_pending_completions = Mock()
    provider.reconcile_registered = lambda **_: None
    provider.dispatch_completed = lambda **_: None
    package = sys.modules["tracer.services.attachments"]
    for module in (imports, derivatives, provider):
        monkeypatch.setitem(sys.modules, module.__name__, module)
        monkeypatch.setattr(
            package, module.__name__.split(".")[-1], module, raising=False
        )
    task.run_attachment_recording_jobs()
    provider.resume_pending_completions.assert_called_once_with(limit=2)
    assert events == ["select", first, second, derivative]


def test_sweep_needs_no_default_decoder_or_source_keys(lane, monkeypatch):
    task = task_module(monkeypatch)
    events = []
    package = sys.modules["tracer.services.attachments"]
    for name, method in (
        ("envelopes", "sweep"),
        ("derivatives", "purge"),
        ("imports", "sweep"),
    ):
        module = types.ModuleType("tracer.services.attachments." + name)
        setattr(module, method, lambda name=name, **_: events.append(name))
        monkeypatch.setitem(sys.modules, module.__name__, module)
        monkeypatch.setattr(package, name, module, raising=False)
    provider = types.ModuleType("tracer.services.attachments.provider_recordings")
    provider.sweep_pending_completions = Mock()
    monkeypatch.setitem(sys.modules, provider.__name__, provider)
    monkeypatch.setattr(package, "provider_recordings", provider, raising=False)
    task.sweep_attachment_recordings()
    provider.sweep_pending_completions.assert_called_once_with(limit=500)
    assert events == ["envelopes", "derivatives", "imports"]
    task.settings.FI_ATTACHMENTS_ENABLED = False
    events.clear()
    task.run_attachment_recording_jobs()
    task.sweep_attachment_recordings()
    assert events == []
