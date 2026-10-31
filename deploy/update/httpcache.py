"""Bounded, private conditional HTTPS cache. Cached HTTP bytes are NOT trusted.

Discovery verifies every exact manifest/bundle pair under the current host
anchor, including a 304. Index pages are metadata only. No offline success is
manufactured here. Callers serialize their tick with the runner lock; immutable
pair evidence additionally uses create-only publication to survive racing ticks.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import stat
import tempfile
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional

from .contracts import ReasonCode
from .manifest import ManifestError, canonical_json, is_version
from .trust import HttpResponse, MAX_DOWNLOAD_BYTES, http_get, https_url

MAX_ENTRY_BYTES = 12 * 1024 * 1024
MAX_CACHE_BYTES = 64 * 1024 * 1024
MAX_CACHE_ENTRIES = 256


class DiscoveryError(ManifestError):
    """Closed diagnostic; never retain an endpoint, body or underlying error."""

    def __init__(self, reason_code: ReasonCode, *, retry_at: Optional[float] = None):
        self.reason_code = reason_code
        self.retry_at = retry_at
        super().__init__(reason_code.value)


def require(condition, reason=ReasonCode.CACHE_INVALID):
    if not condition:
        raise DiscoveryError(reason)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def timestamp(value):
    require(type(value) in (float, int) and math.isfinite(value) and value >= 0, ReasonCode.CLOCK_SKEW)
    return float(value)


def validator(value):
    require(value is None or (isinstance(value, str) and 0 < len(value.encode("utf-8")) <= 1024
                             and all(32 <= ord(c) < 127 for c in value)))
    return value


def retry_time(value, now):
    """Retry-After seconds or a timezone-qualified HTTP date, capped at one day."""
    if value is None:
        return now + 60
    validator(value)
    if re.fullmatch(r"[0-9]{1,10}", value):
        return now + min(int(value), 86400)
    try:
        date = parsedate_to_datetime(value)
        require(date.tzinfo is not None)
        return now + min(max(date.timestamp() - now, 0), 86400)
    except (ValueError, TypeError, OverflowError):
        raise DiscoveryError(ReasonCode.CACHE_INVALID) from None


def _body(value):
    require(isinstance(value, str) and len(value) <= (MAX_DOWNLOAD_BYTES + 2) // 3 * 4)
    raw = base64.b64decode(value, validate=True)
    require(len(raw) <= MAX_DOWNLOAD_BYTES)
    return raw


class PrivateCache:
    """No-follow, owner-only bounded records; atomic fsync + rename/link writes.

    The parent must already exist (normally the registered private state dir).
    We never chmod an existing directory, follow planted links, evict accepted
    release evidence, or silently replace corrupt immutable records.
    """

    def __init__(self, directory: Path):
        self.directory = Path(directory).absolute()
        try:
            # Check existing ancestry BEFORE creating anything through it.
            for path in self.directory.parents:
                require(stat.S_ISDIR(path.lstat().st_mode))
            self.directory.mkdir(mode=0o700, exist_ok=True)
            self._check_directory()
        except OSError:
            raise DiscoveryError(ReasonCode.CACHE_INVALID) from None

    def _check_directory(self):
        for path in (self.directory, *self.directory.parents):
            info = path.lstat()
            require(stat.S_ISDIR(info.st_mode))
        info = self.directory.stat()
        require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)

    def _path(self, name):
        require(re.fullmatch(r"(?:http|pair|proof|rate)-[a-f0-9]{64}", name) is not None)
        self._check_directory()
        return self.directory / (name + ".json")

    def read(self, name, *, strict=False):
        try:
            path = self._path(name)
            fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as handle:
                info = os.fstat(handle.fileno())
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid()
                        and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= MAX_ENTRY_BYTES)
                raw = handle.read(MAX_ENTRY_BYTES + 1)
                require(len(raw) <= MAX_ENTRY_BYTES)
                value = json.loads(raw)
                require(type(value) is dict and type(value.get("schema")) is int and value["schema"] == 1)
                return value
        except FileNotFoundError:
            return None
        except (OSError, ValueError, RecursionError):
            if strict:
                raise DiscoveryError(ReasonCode.CACHE_INVALID) from None
            return None

    def write(self, name, value, *, exclusive=False):
        temporary = None
        try:
            path = self._path(name)
            raw = canonical_json(dict(value, schema=1))
            entries = list(self.directory.iterdir())
            require(len(raw) <= MAX_ENTRY_BYTES and len(entries) < MAX_CACHE_ENTRIES
                    and sum(p.lstat().st_size for p in entries) + len(raw) <= MAX_CACHE_BYTES)
            fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=str(self.directory))
            with os.fdopen(fd, "wb") as handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            if exclusive:
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    return False
            else:
                os.replace(temporary, path)
                temporary = None
            if temporary is not None:
                os.unlink(temporary)
                temporary = None
            fd = os.open(str(self.directory), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            return True
        except OSError:
            raise DiscoveryError(ReasonCode.CACHE_INVALID) from None
        finally:
            if temporary is not None:
                os.unlink(temporary)


@dataclass(frozen=True, repr=False)
class CacheResponse:
    body: bytes
    checked_at: float
    not_modified: bool = False
    link: Optional[str] = None


class HttpCache:
    def __init__(self, directory: Path, *, transport=http_get, clock=time.time, monotonic=time.monotonic):
        self.store = PrivateCache(directory)
        self.transport, self.clock, self.monotonic = transport, clock, monotonic

    def key(self, url, *, channel, source_identity="", representation="application/json"):
        require(channel in ("stable", "beta"), ReasonCode.CHANNEL_MISMATCH)
        try:
            endpoint = https_url(url)
        except ManifestError:
            raise DiscoveryError(ReasonCode.MANIFEST_UNAVAILABLE) from None
        # Credentials/queries/identity are NEVER persisted, including signed CDN URLs.
        return digest(canonical_json([endpoint, channel, digest(source_identity.encode()), representation]))

    def _entry(self, key, now):
        value = self.store.read("http-" + key)
        if value is None:
            return None
        try:
            raw = _body(value["body"])
            require(digest(raw) == value["sha256"] and value["key"] == key)
            validator(value["etag"])
            validator(value["last_modified"])
            require(value["link"] is None or (type(value["link"]) is str and len(value["link"]) <= 8192))
            checked = timestamp(value["checked_at"])
        except (ValueError, KeyError, TypeError, UnicodeError):
            return None
        require(checked <= now, ReasonCode.CLOCK_SKEW)
        return value, raw

    def fetch(self, url, *, channel="stable", source_identity="", representation="application/json", deadline=None,
              validate_link=None, max_bytes=MAX_DOWNLOAD_BYTES):
        require(type(max_bytes) is int and 0 < max_bytes <= MAX_DOWNLOAD_BYTES)
        validator(representation)
        key = self.key(url, channel=channel, source_identity=source_identity, representation=representation)
        now = timestamp(self.clock())
        rate = self.store.read("rate-" + key, strict=True)
        if rate is not None:
            try:
                observed, retry = timestamp(rate["observed_at"]), timestamp(rate["retry_at"])
                require(observed <= now and observed <= retry <= observed + 86400, ReasonCode.CLOCK_SKEW)
            except (KeyError, TypeError):
                raise DiscoveryError(ReasonCode.CACHE_INVALID) from None
            if retry > now:
                raise DiscoveryError(ReasonCode.RATE_LIMITED, retry_at=retry)
        entry = self._entry(key, now)
        headers = {"Accept": representation}
        if entry is not None:
            value, _ = entry
            if value["etag"] is not None:
                headers["If-None-Match"] = value["etag"]
            if value["last_modified"] is not None:
                headers["If-Modified-Since"] = value["last_modified"]
        # At most ONE unconditional recovery GET after a cacheless/bad 304.
        for attempt in range(2):
            timeout = 30.0 if deadline is None else min(30.0, deadline - self.monotonic())
            require(timeout > 0, ReasonCode.DISCOVERY_INCOMPLETE)
            try:
                response = (self.transport(url, headers, timeout) if max_bytes == MAX_DOWNLOAD_BYTES else
                            self.transport(url, headers, timeout, max_bytes=max_bytes))
                require(isinstance(response, HttpResponse))
                https_url(response.final_url)
                require(type(response.body) is bytes and len(response.body) <= max_bytes)
                validator(response.etag)
                validator(response.last_modified)
                require(response.link is None or (type(response.link) is str and len(response.link) <= 8192
                                                  and all(32 <= ord(c) < 127 for c in response.link)))
                if response.link is not None:
                    # The discovery adapter validates same-repo/page-only links
                    # before any header can enter the cache (no auth/query leaks).
                    require(validate_link is not None)
                    validate_link(response.link)
            except DiscoveryError:
                raise
            except (ManifestError, OSError, ValueError):
                raise DiscoveryError(ReasonCode.NETWORK_UNAVAILABLE) from None
            checked = timestamp(self.clock())
            require(checked >= now, ReasonCode.CLOCK_SKEW)
            require(deadline is None or self.monotonic() < deadline, ReasonCode.DISCOVERY_INCOMPLETE)
            if response.status == 429 or (response.status == 403 and response.retry_after is not None):
                retry = retry_time(response.retry_after, checked)
                self.store.write("rate-" + key, {"observed_at": checked, "retry_at": retry})
                raise DiscoveryError(ReasonCode.RATE_LIMITED, retry_at=retry)
            if response.status == 404 or response.status == 410:
                raise DiscoveryError(ReasonCode.RELEASE_INCOMPLETE)
            if response.status == 304:
                matched = entry is not None and len(headers) > 1 and not response.body
                if matched:
                    value, raw = entry
                    matched = (response.etag in (None, value["etag"])
                               and response.last_modified in (None, value["last_modified"]))
                if matched:
                    require(len(raw) <= max_bytes)
                    value["checked_at"] = checked
                    self.store.write("http-" + key, value)
                    return CacheResponse(raw, checked, True, value["link"])
                require(attempt == 0)
                headers = {"Accept": representation}
                entry = None
                continue
            require(response.status == 200, ReasonCode.NETWORK_UNAVAILABLE)
            value = {"key": key, "body": base64.b64encode(response.body).decode("ascii"),
                     "sha256": digest(response.body), "etag": response.etag, "last_modified": response.last_modified,
                     "checked_at": checked, "link": response.link}
            self.store.write("http-" + key, value)
            return CacheResponse(response.body, checked, False, response.link)

    def known_versions(self, url_for_version, *, channel, source_identity):
        """Previously accepted identities, for withdrawal detection ONLY.

        Not signature evidence or an offline candidate. Even under changed trust,
        disappearing higher releases must not silently select an older target.
        """
        paths = list(self.store.directory.glob("pair-*.json"))
        require(len(paths) <= MAX_CACHE_ENTRIES and sum(p.lstat().st_size for p in paths) <= MAX_CACHE_BYTES)
        versions = set()
        try:
            for path in paths:
                value = self.store.read(path.stem, strict=True)
                require(value is not None)
                raw = _body(value["manifest"])
                document = json.loads(raw)
                require(type(document) is dict and is_version(document.get("version")))
                version = document["version"]
                expected = "pair-" + self.key(url_for_version(version), channel=channel,
                                              source_identity=source_identity, representation="signed-pair")
                if path.stem != expected:
                    continue
                require(digest(raw) == value["manifest_sha256"]
                        and digest(_body(value["bundle"])) == value["bundle_sha256"])
                versions.add(version)
            return versions
        except DiscoveryError:
            raise
        except (ValueError, KeyError, TypeError, RecursionError):
            raise DiscoveryError(ReasonCode.CACHE_INVALID) from None

    def accept_evidence(self, url, *, channel, source_identity, manifest, bundle, proof, proof_sha256):
        """Create-only exact-pair proof identity AFTER signed proof validation.

        Neither a 304 nor a new trust root permits replacing accepted bytes.
        The caller revalidates repository/tag/source/predecessor/input bindings.
        """
        require(type(proof) is bytes and len(proof) <= 256 * 1024 and digest(proof) == proof_sha256)
        pair = self.key(url, channel=channel, source_identity=source_identity, representation="signed-pair")
        identity = {"pair": pair, "manifest": digest(manifest), "bundle": digest(bundle), "sha256": proof_sha256}
        name = "proof-" + digest(canonical_json(identity))
        value = dict(identity, body=base64.b64encode(proof).decode("ascii"))
        if self.store.read(name, strict=True) is None:
            self.store.write(name, value, exclusive=True)
        saved = self.store.read(name, strict=True)
        require(saved == dict(value, schema=1), ReasonCode.IMMUTABLE_RELEASE_CHANGED)
        return name

    def accept_pair(self, url, *, channel, source_identity, manifest, bundle, manifest_hash, trust_fingerprint):
        """Called ONLY after current-trust verification and exact tag/channel checks.

        The accepted bytes are permanent evidence, independent of HTTP validators
        and trust rotation. A changed signer cannot authorize rewriting a tag.
        """
        key = self.key(url, channel=channel, source_identity=source_identity, representation="signed-pair")
        name = "pair-" + key
        hashes = {"manifest_sha256": digest(manifest), "bundle_sha256": digest(bundle), "manifest_hash": manifest_hash}
        value = dict(hashes, manifest=base64.b64encode(manifest).decode("ascii"),
                     bundle=base64.b64encode(bundle).decode("ascii"), trust_fingerprint=trust_fingerprint)
        if self.store.read(name, strict=True) is None:
            self.store.write(name, value, exclusive=True)
        saved = self.store.read(name, strict=True)
        try:
            require(saved is not None)
            require(digest(_body(saved["manifest"])) == saved["manifest_sha256"]
                    and digest(_body(saved["bundle"])) == saved["bundle_sha256"])
            require(all(saved.get(k) == v for k, v in hashes.items()), ReasonCode.IMMUTABLE_RELEASE_CHANGED)
        except (ValueError, KeyError, TypeError) as exc:
            if isinstance(exc, DiscoveryError):
                raise
            raise DiscoveryError(ReasonCode.CACHE_INVALID) from None
