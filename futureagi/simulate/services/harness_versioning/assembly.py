"""Assemble a pinned run's authoring tree and cache the sealed bundle it replays.

A run pinned to agent A, environment E and suite S looks its bundle up by
``assembly_key(A, E, S)``. A ready row is a hit: the run replays the cached bundle. Any
other row is a miss: a run of the environment's defaults runs them as before; one pinned
to other versions certifies E and S itself, when that is enabled, and fills the row for
the next run.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

import structlog
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from minio.error import S3Error
from urllib3.exceptions import HTTPError

from simulate.models import (
    HarnessAgentVersionRuntime,
    HarnessAssembledBundle,
    HarnessEnvironmentVersion,
    HarnessSuiteMember,
    HarnessSuiteVersion,
    HostedHarnessJob,
)
from simulate.services.canonical_json import canonical_digest
from simulate.services.harness_versioning import archives, hashing
from simulate.services.hosted_harness import HostedHarnessError

logger = structlog.get_logger(__name__)

CERTIFICATE = "runtime-validation.json"
SEALED_MANIFEST = "environment-bundle/manifest.json"
BUNDLE_ROOT = "environment-bundle/"
CERTIFY_JOB_PATH = "/work/certify-job.json"
CERTIFY_REPORT_PATH = "/work/authoring/certify-report.json"
# The suite index as assembled; certifying prunes /work/authoring/scenarios.json in place.
SUITE_INDEX_PATH = "/work/suite-scenarios.json"
# Run-only fields the certify job must not carry: it certifies the whole suite once.
_CERTIFY_DROPPED = (
    "execution_manifest",
    "selected_scenario_keys",
    "trials",
    "simulation_only",
)
_MISSING_CODES = frozenset({"NoSuchKey", "NoSuchBucket"})
_SOURCE_DIGEST = re.compile(r"[0-9a-f]{64}")
_BUNDLE_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_FILL_ARCHIVE = "/tmp/harness-bundle-fill.tar.gz"
_FILL_LIST = "/tmp/harness-bundle-fill.list"
# A fill runs inside a poll, which has five minutes in all.
_FILL_EXEC_TIMEOUT_SECONDS = 60
_FILL_DOWNLOAD_TIMEOUT_SECONDS = 60
_CERTIFY_REPORT_TIMEOUT_SECONDS = 15
_RESET = {
    "state": HarnessAssembledBundle.State.PENDING,
    "object_key": None,
    "sha256": None,
    "size": None,
    "bundle_digest": None,
    "certificate": None,
    "built_by_attempt": None,
    "error": None,
}


def certify_enabled() -> bool:
    return bool(getattr(settings, "HARNESS_BUNDLE_CERTIFY_ENABLED", False))


def legacy_runtime(runtime: Any) -> bool:
    """A side row without a credential set of its own: backfilled or pinned at build.

    Only a legacy row has no blob, so the blob is read for a discarded row alone; a
    list that never loads it annotates ``blobless`` instead.
    """
    if runtime.preflight_status == "legacy":
        return True
    if runtime.preflight_status != "discarded":
        return False
    blobless = getattr(runtime, "blobless", None)
    return blobless if blobless is not None else not runtime.credential_blob


def runtime_identity(runtime: Any) -> hashing.AgentIdentity:
    """The identity a side row stands for, rebuilt from its stored facts."""
    agent_payload = runtime.agent_payload or {}
    if not legacy_runtime(runtime):
        return _credential_set_identity(runtime)
    refs = agent_payload.get("legacy_secret_refs") or {}
    return hashing.AgentIdentity(
        source=f"{runtime.source_kind}:"
        + json.dumps(runtime.source or {}, sort_keys=True, separators=(",", ":")),
        connector=str(agent_payload.get("connector") or ""),
        mode=str(agent_payload.get("mode") or ""),
        call_direction=str(agent_payload.get("call_direction") or ""),
        target_speaks_first=agent_payload.get("target_speaks_first"),
        config=agent_payload.get("config") or {},
        prompt_hash=runtime.prompt_hash,
        credential_fingerprints={
            name: canonical_digest(ref) for name, ref in sorted(refs.items())
        },
        provider_alias_names=tuple(
            sorted(set(refs) & hashing.PROVIDER_CREDENTIAL_ALIASES)
        ),
    )


def _credential_set_identity(runtime: Any) -> hashing.AgentIdentity:
    """A side row with its own credential set: keyed fingerprints, provider aliases
    included; the provider keys count through their own fingerprint.

    The version hash takes the fingerprints, so a value change is a new version; the
    bundle key takes their names only, so it reuses the bundle, and matches the legacy
    row the version was made from while names, source, prompt and settings match.
    """
    agent_payload = runtime.agent_payload or {}
    fingerprints = {
        str(name): str(value)
        for name, value in sorted((runtime.credential_fingerprints or {}).items())
    }
    provider = {
        name: value
        for name, value in fingerprints.items()
        if name in hashing.PROVIDER_CREDENTIAL_ALIASES
    }
    return hashing.AgentIdentity(
        source=f"{runtime.source_kind}:"
        + json.dumps(runtime.source or {}, sort_keys=True, separators=(",", ":")),
        connector=str(agent_payload.get("connector") or ""),
        mode=str(agent_payload.get("mode") or ""),
        call_direction=str(agent_payload.get("call_direction") or ""),
        target_speaks_first=agent_payload.get("target_speaks_first"),
        config=agent_payload.get("config") or {},
        prompt_hash=runtime.prompt_hash,
        credential_fingerprints=fingerprints,
        provider_credentials_fingerprint=(
            canonical_digest(provider) if provider else None
        ),
        provider_alias_names=tuple(sorted(provider)),
    )


def agent_bundle_hash(runtime: Any) -> str:
    """The agent's part of its bundles' cache keys."""
    return hashing.agent_cache_hash(runtime_identity(runtime))


def same_bundle_agent(agent_version_id: Any, other_id: Any, environment: Any) -> bool:
    """Whether two agent versions run the same bundles in ``environment``: a
    value-only change does. A shared legacy row stands for the environment's facts."""
    from simulate.services.harness_versioning.backfill import effective_runtime

    if not agent_version_id or not other_id:
        return False
    if str(agent_version_id) == str(other_id):
        return True
    runtimes = [
        effective_runtime(
            environment,
            HarnessAgentVersionRuntime.no_workspace_objects.filter(
                agent_version_id=version_id
            ).first(),
        )
        for version_id in (agent_version_id, other_id)
    ]
    if None in runtimes:
        return False
    return agent_bundle_hash(runtimes[0]) == agent_bundle_hash(runtimes[1])


def bundle_key(runtime: Any, environment_version: Any, suite_version: Any) -> str:
    """The cache key of the bundle agent ``runtime`` runs against this pair."""
    return hashing.assembly_key(
        agent_bundle_hash(runtime),
        environment_version.hash,
        suite_version.certify_hash,
    )


def _json_object(body: bytes | None) -> dict[str, Any] | None:
    try:
        value = json.loads(body) if body else None
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def manifest_facts(sealed: Mapping[str, bytes]) -> tuple[str | None, str | None]:
    """The sealed bundle's digest and the source fingerprint it was built from."""
    manifest = _json_object(sealed.get(SEALED_MANIFEST)) or {}
    digest = manifest.get("digest")
    provenance = manifest.get("provenance")
    source = provenance.get("source_digest") if isinstance(provenance, dict) else None
    return (
        (
            digest
            if isinstance(digest, str) and _BUNDLE_DIGEST.fullmatch(digest)
            else None
        ),
        (
            source
            if isinstance(source, str) and _SOURCE_DIGEST.fullmatch(source)
            else None
        ),
    )


def certificate_digest(certificate: Mapping[str, Any] | None) -> str | None:
    """The bundle digest a certificate vouches for (``compiler.bundle_digest``)."""
    compiler = (certificate or {}).get("compiler")
    digest = compiler.get("bundle_digest") if isinstance(compiler, dict) else None
    return digest if isinstance(digest, str) else None


def certified_source(certificate: Mapping[str, Any] | None) -> str | None:
    """The source digest a certificate records (``source.digest``)."""
    source = (certificate or {}).get("source")
    digest = source.get("digest") if isinstance(source, dict) else None
    return (
        digest if isinstance(digest, str) and _SOURCE_DIGEST.fullmatch(digest) else None
    )


def certifies_bundle(sealed: Mapping[str, bytes]) -> bool:
    """Whether the certificate is for this very bundle, not an older or partial one."""
    certificate = _json_object(sealed.get(CERTIFICATE))
    digest = manifest_facts(sealed)[0]
    return (
        certificate is not None
        and digest is not None
        and certificate_digest(certificate) == digest
    )


def sealed_certificate(sealed: Mapping[str, bytes]) -> dict[str, Any] | None:
    """The secret-safe certificate a sealed bundle is cached with, else ``None``."""
    from simulate.services.hosted_harness_gateway import _secret_safe

    if SEALED_MANIFEST not in sealed or CERTIFICATE not in sealed:
        return None
    certificate = _json_object(sealed[CERTIFICATE])
    if certificate is None:
        return None
    safe = _secret_safe(certificate)
    source_digest = manifest_facts(sealed)[1] or certified_source(certificate)
    if source_digest:
        safe["source_digest"] = source_digest
    return safe


def seed_ready_bundle(
    model: Any,
    *,
    job: Any,
    runtime: Any,
    environment_version: Any,
    suite_version: Any,
    sealed: Mapping[str, bytes],
    object_key: str,
    sha256: str,
    size: int,
    bundle_digest: str | None = None,
    replace_ready: bool = False,
) -> Any:
    """A ready cache row for a bundle a build already sealed, or ``None`` without one.

    Only a bundle its certificate is for is cached. An existing row that is not ready takes
    the bundle; a ready one is kept, unless ``replace_ready`` and its digest differs.
    """
    from simulate.services.harness_versioning.backfill import effective_runtime

    certificate = sealed_certificate(sealed)
    if certificate is None:
        return None
    # A shared legacy row stands for this environment's facts, as its runs look it up.
    key = bundle_key(
        effective_runtime(job, runtime), environment_version, suite_version
    )
    if not certifies_bundle(sealed):
        logger.warning(
            "harness_bundle_cache",
            outcome="seed_skipped",
            reason="certificate_digest_mismatch",
            environment_id=str(job.id),
            assembly_key=key,
        )
        return None
    manifest_digest, _ = manifest_facts(sealed)
    digest = manifest_digest or bundle_digest
    values = {
        "state": HarnessAssembledBundle.State.READY,
        "bundle_digest": digest,
        "object_key": object_key,
        "sha256": sha256,
        "size": size,
        "certificate": certificate,
        "warnings": [],
        "error": None,
    }
    bundle, created = model._base_manager.get_or_create(
        job_id=job.id,
        assembly_key=key,
        defaults={
            "organization_id": job.organization_id,
            "agent_version_id": runtime.agent_version_id,
            "environment_version_id": environment_version.id,
            "suite_version_id": suite_version.id,
            **values,
        },
    )
    if created:
        return bundle
    stale = model._base_manager.filter(id=bundle.id)
    if replace_ready:
        # A newer build of the same key replaces the bundle it sealed before.
        stale = stale.exclude(
            state=HarnessAssembledBundle.State.READY, bundle_digest=digest
        )
    else:
        stale = stale.exclude(state=HarnessAssembledBundle.State.READY)
    if stale.update(**values, updated_at=timezone.now()):
        bundle.refresh_from_db()
    return bundle


def seed_from_build(
    job: HostedHarnessJob,
    *,
    environment_version: HarnessEnvironmentVersion,
    suite_version: HarnessSuiteVersion,
    sealed: Mapping[str, bytes],
    object_key: str,
    sha256: str,
    size: int,
) -> HarnessAssembledBundle | None:
    """Cache the bundle a build sealed for the environment's agent, when it has a side row."""
    if not job.agent_version_id:
        return None
    runtime = HarnessAgentVersionRuntime.no_workspace_objects.filter(
        agent_version_id=job.agent_version_id
    ).first()
    if runtime is None:
        return None
    return seed_ready_bundle(
        HarnessAssembledBundle,
        job=job,
        runtime=runtime,
        environment_version=environment_version,
        suite_version=suite_version,
        sealed=sealed,
        object_key=object_key,
        sha256=sha256,
        size=size,
        replace_ready=True,
    )


# --- run create ------------------------------------------------------------------------


@dataclass(frozen=True)
class RunAssembly:
    """The cache row a new run is bound to."""

    bundle: HarnessAssembledBundle
    key: str
    cached: bool
    # Pinned to the environment's defaults when created, so a miss takes today's path.
    defaults: bool = True
    # The row was failed until this run reset it, so the run warns as run-plan did.
    failed_before: bool = False
    # The default pair holds no sealed bundle, so the run certifies one on a miss.
    unsealed: bool = False

    def as_metadata(self) -> dict[str, Any]:
        data = {
            "key": self.key,
            "bundle_id": str(self.bundle.id),
            "cached": self.cached,
            "defaults": self.defaults,
        }
        if self.failed_before:
            data["failed_before"] = True
        if self.unsealed:
            data["default_pair_sealed"] = False
        return data


def run_assembly(
    environment: HostedHarnessJob,
    runtime: HarnessAgentVersionRuntime | None,
    environment_version: HarnessEnvironmentVersion,
    suite_version: HarnessSuiteVersion,
    *,
    defaults: bool = True,
    unsealed: bool = False,
    key: str | None = None,
) -> RunAssembly | None:
    """The cache row for this combination, created pending when it is new; ``key``
    is one a rerun keeps from its source.

    Call under the environment's lock. A failed row is reset, so this run builds again.
    """
    if runtime is None:
        return None
    key = key or bundle_key(runtime, environment_version, suite_version)
    bundle, _ = HarnessAssembledBundle._base_manager.get_or_create(
        job_id=environment.id,
        assembly_key=key,
        defaults={
            "organization_id": environment.organization_id,
            "agent_version_id": runtime.agent_version_id,
            "environment_version_id": environment_version.id,
            "suite_version_id": suite_version.id,
            "state": HarnessAssembledBundle.State.PENDING,
        },
    )
    failed_before = bundle.state == HarnessAssembledBundle.State.FAILED
    if failed_before:
        HarnessAssembledBundle._base_manager.filter(
            id=bundle.id, state=HarnessAssembledBundle.State.FAILED
        ).update(**_RESET, updated_at=timezone.now())
        bundle.refresh_from_db()
    cached = bundle.state == HarnessAssembledBundle.State.READY and bool(
        bundle.object_key
    )
    return RunAssembly(
        bundle=bundle,
        key=key,
        cached=cached,
        defaults=defaults,
        failed_before=failed_before,
        unsealed=unsealed,
    )


def bundle_ready(
    environment: HostedHarnessJob,
    runtime: HarnessAgentVersionRuntime,
    environment_version: HarnessEnvironmentVersion,
    suite_version: HarnessSuiteVersion,
) -> bool:
    """Whether a run of this combination would replay a cached bundle."""
    return HarnessAssembledBundle._base_manager.filter(
        job_id=environment.id,
        assembly_key=bundle_key(runtime, environment_version, suite_version),
        state=HarnessAssembledBundle.State.READY,
        object_key__isnull=False,
    ).exists()


# --- launch ----------------------------------------------------------------------------


@dataclass(frozen=True)
class LaunchAssembly:
    """How a pinned run's sandbox gets its bundle: replayed (hit) or certified (miss)."""

    outcome: str
    key: str
    bundle_id: str
    source_digest: str | None = None

    @property
    def hit(self) -> bool:
        return self.outcome == "hit"


def _assembly(job: HostedHarnessJob) -> dict[str, Any] | None:
    value = ((job.payload or {}).get("metadata") or {}).get("assembly")
    return value if isinstance(value, dict) else None


def _reset_missing(bundle: HarnessAssembledBundle, reason: str) -> None:
    HarnessAssembledBundle._base_manager.filter(
        id=bundle.id, object_key=bundle.object_key
    ).update(**_RESET, updated_at=timezone.now())
    logger.warning(
        "harness_bundle_cache",
        outcome="cached_object_unusable",
        reason=reason,
        bundle_id=str(bundle.id),
        environment_id=str(bundle.job_id),
    )


def _cached_files(bundle: HarnessAssembledBundle) -> dict[str, bytes] | None:
    """The cached bundle's sealed files, or ``None`` when the row cannot be replayed.

    A ready row whose object is gone or not a valid archive goes back to pending; any other
    error (a storage outage) is raised and leaves the row alone.
    """
    if bundle.state != HarnessAssembledBundle.State.READY or not bundle.object_key:
        return None
    try:
        files = archives.read_archive(archives.get_archive(bundle.object_key))
    except S3Error as exc:
        if exc.code not in _MISSING_CODES:
            raise
        _reset_missing(bundle, "missing")
        return None
    except HostedHarnessError:
        _reset_missing(bundle, "unreadable")
        return None
    kept = {path: data for path, data in files.items() if archives._is_sealed(path)}
    if SEALED_MANIFEST not in kept or CERTIFICATE not in kept:
        _reset_missing(bundle, "incomplete")
        return None
    return kept


def is_transient(exc: BaseException) -> bool:
    """A storage outage, which a later launch may not meet; anything else will recur."""
    if isinstance(exc, S3Error):
        return exc.code not in _MISSING_CODES
    return isinstance(exc, (OSError, HTTPError))


def pins_are_defaults(job: HostedHarnessJob) -> bool:
    """Whether the run was pinned to its environment's defaults when it was created.

    A run created before that was recorded is compared with the current default pair.
    """
    recorded = (_assembly(job) or {}).get("defaults")
    if isinstance(recorded, bool):
        return recorded
    defaults = (
        HostedHarnessJob.no_workspace_objects.filter(id=job.environment_id)
        .values_list("environment_version_id", "suite_version_id")
        .first()
    )
    return defaults == (job.environment_version_id, job.suite_version_id)


def runs_unsealed(job: HostedHarnessJob) -> bool:
    """Whether the run was created at a default pair without a sealed bundle, which
    has no path but certifying."""
    return (_assembly(job) or {}).get("default_pair_sealed") is False


def falls_back(job: HostedHarnessJob) -> bool:
    """Whether the run may take today's path: the default pair, sealed or legacy."""
    return pins_are_defaults(job) and not runs_unsealed(job)


def overlay_agent_prompt(
    tree: dict[str, bytes], payload: Mapping[str, Any], agent_version: Any
) -> bool:
    """Give a provider agent's contract the pinned version's prompt; code agents keep theirs."""
    prompt = _overlay_prompt(payload, agent_version)
    contract = _json_object(tree.get("contract.json"))
    if prompt is None or contract is None:
        return False
    if contract.get("system_prompt_excerpt") == prompt:
        return False
    contract["system_prompt_excerpt"] = prompt
    tree["contract.json"] = json.dumps(contract, indent=2, ensure_ascii=False).encode()
    return True


def _overlay_prompt(payload: Mapping[str, Any], agent_version: Any) -> str | None:
    from simulate.services.harness_versioning.versions import _provider_agent

    if agent_version is None or not _provider_agent(payload):
        return None
    prompt = agent_version.description or ""
    return prompt if prompt.strip() else None


def assembled_key(
    organization_id: object, environment_id: object, inputs: Mapping[str, str]
) -> str:
    """Addressed by what the archive is built from, so a built one is never rebuilt."""
    digest = archives.sha256_hex(
        json.dumps(dict(inputs), sort_keys=True, separators=(",", ":")).encode()
    )
    return f"harness-assembled/{organization_id}/{environment_id}/{digest}.tar.gz"


def _assembled_inputs(
    job: HostedHarnessJob,
    environment_version: HarnessEnvironmentVersion,
    suite_version: HarnessSuiteVersion,
    bundle: HarnessAssembledBundle | None,
) -> dict[str, str]:
    # Only a miss is overlaid; a hit replays the sealed bundle's own contract.
    prompt = (
        None
        if bundle is not None
        else _overlay_prompt(job.payload or {}, job.agent_version)
    )
    return {
        "environment": environment_version.archive_sha256,
        "suite": suite_version.archive_sha256,
        "bundle": (bundle.sha256 or bundle.object_key or "") if bundle else "miss",
        "prompt": archives.sha256_hex(prompt.encode()) if prompt else "",
    }


def bundle_object_key(
    organization_id: object, environment_id: object, key: str, body: bytes
) -> str:
    # Content-addressed under the key, so two builders never overwrite each other.
    return (
        f"harness-bundles/{organization_id}/{environment_id}/"
        f"{key.removeprefix('sha256:')}/{archives.sha256_hex(body)}.tar.gz"
    )


def _memoised(job: HostedHarnessJob, assembly: Mapping[str, Any]) -> bool:
    metadata = (job.payload or {}).get("metadata") or {}
    return (
        assembly.get("outcome") in {"hit", "miss"}
        and bool(assembly.get("archive_key"))
        and assembly.get("archive_key") == metadata.get("authoring_object_key")
    )


def _hit_source_digest(
    bundle: HarnessAssembledBundle, cached: Mapping[str, bytes] | None
) -> str | None:
    """The agent source a cached bundle was built from, kept on its row once found."""
    certificate = bundle.certificate or {}
    if certificate.get("source_digest"):
        return certificate["source_digest"]
    found = certified_source(certificate)
    if found is None and cached is not None:
        found = manifest_facts(cached)[1] or certified_source(
            _json_object(cached.get(CERTIFICATE))
        )
    if found is None:
        return None
    try:
        with transaction.atomic():
            HarnessAssembledBundle._base_manager.filter(
                id=bundle.id, object_key=bundle.object_key
            ).update(
                certificate={**certificate, "source_digest": found},
                updated_at=timezone.now(),
            )
    except Exception:  # noqa: BLE001 - the run checks against it either way
        logger.warning(
            "harness_bundle_cache",
            outcome="source_digest_unsaved",
            bundle_id=str(bundle.id),
            exc_info=True,
        )
    return found


def _assembly_unavailable() -> HostedHarnessError:
    return HostedHarnessError(
        "assembly_unavailable",
        "the bundle for this run's pinned versions is no longer available "
        "and building it is turned off; start a new run",
        status_code=422,
    )


def prepare_launch(job: HostedHarnessJob) -> LaunchAssembly | None:
    """Point a pinned run at its assembled authoring archive, building it once.

    ``None`` keeps today's path: the run is not assembled, or it missed the cache while
    certifying is off and is pinned to the default pair; a run pinned off it, or to a
    default pair without a sealed bundle, raises ``assembly_unavailable``. The archive's
    key is stored on the run, so a retry reuses it.
    """
    assembly = _assembly(job)
    if assembly is None:
        return None
    if job.assembled_bundle_id is None:
        if runs_unsealed(job):
            raise _assembly_unavailable()
        return None
    if _memoised(job, assembly):
        return LaunchAssembly(
            outcome=str(assembly["outcome"]),
            key=str(assembly.get("key") or ""),
            bundle_id=str(assembly.get("bundle_id") or ""),
            source_digest=assembly.get("source_digest"),
        )
    bundle = HarnessAssembledBundle._base_manager.filter(
        id=job.assembled_bundle_id
    ).first()
    if bundle is None or job.environment_version_id is None:
        if runs_unsealed(job):
            raise _assembly_unavailable()
        return None
    log = {
        "run_id": str(job.id),
        "environment_id": str(job.environment_id),
        "bundle_id": str(bundle.id),
    }

    defaults = pins_are_defaults(job)
    unsealed = defaults and runs_unsealed(job)

    def fallback() -> None:
        # Today's path runs the default pair, so only a run pinned to it may take it.
        if not defaults or unsealed:
            raise _assembly_unavailable()
        logger.info("harness_bundle_cache", outcome="miss_fallback", **log)

    # Only a run pinned off the defaults certifies; a default-pair miss runs as today,
    # unless that pair was made without a sealed bundle.
    certifies = certify_enabled() and (not defaults or unsealed)
    hit = bundle.state == HarnessAssembledBundle.State.READY and bool(bundle.object_key)
    if not hit and not certifies:
        return fallback()
    environment_version = HarnessEnvironmentVersion._base_manager.get(
        id=job.environment_version_id
    )
    suite_version = HarnessSuiteVersion._base_manager.get(id=job.suite_version_id)

    def key_for(cached_bundle: HarnessAssembledBundle | None) -> str:
        return assembled_key(
            job.organization_id,
            job.environment_id,
            _assembled_inputs(job, environment_version, suite_version, cached_bundle),
        )

    object_key = key_for(bundle if hit else None)
    built = archives.archive_exists(object_key)
    cached = None
    if hit and not built:
        cached = _cached_files(bundle)
        if cached is None:
            hit = False
            if not certifies:
                return fallback()
            object_key = key_for(None)
            built = archives.archive_exists(object_key)
    if not built:
        tree = archives.compose(
            archives.read_archive(
                archives.get_archive(environment_version.archive_object_key)
            ),
            archives.read_archive(
                archives.get_archive(suite_version.archive_object_key)
            ),
            cached,
        )
        if not hit:
            overlay_agent_prompt(tree, job.payload or {}, job.agent_version)
        archives.put_archive(object_key, archives.pack_archive(tree))
    source_digest = _hit_source_digest(bundle, cached) if hit else None
    plan = LaunchAssembly(
        outcome="hit" if hit else "miss",
        key=bundle.assembly_key,
        bundle_id=str(bundle.id),
        source_digest=source_digest,
    )
    with transaction.atomic():
        locked = HostedHarnessJob.no_workspace_objects.select_for_update().get(
            id=job.id
        )
        payload = dict(locked.payload or {})
        metadata = dict(payload.get("metadata") or {})
        metadata["authoring_object_key"] = object_key
        metadata["assembly"] = {
            **(metadata.get("assembly") or {}),
            "cached": plan.hit,
            "outcome": plan.outcome,
            "archive_key": object_key,
            "source_digest": source_digest,
            "bundle_object_key": bundle.object_key if hit else None,
        }
        payload["metadata"] = metadata
        locked.payload = payload
        update_fields = ["payload", "updated_at"]
        if hit and bundle.bundle_digest:
            locked.bundle_digest = bundle.bundle_digest
            update_fields.append("bundle_digest")
        locked.save(update_fields=update_fields)
    job.payload = locked.payload
    job.bundle_digest = locked.bundle_digest
    logger.info("harness_bundle_cache", outcome=plan.outcome, composed=not built, **log)
    return plan


def certify_scenario_count(job: HostedHarnessJob) -> int:
    return HarnessSuiteMember.no_workspace_objects.filter(
        suite_version_id=job.suite_version_id
    ).count()


def certify_job_payload(
    dispatch_payload: Mapping[str, Any], scenario_count: int
) -> dict[str, Any]:
    """The job a miss certifies the pinned pair with: the whole suite, once."""
    certify = copy.deepcopy(dict(dispatch_payload))
    certify["scenario_count"] = scenario_count
    metadata = dict(certify.get("metadata") or {})
    for name in _CERTIFY_DROPPED:
        metadata.pop(name, None)
    metadata["generic_harness_v1"] = True
    certify["metadata"] = metadata
    return certify


def source_check_command(source_digest: str | None) -> str:
    """Refuse to replay a cached bundle against other agent source; exit 78 if it differs."""
    if not source_digest or not _SOURCE_DIGEST.fullmatch(source_digest):
        return ""
    code = (
        "import sys; from fi.alk.harness.provision import source_fingerprint; "
        "e = sys.argv[1]; a = source_fingerprint('/work/source'); "
        "a == e or (sys.stderr.write('bundle_source_mismatch: the cached environment "
        "bundle was built from other agent source (expected %s, found %s)\\n' % (e, a)), "
        "sys.exit(78))"
    )
    return f'python -c "{code}" {source_digest} && '


def certify_command() -> str:
    """Certify the pinned pair into ``/work/bundle`` and make it the run's bundle.

    Certifying writes ``/work/runtime-validation.json`` itself once the bundle is in place
    and never fetches a provider profile.
    """
    return (
        "if [ -f /work/authoring/scenarios.json ]; then "
        f"cp /work/authoring/scenarios.json {SUITE_INDEX_PATH}; fi && "
        "python -m "
        "fi.alk.harness.hosted_authoring_entrypoint "
        f"{CERTIFY_JOB_PATH} --source /work/source --output /work/authoring "
        "--certify-only --bundle-output /work/bundle "
        "&& rm -rf /work/authoring/environment-bundle "
        "&& cp -a /work/bundle /work/authoring/environment-bundle"
    )


# --- fill ------------------------------------------------------------------------------


def _set_fill_attempt(
    job: HostedHarnessJob, attempt_number: int, *, claim: bool
) -> bool:
    """Claim (or release) this attempt's one fill, so a poll never packs the bundle twice."""
    with transaction.atomic():
        locked = HostedHarnessJob.no_workspace_objects.select_for_update().get(
            id=job.id
        )
        payload = dict(locked.payload or {})
        metadata = dict(payload.get("metadata") or {})
        assembly = dict(metadata.get("assembly") or {})
        held = assembly.get("fill_attempt") == attempt_number
        if held == claim:
            return False
        if claim:
            assembly["fill_attempt"] = attempt_number
        else:
            assembly.pop("fill_attempt", None)
        metadata["assembly"] = assembly
        payload["metadata"] = metadata
        locked.payload = payload
        locked.save(update_fields=["payload", "updated_at"])
    return True


def certifies_here(job: HostedHarnessJob) -> bool:
    """Whether the run certifies its own bundle (a miss)."""
    assembly = _assembly(job)
    return (
        assembly is not None
        and assembly.get("outcome") == "miss"
        and job.assembled_bundle_id is not None
    )


def wants_fill(job: HostedHarnessJob, attempt_number: int) -> bool:
    """A miss whose bundle this attempt has not cached yet."""
    return (
        certifies_here(job)
        and (_assembly(job) or {}).get("fill_attempt") != attempt_number
    )


def bundle_paths(manifest: Mapping[str, Any]) -> list[str] | None:
    """The bundle's own files: its manifest and each file it lists, else ``None``."""
    paths = ["manifest.json"]
    for record in manifest.get("files") or []:
        path = record.get("path") if isinstance(record, dict) else None
        if not isinstance(path, str):
            return None
        parts = PurePosixPath(path).parts
        if not path or path.startswith("/") or ".." in parts or "\0" in path:
            return None
        if "__pycache__" not in parts and path not in paths:
            paths.append(path)
    return paths


def _fill_list(paths: list[str]) -> bytes:
    """The bundle files to pack, NUL-separated for ``tar --null -T``."""
    return b"".join(f"bundle/{path}".encode() + b"\0" for path in paths)


def _fill_command() -> str:
    """Pack the certified bundle with every sealed file certifying left in authoring.

    Only the files the bundle's manifest lists (uploaded to ``_FILL_LIST``) are packed,
    never others left in it.
    """
    # The certified-bundle copy is left out: a hit copies environment-bundle, never reuses it.
    candidates = " ".join(
        [
            *(
                f"authoring/{name}"
                for name in sorted(archives.SEALED_FILES)
                if name != CERTIFICATE
            ),
            *(f"authoring/{prefix}*" for prefix in archives.SEALED_PREFIXES),
        ]
    )
    return (
        f"rm -f {_FILL_ARCHIVE} && cd /work && set -- && "
        f'for f in {candidates}; do [ -e "$f" ] && set -- "$@" "$f"; done; '
        f'tar -czf {_FILL_ARCHIVE} --null -T {_FILL_LIST} {CERTIFICATE} "$@"'
    )


def _filled_files(files: Mapping[str, bytes], paths: list[str]) -> dict[str, bytes]:
    listed = {f"bundle/{path}" for path in paths}
    sealed: dict[str, bytes] = {}
    for path, data in files.items():
        if path in listed:
            sealed[BUNDLE_ROOT + path.removeprefix("bundle/")] = data
        elif path == CERTIFICATE:
            sealed[path] = data
        elif path.startswith("authoring/"):
            name = path.removeprefix("authoring/")
            if archives._is_sealed(name) and not name.startswith(archives.SEALED_ROOTS):
                sealed[name] = data
    return sealed


def certify_warnings(sandbox: Any) -> list[dict[str, Any]]:
    """What certifying dropped from or narrowed in the suite; best-effort."""
    try:
        report = (
            _json_object(
                sandbox.fs.download_file(
                    CERTIFY_REPORT_PATH, _CERTIFY_REPORT_TIMEOUT_SECONDS
                )
            )
            or {}
        )
    except Exception:  # noqa: BLE001 - an older image writes no report
        return []
    warnings: list[dict[str, Any]] = []
    dropped = report.get("dropped")
    if isinstance(dropped, list) and dropped:
        warnings.append(
            {"code": "scenario_dropped", "keys": [str(key) for key in dropped]}
        )
    narrowed = report.get("narrowed")
    if isinstance(narrowed, dict) and narrowed:
        warnings.append({"code": "sub_goals_narrowed", "scenarios": narrowed})
    return warnings


def fill_bundle(
    job: HostedHarnessJob, attempt: Any, sandbox: Any, manifest: Mapping[str, Any]
) -> None:
    """Cache the bundle a miss just certified; the first writer wins. Never raises.

    ``manifest`` is the certified bundle's manifest, which lists the files to pack.
    """
    log = {
        "run_id": str(job.id),
        "environment_id": str(job.environment_id),
        "bundle_id": str(job.assembled_bundle_id),
    }
    attempt_number = int(getattr(attempt, "attempt_number", 0) or 0)
    try:
        if not _set_fill_attempt(job, attempt_number, claim=True):
            return
        bundle = HarnessAssembledBundle._base_manager.filter(
            id=job.assembled_bundle_id
        ).first()
        if bundle is None or bundle.state == HarnessAssembledBundle.State.READY:
            # The run still records the bundle it certified.
            digest = manifest.get("digest")
            if isinstance(digest, str) and _BUNDLE_DIGEST.fullmatch(digest):
                HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(
                    bundle_digest=digest
                )
            logger.info("harness_bundle_cache", outcome="fill_skipped", **log)
            return
        paths = bundle_paths(manifest)
        if paths is None:
            logger.warning(
                "harness_bundle_cache",
                outcome="fill_skipped",
                reason="manifest_invalid",
                **log,
            )
            return
        sandbox.fs.upload_file(_fill_list(paths), _FILL_LIST)
        packed = sandbox.process.exec(
            _fill_command(), timeout=_FILL_EXEC_TIMEOUT_SECONDS
        )
        if packed.exit_code:
            # Packing the same files fails again, so the claim is kept.
            logger.warning(
                "harness_bundle_cache",
                outcome="fill_skipped",
                reason="pack_failed",
                exit_code=packed.exit_code,
                **log,
            )
            return
        sealed = _filled_files(
            archives.read_archive(
                sandbox.fs.download_file(_FILL_ARCHIVE, _FILL_DOWNLOAD_TIMEOUT_SECONDS)
            ),
            paths,
        )
        certificate = sealed_certificate(sealed)
        if certificate is None:
            # Not all written yet; a later poll packs it again.
            _set_fill_attempt(job, attempt_number, claim=False)
            logger.warning(
                "harness_bundle_cache",
                outcome="fill_skipped",
                reason="incomplete",
                **log,
            )
            return
        if not certifies_bundle(sealed):
            logger.warning(
                "harness_bundle_cache",
                outcome="fill_skipped",
                reason="certificate_digest_mismatch",
                **log,
            )
            return
        digest, _ = manifest_facts(sealed)
        HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(
            bundle_digest=digest
        )
        body = archives.pack_archive(sealed)
        object_key = bundle_object_key(
            job.organization_id, bundle.job_id, bundle.assembly_key, body
        )
        archives.put_archive(object_key, body)
        filled = (
            HarnessAssembledBundle._base_manager.filter(id=bundle.id)
            .exclude(state=HarnessAssembledBundle.State.READY)
            .update(
                state=HarnessAssembledBundle.State.READY,
                object_key=object_key,
                sha256=archives.sha256_hex(body),
                size=len(body),
                bundle_digest=digest,
                certificate=certificate,
                warnings=certify_warnings(sandbox),
                built_by_attempt_id=attempt.id,
                error=None,
                updated_at=timezone.now(),
            )
        )
        if filled:
            logger.info("harness_bundle_cache", outcome="filled", **log)
            return
        current = (
            HarnessAssembledBundle._base_manager.filter(id=bundle.id)
            .values_list("bundle_digest", flat=True)
            .first()
        )
        logger.info(
            "harness_bundle_cache",
            outcome="fill_skipped",
            reason="different_digest" if current != digest else "already_ready",
            **log,
        )
    except Exception:  # noqa: BLE001 - the cache is best-effort; the run goes on
        logger.exception(
            "harness_bundle_cache", outcome="fill_skipped", reason="error", **log
        )
        try:
            # A later poll packs it again.
            _set_fill_attempt(job, attempt_number, claim=False)
        except Exception:  # noqa: BLE001
            logger.exception("harness_bundle_cache", outcome="fill_unreleased", **log)


def mark_failed(job: HostedHarnessJob, message: str) -> None:
    """A rejected certify leaves its row failed, so the next run rebuilds. A hit whose
    source check failed puts its row back to pending, so the next run certifies again or
    falls back.
    """
    assembly = _assembly(job)
    if assembly is None or job.assembled_bundle_id is None:
        return
    rows = HarnessAssembledBundle._base_manager.filter(id=job.assembled_bundle_id)
    if assembly.get("outcome") == "hit":
        # Only the object this run replayed; a newer fill stays.
        rows.filter(object_key=assembly.get("bundle_object_key")).update(
            **_RESET, updated_at=timezone.now()
        )
        outcome = "reset"
    elif assembly.get("outcome") == "miss":
        rows.exclude(state=HarnessAssembledBundle.State.READY).update(
            state=HarnessAssembledBundle.State.FAILED,
            error={
                "code": "authoring_runtime_validation_failed",
                "message": message[:1000],
                "run_id": str(job.id),
            },
            updated_at=timezone.now(),
        )
        outcome = "failed"
    else:
        return
    logger.info(
        "harness_bundle_cache",
        outcome=outcome,
        run_id=str(job.id),
        bundle_id=str(job.assembled_bundle_id),
    )
