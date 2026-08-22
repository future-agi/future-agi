"""Credential-aware canonicalization and report serialization helpers."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
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
_AUTHORIZATION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(Bearer|Basic)\s+[^\s,;]+",
    re.IGNORECASE,
)
_CREDENTIAL_ASSIGNMENT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b("
    r"api[ _-]?key|access[ _-]?token|refresh[ _-]?token|session[ _-]?token|"
    r"client[ _-]?secret|password|passwd|secret(?:[ _-]?key)?|authorization"
    r")\b(\s*[:=]\s*)[^\s,;]+",
    re.IGNORECASE,
)
_COMMON_API_KEY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(?:sk|gsk|hf|xai)[-_][A-Za-z0-9._-]{8,}\b",
    re.IGNORECASE,
)


def _normalized_key(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _sensitive_key_set(additional_sensitive_keys: Iterable[str]) -> frozenset[str]:
    if isinstance(additional_sensitive_keys, (str, bytes)):
        raise TypeError("additional sensitive keys must be an iterable of strings")
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


def _leaf_text_values(value: Any, *, seen: set[int]) -> set[str]:
    """Collect scalar text nested beneath a credential-shaped field."""
    if value is None:
        return set()
    if isinstance(value, str):
        return {value} if value else set()
    if isinstance(value, bytes):
        decoded = value.decode("utf-8", errors="ignore")
        return {decoded} if decoded else set()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {str(value)}
    if isinstance(value, Path):
        return {str(value)}
    if isinstance(value, Enum):
        return _leaf_text_values(value.value, seen=seen)

    object_id = id(value)
    if object_id in seen:
        return set()

    if is_dataclass(value) and not isinstance(value, type):
        seen.add(object_id)
        try:
            collected: set[str] = set()
            for item in fields(value):
                collected.update(
                    _leaf_text_values(getattr(value, item.name), seen=seen)
                )
            return collected
        finally:
            seen.remove(object_id)

    if isinstance(value, Mapping):
        seen.add(object_id)
        try:
            collected = set()
            for item in value.values():
                collected.update(_leaf_text_values(item, seen=seen))
            return collected
        finally:
            seen.remove(object_id)

    if isinstance(value, (list, tuple, set, frozenset)):
        seen.add(object_id)
        try:
            collected = set()
            for item in value:
                collected.update(_leaf_text_values(item, seen=seen))
            return collected
        finally:
            seen.remove(object_id)

    return set()


def _sensitive_values(
    value: Any,
    *,
    sensitive_keys: frozenset[str],
    seen: set[int] | None = None,
) -> frozenset[str]:
    """Collect credential values that can be removed from diagnostic text."""
    if seen is None:
        seen = set()
    if value is None or isinstance(value, (str, bytes, int, float, bool, Path)):
        return frozenset()
    if isinstance(value, Enum):
        return _sensitive_values(
            value.value,
            sensitive_keys=sensitive_keys,
            seen=seen,
        )

    object_id = id(value)
    if object_id in seen:
        return frozenset()

    collected: set[str] = set()
    if is_dataclass(value) and not isinstance(value, type):
        seen.add(object_id)
        try:
            for item in fields(value):
                item_value = getattr(value, item.name)
                if _is_sensitive_key(item.name, sensitive_keys):
                    collected.update(_leaf_text_values(item_value, seen=set()))
                else:
                    collected.update(
                        _sensitive_values(
                            item_value,
                            sensitive_keys=sensitive_keys,
                            seen=seen,
                        )
                    )
        finally:
            seen.remove(object_id)
        return frozenset(collected)

    if isinstance(value, Mapping):
        seen.add(object_id)
        try:
            for raw_key, item_value in value.items():
                if isinstance(raw_key, str) and _is_sensitive_key(
                    raw_key,
                    sensitive_keys,
                ):
                    collected.update(_leaf_text_values(item_value, seen=set()))
                else:
                    collected.update(
                        _sensitive_values(
                            item_value,
                            sensitive_keys=sensitive_keys,
                            seen=seen,
                        )
                    )
        finally:
            seen.remove(object_id)
        return frozenset(collected)

    if isinstance(value, (list, tuple, set, frozenset)):
        seen.add(object_id)
        try:
            for item in value:
                collected.update(
                    _sensitive_values(
                        item,
                        sensitive_keys=sensitive_keys,
                        seen=seen,
                    )
                )
        finally:
            seen.remove(object_id)
    return frozenset(collected)


def _redact_text(
    value: str,
    *,
    sensitive_values: Iterable[str] = (),
) -> str:
    """Remove known credentials and common credential forms from free text."""
    redacted = value
    known_values = {
        item
        for item in sensitive_values
        if isinstance(item, str) and item
    }
    for secret in sorted(known_values, key=len, reverse=True):
        redacted = redacted.replace(secret, "[REDACTED]")
    redacted = _AUTHORIZATION_PATTERN.sub(
        lambda match: f"{match.group(1)} [REDACTED]",
        redacted,
    )
    redacted = _CREDENTIAL_ASSIGNMENT_PATTERN.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]",
        redacted,
    )
    return _COMMON_API_KEY_PATTERN.sub("[REDACTED]", redacted)


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

    if is_dataclass(value) and not isinstance(value, type):
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
            raw_keys = list(value)
            for raw_key in raw_keys:
                if not isinstance(raw_key, str):
                    raise TypeError(
                        "replay request mappings must use string keys; "
                        f"got {type(raw_key).__qualname__}"
                    )
            for key in sorted(raw_keys):
                if sensitive_keys is not None and _is_sensitive_key(
                    key,
                    sensitive_keys,
                ):
                    output[key] = "[REDACTED]"
                else:
                    output[key] = _canonical_request_value(
                        value[key],
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
    sensitive_values: frozenset[str] | None = None,
    seen: set[int] | None = None,
) -> Any:
    """Return redacted, JSON-serializable report data."""
    if seen is None:
        seen = set()
    if sensitive_values is None:
        try:
            sensitive_values = _sensitive_values(
                value,
                sensitive_keys=sensitive_keys,
            )
        except Exception:
            sensitive_values = frozenset()
    if value is None or isinstance(value, (int, bool)):
        return value
    if isinstance(value, str):
        return _redact_text(value, sensitive_values=sensitive_values)
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
        return _redact_text(
            str(value),
            sensitive_values=sensitive_values,
        )
    if isinstance(value, Enum):
        return _safe_report_value(
            value.value,
            sensitive_keys=sensitive_keys,
            sensitive_values=sensitive_values,
            seen=seen,
        )

    object_id = id(value)
    if object_id in seen:
        return "[CYCLE]"

    if is_dataclass(value) and not isinstance(value, type):
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
                        sensitive_values=sensitive_values,
                        seen=seen,
                    )
            return output
        finally:
            seen.remove(object_id)

    if isinstance(value, Mapping):
        seen.add(object_id)
        try:
            output: dict[str, Any] = {}
            reserved_keys = {
                raw_key for raw_key in value if isinstance(raw_key, str)
            }
            for index, (raw_key, item_value) in enumerate(value.items()):
                if isinstance(raw_key, str):
                    key = raw_key
                else:
                    base_key = f"__key_{index}_{type(raw_key).__name__}"
                    key = base_key
                    suffix = 1
                    while key in reserved_keys or key in output:
                        key = f"{base_key}_{suffix}"
                        suffix += 1
                if isinstance(raw_key, str) and _is_sensitive_key(
                    key,
                    sensitive_keys,
                ):
                    output[key] = "[REDACTED]"
                else:
                    output[key] = _safe_report_value(
                        item_value,
                        sensitive_keys=sensitive_keys,
                        sensitive_values=sensitive_values,
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
                    sensitive_values=sensitive_values,
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
                    sensitive_values=sensitive_values,
                    seen=seen,
                )
                for item in value
            ]
        finally:
            seen.remove(object_id)
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True))

    return {"__type__": type(value).__name__}
