"""Readiness only: bounded PostgreSQL/schema and hosted shared-state checks.

No license/vendor requests, secret resolution, decrypts, writes or migrations.
Liveness remains /health/. No cached success can survive a dependency outage.
"""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from functools import lru_cache
from threading import Lock

from django.conf import settings
from django.db import connections
from django.db.migrations.loader import MigrationLoader
from django.http import JsonResponse
from redis import Redis
from redis.backoff import NoBackoff
from redis.retry import Retry

from agentcc.gateway_configuration import hosted_gateway_public_url_missing
from agentcc.hosted import deployment_profile, validate_hosted_configuration


@lru_cache(maxsize=1)
def required_migrations():
    # Build once from this image's installed code, never from a hardcoded tail.
    loader = MigrationLoader(None, ignore_no_migrations=True)
    required = set()
    for leaf in loader.graph.leaf_nodes():
        required.update(loader.graph.forwards_plan(leaf))
    replacements = {
        node: tuple(migration.replaces)
        for node, migration in loader.replacements.items()
    }
    return frozenset(required), replacements


def database_ready():
    """An isolated connection: do not change a request's transaction/timeouts."""
    connection = connections["default"].copy(alias="hosted_readiness")
    if connection.vendor != "postgresql":
        connection.close()
        return False
    connection.settings_dict["CONN_MAX_AGE"] = 0
    connection.settings_dict["OPTIONS"] = {
        **connection.settings_dict.get("OPTIONS", {}),
        "connect_timeout": 2,
        "options": "-c statement_timeout=1000 -c lock_timeout=500",
    }
    try:
        required, replacements = required_migrations()
        with connection.cursor() as cursor:
            cursor.execute("SELECT app, name FROM django_migrations")
            applied = set(cursor.fetchall())
        return all(
            node in applied
            or (
                node in replacements
                and all(old in applied for old in replacements[node])
            )
            for node in required
        )
    finally:
        connection.close()


def shared_state_ready():
    locations = {settings.REDIS_URL, settings.CACHES["default"]["LOCATION"]}
    for location in locations:
        with Redis.from_url(
            location,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
            retry_on_timeout=False,
            retry=Retry(NoBackoff(), 0),
        ) as client:
            if not client.ping():
                return False
    return True


# One in-flight check per process, no queued probes. Driver timeouts do not
# bound OS DNS resolution; this outer budget also bounds the HTTP response
# without spawning an unbounded set of abandoned threads during an outage.
_probe_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hosted-readiness")
_probe_lock = Lock()
_pending = None
PROBE_DEADLINE_SECONDS = 4


def _check_dependencies():
    reason = "configuration"
    try:
        hosted = deployment_profile() == "hosted"
        validate_hosted_configuration()
        reason = "gateway_public_url"
        if hosted_gateway_public_url_missing():
            return reason
        reason = "database_or_schema"
        if not database_ready():
            return reason
        if hosted:
            reason = "shared_state"
            if not shared_state_ready():
                return reason
    except Exception:
        # Dependency exceptions frequently contain addresses/passwords/SQL.
        # Neither log nor return them from this unauthenticated probe.
        return reason
    return None


def readiness(request):
    global _pending
    if not _probe_lock.acquire(blocking=False):
        return _not_ready("probe_busy")
    try:
        if _pending is not None and not _pending.done():
            return _not_ready("dependency_timeout")
        # Discard any late result: never reuse a prior probe's cached success.
        _pending = _probe_pool.submit(_check_dependencies)
        try:
            reason = _pending.result(timeout=PROBE_DEADLINE_SECONDS)
        except TimeoutError:
            return _not_ready("dependency_timeout")
        if reason:
            return _not_ready(reason)
    finally:
        _probe_lock.release()
    response = JsonResponse({"status": "ready"})
    response["Cache-Control"] = "no-store"
    return response


def _not_ready(reason):
    response = JsonResponse({"status": "not_ready", "reason": reason}, status=503)
    response["Cache-Control"] = "no-store"
    return response
