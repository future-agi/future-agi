"""Read, split, compose and store the archives behind environment and suite versions.

An authoring tree splits into three parts:

* environment: contract, world, sub-goals and everything else the producer reads;
* suite: the scenarios and their index files;
* sealed: the bundle a run replays and its certification, kept with the pair until runs
  assemble their own bundle.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import tarfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath

from minio.error import S3Error

from simulate.services.hosted_harness import HostedHarnessError
from tfc.settings.settings import UPLOAD_BUCKET_NAME
from tfc.utils.storage_client import ensure_bucket, get_storage_client

Files = Mapping[str, bytes]

# A 5,000-scenario suite holds about 110,000 files and 250 MB, so both caps leave headroom.
MAX_ARCHIVE_FILES = 200_000
# Directories and other non-file entries, which carry no bytes but still cost a read.
MAX_ARCHIVE_ENTRIES = 1_000_000
# Archives are read whole into memory and a save holds a few copies, so stay well under 1 GiB.
MAX_ARCHIVE_INFLATED_BYTES = 512 * 1024 * 1024

SUITE_ROOTS = ("scenarios/", "scenario/")
SUITE_FILES = frozenset({"scenarios.json", "coverage.json", "proofs.json"})

SEALED_ROOTS = ("environment-bundle/", "generic-harness/certified-bundle/")
SEALED_FILES = frozenset(
    {
        "runtime-validation.json",
        "generic-harness/runtime-evidence.json",
        "generic-harness/certification.json",
        "generic-harness/tool-certification.json",
        "generic-harness/action-certification.json",
        "generic-harness/repair-history.json",
        "generic-harness/setup-checks-passed.json",
    }
)
SEALED_PREFIXES = (
    "generic-harness/repair-patch-",
    "generic-harness/scenario-repair-",
    "generic-harness/runtime-plan-repair-",
)

# Never stored in a version: per-run spend, build and error logs, the scenario writers'
# journal, the provider agent's fetched definition (it belongs to the agent),
# interpreter caches and the hidden session files at the root of a chat workspace, some
# of which hold credentials.
DROPPED_FILES = frozenset(
    {
        "cost.json",
        "harness-events.jsonl",
        "written.jsonl",
        "provider-import-profile.json",
        "generic-harness/source-data-review-early-error.txt",
    }
)


@dataclass(frozen=True)
class SplitArchive:
    environment: dict[str, bytes]
    suite: dict[str, bytes]
    sealed: dict[str, bytes]


def _too_large() -> HostedHarnessError:
    return HostedHarnessError(
        "archive_too_large",
        "the authoring archive is larger than a version may be",
        status_code=413,
    )


def _invalid(path: str) -> HostedHarnessError:
    return HostedHarnessError(
        "archive_member_invalid",
        f"the authoring archive holds an unsafe member: {path}",
        status_code=422,
    )


def _member_path(name: str) -> str:
    """The member's relative path, refusing absolute paths, parent references and names
    that are not UTF-8.

    ``tar -czf … .`` writes a ``./`` entry for the root, which reads as an empty path.
    """
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        raise _invalid(name.encode("utf-8", "backslashreplace").decode()) from None
    raw = name.replace("\\", "/")
    if raw.startswith("/"):
        raise _invalid(raw)
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if ".." in parts:
        raise _invalid(raw)
    return "/".join(parts)


def read_archive(body: bytes) -> dict[str, bytes]:
    """Every regular file in a tar.gz, after the workspace member rules and size caps."""
    files: dict[str, bytes] = {}
    inflated = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
            for path, member in _regular_members(archive):
                if not path or path in files:
                    raise _invalid(member.name)
                if len(files) >= MAX_ARCHIVE_FILES:
                    raise _too_large()
                inflated += member.size
                if inflated > MAX_ARCHIVE_INFLATED_BYTES:
                    raise _too_large()
                source = archive.extractfile(member)
                files[path] = source.read() if source is not None else b""
    except tarfile.TarError as exc:
        raise HostedHarnessError(
            "archive_invalid",
            "the authoring archive could not be read",
            status_code=422,
        ) from exc
    return files


def archive_stats(body: bytes) -> dict[str, int | bool]:
    """Entry, file and inflated-byte counts of a tar.gz against the caps; reads headers only."""
    entries = files = inflated = 0
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
        for member in archive:
            entries += 1
            if member.isfile():
                files += 1
                inflated += member.size
    return {
        "entries": entries,
        "files": files,
        "inflated_bytes": inflated,
        "within_caps": entries <= MAX_ARCHIVE_ENTRIES
        and files <= MAX_ARCHIVE_FILES
        and inflated <= MAX_ARCHIVE_INFLATED_BYTES,
    }


def pack_archive(files: Files) -> bytes:
    """A deterministic tar.gz: sorted members, fixed modes, owners and timestamps."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for path in sorted(files, key=PurePosixPath):
            data = files[path]
            info = tarfile.TarInfo(path)
            info.size = len(data)
            info.mode = 0o644
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            archive.addfile(info, io.BytesIO(data))
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode="wb", mtime=0, filename="") as zipped:
        zipped.write(raw.getvalue())
    return out.getvalue()


def _is_dropped(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return path in DROPPED_FILES or parts[0].startswith(".") or "__pycache__" in parts


def _is_suite(path: str) -> bool:
    return path in SUITE_FILES or path.startswith(SUITE_ROOTS)


def _is_sealed(path: str) -> bool:
    return (
        path in SEALED_FILES
        or path.startswith(SEALED_ROOTS)
        or path.startswith(SEALED_PREFIXES)
    )


def split_authoring(files: Files) -> SplitArchive:
    environment: dict[str, bytes] = {}
    suite: dict[str, bytes] = {}
    sealed: dict[str, bytes] = {}
    for path, data in files.items():
        if _is_dropped(path):
            continue
        if _is_suite(path):
            suite[path] = data
        elif _is_sealed(path):
            sealed[path] = data
        else:
            environment[path] = data
    return SplitArchive(environment=environment, suite=suite, sealed=sealed)


def compose(
    environment: Files,
    suite: Files,
    sealed: Files | None = None,
) -> dict[str, bytes]:
    """One authoring tree from an environment and a suite.

    Each side contributes only its own files, so a combined (pre-versioning) archive on
    either side never leaks the other side's scenarios or contract into the result.
    """
    tree = {
        path: data
        for path, data in environment.items()
        if not _is_suite(path) and not _is_sealed(path)
    }
    tree.update({path: data for path, data in suite.items() if _is_suite(path)})
    if sealed:
        tree.update({path: data for path, data in sealed.items() if _is_sealed(path)})
    return tree


def sha256_hex(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def environment_key(organization_id: object, job_id: object, body: bytes) -> str:
    return f"harness-environments/{organization_id}/{job_id}/{sha256_hex(body)}.tar.gz"


def suite_key(organization_id: object, job_id: object, body: bytes) -> str:
    return f"harness-suites/{organization_id}/{job_id}/{sha256_hex(body)}.tar.gz"


def sealed_key(organization_id: object, job_id: object, body: bytes) -> str:
    # Content-addressed copy of a build's sealed bundle; READY cache rows point at it.
    return f"harness-sealed/{organization_id}/{job_id}/{sha256_hex(body)}.tar.gz"


def pair_key(organization_id: object, job_id: object, body: bytes) -> str:
    return f"harness-authoring/{organization_id}/{job_id}/{sha256_hex(body)}.tar.gz"


def put_archive(key: str, body: bytes) -> None:
    """Store an archive under its content-addressed key; identical bytes rewrite nothing new."""
    client = get_storage_client()
    ensure_bucket(client, UPLOAD_BUCKET_NAME)
    client.put_object(
        bucket_name=UPLOAD_BUCKET_NAME,
        object_name=key,
        data=io.BytesIO(body),
        length=len(body),
        content_type="application/gzip",
    )


def archive_exists(key: str) -> bool:
    return archive_size(key) is not None


def archive_size(key: str) -> int | None:
    """A stored archive's compressed size; ``None`` when it is gone."""
    try:
        stat = get_storage_client().stat_object(UPLOAD_BUCKET_NAME, key)
    except S3Error as exc:
        if exc.code in {"NoSuchKey", "NoSuchBucket"}:
            return None
        raise
    return int(getattr(stat, "size", 0) or 0)


def _regular_members(archive: tarfile.TarFile):
    for count, member in enumerate(archive, start=1):
        if count > MAX_ARCHIVE_ENTRIES:
            raise _too_large()
        if member.issym() or member.islnk() or member.isdev():
            raise _invalid(member.name)
        path = _member_path(member.name)
        if member.isfile():
            yield path, member


def member_digests(body: bytes, chunk: int = 64 * 1024) -> dict[str, tuple[str, int]]:
    """Each regular member's sha256 and size, read in chunks."""
    digests: dict[str, tuple[str, int]] = {}
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
        for path, member in _regular_members(archive):
            if not path:
                continue
            digest = hashlib.sha256()
            source = archive.extractfile(member)
            while source is not None and (block := source.read(chunk)):
                digest.update(block)
            digests[path] = (digest.hexdigest(), member.size)
    return digests


def read_members(body: bytes, paths: set[str], max_size: int) -> dict[str, bytes]:
    """The named regular members of at most ``max_size`` bytes."""
    found: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
        for path, member in _regular_members(archive):
            if path in paths and member.size <= max_size:
                source = archive.extractfile(member)
                found[path] = source.read() if source is not None else b""
    return found


def get_archive(key: str) -> bytes:
    response = get_storage_client().get_object(UPLOAD_BUCKET_NAME, key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()
