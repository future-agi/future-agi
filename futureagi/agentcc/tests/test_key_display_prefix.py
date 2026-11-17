"""Key prefixes that tell keys apart.

The key list showed ``sk-agentcc-4...`` and ``sk-agentcc-5...``: the gateway
cut every key at 12 characters, so two keys read alike as soon as their first
random character matched. The list now shows the first and last four
characters of a key's random part, as Future AGI's own key list does
(``ec88****fe27``), and never the secret: 40 of its 48 characters stay hidden.
"""

import hashlib
from unittest.mock import MagicMock, patch

import pytest

from agentcc.models import AgentccAPIKey
from agentcc.services import auth_bridge

# Real gateway keys: "sk-agentcc-" + 24 random bytes as 48 hex characters.
KEY_A = "sk-agentcc-4f2a" + "0123456789abcdef" * 2 + "abcd77889c1d"
KEY_B = "sk-agentcc-4e00" + "fedcba9876543210" * 2 + "12340000b7e3"


def _gateway_create(key_id, raw_key):
    """What the gateway's create answers: its own 12-character prefix."""
    return {
        "id": key_id,
        "key": raw_key,
        "key_prefix": raw_key[:12] + "...",
        "name": key_id,
        "owner": "",
        "status": "active",
        "models": [],
        "providers": [],
    }


class TestDisplayKeyPrefix:
    def test_shows_the_first_and_last_four_of_the_random_part(self):
        assert auth_bridge.display_key_prefix(KEY_A) == "sk-agentcc-4f2a…9c1d"
        assert auth_bridge.display_key_prefix(KEY_B) == "sk-agentcc-4e00…b7e3"

    def test_two_keys_that_share_a_first_character_read_apart(self):
        legacy_a, legacy_b = KEY_A[:12] + "...", KEY_B[:12] + "..."
        assert legacy_a == legacy_b  # what the list showed before
        assert auth_bridge.display_key_prefix(KEY_A) != auth_bridge.display_key_prefix(
            KEY_B
        )

    def test_never_more_than_eight_secret_characters(self):
        shown = auth_bridge.display_key_prefix(KEY_A)
        secret = KEY_A[len("sk-agentcc-") :]
        visible = shown[len("sk-agentcc-") :].replace("…", "")
        assert len(visible) == 8
        assert secret not in shown
        assert len(secret) - len(visible) == 40

    def test_fits_the_stored_column(self):
        max_length = AgentccAPIKey._meta.get_field("key_prefix").max_length
        assert len(auth_bridge.display_key_prefix(KEY_A)) <= max_length

    @pytest.mark.parametrize(
        "raw_key",
        ["pk-full-raw-key-123", "sk-agentcc-short", "", "sk-other-" + "a" * 48],
    )
    def test_a_key_of_another_form_keeps_the_gateways_prefix(self, raw_key):
        assert auth_bridge.display_key_prefix(raw_key, fallback="gw...") == "gw..."


class TestPrefixesAgree:
    def test_a_keys_legacy_and_current_prefixes_agree(self):
        assert auth_bridge.prefixes_agree("sk-agentcc-4...", "sk-agentcc-4f2a…9c1d")
        assert auth_bridge.prefixes_agree("sk-agentcc-4f2a…9c1d", "sk-agentcc-4...")

    def test_identical_prefixes_agree(self):
        assert auth_bridge.prefixes_agree("pk-full", "pk-full")
        assert auth_bridge.prefixes_agree("sk-agentcc-8...", "sk-agentcc-8...")

    def test_different_keys_do_not(self):
        assert not auth_bridge.prefixes_agree("sk-agentcc-2...", "sk-agentcc-9...")
        assert not auth_bridge.prefixes_agree("sk-agentcc-5...", "sk-agentcc-4f2a…9c1d")
        assert not auth_bridge.prefixes_agree(
            "sk-agentcc-4f2a…9c1d", "sk-agentcc-4f2a…b7e3"
        )
        assert not auth_bridge.prefixes_agree("pk-full", "pk-fullx")


@pytest.mark.integration
class TestProvisionAndSync:
    @patch("agentcc.services.auth_bridge.get_gateway_client")
    def test_provision_stores_the_two_ended_prefix_never_the_key(
        self, mock_get_client, organization, workspace, user
    ):
        mock_client = MagicMock()
        mock_client.create_key.return_value = _gateway_create("key_two_ends", KEY_A)
        mock_get_client.return_value = mock_client

        api_key, raw_key = auth_bridge.provision_key(name="two-ends", user=user)

        api_key.refresh_from_db()
        assert raw_key == KEY_A
        assert api_key.key_prefix == "sk-agentcc-4f2a…9c1d"
        assert api_key.key_hash == hashlib.sha256(KEY_A.encode()).hexdigest()
        stored = [str(value) for value in api_key.__dict__.values()]
        assert not any(KEY_A[len("sk-agentcc-") :] in value for value in stored)

    @patch("agentcc.services.auth_bridge.get_gateway_client")
    def test_sync_against_an_older_gateway_is_not_an_id_collision(
        self, mock_get_client, organization, workspace
    ):
        # Django stores the two-ended prefix; a gateway that has not been
        # upgraded lists the same key under its 12-character one.
        key = AgentccAPIKey.objects.create(
            gateway_key_id="key_skew",
            name="skew",
            organization=organization,
            workspace=workspace,
            key_hash=hashlib.sha256(KEY_A.encode()).hexdigest(),
            key_prefix=auth_bridge.display_key_prefix(KEY_A),
            metadata={"org_id": str(organization.id)},
        )
        mock_client = MagicMock()
        mock_client.list_keys.return_value = {
            "data": [
                {
                    "id": "key_skew",
                    "key_prefix": KEY_A[:12] + "...",
                    "status": "revoked",
                    "metadata": {"org_id": str(organization.id)},
                }
            ]
        }
        mock_client.import_keys.return_value = {"loaded": 0}
        mock_get_client.return_value = mock_client

        auth_bridge.sync_keys(org=organization)

        key.refresh_from_db()
        # The gateway's revoke reached the row: it was matched as the same key.
        assert key.status == AgentccAPIKey.REVOKED

    @patch("agentcc.services.auth_bridge.get_gateway_client")
    def test_sync_revokes_a_deleted_key_an_older_gateway_still_lists(
        self, mock_get_client, organization, workspace
    ):
        key = AgentccAPIKey.objects.create(
            gateway_key_id="key_deleted_skew",
            name="deleted-skew",
            organization=organization,
            workspace=workspace,
            key_hash=hashlib.sha256(KEY_A.encode()).hexdigest(),
            key_prefix=auth_bridge.display_key_prefix(KEY_A),
            metadata={"org_id": str(organization.id)},
        )
        key.delete()
        mock_client = MagicMock()
        mock_client.list_keys.return_value = {
            "data": [
                {
                    "id": "key_deleted_skew",
                    "key_prefix": KEY_A[:12] + "...",
                    "status": "active",
                    "metadata": {"org_id": str(organization.id)},
                }
            ]
        }
        mock_client.import_keys.return_value = {"loaded": 0}
        mock_get_client.return_value = mock_client

        count = auth_bridge.sync_keys(org=organization)

        mock_client.revoke_key.assert_called_once_with("key_deleted_skew")
        assert count == 0
