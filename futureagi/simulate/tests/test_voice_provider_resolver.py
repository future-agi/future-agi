"""Unit tests for the canonical system-voice-provider resolver.

The resolver is the single source of truth for selecting the simulator-side
voice provider. These tests pin its precedence rules, its enum return
contract, and its normalisation/validation behaviour.
"""

import pytest

from simulate.utils.voice_provider import (
    DEFAULT_SYSTEM_VOICE_PROVIDER,
    SYSTEM_VOICE_PROVIDER_ENV_VAR,
    resolve_system_voice_provider,
)
from tracer.models.observability_provider import ProviderChoices


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """Ensure no ambient SYSTEM_VOICE_PROVIDER leaks across cases."""
    monkeypatch.delenv(SYSTEM_VOICE_PROVIDER_ENV_VAR, raising=False)


class TestDefault:
    @pytest.mark.unit
    def test_default_is_livekit(self):
        assert DEFAULT_SYSTEM_VOICE_PROVIDER is ProviderChoices.LIVEKIT

    @pytest.mark.unit
    def test_no_override_no_env_returns_livekit(self):
        assert resolve_system_voice_provider() is ProviderChoices.LIVEKIT

    @pytest.mark.unit
    def test_always_returns_enum(self):
        assert isinstance(resolve_system_voice_provider(), ProviderChoices)
        assert isinstance(resolve_system_voice_provider("vapi"), ProviderChoices)


class TestEnvPrecedence:
    @pytest.mark.unit
    def test_env_pins_provider(self, monkeypatch):
        monkeypatch.setenv(SYSTEM_VOICE_PROVIDER_ENV_VAR, "vapi")
        assert resolve_system_voice_provider() is ProviderChoices.VAPI

    @pytest.mark.unit
    def test_env_is_case_insensitive(self, monkeypatch):
        monkeypatch.setenv(SYSTEM_VOICE_PROVIDER_ENV_VAR, "LiveKit")
        assert resolve_system_voice_provider() is ProviderChoices.LIVEKIT

    @pytest.mark.unit
    def test_empty_env_falls_through_to_default(self, monkeypatch):
        monkeypatch.setenv(SYSTEM_VOICE_PROVIDER_ENV_VAR, "")
        assert resolve_system_voice_provider() is ProviderChoices.LIVEKIT


class TestOverridePrecedence:
    @pytest.mark.unit
    def test_override_beats_env(self, monkeypatch):
        monkeypatch.setenv(SYSTEM_VOICE_PROVIDER_ENV_VAR, "vapi")
        assert resolve_system_voice_provider("livekit") is ProviderChoices.LIVEKIT

    @pytest.mark.unit
    def test_override_string(self):
        assert resolve_system_voice_provider("vapi") is ProviderChoices.VAPI

    @pytest.mark.unit
    def test_override_enum_passthrough(self):
        assert (
            resolve_system_voice_provider(ProviderChoices.VAPI)
            is ProviderChoices.VAPI
        )

    @pytest.mark.unit
    def test_none_and_empty_override_fall_through(self, monkeypatch):
        monkeypatch.setenv(SYSTEM_VOICE_PROVIDER_ENV_VAR, "vapi")
        assert resolve_system_voice_provider(None) is ProviderChoices.VAPI
        assert resolve_system_voice_provider("") is ProviderChoices.VAPI


class TestValidation:
    @pytest.mark.unit
    def test_unknown_override_raises(self):
        with pytest.raises(ValueError, match="Unknown system voice provider"):
            resolve_system_voice_provider("klingon")

    @pytest.mark.unit
    def test_unknown_env_raises(self, monkeypatch):
        monkeypatch.setenv(SYSTEM_VOICE_PROVIDER_ENV_VAR, "nope")
        with pytest.raises(ValueError, match="Unknown system voice provider"):
            resolve_system_voice_provider()


class TestEngineWiring:
    """Pin P0's end-to-end intent at the VoiceServiceManager wiring level.

    These assert the two roles the resolver feeds (the ``voice_large.py``
    fetch-engine branch): the *system* path (resolver default) must build a
    LiveKit engine, while the *client-fetch* path (customer api_key, no
    explicit provider) must keep building a Vapi engine. This is the only
    genuine behaviour flip in P0; the resolver unit tests alone don't cover it.
    """

    def _vsm(self):
        return pytest.importorskip(
            "ee.voice.services.voice_service_manager"
        ).VoiceServiceManager

    @pytest.mark.unit
    def test_system_path_resolver_default_builds_livekit_engine(self):
        VoiceServiceManager = self._vsm()
        vsm = VoiceServiceManager(
            system_voice_provider=resolve_system_voice_provider()
        )
        assert type(vsm.engine).__name__ == "LivekitService"

    @pytest.mark.unit
    def test_client_fetch_path_defaults_to_vapi_engine(self):
        VoiceServiceManager = self._vsm()
        # Customer-account fetch: api_key, no explicit provider. Must stay Vapi
        # (the engine exposes Vapi-only SDK methods the fetch path calls).
        vsm = VoiceServiceManager(api_key="customer-key")
        assert type(vsm.engine).__name__ == "VapiService"
