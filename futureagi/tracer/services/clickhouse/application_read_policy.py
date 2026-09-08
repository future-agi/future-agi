"""Resource policy for application analytics, not maintenance/diagnostic reads.

Latency objectives are measurements, not per-statement abort thresholds. SQL
pagination, memory safety, spilling and concurrency remain independent controls.
The context marker prevents a pooled native client from leaking this policy into
catalog maintenance or a subsequent bounded diagnostic read.
"""

from contextlib import contextmanager
from contextvars import ContextVar

_application_read = ContextVar("application_analytics_read", default=False)

# Explicit zeros also override inherited user-profile defaults. A locked server
# profile cannot be overridden and must be reported separately at qualification.
UNLIMITED_STATEMENT_SETTINGS = (
    "max_execution_time",
    "max_execution_time_leaf",
    "max_estimated_execution_time",
    "min_execution_speed",
    "min_execution_speed_bytes",
    "max_rows_to_read",
    "max_bytes_to_read",
    "max_rows_to_read_leaf",
    "max_bytes_to_read_leaf",
    "max_result_rows",
    "max_result_bytes",
    "max_rows_to_group_by",
    "max_rows_in_distinct",
    "max_bytes_in_distinct",
    "max_rows_in_set",
    "max_bytes_in_set",
    "max_rows_in_join",
    "max_bytes_in_join",
    "max_rows_to_transfer",
    "max_bytes_to_transfer",
)


def application_read_settings(settings: dict | None = None) -> dict:
    """Drop statement abort caps while retaining finite, configurable memory.

    Smaller explicit memory budgets remain meaningful, especially for optional
    probes. A zero/negative memory request must never disable the global safety cap.
    Standalone v2 readers retain the same defaults without Django configuration.
    """
    from django.conf import settings as django_settings

    def configured(name, fallback):
        return (
            getattr(django_settings, name, fallback)
            if django_settings.configured
            else fallback
        )

    result = dict(settings or {})
    ceiling = int(
        configured("CLICKHOUSE_APPLICATION_READ_MAX_MEMORY_BYTES", 36 * 1024**3)
    )
    if ceiling <= 0:
        raise ValueError("Application read memory ceiling must be positive")
    memory = int(result.get("max_memory_usage", 0) or 0)
    result["max_memory_usage"] = ceiling if memory <= 0 else min(memory, ceiling)
    threads = int(result.get("max_threads", 0) or 0)
    default_threads = int(configured("CLICKHOUSE_APPLICATION_READ_DEFAULT_THREADS", 4))
    max_threads = int(configured("CLICKHOUSE_APPLICATION_READ_MAX_THREADS", 8))
    result["max_threads"] = (
        default_threads if threads <= 0 else min(threads, max_threads)
    )
    result.update(dict.fromkeys(UNLIMITED_STATEMENT_SETTINGS, 0))
    result.update(
        readonly=2,
        read_overflow_mode="throw",
        result_overflow_mode="throw",
        timeout_overflow_mode="throw",
    )
    return result


def is_application_read() -> bool:
    return _application_read.get()


def supports_bounded_speculative_reads(executor) -> bool:
    """Whether a speculative query's tighter abort guards really reach CH.

    Accepting query settings (e.g. memory/threads) does not imply honoring a
    timeout or scan-byte cap. Keep legacy executor behavior only when the more
    specific capability is absent. Application services explicitly disable it.
    """
    return bool(
        getattr(
            executor,
            "supports_bounded_speculative_reads",
            getattr(executor, "supports_per_query_read_settings", True),
        )
    )


@contextmanager
def application_read_context(enabled: bool = True):
    """Scope application mode, including a bounded diagnostic opt-out."""
    if not isinstance(enabled, bool):
        raise TypeError("Application read mode must be bool")
    token = _application_read.set(enabled)
    try:
        yield
    finally:
        _application_read.reset(token)
