"""SQL budgets are deployment settings, validated again by their consumers."""

import os

from django.core.exceptions import ImproperlyConfigured


def _integer(
    name: str, default: int | None, minimum: int, maximum: int | None = None
) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ImproperlyConfigured(f"{name} must be an integer.") from None
    if value < minimum or (maximum is not None and value > maximum):
        raise ImproperlyConfigured(f"{name} is outside its supported range.")
    return value


SQL_WORKBENCH_RESULT_CACHE_SECONDS = _integer(
    "SQL_WORKBENCH_RESULT_CACHE_SECONDS", 60, 0, 3600
)
SQL_WORKBENCH_MAX_ACTOR_QUERIES_24H = _integer(
    "SQL_WORKBENCH_MAX_ACTOR_QUERIES_24H", 1500, 1
)
SQL_WORKBENCH_MAX_ORG_QUERIES_24H = _integer(
    "SQL_WORKBENCH_MAX_ORG_QUERIES_24H", 30000, 1
)
SQL_WORKBENCH_MAX_ACTOR_RESULT_BYTES_24H = _integer(
    "SQL_WORKBENCH_MAX_ACTOR_RESULT_BYTES_24H", 134217728, 1
)
SQL_WORKBENCH_MAX_ORG_RESULT_BYTES_24H = _integer(
    "SQL_WORKBENCH_MAX_ORG_RESULT_BYTES_24H", 536870912, 1
)
SQL_WORKBENCH_ESTIMATES_PER_MINUTE = _integer(
    "SQL_WORKBENCH_ESTIMATES_PER_MINUTE", 30, 1
)
SQL_WORKBENCH_AUDIT_RETENTION_DAYS = _integer(
    "SQL_WORKBENCH_AUDIT_RETENTION_DAYS", None, 1
)
