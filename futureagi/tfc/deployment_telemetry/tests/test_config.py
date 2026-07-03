from unittest.mock import patch

import pytest

from tfc.deployment_telemetry.config import (
    detect_deployment_type,
    get_telemetry_interval_hours,
    get_telemetry_jitter_seconds,
    get_version,
    is_self_hosted_deployment,
    telemetry_is_disabled,
)


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_disabled_values(monkeypatch, value):
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", value)
    assert telemetry_is_disabled() is True


@pytest.mark.parametrize("interval", [1, 2, 3, 4, 6, 8, 12, 24])
def test_valid_intervals(monkeypatch, interval):
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_INTERVAL_HOURS", str(interval))
    assert get_telemetry_interval_hours() == interval


@pytest.mark.parametrize("interval", ["bad", "0", "5", "7", "10", "25"])
def test_invalid_intervals_fall_back_to_six(monkeypatch, interval):
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_INTERVAL_HOURS", interval)
    assert get_telemetry_interval_hours() == 6



def test_default_jitter_is_thirty_minutes(monkeypatch):
    monkeypatch.delenv("FUTURE_AGI_TELEMETRY_JITTER_SECONDS", raising=False)
    assert get_telemetry_jitter_seconds() == 1800


@pytest.mark.parametrize("value", ["0", "45", "1800"])
def test_valid_jitter(monkeypatch, value):
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_JITTER_SECONDS", value)
    assert get_telemetry_jitter_seconds() == int(value)


@pytest.mark.parametrize("value", ["bad", "-1"])
def test_invalid_jitter_falls_back_to_thirty_minutes(monkeypatch, value):
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_JITTER_SECONDS", value)
    assert get_telemetry_jitter_seconds() == 1800


def test_version_fallback_chain(monkeypatch):
    monkeypatch.delenv("FUTURE_AGI_VERSION", raising=False)
    monkeypatch.delenv("SERVICE_VERSION", raising=False)
    monkeypatch.setenv("GIT_SHA", "git-version")
    assert get_version() == "git-version"

    monkeypatch.setenv("SERVICE_VERSION", "service-version")
    assert get_version() == "service-version"

    monkeypatch.setenv("FUTURE_AGI_VERSION", "release-version")
    assert get_version() == "release-version"


def test_deployment_detection(monkeypatch):
    monkeypatch.setenv("FUTURE_AGI_DEPLOYMENT_TYPE", "custom")
    assert detect_deployment_type() == "custom"

    monkeypatch.delenv("FUTURE_AGI_DEPLOYMENT_TYPE")
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "kubernetes")
    assert detect_deployment_type() == "kubernetes"

    monkeypatch.delenv("KUBERNETES_SERVICE_HOST")
    with patch(
        "tfc.deployment_telemetry.config.os.path.exists",
        return_value=True,
    ):
        assert detect_deployment_type() == "docker"

    with patch(
        "tfc.deployment_telemetry.config.os.path.exists",
        return_value=False,
    ):
        assert detect_deployment_type() == "bare_metal"


def test_oss_deployment_sends_telemetry():
    with patch("tfc.ee_loader.has_ee", return_value=False):
        assert is_self_hosted_deployment() is True


def test_self_hosted_ee_deployment_sends_telemetry():
    with (
        patch("tfc.ee_loader.has_ee", return_value=True),
        patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=False),
    ):
        assert is_self_hosted_deployment() is True


def test_cloud_deployment_does_not_send_telemetry():
    with (
        patch("tfc.ee_loader.has_ee", return_value=True),
        patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=True),
    ):
        assert is_self_hosted_deployment() is False


def test_ee_mode_detection_failure_assumes_self_hosted():
    """Half-installed EE (``DeploymentMode`` symbol missing) must default to
    self-hosted so the install still phones home with a logged warning,
    instead of silently going dark."""
    with (
        patch("tfc.ee_loader.has_ee", return_value=True),
        patch(
            "ee.usage.deployment.DeploymentMode.is_cloud",
            side_effect=AttributeError,
        ),
    ):
        assert is_self_hosted_deployment() is True


def test_unexpected_ee_mode_error_propagates():
    """The narrow ``except (ImportError, AttributeError)`` is intentional —
    a ``RuntimeError`` from ``DeploymentMode.is_cloud`` is a real bug we
    want surfaced, not silently coerced to a deployment-mode answer."""
    import pytest

    with (
        patch("tfc.ee_loader.has_ee", return_value=True),
        patch(
            "ee.usage.deployment.DeploymentMode.is_cloud",
            side_effect=RuntimeError,
        ),
        pytest.raises(RuntimeError),
    ):
        is_self_hosted_deployment()
