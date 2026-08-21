"""DEV-only public read routing for the immutable attribute catalog.

The view remains responsible for authorization and its established signed
cursor envelope.  This module owns only the catalog attempt, typed row
adaptation, and opaque catalog checkpoint payload.  Any admission/query defect
returns a sanitized fallback decision; callers then run the unchanged
authoritative ``spans`` selector over the same frozen window.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generic, Literal, Protocol, TypeVar, cast

import structlog
from django.conf import settings

from tracer.services.clickhouse.attribute_reads import (
    AttributeKeyRow,
    AttributeValueRow,
)
from tracer.services.clickhouse.read_budget import ReadDeadline
from tracer.services.clickhouse.v2.attribute_catalog_connection import (
    AttributeCatalogReadExecutor,
)
from tracer.services.clickhouse.v2.attribute_catalog_reader import (
    CATALOG_MAX_PAGE_SIZE,
    AttributeCatalogReader,
    AttributeType,
    CatalogKeyCheckpoint,
    CatalogKeyPage,
    CatalogUnavailable,
    CatalogValueCheckpoint,
    CatalogValuePage,
)

logger = structlog.get_logger(__name__)

CATALOG_DEV_READ_ACK = "I_ACKNOWLEDGE_DEV_ONLY_ATTRIBUTE_CATALOG_READS"
CATALOG_KEY_CURSOR_MARKER = "span-attribute-catalog-key-v1"
CATALOG_VALUE_CURSOR_MARKER = "span-attribute-catalog-value-v1"
CATALOG_PUBLIC_READ_MAX_WALL_MS = 2_000
CATALOG_SYSTEM_PROJECTION_VERSION = 2
CATALOG_SYSTEM_VALUE_METRICS = frozenset({"model"})

_SAFE_REASON_RE = re.compile(r"\A[a-z][a-z0-9_]{0,63}\Z")
_TYPE_RANK: dict[AttributeType, int] = {
    "string": 1,
    "number": 2,
    "boolean": 3,
    "array": 4,
    "map": 5,
    "json": 6,
}


class _CatalogExecutor(Protocol):
    def execute(
        self,
        query: str,
        params: dict[str, Any],
        *,
        timeout_ms: int,
        settings: dict[str, Any],
    ) -> Any: ...


class _RemainingWallExecutor:
    def __init__(
        self,
        delegate: _CatalogExecutor,
        *,
        deadline: ReadDeadline,
        request_deadline: ReadDeadline | None,
    ) -> None:
        self._delegate = delegate
        self._deadline = deadline
        self._request_deadline = request_deadline

    def execute(self, query, params, *, timeout_ms, settings):
        remaining_ms = self._deadline.remaining_ms(
            CATALOG_PUBLIC_READ_MAX_WALL_MS,
            floor_ms=1,
        )
        if self._request_deadline is not None:
            remaining_ms = min(
                remaining_ms,
                self._request_deadline.remaining_ms(
                    CATALOG_PUBLIC_READ_MAX_WALL_MS,
                    floor_ms=1,
                ),
            )
        bounded_timeout_ms = max(1, min(int(timeout_ms), remaining_ms))
        return self._delegate.execute(
            query,
            params,
            timeout_ms=bounded_timeout_ms,
            settings={
                **settings,
                "max_execution_time": bounded_timeout_ms / 1_000,
            },
        )

    def close(self) -> None:
        close = getattr(self._delegate, "close", None)
        if callable(close):
            close()


PageT = TypeVar("PageT", CatalogKeyPage, CatalogValuePage)


@dataclass(frozen=True, slots=True)
class CatalogReadAttempt(Generic[PageT]):
    attempted: bool
    page: PageT | None
    fallback_reason: str | None = None

    @property
    def used_catalog(self) -> bool:
        return self.page is not None


def catalog_read_mode() -> str:
    value = getattr(settings, "SPAN_ATTRIBUTE_CATALOG_READ_MODE", "off")
    return value.strip().lower() if isinstance(value, str) else "off"


def catalog_dev_read_enabled() -> bool:
    if catalog_read_mode() != "read":
        return False
    environment = str(getattr(settings, "ENV_TYPE", "")).strip().lower()
    cloud_deployment = getattr(settings, "CLOUD_DEPLOYMENT", "")
    acknowledgement = getattr(settings, "SPAN_ATTRIBUTE_CATALOG_DEV_READ_ACK", "")
    dev_deployment = environment in {"dev", "development"} or (
        environment == "staging" and cloud_deployment == "DEV"
    )
    return dev_deployment and acknowledgement == CATALOG_DEV_READ_ACK


def try_catalog_key_page(
    *,
    project_ids: Iterable[str],
    window_start: datetime,
    window_end: datetime,
    page_size: int,
    search: str | None,
    after: CatalogKeyCheckpoint | None,
    request_deadline: ReadDeadline | None,
    attribute_types: Iterable[AttributeType] | None = None,
) -> CatalogReadAttempt[CatalogKeyPage]:
    return cast(
        CatalogReadAttempt[CatalogKeyPage],
        _try_catalog_page(
            kind="key",
            project_ids=project_ids,
            window_start=window_start,
            window_end=window_end,
            page_size=page_size,
            search=search,
            after=after,
            attribute_key=None,
            attribute_types=attribute_types,
            request_deadline=request_deadline,
            source_kind="custom_attribute",
            required_projection_version=1,
        ),
    )


def try_catalog_value_page(
    *,
    project_ids: Iterable[str],
    attribute_key: str,
    window_start: datetime,
    window_end: datetime,
    page_size: int,
    attribute_types: Iterable[AttributeType] | None,
    search: str | None,
    after: CatalogValueCheckpoint | None,
    request_deadline: ReadDeadline | None,
) -> CatalogReadAttempt[CatalogValuePage]:
    return cast(
        CatalogReadAttempt[CatalogValuePage],
        _try_catalog_page(
            kind="value",
            project_ids=project_ids,
            window_start=window_start,
            window_end=window_end,
            page_size=page_size,
            search=search,
            after=after,
            attribute_key=attribute_key,
            attribute_types=attribute_types,
            request_deadline=request_deadline,
            source_kind="custom_attribute",
            required_projection_version=1,
        ),
    )


def try_catalog_system_value_page(
    *,
    project_ids: Iterable[str],
    metric_name: str,
    window_start: datetime,
    window_end: datetime,
    page_size: int,
    search: str | None,
    after: CatalogValueCheckpoint | None,
    request_deadline: ReadDeadline | None,
) -> CatalogReadAttempt[CatalogValuePage]:
    """Read one exact system-property page from a projection-v2 epoch.

    Only code-owned hot columns explicitly injected by both historical and
    live ingestion are admitted. Derived voice/dictionary fields continue to
    use their authoritative readers until they receive their own projection.
    """

    if metric_name not in CATALOG_SYSTEM_VALUE_METRICS:
        return CatalogReadAttempt(False, None)
    return cast(
        CatalogReadAttempt[CatalogValuePage],
        _try_catalog_page(
            kind="value",
            project_ids=project_ids,
            window_start=window_start,
            window_end=window_end,
            page_size=page_size,
            search=search,
            after=after,
            attribute_key=metric_name,
            attribute_types=("string",),
            request_deadline=request_deadline,
            source_kind="system_attribute",
            required_projection_version=CATALOG_SYSTEM_PROJECTION_VERSION,
        ),
    )


def _try_catalog_page(
    *,
    kind: Literal["key", "value"],
    project_ids: Iterable[str],
    window_start: datetime,
    window_end: datetime,
    page_size: int,
    search: str | None,
    after: CatalogKeyCheckpoint | CatalogValueCheckpoint | None,
    attribute_key: str | None,
    attribute_types: Iterable[AttributeType] | None,
    request_deadline: ReadDeadline | None,
    source_kind: Literal["custom_attribute", "system_attribute"],
    required_projection_version: int,
) -> CatalogReadAttempt:
    if catalog_read_mode() != "read":
        return CatalogReadAttempt(False, None)
    if not catalog_dev_read_enabled():
        return _fallback(kind, "dev_read_guard_closed")
    # Public catalog reads currently implement only the explicit frozen DEV
    # snapshot. Repeat the startup requirement here without making it
    # conditional: tests and process-local settings overrides can bypass
    # settings import, and must never turn ``read`` into an arbitrary-window
    # catalog query.
    if (
        getattr(settings, "SPAN_ATTRIBUTE_CATALOG_DEV_SNAPSHOT_ENABLED", False)
        is not True
    ):
        return _fallback(kind, "snapshot_guard_closed")
    # Import lazily: the snapshot helper repeats this module's runtime DEV
    # read guard, while this boundary owns the final pre-ClickHouse window
    # admission.
    from tracer.services.clickhouse.v2.attribute_catalog_snapshot import (
        catalog_dev_snapshot_window,
    )

    snapshot_window = catalog_dev_snapshot_window()
    if snapshot_window is None:
        return _fallback(kind, "snapshot_config_invalid")
    if snapshot_window != (window_start, window_end):
        return _fallback(kind, "snapshot_window_mismatch")
    projects = tuple(project_ids)
    if not projects:
        return _fallback(kind, "empty_project_scope")
    executor: _RemainingWallExecutor | None = None
    try:
        page_size = min(max(int(page_size), 1), CATALOG_MAX_PAGE_SIZE)
        wall_ms = CATALOG_PUBLIC_READ_MAX_WALL_MS
        if request_deadline is not None:
            wall_ms = request_deadline.remaining_ms(wall_ms, floor_ms=1)
        deadline = ReadDeadline.start(wall_ms)
        executor = _RemainingWallExecutor(
            _new_executor(),
            deadline=deadline,
            request_deadline=request_deadline,
        )
        reader_kwargs = {
            "project_ids": projects,
            "catalog_epoch": _catalog_epoch(),
            "window_start": window_start,
            "window_end": window_end,
            "catalog_database": _catalog_database(),
        }
        if required_projection_version != 1:
            reader_kwargs["required_projection_version"] = required_projection_version
        reader = _new_reader(executor, **reader_kwargs)
        if kind == "key":
            page = reader.read_key_candidates(
                page_size=page_size,
                search=search,
                after=cast(CatalogKeyCheckpoint | None, after),
                attribute_types=attribute_types,
            )
        else:
            if attribute_key is None:
                raise ValueError("catalog value read requires an attribute key")
            page = reader.read_value_candidates(
                attribute_key,
                page_size=page_size,
                attribute_types=attribute_types,
                search=search,
                after=cast(CatalogValueCheckpoint | None, after),
                source_kind=source_kind,
            )
        if isinstance(page, CatalogUnavailable):
            return _fallback(kind, page.reason)
        logger.info(
            "span_attribute_catalog_public_read", surface=kind, outcome="catalog"
        )
        return CatalogReadAttempt(True, page)
    except Exception:
        return _fallback(kind, "catalog_read_exception")
    finally:
        if executor is not None:
            executor.close()


def _fallback(kind: str, reason: Any) -> CatalogReadAttempt:
    safe_reason = (
        reason
        if isinstance(reason, str) and _SAFE_REASON_RE.fullmatch(reason)
        else "catalog_unavailable"
    )
    logger.warning(
        "span_attribute_catalog_public_read",
        surface=kind,
        outcome="fallback",
        reason=safe_reason,
    )
    return CatalogReadAttempt(True, None, safe_reason)


def _new_executor() -> _CatalogExecutor:
    return AttributeCatalogReadExecutor()


def _new_reader(executor: _CatalogExecutor, **kwargs) -> AttributeCatalogReader:
    return AttributeCatalogReader(executor, **kwargs)


def _catalog_epoch() -> int:
    value = getattr(settings, "SPAN_ATTRIBUTE_CATALOG_EPOCH", 0)
    if type(value) is not int or not 1 <= value <= 65_535:
        raise ValueError("span attribute catalog epoch is not configured")
    return value


def _catalog_database() -> str | None:
    value = getattr(settings, "SPAN_ATTRIBUTE_CATALOG_CH_DATABASE", "")
    if not isinstance(value, str):
        raise ValueError("span attribute catalog database is not configured")
    return value or None


def catalog_key_rows(
    page: CatalogKeyPage,
    *,
    exact_key: str | None = None,
) -> tuple[AttributeKeyRow, ...]:
    grouped: dict[str, set[AttributeType]] = defaultdict(set)
    ordered_keys: list[str] = []
    for candidate in page.candidates:
        if exact_key is not None and candidate.attribute_key != exact_key:
            continue
        if candidate.attribute_key not in grouped:
            ordered_keys.append(candidate.attribute_key)
        grouped[candidate.attribute_key].add(candidate.attribute_type)
    return tuple(
        AttributeKeyRow(
            key,
            sorted(grouped[key], key=_TYPE_RANK.__getitem__)[0],
            1,
            tuple(sorted(grouped[key], key=_TYPE_RANK.__getitem__)),
        )
        for key in ordered_keys
    )


def catalog_value_rows(page: CatalogValuePage) -> tuple[AttributeValueRow, ...]:
    return tuple(
        AttributeValueRow(candidate.value, candidate.attribute_type, 1)
        for candidate in page.candidates
    )


def key_checkpoint_state(checkpoint: CatalogKeyCheckpoint | None) -> tuple:
    if checkpoint is None:
        return ()
    return (
        checkpoint.source,
        checkpoint.catalog_epoch,
        checkpoint.project_scope_fingerprint,
        checkpoint.window_start,
        checkpoint.window_end,
        checkpoint.attribute_types,
        checkpoint.normalized_search,
        checkpoint.query_fingerprint,
        checkpoint.qualification_fingerprint,
        checkpoint.key_folded,
        checkpoint.attribute_key,
        checkpoint.attribute_type_rank,
    )


def value_checkpoint_state(checkpoint: CatalogValueCheckpoint | None) -> tuple:
    if checkpoint is None:
        return ()
    return (
        checkpoint.source,
        checkpoint.catalog_epoch,
        checkpoint.project_scope_fingerprint,
        checkpoint.window_start,
        checkpoint.window_end,
        checkpoint.attribute_key,
        checkpoint.attribute_types,
        checkpoint.normalized_search,
        checkpoint.query_fingerprint,
        checkpoint.qualification_fingerprint,
        checkpoint.value_fingerprint,
        checkpoint.attribute_type_rank,
    )


def key_checkpoint_from_state(value: Any) -> CatalogKeyCheckpoint | None:
    if value == ():
        return None
    if not isinstance(value, tuple) or len(value) != 12:
        raise ValueError("invalid catalog key checkpoint")
    return CatalogKeyCheckpoint(*value)


def value_checkpoint_from_state(value: Any) -> CatalogValueCheckpoint | None:
    if value == ():
        return None
    if not isinstance(value, tuple) or len(value) != 12:
        raise ValueError("invalid catalog value checkpoint")
    return CatalogValueCheckpoint(*value)


def mark_catalog_response(response, attempt: CatalogReadAttempt):
    if not attempt.attempted:
        return response
    response["X-FutureAGI-Attribute-Catalog"] = (
        "catalog" if attempt.used_catalog else "fallback"
    )
    if attempt.fallback_reason:
        response["X-FutureAGI-Attribute-Catalog-Fallback"] = attempt.fallback_reason
    return response


__all__ = [
    "CATALOG_DEV_READ_ACK",
    "CATALOG_KEY_CURSOR_MARKER",
    "CATALOG_VALUE_CURSOR_MARKER",
    "CATALOG_SYSTEM_VALUE_METRICS",
    "CatalogReadAttempt",
    "catalog_dev_read_enabled",
    "catalog_key_rows",
    "catalog_read_mode",
    "catalog_value_rows",
    "key_checkpoint_from_state",
    "key_checkpoint_state",
    "mark_catalog_response",
    "try_catalog_key_page",
    "try_catalog_value_page",
    "try_catalog_system_value_page",
    "value_checkpoint_from_state",
    "value_checkpoint_state",
]
