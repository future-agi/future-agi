"""Hosted configuration refusal at the public app startup seam, no DB calls."""

import os
import secrets
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings


def hosted_environment():
    return {
        "AGENTCC_DEPLOYMENT_PROFILE": "hosted",
        "ENV_TYPE": "production",
        "NO_STARTUP_DB_MUTATIONS": "true",
        "AGENTCC_ADMIN_TOKEN": secrets.token_hex(32),
        "AGENTCC_INTERNAL_API_KEY": secrets.token_hex(32),
        "AGENTCC_WEBHOOK_SECRET": secrets.token_hex(32),
        "AGENTCC_GATEWAY_INTERNAL_URL": "http://gateway:8080",
        "INTEGRATION_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "REDIS_URL": "redis://redis:6379/0",
    }


def hosted_settings(env):
    return {
        "INTEGRATION_ENCRYPTION_KEY": env["INTEGRATION_ENCRYPTION_KEY"],
        "INTEGRATION_ENCRYPTION_KEY_EPHEMERAL": False,
        "DEBUG": False,
        "REDIS_URL": env["REDIS_URL"],
        "CACHES": {
            "default": {
                "BACKEND": "django_redis.cache.RedisCache",
                "LOCATION": env["REDIS_URL"],
            }
        },
    }


@pytest.mark.parametrize(
    "name,value",
    [
        ("AGENTCC_DEPLOYMENT_PROFILE", "hostde"),
        ("AGENTCC_ADMIN_TOKEN", ""),
        ("AGENTCC_INTERNAL_API_KEY", ""),
        ("AGENTCC_WEBHOOK_SECRET", ""),
        ("AGENTCC_GATEWAY_INTERNAL_URL", ""),
        ("INTEGRATION_ENCRYPTION_KEY", ""),
        ("INTEGRATION_ENCRYPTION_KEY", "not-a-fernet-value"),
        ("AGENTCC_ALLOW_INSECURE_ADMIN_TOKEN", "true"),
        ("AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS", "true"),
        ("DJANGO_CACHE_BACKEND", "locmem"),
        ("ENV_TYPE", "local"),
        ("DEBUG", "true"),
        ("NO_STARTUP_DB_MUTATIONS", "false"),
        ("AGENTCC_TEST_ENDPOINT", "http://upstream.invalid"),
    ],
)
def test_hosted_startup_refuses_without_disclosing_values(name, value):
    env = hosted_environment()
    env[name] = value
    with (
        patch.dict(os.environ, env, clear=True),
        override_settings(**hosted_settings(env)),
        pytest.raises(ImproperlyConfigured) as failure,
    ):
        apps.get_app_config("prism").ready()
    for field in (
        "AGENTCC_ADMIN_TOKEN",
        "AGENTCC_INTERNAL_API_KEY",
        "INTEGRATION_ENCRYPTION_KEY",
    ):
        if env[field]:
            assert env[field] not in str(failure.value)


def test_hosted_startup_valid_without_database_access():
    env = hosted_environment()
    with (
        patch.dict(os.environ, env, clear=True),
        override_settings(**hosted_settings(env)),
    ):
        apps.get_app_config("prism").ready()


def test_hosted_startup_refuses_ephemeral_key():
    env = hosted_environment()
    with (
        patch.dict(os.environ, env, clear=True),
        override_settings(
            **{**hosted_settings(env), "INTEGRATION_ENCRYPTION_KEY_EPHEMERAL": True}
        ),
        pytest.raises(ImproperlyConfigured, match="INTEGRATION_ENCRYPTION_KEY"),
    ):
        apps.get_app_config("prism").ready()


def test_self_hosted_startup_unchanged():
    with patch.dict(
        os.environ, {"AGENTCC_DEPLOYMENT_PROFILE": "self-hosted"}, clear=True
    ):
        apps.get_app_config("prism").ready()


def test_hosted_checks_effective_cache_and_debug_settings():
    env = hosted_environment()
    for override in (
        {"DEBUG": True},
        {
            "CACHES": {
                "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
            }
        },
    ):
        with (
            patch.dict(os.environ, env, clear=True),
            override_settings(**{**hosted_settings(env), **override}),
            pytest.raises(ImproperlyConfigured),
        ):
            apps.get_app_config("prism").ready()
