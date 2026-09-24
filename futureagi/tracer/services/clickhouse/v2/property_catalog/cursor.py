"""Opaque cursors for the immutable unified property-definition catalog.

The cursor is intentionally independent from the trace/span list cursor.  A
property page is pinned to an activated ClickHouse catalog revision rather
than to a telemetry time window.  Every token binds the authenticated tenant,
the complete authorization/filter shape, the page size, the activation
fingerprint, and the last six-column ordering tuple.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.core import signing

from .runtime_limits import RUNTIME_LIMITS

PROPERTY_CATALOG_CURSOR_VERSION = 1
PROPERTY_CATALOG_CURSOR_SALT = "tracer.property-catalog-cursor.v1"
PROPERTY_CATALOG_CURSOR_MAX_AGE_SECONDS = RUNTIME_LIMITS.cursor_max_age_seconds
PROPERTY_CATALOG_CURSOR_MAX_BYTES = RUNTIME_LIMITS.cursor_max_bytes
PROPERTY_CATALOG_CURSOR_MAX_PAGE_SIZE = RUNTIME_LIMITS.max_page_size
PROPERTY_CATALOG_ORDER_WIDTH = 6

_SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")


class PropertyCatalogCursorError(ValueError):
    """A sanitized cursor error that is safe to expose at the API edge."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class PropertyCatalogCursor:
    catalog_epoch: int
    catalog_revision: int
    activation_fingerprint: str
    order: tuple[int, int, str, str, str, str]


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def normalize_property_catalog_scope(scope: dict[str, Any]) -> dict[str, Any]:
    """Canonicalize auth/visibility scope before it is cursor-bound."""

    project_ids = sorted({str(item) for item in scope.get("project_ids", ())})
    normalized = {
        "principal_id": str(scope.get("principal_id") or ""),
        "auth_type": str(scope.get("auth_type") or ""),
        "auth_id": str(scope.get("auth_id") or ""),
        "organization_id": str(scope.get("organization_id") or ""),
        "workspace_id": str(scope.get("workspace_id") or ""),
        "project_ids": project_ids,
        "agent_definition_id": str(scope.get("agent_definition_id") or ""),
        "dataset_id": str(scope.get("dataset_id") or ""),
    }
    # Preserve existing explicit-project cursor digests. Workspace-wide reads
    # opt into a stronger contract: ``project_ids`` is the complete eligible
    # Observe-project PG snapshot and the activation must prove exactly that set.
    if scope.get("workspace_scope") is True:
        normalized["workspace_scope"] = True
    return normalized


def normalize_property_catalog_query(query: dict[str, Any]) -> dict[str, Any]:
    """Canonicalize every definition filter that changes page membership."""

    normalized = {
        "category": str(query.get("category") or ""),
        "source": str(query.get("source") or ""),
        "property_kind": str(query.get("property_kind") or ""),
        "per_eval_config": bool(query.get("per_eval_config", False)),
        "search": str(query.get("search") or "").strip().casefold(),
    }
    # Keep empty-role cursor digests byte-for-byte compatible with cursors
    # issued before role-scoped reads were introduced. Only role-filtered
    # consumers bind the extra membership constraint into the cursor.
    role = str(query.get("role") or "")
    if role:
        normalized["role"] = role
    return normalized


def _max_age_seconds() -> int:
    configured = int(
        getattr(
            settings,
            "PROPERTY_CATALOG_CURSOR_MAX_AGE_SECONDS",
            PROPERTY_CATALOG_CURSOR_MAX_AGE_SECONDS,
        )
    )
    return max(1, configured)


def _validate_order(order: Any) -> tuple[int, int, str, str, str, str]:
    if (
        not isinstance(order, (list, tuple))
        or len(order) != PROPERTY_CATALOG_ORDER_WIDTH
    ):
        raise PropertyCatalogCursorError(
            "invalid_cursor", "The property continuation cursor is invalid."
        )
    category_rank, source_rank, primary_source, sort_name, name, property_id = order
    if (
        type(category_rank) is not int
        or not 0 <= category_rank <= 255
        or type(source_rank) is not int
        or not 0 <= source_rank <= 65_535
        or any(
            not isinstance(item, str)
            for item in (primary_source, sort_name, name, property_id)
        )
        or not property_id
        or any(
            len(item.encode("utf-8")) > 4_096
            for item in (primary_source, sort_name, name, property_id)
        )
    ):
        raise PropertyCatalogCursorError(
            "invalid_cursor", "The property continuation cursor is invalid."
        )
    return (
        category_rank,
        source_rank,
        primary_source,
        sort_name,
        name,
        property_id,
    )


def encode_property_catalog_cursor(
    *,
    scope: dict[str, Any],
    query: dict[str, Any],
    page_size: int,
    catalog_epoch: int,
    catalog_revision: int,
    activation_fingerprint: str,
    order: tuple[int, int, str, str, str, str] | list[Any],
) -> str:
    if (
        type(page_size) is not int
        or not 1 <= page_size <= PROPERTY_CATALOG_CURSOR_MAX_PAGE_SIZE
        or type(catalog_epoch) is not int
        or not 1 <= catalog_epoch <= 65_535
        or type(catalog_revision) is not int
        or catalog_revision < 1
        or not isinstance(activation_fingerprint, str)
        or _SHA256_RE.fullmatch(activation_fingerprint) is None
    ):
        raise ValueError("invalid property catalog cursor state")
    checked_order = _validate_order(order)
    payload = {
        "v": PROPERTY_CATALOG_CURSOR_VERSION,
        "scope": _digest(normalize_property_catalog_scope(scope)),
        "query": _digest(normalize_property_catalog_query(query)),
        "page_size": page_size,
        "catalog_epoch": catalog_epoch,
        "catalog_revision": catalog_revision,
        "activation_fingerprint": activation_fingerprint,
        "order": list(checked_order),
    }
    token = signing.dumps(
        payload,
        key=settings.SECRET_KEY,
        salt=PROPERTY_CATALOG_CURSOR_SALT,
        compress=True,
    )
    if len(token.encode("utf-8")) > PROPERTY_CATALOG_CURSOR_MAX_BYTES:
        raise ValueError("property catalog cursor exceeds its transport bound")
    return token


def decode_property_catalog_cursor(
    token: str,
    *,
    scope: dict[str, Any],
    query: dict[str, Any],
    page_size: int,
) -> PropertyCatalogCursor:
    if (
        not isinstance(token, str)
        or not token
        or len(token.encode("utf-8")) > PROPERTY_CATALOG_CURSOR_MAX_BYTES
    ):
        raise PropertyCatalogCursorError(
            "invalid_cursor", "The property continuation cursor is invalid."
        )
    try:
        payload = signing.loads(
            token,
            key=settings.SECRET_KEY,
            salt=PROPERTY_CATALOG_CURSOR_SALT,
            max_age=_max_age_seconds(),
        )
    except signing.SignatureExpired as exc:
        raise PropertyCatalogCursorError(
            "cursor_expired", "The property continuation cursor has expired."
        ) from exc
    except (signing.BadSignature, TypeError, ValueError) as exc:
        raise PropertyCatalogCursorError(
            "invalid_cursor", "The property continuation cursor is invalid."
        ) from exc

    if (
        not isinstance(payload, dict)
        or payload.get("v") != PROPERTY_CATALOG_CURSOR_VERSION
    ):
        raise PropertyCatalogCursorError(
            "invalid_cursor", "The property continuation cursor is invalid."
        )
    if (
        payload.get("scope") != _digest(normalize_property_catalog_scope(scope))
        or payload.get("query") != _digest(normalize_property_catalog_query(query))
        or payload.get("page_size") != page_size
    ):
        raise PropertyCatalogCursorError(
            "cursor_mismatch",
            "The property continuation cursor does not match this request.",
        )

    epoch = payload.get("catalog_epoch")
    revision = payload.get("catalog_revision")
    activation_fingerprint = payload.get("activation_fingerprint")
    if (
        type(epoch) is not int
        or not 1 <= epoch <= 65_535
        or type(revision) is not int
        or revision < 1
        or not isinstance(activation_fingerprint, str)
        or _SHA256_RE.fullmatch(activation_fingerprint) is None
    ):
        raise PropertyCatalogCursorError(
            "invalid_cursor", "The property continuation cursor is invalid."
        )
    return PropertyCatalogCursor(
        catalog_epoch=epoch,
        catalog_revision=revision,
        activation_fingerprint=activation_fingerprint,
        order=_validate_order(payload.get("order")),
    )


__all__ = [
    "PropertyCatalogCursor",
    "PropertyCatalogCursorError",
    "decode_property_catalog_cursor",
    "encode_property_catalog_cursor",
    "normalize_property_catalog_query",
    "normalize_property_catalog_scope",
]
