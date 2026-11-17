"""Operator-owned deployment policy. Import/startup performs no I/O.

This preparation profile does not authorize a deployment, credential rotation,
MCP/credits activation, or startup database mutations.
"""

import base64
import os
import sys
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from agentcc.admin_token import admin_token_problem


def deployment_profile(env=None):
    env = os.environ if env is None else env
    profile = env.get("AGENTCC_DEPLOYMENT_PROFILE", "")
    if profile not in ("", "self-hosted", "hosted"):
        raise ImproperlyConfigured(
            "AGENTCC_DEPLOYMENT_PROFILE must be self-hosted or hosted"
        )
    return profile


def validate_billing_redis_database(env):
    """Check a declared gateway DB without opening Redis or exposing its URL.

    Custom deployments must pass the gateway's effective AGENTCC_REDIS_DB to
    the backend/worker too. If it is absent the remote gateway DB is not locally
    decidable; do not assume that every installation uses the example's DB 4.
    """
    if "AGENTCC_REDIS_DB" not in env:
        return
    from redis.connection import parse_url

    try:
        expected = int(env["AGENTCC_REDIS_DB"])
        if expected < 0:
            raise ValueError
        # Use redis-py's precedence: a ?db= query overrides a URL path. Check
        # the effective setting actually read by the settlement drain as well
        # as the environment that the operator supplied.
        for url in (env.get("REDIS_URL", ""), getattr(settings, "REDIS_URL", "")):
            parsed = parse_url(url)
            if parsed.get("db", 0) != expected:
                raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ImproperlyConfigured(
            "Hosted settlement drain requires REDIS_URL database to match AGENTCC_REDIS_DB"
        ) from None


def validate_hosted_configuration(env=None):
    """Shared by API, workers and operator jobs via AgentccConfig.ready.

    Errors contain setting names only. Never contact a secret service, decrypt
    stored data or initialize a database from the application startup seam.
    """
    env = os.environ if env is None else env
    if deployment_profile(env) != "hosted":
        return
    if env.get("ENV_TYPE") not in ("prod", "production", "staging"):
        raise ImproperlyConfigured("Hosted profile requires a production ENV_TYPE")
    if getattr(settings, "DEBUG", False) or env.get("DEBUG", "false").lower() not in (
        "false",
        "0",
        "",
    ):
        raise ImproperlyConfigured("Hosted profile refuses DEBUG")
    if env.get("NO_STARTUP_DB_MUTATIONS") != "true":
        from model_hub.apps import explicit_management_mutation_authorized

        if not (
            env.get("SERVICE_TYPE") == "bootstrap"
            and env.get("STARTUP_DB_MUTATION_MODE") == "operator"
            and explicit_management_mutation_authorized(sys.argv)
        ):
            raise ImproperlyConfigured(
                "Hosted profile requires NO_STARTUP_DB_MUTATIONS=true outside authorized operator jobs"
            )
    for name in (
        "AGENTCC_ADMIN_TOKEN",
        "AGENTCC_INTERNAL_API_KEY",
        "AGENTCC_WEBHOOK_SECRET",
    ):
        if admin_token_problem(env.get(name, "")):
            raise ImproperlyConfigured(f"Hosted profile requires valid {name}")
    for name in (
        "AGENTCC_ALLOW_INSECURE_ADMIN_TOKEN",
        "AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS",
    ):
        if env.get(name, "") not in ("", "false", "0"):
            raise ImproperlyConfigured(f"Hosted profile refuses {name}")
    if env.get("DJANGO_CACHE_BACKEND") == "locmem":
        raise ImproperlyConfigured("Hosted profile refuses DJANGO_CACHE_BACKEND=locmem")
    cache = getattr(settings, "CACHES", {}).get("default", {})
    if cache.get("BACKEND") not in (
        "django_redis.cache.RedisCache",
        "django.core.cache.backends.redis.RedisCache",
    ) or not isinstance(cache.get("LOCATION"), str):
        raise ImproperlyConfigured(
            "Hosted profile requires shared Redis CACHES.default"
        )
    try:
        cache_url = urlsplit(cache["LOCATION"])
        if cache_url.scheme not in ("redis", "rediss") or not cache_url.hostname:
            raise ValueError("invalid cache URI")
        _ = cache_url.port
    except ValueError:
        raise ImproperlyConfigured(
            "Hosted profile requires valid CACHES.default.LOCATION"
        ) from None
    # Test-only destination overrides must never become a hosted configuration.
    for name, value in env.items():
        if value and (
            name.startswith("AGENTCC_TEST_") or name.startswith("INTEGRATIONS_TEST_")
        ):
            raise ImproperlyConfigured("Hosted profile refuses test endpoint overrides")
    for name, schemes in (
        ("AGENTCC_GATEWAY_INTERNAL_URL", ("http", "https")),
        ("REDIS_URL", ("redis", "rediss")),
    ):
        try:
            parsed = urlsplit(env.get(name, ""))
            valid = parsed.scheme in schemes and bool(parsed.hostname)
            if name == "AGENTCC_GATEWAY_INTERNAL_URL":
                valid = (
                    valid
                    and parsed.username is None
                    and not parsed.query
                    and not parsed.fragment
                )
            _ = parsed.port
        except ValueError:
            valid = False
        if not valid:
            raise ImproperlyConfigured(f"Hosted profile requires valid {name}")
    validate_billing_redis_database(env)
    key = env.get("INTEGRATION_ENCRYPTION_KEY", "")
    if (
        not key
        or getattr(settings, "INTEGRATION_ENCRYPTION_KEY_EPHEMERAL", False)
        or key != getattr(settings, "INTEGRATION_ENCRYPTION_KEY", "")
    ):
        raise ImproperlyConfigured(
            "Hosted profile requires stable INTEGRATION_ENCRYPTION_KEY"
        )
    try:
        decoded = base64.b64decode(key.encode("ascii"), altchars=b"-_", validate=True)
        if (
            len(decoded) != 32
            or base64.urlsafe_b64encode(decoded).decode("ascii") != key
        ):
            raise ValueError("noncanonical encryption key")
        Fernet(key.encode("ascii"))
    except (ValueError, TypeError, UnicodeError):
        raise ImproperlyConfigured(
            "Hosted profile requires valid INTEGRATION_ENCRYPTION_KEY"
        ) from None
