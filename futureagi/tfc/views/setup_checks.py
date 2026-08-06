"""``GET /api/setup-checks/`` — infrastructure probes for the OSS first-run screen.

Unauthenticated by necessity: this runs before the first account exists, which
is the whole point of the screen. Stateless too — every request re-probes, so a
caller polling while the stack boots sees services flip to green as they come
up, rather than a snapshot frozen at the first attempt.

Whether a service is up is a fact about the deployment and never varies by
launch mode. The launch mode decides only how that fact is *reported*: whether
it blocks Continue (``required``) and what a down service is downgraded to
(``on_down``). ``on_down = SKIPPED`` is how a mode declares it does not run a
service at all — the row renders as "Optional" and drops out of the blocking
set, so the container being down reads as the expected state rather than a
fault.

``down_detail`` sits on the check, not inside a mode, because what breaks when a
service is down does not depend on which mode you picked — only whether you are
stopped for it does. One string per check also means the two modes cannot drift
into describing the same outage differently.

Each one names the capability the operator loses, not the failure itself.
"Cannot connect to fi-collector" tells them nothing they cannot see from the
row's status; "spans sent by the SDK will not arrive" tells them what breaks.
Keep them plain and free of mode wording.

This endpoint only reports. It never starts or stops anything — bringing model
serving up or down stays an operator decision, which is why neither mode blocks
on it: an install run purely for observability is a legitimate deployment.

What the modes actually disagree about is the supporting infrastructure. Only
seven services are interdependent enough that nothing works without them, and
those block in both modes. The rest are feature-level: a capability is lost, the
application still runs, and a live deployment says so loudly where an
experimenting one stays quiet.
"""

import os
import socket
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import boto3
import redis
import requests
from botocore.config import Config
from botocore.exceptions import ClientError
from django.core.cache import cache
from django.db import connections
from rest_framework import status
from rest_framework.views import APIView

from tfc.ee_gating import is_oss
from tfc.settings import settings
from tfc.temporal import TEMPORAL_HOST
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import (
    ApiTextErrorResponseSerializer,
    SetupChecksResponseSerializer,
)
from tfc.utils.general_methods import GeneralMethods

PASSED = "passed"
WARNING = "warning"
FAILED = "failed"
SKIPPED = "skipped"

LIVE = "live"
EXPERIMENT = "experiment"
PROBE_TIMEOUT_SECONDS = 3

SNAPSHOT_TTL_SECONDS = 3


def _tcp_up(host: str, port: int) -> bool:
    """Reachability only. Used where the service speaks a protocol we would gain
    nothing from completing a handshake on (AMQP, OTLP gRPC, Temporal gRPC)."""
    with socket.create_connection((host, port), PROBE_TIMEOUT_SECONDS):
        return True


def _http_ok(url: str) -> bool:
    return requests.get(url, timeout=PROBE_TIMEOUT_SECONDS).status_code == 200


def _host_port(url: str, default_port: int) -> tuple:
    parsed = urlparse(url)
    return parsed.hostname or url, parsed.port or default_port


def _postgres_up() -> bool:
    with connections["default"].cursor() as cursor:
        cursor.execute("SELECT 1")
    return True


def _clickhouse_up() -> bool:
    host = os.environ.get("CH_HOST", "clickhouse")
    port = os.environ.get("CH_HTTP_PORT", "8123")
    return _http_ok(f"http://{host}:{port}/ping")


def _redis_up() -> bool:
    client = redis.from_url(
        settings.REDIS_URL,
        socket_connect_timeout=PROBE_TIMEOUT_SECONDS,
        socket_timeout=PROBE_TIMEOUT_SECONDS,
    )
    try:
        return bool(client.ping())
    finally:
        client.close()


def _rabbitmq_up() -> bool:
    host, port = _host_port(
        os.environ.get("CELERY_BROKER_URL", "amqp://rabbitmq:5672//"), 5672
    )
    return _tcp_up(host, port)


def _temporal_up() -> bool:
    host, _, port = TEMPORAL_HOST.rpartition(":")
    return _tcp_up(host, int(port))


def _s3_endpoint_url():
    """Resolve the endpoint the same way ``tfc.utils.storage_client`` does, so
    the probe talks to the storage the application actually writes to."""
    raw = os.environ.get("S3_ENDPOINT") or os.environ.get("S3_ENDPOINT_URL")
    if not raw:
        return None
    if "://" in raw:
        return raw
    secure_env = os.environ.get("S3_SECURE")
    if secure_env is not None:
        secure = secure_env.lower() == "true"
    else:
        secure = os.environ.get("STORAGE_BACKEND", "s3").lower() == "s3"
    return f"{'https' if secure else 'http'}://{raw}"


def _object_storage_up() -> bool:
    client = boto3.client(
        "s3",
        endpoint_url=_s3_endpoint_url(),
        aws_access_key_id=os.environ.get("S3_ACCESS_KEY")
        or os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("S3_SECRET_KEY")
        or os.environ.get("AWS_SECRET_ACCESS_KEY"),
        config=Config(
            connect_timeout=PROBE_TIMEOUT_SECONDS,
            read_timeout=PROBE_TIMEOUT_SECONDS,
            retries={"max_attempts": 0},
        ),
    )
    try:
        client.head_bucket(Bucket=settings.UPLOAD_BUCKET_NAME)
    except ClientError as exc:
        code = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        return code is not None and code != 404
    return True


def _gateway_up() -> bool:
    base = os.environ.get("AGENTCC_INTERNAL_URL", "http://agentcc-gateway:8080")
    return _http_ok(f"{base.rstrip('/')}/healthz")


def _collector_up() -> bool:
    host = os.environ.get("FI_COLLECTOR_HOST", "fi-collector")
    port = int(os.environ.get("FI_COLLECTOR_OTLP_PORT", "4317"))
    return _tcp_up(host, port)


def _code_executor_up() -> bool:
    base = os.environ.get("CODE_EXECUTOR_URL", "http://code-executor:8060")
    return _http_ok(f"{base.rstrip('/')}/health")


def _model_serving_up() -> bool:
    base = os.environ.get("MODEL_SERVING_URL", "http://serving:8080")
    return _http_ok(f"{base.rstrip('/')}/health")



CHECKS = (
    {
        "id": "database",
        "label": "Core application database",
        "down_detail": "Nothing loads without it — check PG_HOST and PG_PASSWORD",
        "probe": _postgres_up,
        LIVE: {
            "required": True,
            "on_down": FAILED,
        },
        EXPERIMENT: {
            "required": True,
            "on_down": FAILED,
        },
    },
    {
        "id": "clickhouse",
        "label": "Tracing data warehouse",
        "down_detail": "Traces, spans and dashboards will not load",
        "probe": _clickhouse_up,
        LIVE: {
            "required": True,
            "on_down": FAILED,
        },
        EXPERIMENT: {
            "required": True,
            "on_down": FAILED,
        },
    },
    {
        "id": "cache",
        "label": "Cache and session store",
        "down_detail": "Sessions, caching and rate limits will not work",
        "probe": _redis_up,
        LIVE: {
            "required": True,
            "on_down": FAILED,
        },
        EXPERIMENT: {
            "required": False,
            "on_down": WARNING,
        },
    },
    {
        "id": "broker",
        "label": "Websocket connection",
        "down_detail": "Live updates will not reach the browser",
        "probe": _rabbitmq_up,
        LIVE: {
            "required": True,
            "on_down": FAILED,
        },
        EXPERIMENT: {
            "required": False,
            "on_down": WARNING,
        },
    },
    {
        "id": "storage",
        "label": "Object storage service",
        "down_detail": "Dataset uploads, exports and media will fail",
        "probe": _object_storage_up,
        LIVE: {
            "required": True,
            "on_down": FAILED,
        },
        EXPERIMENT: {
            "required": False,
            "on_down": WARNING,
        },
    },
    {
        "id": "gateway",
        "label": "LLM request gateway",
        "down_detail": "Every LLM call fails — evaluations, playground and agents",
        "probe": _gateway_up,
        LIVE: {
            "required": True,
            "on_down": FAILED,
        },
        EXPERIMENT: {
            "required": True,
            "on_down": FAILED,
        },
    },
    {
        "id": "temporal",
        "label": "Async task engine",
        "down_detail": "Evaluations, optimizations and scheduled jobs will not run",
        "probe": _temporal_up,
        LIVE: {
            "required": True,
            "on_down": FAILED,
        },
        EXPERIMENT: {
            "required": True,
            "on_down": FAILED,
        },
    },
    {
        "id": "collector",
        "label": "Trace ingestion",
        "down_detail": "Spans sent by the SDK will not arrive",
        "probe": _collector_up,
        LIVE: {
            "required": True,
            "on_down": FAILED,
        },
        EXPERIMENT: {
            "required": True,
            "on_down": FAILED,
        },
    },
    {
        "id": "backend",
        "label": "Django backend",
        "probe": lambda: True,
        LIVE: {"required": True, "on_down": FAILED},
        EXPERIMENT: {"required": True, "on_down": FAILED},
    },
    {
        "id": "frontend",
        "label": "React frontend",
        "probe": lambda: True,
        LIVE: {"required": True, "on_down": FAILED},
        EXPERIMENT: {"required": True, "on_down": FAILED},
    },
    {
        "id": "model_serving",
        "label": "Agent fixer (evals + Error Feed)",
        "down_detail": "Built-in evaluations and guardrails will not run",
        "probe": _model_serving_up,
        LIVE: {
            "required": False,
            "on_down": WARNING,
        },
        EXPERIMENT: {
            "required": False,
            "on_down": SKIPPED,
        },
    },
    {
        "id": "code_executor",
        "label": "Code execution sandbox",
        "down_detail": "Custom code evaluations will not run",
        "probe": _code_executor_up,
        LIVE: {
            "required": False,
            "on_down": WARNING,
        },
        EXPERIMENT: {
            "required": False,
            "on_down": SKIPPED,
        },
    },
)


def _safe(probe) -> bool:
    """Fail closed. A probe that raises means the service is not usable, which is
    exactly what a down service looks like — never a 500 for the whole screen."""
    try:
        return bool(probe())
    except Exception:
        return False


def _run_probes() -> dict:
    """Probe every service, network calls concurrently.

    Serial probes would stack their timeouts: eleven services at 3s each is over
    30s on a fully down stack, far past the client's timeout. Postgres runs on
    this thread — it is local and fast, and keeping it here avoids opening a
    short-lived DB connection per worker thread.
    """
    results = {}

    concurrent = [c for c in CHECKS if c["id"] != "database" and c["probe"]]

    with ThreadPoolExecutor(max_workers=len(concurrent)) as pool:
        futures = {c["id"]: pool.submit(_safe, c["probe"]) for c in concurrent}
        results["database"] = _safe(_postgres_up)
        for check_id, future in futures.items():
            results[check_id] = future.result()

    return results


def _build_checks(mode: str, probe_results: dict) -> list:
    checks = []
    for check in CHECKS:
        overlay = check[mode]
        up = probe_results.get(check["id"], False)
        checks.append(
            {
                "id": check["id"],
                "label": check["label"],
                "status": PASSED if up else overlay["on_down"],
                "required": bool(overlay["required"]),
                "detail": "" if up else check.get("down_detail", ""),
            }
        )
    return checks


def _cached_snapshot(cache_key: str):
    """Redis is one of the services being probed, so the cache it backs cannot be
    a precondition for answering — a down Redis would 500 the very screen that
    exists to report it as down."""
    try:
        return cache.get(cache_key)
    except Exception:
        return None


def _store_snapshot(cache_key: str, result: dict) -> None:
    try:
        cache.set(cache_key, result, SNAPSHOT_TTL_SECONDS)
    except Exception:
        pass


class SetupChecksView(APIView):
    """Public infrastructure probe for the OSS first-run setup screen.

    Returns ``{"status": "ok"|"issues", "mode": ..., "checks": [...]}``. No auth —
    it runs before any account exists. Self-hosted only: on cloud and EE the
    route answers 404, so neither the internal service topology nor the outbound
    probes it triggers are reachable by an anonymous caller.
    """

    authentication_classes = []
    permission_classes = []

    @validated_request(
        responses={
            200: SetupChecksResponseSerializer,
            404: ApiTextErrorResponseSerializer,
            500: ApiTextErrorResponseSerializer,
        }
    )
    def get(self, request, *args, **kwargs):
        gm = GeneralMethods(request)

        if not is_oss():
            return gm.custom_error_response(status.HTTP_404_NOT_FOUND, "Not found.")

        mode = request.query_params.get("mode", LIVE)
        if mode not in (LIVE, EXPERIMENT):
            mode = LIVE

        # Cached per mode: the mode changes what the snapshot says, so the two
        # modes cannot share an entry.
        cache_key = f"setup-checks:{mode}"
        result = _cached_snapshot(cache_key)

        if result is None:
            checks = _build_checks(mode, _run_probes())
            blocked = any(c["required"] and c["status"] == FAILED for c in checks)
            result = {
                "status": "issues" if blocked else "ok",
                "mode": mode,
                "checks": checks,
            }
            _store_snapshot(cache_key, result)

        return gm.success_response(result)
