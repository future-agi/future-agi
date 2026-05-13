from functools import wraps

import structlog
from django.db import connections
from django.db.utils import OperationalError
from django.http import JsonResponse

logger = structlog.get_logger(__name__)

# Aliases whose failure should 503 the request. Everything else (replica,
# default_direct, product DBs we may add later) is best-effort — a transient
# outage there should NOT block requests that don't read from it.
#
# Previously this middleware iterated every entry in `connections` and
# treated any failure as fatal. With the read replica configured, a brief
# replica blip would 503 requests that don't even read from replica — see
# `internal-docs/design/do-and-do-not.md` for the rule.
#
# Operational note: the structured-log event names emitted below
# ("database_connection_failed_critical") replace an earlier f-string
# format. Any alerts / log searches keyed on the old format must be
# migrated.
_CRITICAL_DB_ALIASES = ("default",)


def check_db_connection():
    """Check if the critical database connections are alive.

    Only probes aliases in `_CRITICAL_DB_ALIASES`. A failure here returns
    False (→ 503 from the decorator). Non-critical aliases (`replica`,
    `default_direct`) are intentionally NOT probed on the request path —
    a slow / down replica would otherwise add probe latency to every
    request protected by `@db_connection_required`. Health of non-critical
    aliases is monitored out-of-band via `tfc.telemetry.replica_lag` and
    similar.
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
                {"error": "Database connection error", "status": "service_unavailable"},
                status=503,
            )
        return view_func(request, *args, **kwargs)

    return wrapper
