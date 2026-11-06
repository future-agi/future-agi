"""Channel-isolated host discovery; no Docker, mutation, or stale authorization.

Explicit check/apply still use trust.fetch_manifest unchanged. A registered
runner supplies Verifier(host_only=True), with `which` bound to its registered
host cosign binary. Discovery shares fetching, channel filtering, signature
verification and version policy with the existing manual engine.
"""
from __future__ import annotations

import json
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import parse_qs, urlsplit

from .compatibility import MAX_PROOF_BYTES, PROOF_ASSET, CompatibilityError, validate_release_proof
from .contracts import ReasonCode, VerificationSnapshot
from .httpcache import DiscoveryError, HttpCache, digest, require, timestamp
from .manifest import (BUNDLE_ASSET, MANIFEST_ASSET, SUPPORTED_SCHEMA_VERSIONS, ManifestError,
                       is_prerelease, is_version, manifest_hash, parse_version, validate_manifest)
from .plan import policy_allows
from .trust import (VerificationError, Verifier, fetch_manifest, has_release_assets, https_url, release_records)

MAX_PAGES = 5
PAGE_SIZE = 100
MAX_VERIFICATIONS = 20
DISCOVERY_BUDGET = 120.0


@dataclass(frozen=True, repr=False)
class DiscoveredRelease:
    """Private verified evidence. Never serialize this object into app status."""

    raw_manifest: bytes
    raw_bundle: bytes
    manifest_hash: str
    trust_fingerprint: str
    verification: VerificationSnapshot
    release_proof: bytes = b""
    proof_reason_codes: Tuple[ReasonCode, ...] = ()
    repository: str = "future-agi/future-agi"
    source_identity_sha256: str = ""

    @property
    def manifest(self):
        return validate_manifest(json.loads(self.raw_manifest))

    @property
    def version(self):
        return self.manifest["version"]

    @property
    def manifest_sha256(self):
        return digest(self.raw_manifest)

    @property
    def bundle_sha256(self):
        return digest(self.raw_bundle)


@dataclass(frozen=True, repr=False)
class DiscoveryResult:
    candidate: Optional[DiscoveredRelease] = None
    reason_codes: Tuple[ReasonCode, ...] = ()
    checked_at: Optional[datetime] = None
    retry_at: Optional[float] = None

    @property
    def complete(self):
        return not self.reason_codes and self.checked_at is not None

    def public_summary(self):
        """Closed projection, not the complete S01 publisher schema."""
        return {"state": ("available" if self.candidate else "current") if self.complete else "blocked",
                "candidate_version": self.candidate.version if self.candidate else None,
                "checked_at": self.checked_at.isoformat() if self.checked_at else None,
                "reason_codes": [code.value for code in self.reason_codes]}


class ReleaseDiscovery:
    def __init__(self, cache: HttpCache, verifier: Verifier, *, repository="future-agi/future-agi",
                 source_identity="", monotonic=time.monotonic,
                 api_origin="https://api.github.com", asset_origin="https://github.com"):
        require(isinstance(repository, str) and re.fullmatch(
            r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?/[A-Za-z0-9][A-Za-z0-9._-]*", repository) is not None,
            ReasonCode.MANIFEST_UNAVAILABLE)
        self.cache, self.verifier = cache, verifier
        self.repository, self.source_identity, self.monotonic = repository, source_identity, monotonic
        # Explicit host-controlled origin injection supports a local TLS publisher.
        # No origin, CA, key, redirect or asset URL is taken from a release document.
        for origin in (api_origin, asset_origin):
            require(https_url(origin) == origin + "/" and urlsplit(origin).path == "",
                    ReasonCode.MANIFEST_UNAVAILABLE)
        self.api = f"{api_origin}/repos/{repository}/releases"
        self.asset_origin = asset_origin
        self.source_identity_sha256 = digest(json.dumps([api_origin, asset_origin, repository,
                                                        source_identity], separators=(",", ":")).encode())

    def _remaining(self, deadline):
        require(self.monotonic() < deadline, ReasonCode.DISCOVERY_INCOMPLETE)

    def _checked_at(self, deadline):
        self._remaining(deadline)
        now = timestamp(self.cache.clock())
        require(now >= self._started_at, ReasonCode.CLOCK_SKEW)
        return datetime.fromtimestamp(now, timezone.utc)

    def _begin(self):
        self._started_at = timestamp(self.cache.clock())
        deadline = self.monotonic() + DISCOVERY_BUDGET
        # Verifier's subprocess timeout clock is the real monotonic clock. Its
        # deadline shares the budget, even when tests inject a discovery clock.
        self.verifier.begin_discovery(time.monotonic() + DISCOVERY_BUDGET)
        self._remaining(deadline)
        return deadline

    def _get(self, url, channel, deadline, *, representation="application/json", validate_link=None):
        return self.cache.fetch(url, channel=channel, source_identity=self.source_identity,
                                representation=representation, deadline=deadline, validate_link=validate_link)

    def _next(self, link, page):
        try:
            return self._pagination_next(link, page)
        except DiscoveryError:
            raise
        except ValueError:
            raise DiscoveryError(ReasonCode.DISCOVERY_INCOMPLETE) from None

    def _pagination_next(self, link, page):
        """Only canonical same-repository pagination; never follow a supplied URL."""
        if link is None:
            return False
        found = False
        for part in link.split(","):
            match = re.fullmatch(r'\s*<([^<>]+)>;\s*rel="(next|prev|first|last)"\s*', part)
            require(match is not None, ReasonCode.DISCOVERY_INCOMPLETE)
            url, relation = match.groups()
            parsed = urlsplit(https_url(url))
            query = parse_qs(parsed.query, strict_parsing=True)
            require(f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == self.api
                    and set(query) == {"page", "per_page"} and query["per_page"] == [str(PAGE_SIZE)]
                    and len(query["page"]) == 1 and re.fullmatch(r"[1-9][0-9]{0,5}", query["page"][0]) is not None,
                    ReasonCode.DISCOVERY_INCOMPLETE)
            if relation == "next":
                require(not found and int(query["page"][0]) == page + 1, ReasonCode.DISCOVERY_INCOMPLETE)
                found = True
        return found

    def _records(self, channel, deadline):
        records = {}
        for page in range(1, MAX_PAGES + 1):
            response = self._get(f"{self.api}?per_page={PAGE_SIZE}&page={page}", channel, deadline,
                                 representation="application/vnd.github+json", validate_link=lambda link: self._next(link, page))
            try:
                releases = json.loads(response.body)
                require(type(releases) is list and len(releases) <= PAGE_SIZE, ReasonCode.DISCOVERY_INCOMPLETE)
                more = self._next(response.link, page)
                for release in releases:
                    require(type(release) is dict and type(release.get("tag_name")) is str
                            and type(release.get("draft")) is bool and type(release.get("prerelease")) is bool
                            and type(release.get("assets")) is list, ReasonCode.DISCOVERY_INCOMPLETE)
                    tag = release["tag_name"]
                    require(tag not in records or records[tag] == release, ReasonCode.DISCOVERY_INCOMPLETE)
                    records[tag] = release
            except DiscoveryError:
                raise
            except (ValueError, TypeError, RecursionError):
                raise DiscoveryError(ReasonCode.DISCOVERY_INCOMPLETE) from None
            if not more and len(releases) < PAGE_SIZE:
                selected = {record["tag_name"]: record for record in release_records(list(records.values()), channel)}
                known = self.cache.known_versions(self._url, channel=channel, source_identity=self.source_identity)
                for version in known - set(selected):
                    # A previously verified tag deleted/drafted/reclassified in
                    # the API is withdrawn, not permission to choose older bytes.
                    selected[version] = {"tag_name": version, "assets": []}
                return sorted(selected.values(), key=lambda record: parse_version(record["tag_name"]), reverse=True)
        raise DiscoveryError(ReasonCode.DISCOVERY_INCOMPLETE)

    def _url(self, version):
        return f"{self.asset_origin}/{self.repository}/releases/download/{version}/{MANIFEST_ASSET}"

    def _pair(self, version, channel, deadline):
        self._remaining(deadline)
        require(is_version(version) and is_prerelease(version) == (channel == "beta"), ReasonCode.CHANNEL_MISMATCH)
        url = self._url(version)
        failure = None
        raw_manifest = b""

        def download(source, **_kwargs):
            nonlocal failure, raw_manifest
            try:
                response = self._get(source, channel, deadline)
                if source == url:
                    raw_manifest = response.body
                return response.body
            except DiscoveryError as exc:
                failure = exc
                raise

        fingerprint = self.verifier.trust_fingerprint()
        with tempfile.TemporaryDirectory(prefix="verify-", dir=str(self.cache.store.directory)) as tmp:
            try:
                fetched = fetch_manifest(url, Path(tmp), cwd=Path(tmp), download=download)
            except DiscoveryError:
                raise
            except ManifestError:
                try:
                    value = json.loads(raw_manifest)
                    unsupported = (isinstance(value, dict) and value.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS)
                except (ValueError, RecursionError):
                    unsupported = False
                raise DiscoveryError(ReasonCode.UNSUPPORTED_CONTRACT if unsupported
                                     else ReasonCode.MANIFEST_UNAVAILABLE) from None
            if failure is not None:
                raise failure
            require(fetched.bundle_path is not None, ReasonCode.RELEASE_INCOMPLETE)
            require(fetched.data["version"] == version, ReasonCode.IMMUTABLE_RELEASE_CHANGED)
            require(fetched.data["channel"] == channel, ReasonCode.CHANNEL_MISMATCH)
            require(fetched.data["signature"]["bundle"] == BUNDLE_ASSET, ReasonCode.RELEASE_INCOMPLETE)
            verification = self.verifier.verify(fetched)
            self._remaining(deadline)
            require(verification.verified is True, ReasonCode.SIGNATURE_REJECTED)
            require(self.verifier.trust_fingerprint() == fingerprint
                    and self.verifier.trust_fingerprint(fetched) == fingerprint, ReasonCode.SIGNATURE_REJECTED)
            bundle = fetched.bundle_path.read_bytes()
            canonical = manifest_hash(fetched.data)
            self.cache.accept_pair(url, channel=channel, source_identity=self.source_identity,
                                   manifest=fetched.raw, bundle=bundle, manifest_hash=canonical,
                                   trust_fingerprint=fingerprint)
            proof, reasons = b"", ()
            validation = fetched.data.get("compatibility", {}).get("validation")
            if validation is not None:
                try:
                    require(validation["asset"] == PROOF_ASSET, ReasonCode.UNCERTIFIED_RELEASE)
                    proof_url = url.rsplit("/", 1)[0] + "/" + PROOF_ASSET
                    response = self.cache.fetch(proof_url, channel=channel, source_identity=self.source_identity,
                                                deadline=deadline, max_bytes=MAX_PROOF_BYTES)
                    validate_release_proof(response.body, fetched.data, repository=self.repository)
                    self.cache.accept_evidence(url, channel=channel, source_identity=self.source_identity,
                                               manifest=fetched.raw, bundle=bundle, proof=response.body,
                                               proof_sha256=validation["sha256"])
                    proof = response.body
                except (DiscoveryError, CompatibilityError) as exc:
                    reasons = (exc.reason_code,)
                except ManifestError:
                    reasons = (ReasonCode.UNCERTIFIED_RELEASE,)
            else:
                reasons = (ReasonCode.UNCERTIFIED_RELEASE,)
            checked = self._checked_at(deadline)
            return DiscoveredRelease(fetched.raw, bundle, canonical, fingerprint, VerificationSnapshot(
                True, verification.method, verification.signer, verification.issuer, verification.tool, checked,
                verification.public_key_sha256), proof, reasons, self.repository, self.source_identity_sha256)

    @staticmethod
    def _failure(exc):
        if isinstance(exc, DiscoveryError):
            return DiscoveryResult(reason_codes=(exc.reason_code,), retry_at=exc.retry_at)
        if isinstance(exc, VerificationError):
            reason = ReasonCode.DISCOVERY_INCOMPLETE if str(exc) == "discovery_incomplete" else ReasonCode.HOST_VERIFIER_REQUIRED
        else:
            reason = ReasonCode.MANIFEST_UNAVAILABLE
        return DiscoveryResult(reason_codes=(reason,))

    def discover(self, *, current, channel="stable", policy="minor") -> DiscoveryResult:
        """Highest verified upward release within policy/floor, never a downgrade.

        Missing/withdrawn newest pairs may expose a verified older candidate but
        block automatic use. Bad signatures, unsupported schemas, changed bytes,
        unbounded enumeration and outages never silently fall back to success.
        """
        try:
            require(channel in ("stable", "beta"), ReasonCode.CHANNEL_MISMATCH)
            require(is_version(current) and policy in ("patch", "minor", "major"), ReasonCode.VERSION_POLICY_BLOCKED)
            deadline = self._begin()
            records = self._records(channel, deadline)
            incomplete, floor_blocked, verified = False, False, 0
            for record in records:
                version = record["tag_name"]
                if parse_version(version) <= parse_version(current) or not policy_allows(current, version, policy):
                    continue
                if not has_release_assets(record, MANIFEST_ASSET, BUNDLE_ASSET):
                    incomplete = True
                    continue
                require(verified < MAX_VERIFICATIONS, ReasonCode.DISCOVERY_INCOMPLETE)
                verified += 1
                try:
                    candidate = self._pair(version, channel, deadline)
                except DiscoveryError as exc:
                    if exc.reason_code != ReasonCode.RELEASE_INCOMPLETE:
                        raise
                    incomplete = True
                    continue
                if parse_version(current) < parse_version(candidate.manifest["minimum_version"]):
                    floor_blocked = True
                    continue
                return DiscoveryResult(candidate, (ReasonCode.RELEASE_INCOMPLETE,) if incomplete else (),
                                       None if incomplete else candidate.verification.checked_at)
            if incomplete:
                raise DiscoveryError(ReasonCode.RELEASE_INCOMPLETE)
            if floor_blocked:
                raise DiscoveryError(ReasonCode.VERSION_POLICY_BLOCKED)
            return DiscoveryResult(checked_at=self._checked_at(deadline))
        except (ManifestError, VerificationError, OSError, ValueError) as exc:
            return self._failure(exc)

    def evidence(self, candidate: DiscoveredRelease) -> DiscoveredRelease:
        """Fresh automatic evidence; notify/manual may use a pair without proof.

        No predecessor is inferred here. The baseline/evaluator selects the exact
        signed predecessor + execution-layout row under the engine lock.
        """
        result = self.refresh(candidate.version, channel=candidate.manifest["channel"])
        require(result.complete and result.candidate is not None,
                result.reason_codes[0] if result.reason_codes else ReasonCode.RELEASE_INCOMPLETE)
        fresh = result.candidate
        require((fresh.manifest_sha256, fresh.bundle_sha256, fresh.trust_fingerprint,
                 fresh.repository, fresh.source_identity_sha256) ==
                (candidate.manifest_sha256, candidate.bundle_sha256, candidate.trust_fingerprint,
                 candidate.repository, candidate.source_identity_sha256), ReasonCode.IMMUTABLE_RELEASE_CHANGED)
        require(bool(fresh.release_proof) and not fresh.proof_reason_codes,
                fresh.proof_reason_codes[0] if fresh.proof_reason_codes else ReasonCode.UNCERTIFIED_RELEASE)
        return fresh

    def refresh(self, version, *, channel="stable") -> DiscoveryResult:
        """Fresh exact-pair verification for an in-lock apply/fallback boundary.

        No enumeration and no stale fallback. The caller must still compare the
        returned raw hashes/trust fingerprint to its prepared attempt binding.
        """
        try:
            require(channel in ("stable", "beta"), ReasonCode.CHANNEL_MISMATCH)
            candidate = self._pair(version, channel, self._begin())
            return DiscoveryResult(candidate, checked_at=candidate.verification.checked_at)
        except (ManifestError, VerificationError, OSError, ValueError) as exc:
            return self._failure(exc)
