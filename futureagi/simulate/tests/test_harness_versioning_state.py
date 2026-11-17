"""Availability reports persisted version readiness without inventing progress."""

from types import SimpleNamespace

import pytest

from simulate.services.harness_versioning import read, versions


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("nudges", [False, True])
@pytest.mark.parametrize("has_environment", [False, True])
@pytest.mark.parametrize("has_suite", [False, True])
def test_environment_state_preserves_flags_and_requires_both_version_pins(
    monkeypatch, enabled, nudges, has_environment, has_suite
):
    configured = SimpleNamespace(
        HARNESS_VERSIONING_ENABLED=enabled,
        HARNESS_VERSIONING_NUDGES_ENABLED=nudges,
    )
    monkeypatch.setattr(versions, "settings", configured)
    monkeypatch.setattr(read, "settings", configured)
    environment = SimpleNamespace(
        environment_version_id="environment-version" if has_environment else None,
        suite_version_id="suite-version" if has_suite else None,
    )
    expected_state = (
        "off"
        if not enabled
        else "ready"
        if has_environment and has_suite
        else "enabling"
    )
    assert read.versioning_flags(environment=environment) == {
        "enabled": enabled,
        "nudges_enabled": enabled and nudges,
        "state": expected_state,
    }


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("nudges", [False, True])
def test_callers_without_an_environment_keep_the_original_flags_shape(
    monkeypatch, enabled, nudges
):
    configured = SimpleNamespace(
        HARNESS_VERSIONING_ENABLED=enabled,
        HARNESS_VERSIONING_NUDGES_ENABLED=nudges,
    )
    monkeypatch.setattr(versions, "settings", configured)
    monkeypatch.setattr(read, "settings", configured)
    assert read.versioning_flags() == {
        "enabled": enabled,
        "nudges_enabled": enabled and nudges,
    }
