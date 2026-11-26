"""Read a source ZIP through the same packer as a selected directory."""

import stat
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


@contextmanager
def unpack_source_zip(upload, max_bytes, max_files):

    def invalid(message):
        return HostedHarnessError("source_zip_invalid", message, status_code=400)

    if not upload.name.lower().endswith(".zip") or not 0 < upload.size <= max_bytes:
        raise invalid("Choose a ZIP within the source upload size limit.")
    try:
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
            files = [
                File(stack.enter_context(archive.open(entry)), name=path)
                for entry, path in zip(entries, paths, strict=True)
            ]
            yield files, paths, PurePosixPath(upload.name).stem
    except (BadZipFile, EOFError, NotImplementedError, CompressionError) as exc:
        raise invalid(
            "The ZIP could not be read. Choose a complete, unencrypted ZIP."
        ) from exc
