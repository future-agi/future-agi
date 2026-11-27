"""The ready environments every organization may start from, registered from the pinned list.

`simulate/ready_environments.json` is the list an agent-learning-kit release publishes, copied
here by that release's pull request: each environment's archive with its SHA-256 and both release
digests, and the URL the archives are attached under. `seed` registers each listed environment
that is not registered yet as a system release. One already registered is skipped before any
download, so a deploy whose list did not change makes no request.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import tarfile
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import httpx
import structlog

from simulate.models import HarnessEnvironmentRelease, HarnessSuiteRelease
from simulate.services.harness_releases import register_release
from simulate.services.hosted_harness import HostedHarnessError

logger = structlog.get_logger(__name__)

PINNED = Path(__file__).resolve().parent.parent / "ready_environments.json"
SCHEMA = "futureagi.ready-environments.v1"
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_UNPACKED_BYTES = 512 * 1024 * 1024
MAX_FILES = 20_000
DOWNLOAD_TIMEOUT_SECONDS = 120
PARTS = frozenset({"environment", "suite"})
_NAME = re.compile(r"[a-z0-9_]+")
_ARCHIVE = re.compile(r"ready-environment-[a-z0-9_]+\.tar\.gz")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


class ReadyEnvironmentError(Exception):
    """The pinned list, or an archive it names, is not what a ready environment must be."""


@dataclass
class Seeded:
    registered: list[str] = field(default_factory=list)
    present: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)


def pinned(path: Path = PINNED) -> dict[str, Any]:
    """The pinned list, checked: every field a download and registration rely on."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReadyEnvironmentError(f"{path.name} is not readable JSON: {exc}") from exc
    if not isinstance(document, dict) or document.get("schema_version") != SCHEMA:
        raise ReadyEnvironmentError(f"{path.name} is not a {SCHEMA} list")
    environments = document.get("environments")
    if not isinstance(environments, list):
        raise ReadyEnvironmentError(f"{path.name} lists no environments")
    url = document.get("download_url")
    if environments and not (
        isinstance(url, str) and url.startswith("https://") and url.endswith("/")
    ):
        raise ReadyEnvironmentError(
            f"{path.name}: download_url must be an https:// URL ending in /"
        )
    names = set()
    for entry in environments:
        if not (
            isinstance(entry, dict)
            and isinstance(entry.get("name"), str)
            and _NAME.fullmatch(entry["name"])
            and isinstance(entry.get("archive"), str)
            and _ARCHIVE.fullmatch(entry["archive"])
            and isinstance(entry.get("sha256"), str)
            and _SHA256.fullmatch(entry["sha256"])
            and isinstance(entry.get("size"), int)
            and 0 < entry["size"] <= MAX_ARCHIVE_BYTES
            and isinstance(entry.get("environment"), str)
            and _DIGEST.fullmatch(entry["environment"])
            and isinstance(entry.get("suite"), str)
            and _DIGEST.fullmatch(entry["suite"])
        ):
            raise ReadyEnvironmentError(f"{path.name} has an invalid entry: {entry!r}")
        if entry["name"] in names:
            raise ReadyEnvironmentError(f"{path.name} lists {entry['name']} twice")
        names.add(entry["name"])
    return document


def registered(entry: dict[str, Any]) -> bool:
    """Both releases are registered as system releases. A deleted one counts (the base manager
    sees it), so an environment an operator took off the shelf stays off it."""
    return (
        HarnessEnvironmentRelease._base_manager.filter(
            organization=None, digest=entry["environment"]
        ).exists()
        and HarnessSuiteRelease._base_manager.filter(
            organization=None, digest=entry["suite"]
        ).exists()
    )


def download(url: str, *, sha256: str, size: int) -> bytes:
    """The archive at `url`, refused unless it is exactly `size` bytes with this SHA-256."""
    body = bytearray()
    with (
        httpx.Client(follow_redirects=True, timeout=DOWNLOAD_TIMEOUT_SECONDS) as client,
        client.stream("GET", url) as response,
    ):
        response.raise_for_status()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body) > size:
                raise ReadyEnvironmentError(f"{url} is larger than {size} bytes")
    if len(body) != size or hashlib.sha256(body).hexdigest() != sha256:
        raise ReadyEnvironmentError(f"{url} is not the archive the list names")
    return bytes(body)


def unpack(archive: bytes, destination: Path) -> None:
    """Write the archive's `environment/` and `suite/` files under `destination`. Only regular
    files at relative paths inside those two folders are accepted, within the size limits."""
    total = count = 0
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as source:
        for member in source:
            path = PurePosixPath(member.name)
            if member.isdir():
                continue
            if not member.isfile():
                raise ReadyEnvironmentError(f"{member.name} is not a regular file")
            if (
                path.is_absolute()
                or ".." in path.parts
                or len(path.parts) < 2
                or path.parts[0] not in PARTS
            ):
                raise ReadyEnvironmentError(f"{member.name} is outside the release")
            count += 1
            total += member.size
            if count > MAX_FILES or total > MAX_UNPACKED_BYTES:
                raise ReadyEnvironmentError("the archive is larger than a release")
            body = source.extractfile(member)
            if body is None:
                raise ReadyEnvironmentError(f"{member.name} is not a regular file")
            target = destination.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body.read())


def _register(entry: dict[str, Any], body: bytes) -> None:
    with tempfile.TemporaryDirectory(prefix="ready-environment-") as scratch:
        root = Path(scratch)
        unpack(body, root)
        environment, suite = register_release(
            root / "environment", root / "suite", organization=None
        )
    if (environment.digest, suite.digest) != (entry["environment"], entry["suite"]):
        raise ReadyEnvironmentError(
            "the archive holds other releases than the list names"
        )


def seed(
    path: Path = PINNED,
    *,
    fetch: Callable[..., bytes] | None = None,
) -> Seeded:
    """Register every pinned ready environment not registered yet. One that fails is reported
    and the rest still register."""
    fetch = fetch or download
    document = pinned(path)
    seeded = Seeded()
    for entry in document["environments"]:
        name = entry["name"]
        if registered(entry):
            seeded.present.append(name)
            continue
        try:
            body = fetch(
                document["download_url"] + entry["archive"],
                sha256=entry["sha256"],
                size=entry["size"],
            )
            _register(entry, body)
        except Exception as exc:  # noqa: BLE001 - one environment's failure never stops the rest
            reason = (
                f"{exc.code}: {exc.message}"
                if isinstance(exc, HostedHarnessError)
                else str(exc) or type(exc).__name__
            )
            logger.exception(
                "ready_environment_not_registered", name=name, error=reason
            )
            seeded.failed[name] = reason
            continue
        seeded.registered.append(name)
    return seeded
