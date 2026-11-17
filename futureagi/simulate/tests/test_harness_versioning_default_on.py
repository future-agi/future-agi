"""Default-on versioning keeps an explicit off switch and never invents run pins."""

from types import SimpleNamespace

import pytest

from simulate.services.harness_versioning import read, versions
from simulate.services.hosted_harness import HostedHarnessError


@pytest.fixture
def flags(monkeypatch):
    # Replace module references only: do not depend on or change deployment settings.
    configured = SimpleNamespace()
    monkeypatch.setattr(versions, "settings", configured)
    monkeypatch.setattr(read, "settings", configured)
    return configured


def test_versioning_is_enabled_when_the_setting_is_absent(flags):
    assert versions.versioning_enabled() is True


@pytest.mark.parametrize("enabled", [False, True])
def test_explicit_versioning_setting_is_preserved(flags, enabled):
    flags.HARNESS_VERSIONING_ENABLED = enabled
    assert versions.versioning_enabled() is enabled


def test_default_flags_report_enabled_without_enabling_nudges(flags):
    assert read.versioning_flags() == {"enabled": True, "nudges_enabled": False}


def test_configured_nudges_follow_default_on_versioning(flags):
    flags.HARNESS_VERSIONING_NUDGES_ENABLED = True
    assert read.versioning_flags() == {"enabled": True, "nudges_enabled": True}


def test_explicit_off_disables_versioning_and_nudges(flags):
    flags.HARNESS_VERSIONING_ENABLED = False
    flags.HARNESS_VERSIONING_NUDGES_ENABLED = True
    assert read.versioning_flags() == {"enabled": False, "nudges_enabled": False}


def test_endpoint_guard_allows_the_default_on_state(flags):
    read.require_versioning()


def test_endpoint_guard_keeps_the_explicit_off_response(flags):
    flags.HARNESS_VERSIONING_ENABLED = False
    with pytest.raises(HostedHarnessError) as raised:
        read.require_versioning()
    assert raised.value.code == "versioning_disabled"
    assert raised.value.status_code == 404


@pytest.mark.parametrize(
    ("environment_id", "suite_id", "expected"),
    [
        (None, None, False),
        ("environment-version", None, False),
        (None, "suite-version", False),
        ("environment-version", "suite-version", True),
    ],
)
def test_default_on_pins_runs_only_after_both_versions_exist(
    flags, environment_id, suite_id, expected
):
    environment = SimpleNamespace(
        environment_version_id=environment_id,
        suite_version_id=suite_id,
    )
    assert versions.pins_runs(environment) is expected


def test_explicit_off_does_not_pin_even_a_versioned_environment(flags):
    flags.HARNESS_VERSIONING_ENABLED = False
    environment = SimpleNamespace(
        environment_version_id="environment-version",
        suite_version_id="suite-version",
    )
    assert versions.pins_runs(environment) is False


@pytest.mark.django_db
def test_first_archive_write_versions_an_environment_created_while_off(
    settings, monkeypatch, user, workspace
):
    from simulate.tests.test_harness_versioning_record import _authoring, _environment
    from simulate.tests.test_hosted_harness_conversation import _Storage
    from tfc.utils import storage_client

    storage = _Storage()
    monkeypatch.setattr(storage_client, "_client", storage)
    settings.HARNESS_VERSIONING_ENABLED = False
    environment = _environment(user, workspace, "default-on-upgrade")
    assert environment.environment_version_id is None
    assert environment.suite_version_id is None
    assert versions.pins_runs(environment) is False

    # The service now sees the code's missing-setting default, not a forced True.
    monkeypatch.setattr(versions, "settings", SimpleNamespace())
    recorded = versions.record_authoring(environment, _authoring(), origin="authoring")
    assert recorded is not None
    environment.refresh_from_db()
    assert environment.environment_version_id == recorded.environment.id
    assert environment.suite_version_id == recorded.suite.id
    assert versions.is_versioned(environment) is True
    assert versions.pins_runs(environment) is True
