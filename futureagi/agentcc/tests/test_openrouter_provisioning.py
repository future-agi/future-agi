"""OpenRouter key provisioning against the agentcc-mockup, and the live
switches.

The mock is the agentcc-mockup binary
(``agentcc-gateway/cmd/agentcc-mockup``), built from this checkout and run on
a loopback port. A call to the live API needs all three switches; without them
nothing is sent, and the command prints the request it would send and exits 0.
No test here reaches the network: the live-switch tests patch the HTTP client
to raise.
"""

import io
import itertools
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.request
import uuid
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
from django.core.management import call_command
from django.core.management.base import CommandError
from structlog.testing import capture_logs

from agentcc.models.managed_upstream_key import AgentccManagedUpstreamKey
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import managed_upstream_keys as keys
from agentcc.services import openrouter_provisioning as provisioning
from agentcc.services.credential_manager import decrypt_token

GATEWAY_DIR = Path(__file__).resolve().parents[3] / "agentcc-gateway"
MOCK_FIXTURES = GATEWAY_DIR / "scripts" / "scenarios" / "fixtures" / "common"
COMMAND = "agentcc_managed_upstream_keys"
# A test value, not a real provisioning key.
PROVISIONING_KEY = "sk-or-v1-test-provisioning-key-not-a-real-secret"
Status = AgentccManagedUpstreamKey.Status


@pytest.fixture(scope="session")
def mockup(tmp_path_factory):
    """The agentcc-mockup binary on a free loopback port; yields its URL."""
    go = shutil.which("go")
    if go is None or not (GATEWAY_DIR / "cmd" / "agentcc-mockup").is_dir():
        pytest.skip("needs Go and agentcc-gateway/cmd/agentcc-mockup to build the mock")
    work = tmp_path_factory.mktemp("agentcc-mockup")
    binary = work / "agentcc-mockup"
    subprocess.run(
        [go, "build", "-o", str(binary), "./cmd/agentcc-mockup"],
        cwd=GATEWAY_DIR,
        env={**os.environ, "GOPROXY": "off", "GOFLAGS": "-buildvcs=false"},
        check=True,
        capture_output=True,
        timeout=600,
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    with open(work / "mockup.log", "wb") as log:
        process = subprocess.Popen(
            [binary, "--addr", f"127.0.0.1:{port}", "--fixtures", str(MOCK_FIXTURES)],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    try:
        _wait_until_up(f"{url}/__charges", process)
        yield url
    finally:
        process.terminate()
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(10)


def _wait_until_up(url, process):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"agentcc-mockup exited with {process.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("agentcc-mockup did not come up within 30 s")


@pytest.fixture
def mock_target(mockup, monkeypatch):
    """Point provisioning at the mock under a bearer of this test's own; the
    mock keeps keys per bearer. Returns that bearer."""
    for name in (provisioning.PROVISIONING_KEY_ENV, provisioning.LIVE_ENV):
        monkeypatch.delenv(name, raising=False)
    bearer = f"mock-provisioning-{uuid.uuid4().hex}"
    monkeypatch.setenv(provisioning.MOCK_URL_ENV, f"{mockup}/api/v1")
    monkeypatch.setenv(provisioning.MOCK_KEY_ENV, bearer)
    return bearer


@pytest.fixture
def managed_config(organization):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=organization,
        version=1,
        is_active=True,
        managed_models={"enabled": True},
    )


@pytest.fixture
def gateway():
    client = MagicMock()
    with (
        patch("agentcc.services.config_push.get_gateway_client", return_value=client),
        patch("agentcc.services.config_push._ensure_managed_account", MagicMock()),
    ):
        yield client


@pytest.fixture
def no_network():
    with patch.object(
        requests.Session,
        "request",
        side_effect=requests.ConnectionError("network disabled in this test"),
    ) as request:
        yield request


def _mock_get(url, bearer):
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {bearer}"})
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read())


def _call(*args):
    out, err = io.StringIO(), io.StringIO()
    call_command(COMMAND, *args, stdout=out, stderr=err)
    return out.getvalue() + err.getvalue()


@pytest.mark.integration
class TestClientAgainstMock:
    def test_client_crud_against_mock(self, mock_target):
        client = provisioning.OpenRouterProvisioningClient()
        assert client.target.live is False

        created = client.create_key("futureagi-org-test", Decimal("25"))
        assert created.key and created.key_hash
        assert (created.name, created.limit_usd) == (
            "futureagi-org-test",
            Decimal("25.00"),
        )
        assert created.key not in repr(created)
        assert mock_target not in repr(client.target)

        fetched = client.get_key(created.key_hash)
        assert (fetched["hash"], fetched["limit"], fetched["disabled"]) == (
            created.key_hash,
            25,
            False,
        )
        assert (
            client.update_key(created.key_hash, limit_usd=Decimal("40"))["limit"] == 40
        )
        assert client.update_key(created.key_hash, disabled=True)["disabled"] is True
        client.delete_key(created.key_hash)
        with pytest.raises(provisioning.ProvisioningError) as missing:
            client.get_key(created.key_hash)
        assert missing.value.status_code == 404

    def test_provisioned_key_that_cannot_be_stored_is_deleted_upstream(
        self, organization, managed_config, gateway, mock_target
    ):
        client = provisioning.OpenRouterProvisioningClient()
        listing = provisioning.ProvisioningRequest("GET", "/keys")
        refused = keys.ManagedUpstreamKeyExists("openrouter")

        with patch.object(keys, "set_key", side_effect=refused):
            with pytest.raises(keys.ProvisionedKeyNotStored, match="was deleted"):
                keys.provision(organization, client, limit_usd=Decimal("5"))
        assert client.send(listing)["data"] == []

        # When the delete fails too, the error and the log name the key's hash.
        failed_delete = provisioning.ProvisioningError(
            "DELETE /keys/x returned 500", 500
        )
        with (
            patch.object(keys, "set_key", side_effect=refused),
            patch.object(client, "delete_key", side_effect=failed_delete),
            capture_logs() as logs,
        ):
            with pytest.raises(keys.ProvisionedKeyNotStored, match="at OpenRouter"):
                keys.provision(organization, client)
        [orphan] = client.send(listing)["data"]
        assert [
            log["key_hash"]
            for log in logs
            if log["event"] == "managed_upstream_key_orphaned"
        ] == [orphan["hash"]]
        assert not AgentccManagedUpstreamKey.objects.filter(
            organization=organization
        ).exists()


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
class TestCommandAgainstMock:
    def test_provision_against_mock_creates_and_stores(
        self, organization, managed_config, gateway, mock_target, mockup
    ):
        output = _call(str(organization.id), "--provision", "--limit-usd", "25")

        row = AgentccManagedUpstreamKey.objects.get(
            organization=organization, status=Status.ACTIVE
        )
        assert row.provider_id == "openrouter"
        assert row.limit_usd == Decimal("25.00")
        assert row.label == f"futureagi-org-{organization.id}"
        assert row.encrypted_key.startswith("enc::")
        secret = decrypt_token(row.encrypted_key)
        assert secret not in output
        assert "gateway: config pushed" in output
        # The mock knows the key by the hash stored for it...
        upstream = provisioning.OpenRouterProvisioningClient().get_key(row.key_hash)
        assert (upstream["name"], upstream["limit"]) == (row.label, 25)
        # ...and the stored key is that key: as a bearer, it reports its limit.
        assert _mock_get(f"{mockup}/api/v1/key", secret)["data"]["limit"] == 25
        # The push carried it to the gateway.
        payload = gateway.set_org_config.call_args.args[1]
        assert payload["managed_models"]["upstream_keys"]["openrouter"] == {
            "api_key": secret,
            "key_hash": row.key_hash,
            "label": row.label,
        }

    def test_command_rotate_and_revoke_disable_old_keys_at_mock(
        self, organization, managed_config, gateway, mock_target
    ):
        org_id = str(organization.id)
        _call(org_id, "--provision", "--limit-usd", "10")
        first = keys.active_key(organization, "openrouter")

        output = _call(org_id, "--rotate")
        second = keys.active_key(organization, "openrouter")
        first.refresh_from_db()
        client = provisioning.OpenRouterProvisioningClient()
        assert first.status == Status.DISABLED
        assert second.key_hash != first.key_hash
        assert second.limit_usd == Decimal("10.00")
        assert client.get_key(first.key_hash)["disabled"] is True
        assert client.get_key(second.key_hash)["disabled"] is False
        assert f"disabled upstream key {first.key_hash}" in output

        output = _call(org_id, "--revoke")
        second.refresh_from_db()
        assert second.status == Status.REVOKED
        assert client.get_key(second.key_hash)["disabled"] is True
        assert (
            "upstream_keys"
            not in gateway.set_org_config.call_args.args[1]["managed_models"]
        )
        for row in (first, second):
            assert decrypt_token(row.encrypted_key) not in output

    def test_provision_refused_before_any_call_when_a_key_is_active(
        self, organization, managed_config, gateway, mock_target, no_network
    ):
        keys.set_key(
            organization, "openrouter", "sk-or-v1-test-held-key", key_hash="kh_1"
        )

        with pytest.raises(CommandError, match="already has an active openrouter key"):
            _call(str(organization.id), "--provision")
        no_network.assert_not_called()


def _set_switches(monkeypatch, *, key, live_env):
    monkeypatch.delenv(provisioning.MOCK_URL_ENV, raising=False)
    monkeypatch.delenv(provisioning.MOCK_KEY_ENV, raising=False)
    if key:
        monkeypatch.setenv(provisioning.PROVISIONING_KEY_ENV, PROVISIONING_KEY)
    else:
        monkeypatch.delenv(provisioning.PROVISIONING_KEY_ENV, raising=False)
    if live_env:
        monkeypatch.setenv(provisioning.LIVE_ENV, "1")
    else:
        monkeypatch.setenv(provisioning.LIVE_ENV, "0")


@pytest.mark.integration
class TestLiveSwitches:
    def test_live_refused_without_all_three_switches(
        self, organization, monkeypatch, no_network
    ):
        combinations = [
            combo
            for combo in itertools.product([False, True], repeat=3)
            if combo != (True, True, True)
        ]
        for key, live_env, live_flag in combinations:
            _set_switches(monkeypatch, key=key, live_env=live_env)
            expected_missing = [
                name
                for name, on in (
                    (provisioning.PROVISIONING_KEY_ENV, key),
                    (f"{provisioning.LIVE_ENV}=1", live_env),
                    ("--live", live_flag),
                )
                if not on
            ]

            with pytest.raises(provisioning.LiveProvisioningRefused) as refused:
                provisioning.OpenRouterProvisioningClient(live=live_flag)
            assert list(refused.value.missing) == expected_missing

            output = _call(
                str(organization.id),
                "--provision",
                "--limit-usd",
                "25",
                *(["--live"] if live_flag else []),
            )
            assert "Dry run, nothing sent" in output
            for name in expected_missing:
                assert name in output
            assert (
                f"Would send: POST {provisioning.LIVE_BASE_URL}/keys "
                f'{{"limit": 25.0, "name": "futureagi-org-{organization.id}"}}'
            ) in output
            assert PROVISIONING_KEY not in output
        no_network.assert_not_called()
        assert not AgentccManagedUpstreamKey.objects.filter(
            organization=organization
        ).exists()

        # With all three the gate opens: the patched HTTP client is reached,
        # at the live API and with the provisioning key, and its error stops
        # the command before anything is stored.
        _set_switches(monkeypatch, key=True, live_env=True)
        client = provisioning.OpenRouterProvisioningClient(live=True)
        assert client.target.live is True
        with pytest.raises(provisioning.ProvisioningError):
            client.create_key("futureagi-org-x", Decimal("1"))
        with pytest.raises(CommandError, match="POST /keys failed: ConnectionError"):
            _call(str(organization.id), "--provision", "--live")
        assert no_network.call_count == 2
        method, url = no_network.call_args.args
        assert (method, url) == ("POST", f"{provisioning.LIVE_BASE_URL}/keys")
        assert no_network.call_args.kwargs["headers"] == {
            "Authorization": f"Bearer {PROVISIONING_KEY}"
        }
        assert no_network.call_args.kwargs["allow_redirects"] is False
        assert not AgentccManagedUpstreamKey.objects.filter(
            organization=organization
        ).exists()

    @pytest.mark.parametrize(
        "mock_url",
        [
            "https://openrouter.ai/api/v1",
            "http://10.0.0.5:18911/api/v1",
            "http://user@127.0.0.1:18911/api/v1",
            "ftp://127.0.0.1/api/v1",
        ],
    )
    def test_mock_url_must_be_loopback(self, monkeypatch, no_network, mock_url):
        monkeypatch.setenv(provisioning.MOCK_URL_ENV, mock_url)

        with pytest.raises(provisioning.ProvisioningConfigError):
            provisioning.OpenRouterProvisioningClient()
        no_network.assert_not_called()

    def test_live_flag_refused_while_mock_url_is_set(
        self, organization, monkeypatch, no_network
    ):
        _set_switches(monkeypatch, key=True, live_env=True)
        monkeypatch.setenv(provisioning.MOCK_URL_ENV, "http://127.0.0.1:18911/api/v1")

        with pytest.raises(provisioning.ProvisioningConfigError):
            provisioning.OpenRouterProvisioningClient(live=True)
        with pytest.raises(CommandError, match="cannot be used while"):
            _call(str(organization.id), "--provision", "--live")
        no_network.assert_not_called()

    def test_request_builders_hold_no_secret_and_quote_the_hash(self):
        request = provisioning.update_key_request("a/b?c", disabled=True)
        assert request.describe(provisioning.LIVE_BASE_URL) == (
            f'PATCH {provisioning.LIVE_BASE_URL}/keys/a%2Fb%3Fc {{"disabled": true}}'
        )
        assert provisioning.get_key_request("h").describe("x") == "GET x/keys/h"
        # No limit: the optional field is left out, not sent as null.
        assert provisioning.create_key_request("n", None).body == {"name": "n"}
        assert provisioning.create_key_request("n", Decimal("2.5")).body == {
            "name": "n",
            "limit": 2.5,
        }
        with pytest.raises(ValueError):
            provisioning.update_key_request("h")
        with pytest.raises(ValueError):
            provisioning.create_key_request("n", Decimal("-1"))
