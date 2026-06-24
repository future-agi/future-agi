"""A5b: provider secrets must never leave an API response in cleartext.

`mask_snapshot_secrets` is the single masking choke point used by both the
agent-version response serializer and the run_test serializer (which previously
embedded the raw configuration_snapshot verbatim, leaking customer api_key /
livekit_api_key / livekit_api_secret).
"""

import pytest

from simulate.serializers.response.agent_version import mask_snapshot_secrets

PLAINTEXT_SECRET = "supersecretvalue1234567890"


class TestMaskSnapshotSecrets:
    @pytest.mark.unit
    def test_masks_all_three_secret_fields(self):
        raw = {
            "api_key": "sk-vapi-1234567890abcdef",
            "livekit_api_key": "APIabcdef123456",
            "livekit_api_secret": PLAINTEXT_SECRET,
            "provider": "livekit",
            "livekit_url": "wss://example.livekit.cloud",
        }
        masked = mask_snapshot_secrets(raw)

        # Input dict is not mutated (callers reuse the snapshot).
        assert raw["api_key"] == "sk-vapi-1234567890abcdef"
        assert raw["livekit_api_secret"] == PLAINTEXT_SECRET

        # No secret field is returned in cleartext.
        assert masked["api_key"] != raw["api_key"]
        assert masked["livekit_api_key"] != raw["livekit_api_key"]
        assert masked["livekit_api_secret"] == "********"

        # Non-secret fields are preserved unchanged.
        assert masked["provider"] == "livekit"
        assert masked["livekit_url"] == "wss://example.livekit.cloud"

        # The raw secret value appears nowhere in the masked output.
        assert PLAINTEXT_SECRET not in masked.values()

    @pytest.mark.unit
    def test_livekit_api_key_is_masked(self):
        # Regression for the specific gap: livekit_api_key used to pass through.
        masked = mask_snapshot_secrets({"livekit_api_key": "APIverylongkey123"})
        assert masked["livekit_api_key"] != "APIverylongkey123"
        assert "verylongkey" not in masked["livekit_api_key"]

    @pytest.mark.unit
    def test_non_dict_returned_unchanged(self):
        assert mask_snapshot_secrets(None) is None
        assert mask_snapshot_secrets("not-a-dict") == "not-a-dict"

    @pytest.mark.unit
    def test_empty_or_missing_secrets_are_not_masked_to_noise(self):
        masked = mask_snapshot_secrets(
            {"api_key": "", "livekit_api_key": None, "provider": "vapi"}
        )
        assert masked["api_key"] == ""
        assert masked["livekit_api_key"] is None
        assert masked["provider"] == "vapi"
