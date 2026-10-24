"""Per-org managed upstream keys: storage, writes, push assembly and the
command.

A key is stored encrypted, decrypted only while a push payload is built, and
appears nowhere else: not in an org config or its history, a read, a log or
the command's output (managed-upstream-keys-push-v1).
"""

import io
import json
import threading
import time
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
import requests
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection
from structlog.testing import capture_logs

from accounts.models import Organization
from agentcc.models.managed_upstream_key import AgentccManagedUpstreamKey
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import config_push
from agentcc.services import managed_upstream_keys as keys
from agentcc.services import openrouter_provisioning as provisioning
from agentcc.services.credential_manager import decrypt_token, encrypt_token
from agentcc.services.gateway_client import GatewayClientError
from agentcc.services.org_config_writer import MERGE, write_config_version

# Test values, not real keys.
KEY = "sk-or-v1-test-upstream-key-not-a-real-secret-0001"
NEW_KEY = "sk-or-v1-test-upstream-key-not-a-real-secret-0002"
OTHER_KEY = "gsk-test-upstream-key-not-a-real-secret-0003"
COMMAND = "agentcc_managed_upstream_keys"
Status = AgentccManagedUpstreamKey.Status
PROVISIONING_ENV = (
    provisioning.PROVISIONING_KEY_ENV,
    provisioning.LIVE_ENV,
    provisioning.MOCK_URL_ENV,
    provisioning.MOCK_KEY_ENV,
)


@pytest.fixture
def managed_config(organization):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=organization,
        version=1,
        is_active=True,
        managed_models={"enabled": True},
        routing={"strategy": "round_robin"},
    )


@pytest.fixture
def gateway():
    """The gateway client every push goes through, recording each push."""
    client = MagicMock()
    with (
        patch("agentcc.services.config_push.get_gateway_client", return_value=client),
        patch("agentcc.services.config_push._ensure_managed_account", MagicMock()),
    ):
        yield client


@pytest.fixture
def no_network():
    """Any HTTP call through requests raises, so a test cannot reach a host."""
    with patch.object(
        requests.Session,
        "request",
        side_effect=requests.ConnectionError("network disabled in this test"),
    ) as request:
        yield request


@pytest.fixture
def no_provisioning_env(monkeypatch):
    for name in PROVISIONING_ENV:
        monkeypatch.delenv(name, raising=False)


def _pushed_payloads(gateway):
    return [call.args[1] for call in gateway.set_org_config.call_args_list]


def _pushed_upstream_keys(gateway):
    return _pushed_payloads(gateway)[-1].get("managed_models", {}).get("upstream_keys")


def _active(org):
    return list(
        AgentccManagedUpstreamKey.objects.filter(
            organization=org, status=Status.ACTIVE
        ).values_list("provider_id", "key_hash")
    )


@pytest.mark.integration
class TestStorage:
    def test_encrypt_roundtrip_and_repr_hides_key(self, organization, user):
        change = keys.set_key(
            organization, "openrouter", KEY, key_hash="kh_1", label="acme", actor=user
        )

        row = AgentccManagedUpstreamKey.objects.get(pk=change.key.pk)
        assert row.encrypted_key.startswith("enc::")
        assert KEY not in row.encrypted_key
        assert decrypt_token(row.encrypted_key) == KEY
        assert row.created_by == user
        assert row.status == Status.ACTIVE
        for text in (repr(row), str(row), repr(change)):
            assert KEY not in text
            assert row.encrypted_key not in text
            assert "kh_1" in text
        assert keys.assemble_for_push(str(organization.id)) == {
            "openrouter": {"api_key": KEY, "key_hash": "kh_1", "label": "acme"}
        }

    @pytest.mark.parametrize(
        "api_key",
        ["", "  ", f" {KEY}", "enc::looks-encrypted-test-value", None],
        ids=["empty", "blank", "padded", "ciphertext", "none"],
    )
    def test_set_key_refuses_unusable_key_without_echoing_it(
        self, organization, api_key
    ):
        with pytest.raises(keys.InvalidUpstreamKey) as refused:
            keys.set_key(organization, "openrouter", api_key, key_hash="kh_1")

        if api_key and api_key.strip():
            assert api_key.strip() not in str(refused.value)
        assert not AgentccManagedUpstreamKey.objects.filter(
            organization=organization
        ).exists()

    def test_save_refuses_key_without_enc_prefix(self, organization):
        for value in ("plain-upstream-key-test-value", "", "enc::", "ENC::abc"):
            row = AgentccManagedUpstreamKey(
                organization=organization,
                provider_id="openrouter",
                encrypted_key=value,
                key_hash="kh_1",
            )
            with pytest.raises(ValidationError) as cleaned:
                row.full_clean()
            assert "encrypted_key" in cleaned.value.message_dict
            with pytest.raises(ValidationError) as saved:
                row.save()
            with pytest.raises(ValidationError):
                AgentccManagedUpstreamKey.objects.create(
                    organization=organization,
                    provider_id="openrouter",
                    encrypted_key=value,
                    key_hash="kh_1",
                )
            if value:
                assert value not in str(cleaned.value)
                assert value not in str(saved.value)
        assert not AgentccManagedUpstreamKey.objects.filter(
            organization=organization
        ).exists()

        # A value written through CredentialManager passes both.
        row = AgentccManagedUpstreamKey(
            organization=organization,
            provider_id="openrouter",
            encrypted_key=encrypt_token(KEY),
            key_hash="kh_1",
        )
        row.full_clean()
        row.save()
        # A status-only save does not reload or re-check the key.
        row.status = Status.REVOKED
        row.save(update_fields=["status"])
        assert AgentccManagedUpstreamKey.objects.get(pk=row.pk).status == Status.REVOKED

    def test_unreadable_row_is_left_out_and_logged_by_type(self, organization):
        keys.set_key(organization, "openrouter", KEY, key_hash="kh_1")
        keys.set_key(organization, "groq", OTHER_KEY, key_hash="kh_2")
        # Written around the model's guard, as a raw update would.
        AgentccManagedUpstreamKey.objects.filter(provider_id="groq").update(
            encrypted_key="enc::not-a-fernet-token"
        )

        with capture_logs() as logs:
            assembled = keys.assemble_for_push(str(organization.id))

        assert assembled == {
            "openrouter": {"api_key": KEY, "key_hash": "kh_1", "label": ""}
        }
        unreadable = [
            log for log in logs if log["event"] == "managed_upstream_key_unreadable"
        ]
        assert [(log["provider_id"], log["key_hash"]) for log in unreadable] == [
            ("groq", "kh_2")
        ]
        assert "not-a-fernet-token" not in repr(logs)


@pytest.mark.integration
class TestOneActiveKey:
    def test_one_active_key_per_org_provider(self, organization, user):
        other_org = Organization.objects.create(name="Other org")
        keys.set_key(organization, "openrouter", KEY, key_hash="kh_1", actor=user)

        with pytest.raises(keys.ManagedUpstreamKeyExists):
            keys.set_key(organization, "openrouter", NEW_KEY, key_hash="kh_2")
        assert _active(organization) == [("openrouter", "kh_1")]

        change = keys.rotate(organization, "openrouter", NEW_KEY, key_hash="kh_2")
        assert change.replaced.key_hash == "kh_1"
        assert change.replaced.status == Status.DISABLED
        assert change.replaced.rotated_at is not None
        assert _active(organization) == [("openrouter", "kh_2")]

        # Another provider, and another org, each get their own active key.
        keys.set_key(organization, "groq", OTHER_KEY, key_hash="kh_3")
        keys.set_key(other_org, "openrouter", KEY, key_hash="kh_4")
        assert sorted(_active(organization)) == [
            ("groq", "kh_3"),
            ("openrouter", "kh_2"),
        ]
        assert _active(other_org) == [("openrouter", "kh_4")]
        assert keys.assemble_for_push(str(organization.id)) == {
            "groq": {"api_key": OTHER_KEY, "key_hash": "kh_3", "label": ""},
            "openrouter": {"api_key": NEW_KEY, "key_hash": "kh_2", "label": ""},
        }

        with pytest.raises(keys.InvalidUpstreamKey):
            keys.rotate(organization, "openrouter", KEY, key_hash="kh_2")
        with pytest.raises(keys.ManagedUpstreamKeyMissing):
            keys.rotate(organization, "mistral", KEY, key_hash="kh_5")
        with pytest.raises(keys.ManagedUpstreamKeyMissing):
            keys.revoke(organization, "mistral")
        assert (
            AgentccManagedUpstreamKey.objects.filter(organization=organization).count()
            == 3
        )

    def test_rotate_keeps_label_and_limit_unless_given(self, organization):
        keys.set_key(
            organization,
            "openrouter",
            KEY,
            key_hash="kh_1",
            label="acme",
            limit_usd=Decimal("25.00"),
        )

        kept = keys.rotate(organization, "openrouter", NEW_KEY, key_hash="kh_2").key
        assert (kept.label, kept.limit_usd) == ("acme", Decimal("25.00"))
        given = keys.rotate(
            organization,
            "openrouter",
            KEY,
            key_hash="kh_3",
            label="acme-2",
            limit_usd=Decimal("40.00"),
        ).key
        assert (given.label, given.limit_usd) == ("acme-2", Decimal("40.00"))


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
class TestConcurrentSetKey:
    def test_concurrent_set_key_one_wins_without_integrity_error(self):
        org = Organization.objects.create(name="Concurrent upstream keys")
        first_writing = threading.Event()
        results = {}
        real_encrypt = keys.encrypt_token

        def encrypt_and_hold(value):
            # The first writer holds its transaction until the second waits
            # on its locks, so both have passed the "no active key" check
            # unless the org lock serializes them.
            if threading.current_thread().name == "first":
                first_writing.set()
                deadline = time.monotonic() + 30
                while not _a_backend_waits_on_this_connection():
                    assert time.monotonic() < deadline, "second writer never waited"
                    time.sleep(0.02)
            return real_encrypt(value)

        def write(name, value, key_hash):
            def run():
                if name == "second":
                    assert first_writing.wait(30)
                try:
                    keys.set_key(org, "openrouter", value, key_hash=key_hash)
                    results[name] = "ok"
                except keys.ManagedUpstreamKeyExists:
                    results[name] = "exists"
                except IntegrityError:
                    results[name] = "integrity_error"
                finally:
                    connection.close()

            return threading.Thread(target=run, name=name)

        try:
            with (
                patch.object(keys, "encrypt_token", encrypt_and_hold),
                patch.object(keys, "push_active_config", return_value=None),
            ):
                threads = [
                    write("first", KEY, "kh_1"),
                    write("second", NEW_KEY, "kh_2"),
                ]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(30)

            assert results == {"first": "ok", "second": "exists"}
            assert _active(org) == [("openrouter", "kh_1")]
        finally:
            AgentccManagedUpstreamKey.objects.filter(organization=org).delete()
            org.delete()


def _a_backend_waits_on_this_connection():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_locks"
            " WHERE NOT granted AND pg_backend_pid() = ANY(pg_blocking_pids(pid))"
        )
        return cursor.fetchone()[0] > 0


@pytest.mark.integration
class TestPushOnly:
    def test_assemble_for_push_only_place_key_appears(
        self,
        organization,
        user,
        auth_client,
        managed_config,
        gateway,
        caplog,
        django_capture_on_commit_callbacks,
    ):
        with capture_logs() as logs:
            with django_capture_on_commit_callbacks(execute=True):
                change = keys.set_key(
                    organization, "openrouter", KEY, key_hash="kh_1", label="acme"
                )
            assert change.gateway_synced is True
            # A config write pushes the key too, and saves a version without it.
            response = auth_client.post(
                "/agentcc/gateways/default/update-config/",
                {"cache": {"enabled": True}},
                format="json",
            )
            assert response.status_code == 200, response.content
            reads = [
                auth_client.get("/agentcc/org-configs/"),
                auth_client.get(f"/agentcc/org-configs/{managed_config.pk}/"),
                auth_client.get("/agentcc/org-configs/active/"),
                auth_client.get("/agentcc/gateways/default/config/"),
            ]

        # The pushed payloads carry it...
        payloads = _pushed_payloads(gateway)
        assert len(payloads) == 2
        for payload in payloads:
            assert payload["managed_models"]["upstream_keys"] == {
                "openrouter": {"api_key": KEY, "key_hash": "kh_1", "label": "acme"}
            }
        # ...and nothing else does: no stored version, no read, no log.
        versions = AgentccOrgConfig.all_objects.filter(organization=organization)
        assert versions.count() == 2
        for version in versions:
            stored = json.dumps(
                {
                    section: getattr(version, section)
                    for section in ("managed_models", "routing", "cache", "privacy")
                }
            )
            assert KEY not in stored
            assert "upstream_keys" not in stored
        for read in reads:
            assert read.status_code == 200, read.content
            assert KEY.encode() not in read.content
        assert KEY not in repr(logs)
        assert KEY not in caplog.text
        row = AgentccManagedUpstreamKey.objects.get(organization=organization)
        assert KEY not in row.encrypted_key

    def test_assemble_for_push_not_called_on_save(
        self, organization, auth_client, managed_config, gateway
    ):
        keys.set_key(organization, "openrouter", KEY, key_hash="kh_1")
        spy = MagicMock(wraps=keys.assemble_for_push)

        with patch.object(keys, "assemble_for_push", spy):
            saved, _ = write_config_version(
                organization,
                {"cache": {"enabled": True}},
                mode=MERGE,
                push=lambda config: True,
            )
            config_push.validate_org_config(saved)
            assert spy.call_count == 0

            assert config_push.push_org_config(str(organization.id), saved)
            assert spy.call_count == 1

            # One update-config: one save (no call) and one push (one call).
            response = auth_client.post(
                "/agentcc/gateways/default/update-config/",
                {"budgets": {"enabled": False}},
                format="json",
            )
            assert response.status_code == 200, response.content
            assert spy.call_count == 2
        assert spy.call_args_list[0].args == (str(organization.id),)

    def test_revoke_pushes_and_disables(
        self,
        organization,
        user,
        managed_config,
        gateway,
        django_capture_on_commit_callbacks,
    ):
        with django_capture_on_commit_callbacks(execute=True):
            keys.set_key(organization, "openrouter", KEY, key_hash="kh_1")
        assert _pushed_upstream_keys(gateway)["openrouter"]["api_key"] == KEY
        gateway.reset_mock()

        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            change = keys.revoke(organization, "openrouter", actor=user)
        # The push waits for the commit.
        gateway.set_org_config.assert_not_called()
        assert change.gateway_synced is None
        for callback in callbacks:
            callback()

        assert change.gateway_synced is True
        gateway.set_org_config.assert_called_once()
        assert "upstream_keys" not in _pushed_payloads(gateway)[0]["managed_models"]
        row = AgentccManagedUpstreamKey.objects.get(pk=change.key.pk)
        assert row.status == Status.REVOKED
        assert keys.assemble_for_push(str(organization.id)) == {}
        with pytest.raises(keys.ManagedUpstreamKeyMissing):
            keys.revoke(organization, "openrouter")
        # A new key can be set after a revoke.
        keys.set_key(organization, "openrouter", NEW_KEY, key_hash="kh_2")
        assert _active(organization) == [("openrouter", "kh_2")]

    def test_failed_push_keeps_the_key_and_reports_it(
        self, organization, managed_config, gateway, django_capture_on_commit_callbacks
    ):
        gateway.set_org_config.side_effect = GatewayClientError(
            "Gateway returned 503", status_code=503
        )
        with django_capture_on_commit_callbacks(execute=True):
            change = keys.set_key(organization, "openrouter", KEY, key_hash="kh_1")

        # Fail open: the key stays, and the gateway's next sync pulls it.
        assert change.gateway_synced is False
        assert _active(organization) == [("openrouter", "kh_1")]

        with (
            patch.object(
                config_push, "push_org_config", side_effect=RuntimeError("boom")
            ),
            capture_logs() as logs,
        ):
            assert keys.push_active_config(organization.pk) is False
        assert [log["event"] for log in logs] == ["managed_upstream_keys_push_failed"]

    def test_write_for_org_without_config_skips_push(
        self, organization, gateway, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True):
            change = keys.set_key(organization, "openrouter", KEY, key_hash="kh_1")

        assert change.gateway_synced is None
        gateway.set_org_config.assert_not_called()
        assert not AgentccOrgConfig.all_objects.filter(
            organization=organization
        ).exists()


def _call(*args):
    out, err = io.StringIO(), io.StringIO()
    call_command(COMMAND, *args, stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


@pytest.mark.integration
@pytest.mark.usefixtures("no_provisioning_env")
# Transactional, so each write's push runs when it commits, as in production,
# and the command reports it.
@pytest.mark.django_db(transaction=True)
class TestCommand:
    def test_command_never_prints_key(
        self, organization, managed_config, gateway, no_network, tmp_path
    ):
        key_file, new_key_file = tmp_path / "key", tmp_path / "new-key"
        key_file.write_text(KEY + "\n")
        new_key_file.write_text(NEW_KEY)
        org_id = str(organization.id)
        outputs = []

        with capture_logs() as logs:
            outputs.append(
                _call(
                    org_id,
                    "--set",
                    "--provider",
                    "openrouter",
                    "--key-file",
                    str(key_file),
                    "--label",
                    "acme",
                    "--limit-usd",
                    "25",
                )
            )
            outputs.append(_call(org_id, "--show"))
            outputs.append(_call(org_id, "--rotate", "--key-file", str(new_key_file)))
            outputs.append(_call(org_id, "--revoke"))
            outputs.append(_call(org_id, "--show"))

        text = "\n".join(out + err for out, err in outputs)
        for value in (KEY, NEW_KEY):
            assert value not in text
            assert value not in repr(logs)
        # --show prints the provider, key hash, label, status and limit.
        show = outputs[1][0]
        row = AgentccManagedUpstreamKey.objects.get(
            organization=organization, key_hash=_sha256(KEY)
        )
        assert decrypt_token(row.encrypted_key) == KEY
        assert (
            f"openrouter key_hash={row.key_hash} label=acme status=active limit_usd=25.00"
            in show
        )
        final = outputs[4][0]
        assert "status=revoked" in final and "status=disabled" in final
        # The set pushed the key; the revoke's push dropped it.
        assert (
            _pushed_payloads(gateway)[0]["managed_models"]["upstream_keys"][
                "openrouter"
            ]["api_key"]
            == KEY
        )
        assert "upstream_keys" not in _pushed_payloads(gateway)[-1]["managed_models"]
        assert "gateway: config pushed" in outputs[0][0]
        # The upstream disables were dry runs: printed, never sent.
        assert (
            f"Would send: PATCH {provisioning.LIVE_BASE_URL}/keys/{_sha256(KEY)}"
            in outputs[2][0]
        )
        no_network.assert_not_called()

    def test_command_reports_a_failed_push(
        self, organization, managed_config, gateway, tmp_path
    ):
        gateway.set_org_config.side_effect = GatewayClientError(
            "Gateway returned 503", status_code=503
        )
        key_file = tmp_path / "key"
        key_file.write_text(KEY)

        out, _ = _call(str(organization.id), "--set", "--key-file", str(key_file))

        assert "gateway: push failed" in out
        assert _active(organization) == [("openrouter", _sha256(KEY))]

    def test_set_refuses_second_key_and_unreadable_file(
        self, organization, managed_config, gateway, tmp_path
    ):
        key_file = tmp_path / "key"
        key_file.write_text(KEY)
        org_id = str(organization.id)
        _call(org_id, "--set", "--key-file", str(key_file), "--key-hash", "kh_1")

        with pytest.raises(CommandError, match="already has an active openrouter"):
            _call(org_id, "--set", "--key-file", str(key_file), "--key-hash", "kh_2")
        two_keys = tmp_path / "two"
        two_keys.write_text(f"{KEY}\n{NEW_KEY}")
        with pytest.raises(CommandError) as refused:
            _call(org_id, "--rotate", "--key-file", str(two_keys))
        assert KEY not in str(refused.value)
        with pytest.raises(CommandError, match="Cannot read --key-file"):
            _call(org_id, "--rotate", "--key-file", str(tmp_path / "missing"))
        with pytest.raises(CommandError, match="No organization"):
            _call("not-a-uuid", "--show")
        assert _active(organization) == [("openrouter", "kh_1")]


def _sha256(value):
    import hashlib

    return hashlib.sha256(value.encode()).hexdigest()
