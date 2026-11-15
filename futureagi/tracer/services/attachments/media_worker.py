"""Credential-free media worker. Runs only as ``python -I -S media_worker.py``.

Standard library only: no Django, settings, application code or site-packages.
The parent passes an already-open read-only input descriptor and, for producing
operations, one already-open output descriptor. Before the first untrusted byte
is read this process sets its own resource limits and installs a seccomp-bpf
ALLOWLIST: afterwards it cannot open files, create sockets, exec, fork, ptrace or
signal other processes. Any other syscall kills the process (SIGSYS). See
SANDBOX.md next to this file for the threat model and supported runtimes.

Exit codes: 0 success, 2 rejected input, 3 sandbox unavailable (never a
permissive fallback). Nothing derived from the input is written to stderr.
"""

import ctypes
import json
import math
import os
import resource
import struct
import sys

HEADER_BYTES = 44
SEGMENT_MAX_BYTES = 8 * 1024 * 1024
SEGMENT_MAX_SECONDS = 30
SOURCE_MAX_BYTES = 1024 * 1024 * 1024
SOURCE_MAX_FRAMES_SECONDS = 120 * 60
BUFFER_BYTES = 256 * 1024
PEAK_LEVELS_MS = (10, 100, 1000)

# seccomp_data offsets and BPF opcodes (linux/filter.h, linux/seccomp.h).
_LD_W_ABS, _JEQ_K, _JGE_K, _RET_K = 0x20, 0x15, 0x35, 0x06
_ALLOW, _KILL_PROCESS = 0x7FFF0000, 0x80000000
_PR_SET_NO_NEW_PRIVS, _PR_SET_SECCOMP, _SECCOMP_MODE_FILTER = 38, 22, 2
_X32_BIT = 0x40000000
# Minimal set for reading/writing already-open descriptors and interpreter exit.
_SYSCALLS = {
    "x86_64": (
        0xC000003E,
        {
            "read": 0,
            "write": 1,
            "close": 3,
            "fstat": 5,
            "lseek": 8,
            "mmap": 9,
            "munmap": 11,
            "brk": 12,
            "rt_sigaction": 13,
            "rt_sigprocmask": 14,
            "rt_sigreturn": 15,
            "pread64": 17,
            "mremap": 25,
            "madvise": 28,
            "getpid": 39,
            "exit": 60,
            "sigaltstack": 131,
            "gettid": 186,
            "futex": 202,
            "clock_gettime": 228,
            "exit_group": 231,
            "newfstatat": 262,
        },
    ),
    "aarch64": (
        0xC00000B7,
        {
            "read": 63,
            "write": 64,
            "close": 57,
            "fstat": 80,
            "lseek": 62,
            "mmap": 222,
            "munmap": 215,
            "brk": 214,
            "rt_sigaction": 134,
            "rt_sigprocmask": 135,
            "rt_sigreturn": 139,
            "pread64": 67,
            "mremap": 216,
            "madvise": 233,
            "getpid": 172,
            "exit": 93,
            "sigaltstack": 132,
            "gettid": 178,
            "futex": 98,
            "clock_gettime": 113,
            "exit_group": 94,
            "newfstatat": 79,
        },
    ),
}


class Rejected(Exception):
    pass


class Unavailable(Exception):
    pass


class _Filter(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_ushort),
        ("jt", ctypes.c_ubyte),
        ("jf", ctypes.c_ubyte),
        ("k", ctypes.c_uint),
    ]


class _Program(ctypes.Structure):
    _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(_Filter))]


def filter_program(machine):
    """Return BPF instructions; kill on wrong arch, x32 ABI or unlisted syscall."""
    if machine not in _SYSCALLS:
        raise Unavailable
    arch, table = _SYSCALLS[machine]
    numbers = sorted(set(table.values()))
    prefix: list[tuple[int, int, int, int] | None] = [
        (_LD_W_ABS, 0, 0, 4),
        None,
        (_LD_W_ABS, 0, 0, 0),
    ]
    if machine == "x86_64":
        prefix.append(None)
    body_start = len(prefix)
    kill = body_start + len(numbers)
    allow = kill + 1
    prefix[1] = (_JEQ_K, 0, kill - 2, arch)
    if machine == "x86_64":
        prefix[3] = (_JGE_K, kill - 4, 0, _X32_BIT)
    program: list[tuple[int, int, int, int]] = []
    for instruction in prefix:
        if instruction is None:  # every placeholder is filled above
            raise Unavailable
        program.append(instruction)
    for offset, number in enumerate(numbers):
        position = body_start + offset
        program.append((_JEQ_K, allow - position - 1, 0, number))
    program += [(_RET_K, 0, 0, _KILL_PROCESS), (_RET_K, 0, 0, _ALLOW)]
    if any(jt > 255 or jf > 255 for _, jt, jf, _ in program):
        raise Unavailable
    return program


def confine(output_limit):
    """Resource limits, then no_new_privs, then the seccomp allowlist."""
    try:
        limits = (
            (resource.RLIMIT_AS, 512 * 1024 * 1024),
            (resource.RLIMIT_CPU, 30),
            (resource.RLIMIT_FSIZE, output_limit),
            (resource.RLIMIT_NOFILE, 16),
            (resource.RLIMIT_CORE, 0),
        )
        for which, value in limits:
            resource.setrlimit(which, (value, value))
        program = filter_program(os.uname().machine)
        array = (_Filter * len(program))(*(_Filter(*item) for item in program))
        fprog = _Program(len(program), array)
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl.argtypes = [
            ctypes.c_int,
            ctypes.c_ulong,
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_ulong,
        ]
        if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, None, 0, 0) != 0:
            raise Unavailable
        if libc.prctl(_PR_SET_SECCOMP, _SECCOMP_MODE_FILTER, ctypes.byref(fprog), 0, 0):
            raise Unavailable
    except Unavailable:
        raise
    except Exception:  # noqa: BLE001 - kernel/ctypes boundary in the -I -S child; fails closed as unavailable
        raise Unavailable from None


def _pread_exact(fd, length, offset):
    chunks, remaining = [], length
    while remaining:
        chunk = os.pread(fd, min(BUFFER_BYTES, remaining), offset)
        if not chunk:
            raise Rejected
        chunks.append(chunk)
        offset += len(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def wav_header(*, channels, rate, frames):
    align = channels * 2
    data = frames * align
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        data + 36,
        b"WAVE",
        b"fmt ",
        16,
        1,
        channels,
        rate,
        rate * align,
        align,
        16,
        b"data",
        data,
    )


def read_wav(fd, *, max_bytes, max_seconds):
    """Canonical 44-byte PCM s16 header only; ancillary chunks are rejected."""
    length = os.fstat(fd).st_size
    if not HEADER_BYTES < length <= max_bytes:
        raise Rejected
    header = _pread_exact(fd, HEADER_BYTES, 0)
    (
        magic,
        size,
        kind,
        fmt,
        fmt_size,
        encoding,
        channels,
        rate,
        byte_rate,
        align,
        bits,
        tag,
        data_size,
    ) = struct.unpack("<4sI4s4sIHHIIHH4sI", header)
    if (
        magic != b"RIFF"
        or kind != b"WAVE"
        or size + 8 != length
        or fmt != b"fmt "
        or fmt_size != 16
        or encoding != 1
        or channels not in {1, 2}
        or bits != 16
        or not 8000 <= rate <= 96000
        or align != channels * 2
        or byte_rate != rate * align
        or tag != b"data"
        or data_size + HEADER_BYTES != length
        or data_size == 0
        or data_size % align
    ):
        raise Rejected
    frames = data_size // align
    if frames > rate * max_seconds:
        raise Rejected
    return {
        "mime_type": "audio/wav",
        "sample_rate": rate,
        "channels": channels,
        "frame_count": frames,
    }


def probe(path):
    """In-process parser entry used only by unit tests as a parser double."""
    with open(path, "rb") as source:
        try:
            return read_wav(
                source.fileno(),
                max_bytes=SEGMENT_MAX_BYTES,
                max_seconds=SEGMENT_MAX_SECONDS,
            )
        except Rejected:
            raise ValueError from None


def op_probe(fd_in, _fd_out, _args):
    return read_wav(fd_in, max_bytes=SEGMENT_MAX_BYTES, max_seconds=SEGMENT_MAX_SECONDS)


def op_source(fd_in, _fd_out, _args):
    facts = read_wav(
        fd_in, max_bytes=SOURCE_MAX_BYTES, max_seconds=SOURCE_MAX_FRAMES_SECONDS
    )
    facts["duration_ms"] = -(-facts["frame_count"] * 1000 // facts["sample_rate"])
    return facts


def op_segment(fd_in, fd_out, args):
    """Emit one independently valid WAV of frames [start, start + count)."""
    start, count = (int(value) for value in args)
    facts = read_wav(
        fd_in, max_bytes=SOURCE_MAX_BYTES, max_seconds=SOURCE_MAX_FRAMES_SECONDS
    )
    rate, channels = facts["sample_rate"], facts["channels"]
    align = channels * 2
    if (
        start < 0
        or count < 1
        or start + count > facts["frame_count"]
        or count > rate * SEGMENT_MAX_SECONDS
        or HEADER_BYTES + count * align > SEGMENT_MAX_BYTES
    ):
        raise Rejected
    os.write(fd_out, wav_header(channels=channels, rate=rate, frames=count))
    offset, remaining = HEADER_BYTES + start * align, count * align
    while remaining:
        chunk = _pread_exact(fd_in, min(BUFFER_BYTES, remaining), offset)
        _write_all(fd_out, chunk)
        offset += len(chunk)
        remaining -= len(chunk)
    return {
        "sample_rate": rate,
        "channels": channels,
        "frame_count": count,
        "byte_length": HEADER_BYTES + count * align,
    }


def _write_all(fd, data):
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def _frames(fd, facts):
    """Yield bounded blocks of whole frames from a validated segment."""
    align = facts["channels"] * 2
    block = (BUFFER_BYTES // align) * align
    offset, remaining = HEADER_BYTES, facts["frame_count"] * align
    while remaining:
        chunk = _pread_exact(fd, min(block, remaining), offset)
        offset += len(chunk)
        remaining -= len(chunk)
        yield chunk


def op_split(fd_in, fd_out, args):
    """Stream one channel of a validated stereo segment into a mono WAV."""
    (channel,) = (int(value) for value in args)
    facts = op_probe(fd_in, None, ())
    if facts["channels"] != 2 or channel not in {0, 1}:
        raise Rejected
    frames = facts["frame_count"]
    os.write(fd_out, wav_header(channels=1, rate=facts["sample_rate"], frames=frames))
    for chunk in _frames(fd_in, facts):
        samples = memoryview(chunk).cast("h")
        _write_all(fd_out, samples[channel::2].tobytes())
    return {
        "sample_rate": facts["sample_rate"],
        "channels": 1,
        "frame_count": frames,
        "byte_length": HEADER_BYTES + frames * 2,
    }


def op_peaks(fd_in, fd_out, _args):
    """Fixed-resolution min/max pyramid; size is bounded by segment duration.

    Output: for every level, for every channel, ``buckets`` pairs of
    little-endian int16 (min, max). Levels use about 10 ms, 100 ms and 1 s
    buckets; the exact ``frames_per_bucket`` is reported for seeking.
    """
    facts = op_probe(fd_in, None, ())
    rate, channels = facts["sample_rate"], facts["channels"]
    base = max(1, rate * PEAK_LEVELS_MS[0] // 1000)
    buckets = math.ceil(facts["frame_count"] / base)
    lows = [[32767] * buckets for _ in range(channels)]
    highs = [[-32768] * buckets for _ in range(channels)]
    frame = 0
    for chunk in _frames(fd_in, facts):
        samples = memoryview(chunk).cast("h")
        count = len(samples) // channels
        for channel in range(channels):
            values = samples[channel::channels]
            low, high = lows[channel], highs[channel]
            position = 0
            while position < count:
                bucket = (frame + position) // base
                end = min(count, (bucket + 1) * base - frame)
                part = values[position:end]
                low[bucket] = min(low[bucket], min(part))
                high[bucket] = max(high[bucket], max(part))
                position = end
        frame += count
    levels = []
    for ms in PEAK_LEVELS_MS:
        factor = ms // PEAK_LEVELS_MS[0]
        count = math.ceil(buckets / factor)
        for channel in range(channels):
            pairs = []
            for index in range(count):
                window = slice(index * factor, (index + 1) * factor)
                pairs += [min(lows[channel][window]), max(highs[channel][window])]
            _write_all(fd_out, struct.pack(f"<{len(pairs)}h", *pairs))
        levels.append({"frames_per_bucket": base * factor, "buckets": count})
    return {
        "sample_rate": rate,
        "channels": channels,
        "frame_count": facts["frame_count"],
        "levels": levels,
    }


def _deny_socket(_fd_in, _fd_out, _args):
    import socket  # noqa: PLC0415 - imported before the call, after confinement

    socket.socket()
    return {}


def _deny_open(_fd_in, _fd_out, _args):
    os.open("/etc/hostname", os.O_RDONLY)
    return {}


def _deny_exec(_fd_in, _fd_out, _args):
    os.execv("/bin/true", ["/bin/true"])  # never returns


OPERATIONS = {
    "probe": op_probe,
    "source": op_source,
    "segment": op_segment,
    "split": op_split,
    "peaks": op_peaks,
    # Negative controls: each must die with SIGSYS when confinement is active.
    "deny-socket": _deny_socket,
    "deny-open": _deny_open,
    "deny-exec": _deny_exec,
}


def main(argv):
    try:
        operation = OPERATIONS[argv[1]]
        fd_in, fd_out, output_limit = int(argv[2]), int(argv[3]), int(argv[4])
        if operation is _deny_socket:
            import socket  # noqa: F401, PLC0415 - load the module before the filter
        confine(output_limit)
    except Exception:  # noqa: BLE001 - child entry point (no logging); fails closed as unavailable
        return 3
    try:
        result = operation(fd_in, fd_out if fd_out >= 0 else None, argv[5:])
    except (Rejected, ValueError, struct.error, OverflowError):
        return 2
    except Exception:  # noqa: BLE001 - untrusted-media parser boundary; any failure rejects the input
        return 2
    sys.stdout.write(json.dumps(result, separators=(",", ":")))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
