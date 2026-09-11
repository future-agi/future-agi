from __future__ import annotations

import threading

import structlog

from tfc.licensing.types import LicenseSnapshot, LicenseState, MISSING_LICENSE

logger = structlog.get_logger(__name__)

_lock = threading.Lock()
_current_snapshot: LicenseSnapshot = MISSING_LICENSE


def get_snapshot() -> LicenseSnapshot:
    return _current_snapshot


def set_snapshot(snapshot: LicenseSnapshot) -> None:
    global _current_snapshot
    with _lock:
        _current_snapshot = snapshot
        logger.info(
            "license_state_updated",
            state=snapshot.state.value,
            license_id=snapshot.license_id,
        )


class _LicenseResolver:
    """Implements tfc.capabilities.service.LicenseResolver protocol."""

    def get_snapshot(self) -> LicenseSnapshot:
        return get_snapshot()


def get_resolver() -> _LicenseResolver:
    return _LicenseResolver()
