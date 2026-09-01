from __future__ import annotations

import hashlib
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import structlog

from tfc.licensing.types import (
    DenialReason,
    LicenseSnapshot,
    LicenseState,
    LicenseType,
)

from ee.licensing.keyring import (
    ALLOWED_ALGORITHMS,
    REQUIRED_AUDIENCE,
    REQUIRED_ISSUER,
    REQUIRED_TYPE,
    get_clock_skew_seconds,
    get_key,
    has_any_keys,
)

logger = structlog.get_logger(__name__)

REQUIRED_SCHEMA_VERSION = 1

MAX_STRING_LENGTH = 256
MAX_FEATURE_COUNT = 256
MAX_LIMIT_VALUE = 10**12
MAX_INSTANCES = 10_000
MAX_GRACE_DAYS = 3650

_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")


def validate(license_key: str) -> LicenseSnapshot:
    """Validate a signed license and return an immutable runtime snapshot."""
    if not license_key:
        return LicenseSnapshot(
            state=LicenseState.MISSING,
            denial_reason=DenialReason.LICENSE_MISSING,
            validated_at=datetime.now(UTC),
        )

    if not has_any_keys():
        logger.error("license_validation_no_public_keys")
        return _invalid("No public keys configured")

    try:
        unverified_header = jwt.get_unverified_header(license_key)
    except jwt.exceptions.PyJWTError:
        logger.warning("license_validation_malformed_header")
        return _invalid("Malformed token header")

    kid = unverified_header.get("kid", "default")
    algorithm = unverified_header.get("alg", "")
    if algorithm not in ALLOWED_ALGORITHMS:
        logger.warning("license_validation_bad_algorithm", algorithm=algorithm)
        return _invalid("Disallowed algorithm")

    key_entry = get_key(kid)
    if key_entry is None:
        logger.warning("license_validation_unknown_kid", kid=kid)
        return _invalid("Unknown key ID")
    if key_entry.algorithm != algorithm:
        logger.warning(
            "license_validation_algorithm_mismatch",
            kid=kid,
            expected=key_entry.algorithm,
            actual=algorithm,
        )
        return _invalid("Key algorithm mismatch")

    clock_skew = get_clock_skew_seconds()
    now = datetime.now(UTC)

    try:
        payload = jwt.decode(
            license_key,
            key_entry.public_key,
            algorithms=[key_entry.algorithm],
            issuer=REQUIRED_ISSUER,
            audience=REQUIRED_AUDIENCE,
            options={
                "verify_exp": False,
                "verify_iat": False,
                "verify_nbf": True,
                "verify_iss": True,
                "verify_aud": True,
                "require": ["iss", "aud", "iat", "nbf", "exp"],
            },
            leeway=timedelta(seconds=clock_skew),
        )
    except jwt.exceptions.PyJWTError as exc:
        logger.warning(
            "license_validation_decode_failed",
            error=exc.__class__.__name__,
        )
        return _invalid("Token verification failed")

    try:
        snapshot = _snapshot_from_payload(payload, now, clock_skew)
    except (TypeError, ValueError, OverflowError, OSError) as exc:
        logger.warning(
            "license_validation_claims_invalid",
            error=str(exc),
        )
        return _invalid("Invalid license claims")

    logger.info(
        "license_validated",
        license_id=snapshot.license_id,
        state=snapshot.state.value,
        band=snapshot.band,
        features_count=len(snapshot.features),
    )
    return snapshot


def hash_key(license_key: str) -> str:
    return hashlib.sha256(license_key.encode()).hexdigest()


def _snapshot_from_payload(
    payload: dict[str, Any],
    now: datetime,
    clock_skew_seconds: int,
) -> LicenseSnapshot:
    if payload.get("typ") != REQUIRED_TYPE:
        raise ValueError("Invalid license type marker")
    if payload.get("schema_version") != REQUIRED_SCHEMA_VERSION:
        raise ValueError("Unsupported schema version")

    license_id = _required_string(payload, "license_id")
    customer_id = _optional_string(payload, "customer_id")
    issued_to = _optional_string(payload, "issued_to")
    band = _required_string(payload, "band")
    features = _features(payload.get("features"))
    limits = _limits(payload.get("limits"))
    max_instances = _bounded_integer(
        payload.get("max_instances"), "max_instances", 1, MAX_INSTANCES
    )
    grace_days = _bounded_integer(
        payload.get("grace_days", 0), "grace_days", 0, MAX_GRACE_DAYS
    )
    min_version = _optional_semver(payload.get("min_version"), "min_version")

    try:
        license_type = LicenseType(_required_string(payload, "license_type"))
    except ValueError as exc:
        raise ValueError("Invalid license_type") from exc

    issued_at = _timestamp(payload.get("iat"), "iat")
    not_before = _timestamp(payload.get("nbf"), "nbf")
    expires_at = _timestamp(payload.get("exp"), "exp")
    if issued_at > expires_at or not_before > expires_at:
        raise ValueError("License dates are inconsistent")
    if issued_at > now + timedelta(seconds=clock_skew_seconds):
        raise ValueError("License issued-at time is in the future")

    grace_ends_at = None
    if license_type == LicenseType.PRODUCTION and grace_days:
        grace_ends_at = expires_at + timedelta(days=grace_days)

    state = _compute_state(
        now,
        expires_at,
        grace_ends_at,
        license_type,
        clock_skew_seconds,
    )

    if min_version and not _current_version_satisfies(min_version):
        return LicenseSnapshot(
            state=LicenseState.INVALID,
            denial_reason=DenialReason.LICENSE_VERSION_UNSUPPORTED,
            license_id=license_id,
            min_version=min_version,
            validated_at=now,
        )

    return LicenseSnapshot(
        state=state,
        license_type=license_type,
        license_id=license_id,
        customer_id=customer_id,
        issued_to=issued_to,
        band=band,
        features=features,
        limits=limits,
        max_instances=max_instances,
        min_version=min_version,
        issued_at=issued_at,
        expires_at=expires_at,
        grace_ends_at=grace_ends_at,
        validated_at=now,
    )


def _required_string(payload: dict[str, Any], claim: str) -> str:
    value = payload.get(claim)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{claim} must be a non-empty string")
    if len(value) > MAX_STRING_LENGTH:
        raise ValueError(f"{claim} exceeds max length {MAX_STRING_LENGTH}")
    return value


def _optional_string(payload: dict[str, Any], claim: str) -> str:
    value = payload.get(claim, "")
    if not isinstance(value, str):
        raise ValueError(f"{claim} must be a string")
    if len(value) > MAX_STRING_LENGTH:
        raise ValueError(f"{claim} exceeds max length {MAX_STRING_LENGTH}")
    return value


def _features(value: Any) -> frozenset[str]:
    if not isinstance(value, list):
        raise ValueError("features must be a list")
    if len(value) > MAX_FEATURE_COUNT:
        raise ValueError(f"features count exceeds max {MAX_FEATURE_COUNT}")
    for feature in value:
        if not isinstance(feature, str) or not feature:
            raise ValueError("features must contain non-empty strings")
        if len(feature) > MAX_STRING_LENGTH:
            raise ValueError(f"feature id exceeds max length {MAX_STRING_LENGTH}")
    return frozenset(value)


def _limits(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        raise ValueError("limits must be an object")
    if len(value) > MAX_FEATURE_COUNT:
        raise ValueError(f"limits count exceeds max {MAX_FEATURE_COUNT}")

    limits: dict[str, int] = {}
    for key, limit in value.items():
        if not isinstance(key, str) or not key:
            raise ValueError("limit names must be non-empty strings")
        if len(key) > MAX_STRING_LENGTH:
            raise ValueError(f"limit name exceeds max length {MAX_STRING_LENGTH}")
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError(f"limit {key} must be an integer")
        if limit < -1 or limit > MAX_LIMIT_VALUE:
            raise ValueError(
                f"limit {key} must be between -1 and {MAX_LIMIT_VALUE}"
            )
        limits[key] = limit
    return limits


def _bounded_integer(value: Any, claim: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{claim} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"{claim} must be between {minimum} and {maximum}")
    return value


def _timestamp(value: Any, claim: str) -> datetime:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{claim} must be a timestamp")
    return datetime.fromtimestamp(value, tz=UTC)


def _optional_semver(value: Any, claim: str) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not _SEMVER_RE.match(value):
        raise ValueError(f"{claim} must be a semver (MAJOR.MINOR.PATCH)")
    if len(value) > MAX_STRING_LENGTH:
        raise ValueError(f"{claim} exceeds max length {MAX_STRING_LENGTH}")
    return value


def _parse_semver(value: str) -> tuple[int, int, int] | None:
    match = _SEMVER_RE.match(value)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def _current_version_satisfies(min_version: str) -> bool:
    """Return True when APP_VERSION is unset (skip enforcement) or >= min_version."""
    current = os.environ.get("APP_VERSION", "").lstrip("v").strip()
    if not current:
        logger.warning("license_min_version_no_app_version", min_version=min_version)
        return True
    current_parts = _parse_semver(current)
    min_parts = _parse_semver(min_version)
    if current_parts is None or min_parts is None:
        logger.warning(
            "license_min_version_unparseable",
            current=current,
            min_version=min_version,
        )
        return True
    return current_parts >= min_parts


def _invalid(reason: str) -> LicenseSnapshot:
    logger.debug("license_validation_invalid", reason=reason)
    return LicenseSnapshot(
        state=LicenseState.INVALID,
        denial_reason=DenialReason.LICENSE_INVALID,
        validated_at=datetime.now(UTC),
    )


def _compute_state(
    now: datetime,
    expires_at: datetime,
    grace_ends_at: datetime | None,
    license_type: LicenseType,
    clock_skew_seconds: int,
) -> LicenseState:
    skew = timedelta(seconds=clock_skew_seconds)

    if now < expires_at + skew:
        if license_type == LicenseType.TRIAL:
            return LicenseState.TRIAL_ACTIVE
        return LicenseState.ACTIVE

    if license_type == LicenseType.TRIAL:
        return LicenseState.TRIAL_EXPIRED

    if grace_ends_at and now < grace_ends_at + skew:
        return LicenseState.GRACE

    return LicenseState.EXPIRED
