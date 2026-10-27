from __future__ import annotations

from datetime import UTC, datetime

from tfc.licensing.types import LicenseSnapshot, LicenseState


def test_startup_without_license_key_resets_missing_snapshot(settings):
    from ee.licensing import state
    from ee.licensing.startup import validate_on_startup

    state.set_snapshot(
        LicenseSnapshot(
            state=LicenseState.ACTIVE,
            validated_at=datetime.now(UTC),
        )
    )
    settings.EE_LICENSE_KEY = ""
    settings.EE_LICENSE_PUBLIC_KEY = ""
    settings.EE_LICENSE_PUBLIC_KEYS = ""

    validate_on_startup()

    assert state.get_snapshot().state == LicenseState.MISSING


def test_startup_with_invalid_license_sets_invalid_snapshot(settings, monkeypatch):
    from ee.licensing import state
    from ee.licensing.startup import validate_on_startup
    from tfc.licensing.types import MISSING_LICENSE

    state.set_snapshot(MISSING_LICENSE)
    settings.EE_LICENSE_KEY = "bad-license"
    invalid_snapshot = LicenseSnapshot(
        state=LicenseState.INVALID,
        validated_at=datetime.now(UTC),
    )
    monkeypatch.setattr("ee.licensing.keyring.load_keyring_from_settings", lambda: None)
    monkeypatch.setattr("ee.licensing.validator.validate", lambda license_key: invalid_snapshot)

    validate_on_startup()

    assert state.get_snapshot().state == LicenseState.INVALID


def test_empty_keyring_settings_clear_stale_keys(settings):
    from ee.licensing import keyring
    from ee.licensing.keyring import PublicKeyEntry

    keyring._KEY_RING = {
        "old": PublicKeyEntry(kid="old", algorithm="RS256", public_key="stale")
    }
    settings.EE_LICENSE_PUBLIC_KEY = ""
    settings.EE_LICENSE_PUBLIC_KEYS = ""

    keyring.load_keyring_from_settings()

    assert keyring.has_any_keys() is False


def test_malformed_keyring_settings_fail_closed(settings):
    from ee.licensing import keyring
    from ee.licensing.keyring import PublicKeyEntry

    keyring._KEY_RING = {
        "old": PublicKeyEntry(kid="old", algorithm="RS256", public_key="stale")
    }
    settings.EE_LICENSE_PUBLIC_KEY = ""
    settings.EE_LICENSE_PUBLIC_KEYS = "{}"

    keyring.load_keyring_from_settings()

    assert keyring.has_any_keys() is False
