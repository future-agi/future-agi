from __future__ import annotations

import structlog
from django.conf import settings

logger = structlog.get_logger(__name__)


def validate_on_startup() -> None:
    from ee.licensing.keyring import load_keyring_from_settings
    from ee.licensing.validator import validate
    from ee.licensing.state import set_snapshot

    load_keyring_from_settings()

    license_key = getattr(settings, "EE_LICENSE_KEY", "")
    snapshot = validate(license_key)
    set_snapshot(snapshot)

    if not license_key:
        logger.info("license_startup_no_key_configured")
    elif snapshot.state.value == "invalid":
        logger.warning(
            "license_startup_invalid",
            state=snapshot.state.value,
            denial_reason=snapshot.denial_reason.value if snapshot.denial_reason else None,
        )
    elif snapshot.is_usable:
        logger.info(
            "license_startup_active",
            state=snapshot.state.value,
            band=snapshot.band,
            features_count=len(snapshot.features),
            expires_at=snapshot.expires_at.isoformat() if snapshot.expires_at else None,
        )
    else:
        logger.warning(
            "license_startup_unusable",
            state=snapshot.state.value,
        )
