"""Configuration/startup regressions; no provider posture or database claims.

Can run in a small isolated runtime with --noconftest -c /dev/null. The startup
cases invoke actual Django setup with the attachment settings module and no
installed application graph; full-product startup remains an integration gate.
All credentials, hosts, CA paths and bucket names below are synthetic.
"""

import importlib
import os
import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from tfc.utils.storage_config import (
    StorageConfigurationError,
    _environment_proxy,
    attachment_storage_settings,
    resolve_attachment_storage_config,
    resolve_storage_config,
    storage_http_client,
)


def private_config(**overrides):
    return {
        "backend": "s3",
        "region": "us-east-2",
        "quarantine_bucket": "synthetic-quarantine",
        "serving_bucket": "synthetic-serving",
        "quarantine_access_key": "synthetic-quarantine-access",
        "quarantine_secret_key": "synthetic-quarantine-secret",
        "serving_access_key": "synthetic-serving-access",
        "serving_secret_key": "synthetic-serving-secret",
        **overrides,
    }


def private_env(**overrides):
    return {
        "FI_ATTACHMENTS_ENABLED": "true",
        "FI_ATTACHMENTS_STORAGE_BACKEND": "s3",
        "FI_ATTACHMENTS_STORAGE_REGION": "us-east-2",
        "FI_ATTACHMENTS_QUARANTINE_BUCKET": "synthetic-quarantine",
        "FI_ATTACHMENTS_SERVING_BUCKET": "synthetic-serving",
        "FI_ATTACHMENTS_QUARANTINE_ACCESS_KEY": "synthetic-quarantine-access",
        "FI_ATTACHMENTS_QUARANTINE_SECRET_KEY": "synthetic-quarantine-secret",
        "FI_ATTACHMENTS_SERVING_ACCESS_KEY": "synthetic-serving-access",
        "FI_ATTACHMENTS_SERVING_SECRET_KEY": "synthetic-serving-secret",
        **overrides,
    }


class UnreadableConfig(dict):
    def get(self, *args, **kwargs):
        raise AssertionError("Disabled resolution must not inspect configuration")


def test_disabled_resolver_never_touches_unused_configuration():
    assert (
        resolve_attachment_storage_config(
            UnreadableConfig(), enabled=False, environ=UnreadableConfig()
        )
        is None
    )


@pytest.mark.parametrize(
    "env,backend,endpoint,secure,region",
    [
        ({}, "s3", "s3.amazonaws.com", True, ""),
        (
            {"STORAGE_BACKEND": "S3", "AWS_DEFAULT_REGION": "us-east-2"},
            "s3",
            "s3.amazonaws.com",
            True,
            "us-east-2",
        ),
        (
            {
                "STORAGE_BACKEND": "minio",
                "S3_ENDPOINT_URL": "http://minio:9000",
                "S3_REGION": "local",
            },
            "minio",
            "minio:9000",
            False,
            "local",
        ),
        (
            {
                "STORAGE_BACKEND": "gcs",
                "S3_ENDPOINT": "malformed://[",
                "MINIO_REGION": "eu",
            },
            "gcs",
            "storage.googleapis.com",
            True,
            "eu",
        ),
        ({"STORAGE_BACKEND": "gcs"}, "gcs", "storage.googleapis.com", True, "auto"),
        (
            {
                "S3_ENDPOINT_URL": "https://secondary.test",
                "S3_ENDPOINT": "http://primary.test:9000",
                "S3_REGION": "primary",
                "AWS_DEFAULT_REGION": "secondary",
            },
            "s3",
            "primary.test:9000",
            False,
            "primary",
        ),
        (
            {"S3_ENDPOINT": "http://store.test", "S3_SECURE": "TRUE"},
            "s3",
            "store.test",
            True,
            "",
        ),
        (
            {"S3_ENDPOINT": "https://store.test", "S3_SECURE": "false"},
            "s3",
            "store.test",
            False,
            "",
        ),
        (
            {"STORAGE_BACKEND": "minio", "S3_ENDPOINT": "storage:9000"},
            "minio",
            "storage:9000",
            False,
            "",
        ),
        ({"S3_ENDPOINT": "storage:9000"}, "s3", "storage:9000", True, ""),
        (
            {"S3_ENDPOINT": " https://bücher.test:9000/prefix?query#fragment \n"},
            "s3",
            "xn--bcher-kva.test:9000",
            True,
            "",
        ),
        ({"S3_ENDPOINT": "http://[::1]:9000"}, "s3", "[::1]:9000", False, ""),
    ],
)
def test_shared_selection_preserves_current_semantics(
    env, backend, endpoint, secure, region
):
    config = resolve_storage_config(environ=env)
    assert (config.backend, config.endpoint, config.secure, config.region) == (
        backend,
        endpoint,
        secure,
        region,
    )
    assert config.endpoint_url == f"{'https' if secure else 'http'}://{endpoint}"


@pytest.mark.parametrize(
    "raw",
    [
        "ftp://store.test",
        "https://user:synthetic-secret@store.test",
        "http://[",
        "http://store.test:0",
        "https://store.test:99999",
        "bad host",
        "https://",
        "store.test:",
    ],
)
def test_malformed_endpoint_errors_do_not_echo_configuration(raw):
    with pytest.raises(ValueError) as caught:
        resolve_storage_config(environ={"S3_ENDPOINT": raw})
    assert (
        str(caught.value) == "Storage endpoint must be a host[:port] or an HTTP(S) URL"
    )


@pytest.mark.parametrize("backend", ["s3", "minio", "gcs"])
def test_attachments_inherit_shared_selector_without_public_helpers(backend):
    config = private_config(backend="", region="")
    resolved = resolve_attachment_storage_config(
        config,
        environ={
            "STORAGE_BACKEND": backend,
            "S3_REGION": "us-east-2",
            "S3_ENDPOINT_URL": "https://internal.test",
            "MINIO_URL": "malformed://[",
            "UPLOAD_BUCKET_NAME": "public-not-private",
        },
    )
    assert resolved.storage.backend == backend
    assert resolved.quarantine_bucket == "synthetic-quarantine"
    assert resolved.serving_bucket == "synthetic-serving"
    assert resolved.storage.endpoint == (
        "storage.googleapis.com" if backend == "gcs" else "internal.test"
    )


def test_legacy_explicit_region_is_preserved():
    resolved = resolve_attachment_storage_config(
        private_config(region="eu-west-1"), environ={"S3_REGION": "us-east-2"}
    )
    assert resolved.storage.region == "eu-west-1"


@pytest.mark.parametrize(
    "shared,override,error",
    [
        ("gcs", "s3", "attachment_storage_backend_mismatch"),
        ("minio", "s3", "attachment_storage_backend_mismatch"),
        ("s3", "gcs", "attachment_storage_backend_mismatch"),
        ("s3", "disabled", "attachment_storage_backend_invalid"),
        ("s3", "other", "attachment_storage_backend_invalid"),
    ],
)
def test_selector_conflicts_fail_closed(shared, override, error):
    with pytest.raises(StorageConfigurationError, match=f"^{error}$"):
        resolve_attachment_storage_config(
            private_config(backend=override), environ={"STORAGE_BACKEND": shared}
        )


@pytest.mark.parametrize("purpose", ["quarantine", "serving"])
@pytest.mark.parametrize("part", ["access_key", "secret_key", "session_token"])
def test_explicit_missing_reference_is_never_optional(purpose, part):
    key = f"{purpose}_{part}"
    config = private_config(**{key: "", f"{key}_env": "SYNTHETIC_MISSING"})
    with pytest.raises(
        StorageConfigurationError,
        match="^attachment_storage_credential_reference_missing$",
    ):
        resolve_attachment_storage_config(config, environ={})


@pytest.mark.parametrize("part", ["access_key", "secret_key", "session_token"])
def test_ambiguous_direct_and_reference_credentials_fail(part):
    key = f"quarantine_{part}"
    config = private_config(**{key: "synthetic-direct", f"{key}_env": "SYNTHETIC_REF"})
    with pytest.raises(
        StorageConfigurationError, match="^attachment_storage_credentials_ambiguous$"
    ):
        resolve_attachment_storage_config(
            config, environ={"SYNTHETIC_REF": "synthetic-reference"}
        )


@pytest.mark.parametrize(
    "reference",
    [
        "S3_ACCESS_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SESSION_TOKEN",
        "GCS_HMAC_ACCESS_KEY",
        "MINIO_ROOT_USER",
        "MINIO_ROOT_PASSWORD",
    ],
)
def test_shared_or_root_credential_references_are_rejected(reference):
    config = private_config(
        quarantine_access_key="", quarantine_access_key_env=reference
    )
    with pytest.raises(
        StorageConfigurationError,
        match="^attachment_storage_credential_reference_shared$",
    ):
        resolve_attachment_storage_config(
            config, environ={reference: "synthetic-not-purpose-scoped"}
        )


@pytest.mark.parametrize(
    "reference", ["bad name", "BAD=VALUE", "../../private", "1INVALID"]
)
def test_reference_name_validation_does_not_echo_input(reference):
    config = private_config(serving_session_token_env=reference)
    with pytest.raises(
        StorageConfigurationError,
        match="^attachment_storage_credential_reference_invalid$",
    ):
        resolve_attachment_storage_config(config, environ={})


def test_global_credentials_and_public_bucket_are_not_fallbacks():
    config = private_config(quarantine_access_key="")
    env = {
        "AWS_ACCESS_KEY_ID": "synthetic-global",
        "S3_ACCESS_KEY": "synthetic-global",
        "UPLOAD_BUCKET_NAME": "public",
    }
    with pytest.raises(
        StorageConfigurationError, match="^attachment_storage_credentials_missing$"
    ):
        resolve_attachment_storage_config(config, environ=env)
    config = private_config(quarantine_bucket="")
    with pytest.raises(
        StorageConfigurationError, match="^attachment_storage_configuration_missing$"
    ):
        resolve_attachment_storage_config(config, environ=env)


def test_reference_credentials_resolve_to_immutable_redacted_handoff():
    env = private_env()
    for purpose in ("QUARANTINE", "SERVING"):
        for part in ("ACCESS_KEY", "SECRET_KEY", "SESSION_TOKEN"):
            variable = f"FI_ATTACHMENTS_{purpose}_{part}"
            env.pop(variable, None)
            env[f"{variable}_ENV"] = f"SYNTHETIC_{purpose}_{part}"
            env[f"SYNTHETIC_{purpose}_{part}"] = f"synthetic-{purpose}-{part}"
    settings = attachment_storage_settings(enabled=True, environ=env)
    resolved = settings["resolved"]
    assert resolved == resolve_attachment_storage_config(settings, environ=env)
    for purpose in ("quarantine", "serving"):
        credentials = getattr(resolved, f"{purpose}_credentials")
        for part in ("access_key", "secret_key", "session_token"):
            value = f"synthetic-{purpose.upper()}-{part.upper()}"
            assert getattr(credentials, part) == value
            assert settings[f"{purpose}_{part}"] == value
            assert value not in repr(resolved)
    with pytest.raises(FrozenInstanceError):
        resolved.storage.region = "changed"
    with pytest.raises(FrozenInstanceError):
        resolved.quarantine_credentials.secret_key = "changed"


def test_absent_optional_token_is_none():
    resolved = resolve_attachment_storage_config(private_config(), environ={})
    assert resolved.serving_credentials.session_token is None


def test_empty_explicit_token_reference_fails():
    with pytest.raises(
        StorageConfigurationError,
        match="^attachment_storage_credential_reference_missing$",
    ):
        resolve_attachment_storage_config(
            private_config(serving_session_token_env="SYNTHETIC_TOKEN"),
            environ={"SYNTHETIC_TOKEN": ""},
        )


def test_ambient_sdk_redirects_and_public_urls_do_not_select_private_endpoint():
    env = {
        "AWS_ENDPOINT_URL_S3": "http://moto.test:5000",
        "AWS_ENDPOINT_URL": "http://other.test",
        "AWS_CONFIG_FILE": "/synthetic/not-read",
        "MINIO_URL": "http://public.test",
    }
    resolved = resolve_attachment_storage_config(private_config(), environ=env)
    assert resolved.storage.endpoint_url == "https://s3.amazonaws.com"


def test_shared_transport_accepts_private_bounds_and_existing_ca(monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", "/synthetic/ca.pem")
    resolved = resolve_storage_config()
    assert resolved.ca_certs == "/synthetic/ca.pem"
    transport = storage_http_client(
        connect_timeout=3, read_timeout=10, retries=0, ca_certs=resolved.ca_certs
    )
    try:
        options = transport.connection_pool_kw
        assert options["ca_certs"] == "/synthetic/ca.pem"
        assert options["cert_reqs"] == "CERT_REQUIRED"
        assert options["timeout"].connect_timeout == 3
        assert options["timeout"].read_timeout == 10
        assert options["retries"].total == 0
    finally:
        transport.clear()


@pytest.mark.parametrize(
    "url,no_proxy,expected",
    [
        ("https://bucket.store.test/object", "", "http://proxy.test:8080"),
        ("https://bucket.store.test/object", ".store.test", None),
        ("http://10.0.0.5/object", "10.0.0.0/8", None),
        ("http://127.0.0.1:9000/object", "", None),
        ("http://[::1]:9000/object", "", None),
        ("http://localhost:9000/object", "", None),
        ("https://store.test/object", "*", None),
    ],
)
def test_shared_proxy_policy_keeps_no_proxy_and_loopback(
    monkeypatch, url, no_proxy, expected
):
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        monkeypatch.delenv(key, raising=False)
        monkeypatch.delenv(key.lower(), raising=False)
    monkeypatch.setenv("HTTP_PROXY", "proxy.test:8080")
    monkeypatch.setenv("HTTPS_PROXY", "proxy.test:8080")
    monkeypatch.setenv("NO_PROXY", no_proxy)
    assert _environment_proxy(url) == expected


@pytest.mark.parametrize("backend", ["s3", "gcs", "minio"])
def test_media_factory_keeps_existing_credentials_and_cache(monkeypatch, backend):
    monkeypatch.setattr(
        os,
        "environ",
        {
            "STORAGE_BACKEND": backend,
            "S3_ENDPOINT_URL": "http://internal.test:9000",
            "S3_REGION": "local",
            "MINIO_REGION": "auto",
            "S3_ACCESS_KEY": "synthetic-media-access",
            "S3_SECRET_KEY": "synthetic-media-secret",
            "AWS_ACCESS_KEY_ID": "synthetic-ignored-access",
            "AWS_SECRET_ACCESS_KEY": "synthetic-ignored-secret",
            "GCS_HMAC_ACCESS_KEY": "synthetic-gcs-access",
            "GCS_HMAC_SECRET_KEY": "synthetic-gcs-secret",
        },
    )
    media = importlib.import_module("tfc.utils.storage_client")
    monkeypatch.setattr(media, "STORAGE_BACKEND", backend)
    monkeypatch.setattr(media, "_client", None)
    calls = []

    def fake_client(endpoint, **kwargs):
        calls.append((endpoint, kwargs))
        return object()

    monkeypatch.setattr(media, "Minio", fake_client)
    first = media.get_storage_client()
    assert media.get_storage_client() is first
    assert len(calls) == 1
    endpoint, options = calls[0]
    assert endpoint == (
        "storage.googleapis.com" if backend == "gcs" else "internal.test:9000"
    )
    assert options["access_key"] == (
        "synthetic-gcs-access" if backend == "gcs" else "synthetic-media-access"
    )
    assert options["secret_key"] == (
        "synthetic-gcs-secret" if backend == "gcs" else "synthetic-media-secret"
    )
    assert options["http_client"].connection_pool_kw["timeout"].read_timeout == 300
    assert options["http_client"].connection_pool_kw["retries"].total == 5
    options["http_client"].clear()


def django_startup(env):
    root = Path(__file__).resolve().parents[2]
    code = """
import socket
import sys
sys.path.insert(0, sys.argv[1])
def forbidden(*args, **kwargs):
    raise AssertionError("Configuration startup must not contact a service")
socket.socket.connect = forbidden
socket.create_connection = forbidden
import django
django.setup()
from django.conf import settings
assert "tfc.utils.storage_client" not in sys.modules
assert "minio" not in sys.modules
assert "boto3" not in sys.modules
config = settings.FI_ATTACHMENTS_STORAGE
assert (config["resolved"] is not None) == settings.FI_ATTACHMENTS_ENABLED
print("django-settings-startup-ok")
"""
    return subprocess.run(
        [sys.executable, "-I", "-c", code, str(root)],
        env={
            "PATH": os.defpath,
            "DJANGO_SETTINGS_MODULE": "tfc.settings.attachments",
            **env,
        },
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.parametrize("backend", ["s3", "minio", "gcs"])
def test_real_django_disabled_startup_leaves_invalid_unused_config_lazy(backend):
    result = django_startup(
        {
            "FI_ATTACHMENTS_ENABLED": "false",
            "STORAGE_BACKEND": backend,
            "S3_ENDPOINT": "https://synthetic-secret:invalid@[",
            "FI_ATTACHMENTS_QUARANTINE_SESSION_TOKEN_ENV": "SYNTHETIC_MISSING",
            "MINIO_URL": "bad://[",
        }
    )
    assert result.returncode == 0, result.stderr
    assert "django-settings-startup-ok" in result.stdout


@pytest.mark.parametrize("backend", ["s3", "minio", "gcs"])
def test_real_django_enabled_settings_resolve_without_network_or_public_client(backend):
    result = django_startup(
        private_env(STORAGE_BACKEND=backend, FI_ATTACHMENTS_STORAGE_BACKEND=backend)
    )
    assert result.returncode == 0, result.stderr


def test_real_django_enabled_startup_rejects_missing_named_optional_token():
    result = django_startup(
        private_env(FI_ATTACHMENTS_SERVING_SESSION_TOKEN_ENV="SYNTHETIC_MISSING")
    )
    assert result.returncode != 0
    assert "attachment_storage_credential_reference_missing" in result.stderr
    assert "SYNTHETIC_MISSING" not in result.stderr
    assert "synthetic-serving-secret" not in result.stderr


def test_real_django_enabled_startup_rejects_malformed_used_endpoint():
    result = django_startup(
        private_env(S3_ENDPOINT="https://synthetic-user:synthetic-secret@store.test")
    )
    assert result.returncode != 0
    assert "Storage endpoint must be" in result.stderr
    assert "synthetic-secret" not in result.stderr
