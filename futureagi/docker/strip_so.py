#!/usr/bin/env python3
"""Strip debug sections from Python extension modules, safely and quickly.

Measured on futureagi/future-agi:latest (amd64): the site-packages ELF files
carry 69.5 MB of .debug_* sections (zstandard alone 21.7 MB; also uvloop,
scipy, temporalio, lz4). --strip-debug removes them: ~ -69 MB unpacked,
~ -17 MB gzip.

`strip` has corrupted auditwheel/patchelf-rewritten libraries in the past
("ELF load command address/offset not properly aligned": NixOS/patchelf#10,
pypa/auditwheel#63). Rules applied here:
  * only `--strip-debug` (dynamic symbols and program headers are untouched);
  * vendored libraries in `<pkg>.libs/` and versioned `*.so.N` files are
    skipped (that is where auditwheel/patchelf rewrites live);
  * only files that actually contain >= 64 KiB of .debug_*/.zdebug_* sections
    are touched (found by reading ELF section headers in pure Python, so the
    scan is fast even under QEMU);
  * a stripped copy replaces the original only if it still dlopen()s with
    RTLD_NOW in a fresh interpreter; originals that do not dlopen on their own
    are never touched. dlopen checks run one process per batch and bisect only
    when a batch fails.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys

MIN_DEBUG_BYTES = 64 * 1024
LOAD_CHECK = (
    "import ctypes, sys\n"
    "bad = []\n"
    "for p in sys.argv[1:]:\n"
    "    try:\n"
    "        ctypes.CDLL(p)\n"  # CPython ORs RTLD_NOW into the mode
    "    except OSError:\n"
    "        bad.append(p)\n"
    "print('\\n'.join(bad))\n"
)


def debug_bytes(path: str) -> int:
    """Total size of .debug_*/.zdebug_* sections; 0 for non-ELF files."""
    with open(path, "rb") as f:
        ident = f.read(16)
        if len(ident) < 16 or ident[:4] != b"\x7fELF":
            return 0
        is64 = ident[4] == 2
        end = "<" if ident[5] == 1 else ">"
        if is64:
            f.seek(0x28)
            (shoff,) = struct.unpack(end + "Q", f.read(8))
            f.seek(0x3A)
        else:
            f.seek(0x20)
            (shoff,) = struct.unpack(end + "I", f.read(4))
            f.seek(0x2E)
        shentsize, shnum, shstrndx = struct.unpack(end + "HHH", f.read(6))
        if not shoff or not shnum or shstrndx >= shnum:
            return 0
        f.seek(shoff)
        table = f.read(shentsize * shnum)

        def section(i: int) -> tuple[int, int, int]:
            raw = table[i * shentsize : (i + 1) * shentsize]
            if is64:
                name, _type, _flags, _addr, off, size = struct.unpack(
                    end + "IIQQQQ", raw[:40]
                )
            else:
                name, _type, _flags, _addr, off, size = struct.unpack(
                    end + "IIIIII", raw[:24]
                )
            return name, off, size

        _, str_off, str_size = section(shstrndx)
        f.seek(str_off)
        names = f.read(str_size)
        total = 0
        for i in range(shnum):
            name_off, _, size = section(i)
            name = names[name_off : names.find(b"\0", name_off)]
            if name.startswith((b".debug", b".zdebug")):
                total += size
        return total


def failing(paths: list[str]) -> set[str]:
    """Paths that fail dlopen(RTLD_NOW) in a fresh interpreter."""
    if not paths:
        return set()
    proc = subprocess.run(
        [sys.executable, "-c", LOAD_CHECK, *paths], capture_output=True, text=True
    )
    if proc.returncode == 0:
        return {line for line in proc.stdout.splitlines() if line}
    if len(paths) == 1:  # the loader itself crashed on this file
        return set(paths)
    mid = len(paths) // 2
    return failing(paths[:mid]) | failing(paths[mid:])


def main(root: str) -> None:
    if not shutil.which("strip"):
        sys.exit("strip_so: `strip` (binutils) not found in the build stage")
    candidates = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.endswith(".libs")]
        for name in filenames:
            if not name.endswith(".so"):
                continue
            path = os.path.join(dirpath, name)
            if os.path.islink(path):
                continue
            try:
                if debug_bytes(path) >= MIN_DEBUG_BYTES:
                    candidates.append(path)
            except (OSError, struct.error):
                continue

    unloadable = failing(candidates)
    stripped: dict[str, str] = {}
    for path in candidates:
        if path in unloadable:
            continue
        tmp = path + ".strip-tmp"
        proc = subprocess.run(
            ["strip", "--strip-debug", "-o", tmp, path], capture_output=True
        )
        if proc.returncode == 0 and os.path.getsize(tmp) < os.path.getsize(path):
            stripped[path] = tmp
        elif os.path.exists(tmp):
            os.unlink(tmp)

    broken = failing(list(stripped.values()))
    saved = kept = 0
    for path, tmp in stripped.items():
        if tmp in broken:
            os.unlink(tmp)
            kept += 1
            continue
        saved += os.path.getsize(path) - os.path.getsize(tmp)
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
    print(
        f"strip_so: {len(stripped) - kept}/{len(candidates)} files stripped, "
        f"-{saved / 1e6:.1f} MB; {kept} kept (stripped copy failed dlopen), "
        f"{len(unloadable)} skipped (original does not dlopen standalone)"
    )


if __name__ == "__main__":
    main(sys.argv[1])
