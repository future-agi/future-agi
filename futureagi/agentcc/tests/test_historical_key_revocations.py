"""Historical ID-only revocations; all credentials and authority are synthetic."""

import hashlib
import importlib
import importlib.util
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.apps import apps
from django.db import connection
from rest_framework.test import APIRequestFactory

from accounts.services import eval_envelope
from agentcc.models import AgentccAPIKey
from agentcc.services.auth_bridge import gateway_loadable_keys
from tfc.service_envelope import EnvelopeRejected

LEGACY = "agentcc:key:revoked"
HASHES = "agentcc:key:revoked:sha256:v1"
CHANNEL = "agentcc:key:revoke"
MIGRATION = "agentcc.migrations.0078_historical_key_revocations"


class SyntheticRedis:
    def __init__(self):
        self.sets = {LEGACY: set(), HASHES: set()}
        self.messages = []
        self.fail_add_number = None
        self.add_calls = 0
        self.fail_reads = False
        self.fail_publish = False

    def smembers(self, name):
        if self.fail_reads:
            raise RuntimeError("synthetic-private-transport-detail")
        return self.sets[name].copy()

    def sismember(self, name, value):
        if self.fail_reads:
            raise RuntimeError("synthetic-private-transport-detail")
        return value in self.sets[name]

    def sadd(self, name, value):
        self.add_calls += 1
        if self.add_calls == self.fail_add_number:
            raise RuntimeError("synthetic-private-transport-detail")
        before = len(self.sets[name])
        self.sets[name].add(value)
        return len(self.sets[name]) - before

    def publish(self, channel, message):
        if self.fail_publish:
            raise RuntimeError("synthetic-private-transport-detail")
        self.messages.append((channel, message))
        return 0

    def close(self):
        pass


@pytest.fixture
def authority(settings, monkeypatch):
    settings.REDIS_URL = "redis://127.0.0.1:6379/0"
    monkeypatch.setenv("AGENTCC_REDIS_DB", "0")
    monkeypatch.setenv("REDIS_URL", settings.REDIS_URL)
    client = SyntheticRedis()
    with patch("redis.Redis.from_url", return_value=client):
        yield client


def run_forward():
    # Tests-only RED runs real old admission and leaves ID-only storage intact;
    # absence of the new migration must not cause an import/setup failure.
    if importlib.util.find_spec(MIGRATION) is None:
        return None
    module = importlib.import_module(MIGRATION)
    return module.forwards(apps, SimpleNamespace(connection=connection))


def make_key(organization, user, number, *, key_id=None, key_hash=None, deleted=False):
    return AgentccAPIKey._base_manager.create(
        organization=organization,
        user=user,
        name="synthetic historical revocation",
        gateway_key_id=key_id or f"key_{number:032x}",
        key_hash=(
            hashlib.sha256(f"synthetic-historical-{number}".encode()).hexdigest()
            if key_hash is None else key_hash
        ),
        status="active",
        deleted=deleted,
    )


def authenticate(key):
    request = APIRequestFactory().post(eval_envelope.EVAL_ROUTE, {}, format="json")
    claims = {
        "via": "key:" + key.gateway_key_id,
        "org": str(key.organization_id),
        "user": str(key.user_id),
        "workspace": "",
    }
    # Cryptography and membership are positive controls, not revocation mocks.
    with (
        patch.object(eval_envelope, "pair_keys", return_value={}),
        patch.object(eval_envelope, "verify_request", return_value=claims),
        patch.object(eval_envelope, "DurableNonceStore"),
        patch.object(eval_envelope, "live_person", return_value=(key.user, key.organization, None)),
        patch.object(eval_envelope, "_bind_context"),
    ):
        return eval_envelope.authenticate_eval(request)


def assert_refused(key):
    with pytest.raises(EnvelopeRejected):
        authenticate(key)


@pytest.mark.django_db
@pytest.mark.parametrize("resolvable", [True, False])
def test_historical_legacy_only_denies_django_before_and_after_migration(
    authority, organization, user, resolvable
):
    key = make_key(organization, user, 101, key_hash=None if resolvable else "")
    assert authenticate(key)[0].pk == user.pk
    authority.sets[LEGACY].add(key.gateway_key_id)
    # This assertion is RED on the pinned base, not a missing-symbol failure.
    assert_refused(key)
    run_forward()
    assert_refused(key)
    key.refresh_from_db()
    assert key.status == "revoked", "historical DB credential must stay retired"
    assert key.gateway_key_id in authority.sets[LEGACY]
    assert (key.key_hash in authority.sets[HASHES]) is resolvable


@pytest.mark.django_db
def test_historical_migration_maps_resolved_and_keeps_unresolved(authority, organization, user, capsys):
    resolved = make_key(organization, user, 201)
    hashless = make_key(organization, user, 202, key_hash="")
    reused_slot = make_key(organization, user, 203, key_id="key_1")
    deleted = make_key(organization, user, 204, deleted=True)
    missing = "synthetic-historical-missing"
    original = {k.gateway_key_id for k in (resolved, hashless, reused_slot, deleted)} | {missing}
    authority.sets[LEGACY].update(original)
    run_forward()
    assert authority.sets[HASHES] == {resolved.key_hash, deleted.key_hash}, "trusted historical identities must be migrated"
    assert authority.sets[LEGACY] == original, "no legacy marker may be dropped"
    assert (CHANNEL, "sha256:" + resolved.key_hash) in authority.messages
    for key in (resolved, hashless, reused_slot, deleted):
        key.refresh_from_db()
        assert key.status == "revoked"
    report = capsys.readouterr().out.strip()
    assert json.loads(report) == {"resolved": 2, "unresolved": 3}
    assert all(value not in report for value in original | authority.sets[HASHES])


@pytest.mark.django_db
def test_historical_migration_twice_same_counts_and_state(authority, organization, user, capsys):
    key = make_key(organization, user, 301)
    authority.sets[LEGACY].update({key.gateway_key_id, "synthetic-missing"})
    run_forward()
    first_report = capsys.readouterr().out.strip()
    assert first_report, "migration must emit a counts-only operator report"
    key.refresh_from_db()
    before = (key.status, key.updated_at, {k: v.copy() for k, v in authority.sets.items()})
    run_forward()
    second_report = capsys.readouterr().out.strip()
    key.refresh_from_db()
    assert first_report == second_report
    assert json.loads(first_report) == {"resolved": 1, "unresolved": 1}
    assert (key.status, key.updated_at, authority.sets) == before


@pytest.mark.django_db
def test_historical_migration_does_not_choose_an_ambiguous_id(authority, organization, user, capsys):
    old = make_key(organization, user, 401, deleted=True)
    new = make_key(organization, user, 402, key_id=old.gateway_key_id)
    authority.sets[LEGACY].add(old.gateway_key_id)
    run_forward()
    report = capsys.readouterr().out.strip()
    assert report, "ambiguous historical records must be counted"
    assert json.loads(report) == {"resolved": 0, "unresolved": 1}
    assert not authority.sets[HASHES]
    assert_refused(new)


@pytest.mark.django_db
@pytest.mark.parametrize("failure", ["second_add", "publish", "read"])
def test_historical_failure_mid_migration_never_allows(
    authority, organization, user, capsys, failure
):
    keys = [make_key(organization, user, n) for n in (501, 502, 503)]
    authority.sets[LEGACY].update(k.gateway_key_id for k in keys)
    if failure == "second_add":
        authority.fail_add_number = 2
    elif failure == "publish":
        authority.fail_publish = True
    else:
        authority.fail_reads = True
    with pytest.raises(
        RuntimeError,
        match=(
            r"^historical revocation authority unavailable: "
            r"a Redis or database call failed \(builtins\.RuntimeError\)$"
        ),
    ):
        run_forward()
    captured = capsys.readouterr()
    assert not captured.out and not captured.err
    for key in keys:
        assert_refused(key)
    assert len(authority.sets[LEGACY]) == 3
    authority.fail_reads = authority.fail_publish = False
    authority.fail_add_number = None
    run_forward()
    for key in keys:
        assert_refused(key)
    assert len(authority.sets[HASHES]) == 3


@pytest.mark.django_db
def test_historical_bulk_delivery_excludes_retired_credentials(authority, organization, user):
    resolved = make_key(organization, user, 601)
    unresolved = make_key(organization, user, 602, key_id="key_2")
    clean = make_key(organization, user, 603)
    assert gateway_loadable_keys().filter(pk=clean.pk).exists(), "positive delivery fixture must be loadable"
    authority.sets[LEGACY].update({resolved.gateway_key_id, unresolved.gateway_key_id})
    run_forward()
    assert not gateway_loadable_keys().filter(pk__in=[resolved.pk, unresolved.pk]).exists(), "legacy revocations must not be re-delivered as active"
    assert gateway_loadable_keys().filter(pk=clean.pk).exists()


@pytest.mark.django_db
def test_historical_hash_denial_cannot_be_bypassed_by_a_new_id(authority, organization, user):
    key = make_key(organization, user, 701)
    authority.sets[HASHES].add(key.key_hash)
    assert_refused(key)


@pytest.mark.django_db
def test_historical_migration_redis_database_mismatch_fails_closed(authority, organization, user, monkeypatch):
    key = make_key(organization, user, 801)
    authority.sets[LEGACY].add(key.gateway_key_id)
    monkeypatch.setenv("AGENTCC_REDIS_DB", "4")
    with pytest.raises(
        RuntimeError,
        match=(
            r"^historical revocation authority unavailable: "
            r"REDIS_URL database must match AGENTCC_REDIS_DB "
            r"\(django\.core\.exceptions\.ImproperlyConfigured\)$"
        ),
    ):
        run_forward()
    assert_refused(key)
