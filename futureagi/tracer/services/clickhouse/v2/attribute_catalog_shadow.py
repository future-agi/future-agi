"""Fail-open shadow observations for the inactive span-attribute catalog.

The authoritative ``spans`` selectors remain the only source of public API
responses.  When explicitly enabled, this module gives the catalog reader at
most two seconds of the request's *remaining* wall time and compares only
identity digests.  It never returns catalog rows to a view and never logs a
project id, attribute key, search term, or attribute value.

The underlying :class:`AttributeCatalogReader` owns admission.  In particular,
its immutable contiguous-source fence remains globally disabled until the
schema/writer contract can prove it, and activation/checkpoint lag therefore
always produces ``CatalogUnavailable`` rather than a candidate page.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from time import monotonic
from typing import Any, Literal, Protocol

import structlog
from django.conf import settings

from tracer.services.clickhouse.attribute_reads import (
    AttributeKeyRow,
    AttributeValueRow,
    V2AttributeQueryExecutor,
)
from tracer.services.clickhouse.read_budget import ReadDeadline
from tracer.services.clickhouse.v2.attribute_catalog_codec import (
    encode_catalog_scalar,
)
from tracer.services.clickhouse.v2.attribute_catalog_reader import (
    CATALOG_MAX_PAGE_SIZE,
    CATALOG_MAX_PROJECTS,
    AttributeCatalogReader,
    CatalogKeyPage,
    CatalogUnavailable,
    CatalogValuePage,
)

logger = structlog.get_logger(__name__)

CATALOG_SHADOW_MAX_WALL_MS = 2_000
CATALOG_SHADOW_DEFAULT_PAGE_SIZE = CATALOG_MAX_PAGE_SIZE

CatalogShadowSurface = Literal["span_attribute_keys", "dashboard_attribute_values"]
CatalogShadowOutcome = Literal[
    "match",
    "mismatch",
    "unavailable",
    "skipped",
    "error",
]

_SAFE_REASON_RE = re.compile(r"\A[a-z][a-z0-9_]{0,63}\Z")
_IDENTITY_DOMAIN = b"futureagi.span-attribute-catalog.shadow.v1\x00"


class _CatalogExecutor(Protocol):
    def execute(
        self,
        query: str,
        params: dict[str, Any],
        *,
        timeout_ms: int,
        settings: dict[str, Any],
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class CatalogShadowObservation:
    """Sanitized diagnostics; every tenant/query identity is a SHA-256 digest."""

    surface: CatalogShadowSurface
    outcome: CatalogShadowOutcome
    reason: str
    project_scope_hash: str
    query_identity_hash: str
    authoritative_count: int | None = None
    catalog_count: int | None = None
    mismatch_count: int | None = None
    authoritative_set_hash: str | None = None
    catalog_set_hash: str | None = None
    mismatch_identity_hashes: tuple[str, ...] = ()
    elapsed_ms: float = 0.0


class _LazyV2CatalogExecutor:
    """Avoid opening the CH25 client while the reader's global fuse is closed."""

    def __init__(self) -> None:
        self._delegate: V2AttributeQueryExecutor | None = None

    def execute(self, *args, **kwargs):
        if self._delegate is None:
            self._delegate = V2AttributeQueryExecutor()
        return self._delegate.execute(*args, **kwargs)


class _RemainingWallExecutor:
    """Share one <=2s speculative wall across every reader query."""

    def __init__(
        self,
        delegate: _CatalogExecutor,
        *,
        shadow_deadline: ReadDeadline,
        request_deadline: ReadDeadline | None,
    ) -> None:
        self._delegate = delegate
        self._shadow_deadline = shadow_deadline
        self._request_deadline = request_deadline

    def execute(
        self,
        query: str,
        params: dict[str, Any],
        *,
        timeout_ms: int,
        settings: dict[str, Any],
    ) -> Any:
        remaining_ms = self._shadow_deadline.remaining_ms(
            CATALOG_SHADOW_MAX_WALL_MS,
            floor_ms=1,
        )
        if self._request_deadline is not None:
            remaining_ms = min(
                remaining_ms,
                self._request_deadline.remaining_ms(
                    CATALOG_SHADOW_MAX_WALL_MS,
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
                # The client-side timeout owns transport cancellation; this
                # additionally asks ClickHouse to stop server work at the same
                # remaining-wall boundary.
                "max_execution_time": bounded_timeout_ms / 1_000,
            },
        )


def run_catalog_key_shadow(
    *,
    project_ids: Iterable[str],
    authoritative_rows: Iterable[AttributeKeyRow],
    window_start: datetime,
    window_end: datetime,
    page_size: int = CATALOG_SHADOW_DEFAULT_PAGE_SIZE,
    search: str | None = None,
    continuation: bool = False,
    request_deadline: ReadDeadline | None = None,
) -> CatalogShadowObservation | None:
    """Observe one already-authorized key response without changing it."""

    return _run_catalog_shadow(
        surface="span_attribute_keys",
        project_ids=project_ids,
        authoritative_rows=authoritative_rows,
        window_start=window_start,
        window_end=window_end,
        page_size=page_size,
        search=search,
        attribute_key=None,
        attribute_types=None,
        continuation=continuation,
        request_deadline=request_deadline,
    )


def run_catalog_value_shadow(
    *,
    project_ids: Iterable[str],
    attribute_key: str,
    authoritative_rows: Iterable[AttributeValueRow],
    window_start: datetime,
    window_end: datetime,
    page_size: int = CATALOG_SHADOW_DEFAULT_PAGE_SIZE,
    attribute_types: Iterable[str] | None = None,
    search: str | None = None,
    continuation: bool = False,
    request_deadline: ReadDeadline | None = None,
) -> CatalogShadowObservation | None:
    """Observe one already-authorized dashboard value response without changing it."""

    return _run_catalog_shadow(
        surface="dashboard_attribute_values",
        project_ids=project_ids,
        authoritative_rows=authoritative_rows,
        window_start=window_start,
        window_end=window_end,
        page_size=page_size,
        search=search,
        attribute_key=attribute_key,
        attribute_types=attribute_types,
        continuation=continuation,
        request_deadline=request_deadline,
    )


def _run_catalog_shadow(
    *,
    surface: CatalogShadowSurface,
    project_ids: Iterable[str],
    authoritative_rows: Iterable[AttributeKeyRow] | Iterable[AttributeValueRow],
    window_start: datetime,
    window_end: datetime,
    page_size: int,
    search: str | None,
    attribute_key: str | None,
    attribute_types: Iterable[str] | None,
    continuation: bool,
    request_deadline: ReadDeadline | None,
) -> CatalogShadowObservation | None:
    if _catalog_read_mode() != "shadow":
        return None

    started = monotonic()
    project_scope_hash = _identity_hash("project-scope", ())
    query_identity_hash = _identity_hash("query", (surface,))
    try:
        projects = tuple(project_ids)
        normalized_attribute_types = (
            tuple(attribute_types) if attribute_types is not None else None
        )
        project_scope_hash = _identity_hash("project-scope", tuple(sorted(projects)))
        bounded_page_size = _bounded_page_size(page_size)
        epoch = _catalog_epoch()
        query_identity_hash = _identity_hash(
            "query",
            (
                surface,
                project_scope_hash,
                epoch,
                _datetime_identity(window_start),
                _datetime_identity(window_end),
                attribute_key,
                normalized_attribute_types,
                search,
                bounded_page_size,
                continuation,
            ),
        )
        if not projects:
            return _publish(
                _observation(
                    surface,
                    "skipped",
                    "empty_project_scope",
                    project_scope_hash,
                    query_identity_hash,
                    started,
                )
            )
        if len(projects) > CATALOG_MAX_PROJECTS:
            return _publish(
                _observation(
                    surface,
                    "skipped",
                    "project_scope_too_large",
                    project_scope_hash,
                    query_identity_hash,
                    started,
                )
            )
        if continuation:
            # Schema 025 cannot freeze catalog contents across page requests.
            # The authoritative selector still owns and publishes its cursor.
            return _publish(
                _observation(
                    surface,
                    "skipped",
                    "continuation_not_comparable",
                    project_scope_hash,
                    query_identity_hash,
                    started,
                )
            )

        shadow_wall_ms = CATALOG_SHADOW_MAX_WALL_MS
        if request_deadline is not None:
            shadow_wall_ms = request_deadline.remaining_ms(
                CATALOG_SHADOW_MAX_WALL_MS,
                floor_ms=1,
            )
        shadow_deadline = ReadDeadline.start(shadow_wall_ms)
        executor = _RemainingWallExecutor(
            _LazyV2CatalogExecutor(),
            shadow_deadline=shadow_deadline,
            request_deadline=request_deadline,
        )
        reader = _new_reader(
            executor,
            project_ids=projects,
            catalog_epoch=epoch,
            window_start=window_start,
            window_end=window_end,
            catalog_database=_catalog_database(),
        )
        primary_rows = tuple(authoritative_rows)
        if surface == "span_attribute_keys":
            page = reader.read_key_candidates(
                page_size=bounded_page_size,
                search=search,
            )
            primary_identities = _authoritative_key_identities(primary_rows)
        else:
            if attribute_key is None:
                raise ValueError("attribute_key is required for value shadow reads")
            page = reader.read_value_candidates(
                attribute_key,
                page_size=bounded_page_size,
                attribute_types=normalized_attribute_types,
                search=search,
            )
            primary_identities = _authoritative_value_identities(
                attribute_key,
                primary_rows,
            )

        if isinstance(page, CatalogUnavailable):
            return _publish(
                _observation(
                    surface,
                    "unavailable",
                    _safe_reason(page.reason),
                    project_scope_hash,
                    query_identity_hash,
                    started,
                    authoritative_count=len(primary_identities),
                    authoritative_set_hash=_identity_set_hash(primary_identities),
                )
            )
        if surface == "span_attribute_keys" and isinstance(page, CatalogKeyPage):
            catalog_identities = {
                _identity_hash(
                    "key",
                    (candidate.attribute_key, candidate.attribute_type),
                )
                for candidate in page.candidates
            }
        elif surface == "dashboard_attribute_values" and isinstance(
            page, CatalogValuePage
        ):
            assert attribute_key is not None
            catalog_identities = {
                _identity_hash(
                    "value",
                    (
                        attribute_key,
                        candidate.attribute_type,
                        candidate.value_fingerprint,
                    ),
                )
                for candidate in page.candidates
            }
        else:
            raise TypeError("catalog reader returned an unexpected result type")

        mismatch_identities = tuple(
            sorted(primary_identities.symmetric_difference(catalog_identities))
        )
        outcome: CatalogShadowOutcome = "mismatch" if mismatch_identities else "match"
        return _publish(
            _observation(
                surface,
                outcome,
                "identity_mismatch" if mismatch_identities else "identity_match",
                project_scope_hash,
                query_identity_hash,
                started,
                authoritative_count=len(primary_identities),
                catalog_count=len(catalog_identities),
                mismatch_count=len(mismatch_identities),
                authoritative_set_hash=_identity_set_hash(primary_identities),
                catalog_set_hash=_identity_set_hash(catalog_identities),
                mismatch_identity_hashes=mismatch_identities[:5],
            )
        )
    except Exception as exc:  # Shadowing must never alter the public read.
        return _publish(
            _observation(
                surface,
                "error",
                _exception_reason(exc),
                project_scope_hash,
                query_identity_hash,
                started,
            )
        )


def _new_reader(executor: _CatalogExecutor, **kwargs) -> AttributeCatalogReader:
    return AttributeCatalogReader(executor, **kwargs)


def _catalog_read_mode() -> str:
    value = getattr(settings, "SPAN_ATTRIBUTE_CATALOG_READ_MODE", "off")
    return value.strip().lower() if isinstance(value, str) else "off"


def _catalog_epoch() -> int:
    value = getattr(settings, "SPAN_ATTRIBUTE_CATALOG_EPOCH", 0)
    if type(value) is not int or not 1 <= value <= 65_535:
        raise ValueError("span attribute catalog epoch is not configured")
    return value


def _catalog_database() -> str | None:
    value = getattr(settings, "SPAN_ATTRIBUTE_CATALOG_DATABASE", "")
    if not isinstance(value, str):
        raise ValueError("span attribute catalog database is not configured")
    return value or None


def _bounded_page_size(value: int) -> int:
    if type(value) is not int or value < 1:
        raise ValueError("catalog shadow page size must be positive")
    return min(value, CATALOG_MAX_PAGE_SIZE)


def _datetime_identity(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("catalog shadow windows must be timezone-aware")
    return value.isoformat(timespec="microseconds")


def _authoritative_key_identities(rows: Iterable[Any]) -> set[str]:
    identities: set[str] = set()
    for row in rows:
        key = row.key
        dominant_type = row.type
        types = tuple(getattr(row, "types", ()) or (dominant_type,))
        if (
            not isinstance(key, str)
            or not key
            or not all(isinstance(attribute_type, str) for attribute_type in types)
        ):
            raise ValueError("invalid authoritative key identity")
        identities.update(
            _identity_hash("key", (key, attribute_type)) for attribute_type in types
        )
    return identities


def _authoritative_value_identities(
    attribute_key: str,
    rows: Iterable[Any],
) -> set[str]:
    if not isinstance(attribute_key, str) or not attribute_key:
        raise ValueError("invalid authoritative value key")
    identities: set[str] = set()
    for row in rows:
        attribute_type = row.type
        if not isinstance(attribute_type, str):
            raise ValueError("invalid authoritative value type")
        scalar = encode_catalog_scalar(row.value)
        identities.add(
            _identity_hash(
                "value",
                (attribute_key, attribute_type, scalar.fingerprint),
            )
        )
    return identities


def _identity_hash(domain: str, identity: Any) -> str:
    payload = json.dumps(
        identity,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(
        _IDENTITY_DOMAIN + domain.encode("ascii") + b"\x00" + payload
    ).hexdigest()


def _identity_set_hash(identities: Iterable[str]) -> str:
    return _identity_hash("identity-set", tuple(sorted(identities)))


def _safe_reason(reason: Any) -> str:
    if isinstance(reason, str) and _SAFE_REASON_RE.fullmatch(reason):
        return reason
    return "unclassified"


def _exception_reason(exc: Exception) -> str:
    # Exception messages may contain SQL parameters or transport details.
    # Classify all failures under one bounded, non-tenant label.
    del exc
    return "shadow_exception"


def _observation(
    surface: CatalogShadowSurface,
    outcome: CatalogShadowOutcome,
    reason: str,
    project_scope_hash: str,
    query_identity_hash: str,
    started: float,
    **kwargs,
) -> CatalogShadowObservation:
    return CatalogShadowObservation(
        surface=surface,
        outcome=outcome,
        reason=_safe_reason(reason),
        project_scope_hash=project_scope_hash,
        query_identity_hash=query_identity_hash,
        elapsed_ms=max(0.0, (monotonic() - started) * 1_000),
        **kwargs,
    )


def _publish(observation: CatalogShadowObservation) -> CatalogShadowObservation:
    payload = {
        "surface": observation.surface,
        "outcome": observation.outcome,
        "reason": observation.reason,
        "project_scope_hash": observation.project_scope_hash,
        "query_identity_hash": observation.query_identity_hash,
        "authoritative_count": observation.authoritative_count,
        "catalog_count": observation.catalog_count,
        "mismatch_count": observation.mismatch_count,
        "authoritative_set_hash": observation.authoritative_set_hash,
        "catalog_set_hash": observation.catalog_set_hash,
        "mismatch_identity_hashes": observation.mismatch_identity_hashes,
        "elapsed_ms": round(observation.elapsed_ms, 3),
    }
    log = (
        logger.warning
        if observation.outcome in {"mismatch", "unavailable", "error"}
        else logger.info
    )
    log("span_attribute_catalog_shadow", **payload)
    _record_metrics(observation)
    return observation


def _record_metrics(observation: CatalogShadowObservation) -> None:
    """Emit bounded OpenTelemetry attributes; no tenant identity is a label."""

    try:
        counter, duration = _metric_instruments()
        attributes = {
            "surface": observation.surface,
            "outcome": observation.outcome,
            "reason": observation.reason,
        }
        counter.add(1, attributes)
        duration.record(observation.elapsed_ms, attributes)
    except Exception:
        # Telemetry is itself speculative and cannot affect a public response.
        return


@lru_cache(maxsize=1)
def _metric_instruments():
    from opentelemetry import metrics

    meter = metrics.get_meter(__name__)
    return (
        meter.create_counter("futureagi.span_attribute_catalog.shadow.observations"),
        meter.create_histogram(
            "futureagi.span_attribute_catalog.shadow.duration",
            unit="ms",
        ),
    )


__all__ = [
    "CATALOG_SHADOW_MAX_WALL_MS",
    "CatalogShadowObservation",
    "run_catalog_key_shadow",
    "run_catalog_value_shadow",
]
