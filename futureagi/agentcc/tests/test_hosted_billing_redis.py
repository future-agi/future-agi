"""Decidable hosted settlement mismatches fail before startup, value-free."""

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from agentcc.hosted import validate_hosted_configuration
from agentcc.tests.test_hosted_startup import hosted_environment, hosted_settings


@pytest.mark.parametrize(
    "url",
    [
        "redis://private-user:private-password@redis:6379/0",
        "redis://private-user:private-password@redis:6379/4?db=0",
        "redis://private-user:private-password@redis:6379",
    ],
)
def test_hosted_billing_redis_mismatch_refused_without_values(url):
    env = hosted_environment()
    env.update(REDIS_URL=url, AGENTCC_REDIS_DB="4")
    with (
        override_settings(**hosted_settings(env)),
        pytest.raises(ImproperlyConfigured) as failure,
    ):
        validate_hosted_configuration(env)
    assert "REDIS_URL" in str(failure.value) and "AGENTCC_REDIS_DB" in str(
        failure.value
    )
    for private in (url, "private-user", "private-password", "6379"):
        assert private not in str(failure.value)


@pytest.mark.parametrize("url", ["redis://redis:6379/4", "redis://redis:6379/0?db=4"])
def test_hosted_billing_redis_matching_effective_database(url):
    env = hosted_environment()
    env.update(REDIS_URL=url, AGENTCC_REDIS_DB="4")
    with override_settings(**hosted_settings(env)):
        validate_hosted_configuration(env)


def test_hosted_billing_redis_checks_effective_settings_not_just_environment():
    env = hosted_environment()
    env.update(REDIS_URL="redis://redis:6379/4", AGENTCC_REDIS_DB="4")
    config = hosted_settings(env)
    config["REDIS_URL"] = "redis://redis:6379/0"
    with (
        override_settings(**config),
        pytest.raises(ImproperlyConfigured, match="REDIS_URL"),
    ):
        validate_hosted_configuration(env)


@pytest.mark.parametrize("database", ["not-a-db", "-1", "4.5"])
def test_hosted_billing_redis_bad_expected_database(database):
    env = hosted_environment()
    env["AGENTCC_REDIS_DB"] = database
    with (
        override_settings(**hosted_settings(env)),
        pytest.raises(ImproperlyConfigured, match="AGENTCC_REDIS_DB"),
    ):
        validate_hosted_configuration(env)
