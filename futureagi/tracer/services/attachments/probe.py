"""Credential-free sandbox entrypoint: sniff the bytes, then a bounded check.

Standard library only, with no imports from Django or application code: the
sandbox runs the system interpreter with ``-I -S``. The output is the validated
type plus four integer facts, never names, tags or input fragments.

Every parser requires its container to tile the file exactly to EOF. That one
rule is the polyglot control (no bytes may follow the format's own end) and the
truncation control (no element may run past EOF). Nothing is decoded except the
PNG zlib stream, which is inflated in bounded slices, discarded and counted to
prove its exact size. Pixel, frame and duration caps bound every decoder that
later reads a served file.

Embedded metadata (EXIF, ID3, Vorbis comments, PDF info) is ignored: it is
neither exported nor removed. Originals are served as uploaded.
"""

import json
import os
import re
import struct
import sys
import zlib

# The only exit status that means "these bytes are refused". The interpreter
# itself exits 2 (unreadable script, bad arguments) and 1 (startup failure), so
# neither may be read as a verdict on the input.
REJECTED_EXIT = 65

MAX_BYTES = 8 * 1024 * 1024
MAX_IMAGE_SIDE = 16384
MAX_IMAGE_PIXELS = 40_000_000
MAX_ANIMATION_FRAMES = 1000
MAX_ANIMATION_PIXELS = 4 * MAX_IMAGE_PIXELS
MAX_VIDEO_SIDE = 8192
MAX_VIDEO_PIXELS = 8192 * 4320
MAX_MEDIA_SECONDS = 7200
MAX_ELEMENTS = 200_000
INFLATE_SLICE = 1024 * 1024
# Total bytes the FLAC end-of-file search may CRC-16. Each candidate header
# costs a CRC over the rest of the file, so without a total cap a crafted tail
# of false headers costs up to 256 x 1 MiB of pure-Python CRC.
FLAC_CRC_BUDGET = 4 * 1024 * 1024

# Parsers raise these on malformed input; probe() turns all of them into
# ValueError so nothing about the input escapes the sandbox.
MALFORMED = (
    ValueError,
    IndexError,
    KeyError,
    OverflowError,
    struct.error,
    zlib.error,
    UnicodeDecodeError,
)


def _facts(mime_type, sample_rate=0, channels=0, frame_count=0):
    return {
        "mime_type": mime_type,
        "sample_rate": sample_rate,
        "channels": channels,
        "frame_count": frame_count,
    }


def _require(condition):
    if not condition:
        raise ValueError


# WAV: PCM 16-bit, no ancillary chunks, at most 30 s (one recording segment).


def _wav(data):
    length = len(data)
    _require(44 <= length)
    magic, size, kind = struct.unpack("<4sI4s", data[:12])
    _require(magic == b"RIFF" and kind == b"WAVE" and size + 8 == length)
    _require(data[12:20] == b"fmt \x10\x00\x00\x00")
    encoding, channels, rate, byte_rate, align, bits = struct.unpack(
        "<HHIIHH", data[20:36]
    )
    _require(
        encoding == 1
        and channels in {1, 2}
        and bits == 16
        and 8000 <= rate <= 96000
        and align == channels * 2
        and byte_rate == rate * align
    )
    tag, data_size = struct.unpack("<4sI", data[36:44])
    _require(
        tag == b"data"
        and data_size + 44 == length
        and data_size % align == 0
        and data_size != 0
    )
    frames = data_size // align
    _require(frames <= rate * 30)
    return _facts("audio/wav", rate, channels, frames)


# PNG ---------------------------------------------------------------------------

PNG_DEPTHS = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
PNG_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
ADAM7 = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4))
ADAM7 += ((1, 0, 2, 2), (0, 1, 1, 2))
# APNG animation chunks are refused: every frame would be a separate bomb budget.
PNG_REFUSED = (b"acTL", b"fcTL", b"fdAT")


def _png_raw_size(width, height, bits, interlace):
    def rows(w, h):
        return h * (1 + (w * bits + 7) // 8) if w and h else 0

    if not interlace:
        return rows(width, height)
    return sum(
        rows(
            (width - x + dx - 1) // dx if width > x else 0,
            (height - y + dy - 1) // dy if height > y else 0,
        )
        for x, y, dx, dy in ADAM7
    )


def _png(data):
    size, pos = len(data), 8
    expected = color = None
    inflater = zlib.decompressobj()
    inflated = chunks = 0
    idat_state = 0  # 0 none yet, 1 in the IDAT run, 2 after it
    palette = False
    while True:
        _require(pos + 12 <= size)
        length, kind = struct.unpack(">I4s", data[pos : pos + 8])
        end = pos + 12 + length
        _require(length <= 0x7FFFFFFF and end <= size and kind.isalpha())
        body = data[pos + 8 : pos + 8 + length]
        _require(zlib.crc32(kind + body) == struct.unpack(">I", data[end - 4 : end])[0])
        chunks += 1
        _require(chunks <= 4096)
        _require((expected is None) == (kind == b"IHDR"))
        if kind == b"IHDR":
            _require(length == 13)
            width, height, depth, color, method, filtering, interlace = struct.unpack(
                ">IIBBBBB", body
            )
            _require(
                1 <= width <= MAX_IMAGE_SIDE
                and 1 <= height <= MAX_IMAGE_SIDE
                and width * height <= MAX_IMAGE_PIXELS
                and depth in PNG_DEPTHS.get(color, ())
                and method == 0
                and filtering == 0
                and interlace in (0, 1)
            )
            expected = _png_raw_size(
                width, height, depth * PNG_CHANNELS[color], interlace
            )
        elif kind == b"IDAT":
            _require(idat_state < 2 and not inflater.eof)
            idat_state = 1
            pending = body
            while True:
                out = inflater.decompress(pending, INFLATE_SLICE)
                inflated += len(out)
                _require(inflated <= expected)
                pending = inflater.unconsumed_tail
                if not pending and (len(out) < INFLATE_SLICE or inflater.eof):
                    break
            _require(not inflater.unused_data)
        elif kind == b"IEND":
            _require(length == 0 and end == size)
            break
        else:
            _require(kind not in PNG_REFUSED)
            # Unknown critical chunks (uppercase first letter) are undecodable.
            _require(kind[0] > 0x60 or kind == b"PLTE")
            if kind == b"PLTE":
                _require(idat_state == 0 and 3 <= length <= 768 and length % 3 == 0)
                palette = True
            if idat_state == 1:
                idat_state = 2
        pos = end
    _require(idat_state and inflater.eof and inflated == expected)
    _require(color != 3 or palette)
    return _facts("image/png")


# JPEG --------------------------------------------------------------------------

JPEG_FRAMES = (0xC0, 0xC1, 0xC2)
# Segments that may appear between progressive scans.
JPEG_INTERSCAN = (0xC4, 0xDA, 0xDB, 0xDD, 0xFE)


def _jpeg_segment(data, pos):
    length = struct.unpack(">H", data[pos + 2 : pos + 4])[0]
    end = pos + 2 + length
    _require(length >= 2 and end <= len(data))
    return end


def _jpeg(data):
    size, pos, frame, segments = len(data), 2, False, 0
    _require(data[-2:] == b"\xff\xd9")
    while True:
        _require(pos + 4 <= size and data[pos] == 0xFF)
        marker = data[pos + 1]
        _require(marker not in (0x00, 0x01, 0xD8, 0xD9, 0xFF))
        _require(not 0xD0 <= marker <= 0xD7)
        end = _jpeg_segment(data, pos)
        segments += 1
        _require(segments <= 1024)
        if marker in JPEG_FRAMES:
            _require(not frame)
            precision, height, width, components = struct.unpack(
                ">BHHB", data[pos + 4 : pos + 10]
            )
            _require(
                precision == 8
                and 1 <= width <= MAX_IMAGE_SIDE
                and 1 <= height <= MAX_IMAGE_SIDE
                and width * height <= MAX_IMAGE_PIXELS
                and components in (1, 3, 4)
                and end - pos == 10 + 3 * components
            )
            frame = True
        elif 0xC3 <= marker <= 0xCF and marker != 0xC4:
            # Lossless, hierarchical and arithmetic-coded frames are refused.
            raise ValueError
        elif marker == 0xDA:
            _require(frame)
            break
        pos = end
    # Entropy-coded data: 0xFF is stuffed as FF00, so every other FFxx is a
    # marker. Walk them; the only EOI must be the file's last two bytes.
    pos = end
    while True:
        index = data.find(b"\xff", pos)
        _require(0 <= index < size - 1)
        marker = data[index + 1]
        if marker == 0x00 or 0xD0 <= marker <= 0xD7:
            pos = index + 2
        elif marker == 0xFF:
            pos = index + 1
        elif marker == 0xD9:
            _require(index + 2 == size)
            return _facts("image/jpeg")
        else:
            _require(marker in JPEG_INTERSCAN)
            segments += 1
            _require(segments <= 1024)
            pos = _jpeg_segment(data, index)


# WebP --------------------------------------------------------------------------


def _vp8(body):
    _require(len(body) >= 10 and body[0] & 1 == 0 and body[3:6] == b"\x9d\x01\x2a")
    width, height = struct.unpack("<HH", body[6:10])
    return width & 0x3FFF, height & 0x3FFF


def _vp8l(body):
    _require(len(body) >= 5 and body[0] == 0x2F)
    bits = struct.unpack("<I", body[1:5])[0]
    _require(bits >> 29 == 0)
    return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1


def _riff_chunks(data, pos, end):
    count = 0
    while pos < end:
        _require(pos + 8 <= end)
        kind, length = struct.unpack("<4sI", data[pos : pos + 8])
        stop = pos + 8 + length + (length & 1)
        _require(stop <= end)
        count += 1
        _require(count <= 4096)
        yield kind, data[pos + 8 : pos + 8 + length]
        pos = stop
    _require(pos == end)


def _image_dims(width, height):
    _require(
        1 <= width <= MAX_IMAGE_SIDE
        and 1 <= height <= MAX_IMAGE_SIDE
        and width * height <= MAX_IMAGE_PIXELS
    )


def _webp_bitstream(kind, body):
    return _vp8(body) if kind == b"VP8 " else _vp8l(body)


def _webp(data):
    size = len(data)
    _require(struct.unpack("<I", data[4:8])[0] + 8 == size and size >= 20)
    chunks = list(_riff_chunks(data, 12, size))
    first = chunks[0][0]
    images = [(k, b) for k, b in chunks if k in (b"VP8 ", b"VP8L")]
    if first in (b"VP8 ", b"VP8L"):
        _require(len(chunks) == 1)
        _image_dims(*_webp_bitstream(*chunks[0]))
        return _facts("image/webp")
    _require(first == b"VP8X")
    body = chunks[0][1]
    _require(len(body) == 10 and sum(k == b"VP8X" for k, _ in chunks) == 1)
    flags = body[0]
    canvas_w = 1 + int.from_bytes(body[4:7], "little")
    canvas_h = 1 + int.from_bytes(body[7:10], "little")
    _image_dims(canvas_w, canvas_h)
    frames = [b for k, b in chunks if k == b"ANMF"]
    if flags & 0x02:
        _require(frames and not images)
        _require(len(frames) <= MAX_ANIMATION_FRAMES)
        _require(len(frames) * canvas_w * canvas_h <= MAX_ANIMATION_PIXELS)
        for frame in frames:
            _require(len(frame) >= 16)
            x = 2 * int.from_bytes(frame[0:3], "little")
            y = 2 * int.from_bytes(frame[3:6], "little")
            w = 1 + int.from_bytes(frame[6:9], "little")
            h = 1 + int.from_bytes(frame[9:12], "little")
            _require(x + w <= canvas_w and y + h <= canvas_h)
            inner = [
                (k, b) for k, b in _riff_chunks(frame, 16, len(frame))
                if k in (b"VP8 ", b"VP8L")
            ]
            _require(len(inner) == 1)
            _require(_webp_bitstream(*inner[0]) == (w, h))
    else:
        _require(not frames and len(images) == 1)
        _require(_webp_bitstream(*images[0]) == (canvas_w, canvas_h))
    return _facts("image/webp")


# Ogg Opus ----------------------------------------------------------------------

OGG_SAMPLE_RATE = 48000


def _ogg(data):
    size, pos, pages = len(data), 0, 0
    serial = granule_last = None
    packets, current, eos = [], b"", False
    while pos < size:
        _require(not eos and pos + 27 <= size)
        _require(data[pos : pos + 4] == b"OggS" and data[pos + 4] == 0)
        flags = data[pos + 5]
        granule, stream, sequence, _crc, count = struct.unpack(
            "<qIIIB", data[pos + 6 : pos + 27]
        )
        table = data[pos + 27 : pos + 27 + count]
        start = pos + 27 + count
        end = start + sum(table)
        _require(len(table) == count and end <= size and flags & ~0x07 == 0)
        if pages == 0:
            # One logical stream only: no chained or multiplexed streams.
            _require(flags & 0x02 and not flags & 0x01)
            serial = stream
        else:
            _require(not flags & 0x02 and stream == serial)
        _require(sequence == pages)
        if len(packets) < 2:
            _require(bool(flags & 0x01) == bool(current))
            offset = start
            for lace in table:
                current += data[offset : offset + lace]
                offset += lace
                _require(len(current) <= 1024 * 1024)
                if lace < 255:
                    packets.append(current)
                    current = b""
                    if len(packets) == 2:
                        break
        if granule != -1:
            _require(granule >= 0)
            _require(granule_last is None or granule >= granule_last)
            granule_last = granule
        eos = bool(flags & 0x04)
        pages += 1
        _require(pages <= MAX_ELEMENTS)
        pos = end
    _require(eos and len(packets) == 2 and granule_last is not None)
    head, tags = packets
    _require(len(head) >= 19 and head[:8] == b"OpusHead" and 1 <= head[8] <= 15)
    channels, pre_skip = head[9], struct.unpack("<H", head[10:12])[0]
    mapping = head[18]
    if mapping == 0:
        _require(channels in (1, 2) and len(head) == 19)
    else:
        _require(mapping == 1 and 1 <= channels <= 8 and len(head) == 21 + channels)
    _require(tags[:8] == b"OpusTags")
    samples = granule_last - pre_skip
    _require(0 < samples <= OGG_SAMPLE_RATE * MAX_MEDIA_SECONDS)
    return _facts("audio/ogg", OGG_SAMPLE_RATE, channels, samples)


# MP3 (MPEG audio layer III) ----------------------------------------------------

MP3_BITRATES = {
    3: (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    2: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}
MP3_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}


def _mp3_header(data, pos):
    _require(pos + 4 <= len(data) and data[pos] == 0xFF)
    b1, b2, b3 = data[pos + 1], data[pos + 2], data[pos + 3]
    version, layer = (b1 >> 3) & 3, (b1 >> 1) & 3
    bitrate_index, rate_index = b2 >> 4, (b2 >> 2) & 3
    _require(
        b1 & 0xE0 == 0xE0
        and version != 1
        and layer == 1
        and 0 < bitrate_index < 15
        and rate_index != 3
        and b3 & 3 != 2
    )
    rate = MP3_RATES[version][rate_index]
    bitrate = MP3_BITRATES[3 if version == 3 else 2][bitrate_index] * 1000
    per_frame = 1152 if version == 3 else 576
    length = (per_frame // 8) * bitrate // rate + ((b2 >> 1) & 1)
    channels = 1 if b3 >> 6 == 3 else 2
    return (version, rate, channels), length, per_frame


def _mp3(data):
    size, pos = len(data), 0
    if data[:3] == b"ID3":
        _require(data[3] in (2, 3, 4) and all(b < 0x80 for b in data[6:10]))
        tag = 0
        for byte in data[6:10]:
            tag = (tag << 7) | byte
        pos = 10 + tag + (10 if data[5] & 0x10 else 0)
    end = size
    if size - 128 >= pos and data[size - 128 : size - 125] == b"TAG":
        end = size - 128
    first, frames = None, 0
    while pos < end:
        shape, length, per_frame = _mp3_header(data, pos)
        # Every frame shares one version, rate and channel count: a second
        # stream concatenated onto the first is not one MP3.
        _require(first is None or shape == first)
        first = shape
        _require(length >= 4 and pos + length <= end)
        pos += length
        frames += 1
        _require(frames <= MAX_ELEMENTS)
    _require(frames and pos == end)
    _, rate, channels = first
    samples = frames * per_frame
    _require(samples <= rate * MAX_MEDIA_SECONDS)
    return _facts("audio/mpeg", rate, channels, samples)


# FLAC --------------------------------------------------------------------------


def _crc_table(poly, width):
    top, mask, table = 1 << (width - 1), (1 << width) - 1, []
    for byte in range(256):
        crc = byte << (width - 8)
        for _ in range(8):
            crc = ((crc << 1) ^ poly) if crc & top else (crc << 1)
        table.append(crc & mask)
    return table


CRC8 = _crc_table(0x07, 8)
CRC16 = _crc_table(0x8005, 16)


def _crc8(chunk):
    crc = 0
    for byte in chunk:
        crc = CRC8[crc ^ byte]
    return crc


def _crc16(chunk):
    crc = 0
    for byte in chunk:
        crc = ((crc << 8) & 0xFFFF) ^ CRC16[(crc >> 8) ^ byte]
    return crc


def _flac_frame_header(data, pos):
    """Length of a valid frame header at ``pos`` (CRC-8 checked), else None."""
    if pos + 6 > len(data) or data[pos] != 0xFF or data[pos + 1] not in (0xF8, 0xF9):
        return None
    block, rate = data[pos + 2] >> 4, data[pos + 2] & 0x0F
    layout, depth = data[pos + 3] >> 4, (data[pos + 3] >> 1) & 7
    if not block or rate == 15 or layout > 10 or depth in (3, 7) or data[pos + 3] & 1:
        return None
    lead = data[pos + 4]
    if lead < 0x80:
        coded = 1
    elif 0xC0 <= lead < 0xFE:
        coded = 8 - (lead ^ 0xFF).bit_length()
    else:
        return None
    length = 4 + coded
    length += {6: 1, 7: 2}.get(block, 0) + {12: 1, 13: 2, 14: 2}.get(rate, 0)
    if pos + length + 1 > len(data) or _crc8(data[pos : pos + length]) != data[pos + length]:
        return None
    return length + 1


def _flac(data):
    size, pos, blocks, last, info = len(data), 4, 0, False, None
    while not last:
        _require(pos + 4 <= size)
        header = data[pos]
        last, kind = bool(header & 0x80), header & 0x7F
        length = int.from_bytes(data[pos + 1 : pos + 4], "big")
        end = pos + 4 + length
        _require(end <= size)
        if blocks == 0:
            _require(kind == 0 and length == 34)
            info = data[pos + 4 : end]
        else:
            _require(0 < kind <= 6)
        blocks += 1
        _require(blocks <= 1024)
        pos = end
    min_block, max_block = struct.unpack(">HH", info[:4])
    min_frame = int.from_bytes(info[4:7], "big")
    max_frame = int.from_bytes(info[7:10], "big")
    packed = int.from_bytes(info[10:18], "big")
    rate = packed >> 44
    channels = ((packed >> 41) & 7) + 1
    total = packed & ((1 << 36) - 1)
    _require(16 <= min_block <= max_block and 1 <= rate <= 655350 and total > 0)
    _require(total <= rate * MAX_MEDIA_SECONDS)
    remaining = size - pos
    _require(_flac_frame_header(data, pos) is not None)
    fewest = -(-total // max_block)
    most = -(-total // min_block)
    _require(remaining >= (fewest - 1) * min_frame + 1)
    _require(not max_frame or remaining <= most * max_frame)
    # The last frame must end exactly at EOF: its CRC-16 covers the whole frame,
    # so a truncated or extended file fails. Search back for its header.
    window = max(min(max_frame or 65536, 1024 * 1024), 16)
    start, cursor, tries, crc_bytes = max(pos, size - window - 1), size - 2, 0, 0
    while True:
        index = data.rfind(b"\xff", start, cursor)
        _require(index >= 0 and tries < 256)
        tries += 1
        if _flac_frame_header(data, index) is not None:
            crc_bytes += size - 2 - index
            _require(crc_bytes <= FLAC_CRC_BUDGET)
            if _crc16(data[index : size - 2]) == int.from_bytes(data[-2:], "big"):
                return _facts("audio/flac", rate, channels, total)
        cursor = index


# EBML: WebM audio and video ----------------------------------------------------

EBML_HEADER, SEGMENT, CLUSTER = 0x1A45DFA3, 0x18538067, 0x1F43B675
INFO, TRACKS, ATTACHMENTS = 0x1549A966, 0x1654AE6B, 0x1941A469
LEVEL1 = {
    0x114D9B74,  # SeekHead
    INFO,
    TRACKS,
    CLUSTER,
    0x1C53BB6B,  # Cues
    0x1043A770,  # Chapters
    0x1254C367,  # Tags
    ATTACHMENTS,
}
GLOBAL = {0xEC, 0xBF}  # Void, CRC-32
WEBM_AUDIO = {b"A_OPUS", b"A_VORBIS"}
WEBM_VIDEO = {b"V_VP8", b"V_VP9", b"V_AV1"}


def _ebml_id(data, pos):
    first = data[pos]
    length = 9 - first.bit_length()
    _require(first and length <= 4 and pos + length <= len(data))
    return int.from_bytes(data[pos : pos + length], "big"), pos + length


def _ebml_size(data, pos):
    first = data[pos]
    length = 9 - first.bit_length()
    _require(first and pos + length <= len(data))
    value = int.from_bytes(data[pos : pos + length], "big") & ((1 << (7 * length)) - 1)
    unknown = value == (1 << (7 * length)) - 1
    return (None if unknown else value), pos + length


def _ebml_elements(data, pos, end):
    """Yield (id, body_start, body_end) for children that tile [pos, end)."""
    while pos < end:
        element, after = _ebml_id(data, pos)
        size, body = _ebml_size(data, after)
        _require(size is not None and body + size <= end)
        yield element, body, body + size
        pos = body + size
    _require(pos == end)


def _uint(data, start, end):
    _require(end - start <= 8)
    return int.from_bytes(data[start:end], "big")


def _float(data, start, end):
    _require(end - start in (4, 8))
    return struct.unpack(">f" if end - start == 4 else ">d", data[start:end])[0]


def _webm_tracks(data, start, end):
    tracks = []
    for element, body, stop in _ebml_elements(data, start, end):
        if element != 0xAE:  # TrackEntry
            _require(element in GLOBAL)
            continue
        track = {"type": None, "codec": None, "width": 0, "height": 0}
        track.update(rate=0.0, channels=1)
        for child, inner, close in _ebml_elements(data, body, stop):
            if child == 0x83:
                track["type"] = _uint(data, inner, close)
            elif child == 0x86:
                track["codec"] = data[inner:close]
            elif child == 0x6D80:  # ContentEncodings: compressed or encrypted
                raise ValueError
            elif child == 0xE0:
                for leaf, a, b in _ebml_elements(data, inner, close):
                    if leaf == 0xB0:
                        track["width"] = _uint(data, a, b)
                    elif leaf == 0xBA:
                        track["height"] = _uint(data, a, b)
            elif child == 0xE1:
                for leaf, a, b in _ebml_elements(data, inner, close):
                    if leaf == 0xB5:
                        track["rate"] = _float(data, a, b)
                    elif leaf == 0x9F:
                        track["channels"] = _uint(data, a, b)
        tracks.append(track)
    return tracks


def _webm_cluster(data, pos, end, known):
    """Walk one Cluster's children; return (end, timestamp, last block offset)."""
    timestamp, latest = 0, 0
    while pos < end:
        element, after = _ebml_id(data, pos)
        if not known and element in LEVEL1:
            break
        size, body = _ebml_size(data, after)
        _require(size is not None and body + size <= end)
        stop = body + size
        if element == 0xE7:
            timestamp = _uint(data, body, stop)
        elif element in (0xA3, 0xA0):  # SimpleBlock, BlockGroup
            block = body
            if element == 0xA0:
                blocks = [e for e in _ebml_elements(data, body, stop) if e[0] == 0xA1]
                _require(len(blocks) == 1)
                block = blocks[0][1]
            _, track_end = _ebml_size(data, block)
            relative = struct.unpack(">h", data[track_end : track_end + 2])[0]
            latest = max(latest, relative)
        else:
            _require(element in GLOBAL or element in (0xA7, 0xAB))
        pos = stop
    _require(pos == end or not known)
    return pos, timestamp, latest


def _webm(data):
    size = len(data)
    element, after = _ebml_id(data, 0)
    length, body = _ebml_size(data, after)
    _require(element == EBML_HEADER and length is not None and body + length <= size)
    doctype = b""
    for child, a, b in _ebml_elements(data, body, body + length):
        if child == 0x4282:
            doctype = data[a:b].rstrip(b"\x00")
        elif child == 0x42F7:
            _require(_uint(data, a, b) == 1)
        elif child == 0x42F2:
            _require(_uint(data, a, b) <= 4)
        elif child == 0x42F3:
            _require(_uint(data, a, b) <= 8)
    _require(doctype == b"webm")
    element, after = _ebml_id(data, body + length)
    length, pos = _ebml_size(data, after)
    _require(element == SEGMENT)
    end = size if length is None else pos + length
    _require(end == size)
    scale, duration, info, tracks, count = 1_000_000, 0.0, False, None, 0
    clusters, last_time = 0, 0
    while pos < end:
        count += 1
        _require(count <= MAX_ELEMENTS)
        element, after = _ebml_id(data, pos)
        length, body = _ebml_size(data, after)
        if element == CLUSTER:
            stop = end if length is None else body + length
            _require(stop <= end)
            pos, timestamp, latest = _webm_cluster(data, body, stop, length is not None)
            clusters += 1
            last_time = max(last_time, timestamp + latest)
            continue
        _require(length is not None and body + length <= end)
        _require(element != ATTACHMENTS)  # Embedded files are refused.
        _require(element in LEVEL1 or element in GLOBAL)
        if element == INFO:
            _require(not info)
            info = True
            for child, a, b in _ebml_elements(data, body, body + length):
                if child == 0x2AD7B1:
                    scale = _uint(data, a, b)
                elif child == 0x4489:
                    duration = _float(data, a, b)
        elif element == TRACKS:
            _require(tracks is None)
            tracks = _webm_tracks(data, body, body + length)
        pos = body + length
    _require(pos == end and info and tracks and clusters)
    _require(0 < scale <= 1_000_000_000 and duration == duration and duration >= 0)
    seconds = (duration or last_time) * scale / 1e9
    _require(0 < seconds <= MAX_MEDIA_SECONDS)
    video = [t for t in tracks if t["type"] == 1]
    audio = [t for t in tracks if t["type"] == 2]
    _require(len(video) + len(audio) == len(tracks))
    _require(all(t["codec"] in WEBM_AUDIO for t in audio))
    _require(all(t["codec"] in WEBM_VIDEO for t in video))
    if video:
        for track in video:
            width, height = track["width"], track["height"]
            _require(1 <= width <= MAX_VIDEO_SIDE and 1 <= height <= MAX_VIDEO_SIDE)
            _require(width * height <= MAX_VIDEO_PIXELS)
        return _facts("video/webm")
    _require(len(audio) == 1)
    rate, channels = int(audio[0]["rate"]), audio[0]["channels"]
    _require(8000 <= rate <= 192000 and 1 <= channels <= 8)
    return _facts("audio/webm", rate, channels, round(seconds * rate))


# MP4 / ISO BMFF ----------------------------------------------------------------

MP4_BRANDS = {
    b"isom", b"iso2", b"iso3", b"iso4", b"iso5", b"iso6", b"mp41", b"mp42",
    b"avc1", b"M4A ", b"M4V ", b"dash", b"msnv", b"av01",
}
MP4_VIDEO = {b"avc1", b"avc3", b"hvc1", b"hev1", b"av01", b"vp09"}
MP4_AUDIO = {b"mp4a", b"Opus", b"fLaC"}


def _boxes(data, pos, end, *, top=False):
    count = 0
    while pos < end:
        _require(pos + 8 <= end)
        size, kind = struct.unpack(">I4s", data[pos : pos + 8])
        header = 8
        if size == 1:
            size, header = struct.unpack(">Q", data[pos + 8 : pos + 16])[0], 16
        elif size == 0:
            _require(top)  # Size 0 ("to EOF") only for the last top-level box.
            size = end - pos
        _require(size >= header and pos + size <= end)
        count += 1
        _require(count <= MAX_ELEMENTS)
        yield kind, pos + header, pos + size
        pos += size
    _require(pos == end)


def _child(data, start, end, kind):
    found = [(a, b) for k, a, b in _boxes(data, start, end) if k == kind]
    _require(len(found) == 1)
    return found[0]


def _full_box_times(data, start):
    """(timescale, duration) of an mvhd/mdhd body."""
    if data[start] == 1:
        return struct.unpack(">IQ", data[start + 20 : start + 32])
    _require(data[start] == 0)
    return struct.unpack(">II", data[start + 12 : start + 20])


def _mp4_track(data, start, end):
    tkhd = _child(data, start, end, b"tkhd")[0]
    track_id = struct.unpack_from(">I", data, tkhd + (20 if data[tkhd] == 1 else 12))[0]
    offset = 88 if data[tkhd] == 1 else 76
    width, height = struct.unpack(">II", data[tkhd + offset : tkhd + offset + 8])
    mdia = _child(data, start, end, b"mdia")
    hdlr = _child(data, *mdia, b"hdlr")[0]
    handler = data[hdlr + 8 : hdlr + 12]
    scale, length = _full_box_times(data, _child(data, *mdia, b"mdhd")[0])
    minf = _child(data, *mdia, b"minf")
    dref = _child(data, *_child(data, *minf, b"dinf"), b"dref")
    _require(struct.unpack(">I", data[dref[0] + 4 : dref[0] + 8])[0] >= 1)
    for kind, a, _ in _boxes(data, dref[0] + 8, dref[1]):
        # External data references would make the player fetch other bytes.
        _require(kind in (b"url ", b"urn ") and data[a + 3] & 1)
    stsd = _child(data, *_child(data, *minf, b"stbl"), b"stsd")
    entries = list(_boxes(data, stsd[0] + 8, stsd[1]))
    _require(len(entries) == struct.unpack(">I", data[stsd[0] + 4 : stsd[0] + 8])[0])
    _require(len(entries) >= 1)
    codecs = {kind for kind, _, _ in entries}
    channels = 0
    if handler == b"soun":
        _require(codecs <= MP4_AUDIO)
        channels = struct.unpack(">H", data[entries[0][1] + 16 : entries[0][1] + 18])[0]
    else:
        _require(handler == b"vide" and codecs <= MP4_VIDEO)
    return {
        "id": track_id,
        "handler": handler,
        "width": width >> 16,
        "height": height >> 16,
        "scale": scale,
        "length": length,
        "channels": channels,
    }


def _trex_defaults(data, start, end):
    """track_ID -> default_sample_duration from each mvex/trex."""
    defaults = {}
    for kind, a, b in _boxes(data, start, end):
        if kind == b"trex":
            _require(b - a == 24)
            track_id, _, duration = struct.unpack(">III", data[a + 4 : a + 16])
            _require(track_id not in defaults)
            defaults[track_id] = duration
    return defaults


def _traf_duration(data, start, end, trex):
    """(track_ID, summed sample durations) of one moof/traf.

    A sample's duration comes from its trun entry, else the tfhd default, else
    the trex default. Every trun must hold exactly its declared entries.
    """
    tfhd_start, _ = _child(data, start, end, b"tfhd")
    flags = int.from_bytes(data[tfhd_start + 1 : tfhd_start + 4], "big")
    track_id = struct.unpack(">I", data[tfhd_start + 4 : tfhd_start + 8])[0]
    _require(track_id in trex)
    at = tfhd_start + 8 + (8 if flags & 0x01 else 0) + (4 if flags & 0x02 else 0)
    default = trex[track_id]
    if flags & 0x08:
        default = struct.unpack(">I", data[at : at + 4])[0]
    total = 0
    for kind, a, b in _boxes(data, start, end):
        if kind != b"trun":
            continue
        flags = int.from_bytes(data[a + 1 : a + 4], "big")
        count = struct.unpack(">I", data[a + 4 : a + 8])[0]
        at = a + 8 + (4 if flags & 0x01 else 0) + (4 if flags & 0x04 else 0)
        entry = 4 * sum(bool(flags & bit) for bit in (0x100, 0x200, 0x400, 0x800))
        _require(at + count * entry == b)
        if flags & 0x100:
            # The duration is the first field of each entry.
            total += sum(
                struct.unpack_from(">I", data, i)[0] for i in range(at, b, entry)
            )
        else:
            total += count * default
    return track_id, total


def _fragment_seconds(data, top, moov, tracks):
    """Longest track in seconds, summed over every moof (empty_moov files)."""
    mvex = [(a, b) for kind, a, b in _boxes(data, *moov) if kind == b"mvex"]
    _require(len(mvex) == 1)
    trex = _trex_defaults(data, *mvex[0])
    scales = {track["id"]: track["scale"] for track in tracks}
    _require(set(trex) <= set(scales) and all(scales.values()))
    totals = dict.fromkeys(scales, 0)
    for kind, a, b in top:
        if kind != b"moof":
            continue
        for inner, x, y in _boxes(data, a, b):
            if inner == b"traf":
                track_id, duration = _traf_duration(data, x, y, trex)
                totals[track_id] += duration
    return max(totals[track] / scales[track] for track in totals)


def _mp4(data):
    size = len(data)
    top = list(_boxes(data, 0, size, top=True))
    kinds = [kind for kind, _, _ in top]
    _require(kinds[0] == b"ftyp" and kinds.count(b"moov") == 1)
    _require(b"mdat" in kinds)
    _, a, b = top[0]
    _require(b - a >= 8 and (b - a) % 4 == 0)
    brands = {data[a : a + 4]} | {data[i : i + 4] for i in range(a + 8, b, 4)}
    _require(brands & MP4_BRANDS)
    moov = next((a, b) for kind, a, b in top if kind == b"moov")
    scale, length = _full_box_times(data, _child(data, *moov, b"mvhd")[0])
    tracks, fragment = [], 0
    for kind, a, b in _boxes(data, *moov):
        if kind == b"trak":
            tracks.append(_mp4_track(data, a, b))
        elif kind == b"mvex":
            mehd = [(x, y) for k, x, y in _boxes(data, a, b) if k == b"mehd"]
            if mehd:
                start = mehd[0][0]
                fragment = struct.unpack(
                    ">Q" if data[start] == 1 else ">I",
                    data[start + 4 : start + (12 if data[start] == 1 else 8)],
                )[0]
    _require(scale > 0 and tracks)
    _require(len({track["id"] for track in tracks}) == len(tracks))
    if length or fragment:
        seconds = (length or fragment) / scale
    else:
        # Fragmented with an empty moov and no mehd: only the fragments know.
        seconds = _fragment_seconds(data, top, moov, tracks)
    _require(0 < seconds <= MAX_MEDIA_SECONDS)
    video = [t for t in tracks if t["handler"] == b"vide"]
    if video:
        for track in video:
            _require(1 <= track["width"] <= MAX_VIDEO_SIDE)
            _require(1 <= track["height"] <= MAX_VIDEO_SIDE)
            _require(track["width"] * track["height"] <= MAX_VIDEO_PIXELS)
        return _facts("video/mp4")
    _require(len(tracks) == 1)
    track = tracks[0]
    rate, channels = track["scale"], track["channels"]
    _require(8000 <= rate <= 192000 and 1 <= channels <= 8)
    samples = track["length"] or round(seconds * rate)
    _require(0 < samples <= rate * MAX_MEDIA_SECONDS)
    return _facts("audio/mp4", rate, channels, samples)


# PDF (served download-only) ----------------------------------------------------

PDF_VERSION = re.compile(rb"%PDF-(?:1\.[0-7]|2\.0)[\r\n]")
PDF_OBJECT = re.compile(rb"\d{1,10}\s+\d{1,5}\s+obj\b")
# Best effort only: names inside compressed object streams are not visible.
PDF_ACTIVE = re.compile(
    rb"/(?:JavaScript|JS|Launch|EmbeddedFiles?|XFA|RichMedia)(?![A-Za-z0-9#])"
)


def _pdf(data):
    size = len(data)
    _require(PDF_VERSION.match(data))
    eof = data.rfind(b"%%EOF")
    _require(eof >= max(0, size - 1024))
    _require(not data[eof + 5 :].strip(b"\r\n\t \x00"))
    start = data.rfind(b"startxref", max(0, size - 1100), eof)
    _require(start > 0)
    number = data[start + 9 : eof].strip()
    _require(number.isdigit() and len(number) <= 10)
    offset = int(number)
    _require(9 <= offset < start)
    target = data[offset : offset + 64].lstrip(b"\r\n\t ")
    _require(target.startswith(b"xref") or PDF_OBJECT.match(target))
    _require(not PDF_ACTIVE.search(data))
    return _facts("application/pdf")


def sniff(head):
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return _wav
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return _webp
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return _png
    if head[:3] == b"\xff\xd8\xff":
        return _jpeg
    if head[:4] == b"OggS":
        return _ogg
    if head[:4] == b"fLaC":
        return _flac
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return _webm
    if head[4:8] == b"ftyp":
        return _mp4
    if head[:5] == b"%PDF-":
        return _pdf
    if head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and head[1] >= 0xE0):
        return _mp3
    raise ValueError


def probe(path):
    length = os.stat(path).st_size
    if not 12 <= length <= MAX_BYTES:
        raise ValueError
    with open(path, "rb") as source:
        data = source.read(MAX_BYTES + 1)
    if len(data) != length:
        raise ValueError
    try:
        return sniff(data[:16])(data)
    except MALFORMED:
        raise ValueError from None


if __name__ == "__main__":
    try:
        print(json.dumps(probe(sys.argv[1]), separators=(",", ":")))
    except Exception:
        # Do not include filenames, parser details or input fragments.
        sys.exit(REJECTED_EXIT)
