from functools import wraps

import structlog
from django.db import connections
from django.db.utils import OperationalError
from django.http import JsonResponse

from tfc.utils.api_errors import build_error_envelope

logger = structlog.get_logger(__name__)

# Aliases whose failure should 503 the request. We intentionally probe
# ONLY these on the request path. Other aliases (replica, default_direct,
# product DBs we may add later) are NOT probed here for two reasons:
#   1. probing the replica on every protected request adds latency to
#      every such request, even when the replica is healthy;
#   2. a brief replica blip would 503 requests that don't even read from
#      replica.
# Health of non-critical aliases should be monitored out-of-band by the
# database/platform layer (for example Aurora CloudWatch ReplicaLag).
#
# Operational note: the structured-log event names emitted below
# ("database_connection_failed_critical") replace an earlier f-string
# format. Any alerts / log searches keyed on the old format must be
# migrated.
_CRITICAL_DB_ALIASES = ("default",)


def check_db_connection():
    """Check if the critical database connections are alive.

    Only probes aliases in `_CRITICAL_DB_ALIASES` (currently: `default`).
    A failure here returns False (→ 503 from the decorator). Non-critical
    aliases (`replica`, `default_direct`) are intentionally NOT probed on
    the request path. Monitor their health out-of-band in the
    database/platform layer.

    Consequence for routed endpoints: if `READ_REPLICA_OPT_IN` is enabled
    and the replica is down, a routed view's `.using("replica")` query will
    fail at execution time. The view's own exception handler decides the
    response status (often 400 or 500), NOT this middleware. Mitigation:
    flip `READ_REPLICA_OPT_IN=""` and restart workers to disable routing
    globally; or add an explicit `try/except OperationalError -> retry on
    default` for the affected endpoint.
    """
    all_ok = True
    for db_name in _CRITICAL_DB_ALIASES:
        if db_name not in connections.databases:
            continue
        try:
            c = connections[db_name].cursor()
            c.execute("SELECT 1")
            c.fetchone()
            c.close()
        except OperationalError as e:
            logger.error(
                "database_connection_failed_critical",
                db=db_name,
                error=str(e),
            )
            all_ok = False
    return all_ok


def db_connection_required(view_func):
    """Decorator to check DB connection before view execution"""

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not check_db_connection():
            return JsonResponse(
                build_error_envelope(
                    "Database connection error",
                    status_code=503,
                    code="service_unavailable",
                ),
                status=503,
            )
        return view_func(request, *args, **kwargs)

    return wrapper
