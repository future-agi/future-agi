"""Actor-aware customer analytics for self-hosted deployments.

The event API is intentionally small and content-free.  Events are written to
a durable local outbox before a best-effort background flush.  The existing
six-hour aggregate heartbeat remains the reconciliation/liveness signal.
"""

from __future__ import annotations

import hashlib
import threading
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import structlog
from django.db import close_old_connections
from django.utils import timezone

from tfc.deployment_telemetry.config import (
    detect_deployment_type,
    get_telemetry_buffer_dir,
    get_version,
    is_self_hosted_deployment,
    telemetry_is_disabled,
)
from tfc.deployment_telemetry.event_buffer import (
    delete_event,
    load_event,
    pending_events,
    prune_events,
    store_event,
)
from tfc.deployment_telemetry.models import DeploymentTelemetryState
from tfc.deployment_telemetry.state import get_or_create_telemetry_state
from tfc.deployment_telemetry.transport import TelemetryClient

logger = structlog.get_logger(__name__)

EVENT_ENDPOINT = "/telemetry/events/"
EVENT_BATCH_SIZE = 50
EVENT_SCHEMA_VERSION = 1
EVENT_NAMES = frozenset(
    {
        "deployment_booted",
        "telemetry_registered",
        "user_created",
        "user_logged_in",
        "api_key_created",
        "actor_request_completed",
        "first_project_created",
        "first_trace_received",
    }
)
ACTOR_TYPES = frozenset(
    {
        "human_user",
        "api_key",
        "service_account",
        "agent",
        "mcp_client",
        "anonymous",
        "system_worker",
    }
)
EVENT_SOURCES = frozenset(
    {"web", "api", "cli", "mcp", "sdk", "agent", "system", "unknown"}
)
EVENT_PROPERTY_KEYS = frozenset(
    {"method", "route", "status_code", "auth_method", "feature", "deployment_type", "version"}
)
_flush_lock = threading.Lock()
_flush_thread_lock = threading.Lock()
_flush_thread: threading.Thread | None = None
_instance_id_cache_lock = threading.Lock()
_instance_id_cache: tuple[str, UUID] | None = None


def pseudonymous_id(value: Any, prefix: str, instance_id: UUID | str | None = None) -> str:
    """Return a stable, install-local actor identifier without secrets/PII."""
    scope = str(instance_id) if instance_id is not None else "unregistered"
    digest = hashlib.sha256(f"{scope}:{value}".encode()).hexdigest()[:24]
    return f"{prefix}:{digest}"


def build_event(
    event_name: str,
    *,
    instance_id: UUID,
    actor_type: str = "system_worker",
    actor_id: str | None = None,
    source: str = "system",
    organization_id: str | int | None = None,
    workspace_id: str | int | None = None,
    operation_id: str | None = None,
    outcome: str = "success",
    duration_ms: float | None = None,
    properties: dict[str, Any] | None = None,
    occurred_at: datetime | None = None,
) -> dict:
    if event_name not in EVENT_NAMES:
        raise ValueError(f"unsupported deployment telemetry event: {event_name}")
    if actor_type not in ACTOR_TYPES:
        raise ValueError(f"unsupported actor_type: {actor_type}")
    if source not in EVENT_SOURCES:
        raise ValueError(f"unsupported event source: {source}")
    if not actor_id and actor_type != "anonymous":
        raise ValueError("actor_id is required for identified actors")

    safe_properties = properties or {}
    unknown = set(safe_properties) - EVENT_PROPERTY_KEYS
    if unknown:
        raise ValueError(f"unsupported event properties: {', '.join(sorted(unknown))}")
    for key, value in safe_properties.items():
        if isinstance(value, (dict, list, tuple, bytes)):
            raise ValueError(f"event property {key} must be scalar")
        if isinstance(value, str) and len(value) > 200:
            raise ValueError(f"event property {key} is too long")

    return {
        "schema_version": EVENT_SCHEMA_VERSION,
        "event_id": str(uuid4()),
        "instance_id": str(instance_id),
        "event_name": event_name,
        "occurred_at": (occurred_at or datetime.now(UTC)).astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "actor_type": actor_type,
        "actor_id": actor_id,
        "source": source,
        "organization_id": str(organization_id) if organization_id is not None else None,
        "workspace_id": str(workspace_id) if workspace_id is not None else None,
        "operation_id": operation_id,
        "version": get_version(),
        "deployment_type": detect_deployment_type(),
        "outcome": outcome,
        "duration_ms": duration_ms,
        "properties": safe_properties,
    }


def _store_event(event: dict) -> bool:
    try:
        store_event(event)
        return True
    except Exception:
        logger.warning("deployment_telemetry_event_enqueue_failed", exc_info=True)
        return False


def _flush_events() -> int:
    if telemetry_is_disabled() or not is_self_hosted_deployment():
        return 0
    with _flush_lock:
        state = DeploymentTelemetryState.objects.values("instance_id", "instance_secret").first()
        if not state or not state["instance_secret"]:
            return 0
        events = []
        paths = []
        for path in pending_events(EVENT_BATCH_SIZE):
            event = load_event(path)
            if event is None:
                delete_event(path)
                continue
            events.append(event)
            paths.append(path)
        if not events:
            return 0
        payload = {
            "schema_version": EVENT_SCHEMA_VERSION,
            "instance_id": str(state["instance_id"]),
            "events": events,
        }
        response = TelemetryClient(secret=state["instance_secret"]).post(
            EVENT_ENDPOINT, payload
        )
        if not response.ok:
            return 0
        for path in paths:
            delete_event(path)
        return len(paths)


def flush_events() -> int:
    """Flush pending events synchronously; useful for workers and tests."""
    if not pending_events(limit=1):
        return 0
    close_old_connections()
    try:
        prune_events()
        return _flush_events()
    except Exception:
        logger.warning("deployment_telemetry_event_flush_failed", exc_info=True)
        return 0
    finally:
        close_old_connections()


def _background_flush() -> None:
    global _flush_thread
    try:
        # Drain the current outbox so a registration event and the immediately
        # following signup event cannot strand one another behind one batch.
        for _ in range(10):
            if flush_events() == 0:
                break
    finally:
        with _flush_thread_lock:
            _flush_thread = None


def _schedule_flush() -> None:
    global _flush_thread
    with _flush_thread_lock:
        if _flush_thread and _flush_thread.is_alive():
            return
        _flush_thread = threading.Thread(
            target=_background_flush,
            name="deployment-telemetry-events",
            daemon=True,
        )
        _flush_thread.start()


def _get_cached_instance_id() -> UUID:
    """Resolve the install ID once per process instead of locking the DB per request."""
    global _instance_id_cache
    scope = str(get_telemetry_buffer_dir())
    cached = _instance_id_cache
    if cached is not None and cached[0] == scope:
        return cached[1]
    with _instance_id_cache_lock:
        cached = _instance_id_cache
        if cached is not None and cached[0] == scope:
            return cached[1]
        instance_id = get_or_create_telemetry_state().instance_id
        _instance_id_cache = (scope, instance_id)
        return instance_id


def record_event(event_name: str, **kwargs) -> bool:
    """Enqueue one event and trigger a non-blocking best-effort flush."""
    if telemetry_is_disabled() or not is_self_hosted_deployment():
        return False
    instance_id = _get_cached_instance_id()
    if kwargs.get("actor_id") and kwargs.get("actor_type") != "anonymous":
        # Callers provide the stable source identifier (user/API-key id), not
        # an email or credential. Scope it to this install before it leaves.
        kwargs["actor_id"] = pseudonymous_id(
            kwargs["actor_id"], kwargs.get("actor_type", "actor"), instance_id
        )
    event = build_event(event_name, instance_id=instance_id, **kwargs)
    stored = _store_event(event)
    if stored:
        _schedule_flush()
    return stored


def record_event_for_instance(instance_id: UUID, event_name: str, **kwargs) -> bool:
    """Queue lifecycle events during registration without recursive registration."""
    if telemetry_is_disabled() or not is_self_hosted_deployment():
        return False
    if kwargs.get("actor_id") and kwargs.get("actor_type") != "anonymous":
        kwargs["actor_id"] = pseudonymous_id(
            kwargs["actor_id"], kwargs.get("actor_type", "actor"), instance_id
        )
    stored = _store_event(build_event(event_name, instance_id=instance_id, **kwargs))
    if stored:
        _schedule_flush()
    return stored


def record_request_event(request, response, duration_ms: float) -> bool:
    """Record an authenticated/anonymous request without reading its body."""
    if telemetry_is_disabled() or not is_self_hosted_deployment():
        return False
    path = getattr(request, "path", "") or ""
    if path.startswith(("/telemetry/", "/health", "/ready", "/static/", "/admin/", "/favicon.ico")):
        return False

    api_key = getattr(request, "org_api_key", None)
    user = getattr(request, "user", None)
    is_authenticated = bool(user and getattr(user, "is_authenticated", False))
    agent_header = (getattr(request, "META", {}) or {}).get("HTTP_X_AGENT_ID")
    mcp_client_header = (getattr(request, "META", {}) or {}).get("HTTP_X_MCP_CLIENT")
    if agent_header:
        actor_type = "agent"
        actor_value = agent_header
        auth_method = "agent_header"
    elif mcp_client_header:
        actor_type = "mcp_client"
        actor_value = mcp_client_header
        auth_method = "mcp_client_header"
    elif api_key is not None:
        actor_type = "api_key"
        actor_value = getattr(api_key, "id", None) or "unknown"
        auth_method = "api_key"
    elif is_authenticated:
        actor_type = "human_user"
        actor_value = getattr(user, "id", "unknown")
        auth_method = "session_or_token"
    else:
        actor_type = "anonymous"
        actor_value = None
        auth_method = "anonymous"

    # Anonymous page loads and health/static traffic are not useful sales
    # signals and would turn every browser visit into an fsync. Authenticated
    # API/CLI/MCP requests remain losslessly queued.
    if actor_type == "anonymous":
        return False

    source = "api"
    user_agent = (getattr(request, "META", {}) or {}).get("HTTP_USER_AGENT", "").lower()
    if path.startswith("/mcp") or "mcp" in user_agent:
        source = "mcp"
    elif "cli" in user_agent:
        source = "cli"

    organization = getattr(request, "organization", None)
    workspace = getattr(request, "workspace", None)
    resolver_match = getattr(request, "resolver_match", None)
    route = (
        getattr(resolver_match, "route", None)
        or getattr(resolver_match, "url_name", None)
        or "unresolved"
    )
    return record_event(
        "actor_request_completed",
        actor_type=actor_type,
        actor_id=actor_value if actor_value else None,
        source=source,
        organization_id=getattr(organization, "id", None),
        workspace_id=getattr(workspace, "id", None),
        outcome="success" if getattr(response, "status_code", 500) < 400 else "error",
        duration_ms=duration_ms,
        properties={
            "method": getattr(request, "method", ""),
            "route": str(route)[:200],
            "status_code": getattr(response, "status_code", None),
            "auth_method": auth_method,
        },
    )


def queue_boot_event() -> bool:
    """Queue the first boot marker even before the first account exists."""
    if telemetry_is_disabled() or not is_self_hosted_deployment():
        return False
    return _queue_boot_event_for_instance(get_or_create_telemetry_state().instance_id)


def _queue_boot_event_for_instance(instance_id: UUID) -> bool:
    """Persist the boot event before marking it complete.

    The outbox write can fail independently of the database (for example when
    a mounted volume is read-only). Marking the database first would then make
    the one-time boot event unrecoverable. Holding the row lock across the
    outbox write keeps concurrent schedules from duplicating it while allowing
    a later attempt to retry after an outbox failure.
    """
    from django.db import transaction

    with transaction.atomic():
        state = DeploymentTelemetryState.objects.select_for_update().get(
            instance_id=instance_id
        )
        if state.boot_event_at is not None:
            return False
        if not record_event_for_instance(
            instance_id,
            "deployment_booted",
            actor_type="system_worker",
            actor_id=f"instance:{instance_id}",
            source="system",
        ):
            return False
        state.boot_event_at = timezone.now()
        state.save(update_fields=["boot_event_at", "updated_at"])
        return True
