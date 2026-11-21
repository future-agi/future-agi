"""Review repair regressions with explicit in-memory ORM/transaction doubles.

These tests execute service code, not PostgreSQL triggers or native storage.
"""

import sys
import types
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import Mock
from uuid import uuid4

import pytest

from tracer.tests import test_attachment_simplify_imports_fences as shared
from tracer.tests import test_attachment_simplify_imports_publication as old_publication
from tracer.tests.test_attachment_simplify_imports_fences import (
    Row,
    lease_fixture,
    request,
)

lane = shared.lane


def install(monkeypatch, name, **values):
    module = types.ModuleType("tracer.services.attachments." + name)
    module.__dict__.update(values)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(
        sys.modules["tracer.services.attachments"], name, module, raising=False
    )
    return module


@pytest.mark.parametrize("fail_at", [None, 1, 2])
def test_whole_insert_starts_pending_before_validating(lane, monkeypatch, fail_at):
    install(monkeypatch, "accounting")
    # The reused target is a bare Row, not one of the fixture's model classes.
    monkeypatch.setattr(Row, "objects", NS(rows=[]), raising=False)
    create = lane.Attachment.objects.create
    states = []

    def checked(**values):
        states.append(values["state"])
        assert values["state"] == "pending_upload"
        asset = create(**values)
        save = asset.save

        def transition(**kwargs):
            assert kwargs["update_fields"] == ["state"]
            assert asset.state == "validating"
            states.append(asset.state)
            return save(**kwargs)

        asset.save = transition
        return asset

    monkeypatch.setattr(lane.Attachment.objects, "create", checked)
    old_publication.test_whole_publication_is_atomic_with_current_manifest_and_receipt(
        lane, monkeypatch, fail_at
    )
    assert states and states == ["pending_upload", "validating"] * (
        1 if fail_at == 1 else 2
    )


@pytest.mark.parametrize("capacity_failure", [False, True])
def test_takeover_saves_current_lease_before_resize_and_rolls_back(
    lane, monkeypatch, capacity_failure
):
    now = datetime.now(UTC)
    job = lane.AttachmentImportJob.objects.create(
        scope_id=uuid4(),
        lease_epoch=1,
        lease_token=uuid4(),
        lease_until=now - timedelta(seconds=1),
        lease_phase="writing",
        deadline=now + timedelta(hours=1),
        cleanup_not_before=now,
        source_byte_length=100,
        window_bytes=100,
        reservation_charge_bytes=200,
        reservation_transfers=1,
        reservation_validation_jobs=1,
        reservation_recordings=1,
        reservation_objects=2,
        reservation_links=0,
    )
    original = dict(job.__dict__)
    budget = [200]
    events = []
    saved = {}
    save = job.save

    def save_lease(**kwargs):
        saved.update(
            phase=job.lease_phase,
            epoch=job.lease_epoch,
            token=job.lease_token,
            until=job.lease_until,
        )
        events.append("lease")
        save(**kwargs)

    job.save = save_lease

    @contextmanager
    def atomic():
        before = dict(job.__dict__)
        amount = budget[0]
        try:
            yield
        except Exception:
            job.__dict__.clear()
            job.__dict__.update(before)
            budget[0] = amount
            raise

    @contextmanager
    def locked(*args, **kwargs):
        events.append("owners")
        yield job, None, None, None, None

    def resize(locks, *, owner, amount):
        assert saved["phase"] == "recovering" and saved["epoch"] == 2
        assert saved["until"] > now and saved["token"] != original["lease_token"]
        assert amount.bytes == 400
        events.append("resize")
        budget[0] = 400
        if capacity_failure:
            raise lane.AttachmentError("throttled", 429)

    install(
        monkeypatch,
        "capacity_db",
        count_attempt=lambda **_: True,
        lock_capacity=lambda **_: events.append("budgets"),
        resize_locked=resize,
    )
    monkeypatch.setattr(lane.transaction, "atomic", atomic)
    monkeypatch.setattr(lane, "_locked_job_rows", locked)
    monkeypatch.setattr(lane, "_check_job", lambda *_: events.append("authorized"))
    monkeypatch.setattr(
        lane.sandbox, "prepare_import_scratch", lambda *args: events.append("scratch")
    )
    if capacity_failure:
        with pytest.raises(lane.AttachmentError):
            lane.claim(job.id, scope_id=job.scope_id, now=now)
        assert budget == [200]
        for key in (
            "lease_epoch",
            "lease_token",
            "lease_until",
            "lease_phase",
            "attempts",
        ):
            assert getattr(job, key) == original[key]
        assert events == ["budgets", "owners", "authorized", "lease", "resize"]
    else:
        fence = lane.claim(job.id, scope_id=job.scope_id, now=now)
        assert fence.epoch == 2 and fence.token == saved["token"]
        assert events == [
            "budgets",
            "owners",
            "authorized",
            "lease",
            "resize",
            "scratch",
        ]


@pytest.mark.parametrize("serving", [None, "already-recorded-serving"])
@pytest.mark.parametrize("bad", [None, "digest", "length", "stale", "authority"])
def test_replacement_adopts_only_recorded_single_snapshot(
    lane, monkeypatch, serving, bad
):
    job, _, _, fence, *_ = lease_fixture(lane, monkeypatch)
    upload = lane.AttachmentUpload.objects.create(
        quarantine_version="accepted-q",
        quarantine_sha256="b" * 64,
        received_bytes=100,
        expected_bytes=100,
        state="validating",
        reservation_settled_at=None,
        allocation_epoch=0,
        serving_version=serving,
    )
    asset = Row(verified_blob_id=None)
    job.upload_id, job.recording_id, job.source_byte_length = upload.id, None, 100
    checked = []

    @contextmanager
    def locked(*args):
        checked.append("current-authority")
        if bad == "authority":
            raise lane.AttachmentError("source_unavailable", 403)
        yield job, None, None, upload, asset

    monkeypatch.setattr(lane, "_locked_job", locked)
    install(monkeypatch, "uploads")
    monkeypatch.setattr(
        lane,
        "prepare_put",
        Mock(side_effect=AssertionError("no PUT or reservation on adoption")),
    )
    if bad == "stale":
        job.lease_epoch += 1
    if bad == "length":
        upload.received_bytes += 1
    digest = "c" * 64 if bad == "digest" else "b" * 64
    if bad:
        with pytest.raises((lane.AttachmentError, lane.LeaseLost)):
            lane._single_snapshot(job, fence, object(), digest, object())
        assert upload.allocation_epoch == 0
    else:
        lane._single_snapshot(job, fence, object(), digest, object())
        assert upload.allocation_epoch == fence.epoch
        assert (
            upload.serving_version == serving
            and upload.quarantine_version == "accepted-q"
        )
    assert checked == ["current-authority"]


@pytest.mark.parametrize("receipt_failure", [False, True])
def test_single_ready_receipt_is_inside_publication_transaction(
    lane, monkeypatch, receipt_failure
):
    job, _, _, fence, *_ = lease_fixture(lane, monkeypatch)
    target = lane.AttachmentUpload.objects.create(state="validating", usage_id=None)
    asset = lane.Attachment.objects.create(state="validating")
    job.upload_id, job.recording_id = target.id, None
    active, events = [], []

    @contextmanager
    def atomic():
        saved = [(r, dict(r.__dict__)) for r in (job, target, asset)]
        active.append(True)
        try:
            yield
        except Exception:
            for row, values in saved:
                row.__dict__.clear()
                row.__dict__.update(values)
            raise
        finally:
            active.pop()

    @contextmanager
    def locked(*args):
        yield job, Row(), Row(), target, asset

    def receipt(upload, *, attachment, outcome):
        assert active and target.state == asset.state == outcome == "ready"
        assert job.lease_phase == "publishing"
        target.usage_id = uuid4()
        events.append("receipt")
        if receipt_failure:
            raise RuntimeError("receipt write failed")

    monkeypatch.setattr(lane.transaction, "atomic", atomic)
    monkeypatch.setattr(lane, "_locked_job", locked)
    monkeypatch.setattr(lane, "_remote_recheck", lambda *_: None)
    install(monkeypatch, "validation", validate_import_snapshot=lambda **_: object())
    install(
        monkeypatch,
        "publication",
        publish_verified_locked=lambda **_: setattr(asset, "state", "ready"),
    )
    install(monkeypatch, "accounting", receipt_locked=receipt)
    install(monkeypatch, "derivatives", enqueue_for_asset_locked=lambda *_: None)
    install(
        monkeypatch,
        "uploads",
        settle=Mock(side_effect=AssertionError("budget lock inversion")),
    )
    if receipt_failure:
        with pytest.raises(RuntimeError, match="receipt write failed"):
            lane.publish(job_id=job.id, fence=fence, segment_ids=(), manifest=None)
        assert job.state == "importing" and target.state == asset.state == "validating"
        assert target.usage_id is None
    else:
        lane.publish(job_id=job.id, fence=fence, segment_ids=(), manifest=None)
        assert target.usage_id is not None and job.state == "ready"
    assert events == ["receipt"]


@pytest.mark.parametrize("reconciled", [True, False])
def test_ready_single_runs_shared_reconciliation_before_job_release(
    lane, monkeypatch, reconciled
):
    now = datetime.now(UTC)
    upload = lane.AttachmentUpload.objects.create(
        scope_id=uuid4(), state="ready", reservation_settled_at=None
    )
    job = Row(
        id=uuid4(),
        scope_id=upload.scope_id,
        upload_id=upload.id,
        state="ready",
        cleanup_not_before=now,
        lease_epoch=2,
        storage_binding_id=uuid4(),
    )
    events = []
    monkeypatch.setattr(
        lane.sandbox,
        "cleanup_import_scratch",
        lambda *args: events.append("termination") or True,
    )
    install(
        monkeypatch, "readiness", resolve_historical=lambda *args, **kwargs: object()
    )
    install(
        monkeypatch,
        "uploads",
        settle=Mock(side_effect=AssertionError("reconcile before release")),
    )

    def reconcile(ref, storage):
        events.append("reconcile-U")
        if reconciled:
            ref.reservation_settled_at = now
        return reconciled

    install(monkeypatch, "validation", reconcile_ready_upload=reconcile)
    monkeypatch.setattr(lane, "_clean_versions", lambda *args: events.append("clean-J"))

    @contextmanager
    def locked(*args, **kwargs):
        yield job, None, None, None, None

    monkeypatch.setattr(lane, "_locked_job", locked)
    job.save = lambda **kwargs: None
    install(
        monkeypatch,
        "capacity_db",
        lock_capacity=lambda **_: None,
        release_locked=lambda *args, **kwargs: events.append("release-J") or True,
    )
    assert lane._cleanup(job, now) is reconciled
    assert events == (
        ["termination", "reconcile-U", "clean-J", "release-J"]
        if reconciled
        else ["termination", "reconcile-U"]
    )


def test_replayed_segment_cleanup_preserves_first_erased_time(lane, monkeypatch):
    now = datetime.now(UTC)
    first = now - timedelta(minutes=5)
    row = Row(id=uuid4(), attachment_id=None, state="erased", erased_at=first)
    row.save = lambda **kwargs: None
    job = Row(
        id=uuid4(),
        scope_id=uuid4(),
        upload_id=None,
        state="failed",
        cleanup_not_before=first,
        lease_epoch=1,
        storage_binding_id=uuid4(),
    )
    job.save = lambda **kwargs: None
    monkeypatch.setattr(lane.sandbox, "cleanup_import_scratch", lambda *args: True)
    storage = NS(
        erase_all_versions=lambda *args: NS(empty=True, retryable=False, checked_at=now)
    )
    install(
        monkeypatch, "readiness", resolve_historical=lambda *args, **kwargs: storage
    )
    install(monkeypatch, "uploads")
    install(monkeypatch, "validation")
    install(
        monkeypatch,
        "capacity_db",
        lock_capacity=lambda **_: None,
        release_locked=lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(lane, "_allocations", lambda *args: [("segment", row)])
    monkeypatch.setattr(lane, "_allocation", lambda *args: row)
    monkeypatch.setattr(lane, "_locator", lambda *args: object())

    @contextmanager
    def locked(*args, **kwargs):
        yield job, None, None, None, None

    monkeypatch.setattr(lane, "_locked_job", locked)
    assert lane._cleanup(job, now)
    assert lane._cleanup(job, now + timedelta(seconds=1))
    assert row.erased_at == first


@pytest.mark.parametrize("whole", [False, True])
def test_l16_rejected_before_admission_or_source_body(lane, monkeypatch, whole):
    body = request(whole)
    desc = body["recording" if whole else "upload"]
    desc["mime_type"] = "audio/L16"
    scope = lane.AttachmentScope.objects.create(policy_version=1, deletion_generation=0)
    importer = lane.Importer("user", uuid4())
    lane.CaptureSession.objects.create(
        scope=scope,
        client_id=desc["association"]["capture_session_id"],
        correlation_nonce=desc["association"]["correlation_nonce"],
        writer_kind=importer.kind,
        writer_id=importer.id,
    )
    facts = NS(
        byte_length=desc["declared_bytes"],
        mime_type="audio/L16",
        parent_trace_id=lane.uuid.UUID(desc["association"]["parent"]["trace_id"]),
        parent_span_id=desc["association"]["parent"]["span_id"],
        creator_kind=importer.kind,
        creator_id=importer.id,
        direction=desc["direction"],
    )
    monkeypatch.setattr(lane, "_admit_policy", lambda *args: None)
    install(
        monkeypatch,
        "source_providers",
        _envelope_provider=lambda *_: None,
        resolve=lambda **_: facts,
        open_source=Mock(side_effect=AssertionError("no source fetch")),
    )
    install(monkeypatch, "uploads")
    admission = Mock(side_effect=AssertionError("no admission"))
    install(monkeypatch, "readiness", require_new_admission=admission)
    install(
        monkeypatch, "capacity_db", count_attempt=admission, reserve_locked=admission
    )

    def reject(mime):
        assert mime == "audio/L16"
        raise lane.AttachmentError("unsupported_media", 422)

    install(monkeypatch, "validation", require_supported_plan=reject)
    with pytest.raises(lane.AttachmentError) as error:
        lane.submit(importer, scope_id=scope.id, body=body)
    assert error.value.code == "unsupported_media"
    admission.assert_not_called()
    assert not lane.AttachmentImportJob.objects.rows


@pytest.mark.parametrize("expiry", [False, True])
def test_single_retirement_updates_both_locked_owners(lane, monkeypatch, expiry):
    job, _, _, fence, *_ = lease_fixture(lane, monkeypatch)
    upload = lane.AttachmentUpload.objects.create(state="validating")
    asset = lane.Attachment.objects.create(state="validating")
    job.upload_id, job.recording_id = upload.id, None

    @contextmanager
    def locked(*args, **kwargs):
        yield job, None, None, upload, asset

    monkeypatch.setattr(lane, "_locked_job", locked)
    if expiry:
        assert lane._expire(job.id, job.deadline)
        expected = "expired"
    else:
        lane._retire(job.id, fence, reason="validation_failed", terminal="rejected")
        expected = "rejected"
    assert asset.state == upload.state == job.state == expected
