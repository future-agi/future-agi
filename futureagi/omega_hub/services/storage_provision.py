"""Who owns the Hub bucket (HUB-19): provisioning, verification and reconciliation.

The request path never calls a bucket-admin API, so Hub object credentials can stay object-scoped
(ops/iam/app-policy.json). The bucket is made ready by three callers, all through `provision`:

- `post_migrate` (omega_hub.apps): every shipped bootstrap path runs `migrate` (the Helm bootstrap
  job's bootstrap_install, the compose entrypoint's SERVICE_TYPE=bootstrap, the standalone
  bootstrap and omega-local), so a fresh deploy is provisioned before its first upload;
- the five-minute `omega-hub-operations-check` Temporal activity, which repairs what bootstrap could
  not (storage not up yet at migrate time, the standalone bootstrap skipping an unchanged migrate)
  and pages the maintainers when the bucket is still not ready;
- `manage.py omega_hub_storage provision` in an operator job.

`provision` only writes what is missing, so a bucket that infrastructure code already configured
needs read access to its configuration and nothing more. On S3 and MinIO the bucket must have
object lock (bundles are written under a GOVERNANCE retention). GCS buckets are created by
Terraform, as for the rest of the platform (tfc.utils.storage_client.ensure_bucket); the S3 interop
API has no object lock there, so the Hub verifies the bucket and its versioning and the retention
is the bucket's own retention policy.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import structlog
from django.core.exceptions import ImproperlyConfigured

from omega_hub import conf

logger = structlog.get_logger(__name__)

RAW_LIFECYCLE_RULE_ID = "omega-hub-raw-noncurrent-expiry"
BOOTSTRAP_TIMEOUT_SECONDS = 60
RECONCILE_TIMEOUT_SECONDS = 30
# The last verdict on the bucket, shared by every process through the cache: the operations check
# and the operator's status/provision commands write it; intake and the checker's claim read it.
# An hour covers twelve missed checks; after that the verdict is unknown again, never stale.
STATUS_CACHE_KEY = "omega_hub:storage-status"
STATUS_TTL = 3600


class StorageNotProvisioned(ImproperlyConfigured):
    """The bucket is missing something the Hub needs and this process cannot add it."""


def remember(result: dict[str, Any]) -> None:
    from django.core.cache import cache

    cache.set(STATUS_CACHE_KEY, bool(result.get("ready")), timeout=STATUS_TTL)


def known_unready() -> bool:
    """True only when the last check found the bucket not ready. Unknown (no check yet, or the
    cache unreachable) is not unready: the write itself then answers a typed 503."""
    from django.core.cache import cache

    try:
        return cache.get(STATUS_CACHE_KEY) is False
    except Exception:  # noqa: BLE001 - a cache outage must not refuse uploads by itself
        return False


def require_ready() -> None:
    """Refuse new work up front while the bucket is known not ready, so the Hub never accepts an
    upload it could not take to approval (a bucket without object lock takes raw uploads but
    refuses every bundle)."""
    from omega_hub.errors import HubError

    if known_unready():
        raise HubError("OMEGA_HUB_STORAGE_NOT_PROVISIONED", 503,
                       message="Hub storage is not ready, so uploads are paused. Try again later.",
                       headers={"Retry-After": "300"})


def backend() -> str:
    from tfc.utils import storage_client

    return storage_client.STORAGE_BACKEND


def object_lock_supported() -> bool:
    """S3 and MinIO carry object lock on the S3 API; GCS interop does not."""
    return backend() != "gcs"


def _code(exc: Exception) -> str:
    return getattr(exc, "code", "") or exc.__class__.__name__


def status(client, bucket: str) -> dict[str, Any]:
    """Read-only view of the bucket: what is configured and what is missing."""
    from minio.error import S3Error
    from minio.versioningconfig import ENABLED

    result: dict[str, Any] = {"bucket": bucket, "backend": backend(), "exists": False, "versioning": False,
                              "objectLock": None, "rawLifecycle": None, "problems": []}
    if not client.bucket_exists(bucket):
        result["problems"].append(f"bucket {bucket} does not exist")
        result["ready"] = False
        return result
    result["exists"] = True
    result["versioning"] = getattr(client.get_bucket_versioning(bucket), "status", None) == ENABLED
    if not result["versioning"]:
        result["problems"].append("versioning is not enabled")
    if object_lock_supported():
        try:
            client.get_object_lock_config(bucket)
            result["objectLock"] = True
        except S3Error as exc:
            if _code(exc) != "ObjectLockConfigurationNotFoundError":
                raise
            result["objectLock"] = False
            result["problems"].append("object lock is not enabled")
        lifecycle = client.get_bucket_lifecycle(bucket)
        rules = list(getattr(lifecycle, "rules", None) or [])
        result["rawLifecycle"] = any(getattr(rule, "rule_id", None) == RAW_LIFECYCLE_RULE_ID for rule in rules)
        if not result["rawLifecycle"]:
            result["problems"].append(f"lifecycle rule {RAW_LIFECYCLE_RULE_ID} is missing")
    result["ready"] = not result["problems"]
    return result


def _raw_rule():
    from minio.commonconfig import ENABLED, Filter
    from minio.lifecycleconfig import NoncurrentVersionExpiration, Rule

    # Bounded non-current raw versions once retention has erased the current raw object. Bundles
    # are excluded so a pin can be restored from any retained version.
    return Rule(ENABLED, rule_filter=Filter(prefix="raw/"), rule_id=RAW_LIFECYCLE_RULE_ID,
                noncurrent_version_expiration=NoncurrentVersionExpiration(noncurrent_days=1))


def provision(client, bucket: str) -> dict[str, Any]:
    """Create or complete the bucket's configuration; idempotent; writes only what is missing."""
    from minio.lifecycleconfig import LifecycleConfig
    from minio.versioningconfig import ENABLED, VersioningConfig

    current = status(client, bucket)
    if current["ready"]:
        return current
    if not object_lock_supported():
        raise StorageNotProvisioned(
            f"Omega Hub bucket {bucket} on GCS is not ready ({'; '.join(current['problems'])}). GCS buckets are "
            "created by Terraform: create it with object versioning and a retention policy (ops/RUNBOOK.md)."
        )
    if not current["exists"]:
        client.make_bucket(bucket, object_lock=True)
        logger.info("omega_hub_bucket_created", bucket=bucket)
        current = status(client, bucket)
    if current["objectLock"] is False:
        raise StorageNotProvisioned(
            f"Omega Hub bucket {bucket} exists without object lock, and S3 and MinIO enable object lock only "
            "when a bucket is created. Create a new bucket with object lock (or let the Hub create it: "
            "point OMEGA_HUB_BUCKET at a name that does not exist yet) and move the objects across."
        )
    if not current["versioning"]:
        client.set_bucket_versioning(bucket, VersioningConfig(ENABLED))
    if not current["rawLifecycle"]:
        existing = client.get_bucket_lifecycle(bucket)
        rules = [rule for rule in (getattr(existing, "rules", None) or []) if rule.rule_id != RAW_LIFECYCLE_RULE_ID]
        client.set_bucket_lifecycle(bucket, LifecycleConfig([*rules, _raw_rule()]))
    final = status(client, bucket)
    if not final["ready"]:
        raise StorageNotProvisioned(f"Omega Hub bucket {bucket} is not ready: {'; '.join(final['problems'])}")
    logger.info("omega_hub_bucket_ready", bucket=bucket, backend=final["backend"])
    return final


def _bounded(fn, timeout: float):
    """Run `fn` with a wall-clock bound. The platform storage client waits up to 300 s per connect
    with five retries, which must not hold a bootstrap job or the operations check hostage."""
    outcome: dict[str, Any] = {}

    def run():
        try:
            outcome["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised in the caller's thread
            outcome["error"] = exc

    worker = threading.Thread(target=run, name="omega-hub-storage", daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise TimeoutError(f"object storage did not answer within {timeout:g} s")
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


def _unreachable(exc: Exception) -> bool:
    """Storage not listening yet (connection refused or reset), as opposed to an answer."""
    from urllib3.exceptions import HTTPError as Urllib3Error

    return isinstance(exc, (ConnectionError, Urllib3Error)) and not hasattr(exc, "code")


def reconcile(*, timeout: float = RECONCILE_TIMEOUT_SECONDS, client=None, wait_for_storage: bool = False) -> dict[str, Any]:
    """Make the configured bucket ready if this process may; report, never raise.

    `wait_for_storage` retries while the endpoint refuses connections, within `timeout`: the
    standalone image starts its MinIO beside the bootstrap, which does not wait for it.
    """
    kind = conf.storage_kind()
    if kind != "object":
        return {"ready": True, "kind": kind}
    bucket = conf.bucket()
    deadline = time.monotonic() + timeout
    while True:
        try:
            if client is None:
                from tfc.utils.storage_client import get_storage_client

                client = get_storage_client()
            result = _bounded(lambda c=client: provision(c, bucket), max(deadline - time.monotonic(), 0.1))
            return {"kind": kind, **result}
        except Exception as exc:  # noqa: BLE001 - every failure is reported as "not ready"
            if wait_for_storage and _unreachable(exc) and time.monotonic() + 2 < deadline:
                time.sleep(2)
                continue
            if isinstance(exc, TimeoutError):
                problem = f"object storage did not answer within {timeout:g} s"
            elif isinstance(exc, StorageNotProvisioned):
                problem = str(exc)
            else:
                problem = f"{_code(exc)}: {exc}"
            logger.error("omega_hub_storage_not_ready", bucket=bucket, problem=problem[:500])
            return {"ready": False, "kind": kind, "bucket": bucket, "problems": [problem[:500]]}


def provision_after_migrate(sender=None, **kwargs) -> dict[str, Any] | None:
    """`post_migrate` receiver: provision the bucket in the bootstrap step of every deploy shape.

    A failure does not fail the migration (the rest of the platform does not depend on the Hub's
    bucket); it is printed with the bootstrap output, and the operations check retries and pages.
    """
    from django.conf import settings

    if getattr(settings, "TESTING", False) or conf.storage_kind() != "object":
        # The test settings provision nothing at test-database creation; tests call this directly.
        return None
    result = reconcile(timeout=BOOTSTRAP_TIMEOUT_SECONDS, wait_for_storage=True)
    stdout = kwargs.get("stdout")
    if stdout is not None and kwargs.get("verbosity", 1):
        if result["ready"]:
            stdout.write(f"Omega Hub storage: bucket {result['bucket']} is ready (versioned, object lock).\n")
        else:
            stdout.write(f"Omega Hub storage NOT ready: {'; '.join(result['problems'])}. Uploads answer 503 until it "
                         "is fixed; the operations check retries every 5 minutes and pages the maintainers.\n")
    if result["ready"]:
        result["bundleLocks"] = _renew_bundle_locks(stdout if kwargs.get("verbosity", 1) else None)
    return result


def _renew_bundle_locks(stdout) -> dict[str, Any]:
    """Renew live bundles' locks at bootstrap, so a deploy that adds renewal (or restores a database)
    is not paged `omega_hub_bundle_lock_renewal_overdue` until the first nightly retention run. Like
    provisioning, a failure never fails the migration: the nightly run retries and the page stands."""
    from omega_hub.services import bundle_locks

    try:
        locks = bundle_locks.renew_due()
    except Exception as exc:  # noqa: BLE001 - before the tables exist, or storage refusing; retried nightly
        logger.warning("omega_hub_bundle_lock_bootstrap_failed", error=f"{exc.__class__.__name__}: {exc}"[:300])
        return {"renewed": 0, "failed": 0, "applies": None, "error": exc.__class__.__name__}
    if stdout is not None and locks["applies"]:
        failed = f" ({locks['failed']} failed; the nightly retention run retries)" if locks["failed"] else ""
        stdout.write(f"Omega Hub storage: renewed the lock on {locks['renewed']} live bundles{failed}.\n")
    return locks
