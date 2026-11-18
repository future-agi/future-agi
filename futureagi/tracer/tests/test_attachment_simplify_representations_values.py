"""Database-free executable service tests; doubles are NOT PostgreSQL proof.

Loads only scoped services and real frozen values. No application startup,
production settings, SQL, external storage or secrets. The sandbox tests below
execute the real confined PCM worker against synthetic distinct-channel bytes.
"""

import contextlib
import copy
import hashlib
import importlib.util
import io
import struct
import sys
import types
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ROOT / "services" / "attachments"
NOW = datetime(2026, 11, 16, 21, 0, tzinfo=UTC)


def uid():
    return uuid.uuid4()


class Query:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *expressions, **conditions):
        rows = self.rows
        for name, value in conditions.items():
            name = "id" if name == "pk" else name
            if name.endswith("__isnull"):
                rows = [
                    row
                    for row in rows
                    if (getattr(row, name[:-8], None) is None) == value
                ]
            elif "__" not in name:
                rows = [row for row in rows if getattr(row, name, None) == value]
        return Query(rows)

    def select_related(self, *args):
        return self

    def select_for_update(self):
        return self

    def order_by(self, *args):
        return self

    def first(self):
        return next(iter(self.rows), None)

    def get(self, **conditions):
        row = self.filter(**conditions).first()
        if row is None:
            raise LookupError
        return row

    def exists(self):
        return bool(self.rows)

    def __iter__(self):
        return iter(self.rows)

    def update(self, **values):
        for row in self.rows:
            row.__dict__.update(values)
        return len(self.rows)


class Rows(Query):
    def create(self, **values):
        values.setdefault("id", uid())
        for name in ("attachment", "scope", "occurrence", "media_session"):
            if name in values:
                values[name + "_id"] = values[name].id
        row = NS(**values)
        row.save = Mock()
        self.rows.append(row)
        return row

    def get_or_create(self, *, defaults, **query):
        existing = self.filter(**query).first()
        return (
            (existing, False) if existing else (self.create(**defaults, **query), True)
        )


@pytest.fixture
def service(monkeypatch, tmp_path):
    # Package namespaces deliberately bypass tracer.models/__init__.py and all
    # full-application initialization. These tests cannot verify DB triggers.
    for name, path in (
        ("tracer", ROOT),
        ("tracer.models", ROOT / "models"),
        ("tracer.services", ROOT / "services"),
        ("tracer.services.attachments", SERVICES),
        ("tracer.views", ROOT / "views"),
    ):
        module = types.ModuleType(name)
        module.__path__ = [str(path)]
        monkeypatch.setitem(sys.modules, name, module)

    def shim(name, **values):
        module = types.ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)
        return module

    def load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        return module

    from django.conf import settings

    if not settings.configured:
        settings.configure(
            SECRET_KEY="synthetic-isolated-only",
            DEFAULT_CHARSET="utf-8",
            USE_TZ=True,
            REST_FRAMEWORK={},
        )
    monkeypatch.setattr(settings, "FI_ATTACHMENTS_ENABLED", True, raising=False)
    monkeypatch.setattr(
        settings, "FI_ATTACHMENTS_WORK_DIR", str(tmp_path), raising=False
    )
    state = NS(
        READY="ready",
        DELETED="deleted",
        PENDING="pending_upload",
        VALIDATING="validating",
        FAILED="failed",
        REJECTED="rejected",
        EXPIRED="expired",
    )
    models = {
        name: type(name, (), {"objects": Rows([])})
        for name in (
            "Attachment",
            "AttachmentOccurrence",
            "AttachmentScope",
            "CaptureSession",
            "AttachmentWriteGrant",
            "AttachmentMediaSession",
            "AttachmentPlaybackGrant",
        )
    }
    shim("tracer.models.attachment", **models, AttachmentState=state)
    derivative_model = type("AttachmentRecordingDerivative", (), {"objects": Rows([])})
    shim(
        "tracer.models.attachment_recording",
        AttachmentRecordingDerivative=derivative_model,
    )
    shim("tracer.services.attachments.accounting", audit=Mock())
    shim(
        "tracer.services.attachments.bootstrap",
        LIMITS={"max_upload_bytes": 20 * 1024 * 1024},
        check_session=Mock(),
        session_policy=lambda s: None,
    )
    contract = load("tracer.services.attachments.contract", SERVICES / "contract.py")
    interfaces = load(
        "tracer.services.attachments.interfaces", SERVICES / "interfaces.py"
    )
    capacity = load("tracer.services.attachments.capacity", SERVICES / "capacity.py")
    monkeypatch.setattr(contract, "admit_audio", Mock())
    shim(
        "tracer.services.attachments.occurrences",
        Parent=lambda trace_id, span_id: NS(trace_id=trace_id, span_id=span_id),
        Viewer=lambda *a: NS(user_id=a[0]),
        get_occurrence=Mock(),
        not_found=lambda: contract.AttachmentError("not_found", 404),
        scope_for=Mock(),
    )
    store_module = shim("tracer.services.attachments.storage", for_binding=Mock())
    capacity_db = shim(
        "tracer.services.attachments.capacity_db",
        lock_capacity=Mock(),
        reserve_locked=Mock(),
        release_locked=Mock(return_value=True),
        count_attempt=Mock(return_value=True),
    )
    sandbox = load("tracer.services.attachments.sandbox", SERVICES / "sandbox.py")
    wire = load("tracer.services.attachments.wire", SERVICES / "wire.py")
    derivatives = load(
        "tracer.services.attachments.derivatives", SERVICES / "derivatives.py"
    )
    playback = load("tracer.services.attachments.playback", SERVICES / "playback.py")
    # transaction.atomic is decorator on issue/exchange: unwrap only that real
    # DB wrapper in isolated tests, not any of their authorization bodies.
    playback.issue = playback.issue.__wrapped__
    playback.exchange = playback.exchange.__wrapped__
    monkeypatch.setattr(
        derivatives.transaction, "get_connection", lambda: NS(in_atomic_block=True)
    )
    monkeypatch.setattr(derivatives.timezone, "now", lambda: NOW)
    monkeypatch.setattr(derivatives.transaction, "atomic", contextlib.nullcontext)
    egress = shim(
        "tracer.services.attachments.egress",
        admit=Mock(),
        consume=Mock(),
        EgressDenied=type("EgressDenied", (Exception,), {}),
    )
    media = load(
        "tracer.views.attachment_media", ROOT / "views" / "attachment_media.py"
    )
    load("tracer.services.attachments.references", SERVICES / "references.py")
    load("tracer.services.attachments.binding", SERVICES / "binding.py")
    load("tracer.services.attachments.reads", SERVICES / "reads.py")
    projection = load(
        "tracer.services.attachments.view_enrichment", SERVICES / "view_enrichment.py"
    )
    return NS(
        d=derivatives,
        p=playback,
        m=media,
        v=projection,
        i=interfaces,
        c=contract,
        cap=capacity,
        db=capacity_db,
        models=models,
        rows=derivative_model.objects,
        sandbox=sandbox,
        wire=wire,
        storage=store_module,
        egress=egress,
    )


def asset(**overrides):
    values = {
        "id": uid(),
        "client_id": uid(),
        "scope_id": uid(),
        "storage_binding_id": uid(),
        "state": "ready",
        "deleted_at": None,
        "policy_version": 1,
        "deletion_generation": 0,
        "verified_blob_id": uid(),
        "verified_at": NOW,
        "verified_storage_key": "original/asset",
        "verified_storage_version": "v1",
        "verified_sha256": "a" * 64,
        "verified_byte_length": 3244,
        "verified_mime_type": "audio/wav",
        "verified_sample_rate": 8000,
        "verified_channels": 2,
        "verified_frame_count": 800,
        "source_kind": "Bytes",
    }
    values.update(overrides)
    return NS(**values)


def selection(service, **changes):
    values = {
        "representation": "original",
        "identity": service.i.ObjectIdentity(uid(), "serving", "original/test", "v1"),
        "byte_length": 8,
        "mime_type": "audio/wav",
        "sha256": "a" * 64,
        "derivative_id": None,
    }
    values.update(changes)
    return service.i.RepresentationSelection(**values)


def test_equal_client_ids_across_scopes_have_disjoint_preput_rows(service):
    first = asset()
    second = asset(
        client_id=first.client_id, storage_binding_id=first.storage_binding_id
    )
    one = service.d.enqueue_for_asset_locked(first)
    two = service.d.enqueue_for_asset_locked(second)
    assert len(one) == len(two) == 3
    assert set(one).isdisjoint(two)
    assert service.d.enqueue_for_asset_locked(first) == one
    assert len(service.rows.rows) == 6
    for row in service.rows.rows:
        assert row.storage_object_id == row.id
        assert row.storage_key == f"original/{row.id}"
        assert row.quarantine_key == f"quarantine/{row.id}"
        assert row.reservation_charge_bytes == 0
        assert row.reservation_pool_key == "global:derivatives"
        assert row.reservation_expires_at == NOW + timedelta(hours=3)


def test_enqueue_non_audio_does_not_make_audio_facts(service):
    image = asset(
        verified_mime_type="image/png",
        verified_sample_rate=None,
        verified_channels=None,
        verified_frame_count=None,
    )
    assert service.d.enqueue_for_asset_locked(image) == ()


def test_unbounded_original_remains_playable_but_derivative_plan_is_bounded(service):
    original = asset(verified_frame_count=291744)  # actual 36.468s @8kHz preserved
    occurrence = NS(attachment=original, scope_id=original.scope_id)
    assert (
        service.p.resolve_representation(
            occurrence=occurrence, representation="playable"
        ).identity.version
        == "v1"
    )
    service.d.enqueue_for_asset_locked(original)
    with pytest.raises(service.c.AttachmentError, match="too_large"):
        service.d._plan(original, "channel-0")


@pytest.mark.parametrize("channel", [None, -1, 2, True, "0"])
def test_channel_request_strict_and_no_original_fallback(service, channel):
    original = asset()
    with pytest.raises(service.c.AttachmentError, match="not_found"):
        service.p.resolve_representation(
            occurrence=NS(attachment=original, scope_id=original.scope_id),
            representation="channel",
            channel=channel,
        )


@pytest.mark.parametrize("channel", [0, 1])
def test_channel_resolution_exact_asset_selection(service, channel):
    original = asset()
    service.d.enqueue_for_asset_locked(original)
    row = service.rows.rows[channel]
    row.state, row.published_at, row.storage_version = "ready", NOW, "mono-v1"
    row.byte_length, row.mime_type, row.sha256 = 1644, "audio/wav", "b" * 64
    found = service.p.resolve_representation(
        occurrence=NS(attachment=original, scope_id=original.scope_id),
        representation="channel",
        channel=channel,
    )
    assert found.derivative_id == row.id
    assert found.identity.binding_id == original.storage_binding_id
    assert found.representation == f"channel-{channel}"
    assert service.p.servable(found, "channel").media
    assert service.p.servable(found, "original") is None


def test_unpublished_derivative_never_resolves(service):
    original = asset()
    service.d.enqueue_for_asset_locked(original)
    with pytest.raises(service.c.AttachmentError, match="not_found"):
        service.d.identity_for(attachment_id=original.id, representation="channel-0")


def test_peak_capacity_uses_disjoint_frozen_peak(service):
    original = asset()
    _, length, decoded, vector = service.d._plan(original, "channel-0")
    assert vector == service.cap.derivative_peak(
        source_bytes=3244, output_bytes=length, decoder_scratch_bytes=decoded
    )
    assert vector.objects == 6 and vector.links == 0 and vector.transfers == 1


def test_stale_returned_version_cannot_erase_current_publication(service, monkeypatch):
    original = asset()
    row = NS(
        id=uid(),
        scope_id=original.scope_id,
        storage_key="original/derivative",
        quarantine_key="quarantine/derivative",
        storage_version="current",
    )

    @contextlib.contextmanager
    def locked(*args, **kwargs):
        yield None, original, row

    monkeypatch.setattr(service.d, "_locked", locked)
    store = Mock()
    stale = service.i.ObjectIdentity(
        original.storage_binding_id, "serving", row.storage_key, "stale"
    )
    current = service.i.ObjectIdentity(
        original.storage_binding_id, "serving", row.storage_key, "current"
    )
    assert service.d._cleanup_returned(row, stale, store) == "stale_unowned"
    store.erase_version.assert_called_once_with(stale)
    assert service.d._cleanup_returned(row, current, store) == "already_owned"
    assert store.erase_version.call_count == 1
    store.erase_all_versions.assert_not_called()
    assert row.storage_version == "current"


def test_stale_foreign_key_never_deleted(service, monkeypatch):
    original = asset()
    row = NS(
        id=uid(),
        scope_id=original.scope_id,
        storage_key="original/own",
        quarantine_key="quarantine/own",
        storage_version=None,
    )

    @contextlib.contextmanager
    def locked(*args, **kwargs):
        yield None, original, row

    monkeypatch.setattr(service.d, "_locked", locked)
    store = Mock()
    with pytest.raises(service.c.AttachmentError):
        service.d._cleanup_returned(
            row,
            service.i.ObjectIdentity(
                original.storage_binding_id, "serving", "original/neighbor", "v1"
            ),
            store,
        )
    store.erase_version.assert_not_called()


def test_stream_drops_bytes_revoked_during_read(service):
    chosen = selection(service)
    check = Mock(
        side_effect=[(None, chosen, None), service.c.AttachmentError("not_found", 404)]
    )
    body = io.BytesIO(b"12345678")
    store = NS(open_range=Mock(return_value=body))
    stream = service.m.RevocableBody(check, store, chosen, 0, 7)
    assert list(stream) == []
    assert body.closed
    store.open_range.assert_called_once_with(
        chosen.identity, start=0, end=7, expected_length=8
    )


def test_stream_drops_changed_selection_and_slow_reads(service, monkeypatch):
    chosen = selection(service)
    other = selection(service)
    store = NS(open_range=Mock(return_value=io.BytesIO(b"12345678")))
    stream = service.m.RevocableBody(
        Mock(return_value=(None, other, None)), store, chosen, 0, 7
    )
    assert list(stream) == []
    store.open_range.assert_not_called()
    clock = iter([0, 0, 0, 0, 6])
    monkeypatch.setattr(service.m.time, "monotonic", lambda: next(clock))
    stream = service.m.RevocableBody(
        Mock(return_value=(None, chosen, None)), store, chosen, 0, 7
    )
    assert list(stream) == []


@pytest.mark.parametrize(
    "method,headers",
    [
        ("GET", {}),
        ("HEAD", {}),
        ("GET", {"Range": "bytes=0-2"}),
        ("GET", {"If-None-Match": "*"}),
    ],
)
def test_denied_gateway_never_opens_storage_or_discloses_metadata(
    service, monkeypatch, method, headers
):
    monkeypatch.setattr(
        service.p,
        "authorize",
        Mock(side_effect=service.c.AttachmentError("not_found", 404)),
    )
    request = NS(method=method, headers=headers, COOKIES={service.p.COOKIE_NAME: "x"})
    response = service.m.media_response(
        request,
        media_session_id=str(uid()),
        occurrence_id=str(uid()),
        grant_token="x",
        authority=object(),
        origin="https://app.example.test",
    )
    assert response.status_code == 404
    assert "Content-Range" not in response
    service.storage.for_binding.assert_not_called()


def test_gateway_historical_binding_and_current_grant_selection(service, monkeypatch):
    chosen = selection(service)
    grant, viewer = NS(id=uid(), representation="original"), NS(user_id=uid())
    monkeypatch.setattr(
        service.p, "authorize", Mock(return_value=(grant, chosen, viewer))
    )
    store = NS(
        binding_id=chosen.identity.binding_id,
        verify_identity=Mock(),
        open_range=Mock(return_value=io.BytesIO(b"12345678")),
    )
    service.storage.for_binding.return_value = store
    response = service.m.media_response(
        NS(method="GET", headers={"Range": "bytes=2-4"}, COOKIES={}),
        media_session_id=str(uid()),
        occurrence_id=str(uid()),
        grant_token="x",
        authority=object(),
        origin="https://app.example.test",
    )
    assert response.status_code == 206
    assert response["Content-Length"] == "3"
    service.storage.for_binding.assert_called_once_with(
        chosen.identity.binding_id, operation="read"
    )
    assert b"".join(response.streaming_content) == b"123"


def manifest(layout="mixed", partial=False):
    recording = str(uid())
    tracks = [
        {"track_id": str(uid()), "channels": [0], "direction": "mixed", "role": "mixed"}
    ]
    if layout != "mixed":
        tracks = [
            {
                "track_id": str(uid()),
                "channels": [i],
                "direction": "input" if i == 0 else "output",
                "role": "user" if i == 0 else "assistant",
            }
            for i in range(2)
        ]
    segments, gaps = [], []
    for track in tracks if layout == "separate_tracks" else [None]:
        for index in range(2):
            part = {"start_ms": index * 1000, "duration_ms": 1000}
            if layout == "separate_tracks":
                part["track_id"] = track["track_id"]
            if partial and index == 1:
                gaps.append(dict(part, reason="incomplete"))
            else:
                segments.append(
                    dict(part, occurrence_id=str(uid()), index=len(segments))
                )
    segments.sort(key=lambda p: (p["start_ms"], p["index"]))
    for index, segment in enumerate(segments):
        segment["index"] = index
    return {
        "schema_version": 1,
        "recording_id": recording,
        "timebase": "session_relative_ms",
        "sample_format": "s16le",
        "sample_rate_hz": 8000,
        "channels": len(tracks),
        "codec": "pcm",
        "container": "wav",
        "layout": layout,
        "tracks": tracks,
        "segments": segments,
        "gaps": gaps,
        "playout": [],
        "capture_complete": not partial,
        "duration_ms": 2000,
    }


@pytest.mark.parametrize("layout", ["mixed", "stereo", "separate_tracks"])
@pytest.mark.parametrize("partial", [False, True])
def test_manifest_projection_keeps_layout_gaps_and_capture_truth(
    service, layout, partial
):
    data = manifest(layout, partial)
    before = copy.deepcopy(data)
    assert service.wire.is_valid("RecordingManifest", data)
    assert service.v._playable(
        data, [s["occurrence_id"] for s in data["segments"]], data["recording_id"]
    )
    assert data == before
    assert not service.v._playable(
        data, [s["occurrence_id"] for s in data["segments"]][1:], data["recording_id"]
    )


def test_full_manifest_not_truncated_to_metadata_page(service, monkeypatch):
    data = manifest()
    data["segments"] = [
        {
            "occurrence_id": str(uid()),
            "index": i,
            "start_ms": i * 30000,
            "duration_ms": 30000,
        }
        for i in range(120)
    ]
    data["duration_ms"] = 3600000
    trace, span, project = str(uid()), "a" * 16, str(uid())
    root = {
        "trace_id": trace,
        "id": span,
        "attachments": [
            {
                "reference": {
                    "occurrence_id": data["segments"][0]["occurrence_id"],
                    "purpose": "conversation_recording",
                    "modality": "audio",
                },
                "recording_id": data["recording_id"],
                "status": "partial",
            }
        ],
    }
    monkeypatch.setattr(service.v, "enrich_observations", Mock())
    request = NS(organization=NS(id=uid()), workspace=NS(id=uid()))
    reader = NS(recording_manifest=Mock(return_value=data))
    result = {"project_id": project, "observation_span": [root]}
    service.v.enrich_voice_detail(request=request, result=result, reader=reader)
    projection = result["recording_attachment"]
    assert projection["state"] == "ready"
    assert len(projection["manifest"]["segments"]) == 120
    assert projection["parent"] == {"trace_id": trace, "span_id": span}
    # A denial of any later segment is represented by a denied complete
    # manifest from the authoritative reader, never by dropping that segment.
    reader.recording_manifest.return_value = None
    service.v.enrich_voice_detail(request=request, result=result, reader=reader)
    assert result["recording_attachment"]["state"] != "ready"
    assert "manifest" not in result["recording_attachment"]


def test_real_confined_channel_output_has_distinct_actual_samples(service, tmp_path):
    left = [1000, -2000, 3000, -4000] * 200
    right = [-7000, 6000, -5000, 4000] * 200
    pcm = b"".join(struct.pack("<hh", a, b) for a, b in zip(left, right, strict=True))
    payload = (
        b"RIFF"
        + struct.pack("<I", 36 + len(pcm))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 2, 8000, 32000, 4, 16)
        + b"data"
        + struct.pack("<I", len(pcm))
        + pcm
    )
    source = tmp_path / "stereo.wav"
    source.write_bytes(payload)
    for channel, expected in enumerate((left, right)):
        with (tmp_path / f"mono-{channel}.wav").open("w+b") as output:
            facts = service.sandbox.run(
                "split",
                str(source),
                channel,
                output=output,
                output_limit=44 + len(left) * 2,
            )
            output.seek(0)
            mono = output.read()
        assert facts["channels"] == 1 and facts["frame_count"] == len(left)
        assert list(struct.unpack(f"<{len(left)}h", mono[44:])) == expected
        assert hashlib.sha256(mono).digest() != hashlib.sha256(payload).digest()


def test_service_source_uses_only_frozen_asset_entities(service):
    source = (SERVICES / "derivatives.py").read_text()
    playback = (SERVICES / "playback.py").read_text()
    for removed in ("AttachmentBlob", "UploadReservation", "AttachmentUsageReceipt"):
        assert removed not in source + playback
    assert "uuid.uuid5" not in source
    assert "erase_upload(" not in source
    assert "AttachmentRecordingSegment.objects" not in source
    assert "selected_derivative_id=selection.derivative_id" in playback
    assert "selected_channel=channel" in playback
    assert "media_session=session" in playback
    assert "token_digest=digest(cookie)" in playback
    assert "scope_for(viewer, authority)" in playback


@pytest.mark.parametrize("asset_state", ["ready", "deleted"])
def test_linked_survivor_prevents_all_derivative_erasure(
    service, monkeypatch, asset_state
):
    original = asset(
        state=asset_state, deleted_at=NOW if asset_state == "deleted" else None
    )
    service.models["AttachmentScope"].objects.rows.append(NS(id=original.scope_id))
    service.models["Attachment"].objects.rows.append(original)
    service.models["AttachmentOccurrence"].objects.rows.append(
        NS(id=uid(), attachment_id=original.id, deleted_at=None)
    )
    storage = Mock()
    monkeypatch.setattr(service.d, "_storage", storage)
    assert (
        service.d.erase_for_asset(
            attachment_id=original.id, scope_id=original.scope_id, now=NOW
        )
        is False
    )
    storage.assert_not_called()


def work_row(service, original, **changes):
    service.d.enqueue_for_asset_locked(original)
    row = service.rows.rows[0]
    row.__dict__.update(
        lease_token=None,
        lease_epoch=0,
        lease_until=None,
        write_attempts=0,
        write_deadline_at=None,
        storage_version=None,
        reservation_settled_at=None,
        published_at=None,
    )
    row.__dict__.update(changes)
    return row


def lock_double(service, monkeypatch, original, row):
    @contextlib.contextmanager
    def locked(*args, **kwargs):
        yield "locked-capacity", original, row

    monkeypatch.setattr(service.d, "_locked", locked)


def test_claim_reserves_once_and_retains_charge_on_takeover(service, monkeypatch):
    original = asset()
    row = work_row(service, original)
    lock_double(service, monkeypatch, original, row)
    first = service.d._claim(row, {})
    assert first.epoch == 1 and row.state == "recovering"
    assert row.cleanup_not_before >= first.expires_at + timedelta(seconds=15)
    service.db.reserve_locked.assert_called_once()
    assert service.d._claim(row, {}) is None
    row.reservation_charge_bytes = 20000  # persisted by concrete capacity owner
    row.lease_until = NOW - timedelta(seconds=1)
    second = service.d._claim(row, {})
    assert second.epoch == 2 and second.token != first.token
    assert service.db.reserve_locked.call_count == 1


def test_prepare_records_bounded_permit_before_network_and_caps_writes(
    service, monkeypatch
):
    original = asset()
    row = work_row(
        service,
        original,
        state="writing",
        lease_token=uid(),
        lease_epoch=1,
        lease_until=NOW + timedelta(minutes=14),
    )
    fence = service.i.LeaseFence(
        "derivative", row.id, row.lease_token, 1, row.lease_until
    )
    lock_double(service, monkeypatch, original, row)
    for attempt in (1, 2, 3):
        permit = service.d._prepare(row, fence, {})
        assert row.write_attempts == permit.write_attempt == attempt
        assert permit.deadline_at <= NOW + timedelta(seconds=30)
        assert permit.locator.key == row.storage_key
        assert row.cleanup_not_before >= fence.expires_at + timedelta(seconds=15)
        assert "write_attempts" in row.save.call_args.kwargs["update_fields"]
    with pytest.raises(service.c.AttachmentError):
        service.d._prepare(row, fence, {})
    assert row.write_attempts == 3


def test_record_current_version_immutable_and_stale_cannot_replace(
    service, monkeypatch
):
    original = asset()
    row = work_row(
        service,
        original,
        state="writing",
        lease_token=uid(),
        lease_epoch=1,
        lease_until=NOW + timedelta(minutes=14),
    )
    lock_double(service, monkeypatch, original, row)
    fence = service.i.LeaseFence(
        "derivative", row.id, row.lease_token, 1, row.lease_until
    )
    permit = service.d._prepare(row, fence, {})
    identity = service.i.ObjectIdentity(
        original.storage_binding_id, "serving", row.storage_key, "published"
    )
    facts = {"sample_rate": 8000, "frame_count": 800, "channels": 1}
    assert (
        service.d._record(row, permit, identity, facts, "b" * 64, 1644, {})
        == "recorded"
    )
    assert row.state == "ready" and row.lease_token is None
    before = dict(row.__dict__)
    other = service.i.ObjectIdentity(
        original.storage_binding_id, "serving", row.storage_key, "late-old"
    )
    assert (
        service.d._record(row, permit, other, facts, "c" * 64, 1644, {})
        == "stale_unowned"
    )
    assert row.__dict__ == before
    assert (
        service.d._record(row, permit, identity, facts, "b" * 64, 1644, {})
        == "already_owned"
    )


def test_cleanup_streamed_asset_includes_unpublished_rows_and_keeps_locators(
    service, monkeypatch
):
    original = asset()
    row = work_row(
        service,
        original,
        state="cleanup",
        cleanup_not_before=NOW - timedelta(seconds=1),
    )
    original.state, original.deleted_at = "deleted", NOW
    lock_double(service, monkeypatch, original, row)
    store = NS(
        erase_all_versions=Mock(
            return_value=service.i.EraseResult(True, False, None, NOW)
        )
    )
    with service.d._scratch_lock(row) as work:
        assert service.d._settle(row, store, work)
    assert row.state == "erased"
    assert row.attachment_id == original.id
    assert store.erase_all_versions.call_count == 2
    assert {c.args[0].key for c in store.erase_all_versions.call_args_list} == {
        row.storage_key,
        row.quarantine_key,
    }
    assert row.storage_object_id == row.id
    service.db.release_locked.assert_called_once()


def test_cleanup_failure_and_future_write_horizon_keep_charge(service, monkeypatch):
    original = asset()
    row = work_row(
        service,
        original,
        state="cleanup",
        cleanup_not_before=NOW + timedelta(seconds=1),
    )
    lock_double(service, monkeypatch, original, row)
    store = NS(
        erase_all_versions=Mock(
            return_value=service.i.EraseResult(False, True, "cleanup_unavailable", NOW)
        )
    )
    with service.d._scratch_lock(row) as work:
        assert service.d._settle(row, store, work) is False
        store.erase_all_versions.assert_not_called()
        row.cleanup_not_before = NOW - timedelta(seconds=1)
        assert service.d._settle(row, store, work) is False
    assert row.state == "cleanup"
    service.db.release_locked.assert_not_called()


def test_ready_cleanup_preserves_published_version(service, monkeypatch):
    original = asset()
    row = work_row(
        service,
        original,
        state="ready",
        published_at=NOW,
        storage_version="keep",
        cleanup_not_before=NOW - timedelta(seconds=1),
    )
    lock_double(service, monkeypatch, original, row)

    def versions(locator, **kwargs):
        if locator.purpose == "quarantine":
            return service.i.VersionPage((), (), None)
        return service.i.VersionPage(
            tuple(
                service.i.ObjectIdentity(
                    locator.binding_id, locator.purpose, locator.key, version
                )
                for version in ("keep", "late")
            ),
            (),
            None,
        )

    store = NS(list_versions=versions, erase_version=Mock(), erase_all_versions=Mock())
    with service.d._scratch_lock(row) as work:
        assert service.d._settle(row, store, work)
    assert row.state == "ready" and row.storage_version == "keep"
    assert store.erase_version.call_args.args[0].version == "late"
    store.erase_all_versions.assert_not_called()


def test_scratch_symlink_refused_and_live_worker_holds_cleanup(service, tmp_path):
    original = asset()
    row = work_row(service, original)
    with service.d._scratch_lock(row):
        with pytest.raises(service.c.AttachmentError, match="conflict"):
            with service.d._scratch_lock(row):
                pytest.fail("accepted concurrent scratch owner")
    (tmp_path / "derivative" / str(row.id) / "1").symlink_to(
        tmp_path, target_is_directory=True
    )
    with service.d._scratch_lock(row) as work:
        with pytest.raises(service.c.AttachmentError, match="cleanup_unavailable"):
            service.d._clear_scratch(work)


def test_grant_issuance_persists_channel_and_derivative_after_authorization(
    service, monkeypatch
):
    original = asset()
    row = NS(
        id=uid(),
        client_id=uid(),
        scope_id=original.scope_id,
        attachment=original,
        trace_id=uid(),
        span_id="a" * 16,
    )
    scope = NS(
        id=original.scope_id,
        organization_id=uid(),
        policy_version=1,
        deletion_generation=0,
    )
    viewer = NS(user_id=uid(), application_session_id=uid())
    session = NS(
        id=uid(),
        user_id=viewer.user_id,
        application_session_id=viewer.application_session_id,
        organization_id=scope.organization_id,
        expires_at=NOW + timedelta(minutes=4),
    )
    chosen = selection(service, representation="channel-1", derivative_id=uid())
    monkeypatch.setattr(service.p, "bound_media_session", Mock(return_value=session))
    monkeypatch.setattr(
        service.p,
        "get_occurrence",
        Mock(return_value=(row, scope, NOW + timedelta(minutes=2))),
    )
    row.source_generation = 0

    def selected(**kwargs):
        assert service.p.get_occurrence.called
        assert kwargs["channel"] == 1
        return chosen

    monkeypatch.setattr(service.p, "resolve_representation", selected)
    reply = service.p.issue(
        viewer,
        object(),
        media_session=session,
        row=row,
        scope=scope,
        expiry=NOW + timedelta(minutes=3),
        representation="channel",
        channel=1,
    )
    saved = service.models["AttachmentPlaybackGrant"].objects.rows[0]
    assert saved.selected_channel == 1
    assert saved.selected_derivative_id == chosen.derivative_id
    assert saved.representation == "channel"
    assert reply["gateway_path"].startswith(service.p.session_path(session.id))
    assert str(row.client_id) in reply["gateway_path"]


@pytest.mark.parametrize("representation", ["channel-0", "channel-1", "peaks"])
def test_generate_streamed_asset_with_real_worker_and_frozen_storage_calls(
    service, monkeypatch, representation
):
    samples = [(1000, -3000), (2000, -4000)] * 400
    pcm = b"".join(struct.pack("<hh", *pair) for pair in samples)
    payload = (
        b"RIFF"
        + struct.pack("<I", 36 + len(pcm))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 2, 8000, 32000, 4, 16)
        + b"data"
        + struct.pack("<I", len(pcm))
        + pcm
    )
    original = asset(verified_sha256=hashlib.sha256(payload).hexdigest())
    service.d.enqueue_for_asset_locked(original)
    row = next(row for row in service.rows.rows if row.representation == representation)
    row.__dict__.update(
        lease_token=None,
        lease_epoch=0,
        lease_until=None,
        write_attempts=0,
        write_deadline_at=None,
        storage_version=None,
        reservation_settled_at=None,
        published_at=None,
    )
    lock_double(service, monkeypatch, original, row)
    monkeypatch.setattr(service.d, "_source_authority", Mock(return_value={}))
    readiness = types.ModuleType("tracer.services.attachments.readiness")
    readiness.require_decoder = Mock()
    monkeypatch.setitem(sys.modules, readiness.__name__, readiness)
    captured = {}

    def snapshot(identity, output, *, expected_length):
        assert row.state == "writing" and row.lease_token is not None
        assert service.db.reserve_locked.called
        assert identity == service.d._asset_facts(original).object.identity
        assert expected_length == len(payload)
        output.write(payload)
        return hashlib.sha256(payload).hexdigest()

    def promote(output, *, permit, expected_length, expected_sha256):
        assert row.write_attempts == permit.write_attempt == 1
        assert row.storage_key == permit.locator.key
        assert row.storage_object_id == row.id
        assert row.write_deadline_at == permit.deadline_at
        captured["bytes"] = output.read()
        assert len(captured["bytes"]) == expected_length
        assert hashlib.sha256(captured["bytes"]).hexdigest() == expected_sha256
        return service.i.ObjectIdentity(
            permit.locator.binding_id, "serving", permit.locator.key, "exact-produced"
        )

    store = NS(
        binding_id=original.storage_binding_id,
        verify_identity=Mock(),
        snapshot=snapshot,
        promote=promote,
        list_versions=Mock(return_value=service.i.VersionPage((), (), None)),
    )
    assert (
        service.d.generate(row.id, scope_id=original.scope_id, storage=store) == "ready"
    )
    assert row.storage_version == "exact-produced" and row.state == "ready"
    assert row.lease_token is None
    assert row.unowned_cleanup_due_at > NOW
    readiness.require_decoder.assert_called_once()
    service.db.release_locked.assert_not_called()
    if representation != "peaks":
        channel = int(representation[-1])
        assert list(struct.unpack("<800h", captured["bytes"][44:])) == [
            pair[channel] for pair in samples
        ]
    else:
        assert row.mime_type == "application/octet-stream"
        assert len(row.facts["levels"]) == 3
