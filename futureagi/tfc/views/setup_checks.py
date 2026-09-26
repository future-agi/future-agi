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

``fix`` and ``docs_url`` sit beside it, under the same rule. ``down_detail``
says what breaks; ``fix`` is the one line that gets the operator moving again,
and it stays one line, because the row above it already carries the
consequence. Both are blank on a passing check, since a healthy service has no
remedy to offer. Every check carries them, not only the ones that happen to
fail on a laptop, or the next check added here reintroduces the dead end this
pair exists to close.

This endpoint only reports. It never starts or stops anything — bringing model
serving up or down stays an operator decision. Model serving is an add-on the
standalone install does not run (it starts with the ``ml`` compose profile), and
everything that needs it degrades on its own. So a probe may also answer
``ABSENT``: the service is not deployed at all (for serving, an empty
``MODEL_SERVING_URL``, or in the standalone install a host that does not
resolve). An absent service
reports SKIPPED in both modes, with its own detail and fix, rather than warning
about a choice the operator already made. A serving that is deployed but fails
its health check is a broken install and is reported like any other check.

That is the shape of the disagreement between the modes. Live requires every
check. Experiment requires only the seven that are interdependent enough that
nothing works without them — the application database, the tracing warehouse,
the LLM gateway, the async task engine, trace ingestion, and the backend and
frontend themselves. The rest are feature-level there: a capability is lost,
the application still runs, and the row warns instead of blocking. SSL goes one
further and reports SKIPPED in experiment, because a local stack is not
expected to hold a certificate at all.

SSL is also ABSENT, in both modes, on a local install: every public URL
(``FRONTEND_URL``, and ``VITE_HOST_API`` or else ``BASE_URL``) names
localhost, a private network address or a single-label host, and the browser
reached the API on such a host. Nobody outside the machine or the private
network can reach it, so there is no certificate to hold, and Production mode
is green on a laptop. A public host keeps the check strict.

The response names the setup this API runs in: ``standalone`` (one ``app``
container; its API runs the Temporal worker in-process), ``distributed`` (one
container per service) or ``helm`` (one pod per service, from the Helm chart).
A ``fix`` that differs between them is written per setup, and the screen gets
only the one that applies. It also carries ``collector_http_url``, the
OTLP/HTTP endpoint an SDK outside the stack sends traces to
(FI_COLLECTOR_PUBLIC_URL).
"""

import ipaddress
import os
import socket
import ssl
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from urllib.parse import urlparse, urlsplit

import boto3
import redis
import requests
from botocore.config import Config
from botocore.exceptions import ClientError
from django.core.cache import cache
from django.db import connections
from rest_framework import status
from rest_framework.views import APIView

from agentic_eval.core.embeddings.serving_client import (
    serving_available,
    serving_base_url,
)
from model_hub.utils import is_forbidden_vendor_endpoint
from tfc.ee_gating import is_oss
from tfc.settings import settings
from tfc.temporal import TEMPORAL_HOST
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import (
    ApiTextErrorResponseSerializer,
    SetupChecksResponseSerializer,
)
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.install_setup import (
    DISTRIBUTED,
    HELM,
    STANDALONE,
    current_setup,
    helm_deployment,
    helm_namespace,
)

PASSED = "passed"
WARNING = "warning"
FAILED = "failed"
SKIPPED = "skipped"

# Probe verdict for a service this deployment does not run at all; see the
# check's ``absent`` block.
ABSENT = "absent"

LIVE = "live"
EXPERIMENT = "experiment"
PROBE_TIMEOUT_SECONDS = 3

# Host names only this machine or a private network can resolve.
_LOCAL_HOSTNAMES = frozenset(
    {"localhost", "host.docker.internal", "gateway.docker.internal"}
)
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")
# Carrier-grade NAT; also the address range of Tailscale and similar overlays.
_SHARED_ADDRESS_SPACE = ipaddress.ip_network("100.64.0.0/10")

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


def _websocket_relay_up() -> bool:
    """Temporal workers push live updates by POSTing to WEBSOCKET_ENDPOINT
    (model_hub.utils.call_websocket), whichever channel layer is configured.
    One that does not reach this install's backend drops them silently. A
    futureagi.com endpoint is refused without being contacted, as the relay
    itself refuses it."""
    url = settings.WEBSOCKET_ENDPOINT
    if is_forbidden_vendor_endpoint(url):
        return False
    parsed = urlparse(url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return _tcp_up(parsed.hostname or url, port)


def _channel_layer_up() -> bool:
    """The Channels layer that carries live updates to the browser, plus the
    worker relay that feeds it. Only the rabbitmq layer has a broker of its own;
    the memory layer lives in this process, so answering proves it is up."""
    layer = settings.CHANNEL_LAYERS["default"]["CONFIG"]
    if settings.CHANNEL_LAYER_BACKEND == "rabbitmq":
        layer_up = _tcp_up(*_host_port(layer["host"], 5672))
    elif settings.CHANNEL_LAYER_BACKEND == "redis":
        client = redis.from_url(
            layer["hosts"][0],
            socket_connect_timeout=PROBE_TIMEOUT_SECONDS,
            socket_timeout=PROBE_TIMEOUT_SECONDS,
        )
        try:
            layer_up = bool(client.ping())
        finally:
            client.close()
    else:
        layer_up = True
    return layer_up and _websocket_relay_up()


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
        # An answer of any kind means the service is reachable, including the 404
        # a fresh install gets: the bucket is created on the first upload.
        code = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        return code is not None
    return True


def _gateway_up() -> bool:
    base = os.environ.get("AGENTCC_INTERNAL_URL", "http://agentcc-gateway:8080")
    return _http_ok(f"{base.rstrip('/')}/healthz")


def _collector_up() -> bool:
    host = os.environ.get("FI_COLLECTOR_HOST", "fi-collector")
    port = int(os.environ.get("FI_COLLECTOR_OTLP_PORT", "4317"))
    return _tcp_up(host, port)


def _code_executor_up() -> bool:
    """The standalone install runs the executor inside the app container, so
    ``CODE_EXECUTOR_URL`` points at loopback there; the distributed install and the
    ``sandbox`` profile run it as the ``code-executor`` container. Both serve
    the same ``/health``."""
    base = os.environ.get("CODE_EXECUTOR_URL", "http://code-executor:8060")
    if not base.strip():
        # Not deployed: the Helm chart with codeExecutor.enabled=false.
        return ABSENT
    return _http_ok(f"{base.rstrip('/')}/health")


def _resolves(host: str) -> bool:
    try:
        socket.getaddrinfo(host, None)
    except OSError:
        return False
    return True


def _setup() -> str:
    """``standalone``, ``distributed`` or ``helm``; see
    tfc.utils.install_setup.current_setup."""
    return current_setup()


def _serving_is_opt_in() -> bool:
    """Only the standalone install runs serving as an opt-in (the ``ml``
    profile). In the distributed install a ``serving`` host that does not
    resolve is a stopped or crash-looping container, not a choice."""
    return _setup() == STANDALONE


def _model_serving_up():
    """``ABSENT`` when serving is not deployed: ``MODEL_SERVING_URL`` is empty,
    or, in the standalone install, its host does not resolve (no ``ml``
    profile). Otherwise the same probe the embedding features gate on, so
    this row and their behaviour cannot disagree. Uncached: the screen polls
    while it starts."""
    base = serving_base_url()
    host = urlparse(base).hostname if base else None
    if not host or (_serving_is_opt_in() and not _resolves(host)):
        return ABSENT
    return serving_available(base, use_cache=False)


def _tls_verified(url: str) -> bool:
    """Complete a handshake against the public endpoint with the default trust
    store, which validates the chain, the hostname and the expiry in one go.
    Anything not served over ``https`` is plaintext and cannot pass."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    context = ssl.create_default_context()
    with socket.create_connection(
        (parsed.hostname, parsed.port or 443), PROBE_TIMEOUT_SECONDS
    ) as sock:
        with context.wrap_socket(sock, server_hostname=parsed.hostname) as tls_sock:
            return bool(tls_sock.getpeercert())


def _is_local_host(host) -> bool:
    """A host only this machine or a private network reaches: localhost, a
    loopback, private, link-local or shared (CGNAT/Tailscale) address, a
    single-label name (``app``, ``backend``) or an mDNS/intranet suffix.
    Anything else, a public IP or a dotted DNS name, is public."""
    host = (host or "").strip().strip("[]").rstrip(".").lower()
    if not host:
        return False
    if host in _LOCAL_HOSTNAMES or host.endswith(_LOCAL_SUFFIXES):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # Not an address: a name without a dot cannot be a public DNS name.
        return "." not in host
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped
    return (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or (address.version == 4 and address in _SHARED_ADDRESS_SPACE)
    )


def _url_host(url: str):
    """Host of a URL or a bare ``host[:port]``; None when there is none."""
    url = (url or "").strip()
    try:
        return urlsplit(url if "://" in url else f"//{url}").hostname
    except ValueError:
        return None


def _request_host(request):
    """The host the browser reached this API on (the SPA calls it directly).
    Raw header, not ``get_host()``: this only decides how SSL is reported, and
    a host outside ALLOWED_HOSTS must not 500 the screen."""
    return _url_host(request.META.get("HTTP_HOST", ""))


def _reached_locally(request_host) -> bool:
    """Unknown (no Host header) counts as local, like an unset public URL."""
    return request_host is None or _is_local_host(request_host)


def _public_urls() -> list:
    """The UI URL and the API URL the browser and the SDK are handed. The
    Helm chart gives the backend no VITE_HOST_API (only the frontend pod has
    it), so the API URL falls back to BASE_URL (``urls.api``)."""
    api = os.environ.get("VITE_HOST_API") or os.environ.get("BASE_URL")
    return [
        url.strip()
        for url in (os.environ.get("FRONTEND_URL"), api)
        if url and url.strip()
    ]


def _tls_up(request_host=None):
    """The public URLs are the only TLS the backend can observe — it serves
    plaintext behind the proxy, so what is checkable is whether the endpoints the
    browser and the SDK are handed terminate a valid certificate. Both have to,
    since either one left plaintext is traffic in the clear.

    ``ABSENT`` on a local install: no configured URL names a public host and
    the browser reached the API on a local host (``request_host``; unknown
    counts as local). An empty ``VITE_HOST_API`` makes the SPA call
    http://localhost:8000, which only a browser on the same machine can reach.

    Otherwise the public URLs must all verify, and a browser that came in on
    a public host with none configured counts as down: the Helm chart defaults
    FRONTEND_URL to http://localhost:3000, so a URL naming localhost does not
    prove the install is only reachable locally. Such a deployment is serving
    its UI and its ingest unencrypted, which is the thing this check exists to
    surface.
    """
    public = [url for url in _public_urls() if not _is_local_host(_url_host(url))]
    if not public:
        return ABSENT if _reached_locally(request_host) else False
    return all(_tls_verified(url) for url in public)


def _same_on_compose(compose: str, helm: str) -> dict:
    """A fix that is one command in both compose setups and another on Helm."""
    return {STANDALONE: compose, DISTRIBUTED: compose, HELM: helm}


# Helm fixes name the chart's objects: the chart passes its namespace and
# object-name prefix, so these read e.g. `kubectl -n futureagi logs
# deploy/futureagi-backend` (tfc.utils.install_setup.helm_deployment).
_HELM_PODS = f"kubectl -n {helm_namespace()} get pods"


def _helm_logs(component: str) -> str:
    return f"Check `kubectl -n {helm_namespace()} logs {helm_deployment(component)}`."


def _helm_datastore(values_key: str, what: str = "server") -> str:
    return (
        f"Bundled: check `{_HELM_PODS}`. Your own {what}: check "
        f"`{values_key}` in your values."
    )


# Every docs_url is a heading of INSTALLATION.md (see the test for it).
_PREFLIGHT_DOCS = (
    "https://github.com/future-agi/future-agi/blob/dev/INSTALLATION.md#pre-flight-says-"
)

CHECKS = (
    {
        "id": "database",
        "label": "Core application database",
        "down_detail": "Nothing loads without it — check PG_HOST and PG_PASSWORD",
        "probe": _postgres_up,
        "fix": _same_on_compose(
            "Start it: `docker compose up -d postgres`. Check `PG_HOST` and `PG_PASSWORD` in `.env`.",
            _helm_datastore("postgres.external"),
        ),
        "docs_url": _PREFLIGHT_DOCS + "core-application-database-failed",
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
        "fix": _same_on_compose(
            "Start it: `docker compose up -d clickhouse`, then check `docker compose logs clickhouse`.",
            _helm_datastore("clickhouse.external"),
        ),
        "docs_url": _PREFLIGHT_DOCS + "tracing-data-warehouse-failed",
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
        "fix": {
            STANDALONE: "Redis runs inside `app`: `docker compose restart app`.",
            DISTRIBUTED: "Start it: `docker compose up -d redis`.",
            HELM: _helm_datastore("redis.external"),
        },
        "docs_url": _PREFLIGHT_DOCS + "cache-and-session-store-failed",
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
        "probe": _channel_layer_up,
        "fix": {
            STANDALONE: "It runs inside `app`: `docker compose restart app`, then check `docker compose logs app`.",
            DISTRIBUTED: "Check `CHANNEL_LAYER_BACKEND` and `WEBSOCKET_ENDPOINT` in `.env`, and that Redis is up.",
            HELM: f"Check that Redis is up, then `kubectl -n {helm_namespace()} logs {helm_deployment('backend')}`.",
        },
        "docs_url": _PREFLIGHT_DOCS + "websocket-connection-failed",
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
        "fix": {
            STANDALONE: "Object storage runs inside `app`: `docker compose restart app`.",
            DISTRIBUTED: "Start it: `docker compose up -d minio`.",
            HELM: _helm_datastore("objectStorage.external", "service"),
        },
        "docs_url": _PREFLIGHT_DOCS + "object-storage-service-failed",
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
        "fix": {
            STANDALONE: "The gateway runs inside `app`: `docker compose restart app`, then check `docker compose logs app`.",
            DISTRIBUTED: "Start it: `docker compose up -d agentcc-gateway`.",
            HELM: _helm_logs("agentcc-gateway"),
        },
        "docs_url": _PREFLIGHT_DOCS + "llm-request-gateway-failed",
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
        "fix": {
            STANDALONE: "Temporal runs inside `app`: `docker compose restart app`.",
            DISTRIBUTED: "Start it: `docker compose up -d temporal`.",
            HELM: _helm_datastore("temporal.external.address", "cluster"),
        },
        "docs_url": _PREFLIGHT_DOCS + "async-task-engine-failed",
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
        "fix": {
            STANDALONE: "The collector runs inside `app`: `docker compose restart app`.",
            DISTRIBUTED: "Start it: `docker compose up -d fi-collector`.",
            HELM: _helm_logs("fi-collector"),
        },
        "docs_url": _PREFLIGHT_DOCS + "trace-ingestion-failed",
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
        "fix": {
            STANDALONE: "Start it: `docker compose up -d app`.",
            DISTRIBUTED: "Start it: `docker compose up -d backend`.",
            HELM: _helm_logs("backend"),
        },
        "docs_url": _PREFLIGHT_DOCS + "django-backend-failed",
        LIVE: {"required": True, "on_down": FAILED},
        EXPERIMENT: {"required": True, "on_down": FAILED},
    },
    {
        "id": "frontend",
        "label": "React frontend",
        "probe": lambda: True,
        "fix": {
            STANDALONE: "Start it: `docker compose up -d app`.",
            DISTRIBUTED: "Start it: `docker compose up -d frontend`.",
            HELM: _helm_logs("frontend"),
        },
        "docs_url": _PREFLIGHT_DOCS + "react-frontend-failed",
        LIVE: {"required": True, "on_down": FAILED},
        EXPERIMENT: {"required": True, "on_down": FAILED},
    },
    {
        "id": "model_serving",
        "label": "Agent fixer (evals + Error Feed)",
        "down_detail": (
            "Embedding-based evals, ground truth, Vector DB columns and Error "
            "Feed clustering will not run"
        ),
        "probe": _model_serving_up,
        "fix": _same_on_compose(
            "Start it: `docker compose up -d serving`, then check `docker compose logs serving`.",
            _helm_logs("serving"),
        ),
        "docs_url": _PREFLIGHT_DOCS + "agent-fixer-failed",
        # Not deployed at all: the standalone install without the `ml` profile,
        # or an empty MODEL_SERVING_URL. SKIPPED and never required, in either
        # mode.
        "absent": {
            "detail": (
                "Embedding-based evals, ground truth, Vector DB columns and "
                "Error Feed clustering are off; everything else works"
            ),
            "fix": {
                STANDALONE: "Optional. Turn it on with the `ml` profile: `docker compose --profile ml up -d`.",
                DISTRIBUTED: "Optional. `MODEL_SERVING_URL` is empty; point it at the serving service to turn it on.",
                HELM: "Optional. Turn it on with `serving.enabled=true` in your values.",
            },
        },
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
        "id": "code_executor",
        "label": "Code execution sandbox",
        "down_detail": "Custom code evaluations will not run",
        "probe": _code_executor_up,
        "fix": {
            STANDALONE: (
                "It runs inside `app`: `docker compose restart app`. With "
                "`COMPOSE_PROFILES=sandbox`, run `docker compose up -d code-executor` "
                "(the host must allow `privileged: true`)."
            ),
            DISTRIBUTED: (
                "Start it: `docker compose up -d code-executor`. The host must "
                "allow `privileged: true`."
            ),
            HELM: (
                "Turn it on with `codeExecutor.enabled=true` in your values (the "
                "nodes must allow `privileged: true`), then check "
                f"`kubectl -n {helm_namespace()} logs {helm_deployment('code-executor')}`."
            ),
        },
        "docs_url": _PREFLIGHT_DOCS + "code-execution-sandbox-failed",
        # Not deployed (empty CODE_EXECUTOR_URL): custom code evals are off or
        # run in the workers (CODE_EXECUTOR_LOCAL_FALLBACK); never blocking.
        "absent": {
            "detail": "Custom code evaluations are off; every other eval works",
            "fix": {
                STANDALONE: "Optional. `CODE_EXECUTOR_URL` is empty; remove it from `.env` to use the built-in sandbox.",
                DISTRIBUTED: "Optional. `CODE_EXECUTOR_URL` is empty; point it at the code-executor service to turn it on.",
                HELM: (
                    "Optional. Turn it on with `codeExecutor.enabled=true` (the nodes must "
                    "allow `privileged: true`), or, if everyone who writes evals is trusted, "
                    "run them in the workers with `codeExecutor.localFallback=true`."
                ),
            },
        },
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
        "id": "ssl",
        "label": "SSL/TLS certificate",
        "down_detail": "Browser and SDK traffic travels unencrypted",
        "probe": _tls_up,
        # The probe also needs the host the browser came in on (_tls_up).
        "takes_request_host": True,
        "fix": _same_on_compose(
            "Serve the UI and API over https (a reverse proxy such as Caddy or "
            "nginx), then set `FRONTEND_URL` and `VITE_HOST_API` to those https URLs.",
            "Serve the UI and API over https (an ingress with a TLS certificate), "
            "then set `urls.app` and `urls.api` in your values to those https URLs.",
        ),
        "docs_url": _PREFLIGHT_DOCS + "ssltls-certificate-failed",
        # A local install: only this machine or a private network reaches it.
        "absent": {
            "detail": (
                "Not needed on a local install: it is only reachable from this "
                "machine or a private network"
            ),
            "fix": _same_on_compose(
                "Before you expose it to the internet, serve it over https and "
                "set `FRONTEND_URL` and `VITE_HOST_API` to the https URLs.",
                "Before you expose it to the internet, serve it over https and "
                "set `urls.app` and `urls.api` in your values to the https URLs.",
            ),
        },
        LIVE: {
            "required": True,
            "on_down": FAILED,
        },
        EXPERIMENT: {
            "required": False,
            "on_down": SKIPPED,
        },
    },
)


def _safe(probe):
    """Fail closed. A probe that raises means the service is not usable, which is
    exactly what a down service looks like — never a 500 for the whole screen.
    ``ABSENT`` passes through; anything else is up or down."""
    try:
        result = probe()
    except Exception:
        return False
    return ABSENT if result == ABSENT else bool(result)


def _run_probes(request_host=None) -> dict:
    """Probe every service, network calls concurrently.

    Serial probes would stack their timeouts: eleven services at 3s each is over
    30s on a fully down stack, far past the client's timeout. Postgres runs on
    this thread — it is local and fast, and keeping it here avoids opening a
    short-lived DB connection per worker thread.

    ``request_host`` reaches only the probes that declare ``takes_request_host``.
    """
    results = {}

    concurrent = [c for c in CHECKS if c["id"] != "database" and c["probe"]]

    def probe_of(check):
        if check.get("takes_request_host"):
            return partial(check["probe"], request_host)
        return check["probe"]

    with ThreadPoolExecutor(max_workers=len(concurrent)) as pool:
        futures = {c["id"]: pool.submit(_safe, probe_of(c)) for c in concurrent}
        results["database"] = _safe(_postgres_up)
        for check_id, future in futures.items():
            results[check_id] = future.result()

    return results


def _for_setup(text, setup: str) -> str:
    """A ``fix`` is one string, or one per setup when the remedy differs."""
    if isinstance(text, dict):
        return text.get(setup) or text.get(DISTRIBUTED, "")
    return text or ""


def _build_checks(mode: str, probe_results: dict, setup: str = DISTRIBUTED) -> list:
    checks = []
    for check in CHECKS:
        overlay = check[mode]
        result = probe_results.get(check["id"], False)
        absent = check.get("absent") if result == ABSENT else None
        if absent is not None:
            # Not deployed, or not needed here: never a failure, never blocking.
            checks.append(
                {
                    "id": check["id"],
                    "label": check["label"],
                    "status": SKIPPED,
                    "required": False,
                    "detail": absent["detail"],
                    "fix": _for_setup(absent["fix"], setup),
                    "docs_url": check.get("docs_url", ""),
                }
            )
            continue
        # A check with no ``absent`` block cannot be optional: ABSENT is down.
        up = result != ABSENT and bool(result)
        checks.append(
            {
                "id": check["id"],
                "label": check["label"],
                "status": PASSED if up else overlay["on_down"],
                "required": bool(overlay["required"]),
                "detail": "" if up else check.get("down_detail", ""),
                # Only a down check needs a remedy; a passing row would render an
                # instruction for a problem the operator does not have.
                "fix": "" if up else _for_setup(check.get("fix"), setup),
                "docs_url": "" if up else check.get("docs_url", ""),
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

    Returns ``{"status": "ok"|"issues", "mode": ..., "setup":
    "standalone"|"distributed"|"helm", "collector_http_url": ..., "checks":
    [...]}``. No auth — it runs before any account exists. Self-hosted only:
    on cloud and EE the route answers 404, so neither the internal service
    topology nor the outbound probes it triggers are reachable by an
    anonymous caller.
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

        # Cached per mode, and per whether the browser came in on a local
        # host: both change what the snapshot says (the SSL row), so neither
        # may share an entry.
        request_host = _request_host(request)
        reach = "local" if _reached_locally(request_host) else "remote"
        cache_key = f"setup-checks:{mode}:{reach}"
        result = _cached_snapshot(cache_key)

        if result is None:
            setup = _setup()
            checks = _build_checks(mode, _run_probes(request_host), setup)
            blocked = any(c["required"] and c["status"] == FAILED for c in checks)
            result = {
                "status": "issues" if blocked else "ok",
                "mode": mode,
                "setup": setup,
                "collector_http_url": settings.FI_COLLECTOR_PUBLIC_URL,
                "checks": checks,
            }
            _store_snapshot(cache_key, result)

        return gm.success_response(result)
