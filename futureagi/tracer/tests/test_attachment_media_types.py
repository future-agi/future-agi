"""Per-type validation and serving: sniffed type, bounded structure, served as validated.

The probe rows run the parser in-process; they do not qualify the bubblewrap
sandbox around it. Audio and video fixtures under ``fixtures/attachments`` are
tiny synthetic tones and test patterns encoded by ffmpeg (no recorded content).
Images are made by Pillow here and the PDF is assembled here with exact offsets.
"""

import io
import struct
import uuid
import zlib
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.test import RequestFactory
from django.utils import timezone
from PIL import Image

from tracer.models.attachment import AttachmentBlob, AttachmentState, AttachmentUpload
from tracer.services.attachments import (
    occurrences,
    playback,
    retention,
    uploads,
    validation,
)
from tracer.services.attachments.contract import (
    SERVER_MODALITIES,
    AttachmentError,
    CapturePolicy,
    admit_audio,
    policy_document,
)
from tracer.services.attachments import probe as probe_module
from tracer.services.attachments.probe import probe
from tracer.tests import test_attachment_core as core
from tracer.tests.test_attachment_core import ImmutableStore, initialized, wav_bytes
from tracer.tests.test_attachment_playback import (
    ORIGIN,
    AuthorityDouble,
    RangeStoreDouble,
)
from tracer.views.attachment_media import SANDBOX_CSP, media_response

setup = core.setup

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

FIXTURES = Path(__file__).parent / "fixtures" / "attachments"
POLYGLOT_TAIL = b"<html><script>alert(document.cookie)</script></html>"


def fixture(name):
    return (FIXTURES / name).read_bytes()


def picture(fmt, mode="RGB", size=(32, 24), **options):
    color = {"RGBA": (200, 40, 40, 128), "P": 1}.get(mode, (200, 40, 40))
    stream = io.BytesIO()
    Image.new(mode, size, color).save(stream, fmt, **options)
    return stream.getvalue()


def animated_webp():
    frames = [Image.new("RGB", (32, 24), color) for color in ("red", "blue")]
    stream = io.BytesIO()
    frames[0].save(
        stream, "WEBP", save_all=True, append_images=frames[1:], duration=100
    )
    return stream.getvalue()


def png_chunk(kind, body):
    return (
        struct.pack(">I", len(body))
        + kind
        + body
        + struct.pack(">I", zlib.crc32(kind + body))
    )


def gray_png(width, height, raw_bytes=None, extra=()):
    """8-bit grayscale PNG whose IDAT inflates to ``raw_bytes`` zero bytes."""
    raw = height * (1 + width) if raw_bytes is None else raw_bytes
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", ihdr)
        + b"".join(png_chunk(kind, body) for kind, body in extra)
        + png_chunk(b"IDAT", zlib.compress(b"\x00" * raw, 9))
        + png_chunk(b"IEND", b"")
    )


def pdf_bytes(catalog=b"<< /Type /Catalog /Pages 2 0 R >>"):
    objects = [
        catalog,
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 72 72] >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\n" % (len(objects) + 1)
    out += b"startxref\n%d\n%%%%EOF\n" % xref
    return bytes(out)


def unknown_size(data, element_id, *, last=False):
    """Rewrite one ``element_id``'s size vint as EBML 'unknown size'."""
    at = (data.rindex if last else data.index)(element_id) + len(element_id)
    length = 9 - data[at].bit_length()
    marker = ((1 << (7 * length)) | ((1 << (7 * length)) - 1)).to_bytes(length, "big")
    return data[:at] + marker + data[at + length :]


SEGMENT_ID = b"\x18\x53\x80\x67"
CLUSTER_ID = b"\x1f\x43\xb6\x75"

# (name, payload factory, mime, modality, sample_rate, channels, seconds range)
VALID = [
    ("png", lambda: picture("PNG"), "image/png", "image", 0, 0, None),
    ("png-rgba", lambda: picture("PNG", "RGBA"), "image/png", "image", 0, 0, None),
    ("png-palette", lambda: picture("PNG", "P"), "image/png", "image", 0, 0, None),
    ("jpeg", lambda: picture("JPEG", quality=80), "image/jpeg", "image", 0, 0, None),
    (
        "jpeg-progressive",
        lambda: picture("JPEG", progressive=True),
        "image/jpeg",
        "image",
        0,
        0,
        None,
    ),
    ("webp", lambda: picture("WEBP", quality=80), "image/webp", "image", 0, 0, None),
    (
        "webp-lossless",
        lambda: picture("WEBP", lossless=True),
        "image/webp",
        "image",
        0,
        0,
        None,
    ),
    (
        "webp-alpha",
        lambda: picture("WEBP", "RGBA", quality=80),
        "image/webp",
        "image",
        0,
        0,
        None,
    ),
    ("webp-animated", animated_webp, "image/webp", "image", 0, 0, None),
    ("ogg", lambda: fixture("tone.ogg"), "audio/ogg", "audio", 48000, 1, (0.19, 0.22)),
    ("mp3", lambda: fixture("tone.mp3"), "audio/mpeg", "audio", 22050, 1, (0.2, 0.4)),
    (
        "mp3-id3",
        lambda: fixture("tone-id3.mp3"),
        "audio/mpeg",
        "audio",
        22050,
        1,
        (0.2, 0.4),
    ),
    ("flac", lambda: fixture("tone.flac"), "audio/flac", "audio", 16000, 1, (0.19, 0.21)),
    (
        "webm-audio",
        lambda: fixture("tone.webm"),
        "audio/webm",
        "audio",
        48000,
        1,
        (0.19, 0.22),
    ),
    (
        "webm-audio-live",
        lambda: unknown_size(
            unknown_size(fixture("tone.webm"), SEGMENT_ID), CLUSTER_ID, last=True
        ),
        "audio/webm",
        "audio",
        48000,
        1,
        (0.15, 0.22),
    ),
    ("m4a", lambda: fixture("tone.m4a"), "audio/mp4", "audio", 16000, 1, (0.19, 0.4)),
    ("mp4", lambda: fixture("clip.mp4"), "video/mp4", "video", 0, 0, None),
    # frag_keyframe+empty_moov: mvhd and mdhd carry no duration and there is no
    # mehd, so the duration is summed from the moof/traf/trun boxes.
    (
        "m4a-fragmented",
        lambda: fixture("tone-frag.m4a"),
        "audio/mp4",
        "audio",
        16000,
        1,
        (0.95, 1.1),
    ),
    (
        "m4a-fragmented-multi",
        lambda: fixture("tone-frag-multi.m4a"),
        "audio/mp4",
        "audio",
        16000,
        1,
        (0.95, 1.1),
    ),
    ("mp4-fragmented", lambda: fixture("clip-frag.mp4"), "video/mp4", "video", 0, 0, None),
    ("webm-video", lambda: fixture("clip.webm"), "video/webm", "video", 0, 0, None),
    ("pdf", pdf_bytes, "application/pdf", "document", 0, 0, None),
]
VALID_IDS = [row[0] for row in VALID]


def run(tmp_path, payload):
    path = tmp_path / "input"
    path.write_bytes(payload)
    return probe(path)


@pytest.mark.parametrize(
    "name,make,mime,modality,rate,channels,seconds", VALID, ids=VALID_IDS
)
def test_probe_validates_each_served_type(
    tmp_path, name, make, mime, modality, rate, channels, seconds
):
    facts = run(tmp_path, make())
    assert facts["mime_type"] == mime
    assert mime in SERVER_MODALITIES[modality]
    assert (facts["sample_rate"], facts["channels"]) == (rate, channels)
    if seconds is None:
        assert facts["frame_count"] == 0
    else:
        low, high = seconds
        assert low * rate <= facts["frame_count"] <= high * rate


@pytest.mark.parametrize(
    "name,make,mime,modality,rate,channels,seconds", VALID, ids=VALID_IDS
)
def test_probe_rejects_truncated_and_polyglot(
    tmp_path, name, make, mime, modality, rate, channels, seconds
):
    payload = make()
    for bad in (payload[:-8], payload + POLYGLOT_TAIL, POLYGLOT_TAIL + payload):
        with pytest.raises(ValueError):
            run(tmp_path, bad)


SVG = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>'


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(b"<!doctype html><script>alert(1)</script>", id="html"),
        pytest.param(SVG, id="svg"),
        pytest.param(b'<?xml version="1.0"?>' + SVG, id="svg-xml"),
        pytest.param(b"\x89PNG\r\n\x1a\n" + SVG, id="svg-behind-png-magic"),
        pytest.param(b"\xff\xd8\xff\xe0" + SVG, id="svg-behind-jpeg-magic"),
        pytest.param(picture("GIF"), id="gif-not-served"),
        pytest.param(picture("BMP"), id="bmp-not-served"),
        pytest.param(b"%PDF-1.4\n%%EOF\n", id="pdf-without-xref"),
    ],
)
def test_probe_refuses_disguised_and_unsupported(tmp_path, payload):
    with pytest.raises(ValueError):
        run(tmp_path, payload)


def _patch(data, old, new):
    assert old in data
    return data.replace(old, new)


def _jpeg_dims(data, width, height):
    at = data.index(b"\xff\xc0") + 5
    return data[:at] + struct.pack(">HH", height, width) + data[at + 4 :]


def _frag_patch(name, box, offset, value):
    """Overwrite a 32-bit field ``offset`` bytes into the first ``box`` body."""
    data = bytearray(fixture(name))
    at = data.index(box) + 4 + offset
    data[at : at + 4] = struct.pack(">I", value)
    return bytes(data)


def _mp3_frame_44100():
    # MPEG-1 layer III, 128 kbit/s, 44.1 kHz: a different stream's frame.
    return b"\xff\xfb\x90\x00" + b"\x00" * (144 * 128000 // 44100 - 4)


@pytest.mark.parametrize(
    "make",
    [
        # Decode bombs: dimensions over the pixel cap, or more inflated bytes
        # than the header allows.
        pytest.param(lambda: gray_png(30000, 30000, raw_bytes=10), id="png-dims"),
        pytest.param(
            lambda: gray_png(4000, 4000, raw_bytes=4000 * 4001 + 1024 * 1024),
            id="png-inflate-bomb",
        ),
        pytest.param(
            lambda: gray_png(64, 64, raw_bytes=64 * 65 - 1), id="png-short-inflate"
        ),
        pytest.param(
            lambda: gray_png(64, 64, extra=[(b"acTL", b"\x00" * 8)]), id="apng"
        ),
        pytest.param(
            lambda: _jpeg_dims(picture("JPEG"), 60000, 60000), id="jpeg-dims"
        ),
        # Under the pixel cap but over the per-side cap.
        pytest.param(lambda: _jpeg_dims(picture("JPEG"), 20000, 1), id="jpeg-wide"),
        pytest.param(lambda: _jpeg_dims(picture("JPEG"), 1, 20000), id="jpeg-tall"),
        # Both end in EOI, but the first EOI is not the last two bytes.
        pytest.param(
            lambda: picture("JPEG") + picture("JPEG"), id="jpeg-concatenated"
        ),
        pytest.param(
            lambda: picture("JPEG") + POLYGLOT_TAIL + b"\xff\xd9",
            id="jpeg-eoi-then-tail",
        ),
        pytest.param(
            lambda: b"RIFF"
            + struct.pack("<I", 22)
            + b"WEBPVP8X"
            + struct.pack("<I", 10)
            + b"\x00" * 4
            + (16383).to_bytes(3, "little") * 2,
            id="webp-canvas",
        ),
        # External or embedded resources, chained streams, concatenations.
        pytest.param(
            lambda: _patch(
                fixture("clip.mp4"), b"url \x00\x00\x00\x01", b"url \x00\x00\x00\x00"
            ),
            id="mp4-external-dref",
        ),
        pytest.param(
            lambda: unknown_size(fixture("tone.webm"), SEGMENT_ID)
            + b"\x19\x41\xa4\x69\x80",
            id="webm-attachments",
        ),
        pytest.param(lambda: fixture("tone.ogg") * 2, id="ogg-chained"),
        pytest.param(
            lambda: fixture("tone.mp3") + _mp3_frame_44100(), id="mp3-concatenated"
        ),
        pytest.param(
            lambda: pdf_bytes(
                b"<< /Type /Catalog /Pages 2 0 R"
                b" /OpenAction << /S /JavaScript /JS (app.alert(1)) >> >>"
            ),
            id="pdf-javascript",
        ),
        pytest.param(lambda: wav_bytes()[:44] + b"\x00" * 9, id="wav-misaligned"),
        # Fragmented MP4: a trun that declares fewer entries than it holds, a
        # tfhd default duration far past the 2 h cap, a traf for no track.
        pytest.param(
            lambda: _frag_patch("tone-frag.m4a", b"trun", 4, 16),
            id="fmp4-trun-count-short",
        ),
        pytest.param(
            lambda: _frag_patch("tone-frag-multi.m4a", b"tfhd", 16, 0xFFFFFFF0),
            id="fmp4-duration-over-cap",
        ),
        pytest.param(
            lambda: _frag_patch("tone-frag.m4a", b"tfhd", 4, 7), id="fmp4-unknown-track"
        ),
    ],
)
def test_probe_rejects_bombs_and_active_containers(tmp_path, make):
    with pytest.raises(ValueError):
        run(tmp_path, make())


def test_png_at_the_inflate_bound_is_accepted(tmp_path):
    assert run(tmp_path, gray_png(4000, 4000))["mime_type"] == "image/png"


def riff_chunk(kind, body):
    return kind + struct.pack("<I", len(body)) + body + b"\x00" * (len(body) & 1)


def animated_webp_canvas(width, height, frames):
    """VP8X animation of ``frames`` 1x1 lossless frames on a large canvas."""
    vp8x = bytes([0x02, 0, 0, 0]) + (width - 1).to_bytes(3, "little")
    vp8x += (height - 1).to_bytes(3, "little")
    frame = riff_chunk(
        b"ANMF",
        b"\x00" * 12
        + (100).to_bytes(3, "little")
        + b"\x00"
        + riff_chunk(b"VP8L", b"\x2f\x00\x00\x00\x00"),
    )
    body = b"WEBP" + riff_chunk(b"VP8X", vp8x) + riff_chunk(b"ANIM", b"\x00" * 6)
    body += frame * frames
    return b"RIFF" + struct.pack("<I", len(body)) + body


def test_animated_webp_total_pixels_are_capped(tmp_path):
    # 16384 x 2441 is under the 40 MP still-image cap; 4 frames stay under the
    # 160 MP animation cap and 5 frames go over it.
    width, height = probe_module.MAX_IMAGE_SIDE, 2441
    assert width * height <= probe_module.MAX_IMAGE_PIXELS
    assert 4 * width * height <= probe_module.MAX_ANIMATION_PIXELS < 5 * width * height
    assert run(tmp_path, animated_webp_canvas(width, height, 4))["mime_type"] == (
        "image/webp"
    )
    with pytest.raises(ValueError):
        run(tmp_path, animated_webp_canvas(width, height, 5))


def flac_with_false_headers():
    """tone.flac with no max frame size and a 64 KiB tail of false frame headers.

    Every header passes its CRC-8, so the end-of-file search CRC-16s from each
    one to EOF. Without a total budget that is about 8 MiB of pure-Python CRC.
    """
    data = bytearray(fixture("tone.flac"))
    data[15:18] = b"\x00\x00\x00"  # STREAMINFO max_frame_size: unknown
    header = bytes([0xFF, 0xF8, 0x19, 0x08, 0x00])
    header += bytes([probe_module._crc8(header)])
    tail = bytearray(65536)
    for offset in range(0, len(tail) - 8, 256):
        tail[offset : offset + len(header)] = header
    return bytes(data + tail)


def test_flac_end_search_crc_work_is_capped(tmp_path, monkeypatch):
    payload = flac_with_false_headers()
    crc_bytes = []
    real_crc16 = probe_module._crc16

    def counting(chunk):
        crc_bytes.append(len(chunk))
        return real_crc16(chunk)

    monkeypatch.setattr(probe_module, "_crc16", counting)
    with pytest.raises(ValueError):
        run(tmp_path, payload)
    assert len(crc_bytes) > 1
    assert sum(crc_bytes) <= probe_module.FLAC_CRC_BUDGET


def test_flac_crc_budget_applies_to_valid_files(tmp_path, monkeypatch):
    assert run(tmp_path, fixture("tone.flac"))["mime_type"] == "audio/flac"
    monkeypatch.setattr(probe_module, "FLAC_CRC_BUDGET", 0)
    with pytest.raises(ValueError):
        run(tmp_path, fixture("tone.flac"))


def test_policy_advertises_only_validated_modalities(setup):
    scope, _ = setup
    document = policy_document(scope)
    assert document["allowed_modalities"] == ["audio", "document", "image", "video"]
    assert document["record_video"] is False
    assert document["require_metadata_removal"] is False
    scope.enabled = False
    assert policy_document(scope)["allowed_modalities"] == []


@pytest.mark.parametrize(
    "mime,representation,length,ok",
    [
        ("image/png", "original", 10, True),
        ("image/png", "playable", 10, False),
        ("application/pdf", "playable", 10, False),
        ("audio/ogg", "playable", 10, True),
        ("video/webm", "playable", 10, True),
        ("image/svg+xml", "original", 10, False),
        ("text/html", "original", 10, False),
        ("application/octet-stream", "original", 10, False),
        ("image/png", "thumbnail", 10, False),
        ("image/png", "original", playback.MAX_SERVED_BYTES + 1, False),
    ],
)
def test_only_validated_types_are_servable(mime, representation, length, ok):
    blob = SimpleNamespace(mime_type=mime, byte_length=length, storage_version="v1")
    assert (playback.servable(blob, representation) is not None) is ok


def _stored(setup, payload, mime, modality):
    scope, _ = setup
    upload, token, _, _ = initialized(
        setup, mime_type=mime, modality=modality, declared_bytes=len(payload)
    )
    store = ImmutableStore()
    uploads.transfer(
        token, scope_id=scope.id, upload_id=upload.id, chunks=[payload], storage=store
    )
    uploads.complete(token, scope_id=scope.id, upload_id=upload.id)
    return upload, store


@pytest.mark.parametrize(
    "payload,declared",
    [
        pytest.param(picture("PNG"), "image/jpeg", id="png-declared-jpeg"),
        pytest.param(picture("JPEG"), "image/webp", id="jpeg-declared-webp"),
        pytest.param(wav_bytes(), "audio/ogg", id="wav-declared-ogg"),
        pytest.param(fixture("tone.webm"), "video/webm", id="audio-webm-as-video"),
        pytest.param(fixture("clip.mp4"), "audio/mp4", id="video-mp4-as-audio"),
        pytest.param(SVG, "image/png", id="svg-declared-png"),
    ],
)
def test_type_mismatch_is_rejected_not_relabelled(setup, monkeypatch, payload, declared):
    modality = {"image": "image", "audio": "audio", "video": "video"}[
        declared.split("/")[0]
    ]
    upload, store = _stored(setup, payload, declared, modality)
    monkeypatch.setattr(validation, "sandbox_probe", probe)
    with pytest.raises(AttachmentError):
        validation.validate(upload.id, scope_id=upload.scope_id, storage=store)
    assert AttachmentUpload.objects.get(pk=upload.id).state == AttachmentState.REJECTED
    assert store.promotions == [] and not AttachmentBlob.objects.exists()


def test_oversize_image_is_refused_before_bytes(setup):
    with pytest.raises(AttachmentError, match="too_large"):
        initialized(
            setup,
            mime_type="image/png",
            modality="image",
            declared_bytes=uploads.MAX_UPLOAD_BYTES + 1,
        )
    assert not AttachmentUpload.objects.exists()


@pytest.mark.parametrize(
    "mime,modality",
    [("audio/L16", "audio"), ("application/octet-stream", "file")],
)
def test_types_without_a_validator_are_refused(setup, mime, modality):
    with pytest.raises(AttachmentError, match="unsupported_media"):
        initialized(setup, mime_type=mime, modality=modality)
    assert not AttachmentUpload.objects.exists()


ADMISSION_SCOPE = SimpleNamespace(
    enabled=True,
    require_media_redaction=False,
    allow_unredacted_media_with_text_pii=False,
)


@pytest.mark.parametrize("modality", ["image", "video", "document", "audio"])
@pytest.mark.parametrize(
    "change,direction,reason",
    [
        ({"hide_inputs": True}, "input", "policy_denied"),
        ({"hide_input_text": True}, "input", "policy_denied"),
        ({"hide_output_text": True}, "output", "policy_denied"),
        ({"hide_outputs": True}, "mixed", "policy_denied"),
        ({"pii_redaction": True}, "output", "redaction_unavailable"),
        ({"capture_content": False}, "output", "policy_denied"),
    ],
)
def test_privacy_fences_apply_to_every_modality(modality, change, direction, reason):
    # Screenshots and documents carry text: the hide/PII flags deny them too.
    with pytest.raises(AttachmentError, match=reason):
        admit_audio(
            ADMISSION_SCOPE,
            replace(CapturePolicy(enabled=True, record_audio=True), **change),
            direction=direction,
            source_kind="Bytes",
            modality=modality,
        )


@pytest.mark.parametrize("modality", ["image", "video", "document"])
def test_only_audio_forms_a_recording(modality):
    with pytest.raises(AttachmentError, match="recording_disabled"):
        admit_audio(
            ADMISSION_SCOPE,
            CapturePolicy(enabled=True, record_audio=True),
            direction="output",
            source_kind="Bytes",
            creates_recording=True,
            modality=modality,
        )
    admit_audio(
        ADMISSION_SCOPE,
        CapturePolicy(enabled=True, record_audio=True),
        direction="output",
        source_kind="Bytes",
        creates_recording=True,
    )


def _readable(setup, user, monkeypatch, payload, mime, modality, representation):
    scope, _ = setup
    upload, store = _stored(setup, payload, mime, modality)
    monkeypatch.setattr(validation, "sandbox_probe", probe)
    blob = validation.validate(upload.id, scope_id=scope.id, storage=store)
    row = upload.occurrence
    parent = occurrences.Parent(row.trace_id, row.span_id)
    state = occurrences.SourceState(
        scope.id,
        parent,
        3,
        timezone.now() - timedelta(hours=1),
        timezone.now() + timedelta(hours=1),
        True,
        True,
    )
    retention.bind_retention(scope_id=scope.id, occurrence_id=row.id, source=state)
    viewer = occurrences.Viewer(
        user.id, uuid.uuid4(), scope.organization_id, scope.workspace_id, scope.project_id
    )
    authority = AuthorityDouble(viewer, state)
    _, cookie = playback.exchange(viewer, authority)
    (result,) = playback.resolve(
        viewer,
        authority,
        [
            {
                "occurrence_id": str(row.client_id),
                "parent": {"trace_id": str(parent.trace_id), "span_id": parent.span_id},
                "representation": representation,
            }
        ],
    )
    return SimpleNamespace(
        row=row,
        blob=blob,
        result=result,
        cookie=cookie,
        authority=authority,
        store=RangeStoreDouble(payload),
    )


def _get(r, **headers):
    request = RequestFactory().get(
        "/tracer/v1/attachment-media/test", secure=True, **headers
    )
    request.COOKIES[playback.COOKIE_NAME] = r.cookie
    return media_response(
        request,
        occurrence_id=r.row.client_id,
        grant_token=r.result["gateway_path"].rsplit("/", 1)[1],
        authority=r.authority,
        storage=r.store,
        origin=ORIGIN,
    )


SERVED_ROWS = [
    ("png", lambda: picture("PNG"), "image/png", "image", 'inline; filename="image.png"'),
    ("jpeg", lambda: picture("JPEG"), "image/jpeg", "image", 'inline; filename="image.jpg"'),
    ("webp", lambda: picture("WEBP"), "image/webp", "image", 'inline; filename="image.webp"'),
    ("ogg", lambda: fixture("tone.ogg"), "audio/ogg", "audio", 'inline; filename="audio.ogg"'),
    ("mp3", lambda: fixture("tone.mp3"), "audio/mpeg", "audio", 'inline; filename="audio.mp3"'),
    ("flac", lambda: fixture("tone.flac"), "audio/flac", "audio", 'inline; filename="audio.flac"'),
    ("webm-audio", lambda: fixture("tone.webm"), "audio/webm", "audio", 'inline; filename="audio.webm"'),
    ("m4a", lambda: fixture("tone.m4a"), "audio/mp4", "audio", 'inline; filename="audio.m4a"'),
    ("mp4", lambda: fixture("clip.mp4"), "video/mp4", "video", 'inline; filename="video.mp4"'),
    ("webm-video", lambda: fixture("clip.webm"), "video/webm", "video", 'inline; filename="video.webm"'),
    ("pdf", pdf_bytes, "application/pdf", "document", 'attachment; filename="document.pdf"'),
]


@pytest.mark.parametrize(
    "name,make,mime,modality,disposition",
    SERVED_ROWS,
    ids=[row[0] for row in SERVED_ROWS],
)
def test_gateway_serves_the_validated_type(
    setup, user, monkeypatch, name, make, mime, modality, disposition
):
    payload = make()
    r = _readable(setup, user, monkeypatch, payload, mime, modality, "original")
    assert r.blob.mime_type == mime and r.result["status"] == "ready"
    reply = _get(r)
    assert reply.status_code == 200
    assert b"".join(reply.streaming_content) == payload
    assert reply["Content-Type"] == mime
    assert reply["Content-Disposition"] == disposition
    assert reply["X-Content-Type-Options"] == "nosniff"
    assert reply["Cache-Control"] == "private, no-store"
    media = modality in ("audio", "video")
    assert reply["Accept-Ranges"] == ("bytes" if media else "none")
    if modality == "document":
        assert reply["Content-Security-Policy"] == SANDBOX_CSP
    else:
        assert "sandbox" not in reply["Content-Security-Policy"]
    ranged = _get(r, HTTP_RANGE="bytes=0-9")
    if media:
        assert ranged.status_code == 206
        assert ranged["Content-Range"] == f"bytes 0-9/{len(payload)}"
        assert b"".join(ranged.streaming_content) == payload[:10]
    else:
        # Images and documents ignore Range and send the whole representation.
        assert ranged.status_code == 200
        assert b"".join(ranged.streaming_content) == payload


@pytest.mark.parametrize(
    "make,mime,modality,status",
    [
        (lambda: fixture("tone.ogg"), "audio/ogg", "audio", "ready"),
        (lambda: fixture("clip.mp4"), "video/mp4", "video", "ready"),
        (lambda: picture("PNG"), "image/png", "image", "not_found"),
        (pdf_bytes, "application/pdf", "document", "not_found"),
    ],
)
def test_playable_resolves_only_for_audio_and_video(
    setup, user, monkeypatch, make, mime, modality, status
):
    r = _readable(setup, user, monkeypatch, make(), mime, modality, "playable")
    assert r.result["status"] == status
