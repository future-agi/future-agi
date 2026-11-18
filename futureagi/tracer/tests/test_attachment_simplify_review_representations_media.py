"""Real confined encoded derivatives; database/storage IO doubles, not SQL proof."""

import contextlib
import hashlib
import importlib.util
import json
import math
import struct
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "_review_representation_helpers",
    ROOT / "test_attachment_simplify_representations_values.py",
)
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


@pytest.fixture
def service(monkeypatch, tmp_path):
    """Load only the owned service/reader modules, not unrelated HTTP adapters."""
    root = ROOT.parent
    services = root / "services/attachments"

    def shim(name, **values):
        module = ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)
        return module

    for name, path in (
        ("tracer", root),
        ("tracer.models", root / "models"),
        ("tracer.services", root / "services"),
        ("tracer.services.attachments", services),
    ):
        shim(name, __path__=[str(path)])

    def load(short):
        name = "tracer.services.attachments." + short
        spec = importlib.util.spec_from_file_location(name, services / (short + ".py"))
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        return module

    from django.conf import settings

    if not settings.configured:
        settings.configure(SECRET_KEY="synthetic-only", USE_TZ=True)
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
        name: type(name, (), {"objects": base.Rows([])})
        for name in (
            "Attachment",
            "AttachmentOccurrence",
            "AttachmentScope",
            "CaptureSession",
            "AttachmentWriteGrant",
        )
    }
    shim("tracer.models.attachment", **models, AttachmentState=state)
    row_model = type("AttachmentRecordingDerivative", (), {"objects": base.Rows([])})
    shim("tracer.models.attachment_recording", AttachmentRecordingDerivative=row_model)
    shim(
        "tracer.services.attachments.bootstrap",
        check_session=Mock(),
        session_policy=lambda s: None,
    )
    contract, interfaces, capacity = (
        load("contract"),
        load("interfaces"),
        load("capacity"),
    )
    monkeypatch.setattr(contract, "admit_audio", Mock())
    db = shim(
        "tracer.services.attachments.capacity_db",
        lock_capacity=Mock(),
        reserve_locked=Mock(),
        release_locked=Mock(return_value=True),
        count_attempt=Mock(return_value=True),
    )
    shim("tracer.services.attachments.storage", for_binding=Mock())
    sandbox, wire = load("sandbox"), load("wire")
    load("recordings")
    derivatives = load("derivatives")
    monkeypatch.setattr(
        derivatives.transaction, "get_connection", lambda: NS(in_atomic_block=True)
    )
    monkeypatch.setattr(derivatives.transaction, "atomic", contextlib.nullcontext)
    monkeypatch.setattr(derivatives.timezone, "now", lambda: base.NOW)
    for short in ("references", "binding", "reads"):
        load(short)
    projection = load("view_enrichment")
    return NS(
        d=derivatives,
        i=interfaces,
        c=contract,
        cap=capacity,
        db=db,
        models=models,
        rows=row_model.objects,
        sandbox=sandbox,
        wire=wire,
        v=projection,
    )


FORMATS = [
    ("audio/wav", "canonical", None),
    ("audio/wav", "ancillary", None),
    ("audio/wav", "float", None),
    ("audio/wav", "s24", None),
    ("audio/mpeg", "mp3", "libmp3lame"),
    ("audio/ogg", "ogg", "libopus"),
    ("audio/mp4", "mp4", "aac"),
    ("audio/flac", "flac", "flac"),
    ("audio/webm", "webm", "libopus"),
]


def corpus(tmp_path, container, codec):
    """Synthetic 48 kHz stereo: left 440 Hz, right 880 Hz, nonzero/distinct."""
    rate, count = 48000, 9600
    pcm = b"".join(
        struct.pack(
            "<hh",
            int(12000 * math.sin(2 * math.pi * 440 * n / rate)),
            int(6000 * math.sin(2 * math.pi * 880 * n / rate)),
        )
        for n in range(count)
    )
    path = tmp_path / ("corpus." + container)
    if codec is None:
        encoding, bits = 1, 16
        if container == "float":
            pcm = b"".join(
                struct.pack("<f", value / 32768)
                for value in struct.unpack(f"<{count * 2}h", pcm)
            )
            encoding, bits = 3, 32
        elif container == "s24":
            pcm = b"".join(
                (value << 8).to_bytes(3, "little", signed=True)
                for value in struct.unpack(f"<{count * 2}h", pcm)
            )
            bits = 24
        align = 2 * bits // 8
        extra = (
            b"JUNK" + struct.pack("<I", 4) + b"test"
            if container == "ancillary"
            else b""
        )
        path.write_bytes(
            b"RIFF"
            + struct.pack("<I", 36 + len(extra) + len(pcm))
            + b"WAVEfmt "
            + struct.pack("<IHHIIHH", 16, encoding, 2, rate, rate * align, align, bits)
            + extra
            + b"data"
            + struct.pack("<I", len(pcm))
            + pcm
        )
    else:
        import av

        with av.open(str(path), "w", format=container) as output:
            stream = output.add_stream(codec, rate=rate)
            stream.layout = "stereo"
            for start in range(0, count, 960):
                frame = av.AudioFrame(format="s16", layout="stereo", samples=960)
                frame.sample_rate, frame.pts = rate, start
                frame.planes[0].update(pcm[start * 4 : (start + 960) * 4])
                for packet in stream.encode(frame):
                    output.mux(packet)
            for packet in stream.encode(None):
                output.mux(packet)
    return path


def worker_setup(service, monkeypatch, path, mime, representation, **overrides):
    now = datetime.now(UTC)
    monkeypatch.setattr(service.d.timezone, "now", lambda: now)
    facts = service.sandbox.run("source", path, mime)
    payload = path.read_bytes()
    original = base.asset(
        verified_mime_type=mime,
        verified_byte_length=len(payload),
        verified_sha256=hashlib.sha256(payload).hexdigest(),
        verified_sample_rate=facts["sample_rate"],
        verified_channels=facts["channels"],
        verified_frame_count=facts["frame_count"],
        **overrides,
    )
    service.d.enqueue_for_asset_locked(original)
    row = next(r for r in service.rows.rows if r.representation == representation)
    row.__dict__.update(
        lease_token=None,
        lease_epoch=0,
        lease_until=None,
        write_attempts=0,
        write_deadline_at=None,
        storage_version=None,
        reservation_settled_at=None,
        published_at=None,
        erased_at=None,
    )
    base.lock_double(service, monkeypatch, original, row)
    monkeypatch.setattr(service.d, "_source_authority", lambda *_: {})
    readiness = ModuleType("tracer.services.attachments.readiness")
    readiness.require_decoder = Mock()
    monkeypatch.setitem(sys.modules, readiness.__name__, readiness)
    captured = {}

    def snapshot(identity, output, *, expected_length):
        assert service.db.reserve_locked.called
        assert identity == service.d._asset_facts(original).object.identity
        assert expected_length == len(payload)
        output.write(payload)
        return hashlib.sha256(payload).hexdigest()

    def promote(output, *, permit, expected_length, expected_sha256):
        assert row.write_attempts == permit.write_attempt == 1
        captured["bytes"] = output.read()
        assert len(captured["bytes"]) == expected_length
        assert hashlib.sha256(captured["bytes"]).hexdigest() == expected_sha256
        return service.i.ObjectIdentity(
            permit.locator.binding_id, "serving", permit.locator.key, "exact"
        )

    store = NS(
        binding_id=original.storage_binding_id,
        verify_identity=Mock(),
        snapshot=snapshot,
        promote=Mock(side_effect=promote),
        list_versions=Mock(return_value=service.i.VersionPage((), (), None)),
    )
    return original, row, facts, store, captured


@pytest.mark.parametrize("mime,container,codec", FORMATS)
@pytest.mark.parametrize("representation", ["channel-0", "channel-1", "peaks"])
def test_encoded_generation_real_samples(
    service,
    monkeypatch,
    tmp_path,
    mime,
    container,
    codec,
    representation,
    record_property,
):
    path = corpus(tmp_path, container, codec)
    original, row, facts, store, captured = worker_setup(
        service, monkeypatch, path, mime, representation
    )
    # Independent actual normalized PCM is the sample oracle (not original-byte alias).
    expected = bytearray()
    with service.sandbox.pcm_stream(
        path, mime, facts, deadline=datetime.now(UTC) + timedelta(minutes=2)
    ) as pcm:
        while block := pcm.read(256 * 1024):
            expected.extend(block)
    assert (
        service.d.generate(row.id, scope_id=original.scope_id, storage=store) == "ready"
    )
    produced = captured["bytes"]
    record_property(
        "format_proof",
        json.dumps(
            {
                "mime": mime,
                "container": container,
                "codec": codec,
                "representation": representation,
                "source_sha256": original.verified_sha256,
                "output_sha256": hashlib.sha256(produced).hexdigest(),
                "source_bytes": path.stat().st_size,
                "output_bytes": len(produced),
                "sample_rate": row.sample_rate,
                "channels": row.channels,
                "frame_count": row.frame_count,
                "sandbox": "unchanged seccomp allowlist",
            },
            sort_keys=True,
        ),
    )
    assert produced != path.read_bytes()
    _, output_bytes, decoded_bytes, peak = service.d._plan(original, representation)
    assert service.db.reserve_locked.call_args.kwargs["amount"] == peak
    assert peak.bytes == len(path.read_bytes()) + decoded_bytes + 4 * output_bytes
    service.db.release_locked.assert_not_called()
    samples = struct.unpack(f"<{len(expected) // 2}h", expected)
    assert samples[::2] != samples[1::2]
    assert max(samples) > 1000 and min(samples) < -1000
    if representation != "peaks":
        channel = int(representation[-1])
        assert row.channels == 1 and row.mime_type == "audio/wav"
        assert row.frame_count == facts["frame_count"]
        assert (
            struct.unpack(f"<{row.frame_count}h", produced[44:]) == samples[channel::2]
        )
        assert len(produced) == 44 + row.frame_count * 2
    else:
        expected_peaks = []
        base_frames = facts["sample_rate"] // 100
        lows, highs = [], []
        for channel in range(2):
            mono = samples[channel::2]
            windows = [
                mono[i : i + base_frames] for i in range(0, len(mono), base_frames)
            ]
            lows.append([min(w) for w in windows])
            highs.append([max(w) for w in windows])
        for factor in (1, 10, 100):
            for channel in range(2):
                for i in range(0, len(lows[channel]), factor):
                    expected_peaks.extend(
                        (
                            min(lows[channel][i : i + factor]),
                            max(highs[channel][i : i + factor]),
                        )
                    )
        assert produced == struct.pack(f"<{len(expected_peaks)}h", *expected_peaks)


@pytest.mark.parametrize(
    "field", ["verified_frame_count", "verified_sample_rate", "verified_channels"]
)
def test_verified_facts_mismatch_never_publishes(service, monkeypatch, tmp_path, field):
    path = corpus(tmp_path, "flac", "flac")
    original, row, _, store, _ = worker_setup(
        service, monkeypatch, path, "audio/flac", "peaks"
    )
    setattr(
        original,
        field,
        1 if field == "verified_channels" else getattr(original, field) + 1,
    )
    with pytest.raises(service.c.AttachmentError):
        service.d.generate(row.id, scope_id=original.scope_id, storage=store)
    store.promote.assert_not_called()
    service.db.release_locked.assert_not_called()


def test_real_sandbox_negative_controls(service):
    service.sandbox.self_test()


def cleanup_row(service, monkeypatch, **values):
    original = base.asset()
    row = base.work_row(
        service,
        original,
        state="cleanup",
        cleanup_not_before=base.NOW - timedelta(minutes=1),
        cleanup_checked_at=None,
        unowned_cleanup_due_at=base.NOW,
        erased_at=None,
        **values,
    )
    original.state, original.deleted_at = "deleted", base.NOW
    base.lock_double(service, monkeypatch, original, row)
    return row


def test_partial_cleanup_replay_preserves_erasure_stamp_and_rechecks_locators(
    service, monkeypatch
):
    row = cleanup_row(service, monkeypatch)
    store = NS(
        erase_all_versions=Mock(
            return_value=service.i.EraseResult(True, False, None, base.NOW)
        )
    )
    with service.d._scratch_lock(row) as work:
        assert service.d._settle(row, store, work)
        first = row.erased_at
        monkeypatch.setattr(
            service.d.timezone, "now", lambda: base.NOW + timedelta(seconds=1)
        )
        assert service.d._settle(row, store, work)
    assert row.erased_at == first
    assert store.erase_all_versions.call_count == 4


@pytest.mark.parametrize(
    "empty,retryable,reason,seconds",
    [
        (False, True, "cleanup_unavailable", 0),
        (False, False, None, 0),
        (True, True, None, 0),
        (True, False, "unknown", 0),
        (True, False, None, 1),
        (True, False, None, -120),
    ],
)
def test_unproven_cleanup_never_releases(
    service, monkeypatch, empty, retryable, reason, seconds
):
    row = cleanup_row(service, monkeypatch)
    store = NS(
        erase_all_versions=Mock(
            return_value=service.i.EraseResult(
                empty, retryable, reason, base.NOW + timedelta(seconds=seconds)
            )
        )
    )
    with service.d._scratch_lock(row) as work:
        assert service.d._settle(row, store, work) is False
    service.db.release_locked.assert_not_called()
    assert row.erased_at is None


@pytest.mark.parametrize(
    "outcome", ["none", "provider-error", "false", "true", "truthy", "receipt", "pending"]
)
def test_ready_cleanup_uses_exact_none_contract(service, monkeypatch, outcome):
    row = cleanup_row(service, monkeypatch)
    row.state, row.published_at, row.storage_version = "ready", base.NOW, "keep"
    identity = service.i.ObjectIdentity(
        row.attachment.storage_binding_id, "serving", row.storage_key, "late"
    )
    keep = service.i.ObjectIdentity(
        row.attachment.storage_binding_id, "serving", row.storage_key, "keep"
    )
    results = {
        "none": None,
        "false": False,
        "true": True,
        "truthy": object(),
        "receipt": service.i.EraseResult(True, False, None, base.NOW),
        "pending": service.i.EraseResult(False, True, "pending", base.NOW),
    }
    store = NS(
        list_versions=Mock(side_effect=lambda loc, **kw: service.i.VersionPage(
            (keep, identity) if loc.purpose == "serving" else (), (), None
        )),
        erase_version=Mock(return_value=results.get(outcome)),
    )
    if outcome == "provider-error":
        # Even an unwrapped provider exception must never become success.
        store.erase_version.side_effect = RuntimeError("provider-unavailable")
    with service.d._scratch_lock(row) as work:
        if outcome == "none":
            assert service.d._settle(row, store, work)
            service.db.release_locked.assert_called_once_with(
                "locked-capacity",
                owner=service.i.ReservationOwner("derivative", row.id, row.scope_id),
                cleanup_complete=True,
                now=base.NOW,
            )
            assert row.cleanup_checked_at == base.NOW
            assert row.unowned_cleanup_due_at is None
        else:
            error = RuntimeError if outcome == "provider-error" else service.c.AttachmentError
            with pytest.raises(error):
                service.d._settle(row, store, work)
            service.db.release_locked.assert_not_called()
            assert row.cleanup_checked_at is None
            assert row.unowned_cleanup_due_at == base.NOW
    store.erase_version.assert_called_once_with(identity)
    assert row.storage_version == "keep" and row.state == "ready"


@pytest.mark.parametrize("result", [None, False, True, {"empty": True}])
def test_all_version_cleanup_rejects_invalid_results(service, monkeypatch, result):
    row = cleanup_row(service, monkeypatch)
    store = NS(erase_all_versions=Mock(return_value=result))
    with service.d._scratch_lock(row) as work:
        assert service.d._settle(row, store, work) is False
    service.db.release_locked.assert_not_called()
    assert row.cleanup_checked_at is None and row.erased_at is None
    assert row.unowned_cleanup_due_at == base.NOW


@pytest.mark.parametrize("outstanding", ["exact-error", "exact-malformed", "list-error"])
def test_exact_success_does_not_settle_other_outstanding_versions(
    service, monkeypatch, outstanding
):
    row = cleanup_row(service, monkeypatch)
    row.state, row.published_at, row.storage_version = "ready", base.NOW, "keep"
    quarantine, serving = service.d._locators(row.attachment, row)
    first = service.i.ObjectIdentity(quarantine.binding_id, quarantine.purpose, quarantine.key, "first")
    second = service.i.ObjectIdentity(serving.binding_id, serving.purpose, serving.key, "second")

    def listing(locator, **kwargs):
        if locator == quarantine:
            return service.i.VersionPage((first,), (), None)
        if outstanding == "list-error":
            raise RuntimeError("listing-unavailable")
        return service.i.VersionPage((second,), (), None)

    store = NS(list_versions=Mock(side_effect=listing), erase_version=Mock(
        side_effect=[None, False if outstanding == "exact-malformed" else RuntimeError("provider-unavailable")]
    ))
    error = service.c.AttachmentError if outstanding == "exact-malformed" else RuntimeError
    with service.d._scratch_lock(row) as work, pytest.raises(error):
        service.d._settle(row, store, work)
    assert store.erase_version.call_args_list[0].args == (first,)
    assert store.erase_version.call_count == (1 if outstanding == "list-error" else 2)
    service.db.release_locked.assert_not_called()
    assert row.cleanup_checked_at is None and row.unowned_cleanup_due_at == base.NOW
    assert row.storage_version == "keep" and row.state == "ready"


@pytest.mark.parametrize("fence", ["horizon", "lease"])
def test_exact_cleanup_does_not_start_before_horizon_or_live_lease(service, monkeypatch, fence):
    row = cleanup_row(service, monkeypatch)
    row.state = "ready"
    if fence == "horizon":
        row.cleanup_not_before = base.NOW + timedelta(seconds=1)
    else:
        row.lease_until = base.NOW + timedelta(seconds=1)
    store = NS(list_versions=Mock(), erase_version=Mock())
    with service.d._scratch_lock(row) as work:
        assert service.d._settle(row, store, work) is False
    store.list_versions.assert_not_called()
    store.erase_version.assert_not_called()
    service.db.release_locked.assert_not_called()


@pytest.mark.parametrize("outcome", ["none", "error", "false", "receipt"])
def test_stale_returned_exact_cleanup_never_settles_owner(service, monkeypatch, outcome):
    row = cleanup_row(service, monkeypatch)
    identity = service.i.ObjectIdentity(
        row.attachment.storage_binding_id, "serving", row.storage_key, "late"
    )
    store = NS(erase_version=Mock(return_value={
        "none": None, "false": False,
        "receipt": service.i.EraseResult(True, False, None, base.NOW),
    }.get(outcome)))
    if outcome == "error":
        store.erase_version.side_effect = RuntimeError("provider-unavailable")
    if outcome == "none":
        assert service.d._cleanup_returned(row, identity, store) == "stale_unowned"
    else:
        error = RuntimeError if outcome == "error" else service.c.AttachmentError
        with pytest.raises(error):
            service.d._cleanup_returned(row, identity, store)
    service.db.release_locked.assert_not_called()
    assert row.cleanup_checked_at is None and row.unowned_cleanup_due_at == base.NOW


@pytest.mark.parametrize(
    "kind,code,values,expected",
    [
        ("u8", "B", [0, 64, 128, 255], [-32768, -16384, 0, 32512]),
        ("s16", "h", [-32768, -10, 0, 32767], [-32768, -10, 0, 32767]),
        ("s32", "i", [-2147483648, -655360, 0, 2147418112], [-32768, -10, 0, 32767]),
        (
            "s64",
            "q",
            [-(1 << 63), -(10 << 48), 0, 32767 << 48],
            [-32768, -10, 0, 32767],
        ),
        ("flt", "f", [-2.0, -0.5, 0.0, 2.0], [-32768, -16384, 0, 32767]),
        ("dbl", "d", [-2.0, -0.5, 0.0, 2.0], [-32768, -16384, 0, 32767]),
    ],
)
@pytest.mark.parametrize("planar", [False, True])
def test_sample_format_conversion_has_known_values(
    kind, code, values, expected, planar
):
    worker_spec = importlib.util.spec_from_file_location(
        "_review_pcm_worker", ROOT.parent / "services/attachments/media_worker.py"
    )
    worker = importlib.util.module_from_spec(worker_spec)
    worker_spec.loader.exec_module(worker)
    planes = [struct.pack("<" + code * 4, *values)]
    if planar:
        planes = [
            struct.pack("<" + code * 2, *values[channel::2]) for channel in range(2)
        ]
    frame = NS(
        format=NS(name=kind + ("p" if planar else "")),
        layout=NS(channels=(0, 1)),
        samples=2,
        planes=planes,
    )
    output = b"".join(worker._pcm_chunks(frame))
    assert output == struct.pack("<4h", *expected)


def test_nonfinite_float_samples_rejected():
    worker_spec = importlib.util.spec_from_file_location(
        "_review_pcm_worker", ROOT.parent / "services/attachments/media_worker.py"
    )
    worker = importlib.util.module_from_spec(worker_spec)
    worker_spec.loader.exec_module(worker)
    frame = NS(
        format=NS(name="flt"),
        layout=NS(channels=(0,)),
        samples=1,
        planes=[struct.pack("<f", float("nan"))],
    )
    with pytest.raises(worker.Rejected):
        list(worker._pcm_chunks(frame))
