"""Read a source ZIP through the same packer as a selected directory."""

import stat
import struct
from contextlib import ExitStack, contextmanager
from pathlib import PurePosixPath
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile
from zlib import error as CompressionError

from django.core.files import File

from simulate.services.hosted_harness import HostedHarnessError

_EXCLUDED = {
    ".git",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
    "__MACOSX",
}
_ENV_TEMPLATES = {".env.example", ".env.sample", ".env.template"}


def _check_directory_size(upload, max_files):
    # ZipFile builds an in-memory index before exposing the first member.
    # Bound that index before opening the archive, including ignored entries.
    trailer = struct.Struct("<4s4H2IH")
    upload.seek(max(0, upload.size - 65535 - trailer.size - 20))
    tail = upload.read()
    offset = tail.rfind(b"PK\x05\x06")
    if offset < 0 or len(tail) - offset < trailer.size:
        raise BadZipFile("missing ZIP directory")
    _, disk, start_disk, _, count, size, _, comment = trailer.unpack_from(tail, offset)
    zip64 = offset >= 20 and tail[offset - 20 : offset - 16] == b"PK\x06\x07"
    if (
        disk
        or start_disk
        or zip64
        or count > max(4096, max_files * 4)
        or size > 4 * 1024 * 1024
        or offset + trailer.size + comment != len(tail)
    ):
        raise BadZipFile("unsupported ZIP directory")


@contextmanager
def unpack_source_zip(upload, max_bytes, max_files):

    def invalid(message):
        return HostedHarnessError("source_zip_invalid", message, status_code=400)

    if not upload.name.lower().endswith(".zip") or not 0 < upload.size <= max_bytes:
        raise invalid("Choose a ZIP within the source upload size limit.")
    try:
        _check_directory_size(upload, max_files)
        with ZipFile(upload) as archive, ExitStack() as stack:
            entries = []
            paths = []
            total = 0
            for entry in archive.infolist():
                path = entry.orig_filename.rstrip("/")
                parts = path.split("/")
                if (
                    any(part in {"", ".", ".."} for part in parts)
                    or "\\" in path
                    or ":" in parts[0]
                    or "\x00" in path
                ):
                    raise invalid("The ZIP contains an invalid repository path.")
                leaf = parts[-1].lower()
                if (
                    entry.is_dir()
                    or _EXCLUDED.intersection(parts)
                    or leaf == ".ds_store"
                    or (
                        (leaf == ".env" or leaf.startswith(".env."))
                        and leaf not in _ENV_TEMPLATES
                    )
                ):
                    continue
                if (
                    stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFREG)
                    or entry.flag_bits & 1
                    or entry.compress_type not in (ZIP_STORED, ZIP_DEFLATED)
                ):
                    raise invalid("Use regular, unencrypted source files in the ZIP.")
                total += entry.file_size
                if len(entries) >= max_files or total > max_bytes:
                    raise invalid(
                        "The unpacked ZIP exceeds the source upload size or file-count limit."
                    )
                entries.append(entry)
                paths.append(path)
            if not entries:
                raise invalid("The ZIP contains no uploadable source files.")
            root = paths[0].split("/")[0] + "/"
            if all(path.startswith(root) for path in paths):
                paths = [path[len(root) :] for path in paths]
            members = set(paths)
            if len(members) != len(paths) or any(
                str(parent) in members
                for path in paths
                for parent in PurePosixPath(path).parents
            ):
                raise invalid("The ZIP contains conflicting source paths.")
            files = [
                File(stack.enter_context(archive.open(entry)), name=path)
                for entry, path in zip(entries, paths, strict=True)
            ]
            yield files, paths, PurePosixPath(upload.name).stem
    except (BadZipFile, EOFError, NotImplementedError, CompressionError) as exc:
        raise invalid(
            "The ZIP could not be read. Choose a complete, unencrypted ZIP."
        ) from exc
