"""Signed, authorization-bound keysets for the current property inventory."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.conf import settings
from django.core import signing

from .codec import (
    CUSTOM_ATTRIBUTE_PREFIX,
    MAX_CUSTOM_PROPERTY_ID_BYTES,
    MAX_FOLDED_ATTRIBUTE_KEY_BYTES,
    MAX_IDENTITY_COMPONENT_BYTES,
    validate_text,
)
from .runtime_limits import RUNTIME_LIMITS

PROPERTY_CATALOG_CURSOR_VERSION = 2
PROPERTY_CATALOG_CURSOR_SALT = "tracer.property-catalog-cursor.v2"
PROPERTY_CATALOG_CURSOR_MAX_AGE_SECONDS = RUNTIME_LIMITS.cursor_max_age_seconds
PROPERTY_CATALOG_CURSOR_MAX_BYTES = RUNTIME_LIMITS.cursor_max_bytes
PROPERTY_CATALOG_CURSOR_MAX_PAGE_SIZE = RUNTIME_LIMITS.max_page_size
PROPERTY_CATALOG_ORDER_WIDTH = 6


class PropertyCatalogCursorError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class PropertyCatalogCursor:
    order: tuple[int, int, str, str, str, str]


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()


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
    # Bind the complete currently authorized project set for workspace reads.
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


def validate_scope(scope: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_property_catalog_scope(scope)
    for field in ("organization_id", "workspace_id"):
        normalized[field] = str(UUID(normalized[field]))
    normalized["project_ids"] = tuple(
        sorted({str(UUID(value)) for value in normalized["project_ids"]})
    )
    for field in ("agent_definition_id", "dataset_id"):
        if normalized[field]:
            normalized[field] = str(UUID(normalized[field]))
    return normalized


def validate_page_size(page_size: int) -> None:
    if (
        type(page_size) is not int
        or not 1 <= page_size <= PROPERTY_CATALOG_CURSOR_MAX_PAGE_SIZE
    ):
        raise ValueError("page_size is outside the property catalog limit")


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
    ):
        raise PropertyCatalogCursorError(
            "invalid_cursor", "The property continuation cursor is invalid."
        )
    custom = property_id.startswith(CUSTOM_ATTRIBUTE_PREFIX)
    limits = (
        MAX_IDENTITY_COMPONENT_BYTES,
        MAX_FOLDED_ATTRIBUTE_KEY_BYTES if custom else MAX_IDENTITY_COMPONENT_BYTES,
        MAX_IDENTITY_COMPONENT_BYTES,
        MAX_CUSTOM_PROPERTY_ID_BYTES if custom else MAX_IDENTITY_COMPONENT_BYTES,
    )
    try:
        for index, (item, limit) in enumerate(
            zip((primary_source, sort_name, name, property_id), limits, strict=True)
        ):
            validate_text(
                item,
                field="cursor order",
                max_bytes=limit,
                allow_controls=custom and index > 0,
            )
        if custom and (
            property_id != CUSTOM_ATTRIBUTE_PREFIX + name
            or sort_name != name.casefold()
        ):
            raise ValueError("inconsistent custom attribute keyset")
    except (TypeError, ValueError) as exc:
        raise PropertyCatalogCursorError(
            "invalid_cursor", "The property continuation cursor is invalid."
        ) from exc
    return (
        category_rank,
        source_rank,
        primary_source,
        sort_name,
        name,
        property_id,
    )


def encode_current_cursor(*, salt, scope, query, page_size, order):
    validate_page_size(page_size)
    token = signing.dumps(
        {
            "v": 2,
            "scope": _digest(normalize_property_catalog_scope(scope)),
            "query": _digest(query),
            "page_size": page_size,
            "order": list(order),
        },
        salt=salt,
        compress=True,
    )
    if len(token.encode()) > PROPERTY_CATALOG_CURSOR_MAX_BYTES:
        raise ValueError("property cursor exceeds its transport bound")
    return token


def decode_current_cursor(
    *, token, salt, scope, query, page_size, error_type=PropertyCatalogCursorError
):
    def fail(code):
        messages = {
            "invalid_cursor": "The property continuation cursor is invalid.",
            "cursor_mismatch": "The property continuation cursor does not match this request.",
            "cursor_expired": "The property continuation cursor has expired. Restart from the first page.",
        }
        raise error_type(code, messages[code])

    if (
        not isinstance(token, str)
        or not token
        or len(token.encode()) > PROPERTY_CATALOG_CURSOR_MAX_BYTES
    ):
        fail("invalid_cursor")
    max_age = max(
        1,
        int(
            getattr(
                settings,
                "PROPERTY_CATALOG_CURSOR_MAX_AGE_SECONDS",
                PROPERTY_CATALOG_CURSOR_MAX_AGE_SECONDS,
            )
        ),
    )
    legacy = False
    try:
        payload = signing.loads(token, salt=salt, max_age=max_age)
    except signing.SignatureExpired:
        fail("cursor_expired")
    except (signing.BadSignature, TypeError, ValueError):
        try:
            payload = signing.loads(
                token, salt=salt.replace(".v2", ".v1"), max_age=max_age
            )
            legacy = True
        except signing.SignatureExpired:
            fail("cursor_expired")
        except (signing.BadSignature, TypeError, ValueError):
            fail("invalid_cursor")
    if not isinstance(payload, dict) or payload.get("v") != (1 if legacy else 2):
        fail("invalid_cursor")
    if (
        payload.get("scope") != _digest(normalize_property_catalog_scope(scope))
        or payload.get("query") != _digest(query)
        or payload.get("page_size") != page_size
    ):
        fail("cursor_mismatch")
    if legacy:
        fail("cursor_expired")
    return payload.get("order")


def encode_property_catalog_cursor(*, scope, query, page_size, order):
    return encode_current_cursor(
        salt=PROPERTY_CATALOG_CURSOR_SALT,
        scope=scope,
        query=normalize_property_catalog_query(query),
        page_size=page_size,
        order=_validate_order(order),
    )


def decode_property_catalog_cursor(token, *, scope, query, page_size):
    order = decode_current_cursor(
        token=token,
        salt=PROPERTY_CATALOG_CURSOR_SALT,
        scope=scope,
        query=normalize_property_catalog_query(query),
        page_size=page_size,
    )
    return PropertyCatalogCursor(order=_validate_order(order))


def decode_native_list_cursor(token, *, resource, scope, query, page_size):
    """Retain native fact keysets, but require retired snapshot walks to restart."""
    from tracer.services.clickhouse.list_cursor import (
        ListCursorError,
        decode_list_cursor,
    )

    baseline = dict(query)
    baseline.pop("query_window_mode", None)
    try:
        state = decode_list_cursor(
            token, resource=resource, scope=scope, query=baseline, page_size=page_size
        )
        return state, None
    except ListCursorError as exc:
        if exc.code != "cursor_mismatch":
            raise
        # Only a verified legacy signature receives a restart response.
        decode_list_cursor(
            token,
            resource=resource,
            scope=scope,
            query={**baseline, "query_window_mode": "frozen_snapshot"},
            page_size=page_size,
        )
        raise ListCursorError("cursor_expired", "Restart from the first page.") from exc
