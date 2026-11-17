"""Fail-closed revocation errors name the failed check, never a value.

Every case still refuses with RevocationAuthorityUnavailable. The message adds
the check that failed and the exception class, so an operator can read the
cause at a glance; it never carries a URL, host, port, password or member, and
the inner exception stays out of the printed chain. All values are synthetic.
"""

import importlib
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import redis
from django.apps import apps
from django.db import connection

from agentcc.services.key_revocation_authority import (
    RevocationAuthorityUnavailable,
    credential_is_revoked,
    revocation_authority,
)

PREFIX = "historical revocation authority unavailable"
SECRET = "synthetic-secret-pw-0b1c"
HOST = "10.0.0.9"
PORT = "6390"
URL = f"redis://:{SECRET}@{HOST}:{PORT}/0"
MIGRATION = "agentcc.migrations.0078_historical_key_revocations"
MISMATCH = (
    f"{PREFIX}: REDIS_URL database must match AGENTCC_REDIS_DB"
    " (django.core.exceptions.ImproperlyConfigured)"
)


class FailingRedis:
    def __init__(self, exc):
        self.exc = exc

    def smembers(self, name):
        raise self.exc

    def sismember(self, name, value):
        raise self.exc

    def close(self):
        pass


@pytest.fixture
def authority_env(settings, monkeypatch):
    settings.REDIS_URL = URL
    monkeypatch.setenv("REDIS_URL", URL)
    monkeypatch.delenv("AGENTCC_REDIS_DB", raising=False)
    return settings


def read_inventory():
    with revocation_authority() as client:
        client.smembers("agentcc:key:revoked")


def run_forward(registry=apps):
    module = importlib.import_module(MIGRATION)
    return module.forwards(registry, SimpleNamespace(connection=connection))


def assert_names_only(err, expected):
    text = str(err)
    assert text == expected
    for value in (SECRET, HOST, PORT, URL):
        assert value not in text
    # Nothing inner is printed or reported with it.
    assert err.__cause__ is None
    assert err.__context__ is None or err.__suppress_context__


def test_db_mismatch_names_the_check(authority_env, monkeypatch):
    monkeypatch.setenv("AGENTCC_REDIS_DB", "4")
    with patch("redis.Redis.from_url") as from_url:
        with pytest.raises(RevocationAuthorityUnavailable) as caught:
            read_inventory()
    from_url.assert_not_called()
    assert_names_only(caught.value, MISMATCH)


def test_empty_redis_url_names_the_check(authority_env, monkeypatch):
    authority_env.REDIS_URL = ""
    monkeypatch.setenv("REDIS_URL", "")
    with patch("redis.Redis.from_url") as from_url:
        with pytest.raises(RevocationAuthorityUnavailable) as caught:
            read_inventory()
    from_url.assert_not_called()
    assert_names_only(caught.value, f"{PREFIX}: REDIS_URL is not set")


def test_client_setup_failure_names_the_step_and_class(authority_env):
    with patch("redis.Redis.from_url", side_effect=ValueError(f"bad url {URL}")):
        with pytest.raises(RevocationAuthorityUnavailable) as caught:
            read_inventory()
    assert_names_only(
        caught.value, f"{PREFIX}: Redis client setup failed (builtins.ValueError)"
    )


def test_connect_failure_names_the_call_and_class(authority_env):
    failure = redis.exceptions.ConnectionError(f"Error connecting to {URL}")
    with patch("redis.Redis.from_url", return_value=FailingRedis(failure)):
        with pytest.raises(RevocationAuthorityUnavailable) as caught:
            read_inventory()
    assert_names_only(
        caught.value,
        f"{PREFIX}: the gateway's Redis could not be reached"
        " (redis.exceptions.ConnectionError)",
    )


def test_other_redis_failure_names_the_call_and_class(authority_env):
    failure = redis.exceptions.ResponseError(f"WRONGTYPE member key_{SECRET}")
    with patch("redis.Redis.from_url", return_value=FailingRedis(failure)):
        with pytest.raises(RevocationAuthorityUnavailable) as caught:
            read_inventory()
    assert_names_only(
        caught.value,
        f"{PREFIX}: a Redis or database call failed (redis.exceptions.ResponseError)",
    )


def test_admission_read_failure_keeps_the_reason(authority_env):
    failure = redis.exceptions.TimeoutError(f"Timeout reading from {URL}")
    with patch("redis.Redis.from_url", return_value=FailingRedis(failure)):
        with pytest.raises(RevocationAuthorityUnavailable) as caught:
            credential_is_revoked("key_" + "a" * 32, "b" * 64)
    assert_names_only(
        caught.value,
        f"{PREFIX}: the gateway's Redis could not be reached"
        " (redis.exceptions.TimeoutError)",
    )


def test_admission_malformed_hash_message_is_unchanged(authority_env):
    client = SimpleNamespace(sismember=lambda *args: False, close=lambda: None)
    with patch("redis.Redis.from_url", return_value=client):
        with pytest.raises(RevocationAuthorityUnavailable) as caught:
            credential_is_revoked("key_" + "a" * 32, "not-a-sha256")
    assert str(caught.value) == PREFIX


@pytest.mark.django_db
def test_migration_keeps_the_authority_reason(authority_env, monkeypatch, capsys):
    monkeypatch.setenv("AGENTCC_REDIS_DB", "4")
    with patch("redis.Redis.from_url") as from_url:
        with pytest.raises(RevocationAuthorityUnavailable) as caught:
            run_forward()
    from_url.assert_not_called()
    assert_names_only(caught.value, MISMATCH)
    captured = capsys.readouterr()
    assert not captured.out and not captured.err


def test_migration_setup_failure_names_the_step_and_class(authority_env):
    def get_model(*args):
        raise LookupError(f"synthetic registry detail {SECRET}")

    with pytest.raises(RevocationAuthorityUnavailable) as caught:
        run_forward(SimpleNamespace(get_model=get_model))
    assert_names_only(
        caught.value, f"{PREFIX}: migration setup failed (builtins.LookupError)"
    )
