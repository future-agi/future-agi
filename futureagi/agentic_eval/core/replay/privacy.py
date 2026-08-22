"""Credential-aware canonicalization and report serialization helpers."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Final

_DEFAULT_SENSITIVE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "api_key",
        "apikey",
        "api-key",
        "x-api-key",
        "x-secret-key",
        "token",
        "access_token",
        "refresh_token",
        "session_token",
        "cookie",
        "set-cookie",
        "password",
        "passwd",
        "secret",
        "secret_key",
        "client_secret",
        "private_key",
        "aws_secret_access_key",
    }
)
_SENSITIVE_SUFFIXES: Final[tuple[str, ...]] = (
    "apikey",
    "accesstoken",
    "refreshtoken",
    "sessiontoken",
    "clientsecret",
    "password",
    "secretkey",
    "secret",
    "token",
)


def _normalized_key(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _sensitive_key_set(additional_sensitive_keys: Iterable[str]) -> frozenset[str]:
    additional_keys = tuple(additional_sensitive_keys)
    if any(not isinstance(key, str) for key in additional_keys):
        raise TypeError("additional sensitive keys must be strings")
    if any(not _normalized_key(key) for key in additional_keys):
        raise ValueError("additional sensitive keys cannot be empty")
    return frozenset(
        _normalized_key(key)
        for key in (*_DEFAULT_SENSITIVE_KEYS, *additional_keys)
    )


def _is_sensitive_key(key: str, sensitive_keys: frozenset[str]) -> bool:
    normalized = _normalized_key(key)
    return normalized in sensitive_keys or normalized.endswith(_SENSITIVE_SUFFIXES)


def _canonical_request_value(
    value: Any,
    *,
    sensitive_keys: frozenset[str] | None,
    seen: set[int],
) -> Any:
    """Convert a JSON-shaped request into deterministic canonical data."""
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return {"__float__": "nan"}
        if math.isinf(value):
            return {"__float__": "inf" if value > 0 else "-inf"}
        return value
    if isinstance(value, bytes):
        return {
            "__bytes_sha256__": hashlib.sha256(value).hexdigest(),
            "length": len(value),
        }
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return _canonical_request_value(
            value.value,
            sensitive_keys=sensitive_keys,
            seen=seen,
        )

    object_id = id(value)
    if object_id in seen:
        raise TypeError("request contains a reference cycle")

    if is_dataclass(value):
        seen.add(object_id)
        try:
            output: dict[str, Any] = {}
            for item in fields(value):
                if sensitive_keys is not None and _is_sensitive_key(
                    item.name,
                    sensitive_keys,
                ):
                    output[item.name] = "[REDACTED]"
                else:
                    output[item.name] = _canonical_request_value(
                        getattr(value, item.name),
                        sensitive_keys=sensitive_keys,
                        seen=seen,
                    )
            return output
        finally:
            seen.remove(object_id)

    if isinstance(value, Mapping):
        seen.add(object_id)
        try:
            output: dict[str, Any] = {}
            for raw_key in sorted(value, key=lambda item: str(item)):
                if not isinstance(raw_key, str):
                    raise TypeError(
                        "replay request mappings must use string keys; "
                        f"got {type(raw_key).__qualname__}"
                    )
                key = raw_key
                if sensitive_keys is not None and _is_sensitive_key(
                    key,
                    sensitive_keys,
                ):
                    output[key] = "[REDACTED]"
                else:
                    output[key] = _canonical_request_value(
                        value[raw_key],
                        sensitive_keys=sensitive_keys,
                        seen=seen,
                    )
            return output
        finally:
            seen.remove(object_id)

    if isinstance(value, (list, tuple)):
        seen.add(object_id)
        try:
            return [
                _canonical_request_value(
                    item,
                    sensitive_keys=sensitive_keys,
                    seen=seen,
                )
                for item in value
            ]
        finally:
            seen.remove(object_id)

    if isinstance(value, (set, frozenset)):
        seen.add(object_id)
        try:
            canonical_items = [
                _canonical_request_value(
                    item,
                    sensitive_keys=sensitive_keys,
                    seen=seen,
                )
                for item in value
            ]
        finally:
            seen.remove(object_id)
        return sorted(
            canonical_items,
            key=lambda item: json.dumps(
                item,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )

    raise TypeError(
        "replay requests must contain JSON-compatible values; "
        f"unsupported value type: {type(value).__qualname__}"
    )


def _request_digest(
    request: Mapping[str, Any],
    *,
    sensitive_keys: frozenset[str] | None,
) -> str:
    canonical = _canonical_request_value(
        request,
        sensitive_keys=sensitive_keys,
        seen=set(),
    )
    payload = json.dumps(
        canonical,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def request_fingerprint(
    request: Mapping[str, Any],
    *,
    additional_sensitive_keys: Iterable[str] = (),
) -> str:
    """Return a stable public fingerprint without credential values.

    The public fingerprint is intentionally unsuitable as a candidate-execution
    cache key: requests that differ only by a credential have the same public
    fingerprint. The replay engine uses a separate non-exported digest that
    includes credential values, preventing cross-credential execution reuse.
    """
    if not isinstance(request, Mapping):
        raise TypeError("request must be a mapping")
    return _request_digest(
        request,
        sensitive_keys=_sensitive_key_set(additional_sensitive_keys),
    )


def _isolated_copy(value: Any, *, label: str) -> Any:
    """Deep-copy replay data or fail before mutable state can be shared."""
    try:
        return copy.deepcopy(value)
    except Exception as error:
        raise TypeError(
            f"{label} must support deepcopy for replay isolation"
        ) from error


def _safe_report_value(
    value: Any,
    *,
    sensitive_keys: frozenset[str],
    include_object_repr: bool = False,
    seen: set[int] | None = None,
) -> Any:
    """Return redacted, JSON-serializable report data."""
    if seen is None:
        seen = set()
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        return str(value)
    if isinstance(value, bytes):
        return {
            "__bytes_sha256__": hashlib.sha256(value).hexdigest(),
            "length": len(value),
        }
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return _safe_report_value(
            value.value,
            sensitive_keys=sensitive_keys,
            include_object_repr=include_object_repr,
            seen=seen,
        )

    object_id = id(value)
    if object_id in seen:
        return "[CYCLE]"

    if is_dataclass(value):
        seen.add(object_id)
        try:
            output: dict[str, Any] = {}
            for item in fields(value):
                if _is_sensitive_key(item.name, sensitive_keys):
                    output[item.name] = "[REDACTED]"
                else:
                    output[item.name] = _safe_report_value(
                        getattr(value, item.name),
                        sensitive_keys=sensitive_keys,
                        include_object_repr=include_object_repr,
                        seen=seen,
                    )
            return output
        finally:
            seen.remove(object_id)

    if isinstance(value, Mapping):
        seen.add(object_id)
        try:
            output: dict[str, Any] = {}
            for raw_key in sorted(value, key=lambda item: str(item)):
                key = str(raw_key)
                if _is_sensitive_key(key, sensitive_keys):
                    output[key] = "[REDACTED]"
                else:
                    output[key] = _safe_report_value(
                        value[raw_key],
                        sensitive_keys=sensitive_keys,
                        include_object_repr=include_object_repr,
                        seen=seen,
                    )
            return output
        finally:
            seen.remove(object_id)

    if isinstance(value, (list, tuple)):
        seen.add(object_id)
        try:
            return [
                _safe_report_value(
                    item,
                    sensitive_keys=sensitive_keys,
                    include_object_repr=include_object_repr,
                    seen=seen,
                )
                for item in value
            ]
        finally:
            seen.remove(object_id)

    if isinstance(value, (set, frozenset)):
        seen.add(object_id)
        try:
            items = [
                _safe_report_value(
                    item,
                    sensitive_keys=sensitive_keys,
                    include_object_repr=include_object_repr,
                    seen=seen,
                )
                for item in value
            ]
        finally:
            seen.remove(object_id)
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True))

    output = {"__type__": type(value).__qualname__}
    if include_object_repr:
        output["repr"] = repr(value)
    return output
