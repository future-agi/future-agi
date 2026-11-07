"""Content-addressed manifests and bounded, in-memory skill archives.

No archive is extracted to disk and no uploaded script is executed.
"""

from __future__ import annotations

import hashlib
import io
import json
import posixpath
import re
import stat
import zipfile
import zlib
from collections import Counter

from model_hub.services.skills_hub import limits
from model_hub.services.skills_hub.checks import path_is_valid, run_checks, text_is_valid
from model_hub.services.skills_hub.errors import SkillsHubError

_LOCK_PATH = ".futureagi-skills.lock.json"
_ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")


def file_sha256(content: str) -> str:
    """Hash the exact UTF-8 bytes, preserving newlines and Unicode."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def manifest_digest(files: list[dict]) -> str:
    """Hash canonical path/hash pairs independently of input order."""
    manifest = sorted([[file["path"], file["sha256"] if "sha256" in file else file_sha256(file["content"])] for file in files])
    encoded = json.dumps(manifest, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _too_large(message: str) -> SkillsHubError:
    return SkillsHubError("payload_too_large", message, status=413)


def build_zip(skills: list[dict], *, origin: str, skipped: list[dict] | None = None) -> bytes:
    """Build byte-identical downloads for identical skill and lock metadata."""
    if len(skills) > limits.MAX_DOWNLOAD_SKILLS:
        raise _too_large("Download at most 50 skills at a time.")
    entries: dict[str, bytes] = {}
    lock_skills = []
    names: set[str] = set()
    for skill in sorted(skills, key=lambda entry: entry["name"]):
        name = skill["name"]
        if len(name) > limits.MAX_NAME or not re.fullmatch(limits.NAME_RE, name) or name in names:
            raise SkillsHubError("import_invalid", "Each downloaded skill needs a unique valid name.", status=400)
        names.add(name)
        if len(skill["files"]) > limits.MAX_FILES:
            raise _too_large("Keep each skill within 200 files.")
        paths: set[str] = set()
        total = 0
        for file in skill["files"]:
            path, content = file["path"], file["content"]
            if not path_is_valid(path) or path.casefold() in paths or not text_is_valid(content):
                raise SkillsHubError("import_invalid", "Download files must have unique safe paths and UTF-8 text.", status=400)
            paths.add(path.casefold())
            encoded = content.encode("utf-8")
            total += len(encoded)
            if len(encoded) > limits.MAX_FILE_BYTES or total > limits.MAX_VERSION_BYTES:
                raise _too_large("The skill exceeds the file or total size limit.")
            entries[f"{name}/{path}"] = encoded
        lock_skills.append({key: skill[key] for key in ("name", "version", "version_id", "digest", "label")})
    lock = {
        "schema": 1, "origin": origin, "skills": lock_skills,
        "skipped": sorted(skipped or [], key=lambda entry: (entry["name"], entry["reason"])),
    }
    entries[_LOCK_PATH] = json.dumps(lock, sort_keys=True, separators=(",", ":")).encode("utf-8")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path, content in sorted(entries.items()):
            info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return output.getvalue()


def _ignored(path: str) -> bool:
    parts = path.split("/")
    return "__MACOSX" in parts or ".git" in parts or parts[-1] == ".DS_Store"


def _reject(rejected: list[dict], path: str, reason: str) -> None:
    rejected.append({"path": path, "reason": reason})


def _decode_file(path: str, raw: bytes, rejected: list[dict]) -> str | None:
    if path.lower().endswith(".zip") or raw.startswith(_ZIP_MAGIC):
        _reject(rejected, path, "nested_zip")
        return None
    if len(raw) > limits.MAX_FILE_BYTES:
        _reject(rejected, path, "file_too_large")
        return None
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        _reject(rejected, path, "file_not_text")
        return None
    if not text_is_valid(content):
        _reject(rejected, path, "file_not_text")
        return None
    return content


def _zip_files(filename: str, raw: bytes, rejected: list[dict], skipped: list[str], budget: dict) -> dict[str, str]:
    if len(raw) > limits.MAX_ZIP_BYTES:
        _reject(rejected, filename, "compressed_too_large")
        return {}
    files: dict[str, str] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            budget["entries"] += len(entries)
            budget["bytes"] += sum(entry.file_size for entry in entries)
            if budget["entries"] > limits.MAX_ZIP_ENTRIES:
                raise _too_large("Import at most 1,000 ZIP entries at a time.")
            if budget["bytes"] > limits.MAX_UNZIPPED_BYTES:
                raise _too_large("Keep the uncompressed import within 20 MiB.")
            counts = Counter(entry.orig_filename.rstrip("/").casefold() for entry in entries)
            for entry in entries:
                # orig_filename preserves NULs, which ZipInfo.filename truncates.
                path = entry.orig_filename
                directory = entry.is_dir()
                checked_path = path[:-1] if directory else path
                if not path_is_valid(checked_path):
                    _reject(rejected, path, "path_invalid")
                    continue
                if _ignored(checked_path):
                    skipped.append(path)
                    continue
                reason = _entry_rejection(entry, checked_path, counts)
                if reason:
                    _reject(rejected, path, reason)
                    continue
                if directory:
                    continue
                try:
                    with archive.open(entry) as source:
                        # The central directory is checked first; never trust read()
                        # without a cap, even when the declared size looks small.
                        data = source.read(entry.file_size + 1)
                    if len(data) != entry.file_size:
                        _reject(rejected, path, "size_mismatch")
                        continue
                    content = _decode_file(path, data, rejected)
                    if content is not None:
                        files[path] = content
                except (zipfile.BadZipFile, NotImplementedError, RuntimeError, EOFError, zlib.error):
                    _reject(rejected, path, "archive_invalid")
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, ValueError):
        _reject(rejected, filename, "archive_invalid")
        return {}
    return files


def _entry_rejection(entry: zipfile.ZipInfo, path: str, counts: Counter) -> str | None:
    mode = stat.S_IFMT(entry.external_attr >> 16)
    if mode == stat.S_IFLNK:
        return "symlink"
    if mode not in (0, stat.S_IFREG, stat.S_IFDIR):
        return "special_file"
    if entry.flag_bits & 1:
        return "encrypted"
    if counts[path.casefold()] > 1:
        return "path_duplicate"
    if entry.file_size > limits.MAX_FILE_BYTES:
        return "file_too_large"
    if entry.file_size > limits.MAX_ZIP_RATIO * entry.compress_size:
        return "compression_ratio"
    if path.lower().endswith(".zip"):
        return "nested_zip"
    return None


def _bundles(files: dict[str, str], source: str, fallback_name: str, rejected: list[dict]) -> list[dict]:
    roots: list[str] = []
    for path in sorted(files, key=lambda name: (name.count("/"), name)):
        if posixpath.basename(path) != "SKILL.md":
            continue
        root = posixpath.dirname(path)
        if any(not parent or root.startswith(parent + "/") for parent in roots):
            _reject(rejected, path, "ignored_nested")
        else:
            roots.append(root)
    bundles = []
    for root in roots:
        prefix = root + "/" if root else ""
        bundle_files = [{"path": path[len(prefix):], "content": content} for path, content in sorted(files.items()) if path.startswith(prefix)]
        checks = run_checks(bundle_files)
        name = (checks["frontmatter"] or {}).get("name")
        if not isinstance(name, str) or len(name) > limits.MAX_NAME or not re.fullmatch(limits.NAME_RE, name):
            name = posixpath.basename(root) or fallback_name
        key = hashlib.sha256(json.dumps([source, root], separators=(",", ":")).encode("utf-8")).hexdigest()
        bundles.append({"key": key, "root": root, "name": name, "files": bundle_files, "checks": checks})
    if not roots and files:
        _reject(rejected, source, "root_missing")
    return bundles


def parse_upload(uploads: list[tuple[str, bytes]]) -> dict:
    """Read ZIPs and loose paths with shared limits and outermost bundle roots.

    Malformed members are reported without opening them. Import-wide count/size
    limits fail the whole request rather than returning a silently partial import.
    """
    rejected: list[dict] = []
    skipped: list[str] = []
    bundles: list[dict] = []
    loose: list[tuple[str, bytes]] = []
    budget = {"entries": 0, "bytes": 0}
    for index, (filename, raw) in enumerate(uploads):
        if not path_is_valid(filename):
            _reject(rejected, filename, "path_invalid")
            continue
        if _ignored(filename):
            skipped.append(filename)
            continue
        if filename.lower().endswith(".zip") or raw.startswith(_ZIP_MAGIC):
            files = _zip_files(filename, raw, rejected, skipped, budget)
            bundles.extend(_bundles(files, f"{index}:{filename}", posixpath.splitext(posixpath.basename(filename))[0], rejected))
        else:
            loose.append((filename, raw))
    if len(loose) > limits.MAX_LOOSE_FILES:
        raise _too_large("Upload at most 200 loose files at a time.")
    budget["bytes"] += sum(len(raw) for _, raw in loose)
    if budget["bytes"] > limits.MAX_UNZIPPED_BYTES:
        raise _too_large("Keep the uncompressed import within 20 MiB.")
    counts = Counter(path.casefold() for path, _ in loose)
    loose_files = {}
    for path, raw in loose:
        if counts[path.casefold()] > 1:
            _reject(rejected, path, "path_duplicate")
            continue
        content = _decode_file(path, raw, rejected)
        if content is not None:
            loose_files[path] = content
    bundles.extend(_bundles(loose_files, "loose", "skill", rejected))
    if len(bundles) > limits.MAX_IMPORT_SKILLS:
        raise _too_large("Import at most 50 skills at a time.")
    return {"bundles": bundles, "rejected": rejected, "skipped": skipped}
